# [Upstream Issue Taslağı — ollama/ollama] `llama-server` crashes with 0xc0000005 on Windows when using Vulkan backend on older NVIDIA driver (Quadro RTX 3000, driver 442.94)

> Bu dosya, https://github.com/ollama/ollama/issues adresine açılacak bir issue
> için hazır taslaktır. Henüz açılmadı — EzoEzgi Ops projesinin B036 triage
> sürecinin bir çıktısıdır. Kullanıcı onayı olmadan gönderilmeyecektir.

## Environment

- Ollama version: `0.32.9` (bug also present; not reproduced with `0.30.0` —
  see Version A/B section below)
- OS: Windows 11 Pro, Version 10.0.22631, Build 22631, 64-bit
- CPU: Intel(R) Core(TM) i9-10980HK CPU @ 2.40GHz
- RAM: 65.3 GB total, ~23 GB free at time of test
- GPU (discrete): NVIDIA Quadro RTX 3000, 6144 MiB VRAM
  - NVIDIA driver version: **442.94**
  - `nvidia-smi` reported CUDA version: **10.2**
- GPU (integrated): Intel(R) UHD Graphics
- Vulkan Instance Version: **1.2.131** (via `vulkaninfo`)
- Models tested: `qwen2.5:3b-instruct` (Q4_K_M, 1.9GB), `llama3:latest`
  (Q4_0, 4.7GB) — both crash identically

## What happened

Every single `/api/generate` (and interactive `ollama run`) call against a
local model crashes the `llama-server.exe` subprocess with a Windows access
violation, returned to the client as:

```json
{"error":"llama-server process has terminated: exit status 0xc0000005: The instruction at 0xp referenced memory at 0xp. The memory could not be s."}
```

Windows Event Viewer (Application log) records a matching `Application Error`
(Event ID 1000) for every occurrence:

- Faulting application: `llama-server.exe`
- Exception code: `0xc0000005` (STATUS_ACCESS_VIOLATION)
- Fault offset: `0x0000000000000000`
- **Faulting module name: `unknown`** (Windows could not resolve the module —
  consistent with a fault deep inside a third-party driver/ICD without public
  symbols)

20 identical crash records were collected over a single triage session (see
attached `event_viewer_crash_entries.txt`).

## Root cause (isolated, log-confirmed)

Reproduced 5/5 crashes with the default configuration, where server logs show
the Vulkan backend selecting the discrete GPU:

```
msg="inference compute" id=0 filter_id=1 library=Vulkan compute=0.0 name=Vulkan1 description="Quadro RTX 3000" ...
cmn  common_param:   - Vulkan0 : Quadro RTX 3000 (5980 MiB, 5212 MiB free)
```

Setting **`OLLAMA_VULKAN=false`** (or equivalently **`OLLAMA_LLM_LIBRARY=cpu`**)
before starting `ollama serve` makes the crash disappear completely (5/5
successful `/api/generate` calls, 0 crashes) — and server logs confirm the
Vulkan device is never discovered/selected in this mode:

```
msg="inference compute" id=cpu library=cpu compute="" name=cpu description=cpu ...
cmn  common_param:   - CPU     : Intel(R) Core(TM) i9-10980HK CPU @ 2.40GHz (65337 MiB, 24170 MiB free)
```

This strongly points to an incompatibility between Ollama's Vulkan backend
(`ggml-vulkan.dll`) and this machine's GPU driver/Vulkan runtime stack
(NVIDIA 442.94 / Vulkan Instance 1.2.131 — both several years old relative to
current Vulkan 1.3+ and CUDA 12/13 expectations baked into the Ollama
installation's `cuda_v12`/`cuda_v13` backend folders).

## Version A/B

- **0.32.9** (current): crashes, Vulkan device auto-selected by default.
- **0.30.0** (older): does **not** crash with default settings — but server
  logs show it also does not select the Vulkan device on this hardware
  (falls back to `library=cpu` on its own during GPU discovery). This does
  **not** prove the underlying issue is fixed in 0.30.0 — more likely, an
  earlier/stricter compatibility check in 0.30.0's GPU discovery logic simply
  avoids engaging the faulty code path on this specific driver/Vulkan
  combination, while 0.32.9's discovery logic considers the device
  "supported enough" to select and then crashes when it's actually used.

## Reproduction

Minimal repro steps (see attached `scripts/repro_ollama_crash.ps1` and sample
`repro_output.txt` for exact commands/output):

1. `ollama serve` (default environment, no special flags)
2. `ollama pull qwen2.5:3b-instruct` (or any model)
3. `POST /api/generate` with `{"model":"qwen2.5:3b-instruct","prompt":"hello","stream":false}`
4. Observe `HTTP 500` with the `0xc0000005` error body above, every time.

## Workaround

Starting the server with `OLLAMA_VULKAN=false` (or `OLLAMA_LLM_LIBRARY=cpu`)
avoids the crash entirely by forcing CPU-only inference. Downside: inference
latency increases substantially (observed 7.7s-26.9s per call for a 3B
Q4_K_M model on an 8-core/16-thread CPU), which is expected for CPU-only
inference but may not be acceptable for latency-sensitive use cases.

## Suggested next steps for maintainers

1. Consider a driver/Vulkan-version compatibility check before selecting the
   Vulkan backend, with an automatic fallback to CPU (similar to what 0.30.0
   appears to do implicitly) instead of crashing.
2. If feasible, catch the access violation at the `llama-server` process
   supervision layer and retry/fallback to CPU rather than surfacing a raw
   `HTTP 500` with an unrecoverable state.
3. Document `OLLAMA_VULKAN` and `OLLAMA_LLM_LIBRARY` more prominently as
   workarounds for GPU-driver-related crashes on Windows.

## Attachments (available on request / in the reporting project's repo)

- `host_fingerprint.md` — full host/driver/Vulkan fingerprint
- `event_viewer_crash_entries.txt` — 20 raw Event Viewer crash records
- `gpu_isolation_matrix.md` — full A-E isolation test matrix with log excerpts
- `version_ab_test.md` — full version comparison with log excerpts
- `scripts/repro_ollama_crash.ps1` + `repro_output.txt` — reproduction script and sample output
