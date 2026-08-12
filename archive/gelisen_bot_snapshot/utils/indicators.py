# utils/indicators.py
from __future__ import annotations
import numpy as np
import pandas as pd


def _to_series(arr) -> pd.Series:
    """
    Girdiyi pandas Series'e dönüştürür.

    Args:
        arr: Dönüştürülecek dizi veya pandas Series

    Returns:
        pd.Series: Dönüştürülmüş seri
    """
    if isinstance(arr, pd.Series):
        return arr
    return pd.Series(arr)


def smma(series: pd.Series, period: int) -> pd.Series:
    """
    Düzleştirilmiş Hareketli Ortalama (SMMA) hesaplar.

    Args:
        series (pd.Series): Hesaplama yapılacak seri
        period (int): Ortalama periyodu

    Returns:
        pd.Series: SMMA serisi
    """
    # Serinin kopyasını al ve float'a dönüştür
    series = _to_series(series).astype(float)

    # Periyot kontrolü
    if period <= 1 or len(series) < period:
        return pd.Series([np.nan] * len(series), index=series.index)

    # İlk periyot için seed değeri hesapla
    seed = series.iloc[:period].mean()

    # SMMA hesaplaması için daha güvenli bir yaklaşım
    smma_series = series.copy()

    # İlk period'a kadar NaN doldur
    smma_series.iloc[:period - 1] = np.nan

    # İlk period'dan sonrası için SMMA hesaplaması
    for i in range(period - 1, len(series)):
        if i == period - 1:
            # İlk SMMA değeri
            smma_series.iloc[i] = seed
        else:
            # Sonraki SMMA değerleri
            prev_smma = smma_series.iloc[i - 1]
            current_value = series.iloc[i]
            smma_series.iloc[i] = (prev_smma * (period - 1) + current_value) / period

    return smma_series


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """
    Relatif Güç Endeksi (RSI) hesaplar.

    Geliştirilmiş versiyon, daha güvenli hesaplama yapar.
    """
    close = _to_series(close).astype(float)

    # Yetersiz veri kontrolü
    if len(close) < period + 1:
        return pd.Series([np.nan] * len(close), index=close.index)

    # Delta hesaplama
    delta = close.diff()

    # Kazanç ve kayıpları hesapla
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    # Ortalama kazanç ve kayıpları hesapla (ilk period için)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()

    # RS ve RSI hesaplama
    rs = avg_gain / avg_loss
    rsi_val = 100.0 - (100.0 / (1.0 + rs))

    # NaN değerleri doldur
    rsi_val = rsi_val.fillna(50.0)

    return rsi_val


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Moving Average Convergence Divergence (MACD) hesaplar.
    Daha esnek ve hata ayıklamaya uygun versiyon.
    """
    close = _to_series(close).astype(float)

    # Yetersiz veri kontrolü
    if len(close) < slow + signal:
        empty = pd.Series([np.nan] * len(close), index=close.index)
        return empty, empty, empty

    # EMA hesaplamaları için daha güvenli yaklaşım
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()

    # MACD hattı
    macd_line = ema_fast - ema_slow

    # Sinyal hattı
    macd_signal = macd_line.ewm(span=signal, adjust=False).mean()

    # Histogram
    hist = macd_line - macd_signal

    return macd_line, macd_signal, hist
