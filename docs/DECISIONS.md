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

---

*Yeni ADR eklerken yukarıdaki formatı koru ve numarayı sırayla artır (ADR-020, ...).*
