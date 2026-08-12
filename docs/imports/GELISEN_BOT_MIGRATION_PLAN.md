# Gelisen_Bot → EzoEzgi — Migration Plan

> Bu plan, `GELISEN_BOT_PARITY_CHECKLIST.md`'deki maddeleri EzoEzgi'nin faz yapısına
> (MASTER_ROADMAP.md §7) yerleştirir. **Hiçbir madde bu doküman yazıldığı anda aktif
> değildir** — finans execution DECISIONS.md ADR-011 ile durdurulmuş durumda.

## Faz 0 (mevcut, devam ediyor) — Ön koşullar

Zaten PLAN.md T17-T20 kapsamında planlı, migration'ın önkoşulu:

- [ ] T17 — Audit logger iskeleti (JSONL, append-only)
- [ ] T18 — Risk policy dosyaları (L0-L3 tanımı)
- [ ] T19 — Finance execution mock (yalnızca simülasyon)
- [ ] T20 — Onay akışı CLI simülasyonu

Bunlara ek olarak bu revizyonla eklenenler:

- [ ] Gelisen_Bot parity checklist tamamlandı (bu doküman + `GELISEN_BOT_PARITY_CHECKLIST.md`)
- [ ] `config/constants.py`, `data/olimpos_data.py`, `Olimpos_api_MEXC.py`'deki hardcoded
      credential'ların kullanıcı tarafından rotate edilip edilmediği teyit edildi
      (bkz. `GELISEN_BOT_SECURITY_NOTES.md` — bu, EzoEzgi'nin sorumluluğunda değil,
      kaynak projenin sahibinin aksiyonu, ama Faz 2'ye geçiş öncesi teyit istenir).

## Faz 1 (Gün 15-35, Çekirdek Döngü) — Etkisi yok

Faz 1 kapsamı (TR komut → EN task → tool call → TR yanıt, CLI runner) finans
execution'dan bağımsız. Gelisen_Bot analizi bu fazı **etkilemiyor**; Finance Agent
Faz 1'de devreye girmiyor.

## Faz 2 (Gün 36-60, Ajan Genişleme) — Gelisen_Bot Parity Çalışması Başlar

Bu, Gelisen_Bot'tan gerçek anlamda yeniden-yazım (rewrite) yapılacağı faz. Alt adımlar
(gün numaraları Faz 2'nin göreli ilk yarısı için kaba tahmin, Faz 2 planlaması ile
netleşecek):

| Adım | Kapsam | Efor | Çıktı |
|---|---|---|---|
| M1 | `ExchangeConnector` protokolü tasarımı + tek borsa (ör. Binance) adaptörü | L | BACKLOG B021'in ilk dilimi |
| M2 | API permission validator (withdraw=false kontrolü) | S | BACKLOG B025 |
| M3 | Kullanıcı credential encryption (envelope encryption / KMS) | M | Yeni backlog maddesi — bu planla eklendi (bkz. BACKLOG) |
| M4 | Order sizing + stop-loss/take-profit policy | M | BACKLOG B022, B023 |
| M5 | Circuit breaker (max günlük zarar) — Gelisen_Bot'un `risk_kill_switch.py` deseninden ilham | M | BACKLOG B024 |
| M6 | Trailing-stop yeniden yazımı (L2/L3 onay akışına bağlı) | M | Parity checklist "Yeniden Yazılacak" |
| M7 | Sinyal/strateji motorunun EzoEzgi Finance Agent'a uyarlanması (ilk strateji, Gelisen_Bot'un `strategy_v1.py` deseninden ilham) | L | Yeni backlog maddesi |
| M8 | İkinci borsa adaptörü (parity: en az 2 borsa) | M | BACKLOG B021'in devamı |

**Faz 2 çıkış kriteri (finans için):** M1-M5 tamamlanmış, gerçek borsa bağlantısı
**hâlâ L1 (simülasyon) ile sınırlı** — L2/L3 gerçek para henüz açılmıyor.

## Faz 3-4 (Gün 61-95) — Etkisi dolaylı

STT/TTS/GSM/mobil (Faz 3) ve güvenlik sertleştirme (Faz 4) fazları finans execution'ı
doğrudan içermiyor, ama Faz 4'teki audit log/risk politika sertleştirmesi Gelisen_Bot
parity çalışmasının kalite kapısı olarak kullanılacak (ör. OSV taraması, B016).

## Faz 5+ — Gerçek para (L2/L3) açılışı

Gerçek para ile L2/L3 işlemlerin açılması için **ek bir ADR + kullanıcı onayı** gerekir
(bu migration planı tek başına yeterli değildir). Ön koşul: Faz 2 M1-M8 tamamlanmış,
en az 30 gün L1 (simülasyon) verisiyle risk motoru doğrulanmış olmalı.

## Kapsam Dışı (bu migration planının parçası değil)

- Sentiment/sosyal veri modülleri (Research/Social Agent'a devredilebilir, ayrı karar).
- ML tahmin modelleri (`RealAIModel.py`, `.pkl` dosyaları) — EzoEzgi kendi model
  stratejisini ayrı belirleyecek, Gelisen_Bot modelleri taşınmayacak.
- E-posta doğrulama akışı (`email_checker.py`).
- Vendored SDK kaynak kopyaları (`bitget/`, `okx/`) — pip paketi olarak eklenir.

---

*İlgili dokümanlar: `GELISEN_BOT_ANALIZ_RAPORU.md` (mimari analiz), `GELISEN_BOT_PARITY_CHECKLIST.md`
(özellik bazlı karar tablosu), `GELISEN_BOT_SECURITY_NOTES.md` (güvenlik bulguları),
`GELISEN_BOT_FILE_MAP.md` (dosya bazlı kopyalama kaydı).*
