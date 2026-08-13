# Alert Playbook — Model Gateway

> **Durum notu:** Bu belgedeki alert'ler `infra/monitoring/prometheus/model_gateway_alerts.yaml`'da
> örnek Prometheus alerting-rule formatında tanımlı, ama şu an bunları
> **gerçekten değerlendiren canlı bir Prometheus/Alertmanager kurulumu
> yok** (bkz. `docs/ops/SLO_MODEL_GATEWAY.md` durum notu). Bugün fiilen
> çalışan mekanizma: `scripts/ops/daily_gateway_smoke.ps1`, bu
> eşiklerin bir alt kümesini `metrics_snapshot.json` üzerinden kendisi
> değerlendirip exit code (0/1/2) ile "aksiyon gerekiyor mu" sinyali
> verir — bkz. altta her alert'in "Daily smoke karşılığı" satırı.

## Operatör hızlı aksiyonlar (özet)

| Belirti | Hızlı aksiyon |
|---|---|
| **Marker eksik** | `docs/ops/MODEL_FALLBACK_RUNBOOK.md` "Operatör kontrol listesi" — marker dosyası oluştur, veya `OLLAMA_CPU_VERIFY_STRICT=false` |
| **Primary restricted (Ollama hiç denenmiyor)** | `OLLAMA_CPU_PREFLIGHT_CHECKED`/`OLLAMA_PRIMARY_RESTRICTED` audit kayıtlarını `trace_id` ile incele; marker eksikse yukarıya bak |
| **Null-intent spike** | `data/audit/audit.log.jsonl`'de son `FALLBACK`/`EXHAUSTED` kayıtlarının `reason_code`'una bak — `RUNTIME_CRASH` ise B036 bilinen deseni, `docs/ops/OLLAMA_WORKAROUND_CPU_ONLY.md` |
| **Fallback exhausted (hiçbir sağlayıcı çalışmıyor)** | `python -c "from model_gateway.router import get_router; print(get_router().healthcheck_all())"` ile canlı durumu kontrol et |

---

## HIGH_NULL_INTENT_RATE

- **Koşul:** `null_intent_total` oranı (toplam isteğe göre) eşiği aşıyor.
- **Bakış penceresi:** 1 saat (kritik), 6 saat (warning) — bkz.
  `docs/ops/SLO_MODEL_GATEWAY.md` burn-rate örnekleri.
- **Eşikler:** `ALERT_NULL_INTENT_WARN=0.01` (%1), `ALERT_NULL_INTENT_CRIT=0.02` (%2).
- **Şiddet:** warning → bilet; critical → **sayfala**.
- **Runbook:** bu belge + `docs/ops/MODEL_FALLBACK_RUNBOOK.md` "Olay aksiyonları".
- **Önerilen ilk aksiyon:** Son `EXHAUSTED`/`FALLBACK` audit kayıtlarının
  `reason_code` dağılımına bak — `RUNTIME_CRASH` domine ediyorsa B036
  (Vulkan çöküşü), `PRIMARY_RESTRICTED_CPU_UNVERIFIED` domine ediyorsa
  marker dosyası eksik/eski.
- **Daily smoke karşılığı:** `daily_gateway_smoke.ps1`, snapshot'taki
  `null_intent_total` / `requests_total` oranını `ALERT_NULL_INTENT_CRIT`
  ile karşılaştırır; aşarsa exit code **2**.

## FALLBACK_SPIKE

- **Koşul:** `fallback_total` artış hızı, son 24 saatlik ortalamanın
  `ALERT_FALLBACK_SPIKE_MULTIPLIER` (varsayılan **3.0x**) katından fazla.
- **Bakış penceresi:** 15 dakika (spike'ı kısa pencerede yakalamak için).
- **Şiddet:** **sayfala** (critical) — birincil sağlayıcı muhtemelen
  aktif olarak çöküyor.
- **Runbook:** bu belge + `docs/ops/MODEL_FALLBACK_RUNBOOK.md`
  "reason_code" tablosu.
- **Önerilen ilk aksiyon:** `fallback_total{from_provider="ollama"}`
  etiketindeki `reason_code` dağılımına bak.
- **Daily smoke karşılığı:** Şu an **yok** — bu, kısa-pencereli bir
  spike tespiti gerektirir ve günde bir kez çalışan bir smoke script'in
  doğal kapsamı dışında (bkz. "Bilinen sınırlamalar"). Canlı bir
  Prometheus kurulduğunda `model_gateway_alerts.yaml`'daki kural
  doğrudan kullanılabilir.

## PRIMARY_RESTRICTED_PERSISTENT

- **Koşul:** `restrictions_total{provider="ollama",reason_code="PRIMARY_RESTRICTED_CPU_UNVERIFIED"}`
  son N kontrolün tamamında (ör. son 24 saatteki her preflight kontrolünde) mevcut.
- **Bakış penceresi:** 24 saat.
- **Şiddet:** **bilet** (genellikle "beklenen ama unutulmuş" bir durum —
  marker dosyası hiç oluşturulmamış).
- **Runbook:** `docs/ops/MODEL_FALLBACK_RUNBOOK.md` "Operatör kontrol listesi".
- **Önerilen ilk aksiyon:** Marker dosyasının var olup olmadığını
  kontrol et: `Test-Path $env:OLLAMA_CPU_MARKER_FILE`.
- **Daily smoke karşılığı:** `daily_gateway_smoke.ps1`, preflight
  snapshot'ında `status != VERIFIED` VE `STRICT=true` ise bunu
  `summary.md`'de açıkça not eder ve exit code en az **1** döner
  (degraded but operating — bu STRICT modda beklenen/kasıtlı davranış
  olabilir, bu yüzden 2 değil 1).

## PREFLIGHT_UNKNOWN_PERSISTENT

- **Koşul:** `preflight_total{status="UNKNOWN"}` oranı sürekli yüksek.
- **Bakış penceresi:** 24 saat.
- **Şiddet:** **bilet**, genellikle bilgilendirici (bkz.
  `docs/ops/SLO_MODEL_GATEWAY.md` Preflight Confidence SLI notu — marker
  politikası uygulanmıyorsa bu **normaldir**).
- **Runbook:** bu belge.
- **Önerilen ilk aksiyon:** Operatörün marker dosyası politikasını
  bilerek uygulamadığını doğrula (ör. `OLLAMA_CPU_VERIFY_STRICT=false`
  seçilmişse bu alert'in önemi düşüktür).
- **Daily smoke karşılığı:** `preflight_snapshot.json`'a yazılır,
  eğilim izleme amaçlı — tek başına exit code'u değiştirmez.

## CIRCUIT_OPEN_STUCK

- **Koşul:** `model_gateway_circuit_open{provider}` gauge'u **30 dakikadan
  uzun süre** `1` (açık) kalıyor.
- **Bakış penceresi:** 30 dakika (sürekli).
- **Şiddet:** **sayfala** — `CIRCUIT_BREAKER_RESET_SEC` (varsayılan 120s)
  süresi çoktan geçmiş olmalıydı; devrenin kendiliğinden yarı-açılmaması
  ya sürekli yeni hata ya da bir kod/config sorunu olduğunu gösterir.
- **Runbook:** bu belge + `services/model-gateway/src/model_gateway/health.py`.
- **Önerilen ilk aksiyon:** `CIRCUIT_BREAKER_FAILS`/`_RESET_SEC`
  değerlerini doğrula; ilgili sağlayıcının health check'ini manuel
  çalıştır.
- **Daily smoke karşılığı:** `provider_health` snapshot'ında ilgili
  sağlayıcı sürekli `0` (unhealthy) görünüyorsa `summary.md`'de not
  edilir.

---

## Bilinen sınırlamalar

- Hiçbir alert şu an gerçekten **sayfalamıyor** — canlı bir
  Prometheus/Alertmanager/PagerDuty entegrasyonu yok. Bu belge ve
  `model_gateway_alerts.yaml`, böyle bir altyapı eklendiğinde
  doğrudan kullanılabilecek şekilde hazırlandı.
- `FALLBACK_SPIKE`, kısa-pencereli (15 dakika) bir tespit gerektirir —
  günde bir kez çalışan `daily_gateway_smoke.ps1` bunu doğal olarak
  yakalayamaz; yalnızca canlı bir metrik toplama/alerting pipeline'ı ile
  anlamlı olur.
- Eşikler (`ALERT_NULL_INTENT_WARN/CRIT`, `ALERT_FALLBACK_SPIKE_MULTIPLIER`)
  ilk tahminlerdir (bkz. `docs/ops/SLO_MODEL_GATEWAY.md`) — gerçek
  üretim verisi biriktikçe kalibre edilmesi beklenir.
