# BACKLOG — EzoEzgi Ops

> PLAN.md içindeki aktif sprint kapsamı dışında kalan tüm istekler buraya yazılır.
> Sprint ortasında PLAN.md kapsamı değişmez; yeni fikir/istek önce buraya düşer.

| ID | Başlık | Öncelik | Durum | Faz | Sorumlu Ajan | Not |
|---|---|---|---|---|---|---|
| B001 | Kök `README.md` + proje özeti | Yüksek | Açık | Faz 0 | — | PLAN.md T3 ile aynı, referans amaçlı |
| B002 | Config loader — hot reload | Yüksek | Açık | Faz 0 | Orchestrator | PLAN.md T5 |
| B003 | Ollama local runtime kurulum betiği | Yüksek | Açık | Faz 1 | Bridge Agent | Model indirme + sağlık kontrolü |
| B004 | Whisper STT entegrasyonu | Orta | Açık | Faz 3 | Voice Agent | services/stt-whisper iskeleti boş |
| B005 | TTS servis seçimi (local model kararı) | Orta | Açık | Faz 3 | Voice Agent | DECISIONS.md'de ADR gerekiyor |
| B006 | Finance Engine veri modeli tasarımı | Orta | Açık | Faz 2 | Finance Agent | Harcama/gelir şeması |
| B007 | Social Engine onay akışı (auto-post guard) | Yüksek | Açık | Faz 5 | Social Agent | §8 güvenlik mekanizmasına bağımlı |
| B008 | Research Engine — web erişim politikası | Orta | Açık | Faz 2 | Research Agent | Offline/online fallback davranışı |
| B009 | Doc Ingestion — desteklenecek dosya formatları | Düşük | Açık | Faz 2 | Doc Agent | PDF/DOCX/TXT önceliklendirme |
| B010 | GSM Gateway — sağlayıcı/donanım seçimi | Orta | Açık | Faz 3 | Device Agent | SIM/modem tipi netleşmeli |
| B011 | Bluetooth cihaz senaryoları | Düşük | Açık | Faz 3+ | Device Agent | Kapsam MASTER_ROADMAP §4'te ertelendi |
| B012 | Gesture-vision — privacy-by-default doğrulama | Yüksek | Açık | Faz 5 | Device Agent | Görüntü diske yazılmama garantisi |
| B013 | Admin Panel — ilk wireframe | Düşük | Açık | Faz 5 | — | apps/admin-panel şu an boş |
| B014 | Mobile PWA — offline cache stratejisi | Orta | Açık | Faz 3 | — | Service worker tasarımı |
| B015 | Prometheus/Grafana dashboard seti | Düşük | Açık | Faz 6 | — | infra/monitoring iskeleti boş |
| B016 | OSV taramasının CI'ya bağlanması | Orta | Açık | Faz 4 | — | CI henüz kurulu değil |
| B017 | Audit log şeması (append-only) | Yüksek | Açık | Faz 4 | Orchestrator | data/audit formatı netleşmeli |
| B018 | Risk seviyesi taksonomisi detaylandırma | Yüksek | Açık | Faz 4 | Orchestrator | low/medium/high/irreversible kriterleri |
| B019 | Çok dilli genişleme (TR/EN dışı) | Düşük | Açık | Backlog | Bridge Agent | Şimdilik kapsam dışı, ADR gerektirir |
| B020 | Native mobil uygulama ihtiyacı değerlendirmesi | Düşük | Açık | Backlog | — | PWA yeterli mi — ayrı ADR |
