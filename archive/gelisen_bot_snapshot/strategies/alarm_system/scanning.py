# strategies/alarm_system/scanning.py
import logging
import numpy as np
from datetime import datetime, timezone
import pickle
import os
import asyncio
from RealAIModel import RealAIModel
import math
import pandas as pd
import joblib
from typing import List, Dict, Any, Optional, Tuple, cast, Callable, Awaitable
from telegram.ext import CallbackContext
import json
import time
import ccxt.async_support as ccxt
from config_service import ConfigService
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from strategies.technical_indicators import TechnicalIndicators
from strategies.alarm_system.analytics import SymbolPerformanceTracker
from services.market_data import MarketDataService
# --- Optional TA-Lib ---
try:
    import talib  # type: ignore
    HAS_TALIB = True
except Exception as e:
    logging.warning("[scanning] TA-Lib import edilemedi: %r", e)
    talib = None  # type: ignore
    HAS_TALIB = False

logger = logging.getLogger(__name__)

def _ensure_market_data_service(cls) -> Optional[MarketDataService]:
    """
    cls.exchange hazırsa cls.md'yi garanti eder.
    Exchange restart sonrası da yeniden enjekte edilebilir.
    """
    ex = getattr(cls, "exchange", None)
    if ex is None:
        return None

    md = getattr(cls, "md", None) or getattr(cls, "market_data_service", None)

    # md yoksa veya md.exchange farklı instance ise yeniden yarat
    try:
        md_ex = getattr(md, "exchange", None)
    except Exception:
        md_ex = None

    if md is None or md_ex is not ex:
        cls.md = MarketDataService(ex)
        cls.market_data_service = cls.md
        return cls.md

    # isim uyumu için iki attribute'u da set tut
    cls.md = md
    cls.market_data_service = md
    return md


async def _select_symbols_via_candidate_selector(
    md,
    ai_model,
    strategy_id: str,
    to_futures_symbol: Optional[Callable[[str], str]] = None,
) -> list[str]:
    from core.candidate_selector import CandidateSelector

    params = {"CANDIDATE_SELECTION": ConfigService.get("CANDIDATE_SELECTION", {})}
    selector = CandidateSelector(
        market_data_service=md,
        ai_model=ai_model,
        logger=logging.getLogger("candidate_selector"),
        params=params
    )

    cands = await selector.select_candidates_for(strategy_id)

    out: list[str] = []
    for c in (cands or []):
        sym = str(getattr(c, "symbol", "") or "").strip()
        if not sym:
            continue

        # 1) "BTC/USDT:USDT" -> "BTC/USDT"
        spot_like = _norm_symbol_for_match(sym)  # zaten ':' kırpıyor

        # 2) Futures'a çevir (MEXC multiplier dahil)
        if callable(to_futures_symbol):
            fut = to_futures_symbol(spot_like)
        else:
            # fallback: en azından settle ekle
            fut = spot_like

        out.append(fut)

    return out


def _norm_symbol_for_match(s: Any) -> str:
    """
    BTC/USDT:USDT -> BTC/USDT
    btc/usdt -> BTC/USDT
    BTC_USDT -> BTC/USDT (istersen)
    """
    ss = str(s or "").strip().upper()
    if not ss:
        return ""
    # futures settle kısmını at
    if ":" in ss:
        ss = ss.split(":", 1)[0].strip()
    # istersen underscore normalize:
    if "_" in ss and "/" not in ss:
        ss = ss.replace("_", "/")
    return ss

def _get_bb_edge_filter_cfg(timeframe: str) -> Dict[str, Any]:
    """
    Config yolu önerisi:
      scans.tf_profiles.{tf}.common_filters.bb_edge_filter.*
    """
    tf = str(timeframe or "").strip()
    base_path = f"scans.tf_profiles.{tf}.common_filters.bb_edge_filter"

    enabled = bool(ConfigService.get(f"{base_path}.enabled", False))

    # Edge thresholds
    long_pb_max = _to_float(ConfigService.get(f"{base_path}.long_percent_b_max", 0.95), 0.95)
    short_pb_min = _to_float(ConfigService.get(f"{base_path}.short_percent_b_min", 0.05), 0.05)

    # Width guard (opsiyonel)
    bb_width_max_raw = ConfigService.get(f"{base_path}.bb_width_max", None)
    bb_width_max = _to_float_opt(bb_width_max_raw)  # None kalabilsin

    # Override koşulları
    adx_override = _to_float(ConfigService.get(f"{base_path}.override.adx_min", 28.0), 28.0)
    vr_override = _to_float(ConfigService.get(f"{base_path}.override.vr_min", 1.2), 1.2)

    # Trend doğrulama: "local_trend" string kontrolü için anahtar kelimeler
    # (AI scan’de indicators.local_trend var)
    require_trend_hint = bool(ConfigService.get(f"{base_path}.override.require_trend_hint", True))

    return {
        "enabled": enabled,
        "long_pb_max": long_pb_max,
        "short_pb_min": short_pb_min,
        "bb_width_max": bb_width_max,
        "override": {
            "adx_min": adx_override,
            "vr_min": vr_override,
            "require_trend_hint": require_trend_hint,
        }
    }


def _to_float_opt(x: Any) -> Optional[float]:
    """None -> None, sayı/str -> float, NaN/inf -> None"""
    if x is None:
        return None
    try:
        v = float(x)
    except Exception:
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v

def _to_float(x: Any, default: float) -> float:
    v = _to_float_opt(x)
    return default if v is None else float(v)



def _bb_edge_decision(
    *,
    symbol: str,
    timeframe: str,
    direction: str,
    bb_percent_b: Optional[float],
    bb_width: Optional[float],
    adx: Optional[float],
    vr: Optional[float],
    local_trend: Optional[str],
    cfg: Dict[str, Any],
) -> Tuple[bool, str]:
    """
    Returns: (allowed, reason)
      - allowed=False -> hard-skip
      - allowed=True  -> pass (normal veya override)
    """
    if not cfg.get("enabled", False):
        return True, "disabled"

    diru = str(direction or "").upper().strip()
    if diru not in ("LONG", "SHORT"):
        return True, "no_direction"

    try:
        pb = float(bb_percent_b) if bb_percent_b is not None else float("nan")
    except Exception:
        pb = float("nan")

    try:
        bw = float(bb_width) if bb_width is not None else float("nan")
    except Exception:
        bw = float("nan")

    try:
        ax = float(adx) if adx is not None else float("nan")
    except Exception:
        ax = float("nan")

    try:
        vratio = float(vr) if vr is not None else float("nan")
    except Exception:
        vratio = float("nan")

    lt = str(local_trend or "")

    # Width hard-guard (opsiyonel)
    bw_max_raw = cfg.get("bb_width_max", None)
    bw_max = _to_float_opt(bw_max_raw)
    if bw_max is not None and math.isfinite(bw) and bw > bw_max:
        logging.info(
            "[BB_FILTER_PASS] sym=%s tf=%s dir=%s reason=width_not_applicable bw=%.6f bw_max=%.6f pb=%s adx=%s vr=%s lt=%r",
            symbol, timeframe, diru, bw, float(bw_max),
            (f"{pb:.4f}" if math.isfinite(pb) else "nan"),
            (f"{ax:.2f}" if math.isfinite(ax) else "nan"),
            (f"{vratio:.2f}" if math.isfinite(vratio) else "nan"),
            lt,
        )
        return True, "width_not_applicable"

    # Edge tespiti
    long_pb_max = float(cfg.get("long_pb_max", 0.95) or 0.95)
    short_pb_min = float(cfg.get("short_pb_min", 0.05) or 0.05)

    is_edge = False
    if math.isfinite(pb):
        if diru == "LONG" and pb >= long_pb_max:
            is_edge = True
        elif diru == "SHORT" and pb <= short_pb_min:
            is_edge = True

    if not is_edge:
        return True, "not_edge"

    # Override koşulları (güçlü trend continuation)
    ov = cfg.get("override", {}) or {}
    adx_min = float(ov.get("adx_min", 28.0) or 28.0)
    vr_min = float(ov.get("vr_min", 1.2) or 1.2)
    req_hint = bool(ov.get("require_trend_hint", True))

    ok_adx = (math.isfinite(ax) and ax >= adx_min)
    ok_vr = (math.isfinite(vratio) and vratio >= vr_min)

    ok_trend_hint = True
    if req_hint:
        # local_trend içinde yönü ima eden basit kontrol (AI tarafında "📈 Yükseliş"/"📉 Düşüş" geliyor)
        if diru == "LONG":
            ok_trend_hint = ("📈" in lt) or ("YÜKSEL" in lt.upper())
        else:
            ok_trend_hint = ("📉" in lt) or ("DÜŞ" in lt.upper())

    if ok_adx and ok_vr and ok_trend_hint:
        logging.info(
            "[BB_FILTER_OVERRIDE] sym=%s tf=%s dir=%s reason=edge_override pb=%.4f edge=(L>=%.2f S<=%.2f) adx=%.2f vr=%.2f lt=%r",
            symbol, timeframe, diru, pb, long_pb_max, short_pb_min, ax, vratio, lt
        )
        return True, "edge_override"

    # Hard skip
    logging.info(
        "[BB_FILTER_SKIP] sym=%s tf=%s dir=%s reason=edge_no_override pb=%.4f edge=(L>=%.2f S<=%.2f) adx=%s vr=%s lt=%r",
        symbol, timeframe, diru, pb, long_pb_max, short_pb_min,
        (f"{ax:.2f}" if math.isfinite(ax) else "nan"),
        (f"{vratio:.2f}" if math.isfinite(vratio) else "nan"),
        lt
    )
    return False, "edge_no_override"


def _resolve_to_futures_symbol(cls, raw_symbol: str) -> Optional[str]:
    """
    Exchange-agnostic: verilen sembolü (spot veya futures) o anki borsanın
    gerçek futures/swap market symbol'üne çevirir.
    - Öncelik: markets içindeki contract/swap marketler
    - Bulamazsa: cls.to_ccxt_symbol(..., prefer_futures=True) fallback
    """
    s = str(raw_symbol or "").strip()
    if not s:
        return None

    # 0) Zaten geçerli futures market mi?
    try:
        if cls.has_futures_market(s):
            return s
    except Exception:
        pass

    # 1) Spot-like normalize et (BTC/USDT:USDT -> BTC/USDT, BTC_USDT -> BTC/USDT)
    spot_like = _norm_symbol_for_match(s)
    if "/" not in spot_like:
        # bazı borsalar BTCUSDT gibi verebilir; burada senin normalize_symbol devreye girebilir
        try:
            spot_like = _norm_symbol_for_match(cls.normalize_symbol(spot_like))
        except Exception:
            pass
    if "/" not in spot_like:
        return None

    base, quote = spot_like.split("/", 1)

    # 2) markets içinden aynı base/quote futures/swap bul
    ex = getattr(cls, "exchange", None)
    markets = getattr(ex, "markets", None) if ex else None
    if isinstance(markets, dict) and markets:
        best: Optional[str] = None
        for m_sym, m in markets.items():
            try:
                if not isinstance(m, dict):
                    continue

                m_base = str(m.get("base") or "").upper()
                m_quote = str(m.get("quote") or "").upper()
                if m_base != base or m_quote != quote:
                    continue

                # futures/swap mı?
                m_contract = bool(m.get("contract"))
                m_swap = bool(m.get("swap"))
                m_future = bool(m.get("future"))
                m_type = str(m.get("type") or "").lower()
                is_derivative = m_contract or m_swap or m_future or (m_type in ("swap", "future", "futures"))

                if not is_derivative:
                    continue

                # aktif mi?
                if m.get("active") is False:
                    continue

                # burada "en iyi" seçimi yapabiliriz (örn. linear tercih)
                best = str(m.get("symbol") or m_sym)
                # ilk bulduğunu döndürmek de yeterli; ama best ile son bulduğumuz kalır.
            except Exception:
                continue

        if best:
            return best

    # 3) Fallback: mevcut helper (borsaya göre değişebilir)
    try:
        cc = cls.to_ccxt_symbol(spot_like, prefer_futures=True)
        if cc and cls.has_futures_market(cc):
            return cc
    except Exception:
        pass

    return None


def _resolve_futures_symbol(cls, raw_symbol: str) -> Optional[str]:
    """
    Exchange-agnostic futures resolver.
    - raw_symbol futures ise -> aynen döndürür
    - spot-like ise -> exchange.markets içinde base/quote aynı olan swap/future contract marketi bulur
    - bulamazsa -> cls.to_ccxt_symbol(..., prefer_futures=True) ile dener
    """
    s = str(raw_symbol or "").strip()
    if not s:
        return None

    # 0) Zaten futures market mi?
    try:
        if cls.has_futures_market(s):
            return s
    except Exception:
        pass

    # 1) spot-like normalize
    spot_like = _norm_symbol_for_match(s)  # BTC/USDT:USDT -> BTC/USDT
    if "/" not in spot_like:
        try:
            spot_like = _norm_symbol_for_match(cls.normalize_symbol(spot_like))
        except Exception:
            pass
    if "/" not in spot_like:
        return None

    base, quote = spot_like.split("/", 1)

    ex = getattr(cls, "exchange", None)
    markets = getattr(ex, "markets", None) if ex else None
    if isinstance(markets, dict) and markets:
        best = None

        for m_key, m in markets.items():
            if not isinstance(m, dict):
                continue

            try:
                m_base = str(m.get("base") or "").upper()
                m_quote = str(m.get("quote") or "").upper()
                if m_base != base or m_quote != quote:
                    continue

                # derivative mi?
                m_type = str(m.get("type") or "").lower()
                is_derivative = bool(m.get("contract") or m.get("swap") or m.get("future")) or (m_type in ("swap", "future", "futures"))
                if not is_derivative:
                    continue

                if m.get("active") is False:
                    continue

                # çoğu borsada m["symbol"] == m_key, ama garanti olsun diye ikisini de deneyebiliriz
                cand1 = str(m.get("symbol") or "")
                cand2 = str(m_key)

                # has_futures_market hangi formatı bekliyorsa yakalamak için:
                for cand in (cand1, cand2):
                    if cand:
                        try:
                            if cls.has_futures_market(cand):
                                return cand
                        except Exception:
                            pass

                # en azından aday kalsın (son çare)
                best = cand1 or cand2

            except Exception:
                continue

        if best:
            return best

    # 2) Fallback: sistemin genel dönüştürücüsü
    try:
        cc = cls.to_ccxt_symbol(spot_like, prefer_futures=True)
        if cc and cls.has_futures_market(cc):
            return cc
    except Exception:
        pass

    return None


# do_ai_scan
async def do_ai_scan(
    cls,
    timeframe: str,
    strategy: str,
    limit: int,
    chat_id: int,
    context: CallbackContext,
    user_id: int,
    progress_callback: Optional[Callable[[int, int], Awaitable[None]]] = None
) -> Dict[str, Any]:

    if not hasattr(cls, "processing_symbols") or cls.processing_symbols is None:
        cls.processing_symbols = set()

    if not hasattr(cls, "_ohlcv_sem") or cls._ohlcv_sem is None:
        cls._ohlcv_sem = asyncio.Semaphore(3)

    # --- Güvenli importlar ---
    try:
        from strategies.alarm_system.analytics import SymbolPerformanceTracker
    except Exception:
        SymbolPerformanceTracker = None  # type: ignore

    try:
        from strategies.technical_indicators import TechnicalIndicators
    except Exception:
        TechnicalIndicators = None  # type: ignore

    async def _safe_send_message(
        *,
        target_chat_id: int,
        text: str,
        reply_markup: Optional[InlineKeyboardMarkup] = None
    ) -> bool:
        try:
            try:
                chat_obj = await context.bot.get_chat(target_chat_id)
                if getattr(chat_obj, "is_bot", False):
                    logging.error(f"[TG_SEND_SKIP_BOT_TARGET] chat_id={target_chat_id} is_bot=True")
                    return False
            except Exception as e1:
                logging.warning(f"[TG_GET_CHAT_WARN] chat_id={target_chat_id} err={e1}")

            await context.bot.send_message(chat_id=target_chat_id, text=text, reply_markup=reply_markup)
            return True
        except Exception as e2:
            msg = str(e2)
            if "bots can't send messages to bots" in msg or "Forbidden" in msg:
                logging.error(f"[TG_SEND_FORBIDDEN] chat_id={target_chat_id} err={e2}")
                return False
            logging.error(f"[TG_SEND_ERR] chat_id={target_chat_id} err={e2}", exc_info=True)
            return False

    # 0) Strateji seçimi
    raw_strategy = (strategy or "").strip().lower()
    if raw_strategy in ("", "both", "all", "v1+v2"):
        selected_strategy = "both"
    elif raw_strategy in ("v1", "v2"):
        selected_strategy = raw_strategy
    else:
        selected_strategy = "both"

    logging.info(f"[AI_SCAN_START] tf={timeframe} strategy={strategy} -> selected={selected_strategy}")

    summary: Dict[str, int] = {
        "pool_in": 0,
        "pool_after_cs": 0,
        "analyzed_ok": 0,
        "rej_bb_filter":0,

        "skip_no_ccxt": 0,
        "skip_no_futures_market": 0,
        "skip_no_ohlcv": 0,
        "skip_short_ohlcv": 0,
        "skip_indicator_err": 0,
        "skip_blacklist": 0,

        "rej_conf": 0,
        "rej_potential": 0,
        "rej_volume_usd": 0,
        "rej_volume_ratio": 0,

        "pass_main": 0,
        "pass_alt": 0,
    }
    bb_cfg = _get_bb_edge_filter_cfg(timeframe)

    last_progress_push = 0.0
    progress_min_interval = 0.8

    async def _progress(done: int, total: int) -> None:
        nonlocal last_progress_push
        if not progress_callback:
            return
        now_ts = time.monotonic()
        if (now_ts - last_progress_push) >= progress_min_interval or done >= total:
            last_progress_push = now_ts
            await progress_callback(done, total)

    async def _with_timeout(coro, timeout_sec: float, tag: str):
        try:
            return await asyncio.wait_for(coro, timeout=timeout_sec)
        except asyncio.TimeoutError:
            logging.warning(f"[TIMEOUT] tag={tag} timeout_sec={timeout_sec}")
            raise

    def safe_int(x, default: int = 2) -> int:
        try:
            if x is None:
                return default
            if isinstance(x, bool):
                return default
            if isinstance(x, int):
                return x
            if isinstance(x, float):
                if math.isnan(x) or math.isinf(x):
                    return default
                return int(x)
            if isinstance(x, str):
                s = x.strip()
                if not s:
                    return default
                return int(float(s))
            return int(x)
        except Exception:
            return default

    def _get_momentum_lookback() -> int:
        try:
            lb = None
            st = getattr(cls, "strategy", None)
            if isinstance(st, dict):
                common = st.get("common")
                if isinstance(common, dict):
                    lb = common.get("momentum_period")

            if lb is None:
                lb = ConfigService.get("strategy.common.momentum_period", 2)

            return max(2, safe_int(lb, default=2))
        except Exception:
            return 2

    def _pick_vr(indicators: Dict[str, Any], fallback_vr: float) -> float:
        """İndikatörden VR varsa onu kullan, yoksa df VR."""
        try:
            for k in ("volume_ratio", "vr", "VR"):
                if k in indicators:
                    v = float(indicators.get(k) or 0.0)
                    if v > 0:
                        return v
        except Exception:
            pass
        return float(fallback_vr or 1.0)

    async def _predict_ai_confidence(ccxt_symbol: str, df, indicators: Dict[str, Any], fallback_conf: float) -> float:
        """
        Gerçek AI confidence üretmeye çalışır.
        Model metodunu bilmediğimiz için 'varsa kullan' stratejisi.
        """
        model = getattr(cls, "_ai_model", None)
        if not model:
            return float(fallback_conf)

        # Olası metod isimleri (sende hangisi varsa)
        for fn_name in ("predict_confidence", "predict_proba_confidence", "predict_signal_confidence"):
            fn = getattr(model, fn_name, None)
            if callable(fn):
                try:
                    # bazı implementasyonlar df/indicators ister
                    val = fn(symbol=ccxt_symbol, timeframe=timeframe, df=df, indicators=indicators)
                    conf = float(val or 0.0)
                    return max(0.0, min(1.0, conf))
                except TypeError:
                    try:
                        val = fn(df, indicators)
                        conf = float(val or 0.0)
                        return max(0.0, min(1.0, conf))
                    except Exception:
                        pass
                except Exception:
                    pass

        return float(fallback_conf)

    try:
        # 1) Exchange kontrol
        ex = getattr(cls, "exchange", None)
        if not ex:
            logging.warning("[AI_SCAN] exchange yok; initialize_exchange üst katmanda yapılmalı. tarama atlandı.")
            return {"sent_count": 0, "created_symbols": [], "strategy": strategy, "timeframe": timeframe}

        exchange_name = getattr(ex, "id", "mexc")
        _ensure_market_data_service(cls)

        # 2) Model eğitim/loader
        last_train_time = cls.get_last_train_time_for_exchange(exchange_name)
        needs_training = True
        if last_train_time:
            time_since_training = datetime.now(timezone.utc) - last_train_time
            if time_since_training.total_seconds() < (24 * 3600):
                needs_training = False
                logging.info(f"✅ {exchange_name.upper()} modeli güncel, eğitim atlanıyor.")

        if needs_training:
            keyboard = [[InlineKeyboardButton("🏠 Ana Menü", callback_data="main_menu")]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await _safe_send_message(
                target_chat_id=chat_id,
                text=f"🎓 {exchange_name.upper()} için AI modeli eğitiliyor, lütfen bekleyin...",
                reply_markup=reply_markup
            )

            trained_ok = await cls.train_ai_model_dynamic(exchange=exchange_name, triggered_by_user_id=user_id)
            if not trained_ok:
                await _safe_send_message(target_chat_id=chat_id, text="❌ Eğitim başarısız.", reply_markup=reply_markup)
                return {"sent_count": 0, "created_symbols": [], "strategy": strategy, "timeframe": timeframe}

        if not getattr(cls, "_ai_model", None):
            cls._ai_model = RealAIModel()

        if not needs_training:
            loaded_ok = cls._ai_model.load_models_for_exchange(exchange_name)
            if not loaded_ok:
                return {"sent_count": 0, "created_symbols": [], "strategy": strategy, "timeframe": timeframe}

        # 3) Ayarlar + limitler + eşikler
        scans_cfg = cls._load_scan_settings() or {}
        if not isinstance(scans_cfg, dict):
            scans_cfg = {}
        settings = scans_cfg

        tf_profile = (scans_cfg.get("tf_profiles", {}) or {}).get(timeframe, {}) or {}
        ai_scan_config = tf_profile.get("ai_scan", {}) or {}
        limits_config = ai_scan_config.get("limits", {}) or {}

        if raw_strategy in ("v1", "v2"):
            limit_v1 = int(limit) if raw_strategy == "v1" else 0
            limit_v2 = int(limit) if raw_strategy == "v2" else 0
        else:
            cfg_v1 = int(limits_config.get("v1", 0) or 0)
            cfg_v2 = int(limits_config.get("v2", 0) or 0)
            total = int(limit) if limit is not None else (cfg_v1 + cfg_v2)
            total = max(0, total)
            limit_v1 = max(0, total // 2)
            limit_v2 = max(0, total - limit_v1)

        limit_v1 = max(0, int(limit_v1))
        limit_v2 = max(0, int(limit_v2))

        th_raw = cls.get_ai_thresholds_from_config(timeframe) or {}
        mode = ConfigService.control_mode().upper()
        logging.info(f"[HEALTH_MODE] control_mode={mode} health_policy=enabled")
        min_score = ConfigService.get("scans.tf_profiles.15m.strategy_scan.v1.min_score.value", None)
        logger.info(f"[THRESH_USED] tf=15m strategy_scan.v1 min_score={min_score}")

        alt_rule = th_raw.get("alt_accept_rule") or {}
        if not isinstance(alt_rule, dict):
            alt_rule = {}

        th = {
            "min_conf": float(th_raw.get("min_conf", 0.60)),
            "min_potential_pct": float(th_raw.get("min_potential_pct", 0.2)),
            "min_volume_usd": float(th_raw.get("min_volume_usd", 2_000_000)),
            "min_volume_ratio": float(th_raw.get("min_volume_ratio", 0.40)),
            "diversify_by_sector": bool(th_raw.get("diversify_by_sector", True)),
            "alt_accept_rule": {
                "enabled": bool(alt_rule.get("enabled", True)),
                "extra_conf": float(alt_rule.get("extra_conf", 0.03)),
                "min_volume_multiplier": float(alt_rule.get("min_volume_multiplier", 1.1)),
                "min_vr_floor": float(alt_rule.get("min_vr_floor", 0.35)),
            }
        }

        logging.info(
            f"[{exchange_name.upper()}|AI_SCAN|{timeframe}|{mode}] "
            f"Limitler -> V1:{limit_v1}, V2:{limit_v2} | th={th}"
        )

        # 4) Ticker -> futures filtre
        tickers = await cls.safe_fetch_tickers()
        futures_rows = await cls.filter_symbols_by_volume_futures_only(tickers)
        if not futures_rows:
            logging.warning("[AI_SCAN] futures_rows boş (volume filtresi sonrası)")
            return {"sent_count": 0, "created_symbols": [], "strategy": strategy, "timeframe": timeframe}

        # 5) Kandidat havuzu
        pool_size = max((limit_v1 + limit_v2) * 5, 50)
        candidates_pool = futures_rows[:pool_size]

        unique_map: Dict[tuple, dict] = {}
        for row in candidates_pool:
            s = str(row.get("symbol") or "").strip().upper()
            key = (s, timeframe)
            if s and key not in unique_map:
                unique_map[key] = row
        candidates_pool = list(unique_map.values())
        summary["pool_in"] = len(candidates_pool)

        symbol_tracker = SymbolPerformanceTracker() if SymbolPerformanceTracker else None
        last_tickers = getattr(cls, "_last_tickers", {}) or {}

        # 5.b) CandidateSelector
        use_cs = bool((ConfigService.get("CANDIDATE_SELECTION", {}) or {}).get("enabled", True))
        if use_cs:
            try:
                md = getattr(cls, "md", None) or getattr(cls, "market_data_service", None)
                if md is None:
                    logging.warning("[CANDIDATE_SELECTOR] md bulunamadı; CandidateSelector atlandı.")
                else:
                    sid_for_select = "v1" if selected_strategy in ("v1", "both") else "v2"
                    cs_symbols = await _select_symbols_via_candidate_selector(
                        md,
                        cls._ai_model,
                        sid_for_select,
                        to_futures_symbol=lambda s: (_resolve_futures_symbol(cls, s) or s),
                    )
                    cs_norm_set = {_norm_symbol_for_match(x) for x in (cs_symbols or []) if x}

                    before = len(candidates_pool)
                    candidates_pool = [
                        r for r in candidates_pool
                        if _norm_symbol_for_match(r.get("symbol")) in cs_norm_set
                    ]

                    logging.info(f"[CANDIDATE_SELECTOR_POOL] in_futures={len(futures_rows)} before={before} out_pool={len(candidates_pool)}")
            except Exception as e1:
                logging.error(f"[CANDIDATE_SELECTOR_FATAL] {e1}", exc_info=True)

        summary["pool_after_cs"] = len(candidates_pool)

        # 6) İlerleme
        total_symbols_to_analyze = len(candidates_pool)
        processed_count = 0
        await _progress(0, total_symbols_to_analyze)

        analyzed: List[Dict[str, Any]] = []
        sem = asyncio.Semaphore(6)

        async def analyze_one_symbol(row1: Dict[str, Any]) -> None:
            nonlocal processed_count

            symbol_name = row1.get("symbol")
            s_norm = str(symbol_name or "").strip().upper()
            sym_key = (s_norm, timeframe)

            if not s_norm:
                processed_count += 1
                await _progress(processed_count, total_symbols_to_analyze)
                return

            if sym_key in cls.processing_symbols:
                processed_count += 1
                await _progress(processed_count, total_symbols_to_analyze)
                return

            cls.processing_symbols.add(sym_key)
            try:
                async with sem:
                    ccxt_symbol = cls.to_ccxt_symbol(s_norm, prefer_futures=True)
                    if not ccxt_symbol:
                        summary["skip_no_ccxt"] += 1
                        return

                    if not cls.has_futures_market(ccxt_symbol):
                        summary["skip_no_futures_market"] += 1
                        return

                    # OHLCV
                    try:
                        # fetch_ohlcv_with_retry zaten:
                        # - cls._ohlcv_sem ile concurrency kısıtlıyor
                        # - per-attempt timeout uyguluyor
                        # - retry yapıyor
                        #
                        # O yüzden burada semaforu bir daha acquire ETMİYORUZ.

                        # Outer timeout'u, inner retry'ların worst-case süresinden büyük tut:
                        # max_retries=2, timeout=20 => ~41s + jitter payı
                        max_retries = 2
                        per_try_timeout = 20.0
                        backoff_total = sum(0.6 * i for i in range(1, max_retries))  # 0.6
                        outer_timeout = (max_retries * per_try_timeout) + backoff_total + 10.0  # +10s buffer

                        df = await _with_timeout(
                            fetch_ohlcv_with_retry(
                                cls,
                                ccxt_symbol,
                                timeframe=timeframe,
                                max_retries=2,
                                timeout=20,
                                context=context,
                            ),
                            timeout_sec=outer_timeout,
                            tag=f"ohlcv:{ccxt_symbol}:{timeframe}"
                        )


                    except asyncio.TimeoutError:
                        summary["skip_no_ohlcv"] += 1
                        return
                    except Exception as _fe:
                        summary["skip_no_ohlcv"] += 1
                        logging.debug(f"[OHLCV_ERR] {ccxt_symbol} err={_fe}")
                        return

                    if df is None or getattr(df, "empty", True):
                        summary["skip_no_ohlcv"] += 1
                        return
                    if len(df) < 60:
                        summary["skip_short_ohlcv"] += 1
                        return

                    # df VR
                    df_vr = 1.0
                    if "volume" in df.columns:
                        try:
                            volumes = df["volume"].astype(float).to_numpy()
                            if len(volumes) >= 25:
                                cur_v = float(volumes[-1])
                                avg_v = float(pd.Series(volumes[-20:]).mean())
                                if avg_v > 0:
                                    df_vr = cur_v / avg_v
                        except Exception:
                            df_vr = 1.0

                    # indicators (thread + timeout)
                    indicators: Dict[str, Any] = {}
                    if TechnicalIndicators:
                        try:
                            indicators_raw = await _with_timeout(
                                asyncio.to_thread(TechnicalIndicators.calculate_all, df, ccxt_symbol),
                                timeout_sec=12.0,
                                tag=f"ind:{ccxt_symbol}:{timeframe}"
                            )
                            indicators = indicators_raw if isinstance(indicators_raw, dict) else {}
                        except asyncio.TimeoutError:
                            summary["skip_indicator_err"] += 1
                            return
                        except Exception as ind_err:
                            summary["skip_indicator_err"] += 1
                            logging.debug(f"[IND_ERR] {ccxt_symbol}: {ind_err}")
                            indicators = {}

                    # pot / score
                    pot = 0.0
                    technical_score = 0.0
                    last_close = 0.0

                    try:
                        close_arr = df["close"].astype(float).to_numpy()
                        last_close = float(close_arr[-1])
                        lookback_p = _get_momentum_lookback()
                        if len(close_arr) > lookback_p:
                            base = float(close_arr[-lookback_p - 1])
                            pot = (last_close - base) / base if base != 0 else 0.0

                        rsi = float(indicators.get("rsi") or 50.0)
                        lt = str(indicators.get("local_trend") or "")
                        if "📈" in lt or "Yükseliş" in lt:
                            trend_flag = 1.0
                        elif "📉" in lt or "Düşüş" in lt:
                            trend_flag = 0.0
                        else:
                            ema200 = float(indicators.get("ema_200") or 0.0)
                            trend_flag = 1.0 if 0 < ema200 < last_close else 0.0

                        technical_score = max(0.0, min(100.0, (rsi / 100.0) * 60.0 + trend_flag * 40.0))
                    except Exception as calc_err:
                        logging.debug(f"[AI_METRIC_ERR] {ccxt_symbol}: {calc_err}")
                        try:
                            last_close = float(df["close"].iloc[-1])
                        except Exception:
                            last_close = 0.0
                    logging.info("[AI_IND_KEYS] sym=%s tf=%s keys_sample=%s",
                        ccxt_symbol, timeframe, sorted(list(indicators.keys()))[:25])

                    fallback_conf = max(0.0, min(1.0, technical_score / 100.0))
                    ai_conf = await _predict_ai_confidence(ccxt_symbol, df, indicators, fallback_conf=fallback_conf)

                    # volume_usd
                    try:
                        volume_usd = float(row1.get("volume_24h_usdt") or 0.0)
                    except Exception:
                        volume_usd = 0.0
                    if volume_usd <= 0.0:
                        try:
                            base_spot = ccxt_symbol.split(":", 1)[0]
                            tick = {}
                            if isinstance(last_tickers, dict):
                                tick = last_tickers.get(ccxt_symbol) or last_tickers.get(base_spot) or {}
                            vol_fn = getattr(cls, "safe_ticker_volume_usd", None) or getattr(cls,
                                "_safe_ticker_volume_usd", None)
                            if callable(vol_fn):
                                volume_usd = float(vol_fn(tick) or 0.0)
                            else:
                                volume_usd = 0.0
                        except Exception:
                            volume_usd = 0.0

                    # VR: indikatörden varsa onu al
                    vr = _pick_vr(indicators, df_vr)

                    ai_row: Dict[str, Any] = {
                        "symbol": ccxt_symbol,
                        "timeframe": timeframe,
                        "ai_confidence": ai_conf,
                        "potential_pct": pot,
                        "technical_score": technical_score,
                        "volume_usd": volume_usd,
                        "volume_ratio": vr,
                        "direction": "LONG" if pot >= 0 else "SHORT",
                        "signal": "BUY" if pot >= 0 else "SELL",
                        "market_type": "swap",
                        "indicators": indicators,
                        "adx": indicators.get("adx", 0.0),
                        "rsi": indicators.get("rsi", 50.0),
                        "stoch_k": indicators.get("stoch_k", 50.0),
                        "stoch_d": indicators.get("stoch_d", 50.0),
                        "bb_width": indicators.get("bb_width", 0.0),
                        "momentum_tension": indicators.get("momentum_tension", 0.0),
                        "obv_slope": indicators.get("obv_slope", 0.0),
                        "obv_status": indicators.get("obv_status", "⚪️ Nötr"),
                        "local_trend": indicators.get("local_trend", "❓ Bilinmiyor"),
                        "local_regime": indicators.get("local_trend", "❓ Bilinmiyor"),
                        "close": last_close,
                    }

                    if selected_strategy in ("v1", "both"):
                        ai_row["v1_score"] = cls.compute_strategy_fit_score(ai_row, "v1", timeframe, settings, "ai_scan")
                    if selected_strategy in ("v2", "both"):
                        ai_row["v2_score"] = cls.compute_strategy_fit_score(ai_row, "v2", timeframe, settings, "ai_scan")

                    # Health
                    if symbol_tracker:
                        sym_for_health = str(ai_row["symbol"] or "")
                        base_sym = sym_for_health.split(":", 1)[0]
                        symbol_health = (
                            symbol_tracker.get_symbol_health(sym_for_health)
                            or symbol_tracker.get_symbol_health(base_sym)
                            or {}
                        )

                        factor = float(symbol_health.get("penalty_factor", 1.0) or 1.0)
                        priority_bonus = float(symbol_health.get("priority_bonus", 0.0) or 0.0)
                        status = str(symbol_health.get("status", "neutral"))
                        reason = str(symbol_health.get("reason", ""))
                        action = str(symbol_health.get("action", "allow"))
                        cooldown_until = str(symbol_health.get("cooldown_until", ""))

                        logging.info(
                            f"[RP][AI] mode={mode} sym={sym_for_health} base={base_sym} "
                            f"factor={factor:.2f} bonus={priority_bonus:.2f} "
                            f"status={status} action={action} until={cooldown_until} reason={reason}"
                        )

                        if action == "blacklist_skip":
                            summary["skip_blacklist"] += 1
                            logging.info(f"[RP_SKIP] {sym_for_health} skipped due to blacklist. reason={reason} until={cooldown_until}")
                            return

                        if factor != 1.0:
                            if "v1_score" in ai_row:
                                ai_row["v1_score"] = float(ai_row["v1_score"]) * factor
                            if "v2_score" in ai_row:
                                ai_row["v2_score"] = float(ai_row["v2_score"]) * factor

                        if priority_bonus:
                            if "v1_score" in ai_row:
                                ai_row["v1_score"] = float(ai_row["v1_score"]) + priority_bonus
                            if "v2_score" in ai_row:
                                ai_row["v2_score"] = float(ai_row["v2_score"]) + priority_bonus

                    analyzed.append(ai_row)
                    summary["analyzed_ok"] += 1

            finally:
                cls.processing_symbols.discard(sym_key)
                processed_count += 1
                await _progress(processed_count, total_symbols_to_analyze)

        tasks = [asyncio.create_task(analyze_one_symbol(r)) for r in candidates_pool]
        hard_timeout = max(120.0, total_symbols_to_analyze * 8.0)
        try:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=hard_timeout)
        except asyncio.TimeoutError:
            pending = [t for t in tasks if not t.done()]
            logging.warning(f"[AI_SCAN_HARD_TIMEOUT] pending={len(pending)}/{len(tasks)} hard_timeout={hard_timeout}")
            for t in pending:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        await _progress(total_symbols_to_analyze, total_symbols_to_analyze)

        # 7) Eşik filtreleri
        min_pot = float(th.get("min_potential_pct", 0.0)) / 100.0

        # 15m için: config’e saygı + çok sert olmasın
        base_vr = float(th.get("min_volume_ratio", 0.4))
        eff_vr = base_vr  # artık zorla 0.6 yapmıyoruz

        alt = th["alt_accept_rule"]
        alt_enabled = bool(alt.get("enabled", False))
        alt_extra_conf = float(alt.get("extra_conf", 0.03))
        alt_vol_mult = float(alt.get("min_volume_multiplier", 1.1))
        alt_vr_floor = float(alt.get("min_vr_floor", 0.35))

        filtered: List[Dict[str, Any]] = []
        for row_ai in analyzed:
            try:
                conf = float(row_ai.get("ai_confidence", 0.0) or 0.0)
                pot_raw = float(row_ai.get("potential_pct", 0.0) or 0.0)
                if 1.0 < abs(pot_raw) <= 300.0:
                    pot_raw /= 100.0
                pot_abs = abs(pot_raw)

                vol_usd = float(row_ai.get("volume_usd", 0.0) or 0.0)
                vr = float(row_ai.get("volume_ratio", 1.0) or 1.0)

                if conf < th["min_conf"]:
                    summary["rej_conf"] += 1
                if pot_abs < min_pot:
                    summary["rej_potential"] += 1
                if vol_usd < th["min_volume_usd"]:
                    summary["rej_volume_usd"] += 1
                if vr < eff_vr:
                    summary["rej_volume_ratio"] += 1
                # --- BB EDGE FILTER (Hard-skip + override) ---
                ind = row_ai.get("indicators") if isinstance(row_ai.get("indicators"), dict) else {}
                bb_pb = ind.get("bb_percent_b", None)
                bb_w = ind.get("bb_width", row_ai.get("bb_width", None))
                dirn = str(row_ai.get("direction", "") or "").upper()
                adx_v = row_ai.get("adx", ind.get("adx", None))
                lt = row_ai.get("local_trend", ind.get("local_trend", ""))

                bb_pb_f = _to_float_opt(bb_pb)
                bb_w_f = _to_float_opt(bb_w)
                adx_f = _to_float_opt(adx_v)
                vr_f = _to_float_opt(vr)  # vr zaten float ama yine de güvenli

                allowed, bb_reason = _bb_edge_decision(
                    symbol=str(row_ai.get("symbol") or ""),
                    timeframe=str(timeframe),
                    direction=dirn,
                    bb_percent_b=bb_pb_f,
                    bb_width=bb_w_f,
                    adx=adx_f,
                    vr=vr_f,
                    local_trend=str(lt or ""),
                    cfg=bb_cfg,
                )

                if not allowed:
                    summary["rej_bb_filter"] += 1
                    continue

                ok_main = (conf >= th["min_conf"] and pot_abs >= min_pot and vol_usd >= th["min_volume_usd"] and vr >= eff_vr)

                ok_alt = False
                if alt_enabled:
                    ok_alt = (
                        (pot_abs < min_pot)
                        and (conf >= th["min_conf"] + alt_extra_conf)
                        and (vol_usd >= th["min_volume_usd"] * alt_vol_mult)
                        and (vr >= max(alt_vr_floor, eff_vr - 0.2))
                    )

                if ok_main:
                    summary["pass_main"] += 1
                elif ok_alt:
                    summary["pass_alt"] += 1

                if ok_main or ok_alt:
                    row_ai["potential_pct"] = pot_raw
                    filtered.append(row_ai)
            except Exception:
                continue

        v1_candidates: List[Dict[str, Any]] = []
        v2_candidates: List[Dict[str, Any]] = []

        if selected_strategy in ("v1", "both"):
            v1_candidates = [x for x in filtered if float(x.get("v1_score", 0.0) or 0.0) > 0]
            v1_candidates.sort(key=lambda x: float(x.get("v1_score", 0.0) or 0.0), reverse=True)

        if selected_strategy in ("v2", "both"):
            v2_candidates = [x for x in filtered if float(x.get("v2_score", 0.0) or 0.0) > 0]
            v2_candidates.sort(key=lambda x: float(x.get("v2_score", 0.0) or 0.0), reverse=True)

        if selected_strategy == "v1":
            v1_final = cls.diversify_and_limit(v1_candidates, settings, timeframe, "v1")
            v2_final = []
        elif selected_strategy == "v2":
            v1_final = []
            v2_final = cls.diversify_and_limit(v2_candidates, settings, timeframe, "v2")
        else:
            v1_temp = cls.diversify_and_limit(v1_candidates, settings, timeframe, "v1")
            v2_temp = cls.diversify_and_limit(v2_candidates, settings, timeframe, "v2")
            v1_final, v2_final = cls.cross_deduplicate(v1_temp, v2_temp)

        v1_final = v1_final[: max(limit_v1 * 3, limit_v1)] if limit_v1 > 0 else []
        v2_final = v2_final[: max(limit_v2 * 3, limit_v2)] if limit_v2 > 0 else []

        logging.info(
            "[AI_SCAN_PIPE] filtered=%d v1_candidates=%d v2_candidates=%d v1_final=%d v2_final=%d limit_v1=%d limit_v2=%d",
            len(filtered),
            len(v1_candidates) if 'v1_candidates' in locals() else -1,
            len(v2_candidates) if 'v2_candidates' in locals() else -1,
            len(v1_final) if 'v1_final' in locals() else -1,
            len(v2_final) if 'v2_final' in locals() else -1,
            limit_v1, limit_v2
        )

        logging.info(f"[AI_SCAN][LIMITS_APPLIED] V1: {len(v1_final)}/{limit_v1}, V2: {len(v2_final)}/{limit_v2}")

        sent = 0
        created_symbols: List[str] = []

        async def setup_rows(rows: List[Dict[str, Any]], sid: str, need: int) -> None:
            nonlocal sent, created_symbols
            need = int(need or 0)
            if need <= 0:
                return

            created_here = 0
            for item_ai in rows:
                if created_here >= need:
                    break
                try:
                    sym = str(item_ai.get("symbol") or "").strip()
                    if not sym:
                        continue

                    sym_ccxt = _resolve_futures_symbol(cls, sym)
                    if not sym_ccxt:
                        continue

                    meta = {
                        "source": "ai_scan",
                        "ai_scan": True,
                        "timeframe": timeframe,
                        "strategy_id": sid,
                        "chat_id": chat_id,
                        "market_type": "swap",
                        "ai_confidence": item_ai.get("ai_confidence", 0.0),
                        "potential_pct": item_ai.get("potential_pct", 0.0),
                        "technical_score": item_ai.get("technical_score", 0.0),
                        "volume_usd": item_ai.get("volume_usd", 0.0),
                        "volume_ratio": item_ai.get("volume_ratio", 1.0),
                        "indicators": item_ai.get("indicators", {}),
                    }

                    added_ok = await cls.add_alarm_debug(
                        context=context,
                        symbol=sym_ccxt,
                        timeframe=timeframe,
                        user_id=user_id,
                        strategy_id=sid,
                        source_meta=meta
                    )
                    if added_ok:
                        sent += 1
                        created_symbols.append(sym_ccxt)
                        created_here += 1
                    else:
                        logging.warning(f"[AI_ALARM_CREATE_FAIL] sym={sym_ccxt} tf={timeframe} sid={sid}")

                except Exception as e1:
                    logging.error(f"[AI_SCAN_ADD_ALARM_ERR] {item_ai.get('symbol', '?')}: {e1}", exc_info=True)

        await setup_rows(v1_final, "v1", limit_v1)
        await setup_rows(v2_final, "v2", limit_v2)

        logging.info(
            "[AI_SCAN_SUMMARY] ex=%s tf=%s selected=%s pool_in=%d pool_after_cs=%d rej_bb_filter=%d analyzed_ok=%d "
            "skip_no_ccxt=%d skip_no_futures=%d skip_no_ohlcv=%d skip_short_ohlcv=%d skip_ind_err=%d skip_blacklist=%d "
            "rej_conf=%d rej_pot=%d rej_vol_usd=%d rej_vr=%d pass_main=%d pass_alt=%d created=%d",
            exchange_name, timeframe, selected_strategy,
            summary["pool_in"], summary["pool_after_cs"], summary["rej_bb_filter"], summary["analyzed_ok"],
            summary["skip_no_ccxt"], summary["skip_no_futures_market"], summary["skip_no_ohlcv"], summary["skip_short_ohlcv"],
            summary["skip_indicator_err"], summary["skip_blacklist"],
            summary["rej_conf"], summary["rej_potential"], summary["rej_volume_usd"], summary["rej_volume_ratio"],
            summary["pass_main"], summary["pass_alt"],
            sent
        )

        return {"sent_count": sent, "created_symbols": created_symbols, "strategy": strategy, "timeframe": timeframe}

    except asyncio.CancelledError:
        raise
    except Exception as err_unexpected:
        logging.exception(err_unexpected)
        return {"sent_count": 0, "created_symbols": [], "strategy": strategy, "timeframe": timeframe}


# _do_strategy_scan
async def _do_strategy_scan(
    cls,
    timeframe: str,
    strategy: str,
    limit: int,
    chat_id: int,
    context: CallbackContext,
    user_id: int,
    progress_callback: Optional[Callable[[int, int], Awaitable[None]]] = None
) -> Dict[str, Any]:
    """
    Strateji Taraması (yeni yapı uyumlu):
    - symbols: safe_fetch_tickers + futures volume filter
    - ohlcv: fetch_ohlcv_with_retry (semafor/retry/session-safe)
    - indicators: candidate içine eklenir, meta'da tek kaynak olarak taşınır
    """
    _ = chat_id  # bu fonksiyonda chat_id kullanılmıyor (alarm göndermek user_id üzerinden)
    symbol_tracker = SymbolPerformanceTracker()
    if not hasattr(cls, "processing_symbols") or cls.processing_symbols is None:
        cls.processing_symbols = set()
    if not hasattr(cls, "_ohlcv_sem") or cls._ohlcv_sem is None:
        cls._ohlcv_sem = asyncio.Semaphore(3)  # örnek: 3
    try:
        logging.info(f"[STRAT_SCAN] tf={timeframe} strategy={strategy}")

        # ✅ health policy mode log (1 kez)
        mode = ConfigService.control_mode().upper()
        logging.info(f"[HEALTH_MODE] control_mode={mode} health_policy=enabled")

        # 1) Ayarlar + limit
        scans_cfg = cls._load_scan_settings() or {}
        if not isinstance(scans_cfg, dict):
            scans_cfg = {}

        tf_profile = (scans_cfg.get("tf_profiles", {}) or {}).get(timeframe, {}) or {}
        strat_block = (tf_profile.get("strategy_scan", {}) or {})
        strat_config = (strat_block.get(strategy, {}) or {}) if isinstance(strat_block, dict) else {}

        cfg_limit = int(strat_config.get("limit", 0) or 0)

        # ✅ replenish "need" gönderiyorsa büyütme:
        # param limit > 0 ise onu üst sınır kabul et
        param_limit = int(limit or 0)
        if param_limit > 0 and cfg_limit > 0:
            scan_limit = min(cfg_limit, param_limit)
        elif param_limit > 0:
            scan_limit = param_limit
        elif cfg_limit > 0:
            scan_limit = cfg_limit
        else:
            scan_limit = 1

        scan_limit = max(1, int(scan_limit))

        local_th = cls.get_strat_thresholds_from_config(timeframe, strategy)
        min_vol = float(local_th.get("min_volume_usd", 2_000_000) or 0.0)

        # min_score'ı strategy'e özel anahtardan da okuyalım
        sid = "v1" if str(strategy).lower() == "v1" else "v2"
        min_score_val = float(
            local_th.get(f"min_score_{sid}", local_th.get("min_score", 60)) or 60
        )

        logging.info(f"[STRAT_SCAN_LIMIT] tf={timeframe} strat={strategy} limit={scan_limit} "
                     f"min_score={min_score_val} min_vol={min_vol:,.0f}")

        # 2) Exchange yoksa burada initialize etmeyelim (imza uyuşmazlığı riski)
        if not getattr(cls, "exchange", None):
            logging.warning("[STRAT_SCAN] exchange yok; initialize_exchange üst katmanda yapılmalı. tarama atlandı.")
            return {"sent_count":0, "found_symbols":[], "strategy":strategy}
        min_score = ConfigService.get("scans.tf_profiles.15m.strategy_scan.v1.min_score.value", None)
        logger.info(f"[THRESH_USED] tf=15m strategy_scan.v1 min_score={min_score}")

        # ✅ NEW: md'yi garanti et (strategy_scan'de md yok hatasını bitirir)
        _ensure_market_data_service(cls)
        # 3) Ticker -> futures filtre
        tickers = await cls.safe_fetch_tickers()
        futures_rows = await cls.filter_symbols_by_volume_futures_only(tickers)

        if not futures_rows:
            return {"sent_count": 0, "found_symbols": [], "strategy": strategy}

        futures_symbols = [
            r for r in futures_rows
            if float(r.get("volume_24h_usdt", 0.0) or 0.0) >= min_vol
        ][:200]

        # ✅ CandidateSelector ile preselect (strategy_scan için de)
        use_cs = bool((ConfigService.get("CANDIDATE_SELECTION", {}) or {}).get("enabled", True))
        if use_cs:
            try:
                md = getattr(cls, "md", None) or getattr(cls, "market_data_service", None)
                if md is None:
                    logging.warning("[CANDIDATE_SELECTOR][STRAT] md bulunamadı; atlandı.")
                else:
                    import inspect
                    if (ConfigService.get("CANDIDATE_SELECTION", {}) or {}).get("debug", False):
                        logging.debug(
                            "[CS_DEBUG] fn=%r module=%s qual=%s sig=%s",
                            _select_symbols_via_candidate_selector,
                            getattr(_select_symbols_via_candidate_selector, "__module__", None),
                            getattr(_select_symbols_via_candidate_selector, "__qualname__", None),
                            str(inspect.signature(_select_symbols_via_candidate_selector)),
                        )

                    ai_model = getattr(cls, "_ai_model", None)
                    if ai_model is None:
                        logging.warning("[CANDIDATE_SELECTOR][STRAT] ai_model yok; selector atlandı.")
                    else:
                        cs_symbols = await _select_symbols_via_candidate_selector(
                            md=md,
                            ai_model=ai_model,
                            strategy_id=sid,
                            to_futures_symbol=lambda s:(_resolve_futures_symbol(cls, s) or s),
                        )

                        cs_norm_set = {_norm_symbol_for_match(x) for x in (cs_symbols or []) if x}
                        before = len(futures_symbols)
                        futures_symbols = [
                            r for r in futures_symbols
                            if _norm_symbol_for_match(r.get("symbol")) in cs_norm_set
                        ]
                        logging.info(
                            f"[CANDIDATE_SELECTOR_POOL][STRAT] before={before} after={len(futures_symbols)} sid={sid}"
                        )
            except Exception as e:
                logging.error(f"[CANDIDATE_SELECTOR_FATAL][STRAT] {e}", exc_info=True)

        total_symbols_to_analyze = len(futures_symbols)
        if progress_callback:
            await progress_callback(0, total_symbols_to_analyze)

        candidates: List[Dict[str, Any]] = []
        processed_count = 0

        # 4) Kontrollü paralellik (çok yükseltme: API boğulur)
        sem = asyncio.Semaphore(6)

        async def analyze_one(sym_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            nonlocal processed_count

            symbol = sym_data.get("symbol")
            if not symbol:
                processed_count += 1
                if progress_callback:
                    await progress_callback(processed_count, total_symbols_to_analyze)
                return None

            ccxt_symbol = cls.to_ccxt_symbol(symbol, prefer_futures=True)
            if not ccxt_symbol or not cls.has_futures_market(ccxt_symbol):
                processed_count += 1
                if progress_callback:
                    await progress_callback(processed_count, total_symbols_to_analyze)
                return None

            # ✅ OHLCV (tek kapı): Optional sonucu ayrı değişkende tut
            df_opt: Optional[pd.DataFrame] = None
            async with sem:
                try:
                    df_opt = await fetch_ohlcv_with_retry(cls,
                        ccxt_symbol,
                        timeframe=timeframe,
                        max_retries=3,
                        timeout=30,
                        context=context
                    )
                except (asyncio.TimeoutError, ccxt.NetworkError, ccxt.ExchangeError, RuntimeError, ValueError,
                        TypeError) as e2:
                    logging.debug(f"[STRAT_SCAN_OHLCV_ERR] {ccxt_symbol} tf={timeframe}: {e2!r}")
                    df_opt = None

            processed_count += 1
            if progress_callback:
                await progress_callback(processed_count, total_symbols_to_analyze)

            # ✅ Guard (runtime) + type narrowing (static)
            if df_opt is None or df_opt.empty or len(df_opt) < 60:
                return None

            df = cast(pd.DataFrame, df_opt)  # <- Pylance/PyCharm burada artık df'yi kesin DataFrame görür

            required_cols = {"open", "high", "low", "close", "volume"}
            if not required_cols.issubset(df.columns):
                return None

            # timestamp standard (df zaten utc olabilir)
            if "timestamp" in df.columns:
                try:
                    # errors='coerce' hem runtime'ı sağlamlaştırır, hem type-checker'ı sakinleştirir
                    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True, errors="coerce")
                except (ValueError, TypeError, OverflowError):
                    pass

            # Indicators (tek kaynak)
            try:
                ind_raw = TechnicalIndicators.calculate_all(df, symbol)
                indicators = ind_raw if isinstance(ind_raw, dict) else TechnicalIndicators.get_default_indicators()
            except (KeyError, ValueError, TypeError) as e3:
                logging.debug(f"[STRAT_SCAN_IND_ERR] {ccxt_symbol}: {e3!r}")
                indicators = TechnicalIndicators.get_default_indicators()

            # talib tabanlı ek değerler (meta)
            try:
                close_arr = df["close"].astype(float).to_numpy()
                high_arr = df["high"].astype(float).to_numpy()
                low_arr = df["low"].astype(float).to_numpy()
                vol_arr = df["volume"].astype(float).to_numpy()
            except (KeyError, TypeError, ValueError) as e4:
                logging.debug(f"[STRAT_SCAN_ARRAY_ERR] {ccxt_symbol}: {e4!r}")
                return None

            if not HAS_TALIB:
                return None

            try:
                rsi_arr = talib.RSI(close_arr, timeperiod=14)
                macd_arr, signal_arr, _hist = talib.MACD(close_arr, fastperiod=12, slowperiod=26, signalperiod=9)
                upper_arr, _middle_arr, lower_arr = talib.BBANDS(close_arr, timeperiod=20, nbdevup=2, nbdevdn=2)
                atr_arr = talib.ATR(high_arr, low_arr, close_arr, timeperiod=14)
                ema20_arr = talib.EMA(close_arr, timeperiod=20)
                ema50_arr = talib.EMA(close_arr, timeperiod=50)
                adx_arr = talib.ADX(high_arr, low_arr, close_arr, timeperiod=14)
                stoch_k_arr, stoch_d_arr = talib.STOCHRSI(close_arr, timeperiod=14, fastk_period=3, fastd_period=3)
                obv_arr = talib.OBV(close_arr, vol_arr)
            except (ValueError, TypeError) as e5:
                logging.debug(f"[STRAT_SCAN_TALIB_ERR] {ccxt_symbol}: {e5!r}")
                return None

            def last_f(a, default: float) -> float:
                try:
                    v = float(a[-1])
                except (TypeError, ValueError, IndexError):
                    return default
                if math.isnan(v) or math.isinf(v):
                    return default
                return v

            try:
                close_last = float(df["close"].iloc[-1])
                close = close_last if pd.notna(close_last) else 0.0
            except (KeyError, IndexError, TypeError, ValueError):
                close = 0.0

            rsi = last_f(rsi_arr, 50.0)
            macd_val = last_f(macd_arr, 0.0)
            macd_sig = last_f(signal_arr, 0.0)
            atr = last_f(atr_arr, 0.0)
            ema_20 = last_f(ema20_arr, close)
            ema_50 = last_f(ema50_arr, close)
            adx = last_f(adx_arr, 0.0)
            stoch_k = last_f(stoch_k_arr, 50.0)
            stoch_d = last_f(stoch_d_arr, 50.0)
            obv_current = last_f(obv_arr, 0.0)
            bb_upper = last_f(upper_arr, close)
            bb_middle = last_f(_middle_arr, close)
            bb_lower = last_f(lower_arr, close)

            try:
                obv_prev = float(obv_arr[-2]) if len(obv_arr) > 1 and pd.notna(obv_arr[-2]) else 0.0
            except (TypeError, ValueError, IndexError):
                obv_prev = 0.0

            obv_slope = obv_current - obv_prev
            obv_status = "📈 Yükselen" if obv_current > obv_prev else "📉 Düşen" if obv_current < obv_prev else "⚪️ Nötr"

            # Volume ratio
            try:
                vol_cur = float(df["volume"].iloc[-1])
                vol_avg = float(df["volume"].iloc[-20:].mean()) if len(df) >= 20 else float(df["volume"].mean())
                volume_ratio = (vol_cur / vol_avg) if vol_avg > 0 else 0.0
            except (KeyError, TypeError, ValueError, IndexError):
                volume_ratio = 0.0

            # Momentum
            try:
                momentum_period = max(1, int(cls.strategy.get("momentum_period", 2)))
            except (TypeError, ValueError, AttributeError):
                momentum_period = 2

            if len(df) > momentum_period:
                try:
                    prev_close = float(df["close"].iloc[-(momentum_period + 1)])
                    momentum = (close - prev_close) / prev_close if prev_close > 0 else 0.0
                except (KeyError, TypeError, ValueError, IndexError):
                    momentum = 0.0
            else:
                momentum = 0.0

            bb_width = (bb_upper - bb_lower) / bb_middle if bb_middle > 0 else 0.0
            compression = max(0.0, 1.0 - bb_width)
            mt_align = close > ema_20 > ema_50
            structure_ok = bb_lower <= close <= bb_upper
            # %B (0..1 clamp)
            bb_percent_b = float("nan")
            denom = (bb_upper - bb_lower)
            if denom and math.isfinite(denom) and abs(denom) > 1e-12:
                bb_percent_b = (float(close) - float(bb_lower)) / float(denom)
                bb_percent_b = max(0.0, min(1.0, float(bb_percent_b)))

            try:
                ema_200_arr = talib.EMA(close_arr, timeperiod=200)
                ema_200 = float(ema_200_arr[-1]) if ema_200_arr is not None and len(ema_200_arr) else 0.0
                local_regime = "📈 Yükseliş" if close > ema_200 else "📉 Düşüş"
            except (TypeError, ValueError, IndexError):
                local_regime = "Bilinmiyor"

            # Teknik skorlama
            if sid == "v1":
                score = 0.0
                if 30 <= rsi <= 70:
                    score += 20
                if macd_val > macd_sig:
                    score += 20
                if momentum > 0:
                    score += 20
                if volume_ratio > 1.0:
                    score += 20
                if mt_align:
                    score += 20
                technical_score = score
            else:
                score = 0.0
                if compression > 0.5:
                    score += 25
                if 40 <= rsi <= 60:
                    score += 20
                if 0.8 <= volume_ratio <= 1.5:
                    score += 20
                if structure_ok:
                    score += 20
                if close > ema_20:
                    score += 15
                technical_score = score

            # Health penalty/reward (normalize + log)
            sym_for_health = str(ccxt_symbol or symbol or "")
            base_sym = sym_for_health.split(":", 1)[0]  # "BTC/USDT:USDT" -> "BTC/USDT"

            symbol_health = (
                symbol_tracker.get_symbol_health(sym_for_health)
                or symbol_tracker.get_symbol_health(base_sym)
                or {}
            )

            try:
                factor = float(symbol_health.get("penalty_factor", 1.0) or 1.0)
            except (TypeError, ValueError, AttributeError):
                factor = 1.0

            # ✅ NEW: action/cooldown/priority_bonus
            status = str(symbol_health.get("status", "neutral"))
            reason = str(symbol_health.get("reason", ""))
            action = str(symbol_health.get("action", "allow"))
            cooldown_until = str(symbol_health.get("cooldown_until", ""))
            try:
                priority_bonus = float(symbol_health.get("priority_bonus", 0.0) or 0.0)
            except (TypeError, ValueError):
                priority_bonus = 0.0

            logging.info(
                f"[RP][STRAT] mode={mode} sym={sym_for_health} base={base_sym} "
                f"factor={factor:.2f} bonus={priority_bonus:.2f} "
                f"status={status} action={action} until={cooldown_until} reason={reason}"
            )

            # ✅ Hard skip (kara liste)
            if action == "blacklist_skip":
                logging.info(
                    f"[RP_SKIP] {sym_for_health} skipped due to blacklist. reason={reason} until={cooldown_until}"
                )
                return None

            # factor uygula
            if factor != 1.0:
                technical_score *= factor

            # ✅ priority bonus uygula (gerçek öncelik/sıralama etkisi)
            if priority_bonus:
                technical_score += priority_bonus

            if technical_score < min_score_val:
                return None

            try:
                volume_usd = float(sym_data.get("volume_24h_usdt", 0.0) or 0.0)
            except (TypeError, ValueError):
                volume_usd = 0.0

            if volume_usd < min_vol:
                return None

            vol_proxy = (atr / close) if close > 0 else 0.0
            strat_potential = max(0.0, min(1.0, (momentum if momentum > 0 else 0.0) + min(vol_proxy, 0.02)))
            momentum_tension = abs(momentum) * 100.0

            # ------------------------------------------------------------
            # NEW: Direction-aware SL/TP (ATR-based) + config-driven
            # ------------------------------------------------------------
            direction = "LONG" if momentum >= 0 else "SHORT"
            # --- BB EDGE FILTER (Hard-skip + override) ---
            bb_cfg = _get_bb_edge_filter_cfg(timeframe)

            # Strategy scan override için trend hint: mt_align + adx + volume_ratio
            # local_trend string yerine burada mt_align'i "trend hint" gibi kullanacağız.
            lt_hint = "📈 Yükseliş" if (direction == "LONG" and mt_align) else ("📉 Düşüş" if (direction == "SHORT" and (close < ema_20 < ema_50)) else "")

            allowed, bb_reason = _bb_edge_decision(
                symbol=str(ccxt_symbol or symbol or ""),
                timeframe=str(timeframe),
                direction=str(direction),
                bb_percent_b=_to_float_opt(bb_percent_b) if math.isfinite(bb_percent_b) else None,
                bb_width=_to_float_opt(bb_width),
                adx=_to_float_opt(adx),
                vr=_to_float_opt(volume_ratio),
                local_trend=str(lt_hint),
                cfg=bb_cfg,
            )

            if not allowed:
                return None


            # Config: risk ve TP çarpanları
            try:
                risk_mult = float(ConfigService.get("strategy_scan.risk_mult", 2.0) or 2.0)
            except Exception:
                risk_mult = 2.0

            tp_mults_cfg = ConfigService.get("strategy_scan.tp_mults", [1.0, 1.5, 2.0, 3.0, 4.0])
            if not isinstance(tp_mults_cfg, list) or not tp_mults_cfg:
                tp_mults_cfg = [1.0, 1.5, 2.0, 3.0, 4.0]

            tp_mults: list[float] = []
            for m in tp_mults_cfg[:5]:
                try:
                    mf = float(m)
                    if mf > 0 and math.isfinite(mf):
                        tp_mults.append(mf)
                except Exception:
                    continue
            if not tp_mults:
                tp_mults = [1.0, 1.5, 2.0, 3.0, 4.0]

            # ATR fallback: ATR yoksa %1
            atr_val = float(atr or 0.0)
            if not math.isfinite(atr_val) or atr_val <= 0.0:
                atr_val = float(close) * 0.01  # %1 kaba fallback

            risk = atr_val * float(risk_mult)

            if direction == "LONG":
                stop_loss_val = float(close) - float(risk)
                targets_val = [float(close) + (atr_val * float(m)) for m in tp_mults]
            else:
                stop_loss_val = float(close) + float(risk)
                targets_val = [float(close) - (atr_val * float(m)) for m in tp_mults]

            # Güvenlik: fiyatlar pozitif olmalı
            # (özellikle düşük fiyatlı coinlerde negatif target çıkmasın)
            targets_val = [t for t in targets_val if isinstance(t, (int, float)) and math.isfinite(t) and t > 0]
            if not targets_val:
                # son çare: close bazlı yüzde hedefler
                if direction == "LONG":
                    targets_val = [float(close) * (1.01 + 0.01 * i) for i in range(5)]
                else:
                    targets_val = [float(close) * (0.99 - 0.01 * i) for i in range(5)]

            # ------------------------------------------------------------
            # result_row (FULL)
            # ------------------------------------------------------------
            result_row: Dict[str, Any] = {
                "symbol":symbol,
                "ccxt_symbol":ccxt_symbol,
                "score":float(technical_score),
                "technical_score":float(technical_score),
                "volume_usd":float(volume_usd),
                "volume_ratio":float(volume_ratio),
                "momentum":float(momentum),
                "compression":float(compression),
                "delay_quality":0.0,
                "mt_align":bool(mt_align),
                "structure_ok":bool(structure_ok),

                "rsi":float(rsi),
                "macd":float(macd_val),
                "close":float(close),
                "atr":float(atr),
                "timeframe":str(timeframe),
                "strategy_id":str(sid),

                "direction":direction,
                "entry_price":float(close),
                "stop_loss":float(stop_loss_val),
                "targets":[float(x) for x in targets_val[:5]],

                "strat_potential":float(strat_potential),
                "indicators":indicators,

                "adx":float(adx),
                "stoch_k":float(stoch_k),
                "stoch_d":float(stoch_d),
                "bb_width":float(bb_width),
                "momentum_tension":float(momentum_tension),
                "obv_slope":float(obv_slope),
                "obv_status":str(obv_status),
                "local_regime":str(local_regime),
                "bb_percent_b":(None if not math.isfinite(bb_percent_b) else float(bb_percent_b)),

                # debug / açıklama
                "calc_method":{
                    "sl":"ATR" if (atr is not None and float(atr or 0.0) > 0) else "PCT_FALLBACK",
                    "tp":"ATR_MULTS" if (atr is not None and float(atr or 0.0) > 0) else "PCT_FALLBACK",
                    "risk_mult":float(risk_mult),
                    "tp_mults":[float(x) for x in tp_mults[:5]],
                }
            }

            # score mirror (mevcut davranışı koru)
            if sid == "v1":
                result_row["v1_score"] = float(technical_score)
            else:
                result_row["v2_score"] = float(technical_score)

            cls.debug_log_scan_evaluation(
                symbol, timeframe, sid, local_th,
                {
                    "potential_pct": 0.0,
                    "ai_confidence": 0.0,
                    "technical_score": technical_score,
                    "volume_usd": volume_usd,
                },
                tag_prefix="STRAT_EVAL",
            )

            return result_row

        # 5) Analizleri çalıştır
        try:
            results = await asyncio.gather(*(analyze_one(r) for r in futures_symbols), return_exceptions=False)
        finally:
            if progress_callback:
                await progress_callback(total_symbols_to_analyze, total_symbols_to_analyze)

        for r in results:
            if isinstance(r, dict):
                candidates.append(r)

        # 6) Limit uygula
        if candidates:
            candidates.sort(key=lambda x: float(x.get("score", 0.0) or 0.0), reverse=True)
            candidates = candidates[: max(scan_limit * 3, scan_limit)]

        # 7) Alarm ekle
        sent_count = 0

        for candidate in candidates:
            if sent_count >= scan_limit:
                break

            try:
                sym = str(candidate.get("symbol") or "").strip()
                if not sym:
                    continue

                sym_ccxt = _resolve_to_futures_symbol(cls, sym)
                if not sym_ccxt:
                    continue

                ind = candidate.get("indicators") if isinstance(candidate.get("indicators"), dict) else {}
                if not isinstance(ind, dict):
                    ind = {}

                candidate_meta = {
                    "source":"strategy_scan",
                    "strategy_scan":True,
                    "indicators":ind,
                    "technical_score":candidate.get("technical_score", candidate.get("score", 0.0)),
                    "volume_usd":candidate.get("volume_usd", 0.0),
                    "volume_ratio":candidate.get("volume_ratio", ind.get("volume_ratio", 1.0)),
                    "momentum":candidate.get("momentum", 0.0),
                    "compression":candidate.get("compression", 0.0),
                    "macd":candidate.get("macd", 0.0),
                    "rsi":candidate.get("rsi", ind.get("rsi", 50.0)),
                    "adx":candidate.get("adx", ind.get("adx", 0.0)),
                    "stoch_k":candidate.get("stoch_k", ind.get("stoch_k", 50.0)),
                    "stoch_d":candidate.get("stoch_d", ind.get("stoch_d", 50.0)),
                    "bb_width":candidate.get("bb_width", ind.get("bb_width", 0.0)),
                    "bb_percent_b":candidate.get("bb_percent_b", ind.get("bb_percent_b", None)),

                    "momentum_tension":candidate.get("momentum_tension", ind.get("momentum_tension", 0.0)),
                    "obv_slope":candidate.get("obv_slope", ind.get("obv_slope", 0.0)),
                    "obv_status":candidate.get("obv_status", ind.get("obv_status", "⚪️ Nötr")),
                    "local_trend":(
                            ind.get("local_trend")
                            or candidate.get("local_trend")
                            or candidate.get("local_regime")
                            or "❓ Bilinmiyor"
                    ),
                    "local_regime":(
                            candidate.get("local_regime")
                            or ind.get("local_trend")
                            or candidate.get("local_trend")
                            or "❓ Bilinmiyor"
                    ),
                }

                ok = await cls.add_alarm_debug(
                    context=context,
                    symbol=sym_ccxt,
                    user_id=user_id,
                    timeframe=timeframe,
                    strategy_id=sid,
                    source_meta=candidate_meta
                )
                if ok:
                    sent_count += 1

            except Exception as e3:
                logging.error(f"[STRAT_SCAN_ADD_ALARM_ERR] {candidate.get('symbol', '?')}: {e3}")

        logging.info(f"[STRAT_SCAN][DONE] tf={timeframe} strat={sid} sent={sent_count}")
        return {"sent_count": sent_count, "found_symbols": candidates, "strategy": sid}

    except Exception as e3:
        logging.error(f"[STRAT_SCAN_ERR] {strategy}: {e3}", exc_info=True)
        return {"sent_count": 0, "found_symbols": [], "strategy": strategy}

def format_usd_short(value: float) -> str:
    # Örn.: 1234567.89 -> "1.23M $"
    # Burada yalnızca imza önemli; gerçek içerik sizde olabilir.
    abs_v = abs(value)
    if abs_v >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B $"
    if abs_v >= 1_000_000:
        return f"{value / 1_000_000:.2f}M $"
    if abs_v >= 1_000:
        return f"{value / 1_000:.2f}K $"
    return f"{value:.2f} $"


# filter_symbols_by_volume_futures_only
async def filter_symbols_by_volume_futures_only(cls, tickers: Dict[str, Dict[str, Any]]):
    """
    DÜZELTİLDİ: Boş ticker kontrolü + detaylı log
    """
    try:
        if not tickers or not isinstance(tickers, dict):
            logging.error("[FILTER_FUT] Gelen ticker verisi boş veya geçersiz, filtreleme yapılamıyor.")
            return []

        s = cls._load_scan_settings()
        scan_cfg = (s.get("scan", {}) or {})
        scans_cfg = (s.get("scans", {}) or {})
        min_vol = float(
            scan_cfg.get("futures_min_volume_usdt")
            or scans_cfg.get("futures_min_volume_usdt")
            or 2_000_000
        )
        logging.info(f"[FILTER_FUT] Min volume: ${min_vol:,.0f}")

        futures_symbols = await cls.get_futures_symbols_only()
        if not futures_symbols:
            logging.warning("[FILTER_FUT] get_futures_symbols_only boş döndü")
            return []

        logging.info(f"[FILTER_FUT] Kontrol edilecek: {len(futures_symbols)} futures sembol")

        filtered = []

        for symbol_data in futures_symbols:
            symbol = "<unknown>"  # predefine to avoid 'referenced before assignment'
            try:
                symbol = symbol_data if isinstance(symbol_data, str) else symbol_data.get('symbol')
                if not isinstance(symbol, str) or not symbol:
                    continue

                # Ticker bilgisi al
                t = tickers.get(symbol)

                if not isinstance(t, dict) or not t:
                    # Spot karşılığını dene
                    base_spot = symbol.split(':')[0] if ':' in symbol else symbol
                    t = tickers.get(base_spot, {})

                # Hacim kontrolü
                vol = float(cls._safe_ticker_volume_usd(t))

                if vol >= min_vol:
                    filtered.append({'symbol':symbol, 'volume_24h_usdt':vol, 'market_type':'futures'})

            except Exception as e:
                logging.debug(f"[FILTER_FUT] {symbol} hata: {e}")
                continue

        logging.info(f"✅ Hacim filtresinden geçen vadeli işlem coini: {len(filtered)}")

        for i, item in enumerate(filtered[:5], 1):
            logging.debug(f"  {i}. {item['symbol']} "
                          f"vol={format_usd_short(item['volume_24h_usdt'])}")

        return filtered

    except Exception as e:
        logging.error(f"[FILTER_FUT_ERR] {e}")
        return []


# analyze_single_coin_with_real_ai_safe
async def _analyze_single_coin_with_real_ai_safe(
    cls,
    symbol: str,
    timeframe: str = "1h",
    context: Optional[CallbackContext] = None
) -> Dict[str, Any]:
    """
    Tek coin AI-safe analiz:
    - Sembol normalize + futures guard
    - OHLCV: cls.fetch_ohlcv_with_retry üzerinden (semafor/retry/restart kontrolü)
    - Feature'lar: rsi/macd/cci tek seferde
    - Volume: _last_tickers cache, yetmezse fetch_ticker retry
    """
    try:
        # 0) symbol normalize (dict gelebiliyor)
        if isinstance(symbol, dict):
            symbol = symbol.get("symbol") or symbol.get("sym") or symbol.get("s")  # type: ignore
        if not isinstance(symbol, str) or not symbol.strip():
            logger.info("[AI_SCAN][SKIP] Geçersiz sembol: %r", symbol)
            return {}

        raw_symbol = symbol.strip()

        # 1) Exchange var mı?
        if getattr(cls, "exchange", None) is None:
            # Burada doğrudan raise yerine güvenli dönüş tercih ediyorum.
            # Çünkü scan akışında tek coin yüzünden tüm batch düşmesin.
            logger.warning("[AI_SCAN][SKIP] Exchange bağlantısı yok")
            return {}

        # 2) CCXT sembolüne çevir + futures kontrol
        ccxt_symbol = cls.to_ccxt_symbol(raw_symbol, prefer_futures=True)
        if not ccxt_symbol:
            logger.info("[AI_SCAN][SKIP] CCXT sembol üretilemedi: %s", raw_symbol)
            return {}

        if not cls.has_futures_market(ccxt_symbol):
            logger.info("[AI_SCAN][SKIP] Futures market yok: %s -> %s", raw_symbol, ccxt_symbol)
            return {}

        # 3) OHLCV (yeni yapıda: retry+semafor+session closed handling burada)
        # fetch_ohlcv_with_retry DataFrame döndürüyor (senin yeni sürümün gibi)
        df = await fetch_ohlcv_with_retry(cls,
            ccxt_symbol,
            timeframe=timeframe,
            max_retries=3,
            timeout=30,
            context=context
        )

        if df is None or df.empty or len(df) < 60:
            logger.info("[AI_SCAN][SKIP] %s tf=%s yetersiz DF: len=%s",
                        ccxt_symbol, timeframe, (len(df) if df is not None else 0))
            return {}

        # Kolon güvenliği
        required_cols = {"open", "high", "low", "close", "volume"}
        if not required_cols.issubset(set(df.columns)):
            logger.info("[AI_SCAN][SKIP] %s kolonlar eksik: %s", ccxt_symbol, df.columns.tolist())
            return {}

        # 4) Feature'lar (tek seferde)
        close_arr = df["close"].astype(float).to_numpy()
        high_arr  = df["high"].astype(float).to_numpy()
        low_arr   = df["low"].astype(float).to_numpy()
        if not HAS_TALIB:
            return {}

        # talib çıktıları ndarray -> son değerleri güvenle al
        try:
            rsi_arr = talib.RSI(close_arr, timeperiod=14)
            macd_arr, signal_arr, _hist = talib.MACD(close_arr, fastperiod=12, slowperiod=26, signalperiod=9)
            cci_arr = talib.CCI(high_arr, low_arr, close_arr, timeperiod=14)

            rsi_last = float(rsi_arr[-1]) if rsi_arr is not None and len(rsi_arr) else 50.0
            # macd/signal son değer NaN olabilir → fallback
            macd_last = float(macd_arr[-1]) if macd_arr is not None and len(macd_arr) else 0.0
            sig_last  = float(signal_arr[-1]) if signal_arr is not None and len(signal_arr) else 0.0
            cci_last  = float(cci_arr[-1]) if cci_arr is not None and len(cci_arr) else 0.0

            # DataFrame'e feature adıyla ekle (model predict tarafında gerekebilir)
            # (İstersen sadece son satıra eklemek de olur; ama burada full seri ekliyoruz)
            df["rsi"] = rsi_arr
            df["macd"] = macd_arr
            df["signal"] = signal_arr
            df["cci"] = cci_arr

        except Exception as ta_err:
            logging.error("[%s] AI analizi için teknik gösterge hatası: %r", ccxt_symbol, ta_err)
            return {}

        # 5) ATR (manual TR üzerinden) — senin yaklaşımın korunuyor
        try:
            atr_period = int((cls.strategy.get("targets", {}) or {}).get("atr_lookback", 14))
        except Exception:
            atr_period = 14

        # true range serisi
        tr1 = high_arr[1:] - low_arr[1:]
        tr2 = np.abs(high_arr[1:] - close_arr[:-1])
        tr3 = np.abs(low_arr[1:] - close_arr[:-1])
        tr = np.maximum(tr1, np.maximum(tr2, tr3))
        atr = float(pd.Series(tr).rolling(window=max(1, atr_period), min_periods=1).mean().iloc[-1])

        # 6) movement_potential (lookback return)
        try:
            lookback_p = int(max(2, cls.strategy.get("momentum_period", 2)))
        except Exception:
            lookback_p = 2

        if len(close_arr) > lookback_p:
            base = float(close_arr[-lookback_p - 1])
            last = float(close_arr[-1])
            movement_potential = float((last - base) / base) if base != 0 else 0.0
        else:
            movement_potential = 0.0

        # 7) Teknik skor + confidence (senin eski mantık, ama RSI tekrar hesaplanmıyor)
        trend_ma = float(pd.Series(close_arr).rolling(window=20, min_periods=1).mean().iloc[-1])
        trend = 1.0 if float(close_arr[-1]) >= trend_ma else 0.0

        technical_score = float(max(0.0, min(100.0, (rsi_last / 100.0) * 60.0 + trend * 40.0)))
        ai_confidence = float(max(0.0, min(1.0, technical_score / 100.0)))

        # 8) Volume USD (cache -> fetch_ticker retry)
        volume_usd = 0.0
        try:
            last_tickers = getattr(cls, "_last_tickers", {}) or {}

            # ccxt_symbol bazen "BTC/USDT:USDT", bazen "BTC/USDT" → iki anahtarı da dene
            t = (last_tickers.get(ccxt_symbol) if isinstance(last_tickers, dict) else None) or {}
            if not t and ":" in ccxt_symbol:
                base_spot = ccxt_symbol.split(":", 1)[0]
                t = (last_tickers.get(base_spot) if isinstance(last_tickers, dict) else None) or {}

            volume_usd = float(cls._safe_ticker_volume_usd(t) or 0.0)

            if volume_usd <= 0.0:
                ex = getattr(cls, "exchange", None)
                if ex is not None:
                    t2 = await cls._retry_async(
                        ex.fetch_ticker,
                        ccxt_symbol,
                        retries=2,
                        base_delay=0.6,
                        max_delay=getattr(cls, "MAX_BACKOFF_SEC", 8)
                    )
                    volume_usd = float(cls._safe_ticker_volume_usd(t2) or 0.0)
        except Exception as vol_err:
            logger.warning("[AI_SCAN][VOL_ERR] %s %r", ccxt_symbol, vol_err)
            volume_usd = 0.0

        direction = "LONG" if movement_potential >= 0 else "SHORT"

        logging.info(
            f"[AI_SCAN][CALC] {ccxt_symbol} tf={timeframe} ohlcv_len={len(df)} "
            f"atr={atr:.6f} pot={movement_potential:.3f} conf={ai_confidence:.3f} "
            f"tech={technical_score:.2f} vol_usd={volume_usd:.0f} "
            f"rsi={rsi_last:.1f} macd={macd_last:.5f}/{sig_last:.5f} cci={cci_last:.1f}"
        )

        return {
            "symbol": ccxt_symbol,                 # ✅ CCXT sembol döndür (tüm sistem aynı formatta)
            "timeframe": timeframe,
            "potential_pct": movement_potential,   # (-1..+1) oran
            "ai_confidence": ai_confidence,
            "technical_score": technical_score,
            "volume_usd": volume_usd,
            "direction": direction,
            "signal": "BUY" if movement_potential >= 0 else "SELL",
            "market_type": "swap",

            # İstersen faydalı debug/feature alanları:
            "rsi": rsi_last,
            "macd": macd_last,
            "macd_signal": sig_last,
            "cci": cci_last,
            "atr": atr,
        }

    except Exception as e:
        logger.exception("AI analiz hata %r tf=%s: %s", symbol, timeframe, e)
        return {}


# fetch_ohlcv_with_retry
async def fetch_ohlcv_with_retry(
    cls,
    symbol: str,
    timeframe: str = "15m",
    max_retries: int = 3,
    timeout: int = 30,
    context: Optional[CallbackContext] = None
) -> Optional[pd.DataFrame]:

    # ✅ Eğer zaten CCXT formatıysa tekrar çevirmeyelim
    raw = (symbol or "").strip()
    if "/" in raw:
        ccxt_symbol = raw
    else:
        ccxt_symbol = cls.to_ccxt_symbol(raw, prefer_futures=True)

    if not ccxt_symbol:
        logging.error(f"Geçersiz sembol: {symbol}")
        return None

    if not cls.has_futures_market(ccxt_symbol):
        logging.warning(f"No futures market for {ccxt_symbol}")
        return None

    for attempt in range(1, max_retries + 1):
        ex = getattr(cls, "exchange", None)
        if ex is None:
            await cls.soft_restart_exchange(context=context, reason="exchange_none")
            ex = getattr(cls, "exchange", None)
            if ex is None:
                return None

        try:
            async with cls._ohlcv_sem:
                ohlcv: Any = await asyncio.wait_for(
                    ex.fetch_ohlcv(ccxt_symbol, timeframe, limit=500),
                    timeout=timeout
                )

            if not ohlcv or not isinstance(ohlcv, list) or len(ohlcv) < 20:
                return None

            df: pd.DataFrame = pd.DataFrame(
                ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
            ).dropna()

            if df.empty or len(df) < 20:
                return None

            # ✅ varsa standardize et
            std_fn = getattr(cls, "_standardize_ohlcv_df", None)
            if callable(std_fn):
                df2 = std_fn(df)

                # Type-checker + runtime güvenliği: DataFrame değilse iptal
                if df2 is None:
                    return None
                if not isinstance(df2, pd.DataFrame):
                    logging.warning(
                        f"[FETCH_OHLCV] _standardize_ohlcv_df DataFrame döndürmedi: {type(df2).__name__}"
                    )
                    return None

                df = df2

                if df.empty:
                    return None

            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            return df

        except RuntimeError as e:
            if "Session is closed" in str(e):
                logging.warning(f"[FETCH_OHLCV] session closed {symbol} attempt={attempt}")
                await cls.soft_restart_exchange(context=context, reason="session_closed")
                await asyncio.sleep(0.8)
                continue
            raise

        except (ccxt.NetworkError, ccxt.ExchangeError, asyncio.TimeoutError) as e:
            logging.warning(f"[FETCH_OHLCV_ERR] {symbol} ({attempt}/{max_retries}): {repr(e)}")
            await asyncio.sleep(0.6 * attempt)
            continue

        except Exception as e:
            logging.error(f"[FETCH_OHLCV_FATAL] {symbol}: {repr(e)}", exc_info=True)
            await asyncio.sleep(0.6 * attempt)

    return None



# debug_log_scan_evaluation
def debug_log_scan_evaluation(
        cls,
        symbol: str,
        timeframe: str,
        strategy_id: str,
        thresholds: Dict[str, Any],
        row_meta: Dict[str, Any],
        tag_prefix: str = "EVAL",
) -> None:
    try:
        sid = (strategy_id or "").lower()
        sym = str(symbol or "-")
        tf = str(timeframe or "-")
        tag = str(tag_prefix or "EVAL")

        has_ai_keys = all(
            k in (thresholds or {})
                for k in ("min_conf", "min_potential_pct", "min_volume_usd", "min_volume_ratio")
        )
        has_strat_keys = any(
            k in (thresholds or {}) for k in ("min_score_v1", "min_score_v2", "min_score")
        )
        mode = "ai_scan" if has_ai_keys else ("strategy_scan" if has_strat_keys else "unknown")

        # conf: float garantile
        try:
            conf = float(row_meta.get("ai_confidence", row_meta.get("confidence", 0.0)) or 0.0)
            if math.isnan(conf) or math.isinf(conf):
                conf = 0.0
        except (TypeError, ValueError):
            conf = 0.0

        # potential normalize
        pot_disp: str
        pot_norm: float
        try:
            pot_raw = row_meta.get("potential_pct", row_meta.get("movement_potential"))
            if pot_raw is None:
                pot_raw = row_meta.get("strat_potential_pct", row_meta.get("strat_potential", 0.0))
            pv = float(pot_raw or 0.0)
            if math.isnan(pv) or math.isinf(pv):
                pv = 0.0
            if 1.0 < abs(pv) <= 300.0:
                pot_norm = pv / 100.0
                pot_disp = f"{pv:.2f}%"
            else:
                pot_norm = pv
                pot_disp = f"{pv * 100:.2f}%"
        except (TypeError, ValueError):
            pot_norm = 0.0
            pot_disp = "0.00%"

        # vol_usd: float garantile
        try:
            vol_usd = float(row_meta.get("volume_usd", 0.0) or 0.0)
            if math.isnan(vol_usd) or math.isinf(vol_usd):
                vol_usd = 0.0
        except (TypeError, ValueError):
            vol_usd = 0.0

        # vr: float garantile
        try:
            vr = float(row_meta.get("volume_ratio", 1.0) or 1.0)
            if math.isnan(vr) or math.isinf(vr):
                vr = 1.0
        except (TypeError, ValueError):
            vr = 1.0

        direction = (row_meta.get("direction") or row_meta.get("type") or "-")

        # Strategy fit skorları
        v1_score = row_meta.get("v1_score")
        v2_score = row_meta.get("v2_score")

        if mode == "ai_scan":
            th_conf = float(thresholds.get("min_conf", 0.0) or 0.0)
            th_pot = float(thresholds.get("min_potential_pct", 0.0) or 0.0)
            th_vol = float(thresholds.get("min_volume_usd", 0.0) or 0.0)
            th_vr = float(thresholds.get("min_volume_ratio", 1.0) or 1.0)
            th_text = f"TH[min_conf={th_conf:.2f}, min_pot={th_pot:.2f}%, min_vol=${th_vol:,.0f}, min_vr={th_vr:.2f}]"

            ok_conf = conf >= th_conf
            ok_pot = abs(pot_norm) >= (th_pot / 100.0 if th_pot > 1.0 else th_pot)
            ok_vol = vol_usd >= th_vol
            ok_vr = vr >= th_vr
            checks = f"CHK[conf={'OK' if ok_conf else 'NG'}, pot={'OK' if ok_pot else 'NG'}, vol={'OK' if ok_vol else 'NG'}, vr={'OK' if ok_vr else 'NG'}]"

        elif mode == "strategy_scan":
            th_vol = float(thresholds.get("min_volume_usd", 0.0) or 0.0)
            th_score = float(
                thresholds.get(
                    f"min_score_{sid}",
                    thresholds.get(
                        "min_score",
                        thresholds.get("min_score_v1" if sid == "v1" else "min_score_v2", 0.0),
                    ),
                )
                or 0.0
            )

            current_score: Optional[float]
            try:
                raw_score = v1_score if sid == "v1" else v2_score
                current_score = float(raw_score) if raw_score is not None else None
                if current_score is not None and (math.isnan(current_score) or math.isinf(current_score)):
                    current_score = None
            except (TypeError, ValueError):
                current_score = None

            th_text = f"TH[min_score_{sid}={th_score:.1f}, min_vol=${th_vol:,.0f}]"
            ok_score = (current_score is not None) and (current_score >= th_score)
            ok_vol = vol_usd >= th_vol
            checks = f"CHK[score={'OK' if ok_score else 'NG'}, vol={'OK' if ok_vol else 'NG'}]"

        else:
            th_text = "TH[unknown]"
            checks = "CHK[unknown]"

        # Teknik özet
        tech_parts = []
        if isinstance(row_meta.get("technical_score"), (int, float)):
            try:
                tval = float(row_meta["technical_score"])
                if not (math.isnan(tval) or math.isinf(tval)):
                    tech_parts.append(f"tech={tval:.2f}")
            except (TypeError, ValueError):
                pass

        if "rsi" in row_meta:
            try:
                rsi_val = float(row_meta["rsi"])
                if not (math.isnan(rsi_val) or math.isinf(rsi_val)):
                    tech_parts.append(f"rsi={rsi_val:.1f}")
            except (TypeError, ValueError):
                pass

        if "macd_relation" in row_meta:
            tech_parts.append(f"macd={row_meta['macd_relation']}")
        if "smma_trend" in row_meta:
            tech_parts.append(f"smma={row_meta['smma_trend']}")
        if "compression_active" in row_meta:
            tech_parts.append(f"comp={'on' if row_meta['compression_active'] else 'off'}")

        tech_txt = f" | {' '.join(tech_parts)}" if tech_parts else ""

        # Renkler
        dir_col = (direction or "-").upper()
        if dir_col == "LONG":
            dir_col = cls._c("LONG", "green")
        elif dir_col == "SHORT":
            dir_col = cls._c("SHORT", "yellow")

        def colorize_okng(txt: str) -> str:
            try:
                inner = txt.strip()
                if not inner.startswith("CHK[") or not inner.endswith("]"):
                    return txt
                inner_core = inner[4:-1]
                parts = [p.strip() for p in inner_core.split(",")]
                colored = []
                for p in parts:
                    if "=" in p:
                        k, v = p.split("=", 1)
                        v_up = v.strip().upper()
                        if v_up in ("OK", "TRUE", "PASS"):
                            colored.append(f"{k}={cls._c('OK', 'green')}")
                        elif v_up in ("NG", "FAIL", "FALSE"):
                            colored.append(f"{k}={cls._c(v_up, 'blue')}")
                        else:
                            colored.append(f"{k}={v}")
                    else:
                        colored.append(p)
                return "CHK[" + ", ".join(colored) + "]"
            except Exception as e1:
                logging.error(f"colorize_okng error: {e1}")
            return txt

        checks_colored = colorize_okng(checks)

        logging.info(
            f"[{tag}] {sym} {tf} {sid.upper()} "
            f"dir={dir_col} "
            f"conf={conf:.3f} "
            f"pot={pot_disp} "
            f"vol=${vol_usd:,.0f} "
            f"vr={vr:.2f} "
            f"{th_text} {checks_colored}{tech_txt}"
        )
        return

    except Exception as e:
        logging.debug(f"[{tag_prefix}] log_eval err: {e}")


# ai_coin_scanner_only
async def ai_coin_scanner_only(cls, context):
    """
    Sadece AI coin taraması - Eğitim yapmaz
    (Yeni yapı uyumlu: exchange init burada yapılmaz; gerekiyorsa soft_restart_exchange denenir.)
    """
    try:
        logging.info("🔍 RealAI sadece tarama başlıyor...")

        # Model kontrolü
        if not getattr(cls, "_ai_model", None) or not getattr(cls._ai_model, "is_trained", False):
            logging.error("❌ AI model eğitilmemiş - Tarama yapılamaz!")
            return []

        # Scaler kontrolü ve yeniden yükleme (güvenlik)
        try:
            if not hasattr(cls._ai_model, "scaler") or cls._ai_model.scaler is None:
                scaler_path = os.path.join(cls._ai_model.models_dir, "scaler.pkl")
                if os.path.exists(scaler_path):
                    try:
                        cls._ai_model.scaler = joblib.load(scaler_path)
                    except Exception as e:
                        logging.error(f"[SCALER_LOAD_ERR] joblib: {e}")
                        with open(scaler_path, "rb") as f:
                            cls._ai_model.scaler = pickle.load(f)
                    logging.info("✅ Scaler başarıyla yeniden yüklendi")
                else:
                    logging.error("❌ Scaler dosyası bulunamadı")
                    return []
        except Exception as scaler_load_error:
            logging.error(f"❌ Scaler yükleme hatası: {scaler_load_error}")
            return []

        # Aktif model sayısı kontrol
        try:
            active_models = [m for m in (cls._ai_model.models or {}).values() if m is not None]
        except Exception:
            active_models = []

        if not active_models:
            logging.error("❌ Aktif model yok - Tarama yapılamaz!")
            return []

        logging.info(f"✅ {len(active_models)} aktif model ile tarama başlıyor")

        # Exchange: burada initialize_exchange çağırmıyoruz; gerekiyorsa soft restart deniyoruz
        if not getattr(cls, "exchange", None):
            await cls.soft_restart_exchange(context=context, reason="ai_coin_scanner_only_exchange_missing")
            if not getattr(cls, "exchange", None):
                logging.warning("[AI_ONLY_SCAN] exchange hala yok, tarama iptal.")
                return []

        # Ticker alma (güvenli)
        try:
            tickers = await cls.safe_fetch_tickers()
            if not isinstance(tickers, dict) or not tickers:
                logging.error("❌ Ticker verisi alınamadı")
                return []
        except Exception as ticker_error:
            logging.error(f"❌ Ticker alma hatası: {ticker_error}")
            return []

        # Futures-only filtre
        try:
            filtered_symbols = await cls.filter_symbols_by_volume_futures_only(tickers)
            logging.info(f"✅ {len(filtered_symbols)} vadeli işlem coini filtrelendi")
        except Exception as filter_error:
            logging.error(f"❌ Vadeli filtreleme hatası: {filter_error}")
            return []

        if not filtered_symbols:
            logging.warning("⚠️ Vadeli işlem coini bulunamadı!")
            return []

        # Semafor ile kısıtlı paralellik
        sem = asyncio.Semaphore(6)

        async def analyze_symbol_with_semaphore(coin_row: dict):
            async with sem:
                sym = ""
                try:
                    sym = cls.normalize_symbol((coin_row or {}).get("symbol", ""))
                except Exception as nerr:
                    logging.error(f"[NORM_ERR] {((coin_row or {}).get('symbol'))} {nerr}")

                if not sym:
                    return None
                if not HAS_TALIB:
                    return None

                try:
                    # analyze fonksiyonun CCXT formatı döndürüyor (BTC/USDT:USDT)
                    return await cls._analyze_single_coin_with_real_ai_safe(sym, context=context)
                except Exception as err:
                    logging.error(f"[AI_ONLY_SCAN_SYMBOL_ERR] {sym}: {err}")
                    return None

        # Batch ayarı
        total = len(filtered_symbols)
        batch_size = min(20, max(6, int(total * 0.12)))  # dinamik, 6-20 arası

        high_potential_coins = []

        for i in range(0, total, batch_size):
            batch = filtered_symbols[i:i + batch_size]

            results = await asyncio.gather(
                *[analyze_symbol_with_semaphore(coin) for coin in batch],
                return_exceptions=False
            )

            for r in results:
                if isinstance(r, dict) and r:
                    # Futures bonusu uygula (✅ doğru alan: potential_pct)
                    r["futures_bonus"] = True
                    try:
                        pot = float(r.get("potential_pct", 0.0) or 0.0)
                        pot *= 1.1
                        r["potential_pct"] = pot

                        # Geriye uyumluluk (bazı eski yerler movement_potential bekliyorsa)
                        r["movement_potential"] = pot
                    except Exception as e:
                        logging.error(f"[POT_BONUS_ERR] {r.get('symbol','?')}: {e}")

                    # normalize tekrar (güvenlik)
                    try:
                        r["symbol"] = cls.normalize_symbol(r.get("symbol", ""))
                    except (TypeError, ValueError, KeyError):
                        pass

                    high_potential_coins.append(r)

            logging.info(f"📊 Batch {i // batch_size + 1}/{(total - 1) // batch_size + 1} işlendi...")
            await asyncio.sleep(0.25)

        # Sıralama (✅ potential_pct)
        high_potential_coins.sort(
            key=lambda x: float((x or {}).get("potential_pct", 0.0) or 0.0),
            reverse=True
        )
        logging.info(f"✅ {len(high_potential_coins)} yüksek potansiyel vadeli coini sıralandı")

        # Sonuçları kanallara/alarmlara işleme: ÇALIŞMA MODUNDA ALARM KURMA!
        if high_potential_coins:
            try:
                if getattr(cls, "run_ai_strategy_active", False) and not getattr(cls, "allow_scans_while_running", False):
                    logging.info("[RUN_MODE] Otomatik alarm kurma devre dışı (ai_coin_scanner_only)")
                else:
                    await cls._process_high_potential_futures_coins(context, high_potential_coins)
            except Exception as process_error:
                logging.error(f"❌ Sonuç işleme hatası: {process_error}")

        # Özet mesajı çalışma modunda göndermeyelim
        try:
            if high_potential_coins and not getattr(cls, "run_ai_strategy_active", False):
                await cls.send_ai_scan_results(context, high_potential_coins)
        except Exception as e:
            logging.error(f"[AI_SCAN_SUMMARY_SEND_ERR] {e}")

        # Cache güncelle
        cls.ai_scan_cache = {
            "timestamp": datetime.now(timezone.utc),
            "count": len(high_potential_coins)
        }

        return high_potential_coins

    except Exception as unexpected_error:
        logging.error(f"❌ Beklenmeyen RealAI tarama hatası: {unexpected_error}", exc_info=True)
        return []


# collect_comprehensive_training_data
def preprocess_training_data(cls, df):
    """
    Eğitim verilerini ön işleme
    """
    _ = cls
    if not HAS_TALIB:
        return df

    try:
        # NaN değerlerini temizle
        df = df.dropna()
        if df.empty:
            logging.warning("📉 Boş DataFrame, veri işleme yapılamıyor.")
            return df  # Boş döndür
            # DÜZELTME: 'col' kullanılmadığı için döngü kaldırıldı, daha verimli pandas metotları kullanıldı.
        numeric_columns = ['close', 'volume']
        for column_name in numeric_columns:

            q1 = df[column_name].quantile(0.25)
            q3 = df[column_name].quantile(0.75)
            iqr = q3 - q1
            lower_bound = q1 - 1.5 * iqr
            upper_bound = q3 + 1.5 * iqr

            df = df[(df[column_name] >= lower_bound) & (df[column_name] <= upper_bound)]

        # Teknik göstergeleri ekle
        df['rsi'] = talib.RSI(df['close'].values, timeperiod=14)
        df['macd'], df['signal'], _ = talib.MACD(df['close'].values)
        df['cci'] = talib.CCI(df['high'].values, df['low'].values, df['close'].values, timeperiod=14)

        return df

    except Exception as e:
        logging.error(f"❌ Veri ön işleme hatası: {e}")
        return df


# collect_comprehensive_training_data
async def collect_comprehensive_training_data(cls, exchange: str, symbols_count: int = 50):
    _ = cls
    """
    Belirtilen borsa için kapsamlı eğitim verisi toplar.
    DÜZELTME: Artık 'context' yerine 'exchange' alıyor ve bu borsaya özel bir exchange nesnesi kuruyor.
    """
    temp_exchange = None
    try:  # type: ignore
        temp_exchange = None  # temp_exchange'i başlangıçta None olarak ayarla
        logging.info("📊 Kapsamlı eğitim verisi toplanıyor...")

        temp_exchange = None  # temp_exchange'i başlangıçta None olarak ayarla
        # Borsa bazlı geçici bir exchange nesnesi oluştur
        try:
            # DÜZELTME: Borsa sınıfının ccxt içinde var olup olmadığını kontrol et.
            if not hasattr(ccxt, exchange):
                logging.error(
                    f"Hata: {exchange.upper()} borsası CCXT kütüphanesinde desteklenmiyor. Lütfen `pip install --upgrade ccxt` komutu ile kütüphaneyi güncelleyin.")
                return []

            exchange_class = getattr(ccxt, exchange)
            temp_exchange = exchange_class({'enableRateLimit':True, 'timeout':30000})
            await temp_exchange.load_markets()
            logging.info(f"Eğitim için geçici {exchange.upper()} nesnesi oluşturuldu.")
        except Exception as ex_err:
            logging.error(f"Eğitim için {exchange.upper()} nesnesi oluşturulamadı: {ex_err}")
            return []

        # Ticker alma
        tickers = await temp_exchange.fetch_tickers()
        if not isinstance(tickers, dict) or not tickers:
            logging.error("❌ Ticker verisi yok (training_data).")
            return []

        all_symbols = [
            s for s in tickers.keys()
            # DÜZELTME: Bybit'in 'BTCUSDT' formatını da yakalamak için daha esnek filtreleme
            if isinstance(s, str) and (
                    s.endswith('/USDT') or s.endswith(':USDT') or (s.endswith('USDT') and '/' not in s and ':' not in s)
            ) and not any(x in s for x in ['UP', 'DOWN', '3L', '3S', '5L', '5S'])
        ]

        pairs = []
        for sym in all_symbols:
            t = tickers.get(sym) or {}
            # quoteVolume'u güvenli şekilde sayıya çevir
            try:
                qv = float(
                    t.get('quoteVolume') or
                    (t.get('info') or {}).get('quoteVolume') or
                    0.0
                )
            except (ValueError, TypeError, KeyError):
                qv = 0.0
            pairs.append((sym, qv))

        # En yüksek quoteVolume'a göre sırala
        sorted_symbols = sorted(pairs, key=lambda x:x[1], reverse=True)

        comprehensive_data = []
        processed_symbols = 0

        for symbol, _qv in sorted_symbols[:symbols_count]:
            try:
                # Farklı timeframe'lerde veri al
                timeframes = ['1h', '4h', '1d']
                for timeframe in timeframes:
                    ohlcv = await temp_exchange.fetch_ohlcv(
                        symbol, timeframe, limit=500
                    )

                    if ohlcv and len(ohlcv) >= 300:
                        df = pd.DataFrame(
                            ohlcv,
                            columns=[
                                'timestamp', 'open', 'high',
                                'low', 'close', 'volume'
                            ]
                        )
                        df['symbol'] = symbol
                        df['timeframe'] = timeframe

                        comprehensive_data.append((symbol, df))
                        processed_symbols += 1

                    await asyncio.sleep(0.1)  # Rate limit

            except (ValueError, TypeError, KeyError) as symbol_data_err:
                logging.error(
                    f"❌ {symbol} veri parse hatası: {symbol_data_err}"
                )
                continue
            except (OSError, RuntimeError) as symbol_runtime_err:
                logging.error(
                    f"❌ {symbol} runtime hatası: {symbol_runtime_err}"
                )
                continue
            except Exception as symbol_error:
                # Beklenmeyen hata: ayrı logla ama akışı sürdür
                logging.error(
                    f"❌ {symbol} beklenmeyen veri toplama hatası: {symbol_error}"
                )
                continue

        logging.info(f"✅ {processed_symbols} sembolden eğitim verisi toplandı")
        return comprehensive_data

    except (ValueError, TypeError, KeyError) as top_level_expected:
        logging.error(
            f"❌ Kapsamlı eğitim verisi toplama (beklenen) hatası: {top_level_expected}"
        )
        return []
    except (OSError, RuntimeError) as top_level_runtime:
        logging.error(
            f"❌ Kapsamlı eğitim verisi toplama runtime hatası: {top_level_runtime}"
        )
        return []
    except Exception as e:
        logging.error(
            f"❌ Kapsamlı eğitim verisi toplama beklenmeyen hata: {e}"
        )
        return []
    finally:
        if temp_exchange:
            await temp_exchange.close()


# train_ai_model_advanced
async def train_ai_model_advanced(cls, training_data, exchange: str):
    """
    Gelişmiş AI modelini eğitir ve modelleri borsaya özel olarak kaydeder.
    """
    try:
        # AI model kontrolü
        if not cls._ai_model:
            cls._ai_model = RealAIModel()
        logger.info(f"[{exchange.upper()}] Gelişmiş AI model eğitimi başlıyor...")
        # Veri ön işleme
        processed_data = []
        for symbol, df in training_data:
            logger.debug(f"[{exchange.upper()}] Ön işleme: {symbol}")
            # Veri temizleme ve zenginleştirme
            df = preprocess_training_data(cls, df)
            processed_data.append((symbol, df))

        logger.info(f"[{exchange.upper()}] {len(processed_data)} adet veri ön işlendi. Model eğitimine geçiliyor...")
        # Eğitim
        # DÜZELTME: train metodu artık borsa ismini de almalı ki modelleri doğru isimle kaydetsin.
        # RealAIModel.py dosyasındaki train ve save_models metodlarını da güncellemeniz gerekecek.
        training_success = cls._ai_model.train(processed_data, exchange=exchange)
        logger.info(f"[{exchange.upper()}] Model eğitim sonucu: {'Başarılı' if training_success else 'Başarısız'}")

        if training_success:
            # Metadata'yı güncelle
            metadata_path = "models/metadata.json"
            all_metadata = {}
            # DÜZELTME: Dosyayı yazmadan önce mevcut içeriğini oku. Bu, her eğitimin diğerlerini silmesini engeller.
            if os.path.exists(metadata_path) and os.path.getsize(metadata_path) > 0:
                with open(metadata_path, 'r') as f:
                    try:
                        all_metadata = json.load(f)
                    except json.JSONDecodeError:
                        logger.warning("metadata.json bozuk, yeniden oluşturulacak.")

            # İlgili borsa için yeni metadata'yı oluştur ve ata
            # model_results_for_json, cls._ai_model.training_metadata'dan gelmeli
            model_results_for_json = cls._ai_model.training_metadata.get('model_results', {})

            all_metadata[exchange] = {
                "timestamp":datetime.now(timezone.utc).isoformat(),
                "model_results":model_results_for_json
            }

            with open(metadata_path, 'w') as f:
                json.dump(all_metadata, f, indent=4)

            logger.info(f"✅ {exchange.upper()} için metadata.json güncellendi.")

            # Sınıf değişkenini de güncelle
            cls._last_model_train_time = datetime.now(timezone.utc)

            logging.info(f"✅ {exchange.upper()} için Gelişmiş AI model eğitimi tamamlandı")
            return True
        else:
            logging.error("❌ Gelişmiş eğitim başarısız")
            return False

    except Exception as e:
        logging.error(f"❌ Gelişmiş eğitim hatası: {e}")
        return False


# preprocess_training_data
async def train_ai_model_dynamic(cls, exchange: str, triggered_by_user_id: Optional[int] = None) -> bool:
    """
    Belirtilen borsa ('exchange') için AI modelini eğitir, sonuçları kaydeder ve kullanıcıyı bilgilendirir.
    Bu fonksiyon, ana olay döngüsünü engellemeden ve aynı anda sadece bir eğitim çalışacak şekilde tasarlanmıştır.
    """
    # "MEŞGUL" tabelasını kontrol et
    if cls._is_training_in_progress:
        logger.warning(f"🏃‍♂️ Model eğitimi zaten devam ediyor. {exchange.upper()} için yeni talep reddedildi.")
        if triggered_by_user_id and cls._application:
            try:
                await cls._application.bot.send_message(
                    chat_id=triggered_by_user_id,
                    text="⚠️ Bir model eğitimi zaten devam ediyor. Lütfen mevcut işlem bittikten sonra tekrar deneyin."
                )
            except Exception as e:
                logger.error(f"Kullanıcı bilgilendirme hatası: {e}")
        return False

    # Atölyeyi kilitle ve "MEŞGUL" tabelasını as
    async with cls._training_lock:
        cls._is_training_in_progress = True
        logger.info(f"🚀 Arka planda {exchange.upper()} için model eğitimi görevi başlatılıyor ve sistem kilitlendi.")

        try:
            # 1. Veri Toplama: Belirtilen borsa için veri topla.
            # `collect_comprehensive_training_data` fonksiyonu artık 'exchange' parametresi almalı.
            logger.info(f"📊 {exchange.upper()} için eğitim verisi toplanıyor...")
            training_data = await cls.collect_comprehensive_training_data(exchange=exchange, symbols_count=50)

            if not training_data:
                logger.error(f"❌ {exchange.upper()} için eğitim verisi toplanamadı. Eğitim iptal edildi.")
                if triggered_by_user_id and cls._application:
                    await cls._application.bot.send_message(
                        chat_id=triggered_by_user_id,
                        text=f"❌ {exchange.upper()} için eğitim verisi toplanamadı. İşlem iptal edildi."
                    )
                return False

            logger.info(f"✅ {exchange.upper()} için {len(training_data)} sembolden veri toplandı.")

            # 2. Model Eğitimi: Gelişmiş eğitim fonksiyonuna 'exchange' parametresini gönder.
            # `train_ai_model_advanced` fonksiyonu artık 'exchange' parametresi almalı.
            training_success = await train_ai_model_advanced(cls, training_data, exchange=exchange)

            # 3. Kullanıcıyı Bilgilendirme
            if triggered_by_user_id and cls._application:
                if training_success:
                    message = f"✅ {exchange.upper()} için AI modeli başarıyla eğitildi ve güncellendi!"
                else:
                    message = f"❌ {exchange.upper()} için AI modeli eğitimi sırasında bir hata oluştu. Detaylar için logları kontrol edin."

                await cls._application.bot.send_message(
                    chat_id=triggered_by_user_id,
                    text=message
                )

            if training_success:
                logger.info(f"✅ {exchange.upper()} için AI modeli başarıyla eğitildi ve güncellendi!")
            else:
                logger.error(f"❌ {exchange.upper()} için AI modeli eğitimi başarısız oldu.")

            return training_success

        except Exception as e:
            logger.error(f"❌ {exchange.upper()} için AI eğitimi sırasında kritik hata: {e}", exc_info=True)
            if triggered_by_user_id and cls._application:
                try:
                    await cls._application.bot.send_message(
                        chat_id=triggered_by_user_id,
                        text=f"❌ {exchange.upper()} için eğitimi başlatırken kritik bir hata oluştu: {e}"
                    )
                except Exception as e:
                    logging.error(f"Hata: {e}")
                    pass
            return False
        finally:
            # İş bitince "MEŞGUL" tabelasını kaldır ve kilidi aç
            cls._is_training_in_progress = False
            logger.info(f"🔑 {exchange.upper()} için model eğitimi görevi tamamlandı, sistem kilidi açıldı.")

            # YENİ: Eğitim sonrası admin menüsünü tekrar göster
            if triggered_by_user_id and cls._application:
                try:
                    from olimpos_admin import admin_menu
                    # DÜZELTME: Artık update=None ve chat_id_override ile çağırıyoruz.
                    await admin_menu(update=None, context=cls._application, chat_id_override=triggered_by_user_id)
                except Exception as menu_err:
                    logger.error(f"Eğitim sonrası admin menüsü gösterilemedi: {menu_err}")


# get_ai_recommended_timeframe
async def get_ai_recommended_timeframe(cls, symbol: str, context: Optional[CallbackContext] = None):
    """
    AI ile en uygun timeframe önerisi (yeni yapı uyumlu):
    - exchange.fetch_ohlcv direkt kullanılmaz
    - fetch_ohlcv_with_retry tek kapıdır
    """
    try:
        if not getattr(cls, "exchange", None):
            logging.warning("[AI_TF] exchange yok, timeframe önerisi yapılamadı")
            return None

        # futures CCXT sembolüne çevir
        ccxt_symbol = cls.to_ccxt_symbol(symbol, prefer_futures=True)
        if not ccxt_symbol or not cls.has_futures_market(ccxt_symbol):
            logging.warning(f"[AI_TF] futures market yok / sembol geçersiz: {symbol} -> {ccxt_symbol}")
            return None

        best_score = -1e9
        best_timeframe = None

        for tf in ["5m", "15m", "30m", "1h", "4h", "1d"]:
            try:
                df = await fetch_ohlcv_with_retry(cls,
                    ccxt_symbol,
                    timeframe=tf,
                    max_retries=2,
                    timeout=20,
                    context=context
                )
                if df is None or df.empty or len(df) < 60:
                    continue

                ai_prediction = cls._ai_model.predict(df)
                if not ai_prediction:
                    continue

                confidence = float(ai_prediction.get("confidence", 0.0) or 0.0)
                potential = float(cls.calculate_movement_potential(df, ai_prediction) or 0.0)

                score = confidence * potential
                if score > best_score:
                    best_score = score
                    best_timeframe = tf

            except Exception as tf_error:
                logging.error(f"[AI_TF_ERR] {ccxt_symbol} tf={tf}: {tf_error}")
                continue

        return best_timeframe

    except Exception as e:
        logging.error(f"[AI_TF_FATAL] {symbol}: {e}", exc_info=True)
        return None


# safe_fetch_tickers
async def safe_fetch_tickers(
    cls,
    symbols: Optional[List[str]] = None,
    retries: int = 3,
    delay: float = 2.0
) -> Dict[str, dict]:
    """
    fetch_tickers için güvenli sarmalayıcı. Her zaman Dict[str, dict] döndürür.

    Garantiler:
    - None dönmez (NoneType.keys hatasını bitirir)
    - dict değilse retry eder
    - boş dict ise retry eder (son çare cache'e düşer)
    - aynı anda çağrıları cls._ticker_sem ile sınırlar
    - kritik hatalarda (timeout / session closed / network) soft restart dener (cooldown'lu)
    """
    # param guard
    try:
        retries_i = int(retries)
    except Exception:
        retries_i = 3
    retries_i = max(1, retries_i)

    try:
        delay_f = float(delay)
    except Exception:
        delay_f = 2.0
    delay_f = max(0.0, delay_f)

    # semafor garanti
    if not hasattr(cls, "_ticker_sem") or getattr(cls, "_ticker_sem") is None:
        cls._ticker_sem = asyncio.Semaphore(2)

    last_error: Any = None

    # son cache (fallback için)
    cached_last = None
    try:
        cached_last = getattr(cls, "_last_tickers", None)
    except Exception:
        cached_last = None

    for attempt in range(1, retries_i + 1):
        ex = getattr(cls, "exchange", None)

        # exchange yoksa restart dene
        if ex is None:
            logging.warning(f"[SAFE_FETCH_TICKERS] exchange yok | attempt={attempt}/{retries_i}")
            try:
                await cls.soft_restart_exchange(context=None, reason="safe_fetch_tickers_no_exchange")
            except Exception as e:
                last_error = e

            ex = getattr(cls, "exchange", None)
            if ex is None:
                await asyncio.sleep(delay_f * attempt)
                continue

        try:
            # MEXC küçük gecikme (spam azaltır)
            if getattr(ex, "id", "") == "mexc":
                await asyncio.sleep(0.15)

            async with cls._ticker_sem:
                raw: Any = await asyncio.wait_for(
                    ex.fetch_tickers(symbols) if symbols else ex.fetch_tickers(),
                    timeout=30.0
                )

            # None / tip / boş kontrol
            if raw is None:
                last_error = ValueError("fetch_tickers returned None")
                logging.warning(f"[SAFE_FETCH_TICKERS] None response | attempt={attempt}/{retries_i}")
                await asyncio.sleep(delay_f * attempt)
                continue

            if not isinstance(raw, dict):
                last_error = TypeError(f"fetch_tickers returned {type(raw).__name__}")
                logging.warning(
                    f"[SAFE_FETCH_TICKERS] non-dict response | attempt={attempt}/{retries_i} type={type(raw).__name__}"
                )
                await asyncio.sleep(delay_f * attempt)
                continue

            if not raw:
                last_error = ValueError("fetch_tickers returned empty dict")
                logging.warning(f"[SAFE_FETCH_TICKERS] empty dict | attempt={attempt}/{retries_i}")
                await asyncio.sleep(delay_f * attempt)
                continue

            # temizle: sadece str->dict
            clean: Dict[str, dict] = {}
            for k, v in raw.items():
                if isinstance(k, str) and isinstance(v, dict):
                    clean[k] = v

            if clean:
                # cache güncelle
                try:
                    cls._last_tickers = clean
                except Exception:
                    pass
                return clean

            last_error = ValueError("fetch_tickers returned dict but no valid (str->dict) entries")
            logging.warning(f"[SAFE_FETCH_TICKERS] cleaned empty | attempt={attempt}/{retries_i}")
            await asyncio.sleep(delay_f * attempt)
            continue

        except asyncio.TimeoutError as e:
            last_error = e
            logging.warning(f"[SAFE_FETCH_TICKERS] timeout | attempt={attempt}/{retries_i}")
            if attempt < retries_i:
                try:
                    await cls.soft_restart_exchange(context=None, reason="safe_fetch_tickers_timeout")
                except Exception as re_err:
                    last_error = re_err
            await asyncio.sleep(delay_f * attempt)
            continue

        except ccxt.NetworkError as e:
            last_error = e
            logging.warning(f"[SAFE_FETCH_TICKERS] NetworkError | attempt={attempt}/{retries_i} err={e!r}")
            if attempt < retries_i:
                try:
                    await cls.soft_restart_exchange(context=None, reason="safe_fetch_tickers_network")
                except Exception as re_err:
                    last_error = re_err
            await asyncio.sleep(delay_f * attempt)
            continue

        except Exception as e:
            last_error = e
            msg = str(e or "")
            is_session_closed = ("Session is closed" in msg) or ("session is closed" in msg)
            is_conn_closed = ("Connector is closed" in msg) or ("Unclosed" in msg)

            logging.warning(
                f"[SAFE_FETCH_TICKERS] Exception | attempt={attempt}/{retries_i} "
                f"type={type(e).__name__} err={msg}"
            )

            if (is_session_closed or is_conn_closed) and attempt < retries_i:
                try:
                    await cls.soft_restart_exchange(context=None, reason="safe_fetch_tickers_session_closed")
                except Exception as re_err:
                    last_error = re_err

            await asyncio.sleep(delay_f * attempt)
            continue

    # give up: cache fallback
    if isinstance(cached_last, dict):
        logging.error(f"[SAFE_FETCH_TICKERS] Give up -> cached_return | retries={retries_i} last_error={last_error!r}")
        return cached_last

    logging.error(f"[SAFE_FETCH_TICKERS] Give up | retries={retries_i} last_error={last_error!r}")
    return {}


async def soft_restart_exchange(cls, context=None, reason: str = "") -> bool:
    """
    Exchange'i güvenli biçimde soft-restart eder.

    - restart lock + cooldown (spam'i engeller)
    - eski exchange instance'ı mümkünse await ex.close() ile kapatır
    - MEXC için defaultType=swap
    - restart sonrası MarketDataService rebind edilir
    """
    if not hasattr(cls, "_exchange_restart_lock") or getattr(cls, "_exchange_restart_lock") is None:
        cls._exchange_restart_lock = asyncio.Lock()

    async with cls._exchange_restart_lock:
        now = time.monotonic()
        cooldown_until = float(getattr(cls, "_exchange_restart_cooldown_until", 0.0) or 0.0)
        if now < cooldown_until:
            logging.info(f"[EXCHANGE_RESTART] cooldown aktif, atlandı. reason={reason}")
            return False

        ex = getattr(cls, "exchange", None)

        # exchange id tespiti
        exid = None
        try:
            exid = getattr(ex, "id", None)
        except Exception:
            exid = None

        if not exid:
            try:
                exid = (getattr(cls, "exchange_api_info", {}) or {}).get("exchange_name")
            except Exception:
                exid = None

        exid = (exid or "mexc").lower().strip()

        exchange_class = getattr(ccxt, exid, None)
        if exchange_class is None:
            logging.error(f"[EXCHANGE_RESTART] ccxt içinde exchange yok: {exid}")
            cls._exchange_restart_cooldown_until = time.monotonic() + 25.0
            return False

        logging.warning(f"[EXCHANGE_RESTART] {exid} yeniden başlatılıyor... reason={reason}")

        # eski instance'ı kapat (leak fix)
        if ex is not None:
            try:
                # bazı sürümlerde session ayrı kapanabiliyor
                try:
                    sess = getattr(ex, "session", None)
                    if sess is not None and getattr(sess, "closed", True) is False:
                        await asyncio.wait_for(sess.close(), timeout=2.0)
                except Exception:
                    pass

                await asyncio.wait_for(ex.close(), timeout=8.0)
                logging.info(f"[EXCHANGE_RESTART] {exid} kapatıldı")
            except Exception as e:
                logging.warning(f"[EXCHANGE_RESTART] close error: {e}")

        cls.exchange = None
        await asyncio.sleep(0.4)

        # yeni instance config (public için yeterli)
        options: Dict[str, Any] = {
            "adjustForTimeDifference": True,
            "recvWindow": 10000,
            "createMarketBuyOrderRequiresPrice": False,
        }

        # Futures defaultType
        if exid == "mexc":
            options["defaultType"] = "swap"
            options.pop("timeDifference", None)
        elif exid == "binance":
            # binance futures
            # bazı sürümlerde root defaultType daha doğru; burada conservative kalıyoruz
            pass
        elif exid == "bybit":
            # bybit linear
            pass

        new_ex = exchange_class({
            "enableRateLimit": True,
            "timeout": 30000,
            "rateLimit": 200,
            "options": options,
        })

        cls.exchange = new_ex

        # markets yükle
        try:
            await asyncio.wait_for(new_ex.load_markets(), timeout=20.0)
            logging.info("[EXCHANGE_RESTART] Markets yüklendi")
        except Exception as e:
            logging.warning(f"[EXCHANGE_RESTART] Markets load error: {e}")

        # cooldown
        cls._exchange_restart_cooldown_until = time.monotonic() + 25.0
        logging.info("[EXCHANGE_RESTART] cooldown set edildi (25s)")

        # md rebind (tek doğru yol)
        try:
            _ensure_market_data_service(cls)
        except Exception as e:
            logging.warning(f"[EXCHANGE_RESTART] MarketDataService rebind hata: {e}")

        logging.info(f"[EXCHANGE_RESTART_DEBUG] exchange_id={getattr(getattr(cls, 'exchange', None), 'id', None)}")
        return True


# get_cached_tickers
async def get_cached_tickers(cls) -> Dict[str, Dict[str, Any]]:
    """
    Ticker verilerini cache'den döndürür veya güvenli şekilde yeniden çeker.

    Garantiler:
    - Her zaman dict döndürür (None dönmez)
    - safe_fetch_tickers başarısızsa cache'e düşer
    - MEXC için opsiyonel özel fallback (varsa) dener
    """
    try:
        now = datetime.now(timezone.utc)

        # 1) cache TTL
        ttl_sec = 10.0
        try:
            cache = getattr(cls, "_ticker_cache", None)
        except Exception:
            cache = None

        if cache and isinstance(cache, tuple) and len(cache) == 2:
            cache_time, cache_data = cache
            if isinstance(cache_time, datetime) and isinstance(cache_data, dict) and cache_data:
                if (now - cache_time).total_seconds() < ttl_sec:
                    return cache_data

        # 2) fetch
        tickers = await cls.safe_fetch_tickers()

        # 3) doğrula
        if isinstance(tickers, dict) and tickers:
            try:
                cls._ticker_cache = (now, tickers)
                cls._last_tickers = tickers
            except Exception:
                pass
            return tickers

        # 4) dict değil/boş -> MEXC özel fallback (opsiyonel)
        ex = getattr(cls, "exchange", None)
        exid = getattr(ex, "id", "") if ex else ""
        if exid == "mexc":
            try:
                # Bu fonksiyon projede varsa kullan; yoksa except'e düşer.
                from settings.mexc_api_ayarlari import _fetch_mexc_tickers_fallback
                fb = await _fetch_mexc_tickers_fallback()
                if isinstance(fb, dict) and fb:
                    try:
                        cls._ticker_cache = (now, fb)
                        cls._last_tickers = fb
                    except Exception:
                        pass
                    logging.warning("[GET_CACHED_TICKERS] MEXC fallback ile tickers alındı")
                    return fb
            except Exception as e:
                logging.warning(f"[GET_CACHED_TICKERS] MEXC fallback başarısız: {e}")

        # 5) cache fallback
        try:
            last = getattr(cls, "_last_tickers", None)
            if isinstance(last, dict):
                return last
        except Exception:
            pass

        return {}

    except Exception as e:
        logging.error(f"[GET_CACHED_TICKERS_ERR] {e}", exc_info=True)
        # son çare cache
        try:
            last = getattr(cls, "_last_tickers", None)
            if isinstance(last, dict):
                return last
        except Exception:
            pass
        return {}


# _emergency_ticker_fallback
async def emergency_ticker_fallback(cls, symbol: str) -> Optional[dict]:
    """
    Tek bir sembol için acil durum ticker fetch
    """
    try:
        if not cls.exchange:
            return None

        # Tek sembol fetch dene
        ticker = await asyncio.wait_for(
            cls.exchange.fetch_ticker(symbol),
            timeout=10.0
        )

        if ticker and isinstance(ticker, dict):
            return ticker

    except Exception as e:
        logging.debug(f"[EMERGENCY_TICKER] {symbol} başarısız: {e}")

    return None


# get_futures_symbols_only
async def get_futures_symbols_only(cls):
    """
    DÜZELTİLDİ: MEXC futures sembol formatını doğru yakala
    """
    try:
        if not cls.exchange or not hasattr(cls.exchange, 'markets'):
            logging.warning("[GET_FUT_SYMBOLS] Exchange veya markets yok")
            return []

        markets = cls.exchange.markets or {}
        if not markets:
            logging.warning("[GET_FUT_SYMBOLS] Markets boş")
            return []

        # Minimum hacim
        s = cls._load_scan_settings()
        min_vol = float(s.get('scan', {}).get('futures_min_volume_usdt', 2_000_000))

        futures_list = []

        for symbol, market_info in markets.items():
            try:
                # 🔧 DÜZELTİLDİ: MEXC futures formatı kontrolü
                # MEXC'de futures: "BTC/USDT:USDT", "ETH/USDT:USDT" formatında

                # 1. Tip kontrolü
                market_type = market_info.get('type', '').lower()
                is_futures = market_type in ['swap', 'future', 'linear', 'inverse']

                # 2. Sembol formatı kontrolü (":USDT" veya ":USDT.P" içeriyorsa)
                has_settle = ':' in symbol and (
                        symbol.endswith(':USDT') or
                        symbol.endswith(':USDT.P') or
                        symbol.endswith(':USD')
                )

                # 3. Active kontrolü
                is_active = market_info.get('active', True)

                # Futures olarak kabul et
                if (is_futures or has_settle) and is_active:
                    # USDT çiftlerini filtrele
                    if '/USDT' in symbol or symbol.endswith(':USDT'):
                        futures_list.append({
                            'symbol':symbol,
                            'market_type':'futures',
                            'type':market_type
                        })

            except Exception as e:
                logging.debug(f"[GET_FUT_SYMBOLS] {symbol} hata: {e}")
                continue

        logging.info(
            f"[GET_FUT_SYMBOLS] Toplam: {len(futures_list)} futures sembol "
            f"(min_volume={format_usd_short(min_vol)})"
        )

        # İlk 10'u logla
        for i, item in enumerate(futures_list[:10], 1):
            logging.debug(f"  {i}. {item['symbol']} type={item.get('type', 'N/A')}")

        return futures_list

    except Exception as e:
        logging.error(f"[GET_FUT_SYMBOLS_ERR] {e}")
        return []


# _is_valid_futures_market
def is_valid_futures_market(cls, market_info: dict) -> bool:
    _ = cls
    """
    Market bilgisinin geçerli bir futures/swap olup olmadığını kontrol eder
    """
    if not market_info:
        return False

    # Durum kontrolü
    info = market_info.get('info') or {}
    status = str(info.get('status') or info.get('state') or 'TRADING').upper()
    status_ok = (
            market_info.get('active') is True or
            status in ('TRADING', 'ONLINE', 'ENABLED', 'OPEN', '1', '0')
    )

    # Tip kontrolü
    mtype = str(market_info.get('type') or '').lower()
    is_futures = (
            market_info.get('swap') or
            market_info.get('contract') or
            mtype in ('swap', 'future', 'futures')
    )

    return status_ok and is_futures


# _parse_futures_symbol
def parse_futures_symbol(cls, symbol: str, market_info: dict) -> Optional[dict]:
    _ = cls
    _ = market_info
    """
    DİNAMİK: Futures sembolünü parse eder ve spot karşılığını bulur

    Örnekler:
        BTC/USDT:USDT -> {base: BTC, spot: BTC/USDT, mult: 1}
        1000PEPE/USDT:USDT -> {base: PEPE, spot: PEPE/USDT, mult: 1000}
        1000000MOG/USDT:USDT -> {base: MOG, spot: MOG/USDT, mult: 1000000}

    Args:
        symbol: Futures sembol (exchange formatında)
        market_info: CCXT market bilgisi

    Returns:
        dict veya None
    """
    try:
        # Format: BASE/QUOTE:SETTLE
        if ':' not in symbol:
            return None

        left, settle = symbol.split(':', 1)

        if '/' not in left:
            return None

        base_raw, quote = left.split('/', 1)

        # Çarpan tespiti (dinamik)
        multiplier = 1
        base_clean = base_raw

        # 1000000 çarpanı kontrolü
        if base_raw.startswith('1000000'):
            multiplier = 1000000
            base_clean = base_raw[7:]  # "1000000" çıkar

        # 1000 çarpanı kontrolü
        elif base_raw.startswith('1000'):
            multiplier = 1000
            base_clean = base_raw[4:]  # "1000" çıkar

        # 100 çarpanı kontrolü (nadiren kullanılır)
        elif base_raw.startswith('100') and len(base_raw) > 3:
            # "100" ile başlayan ama normal coin olmayan durumlar
            # Örnek: 100FLOKI gibi
            test_base = base_raw[3:]
            if len(test_base) >= 3:  # En az 3 karakter olmalı
                multiplier = 100
                base_clean = test_base

        # Spot sembol oluştur
        spot_symbol = f"{base_clean}/{quote}"

        return {
            'base':base_clean,
            'quote':quote,
            'settle':settle,
            'spot_symbol':spot_symbol,
            'multiplier':multiplier,
            'raw_base':base_raw
        }

    except Exception as e:
        logging.debug(f"[PARSE_FUT_SYM_ERR] {symbol}: {e}")
        return None


# get_futures_from_spot
def get_futures_from_spot(cls, spot_symbol: str) -> str:
    """
    Spot sembolünden futures karşılığını dinamik olarak bulur.

    Args:
        cls: Sınıf referansı (classmethod içinde otomatik geçer).
        spot_symbol: Spot sembolü (ör. "MOG/USDT", "PEPE/USDT").

    Returns:
        Futures sembolü (ör. "1000000MOG/USDT:USDT", "1000PEPE/USDT:USDT").
        Piyasa bulunamazsa makul bir varsayılan format döndürür.
    """
    try:
        if not cls.exchange:
            return f"{spot_symbol}:USDT"

        # Normalize
        normalized = cls.normalize_symbol(spot_symbol)

        # Markets'te ara
        markets = getattr(cls.exchange, 'markets', {})
        if not markets:
            return f"{normalized}:USDT"

        # Base/Quote ayır
        if '/' not in normalized:
            return f"{normalized}:USDT"

        base, quote = normalized.split('/', 1)

        # Olası futures formatları
        candidates = [
            f"{base}/{quote}:USDT",  # Standart
            f"1000{base}/{quote}:USDT",  # 1000x
            f"1000000{base}/{quote}:USDT",  # 1000000x
            f"100{base}/{quote}:USDT",  # 100x
        ]

        # İlk geçerli eşleşeni döndür
        for candidate in candidates:
            if candidate in markets:
                market_info = markets[candidate]
                if is_valid_futures_market(cls, market_info):
                    logging.debug(f"[GET_FUT_FROM_SPOT] {spot_symbol} -> {candidate}")
                    return candidate

        # Bulunamadı, standart format döndür
        return f"{base}/{quote}:USDT"

    except Exception as e:
        logging.error(f"[GET_FUT_FROM_SPOT_ERR] {spot_symbol}: {e}")
        return f"{spot_symbol}:USDT"
