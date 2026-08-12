# market_data.py

import logging
import pandas as pd
import asyncio


class MarketDataService:
    """
    Asenkron exchange (ccxt.async_support) kullanan sistem için
    basit veri erişim adaptörü.
    """
    def __init__(self, exchange, logger=None):
        self.exchange = exchange
        # Sınıf adını logger ismi olarak kullan
        self.logger = logger or logging.getLogger(self.__class__.__name__)

    async def fetch_tickers_async(self):
        try:
            return await self.exchange.fetch_tickers()
        except Exception as e:
            self.logger.error(f"[MarketData] fetch_tickers hata: {e}")
            await asyncio.sleep(0.5)

            return {}

    async def fetch_ohlcv_async(self, symbol: str, timeframe: str = '15m', limit: int = 200):
        try:
            raw = await self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            if not raw or len(raw) == 0:
                return None
            df = pd.DataFrame(raw, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            return df
        except Exception as e:
            self.logger.error(f"[MarketData] fetch_ohlcv {symbol} hata: {e}")
            return None
