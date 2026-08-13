# Upstream Follow-up Comment Draft — Issue #17716

> Status: **DRAFT, NOT POSTED.** Prepared for manual submission to
> <https://github.com/ollama/ollama/issues/17716>. This file is the
> ready-to-paste comment body (English, upstream repo language).

---

Follow-up with a larger, controlled reproduction batch (n=100, 5 scenarios
x 20 calls each) to add statistical confidence to the original report.

## Environment (unchanged since original report)

- OS: Windows 11 Pro, Version 10.0.22631, Build 22631, 64-bit
- GPU: NVIDIA Quadro RTX 3000, driver 442.94, `nvidia-smi` CUDA version 10.2
- Vulkan Instance Version: 1.2.131
- Ollama version: 0.32.9
- Model used for this batch: `qwen2.5:3b-instruct` (Q4_K_M)

## Test matrix

| Scenario | Description |
|---|---|
| A | Vulkan ON, baseline (default settings) |
| B | Vulkan OFF (`OLLAMA_VULKAN=false`) — CPU-only control |
| C | Vulkan ON + explicit `OLLAMA_NUM_PARALLEL=1` (already the default; run to confirm no behavioral difference) |
| D | Vulkan ON + reduced context (`options.num_ctx=256`) |
| E | Vulkan ON + a clean server restart before every single call (rules out any state carried over between calls) |

Each scenario: 20 sequential `/api/generate` calls, `stream:false`.

## Outcomes

| Scenario | Runs | Crashes | Success rate |
|---|---|---|---|
| A | 20 | 20 | 0% |
| B | 20 | 0 | 100% |
| C | 20 | 20 | 0% |
| D | 20 | 20 | 0% |
| E | 20 | 20 | 0% |

**80/100 total crashes. Every Vulkan-enabled variation (A, C, D, E) crashed
on every single call — 20/20, no exceptions. The only crash-free scenario
was B, where the Vulkan backend is disabled entirely.**

All crashes carried the same signature as the original report:

```
{"error":"llama-server process has terminated: exit status 0xc0000005: The instruction at 0xp referenced memory at 0xp. The memory could not be s."}
```

## What this batch adds to the original report

- Reduced concurrency (C) made no difference — `OLLAMA_NUM_PARALLEL` was
  already `1` by default in our environment.
- Reduced context length (D, `num_ctx=256`) made no difference — this
  points away from a context-size-dependent memory issue and toward
  something in backend/model initialization on the Vulkan path.
- A clean server restart before every call (E) made no difference — this
  rules out any carried-over state or cache from a previous request as the
  cause; the crash reproduces from a completely fresh process every time.

## Question for maintainers

Given that disabling Vulkan entirely (`OLLAMA_VULKAN=false`) is the only
configuration we found that avoids the crash, is there a more targeted
diagnostic flag or debug build we could run to narrow down where in the
Vulkan backend initialization/inference path the access violation occurs?
Happy to run any additional instrumented build or flag combination against
this hardware.

## Artifacts

Available in the reporting project's incident folder
(`reports/runtime_incident_20260813T004855Z/exp_01_post_upstream/` and
parent folder), can be attached on request:

- `test_matrix.md` — full scenario definitions
- `results.jsonl` — raw per-call results (100 lines)
- `summary.md` — computed statistics and interpretation
- `env_snapshot.txt` — Ollama version at time of this batch
- `raw_logs/` — per-scenario server stdout/stderr logs
