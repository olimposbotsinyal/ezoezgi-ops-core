"""RUN_ECHO task handler.

Girdi TR cumlesinden echo edilecek degeri cikarir (mock, basit sezgisel) ve
gercek calistirmayi artik tekil bir fonksiyon yerine genel, whitelist tabanli
`runner.run_command()` (PLAN.md T12) uzerinden yapar -- boylece orchestrator
gercekten "cli-runner"a gidiyor, dogrudan bir Python string'i degil.
"""

from __future__ import annotations

import re
from typing import Any

from runner import run_command

_QUOTED_RE = re.compile(r"['\"]([^'\"]+)['\"]")
_ILE_YAZ_RE = re.compile(r"\bile\b\s+(.*?)\s+\byaz\w*\b", re.IGNORECASE)


def _extract_echo_value(original_tr: str | None) -> str:
    if not original_tr:
        return ""

    quoted = _QUOTED_RE.search(original_tr)
    if quoted:
        return quoted.group(1).strip()

    ile_yaz = _ILE_YAZ_RE.search(original_tr)
    if ile_yaz:
        return ile_yaz.group(1).strip()

    return ""


def run_echo(task: dict[str, Any]) -> dict[str, Any]:
    """RUN_ECHO handler'i -- `runner.run_command("echo", [deger])` cagirir."""
    value = _extract_echo_value(task.get("original_tr"))
    if not value:
        return {"value": "", "status": "ok"}

    capitalized = value[0].upper() + value[1:]
    cli_result = run_command("echo", [capitalized])

    if cli_result["status"] != "ok":
        return {
            "value": "",
            "status": "error",
            "error_code": cli_result.get("error_code"),
            "error": cli_result.get("stderr"),
        }

    # echo.exe genelde stdout'a sondaki newline ile yazar; temizle.
    echoed = cli_result["stdout"].strip()
    return {"value": echoed or capitalized, "status": "ok"}
