# B036 — Tetikleyici Bazlı Doğrulama Planı

> Bu plan, `docs/ops/B036_UPSTREAM_WATCH_PROTOCOL.md`'nin karar ağacında
> tanımlanan üç tetikleyiciye (Trigger A/B/C) göre çalıştırılacak koşuları
> tanımlar. **Hiçbir koşu, bu tetikleyicilerden biri gerçekleşmeden
> spekülatif olarak çalıştırılmayacak** — bkz. `docs/RUNBOOK.md` "B036
> Upstream Watch Mode Enabled".

## Trigger A — Maintainer bir env flag/diagnostic önerdi

**Aksiyon:** Önerilen flag/ayarla, hedefli minimal reprodüksiyon.

- Çağrı sayısı: **10** (tek senaryo, önerilen ayarla)
- Model: `qwen2.5:3b-instruct` (mevcut en küçük model)
- Araç: `scripts/repro_ollama_crash.ps1` (tekli/manuel doğrulama için) veya
  gerekirse `scripts/repro_b036_batch.ps1`'in tek-senaryo modunda
  kullanılması (script parametrelerinin önerilen flag'e göre uyarlanması
  gerekebilir — bu, tetikleyici geldiğinde ayrıca değerlendirilecek)
- Kayıt: yeni bir `reports/runtime_incident_<timestamp>/trigger_a_*`
  klasörü veya mevcut `exp_01_post_upstream/` altına ek bir alt klasör

## Trigger B — Maintainer bir patch/commit paylaştı

**Aksiyon:** 3 senaryolu doğrulama (A/C/E) + kontrol (B), toplam **40 çağrı**.

| Senaryo | Çağrı sayısı | Amaç |
|---|---|---|
| A (Vulkan ON, baseline, patch uygulanmış) | 10 | Patch'in ana çöküş yolunu düzeltip düzeltmediğini test et |
| C (Vulkan ON + `OLLAMA_NUM_PARALLEL=1`) | 10 | Eşzamanlılık koşulunun hâlâ etkisiz olduğunu doğrula |
| E (Vulkan ON + her çağrıda temiz restart) | 10 | Patch sonrası da tutarlı olduğunu doğrula |
| B (Vulkan OFF, kontrol) | 10 | Referans/kontrol grubu, workaround'un hâlâ çalıştığını doğrula |

- Toplam: 40 çağrı
- Araç: `scripts/repro_b036_batch.ps1` (senaryo alt kümesi parametreli
  çalıştırılacak — script şu an tüm A-E'yi sırayla çalıştırıyor; bu
  tetikleyici geldiğinde yalnızca A/B/C/E'yi çalıştıracak küçük bir
  parametre değişikliği gerekebilir, D bu doğrulamada atlanır çünkü D
  zaten context-boyutunun etkisiz olduğunu kanıtladı)

## Trigger C — Yeni bir Ollama sürümü, düzeltmeyi belirtiyor

**Aksiyon:** Tam 5 senaryolu matris (A-E), toplam **100 çağrı** — orijinal
`test_matrix.md` ile birebir aynı yapı, yeni sürümle tekrarlanır.

- Araç: `scripts/repro_b036_batch.ps1 -RunsPerScenario 20` (mevcut script,
  değişiklik gerekmez)
- Kayıt: `reports/runtime_incident_<yeni_timestamp>/exp_02_version_validation/`
  (mevcut `exp_01_post_upstream/`'in üzerine yazılmaz)

## B036 → READY_FOR_RETEST geçiş koşulu (hangi tetikleyiciden gelirse gelsin)

Hangi trigger'dan sonuç alınırsa alınsın, B036'nın `IN_PROGRESS` →
`READY_FOR_RETEST` geçişi için (bkz. `docs/BACKLOG.md` "B036 Çıkış
Kriterleri" kilitli checklist'i ile birebir aynı):

- [ ] Vulkan yolunda (varsayılan profil) en az **50 ardışık** çağrı
  çökmeden tamamlanmalı
- [ ] **2 ayrı soğuk restart** arasında tutarlı başarı
- [ ] Log paketinde **`0xc0000005` imzası hiç görünmemeli**

Bu üç koşul sağlanmadan hiçbir trigger sonucu B036'yı kapatmaz — yalnızca
`docs/BACKLOG.md`'deki checklist'in ilerlemesine kanıt olarak eklenir.
