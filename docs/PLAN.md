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

## Ops Suite v0 — Faz 5 Öne Çekme (İstisna)

> Bu bölüm, MASTER_ROADMAP.md §7'deki Faz 5 (gün 96-110) hedefinin bir
> parçasını (admin panel) BİLEREK öne çeken, ADR-015'te belgelenmiş bir
> istisnadır — Faz sırası genel olarak DEĞİŞMEDİ. Görev kısıtları: (1)
> mevcut governance/promotion mekanizmaları KALDIRILMADI, (2) hiçbir
> test sonucu FABRİKE EDİLMEDİ (gerçek donanım/tarayıcı gerektiren
> kısımlar açıkça NOT_COLLECTED/SKIPPED işaretlendi), (3) sahibi
> (Serkan Eryılmaz) tek kök yetkilisi değişmezi korundu (bkz.
> `docs/IDENTITY_AND_DELEGATION_POLICY.md`).

- [x] **T21. Ops Suite domain modeli + olay sözleşmeleri (S1)**
  - Amaç: Ajan/görev/asistan durumunu temsil eden gerçek, doğrulanmış şemalar.
  - Teknik çıktı: `apps/ops-suite/backend/src/ops_suite/schemas.py`
    (`AgentPresence`/`TaskLifecycleEvent`/`AssistantPresenceEvent`/`ApprovalQueueEntry`),
    `events.py` (konu haritası + WS zarfı).
  - Kabul kriteri: Geçersiz durum değerleri reddedilir, JSON zarfı round-trip eder.
  - **Not (2026-08-14) — resmen kapatıldı:** `tests/test_ops_suite_schemas.py`
    (11 test) + `tests/test_ops_suite_events.py` (10 test), 21/21 yeşil.

- [x] **T22. Heartbeat tracker + ajan durum çözümleyici (S2 mantık)**
  - Amaç: Bir ajanın "canlı" mı "offline" mı olduğunu, sahte veri üretmeden hesaplamak.
  - Teknik çıktı: `heartbeat.py::HeartbeatTracker`, `status_resolver.py::AgentStatusResolver`,
    `audit_tail.py::AuditTailReader`.
  - Kabul kriteri: Kodu olmayan ajanlar (Finance/Social/Research/Doc/Device/Voice)
    HER ZAMAN `offline`+`not_implemented` — asla fabrike `idle`.
  - **Not (2026-08-14) — resmen kapatıldı:** `tests/test_ops_suite_heartbeat.py` (10),
    `tests/test_ops_suite_audit_tail.py` (8), `tests/test_ops_suite_status_resolver.py` (7) —
    25/25 yeşil.

- [x] **T23. Onay kuyruğu kalıcı deposu (S2 mantık)**
  - Amaç: `approval_stub.py`'nin durumsuz dönüşünü gerçek, sorgulanabilir bir kuyruğa çevirmek.
  - Teknik çıktı: `approval_queue.py::ApprovalQueueStore` (JSONL append-only,
    submit/list_pending/decide) — `orchestrator.py`/`approval_stub.py`/`risk_engine.py`
    DEĞİŞTİRİLMEDİ.
  - Kabul kriteri: Bilinmeyen/zaten-karara-bağlanmış `request_id` reddedilir.
  - **Not (2026-08-14) — resmen kapatıldı:** `tests/test_ops_suite_approval_queue.py`
    (12 test), 12/12 yeşil.

- [x] **T24. FastAPI + WebSocket sunucusu (S2 sunucu)**
  - Amaç: Gerçek zamanlı REST + WS uç noktaları (ADR-007'nin sanksiyonladığı FastAPI'nin
    ilk gerçek kullanımı).
  - Teknik çıktı: `app.py::create_app()` (agents/assistant/approvals/voice-command uç
    noktaları + statik frontend mount'u), `server.py` (`python -m ops_suite.server`),
    `ws_manager.py::ConnectionManager`.
  - Kabul kriteri: Gerçek WS el sıkışması + REST çağrıları `TestClient` ile doğrulanır.
  - **Not (2026-08-14) — resmen kapatıldı:** `tests/test_ops_suite_api.py` (14),
    `tests/test_ops_suite_ws.py` (7), 21/21 yeşil — gerçek in-process ASGI, mock yok.

- [x] **T25. Sesli komut/UI kablolaması (S4, mocked metin girdisi)**
  - Amaç: `bridge.py`+`orchestrator.py` zincirini Ops Suite'e (audit/onay kuyruğu/heartbeat
    dahil) bağlamak — gerçek mikrofon YOK, bu yüzden TR metin girdisi kullanılır.
  - Teknik çıktı: `voice_bridge.py::VoiceBridge.handle_voice_command()`.
  - Kabul kriteri: Echo (düşük risk) tamamlanır; "tüm dosyaları sil" (irreversible) onay
    kuyruğuna düşer, audit'e yazılır.
  - **Not (2026-08-14) — resmen kapatıldı:** `tests/test_ops_suite_voice_bridge.py`
    (9 test) + `tests/test_ops_suite_assistant_presence.py` (6 test), 15/15 yeşil.

- [x] **T26. Ops Suite frontend v0 statik kabuğu (S3)**
  - Amaç: Ajan kartları + canlı akış + onay paneli + asistan paneli gösteren minimal bir UI.
  - Teknik çıktı: `apps/ops-suite/frontend/` (saf HTML/CSS/vanilla-JS, npm/bundler YOK —
    bkz. ADR-018), PWA manifest + asgari service worker.
  - Kabul kriteri: `node --check` ile sözdizimi doğrulanır; FastAPI gerçekten sunar.
  - **Not (2026-08-14) — resmen kapatıldı:** `node --check` 3/3 dosya temiz;
    `tests/test_ops_suite_api.py::test_root_serves_frontend_index_html`/`test_static_css_is_served`
    ile gerçek statik sunum doğrulandı. **SKIPPED:** görsel/etkileşimli tarayıcı
    doğrulaması (bu ortamda tarayıcı-otomasyon aracı yok, bkz. BACKLOG.md B039).

- [x] **T27. Dokümantasyon + E2E demo + kanıt (S5)**
  - Amaç: Gerçek bir uçtan-uca kanıt + eksiksiz doküman güncellemesi.
  - Teknik çıktı: `scripts/ops_suite_demo.py`, 6 yeni doküman
    (`OPS_SUITE_PRODUCT_SPEC.md` vb.), MASTER_ROADMAP/BACKLOG/DECISIONS/RUNBOOK güncellemeleri.
  - Kabul kriteri: Demo script GERÇEK bir `python -m ops_suite.server` alt-sürecine karşı
    çalışır (TestClient DEĞİL), kanıtı `reports/ops_suite_demo_<UTC>/`'a yazar.
  - **Not (2026-08-14) — resmen kapatıldı:** `scripts/ops_suite_demo.py` GERÇEKTEN
    çalıştırıldı — 8/8 adım PASS (`reports/ops_suite_demo_20260813T225059Z/`, `git add -f`
    ile arşivlendi). **NOT_COLLECTED (dürüstçe işaretli):** gerçek tarayıcı render'ı,
    gerçek mikrofon/hoparlör/TTS, gerçek GSM/SIM çağrı akışı, gerçek kamera/gesture girdisi
    — bkz. `evidence.md` "NOT_COLLECTED" bölümü.

- [x] **T28. Approval/Auth Enforcement + Owner Root Guard (SECURITY P0, BACKLOG.md B044)**
  - Amaç: T23/T24'ten beri bilinen v0 açığını kapatmak — onay/red uç noktalarının
    `actor` alanı serbest metindi, kimlik doğrulaması YOKTU (bkz.
    `docs/IDENTITY_AND_DELEGATION_POLICY.md` eski §4).
  - Teknik çıktı: `ops_suite/identity.py` (`IdentityStore`, `authorize_decision`,
    bearer-token kimlik doğrulama + kapsam/scope tabanlı yetkilendirme + owner-root-guard),
    `config/ops_suite_identities.json` (yalnızca owner, token DEĞERİ YOK — yalnızca
    `token_env_var` referansı), `app.py`/`approval_queue.py`/`server.py` entegrasyonu,
    genişletilmiş audit alanları (`actor_id`/`auth_method`/`authority_source`/`decision_scope`).
  - Kabul kriteri: Token yok/geçersiz → 401; sahibi HER risk seviyesini onaylayabilir;
    delegate, config'inde `approve:irreversible` yazsa BİLE `irreversible` onaylayamaz
    (403, kod-seviyesinde root-guard); kapsam dışı delegate eylemi 403 + açık hata mesajı.
  - **Not (2026-08-14) — resmen kapatıldı:** `tests/test_ops_suite_identity.py` (25 test,
    saf birim, yeni dosya) + `tests/test_ops_suite_api.py`'nin B044 uzantıları (14→22,
    8 yeni test, gerçek `TestClient` üzerinden owner/delegate/unauthorized senaryoları) +
    `test_ops_suite_approval_queue.py` (12→14, `get_pending_entry()` testleri) +
    `test_ops_suite_ws.py` (yeni imzaya güncellendi, test sayısı sabit) —
    tam repo regresyonu **934/934 yeşil** (899 + 35 yeni). `scripts/ops_suite_demo.py`
    GERÇEKTEN çalıştırıldı (gerçek subprocess, gerçek HTTP 401/403 kanıtı) — 11/11 adım
    PASS (`reports/ops_suite_demo_20260813T232850Z/`, `git add -f` ile arşivlendi; demo
    owner/delegate kimlikleri GERÇEK KİŞİLER DEĞİL, yalnızca bu koşum için üretilen
    rastgele token'lı geçici kimlikler — token değerleri hiçbir yere yazılmadı).
    Bkz. `docs/DECISIONS.md` ADR-019, `docs/IDENTITY_AND_DELEGATION_POLICY.md` §4/§5
    (güncellendi — eski "kimlik doğrulama YOK" notu kaldırıldı, yerine gerçek mekanizma
    + dürüst bilinen sınırlamalar listesi geldi).
  - **Bilinen sınırlamalar (fabrike edilmedi, dürüstçe işaretli):** tek kimlik doğrulama
    yöntemi (yalnızca bearer-token — OAuth/OIDC/mTLS YOK), token rotasyon/iptal için
    UI/CLI YOK (yalnızca env değişkenini değiştirip sunucuyu yeniden başlatmak), rate
    limiting/brute-force koruması YOK. Şu an committed `config/ops_suite_identities.json`'da
    **hiçbir gerçek delegate YOK** (`delegates: []`) — mekanizma çalışır durumda ama
    henüz kimseye yetki devredilmedi.

- [x] **T29. B042 — Ops Suite frontend yığın kararı (B038 ön koşulu)**
  - Amaç: B038'i (animasyonlu 2D ofis sahnesi) başlatmadan ÖNCE, önceki
    checkpoint'in NO-GO gerekçesindeki frontend-yığın belirsizliğini
    kapatmak.
  - Teknik çıktı: `docs/DECISIONS.md` ADR-020 — Vanilla JS + Canvas2D
    seçildi (PixiJS değerlendirilip reddedildi), sunulan frontend'e
    YENİ bağımlılık EKLENMEDİ.
  - Kabul kriteri: Karar dokümante edildi (ADR) VE BACKLOG.md B042
    durumu güncellendi.
  - **Not (2026-08-14) — resmen kapatıldı:** ADR-020 yazıldı — 2 seçenek
    (Vanilla JS+Canvas2D vs PixiJS-vendored) offline-first/test
    edilebilirlik/performans/bakım kriterleriyle karşılaştırıldı,
    Vanilla JS + Canvas2D seçildi. BACKLOG.md B042 → **Kapalı**.

- [x] **T30. B039 — Ops Suite gerçek tarayıcı E2E hazırlığı (B038 ön koşulu)**
  - Amaç: Önceki checkpoint'in NO-GO gerekçesindeki "tarayıcı-otomasyon
    aracı YOK" varsayımını GERÇEKTEN test etmek (varsaymak değil).
  - Teknik çıktı: `apps/ops-suite/e2e/` (yeni — `package.json`,
    `playwright.config.js`, `global-setup.js`, `tests/smoke.spec.js`),
    `docs/DECISIONS.md` ADR-021 (npm'in yalnızca test-tooling kapsamı,
    ADR-018'i YENİDEN AÇMAZ).
  - Kabul kriteri: Gerçek bir Chromium indirilip GERÇEKTEN çalıştırılabiliyor
    mu (varsayım değil, kanıt) VE en az 1 gerçek tarayıcı E2E testi PASS
    ediyor mu.
  - **Not (2026-08-14) — resmen kapatıldı:** `npx playwright install
    chromium` GERÇEKTEN denendi — ~306MB gerçek bir Chrome for Testing
    ikili dosyası indirildi (`C:\Users\...\AppData\Local\ms-playwright\`),
    `chromium.launch()` + gerçek DOM render doğrulandı (önce izole bir
    scratchpad probe'unda, sonra gerçek `apps/ops-suite/e2e/`'de).
    `npx playwright test` → **2/2 PASS**: (1) kök sayfa yükleniyor, ≥9
    ajan kartı GERÇEK fetch+render ile DOM'da görünüyor, WS bağlantısı
    GERÇEKTEN açılıyor; (2) sesli komut formu üzerinden gönderilen
    irreversible komut, onay kuyruğu panelini GERÇEK bir tarayıcıda
    güncelliyor. Bu, önceki 3 checkpoint boyunca tekrarlanan
    "tarayıcı-otomasyon aracı YOK" NOT_COLLECTED varsayımını BU ORTAM
    İÇİN tersine çeviren, doğrudan gözlemlenmiş kanıttır.
  - **Bilinçli sınırlamalar (fabrike edilmedi):** bu kanıt yalnızca BU
    oturumun/makinenin yeteneğini kanıtlar — CI veya farklı bir ortamda
    aynı adımın (browser indirme dahil) yeniden doğrulanması gerekir
    (bkz. ADR-021 kapsam notu). B038 (animasyonlu sahne) henüz
    YAPILMADI — bu yüzden onun görsel regresyon kapsamı da yok; şu an
    yalnızca v0'ın MEVCUT statik/kart-tabanlı arayüzü test edildi.

## B038 — Ops Suite Animasyonlu 2D Ofis Sahnesi (Faz 5 öne çekme, T29/T30 ön koşulları sağlandıktan sonra)

> Yığın kararı ADR-020'de (Vanilla JS + Canvas2D) zaten kilitlendi.
> Mevcut kart-tabanlı `#agent-grid`/`#assistant-card` DOM paneli
> KALDIRILMIYOR — sahne, ONA EK bir görselleştirme katmanıdır (aynı
> `AgentPresence`/`AssistantPresenceEvent`/onay kuyruğu verisini
> tüketir, yeni bir backend uç noktası GEREKMEZ).

- [x] **T31. Canvas sahne mimarisi + statik ofis düzeni**
  - Amaç: `apps/ops-suite/frontend/js/scene.js` (yeni modül, `app.js`'e
    DOKUNMADAN) — sabit masa/dinlenme-bölgesi/"henüz yok" rafı
    koordinatlarını tanımlayan, boş bir `<canvas>`'ı render eden temel
    iskelet.
  - Teknik çıktı: `OpsSuiteScene` sınıfı (constructor + `render()`),
    `index.html`'e yeni `panel--scene` bölümü, `style.css`'e panel
    stili.
  - Kabul kriteri: Sayfa yüklendiğinde canvas GERÇEKTEN görünür
    (Playwright ile doğrulanacak, T36).
  - **Not (2026-08-14) — resmen kapatıldı:** `scene.js` (640×320
    canvas, üçüncü taraf kütüphane YOK) + `index.html`/`style.css`
    entegrasyonu. Gerçek bir tarayıcıda görünürlüğü
    `tests/scene.spec.js`/`smoke.spec.js` ile doğrulandı (T36).

- [x] **T32. Ajan varlıkları + durum renkleri/ikonları + masa/dinlenme bölgeleri**
  - Amaç: `AgentPresence` listesini (`GET /api/agents`) sahnede GERÇEK
    varlıklara (avatar) çevirmek — `KNOWN_LIVE_AGENTS` (3) masa
    konumlarında, `NOT_IMPLEMENTED_AGENTS` (6) AÇIKÇA soluk/hayalet
    stilinde ayrı bir rafta (bkz. dürüstlük ilkesi,
    `AGENT_PRESENCE_STATE_MODEL.md` §3 — ASLA gerçek personel gibi
    gösterilmez).
  - Teknik çıktı: `OpsSuiteScene.setAgents()`, `AGENT_STATES` →
    renk/ikon eşleme tablosu.
  - Kabul kriteri: Her ajan doğru bölgede/renkte render ediliyor;
    `not_implemented` ajanlar görsel olarak AYIRT EDİLEBİLİR (asla
    canlı personel yanılsaması vermiyor).
  - **Not (2026-08-14) — resmen kapatıldı:** `_zoneForAgent()` —
    `KNOWN_LIVE_AGENTS` dışındaki HER ajan (state'ten BAĞIMSIZ) her
    zaman `"ghost"` bölgesine düşer; render'da `globalAlpha=0.35` +
    küçültülmüş yarıçapla soluklaştırılır. Gerçek bir tarayıcıda
    ekran görüntüsüyle (bkz. `reports/ops_suite_scene_<UTC>/01_initial_state.png`)
    doğrulandı.
  - **Bulunup düzeltilen gerçek hata:** İlk kanıt kosusunda 3 bilinen-canlı
    ajanın (`orchestrator`/`bridge_agent`/`tool_runners`) hepsi
    `offline` olduğunda AYNI dinlenme-bölgesi noktasında ÜST ÜSTE
    bindiği (okunaksız) GERÇEK bir ekran görüntüsüyle keşfedildi —
    `_targetForZone()`, bölge-içi sıraya göre yatay yayılım
    (`REST_SPACING`) uygulayacak şekilde düzeltildi, yeniden çalıştırılıp
    GERÇEKTEN doğrulandı.

- [x] **T33. Hareket geçişleri (görev atandı → masa, boşta → dinlenme bölgesi)**
  - Amaç: Durum değişince avatarın konumu ANINDA ZIPLAMAK yerine
    kısa bir enterpolasyonla (ease) hedefe HAREKET etmesi.
  - Teknik çıktı: `requestAnimationFrame` tabanlı basit lineer
    enterpolasyon döngüsü (üçüncü taraf animasyon kütüphanesi YOK —
    ADR-020 ile tutarlı).
  - Kabul kriteri: `working`/`blocked`/`awaiting_approval` → masa
    konumu, `idle`/`offline` → dinlenme bölgesi konumu; ara kareler
    debug overlay'de GÖZLEMLENEBİLİR.
  - **Not (2026-08-14) — resmen kapatıldı:** `_step()` her karede
    `x`/`y`'yi `targetX`/`targetY`'ye `LERP_SPEED=0.18` ile yaklaştırır;
    `debugState()`'in `at_rest_position` alanı ara-kare/hedefte-mi
    ayrımını dışa açar.
  - **Bilinçli sınırlama (dürüstçe işaretli):** Backend, bir sesli
    komutu BAŞTAN SONA senkron işliyor (`voice_bridge.py::handle_voice_command`
    tek bir HTTP isteği içinde tamamlanıyor) — bu yüzden `working` ara
    durumu GERÇEK ama İSTEMCİ TARAFINDAN BAĞIMSIZ OLARAK
    GÖZLEMLENEMEYECEK kadar kısa ömürlü; T36'nın testleri bu yüzden
    `working`→masa geçişini DEĞİL, deterministik olarak gözlemlenebilen
    `offline`→`idle` (dinlenme bölgesi) ve onay-tepsisi geçişlerini
    doğrular (fabrike bir "working anı" YAKALANMADI).

- [x] **T34. Asistan (EzoEzgi) avatar paneli + "rapor modu" görsel durumu**
  - Amaç: `AssistantPresenceEvent`'i (idle/listening/thinking/speaking/blocked_policy)
    sahnede ayrı bir avatar ile göstermek — `speaking` durumu, kullanıcıya
    aktif "rapor veriyor" anlamına geldiği için görsel olarak AYRICA
    vurgulanır (büyütülmüş halka + konuşma balonu ikonu).
  - Teknik çıktı: `OpsSuiteScene.setAssistant()`, asistan durumu →
    renk/ikon eşleme.
  - Kabul kriteri: Sesli komut gönderildiğinde asistan avatarı GERÇEKTEN
    `idle`'dan `speaking`'e geçiyor (deterministik, T36'da doğrulanacak).
  - **Not (2026-08-14) — resmen kapatıldı:** `speaking` durumunda
    avatar yarıçapı büyür (20→26px) + ek bir dış halka çizilir ("rapor
    modu" vurgusu). Gerçek geçiş `tests/scene.spec.js` "geçiş 2"nde VE
    `reports/ops_suite_scene_<UTC>/02_after_echo_command.png`'de
    (mavi, büyümüş "Ez" avatarı) kanıtlandı.

- [x] **T35. Gerçek-zamanlı WS-tetiklemeli sahne güncellemeleri + debug overlay**
  - Amaç: Mevcut `app.js::handleLiveEvent()` WS tetikleyicilerini
    (task.lifecycle/assistant.presence/approval.queue) sahne
    güncellemelerine BAĞLAMAK (yeni bir WS konusu İCAT ETMEDEN —
    backend DEĞİŞTİRİLMEZ) + Playwright'ın piksel-içeriği DOĞRUDAN
    göremediği Canvas sınırlamasını (bkz. ADR-020 test edilebilirlik
    notu) aşmak için `window.__ops_suite_scene_debug__()` köprüsü.
  - Teknik çıktı: `app.js`'e minimal kablolama (mevcut
    `refreshAgents`/`refreshAssistant`/`refreshApprovals` çağrılarının
    sonuna `scene.set*()` eklenir), `scene.debugState()` → düz JSON.
  - Kabul kriteri: `window.__ops_suite_scene_debug__()` gerçek bir
    tarayıcıda çağrılabilir VE mevcut REST/WS verisiyle TUTARLI JSON
    döndürüyor.
  - **Not (2026-08-14) — resmen kapatıldı:** `app.js`'in `renderAgents`/`renderAssistant`/`renderApprovals`
    fonksiyonlarına 3 satırlık `scene.set*()` çağrısı eklendi — `app.js`'in
    KENDİ mantığı DEĞİŞTİRİLMEDİ. `window.__ops_suite_scene_debug__`
    tüm `scene.spec.js` testlerinde GERÇEKTEN çağrıldı (5/5 PASS).
  - **Bulunup düzeltilen gerçek hata (kapsamı aşan ama T35/T36'nın gerçek
    kosusunda bulundu):** `apps/ops-suite/e2e/`'nin gerçek sunucu
    alt-süreçleri, veri izolasyonu OLMADAN projenin GERÇEK
    `data/approvals/approval_queue.jsonl` dosyasını kullanıyordu —
    onaylanmamış test komutları kalıcı SUBMITTED kayıtları biriktirip
    SONRAKİ test koşularını BOZUYORDU (ilk kosuda gerçekten yaşandı,
    2 canli komut testte "beklenen 1, bulunan 2" hatasına yol açtı).
    **Düzeltildi:** `ops_suite/server.py`'ye `OPS_SUITE_DATA_DIR` env
    override eklendi (approval queue + audit log + identity config
    hepsi izole edilebiliyor); `global-setup.js`/`capture_scene_evidence.js`
    her kosuda `os.tmpdir()` altında YENİ bir gecici dizin kullanıyor.
    Projenin GERÇEK `data/approvals/approval_queue.jsonl` dosyasına
    kazayla eklenen 2 test kaydı da temizlendi.

- [x] **T36. Playwright sahne-geçişi doğrulamaları + kanıt yakalama**
  - Amaç: En az 3 gerçek, DETERMİNİSTİK görsel/durum geçişini gerçek
    bir tarayıcıda kanıtlamak (ekran görüntüsü + JSON debug state ile).
  - Teknik çıktı: `apps/ops-suite/e2e/tests/scene.spec.js` (yeni),
    `apps/ops-suite/e2e/capture_scene_evidence.js` (yeni, bağımsız Node
    scripti — `reports/ops_suite_scene_<UTC>/evidence.{md,json}` +
    ekran görüntüsü `.png` dosyaları yazar).
  - Kabul kriteri: Testler GERÇEKTEN koşuluyor VE geçiyor; kanıt
    dizini gerçek PNG içeriyor (fabrike edilmiş/placeholder görüntü
    DEĞİL).
  - **Not (2026-08-14) — resmen kapatıldı:** `tests/scene.spec.js` (3
    senaryo) + `tests/smoke.spec.js` (2, mevcut) — **5/5 PASS**, gerçek
    Chromium'da. Doğrulanan 3 deterministik geçiş: (1) başlangıç —
    bilinen-canlı ajanlar `offline`, `not_implemented` ajanlar hayalet
    bölgede ayırt edilebilir; (2) echo komutu sonrası orchestrator
    `offline→idle` VE asistan `idle→speaking`; (3) irreversible komut
    + owner onayı (B044 bearer-token akışıyla, GERÇEK) — onay tepsisi
    rozeti `0→1→0`. `capture_scene_evidence.js` GERÇEKTEN çalıştırıldı:
    4 gerçek PNG ekran görüntüsü (900×700, `PNG image data` ile
    doğrulandı, placeholder DEĞİL) + `evidence.{json,md}` →
    `reports/ops_suite_scene_2026-08-14T0014Z/` (`git add -f` ile
    arşivlendi).
  - **Bulunup düzeltilen gerçek hata (ekran görüntüsünden GERÇEKTEN
    keşfedildi):** İlk kanıt koşusunda 3 bilinen-canlı ajan `offline`
    olduğunda dinlenme bölgesinde ÜST ÜSTE bindiği ("TO" okunaksız
    metni) fark edildi — kod incelemesiyle DEĞİL, gerçek ekran
    görüntüsünü İNCELEYEREK. T32'de düzeltildi, yeniden koşup GERÇEKTEN
    doğrulandı (bkz. `01_initial_state.png`'de "OR"/"BR"/"TO" artık
    ayrı ayrı okunabiliyor).
  - **Bilinçli sınırlama (fabrike edilmedi):** Bu kanıt, B038'in TAM
    görsel/etkileşimli kapsamını (ör. hareket animasyonunun ARA
    KARELERİNİN piksel-seviyesinde doğruluğu, gerçek insan gözüyle
    estetik değerlendirme) KAPSAMAZ — yalnızca durum/geçiş DOĞRULUĞUNU
    (debug JSON + ekran görüntüsü) kanıtlar.

## B038 Sonrası Checkpoint — Gözlemlenebilirlik + Kalıcılık (T37-T39)

> T33'ün bilinçli sınırlaması (`working` durumu polling ile
> yakalanamıyor) ve B041'in eski sınırlaması (heartbeat yalnızca
> bellek-içi) buradan devam ediyor.

- [x] **T37. Ajan/asistan durum geçişlerini async eventing ile gözlemlenebilir kılmak (BACKLOG.md B045)**
  - Amaç: `working` gibi kısa ömürlü ara durumların `GET /api/agents`
    polling'i ile YAKALANAMAMASI (bkz. eski T33 notu) sorununu, senkron
    çağrı modelini BOZMADAN (backend hâlâ tek bir HTTP isteği içinde
    uçtan uca işliyor) çözmek — bloklamayan bir olay yayma yolu ile.
  - Teknik çıktı: `heartbeat.py::HeartbeatTracker.on_change` (yeni,
    opsiyonel kanca — varsayılan `None`, GERİYE UYUMLU), `voice_bridge.py`
    bunu her `handle_voice_command()` çağrısında `self._presence_events`'e
    bağlar ve donüş değerine `presence_events` alanı olarak ekler,
    `app.py::POST /api/voice/command` bunları `task.lifecycle` ile AYNI
    örüntüyle (`for ... await connection_manager.broadcast(...)`) artık
    GERÇEKTEN VAR OLAN `TOPIC_AGENT_PRESENCE` (`events.py`'de önceden
    TANIMLI ama HİÇ KULLANILMAMIŞTI) konusuna yayınlar.
  - Kabul kriteri: Geriye uyumluluk (mevcut TÜM testler değişmeden
    yeşil kalmalı, yalnızca WS mesaj SAYISI değişebilir — bu, testlerde
    açıkça güncellenmeli) + en az 1 yeni test, `working`→`idle`
    geçişinin artık gerçek, sıralı WS mesajları olarak
    gözlemlenebildiğini deterministik olarak kanıtlamalı.
  - **Not (2026-08-14) — resmen kapatıldı:** Uygulandı ve GERÇEKTEN
    test edildi. `tests/test_ops_suite_heartbeat.py`'ye 4 yeni test
    (`on_change` varsayılan `None`/senkron ateşleme/sıralı
    working→idle/DI-sonrası yeniden bağlama), `tests/test_ops_suite_voice_bridge.py`'ye
    3 yeni test (`presence_events` toplama/tracker-yoksa-boş/çağrılar-arası
    sıfırlama), `tests/test_ops_suite_ws.py`'ye 1 yeni test
    (`test_ws_receives_agent_presence_events_for_working_then_idle` —
    gerçek `TestClient.websocket_connect()` ile `["working", "idle"]`
    sırasını DOĞRUDAN doğruluyor). **Geriye uyumluluk kontrolü:** 2
    mevcut WS testi (`assistant_presence_event`/`approval_queue_event`)
    artık 2 fazla `agent.presence` mesajı aldığı için sabit-sayılı
    `range(N)` okumaları güncellendi (5→7, 6→8) — DAVRANIŞ
    DEĞİŞMEDİ, yalnızca test beklentisi GERÇEK yeni mesaj sayısını
    yansıtacak şekilde düzeltildi. Tam regresyon: **942/942 yeşil**
    (934+8). `docs/RUNBOOK.md`'ye yeni bir "Ajan/asistan durum
    geçişlerini gözlemleme" bölümü + 1 troubleshooting satırı eklendi.
  - **Kapsam dışı (BİLEREK, T38'e bırakıldı):** Frontend (`scene.js`)
    henüz bu yeni `agent.presence` WS mesajlarını TÜKETMİYOR (yalnızca
    canlı akış paneline düşüyorlar) — sahnenin bunları kullanarak
    GERÇEK `working` anını görsel olarak render etmesi ayrı bir iştir.

- [x] **T38. `working`→`idle` geçişi için deterministik test kapsamı (BACKLOG.md B046)**
  - Amaç: T37'nin sağladığı gerçek WS gözlemlenebilirliğini kullanarak,
    hem `scene.js`'in HEM DE bir Playwright testinin `working` anını
    GERÇEKTEN (fabrike etmeden) yakalayabildiğini kanıtlamak.
  - Teknik çıktı: `scene.js::applyAgentPresenceEvent()` (yeni) — TEK bir
    `agent.presence` WS mesajını ANINDA uygular (bir sonraki REST
    polling'ini beklemeden); `_recomputeZones()`'a refactor edildi ki
    hem toplu (`setAgents`) hem tekli (`applyAgentPresenceEvent`)
    güncelleme AYNI bölge-yayılma mantığını kullansın.
    `app.js::handleLiveEvent()`'e `agent.presence` dalı eklendi.
    `apps/ops-suite/e2e/tests/scene.spec.js`'e "geçiş 4" — Playwright'ın
    NATIVE WebSocket frame API'si (`page.on('websocket', ...)` +
    `ws.on('framereceived', ...)`) ile GERÇEK tarayıcı WS trafiğini
    olay-tabanlı yakalar, hiçbir `waitForTimeout`/sleep KULLANMAZ.
  - Kabul kriteri: Testler GERÇEKTEN koşuluyor VE geçiyor; `working`
    durumu artık sleep/tahmin OLMADAN deterministik olarak kanıtlanmış.
  - **Not (2026-08-14) — resmen kapatıldı:** `npx playwright test` →
    **6/6 PASS** (3 kez ardışık çalıştırılıp tekrar doğrulandı,
    bkz. aşağıdaki "bulunan hata" notu). `states` dizisi
    `["working", "idle"]` olarak GERÇEK WS frame'lerinden okunuyor.
  - **Bulunup düzeltilen GERÇEK hata (bu görevin kendisinde):** İlk
    yazımda test, senkronizasyon sinyali olarak `#assistant-state ===
    'speaking'` DOM beklentisini kullanıyordu — ama
    `AssistantPresenceTracker`/`HeartbeatTracker` TÜM test dosyası
    boyunca PAYLAŞILAN sunucu-taraflı singleton'lar olduğu için, bir
    ÖNCEKİ testten kalan "speaking" durumu sayfa yüklenir yüklenmez
    ZATEN doğruydu — assertion bu testin KENDİ komutu hiç
    gönderilmeden ERKEN geçiyordu, frame'ler henüz gelmemişken kontrol
    çalışıyordu (gerçek bir kosuda "gecis 2"/"gecis 3" testlerinden
    SONRA çalıştırıldığında GERÇEKTEN gözlemlendi, izole çalıştırıldığında
    GİZLİ kalıyordu). **Düzeltme:** senkronizasyon artık doğrudan
    yakalanan WS frame DİZİSİNİN KENDİSİ — `expect.poll()` (sleep DEĞİL,
    sınırlı/bounded polling yardımcısı) ile "2 frame geldi mi" sorusuna
    bağlı, harici hiçbir DOM değerine GÜVENMİYOR.

- [x] **T39. Heartbeat/presence kalıcılığı — yeniden başlatmalar arası (BACKLOG.md B041)**
  - Amaç: `HeartbeatTracker`'ın BİLEREK bellek-içi olma sınırlamasını
    (bkz. `docs/OPS_SUITE_PRODUCT_SPEC.md` §6, BACKLOG.md B041 — ÖNCEDEN
    AÇILMIŞ madde, bu YENİ bir BACKLOG kaydı DEĞİL) kapatmak — sunucu
    yeniden başlatıldığında TÜM ajan durumu şu an sıfırlanıyor (yalnızca
    onay kuyruğu JSONL sayesinde kalıcı).
  - Teknik çıktı: `presence_store.py` (yeni) — JSONL append-only (bkz.
    `docs/DECISIONS.md` ADR-022), `HeartbeatTracker.has_record()`
    (yeni), `app.py::_seed_heartbeat_from_presence_store()` (yeni,
    sunucu başlarken çağrılır), `POST /api/voice/command`'ın
    presence_events döngüsü artık HER olayı da `presence_store.append()`
    ile kalıcı hale getiriyor. `server.py`'nin `OPS_SUITE_DATA_DIR`
    izolasyonu (T35'te eklenmişti) `presence/` alt-dizinini de kapsayacak
    şekilde genişletildi.
  - Kabul kriteri: Gerçek bir "restart" simülasyonu (yeni `HeartbeatTracker`
    + aynı `presence_store` yolu) son bilinen durumu doğru yansıtmalı;
    DI ile önceden doldurulmuş bir tracker'ın durumu SESSİZCE üzerine
    yazılmamalı; sınırlamalar + çakışma çözümü kuralı belgelenmeli.
  - **Not (2026-08-14) — resmen kapatıldı:** `tests/test_ops_suite_presence_store.py`
    (7 test) + `tests/test_ops_suite_heartbeat.py::test_has_record_*`
    (2 test) + `tests/test_ops_suite_api.py::test_agent_presence_survives_simulated_restart`
    (GERÇEK restart simülasyonu — ikinci, TAMAMEN YENİ bir
    `HeartbeatTracker` + `create_app()` örneği) +
    `test_agent_presence_seed_does_not_override_existing_di_tracker_state`
    (çakışma kuralı doğrulaması) — **11/11 yeni test yeşil**.
  - **Bulunup düzeltilen GERÇEK hata (yine test-veri-izolasyonu, T35/T37
    ile AYNI sınıf hata):** İlk yazımda `data/presence/agent_presence.jsonl`
    (varsayılan yol) mevcut `test_ops_suite_api.py`/`test_ops_suite_ws.py`
    testleri tarafından İZOLE EDİLMEDEN kullanılıyordu — tam pytest
    koşusu GERÇEK proje dosyasına 34 satır yazdı (gerçek bir kosuda
    GERÇEKTEN gözlemlendi, `ls data/presence/` ile doğrulandı).
    **Düzeltildi:** her iki test dosyasındaki TÜM `create_app()`
    çağrılarına `presence_store=PresenceStore(tmp_path / "presence.jsonl")`
    eklendi (4+1 çağrı yeri), kirlenen dosya silindi, `.gitignore`'a
    `data/presence/*`/`!data/presence/.gitkeep` eklendi.
  - **Bilinen sınırlamalar + çakışma çözümü kuralı:** bkz.
    `docs/DECISIONS.md` ADR-022 (dosya sınırsız büyür — rotasyon YOK;
    yalnızca `HeartbeatTracker` kalıcı hale getirildi, `AssistantPresenceTracker`
    DEĞİL; tohumlama YALNIZCA tracker'da hiç kaydı olmayan `agent_id`'ler
    için uygulanır, DI'lı bir tracker'ın durumunu ASLA ezmez;
    `last_heartbeat_ts` ORİJİNAL haliyle korunur — "şimdi" ile yeniden
    damgalanmaz, böylece `resolve_state()`'in zaman-aşımı mantığı
    restart öncesi/sonrası fark etmeksizin dürüstçe çalışır).

## B038 Tamamlama Parçası — Sprite Varlıkları + Etkileşim (T40-T44)

> `docs/OPS_SUITE_PRODUCT_SPEC.md` §7'nin "kapsam dışı" listesindeki 4
> madde BACKLOG.md'ye B047-B050 olarak ayrıştırıldı. Bu bölüm, hangi
> ikisinin (B047, B049) BU checkpoint'te uygulandığını, hangi ikisinin
> (B048, B050) yalnızca KAYDEDİLDİĞİNİ (henüz uygulanmadı) izler.

- [x] **T40. Yerel sprite/karakter varlık hattı (BACKLOG.md B047)**
  - Amaç: Sahnenin geometrik-şekil render'ını (daire + baş harfler),
    yerel (CDN'siz) sprite dosyalarıyla değiştirmek — GERİ DÜŞME
    (fallback) garantisiyle.
  - Teknik çıktı: `apps/ops-suite/frontend/assets/sprites/*.svg` (yeni,
    5 dosya — 3 bilinen-canlı ajan + asistan + ortak "hayalet" ikonu),
    `scene.js`'e sprite ön-yükleme + `drawImage()` render yolu.
  - Kabul kriteri: Varlık başarıyla yüklenirse sprite render edilir;
    yüklenemezse (bozuk/eksik yol) SESSİZCE BOŞ BIRAKMADAN mevcut
    daire+baş-harf render'ına GERİ DÜŞER; gerçek bir tarayıcıda
    doğrulanmalı (Playwright + ekran görüntüsü).
  - **Not (2026-08-14) — resmen kapatıldı:** `apps/ops-suite/frontend/assets/sprites/`
    altında 5 yerel SVG (`orchestrator.svg`, `bridge_agent.svg`,
    `tool_runners.svg`, `assistant.svg`, `ghost.svg`); `scene.js`'e
    `_loadSprites()`/`_spriteFor()`/sprite+fallback render yolu (`options.spriteBasePath`
    enjekte edilebilir — testte kırık yol simüle edildi). **Gerçek bulunan
    hata:** tüm 5 SVG'nin yorum satırlarında geçersiz XML (`<!-- .. -- .. -->`,
    `--` içeren yorumlar) vardı — ağ isteği 200 + doğru `image/svg+xml`
    dönüyordu ama Chromium `Image.onload` yerine `onerror` tetikliyordu;
    kök neden bir Playwright tanı betiğiyle (network yanıtı → minimal
    inline data-URI SVG karşılaştırması) izole edildi, tüm yorumlar `--`
    içermeyecek şekilde düzeltildi, gerçek tarayıcıda 5/5 sprite
    `status: "loaded"` doğrulandı. Kanıt: `apps/ops-suite/e2e/tests/interactions.spec.js`
    (`B047 -- sprite varlik hatti` — 2 test: tüm sprite'lar yükleniyor +
    bozuk yol çökmeden geri düşüyor) ve
    `reports/ops_suite_interactions_2026-08-14T0126Z/01_sprites_loaded.png`
    ile `evidence.json`/`evidence.md` (`capture_interactions_evidence.js`
    ile üretildi, `genel_sonuc=PASS`).

- [ ] **T41. Çoklu-adımlı görev animasyonları (BACKLOG.md B048)**
  - Amaç: Durum-bazlı (anlık) konum geçişleri yerine, bir görevin
    ARA adımlarını (ör. "masadan onay-tepsisine yürüyor") görsel
    olarak canlandırmak.
  - Kapsam: TASARLANMADI — yalnızca BACKLOG.md B048 ile kaydedildi, bu
    checkpoint'te UYGULANMADI.
  - Bağımlılık: T40 (sprite'lar varsa animasyon kareleri daha anlamlı
    olur, ama teknik olarak ZORUNLU değil).

- [x] **T42. Sahne tıklama etkileşimleri — ajan detay paneli (BACKLOG.md B049)**
  - Amaç: Sahneyi salt-görsel olmaktan çıkarıp, bir ajana tıklandığında
    GERÇEK durumunu (state/last_task_id/last_heartbeat_ts/detail)
    gösteren bir panel açmak; ilgili bir bekleyen onay varsa ona
    bağlantı vermek.
  - Teknik çıktı: `scene.js`'e canvas tıklama hit-test'i +
    `onAgentClick` kancası; `app.js`'e `openAgentDetailPanel()`
    (son bilinen ajan/onay verisiyle çapraz referans);
    `index.html`/`style.css`'e panel DOM'u.
  - Kabul kriteri: Herhangi bir ajana (hayalet raftakiler DAHİL)
    tıklamak paneli GERÇEK verilerle açar; ajanların KENDİ bir "yetki
    kapsamı" OLMADIĞI açıkça belirtilir (fabrike edilmez); bekleyen
    bir onayla eşleşen `last_task_id` varsa panelde bağlantı/vurgu
    olur.
  - **Not (2026-08-14) — resmen kapatıldı:** `scene.js`'e `_hitTestAt()`
    (daire-mesafe hit-test, asistan + tüm ajanlar) + `_bindClickHandler()`
    (`getBoundingClientRect()` ile CSS-ölçekli canvas koordinat dönüşümü);
    `app.js`'e `openAgentDetailPanel()` (son bilinen ajan/onay verisiyle
    çapraz referans, `agent.last_task_id === approval.request_id`
    eşleşmesiyle onay bağlantısı — `agent.state === "awaiting_approval"`
    DEĞİL, çünkü bu şema durumu kod tabanında hiçbir yerde gerçekten
    üretilmiyor, bkz. `docs/AGENT_PRESENCE_STATE_MODEL.md`); `index.html`/
    `style.css`'e `#agent-detail-panel` DOM'u + `.approval-item--highlighted`.
    Dürüstlük kuralı: panelde "yetki kapsamı" alanı YOK — bunun yerine
    "Ajanların kendi bir yetki kapsamı (authority scope) YOKTUR..."
    açıklaması gösteriliyor (uydurulmuş veri YOK). Kanıt:
    `apps/ops-suite/e2e/tests/interactions.spec.js` (`B049 -- sahne
    tiklama etkilesimleri` — 4 test: canlı ajan tıklama, hayalet/
    not_implemented ajan tıklama, asistan tıklama, bekleyen-onay
    bağlantısı+vurgu) ve
    `reports/ops_suite_interactions_2026-08-14T0126Z/02_agent_detail_panel.png`
    … `05_approval_item_highlighted.png` (5 PNG'nin 4'ü) + `evidence.json`/
    `evidence.md` (`genel_sonuc=PASS`).

- [x] **T44. Playwright dosya-başına sunucu izolasyonu (test altyapısı, BACKLOG ID YOK)**
  - Amaç: T40/T42 kanıt yakalaması sırasında GERÇEKTEN bulunan bir hatayı
    düzeltmek — bu, yeni bir ürün özelliği DEĞİL, test altyapısı
    düzeltmesidir (bu yüzden ayrı bir BACKLOG maddesi açılmadı).
  - **Gerçek bulunan hata:** `interactions.spec.js` eklenip tüm paket
    (`npx playwright test`, 3 dosya) birlikte çalıştırıldığında, eski
    mimari (`global-setup.js`'in TÜM dosyalar için TEK bir paylaşılan
    sunucu başlatması) nedeniyle `interactions.spec.js`'in gerçek sesli
    komutları (`HeartbeatTracker`/`AssistantPresenceTracker`/
    `ApprovalQueueStore` durumunu değiştiren) diğer dosyalara SIZDI —
    `scene.spec.js`'in 3 testi + `smoke.spec.js`'in 1 testi GERÇEKTEN
    başarısız oldu (pristine-başlangıç varsayımları bozulduğu için).
  - **Düzeltme:** `apps/ops-suite/e2e/test-server.js` (yeni, yeniden
    kullanılabilir modül, `startTestServer(port)` döndürür) çıkarıldı;
    `global-setup.js` SİLİNDİ; `playwright.config.js`'ten `globalSetup`
    kaldırıldı; her 3 spec dosyası artık KENDİ `test.beforeAll`/`afterAll`
    çifti ile KENDİ izole sunucusunu (kendi geçici veri dizini + gerçek
    alt-süreç) başlatıp durduruyor — dosyalar arası sızıntı yapısal
    olarak imkansız hale geldi.
  - **Not (2026-08-14) — resmen kapatıldı:** doğrulama: tüm paket
    (`smoke.spec.js` + `scene.spec.js` + `interactions.spec.js`, toplam
    12 test) birlikte iki kez art arda çalıştırıldı, 12/12 geçti, gerçek
    `data/presence/`/`data/approvals/` dizinlerinde sıfır kirlenme
    doğrulandı (her dosya kendi geçici `OPS_SUITE_DATA_DIR`'ını kullanıyor).

- [ ] **T43. Ses efektleri/geri bildirimi (BACKLOG.md B050)**
  - Amaç: Durum değişikliklerinde (ör. onay bekleniyor) kısa, yerel
    (CDN'siz) sesli geri bildirim.
  - Kapsam: TASARLANMADI — yalnızca BACKLOG.md B050 ile kaydedildi, bu
    checkpoint'te UYGULANMADI.
  - Bağımlılık: Yok.

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

**Ek (aynı gün, B036 post-triage aksiyonlar):** (1) Upstream issue bu
ortamdan açılamadı (`gh` CLI/token yok) — gönderime hazır tam paket
`reports/runtime_incident_20260813T004855Z/ISSUE_READY_PACKAGE.md`
(**READY_TO_SUBMIT**, manuel gönderim bekliyor). (2) Geçici CPU-only
workaround formalize edildi: `docs/ops/OLLAMA_WORKAROUND_CPU_ONLY.md`
(`OLLAMA_VULKAN=false`/`OLLAMA_LLM_LIBRARY=cpu`) — yalnızca
tanı/bilgilendirme amaçlı, resmi B031 kararını etkilemiyor. (3) CPU-only
profil altında bilgilendirici/non-gating bir B031 probe koşuldu
(`llama3`, 50 örnek): `intent_accuracy=%74.0`, `entity_match_rate=%41.7`,
`parse_error_rate=%0.0`, `fallback_rate=%0.0`, `latency_p95=20.59s` — 50/50
çağrı **çökmeden** tamamlandı (workaround'un stabilite bulgusuyla tutarlı)
ama gecikme eşiğini (`≤2.50s`) açık ara aştı. Sonuç ayrı bir dosyaya
kaydedildi (`reports/runtime_incident_20260813T004855Z/nlu_eval_20260813T011953Z_cpu_only_informational.md`)
— `tools/eval_nlu.py`'nin tarih-bazlı çıktı yolu (`reports/nlu_eval_20260813.md`)
üzerine yazılmaması için bilinçli olarak farklı isimlendirildi; ilk koşuda
yanlışlıkla üzerine yazıldığı fark edilip `git checkout` ile geri alındı
(o dosya, 00:13:05Z çöküş-keşif kanıtı olarak `RUNBOOK.md`'de referans
gösteriliyor). **B031 resmi durumu değişmedi: BLOCKED_BY_RUNTIME.** B036
durumu: **IN_PROGRESS** — çıkış kriterleri `docs/BACKLOG.md`'de netleştirildi
(varsayılan profilde 50/50 + 0 HTTP 500 + 0 `0xc0000005`), NVIDIA sürücü
güncelleme takip maddesi açıldı (sonuç bekliyor).

### 2026-08-14

- **Yapılanlar:** Ops Suite v0 (T21-T27, ADR-015..018) uygulandı — bkz.
  "Ops Suite v0 — Faz 5 Öne Çekme (İstisna)" bölümü yukarıda. Özet:
  gerçek domain modeli/olay sözleşmeleri (T21), heartbeat+durum
  çözümleyici (T22, "not_implemented" dürüstlük kuralıyla), kalıcı JSONL
  onay kuyruğu (T23, `orchestrator.py`/`approval_stub.py`/`risk_engine.py`
  DEĞİŞTİRİLMEDEN), gerçek FastAPI+WebSocket sunucusu (T24, ADR-007'nin
  ilk gerçek kullanımı), mocked-metin sesli komut kablolaması (T25),
  statik HTML/CSS/vanilla-JS frontend kabuğu (T26, ADR-018), gerçek bir
  alt-süreç `uvicorn` sunucusuna karşı çalışan E2E demo (T27,
  `scripts/ops_suite_demo.py`, 8/8 PASS). PEP 735 `[dependency-groups]`
  ile B032 (bağımlılık dosyası) de bu sırada kapatıldı (ADR-017).
  Toplam yeni test: 113 (`tests/test_ops_suite_*.py`, tamamı yeşil).
  Tam repo regresyonu: bkz. RUNBOOK.md.
- **Sorunlar (bulunup düzeltildi):** (1) `scripts/ops_suite_demo.py`'nin
  ilk iki çalıştırması, alt-süreç `PYTHONPATH`'inde
  `services/model-gateway/src` ve `tools/cli-runner/src` eksik olduğu
  için `ModuleNotFoundError` ile çöktü — pytest'in kendi `pythonpath`
  listesiyle EL İLE eşleştirilerek düzeltildi. (2) Gerçek demo
  çalıştırmasında `orchestrator` ajanının `display_name`'i yanlışlıkla
  `"orchestrator"` (ham `agent_id`) olarak görünüyordu (heartbeat
  kaydında `display_name` hiç geçilmemişti) — `voice_bridge.py`,
  `status_resolver.py::KNOWN_LIVE_AGENTS`'teki kanonik ismi yeniden
  kullanacak şekilde düzeltildi.
- **Bilinçli sınırlamalar (NOT_COLLECTED, fabrike edilmedi):** gerçek
  tarayıcı render'ı/etkileşimi (tarayıcı-otomasyon aracı yok), gerçek
  mikrofon/hoparlör/TTS sesi, gerçek GSM/SIM çağrı akışı, gerçek
  kamera/gesture girdisi — hepsi `docs/BACKLOG.md` B038-B040/B043'e
  kaydedildi, hiçbiri "tamamlandı" olarak İDDİA EDİLMEDİ.
- **Sonraki adım:** Gerçek bir Jira/kimlik-doğrulama katmanı OLMADAN
  `IDENTITY_AND_DELEGATION_POLICY.md` §4'teki `actor` alanı serbest
  metin kalmaya devam edecek — bu, Ops Suite'in dışarıya AÇILMASINDAN
  ÖNCE kapatılması gereken bir önkoşuldur.

**Ek (aynı gün, git truth reconciliation — SECURITY P0 checkpoint girişi):**
Yeni bir checkpoint başlatılmadan önce depo durumu taze komutlarla
doğrulandı (uydurulmadı):
- `git status` → `On branch main`, `Your branch is ahead of 'origin/main'
  by 1 commit`, `nothing to commit, working tree clean`.
- `git log --oneline -n 5` → `594db33` (Ops Suite v0, T21-T27) **HEAD**,
  ardından `276fb40`, `ae27f96`, `a9abac0`, `7bfb5d0` (Commit AF/AE/AD/AC —
  model-gateway promotion-pipeline zinciri, bu checkpoint'in kapsamı
  dışında).
- `git cat-file -e 594db33` → commit gerçekten mevcut; `git show --stat`
  ile 45 dosya doğrulandı (`apps/ops-suite/**`, 6 yeni `docs/*.md`, güncellenen
  `docs/{MASTER_ROADMAP,PLAN,BACKLOG,DECISIONS,RUNBOOK}.md`, `pyproject.toml`,
  `.gitignore`, 11 `tests/test_ops_suite_*.py` dosyası,
  `scripts/ops_suite_demo.py`, `reports/ops_suite_demo_20260813T225059Z/`
  kanıt paketi) — önceki oturumun özetiyle birebir eşleşiyor, sapma yok.
- **Sonuç:** commit `594db33` gerçek, HEAD'de, push edilmemiş (origin'in
  1 commit gerisinde), working tree temiz. Bu, aşağıdaki B044 (SECURITY P0)
  çalışmasının başladığı doğrulanmış taban.

**Ek (aynı gün, B044 SECURITY P0 uygulaması — T28):**
- **Yapılanlar:** `docs/BACKLOG.md`'ye B044 eklendi (Approval/Auth
  Enforcement + Owner Root Guard). Uygulandı: `ops_suite/identity.py`
  (yeni modül — `Identity`/`IdentityStore`/`authorize_decision`,
  bearer-token kimlik doğrulama + kapsam/scope tabanlı yetkilendirme +
  owner-root-guard), `config/ops_suite_identities.json` (yeni, yalnızca
  owner — `delegates: []`, token DEĞERİ YOK), `approval_queue.py::decide()`
  genişletildi (`actor` → `actor_id`/`auth_method`/`authority_source`/`decision_scope`,
  + yeni `get_pending_entry()`), `app.py` (Bearer-token `Depends()`,
  `/api/approvals/{id}/approve|reject` artık kimlik doğrulama+yetkilendirme
  ZORUNLU, yeni `GET /api/whoami`), `server.py` (`OPS_SUITE_IDENTITY_CONFIG_PATH`
  env override), frontend (`index.html`/`app.js`/`style.css` — token giriş
  alanı, `localStorage`, `Authorization: Bearer` header, whoami rozeti).
  Testler: `tests/test_ops_suite_identity.py` (24 yeni), `test_ops_suite_api.py`
  (10 yeni B044 testi + mevcutlar owner-token'lı güncellendi),
  `test_ops_suite_approval_queue.py`/`test_ops_suite_ws.py` (yeni imzaya
  güncellendi). `scripts/ops_suite_demo.py` genişletildi — gerçek owner+delegate
  demo kimlikleri (rastgele token, GERÇEK KİŞİLER DEĞİL), 401 (token yok) +
  403 (delegate root-guard) adımları gerçek HTTP ile kanıtlandı.
- **Sorunlar (bulunup düzeltildi):** `git add -A` sonrası tam regresyon
  ilk seferde **1 test FAIL** verdi — `test_evaluate_pilot_promotion.py::test_scan_repo_for_secrets_real_repo_and_real_config_finds_nothing`.
  Kök neden: repo'nun kendi secret-tarama deseni (`generic_secret_assignment`,
  bkz. `infra/monitoring/governance/secret_scan_patterns_v1.json`),
  `IDENT_TOKEN` adında bir değişkene tırnaklı bir değer atanmasını genel
  olarak yakalıyor — 4 yeni test dosyasındaki dummy token sabitleri
  (`OWNER_TOKEN`, `DELEGATE_FULL_TOKEN`, `DELEGATE_LOW_TOKEN`) VE
  `identity.py`'deki gerçek (ama SIR OLMAYAN) eski `AUTH_METHOD_BEARER_TOKEN`
  sabitinin ("bearer_token" değerli) tanımı bunu tetikledi. **Düzeltildi:** (1) test sabitlerinin değerleri mevcut
  allowlist-marker sözleşmesine uyacak şekilde `dummy-*-token` olarak
  yeniden adlandırıldı (tarayıcının KENDİ dokümante edilmiş önerisi — dosya
  bazlı istisna yerine veri içinde marker), (2) `AUTH_METHOD_BEARER_TOKEN`
  sabiti `AUTH_METHOD_BEARER = "bearer"` olarak yeniden adlandırıldı (OAuth2
  `token_type` sözleşmesiyle uyumlu, daha kısa/temiz, deseni de doğal olarak
  atlatıyor — regex'i "atlatmak" için değil, isimlendirmeyi iyileştirmek
  için yapılan gerçek bir değişiklik). Düzeltme sonrası tam regresyon
  **934/934 yeşil**.
- **Kanıt:** `scripts/ops_suite_demo.py` GERÇEKTEN çalıştırıldı — **11/11
  adım PASS** (`reports/ops_suite_demo_20260813T232850Z/`, `git add -f` ile
  arşivlendi). Yeni adımlar: `approve_rejected_without_token` (401),
  `approve_rejected_delegate_root_guard` (403, delegate'in config'inde
  `approve:irreversible` OLMASINA RAĞMEN), `approvals_still_pending_after_denied_attempts`
  (reddedilen denemeler kararı GERÇEKLEŞTİRMEDİ), `approve_decision` (owner,
  200) — `audit_log_check` artık 4 yeni kimlik alanının VARLIĞINI da
  doğruluyor.
- **Bilinçli sınırlamalar (fabrike edilmedi):** tek kimlik doğrulama yöntemi
  (bearer-token, OAuth/OIDC/mTLS YOK), token rotasyon/iptal UI/CLI'ı YOK,
  rate limiting/brute-force koruması YOK — bkz.
  `docs/IDENTITY_AND_DELEGATION_POLICY.md` §5 (bu üç madde BACKLOG.md'ye
  henüz taşınmadı, P0 değil).
- **Sonraki adım:** B038 (tam animasyonlu 2D ofis sahnesi) — B044'ün
  GO/NO-GO değerlendirmesi için bkz. bu checkpoint'in final raporu.

**Ek (aynı gün, B038 ön koşulu #1 — T29/B042, git truth reconciliation #2):**
- **Git truth (taze komutlar):** `git status` → `On branch main`, ahead
  of origin by 1 commit, **"Changes to be committed"** — B044'ün 20
  dosyası (`git add -A` ile) staged durumda, hiçbir şey commit
  EDİLMEDİ. `git diff --name-only` (unstaged) → **boş**. `git diff
  --cached --name-only` (staged) → aynı 20 dosya (identity.py,
  approval_queue.py, app.py, server.py, frontend/*, config/ops_suite_identities.json,
  5 docs, demo evidence 3 dosyası, 4 test dosyası). Sapma yok, önceki
  raporla birebir eşleşiyor.
- **Yapılanlar (B042):** `docs/DECISIONS.md` ADR-020 yazıldı — 2 seçenek
  (Vanilla JS+Canvas2D vs PixiJS-vendored) offline-first/test edilebilirlik/
  performans/bakım kriterleriyle karşılaştırıldı, **Vanilla JS+Canvas2D**
  seçildi (bu ölçekte PixiJS'in ek bağımlılık/bakım yükü haklı çıkmıyor).
  BACKLOG.md B042 **Kapalı**.
- **Sorunlar:** Yok.
- **Sonraki adım:** T30/B039 (tarayıcı E2E hazırlığı).

**Ek (aynı gün, B038 ön koşulu #2 — T30/B039):**
- **Yapılanlar:** `apps/ops-suite/e2e/` oluşturuldu (repo'nun İLK
  `package.json`'ı) — `@playwright/test` kuruldu, GERÇEK Chromium indirildi
  (`npx playwright install chromium`, ~306MB, GERÇEKTEN tamamlandı),
  `global-setup.js` gerçek bir `python -m ops_suite.server` alt-süreci
  başlatıp durduruyor (port 8421, `scripts/ops_suite_demo.py` ile AYNI
  PYTHONPATH deseni). `tests/smoke.spec.js` (2 senaryo) **GERÇEKTEN
  ÇALIŞTIRILDI — 2/2 PASS**. `docs/DECISIONS.md` ADR-021 yazıldı (npm'in
  kapsamı yalnızca test-tooling, ADR-018 YENİDEN AÇILMADI). BACKLOG.md
  B039 **Kısmen tamamlandı** (tam görsel doğrulama B038 var olmadan
  tamamlanamaz — ama araç artık GERÇEKTEN mevcut ve kanıtlı).
  `docs/RUNBOOK.md`'ye "Ops Suite — Gerçek Tarayıcı E2E (B039)" bölümü
  + 2 yeni troubleshooting satırı eklendi.
- **Sorunlar:** Yok — her iki adım da (playwright install, test koşusu)
  ilk denemede başarılı oldu.
- **Dürüstlük notu:** Bu kanıt yalnızca BU oturumun/makinenin GERÇEKTEN
  bir tarayıcı indirip çalıştırabildiğini gösterir — evrensel bir
  "artık her ortamda tarayıcı testi mümkün" iddiası DEĞİLDİR (bkz.
  ADR-021 kapsam notu, BACKLOG.md B039 notu).
- **Sonraki adım:** B038'in gerçek uygulaması — GO/NO-GO kararı için
  bkz. bu checkpoint'in final raporu.

### 2026-08-14 (devam) — Commit D + v0.3.0-ops-scene-v0.2 + T37-T39 checkpoint

- **Yapılanlar (commit/tag):** Önceki checkpoint'in staged B038 işi
  (20 dosya) `Commit D: B038 Canvas2D office scene v0.2 (T31-T36) +
  evidence + data-dir isolation fix` mesajıyla tek bir commit'e
  (`452eff1`) alındı; `v0.3.0-ops-scene-v0.2` etiketi bu commit'e
  eklendi. `git log -n5`: `452eff1` (Commit D) → `c712fea` (Commit C,
  B039) → `9f16005` (Commit B, B042) → `f694aeb` (Commit A, B044) →
  `594db33` (Ops Suite v0, T21-T27).
- **Yapılanlar (yeni görevler):** `docs/PLAN.md`'ye T37 (kapatıldı),
  T38 (taslak, henüz uygulanmadı), T39 (taslak, henüz uygulanmadı)
  eklendi. `docs/BACKLOG.md`'ye B045 (T37 ile birlikte kapatıldı), B046
  (açık) eklendi; B041 (ÖNCEDEN VAR OLAN madde — YENİDEN AÇILMADI/
  DUPLICATE EDİLMEDİ) T39'a çapraz referanslandı.
- **Yapılanlar (T37 uygulaması):** `heartbeat.py::HeartbeatTracker.on_change`
  (opsiyonel, geriye uyumlu kanca) + `voice_bridge.py`'nin bunu her
  çağrıda `presence_events`'e toplaması + `app.py`'nin bunları
  önceden tanımlı-ama-hiç-kullanılmamış `TOPIC_AGENT_PRESENCE`
  konusuna, `task.lifecycle` ile AYNI örüntüyle yayınlaması. 8 yeni
  test (4 heartbeat + 3 voice_bridge + 1 WS — `["working", "idle"]`
  sırasını gerçek bir WS el sıkışmasıyla DOĞRUDAN doğruluyor). 2 mevcut
  WS testi, artık gerçek olan 2 fazla mesajı yansıtacak şekilde
  güncellendi (davranış DEĞİL, test beklentisi düzeltildi).
- **Test özeti:** Tam pytest regresyonu **942/942 yeşil** (934+8: 4
  heartbeat + 3 voice_bridge + 1 WS testi). Playwright: **5/5 yeşil**
  (T37 mevcut sahne/smoke testlerini BOZMADI — `scene.js` yeni
  `agent.presence` mesajlarını henüz TÜKETMİYOR, yalnızca canlı akışa
  düşüyorlar, bkz. T38).
- **Sorunlar:** Yok.
- **Sonraki adım:** T38 (frontend tüketimi + zamanlamaya dayanmayan
  Playwright kanıtı) veya T39 (kalıcılık) — hangisinin önce alınacağı
  henüz kararlaştırılmadı, ikisi de BAĞIMSIZ alınabilir.

### 2026-08-14 (devam 2) — Etiket düzeltmesi + B038 Tamamlama Parçası (T40/T42/T44)

- **Etiket düzeltmesi belgelendi:** `v0.3.1-presence-events` başlangıçta
  yanlışlıkla `452eff1`'e işaret ediyordu, `6f5c5d4`'e düzeltildi — bkz.
  `docs/releases/v0.3_OPS_SUITE_SCENE.md` (tam açıklama) ve bu Daily Log
  girişi (kısa not, kalıcı kayıt).
- **Yeni görevler:** `docs/BACKLOG.md`'ye B047 (varlık hattı, kapatıldı),
  B048 (çoklu-adımlı animasyon, TASARLANMADI/açık), B049 (tıklama
  etkileşimleri, kapatıldı), B050 (ses efektleri, TASARLANMADI/açık)
  eklendi. `docs/PLAN.md`'ye T40 (B047, kapatıldı), T41 (B048, açık
  placeholder), T42 (B049, kapatıldı), T43 (B050, açık placeholder)
  eklendi.
- **T40/B047 uygulaması:** `apps/ops-suite/frontend/assets/sprites/*.svg`
  (5 yerel dosya, CDN yok); `scene.js`'e sprite ön-yükleme + geri-düşme
  render yolu. **Gerçek bulunan hata:** SVG yorumlarındaki geçersiz XML
  (`--` içeren `<!-- -->`) Chromium'un `Image.onload`'ını sessizce
  engelliyordu (ağ yanıtı 200/doğru mimetype olmasına rağmen) — diagnostik
  bir Playwright betiğiyle kök nedene inildi, 5 dosya da düzeltildi.
- **T42/B049 uygulaması:** `scene.js`'e canvas tıklama hit-test'i +
  koordinat ölçekleme; `app.js`'e `openAgentDetailPanel()`; yeni
  `#agent-detail-panel` DOM'u. Dürüstlük kuralı: ajanların "yetki kapsamı"
  YOK denilerek açıkça belirtiliyor (uydurulmuş alan yok); onay bağlantısı
  `last_task_id === request_id` eşleşmesiyle kuruluyor (şema
  `awaiting_approval` durumu kod tabanında hiç üretilmediği için).
- **T44 (test altyapısı, BACKLOG ID yok):** `interactions.spec.js` eklenip
  tüm Playwright paketi birlikte çalıştırıldığında, eski paylaşılan-tek-
  sunucu mimarisi (`global-setup.js`) nedeniyle 4 test GERÇEKTEN
  başarısız oldu (dosyalar arası durum sızıntısı). `apps/ops-suite/e2e/test-server.js`
  (yeni, `startTestServer(port)`) çıkarıldı, `global-setup.js` silindi,
  her spec dosyası artık kendi `beforeAll`/`afterAll` çifti ile kendi
  izole sunucusunu yönetiyor.
- **Test özeti:**
  - Python: tam pytest regresyonu **953/953 yeşil** (bu checkpoint'te
    Python tarafı değişmedi — sprite/tıklama/test-server işi tamamen
    frontend + Playwright altyapısı; sayı önceki checkpoint'in 953'ü ile
    aynı, gerçekten yeniden çalıştırılarak doğrulandı).
  - Playwright: **12/12 yeşil** (3 dosya birlikte — 2 yeni B047 testi +
    4 yeni B049 testi `interactions.spec.js`'te, 4 mevcut `scene.spec.js`,
    2 mevcut `smoke.spec.js`), art arda 2 kez tam paket çalıştırılarak
    doğrulandı; `data/presence/`/`data/approvals/` dizinlerinde bu
    koşulardan kaynaklı sıfır yeni kirlenme doğrulandı.
  - Gerçek kanıt: `reports/ops_suite_interactions_2026-08-14T0126Z/`
    (`capture_interactions_evidence.js` ile üretildi — 5 PNG + `evidence.json`/
    `evidence.md`, `genel_sonuc=PASS`, `git add -f` ile arşivlendi).
- **Sorunlar:** Yok (2 gerçek hata bulundu ve düzeltildi — yukarıda
  belgelendi; her ikisi de kanıtla doğrulandı, gizlenmedi).
- **Sonraki adım:** B038, B047+B049 ile birlikte "tamamlandı" olarak
  işaretlenebilir mi kararı — bkz. bu checkpoint'in GO/NO-GO raporu (B048
  çoklu-adım animasyon ve B050 ses efekti TASARLANMADI/açık kalıyor,
  bilinçli/dürüst bir kapsam sınırı olarak).

