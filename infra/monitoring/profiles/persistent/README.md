# Kalıcı (persistent) İzleme Profili — Model Gateway

Bu dizin, `infra/monitoring/prometheus/prometheus.yml` +
`infra/monitoring/alertmanager/alertmanager.yml`'nin (geliştirme/tek
seferlik doğrulama amaçlı — bkz. `docs/ops/MONITORING_STACK_RUNBOOK.md`
"Gate D gerçek doğrulama") **üretim-güvenli, uzun-süreli çalışması
düşünülmüş** karşılığını içerir.

**Bu profil henüz bu makinede FİİLEN dağıtılmadı** — dosyalar hazır ve
geçerlidir, ama kalıcı bir kurulum (ör. bir Windows servisi olarak
sürekli çalışan Prometheus/Alertmanager) bu görevin kapsamında
GERÇEKTEN kurulmadı. `docs/ops/MONITORING_STACK_RUNBOOK.md` "Gate D
gerçek doğrulama" bölümünde anlatılan doğrulama, bu dosyaları DEĞİL,
geliştirme profilinin geçici bir varyasyonunu kullandı.

## Saklama (retention) — açık, gerekli

| Bileşen | Bayrak | Önerilen değer | Gerekçe |
|---|---|---|---|
| Prometheus | `--storage.tsdb.retention.time` | `15d` | `calibrate_alert_thresholds.py`'nin en geniş penceresi (14d) + 1 gün marj |
| Prometheus | `--storage.tsdb.path` | `data/monitoring/prometheus-tsdb` (repo dışı bir yola taşımak da düşünülebilir, disk büyümesi için) | TSDB verisinin nerede biriktiğini açıkça belirtir |
| Alertmanager | `--data.retention` | `120h` (5 gün) | Alertmanager'ın varsayılanı zaten budur — açıkça belirtmek, gelecekte "varsayılan neydi" belirsizliğini önler |
| Alertmanager | `--storage.path` | `data/monitoring/alertmanager-data` | Nüktleme/susturma (silence) durumu burada saklanır |

**Retention süresi hiçbir zaman `model_gateway_jsonl_retention_days`
(varsayılan 7 gün, bkz. `config.py`) ile karıştırılmamalıdır** — o,
`/metrics` cross-process JSONL sink'inin kendi retention'ıdır, TAMAMEN
AYRI bir mekanizmadır (bkz. `docs/ops/MONITORING_STACK_RUNBOOK.md`
"Performans ayar rehberi").

## Restart politikası rehberi

Bu projede Docker/systemd YOK (bkz. `docs/ops/MONITORING_STACK_RUNBOOK.md`
"Bu makinenin gerçek durumu") — Windows-native bir yaklaşım önerilir:

1. **En basit (elle):** `scripts/ops/start_monitoring_stack.ps1` — üç
   bileşeni de başlatır, PID'leri `reports/monitoring_stack/pids.json`'a
   yazar. Kalıcı bir kurulumda bu, bir zamanlanmış görevle (Task
   Scheduler) "sistem başlangıcında çalıştır" olarak ayarlanabilir.
2. **Otomatik yeniden başlatma isteniyorsa:** Windows Task Scheduler'da
   görev oluşturup "Eylem başarısız olursa yeniden başlat" seçeneğini
   işaretleyin (Özellikler → Ayarlar), VEYA [NSSM](https://nssm.cc/)
   (Non-Sucking Service Manager) ile `prometheus.exe`/`alertmanager.exe`'yi
   gerçek birer Windows servisine dönüştürün (bu araç bu depoya dahil
   DEĞİLDİR, ayrı bir kurulum kararı gerektirir — bu görev kapsamında
   KURULMADI, yalnızca yönlendirme olarak belirtiliyor).
3. **Sağlık kontrolü:** `scripts/ops/run_observability_gates.ps1`'in
   Gate A/B'si (`/metrics` kullanılabilirliği + scrape başarı oranı)
   periyodik olarak (bkz. "Haftalık gözden geçirme" —
   `scripts/ops/weekly_observability_review.ps1`) çalıştırılarak
   sürecin gerçekten ayakta olduğu doğrulanmalıdır.

## Etiket sözleşmesi (label conventions)

| Etiket | Geliştirme değeri | Kalıcı/üretim değeri | Not |
|---|---|---|---|
| `env` | `local-dev` | `production` | `external_labels` (Prometheus) + scrape `labels` içinde tutarlı olmalı |
| `service` | `model-gateway` | `model-gateway` | Değişmez — tüm profillerde aynı |
| `instance` | `serve_metrics-local` | `model-gateway-primary` | Birden fazla örnek çalışıyorsa (ör. `model-gateway-secondary`) ayırt edici olmalı |

Bu etiketler `infra/monitoring/baseline/metrics_manifest_v1.json`'daki
metrik şemasının BİR PARÇASI DEĞİLDİR (onlar Prometheus scrape-config
seviyesinde eklenir, `/metrics` çıktısının kendisinde değil) — bu yüzden
`scripts/ops/detect_observability_drift.py` bunları izlemez, yalnızca
bu README ile belgelenir.

## Secrets — hiçbir gerçek webhook URL'si commit edilmez

`alertmanager.yml`'deki `${ALERTMANAGER_WARNING_WEBHOOK_URL}` /
`${ALERTMANAGER_CRITICAL_WEBHOOK_URL}` **literal placeholder metinlerdir**
— Alertmanager bunları kendiliğinden bir ortam değişkeniyle DEĞİŞTİRMEZ
(bu, birçok operatörün yanlış varsaydığı bir noktadır, 2026-08-13'teki
gerçek Gate D doğrulamasında ampirik olarak doğrulandı — bkz. o
dosyadaki "ÖNEMLİ" notu). Gerçek bir dağıtımda:

- Aşama 1'de (observe-only, varsayılan) kalınıyorsa: bu iki değeri
  herhangi bir sözdizimsel olarak geçerli ama gerçek olmayan URL ile
  değiştirin (config-validation'ı geçmek için yeterli) — hiçbir yere
  asla yönlendirilmeyecekler.
- Aşama 2/3'e geçiliyorsa: gerçek webhook URL'lerini bir `envsubst`
  adımıyla veya dağıtım aracınız üzerinden, **bu depoya ASLA commit
  ETMEDEN** enjekte edin.

## İlgili dokümanlar

- `docs/ops/MONITORING_STACK_RUNBOOK.md` — "Kademeli rollout aşamaları",
  "Go/No-Go checklist", "Gate D gerçek doğrulama"
- `docs/ops/ALERT_PLAYBOOK_MODEL_GATEWAY.md` — "Post-GO haftalık ritüel"
- `infra/monitoring/baseline/` — metrik şeması + alert kuralları
  checksum baseline'ı (drift tespiti bunlara karşı çalışır)
