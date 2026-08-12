# strategies/alarm_system/monitoring.py
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from typing import Any, Optional
import pandas as pd
from telegram.ext import CallbackContext
from data.olimpos_data import get_api_key, get_user_settings
import logging
import os
from core.strategy_manager import StrategyManager as SMRef
from io import BytesIO
from config_service import ConfigService
from strategies.alarm_system import persistence as alarm_persistence
from config.constants import ADMIN_USER_ID
# Stratejileri Kaydet
from strategies.strategy_v1 import StrategyV1
from strategies.strategy_v2 import StrategyV2
SMRef.register(StrategyV1)
SMRef.register(StrategyV2)
import talib as ta



# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    force=True
)

logger = logging.getLogger(__name__)


def _map_global_tf(local_tf: str) -> str:
    local_tf = str(local_tf or "").strip()
    mapping = {
        "1m":"15m",
        "3m":"1h",
        "5m":"1h",
        "15m":"4h",
        "30m":"4h",
        "45m":"4h",
        "1h":"1d",
        "2h":"1d",
        "4h":"1d",
        "6h":"1d",
        "8h":"1d",
        "12h":"1d",
        "1d":"1d",
    }
    # Config override: trend.global_tf_map.15m = "4h" gibi
    try:
        cfg_map = ConfigService.get("trend.global_tf_map", {}) or {}
        if isinstance(cfg_map, dict) and local_tf in cfg_map:
            return str(cfg_map[local_tf])
    except Exception:
        pass
    return mapping.get(local_tf, "1d")


def _calc_trend_snapshot_from_df(
        df: pd.DataFrame,
        sym: str,
        tf: str,
        ema_period: int = 200,
        adx_period: int = 14,
        ema_tolerance: float = 0.005,
        adx_min_trend: float = 15.0
) -> dict:
    """
    DF closed mum olmalı (sen zaten df_closed kullanıyorsun).
    Trend kanıtı: EMA + ADX.
    Net trend: ADX düşükse NÖTR.

    DÜZELTME:
      - Bazı borsalar 1d/4h gibi TF’lerde 200 bar altı döndürebiliyor.
      - Önceden strict len kontrolü yüzünden EMA/ADX hesaplanmadan 0 kalıyor -> mesajda N/A görünüyordu.
      - Artık yeterli veri yoksa period’ları güvenli şekilde küçültüp yine snapshot üretiyoruz.
    """
    out = {
        "symbol": str(sym),
        "tf": str(tf),
        "close": 0.0,
        "ema200": 0.0,
        "adx": 0.0,
        "raw_trend": "❓ Bilinmiyor",
        "net_trend": "❓ Bilinmiyor",
        "method": "EMA200(+tolerance)+ADX",
        # debug/izleme:
        "ema_period_used": int(ema_period),
        "adx_period_used": int(adx_period),
        "bars": int(len(df)) if df is not None else 0,
    }

    if df is None or df.empty:
        return out

    # Kolon güvenliği
    for c in ("close", "high", "low"):
        if c not in df.columns:
            return out

    # En az 3 bar olmadan anlamlı bir şey diyemeyiz
    if len(df) < 3:
        return out

    try:
        close = df["close"].astype(float).values
        high = df["high"].astype(float).values
        low = df["low"].astype(float).values

        last_close = float(df["close"].iloc[-1])
        out["close"] = last_close

        # -------------------------
        # ✅ Period adaptasyonu
        # -------------------------
        # EMA için pratik alt limit: 20
        ema_p = int(ema_period)
        if len(df) < ema_p + 1:
            ema_p = max(20, min(ema_p, len(df) - 1))
        out["ema_period_used"] = int(ema_p)

        # ADX için pratik alt limit: 7
        adx_p = int(adx_period)
        if len(df) < adx_p + 2:
            adx_p = max(7, min(adx_p, len(df) - 2))
        out["adx_period_used"] = int(adx_p)

        # -------------------------
        # EMA
        # -------------------------
        ema_arr = ta.EMA(close, timeperiod=int(ema_p))
        ema_s = pd.Series(ema_arr).dropna()
        if not ema_s.empty:
            ema_val = float(ema_s.iloc[-1])
            out["ema200"] = ema_val  # alan adı geriye uyumluluk için aynı kaldı

            tol = float(ema_tolerance)
            if last_close > ema_val * (1.0 + tol):
                out["raw_trend"] = "📈 Yükseliş"
            elif last_close < ema_val * (1.0 - tol):
                out["raw_trend"] = "📉 Düşüş"
            else:
                out["raw_trend"] = "⚪️ Nötr"

        # -------------------------
        # ADX
        # -------------------------
        adx_arr = ta.ADX(high, low, close, timeperiod=int(adx_p))
        adx_s = pd.Series(adx_arr).dropna()
        if not adx_s.empty:
            out["adx"] = float(adx_s.iloc[-1])

        # -------------------------
        # Net trend (ADX gate)
        # -------------------------
        if out["raw_trend"].startswith("📈") or out["raw_trend"].startswith("📉"):
            if out["adx"] > 0 and out["adx"] < float(adx_min_trend):
                out["net_trend"] = "⚪️ Nötr"
            else:
                out["net_trend"] = out["raw_trend"]
        else:
            out["net_trend"] = out["raw_trend"]

        return out

    except Exception:
        return out


def _get_bb_edge_cfg(tf: str) -> dict:
    tf = str(tf or "15m").strip()
    prof = ConfigService.tf_profile(tf, {}) or {}
    cf = (prof.get("common_filters") or {}) if isinstance(prof, dict) else {}
    bb = (cf.get("bb_edge_filter") or {}) if isinstance(cf, dict) else {}
    if not isinstance(bb, dict):
        bb = {}

    ov = bb.get("override") if isinstance(bb.get("override"), dict) else {}

    return {
        "enabled": bool(bb.get("enabled", False)),
        "bb_width_max": float(bb.get("bb_width_max", 0.055)),
        "long_percent_b_max": float(bb.get("long_percent_b_max", 0.95)),
        "short_percent_b_min": float(bb.get("short_percent_b_min", 0.05)),
        # override (opsiyonel)
        "override": {
            "adx_min": float(ov.get("adx_min", 28.0)),
            "vr_min": float(ov.get("vr_min", 1.2)),
            "require_trend_hint": bool(ov.get("require_trend_hint", True)),
        }
    }


def _apply_bb_edge_filter(
        df_closed: pd.DataFrame,
        direction: str,
        meta: dict,
        timeframe: str
) -> tuple[bool, str]:
    """
    BB Edge Filter (Stop azaltma / late-entry engelleme)
    Mantık:
      - Filtre sadece bant DAR ise aktif (width <= bb_width_max).
      - Edge bölgesinde:
          * LONG + üst banda dayalıysa => late-entry riski => default BLOCK
          * SHORT + alt banda dayalıysa => late-entry riski => default BLOCK
        Ancak trend continuation güçlü ise override ile ALLOW.
      - Ters yön (üst bantta SHORT, alt bantta LONG) şimdilik konservatif BLOCK.

    Returns:
      (ok: bool, reason: str)
    """
    cfg = _get_bb_edge_cfg(timeframe)
    if not cfg.get("enabled", False):
        return True, "ok:disabled"

    if df_closed is None or df_closed.empty or len(df_closed) < 30:
        return True, "ok:insufficient_bars"

    direction = str(direction or "").upper().strip()
    if direction not in ("LONG", "SHORT"):
        return True, "ok:bad_direction"

    # --- Column guard ---
    if "close" not in df_closed.columns:
        return True, "ok:no_close"

    # --- BBANDS ---
    try:
        close = df_closed["close"].astype(float).values
        up, mid, low = ta.BBANDS(
            close,
            timeperiod=20,
            nbdevup=2.0,
            nbdevdn=2.0,
            matype=ta.MA_Type.SMA
        )
    except Exception as e:
        return True, f"ok:bb_calc_err {type(e).__name__}"

    up_s = pd.Series(up).dropna()
    mid_s = pd.Series(mid).dropna()
    low_s = pd.Series(low).dropna()
    if up_s.empty or low_s.empty or mid_s.empty:
        return True, "ok:bb_nan"

    last_close = float(df_closed["close"].iloc[-1])
    bb_up = float(up_s.iloc[-1])
    bb_mid = float(mid_s.iloc[-1])
    bb_low = float(low_s.iloc[-1])

    denom = (bb_up - bb_low)

    # width = (upper-lower)/close
    width = 0.0
    if denom > 0 and last_close > 0:
        width = denom / max(1e-12, last_close)

    # %B = (close - lower) / (upper - lower)
    percent_b = 0.5
    if denom > 0:
        percent_b = (last_close - bb_low) / denom

    # meta’ya yaz (raporlama/debug için)
    try:
        if isinstance(meta, dict):
            meta["bb_width"] = float(width)
            meta["bb_percent_b"] = float(percent_b)
            meta["bb_upper"] = float(bb_up)
            meta["bb_mid"] = float(bb_mid)
            meta["bb_lower"] = float(bb_low)
    except Exception:
        pass

    # ------------------------------------------------------------
    # Helper: trend continuation override kontrolü
    # ------------------------------------------------------------
    def _trend_continuation_ok() -> tuple[bool, str]:
        try:
            ov = cfg.get("override") if isinstance(cfg.get("override"), dict) else {}
            adx_min = float(ov.get("adx_min", 28.0) or 28.0)
            vr_min = float(ov.get("vr_min", 1.2) or 1.2)
            require_hint = bool(ov.get("require_trend_hint", True))

            ts = meta.get("trend_snapshot") if isinstance(meta, dict) else None
            ts = ts if isinstance(ts, dict) else {}
            loc = ts.get("local") if isinstance(ts.get("local"), dict) else {}

            # local ADX
            try:
                local_adx = float(loc.get("adx") or 0.0)
            except Exception:
                local_adx = 0.0

            # volume ratio (AI/strategy scan meta’larında var)
            vr = 0.0
            try:
                vr = float(meta.get("volume_ratio") or meta.get("vr") or 0.0)
            except Exception:
                vr = 0.0

            # trend hint string
            hint = str(loc.get("net_trend") or loc.get("raw_trend") or meta.get("local_trend") or "")
            hint_up = ("📈" in hint) or ("YÜKSEL" in hint.upper())
            hint_dn = ("📉" in hint) or ("DÜŞ" in hint.upper())

            ok_adx = (adx_min <= 0) or (local_adx >= adx_min)

            # vr yoksa (0/None) bloklama yapma; varsa threshold uygula
            ok_vr = (vr <= 0) or (vr >= vr_min)

            # mid konumu: trend devamı için küçük ama etkili bir check
            ok_mid = (last_close >= bb_mid) if direction == "LONG" else (last_close <= bb_mid)

            ok_hint = True
            if require_hint:
                ok_hint = hint_up if direction == "LONG" else hint_dn

            ok = bool(ok_adx and ok_vr and ok_mid and ok_hint)
            why = f"adx={local_adx:.2f}/{adx_min:.2f} vr={vr:.2f}/{vr_min:.2f} mid_ok={ok_mid} hint={hint!r}"
            return ok, why
        except Exception as e:
            return False, f"override_err:{type(e).__name__}"

    # ------------------------------------------------------------
    # Ana karar: filtre sadece bant dar ise devrede
    # ------------------------------------------------------------
    bb_width_max = float(cfg.get("bb_width_max", 0.055))
    if width > bb_width_max:
        return True, "ok:width_wide_filter_inactive"

    long_edge = float(cfg.get("long_percent_b_max", 0.95))
    short_edge = float(cfg.get("short_percent_b_min", 0.05))

    is_upper_edge = (percent_b >= long_edge)
    is_lower_edge = (percent_b <= short_edge)

    if not (is_upper_edge or is_lower_edge):
        return True, "ok:not_edge"

    # 1) Late-entry (kovalama) – default BLOCK, trend continuation varsa ALLOW
    late_entry = (direction == "LONG" and is_upper_edge) or (direction == "SHORT" and is_lower_edge)
    if late_entry:
        ok_cont, why = _trend_continuation_ok()
        if ok_cont:
            try:
                meta["bb_edge_overridden"] = True
                meta["bb_edge_override_reason"] = f"TREND_CONTINUATION {why}"
            except Exception:
                pass
            return True, "ok:override_trend_continuation"

        return False, f"BB_EDGE_LATE_{direction} %B={percent_b:.3f} width={width:.4f} mid={bb_mid:.6f}"

    # 2) Ters yön (mean-reversion) – şimdilik konservatif BLOCK
    #    (Üst bantta SHORT / alt bantta LONG)
    return False, f"BB_EDGE_CONTRA_{direction} %B={percent_b:.3f} width={width:.4f} mid={bb_mid:.6f}"



async def monitor_symbols(cls, context: CallbackContext, price_map: dict | None = None):
    """
    Sembol izleme - Strateji bazlı doğru strategy_hint kullanımı.
    GÜNCELLEME:
      - Derivative market_type (swap/perp/linear_swap) desteği
      - 'converted' statüsü yalnızca OPEN başarıyla tamamlanınca set edilir
      - OPEN/RENDER fail olursa alarm statüsü geri alınır (kilitlenme olmaz)
      - Bar-guard ve OHLCV cache korunur
    """
    _init_caches_if_needed(cls)

    # -----------------------------
    # Local helpers (self-contained)
    # -----------------------------
    _settings_cache: dict[tuple[int, str], dict] = {}

    def _get_settings_cached(uid: int, ex_name: str) -> dict:
        key = (int(uid), str(ex_name or "").strip().lower())
        if key in _settings_cache:
            return _settings_cache[key]
        try:
            s = get_user_settings(key[0], key[1])
            _settings_cache[key] = s if isinstance(s, dict) else {}
        except Exception:
            _settings_cache[key] = {}
        return _settings_cache[key]

    def _norm_mtype_guard(x: str | None) -> str:
        s = str(x or "").strip().lower()
        if s in ("futures", "future"):
            return "future"
        if s in ("perp", "perpetual", "swap", "linear_swap", "inverse_swap"):
            return "swap"
        if s == "spot":
            return "spot"
        return s or "unknown_type"

    def _is_derivative_market(market_type: str | None) -> bool:
        mt = str(market_type or "").strip().lower()
        return mt in {
            "futures", "future",
            "swap", "linear_swap", "inverse_swap",
            "perp", "perpetual"
        }

    def _parse_iso(s):
        try:
            if not s:
                return None
            dt = datetime.fromisoformat(str(s))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None

    def _alarm_sort_key(a: dict):
        # converted en sona
        status = str(a.get("status") or "").lower()
        is_converted = (status == "converted")

        created_at = _parse_iso(a.get("created_at"))
        last_attempt = _parse_iso(a.get("last_attempt_ts"))

        created_ts = created_at.timestamp() if created_at else 0.0
        last_attempt_ts = last_attempt.timestamp() if last_attempt else 0.0
        return (is_converted, created_ts, last_attempt_ts)

    def _save_alarms_state_safe():
        try:
            alarm_persistence.save_active_alarms_from_cls(cls)
        except Exception:
            pass

    def _set_alarm_status(alarm: dict, status: str, err: str | None = None):
        if not isinstance(alarm, dict):
            return
        alarm["status"] = status
        alarm["last_attempt_ts"] = datetime.now(timezone.utc).isoformat()
        if err:
            alarm["last_error"] = str(err)[:500]
        _save_alarms_state_safe()

    def _rollback_open(alarm: dict, err: str | Exception | None = None):
        # tekrar denenecek hale getir
        _set_alarm_status(alarm, "", err=str(err) if err else None)

    def _shrink_df_for_cache(df: pd.DataFrame, tf: str) -> pd.DataFrame:
        try:
            tf = str(tf)
            if tf in ("1m", "5m", "15m", "30m", "1h"):
                return df.tail(600).copy()
            if tf in ("4h",):
                return df.tail(500).copy()
            if tf in ("1d",):
                return df.tail(450).copy()
            return df.tail(600).copy()
        except Exception:
            return df

    async def _get_df_cached(sym: str, tf: str, ttl_sec: float):
        ex = getattr(cls, "exchange", None)
        ex_id = str(getattr(ex, "id", "") or getattr(ex, "name", "") or "")
        try:
            dt = (ex.options.get("defaultType") if (ex is not None and getattr(ex, "options", None)) else None)
        except Exception:
            dt = None
        mtype = _norm_mtype_guard(dt)

        sym_key = cls.to_ccxt_symbol(sym, True) or str(sym).strip()
        k = (ex_id or "unknown_ex", mtype, sym_key, tf)

        df_cached = cls._ohlcv_cache_obj.get(k, ttl_sec)
        if df_cached is not None:
            return df_cached

        df_new = await cls.fetch_ohlcv_with_retry(sym_key, tf, max_retries=2, timeout=20, context=context)
        if df_new is not None and not df_new.empty:
            cls._ohlcv_cache_obj.set(k, _shrink_df_for_cache(df_new, tf))
        return df_new

    # alarm alanlarını güvenli normalize et (akışı bozmadan)
    def _safe_alarm_meta(alarm: dict) -> dict:
        m = alarm.get("meta")
        return m if isinstance(m, dict) else {}

    def _pick_alarm_symbol(alarm: dict) -> str:
        # UI/State bazen core_symbol tutuyor, bazen symbol
        return str(alarm.get("symbol") or alarm.get("core_symbol") or "").strip()

    def _pick_alarm_timeframe(alarm: dict) -> str:
        return str(alarm.get("timeframe") or "15m").strip()

    def _pick_alarm_source(alarm: dict) -> str:
        meta = _safe_alarm_meta(alarm)
        s = meta.get("source") or alarm.get("source") or "manual"
        s = str(s or "").strip()
        return s if s in ("ai_scan", "strategy_scan") else "manual"

    def _pick_alarm_strategy_id(alarm: dict) -> str:
        sid = alarm.get("strategy_hint") or alarm.get("strategy_id") or "v1"
        sid = str(sid or "").strip().lower()
        if sid not in ("v1", "v2"):
            # bilmeyen değerleri v1'e düşürme -> güvenli default
            sid = "v1"
        return sid

    # -----------------------------
    # Main
    # -----------------------------
    try:
        logging.info("🔍 Sembol monitoring başlatıldı...")
        # 1) Exchange kontrol / init
        if not cls.exchange:
            user_id = context.user_data.get("user_id", ADMIN_USER_ID)
            exchange_name = context.user_data.get("selected_exchange", "mexc")
            api_info = get_api_key(user_id, exchange_name)

            api_key = api_info.get("api_key") if api_info else context.user_data.get("api_key", "")
            secret_key = api_info.get("secret_key") if api_info else context.user_data.get("secret_key", "")
            passphrase = api_info.get("passphrase") if api_info else context.user_data.get("passphrase")

            await cls.initialize_exchange(
                user_id=user_id,
                exchange_name=exchange_name,
                api_key=api_key,
                secret_key=secret_key,
                passphrase=passphrase,
                context=context
            )

        # 2) Aktif sembol kontrol
        if not cls.active_symbols:
            active_trade_count = len([s for s in cls.active_signals if isinstance(s, dict) and s.get("active")])
            if active_trade_count > 0:
                logging.info(f"📊 Aktif Alarm Listesi boş. (Takip edilen aktif Sinyal: {active_trade_count})")
            else:
                logging.info("📊 İzlenecek sembol veya aktif işlem yok. (Bot boşta)")
            return

        logging.info(f"📊 {len(cls.active_symbols)} aktif sembol izleniyor...")
        # ✅ Self-healing: converted alarm ama aktif sinyal yoksa kilidi kaldır
        try:
            active_keys = set()
            for s in (cls.active_signals or []):
                if not isinstance(s, dict) or not s.get("active"):
                    continue
                k = (
                    cls.normalize_symbol(s.get("symbol") or s.get("core_symbol") or ""),
                    str(s.get("timeframe") or "").strip(),
                    str(s.get("strategy_id") or "").strip().lower(),
                )
                active_keys.add(k)

            unlocked = 0
            for a in (cls.active_symbols or []):
                if not isinstance(a, dict):
                    continue
                if str(a.get("status") or "").lower() != "converted":
                    continue

                k = (
                    cls.normalize_symbol(a.get("core_symbol") or a.get("symbol") or ""),
                    str(a.get("timeframe") or "").strip(),
                    str(a.get("strategy_id") or a.get("strategy_hint") or "").strip().lower(),
                )

                # Aktif sinyali yoksa -> converted kilidini kaldır
                if k not in active_keys:
                    a["status"] = ""
                    a["last_error"] = ""
                    a["last_attempt_ts"] = datetime.now(timezone.utc).isoformat()
                    unlocked += 1

            if unlocked:
                alarm_persistence.save_active_alarms_from_cls(cls)
                logging.info(f"[CONVERTED_UNLOCK] unlocked={unlocked}")
        except Exception as _unlock_err:
            logging.debug(f"[CONVERTED_UNLOCK_WARN] {_unlock_err}")

        # 3) Global rejim (BTC) - (legacy crash/check bilgisi)
        global_regime_info = {"trend": "NEUTRAL", "btc_price": 0.0, "btc_ema200": 0.0, "is_crash": False}
        try:
            btc_sym = (
                cls.to_ccxt_symbol("BTCUSDT", prefer_futures=True)
                or cls.to_ccxt_symbol("BTC/USDT", prefer_futures=True)
                or "BTC/USDT"
            )
            btc_df = await _get_df_cached(btc_sym, "15m", ttl_sec=25.0)
            if btc_df is not None and not btc_df.empty and len(btc_df) > 200:
                btc_df_closed = btc_df.iloc[:-1] if len(btc_df) >= 3 else btc_df
                btc_close = float(btc_df_closed["close"].iloc[-1])
                ema_arr = ta.EMA(btc_df_closed["close"].astype(float).values, timeperiod=200)
                ema_s = pd.Series(ema_arr).dropna()
                if ema_s.empty:
                    raise ValueError("BTC EMA200 hesaplanamadı (NaN)")
                btc_ema200 = float(ema_s.iloc[-1])

                piyasa_rejimi_dusus = btc_close < btc_ema200
                is_crash = btc_close < (btc_ema200 * 0.85)

                global_regime_info = {
                    "trend": "DOWN" if piyasa_rejimi_dusus else "UP",
                    "btc_price": btc_close,
                    "btc_ema200": btc_ema200,
                    "is_crash": is_crash
                }
                trend_icon = "📉" if piyasa_rejimi_dusus else "📈"
                logging.info(f"{trend_icon} Global Rejim: {global_regime_info['trend']} | BTC: {btc_close:.2f}")
        except Exception as e_btc:
            logging.warning(f"BTC Rejim Analizi Hatası (NEUTRAL): {e_btc}")

        # market regime (compat)
        try:
            if hasattr(cls, "_get_market_regime"):
                market_regime = await cls._get_market_regime(context)
            else:
                market_regime = await cls.get_market_regime(context)
        except Exception as e_mr:
            logging.warning(f"[MARKET_REGIME_WARN] {e_mr}")
            market_regime = str(getattr(cls, "_market_regime", "NEUTRAL"))

        # Dedup
        try:
            cls.deduplicate_active_symbols()
        except Exception as _d_err:
            logging.error(f"[DEDUP_CALL_ERR] {_d_err}")

        # 4) Alarm filtreleme
        alarms_in = [a for a in (cls.active_symbols or []) if isinstance(a, dict)]
        filtered: list[dict] = []
        filtered_detail: dict[str, str] = {}

        for a in alarms_in:
            sym_dbg = _pick_alarm_symbol(a) or "<?>"
            status_dbg = str(a.get("status") or "").lower().strip()
            mt_dbg = a.get("market_type")
            tf_dbg = _pick_alarm_timeframe(a)
            src_dbg = _pick_alarm_source(a)
            sid_dbg = _pick_alarm_strategy_id(a)

            if not _is_derivative_market(mt_dbg):
                filtered_detail[sym_dbg] = f"skip:not_derivative market_type={mt_dbg}"
                continue

            if status_dbg == "converted":
                filtered_detail[sym_dbg] = "skip:converted"
                continue

            mt_dbg = a.get("market_type")
            if (not mt_dbg) and src_dbg == "manual":
                mt_dbg = "swap"
                a["market_type"] = "swap"

            if sid_dbg not in ("v1", "v2"):
                filtered_detail[sym_dbg] = f"skip:bad_strategy_id({sid_dbg})"
                continue

            if not tf_dbg:
                filtered_detail[sym_dbg] = "skip:no_timeframe"
                continue

            filtered.append(a)

        alarms = filtered
        alarms.sort(key=_alarm_sort_key)

        logging.info(
            f"[ALARM_FILTERED] count={len(alarms)} symbols={[a.get('symbol') for a in alarms if isinstance(a, dict)]}"
        )
        if filtered_detail:
            logging.info(f"[ALARM_FILTERED_DETAIL] {filtered_detail}")

        logging.info(f"📊 {len(alarms)} aktif alarm sinyal için analiz edilecek (converted hariç).")

        # Tur sayaçları
        processed = 0
        skipped = 0
        errors = 0
        converted = 0

        skip_reasons = {
            "no_strategy_id": 0,
            "no_futures_market": 0,
            "no_ccxt_symbol": 0,
            "no_ohlcv": 0,
            "bar_guard_skip": 0,
            "no_signal": 0,
            "bad_entry": 0,
            "bad_stop": 0,
            "no_channels": 0,
            "open_fail": 0,
            "render_fail": 0,
        }
        skip_reasons_detail: dict[str, str] = {}

        # 5) Sembol döngüsü
        for alarm in alarms:
            processed += 1
            signal_data = None

            try:
                if not isinstance(alarm, dict):
                    skipped += 1
                    continue

                # --- normalize edilmiş çekimler (V1/V2, source, tf, symbol) ---
                symbol = _pick_alarm_symbol(alarm)
                timeframe = _pick_alarm_timeframe(alarm)
                source = _pick_alarm_source(alarm)
                strategy_id = _pick_alarm_strategy_id(alarm)
                user_id = alarm.get("user_id") or context.user_data.get("user_id") or ADMIN_USER_ID

                if not symbol:
                    skipped += 1
                    skip_reasons_detail["<?>"] = "skip:no_symbol"
                    continue

                if not strategy_id:
                    skip_reasons["no_strategy_id"] += 1
                    skipped += 1
                    skip_reasons_detail[symbol] = "skip:no_strategy_id"
                    logging.warning(f"[ALARM_SKIP_NO_STRAT] symbol={symbol} alarm strategy_hint yok – atlanıyor")
                    continue

                logging.info(f"🔍 {symbol} ({timeframe}) {source} {strategy_id} analiz ediliyor...")

                # Futures market check (ccxt sembol üzerinden)
                try:
                    norm_for_check = (
                        cls.to_ccxt_symbol(cls.normalize_symbol(symbol), True)
                        or cls.to_ccxt_symbol(symbol, True)
                        or symbol
                    )
                    if not cls.has_futures_market(norm_for_check):
                        skip_reasons["no_futures_market"] += 1
                        skipped += 1
                        skip_reasons_detail[symbol] = f"skip:no_futures_market norm={norm_for_check}"
                        logging.warning(f"[FUT_SKIP_NO_MARKET] {symbol} futures kontratı yok - atlanıyor")
                        continue
                except Exception as _se:
                    logging.error(f"[SIGNAL_SYM_NORM_ERR] {symbol} {_se}")

                # -----------------------------
                # Rejim meta (regime_meta) başlangıç
                # -----------------------------
                regime_meta: dict[str, Any] = {}
                try:
                    regime_meta["global_regime_1d"] = cls._market_regime
                except Exception:
                    regime_meta["global_regime_1d"] = str(getattr(cls, "_market_regime", "Yatay"))

                # BTC trend (TF) - legacy alanı koru
                btc_tf_trend = "NEUTRAL"
                try:
                    btc_sym_tf = cls.to_ccxt_symbol("BTCUSDT", prefer_futures=True) or "BTC/USDT"
                    df_btc_tf = await _get_df_cached(btc_sym_tf, timeframe, ttl_sec=25.0)
                    if df_btc_tf is not None and not df_btc_tf.empty and len(df_btc_tf) > 200:
                        btc_tf_src = df_btc_tf.iloc[:-1] if len(df_btc_tf) >= 3 else df_btc_tf
                        last_tf = float(btc_tf_src["close"].iloc[-1])
                        ema_tf_arr = ta.EMA(btc_tf_src["close"].astype(float).values, timeperiod=200)
                        ema_tf_s = pd.Series(ema_tf_arr).dropna()
                        ema_tf = float(ema_tf_s.iloc[-1]) if not ema_tf_s.empty else None
                        if ema_tf and last_tf:
                            if last_tf > ema_tf * 1.005:
                                btc_tf_trend = "UP"
                            elif last_tf < ema_tf * 0.995:
                                btc_tf_trend = "DOWN"
                except Exception as _btc_tr:
                    logging.debug(f"[REGIME_META_BTC_TF_WARN] {_btc_tr}")
                regime_meta["global_btc_trend_tf"] = btc_tf_trend

                # Coin trend 1d + tf (legacy alanları koru)
                coin_daily_trend = "NEUTRAL"
                coin_tf_trend = "NEUTRAL"
                coin_core = cls.normalize_symbol(symbol)
                coin_symbol_ccxt = cls.to_ccxt_symbol(coin_core, True) or cls.to_ccxt_symbol(symbol, True) or symbol

                try:
                    df_coin_1d = await _get_df_cached(coin_symbol_ccxt, "1d", ttl_sec=900.0)
                    if df_coin_1d is not None and not df_coin_1d.empty and len(df_coin_1d) > 200:
                        last_1d = float(df_coin_1d["close"].iloc[-1])
                        ema_1d_arr = ta.EMA(df_coin_1d["close"].astype(float).values, timeperiod=200)
                        ema_1d = float(pd.Series(ema_1d_arr).dropna().iloc[-1])
                        if last_1d > ema_1d * 1.01:
                            coin_daily_trend = "UP"
                        elif last_1d < ema_1d * 0.99:
                            coin_daily_trend = "DOWN"
                except Exception as _coin_tr:
                    logging.debug(f"[REGIME_META_COIN_1D_WARN] {_coin_tr}")

                # ccxt symbol
                ccxt_symbol_norm = (
                    cls.to_ccxt_symbol(cls.normalize_symbol(symbol), True)
                    or cls.to_ccxt_symbol(symbol, True)
                )
                try:
                    logging.info(
                        f"[SYM_CHAIN] alarm_symbol={symbol} "
                        f"core={cls.normalize_symbol(symbol)} "
                        f"ccxt_norm={ccxt_symbol_norm} "
                        f"ccxt_resolved={cls.to_ccxt_symbol(ccxt_symbol_norm, True)} "
                        f"display={cls.to_display_symbol(symbol)}"
                    )
                except Exception:
                    pass

                if not ccxt_symbol_norm:
                    skip_reasons["no_ccxt_symbol"] += 1
                    skipped += 1
                    skip_reasons_detail[symbol] = "skip:no_ccxt_symbol"
                    logging.warning(f"[SYM_SKIP_NO_CCXT] {symbol} ccxt sembol üretilemedi")
                    continue

                # OHLCV (closed candles)
                df = await _get_df_cached(ccxt_symbol_norm, timeframe, ttl_sec=25.0)
                df_closed = df.iloc[:-1].copy() if (df is not None and not df.empty and len(df) >= 3) else None

                # fallback: exchange defaultType tweak (legacy davranışını koruyor)
                if df_closed is None or df_closed.empty:
                    original_type = cls.exchange.options.get("defaultType", "spot") if hasattr(cls.exchange, "options") else "spot"
                    try:
                        if hasattr(cls.exchange, "options"):
                            cls.exchange.options["defaultType"] = "future"
                        df_retry = await _get_df_cached(ccxt_symbol_norm, timeframe, ttl_sec=10.0)
                    finally:
                        if hasattr(cls.exchange, "options"):
                            cls.exchange.options["defaultType"] = original_type

                    if df_retry is None or df_retry.empty or len(df_retry) < 3:
                        skip_reasons["no_ohlcv"] += 1
                        skipped += 1
                        skip_reasons_detail[symbol] = "skip:no_ohlcv(retry_failed)"
                        continue

                    df_closed = df_retry.iloc[:-1].copy()

                df_closed = _standardize_ohlcv_df(cls, df_closed)

                if df_closed is None or df_closed.empty:
                    skip_reasons["no_ohlcv"] += 1
                    skipped += 1
                    skip_reasons_detail[symbol] = "skip:no_ohlcv(standardize_failed)"
                    continue

                # -----------------------------
                # ✅ Trend Snapshot (GLOBAL + LOCAL)  (DOĞRU KONUM)
                # -----------------------------
                try:
                    ema_period = int(ConfigService.get("trend.ema_period", 200) or 200)
                    adx_period = int(ConfigService.get("trend.adx_period", 14) or 14)
                    ema_tolerance = float(ConfigService.get("trend.ema_tolerance", 0.005) or 0.005)
                    adx_min_trend = float(ConfigService.get("trend.adx_min_trend", 15.0) or 15.0)

                    global_tf = _map_global_tf(timeframe)
                    regime_meta["global_tf"] = global_tf

                    btc_snapshot = {
                        "symbol":"BTC/USDT",
                        "tf":global_tf,
                        "close":0.0,
                        "ema200":0.0,
                        "adx":0.0,
                        "raw_trend":"❓ Bilinmiyor",
                        "net_trend":"❓ Bilinmiyor",
                        "method":"EMA200(+tolerance)+ADX",
                        # ✅ debug alanları (logda bars=None görmeyelim)
                        "bars":None,
                        "ema_period_used":None,
                        "adx_period_used":None,
                    }

                    try:
                        # 1) Önce futures/swap BTC dene
                        btc_sym_fut = (
                                cls.to_ccxt_symbol("BTCUSDT", prefer_futures=True)
                                or cls.to_ccxt_symbol("BTC/USDT", prefer_futures=True)
                                or "BTC/USDT"
                        )

                        df_btc = await _get_df_cached(btc_sym_fut, global_tf, ttl_sec=25.0)

                        # 2) Gelmezse spot fallback dene (bazı borsalarda 4h swap OHLCV sorunlu olabiliyor)
                        if df_btc is None or df_btc.empty or len(df_btc) < 3:
                            btc_sym_spot = "BTC/USDT"
                            df_btc = await _get_df_cached(btc_sym_spot, global_tf, ttl_sec=25.0)

                        # 3) Hâlâ yoksa logla (DEBUG değil, INFO/WARNING)
                        if df_btc is None or df_btc.empty or len(df_btc) < 3:
                            try:
                                ln = 0 if (df_btc is None) else int(len(df_btc))
                            except Exception:
                                ln = -1
                            logging.warning(
                                f"[BTC_SNAPSHOT_NO_OHLCV] tf={global_tf} len={ln} "
                                f"sym_fut={btc_sym_fut}"
                            )
                        else:
                            df_btc_closed = df_btc.iloc[:-1].copy() if len(df_btc) >= 3 else df_btc.copy()
                            _std = _standardize_ohlcv_df(cls, df_btc_closed)
                            df_btc_closed = _std if _std is not None else df_btc_closed

                            btc_snapshot = _calc_trend_snapshot_from_df(
                                df=df_btc_closed,
                                sym="BTC/USDT",
                                tf=global_tf,
                                ema_period=ema_period,
                                adx_period=adx_period,
                                ema_tolerance=ema_tolerance,
                                adx_min_trend=adx_min_trend,
                            )

                    except Exception as _btc_snap_err:
                        logging.warning(f"[BTC_SNAPSHOT_ERR] tf={global_tf} err={_btc_snap_err}", exc_info=True)

                    local_snapshot = _calc_trend_snapshot_from_df(
                        df=df_closed,
                        sym=cls.to_display_symbol(symbol),
                        tf=timeframe,
                        ema_period=ema_period,
                        adx_period=adx_period,
                        ema_tolerance=ema_tolerance,
                        adx_min_trend=adx_min_trend,
                    )

                    regime_meta["trend_snapshot"] = {
                        "global": btc_snapshot,
                        "local": local_snapshot,
                        "params": {
                            "ema_period": ema_period,
                            "adx_period": adx_period,
                            "ema_tolerance": ema_tolerance,
                            "adx_min_trend": adx_min_trend,
                        }
                    }

                except Exception as _snap_err:
                    logging.debug(f"[TREND_SNAPSHOT_WARN] {symbol} tf={timeframe} err={_snap_err}")
                try:
                    g = (regime_meta.get("trend_snapshot") or {}).get("global") or {}
                    logging.info(
                        f"[TREND_SNAP_DBG] BTC tf={g.get('tf')} bars={g.get('bars')} ema={g.get('ema200')} adx={g.get('adx')}")
                except Exception:
                    pass

                # bar guard
                ex_id = str(getattr(cls.exchange, "id", None) or getattr(cls.exchange, "name", None) or "")
                try:
                    ex_default_type = cls.exchange.options.get("defaultType") if getattr(cls.exchange, "options", None) else None
                except Exception:
                    ex_default_type = None
                mtype_norm = _norm_mtype_guard(ex_default_type or alarm.get("market_type"))

                guard_key, closed_ts = _closed_bar_key_and_ts(cls,
                    df_closed,
                    ccxt_symbol=ccxt_symbol_norm,
                    timeframe=timeframe,
                    user_id=user_id,
                    exchange_id=ex_id,
                    market_type=mtype_norm,
                )

                if closed_ts is None:
                    skip_reasons["no_ohlcv"] += 1
                    skipped += 1
                    skip_reasons_detail[symbol] = "skip:no_closed_ts"
                    continue

                # tf trend (coin) from df_closed (legacy)
                try:
                    if len(df_closed) > 200:
                        last_tf_c = float(df_closed["close"].iloc[-1])
                        ema_tf_c_arr = ta.EMA(df_closed["close"].astype(float).values, timeperiod=200)
                        ema_tf_c = float(pd.Series(ema_tf_c_arr).dropna().iloc[-1])
                        if last_tf_c > ema_tf_c * 1.005:
                            coin_tf_trend = "UP"
                        elif last_tf_c < ema_tf_c * 0.995:
                            coin_tf_trend = "DOWN"
                except Exception:
                    pass

                regime_meta["local_coin_trend_1d"] = coin_daily_trend
                regime_meta["local_coin_trend_tf"] = coin_tf_trend

                # ✅ NEW: ConfigService'ten strateji paramlarını çekip Strategy instance'a ver
                try:
                    strat_params = ConfigService.get(f"strategy.{strategy_id.upper()}", {}) or {}
                    if not isinstance(strat_params, dict):
                        strat_params = {}
                except Exception:
                    strat_params = {}

                picked_strategy = SMRef.get(strategy_id, params=strat_params, scope_key=user_id,
                    recreate_on_param_change=True)
                if not picked_strategy:
                    skip_reasons["no_signal"] += 1
                    skipped += 1
                    skip_reasons_detail[symbol] = f"skip:no_strategy_instance({strategy_id})"
                    continue
                active_strat = picked_strategy

                # ✅ NEW: runtime context (market_regime + trend_snapshot)
                try:
                    if hasattr(active_strat, "set_runtime_context"):
                        active_strat.set_runtime_context({
                            "market_regime": str(market_regime),
                            "trend_snapshot": regime_meta.get("trend_snapshot"),
                        })
                except Exception:
                    pass

                # aktif sinyal var mı?
                existing_signal = next(
                    (s for s in (cls.active_signals or [])
                     if isinstance(s, dict)
                     and s.get("active")
                     and cls.normalize_symbol(s.get("symbol", "")) == cls.normalize_symbol(symbol)
                     and str(s.get("strategy_id") or "").strip().lower() == active_strat.id),
                    None
                )
                if existing_signal:
                    skipped += 1
                    skip_reasons_detail[symbol] = f"skip:existing_active_signal({active_strat.id})"
                    logging.info(f"⚠️ {symbol} ({active_strat.id}) için aktif sinyal var - geçiliyor")
                    continue

                # v1 param sync (mevcut davranış)
                if active_strat.id == "v1":
                    mt_global = (cls.strategy or {}).get("momentum_threshold")
                    if mt_global is not None:
                        if abs(active_strat.params.get("momentum_threshold", mt_global) - mt_global) > 1e-9:
                            active_strat.params["momentum_threshold"] = mt_global

                # v2 numeric cast (mevcut davranış)
                if active_strat.id == "v2":
                    for c in ["open", "high", "low", "close", "volume"]:
                        if c in df_closed.columns:
                            df_closed[c] = pd.to_numeric(df_closed[c], errors="coerce")
                        else:
                            df_closed[c] = df_closed["close"] if c != "volume" else 0.0

                # alarm last_attempt
                try:
                    alarm["last_attempt_ts"] = datetime.now(timezone.utc).isoformat()
                except Exception:
                    pass

                # analyze (TEK ÇAĞRI)
                if hasattr(active_strat, "analyze"):
                    try:
                        sig_raw = await active_strat.analyze(df_closed, symbol, global_regime=global_regime_info)
                    except Exception as e_analyze:
                        logging.error(f"Strateji Analyze Hatası ({symbol}): {e_analyze}", exc_info=True)
                        sig_raw = None
                else:
                    try:
                        sig_raw = active_strat.generate_signal(df_closed, market_regime=market_regime)
                    except Exception as e_gen:
                        logging.error(f"Strateji generate_signal Hatası ({symbol}): {e_gen}", exc_info=True)
                        sig_raw = None

                # Debug: ham çıktı tipi
                try:
                    logging.info(
                        f"[STRAT_RAW_TYPE] symbol={symbol} strat={active_strat.id} "
                        f"type={type(sig_raw)} value_preview={str(sig_raw)[:400]}"
                    )
                except Exception:
                    pass

                # Ortak yorumla
                is_signal, _direction_unused, reason, sig_payload = _interpret_strategy_output(cls, sig_raw)

                # NO_SIGNAL_DEBUG
                try:
                    logging.info(
                        f"[NO_SIGNAL_DEBUG] symbol={symbol} tf={timeframe} strat={active_strat.id} "
                        f"df_len={len(df_closed) if df_closed is not None else None} "
                        f"last_close={float(df_closed['close'].iloc[-1]) if df_closed is not None and not df_closed.empty else None} "
                        f"meta_source={source} alarm_id={alarm.get('alarm_id')} status={alarm.get('status')}"
                    )
                except Exception:
                    pass

                if not is_signal:
                    skip_reasons["no_signal"] += 1
                    skipped += 1
                    skip_reasons_detail[symbol] = f"skip:no_signal reason={reason or '-'}"
                    _log_no_signal_reason(cls, symbol, timeframe, active_strat.id, sig_payload, reason)
                    continue

                # ✅ HER DURUMDA normalize et
                sig_obj = _normalize_sig_obj(cls, sig_raw, df_closed=df_closed)

                if not sig_obj:
                    skip_reasons["no_signal"] += 1
                    skipped += 1
                    skip_reasons_detail[symbol] = "skip:no_signal(normalize_failed)"
                    continue

                signal_type = str(sig_obj.get("direction") or "").upper().strip()
                if signal_type not in ("LONG", "SHORT"):
                    skip_reasons["no_signal"] += 1
                    skipped += 1
                    skip_reasons_detail[symbol] = f"skip:bad_signal_type({signal_type})"
                    continue

                entry_price = float(sig_obj.get("entry") or float("nan"))
                stop_loss = float(sig_obj.get("stop") or float("nan"))
                targets = [t for t in (sig_obj.get("targets") or []) if isinstance(t, (int, float)) and t == t]

                # entry yoksa son kapanış
                if not isinstance(entry_price, (int, float)) or entry_price != entry_price:
                    try:
                        entry_price = float(df_closed["close"].iloc[-1])
                    except Exception:
                        entry_price = float("nan")

                # ATR fallback (stop veya targets yoksa)
                need_sl = (not isinstance(stop_loss, (int, float))) or (stop_loss != stop_loss)
                need_tg = (not targets)

                if need_sl or need_tg:
                    try:
                        atr_arr = ta.ATR(
                            df_closed["high"].astype(float).values,
                            df_closed["low"].astype(float).values,
                            df_closed["close"].astype(float).values,
                            timeperiod=14
                        )
                        atr_s = pd.Series(atr_arr).dropna()
                        atr = float(atr_s.iloc[-1]) if not atr_s.empty else 0.0
                        sl_mul = 2.0

                        if atr > 0 and entry_price == entry_price:
                            if signal_type == "LONG":
                                if need_sl:
                                    stop_loss = entry_price - (atr * sl_mul)
                                if need_tg:
                                    targets = [entry_price + (atr * m) for m in [1.5, 2.5, 3.5, 5.0, 8.0]]
                            else:
                                if need_sl:
                                    stop_loss = entry_price + (atr * sl_mul)
                                if need_tg:
                                    targets = [entry_price - (atr * m) for m in [1.5, 2.5, 3.5, 5.0, 8.0]]
                    except Exception:
                        pass

                # ticker hint
                current_price = None
                if isinstance(price_map, dict) and price_map:
                    t = price_map.get(ccxt_symbol_norm)
                    if not t and isinstance(ccxt_symbol_norm, str) and ':' in ccxt_symbol_norm:
                        t = price_map.get(ccxt_symbol_norm.split(':', 1)[0])
                    if not t:
                        core = cls.normalize_symbol(symbol)
                        if core and core.endswith("USDT"):
                            t = price_map.get(core[:-4] + "/USDT")
                    if not t:
                        t = price_map.get(symbol)

                    if isinstance(t, dict):
                        current_price = t.get("last") or t.get("close") or (t.get("info") or {}).get("lastPrice")
                    elif isinstance(t, (int, float)):
                        current_price = t

                # execution hint meta
                try:
                    if current_price and isinstance(current_price, (int, float)) and current_price > 0:
                        if not isinstance(sig_obj.get("meta"), dict):
                            sig_obj["meta"] = {}
                        sig_obj["meta"]["execution_price_hint"] = float(current_price)
                except Exception:
                    pass

                # entry/stop validasyon
                if not isinstance(entry_price, (int, float)) or entry_price != entry_price:
                    skip_reasons["bad_entry"] += 1
                    skipped += 1
                    skip_reasons_detail[symbol] = "skip:bad_entry"
                    logging.warning(f"[SIG_SKIP_BAD_ENTRY] {symbol} entry invalid")
                    continue

                if not isinstance(stop_loss, (int, float)) or stop_loss != stop_loss:
                    skip_reasons["bad_stop"] += 1
                    skipped += 1
                    skip_reasons_detail[symbol] = "skip:bad_stop"
                    logging.warning(f"[SIG_SKIP_BAD_STOP] {symbol} stop invalid")
                    continue

                # meta merge
                meta = sig_obj.get("meta", {})
                if not isinstance(meta, dict):
                    meta = {}
                if regime_meta:
                    meta.update(regime_meta)
                # ✅ SNAPSHOT: işlem anındaki lot(leverage) -> meta'ya kalıcı yaz
                try:
                    # exchange adı: önce context, yoksa ccxt exchange id
                    ex_name = (
                        str(context.user_data.get("selected_exchange") or context.user_data.get("exchange") or "").strip().lower()
                        or str(getattr(cls.exchange, "id", "") or "").strip().lower()
                        or "mexc"
                    )

                    uid_int = int(user_id)

                    # yalnızca daha önce yazılmadıysa set et (geçmişi bozmayalım)
                    if meta.get("entry_amount") is None and meta.get("margin_usdt") is None and meta.get("margin_used_usdt") is None:
                        st = _get_settings_cached(uid_int, ex_name)
                        lot_val = st.get("lot")
                        try:
                            lot_f = float(lot_val)
                        except Exception:
                            lot_f = 0.0
                        if lot_f > 0:
                            meta["entry_amount"] = lot_f           # ✅ raporun kullanacağı ana alan
                            meta["margin_usdt"] = lot_f            # opsiyonel alias
                            meta["margin_used_usdt"] = lot_f       # opsiyonel alias

                    if meta.get("leverage") is None and meta.get("leverage_used") is None:
                        st = _get_settings_cached(uid_int, ex_name)
                        lev_val = st.get("leverage")
                        try:
                            lev_f = float(lev_val)
                        except Exception:
                            lev_f = 0.0
                        if lev_f > 0:
                            meta["leverage"] = lev_f               # ✅ rapor için
                            meta["leverage_used"] = lev_f          # opsiyonel alias

                    # exchange'i de meta'ya yazmak iyi olur (raporda DB eşleştirme/debug)
                    if not meta.get("exchange"):
                        meta["exchange"] = ex_name

                except Exception as _lot_snap_err:
                    logging.debug(f"[LOT_SNAPSHOT_WARN] {symbol} tf={timeframe} err={_lot_snap_err}")

                # --- RR GATE (NEW) ---
                try:
                    if not targets:
                        raise ValueError("no_targets_for_rr")

                    rr1 = _rr(float(entry_price), float(stop_loss), float(targets[0]), str(signal_type))

                    min_rr_map = ConfigService.get("risk.min_rr_first_target_by_strategy", {}) or {}
                    if not isinstance(min_rr_map, dict):
                        min_rr_map = {}
                    min_rr1 = float(min_rr_map.get(str(strategy_id).lower(),
                        ConfigService.get("risk.min_rr_first_target", 0.6)) or 0.6)

                    meta["rr1"] = float(rr1)
                    meta["min_rr1"] = float(min_rr1)

                    if rr1 < min_rr1:
                        rr_mode = str(ConfigService.get("risk.rr_gate_mode", "REBUILD_TARGETS") or "REBUILD_TARGETS").upper()
                        meta["rr_gate_mode"] = rr_mode

                        if rr_mode == "REJECT":
                            skip_reasons["no_signal"] += 1
                            skipped += 1
                            skip_reasons_detail[symbol] = f"skip:rr_gate rr1={rr1:.3f} < {min_rr1:.3f}"
                            logging.info(f"[RR_REJECT] {symbol} tf={timeframe} rr1={rr1:.3f} min={min_rr1:.3f}")
                            continue

                        atr = 0.0
                        try:
                            atr_arr = ta.ATR(
                                df_closed["high"].astype(float).values,
                                df_closed["low"].astype(float).values,
                                df_closed["close"].astype(float).values,
                                timeperiod=14
                            )
                            atr_s = pd.Series(atr_arr).dropna()
                            atr = float(atr_s.iloc[-1]) if not atr_s.empty else 0.0
                        except Exception:
                            atr = 0.0

                        if atr > 0:
                            risk = abs(float(entry_price) - float(stop_loss))
                            rr_targets = [1.0, 1.5, 2.0, 3.0, 4.0]

                            new_targets = []
                            for r in rr_targets:
                                if str(signal_type).upper() == "SHORT":
                                    new_targets.append(float(entry_price) - (risk * r))
                                else:
                                    new_targets.append(float(entry_price) + (risk * r))

                            targets = new_targets
                            meta["rr_rebuilt"] = True
                            meta["rr_rebuilt_from"] = "risk_multiple"
                            meta["rr_rebuilt_risk"] = float(risk)
                            logging.info(f"[RR_REBUILD] {symbol} tf={timeframe} rr1={rr1:.3f} -> targets rebuilt (risk-multiple)")
                        else:
                            skip_reasons["no_signal"] += 1
                            skipped += 1
                            skip_reasons_detail[symbol] = f"skip:rr_gate_no_atr rr1={rr1:.3f}"
                            logging.info(f"[RR_REJECT_NO_ATR] {symbol} tf={timeframe} rr1={rr1:.3f}")
                            continue

                except Exception as _rr_err:
                    try:
                        meta["rr_gate_error"] = str(_rr_err)[:200]
                    except Exception:
                        pass
                # --- RR GATE (END) ---

                # --- ORDERBOOK CONFIRM (NEW) ---
                try:
                    base_score = 0.0
                    try:
                        base_score = float(meta.get("score") or sig_obj.get("score") or 0.0)
                    except Exception:
                        base_score = 0.0

                    ob_ret = await cls._confirm_signal_with_orderbook(
                        ccxt_symbol=ccxt_symbol_norm,
                        direction=str(signal_type),
                        base_score=base_score,
                        meta=meta,
                        timeframe=timeframe
                    )

                    ok_ob = True
                    meta2 = meta

                    if isinstance(ob_ret, tuple):
                        if len(ob_ret) >= 2:
                            ok_ob, meta2 = ob_ret[0], ob_ret[1]
                        elif len(ob_ret) == 1:
                            ok_ob = bool(ob_ret[0])
                    elif isinstance(ob_ret, dict):
                        ok_ob = bool(ob_ret.get("ok", True))
                        meta2 = ob_ret.get("meta", meta)

                    if isinstance(meta2, dict):
                        meta = meta2

                    if not ok_ob:
                        skip_reasons["no_signal"] += 1
                        skipped += 1
                        skip_reasons_detail[symbol] = "skip:orderbook_confirm_failed(require_pass=true)"
                        obm = (meta.get("orderbook") or {}) if isinstance(meta, dict) else {}
                        obf = (obm.get("features") or {}) if isinstance(obm, dict) else {}
                        reasons = obm.get("reasons") or []
                        logging.info(
                            f"[ORDERBOOK_REJECT] symbol={symbol} ccxt={ccxt_symbol_norm} "
                            f"reasons={reasons} spread_bps={obf.get('spread_bps')} "
                            f"liq_usd={obf.get('top_liquidity_usd')} imb={obf.get('imbalance')}"
                        )
                        continue

                except Exception as ob_err:
                    logging.warning(f"[ORDERBOOK_CONFIRM_WARN] symbol={symbol} err={ob_err}", exc_info=True)

                # ORDERBOOK META SUMMARY
                try:
                    ob = (meta.get("orderbook") or {}) if isinstance(meta, dict) else {}
                    obf = (ob.get("features") or {}) if isinstance(ob, dict) else {}
                    meta["ob_passed"] = bool(ob.get("passed", False))
                    meta["ob_spread_bps"] = float(obf.get("spread_bps") or 0.0)
                    meta["ob_imbalance"] = float(obf.get("imbalance") or 0.5)
                    meta["ob_top_liquidity_usd"] = float(obf.get("top_liquidity_usd") or 0.0)
                    meta["ob_score_ob"] = float(ob.get("score_ob") or 0.0)
                except Exception:
                    pass

                # --- BB EDGE FILTER (NEW) ---
                try:
                    ok_bb, bb_reason = _apply_bb_edge_filter(df_closed, signal_type, meta, timeframe)
                    meta["bb_edge_ok"] = bool(ok_bb)
                    meta["bb_edge_reason"] = str(bb_reason)
                    if not ok_bb:
                        skip_reasons["no_signal"] += 1
                        skipped += 1
                        skip_reasons_detail[symbol] = f"skip:bb_edge {bb_reason}"
                        logging.info(f"[BB_EDGE_BLOCKED] symbol={symbol} tf={timeframe} dir={signal_type} {bb_reason}")
                        continue
                except Exception as _bb_err:
                    logging.warning(f"[BB_EDGE_WARN] symbol={symbol} err={_bb_err}", exc_info=True)
                # --- BB EDGE FILTER END ---

                # --- ENTRY GATES (NEW: STOP azaltma turnikesi) ---
                try:
                    ok_gate, gate_reason = _apply_entry_gates(
                        symbol=symbol,
                        timeframe=timeframe,
                        strategy_id=strategy_id,
                        direction=signal_type,
                        entry_price=float(entry_price),
                        stop_loss=float(stop_loss),
                        meta=meta
                    )
                    meta["entry_gate_ok"] = bool(ok_gate)
                    meta["entry_gate_reason"] = str(gate_reason)

                    if not ok_gate:
                        skip_reasons["no_signal"] += 1
                        skipped += 1
                        skip_reasons_detail[symbol] = f"skip:entry_gate {gate_reason}"

                        if _get_entry_gates_cfg(timeframe).get("log_blocked", True):
                            logging.info(
                                f"[ENTRY_BLOCKED] symbol={symbol} tf={timeframe} strat={strategy_id} "
                                f"dir={signal_type} reason={gate_reason} "
                                f"adx={((meta.get('trend_snapshot') or {}).get('local') or {}).get('adx')} "
                                f"local_net={((meta.get('trend_snapshot') or {}).get('local') or {}).get('net_trend')} "
                                f"global={(meta.get('global_regime') or meta.get('global_regime_1d'))}"
                            )
                        continue

                except Exception as _eg_err:
                    logging.warning(f"[ENTRY_GATE_WARN] symbol={symbol} err={_eg_err}", exc_info=True)
                # --- ENTRY GATES END ---

                # Parent alarm find
                parent_alarm = next(
                    (a for a in (cls.active_symbols or [])
                     if isinstance(a, dict)
                     and cls.normalize_symbol(a.get("symbol") or a.get("core_symbol") or "") == cls.normalize_symbol(symbol)
                     and str(a.get("timeframe") or "15m").strip() == timeframe
                     and str((a.get("strategy_hint") or a.get("strategy_id") or "v1")).strip().lower() == active_strat.id),
                    None
                )

                # IDs
                ALARM_COUNTER_PATH = os.path.join("alarm_raporlari", "alarm_counter.json")
                SIGNAL_SEQ_PATH = os.path.join("alarm_raporlari", "signal_seq_by_alarm.json")

                alarm_id = parent_alarm.get("alarm_id") if (parent_alarm and parent_alarm.get("alarm_id")) else None
                if not alarm_id:
                    alarm_id = await alarm_persistence.next_alarm_id(counter_path=ALARM_COUNTER_PATH)

                # alarm_id collision guard
                for s in (cls.active_signals or []):
                    if isinstance(s, dict) and s.get("alarm_id") == alarm_id and s.get("symbol") != symbol:
                        logging.error(f"[ALARM_ID_COLLISION] {alarm_id} old_sym={s.get('symbol')} new_sym={symbol} -> regen")
                        alarm_id = await alarm_persistence.next_alarm_id(counter_path=ALARM_COUNTER_PATH)
                        break

                signal_id = await alarm_persistence.next_signal_id(alarm_id, seq_path=SIGNAL_SEQ_PATH)

                existing_sig_ids = {x.get("signal_id") for x in (cls.active_signals or []) if isinstance(x, dict)}
                if signal_id in existing_sig_ids:
                    logging.error(f"[SIGNAL_ID_COLLISION] {signal_id} -> regen")
                    signal_id = await alarm_persistence.next_signal_id(alarm_id, seq_path=SIGNAL_SEQ_PATH)
                # --- OPEN SANITY CHECK (anti-instant-stop) ---
                try:
                    cp = None
                    try:
                        cp = float(current_price) if current_price is not None else None
                    except Exception:
                        cp = None

                    ep = float(entry_price)
                    sl = float(stop_loss)

                    if cp and cp > 0 and ep > 0 and sl > 0:
                        if signal_type == "LONG" and cp <= sl:
                            skip_reasons["no_signal"] += 1
                            skipped += 1
                            skip_reasons_detail[symbol] = f"skip:open_sanity cp({cp})<=sl({sl})"
                            logging.info(f"[OPEN_SANITY_BLOCK] {symbol} LONG cp={cp} <= sl={sl} (entry={ep})")
                            continue

                        if signal_type == "SHORT" and cp >= sl:
                            skip_reasons["no_signal"] += 1
                            skipped += 1
                            skip_reasons_detail[symbol] = f"skip:open_sanity cp({cp})>=sl({sl})"
                            logging.info(f"[OPEN_SANITY_BLOCK] {symbol} SHORT cp={cp} >= sl={sl} (entry={ep})")
                            continue
                except Exception:
                    pass

                # Signal data (henüz active_signals'a eklemiyoruz!)
                signal_data = {
                    "alarm_id": alarm_id,
                    "signal_id": signal_id,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "signal_type": signal_type,
                    "entry_price": entry_price,
                    "stop_loss": stop_loss,
                    "targets": targets,
                    "targets_hit": [False] * len(targets),
                    "targets_hit_times": [None] * len(targets),
                    "stop_loss_hit": False,
                    "stop_time": None,
                    "active": True,
                    "signal_time": datetime.now(timezone.utc),
                    "message_ids": [],
                    "original_text": "",
                    "meta": meta,
                    "strategy_id": active_strat.id,
                    "strategy_class": active_strat.__class__.__name__,
                    "strategy_version": getattr(active_strat, "strategy_version_counter", None),
                    "segment_key": parent_alarm.get("segment_key") if parent_alarm else None,
                    "main_messages": [],
                    "user_id": user_id,
                    "activation":{
                        "armed":True,
                        "activated":False,
                        "activated_time":None,
                        "activation_price":None,
                        "rule":"TOUCH_ENTRY",
                        "tolerance_pct":float(ConfigService.get("execution.activation_tolerance_pct", 0.00) or 0.0),
                    },
                    "open_price_hint":(float(current_price) if current_price is not None else None),

                }
                # opsiyonel: top-level snapshot (rapor tarafı daha kolay okur)
                try:
                    if "margin_used_usdt" not in signal_data:
                        signal_data["margin_used_usdt"] = float(meta.get("entry_amount") or 0.0)
                    if "leverage_used" not in signal_data:
                        signal_data["leverage_used"] = float(meta.get("leverage") or 0.0)
                except Exception:
                    pass

                if parent_alarm and isinstance(parent_alarm.get("meta"), dict):
                    try:
                        for k, v in (parent_alarm.get("meta") or {}).items():
                            if k not in signal_data["meta"]:
                                signal_data["meta"][k] = v
                    except Exception:
                        pass

                # entry_index
                try:
                    entry_index = len(df_closed) - 1
                    signal_data["entry_index"] = entry_index
                except Exception:
                    pass

                # ✅ meta kesin dict olsun
                _meta = signal_data.get("meta")
                if not isinstance(_meta, dict):
                    _meta = {}
                    signal_data["meta"] = _meta

                _meta["market_regime"] = str(market_regime)
                _meta["global_regime"] = str((global_regime_info or {}).get("trend", "NEUTRAL"))

                # log open event
                cls.log_signal_event(
                    symbol, signal_type, entry_price, signal_data.get("meta", {}),
                    alarm_id=alarm_id, signal_id=signal_id
                )

                # metin
                cls.normalize_signal_dict(signal_data)
                detailed_message = await cls.build_signal_template_text(
                    signal_data,
                    context=context,
                    current_price=(float(current_price) if current_price is not None else entry_price)
                )

                signal_data["original_text_base"] = detailed_message
                signal_data["original_text"] = detailed_message

                # chart render (base chart)
                strategy_meta = {
                    "strategy_id": str(signal_data.get("strategy_id", "")),
                    "strategy_class": str(signal_data.get("strategy_class", "")),
                    "confidence_index": str((meta or {}).get("confidence_index", "")),
                }
                if isinstance(sig_obj.get("meta"), dict):
                    try:
                        strategy_meta.update(sig_obj["meta"])
                    except Exception:
                        pass

                ccfg = getattr(cls, "_chart_cfg", {})

                def _buf_size(b) -> int:
                    try:
                        if b is None:
                            return 0
                        if hasattr(b, "getbuffer"):
                            return int(b.getbuffer().nbytes)
                        if hasattr(b, "getvalue"):
                            return len(b.getvalue())
                        return 1
                    except Exception:
                        return 0

                # --- chart render (base chart) ---
                chart_buf = None
                try:
                    chart_buf = cls.chart_renderer.render(
                        symbol=cls.to_display_symbol(symbol),
                        df=df_closed,
                        signal_type=str(signal_type),
                        entry_price=entry_price,
                        targets=targets,
                        stop_loss=stop_loss,
                        patterns=[],
                        timeframe=timeframe,
                        strategy_meta=strategy_meta,
                        signal_time=signal_data.get("signal_time"),
                        alarm_id=alarm_id,
                        signal_id=signal_id,
                        width=ccfg.get("width", 1200),
                        height=ccfg.get("height", 720),
                    )
                    if isinstance(chart_buf, tuple) and chart_buf:
                        chart_buf = chart_buf[0]
                    elif isinstance(chart_buf, dict):
                        chart_buf = chart_buf.get("buf") or chart_buf.get("chart_buf") or chart_buf.get("image")

                except Exception as rerr:
                    logging.error(
                        f"[CHART_RENDER_EXC] symbol={symbol} tf={timeframe} signal_id={signal_id} err={rerr}",
                        exc_info=True
                    )
                    chart_buf = None

                base_size = _buf_size(chart_buf)
                min_used = False

                if base_size <= 0:
                    logging.error(
                        f"[CHART_RENDER_NONE] symbol={symbol} tf={timeframe} signal_id={signal_id} "
                        f"df_cols={list(df_closed.columns) if df_closed is not None else None} "
                        f"df_len={len(df_closed) if df_closed is not None else None} "
                        f"dtypes={(df_closed.dtypes.astype(str).to_dict() if df_closed is not None else None)}"
                    )

                    try:
                        chart_buf = cls.chart_renderer.render_minimal(
                            symbol=cls.to_display_symbol(symbol),
                            direction=str(signal_type).upper(),
                            entry_price=float(entry_price),
                            stop_loss=float(stop_loss),
                            targets=list(targets or [])[:5],
                            timeframe=timeframe,
                            width=int(ccfg.get("width", 1200)),
                            height=int(ConfigService.get("charts.dimensions.event_chart_height", 600) or 600),
                            df=df_closed
                        )
                        base_size = _buf_size(chart_buf)
                        min_used = True if base_size > 0 else False
                        logging.warning(
                            f"[CHART_MINIMAL_USED] symbol={symbol} tf={timeframe} signal_id={signal_id} size={base_size}"
                        )
                    except Exception as _mf:
                        logging.error(
                            f"[CHART_MINIMAL_FAIL] symbol={symbol} signal_id={signal_id} err={_mf}",
                            exc_info=True
                        )
                        chart_buf = None
                        base_size = 0

                if base_size <= 0:
                    _rollback_open(alarm, err="chart_render_failed")
                    continue

                if min_used and bool(ConfigService.get("charts.block_open_on_minimal", False)):
                    _rollback_open(alarm, err="base_chart_failed_minimal_generated")
                    continue
                else:
                    logging.info(
                        f"[CHART_RENDER_OK] symbol={symbol} tf={timeframe} signal_id={signal_id} size={base_size}"
                    )

                if chart_buf is not None and base_size <= 0:
                    _rollback_open(alarm, err="base_chart_failed_minimal_generated")
                    continue

                # raw bytes store (chart_buf varsa)
                _chart_buf_raw_bytes = None
                if chart_buf:
                    try:
                        chart_buf.seek(0)
                        _chart_buf_raw_bytes = chart_buf.read()
                        chart_buf = BytesIO(_chart_buf_raw_bytes)
                    except Exception as _raw_err:
                        logging.error(f"[OPEN_RAW_STORE_ERR] {symbol} {_raw_err}", exc_info=True)
                        _chart_buf_raw_bytes = None

                if _chart_buf_raw_bytes:
                    signal_data["chart_buf_raw"] = _chart_buf_raw_bytes

                # --- OPEN PHASE ---
                _set_alarm_status(alarm, "converting")

                target_channel_ids = await cls.resolve_target_channels(context, user_id=user_id)
                if not target_channel_ids:
                    skip_reasons["no_channels"] += 1
                    skipped += 1
                    skip_reasons_detail[symbol] = "skip:no_channels"
                    logging.warning(f"[OPEN_SKIP] Kullanıcı {user_id} için bildirim kanalı yok.")
                    _rollback_open(alarm, err="no_channels")
                    continue

                # render main with targets
                try:
                    signal_for_render = dict(signal_data)
                    signal_for_render["symbol"] = cls.to_display_symbol(signal_data.get("symbol"))

                    main_buf = await cls.chart_renderer.render_main_with_targets(
                        signal=signal_for_render,
                        current_price=entry_price,
                        user_id=user_id,
                        chart_buf=chart_buf
                    )

                except Exception as e_render:
                    skip_reasons["render_fail"] += 1
                    errors += 1
                    skip_reasons_detail[symbol] = f"err:render_fail {type(e_render).__name__}"
                    logging.exception(f"[RENDER_MAIN_FAIL] symbol={symbol} signal_id={signal_id} err={e_render}")
                    _rollback_open(alarm, err=e_render)
                    continue

                if not main_buf:
                    skip_reasons["render_fail"] += 1
                    skipped += 1
                    skip_reasons_detail[symbol] = "skip:render_main_none"
                    logging.error(f"[RENDER_MAIN_FAIL] symbol={symbol} signal_id={signal_id} main_buf=None")
                    _rollback_open(alarm, err="render_main returned None")
                    continue

                # ✅ BAR GUARD
                if not cls._bar_guard.should_process(guard_key, closed_ts):
                    skip_reasons["bar_guard_skip"] += 1
                    skipped += 1
                    skip_reasons_detail[symbol] = "skip:bar_guard_skip"
                    _rollback_open(alarm, err="bar_guard_skip")
                    continue

                success_count = 0
                from typing import Any, cast

                mm = signal_data.get("main_messages")
                if not isinstance(mm, list):
                    mm = []
                    signal_data["main_messages"] = mm
                mm_typed = cast(list[dict[str, Any]], mm)

                for channel_id in target_channel_ids:
                    try:
                        cid_raw = channel_id
                        if isinstance(cid_raw, dict):
                            cid_raw = cid_raw.get("channel_id") or cid_raw.get("id") or cid_raw.get("chat_id")

                        cid = int(cid_raw)

                        main_buf.seek(0)
                        photo_msg = await context.bot.send_photo(chat_id=cid, photo=main_buf)

                        mm_typed.append({
                            "channel_id": cid,
                            "message_id": int(getattr(photo_msg, "message_id", 0) or 0),
                        })
                        success_count += 1

                    except Exception as send_err:
                        logging.error(f"[MAIN_SEND_ERR] {channel_id} {send_err}")

                try:
                    if hasattr(main_buf, "close"):
                        main_buf.close()
                except Exception:
                    pass

                if success_count == 0:
                    skip_reasons["open_fail"] += 1
                    skipped += 1
                    skip_reasons_detail[symbol] = "skip:open_fail_all_channels"
                    logging.error(f"[OPEN_FAIL] {symbol} ana mesaj gönderilemedi")
                    _rollback_open(alarm, err="open_fail_all_channels")
                    continue

                # --- COMMIT ---
                if signal_data not in cls.active_signals:
                    cls.active_signals.append(signal_data)

                _set_alarm_status(alarm, "converted")
                converted += 1

                try:
                    cls.save_active_signals(force=True)
                except Exception:
                    pass

                logging.info(
                    f"[ALARM_CONVERTED] {symbol} ({active_strat.id}) sinyale dönüştü. sent={success_count}"
                )

                # trade executor forward
                try:
                    await cls.forward_signal_to_trade_executor(signal_data, context)
                except Exception as _te:
                    logging.error(f"[OPEN_TRADE_EXEC_FAIL] {symbol} {_te}", exc_info=True)

                # signal merkezi forward
                try:
                    await cls._forward_open_to_signal_merkezi(signal_data, context=context)
                except Exception as fwd_err:
                    logging.error(f"[OPEN_FWD_FAIL] {symbol} {fwd_err}", exc_info=True)

            except Exception as symbol_err:
                errors += 1
                cur_sym = (signal_data.get("symbol") if isinstance(signal_data, dict) else None) or _pick_alarm_symbol(alarm) or "UNKNOWN"
                logging.error(f"❌ {cur_sym} sinyal işleme hatası: {symbol_err}", exc_info=True)
                try:
                    _rollback_open(alarm, err=symbol_err)
                except Exception:
                    pass
                if isinstance(signal_data, dict) and signal_data in (cls.active_signals or []):
                    try:
                        cls.active_signals.remove(signal_data)
                    except Exception:
                        pass

            await asyncio.sleep(float(ConfigService.get("monitor_symbols.per_alarm_sleep_sec", 0.15) or 0.15))

        active_count = len([s for s in (cls.active_signals or []) if isinstance(s, dict) and s.get("active")])
        logging.info(
            "[MONITOR_DONE] ✅ Monitoring turu tamamlandı | "
            f"processed={processed} skipped={skipped} errors={errors} converted={converted} | "
            f"active_signals={active_count}"
        )

        top_reasons = sorted(skip_reasons.items(), key=lambda x: x[1], reverse=True)
        top_reasons = [f"{k}={v}" for k, v in top_reasons if v > 0][:6]
        if top_reasons:
            logging.info("[MONITOR_SKIPS] " + " | ".join(top_reasons))

        if skip_reasons_detail:
            logging.info(f"[MONITOR_SKIPS_DETAIL] {skip_reasons_detail}")

    except Exception as error:
        logging.error(f"❌ Symbol monitoring genel hatası: {error}", exc_info=True)

    finally:
        try:
            import gc
            gc.collect()
        except Exception:
            pass


def _standardize_ohlcv_df(cls, df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """
    OHLCV kolonlarını standardize eder ve mümkünse zaman bilgisini normalize eder.

    - open/high/low/close/volume zorunlu
    - timestamp opsiyonel (varsa numeric yapılır)
    - datetime opsiyonel (varsa korunur; timestamp üretmek için kullanılabilir)
    - mümkünse zaman sırasına göre sıralar (timestamp veya datetime veya index ile)
    """
    try:
        if df is None or getattr(df, "empty", True):
            return None

        df = df.copy()

        # yaygın alternatif kolon adları
        # NOT: 'datetime' -> 'timestamp' rename YAPMIYORUZ (datetime'ı korumak daha iyi)
        rename_map = {
            "time":"timestamp",
            "date":"timestamp",  # bazı kaynaklarda date numeric ts olabilir
            "ts":"timestamp",
            "o":"open",
            "h":"high",
            "l":"low",
            "c":"close",
            "v":"volume",
        }
        df.rename(columns={k:v for k, v in rename_map.items() if k in df.columns}, inplace=True)

        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(df.columns):
            logging.error(
                f"_standardize_ohlcv_df missing cols: {required - set(df.columns)} | cols={list(df.columns)}")
            return None

        # OHLCV numeric cast (sessizce)
        for c in ["open", "high", "low", "close", "volume"]:
            try:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            except Exception:
                pass

        # timestamp opsiyonel; varsa numeric hale getir
        if "timestamp" in df.columns:
            try:
                df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
            except Exception:
                pass
        # ✅ datetime kolonu yoksa timestamp'ten üret (chart renderer'lar genelde bunu sever)
        if "datetime" not in df.columns:
            if "timestamp" in df.columns and df["timestamp"].notna().any():
                try:
                    # timestamp ms mi s mi? heuristik:
                    # 1e11 üstü -> ms, 1e9 üstü -> s
                    ts_last = df["timestamp"].dropna().iloc[-1]
                    unit = "ms" if float(ts_last) >= 1e11 else ("s" if float(ts_last) >= 1e9 else None)
                    if unit:
                        df["datetime"] = pd.to_datetime(df["timestamp"], unit=unit, utc=True, errors="coerce")
                except Exception:
                    pass

        # datetime opsiyonel; varsa Timestamp'a çevir (utc)
        if "datetime" in df.columns:
            try:
                df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
            except Exception:
                pass

        # Zaman sıralaması: timestamp -> datetime -> index
        try:
            if "timestamp" in df.columns and df["timestamp"].notna().any():
                df = df.sort_values("timestamp", kind="stable")
            elif "datetime" in df.columns and df["datetime"].notna().any():
                df = df.sort_values("datetime", kind="stable")
            else:
                # index sortable mı dene
                df = df.sort_index()
        except Exception:
            pass

        return df

    except Exception as e:
        logging.error(f"_standardize_ohlcv_df err: {repr(e)}")
        return None


def _closed_bar_key_and_ts(
        cls,
        df_closed: pd.DataFrame,
        ccxt_symbol: str,
        timeframe: str,
        user_id: str | int | None = None,
        exchange_id: str | None = None,
        market_type: str | None = None,
):
    """
    Guard anahtarı:
        (exchange_id, market_type, user_id, ccxt_symbol, timeframe)

    closed_ts:
      - Tercih: df_closed['timestamp'].iloc[-1]
      - Alternatif: df_closed['datetime'].iloc[-1]
      - Fallback: df_closed.index[-1]

    closed_ts mümkünse epoch-ms (int).
    """

    def _norm_mtype(x: str | None) -> str:
        s = str(x or "").strip().lower()
        if s in ("futures", "future"):
            return "future"
        if s in ("perp", "perpetual", "swap"):
            return "swap"
        if s == "spot":
            return "spot"
        return s or "unknown_type"

    # --- exchange_id / market_type türet (verilmediyse) ---
    ex_id = str(exchange_id or "").strip()
    mtype = str(market_type or "").strip()

    try:
        ex = getattr(cls, "exchange", None)
        if not ex_id and ex is not None:
            ex_id = str(getattr(ex, "id", "") or getattr(ex, "name", "") or "").strip()

        if not mtype and ex is not None:
            opts = getattr(ex, "options", None)
            if isinstance(opts, dict):
                mtype = str(opts.get("defaultType") or "").strip()
    except Exception:
        pass

    if not ex_id:
        ex_id = "unknown_ex"
    mtype = _norm_mtype(mtype)

    uid = str(user_id) if user_id is not None else "unknown_user"
    sym = str(ccxt_symbol or "").strip()
    tf = str(timeframe or "").strip()

    key = (ex_id, mtype, uid, sym, tf)

    if df_closed is None or getattr(df_closed, "empty", True):
        return key, None

    def _to_epoch_ms(x):
        if x is None:
            return None

        # pandas NaN / NaT
        try:
            if pd.isna(x):
                return None
        except Exception:
            pass

        # numpy scalar -> python scalar
        try:
            if hasattr(x, "item") and callable(x.item):
                x = x.item()
        except Exception:
            pass

        # pandas Timestamp
        try:
            if isinstance(x, pd.Timestamp):
                if pd.isna(x):
                    return None
                return int(x.value // 1_000_000)  # ns -> ms
        except Exception:
            pass

        # python datetime
        try:
            if isinstance(x, datetime):
                if x.tzinfo is None:
                    x = x.replace(tzinfo=timezone.utc)
                return int(x.timestamp() * 1000)
        except Exception:
            pass

        # string
        if isinstance(x, str):
            xs = x.strip()
            if not xs:
                return None
            # numeric string
            try:
                x = float(xs)
            except Exception:
                # datetime string
                try:
                    dt = pd.to_datetime(xs, utc=True, errors="coerce")
                    if pd.isna(dt):
                        return None
                    return int(dt.value // 1_000_000)
                except Exception:
                    return None

        # numeric: s vs ms heuristiği
        try:
            if isinstance(x, (int, float)):
                if x != x:
                    return None
                v = float(x)
                if v <= 0:
                    return None
                if v >= 1e11:  # ms
                    return int(v)
                if v >= 1e9:  # s
                    return int(v * 1000)
                return None
        except Exception:
            return None

        return None

    # 1) timestamp kolonu
    if hasattr(df_closed, "columns") and ("timestamp" in df_closed.columns):
        try:
            ts_raw = df_closed["timestamp"].iloc[-1]
            ts_ms = _to_epoch_ms(ts_raw)
            if ts_ms is not None:
                return key, ts_ms
        except Exception:
            pass

    # 2) datetime kolonu
    if hasattr(df_closed, "columns") and ("datetime" in df_closed.columns):
        try:
            ts_raw = df_closed["datetime"].iloc[-1]
            ts_ms = _to_epoch_ms(ts_raw)
            if ts_ms is not None:
                return key, ts_ms
        except Exception:
            pass

    # 3) index fallback
    try:
        idx_raw = df_closed.index[-1]
        ts_ms = _to_epoch_ms(idx_raw)
        if ts_ms is not None:
            return key, ts_ms
    except Exception:
        pass

    return key, None


def _init_caches_if_needed(cls):
        from core.cache_utils import LRUCacheTTL, DedupGuardTTL

        if not hasattr(cls, "_ohlcv_cache_obj") or cls._ohlcv_cache_obj is None:
            cls._ohlcv_cache_obj = LRUCacheTTL(
                max_items=int(ConfigService.get("ohlcv_cache.max_items", 2000) or 2000),
                gc_every=int(ConfigService.get("ohlcv_cache.gc_every", 200) or 200),
                max_entry_age_sec=float(ConfigService.get("ohlcv_cache.max_entry_age_sec", 3600) or 3600),
            )

        if not hasattr(cls, "_trend_cache_obj") or cls._trend_cache_obj is None:
            cls._trend_cache_obj = LRUCacheTTL(
                max_items=int(ConfigService.get("trend_cache.max_items", 5000) or 5000),
                gc_every=int(ConfigService.get("trend_cache.gc_every", 200) or 200),
                max_entry_age_sec=float(ConfigService.get("trend_cache.ttl_sec", 120) or 120),
            )

        if not hasattr(cls, "_bar_guard") or cls._bar_guard is None:
            cls._bar_guard = DedupGuardTTL(
                ttl_sec=float(ConfigService.get("guard.ttl_sec", 21600) or 21600),
                max_items=int(ConfigService.get("guard.max_items", 20000) or 20000),
                gc_every=int(ConfigService.get("guard.gc_every", 500) or 500),
            )


def _interpret_strategy_output(cls, sig_raw):
    _ = cls
    """
    Dönüş:global_trend != "UP"
      (is_signal: bool, direction: str|None, reason: str|None, payload: dict)
    """
    try:
        if sig_raw is None:
            return False, None, "empty:none", {}

        if not isinstance(sig_raw, dict):
            return False, None, f"unsupported_type:{type(sig_raw).__name__}", {}

        if not sig_raw:
            return False, None, "empty:dict", {}

        # Legacy tolerans: {"signal": "LONG"} gibi
        sig_val = sig_raw.get("signal")
        if isinstance(sig_val, str):
            d = sig_val.upper().strip()
            if d in ("LONG", "SHORT"):
                return True, d, None, sig_raw

        if "signal" in sig_raw and not bool(sig_raw.get("signal")):
            reason = sig_raw.get("reason") or (sig_raw.get("meta", {}) or {}).get("reason")
            return False, None, str(reason) if reason else "no_signal:false_flag", sig_raw

        if bool(sig_raw.get("signal")) is True:
            direction = sig_raw.get("direction") or sig_raw.get("signal_type") or sig_raw.get("type")
            direction = str(direction or "").upper().strip()
            if direction in ("LONG", "SHORT"):
                return True, direction, None, sig_raw
            return False, None, "bad_direction", sig_raw

        direction = sig_raw.get("type") or sig_raw.get("direction") or sig_raw.get("signal_type")
        direction = str(direction or "").upper().strip()
        if direction in ("LONG", "SHORT"):
            return True, direction, None, sig_raw

        return False, None, sig_raw.get("reason") or "no_signal:unknown_shape", sig_raw

    except Exception as e:
        return False, None, f"interpret_err:{type(e).__name__}", {}


def _normalize_sig_obj(cls, sig_raw: Any, df_closed: pd.DataFrame) -> Optional[dict]:
    """
    Stratejilerden dönen farklı formatları TEK formata indirger.
    Dönen sig_obj formatı:
      {
        "direction": "LONG"/"SHORT",
        "entry": float,
        "stop": float,
        "targets": [float...],
        "meta": dict
      }
    """
    try:
        if sig_raw is None:
            return None

        # 1) Eğer zaten dict ise
        if isinstance(sig_raw, dict):
            # bazı stratejiler: {"signal": True, "direction": "LONG", ...}
            if not bool(sig_raw.get("signal", True)):
                return None

            direction = (sig_raw.get("direction") or sig_raw.get("signal_type") or sig_raw.get(
                "type") or "").upper().strip()
            if direction not in ("LONG", "SHORT"):
                # legacy tolerans: {"signal": "LONG"}
                s = sig_raw.get("signal")
                if isinstance(s, str) and s.upper().strip() in ("LONG", "SHORT"):
                    direction = s.upper().strip()

            if direction not in ("LONG", "SHORT"):
                return None

            entry = sig_raw.get("entry_price") or sig_raw.get("entry") or sig_raw.get("price")
            if entry is None and df_closed is not None and not df_closed.empty:
                entry = float(df_closed["close"].iloc[-1])

            stop = sig_raw.get("stop_loss") or sig_raw.get("stop")
            targets = sig_raw.get("targets") or sig_raw.get("target_list") or []

            meta = sig_raw.get("meta") if isinstance(sig_raw.get("meta"), dict) else {}
            # score bazı stratejilerde üst seviyede
            if "score" in sig_raw and "score" not in meta:
                meta["score"] = sig_raw.get("score")

            return {
                "direction":direction,
                "entry":_ensure_float(entry, default=float("nan")),
                "stop":_ensure_float(stop, default=float("nan")),
                "targets":[_ensure_float(t, default=float("nan")) for t in list(targets or [])],
                "meta":meta,
            }

        # 2) Eğer SignalResult benzeri bir nesne ise (StrategyV2 SignalResult gibi)
        # (sig_raw.signal alanı bool olabilir; direction ayrı alanda)
        direction = (getattr(sig_raw, "direction", None) or "").upper().strip()
        if direction not in ("LONG", "SHORT"):
            # bazı yapılarda "type" olabilir
            direction = (getattr(sig_raw, "type", None) or "").upper().strip()

        if direction not in ("LONG", "SHORT"):
            return None

        entry = getattr(sig_raw, "entry_price", None)
        if entry is None and df_closed is not None and not df_closed.empty:
            entry = float(df_closed["close"].iloc[-1])

        stop = getattr(sig_raw, "stop_loss", None)
        targets = getattr(sig_raw, "targets", None) or []

        meta = getattr(sig_raw, "meta", {}) or {}
        if not isinstance(meta, dict):
            meta = {}

        score = getattr(sig_raw, "score", None)
        if score is not None and "score" not in meta:
            meta["score"] = score

        return {
            "direction":direction,
            "entry":_ensure_float(entry, default=float("nan")),
            "stop":_ensure_float(stop, default=float("nan")),
            "targets":[_ensure_float(t, default=float("nan")) for t in list(targets or [])],
            "meta":meta,
        }


    except Exception:
        return None


def _ensure_float(x: Any, default: float = float("nan")) -> float:
    try:
        v = float(x)
        return v
    except Exception:
        return float(default)


def _rr(entry: float, stop: float, target: float, direction: str) -> float:
    if entry <= 0 or stop <= 0 or target <= 0:
        return 0.0
    direction = str(direction).upper()
    risk = (stop - entry) if direction == "SHORT" else (entry - stop)
    reward = (entry - target) if direction == "SHORT" else (target - entry)
    if risk <= 0:
        return 0.0
    return float(reward / risk)


def _get_entry_gates_cfg(tf: str) -> dict:
    """
    scans.tf_profiles.<tf>.entry_gates altından okur.
    Yoksa güvenli default döner.
    """
    tf = str(tf or "15m").strip()
    prof = ConfigService.tf_profile(tf, {}) or {}
    eg = (prof.get("entry_gates") or {}) if isinstance(prof, dict) else {}
    if not isinstance(eg, dict):
        eg = {}

    def _f(key, default):
        v = eg.get(key, default)
        try:
            return float(v) if isinstance(default, (int, float)) else v
        except Exception:
            return default

    return {
        "enabled": bool(eg.get("enabled", True)),

        # 1) ADX gate
        "min_local_adx": float(_f("min_local_adx", 15.0)),
        "block_when_local_trend_neutral": bool(eg.get("block_when_local_trend_neutral", True)),

        # 2) Rejim uyumu
        "require_regime_alignment": bool(eg.get("require_regime_alignment", True)),
        "global_down_blocks_long": bool(eg.get("global_down_blocks_long", True)),
        "global_up_blocks_short": bool(eg.get("global_up_blocks_short", True)),

        # 3) SL mesafesi (yüzde)
        "min_sl_pct": float(_f("min_sl_pct", 0.20)),   # %0.20 default (15m için mantıklı başlangıç)

        # opsiyonel: log
        "log_blocked": bool(eg.get("log_blocked", True)),
    }


def _apply_entry_gates(
        symbol: str,
        timeframe: str,
        strategy_id: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        meta: dict
) -> tuple[bool, str]:
    """
    Returns: (ok, reason)
    """
    cfg = _get_entry_gates_cfg(timeframe)
    if not cfg.get("enabled", True):
        return True, "ok:disabled"

    direction = str(direction or "").upper().strip()
    if direction not in ("LONG", "SHORT"):
        return False, "bad_direction"

    # --- Trend snapshot içinden local ADX + local net trend ---
    ts = (meta.get("trend_snapshot") or {}) if isinstance(meta, dict) else {}
    loc = (ts.get("local") or {}) if isinstance(ts, dict) else {}

    local_adx = None
    local_net = None
    try:
        local_adx = float(loc.get("adx")) if loc.get("adx") is not None else None
    except Exception:
        local_adx = None

    try:
        local_net = str(loc.get("net_trend") or loc.get("raw_trend") or "")
    except Exception:
        local_net = ""

    # 1) ADX gate
    min_local_adx = float(cfg.get("min_local_adx", 15.0))
    if local_adx is not None and local_adx > 0:
        if local_adx < min_local_adx:
            return False, f"ADX_LOW adx={local_adx:.2f} < {min_local_adx:.2f}"
    else:
        # ADX yoksa: konservatif davranmak istersen block, değilse geç.
        # Şimdilik geçiyoruz (mevcut akışı bozmamak için).
        pass

    # 1b) Local trend neutral block
    if cfg.get("block_when_local_trend_neutral", True):
        if "nötr" in local_net.lower() or local_net.strip().startswith("⚪"):
            return False, f"LOCAL_NEUTRAL net={local_net}"

    # 2) Rejim uyumu gate (global_regime alanın zaten meta'da var)
    if cfg.get("require_regime_alignment", True):
        g = str(meta.get("global_regime") or meta.get("global_regime_1d") or "").upper().strip()
        # sende global_regime genelde "UP"/"DOWN" dönüyor
        if cfg.get("global_down_blocks_long", True) and g == "DOWN" and direction == "LONG":
            return False, "REGIME_MISMATCH global=DOWN blocks LONG"
        if cfg.get("global_up_blocks_short", True) and g == "UP" and direction == "SHORT":
            return False, "REGIME_MISMATCH global=UP blocks SHORT"

    # 3) SL mesafesi gate
    try:
        e = float(entry_price)
        sl = float(stop_loss)
        if e > 0 and sl > 0:
            sl_pct = abs(e - sl) / e * 100.0
            min_sl_pct = float(cfg.get("min_sl_pct", 0.20))
            meta["sl_pct"] = float(sl_pct)
            meta["min_sl_pct"] = float(min_sl_pct)
            if sl_pct < min_sl_pct:
                return False, f"SL_TOO_TIGHT sl_pct={sl_pct:.3f}% < {min_sl_pct:.3f}%"
    except Exception:
        pass

    return True, "ok"


def _log_no_signal_reason(cls, symbol: str, tf: str, strat_id: str, sig_payload: dict, reason: str | None):
    try:
        meta = sig_payload.get("meta") if isinstance(sig_payload, dict) else None
        if not isinstance(meta, dict):
            meta = {}
        score = meta.get("score", sig_payload.get("score"))
        logging.info(
            f"[NO_SIGNAL] symbol={symbol} tf={tf} strat={strat_id} "
            f"reason={reason or meta.get('reason') or '-'} score={score}"
        )
    except Exception:
        pass


