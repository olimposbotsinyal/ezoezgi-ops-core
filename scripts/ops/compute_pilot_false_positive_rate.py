"""v1.2 pilot ozellikleri icin GERCEK kanit dosyalarindan yanlis-pozitif
orani (false_positive_rate) hesaplar -- `pilot_fpr_core.py`'nin saf
mantigini GERCEK dosya taramasiyla besler.

Girdiler:
  - Drift raporlari (`reports/drift_*/drift_report.json`) -- su an
    dogrudan sinyal KAYNAGI olarak KULLANILMAZ (emergency_governance
    bulgulari zaten `evaluate_pilot_promotion.py`'nin `unresolved_critical`
    kriteri tarafindan ayrica ele alinir), ama GELECEKTE ek sinyal
    turleri icin bu script'e kolayca eklenebilir sekilde tasarlanmistir.
  - Apply/rollback raporlari (`reports/**/apply_report.json`) --
    `auto_rollback.triggered=true` olan HER rapor bir SINYALDIR (bu
    rollback GERCEKTEN gerekli miydi, yoksa yanlis-alarm miydi?).
  - `chain_eval.json` (`reports/**/chain_eval.json`) -- `v1_2_checksum_chain
    == BROKEN_CHAIN` olan HER satir bir SINYALDIR.
  - `legitimacy_report.json` (`reports/**/legitimacy_report.json`) --
    `legitimacy_status == FAIL` olan HER rapor bir SINYALDIR.
  - `infra/monitoring/governance/pilot_fpr_adjudications.json` (OPSIYONEL,
    insan tarafindan doldurulur) -- yukaridaki sinyallerin HANGILERININ
    GERCEKTEN yanlis-pozitif oldugunu kaydeder.

Cikti: `reports/pilot_metrics_<UTC>/fpr_summary.md`+`.json` --
`evaluate_pilot_promotion.py` bunu OTOMATIK olarak (varsa) tuketir.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT_DEFAULT = Path("d:/Projects/ezoezgi-ops")
DEFAULT_ADJUDICATIONS_PATH = "infra/monitoring/governance/pilot_fpr_adjudications.json"


def _load_json_safely(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def collect_chain_matching_signals(repo_root: Path):
    from pilot_fpr_core import Signal

    signals: list[Signal] = []
    for f in sorted(repo_root.glob("reports/emergency_chain_trial_*/chain_eval.json")) + sorted(
        repo_root.glob("reports/v1_2_pilot_*/**/chain_eval.json")
    ):
        payload = _load_json_safely(f)
        if payload is None:
            continue
        for row in payload.get("rows", []):
            if row.get("v1_2_checksum_chain") == "BROKEN_CHAIN":
                signal_id = row.get("proposal_id") or f"{f}::{row.get('alert_name')}"
                signals.append(Signal(feature="emergency_chain_matching", signal_id=str(signal_id), evidence_path=str(f)))
    return signals


def collect_auto_rollback_signals(repo_root: Path):
    from pilot_fpr_core import Signal

    signals: list[Signal] = []
    for f in sorted(repo_root.glob("reports/v1_2_pilot_*/**/apply_report.json")) + sorted(
        repo_root.glob("reports/threshold_apply_*/apply_report.json")
    ):
        payload = _load_json_safely(f)
        if payload is None:
            continue
        auto_rollback = payload.get("auto_rollback")
        if isinstance(auto_rollback, dict) and auto_rollback.get("triggered"):
            signal_id = payload.get("proposal_id") or str(f)
            signals.append(Signal(feature="auto_rollback_on_verify_fail", signal_id=str(signal_id), evidence_path=str(f)))
    return signals


def collect_legitimacy_signals(repo_root: Path):
    from pilot_fpr_core import Signal

    signals: list[Signal] = []
    for f in sorted(repo_root.glob("reports/emergency_legitimacy_*/legitimacy_report.json")) + sorted(
        repo_root.glob("reports/v1_2_pilot_*/**/legitimacy_report.json")
    ):
        payload = _load_json_safely(f)
        if payload is None:
            continue
        if payload.get("legitimacy_status") == "FAIL":
            signal_id = payload.get("incident_id") or str(f)
            signals.append(Signal(feature="emergency_legitimacy_required", signal_id=str(signal_id), evidence_path=str(f)))
    return signals


def main() -> int:
    parser = argparse.ArgumentParser(description="v1.2 pilot ozellikleri icin false_positive_rate hesaplar")
    parser.add_argument("--repo-root", default=str(REPO_ROOT_DEFAULT))
    parser.add_argument("--adjudications-path", default=None)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    repo_root = Path(args.repo_root)
    adjudications_path = (
        Path(args.adjudications_path) if args.adjudications_path else repo_root / DEFAULT_ADJUDICATIONS_PATH
    )

    sys.path.insert(0, str(repo_root / "scripts" / "ops"))
    from pilot_promotion_core import FEATURE_NAMES
    from pilot_fpr_core import compute_feature_fpr, parse_adjudications, validate_adjudications_file, write_fpr_summary

    adjudications_payload = _load_json_safely(adjudications_path) or {"adjudications": []}
    adjudication_errors = validate_adjudications_file(adjudications_payload)
    if adjudication_errors:
        print("HATA: adjudikasyon dosyasi GECERSIZ:", file=sys.stderr)
        for e in adjudication_errors:
            print(f"  - {e}", file=sys.stderr)
        return 2
    all_adjudications = parse_adjudications(adjudications_payload)

    signals_by_feature = {
        "emergency_chain_matching": collect_chain_matching_signals(repo_root),
        "auto_rollback_on_verify_fail": collect_auto_rollback_signals(repo_root),
        "emergency_legitimacy_required": collect_legitimacy_signals(repo_root),
    }

    results = {}
    for feature_name in FEATURE_NAMES:
        feature_signals = signals_by_feature.get(feature_name, [])
        feature_adjudications = [a for a in all_adjudications if a.feature == feature_name]
        results[feature_name] = compute_feature_fpr(feature_signals, feature_adjudications)

    now = datetime.now(timezone.utc)
    generated_at = now.isoformat()
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        ts = now.strftime("%Y%m%dT%H%M%SZ")
        out_dir = repo_root / "reports" / f"pilot_metrics_{ts}"

    write_fpr_summary(results, out_dir, generated_at=generated_at)

    for feature_name, r in results.items():
        print(
            f"{feature_name}: total_signals={r.total_signals} adjudicated={r.adjudicated_signals} "
            f"confirmed_fp={r.confirmed_false_positives} rate={r.false_positive_rate} "
            f"confidence={r.confidence_band} status={r.status}"
        )
    print(f"evidence_dir={out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
