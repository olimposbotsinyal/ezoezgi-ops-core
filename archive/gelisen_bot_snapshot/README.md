# Olimpos Gelişen Bot

Bu proje, kripto para borsalarında (Binance, Bitget, OKX, MEXC, Bybit, BitMart gibi) otomatik alım-satım işlemleri yapan bir bot sistemidir. Gerçek zamanlı fiyat takibi, sinyal üretimi, AI tabanlı analizler (RealAIModel.py) ve trailing stop gibi özellikler içerir. Ayrıca, raporlama, loglama ve admin paneli gibi araçlarla desteklenir.

## Özellikler
- **Çoklu Exchange Desteği**: Binance, Bitget, OKX, MEXC, Bybit, BitMart gibi borsalar için API entegrasyonu.
- **AI ve Analiz**: Gerçek zamanlı fiyat analizi, duygu analizi (sentiment_analysis.py) ve patern tanıma.
- **Sinyal Merkezi**: Otomatik sinyal üretimi ve merkezi yönetim (signal_merkezi.py).
- **Raporlama ve Loglama**: Alarm raporları, analytics ve log dosyaları (logs/ klasörü altında).
- **Güvenlik**: Trailing stop, risk yönetimi ve API ayarları (config/ altında).
- **Test ve Geliştirme**: tests/ klasörü altında birim testleri.

## Kurulum

### Gereksinimler
- Python 3.8 veya üzeri
- Gerekli kütüphaneler: `requirements.txt` dosyasından yükleyin (aşağıya bakın).

### Adımlar
1. **Repo'yu Klonlayın**:
git clone https://github.com/olimposbotsinyal/Gelisen_Bot.git
cd Gelisen_Bot

2. **Sanal Ortam Oluşturun** (Önerilen):
python -m venv .venv
source .venv/bin/activate  # Windows'ta: .venv\Scripts\activate

3. **Bağımlılıkları Yükleyin**:
pip install -r requirements.txt

4. **API Anahtarlarını Ayarlayın**:
- `config/` klasöründeki dosyalara (örneğin `binance_api_ayarlari.py`) API anahtarlarınızı ekleyin.
- **Güvenlik Notu**: Bu dosyaları `.gitignore`'a ekleyin ve asla commit etmeyin.

5. **Botu Çalıştırın**:

python Olimpos_Cripto_Bot.py

- Test için: `python tests/test_example.py` (tests/ altında örnek testler ekleyin).

## Kullanım
- **Ana Bot**: `Olimpos_Cripto_Bot.py` çalıştırarak başlatın. Otomatik alım-satım yapacaktır.
- **Admin Paneli**: `olimpos_admin.py` ile yönetim.
- **Fiyat Takibi**: `price_manager.py` ile manuel kontrol.
- **Loglar**: `logs/` altında hataları inceleyin.

### Örnek Komut
python Olimpos_Cripto_Bot.py --exchange binance --symbol BTCUSDT


## Geliştirme
- **Kod Standartları**: Tüm dosyalar snake_case İngilizce olsun (örneğin `binance_api_settings.py`).
- **Testler**: `tests/` altında yeni testler ekleyin.
- **Bağımlılık Yönetimi**: Yeni kütüphane eklerseniz, `requirements.txt`'yi güncelleyin (aşağıya bakın).

## Sorun Giderme
- API bağlantı hataları için: Bağlantı test dosyalarını (`binance_api_test.py` gibi) kullanın, ama prodüksiyonda silin.
- Loglar: `logs/` altında detaylı hatalar var.
- Daha fazla yardım için issue açın.

## Lisans
Bu proje açık kaynaklıdır. Kullanım için sorumluluk kabul edilmez – kripto işlemleri risk içerir.

## Katkıda Bulunma
Pull request gönderin veya issue açın. Kod kalitesine dikkat edin.


