"""`generate_threshold_proposals.py` testleri -- uctan uca proposal
uretimi (gercek JSONL verisiyle), yalnizca UYGULANABILIR (CALIBRATED +
mevcuttan farkli) alertler icin proposal uretildigi, sema-uyumluluğu."""

from __future__ import annotations

import json
import time
from pathlib import Path

from generate_threshold_proposals import generate_proposals, write_proposal
from model_gateway.metrics_sink import JsonlAppendSink, MetricEvent
from threshold_governance_core import compute_checksum, validate_against_schema

SCHEMA_PATH = Path("infra/monitoring/governance/threshold_proposal_schema_v1.json")


def _write_events(jsonl_path, *, requests=300, null_intent=15):
    sink = JsonlAppendSink(jsonl_path, max_mb=50, retention_days=30)
    now = time.time()
    for _ in range(requests):
        sink.write(
            MetricEvent(ts=now, metric_type="counter", name="model_gateway_requests_total",
                        labels={"provider": "ollama", "result": "success"}, value=1.0)
        )
    for _ in range(null_intent):
        sink.write(
            MetricEvent(ts=now, metric_type="counter", name="model_gateway_null_intent_total",
                        labels={"terminal_reason": "x"}, value=1.0)
        )


def test_generate_proposals_produces_schema_valid_proposal_for_calibrated_alert(tmp_path):
    jsonl_path = tmp_path / "m.jsonl"
    _write_events(jsonl_path, requests=300, null_intent=15)  # oran 0.05, tum pencerelerde yeterli+tutarli -> HIGH

    proposals = generate_proposals(
        jsonl_path=jsonl_path, repo_root=Path("d:/Projects/ezoezgi-ops"), calibration_out_dir=tmp_path / "calib"
    )

    null_intent_proposals = [p for p in proposals if p["alert_name"] == "HIGH_NULL_INTENT_RATE"]
    assert len(null_intent_proposals) == 1
    proposal = null_intent_proposals[0]

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert validate_against_schema(proposal, schema) == []
    assert proposal["checksum"] == compute_checksum(proposal)
    assert proposal["confidence"] == "HIGH"
    assert proposal["proposed_values"]["warn"] == 0.1


def test_generate_proposals_empty_when_no_traffic(tmp_path):
    jsonl_path = tmp_path / "does_not_exist.jsonl"
    proposals = generate_proposals(
        jsonl_path=jsonl_path, repo_root=Path("d:/Projects/ezoezgi-ops"), calibration_out_dir=tmp_path / "calib"
    )
    assert proposals == []


def test_generate_proposals_omits_alerts_without_env_var_mapping(tmp_path):
    """PRIMARY_RESTRICTED_PERSISTENT/PREFLIGHT_UNKNOWN_PERSISTENT icin
    ozel bir esik ortam degiskeni yok -- ne kadar veri olursa olsun
    proposal uretilmemeli."""
    jsonl_path = tmp_path / "m.jsonl"
    _write_events(jsonl_path, requests=300, null_intent=1)  # dusuk oran -- HIGH_NULL_INTENT_RATE degisikligi olmayabilir

    proposals = generate_proposals(
        jsonl_path=jsonl_path, repo_root=Path("d:/Projects/ezoezgi-ops"), calibration_out_dir=tmp_path / "calib"
    )
    alert_names = {p["alert_name"] for p in proposals}
    assert "PRIMARY_RESTRICTED_PERSISTENT" not in alert_names
    assert "PREFLIGHT_UNKNOWN_PERSISTENT" not in alert_names


def test_write_proposal_creates_expected_directory_structure(tmp_path):
    jsonl_path = tmp_path / "m.jsonl"
    _write_events(jsonl_path, requests=300, null_intent=15)
    proposals = generate_proposals(
        jsonl_path=jsonl_path, repo_root=Path("d:/Projects/ezoezgi-ops"), calibration_out_dir=tmp_path / "calib"
    )
    proposal = proposals[0]

    out_path = write_proposal(proposal, tmp_path / "proposals_base")

    assert out_path.name == "proposal.json"
    assert out_path.parent.name == proposal["proposal_id"]
    reloaded = json.loads(out_path.read_text(encoding="utf-8"))
    assert reloaded == proposal
