"""Gozlemlenebilirlik (observability) go-live imza (sign-off) dosyasi --
gorev paketinin nihai, insan-onayli kanit paketini uretir.

Toplar:
  - git SHA (HEAD) + kisa log
  - test ozeti (tam pytest suite calistirilir -- `--skip-tests` ile
    atlanabilir, ama VARSAYILAN davranis GERCEK bir calistirmadir;
    "muhtemelen gecer" gibi bir tahmin asla yazilmaz)
  - EN SON `gate_results.json` (varsa, bkz. `run_observability_gates.ps1`)
    -- yoksa acikca "gate henuz calistirilmadi" notu, fabrike edilmis
    bir sonuc ASLA yazilmaz
  - bilinen sinirlamalar (bu depoda zaten belgelenmis, sabit bir liste)
  - rollback komutlari (`scripts/ops/rollback_observability.ps1` referansi)

GO/NO-GO onerisi (`go_no_go_recommendation`) SAF bir fonksiyondur --
girdi olarak yalnizca test exit code + gate sonuclarini alir, hicbir
yan etkisi yoktur, deterministik test edilebilir.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT_DEFAULT = Path("d:/Projects/ezoezgi-ops")

GO = "GO"
CONDITIONAL_GO = "CONDITIONAL-GO"
NO_GO = "NO-GO"

# Bu depoda zaten dokumante edilmis, bilinen sinirlamalar -- BACKLOG/RUNBOOK
# ile senkron tutulmalidir (kaynak referanslariyla, tekrar tam metin
# COPY-PASTE edilmez -- staleness riskini azaltmak icin).
KNOWN_LIMITATIONS = [
    "Bu makinede Prometheus/Alertmanager KURULU DEGIL -- canli scrape/"
    "alert-firing/routing pipeline'i dogrulanamaz (bkz. "
    "docs/ops/MONITORING_STACK_RUNBOOK.md 'Bu makinenin gercek durumu').",
    "Gate D (Alertmanager alma yolu), bu makinede SKIPPED doner -- "
    "altyapi eksikligi nedeniyle, fabrike edilmis bir PASS degil.",
    "/metrics cross-process gorunurlugu eventual-consistency'dir -- "
    "gercek zamanli push degildir (bkz. docs/ops/MONITORING_STACK_RUNBOOK.md "
    "'Bilinen odunlesimler').",
    "Alert esikleri (ALERT_NULL_INTENT_WARN/CRIT, "
    "ALERT_FALLBACK_SPIKE_MULTIPLIER) bu depoda GERCEK uretim trafigiyle "
    "kalibre EDILMEMISTIR -- bkz. docs/ops/ALERT_PLAYBOOK_MODEL_GATEWAY.md "
    "'Ilk vs kalibre edilmis esikler'.",
    "tools/cli-runner/src/runner.py, shutil.which('echo') ile calisir -- "
    "saf bir PowerShell surecinin PATH'inde basarisiz olabilir "
    "(EXECUTABLE_NOT_FOUND); classify/fallback sozlesmesiyle ilgisiz, "
    "onceden var olan bir ortam sinirlamasi.",
]


@dataclass
class RunSummary:
    ran: bool
    exit_code: int | None
    summary_line: str


def get_git_sha(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip() if result.returncode == 0 else "UNKNOWN"
    except (subprocess.TimeoutExpired, OSError):
        return "UNKNOWN"


def get_git_short_log(repo_root: Path, n: int = 8) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "log", f"-{n}", "--oneline"], cwd=repo_root, capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return []
        return [ln for ln in result.stdout.splitlines() if ln.strip()]
    except (subprocess.TimeoutExpired, OSError):
        return []


def run_full_test_suite(python_exe: str, repo_root: Path) -> RunSummary:
    try:
        result = subprocess.run(
            [python_exe, "-m", "pytest"], cwd=repo_root, capture_output=True, text=True, timeout=300
        )
        lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
        summary_line = lines[-1] if lines else ""
        return RunSummary(ran=True, exit_code=result.returncode, summary_line=summary_line)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return RunSummary(ran=True, exit_code=-1, summary_line=f"pytest calistirilamadi: {exc}")


def find_latest_gate_results(repo_root: Path) -> dict[str, Any] | None:
    """`reports/go_live_gates_*/gate_results.json` altinda EN SON
    (mtime'a gore) dosyayi bulur, ayristirir. Hicbiri yoksa/bozuksa
    `None` doner -- caller bunu "gate henuz calistirilmadi/gecersiz"
    olarak acikca raporlar, ASLA varsayilan bir PASS uydurmaz."""
    candidates = sorted(
        repo_root.glob("reports/go_live_gates_*/gate_results.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return None

    sys.path.insert(0, str(repo_root / "scripts" / "ops"))
    from observability_gates_core import parse_gate_results_json

    try:
        data = parse_gate_results_json(candidates[0])
        data["_source_file"] = str(candidates[0])
        return data
    except ValueError:
        return None


def go_no_go_recommendation(*, test_exit_code: int | None, gate_results: dict[str, Any] | None) -> tuple[str, str]:
    """SAF karar fonksiyonu -- yan etkisi yok, deterministik test edilir.
    Doner: (karar, gerekce)."""
    if test_exit_code is None:
        return NO_GO, "Test suite calistirilmadi (--skip-tests) -- test sonucu olmadan GO verilemez."
    if test_exit_code != 0:
        return NO_GO, f"Test suite basarisiz (exit_code={test_exit_code}) -- regresyon suphesi."

    if gate_results is None:
        return NO_GO, "Go-live gate'leri henuz hic calistirilmamis (gate_results.json bulunamadi)."

    gate_exit = gate_results.get("overall_exit_code")
    if gate_exit == 2:
        return NO_GO, "En az bir go-live gate'i FAIL -- bkz. gate_report.md."
    if gate_exit == 1:
        return (
            CONDITIONAL_GO,
            "Test suite yesil, gate'ler PARTIAL/SKIPPED (genellikle bu makinede eksik "
            "Prometheus/Alertmanager altyapisindan kaynaklanir) -- gercek altyapiyla "
            "yeniden dogrulama ONERILIR, ama kod degisikligi acisindan engelleyici degildir.",
        )
    if gate_exit == 0:
        return GO, "Test suite yesil, tum go-live gate'leri PASS."

    return NO_GO, f"Taninmayan gate_exit_code={gate_exit!r} -- guvenli tarafta kal, NO-GO."


def build_signoff(
    *,
    git_sha: str,
    git_log: list[str],
    test_summary: RunSummary,
    gate_results: dict[str, Any] | None,
    generated_at: str,
) -> dict[str, Any]:
    decision, decision_rationale = go_no_go_recommendation(
        test_exit_code=test_summary.exit_code if test_summary.ran else None, gate_results=gate_results
    )
    return {
        "generated_at": generated_at,
        "git_sha": git_sha,
        "git_recent_log": git_log,
        "test_summary": asdict(test_summary),
        "gate_results": gate_results,
        "go_no_go_decision": decision,
        "go_no_go_rationale": decision_rationale,
        "known_limitations": KNOWN_LIMITATIONS,
        "rollback_reference": {
            "script": "scripts/ops/rollback_observability.ps1",
            "dry_run_default": True,
            "apply_command": "powershell -ExecutionPolicy Bypass -File scripts\\ops\\rollback_observability.ps1 -Apply",
            "dry_run_command": "powershell -ExecutionPolicy Bypass -File scripts\\ops\\rollback_observability.ps1",
        },
    }


def render_signoff_md(data: dict[str, Any]) -> str:
    lines = [
        "# Observability Go-Live Sign-off -- Model Gateway",
        "",
        f"Uretildi (UTC): {data['generated_at']}",
        f"Git SHA: `{data['git_sha']}`",
        "",
        f"## Karar: **{data['go_no_go_decision']}**",
        "",
        data["go_no_go_rationale"],
        "",
        "## Test ozeti",
        "",
        f"- Calistirildi: {data['test_summary']['ran']}",
        f"- Exit code: {data['test_summary']['exit_code']}",
        f"- Ozet: {data['test_summary']['summary_line']}",
        "",
        "## Gate sonuclari",
        "",
    ]
    if data["gate_results"] is None:
        lines.append("- **Gate'ler henuz calistirilmadi** -- bkz. `scripts/ops/run_observability_gates.ps1`.")
    else:
        gr = data["gate_results"]
        lines.append(f"- Kaynak: `{gr.get('_source_file', '?')}`")
        lines.append(f"- Genel durum: {gr.get('overall_status')} (exit code {gr.get('overall_exit_code')})")
        for g in gr.get("gates", []):
            lines.append(f"  - {g['name']}: {g['status']} -- {g['detail']}")

    lines += ["", "## Bilinen sinirlamalar", ""]
    for lim in data["known_limitations"]:
        lines.append(f"- {lim}")

    lines += [
        "",
        "## Rollback",
        "",
        f"- Script: `{data['rollback_reference']['script']}` (varsayilan mod: dry-run)",
        f"- Dry-run: `{data['rollback_reference']['dry_run_command']}`",
        f"- Uygula: `{data['rollback_reference']['apply_command']}`",
        "",
        "## Son commit'ler",
        "",
    ]
    for entry in data["git_recent_log"]:
        lines.append(f"- `{entry}`")

    return "\n".join(lines) + "\n"


def write_signoff(data: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "SIGNOFF.md"
    json_path = out_dir / "SIGNOFF.json"
    md_path.write_text(render_signoff_md(data), encoding="utf-8")
    json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return md_path, json_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Model gateway observability go-live sign-off paketi")
    parser.add_argument("--repo-root", default=str(REPO_ROOT_DEFAULT))
    parser.add_argument("--python-exe", default=str(REPO_ROOT_DEFAULT / ".venv" / "Scripts" / "python.exe"))
    parser.add_argument("--skip-tests", action="store_true", help="Test suite'i calistirma (HIZLI ama NO-GO'ya zorlar)")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    repo_root = Path(args.repo_root)
    git_sha = get_git_sha(repo_root)
    git_log = get_git_short_log(repo_root)

    if args.skip_tests:
        test_summary = RunSummary(ran=False, exit_code=None, summary_line="--skip-tests ile atlandi")
    else:
        test_summary = run_full_test_suite(args.python_exe, repo_root)

    gate_results = find_latest_gate_results(repo_root)

    generated_at = datetime.now(timezone.utc).isoformat()
    data = build_signoff(
        git_sha=git_sha, git_log=git_log, test_summary=test_summary, gate_results=gate_results,
        generated_at=generated_at,
    )

    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = repo_root / "reports" / f"observability_signoff_{ts}"

    md_path, json_path = write_signoff(data, out_dir)
    print(render_signoff_md(data))
    print(f"signoff_md={md_path}")
    print(f"signoff_json={json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
