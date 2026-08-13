"""`weekly_review_core.py` testleri -- GREEN/YELLOW/RED durum
siniflandirmasi (gorevin acikca istedigi test odagi) + review.md
girdisi olusturma/EKLEME (silme/uzerine yazma DEGIL)."""

from __future__ import annotations

from weekly_review_core import (
    STATUS_GREEN,
    STATUS_RED,
    STATUS_YELLOW,
    append_review_entry,
    classify_weekly_status,
    render_review_entry_md,
    status_to_exit_code,
)

DEFAULTS = dict(null_intent_warn=0.01, null_intent_crit=0.02)


def test_all_healthy_is_green():
    result = classify_weekly_status(
        gate_exit_code=0, drift_exit_code=0, fallback_ratio=0.01, null_intent_ratio=0.005, **DEFAULTS
    )
    assert result.status == STATUS_GREEN
    assert result.top_alerts == []


def test_no_data_at_all_is_green_not_crash():
    """Hic trafik yoksa (bu makinenin gercek durumu) oranlar None olur
    -- bu bir HATA/kirmizi durum DEGILDIR, saglikli-ama-veri-yok
    olarak GREEN sayilir (gate/drift de saglikliyse)."""
    result = classify_weekly_status(
        gate_exit_code=0, drift_exit_code=0, fallback_ratio=None, null_intent_ratio=None, **DEFAULTS
    )
    assert result.status == STATUS_GREEN


def test_gate_fail_is_red():
    result = classify_weekly_status(
        gate_exit_code=2, drift_exit_code=0, fallback_ratio=0.0, null_intent_ratio=0.0, **DEFAULTS
    )
    assert result.status == STATUS_RED
    assert any("FAIL" in r for r in result.reasons)


def test_critical_drift_is_red():
    result = classify_weekly_status(
        gate_exit_code=0, drift_exit_code=2, fallback_ratio=0.0, null_intent_ratio=0.0, **DEFAULTS
    )
    assert result.status == STATUS_RED
    assert any("KRITIK" in r for r in result.reasons)


def test_null_intent_above_crit_is_red():
    result = classify_weekly_status(
        gate_exit_code=0, drift_exit_code=0, fallback_ratio=0.0, null_intent_ratio=0.05, **DEFAULTS
    )
    assert result.status == STATUS_RED
    assert "HIGH_NULL_INTENT_RATE (CRIT)" in result.top_alerts


def test_gate_partial_is_yellow():
    result = classify_weekly_status(
        gate_exit_code=1, drift_exit_code=0, fallback_ratio=0.0, null_intent_ratio=0.0, **DEFAULTS
    )
    assert result.status == STATUS_YELLOW


def test_non_critical_drift_is_yellow():
    result = classify_weekly_status(
        gate_exit_code=0, drift_exit_code=1, fallback_ratio=0.0, null_intent_ratio=0.0, **DEFAULTS
    )
    assert result.status == STATUS_YELLOW


def test_null_intent_above_warn_below_crit_is_yellow():
    result = classify_weekly_status(
        gate_exit_code=0, drift_exit_code=0, fallback_ratio=0.0, null_intent_ratio=0.015, **DEFAULTS
    )
    assert result.status == STATUS_YELLOW
    assert "HIGH_NULL_INTENT_RATE (WARN)" in result.top_alerts


def test_red_takes_priority_over_yellow_conditions():
    """Ayni anda hem RED hem YELLOW kosullari varsa (ornegin gate FAIL +
    drift WARN), sonuc RED olmali -- en yuksek siddet KAZANIR."""
    result = classify_weekly_status(
        gate_exit_code=2, drift_exit_code=1, fallback_ratio=0.0, null_intent_ratio=0.0, **DEFAULTS
    )
    assert result.status == STATUS_RED


def test_status_to_exit_code_mapping():
    assert status_to_exit_code(STATUS_GREEN) == 0
    assert status_to_exit_code(STATUS_YELLOW) == 1
    assert status_to_exit_code(STATUS_RED) == 2


def test_render_review_entry_md_includes_status_and_ratios():
    result = classify_weekly_status(
        gate_exit_code=0, drift_exit_code=0, fallback_ratio=0.03, null_intent_ratio=0.005, **DEFAULTS
    )
    md = render_review_entry_md(result, generated_at="2026-08-13T00:00:00Z", iso_week="2026-W33")
    assert "GREEN" in md
    assert "2026-W33" in md
    assert "0.0300" in md


def test_append_review_entry_creates_file_with_header_on_first_call(tmp_path):
    path = tmp_path / "review.md"
    append_review_entry(path, "## entry1\ncontent1\n", iso_week="2026-W33")

    content = path.read_text(encoding="utf-8")
    assert "2026-W33" in content.splitlines()[0]
    assert "entry1" in content


def test_append_review_entry_appends_without_overwriting_prior_entries(tmp_path):
    path = tmp_path / "review.md"
    append_review_entry(path, "## entry1\ncontent1\n", iso_week="2026-W33")
    append_review_entry(path, "## entry2\ncontent2\n", iso_week="2026-W33")

    content = path.read_text(encoding="utf-8")
    assert "entry1" in content
    assert "entry2" in content
    assert content.count("# Haftalik") == 1  # baslik yalnizca BIR KEZ yazilir
