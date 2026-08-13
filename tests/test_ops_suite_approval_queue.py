"""PLAN.md T23 -- ops_suite.approval_queue testleri: submit/list_pending/decide
dongusu, replay-tabanli durum hesabi, bilinmeyen/zaten-karara-baglanmis
request_id korumalari -- gercek gecici JSONL dosyalariyla (tmp_path)."""

from __future__ import annotations

import json

import pytest

from ops_suite.approval_queue import (
    AlreadyDecidedError,
    ApprovalQueueStore,
    UnknownRequestIdError,
)


def _store(tmp_path) -> ApprovalQueueStore:
    return ApprovalQueueStore(tmp_path / "approval_queue.jsonl")


def test_submit_appends_a_record_and_creates_parent_dir(tmp_path):
    store = ApprovalQueueStore(tmp_path / "nested" / "approval_queue.jsonl")
    store.submit(request_id="r1", alias="ezo", task="RUN_DELETE_FILE", risk_level="irreversible", original_tr="sil")
    assert store.queue_path.exists()


def test_list_pending_empty_when_nothing_submitted(tmp_path):
    store = _store(tmp_path)
    assert store.list_pending() == []


def test_list_pending_includes_submitted_entry(tmp_path):
    store = _store(tmp_path)
    store.submit(request_id="r1", alias="ezo", task="RUN_DELETE_FILE", risk_level="irreversible", original_tr="sil")
    pending = store.list_pending()
    assert len(pending) == 1
    assert pending[0].request_id == "r1"
    assert pending[0].risk_level == "irreversible"


def test_list_pending_excludes_decided_entry(tmp_path):
    store = _store(tmp_path)
    store.submit(request_id="r1", alias="ezo", task="RUN_DELETE_FILE", risk_level="irreversible", original_tr="sil")
    store.decide("r1", "approved", actor="serkan")
    assert store.list_pending() == []


def test_list_pending_only_shows_undecided_among_multiple(tmp_path):
    store = _store(tmp_path)
    store.submit(request_id="r1", alias="ezo", task="A", risk_level="high", original_tr="x")
    store.submit(request_id="r2", alias="ezo", task="B", risk_level="irreversible", original_tr="y")
    store.decide("r1", "rejected", actor="serkan")
    pending = store.list_pending()
    assert [p.request_id for p in pending] == ["r2"]


def test_decide_unknown_request_id_raises():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        store = ApprovalQueueStore(Path(d) / "q.jsonl")
        with pytest.raises(UnknownRequestIdError):
            store.decide("never-submitted", "approved", actor="serkan")


def test_decide_twice_raises_already_decided(tmp_path):
    store = _store(tmp_path)
    store.submit(request_id="r1", alias="ezo", task="A", risk_level="high", original_tr="x")
    store.decide("r1", "approved", actor="serkan")
    with pytest.raises(AlreadyDecidedError):
        store.decide("r1", "rejected", actor="serkan")


def test_decide_invalid_decision_value_raises(tmp_path):
    store = _store(tmp_path)
    store.submit(request_id="r1", alias="ezo", task="A", risk_level="high", original_tr="x")
    with pytest.raises(ValueError, match="gecersiz decision"):
        store.decide("r1", "maybe", actor="serkan")


def test_decide_returns_record_with_actor_and_note(tmp_path):
    store = _store(tmp_path)
    store.submit(request_id="r1", alias="ezo", task="A", risk_level="high", original_tr="x")
    record = store.decide("r1", "approved", actor="serkan", note="onaylandi, sinirli kapsam")
    assert record["actor"] == "serkan"
    assert record["note"] == "onaylandi, sinirli kapsam"
    assert record["decision"] == "approved"


def test_store_is_append_only_jsonl_on_disk(tmp_path):
    store = _store(tmp_path)
    store.submit(request_id="r1", alias="ezo", task="A", risk_level="high", original_tr="x")
    store.decide("r1", "approved", actor="serkan")
    lines = store.queue_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["record_type"] == "SUBMITTED"
    assert json.loads(lines[1])["record_type"] == "DECIDED"


def test_reopening_store_reads_existing_pending_from_disk(tmp_path):
    path = tmp_path / "approval_queue.jsonl"
    store_a = ApprovalQueueStore(path)
    store_a.submit(request_id="r1", alias="ezo", task="A", risk_level="high", original_tr="x")

    store_b = ApprovalQueueStore(path)  # ayni dosyayi yeniden acan yeni bir instance
    assert [p.request_id for p in store_b.list_pending()] == ["r1"]


def test_submit_defaults_details_to_empty_dict(tmp_path):
    store = _store(tmp_path)
    store.submit(request_id="r1", alias="ezo", task="A", risk_level="high", original_tr="x")
    pending = store.list_pending()
    assert pending[0].details == {}
