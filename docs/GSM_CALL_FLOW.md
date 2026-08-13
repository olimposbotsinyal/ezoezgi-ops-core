# GSM Çağrı Akışı (GSM Call Flow) — TASARIM-ONLY

> Durum: **TASARIM-ONLY — hiçbir gerçek kod YOKTUR**, 2026-08-14. Bkz.
> [MASTER_ROADMAP.md](MASTER_ROADMAP.md) §4, [BACKLOG.md](BACKLOG.md)
> B010/B040/B043.

## 0. Dürüstlük notu (en başa, kasıtlı)

**Bu belge, `services/gsm-gateway`'de HENÜZ TEK SATIR kod olmadan
yazılmıştır.** `services/gsm-gateway/` klasörü şu an yalnızca
`.gitkeep` içerir (bkz. `docs/MASTER_ROADMAP.md` §4, `docs/BACKLOG.md`
B010 "GSM Gateway — sağlayıcı/donanım seçimi"). Bu belge, Ops Suite'in
gelecekteki GSM entegrasyonunu NASIL bir olay akışına
BAĞLAYACAĞINI (bkz. §3) TASARLAR — ama hiçbir donanım/API çağrısı bu
ortamda test EDİLEMEDİ (bkz. `scripts/ops_suite_demo.py`'nin
`NOT_COLLECTED` listesi: `real_gsm_sim_call_flow`).

## 1. Kapsam (MASTER_ROADMAP.md §4'ten)

- **Giden bildirimler:** Sistem, önemli olayları (örneğin bir
  `irreversible` onay bekliyor, bir kritik hata oluştu) SMS/arama ile
  sahibine bildirebilir.
- **Gelen sesli komut işleme:** Bir telefon araması üzerinden alınan
  sesli komut, mevcut voice-first zincirine (bkz.
  `VOICE_FIRST_INTERACTION_POLICY.md`) beslenebilir.

## 2. Tasarlanan mimari (henüz uygulanmadı)

```
[Telefon/SIM donanimi]
        │  (GSM modem, sağlayıcı henüz SECILMEDI -- bkz. B010)
        ▼
services/gsm-gateway/  (BOS -- hicbir kod yok)
        │  gelen arama -> STT -> TR metin
        │  giden bildirim <- TR metin -> TTS -> arama/SMS
        ▼
[TASARLANAN] ops_suite.voice_bridge.VoiceBridge.handle_voice_command(input_tr)
        │  AYNI sozlesme -- girdi KAYNAGI degisir (klavye/API -> GSM), IC MANTIK degismez
        ▼
(AYNI TaskLifecycleEvent/AssistantPresenceEvent akisi, bkz. AGENT_PRESENCE_STATE_MODEL.md)
```

**Kritik tasarım kararı:** `services/gsm-gateway` gerçekleştiğinde, Ops
Suite'in `VoiceBridge.handle_voice_command(input_tr: str)` sözleşmesi
**DEĞİŞMEMELİDİR** — GSM gateway kendi STT'sini çalıştırıp yalnızca TR
metni bu fonksiyona besler (tıpkı bugünkü mocked-metin girdisi gibi).
Bu, `VOICE_FIRST_INTERACTION_POLICY.md` §3'teki "girdi kaynağı değişir,
sözleşme değişmez" ilkesinin GSM'e uygulanmasıdır.

## 3. Giden bildirim akışı (tasarım)

```
Ops Suite backend olayı (ornegin approval_queue.submit() cagrildi)
        │
        ▼
[TASARLANAN] bildirim politikasi (hangi olaylar SMS/arama tetikler?
        │   -- HENUZ TANIMLANMADI, gorev kisiti: risk_level=irreversible
        │   olaylari icin makul bir aday, ama KARARLASTIRILMADI)
        ▼
services/gsm-gateway/  (BOS)
        │
        ▼
[Telefon/SIM donanimi]
```

## 4. Açık sorular (bilinçli olarak yanıtlanmadı)

- GSM sağlayıcısı/donanımı (bkz. B010) — henüz seçilmedi.
- Bildirim politikası (hangi olaylar bildirim tetikler, ne sıklıkla,
  hız sınırlama var mı) — henüz tasarlanmadı.
- Gelen aramanın kimlik doğrulaması (yalnızca sahibinin numarasından mı
  kabul edilir?) — `IDENTITY_AND_DELEGATION_POLICY.md`'nin GSM'e nasıl
  uygulanacağı henüz belirlenmedi.

## 5. Doğrulama planı (GSM donanımı geldiğinde)

Gerçek bir GSM modem/SIM edinildiğinde: (1) gelen bir aramanın gerçekten
`VoiceBridge`'e ulaştığını, (2) giden bir bildirimin gerçekten
gönderildiğini, (3) yanlış numaradan gelen bir aramanın REDDEDİLDİĞİNİ
kanıtlayan gerçek, sahte-olmayan bir test seti gerekir — bkz.
`docs/BACKLOG.md` B043.
