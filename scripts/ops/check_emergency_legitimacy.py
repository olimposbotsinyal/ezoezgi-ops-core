"""Acil durum (APPROVE_EMERGENCY) mesruiyet on-kontrolu -- BAGIMSIZ CLI
(v1.2 PILOT, non-blocking). `emergency_legitimacy_core.py`'nin saf
mantigini cagirir, `reports/emergency_legitimacy_<UTC>/legitimacy_report.md`+`.json`
yazar.

**Bu script'in kendi exit code'u DURUMU DURUSTCE yansitir** (0=PASS/SKIPPED,
2=FAIL) -- ama bu, `apply_threshold_proposal.ps1`/`check_apply_eligibility`
akisina HENUZ BAGLANMADI (hicbir yerden cagrilip apply'i engellemiyor).
"Non-blocking" olan budur: KİMSE bu script'in sonucunu apply kararina
KATMIYOR, script'in kendisi sessizce 0 dondurmuyor.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT_DEFAULT = Path("d:/Projects/ezoezgi-ops")


def main() -> int:
    parser = argparse.ArgumentParser(description="Acil durum mesruiyet on-kontrolu (v1.2 PILOT, non-blocking)")
    parser.add_argument("--repo-root", default=str(REPO_ROOT_DEFAULT))
    parser.add_argument("--incident-id", default=None)
    parser.add_argument("--provider", default="none", choices=["none", "mock", "jira_stub"])
    parser.add_argument("--ticket-regex", default=None, help="Varsayilan: GOV_TICKET_REGEX ortam degiskeni, o da yoksa OPS-\\d+")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    repo_root = Path(args.repo_root)
    sys.path.insert(0, str(repo_root / "scripts" / "ops"))
    from emergency_legitimacy_core import DEFAULT_TICKET_REGEX, evaluate_legitimacy, write_legitimacy_report

    ticket_regex = args.ticket_regex or os.environ.get("GOV_TICKET_REGEX") or DEFAULT_TICKET_REGEX
    enforcement_flag = os.environ.get("GOV_EMERGENCY_LEGITIMACY_REQUIRED", "0")

    result = evaluate_legitimacy(incident_id=args.incident_id, provider=args.provider, ticket_regex=ticket_regex)

    generated_at = datetime.now(timezone.utc).isoformat()
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = repo_root / "reports" / f"emergency_legitimacy_{ts}"

    write_legitimacy_report(result, out_dir, generated_at=generated_at, incident_id=args.incident_id, provider=args.provider)

    print(f"legitimacy_status={result.status}")
    for r in result.reasons:
        print(f"  - {r}")
    print(f"GOV_EMERGENCY_LEGITIMACY_REQUIRED={enforcement_flag} (PILOT'ta HENUZ zorlayici degil -- bkz. docs 'Promotion criteria')")
    print(f"evidence_dir={out_dir}")

    if result.status == "FAIL":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
