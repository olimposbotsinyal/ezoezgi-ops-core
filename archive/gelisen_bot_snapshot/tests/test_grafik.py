import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from io import BytesIO
import logging
import copy

# Proje modüllerini import et
try:
    from charts.chart_renderer import ChartRenderer
    from config_service import ConfigService
except ImportError as e:
    print("Hata: Modüller bulunamadı. Lütfen bu dosyayı projenin ana dizininde çalıştırın.")
    print(f"Detay: {e}")
    sys.exit(1)

# Loglama ayarı
logging.basicConfig(level=logging.INFO)


def generate_dummy_data(length=150, start_price=50000, trend='up'):
    """Rastgele OHLCV verisi üretir."""
    data = []
    price = start_price
    now = datetime.now(timezone.utc)

    for i in range(length):
        # Trend yönü
        if trend == 'up':
            change = np.random.uniform(-0.2, 0.6)
        else:
            change = np.random.uniform(-0.6, 0.2)

        close = price * (1 + change / 100)
        high = max(price, close) * (1 + np.random.uniform(0, 0.5) / 100)
        low = min(price, close) * (1 - np.random.uniform(0, 0.5) / 100)
        open_p = price
        vol = np.random.uniform(1000, 5000)

        # Timestamp
        ts = now - timedelta(minutes=15 * (length - i))

        data.append({
            'timestamp':ts,
            'open':open_p,
            'high':high,
            'low':low,
            'close':close,
            'volume':vol
        })
        price = close

    df = pd.DataFrame(data)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)
    return df


async def render_and_save(renderer, signal, price, filename):
    """Yardımcı fonksiyon: Grafiği oluştur ve kaydet"""
    print(f"   ⚙️ Render ediliyor: {filename}...")

    # 1. Grafiği Çiz (Ham Veri)
    # Not: Gerçek sistemde monitor_symbols bunu yapar, burada simüle ediyoruz.
    chart_buf = renderer.render(
        symbol=signal['symbol'],
        df=signal['_df'],  # Test için geçici df taşıyıcısı
        signal_type=signal['signal_type'],
        entry_price=signal['entry_price'],
        targets=signal['targets'],
        stop_loss=signal['stop_loss'],
        timeframe=signal['timeframe'],
        signal_id=signal['signal_id'],
        entry_index=len(signal['_df']) - 5,
        target_hits=signal['targets_hit'],
        targets_hit_times=signal['targets_hit_times']
    )

    if chart_buf:
        signal['chart_buf_raw'] = chart_buf.read()

        # 2. Ana Kartı Oluştur (Grafik + Metin)
        final_img = await renderer.render_main_card(signal, price)

        if final_img:
            with open(filename, "wb") as f:
                f.write(final_img.read())
            print(f"   ✅ KAYDEDİLDİ: {filename}")
        else:
            print(f"   ❌ HATA: {filename} oluşturulamadı (Main Card).")
    else:
        print(f"   ❌ HATA: {filename} oluşturulamadı (Raw Chart).")


async def run_test():
    print("🚀 DETAYLI GRAFİK TESTİ BAŞLATILIYOR...\n")
    ConfigService.init()
    renderer = ChartRenderer()

    # ==========================================
    # SENARYO 1: LONG (BTC/USDT) - HEDEFLER VE FİNAL
    # ==========================================
    print("🟦 SENARYO 1: BTC/USDT (LONG) - Başarı Hikayesi")

    df_long = generate_dummy_data(150, 65000, 'up')
    entry_price = df_long['close'].iloc[-20]  # Biraz geriden giriş

    base_signal_long = {
        'symbol':'BTC/USDT',
        'signal_type':'LONG',
        'strategy_id':'v1',
        'signal_id':'LONG-TEST-001',
        'entry_price':entry_price,
        'stop_loss':entry_price * 0.97,
        'targets':[entry_price * (1 + 0.02 * i) for i in range(1, 6)],  # %2, %4...
        'targets_hit':[False] * 5,
        'targets_hit_times':[None] * 5,
        'stop_loss_hit':False,
        'exit_type':None,
        'timeframe':'15m',
        'signal_time':(datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(),
        'meta':{'calc_method':{'sl':'ATR (2.0)', 'tp':'Fibonacci'}, 'exchange':'mexc'},
        '_df':df_long  # Test için veri
    }

    # 1.1 AÇILIŞ (Henüz hedef yok)
    sig = copy.deepcopy(base_signal_long)
    await render_and_save(renderer, sig, entry_price, "long_01_open.jpg")

    # 1.2 HEDEF 1 VURULDU
    sig['targets_hit'][0] = True
    sig['targets_hit_times'][0] = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
    current_price = sig['targets'][0]
    await render_and_save(renderer, sig, current_price, "long_02_target1.jpg")

    # 1.3 HEDEF 2 VURULDU
    sig['targets_hit'][1] = True
    sig['targets_hit_times'][1] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    current_price = sig['targets'][1]
    await render_and_save(renderer, sig, current_price, "long_03_target2.jpg")

    # 1.4 FINAL (Hepsi Vuruldu)
    sig['targets_hit'] = [True] * 5
    sig['targets_hit_times'] = [(datetime.now(timezone.utc) - timedelta(minutes=10 * i)).isoformat() for i in range(5)]
    sig['exit_type'] = 'TARGET_FINAL'
    current_price = sig['targets'][-1]
    await render_and_save(renderer, sig, current_price, "long_04_final.jpg")

    # ==========================================
    # SENARYO 2: LONG (BTC/USDT) - STOP LOSS
    # ==========================================
    print("\nApp 🟦 SENARYO 2: BTC/USDT (LONG) - Stop Loss Senaryosu")
    sig_stop = copy.deepcopy(base_signal_long)
    sig_stop['stop_loss_hit'] = True
    sig_stop['exit_type'] = 'STOP'
    current_price = sig_stop['stop_loss'] * 0.99  # Stopun biraz altı
    await render_and_save(renderer, sig_stop, current_price, "long_05_stop.jpg")

    # ==========================================
    # SENARYO 3: SHORT (ETH/USDT) - NORMAL VE HEDEF
    # ==========================================
    print("\n🟧 SENARYO 3: ETH/USDT (SHORT)")

    df_short = generate_dummy_data(150, 3500, 'down')
    entry_price_s = df_short['close'].iloc[-20]

    base_signal_short = {
        'symbol':'ETH/USDT',
        'signal_type':'SHORT',
        'strategy_id':'v2',
        'signal_id':'SHORT-TEST-002',
        'entry_price':entry_price_s,
        'stop_loss':entry_price_s * 1.03,
        'targets':[entry_price_s * (1 - 0.02 * i) for i in range(1, 6)],
        'targets_hit':[False] * 5,
        'targets_hit_times':[None] * 5,
        'stop_loss_hit':False,
        'exit_type':None,
        'timeframe':'1h',
        'signal_time':(datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
        'meta':{'calc_method':{'sl':'Sabit %', 'tp':'Sabit %'}, 'exchange':'binance'},
        '_df':df_short
    }

    # 3.1 AÇILIŞ
    sig_s = copy.deepcopy(base_signal_short)
    await render_and_save(renderer, sig_s, entry_price_s, "short_01_open.jpg")

    # 3.2 HEDEF 1 VURULDU
    sig_s['targets_hit'][0] = True
    sig_s['targets_hit_times'][0] = datetime.now(timezone.utc).isoformat()
    await render_and_save(renderer, sig_s, sig_s['targets'][0], "short_02_target1.jpg")

    print("\n🏁 Tüm test görselleri oluşturuldu. Dosyaları kontrol edebilirsiniz.")


if __name__ == "__main__":
    import asyncio

    asyncio.run(run_test())