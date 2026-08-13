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


def scan_repo_for_secrets(repo_root: Path, config_path: Path) -> list:
    """`git ls-files` ile TAKIP EDILEN tum dosyalari okuyup `secret_scan_core.scan_files_for_secrets`'e
    besler. Yapilandirma `secret_scan_patterns_v1.json`'dan okunur (yoksa
    varsayilanlara duser). Okunamayan/ikili dosyalar SESSIZCE atlanir."""
    import subprocess

    from secret_scan_core import load_scan_config, scan_files_for_secrets

    config = _load_json_safely(config_path) or {}
    denylist, allowlist_markers, allowlist_paths = load_scan_config(config)

    try:
        result = subprocess.run(
            ["git", "ls-files"], capture_output=True, text=True, cwd=str(repo_root), timeout=30
        )
        tracked_paths = result.stdout.splitlines() if result.returncode == 0 else []
    except (OSError, subprocess.SubprocessError):
        tracked_paths = []

    files: list[tuple[str, str]] = []
    for rel_path in tracked_paths:
        fp = repo_root / rel_path
        if not fp.is_file():
            continue
        try:
            content = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        files.append((rel_path, content))

    return scan_files_for_secrets(files, denylist_patterns=denylist, allowlist_markers=allowlist_markers, allowlist_paths=allowlist_paths)


def check_classify_contract_drift(repo_root: Path, baseline_path: Path) -> tuple[bool, str]:
    """`classify_contract_core.py`'yi kullanarak GERCEK `classify()`
    imzasini (services/tr-en-bridge/src/ollama_nlu.py) baseline checksum
    ile karsilastirir. Doner: (degisti_mi, aciklama)."""
    from classify_contract_core import check_contract_drift, extract_function_signature

    source_path = repo_root / "services" / "tr-en-bridge" / "src" / "ollama_nlu.py"
    try:
        source = source_path.read_text(encoding="utf-8")
    except OSError as exc:
        return True, f"classify() kaynak dosyasi okunamadi: {source_path} ({exc})"

    try:
        baseline_lines = [
            line.strip() for line in baseline_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        baseline_checksum = baseline_lines[0] if baseline_lines else ""
    except OSError as exc:
        return True, f"classify() baseline checksum dosyasi okunamadi: {baseline_path} ({exc})"

    signature = extract_function_signature(source, "classify")
    return check_contract_drift(signature, baseline_checksum)


def _check_blockers(
    feature_name: str,
    blocker_conditions: list[str],
    *,
    evidence_payloads: list[dict[str, Any]],
    drift_report: dict[str, Any] | None,
    secrets_found: list,
    contract_changed: bool,
    evidence_schema_invalid: bool = False,
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
            if secrets_found:
                tripped.append(blocker)
        elif blocker == "classify_contract_changed":
            if contract_changed:
                tripped.append(blocker)
        elif blocker == "provider_is_stub_only":
            if feature_name == "emergency_legitimacy_required" and evidence_payloads:
                # "GERCEK saglayici kullanildi" iddiasi, provider alaninin
                # stub-disi olmasi TEK BASINA YETMEZ -- provider_evidence.checked
                # de True olmalidir (SKIPPED/checked=False, provider='jira'
                # YAPILANDIRILMAMIS OLSA BILE tripped'i yanlislikla
                # temizleyebilirdi -- bkz. "no implicit pass on unchecked
                # provider" gorev kisiti, AYNI ilke burada da gecerlidir).
                real_provider_used = any(
                    p.get("provider") not in ("mock", "jira_stub", "none", None)
                    and isinstance(p.get("provider_evidence"), dict)
                    and p["provider_evidence"].get("checked") is True
                    for p in evidence_payloads
                )
                if not real_provider_used:
                    tripped.append(blocker)
        elif blocker == "invalid_evidence_schema":
            if evidence_schema_invalid:
                tripped.append(blocker)
    return tripped


def _find_latest_fpr_summary(repo_root: Path) -> dict[str, Any] | None:
    candidates = sorted(repo_root.glob("reports/pilot_metrics_*/fpr_summary.json"))
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


def _find_latest_weekly_review_json(repo_root: Path) -> dict[str, Any] | None:
    """En son `reports/weekly_observability_*/review.json` (JSONL --
    her satir bir calistirmadir) dosyasinin SON (en yeni) girdisini
    doner. Markdown'a ASLA GERI DUSMEZ (gorev kisiti: "fallback to
    markdown ignored (as now)") -- `review.json` yoksa `None` doner."""
    candidates = sorted(repo_root.glob("reports/weekly_observability_*/review.json"))
    latest_entry = None
    latest_dt = None
    for c in candidates:
        try:
            lines = [ln for ln in c.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except OSError:
            continue
        for line in lines:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            dt = _extract_generated_at(entry)
            if dt is None:
                continue
            if latest_dt is None or dt > latest_dt:
                latest_dt = dt
                latest_entry = entry
    return latest_entry


def collect_evidence_schema_errors(
    repo_root: Path, *, fpr_summary: dict[str, Any] | None, weekly_context: dict[str, Any] | None
) -> list[str]:
    """ASGARI kanit sema kontrolleri (gorev kisiti: "Minimum data
    contracts") -- `fpr_summary.json`/`review.json` (bulunduysa) VE HER
    bulunan `legitimacy_report.json` dosyasi `pilot_promotion_core.py`'daki
    saf `validate_*_schema()` fonksiyonlariyla dogrulanir. Hata BULUNURSA
    (bos olmayan liste), `invalid_evidence_schema` blocker'i tetiklenir
    (bkz. `_check_blockers`) -- REJECT'e zorlar, cunku SEMASI BOZUK bir
    kanit dosyasina dayanarak PROMOTE etmek fabrike edilmis bir karar
    riski tasir."""
    from pilot_promotion_core import (
        validate_fpr_summary_schema,
        validate_legitimacy_report_schema,
        validate_weekly_review_schema,
    )

    errors: list[str] = []
    if fpr_summary is not None:
        errors += [f"fpr_summary.json: {e}" for e in validate_fpr_summary_schema(fpr_summary)]
    if weekly_context is not None:
        errors += [f"review.json: {e}" for e in validate_weekly_review_schema(weekly_context)]

    legitimacy_files, _ = _find_evidence_files(
        repo_root,
        ["reports/emergency_legitimacy_*/legitimacy_report.json", "reports/v1_2_pilot_*/**/legitimacy_report.json"],
    )
    for f in legitimacy_files:
        payload = _load_json_safely(f)
        if payload is None:
            continue
        errors += [f"{f}: {e}" for e in validate_legitimacy_report_schema(payload)]

    return errors


def compute_evidence_summary(
    feature_name: str,
    criteria: dict[str, Any],
    *,
    repo_root: Path,
    now: datetime,
    secrets_found: list,
    contract_changed: bool,
    fpr_summary: dict[str, Any] | None = None,
    evidence_schema_invalid: bool = False,
):
    from pilot_promotion_core import EvidenceSummary

    patterns = criteria["required_evidence_paths"]
    files, missing_patterns = _find_evidence_files(repo_root, patterns)

    # `payloads`: TUM basariyla yuklenen kanit dosyalari (blocker kontrolleri
    # -- ornegin provider_is_stub_only -- BUNU gorur, SKIPPED dahil).
    # `countable_payloads`: yalnizca GERCEKTEN tamamlanmis bir dogrulamayi
    # TEMSIL EDEN girdiler -- `emergency_legitimacy_required` icin
    # `legitimacy_status=SKIPPED` olan bir legitimacy_report.json "hic
    # kontrol edilmedi" anlamina gelir, bu yuzden runs/observation_days/
    # false_positive_rate hesaplamasina KATILMAZ (gorev kisiti: "evaluator
    # treats SKIPPED as non-promotable evidence for legitimacy_required").
    payloads: list[dict[str, Any]] = []
    countable_payloads: list[dict[str, Any]] = []
    generated_ats: list[datetime] = []
    fp_rates: list[float] = []
    skipped_evidence_count = 0
    for f in files:
        payload = _load_json_safely(f)
        if payload is None:
            continue
        payloads.append(payload)
        is_skipped_legitimacy = (
            feature_name == "emergency_legitimacy_required" and payload.get("legitimacy_status") == "SKIPPED"
        )
        if is_skipped_legitimacy:
            skipped_evidence_count += 1
            continue
        countable_payloads.append(payload)
        dt = _extract_generated_at(payload)
        if dt is not None:
            generated_ats.append(dt)
        if isinstance(payload.get("false_positive_rate"), (int, float)):
            fp_rates.append(payload["false_positive_rate"])

    observation_days = 0.0
    if generated_ats:
        earliest = min(generated_ats)
        observation_days = max(0.0, (now - earliest).total_seconds() / 86400)

    # `fpr_summary.json` (bkz. compute_pilot_false_positive_rate.py) VARSA
    # VE bu ozellik icin GERCEKTEN hesaplanmissa (status=COMPUTED, yeterli
    # insan-adjudikasyonu var) ONCELIKLIDIR -- eski davranisa (kanit
    # dosyalarina gomulu tek-tek `false_positive_rate` alanlarinin
    # ortalamasi, Commit Y'den) YALNIZCA fpr_summary YOKSA duser (geriye
    # donuk uyumluluk).
    fp_rate: float | None
    if fpr_summary and feature_name in fpr_summary.get("features", {}):
        feature_fpr = fpr_summary["features"][feature_name]
        if feature_fpr.get("status") == "COMPUTED" and isinstance(feature_fpr.get("false_positive_rate"), (int, float)):
            fp_rate = feature_fpr["false_positive_rate"]
        else:
            fp_rate = None
    else:
        fp_rate = (sum(fp_rates) / len(fp_rates)) if fp_rates else None

    drift_report = _find_latest_drift_report(repo_root)
    unresolved_critical = 0
    if drift_report:
        unresolved_critical = sum(
            1 for f in drift_report.get("findings", [])
            if f.get("category") == "emergency_governance" and f.get("severity") == "CRITICAL"
        )

    blockers = _check_blockers(
        feature_name, criteria["blocker_conditions"], evidence_payloads=payloads, drift_report=drift_report,
        secrets_found=secrets_found, contract_changed=contract_changed, evidence_schema_invalid=evidence_schema_invalid,
    )

    return EvidenceSummary(
        runs=len(countable_payloads),
        observation_days=observation_days,
        false_positive_rate=fp_rate,
        unresolved_critical=unresolved_critical,
        blockers_tripped=blockers,
        missing_evidence_patterns=missing_patterns,
        evidence_files=[str(f) for f in files],
        skipped_evidence_count=skipped_evidence_count,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="v1.2 pilot ozelliklerinin PILOT->ENFORCED terfi degerlendirmesi")
    parser.add_argument("--repo-root", default=str(REPO_ROOT_DEFAULT))
    parser.add_argument("--criteria-path", default=None)
    parser.add_argument("--secret-scan-config-path", default=None)
    parser.add_argument("--contract-baseline-path", default=None)
    parser.add_argument(
        "--skip-secret-scan", action="store_true",
        help="Repo-genelinde secret taramasini ATLA (yavas olabilir -- test/gelistirme icin)",
    )
    parser.add_argument(
        "--rehearsal", action="store_true",
        help="Karar PROVASI (rehearsal) modu: AYNI karari hesaplar ama pilot_flags_state.json'a "
        "veya baska hicbir kalici duruma DOKUNMAZ (zaten dokunmuyordu) -- 'kac calistirma/gun daha "
        "gerekli' seklinde SOMUT bir rehearsal_report.md/json yazar, promotion_report.md/json YAZMAZ.",
    )
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    repo_root = Path(args.repo_root)
    criteria_path = Path(args.criteria_path) if args.criteria_path else repo_root / DEFAULT_CRITERIA_PATH
    secret_scan_config_path = (
        Path(args.secret_scan_config_path) if args.secret_scan_config_path
        else repo_root / "infra" / "monitoring" / "governance" / "secret_scan_patterns_v1.json"
    )
    contract_baseline_path = (
        Path(args.contract_baseline_path) if args.contract_baseline_path
        else repo_root / "infra" / "contracts" / "classify_contract_checksum_v1.txt"
    )

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

    # Blocker taramalari HER OZELLIK icin degil, BIR KEZ calisir (repo
    # genelinde secret taramasi/classify() sozlesme kontrolu ozellikten
    # BAGIMSIZDIR).
    secrets_found = [] if args.skip_secret_scan else scan_repo_for_secrets(repo_root, secret_scan_config_path)
    if secrets_found:
        print(f"UYARI: {len(secrets_found)} olasi sir bulgusu tespit edildi (secrets_committed blocker'i etkiler).")
    contract_changed, contract_reason = check_classify_contract_drift(repo_root, contract_baseline_path)
    print(f"classify() sozlesme kontrolu: {contract_reason}")

    fpr_summary = _find_latest_fpr_summary(repo_root)
    weekly_context = _find_latest_weekly_review_json(repo_root)
    if weekly_context:
        print(f"En son yapisal haftalik kanit bulundu: durum={weekly_context.get('status')}")

    evidence_schema_errors = collect_evidence_schema_errors(repo_root, fpr_summary=fpr_summary, weekly_context=weekly_context)
    if evidence_schema_errors:
        print(f"UYARI: {len(evidence_schema_errors)} kanit semasi hatasi tespit edildi (invalid_evidence_schema blocker'ini etkiler).")
        for e in evidence_schema_errors:
            print(f"  - {e}")
    evidence_schema_invalid = bool(evidence_schema_errors)

    decisions = []
    evidence_by_feature: dict[str, Any] = {}
    criteria_by_feature: dict[str, Any] = {}
    for feature_name in FEATURE_NAMES:
        criteria = get_feature_criteria(manifest, feature_name)
        evidence = compute_evidence_summary(
            feature_name, criteria, repo_root=repo_root, now=now,
            secrets_found=secrets_found, contract_changed=contract_changed, fpr_summary=fpr_summary,
            evidence_schema_invalid=evidence_schema_invalid,
        )
        criteria_by_feature[feature_name] = criteria
        evidence_by_feature[feature_name] = evidence
        decisions.append(evaluate_feature(feature_name, criteria, evidence))

    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        ts = now.strftime("%Y%m%dT%H%M%SZ")
        out_dir = repo_root / "reports" / f"pilot_promotion_{ts}"

    if args.rehearsal:
        from pilot_promotion_core import compute_rehearsal_detail, write_rehearsal_report

        details = [
            compute_rehearsal_detail(d.feature, criteria_by_feature[d.feature], evidence_by_feature[d.feature], d)
            for d in decisions
        ]
        write_rehearsal_report(details, out_dir, generated_at=generated_at)
        print("=== KARAR PROVASI (REHEARSAL) -- hicbir kalici durum degistirilmedi ===")
        for d in details:
            note = ""
            if d["decision"] == "REJECT":
                note = f"blocker(lar): {', '.join(d['blockers_tripped'])}"
            else:
                parts = []
                if d["runs_needed"] > 0:
                    parts.append(f"{d['runs_needed']} calistirma daha")
                if d["days_needed"] > 0:
                    parts.append(f"{d['days_needed']:.1f} gun daha")
                note = ", ".join(parts) if parts else "tum kriterler karsilandi"
            print(f"{d['feature']}: {d['decision']} -- {note}")
        print(f"evidence_dir={out_dir}")
        return overall_exit_code(decisions)

    write_promotion_report(decisions, out_dir, generated_at=generated_at)
    if weekly_context:
        (out_dir / "weekly_evidence_context.json").write_text(
            json.dumps(weekly_context, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    for d in decisions:
        print(f"{d.feature}: {d.decision} -- {d.rationale}")
    print(f"evidence_dir={out_dir}")

    return overall_exit_code(decisions)


if __name__ == "__main__":
    sys.exit(main())
