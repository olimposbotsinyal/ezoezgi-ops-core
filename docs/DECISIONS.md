# DECISIONS — Architecture Decision Records (ADR)

> Format: Karar ID | Tarih | Karar | Gerekçe | Alternatif | Sonuç

---

## ADR-001
- **Tarih:** 2026-08-12
- **Karar:** Local model çalışma zamanı olarak **Ollama** kullanılacak.
- **Gerekçe:** Local-first hedefi; bulut API maliyeti/gecikmesi ve veri gizliliği
  riskleri olmadan model çalıştırma ihtiyacı.
- **Alternatif:** LM Studio, llama.cpp doğrudan entegrasyonu, bulut-only (OpenAI/Anthropic API).
- **Sonuç:** Kabul edildi. `services/tr-en-bridge` ve diğer ajanlar Ollama endpoint'i
  üzerinden model çağıracak (bkz. PLAN.md T8).

## ADR-002
- **Tarih:** 2026-08-12
- **Karar:** Konuşmadan metne (STT) için **Whisper** kullanılacak.
- **Gerekçe:** Açık kaynak, local çalışabilir, çok dilli destek (TR dahil) olgun.
- **Alternatif:** Vosk, bulut STT servisleri (Google/Azure).
- **Sonuç:** Kabul edildi. `services/stt-whisper` iskeleti bu kararla ayrıldı;
  entegrasyon Faz 3'te (bkz. MASTER_ROADMAP.md §7).

## ADR-003
- **Tarih:** 2026-08-12
- **Karar:** Ajan orkestrasyonu **Hermes/Crew-tarzı** (rol bazlı, görev grafiği ile
  ajan çağıran) bir yaklaşımla yapılacak.
- **Gerekçe:** Basit tek-model çağrısı yerine, uzmanlaşmış ajanların görev bazlı
  seçilip çağrılmasına ihtiyaç var (finans, araştırma, sosyal medya, cihaz vb.).
- **Alternatif:** LangGraph tarzı state-machine orkestrasyon, tamamen custom kod.
- **Sonuç:** Kabul edildi. Kesin kütüphane seçimi (CrewAI/Hermes-uyumlu framework
  vs. custom) Faz 1'de ayrı bir ADR ile netleşecek — bkz. BACKLOG.md.

## ADR-004
- **Tarih:** 2026-08-12
- **Karar:** İzleme (monitoring) için **Prometheus + Grafana** kullanılacak.
- **Gerekçe:** Self-hosted, local-first hedefiyle uyumlu, endüstri standardı,
  servis sayısı arttıkça (10+ servis) gözlemlenebilirlik kritik hale geliyor.
- **Alternatif:** Bulut APM (Datadog, New Relic), sade log dosyası + grep.
- **Sonuç:** Kabul edildi. Kurulum Faz 6'da (`infra/monitoring`) — bkz.
  MASTER_ROADMAP.md §7.

## ADR-005
- **Tarih:** 2026-08-12
- **Karar:** Bağımlılık güvenlik taraması için **OSV** (OSV-Scanner) kullanılacak.
- **Gerekçe:** Açık kaynak, ücretsiz, çoklu ekosistem (npm/pip/vb.) desteği, CI'ya
  kolay entegre edilebilir.
- **Alternatif:** Snyk, GitHub Dependabot (yalnızca GitHub'a bağımlı kalır).
- **Sonuç:** Kabul edildi. CI entegrasyonu Faz 4'te — bkz. BACKLOG.md B016.

## ADR-006
- **Tarih:** 2026-08-12
- **Karar:** Ana geliştirme dili **Python 3.12** olacak.
- **Gerekçe:** Ekosistem olgunluğu (ML/finans/otomasyon kütüphaneleri), Ollama/Whisper
  istemcileri ve borsa SDK'larının çoğunun Python desteği, tek dilde tutarlılık.
- **Alternatif:** Node.js/TypeScript, Go.
- **Sonuç:** Kabul edildi. `apps/` ve `services/` altındaki tüm servisler Python 3.12
  ile yazılacak (arayüz/PWA katmanı hariç).

## ADR-007
- **Tarih:** 2026-08-12
- **Karar:** Backend framework **FastAPI** olacak.
- **Gerekçe:** Async destek (borsa/websocket bağlantıları için önemli), otomatik
  OpenAPI şeması, Pydantic ile tip güvenliği, olgun ekosistem.
- **Alternatif:** Flask, Django REST Framework.
- **Sonuç:** Kabul edildi. `apps/api` ve dış HTTP arayüzü olan servisler FastAPI
  kullanacak.

## ADR-008
- **Tarih:** 2026-08-12
- **Karar:** Orchestrator yaklaşımı — başlangıçta **custom lightweight orchestrator**,
  ilerleyen fazda **CrewAI adaptörü** ile genişletilecek.
- **Gerekçe:** Faz 0–1'de basit, denetlenebilir, bağımlılığı az bir çekirdek
  (task graph + registry) yeterli; CrewAI gibi framework'lerin erken kilitlenmesi
  esnekliği azaltır. CrewAI adaptörü, ajan sayısı arttığında (Faz 2+) devreye girer.
- **Alternatif:** Baştan CrewAI/LangGraph'e bağlanmak, tamamen custom kalmaya devam.
- **Sonuç:** Kabul edildi. ADR-003'ü netleştirir/somutlaştırır. `apps/orchestrator`
  önce custom registry (bkz. PLAN.md T11) ile başlar; CrewAI adaptör kararı ayrı bir
  ADR ile Faz 2'de teyit edilecek.

## ADR-009
- **Tarih:** 2026-08-12
- **Karar:** Audit log formatı **JSONL, append-only** olacak
  (`data/audit/audit.log.jsonl`).
- **Gerekçe:** Satır bazlı, stream'e uygun, kolay parse edilebilir; append-only
  disiplinle geriye dönük değiştirilemez kayıt garantisi (özellikle finans işlemleri
  ve onay kararları için kritik — bkz. MASTER_ROADMAP.md §5, §8).
- **Alternatif:** SQLite, düz metin log, harici log servisi.
- **Sonuç:** Kabul edildi. Şema ve logger iskeleti PLAN.md T17 kapsamında yazılacak.

## ADR-010
- **Tarih:** 2026-08-12
- **Karar:** Config standardı **JSON + `.env`**; secret'lar yalnızca env/vault
  üzerinden sağlanacak, hiçbir zaman JSON config dosyalarına yazılmayacak.
- **Gerekçe:** JSON, statik/versiyonlanabilir config (identity, risk eşikleri vb.)
  için yeterli ve okunabilir; secret'ların (API key, withdraw-kapalı borsa anahtarları
  dahil) git'e sızma riskini `.env`/vault ayrımıyla azaltmak gerekiyor.
- **Alternatif:** YAML config, tüm config'i env değişkeninde tutmak, dedicated
  secret manager (Vault/1Password) baştan zorunlu kılmak.
- **Sonuç:** Kabul edildi. `.env` dosyaları `.gitignore`'da kalır; ileride vault
  entegrasyonu ayrı bir ADR ile değerlendirilebilir.

## ADR-011
- **Tarih:** 2026-08-12
- **Karar:** Finance execution deferred until legacy bot parity analysis —
  gerçek/mock finans işlem yürütme (PLAN.md T19 ve sonrası), Gelisen_Bot parity
  analizi tamamlanana kadar kapalı tutulacak.
- **Gerekçe:** Kaynak proje Gelisen_Bot (`C:\Users\Serkan\PycharmProjects\PycharmProjects\Gelisen_Bot`)
  incelendiğinde üç dosyada (`config/constants.py`, `data/olimpos_data.py`,
  `Olimpos_api_MEXC.py`) hardcoded credential-benzeri literal, kullanıcı borsa
  secret'larının DB'de plaintext saklanması ve borsa emir gönderiminde tekrarlanan/
  tutarsız kod deseni tespit edildi (bkz. `docs/imports/GELISEN_BOT_SECURITY_NOTES.md`,
  `GELISEN_BOT_ANALIZ_RAPORU.md` §4). Bu desenlerin EzoEzgi'ye kazara taşınmasını
  önlemek ve finans execution'ı sağlam bir mimari üzerine (credential encryption,
  tek `ExchangeConnector` soyutlaması, secret fail-fast okuma) kurmak için execution
  çalışması bilinçli olarak durduruldu.
- **Alternatif:** Analiz beklemeden T19'a paralel şekilde execution kodu yazmaya
  başlamak (reddedildi — aynı hataların EzoEzgi'de tekrarlanma riski yüksek);
  Gelisen_Bot'u hiç incelememek (reddedildi — mevcut, çalışır durumdaki bir botun
  deneyiminden öğrenmemek kaynak israfı olur).
- **Sonuç:** Kabul edildi. Yalnızca gerçek/mock **işlem yürütme** (T19 ve devamı,
  BACKLOG B021/B022/B023/B024/B025/B030) durduruldu; audit logger (T17), risk
  policy tanımları (T18) ve onay akışı simülasyonu (T20) genel/yeniden kullanılabilir
  altyapı oldukları için devam ediyor. Yeniden açılış kriteri:
  `docs/imports/GELISEN_BOT_PARITY_CHECKLIST.md`'deki "Yeniden Yazılacak" P0
  maddelerinin tamamlanması (bkz. `GELISEN_BOT_MIGRATION_PLAN.md` Faz 2).

## ADR-012
- **Tarih:** 2026-08-13
- **Karar:** Tüm tool-call/CLI çalıştırma katmanları için üç değişmez güvenlik
  kuralı zorunlu kılınır: **(1)** `shell=True` hiçbir zaman kullanılmaz —
  komutlar yalnızca liste argümanlı (`subprocess.run([executable, *args],
  shell=False, ...)`) çalıştırılır; **(2)** yalnızca whitelist'te
  (`config/cli_whitelist.json`) açıkça tanımlı komut adları çalıştırılabilir,
  whitelist dışı bir istek hiçbir process başlatmadan deterministic bir hata
  koduyla reddedilir; **(3)** her çağrıda zorunlu bir timeout uygulanır
  (whitelist girdisi → `default_timeout_seconds` → sabit varsayılan), süresiz
  çalışan bir çağrı asla olmaz.
- **Gerekçe:** `tools/cli-runner` (T12), orchestrator'ın gerçek sistem
  komutları çalıştırabildiği tek nokta; Gelisen_Bot analizinde (bkz.
  `docs/imports/GELISEN_BOT_ANALIZ_RAPORU.md` §4) tutarsız hata yönetimi ve
  merkezi olmayan güvenlik kontrollerinin bakım/güvenlik riski yarattığı
  görülmüştü — bu üç kural, EzoEzgi'nin ilk günden itibaren aynı hataları
  tekrarlamamasını garanti eder. `shell=True` yasağı, komut enjeksiyonunu
  (`;`, `&&`, `|` vb.) yapısal olarak imkansız kılar.
- **Alternatif:** Komut bazlı ad-hoc güvenlik kontrolleri (her tool kendi
  whitelist/timeout mantığını yazar — reddedildi, tutarsızlık riski yüksek);
  tam sandboxing/container izolasyonu (reddedildi, Faz 0 için aşırı karmaşık,
  ihtiyaç doğarsa ayrı bir ADR ile değerlendirilebilir).
- **Sonuç:** Kabul edildi. `tools/cli-runner/src/runner.py` bu üç kuralı
  uyguluyor; test: `tests/test_cli_runner.py::test_never_invokes_shell` bu
  kuralı programatik olarak doğruluyor. Gelecekteki tüm tool-runner'lar
  (browser-runner, file-runner vb.) aynı üç kurala tabi olmalı.

---

*Yeni ADR eklerken yukarıdaki formatı koru ve numarayı sırayla artır (ADR-013, ...).*
