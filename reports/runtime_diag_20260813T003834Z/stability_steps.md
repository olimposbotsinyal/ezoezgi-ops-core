# B036 Stabilizasyon Deneyleri — Sıralı Sonuçlar

> Üretim tarihi (UTC): 2026-08-13T00:38:34Z–00:45:00Z arası
> Ortam: bkz. `env_summary.md`

## Özet tablo

| Adım | Açıklama | Model | HTTP | Süre | `0xc0000005` |
|---|---|---|---|---|---|
| A | Baseline (mevcut durum) | qwen2.5:3b-instruct | 500 | ~8.74s | **VAR** |
| B | CPU izolasyon (`OLLAMA_NUM_GPU=0`, servis yeniden başlatıldı) | qwen2.5:3b-instruct | 500 | ~8.19s | **VAR** |
| C | Temiz model (rm + repull, hash doğrulandı) | qwen2.5:3b-instruct | 500 | ~7.51s | **VAR** |
| D | Farklı/daha büyük model | llama3:latest | 500 | ~7.91s | **VAR** |

**4/4 deney aynı imzayla çöktü.** Tek bir tekli-çağrı testi bile başarılı
olmadı.

## Adım detayları

### A) Baseline
- Komut: `POST /api/generate` (`qwen2.5:3b-instruct`, `stream:false`)
- Çıktı: `step_A_baseline.txt`
- Sonuç: `HTTP 500`, `llama-server process has terminated: exit status
  0xc0000005: The instruction at 0xp referenced memory at 0xp...`

### B) CPU izolasyon testi
- Komut: mevcut `ollama`/`ollama app` süreçleri durduruldu,
  `OLLAMA_NUM_GPU=0` env ile `ollama serve` yeniden başlatıldı, ardından
  aynı generate çağrısı tekrarlandı.
- Çıktı: `step_B_generate.txt`, sunucu logu `step_B_server_log_tail.txt`,
  değerlendirme notu `step_B_note.md`
- Sonuç: `HTTP 500`, aynı `0xc0000005`.
- **Önemli çekince:** `OLLAMA_NUM_GPU=0`'a rağmen sunucu logunda
  `Vulkan0 : Quadro RTX 3000` cihazı hâlâ listeleniyordu — bu ortam
  değişkeni Vulkan/GPU algılamasını tam kapatmamış olabilir. Bu deney
  "GPU'yu tamamen devre dışı bıraktık" iddiasını kanıtlamıyor, yalnızca
  "bu ayarla da çöküyor" bilgisini veriyor.

### C) Temiz model testi
- Komut: `ollama rm qwen2.5:3b-instruct` → `ollama pull qwen2.5:3b-instruct`
  (sha256 doğrulandı, `success`) → aynı generate çağrısı.
- Çıktı: `step_C_rm.txt`, `step_C_pull.txt`, `step_C_generate.txt`
- Sonuç: `HTTP 500`, aynı `0xc0000005`. Model dosyası bozukluğu ekarte
  edildi.

### D) llama3 testi
- Komut: aynı generate çağrısı, `model: "llama3:latest"` (8B, farklı model
  ailesi/boyutu).
- Çıktı: `step_D_generate.txt`
- Sonuç: `HTTP 500`, aynı `0xc0000005`.

## Ara değerlendirme

Çöküş; model boyutundan (1.9GB↔4.7GB), model ailesinden (qwen2↔llama),
GPU ayarından (`OLLAMA_NUM_GPU=0` denemesi dahil) ve model dosyası
bütünlüğünden (temiz repull) **bağımsız** olarak, her denemede birebir
aynı imzayla (`0xc0000005`) tekrarlandı. Bu, sorunun bu makinedeki
`llama-server.exe` binary'sinin kendisiyle veya onun bağımlı olduğu bir
sistem bileşeniyle (ör. Visual C++ Redistributable, Vulkan sürücü zinciri)
ilgili olduğuna işaret ediyor — modelle veya belirli bir yapılandırmayla
sınırlı değil.

**Sonuç: 50 çağrılık stabilite kapısı çalıştırılmadı** (görev talimatı:
"herhangi bir tekli test başarılı olursa batch testi çalıştır" — hiçbiri
başarılı olmadığından bu koşul hiç sağlanmadı, batch test anlamsız/israf
olurdu). **B031 yeniden ölçümü de bu nedenle çalıştırılmadı.**
