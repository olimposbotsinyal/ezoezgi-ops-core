"""PLAN.md T39, BACKLOG.md B041 -- ops_suite.presence_store testleri:
append/load_latest dongusu, "last write wins" cakisma cozumu,
bozuk/eksik dosya toleransi -- gercek gecici JSONL dosyalariyla (tmp_path)."""

from __future__ import annotations

import json

from ops_suite.presence_store import PresenceStore
from ops_suite.schemas import AgentPresence


def _presence(agent_id="orchestrator", state="working", **overrides):
    defaults = dict(
        agent_id=agent_id, display_name="Orchestrator", state=state,
        last_heartbeat_ts="2026-08-14T00:00:00+00:00", last_task_id=None, detail="", updated_at="",
    )
    defaults.update(overrides)
    return AgentPresence(**defaults)


def test_append_creates_parent_dir_and_writes_a_line(tmp_path):
    store = PresenceStore(tmp_path / "nested" / "agent_presence.jsonl")
    store.append(_presence())
    assert store.log_path.exists()
    lines = store.log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["agent_id"] == "orchestrator"


def test_load_latest_empty_when_file_missing(tmp_path):
    store = PresenceStore(tmp_path / "agent_presence.jsonl")
    assert store.load_latest() == {}


def test_load_latest_returns_last_write_per_agent(tmp_path):
    store = PresenceStore(tmp_path / "agent_presence.jsonl")
    store.append(_presence(state="working"))
    store.append(_presence(state="idle"))
    latest = store.load_latest()
    assert len(latest) == 1
    assert latest["orchestrator"]["state"] == "idle"


def test_load_latest_tracks_multiple_agents_independently(tmp_path):
    store = PresenceStore(tmp_path / "agent_presence.jsonl")
    store.append(_presence(agent_id="orchestrator", state="idle"))
    store.append(_presence(agent_id="bridge_agent", state="working", display_name="Bridge Agent"))
    latest = store.load_latest()
    assert set(latest.keys()) == {"orchestrator", "bridge_agent"}
    assert latest["bridge_agent"]["state"] == "working"


def test_load_latest_tolerates_malformed_lines(tmp_path):
    store = PresenceStore(tmp_path / "agent_presence.jsonl")
    store.append(_presence())
    with store.log_path.open("a", encoding="utf-8") as f:
        f.write("{bozuk json\n")
        f.write("\n")  # bos satir
    latest = store.load_latest()
    assert "orchestrator" in latest


def test_append_is_append_only_on_disk(tmp_path):
    store = PresenceStore(tmp_path / "agent_presence.jsonl")
    store.append(_presence(state="working"))
    store.append(_presence(state="idle"))
    lines = store.log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["state"] == "working"
    assert json.loads(lines[1])["state"] == "idle"


def test_reopening_store_reads_existing_data_from_disk(tmp_path):
    path = tmp_path / "agent_presence.jsonl"
    store_a = PresenceStore(path)
    store_a.append(_presence(state="working"))

    store_b = PresenceStore(path)  # ayni dosyayi yeniden acan yeni bir instance
    assert store_b.load_latest()["orchestrator"]["state"] == "working"
