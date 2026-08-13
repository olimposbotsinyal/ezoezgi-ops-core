# B036 Runtime Incident — `runtime_incident_20260813T004855Z`

Ollama Windows `0xc0000005` (`llama-server` access violation) çöküşünün
derin triage + post-upstream kanıt paketi. Kronolojik olarak üç aşamada
üretildi: (1) ilk deep triage, (2) upstream issue açma, (3) post-upstream
deney partisi.

## Hızlı yorum (TL;DR)

- **Kök neden:** GPU/Vulkan backend seçimi (Quadro RTX 3000, sürücü 442.94,
  Vulkan 1.2.131) — log-kanıtlı, iki bağımsız deney partisiyle doğrulandı
  (küçük örneklem: `gpu_isolation_matrix.md`; büyük örneklem, n=100:
  `exp_01_post_upstream/summary.md`).
- **Geçici çözüm:** `OLLAMA_VULKAN=false` / `OLLAMA_LLM_LIBRARY=cpu` —
  çökmüyor ama yavaş (bkz. `../../docs/ops/OLLAMA_WORKAROUND_CPU_ONLY.md`).
- **Upstream takip:** <https://github.com/ollama/ollama/issues/17716> (AÇILDI).
- **B036 durumu:** IN_PROGRESS. **B031 durumu:** BLOCKED_BY_RUNTIME.

## Aşama 1 — Deep Triage

| Dosya | İçerik |
|---|---|
| `host_fingerprint.md` | OS/GPU/sürücü/Vulkan/Ollama sürüm fingerprint |
| `host_raw.txt`, `vulkaninfo_head.txt` | Ham host/Vulkan komut çıktıları |
| `event_viewer_crash_entries.txt` | 20 adet Windows Event Viewer çöküş kaydı |
| `gpu_isolation_matrix.md` | A-E izolasyon testleri (küçük örneklem, n=5) + log kanıtı |
| `matrix_A/B/C_*` | İzolasyon matrisi ham sunucu logları/sonuçları |
| `version_ab_test.md` | Ollama `0.32.9` vs `0.30.0` A/B testi |
| `version_ab_old_*`, `version_*_install_log.txt` | Sürüm A/B ham çıktıları |
| `repro_output.txt` | `scripts/repro_ollama_crash.ps1` örnek çıktısı |
| `repro_verify_*` | Repro scriptinin başarı-yolu doğrulaması |
| `OLLAMA_GITHUB_ISSUE_DRAFT.md` | İlk issue taslağı (İngilizce) |

## Aşama 2 — Upstream Issue

| Dosya | İçerik |
|---|---|
| `ISSUE_READY_PACKAGE.md` | Gönderime hazır paket → **AÇILDI**, issue #17716 |
| `b031_probe_cpu_only_*` | CPU-only workaround altında bilgilendirici B031 koşusu (log + pid) |
| `nlu_eval_20260813T011953Z_cpu_only_informational.md` | O koşunun metrik raporu (INFORMATIONAL_ONLY, non-gating) |

## Aşama 3 — Post-Upstream Experiment Batch #1

Klasör: `exp_01_post_upstream/`

| Dosya | İçerik |
|---|---|
| `test_matrix.md` | 5 senaryolu (A-E) test matrisi tanımı |
| `results.jsonl` | 100 çağrının ham sonucu (JSONL, 1 satır/çağrı) |
| `summary.md` | Senaryo başına crash/success/latency istatistikleri + karar |
| `env_snapshot.txt` | Koşu zamanındaki Ollama sürümü + kısa not |
| `raw_logs/` | Her senaryonun ham sunucu stdout/stderr logları |

**Sonuç (n=100, 5×20):** A/C/D/E (Vulkan etkin varyasyonlar) 20/20 çöküş;
B (CPU-only) 20/20 başarı. Hiçbir Vulkan-etkin senaryo crash-free
olmadığından B036 `READY_FOR_RETEST`'e geçemedi, B031 yeniden
değerlendirmesi atlandı (`blocked_by_runtime_instability`).

## İlgili dokümanlar (repo kökünden)

- `docs/RUNBOOK.md` — "Faz 1 — Ollama NLU Entegrasyonu (B031)" bölümü,
  tüm B036 alt-bölümleri
- `docs/BACKLOG.md` — B036 satırı (çıkış kriterleri + güncel durum)
- `docs/ops/OLLAMA_WORKAROUND_CPU_ONLY.md` — geçici workaround profili
- `scripts/repro_ollama_crash.ps1` — tekli reprodüksiyon scripti
- `scripts/repro_b036_batch.ps1` — çok senaryolu, tekrar sayılı deney scripti
