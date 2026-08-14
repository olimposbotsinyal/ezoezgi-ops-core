"""PLAN.md T22 -- ops_suite.heartbeat testleri: kayit/cozumleme, zaman
asimi (offline dusme), deterministik sahte saat enjeksiyonu ile (gercek
uyku YOK)."""

from __future__ import annotations

from ops_suite.heartbeat import HeartbeatTracker


class _FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def test_resolve_state_offline_when_never_recorded():
    tracker = HeartbeatTracker()
    assert tracker.resolve_state("ghost_agent") == "offline"


def test_resolve_state_idle_by_default_after_record():
    clock = _FakeClock()
    tracker = HeartbeatTracker(clock=clock)
    tracker.record("a1")
    assert tracker.resolve_state("a1") == "idle"


def test_resolve_state_uses_declared_state():
    clock = _FakeClock()
    tracker = HeartbeatTracker(clock=clock)
    tracker.record("a1", declared_state="working")
    assert tracker.resolve_state("a1") == "working"


def test_resolve_state_offline_after_timeout():
    clock = _FakeClock()
    tracker = HeartbeatTracker(timeout_seconds=10.0, clock=clock)
    tracker.record("a1", declared_state="working")
    clock.now += 11.0
    assert tracker.resolve_state("a1") == "offline"


def test_resolve_state_still_alive_just_under_timeout():
    clock = _FakeClock()
    tracker = HeartbeatTracker(timeout_seconds=10.0, clock=clock)
    tracker.record("a1", declared_state="working")
    clock.now += 9.0
    assert tracker.resolve_state("a1") == "working"


def test_record_preserves_previous_declared_state_when_not_repeated():
    clock = _FakeClock()
    tracker = HeartbeatTracker(clock=clock)
    tracker.record("a1", declared_state="working")
    tracker.record("a1")  # yalnizca heartbeat -- state tekrar verilmedi
    assert tracker.resolve_state("a1") == "working"


def test_snapshot_returns_agent_presence_objects():
    clock = _FakeClock()
    tracker = HeartbeatTracker(clock=clock)
    tracker.record("a1", declared_state="working", display_name="Agent One", last_task_id="req-1")
    snapshot = tracker.snapshot()
    assert len(snapshot) == 1
    presence = snapshot[0]
    assert presence.agent_id == "a1"
    assert presence.display_name == "Agent One"
    assert presence.state == "working"
    assert presence.last_task_id == "req-1"
    assert presence.last_heartbeat_ts is not None


def test_snapshot_reflects_offline_after_timeout():
    clock = _FakeClock()
    tracker = HeartbeatTracker(timeout_seconds=5.0, clock=clock)
    tracker.record("a1", declared_state="working")
    clock.now += 6.0
    snapshot = tracker.snapshot()
    assert snapshot[0].state == "offline"


def test_snapshot_empty_when_no_agents_recorded():
    tracker = HeartbeatTracker()
    assert tracker.snapshot() == []


def test_record_with_explicit_ts_overrides_clock():
    clock = _FakeClock()
    tracker = HeartbeatTracker(clock=clock)
    tracker.record("a1", declared_state="working", ts=500.0)
    snapshot = tracker.snapshot()
    assert snapshot[0].last_heartbeat_ts.startswith("1970-01-01")


# --- T37 (BACKLOG.md B045) -- on_change kancasi -----------------------------


def test_on_change_defaults_to_none_and_does_not_fire():
    tracker = HeartbeatTracker()
    assert tracker.on_change is None
    tracker.record("a1", declared_state="working")  # exception FIRLATMAMALI


def test_on_change_fires_synchronously_with_current_presence():
    received = []
    tracker = HeartbeatTracker(on_change=received.append)
    tracker.record("a1", declared_state="working", display_name="Agent One")
    assert len(received) == 1
    assert received[0].agent_id == "a1"
    assert received[0].state == "working"


def test_on_change_fires_once_per_record_call_with_each_states_snapshot():
    received = []
    clock = _FakeClock()
    tracker = HeartbeatTracker(clock=clock, on_change=received.append)
    tracker.record("a1", declared_state="working")
    tracker.record("a1", declared_state="idle")
    assert [p.state for p in received] == ["working", "idle"]


def test_has_record_false_for_unseen_agent():
    tracker = HeartbeatTracker()
    assert tracker.has_record("ghost_agent") is False


def test_has_record_true_after_record_even_if_offline_by_timeout():
    clock = _FakeClock()
    tracker = HeartbeatTracker(timeout_seconds=5.0, clock=clock)
    tracker.record("a1", declared_state="working")
    clock.now += 100.0
    assert tracker.resolve_state("a1") == "offline"
    assert tracker.has_record("a1") is True  # T39 -- "kayit var mi" != "canli mi"


def test_on_change_can_be_reassigned_after_construction():
    """`create_app()`/`VoiceBridge`'in DI ile verilmis bir tracker'a bile
    SONRADAN kanca baglayabilmesi icin -- `on_change` duz bir ozelliktir,
    yalnizca constructor parametresi DEGIL."""
    tracker = HeartbeatTracker()
    received = []
    tracker.on_change = received.append
    tracker.record("a1", declared_state="working")
    assert len(received) == 1
