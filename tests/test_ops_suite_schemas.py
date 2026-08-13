"""PLAN.md T21 -- ops_suite.schemas testleri: gecerli/gecersiz durum
degerleri, to_dict serilestirmesi, degismezlik (frozen dataclass)."""

from __future__ import annotations

import pytest

from ops_suite.schemas import (
    AGENT_STATES,
    ASSISTANT_STATES,
    TASK_LIFECYCLE_STATES,
    AgentPresence,
    ApprovalQueueEntry,
    AssistantPresenceEvent,
    TaskLifecycleEvent,
)


def test_agent_presence_accepts_valid_state():
    for state in AGENT_STATES:
        AgentPresence(agent_id="a", display_name="A", state=state)


def test_agent_presence_rejects_invalid_state():
    with pytest.raises(ValueError, match="gecersiz state"):
        AgentPresence(agent_id="a", display_name="A", state="dancing")


def test_agent_presence_to_dict_includes_all_fields():
    presence = AgentPresence(agent_id="a", display_name="A", state="idle", detail="x")
    d = presence.to_dict()
    assert d["agent_id"] == "a"
    assert d["state"] == "idle"
    assert d["detail"] == "x"


def test_agent_presence_is_frozen():
    presence = AgentPresence(agent_id="a", display_name="A", state="idle")
    with pytest.raises(Exception):
        presence.state = "working"  # type: ignore[misc]


def test_task_lifecycle_event_accepts_all_valid_states():
    for state in TASK_LIFECYCLE_STATES:
        TaskLifecycleEvent(request_id="r1", state=state)


def test_task_lifecycle_event_rejects_invalid_state():
    with pytest.raises(ValueError, match="gecersiz state"):
        TaskLifecycleEvent(request_id="r1", state="teleporting")


def test_task_lifecycle_event_to_dict():
    event = TaskLifecycleEvent(request_id="r1", state="received", task_en="RUN_ECHO")
    d = event.to_dict()
    assert d["request_id"] == "r1"
    assert d["task_en"] == "RUN_ECHO"


def test_assistant_presence_event_accepts_all_valid_states():
    for state in ASSISTANT_STATES:
        AssistantPresenceEvent(assistant_id="ezoezgi-core", state=state)


def test_assistant_presence_event_rejects_invalid_state():
    with pytest.raises(ValueError, match="gecersiz state"):
        AssistantPresenceEvent(assistant_id="ezoezgi-core", state="sleeping")


def test_assistant_presence_event_to_dict_optional_fields_default_none():
    event = AssistantPresenceEvent(assistant_id="ezoezgi-core", state="idle")
    d = event.to_dict()
    assert d["utterance_tr"] is None
    assert d["related_request_id"] is None


def test_approval_queue_entry_to_dict_defaults_empty_details():
    entry = ApprovalQueueEntry(
        request_id="r1", alias="ezo", task="RUN_DELETE_FILE", risk_level="irreversible",
        original_tr="tum dosyalari sil", submitted_at="2026-08-14T00:00:00+00:00",
    )
    d = entry.to_dict()
    assert d["details"] == {}
    assert d["risk_level"] == "irreversible"
