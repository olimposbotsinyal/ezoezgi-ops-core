# Sesli-Öncelikli Etkileşim Politikası (Voice-First Interaction Policy)

> Durum: v0 (mocked metin girdisi), 2026-08-14 — bkz. [PLAN.md](PLAN.md)
> T25, [DECISIONS.md](DECISIONS.md) ADR-015.

## 1. TR-girdi / EN-iç-akıl-yürütme / TR-çıktı ilkesi

`config/assistant.identity.json`'daki `language_mode` sözleşmesi
(`user_input: tr`, `internal_reasoning_task_language: en`,
`user_output: tr`) Ops Suite'te DEĞİŞMEDEN korunur —
`bridge.translate_and_extract()` TR metni EN bir `task_en`'e çevirir,
`bridge.generate_tr_response()` sonucu TEKRAR TR'ye çevirir. Ops Suite
bu sözleşmeyi TÜKETİR, DEĞİŞTİRMEZ.

## 2. Wake-alias + komut akışı

```
"Ezo, echo ile 'merhaba' yaz"
        │
        ▼
alias_matcher.detect_alias()  -- Türkçe-karakter-farkında, tam-kelime eşleşme
        │  detected_alias="ezo"
        ▼
bridge._classify()  -- NLU_PROVIDER (mock/ollama)
        │  task_en="RUN_ECHO", confidence=0.6
        ▼
orchestrator.handle_task()  -- risk kontrolü + handler çağrısı
        │
        ▼
bridge.generate_tr_response()  -- TR yanıt şablonu
```

Ops Suite'in `VoiceBridge.handle_voice_command()`'ı bu ZİNCİRİ
SARAR — her aşama geçişinde bir `TaskLifecycleEvent` üretir (bkz.
`docs/AGENT_PRESENCE_STATE_MODEL.md` §4) ve `AssistantPresenceTracker`'ı
günceller (`listening` → `thinking` → belki `blocked_policy` →
`speaking`).

## 3. "Ses girdisi" NEDEN mocked metin?

**Bu ortamda gerçek mikrofon/hoparlör donanımı YOKTUR.** Ama bu, Ops
Suite'in voice-first sözleşmesini test edilemez KILMAZ — çünkü
`bridge.translate_and_extract(input_tr: str, ...)` ZATEN ham ses değil,
**METİN** alır (STT — Speech-to-Text — bridge'in DIŞINDA, ayrı bir
katman olarak tasarlanmıştır, bkz. `docs/BACKLOG.md` B004).

Bu nedenle:

- `POST /api/voice/command` `{"input_tr": "..."}` alır — bir kullanıcının
  SÖYLEDİĞİ şeyin STT tarafından ZATEN metne çevrilmiş hâlini temsil
  eder.
- Gerçek STT (B004) eklendiğinde, bu uç noktanın **SÖZLEŞMESİ
  DEĞİŞMEZ** — yalnızca girdi KAYNAĞI değişir: bugün klavye/API
  çağrısı, gelecekte gerçek mikrofon + STT motoru. Ops Suite'in TÜM iç
  mantığı (bridge/orchestrator/audit/onay kuyruğu/WS yayını) AYNEN
  kalır.
- Benzer şekilde, `AssistantPresenceEvent.utterance_tr` alanı bugün
  yalnızca EKRANDA gösterilir — gerçek TTS (B005) eklendiğinde, AYNI
  alan seslendirme motoruna beslenecektir; şema DEĞİŞMEZ.

## 4. Politika-engeli (policy-block) uyarılarının gösterimi

Bir komut `risk_engine.py` tarafından `high`/`irreversible` olarak
sınıflandırılırsa: `AssistantPresenceEvent.state="blocked_policy"`
yayınlanır VE nihai TR yanıt (`bridge.generate_tr_response`) her zaman
"onay bekliyor" ifadesini AÇIKÇA içerir (bkz.
`tests/test_e2e_acceptance.py::test_irreversible_command_waits_for_approval_and_is_audited`) —
kullanıcıya YANLIŞ bir "tamamlandı" izlenimi ASLA verilmez.

## 5. Kapsam dışı (bu belge kapsamında DEĞİL)

Gerçek STT/TTS motor seçimi (Whisper vb.), ses kalitesi, gürültü
bastırma, çoklu-konuşmacı ayrımı — bunlar `docs/BACKLOG.md` B004/B005
kapsamındadır, bu politika belgesi yalnızca Ops Suite'in voice-first
SÖZLEŞMESİNİ (girdi/çıktı şekli, durum modeli) tanımlar.
