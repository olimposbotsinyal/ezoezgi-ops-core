"""`export_weekly_observability_json.py` testleri -- `weekly_review_core.py::render_review_entry_md`'nin
GERCEK cikardigi bicime karsi parse dogrulugu (deterministik, gercek
`render_review_entry_md`/`append_review_entry` ile uretilmis markdown
kullanarak) + uctan uca CLI davranisi."""

from __future__ import annotations

import json
from pathlib import Path

from export_weekly_observability_json import (
    count_recent_incidents,
    export_review_json,
    main,
    parse_review_markdown,
    write_review_json,
)
from weekly_review_core import append_review_entry, classify_weekly_status, render_review_entry_md


def test_parse_review_markdown_single_green_entry():
    result = classify_weekly_status(
        gate_exit_code=0, drift_exit_code=0, fallback_ratio=0.02, null_intent_ratio=0.01,
        null_intent_warn=0.05, null_intent_crit=0.1,
    )
    md = render_review_entry_md(result, generated_at="2026-08-06T00:00:00+00:00", iso_week="2026-W32")
    entries = parse_review_markdown(md)

    assert len(entries) == 1
    assert entries[0]["status"] == "GREEN"
    assert entries[0]["iso_week"] == "2026-W32"
    assert entries[0]["fallback_ratio"] == 0.02
    assert entries[0]["null_intent_ratio"] == 0.01
    assert entries[0]["top_alerts"] == []
    assert entries[0]["reasons"] == ["Tum kontroller saglikli."]


def test_parse_review_markdown_red_entry_with_multiple_reasons_and_alerts():
    result = classify_weekly_status(
        gate_exit_code=2, drift_exit_code=2, fallback_ratio=None, null_intent_ratio=0.15,
        null_intent_warn=0.05, null_intent_crit=0.1,
    )
    md = render_review_entry_md(result, generated_at="2026-08-13T00:00:00+00:00", iso_week="2026-W33")
    entries = parse_review_markdown(md)

    assert entries[0]["status"] == "RED"
    assert len(entries[0]["reasons"]) == 3
    assert entries[0]["fallback_ratio"] is None  # "n/a" -- yetersiz/hic veri yok
    assert entries[0]["null_intent_ratio"] == 0.15
    assert "HIGH_NULL_INTENT_RATE (CRIT)" in entries[0]["top_alerts"]


def test_parse_review_markdown_handles_multiple_appended_entries():
    """`weekly_observability_review.ps1` AYNI hafta birden fazla kez
    calistirilirsa, review.md'ye BIRDEN FAZLA girdi EKLENIR (uzerine
    yazilmaz) -- parser TUMUNU YAKALAMALIDIR."""
    result = classify_weekly_status(
        gate_exit_code=0, drift_exit_code=0, fallback_ratio=0.01, null_intent_ratio=0.01,
        null_intent_warn=0.05, null_intent_crit=0.1,
    )
    entry1 = render_review_entry_md(result, generated_at="2026-08-06T00:00:00+00:00", iso_week="2026-W32")
    entry2 = render_review_entry_md(result, generated_at="2026-08-07T00:00:00+00:00", iso_week="2026-W32")
    combined = entry1 + entry2
    entries = parse_review_markdown(combined)
    assert len(entries) == 2
    assert entries[0]["generated_at"] == "2026-08-06T00:00:00+00:00"
    assert entries[1]["generated_at"] == "2026-08-07T00:00:00+00:00"


def test_parse_review_markdown_empty_text_returns_empty_list():
    assert parse_review_markdown("") == []


def test_parse_review_markdown_malformed_text_returns_empty_list_not_crash():
    assert parse_review_markdown("bu gecerli bir bicim degil\nrastgele metin") == []


# --- count_recent_incidents ---------------------------------------------------------


def test_count_recent_incidents_zero_when_directory_missing(tmp_path):
    assert count_recent_incidents(tmp_path, iso_week="2026-W32") == 0


def test_count_recent_incidents_counts_subdirectories(tmp_path):
    (tmp_path / "reports" / "incidents" / "gateway_1").mkdir(parents=True)
    (tmp_path / "reports" / "incidents" / "gateway_2").mkdir(parents=True)
    (tmp_path / "reports" / "incidents" / "not_a_dir.txt").write_text("x", encoding="utf-8")
    assert count_recent_incidents(tmp_path, iso_week="2026-W32") == 2


# --- export_review_json / write_review_json ------------------------------------------


def test_export_review_json_end_to_end_with_real_core_functions(tmp_path):
    result = classify_weekly_status(
        gate_exit_code=0, drift_exit_code=0, fallback_ratio=0.02, null_intent_ratio=0.01,
        null_intent_warn=0.05, null_intent_crit=0.1,
    )
    review_md_path = tmp_path / "reports" / "weekly_observability_2026-W32" / "review.md"
    entry_md = render_review_entry_md(result, generated_at="2026-08-06T00:00:00+00:00", iso_week="2026-W32")
    append_review_entry(review_md_path, entry_md, iso_week="2026-W32")

    entries = export_review_json(review_md_path, repo_root=tmp_path)
    assert len(entries) == 1
    assert entries[0]["incidents_count"] == 0


def test_export_review_json_missing_file_returns_empty_list(tmp_path):
    assert export_review_json(tmp_path / "does_not_exist.md", repo_root=tmp_path) == []


def test_write_review_json_produces_jsonl(tmp_path):
    entries = [{"a": 1}, {"b": 2}]
    out_path = tmp_path / "review.json"
    write_review_json(entries, out_path)
    lines = out_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"a": 1}


def test_write_review_json_empty_entries_produces_empty_file(tmp_path):
    out_path = tmp_path / "review.json"
    write_review_json([], out_path)
    assert out_path.read_text(encoding="utf-8") == ""


# --- Uctan uca CLI --------------------------------------------------------------------


def test_main_processes_all_weekly_dirs_when_no_specific_path_given(tmp_path, monkeypatch):
    result = classify_weekly_status(
        gate_exit_code=0, drift_exit_code=0, fallback_ratio=0.01, null_intent_ratio=0.01,
        null_intent_warn=0.05, null_intent_crit=0.1,
    )
    review_md_path = tmp_path / "reports" / "weekly_observability_2026-W32" / "review.md"
    entry_md = render_review_entry_md(result, generated_at="2026-08-06T00:00:00+00:00", iso_week="2026-W32")
    append_review_entry(review_md_path, entry_md, iso_week="2026-W32")

    monkeypatch.setattr("sys.argv", ["export_weekly_observability_json.py", "--repo-root", str(tmp_path)])
    exit_code = main()
    assert exit_code == 0
    assert (review_md_path.parent / "review.json").exists()


def test_main_returns_zero_when_no_weekly_dirs_exist(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["export_weekly_observability_json.py", "--repo-root", str(tmp_path)])
    exit_code = main()
    assert exit_code == 0
