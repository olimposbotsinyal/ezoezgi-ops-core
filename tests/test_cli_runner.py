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
    """`echo` gercekte harici bir yurutulebilir olarak COZULMEYEBILIR
    (bkz. B037 -- saf PowerShell PATH'inde `echo` bir kabuk ic komutudur,
    `shutil.which` None doner ve saf-Python fallback devreye girer,
    subprocess.run'a HIC UGRAMADAN). Bu test ozellikle "harici bir
    yurutulebilir cozuldugunde `shell=True` KULLANILMIYOR" ozelligini
    dogruluyor -- bu yuzden gercek makine PATH durumundan BAGIMSIZ
    olmasi icin `shutil.which` de sahteleniyor, boylece harici-yol
    deterministik olarak alinir."""
    whitelist_path = _write_whitelist(tmp_path, {"echo": {"executable": "echo"}})

    fake_completed = subprocess.CompletedProcess(
        args=["echo", "hi"], returncode=0, stdout="hi\n", stderr=""
    )
    with (
        patch("runner.shutil.which", return_value="C:\\fake\\path\\echo.exe"),
        patch("runner.subprocess.run", return_value=fake_completed) as mock_run,
    ):
        run_command("echo", args=["hi"], whitelist_path=whitelist_path)

    _, kwargs = mock_run.call_args
    assert kwargs.get("shell") is False


def test_nonzero_exit_code_is_reported_as_error(tmp_path):
    # sys.executable ile kasitli olarak basarisiz cikan bir python komutu.
    whitelist_path = _write_whitelist(tmp_path, {"fail": {"executable": sys.executable}})

    result = run_command("fail", args=["-c", "import sys; sys.exit(3)"], whitelist_path=whitelist_path)

    assert result["status"] == STATUS_ERROR
    assert result["returncode"] == 3


# --- B037: platform-bagimsiz komut cozumlemesi (PowerShell/Git Bash paritesi) ---
#
# Bu testler `shutil.which` GERCEKTEN kararliligi sahteleyerek (mock) HER
# platformda/kabukta deterministik calisir -- gercek makine PATH icerigine
# BAGIMLI DEGILDIR (bu tam olarak B037'nin kok nedeniydi: gercek PATH
# icerigi PowerShell'de/Git Bash'te FARKLIYDI). Boylece "PowerShell'de de
# Git Bash'te de ayni davranis" iddiasi, hangi kabuktan calistirilirsa
# calistirilsin AYNI SONUCU verir.


def test_builtin_fallback_used_when_which_fails_for_echo(tmp_path):
    """`shutil.which` basarisiz olduğunda (B037'nin gercek makinedeki
    SAF PowerShell kosulunu simule eder), saf-Python `echo` esdegeri
    devreye girmeli -- EXECUTABLE_NOT_FOUND DEGIL."""
    whitelist_path = _write_whitelist(tmp_path, {"echo": {"executable": "echo"}})

    with patch("runner.shutil.which", return_value=None):
        result = run_command("echo", args=["Merhaba", "Dunya"], whitelist_path=whitelist_path)

    assert result["status"] == STATUS_OK
    assert result["error_code"] is None
    assert result["stdout"] == "Merhaba Dunya\n"
    assert result["returncode"] == 0
    assert result["resolved_via"] == "python_native_fallback"


def test_builtin_fallback_used_when_which_fails_for_pwd(tmp_path):
    whitelist_path = _write_whitelist(tmp_path, {"pwd": {"executable": "pwd"}})

    with patch("runner.shutil.which", return_value=None):
        result = run_command("pwd", whitelist_path=whitelist_path)

    assert result["status"] == STATUS_OK
    assert result["stdout"].strip() != ""
    assert result["resolved_via"] == "python_native_fallback"


def test_external_executable_path_used_when_which_succeeds(tmp_path):
    """`shutil.which` basarili oldugunda (Git Bash'in gercek `echo.exe`
    bulmasini simule eder), harici yurutulebilir yolu kullanilmali --
    saf-Python fallback DEGIL."""
    whitelist_path = _write_whitelist(tmp_path, {"echo": {"executable": "echo"}})

    fake_completed = subprocess.CompletedProcess(
        args=["echo", "hi"], returncode=0, stdout="hi\n", stderr=""
    )
    with (
        patch("runner.shutil.which", return_value="C:\\fake\\git\\usr\\bin\\echo.exe"),
        patch("runner.subprocess.run", return_value=fake_completed),
    ):
        result = run_command("echo", args=["hi"], whitelist_path=whitelist_path)

    assert result["status"] == STATUS_OK
    assert result["resolved_via"] == "external_executable"


def test_builtin_echo_output_matches_real_external_echo_shape(tmp_path):
    """Saf-Python `echo` fallback'i, harici bir `echo` cagrisiyla BIREBIR
    ayni cikti seklini (argumanlar bosluk-birlestirilmis + tek newline,
    returncode 0) uretmeli -- caller (echo_runner.py) icin fark edilmez
    olmali."""
    whitelist_path = _write_whitelist(tmp_path, {"echo": {"executable": "echo"}})

    with patch("runner.shutil.which", return_value=None):
        fallback_result = run_command("echo", args=["Test", "Deger"], whitelist_path=whitelist_path)

    fake_completed = subprocess.CompletedProcess(
        args=["echo", "Test", "Deger"], returncode=0, stdout="Test Deger\n", stderr=""
    )
    with (
        patch("runner.shutil.which", return_value="C:\\fake\\echo.exe"),
        patch("runner.subprocess.run", return_value=fake_completed),
    ):
        external_result = run_command("echo", args=["Test", "Deger"], whitelist_path=whitelist_path)

    assert fallback_result["stdout"] == external_result["stdout"]
    assert fallback_result["status"] == external_result["status"] == STATUS_OK
    assert fallback_result["returncode"] == external_result["returncode"] == 0


def test_unknown_missing_executable_still_errors_no_silent_fallback(tmp_path):
    """B037 fallback yalnizca BILINEN, SABIT bir komut kumesi (echo, pwd)
    icin gecerlidir -- bilinmeyen bir komut icin eski davranis (acik
    hata) AYNEN korunmali, sessizce bir seyler UYDURULMAZ."""
    whitelist_path = _write_whitelist(
        tmp_path, {"ghost": {"executable": "this-binary-does-not-exist-anywhere"}}
    )

    with patch("runner.shutil.which", return_value=None):
        result = run_command("ghost", whitelist_path=whitelist_path)

    assert result["status"] == STATUS_ERROR
    assert result["error_code"] == ERROR_EXECUTABLE_NOT_FOUND


def test_echo_runner_succeeds_when_echo_resolves_via_builtin_fallback(monkeypatch):
    """Uctan uca: `echo_runner.run_echo()`, `shutil.which("echo")`
    basarisiz oldugu (SAF PowerShell PATH'i simule edilir) bir ortamda
    bile basariyla calismali -- B037'nin asil belirtisiydi bu (orchestrator
    smoke testleri `EXECUTABLE_NOT_FOUND` ile basarisiz oluyordu)."""
    import echo_runner

    with patch("runner.shutil.which", return_value=None):
        result = echo_runner.run_echo({"original_tr": "Ezo, 'merhaba dunya' yaz"})

    assert result["status"] == "ok"
    assert result["value"] == "Merhaba dunya"
