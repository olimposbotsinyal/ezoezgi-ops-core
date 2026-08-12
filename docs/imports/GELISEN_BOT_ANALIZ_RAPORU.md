# Gelisen_Bot — Analiz Raporu

> Kaynak proje: `C:\Users\Serkan\PycharmProjects\PycharmProjects\Gelisen_Bot`
> İnceleme kapsamı: Kök dizin + tüm alt klasörler (`.venv`, `.idea`, `__pycache__`,
> `.pytest_cache`, `olimpos_cripto_bot.zip` hariç). Bu SADECE ANALİZ dokümanıdır —
> hiçbir kod aktif entegre edilmedi, hiçbir kaynak dosya değiştirilmedi.
> `.env` dosyasının içeriği hiçbir aşamada okunmadı/raporlanmadı; kod içinde tespit
> edilen hardcoded görünen secret'ların değerleri de bu raporda **yer almıyor**
> (bkz. `GELISEN_BOT_SECURITY_NOTES.md`).

Gelisen_Bot, çoklu borsa destekli (Binance, Bitget, OKX, MEXC, Bybit, BitMart)
Telegram tabanlı bir kripto vadeli işlem botu. Kullanıcılar Telegram üzerinden kendi
borsa API anahtarlarını girip botun kendi hesapları adına otomatik alım-satım
yapmasına izin veriyor (çok kullanıcılı, PostgreSQL destekli bir yapı).

---

## 1. Genel Mimari

| Alan | Klasör/Dosya | Rol |
|---|---|---|
| Giriş noktası | `Olimpos_Cripto_Bot.py` | Telegram Application kurulumu, scheduler, arka plan görevleri, tüm alt modülleri bağlayan orkestratör (tek `__main__` bloğu burada). |
| Admin/kanal | `olimpos_admin.py`, `olimpos_channel.py` | Bağımsız çalışmaz, ana bot tarafından import edilir; RBAC'lı admin paneli ve bildirim kanalı yönetimi. |
| Çekirdek servisler | `core/` | Risk kill-switch, portföy giriş kapısı, strateji registry, sembol normalizasyonu, aday seçici, cache. |
| Strateji/sinyal | `strategies/`, `strategies/alarm_system/`, `patterns/` | Strateji motorunun kalbi — teknik analiz, AI tahmin entegrasyonu, tarama, izleme, sinyal akışı. |
| Emir yürütme | `settings/execution/*_al_sat.py` | Borsa başına gerçek emir gönderme (open/close, market/limit/stop) kodu. |
| Telegram menüleri | `settings/*_api_ayarlari.py`, `strategies/strateji_ayarlari.py` | Borsa API key girişi ve strateji parametre ayarları için Telegram menü akışları. |
| Veri katmanı | `data/olimpos_data.py`, `data/credentials_repo.py` | PostgreSQL bağlantı havuzu, tüm CRUD sorguları, kullanıcı API key okuma. |
| Config | `config/constants.py`, `config_service.py`, `config/*.json` | Statik sabitler/Enum'lar + JSON tabanlı çalışma zamanı ayarları (manuel/otomatik, hot-reload). |
| AI/adaptasyon | `RealAIModel.py`, `StrategyAdaptiveTuner.py`, `models/*.pkl` | Borsa başına eğitilmiş ML modelleri (RF/XGBoost/LightGBM/GB) + kapalı-döngü otomatik parametre ayarı. |
| Görselleştirme | `charts/chart_renderer.py` | Sinyal/PnL kartlarını PNG olarak render eden Pillow tabanlı motor. |
| Loglama | `logger_config.py`, `logs/admin/` | Rotating file handler + amaca özel logger'lar (admin, API, hata vb.). |
| Vendored (gömülü) | `bitget/`, `okx/`, `ta-lib/` | Üçüncü taraf SDK/kaynak kopyaları — proje kodu tarafından doğrudan kullanılmıyor gibi görünüyor (referans amaçlı). |

**Üst seviye akış:** `Olimpos_Cripto_Bot.py` → `olimpos_admin.py` / `olimpos_channel.py`
/ `signal_merkezi.py` (manuel `/signal`) / `strategies/alarm_strateji.py::OlimposStrategy`
(otomatik tarama) → `strategies/alarm_system/*` → `settings/execution/*_al_sat.py`
(gerçek emir) → `data/olimpos_data.py` (PostgreSQL) + `core/*` (risk/portföy kontrolü).

---

## 2. Çalışma Akışı (Adım Adım)

1. **Modül yükleme** — `Olimpos_Cripto_Bot.py` başında `update_requirements_if_needed()`
   çağrılır (Cumartesi günleri `pip freeze` ile `requirements.txt`'yi otomatik günceller
   — bot her başlatıldığında koşullu bir alt-process çalıştırıyor olması dikkat çekici).
   Ardından merkezi logger kurulur, `OlimposStrategy()` nesnesi oluşturulur.
2. **`run_bot()`** — Windows event loop policy ayarlanır, `OlimposBotManager()` kurulur,
   strateji ID senkronizasyonu thread'de yapılır, `bot_manager.run()` çağrılır.
3. **`OlimposBotManager.run()` açılış sırası:**
   1. `initialize_database()` → tablo oluşturma + süper admin var mı kontrolü (`.env`'deki
      `ADMIN_PASSWORD`'u SHA-256 hashleyip DB'ye upsert eder).
   2. `setup_application()` → `Application.builder().token(BOT_TOKEN).build()`,
      `ConversationHandler` (yaklaşık 100 `State` değeri), kanal mesajı handler'ı,
      merkezi hata yakalayıcı (`add_error_handler`).
   3. `setup_scheduler()` → `AsyncIOScheduler`: her 15 dakikada bakiye güncelleme,
      her gün 03:00'te model eğitimi.
   4. Telegram bildirim kanallarının senkronizasyonu (thread'de).
   5. `start_background_tasks()` → `periodic_check` (tarama döngüsü) + `EquityService`
      (60 sn'de bir toplam equity ölçümü, `data/daily_summary.jsonl`'a yazıyor).
   6. Telegram **long-polling** başlatılır (webhook kullanılmıyor).
   7. `asyncio.Event().wait()` ile bot canlı tutulur; `SIGINT`/`SIGTERM` ile graceful
      shutdown (`cleanup()` — equity servisi durdurma, açık ccxt bağlantılarını kapatma,
      scheduler/PTB kapatma).
4. **Periyodik tarama** — `run_periodic_scans()`, `ADMIN_USER_ID` + varsayılan `mexc`
   borsasıyla context oluşturup `OlimposStrategy.run_smart_scan_and_cleanup` çağırır;
   bu da `strategies/alarm_system/scanning.py` üzerinden AI+strateji taramasını tetikler.
5. **`olimpos_admin.py` / `olimpos_channel.py`** bağımsız çalıştırılamaz — saf modül
   olarak import edilirler; kendi `__main__` blokları yoktur.

---

## 3. Bağımlılıklar

- **Python paket sayısı:** `requirements.txt` içinde ~155 paket (tam liste dosyada mevcut).
- **Borsa erişimi:** `ccxt==4.5.12` ana soyutlama katmanı (gerçek emirlerin çoğu buradan
  gidiyor); ek olarak `python-binance==1.0.29` (Binance menü/bakiye işlemleri için,
  ccxt'e ek olarak) ve MEXC için özel `curl_cffi` tabanlı ham HTTP istemcisi
  (`Olimpos_api_MEXC.py`).
- **Telegram — iki farklı kütüphane bir arada:** `python-telegram-bot==22.5` (ana bot
  çatısı) **ve** `pyTelegramBotAPI==4.29.1` (`telebot` paket adı) aynı projede birlikte
  kullanılıyor; ayrıca `requirements.txt`'de bunlarla alakasız, isim çakışması riski
  taşıyan bir `telebot==0.0.5` paketi de listeli. Bu, kendi başına bir mimari
  tutarsızlık — EzoEzgi'ye taşınırken tek kütüphaneye indirgenmeli (bkz. §6).
- **Vendored SDK'lar:** `bitget/` (resmi çok dilli Bitget SDK deposu), `okx/` (resmi OKX
  Python SDK'sı) — kod tabanında doğrudan import edildiklerine dair güçlü bir işaret yok,
  muhtemelen referans kopyalar.
- **DB:** `psycopg2-binary` → PostgreSQL.
- **AI/ML:** `scikit-learn`, `xgboost`, `lightgbm`, `torch`, `keras`, `tensorboard`,
  `stable_baselines3`.
- **Diğer:** `IMAPClient` (e-posta), `vaderSentiment`/`praw`/`tweepy`/`newsapi`
  (sentiment/sosyal veri), `APScheduler`, `Pillow`/`matplotlib`/`mplfinance` (grafik),
  `TA-Lib` (C tabanlı teknik analiz).

---

## 4. Riskli Alanlar

### 4.1 Gerçek emir gönderen kod parçaları
- `strategies/alarm_strateji.py:3163` — Binance SL senkronizasyonu için gerçek ccxt
  `create_order` çağrısı.
- `settings/execution/binance_al_sat.py:1332-1357` (`_send_once`) — `market`/`limit`/
  `STOP_MARKET`/`TAKE_PROFIT_MARKET` tipli gerçek Binance emirleri; hata durumunda özel
  `OrderCreationError` (satır 1017).
- `settings/execution/mexc_al_sat.py:519,561,1198,2031` — gerçek MEXC pozisyon
  açma/kapama emirleri.
- `settings/execution/bitget_al_sat.py`, `okx_al_sat.py`, `bybit_al_sat.py`,
  `bitmart_al_sat.py` — her biri kendi borsası için benzer emir fonksiyonları barındırıyor.
- `core_trailing.py` — trailing stop-loss güncellemesi, dinamik olarak borsaya yeni stop
  emri gönderiyor.
- `signal_merkezi.py:2388` (`signal_handler`) — Telegram grup/kanalından manuel
  `/signal open|close ...` komutlarını parse edip execution plan'a yönlendiriyor.

### 4.2 Hardcoded secret şüphesi (değerler bu raporda yok)
Üç birinci-taraf dosyada, bağımsız olarak da doğrulanmış (grep ile, değer okunmadan)
hardcoded/non-empty credential-benzeri literal tespit edildi:
- `config/constants.py` — `BOT_TOKEN`, `ADMIN_USER_ID`, `ADMIN_PASSWORD`, `DB_PASS`,
  `EMAIL_PASSWORD` için `os.getenv(..., "<gerçek-değere-benzer-fallback>")` paterni.
- `data/olimpos_data.py` — bağımsız, ikinci bir `DB_PASS` fallback tanımı.
- `Olimpos_api_MEXC.py:159` — `API_KEY = "..."` doğrudan string literal (boş değil).

**Kritik uyuşmazlık:** `.env` dosyasındaki değişken adı `TELEGRAM_BOT_TOKEN`, kod ise
`BOT_TOKEN` okuyor — isimler örtüşmüyor, yani sistem muhtemelen `.env`'i hiç kullanmadan
her zaman koddaki hardcoded fallback token'ı kullanıyor. `ADMIN_USER_ID` ve
`ADMIN_PASSWORD` `.env`'de hiç tanımlı değil — aynı durum. Detaylar ve önerilen aksiyon
için bkz. `GELISEN_BOT_SECURITY_NOTES.md`.

### 4.3 Kullanıcı API anahtarlarının saklanması
`data/olimpos_data.py` (`get_api_key`) ve `data/credentials_repo.py` — kullanıcıların
borsa `api_key`/`secret_key`/`passphrase` bilgileri PostgreSQL'de **düz metin** olarak
saklanıyor; şifreleme katmanına rastlanmadı.

### 4.4 Silme/kritik veri aksiyonları
`data/olimpos_data.py` içinde admin silme, kanal silme, API key silme (`delete_api_key`),
kullanıcı silme (`delete_user`, birden fazla tablo üzerinde döngüsel) fonksiyonları var.
`olimpos_admin.py` tarafında kullanıcı silme iki adımlı (onaylı) bir akışla tetikleniyor;
dosya sistemi seviyesinde (`os.remove`/`shutil.rmtree`) yıkıcı komutlara rastlanmadı.

### 4.5 Hataların sessizce yutulduğu noktalar
`except Exception` kullanımı çok yoğun (`olimpos_admin.py`'de 64, `Olimpos_Cripto_Bot.py`'de
49 kez). Özellikle dikkat çekenler:
- `Olimpos_Cripto_Bot.py:2107-2147` (cleanup) — art arda `except Exception: pass`.
- `strategies/alarm_strateji.py:3143-3147` — SL iptalinde hata olursa sessizce geçilip
  yeni stop emri oluşturulmaya devam ediliyor → **borsada birden fazla stop emri kalma
  riski**.
- `coin_scanner.py:73-80` — bir `except` bloğunun içine muhtemel bir girinti hatasıyla
  sıralama/cache kodu gömülmüş görünüyor; bu haliyle normal akışta hiç çalışmıyor
  olabilir (potansiyel bug, EzoEzgi'ye taşınmadan önce doğrulanmalı).
- Proje genelinde özel exception sınıfı sayısı çok az (`OrderCreationError` dışında
  yok) — hata ayrımı çoğunlukla `str(e)` içeriğine veya borsa hata koduna bakılarak
  yapılıyor.

---

## 5. EzoEzgi'ye Taşınabilecek Parçalar

Bunlar **mimari desen/konsept** olarak değerli; birebir kopya değil, EzoEzgi'nin kendi
mimarisine (Python 3.12, FastAPI, JSONL audit log, L0–L3 risk modeli) uyarlanarak
yeniden yazılmalı:

1. **Risk kill-switch deseni** (`core/risk_kill_switch.py`) — günlük drawdown limiti
   aşıldığında yeni pozisyon açılışını global olarak engelleme fikri; EzoEzgi'nin
   `policies/risk/*.yaml` + circuit breaker (BACKLOG B024) tasarımı için doğrudan
   referans.
2. **Execution portfolio gate** (`core/execution_portfolio_gate.py`) — aynı sembolde
   çift pozisyon açılmasını / limit aşımını engelleyen kapı deseni.
3. **Telegram rate-limit + retry sarmalayıcısı** (`telegram_rate_limit.py`) — projedeki
   en olgun retry/backoff implementasyonu; TR-EN köprüsünün Telegram/mesajlaşma
   kanalına çıkacağı senaryoda doğrudan ilham alınabilir.
4. **Config hot-reload deseni** (`config_service.py`) — mtime kontrolüyle JSON config'i
   restart gerektirmeden yeniden yükleme; EzoEzgi PLAN.md T5 (config loader) ile
   birebir örtüşüyor.
5. **RBAC izin sistemi** (`config/permissions.json` + `olimpos_admin.py` izin
   kontrolleri) — rol/izin ayrımı deseni, EzoEzgi'nin onay seviyeleri (L0–L3) ve
   admin/kullanıcı ayrımı için referans.
6. **Adaptif strateji ayarı** (`StrategyAdaptiveTuner.py`) — guardrail'li (min gözlem
   sayısı, cooldown, adım limiti, bounds clamp) kapalı-döngü otomatik parametre ayarı
   deseni; EzoEzgi finance execution'ın ileri fazında (Faz 2+) değerlendirilebilir.
7. **Exchange execution soyutlaması ihtiyacı** — `settings/execution/*_al_sat.py`
   dosyaları borsa başına ayrı ayrı yazılmış (kod tekrarı yüksek); EzoEzgi'de bunun
   yerine tek bir `ExchangeConnector` arayüzü + borsa adaptörleri olarak tasarlanmalı
   (BACKLOG B021 ile birebir örtüşüyor).
8. **Logging altyapısı deseni** (`logger_config.py`) — rotating file handler + amaca
   özel logger'lar + Windows dosya kilidi için özel `SafeRotatingFileHandler` çözümü;
   EzoEzgi'nin audit logger'ı (PLAN.md T17) için pratik bir referans (ama JSONL
   append-only formatına adapte edilmeli, ADR-009).
9. **Rejim/trend filtresi** (`strategies/alarm_system/regime.py`, `trend_analyzer.py`)
   — piyasa rejimine göre strateji davranışını değiştirme fikri, ileri faz strateji
   motoru tasarımı için referans.

## 6. Taşınmaması Gereken Parçalar

1. **`config/constants.py`, `data/olimpos_data.py`, `Olimpos_api_MEXC.py`** —
   hardcoded credential-benzeri literal içerdiği tespit edildi; **hiçbir biçimde**
   (ne kod ne pattern olarak) birebir kopyalanmamalı. Yeniden yazılırken secret'lar
   sıfırdan, yalnızca `.env`/vault üzerinden okunacak şekilde tasarlanmalı (ADR-010).
2. **Çift Telegram kütüphanesi (`python-telegram-bot` + `pyTelegramBotAPI`/`telebot`)**
   — mimari tutarsızlık; EzoEzgi tek bir kütüphaneyle (muhtemelen `python-telegram-bot`)
   başlamalı.
3. **Plaintext API key/secret saklama** (`data/olimpos_data.py`, `data/credentials_repo.py`
   deseni) — bu davranış birebir taşınmamalı; EzoEzgi'de kullanıcı borsa secret'ları en
   azından uygulama seviyesinde şifrelenmeli (envelope encryption / KMS / Fernet).
4. **Dosya bazlı `pip freeze` ile `requirements.txt` otomatik güncelleme**
   (`update_requirements.py`, ana bottan her başlatmada koşullu çağrılıyor) — üretim
   ortamında dependency dosyasının çalışma zamanında kendi kendini güncellemesi
   öngörülemezlik/güvenlik riski taşır; taşınmamalı.
5. **Sessizce yutulan `except Exception: pass` blokları** (madde 4.5) — desen olarak
   taşınmamalı; EzoEzgi'de her `except` bloğu en azından loglamalı, kritik yollarda
   (emir gönderimi, SL iptali) özel exception tipleri kullanılmalı.
6. **`bitget/`, `okx/`, `ta-lib/` vendored klasörleri** — üçüncü taraf kaynak kodu;
   gerekirse pip paketleri üzerinden bağımlılık olarak eklenmeli, kaynağıyla
   taşınmamalı.
7. **`models/*.pkl` (150-220MB ML modelleri), `logs/`, `alarm_raporlari/`,
   `analytics/*.jsonl` çalışma zamanı verileri** — bunlar veri/artefakt, kod değil;
   migration kapsamı dışında (gerekirse ayrı bir veri taşıma kararıyla ele alınır).
8. **"test için silinecek.py"** — yazarı tarafından zaten "silinecek" olarak
   işaretlenmiş geçici debug scripti; taşınmadı.

## 7. Refactor Önerileri

1. **Secret yönetimini merkezi hale getir** — tüm `os.getenv(KEY, "<fallback>")`
   paternlerini kaldır; `KEY` bulunamazsa açıkça hata fırlat (fail-fast), asla sessiz
   hardcoded fallback kullanma. EzoEzgi'de ADR-010 zaten bu yönde.
2. **Tek borsa soyutlama katmanı** — 6 ayrı `*_al_sat.py` dosyasındaki tekrar eden
   emir gönderme mantığını tek bir `ExchangeConnector` protokolü + borsa adaptörleri
   olarak yeniden yaz (BACKLOG B021).
3. **Özel exception hiyerarşisi** — `OrderCreationError` deseni genişletilmeli:
   `InsufficientBalanceError`, `RateLimitError`, `InvalidSymbolError` gibi tipler
   tanımlanıp `str(e)` parse etmek yerine tip bazlı hata yönetimine geçilmeli.
4. **Tek Telegram kütüphanesi** — `python-telegram-bot` veya `pyTelegramBotAPI`'den
   birine karar verip diğerini tamamen kaldır.
5. **Kullanıcı secret'larını şifrele** — DB'de plaintext API key/secret yerine
   envelope encryption (ör. uygulama seviyesinde Fernet + KMS'te tutulan master key).
6. **`coin_scanner.py` girinti/mantık hatasını doğrula** — 4.5'te belirtilen şüpheli
   `except` bloğu içi kod, EzoEzgi'ye herhangi bir mantık taşınmadan önce netleştirilmeli.
7. **Audit/log rotasyonu** — sürekli büyüyen `analytics/*.jsonl` dosyaları için
   rotasyon/arşivleme mekanizması yok; EzoEzgi'nin JSONL audit logger'ı (T17) baştan
   rotasyon/arşivleme stratejisiyle tasarlanmalı.
8. **Merkezi retry/backoff yardımcı fonksiyonu** — şu an yalnızca
   `telegram_rate_limit.py`'de olgun bir retry var; borsa emir gönderimlerinde tutarlı,
   paylaşılan bir retry/backoff yardımcı fonksiyonu olmalı.

---

*Bu rapor, `archive/gelisen_bot_snapshot/` altına kopyalanan dosyalar için referans
niteliğindedir — bkz. `GELISEN_BOT_FILE_MAP.md`. Güvenlik bulguları için ayrıca bkz.
`GELISEN_BOT_SECURITY_NOTES.md`. Taşıma planı için bkz. `GELISEN_BOT_MIGRATION_PLAN.md`
ve `GELISEN_BOT_PARITY_CHECKLIST.md`.*
