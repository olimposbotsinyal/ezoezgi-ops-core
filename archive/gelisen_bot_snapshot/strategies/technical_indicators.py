# strategies/technical_indicators.py
"""
✅ Teknik göstergelerin doğru hesaplanması (Yeni yapı uyumlu)
- calculate_all artık min_length parametresi alır (scan timeframe'lerine uyum).
- Veri temizleme/validasyon daha toleranslı ve tutarlı.
"""
from __future__ import annotations

import logging
from typing import Dict, Tuple, Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# --- Optional TA-Lib ---
try:
    import talib  # type: ignore
    HAS_TALIB = True
except Exception as e:
    logger.warning("[TechnicalIndicators] TA-Lib import edilemedi: %r", e)
    talib = None  # type: ignore
    HAS_TALIB = False



class TechnicalIndicators:
    """Tüm teknik göstergeleri merkezi olarak hesapla"""

    @staticmethod
    def _to_clean_float_array(series: pd.Series) -> np.ndarray:
        """
        Sayısala çevir + ffill/bfill + float array.
        """
        s = pd.to_numeric(series, errors="coerce")
        # önce ileri doldur, baştaki NaN'ler için geri doldur
        s = s.ffill().bfill()
        return s.to_numpy(dtype=float)

    @staticmethod
    def validate_data(
        close: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        volume: np.ndarray,
        min_length: int = 60,
        recent_window: int = 20
    ) -> bool:
        """
        Veri geçerliliğini kontrol et - Geliştirilmiş ve scan uyumlu.
        - min_length: indikatörler için minimum mum sayısı
        - recent_window: son N mumda NaN/<=0 kontrolü
        """
        if close is None or high is None or low is None or volume is None:
            return False

        n = len(close)
        if n < min_length:
            logger.debug("Yetersiz veri: %s < %s", n, min_length)
            return False

        # son pencereyi güvenli seç
        w = min(recent_window, n)
        c = close[-w:]

        # fiyatlar pozitif olmalı
        if np.any(np.isnan(c)) or np.any(c <= 0):
            logger.debug("NaN veya geçersiz close (son %s mum)", w)
            return False

        # high/low tutarlılığı
        h = high[-w:]
        l = low[-w:]
        if np.any(np.isnan(h)) or np.any(np.isnan(l)):
            logger.debug("NaN high/low (son %s mum)", w)
            return False
        if np.any(h <= 0) or np.any(l <= 0):
            logger.debug("Geçersiz high/low (<=0)")
            return False
        if np.any(h < l):
            logger.debug("Tutarsız OHLC: high < low")
            return False

        # Volatilite kontrolü (tam 0 yerine çok küçük tolerans)
        if float(np.std(c)) < 1e-12:
            logger.debug("Sıfıra yakın volatilite")
            return False

        # Volume negatif olmasın
        v = volume[-w:]
        if np.any(np.isnan(v)):
            logger.debug("NaN volume (son %s mum)", w)
            return False
        if np.any(v < 0):
            logger.debug("Negatif volume")
            return False

        return True

    @staticmethod
    def calculate_rsi(close: np.ndarray, period: int = 14) -> Tuple[float, str]:
        """RSI hesapla"""
        try:
            if not HAS_TALIB:
                return 50.0, "⚪️ Nötr"

            if len(close) < period + 10:
                return 50.0, "⚪️ Nötr"

            rsi_values = talib.RSI(close, timeperiod=period)
            valid_rsi = rsi_values[~np.isnan(rsi_values)]
            if len(valid_rsi) < 5:
                return 50.0, "⚪️ Nötr"

            rsi = float(valid_rsi[-1])

            if rsi > 80:
                return rsi, "🔴 Aşırı Alım"
            elif rsi > 70:
                return rsi, "🟡 Alım"
            elif rsi < 20:
                return rsi, "🟢 Aşırı Satım"
            elif rsi < 30:
                return rsi, "🟡 Satım"
            elif rsi > 55:
                return rsi, "📈 Yükseliş"
            elif rsi < 45:
                return rsi, "📉 Düşüş"
            else:
                return rsi, "⚪️ Nötr"

        except Exception as e:
            logger.error("RSI Hesaplama Hatası: %r", e)
            return 50.0, "⚪️ Nötr"


    @staticmethod
    def calculate_adx(
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        period: int = 14
    ) -> Tuple[float, str]:
        """ADX hesapla"""
        try:
            if not HAS_TALIB:
                return 0.0, "💤 Zayıf"
            if len(close) < period + 20:
                return 0.0, "💤 Zayıf"

            adx_values = talib.ADX(high, low, close, timeperiod=period)
            valid_adx = adx_values[~np.isnan(adx_values)]
            if len(valid_adx) < 5:
                return 0.0, "💤 Zayıf"

            adx = float(valid_adx[-1])

            if adx > 50:
                return adx, "🚀 Çok Güçlü"
            elif adx > 40:
                return adx, "💪 Güçlü"
            elif adx > 30:
                return adx, "⚡️ Orta"
            elif adx > 20:
                return adx, "📊 Zayıf"
            else:
                return adx, "💤 Çok Zayıf"

        except Exception as e:
            logger.error("ADX Hesaplama Hatası: %r", e)
            return 0.0, "💤 Zayıf"

    @staticmethod
    def calculate_bollinger_bands(
            close: np.ndarray,
            period: int = 20,
            std_dev: float = 2.0
    ) -> Tuple[float, str]:
        """Bollinger Bands width + status (calculate_bollinger_details wrapper)"""
        info = TechnicalIndicators.calculate_bollinger_details(
            close=close,
            period=period,
            std_dev=std_dev
        )
        return float(info.get("bb_width", 0.0) or 0.0), str(info.get("bb_status", "🔐 Sıkışma") or "🔐 Sıkışma")

    @staticmethod
    def calculate_bollinger_details(
        close: np.ndarray,
        period: int = 20,
        std_dev: float = 2.0
    ) -> Dict[str, Any]:
        """
        Bollinger detayları:
        - upper/middle/lower
        - bb_width = (upper-lower)/middle
        - bb_percent_b = (close-lower)/(upper-lower)
        """
        try:
            if not HAS_TALIB:
                return {
                    "bb_upper": float("nan"),
                    "bb_middle": float("nan"),
                    "bb_lower": float("nan"),
                    "bb_width": 0.0,
                    "bb_percent_b": float("nan"),
                    "bb_status": "🔐 Sıkışma",
                }

            if len(close) < period + 10:
                return {
                    "bb_upper": float("nan"),
                    "bb_middle": float("nan"),
                    "bb_lower": float("nan"),
                    "bb_width": 0.0,
                    "bb_percent_b": float("nan"),
                    "bb_status": "🔐 Sıkışma",
                }

            upper, middle, lower = talib.BBANDS(
                close,
                timeperiod=period,
                nbdevup=std_dev,
                nbdevdn=std_dev
            )

            # son geçerli değerleri al
            def last_valid(a: np.ndarray) -> float:
                a2 = a[~np.isnan(a)]
                return float(a2[-1]) if len(a2) else float("nan")

            u = last_valid(upper)
            m = last_valid(middle)
            l = last_valid(lower)
            c = float(close[-1])

            width = 0.0
            if np.isfinite(u) and np.isfinite(l) and np.isfinite(m) and m != 0.0:
                width = float((u - l) / m)

            # %B
            pb = float("nan")
            denom = (u - l) if (np.isfinite(u) and np.isfinite(l)) else float("nan")
            if np.isfinite(denom) and abs(denom) > 1e-12 and np.isfinite(c):
                pb = float((c - l) / denom)
                # clamp (taşmaları loglamak istersen ayrıca loglayabiliriz)
                pb = max(0.0, min(1.0, pb))

            # status (mevcut eşiklerle uyumlu)
            if width < 0.005:
                status = "🔐 Çok Sıkı"
            elif width < 0.01:
                status = "🔐 Sıkışma"
            elif width < 0.03:
                status = "📊 Normal"
            elif width < 0.06:
                status = "📈 Geniş"
            else:
                status = "🚀 Çok Geniş"

            return {
                "bb_upper": u,
                "bb_middle": m,
                "bb_lower": l,
                "bb_width": width,
                "bb_percent_b": pb,
                "bb_status": status,
            }

        except Exception as e:
            logger.error("BB Details Hesaplama Hatası: %r", e)
            return {
                "bb_upper": float("nan"),
                "bb_middle": float("nan"),
                "bb_lower": float("nan"),
                "bb_width": 0.0,
                "bb_percent_b": float("nan"),
                "bb_status": "🔐 Sıkışma",
            }

    @staticmethod
    def calculate_stochrsi(
        close: np.ndarray,
        period: int = 14,
        fastk: int = 3,
        fastd: int = 3
    ) -> Tuple[float, float, str]:
        """StochRSI - düzeltilmiş sürüm (mevcut mantık korunuyor)"""
        try:
            if not HAS_TALIB:
                return 50.0, 50.0, "✅ Normal"

            if len(close) < period + 20:
                return 50.0, 50.0, "✅ Normal"

            rsi_values = talib.RSI(close, timeperiod=period)
            valid_mask = ~np.isnan(rsi_values)
            valid_rsi = rsi_values[valid_mask].astype(float)

            if len(valid_rsi) < fastk + fastd:
                return 50.0, 50.0, "✅ Normal"

            available_data = min(len(valid_rsi), period * 2)
            rsi_arr = valid_rsi[-available_data:]

            stoch_k_values = []
            for i in range(len(rsi_arr) - fastk + 1):
                window = rsi_arr[i:i + fastk]
                current_rsi = rsi_arr[i + fastk - 1]
                low_val = float(np.min(window))
                high_val = float(np.max(window))

                if abs(high_val - low_val) > 1e-10:
                    stoch_value = ((current_rsi - low_val) / (high_val - low_val)) * 100.0
                    stoch_k_values.append(float(stoch_value))
                else:
                    stoch_k_values.append(50.0)

            if len(stoch_k_values) < fastd:
                return 50.0, 50.0, "✅ Normal"

            stoch_k_array = np.array(stoch_k_values, dtype=float)
            stoch_d_values = []
            for i in range(len(stoch_k_array) - fastd + 1):
                d_window = stoch_k_array[i:i + fastd]
                stoch_d_values.append(float(np.mean(d_window)))

            if not stoch_d_values:
                return 50.0, 50.0, "✅ Normal"

            k_val = float(stoch_k_array[-1])
            d_val = float(stoch_d_values[-1])

            if k_val > 80 or d_val > 80:
                status = "🔴 Aşırı Alım"
            elif k_val < 20 or d_val < 20:
                status = "🟢 Aşırı Satım"
            elif k_val > 65 or d_val > 65:
                status = "🟡 Alım"
            elif k_val < 35 or d_val < 35:
                status = "🟡 Satım"
            else:
                status = "✅ Normal"

            return k_val, d_val, status

        except Exception as e:
            logger.error("StochRSI Hesaplama Hatası: %r", e)
            return 50.0, 50.0, "✅ Normal"

    @staticmethod
    def calculate_momentum(close: np.ndarray, period: int = 10) -> Tuple[float, str]:
        """Momentum (Fiyat Değişim %)"""
        try:
            if len(close) <= period:
                return 0.0, "💤 Düşük"

            close_last = float(close[-1])
            close_prev = float(close[-period - 1])
            if close_prev == 0:
                return 0.0, "💤 Düşük"

            momentum = abs((close_last - close_prev) / close_prev * 100.0)

            if momentum > 5.0:
                return momentum, "🚀 Çok Yüksek"
            elif momentum > 2.0:
                return momentum, "📊 Yüksek"
            elif momentum > 0.5:
                return momentum, "✅ Normal"
            else:
                return momentum, "💤 Düşük"

        except Exception as e:
            logger.error("Momentum Hesaplama Hatası: %r", e)
            return 0.0, "💤 Düşük"

    @staticmethod
    def calculate_volume_ratio(
        volume: np.ndarray,
        period_short: int = 20,
        period_long: int = 60
    ) -> Tuple[float, str]:
        """Hacim Oranı"""
        try:
            if len(volume) < period_long:
                return 1.0, "⚪️ Normal"

            v_short = float(np.mean(volume[-period_short:]))
            v_long = float(np.mean(volume[-period_long:]))

            if v_long == 0:
                return 1.0, "⚪️ Normal"

            ratio = v_short / v_long

            if ratio > 2.0:
                return ratio, "📊 Çok Yüksek"
            elif ratio > 1.5:
                return ratio, "📊 Yüksek"
            elif ratio > 1.0:
                return ratio, "✅ Normal"
            else:
                return ratio, "⚪️ Normal"

        except Exception as e:
            logger.error("Volume Ratio Hesaplama Hatası: %r", e)
            return 1.0, "⚪️ Normal"

    @staticmethod
    def calculate_obv(close: np.ndarray, volume: np.ndarray) -> Tuple[float, str]:
        """OBV slope"""
        try:
            if not HAS_TALIB:
                return 0.0, "⚪️ Nötr"

            if len(close) < 2:
                return 0.0, "⚪️ Nötr"

            obv_arr = talib.OBV(close, volume)
            obv_current = float(obv_arr[-1])
            obv_prev = float(obv_arr[-2])
            obv_slope = obv_current - obv_prev

            if obv_slope > 0:
                return obv_slope, "📈 Yükselen"
            elif obv_slope < 0:
                return obv_slope, "📉 Düşen"
            else:
                return 0.0, "⚪️ Nötr"

        except Exception as e:
            logger.error("OBV Hesaplama Hatası: %r", e)
            return 0.0, "⚪️ Nötr"

    @staticmethod
    def calculate_ema(close: np.ndarray, period: int = 200) -> Tuple[float, str]:
        """EMA + trend"""
        try:
            if not HAS_TALIB:
                return float(close[-1]), "⚪️ Bilinmiyor"

            if len(close) < period:
                return float(close[-1]), "⚪️ Bilinmiyor"

            ema = talib.EMA(close, timeperiod=period)[-1]
            if np.isnan(ema):
                return float(close[-1]), "⚪️ Bilinmiyor"

            ema = float(ema)
            close_last = float(close[-1])

            tolerance = 0.001  # 0.1%
            if close_last > ema * (1 + tolerance):
                return ema, "📈 Yükseliş"
            elif close_last < ema * (1 - tolerance):
                return ema, "📉 Düşüş"
            else:
                return ema, "⚪️ Nötr"

        except Exception as e:
            logger.error("EMA Hesaplama Hatası: %r", e)
            return float(close[-1]), "⚪️ Bilinmiyor"

    @classmethod
    def calculate_all(
        cls,
        df: pd.DataFrame,
        symbol: str,
        min_length: int = 60
    ) -> Dict[str, Any]:
        """
        Tüm göstergeleri hesapla (yeni yapı uyumlu).

        min_length:
        - AI scan'de 60 genelde yeterli
        - Strategy scan'de istersen 100 verebilirsin
        """
        try:
            if not HAS_TALIB:
                logger.warning("[%s] TA-Lib yok: default indicators dönülüyor", symbol)
                return cls.get_default_indicators()

            # Kolon kontrolü
            for col in ("close", "high", "low", "volume"):
                if col not in df.columns:
                    logger.debug("[%s] Eksik kolon: %s", symbol, col)
                    return cls.get_default_indicators()

            # Temiz numpy array’ler
            close = cls._to_clean_float_array(df["close"])
            high = cls._to_clean_float_array(df["high"])
            low = cls._to_clean_float_array(df["low"])
            volume = cls._to_clean_float_array(df["volume"])

            # NaN son kontrol (özellikle son 50)
            tail_n = min(50, len(close))
            if (
                np.any(np.isnan(close[-tail_n:])) or
                np.any(np.isnan(high[-tail_n:])) or
                np.any(np.isnan(low[-tail_n:])) or
                np.any(np.isnan(volume[-tail_n:]))
            ):
                logger.debug("[%s] NaN değerler tespit edildi", symbol)
                return cls.get_default_indicators()

            # Veri doğrulama
            if not cls.validate_data(close, high, low, volume, min_length=min_length):
                logger.debug("[%s] Veri doğrulama başarısız (min_length=%s)", symbol, min_length)
                return cls.get_default_indicators()

            # Göstergeler
            rsi, rsi_status = cls.calculate_rsi(close)
            adx, adx_status = cls.calculate_adx(high, low, close)
            bb_info = cls.calculate_bollinger_details(close)
            bb_width = float(bb_info.get("bb_width", 0.0) or 0.0)
            bb_status = str(bb_info.get("bb_status", "🔐 Sıkışma") or "🔐 Sıkışma")

            stoch_k, stoch_d, stoch_status = cls.calculate_stochrsi(close)
            momentum, momentum_status = cls.calculate_momentum(close)
            vol_ratio, vol_status = cls.calculate_volume_ratio(volume)
            obv_slope, obv_status = cls.calculate_obv(close, volume)
            ema_200, local_trend = cls.calculate_ema(close, period=200)

            last_close = float(close[-1])
            last_vol = float(volume[-1])

            results: Dict[str, Any] = {
                "close": last_close,

                # ⚠️ Bu gerçek USD hacim değildir; son mum için proxy'dir.
                # Gerçek volume_usd için dışarıda ticker (quoteVolume) baz alınmalı.
                "volume_usd": float(last_close * last_vol),

                "rsi": rsi,
                "rsi_status": rsi_status,
                "adx": adx,
                "adx_status": adx_status,

                "bb_width": bb_width,
                "bb_status": bb_status,
                "bb_upper":bb_info.get("bb_upper", float("nan")),
                "bb_middle":bb_info.get("bb_middle", float("nan")),
                "bb_lower":bb_info.get("bb_lower", float("nan")),
                "bb_percent_b":bb_info.get("bb_percent_b", float("nan")),

                "stoch_k": stoch_k,
                "stoch_d": stoch_d,
                "stoch_status": stoch_status,
                "momentum_tension": momentum,
                "momentum_status": momentum_status,
                "volume_ratio": vol_ratio,
                "volume_status": vol_status,
                "obv_slope": obv_slope,
                "obv_status": obv_status,
                "ema_200": ema_200,
                "local_trend": local_trend,
            }

            logger.debug(
                "[%s] indicators: RSI=%.1f ADX=%.1f BBw=%.4f %%B=%s StochK=%.1f VR=%.2f",
                symbol,
                results["rsi"],
                results["adx"],
                results["bb_width"],
                (f'{results.get("bb_percent_b"):.4f}' if isinstance(results.get("bb_percent_b"), float) and np.isfinite(
                    results.get("bb_percent_b")) else "nan"),
                results["stoch_k"],
                results["volume_ratio"],
            )

            return results

        except Exception as e:
            logger.error("[%s] Gösterge Hesaplama Hatası: %r", symbol, e, exc_info=True)
            return cls.get_default_indicators()

    @staticmethod
    def get_default_indicators() -> Dict[str, Any]:
        """Varsayılan göstergeler"""

        return {
            "close": 0.0,
            "volume_usd": 0.0,
            "rsi": 50.0,
            "rsi_status": "⚪️ Nötr",
            "adx": 0.0,
            "adx_status": "💤 Zayıf",
            "bb_width": 0.0,
            "bb_status": "🔐 Sıkışma",
            "bb_upper":float("nan"),
            "bb_middle":float("nan"),
            "bb_lower":float("nan"),
            "bb_percent_b":float("nan"),
            "stoch_k": 50.0,
            "stoch_d": 50.0,
            "stoch_status": "✅ Normal",
            "momentum_tension": 0.0,
            "momentum_status": "💤 Düşük",
            "volume_ratio": 1.0,
            "volume_status": "⚪️ Normal",
            "obv_slope": 0.0,
            "obv_status": "⚪️ Nötr",
            "ema_200": 0.0,
            "local_trend": "⚪️ Bilinmiyor",
        }
