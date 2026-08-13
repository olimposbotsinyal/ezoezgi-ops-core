"""Acil durum (APPROVE_EMERGENCY) esik degisikliklerinin retroaktif
inceleme vadesini (`retro_review_due_utc`) kacirip kacirmadigini
kontrol eder -- BAGIMSIZ, hizli bir "acil durum takip listesi" CLI'i.

`detect_observability_drift.py`'nin ana drift pipeline'i ZATEN AYNI
kontrolu (`observability_drift_core.check_emergency_review_overdue_drift`)
her calistirmasinda otomatik olarak yapar (bkz. o script'in
`run_drift_detection()` fonksiyonu) -- bu script, o genel pipeline'i
BEKLEMEDEN, YALNIZCA acil durum vadelerine odaklanan, hizli/tekrarli
bir kontrol (ornegin gunluk bir cron/scheduled task veya on-call
sabah kontrolu) icin sunulur. Mantigin KENDISI KOPYALANMAZ -- ayni
`observability_drift_core.py` fonksiyonu dogrudan yeniden kullanilir.

Cikis kodu: 0 (vadesi gecmis, takipsiz acil durum YOK), 2 (en az bir
vadesi gecmis, takipsiz acil durum VAR -- CRITICAL governance drift).
ASLA fabrike edilmis 0 donmez.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT_DEFAULT = Path("d:/Projects/ezoezgi-ops")
DEFAULT_LEDGER_PATH = "infra/monitoring/baseline/approved_checksums_ledger.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Acil durum (APPROVE_EMERGENCY) esik degisikliklerinin retroaktif inceleme vadesini kontrol eder"
    )
    parser.add_argument("--repo-root", default=str(REPO_ROOT_DEFAULT))
    parser.add_argument("--ledger-path", default=None)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    repo_root = Path(args.repo_root)
    ledger_path = Path(args.ledger_path) if args.ledger_path else repo_root / DEFAULT_LEDGER_PATH

    sys.path.insert(0, str(repo_root / "scripts" / "ops"))
    from observability_drift_core import (
        check_emergency_review_overdue_drift,
        overall_drift_exit_code,
        render_drift_report_md,
        write_drift_report_json,
    )
    from threshold_apply_core import load_ledger_entries

    ledger_entries = load_ledger_entries(ledger_path)
    now = datetime.now(timezone.utc)
    findings = check_emergency_review_overdue_drift(ledger_entries, now=now)

    generated_at = now.isoformat()
    window_label = f"tum onayli-degisiklik defteri ({ledger_path}) -- acil durum retroaktif inceleme vadesi kontrolu"

    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        ts = now.strftime("%Y%m%dT%H%M%SZ")
        out_dir = repo_root / "reports" / f"emergency_review_check_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    report_md = render_drift_report_md(findings, generated_at=generated_at, window_label=window_label)
    (out_dir / "overdue_report.md").write_text(report_md, encoding="utf-8")
    write_drift_report_json(findings, out_dir / "overdue_report.json", generated_at=generated_at, window_label=window_label)

    print(report_md)
    print(f"evidence_dir={out_dir}")
    return overall_drift_exit_code(findings)


if __name__ == "__main__":
    sys.exit(main())
