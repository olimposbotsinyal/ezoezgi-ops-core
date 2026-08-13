# Governance v1.2 Pilot Özeti

Üretildi (UTC): 2026-08-13T18:05:00+00:00
Repo: `D:\Projects\ezoezgi-ops`, branch `main`
Yürüten: Bridge Agent (bu görev — "Governance v1.2 pilot trials")

Bu klasör, v1.2 pilot özelliklerinin **gerçek** kod yollarıyla, **gerçek**
dosya mutasyonları/geri almalarla test edildiğinin kanıtıdır — hiçbir
sonuç fabrike edilmedi. Tüm testler, çalıştırıldıktan sonra repo'yu
pristine (baseline ile bit-bit aynı) duruma geri getirecek şekilde
temizlendi; yalnızca bu özet + alt klasörlerdeki kanıt dosyaları kalıcı
olarak saklanıyor.

## Senaryo 1 — Chain-matching comparison (v1.1 vs v1.2)

**Kurulum (gerçek, uçtan uca):**
1. Gerçek bir `APPROVE_EMERGENCY` proposal+review oluşturuldu
   (`incident_id=OPS-5001`, `retro_review_due_utc` bilerek GEÇMİŞ bir
   tarihe ayarlandı ki karşılaştırma HEMEN anlamlı olsun — kod yolu
   gerçek, yalnızca zaman parametresi demoyu hemen tetikleyecek şekilde
   seçildi).
2. `apply_threshold_proposal.ps1 -Apply` ile GERÇEKTEN uygulandı.
3. `rollback_threshold_apply.ps1 -Apply` ile GERÇEKTEN geri alındı
   (dosya baseline'a döndü, ledger girdisi A kalıcı kaldı).
4. AYNI alert için, TAMAMEN ALAKASIZ, normal bir `APPROVE` proposal
   GERÇEKTEN uygulandı (ledger girdisi B — `old_checksum`'i, RESTORE
   EDİLMİŞ baseline'dan devam ediyor, girdi A'nın `new_checksum`'inden
   DEĞİL).
5. Bu da GERÇEKTEN geri alındı (pristine'e dönüş).

**Sonuç (`scenario1_chain_matching/chain_eval.md` + `drift_detector_comparison.txt`):**

| Kontrol | v1.1 (alert_name-only) | v1.2 (checksum-chain) |
|---|---|---|
| `run_emergency_chain_trial.py` sınıflandırması | **RESOLVED** | **BROKEN_CHAIN** |
| `check_emergency_review_overdue_drift` finding | (yok — sessizce "çözüldü") | **WARN** — "checksum ZİNCİRİ SÜREKSİZ, elle doğrulayın" |

**Gözlem:** v1.1, "aynı alert + sonraki tarih" kuralı yüzünden, acil
durumun GERÇEKTEN doğru şekilde takip edildiğini VARSAYDI — oysa
gerçekte emergency değişiklik geri alınmış, sonra TAMAMEN alakasız bir
normal değişiklik yapılmıştı. Bu, gerçek bir YANLIŞ-POZİTİF "çözüldü"
senaryosudur. v1.2, checksum zincirinin kırık olduğunu doğru tespit
edip WARN üretti.

**Risk:** v1.2'nin WARN'ı bazı meşru "önce geri al, sonra tamamen
ilgisiz bir kalibrasyon yap" senaryolarında da tetiklenecektir (yanlış-
pozitif WARN, yanlış-pozitif "çözüldü" değil) — ama bu YALNIZCA WARN'dır
(CRITICAL değil), yani gürültü maliyeti düşük, güvenlik kazancı yüksek.

## Senaryo 2 — VerifyReload FAIL: auto-rollback OFF → ON

**Kurulum (gerçek):** Gerçek bir `APPROVE` proposal, gerçek `amtool.exe`
ile (`infra/monitoring/profiles/persistent/alertmanager.yml`'deki
bilinen `${VAR}` yer tutucusu sınırlaması, Commit R/V'den) GERÇEK bir
`VerifyReload` FAIL'i tetiklendi.

**A) `-AutoRollbackOnVerifyFail` OLMADAN (varsayılan):**
`scenario2_verify_fail_autorollback/autorollback_OFF/apply_report.json`
— `verification_state=FAIL`, `auto_rollback=null`, dosya checksum'i
`new_checksum` ile eşleşti (yani **UYGULANMIŞ HALDE KALDI**, manuel
`rollback_threshold_apply.ps1` ile geri alınması gerekti).

**B) `-AutoRollbackOnVerifyFail -AutoRollbackMode strict` İLE:**
`scenario2_verify_fail_autorollback/autorollback_ON_strict/apply_report.json`
— `verification_state=FAIL`, `auto_rollback.triggered=true`,
`auto_rollback.restored_checksum` == baseline checksum (dosya
**OTOMATİK OLARAK geri yüklendi**, manuel müdahale GEREKMEDİ).
`data/audit/audit.log.jsonl`'e gerçek bir `task=auto_rollback_triggered`
kaydı eklendi (`risk_level=high`).

**Gözlem:** Flag KAPALIYKEN davranış BİREBİR v1.1 ile aynı kaldı (dosya
mutasyona uğramış halde bırakıldı, exit code 3) — flag AÇILDIĞINDA
davranış GERÇEKTEN değişti ve dosya otomatik geri alındı. Bu, "default
behavior unchanged unless explicit flag enabled" kısıtının gerçek
kanıtıdır.

## Senaryo 3 — Emergency legitimacy check (mock provider)

**Kurulum (gerçek):** `check_emergency_legitimacy.py --provider mock`
iki kez çalıştırıldı — biri geçerli (`OPS-5001`), biri geçersiz
(`TICKET-BAD-FORMAT`) bilet formatıyla.

**Sonuç:** `scenario3_legitimacy_mock/pass_case/legitimacy_report.json`
→ `legitimacy_status=PASS` (exit 0); `scenario3_legitimacy_mock/fail_case/legitimacy_report.json`
→ `legitimacy_status=FAIL` (exit 2). İkisi de GERÇEK bir ağ çağrısı/sır
KULLANMADI (`[PILOT STUB]` etiketi raporlarda açıkça görünür).

**Gözlem:** Mekanizma beklendiği gibi çalışıyor VE `apply_threshold_proposal.ps1`'in
hiçbir yerinde bu sonucu OKUYAN/UYGULAYAN bir kod yolu YOK — pilot,
non-blocking niteliğini koruyor (kod incelemesiyle de doğrulandı, bkz.
`threshold_governance_core.py::check_apply_eligibility` — `legitimacy_report_path`
alanına HİÇ bakmıyor).

## Genel Değerlendirme ve Öneri

| Özellik | Gözlemlenen davranış | Risk | Öneri |
|---|---|---|---|
| Chain-matching (`GOV_EMERGENCY_CHAIN_MATCHING`) | Gerçek bir yanlış-pozitif "çözüldü" senaryosunu doğru yakaladı | Düşük (yalnızca WARN üretir, apply'ı engellemiyor) | **KEEP PILOT** — birkaç haftalık gerçek trafik + `chain_eval.md` biriktirmesi sonrası "Promotion criteria" değerlendirilebilir |
| Auto-rollback (`GOV_AUTO_ROLLBACK_ON_VERIFY_FAIL`) | Yalnızca FAIL'de, yalnızca flag açıkken tetiklendi; safe/strict ayrımı kod incelemesiyle + birim testleriyle doğrulandı (concurrent-değişiklik senaryosu gerçek dosya ile prova edilmedi, yalnızca birim testte) | Orta (dosyayı OTOMATİK değiştiren bir mekanizma — yanlış yapılandırılmış bir `-AmtoolPath` gibi "beklenmedik FAIL" durumlarında istenmeyen rollback tetikleyebilir) | **KEEP PILOT** — `safe` modun concurrent-değişiklik senaryosu GERÇEK bir dosya üzerinde (yalnızca birim testte değil) ayrıca doğrulanmadan `strict`'in varsayılan/geniş kullanımı ÖNERİLMEZ |
| Emergency legitimacy (`GOV_EMERGENCY_LEGITIMACY_REQUIRED`) | Mekanizma çalışıyor, apply akışına HİÇ bağlı değil (kasıtlı) | Düşük (hiçbir gerçek davranışı etkilemiyor) | **KEEP PILOT** — gerçek bir provider entegrasyonu (Jira API vb.) OLMADAN `enforced` moda terfi ANLAMSIZDIR; şu an yalnızca format kontrolü + stub var |

**Genel öneri: KEEP PILOT (üç özellik için de).** Hiçbiri, mevcut kanıt
temelinde "enforced" (varsayılan/flag'siz) moda terfi için yeterli
olgunlukta değil — `docs/ops/MONITORING_STACK_RUNBOOK.md`'deki
"Promotion criteria from pilot → enforced" bölümündeki 2-4 haftalık
gözlem + belgelenmiş karar kriterleri henüz sağlanmadı (bu pilot,
mekanizmanın DOĞRU ÇALIŞTIĞININ tek-seferlik kanıtıdır, UZUN VADELİ
gözlem kanıtı DEĞİLDİR).

## Temizlik Doğrulaması

Tüm senaryolar sonrası `infra/monitoring/prometheus/model_gateway_alerts.yaml`
checksum'i baseline (`1677b2657abc8cb8e67e47c300b7aa8c7fcdc7410b013cd39eb2bd04c0bd719c`)
ile BİREBİR eşleşiyor (`git diff` boş) — hiçbir kalıcı yan etki
bırakılmadı. Test sırasında oluşan `approved_checksums_ledger.jsonl`
girdileri (yalnızca pilot verisi, gerçek prod verisi DEĞİL) commit
ÖNCESİ silindi.
