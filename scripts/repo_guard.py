#!/usr/bin/env python
"""Repo Guard -- proje kimligi/izolasyon dogrulamasi.

Bu repo'nun baska bir projeyle/checkout ile karismadigini dogrular:
  1) `git rev-parse --show-toplevel` ile gercek repo root'u bulur.
  2) Scriptin kendi konumunun (`<root>/scripts/repo_guard.py`) bu repo
     root'una gore tutarli oldugunu dogrular -- boylece script baska bir
     dizine/repoya kopyalanip yanlislikla "gecerli" sonuc vermez.
  3) `<root>/PROJECT_IDENTITY.yaml` manifestinin var oldugunu, `repo_slug`
     alaninin beklenenle eslestigini ve `canonical_paths` altindaki
     klasorlerin gercekten var oldugunu dogrular.

Kullanim:
    python scripts/repo_guard.py

Basarili: stdout'a "REPO_GUARD_OK" yazar, exit code 0.
Basarisiz: stderr'e net bir hata mesaji yazar, exit code 1.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

EXPECTED_REPO_SLUG = "ezoezgi-asaf-core"
MANIFEST_FILENAME = "PROJECT_IDENTITY.yaml"
SUCCESS_MESSAGE = "REPO_GUARD_OK"


class RepoGuardError(RuntimeError):
    """Repo kimlik dogrulamasi basarisiz oldugunda firlatilir."""


def _script_dir() -> Path:
    return Path(__file__).resolve().parent


def _git_toplevel(cwd: Path) -> Path:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            shell=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RepoGuardError(f"'git rev-parse' çalıştırılamadı: {exc}") from exc

    if result.returncode != 0:
        raise RepoGuardError(
            "Bu dizin bir git deposu içinde değil (git rev-parse başarısız): "
            f"{result.stderr.strip()}"
        )

    toplevel = result.stdout.strip()
    if not toplevel:
        raise RepoGuardError("'git rev-parse --show-toplevel' boş sonuç döndürdü.")

    return Path(toplevel).resolve()


def _load_manifest(manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.is_file():
        raise RepoGuardError(f"Manifest bulunamadı: {manifest_path}")

    try:
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RepoGuardError(f"Manifest geçersiz YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise RepoGuardError("Manifest bir YAML objesi olmalı")

    return data


def check_manifest(repo_root: Path) -> dict[str, Any]:
    """Manifesti okur ve dogrular (repo_slug + canonical_paths).

    Repo root'un gercekten bir git deposu olup olmadigina veya scriptin
    konumuna bakmaz -- bu, `run_guard()`'in ayri bir sorumlulugu. Bu ayrim,
    manifest dogrulamasinin sahte bir repo ile (tmp_path) de test
    edilebilmesi icin bilerek yapildi.
    """
    manifest_path = repo_root / MANIFEST_FILENAME
    manifest = _load_manifest(manifest_path)

    repo_slug = manifest.get("repo_slug")
    if repo_slug != EXPECTED_REPO_SLUG:
        raise RepoGuardError(
            f"Manifest repo_slug uyuşmuyor: bulunan={repo_slug!r}, "
            f"beklenen={EXPECTED_REPO_SLUG!r}. Bu repo başka bir projeye "
            "ait olabilir."
        )

    canonical_paths = manifest.get("canonical_paths") or []
    if not isinstance(canonical_paths, list) or not canonical_paths:
        raise RepoGuardError("Manifest 'canonical_paths' boş olmayan bir liste olmalı")

    missing = [p for p in canonical_paths if not (repo_root / p).is_dir()]
    if missing:
        raise RepoGuardError(
            "Manifestte tanımlı kritik klasör(ler) eksik: " + ", ".join(missing)
        )

    return manifest


def run_guard(start_dir: Path | None = None) -> str:
    """Tum guard kontrollerini calistirir.

    Basarili olursa `SUCCESS_MESSAGE` ("REPO_GUARD_OK") dondurur.
    Basarisiz olursa `RepoGuardError` firlatir (mesaji kullaniciya
    gosterilebilecek netlikte).
    """
    script_dir = (start_dir or _script_dir()).resolve()
    repo_root = _git_toplevel(script_dir)

    expected_script_dir = (repo_root / "scripts").resolve()
    if script_dir != expected_script_dir:
        raise RepoGuardError(
            f"Script konumu beklenenle uyuşmuyor: {script_dir} != "
            f"{expected_script_dir}. Script farklı bir repoya kopyalanmış "
            "olabilir."
        )

    check_manifest(repo_root)
    return SUCCESS_MESSAGE


def main() -> None:
    try:
        result = run_guard()
    except RepoGuardError as exc:
        print(f"REPO_GUARD_FAIL: {exc}", file=sys.stderr)
        sys.exit(1)

    print(result)
    sys.exit(0)


if __name__ == "__main__":
    main()
