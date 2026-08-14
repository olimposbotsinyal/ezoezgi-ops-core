# Ajan/Asistan Durum Modeli (Agent Presence State Model)

> Durum: v0.1, 2026-08-14 — bkz. [PLAN.md](PLAN.md) T21/T31-T36,
> [DECISIONS.md](DECISIONS.md) ADR-015/ADR-020, kod:
> `apps/ops-suite/backend/src/ops_suite/schemas.py`,
> `apps/ops-suite/frontend/js/scene.js`.

## 1. Üç şema, üç ayrı amaç

| Şema | Neyi temsil eder | Kim üretir |
|---|---|---|
| `AgentPresence` | Bir ajanın O ANKİ anlık görüntüsü | `AgentStatusResolver.resolve_all()` |
| `TaskLifecycleEvent` | Tek bir görevin (request_id) yaşam döngüsündeki TEK bir aşama geçişi (append-only) | `VoiceBridge.handle_voice_command()` |
| `AssistantPresenceEvent` | Asistanın (EzoEzgi'nin kendisinin) O ANKİ "sunum" durumu | `AssistantPresenceTracker` |

## 2. `AgentPresence.state` — geçiş diyagramı

```
        record()                    timeout_seconds asilirsa
offline ────────────► idle/working/blocked/awaiting_approval ──────────► offline
   ▲                                     │
   └─────────────────────────────────────┘
        hic heartbeat atilmadi
```

- `working` / `blocked` / `awaiting_approval`: `HeartbeatTracker.record(declared_state=...)`
  ile açıkça bildirilir.
- `idle`: `record()` çağrıldı ama `declared_state` verilmedi (varsayılan).
- `offline`: iki durumdan biri — (a) hiç `record()` çağrılmadı, (b) son
  `record()`'dan bu yana `timeout_seconds` (varsayılan 30s) geçti.

## 3. Dürüstlük kuralı — "not_implemented" ayrımı

`MASTER_ROADMAP.md §3`'te tanımlı ama **henüz gerçek çalışan kodu
OLMAYAN** ajanlar (Finance/Social/Research/Doc/Device/Voice Agent —
karşılık gelen `services/*` klasörleri hâlâ yalnızca `.gitkeep`
içeriyor) `AgentStatusResolver.resolve_all()` tarafından **HER ZAMAN**
şöyle raporlanır:

```python
AgentPresence(agent_id=..., state="offline", detail="not_implemented -- bkz. MASTER_ROADMAP.md §3, servis klasoru henuz bos")
```

Bu ajanlar **ASLA** `"idle"`/`"working"` olarak gösterilmez — bu, olmayan
bir sürecin sessizce çalışıyormuş gibi görünmesini (fabrike edilmiş bir
"canlılık" izlenimini) önler (gorev kısıtı: "don't fabricate test
results"). Bir ajan gerçek koda kavuştuğunda, `status_resolver.py::KNOWN_LIVE_AGENTS`'a
taşınması GEREKİR — bu, bir kod incelemesiyle görünür bir değişikliktir,
sessiz bir varsayım DEĞİLDİR.

v0'da `KNOWN_LIVE_AGENTS`: `orchestrator`, `bridge_agent`, `tool_runners`.
`NOT_IMPLEMENTED_AGENTS`: `finance_agent`, `social_agent`,
`research_agent`, `doc_agent`, `device_agent`, `voice_agent`.

## 4. `TaskLifecycleEvent.state` — sıralı akış

```
received → translating → risk_checked → ┬─→ awaiting_approval
                                          ├─→ completed
                                          └─→ failed

(awaiting_approval'dan sonra, AYRI bir onay kararı olayı olarak:)
                                          → rejected (approval_queue.decide("rejected"))
```

`routed`/`executing` durumları şemada TANIMLIDIR (gelecekteki daha ince
taneli izleme için) ama `VoiceBridge` v0'da bunları ÜRETMEZ —
`orchestrator.handle_task()` şu an TEK senkron bir çağrı olduğu için ara
adımlar yoktur.

`orchestrator.py` durum kodlarının `TaskLifecycleEvent.state`'e eşlemesi
(`voice_bridge.py::_STATUS_TO_LIFECYCLE_STATE`):

| `orchestrator` durumu | `TaskLifecycleEvent.state` |
|---|---|
| `STATUS_OK` ("ok") | `completed` |
| `STATUS_NO_HANDLER` ("no_handler") | `failed` |
| `STATUS_ERROR` ("error") | `failed` |
| `STATUS_WAITING_APPROVAL` | `awaiting_approval` |

## 5. `AssistantPresenceEvent.state` — geçiş diyagramı

```
idle → listening → thinking → ┬─→ blocked_policy → speaking
                                └────────────────────→ speaking
```

`VoiceBridge.handle_voice_command()` sırasıyla: `listening` (girdi
alındı) → `thinking` (orchestrator çalışıyor) → (yalnızca
`WAITING_APPROVAL` ise) `blocked_policy` → HER ZAMAN son olarak
`speaking` (TR yanıt üretildi — onay bekleyen bir görev için bile,
"onay bekliyor" mesajı SÖYLENİR).

## 6. Heartbeat SLA tanımı

`HeartbeatTracker(timeout_seconds=30.0)` — bir ajan son 30 saniye
içinde `record()` çağırmadıysa `"offline"` sayılır. Bu eşik şu an TEK
bir sabittir (tüm ajanlar için aynı) — ajan-bazlı farklı SLA'lar
GELECEK bir iyileştirmedir (bkz. `docs/BACKLOG.md`).

## 7. Görsel sahnede durustluk kuralinin uygulanmasi (B038, `scene.js`)

`§3`'teki dürüstlük kuralı, animasyonlu ofis sahnesinde (BACKLOG.md
B038) de AYNEN geçerlidir — `scene.js::_zoneForAgent()`:

```js
if (!DESK_POSITIONS[agentId]) { return "ghost"; }  // state'ten BAGIMSIZ, HER ZAMAN
```

`NOT_IMPLEMENTED_AGENTS`'taki bir ajan, backend `state` alanı ne olursa
olsun (şu an her zaman `"offline"`, ama gelecekte state'ten BAĞIMSIZ
olarak) **HER ZAMAN** ayrı, soluk (`globalAlpha=0.35`), küçültülmüş bir
"hayalet raf"ta render edilir — ASLA `KNOWN_LIVE_AGENTS`'ın masa
konumlarında, ASLA tam opaklıkta. Bu, kod incelemesiyle
DOĞRULANABİLİR bir görsel ayrımdır (bkz.
`reports/ops_suite_scene_<UTC>/01_initial_state.png`) — sahne
katmanının §3'ün ihlal edilmediğini KANITLAYAN gerçek bir ekran
görüntüsü.

**Durum → görsel bölge eşlemesi** (`scene.js::_zoneForAgent`):

| `AgentPresence.state` | `KNOWN_LIVE_AGENTS` için bölge | `NOT_IMPLEMENTED_AGENTS` için bölge |
|---|---|---|
| `working` / `blocked` / `awaiting_approval` | masa (`DESK_POSITIONS`) | hayalet raf (HER ZAMAN) |
| `idle` / `offline` | dinlenme bölgesi (yayılmış) | hayalet raf (HER ZAMAN) |
