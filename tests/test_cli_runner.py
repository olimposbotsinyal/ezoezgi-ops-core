"""T12 -- runner.py (whitelist tabanli CLI runner) testleri."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from runner import (
    ERROR_EXECUTABLE_NOT_FOUND,
    ERROR_NOT_WHITELISTED,
    ERROR_TIMEOUT,
    STATUS_ERROR,
    STATUS_OK,
    run_command,
)


def _write_whitelist(tmp_path, commands: dict, default_timeout: float = 5) -> Path:
    whitelist_path = tmp_path / "cli_whitelist.json"
    whitelist_path.write_text(
        json.dumps({"default_timeout_seconds": default_timeout, "commands": commands}),
        encoding="utf-8",
    )
    return whitelist_path


def test_allowed_command_runs_successfully_with_real_project_whitelist():
    # Projenin gercek config/cli_whitelist.json'unu kullanir (DEFAULT_WHITELIST_PATH).
    result = run_command("echo", args=["merhaba"])

    assert result["status"] == STATUS_OK
    assert result["error_code"] is None
    assert "merhaba" in result["stdout"]
    assert result["returncode"] == 0


def test_disallowed_command_is_blocked_with_deterministic_error_code(tmp_path):
    whitelist_path = _write_whitelist(tmp_path, {"echo": {"executable": "echo"}})

    result = run_command("rm", args=["-rf", "/"], whitelist_path=whitelist_path)

    assert result["status"] == STATUS_ERROR
    assert result["error_code"] == ERROR_NOT_WHITELISTED
    assert result["returncode"] is None


def test_timeout_behavior_is_enforced(tmp_path):
    whitelist_path = _write_whitelist(
        tmp_path,
        {
            "slow": {
                "executable": sys.executable,
                "timeout_seconds": 0.3,
            }
        },
    )

    result = run_command(
        "slow",
        args=["-c", "import time; time.sleep(2)"],
        whitelist_path=whitelist_path,
    )

    assert result["status"] == STATUS_ERROR
    assert result["error_code"] == ERROR_TIMEOUT


def test_whitelisted_but_missing_executable_returns_deterministic_error(tmp_path):
    whitelist_path = _write_whitelist(
        tmp_path, {"ghost": {"executable": "this-binary-does-not-exist-anywhere"}}
    )

    result = run_command("ghost", whitelist_path=whitelist_path)

    assert result["status"] == STATUS_ERROR
    assert result["error_code"] == ERROR_EXECUTABLE_NOT_FOUND


def test_missing_whitelist_file_does_not_raise(tmp_path):
    result = run_command("echo", whitelist_path=tmp_path / "does-not-exist.json")

    assert result["status"] == STATUS_ERROR
    assert result["error_code"] == ERROR_NOT_WHITELISTED


def test_never_invokes_shell(tmp_path):
    whitelist_path = _write_whitelist(tmp_path, {"echo": {"executable": "echo"}})

    fake_completed = subprocess.CompletedProcess(
        args=["echo", "hi"], returncode=0, stdout="hi\n", stderr=""
    )
    with patch("runner.subprocess.run", return_value=fake_completed) as mock_run:
        run_command("echo", args=["hi"], whitelist_path=whitelist_path)

    _, kwargs = mock_run.call_args
    assert kwargs.get("shell") is False


def test_nonzero_exit_code_is_reported_as_error(tmp_path):
    # sys.executable ile kasitli olarak basarisiz cikan bir python komutu.
    whitelist_path = _write_whitelist(tmp_path, {"fail": {"executable": sys.executable}})

    result = run_command("fail", args=["-c", "import sys; sys.exit(3)"], whitelist_path=whitelist_path)

    assert result["status"] == STATUS_ERROR
    assert result["returncode"] == 3
