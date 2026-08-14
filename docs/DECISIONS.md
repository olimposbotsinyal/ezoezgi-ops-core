# DECISIONS — Architecture Decision Records (ADR)

> Format: Karar ID | Tarih | Karar | Gerekçe | Alternatif | Sonuç

---

## ADR-001
- **Tarih:** 2026-08-12
- **Karar:** Local model çalışma zamanı olarak **Ollama** kullanılacak.
- **Gerekçe:** Local-first hedefi; bulut API maliyeti/gecikmesi ve veri gizliliği
  riskleri olmadan model çalıştırma ihtiyacı.
- **Alternatif:** LM Studio, llama.cpp doğrudan entegrasyonu, bulut-only (OpenAI/Anthropic API).
- **Sonuç:** Kabul edildi. `services/tr-en-bridge` ve diğer ajanlar Ollama endpoint'i
  üzerinden model çağıracak (bkz. PLAN.md T8).

## ADR-002
- **Tarih:** 2026-08-12
- **Karar:** Konuşmadan metne (STT) için **Whisper** kullanılacak.
- **Gerekçe:** Açık kaynak, local çalışabilir, çok dilli destek (TR dahil) olgun.
- **Alternatif:** Vosk, bulut STT servisleri (Google/Azure).
- **Sonuç:** Kabul edildi. `services/stt-whisper` iskeleti bu kararla ayrıldı;
  entegrasyon Faz 3'te (bkz. MASTER_ROADMAP.md §7).

## ADR-003
- **Tarih:** 2026-08-12
- **Karar:** Ajan orkestrasyonu **Hermes/Crew-tarzı** (rol bazlı, görev grafiği ile
  ajan çağıran) bir yaklaşımla yapılacak.
- **Gerekçe:** Basit tek-model çağrısı yerine, uzmanlaşmış ajanların görev bazlı
  seçilip çağrılmasına ihtiyaç var (finans, araştırma, sosyal medya, cihaz vb.).
- **Alternatif:** LangGraph tarzı state-machine orkestrasyon, tamamen custom kod.
- **Sonuç:** Kabul edildi. Kesin kütüphane seçimi (CrewAI/Hermes-uyumlu framework
  vs. custom) Faz 1'de ayrı bir ADR ile netleşecek — bkz. BACKLOG.md.

## ADR-004
- **Tarih:** 2026-08-12
- **Karar:** İzleme (monitoring) için **Prometheus + Grafana** kullanılacak.
- **Gerekçe:** Self-hosted, local-first hedefiyle uyumlu, endüstri standardı,
  servis sayısı arttıkça (10+ servis) gözlemlenebilirlik kritik hale geliyor.
- **Alternatif:** Bulut APM (Datadog, New Relic), sade log dosyası + grep.
- **Sonuç:** Kabul edildi. Kurulum Faz 6'da (`infra/monitoring`) — bkz.
  MASTER_ROADMAP.md §7.

## ADR-005
- **Tarih:** 2026-08-12
- **Karar:** Bağımlılık güvenlik taraması için **OSV** (OSV-Scanner) kullanılacak.
- **Gerekçe:** Açık kaynak, ücretsiz, çoklu ekosistem (npm/pip/vb.) desteği, CI'ya
  kolay entegre edilebilir.
- **Alternatif:** Snyk, GitHub Dependabot (yalnızca GitHub'a bağımlı kalır).
- **Sonuç:** Kabul edildi. CI entegrasyonu Faz 4'te — bkz. BACKLOG.md B016.

## ADR-006
- **Tarih:** 2026-08-12
- **Karar:** Ana geliştirme dili **Python 3.12** olacak.
- **Gerekçe:** Ekosistem olgunluğu (ML/finans/otomasyon kütüphaneleri), Ollama/Whisper
  istemcileri ve borsa SDK'larının çoğunun Python desteği, tek dilde tutarlılık.
- **Alternatif:** Node.js/TypeScript, Go.
- **Sonuç:** Kabul edildi. `apps/` ve `services/` altındaki tüm servisler Python 3.12
  ile yazılacak (arayüz/PWA katmanı hariç).

## ADR-007
- **Tarih:** 2026-08-12
- **Karar:** Backend framework **FastAPI** olacak.
- **Gerekçe:** Async destek (borsa/websocket bağlantıları için önemli), otomatik
  OpenAPI şeması, Pydantic ile tip güvenliği, olgun ekosistem.
- **Alternatif:** Flask, Django REST Framework.
- **Sonuç:** Kabul edildi. `apps/api` ve dış HTTP arayüzü olan servisler FastAPI
  kullanacak.

## ADR-008
- **Tarih:** 2026-08-12
- **Karar:** Orchestrator yaklaşımı — başlangıçta **custom lightweight orchestrator**,
  ilerleyen fazda **CrewAI adaptörü** ile genişletilecek.
- **Gerekçe:** Faz 0–1'de basit, denetlenebilir, bağımlılığı az bir çekirdek
  (task graph + registry) yeterli; CrewAI gibi framework'lerin erken kilitlenmesi
  esnekliği azaltır. CrewAI adaptörü, ajan sayısı arttığında (Faz 2+) devreye girer.
- **Alternatif:** Baştan CrewAI/LangGraph'e bağlanmak, tamamen custom kalmaya devam.
- **Sonuç:** Kabul edildi. ADR-003'ü netleştirir/somutlaştırır. `apps/orchestrator`
  önce custom registry (bkz. PLAN.md T11) ile başlar; CrewAI adaptör kararı ayrı bir
  ADR ile Faz 2'de teyit edilecek.

## ADR-009
- **Tarih:** 2026-08-12
- **Karar:** Audit log formatı **JSONL, append-only** olacak
  (`data/audit/audit.log.jsonl`).
- **Gerekçe:** Satır bazlı, stream'e uygun, kolay parse edilebilir; append-only
  disiplinle geriye dönük değiştirilemez kayıt garantisi (özellikle finans işlemleri
  ve onay kararları için kritik — bkz. MASTER_ROADMAP.md §5, §8).
- **Alternatif:** SQLite, düz metin log, harici log servisi.
- **Sonuç:** Kabul edildi. Şema ve logger iskeleti PLAN.md T17 kapsamında yazılacak.

## ADR-010
- **Tarih:** 2026-08-12
- **Karar:** Config standardı **JSON + `.env`**; secret'lar yalnızca env/vault
  üzerinden sağlanacak, hiçbir zaman JSON config dosyalarına yazılmayacak.
- **Gerekçe:** JSON, statik/versiyonlanabilir config (identity, risk eşikleri vb.)
  için yeterli ve okunabilir; secret'ların (API key, withdraw-kapalı borsa anahtarları
  dahil) git'e sızma riskini `.env`/vault ayrımıyla azaltmak gerekiyor.
- **Alternatif:** YAML config, tüm config'i env değişkeninde tutmak, dedicated
  secret manager (Vault/1Password) baştan zorunlu kılmak.
- **Sonuç:** Kabul edildi. `.env` dosyaları `.gitignore`'da kalır; ileride vault
  entegrasyonu ayrı bir ADR ile değerlendirilebilir.

## ADR-011
- **Tarih:** 2026-08-12
- **Karar:** Finance execution deferred until legacy bot parity analysis —
  gerçek/mock finans işlem yürütme (PLAN.md T19 ve sonrası), Gelisen_Bot parity
  analizi tamamlanana kadar kapalı tutulacak.
- **Gerekçe:** Kaynak proje Gelisen_Bot (`C:\Users\Serkan\PycharmProjects\PycharmProjects\Gelisen_Bot`)
  incelendiğinde üç dosyada (`config/constants.py`, `data/olimpos_data.py`,
  `Olimpos_api_MEXC.py`) hardcoded credential-benzeri literal, kullanıcı borsa
  secret'larının DB'de plaintext saklanması ve borsa emir gönderiminde tekrarlanan/
  tutarsız kod deseni tespit edildi (bkz. `docs/imports/GELISEN_BOT_SECURITY_NOTES.md`,
  `GELISEN_BOT_ANALIZ_RAPORU.md` §4). Bu desenlerin EzoEzgi'ye kazara taşınmasını
  önlemek ve finans execution'ı sağlam bir mimari üzerine (credential encryption,
  tek `ExchangeConnector` soyutlaması, secret fail-fast okuma) kurmak için execution
  çalışması bilinçli olarak durduruldu.
- **Alternatif:** Analiz beklemeden T19'a paralel şekilde execution kodu yazmaya
  başlamak (reddedildi — aynı hataların EzoEzgi'de tekrarlanma riski yüksek);
  Gelisen_Bot'u hiç incelememek (reddedildi — mevcut, çalışır durumdaki bir botun
  deneyiminden öğrenmemek kaynak israfı olur).
- **Sonuç:** Kabul edildi. Yalnızca gerçek/mock **işlem yürütme** (T19 ve devamı,
  BACKLOG B021/B022/B023/B024/B025/B030) durduruldu; audit logger (T17), risk
  policy tanımları (T18) ve onay akışı simülasyonu (T20) genel/yeniden kullanılabilir
  altyapı oldukları için devam ediyor. Yeniden açılış kriteri:
  `docs/imports/GELISEN_BOT_PARITY_CHECKLIST.md`'deki "Yeniden Yazılacak" P0
  maddelerinin tamamlanması (bkz. `GELISEN_BOT_MIGRATION_PLAN.md` Faz 2).

## ADR-012
- **Tarih:** 2026-08-13
- **Karar:** Tüm tool-call/CLI çalıştırma katmanları için üç değişmez güvenlik
  kuralı zorunlu kılınır: **(1)** `shell=True` hiçbir zaman kullanılmaz —
  komutlar yalnızca liste argümanlı (`subprocess.run([executable, *args],
  shell=False, ...)`) çalıştırılır; **(2)** yalnızca whitelist'te
  (`config/cli_whitelist.json`) açıkça tanımlı komut adları çalıştırılabilir,
  whitelist dışı bir istek hiçbir process başlatmadan deterministic bir hata
  koduyla reddedilir; **(3)** her çağrıda zorunlu bir timeout uygulanır
  (whitelist girdisi → `default_timeout_seconds` → sabit varsayılan), süresiz
  çalışan bir çağrı asla olmaz.
- **Gerekçe:** `tools/cli-runner` (T12), orchestrator'ın gerçek sistem
  komutları çalıştırabildiği tek nokta; Gelisen_Bot analizinde (bkz.
  `docs/imports/GELISEN_BOT_ANALIZ_RAPORU.md` §4) tutarsız hata yönetimi ve
  merkezi olmayan güvenlik kontrollerinin bakım/güvenlik riski yarattığı
  görülmüştü — bu üç kural, EzoEzgi'nin ilk günden itibaren aynı hataları
  tekrarlamamasını garanti eder. `shell=True` yasağı, komut enjeksiyonunu
  (`;`, `&&`, `|` vb.) yapısal olarak imkansız kılar.
- **Alternatif:** Komut bazlı ad-hoc güvenlik kontrolleri (her tool kendi
  whitelist/timeout mantığını yazar — reddedildi, tutarsızlık riski yüksek);
  tam sandboxing/container izolasyonu (reddedildi, Faz 0 için aşırı karmaşık,
  ihtiyaç doğarsa ayrı bir ADR ile değerlendirilebilir).
- **Sonuç:** Kabul edildi. `tools/cli-runner/src/runner.py` bu üç kuralı
  uyguluyor; test: `tests/test_cli_runner.py::test_never_invokes_shell` bu
  kuralı programatik olarak doğruluyor. Gelecekteki tüm tool-runner'lar
  (browser-runner, file-runner vb.) aynı üç kurala tabi olmalı.

## ADR-013
- **Tarih:** 2026-08-13
- **Karar:** Gerçek NLU entegrasyonu (Faz 1, B031), `bridge.py`'nin mevcut
  `translate_and_extract()` arayüzünü değiştirmeden, `NLU_PROVIDER`
  ortam değişkeniyle seçilen bir **feature flag + adaptör** deseniyle
  eklenir. Yeni adaptör (`services/tr-en-bridge/src/ollama_nlu.py`) kendi
  canonical şemasını (`intent`/`entities`/`confidence`/`raw`) üretir;
  `bridge.py` bunu mevcut `task_en`/`confidence` sözleşmesine eşler. Varsayılan
  sağlayıcı **her zaman `mock`** kalır; `ollama` sağlayıcısı herhangi bir
  nedenle (servis kapalı, timeout, bozuk çıktı, tanımsız intent) başarısız
  olursa **sessizce mock'a düşer** — hiçbir zaman exception fırlatmaz veya
  orchestrator/risk/audit zincirini etkilemez.
- **Gerekçe:** Faz 0'da kurulan orchestrator, risk motoru (T13) ve audit
  logger (T17), `translate_and_extract()`'in çıktı şekline göre yazıldı;
  büyük bir refactor riske değmezdi ("keep changes minimal, modular,
  auditable"). Feature flag + safe-fallback deseni, gerçek modelin henüz
  bu ortamda kurulu/test edilmiş olmamasına rağmen kodun mainline'a
  güvenle girmesini sağlıyor — varsayılan davranış (mock) hiç değişmiyor,
  yalnızca bilinçli olarak `NLU_PROVIDER=ollama` ayarlanırsa yeni yol
  devreye giriyor.
- **Alternatif:** `translate_and_extract()`'i doğrudan Ollama'ya bağlayıp
  mock'u tamamen kaldırmak (reddedildi — geri dönüşü yok, gerçek model bu
  ortamda doğrulanamadığından riskli); orchestrator'ı NLU sağlayıcısından
  haberdar hale getirmek (reddedildi — gereksiz coupling, bridge/orchestrator
  servis ayrımını bozar, bkz. MASTER_ROADMAP.md §3).
- **Sonuç:** Kabul edildi. Test: `tests/test_ollama_nlu.py` (adaptör, 10
  test) + `tests/test_nlu_provider_flag.py` (bayrak + orchestrator smoke,
  12 test) — hepsi ağsız/deterministik (gerçek client `monkeypatch` ile
  değiştiriliyor). Gerçek bir Ollama modeliyle uçtan uca kalite/prompt
  doğrulaması bu ortamda yapılamadı (Ollama kurulu değil) — BACKLOG.md B031
  "kısmen tamamlandı" olarak işaretlendi, kalan iş orada.

## ADR-014
- **Tarih:** 2026-08-13
- **Karar:** Bir NLU/AI bileşeninin "tamamlandı" sayılabilmesi için, öznel
  değerlendirme yerine **golden set + nicel eşik (quality gate)** metodolojisi
  kullanılır. B031 için: `tests/fixtures/nlu_golden_tr.jsonl` (50 Türkçe
  örnek, dengeli kategoriler) + `tools/eval_nlu.py` (intent accuracy ≥%90,
  entity match ≥%85, parse error ≤%2, fallback ≤%5, p95 latency ≤2.5s).
  Gerçek sağlayıcı (`ollama`) bir ortamda kullanılamıyorsa araç sonucu
  **uydurmaz** — `NOT_EVALUATED` olarak işaretler ve sebebini raporlar;
  bu asla `PASS` ile karıştırılamaz.
- **Gerekçe:** "Mock çalışıyor, o zaman gerçek model de çalışır" varsayımı
  yanıltıcıdır — özellikle Türkçe, düşük kaynaklı bir dilde model kalitesi
  büyük farklılık gösterebilir. Nicel bir eşik olmadan "B031 tamamlandı"
  demek, doğrulanamamış bir iddiadır. Bu ortamda Ollama kurulu olmadığı
  için canlı ölçüm yapılamadı (bkz. `reports/nlu_eval_20260812.md`) — bu,
  metodolojinin tam olarak öngördüğü, dürüst bir "henüz doğrulanamadı"
  durumudur.
- **Alternatif:** Sınırlı sayıda manuel/anekdotsal test ile "yeterince iyi"
  denip B031'i kapatmak (reddedildi — ölçülemez, tekrarlanamaz, gelecekteki
  model/prompt değişikliklerinde regresyon tespit edilemez).
- **Sonuç:** Kabul edildi. Bu golden-set + eşik deseni, gelecekteki diğer
  AI/model tabanlı bileşenler (ör. sentiment analizi, strateji motoru) için
  de şablon olarak kullanılabilir. Test: `tests/test_eval_nlu.py` (30 test,
  loader/parser, metrik hesaplama, fallback/parse-error ayrımı dahil).

## ADR-015
- **Tarih:** 2026-08-14
- **Karar:** Ops Suite v0 (gerçek zamanlı ajan/asistan/onay kontrol
  merkezi) şu kapsamla uygulanacak: (1) domain modeli + olay sözleşmeleri
  tam/gerçek, (2) heartbeat/status-resolver/approval-queue mantığı
  tam/gerçek, (3) tek süreçli FastAPI + WebSocket sunucusu tam/gerçek
  (ADR-007'nin zaten sanksiyonladığı çerçeve, ilk gerçek kullanımı), (4)
  frontend yalnızca statik HTML/CSS/vanilla-JS bir v0 "shell" (tam
  animasyonlu 2D ofis sahnesi DEĞİL), (5) sesli komut mocked TR metin
  girdisiyle (gerçek mikrofon/GSM/kamera donanımı bu ortamda YOK). "Event
  bus", harici bir araç (Redis/Kafka) DEĞİL, `ws_manager.py::ConnectionManager`'in
  TEK bir uvicorn sürecinin bellek-içi asyncio yayıncısıdır.
- **Gerekçe:** Kullanıcının istediği tam kapsam (5 fazlı sprint: domain
  modeli, backend, animasyonlu frontend, ses+GSM+kamera entegrasyonu,
  E2E) gerçekte çok haftalık bir mühendislik işidir; bu ortamda ne
  tarayıcı-otomasyonu ne de ses/GSM/kamera donanımı vardır. Görev kısıtı
  "don't fabricate test results, mark NOT_COLLECTED/SKIPPED" açıkça bunu
  öngörüyordu — bu yüzden dürüstçe test edilebilir/gerçek olan HER ŞEY
  tam uygulandı, gerçek donanım gerektiren kısımlar açıkça ertelendi
  (bkz. BACKLOG.md B038-B040/B043) FABRİKE EDİLMEDİ.
- **Alternatif:** (a) Yalnızca planlama/doküman, hiç kod yazmamak —
  reddedildi, görev "IMPLEMENTATION SPRINT PLAN (NOW)" diye açıkça
  yürütme istiyordu. (b) Tüm 5 fazı "tam" iddia edip gerçekte
  test edilemeyen kısımları sessizce atlamak — reddedildi, "don't
  fabricate" kısıtını doğrudan ihlal ederdi.
- **Sonuç:** Kabul edildi. Bkz. `docs/OPS_SUITE_PRODUCT_SPEC.md`
  (tam kapsam tablosu), `docs/PLAN.md` T21-T27,
  `scripts/ops_suite_demo.py` (gerçek E2E kanıtı +
  `reports/ops_suite_demo_<UTC>/` NOT_COLLECTED bölümü).

## ADR-016
- **Tarih:** 2026-08-14
- **Karar:** `approval_stub.py`'nin durumsuz `WAITING_APPROVAL` dönüşü,
  `data/approvals/approval_queue.jsonl`'a append-only (ADR-009 deseni)
  yazan, replay-tabanlı (SUBMITTED-eksi-DECIDED) bir kalıcı kuyruğa
  (`ops_suite/approval_queue.py::ApprovalQueueStore`) genelleştirildi.
  `orchestrator.py`/`approval_stub.py`/`risk_engine.py` HİÇBİRİ
  DEĞİŞTİRİLMEDİ.
- **Gerekçe:** Ops Suite'in onay kuyruğu panelinin GERÇEK, sorgulanabilir
  bir veri kaynağına ihtiyacı var — `approval_stub.py` yalnızca bir
  stub'dur, hiçbir kalıcı/sorgulanabilir durum tutmaz. Yeni bir veritabanı
  bağımlılığı yerine, projenin ZATEN kanıtlanmış JSONL append-only
  deseni (ADR-009) tercih edildi.
- **Kapsam notu (önemli):** bu, ADR-011'in (finans yürürlüğü erteleme)
  VEYA PLAN.md T20'nin (finans-özel onay CLI simülasyonu, T19'a bağlı)
  YENİDEN AÇILMASI DEĞİLDİR — herhangi bir `high`/`irreversible` task
  için genel/yeniden-kullanılabilir altyapıdır (T17/T18'in aynı
  istisnası).
- **Alternatif:** SQLite/gerçek bir veritabanı (reddedildi — bu ölçekte
  gereksiz bir bağımlılık); durumu yalnızca bellek-içi tutmak
  (reddedildi — sunucu yeniden başladığında onay kuyruğu kaybolurdu).
- **Sonuç:** Kabul edildi. v0 bilinen sınırlaması: `decide()`'ın `actor`
  alanı serbest metindir, kimlik doğrulaması YOKTUR (bkz.
  `docs/IDENTITY_AND_DELEGATION_POLICY.md` §4). Test:
  `tests/test_ops_suite_approval_queue.py` (12 test).

## ADR-017
- **Tarih:** 2026-08-14
- **Karar:** Python bağımlılıkları artık PEP 735 `[dependency-groups]`
  ile `pyproject.toml`'da resmi olarak beyan ediliyor (`dev`:
  pytest/pyyaml, `ops-suite`: fastapi/uvicorn/websockets/httpx) —
  `[project.dependencies]` DEĞİL.
- **Gerekçe:** BACKLOG.md B032 ("Python bağımlılık dosyası") uzun süredir
  açıktı — pytest/pyyaml yalnızca ad-hoc `.venv`'e kuruluydu,
  reproducibility riski taşıyordu. Ops Suite'in FastAPI'yi (ADR-007'nin
  zaten sanksiyonladığı ama hiç kurulmamış çerçeve) ilk kez gerçekten
  kurması, bunu düzeltmek için doğal bir fırsattı.
- **`[project.dependencies]` DEĞİL, `[dependency-groups]` NEDEN:** bu
  repo, tek bir `pip install -e .` ile kurulabilir bir paket DEĞİLDİR VE
  olmamalıdır — birden çok ayrı `apps/*/src`/`services/*/src` kökü var;
  bunları tek bir ad alanına zorlamak riskli/kapsam dışı bir refactor
  olurdu. Dependency-groups, `[project]`/`[build-system]` tablosu
  GEREKTİRMEDEN `pip install --group <ad>` ile kurulabilir.
- **Alternatif:** `requirements-dev.txt` (B032'nin önerdiği diğer
  seçenek) — reddedildi, `pyproject.toml` zaten var ve tek dosyada
  tutmak daha az dosya-senkronizasyon riski taşır.
- **Sonuç:** Kabul edildi. FastAPI yığını KENDİ grubunda izole edildi
  (kök bağımlılık DEĞİL) — `scripts/ops/`'un stdlib-yalnız felsefesi
  ihlal edilmedi (bkz. `threshold_governance_core.py`'deki not).

## ADR-018
- **Tarih:** 2026-08-14
- **Karar:** Ops Suite v0 frontend'i saf HTML/CSS/vanilla JavaScript'tir
  — npm, bundler (webpack/vite), veya bir framework (React/Vue/Svelte)
  KULLANILMAZ. PWA manifest + asgari bir cache-first service worker
  (yalnızca statik shell dosyaları, API/WS trafiği ASLA önbelleklenmez)
  eklendi.
- **Gerekçe:** Repo'da şu ana kadar HİÇ JS/TS araç zinciri (`package.json`
  bile) yoktu — bir npm/bundler/framework seçimi, tek başına büyük ve
  geri dönüşü zor bir mimari karardır, "IMPLEMENTATION SPRINT PLAN (NOW)"
  kapsamında TEK TARAFLI verilmemesi gereken bir karardır. Saf HTML/CSS/JS,
  MASTER_ROADMAP.md §6 offline-first ilkesine de doğal olarak uyar (harici
  bir CDN/build adımı gerektirmez).
- **Alternatif:** React/Vite gibi modern bir yığın (reddedildi — bu
  oturumda unilateral olarak karar verilecek kadar büyük bir seçim,
  ayrıca gerçek bir tarayıcıda test edilemeyeceği için doğrulanamaz bir
  karmaşıklık eklerdi); Node bu makinede KURULU (v24.14.1) olsa da, bu
  yalnızca `node --check` ile sözdizimi doğrulaması YAPILABİLDİĞİ
  anlamına gelir, gerçek bir SPA çalıştırma/render doğrulaması YAPILAMAZ.
- **Sonuç:** Kabul edildi. Tam 2D ofis sahnesi/animasyon VE olası bir
  npm/framework benimsemesi `docs/BACKLOG.md` B038/B042'ye ertelendi.
  Doğrulama: `node --check` (sözdizimi) + `fastapi.testclient.TestClient`
  ile gerçek statik dosya sunumu testi (`tests/test_ops_suite_api.py::test_root_serves_frontend_index_html`) —
  görsel/etkileşimli doğrulama bu ortamda YAPILAMADI (SKIPPED, tarayıcı
  aracı yok).

## ADR-019
- **Tarih:** 2026-08-14
- **Karar:** Ops Suite onay uç noktaları (`POST /api/approvals/{id}/approve|reject`)
  için **bearer-token kimlik doğrulama + kapsam (scope) tabanlı
  yetkilendirme + owner-root-guard** eklendi (BACKLOG.md B044, SECURITY
  P0). Ayrıntılar: (1) her kimlik (`owner` veya `delegate`) bir token'a
  eşlenir, token DEĞERİ yalnızca ortam değişkeninde tutulur (config
  dosyasında ASLA); (2) `authority_source="owner"` kimlik kod
  seviyesinde HER ZAMAN tüm kapsamlara sahiptir; (3) `risk_level="irreversible"`
  (veya bilinmeyen risk seviyesi) onayı, delegate'in config'i ne
  yazarsa yazsın, YALNIZCA owner'a açıktır (defense-in-depth); (4)
  delegate'ler `approve:low`/`approve:medium`/`approve:high`/`approve:irreversible`/`reject`
  kapsamlarından bir alt küme alır, kapsam dışı eylem HTTP 403 ile
  reddedilir; (5) audit izine `actor_id`/`auth_method`/`authority_source`/`decision_scope`
  eklendi (`AuditLogger`'ın kendi şeması DEĞİŞTİRİLMEDİ, bu alanlar
  `details` sözlüğüne yazılıyor).
- **Gerekçe:** `docs/IDENTITY_AND_DELEGATION_POLICY.md` §4, T23/T24'ten
  beri Ops Suite'in bilinen, açık bir güvenlik açığını belgeliyordu:
  `actor` alanı serbest metindi, kimlik doğrulaması YOKTU — yalnızca
  `127.0.0.1` loopback izolasyonuna güveniliyordu. Kullanıcı bunu
  açıkça "SECURITY P0" olarak işaretleyip kapatılmasını istedi.
  Tam bir OAuth2/OIDC/mTLS katmanı, tek-kullanıcılı/yerel bir kontrol
  merkezi için orantısız bir mühendislik yükü olurdu; paylaşılan
  bearer-token + kapsam modeli, gerçek (mock/theater DEĞİL) bir
  yetkilendirme kontrolü sağlarken karmaşıklığı sistemin gerçek tehdit
  modeliyle (tek sahibi + olası birkaç güvenilir delegate, hepsi
  loopback üzerinden) orantılı tutuyor.
- **Alternatif:** (a) Tam OAuth2/OIDC entegrasyonu (reddedildi — bu
  ölçekte aşırı mühendislik, ayrıca bu ortamda gerçek bir identity
  provider'a karşı test EDİLEMEZDİ); (b) yalnızca "gizli bir URL
  path'i" veya IP allowlist'i (reddedildi — bu bir kimlik doğrulama
  DEĞİL, yalnızca gizlilik-yoluyla-güvenliktir); (c) `risk_level`'den
  BAĞIMSIZ, tek bir "approve" kapsamı (reddedildi — owner-root-guard'ın
  ayrım gücünü kaybederdi, `irreversible` ile `low` aynı kapsamda
  olurdu).
- **Sonuç:** Kabul edildi. Bkz. `apps/ops-suite/backend/src/ops_suite/identity.py`,
  `docs/PLAN.md` T28, `tests/test_ops_suite_identity.py` (25 test, yeni) +
  `tests/test_ops_suite_api.py`'nin B044 uzantıları (8 yeni test) +
  `scripts/ops_suite_demo.py`'nin gerçek 401/403 kanıt adımları.
  **Bilinen sınırlamalar** (BACKLOG'a henüz taşınmadı, bkz.
  `docs/IDENTITY_AND_DELEGATION_POLICY.md` §5): token rotasyon/iptal
  UI'ı yok, rate limiting yok, tek kimlik doğrulama yöntemi
  (bearer-token).

## ADR-020
- **Tarih:** 2026-08-14
- **Karar:** B038'in (tam animasyonlu 2D ofis sahnesi/ajan avatarları)
  frontend yığın kararı — **Seçenek A: saf Vanilla JS + HTML5 Canvas2D**
  (üçüncü bir kütüphane/framework/bundler EKLENMEDEN) — kabul edildi.
  BACKLOG.md B042'yi kapatır.

  **Değerlendirilen 2 seçenek:**

  | Kriter | A) Vanilla JS + Canvas2D | B) PixiJS (vendored UMD, CDN yok) |
  |---|---|---|
  | Offline-first | Mükemmel — sıfır yeni bağımlılık, mevcut PWA/service-worker önbellekleme deseniyle (ADR-018) birebir uyumlu | İyi — tek bir local dosya olarak vendor edilebilir (CDN'e bağımlı DEĞİL), ama ~500KB üçüncü taraf kod repoya girer |
  | Test edilebilirlik | Canvas pikselleri Playwright DOM sorgularıyla doğrudan görülemez (screenshot-diff veya `window.__debug_state__` köprüsü gerekir) — B039'un yeni Playwright altyapısıyla AYNI sınırlama, seçenekten BAĞIMSIZ | Aynı sınırlama (Canvas/WebGL tabanlı) — sahne grafiği (`displayObject` ağacı) test-zamanlı durum sorgusu için biraz daha yapılandırılmış bir yüzey sunar |
  | Performans | Küçük sayıda ajan avatarı (şu an ≤9 ajan) için fazlasıyla yeterli — WebGL'in sunduğu batching avantajı bu ölçekte fark yaratmaz | Daha yüksek performans tavanı (WebGL) ama bu ölçekte gözlemlenebilir bir kazanç değil — over-engineering riski |
  | Bakım yükü | Sprite/animasyon/hit-testing elle yazılır (daha fazla boilerplate) ama YENİ bağımlılık YOK — versiyon güncelleme/güvenlik takibi yükü sıfır | Sprite/animasyon yönetimi kütüphane tarafından sağlanır (daha az boilerplate) ama YENİ bir tedarik-zinciri yüzeyi (PixiJS sürüm takibi, güvenlik güncellemeleri, API kırılmaları) açar |

- **Gerekçe:** ADR-018 (2026-08-14, Ops Suite v0), bilinçli olarak
  npm/bundler/framework'ü ERTELEMİŞTİ — bu karar o kararı YENİDEN
  AÇMIYOR, üzerine İNŞA EDİYOR. B038'in gerçek kapsamı (küçük sayıda
  ajan avatarı + basit durum animasyonları — kalabalık bir oyun sahnesi
  DEĞİL) performans/geliştirme-hızı avantajının PixiJS'i haklı
  çıkaracağı bir eşiğin altında. MASTER_ROADMAP.md §6'nın offline-first
  ilkesi ve projenin genelinde tekrarlanan "yeni bağımlılık = yeni bakım
  yükü, yalnızca gerekliyse ekle" ilkesi (bkz. ADR-017'nin
  `dependency-groups` izolasyon gerekçesi), sıfır-bağımlılık seçeneğini
  destekliyor.
- **Alternatif (reddedildi):** (a) PixiJS — yukarıdaki tabloda
  gerekçelendirildiği gibi, bu ölçek için gereksiz karmaşıklık; B038'in
  kapsamı büyür/performans GERÇEKTEN darboğaz olursa AYRI bir ADR ile
  yeniden değerlendirilebilir. (b) React/Vue + bundler tam framework
  pivotu — reddedildi, ADR-018'in gerekçesini (tek taraflı büyük mimari
  karar, bu ortamda gerçek render doğrulaması YAPILAMAZDI) hâlâ
  geçerli; **ancak** B039 artık gerçek bir tarayıcı test altyapısı
  sağladığı için (bkz. ADR-021) bu itiraz kısmen zayıfladı — yine de
  kapsam/fayda oranı bu kararı desteklemiyor.
- **Sonuç:** Kabul edildi. B038'in gerçek uygulaması (sahne çizimi,
  sprite/animasyon durumu, ajan state→görsel eşlemesi) henüz
  YAPILMADI — bu ADR yalnızca YIĞIN kararını kilitliyor. Gelecekteki
  B038 implementasyonu `apps/ops-suite/frontend/js/scene.js` (yeni
  modül, mevcut `app.js`'i BOZMADAN) altında, mevcut `AgentPresence`
  şemasını (`schemas.py`) tüketerek inşa edilmeli.

## ADR-021
- **Tarih:** 2026-08-14
- **Karar:** Ops Suite için GERÇEK tarayıcı E2E test altyapısı
  (**Playwright**, `@playwright/test`) eklendi — `apps/ops-suite/e2e/`
  altında, repo'nun İLK `package.json`'ı. Bu, **yalnızca test-zamanlı
  tooling'dir** — ADR-018'in "sunulan frontend uygulaması npm/bundler
  KULLANMAZ" kararını DEĞİŞTİRMEZ (`apps/ops-suite/frontend/` hâlâ saf
  HTML/CSS/vanilla-JS, hiçbir build adımı YOK).
- **Gerekçe:** BACKLOG.md B039, bu ortamda "tarayıcı-otomasyon aracı
  YOK" diye tekrar tekrar NOT_COLLECTED işaretlenmişti (T26, T27
  notları). Bu oturumda GERÇEKTEN denendi: `npx playwright install
  chromium` ile ~306MB'lık gerçek bir Chrome for Testing ikili dosyası
  indirilip çalıştırıldı, `chromium.launch()` + gerçek DOM render +
  gerçek bir sayfaya `page.goto()` GERÇEKTEN başarılı oldu (bkz.
  `apps/ops-suite/e2e/tests/smoke.spec.js`, 2/2 PASS). Bu, önceki
  NOT_COLLECTED varsayımını **bu ortam için** tersine çeviren, doğrudan
  gözlemlenmiş bir kanıttır — fabrike edilmedi, gerçekten çalıştırıldı.
- **Kapsam notu (önemli):** bu, "her ortamda/CI'da her zaman tarayıcı
  otomasyonu mevcuttur" ANLAMINA GELMEZ — yalnızca BU oturumun/makinenin
  bu anda gerçek bir Chromium indirip çalıştırabildiğini kanıtlar.
  Gelecekteki bir CI/farklı ortam kurulumunda aynı adımın (`npm install`
  + `npx playwright install chromium`) tekrar doğrulanması gerekir.
- **Alternatif:** Puppeteer (reddedildi — Playwright'ın
  `@playwright/test` runner'ı, `globalSetup`/`globalTeardown` ve
  otomatik retry/trace desteğiyle daha az elle-yazılmış altyapı
  gerektiriyor); Cypress (reddedildi — gerçek bir alt-süreç sunucusuna
  karşı çalışmak Playwright kadar doğal değil, bu deponun "gerçek
  subprocess'e karşı test et" desenine — bkz. `scripts/ops_suite_demo.py`
  — Playwright daha yakın).
- **Sonuç:** Kabul edildi. `apps/ops-suite/e2e/global-setup.js`, gerçek
  `python -m ops_suite.server` alt-sürecini başlatır (PYTHONPATH,
  `scripts/ops_suite_demo.py::_subprocess_env()` ile AYNI listeyle elle
  senkron tutulur), testler bittikten sonra GERÇEKTEN sonlandırır.
  `tests/smoke.spec.js` (2 senaryo): kök sayfa + ajan kartı render'ı +
  gerçek WS bağlantısı; sesli komut gönderimi → onay kuyruğu DOM
  güncellemesi. Çalıştırma: `docs/RUNBOOK.md` "Ops Suite — Gerçek
  Tarayıcı E2E (B039)". `node_modules/`/`test-results/`/`playwright-report/`
  `.gitignore`'da; `package.json`/`package-lock.json`/config/test dosyaları
  normal izlenir.

## ADR-022
- **Tarih:** 2026-08-14
- **Karar:** Ajan presence kalıcılığı (PLAN.md T39, BACKLOG.md B041) için
  **JSONL append-only** deseni (ADR-009/ADR-016 ile AYNI) seçildi —
  SQLite veya başka bir veritabanına GEÇİLMEDİ. Tasarımın 3 temel
  parçası: (1) `presence_store.py::PresenceStore.append()` her
  `HeartbeatTracker` değişikliğini (T37'nin zaten ürettiği
  `presence_events` listesi üzerinden, `app.py::voice_command`
  içinde) bir satır olarak ekler; (2) `load_latest()` dosyayı BAŞTAN
  SONA okuyup HER `agent_id` için EN SON satırı döner ("last write
  wins" çakışma çözümü); (3) sunucu başlarken (`create_app()`),
  `HeartbeatTracker`'da HENÜZ kaydı OLMAYAN her `agent_id` için,
  persisted `last_heartbeat_ts` ORİJİNAL haliyle (yeniden
  damgalanmadan) `HeartbeatTracker.record(ts=...)`'e verilir.
- **Gerekçe (dürüstlük ilkesiyle uyum):** `last_heartbeat_ts`'in
  ORİJİNAL haliyle korunması kritik bir tasarım kararıdır — eğer
  yeniden başlatma anında "şimdi" ile damgalansaydı, sunucu bir ajanın
  restart'tan HEMEN ÖNCE gerçekten CANLI olduğunu (fabrike bir
  "yeniden canlanma" izlenimi) iddia etmiş olurdu. Bunun yerine,
  `HeartbeatTracker.resolve_state()`'in VAROLAN zaman-aşımı mantığı
  (`timeout_seconds=30s`) hiçbir ÖZEL DURUM KODU olmadan doğal olarak
  çalışır: restart'tan bu yana 30s'den fazla geçtiyse `offline`
  (doğru — kanıt yok), geçmediyse son bilinen durum geçerli sayılır
  (kısa bir restart penceresinde makul bir varsayım, hâlâ GERÇEK bir
  geçmiş kayda dayanıyor, uydurulmuş değil).
- **Çakışma çözümü kuralı:** Tohumlama YALNIZCA `HeartbeatTracker`'da
  HİÇBİR kaydı OLMAYAN `agent_id`'ler için uygulanır
  (`HeartbeatTracker.has_record()`, yeni) — DI ile önceden
  doldurulmuş bir tracker'ın (örn. testlerde) durumu SESSİZCE ÜZERİNE
  YAZILMAZ. Bu, "disk her zaman kazanır" gibi daha basit ama testleri
  öngörülemez kılacak bir kurala tercih edildi.
- **Alternatif:** (a) SQLite (reddedildi — ADR-016'nın "bu ölçekte
  gereksiz bağımlılık" gerekçesiyle tutarlı kalındı, JSONL zaten
  kanıtlanmış); (b) periyodik tam-anlık-görüntü dosyası (`snapshot.json`,
  her N saniyede bir üzerine yazılan) — reddedildi, append-only
  denetlenebilirlik/geçmiş izini kaybettirir VE ADR-009'un genel
  "hiçbir satır asla değiştirilmez" ilkesinden sapardı; (c) restart
  anında "şimdi" ile yeniden damgalama — reddedildi, yukarıdaki
  dürüstlük gerekçesiyle AÇIKÇA reddedildi.
- **Bilinen sınırlamalar (v0.1, dürüstçe):** Dosya sınırsız büyür
  (rotasyon/budama YOK — v0 ölçeğinde pratik bir sorun değil, gelecekte
  gerekirse ayrı bir madde); yalnızca `HeartbeatTracker`/`AgentPresence`
  kalıcı hale getirildi, `AssistantPresenceTracker`/`ApprovalQueueStore`'un
  KENDİ kalıcılığı (ikincisi zaten ADR-016 ile kalıcı) kapsam dışı.
  Test: `tests/test_ops_suite_presence_store.py` (7 test) +
  `tests/test_ops_suite_heartbeat.py::test_has_record_*` (2 test) +
  `tests/test_ops_suite_api.py::test_agent_presence_survives_simulated_restart`/`test_agent_presence_seed_does_not_override_existing_di_tracker_state`
  (2 test, gerçek "restart" simülasyonu — ikinci bir `create_app()`
  örneği, tamamen yeni bir `HeartbeatTracker` ile).
- **Sonuç:** Kabul edildi. Bkz. `apps/ops-suite/backend/src/ops_suite/presence_store.py`,
  `docs/PLAN.md` T39, `docs/BACKLOG.md` B041.

---

## ADR-023
- **Tarih:** 2026-08-14
- **Karar:** Çoklu-adımlı görev animasyonu (PLAN.md T45, BACKLOG.md B048)
  için 4 görsel aşama (kuyrukta/atandı/çalışıyor/tamamlandı), YENİ bir
  backend olayı/durumu İCAT EDİLMEDEN, tamamen ZATEN VAR OLAN
  `task.lifecycle` + `agent.presence` WS olaylarına eşlendi:
  `received`/`translating`/`risk_checked` (agent_id yok) → kuyrukta;
  nihai `task.lifecycle` olayı (agent_id artık var) → atandı; eşleşen
  `agent.presence` `working` (last_task_id çapraz referansı, B049'un
  onay-bağlantısı deseniyle AYNI) → çalışıyor; eşleşen `agent.presence`
  `idle` → tamamlandı.
- **Gerekçe (dürüstlük ilkesiyle uyum):** `schemas.py::TASK_LIFECYCLE_STATES`
  şemada `routed`/`executing` durumlarını TANIMLAR ama `voice_bridge.py`
  bunları HİÇBİR KOD YOLUNDA üretmez (yalnızca received/translating/
  risk_checked + nihai bir durum üretilir). Şemadaki tanımlı-ama-hiç-
  üretilmeyen durumları görsel aşamalara birebir eşlemeye çalışmak,
  var olmayan ara adımları FABRİKE etmek anlamına gelirdi (T33/T36'nın
  "working anı yakalanmadı, dürüstçe atlandı" ilkesiyle ÇELİŞirdi).
  Bunun yerine gerçekten yayınlanan olaylar kullanıldı.
- **"Tamamlandı" etiketinin GERÇEK anlamı:** `voice_bridge.py`'de
  working→idle heartbeat çevrimi KOŞULSUZDUR (görev başarılı/başarısız/
  onay-bekliyor olsun fark etmez, ikisi de aynı şekilde çalışır). Bu
  yüzden "tamamlandı" görsel aşaması yalnızca "ajan bu görev için
  İŞLEMEYİ BİTİRDİ" anlamına gelir, "görev BAŞARILI oldu" DEĞİL —
  başarısız/reddedilen/onay-bekleyen görevler de AYNI görsel aşama ile
  gösterilir. Bu, koda ve dokümana AÇIKÇA yazıldı (yanlış bir "başarı"
  izlenimi vermemek için).
- **Alternatif:** (a) `routed`/`executing` durumlarını backend'de
  GERÇEKTEN üretmek (heartbeat/task.lifecycle'a yeni ara kayıtlar
  eklemek) — reddedildi, kullanıcı talebi "büyük refactor değil,
  minimal ama gerçek"; ayrıca senkron tek-istek modelinde bu ara
  durumların GERÇEK bir zaman aralığı yoktur (T33'ün "working anı"
  sorunuyla AYNI sınıf). (b) Sabit bir zamanlayıcıyla (`setTimeout`)
  aşamaları simüle etmek — reddedildi, fabrike/sahte zamanlama, hiçbir
  gerçek backend olayına dayanmaz, test edilemez şekilde flaky olurdu.
- **Bilinen sınırlamalar:** `heartbeat_tracker` DI edilmeden (`app.py`'ye
  hiç verilmezse) working/idle presence olayları hiç üretilmez — bu
  durumda görev işaretçisi "atandı" aşamasında SIKIŞIR (gerçek üretim
  konfigürasyonunda `server.py` her zaman bir tracker sağlar, bu yüzden
  pratikte gözlenmez, ama teorik bir v0 sınırı olarak KAYDEDİLDİ).
  Bellek sınırı: en fazla `MAX_TASK_MARKERS` (8) işaretçi izlenir, aşımda
  en eski TAMAMLANMIŞ işaretçiler GC edilir (aktif olanlar asla silinmez).
- **Sonuç:** Kabul edildi. Bkz. `apps/ops-suite/frontend/js/scene.js`,
  `docs/PLAN.md` T45, `docs/BACKLOG.md` B048.

## ADR-024
- **Tarih:** 2026-08-14
- **Karar:** Ses ipucu çerçevesi (PLAN.md T46, BACKLOG.md B050) için
  **Web Audio API ile sentezlenmiş ton** (`OscillatorNode`) yaklaşımı
  seçildi — GERÇEK ses DOSYASI (mp3/wav, CDN veya yerel binary) EKLENMEDİ.
  3 ipucu: `approval_needed` (660Hz, üçgen dalga), `task_complete`
  (880Hz, sinüs), `policy_block` (220Hz, testere dişi dalga).
- **Tetikleyici eşleme (dürüstlük):** yalnızca 2 doğal/gerçek tetik
  backend'de zaten ayrı ayrı gözlemlenebilir: `approval.queue` konusunda
  YENİ bir kayıt (payload'da `decision` alanı YOK — `_decide_and_broadcast`'in
  "karar verildi" yayınından `submit()`'in "yeni kayıt" yayınını ayırt
  eder) → `approval_needed`; `task.lifecycle` `state=="completed"` →
  `task_complete`. Backend'de bunlardan AYRI, üçüncü bir "politika engeli"
  durumu YOK (`awaiting_approval`'dan farklı bir kod yolu yok) — bu
  yüzden 3. ipucu, B044'ün (owner-root-guard) GERÇEK 401/403 red
  yanıtlarına (`decide()` içinde, `/api/approvals/{id}/approve`\|`reject`)
  bağlandı; bu GERÇEKTEN var olan, test edilmiş bir "politika tarafından
  reddedildi" olayıdır — fabrike bir 3. backend durumu İCAT EDİLMEDİ.
- **Neden ses dosyası değil sentezlenmiş ton:** (1) offline-first ilkesi
  (ADR-018) ile doğrudan uyum — CDN YOK, ayrıca yeni bir binary asset
  pipeline'ı da GEREKMEZ (B047'nin SVG sprite hattından farklı olarak);
  (2) test edilebilirlik — bir `OscillatorNode`/`AudioContext` çağrısının
  GERÇEKTEN yapılıp yapılmadığı (ve hangi parametrelerle) Playwright'ta
  doğrudan gözlemlenebilir, bir ses dosyasının GERÇEKTEN çalınıp
  çalınmadığını (dosya decode/playback) doğrulamak çok daha kırılgan
  olurdu; (3) bu ortamda zaten hoparlör donanımı yok — insan kulağıyla
  doğrulama HER İKİ yaklaşımda da NOT_COLLECTED olacaktı, bu yüzden
  dosya-tabanlı yaklaşımın "gerçekçilik" avantajı bu bağlamda YOK.
- **Sessize alma kalıcılığı:** `localStorage` (`ops_suite_sound_muted`,
  B044'ün token deposuyla AYNI mekanizma) — sekme kapatılıp açılsa bile
  kullanıcı tercihi korunur. `localStorage` erişilemezse (özel/gizli
  tarayıcı modu vb.) GÜVENLİ varsayılan SESLİ (`false`/hata → sessizce
  yutulur, oturum içi mute state hâlâ geçerli kalır).
- **Politika kapısı:** v0'da `SoundCues` constructor'ında sabit
  `policyEnabled=true` (gerçek bir merkezi config kaynağı — ör.
  `/api/config` — HENÜZ YOK); `setPolicyEnabled()` metodu programatik
  olarak (ve testte) kapatılabilir. Gelecekte gerçek bir config uç
  noktası eklenirse bu değer ORADAN beslenecek — v0 bunu bilerek
  ERTELİYOR (kapsam dışına atmak yerine, en azından KAPIYI hazır tutar).
- **Bilinen sınırlamalar (NOT_COLLECTED):** bu ortamda hoparlör donanımı
  yok — sesin insan kulağıyla GERÇEKTEN duyulduğu doğrulanamaz; test
  edilen yalnızca doğru koşullarda doğru parametrelerle GERÇEK bir Web
  Audio API çağrısının yapılıp YAPILMADIĞIDIR (`SoundCues.debugState().last_play`).
- **Sonuç:** Kabul edildi. Bkz. `apps/ops-suite/frontend/js/sound_cues.js`,
  `docs/PLAN.md` T46, `docs/BACKLOG.md` B050.

---

## ADR-025
- **Tarih:** 2026-08-14
- **Karar:** Güvenlik Sertleştirme Sprint-1 (PLAN.md T50-T52, BACKLOG.md
  B051-B053, SECURITY) — B044'ün (owner-root-guard) derinleştirilmesi,
  3 minimal-ama-gerçek dilim: (1) token rotasyonu/iptali
  (`identity.py::TokenRevocationStore`, hash-only JSONL — ADR-009/016/022
  ile AYNI append-only desen); (2) auth-hassas uç noktalarda sabit-pencere
  hız sınırlama (`rate_limiter.py::RateLimiter`, `HeartbeatTracker` ile
  AYNI enjekte-edilebilir `clock` deseni, bellek-içi/kalıcılık YOK); (3)
  auth-karar audit alanlarının standardizasyonu (`auth_audit.py::build_auth_decision_details()`,
  ADDITIVE — B044'ün mevcut `actor_id`/`authority_source`/`decision_scope`
  alanlarını DEĞİŞTİRMEZ).
- **Kapsam kararı (kullanıcı talebi — "büyük refactor değil"):** her
  dilim mevcut `identity.py`/`app.py` akışına DAR entegrasyon
  noktalarıyla eklendi — hiçbir mevcut fonksiyon imzası/davranışı
  KIRILMADI (yalnızca yeni, opsiyonel/varsayılan-değerli parametreler
  eklendi: `IdentityStore(..., revocation_store=None)`,
  `create_app(..., rate_limiter=None)`).
- **B051 — iptal listesi neden hash-only:** `identity.py`'nin zaten var
  olan "sırlar diske yazılmaz" ilkesiyle (token DEĞERLERİ config'e ASLA
  yazılmaz) TUTARLI kalmak için — SHA-256 tek-yönlü özet, iptal
  denetlenebilirliğini (KİM, NE ZAMAN, HANGİ actor_id için iptal etti)
  ham sır sızıntısı riski OLMADAN sağlar. İptal kontrolü kimlik
  eşlemesinden ÖNCE yapılır — böylece bir restart'ta env değişkeni HALA
  eski değeri taşısa bile token geçersiz KALIR (yalnızca bellek-içi
  eşleme silme YETERSİZ olurdu, çünkü `IdentityStore.from_config_path()`
  her başlangıçta env'den YENİDEN yüklenir).
- **B052 — neden kalıcılık YOK:** rate limiting'in amacı kısa vadeli
  kötüye kullanımı engellemek — uzun vadeli bir denetim izi tutmak
  DEĞİL (o iş zaten `auth_audit.py`'nin işi, B053 ile ayrı ayrı
  loglanıyor). Bir restart'ın pencereyi doğal olarak sıfırlaması
  BEKLENEN ve ZARARSIZ bir davranıştır — JSONL tabanlı bir kalıcılık
  (ADR-009 deseni) burada GEREKSİZ karmaşıklık olurdu.
- **B052 — kategori anahtarlama:** `f"{actor_id}:{category}"` (`approval_decision`
  vs `identity_admin`) — tek bir global sayaç yerine seçildi, çünkü bir
  actor'un yoğun onay/red trafiği, KENDİ kimlik-yönetimi (rotate/revoke)
  eylemlerini YANLIŞLIKLA KİLİTLEMEMELİDİR (farklı risk profilleri).
- **B053 — additive tasarım (B044'ü bozmama):** `tests/test_ops_suite_api.py::test_approve_writes_audit_record_with_full_identity_fields`
  gibi mevcut testler `details.actor_id`/`authority_source`/`decision_scope`
  alanlarına DAYANIYORDU — bunları yeniden adlandırmak/kaldırmak
  gereksiz bir kırılma riski olurdu. Bunun yerine YENİ, standardize
  alanlar (`actor`/`scope`/`decision`/`reason_code`) `details.auth_decision`
  ALT-SÖZLÜĞÜ altında EKLENDİ — hem eski hem yeni tüketiciler için
  çalışır.
- **B053 — önceden var olan gerçek boşluk:** 401 (kimlik doğrulama
  başarısız)/403 (yetkilendirme başarısız)/429 (hız sınırı) HİÇBİRİ
  audit'e YAZILMIYORDU — yalnızca BAŞARILI kararlar loglanıyordu. Bu,
  örneğin bir brute-force token deneme dizisinin audit log'da HİÇBİR İZ
  BIRAKMAYACAĞI anlamına geliyordu. Şimdi TÜM auth-karar noktaları
  (`_get_current_identity`, `authorize_decision` başarısızlığı,
  `_check_rate_limit`, `_require_owner`) `_log_auth_decision()` çağırır.
- **Gerçek bulunan/düzeltilen hata (T50 sırasında):** ilk yazımda
  `REASON_CODE_AUTH_MISSING_TOKEN`/`REASON_CODE_AUTH_INVALID_TOKEN`
  sabitleri, repo'nun secret-scanner testini (`scripts/ops/secret_scan_core.py::generic_secret_assignment`
  — "TOKEN" kelimesi bir `=` işaretine bitişik değişken adları)
  GERÇEKTEN tetikledi, tam pytest koşusunda yakalandı (bu ADR yazılana
  KADAR fark edilmemiş olabilirdi). `AUTH_TOKEN_MISSING`/`AUTH_TOKEN_INVALID`
  olarak yeniden adlandırılarak düzeltildi — B044'teki
  `AUTH_METHOD_BEARER_TOKEN`→`AUTH_METHOD_BEARER` ile AYNI hata sınıfı
  (bu projede artık en az 2. kez karşılaşılan, bilinen bir desen).
- **Bilinen sınırlamalar:** B052'nin politika kapısı (rate limit eşiği)
  kod-içi sabit (`max_requests=20, window_seconds=60.0`) — merkezi bir
  config kaynağından beslenmiyor (B050'nin ses politika kapısıyla AYNI
  v0 sınırı). B051'in iptal listesi sınırsız büyür (rotasyon/budama
  YOK — ADR-022 ile AYNI gerekçe, v0 ölçeğinde pratik bir sorun değil).
  Token rotasyonu/iptali CLI'dan DEĞİL, yalnızca API'den yapılabilir.
- **Sonuç:** Kabul edildi. Bkz. `apps/ops-suite/backend/src/ops_suite/identity.py`,
  `rate_limiter.py`, `auth_audit.py`, `docs/PLAN.md` T50-T52,
  `docs/BACKLOG.md` B051-B053.

---

*Yeni ADR eklerken yukarıdaki formatı koru ve numarayı sırayla artır (ADR-026, ...).*
