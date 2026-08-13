"""PLAN.md T21 -- ops_suite.events testleri: konu dogrulama, WS zarfi
serilestirme/ayristirma, hatali girdilerin SESSIZCE yutulmadigini
(InvalidEventError firlatildigini) dogrular."""

from __future__ import annotations

import json

import pytest

from ops_suite.events import (
    ALL_TOPICS,
    TOPIC_AGENT_PRESENCE,
    TOPIC_TASK_LIFECYCLE,
    InvalidEventError,
    WSEvent,
    build_event,
    parse_event,
)


def test_build_event_accepts_all_known_topics():
    for topic in ALL_TOPICS:
        event = build_event(topic, {"x": 1})
        assert event.topic == topic


def test_build_event_rejects_unknown_topic():
    with pytest.raises(InvalidEventError, match="bilinmeyen konu"):
        build_event("agent.gossip", {})


def test_to_json_round_trips_through_parse_event():
    event = build_event(TOPIC_AGENT_PRESENCE, {"agent_id": "a", "state": "idle"}, seq=3)
    raw = event.to_json()
    parsed = parse_event(raw)
    assert parsed == event


def test_parse_event_rejects_malformed_json():
    with pytest.raises(InvalidEventError, match="gecersiz JSON"):
        parse_event("{not valid json")


def test_parse_event_rejects_non_object_json():
    with pytest.raises(InvalidEventError, match="obje"):
        parse_event(json.dumps([1, 2, 3]))


def test_parse_event_rejects_missing_topic_field():
    with pytest.raises(InvalidEventError, match="topic"):
        parse_event(json.dumps({"payload": {}}))


def test_parse_event_rejects_missing_payload_field():
    with pytest.raises(InvalidEventError, match="payload"):
        parse_event(json.dumps({"topic": TOPIC_TASK_LIFECYCLE}))


def test_parse_event_rejects_unknown_topic_in_envelope():
    with pytest.raises(InvalidEventError, match="bilinmeyen konu"):
        parse_event(json.dumps({"topic": "not.a.real.topic", "payload": {}}))


def test_parse_event_defaults_seq_to_zero_when_absent():
    parsed = parse_event(json.dumps({"topic": TOPIC_AGENT_PRESENCE, "payload": {}}))
    assert parsed.seq == 0


def test_wsevent_to_json_is_valid_json_with_expected_keys():
    event = WSEvent(topic=TOPIC_AGENT_PRESENCE, payload={"a": 1}, seq=5)
    parsed = json.loads(event.to_json())
    assert parsed == {"topic": TOPIC_AGENT_PRESENCE, "payload": {"a": 1}, "seq": 5}
