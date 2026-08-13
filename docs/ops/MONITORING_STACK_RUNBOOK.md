# Monitoring Stack Runbook — Model Gateway

> Bu belge, `docs/ops/SLO_MODEL_GATEWAY.md` ve
> `docs/ops/ALERT_PLAYBOOK_MODEL_GATEWAY.md`'nin dayandığı gerçek
> altyapıyı (`/metrics` endpoint'i, Prometheus scrape, Alertmanager
> routing) kurma/işletme/geri alma kılavuzudur.

## Bu makinenin gerçek durumu (dürüstçe, keşif ile doğrulandı)

Bu görev başlamadan önce kontrol edildi:

- **Docker: KURULU DEĞİL.**
- **Prometheus/Alertmanager binary: sistem PATH'inde kalıcı olarak KURULU
  DEĞİL** — ama **2026-08-13'te GERÇEK Prometheus v3.13.2 + Alertmanager
  v0.33.1 binary'leri indirilip yerel olarak (repo DIŞINDA,
  `C:\Temp\monitoring_bin\`) çalıştırılarak TAM E2E doğrulama yapıldı**
  (bkz. aşağıdaki "Gate D gerçek doğrulama" bölümü). Bu, kalıcı bir kurulum
  DEĞİLDİR — yalnızca doğrulama süresince ayakta tutuldu, sonra durduruldu.
  Aynı adımlar herhangi bir operatör tarafından tekrarlanabilir (tarif
  aşağıda).
- **FastAPI/Flask/uvicorn: KURULU DEĞİL** (`/metrics` hâlâ stdlib
  `http.server` kullanıyor, bu değişmedi).
- Repoda hiçbir `docker-compose*` dosyası **yoktu** (bu görevle eklenmedi —
  görev talimatı "repo'da compose varsa patch'le, yoksa PowerShell
  script'i sağla" diyordu; repo'da compose olmadığı için PowerShell
  script yolu izlendi).
- Bu projede **kalıcı bir servis süreci yok** — `classify()` çağrıları
  kısa ömürlü CLI/test çalıştırmalarından yapılıyor.

Bu, aşağıdaki her bileşenin "gerçek ve çalışır durumda test edildi" ile
"gerçek, geçerli config ama hiçbir canlı süreç tarafından henüz
okunmuyor" arasında net bir ayrım gerektirdiği anlamına gelir — bu belge
bu ayrımı her bölümde açıkça belirtir.

## Bileşenler ve gerçek durumları

| Bileşen | Dosya | Durum |
|---|---|---|
| `/metrics` HTTP endpoint | `scripts/ops/serve_metrics.py` | **Gerçek, elle test edildi** — başlatıldı, `curl`landı, 200/404/503 davranışları doğrulandı; `METRICS_SINK=jsonl_append` (varsayılan) ile gerçek AYRI OS süreçlerinden gelen metrikler tek scrape'te birleştirildi |
| Cross-process metrik sink'i | `model_gateway/metrics_sink.py`, `model_gateway/metrics_aggregate.py` | **Gerçek, elle test edildi** — 2 bağımsız yazıcı süreç + `serve_metrics.py` üçüncü süreç, `curl` ile doğrulandı |
| Prometheus scrape config | `infra/monitoring/prometheus/prometheus.yml` | Geçerli YAML, ama hiçbir Prometheus süreci okumuyor |
| Alertmanager routing | `infra/monitoring/alertmanager/alertmanager.yml` | Geçerli YAML, ASAMA 1 (observe-only), hiçbir Alertmanager süreci okumuyor |
| Sentetik sinyal üreticisi | `scripts/ops/emit_synthetic_gateway_signals.py` | **Gerçek, elle test edildi** — hem yerel registry'ye hem HTTP üzerinden çalışan bir sürece enjeksiyon doğrulandı |
| E2E doğrulama | `scripts/ops/verify_alert_pipeline.ps1` | **Gerçek, elle çalıştırıldı** — bu makinede exit code **1** (kısmi) üretti, çünkü Prometheus/Alertmanager yok. **0 fabrike edilmedi.** |
| Go-live gate otomasyonu (A-E) | `scripts/ops/run_observability_gates.ps1` + `scripts/ops/observability_gates_core.py` | **Gerçek, elle çalıştırıldı** — bu makinede exit code **2** (FAIL) üretti: A/B/C PASS, D SKIPPED (Alertmanager yok), E FAIL (bkz. "Go/No-Go checklist" altındaki not). **0 fabrike edilmedi.** |
| Alert eşik kalibrasyonu | `scripts/ops/calibrate_alert_thresholds.py` | **Gerçek, elle çalıştırıldı** — bu makinede `INSUFFICIENT_DATA` (gerçek trafik yok), mevcut varsayılanlar korundu |
| Sign-off paketi | `scripts/ops/build_observability_signoff.py` | **Gerçek, elle çalıştırıldı** — git SHA + gerçek tam test suite sonucu + gate sonucu (varsa) toplanıp GO/NO-GO kararı üretiliyor |
| Rollback drill | `scripts/ops/rollback_observability.ps1` | **Gerçek, elle çalıştırıldı** — hem dry-run hem apply modunda, hem "zaten güvenli" hem "gerçek değişiklik gerekiyor" senaryolarında test edildi (bkz. "Go/No-Go checklist") |

## ÇÖZÜLDÜ (büyük ölçüde): süreç-içi metrik izolasyonu → cross-process JSONL sink

> **Güncelleme:** Bu bölüm önceden "KRİTİK mimari sınırlama" başlığı
> altındaydı. `METRICS_SINK=jsonl_append` (yeni varsayılan,
> `model_gateway/metrics_sink.py` + `model_gateway/metrics_aggregate.py`)
> ile bu sınırlama **kapatıldı** — aşağıda eski/yeni durum ve kalan
> ödünleşimler açıkça ayrılıyor.

**Eski davranış (hâlâ `METRICS_SINK=in_memory` ile opt-in olarak
mevcut):** `model_gateway.metrics.get_metrics()` **süreç-içi bir
singleton'dur.** `scripts/ops/serve_metrics.py`, ayrı bir süreç olarak
çalıştığından, kısa ömürlü **ayrı** süreçlerden yapılan `classify()`
çağrılarının metriklerini **hiç göremiyordu.**

**Yeni davranış (varsayılan):** her süreç (`classify()` çağrısı yapan
kısa ömürlü CLI/test çalıştırması dahil), `MetricsRegistry` üzerinden
yaptığı her `inc_counter`/`observe_histogram`/`set_gauge` çağrısını, EK
OLARAK, paylaşılan bir JSONL dosyasına (`METRICS_JSONL_PATH`, varsayılan
`./data/metrics/model_gateway_metrics.jsonl`) append eder
(`JsonlAppendSink`, kilit + rotasyon + retention + sınırlı-kardinalite
korumalı). `serve_metrics.py`, `/metrics` her çağrıldığında bu dosyayı
`metrics_aggregate.aggregate()` ile okuyup TEK bir Prometheus
görüntüsüne birleştirir — böylece **tüm süreçlerin** katkısı tek bir
scrape'te görünür.

Bu, **gerçek ayrı OS süreçleriyle elle doğrulandı** (2 bağımsız yazıcı
süreç + `serve_metrics.py` ayrı üçüncü/dördüncü bir süreç olarak
çalıştırıldı, `curl /metrics` ile gerçek HTTP isteği yapıldı — sonuç:
her iki yazıcının toplam katkısı tek bir counter değerinde doğru
şekilde toplandı).

`serve_metrics.py --enable-debug-injection` + `POST /debug/inject_synthetic`
yolu (E2E test senaryosu için, önceki görev serisinden) hâlâ mevcut ve
çalışıyor — artık debug-injection AYRICA JSONL sink'e de yazıyor (aynı
registry üzerinden), yani hem "aynı sürece HTTP enjeksiyonu" hem de
"paylaşılan dosya" yolu tutarlı.

### Bilinen ödünleşimler (Known trade-offs)

- **Eventual consistency penceresi:** Bir sürecin `write()` çağrısı ile
  o olayın `/metrics` çıktısında görünmesi arasında gerçek-zamanlı bir
  push YOKTUR — olay yalnızca dosyaya yazılmıştır; bir SONRAKI `/metrics`
  GET'i o dosyayı yeniden okuyup birleştirdiğinde görünür hale gelir.
  Pratikte bu pencere tipik olarak alt-saniye mertebesindedir (dosya
  append + sonraki scrape arası), ama garanti edilen bir üst sınır
  yoktur.
- **Scrape gecikmesi (GÜNCELLENDİ — artık bir "bilinen ödünleşim"
  DEĞİL, çözüldü):** Önceden her `/metrics` isteği, ana JSONL dosyasını
  (+ pencere içindeki tüm rotasyon dosyalarını) **baştan sona yeniden
  okuyup birleştiriyordu**. `IncrementalAggregator` (bkz. aşağıdaki
  "Performans ayar rehberi") artık yalnızca SON okumadan beri eklenen
  byte'ları okur (dosya kimliği rename/rotasyona karşı şeffaftır) ve
  `METRICS_AGG_CACHE_TTL_SEC` içindeki tekrar istekleri onbellekten
  yanıtlanır — çıktı tam-yeniden-taramayla BİREBİR AYNIDIR (testle
  kanıtlandı), yalnızca maliyeti değişti. Kalan gerçek ödünleşim: state
  dosyası (`METRICS_AGG_STATE_PATH`) yalnızca gerçekten bir şey
  değiştiğinde (yeni satır/pencere dışına çıkan olay/kardinalite
  ataması) diske yazılır — bu, "steady-state" (yeni yazma yokken tekrar
  scrape) senaryosunda gereksiz tam tampon serileştirmesini önler.
- **`write_failures`/`events_dropped` yalnızca RAPORLAYAN sürece
  özeldir:** `metrics_sink_write_failures_total` ve
  `metrics_events_dropped_total`, `serve_metrics.py`'nin KENDİ
  registry'sinin sink'inden okunur — başarısız bir yazma, tanım gereği
  JSONL dosyasına hiç yazılamadığından, BAŞKA bir sürecin yazma
  hatalarını bu sayaçlar üzerinden gözlemlemek mümkün DEĞİLDİR. Bu,
  best-effort bir kendi-kendini-gözlem (self-observation) mekanizmasıdır,
  global bir toplam değildir.
- **`metrics_aggregator_read_failures_total`** ise `serve_metrics.py`
  sürecinin kendi `aggregate()` çağrılarının sert-hata sayısını yansıtır
  (süreç ömrü boyunca birikir) — bu, gerçekten global bir sağlık
  göstergesidir çünkü aggregation her zaman aynı (serve) süreçte olur.
- `METRICS_SINK=in_memory` (eski davranış) hâlâ desteklenir (opt-in,
  `METRICS_SINK` ortam değişkeni ile) — bu modda önceki sınırlama
  (yalnızca aynı süreç görünür) aynen geçerlidir.

## Performans ayar rehberi (artımlı aggregator + TTL önbellek)

`model_gateway.metrics_aggregate.IncrementalAggregator` +
`model_gateway.metrics_cache.CachedMetricsRenderer` (bkz.
`scripts/ops/serve_metrics.py::main()`), tam-yeniden-tarama yerine artık
yalnızca yeni byte'ları okur ve kısa süreli bir TTL önbelleği kullanır —
metrik çıktısı DEĞİŞMEZ (testle kanıtlı denklik), yalnızca maliyeti
düşer. Bu makinede `scripts/ops/benchmark_metrics_scrape.py`
(varsayılan parametrelerle: 5000 olay, 20 farklı seri, ~6 rotasyon
dosyası, 15 tekrar) ile ölçülen gerçek sonuç:

| Senaryo | p50 gecikme | Tepe bellek |
|---|---|---|
| Tam-yeniden-tarama (ÖNCESİ) | ~249 ms | ~2.67 MB |
| Artımlı, önbelleksiz (SONRASI) | ~45 ms (**~5.6x** hızlı) | ~122 KB |
| Artımlı + TTL önbellek (SONRASI) | ~0.004 ms (önbellek isabetinde) | ~0.3 KB |

> Bu sayılar makineye/veri kümesine bağlı KABA tahminlerdir
> (dogrulama-sınıfı kıyaslama değil) — kendi ortamınızda
> `python scripts/ops/benchmark_metrics_scrape.py` ile yeniden üretin.
> Çıktı: `reports/metrics_perf_<UTC>/summary.md` + `raw.json` (rutin/
> tekrar-üretilebilir olduğundan `.gitignore`'da).

Ayar tablosu (trafik hacmine göre önerilen değerler):

| Ayar | Düşük trafik (varsayılan) | Orta trafik | Yüksek trafik |
|---|---|---|---|
| `METRICS_AGG_CACHE_TTL_SEC` | 5 | 5–10 | 10–30 (scrape aralığına yakın) |
| `METRICS_AGG_WINDOW_MIN` | 60 | 60 | 15–30 (tampon boyutunu sınırlar) |
| `METRICS_AGG_MAX_SERIES` | 2000 | 2000–5000 | 5000–20000 (gerçek benzersiz etiket kombinasyonu sayısına göre) |
| `METRICS_AGG_MAX_EVENTS_PER_SCRAPE` | 5000 | 5000–20000 | 20000+ (yüksek olay hızında tek scrape'in gecikmesini sınırlar; aşımlar bir sonraki scrape'e ertelenir, veri kaybı OLMAZ) |
| `METRICS_JSONL_MAX_MB` (sink rotasyonu) | 50 | 50–100 | 100–200 |

Genel kural: `CACHE_TTL_SEC`'i Prometheus `scrape_interval`'inize yakın
tutun (önbellek isabetini maksimize eder, gereksiz yeniden hesaplamayı
önler); `MAX_SERIES`/`MAX_EVENTS_PER_SCRAPE`'i, ilk üretim gözleminden
sonra gerçek kardinalite/olay hızınıza göre kalibre edin (varsayılanlar
bilinçli olarak muhafazakar/güvenli taraftadır — aşımlar sessizce veri
kaybına yol AÇMAZ, yalnızca ertelenir/loglanır/öz-metriklere yansır).

## Kademeli rollout aşamaları

### Aşama 1 — observe-only (VARSAYILAN, şu anki durum)

- `infra/monitoring/alertmanager/alertmanager.yml`'de `route.receiver: null-receiver`.
- Hiçbir alert hiçbir yere yönlendirilmiyor/sayfalanmıyor.
- Amaç: metrikleri/alert kurallarını canlı veriyle gözlemlemek, gürültü
  seviyesini/eşiklerin doğruluğunu değerlendirmek.

### Aşama 2 — warning routes on

- `alertmanager.yml`'de `severity: warning` route'unun receiver'ı
  `null-receiver`'dan `warning-sink`'e değiştirilir.
- `ALERTMANAGER_WARNING_WEBHOOK_URL` ortam değişkeni gerçek bir webhook
  ile ayarlanır (bu repo'ya **asla** gerçek URL/token commit edilmez).
- Yalnızca **Gate A ve B** sağlandıktan sonra.

### Aşama 3 — critical routes on

- `severity: critical` route'u `critical-sink`'e bağlanır.
- Yalnızca **Gate A, B, C, D'nin tümü** sağlandıktan sonra (bkz. aşağıda).

## Rollout güvenlik kapıları (ilk, manuel değerlendirme — bkz. GÜNCELLEME)

> **GÜNCELLEME:** Bu bölümdeki A-D kapıları, bu alt-sistemin İLK
> tanıtımı sırasında ELLE değerlendirilmişti. Artık bunların OTOMATİK,
> tekrar-çalıştırılabilir, kanıt-üreten karşılığı var:
> `scripts/ops/run_observability_gates.ps1` (Gate A-E, farklı/daha
> kapsamlı harf şeması — karıştırmayın) — bkz. aşağıdaki
> "Go-live gate otomasyonu" bölümü ve "Go/No-Go checklist". Bu eski
> tablo yalnızca TARİHSEL bağlam için korunuyor.

| Kapı (eski, manuel) | Kriter | O görev sırasındaki durum |
|---|---|---|
| A | `/metrics` 24 saat sağlıklı | **Doğrulanmadı** — yalnızca kısa süreli elle test edildi, 24 saatlik gözlem o görevin kapsamında değildi |
| B | Scrape başarı oranı ≥ %99 | **Doğrulanamaz** — bu makinede Prometheus yok |
| C | Her alert tipinden bir sentetik alarm doğrulandı | **Kısmen** — sentetik sinyal üretimi ve `/metrics`'te görünürlüğü doğrulandı; Prometheus'un bunu gerçekten `firing`/`pending`'e çevirmesi ve Alertmanager'a iletmesi **doğrulanamadı** (altyapı yok) |
| D | classify() sözleşmesinde regresyon yok | **Doğrulandı** — tam regresyon 194/194 yeşil, `classify()` dış sözleşmesi değişmedi |

**Sonuç: Gate A/B/C tam olarak sağlanamadığından, warning/critical
routing AÇILMADI.** `alertmanager.yml` bilerek Aşama 1'de (observe-only,
`null-receiver`) bırakıldı. Bu görev, Aşama 2/3'e geçiş için gereken
ALTYAPIYI (config, script'ler) hazırlar ama gerçek geçişi YAPMAZ.

## Go-live gate otomasyonu (Gate A-E, otomatik)

`scripts/ops/run_observability_gates.ps1`, yukarıdaki manuel
değerlendirmenin OTOMATİK, kanıt-üreten (`gate_report.md` +
`gate_results.json`) karşılığıdır.

**GÜNCEL DURUM (2026-08-13, B037 düzeltmesi + gerçek Prometheus/
Alertmanager doğrulaması SONRASI):**

| Gate | Ne kontrol eder | Bu makinede gerçek sonuç (elle çalıştırıldı) |
|---|---|---|
| A_metrics_availability | `/metrics` kullanılabilirliği (varsayılan: SIMULASYON, kısa örnek pencere — `-Real24h` ile gerçek pencere için etiketlenir, gerçek 24s gözlem TEK bir script çalıştırmasında YAPILAMAZ) | **PASS** (10/10 örnek) |
| B_scrape_success_rate | Scrape başarı oranı ≥ %99 (Prometheus yoksa endpoint'in kendisine tekrar istek PROXY'si) | **PASS** (20/20 örnek) |
| C_synthetic_alerts_visible | 4 sentetik alert modunun (fallback-spike, null-intent-spike, preflight-unknown, circuit-open-stuck) `/metrics`'te görünürlüğü | **PASS** (4/4 mod) |
| D_alertmanager_receive_path | Alertmanager alma yolu | **PASS** — gerçek Alertmanager v0.33.1, gerçek bir alert aldı (bkz. aşağıdaki "Gate D gerçek doğrulama") |
| E_classify_regression_smoke | classify/fallback ile ilgili pytest dosyaları | **PASS** (54/54) — B037 düzeltildi (bkz. aşağıda) |

**Genel sonuç: PASS (exit code 0).** Kanıt: `reports/go_live_gates_20260813T073109Z/`
(`git add -f` ile arşivlendi, bkz. bu görevin final raporu). **0 fabrike
edilmedi** — her iki gate de gerçekten çalıştırılıp gerçekten PASS oldu.

**ÖNCEKİ DURUM (bu görevden önce, referans için korunuyor):** Aynı script,
B037 düzeltmesinden ve gerçek Alertmanager kurulumundan ÖNCE genel sonuç
**FAIL (exit code 2)** döndürüyordu — A/B/C PASS, D SKIPPED (Alertmanager
kurulu değildi), E FAIL (B037). Bu, o zamanki GERÇEK durumdu, fabrike
edilmemişti — aşağıdaki iki bölüm neyin nasıl düzeltildiğini açıklıyor.

### B037 düzeltmesi (Gate E artık PASS)

**Kök neden:** `tools/cli-runner/src/runner.py`, `shutil.which("echo")`
ile çalışıyordu. Git Bash'in PATH'inde gerçek bir `echo.exe` var, ama
SAF bir PowerShell sürecinin PATH'inde `echo` yalnızca bir dil anahtar
kelimesi/alias'tır, gerçek bir yürütülebilir DEĞİLDİR — bu yüzden
`test_nlu_provider_flag.py`'nin 3 orchestrator-smoke alt-testi
`EXECUTABLE_NOT_FOUND` ile başarısız oluyordu. Bu, **classify/fallback
sözleşmesinde bir regresyon DEĞİLDİ.**

**Düzeltme (Commit O):** `runner.py`, `shutil.which()` başarısız
olduğunda, KÜÇÜK ve SABİT bir komut kümesi (`echo`, `pwd`) için SAF
PYTHON eşdeğerleri kullanacak şekilde güncellendi — `cmd.exe /c` gibi
bir kabuğa fallback YAPILMADI (bu, `runner.py`'nin "shell=True asla
kullanılmaz" güvenlik ilkesini dolaylı olarak ihlal ederdi). Saf-Python
yol, gerçek bir yürütülebilirden DAHA AZ değil DAHA GÜVENLİDİR (hiçbir
subprocess/shell yüzeyi yok) ve her platformda birebir aynı çıktıyı
üretir. Bilinmeyen komutlar için eski davranış (açık hata) aynen
korundu — hiçbir şey sessizce uydurulmadı.

**Doğrulama:** Tam test suite'i (278 test) hem Git Bash hem native
PowerShell üzerinden **278/278 yeşil** — önceden native PowerShell'de
3 test başarısız oluyordu.

### Gate D gerçek doğrulama (2026-08-13, Prometheus v3.13.2 + Alertmanager v0.33.1)

Bu makinede kalıcı bir Prometheus/Alertmanager kurulumu YOK — ama Gate
D'yi gerçekten (simülasyon değil) doğrulamak için GERÇEK binary'ler
indirilip yerel olarak çalıştırıldı, tam uçtan uca akış (scrape → rule
eval → pending → **firing** → Alertmanager receive) kanıtlandı:

1. **İndirme** (resmi GitHub release'leri, repo'ya COMMIT EDİLMEDİ):

   ```text
   https://github.com/prometheus/prometheus/releases/download/v3.13.2/prometheus-3.13.2.windows-amd64.zip
   https://github.com/prometheus/alertmanager/releases/download/v0.33.1/alertmanager-0.33.1.windows-amd64.zip
   ```

   `C:\Temp\monitoring_bin\`'e (repo DIŞI) açıldı.
2. **Geçici doğrulama config'i:** `infra/monitoring/prometheus/prometheus.yml`
   ve `model_gateway_alerts.yaml`'nin BİREBİR kopyaları — TEK fark,
   `for:` sürelerinin (30m/24h/15m/10m/5m → hepsi 10s) test hızı için
   kısaltılmış olması (expr/labels/annotations aynı). Bu geçici
   dosyalar da `C:\Temp\monitoring_bin\`'de yaşıyor, repoya HİÇ
   girmedi. `infra/monitoring/alertmanager/alertmanager.yml` ise
   **repodaki gerçek dosyanın kendisiyle, hiç değiştirilmeden**
   kullanıldı (yalnızca `${ALERTMANAGER_*_WEBHOOK_URL}` placeholder'ları,
   Alertmanager'ın config-validation'ı geçebilmesi için yerel/dummy bir
   URL'e çözümlenen AYRI bir kopyada dolduruldu — gerçek repo dosyası
   asla dokunulmadı, `git diff` ile doğrulandı).
3. **Akış:** `serve_metrics.py --enable-debug-injection` (port 9108) →
   gerçek `prometheus.exe` (port 9090, hedefi scrape etti, `up`) →
   `POST /debug/inject_synthetic {"mode": "circuit-open-stuck"}` →
   ~15 saniye içinde Prometheus alert'i **firing**'e çevirdi → gerçek
   `alertmanager.exe` (port 9093) alert'i ALDI, `null-receiver`'a
   yönlendirdi (**observe-only korundu, hiçbir yere dışarı çıkmadı**).
4. **Kanıt:** `reports/gate_d_real_validation_20260813T072946Z/` —
   Prometheus `/api/v1/targets` + `/api/v1/alerts`, Alertmanager
   `/api/v2/alerts` + `/api/v2/status` ham JSON yanıtları, kullanılan
   geçici config'lerin kopyaları. `git add -f` ile arşivlendi (rutin
   olmayan, gerçek go-live kanıtı).
5. **Temizlik:** Her iki süreç de doğrulama sonrası durduruldu — bu
   makinede kalıcı bir Prometheus/Alertmanager kurulumu YOK, yalnızca
   doğrulama süresince ayaktaydı.

**Tekrarlamak isteyen bir operatör için:** yukarıdaki adımlar,
`docs/ops/MONITORING_STACK_RUNBOOK.md` "Kurulum" bölümündeki
`prometheus.exe`/`alertmanager.exe` indirme adımlarıyla + bu bölümdeki
geçici config tarifiyle birebir tekrarlanabilir. `run_observability_gates.ps1`'in
Gate D kontrolü artık **erişilebilirliği BİRİNCİL sinyal** olarak
kullanıyor (yalnızca sistem PATH'i değil) — böylece PATH dışında
başlatılmış bir Alertmanager de doğru tespit edilir.

## Performans ayar rehberi tablosu (hatırlatma)

Yukarıdaki (Commit K/L, incremental aggregator + cache) ayar rehberi
tablosu için bkz. "Performans ayar rehberi" bölümü (yukarıda) — go-live
öncesi bu değerlerin trafik hacmine göre kalibre edilmesi önerilir.

## Go/No-Go checklist

Bu, bir go-live kararı vermeden önce (veya periyodik sağlık
denetiminde) sırayla çalıştırılması/kontrol edilmesi gereken adımların
KANONİK listesidir. `scripts/ops/build_observability_signoff.py` bu
listenin çoğunu OTOMATİK olarak toplar (bkz. aşağıda).

- [ ] `./.venv/Scripts/python.exe -m pytest` — tam suite yeşil mi? (B037
      düzeltmesinden beri hem Bash hem native PowerShell'de aynı sonucu
      verir, bkz. "B037 düzeltmesi" bölümü — herhangi biriyle çalıştırılabilir)
- [ ] `powershell -ExecutionPolicy Bypass -File scripts\ops\run_observability_gates.ps1`
      — Gate A-E sonucu nedir? (`gate_results.json`'daki `overall_status`)
      Gerçek Prometheus/Alertmanager ayaktaysa Gate D gerçekten doğrulanır
      (bkz. "Gate D gerçek doğrulama" bölümü); yoksa SKIPPED döner (fabrike
      edilmiş bir PASS değil). Gerçek altyapı varsa `-Real24h` ile gerçek
      bir 24 saatlik pencere sonrası tekrar çalıştırın.
- [ ] `python scripts/ops/calibrate_alert_thresholds.py --hours 24` —
      eşikler gerçek trafikle kalibre edilmiş mi, yoksa hâlâ
      `INSUFFICIENT_DATA` mı? (İkincisi go-live'ı ENGELLEMEZ ama
      go-live SONRASI ilk fırsatta yeniden çalıştırılmalı.)
- [ ] `alertmanager.yml`'in route receiver'ları amaçlanan aşamada mı
      (Aşama 1: hepsi `null-receiver`)? `powershell -File
      scripts\ops\rollback_observability.ps1` (dry-run) ile doğrulayın.
- [ ] `python scripts/ops/build_observability_signoff.py` — nihai
      GO / CONDITIONAL-GO / NO-GO kararı nedir, gerekçesi nedir?
- [ ] Karar **NO-GO** ise: `docs/BACKLOG.md`'ye bir madde düşün, kök
      nedeni giderin, checklist'i baştan çalıştırın.
- [ ] Karar **CONDITIONAL-GO** ise (genellikle eksik Prometheus/
      Alertmanager altyapısından): kısıtlamaları ilgili paydaşlara
      açıkça iletin, `docs/ops/MONITORING_STACK_RUNBOOK.md`'deki
      "Kurulum" adımlarını ne zaman tamamlayacağınızı planlayın.
- [ ] Karar **GO** ise: `reports/observability_signoff_<UTC>/SIGNOFF.md`'yi
      arşivleyin/paylaşın, rollout'u başlatın, `docs/ops/ALERT_PLAYBOOK_MODEL_GATEWAY.md`
      "İlk 10 dakika kontrol listesi"nin on-call ekibine iletildiğinden
      emin olun.

**Rollback (her zaman kullanılabilir, geri dönüşü kolay):**
`powershell -ExecutionPolicy Bypass -File scripts\ops\rollback_observability.ps1`
(varsayılan: dry-run, yalnızca ne değişeceğini raporlar) — gerçekten
uygulamak için `-Apply`. `/metrics` endpoint'ini ASLA kapatmaz,
`data/audit/`/`data/metrics/` içeriğine ASLA dokunmaz, yalnızca
Alertmanager route receiver'larını Aşama 1'e (observe-only) döndürür.
Bu makinede hem dry-run hem apply modu, hem "zaten güvenli" hem
"gerçek escalate edilmiş durumu düzeltme" senaryolarında elle test
edildi (dosyadaki tüm açıklama satırları korunarak, yalnızca
`receiver:` değerleri değiştirilir).

## Kurulum (Aşama 2/3'e geçmeden önce gerekli)

1. Prometheus indirin: <https://prometheus.io/download/> — `prometheus.exe`'yi PATH'e ekleyin.
2. Alertmanager indirin (aynı sayfa) — `alertmanager.exe`'yi PATH'e ekleyin.
3. `scripts/ops/start_monitoring_stack.ps1` çalıştırın — üç bileşeni de
   (metrics, Prometheus, Alertmanager) başlatmayı dener, hangisinin
   gerçekten başladığını raporlar.
4. `scripts/ops/verify_alert_pipeline.ps1` ile E2E doğrulama yapın
   (bkz. aşağıda "Sentetik testler" notu).
5. Gate A (24 saat gözlem) tamamlandıktan sonra Aşama 2'ye geçin.

## Geri alma (rollback)

- **Aşama 2/3 → 1:** `alertmanager.yml`'deki ilgili route'un
  receiver'ını `null-receiver`'a geri çevirin, Alertmanager'ı yeniden
  başlatın (config reload).
- **Tüm izleme yığınını durdurma:** `scripts/ops/stop_monitoring_stack.ps1`.
- **Metrics endpoint'ini tamamen kapatma:** `METRICS_ENABLED=false` —
  `/metrics` 404 döner, sıfır davranış değişikliği (bkz.
  `docs/ops/SLO_MODEL_GATEWAY.md`).
- Kod seviyesinde tam geri alma: bu görevin commit'lerini (Commit G, H)
  revert edin — `services/model-gateway/src/model_gateway/router.py`/`compat.py`/`runtime_verify.py`
  bu commit'lerde değişmedi, yalnızca YENİ, bağımsız dosyalar eklendi.

## Sık görülen hata desenleri ve düzeltmeleri

| Belirti | Olası neden | Düzeltme |
|---|---|---|
| `/metrics` boş dönüyor | Hiç metrik yazılmadı (fresh JSONL dosyası/temiz kurulum) VEYA `METRICS_SINK=in_memory` iken sentetik sinyal AYRI bir süreçten enjekte edildi | `METRICS_SINK=jsonl_append` (varsayılan) kullanın; `in_memory` modunda `scripts/ops/emit_synthetic_gateway_signals.py --target-url` kullanın |
| `/metrics` 503 dönüyor | JSONL dosyası VAR ama okunamıyor (izin/disk hatası) — `AggregationError` | Yanıt gövdesindeki tanılama nedenine bakın; `METRICS_JSONL_PATH` yolunun bir dosya (dizin değil) olduğunu ve okunabilir olduğunu doğrulayın |
| `verify_alert_pipeline.ps1` sürekli exit 1 dönüyor | Prometheus/Alertmanager kurulu değil (bu makinenin varsayılan durumu) | Beklenen — "Kurulum" bölümüne bakın |
| Prometheus hedefi `down` gösteriyor | `serve_metrics.py` çalışmıyor veya port çakışması | `Test-NetConnection 127.0.0.1 -Port 9108` ile kontrol edin |
| Alertmanager'da alert görünmüyor ama Prometheus'ta firing | `alertmanager.yml`'deki `alerting.alertmanagers` hedefi yanlış/Alertmanager farklı portta | `prometheus.yml`'deki `alerting` bölümünü kontrol edin |

## Sentetik testler — izolasyon uyarısı

> **`scripts/ops/verify_alert_pipeline.ps1` ve `emit_synthetic_gateway_signals.py`,
> bakım penceresinde veya izole bir ortamda çalıştırılmalıdır.** Bu
> script'ler `synthetic="true"` etiketiyle işaretlenmiş sahte metrik
> örnekleri üretir — gerçek bir izleme panosunu/on-call'ı yanlışlıkla
> tetiklememesi için (Aşama 2/3 aktifse) bu ayrım önemlidir. Aşama 1
> (observe-only, mevcut varsayılan) altında risk yoktur çünkü hiçbir
> alert hiçbir yere yönlendirilmiyor.

## Post-GO weekly ritual

GO kararı verildikten sonra gözlemlenebilirlik altyapısının sessizce
bozulmaması ("gözlemlenebilirlik borcu") için, **haftalık olarak**
(önerilen: her Pazartesi, veya Windows Task Scheduler ile otomatik):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\ops\weekly_observability_review.ps1
```

Bu script otomatik olarak:

1. **Gate snapshot'ı** (hafif mod — `run_observability_gates.ps1 -Lightweight`,
   Gate E/tam pytest suite atlanır, yalnızca operasyonel sinyaller
   A/B/C/D kontrol edilir — hızlı, ~5 saniye).
2. **Drift tespiti** (`scripts/ops/detect_observability_drift.py`) —
   metrik şeması/alert kuralları/config varsayılanları onaylı durumdan
   sapmış mı?
3. **Son 7 günlük fallback + null-intent oranlarını** (sentetik hariç,
   `calibrate_alert_thresholds.py` ile AYNI hesaplama mantığı).
4. `reports/weekly_observability_<YYYY-WW>/review.md`'ye bir girdi
   **EKLER** (var olanları silmez — kalıcı bir günlük/log oluşturur).
   Diğer `reports/*` çıktılarının aksine bu dizin `.gitignore`'da
   KASITLI olarak YOKTUR — normal `git add` ile (özel bir `-f` bayrağı
   GEREKMEDEN) commit edilip uzun vadeli bir trend kaydı olarak
   saklanması önerilir.
5. **GREEN / YELLOW / RED** durumunu + gerekçelerini yazdırır, durumla
   eşleşen exit code (0/1/2) ile çıkar.

| Durum | Anlamı | Aksiyon |
|---|---|---|
| GREEN | Tüm kontroller sağlıklı | Yok — bir sonraki haftaya kadar bekleyin |
| YELLOW | Gate PARTIAL, kritik olmayan drift, veya null-intent WARN eşiğini aşıyor | İnceleyin, `docs/BACKLOG.md`'ye not düşün, bir sonraki haftaya kadar izleyin (acil değil) |
| RED | Gate FAIL, KRİTİK drift, veya null-intent CRIT eşiğini aşıyor | **HEMEN** inceleyin — bkz. aşağıdaki "Drift response SOP" ve `docs/ops/ALERT_PLAYBOOK_MODEL_GATEWAY.md` "Triaj karar ağacı" |

## Drift response SOP

`scripts/ops/detect_observability_drift.py`, `infra/monitoring/baseline/`
altındaki ONAYLI durumla (metrik şeması + alert kuralları checksum'i +
config varsayılanları) gerçek durumu karşılaştırır. Bir drift
bulunduğunda:

1. **Raporu oku:** `reports/drift_<UTC>/drift_report.md` — hangi
   kategori (`metrics_schema`/`alert_rules`/`remote_default`/`strict_flag`),
   hangi şiddet (WARN/CRITICAL), ham kanıt (`evidence`).
2. **`remote_default` CRITICAL ise:** BU EN CİDDİ senaryodur — görev
   kısıtlarının ("remote-default değişikliği yok") sessizce ihlal
   edildiği anlamına gelir. `services/model-gateway/src/model_gateway/config.py`'de
   `load_config()`'in `REMOTE_ENABLED`/`remote_enabled` için varsayılanının
   `False`'tan `True`'ya değiştirilip değiştirilmediğini KONTROL EDİN —
   kasıtsız/onaysız ise HEMEN geri alın (git revert), kasıtlıysa
   `infra/monitoring/baseline/metrics_manifest_v1.json`'daki
   `config_defaults.remote_enabled`'ı GÜNCELLEYİN (aynı commit'te, bkz.
   o dosyanın "guncelleme_proseduru" notu) VE `docs/BACKLOG.md`'ye
   AÇIKÇA bir ADR/karar notu düşün.
3. **`alert_rules` CRITICAL ise:** `infra/monitoring/prometheus/model_gateway_alerts.yaml`
   onay dışı değişmiş demektir. ÖNCE `infra/monitoring/baseline/approved_checksums_ledger.jsonl`'e
   bakın — eğer gözlenen checksum orada `new_checksum` olarak KAYITLIYSA,
   bu zaten `scripts/ops/apply_threshold_proposal.ps1 -Apply` ile
   GERÇEKTEN onaylanıp uygulanmış bir eşik değişikliğidir ve drift
   detector bunu OTOMATİK OLARAK CRITICAL'e DÜŞÜRMEZ (severity=NONE,
   bkz. "Eşik Değişikliği SOP" bölümü) — bu durumda EK bir işlem
   GEREKMEZ. Ledger'da YOKSA: `git log -p -- infra/monitoring/prometheus/model_gateway_alerts.yaml`
   ile SON değişikliği inceleyin — kasıtlı/incelenmiş bir değişiklikse
   `infra/monitoring/baseline/alerts_checksum_v1.txt`'i YENİDEN hesaplayıp
   güncelleyin (`sha256sum infra/monitoring/prometheus/model_gateway_alerts.yaml`);
   değilse geri alın.
4. **`metrics_schema` WARN ise:** Ya (a) gerçekten yeni bir metrik/etiket
   eklendi (kasıtlı — `metrics_manifest_v1.json`'ı güncelleyin) ya da
   (b) bir yazım hatası/kazara etiket sızıntısı var (kod tarafında
   düzeltin). Hangisi olduğunu `evidence.metric`/`evidence.unexpected_labels`
   alanlarından + ilgili kod yolundan (bkz. `metrics_manifest_v1.json`'daki
   `source` alanı) belirleyin.
5. **`strict_flag` WARN ise:** Genellikle kasıtlı bir operatör kararıdır
   (bkz. `docs/ops/MODEL_FALLBACK_RUNBOOK.md`) — doğrulayın, kasıtlıysa
   baseline'ı güncelleyin, değilse geri alın.
6. **Her durumda:** Düzeltme/onay AYNI commit'te olmalı, "sessiz bir
   baseline düzeltme commit'i" ASLA olmamalı — değişiklik code review'da
   açıkça görülmelidir.

## Eşik Değişikliği SOP (proposal → review → apply → rollback)

Alert eşiklerini (`model_gateway_alerts.yaml`'deki sayısal değerler)
DEĞİŞTİRMENİN TEK onaylı yolu bu iş akışıdır — dosyayı elle düzenlemek
YASAKTIR (denetim izi bırakmaz, `alerts_checksum_v1.txt`/ledger ile
senkron kalmaz).

**Araçlar** (hepsi `scripts/ops/` altında):

| Adım | Araç | Girdi | Çıktı |
|---|---|---|---|
| 1. Öneri (proposal) üret | `generate_threshold_proposals.py` | JSONL metrik verisi | `reports/threshold_proposals/<proposal_id>/proposal.json` |
| 2. İnceleme (review) kaydet | `create_threshold_review_record.py` | proposal.json + karar (APPROVE/REJECT/NEEDS_DATA) | `reports/threshold_reviews/<proposal_id>/review_record.json` |
| 3. Uygula (apply) | `apply_threshold_proposal.ps1` | proposal.json + review_record.json | `reports/threshold_apply_<UTC>/apply_report.md`+`.json`, gerçek dosya değişikliği (yalnızca `-Apply` ile) |
| 4. Geri al (rollback, gerekirse) | `rollback_threshold_apply.ps1` | apply_report.json | `reports/threshold_rollback_<UTC>/rollback_report.md`+`.json`, dosya eski haline döner |

**Uygunluk kuralları** (`apply_threshold_proposal.ps1`, TÜMÜ sağlanmalı,
aksi halde exit code 2 ve HİÇBİR dosya değişmez):
1. Proposal, `infra/monitoring/governance/threshold_proposal_schema_v1.json`
   şemasına uygun olmalı.
2. review_record'un `decision`'ı `APPROVE` VEYA `APPROVE_EMERGENCY` olmalı.
3. review_record'un `proposal_id`'si proposal ile eşleşmeli.
4. review_record'daki `linked_proposal_checksum`, proposal'ın YENİDEN
   HESAPLANMIŞ checksum'ıyla birebir eşleşmeli (review'dan SONRA proposal
   değiştiyse apply REDDEDİLİR).
5. `decision == APPROVE_EMERGENCY` ise EK OLARAK: `incident_id`,
   `justification`, `timebox_hours` ((0, 24] aralığında), `retro_review_due_utc`
   (geçerli, `approved_at_utc`'den SONRA bir ISO8601 UTC tarih) ZORUNLUDUR
   — bkz. asağıdaki "Emergency Change Protocol (Enforced)".

**Varsayılan mod her zaman DRY-RUN'dır** — `apply_threshold_proposal.ps1`
`-Apply` olmadan hiçbir dosyayı değiştirmez, yalnızca uygunluğu kontrol
edip NE değişeceğini raporlar. Gerçek uygulama:
- Hedef dosyanın bir YEDEĞİNİ alır (`reports/threshold_apply_<UTC>/backups/`).
- YALNIZCA ilgili sayısal eşiği hedefli bir regex ile değiştirir (tam
  YAML yeniden-serileştirme YAPILMAZ — dosyadaki açıklamalar korunur).
- `infra/monitoring/baseline/approved_checksums_ledger.jsonl`'e bir
  girdi ekler (drift detector'ın "onaylı değişiklik" istisnası bunu okur).
- `data/audit/audit.log.jsonl`'e gerçek bir denetim kaydı ekler
  (`task=THRESHOLD_CHANGE_APPLY`, `status=APPLIED`).

**RACI:**

| Rol | Kim | Sorumluluk |
|---|---|---|
| Proposer (Önerir) | On-call mühendis / kalibrasyon çalıştıran kişi | `generate_threshold_proposals.py` çalıştırır, öneriyi paylaşır |
| Reviewer (İnceler/Onaylar) | On-call mühendisten FARKLI bir kişi (ikinci göz) | Öneriyi + kanıtı (`evidence_paths`) inceler, `create_threshold_review_record.py` ile APPROVE/REJECT/NEEDS_DATA kararını kaydeder |
| Applier (Uygular) | Reviewer VEYA Proposer (ikisinden biri, ama Reviewer'ın kendi APPROVE'unu kendisi uygulaması TERCİH EDİLİR) | `apply_threshold_proposal.ps1 -Apply` çalıştırır, sonucu doğrular |
| Accountable (Hesap verebilir) | Model Gateway sahibi/on-call lead | Ledger + audit log'un tutarlılığından, acil durum değişikliklerinin RETROAKTİF incelendiğinden sorumlu |

## Emergency Change Protocol (Enforced)

**v1.1 ile bu artık yalnızca bir SOP DEĞİL — MAKİNE TARAFINDAN ZORLANAN
bir yoldur.** Bir alert gerçek bir olayda gürültü yapıyorsa VE normal
review turu (saatler, farklı bir incelemeci) beklenemeyecek kadar
acilse, on-call mühendis `--decision APPROVE_EMERGENCY` kullanarak
KENDİ review_record'unu oluşturabilir — ama bu, dört alanın TÜMÜNÜN
GEÇERLİ olmasını ZORUNLU kılar (`threshold_governance_core.py::validate_emergency_fields`,
hem review_record OLUŞTURULURKEN hem de apply ANINDA — review_record.json
elle değiştirilse bile — İKİ KEZ doğrulanır):

1. **`--incident-id`** — boş olamayan bir olay tanımlayıcısı (ör. `INC-1234`).
2. **`--justification`** — boş olamayan, neden normal incelemenin
   beklenemediğini açıklayan bir gerekçe (genel `--rationale` alanından
   AYRI, acil duruma özgü).
3. **`--timebox-hours`** — `(0, 24]` aralığında sayısal bir değer.
   `24`'ten büyük bir değer REDDEDİLİR (exit 2, review_record.json
   YAZILMAZ).
4. **`--retro-review-due-utc`** — geçerli, saat dilimi içeren bir
   ISO8601 UTC tarih, `approved_at_utc`'den KESİNLİKLE SONRA olmalı.

```powershell
python scripts/ops/create_threshold_review_record.py `
  --proposal-path reports/threshold_proposals/<id>/proposal.json `
  --reviewer "on-call-muhendis" --decision APPROVE_EMERGENCY `
  --rationale "acil esik gevsetmesi" `
  --incident-id "INC-1234" `
  --justification "Prod'da yanlis-pozitif alarm firtinasi, hemen mudahale gerekiyor" `
  --timebox-hours 6 `
  --retro-review-due-utc "2026-08-15T00:00:00+00:00"

pwsh scripts/ops/apply_threshold_proposal.ps1 -ProposalPath ... -ReviewRecordPath ... -Apply
```

**Bu, normal onay akışını BYPASS ETMEZ** — review_record ZORUNLUDUR,
checksum/şema bütünlüğü kontrolleri AYNEN uygulanır; yalnızca "farklı
bir kişi onaylasın" (iki-göz) kısıtı GEÇİCİ olarak esnetilir.

**Uygulama sonrası otomatik etiketleme:** başarılı bir acil durum
apply'ı `apply_report.json` (`is_emergency: true` + tam `emergency_fields`),
`data/audit/audit.log.jsonl` (`risk_level: high`, tam acil durum
alanları) VE `approved_checksums_ledger.jsonl` (`is_emergency`,
`retro_review_due_utc`) içinde AÇIKÇA görünür.

**Otomatik vade takibi (bkz. "Overdue emergency review escalation SOP"
aşağıda):** `retro_review_due_utc` vadesi geçtiğinde VE aynı alert için
takip eden NORMAL bir onay YOKSA, hem `detect_observability_drift.py`'nin
her çalıştırmasında hem de bağımsız `scripts/ops/check_emergency_review_overdue.py`
ile bu OTOMATİK OLARAK CRITICAL governance drift olarak yakalanır —
kimse manuel takip etmeyi unutursa bile SESSİZCE kaçırılmaz.

Bu yol, `docs/ops/ALERT_PLAYBOOK_MODEL_GATEWAY.md`'deki ilgili alert
playbook'unda AYRICA belgelenir.

## VerifyReload operational prerequisites

`apply_threshold_proposal.ps1 -Apply -VerifyReload`, gerçek `-Apply`
BAŞARILI olduktan SONRA dört bağımsız kontrol çalıştırır — hiçbirini
FABRİKE ETMEZ, hiçbiri yapılandırılmamışsa/erişilemezse bunu AÇIKÇA
`VERIFICATION_SKIPPED` (yapılandırılmadı) veya `FAIL` (yapılandırıldı
ama başarısız) olarak raporlar:

| Kontrol | Parametre | Bu makinede varsayılan durum |
|---|---|---|
| `promtool check rules` | `-PromtoolPath` | Kurulu DEĞİL — `VERIFICATION_SKIPPED` (bkz. aşağıdaki NOT) |
| `amtool check-config` | `-AmtoolPath` + `-AlertmanagerConfigPath` | Kurulu DEĞİL — `VERIFICATION_SKIPPED` |
| Prometheus health endpoint | `-PrometheusHealthUrl` | Sağlanmadı — `VERIFICATION_SKIPPED` |
| Alertmanager readiness endpoint | `-AlertmanagerReadyUrl` | Sağlanmadı — `VERIFICATION_SKIPPED` |

**NOT (bu makineye özgü, geçici):** Gerçek Prometheus v3.13.2 +
Alertmanager v0.33.1 ikilileri daha önce (Go-live Gate D doğrulaması
için, bkz. B037/Gate D bölümü) `C:\Temp\monitoring_bin\` altına
indirildi — repo'ya COMMIT EDİLMEDİ (geçici, makineye özgü). Bu ikililer
mevcutsa `-PromtoolPath`/`-AmtoolPath` ile GEÇİLEBİLİR; **gerçek bir
`-Apply -VerifyReload` çalıştırmasında bu tespit edildi:** `promtool
check rules` PASS verdi, ama `amtool check-config` `infra/monitoring/profiles/persistent/alertmanager.yml`
üzerinde GERÇEK bir sorun buldu (`invalid URL: unsupported scheme ""`)
— bu, o profildeki webhook receiver'ın `${VAR}` yer tutucusunun
Alertmanager tarafından ÇÖZÜLMEDİĞİ (Commit R'de zaten bilinen/belgeli
bir sınırlama, bkz. `infra/monitoring/profiles/persistent/README.md`)
nedeniyle **beklenen, gerçek bir FAIL'dir** — VerifyReload'ın "asla PASS
uydurma" ilkesinin çalıştığının somut kanıtıdır, kod tarafında bir hata
DEĞİLDİR.

**Genel durum semantiği:** herhangi bir kontrol FAIL ise genel durum
FAIL; TÜMÜ VERIFICATION_SKIPPED ise genel durum VERIFICATION_SKIPPED;
aksi halde (en az bir PASS, hiç FAIL yok) PASS. Genel durum FAIL ise
`apply_threshold_proposal.ps1` **exit code 3** ile çıkar (0/2'den
AYRI) — dosya ZATEN uygulandı (geri alınmadı), operatör
`apply_report.md`'yi incelemeli ve gerekirse `rollback_threshold_apply.ps1`
çalıştırmalıdır.

## Overdue emergency review escalation SOP

`scripts/ops/check_emergency_review_overdue.py` (bağımsız CLI) VE
`detect_observability_drift.py`'nin her çalıştırması (otomatik
entegre, `observability_drift_core.check_emergency_review_overdue_drift`),
`approved_checksums_ledger.jsonl`'deki HER acil durum (`is_emergency: true`)
girdisini kontrol eder:

1. **Vadesi henüz gelmemiş** (`now <= retro_review_due_utc`) → sessizce
   geçilir, HİÇBİR bulgu üretilmez.
2. **Vadesi geçmiş VE aynı `alert_name` için SONRAKİ bir NORMAL
   (`is_emergency: false`) apply girdisi VARSA** → "takip eden onay
   gerçekleşti" sayılır, HİÇBİR bulgu üretilmez.
3. **Vadesi geçmiş VE takip eden normal onay YOKSA** → `CATEGORY_EMERGENCY_GOVERNANCE`
   altında **CRITICAL** bir drift bulgusu üretilir (`proposal_id`,
   `alert_name`, `retro_review_due_utc`, `hours_overdue`, `apply_report_path`
   kanıtlarıyla) — hem `drift_report.md`'de (rutin drift taramasının
   PARÇASI olarak) hem de bağımsız `overdue_report.md`'de görünür.

**Eskalasyon adımları (bir CRITICAL `emergency_governance` bulgusu
görüldüğünde):**
1. `evidence.apply_report_path`'teki apply raporunu VE ilgili
   `incident_id`'yi inceleyin.
2. Değişiklik hâlâ GEÇERLİ/doğruysa: HEMEN normal bir `create_threshold_review_record.py
   --decision APPROVE` review_record'u oluşturup (farklı bir
   incelemeci ile) tekrar `apply_threshold_proposal.ps1 -Apply`
   çalıştırın (aynı değere yeniden yazar, `old_checksum == new_checksum`,
   zararsız) — bu, ledger'a "takip eden normal onay" girdisini ekler ve
   drift'i temizler.
3. Değişiklik ARTIK geçerli DEĞİLSE: `rollback_threshold_apply.ps1 -Apply`
   ile geri alın — dosya baseline'a (veya önceki onaylı duruma) döner,
   drift de doğal olarak temizlenir (checksum artık bu ledger girdisine
   değil, önceki duruma işaret eder).
4. Her iki durumda da `docs/BACKLOG.md`'ye bir not düşün: acil durumun
   ne zaman kapatıldığı + kim tarafından.
5. **Bu, "acil durum → asla unutulmaz" garantisinin somut mekanizmasıdır** —
   `retro_review_due_utc` geçtiğinde kimse manuel takip etmeyi
   unutursa bile, drift tespiti (haftalık ritüelin veya günlük
   çalıştırmanın PARÇASI olarak) bunu OTOMATİK yakalar.

## Pilot Flags Matrix (default OFF)

**v1.2, hiçbir flag açılmadan önceki davranışı BİREBİR korur** — aşağıdaki
DÖRT flag'in hepsi, açıkça etkinleştirilmedikçe hiçbir kod yolunu
DEĞİŞTİRMEZ (gorev kısıtı: "Default behavior unchanged unless explicit
flag enabled"). Hiçbiri `config/model_gateway.yaml`'da DEĞİL — hepsi
ortam değişkeni (env var) olarak okunur, CLI/PS1 parametreleriyle
per-run override edilebilir.

| Flag | Varsayılan | Etkilediği araç | Açıldığında ne değişir |
|---|---|---|---|
| `GOV_EMERGENCY_CHAIN_MATCHING` | `0` (KAPALI) | `detect_observability_drift.py` (`--use-chain-matching`) | "Takip eden onay" kontrolü yalnızca `alert_name` yerine checksum-zinciri sürekliliğini de arar; aynı-alert-ama-kırık-zincir durumu artık sessizce "çözüldü" sayılmaz, WARN üretir |
| `GOV_AUTO_ROLLBACK_ON_VERIFY_FAIL` | `0` (KAPALI) | `apply_threshold_proposal.ps1` (`-AutoRollbackOnVerifyFail`) | `-VerifyReload` genel durumu FAIL verirse, dosya OTOMATİK olarak yedekten geri yüklenir (yalnızca FAIL'de — SKIPPED/PASS'te ASLA) |
| `GOV_EMERGENCY_LEGITIMACY_REQUIRED` | `0` (KAPALI, pilot non-blocking) | Hiçbiri (henüz) — yalnızca `check_emergency_legitimacy.py`'nin çıktısında/raporunda GÖRÜNÜR | **v1.2'de bu flag hiçbir apply akışını ETKİLEMEZ** — yalnızca "gelecekte enforced moda geçilirse bu kontrol zorunlu hale gelecek" niyetinin dokümantasyonudur (bkz. aşağıdaki "Promotion criteria") |
| `GOV_TICKET_REGEX` | `^OPS-\d+$` | `check_emergency_legitimacy.py` (`--ticket-regex`) | Ticket format doğrulamasının kabul ettiği düzenli ifadeyi değiştirir |

**Ek parametre (env var değil, yalnızca PS1 anahtarı):** `-AutoRollbackMode <safe|strict>`
(varsayılan `safe`) — `safe`, dosyanın checksum'ı apply'ın yazdığı
değerden SAPMIŞSA (araya başka bir değişiklik girmişse) auto-rollback'i
İPTAL EDER; `strict` bu ek kontrolü yapmadan her FAIL'de tetiklenir.

## How to run v1.2 trials safely

1. **Her zaman ÖNCE dry-run/salt-okunur araçlarla başlayın:**
   - `python scripts/ops/run_emergency_chain_trial.py` — hiçbir şeyi
     DEĞİŞTİRMEZ, mevcut ledger üzerinde v1.1 vs v1.2 karşılaştırması
     üretir (`reports/emergency_chain_trial_<UTC>/chain_eval.md`).
   - `python scripts/ops/check_emergency_legitimacy.py --incident-id <id> --provider mock`
     — hiçbir gerçek servise bağlanmaz, hiçbir sır gerektirmez.
2. **Flag'leri TEK TEK açın, birlikte değil** — örneğin önce yalnızca
   `GOV_EMERGENCY_CHAIN_MATCHING=1` ile bir haftalık gözden geçirme
   döngüsü çalıştırıp sonuçları inceleyin, ondan SONRA
   `GOV_AUTO_ROLLBACK_ON_VERIFY_FAIL` ile denemeye geçin.
3. **`-AutoRollbackOnVerifyFail`'i İLK KEZ açmadan önce**, hızlı bir
   geri dönüş yolunun var olduğunu doğrulayın: `rollback_threshold_apply.ps1`'in
   çalıştığını (bkz. "Eşik Değişikliği SOP") ve gerçek `promtool`/`amtool`
   binary'lerinin (varsa) doğru yollarda olduğunu kontrol edin — gorev
   kısıtı: "Fast rollback path must exist before enabling any trial
   feature".
4. **`-AutoRollbackMode`'u ÖNCE `safe` ile deneyin**, yalnızca `safe`
   modun beklendiği gibi çalıştığını (concurrent-değişiklik korumasının
   işe yaradığını) gördükten SONRA `strict`'i düşünün.
5. **Her deneme bir kanıt paketi ÜRETMELİDİR** — `reports/emergency_chain_trial_<UTC>/`,
   `reports/emergency_legitimacy_<UTC>/`, veya bir `apply_report.json`'daki
   `auto_rollback` alanı; kanıtsız bir "denedim, çalıştı" ASLA yeterli
   değildir (bkz. `reports/v1_2_pilot_<UTC>/pilot_summary.md`).
6. **Genel rollout YAPMAYIN** — bu flag'ler, açık ADR/karar OLMADAN
   `config/model_gateway.yaml`'a veya CI/CD ortam değişkenlerine
   KALICI olarak eklenmemelidir (gorev kısıtı: "Pilot-first, no broad
   rollout").

## Promotion criteria from pilot → enforced

Bir v1.2 pilot özelliğinin "enforced" (varsayılan davranışın PARÇASI,
flag'siz) statüsüne terfi edebilmesi için AŞAĞIDAKİLERİN HEPSİ
sağlanmalıdır:

1. **En az 2-4 hafta** gerçek (veya gerçekçi sentetik) trafikte,
   flag AÇIK durumda, hiçbir beklenmedik/istenmeyen davranış OLMADAN
   çalıştırılmış olmalı.
2. **v1.1/v1.2 sonuç farkı** (`chain_eval.md` gibi karşılaştırma
   raporları) İNCELENMİŞ ve YANLIŞ-POZİTİF/YANLIŞ-NEGATİF oranının
   KABUL EDİLEBİLİR olduğu (ideal: sıfır yanlış-negatif) DOĞRULANMIŞ
   olmalı.
3. **`docs/BACKLOG.md`'de açık bir karar notu** (kim, ne zaman, hangi
   kanıta dayanarak) bulunmalı — sessiz bir "varsayılan değiştirme
   commit'i" ASLA olmamalı.
4. **Geri alma planı belgelenmiş olmalı** — terfi sonrası bir sorun
   çıkarsa, flag'i TEKRAR `0`'a çekmenin (veya kod satırını geri
   almanın) YOLU AÇIKÇA yazılmalı.
5. **Özel olarak `GOV_EMERGENCY_LEGITIMACY_REQUIRED`** için: **GÜNCELLENDİ —
   bu artık uygulandı** (bkz. aşağıdaki "Pilot Promotion Policy").
   `check_apply_eligibility`, `legitimacy_enforcement_active`/`legitimacy_report`
   parametrelerini kabul eder — ama bunlar YALNIZCA `apply_threshold_proposal.ps1`
   tarafından, `emergency_legitimacy_required`'ın GERÇEKTEN PROMOTE
   edildiği (bkz. `pilot_flags_state.json`) VE `GOV_EMERGENCY_LEGITIMACY_REQUIRED=1`
   olduğu durumda True/gerçek rapor ile doldurulur — flag'in tek
   başına `1` yapılması hâlâ YETERLİ DEĞİLDİR.

Bu kriterler karşılanana kadar TÜM v1.2 özellikleri **KEEP PILOT**
durumunda kalır (bkz. `reports/v1_2_pilot_<UTC>/pilot_summary.md`
öneri alanı).

## Pilot Promotion Policy

v1.2 pilot özelliklerinin (chain-matching, auto-rollback, legitimacy)
**PILOT → ENFORCED** geçişi artık tamamen **olay-tabanlı, ölçülebilir,
denetlenebilir** bir kapıdan geçer — kimse "bence artık hazır" diyerek
bir flag'i elle açamaz.

**Bileşenler:**

| Bileşen | Dosya | Rolü |
|---|---|---|
| Kriter manifestosu (policy-as-data) | `infra/monitoring/governance/pilot_promotion_criteria_v1.json` | Her özellik için `observation_min_days`, `min_runs`, `max_false_positive_rate`, `max_unresolved_critical`, `required_evidence_paths`, `blocker_conditions` tanımlar |
| Değerlendirici | `scripts/ops/evaluate_pilot_promotion.py` | GERÇEK kanıt dosyalarını tarar, her özellik için `PROMOTE`/`EXTEND_PILOT`/`REJECT` kararı üretir (`reports/pilot_promotion_<UTC>/promotion_report.md`+`.json`) |
| Kalıcı terfi durumu | `infra/monitoring/governance/pilot_flags_state.json` | Hangi özelliklerin GERÇEKTEN terfi ettiğinin TEK doğruluk kaynağı (source of truth) — normal `git add` ile izlenir |
| Uygulama aracı | `scripts/ops/promote_pilot_flags.ps1` | `promotion_report.json`'daki `PROMOTE` kararlarını `pilot_flags_state.json`'a YAZAR (VARSAYILAN dry-run) |
| Geri alma aracı | `scripts/ops/rollback_pilot_flags.ps1` | Bir terfiyi `flag_apply_report.json`'daki `previous_state`'e BİREBİR geri döndürür |

**Kritik tasarım ilkesi — çift kapı (double-gate):** `GOV_EMERGENCY_LEGITIMACY_REQUIRED=1`
ortam değişkenini elle ayarlamak TEK BAŞINA HİÇBİR ŞEYİ engellemez.
Zorlayıcı (blocking) davranış YALNIZCA hem (a) bu ortam değişkeni `1`
HEM DE (b) `pilot_flags_state.json`'da `emergency_legitimacy_required.promoted == true`
olduğunda etkinleşir (bkz. `pilot_promotion_core.py::is_legitimacy_enforcement_active`).
Bu, bir operatörün yanlışlıkla (veya kötü niyetle) tek bir ortam
değişkeniyle üretim davranışını değiştirememesini GARANTİ eder — gerçek
bir terfi kaydı (audit + `pilot_flags_state.json` güncellemesi)
ZORUNLUDUR.

**Blocker koşulları KOŞULSUZDUR:** Herhangi bir `blocker_condition`
tetiklenirse (ör. `provider_is_stub_only` — legitimacy kontrolü hiç
GERÇEK bir sağlayıcıyla test edilmemişse), sonuç sayısal kriterlerden
BAĞIMSIZ olarak **REJECT**'tir. Hiçbir "yeterli çalışma sayısı" veya
"yeterli gözlem süresi" bunu geri alamaz.

## Decision matrix and escalation path

| Değerlendirici kararı | Anlamı | Sonraki adım |
|---|---|---|
| `PROMOTE` | TÜM ölçülebilir kriterler karşılandı, hiçbir blocker tetiklenmedi | `promote_pilot_flags.ps1 -Apply` ile UYGULANABİLİR |
| `EXTEND_PILOT` | En az bir kriter henüz karşılanmadı (yetersiz çalıştırma/gözlem penceresi/eksik false-positive verisi/vb.) | Pilot DEVAM EDER, `promote_pilot_flags.ps1` bu özelliğe DOKUNMAZ |
| `REJECT` | Bir `blocker_condition` tetiklendi | `promote_pilot_flags.ps1` bu özelliğe ASLA dokunmaz — kök nedeni (`promotion_report.md`'deki gerekçe) ELLE çözün, sonra yeniden değerlendirin |

**`evaluate_pilot_promotion.py` çıkış kodları:** `0` (tüm özellikler
PROMOTE), `1` (en az biri EXTEND_PILOT, hiçbiri REJECT değil — normal/
beklenen erken-pilot durumu), `2` (herhangi biri REJECT — bir CI/cron
bağlamında bu, insan dikkatine escalate edilmelidir).

**Eskalasyon:** Bir `REJECT` görüldüğünde, `docs/BACKLOG.md`'ye bir not
düşün (hangi blocker, ne zaman, kim inceledi) — REJECT kararını
"yoksaymak" veya kriterleri gevşeterek REJECT'i dolanmak (bkz.
`infra/monitoring/governance/pilot_promotion_criteria_v1.json`'daki
"guncelleme_proseduru") KESİNLİKLE aynı commit'te açık bir gerekçe
gerektirir.

## How to revert promoted flags safely

1. **Hangi apply'ı geri alacağınızı bulun:** `reports/pilot_promotion_<UTC>/flag_apply_report.json`
   (o TERFİ ANINDA üretilen rapor — `promote_pilot_flags.ps1 -Apply`'ın
   çıktısındaki `evidence_dir` alanına bakın).
2. **Önce dry-run:** `pwsh scripts/ops/rollback_pilot_flags.ps1 -FlagApplyReportPath <flag_apply_report.json>`
   — mevcut durumu VE geri yüklenecek (önceki) durumu YAN YANA gösterir,
   hiçbir şeyi DEĞİŞTİRMEZ.
3. **Sonra gerçek geri alma:** AYNI komuta `-Apply` ekleyin —
   `pilot_flags_state.json`'ı o apply'dan HEMEN ÖNCEKİ haline BİREBİR
   döndürür, `data/audit/audit.log.jsonl`'e `task=pilot_flag_rollback`
   kaydı ekler, `reports/pilot_flag_rollback_<UTC>/rollback_report.md`+`.json`
   yazar.
4. **Birden fazla terfiyi geri almanız gerekiyorsa**, EN SON yapılan
   terfiden başlayarak (LIFO — son giren ilk çıkar) TEK TEK geri alın
   — her `flag_apply_report.json` yalnızca KENDİ apply'ının değiştirdiği
   özellik(ler)in `previous_state`'ini bilir.
5. **Doğrulayın:** `cat infra/monitoring/governance/pilot_flags_state.json`
   ile beklenen duruma döndüğünü teyit edin; `git diff` ile de
   kontrol edilebilir (bu dosya normal `git add` ile izlenir).
6. **`GOV_EMERGENCY_LEGITIMACY_REQUIRED` promote edilmişken geri
   alınırsa:** çift-kapı tasarımı sayesinde, rollback SONRASI ortam
   değişkeni hâlâ `1` olsa bile zorlayıcı davranış OTOMATİK olarak
   devre dışı kalır (`is_feature_promoted` artık `False` döner) — EK
   bir "env var'ı da kapat" adımı GEREKMEZ, ama netlik için yine de
   kapatılması ÖNERİLİR.

## Kalıcı (persistent) izleme profili

`infra/monitoring/profiles/persistent/` — `prometheus.yml` +
`alertmanager.yml` + `README.md` (retention/restart politikası/etiket
sözleşmesi rehberi). Bu, geliştirme/tek-seferlik-doğrulama profilinin
(`infra/monitoring/prometheus/prometheus.yml` vb.) üretim-güvenli
karşılığıdır — **bu makinede fiilen dağıtılmadı**, dosyalar hazır ve
`promtool check config` ile gerçekten doğrulandı (bkz. README.md).

## İlgili dokümanlar

- `docs/ops/SLO_MODEL_GATEWAY.md`
- `docs/ops/ALERT_PLAYBOOK_MODEL_GATEWAY.md`
- `docs/ops/MODEL_FALLBACK_RUNBOOK.md`
- `infra/monitoring/baseline/` — drift tespiti onaylı durumu
- `infra/monitoring/profiles/persistent/README.md` — kalıcı kurulum rehberi
