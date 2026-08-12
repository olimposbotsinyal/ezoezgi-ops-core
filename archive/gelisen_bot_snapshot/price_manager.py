# price_manager.py
import ccxt
import asyncio
import logging
import functools
from typing import Dict, List, Any
from config.constants import Exchange


class ExchangeSymbolDiscovery:
    @staticmethod
    async def discover_valid_symbols(
            exchange_instance: ccxt.Exchange,
            base_symbol: str,
            logger: logging.Logger
    ) -> List[str]:
        """
        Borsadaki geçerli sembol ve sözleşmeleri keşfet
        """
        try:
            # Market listesini yükle - run_in_executor kullanımı
            loop = asyncio.get_running_loop()
            markets = await loop.run_in_executor(
                None,
                functools.partial(exchange_instance.load_markets)
            )

            # USDT sonekini kaldır
            clean_base_symbol = base_symbol.replace('USDT', '').upper()

            # Tam eşleşme ve kısmi eşleşme listeleri
            exact_matches = []
            partial_matches = []

            for market_symbol, market_info in markets.items():
                # Sembol kontrol kriterleri - tüm semboller için
                conditions = [
                    market_info.get('type') in ['swap', 'future'],
                    'USDT' in market_symbol.upper(),
                    market_info.get('active', True)
                ]

                if all(conditions):
                    # Sembol formatını normalize et (örn: BTC/USDT:USDT -> BTC)
                    normalized_market_base = market_symbol.split('/')[0]

                    # Tam eşleşme kontrolü
                    if normalized_market_base.upper() == clean_base_symbol.upper():
                        exact_matches.append(market_symbol)
                    # Kısmi eşleşme kontrolü
                    elif clean_base_symbol.upper() in normalized_market_base.upper():
                        partial_matches.append(market_symbol)

            # Önce tam eşleşmelere bak
            if exact_matches:
                # Sadece ana sembol (en sade sembol, genellikle en kısa olanı)
                exact_matches.sort(key=lambda x: len(x.split('/')[0]))
                logger.info(f"🔍 {exchange_instance.id.upper()} için bulunan sembol: [{exact_matches[0]}]")
                return [exact_matches[0]]

            # Tam eşleşme yoksa, kısmi eşleşmelerden en uygununu seç
            elif partial_matches:
                # Tam olarak istenen sembolün başında olan sembollere öncelik ver
                prefix_matches = [s for s in partial_matches
                                  if s.split('/')[0].startswith(clean_base_symbol.upper())]

                if prefix_matches:
                    prefix_matches.sort(key=lambda x: len(x.split('/')[0]))
                    logger.info(
                        f"🔍 {exchange_instance.id.upper()} için bulunan sembol (önek eşleşmesi): [{prefix_matches[0]}]")
                    return [prefix_matches[0]]
                else:
                    # Hiçbir önek eşleşmesi yoksa, tüm kısmi eşleşmeleri sırala
                    partial_matches.sort(key=lambda x: len(x.split('/')[0]))
                    logger.info(
                        f"🔍 {exchange_instance.id.upper()} için bulunan sembol (kısmi eşleşme): [{partial_matches[0]}]")
                    return [partial_matches[0]]

            # Hiç eşleşme yoksa
            logger.warning(f"❌ {exchange_instance.id.upper()} için sembol bulunamadı")
            return []

        except Exception as e:
            logger.error(f"Sembol keşfetme hatası {exchange_instance.id}: {e}")
            return []


class MultiExchangePriceManager:
    def __init__(self):
        logging.basicConfig(
            level=logging.INFO,
            format='%(message)s'
        )
        self.logger = logging.getLogger(__name__)
        self.symbol_discovery = ExchangeSymbolDiscovery()

        # Exchange mapping (sadece vadeli USDT)
        self.exchange_map = {
            # Exchange.BINANCE: ccxt.binanceusdm(),
            Exchange.MEXC: ccxt.mexc({'options': {'defaultType': 'future'}}),
            # Exchange.BYBIT: ccxt.bybit(),
            # Exchange.BITGET: ccxt.bitget(),
            # Exchange.BITMART: ccxt.bitmart(),
            # Exchange.OKX: ccxt.okx()
        }

    async def fetch_price(
            self,
            symbol: str,
            exchanges: List[Exchange] = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        Çoklu borsadan vadeli USDT fiyat alma ve sembol keşfetme
        """
        if exchanges is None:
            exchanges = [
                # Exchange.BINANCE,
                # Exchange.BYBIT,
                # Exchange.OKX,
                # Exchange.BITGET,
                Exchange.MEXC,
                # Exchange.BITMART
            ]

        results = {}
        base_symbol = symbol.replace('USDT', '').upper()
        loop = asyncio.get_running_loop()

        for exchange in exchanges:
            try:
                exchange_instance = self.exchange_map.get(exchange)

                if not exchange_instance:
                    continue

                # Geçerli sembolleri keşfet - artık sadece en uygun sembol gelecek
                valid_symbols = await self.symbol_discovery.discover_valid_symbols(
                    exchange_instance,
                    base_symbol,
                    self.logger
                )

                # Eğer hiç sembol bulunamazsa atla
                if not valid_symbols:
                    continue

                # İlk (ve tek) geçerli sembolü kullan
                valid_symbol = valid_symbols[0]

                try:
                    # Fiyat bilgisi - run_in_executor kullanımı
                    ticker = await loop.run_in_executor(
                        None,
                        functools.partial(exchange_instance.fetch_ticker, valid_symbol)
                    )

                    # Fiyat normalizasyonu
                    price = ticker['last']

                    # Detaylı bilgi
                    results[exchange.value] = {
                        'symbol': valid_symbol,
                        'price': price
                    }

                    # Daha temiz log çıktısı
                    self.logger.info(f"🔹 {exchange.value.upper()}")
                    self.logger.info(f"  Sembol: {valid_symbol}")
                    self.logger.info(f"  Fiyat: {price:.8f}")
                    self.logger.info("-" * 30)

                except Exception as ticker_error:
                    self.logger.warning(f"Fiyat çekme hatası {exchange.value}: {ticker_error}")
                    continue

            except Exception as e:
                self.logger.error(f"❌ {exchange.value.upper()} Genel Hata: {str(e)}")

        return results
