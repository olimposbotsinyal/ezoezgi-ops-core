# Model Fallback Runbook — Model Gateway (B036 resilience layer)

> Kapsam: `services/model-gateway/src/model_gateway/`. Bu katman, Ollama'nın
> Windows/Vulkan çalışma zamanı kararsızlığına (bkz. B036,
> `reports/runtime_incident_20260813T004855Z/`) karşı sıralı, açık-loglu
> bir fallback mekanizması ekler. **Bu, B036'yı çözmez** — kök nedeni
> (GPU/Vulkan sürücü etkileşimi) etkilemez, yalnızca sistemin tek bir
> saglayıcının çökmesiyle tamamen durmasını önler ve her geçişi
> denetlenebilir kılar.

## Sağlayıcı sırası (varsayılan: yalnızca local-first)

`MODEL_PROVIDER_ORDER` varsayılan değeri **`ollama`** — yani varsayılan
kurulumda yalnızca Ollama denenir, hiçbir fallback yoktur (mevcut
davranışla birebir aynı). Çok saglayıcılı fallback'i etkinleştirmek
isteğe bağlıdır:

```bash
MODEL_PROVIDER_ORDER=ollama,local_alt,remote
```

| Sıra | Saglayıcı | Varsayılan durum | Etkinleştirme |
|---|---|---|---|
| 1 (birincil) | `ollama` | Açık | `OLLAMA_ENABLED=true` (varsayılan) |
| 2 (ikincil) | `local_alt` | **Kapalı** | `LOCAL_ALT_ENABLED=true` + host/model |
| 3 (üçüncül) | `remote` | **Kapalı + politika kapılı** | `REMOTE_ENABLED=true` **VE** `policies/risk/tool_risk_policy.yaml`'da `remote_model_policy.allowed: true` |

## Ortam değişkenleri (tam liste)

```bash
MODEL_PROVIDER_ORDER=ollama          # virgülle ayrılmış, varsayılan: yalnızca ollama
OLLAMA_ENABLED=true
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3

LOCAL_ALT_ENABLED=false
LOCAL_ALT_TYPE=lmstudio|llamacpp|vllm
LOCAL_ALT_HOST=http://localhost:8080
LOCAL_ALT_MODEL=

REMOTE_ENABLED=false
REMOTE_PROVIDER=openai|...
REMOTE_MODEL=
REMOTE_TIMEOUT_MS=10000
REMOTE_POLICY_GATE=required          # "required" dışında bir değer politika kontrolünü atlar -- DEĞİŞTİRMEYİN
REMOTE_API_KEY=                      # yalnızca remote_provider.py tarafından okunur, hiçbir yerde loglanmaz

FALLBACK_MAX_HOPS=2
CIRCUIT_BREAKER_FAILS=3
CIRCUIT_BREAKER_RESET_SEC=120
```

Opsiyonel YAML override: `config/model_gateway.yaml` (env değişkeni
tanımlıysa env kazanır — bkz. `model_gateway/config.py`).

## Başlangıç kontrol listesi (startup checklist)

1. **Ollama sunucusunu `OLLAMA_VULKAN=false` ile başlatın** (bkz.
   `docs/ops/OLLAMA_WORKAROUND_CPU_ONLY.md`). **Önemli sınırlama:**
   `OllamaProvider`, `OLLAMA_VULKAN=false`'ı yalnızca **kendi Python
   sürecinin** ortamına yazar — `ollama serve` bu kod tarafından
   başlatılmadığı için (harici, ayrı bir sistem süreci), bu ayar zaten
   çalışan bir Ollama sunucusunu **etkilemez**. Sunucuyu doğru env ile
   başlatmak operatörün sorumluluğundadır.
2. Aşağıdaki healthcheck komutuyla tüm sağlayıcıların sağlık durumunu
   kontrol edin (bkz. "Healthcheck komutları").
3. `policies/risk/tool_risk_policy.yaml`'daki `remote_model_policy.allowed`
   değerinin beklediğiniz gibi olduğunu doğrulayın (varsayılan: `false`).
4. `data/audit/audit.log.jsonl`'in yazılabilir olduğunu doğrulayın (router
   her istekte buraya yazar).

## Healthcheck komutları

```bash
# Tüm sağlayıcıların sağlık durumu
python -c "from model_gateway.router import get_router; print(get_router().healthcheck_all())"

# Yalnızca Ollama (mevcut, değişmemiş yöntem)
powershell -File scripts/repro_ollama_crash.ps1
```

## Olay (incident) aksiyonları

Router bir fallback event'i ürettiğinde (`data/audit/audit.log.jsonl`'de
`task=MODEL_GATEWAY_GENERATE`, `status=FALLBACK`), `details.reason_code`
alanına göre:

| reason_code | Anlamı | Aksiyon |
|---|---|---|
| `PRIMARY_UNHEALTHY` | Sağlayıcının health check'i başarısız (servis tamamen erişilemez) | Servisin ayakta olup olmadığını kontrol edin |
| `RUNTIME_CRASH` | Health check başarılıydı ama generate() çöktü — Ollama için bu **bilinen B036 deseni** (0xc0000005) | `docs/ops/OLLAMA_WORKAROUND_CPU_ONLY.md`'ye bakın |
| `TIMEOUT` | İstek zaman aşımına uğradı | Timeout değerini gözden geçirin (`OLLAMA_TIMEOUT_SECONDS`/`REMOTE_TIMEOUT_MS`) |
| `POLICY_BLOCK` | Politika remote kullanımına izin vermiyor | Bilinçli bir tasarım — izin gerekiyorsa `tool_risk_policy.yaml`'ı güncelleyin |
| `DISABLED` | Sağlayıcı config'de kapalı | Beklenen davranış, aksiyon gerekmez |
| `CIRCUIT_OPEN` | Art arda `CIRCUIT_BREAKER_FAILS` kez başarısız oldu, `CIRCUIT_BREAKER_RESET_SEC` saniye beklenmeden tekrar denenmiyor | Kök nedeni düzeltin, devre kendiliğinden yarı-açılacak |

## Geri alma (rollback): yalnızca ollama-only CPU modu

Herhangi bir sorun durumunda, sistemi bilinen-kararlı tek-sağlayıcı
duruma döndürmek için:

```bash
MODEL_PROVIDER_ORDER=ollama
OLLAMA_ENABLED=true
LOCAL_ALT_ENABLED=false
REMOTE_ENABLED=false
OLLAMA_VULKAN=false   # ollama serve'i BUNUNLA başlatın (harici komut)
```

Bu, model gateway kodunu kaldırmadan, yalnızca config ile mevcut
CPU-only workaround'a (`docs/ops/OLLAMA_WORKAROUND_CPU_ONLY.md`) geri
döner. Kod seviyesinde tam geri alma gerekiyorsa: `ollama_nlu.py`'deki
`RouterBackedClient` entegrasyonunu içeren commit'i revert edin — bu,
`ollama_nlu.classify()`'ı doğrudan `OllamaModelClient` kullanan önceki
haline döndürür (bkz. bu runbook'un dayandığı commit çifti: Commit A
altyapı, Commit B entegrasyon — yalnızca Commit B'yi revert etmek
yeterlidir, Commit A'nın varlığı davranışı değiştirmez).

## Bilinen sınırlamalar

- `remote_provider.py` yalnızca OpenAI-uyumlu `/v1/chat/completions`
  şeklini konuşur — Anthropic gibi farklı API şekline sahip sağlayıcılar
  için ayrı bir adaptör gerekir (henüz yok).
- `local_alt_provider.py`, LM Studio/llama.cpp server/vLLM için ortak bir
  OpenAI-uyumlu istemci varsayar — bu üç backend türünden hiçbiri bu
  projede kurulu/test edilmedi (varsayım, doğrulanmadı).
- Circuit breaker durumu yalnızca bellek-içi (in-process) — süreç yeniden
  başlarsa sıfırlanır.
- `OLLAMA_VULKAN=false` zorlaması yalnızca bu Python sürecinin kendi
  ortamına yazılır (yukarıdaki "Başlangıç kontrol listesi" madde 1'e
  bakın) — harici `ollama serve` sürecini etkilemez.
