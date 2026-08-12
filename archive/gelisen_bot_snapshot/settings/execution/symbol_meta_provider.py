# settings/execution/symbol_meta_provider.py

from __future__ import annotations
from typing import Any, Dict, Optional, Tuple
import time
import math
import ccxt
import logging
from core.symbol_resolver import normalize_core_symbol

logger = logging.getLogger("symbol_meta_provider")

# ---- caches ----
_MARKETS_CACHE: Dict[str, Dict[str, Any]] = {}   # key=exchange_id -> {"ts":..., "markets":...}
_META_CACHE: Dict[Tuple[str, str], Dict[str, Any]] = {}  # key=(exchange, symbol_core) -> {"ts":..., "data":...}

TTL_SECONDS = 60


def _calc_decimals(step: float) -> int:
    try:
        step = float(step)
    except Exception:
        return 6
    if step >= 1:
        return 0
    try:
        return min(12, abs(int(round(math.log10(step)))))
    except Exception:
        s2 = f"{step:.12f}".rstrip("0")
        if "." in s2:
            return len(s2.split(".")[1])
        return 6


def _exchange_ccxt_id(exchange: str) -> str:
    ex = (exchange or "").lower().strip()
    if ex == "binance":
        return "binanceusdm"
    return ex  # mexc, bybit, bitget, okx, bitmart ...


def _ccxt_options_for_exchange(exchange: str) -> Dict[str, Any]:
    """
    ccxt options: derivatives marketleri daha doğru getirmek için.
    """
    ex = (exchange or "").lower().strip()

    # Not: ccxt'te bazı borsalarda defaultType değerleri "swap/future" farklılık gösterir.
    if ex == "binance":
        return {"defaultType": "future"}   # binanceusdm zaten usdm
    if ex == "mexc":
        return {"defaultType": "future"}
    if ex == "bybit":
        return {"defaultType": "swap"}     # linear perp
    if ex == "bitget":
        return {"defaultType": "swap"}     # mix/perp
    if ex == "okx":
        return {"defaultType": "swap"}     # SWAP
    if ex == "bitmart":
        # bitmart için derivatives varsa ayrı; yoksa spot kalabilir
        return {}
    return {}


def _load_markets(exchange: str) -> Dict[str, Any]:
    ex_l = (exchange or "").lower().strip()
    ccxt_id = _exchange_ccxt_id(ex_l)
    now = time.time()

    cached = _MARKETS_CACHE.get(ccxt_id)
    if cached and (now - cached["ts"] < TTL_SECONDS):
        return cached["markets"]

    klass = getattr(ccxt, ccxt_id, None)
    if klass is None:
        raise ValueError(f"ccxt exchange not found: {ccxt_id}")

    options = _ccxt_options_for_exchange(ex_l)
    client = klass({"enableRateLimit": True, "options": options} if options else {"enableRateLimit": True})

    markets = client.load_markets()
    _MARKETS_CACHE[ccxt_id] = {"ts": now, "markets": markets}
    logger.info(f"[META_MARKETS_REFRESH] ex={ex_l} ccxt_id={ccxt_id} count={len(markets)} options={options}")
    return markets


def _is_derivatives_market(exchange: str, m: Dict[str, Any]) -> bool:
    """
    Derivatives olup olmadığını robust tespit et.
    ccxt market dict: swap/future booleans / type alanı olabilir.
    """
    ex = (exchange or "").lower().strip()
    if ex in ("bybit", "okx", "bitget", "binance", "mexc"):
        if m.get("swap") or m.get("future"):
            return True
        t = str(m.get("type") or "").lower()
        if t in ("swap", "future", "futures"):
            return True
        # bazı marketler contract=true set eder
        if m.get("contract") is True:
            return True
    return False


def _candidate_ccxt_symbols(exchange: str, symbol_core: str) -> list[str]:
    """
    symbol_core: BTCUSDT
    çeşitli ccxt market key varyantlarını dene.
    """
    base = symbol_core.replace("USDT", "")
    ex = (exchange or "").lower().strip()

    # common
    cands = [
        f"{base}/USDT:USDT",   # swap format
        f"{base}/USDT",        # bazen swap da böyle görünür (nadiren)
        symbol_core,           # binanceusdm market("BTCUSDT") gibi
    ]

    if ex == "mexc":
        # mexc get_symbol_meta kendi içinde eşliyor ama provider da arayabilsin
        cands.insert(0, f"{base}_USDT")
        cands.insert(0, f"{base}/USDT:USDT")

    if ex == "bitget":
        # ccxt bitget’te swap market symbol çoğunlukla "BTC/USDT:USDT"
        # ama sizin executor "BTCUSDT_UMCBL" kullanıyor. Provider bunu symbol key olarak kullanmayacak,
        # sadece meta bulacak. O yüzden ccxt tarafında base/quote ile arayacağız.
        cands.insert(0, f"{base}/USDT:USDT")
        cands.insert(0, f"{base}/USDT")

    if ex == "okx":
        # okx ccxt sembol: "BTC/USDT:USDT" veya info.instId "BTC-USDT-SWAP"
        cands.insert(0, f"{base}/USDT:USDT")
        cands.insert(0, f"{base}/USDT")

    if ex == "bybit":
        cands.insert(0, f"{base}/USDT:USDT")
        cands.insert(0, f"{base}/USDT")

    # uniq, order-preserving
    seen = set()
    out = []
    for x in cands:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def get_symbol_meta(
    *,
    exchange: str,
    symbol: str,
) -> Optional[Dict[str, Any]]:
    """
    Kesin meta sağlayıcı:
    - exchange: 'mexc','binance','bybit','bitget','okx',...
    - symbol: core formatta da gelebilir, exchange formatta da gelebilir.
    Dönen dict:
      ccxt_symbol, price_step, amount_step, price_decimals, amount_decimals, min_amount
    """
    ex = (exchange or "").lower().strip()
    symbol_core = normalize_core_symbol(symbol)
    if not ex or not symbol_core or not symbol_core.endswith("USDT"):
        return None

    now = time.time()
    cache_key = (ex, symbol_core)
    cached = _META_CACHE.get(cache_key)
    if cached and now - cached["ts"] < TTL_SECONDS:
        return cached["data"]

    try:
        markets = _load_markets(ex)
    except Exception as e:
        logger.error(f"[META_MARKETS_ERR] ex={ex} sym={symbol_core} err={e}", exc_info=True)
        return None

    # 1) direct candidates (fast)
    chosen_key = None
    chosen = None

    for cand in _candidate_ccxt_symbols(ex, symbol_core):
        m = markets.get(cand)
        if m and _is_derivatives_market(ex, m) and str(m.get("quote", "")).upper() == "USDT":
            chosen_key, chosen = cand, m
            break

    # 2) fallback: scan
    if chosen is None:
        base = symbol_core.replace("USDT", "")
        candidates = []
        for mk, m in markets.items():
            if not isinstance(m, dict):
                continue
            if not _is_derivatives_market(ex, m):
                continue
            if str(m.get("quote", "")).upper() != "USDT":
                continue

            # base match (strip leading digits: 1000PEPE -> PEPE)
            b = str(m.get("base") or "").upper()
            b2 = b.lstrip("0123456789")
            if b2 == base:
                candidates.append((mk, m))

        if candidates:
            # shortest symbol is usually canonical
            candidates.sort(key=lambda x: len(str(x[0])))
            chosen_key, chosen = candidates[0]

    if chosen is None:
        logger.warning(f"[META_NOT_FOUND] ex={ex} sym={symbol_core}")
        return None

    precision = chosen.get("precision") or {}
    limits = chosen.get("limits") or {}
    amt_limits = (limits.get("amount") or {}) if isinstance(limits.get("amount"), dict) else {}

    # ccxt precision: bazı borsalarda int (decimals), bazılarında step.
    # güvenli şekilde step'e çeviriyoruz.
    def _precision_to_step(v: Any, fallback_step: float) -> float:
        if v is None:
            return fallback_step
        try:
            # int -> decimals
            if isinstance(v, int):
                return float(10 ** (-v))
            # float step
            fv = float(v)
            if fv > 0:
                return fv
        except Exception:
            pass
        return fallback_step

    price_step = _precision_to_step(precision.get("price"), 0.0001)
    amount_step = _precision_to_step(precision.get("amount"), 1.0)

    min_amount = amt_limits.get("min")
    try:
        min_amount = float(min_amount) if min_amount is not None else None
    except Exception:
        min_amount = None
    if not min_amount or min_amount <= 0:
        min_amount = float(amount_step)
    # --- contract_size / contractSize yakala ---
    contract_size = None
    try:
        # ccxt unified alanları
        contract_size = chosen.get("contractSize") or chosen.get("contract_size")
    except Exception:
        contract_size = None

    # info içinden de dene (bazı borsalarda sadece burada olur)
    if contract_size is None:
        info = chosen.get("info") or {}
        if isinstance(info, dict):
            contract_size = info.get("contractSize") or info.get("contract_size") or info.get("contractValue")

    # Hâlâ yoksa ve base "1000PEPE" gibi başında sayı taşıyorsa, prefix’i multiplier gibi kullan
    if contract_size is None:
        try:
            b = str(chosen.get("base") or "").upper()
            # baştaki sayıları al
            m = ""
            for ch in b:
                if ch.isdigit():
                    m += ch
                else:
                    break
            if m:
                contract_size = float(m)  # "1000PEPE" -> 1000
        except Exception:
            contract_size = None

    try:
        contract_size = float(contract_size) if contract_size is not None else 1.0
    except Exception:
        contract_size = 1.0
    if contract_size <= 0:
        contract_size = 1.0

    # --- min_notional / min_cost (USDT) yakala ---
    min_notional = None
    try:
        cost_limits = (limits.get("cost") or {}) if isinstance(limits.get("cost"), dict) else {}
        min_notional = cost_limits.get("min")
    except Exception:
        min_notional = None

    try:
        min_notional = float(min_notional) if min_notional is not None else 0.0
    except Exception:
        min_notional = 0.0

    meta = {
        "ccxt_symbol":str(chosen.get("symbol") or chosen_key),
        "price_step":float(price_step),
        "amount_step":float(amount_step),
        "price_decimals":int(_calc_decimals(price_step)),
        "amount_decimals":int(_calc_decimals(amount_step)),
        "min_amount":float(min_amount),

        # ✅ kritik alanlar
        "contract_size":float(contract_size),
        "min_notional":float(min_notional),
    }

    _META_CACHE[cache_key] = {"ts": now, "data": meta}
    logger.info(
        f"[META_OK] ex={ex} core={symbol_core} ccxt={meta['ccxt_symbol']} "
        f"price_step={meta['price_step']} amount_step={meta['amount_step']} min_amount={meta['min_amount']} "
        f"contract_size={meta['contract_size']} min_notional={meta['min_notional']}"
    )

    return meta
