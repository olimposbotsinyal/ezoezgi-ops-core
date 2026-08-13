"""PLAN.md T24 -- ops_suite.app WebSocket testleri. GERCEK bir WS el
sikismasi (`TestClient.websocket_connect`) uzerinden -- mock YOK, surec-ici
ASGI transport uzerinden gercek WS protokolu calisir."""

from __future__ import annotations

from audit_logger import AuditLogger
from fastapi.testclient import TestClient

from ops_suite.app import create_app
from ops_suite.approval_queue import ApprovalQueueStore
from ops_suite.assistant_presence import AssistantPresenceTracker
from ops_suite.heartbeat import HeartbeatTracker
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


def test_ws_connects_successfully(tmp_path):
    client = _client(tmp_path)
    with client.websocket_connect("/ws/live"):
        pass  # bağlanti + kapanma hatasiz tamamlanmali


def test_ws_receives_task_lifecycle_events_from_voice_command(tmp_path):
    client = _client(tmp_path)
    with client.websocket_connect("/ws/live") as websocket:
        client.post("/api/voice/command", json={"input_tr": "Ezo, echo ile 'merhaba' yaz"})

        received_topics = []
        for _ in range(4):  # received/translating/risk_checked/completed
            message = websocket.receive_json()
            received_topics.append(message["topic"])

        assert received_topics.count("task.lifecycle") == 4


def test_ws_receives_assistant_presence_event_after_voice_command(tmp_path):
    client = _client(tmp_path)
    with client.websocket_connect("/ws/live") as websocket:
        client.post("/api/voice/command", json={"input_tr": "Ezo, echo ile 'merhaba' yaz"})

        topics = [websocket.receive_json()["topic"] for _ in range(5)]
        assert "assistant.presence" in topics


def test_ws_receives_approval_queue_event_for_irreversible_command(tmp_path):
    client = _client(tmp_path)
    with client.websocket_connect("/ws/live") as websocket:
        client.post("/api/voice/command", json={"input_tr": "Ezo, tüm dosyaları sil"})

        topics = [websocket.receive_json()["topic"] for _ in range(6)]
        assert "approval.queue" in topics


def test_ws_event_payload_matches_lifecycle_state_sequence(tmp_path):
    client = _client(tmp_path)
    with client.websocket_connect("/ws/live") as websocket:
        client.post("/api/voice/command", json={"input_tr": "Ezo, echo ile 'merhaba' yaz"})

        states = []
        for _ in range(4):
            message = websocket.receive_json()
            if message["topic"] == "task.lifecycle":
                states.append(message["payload"]["state"])

        assert states == ["received", "translating", "risk_checked", "completed"]


def test_ws_seq_numbers_increase_monotonically(tmp_path):
    client = _client(tmp_path)
    with client.websocket_connect("/ws/live") as websocket:
        client.post("/api/voice/command", json={"input_tr": "Ezo, echo ile 'merhaba' yaz"})
        seqs = [websocket.receive_json()["seq"] for _ in range(5)]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)


def test_ws_receives_approval_decision_broadcast(tmp_path):
    client = _client(tmp_path)
    request_id = client.post("/api/voice/command", json={"input_tr": "Ezo, tüm dosyaları sil"}).json()["request_id"]

    with client.websocket_connect("/ws/live") as websocket:
        client.post(f"/api/approvals/{request_id}/approve", json={"actor": "serkan"})
        message = websocket.receive_json()
        assert message["topic"] == "approval.queue"
        assert message["payload"]["decision"] == "approved"
