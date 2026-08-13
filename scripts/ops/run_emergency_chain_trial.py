"""v1.2 PILOT: eski (v1.1, alert_name-only) ile yeni (v1.2, checksum-
zinciri) acil durum kapanis mantiginin GERCEK ledger verisi uzerinde
YAN YANA karsilastirmasini uretir -- `emergency_chain_core.py`'nin
KENDI fonksiyonlarini CAGIRIR, mantigi TEKRARLAMAZ.

Bu script SALT-OKUNURDUR -- hicbir dosyayi degistirmez, yalnizca
`reports/emergency_chain_trial_<UTC>/chain_eval.md`+`.json` yazar
(gorev v1.2 madde 1'in "Output evidence" gereksinimi + pilot senaryo 1:
"chain-matching comparison (old vs new logic)").
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT_DEFAULT = Path("d:/Projects/ezoezgi-ops")
DEFAULT_LEDGER_PATH = "infra/monitoring/baseline/approved_checksums_ledger.jsonl"


def render_chain_eval_md(rows: list[dict], *, generated_at: str) -> str:
    lines = [
        "# Acil Durum Kapanis Mantigi Karsilastirmasi (v1.1 vs v1.2 PILOT)",
        "",
        f"Uretildi (UTC): {generated_at}",
        "",
    ]
    if not rows:
        lines.append("Vadesi gecmis hicbir acil durum girdisi bulunamadi -- karsilastirilacak bir sey yok.")
        return "\n".join(lines) + "\n"

    lines += [
        "| Proposal ID | Alert | v1.1 (alert_name-only) | v1.2 (checksum-chain) | Fark var mi? |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['proposal_id']} | {r['alert_name']} | {r['v1_1_alert_name_only']} | "
            f"{r['v1_2_checksum_chain']} | {'EVET' if r['outcome_differs'] else 'hayir'} |"
        )
    differing = sum(1 for r in rows if r["outcome_differs"])
    lines += ["", f"**Toplam {len(rows)} vadesi-gecmis girdi, {differing} tanesinde v1.1/v1.2 sonucu FARKLI.**"]
    return "\n".join(lines) + "\n"


def write_chain_eval_report(rows: list[dict], out_dir: Path, *, generated_at: str) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md = render_chain_eval_md(rows, generated_at=generated_at)
    md_path = out_dir / "chain_eval.md"
    md_path.write_text(md, encoding="utf-8")

    json_path = out_dir / "chain_eval.json"
    payload = {"generated_at": generated_at, "rows": rows}
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"md": md_path, "json": json_path}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="v1.1 (alert_name-only) vs v1.2 (checksum-chain) acil durum kapanis mantigi karsilastirmasi (salt-okunur)"
    )
    parser.add_argument("--repo-root", default=str(REPO_ROOT_DEFAULT))
    parser.add_argument("--ledger-path", default=None)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    repo_root = Path(args.repo_root)
    ledger_path = Path(args.ledger_path) if args.ledger_path else repo_root / DEFAULT_LEDGER_PATH

    sys.path.insert(0, str(repo_root / "scripts" / "ops"))
    from emergency_chain_core import compare_matching_strategies, find_overdue_emergency_entries
    from threshold_apply_core import load_ledger_entries

    now = datetime.now(timezone.utc)
    ledger_entries = load_ledger_entries(ledger_path)
    overdue_entries = find_overdue_emergency_entries(ledger_entries, now=now)
    rows = compare_matching_strategies(ledger_entries, overdue_entries)

    generated_at = now.isoformat()
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        ts = now.strftime("%Y%m%dT%H%M%SZ")
        out_dir = repo_root / "reports" / f"emergency_chain_trial_{ts}"

    paths = write_chain_eval_report(rows, out_dir, generated_at=generated_at)
    print(render_chain_eval_md(rows, generated_at=generated_at))
    print(f"evidence_dir={out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
