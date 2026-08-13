"""package_gateway_incident_core.py testleri -- ozellikle secret masking.

Gercek dosya sistemi/git/ag gerektirmez (git testleri tmp_path'te gecici
bir repo kullanir).
"""

from __future__ import annotations

import json

from package_gateway_incident_core import (
    MASK_VALUE,
    build_config_fingerprint,
    collect_audit_slice,
    mask_secrets,
)


def test_mask_secrets_masks_known_sensitive_keys():
    data = {
        "REMOTE_API_KEY": "sk-real-secret-value-12345",
        "GITHUB_TOKEN": "ghp_abcdefgh",
        "DB_PASSWORD": "hunter2",
        "auth_header": "Bearer xyz",
        "some_credential_blob": "abc",
    }

    masked = mask_secrets(data)

    assert masked["REMOTE_API_KEY"] == MASK_VALUE
    assert masked["GITHUB_TOKEN"] == MASK_VALUE
    assert masked["DB_PASSWORD"] == MASK_VALUE
    assert masked["auth_header"] == MASK_VALUE
    assert masked["some_credential_blob"] == MASK_VALUE
    # Orijinal deger hicbir yerde gorunmemeli.
    assert "sk-real-secret-value-12345" not in json.dumps(masked)
    assert "hunter2" not in json.dumps(masked)


def test_mask_secrets_leaves_non_sensitive_keys_untouched():
    data = {"OLLAMA_HOST": "http://localhost:11434", "MODEL_PROVIDER_ORDER": "ollama"}

    masked = mask_secrets(data)

    assert masked == data


def test_mask_secrets_leaves_empty_secret_values_as_is():
    """Bos/None bir 'secret' degeri maskelenmemeli -- 'tanimli degil'
    teshisinin kendisi degerli bilgi, gizlenecek bir sey yok."""
    data = {"REMOTE_API_KEY": "", "GITHUB_TOKEN": None}

    masked = mask_secrets(data)

    assert masked["REMOTE_API_KEY"] == ""
    assert masked["GITHUB_TOKEN"] is None


def test_mask_secrets_recurses_into_nested_dicts():
    data = {"config": {"REMOTE_API_KEY": "sk-nested-secret"}, "other": {"host": "localhost"}}

    masked = mask_secrets(data)

    assert masked["config"]["REMOTE_API_KEY"] == MASK_VALUE
    assert masked["other"]["host"] == "localhost"


def test_build_config_fingerprint_masks_both_config_and_env():
    config_dict = {"ollama_host": "http://localhost:11434", "remote_api_key": "sk-should-be-masked"}
    env_dict = {"REMOTE_API_KEY": "sk-env-secret", "PATH": "/usr/bin"}

    fingerprint = build_config_fingerprint(config_dict, env_dict)

    assert fingerprint["config"]["remote_api_key"] == MASK_VALUE
    assert fingerprint["config"]["ollama_host"] == "http://localhost:11434"
    assert fingerprint["env"]["REMOTE_API_KEY"] == MASK_VALUE
    assert fingerprint["env"]["PATH"] == "/usr/bin"
    full_text = json.dumps(fingerprint)
    assert "sk-should-be-masked" not in full_text
    assert "sk-env-secret" not in full_text


def test_collect_audit_slice_filters_by_timestamp_window(tmp_path):
    audit_path = tmp_path / "audit.log.jsonl"
    lines = [
        {"timestamp": "2026-08-13T00:00:00+00:00", "status": "SUCCESS"},
        {"timestamp": "2026-08-13T01:00:00+00:00", "status": "FALLBACK"},
        {"timestamp": "2026-08-13T02:00:00+00:00", "status": "SUCCESS"},
    ]
    audit_path.write_text("\n".join(json.dumps(ln) for ln in lines) + "\n", encoding="utf-8")

    sliced = collect_audit_slice(audit_path, "2026-08-13T00:30:00+00:00", "2026-08-13T01:30:00+00:00")

    assert len(sliced) == 1
    assert sliced[0]["status"] == "FALLBACK"


def test_collect_audit_slice_skips_malformed_lines(tmp_path):
    audit_path = tmp_path / "audit.log.jsonl"
    audit_path.write_text(
        'not valid json\n{"timestamp": "2026-08-13T01:00:00+00:00", "status": "OK"}\n', encoding="utf-8"
    )

    sliced = collect_audit_slice(audit_path, "2026-08-13T00:00:00+00:00", "2026-08-13T02:00:00+00:00")

    assert len(sliced) == 1
    assert sliced[0]["status"] == "OK"
