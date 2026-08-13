# Monitoring Stack Runbook — Model Gateway

> Bu belge, `docs/ops/SLO_MODEL_GATEWAY.md` ve
> `docs/ops/ALERT_PLAYBOOK_MODEL_GATEWAY.md`'nin dayandığı gerçek
> altyapıyı (`/metrics` endpoint'i, Prometheus scrape, Alertmanager
> routing) kurma/işletme/geri alma kılavuzudur.

## Bu makinenin gerçek durumu (dürüstçe, keşif ile doğrulandı)

Bu görev başlamadan önce kontrol edildi:

- **Docker: KURULU DEĞİL.**
- **Prometheus binary: KURULU DEĞİL.**
- **Alertmanager binary: KURULU DEĞİL.**
- **FastAPI/Flask/uvicorn: KURULU DEĞİL.**
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

## Rollout güvenlik kapıları (Gate A-D)

| Kapı | Kriter | Bu görev sırasında durum |
|---|---|---|
| A | `/metrics` 24 saat sağlıklı | **Doğrulanmadı** — yalnızca kısa süreli elle test edildi, 24 saatlik gözlem bu görevin kapsamında değil |
| B | Scrape başarı oranı ≥ %99 | **Doğrulanamaz** — bu makinede Prometheus yok |
| C | Her alert tipinden bir sentetik alarm doğrulandı | **Kısmen** — sentetik sinyal üretimi ve `/metrics`'te görünürlüğü doğrulandı; Prometheus'un bunu gerçekten `firing`/`pending`'e çevirmesi ve Alertmanager'a iletmesi **doğrulanamadı** (altyapı yok) |
| D | classify() sözleşmesinde regresyon yok | **Doğrulandı** — tam regresyon 194/194 yeşil, `classify()` dış sözleşmesi değişmedi |

**Sonuç: Gate A/B/C tam olarak sağlanamadığından, warning/critical
routing AÇILMADI.** `alertmanager.yml` bilerek Aşama 1'de (observe-only,
`null-receiver`) bırakıldı. Bu görev, Aşama 2/3'e geçiş için gereken
ALTYAPIYI (config, script'ler) hazırlar ama gerçek geçişi YAPMAZ.

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

## İlgili dokümanlar

- `docs/ops/SLO_MODEL_GATEWAY.md`
- `docs/ops/ALERT_PLAYBOOK_MODEL_GATEWAY.md`
- `docs/ops/MODEL_FALLBACK_RUNBOOK.md`
