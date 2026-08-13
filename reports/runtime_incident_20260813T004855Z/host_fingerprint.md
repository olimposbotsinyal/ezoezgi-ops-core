# Host Fingerprint — B036 Incident Triage

- Üretim tarihi (UTC): 2026-08-13T00:48:55Z
- Repo commit: bkz. `repo_commit_sha.txt` (bu klasörde, ilerleyen adımda eklendi)

## İşletim Sistemi

- Microsoft Windows 11 Pro
- Version: 10.0.22631, Build: 22631
- Mimari: 64 bit
- Toplam Fiziksel Bellek: 65.338 MB (~65.3 GB)

## GPU

- **Intel(R) UHD Graphics** (entegre)
- **NVIDIA Quadro RTX 3000** (ayrık, 6144 MiB VRAM)
  - `nvidia-smi` sürücü sürümü: **442.94**
  - `nvidia-smi` raporlanan CUDA sürümü: **10.2**

> **Kritik gözlem:** NVIDIA sürücüsü **442.94** — bu, ~2020 başı dönemine ait,
> **çok eski** bir sürücü. Ollama'nın kendi kurulumunda (`lib/ollama/`) hem
> `cuda_v12` hem `cuda_v13` backend klasörleri var — yani Ollama, CUDA 12/13
> uyumlu bir sürücü bekliyor, ama yüklü sürücü yalnızca CUDA 10.2
> destekliyor. Bu, ciddi bir sürüm uyuşmazlığı.

## Vulkan

- `vulkaninfo` mevcut (`C:\Windows\System32\vulkaninfo.exe`)
- **Vulkan Instance Version: 1.2.131** — yine eski/düşük bir Vulkan sürümü
  (modern Vulkan 1.3+ ile karşılaştırıldığında). `vulkan-1.dll` sistemde
  mevcut.
- İki Vulkan cihazı görünür: `GPU id 0 (Quadro RTX 3000)`,
  `GPU id 1 (Intel(R) UHD Graphics)`.

## Ollama

- Sürüm: `ollama version is 0.32.9`
- Kurulum yolu: `C:\Users\Serkan\AppData\Local\Programs\Ollama\`
- **Backend klasör yapısı** (`lib/ollama/`):
  - `ggml-cpu-*.dll` (ana klasörde, çoklu CPU mikro-mimarisi için ayrı ayrı:
    sandybridge, haswell, skylakex, icelake, sapphirerapids, zen4, vb.)
  - `cuda_v12/`, `cuda_v13/` (CUDA backend'leri — sürücü çok eski olduğu
    için muhtemelen hiç kullanılamıyor)
  - `rocm_v7_1/` (AMD ROCm, bu makinede alakasız)
  - `vulkan/ggml-vulkan.dll` (50MB, Vulkan backend — sürücünün desteklediği
    en "genel" GPU yolu, şu an fiilen kullanılan yol bu görünüyor)

## Python / Repo

- Python: `Python 3.13.3` (proje `.venv`)
- Repo commit (bu triage başlangıcında): bkz. `repo_commit_sha.txt`

## Event Viewer — Application Error kayıtları

`event_viewer_crash_entries.txt` dosyasında **20 adet** `llama-server.exe`
çökme kaydı (Event ID 1000, `Application Error`), hepsi:
- İstisna kodu: `0xc0000005` (STATUS_ACCESS_VIOLATION)
- Hata uzaklığı: `0x0000000000000000`
- **Hatalı modül adı: `unknown`** — Windows, çökmeye sebep olan DLL'i
  tanımlayamamış. Bu, hatanın genellikle derin bir sürücü/GPU-ICD
  etkileşiminde (sembol bilgisi eksik üçüncü taraf bir modülde) olduğu
  durumlarda görülen tipik bir imza.
- 2026-08-13 03:20–03:44 arası (bu ve önceki triage oturumlarına karşılık
  gelen zaman aralığı).

## Ön değerlendirme (kanıta dayalı, henüz kesin değil)

Üç bağımsız kanıt parçası aynı yöne işaret ediyor:
1. NVIDIA sürücüsü (442.94/CUDA 10.2) Ollama'nın beklediği CUDA 12/13'ten
   çok eski.
2. Vulkan Instance Version (1.2.131) düşük/eski.
3. Event Viewer'da hatalı modül "unknown" — düşük seviye sürücü etkileşimi
   ile tutarlı.

Bu, **GPU sürücü/Vulkan yığınının güncelliğini yitirmiş olması**nı en
olası kök neden adayı yapıyor. Bölüm 3'teki izolasyon matrisi bunu daha
kesin olarak test ediyor.
