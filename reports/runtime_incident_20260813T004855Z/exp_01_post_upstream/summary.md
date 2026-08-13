# Post-Upstream Experiment Batch #1 — Özet

- Üretim tarihi (UTC): 2026-08-13T02:33Z (yaklaşık, koşu ~02:10-02:33 UTC arası sürdü)
- Toplam çağrı: 100 (5 senaryo × 20 çağrı)
- Ham veri: `results.jsonl`, sunucu logları: `raw_logs/`
- Script: `scripts/repro_b036_batch.ps1`

## Sonuçlar

| Senaryo | n | crash_count | success_count | success_rate | p50 | p95 | min | max |
|---|---|---|---|---|---|---|---|---|
| A (Vulkan ON, baseline) | 20 | 20 | 0 | %0.0 | 8.04s | 9.71s | 7.52s | 10.39s |
| B (Vulkan OFF, CPU-only) | 20 | 0 | 20 | %100.0 | 9.58s | 14.48s | 3.81s | 19.52s |
| C (Vulkan ON + `OLLAMA_NUM_PARALLEL=1`) | 20 | 20 | 0 | %0.0 | 8.17s | 9.00s | 7.50s | 9.59s |
| D (Vulkan ON + `num_ctx=256`) | 20 | 20 | 0 | %0.0 | 8.33s | 9.22s | 7.63s | 10.07s |
| E (Vulkan ON + her çağrı öncesi temiz restart) | 20 | 20 | 0 | %0.0 | 10.88s | 13.29s | 9.59s | 42.92s |

**Toplam: 100 çağrı, 80 çöküş (%80), 20 başarı (%20) — başarıların tamamı
Senaryo B'den (CPU-only).**

## Yorum

- **A, C, D, E: 20/20 çöküş, istisnasız.** Önceki küçük örneklemli
  bulgunun (5/5 çöküş, bkz. `../gpu_isolation_matrix.md`) 20 çağrılık
  örneklemle **tam olarak doğrulandığı** anlamına gelir — çöküş şansa
  bağlı/aralıklı değil, Vulkan cihazı her seçildiğinde deterministik.
- **C, Senaryo A ile davranışsal olarak özdeş çıktı** (`OLLAMA_NUM_PARALLEL=1`
  zaten varsayılandı; bu, `test_matrix.md`'deki beklentiyi doğruluyor —
  eşzamanlılığı azaltmak çöküşü etkilemiyor).
- **D, azaltılmış context (`num_ctx=256`) çöküşü önlemedi** — bellek
  erişim ihlali, işlenecek context büyüklüğüyle ilgili değil; model
  yükleme/Vulkan backend başlatma aşamasıyla ilgili olduğu hipotezini
  destekliyor.
- **E, her çağrı için temiz sunucu restart'ı da çöküşü önlemedi** — bu,
  önceki bir sunucu durumunun/önbelleğin kalıcı bir yan etkisi olmadığını,
  sorunun sıfırdan her başlatmada yeniden üretildiğini doğruluyor.
- **B (CPU-only), tek crash-free senaryo** — beklendiği gibi, ama bu B036'yı
  ÇÖZMÜYOR: B036'nın resmi çıkış kriteri **varsayılan (Vulkan etkin)
  profilde** stabilite gerektiriyor (bkz. `docs/BACKLOG.md`). B, zaten
  bilinen ve `docs/ops/OLLAMA_WORKAROUND_CPU_ONLY.md`'de formalize edilmiş
  workaround'un aynısı — yeni bir çözüm değil.

## En iyi senaryo

**B (Vulkan OFF / CPU-only)** — tek crash-free senaryo, ama zaten bilinen
workaround. Vulkan etkin senaryolardan (A, C, D, E) hiçbiri crash-free
sonuç vermedi.

## B036 kararı

**B036: IN_PROGRESS (değişmedi).** Hiçbir Vulkan-etkin senaryo crash-free
çıkmadı; `READY_FOR_RETEST`'e geçiş için gereken kanıt (varsayılan profilde
kararlı koşu) bu deney partisinde elde edilemedi. B036 resmi olarak
**RESOLVED yapılmadı** — kalıcı çözüm hâlâ (a) NVIDIA sürücü güncellemesi
veya (b) upstream Ollama düzeltmesi (issue #17716 takip ediliyor) bekliyor.

## B031 gate

Crash-free/reproducible bir Vulkan-etkin senaryo bulunamadığı için B031
yeniden değerlendirmesi **atlandı**. Neden: `blocked_by_runtime_instability`.
B031 resmi durumu değişmeden **BLOCKED_BY_RUNTIME** kalıyor.
