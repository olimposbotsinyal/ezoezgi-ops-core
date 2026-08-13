# Kimlik ve Yetki Devri Politikası (Identity and Delegation Policy)

> Durum: v0, 2026-08-14 — bkz. [PLAN.md](PLAN.md) T23/T24,
> [DECISIONS.md](DECISIONS.md) ADR-016.

## 1. Sahibi (owner) yetki değişmezi

**Serkan Eryılmaz, EzoEzgi sisteminin TEK kök yetkilisidir (sole root
authority).** Bu, projenin tüm katmanlarında (governance, onay akışı,
Ops Suite) değişmez bir referans noktasıdır — hiçbir kod yolu, config
değişikliği veya delegasyon mekanizması bu invaryantı DOLAYLI olarak
bile değiştiremez. Bu belge, bu invaryantın Ops Suite bağlamında NASIL
(henüz kısmen) korunduğunu ve NEREDE henüz teknik olarak
uygulanmadığını AÇIKÇA belgeler.

## 2. Ajan kimlik taksonomisi

`apps/ops-suite/backend/src/ops_suite/status_resolver.py`'deki sabit
`agent_id` kümesi:

| `agent_id` | Görünen ad | Durum |
|---|---|---|
| `orchestrator` | Orchestrator | Gerçek, çalışan kod |
| `bridge_agent` | Bridge Agent | Gerçek, çalışan kod |
| `tool_runners` | Tool Runners | Gerçek, çalışan kod (whitelist tabanlı, T12) |
| `finance_agent` / `social_agent` / `research_agent` / `doc_agent` / `device_agent` / `voice_agent` | (MASTER_ROADMAP.md §3) | `not_implemented` — servis klasörleri boş |

Yeni bir `agent_id` eklemek İSTEĞE BAĞLI/kod-incelemesi-gerektiren bir
değişikliktir — `status_resolver.py`'nin dürüstlük kuralı (bkz.
`AGENT_PRESENCE_STATE_MODEL.md`) SESSİZCE atlanamaz.

## 3. Onay/delegasyon modeli — mevcut zincir

```
risk_engine.py (RiskEngine.get_risk)
        │  low/medium/high/irreversible
        ▼
approval_stub.py (check_approval)
        │  AUTO_ALLOWED | WAITING_APPROVAL
        ▼
[YENİ, T23] approval_queue.py (ApprovalQueueStore)
        │  submit() -- kalıcı, sorgulanabilir kayıt
        ▼
Ops Suite API (POST /api/approvals/{id}/approve|reject)
        │  actor (serbest metin), note (opsiyonel)
        ▼
audit_logger.py (APPROVED/REJECTED kaydı) + approval_queue.decide()
```

`risk_engine.py`/`approval_stub.py` **DEĞİŞTİRİLMEDİ** — Ops Suite
yalnızca `WAITING_APPROVAL` durumunu, önceden var olmayan kalıcı bir
kuyruğa SARAR (bkz. `docs/DECISIONS.md` ADR-016).

## 4. v0 bilinen sınırlama — kimlik doğrulaması YOK (açık, önemli)

`POST /api/approvals/{id}/approve|reject`'in `actor` alanı **SERBEST
METİNDİR, kimlik doğrulaması (authentication) YOKTUR.** Bu, §1'deki
sahibi-yetki invaryantının şu an yalnızca **PROSEDÜREL** olarak
korunduğu, **TEKNİK** olarak henüz UYGULANMADIĞI anlamına gelir:

- Ops Suite v0, tek-kullanıcılı ve yalnızca `127.0.0.1` (loopback)
  üzerinde çalışacak şekilde tasarlanmıştır (bkz.
  `ops_suite/server.py::DEFAULT_HOST`) — dışarıdan erişim YOKTUR.
- Bu, gerçek bir kimlik doğrulama/yetkilendirme katmanının YERİNE
  GEÇMEZ — yalnızca ağ düzeyinde bir izolasyondur.
- `actor` alanına HERHANGİ bir metin girilebilir; sistem bunun
  GERÇEKTEN Serkan Eryılmaz olduğunu doğrulamaz.

**Bu sınırlama BİLEREK v0 kapsamında bırakıldı** (bir kimlik doğrulama
sistemi kurmak, Ops Suite'in gerçek zamanlı görünürlük hedefinden
AYRI, kendi başına büyük bir iş kalemidir) — gelecekteki bir kimlik
doğrulama katmanı (`docs/BACKLOG.md`'ye eklenmesi gereken bir madde)
GERÇEK yetki denetimini sağlayana kadar, Ops Suite'in onay
uçnoktalarının **yalnızca güvenilir/yerel bir ortamda** çalıştırıldığı
varsayılır.

## 5. Delegasyon (gelecek)

MASTER_ROADMAP.md'nin genel "delegable authorization model" hedefi
(başka kişilere sınırlı yetki devri) Ops Suite v0 kapsamında
UYGULANMADI — mevcut model tek-sahipli (`actor` yalnızca bilgi amaçlı
bir alan, ayrı yetki seviyeleri YOK). Bu, kimlik doğrulama katmanıyla
BİRLİKTE ele alınması gereken bir gelecek işidir.
