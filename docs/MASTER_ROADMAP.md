# MASTER ROADMAP — EzoEzgi Ops

> Durum: Taslak v0.1 — Bootstrap aşaması
> Son güncelleme: 2026-08-14 (§11 Ops Suite eklendi)

## 1. Vizyon ve Kapsam

EzoEzgi, kullanıcıyla **Türkçe konuşan** ama iç işleyişte (planlama, tool-call, ajan-arası
mesajlaşma, loglama) **İngilizce çalışan** local-first, çok ajanlı bir operasyon asistanıdır.

Kapsam:
- Kullanıcının günlük operasyonel yükünü (finans takibi, sosyal medya, araştırma, doküman
  işleme, cihaz/GSM etkileşimi) tek bir konuşma arayüzünden yönetebilmesi.
- Bulut bağımlılığı olmadan (local runtime — Ollama) çalışabilmesi; bulut modelleri opsiyonel
  bir yükseltme katmanı olarak kalması.
- Riskli/geri döndürülemez aksiyonlarda insan onayı zorunlu kılan bir güvenlik katmanı.

Kapsam dışı (şimdilik):
- Çoklu kullanıcı / çoklu tenant SaaS modeli.
- Üçüncü taraflara açık genel API servisi.
- Mobil native uygulama (PWA ile başlanacak, native ihtiyaç ayrı ADR ile değerlendirilecek).

## 2. TR→EN→TR Köprü Mimarisi

```
Kullanıcı (TR)
   │
   ▼
[tr-en-bridge] ── girdi normalize + TR→EN çeviri/intent extraction
   │
   ▼
[orchestrator] ── EN task graph, ajan seçimi, tool-call planlama
   │
   ▼
[ajanlar / services] ── EN içi işlemler, tool çağrıları, sonuç üretimi
   │
   ▼
[tr-en-bridge] ── EN→TR çeviri + ton/uslüp normalize
   │
   ▼
Kullanıcı (TR)
```

Prensipler:
- Köprü katmanı **stateless** olacak; konuşma hafızası orchestrator/memory katmanında tutulur.
- Wake alias eşlemesi (`ezo`, `ezgi`) girdi normalize adımında, çeviriden önce yapılır.
- Çeviri hatası/belirsizlik durumunda köprü, orijinal TR cümleyi de EN task'a context olarak
  ekler (kayıp anlam riskini azaltmak için).

## 3. Çok Ajanlı Rol Dağılımı

| Ajan | Sorumluluk | Servis |
|---|---|---|
| Orchestrator | Task graph, ajan seçimi, sonuç birleştirme | apps/orchestrator |
| Bridge Agent | TR↔EN çeviri, wake/alias yönetimi | services/tr-en-bridge |
| Finance Agent | Bütçe, harcama, rapor üretimi | services/finance-engine |
| Social Agent | Sosyal medya planlama/paylaşım taslağı | services/social-engine |
| Research Agent | Web/doküman araştırma, özetleme | services/research-engine |
| Doc Agent | Doküman ingestion, indeksleme | services/doc-ingestion |
| Device Agent | GSM/Bluetooth/kamera köprüsü | services/gsm-gateway, services/gesture-vision |
| Voice Agent | STT/TTS | services/stt-whisper, services/tts-service |
| Tool Runners | CLI/tarayıcı/dosya işlemleri (sandboxed) | tools/* |
| Ops Suite (gözlemleyici katman) | Ajan/asistan durumu, görev akışı görselleştirme, onay kuyruğu UI — bir ajan DEĞİL, salt-gözlemleyici bir katman | apps/ops-suite |

Orchestrator, Hermes/Crew-tarzı bir yaklaşımla ajanları görev bazlı çağırır (bkz. DECISIONS.md).

## 4. Mobil + GSM + Bluetooth + Kamera Etkileşimi

- **Mobil (PWA):** İlk faz arayüzü; offline cache + service worker ile local-first.
- **GSM Gateway:** SMS/arama tetikleyicili komutlar (ör. "Ezo, bakiyeyi SMS'le") — düşük
  bant genişliğinde fallback kanal.
- **Bluetooth:** Yakın-çevre cihaz sinyalleşmesi (ör. giyilebilir bildirim), ilk fazda
  kapsam dışı, faz 3+ için ayrılmış.
- **Kamera (gesture-vision):** Jest/QR/doküman tarama girişi; local model ile işlenir,
  görüntü varsayılan olarak diske yazılmaz (privacy-by-default).

## 5. Finans ve Sosyal Medya Motoru

- **Finance Engine:** Harcama/gelir kayıtları, bütçe uyarıları, rapor üretimi **ve**
  kullanıcının izin verdiği borsa API'lerinde gerçek işlem açma/kapatma. Gerçek işlem
  **kapsam dışı değildir** — ancak sıkı bir risk/onay katmanına bağlıdır:
  - **Withdraw yetkisi kapalı API key zorunlu.** Finance Engine hiçbir zaman para/kripto
    çekme (withdraw) yetkisi olan bir API key ile çalışmaz; bağlantı kurulmadan önce
    anahtarın withdraw izni doğrulanır (bkz. BACKLOG "API permission validator").
  - **Risk motoru + onay mekanizması zorunludur.** Her işlem, yürütülmeden önce risk
    motorundan geçer ve işlem seviyesine göre onay ister (aşağıya bkz.).
  - **İşlem seviyeleri (execution levels):**
    | Seviye | Tanım | Onay |
    |---|---|---|
    | **L0** | Sadece bilgi (fiyat/portföy/rapor sorgusu) | Onay gerekmez |
    | **L1** | Simülasyon (mock emir, borsaya gönderilmez) | Onay gerekmez, sonuç loglanır |
    | **L2** | Küçük işlem (kullanıcı tanımlı eşik altı) | Tek onay |
    | **L3** | Büyük işlem (eşik üstü) | Çift onay + zorunlu bekleme süresi (cooling-off) |
  - **Tüm işlemler** (L0 hariç, L1–L3) `data/audit` altında append-only audit log'a
    (bkz. ADR-009) yazılır: talep, risk skoru, onay/red kararı, sonuç.
  - Detaylı güvenlik prosedürleri için bkz. `docs/RUNBOOK.md` → "Finans Güvenlik
    Operasyonları".
- **Social Engine:** İçerik taslağı üretimi, zamanlama önerisi. Otomatik yayınlama
  (auto-post) **onay mekanizması olmadan devrede olmayacak** (bkz. §8).

## 6. Offline-First Çalışma

- Local runtime: Ollama üzerinden model servisi (bkz. DECISIONS.md).
- `data/memory` ve `data/knowledge` local disk üzerinde tutulur, bulut senkronu opsiyonel.
- Ağ yokken: Bridge + Orchestrator + Tool Runners local modelle çalışmaya devam eder;
  yalnızca bulut-bağımlı ajanlar (ör. güncel web araştırması) graceful degrade olur.

## 7. 120 Günlük Faz Planı

| Faz | Gün | Odak |
|---|---|---|
| Faz 0 — Bootstrap | 1–14 | İskelet, kimlik/config, TR-EN köprü PoC, orchestrator skeleton |
| Faz 1 — Çekirdek Döngü | 15–35 | E2E: TR komut → EN task → tool call → TR yanıt, CLI runner |
| Faz 2 — Ajan Genişleme | 36–60 | Finance + Research + Doc Ingestion ajanları, temel memory |
| Faz 3 — Ses ve Cihaz | 61–80 | STT/TTS entegrasyonu, GSM gateway, mobil PWA ilk sürüm |
| Faz 4 — Güvenlik Sertleştirme | 81–95 | Onay mekanizması, audit log, risk politikaları, OSV taraması |
| Faz 5 — Sosyal + Gesture | 96–110 | Social engine, gesture-vision, admin panel¹ |
| Faz 6 — Stabilizasyon | 111–120 | Monitoring (Prometheus/Grafana), yük testi, DoD doğrulama |

## 8. Güvenlik ve Onay Mekanizması

- Her aksiyon **risk seviyesi** ile etiketlenir: `low / medium / high / irreversible`.
- `high` ve `irreversible` seviyedeki aksiyonlar (ödeme, otomatik sosyal paylaşım, dosya
  silme, dış API'ye veri gönderimi) **kullanıcı onayı olmadan yürütülmez**.
- Tüm onay/red kararları `data/audit` altında değiştirilemez (append-only) log olarak tutulur.
- Bağımlılık güvenlik taraması: OSV (bkz. DECISIONS.md).
- Politika kaynağı: `policies/security`, `policies/risk`, `policies/compliance`.

## 9. KPI'lar

- TR→EN→TR köprü doğruluğu (intent-preserving çeviri) ≥ %90 (manuel örneklem ile).
- Uçtan uca basit komut yanıt süresi (local) ≤ 5 sn (p95).
- Onay gerektiren aksiyonlarda yanlış-otomasyon (onaysız yürütme) = 0 vaka.
- Faz 1 sonunda çalışan E2E demo: 1 (evet/hayır).
- Haftalık PLAN.md görev tamamlama oranı ≥ %80.

## 10. Definition of Done

Bir görev/faz "tamamlandı" sayılır ancak:
- Kabul kriterleri PLAN.md/BACKLOG.md'de tanımlanmış ve karşılanmışsa,
- İlgili ADR (varsa) DECISIONS.md'e yazılmışsa,
- Güvenlik/onay etkisi olan değişiklikler `policies/` altında belgelenmişse,
- Kod/servis iskeletleri için en az bir manuel veya otomatik doğrulama yapılmışsa,
- Daily Log'a sonuç ve varsa açık sorunlar not düşülmüşse.

## 11. Ops Suite — Gerçek Zamanlı Kontrol Merkezi (Faz 5'ten öne çekilmiş, bilinçli istisna)

**¹** Bu bölüm, §7'deki Faz 5 satırındaki "admin panel" hedefinin bir
kısmını (ve Faz 6'daki `B015` izleme paneli hedefinin bir kısmını)
BİLEREK öne çeken bir istisnadır (bkz. `docs/DECISIONS.md` ADR-015) —
Faz tablosunun genel sırası DEĞİŞTİRİLMEDİ, yalnızca bu ÖZEL özellik
erken teslim edildi.

**Vizyon:** Sahibinin (Serkan Eryılmaz), EzoEzgi'nin hangi ajanların ne
durumda olduğunu, hangi görevin hangi ajana yönlendirildiğini ve hangi
kararların onay beklediğini **gerçek zamanlı** görebildiği bir web
kontrol merkezi ("Command Center").

**v0 kapsamı (uygulandı):** gerçek domain modeli + olay sözleşmeleri,
gerçek heartbeat/durum çözümleme, gerçek kalıcı onay kuyruğu, gerçek
tek-süreçli FastAPI+WebSocket sunucusu, statik HTML/CSS/vanilla-JS
frontend kabuğu, mocked-metin sesli komut kablolaması. **v0 kapsamı
dışı (gerçek donanım/tarayıcı gerektirir, bkz. `docs/BACKLOG.md`
B038-B040/B043):** tam animasyonlu 2D ofis sahnesi, gerçek-tarayıcı
görsel doğrulama, gerçek ses/GSM/kamera entegrasyonu.

Ayrıntı: `docs/OPS_SUITE_PRODUCT_SPEC.md`, `docs/AGENT_PRESENCE_STATE_MODEL.md`,
`docs/IDENTITY_AND_DELEGATION_POLICY.md`, `docs/VOICE_FIRST_INTERACTION_POLICY.md`,
`docs/GSM_CALL_FLOW.md`, `docs/TR_EN_BRIDGE_EXTERNAL_AI_POLICY.md`,
`docs/PLAN.md` (T21-T27).
