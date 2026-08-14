"""Auth karar audit alanlarinin standardizasyonu (BACKLOG.md B053,
PLAN.md T52, DECISIONS.md ADR-025, SECURITY).

**Neden bu modul var:** B044 audit trail'e `actor_id`/`auth_method`/
`authority_source`/`decision_scope` eklemisti (ADR-019) ama YALNIZCA
BASARILI onay/red kararlari icin -- 401 (kimlik dogrulama basarisiz)/
403 (yetkilendirme basarisiz)/429 (hiz siniri asildi) HICBIR ZAMAN
audit'e YAZILMIYORDU. Bu, gercek, sessiz bir gozlemlenebilirlik
boslugu -- ornegin bir brute-force token deneme dizisi audit log'da
HICBIR IZ BIRAKMAZDI.

**Tasarim (ADDITIVE, B044'u BOZMAZ):** `build_auth_decision_details()`
mevcut `actor_id`/`authority_source`/`decision_scope` alanlarini
DEGISTIRMEZ/SILMEZ (bkz. `tests/test_ops_suite_api.py::test_approve_writes_audit_record_with_full_identity_fields`
-- bu alanlara HALA dayanir) -- yalnizca `details.auth_decision` altinda
YENI, standardize edilmis `actor`/`scope`/`decision`/`reason_code`
alanlarini EKLER. `request_id`/`timestamp` zaten `AuditLogger.log()`'un
her kaydinda UST SEVIYEDE bulunur -- burada TEKRAR EDILMEZ.

**Durustluk siniri:** hicbir cagiran taraf `actor` alanina HAM TOKEN
DEGERI GECIRMEMELIDIR -- yalnizca `actor_id` (zaten kamuya acik bir
tanimlayici) veya kimlik dogrulanamadigi icin actor BILINMIYORSA
`None`."""

from __future__ import annotations

from typing import Any


def build_auth_decision_details(
    *,
    actor: str | None,
    scope: str,
    decision: str,
    reason_code: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`AuditLogger.log(details=...)`'e verilecek sozlugu insa eder.
    `extra` (varsa) BASE olarak kullanilir -- cagiran taraf B044'un
    mevcut alanlarini (`actor_id`/`auth_method`/`authority_source`/
    `decision_scope`/vb.) `extra` icinde geçirebilir, bu fonksiyon
    yalnizca `auth_decision` alt-sozlugunu EKLER, EZMEZ."""
    payload: dict[str, Any] = dict(extra or {})
    payload["auth_decision"] = {
        "actor": actor,
        "scope": scope,
        "decision": decision,
        "reason_code": reason_code,
    }
    return payload
