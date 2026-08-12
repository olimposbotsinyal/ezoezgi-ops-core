# strategies/strategy_v2.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple, cast

import logging
import math

import numpy as np
import pandas as pd

from .base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

# --- Optional TA-Lib ---
try:
    import talib as ta  # type: ignore
    HAS_TALIB = True
except Exception as e:
    logger.warning(f"[StrategyV2] TA-Lib import edilemedi: {e}")
    HAS_TALIB = False


@dataclass
class SignalResult:
    signal: bool
    strategy_id: str
    direction: Optional[str] = None  # "LONG" / "SHORT"
    score: Optional[float] = None
    confidence: Optional[float] = None
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    targets: List[float] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signal": bool(self.signal),
            "strategy_id": self.strategy_id,
            "direction": self.direction,
            "score": self.score,
            "confidence": self.confidence,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "targets": self.targets,
            "meta": self.meta,
        }


class StrategyV2(BaseStrategy):
    """
    V2 = Trigger (SMMA crossover) + Filter (score/regime) + Risk (ATR SL/TP)
    StrategyManager ile uyum:
      - execute_strategy -> analyze(..., global_regime=...)
      - monitor_symbols  -> generate_signal(df)  (opsiyonel context runtime'dan gelir)

    Kritik: circular import yok. global_trade_type / market_regime StrategyManager'dan geçer.
    """

    strategy_id = "v2"
    default_params: Dict[str, Any] = {}

    def __init__(self, strategy_id: str, params: dict = None):
        super().__init__(strategy_id, params)
        self.logger = logger

        # StrategyManager bu sınıfa params=params.get('V2', {}) gönderiyor.
        # O yüzden self.params zaten V2 param dict'i.
        if self.params is None:
            self.params = {}

        # runtime context: StrategyManager set edebilir
        self._runtime_context: Dict[str, Any] = {}

        self.default_min_score_long = 70.0
        self.default_min_score_short = 70.0

    # ---------------------------------------------------------------------
    # Runtime context (StrategyManager -> Strategy)
    # ---------------------------------------------------------------------
    def set_runtime_context(self, ctx: Optional[Dict[str, Any]]) -> None:
        """
        StrategyManager her döngüde:
          {"global_trade_type": "BOTH/LONG/SHORT", "market_regime": "Yükseliş/Düşüş/Yatay", ...}
        gibi bilgileri buradan aktarabilir.
        """
        if isinstance(ctx, dict):
            self._runtime_context = ctx
        else:
            self._runtime_context = {}

    def _ctx(self, key: str, default: Any = None) -> Any:
        return self._runtime_context.get(key, default)

    @staticmethod
    def _no(reason: str, **meta) -> Dict[str, Any]:
        d: Dict[str, Any] = {"signal": False, "reason": str(reason)}
        if meta:
            d["meta"] = dict(meta)
        return d

    # ---------------------------------------------------------------------
    # Param helper (self.params = V2 dict)
    # ---------------------------------------------------------------------
    def _p(self, key: str, default=None):
        # 1) Instance params (StrategyManager / alarm_strateji tarafından verilmiş)
        try:
            if isinstance(getattr(self, "params", None), dict) and key in self.params:
                v = self.params.get(key)
                return default if v is None else v
        except Exception:
            pass

        # 2) ConfigService merged config (control_mode=auto ise tuner override dahil)
        try:
            from config_service import ConfigService
            v = ConfigService.get(f"strategy.v2.{key}", None)
            return default if v is None else v
        except Exception:
            pass

        return default

    # ---------------------------------------------------------------------
    # Data preparation
    # ---------------------------------------------------------------------
    @staticmethod
    def _ensure_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
        if not isinstance(df.index, pd.DatetimeIndex):
            if "timestamp" in df.columns:
                df = df.copy()
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", errors="coerce")
                df = df.set_index("timestamp")
            else:
                df = df.copy()
                df.index = pd.to_datetime(df.index, errors="coerce")
        return df.sort_index()

    @staticmethod
    def _coerce_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
        need = ["open", "high", "low", "close", "volume"]
        for c in need:
            if c not in df.columns:
                raise ValueError(f"missing column: {c}")
        out = df.copy()
        for c in need:
            out[c] = pd.to_numeric(out[c], errors="coerce")
        out = out.dropna(subset=need)
        return out

    @staticmethod
    def _recent_crossover(close_alt: pd.Series, open_alt: pd.Series, lookback: int = 3) -> Tuple[bool, bool]:
        n = min(len(close_alt), len(open_alt))
        if n < 2:
            return False, False

        lb = max(1, int(lookback))
        start = max(1, n - lb)

        bull = False
        bear = False
        for i in range(start, n):
            prev_c, prev_o = close_alt.iloc[i - 1], open_alt.iloc[i - 1]
            last_c, last_o = close_alt.iloc[i], open_alt.iloc[i]
            if any(pd.isna(x) for x in [prev_c, prev_o, last_c, last_o]):
                continue
            bull = bull or ((prev_c <= prev_o) and (last_c > last_o))
            bear = bear or ((prev_c >= prev_o) and (last_c < last_o))

        return bull, bear

    # ---------------------------------------------------------------------
    # Indicators (fallback included)
    # ---------------------------------------------------------------------
    @staticmethod
    def _ema(series: pd.Series, length: int) -> pd.Series:
        s = pd.to_numeric(series, errors="coerce")
        return s.ewm(span=int(length), adjust=False, min_periods=int(length)).mean()

    @staticmethod
    def _rsi(series: pd.Series, length: int = 14) -> pd.Series:
        s = pd.to_numeric(series, errors="coerce")
        delta = s.diff()
        up = delta.clip(lower=0.0)
        down = (-delta).clip(lower=0.0)
        roll_up = up.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
        roll_down = down.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
        rs = roll_up / roll_down.replace(0.0, np.nan)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi

    @staticmethod
    def smma(series: pd.Series, length: int) -> pd.Series:
        series = pd.to_numeric(series, errors="coerce")
        n = len(series)
        if n == 0:
            return series.copy()
        if length <= 1:
            return series.copy()

        out = [np.nan] * n
        seed_range = min(length, n)
        seed = pd.Series(series.iloc[:seed_range]).mean()
        prev = seed

        for i in range(seed_range - 1, n):
            val = series.iloc[i]
            if pd.isna(val):
                out[i] = np.nan
                continue
            if pd.isna(prev):
                prev = val
            prev = (prev * (length - 1) + val) / length
            out[i] = prev

        return pd.Series(out, index=series.index, dtype="float64")

    @staticmethod
    def _atr(df: pd.DataFrame, period: int) -> pd.Series:
        h = df["high"].astype(float)
        l = df["low"].astype(float)
        c = df["close"].astype(float)

        if HAS_TALIB:
            atr = ta.ATR(h.values, l.values, c.values, timeperiod=int(period))
            return pd.Series(atr, index=df.index)

        pc = c.shift(1)
        tr = pd.concat([(h - l).abs(), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
        return StrategyV2.smma(tr, int(period))

    @staticmethod
    def _macd_hist(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
        s = pd.to_numeric(series, errors="coerce")
        if HAS_TALIB:
            macd, macds, macdh = ta.MACD(s.values, fastperiod=fast, slowperiod=slow, signalperiod=signal)
            return pd.Series(macdh, index=s.index)

        ema_fast = StrategyV2._ema(s, fast)
        ema_slow = StrategyV2._ema(s, slow)
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
        return macd_line - signal_line

    @staticmethod
    def _bb_width(
            series: pd.Series,
            length: int = 20,
            nbdev: float = 2.0
    ) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
        s = pd.to_numeric(series, errors="coerce")

        if HAS_TALIB:
            # MA_Type yoksa bile (çok uç durum), getattr SMA döndürsün; yine MA_Type gibi davranır
            ma_type = getattr(ta.MA_Type, "SMA", ta.MA_Type.SMA)
            ma_type = cast("ta.MA_Type", ma_type)

            upper, middle, lower = ta.BBANDS(
                s.values,
                timeperiod=int(length),
                nbdevup=float(nbdev),
                nbdevdn=float(nbdev),
                matype=ma_type,
            )
            upper = pd.Series(upper, index=s.index)
            middle = pd.Series(middle, index=s.index)
            lower = pd.Series(lower, index=s.index)
        else:
            middle = s.rolling(int(length), min_periods=int(length)).mean()
            std = s.rolling(int(length), min_periods=int(length)).std(ddof=0)
            upper = middle + float(nbdev) * std
            lower = middle - float(nbdev) * std

        width = (upper - lower) / middle.replace(0.0, np.nan)
        return upper, middle, lower, width

    @staticmethod
    def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
        h = df["high"].astype(float)
        l = df["low"].astype(float)
        c = df["close"].astype(float)

        if HAS_TALIB:
            adx = ta.ADX(h.values, l.values, c.values, timeperiod=int(period))
            return pd.Series(adx, index=df.index)

        up_move = h.diff()
        down_move = -l.diff()
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        pc = c.shift(1)
        tr = pd.concat([(h - l).abs(), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)

        tr_smma = StrategyV2.smma(tr, period)
        plus_di = 100.0 * StrategyV2.smma(pd.Series(plus_dm, index=df.index), period) / tr_smma.replace(0.0, np.nan)
        minus_di = 100.0 * StrategyV2.smma(pd.Series(minus_dm, index=df.index), period) / tr_smma.replace(0.0, np.nan)

        dx = (100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan))
        return StrategyV2.smma(dx, period)

    @staticmethod
    def _mfi(df: pd.DataFrame, period: int = 14) -> pd.Series:
        h = df["high"].astype(float)
        l = df["low"].astype(float)
        c = df["close"].astype(float)
        v = df["volume"].astype(float)

        if HAS_TALIB:
            mfi = ta.MFI(h.values, l.values, c.values, v.values, timeperiod=int(period))
            return pd.Series(mfi, index=df.index)

        tp = (h + l + c) / 3.0
        rmf = tp * v
        direction = tp.diff().astype("float64")

        zero = rmf.mul(0.0)
        pos = rmf.where(direction > 0.0, other=zero)
        neg = rmf.where(direction < 0.0, other=zero).abs()

        p = int(period)

        pos_sum = pos.rolling(window=p, min_periods=p).sum()
        neg_sum = (
            neg.rolling(window=p, min_periods=p)
            .sum()
            .replace(to_replace=0.0, value=np.nan)
        )

        mfr = pos_sum / neg_sum
        return 100.0 - (100.0 / (1.0 + mfr))

    # ---------------------------------------------------------------------
    # Trigger (SMMA crossover) - SAFE
    # ---------------------------------------------------------------------
    def _compute_smma_series(self, df: pd.DataFrame, length: int, delay_offset: int) -> Tuple[pd.Series, pd.Series]:
        op = df["open"].astype(float)
        cl = df["close"].astype(float)
        if delay_offset > 0:
            op = op.shift(int(delay_offset))
            cl = cl.shift(int(delay_offset))
        return self.smma(op, int(length)), self.smma(cl, int(length))

    @staticmethod
    def _last_bar_crossover(close_alt: pd.Series, open_alt: pd.Series) -> Tuple[bool, bool]:
        if len(close_alt) < 2 or len(open_alt) < 2:
            return False, False

        prev_c, prev_o = close_alt.iloc[-2], open_alt.iloc[-2]
        last_c, last_o = close_alt.iloc[-1], open_alt.iloc[-1]

        if any(pd.isna(x) for x in [prev_c, prev_o, last_c, last_o]):
            return False, False

        bull = (prev_c <= prev_o) and (last_c > last_o)
        bear = (prev_c >= prev_o) and (last_c < last_o)
        return bull, bear

    # ---------------------------------------------------------------------
    # Filters
    # ---------------------------------------------------------------------
    @staticmethod
    def _passes_global_trade_type(direction: str, global_trade_type: str) -> bool:
        gtt = (global_trade_type or "BOTH").upper()
        if direction == "LONG" and gtt == "SHORT":
            return False
        if direction == "SHORT" and gtt == "LONG":
            return False
        return True

    @staticmethod
    def _alt_last_values(series: pd.Series, factor: int) -> pd.Series:
        n = len(series)
        if factor <= 1 or n < factor * 2:
            return series.copy()

        gindex = np.arange(n) // factor
        # her grubun son değerini tek bir seri olarak çıkar (index: grup son barı)
        last_idx = pd.Series(np.arange(n)).groupby(gindex).max().values
        out = series.iloc[last_idx]
        return out

    @staticmethod
    def _passes_market_regime_label(direction: str, market_regime: Optional[str]) -> bool:
        if not isinstance(market_regime, str):
            return True
        if market_regime == "Yükseliş" and direction == "SHORT":
            return False
        if market_regime == "Düşüş" and direction == "LONG":
            return False
        return True

    @staticmethod
    def _zscore_last(series: pd.Series, window: int) -> float:
        s = pd.to_numeric(series, errors="coerce").dropna()
        if len(s) < window + 5:
            return float("nan")
        w = s.iloc[-window:]
        mu = float(w.mean())
        sd = float(w.std(ddof=0))
        if not (math.isfinite(sd) and sd > 0):
            return float("nan")
        return (float(s.iloc[-1]) - mu) / sd

    def _compute_score(
            self,
            df: pd.DataFrame,
            direction: str,
            global_regime: Optional[Dict[str, Any]] = None,
            countertrend_override: Optional[bool] = None,
    ) -> Tuple[float, float, Dict[str, Any]]:
        """
        Returns:
          (final_score, threshold_used, meta_bits)

        countertrend_override:
          - None: self._ctx("countertrend") değerini kullan (mevcut davranış)
          - True/False: skor içinde countertrend çarpanını bu değere göre uygula
            (Hybrid modda LONG/SHORT ayrı ayrı doğru cezayla kıyaslanabilsin)
        """
        global_regime = global_regime or {}
        direction = str(direction or "").upper().strip()
        if direction not in ("LONG", "SHORT"):
            return float("nan"), float("nan"), {"error":"bad_direction"}

        close = df["close"].astype(float)
        vol = df["volume"].astype(float)

        # --- EMA ---
        ema50_last = float((ta.EMA(close.values, 50)[-1]) if HAS_TALIB else self._ema(close, 50).iloc[-1])
        ema200_last = float((ta.EMA(close.values, 200)[-1]) if HAS_TALIB else self._ema(close, 200).iloc[-1])

        # --- RSI ---
        rsi_s = pd.Series(ta.RSI(close.values, 14), index=df.index) if HAS_TALIB else self._rsi(close, 14)
        rsi_last = float(rsi_s.iloc[-1]) if pd.notna(rsi_s.iloc[-1]) else float("nan")

        # --- MACD histogram ---
        macdh = self._macd_hist(close, 12, 26, 9)
        macdh_last = float(macdh.iloc[-1]) if pd.notna(macdh.iloc[-1]) else float("nan")
        macdh_prev = float(macdh.iloc[-2]) if len(macdh) >= 2 and pd.notna(macdh.iloc[-2]) else float("nan")

        # --- ADX ---
        adx_s = self._adx(df, 14)
        adx_last = float(adx_s.iloc[-1]) if pd.notna(adx_s.iloc[-1]) else float("nan")

        # --- MFI ---
        mfi_s = self._mfi(df, 14)
        mfi_last = float(mfi_s.iloc[-1]) if pd.notna(mfi_s.iloc[-1]) else float("nan")

        # --- BB width + adaptive squeeze ---
        _, _, _, bb_width = self._bb_width(close, 20, 2.0)
        bb_width_last = float(bb_width.iloc[-1]) if pd.notna(bb_width.iloc[-1]) else float("nan")

        q_window = int(self._p("bb_quantile_window", 200) or 200)
        q_level = float(self._p("bb_squeeze_quantile", 0.2) or 0.2)
        squeeze = False
        squeeze_thr = float("nan")
        if len(bb_width.dropna()) >= q_window:
            squeeze_thr = float(bb_width.rolling(q_window, min_periods=q_window).quantile(q_level).iloc[-1])
            if math.isfinite(squeeze_thr) and math.isfinite(bb_width_last):
                squeeze = bb_width_last <= squeeze_thr

        # --- Volume z-score confirmation ---
        vz_window = int(self._p("vol_z_window", 48) or 48)
        vol_z = self._zscore_last(vol, vz_window)

        # --- Trend context (local/global) ---
        current_close = float(close.iloc[-1])
        local_trend = "BULL" if (math.isfinite(ema200_last) and current_close > ema200_last) else "BEAR"
        global_trend = str((global_regime or {}).get("trend", "UNKNOWN")).upper()

        raw = 0.0

        # ------------------------------------------------------------------
        # RSI scoring (symmetric)
        # ------------------------------------------------------------------
        if math.isfinite(rsi_last):
            if direction == "LONG":
                if 50.0 < rsi_last < 70.0:
                    raw += 20.0
                elif rsi_last >= 70.0:
                    raw += 10.0
                elif rsi_last < 40.0:
                    raw -= 10.0
            else:  # SHORT
                if 30.0 < rsi_last < 50.0:
                    raw += 20.0
                elif rsi_last <= 30.0:
                    raw += 10.0
                elif rsi_last > 60.0:
                    raw -= 10.0

        # ------------------------------------------------------------------
        # MACD histogram momentum (symmetric)
        # ------------------------------------------------------------------
        if math.isfinite(macdh_last) and math.isfinite(macdh_prev):
            if direction == "LONG":
                if macdh_last > 0.0 and macdh_last > macdh_prev:
                    raw += 20.0
                elif macdh_last < 0.0:
                    raw -= 5.0
            else:  # SHORT
                if macdh_last < 0.0 and macdh_last < macdh_prev:
                    raw += 20.0
                elif macdh_last > 0.0:
                    raw -= 5.0

        # ------------------------------------------------------------------
        # Trend alignment (EMA50/EMA200)
        # ------------------------------------------------------------------
        if math.isfinite(ema50_last) and math.isfinite(ema200_last):
            if direction == "LONG":
                if current_close > ema50_last:
                    raw += 15.0
                if current_close > ema200_last:
                    raw += 15.0
            else:  # SHORT
                if current_close < ema50_last:
                    raw += 15.0
                if current_close < ema200_last:
                    raw += 15.0

        # ------------------------------------------------------------------
        # ADX contribution
        # ------------------------------------------------------------------
        if math.isfinite(adx_last):
            adx_good = float(self._p("adx_good", 25) or 25)
            adx_bad = float(self._p("adx_bad", 20) or 20)
            if adx_last > adx_good:
                raw += 10.0
            elif adx_last < adx_bad:
                raw -= 10.0

        # ------------------------------------------------------------------
        # MFI contribution (light)
        # ------------------------------------------------------------------
        if math.isfinite(mfi_last):
            if direction == "LONG":
                if 50.0 < mfi_last < 80.0:
                    raw += 5.0
                elif mfi_last > 85.0:
                    raw -= 5.0
            else:  # SHORT
                if 20.0 < mfi_last < 50.0:
                    raw += 5.0
                elif mfi_last < 15.0:
                    raw -= 5.0

        # ------------------------------------------------------------------
        # Squeeze + Volume anomaly
        # ------------------------------------------------------------------
        if squeeze:
            raw += float(self._p("squeeze_bonus", 5) or 5)

        vol_z_thr = float(self._p("vol_z_min", 0.8) or 0.8)
        if math.isfinite(vol_z) and vol_z >= vol_z_thr:
            raw += float(self._p("vol_bonus", 8) or 8)

        # ------------------------------------------------------------------
        # Thresholds + regime adjustment
        # ------------------------------------------------------------------
        sms = self._p("signal_min_score", None)
        if sms is not None:
            base_thr = float(sms)
        else:
            base_thr = float(self._p("min_score_long" if direction == "LONG" else "min_score_short", 70.0))

        thr = float(base_thr)
        regime_bonus = 0.0

        if global_trend == "UNKNOWN":
            thr += float(self._p("unknown_regime_penalty", 8.0) or 8.0)

        elif global_trend == "UP":
            if direction == "LONG":
                if local_trend == "BULL":
                    regime_bonus += 5.0
                    thr -= 2.0
                else:
                    thr += 5.0
            else:  # SHORT in UP
                thr += 10.0
                regime_bonus -= 5.0

        elif global_trend == "DOWN":
            if direction == "SHORT":
                if local_trend == "BEAR":
                    regime_bonus += 5.0
                    thr -= 2.0
                else:
                    thr += 5.0
            else:  # LONG in DOWN
                thr += 10.0
                regime_bonus -= 5.0

        # ------------------------------------------------------------------
        # Countertrend multiplier (Hybrid override aware)
        # ------------------------------------------------------------------
        ct = self._ctx("countertrend", False) if countertrend_override is None else bool(countertrend_override)
        if ct:
            mult = float(self._p("countertrend_score_mult", 0.7) or 0.7)
            final_score = (raw + regime_bonus) * mult
        else:
            final_score = raw + regime_bonus

        meta_bits = {
            "global_trend":global_trend,
            "local_trend":local_trend,
            "rsi":None if not math.isfinite(rsi_last) else round(rsi_last, 2),
            "adx":None if not math.isfinite(adx_last) else round(adx_last, 2),
            "mfi":None if not math.isfinite(mfi_last) else round(mfi_last, 2),
            "bb_width":None if not math.isfinite(bb_width_last) else round(bb_width_last, 4),
            "bb_squeeze":bool(squeeze),
            "bb_squeeze_thr":None if not math.isfinite(squeeze_thr) else round(squeeze_thr, 4),
            "vol_z":None if not math.isfinite(vol_z) else round(vol_z, 3),
            "ema50":round(ema50_last, 8) if math.isfinite(ema50_last) else None,
            "ema200":round(ema200_last, 8) if math.isfinite(ema200_last) else None,
            "macdh":None if not math.isfinite(macdh_last) else round(macdh_last, 6),
            "raw_score":round(raw, 2),
            "regime_bonus":round(regime_bonus, 2),
            "threshold":round(thr, 2),
            "final_score":round(final_score, 2),
            "countertrend_applied":bool(ct),
        }

        return float(final_score), float(thr), meta_bits

    def _build_risk_levels(self, df: pd.DataFrame, direction: str, entry: float) -> Tuple[Optional[float], List[float], Dict[str, Any]]:
        use_atr = bool(self._p("use_atr_targets", True))
        targets: List[float] = []
        stop: Optional[float] = None
        meta: Dict[str, Any] = {}

        if use_atr:
            atr_len = int(self._p("atr_len", self._p("atr_period", 14)) or 14)
            atr_s = self._atr(df, atr_len)
            atr = float(atr_s.iloc[-1]) if len(atr_s) and pd.notna(atr_s.iloc[-1]) else float("nan")

            if math.isfinite(atr) and atr > 0:
                if direction == "LONG":
                    m_sl = float(self._p("atr_mult_sl_long", self._p("atr_multiplier", 1.4)) or 1.4)
                    tps = self._p("atr_mult_tps_long", [0.9, 1.1, 1.8, 2.6, 3.6]) or []
                    stop = round(entry - atr * m_sl, 8)
                    for m in list(tps)[:5]:
                        targets.append(round(entry + atr * float(m), 8))
                else:
                    m_sl = float(self._p("atr_mult_sl_short", self._p("atr_multiplier", 1.4)) or 1.4)
                    tps = self._p("atr_mult_tps_short", [0.9, 1.1, 1.8, 2.6, 3.6]) or []
                    stop = round(entry + atr * m_sl, 8)
                    for m in list(tps)[:5]:
                        targets.append(round(entry - atr * float(m), 8))

                meta["target_mode"] = "atr"
                meta["atr_len"] = atr_len
                meta["atr_last"] = round(atr, 8)
                meta["calc_method"] = {"tp": "ATR Multiplier", "sl": "ATR Multiplier"}
                return stop, targets, meta

        # Percent fallback
        sl_pct = float(self._p("stop_loss_pct_v2", 2.0) or 2.0) / 100.0
        raw_tps = self._p("take_profit_list_v2", [2.0, 4.0, 6.0, 8.0, 10.0]) or []
        if direction == "LONG":
            stop = round(entry * (1 - sl_pct), 8)
            targets = [round(entry * (1 + float(p) / 100.0), 8) for p in list(raw_tps)[:5]]
        else:
            stop = round(entry * (1 + sl_pct), 8)
            targets = [round(entry * (1 - float(p) / 100.0), 8) for p in list(raw_tps)[:5]]

        meta["target_mode"] = "percent"
        meta["calc_method"] = {"tp": "Percent", "sl": "Percent"}
        return stop, targets, meta

    # ---------------------------------------------------------------------
    # Main analyze (Manager execute_strategy ile uyumlu)
    # ---------------------------------------------------------------------
    async def analyze(self, df: pd.DataFrame, symbol: str, global_regime: Dict[str, Any] = None) -> Dict[str, Any]:
        self.logger.info("[V2_PARAMS] basis_len=%s alt_resolution_factor=%s delay_offset=%s",
            self._p("basis_len", None), self._p("alt_resolution_factor", None), self._p("delay_offset", None)
        )
        self.logger.info(
            "[V2_THR_SRC] params_signal_min_score=%s cfg_signal_min_score=%s",
            (self.params or {}).get("signal_min_score") if isinstance(self.params, dict) else None,
            __import__("config_service").ConfigService.get("strategy.v2.signal_min_score", None),
        )
        self._runtime_context.pop("countertrend", None)

        try:
            min_bars = int(self._p("min_bars", 220) or 220)
            if df is None or len(df) < min_bars:
                return self._no("v2:insufficient_bars", have=0 if df is None else len(df), need=min_bars)

            df = self._ensure_datetime_index(df)
            df = self._coerce_ohlcv(df)
            if len(df) < min_bars:
                return self._no("v2:cleaned_df_too_short", have=len(df), need=min_bars)

            global_regime = global_regime or {}
            trade_type = str(self._p("trade_type_v2", "BOTH") or "BOTH").upper()
            use_score_filter = bool(self._p("use_score_filter", True))
            use_trigger = bool(self._p("use_trigger", True))

            # Context kaynakları (öncelik: global_regime > runtime_context > params)
            global_trade_type = (
                    global_regime.get("global_trade_type")
                    or self._ctx("global_trade_type")
                    or self._p("global_trade_type", "BOTH")
            )

            # 1) Önce trend -> label üret (en güçlü sinyal)
            trend = str((global_regime or {}).get("trend", "")).upper()
            market_regime_label = None
            if trend == "DOWN":
                market_regime_label = "Düşüş"
            elif trend == "UP":
                market_regime_label = "Yükseliş"
            elif trend in ("SIDEWAYS", "RANGE"):
                market_regime_label = "Yatay"

            # 2) Trend UNKNOWN ise diğer kaynaklara düş
            if not market_regime_label:
                market_regime_label = (
                        global_regime.get("regime_label")
                        or self._ctx("market_regime")
                        or None
                )

            # 1) Trigger -> direction
            if use_trigger:
                use_alt = bool(self._p("use_alt_resolution", True))
                alt_factor = int(self._p("alt_resolution_factor", 4) or 4)
                basis_len = int(self._p("basis_len", 2) or 2)
                delay_off = int(self._p("delay_offset", 0) or 0)

                open_smma, close_smma = self._compute_smma_series(df, basis_len, delay_off)
                if use_alt and alt_factor > 1:
                    open_alt = self._alt_last_values(open_smma, factor=alt_factor)
                    close_alt = self._alt_last_values(close_smma, factor=alt_factor)
                else:
                    open_alt, close_alt = open_smma, close_smma

                lookback = int(self._p("trigger_lookback_bars", 3) or 3)
                bull, bear = self._recent_crossover(close_alt, open_alt, lookback=lookback)
                direction = "LONG" if bull else ("SHORT" if bear else None)

                if direction is None:
                    if bool(self._p("debug_score_on_no_trigger", False)):
                        score_long, thr_long, meta_long = self._compute_score(df, "LONG", global_regime)
                        score_short, thr_short, meta_short = self._compute_score(df, "SHORT", global_regime)
                        return self._no(
                            "v2:no_trigger",
                            trigger="smma_crossover",
                            alt_factor=alt_factor,
                            basis_len=basis_len,
                            delay_offset=delay_off,
                            score_long=None if not math.isfinite(score_long) else float(score_long),
                            thr_long=None if not math.isfinite(thr_long) else float(thr_long),
                            score_short=None if not math.isfinite(score_short) else float(score_short),
                            thr_short=None if not math.isfinite(thr_short) else float(thr_short),
                            score_meta_long=meta_long,
                            score_meta_short=meta_short,
                        )

                    return self._no(
                        "v2:no_trigger",
                        trigger="smma_crossover",
                        alt_factor=alt_factor,
                        basis_len=basis_len,
                        delay_offset=delay_off,
                    )

                trigger_meta = {
                    "trigger":"smma_crossover",
                    "alt_factor":alt_factor,
                    "basis_len":basis_len,
                    "delay_offset":delay_off,
                }

            else:
                # trigger kapalı ise direction default LONG
                direction = "LONG"
                trigger_meta = {"trigger":"disabled"}
            # StrategyV2.analyze içinde, direction belirlendikten HEMEN sonra:

            hybrid_enabled = bool(self._p("hybrid_enabled", True))
            hybrid_gap = float(self._p("hybrid_flip_min_gap", 10.0) or 10.0)
            hybrid_log = bool(self._p("hybrid_log", True))
            require_trigger_for_flip = bool(self._p("hybrid_require_trigger_for_flip", False))
            allow_counter = bool(self._p("allow_countertrend", False))

            hybrid_dbg = {}

            if hybrid_enabled and use_trigger and direction in ("LONG", "SHORT") and use_score_filter:
                # allowed by trade filters
                allowl = (trade_type in ("BOTH", "LONG")) and self._passes_global_trade_type("LONG",
                    str(global_trade_type))
                allows = (trade_type in ("BOTH", "SHORT")) and self._passes_global_trade_type("SHORT",
                    str(global_trade_type))

                # countertrend status per direction (market_regime_label bazlı)
                ctl = bool(allow_counter and (not self._passes_market_regime_label("LONG", market_regime_label)))
                cts = bool(allow_counter and (not self._passes_market_regime_label("SHORT", market_regime_label)))

                sl, tl, _ = self._compute_score(df, "LONG", global_regime, countertrend_override=ctl)
                ss, ts, _ = self._compute_score(df, "SHORT", global_regime, countertrend_override=cts)

                okl = bool(allowl and math.isfinite(sl) and math.isfinite(tl) and (sl >= tl))
                oks = bool(allows and math.isfinite(ss) and math.isfinite(ts) and (ss >= ts))

                if hybrid_log:
                    hybrid_dbg = {
                        "hybrid_enabled":True,
                        "hybrid_trigger_dir":direction,
                        "hybrid_allow_long":allowl,
                        "hybrid_allow_short":allows,
                        "hybrid_countertrend_long":ctl,
                        "hybrid_countertrend_short":cts,
                        "hybrid_score_long":None if not math.isfinite(sl) else float(round(sl, 2)),
                        "hybrid_thr_long":None if not math.isfinite(tl) else float(round(tl, 2)),
                        "hybrid_score_short":None if not math.isfinite(ss) else float(round(ss, 2)),
                        "hybrid_thr_short":None if not math.isfinite(ts) else float(round(ts, 2)),
                        "hybrid_ok_long":okl,
                        "hybrid_ok_short":oks,
                        "hybrid_gap":float(hybrid_gap),
                    }

                chosen = direction

                # Flip kararları (require_trigger_for_flip şimdilik kapalı; açarsan bull/bear bilgisini burada kullan)
                if direction == "LONG":
                    if oks and (not okl):
                        chosen = "SHORT"
                        if hybrid_log: hybrid_dbg["hybrid_decision"] = "flip_short_because_long_failed"
                    elif oks and okl and ((ss - sl) >= hybrid_gap):
                        if not require_trigger_for_flip:
                            chosen = "SHORT"
                            if hybrid_log: hybrid_dbg["hybrid_decision"] = f"flip_short_gap>={hybrid_gap}"
                else:
                    if okl and (not oks):
                        chosen = "LONG"
                        if hybrid_log: hybrid_dbg["hybrid_decision"] = "flip_long_because_short_failed"
                    elif okl and oks and ((sl - ss) >= hybrid_gap):
                        if not require_trigger_for_flip:
                            chosen = "LONG"
                            if hybrid_log: hybrid_dbg["hybrid_decision"] = f"flip_long_gap>={hybrid_gap}"

                direction = chosen

                # önemli: score filtresi aşağıda tekrar hesaplanacaksa,
                # burada sadece direction seçtik; final_score/threshold_used yine seçilen yöne göre compute edilecek.

            # 2) Trade-type filtreleri
            if direction == "LONG" and trade_type not in ("BOTH", "LONG"):
                return self._no("v2:blocked_by_trade_type", direction=direction, trade_type=trade_type)
            if direction == "SHORT" and trade_type not in ("BOTH", "SHORT"):
                return self._no("v2:blocked_by_trade_type", direction=direction, trade_type=trade_type)

            if not self._passes_global_trade_type(direction, str(global_trade_type)):
                return self._no("v2:blocked_by_global_trade_type", direction=direction,
                    gtt=str(global_trade_type).upper())

            if not self._passes_market_regime_label(direction, market_regime_label):
                allow_counter = bool(self._p("allow_countertrend", False))
                if not allow_counter:
                    return self._no(
                        "v2:blocked_by_market_regime",
                        direction=direction,
                        regime=market_regime_label,
                        global_trend=str((global_regime or {}).get("trend", "UNKNOWN")).upper(),
                    )
                # countertrend allowed -> skor aşamasında ceza uygula
                self._runtime_context["countertrend"] = True

            # 3) Score filtresi
            final_score = None
            threshold_used = None
            score_meta: Dict[str, Any] = {}

            if use_score_filter:
                final_score, threshold_used, score_meta = self._compute_score(df, direction, global_regime)
                if hybrid_dbg and isinstance(score_meta, dict):
                    score_meta["hybrid_debug"] = hybrid_dbg

                if not (math.isfinite(final_score) and math.isfinite(threshold_used)):
                    return self._no("v2:score_nan", direction=direction)
                if final_score < threshold_used:
                    return self._no(
                        "v2:score_below_threshold",
                        direction=direction,
                        score=float(final_score),
                        thr=float(threshold_used),
                        **(score_meta or {})
                    )
                # Extreme RSI safety (sym)
                rsi_max_long = float(self._p("rsi_max_long", 78) or 78)
                rsi_min_short = float(self._p("rsi_min_short", 22) or 22)
                rsi_val = score_meta.get("rsi", None)
                if isinstance(rsi_val, (int, float)):
                    if direction == "LONG" and rsi_val >= rsi_max_long:
                        return self._no("v2:rsi_too_high", direction=direction, rsi=float(rsi_val),
                            rsi_max=float(rsi_max_long))
                    if direction == "SHORT" and rsi_val <= rsi_min_short:
                        return self._no("v2:rsi_too_low", direction=direction, rsi=float(rsi_val),
                            rsi_min=float(rsi_min_short))

            # 4) Risk
            entry = float(df["close"].iloc[-1])
            stop, targets, risk_meta = self._build_risk_levels(df, direction, entry)

            confidence = None
            if isinstance(final_score, (int, float)) and math.isfinite(float(final_score)):
                confidence = max(0.0, min(1.0, float(final_score) / 100.0))

            res = SignalResult(
                signal=True,
                strategy_id="v2",
                direction=direction,
                score=None if final_score is None else round(float(final_score), 2),
                confidence=None if confidence is None else round(float(confidence), 4),
                entry_price=round(entry, 8),
                stop_loss=None if stop is None else float(stop),
                targets=[float(x) for x in targets[:5]],
                meta={
                    "symbol": symbol,
                    "trade_type": trade_type,
                    "global_trade_type": str(global_trade_type).upper(),
                    "countertrend":bool(self._ctx("countertrend", False)),
                    "market_regime_label": market_regime_label,
                    "use_trigger": use_trigger,
                    "use_score_filter": use_score_filter,
                    **trigger_meta,
                    **score_meta,
                    **risk_meta,
                },
            )

            self.logger.info(
                "[V2] %s dir=%s entry=%.8f stop=%s score=%s thr=%s gtt=%s",
                symbol,
                direction,
                entry,
                f"{res.stop_loss:.8f}" if res.stop_loss is not None else "None",
                res.score,
                threshold_used,
                str(global_trade_type).upper(),
            )

            return res.to_dict()


        except Exception as e1:
            logger.error(f"[StrategyV2.analyze] Hata: {e1}", exc_info=True)
            return self._no("v2:exception", err=str(e1)[:200])

    # ---------------------------------------------------------------------
    # generate_signal (monitor_symbols ile uyumlu, sync)
    # ---------------------------------------------------------------------
    def generate_signal(
            self,
            df: pd.DataFrame,
            market_regime: str = "Yatay",
            global_trade_type: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        StrategyManager.monitor_symbols -> generate_signal(df) çağırıyor.
        Bu yüzden:
          - global_trade_type parametre ile veya runtime_context ile gelebilir.
          - market_regime string filtresi korunur (backward uyum).
        """
        try:
            if df is None or len(df) < 60:
                return None

            df = self._ensure_datetime_index(df)
            df = self._coerce_ohlcv(df)
            if len(df) < 60:
                return None

            # trade filters
            trade_type = str(self._p("trade_type_v2", "BOTH") or "BOTH").upper()
            gtt = (global_trade_type or self._ctx("global_trade_type") or self._p("global_trade_type", "BOTH"))

            # Trigger params
            use_alt = bool(self._p("use_alt_resolution", True))
            alt_factor = int(self._p("alt_resolution_factor", 4) or 4)
            basis_len = int(self._p("basis_len", 2) or 2)
            delay_off = int(self._p("delay_offset", 0) or 0)

            # Trigger compute
            open_smma, close_smma = self._compute_smma_series(df, basis_len, delay_off)
            if use_alt and alt_factor > 1:
                open_alt = self._alt_last_values(open_smma, factor=alt_factor)
                close_alt = self._alt_last_values(close_smma, factor=alt_factor)
            else:
                open_alt, close_alt = open_smma, close_smma

            lookback = int(self._p("trigger_lookback_bars", 3) or 3)
            bull, bear = self._recent_crossover(close_alt, open_alt, lookback=lookback)

            direction: Optional[str] = None
            if bull and trade_type in ("BOTH", "LONG"):
                direction = "LONG"
            elif bear and trade_type in ("BOTH", "SHORT"):
                direction = "SHORT"
            else:
                return None

            # Market regime + countertrend handling (TEK kontrol)
            allow_counter = bool(self._p("allow_countertrend", False))
            if not self._passes_market_regime_label(direction, market_regime):
                if not allow_counter:
                    return None
                countertrend = True
            else:
                countertrend = False

            # Global trade type filter
            if not self._passes_global_trade_type(direction, str(gtt)):
                return None

            # Risk
            entry = float(df["close"].iloc[-1])
            stop, targets, risk_meta = self._build_risk_levels(df, direction, entry)

            meta = {
                "strategy_id":"v2",
                "trigger":"smma_crossover",
                "countertrend":bool(countertrend),
                "market_regime":market_regime,
                "alt_resolution_factor":alt_factor,
                "basis_len":basis_len,
                "delay_offset":delay_off,
                "direction_reason":"smma_recent_crossover",
                "trade_type":trade_type,
                "global_trade_type":str(gtt).upper(),
                **risk_meta,
            }

            return {
                "type":direction,
                "entry":float(round(entry, 8)),
                "stop":None if stop is None else float(stop),
                "targets":[float(x) for x in targets[:5]],
                "meta":meta,
            }

        except Exception as error:
            logging.error(f"[StrategyV2.generate_signal] hata: {error}", exc_info=True)
            return None

    @property
    def id(self):
        return self.strategy_id
