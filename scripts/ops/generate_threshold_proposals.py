"""Esik degisikligi ONERI (proposal) dosyalarini uretir.

`calibrate_alert_thresholds.py` v1'in kalibrasyon sonuclarini
(`AlertCalibrationV1`) tuketip, `infra/monitoring/governance/threshold_proposal_schema_v1.json`
semasina uygun, checksum'li, benzersiz `proposal_id`'li JSON dosyalari
yazar -- `reports/threshold_proposals/<proposal_id>/proposal.json`.

Yalnizca GERCEKTEN uygulanabilir (CALIBRATED durumda VE mevcut degerden
FARKLI bir oneri iceren) alert'ler icin proposal uretilir -- ayni
"anlamsiz oneri gosterme" ilkesi `calibrate_alert_thresholds.py`'nin
`proposed_threshold_patch.yaml`'inde de gecerlidir.

**POLITIKA:** Bu script de (calibrate_alert_thresholds.py gibi) HICBIR
config/ortam degiskenini/alert kural dosyasini DEGISTIRMEZ -- yalnizca
bir ONERI dosyasi yazar. Uygulamak icin bkz.
`scripts/ops/create_threshold_review_record.py` (insan onayi) +
`scripts/ops/apply_threshold_proposal.ps1` (kontrollu uygulama).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SCHEMA_PATH = "infra/monitoring/governance/threshold_proposal_schema_v1.json"


def _evidence_paths_for(calibration_out_dir: Path) -> list[str]:
    return [
        str(calibration_out_dir / "calibration_v1.md"),
        str(calibration_out_dir / "calibration_v1.json"),
    ]


def generate_proposals(
    *,
    jsonl_path: Path,
    repo_root: Path,
    calibration_out_dir: Path,
) -> list[dict]:
    """Kalibrasyonu GERCEKTEN calistirir (calibrate_alert_thresholds.py'nin
    AYNI fonksiyonlarini yeniden kullanarak -- iki ayri hesaplama
    mantigi riski yok) ve uygulanabilir her alert icin bir proposal
    sozlugu doner (henuz DISKE YAZILMADAN)."""
    from calibrate_alert_thresholds import WINDOWS_HOURS, _is_synthetic, calibrate_multi_window
    from model_gateway import metrics_aggregate
    from model_gateway.config import load_config
    from threshold_governance_core import build_proposal

    config = load_config()
    import time

    now = time.time()
    largest_window_minutes = WINDOWS_HOURS["14d"] * 60
    all_events = metrics_aggregate.read_recent_events(jsonl_path, window_minutes=largest_window_minutes)
    non_synthetic_events = [e for e in all_events if not _is_synthetic(e)]

    events_by_window = {}
    for window, hours in WINDOWS_HOURS.items():
        cutoff = now - hours * 3600
        events_by_window[window] = [e for e in non_synthetic_events if e.ts >= cutoff]

    results = calibrate_multi_window(
        events_by_window,
        current_null_intent_warn=config.alert_null_intent_warn,
        current_null_intent_crit=config.alert_null_intent_crit,
        current_fallback_spike_multiplier=config.alert_fallback_spike_multiplier,
    )

    generated_at = datetime.now(timezone.utc).isoformat()
    evidence_paths = _evidence_paths_for(calibration_out_dir)

    proposals = []
    for r in results:
        if not r.env_vars or r.primary_suggestion.status != "CALIBRATED":
            continue  # uygulanamaz (yetersiz veri VEYA ozel bir esik ortam degiskeni yok)

        p = r.primary_suggestion
        current_values: dict[str, float] = {}
        proposed_values: dict[str, float] = {}
        if p.suggested_warn != p.current_warn:
            current_values["warn"] = p.current_warn
            proposed_values["warn"] = p.suggested_warn
        if p.suggested_crit != p.current_crit and p.suggested_crit != proposed_values.get("warn"):
            current_values["crit"] = p.current_crit
            proposed_values["crit"] = p.suggested_crit
        if not proposed_values:
            continue

        risk_note = f"{p.false_positive_risk} | {r.change_impact}"

        proposal = build_proposal(
            alert_name=r.alert_name,
            source_window=r.primary_window,
            current_values=current_values,
            proposed_values=proposed_values,
            confidence=r.confidence,
            sample_adequacy=r.sample_adequacy,
            risk_note=risk_note,
            evidence_paths=evidence_paths,
            generated_at_utc=generated_at,
        )
        proposals.append(proposal)

    return proposals


def write_proposal(proposal: dict, base_out_dir: Path) -> Path:
    out_dir = base_out_dir / proposal["proposal_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "proposal.json"
    out_path.write_text(json.dumps(proposal, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Model gateway esik degisikligi ONERI (proposal) uretici")
    parser.add_argument("--jsonl-path", default=None, help="Varsayilan: config.metrics_jsonl_path")
    parser.add_argument("--repo-root", default="d:/Projects/ezoezgi-ops")
    parser.add_argument(
        "--calibration-out-dir",
        default=None,
        help="calibrate_alert_thresholds.py'nin bu calistirmayla iliskili kanit dizini (evidence_paths icin)",
    )
    parser.add_argument("--output-base-dir", default=None, help="Varsayilan: reports/threshold_proposals/")
    args = parser.parse_args()

    from model_gateway.config import load_config

    config = load_config()
    jsonl_path = Path(args.jsonl_path) if args.jsonl_path else Path(config.metrics_jsonl_path)
    repo_root = Path(args.repo_root)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    calibration_out_dir = (
        Path(args.calibration_out_dir) if args.calibration_out_dir else Path("reports") / f"alert_calibration_{ts}"
    )
    output_base_dir = Path(args.output_base_dir) if args.output_base_dir else Path("reports") / "threshold_proposals"

    proposals = generate_proposals(jsonl_path=jsonl_path, repo_root=repo_root, calibration_out_dir=calibration_out_dir)

    if not proposals:
        print("Uygulanabilir bir oneri yok (yetersiz veri veya tum onerilen degerler mevcutla ayni).")
        return 0

    written_paths = []
    for proposal in proposals:
        path = write_proposal(proposal, output_base_dir)
        written_paths.append(path)
        print(f"proposal_id={proposal['proposal_id']} alert={proposal['alert_name']} "
              f"confidence={proposal['confidence']} checksum={proposal['checksum'][:12]}...")
        print(f"  written={path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
