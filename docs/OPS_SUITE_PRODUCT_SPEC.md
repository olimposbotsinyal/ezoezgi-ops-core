# Ops Suite — Ürün Spesifikasyonu (v0)

> Durum: **v0 (statik shell + gerçek backend), 2026-08-14** — bkz.
> [PLAN.md](PLAN.md) T21–T27, [DECISIONS.md](DECISIONS.md) ADR-015..018,
> [MASTER_ROADMAP.md](MASTER_ROADMAP.md) §11.

## 1. Amaç

Ops Suite, EzoEzgi'nin ajan/asistan durumunu, görev yaşam döngüsünü ve
onay kuyruğunu **gerçek zamanlı** gösteren bir web kontrol merkezidir —
"görünmez arka plan sürecleri" yerine, sahibinin (Serkan Eryılmaz) HER
ZAMAN ne olup bittiğini görebildiği bir "ofis" metaforu sunar (bkz.
kullanıcı isteği: "assistant and agents are visible in real-time").

## 2. Kapsam — v0 vs. sonraki sürümler

| Alan | v0 (bu sprint) | Sonrası (backlog) |
|---|---|---|
| Ajan durumu | Gerçek heartbeat + audit-tail tabanlı `AgentPresence` (3 canlı ajan + 6 "not_implemented" ajan, dürüstçe işaretli) | Yeni ajanlar gerçek koda kavuştukça `KNOWN_LIVE_AGENTS`'a taşınır |
| Görev akışı | `TaskLifecycleEvent` dizisi, WebSocket ile canlı yayın | Daha ince taneli ("routed"/"executing" alt-adımları) |
| Onay kuyruğu | Gerçek, kalıcı (JSONL) kuyruk + REST approve/reject | Kimlik doğrulamalı `actor` (bkz. B0xx, IDENTITY_AND_DELEGATION_POLICY.md v0 sınırlaması) |
| Frontend | Statik HTML/CSS/vanilla JS "Command Center" (ajan kartları, canlı akış, onay paneli, asistan paneli) | Tam animasyonlu 2D ofis sahnesi (B038), gerçek-tarayıcı E2E doğrulama (B039) |
| Ses/GSM/kamera | Sesli komut YALNIZCA mocked TR metin girdisiyle (gerçek mikrofon/GSM/kamera donanımı bu ortamda YOK) | Gerçek STT/TTS/GSM/kamera entegrasyonu (B040, B004/B005) |

## 3. Panel envanteri — veri kaynağı eşlemesi

| Panel | Veri kaynağı | Uç nokta/konu |
|---|---|---|
| Asistan paneli | `AssistantPresenceTracker` | `GET /api/assistant`, WS konu `assistant.presence` |
| Ajan kartları | `AgentStatusResolver` (heartbeat + audit-tail) | `GET /api/agents`, WS konu `task.lifecycle` tetiklediğinde yeniden çekilir |
| Onay kuyruğu paneli | `ApprovalQueueStore` | `GET /api/approvals?status=pending`, `POST /api/approvals/{id}/approve|reject`, WS konu `approval.queue` |
| Canlı akış | Tüm WS konuları (`agent.presence`/`task.lifecycle`/`assistant.presence`/`approval.queue`) | `WS /ws/live` |

## 4. Mimari (v0)

```
apps/ops-suite/
├── backend/src/ops_suite/   -- FastAPI (ADR-007'nin zaten sanksiyonladigi
│                                cerceve), TEK surecli, TEK uvicorn islemi
│   ├── schemas.py           -- AgentPresence/TaskLifecycleEvent/AssistantPresenceEvent/ApprovalQueueEntry
│   ├── events.py            -- konu haritasi + WS zarfi
│   ├── heartbeat.py         -- bellek-ici HeartbeatTracker
│   ├── status_resolver.py   -- AgentStatusResolver (durustluk kurali burada)
│   ├── approval_queue.py    -- JSONL append-only kalici kuyruk
│   ├── audit_tail.py        -- data/audit/audit.log.jsonl salt-okunur tail
│   ├── assistant_presence.py
│   ├── voice_bridge.py      -- bridge.py + orchestrator.py'yi SARAR, DEGISTIRMEZ
│   ├── ws_manager.py        -- surec-ici asyncio yayinci (harici event bus YOK)
│   ├── app.py                -- create_app() fabrikasi + tum REST/WS rotalari
│   └── server.py             -- `python -m ops_suite.server` gercek girdi noktasi
└── frontend/                 -- statik HTML/CSS/vanilla JS (npm/bundler YOK)
```

`ops_suite`, `apps/orchestrator/src`in mevcut modüllerini (`orchestrator.py`,
`audit_logger.py`, `risk_engine.py`, `approval_stub.py`, `config_loader.py`)
**hiçbirini değiştirmeden** sarar — bkz. `scripts/e2e_demo.py`'nin aynı
"sar, dokunma" deseni.

## 5. Güvenlik ve yetki

- **Sahibi (owner) invaryantı:** Serkan Eryılmaz, sistemin tek kök
  yetkilisidir — bu, kod tarafından DEĞİL, şu an yalnızca prosedürel
  olarak korunur (bkz. `IDENTITY_AND_DELEGATION_POLICY.md`).
- Onay kuyruğu, mevcut `risk_engine.py`/`approval_stub.py` risk
  seviyelerini (yalnızca `high`/`irreversible`) kullanır — risk
  hesaplama mantığı Ops Suite tarafından DEĞİŞTİRİLMEZ/atlanamaz.
- **v0 bilinen sınırlama:** onay API'sindeki `actor` alanı serbest
  metindir, kimlik doğrulaması YOKTUR — bkz. §6 ve
  `IDENTITY_AND_DELEGATION_POLICY.md`.

## 6. Bilinen sınırlamalar (v0, dürüstçe)

- Tam animasyonlu 2D ofis sahnesi/avatar YOKTUR (statik kart grid'i) — B038.
- Gerçek tarayıcıda görsel/etkileşimli doğrulama YAPILMADI (bu ortamda
  tarayıcı-otomasyon aracı yok) — B039, yalnızca `node --check` ile
  sözdizimi doğrulandı.
- Gerçek ses (mikrofon/hoparlör/STT/TTS), GSM/SIM, kamera/gesture
  donanımı bu ortamda YOKTUR — B040. Sesli komutlar yalnızca MOCKED TR
  metin girdisiyle test edildi (bkz. `scripts/ops_suite_demo.py`
  `NOT_COLLECTED` bölümü).
- Heartbeat/presence durumu yalnızca BELLEK-İÇİ tutulur, sunucu yeniden
  başlatıldığında sıfırlanır — B041.
- Onay `actor` alanı kimlik doğrulamasızdır — B0xx (bkz.
  `IDENTITY_AND_DELEGATION_POLICY.md`).
