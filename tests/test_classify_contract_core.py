"""`classify_contract_core.py` testleri -- AST tabanli imza cikarimi +
checksum + drift tespiti, tamamen deterministik/sentetik kaynak
metniyle."""

from __future__ import annotations

from classify_contract_core import (
    check_contract_drift,
    compute_contract_checksum,
    extract_function_signature,
)

REAL_SOURCE = "services/tr-en-bridge/src/ollama_nlu.py"


def test_extract_function_signature_finds_real_classify():
    from pathlib import Path

    source = Path(REAL_SOURCE).read_text(encoding="utf-8")
    sig = extract_function_signature(source, "classify")
    assert sig is not None
    assert sig.startswith("def classify(")
    assert "input_tr:str" in sig


def test_extract_function_signature_returns_none_for_missing_function():
    sig = extract_function_signature("def other():\n    pass\n", "classify")
    assert sig is None


def test_extract_function_signature_handles_syntax_error_gracefully():
    assert extract_function_signature("def broken(:\n", "classify") is None


def test_extract_function_signature_ignores_docstring_and_comments():
    """Sozlesme (imza) checksum'i, docstring/yorum degisikliklerinden
    ETKILENMEMELIDIR -- yalnizca parametre/tip/donus tipi onemlidir."""
    source_v1 = 'def classify(x: int) -> int:\n    """Eski docstring."""\n    return x\n'
    source_v2 = 'def classify(x: int) -> int:\n    """TAMAMEN FARKLI, uzun bir docstring burada."""\n    # yorum\n    return x\n'
    sig1 = extract_function_signature(source_v1, "classify")
    sig2 = extract_function_signature(source_v2, "classify")
    assert sig1 == sig2
    assert compute_contract_checksum(sig1) == compute_contract_checksum(sig2)


def test_extract_function_signature_default_value_change_does_not_affect_signature():
    """Bir varsayilan DEGERIN degismesi (ornegin 2 -> 3) DAVRANIS
    degisikligidir, SOZLESME degisikligi DEGILDIR -- imza AYNI kalmali."""
    source_v1 = "def classify(x: int, max_attempts: int = 2) -> int:\n    return x\n"
    source_v2 = "def classify(x: int, max_attempts: int = 3) -> int:\n    return x\n"
    sig1 = extract_function_signature(source_v1, "classify")
    sig2 = extract_function_signature(source_v2, "classify")
    assert sig1 == sig2


def test_extract_function_signature_new_required_parameter_changes_signature():
    source_v1 = "def classify(x: int) -> int:\n    return x\n"
    source_v2 = "def classify(x: int, y: int) -> int:\n    return x\n"
    sig1 = extract_function_signature(source_v1, "classify")
    sig2 = extract_function_signature(source_v2, "classify")
    assert sig1 != sig2


def test_extract_function_signature_return_type_change_changes_signature():
    source_v1 = "def classify(x: int) -> int:\n    return x\n"
    source_v2 = "def classify(x: int) -> str:\n    return str(x)\n"
    sig1 = extract_function_signature(source_v1, "classify")
    sig2 = extract_function_signature(source_v2, "classify")
    assert sig1 != sig2


def test_compute_contract_checksum_deterministic():
    assert compute_contract_checksum("def x() -> None") == compute_contract_checksum("def x() -> None")


def test_check_contract_drift_false_when_signature_matches_baseline():
    sig = "def classify(x:int) -> int"
    checksum = compute_contract_checksum(sig)
    changed, reason = check_contract_drift(sig, checksum)
    assert changed is False
    assert "eslesiyor" in reason


def test_check_contract_drift_true_when_signature_differs():
    changed, reason = check_contract_drift("def classify(x:int) -> int", "0" * 64)
    assert changed is True
    assert "DEGISMIS" in reason


def test_check_contract_drift_true_when_signature_is_none():
    changed, reason = check_contract_drift(None, "0" * 64)
    assert changed is True
    assert "BULUNAMADI" in reason


def test_check_contract_drift_baseline_checksum_is_case_and_whitespace_insensitive():
    sig = "def classify(x:int) -> int"
    checksum = compute_contract_checksum(sig)
    changed, _ = check_contract_drift(sig, f"  {checksum.upper()}  \n")
    assert changed is False
