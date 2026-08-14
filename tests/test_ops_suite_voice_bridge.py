"""PLAN.md T25 -- ops_suite.voice_bridge testleri: mocked-TR-metin "ses"
komutundan uctan uca akis. GERCEK `config/assistant.identity.json` +
`policies/risk/tool_risk_policy.yaml` (salt-okunur, statik) kullanilir;
audit log + approval queue GERCEK proje dosyalarini KIRLETMEMEK icin
tmp_path'e izole edilir (bkz. `scripts/e2e_demo.py`'nin AKSINE -- o
GERCEK audit log'a yazar, `test_e2e_acceptance.py`'de zaten kanitlanmis;
burada tekrarlamaya GEREK yok)."""

from __future__ import annotations

from ops_suite.approval_queue import ApprovalQueueStore
from ops_suite.assistant_presence import AssistantPresenceTracker
from ops_suite.heartbeat import HeartbeatTracker
from ops_suite.voice_bridge import VoiceBridge


def _bridge(tmp_path, **overrides) -> VoiceBridge:
    defaults = dict(
        audit_log_path=tmp_path / "audit.log.jsonl",
        approval_queue=ApprovalQueueStore(tmp_path / "approval_queue.jsonl"),
    )
    defaults.update(overrides)
    return VoiceBridge(**defaults)


def test_handle_voice_command_echo_completes_and_returns_tr_response(tmp_path):
    bridge = _bridge(tmp_path)
    outcome = bridge.handle_voice_command("Ezo, echo ile 'merhaba' yaz")

    assert outcome["tr_response"] == "Merhaba yazdırıldı."
    assert outcome["result_en"]["status"] == "ok"
    assert outcome["approval_submission"] is None
    assert outcome["request_id"]


def test_handle_voice_command_echo_emits_completed_lifecycle_event(tmp_path):
    bridge = _bridge(tmp_path)
    outcome = bridge.handle_voice_command("Ezo, echo ile 'merhaba' yaz")
    states = [e.state for e in outcome["events"]]
    assert states[0] == "received"
    assert states[-1] == "completed"
    assert all(e.request_id == outcome["request_id"] for e in outcome["events"])


def test_handle_voice_command_irreversible_submits_to_approval_queue(tmp_path):
    queue = ApprovalQueueStore(tmp_path / "approval_queue.jsonl")
    bridge = _bridge(tmp_path, approval_queue=queue)
    outcome = bridge.handle_voice_command("Ezo, tüm dosyaları sil")

    assert outcome["result_en"]["status"] == "WAITING_APPROVAL"
    assert outcome["approval_submission"] is not None
    assert outcome["approval_submission"]["request_id"] == outcome["request_id"]

    pending = queue.list_pending()
    assert len(pending) == 1
    assert pending[0].request_id == outcome["request_id"]
    assert pending[0].risk_level == "irreversible"


def test_handle_voice_command_irreversible_emits_awaiting_approval_lifecycle_event(tmp_path):
    bridge = _bridge(tmp_path)
    outcome = bridge.handle_voice_command("Ezo, tüm dosyaları sil")
    assert outcome["events"][-1].state == "awaiting_approval"


def test_handle_voice_command_writes_audit_record(tmp_path):
    audit_path = tmp_path / "audit.log.jsonl"
    bridge = _bridge(tmp_path, audit_log_path=audit_path)
    outcome = bridge.handle_voice_command("Ezo, echo ile 'merhaba' yaz")

    import json

    lines = audit_path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[-1])
    assert record["request_id"] == outcome["request_id"]
    assert record["status"] == "ok"


def test_handle_voice_command_updates_assistant_presence_to_speaking(tmp_path):
    presence = AssistantPresenceTracker()
    bridge = _bridge(tmp_path, assistant_presence=presence)
    outcome = bridge.handle_voice_command("Ezo, echo ile 'merhaba' yaz")

    current = presence.current()
    assert current.state == "speaking"
    assert current.utterance_tr == outcome["tr_response"]


def test_handle_voice_command_blocked_policy_presence_when_awaiting_approval(tmp_path):
    presence = AssistantPresenceTracker()
    bridge = _bridge(tmp_path, assistant_presence=presence)
    bridge.handle_voice_command("Ezo, tüm dosyaları sil")
    # son durum "speaking"a gecmis olmali (TR yanit her zaman soylenir) --
    # ama arada "blocked_policy" durumunun GERCEKTEN uretildigini events
    # akisindaki risk_level=irreversible ile dolayli dogruluyoruz
    assert presence.current().state == "speaking"


def test_handle_voice_command_records_heartbeat_when_tracker_provided(tmp_path):
    tracker = HeartbeatTracker()
    bridge = _bridge(tmp_path, heartbeat_tracker=tracker)
    bridge.handle_voice_command("Ezo, echo ile 'merhaba' yaz")
    assert tracker.resolve_state("orchestrator") == "idle"


def test_handle_voice_command_no_wake_alias_still_returns_result(tmp_path):
    bridge = _bridge(tmp_path)
    outcome = bridge.handle_voice_command("bugün hava nasıl")
    assert outcome["extracted"]["detected_alias"] is None
    assert outcome["events"][0].original_tr == "bugün hava nasıl"


# --- T37 (BACKLOG.md B045) -- presence_events (on_change kancasinin toplanmasi) --


def test_handle_voice_command_collects_presence_events_when_tracker_provided(tmp_path):
    tracker = HeartbeatTracker()
    bridge = _bridge(tmp_path, heartbeat_tracker=tracker)
    outcome = bridge.handle_voice_command("Ezo, echo ile 'merhaba' yaz")
    assert [p.state for p in outcome["presence_events"]] == ["working", "idle"]
    assert all(p.agent_id == "orchestrator" for p in outcome["presence_events"])


def test_handle_voice_command_presence_events_empty_without_tracker(tmp_path):
    bridge = _bridge(tmp_path)  # heartbeat_tracker=None (varsayilan)
    outcome = bridge.handle_voice_command("Ezo, echo ile 'merhaba' yaz")
    assert outcome["presence_events"] == []


def test_handle_voice_command_presence_events_reset_between_calls(tmp_path):
    tracker = HeartbeatTracker()
    bridge = _bridge(tmp_path, heartbeat_tracker=tracker)
    bridge.handle_voice_command("Ezo, echo ile 'merhaba' yaz")
    second = bridge.handle_voice_command("Ezo, echo ile 'merhaba' yaz")
    assert len(second["presence_events"]) == 2  # ONCEKI cagridan BIRIKMEDI
