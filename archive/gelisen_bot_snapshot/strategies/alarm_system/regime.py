# strategies/alarm_system/regime.py
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
import numpy as np
import pandas as pd
# Telegram imports
from telegram.ext import CallbackContext
import logging
from core.strategy_manager import StrategyManager as SMRef
from config_service import ConfigService
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


async def _get_market_regime(cls, context: CallbackContext) -> str:
        """
        Mevcut piyasa rejimini döndürür. Gerekirse günceller.

        Args:
            context: CallbackContext - user_data içinde api_key, secret_key, passphrase bulunmalı

        Returns:
            str: Mevcut piyasa rejimi ('Yükseliş', 'Düşüş', 'Yatay')
        """
        now = datetime.now(timezone.utc)

        # Regime check aralığı geçtiyse güncelle
        if (
                cls._last_regime_check is None
                or (now - cls._last_regime_check).total_seconds() > cls.REGIME_CHECK_INTERVAL_MIN * 60
        ):
            # ✅ context'ten gerekli parametreleri al
            api_key = context.user_data.get('api_key', '')
            secret_key = context.user_data.get('secret_key', '')
            passphrase = context.user_data.get('passphrase')
            user_id = context.user_data.get('user_id', 0)
            exchange_name = context.user_data.get('selected_exchange', 'mexc')  # DÜZELTME: 'selected_exchange' kullan

            # ✅ Parametreleri _update_market_regime'e gönder
            await cls._update_market_regime(
                context=context,
                api_key=api_key,
                secret_key=secret_key,
                passphrase=passphrase,
                user_id=user_id,
                exchange_name=exchange_name
            )

        return cls._market_regime


async def _update_market_regime(
        cls,
        context: CallbackContext,
        api_key: str,
        secret_key: str,
        passphrase: Optional[str] = None,
        user_id: int = 0,
        exchange_name: str = 'mexc'
) -> None:
    """
    ═══════════════════════════════════════════════════════════════════════════
    Piyasa rejimini (Yükseliş/Düşüş) ana pariteye göre günceller.
    ═══════════════════════════════════════════════════════════════════════════

    PARAMETRELER:
    - context: CallbackContext (Telegram Bot API context nesnesi)
    - api_key: Exchange API anahtarı
    - secret_key: Exchange secret anahtarı
    - passphrase: Exchange passphrase (OKX/Bybit/Bitget için gerekli)
    - user_id: Kullanıcı ID
    - exchange_name: Exchange adı (varsayılan: 'mexc')

    AÇIKLAMA:
    1. ta.EMA() → NumPy ndarray döndürür (pandas Series DEĞİL)
    2. NumPy ndarray → İndeks erişimi: array[-1] (✅ Doğru)
    3. NumPy ndarray → .iloc[-1] (❌ HATA - pandas Series için)
    4. Çözüm: ndarray → pandas Series'e dönüştür veya doğrudan indeks kullan
    """

    try:
        # ═══════════════════════════════════════════════════════════════════════════
        # SATIR 1-10: EXCHANGE BAŞLATMA KONTROLÜ
        # ═══════════════════════════════════════════════════════════════════════════

        # SATIR 1: Exchange nesnesi var mı kontrol et
        if not cls.exchange:
            logger.info(
                f"⚠️ Exchange başlatılmamış, şimdi başlatılıyor: {exchange_name}"
            )

            # SATIR 2-4: API anahtarları doğrulama
            if not api_key or not secret_key:
                logger.error(
                    "❌ API anahtarları eksik, rejim analizi yapılamıyor"
                )
                cls._market_regime = "Yatay"
                return

            # SATIR 5-8: Exchange başlatma
            init_success = await cls.initialize_exchange(
                user_id=user_id,
                exchange_name=exchange_name,
                api_key=api_key,
                secret_key=secret_key,
                passphrase=passphrase,
                context=context
            )

            # SATIR 9-10: Başlatma başarısızlığı kontrolü
            if not init_success:
                logger.error(
                    "❌ Exchange başlatılamadı, rejim analizi yapılamıyor"
                )
                cls._market_regime = "Yatay"
                return

        # ═══════════════════════════════════════════════════════════════════════════
        # SATIR 11-15: OHLCV VERİLERİNİ ÇEK
        # ═══════════════════════════════════════════════════════════════════════════
        regime_tf = ConfigService.get('market_regime.timeframe', '1d')
        # SATIR 11: OHLCV verilerini retry mekanizması ile çek
        ohlcv = await cls.fetch_ohlcv_with_retry(
            symbol=cls.REGIME_SYMBOL,  # BTC/USDT
            timeframe=regime_tf,
            max_retries=2  # 2 kez dene
        )

        # SATIR 12-15: Veri kontrolü
        # - ohlcv is None: Veri çekilemedi
        # - ohlcv.empty: DataFrame boş
        # - len(ohlcv) < 200: 200 günlük veri yok
        ema_period = int(ConfigService.get('market_regime.ema_period', cls.REGIME_EMA_PERIOD))
        if ohlcv is None or ohlcv.empty or len(ohlcv) < ema_period:
            ema_period = int(ConfigService.get('market_regime.ema_period', 200))
            logger.warning(
                f"[REJİM] {cls.REGIME_SYMBOL} için rejim analizi yapılamadı "
                f"(yetersiz veri). Veri sayısı: {len(ohlcv) if ohlcv is not None else 0}, "
                f"Gerekli: {ema_period}"
            )
            cls._market_regime = "Yatay"
            return

        # ═══════════════════════════════════════════════════════════════════════════
        # SATIR 16-20: NaN DEĞERLERİNİ TEMİZLE
        # ═══════════════════════════════════════════════════════════════════════════

        # SATIR 16: NaN (Not a Number) değerleri kaldır
        # - dropna(): NaN içeren satırları siler
        # - subset=['close']: Sadece 'close' kolonunda NaN ara
        # - inplace=True: Orijinal DataFrame'i değiştir
        ohlcv.dropna(subset=['close'], inplace=True)

        # SATIR 17-20: NaN temizliği sonrası veri kontrolü

        if len(ohlcv) < ema_period:
            logger.warning(
                f"[REJİM] {cls.REGIME_SYMBOL} için rejim analizi yapılamadı "
                f"(NaN temizliği sonrası yetersiz veri). Kalan veri: {len(ohlcv)}, "
                f"Gerekli: {ema_period}"
            )
            cls._market_regime = "Yatay"
            return
        # ═══════════════════════════════════════════════════════════════════════════
        # SATIR 21-25: KAPANIŞ FİYATLARINI HAZIRLA
        # ═══════════════════════════════════════════════════════════════════════════
        # SATIR 21: 'close' kolonunu al ve float'a dönüştür
        # - ohlcv['close']: pandas Series (fiyat değerleri)
        # - .astype(float): Tüm değerleri float tipine dönüştür
        close_prices = ohlcv["close"].astype("float64")
        # ✅ TA-Lib'e ndarray ver (type-checker uyarısı biter)
        close_arr = close_prices.to_numpy(dtype=np.float64, copy=False)
        ema_arr = ta.EMA(close_arr, timeperiod=int(ema_period))  # -> np.ndarray
        # İstersen index'i koru
        ema200_series = pd.Series(ema_arr, index=ohlcv.index, dtype="float64")
        valid_ema_values = ema200_series.dropna()
        logger.debug(
            f"[EMA_CALC] EMA200 hesaplandı. Tip: {type(ema200_series)}, "
            f"Uzunluk: {len(ema200_series)}"
        )
        # SATIR 26: NaN olmayan değerleri filtrele
        # - ~np.isnan(ema200_series): NaN olmayan değerleri seç (~ = NOT operatörü)
        # - valid_ema_values: Sadece geçerli (NaN olmayan) EMA değerleri
        valid_ema_values = ema200_series.dropna()

        # SATIR 27-32: Geçerli EMA değeri kontrolü
        if len(valid_ema_values) == 0:
            logger.warning(
                f"[REJİM] {cls.REGIME_SYMBOL} için geçerli bir "
                f"EMA{ema_period} değeri hesaplanamadı. "
                f"Tüm EMA değerleri NaN'dır."
            )
            cls._market_regime = "Yatay"
            return

        # ═══════════════════════════════════════════════════════════════════════════
        # SATIR 33-38: SON GEÇERLİ DEĞERLERİ AL
        # ═══════════════════════════════════════════════════════════════════════════

        # SATIR 33: Son geçerli EMA değerini al
        # ✅ DÜZELTME: .iloc[-1] kullanabiliriz (Series'e dönüştürdük)
        # - valid_ema_values: pandas Series (NaN olmayan değerler)
        # - .iloc[-1]: Son elemanı al
        # - float(): Float tipine dönüştür
        last_ema = float(valid_ema_values.iloc[-1])

        # SATIR 34: Son kapaniş fiyatını al
        # - close_prices.iloc[-1]: Son fiyat (pandas Series)
        # - float(): Float tipine dönüştür
        last_price = float(close_prices.iloc[-1])

        logger.debug(
            f"[LAST_VALUES] Son Fiyat: {last_price:.2f}, "
            f"Son EMA{ema_period}: {last_ema:.2f}"
        )

        # ═══════════════════════════════════════════════════════════════════════════
        # SATIR 35-45: PİYASA REJİMİNİ BELİRLE
        # ═══════════════════════════════════════════════════════════════════════════

        # SATIR 35: Fiyat > EMA200 * 1.01 (EMA'nın %1 üstünde)
        if last_price > last_ema * 1.01:
            cls._market_regime = "Yükseliş"
            regime_signal = "📈 YÜKSELIŞ"

        # SATIR 36: Fiyat < EMA200 * 0.99 (EMA'nın %1 altında)
        elif last_price < last_ema * 0.99:
            cls._market_regime = "Düşüş"
            regime_signal = "📉 DÜŞÜŞ"

        # SATIR 37: Fiyat EMA200 civarında (±%1)
        else:
            cls._market_regime = "Yatay"
            regime_signal = "➡️ YATAY"

        # SATIR 46: Son kontrol zamanını kaydet
        # - datetime.now(timezone.utc): Şu anki UTC zamanı
        cls._last_regime_check = datetime.now(timezone.utc)

        # ═══════════════════════════════════════════════════════════════════════════
        # SATIR 49-55: BAŞARI MESAJI LOGA YAZ
        # ═══════════════════════════════════════════════════════════════════════════

        # SATIR 49-55: Detaylı log mesajı
        price_ema_diff_percent = ((last_price / last_ema - 1) * 100)

        logger.info(
            f"{regime_signal} Piyasa Rejimi Güncellendi: {cls._market_regime} "
            f"(Fiyat: {last_price:.2f}, EMA{ema_period}: {last_ema:.2f}, "
            f"Fark: {price_ema_diff_percent:.2f}%)"
        )

    except Exception as e:
        # ═══════════════════════════════════════════════════════════════════════════
        # SATIR 56-58: HATA YÖNETIMI
        # ═══════════════════════════════════════════════════════════════════════════

        logger.error(
            f"[REJİM] Piyasa rejimi güncellenirken hata: {e}",
            exc_info=True  # Stack trace'i de yazdır
        )
        cls._market_regime = "Yatay"
