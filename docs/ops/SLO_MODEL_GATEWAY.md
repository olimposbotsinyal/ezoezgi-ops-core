# SLO — Model Gateway

> **Durum notu (dürüstçe belirtilmeli, güncellendi):** `scripts/ops/serve_metrics.py`
> artık gerçek, bağımsız bir `/metrics` HTTP endpoint'i sunuyor (stdlib
> `http.server`, harici bağımlılık yok — bkz.
> `docs/ops/MONITORING_STACK_RUNBOOK.md`). **Ama bu makinede Prometheus/
> Alertmanager kurulu değil** (Docker da yok) — endpoint gerçek ve
> test edildi, canlı scrape/alert-firing/routing pipeline'ı ise
> **doğrulanmadı** (altyapı eksikliği nedeniyle, "başarılı" iddia
> edilmedi).
>
> **Cross-process görünürlük (güncellendi):** önceden bu endpoint
> yalnızca KENDİ sürecinde biriken metrikleri gösteriyordu (kritik bir
> mimari sınırlama, çünkü `classify()` çağrıları kısa ömürlü, ayrı
> süreçlerden yapılır). Bu artık `METRICS_SINK=jsonl_append`
> (varsayılan) ile **çözüldü**: her süreç paylaşılan bir JSONL dosyasına
> yazar, `/metrics` her çağrıda bu dosyayı okuyup birleştirir — 2 gerçek
> ayrı OS süreci + `serve_metrics.py` ile elle doğrulandı. Kalan
> ödünleşim (eventual-consistency penceresi):
> `docs/ops/MONITORING_STACK_RUNBOOK.md` "Bilinen ödünleşimler". Eski
> davranış (`METRICS_SINK=in_memory`) hâlâ opt-in olarak mevcut.
> Alternatif/tamamlayıcı: `scripts/ops/daily_gateway_smoke.ps1` ile
> düzenli aralıklarla dosyaya (`reports/daily_smoke_<UTC>/metrics_snapshot.json`)
> yazma yolu hâlâ geçerli ve gerçek üretim çağrılarını yakalar.
>
> **Performans (güncellendi):** `/metrics`, artık her scrape'te tüm
> JSONL geçmişini yeniden taramıyor — `IncrementalAggregator` +
> `CachedMetricsRenderer` (bkz. `docs/ops/MONITORING_STACK_RUNBOOK.md`
> "Performans ayar rehberi") yalnızca yeni olayları okur ve kısa süreli
> bir TTL önbelleği kullanır. Bu makinede ölçülen gerçek sonuç: p50
> scrape gecikmesi ~249ms → ~45ms (önbelleksiz, **~5.6x**), önbellek
> isabetinde ~0.004ms. Çıktı tam-yeniden-taramayla BİREBİR AYNIDIR
> (testle kanıtlı denklik) — bu yalnızca bir performans optimizasyonudur,
> SLI/SLO tanımlarını veya `classify()` sözleşmesini etkilemez.

## Kapsam

`services/model-gateway/src/model_gateway/` üzerinden geçen tüm
`classify()` çağrıları (yani `services/tr-en-bridge/src/ollama_nlu.py`'nin
gerçek üretim yolu, `client=` test enjeksiyonu hariç).

## SLI Tanımları

### 1. Availability SLI (kullanılabilirlik)

```
Availability = (başarılı classify() yanıtları) / (toplam classify() istekleri)
```

"Başarılı", `model_gateway_requests_total{result="success"}` toplamının
(hangi provider'dan gelirse gelsin) toplam istek sayısına oranıdır.
**Null-intent bir "başarısızlık" değildir** (Availability SLI açısından)
— `classify()` hiçbir zaman exception fırlatmaz, `UNKNOWN` da geçerli bir
yanıttır. Bu SLI yalnızca "sistem cevap üretebildi mi" sorusunu ölçer;
"cevap gerçek bir modelden mi geldi" sorusunu Degradation SLI ölçer.

### 2. Primary Usage SLI (bilgilendirici, sayfalamaz)

```
Primary Usage = (provider="ollama" ile SUCCESS olan istekler) / (toplam istekler)
```

Sadece bilgilendirici — düşük bir değer, CPU-verify kapısının sık sık
Ollama'yı kısıtladığını veya Ollama'nın sık çöktüğünü gösterir, ama tek
başına bir SLO ihlali değildir (Degradation SLI zaten bunu yakalar).

### 3. Degradation SLI (bozulma)

```
Degradation = (terminal null-intent yanıtları) / (toplam classify() istekleri)
```

`model_gateway_null_intent_total` toplamının toplam isteğe oranı —
kullanıcının gerçek bir model yanıtı yerine `UNKNOWN`/boş sonuç aldığı
oran.

### 4. Preflight Confidence SLI

```
Preflight Confidence = (VERIFIED sonuçlu preflight kontrolleri) / (toplam preflight kontrolleri)
```

Yalnızca `OLLAMA_CPU_VERIFY_ENABLED=true` VE `OLLAMA_CPU_VERIFY_STRICT=true`
olan ortamlarda anlamlıdır (bkz. `docs/ops/MODEL_FALLBACK_RUNBOOK.md`
"ÖNEMLİ DAVRANIŞ SONUCU") — operatör marker dosyası politikasını
uyguluyorsa bu SLI'nin yüksek olması beklenir; uygulamıyorsa **düşük
olması beklenen, normal bir durumdur**, alarm nedeni değildir (bkz.
Alert Playbook `PREFLIGHT_UNKNOWN_PERSISTENT`'in "ilk aksiyon" notu).

## Başlangıç SLO Hedefleri (config-destekli, düzenlenebilir)

| SLI | Hedef | Pencere | Config kaynağı |
|---|---|---|---|
| Availability | ≥ %99.5 | 7 gün (rolling) | `SLO_WINDOW_DAYS` |
| Degradation | ≤ %2.0 | 7 gün (rolling) | `ALERT_NULL_INTENT_CRIT` (kritik eşik, bkz. alert playbook) |
| Preflight Confidence | ≥ %90 | 7 gün (rolling) | yalnızca STRICT+marker politikası olan ortamlarda geçerli |

`SLO_WINDOW_DAYS` varsayılanı **7** — `config/model_gateway.yaml` veya
env ile değiştirilebilir.

## Error Budget ve Burn-Rate

7 günlük pencerede Availability için error budget: `%100 - %99.5 = %0.5`
(yani 10.080 dakikalık haftada ~50 dakikalık izin verilen "başarısızlık"
payı — burada "başarısızlık", Availability SLI'nin ihlali, yani
`classify()`'in hiç yanıt üretememesi/exception fırlatması anlamına
gelir; pratikte `classify()` asla exception fırlatmadığından bu SLI'nin
gerçek dünyada ihlal edilmesi son derece olası değildir — bu SLO daha çok
bir güvenlik ağı/gelecekteki mimari değişiklikler için bir üst sınır
görevi görür).

**Burn-rate örnekleri** (Google SRE burn-rate yöntemine benzer, basitleştirilmiş):

- **1 saatlik burn-rate:** Son 1 saatteki Degradation oranı, 7 günlük
  bütçenin **14.4x** hızında tükeniyor mu?
  ```
  burn_rate_1h = degradation_rate_1h / (slo_degradation_threshold / (7*24))
  ```
  Örnek: `ALERT_NULL_INTENT_CRIT=0.02` (7 gün ortalaması), son 1 saatte
  gözlenen null-intent oranı `%29` ise → `0.29 / (0.02/168) ≈ 2436x` —
  bu, **kritik/sayfalama seviyesinde** bir burn-rate'tir (bkz. Alert
  Playbook `HIGH_NULL_INTENT_RATE` kritik eşiği).
- **6 saatlik burn-rate:** Aynı formül, 6 saatlik pencere ve `6x`
  çarpanla — daha "gürültüsüz", kalıcı bozulmayı yakalamak için.

## Ne sayfalar (page), ne bilet açar (ticket)

| Durum | Aksiyon |
|---|---|
| 1h burn-rate kritik eşiği aşan `HIGH_NULL_INTENT_RATE` | **Sayfala** (on-call) — kullanıcı etkisi aktif ve hızlı büyüyor |
| `FALLBACK_SPIKE` (bkz. alert playbook) | **Sayfala** — birincil sağlayıcı muhtemelen çöküyor |
| `CIRCUIT_OPEN_STUCK` (>30 dk kapalı) | **Sayfala** — otomatik kurtarma başarısız |
| 6h burn-rate warning eşiği | **Bilet aç** — trend izlenmeli ama acil değil |
| `PRIMARY_RESTRICTED_PERSISTENT` (STRICT+marker eksik, beklenen durum) | **Bilet aç** (yalnızca operatör marker dosyası oluşturmayı unuttuysa) — bkz. "Operatör hızlı aksiyonlar" |
| `PREFLIGHT_UNKNOWN_PERSISTENT` | **Bilet aç** — genellikle beklenen, yalnızca izleme amaçlı |

## İlgili dokümanlar

- `docs/ops/ALERT_PLAYBOOK_MODEL_GATEWAY.md` — tam alert listesi + ilk aksiyonlar
- `docs/ops/MODEL_FALLBACK_RUNBOOK.md` — CPU-verify kapısı, rollback
- `services/model-gateway/src/model_gateway/metrics.py` — metrik kaynak kodu
