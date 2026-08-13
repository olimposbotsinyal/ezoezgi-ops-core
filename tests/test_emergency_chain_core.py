"""`emergency_chain_core.py` testleri -- v1.2 PILOT checksum-zinciri
kapanis siniflandirmasi: gercek zincir-devamliligi vs yalnizca ayni
alert_name (yanlis-pozitif "cozuldu" riski) ayrimi."""

from __future__ import annotations

from datetime import datetime, timezone

from emergency_chain_core import (
    CLOSURE_BROKEN_CHAIN,
    CLOSURE_NO_FOLLOWUP,
    CLOSURE_RESOLVED,
    compare_matching_strategies,
    evaluate_chain_closure,
    find_overdue_emergency_entries,
)


def _entry(**overrides):
    entry = {
        "timestamp": "2026-08-10T00:00:00+00:00",
        "proposal_id": "HIGH_NULL_INTENT_RATE-20260810T000000",
        "alert_name": "HIGH_NULL_INTENT_RATE",
        "old_checksum": "aaa",
        "new_checksum": "bbb",
        "apply_report_path": "r1.json",
        "is_emergency": True,
        "retro_review_due_utc": "2026-08-11T00:00:00+00:00",
    }
    entry.update(overrides)
    return entry


def test_evaluate_chain_closure_resolved_when_followup_old_checksum_matches():
    emergency = _entry(new_checksum="bbb")
    followup = _entry(
        proposal_id="X-2", timestamp="2026-08-12T00:00:00+00:00", is_emergency=False,
        old_checksum="bbb", new_checksum="ccc", retro_review_due_utc=None,
    )
    assert evaluate_chain_closure(emergency, [emergency, followup]) == CLOSURE_RESOLVED


def test_evaluate_chain_closure_broken_chain_when_followup_checksum_does_not_match():
    """Bu, v1.1'in `has_followup` (alert_name-only) mantiginin yakalayamadigi
    TAM DA senaryo -- ayni alert icin sonraki bir normal apply VAR ama o,
    acil durumun BIRAKTIGI dosya durumundan devam ETMIYOR (araya baska
    bir degisiklik girmis)."""
    emergency = _entry(new_checksum="bbb")
    unrelated_followup = _entry(
        proposal_id="X-2", timestamp="2026-08-12T00:00:00+00:00", is_emergency=False,
        old_checksum="SOMETHING_ELSE", new_checksum="ccc", retro_review_due_utc=None,
    )
    assert evaluate_chain_closure(emergency, [emergency, unrelated_followup]) == CLOSURE_BROKEN_CHAIN


def test_evaluate_chain_closure_no_followup_when_nothing_for_alert():
    emergency = _entry()
    assert evaluate_chain_closure(emergency, [emergency]) == CLOSURE_NO_FOLLOWUP


def test_evaluate_chain_closure_no_followup_ignores_different_alert():
    emergency = _entry()
    different_alert_followup = _entry(
        proposal_id="Y-1", alert_name="FALLBACK_SPIKE", timestamp="2026-08-12T00:00:00+00:00",
        is_emergency=False, old_checksum="bbb", new_checksum="ccc", retro_review_due_utc=None,
    )
    assert evaluate_chain_closure(emergency, [emergency, different_alert_followup]) == CLOSURE_NO_FOLLOWUP


def test_evaluate_chain_closure_resolved_when_any_of_several_followups_chains():
    emergency = _entry(new_checksum="bbb")
    broken = _entry(
        proposal_id="X-2", timestamp="2026-08-11T12:00:00+00:00", is_emergency=False,
        old_checksum="wrong", new_checksum="ddd", retro_review_due_utc=None,
    )
    chained = _entry(
        proposal_id="X-3", timestamp="2026-08-12T00:00:00+00:00", is_emergency=False,
        old_checksum="bbb", new_checksum="ccc", retro_review_due_utc=None,
    )
    assert evaluate_chain_closure(emergency, [emergency, broken, chained]) == CLOSURE_RESOLVED


def test_evaluate_chain_closure_ignores_earlier_entries():
    """Zaman damgasi acil durumdan ONCE olan bir girdi 'takip eden' SAYILMAZ."""
    emergency = _entry(timestamp="2026-08-10T00:00:00+00:00", new_checksum="bbb")
    earlier = _entry(
        proposal_id="X-0", timestamp="2026-08-09T00:00:00+00:00", is_emergency=False,
        old_checksum="bbb", new_checksum="ccc", retro_review_due_utc=None,
    )
    assert evaluate_chain_closure(emergency, [emergency, earlier]) == CLOSURE_NO_FOLLOWUP


def test_find_overdue_emergency_entries_filters_correctly():
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    overdue = _entry(retro_review_due_utc="2026-08-11T00:00:00+00:00")
    not_yet_due = _entry(proposal_id="Y-1", retro_review_due_utc="2026-09-01T00:00:00+00:00")
    normal_entry = _entry(proposal_id="Z-1", is_emergency=False, retro_review_due_utc=None)

    result = find_overdue_emergency_entries([overdue, not_yet_due, normal_entry], now=now)
    assert result == [overdue]


def test_find_overdue_emergency_entries_handles_malformed_date_gracefully():
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    malformed = _entry(retro_review_due_utc="not-a-date")
    assert find_overdue_emergency_entries([malformed], now=now) == []


def test_compare_matching_strategies_flags_outcome_difference():
    emergency = _entry(new_checksum="bbb")
    broken_followup = _entry(
        proposal_id="X-2", timestamp="2026-08-12T00:00:00+00:00", is_emergency=False,
        old_checksum="wrong", new_checksum="ccc", retro_review_due_utc=None,
    )
    ledger = [emergency, broken_followup]
    rows = compare_matching_strategies(ledger, [emergency])

    assert len(rows) == 1
    assert rows[0]["v1_1_alert_name_only"] == "RESOLVED"
    assert rows[0]["v1_2_checksum_chain"] == CLOSURE_BROKEN_CHAIN
    assert rows[0]["outcome_differs"] is True


def test_compare_matching_strategies_no_difference_when_both_agree():
    emergency = _entry(new_checksum="bbb")
    chained_followup = _entry(
        proposal_id="X-2", timestamp="2026-08-12T00:00:00+00:00", is_emergency=False,
        old_checksum="bbb", new_checksum="ccc", retro_review_due_utc=None,
    )
    ledger = [emergency, chained_followup]
    rows = compare_matching_strategies(ledger, [emergency])

    assert rows[0]["v1_1_alert_name_only"] == "RESOLVED"
    assert rows[0]["v1_2_checksum_chain"] == CLOSURE_RESOLVED
    assert rows[0]["outcome_differs"] is False
