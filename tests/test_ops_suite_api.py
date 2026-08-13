"""PLAN.md T24 -- ops_suite.app REST uc noktalari testleri. GERCEK
`fastapi.testclient.TestClient` kullanir -- surec-ici ASGI cagrilari,
mock YOK. Her test tmp_path'e izole edilmis bilesenlerle KENDI
`create_app()` ornegini kurar (gercek proje `data/audit`/`data/approvals`
dosyalarina DOKUNMAZ)."""

from __future__ import annotations

from audit_logger import AuditLogger
from fastapi.testclient import TestClient

from ops_suite.app import create_app
from ops_suite.approval_queue import ApprovalQueueStore
from ops_suite.assistant_presence import AssistantPresenceTracker
from ops_suite.heartbeat import HeartbeatTracker
from ops_suite.status_resolver import KNOWN_LIVE_AGENTS, NOT_IMPLEMENTED_AGENTS
from ops_suite.voice_bridge import VoiceBridge


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
        voice_bridge=voice_bridge, audit_logger=audit_logger,
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


def test_approve_endpoint_clears_pending_entry(tmp_path):
    client = _client(tmp_path)
    request_id = client.post("/api/voice/command", json={"input_tr": "Ezo, tüm dosyaları sil"}).json()["request_id"]

    response = client.post(f"/api/approvals/{request_id}/approve", json={"actor": "serkan"})
    assert response.status_code == 200
    assert response.json()["decision"] == "approved"

    assert client.get("/api/approvals").json() == []


def test_reject_endpoint_clears_pending_entry(tmp_path):
    client = _client(tmp_path)
    request_id = client.post("/api/voice/command", json={"input_tr": "Ezo, tüm dosyaları sil"}).json()["request_id"]

    response = client.post(f"/api/approvals/{request_id}/reject", json={"actor": "serkan", "note": "riskli"})
    assert response.status_code == 200
    assert response.json()["decision"] == "rejected"
    assert response.json()["note"] == "riskli"


def test_approve_unknown_request_id_returns_404(tmp_path):
    client = _client(tmp_path)
    response = client.post("/api/approvals/does-not-exist/approve", json={"actor": "serkan"})
    assert response.status_code == 404


def test_approve_twice_returns_409(tmp_path):
    client = _client(tmp_path)
    request_id = client.post("/api/voice/command", json={"input_tr": "Ezo, tüm dosyaları sil"}).json()["request_id"]
    client.post(f"/api/approvals/{request_id}/approve", json={"actor": "serkan"})

    response = client.post(f"/api/approvals/{request_id}/approve", json={"actor": "serkan"})
    assert response.status_code == 409


def test_approve_writes_audit_record(tmp_path):
    client = _client(tmp_path)
    request_id = client.post("/api/voice/command", json={"input_tr": "Ezo, tüm dosyaları sil"}).json()["request_id"]
    client.post(f"/api/approvals/{request_id}/approve", json={"actor": "serkan"})

    import json

    lines = (tmp_path / "audit.log.jsonl").read_text(encoding="utf-8").splitlines()
    last = json.loads(lines[-1])
    assert last["status"] == "APPROVED"
    assert last["request_id"] == request_id


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
