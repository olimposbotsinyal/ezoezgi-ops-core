# Ops Suite — Ürün Spesifikasyonu (v0.2)

> Durum: **v0.2 (kart-tabanlı shell + Canvas2D ofis sahnesi + gerçek
> backend + kimlik doğrulama), 2026-08-14** — bkz.
> [PLAN.md](PLAN.md) T21–T36, [DECISIONS.md](DECISIONS.md) ADR-015..021,
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
| Onay kuyruğu | Gerçek, kalıcı (JSONL) kuyruk + REST approve/reject, **bearer-token kimlik doğrulama + kapsam tabanlı yetkilendirme + owner-root-guard (B044, kapalı)** | Token rotasyon/iptal UI'ı, çoklu kimlik doğrulama yöntemi (bkz. IDENTITY_AND_DELEGATION_POLICY.md §5) |
| Frontend | Kart-tabanlı "Command Center" (ajan kartları, canlı akış, onay paneli, asistan paneli) **+ saf Canvas2D animasyonlu ofis sahnesi (B038, kısmen — bkz. §7)**, gerçek-tarayıcı E2E doğrulama (B039, kısmen tamamlandı) | Tam görsel/etkileşimli regresyon paketi, hareket ara-karelerinin piksel-seviyesi doğrulaması |
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
│   ├── identity.py          -- B044: IdentityStore + owner-root-guard (bkz. ADR-019)
│   ├── voice_bridge.py      -- bridge.py + orchestrator.py'yi SARAR, DEGISTIRMEZ
│   ├── ws_manager.py        -- surec-ici asyncio yayinci (harici event bus YOK)
│   ├── app.py                -- create_app() fabrikasi + tum REST/WS rotalari
│   └── server.py             -- `python -m ops_suite.server` gercek girdi noktasi,
│                                OPS_SUITE_DATA_DIR/OPS_SUITE_IDENTITY_CONFIG_PATH ile izole edilebilir
├── frontend/                 -- statik HTML/CSS/vanilla JS (npm/bundler YOK, ADR-018/020)
│   └── js/scene.js           -- B038: saf Canvas2D animasyonlu ofis sahnesi
└── e2e/                      -- B039: Playwright E2E test tooling (npm SADECE burada, ADR-021)
    ├── tests/smoke.spec.js
    ├── tests/scene.spec.js
    └── capture_scene_evidence.js
```

`ops_suite`, `apps/orchestrator/src`in mevcut modüllerini (`orchestrator.py`,
`audit_logger.py`, `risk_engine.py`, `approval_stub.py`, `config_loader.py`)
**hiçbirini değiştirmeden** sarar — bkz. `scripts/e2e_demo.py`'nin aynı
"sar, dokunma" deseni.

## 5. Güvenlik ve yetki

- **Sahibi (owner) invaryantı:** Serkan Eryılmaz, sistemin tek kök
  yetkilisidir — B044 (ADR-019) ile bu artık KOD SEVİYESİNDE de
  uygulanır (`identity.py::authorize_decision` owner-root-guard):
  `risk_level="irreversible"` onayı delegate'in config'i ne yazarsa
  yazsın YALNIZCA owner'a açıktır (bkz. `IDENTITY_AND_DELEGATION_POLICY.md`).
- Onay kuyruğu, mevcut `risk_engine.py`/`approval_stub.py` risk
  seviyelerini (yalnızca `high`/`irreversible`) kullanır — risk
  hesaplama mantığı Ops Suite tarafından DEĞİŞTİRİLMEZ/atlanamaz.
- Onay/red uç noktaları `Authorization: Bearer <token>` ZORUNLU kılar;
  audit izine `actor_id`/`auth_method`/`authority_source`/`decision_scope`
  yazılır.
- **Kalan bilinen sınırlamalar** (B044 sonrası bile): tek kimlik
  doğrulama yöntemi (bearer-token), token rotasyon/iptal UI'ı yok, rate
  limiting yok — bkz. `IDENTITY_AND_DELEGATION_POLICY.md` §5.

## 6. Bilinen sınırlamalar (v0.2, dürüstçe)

- Ofis sahnesi (B038) yalnızca durum/geçiş doğruluğu kanıtlanmış bir
  KISMİ uygulamadır — bkz. §7. Hareket animasyonunun ara-karelerinin
  piksel-seviyesi doğruluğu, insan-gözü estetik değerlendirmesi
  YAPILMADI.
- Gerçek tarayıcı E2E altyapısı artık VAR ve GERÇEKTEN çalıştırıldı
  (B039, ADR-021) — ama bu yalnızca BU oturumun/makinenin kanıtıdır,
  CI/farklı bir ortamda yeniden doğrulanmalıdır.
- Gerçek ses (mikrofon/hoparlör/STT/TTS), GSM/SIM, kamera/gesture
  donanımı bu ortamda YOKTUR — B040. Sesli komutlar yalnızca MOCKED TR
  metin girdisiyle test edildi (bkz. `scripts/ops_suite_demo.py`
  `NOT_COLLECTED` bölümü).
- Heartbeat/presence durumu yalnızca BELLEK-İÇİ tutulur, sunucu yeniden
  başlatıldığında sıfırlanır — B041.

## 7. Ofis sahnesi (B038 — PLAN.md T31-T46)

**Kapsam (uygulandı):** saf Canvas2D (üçüncü taraf kütüphane YOK, ADR-020),
3 bilinen-canlı ajan için sabit masa konumu + paylaşılan/yayılmış bir
dinlenme bölgesi, 6 `not_implemented` ajan için AÇIKÇA soluk/ayrık bir
"hayalet raf" (asla canlı personel yanılsaması vermez — bkz.
`AGENT_PRESENCE_STATE_MODEL.md` §3), asistan avatarı (özel "rapor modu"
görseli `speaking` durumunda), onay-tepsisi rozeti (bekleyen onay
sayısı), `requestAnimationFrame` tabanlı basit hareket enterpolasyonu,
`window.__ops_suite_scene_debug__()` test köprüsü. **T40/B047 (2026-08-14):**
geometrik daire/baş-harf render'ının yerini yerel (CDN'siz) SVG
sprite'lar aldı — ajan/asistan/hayalet başına ayırt edilebilir bir ikon,
varlık eksik/bozuksa MEVCUT geometrik render'a sessizce-boş-bırakmadan
GERİ DÜŞME garantisiyle. **T42/B049 (2026-08-14):** sahne artık
tıklanabilir — bir ajana (hayalet raftakiler dahil) veya asistana
tıklamak, GERÇEK durumunu (state/last_task_id/last_heartbeat_ts/detail)
gösteren bir detay paneli açar; ajanların KENDİ bir "yetki kapsamı"
OLMADIĞI panelde açıkça belirtilir (fabrike edilmez); bekleyen bir
onayla eşleşen `last_task_id` varsa panelde bağlantı/vurgu olur.
**T45/B048 (2026-08-14):** her görev (`request_id`) artık sahnede bir
"görev işaretçisi" ile canlandırılıyor — kuyrukta → atandı → çalışıyor →
tamamlandı, tamamen zaten var olan `task.lifecycle`/`agent.presence` WS
olaylarıyla sürülüyor (backend'e dokunulmadı). "Tamamlandı" görevin
BAŞARILI olduğu anlamına GELMEZ — yalnızca ajanın işlemeyi bitirdiği
anlamına gelir (bkz. `docs/DECISIONS.md` ADR-023). **T46/B050
(2026-08-14):** yerel (CDN'siz, Web Audio API sentezlenmiş ton) bir ses
ipucu çerçevesi eklendi — global sessize alma (kalıcı) + bir politika
kapısı + 3 ayırt edilebilir ipucu (onay-gerekli/görev-tamamlandı/
politika-engeli — sonuncusu B044'ün gerçek 401/403 auth reddine
bağlandı, bkz. ADR-024).

**Kapsam dışı artık YOK (B038'in bilinen tüm v0+ tamamlama parçaları
uygulandı):** sprite varlıkları (B047), tıklama etkileşimi (B049),
çoklu-adımlı görev animasyonu (B048) ve ses ipuçları (B050) hepsi
YUKARIDAKİ "Kapsam (uygulandı)" bölümünde. Geriye kalan tek gerçek
sınırlama, sesin insan kulağıyla duyulduğunun doğrulanamaması
(NOT_COLLECTED — bu ortamda hoparlör donanımı yok, bkz. `docs/RUNBOOK.md`).

**Mimari sınırlama (T37/T38 ile ÇÖZÜLDÜ):** backend bir sesli komutu
BAŞTAN SONA senkron işler, bu yüzden bir ajanın `working` (masada aktif
çalışıyor) görsel durumu GERÇEK ama son derece kısa ömürlüdür. T36'da
bu, istemci tarafından bağımsız olarak GÖZLEMLENEMEZ kabul edilmişti.
T37 (`heartbeat.py::on_change` kancası + `agent.presence` WS yayını) ve
T38 (Playwright'ın native WS frame API'siyle deterministik doğrulama,
sleep/polling YOK) bunu tersine çevirdi — `working`→`idle` sırası artık
hem sahnede GERÇEK ZAMANLI tüketiliyor hem de testte kanıtlanabiliyor,
bkz. `apps/ops-suite/e2e/tests/scene.spec.js` "geçiş 4 (T38)".
