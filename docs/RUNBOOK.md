# RUNBOOK — EzoEzgi Ops

> Operasyonel başvuru dokümanı. Servisler kod ile birlikte gelince buradaki komutlar
> güncellenecek; şu an Faz 0 (iskelet) için geçerli minimum akış.

## Günlük Çalışma Akışı

1. `docs/PLAN.md` içinde bugünün görevlerini kontrol et.
2. Görev tamamlanınca ilgili checkbox `[x]` yapılır.
3. Gün sonunda `docs/PLAN.md` → **Daily Log** bölümüne yeni giriş eklenir:
   - Yapılanlar
   - Sorunlar
   - Sonraki adım
4. Kapsam dışı yeni istekler `docs/BACKLOG.md`'ye eklenir, aktif sprint'e dahil edilmez.
5. Mimari/teknoloji kararları `docs/DECISIONS.md`'ye ADR olarak yazılır.

## Preflight Kontrol

Repo üzerinde çalışmaya başlamadan önce (özellikle birden fazla proje/checkout
arasında geçiş yapıldıysa) çalıştırılması önerilir:

```
.\.venv\Scripts\python.exe scripts\preflight.py
```

Bu komut önce `scripts/repo_guard.py`'yi çağırır — bu dizinin gerçekten
EzoEzgi Ops olduğunu (`PROJECT_IDENTITY.yaml` manifestinin varlığı, `repo_slug`
eşleşmesi, `canonical_paths` altındaki kritik klasörlerin varlığı ve scriptin
kendi konumunun repo root ile tutarlılığı üzerinden) doğrular. Ardından kısa
bir sağlık kontrolü yapar: Python sürümü, `config/cli_whitelist.json` ve
`docs/PLAN.md` dosyalarının varlığı. Sonuç bir PASS/FAIL tablosu olarak
yazdırılır; herhangi bir madde FAIL ise exit code `1` döner.

Örnek çıktı:
```
Preflight Kontrol -- EzoEzgi Ops
============================================
[PASS] Repo Guard                 REPO_GUARD_OK
[PASS] Python version             3.13.3 (>= 3.10 gerekli)
[PASS] config/cli_whitelist.json  bulundu
[PASS] docs/PLAN.md               bulundu
--------------------------------------------
Sonuç: 4/4 PASS
```

`PROJECT_IDENTITY.yaml` (repo köküne) elle silinmemeli/taşınmamalı —
silinirse `repo_guard`/`preflight` bilinçli olarak FAIL verir. Yalnızca
`repo_guard.py`'yi çalıştırmak isterseniz:
```
.\.venv\Scripts\python.exe scripts\repo_guard.py
```

## Kimlik / Wake-Alias Değişikliği

- Kaynak dosya: `config/assistant.identity.json`.
- `wake_aliases` listesini düzenlemek restart gerektirmez (`alias_update_policy.requires_restart: false`).
- Değişiklikten sonra bir sonraki kullanıcı komutunda yeni alias'lar geçerli olmalı
  (bkz. PLAN.md T5 — config loader).

## E2E Demo Çalıştırma (T5–T14, T17 — finans DIŞI, Faz 0 çekirdek zincir)

Bu demo, finans içermeyen çekirdek zinciri (config → wake-alias → bridge →
risk kontrolü → onay kontrolü → cli-runner (echo) → audit log → TR yanıt)
uçtan uca gösterir. T14 ile resmen kapandı (bkz. PLAN.md T14).

**Kurulum (bir kez):**
```
cd D:\Projects\ezoezgi-ops
C:\Python313\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip pytest pyyaml
```

**Testleri çalıştırma:**
```
.\.venv\Scripts\python.exe -m pytest
```
Beklenen: `tests/` altındaki tüm testler (T5, T6, T7-T9, T10-T11, T12, T13,
T14, T17) yeşil — toplam 73 test. Test config'i (`pyproject.toml`)
`pythonpath` ile `apps/orchestrator/src`, `services/tr-en-bridge/src`,
`tools/cli-runner/src`, `scripts` dizinlerini otomatik ekler — testler
modülleri paket kurulumu olmadan doğrudan import edebilir.

**E2E demo'yu çalıştırma — Senaryo 1 (düşük risk, otomatik çalışır):**
```
.\.venv\Scripts\python.exe scripts\e2e_demo.py "Ezo, echo ile 'merhaba' yaz"
```
Argümansız çalıştırılırsa varsayılan örnek cümle kullanılır. Beklenen çıktı:
```
[TR girdi] Ezo, echo ile 'merhaba' yaz
[TR yanit] Merhaba yazdırıldı.
[risk] low  [status] ok
[audit log] data\audit\audit.log.jsonl
```

**E2E demo'yu çalıştırma — Senaryo 2 (irreversible risk, onay bekliyor):**
```
.\.venv\Scripts\python.exe scripts\e2e_demo.py "Ezo, tüm dosyaları sil"
```
Beklenen çıktı:
```
[TR girdi] Ezo, tüm dosyaları sil
[TR yanit] Bu işlem yüksek riskli (irreversible) olduğu için onay bekliyor. Devam etmek için onay vermeniz gerekiyor.
[risk] irreversible  [status] WAITING_APPROVAL
[audit log] data\audit\audit.log.jsonl
```
Bu senaryoda `run_echo`/`runner.run_command` hiç çağrılmaz — risk kontrolü
handler'dan önce devreye girer (bkz. "Risk Etiketleme ve Onay Akışı" altında).

Her iki senaryo da `data/audit/audit.log.jsonl`'a yeni bir JSONL satırı ekler
(append-only, bkz. ADR-009).

## Whitelist Yönetimi (CLI Runner, T12)

- Kaynak dosya: `config/cli_whitelist.json`.
- Yeni bir komut eklemek için `commands` altına yeni bir anahtar eklenir:
  ```json
  "yeni_komut": {
    "executable": "gercek-binary-adi",
    "description": "Ne yaptigi",
    "risk_level": "low",
    "timeout_seconds": 5
  }
  ```
- **Kurallar (asla bozulmamalı, bkz. ADR-012):**
  - `executable`, sistemde gerçekten bulunabilen (`shutil.which()` ile
    çözümlenebilen) bir program olmalı; aksi halde çağrı `EXECUTABLE_NOT_FOUND`
    ile başarısız olur (crash değil).
  - Komut asla bir shell string'i olarak değil, liste argümanlarıyla çalıştırılır
    (`shell=True` yasak) — whitelist'e `"executable": "bash -c ..."` gibi bir
    shell komutu **eklenmemeli**.
  - `timeout_seconds` verilmezse `default_timeout_seconds` (varsayılan 5)
    kullanılır; süresiz çalışan bir komut asla olmaz.
- Whitelist dışı bir `command_name` çağrılırsa (`runner.run_command`) hiçbir
  process başlamaz, `NOT_WHITELISTED` hata kodu döner.
- Yeni bir komut eklerken karşılığında `policies/risk/tool_risk_policy.yaml`'a
  da (T13) bir `risk_level` girişi eklenmesi önerilir — eklenmezse
  `default_risk_level` (`medium`, otomatik izinli ama en gevşek seviye değil)
  uygulanır.

## Risk Etiketleme ve Onay Akışı (T13)

- Kaynak dosya: `policies/risk/tool_risk_policy.yaml` — `tasks:` altında
  `task_en → risk_level` (`low`/`medium`/`high`/`irreversible`) eşlemesi.
- `apps/orchestrator/src/risk_engine.py::RiskEngine.get_risk(task_name)` bu
  dosyayı okur; policy'de tanımsız bir task, `default_risk_level`'a
  (varsayılan `medium`) düşer — **asla otomatik `low` sayılmaz**.
- `apps/orchestrator/src/approval_stub.py::check_approval(risk_level)`:
  - `low`/`medium` → `AUTO_ALLOWED` (orchestrator handler'ı direkt çağırır).
  - `high`/`irreversible` → `WAITING_APPROVAL` (handler'a **hiç gidilmez**,
    `Orchestrator.handle_task()` bu durumu döner).
- Bu aşamada gerçek bir approve/reject arayüzü yok (yalnızca stub) — Faz 4'te
  (bkz. MASTER_ROADMAP.md §8) gerçek onay akışı eklenecek. Şimdilik
  `WAITING_APPROVAL` durumu yalnızca audit log'a düşer ve TR yanıt olarak
  kullanıcıya bildirilir, otomatik yürütme olmaz.
- Yeni bir risk seviyesi eklemek/değiştirmek için `tool_risk_policy.yaml`'ı
  düzenlemek yeterli — kod değişikliği gerekmez.

**Bilinen ortam notları:**
- pytest'in varsayılan geçici dizini (`%TEMP%\pytest-of-<kullanıcı>`) bu ortamda
  `PermissionError` verebiliyor; `pyproject.toml`'da `addopts = "--basetemp=.pytest_tmp"`
  ile proje-lokal bir geçici dizine yönlendirildi (`.gitignore`'da).
- Windows konsolları varsayılan olarak UTF-8 olmayabilir; TR karakterler
  (`ı`, `ş`, `ğ` vb.) bu durumda ekranda bozuk görünebilir (veri bozulmaz,
  yalnızca render). `e2e_demo.py` bunu `sys.stdout.reconfigure(encoding="utf-8")`
  ile otomatik düzeltmeye çalışır; sorun devam ederse `chcp 65001` veya
  `$env:PYTHONIOENCODING="utf-8"` deneyin.

## Faz 1 — Ollama NLU Entegrasyonu (B031)

> Migration notu: Bu bölüm, `bridge.py`'nin mock (anahtar kelime tabanlı)
> sınıflandırıcının yanına gerçek bir Ollama tabanlı NLU adaptörü
> (`services/tr-en-bridge/src/ollama_nlu.py`) eklendiğinde yazıldı
> (Faz 1, BACKLOG.md B031). Mevcut `translate_and_extract()` arayüzü
> (`detected_alias`/`task_en`/`original_tr`/`confidence`) **değişmedi** —
> orchestrator, audit logger ve `scripts/e2e_demo.py` hiçbir değişiklik
> gerektirmedi.

### Nasıl etkinleştirilir

Varsayılan davranış **değişmedi** — hiçbir env değişkeni ayarlanmazsa mock
sınıflandırıcı (agsız, anlık) kullanılmaya devam eder. Ollama'yı etkinleştirmek
için:

```
$env:NLU_PROVIDER = "ollama"
.\.venv\Scripts\python.exe scripts\e2e_demo.py "Ezo, echo ile merhaba yaz"
```

### Gerekli/opsiyonel env değişkenleri

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `NLU_PROVIDER` | `mock` | `mock` veya `ollama`. Tanımsız/geçersiz bir değer (örn. yazım hatası) sessizce `mock`'a düşer — asla bilinmeyen bir sağlayıcıya yönlendirmez. |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | T8'de tanımlı, `model_client.py` tarafından okunur. |
| `OLLAMA_MODEL` | `llama3` | T8'de tanımlı, `model_client.py` tarafından okunur. |

### Hata/fallback davranışı

`ollama` sağlayıcısı seçiliyken aşağıdaki durumların **hiçbiri** bridge'i
çökertmez veya exception fırlatmaz — hepsi sessizce mock sınıflandırmaya
düşer:

- Ollama servisi ayakta değil / erişilemiyor (health check başarısız).
- Zorunlu timeout aşıldı (`OllamaModelClient` varsayılan 2.0s).
- Model geçerli JSON dönmedi veya beklenen alanları içermiyor (`intent`,
  `confidence`) — bir kez daha denenir (`max_attempts`, varsayılan 2), yine
  başarısız olursa `UNKNOWN` intent'e düşülür.
- Model, whitelist dışı (bilinmeyen) bir `intent` döndürdü.

Bu davranış, `data/audit/audit.log.jsonl`'a düşen kayıtları etkilemez —
audit logger, hangi sağlayıcının kullanıldığından habersizdir, yalnızca
nihai `task_en`/`risk_level`/`status`'u görür.

**Güvenlik notu:** Bu entegrasyon finans execution'ı **etkilemez** —
T18–T20 hâlâ Paused (ADR-011). NLU sağlayıcısı ne olursa olsun, risk/onay
akışı (T13) değişmeden çalışmaya devam eder (`RUN_DELETE_FILE` gibi
`irreversible` task'lar, hangi sağlayıcı tespit ederse etsin `WAITING_APPROVAL`'a
düşer).

### Quality Gate (B031 tamamlanma kriteri)

B031'in "tamamlandı" sayılabilmesi için `mock` yerine `ollama`'nın **canlı ve
ölçülmüş** bir kalitede çalıştığının kanıtlanması gerekir. Bunun için:

- **Golden veri seti:** `tests/fixtures/nlu_golden_tr.jsonl` — 50 Türkçe
  örnek (JSONL, satır başına bir örnek: `text`, `expected_intent`,
  `expected_entities`, opsiyonel `notes`/`risk_level`). Dengeli kategoriler:
  net komutlar, belirsiz cümleler, yazım hatası/konuşma dili varyantları,
  whitelist dışı niyetler, `irreversible` (onay bekleyen) aksiyonlar.
- **Değerlendirme aracı:** `tools/eval_nlu.py` — aynı golden set üzerinde
  `mock` ve `ollama`'yı karşılaştırır, markdown rapor üretir
  (`reports/nlu_eval_<YYYYMMDD>.md`).

**Çalıştırma:**
```
.\.venv\Scripts\python.exe tools\eval_nlu.py
```
Yalnızca tek bir sağlayıcıyı çalıştırmak için: `--providers mock` veya
`--providers ollama`. Özel golden set/çıktı yolu için: `--golden <path>`,
`--out <path>`.

**Ollama kurulu değilse/erişilemiyorsa:** araç çökmez veya sayı uydurmaz —
`ollama` sütununu `N/A` olarak işaretler, sebebini yazar (health check
hatası) ve kurulum adımlarını (Ollama indir, `ollama serve`, `ollama pull
<model>`) rapora ekler. Bu durumda `mock` sonuçları yine üretilir ama
**yalnızca referans amaçlıdır** — quality gate kriterleri özellikle canlı
Ollama içindir, mock'a karşı asla "PASS" sayılmaz.

> **Önemli:** Mock provider entity extraction üretmez; entity metriği
> yalnızca Ollama canlı değerlendirmesinde gate kriteridir. Bu yüzden
> `entity_match_rate` mock için her zaman `%0`/`FAIL`'dir — bu beklenen ve
> zararsızdır, B031'in tamamlanma kararını **etkilemez** (karar yalnızca
> `ollama` sütununa bakar).

### Canlı Ollama Kapanış Prosedürü (B031 Completed kararı)

Ollama kurulu bir makinede (bu geliştirme ortamı **değil**), B031'i
kapatmak için sırayla:

**1) Servisi başlat:**
```
ollama serve
```
(Bazı kurulumlarda arka planda otomatik başlar; `ollama list` komutu hata
vermeden çalışıyorsa servis zaten ayaktadır.)

**2) Modeli indir** (varsayılan `OLLAMA_MODEL=llama3`; farklı bir model
kullanılacaksa önce `$env:OLLAMA_MODEL` ile ayarlanmalı):
```
ollama pull llama3
```

**3) Değerlendirmeyi çalıştır:**
```
cd D:\Projects\ezoezgi-ops
.\.venv\Scripts\python.exe tools\eval_nlu.py
```
Beklenen: rapor artık `ollama` sütununda `N/A` değil, gerçek sayılar
gösterir (`reports/nlu_eval_<YYYYMMDD>.md`).

**4) Eşikleri yorumla:** Rapordaki "### ollama" tablosunda 5 satırın
`Sonuç` kolonuna bak (bkz. "Metrikler ve eşikler" tablosu yukarıda).

**5) Karar ver:**
- **5 kriterin tamamı `PASS`** → `docs/BACKLOG.md`'de B031'i **Tamamlandı**
  olarak işaretle, rapor dosyasını (`reports/nlu_eval_<tarih>.md`) referans
  ver, gerekiyorsa `docs/DECISIONS.md`'ye kapanış ADR'ı ekle.
- **En az bir kriter `FAIL`** → B031 **Kısmen tamamlandı** kalır; BACKLOG
  satırına hangi metrik(ler)in eşiği karşılamadığını ve sayısal değerini
  ekle. Muhtemel iyileştirme: `ollama_nlu.py::_build_prompt`'u modelin
  gerçekte döndürdüğü formata göre ayarlamak veya farklı bir model denemek.
- **Sonuç uydurma yok:** Çıktı ne olursa olsun rapor dosyası olduğu gibi
  saklanır/commit edilir; PASS görünmesi için eşik veya golden set
  değiştirilmez.

**Metrikler ve eşikler** (`tools/eval_nlu.py::ACCEPTANCE_CRITERIA`):

| Metrik | Eşik | Anlamı |
|---|---|---|
| Intent accuracy | ≥ %90 | Doğru sınıflandırılan örnek oranı |
| Entity match rate | ≥ %85 | Kritik entity alanlarının tam eşleştiği örnek oranı (yalnızca `expected_entities` dolu örnekler üzerinden) |
| Parse error rate | ≤ %2 | Modelin geçerli/ayrıştırılabilir JSON döndürmediği örnek oranı |
| Fallback rate | ≤ %5 | Servise hiç ulaşılamadığı için mock'a düşülen örnek oranı |
| Latency p95 | ≤ 2.5s | 50 örneklik koşunun 95. yüzdelik gecikmesi |

**Yorumlama:** Rapordaki "Sonuç" bölümü üç durumdan birini yazar:
- **PASS** — tüm kriterler karşılandı, B031 tamamlanmaya hazır.
- **FAIL** — en az bir kriter karşılanmadı; hangi metrik(ler) eşiğin altında/üstünde
  kaldıysa tablo bunu gösterir. Sonraki adım genelde prompt iyileştirme
  (`ollama_nlu.py::_build_prompt`) veya farklı bir model denemek.
- **NOT_EVALUATED** — Ollama bu ortamda kullanılamadı, kriterler hiç test
  edilemedi. Bu bir başarısızlık değil, ortam kısıtıdır.

**Sorun giderme:**
| Belirti | Olası neden | Bakılacak yer |
|---|---|---|
| `ollama` sütunu hep N/A | Ollama kurulu/çalışır değil | `ollama serve` çalıştırın, `curl http://localhost:11434/api/tags` ile test edin |
| Tüm örnekler `parse_error` | Model JSON formatını takip etmiyor | `ollama_nlu.py::_build_prompt` içindeki formatı modelin desteklediği bir formata uyarlayın |
| `entity_match_rate` düşük ama `intent_accuracy` yüksek | Model intent'i doğru buluyor ama entity şemasını bilmiyor | Prompt zaten `ollama_nlu.py::ENTITY_SCHEMA_HINTS`'teki alan adlarını modele bildiriyor (`RUN_ECHO` → `value`); hâlâ düşükse ilgili `task_en` için sözlüğe yeni bir satır ekleyin veya örnek/açıklamayı zenginleştirin |
| Rapor `reports/`'a yazılmıyor | Dizin izin sorunu | `reports/` klasörünün var/yazılabilir olduğunu kontrol edin (`.gitkeep` yoksa oluşturun) |

**Değerlendirme koşu kayıtları:**

"Canlı Ollama endpoint (localhost:11434) timeout nedeniyle B031 metrikleri
ölçülemedi; sonuç Partial / NOT_EVALUATED. Mock sonuçlar karar-dışı referans
olarak saklandı."

- Preflight: 4/4 PASS
- Eval artifact: `reports/nlu_eval_20260812.md`

| Tarih (UTC) | Ortam | Intent acc. | Entity match | Parse error | Fallback | p95 latency | Karar |
|---|---|---|---|---|---|---|---|
| 2026-08-12T23:45:38Z | local Ollama (`http://localhost:11434`) | N/A | N/A | N/A | N/A | N/A | **Partial / NOT_EVALUATED** — Ollama servisine ulaşılamadı (`urlopen error timed out`); mock referans: intent acc. %92.0, entity match %0.0 (bkz. `reports/nlu_eval_20260812.md`) |
| 2026-08-12T23:49:36Z | local Ollama (`http://localhost:11434`) | N/A | N/A | N/A | N/A | N/A | **Partial / NOT_EVALUATED** — preflight 4/4 PASS sonrası tekrar koşuldu, sonuç değişmedi: Ollama servisine ulaşılamadı (`urlopen error timed out`); mock referans: intent acc. %92.0, entity match %0.0 (bkz. `reports/nlu_eval_20260812.md`) |
| 2026-08-13T00:01:19Z | local Ollama (`http://localhost:11434`, `llama3:latest`, winget ile kuruldu) | %30.0 | %0.0 | %0.0 | %100.0 | 6.06s | **Partial / FAILED_THRESHOLDS** — ilk canlı ölçüm; 5 kriterden 4'ü FAIL (yalnızca parse_error_rate PASS). Kök neden: `model_client.py::DEFAULT_TIMEOUT_SECONDS=2.0s`, gerçek CPU inference gecikmesinin (~6s, ölçülen p95) çok altında — her istek zaman aşımına uğrayıp mock fallback'e düşüyor (`fallback_rate=%100`). `intent_accuracy=%30`, modelin gerçekten çalışmasından değil, golden set'teki `UNKNOWN` oranıyla (15/50) rastlantısal örtüşmeden kaynaklanıyor — model bir kez bile gerçek yanıt veremedi. **Sonraki adım:** timeout'u gerçekçi bir değere (ör. 15-30s) çıkarıp yeniden koşmak (bkz. `reports/nlu_eval_20260813.md`) |
| 2026-08-13T00:13:05Z | local Ollama (`llama3:latest`, `OLLAMA_TIMEOUT_SECONDS` hotfix sonrası: 2.0s→30.0s) | %30.0 | %0.0 | %0.0 | %100.0 | 12.89s | **Partial / FAILED_THRESHOLDS** — timeout hotfix'i doğru çalıştı (istekler artık erken kesilmiyor, gerçek sunucuya ulaşıyor) ama **yeni ve farklı bir kök neden** ortaya çıktı: Ollama'nın `llama-server` alt süreci her istekte çöküyor — sunucu `HTTP 500` ile `"llama-server process has terminated: exit status 0xc0000005: The instruction at 0xp referenced memory at 0xp"` (Windows access violation) döndürüyor; manuel `curl` ile doğrulandı. Bu bir timeout/config sorunu değil, yerel Ollama çalışma zamanının bu makinede çökmesi. Sistem RAM'i yeterli (66.9GB toplam, 23GB boş) — kaynak kısıtı değil. Sayılar önceki koşuyla aynı görünüyor (%30/%0/%100) ama nedeni farklı — tesadüfen aynı fallback deseni. **Sonraki adım:** kullanıcıyla birlikte netleştirilecek (bkz. sohbetteki soru) |

### Incident: Ollama Windows runtime çöküyor (0xc0000005) — 2026-08-13

**Özet:** Timeout hotfix'i sonrası ikinci canlı B031 koşusunda, Ollama'nın
`/api/generate` uç noktası her istekte `HTTP 500` döndürdü. Kök neden,
Ollama'nın çıkarım (inference) için başlattığı alt süreç olan
`llama-server`'ın bir Windows *access violation* (bellek erişim ihlali,
`STATUS_ACCESS_VIOLATION`) ile çökmesi.

**Tam hata gövdesi (manuel `curl` ile yakalandı):**
```
{"error":"llama-server process has terminated: exit status 0xc0000005: The instruction at 0xp referenced memory at 0xp. The memory could not be s."}
```

**Diagnostik komut çıktıları (kısa):**

| Komut | Sonuç |
|---|---|
| `ollama --version` | `ollama version is 0.32.9` |
| `ollama ps` | Boş (çalışan model/süreç yok — çöküş sonrası kalıcı bir model instance kalmıyor) |
| `ollama list` | `llama3:latest` (4.7GB), `qwen2.5:3b-instruct` (1.9GB, bu tanı için ayrıca çekildi) |
| `curl .../api/tags` | `HTTP 200`, servis kendisi (API katmanı) ayakta ve sağlıklı |
| `curl .../api/generate` (`llama3`) | `HTTP 500`, `0xc0000005` |
| `curl .../api/generate` (`qwen2.5:3b-instruct`, çok daha küçük model) | **Aynı `HTTP 500`, aynı `0xc0000005`** |

**Değerlendirme:** Çöküş, `llama3` (8B) ile sınırlı değil — çok daha küçük
`qwen2.5:3b-instruct` (1.9GB) ile de **birebir aynı hata** tekrarlandı. Bu,
sorunun belirli bir modelin boyutu/kaynak ihtiyacıyla değil, bu makinedeki
`llama-server` binary'sinin kendisiyle (muhtemelen CPU komut seti uyumsuzluğu,
bozuk/eksik bir bağımlılık ya da bu Ollama sürümüne özgü bir Windows hatası)
ilgili olduğunu gösteriyor. Sistem RAM'i bol (66.9GB toplam, 23GB boş) —
kaynak yetersizliği değil. API katmanı (`/api/tags`) sağlıklı yanıt veriyor;
yalnızca gerçek çıkarım gerektiren `/api/generate` çağrıları çöküyor.

**Sonuç: Ollama çalışma zamanı bu makinede kararlı değil (runtime unstable).**
B031 için canlı ölçüm bu haliyle güvenilir şekilde tamamlanamaz.

**Karar:** B031 **Partial / FAILED_THRESHOLDS** olarak kalıyor — sayı
uydurulmadı, PASS iddia edilmedi. Kapanış koşulu netleşene kadar (bkz.
`docs/BACKLOG.md` yeni madde) canlı Ollama koşuları güvenilir kabul
edilmeyecek.

### B036 Runtime Stabilizasyon Kapısı — 2026-08-13T00:38:34Z

Tüm ham çıktılar: `reports/runtime_diag_20260813T003834Z/`
(`env_summary.md`, `stability_steps.md`, `batch_50_summary.md`,
`batch_50_raw.jsonl`, ham komut çıktıları).

**4 sıralı, minimal-riskli deney çalıştırıldı** (hepsi `qwen2.5:3b-instruct`
veya `llama3:latest` ile, tek çağrılık):

| Adım | Değişken | HTTP | `0xc0000005` |
|---|---|---|---|
| A) Baseline | mevcut durum | 500 | VAR |
| B) CPU izolasyon | `OLLAMA_NUM_GPU=0` + servis yeniden başlatma | 500 | VAR |
| C) Temiz model | `rm` + `pull` (sha256 doğrulandı) | 500 | VAR |
| D) Farklı model | `llama3:latest` (8B) | 500 | VAR |

**4/4 aynı imzayla çöktü — tek bir tekli çağrı bile başarılı olmadı.**
Not: B) deneyinde `OLLAMA_NUM_GPU=0`'a rağmen sunucu logu hâlâ
`Vulkan0 : Quadro RTX 3000` cihazını listeliyordu — bu ayar Vulkan/GPU
algılamasını tam kapatmamış olabilir, bu yüzden GPU/Vulkan sürücü
etkileşimi kesin olarak ekarte edilemedi (bkz. `stability_steps.md`).

**50 çağrılık hard gate: ÇALIŞTIRILMADI.** Görev kuralı gereği ("herhangi
bir tekli test başarılı olursa batch testi çalıştır") — hiçbir tekli test
başarılı olmadığından bu koşul hiç sağlanmadı; 50 çağrıyı boşuna
tekrarlamak yerine gate doğrudan FAIL olarak değerlendirildi.

**Ollama runtime stabilizasyon kapısı (50/50) geçilemedi; HTTP 500 ve/veya
0xc0000005 çöküşü nedeniyle B031 yeniden değerlendirmesi bloklandı.**

B031 durumu değişmedi: **Partial / FAILED_THRESHOLDS** (canlı yeniden
ölçüm bu koşuda hiç denenmedi — engellendiği için).

### B036 Derin Triage — GPU/Vulkan İzolasyon Matrisi + Sürüm A/B — 2026-08-13

Tüm ham kanıtlar: `reports/runtime_incident_20260813T004855Z/`
(`host_fingerprint.md`, `event_viewer_crash_entries.txt`,
`gpu_isolation_matrix.md`, `version_ab_test.md`,
`OLLAMA_GITHUB_ISSUE_DRAFT.md`, ham log/sonuç dosyaları).

**Host fingerprint (özet):** Windows 11 Pro 10.0.22631; NVIDIA Quadro RTX
3000, **sürücü 442.94 / CUDA 10.2** (Ollama'nın `cuda_v12`/`cuda_v13`
backend'leriyle uyumsuz — çok eski); **Vulkan Instance Version 1.2.131**
(eski); Event Viewer'da 20 adet özdeş `0xc0000005` `Application Error`
kaydı (`llama-server.exe`, Hatalı modül adı: `unknown`).

**GPU/Vulkan izolasyon matrisi (A-E) sonucu:**

| Test | Ortam | Vulkan cihazı başlatıldı mı (log kanıtı) | `/api/generate` | Çöküş |
|---|---|---|---|---|
| A) Baseline | (yok) | EVET (`Vulkan1`/Quadro RTX 3000) | HTTP 500 | EVET |
| B) `OLLAMA_VULKAN=false` | env | HAYIR (`library=cpu`) | HTTP 200 ×4 | HAYIR |
| C) `OLLAMA_LLM_LIBRARY=cpu` | env | HAYIR (`library=cpu`) | HTTP 200 ×1 | HAYIR |
| D) env görünürlüğü | — | Sunucunun kendi `server config` log satırıyla doğrulandı | — | — |
| E) Donanım seviyesi GPU devre dışı | — | **Atlandı** (B/C zaten kesin kanıt sağladı; donanım değişikliği riski/kapsamı gerekçesiz) | — | — |

GPU/Vulkan cihazı log kanıtıyla doğrulanmış şekilde hiç başlatılmadığında
(B, C) çöküş **tamamen** ortadan kalkıyor (5/5 başarılı, 0 çöküş).
`OLLAMA_NUM_GPU=0`'ın aksine (önceki B036 koşusundaki çekince), bu iki
değişken sunucu loglarıyla **kanıtlanmış** şekilde Vulkan cihaz keşfini
tamamen atlıyor.

**Sürüm A/B testi:** Mevcut `0.32.9` 5/5 çöktü; önceki `0.30.0` (gerçekten
kurulup test edildi, ardından `0.32.9`'a geri yüklendi) varsayılan ayarlarla
**çökmedi** — ancak log kanıtı, 0.30.0'ın bu donanımda Vulkan cihazını hiç
seçmediğini (otomatik CPU'ya düştüğünü) gösteriyor; bu, "bug 0.30.0'da
düzeltilmiş" anlamına gelmiyor, yalnızca o sürümün GPU keşif mantığının bu
eski sürücü/Vulkan kombinasyonunda farklı davrandığı anlamına geliyor (bkz.
`version_ab_test.md`).

**Reprodüksiyon scripti:** `scripts/repro_ollama_crash.ps1` — env yazdırır,
`/api/tags` ve tek bir `/api/generate` çağrısı yapar, HTTP/gövde/gecikmeyi
yazdırır, `HTTP 500` veya çöküş imzası bulunursa exit code `1` ile çıkar
(başarıda `0`, sunucuya ulaşılamazsa `2`). Her iki yol da (çöküş / başarı)
bu triage sırasında doğrulandı (bkz. `repro_output.txt`).

**Ollama 0xc0000005 incident triage tamamlandı; GPU/Vulkan izolasyon
matrisi ve sürüm A/B sonuçları raporlandı, B031 runtime stabil olana kadar
bloklu.**

**B036 çıkış kriteri (değişmedi, netleştirildi):** B031 quality gate'i
yeniden koşulmadan önce, **varsayılan (Vulkan etkin) ortamda** 50/50
başarılı `/api/generate` çağrısı, 0 `HTTP 500`, loglarda 0 `0xc0000005`
gerekiyor. Şu an bilinen tek çalışan yapılandırma (`OLLAMA_VULKAN=false` /
`OLLAMA_LLM_LIBRARY=cpu`, CPU-only) bu kriteri karşılamıyor sayılmaz —
çökmediği için 50 çağrılık gate bu modda **denenebilir** — ama CPU-only
gecikmesi (gözlemlenen 7.7s-26.9s/çağrı) B031'in `latency_p95≤2.5s`
eşiğini büyük olasılıkla geçemeyecek. Bu nedenle B036'nın "çözüldü" sayılması
için ya (a) GPU sürücüsü/Vulkan yığını güncellenip Vulkan modunda 50/50
gate'i geçmeli, ya da (b) B031'in gecikme eşiği CPU-only gerçeğine göre ayrı
bir ADR ile yeniden değerlendirilmeli. Her iki karar da bu triage'ın
kapsamı dışında, kullanıcı/ekip kararını bekliyor.

### B036 Upstream Issue — Durum

**Durum: AÇILDI.** Açılış zaman damgası (Europe/Istanbul): 2026-08-13 ~05:02
(+03:00). `ollama/ollama` deposuna, `reports/runtime_incident_20260813T004855Z/ISSUE_READY_PACKAGE.md`
paketindeki başlık/gövde/ek dosyalar kullanılarak manuel olarak issue açıldı
(bu ortamda `gh` CLI/token bulunmadığından otomatik açılamamıştı — kullanıcı
manuel gönderdi).

**Upstream issue submitted: <https://github.com/ollama/ollama/issues/17716> —
B036 takibi bu issue üzerinden sürdürülecek.**

Kısa özet: `llama-server crashes with 0xc0000005 on Windows when using
Vulkan backend on older NVIDIA driver (Quadro RTX 3000, driver 442.94)` —
issue içeriği bu triage'ın kanıt paketiyle (host fingerprint, GPU/Vulkan
izolasyon matrisi, sürüm A/B, repro scripti) birebir tutarlı.

Beklenen takip: maintainer yanıtı ve olası patch/duyuru takibi. Bu
bölüm, upstream'de bir gelişme (yanıt, düzeltme, sürüm notu) olduğunda
güncellenecek. **Bu, B036'nın kendisini kapatmıyor** — B036 hâlâ
`docs/BACKLOG.md`'deki açık çıkış kriterlerine tabi (varsayılan/Vulkan
profilde 50/50 stabil koşu).

### Geçici CPU-Only Workaround

B036 çözülene kadar, çöküşten kaçınmak isteyen manuel/tanı amaçlı koşular
için geçici bir CPU-only profil formalize edildi:
`docs/ops/OLLAMA_WORKAROUND_CPU_ONLY.md` (`OLLAMA_VULKAN=false` veya
`OLLAMA_LLM_LIBRARY=cpu`). **Bu, B031'in resmi kabul kararı için
kullanılmaz** — yalnızca bilgilendirici koşular içindir (bkz. aşağıdaki
"B031 Informational Probe").

### B031 Informational Probe (CPU-only, non-gating) — 2026-08-13T01:19:53Z

CPU-only workaround (`OLLAMA_VULKAN=false`) aktifken `tools/eval_nlu.py`
bilgilendirme amaçlı çalıştırıldı (model: `llama3`, 50 örnek). Tam rapor:
`reports/runtime_incident_20260813T004855Z/nlu_eval_20260813T011953Z_cpu_only_informational.md`
(bilerek `reports/nlu_eval_20260813.md`'nin **dışında** ayrı bir dosyaya
kaydedildi — o dosya, 2026-08-13T00:13:05Z çöküş-keşif koşusunun kanıtı
olarak yukarıdaki tabloda referans gösteriliyor ve üzerine yazılmadı).

| Kriter | Değer | Eşik | Sonuç |
|---|---|---|---|
| intent_accuracy | 74.0% | ≥ 90.0% | FAIL |
| entity_match_rate | 41.7% | ≥ 85.0% | FAIL |
| parse_error_rate | 0.0% | ≤ 2.0% | PASS |
| fallback_rate | 0.0% | ≤ 5.0% | PASS |
| latency_p95 | 20.59s | ≤ 2.50s | FAIL |

**Sonuç: INFORMATIONAL_ONLY (non-gating).** Önemli gözlem: `fallback_rate`
ve `parse_error_rate` `%0.0` — 50/50 çağrı **çökmeden** tamamlandı (CPU-only
workaround'un stabilite bulgusuyla tutarlı). Ama `latency_p95=20.59s`,
eşiğin (`2.50s`) çok üzerinde — beklenen CPU-only ödünleşimi. **Bu sonuç
resmi B031 kararını DEĞİŞTİRMEZ; B031 resmi durumu BLOCKED_BY_RUNTIME
olarak kalır.**

### Checkpoint — Upstream Issue Sonrası — 2026-08-13 ~05:07 (+03:00)

- Upstream issue **#17716** açıldı ve bağlandı:
  <https://github.com/ollama/ollama/issues/17716> (bkz. yukarıdaki "B036
  Upstream Issue — Durum").
- **B036: IN_PROGRESS** (değişmedi — issue açılması B036'yı çözmüyor).
- **B031: BLOCKED_BY_RUNTIME** (değişmedi).
- Sonraki aksiyon: kontrollü bir runtime stabilizasyon deney partisi
  (Post-upstream Experiment Batch #1) — bkz. aşağıdaki bölüm.

### Post-upstream Experiment Batch #1 — 2026-08-13T02:33Z

Kontrollü, 5 senaryolu (A-E), senaryo başına 20 çağrılık (toplam **n=100**)
bir stabilizasyon deney partisi çalıştırıldı — amaç, önceki küçük
örneklemli (n=5) bulguyu istatistiksel olarak daha güçlü bir örneklemle
doğrulamak ve upstream issue #17716 için ek kanıt üretmek. Artifact klasörü:
`reports/runtime_incident_20260813T004855Z/exp_01_post_upstream/`
(`test_matrix.md`, `results.jsonl`, `summary.md`, `env_snapshot.txt`,
`raw_logs/`). Script: `scripts/repro_b036_batch.ps1`.

| Senaryo | Açıklama | n | crash_count | success_rate | p50 | p95 |
|---|---|---|---|---|---|---|
| A | Vulkan ON, baseline | 20 | 20 | %0.0 | 8.04s | 9.71s |
| B | Vulkan OFF (CPU-only) | 20 | 0 | %100.0 | 9.58s | 14.48s |
| C | Vulkan ON + `OLLAMA_NUM_PARALLEL=1` | 20 | 20 | %0.0 | 8.17s | 9.00s |
| D | Vulkan ON + `num_ctx=256` | 20 | 20 | %0.0 | 8.33s | 9.22s |
| E | Vulkan ON + her çağrı öncesi temiz restart | 20 | 20 | %0.0 | 10.88s | 13.29s |

**Toplam: 100 çağrı, 80 çöküş, 20 başarı — başarıların tamamı Senaryo
B'den (CPU-only).** A/C/D/E'de **20/20 çöküş, istisnasız** — önceki
küçük örneklemli bulgu (5/5 çöküş) tam olarak doğrulandı; çöküş aralıklı
değil, deterministik. Eşzamanlılığı azaltmak (C), context'i küçültmek (D)
ve her çağrıda temiz restart yapmak (E), hiçbiri çöküşü önlemedi — yalnızca
Vulkan cihazını tamamen devre dışı bırakmak (B) önlüyor.

**B036 kararı: IN_PROGRESS (değişmedi).** Hiçbir Vulkan-etkin senaryo
crash-free çıkmadığından `READY_FOR_RETEST`'e geçilmedi; B036 **RESOLVED
yapılmadı**.

**B031 gate:** Atlandı — neden: `blocked_by_runtime_instability` (crash-free/
reproducible bir Vulkan-etkin senaryo bulunamadı). B031 resmi durumu
değişmeden **BLOCKED_BY_RUNTIME** kalıyor.

### Upstream Follow-up Prepared (#17716) — 2026-08-13 ~05:37 (+03:00)

Post-upstream Experiment Batch #1'in (n=100) sonuçlarını issue
<https://github.com/ollama/ollama/issues/17716>'a ek yorum olarak iletmek
üzere bir taslak hazırlandı:
`reports/runtime_incident_20260813T004855Z/exp_01_post_upstream/upstream_comment_draft_17716.md`.

**Gönderim durumu: MANUAL_POST_REQUIRED.** Bu ortamda `gh` CLI kurulu
değil ve `GITHUB_TOKEN`/`GH_TOKEN` tanımlı değil — otomatik gönderim
yapılmadı, sahte "gönderildi" iddiası yok. Kullanıcı taslağı manuel
olarak issue'ya yorum ekleyerek gönderebilir.

### B036 Upstream Watch Mode Enabled — 2026-08-13 ~05:47 (+03:00)

Yukarıdaki takip yorumu kullanıcı tarafından manuel olarak issue #17716'ya
eklendi (permalink:
<https://github.com/ollama/ollama/issues/17716#issuecomment-5275320830>).
Bu noktadan itibaren B036, **watch mode**'a geçti — protokol:
`docs/ops/B036_UPSTREAM_WATCH_PROTOCOL.md` (kontrol sıklığı: günde 2×,
09:30/17:30 Europe/Istanbul; kontrol geçmişi:
`reports/runtime_incident_20260813T004855Z/upstream_watch_log.md`).

**Maintainer'dan bir yanıt (tanı isteği, patch/commit veya sürüm ipucu)
gelene kadar geniş kapsamlı yeni bir deney partisi çalıştırılmayacak.**
Bir tetikleyici geldiğinde hangi ölçekte doğrulama yapılacağı önceden
tanımlandı:
`reports/runtime_incident_20260813T004855Z/validation_on_trigger.md`
(Trigger A: 10 çağrı hedefli, Trigger B: 40 çağrı 3-senaryolu, Trigger C:
100 çağrı tam matris).

Canonical audit reference added for upstream comment: issue comment ID 5275320830.

### B036 Watch Automation

Manuel kontrol turlarına ek olarak, issue #17716'yı kontrol edip
aktiviteyi sınıflandıran ve `upstream_watch_log.md`'ye satır ekleyen bir
otomasyon eklendi:

```bash
python scripts/watch_b036_upstream.py
powershell -File scripts\run_watch_b036.ps1
```

- `scripts/watch_b036_upstream.py`: GitHub API'sinden issue + yorumları
  okur, bizim postaladığımız takip yorumundan (`comment id 5275320830`)
  sonra gelen ve başka bir kullanıcıdan olan en yeni yorumu bulur, içeriğini
  anahtar-kelime tabanlı olarak sınıflandırır (`NONE`,
  `DIAGNOSTIC_REQUEST`, `PATCH_REFERENCE`, `NEW_RELEASE_HINT`), ve
  `upstream_watch_log.md`'ye bir satır ekler. Ağ erişimi başarısız olursa
  sahte bir sonuç uydurmaz — `CHECK_FAILED_NETWORK` yazdırır.
- `scripts/run_watch_b036.ps1`: Python script'ini çalıştırır, çıktıyı
  `reports/runtime_incident_20260813T004855Z/watch_runs/watch_<timestamp>.log`
  dosyasına kaydeder. Yalnızca gerçek sert hatalarda (Python
  çalıştırılamadı vb.) sıfır olmayan bir çıkış kodu döner —
  `CHECK_FAILED_NETWORK` veya `NONE` sonucu bir hata sayılmaz.
- **Tetikleyici eşlemesi:** sınıflandırma sonucu `DIAGNOSTIC_REQUEST` /
  `PATCH_REFERENCE` / `NEW_RELEASE_HINT` çıkarsa, hangi ölçekte doğrulama
  yapılacağı `reports/runtime_incident_20260813T004855Z/validation_on_trigger.md`'de
  tanımlı (Trigger A/B/C).
- **Önemli: bu script hiçbir runtime deneyi (ollama serve, /api/generate
  vb.) çalıştırmaz** — yalnızca upstream issue'yu okur ve sınıflandırır.
  Bir tetikleyici tespit edilirse, gerçek doğrulama koşusu hâlâ
  `validation_on_trigger.md`'ye göre **manuel** olarak başlatılır.

## Model Gateway (B036 resilience layer)

> Tam operasyonel kılavuz: `docs/ops/MODEL_FALLBACK_RUNBOOK.md` (startup
> checklist, healthcheck komutları, incident aksiyonları, rollback).

Ollama'nın Windows/Vulkan çalışma zamanı kararsızlığına (B036) karşı,
`services/model-gateway/src/model_gateway/` altında sıralı, açık-loglu
fallback uygulayan bir katman eklendi. **Bu B036'yı çözmez** — yalnızca
tek bir sağlayıcının çökmesiyle sistemin tamamen durmasını önler ve her
geçişi denetlenebilir kılar.

- **Sağlayıcı sırası varsayılanı değişmedi:** `MODEL_PROVIDER_ORDER`
  hâlâ yalnızca `ollama` — çok saglayıcılı fallback etkinleştirilmediği
  sürece kod yolu aynı.
- **Sağlayıcı sırası:** `ollama` (birincil, açık) → `local_alt` (ikincil,
  varsayılan kapalı) → `remote` (üçüncül, varsayılan kapalı **ve**
  politika kapılı — `policies/risk/tool_risk_policy.yaml` `remote_model_policy`).
- **CPU-only doğrulama kapısı (YENİ, davranışı gerçekten değiştirir):**
  `OLLAMA_CPU_VERIFY_ENABLED=true` + `OLLAMA_CPU_VERIFY_STRICT=true`
  varsayılan olarak açık — operatör bir marker dosyası oluşturmadıkça
  (bkz. `docs/ops/MODEL_FALLBACK_RUNBOOK.md` operatör kontrol listesi),
  Ollama STRICT modda birincil olarak **seçilmez**, sistem doğrudan
  null-intent'e düşer (Ollama gerçekten sağlıklı çalışıyor olsa bile).
  Bu **bilinçli, yüksek sesle dokümante edilmiş** bir tasarım kararıdır
  ("sessiz davranış değişikliği yok" ilkesi gereği) — bkz.
  `docs/ops/MODEL_FALLBACK_RUNBOOK.md` "ÖNEMLİ DAVRANIŞ SONUCU" uyarısı.
  Kanıt kaynakları (`runtime_verify.py`): operatör marker dosyası
  (pozitif), Windows Event Viewer'da bilinen B036 çöküş imzası (negatif),
  `/api/ps` VRAM kullanımı (zayıf negatif) — hiçbiri kesinlik iddia etmez.
- **`OLLAMA_VULKAN=false` zorlaması:** `OllamaProvider`, bu değeri kendi
  sürecinin ortamına yazar — ancak `ollama serve` bu kod tarafından
  başlatılmadığından (harici süreç), zaten çalışan bir sunucuyu
  **etkilemez**. Gerçek zorlama hâlâ operatörün sunucuyu doğru env ile
  başlatmasına bağlı (bkz. `docs/ops/OLLAMA_WORKAROUND_CPU_ONLY.md`).
- **Sessiz fallback yok:** her geçiş (`FALLBACK`/`SKIPPED`/`SUCCESS`)
  hem log'a hem `data/audit/audit.log.jsonl`'e (`task=MODEL_GATEWAY_GENERATE`,
  ayrıca CPU-verify kararları için `OLLAMA_CPU_PREFLIGHT_CHECKED` /
  `OLLAMA_PRIMARY_RESTRICTED`, hepsi ortak bir `trace_id` ile
  korelasyonlanabilir) yapısal olarak yazılır — `reason_code` alanı:
  `PRIMARY_UNHEALTHY`, `TIMEOUT`, `RUNTIME_CRASH`, `POLICY_BLOCK`,
  `DISABLED`, `CIRCUIT_OPEN`, `PRIMARY_RESTRICTED_CPU_UNVERIFIED`,
  `FALLBACK_EXHAUSTED`.
- **Entegrasyon:** `services/tr-en-bridge/src/ollama_nlu.py::classify()`,
  varsayılan (test-enjeksiyonu olmayan) durumda artık
  `model_gateway.compat.RouterBackedClient` üzerinden router'a bağlı —
  `classify()`'in dış sözleşmesi (`intent`/`entities`/`confidence`/`raw`)
  ve mevcut `client=` test enjeksiyon noktası **değişmedi**. Tüm
  saglayıcılar tükendiğinde tek bir debug log satırı (`trace_id` +
  `FALLBACK_EXHAUSTED`) yazılır.
- **Rollback:** `docs/ops/MODEL_FALLBACK_RUNBOOK.md` "Geri alma" bölümüne
  bakın — config ile ollama-only CPU moduna dönüş (CPU-verify kapısını
  `OLLAMA_CPU_VERIFY_ENABLED=false` ile kapatmak dahil), kod revert'i
  gerekmez.
- **Gözlemlenebilirlik (SLI/SLO/alert/ops otomasyonu):**
  `services/model-gateway/src/model_gateway/metrics.py` (bellek-içi
  registry, `METRICS_SINK=jsonl_append` varsayılanıyla ayrıca paylaşılan
  bir JSONL dosyasına yazar — bkz. `metrics_sink.py`/`metrics_aggregate.py`
  ve aşağıdaki "Canlı `/metrics`" notu), `docs/ops/SLO_MODEL_GATEWAY.md`,
  `docs/ops/ALERT_PLAYBOOK_MODEL_GATEWAY.md`,
  `scripts/ops/daily_gateway_smoke.ps1` (günlük sağlık kontrolü, exit
  code 0/1/2), `scripts/ops/package_gateway_incident.ps1` (olay kanıt
  paketleyici, secret'lar maskeli). Tam kılavuz:
  `docs/ops/MODEL_FALLBACK_RUNBOOK.md` "Gözlemlenebilirlik ve Ops
  Otomasyonu".
- **Canlı `/metrics` + izleme yığını:** `scripts/ops/serve_metrics.py`
  (gerçek, test edilmiş `GET /metrics` endpoint'i — stdlib
  `http.server`, harici bağımlılık yok). `METRICS_SINK=jsonl_append`
  (varsayılan) ile **cross-process görünürlük**: tüm süreçlerin
  (kısa ömürlü `classify()` çağrıları dahil) metrikleri paylaşılan bir
  JSONL dosyası üzerinden tek scrape'te birleşir — 2 gerçek ayrı OS
  süreci ile elle doğrulandı, ayrıntı ve ödünleşimler:
  `docs/ops/MONITORING_STACK_RUNBOOK.md` "Bilinen ödünleşimler".
  Aggregator sert hatada `/metrics` 503 döner (`AggregationError`).
  **Performans:** `/metrics` artık her scrape'te tüm JSONL geçmişini
  yeniden TARAMAZ — `IncrementalAggregator` (yalnızca yeni byte'ları
  okur, dosya kimliği rotasyona karşı şeffaf) + `CachedMetricsRenderer`
  (`METRICS_AGG_CACHE_TTL_SEC` içindeki tekrar istekleri önbellekten
  yanıtlar) kullanır — çıktı tam-yeniden-taramayla BİREBİR AYNIDIR,
  bu makinede ölçülen p50 gecikme ~5.6x azaldı (önbellek isabetinde
  ~pratikte sıfır). Ayar rehberi + gerçek benchmark sonuçları:
  `docs/ops/MONITORING_STACK_RUNBOOK.md` "Performans ayar rehberi".
  `infra/monitoring/prometheus/prometheus.yml` +
  `infra/monitoring/alertmanager/alertmanager.yml` (geçerli config,
  Aşama 1/observe-only, bu makinede Prometheus/Alertmanager kurulu
  DEĞİL — hiçbiri gerçekten okumuyor),
  `scripts/ops/emit_synthetic_gateway_signals.py` +
  `scripts/ops/verify_alert_pipeline.ps1` (E2E doğrulama, bu makinede
  dürüstçe exit code 1/kısmi döner). Tam kılavuz + kademeli rollout
  aşamaları + eski (manuel) Gate A-D: `docs/ops/MONITORING_STACK_RUNBOOK.md`.
- **Go-live paketi (gate otomasyonu, kalibrasyon, sign-off, rollback):**
  `scripts/ops/run_observability_gates.ps1` (otomatik Gate A-E — metrics
  availability, scrape success, 4 sentetik alert modunun görünürlüğü,
  Alertmanager alma yolu, classify regresyon smoke — `gate_report.md` +
  `gate_results.json` üretir, exit code 0/1/2). **2026-08-13 güncel
  gerçek sonuç: PASS (5/5 gate)** — B037 düzeltmesi (`tools/cli-runner/src/runner.py`,
  PowerShell PATH'inde `echo` bulunamaması artık saf-Python fallback ile
  çözüldü, classify/fallback sözleşmesiyle ilgisiz bir ortam sorunuydu)
  ile gerçek Prometheus v3.13.2/Alertmanager v0.33.1 ile uçtan uca
  doğrulama (sentetik sinyal → gerçek firing → Alertmanager gerçekten
  aldı, observe-only korunarak) sonrasında. Kanıt: `reports/go_live_gates_20260813T073109Z/`,
  `reports/gate_d_real_validation_20260813T072946Z/` (`git add -f` ile
  arşivlendi). Ayrıntı: `docs/ops/MONITORING_STACK_RUNBOOK.md` "Go-live
  gate otomasyonu". `scripts/ops/calibrate_alert_thresholds.py`
  (son N saatlik gerçek veriden WARN/CRIT önerisi — bu makinede
  `INSUFFICIENT_DATA`, mevcut varsayılanlar korunuyor).
  `scripts/ops/build_observability_signoff.py` (git SHA + tam test
  özeti + en son gate sonucu + GO/CONDITIONAL-GO/NO-GO kararı,
  `SIGNOFF.md`/`SIGNOFF.json`). `scripts/ops/rollback_observability.ps1`
  (varsayılan **dry-run**, `-Apply` ile gerçek uygulama — yalnızca
  Alertmanager route receiver'larını Aşama 1'e döndürür, `/metrics`'i
  ASLA kapatmaz, audit/metrics verisine ASLA dokunmaz; hem dry-run hem
  apply, hem "zaten güvenli" hem "gerçek escalate durumunu düzeltme"
  senaryolarında elle doğrulandı). Kanonik "Go/No-Go checklist":
  `docs/ops/MONITORING_STACK_RUNBOOK.md`.

## Onay Gerektiren Aksiyonlar (Faz 4+ ile aktif olacak)

- Risk seviyesi `high` veya `irreversible` olan her aksiyon, kullanıcı onayı
  alınmadan yürütülmez.
- Onay/red kararı `data/audit` altına append-only olarak yazılır.
- Onay akışı henüz (Faz 0) yalnızca stub/simülasyon seviyesindedir — gerçek kullanıcı
  arayüzü Faz 4'te eklenecek.

## Finans Güvenlik Operasyonları

> Kapsam ve işlem seviyeleri (L0–L3) için bkz. `docs/MASTER_ROADMAP.md` §5;
> policy kaynağı `policies/risk/*.yaml` (bkz. PLAN.md T18).

### API Key Güvenliği

- Finance Engine'e tanımlanan her borsa API key'i **withdraw (çekme) yetkisi
  kapalı** olmak zorundadır; bağlantı öncesi izin doğrulaması yapılır (bkz.
  BACKLOG "API permission validator", B025).
- API key/secret **yalnızca `.env` veya vault** üzerinden sağlanır, hiçbir zaman
  `config/` altındaki JSON dosyalarına veya git'e yazılmaz (bkz. ADR-010).
- Key rotasyonu/iptali gerektiğinde ilgili borsa panelinden yapılır; sistem
  tarafında yalnızca yeni key `.env`'e güncellenir, eski işlemler audit log'da
  değişmeden kalır.

### Onay Seviyeleri

| Seviye | Onay | Operasyonel not |
|---|---|---|
| L0 — Bilgi | Yok | Salt okunur sorgular; risk yok |
| L1 — Simülasyon | Yok (loglanır) | Borsaya emir gitmez, mock sonuç üretilir |
| L2 — Küçük işlem | Tek onay | Onaylayan kullanıcı `data/audit`'e kaydedilir |
| L3 — Büyük işlem | Çift onay + bekleme süresi | Bekleme süresi dolmadan yürütme yok; iki farklı onay adımı zorunlu |

- Onay/red kararları CLI simülasyonu üzerinden verilir (bkz. PLAN.md T20);
  gerçek kullanıcı arayüzü ileri fazda eklenecek.
- Onay bekleyen bir işlem, süre dolmadan veya reddedilmeden **kendiliğinden
  yürütülmez**.

### Acil Durdurma (Kill Switch)

- Şüpheli/istenmeyen işlem tespit edildiğinde ilk adım: Finance Engine'in
  yürütme yetkisini durdurmak (ör. config/flag üzerinden `execution_enabled:
  false` — bu görev henüz kod olarak yazılmadı, bkz. BACKLOG "Circuit breaker").
- Kill switch aktifken: L0/L1 (bilgi/simülasyon) çalışmaya devam edebilir,
  L2/L3 (gerçek işlem) tamamen bloklanır.
- Kill switch aktivasyonu/deaktivasyonu da audit log'a yazılır (kim, ne zaman,
  neden).

### Hatalı İşlemde Geri Alma Prosedürü

- **Önemli sınır:** Borsaya gerçekten iletilmiş bir emir (fiyat/miktar hatası
  dahil) genel olarak **geri alınamaz** — yalnızca ters işlemle (karşı pozisyon
  açma) etkisi azaltılabilir; bu bir "geri alma" değil, yeni bir işlemdir ve
  aynı onay seviyesine tabidir.
- Geri alma/telafi mümkün olan senaryolar:
  - İşlem henüz **onay bekliyor** (L2/L3, süre dolmadı) → reject ile iptal
    edilir, borsaya hiç gitmez.
  - İşlem **L1 (simülasyon)** seviyesinde → zaten gerçek değildir, iptal =
    mock kaydın "cancelled" olarak işaretlenmesi.
- Her durumda ilk adım: kill switch ile yeni işlemleri durdur, ardından
  mevcut pozisyonu manuel olarak (kullanıcı onayıyla) değerlendir.
- Post-mortem: Olay `data/audit` kayıtlarına referansla `docs/DECISIONS.md`'ye
  gerekirse bir ADR olarak (politika değişikliği varsa) işlenir.

## Sorun Giderme (Genel)

| Belirti | Olası Neden | İlk Bakılacak Yer |
|---|---|---|
| Wake-alias tanınmıyor | Config yüklenmemiş / eski | `config/assistant.identity.json`, config loader logu |
| TR yanıt anlamsız/bozuk | Bridge çeviri hatası | `services/tr-en-bridge` logu, ADR-001 (Ollama) bağlantısı |
| Tool-call hiç çalışmıyor | Whitelist dışı komut | `tools/cli-runner` whitelist tanımı |
| Aksiyon sessizce yürütülmüyor | Onay bekliyor (beklenen davranış) | `data/audit` log kaydı |

## İlgili Dokümanlar

- Vizyon/mimari: `docs/MASTER_ROADMAP.md`
- Aktif sprint: `docs/PLAN.md`
- Ertelenen/gelecek işler: `docs/BACKLOG.md`
- Mimari kararlar: `docs/DECISIONS.md`
