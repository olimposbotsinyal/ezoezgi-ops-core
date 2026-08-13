# Step B — CPU izolasyon testi (OLLAMA_NUM_GPU=0)

- Komut: mevcut ollama/ollama-app süreçleri durduruldu, `OLLAMA_NUM_GPU=0` env
  ile `ollama serve` yeniden başlatıldı.
- Generate çağrısı sonucu: yine `HTTP 500`, yine `0xc0000005` (bkz.
  step_B_generate.txt) — elapsed ~8.19s.
- **Önemli/beklenmedik gözlem** (server.log'dan, bkz. step_B_server_log_tail.txt):
  `OLLAMA_NUM_GPU=0` ayarına rağmen llama-server'ın kendi başlatma logu hâlâ
  `Vulkan0 : Quadro RTX 3000 (5980 MiB, 5212 MiB free)` cihazını listeliyor.
  Yani bu ortam değişkeni Vulkan/GPU algılamasını tam olarak devre dışı
  bırakmamış olabilir (Ollama 0.32.9'da GPU katman sayısını sınırlayan bir
  ayar olabilir, Vulkan backend seçimini engellemeyebilir).
- **Sonuç:** Bu deney GPU'yu kesin olarak devre dışı bırakamadığı için
  "CPU-only'de de çöküyor" iddiası bu haliyle **kısmen doğrulanmış** sayılır
  — çöküş tekrarlandı, ama Vulkan/GPU cihazı hâlâ görünür durumdaydı, o yüzden
  GPU/Vulkan sürücü etkileşimi tam olarak ekarte edilemedi.
