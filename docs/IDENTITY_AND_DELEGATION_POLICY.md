# Kimlik ve Yetki Devri Politikası (Identity and Delegation Policy)

> Durum: v0.1, 2026-08-14 — bkz. [PLAN.md](PLAN.md) T23/T24/T28,
> [DECISIONS.md](DECISIONS.md) ADR-016/ADR-019, [BACKLOG.md](BACKLOG.md) B044
> (SECURITY P0, kapalı).

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
approval_queue.py (ApprovalQueueStore)
        │  submit() -- kalıcı, sorgulanabilir kayıt
        ▼
[YENİ, T28/B044] identity.py (IdentityStore.authenticate + authorize_decision)
        │  Authorization: Bearer <token> → Identity (owner|delegate)
        │  risk_level + decision → izinli mi? → decision_scope
        ▼
Ops Suite API (POST /api/approvals/{id}/approve|reject)
        │  identity.actor_id, auth_method, authority_source, decision_scope, note (opsiyonel)
        ▼
audit_logger.py (APPROVED/REJECTED kaydı, 4 yeni kimlik alanıyla) + approval_queue.decide()
```

`risk_engine.py`/`approval_stub.py` **DEĞİŞTİRİLMEDİ** — Ops Suite
yalnızca `WAITING_APPROVAL` durumunu, önceden var olmayan kalıcı bir
kuyruğa SARAR (bkz. `docs/DECISIONS.md` ADR-016).

## 4. Kimlik doğrulama + yetkilendirme (v0.1, B044 ile kapatıldı)

**Önceki v0 sınırlaması (2026-08-14, T23/T24 sırasında) kapatıldı** —
`POST /api/approvals/{id}/approve|reject` artık **`Authorization:
Bearer <token>` ZORUNLUDUR** (bkz. `ops_suite/identity.py`,
`docs/DECISIONS.md` ADR-019). Eylemi kimin yaptığı artık istemcinin
beyan ettiği serbest metinden DEĞİL, dogrulanmış bir `Identity`'den
gelir.

**Kimlik kaynağı:** `config/ops_suite_identities.json` — yalnızca
`actor_id`/`display_name`/`token_env_var`(/`scopes`) alanlarını
içerir; **token DEĞERLERİ bu dosyada ASLA tutulmaz** (ADR-010 ile aynı
ilke — bkz. `legitimacy_provider_client.py`'nin Jira credential
deseni). Gerçek token yalnızca `token_env_var`'ın işaret ettiği ortam
değişkeninde yaşar. Env değişkeni SET EDİLMEMİŞSE o kimlikle giriş
**YAPILAMAZ** — fail-closed, fail-open DEĞİL.

**Sahibi kök koruyucu (owner root guard) — kod seviyesinde, config'ten
BAĞIMSIZ:**
- `authority_source="owner"` olan kimlik HER ZAMAN tüm kapsamlara
  (scopes) sahiptir (`Identity.has_scope` sabiti).
- `risk_level="irreversible"` (veya bilinmeyen/eşleşmeyen bir risk
  seviyesi) onayı **YALNIZCA sahibi** tarafından verilebilir — bir
  delegate'in config'inde `approve:irreversible` yazıyor OLSA BİLE
  reddedilir (bkz. `identity.py::authorize_decision`,
  `tests/test_ops_suite_identity.py::test_delegate_cannot_approve_irreversible_even_without_config_restriction`).
  Bu, yanlış yapılandırılmış bir delegate kaydına karşı
  defense-in-depth'tir.

**Delegasyon (kapsam/scope modeli, artık UYGULANDI):** `config/ops_suite_identities.json`'ın
`delegates` listesi, her delegate'e ayrı bir `scopes` kümesi tanımlar
(`approve:low`/`approve:medium`/`approve:high`/`approve:irreversible`/`reject`
— bkz. `identity.py::ALL_SCOPES`). Kapsam dışı bir eylem denemesi
**HTTP 403** ile, açık bir hata mesajıyla reddedilir (ör. `"'delegate_1'
(delegate) 'approve:high' kapsamına sahip DEĞİL"`). Şu an
**komite edilen `config/ops_suite_identities.json`'da hiçbir gerçek
delegate YOK** (`delegates: []`) — mekanizma çalışır durumda ama
Serkan Eryılmaz henüz kimseye yetki devretmedi; ilk gerçek delegate
eklenmesi ayrı, açık bir config değişikliği + BACKLOG kaydı
gerektirir.

**Ağ düzeyi izolasyon hâlâ geçerli** (Ops Suite v0, yalnızca
`127.0.0.1` loopback'te çalışır, bkz. `ops_suite/server.py::DEFAULT_HOST`)
— ama artık kimlik doğrulama/yetkilendirme katmanının **YERİNE**
değil, **YANINDA** ikinci bir savunma katmanı olarak.

**Audit izi genişletildi:** her onay/red kararı artık `actor_id`,
`auth_method` (şu an her zaman `"bearer"`), `authority_source`
(`owner`/`delegate`), `decision_scope` (`"owner_root"` veya ör.
`"approve:high"`) alanlarını hem `data/audit/audit.log.jsonl`'a hem
`data/approvals/approval_queue.jsonl`'ın `DECIDED` kaydına yazar (bkz.
`approval_queue.py::ApprovalQueueStore.decide()`).

**Güvenlik Sertleştirme Sprint-1 (2026-08-14, B051-B053) ile derinleştirildi:**
- **Token rotasyonu/iptali (B051):** owner-only `POST /api/identity/{actor_id}/rotate`\|`revoke`
  artık VAR — bir token'ı iptal etmek için sunucu yeniden başlatma
  GEREKMEZ. İptal KALICIDIR (`data/identity/token_revocations.jsonl`,
  yalnızca SHA-256 özet — ham token DEĞERİ ASLA), sunucu yeniden
  başlasa BİLE etkili kalır. Bkz. `identity.py::TokenRevocationStore`,
  `docs/DECISIONS.md` ADR-025.
- **Rate limiting (B052):** onay/red + rotate/revoke uç noktaları artık
  kimlik doğrulanmış actor+eylem-kategorisi başına bir istek-sıklığı
  sınırına (varsayılan 20/60sn) sahip — aşıldığında yapılandırılmış bir
  429 döner. Bkz. `rate_limiter.py::RateLimiter`.
- **Auth karar audit standardizasyonu (B053):** ÖNCEDEN yalnızca
  BAŞARILI onay/red kararları audit'e yazılıyordu — şimdi 401/403/429
  DAHİL her auth kararı, standardize `details.auth_decision.{actor,scope,decision,reason_code}`
  alanlarıyla loglanıyor. Bkz. `auth_audit.py`.

## 5. Bilinen sınırlamalar (v0.1, dürüstçe işaretli)

- **Tek kimlik doğrulama yöntemi:** yalnızca bearer-token (paylaşılan
  sır) — OAuth/OIDC/mTLS/donanım anahtarı YOK. Tek-kullanıcılı, yerel
  bir kontrol merkezi için orantılı görülüyor (bkz.
  `docs/DECISIONS.md` ADR-019), ama gelecekte dışarıya açılma
  senaryosunda yeniden değerlendirilmeli.
- ~~Token rotasyonu/iptali için UI/CLI YOK~~ — **2026-08-14'te B051 ile
  KAPATILDI** (owner-only API artık var, yukarıya bkz.). CLI hâlâ YOK
  (yalnızca API) — bu, dışarıya açılma senaryosunda ayrı bir madde
  olarak değerlendirilebilir.
- ~~Rate limiting/brute-force koruması YOK~~ — **2026-08-14'te B052 ile
  KAPATILDI** (yukarıya bkz.). `IdentityStore.authenticate()` hâlâ
  zamanlama-saldırısına dayanıklıdır (`hmac.compare_digest`); rate
  limiting eşiği v0'da kod-içi sabittir (merkezi bir config kaynağından
  beslenmiyor — B050'nin ses politika kapısıyla AYNI v0 sınırı).
- **Rate limiting eşiği config'ten beslenmiyor** (v0 sınırı, yukarıya
  bkz.) ve **token rotasyonu/iptali CLI'dan yapılamıyor** (yalnızca
  API) — bu iki madde henüz **BACKLOG.md**'ye kaydedilmedi (henüz P0
  değil, Ops Suite hâlâ yalnızca loopback'te) — dışarıya açılma kararı
  alınırsa önce buraya, sonra BACKLOG'a eklenmelidir.
