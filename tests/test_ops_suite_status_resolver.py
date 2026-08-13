"""PLAN.md T22 -- ops_suite.status_resolver testleri: bilinen-canli
ajanlarin heartbeat'e gore cozumlenmesi, HENUZ kodu olmayan ajanlarin
HER ZAMAN offline/not_implemented olarak isaretlendigini (asla fabrike
edilmis bir 'idle' verilmedigini) dogrular."""

from __future__ import annotations

from ops_suite.heartbeat import HeartbeatTracker
from ops_suite.status_resolver import (
    KNOWN_LIVE_AGENTS,
    NOT_IMPLEMENTED_AGENTS,
    AgentStatusResolver,
)


def test_resolve_all_includes_every_known_live_agent():
    resolver = AgentStatusResolver(HeartbeatTracker())
    presences = {p.agent_id: p for p in resolver.resolve_all()}
    for agent_id in KNOWN_LIVE_AGENTS:
        assert agent_id in presences


def test_resolve_all_includes_every_not_implemented_agent_as_offline():
    resolver = AgentStatusResolver(HeartbeatTracker())
    presences = {p.agent_id: p for p in resolver.resolve_all()}
    for agent_id in NOT_IMPLEMENTED_AGENTS:
        assert presences[agent_id].state == "offline"
        assert presences[agent_id].detail.startswith("not_implemented")


def test_resolve_all_never_reports_not_implemented_agent_as_idle_or_working():
    """Kritik durustluk kontrolu: gorev kisiti 'dont fabricate test
    results' -- kodu olmayan bir ajan ASLA canli gorunmemelidir."""
    resolver = AgentStatusResolver(HeartbeatTracker())
    presences = resolver.resolve_all()
    for p in presences:
        if p.agent_id in NOT_IMPLEMENTED_AGENTS:
            assert p.state not in ("idle", "working", "blocked", "awaiting_approval")


def test_resolve_all_known_live_agent_without_heartbeat_is_offline_with_reason():
    resolver = AgentStatusResolver(HeartbeatTracker())
    presences = {p.agent_id: p for p in resolver.resolve_all()}
    assert presences["orchestrator"].state == "offline"
    assert "heartbeat" in presences["orchestrator"].detail


def test_resolve_all_known_live_agent_with_heartbeat_reflects_declared_state():
    tracker = HeartbeatTracker()
    tracker.record("orchestrator", declared_state="working", display_name="Orchestrator")
    resolver = AgentStatusResolver(tracker)
    presences = {p.agent_id: p for p in resolver.resolve_all()}
    assert presences["orchestrator"].state == "working"


def test_resolve_all_unknown_agent_with_heartbeat_still_appears():
    """Ne KNOWN_LIVE_AGENTS ne NOT_IMPLEMENTED_AGENTS'te olan (ornegin
    ileride eklenecek yeni) bir ajan heartbeat atarsa, SESSIZCE
    KAYBOLMAMALIDIR."""
    tracker = HeartbeatTracker()
    tracker.record("brand_new_agent", declared_state="working")
    resolver = AgentStatusResolver(tracker)
    presences = {p.agent_id: p for p in resolver.resolve_all()}
    assert presences["brand_new_agent"].state == "working"


def test_resolve_all_total_count_matches_known_plus_not_implemented():
    resolver = AgentStatusResolver(HeartbeatTracker())
    presences = resolver.resolve_all()
    assert len(presences) == len(KNOWN_LIVE_AGENTS) + len(NOT_IMPLEMENTED_AGENTS)
