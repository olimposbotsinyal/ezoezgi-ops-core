"""PLAN.md T24/T28 -- ops_suite.app REST uc noktalari testleri. GERCEK
`fastapi.testclient.TestClient` kullanir -- surec-ici ASGI cagrilari,
mock YOK. Her test tmp_path'e izole edilmis bilesenlerle KENDI
`create_app()` ornegini kurar (gercek proje `data/audit`/`data/approvals`
dosyalarina DOKUNMAZ).

B044 (SECURITY P0) -- yetkilendirme testleri gercek bir `IdentityStore`
kullanir (dosya/env YOK, dogrudan `IdentityStore({token: Identity})`
enjekte edilir) -- owner + iki delegate test kimligi (bkz. `_identity_store`)."""

from __future__ import annotations

import json

from audit_logger import AuditLogger
from fastapi.testclient import TestClient

from ops_suite.app import create_app
from ops_suite.approval_queue import ApprovalQueueStore
from ops_suite.assistant_presence import AssistantPresenceTracker
from ops_suite.heartbeat import HeartbeatTracker
from ops_suite.identity import (
    AUTHORITY_DELEGATE,
    AUTHORITY_OWNER,
    SCOPE_APPROVE_HIGH,
    SCOPE_APPROVE_IRREVERSIBLE,
    SCOPE_APPROVE_LOW,
    SCOPE_REJECT,
    Identity,
    IdentityStore,
)
from ops_suite.status_resolver import KNOWN_LIVE_AGENTS, NOT_IMPLEMENTED_AGENTS
from ops_suite.voice_bridge import VoiceBridge

OWNER_TOKEN = "dummy-owner-token"  # noqa: S105 -- test-only dummy deger, gercek secret DEGIL
DELEGATE_FULL_TOKEN = "dummy-delegate-full-token"  # noqa: S105
DELEGATE_LOW_TOKEN = "dummy-delegate-low-token"  # noqa: S105


def _identity_store() -> IdentityStore:
    return IdentityStore(
        {
            OWNER_TOKEN: Identity(actor_id="serkan_eryilmaz", display_name="Serkan Eryılmaz", authority_source=AUTHORITY_OWNER),
            # BILEREK butun scope'lari (approve:irreversible dahil) tasiyan bir delegate --
            # owner-root-guard'in config'teki scope'lardan BAGIMSIZ oldugunu kanitlamak icin.
            DELEGATE_FULL_TOKEN: Identity(
                actor_id="delegate_full", display_name="Test Delegate (full)", authority_source=AUTHORITY_DELEGATE,
                scopes=frozenset({SCOPE_APPROVE_LOW, SCOPE_APPROVE_HIGH, SCOPE_APPROVE_IRREVERSIBLE, SCOPE_REJECT}),
            ),
            DELEGATE_LOW_TOKEN: Identity(
                actor_id="delegate_low", display_name="Test Delegate (low)", authority_source=AUTHORITY_DELEGATE,
                scopes=frozenset({SCOPE_APPROVE_LOW}),
            ),
        }
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _client(tmp_path) -> TestClient:
    heartbeat = HeartbeatTracker()
    approval_queue = ApprovalQueueStore(tmp_path / "approval_queue.jsonl")
    assistant_presence = AssistantPresenceTracker()
    audit_logger = AuditLogger(tmp_path / "audit.log.jsonl")
    voice_bridge = VoiceBridge(
        audit_log_path=tmp_path / "audit.log.jsonl", approval_queue=approval_queue,
        assistant_presence=assistant_presence, heartbeat_tracker=heartbeat,
    )
    app = create_app(
        heartbeat_tracker=heartbeat, approval_queue=approval_queue, assistant_presence=assistant_presence,
        voice_bridge=voice_bridge, audit_logger=audit_logger, identity_store=_identity_store(),
    )
    return TestClient(app)


def test_get_agents_lists_known_live_and_not_implemented_agents(tmp_path):
    client = _client(tmp_path)
    response = client.get("/api/agents")
    assert response.status_code == 200
    agent_ids = {a["agent_id"] for a in response.json()}
    assert agent_ids == set(KNOWN_LIVE_AGENTS) | set(NOT_IMPLEMENTED_AGENTS)


def test_get_assistant_defaults_to_idle(tmp_path):
    client = _client(tmp_path)
    response = client.get("/api/assistant")
    assert response.status_code == 200
    assert response.json()["state"] == "idle"


def test_get_approvals_empty_by_default(tmp_path):
    client = _client(tmp_path)
    response = client.get("/api/approvals")
    assert response.status_code == 200
    assert response.json() == []


def test_get_approvals_rejects_unsupported_status(tmp_path):
    client = _client(tmp_path)
    response = client.get("/api/approvals?status=archived")
    assert response.status_code == 400


def test_post_voice_command_echo_returns_tr_response(tmp_path):
    client = _client(tmp_path)
    response = client.post("/api/voice/command", json={"input_tr": "Ezo, echo ile 'merhaba' yaz"})
    assert response.status_code == 200
    body = response.json()
    assert body["tr_response"] == "Merhaba yazdırıldı."
    assert body["result_en"]["status"] == "ok"


def test_post_voice_command_irreversible_appears_in_pending_approvals(tmp_path):
    client = _client(tmp_path)
    response = client.post("/api/voice/command", json={"input_tr": "Ezo, tüm dosyaları sil"})
    request_id = response.json()["request_id"]

    approvals = client.get("/api/approvals").json()
    assert any(a["request_id"] == request_id for a in approvals)


# --- Kimlik dogrulama (B044) --------------------------------------------


def test_approve_without_token_returns_401(tmp_path):
    client = _client(tmp_path)
    request_id = client.post("/api/voice/command", json={"input_tr": "Ezo, tüm dosyaları sil"}).json()["request_id"]

    response = client.post(f"/api/approvals/{request_id}/approve", json={})
    assert response.status_code == 401


def test_approve_with_invalid_token_returns_401(tmp_path):
    client = _client(tmp_path)
    request_id = client.post("/api/voice/command", json={"input_tr": "Ezo, tüm dosyaları sil"}).json()["request_id"]

    response = client.post(f"/api/approvals/{request_id}/approve", json={}, headers=_auth("not-a-real-token"))
    assert response.status_code == 401


def test_whoami_without_token_returns_401(tmp_path):
    client = _client(tmp_path)
    assert client.get("/api/whoami").status_code == 401


def test_whoami_with_owner_token_returns_identity(tmp_path):
    client = _client(tmp_path)
    response = client.get("/api/whoami", headers=_auth(OWNER_TOKEN))
    assert response.status_code == 200
    body = response.json()
    assert body["actor_id"] == "serkan_eryilmaz"
    assert body["authority_source"] == "owner"


# --- Yetkilendirme / owner-root-guard (B044) ----------------------------


def test_owner_can_approve_irreversible(tmp_path):
    client = _client(tmp_path)
    request_id = client.post("/api/voice/command", json={"input_tr": "Ezo, tüm dosyaları sil"}).json()["request_id"]

    response = client.post(f"/api/approvals/{request_id}/approve", json={}, headers=_auth(OWNER_TOKEN))
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "approved"
    assert body["authority_source"] == "owner"
    assert body["decision_scope"] == "owner_root"


def test_delegate_with_irreversible_scope_still_denied_root_guard(tmp_path):
    """DELEGATE_FULL_TOKEN kimligi `approve:irreversible` scope'una config'te
    SAHIP OLSA BILE, owner-root-guard bunu YOK SAYAR -- yalnizca owner
    irreversible onaylayabilir (defense-in-depth, bkz. identity.py)."""
    client = _client(tmp_path)
    request_id = client.post("/api/voice/command", json={"input_tr": "Ezo, tüm dosyaları sil"}).json()["request_id"]

    response = client.post(f"/api/approvals/{request_id}/approve", json={}, headers=_auth(DELEGATE_FULL_TOKEN))
    assert response.status_code == 403

    # Kuyrukta HALA bekliyor olmali -- reddedilen yetkilendirme denemesi
    # kararı GERCEKLESTIRMEMIS olmali.
    assert any(a["request_id"] == request_id for a in client.get("/api/approvals").json())


def test_delegate_with_low_scope_can_approve_low_risk_entry(tmp_path):
    """Voice bridge uzerinden erisilebilir tek WAITING_APPROVAL senaryosu
    irreversible oldugu icin (bkz. policies/risk/tool_risk_policy.yaml),
    dusuk riskli bir bekleyen kaydi DOGRUDAN kuyruga submit ederek
    (gercek store metodu, fabrike veri DEGIL) delegate kapsam kontrolu
    izole test edilir."""
    heartbeat = HeartbeatTracker()
    approval_queue = ApprovalQueueStore(tmp_path / "approval_queue.jsonl")
    assistant_presence = AssistantPresenceTracker()
    audit_logger = AuditLogger(tmp_path / "audit.log.jsonl")
    voice_bridge = VoiceBridge(
        audit_log_path=tmp_path / "audit.log.jsonl", approval_queue=approval_queue,
        assistant_presence=assistant_presence, heartbeat_tracker=heartbeat,
    )
    app = create_app(
        heartbeat_tracker=heartbeat, approval_queue=approval_queue, assistant_presence=assistant_presence,
        voice_bridge=voice_bridge, audit_logger=audit_logger, identity_store=_identity_store(),
    )
    client = TestClient(app)

    approval_queue.submit(request_id="low-r1", alias="ezo", task="SHOW_DAILY_SPENDING", risk_level="low", original_tr="harcama")

    response = client.post("/api/approvals/low-r1/approve", json={}, headers=_auth(DELEGATE_LOW_TOKEN))
    assert response.status_code == 200
    assert response.json()["decision_scope"] == "approve:low"


def test_delegate_without_matching_scope_returns_403(tmp_path):
    heartbeat = HeartbeatTracker()
    approval_queue = ApprovalQueueStore(tmp_path / "approval_queue.jsonl")
    assistant_presence = AssistantPresenceTracker()
    audit_logger = AuditLogger(tmp_path / "audit.log.jsonl")
    voice_bridge = VoiceBridge(
        audit_log_path=tmp_path / "audit.log.jsonl", approval_queue=approval_queue,
        assistant_presence=assistant_presence, heartbeat_tracker=heartbeat,
    )
    app = create_app(
        heartbeat_tracker=heartbeat, approval_queue=approval_queue, assistant_presence=assistant_presence,
        voice_bridge=voice_bridge, audit_logger=audit_logger, identity_store=_identity_store(),
    )
    client = TestClient(app)

    approval_queue.submit(request_id="high-r1", alias="ezo", task="RUN_SHELL_SAFE", risk_level="high", original_tr="komut")

    response = client.post("/api/approvals/high-r1/approve", json={}, headers=_auth(DELEGATE_LOW_TOKEN))
    assert response.status_code == 403


def test_delegate_without_reject_scope_returns_403(tmp_path):
    heartbeat = HeartbeatTracker()
    approval_queue = ApprovalQueueStore(tmp_path / "approval_queue.jsonl")
    assistant_presence = AssistantPresenceTracker()
    audit_logger = AuditLogger(tmp_path / "audit.log.jsonl")
    voice_bridge = VoiceBridge(
        audit_log_path=tmp_path / "audit.log.jsonl", approval_queue=approval_queue,
        assistant_presence=assistant_presence, heartbeat_tracker=heartbeat,
    )
    app = create_app(
        heartbeat_tracker=heartbeat, approval_queue=approval_queue, assistant_presence=assistant_presence,
        voice_bridge=voice_bridge, audit_logger=audit_logger, identity_store=_identity_store(),
    )
    client = TestClient(app)

    approval_queue.submit(request_id="low-r2", alias="ezo", task="SHOW_DAILY_SPENDING", risk_level="low", original_tr="harcama")

    # DELEGATE_LOW_TOKEN kimliginin scope'unda yalnizca approve:low var, reject YOK.
    response = client.post("/api/approvals/low-r2/reject", json={}, headers=_auth(DELEGATE_LOW_TOKEN))
    assert response.status_code == 403


def test_reject_endpoint_clears_pending_entry(tmp_path):
    client = _client(tmp_path)
    request_id = client.post("/api/voice/command", json={"input_tr": "Ezo, tüm dosyaları sil"}).json()["request_id"]

    response = client.post(f"/api/approvals/{request_id}/reject", json={"note": "riskli"}, headers=_auth(OWNER_TOKEN))
    assert response.status_code == 200
    assert response.json()["decision"] == "rejected"
    assert response.json()["note"] == "riskli"

    assert client.get("/api/approvals").json() == []


def test_approve_unknown_request_id_returns_404(tmp_path):
    client = _client(tmp_path)
    response = client.post("/api/approvals/does-not-exist/approve", json={}, headers=_auth(OWNER_TOKEN))
    assert response.status_code == 404


def test_approve_twice_returns_409(tmp_path):
    client = _client(tmp_path)
    request_id = client.post("/api/voice/command", json={"input_tr": "Ezo, tüm dosyaları sil"}).json()["request_id"]
    client.post(f"/api/approvals/{request_id}/approve", json={}, headers=_auth(OWNER_TOKEN))

    response = client.post(f"/api/approvals/{request_id}/approve", json={}, headers=_auth(OWNER_TOKEN))
    assert response.status_code == 409


def test_approve_writes_audit_record_with_full_identity_fields(tmp_path):
    client = _client(tmp_path)
    request_id = client.post("/api/voice/command", json={"input_tr": "Ezo, tüm dosyaları sil"}).json()["request_id"]
    client.post(f"/api/approvals/{request_id}/approve", json={}, headers=_auth(OWNER_TOKEN))

    lines = (tmp_path / "audit.log.jsonl").read_text(encoding="utf-8").splitlines()
    last = json.loads(lines[-1])
    assert last["status"] == "APPROVED"
    assert last["request_id"] == request_id
    assert last["details"]["actor_id"] == "serkan_eryilmaz"
    assert last["details"]["auth_method"] == "bearer"
    assert last["details"]["authority_source"] == "owner"
    assert last["details"]["decision_scope"] == "owner_root"


def test_post_voice_command_updates_assistant_endpoint(tmp_path):
    client = _client(tmp_path)
    client.post("/api/voice/command", json={"input_tr": "Ezo, echo ile 'merhaba' yaz"})
    assert client.get("/api/assistant").json()["state"] == "speaking"


def test_root_serves_frontend_index_html(tmp_path):
    """T26 -- StaticFiles mount'unun `/api/*`/`/ws/live` rotalarindan
    SONRA eklendigini VE onlarin ONUNE GECMEDIGINI dogrular (mount
    sirasi hatasi olsaydi API rotalari calismaz olurdu -- yukaridaki
    testler zaten bunu dolayli kanitliyor; bu test frontend'in de
    GERCEKTEN sunuldugunu dogrudan kanitlar)."""
    client = _client(tmp_path)
    response = client.get("/")
    assert response.status_code == 200
    assert "EzoEzgi Ops Suite" in response.text


def test_static_css_is_served(tmp_path):
    client = _client(tmp_path)
    response = client.get("/css/style.css")
    assert response.status_code == 200
    assert "--accent" in response.text
