"""`secret_scan_core.py` testleri -- desen-tabanli SIR taramasi,
allowlist/denylist davranisi, gercek deger ASLA rapor edilmez (yalnizca
maskelenmis on-izleme)."""

from __future__ import annotations

from secret_scan_core import (
    DEFAULT_ALLOWLIST_MARKERS,
    DEFAULT_DENYLIST_PATTERNS,
    load_scan_config,
    scan_files_for_secrets,
)


def test_scan_detects_aws_access_key():
    findings = scan_files_for_secrets([("f.py", 'key = "AKIAABCDEFGHIJKLMNOP"')])
    assert len(findings) == 1
    assert findings[0].pattern_name == "aws_access_key_id"


def test_scan_detects_generic_api_key_assignment():
    findings = scan_files_for_secrets([("f.py", 'api_key = "sk-proj-1234567890abcdefghij"')])
    assert any(f.pattern_name == "generic_api_key_assignment" for f in findings)


def test_scan_detects_generic_secret_assignment():
    findings = scan_files_for_secrets([("f.py", 'password = "realSecretValue123"')])
    assert any(f.pattern_name == "generic_secret_assignment" for f in findings)


def test_scan_detects_private_key_block():
    findings = scan_files_for_secrets([("id_rsa", "-----BEGIN RSA PRIVATE KEY-----")])
    assert any(f.pattern_name == "private_key_block" for f in findings)


def test_scan_detects_slack_token():
    findings = scan_files_for_secrets([("f.py", 'token = "xoxb-1234567890-abcdefghij"')])
    assert len(findings) >= 1


def test_scan_ignores_clean_code():
    findings = scan_files_for_secrets([("f.py", "x = 1\ndef foo():\n    return x\n")])
    assert findings == []


def test_scan_allowlist_marker_suppresses_finding():
    findings = scan_files_for_secrets([("f.py", 'api_key = "EXAMPLE1234567890ab"  # dummy for docs')])
    assert findings == []


def test_scan_allowlist_path_prefix_skips_entire_file():
    findings = scan_files_for_secrets(
        [(".venv/lib/whatever.py", 'api_key = "realLookingKey1234567890"')]
    )
    assert findings == []


def test_scan_finding_never_contains_raw_secret_value():
    real_secret = "AKIAABCDEFGHIJKLMNOP"
    findings = scan_files_for_secrets([("f.py", f'key = "{real_secret}"')])
    assert len(findings) == 1
    assert real_secret not in findings[0].matched_snippet


def test_scan_reports_correct_line_number():
    content = "line1\nline2\napi_key = \"realLookingKey1234567890\"\nline4"
    findings = scan_files_for_secrets([("f.py", content)])
    assert findings[0].line_number == 3


def test_scan_multiple_files_multiple_findings():
    findings = scan_files_for_secrets(
        [("a.py", 'key = "AKIAABCDEFGHIJKLMNOP"'), ("b.py", "x = 1"), ("c.py", 'key = "AKIAZZZZZZZZZZZZZZZZ"')]
    )
    assert len(findings) == 2
    assert {f.path for f in findings} == {"a.py", "c.py"}


def test_scan_custom_denylist_pattern():
    findings = scan_files_for_secrets(
        [("f.py", "MY_CUSTOM_TOKEN=abc123")], denylist_patterns={"custom": r"MY_CUSTOM_TOKEN=\w+"}
    )
    assert len(findings) == 1
    assert findings[0].pattern_name == "custom"


def test_load_scan_config_falls_back_to_defaults_for_empty_config():
    denylist, markers, paths = load_scan_config({})
    assert denylist == DEFAULT_DENYLIST_PATTERNS
    assert markers == DEFAULT_ALLOWLIST_MARKERS


def test_load_scan_config_uses_provided_values():
    config = {"denylist_patterns": {"x": "y"}, "allowlist_markers": ["FOO"], "allowlist_paths": ["bar/"]}
    denylist, markers, paths = load_scan_config(config)
    assert denylist == {"x": "y"}
    assert markers == ["FOO"]
    assert paths == ["bar/"]


def test_real_repo_secret_scan_config_is_valid_and_loadable():
    import json
    from pathlib import Path

    config = json.loads(Path("infra/monitoring/governance/secret_scan_patterns_v1.json").read_text(encoding="utf-8"))
    denylist, markers, paths = load_scan_config(config)
    assert len(denylist) > 0
    assert len(markers) > 0
