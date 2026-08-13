# NLU Değerlendirme Raporu — B031 Quality Gate

> **INFORMATIONAL_ONLY (non-gating).** Bu koşu, B036 geçici CPU-only
> workaround'u (`OLLAMA_VULKAN=false`) aktifken çalıştırıldı — resmi B031
> kabul kararı için kullanılmaz. Resmi B031 durumu **BLOCKED_BY_RUNTIME**
> olarak kalır (bkz. `docs/RUNBOOK.md`, `docs/BACKLOG.md`). Bu dosya,
> `tools/eval_nlu.py`'nin varsayılan `reports/nlu_eval_20260813.md` çıktı
> yolunu (tarih-bazlı, saat içermiyor) **ezmemek** için bilinçli olarak farklı
> bir dosya adıyla buraya taşındı — orijinal `reports/nlu_eval_20260813.md`,
> 2026-08-13T00:13:05Z'deki çöküş-keşif koşusunun kanıtı olarak
> `docs/RUNBOOK.md`'nin "Değerlendirme koşu kayıtları" tablosunda referans
> gösteriliyor ve değiştirilmeden korunmalı.

- Üretim tarihi (UTC): 2026-08-13T01:19:53Z
- Golden set: `tests\fixtures\nlu_golden_tr.jsonl`
- Ortam: local Ollama, `OLLAMA_VULKAN=false` (CPU-only workaround aktif),
  model: `llama3:latest` (varsayılan `DEFAULT_MODEL`), `OLLAMA_TIMEOUT_SECONDS=90`

## Metrikler (mock vs ollama)

| Metrik | mock | ollama |
|---|---|---|
| Örnek sayısı | 50 | 50 |
| Intent accuracy | 92.0% | 74.0% |
| Entity match rate | 0.0% | 41.7% |
| Parse error rate | 0.0% | 0.0% |
| Fallback rate | 0.0% | 0.0% |
| Latency p50 | 0.0 ms | 11796.5 ms |
| Latency p95 | 0.0 ms | 20586.1 ms |

### mock

| Kriter | Değer | Eşik | Sonuç |
|---|---|---|---|
| intent_accuracy | 92.0% | ≥ 90.0% | PASS |
| entity_match_rate | 0.0% | ≥ 85.0% | FAIL |
| parse_error_rate | 0.0% | ≤ 2.0% | PASS |
| fallback_rate | 0.0% | ≤ 5.0% | PASS |
| latency_p95 | 0.00s | ≤ 2.50s | PASS |

### ollama (CPU-only workaround, informational)

| Kriter | Değer | Eşik | Sonuç |
|---|---|---|---|
| intent_accuracy | 74.0% | ≥ 90.0% | FAIL |
| entity_match_rate | 41.7% | ≥ 85.0% | FAIL |
| parse_error_rate | 0.0% | ≤ 2.0% | PASS |
| fallback_rate | 0.0% | ≤ 5.0% | PASS |
| latency_p95 | 20.59s | ≤ 2.50s | FAIL |

## Sonuç

**B031 quality gate (bu koşu, informational): FAIL** — intent_accuracy,
entity_match_rate ve latency_p95 eşiklerini karşılamıyor.

## Değerlendirme (bilgilendirici)

- **Çöküş yok:** `fallback_rate=0.0%`, `parse_error_rate=0.0%` — 50/50
  çağrı bir HTTP 500/`0xc0000005` olmadan tamamlandı. Bu, CPU-only
  workaround'un (bkz. `docs/ops/OLLAMA_WORKAROUND_CPU_ONLY.md`) stabilite
  açısından beklendiği gibi çalıştığını doğruluyor (B036 root-cause
  bulgusuyla tutarlı).
- **Gecikme eşiği açık ara aşıldı:** p95 20.59s >> 2.50s eşiği. Bu,
  `gpu_isolation_matrix.md`'de gözlemlenen CPU-only gecikme aralığıyla
  (7.7s-26.9s/çağrı, daha küçük `qwen2.5:3b-instruct` ile) tutarlı; burada
  daha büyük `llama3` (8B) modeliyle daha da yüksek çıktı — beklenen sonuç.
- **Intent/entity doğruluğu düşük:** `intent_accuracy=74.0%` (eşik %90),
  `entity_match_rate=41.7%` (eşik %85). Bu koşu CPU-only workaround altında
  yapıldığından, bu sonuçların model kalitesinden mi yoksa workaround'dan mı
  kaynaklandığı bu veriyle ayırt edilemez — bu nedenle resmi karar için
  kullanılmıyor.
- **Resmi B031 kararı değişmedi:** BLOCKED_BY_RUNTIME. Varsayılan
  (Vulkan etkin) profilde 50/50 stabil koşu elde edilmeden resmi gate
  yeniden değerlendirilmeyecek (bkz. `docs/BACKLOG.md` B036 çıkış
  kriterleri).
