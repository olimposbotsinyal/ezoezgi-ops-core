# strategies/alarm_system/orderbook.py
from __future__ import annotations
import asyncio
from typing import Optional

from core.strategy_manager import StrategyManager as SMRef
import math
import logging

from strategies.strategy_v1 import StrategyV1
from strategies.strategy_v2 import StrategyV2
SMRef.register(StrategyV1)
SMRef.register(StrategyV2)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    force=True
)

logger = logging.getLogger(__name__)

async def _fetch_orderbook_cached(cls, ccxt_symbol: str, depth: int, cfg: Optional[dict] = None) -> Optional[dict]:
    cfg = cfg or cls._get_orderbook_cfg()
    ttl = float(cfg.get("cache_ttl_sec") or 2.5)
    timeout_sec = float(cfg.get("timeout_sec") or 4.0)

    ex = getattr(cls, "exchange", None)
    if not ex:
        return None

    ex_id = str(getattr(ex, "id", "") or getattr(ex, "name", "") or "ex").lower()
    key = (ex_id, str(ccxt_symbol), int(depth))

    now = asyncio.get_running_loop().time()
    hit = cls._orderbook_cache.get(key)
    if hit and (now - float(hit.get("ts", 0))) <= ttl:
        return hit.get("data")

    async with cls._orderbook_sem:
        try:
            ob = await asyncio.wait_for(
                ex.fetch_order_book(str(ccxt_symbol), limit=int(depth)),
                timeout=timeout_sec
            )
            cls._orderbook_cache[key] = {"ts":now, "data":ob}
            return ob
        except Exception as e:
            logger.warning(f"[ORDERBOOK_FETCH_FAIL] ex={ex_id} sym={ccxt_symbol} depth={depth} err={e}")
            return None


def _compute_orderbook_features(cls, ob: dict) -> dict:
    """
    Orderbook verisini robust şekilde işler.
    bids/asks level formatları:
      - [price, amount]
      - [price, amount, count]  (bazı borsalar)
      - {"price": ..., "amount": ...} gibi dict (nadiren wrapper)
    Bozuk satırlar atlanır, yeterli veri yoksa ok=False döner.
    """

    bids_raw = (ob.get("bids") or []) if isinstance(ob, dict) else []
    asks_raw = (ob.get("asks") or []) if isinstance(ob, dict) else []

    if not bids_raw or not asks_raw:
        return {"ok":False}

    def _level_to_px_qty(level) -> Optional[tuple[float, float]]:
        try:
            # dict formatı
            if isinstance(level, dict):
                p = level.get("price")
                q = level.get("amount", level.get("qty", level.get("quantity")))
                if p is None or q is None:
                    return None
                pf = float(p)
                qf = float(q)
                if not math.isfinite(pf) or not math.isfinite(qf):
                    return None
                return pf, qf

            # list/tuple formatı: [price, qty] veya [price, qty, ...]
            if isinstance(level, (list, tuple)) and len(level) >= 2:
                pf = float(level[0])
                qf = float(level[1])
                if not math.isfinite(pf) or not math.isfinite(qf):
                    return None
                return pf, qf

            return None
        except Exception:
            return None

    # Parse + temizle
    bids: list[tuple[float, float]] = []
    asks: list[tuple[float, float]] = []

    for lv in bids_raw:
        pq = _level_to_px_qty(lv)
        if pq:
            bids.append(pq)

    for lv in asks_raw:
        pq = _level_to_px_qty(lv)
        if pq:
            asks.append(pq)

    if not bids or not asks:
        return {"ok":False}

    # Best bid/ask
    bid1 = float(bids[0][0])
    ask1 = float(asks[0][0])

    mid = (bid1 + ask1) / 2.0 if (bid1 > 0 and ask1 > 0) else 0.0
    spread_bps = ((ask1 - bid1) / mid * 10000.0) if mid > 0 else 9999.0

    # Qty toplamı
    bid_qty = 0.0
    ask_qty = 0.0
    bid_usd = 0.0
    ask_usd = 0.0

    for p, q in bids:
        bid_qty += float(q)
        bid_usd += float(p) * float(q)

    for p, q in asks:
        ask_qty += float(q)
        ask_usd += float(p) * float(q)

    denom = bid_qty + ask_qty
    imbalance = (bid_qty / denom) if denom > 0 else 0.5

    top_liquidity_usd = min(bid_usd, ask_usd)

    return {
        "ok":True,
        "bid1":bid1,
        "ask1":ask1,
        "mid":mid,
        "spread_bps":spread_bps,
        "imbalance":imbalance,
        "bid_usd":bid_usd,
        "ask_usd":ask_usd,
        "top_liquidity_usd":top_liquidity_usd,
        # İstersen debug için ham sayıları da ekleyebilirsin:
        # "bid_qty": bid_qty,
        # "ask_qty": ask_qty,
        # "levels_bid": len(bids),
        # "levels_ask": len(asks),
    }


def _apply_orderbook_confirm(cls, cand: dict, obf: dict, direction: str, cfg: Optional[dict] = None) -> dict:
    cfg = cfg or cls._get_orderbook_cfg()
    th = (cfg.get("thresholds") or {})
    sc = (cfg.get("scoring") or {})

    imbalance_w = float(sc.get("imbalance_weight") or 25)
    spread_pen = float(sc.get("spread_penalty") or 15)
    liq_w = float(sc.get("liquidity_weight") or 10)

    min_long = float(th.get("min_imbalance_long") or 0.58)
    max_short = float(th.get("max_imbalance_short") or 0.42)
    max_spread = float(th.get("max_spread_bps") or 12)
    min_liq = float(th.get("min_top_liquidity_usd") or 8000)

    bonus = 0.0
    penalty = 0.0
    passed = True
    reasons: list[str] = []

    spread_bps = float(obf.get("spread_bps") or 9999)
    imbalance = float(obf.get("imbalance") or 0.5)
    liq = float(obf.get("top_liquidity_usd") or 0)

    # spread
    if spread_bps > max_spread:
        penalty += spread_pen * min(2.0, (spread_bps / max_spread))
        passed = False
        reasons.append(f"spread>{max_spread:.2f}bps")

    # liquidity
    if liq < min_liq:
        penalty += liq_w * (1.0 - (liq / min_liq))
        passed = False
        reasons.append(f"liq<{min_liq:.0f}usd")
    else:
        bonus += liq_w * 0.5

    # imbalance
    dir_up = str(direction or "").upper()
    if dir_up in ("LONG", "BUY"):
        if imbalance >= min_long:
            bonus += imbalance_w * ((imbalance - min_long) / max(1e-6, (1.0 - min_long)))
        else:
            penalty += imbalance_w * ((min_long - imbalance) / max(1e-6, min_long))
            passed = False
            reasons.append(f"imb<{min_long:.2f}(long)")
    else:
        if imbalance <= max_short:
            bonus += imbalance_w * ((max_short - imbalance) / max(1e-6, max_short))
        else:
            penalty += imbalance_w * ((imbalance - max_short) / max(1e-6, (1.0 - max_short)))
            passed = False
            reasons.append(f"imb>{max_short:.2f}(short)")

    meta = cand.get("meta") if isinstance(cand.get("meta"), dict) else {}

    base_score = float(
        cand.get("score") or cand.get("final_score") or cand.get("v1_score") or cand.get("v2_score") or 0.0
    )
    score_ob = base_score + bonus - penalty

    meta["orderbook"] = {
        "passed":passed,
        "reasons":reasons,  # ✅ kritik
        "bonus":bonus,
        "penalty":penalty,
        "score_ob":score_ob,
        "features":obf,
        "thresholds_used":{  # ✅ debug kolaylığı
            "max_spread_bps":max_spread,
            "min_top_liquidity_usd":min_liq,
            "min_imbalance_long":min_long,
            "max_imbalance_short":max_short,
        },
    }
    cand["meta"] = meta

    cand["score_ob"] = score_ob
    cand["orderbook_passed"] = passed
    return cand


async def _confirm_signal_with_orderbook(
        cls,
        ccxt_symbol: str,
        direction: str,
        base_score: float | None,
        meta: dict,
        timeframe: str | None = None
) -> tuple[bool, dict]:
    meta = meta if isinstance(meta, dict) else {}

    exchange_name = ""
    try:
        exchange_name = str(getattr(cls.exchange, "id", "") or "").lower()
    except Exception:
        exchange_name = ""

    cfg = cls._get_orderbook_cfg_for(exchange_name, timeframe)
    if not bool(cfg.get("enabled", False)):
        return True, meta

    meta["orderbook_cfg_ctx"] = {"exchange":exchange_name, "timeframe":timeframe}

    # semaphore override
    try:
        mp = int(cfg.get("max_parallel") or 3)
        if int(getattr(cls, "_orderbook_sem_max", 0) or 0) != mp:
            cls._orderbook_sem = asyncio.Semaphore(mp)
            cls._orderbook_sem_max = mp
    except Exception:
        pass

    depth = int(cfg.get("book_depth") or 20)
    require_pass = bool(cfg.get("require_pass", False))

    # ✅ yeni soft-pass kontrolleri
    soft_pass_on_errors = bool(cfg.get("soft_pass_on_errors", True))
    soft_pass_error_types = cfg.get("soft_pass_error_types") or ["no_orderbook", "bad_orderbook_data", "exception"]
    if not isinstance(soft_pass_error_types, list):
        soft_pass_error_types = ["no_orderbook", "bad_orderbook_data", "exception"]

    def _should_soft_pass(err_type: str) -> bool:
        return soft_pass_on_errors and (str(err_type) in set(map(str, soft_pass_error_types)))

    # --- fetch
    try:
        ob = await cls._fetch_orderbook_cached(str(ccxt_symbol), int(depth), cfg=cfg)
    except Exception as e:
        meta["orderbook"] = {"passed":False, "error":"exception", "detail":str(e)[:200]}
        # ✅ sadece hata ise soft-pass
        return ((not require_pass) or _should_soft_pass("exception")), meta

    if not ob:
        meta["orderbook"] = {"passed":False, "error":"no_orderbook"}
        return ((not require_pass) or _should_soft_pass("no_orderbook")), meta

    obf = cls._compute_orderbook_features(ob)
    if not obf.get("ok"):
        meta["orderbook"] = {"passed":False, "error":"bad_orderbook_data"}
        return ((not require_pass) or _should_soft_pass("bad_orderbook_data")), meta

    cand = {"meta":meta, "score":float(base_score or 0.0)}
    cand = cls._apply_orderbook_confirm(cand, obf, str(direction), cfg=cfg)

    updated_meta = cand.get("meta") if isinstance(cand.get("meta"), dict) else meta
    passed = bool(cand.get("orderbook_passed", False))

    # ✅ debug (istersen INFO yapabilirsin)
    try:
        obm = (updated_meta.get("orderbook") or {}) if isinstance(updated_meta, dict) else {}
        rs = obm.get("reasons") or []
        logger.debug(
            f"[ORDERBOOK_EVAL] ex={exchange_name} tf={timeframe} sym={ccxt_symbol} "
            f"passed={passed} reasons={rs} spread_bps={obf.get('spread_bps')} liq={obf.get('top_liquidity_usd')} imb={obf.get('imbalance')}"
        )
    except Exception:
        pass

    # ✅ Burada soft-pass YOK: çünkü bu “metrik fail” (spread/liq/imbalance)
    if require_pass and not passed:
        return False, updated_meta

    return True, updated_meta


def _merge_dict_deepish(cls, dst: dict, src: dict) -> dict:
    """
    Küçük, kontrollü merge:
    - dict-dict ise birleştirir (1 seviye derin)
    - diğer tiplerde src kazanır
    """
    if not isinstance(dst, dict):
        dst = {}
    if not isinstance(src, dict):
        return dst
    out = dict(dst)
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out


def _get_orderbook_cfg_for(cls, exchange_id: str | None, timeframe: str | None) -> dict:
    base = cls._get_orderbook_cfg() or {}
    profs = base.get("profiles") or {}
    if not isinstance(profs, dict):
        return base

    ex = str(exchange_id or "").lower().strip() or "default"
    tf = str(timeframe or "").strip() or ""

    out = dict(base)

    # default
    out = cls._merge_dict_deepish(out, profs.get("default") or {})

    # exchange overrides
    ex_map = profs.get("exchanges") or {}
    if isinstance(ex_map, dict):
        out = cls._merge_dict_deepish(out, ex_map.get(ex) or {})

    # timeframe overrides
    tf_map = profs.get("timeframes") or {}
    if isinstance(tf_map, dict) and tf:
        out = cls._merge_dict_deepish(out, tf_map.get(tf) or {})

    # matrix overrides (most specific)
    mx = profs.get("matrix") or {}
    if isinstance(mx, dict) and tf:
        ex_mx = mx.get(ex)
        if isinstance(ex_mx, dict):
            out = cls._merge_dict_deepish(out, ex_mx.get(tf) or {})

    return out


