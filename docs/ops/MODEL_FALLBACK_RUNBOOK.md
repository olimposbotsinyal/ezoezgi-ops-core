# Model Fallback Runbook — Model Gateway (B036 resilience layer)

> Kapsam: `services/model-gateway/src/model_gateway/`. Bu katman, Ollama'nın
> Windows/Vulkan çalışma zamanı kararsızlığına (bkz. B036,
> `reports/runtime_incident_20260813T004855Z/`) karşı sıralı, açık-loglu
> bir fallback mekanizması ekler. **Bu, B036'yı çözmez** — kök nedeni
> (GPU/Vulkan sürücü etkileşimi) etkilemez, yalnızca sistemin tek bir
> saglayıcının çökmesiyle tamamen durmasını önler ve her geçişi
> denetlenebilir kılar.

## Operatör hızlı aksiyonlar (özet)

| Belirti | Hızlı aksiyon |
|---|---|
| **Marker eksik** | Marker dosyasını oluştur: `docs/ops/MODEL_FALLBACK_RUNBOOK.md` § "Operatör kontrol listesi", veya geçici olarak `OLLAMA_CPU_VERIFY_STRICT=false` |
| **Primary restricted** (Ollama hiç denenmiyor) | `reports/daily_smoke_<UTC>/preflight_snapshot.json`'a bak veya `scripts/ops/daily_gateway_smoke.ps1`'i çalıştır; `status != VERIFIED` ise marker eksik/eski demektir |
| **Null-intent spike** | `scripts/ops/package_gateway_incident.ps1` ile kanıt paketi topla, `audit_slice.jsonl`'deki `reason_code` dağılımına bak (`RUNTIME_CRASH` ise B036, `PRIMARY_RESTRICTED_CPU_UNVERIFIED` ise marker) |
| **Fallback exhausted** (hiçbir sağlayıcı çalışmıyor) | `python -c "from model_gateway.router import get_router; print(get_router().healthcheck_all())"` ile canlı sağlık durumunu kontrol et |

Tam alert listesi ve eşikler: `docs/ops/ALERT_PLAYBOOK_MODEL_GATEWAY.md`.
SLO tanımları: `docs/ops/SLO_MODEL_GATEWAY.md`.

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

OLLAMA_CPU_VERIFY_ENABLED=true        # kapatmak icin false -- kapaninca Ollama eski davranisina doner
OLLAMA_CPU_VERIFY_STRICT=true         # bkz. asagidaki "ONEMLI DAVRANIS SONUCU" notu
OLLAMA_CPU_VERIFY_TIMEOUT_MS=1200
OLLAMA_CPU_VERIFY_METHODS=http,process,marker
OLLAMA_CPU_MARKER_FILE=./runtime/ollama_cpu_mode.ok
OLLAMA_ON_UNVERIFIED=RESTRICT_PRIMARY # su an desteklenen tek deger
STARTUP_PREFLIGHT_REQUIRED=true
OLLAMA_CPU_VERIFY_CACHE_TTL_SEC=60    # her istekte degil, bu araliklarla yeniden dogrulanir
```

> **ÖNEMLİ DAVRANIŞ SONUCU:** Bu dört değişkenin (`OLLAMA_CPU_VERIFY_ENABLED`,
> `_STRICT`, marker dosyası yok) **hepsi varsayılan** kalırsa, operatör
> `OLLAMA_CPU_MARKER_FILE`'ı elle oluşturmadığı sürece doğrulama neredeyse
> her zaman `UNKNOWN`/`SIGNAL_NOT_AVAILABLE` döner ve **Ollama STRICT modda
> birincil olarak seçilmez** — sistem, Ollama gerçekten sağlıklı çalışıyor
> olsa bile `local_alt`/`remote` (varsayılan kapalı) yoksa doğrudan
> null-intent'e düşer. Bu, **kasıtlı, dokümante edilmiş bir tasarım
> kararıdır** ("sessiz davranış değişikliği yok" ilkesi gereği burada
> yüksek sesle belirtiliyor) — operatörün ya marker dosyasını oluşturması
> (bkz. aşağıdaki kontrol listesi) ya da bilerek `OLLAMA_CPU_VERIFY_STRICT=false`
> veya `OLLAMA_CPU_VERIFY_ENABLED=false` seçmesi gerekir.

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

Router bir fallback/skip event'i ürettiğinde (`data/audit/audit.log.jsonl`'de
`task=MODEL_GATEWAY_GENERATE`, `status=FALLBACK` veya `SKIPPED`),
`details.reason_code` alanına göre:

| reason_code | Anlamı | Aksiyon |
|---|---|---|
| `PRIMARY_UNHEALTHY` | Sağlayıcının health check'i başarısız (servis tamamen erişilemez) | Servisin ayakta olup olmadığını kontrol edin |
| `RUNTIME_CRASH` | Health check başarılıydı ama generate() çöktü — Ollama için bu **bilinen B036 deseni** (0xc0000005) | `docs/ops/OLLAMA_WORKAROUND_CPU_ONLY.md`'ye bakın |
| `TIMEOUT` | İstek zaman aşımına uğradı | Timeout değerini gözden geçirin (`OLLAMA_TIMEOUT_SECONDS`/`REMOTE_TIMEOUT_MS`) |
| `POLICY_BLOCK` | Politika remote kullanımına izin vermiyor | Bilinçli bir tasarım — izin gerekiyorsa `tool_risk_policy.yaml`'ı güncelleyin |
| `DISABLED` | Sağlayıcı config'de kapalı | Beklenen davranış, aksiyon gerekmez |
| `CIRCUIT_OPEN` | Art arda `CIRCUIT_BREAKER_FAILS` kez başarısız oldu, `CIRCUIT_BREAKER_RESET_SEC` saniye beklenmeden tekrar denenmiyor | Kök nedeni düzeltin, devre kendiliğinden yarı-açılacak |
| `PRIMARY_RESTRICTED_CPU_UNVERIFIED` | CPU-only doğrulama VERIFIED dönmedi, STRICT modda Ollama hiç denenmedi | Bkz. yukarıdaki "CPU-only doğrulama" bölümü — genellikle marker dosyası eksik |
| `FALLBACK_EXHAUSTED` | Sıradaki TÜM sağlayıcılar (atlananlar dahil) tükendi, `AllProvidersFailedError` fırlatıldı | `attempts` listesindeki her adımın reason_code'una tek tek bakın |

### Yeni audit event tipleri (CPU-verify kapısı)

Yukarıdaki `MODEL_GATEWAY_GENERATE` event'ine ek olarak, CPU-verify kapısı
kendi `task` değerleriyle ayrı event'ler yazar (aynı `trace_id` ile
korelasyonlanabilir):

| task | Ne zaman yazılır | Önemli alanlar |
|---|---|---|
| `OLLAMA_CPU_PREFLIGHT_CHECKED` | Doğrulama gerçekten çalıştığında (cache hit'te YAZILMAZ) | `verification_status`, `verification_reason_code`, `evidence` |
| `OLLAMA_PRIMARY_RESTRICTED` | STRICT modda Ollama kısıtlandığında | `action_taken=RESTRICT_PRIMARY` |

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

**CPU-verify kapısı beklenmedik şekilde Ollama'yı kısıtlıyorsa** (bkz.
yukarıdaki "ÖNEMLİ DAVRANIŞ SONUCU"), en hızlı geri alma kod değişikliği
gerektirmez:

```bash
OLLAMA_CPU_VERIFY_ENABLED=false   # kapıyı tamamen kapat, Commit C/D öncesi davranış
# veya, dogrulamayi acik tutup yalnizca zorlamayi gevsetmek icin:
OLLAMA_CPU_VERIFY_STRICT=false
```

## CPU-only doğrulama kanıt temellidir, sihirli değildir

> **Durum:** Bu bölümde anlatılan doğrulama mekanizması router'a
> **bağlıdır** — `MODEL_PROVIDER_ORDER`'da `ollama` denenmeden hemen önce
> çalışır. STRICT modda `VERIFIED` dönmeyen bir sonuç, Ollama'nın o turda
> hiç denenmemesi anlamına gelir (bkz. yukarıdaki "ÖNEMLİ DAVRANIŞ SONUCU"
> ve "Olay aksiyonları" tablosundaki `PRIMARY_RESTRICTED_CPU_UNVERIFIED`).
> Aşağıdaki kısım, bu kararı besleyen kanıt toplama mekanizmasını anlatır.

Önceki bölümde belirtildiği gibi, `OLLAMA_VULKAN=false` zorlaması yalnızca
bu Python sürecinin kendi ortamına yazılır — harici, zaten çalışan bir
`ollama serve` sürecini **etkilemez**. `model_gateway/runtime_verify.py`,
bu operasyonel güvenlik açığını **kapatmaz** (kapatamaz) — yalnızca
**kanıt toplar** ve o kanıda göre bir karar verir. Kesinlik iddia etmez.

Üç kanıt kaynağı (`OLLAMA_CPU_VERIFY_METHODS` ile yapılandırılabilir):

| Yöntem | Kanıt türü | Ne zaman güvenilir |
|---|---|---|
| `marker` | **Pozitif** — operatörün elle oluşturduğu bir dosya | Operatör gerçekten doğru env ile başlattıysa |
| `process` | **Negatif** — Windows Event Viewer'da yakın zamanlı `llama-server.exe`/`0xc0000005` kaydı | Yalnızca Windows'ta, yalnızca bir çöküş zaten OLDUYSA |
| `http` | **Zayıf negatif** — `/api/ps` üzerinden yüklü bir modelin VRAM kullanıp kullanmadığı | Yalnızca bir model zaten yüklüyse |

**Neden otomatik pozitif doğrulama yok:** Python'dan/PowerShell'den harici,
zaten çalışan bir sürecin ortam değişkenlerini okumenin genel, ayrıcalık
gerektirmeyen bir yolu yoktur (.NET `Process.StartInfo.EnvironmentVariables`
yalnızca SİZİN başlattığınız süreçler için doludur). Bu yüzden tek
güvenilir pozitif sinyal, operatörün kendi beyanıdır (marker dosyası).

### Operatör kontrol listesi: marker dosyası oluşturma/doğrulama

1. Ollama sunucusunu doğru şekilde başlattığınızdan emin olun:
   ```bash
   set OLLAMA_VULKAN=false
   ollama serve
   ```
2. Gerçekten çöküşsüz çalıştığını doğrulayın (en az bir başarılı
   `/api/generate` çağrısı, bkz. `scripts/repro_ollama_crash.ps1`).
3. Marker dosyasını oluşturun (yol: `OLLAMA_CPU_MARKER_FILE`, varsayılan
   `./runtime/ollama_cpu_mode.ok`):
   ```powershell
   New-Item -ItemType Directory -Force -Path .\runtime | Out-Null
   '{"verified_at":"<ISO8601>","method":"manual","operator":"<isminiz>"}' | Set-Content .\runtime\ollama_cpu_mode.ok
   ```
4. Marker dosyası **24 saat** taze sayılır (`runtime_verify.py`'deki
   `MARKER_MAX_AGE_SECONDS` sabiti) — bu süre sonunda operatörün tekrar
   doğrulayıp dosyayı yeniden oluşturması gerekir (basitçe `Set-Content`
   ile üzerine yazmak, mtime'ı günceller, yeterlidir).

### Bağımsız doğrulama kontrolünü çalıştırma

```bash
python -c "
from model_gateway.runtime_verify import verify_ollama_cpu_mode
r = verify_ollama_cpu_mode(
    ollama_healthy=True,  # veya gercek bir health_check() sonucu
    base_url='http://localhost:11434',
    marker_file='./runtime/ollama_cpu_mode.ok',
    methods=('http', 'process', 'marker'),
    timeout_ms=1200,
)
print(r.status, r.reason_code)
print(r.evidence)
"
```

### UNVERIFIED/UNKNOWN durumunda ne yapmalı

- **Beklenen bir durumdur, hata değildir** — operatör marker dosyası
  oluşturmadıysa (yukarıya bakın) bu, `OLLAMA_CPU_VERIFY_STRICT=true`
  varsayılanıyla **normaldir**.
- STRICT modda kalmak isteniyorsa: marker dosyasını oluşturun (yukarıdaki
  kontrol listesi).
- Geçici olarak eski (workaround öncesi) davranışa dönmek isteniyorsa:
  `OLLAMA_CPU_VERIFY_ENABLED=false` (dogrulama tamamen kapanir) veya
  `OLLAMA_CPU_VERIFY_STRICT=false` (dogrulanamasa bile Ollama yine de
  denenir, yalnizca uyari loglanir).
- `reason_code=ENV_MISMATCH` görürseniz bu **negatif kanıttır** (yakın
  zamanlı çöküş veya VRAM kullanımı tespit edildi) — bu durumda marker
  dosyası oluşturmak yerine önce gerçek sorunu çözün
  (`docs/ops/OLLAMA_WORKAROUND_CPU_ONLY.md`).

## Gözlemlenebilirlik ve Ops Otomasyonu

> Tam SLI/SLO tanımları: `docs/ops/SLO_MODEL_GATEWAY.md`. Tam alert
> listesi: `docs/ops/ALERT_PLAYBOOK_MODEL_GATEWAY.md`.

### Metrikler

`METRICS_ENABLED=true` (varsayılan) ile `services/model-gateway/src/model_gateway/metrics.py`
tüm istekleri/fallback'leri/kısıtlamaları/gecikmeleri bellek-içi olarak
biriktirir. `METRICS_EXPORTER=prometheus` ile Prometheus exposition-format
metnini üretebilir (`MetricsRegistry.render_prometheus_text()`) — ama
şu an bunu sunan canlı bir `/metrics` HTTP endpoint'i **yok**. Pratik
kullanım: `scripts/ops/daily_gateway_smoke.ps1` her koşuda
`metrics_snapshot.json`'a yazar.

### Günlük smoke kontrolü

```bash
powershell -ExecutionPolicy Bypass -File scripts\ops\daily_gateway_smoke.ps1
```

Config sanity, preflight anlık görüntüsü, sağlayıcı sağlığı, tek bir
yıkıcı-olmayan sentetik `classify()` probu ve audit-append doğrulamasını
çalıştırır; kanıtı `reports/daily_smoke_<UTC>/` altına yazar (bkz.
`.gitignore` — bu klasörler varsayılan olarak repoya eklenmez, günlük
biriken rutin çıktılar). Çıkış kodu:

| Kod | Anlam |
|---|---|
| `0` | Sağlıklı — gerçek bir model yanıtı alındı |
| `1` | Bozulmuş ama çalışıyor — genellikle STRICT CPU-verify kapısının kasıtlı kısıtlaması |
| `2` | Aksiyon gerekiyor — audit yazılamıyor, hiçbir sağlayıcı sağlıklı değil, veya `remote_policy_gate` zayıflatılmış |

### Olay kanıt paketleyici

```bash
powershell -ExecutionPolicy Bypass -File scripts\ops\package_gateway_incident.ps1 `
    -WindowStart "<ISO8601>" -WindowEnd "<ISO8601>" -KnownGoodRef "<git-sha-veya-tag>"
```

Verilen zaman penceresi için audit dilimini, sağlayıcı/preflight anlık
görüntüsünü, **maskelenmiş** config parmak izini (`REMOTE_API_KEY` gibi
anahtar-benzeri adlar otomatik maskelenir — bkz.
`scripts/ops/package_gateway_incident_core.py::mask_secrets`) ve son
bilinen-iyi referanstan bu yana değişen dosyaları
`reports/incidents/gateway_<timestamp>/` altına paketler (bu klasör de
varsayılan olarak repoya eklenmez).

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
- **CPU-verify kapısı, varsayılan ayarlarla ve operatör eylemi olmadan,
  pratikte Ollama'yı hiç kullanılamaz hale getirebilir** (bkz.
  "Ortam değişkenleri" bölümündeki "ÖNEMLİ DAVRANIŞ SONUCU" uyarısı) —
  bu bilinçli bir tasarım kararıdır, ama kurulumdan sonra ilk fark edilen
  şey "neden Ollama artık hiç kullanılmıyor" olabilir. İlk kurulumda
  mutlaka marker dosyası oluşturun veya `OLLAMA_CPU_VERIFY_STRICT=false`
  seçin.
- **Metrikler yalnızca bellek-içi** — süreç yeniden başlarsa sıfırlanır,
  canlı bir Prometheus scrape endpoint'i yok. `FALLBACK_SPIKE` gibi
  kısa-pencereli alert'ler bu yüzden şu an yalnızca dokümante edilmiş
  (`infra/monitoring/prometheus/model_gateway_alerts.yaml`), gerçekten
  değerlendirilmiyor — bkz. `docs/ops/ALERT_PLAYBOOK_MODEL_GATEWAY.md`
  "Bilinen sınırlamalar".
