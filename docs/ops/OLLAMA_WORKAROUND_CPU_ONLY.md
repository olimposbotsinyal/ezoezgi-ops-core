# Ollama — Geçici CPU-Only Çalıştırma Profili (B036 workaround)

> **Durum: GEÇİCİ workaround. B036'yı KAPATMAZ.**
> Bu profil, `0xc0000005` (`llama-server` access violation) çöküşünden
> kaçınmak için Vulkan/GPU backend'ini tamamen devre dışı bırakır. Kök neden
> (bu makinedeki eski NVIDIA sürücüsü 442.94/CUDA 10.2 + Vulkan 1.2.131 ile
> Ollama'nın Vulkan backend'i arasındaki etkileşim) hâlâ çözülmedi — bkz.
> `reports/runtime_incident_20260813T004855Z/gpu_isolation_matrix.md`.
> B036, yalnızca **varsayılan (Vulkan etkin) profilde** 50/50 kararlı çağrı
> elde edildiğinde kapatılabilir (bkz. `docs/BACKLOG.md` B036 çıkış
> kriterleri).

## Ne zaman kullanılır

- Yalnızca **geliştirme/tanı amaçlı** manuel testler için (ör. B031'in
  bilgilendirici/non-gating bir CPU-only koşusu).
- **Resmi B031 quality gate kararı için kullanılmaz** — B031'in
  `latency_p95≤2.5s` eşiği CPU-only modda muhtemelen karşılanamaz (bkz.
  aşağıdaki "Bilinen ödünleşim").
- Production/varsayılan çalışma modu OLARAK benimsenmemelidir; bu yalnızca
  B036 çözülene kadar geçici bir el ile devreye alınan mod.

## Operasyonel profil

İki eşdeğer mekanizma, ikisi de sunucu loglarıyla doğrulanmış şekilde Vulkan
cihaz keşfini tamamen atlıyor (bkz. `gpu_isolation_matrix.md` Test B/C):

- `OLLAMA_VULKAN=false` (birincil, önerilen)
- `OLLAMA_LLM_LIBRARY=cpu` (alternatif, aynı sonucu veriyor)

### Başlatma (PowerShell)

```powershell
$env:OLLAMA_VULKAN = "false"
& "C:\Users\Serkan\AppData\Local\Programs\Ollama\ollama.exe" serve
```

Veya arka planda, loglar dosyaya yönlendirilerek:

```powershell
$env:OLLAMA_VULKAN = "false"
Start-Process -FilePath "C:\Users\Serkan\AppData\Local\Programs\Ollama\ollama.exe" `
  -ArgumentList "serve" `
  -RedirectStandardOutput "cpu_only_stdout.log" `
  -RedirectStandardError "cpu_only_stderr.log" `
  -WindowStyle Hidden -PassThru
```

### Durdurma

```powershell
Get-Process | Where-Object { $_.ProcessName -like "*ollama*" } | Stop-Process -Force
Remove-Item Env:\OLLAMA_VULKAN -ErrorAction SilentlyContinue
```

### Doğrulama

1. Sunucu loguna bak — `OLLAMA_VULKAN:false` satırı env dökümünde görünmeli
   ve `inference compute` satırı `id=cpu library=cpu` olmalı (**`Vulkan`
   kelimesi hiçbir yerde geçmemeli**):
   ```powershell
   Select-String -Path <stderr.log> -Pattern "OLLAMA_VULKAN|inference compute|Vulkan"
   ```
2. Tek bir çağrı ile canlı doğrulama (`scripts/repro_ollama_crash.ps1`):
   ```powershell
   $env:OLLAMA_VULKAN = "false"
   powershell -ExecutionPolicy Bypass -File scripts\repro_ollama_crash.ps1
   ```
   Beklenen: `HTTP 200`, exit code `0`, "cokus yok" mesajı.

## Bilinen ödünleşim (tradeoff)

- **Gecikme önemli ölçüde artıyor.** `gpu_isolation_matrix.md` Test B'de
  `qwen2.5:3b-instruct` (3B, Q4_K_M) ile gözlemlenen tek-çağrı gecikmeleri:
  9.86s / 14.38s / 23.19s / 26.90s (4 ardışık çağrı, aynı süreç).
- B031'in kabul eşiği `latency_p95≤2.5s` — CPU-only modda bu eşiğin
  karşılanması **beklenmiyor**. Bu nedenle CPU-only altında yapılan hiçbir
  B031 koşusu resmi kabul kararı için kullanılamaz; yalnızca bilgilendirme
  amaçlıdır (bkz. `docs/RUNBOOK.md` "B031 Informational Probe" notu).

## Kalıcı çözüme dönüş

Bu workaround, aşağıdakilerden biri gerçekleşene kadar geçerlidir:

1. NVIDIA GPU sürücüsü güncellenir ve varsayılan (Vulkan etkin) profilde
   50/50 kararlı çağrı elde edilir (bkz. `docs/BACKLOG.md` B036 sürücü
   güncelleme takip maddesi) — bu durumda workaround tamamen bırakılır.
2. Ollama upstream'de bug düzeltilir (bkz.
   `reports/runtime_incident_20260813T004855Z/ISSUE_READY_PACKAGE.md`) ve
   güncel sürüm bu makinede doğrulanır.
3. B031'in gecikme eşiği, CPU-only gerçeğini kalıcı olarak kabul eden ayrı
   bir ADR ile bilinçli şekilde yeniden tanımlanır (şu an planlanmıyor,
   yalnızca teorik bir seçenek).
