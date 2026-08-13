# Post-Upstream Experiment Batch #1 — Test Matrisi

- Amaç: çöküş riskini azaltacak/kesin sınırlayacak ayarları kontrollü,
  tekrar sayısı artırılmış (min. 20 çağrı/senaryo) koşularla doğrulamak ve
  upstream issue #17716 için ek, istatistiksel olarak daha güçlü kanıt
  üretmek.
- Model: `qwen2.5:3b-instruct` (bu makinede mevcut en küçük model).
- Script: `scripts/repro_b036_batch.ps1`
- Sonuçlar: `results.jsonl` (her çağrı için bir satır), ham sunucu logları:
  `raw_logs/`.

## Senaryolar

| ID | Açıklama | Env değişkenleri | Model/Options | Çağrı sayısı | Sunucu | Başarı kriteri |
|---|---|---|---|---|---|---|
| A | Vulkan ON, baseline (mevcut sürücü/ayarlar) | (yok) | `qwen2.5:3b-instruct`, varsayılan | 20 | Tek süreç, tüm çağrılar arasında paylaşılıyor | crash_count=0, success_rate=%100 |
| B | Vulkan OFF (CPU-only kontrol) | `OLLAMA_VULKAN=false` | `qwen2.5:3b-instruct`, varsayılan | 20 | Tek süreç, paylaşılıyor | crash_count=0, success_rate=%100 (biliniyor: p95 eşiği aşacak, bu ayrı bir konu) |
| C | Vulkan ON + açık düşürülmüş eşzamanlılık | `OLLAMA_NUM_PARALLEL=1` | `qwen2.5:3b-instruct`, varsayılan | 20 | Tek süreç, paylaşılıyor | crash_count=0 beklenmiyor — `OLLAMA_NUM_PARALLEL` zaten varsayılan olarak `1` (bkz. `matrix_A_stderr.log` server config dökümü); bu senaryo A'nın davranışsal olarak aynı olmasını **doğrulamak** için var, "yeni bir çözüm" iddiası değil |
| D | Vulkan ON + azaltılmış context | (yok) | `qwen2.5:3b-instruct`, `options.num_ctx=256` | 20 | Tek süreç, paylaşılıyor | Bu makinede kurulu daha küçük bir model yok (qwen 3B zaten en küçüğü); "azaltılmış context" gerçekten test edilebilen tek değişken |
| E | Vulkan ON + her çağrı öncesi temiz sunucu restart'ı | (yok) | `qwen2.5:3b-instruct`, varsayılan | 20 | Her çağrı için ayrı süreç (start → tek çağrı → stop) | Modelin her seferinde sıfırdan yüklenmesinin çöküşü etkileyip etkilemediğini test eder |

## Önceki kanıtla ilişki

A, B senaryoları önceki triage'da (bkz. `../gpu_isolation_matrix.md`) küçük
örneklemle (5 çağrı) zaten test edildi — sonuç: A için 5/5 çöküş, B için
5/5 başarı. Bu deney partisi, aynı bulguyu **20 çağrılık** daha büyük bir
örneklemle doğrulamak ve upstream issue'ya daha güçlü istatistiksel kanıt
eklemek için çalıştırılıyor — sonuç yönü değişmesi beklenmiyor, ama
gerçek ölçüm yapılmadan hiçbir sayı iddia edilmeyecek.

## Değerlendirme

Her senaryo için `results.jsonl`'den hesaplanacak: `crash_count`,
`success_count`, `success_rate`, `latency_p50`/`p95`. Nihai özet:
`summary.md`.
