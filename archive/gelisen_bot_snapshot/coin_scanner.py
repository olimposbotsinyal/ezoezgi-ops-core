# coin_scanner.py
import pandas as pd
import numpy as np
import talib
import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class AutoCoinScanner:
	"""
	Otomatik coin tarayıcı - Yüksek potansiyelli coinleri bulur
	"""

	def __init__(self, exchange):
		self.exchange = exchange
		self.scan_cache = {}
		self.last_scan = None

	async def scan_high_potential_coins(self, min_volume=1000000, min_potential=30):
		"""
		Yüksek potansiyelli coinleri tara
		"""
		try:
			logger.info("🔍 Yüksek potansiyel coin taraması başlıyor...")

			# Tüm USDT çiftlerini al
			markets = self.exchange.markets
			usdt_symbols = [symbol for symbol in markets.keys() if symbol.endswith('/USDT')]

			# Ticker verilerini al
			tickers = await self.exchange.fetch_tickers()

			high_potential_coins = []

			for symbol in usdt_symbols[:100]:  # İlk 100 coin
				try:
					if symbol not in tickers:
						continue

					ticker = tickers[symbol]
					volume_24h = ticker['quoteVolume']

					# Minimum hacim kontrolü
					if not volume_24h or volume_24h < min_volume:
						continue

					# OHLCV verilerini al
					ohlcv = await self.exchange.fetch_ohlcv(symbol, '1h', limit=100)
					df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

					if len(df) < 50:
						continue

					# Potansiyel analizi
					potential_score = self.calculate_potential_score(df, ticker)

					if potential_score >= min_potential:
						coin_data = {
							'symbol': symbol,
							'potential_score': potential_score,
							'price': ticker['last'],
							'change_24h': ticker['percentage'] or 0,
							'volume_24h': volume_24h,
							'analysis': self.get_detailed_analysis(df, ticker)
						}

						high_potential_coins.append(coin_data)
						logger.info(f"🎯 Yüksek potansiyel: {symbol} - Skor: {potential_score:.1f}")

				except Exception as symbol_error:
					logger.error(f"❌ {symbol} analiz hatası: {symbol_error}")
					continue

					# Potansiyel skora göre sırala
					high_potential_coins.sort(key=lambda x: x['potential_score'], reverse=True)

					# Cache'e kaydet
					self.scan_cache = high_potential_coins
					self.last_scan = datetime.now()

					logger.info(f"✅ {len(high_potential_coins)} yüksek potansiyelli coin bulundu")
					return high_potential_coins[:20]  # En iyi 20 coin

		except Exception as e:
			logger.error(f"❌ Coin tarama hatası: {str(e)}")
			return []

	def calculate_potential_score(self, df, ticker):
		"""
		Potansiyel skoru hesapla (0-100 arası)
		"""
		try:
			score = 0

			# 1. Teknik Analiz Skoru (40 puan)
			technical_score = self.calculate_technical_score(df)
			score += technical_score * 0.4

			# 2. Momentum Skoru (25 puan)
			momentum_score = self.calculate_momentum_score(df)
			score += momentum_score * 0.25

			# 3. Volume Analizi (20 puan)
			volume_score = self.calculate_volume_score(df)
			score += volume_score * 0.20

			# 4. Volatilite Skoru (15 puan)
			volatility_score = self.calculate_volatility_score(df)
			score += volatility_score * 0.15

			return min(score, 100)  # Max 100 puan

		except Exception as e:
			logger.error(f"❌ Potansiyel skor hesaplama hatası: {str(e)}")
			return 0

	def calculate_technical_score(self, df):
		"""
		Teknik analiz skoru (0-100)
		"""
		try:
			score = 0
			close_prices = df['close'].values
			high_prices = df['high'].values
			low_prices = df['low'].values
			volume = df['volume'].values

			# RSI Analizi
			rsi = talib.RSI(close_prices, timeperiod=14)[-1]
			if 25 <= rsi <= 35:  # Oversold bölgesi
				score += 30
			elif 35 < rsi <= 50:  # Orta bölge
				score += 20
			elif 50 < rsi <= 65:  # Güçlü bölge
				score += 15
			elif rsi > 75:  # Overbought
				score += 5

			# MACD Analizi
			macd, macd_signal, macd_histogram = talib.MACD(close_prices)
			if len(macd) > 1:
				if macd[-1] > macd_signal[-1] and macd[-2] <= macd_signal[-2]:  # Bullish crossover
					score += 25
				elif macd[-1] > macd_signal[-1]:  # Bullish durumda
					score += 15

			# Bollinger Bands Analizi
			bb_upper, bb_middle, bb_lower = talib.BBANDS(close_prices, timeperiod=20)
			current_price = close_prices[-1]
			bb_position = (current_price - bb_lower[-1]) / (bb_upper[-1] - bb_lower[-1])

			if bb_position < 0.2:  # Alt banda yakın
				score += 20
			elif bb_position > 0.8:  # Üst banda yakın
				score += 10

			# Stochastic Analizi
			stoch_k, stoch_d = talib.STOCH(high_prices, low_prices, close_prices)
			if len(stoch_k) > 0:
				if stoch_k[-1] < 20:  # Oversold
					score += 15
				elif stoch_k[-1] > 80:  # Overbought
					score += 5

			# Support/Resistance Analizi
			recent_low = np.min(low_prices[-20:])
			recent_high = np.max(high_prices[-20:])
			price_position = (current_price - recent_low) / (recent_high - recent_low)

			if price_position < 0.3:  # Support yakını
				score += 10

			return min(score, 100)

		except Exception as e:
			logger.error(f"❌ Teknik skor hesaplama hatası: {str(e)}")
			return 0

	def calculate_momentum_score(self, df):
		"""
		Momentum skoru hesapla (0-100)
		"""
		try:
			score = 0
			close_prices = df['close'].values

			# Fiyat değişim oranları
			returns_1h = (close_prices[-1] / close_prices[-2] - 1) * 100
			returns_4h = (close_prices[-1] / close_prices[-5] - 1) * 100
			returns_24h = (close_prices[-1] / close_prices[-25] - 1) * 100

			# Pozitif momentum puanları
			if returns_1h > 1:
				score += 20
			elif returns_1h > 0.5:
				score += 15
			elif returns_1h > 0:
				score += 10

			if returns_4h > 2:
				score += 25
			elif returns_4h > 1:
				score += 20
			elif returns_4h > 0:
				score += 15

			if returns_24h > 5:
				score += 30
			elif returns_24h > 2:
				score += 25
			elif returns_24h > 0:
				score += 20

			# Momentum göstergeleri
			mom = talib.MOM(close_prices, timeperiod=10)
			if len(mom) > 0 and mom[-1] > 0:
				score += 15

			roc = talib.ROC(close_prices, timeperiod=10)
			if len(roc) > 0 and roc[-1] > 2:
				score += 10

			return min(score, 100)

		except Exception as e:
			logger.error(f"❌ Momentum skor hesaplama hatası: {str(e)}")
			return 0

	def calculate_volume_score(self, df):
		"""
		Volume skoru hesapla (0-100)
		"""
		try:
			score = 0
			volume = df['volume'].values

			# Volume artış oranları
			current_volume = volume[-1]
			avg_volume_5 = np.mean(volume[-5:])
			avg_volume_20 = np.mean(volume[-20:])

			volume_ratio_5 = current_volume / avg_volume_5 if avg_volume_5 > 0 else 1
			volume_ratio_20 = current_volume / avg_volume_20 if avg_volume_20 > 0 else 1

			# Volume artış puanları
			if volume_ratio_5 > 3:
				score += 40
			elif volume_ratio_5 > 2:
				score += 30
			elif volume_ratio_5 > 1.5:
				score += 20
			elif volume_ratio_5 > 1.2:
				score += 10

			if volume_ratio_20 > 2:
				score += 30
			elif volume_ratio_20 > 1.5:
				score += 20
			elif volume_ratio_20 > 1.2:
				score += 15

			# Volume trend analizi
			volume_trend = np.polyfit(range(len(volume[-10:])), volume[-10:], 1)[0]
			if volume_trend > 0:
				score += 15

			# On Balance Volume (OBV)
			obv = talib.OBV(df['close'].values, volume)
			obv_trend = np.polyfit(range(len(obv[-10:])), obv[-10:], 1)[0]
			if obv_trend > 0:
				score += 15

			return min(score, 100)

		except Exception as e:
			logger.error(f"❌ Volume skor hesaplama hatası: {str(e)}")
			return 0

	def calculate_volatility_score(self, df):
		"""
		Volatilite skoru hesapla (0-100)
		"""
		try:
			score = 0
			close_prices = df['close'].values

			# ATR bazlı volatilite
			atr = talib.ATR(df['high'].values, df['low'].values, close_prices, timeperiod=14)
			atr_ratio = atr[-1] / close_prices[-1] * 100

			# Optimal volatilite aralığı %2-8
			if 2 <= atr_ratio <= 8:
				score += 40
			elif 1 <= atr_ratio < 2:
				score += 25
			elif 8 < atr_ratio <= 12:
				score += 30
			elif atr_ratio > 15:
				score += 10  # Çok yüksek volatilite riskli

			# Bollinger Band genişliği
			bb_upper, bb_middle, bb_lower = talib.BBANDS(close_prices, timeperiod=20)
			bb_width = (bb_upper[-1] - bb_lower[-1]) / bb_middle[-1] * 100

			if 3 <= bb_width <= 10:
				score += 30
			elif bb_width > 10:
				score += 20

			# Volatilite trendi
			volatility_5 = np.std(close_prices[-5:] / close_prices[-6:-1] - 1)
			volatility_20 = np.std(close_prices[-20:] / close_prices[-21:-1] - 1)

			if volatility_5 > volatility_20:  # Artan volatilite
				score += 20

			# True Range analizi
			tr = talib.TRANGE(df['high'].values, df['low'].values, close_prices)
			tr_ratio = tr[-1] / close_prices[-1] * 100

			if 1 <= tr_ratio <= 5:
				score += 10

			return min(score, 100)

		except Exception as e:
			logger.error(f"❌ Volatilite skor hesaplama hatası: {str(e)}")
			return 0

	def get_detailed_analysis(self, df, ticker):
		"""
		Detaylı analiz bilgilerini al
		"""
		try:
			close_prices = df['close'].values
			volume = df['volume'].values

			# Temel bilgiler
			current_price = close_prices[-1]
			change_24h = ticker['percentage'] or 0
			volume_24h = ticker['quoteVolume']

			# Teknik indikatörler
			rsi = talib.RSI(close_prices, timeperiod=14)[-1]
			macd, macd_signal, _ = talib.MACD(close_prices)
			bb_upper, bb_middle, bb_lower = talib.BBANDS(close_prices, timeperiod=20)
			atr = talib.ATR(df['high'].values, df['low'].values, close_prices, timeperiod=14)[-1]

			# Support/Resistance
			support = np.min(df['low'].values[-20:])
			resistance = np.max(df['high'].values[-20:])

			# Volume analizi
			avg_volume = np.mean(volume[-20:])
			volume_ratio = volume[-1] / avg_volume if avg_volume > 0 else 1

			# Momentum
			momentum_5 = (current_price / close_prices[-6] - 1) * 100
			momentum_24 = (current_price / close_prices[-25] - 1) * 100

			return {
				'price': current_price,
				'change_24h': change_24h,
				'volume_24h_usdt': volume_24h,
				'rsi': rsi,
				'macd_signal': 'BUY' if macd[-1] > macd_signal[-1] else 'SELL',
				'bb_position': (current_price - bb_lower[-1]) / (bb_upper[-1] - bb_lower[-1]),
				'atr_ratio': atr / current_price * 100,
				'support': support,
				'resistance': resistance,
				'volume_ratio': volume_ratio,
				'momentum_5h': momentum_5,
				'momentum_24h': momentum_24,
				'volatility': np.std(close_prices[-20:] / close_prices[-21:-1] - 1) * 100
			}

		except Exception as e:
			logger.error(f"❌ Detaylı analiz hatası: {str(e)}")
			return {}

	async def scan_breakout_candidates(self, min_volume=500000):
		"""
		Breakout adaylarını tara
		"""
		try:
			logger.info("🚀 Breakout adayları taranıyor...")

			markets = self.exchange.markets
			usdt_symbols = [symbol for symbol in markets.keys() if symbol.endswith('/USDT')]
			tickers = await self.exchange.fetch_tickers()

			breakout_candidates = []

			for symbol in usdt_symbols[:50]:
				try:
					if symbol not in tickers:
						continue

					ticker = tickers[symbol]
					volume_24h = ticker['quoteVolume']

					if not volume_24h or volume_24h < min_volume:
						continue

					# OHLCV verilerini al
					ohlcv = await self.exchange.fetch_ohlcv(symbol, '1h', limit=100)
					df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

					if len(df) < 50:
						continue

					# Breakout analizi
					breakout_score = self.calculate_breakout_score(df)

					if breakout_score >= 70:  # Yüksek breakout potansiyeli
						breakout_data = {
							'symbol': symbol,
							'breakout_score': breakout_score,
							'price': ticker['last'],
							'change_24h': ticker['percentage'] or 0,
							'volume_24h': volume_24h,
							'breakout_type': self.identify_breakout_type(df),
							'target_price': self.calculate_breakout_target(df)
						}

						breakout_candidates.append(breakout_data)
						logger.info(f"🚀 Breakout adayı: {symbol} - Skor: {breakout_score:.1f}")

				except Exception as symbol_error:
					logger.error(f"❌ {symbol} breakout analizi hatası: {symbol_error}")
					continue

			breakout_candidates.sort(key=lambda x: x['breakout_score'], reverse=True)

			logger.info(f"✅ {len(breakout_candidates)} breakout adayı bulundu")
			return breakout_candidates[:10]  # En iyi 10 aday

		except Exception as e:
			logger.error(f"❌ Breakout tarama hatası: {str(e)}")
			return []

	def calculate_breakout_score(self, df):
		"""
		Breakout skoru hesapla
		"""
		try:
			score = 0
			close_prices = df['close'].values
			high_prices = df['high'].values
			low_prices = df['low'].values
			volume = df['volume'].values

			# Resistance yakınlığı
			resistance = np.max(high_prices[-50:])
			current_price = close_prices[-1]
			resistance_distance = (resistance - current_price) / current_price * 100

			if resistance_distance < 2:  # %2 içinde
				score += 30
			elif resistance_distance < 5:  # %5 içinde
				score += 20

			# Volume artışı
			avg_volume = np.mean(volume[-20:])
			current_volume = volume[-1]
			volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1

			if volume_ratio > 2:
				score += 25
			elif volume_ratio > 1.5:
				score += 15

			# Consolidation pattern
			price_range = (np.max(close_prices[-20:]) - np.min(close_prices[-20:])) / np.mean(
				close_prices[-20:]) * 100
			if price_range < 10:  # Dar aralık
				score += 20

			# Momentum buildup
			rsi = talib.RSI(close_prices, timeperiod=14)[-1]
			if 50 < rsi < 70:  # Güçlü ama aşırı alım değil
				score += 15

			# MACD momentum
			macd, macd_signal, _ = talib.MACD(close_prices)
			if len(macd) > 1 and macd[-1] > macd_signal[-1]:
				score += 10

			return min(score, 100)

		except Exception as e:
			logger.error(f"❌ Breakout skor hesaplama hatası: {str(e)}")
			return 0

	def identify_breakout_type(self, df):
		"""
		Breakout tipini belirle
		"""
		try:
			close_prices = df['close'].values
			high_prices = df['high'].values
			low_prices = df['low'].values

			# Son 20 mumun analizi
			recent_highs = high_prices[-20:]
			recent_lows = low_prices[-20:]
			recent_closes = close_prices[-20:]

			# Horizontal resistance
			resistance_level = np.max(recent_highs)
			resistance_touches = np.sum(recent_highs > resistance_level * 0.995)

			if resistance_touches >= 3:
				return "Horizontal Resistance Breakout"

			# Ascending triangle
			high_trend = np.polyfit(range(len(recent_highs)), recent_highs, 1)[0]
			low_trend = np.polyfit(range(len(recent_lows)), recent_lows, 1)[0]

			if abs(high_trend) < 0.001 and low_trend > 0:
				return "Ascending Triangle Breakout"

			# Descending triangle
			if high_trend < 0 and abs(low_trend) < 0.001:
				return "Descending Triangle Breakout"

			# Symmetrical triangle
			if high_trend < 0 and low_trend > 0:
				return "Symmetrical Triangle Breakout"

			# Flag pattern
			if len(recent_closes) >= 10:
				flag_slope = np.polyfit(range(len(recent_closes[-10:])), recent_closes[-10:], 1)[0]
				if abs(flag_slope) < 0.001:
					return "Flag Pattern Breakout"

			return "General Breakout"

		except Exception as e:
			logger.error(f"❌ Breakout tipi belirleme hatası: {str(e)}")
			return "Unknown"

	def calculate_breakout_target(self, df):
		"""
		Breakout hedef fiyatını hesapla
		"""
		try:
			close_prices = df['close'].values
			high_prices = df['high'].values
			low_prices = df['low'].values

			current_price = close_prices[-1]

			# Resistance seviyesi
			resistance = np.max(high_prices[-50:])

			# Support seviyesi
			support = np.min(low_prices[-50:])

			# Hedef hesaplama (resistance-support mesafesi kadar yukarı)
			range_size = resistance - support
			target_price = resistance + range_size

			return target_price

		except Exception as e:
			logger.error(f"❌ Breakout hedef hesaplama hatası: {str(e)}")
			return 0

	def get_scan_summary(self):
		"""
		Tarama özetini al
		"""
		try:
			if not self.scan_cache or not self.last_scan:
				return "Henüz tarama yapılmadı."

			total_scanned = len(self.scan_cache)
			high_potential = len([coin for coin in self.scan_cache if coin['potential_score'] >= 70])
			medium_potential = len([coin for coin in self.scan_cache if 50 <= coin['potential_score'] < 70])

			summary = f"""
            🔍 TARAMA ÖZETİ
            📅 Son Tarama: {self.last_scan.strftime('%d.%m.%Y %H:%M')}
            📊 Taranan Coin: {total_scanned}
            🎯 Yüksek Potansiyel (70+): {high_potential}
            ⚡ Orta Potansiyel (50-70): {medium_potential}
                        """

			if high_potential > 0:
				summary += f"\n🚀 En İyi 3 Coin:\n"
				for i, coin in enumerate(self.scan_cache[:3]):
					summary += f"{i + 1}. {coin['symbol']} - Skor: {coin['potential_score']:.1f}\n"

			return summary.strip()

		except Exception as e:
			logger.error(f"❌ Tarama özeti hatası: {str(e)}")
			return "Tarama özeti alınamadı."
