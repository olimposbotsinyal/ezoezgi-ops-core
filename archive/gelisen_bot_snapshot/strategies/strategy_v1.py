# strategies/strategy_v1.py
from __future__ import annotations

import logging
from typing import Dict, Any, Optional, List
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
    logger.warning(f"[StrategyV1] TA-Lib import edilemedi: {e}")
    HAS_TALIB = False


class StrategyV1(BaseStrategy):
    strategy_id = "v1"
    default_params = {}

    # Sinyal meta cache (sembol bazlı)
    _last_signal_meta: dict = {}

    def __init__(self, strategy_id: str, params: dict = None):
        super().__init__(strategy_id, params)
        if self.params is None:
            self.params = {}

        self.default_min_score = 70.0
        self._runtime_context: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Runtime context (StrategyManager -> Strategy)  [V2 ile uyumlu hale getirildi]
    # ------------------------------------------------------------------
    def set_runtime_context(self, ctx: Optional[Dict[str, Any]]) -> None:
        if isinstance(ctx, dict):
            self._runtime_context = ctx
        else:
            self._runtime_context = {}

    def _ctx(self, key: str, default: Any = None) -> Any:
        return self._runtime_context.get(key, default)

    # ------------------------------------------------------------------
    # Param helpers
    # ------------------------------------------------------------------
    def _p(self, key: str, default: Any = None) -> Any:
        """StrategyManager V1'e zaten V1 param dict'ini gönderiyor."""
        return (self.params or {}).get(key, default)

    def _rp(self, key: str, default: Any = None) -> Any:
        """
        runtime_params varsa onu kullan, yoksa params'a düş.
        BaseStrategy'de runtime_params yoksa patlamasın.
        """
        rp = getattr(self, "runtime_params", None)
        if isinstance(rp, dict) and key in rp:
            return rp.get(key, default)
        return self._p(key, default)

    # ------------------------------------------------------------------
    # Fallback indicator helpers (TA-Lib yoksa)
    # ------------------------------------------------------------------
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
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def _atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
        h = pd.to_numeric(df["high"], errors="coerce")
        l = pd.to_numeric(df["low"], errors="coerce")
        c = pd.to_numeric(df["close"], errors="coerce")

        if HAS_TALIB:
            atr = ta.ATR(h.values, l.values, c.values, timeperiod=int(length))
            return pd.Series(atr, index=df.index)

        pc = c.shift(1)
        tr = pd.concat([(h - l).abs(), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
        # Wilder benzeri
        return tr.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------
    async def analyze(self, df: pd.DataFrame, symbol: str, global_regime: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        V1 Stratejisi - MOMENTUM BEAST EDITION
        Akış korunmuştur:
          1) İndikatörler
          2) Puanlama
          3) Adaptive tuner
          4) Rejim mantığı
          5) SL/TP
        """
        if df is None or df.empty or len(df) < 200:
            return self._no("v1:insufficient_bars", have=0 if df is None else len(df), need=200)

        try:
            global_regime = global_regime or {}

            # 1) Veri hazırlığı
            # Not: numeric coercion ile sessiz NaN temizliği
            df = df.copy()
            for c in ("close", "high", "low", "volume"):
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df = df.dropna(subset=["close", "high", "low", "volume"])
            if len(df) < 200:
                return self._no("v1:cleaned_df_too_short", have=len(df), need=200)

            np_close = df["close"].values.astype(float)
            np_high = df["high"].values.astype(float)
            np_low = df["low"].values.astype(float)
            np_volume = df["volume"].values.astype(float)
            volume_series = df["volume"].astype(float)

            # 2) İndikatörler
            current_price = float(np_close[-1])

            if HAS_TALIB:
                ema50 = float(ta.EMA(np_close, timeperiod=50)[-1])
                ema200 = float(ta.EMA(np_close, timeperiod=200)[-1])
                rsi = float(ta.RSI(np_close, timeperiod=14)[-1])
                atr = float(ta.ATR(np_high, np_low, np_close, timeperiod=14)[-1])

                fastk, fastd = ta.STOCHRSI(
                    np_close, timeperiod=14, fastk_period=3, fastd_period=3, fastd_matype=0
                )
                stoch_k = float(fastk[-1]) if len(fastk) and not np.isnan(fastk[-1]) else 50.0
                stoch_d = float(fastd[-1]) if len(fastd) and not np.isnan(fastd[-1]) else 50.0

                obv = ta.OBV(np_close, np_volume)
                obv_slope = float(ta.LINEARREG_SLOPE(obv, timeperiod=14)[-1]) if len(obv) >= 14 else 0.0
            else:
                close_s = df["close"].astype(float)
                ema50 = float(self._ema(close_s, 50).iloc[-1])
                ema200 = float(self._ema(close_s, 200).iloc[-1])
                rsi = float(self._rsi(close_s, 14).iloc[-1])
                atr = float(self._atr(df, 14).iloc[-1])

                # Basit fallback stochRSI/OBV: eksik kalır ama strateji çökmez
                stoch_k, stoch_d = 50.0, 50.0
                obv_slope = 0.0

            # Hacim oranı (daha güvenli: vol_ma NaN olabilir)
            vol_ma = float(volume_series.rolling(24).mean().iloc[-1]) if len(volume_series) >= 24 else float("nan")
            current_vol = float(np_volume[-1])
            vol_ratio = (current_vol / vol_ma) if (math.isfinite(vol_ma) and vol_ma > 0) else 0.0

            # 3) PUANLAMA (mevcut mantık korunarak küçük kalite iyileştirmeleri)
            base_score = 0.0

            # Trend puanı
            if current_price > ema50:
                base_score += 15
            if current_price > ema200:
                base_score += 15

            # RSI
            if 40 < rsi < 70:
                base_score += 20
            elif rsi >= 70:
                base_score += 10

            # Hacim ve OBV (vol_ratio aşırı uçsa manipülasyon olabilir -> bonusu sınırlayalım)
            if vol_ratio > 1.2:
                base_score += 10
            if obv_slope > 0:
                base_score += 15

            # StochRSI zamanlaması
            if stoch_d < stoch_k < 80:
                base_score += 15
            if stoch_k < 20:
                base_score += 10

            # 4) ADAPTIVE TUNER  ✅ (kritik bug fix: self.params['V1'] değil, direkt self.params)
            learned_threshold = float(self._p("min_score", self.default_min_score))

            # 5) REJİM MANTIĞI (akış korunuyor)
            regime_result = self._apply_regime_logic(
                current_price=current_price,
                ema200=ema200,
                global_regime=global_regime,
                base_score=base_score,
                learned_threshold=learned_threshold,
            )

            final_score = float(regime_result["final_score"])
            direction = str(regime_result["direction"])
            required_threshold = float(regime_result["threshold"])
            local_trend = str(regime_result["local_trend"])

            # Cache update
            self._last_signal_meta[symbol] = {
                "score": final_score,
                "direction": direction,
                "rsi": rsi,
                "vol_ratio": vol_ratio,
            }

            # 6) KARAR + SL/TP
            if final_score >= required_threshold:
                # Ek güvenlik (mevcut mantık)
                if direction == "SHORT" and rsi < 35:
                    return self._no("v1:short_blocked_low_rsi", rsi=rsi)
                if direction == "LONG" and rsi > 75:
                    return self._no("v1:long_blocked_high_rsi", rsi=rsi)

                # Global trade type filtresi (V2 ile aynı mimari): manager'dan gelir
                gtt = str(global_regime.get("global_trade_type") or self._ctx("global_trade_type") or "BOTH").upper()
                if direction == "LONG" and gtt == "SHORT":
                    return self._no("v1:blocked_by_global_trade_type", direction=direction, gtt=gtt)

                # ATR bazlı SL/TP
                sl_multiplier = float(self._p("sl_atr_mult_v1", 2.0) or 2.0)
                if not (math.isfinite(atr) and atr > 0):
                    # ATR bozuksa yüzde fallback (SL + TP)
                    sl_pct = float(self._p("stop_loss_pct_v1", 3.0) or 3.0) / 100.0
                    tp_list = self._p("take_profit_list_v1", [1.0, 2.0, 3.5, 5.0, 8.0]) or [1.0, 2.0, 3.5, 5.0, 8.0]

                    if direction == "LONG":
                        stop_loss = current_price * (1 - sl_pct)
                        targets = [current_price * (1 + float(p) / 100.0) for p in list(tp_list)[:5]]
                    else:
                        stop_loss = current_price * (1 + sl_pct)
                        targets = [current_price * (1 - float(p) / 100.0) for p in list(tp_list)[:5]]
                else:
                    if direction == "LONG":
                        stop_loss = current_price - (atr * sl_multiplier)
                        targets = [current_price + (atr * mult) for mult in [1.5, 2.5, 3.5, 5.0, 8.0]]
                    else:
                        stop_loss = current_price + (atr * sl_multiplier)
                        targets = [current_price - (atr * mult) for mult in [1.5, 2.5, 3.5, 5.0, 8.0]]

                return {
                    "signal": True,
                    "strategy_id": "v1",
                    "direction": direction,
                    "score": final_score,
                    "raw_score": base_score,
                    "price": current_price,
                    "entry_price": current_price,
                    "stop_loss": float(stop_loss),
                    "targets": [float(x) for x in targets],
                    "meta": {
                        "regime": f"{global_regime.get('trend', 'N/A')}|{local_trend}",
                        "global_trade_type": gtt,
                        "vol_ratio": round(float(vol_ratio), 2),
                        "rsi": round(float(rsi), 2),
                        "stoch_k": round(float(stoch_k), 2),
                        "stoch_d": round(float(stoch_d), 2),
                        "obv_slope": round(float(obv_slope), 4),
                        "atr": round(float(atr), 6) if math.isfinite(atr) else None,
                        "threshold_used": required_threshold,
                        "calc_method": {"tp": "ATR Multiplier", "sl": f"{sl_multiplier}xATR"},
                    },
                }

            return self._no(
                "v1:no_trigger",
                score=float(final_score),
                raw_score=float(base_score),
                threshold=float(required_threshold),
                direction=str(direction),
                rsi=float(rsi),
                vol_ratio=float(vol_ratio),
                local_trend=str(local_trend),
                global_trend=str((global_regime or {}).get("trend", "N/A")),
            )

        except Exception as e1:
            logger.error(f"[StrategyV1.analyze] Hata: {e1}", exc_info=True)
            return self._no("v1:exception", err=str(e1)[:200])

    @staticmethod
    def _no(reason: str, **meta):
        m = {"reason":reason}
        m.update(meta or {})
        return {"signal":False, "reason":reason, "meta":m}

    @staticmethod
    def _apply_regime_logic(current_price, ema200, global_regime, base_score, learned_threshold):
        """
        Orijinal akış korunmuştur.
        Sadece global_regime yoksa varsayılan UP davranışı aynı.
        """
        global_trend = global_regime.get("trend", "UP") if global_regime else "UP"
        local_trend = "BULL" if current_price > ema200 else "BEAR"

        if global_trend == "UP":
            if local_trend == "BULL":
                direction = "LONG"
                modifier = +5
                threshold = learned_threshold - 5
            else:
                direction = "LONG"
                modifier = -5
                threshold = learned_threshold + 5
        else:
            if local_trend == "BEAR":
                direction = "SHORT"
                modifier = +5
                threshold = learned_threshold - 2
            else:
                direction = "LONG"
                modifier = -8
                threshold = learned_threshold + 7

        return {
            "final_score": base_score + modifier,
            "direction": direction,
            "threshold": threshold,
            "local_trend": local_trend,
        }

    # ------------------------------------------------------------------
    # Public (Weighted Logic) - monitor_symbols ile uyumlu
    # ------------------------------------------------------------------
    def generate_signal(self, df, market_regime: str = "Yatay", global_trade_type: Optional[str] = None):
        """
        Ağırlıklı skor mantığı – Alternatif Sinyal Üretici
        Akış korunmuştur, ancak:
          - alarm_strateji import kaldırıldı (circular risk yok)
          - global_trade_type param veya runtime_context ile filtrelenir
          - TA-Lib yoksa güvenli fallback
        """
        try:
            if df is None or len(df) < 50:
                return None

            df = df.copy()
            for c in ("close", "high", "low", "volume"):
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
            df = df.dropna(subset=["close", "high", "low"])
            if len(df) < 50:
                return None

            np_close = df["close"].values.astype(float)
            if len(np_close) < 25:
                return None

            # RSI
            if HAS_TALIB:
                rsi_vals = ta.RSI(np_close, timeperiod=14)
                cur_rsi = float(rsi_vals[-1]) if len(rsi_vals) and not np.isnan(rsi_vals[-1]) else 50.0
            else:
                cur_rsi = float(self._rsi(df["close"], 14).iloc[-1]) if len(df) >= 20 else 50.0
                if not math.isfinite(cur_rsi):
                    cur_rsi = 50.0

            # MACD line/signal
            if HAS_TALIB:
                macd_l, macd_s, _ = ta.MACD(np_close, 12, 26, 9)
                macd_lc = float(macd_l[-1]) if len(macd_l) and not np.isnan(macd_l[-1]) else 0.0
                macd_sc = float(macd_s[-1]) if len(macd_s) and not np.isnan(macd_s[-1]) else 0.0
            else:
                # fallback: EMA farklarıyla basit macd
                ema12 = self._ema(df["close"], 12)
                ema26 = self._ema(df["close"], 26)
                macd_line = ema12 - ema26
                signal_line = macd_line.ewm(span=9, adjust=False, min_periods=9).mean()
                macd_lc = float(macd_line.iloc[-1]) if pd.notna(macd_line.iloc[-1]) else 0.0
                macd_sc = float(signal_line.iloc[-1]) if pd.notna(signal_line.iloc[-1]) else 0.0

            # "SMMA" diye isimlendirilmiş ama EMA20 kullanıyordunuz; akış bozulmasın diye koruyoruz
            smma_close = float(df["close"].ewm(span=20, adjust=False).mean().iloc[-1])

            # Parametreler (orijinal davranış)
            w = self._p(
                "signal_weights",
                {"price": 20, "rsi": 20, "macd": 20, "momentum": 20, "smma": 20, "threshold": 60},
            )
            mom_period = max(2, int(self._p("momentum_period", 2) or 2))

            if len(df) <= mom_period + 1:
                return None

            ref_close = float(df["close"].iloc[-mom_period - 1])
            current_close = float(df["close"].iloc[-1])
            prev_close = float(df["close"].iloc[-2])

            if ref_close <= 0:
                return None

            abs_change = abs(current_close / ref_close - 1.0)

            # Koşullar
            cond_price_long = 1 if current_close > prev_close else 0
            cond_price_short = 1 if current_close < prev_close else 0
            cond_rsi = 1 if 30 < cur_rsi < 70 else 0
            cond_macd_long = 1 if macd_lc > macd_sc else 0
            cond_macd_short = 1 if macd_lc < macd_sc else 0
            cond_momentum = 1 if abs_change > float(self._p("momentum_threshold", 0.02) or 0.02) else 0
            cond_smma_long = 1 if current_close > smma_close else 0
            cond_smma_short = 1 if current_close < smma_close else 0

            long_score = (
                cond_price_long * float(w.get("price", 20)) +
                cond_rsi * float(w.get("rsi", 20)) +
                cond_macd_long * float(w.get("macd", 20)) +
                cond_momentum * float(w.get("momentum", 20)) +
                cond_smma_long * float(w.get("smma", 20))
            )
            short_score = (
                cond_price_short * float(w.get("price", 20)) +
                cond_rsi * float(w.get("rsi", 20)) +
                cond_macd_short * float(w.get("macd", 20)) +
                cond_momentum * float(w.get("momentum", 20)) +
                cond_smma_short * float(w.get("smma", 20))
            )

            threshold = float(w.get("threshold", 60))

            # En yüksek skoru seç (orijinal davranış)
            if long_score >= threshold or short_score >= threshold:
                if long_score >= short_score:
                    direction = "LONG"
                    score_used = long_score
                else:
                    direction = "SHORT"
                    score_used = short_score
            else:
                return None

            # Piyasa rejimi filtresi (korundu)
            if market_regime == "Yükseliş" and direction == "SHORT":
                return None
            if market_regime == "Düşüş" and direction == "LONG":
                return None

            # Global trade type filtresi (alarm_strateji yerine manager/context)
            gtt = str(global_trade_type or self._ctx("global_trade_type") or "BOTH").upper()
            if direction == "LONG" and gtt == "SHORT":
                return None
            if direction == "SHORT" and gtt == "LONG":
                return self._no("v1:blocked_by_global_trade_type", direction=direction, gtt=gtt)

            entry = current_close
            stop = self._compute_stop(entry, direction, df)
            targets = self._atr_targets(entry, direction, df)

            meta = {
                "weighted": True,
                "rsi": float(cur_rsi),
                "momentum_abs": float(abs_change),
                "long_score": round(float(long_score), 3),
                "short_score": round(float(short_score), 3),
                "threshold": float(threshold),
                "market_regime": market_regime,
                "global_trade_type": gtt,
            }

            return {
                "type": direction,
                "score": round(float(score_used), 2),
                "entry": float(entry),
                "stop": float(stop) if stop is not None else None,
                "targets": [float(x) for x in (targets[:5] if targets else [])],
                "meta": meta,
            }

        except Exception as main_err:
            logging.error(f"[StrategyV1.generate_signal] hata: {main_err}", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Helpers (Stop/Targets) - akış korunarak güvenlik iyileştirildi
    # ------------------------------------------------------------------
    def _compute_stop(self, entry, signal, input_df):
        """
        ATR bazlı dinamik stop loss hesaplama (Güvenli)
        - runtime_params varsa onu kullanır, yoksa params'a düşer.
        """
        try:
            entry = float(entry)

            atr_len = int(self._rp("atr_len", 14) or 14)
            atr_vals = self._atr(input_df, atr_len)
            current_atr = float(atr_vals.iloc[-1]) if len(atr_vals) and pd.notna(atr_vals.iloc[-1]) else (entry * 0.02)

            atr_multiplier = float(self._rp("atr_multiplier", 2.0) or 2.0)
            atr_stop = current_atr * atr_multiplier

            pct_stop = float(self._rp("stop_loss", 3.0) or 3.0) / 100.0

            if signal == "LONG":
                # Daha TIGHT (entry'ye daha yakın) stop seç
                stop = max(round(entry * (1 - pct_stop), 8), round(entry - atr_stop, 8))
            else:
                # SHORT'ta da daha TIGHT stop seç
                stop = min(round(entry * (1 + pct_stop), 8), round(entry + atr_stop, 8))

            return float(stop)

        except Exception as calc_err:
            logging.error(f"Stop loss hesaplama hatası: {calc_err}", exc_info=True)
            return float(round(entry * 0.97, 8) if signal == "LONG" else round(entry * 1.03, 8))

    def _atr_targets(self, entry, signal, df):
        """
        ATR Bazlı Hedef Hesaplama
        """
        try:
            entry = float(entry)
            look = int(max(5, self._rp("atr_lookback", 14) or 14))

            atr_series = self._atr(df, look)
            atr = float(atr_series.iloc[-1]) if len(atr_series) and pd.notna(atr_series.iloc[-1]) else 0.0

            if not (math.isfinite(atr) and atr > 0):
                return self._fib_targets(entry, signal, df)

            mults = self._rp(
                "atr_multipliers_long" if signal == "LONG" else "atr_multipliers_short",
                [0.6, 1.0, 1.6, 2.3, 3.2],
            )

            out: List[float] = []
            for m in list(mults)[:5]:
                m = float(m)
                tgt = entry + atr * m if signal == "LONG" else entry - atr * m
                out.append(float(round(tgt, 8)))

            return out[:5]

        except Exception as calc_err:
            logging.error(f"[StrategyV1] atr target hata: {calc_err}", exc_info=True)
            return self._fib_targets(entry, signal, df)

    def _fib_targets(self, entry, signal, df):
        """
        Fibonacci Fallback Hedefleri
        - KRİTİK BUG FIX: low.max() -> low.min()
        - Akış korunuyor, sadece hesap düzeltildi.
        """
        try:
            entry = float(entry)
            fibs = self._rp("fibonacci_levels", [0.236, 0.382, 0.618, 0.786, 1.0])

            if len(df) >= 50:
                hi = float(pd.to_numeric(df["high"].iloc[-50:], errors="coerce").max())
                lo = float(pd.to_numeric(df["low"].iloc[-50:], errors="coerce").min())
            else:
                hi = float(pd.to_numeric(df["high"], errors="coerce").max())
                lo = float(pd.to_numeric(df["low"], errors="coerce").min())

            out: List[float] = []
            if signal == "LONG":
                rng = hi - entry
                if rng <= 0:
                    return [round(entry * (1 + p), 8) for p in [0.02, 0.04, 0.06, 0.08, 0.10]]
                for f in list(fibs)[:5]:
                    out.append(round(entry + rng * float(f), 8))
            else:
                rng = entry - lo
                if rng <= 0:
                    return [round(entry * (1 - p), 8) for p in [0.02, 0.04, 0.06, 0.08, 0.10]]
                for f in list(fibs)[:5]:
                    out.append(round(entry - rng * float(f), 8))

            return [float(x) for x in out[:5]]

        except (ValueError, TypeError, KeyError, IndexError):
            return [round(entry * 1.02, 8), round(entry * 1.04, 8)]

    @property
    def id(self):
        return self.strategy_id
