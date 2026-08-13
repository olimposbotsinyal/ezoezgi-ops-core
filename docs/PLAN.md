# PLAN — İlk 14 Gün (Faz 0: Bootstrap)

> Kapsam: Monorepo iskeleti, kimlik/alias config, TR-EN köprü iskeleti, orchestrator
> iskeleti, CLI runner iskeleti, basit E2E (TR komut → EN task → tool call → TR yanıt),
> audit logger + risk policy + finance execution mock + onay akışı simülasyonu (T17–T20).
> Kural: Sprint ortasında kapsam değişikliği yok — yeni istekler BACKLOG.md'ye yazılır.
> T17–T20, MASTER_ROADMAP.md §5 finans kapsam revizyonu ile 2026-08-12'de bilinçli
> olarak sprint'e eklendi (istisna, ad-hoc değil).

## Gün 1–2 — Monorepo ve Klasör İskeleti

- [x] **T1. Klasör yapısını oluştur**
  - Amaç: Tüm ekiplerin/ajanların ortak dizin sözleşmesine sahip olması.
  - Teknik çıktı: `docs/ config/ apps/ services/ tools/ infra/ data/ policies/` ağacı.
  - Kabul kriteri: Belirlenen tüm alt klasörler mevcut, boş klasörlerde `.gitkeep`.
  - Bağımlılıklar: Yok.

- [x] **T2. Git deposu başlat**
  - Amaç: Sürüm takibini gün 1'den itibaren garanti altına almak.
  - Teknik çıktı: `git init`, `.gitignore`, ilk commit.
  - Kabul kriteri: `git log` en az 1 commit gösteriyor, `git status` temiz.
  - Bağımlılıklar: T1.

- [x] **T3. Kök README ve lisans/politika notu**
  - Amaç: Depoya yeni katılan biri veya ajanın 1 dakikada bağlam kazanması.
  - Teknik çıktı: `README.md` (proje özeti + docs/ indeksine link).
  - Kabul kriteri: README, MASTER_ROADMAP.md ve PLAN.md'ye link veriyor.
  - Bağımlılıklar: T1.
  - **Not (2026-08-13):** `README.md` güncellendi — proje özeti, tüm `docs/`
    indeks linkleri (+ yeni `docs/releases/PHASE0_CLOSURE.md`), ve kısa bir
    "Çalıştırma (Hızlı Başlangıç)" bölümü (`.venv` kurulumu + `pytest` +
    `e2e_demo.py` komutu, detay için RUNBOOK.md'ye link). "Durum" bölümü de
    Faz 0'ın gerçek durumunu (çekirdek zincir kapandı, finans ertelendi)
    yansıtacak şekilde güncellendi.

## Gün 3–4 — Kimlik / Alias Config Altyapısı

- [x] **T4. `assistant.identity.json` oluştur**
  - Amaç: Asistan adı ve wake-alias listesinin tek doğruluk kaynağı olması.
  - Teknik çıktı: `config/assistant.identity.json` (assistant_id, display_name,
    wake_aliases, language_mode, alias_update_policy).
  - Kabul kriteri: JSON şema doğrulanabilir; `wake_aliases` en az `["ezo","ezgi"]` içerir.
  - Bağımlılıklar: T1.

- [x] **T5. Config loader (basit)**
  - Amaç: Servislerin identity config'i restart gerektirmeden okuyabilmesi.
  - Teknik çıktı: `apps/orchestrator` içinde config okuma modülü (dosya değişimini
    poll veya watch eden basit fonksiyon).
  - Kabul kriteri: Config dosyası değiştirildiğinde bir sonraki komutta yeni alias
    listesi kullanılıyor (restart yok).
  - Bağımlılıklar: T4.
  - **Not (2026-08-13):** `apps/orchestrator/src/config_loader.py` — `ConfigLoader`
    sınıfı, mtime polling ile hot-reload yapıyor; şema validasyonu (5 zorunlu alan)
    başarısız olursa son geçerli config'i koruyup uyarı logluyor, crash olmuyor.
    Test: `tests/test_config_loader.py` (7/7 yeşil).

- [x] **T6. Wake-alias eşleme testi**
  - Amaç: "Ezo" / "Ezgi" ifadelerinin doğru tanınmasını garanti etmek.
  - Teknik çıktı: Basit unit test seti (ör. 10 örnek cümle, alias'lı/alias'sız).
  - Kabul kriteri: Tüm test örnekleri doğru sınıflandırılıyor (pass).
  - Bağımlılıklar: T5.
  - **Not (2026-08-13):** `services/tr-en-bridge/src/alias_matcher.py` —
    case-insensitive, bas/son noktalama temizligi, turkce "İ" (noktali buyuk I)
    normalizasyonu (str.lower()'in "İ"→"i̇" bilesik-nokta hatasindan kacinmak icin
    ozel translate tablosu kullanildi). Kelime bazli tam eslesme (substring degil —
    "ezocan"/"ezgiyi" eslesmiyor). Test: `tests/test_alias_matcher.py`, 19 parametrize
    + 4 ek vaka = 23/23 yeşil (istenen 15'in üzerinde).

## Gün 5–7 — TR-EN Köprü Servis İskeleti

- [x] **T7. `tr-en-bridge` servis iskeleti**
  - Amaç: TR girdi → EN task çıktısı üreten servisin minimum çalışan formu.
  - Teknik çıktı: `services/tr-en-bridge` altında giriş/çıkış arayüzü tanımlı (ör.
    `translate_and_extract(input_tr) -> {task_en, original_tr}`), henüz gerçek
    model bağlanmamış (stub/mock çeviri).
  - Kabul kriteri: Stub servis, örnek TR cümleyi alıp sabit/mock EN task döndürüyor.
  - Bağımlılıklar: T1.
  - **Not (2026-08-13):** `services/tr-en-bridge/src/bridge.py` —
    `translate_and_extract(input_tr, aliases) -> {detected_alias, task_en,
    original_tr, confidence}`. Anahtar kelime tabanlı mock sınıflandırma
    ("harcama" → `SHOW_DAILY_SPENDING`, "echo" → `RUN_ECHO`). Ayrıca
    `generate_tr_response(task_en, result)` ile EN→TR şablon üretimi eklendi
    (T9/T14'ün "TR yanıt" ihtiyacı için).

- [x] **T8. Local model bağlantı noktası (Ollama) tanımı**
  - Amaç: Gerçek çeviri/anlama için local runtime bağlantısının yerinin netleşmesi.
  - Teknik çıktı: `services/tr-en-bridge` içinde Ollama çağrısı için ayrılmış
    interface/config (henüz zorunlu canlı bağlantı değil).
  - Kabul kriteri: Ollama servis adresi config'ten okunuyor; servis yoksa graceful
    hata/log veriyor (crash yok).
  - Bağımlılıklar: T7.
  - **Not (2026-08-13):** `services/tr-en-bridge/src/model_client.py` —
    `OllamaModelClient` (stdlib `urllib`, ek bağımlılık yok). `OLLAMA_BASE_URL`
    (varsayılan `http://localhost:11434`) ve `OLLAMA_MODEL` (varsayılan `llama3`)
    env'den okunuyor. `health_check()` ve `generate()` hiçbir zaman exception
    fırlatmıyor — servis erişilemezse `fallback: True` ile mock yanıt dönüyor.
    Test: `tests/test_model_client.py`, erişilemeyen porta (127.0.0.1:1) karşı
    6/6 yeşil.

- [x] **T9. Bridge round-trip testi (mock)**
  - Amaç: TR→EN→TR döngüsünün uçtan uca çalıştığını mock veriyle kanıtlamak.
  - Teknik çıktı: Basit test: `"Ezo, bugünkü harcamaları göster"` → mock EN task →
    mock TR yanıt.
  - Kabul kriteri: Test yeşil; wake-alias doğru yakalanıyor (T6 ile birlikte).
  - Bağımlılıklar: T7, T6.
  - **Not (2026-08-13):** `tests/test_bridge_roundtrip.py` — echo ve harcama
    senaryoları + alias'sız girdi + bilinmeyen komut durumu, 4/4 yeşil.

## Gün 8–9 — Orchestrator Skeleton

- [x] **T10. `orchestrator` servis iskeleti**
  - Amaç: Task graph'ı alıp uygun ajana/tool'a yönlendirecek çekirdeğin taslağı.
  - Teknik çıktı: `apps/orchestrator` altında `handle_task(task_en) -> result_en`
    fonksiyonu; ilk sürümde tek bir sabit tool-call rotası (ör. CLI runner'a echo).
  - Kabul kriteri: Orchestrator, bridge'den gelen mock task'ı alıp tool-call
    tetikleyebiliyor.
  - Bağımlılıklar: T7.
  - **Not (2026-08-13):** `apps/orchestrator/src/orchestrator.py` —
    `Orchestrator.handle_task(extracted) -> result_en`, `status`
    (`ok`/`no_handler`/`error`) her zaman dönüyor; handler'daki exception
    orchestrator'ı çökertmiyor (yakalanıp `error` status'una çevriliyor).

- [x] **T11. Ajan kayıt (registry) taslağı**
  - Amaç: İleride yeni ajan eklemenin (finance, research, vb.) tek noktadan
    yönetilmesi.
  - Teknik çıktı: Basit registry yapısı (ajan adı → handler referansı), şimdilik
    yalnızca `cli-runner` kayıtlı.
  - Kabul kriteri: Registry'e yeni bir mock ajan eklenip orchestrator üzerinden
    çağrılabiliyor.
  - Bağımlılıklar: T10.
  - **Not (2026-08-13):** `apps/orchestrator/src/registry.py` — `Registry` sınıfı
    + `build_default_registry()` (şu an yalnızca `RUN_ECHO` →
    `tools/cli-runner/src/echo_runner.py::run_echo` kayıtlı). Test:
    `tests/test_orchestrator.py`, registry + orchestrator birlikte 7/7 yeşil
    (bridge çıktısından uçtan uca echo çalıştırma dahil).

## Gün 10–11 — CLI Runner Skeleton

- [x] **T12. `cli-runner` tool iskeleti**
  - Amaç: Orchestrator'ın gerçek bir sistem komutunu (sandboxed) çalıştırabilmesi.
  - Teknik çıktı: `tools/cli-runner` altında whitelist'li, güvenli komut çalıştırma
    fonksiyonu (ör. yalnızca izinli komut listesi çalıştırılabilir).
  - Kabul kriteri: Whitelist dışı komut reddediliyor; izinli komut (ör. `echo`)
    başarıyla çalışıp sonucu döndürüyor.
  - Bağımlılıklar: T1.
  - **Not (2026-08-13):** `tools/cli-runner/src/runner.py` — `run_command(command_name,
    args, context) -> dict`, whitelist `config/cli_whitelist.json`'dan okunuyor
    (`echo`, `pwd`). Üç sabit güvenlik kuralı: `shell=True` hiç kullanılmıyor
    (liste argümanlı `subprocess.run`), her çağrıda zorunlu timeout (whitelist
    girdisi → `default_timeout_seconds` → 5s), whitelist dışı komut process hiç
    başlatmadan deterministic `NOT_WHITELISTED` hata koduyla dönüyor (bkz. ADR-012).
    `echo_runner.py` (T11) artık doğrudan Python string birleştirmesi yerine bu
    runner'ı çağırıyor. Test: `tests/test_cli_runner.py`, 7/7 yeşil (izinli komut,
    whitelist dışı, timeout, eksik executable, bozuk whitelist dosyası, `shell=False`
    doğrulaması, sıfır olmayan exit code dahil).

- [x] **T13. Risk etiketleme + onay stub'ı**
  - Amaç: §8 (Güvenlik ve Onay Mekanizması) için ilk iskeleti erken kurmak.
  - Teknik çıktı: Her tool-call'a `risk_level` alanı eklenmesi; `high`/`irreversible`
    için "onay bekliyor" durumunu simüle eden stub.
  - Kabul kriteri: `low` risk komut direkt çalışıyor; `high` risk komut onay
    olmadan bloklanıyor (log'a düşüyor).
  - Bağımlılıklar: T12.
  - **Not (2026-08-13):** `policies/risk/tool_risk_policy.yaml` (RUN_ECHO=low,
    SHOW_DAILY_SPENDING=low, RUN_SHELL_SAFE=medium, RUN_DELETE_FILE=irreversible,
    `default_risk_level=medium` — tanımsız task asla "low" sayılmıyor) +
    `apps/orchestrator/src/risk_engine.py` (`RiskEngine.get_risk(task_name)`) +
    `apps/orchestrator/src/approval_stub.py` (`check_approval(risk_level)` →
    `AUTO_ALLOWED`/`WAITING_APPROVAL`). `orchestrator.py`'ye entegre edildi: risk
    kontrolü handler çağrısından **önce** yapılıyor, `high`/`irreversible` ise
    handler'a hiç dokunulmadan `WAITING_APPROVAL` dönüyor. Test:
    `tests/test_risk_approval_flow.py`, 12/12 yeşil (birim + orchestrator
    entegrasyonu + audit log doğrulaması dahil).

## Gün 12–14 — Finans Güvenlik ve Onay Altyapısı (T17–T20, Revizyon)

> Bu blok, MASTER_ROADMAP.md §5'in finans kapsam revizyonu (gerçek işlem + L0–L3
> risk seviyeleri) sonrası eklendi. Gün 12–14 penceresinde, aşağıdaki E2E bloğuyla
> paralel yürütülür; gerçek borsa bağlantısı bu fazda yok (yalnızca mock/simülasyon).
>
> **⏸ 2026-08-12 revizyonu:** Gelisen_Bot (mevcut/eski bir kripto trading botu) analiz
> edildi — bkz. `docs/imports/GELISEN_BOT_*`. **Finance execution (T19), Gelisen_Bot
> parity checklist tamamlanmadan (bkz. `docs/imports/GELISEN_BOT_PARITY_CHECKLIST.md`)
> açılmayacak** — bkz. ADR-011. T17 (audit logger), T18 (risk policy) ve T20 (onay
> CLI simülasyonu) genel/yeniden kullanılabilir altyapı oldukları için **duraklatılmadı**,
> aktif kalmaya devam ediyor.

- [x] **T17. Audit logger iskeleti**
  - Amaç: Finans işlemleri ve onay kararları dahil tüm riskli aksiyonlar için
    değiştirilemez (append-only) kayıt altyapısını kurmak (bkz. ADR-009).
  - Teknik çıktı: `data/audit/audit.log.jsonl` şeması (ör. `timestamp`, `actor`,
    `action`, `risk_level`, `request`, `decision`, `result`) + satır bazlı,
    append-only JSONL logger fonksiyonu.
  - Kabul kriteri: Logger çağrıldığında dosyaya yeni satır eklenir, mevcut satırlar
    değişmez/silinmez; şema alanları dokümante edilmiş.
  - Bağımlılıklar: T1.
  - **Not (2026-08-13):** `apps/orchestrator/src/audit_logger.py` — `AuditLogger.log()`
    alanları (bootstrap'taki isimlerden hafif farklı, genel amaçlı kullanım için
    netleştirildi): `timestamp`, `request_id`, `alias`, `task`, `risk_level`,
    `status`, `details`. Her çağrı `data/audit/audit.log.jsonl`'a tek satır JSONL
    append ediyor (dosya modu `"a"`, üzerine yazma yok). Test:
    `tests/test_audit_logger.py`, 4/4 yeşil (tek-satır doğrulama + append-only +
    otomatik parent-dir oluşturma).

- [ ] **T18. Risk policy dosyaları (`policies/risk/*.yaml`) — başlangıç kuralları**
  - Amaç: L0–L3 işlem seviyelerini ve onay eşiklerini kod dışı, düzenlenebilir
    politika olarak tanımlamak (bkz. MASTER_ROADMAP.md §5).
  - Teknik çıktı: `policies/risk/execution_levels.yaml` (L0–L3 tanımı + onay
    gereksinimi) ve `policies/risk/finance.yaml` (withdraw=false zorunluluğu,
    örnek L2/L3 eşik değerleri, cooling-off süresi placeholder).
  - Kabul kriteri: YAML dosyaları geçerli/parse edilebilir; her seviye için onay
    tipi (yok / tek / çift+bekleme) açıkça tanımlı.
  - Bağımlılıklar: T1.
  - **Not (2026-08-13):** Bununla karıştırılmasın — T13 kapsamında ayrı bir dosya,
    `policies/risk/tool_risk_policy.yaml`, oluşturuldu (genel tool-call risk
    seviyeleri: `low`/`medium`/`high`/`irreversible`). T18'in finans-özel L0–L3
    şeması (`execution_levels.yaml`, `finance.yaml`) hâlâ yapılmadı ve T19 ile
    birlikte Faz 2'ye kalıyor (bkz. ADR-011) — bu görev hâlâ açık.

- [ ] **T19. Finance execution mock (işlem aç/kapat simülasyonu)** — ⏸ **Paused (Phase-2+)**
  - **Duraklatma nedeni:** Gelisen_Bot analizi tamamlanmadan finans execution
    açılmayacak (bkz. `docs/imports/GELISEN_BOT_PARITY_CHECKLIST.md`, ADR-011).
    Bu görev Faz 2'de, parity checklist'teki "Yeniden Yazılacak" P0 maddeleri
    (`ExchangeConnector` soyutlaması, credential encryption, secret temizliği)
    tamamlandıktan sonra yeniden ele alınacak.
  - Amaç: Gerçek borsa bağlantısı olmadan L1 (simülasyon) seviyesinde işlem
    akışını uçtan uca kanıtlamak; gerçek emir gönderimi bu görevde yok.
  - Teknik çıktı: `services/finance-engine` içinde `open_position_mock()` /
    `close_position_mock()` — risk motoruna (T18) ve audit logger'a (T17) bağlı.
  - Kabul kriteri: Mock işlem `L1` olarak etiketlenir, risk motorundan geçer,
    sonucu audit log'a JSONL satırı olarak düşer.
  - Bağımlılıklar: T17, T18.

- [ ] **T20. "Onay akışı" CLI simülasyonu (approve/reject)**
  - Amaç: L2/L3 işlemler için insan onayı adımının en az CLI seviyesinde
    çalıştığını göstermek (kill switch/geri alma değil — yalnızca onay akışı).
  - Teknik çıktı: Basit CLI komutu — bekleyen işlemi listeler, approve/reject
    seçeneği sunar, kararı audit log'a yazar.
  - Kabul kriteri: `L2` mock işlem onay bekler durumda kalır; approve ile
    yürütülür, reject ile iptal edilir, her iki durumda audit log satırı oluşur;
    `L3` işlemde ayrıca bekleme süresi (cooling-off) alanı log'da görünür.
  - Bağımlılıklar: T19.

## Gün 12–14 — Basit E2E ve Faz Kapanışı

- [x] **T14. E2E entegrasyon: TR komut → EN task → tool call → TR yanıt**
  - Amaç: Tüm iskeletin uçtan uca, gerçek (mock olmayan akış, mock model) bir
    örnek üzerinden çalıştığını göstermek.
  - Teknik çıktı: Tek komutla çalıştırılabilir demo script/senaryo.
  - Kabul kriteri: `"Ezo, echo ile 'merhaba' yaz"` girdisi → orchestrator → cli-runner
    (echo) → bridge → `"Merhaba yazdırıldı."` TR yanıtı üretiyor; audit log'a kayıt
    düşüyor.
  - Bağımlılıklar: T6, T9, T11, T13.
  - **Not (2026-08-13) — resmen kapatıldı:** `scripts/e2e_demo.py::run_e2e_demo()`
    artık tam zinciri çalıştırıyor: bridge (alias+task) → `RiskEngine` (T13) →
    `high`/`irreversible` ise `WAITING_APPROVAL` (handler'a hiç gidilmez), aksi
    halde `registry` → `echo_runner` → **gerçek `runner.run_command("echo", …)`**
    (T12, whitelist tabanlı, `shell=False`) → audit log → TR yanıt. İki kabul
    senaryosu doğrulandı: (1) `"Ezo, echo ile 'merhaba' yaz"` →
    `"Merhaba yazdırıldı."`, `risk=low`, `status=ok`; (2) `"Ezo, tüm dosyaları sil"`
    → `risk=irreversible`, `status=WAITING_APPROVAL`, TR yanıtı "...onay bekliyor"
    içeriyor. Her iki senaryo da `data/audit/audit.log.jsonl`'a satır düşürüyor.
    Test: `tests/test_e2e_acceptance.py`, 3/3 yeşil (projenin gerçek
    config/policy/audit yollarıyla, izole mock değil).

- [ ] **T15. Faz 0 gözden geçirme + BACKLOG güncelleme**
  - Amaç: Kapsam dışı kalan / ertelenen konuların BACKLOG'a taşınması.
  - Teknik çıktı: BACKLOG.md güncellemesi, DECISIONS.md'de varsa yeni ADR'ler.
  - Kabul kriteri: Açık PLAN.md maddesi kalmıyor (ya tamam ya BACKLOG'a taşınmış).
  - Bağımlılıklar: T14.

- [ ] **T16. Faz 1 kapsamının taslağını çıkar**
  - Amaç: Faz 0'dan Faz 1'e (Çekirdek Döngü) sorunsuz geçiş.
  - Teknik çıktı: MASTER_ROADMAP.md §7 ile uyumlu, PLAN.md'ye Faz 1 bölümü eklenmesi
    (bu bootstrap kapsamında yalnızca taslak başlıkları).
  - Kabul kriteri: Faz 1 için en az 5 üst seviye görev BACKLOG.md'de mevcut.
  - Bağımlılıklar: T15.

---

## Daily Log

Entity-schema prep PR opened for B031 (prompt-level schema hints); no reproducible CLI regression on clean main, validated by full green test run.

### 2026-08-12
- **Yapılanlar:** Proje bootstrap edildi — klasör iskeleti, `assistant.identity.json`,
  MASTER_ROADMAP.md, PLAN.md, BACKLOG.md, DECISIONS.md oluşturuldu; git deposu başlatıldı.
  Ardından finans kapsamı revize edildi: gerçek borsa işlemleri (withdraw kapalı API key +
  L0–L3 risk/onay modeliyle) kapsama alındı; ADR-006–010 eklendi (Python 3.12, FastAPI,
  custom→CrewAI orchestrator, JSONL audit log, JSON+.env config); PLAN.md'ye T17–T20
  (audit logger, risk policy, finance mock, onay CLI simülasyonu) eklendi; BACKLOG.md'ye
  borsa/risk maddeleri eklendi; RUNBOOK.md'ye "Finans Güvenlik Operasyonları" eklendi.
- **Sorunlar:** Yok (henüz kod yazılmadı, yalnızca iskelet/doküman).
- **Sonraki adım:** T3 (README), T5 (config loader) ve T17 (audit logger) ile devam.

**Ek (aynı gün, ikinci revizyon):** Gelisen_Bot (eski bir kripto trading botu,
`C:\Users\Serkan\PycharmProjects\PycharmProjects\Gelisen_Bot`) salt-analiz amacıyla
incelendi; hiçbir kod aktif entegre edilmedi. `docs/imports/` altında 5 doküman
üretildi (ANALIZ_RAPORU, FILE_MAP, PARITY_CHECKLIST, MIGRATION_PLAN, SECURITY_NOTES);
89 hassas-olmayan kaynak dosyası `archive/gelisen_bot_snapshot/` altına kopyalandı,
3 dosyada (`config/constants.py`, `data/olimpos_data.py`, `Olimpos_api_MEXC.py`)
hardcoded credential-benzeri literal tespit edildiği için kopyalanmadı. T19 (Finance
execution mock) Gelisen_Bot parity analizi tamamlanana kadar **Paused (Phase-2+)**
olarak işaretlendi (bkz. ADR-011). **Sorun:** Kaynak projede tespit edilen hardcoded
secret'ların rotate edilmesi kullanıcının kendi aksiyonu — EzoEzgi tarafında yapılacak
bir şey yok, yalnızca not düşüldü (bkz. `GELISEN_BOT_SECURITY_NOTES.md`).

### 2026-08-13
- **Yapılanlar:** `feature/core-bootstrap-week1` branch'i açıldı. Proje için
  `.venv` + `pytest` kuruldu (`pyproject.toml`, root `tests/` dizini,
  `pythonpath`/`--basetemp` ayarları). T5 (config loader), T6 (alias matcher),
  T7 (bridge iskeleti), T8 (Ollama model client, graceful fail), T9 (bridge
  round-trip testi), T10-T11 (orchestrator + registry + varsayılan `RUN_ECHO`
  handler'ı `tools/cli-runner/src/echo_runner.py`), T17 (audit logger, JSONL
  append-only) uygulandı ve `[x]` işaretlendi. `scripts/e2e_demo.py` ile finans
  DIŞI bir E2E önizlemesi kuruldu ve çalıştığı doğrulandı (bkz. RUNBOOK.md).
  Toplam 51 test yazıldı, 51/51 yeşil (`./.venv/Scripts/python.exe -m pytest`).
  Finance execution (T19) pause notu korundu, dokunulmadı.
- **Sorunlar:** (1) pytest'in varsayılan temp dizininde (`%TEMP%\pytest-of-*`)
  `PermissionError` alındı — `--basetemp=.pytest_tmp` ile çözüldü (`.gitignore`'a
  eklendi). (2) Windows konsolunda TR karakterli çıktı (`yazdırıldı`) mojibake
  görünüyordu — veri bozulmuyor, yalnızca ekran render sorunu; `e2e_demo.py`
  içinde `sys.stdout.reconfigure(encoding="utf-8")` ile giderildi.
- **Sonraki adım:** T3 (README), T12 (cli-runner whitelist iskeleti — şu an
  yalnızca tekil `echo_runner.py` var, genel whitelist mekanizması yok), T13
  (risk etiketleme + onay stub'ı) ile devam; bunlar tamamlanınca T14 resmen
  kapatılabilir.

**Ek (aynı gün, T12+T13+T14 resmi kapanış):** `config/cli_whitelist.json` +
`tools/cli-runner/src/runner.py` (whitelist tabanlı, `shell=False`, zorunlu
timeout) ile T12 tamamlandı; `echo_runner.py` artık bu runner'ı çağırıyor.
`policies/risk/tool_risk_policy.yaml` + `apps/orchestrator/src/risk_engine.py`
+ `approval_stub.py` ile T13 tamamlandı, `orchestrator.py`'ye risk kontrolü
(handler'dan önce) entegre edildi. `scripts/e2e_demo.py` iki senaryoyu da
(normal echo + irreversible/onay bekleyen) uçtan uca çalıştırıyor; T14 resmen
`[x]`. ADR-012 (CLI/tool execution güvenlik prensibi) eklendi. Toplam test
sayısı 51 → 73 (73/73 yeşil). Faz 0'da hâlâ açık: T3 (README), T18/T19/T20
(finans-özel, bilinçli olarak Faz 2'ye ertelendi, ADR-011), T15/T16 (T3 açık
olduğu için henüz başlatılmadı).
- **Sorun:** Teknik notlarda yanlışlıkla "2026-08-14" tarihi yazılmış,
  gerçek tarih (2026-08-13) ile düzeltildi.

**Ek (aynı gün, release prep):** T3 kapatıldı — `README.md` proje özeti +
tüm docs indeks linkleri + kısa "Çalıştırma" bölümü ile güncellendi.
`docs/releases/PHASE0_CLOSURE.md` eklendi (Faz 0 kapanış özeti: tamamlanan
görevler, test özeti, güvenlik kazanımları, bilinçli ertelenenler, Faz 1
giriş kriterleri). Git tag hazırlığı yapıldı (`v0.1.0-phase0-closed` önerisi)
— henüz oluşturulmadı/push edilmedi, yalnızca komut önerisi verildi.

**Ek (aynı gün, post-merge hijyen):** PR #1 (Ollama NLU adaptörü, B031
temel entegrasyon) ve PR #2 (B031 quality-gate framework: golden set +
`tools/eval_nlu.py` + eşikler) `main`'e merge edildi (`4b8d2f8`). Lokal
hijyen yapıldı: `feature/core-bootstrap-week1` ve
`feature/phase1-b031-quality-gate` branch'leri (ikisi de merge doğrulanarak,
`-d` ile, force kullanılmadan) silindi; `git remote prune origin` ile stale
remote-tracking referansları temizlendi. `main` üzerinde tam regresyon
132/132 yeşil. Docs (`BACKLOG.md`/`RUNBOOK.md`/`README.md`/`DECISIONS.md`)
tutarlılık taraması yapıldı — çelişki bulunmadı, patch gerekmedi. B031 hâlâ
**Kısmen tamamlandı**: quality-gate altyapısı hazır ve test edilmiş, ancak
canlı Ollama ölçümü bu ortamda hâlâ yapılamadı (`NOT_EVALUATED`) — bu
iddia edilmiyor, dürüstçe korunuyor.

**Ek (aynı gün, kapanış notu):** Feature/phase1-b031-entity-schema-prompt
merged to main via fast-forward; branch cleanup completed; next gate is
live Ollama eval for B031 acceptance metrics.

**Ek (aynı gün, B031 eval koşusu):** Preflight 4/4 PASS; `tools/eval_nlu.py`
çalıştırıldı; Ollama erişilemedi (timeout); B031 hâlâ **NOT_EVALUATED**
(bkz. `docs/RUNBOOK.md` "Değerlendirme koşu kayıtları",
`reports/nlu_eval_20260812.md`).

**Ek (aynı gün, ilk canlı Ollama koşusu):** Ollama winget ile kuruldu
(`Ollama.Ollama`), `llama3:latest` çekildi (4.7GB). Preflight 4/4 PASS,
`tools/eval_nlu.py` canlı Ollama'ya karşı çalıştırıldı — **B031 ilk kez
gerçekten ölçüldü ve FAIL aldı**: 5 kriterden 4'ü karşılanmadı
(intent_accuracy %30, entity_match %0, fallback_rate %100, p95 6.06s;
yalnızca parse_error_rate PASS). Kök neden teşhis edildi: istemci timeout'u
(2.0s) gerçek CPU inference süresinden (~6s) kısa, her istek fallback'e
düşüyor. B031 durumu **Partial / FAILED_THRESHOLDS** olarak güncellendi
(önceki NOT_EVALUATED'den farklı — artık gerçek, olumsuz bir ölçüm var).
Sayı uydurulmadı. Detay: `docs/RUNBOOK.md` "Değerlendirme koşu kayıtları",
`reports/nlu_eval_20260813.md`.

**Ek (aynı gün, timeout hotfix + ikinci canlı koşu):**
`services/tr-en-bridge/src/model_client.py`'de `DEFAULT_TIMEOUT_SECONDS`
2.0s'den 30.0s'ye çıkarıldı, `OLLAMA_TIMEOUT_SECONDS` env override eklendi
(explicit arg > env > varsayılan önceliğiyle). 6 yeni test eklendi
(`tests/test_model_client.py`, 12/12 yeşil), tam regresyon 142/142 yeşil.
Preflight sonrası `tools/eval_nlu.py` yeniden çalıştırıldı: **timeout
düzeltmesi doğru çalıştı** (istekler artık erken kesilmiyor) ama **yeni,
farklı bir kök neden** ortaya çıktı — Ollama'nın `llama-server` alt süreci
her istekte çöküyor (`HTTP 500`, Windows access violation `0xc0000005`,
manuel `curl` ile doğrulandı). RAM yeterli (66.9GB/23GB boş), kaynak kısıtı
değil. Sonuç sayısal olarak öncekiyle aynı (%30/%0/%100) ama nedeni
tamamen farklı — B031 hâlâ **Partial / FAILED_THRESHOLDS**. Sayı
uydurulmadı, yeni kök neden dürüstçe kaydedildi. Sonraki adım kullanıcıya
soruldu (farklı/daha küçük model denemek mi, Ollama crash'ini ayrıca mı
teşhis etmek). Detay: `docs/RUNBOOK.md` "Değerlendirme koşu kayıtları"
(2026-08-13T00:13:05Z satırı), `reports/nlu_eval_20260813.md`.

**Ek (aynı gün, B036 stabilizasyon kapısı):** 2026-08-13T00:38:34Z'de 4
sıralı, minimal-riskli deney çalıştırıldı (baseline, `OLLAMA_NUM_GPU=0` +
servis restart, temiz `rm`+`pull`, farklı model `llama3`) — tüm ham
çıktılar `reports/runtime_diag_20260813T003834Z/`'de. **4/4 aynı
`0xc0000005` çöküşüyle sonuçlandı**, tek bir tekli çağrı bile başarılı
olmadı. Görev kuralı gereği 50 çağrılık hard gate hiç koşulmadı (koşum
koşulu — en az bir başarılı tekli test — hiç sağlanmadı) ve **B031 canlı
yeniden ölçümü bu nedenle çalıştırılmadı** (bloklandı). B031 durumu
değişmeden **Partial / FAILED_THRESHOLDS** kalıyor. Sayı uydurulmadı,
50/50 iddia edilmedi. `docs/BACKLOG.md` B036 rafine edilmiş sonraki
adımlarla güncellendi (Vulkan/GPU'yu gerçekten kapatan doğru env
değişkenini bulmak dahil — `OLLAMA_NUM_GPU=0` denemesi Vulkan cihazını
sunucu logundan tam silmedi).

**Ek (aynı gün, B036 derin triage — issue-ready kanıt paketi):**
`reports/runtime_incident_20260813T004855Z/` altında tam bir triage kanıt
paketi üretildi: (1) `host_fingerprint.md` — NVIDIA sürücü 442.94/CUDA 10.2
(çok eski), Vulkan Instance 1.2.131 (eski), Event Viewer'da 20 özdeş
`0xc0000005` kaydı (Hatalı modül: `unknown`); (2) `gpu_isolation_matrix.md`
— A-E testleri: `OLLAMA_VULKAN=false` ve `OLLAMA_LLM_LIBRARY=cpu`, ikisi de
sunucu loglarıyla **kanıtlanmış** şekilde Vulkan cihazını hiç başlatmıyor
ve bu modda 5/5 çağrı başarılı/0 çöküş; Vulkan etkinken 5/5 çöküş (Test E —
donanım seviyesi GPU devre dışı bırakma — kapsam/risk gerekçesiyle bilinçli
atlandı); (3) `version_ab_test.md` — mevcut `0.32.9` çöküyor, önceki
`0.30.0` (gerçekten kurulup test edildi, ardından `0.32.9`'a geri
yüklendi) varsayılan ayarlarla çökmüyor ama log'a göre bu sürüm de Vulkan
cihazını bu donanımda seçmiyor (otomatik CPU fallback) — "0.30.0'da
düzeltilmiş" değil, farklı GPU-keşif davranışı; (4)
`scripts/repro_ollama_crash.ps1` yazıldı ve hem çöküş hem başarı yolunda
doğrulandı (`repro_output.txt`); (5)
`OLLAMA_GITHUB_ISSUE_DRAFT.md` hazırlandı (gönderilmedi). **Sonuç: GPU/Vulkan
sürücü etkileşimi kök neden olarak log-kanıtlı şekilde izole edildi.**
`docs/RUNBOOK.md` ve `docs/BACKLOG.md` (B036) bu bulgularla güncellendi.
B031 durumu değişmedi: **BLOCKED_BY_RUNTIME** (varsayılan/Vulkan modunda
50/50 gate hâlâ koşulmadı; CPU-only workaround çökmüyor ama B031'in
gecikme eşiğini muhtemelen karşılamıyor).
