"""v1.2 pilot ozelliklerinin PILOT -> ENFORCED terfi kararini GERCEK
kanit dosyalarini tarayarak verir -- `infra/monitoring/governance/pilot_promotion_criteria_v1.json`'daki
olculebilir kriterlere karsi degerlendirir (bkz. `pilot_promotion_core.py::evaluate_feature`).

Kanit kaynaklari:
  - `required_evidence_paths` desenleriyle eslesen dosyalar (chain_eval.json,
    apply_report.json, legitimacy_report.json) -- 'runs' sayisi + gozlem
    penceresi (en eski kanitin `generated_at`'inden `now`'a kadar) buradan
    cikarilir.
  - En son `reports/drift_*/drift_report.json` -- `emergency_governance`
    kategorisindeki CRITICAL bulgular `unresolved_critical` olarak sayilir.
  - `reports/weekly_observability_*/review.md` dosyalarinin VARLIGI
    (sayisi) -- yalnizca BILGI AMACLI baglam, sayisal kriterlere
    KATILMAZ (markdown'dan yapisal veri cikarmak KIRILGAN olacagindan
    kasitli olarak YAPILMAZ -- bkz. docs 'known limitations').
  - `false_positive_rate`: HERHANGI bir kanit dosyasinda acik bir
    `false_positive_rate` alani VARSA ortalamasi alinir; HICBIR kanitta
    YOKSA `None` (BILINMIYOR) -- bu, PROMOTE'u ENGELLER (fabrike edilmis
    bir oran ASLA uretilmez).

Cikis kodu: 0 (tumu PROMOTE), 1 (en az biri EXTEND_PILOT, hicbiri
REJECT degil), 2 (herhangi biri REJECT).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT_DEFAULT = Path("d:/Projects/ezoezgi-ops")
DEFAULT_CRITERIA_PATH = "infra/monitoring/governance/pilot_promotion_criteria_v1.json"


def _load_json_safely(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _find_evidence_files(repo_root: Path, patterns: list[str]) -> tuple[list[Path], list[str]]:
    """Her `patterns` deseni icin `repo_root.glob(pattern)` calistirir.
    Doner: (TUM benzersiz eslesen dosyalar, hicbir dosyayla eslesmeyen
    desenler). `Path.glob()` `**` recursive joker karakterini destekler."""
    found: list[Path] = []
    missing_patterns: list[str] = []
    for pattern in patterns:
        matches = sorted(repo_root.glob(pattern))
        if not matches:
            missing_patterns.append(pattern)
        for m in matches:
            if m not in found:
                found.append(m)
    return found, missing_patterns


def _extract_generated_at(payload: dict[str, Any]) -> datetime | None:
    raw = payload.get("generated_at")
    if not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _find_latest_drift_report(repo_root: Path) -> dict[str, Any] | None:
    candidates = sorted(repo_root.glob("reports/drift_*/drift_report.json"))
    latest_payload = None
    latest_dt = None
    for c in candidates:
        payload = _load_json_safely(c)
        if payload is None:
            continue
        dt = _extract_generated_at(payload)
        if dt is None:
            continue
        if latest_dt is None or dt > latest_dt:
            latest_dt = dt
            latest_payload = payload
    return latest_payload


def _check_blockers(
    feature_name: str, blocker_conditions: list[str], *, evidence_payloads: list[dict[str, Any]], drift_report: dict[str, Any] | None
) -> list[str]:
    tripped: list[str] = []
    for blocker in blocker_conditions:
        if blocker == "remote_default_changed":
            if drift_report:
                for f in drift_report.get("findings", []):
                    if f.get("category") == "remote_default" and f.get("severity") == "CRITICAL":
                        tripped.append(blocker)
                        break
        elif blocker == "secrets_committed":
            pass  # yapisal yer tutucu -- bkz. criteria manifestosundaki 'blocker_condition_notes'
        elif blocker == "classify_contract_changed":
            pass  # yapisal yer tutucu -- bkz. criteria manifestosundaki 'blocker_condition_notes'
        elif blocker == "provider_is_stub_only":
            if feature_name == "emergency_legitimacy_required" and evidence_payloads:
                providers = {p.get("provider") for p in evidence_payloads if "provider" in p}
                real_provider_used = bool(providers - {"mock", "jira_stub", "none", None})
                if not real_provider_used:
                    tripped.append(blocker)
    return tripped


def compute_evidence_summary(feature_name: str, criteria: dict[str, Any], *, repo_root: Path, now: datetime):
    from pilot_promotion_core import EvidenceSummary

    patterns = criteria["required_evidence_paths"]
    files, missing_patterns = _find_evidence_files(repo_root, patterns)

    payloads: list[dict[str, Any]] = []
    generated_ats: list[datetime] = []
    fp_rates: list[float] = []
    for f in files:
        payload = _load_json_safely(f)
        if payload is None:
            continue
        payloads.append(payload)
        dt = _extract_generated_at(payload)
        if dt is not None:
            generated_ats.append(dt)
        if isinstance(payload.get("false_positive_rate"), (int, float)):
            fp_rates.append(payload["false_positive_rate"])

    observation_days = 0.0
    if generated_ats:
        earliest = min(generated_ats)
        observation_days = max(0.0, (now - earliest).total_seconds() / 86400)

    fp_rate = (sum(fp_rates) / len(fp_rates)) if fp_rates else None

    drift_report = _find_latest_drift_report(repo_root)
    unresolved_critical = 0
    if drift_report:
        unresolved_critical = sum(
            1 for f in drift_report.get("findings", [])
            if f.get("category") == "emergency_governance" and f.get("severity") == "CRITICAL"
        )

    blockers = _check_blockers(
        feature_name, criteria["blocker_conditions"], evidence_payloads=payloads, drift_report=drift_report
    )

    return EvidenceSummary(
        runs=len(payloads),
        observation_days=observation_days,
        false_positive_rate=fp_rate,
        unresolved_critical=unresolved_critical,
        blockers_tripped=blockers,
        missing_evidence_patterns=missing_patterns,
        evidence_files=[str(f) for f in files],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="v1.2 pilot ozelliklerinin PILOT->ENFORCED terfi degerlendirmesi")
    parser.add_argument("--repo-root", default=str(REPO_ROOT_DEFAULT))
    parser.add_argument("--criteria-path", default=None)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    repo_root = Path(args.repo_root)
    criteria_path = Path(args.criteria_path) if args.criteria_path else repo_root / DEFAULT_CRITERIA_PATH

    sys.path.insert(0, str(repo_root / "scripts" / "ops"))
    from pilot_promotion_core import (
        FEATURE_NAMES,
        evaluate_feature,
        get_feature_criteria,
        overall_exit_code,
        validate_criteria_manifest,
        write_promotion_report,
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

    now = datetime.now(timezone.utc)
    generated_at = now.isoformat()

    decisions = []
    for feature_name in FEATURE_NAMES:
        criteria = get_feature_criteria(manifest, feature_name)
        evidence = compute_evidence_summary(feature_name, criteria, repo_root=repo_root, now=now)
        decisions.append(evaluate_feature(feature_name, criteria, evidence))

    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        ts = now.strftime("%Y%m%dT%H%M%SZ")
        out_dir = repo_root / "reports" / f"pilot_promotion_{ts}"

    write_promotion_report(decisions, out_dir, generated_at=generated_at)

    for d in decisions:
        print(f"{d.feature}: {d.decision} -- {d.rationale}")
    print(f"evidence_dir={out_dir}")

    return overall_exit_code(decisions)


if __name__ == "__main__":
    sys.exit(main())
