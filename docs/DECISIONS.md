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

---

*Yeni ADR eklerken yukarıdaki formatı koru ve numarayı sırayla artır (ADR-006, ...).*
