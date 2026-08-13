"""Bir esik degisikligi ONERISI (proposal) icin insan onayli inceleme
(review/sign-off) kaydi olusturur.

Bu script HICBIR config/ortam degiskenini/alert kural dosyasini
DEGISTIRMEZ -- yalnizca `reports/threshold_reviews/<proposal_id>/review_record.json`
yazar. Uygulamak (`apply_threshold_proposal.ps1`), decision=APPROVE VE
checksum eslesmesi OLMADAN calismayi REDDEDER (bkz. threshold_governance_core.py::check_apply_eligibility).

**Bu, gorevin "no auto-apply without explicit approval artifact"
kisitinin BIRINCI yarisidir** -- bir insanin (`--reviewer`) ACIKCA bir
karar (`--decision`) + gerekce (`--rationale`) vermesini ZORUNLU kilar.

**Acil durum (--decision APPROVE_EMERGENCY):** normal iki-goz incelemesi
beklenemeyecek kadar acil durumlarda, TEK bir mühendis kendi APPROVE_EMERGENCY
kararini kaydedebilir -- ama bu, `--incident-id`/`--justification`/
`--timebox-hours` (maks 24)/`--retro-review-due-utc` alanlarinin TUMUNUN
gecerli olmasini ZORUNLU kilar (bkz. `threshold_governance_core.py::validate_emergency_fields`).
Bu alanlar hem burada (yazmadan ONCE) hem de `apply_threshold_proposal.ps1`
icinde (apply ANINDA, review_record.json elle degistirilmis olsa bile
GUVENLI kalmak icin) TEKRAR dogrulanir.
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
    parser.add_argument("--decision", required=True, choices=["APPROVE", "REJECT", "NEEDS_DATA", "APPROVE_EMERGENCY"])
    parser.add_argument("--rationale", required=True, help="Karar gerekcesi (bos birakilamaz)")
    parser.add_argument("--incident-id", default=None, help="YALNIZCA --decision APPROVE_EMERGENCY icin zorunlu")
    parser.add_argument("--justification", default=None, help="YALNIZCA --decision APPROVE_EMERGENCY icin zorunlu")
    parser.add_argument(
        "--timebox-hours", type=float, default=None,
        help="YALNIZCA --decision APPROVE_EMERGENCY icin zorunlu, 0 ile 24 arasinda",
    )
    parser.add_argument(
        "--retro-review-due-utc", default=None,
        help="YALNIZCA --decision APPROVE_EMERGENCY icin zorunlu, ISO8601 UTC (ornek: 2026-08-14T12:00:00+00:00)",
    )
    parser.add_argument(
        "--legitimacy-report-path", default=None,
        help="Opsiyonel (v1.2 PILOT, non-blocking) -- scripts/ops/check_emergency_legitimacy.py "
        "ciktisina bir referans; yalnizca bilgi amaclidir, apply uygunlugunu ETKILEMEZ",
    )
    parser.add_argument("--output-base-dir", default=None, help="Varsayilan: reports/threshold_reviews/")
    args = parser.parse_args()

    if not args.rationale.strip():
        print("HATA: --rationale bos olamaz.", file=sys.stderr)
        return 2

    from threshold_governance_core import build_review_record, validate_emergency_fields

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
        incident_id=args.incident_id,
        justification=args.justification,
        timebox_hours=args.timebox_hours,
        retro_review_due_utc=args.retro_review_due_utc,
        legitimacy_report_path=args.legitimacy_report_path,
    )

    if args.decision == "APPROVE_EMERGENCY":
        emergency_errors = validate_emergency_fields(review_record)
        if emergency_errors:
            print("HATA: APPROVE_EMERGENCY icin zorunlu alanlar gecersiz/eksik -- review_record YAZILMADI:", file=sys.stderr)
            for e in emergency_errors:
                print(f"  - {e}", file=sys.stderr)
            return 2

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
