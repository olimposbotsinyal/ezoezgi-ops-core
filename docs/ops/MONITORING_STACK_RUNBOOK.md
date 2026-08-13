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
| `/metrics` HTTP endpoint | `scripts/ops/serve_metrics.py` | **Gerçek, elle test edildi** — başlatıldı, `curl`landı, 200/404 davranışları doğrulandı |
| Prometheus scrape config | `infra/monitoring/prometheus/prometheus.yml` | Geçerli YAML, ama hiçbir Prometheus süreci okumuyor |
| Alertmanager routing | `infra/monitoring/alertmanager/alertmanager.yml` | Geçerli YAML, ASAMA 1 (observe-only), hiçbir Alertmanager süreci okumuyor |
| Sentetik sinyal üreticisi | `scripts/ops/emit_synthetic_gateway_signals.py` | **Gerçek, elle test edildi** — hem yerel registry'ye hem HTTP üzerinden çalışan bir sürece enjeksiyon doğrulandı |
| E2E doğrulama | `scripts/ops/verify_alert_pipeline.ps1` | **Gerçek, elle çalıştırıldı** — bu makinede exit code **1** (kısmi) üretti, çünkü Prometheus/Alertmanager yok. **0 fabrike edilmedi.** |

## KRİTİK mimari sınırlama: süreç-içi metrik izolasyonu

`model_gateway.metrics.get_metrics()` **süreç-içi bir singleton'dur.**
`scripts/ops/serve_metrics.py`, kendi başına, ayrı bir süreç olarak
çalışır. Bu projede `classify()` çağrıları (gerçek üretim kullanımı)
kısa ömürlü, **ayrı** süreçlerden yapıldığından, `serve_metrics.py`'nin
`/metrics` çıktısı **gerçek üretim trafiğini varsayılan olarak
YAKALAMAZ.**

Bu, geliştirme sırasında elle test edilirken keşfedildi: sentetik sinyal
üreticisi başlangıçta ayrı bir alt-süreç olarak metrik enjekte ediyordu
ve `/metrics` çıktısında **hiçbir zaman görünmüyordu** (boş kaldı).
Düzeltme: `serve_metrics.py`'ye `--enable-debug-injection` ile açılan bir
`POST /debug/inject_synthetic` yolu eklendi — `emit_synthetic_gateway_signals.py --target-url ...`
artık AYNI sürece HTTP üzerinden yazıyor, böylece E2E test senaryosu
kendi içinde tutarlı. **Bu, gerçek üretim trafiğinin görünürlüğü
sorununu çözmez** — yalnızca E2E test/doğrulama senaryosunu düzeltir.

Gerçek üretim trafiğinin metriklerini görmek için iki gerçekçi yol var:
1. `scripts/ops/daily_gateway_smoke.ps1` — her koşuda gerçek bir
   `classify()` probu çalıştırır ve sonucu dosyaya yazar (zaten mevcut,
   B036 görev serisinden).
2. İleride kalıcı bir servis süreci eklenirse (mimari genişleme, bu
   görevin kapsamı dışında), `serve_metrics.py`'nin mantığı o sürece
   doğrudan entegre edilebilir.

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
| `/metrics` boş dönüyor | Hiç metrik enjekte edilmedi (fresh süreç) VEYA sentetik sinyal AYRI bir süreçten enjekte edildi | `scripts/ops/emit_synthetic_gateway_signals.py --target-url` kullanın, yerel enjeksiyon değil |
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
