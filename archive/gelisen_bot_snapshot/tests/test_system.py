# test_system.py
import asyncio
import pandas as pd
import numpy as np
import sys
import os
import logging
import pytest


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Path ekle
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
	from strategies.alarm_strateji import OlimposStrategy

	print("✅ alarm_strateji import edildi")
except ImportError as e:
	print(f"❌ Import hatası: {e}")
	sys.exit(1)


@pytest.mark.asyncio
async def test_ai_system():
	"""AI sistemini test et"""
	print("\n🧪 AI Sistemi Test Ediliyor...")
	print("=" * 50)

	# Test config
	config = {
		'exchange': 'binance',
		'enable_ai_training': True,
		'max_position_size': 0.1,
		'stop_loss_pct': 0.02,
		'take_profit_pct': 0.04,
		'min_confidence': 0.6,
		'analysis_interval': 60,
		'dry_run': True
	}

	try:
		print("1️⃣ Strategy oluşturuluyor...")
		strategy = OlimposStrategy(config)
		print("✅ Strategy oluşturuldu")

		print("\n2️⃣ AI sistemini başlatılıyor...")
		success = await strategy.initialize_ai_system(None)
		if success:
			print("✅ AI sistemi başarıyla başlatıldı")
		else:
			print("❌ AI sistemi başlatılamadı")
			return False

		print("\n3️⃣ Test verisi oluşturuluyor...")
		dates = pd.date_range(start='2024-01-01', periods=100, freq='1H')
		test_data = pd.DataFrame({
			'timestamp': dates,
			'open': np.random.uniform(40000, 50000, 100),
			'high': np.random.uniform(50000, 52000, 100),
			'low': np.random.uniform(38000, 40000, 100),
			'close': np.random.uniform(40000, 50000, 100),
			'volume': np.random.uniform(1000, 10000, 100)
		})
		test_data.set_index('timestamp', inplace=True)
		print("✅ Test verisi oluşturuldu")

		print("\n4️⃣ AI Model test ediliyor...")
		if strategy._ai_model:
			try:
				model_status = strategy._ai_model.get_model_status()
				print(f"📊 AI Model Durumu: {model_status}")

				# Prediction test
				prediction = strategy._ai_model.predict(test_data)
				print(f"🎯 AI Prediction: {prediction}")
			except Exception as e:
				print(f"❌ AI Model test hatası: {e}")

		print("\n5️⃣ Sentiment Analyzer test ediliyor...")
		if strategy._sentiment_analyzer:
			try:
				sentiment = strategy._sentiment_analyzer.analyze_symbol_sentiment('BTC/USDT')
				print(f"😊 Sentiment: {sentiment}")
			except Exception as e:
				print(f"❌ Sentiment test hatası: {e}")

		print("\n6️⃣ Signal Generator test ediliyor...")
		if hasattr(strategy, 'signal_generator') and strategy.signal_generator:
			try:
				signals = strategy.signal_generator.generate_signals('BTC/USDT', test_data)
				print(f"📡 Signals: {signals}")
			except Exception as e:
				print(f"❌ Signal Generator test hatası: {e}")

		print("\n7️⃣ Enhanced check signals test ediliyor...")
		try:
			signal_result = await strategy.enhanced_check_signals(test_data, 'BTC/USDT')
			print(f"🔍 Enhanced Signals: {signal_result}")
		except Exception as e:
			print(f"❌ Enhanced signals test hatası: {e}")

		print("\n" + "=" * 50)
		print("🎉 Tüm testler tamamlandı!")
		return True

	except Exception as e:
		print(f"❌ Genel test hatası: {e}")
		import traceback
		traceback.print_exc()
		return False


def main():
	"""Ana fonksiyon"""
	print("🚀 AI Trading Sistemi Test Başlatılıyor...")

	# Event loop oluştur ve çalıştır
	try:
		result = asyncio.run(test_ai_system())
		if result:
			print("\n✅ Test başarıyla tamamlandı!")
		else:
			print("\n❌ Test başarısız!")
	except Exception as e:
		print(f"❌ Ana fonksiyon hatası: {e}")
		import traceback
		traceback.print_exc()


if __name__=="__main__":
	main()
