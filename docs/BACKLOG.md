# BACKLOG — EzoEzgi Ops

> PLAN.md içindeki aktif sprint kapsamı dışında kalan tüm istekler buraya yazılır.
> Sprint ortasında PLAN.md kapsamı değişmez; yeni fikir/istek önce buraya düşer.

| ID | Başlık | Öncelik | Durum | Faz | Sorumlu Ajan | Not |
|---|---|---|---|---|---|---|
| B001 | Kök `README.md` + proje özeti | Yüksek | Açık | Faz 0 | — | PLAN.md T3 ile aynı, referans amaçlı |
| B002 | Config loader — hot reload | Yüksek | Açık | Faz 0 | Orchestrator | PLAN.md T5 |
| B003 | Ollama local runtime kurulum betiği | Yüksek | Açık | Faz 1 | Bridge Agent | Model indirme + sağlık kontrolü |
| B004 | Whisper STT entegrasyonu | Orta | Açık | Faz 3 | Voice Agent | services/stt-whisper iskeleti boş |
| B005 | TTS servis seçimi (local model kararı) | Orta | Açık | Faz 3 | Voice Agent | DECISIONS.md'de ADR gerekiyor |
| B006 | Finance Engine veri modeli tasarımı | Orta | Açık | Faz 2 | Finance Agent | Harcama/gelir şeması |
| B007 | Social Engine onay akışı (auto-post guard) | Yüksek | Açık | Faz 5 | Social Agent | §8 güvenlik mekanizmasına bağımlı |
| B008 | Research Engine — web erişim politikası | Orta | Açık | Faz 2 | Research Agent | Offline/online fallback davranışı |
| B009 | Doc Ingestion — desteklenecek dosya formatları | Düşük | Açık | Faz 2 | Doc Agent | PDF/DOCX/TXT önceliklendirme |
| B010 | GSM Gateway — sağlayıcı/donanım seçimi | Orta | Açık | Faz 3 | Device Agent | SIM/modem tipi netleşmeli |
| B011 | Bluetooth cihaz senaryoları | Düşük | Açık | Faz 3+ | Device Agent | Kapsam MASTER_ROADMAP §4'te ertelendi |
| B012 | Gesture-vision — privacy-by-default doğrulama | Yüksek | Açık | Faz 5 | Device Agent | Görüntü diske yazılmama garantisi |
| B013 | Admin Panel — ilk wireframe | Düşük | Açık | Faz 5 | — | apps/admin-panel şu an boş |
| B014 | Mobile PWA — offline cache stratejisi | Orta | Açık | Faz 3 | — | Service worker tasarımı |
| B015 | Prometheus/Grafana dashboard seti | Düşük | Açık | Faz 6 | — | infra/monitoring iskeleti boş |
| B016 | OSV taramasının CI'ya bağlanması | Orta | Açık | Faz 4 | — | CI henüz kurulu değil |
| B017 | Audit log şeması (append-only) | Yüksek | Açık | Faz 4 | Orchestrator | data/audit formatı netleşmeli |
| B018 | Risk seviyesi taksonomisi detaylandırma | Yüksek | Açık | Faz 4 | Orchestrator | low/medium/high/irreversible kriterleri; T13'te ilk çalışan sürüm (`risk_engine.py` + `tool_risk_policy.yaml`) teslim edildi, Faz 4'te daha ayrıntılı kriterler/hardening bekliyor |
| B019 | Çok dilli genişleme (TR/EN dışı) | Düşük | Açık | Backlog | Bridge Agent | Şimdilik kapsam dışı, ADR gerektirir |
| B020 | Native mobil uygulama ihtiyacı değerlendirmesi | Düşük | Açık | Backlog | — | PWA yeterli mi — ayrı ADR |
| B021 | Exchange connector abstraction (Binance/Bybit/BtcTurk vb.) | Yüksek | Açık | Faz 2 | Finance Agent | Ortak arayüz, borsa-özel adaptörler; gerçek bağlantı bu maddede yok |
| B022 | Order sizing policy | Yüksek | Açık | Faz 2 | Finance Agent | L2/L3 eşiklerini belirleyen pozisyon büyüklüğü kuralları; policies/risk/finance.yaml ile ilişkili |
| B023 | Stop-loss / take-profit policy | Yüksek | Açık | Faz 2 | Finance Agent | Otomatik risk sınırlama kuralları; execution mock (PLAN.md T19) sonrası netleşecek |
| B024 | Circuit breaker (max günlük zarar) | Yüksek | Açık | Faz 2 | Finance Agent | Eşik aşılınca tüm L2/L3 işlemleri otomatik durdurma; RUNBOOK kill switch ile ilişkili |
| B025 | API permission validator (withdraw=false kontrolü) | Yüksek | Açık | Faz 2 | Finance Agent | Bağlantı kurulmadan önce API key izinlerini doğrular; MASTER_ROADMAP.md §5 zorunlu ön koşul |
| B026 | Gelisen_Bot parity checklist | Yüksek | Açık | Faz 2 | Finance Agent | `docs/imports/GELISEN_BOT_PARITY_CHECKLIST.md`; T19'un yeniden açılması için ön koşul (ADR-011) |
| B027 | Telegram command adapter tasarımı | Orta | Açık | Faz 2 | Bridge Agent | Gelisen_Bot'un ikili Telegram kütüphanesi (python-telegram-bot + telebot) karmaşasından kaçınacak tek-kütüphane adapter; `GELISEN_BOT_ANALIZ_RAPORU.md` §3 referans |
| B028 | Strategy abstraction katmanı | Orta | Açık | Faz 2 | Finance Agent | Gelisen_Bot `strategies/base_strategy.py`/`strategy_v1.py` deseninden ilham; EzoEzgi Finance Agent'a uyarlanacak |
| B029 | Dry-run replay test altyapısı | Orta | Açık | Faz 2 | Finance Agent | Geçmiş piyasa verisiyle strateji/risk motorunu gerçek para olmadan (L1) tekrar oynatma; Gelisen_Bot'ta karşılığı yok, yeni yetenek |
| B030 | Kullanıcı borsa credential encryption | Yüksek | Açık | Faz 2 | Finance Agent | Gelisen_Bot'ta plaintext DB saklama tespit edildi (`GELISEN_BOT_SECURITY_NOTES.md` P1); EzoEzgi'de envelope encryption zorunlu, B021 öncesi ön koşul |
| B031 | Gerçek TR-EN çeviri/NLU entegrasyonu | Yüksek | **Kısmen tamamlandı — quality gate hazır, canlı ölçüm bekliyor** | Faz 1 | Bridge Agent | Adaptör + bayrak (`ollama_nlu.py`, `NLU_PROVIDER`) tamam. Quality gate altyapısı da tamam: 50 örnekli golden set (`tests/fixtures/nlu_golden_tr.jsonl`) + değerlendirme aracı (`tools/eval_nlu.py`) + kabul eşikleri. Bu ortamda Ollama kurulu olmadığından **canlı ölçüm hiç yapılamadı** (`NOT_EVALUATED`, bkz. `reports/nlu_eval_20260812.md`) — mock referans sonucu: intent accuracy %92, entity match %0 (mock'ta entity çıkarımı hiç yok). Tamamlanma koşulu: Ollama kurulu bir ortamda `python tools/eval_nlu.py` çalıştırılıp 5 eşiğin (intent≥%90, entity≥%85, parse-error≤%2, fallback≤%5, p95≤2.5s) hepsi PASS olması — bkz. `docs/RUNBOOK.md` "Faz 1 — Ollama NLU Entegrasyonu / Quality Gate". Hazırlık (`feature/phase1-b031-entity-schema-prompt`): `ollama_nlu.py`'ye `ENTITY_SCHEMA_HINTS` eklendi, prompt artık `RUN_ECHO` için beklenen `value` alanını modele bildiriyor — entity_match_rate eşiğini geçme ihtimalini artırmayı hedefler; bu tek başına B031'i tamamlamaz, hâlâ canlı ölçüm gerekiyor |
| B032 | Python bağımlılık dosyası (dev dependencies) | Orta | Açık | Faz 0/1 | — | `pytest` + `pyyaml` şu an yalnızca `.venv`'e ad-hoc kuruldu; reproducibility için `pyproject.toml` `[project.dependencies]` veya `requirements-dev.txt` eklenmeli |
| B033 | Alias matcher'da Türkçe ek toleransı | Düşük | Açık | Backlog | Bridge Agent | `alias_matcher.py` yalnızca tam kelime eşleşmesi yapıyor; "ezgi'ye" gibi ek almış formlar kapsam dışı bırakıldı (bkz. `tests/test_alias_matcher.py` "EZGİYE söyle" negatif vakası) |
| B034 | `cli-runner` genel whitelist mekanizması (T12) | Yüksek | **Kapalı** | Faz 0 (Gün 10-11) | — | 2026-08-13'te T12 ile tamamlandı: `tools/cli-runner/src/runner.py` + `config/cli_whitelist.json` (bkz. ADR-012); `echo_runner.py` artık bunu kullanıyor |
| B035 | Risk taksonomisi uyumlulaştırma (genel vs finans) | Orta | Açık | Faz 2 | Orchestrator | T13'ün genel `low/medium/high/irreversible` şeması (`tool_risk_policy.yaml`) ile finansın `L0-L3` şeması (T18, MASTER_ROADMAP.md §5) şu an paralel/ayrı; Faz 2'de finans task'ları genel registry'ye eklenirken iki şemanın nasıl ilişkileneceği netleşmeli |
