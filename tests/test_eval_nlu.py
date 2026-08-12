"""Faz 1 B031 completion gate -- tools/eval_nlu.py testleri.

Kapsam: golden set loader/parser doğrulaması, metrik hesaplama mantığı,
provider switch/sonuç sınıflandırma (fallback vs parse-error) sağlığı.
Hiçbir test gerçek Ollama servisi gerektirmez.
"""

from __future__ import annotations

import json

import pytest

from eval_nlu import (
    ACCEPTANCE_CRITERIA,
    DEFAULT_GOLDEN_PATH,
    GoldenSample,
    SampleResult,
    _entities_match,
    _has_parseable_json,
    _percentile,
    _run_mock,
    _run_ollama,
    compute_metrics,
    evaluate_acceptance,
    load_golden_set,
)


# --- load_golden_set ---------------------------------------------------------


def _write_jsonl(tmp_path, lines: list[str]):
    path = tmp_path / "golden.jsonl"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_load_golden_set_parses_valid_lines(tmp_path):
    path = _write_jsonl(
        tmp_path,
        [
            json.dumps({"text": "Ezo, echo ile merhaba yaz", "expected_intent": "RUN_ECHO", "expected_entities": {"value": "merhaba"}}),
            json.dumps({"text": "Ezo, hava durumunu söyle", "expected_intent": "UNKNOWN"}),
        ],
    )

    samples = load_golden_set(path)

    assert len(samples) == 2
    assert samples[0] == GoldenSample(
        text="Ezo, echo ile merhaba yaz",
        expected_intent="RUN_ECHO",
        expected_entities={"value": "merhaba"},
        notes="",
        risk_level=None,
    )
    assert samples[1].expected_entities == {}


def test_load_golden_set_skips_blank_lines(tmp_path):
    path = _write_jsonl(
        tmp_path,
        ["", json.dumps({"text": "a", "expected_intent": "UNKNOWN"}), "   ", ""],
    )

    samples = load_golden_set(path)

    assert len(samples) == 1


def test_load_golden_set_raises_on_malformed_json(tmp_path):
    path = _write_jsonl(tmp_path, ["{bu gecerli json degil"])

    with pytest.raises(ValueError, match="geçersiz JSON"):
        load_golden_set(path)


def test_load_golden_set_raises_when_text_missing(tmp_path):
    path = _write_jsonl(tmp_path, [json.dumps({"expected_intent": "UNKNOWN"})])

    with pytest.raises(ValueError, match="'text'"):
        load_golden_set(path)


def test_load_golden_set_raises_when_expected_intent_missing(tmp_path):
    path = _write_jsonl(tmp_path, [json.dumps({"text": "merhaba"})])

    with pytest.raises(ValueError, match="'expected_intent'"):
        load_golden_set(path)


def test_load_golden_set_raises_when_file_empty(tmp_path):
    path = _write_jsonl(tmp_path, [])

    with pytest.raises(ValueError, match="boş"):
        load_golden_set(path)


def test_real_golden_dataset_loads_and_is_within_size_target():
    samples = load_golden_set(DEFAULT_GOLDEN_PATH)

    assert 40 <= len(samples) <= 60
    intents = {s.expected_intent for s in samples}
    # dört bilinen kategori de temsil edilmeli.
    assert intents == {"RUN_ECHO", "SHOW_DAILY_SPENDING", "RUN_DELETE_FILE", "UNKNOWN"}


# --- _entities_match ----------------------------------------------------------


def test_entities_match_not_applicable_when_expected_empty():
    assert _entities_match({}, {"value": "x"}) is None


def test_entities_match_true_on_exact_match():
    assert _entities_match({"value": "merhaba"}, {"value": "merhaba"}) is True


def test_entities_match_false_on_mismatch():
    assert _entities_match({"value": "merhaba"}, {"value": "selam"}) is False


def test_entities_match_false_when_key_missing_in_actual():
    assert _entities_match({"value": "merhaba"}, {}) is False


def test_entities_match_ignores_extra_actual_keys():
    assert _entities_match({"value": "merhaba"}, {"value": "merhaba", "extra": "x"}) is True


# --- _has_parseable_json --------------------------------------------------------


def test_has_parseable_json_true_for_valid_json_block():
    assert _has_parseable_json('metin öncesi {"intent": "RUN_ECHO"} metin sonrası') is True


def test_has_parseable_json_false_for_plain_text():
    assert _has_parseable_json("bu sadece düz metin") is False


def test_has_parseable_json_false_for_empty_string():
    assert _has_parseable_json("") is False


# --- _percentile ----------------------------------------------------------------


def test_percentile_p50_on_simple_list():
    assert _percentile([1.0, 2.0, 3.0], 50) == 2.0


def test_percentile_empty_list_returns_zero():
    assert _percentile([], 95) == 0.0


def test_percentile_single_value():
    assert _percentile([1.5], 95) == 1.5


# --- compute_metrics -------------------------------------------------------------


def _sample(expected_intent="RUN_ECHO", expected_entities=None):
    return GoldenSample(text="x", expected_intent=expected_intent, expected_entities=expected_entities or {})


def test_compute_metrics_all_correct():
    results = [
        SampleResult(
            sample=_sample("RUN_ECHO", {"value": "merhaba"}),
            predicted_intent="RUN_ECHO",
            predicted_entities={"value": "merhaba"},
            confidence=0.9,
            latency_seconds=0.1,
            parse_error=False,
            fallback=False,
        )
        for _ in range(4)
    ]

    metrics = compute_metrics("test", results)

    assert metrics.available is True
    assert metrics.total_samples == 4
    assert metrics.intent_accuracy == 1.0
    assert metrics.entity_match_rate == 1.0
    assert metrics.parse_error_rate == 0.0
    assert metrics.fallback_rate == 0.0


def test_compute_metrics_mixed_results():
    correct = SampleResult(
        sample=_sample("RUN_ECHO"), predicted_intent="RUN_ECHO", predicted_entities={},
        confidence=0.9, latency_seconds=0.1, parse_error=False, fallback=False,
    )
    wrong = SampleResult(
        sample=_sample("SHOW_DAILY_SPENDING"), predicted_intent="UNKNOWN", predicted_entities={},
        confidence=0.0, latency_seconds=0.2, parse_error=True, fallback=False,
    )
    fell_back = SampleResult(
        sample=_sample("RUN_DELETE_FILE"), predicted_intent="UNKNOWN", predicted_entities={},
        confidence=0.0, latency_seconds=0.05, parse_error=False, fallback=True,
    )

    metrics = compute_metrics("test", [correct, wrong, fell_back])

    assert metrics.total_samples == 3
    assert metrics.intent_correct == 1
    assert metrics.intent_accuracy == pytest.approx(1 / 3)
    assert metrics.parse_errors == 1
    assert metrics.fallbacks == 1


def test_compute_metrics_entity_rate_ignores_samples_without_expected_entities():
    with_entity_correct = SampleResult(
        sample=_sample("RUN_ECHO", {"value": "merhaba"}), predicted_intent="RUN_ECHO",
        predicted_entities={"value": "merhaba"}, confidence=0.9, latency_seconds=0.1,
        parse_error=False, fallback=False,
    )
    no_entity_expected = SampleResult(
        sample=_sample("UNKNOWN", {}), predicted_intent="UNKNOWN", predicted_entities={},
        confidence=0.0, latency_seconds=0.1, parse_error=False, fallback=False,
    )

    metrics = compute_metrics("test", [with_entity_correct, no_entity_expected])

    assert metrics.entity_applicable == 1
    assert metrics.entity_match_rate == 1.0


def test_compute_metrics_unavailable_returns_zeroed_metrics_with_reason():
    metrics = compute_metrics("ollama", [], available=False, unavailable_reason="servis kapalı")

    assert metrics.available is False
    assert metrics.unavailable_reason == "servis kapalı"
    assert metrics.total_samples == 0


# --- evaluate_acceptance ----------------------------------------------------------


def test_evaluate_acceptance_all_pass_when_thresholds_met():
    metrics = compute_metrics(
        "test",
        [
            SampleResult(
                sample=_sample("RUN_ECHO", {"value": "m"}), predicted_intent="RUN_ECHO",
                predicted_entities={"value": "m"}, confidence=0.9, latency_seconds=0.01,
                parse_error=False, fallback=False,
            )
            for _ in range(20)
        ],
    )

    verdicts = evaluate_acceptance(metrics)

    assert verdicts["intent_accuracy"]["status"] == "PASS"
    assert verdicts["entity_match_rate"]["status"] == "PASS"
    assert verdicts["parse_error_rate"]["status"] == "PASS"
    assert verdicts["fallback_rate"]["status"] == "PASS"
    assert verdicts["latency_p95"]["status"] == "PASS"


def test_evaluate_acceptance_fails_below_intent_accuracy_threshold():
    results = [
        SampleResult(sample=_sample("RUN_ECHO"), predicted_intent="RUN_ECHO", predicted_entities={}, confidence=0.9, latency_seconds=0.01, parse_error=False, fallback=False)
        for _ in range(8)
    ] + [
        SampleResult(sample=_sample("RUN_ECHO"), predicted_intent="UNKNOWN", predicted_entities={}, confidence=0.0, latency_seconds=0.01, parse_error=False, fallback=False)
        for _ in range(2)
    ]
    metrics = compute_metrics("test", results)  # %80 accuracy < %90 esik

    verdicts = evaluate_acceptance(metrics)

    assert verdicts["intent_accuracy"]["status"] == "FAIL"


def test_evaluate_acceptance_not_evaluated_when_unavailable():
    metrics = compute_metrics("ollama", [], available=False, unavailable_reason="kurulu değil")

    verdicts = evaluate_acceptance(metrics)

    assert set(verdicts.keys()) == set(ACCEPTANCE_CRITERIA.keys())
    assert all(v["status"] == "NOT_EVALUATED" for v in verdicts.values())
    assert verdicts["intent_accuracy"]["reason"] == "kurulu değil"


def test_evaluate_acceptance_marks_entity_rate_not_applicable_when_no_critical_entities():
    results = [
        SampleResult(sample=_sample("UNKNOWN", {}), predicted_intent="UNKNOWN", predicted_entities={}, confidence=0.0, latency_seconds=0.01, parse_error=False, fallback=False)
        for _ in range(5)
    ]
    metrics = compute_metrics("test", results)

    verdicts = evaluate_acceptance(metrics)

    assert verdicts["entity_match_rate"]["status"] == "N/A"


# --- provider switch / calistirma sagligi -----------------------------------------


def test_run_mock_never_produces_entities():
    result = _run_mock(GoldenSample(text="Ezo, echo ile merhaba yaz", expected_intent="RUN_ECHO"))

    assert result.predicted_intent == "RUN_ECHO"
    assert result.predicted_entities == {}
    assert result.fallback is False
    assert result.parse_error is False


class _FakeOllamaClient:
    def __init__(self, response_text: str = "", fallback: bool = False):
        self._response_text = response_text
        self._fallback = fallback

    def generate(self, prompt: str) -> dict:
        return {"response": self._response_text, "model": "fake", "fallback": self._fallback}


def test_run_ollama_detects_fallback_via_prefix():
    # model_client basarisiz oldugunda "[mock] ..." ile baslayan bir yanit doner
    # (bkz. model_client.py::_mock_fallback) -- bu, ollama_nlu.classify()'in
    # "fallback" bilgisini disariya tasidigi tek yol, burada simule ediliyor.
    fake = _FakeOllamaClient(response_text="[mock] test", fallback=True)

    result = _run_ollama(GoldenSample(text="test", expected_intent="RUN_ECHO"), fake)

    assert result.fallback is True
    assert result.parse_error is False
    assert result.predicted_intent == "UNKNOWN"


def test_run_ollama_detects_parse_error_on_unparseable_response():
    fake = _FakeOllamaClient(response_text="bu gecerli JSON degil, duz metin")

    result = _run_ollama(GoldenSample(text="test", expected_intent="RUN_ECHO"), fake)

    assert result.fallback is False
    assert result.parse_error is True


def test_run_ollama_success_path_has_no_fallback_or_parse_error():
    fake = _FakeOllamaClient(response_text='{"intent": "RUN_ECHO", "entities": {"value": "merhaba"}, "confidence": 0.9}')

    result = _run_ollama(GoldenSample(text="test", expected_intent="RUN_ECHO"), fake)

    assert result.predicted_intent == "RUN_ECHO"
    assert result.predicted_entities == {"value": "merhaba"}
    assert result.fallback is False
    assert result.parse_error is False
