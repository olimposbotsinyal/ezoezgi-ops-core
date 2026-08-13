"""`pilot_fpr_core.py` testleri -- gorevin acikca istedigi test odagi:
FPR hesaplama + yetersiz-veri mantigi (deterministik, sentetik
sinyal/adjudikasyon verisiyle, gercek dosya sistemi olmadan)."""

from __future__ import annotations

import json

import pytest

from pilot_fpr_core import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    MIN_ADJUDICATED_FOR_RATE,
    STATUS_COMPUTED,
    STATUS_INSUFFICIENT_DATA,
    VERDICT_FALSE_POSITIVE,
    VERDICT_TRUE_POSITIVE,
    Adjudication,
    Signal,
    compute_feature_fpr,
    confidence_band_for_sample_size,
    parse_adjudications,
    render_fpr_summary_md,
    validate_adjudication_traceability,
    validate_adjudications_file,
    write_fpr_summary,
)


def _signal(signal_id: str) -> Signal:
    return Signal(feature="x", signal_id=signal_id, evidence_path=f"reports/{signal_id}.json")


def _adjudication(signal_id: str, verdict: str) -> Adjudication:
    return Adjudication(
        feature="x", signal_id=signal_id, verdict=verdict, adjudicated_by="alice", adjudicated_at_utc="2026-08-13T00:00:00+00:00"
    )


# --- confidence_band_for_sample_size ---------------------------------------------


def test_confidence_band_none_below_minimum():
    assert confidence_band_for_sample_size(MIN_ADJUDICATED_FOR_RATE - 1) is None


def test_confidence_band_low_at_minimum():
    assert confidence_band_for_sample_size(MIN_ADJUDICATED_FOR_RATE) == CONFIDENCE_LOW


def test_confidence_band_medium_threshold():
    assert confidence_band_for_sample_size(10) == CONFIDENCE_MEDIUM


def test_confidence_band_high_threshold():
    assert confidence_band_for_sample_size(30) == CONFIDENCE_HIGH


# --- compute_feature_fpr ----------------------------------------------------------


def test_compute_feature_fpr_insufficient_data_below_threshold():
    signals = [_signal("a"), _signal("b")]
    adjudications = [_adjudication("a", VERDICT_FALSE_POSITIVE), _adjudication("b", VERDICT_TRUE_POSITIVE)]
    result = compute_feature_fpr(signals, adjudications)
    assert result.status == STATUS_INSUFFICIENT_DATA
    assert result.false_positive_rate is None
    assert result.confidence_band is None
    assert result.adjudicated_signals == 2


def test_compute_feature_fpr_insufficient_data_never_reports_zero_rate():
    """Kritik davranis: yetersiz veri VARKEN oran ASLA `0.0` FABRIKE
    EDILMEZ -- `None` doner."""
    signals = [_signal("a")]
    adjudications = [_adjudication("a", VERDICT_TRUE_POSITIVE)]
    result = compute_feature_fpr(signals, adjudications)
    assert result.false_positive_rate is None
    assert result.false_positive_rate != 0.0


def test_compute_feature_fpr_computed_when_threshold_met():
    signals = [_signal(f"s{i}") for i in range(3)]
    adjudications = [
        _adjudication("s0", VERDICT_FALSE_POSITIVE),
        _adjudication("s1", VERDICT_TRUE_POSITIVE),
        _adjudication("s2", VERDICT_TRUE_POSITIVE),
    ]
    result = compute_feature_fpr(signals, adjudications)
    assert result.status == STATUS_COMPUTED
    assert result.false_positive_rate == pytest.approx(1 / 3)
    assert result.confidence_band == CONFIDENCE_LOW


def test_compute_feature_fpr_all_false_positives_rate_is_one():
    signals = [_signal(f"s{i}") for i in range(3)]
    adjudications = [_adjudication(f"s{i}", VERDICT_FALSE_POSITIVE) for i in range(3)]
    result = compute_feature_fpr(signals, adjudications)
    assert result.false_positive_rate == 1.0


def test_compute_feature_fpr_ignores_unmatched_adjudications():
    """Farkli bir sinyal_id icin adjudikasyon, bu ozelligin sayaclarini
    ETKILEMEMELIDIR."""
    signals = [_signal("a")]
    adjudications = [_adjudication("UNRELATED_SIGNAL", VERDICT_FALSE_POSITIVE)]
    result = compute_feature_fpr(signals, adjudications)
    assert result.adjudicated_signals == 0
    assert result.total_signals == 1


def test_compute_feature_fpr_total_signals_includes_unadjudicated():
    signals = [_signal("a"), _signal("b"), _signal("c"), _signal("d")]
    adjudications = [_adjudication("a", VERDICT_FALSE_POSITIVE), _adjudication("b", VERDICT_TRUE_POSITIVE), _adjudication("c", VERDICT_TRUE_POSITIVE)]
    result = compute_feature_fpr(signals, adjudications)
    assert result.total_signals == 4
    assert result.adjudicated_signals == 3
    assert result.status == STATUS_COMPUTED


# --- Adjudication dogrulama --------------------------------------------------------


def test_adjudication_rejects_invalid_verdict():
    with pytest.raises(ValueError):
        Adjudication(feature="x", signal_id="a", verdict="MAYBE", adjudicated_by="alice", adjudicated_at_utc="t")


def test_validate_adjudications_file_accepts_well_formed():
    payload = {"adjudications": [{"feature": "x", "signal_id": "a", "verdict": VERDICT_FALSE_POSITIVE, "adjudicated_by": "alice", "adjudicated_at_utc": "t"}]}
    assert validate_adjudications_file(payload) == []


def test_validate_adjudications_file_accepts_empty_list():
    assert validate_adjudications_file({"adjudications": []}) == []


def test_validate_adjudications_file_rejects_non_dict():
    errors = validate_adjudications_file("not a dict")
    assert errors


def test_validate_adjudications_file_rejects_missing_adjudications_key():
    errors = validate_adjudications_file({})
    assert any("adjudications" in e for e in errors)


def test_validate_adjudications_file_rejects_missing_required_field():
    payload = {"adjudications": [{"feature": "x", "signal_id": "a", "verdict": VERDICT_FALSE_POSITIVE, "adjudicated_by": "alice"}]}
    errors = validate_adjudications_file(payload)
    assert any("adjudicated_at_utc" in e for e in errors)


def test_validate_adjudications_file_rejects_invalid_verdict():
    payload = {"adjudications": [{"feature": "x", "signal_id": "a", "verdict": "MAYBE", "adjudicated_by": "alice", "adjudicated_at_utc": "t"}]}
    errors = validate_adjudications_file(payload)
    assert any("verdict" in e for e in errors)


def test_parse_adjudications_builds_objects():
    payload = {"adjudications": [{"feature": "x", "signal_id": "a", "verdict": VERDICT_TRUE_POSITIVE, "adjudicated_by": "alice", "adjudicated_at_utc": "t", "notes": "n"}]}
    result = parse_adjudications(payload)
    assert len(result) == 1
    assert result[0].signal_id == "a"
    assert result[0].notes == "n"


def test_parse_adjudications_skips_malformed_entries_silently():
    payload = {"adjudications": [{"feature": "x", "signal_id": "a"}]}  # eksik alanlar
    assert parse_adjudications(payload) == []


def test_real_repo_adjudications_file_is_valid():
    """`pilot_fpr_adjudications.json` artik BOS DEGIL -- 'Final promotion
    attempt preparation' gorevinde, docs/BACKLOG.md + pilot_summary.md'de
    ZATEN yazili/belgelenmis 3 sonuc (CONFIRMED_TRUE_POSITIVE, her biri
    icin) dogrudan alintilanarak eklendi (bkz. dosyanin $adjudications_source_note
    alani + her girdinin `notes`u). Semasi hala GECERLI olmali."""
    from pathlib import Path

    payload = json.loads(Path("infra/monitoring/governance/pilot_fpr_adjudications.json").read_text(encoding="utf-8"))
    assert validate_adjudications_file(payload) == []
    assert len(payload["adjudications"]) == 3
    for entry in payload["adjudications"]:
        assert entry["verdict"] in (VERDICT_TRUE_POSITIVE, VERDICT_FALSE_POSITIVE)
        assert entry["adjudicated_by"].strip() != ""


def test_real_repo_adjudications_are_traceable_to_real_signals():
    """Repo'daki GERCEK adjudikasyonlarin, `compute_pilot_false_positive_rate.py`'nin
    GERCEK sinyal toplayicilarinin urettigi sinyallerle eslesip
    eslesmedigini dogrular -- hayalet/izlenemeyen bir adjudikasyon
    olmamali (gorev kisiti: "keep entries traceable to concrete evidence
    ids/paths")."""
    from pathlib import Path

    import sys

    sys.path.insert(0, "scripts/ops")
    from compute_pilot_false_positive_rate import (
        collect_auto_rollback_signals,
        collect_chain_matching_signals,
        collect_legitimacy_signals,
    )

    repo_root = Path(".")
    all_signals = (
        collect_chain_matching_signals(repo_root)
        + collect_auto_rollback_signals(repo_root)
        + collect_legitimacy_signals(repo_root)
    )
    payload = json.loads(Path("infra/monitoring/governance/pilot_fpr_adjudications.json").read_text(encoding="utf-8"))
    adjudications = parse_adjudications(payload)
    assert validate_adjudication_traceability(adjudications, all_signals) == []


# --- validate_adjudication_traceability (deterministik/sentetik) -----------------


def test_validate_adjudication_traceability_accepts_matching_signal():
    signals = [Signal(feature="x", signal_id="a", evidence_path="p")]
    adjudications = [_adjudication("a", VERDICT_TRUE_POSITIVE)]
    assert validate_adjudication_traceability(adjudications, signals) == []


def test_validate_adjudication_traceability_rejects_ghost_entry():
    """Hicbir gercek sinyalle eslesmeyen bir adjudikasyon (ornegin
    sinyal artik kanittan silindiyse veya signal_id yanlis yazildiysa)
    izlenemeyen/hayalet bir girdi olarak REDDEDILMELIDIR."""
    signals: list[Signal] = []
    adjudications = [_adjudication("ghost-id", VERDICT_TRUE_POSITIVE)]
    errors = validate_adjudication_traceability(adjudications, signals)
    assert len(errors) == 1
    assert "hayalet" in errors[0]


def test_validate_adjudication_traceability_rejects_empty_adjudicated_by():
    signals = [Signal(feature="x", signal_id="a", evidence_path="p")]
    anonymous = Adjudication(
        feature="x", signal_id="a", verdict=VERDICT_TRUE_POSITIVE, adjudicated_by="  ", adjudicated_at_utc="t"
    )
    errors = validate_adjudication_traceability([anonymous], signals)
    assert any("anonim" in e for e in errors)


def test_validate_adjudication_traceability_empty_lists_no_errors():
    assert validate_adjudication_traceability([], []) == []


def test_validate_adjudication_traceability_matches_by_feature_and_signal_id_together():
    """Ayni signal_id, FARKLI bir feature altinda GECERLI bir sinyal
    DEGILDIR -- eslestirme (feature, signal_id) CIFTI uzerinden
    yapilmalidir, yalnizca signal_id uzerinden DEGIL."""
    signals = [Signal(feature="feature_a", signal_id="shared-id", evidence_path="p")]
    adjudications = [_adjudication("shared-id", VERDICT_TRUE_POSITIVE)]  # feature="x", signals'taki feature_a DEGIL
    errors = validate_adjudication_traceability(adjudications, signals)
    assert len(errors) == 1


# --- Rapor uretimi ------------------------------------------------------------------


def test_render_fpr_summary_md_shows_insufficient_data_explicitly():
    from pilot_fpr_core import FeatureFprResult

    results = {"x": FeatureFprResult(total_signals=1, adjudicated_signals=0, status=STATUS_INSUFFICIENT_DATA)}
    md = render_fpr_summary_md(results, generated_at="2026-08-13T00:00:00+00:00")
    assert "INSUFFICIENT_DATA" in md
    assert "n/a" in md


def test_write_fpr_summary_creates_md_and_json(tmp_path):
    from pilot_fpr_core import FeatureFprResult

    results = {"x": FeatureFprResult(total_signals=3, adjudicated_signals=3, confirmed_false_positives=1, false_positive_rate=1 / 3, confidence_band=CONFIDENCE_LOW, status=STATUS_COMPUTED)}
    paths = write_fpr_summary(results, tmp_path / "out", generated_at="2026-08-13T00:00:00+00:00")
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert payload["features"]["x"]["status"] == STATUS_COMPUTED
    assert payload["features"]["x"]["false_positive_rate"] == pytest.approx(1 / 3)
