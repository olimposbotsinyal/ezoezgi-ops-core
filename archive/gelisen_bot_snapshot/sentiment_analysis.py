# sentiment_analysis.py
import logging
import asyncio
from datetime import datetime
import aiohttp
import json

logger = logging.getLogger(__name__)


class CryptoSentimentAnalyzer:
    """
    Kripto para sentiment analizi
    Sosyal medya ve haber kaynaklarından sentiment verisi toplar
    """

    def __init__(self):
        self.sentiment_cache = {}
        self.last_update = {}

    async def get_fear_greed_index(self):
        """
        Fear & Greed Index'i al
        """
        try:
            url = "https://api.alternative.me/fng/"

            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()

                        if data and 'data' in data and len(data['data']) > 0:
                            fng_data = data['data'][0]

                            return {
                                'value': int(fng_data['value']),
                                'classification': fng_data['value_classification'],
                                'timestamp': fng_data['timestamp']
                            }
            return None

        except Exception as e:
            logger.error(f"❌ Fear & Greed Index alma hatası: {str(e)}")
            return None

    async def analyze_crypto_sentiment(self, symbol):
        """
        Kripto para sentiment analizi
        """
        try:
            # Cache kontrolü
            cache_key = symbol.replace('/USDT', '')
            if cache_key in self.sentiment_cache:
                last_update = self.last_update.get(cache_key, datetime.min)
                if (datetime.now() - last_update).seconds < 3600:  # 1 saat cache
                    return self.sentiment_cache[cache_key]

            # Fear & Greed Index
            fng_data = await self.get_fear_greed_index()

            # Basit sentiment skoru hesaplama
            sentiment_score = 0.5  # Nötr başlangıç

            if fng_data:
                fng_value = fng_data['value']
                # 0-100 arası değeri 0-1 arası normalize et
                fng_normalized = fng_value / 100
                sentiment_score = fng_normalized

            # Sonuç objesi
            result = {
                'symbol': symbol,
                'sentiment_score': sentiment_score,
                'fear_greed_index': fng_data,
                'bullish_signals': self.get_bullish_signals(sentiment_score),
                'bearish_signals': self.get_bearish_signals(sentiment_score),
                'overall_sentiment': self.classify_sentiment(sentiment_score),
                'confidence': 0.7,  # Orta güven seviyesi
                'timestamp': datetime.now()
            }

            # Cache'e kaydet
            self.sentiment_cache[cache_key] = result
            self.last_update[cache_key] = datetime.now()

            return result

        except Exception as e:
            logger.error(f"❌ Sentiment analizi hatası {symbol}: {str(e)}")
            return None

    def get_bullish_signals(self, sentiment_score):
        """
        Bullish sinyalleri belirle
        """
        signals = []

        if sentiment_score > 0.7:
            signals.append("Yüksek pozitif sentiment")
        if sentiment_score > 0.6:
            signals.append("Piyasa iyimserliği")

        return signals

    def get_bearish_signals(self, sentiment_score):
        """
        Bearish sinyalleri belirle
        """
        signals = []

        if sentiment_score < 0.3:
            signals.append("Yüksek korku seviyesi")
        if sentiment_score < 0.4:
            signals.append("Piyasa kötümserliği")

        return signals

    def classify_sentiment(self, sentiment_score):
        """
        Sentiment'i sınıflandır
        """
        if sentiment_score >= 0.7:
            return "Çok Bullish"
        elif sentiment_score >= 0.6:
            return "Bullish"
        elif sentiment_score >= 0.4:
            return "Nötr"
        elif sentiment_score >= 0.3:
            return "Bearish"
        else:
            return "Çok Bearish"
