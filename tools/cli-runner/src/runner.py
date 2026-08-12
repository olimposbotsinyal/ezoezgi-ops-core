"""Whitelist tabanli, guvenli CLI runner (PLAN.md T12).

Guvenlik prensipleri:
  - `shell=True` KESINLIKLE kullanilmaz -- komutlar liste halinde argumanlarla
    (`subprocess.run([executable, *args], shell=False, ...)`) calistirilir,
    boylece shell injection riski (`;`, `&&`, `|` vb.) yapisal olarak yoktur.
  - Yalnizca `config/cli_whitelist.json`'da tanimli komut adlari calistirilabilir;
    whitelist disi bir `command_name` hicbir sekilde process baslatmaz, sadece
    deterministic bir hata kodu (`NOT_WHITELISTED`) doner.
  - Her komut icin timeout zorunludur (whitelist girdisinde `timeout_seconds`
    yoksa `default_timeout_seconds`, o da yoksa `DEFAULT_TIMEOUT_SECONDS`
    kullanilir) -- sinirsiz suren bir cagri asla olmaz.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

DEFAULT_WHITELIST_PATH = Path("config/cli_whitelist.json")
DEFAULT_TIMEOUT_SECONDS = 5

STATUS_OK = "ok"
STATUS_ERROR = "error"

ERROR_NOT_WHITELISTED = "NOT_WHITELISTED"
ERROR_EXECUTABLE_NOT_FOUND = "EXECUTABLE_NOT_FOUND"
ERROR_TIMEOUT = "TIMEOUT"
ERROR_EXECUTION_FAILED = "EXECUTION_FAILED"


class CliWhitelistError(ValueError):
    """Whitelist config dosyasi okunamadi/gecersiz formatta oldugunda firlatilir."""


def _error_result(error_code: str, message: str) -> dict[str, Any]:
    return {
        "status": STATUS_ERROR,
        "error_code": error_code,
        "stdout": "",
        "stderr": message,
        "returncode": None,
    }


def _load_whitelist(path: str | Path) -> dict[str, Any]:
    whitelist_path = Path(path)
    try:
        data = json.loads(whitelist_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CliWhitelistError(f"Whitelist okunamadi ({whitelist_path}): {exc}") from exc

    if not isinstance(data.get("commands"), dict):
        raise CliWhitelistError("Whitelist 'commands' alani bir obje olmali")

    return data


def run_command(
    command_name: str,
    args: list[str] | None = None,
    context: dict[str, Any] | None = None,
    whitelist_path: str | Path = DEFAULT_WHITELIST_PATH,
) -> dict[str, Any]:
    """Whitelist'te kayitli bir komutu shell olmadan calistirir.

    Donen sozluk her zaman `status` (`ok`/`error`), `error_code` (basarili ise
    None), `stdout`, `stderr`, `returncode` alanlarini icerir. Bu fonksiyon
    hicbir zaman exception firlatmaz (whitelist okunamasa dahi kontrollu bir
    hata sozlugu doner) -- orchestrator'i cokertmemesi icin.
    """
    args = args or []
    context = context or {}  # ileride yetki/kullanici bilgisi tasimak icin ayrildi

    try:
        whitelist = _load_whitelist(whitelist_path)
    except CliWhitelistError as exc:
        return _error_result(ERROR_NOT_WHITELISTED, str(exc))

    commands = whitelist["commands"]
    default_timeout = whitelist.get("default_timeout_seconds", DEFAULT_TIMEOUT_SECONDS)

    if command_name not in commands:
        return _error_result(
            ERROR_NOT_WHITELISTED, f"'{command_name}' whitelist'te kayitli degil."
        )

    entry = commands[command_name]
    executable_name = entry.get("executable", command_name)
    resolved_executable = shutil.which(executable_name)

    if not resolved_executable:
        return _error_result(
            ERROR_EXECUTABLE_NOT_FOUND, f"'{executable_name}' bu sistemde bulunamadi."
        )

    timeout_seconds = entry.get("timeout_seconds", default_timeout)

    try:
        completed = subprocess.run(
            [resolved_executable, *args],
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return _error_result(
            ERROR_TIMEOUT, f"Komut {timeout_seconds}s icinde tamamlanmadi."
        )
    except OSError as exc:
        return _error_result(ERROR_EXECUTION_FAILED, str(exc))

    if completed.returncode != 0:
        return {
            "status": STATUS_ERROR,
            "error_code": ERROR_EXECUTION_FAILED,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "returncode": completed.returncode,
        }

    return {
        "status": STATUS_OK,
        "error_code": None,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "returncode": completed.returncode,
    }
