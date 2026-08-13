"""v1.2 pilot ozellikleri icin GOZLEM PENCERESI (observation window)
kontrolu -- her ozellik icin `observed_days`/`run_count`/`remaining_days`/
`remaining_runs`'i, `evaluate_pilot_promotion.py` ile AYNI kanit
tarama mantigini (`compute_evidence_summary`) kullanarak hesaplar.

**Bu script BLOCKER/FPR/karar mantigina KARISMAZ** -- yalnizca "ne kadar
kanit birikti, ne kadar daha gerekli" sorusuna odaklanir (gorev kisiti:
"per feature: observed_days, run_count, remaining_days, remaining_runs").
Tam karar icin `evaluate_pilot_promotion.py` (normal veya `--rehearsal`
modu) kullanilmalidir.

Cikti: `reports/pilot_promotion_<UTC>/observation_window_report.json`+`.md`
-- `evaluate_pilot_promotion.py` (normal ve `--rehearsal` modlarinin
ikisi de) EN SON boyle bir raporu bulursa BILGI AMACLI referans verir
(bkz. `_find_latest_observation_window_report`)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT_DEFAULT = Path("d:/Projects/ezoezgi-ops")
DEFAULT_CRITERIA_PATH = "infra/monitoring/governance/pilot_promotion_criteria_v1.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="v1.2 pilot ozellikleri icin gozlem penceresi kontrolu")
    parser.add_argument("--repo-root", default=str(REPO_ROOT_DEFAULT))
    parser.add_argument("--criteria-path", default=None)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    repo_root = Path(args.repo_root)
    criteria_path = Path(args.criteria_path) if args.criteria_path else repo_root / DEFAULT_CRITERIA_PATH

    sys.path.insert(0, str(repo_root / "scripts" / "ops"))
    from pilot_promotion_core import (
        FEATURE_NAMES,
        compute_observation_window,
        get_feature_criteria,
        validate_criteria_manifest,
        write_observation_window_report,
    )

    try:
        manifest = json.loads(criteria_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"HATA: kriter manifestosu okunamadi/ayristirilamadi ({criteria_path}): {exc}", file=sys.stderr)
        return 2

    manifest_errors = validate_criteria_manifest(manifest)
    if manifest_errors:
        print("HATA: kriter manifestosu GECERSIZ:", file=sys.stderr)
        for e in manifest_errors:
            print(f"  - {e}", file=sys.stderr)
        return 2

    # `compute_evidence_summary`, evaluate_pilot_promotion.py'nin GERCEK
    # kanit-tarama mantigidir -- bu script'in AYRI/tutarsiz bir surumunu
    # YAZMAMAK icin dogrudan yeniden kullanilir (tek dogruluk kaynagi).
    # secrets_found/contract_changed bu script icin ILGISIZDIR (bu script
    # blocker HESAPLAMAZ, yalnizca gozlem penceresini raporlar) -- bu
    # yuzden notr/bos deger olarak gecirilir; GERCEK blocker durumu
    # yalnizca evaluate_pilot_promotion.py'nin KENDISINDE hesaplanir.
    from evaluate_pilot_promotion import compute_evidence_summary

    now = datetime.now(timezone.utc)
    generated_at = now.isoformat()

    windows = []
    for feature_name in FEATURE_NAMES:
        criteria = get_feature_criteria(manifest, feature_name)
        evidence = compute_evidence_summary(
            feature_name, criteria, repo_root=repo_root, now=now, secrets_found=[], contract_changed=False,
        )
        windows.append(compute_observation_window(feature_name, criteria, evidence))

    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        ts = now.strftime("%Y%m%dT%H%M%SZ")
        out_dir = repo_root / "reports" / f"pilot_promotion_{ts}"

    write_observation_window_report(windows, out_dir, generated_at=generated_at)

    for w in windows:
        print(
            f"{w['feature']}: observed_days={w['observed_days']} run_count={w['run_count']} "
            f"remaining_days={w['remaining_days']} remaining_runs={w['remaining_runs']}"
        )
    print(f"evidence_dir={out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
