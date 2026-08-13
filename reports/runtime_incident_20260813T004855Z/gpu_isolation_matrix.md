# GPU/Vulkan İzolasyon Matrisi — B036 Deep Triage

- Üretim tarihi (UTC): 2026-08-13T01:00:00Z (yaklaşık; testler ~03:57-04:00 TSİ arası çalıştırıldı)
- Test modeli: `qwen2.5:3b-instruct` (daha küçük model, hızlı iterasyon için)
- Her test için: temiz `ollama serve` süreci (önceki süreç durdurulup yeniden başlatıldı),
  stdout/stderr doğrudan dosyaya yönlendirildi, tek bir `/api/generate` çağrısı
  (`stream:false`) yapıldı, ardından log dosyası GPU/Vulkan cihaz keşfi için incelendi.

**Önemli kural (görev talimatı gereği):** GPU'nun devre dışı bırakıldığı yalnızca
süreç loglarında (a) `library=cpu` cihazının seçildiği VE (b) hiçbir Vulkan cihazının
(`library=Vulkan`, `Vulkan0`, `Vulkan1`) keşfedilmediği/başlatılmadığı **açıkça
görüldüğünde** iddia edilmiştir. Aksi halde "kesin değil" olarak işaretlenmiştir.

---

## Test A — Varsayılan başlatma (baseline)

- **Env değişkenleri:** hiçbiri (varsayılan)
- **Başlatma komutu:** `ollama.exe serve` (özel env yok)
- **Sunucu log kanıtı** (`matrix_A_stderr.log`):
  ```
  env="map[... OLLAMA_LLM_LIBRARY: ... OLLAMA_VULKAN:true ...]"
  msg="discovering available GPUs..."
  msg="dropping integrated GPU; to enable, set OLLAMA_IGPU_ENABLE=1" library=Vulkan name=Vulkan0 description="Intel(R) UHD Graphics"
  msg="inference compute" library=Vulkan name=Vulkan1 description="Quadro RTX 3000" type=discrete
  cmn  common_param:   - Vulkan0 : Quadro RTX 3000 (5980 MiB, 5212 MiB free)
  ```
- **GPU/Vulkan cihazı başlatıldı mı:** **EVET** — `Vulkan1`/`Quadro RTX 3000` seçilip
  llama-server'a aktarıldı.
- **`/api/generate` sonucu:** `HTTP 500`, gecikme 7.83s
  ```
  {"error":"llama-server process has terminated: exit status 0xc0000005: The instruction at 0xp referenced memory at 0xp. The memory could not be s."}
  ```
- **Çöküş imzası:** **EVET** (`0xc0000005`)
- **Sonuç:** Baseline, önceki oturumların 4/4 çöküş bulgusuyla tutarlı — 5. tekrar da çöktü.

---

## Test B — `OLLAMA_VULKAN=false` (birincil CPU-zorlama mekanizması)

- **Env değişkenleri:** `OLLAMA_VULKAN=false`
- **Başlatma komutu:** `$env:OLLAMA_VULKAN="false"; ollama.exe serve`
- **Sunucu log kanıtı** (`matrix_B_stderr.log`):
  ```
  env="map[... OLLAMA_VULKAN:false ...]"
  msg="discovering available GPUs..."
  msg="inference compute" id=cpu library=cpu compute="" name=cpu description=cpu total="63.8 GiB" available="23.6 GiB"
  msg="vram-based default context" total_vram="0 B" default_num_ctx=4096
  cmn  common_param:   - CPU     : Intel(R) Core(TM) i9-10980HK CPU @ 2.40GHz (65337 MiB, 24170 MiB free)
  ```
- **GPU/Vulkan cihazı başlatıldı mı:** **HAYIR** — log'da `Vulkan`, `Vulkan0/1`,
  `Quadro RTX 3000` hiçbir yerde geçmiyor; yalnızca `library=cpu` ve `CPU :
  Intel(R) Core(TM) i9-10980HK` cihaz bilgisi var. `total_vram="0 B"` de VRAM'in
  hiç dikkate alınmadığını doğruluyor.
- **`/api/generate` sonucu (1. çağrı):** `HTTP 200`, gecikme 9.86s
  ```
  {"model":"qwen2.5:3b-instruct", ..., "response":"Selameti! Ben bir asistanıyım ve
  hayatım benim şekilde yaşanıyor. Bugün nasıl yardımcı olabilirim?","done":true,
  "done_reason":"stop", ...}
  ```
  Gerçek, anlamlı bir Türkçe yanıt üretildi (sahte/simüle değil).
- **Doğrulama tekrarları (aynı süreç, 3 ek çağrı):** 3/3 `HTTP 200`
  (gecikmeler: 14.38s, 23.19s, 26.90s — CPU inference beklenen şekilde yavaş
  ve tutarsız, ama **çökmüyor**).
- **Toplam:** **4/4 başarılı, 0 çöküş, 0 HTTP 500.**
- **Çöküş imzası:** **HAYIR**
- **Sonuç:** Vulkan cihazı hiç başlatılmadığında çöküş **tamamen ortadan kalkıyor**.
  Bu, GPU/Vulkan yığınının kök neden olduğuna dair güçlü kanıt.

---

## Test C — `OLLAMA_LLM_LIBRARY=cpu` (alternatif/dokümante edilmiş ikinci mekanizma)

- **Env değişkenleri:** `OLLAMA_LLM_LIBRARY=cpu`
- **Başlatma komutu:** `$env:OLLAMA_LLM_LIBRARY="cpu"; ollama.exe serve`
- **Sunucu log kanıtı** (`matrix_C_stderr.log`):
  ```
  env="map[... OLLAMA_LLM_LIBRARY:cpu ... OLLAMA_VULKAN:true ...]"
  msg="discovering available GPUs..."
  msg="inference compute" id=cpu library=cpu compute="" name=cpu description=cpu total="63.8 GiB" available="22.4 GiB"
  ```
  (Not: `OLLAMA_VULKAN` burada hâlâ varsayılan `true` görünüyor, ama
  `OLLAMA_LLM_LIBRARY=cpu` GPU keşfini daha da erken/kesin şekilde atlıyor —
  "discovering available GPUs..." ile "inference compute id=cpu" arası yalnızca
  ~50ms, Test A/B'deki ~1.8-5.8s'lik Vulkan cihaz sorgulama süresi hiç yok.)
- **GPU/Vulkan cihazı başlatıldı mı:** **HAYIR** — aynı şekilde `Vulkan` string'i
  log'da hiç geçmiyor.
- **`/api/generate` sonucu:** `HTTP 200`, gecikme 7.74s — geçerli yanıt üretildi.
- **Çöküş imzası:** **HAYIR**
- **Sonuç:** İkinci, bağımsız env değişkeni de aynı sonucu doğruluyor — CPU-only
  zorlandığında çöküş oluşmuyor.

---

## Test D — Env'in sürece görünürlüğünün doğrulanması

Ollama sunucu süreci başlangıçta kendi okuduğu ortam değişkenlerini
`msg="server config" env="map[...]"` satırıyla **kendi loguna yazıyor** — bu,
harici bir gözlemin (örn. `Get-Process` ile env okumaya çalışmak) ötesinde,
sürecin **kendi bildirdiği** en güvenilir kanıt. Test B ve Test C loglarında
sırasıyla `OLLAMA_VULKAN:false` ve `OLLAMA_LLM_LIBRARY:cpu` değerlerinin
doğru şekilde yansıdığı doğrulandı (bkz. yukarıdaki log alıntıları). Ayrı bir
"clean shell" testine gerek kalmadı çünkü kanıt zaten sürecin kendisinden geldi.

**Sonuç: env değişkenleri sürece güvenilir şekilde ulaşıyor — EVET, log ile doğrulandı.**

---

## Test E — Ayrık GPU'yu donanım seviyesinde geçici devre dışı bırakma

**Atlandı (bilinçli karar).** Gerekçe:
- Test B ve Test C zaten yazılım seviyesinde (env değişkeni) **kesin ve tekrarlanabilir**
  bir izolasyon sağladı (4/4 ve 1/1 başarı, log ile doğrulanmış sıfır Vulkan cihaz
  başlatma). Donanım seviyesinde ek bir deney, aynı sonucu bir kez daha doğrulamaktan
  öteye geçmeyecekti.
- Ayrık GPU'yu Aygıt Yöneticisi'nden veya BIOS/Optimus ayarından devre dışı bırakmak,
  bu makinedeki diğer GPU'ya bağımlı uygulamaları/oturumu etkileyebilecek, geri alınması
  anlık olmayan bir sistem değişikliği. Görev talimatı bunu zaten "opsiyonel... eğer
  yapılabilir/güvenliyse" olarak işaretlemişti; kapsam ve risk oranı bu ek adımı
  gerektirmiyor.

---

## Genel Sonuç

| Test | Env | Vulkan cihazı başlatıldı mı | Sonuç | Çöküş |
|---|---|---|---|---|
| A (baseline) | (yok) | EVET (`Vulkan1`/Quadro RTX 3000) | HTTP 500 | EVET (`0xc0000005`) |
| B | `OLLAMA_VULKAN=false` | HAYIR | HTTP 200 x4 | HAYIR |
| C | `OLLAMA_LLM_LIBRARY=cpu` | HAYIR | HTTP 200 x1 | HAYIR |
| D | (env görünürlük doğrulaması) | N/A | Log ile doğrulandı | N/A |
| E | (donanım seviyesi GPU devre dışı) | Atlandı | Atlandı | Atlandı |

**GPU/Vulkan sürücü etkileşimi, süreç loglarıyla doğrulanmış şekilde kök neden
olarak izole edildi:** Vulkan cihazı (Quadro RTX 3000) her başlatıldığında
(Test A) çöküş oluşuyor; Vulkan cihazı log kanıtıyla doğrulanmış şekilde hiç
başlatılmadığında (Test B, Test C — iki bağımsız mekanizma) çöküş **tamamen**
ortadan kalkıyor (5/5 başarılı CPU-only çağrı, 0/5 çöküş).

Bu, görev talimatındaki "GPU süreç loglarıyla CPU-only backend doğrulanmadan
ve Vulkan cihaz başlatılmadığı teyit edilmeden ekarte edilmiş sayılamaz" koşulunu
karşılıyor: **GPU/Vulkan, bu makinede `0xc0000005` çöküşünün kök nedeni olarak
kanıtla doğrulanmıştır (conclusively isolated: EVET).**

**Pratik geçici çözüm (workaround):** `OLLAMA_VULKAN=false` (veya
`OLLAMA_LLM_LIBRARY=cpu`) ile başlatılan bir `ollama serve` süreci bu makinede
kararlı çalışıyor — ancak CPU-only inference önemli ölçüde daha yavaş
(gözlemlenen gecikme aralığı: 7.7s-26.9s / çağrı, `qwen2.5:3b-instruct` gibi
küçük bir modelde bile). B031 quality gate'inin `latency_p95≤2.5s` eşiğini
bu modda geçmesi olası değil — bu nedenle workaround, çöküşü çözse de, B031'in
performans eşiklerini otomatik olarak karşılamıyor. Kalıcı çözüm hâlâ GPU
sürücüsü/Vulkan yığınının güncellenmesi (bkz. `host_fingerprint.md`).
