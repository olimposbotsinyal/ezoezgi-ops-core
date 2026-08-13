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

Platform-bagimsiz komut cozumlemesi (B037 duzeltmesi):
  `shutil.which("echo")`/`shutil.which("pwd")`, Git Bash'in PATH'inde
  (MSYS/Git-for-Windows Unix araclari) gercek birer `echo.exe`/`pwd.exe`
  buldugundan orada calisir, ama SAF bir Windows PowerShell/cmd
  surecinin PATH'inde bu araclar YOKTUR -- orada `echo`/`pwd`, yalnizca
  o KABUGUN kendi ic komutlaridir (gercek, bagimsiz bir yurutulebilir
  DEGILDIR), bu yuzden `shutil.which` `None` doner ve cagri
  `EXECUTABLE_NOT_FOUND` ile basarisiz olurdu -- SAF PowerShell'de
  calisirken Git Bash'te calisan ayni komut icin FARKLI davranis.
  Bunu, bir shell'e (`cmd.exe /c ...`) fallback YAPARAK cozmek, yukaridaki
  "shell=True asla kullanilmaz" ilkesini yeniden (dolayli olarak) ihlal
  ederdi -- cmd.exe'nin kendi ayristirmasi, whitelist'li bir komuta
  gelen (potansiyel olarak kullanici turevli) argumanlarda metakarakter
  yorumlamasi riski tasir. Bunun yerine, `shutil.which` basarisiz
  oldugunda, bu KUCUK ve SABIT komut kumesi icin (`_BUILTIN_FALLBACKS`)
  DAVRANISI birebir eslestiren SAF PYTHON esdegerleri kullanilir --
  hicbir subprocess/shell yuzeyi olmadigindan bu, harici bir yurutulebilir
  bulmaktan daha az degil, DAHA GUVENLIDIR, ve HER platformda (Windows
  PowerShell/cmd, Git Bash, Linux, macOS) birebir ayni ciktiyi uretir.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

DEFAULT_WHITELIST_PATH = Path("config/cli_whitelist.json")
DEFAULT_TIMEOUT_SECONDS = 5

STATUS_OK = "ok"
STATUS_ERROR = "error"

ERROR_NOT_WHITELISTED = "NOT_WHITELISTED"
ERROR_EXECUTABLE_NOT_FOUND = "EXECUTABLE_NOT_FOUND"
ERROR_TIMEOUT = "TIMEOUT"
ERROR_EXECUTION_FAILED = "EXECUTION_FAILED"

RESOLVED_VIA_EXTERNAL = "external_executable"
RESOLVED_VIA_BUILTIN_FALLBACK = "python_native_fallback"


def _builtin_echo(args: list[str]) -> subprocess.CompletedProcess:
    """Gercek `echo <args>` ile BIREBIR ayni cikti: argumanlar bosluk ile
    birlestirilir + tek bir sondaki newline eklenir, returncode 0."""
    stdout = (" ".join(args) + "\n") if args else "\n"
    return subprocess.CompletedProcess(args=["echo", *args], returncode=0, stdout=stdout, stderr="")


def _builtin_pwd(args: list[str]) -> subprocess.CompletedProcess:
    """Gercek `pwd` ile BIREBIR ayni cikti: calisma dizini + sondaki newline."""
    stdout = os.getcwd() + "\n"
    return subprocess.CompletedProcess(args=["pwd", *args], returncode=0, stdout=stdout, stderr="")


# Yalnizca `shutil.which()` GERCEKTEN basarisiz oldugunda (hicbir bagimsiz
# yurutulebilir bulunamadiginda) devreye giren, SABIT ve KUCUK bir esleme --
# `config/cli_whitelist.json`'daki komut adi DEGIL, `executable` alaniyla
# eslesir (whitelist'teki `command_name` ile `executable` farkli olabilir).
_BUILTIN_FALLBACKS: dict[str, Callable[[list[str]], subprocess.CompletedProcess]] = {
    "echo": _builtin_echo,
    "pwd": _builtin_pwd,
}


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
    timeout_seconds = entry.get("timeout_seconds", default_timeout)

    if resolved_executable:
        resolved_via = RESOLVED_VIA_EXTERNAL
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
    else:
        # Bagimsiz bir yurutulebilir bulunamadi -- platform-bagimsiz, saf
        # Python esdegeri varsa (bkz. modul docstring'i, B037) onu kullan.
        # Yoksa (bilinmeyen bir komut) eski davranis aynen korunur.
        builtin = _BUILTIN_FALLBACKS.get(executable_name)
        if builtin is None:
            return _error_result(
                ERROR_EXECUTABLE_NOT_FOUND, f"'{executable_name}' bu sistemde bulunamadi."
            )
        resolved_via = RESOLVED_VIA_BUILTIN_FALLBACK
        try:
            completed = builtin(args)
        except Exception as exc:  # saf-Python fallback'in kendisi bile asla cokertmemeli
            return _error_result(ERROR_EXECUTION_FAILED, str(exc))

    if completed.returncode != 0:
        return {
            "status": STATUS_ERROR,
            "error_code": ERROR_EXECUTION_FAILED,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "returncode": completed.returncode,
            "resolved_via": resolved_via,
        }

    return {
        "status": STATUS_OK,
        "error_code": None,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "returncode": completed.returncode,
        "resolved_via": resolved_via,
    }
