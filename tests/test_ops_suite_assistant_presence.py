"""PLAN.md T24 -- ops_suite.assistant_presence testleri."""

from __future__ import annotations

import pytest

from ops_suite.assistant_presence import AssistantPresenceTracker


def test_current_defaults_to_idle_when_never_set():
    tracker = AssistantPresenceTracker()
    current = tracker.current()
    assert current.state == "idle"
    assert current.timestamp != ""


def test_set_state_updates_current():
    tracker = AssistantPresenceTracker()
    tracker.set_state("listening")
    assert tracker.current().state == "listening"


def test_set_state_records_utterance_and_related_request_id():
    tracker = AssistantPresenceTracker()
    event = tracker.set_state("speaking", utterance_tr="Merhaba yazdirildi.", related_request_id="r1")
    assert event.utterance_tr == "Merhaba yazdirildi."
    assert event.related_request_id == "r1"
    assert tracker.current().related_request_id == "r1"


def test_set_state_rejects_invalid_state_and_leaves_current_unchanged():
    tracker = AssistantPresenceTracker()
    tracker.set_state("thinking")
    with pytest.raises(ValueError, match="gecersiz state"):
        tracker.set_state("napping")
    assert tracker.current().state == "thinking"


def test_uses_configured_assistant_id():
    tracker = AssistantPresenceTracker(assistant_id="custom-id")
    assert tracker.current().assistant_id == "custom-id"


def test_uses_injected_clock_for_deterministic_timestamp():
    tracker = AssistantPresenceTracker(clock=lambda: 0.0)
    event = tracker.set_state("idle")
    assert event.timestamp.startswith("1970-01-01")
