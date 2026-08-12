# Gelisen_Bot — Dosya Haritası (File Map)

> `kaynak_yol`: `C:\Users\Serkan\PycharmProjects\PycharmProjects\Gelisen_Bot\` köküne göre.
> `hedef_yol`: `D:\Projects\ezoezgi-ops\` köküne göre.
> Kaynak proje **hiç değiştirilmedi** — yalnızca kopya alındı.
> Kategoriler: core / telegram / strategy / utils / config / logging / data / test / docs.

## Kopyalanan Dosyalar (89)

### Kök dizin

| kaynak_yol | hedef_yol | kategori | hassas_mi | not |
|---|---|---|---|---|
| `Olimpos_Cripto_Bot.py` | `archive/gelisen_bot_snapshot/Olimpos_Cripto_Bot.py` | core | hayır | Ana giriş noktası, Telegram Application + scheduler + background task orkestratörü. |
| `README.md` | `archive/gelisen_bot_snapshot/README.md` | docs | hayır | Orijinal proje README'si, referans amaçlı. |
| `RealAIModel.py` | `archive/gelisen_bot_snapshot/RealAIModel.py` | strategy | hayır | Çoklu ML modeli (RF/XGB/LGBM/GB) eğitim + tahmin sınıfı. |
| `StrategyAdaptiveTuner.py` | `archive/gelisen_bot_snapshot/StrategyAdaptiveTuner.py` | strategy | hayır | Guardrail'li otomatik strateji parametre ayarı. |
| `__init__.py` | `archive/gelisen_bot_snapshot/__init__.py` | core | hayır | Boş paket dosyası. |
| `coin_scanner.py` | `archive/gelisen_bot_snapshot/coin_scanner.py` | strategy | hayır | Yüksek potansiyelli coin tarayıcı — analiz raporunda şüpheli bir `except` içi girinti hatası işaretlendi, taşınmadan önce doğrulanmalı. |
| `config_service.py` | `archive/gelisen_bot_snapshot/config_service.py` | config | hayır | JSON config hot-reload servisi (manuel/otomatik deep-merge). |
| `core_trailing.py` | `archive/gelisen_bot_snapshot/core_trailing.py` | strategy | hayır | Borsa bazlı trailing-stop adaptör supervisor'ı. |
| `daily_reset.py` | `archive/gelisen_bot_snapshot/daily_reset.py` | utils | hayır | Günlük state sıfırlama yardımcı fonksiyonları. |
| `email_checker.py` | `archive/gelisen_bot_snapshot/email_checker.py` | core | hayır | IMAP e-posta kontrolü + Telegram bildirimi. |
| `fix_json_file.py` | `archive/gelisen_bot_snapshot/fix_json_file.py` | utils | hayır | Bozuk (NaN/Infinity içeren) JSON dosyalarını onaran script. |
| `logger_config.py` | `archive/gelisen_bot_snapshot/logger_config.py` | logging | hayır | Merkezi logging kurulumu, rotating handler, amaca özel logger'lar. |
| `olimpos_admin.py` | `archive/gelisen_bot_snapshot/olimpos_admin.py` | telegram | hayır | Admin panel handler'ları + RBAC izin kontrolü. |
| `olimpos_channel.py` | `archive/gelisen_bot_snapshot/olimpos_channel.py` | telegram | hayır | Bildirim kanalı ekleme/silme/mesaj gönderme. |
| `price_manager.py` | `archive/gelisen_bot_snapshot/price_manager.py` | core | hayır | ccxt ile sembol/kontrat keşfi. |
| `report_full.py` | `archive/gelisen_bot_snapshot/report_full.py` | data | hayır | JSONL'den tam performans raporu üretimi (pandas). |
| `report_pnl.py` | `archive/gelisen_bot_snapshot/report_pnl.py` | data | hayır | PnL odaklı raporlama scripti. |
| `requirements.txt` | `archive/gelisen_bot_snapshot/requirements.txt` | config | hayır | Python bağımlılık listesi (~155 paket). |
| `sentiment_analysis.py` | `archive/gelisen_bot_snapshot/sentiment_analysis.py` | strategy | hayır | Duygu analizi modülü. |
| `signal_merkezi.py` | `archive/gelisen_bot_snapshot/signal_merkezi.py` | strategy | hayır | Manuel `/signal` komutu işleyici + execution plan inşası. |
| `telegram_rate_limit.py` | `archive/gelisen_bot_snapshot/telegram_rate_limit.py` | telegram | hayır | Merkezi rate-limit + retry/backoff sarmalayıcı — EzoEzgi için referans değeri yüksek. |
| `update_requirements.py` | `archive/gelisen_bot_snapshot/update_requirements.py` | utils | hayır | `pip freeze` ile `requirements.txt` otomatik güncelleme — EzoEzgi'ye taşınmaması önerildi (bkz. analiz raporu §6). |

### `analytics/`

| kaynak_yol | hedef_yol | kategori | hassas_mi | not |
|---|---|---|---|---|
| `analytics/performance_report.py` | `archive/gelisen_bot_snapshot/analytics/performance_report.py` | data | hayır | Performans raporu üretimi. |
| `analytics/segment_stats.py` | `archive/gelisen_bot_snapshot/analytics/segment_stats.py` | data | hayır | Segment bazlı istatistik hesaplama. |
| `analytics/timeline_hooks.py` | `archive/gelisen_bot_snapshot/analytics/timeline_hooks.py` | data | hayır | Zaman çizelgesi olay kancaları. |
| `analytics/user_trade_recorder.py` | `archive/gelisen_bot_snapshot/analytics/user_trade_recorder.py` | data | hayır | Kullanıcı işlem kaydı tutucu. |

### `charts/`

| kaynak_yol | hedef_yol | kategori | hassas_mi | not |
|---|---|---|---|---|
| `charts/chart_renderer.py` | `archive/gelisen_bot_snapshot/charts/chart_renderer.py` | utils | hayır | Pillow tabanlı sinyal/PnL kart render motoru (133KB). |

### `config/`

| kaynak_yol | hedef_yol | kategori | hassas_mi | not |
|---|---|---|---|---|
| `config/StrategyAdaptiveTuner_ayarlari.json` | `archive/gelisen_bot_snapshot/config/StrategyAdaptiveTuner_ayarlari.json` | config | hayır | Tuner'ın çalışma zamanı otomatik ayarları — içerik kontrol edildi, secret yok. |
| `config/api_action_settings.py` | `archive/gelisen_bot_snapshot/config/api_action_settings.py` | config | hayır | API aksiyon ayar tanımları. |
| `config/olimpos_tarama_ayarlari.json` | `archive/gelisen_bot_snapshot/config/olimpos_tarama_ayarlari.json` | config | hayır | Manuel tarama ayarları — içerik kontrol edildi, secret yok. |
| `config/permissions.json` | `archive/gelisen_bot_snapshot/config/permissions.json` | config | hayır | RBAC rol/izin tanımı — EzoEzgi onay seviyeleri tasarımı için referans. |
| **`config/constants.py`** | **kopyalanmadı** | config | **evet** | Hardcoded credential-benzeri literal (`BOT_TOKEN`/`ADMIN_PASSWORD`/`DB_PASS`/`EMAIL_PASSWORD` fallback'leri) — bkz. `GELISEN_BOT_SECURITY_NOTES.md`. |

### `core/`

| kaynak_yol | hedef_yol | kategori | hassas_mi | not |
|---|---|---|---|---|
| `core/cache_utils.py` | `archive/gelisen_bot_snapshot/core/cache_utils.py` | core | hayır | LRU cache yardımcı sınıfı. |
| `core/candidate_selector.py` | `archive/gelisen_bot_snapshot/core/candidate_selector.py` | core | hayır | Aday sembol seçici. |
| `core/execution_portfolio_gate.py` | `archive/gelisen_bot_snapshot/core/execution_portfolio_gate.py` | core | hayır | Pozisyon açma kapısı (çift pozisyon/limit kontrolü) — EzoEzgi risk motoru için referans. |
| `core/risk_kill_switch.py` | `archive/gelisen_bot_snapshot/core/risk_kill_switch.py` | core | hayır | Günlük drawdown limit kill-switch — EzoEzgi circuit breaker (BACKLOG B024) için referans. |
| `core/strategy_manager.py` | `archive/gelisen_bot_snapshot/core/strategy_manager.py` | core | hayır | Strateji registry deseni. |
| `core/symbol_resolver.py` | `archive/gelisen_bot_snapshot/core/symbol_resolver.py` | core | hayır | Sembol normalizasyonu. |

### `data/`

| kaynak_yol | hedef_yol | kategori | hassas_mi | not |
|---|---|---|---|---|
| `data/credentials_repo.py` | `archive/gelisen_bot_snapshot/data/credentials_repo.py` | data | kısmen | Kod içinde hardcoded secret **yok** (doğrulandı), ama kullanıcı API key/secret'larını DB'den plaintext okuyan bir fonksiyon barındırıyor — desen olarak taşınmamalı (bkz. analiz raporu §6), yalnızca referans için kopyalandı. |
| **`data/olimpos_data.py`** | **kopyalanmadı** | data | **evet** | Hardcoded `DB_PASS` fallback + kullanıcı API key plaintext okuma (`get_api_key`) — bkz. `GELISEN_BOT_SECURITY_NOTES.md`. |

### `patterns/`

| kaynak_yol | hedef_yol | kategori | hassas_mi | not |
|---|---|---|---|---|
| `patterns/pattern_engine.py` | `archive/gelisen_bot_snapshot/patterns/pattern_engine.py` | strategy | hayır | Grafik formasyon (pattern) tanıma motoru. |
| `patterns/pattern_shapes.py` | `archive/gelisen_bot_snapshot/patterns/pattern_shapes.py` | strategy | hayır | Formasyon veri tipleri (`PatternShape` dataclass). |

### `services/`

| kaynak_yol | hedef_yol | kategori | hassas_mi | not |
|---|---|---|---|---|
| `services/market_data.py` | `archive/gelisen_bot_snapshot/services/market_data.py` | core | hayır | Küçük piyasa verisi servis sarmalayıcısı. |

### `settings/`

| kaynak_yol | hedef_yol | kategori | hassas_mi | not |
|---|---|---|---|---|
| `settings/__init__.py` | `archive/gelisen_bot_snapshot/settings/__init__.py` | config | hayır | Paket dosyası. |
| `settings/binance_api_ayarlari.py` | `archive/gelisen_bot_snapshot/settings/binance_api_ayarlari.py` | telegram | hayır | Binance API key giriş/menü akışı (76KB). |
| `settings/bitget_api_ayarlari.py` | `archive/gelisen_bot_snapshot/settings/bitget_api_ayarlari.py` | telegram | hayır | Bitget API key giriş/menü akışı. |
| `settings/bitmart_api_ayarlari.py` | `archive/gelisen_bot_snapshot/settings/bitmart_api_ayarlari.py` | telegram | hayır | BitMart API key giriş/menü akışı. |
| `settings/bybit_api_ayarlari.py` | `archive/gelisen_bot_snapshot/settings/bybit_api_ayarlari.py` | telegram | hayır | Bybit API key giriş/menü akışı. |
| `settings/execution_plan_serialization.py` | `archive/gelisen_bot_snapshot/settings/execution_plan_serialization.py` | core | hayır | Execution plan (de)serileştirme. |
| `settings/execution_plan_types.py` | `archive/gelisen_bot_snapshot/settings/execution_plan_types.py` | core | hayır | Execution plan veri modelleri. |
| `settings/execution_settings_plan.py` | `archive/gelisen_bot_snapshot/settings/execution_settings_plan.py` | core | hayır | Execution plan inşa mantığı. |
| `settings/mexc_api_ayarlari.py` | `archive/gelisen_bot_snapshot/settings/mexc_api_ayarlari.py` | telegram | hayır | MEXC API key giriş/menü akışı. |
| `settings/okx_api_ayarlari.py` | `archive/gelisen_bot_snapshot/settings/okx_api_ayarlari.py` | telegram | hayır | OKX API key giriş/menü akışı. |
| `settings/trailing_manager.py` | `archive/gelisen_bot_snapshot/settings/trailing_manager.py` | strategy | hayır | Trailing-stop döngü yöneticisi (31KB). |
| `settings/trailing_price_feed.py` | `archive/gelisen_bot_snapshot/settings/trailing_price_feed.py` | strategy | hayır | Trailing için fiyat besleme akışı. |

### `settings/execution/` (gerçek emir gönderim motoru — mimari referans, aktif değil)

| kaynak_yol | hedef_yol | kategori | hassas_mi | not |
|---|---|---|---|---|
| `settings/execution/__init__.py` | `archive/gelisen_bot_snapshot/settings/execution/__init__.py` | core | hayır | Paket dosyası. |
| `settings/execution/binance_al_sat.py` | `archive/gelisen_bot_snapshot/settings/execution/binance_al_sat.py` | core | hayır | Binance gerçek emir gönderimi — riskli alan (analiz raporu §4.1), yalnızca mimari referans. |
| `settings/execution/binance_client_factory.py` | `archive/gelisen_bot_snapshot/settings/execution/binance_client_factory.py` | core | hayır | Binance ccxt client oluşturucu. |
| `settings/execution/bitget_al_sat.py` | `archive/gelisen_bot_snapshot/settings/execution/bitget_al_sat.py` | core | hayır | Bitget gerçek emir gönderimi — riskli alan, yalnızca mimari referans. |
| `settings/execution/bitmart_al_sat.py` | `archive/gelisen_bot_snapshot/settings/execution/bitmart_al_sat.py` | core | hayır | BitMart gerçek emir gönderimi — riskli alan, yalnızca mimari referans. |
| `settings/execution/bybit_al_sat.py` | `archive/gelisen_bot_snapshot/settings/execution/bybit_al_sat.py` | core | hayır | Bybit gerçek emir gönderimi — riskli alan, yalnızca mimari referans. |
| `settings/execution/daily_summary_manager.py` | `archive/gelisen_bot_snapshot/settings/execution/daily_summary_manager.py` | data | hayır | Günlük özet (equity/pnl) yönetimi. |
| `settings/execution/equity_binance.py` | `archive/gelisen_bot_snapshot/settings/execution/equity_binance.py` | core | hayır | Binance equity/bakiye anlık görüntüleme. |
| `settings/execution/equity_runner_binance.py` | `archive/gelisen_bot_snapshot/settings/execution/equity_runner_binance.py` | core | hayır | Equity ölçüm döngüsü runner'ı. |
| `settings/execution/equity_service.py` | `archive/gelisen_bot_snapshot/settings/execution/equity_service.py` | core | hayır | Genel equity servis katmanı. |
| `settings/execution/mexc_al_sat.py` | `archive/gelisen_bot_snapshot/settings/execution/mexc_al_sat.py` | core | hayır | MEXC gerçek emir gönderimi (en büyük execution dosyası, 112KB) — riskli alan, yalnızca mimari referans. |
| `settings/execution/okx_al_sat.py` | `archive/gelisen_bot_snapshot/settings/execution/okx_al_sat.py` | core | hayır | OKX gerçek emir gönderimi — riskli alan, yalnızca mimari referans. |
| `settings/execution/symbol_meta_provider.py` | `archive/gelisen_bot_snapshot/settings/execution/symbol_meta_provider.py` | core | hayır | Sembol meta verisi sağlayıcı. |
| `settings/execution/user_repo.py` | `archive/gelisen_bot_snapshot/settings/execution/user_repo.py` | data | hayır | Kullanıcı DB erişim katmanı (execution bağlamında). |

### `strategies/`

| kaynak_yol | hedef_yol | kategori | hassas_mi | not |
|---|---|---|---|---|
| `strategies/alarm_strateji.py` | `archive/gelisen_bot_snapshot/strategies/alarm_strateji.py` | strategy | hayır | `OlimposStrategy` ana sınıfı — projenin en büyük dosyası (208KB). |
| `strategies/base_strategy.py` | `archive/gelisen_bot_snapshot/strategies/base_strategy.py` | strategy | hayır | Soyut taban strateji sınıfı. |
| `strategies/strategy_v1.py` | `archive/gelisen_bot_snapshot/strategies/strategy_v1.py` | strategy | hayır | Strateji v1 somut implementasyonu. |
| `strategies/strategy_v2.py` | `archive/gelisen_bot_snapshot/strategies/strategy_v2.py` | strategy | hayır | Strateji v2 somut implementasyonu. |
| `strategies/strateji_ayarlari.py` | `archive/gelisen_bot_snapshot/strategies/strateji_ayarlari.py` | telegram | hayır | Strateji parametrelerinin Telegram üzerinden ayarlanması (108KB). |
| `strategies/technical_indicators.py` | `archive/gelisen_bot_snapshot/strategies/technical_indicators.py` | strategy | hayır | Teknik gösterge hesaplama (`calculate_all`). |
| `strategies/trend_analyzer.py` | `archive/gelisen_bot_snapshot/strategies/trend_analyzer.py` | strategy | hayır | Trend analiz motoru. |

### `strategies/alarm_system/`

| kaynak_yol | hedef_yol | kategori | hassas_mi | not |
|---|---|---|---|---|
| `strategies/alarm_system/__init__.py` | `archive/gelisen_bot_snapshot/strategies/alarm_system/__init__.py` | strategy | hayır | Paket dosyası. |
| `strategies/alarm_system/analytics.py` | `archive/gelisen_bot_snapshot/strategies/alarm_system/analytics.py` | data | hayır | Alarm sistemi analitik hesaplamaları. |
| `strategies/alarm_system/handlers.py` | `archive/gelisen_bot_snapshot/strategies/alarm_system/handlers.py` | telegram | hayır | Alarm kurulum akışının Telegram callback'leri — en büyük dosya (213KB). |
| `strategies/alarm_system/monitoring.py` | `archive/gelisen_bot_snapshot/strategies/alarm_system/monitoring.py` | strategy | hayır | Açık pozisyon/alarm izleme (104KB). |
| `strategies/alarm_system/orderbook.py` | `archive/gelisen_bot_snapshot/strategies/alarm_system/orderbook.py` | strategy | hayır | Emir defteri analizi. |
| `strategies/alarm_system/persistence.py` | `archive/gelisen_bot_snapshot/strategies/alarm_system/persistence.py` | data | hayır | Alarm/sinyal state kalıcılığı. |
| `strategies/alarm_system/regime.py` | `archive/gelisen_bot_snapshot/strategies/alarm_system/regime.py` | strategy | hayır | Piyasa rejimi (trend/yatay/volatil) tespiti. |
| `strategies/alarm_system/scanning.py` | `archive/gelisen_bot_snapshot/strategies/alarm_system/scanning.py` | strategy | hayır | Tarama/AI motoru (143KB). |
| `strategies/alarm_system/signal_flow.py` | `archive/gelisen_bot_snapshot/strategies/alarm_system/signal_flow.py` | strategy | hayır | Sinyal akış orkestrasyonu. |
| `strategies/alarm_system/stop_reason.py` | `archive/gelisen_bot_snapshot/strategies/alarm_system/stop_reason.py` | strategy | hayır | Stop nedeni sınıflandırma. |
| `strategies/alarm_system/symbols.py` | `archive/gelisen_bot_snapshot/strategies/alarm_system/symbols.py` | strategy | hayır | Sembol yardımcı fonksiyonları. |

### `tests/`

| kaynak_yol | hedef_yol | kategori | hassas_mi | not |
|---|---|---|---|---|
| `tests/test_grafik.py` | `archive/gelisen_bot_snapshot/tests/test_grafik.py` | test | hayır | Grafik render testi. |
| `tests/test_signal_generation.py` | `archive/gelisen_bot_snapshot/tests/test_signal_generation.py` | test | hayır | Sinyal üretim testi (52KB). |
| `tests/test_system.py` | `archive/gelisen_bot_snapshot/tests/test_system.py` | test | hayır | Sistem/entegrasyon testi. |

### `utils/`

| kaynak_yol | hedef_yol | kategori | hassas_mi | not |
|---|---|---|---|---|
| `utils/indicators.py` | `archive/gelisen_bot_snapshot/utils/indicators.py` | utils | hayır | Küçük teknik gösterge yardımcı fonksiyonları. |

---

## Kopyalanmayan Dosyalar

### Hassas (secret/credential riski nedeniyle)

| kaynak_yol | kategori | hassas_mi | not |
|---|---|---|---|
| `.env` | config | evet | Ortam değişkenleri — hiçbir zaman okunmadı/kopyalanmadı (kural gereği). |
| `config/constants.py` | config | evet | Hardcoded `BOT_TOKEN`/`ADMIN_PASSWORD`/`DB_PASS`/`EMAIL_PASSWORD` fallback'leri — bkz. `GELISEN_BOT_SECURITY_NOTES.md`. |
| `data/olimpos_data.py` | data | evet | Hardcoded ikinci `DB_PASS` fallback + kullanıcı API key plaintext okuma. |
| `Olimpos_api_MEXC.py` | core | evet | Satır 159'da doğrudan `API_KEY = "..."` string literal (boş değil). |

### Kapsam dışı (hassas değil, ama analiz/mimari referans amacına hizmet etmiyor)

| kaynak_yol | kategori | hassas_mi | not |
|---|---|---|---|
| `.venv/` | — | hayır | Python sanal ortamı — üçüncü taraf paket kurulumları, kaynak kod değil. |
| `.idea/`, `__pycache__/`, `.pytest_cache/` | — | hayır | IDE/derleme/test cache dosyaları. |
| `bitget/`, `okx/` | — | hayır | Vendored (gömülü) üçüncü taraf borsa SDK kaynak depoları — proje kodu tarafından doğrudan kullanılmıyor gibi görünüyor; gerekirse pip paketi olarak eklenir. |
| `ta-lib/` | — | hayır | TA-Lib C kütüphanesi kaynak/derleme dosyaları — pip paketi zaten `requirements.txt`'de mevcut. |
| `models/*.pkl` | data | hayır | Eğitilmiş ML modelleri (borsa başına 150-220MB) — veri/artefakt, kod değil; migration kapsamı dışında. |
| `logs/`, `alarm_raporlari/`, `analytics/*.jsonl`, `analytics/*.json` | data | hayır | Çalışma zamanı ürettiği log/rapor verisi — kod değil, ayrı bir veri taşıma kararı gerektirir. |
| `assets/` | — | hayır | Statik görsel/logo dosyaları — mimari analiz için gerekli değil. |
| `olimpos_cripto_bot.zip` | archive | belirsiz | 1.8MB arşiv — içeriği görev kısıtı gereği açılmadı/incelenmedi; kopyalanmadı. |
| `"test için silinecek.py"` | archive | hayır | Yazarı tarafından zaten "silinecek" olarak işaretlenmiş geçici debug scripti; düşük değer, kopyalanmadı. |

---

## Doğrulama

Kopyalama sonrası `archive/gelisen_bot_snapshot/` üzerinde bağımsız bir secret-pattern
taraması tekrar çalıştırıldı (Telegram token formatı + `PASSWORD`/`TOKEN`/`SECRET`/
`API_KEY`/`PASSPHRASE` literal ataması) — **sıfır eşleşme**. Detaylar için bkz.
`GELISEN_BOT_SECURITY_NOTES.md`.
