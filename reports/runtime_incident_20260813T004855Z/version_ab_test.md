# Ollama Sürüm A/B Testi — B036 Deep Triage

- Test modeli: `qwen2.5:3b-instruct`
- A: mevcut sürüm (`0.32.9`, olay boyunca kullanılan sürüm)
- B: önceki bilinen sürüm (`0.30.0`, winget katalogundaki 0.32.x öncesi son
  "minor" sürüm serisi; 2026-06-01 build tarihli)
- Kurulum yöntemi: `winget install --id Ollama.Ollama --version <X> --exact --force`
  (0.30.0 için winget'in etkileşimli GUI adımı `/VERYSILENT` ile atlanamadığından,
  installer doğrudan GitHub Releases'ten indirilip
  `OllamaSetup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART` ile kuruldu — bkz.
  `version_downgrade_install_log.txt`)
- **Not:** Test sonunda sistem orijinal sürüm olan **0.32.9'a geri yüklendi**
  (`winget install --id Ollama.Ollama --version 0.32.9 --exact --force`, bkz.
  `version_restore_install_log.txt`) — bu makinenin Ollama kurulumu, triage
  öncesindeki durumuyla aynı sürümde bırakıldı.

---

## A — Sürüm 0.32.9 (mevcut, varsayılan env)

- **Komut:** `ollama.exe serve` (env değişkeni yok, varsayılan)
- **Sunucu log kanıtı:** `library=Vulkan name=Vulkan1 description="Quadro RTX 3000"`
  cihazı keşfedip seçti (bkz. `gpu_isolation_matrix.md` Test A ile aynı bulgu).
- **`/api/generate` sonucu:** `HTTP 500`, çöküş imzası `0xc0000005` (bkz.
  `matrix_A_result.txt`).
- **Sonuç:** ÇÖKÜYOR.

## B — Sürüm 0.30.0 (önceki, varsayılan env — hiçbir CPU-zorlama bayrağı yok)

- **Komut:** `ollama.exe serve` (env değişkeni yok, varsayılan — 0.32.9 testiyle
  bilerek aynı, "adil" bir A/B karşılaştırması için)
- **Sunucu log kanıtı** (`version_ab_old_stderr.log`):
  ```
  env="map[... OLLAMA_VULKAN:true ...]"
  msg="discovering available GPUs..."
  msg="inference compute" id=cpu library=cpu compute="" name=cpu description=cpu total="63.8 GiB" available="20.5 GiB"
  ```
  **Önemli:** `OLLAMA_VULKAN` burada da varsayılan olarak `true`, ama GPU keşif
  aşaması `Vulkan`/`Quadro RTX 3000` cihazını **hiç seçmedi** — doğrudan
  `library=cpu`'ya düştü. 0.32.9'daki davranıştan (aynı donanımda Vulkan
  cihazını başarıyla keşfedip seçmesi) farklı.
- **`/api/generate` sonucu:** `HTTP 200`, gecikme 11.24s, geçerli Türkçe yanıt
  üretildi (bkz. `version_ab_old_result.txt`).
- **Sonuç:** ÇÖKMÜYOR.

---

## Yorum (aşırı iddia etmeden)

0.30.0, bu donanım/sürücü kombinasyonunda (NVIDIA 442.94 / Vulkan 1.2.131)
Vulkan cihazını **hiç seçmiyor** ve otomatik olarak CPU'ya düşüyor — bu yüzden
çökmüyor. 0.32.9 ise aynı cihazı **seçiyor** ve seçtiği anda (model
`llama-server`'a yüklenirken) çöküyor.

Bu gözlem şunu **kanıtlamıyor**: "0.30.0'da bug yok, 0.32.x'te regresyon var."
Kanıtladığı şey daha dar: **0.30.0'ın GPU keşif/uyumluluk mantığı, bu eski
sürücü+Vulkan kombinasyonunu 0.32.9'dan farklı şekilde değerlendiriyor ve
sonuç olarak riskli kod yoluna hiç girmiyor.** Kök neden hâlâ
sürücü/Vulkan-ICD seviyesinde (bkz. `gpu_isolation_matrix.md` sonucu) — 0.30.0
bunu "düzeltmiyor", yalnızca bu spesifik donanımda o yola hiç girmediği için
tesadüfen etkilenmiyor. Bu ayrım, olası bir upstream issue'da doğru şekilde
belirtilmeli (bkz. `OLLAMA_GITHUB_ISSUE_DRAFT.md`).

**Sonuç: A/B testi tamamlandı — EVET.** İki sürüm de gerçek şekilde kurulup
test edildi, sonuçlar (0.32.9 çöküyor / 0.30.0 çökmüyor) log kanıtıyla
doğrulandı, sistem 0.32.9'a geri yüklendi.
