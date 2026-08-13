"""Kontrollu VerifyReload FAIL + auto-rollback "drill"i icin kanit
paketleyici -- `promotion_drill_core.py`'nin saf mantigini GERCEK bir
`apply_report.json` + `data/audit/audit.log.jsonl` dosyasina uygular.

**Varsayilan olarak GERCEK bir drill TETIKLEMEZ** -- bu ortamda
`promtool`/`amtool` kurulu degilse (bkz. `--fresh-run` bayragi), ONCEDEN
GERCEKTEN calistirilmis bir `apply_report.json`'u (varsayilan kaynak:
bilinen bir GERCEK strict-mod auto-rollback drilli) okuyup SEFFAF
sekilde paketler -- fabrike edilmis hicbir veri YOKTUR, kaynak dosya
BIREBIR okunur/dogrulanir.

`--fresh-run` yalnizca operator, BU CALISTIRMADAN HEMEN ONCE
`apply_threshold_proposal.ps1 -AutoRollbackOnVerifyFail -AutoRollbackMode
strict` ile GERCEKTEN TAZE bir drill calistirdiysa ve `--source-apply-report-path`
o TAZE calistirmanin ciktisini gosteriyorsa kullanilmalidir -- bu
script'in KENDISI bir drill TETIKLEMEZ, yalnizca SONUCUNU paketler."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT_DEFAULT = Path("d:/Projects/ezoezgi-ops")
DEFAULT_SOURCE_APPLY_REPORT = (
    "reports/v1_2_pilot_20260813T180035Z/scenario2_verify_fail_autorollback/"
    "autorollback_ON_strict/apply_report.json"
)
DEFAULT_AUDIT_LOG_PATH = "data/audit/audit.log.jsonl"


def _load_json_safely(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _load_audit_entries(path: Path) -> list[dict]:
    entries: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return entries
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description="Kontrollu VerifyReload FAIL + auto-rollback drill kanit paketleyici")
    parser.add_argument("--repo-root", default=str(REPO_ROOT_DEFAULT))
    parser.add_argument(
        "--source-apply-report-path", default=None,
        help=f"Varsayilan: bilinen GERCEK bir strict-mod auto-rollback drilli ({DEFAULT_SOURCE_APPLY_REPORT})",
    )
    parser.add_argument("--audit-log-path", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument(
        "--fresh-run", action="store_true",
        help="YALNIZCA bu calistirmadan HEMEN ONCE GERCEK bir drill TAZE calistirildiysa verin -- "
        "bu script'in KENDISI bir drill TETIKLEMEZ, yalnizca bunu ACIKCA ETIKETLER.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root)
    source_path = (
        Path(args.source_apply_report_path) if args.source_apply_report_path
        else repo_root / DEFAULT_SOURCE_APPLY_REPORT
    )
    audit_log_path = Path(args.audit_log_path) if args.audit_log_path else repo_root / DEFAULT_AUDIT_LOG_PATH

    sys.path.insert(0, str(repo_root / "scripts" / "ops"))
    from promotion_drill_core import (
        find_matching_audit_entry,
        parse_drill_evidence,
        validate_drill_acceptance,
        write_drill_package,
    )

    source_payload = _load_json_safely(source_path)
    if source_payload is None:
        print(f"HATA: kaynak apply_report.json okunamadi/ayristirilamadi: {source_path}", file=sys.stderr)
        return 2

    parsed = parse_drill_evidence(source_payload)
    accepted, violations = validate_drill_acceptance(parsed)

    audit_entries = _load_audit_entries(audit_log_path)
    audit_entry = find_matching_audit_entry(audit_entries, parsed.get("proposal_id"))
    if audit_entry is None:
        accepted = False
        violations = violations + [f"audit.log.jsonl'de proposal_id={parsed.get('proposal_id')!r} icin eslesen bir auto_rollback_triggered kaydi bulunamadi"]

    now = datetime.now(timezone.utc)
    generated_at = now.isoformat()
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        ts = now.strftime("%Y%m%dT%H%M%SZ")
        out_dir = repo_root / "reports" / f"promotion_drill_{ts}"

    write_drill_package(
        out_dir, generated_at=generated_at, source_apply_report_path=str(source_path),
        source_generated_at=parsed.get("generated_at"), source_payload=source_payload, parsed=parsed,
        accepted=accepted, violations=violations, audit_entry=audit_entry, is_fresh_run=args.fresh_run,
    )

    print(f"is_fresh_run={args.fresh_run}")
    print(f"source_apply_report_path={source_path}")
    print(f"verification_state={parsed.get('verification_state')}")
    print(f"auto_rollback_triggered={parsed.get('auto_rollback_triggered')}")
    print(f"auto_rollback_restored={parsed.get('auto_rollback_restored')}")
    print(f"audit_entry_found={audit_entry is not None}")
    print(f"accepted={accepted}")
    if violations:
        for v in violations:
            print(f"  - VIOLATION: {v}")
    print(f"evidence_dir={out_dir}")

    return 0 if accepted else 2


if __name__ == "__main__":
    sys.exit(main())
