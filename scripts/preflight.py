#!/usr/bin/env python
"""Preflight kontrol -- repo guard + kısa sağlık kontrolü.

Kullanım:
    python scripts/preflight.py

Önce `repo_guard.run_guard()` çağrılır (repo kimliği/izolasyon
doğrulaması), ardından birkaç hızlı dosya/sürüm kontrolü yapılır. Tüm
sonuçlar tek bir PASS/FAIL tablosu olarak yazdırılır. Herhangi bir kontrol
FAIL ise exit code 1 döner (CI/otomasyon için).
"""

from __future__ import annotations

import sys
from pathlib import Path

from repo_guard import RepoGuardError, run_guard

REPO_ROOT = Path(__file__).resolve().parent.parent
MIN_PYTHON = (3, 10)


def _check_repo_guard() -> tuple[bool, str]:
    try:
        result = run_guard()
    except RepoGuardError as exc:
        return False, str(exc)
    return True, result


def _check_python_version() -> tuple[bool, str]:
    ok = sys.version_info[:2] >= MIN_PYTHON
    version_str = ".".join(str(part) for part in sys.version_info[:3])
    min_str = ".".join(str(part) for part in MIN_PYTHON)
    return ok, f"{version_str} (>= {min_str} gerekli)"


def _check_file_exists(relative_path: str) -> tuple[bool, str]:
    exists = (REPO_ROOT / relative_path).is_file()
    return exists, "bulundu" if exists else "BULUNAMADI"


def run_preflight() -> list[tuple[str, bool, str]]:
    """Tum kontrolleri calistirir, (isim, ok, detay) listesi dondurur."""
    checks_raw = [
        ("Repo Guard", _check_repo_guard()),
        ("Python version", _check_python_version()),
        ("config/cli_whitelist.json", _check_file_exists("config/cli_whitelist.json")),
        ("docs/PLAN.md", _check_file_exists("docs/PLAN.md")),
    ]
    return [(name, ok, detail) for name, (ok, detail) in checks_raw]


def main() -> None:
    # Windows konsollari varsayilan olarak UTF-8 kullanmayabilir; TR karakterler
    # bu durumda mojibake gorunur (veri bozulmaz, bkz. scripts/e2e_demo.py).
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    checks = run_preflight()

    name_width = max(len(name) for name, _, _ in checks)
    print("Preflight Kontrol -- EzoEzgi Ops")
    print("=" * 44)
    for name, ok, detail in checks:
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {name.ljust(name_width)}  {detail}")
    print("-" * 44)

    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    print(f"Sonuç: {passed}/{total} PASS")

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
