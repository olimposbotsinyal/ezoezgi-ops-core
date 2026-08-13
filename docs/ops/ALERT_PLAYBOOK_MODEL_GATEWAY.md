# Alert Playbook — Model Gateway

> **Durum notu (güncellendi):** `scripts/ops/serve_metrics.py` artık
> gerçek, test edilmiş bir `/metrics` endpoint'i sunuyor ve
> `scripts/ops/verify_alert_pipeline.ps1` E2E pipeline'ı gerçekten
> çalıştırıyor — ama bu makinede Prometheus/Alertmanager **kurulu değil**
> (Docker da yok, keşifle doğrulandı). E2E script bu makinede dürüstçe
> **exit code 1 (kısmi)** döner: metrics endpoint çalışıyor ve sentetik
> sinyal görünür durumda, ama Prometheus'un alert'i `firing`/`pending`'e
> çevirip Alertmanager'a iletmesi **doğrulanamadı** çünkü ikisi de kurulu
> değil. Rollout Aşama 1'de (observe-only) kalıyor — bkz.
> `docs/ops/MONITORING_STACK_RUNBOOK.md` "Rollout güvenlik kapıları".
> Alternatif/tamamlayıcı mekanizma: `scripts/ops/daily_gateway_smoke.ps1`,
> bu eşiklerin bir alt kümesini `metrics_snapshot.json` üzerinden kendisi
> değerlendirip exit code (0/1/2) ile "aksiyon gerekiyor mu" sinyali
> verir — bkz. altta her alert'in "Daily smoke karşılığı" satırı.
>
> **Go-live paketi eklendi:** `scripts/ops/run_observability_gates.ps1`
> (5 gate: metrics availability, scrape success, sentetik alert
> görünürlüğü, Alertmanager alma yolu, classify regresyon smoke),
> `scripts/ops/calibrate_alert_thresholds.py` (eşik kalibrasyonu),
> `scripts/ops/build_observability_signoff.py` (GO/NO-GO imza paketi),
> `scripts/ops/rollback_observability.ps1` (varsayılan dry-run rollback
> drill). Ayrıntı + "Go/No-Go checklist": `docs/ops/MONITORING_STACK_RUNBOOK.md`.

## Triaj karar ağacı

```
Alert geldi
  │
  ├─ /metrics erişilebilir mi? (curl http://127.0.0.1:9108/metrics)
  │    │
  │    ├─ HAYIR → serve_metrics.py çalışmıyor/çökmüş olabilir.
  │    │           Süreci yeniden başlat, PID/loglara bak.
  │    │           BU DURUMDA TÜM ALERT'LER "kör" olabilir → ESCALATE.
  │    │
  │    └─ EVET → aşağı devam et
  │
  ├─ Alert `synthetic="true"` etiketli mi (test/kalibrasyon kaynaklı)?
  │    │
  │    ├─ EVET → GERÇEK bir olay değil. Silence uygun (bkz. "Sustur vs
  │    │          Yükselt"). Sentetik testin kim/ne zaman çalıştırdığını
  │    │          doğrula (verify_alert_pipeline.ps1 / run_observability_gates.ps1).
  │    │
  │    └─ HAYIR → gerçek sinyal, aşağı devam et
  │
  ├─ `data/audit/audit.log.jsonl`'de son ilgili kayıtların `reason_code`'u ne?
  │    │
  │    ├─ RUNTIME_CRASH → B036 bilinen deseni (Vulkan çöküşü).
  │    │                   docs/ops/OLLAMA_WORKAROUND_CPU_ONLY.md'ye bak,
  │    │                   CPU-only workaround'ı değerlendir.
  │    │
  │    ├─ PRIMARY_RESTRICTED_CPU_UNVERIFIED → marker dosyası eksik/eski
  │    │                   VEYA STRICT modda kasıtlı davranış. Operatör
  │    │                   kontrol listesine bak (MODEL_FALLBACK_RUNBOOK.md).
  │    │
  │    ├─ POLICY_BLOCK / DISABLED → beklenen/kasıtlı config durumu,
  │    │                   genellikle gerçek bir olay DEĞİL.
  │    │
  │    └─ FALLBACK_EXHAUSTED → TÜM sağlayıcılar başarısız → CIDDI,
  │                       hemen ESCALATE (kullanıcı etkisi aktif).
  │
  └─ Şiddet neydi (bkz. her alert bölümündeki "Şiddet")?
       ├─ bilet → bilet aç, iş saatinde takip yeterli
       └─ sayfala (critical) → ESCALATE (bkz. aşağıdaki bölüm)
```

## Sustur (silence) vs Yükselt (escalate)

| Durum | Aksiyon | Gerekçe |
|---|---|---|
| Alert `synthetic="true"` etiketli | **Sustur** | Test/kalibrasyon sinyali, gerçek kullanıcı etkisi yok |
| Bilinen, zaten takip edilen bir olayın (ör. B036) devamı, yeni bulgu yok | **Sustur** (mevcut olay bileti altında not düş) | Aynı kök nedenin tekrar sayfalanması gürültü yaratır — `inhibit_rules` (alertmanager.yml) bunu kısmen otomatikleştirir |
| `PREFLIGHT_UNKNOWN_PERSISTENT`, `OLLAMA_CPU_VERIFY_STRICT=false` kasıtlı seçilmişken | **Sustur** | Belgelenmiş, kasıtlı bir config durumu (bkz. bu alert'in bölümü) |
| `FALLBACK_EXHAUSTED` reason_code'u audit'te görünüyor | **Yükselt (hemen)** | Tüm sağlayıcılar başarısız — kullanıcı etkisi aktif |
| `HIGH_NULL_INTENT_RATE` kritik eşiği aşan, yeni/bilinmeyen bir `reason_code` dağılımı | **Yükselt** | Bilinen desenlerden (B036, marker eksikliği) biri değilse kök neden bilinmiyor demektir |
| `CIRCUIT_OPEN_STUCK` 30dk+ | **Yükselt** | Kendiliğinden yarı-açılması gereken bir mekanizma çalışmıyor demektir — kod/config sorunu şüphesi |
| `/metrics` kendisi erişilemez | **Yükselt (hemen)** | Gözlemlenebilirlik körleşmiş — diğer TÜM alert'lerin güvenilirliği şüpheli |

## İlk 10 dakika kontrol listesi (her alert için ortak)

1. **Doğrula:** `curl http://127.0.0.1:9108/metrics | Select-String <ilgili_metrik>` (veya tarayıcıda aç) ile alert'in dayandığı metriğin GERÇEKTEN o değeri gösterdiğini doğrula — Prometheus/Alertmanager arasında bir gecikme/senkronizasyon sorunu olabilir.
2. **Etiketle:** Alert `synthetic="true"` mü? (yukarıdaki karar ağacına bak) — evetse dur, sentetik kaynağı doğrula, sustur.
3. **Bağlamla:** `data/audit/audit.log.jsonl`'in SON 15 dakikasını incele (`Get-Content data\audit\audit.log.jsonl -Tail 50 | ConvertFrom-Json` veya `package_gateway_incident.ps1` ile otomatik dilim al) — `reason_code`/`trace_id` dağılımına bak.
4. **Kapsamı ölç:** Kaç istek etkilendi? (`model_gateway_requests_total` toplamına oranla) — tek bir istek mi, sürekli bir desen mi?
5. **Bilinen desen mi?:** Yukarıdaki triaj karar ağacındaki bilinen `reason_code`'lardan biri mi (RUNTIME_CRASH/B036, marker eksikliği)? Öyleyse ilgili runbook bölümüne geç.
6. **Kanıt topla (şüpheliyse):** `scripts/ops/package_gateway_incident.ps1` çalıştır (aşağıdaki komut örneklerine bak) — sonraki adımlardan BAĞIMSIZ olarak kanıtı ERKEN topla (durum değişmeden önce).
7. **Karar ver:** Sustur mu, yükselt mi? (yukarıdaki tablo) — belirsizsen VARSAYILAN olarak yükselt (sessizce görmezden gelmek her zaman daha risklidir).

## Kanıt paketi (evidence bundle) komut örnekleri

```powershell
# Olay kanıt paketi (audit dilimi + config parmak izi + git SHA) -- secret'lar maskeli
powershell -ExecutionPolicy Bypass -File scripts\ops\package_gateway_incident.ps1

# Go-live gate'lerini yeniden çalıştır (bir olay SONRASI "hala sağlıklı mı" doğrulaması için)
powershell -ExecutionPolicy Bypass -File scripts\ops\run_observability_gates.ps1

# Sign-off/durum dosyası (git SHA + test özeti + en son gate sonucu + rollback referansı)
.venv\Scripts\python.exe scripts\ops\build_observability_signoff.py

# Alert eşiklerini son 24 saatlik gerçek veriyle yeniden kalibre et (olay sonrası eşik gözden geçirme)
.venv\Scripts\python.exe scripts\ops\calibrate_alert_thresholds.py --hours 24
```

## Olay sonrası not şablonu (post-incident note)

Her ESCALATE edilen (veya tekrarlayan/gürültülü) alert için, olay bileti/BACKLOG.md'ye şu şablonla bir not düşülmeli:

```markdown
### <Alert adı> -- <UTC zaman damgası>

- **Tetikleyen metrik/eşik:** <metrik adı + gözlenen değer vs eşik>
- **Gerçek mi/sentetik mi:** <synthetic="true" etiketi var mıydı?>
- **Kök neden (reason_code / audit bulgusu):** <ör. RUNTIME_CRASH, FALLBACK_EXHAUSTED>
- **Kapsam:** <kaç istek/kullanıcı etkilendi, ne kadar sürdü>
- **Aksiyon alındı:** <sustur / escalate / workaround uygulandı / kod değişikliği>
- **Kanıt paketi:** <reports/incidents/gateway_<timestamp>/ yolu>
- **Kalıcı düzeltme gerekiyor mu:** <EVET/HAYIR -- gerekiyorsa BACKLOG.md'ye madde ekle>
- **Eşik kalibrasyonu gerekiyor mu:** <bu olay eşiklerin çok hassas/çok gevşek olduğunu mu gösterdi?>
```

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

## İlk vs kalibre edilmiş eşikler

`scripts/ops/calibrate_alert_thresholds.py` **v1** (post-GO sertleştirmesi),
24h/7d/14d pencerelerinden (sentetik `synthetic="true"` etiketli
olanlar HARİÇ) WARN/CRIT önerileri + bir **GÜVEN SKORU** (LOW/MEDIUM/HIGH)
üretir — bkz. `reports/alert_calibration_<UTC>/calibration_v1.md` +
`calibration_v1.json`. Bu makinede (gerçek üretim trafiği yok)
çalıştırıldığında sonuç `INSUFFICIENT_DATA`/`LOW` güven olur ve mevcut
varsayılanlar KORUNUR — bu, kalibre edilmemiş bir eksiklik değil, dürüst
bir "henüz yeterli veri yok" bulgusudur:

| Alert | İlk (kod-içi) varsayılan | Kalibre edilmiş (bu makinede) | Güven | Not |
|---|---|---|---|---|
| HIGH_NULL_INTENT_RATE | WARN=0.01, CRIT=0.02 | `INSUFFICIENT_DATA` — ilk varsayılan korunuyor | LOW | Gerçek trafik birikince yeniden çalıştırın |
| FALLBACK_SPIKE | multiplier=3.0x | `INSUFFICIENT_DATA` — ilk varsayılan korunuyor | LOW | Kısa-pencereli (15dk) tespit, canlı Prometheus gerektirir |
| PRIMARY_RESTRICTED_PERSISTENT | tüm kontroller (oran=1.0) | `INSUFFICIENT_DATA` — ilk varsayılan korunuyor | LOW | Ayrı bir eşik ortam değişkeni yok |
| PREFLIGHT_UNKNOWN_PERSISTENT | tüm kontroller (oran=1.0) | `INSUFFICIENT_DATA` — ilk varsayılan korunuyor | LOW | Ayrı bir eşik ortam değişkeni yok |

Go-live SONRASI, ilk gerçek trafik penceresi biriktiğinde bu tablo
`calibrate_alert_thresholds.py`'nin GERÇEK çıktısıyla güncellenmelidir
— bu script tek seferlik bir kurulum adımı değildir, **haftalık
gözden geçirme ritüelinin** (bkz. `docs/ops/MONITORING_STACK_RUNBOOK.md`
"Post-GO weekly ritual") bir parçası olarak periyodik yeniden
çalıştırılması önerilir.

## Eşik güncellemelerini nasıl onaylarım (approval SOP)

**Politika koruması (yapısal, yalnızca belgesel değil):** Ne
`calibrate_alert_thresholds.py`'de ne de `generate_threshold_proposals.py`'de
config'i/dosyayı OTOMATİK OLARAK değiştiren HİÇBİR kod yolu YOKTUR —
her ikisi de yalnızca birer **ÖNERİ** üretir. Bir eşiği GERÇEKTEN
uygulamanın TEK onaylı yolu, `scripts/ops/apply_threshold_proposal.ps1`'in
**dört zorunlu uygunluk kuralını** (şema geçerli + review=APPROVE +
proposal_id eşleşiyor + checksum eşleşiyor) sağladığı, insan onaylı bir
proposal → review → apply zinciridir (tam RACI + acil durum yolu için
bkz. `docs/ops/MONITORING_STACK_RUNBOOK.md` "Eşik Değişikliği SOP").

1. **Öneri üretin:** `python scripts/ops/generate_threshold_proposals.py`
   — gerçek JSONL metrik verisinden (`calibrate_alert_thresholds.py`'nin
   24h/7d/14d kalibrasyon mantığını sarmalar) `reports/threshold_proposals/<proposal_id>/proposal.json`
   üretir. Yalnızca güven=CALIBRATED VE mevcut değerden GERÇEKTEN farklı
   öneriler için proposal üretilir.
2. **İnceleyin:** `proposal.json`'daki her alan için:
   - **`confidence` HIGH mı?** — değilse (LOW/MEDIUM), uygulamadan önce
     EK bir gözlem penceresi (bir sonraki haftalık çalıştırma) bekleyin.
   - **`sample_adequacy`** "YETERLİ" mi, yoksa "SINIRDA" mı?
   - **`risk_note`**'u okuyun — yanlış-pozitif riski + değişiklik-etkisi
     bir arada.
   - **`evidence_paths`**'teki kalibrasyon çıktısına bakarak eşiğin
     gevşetildiğini mi sıkılaştırıldığını mı doğrulayın.
3. **Onaylayın (review_record oluşturun):** Yukarıdaki kriterler
   tatmin ediciyse, `python scripts/ops/create_threshold_review_record.py
   --proposal-path <proposal.json> --reviewer <adınız> --decision APPROVE
   --rationale "<gerekçe>"` çalıştırın — `reports/threshold_reviews/<proposal_id>/review_record.json`
   üretir. Onaylayan, öneriyi üretenden FARKLI bir kişi olmalıdır (ikinci
   göz ilkesi — bkz. RACI tablosu).
4. **Önce dry-run, sonra gerçek uygulama:** `pwsh scripts/ops/apply_threshold_proposal.ps1
   -ProposalPath <proposal.json> -ReviewRecordPath <review_record.json>`
   (VARSAYILAN: dry-run, hiçbir dosya değişmez) — çıktıyı kontrol edin,
   ardından AYNI komutu `-Apply` ekleyerek tekrar çalıştırın. Başarılı
   bir apply: dosyayı yamalar, yedek alır, `approved_checksums_ledger.jsonl`'e
   kaydeder (drift detector artık bunu CRITICAL SAYMAZ), gerçek bir
   denetim kaydı (`data/audit/audit.log.jsonl`) ekler.
5. **Kaydedin:** `docs/BACKLOG.md`'ye uygulanan değişikliği (eski
   değer → yeni değer, gerekçe, `proposal_id`) not düşün.
6. **Doğrulayın:** Bir sonraki haftalık gözden geçirmede (`weekly_observability_review.ps1`),
   yeni eşiğin beklenen davranışı (daha az/çok alarm) gösterip
   göstermediğini kontrol edin — beklenmedik bir sonuç varsa
   `rollback_threshold_apply.ps1 -ApplyReportPath <apply_report.json> -Apply`
   ile geri alın.

**Reddetme kriterleri (REJECT/NEEDS_DATA verin, apply hiçbir zaman
çalışmaz çünkü decision≠APPROVE/APPROVE_EMERGENCY ile engellenir):** güven
LOW ise; örnek boyutu "YETERSİZ" ise; önerilen değer mevcut değerin ÇOK
altındaysa (aşırı hassas eşik riski — script zaten bunu `max(gözlenen*çarpan,
mevcut)` ile önler, ama yine de gözden geçirin); yakın zamanda (son 48
saat) büyük bir trafik-deseni değişikliği (ör. yeni bir entegrasyon,
sağlayıcı değişikliği) olduysa.

**Acil durum (`--decision APPROVE_EMERGENCY`):** normal review turu
beklenemeyecek kadar acil bir gürültü/yanlış-alarm firtinasi varsa, bu
karar TEK bir mühendisin kendi onayını kaydetmesine izin verir — ama
`--incident-id`/`--justification`/`--timebox-hours` (maks 24)/
`--retro-review-due-utc` alanlarının TÜMÜ ZORUNLUDUR ve MAKİNE
TARAFINDAN doğrulanır (bkz. `docs/ops/MONITORING_STACK_RUNBOOK.md`
"Emergency Change Protocol (Enforced)" — tam RACI, komut örneği, ve
retroaktif inceleme vadesinin kaçırılması durumunda OTOMATİK CRITICAL
drift üretimi orada belgelidir).

**v1.2 PİLOT özellikleri (hepsi VARSAYILAN KAPALI — flag ile açılmadıkça
hiçbir davranış değişmez):** checksum-zinciri farkındalıklı vade takibi
(`GOV_EMERGENCY_CHAIN_MATCHING`), VerifyReload FAIL'de opsiyonel otomatik
geri alma (`GOV_AUTO_ROLLBACK_ON_VERIFY_FAIL` / `-AutoRollbackOnVerifyFail`),
ve acil durum bilet-format ön-kontrolü (`check_emergency_legitimacy.py`).
Tam flag tablosu + güvenli deneme adımları: `docs/ops/MONITORING_STACK_RUNBOOK.md`
"Pilot Flags Matrix (default OFF)".

**GÜNCELLENDİ — resmi terfi (promotion) kapısı eklendi:** `--decision
APPROVE_EMERGENCY` akışında `legitimacy_report_path` artık **GERÇEKTEN
ENGELLEYİCİ** olabilir — ama YALNIZCA `emergency_legitimacy_required`
özelliği `scripts/ops/evaluate_pilot_promotion.py` tarafından ÖLÇÜLEBİLİR
kriterlerle `PROMOTE` edildiğinde VE bu terfi `scripts/ops/promote_pilot_flags.ps1
-Apply` ile GERÇEKTEN uygulandığında (`pilot_flags_state.json`'da
kayıtlı) — `GOV_EMERGENCY_LEGITIMACY_REQUIRED=1` ortam değişkenini TEK
BAŞINA ayarlamak HİÇBİR ŞEYİ değiştirmez. Engellendiğinde hata mesajı
AÇIKÇA nedeni gösterir (rapor eksik/okunamadı/`FAIL`). Tam politika +
karar matrisi + güvenli geri alma: `docs/ops/MONITORING_STACK_RUNBOOK.md`
"Pilot Promotion Policy".

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
  ilk tahminlerdir (yukarıdaki "İlk vs kalibre edilmiş eşikler" tablosuna
  bakın) — gerçek üretim verisi biriktikçe `calibrate_alert_thresholds.py`
  ile kalibre edilmesi beklenir.
- **GÜNCELLENDİ:** `/metrics` endpoint'i artık süreç-içi bir singleton'a
  BAĞIMLI DEĞİL — `METRICS_SINK=jsonl_append` (varsayılan) ile
  cross-process görünürlük sağlanıyor (bkz.
  `docs/ops/MONITORING_STACK_RUNBOOK.md` "ÇÖZÜLDÜ (büyük ölçüde):
  süreç-içi metrik izolasyonu"). Kalan gerçek ödünleşim eventual-consistency
  penceresidir (gerçek zamanlı push değil), kritik bir sınırlama değildir.
- **ÇÖZÜLDÜ (2026-08-13):** Go-live Gate D (Alertmanager alma yolu) —
  bu makinede kalıcı bir Alertmanager kurulumu hâlâ YOK, ama gerçek
  Prometheus v3.13.2 + Alertmanager v0.33.1 ile TAM bir uçtan uca
  doğrulama yapıldı (sentetik sinyal → gerçek `firing` → Alertmanager
  gerçekten aldı, observe-only/`null-receiver` korunarak) — bkz.
  `docs/ops/MONITORING_STACK_RUNBOOK.md` "Gate D gerçek doğrulama".
  Kalıcı altyapı kurulmadığından, her yeni gate koşusunda Alertmanager
  ayrıca başlatılmadıysa Gate D yine **SKIPPED** döner (fabrike edilmiş
  bir PASS asla üretilmez) — bu beklenen, dürüst bir davranıştır.
- **ÇÖZÜLDÜ (B037, 2026-08-13):** `tools/cli-runner/src/runner.py`,
  `shutil.which("echo")` başarısız olduğunda artık saf-Python bir
  eşdeğere düşüyor (Git Bash'te de saf PowerShell'de de birebir aynı
  davranış) — bkz. `docs/ops/MONITORING_STACK_RUNBOOK.md` "B037
  düzeltmesi". Tam suite artık her iki kabukta da yeşil.
