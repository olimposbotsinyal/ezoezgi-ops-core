"""PLAN.md T22 -- ops_suite.audit_tail testleri: yeni-satir tarama (offset
ilerlemesi), tam-dosya okuma, eksik dosya/bozuk satir toleransi -- gercek
gecici dosyalarla (tmp_path), AuditLogger'in kendisi degistirilmeden."""

from __future__ import annotations

import json

from ops_suite.audit_tail import AuditTailReader


def _write_lines(path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")


def test_read_new_records_returns_empty_when_file_missing(tmp_path):
    reader = AuditTailReader(tmp_path / "does_not_exist.jsonl")
    assert reader.read_new_records() == []


def test_read_new_records_returns_all_lines_on_first_call(tmp_path):
    log_path = tmp_path / "audit.log.jsonl"
    _write_lines(log_path, [{"task": "A"}, {"task": "B"}])
    reader = AuditTailReader(log_path)
    records = reader.read_new_records()
    assert [r["task"] for r in records] == ["A", "B"]


def test_read_new_records_only_returns_lines_added_since_last_call(tmp_path):
    log_path = tmp_path / "audit.log.jsonl"
    _write_lines(log_path, [{"task": "A"}])
    reader = AuditTailReader(log_path)
    reader.read_new_records()  # ilk cagri -- offset'i ilerletir

    _write_lines(log_path, [{"task": "B"}])
    second = reader.read_new_records()
    assert [r["task"] for r in second] == ["B"]


def test_read_new_records_second_call_empty_when_nothing_new(tmp_path):
    log_path = tmp_path / "audit.log.jsonl"
    _write_lines(log_path, [{"task": "A"}])
    reader = AuditTailReader(log_path)
    reader.read_new_records()
    assert reader.read_new_records() == []


def test_read_new_records_skips_malformed_lines_without_crashing(tmp_path):
    log_path = tmp_path / "audit.log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text('{"task": "A"}\n{not valid json\n{"task": "B"}\n', encoding="utf-8")
    reader = AuditTailReader(log_path)
    records = reader.read_new_records()
    assert [r["task"] for r in records] == ["A", "B"]


def test_read_all_records_does_not_advance_offset(tmp_path):
    log_path = tmp_path / "audit.log.jsonl"
    _write_lines(log_path, [{"task": "A"}])
    reader = AuditTailReader(log_path)
    reader.read_all_records()
    # offset hala 0 olmali -- read_new_records TUM satirlari YENIDEN donmeli
    assert [r["task"] for r in reader.read_new_records()] == ["A"]


def test_read_all_records_empty_when_file_missing(tmp_path):
    reader = AuditTailReader(tmp_path / "missing.jsonl")
    assert reader.read_all_records() == []


def test_reset_offset_allows_rereading_from_start(tmp_path):
    log_path = tmp_path / "audit.log.jsonl"
    _write_lines(log_path, [{"task": "A"}])
    reader = AuditTailReader(log_path)
    reader.read_new_records()
    reader.reset_offset()
    assert [r["task"] for r in reader.read_new_records()] == ["A"]
