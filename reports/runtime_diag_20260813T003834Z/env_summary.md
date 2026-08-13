# Ortam Diagnostik Özeti — B036 Runtime Stabilizasyon Kapısı

- Üretim tarihi (UTC): 2026-08-13T00:38:34Z
- Repo commit (bu koşu başlangıcında): `0696be1448bb43dcbdf9cf0d5d9cd5ac2aee93b4`

## Ollama

- Sürüm: `ollama version is 0.32.9`
- `ollama ps`: boş (çalışan/yüklü model instance yok)
- `ollama list`:
  - `qwen2.5:3b-instruct` (357c53fb659c, 1.9 GB)
  - `llama3:latest` (365c0bd3c000, 4.7 GB)
- `curl http://localhost:11434/api/tags` → `HTTP_STATUS=200` (API katmanı sağlıklı)

## Python

- `Python 3.13.3` (proje `.venv`)

## İşletim Sistemi

- Microsoft Windows 11 Pro
- Version: 10.0.22631, Build: 22631
- Mimari: 64 bit

## CPU

- Intel(R) Core(TM) i9-10980HK CPU @ 2.40GHz
- 8 çekirdek / 16 mantıksal işlemci

## GPU

- Intel(R) UHD Graphics (entegre, ~1GB paylaşımlı bellek)
- **NVIDIA Quadro RTX 3000** (ayrık GPU, ~4GB VRAM, sürücü 26.21.14.4294)

**Not:** Ayrık bir NVIDIA GPU'nun varlığı önemli — Ollama varsayılan olarak
GPU hızlandırmasını dener. `0xc0000005` çöküşünün bir CUDA/GPU sürücü
uyumsuzluğundan kaynaklanma ihtimali var; bu yüzden Bölüm 3'teki "CPU
izolasyon testi" (`OLLAMA_NUM_GPU=0`) özellikle anlamlı bir deney.

Ham komut çıktıları bu klasördeki `ollama_version.txt`, `ollama_ps.txt`,
`ollama_list.txt`, `curl_tags.txt`, `os_info.txt`, `gpu_info.txt`,
`cpu_info.txt`, `python_version.txt`, `repo_commit_sha.txt` dosyalarında.
