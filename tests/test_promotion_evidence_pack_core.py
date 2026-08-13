"""`promotion_evidence_pack_core.py` testleri -- runpack bundling saf
mantigi: auto-rollback ON/OFF sayimi, terfi gorunumu (outlook)
haritalamasi, `runpack_index.json`/`runpack_summary.md` uretimi.
Tamamen deterministik -- gercek bir PowerShell sureci veya ag cagrisi
GEREKMEZ."""

from __future__ import annotations

import json

from promotion_evidence_pack_core import (
    OUTLOOK_UNKNOWN,
    build_runpack_index,
    collect_auto_rollback_evidence,
    count_auto_rollback_evidence,
    promotion_readiness_outlook,
    render_runpack_summary_md,
    write_runpack_bundle,
)


def test_promotion_readiness_outlook_zero_is_promote_ready():
    assert promotion_readiness_outlook(0) == "PROMOTE-ready"


def test_promotion_readiness_outlook_one_is_extend_likely():
    assert promotion_readiness_outlook(1) == "EXTEND-likely"


def test_promotion_readiness_outlook_two_is_reject_risk():
    assert promotion_readiness_outlook(2) == "REJECT-risk"


def test_promotion_readiness_outlook_none_is_unknown():
    assert promotion_readiness_outlook(None) == OUTLOOK_UNKNOWN


def test_promotion_readiness_outlook_unexpected_code_is_unknown():
    assert promotion_readiness_outlook(99) == OUTLOOK_UNKNOWN


def test_count_auto_rollback_evidence_counts_triggered_as_on():
    payloads = [
        {"auto_rollback": {"triggered": True}},
        {"auto_rollback": {"triggered": False}},
        {},
    ]
    on, off = count_auto_rollback_evidence(payloads)
    assert on == 1
    assert off == 2


def test_count_auto_rollback_evidence_empty_list():
    assert count_auto_rollback_evidence([]) == (0, 0)


def test_count_auto_rollback_evidence_malformed_auto_rollback_field_counts_as_off():
    payloads = [{"auto_rollback": "not a dict"}]
    on, off = count_auto_rollback_evidence(payloads)
    assert on == 0
    assert off == 1


def test_collect_auto_rollback_evidence_reads_real_files(tmp_path):
    (tmp_path / "reports/v1_2_pilot_A/sub").mkdir(parents=True)
    (tmp_path / "reports/v1_2_pilot_A/sub/apply_report.json").write_text(
        json.dumps({"auto_rollback": {"triggered": True}}), encoding="utf-8"
    )
    (tmp_path / "reports/threshold_apply_B").mkdir(parents=True)
    (tmp_path / "reports/threshold_apply_B/apply_report.json").write_text(
        json.dumps({"auto_rollback": {"triggered": False}}), encoding="utf-8"
    )

    on, off, total = collect_auto_rollback_evidence(tmp_path)
    assert on == 1
    assert off == 1
    assert total == 2


def test_collect_auto_rollback_evidence_no_files_found(tmp_path):
    on, off, total = collect_auto_rollback_evidence(tmp_path)
    assert (on, off, total) == (0, 0, 0)


def test_collect_auto_rollback_evidence_skips_malformed_json(tmp_path):
    """Bozuk JSON dosyasi ON/OFF SAYIMINA KATILMAZ (parse edilemedi),
    ama yine de TARANAN dosyalar arasinda SAYILIR (`total`) -- dosyanin
    varligi/bulunmasi ile GECERLI icerigi barindirmasi AYRI seylerdir."""
    (tmp_path / "reports/threshold_apply_X").mkdir(parents=True)
    (tmp_path / "reports/threshold_apply_X/apply_report.json").write_text("{not valid json", encoding="utf-8")

    on, off, total = collect_auto_rollback_evidence(tmp_path)
    assert (on, off, total) == (0, 0, 1)


def test_build_runpack_index_includes_outlook_and_steps():
    steps = [{"step": "chain_trial", "exit_code": 0, "status": "OK", "evidence_path": "x"}]
    index = build_runpack_index(steps, generated_at="2026-08-13T00:00:00+00:00", incident_id="OPS-1", evaluator_exit_code=0)
    assert index["promotion_readiness_outlook"] == "PROMOTE-ready"
    assert index["steps"] == steps
    assert index["incident_id"] == "OPS-1"


def test_render_runpack_summary_md_includes_outlook_and_table():
    index = build_runpack_index(
        [{"step": "x", "exit_code": 1, "status": "PARTIAL", "evidence_path": None}],
        generated_at="2026-08-13T00:00:00+00:00", incident_id="OPS-1", evaluator_exit_code=1,
    )
    md = render_runpack_summary_md(index)
    assert "EXTEND-likely" in md
    assert "| x | PARTIAL | 1 | - |" in md


def test_render_runpack_summary_md_mentions_no_state_mutation():
    index = build_runpack_index([], generated_at="t", incident_id=None, evaluator_exit_code=None)
    md = render_runpack_summary_md(index)
    assert "KALICI DURUMU DEGISTIRMEZ" in md
    assert "GOZLEMSELDIR" in md


def test_write_runpack_bundle_creates_index_and_summary(tmp_path):
    index = build_runpack_index(
        [{"step": "x", "exit_code": 0, "status": "OK", "evidence_path": "p"}],
        generated_at="t", incident_id="OPS-1", evaluator_exit_code=0,
    )
    paths = write_runpack_bundle(index, tmp_path / "out")
    assert paths["index"].exists()
    assert paths["summary"].exists()
    payload = json.loads(paths["index"].read_text(encoding="utf-8"))
    assert payload["promotion_readiness_outlook"] == "PROMOTE-ready"
