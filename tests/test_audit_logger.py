"""T17 -- audit_logger.py testleri."""

from __future__ import annotations

import json

from audit_logger import REQUIRED_RECORD_FIELDS, AuditLogger


def test_log_writes_single_valid_jsonl_line(tmp_path):
    log_path = tmp_path / "audit" / "audit.log.jsonl"
    logger = AuditLogger(log_path)

    logger.log(
        alias="ezo",
        task="RUN_ECHO",
        status="ok",
        risk_level="low",
        details={"value": "Merhaba"},
    )

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1

    record = json.loads(lines[0])
    for field in REQUIRED_RECORD_FIELDS:
        assert field in record

    assert record["alias"] == "ezo"
    assert record["task"] == "RUN_ECHO"
    assert record["status"] == "ok"
    assert record["risk_level"] == "low"
    assert record["details"] == {"value": "Merhaba"}
    assert record["request_id"]  # otomatik uretilmis, bos olmamali


def test_log_is_append_only_across_multiple_calls(tmp_path):
    log_path = tmp_path / "audit.log.jsonl"
    logger = AuditLogger(log_path)

    logger.log(alias="ezo", task="RUN_ECHO", status="ok")
    logger.log(alias="ezgi", task="SHOW_DAILY_SPENDING", status="ok")

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    second = json.loads(lines[1])
    assert first["task"] == "RUN_ECHO"
    assert second["task"] == "SHOW_DAILY_SPENDING"
    # ilk satir ikinci yazmadan etkilenmemis (append-only).
    assert first["alias"] == "ezo"


def test_log_creates_parent_directory_if_missing(tmp_path):
    log_path = tmp_path / "nested" / "does" / "not" / "exist" / "audit.log.jsonl"

    logger = AuditLogger(log_path)
    logger.log(alias=None, task="UNKNOWN", status="no_handler")

    assert log_path.exists()


def test_log_returns_the_written_record(tmp_path):
    logger = AuditLogger(tmp_path / "audit.log.jsonl")

    record = logger.log(alias="ezo", task="RUN_ECHO", status="ok", request_id="req-123")

    assert record["request_id"] == "req-123"
    assert record["alias"] == "ezo"
