"""scripts/repo_guard.py testleri.

Senaryolar: manifest var (pass), manifest yok (fail), kritik klasör eksik
(fail) + bonus: yanlış repo_slug (fail), gerçek repo üzerinde tam entegrasyon
(pass), script yanlış konumdan çalıştırılırsa (fail).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_guard import RepoGuardError, check_manifest, run_guard

VALID_MANIFEST = """
project_name: "EzoEzgi (ASAF Core)"
repo_slug: ezoezgi-asaf-core
canonical_paths:
  - apps/
  - services/
"""


def _make_fake_repo(tmp_path: Path, manifest_content: str | None, dirs: list[str]) -> Path:
    for relative_dir in dirs:
        (tmp_path / relative_dir).mkdir(parents=True, exist_ok=True)
    if manifest_content is not None:
        (tmp_path / "PROJECT_IDENTITY.yaml").write_text(manifest_content, encoding="utf-8")
    return tmp_path


# --- Senaryo 1: manifest var => pass -----------------------------------


def test_manifest_present_and_valid_passes(tmp_path):
    repo_root = _make_fake_repo(tmp_path, VALID_MANIFEST, ["apps", "services"])

    manifest = check_manifest(repo_root)

    assert manifest["repo_slug"] == "ezoezgi-asaf-core"


# --- Senaryo 2: manifest yok => fail -------------------------------------


def test_missing_manifest_fails(tmp_path):
    repo_root = _make_fake_repo(tmp_path, None, ["apps", "services"])

    with pytest.raises(RepoGuardError, match="Manifest bulunamadı"):
        check_manifest(repo_root)


# --- Senaryo 3: kritik klasör eksik => fail ------------------------------


def test_missing_canonical_path_fails(tmp_path):
    # Manifest 'services/' istiyor ama yalnizca 'apps/' olusturuldu.
    repo_root = _make_fake_repo(tmp_path, VALID_MANIFEST, ["apps"])

    with pytest.raises(RepoGuardError, match="kritik klasör"):
        check_manifest(repo_root)


# --- Ek vakalar -----------------------------------------------------------


def test_wrong_repo_slug_fails(tmp_path):
    mismatched = VALID_MANIFEST.replace(
        "repo_slug: ezoezgi-asaf-core", "repo_slug: some-other-project"
    )
    repo_root = _make_fake_repo(tmp_path, mismatched, ["apps", "services"])

    with pytest.raises(RepoGuardError, match="repo_slug"):
        check_manifest(repo_root)


def test_invalid_yaml_manifest_fails(tmp_path):
    repo_root = tmp_path
    (repo_root / "PROJECT_IDENTITY.yaml").write_text(
        "repo_slug: [unclosed", encoding="utf-8"
    )

    with pytest.raises(RepoGuardError):
        check_manifest(repo_root)


def test_run_guard_passes_for_real_repo():
    # Gercek repo uzerinde tam entegrasyon: git rev-parse + script konumu +
    # manifest dogrulamasi.
    assert run_guard() == "REPO_GUARD_OK"


def test_run_guard_fails_when_started_outside_scripts_dir():
    # Gercek repo root'u kullan ama scripts/ disinda bir dizinden "calistir" --
    # script-konum tutarlilik kontrolu bunu reddetmeli.
    repo_root = Path(__file__).resolve().parent.parent

    with pytest.raises(RepoGuardError, match="Script konumu"):
        run_guard(start_dir=repo_root)
