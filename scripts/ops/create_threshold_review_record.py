"""Bir esik degisikligi ONERISI (proposal) icin insan onayli inceleme
(review/sign-off) kaydi olusturur.

Bu script HICBIR config/ortam degiskenini/alert kural dosyasini
DEGISTIRMEZ -- yalnizca `reports/threshold_reviews/<proposal_id>/review_record.json`
yazar. Uygulamak (`apply_threshold_proposal.ps1`), decision=APPROVE VE
checksum eslesmesi OLMADAN calismayi REDDEDER (bkz. threshold_governance_core.py::check_apply_eligibility).

**Bu, gorevin "no auto-apply without explicit approval artifact"
kisitinin BIRINCI yarisidir** -- bir insanin (`--reviewer`) ACIKCA bir
karar (`--decision`) + gerekce (`--rationale`) vermesini ZORUNLU kilar.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Esik degisikligi onerisi icin inceleme kaydi olusturur")
    parser.add_argument("--proposal-path", required=True, help="reports/threshold_proposals/<id>/proposal.json yolu")
    parser.add_argument("--reviewer", required=True, help="Inceleyen kisinin adi/e-postasi")
    parser.add_argument("--decision", required=True, choices=["APPROVE", "REJECT", "NEEDS_DATA"])
    parser.add_argument("--rationale", required=True, help="Karar gerekcesi (bos birakilamaz)")
    parser.add_argument("--output-base-dir", default=None, help="Varsayilan: reports/threshold_reviews/")
    args = parser.parse_args()

    if not args.rationale.strip():
        print("HATA: --rationale bos olamaz.", file=sys.stderr)
        return 2

    from threshold_governance_core import build_review_record

    proposal_path = Path(args.proposal_path)
    try:
        proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"HATA: proposal dosyasi okunamadi/ayristirilamadi ({proposal_path}): {exc}", file=sys.stderr)
        return 2

    if "proposal_id" not in proposal or "checksum" not in proposal:
        print("HATA: proposal dosyasi 'proposal_id'/'checksum' alanlarini icermiyor -- gecerli bir proposal degil.", file=sys.stderr)
        return 2

    review_record = build_review_record(
        reviewer=args.reviewer,
        decision=args.decision,
        rationale=args.rationale,
        proposal=proposal,
        approved_at_utc=datetime.now(timezone.utc).isoformat(),
    )

    output_base_dir = Path(args.output_base_dir) if args.output_base_dir else Path("reports") / "threshold_reviews"
    out_dir = output_base_dir / proposal["proposal_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "review_record.json"
    out_path.write_text(json.dumps(review_record, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(review_record, indent=2, ensure_ascii=False))
    print(f"review_record={out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
