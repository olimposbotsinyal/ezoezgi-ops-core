"""T5 -- config_loader.py testleri."""

from __future__ import annotations

import json
import os

import pytest

from config_loader import ConfigLoader, ConfigValidationError

VALID_CONFIG = {
    "assistant_id": "ezoezgi-core",
    "display_name": "EzoEzgi",
    "wake_aliases": ["ezo", "ezgi"],
    "language_mode": {
        "user_input": "tr",
        "internal_reasoning_task_language": "en",
        "user_output": "tr",
    },
    "alias_update_policy": {
        "editable": True,
        "source_of_truth": "config/assistant.identity.json",
        "requires_restart": False,
    },
}


def _write(path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _bump_mtime(path) -> None:
    """mtime cozunurlugu dusuk dosya sistemlerinde bile degisikligi garanti eder."""
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + 5))


def test_loads_valid_config(tmp_path):
    config_path = tmp_path / "assistant.identity.json"
    _write(config_path, VALID_CONFIG)

    loader = ConfigLoader(config_path)

    assert loader.get_wake_aliases() == ["ezo", "ezgi"]
    assert loader.get_config()["display_name"] == "EzoEzgi"


def test_missing_file_raises_on_initial_load(tmp_path):
    with pytest.raises(ConfigValidationError):
        ConfigLoader(tmp_path / "does-not-exist.json")


def test_invalid_json_raises_on_initial_load(tmp_path):
    config_path = tmp_path / "assistant.identity.json"
    config_path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(ConfigValidationError):
        ConfigLoader(config_path)


def test_missing_required_field_raises_on_initial_load(tmp_path):
    config_path = tmp_path / "assistant.identity.json"
    bad = dict(VALID_CONFIG)
    del bad["wake_aliases"]
    _write(config_path, bad)

    with pytest.raises(ConfigValidationError):
        ConfigLoader(config_path)


def test_hot_reload_picks_up_alias_change_without_restart(tmp_path):
    config_path = tmp_path / "assistant.identity.json"
    _write(config_path, VALID_CONFIG)
    loader = ConfigLoader(config_path)
    assert loader.get_wake_aliases() == ["ezo", "ezgi"]

    updated = dict(VALID_CONFIG)
    updated["wake_aliases"] = ["ezo", "ezgi", "olimpos"]
    _write(config_path, updated)
    _bump_mtime(config_path)

    # Ayni loader nesnesi, restart yok -- yalnizca bir sonraki cagri.
    assert loader.get_wake_aliases() == ["ezo", "ezgi", "olimpos"]


def test_invalid_reload_keeps_last_valid_config(tmp_path):
    config_path = tmp_path / "assistant.identity.json"
    _write(config_path, VALID_CONFIG)
    loader = ConfigLoader(config_path)
    assert loader.get_wake_aliases() == ["ezo", "ezgi"]

    config_path.write_text("{ this is broken json", encoding="utf-8")
    _bump_mtime(config_path)

    # Bozuk dosyaya ragmen crash olmaz, son gecerli config korunur.
    assert loader.get_wake_aliases() == ["ezo", "ezgi"]


def test_deleted_file_keeps_last_valid_config(tmp_path):
    config_path = tmp_path / "assistant.identity.json"
    _write(config_path, VALID_CONFIG)
    loader = ConfigLoader(config_path)
    assert loader.get_wake_aliases() == ["ezo", "ezgi"]

    config_path.unlink()

    assert loader.get_wake_aliases() == ["ezo", "ezgi"]
