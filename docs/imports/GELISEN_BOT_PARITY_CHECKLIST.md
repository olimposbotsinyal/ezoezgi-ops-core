# Gelisen_Bot → EzoEzgi — Parity Checklist

> Gelisen_Bot'un mevcut yeteneklerinin EzoEzgi Finance Engine'e göre durumu.
> Efor: **S** (≤1 gün), **M** (2-5 gün), **L** (>5 gün / ayrı bir alt-faz).
> Bu checklist tamamlanmadan (özellikle "Birebir Taşınacak" ve "Yeniden Yazılacak"
> P0/P1 maddeleri) finans execution EzoEzgi'de **açılmayacak** (bkz. DECISIONS.md
> ADR-011, PLAN.md Paused (Phase-2+) etiketi).

## Birebir Taşınacak (mimari desen olarak, kod olarak değil)

| Özellik | Kaynak (Gelisen_Bot) | EzoEzgi karşılığı | Efor | Bağımlılık |
|---|---|---|---|---|
| Günlük drawdown kill-switch | `core/risk_kill_switch.py` | `policies/risk/finance.yaml` + circuit breaker (BACKLOG B024) | M | ADR-009 (audit log) |
| Pozisyon/portföy giriş kapısı | `core/execution_portfolio_gate.py` | Risk motoru — `open_position_mock` öncesi kontrol katmanı | M | PLAN.md T19 |
| Telegram rate-limit + retry | `telegram_rate_limit.py` | Bildirim/onay kanalı retry sarmalayıcısı (ileri faz) | S | — |
| Config hot-reload (mtime bazlı) | `config_service.py` | PLAN.md T5 (config loader) | S | PLAN.md T5 |
| RBAC rol/izin modeli | `config/permissions.json` + `olimpos_admin.py` | Onay seviyeleri (L0-L3) + admin/kullanıcı ayrımı | M | MASTER_ROADMAP.md §5 |
| Rotating file + amaca özel logger | `logger_config.py` | JSONL audit logger (PLAN.md T17, ADR-009) — format değişecek | S | PLAN.md T17 |

## Yeniden Yazılacak (davranış korunacak, kod tamamen yeni)

| Özellik | Kaynak (Gelisen_Bot) | Neden yeniden yazılıyor | Efor | Öncelik |
|---|---|---|---|---|
| Borsa emir gönderimi (open/close) | `settings/execution/*_al_sat.py` (6 dosya, yüksek tekrar) | Tek `ExchangeConnector` soyutlaması + withdraw=false validasyonu + L0-L3 etiketleme baştan gömülü olmalı | L | P0 |
| Kullanıcı borsa credential saklama | `data/olimpos_data.py` / `data/credentials_repo.py` | Plaintext saklama kabul edilemez; envelope encryption ile yeniden yazılacak | M | P0 |
| Secret/config okuma | `config/constants.py` | Hardcoded fallback yasak; fail-fast + `.env`/vault (ADR-010) | S | P0 |
| Trailing stop yönetimi | `core_trailing.py`, `settings/trailing_manager.py` | Konsept değerli, ama L2/L3 onay akışına bağlanarak yeniden yazılacak | M | P1 |
| Adaptif strateji ayarı | `StrategyAdaptiveTuner.py` | Guardrail deseni korunacak, config formatı EzoEzgi JSON+.env standardına (ADR-010) uyarlanacak | M | P2 (Faz 2+) |
| Sinyal/strateji motoru | `strategies/*`, `strategies/alarm_system/*` | Konsept (tarama→AI→sinyal→execution plan) korunacak, kod EzoEzgi orchestrator/agent modeline (Finance Agent) uyarlanacak | L | P1 |
| Özel exception hiyerarşisi | Yalnızca `OrderCreationError` var | Daha kapsamlı, tip bazlı hata yönetimi (`InsufficientBalanceError` vb.) | S | P1 |

## İptal Edilecek (taşınmayacak)

| Özellik | Kaynak (Gelisen_Bot) | Neden iptal |
|---|---|---|
| Çalışma zamanında `pip freeze` ile `requirements.txt` güncelleme | `update_requirements.py` | Üretimde öngörülemezlik/güvenlik riski. |
| İkinci Telegram kütüphanesi (`pyTelegramBotAPI`/`telebot`) | `requirements.txt`, `Olimpos_Cripto_Bot.py:2151` | Mimari tutarsızlık; tek kütüphane (`python-telegram-bot`) yeterli. |
| Vendored borsa SDK kaynak kopyaları | `bitget/`, `okx/` | Gerekirse pip paketi olarak eklenir, kaynağıyla taşınmaz. |
| Sessiz `except Exception: pass` deseni | Proje geneli (özellikle cleanup, SL iptali) | Audit/log disiplinine aykırı; her hata loglanmalı. |
| E-posta tabanlı doğrulama akışı | `email_checker.py` | EzoEzgi'nin kapsamında şu an yok; ihtiyaç doğarsa ayrı ADR ile değerlendirilir. |
| Sentiment/sosyal veri modülleri (Reddit/Twitter/News) | `sentiment_analysis.py`, `praw`/`tweepy`/`newsapi` bağımlılıkları | Research/Social Agent kapsamına girer, Finance Engine'in parçası değil — ayrı değerlendirilecek. |
| ML modelleri (`.pkl`, 150-220MB) | `models/` | Veri/artefakt; EzoEzgi kendi model eğitim/versiyonlama stratejisini ayrı belirleyecek. |

## Bağımlılık Sırası (yüksek seviye)

1. **Secret/config temizliği** (ADR-010 uygulaması) → hiçbir execution kodu yazılmadan önce.
2. **Audit logger** (PLAN.md T17) → risk/execution kararlarının kaydı olmadan hiçbir mock/gerçek işlem yapılmaz.
3. **Risk policy + execution levels** (PLAN.md T18) → kill-switch/gate desenleri buna bağlı.
4. **ExchangeConnector soyutlaması** (BACKLOG B021) → mevcut 6 borsa dosyasının yerini alacak tek arayüz.
5. **Credential encryption** → gerçek API key saklamadan önce zorunlu ön koşul.
6. **Finance execution mock** (PLAN.md T19) → yukarıdakiler olmadan anlamsız (mock bile olsa risk/audit zincirini test etmeli).
7. **Onay akışı** (PLAN.md T20) → L2/L3 gerçek işlem öncesi son kapı.
8. **Gerçek borsa bağlantısı** (Faz 2 backlog: B021-B025) → 1-7 tamamlanmadan başlamaz.

Detaylı zaman çizelgesi için bkz. `GELISEN_BOT_MIGRATION_PLAN.md`.
