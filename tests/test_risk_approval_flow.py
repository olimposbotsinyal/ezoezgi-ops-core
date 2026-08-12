"""T13 -- risk_engine.py + approval_stub.py + orchestrator entegrasyonu testleri."""

from __future__ import annotations

import json

import pytest

from approval_stub import STATUS_AUTO_ALLOWED, STATUS_WAITING_APPROVAL, check_approval
from audit_logger import AuditLogger
from orchestrator import STATUS_OK, Orchestrator
from registry import build_default_registry
from risk_engine import RiskEngine, RiskPolicyError

VALID_POLICY_YAML = """
tasks:
  RUN_ECHO: low
  RUN_SHELL_SAFE: medium
  RUN_DELETE_FILE: irreversible
default_risk_level: medium
"""


# --- approval_stub birim testleri -----------------------------------------


@pytest.mark.parametrize(
    "risk_level, expected",
    [
        ("low", STATUS_AUTO_ALLOWED),
        ("medium", STATUS_AUTO_ALLOWED),
        ("high", STATUS_WAITING_APPROVAL),
        ("irreversible", STATUS_WAITING_APPROVAL),
    ],
)
def test_check_approval(risk_level, expected):
    assert check_approval(risk_level) == expected


# --- risk_engine birim testleri --------------------------------------------


def test_risk_engine_returns_configured_level(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(VALID_POLICY_YAML, encoding="utf-8")
    engine = RiskEngine(policy_path)

    assert engine.get_risk("RUN_ECHO") == "low"
    assert engine.get_risk("RUN_DELETE_FILE") == "irreversible"


def test_risk_engine_falls_back_to_default_for_unknown_task(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(VALID_POLICY_YAML, encoding="utf-8")
    engine = RiskEngine(policy_path)

    assert engine.get_risk("SOME_UNKNOWN_TASK") == "medium"


def test_risk_engine_rejects_invalid_risk_level(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text("tasks:\n  RUN_ECHO: not-a-real-level\n", encoding="utf-8")

    with pytest.raises(RiskPolicyError):
        RiskEngine(policy_path)


def test_risk_engine_missing_file_raises(tmp_path):
    with pytest.raises(RiskPolicyError):
        RiskEngine(tmp_path / "does-not-exist.yaml")


# --- orchestrator + risk + approval entegrasyonu ---------------------------


def test_low_risk_task_runs_directly_without_approval():
    orchestrator = Orchestrator(build_default_registry())

    result_en = orchestrator.handle_task(
        {
            "task_en": "RUN_ECHO",
            "original_tr": "Ezo, echo ile merhaba yaz",
            "detected_alias": "ezo",
        }
    )

    assert result_en["status"] == STATUS_OK
    assert result_en["risk_level"] == "low"


def test_irreversible_task_is_blocked_and_waits_for_approval():
    orchestrator = Orchestrator(build_default_registry())

    result_en = orchestrator.handle_task(
        {
            "task_en": "RUN_DELETE_FILE",
            "original_tr": "tum dosyalari sil",
            "detected_alias": "ezo",
        }
    )

    assert result_en["status"] == STATUS_WAITING_APPROVAL
    assert result_en["risk_level"] == "irreversible"


def test_audit_log_records_risk_and_status_for_waiting_approval(tmp_path):
    orchestrator = Orchestrator(build_default_registry())
    audit_logger = AuditLogger(tmp_path / "audit.log.jsonl")

    extracted = {
        "task_en": "RUN_DELETE_FILE",
        "original_tr": "tum dosyalari sil",
        "detected_alias": "ezo",
        "confidence": 0.6,
    }
    result_en = orchestrator.handle_task(extracted)
    audit_logger.log(
        alias=extracted["detected_alias"],
        task=extracted["task_en"],
        status=result_en["status"],
        risk_level=result_en["risk_level"],
        details={"result_en": result_en},
    )

    record = json.loads((tmp_path / "audit.log.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert record["status"] == "WAITING_APPROVAL"
    assert record["risk_level"] == "irreversible"
    assert record["task"] == "RUN_DELETE_FILE"


def test_audit_log_records_risk_and_status_for_auto_allowed(tmp_path):
    orchestrator = Orchestrator(build_default_registry())
    audit_logger = AuditLogger(tmp_path / "audit.log.jsonl")

    extracted = {
        "task_en": "RUN_ECHO",
        "original_tr": "Ezo, echo ile merhaba yaz",
        "detected_alias": "ezo",
        "confidence": 0.6,
    }
    result_en = orchestrator.handle_task(extracted)
    audit_logger.log(
        alias=extracted["detected_alias"],
        task=extracted["task_en"],
        status=result_en["status"],
        risk_level=result_en["risk_level"],
        details={"result_en": result_en},
    )

    record = json.loads((tmp_path / "audit.log.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert record["status"] == "ok"
    assert record["risk_level"] == "low"
