"""v1.2 pilot ozelliklerinin PILOT -> ENFORCED terfi kararini veren saf
mantik (dosya/ag G/C YOK) -- `scripts/ops/evaluate_pilot_promotion.py`
GERCEK kanit dosyalarini tarayip bir `EvidenceSummary` insa eder ve bu
modulun `evaluate_feature()`'ina besler; boylece "hangi kanit hangi
karara karsilik gelir" mantigi, gercek dosya sistemi olmadan
deterministik test edilebilir (bkz. `observability_gates_core.py`/
`observability_drift_core.py` ile AYNI ayrim ilkesi).

Ayrica `pilot_flags_state.json`'in (kalici terfi durumu defteri) SAF
durum-gecisi (state transition) mantigi da burada yasar --
`promote_pilot_flags.ps1`/`rollback_pilot_flags.ps1`'in gomulu
suruculeri yalnizca dosya okuma/yazma yapar, KARAR VERMEZ.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DECISION_PROMOTE = "PROMOTE"
DECISION_EXTEND_PILOT = "EXTEND_PILOT"
DECISION_REJECT = "REJECT"
VALID_DECISIONS = (DECISION_PROMOTE, DECISION_EXTEND_PILOT, DECISION_REJECT)

REQUIRED_CRITERIA_FIELDS = (
    "observation_min_days",
    "min_runs",
    "max_false_positive_rate",
    "max_unresolved_critical",
    "required_evidence_paths",
    "blocker_conditions",
)

FEATURE_NAMES = ("emergency_chain_matching", "auto_rollback_on_verify_fail", "emergency_legitimacy_required")


# ---------------------------------------------------------------------------
# Kriter manifestosu dogrulama
# ---------------------------------------------------------------------------


def validate_criteria_manifest(manifest: dict[str, Any]) -> list[str]:
    """`pilot_promotion_criteria_v1.json`'un yapisini dogrular -- hata
    mesajlari listesi doner (bos = gecerli). ASLA exception firlatmaz."""
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return [f"manifesto bir obje (dict) olmali, gozlenen tip: {type(manifest).__name__}"]
    features = manifest.get("features")
    if not isinstance(features, dict):
        return ["'features' alani eksik veya obje (dict) degil"]

    for name in FEATURE_NAMES:
        if name not in features:
            errors.append(f"'features' icinde beklenen ozellik eksik: '{name}'")
            continue
        criteria = features[name]
        if not isinstance(criteria, dict):
            errors.append(f"'{name}' icin kriterler obje (dict) degil")
            continue
        for field_name in REQUIRED_CRITERIA_FIELDS:
            if field_name not in criteria:
                errors.append(f"'{name}': zorunlu kriter alani eksik: '{field_name}'")

        if "observation_min_days" in criteria and not isinstance(criteria["observation_min_days"], (int, float)):
            errors.append(f"'{name}.observation_min_days' sayisal olmali")
        elif "observation_min_days" in criteria and criteria["observation_min_days"] <= 0:
            errors.append(f"'{name}.observation_min_days' pozitif olmali")

        if "min_runs" in criteria and not isinstance(criteria["min_runs"], int):
            errors.append(f"'{name}.min_runs' tam sayi olmali")

        if "max_false_positive_rate" in criteria:
            rate = criteria["max_false_positive_rate"]
            if not isinstance(rate, (int, float)) or not (0 <= rate <= 1):
                errors.append(f"'{name}.max_false_positive_rate' [0, 1] araliginda sayisal olmali")

        if "max_unresolved_critical" in criteria and not isinstance(criteria["max_unresolved_critical"], int):
            errors.append(f"'{name}.max_unresolved_critical' tam sayi olmali")

        if "required_evidence_paths" in criteria and not isinstance(criteria["required_evidence_paths"], list):
            errors.append(f"'{name}.required_evidence_paths' liste olmali")

        if "blocker_conditions" in criteria and not isinstance(criteria["blocker_conditions"], list):
            errors.append(f"'{name}.blocker_conditions' liste olmali")

    return errors


def get_feature_criteria(manifest: dict[str, Any], feature_name: str, *, observation_days_override: int | None = None) -> dict[str, Any]:
    """`feature_name` icin kriterleri doner -- `observation_days_override`
    verilirse (ornegin 28 gunluk 'genisletilmis gozlem' modu icin),
    `observation_min_days`'i GEÇICI olarak degistirir (manifestoyu
    KALICI olarak DEGISTIRMEZ)."""
    criteria = dict(manifest["features"][feature_name])
    if observation_days_override is not None:
        criteria["observation_min_days"] = observation_days_override
    return criteria


# ---------------------------------------------------------------------------
# Minimum kanit semasi kontrolleri -- "invalid_evidence_schema" blocker'i
# icin kullanilir (bkz. evaluate_pilot_promotion.py). Hicbiri exception
# FIRLATMAZ -- `validate_criteria_manifest`/`pilot_fpr_core.validate_adjudications_file`
# ile AYNI "hata mesaji listesi doner, bos=gecerli" deseni.
# ---------------------------------------------------------------------------

# `pilot_fpr_core.py`'a bagimlilik EKLEMEMEK icin (dongusel import riski
# + bu modulun "saf/bagimsiz" ilkesi), STATUS_COMPUTED/STATUS_INSUFFICIENT_DATA
# degerleri burada SABIT DIZE olarak yinelenir -- `pilot_fpr_core.py`'daki
# GERCEK sabitlerle senkron tutulmalidir (degisirse ikisi de guncellenmeli).
STATUS_COMPUTED_LITERAL = "COMPUTED"
STATUS_INSUFFICIENT_DATA_LITERAL = "INSUFFICIENT_DATA"


def validate_fpr_summary_schema(payload: Any) -> list[str]:
    """`compute_pilot_false_positive_rate.py::write_fpr_summary`'nin
    urettigi `fpr_summary.json`'un ASGARI alan/tip sozlesmesini
    dogrular."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return [f"fpr_summary.json bir obje (dict) olmali, gozlenen tip: {type(payload).__name__}"]
    if not isinstance(payload.get("generated_at"), str):
        errors.append("'generated_at' eksik veya string degil")
    features = payload.get("features")
    if not isinstance(features, dict):
        return errors + ["'features' eksik veya obje (dict) degil"]
    for name, entry in features.items():
        if not isinstance(entry, dict):
            errors.append(f"features['{name}']: obje (dict) degil")
            continue
        for required in ("total_signals", "adjudicated_signals", "confirmed_false_positives", "status"):
            if required not in entry:
                errors.append(f"features['{name}']: zorunlu alan eksik: '{required}'")
        if "status" in entry and entry["status"] not in (STATUS_COMPUTED_LITERAL, STATUS_INSUFFICIENT_DATA_LITERAL):
            errors.append(f"features['{name}']: gecersiz status: {entry['status']!r}")
        fp_rate = entry.get("false_positive_rate")
        if fp_rate is not None and not isinstance(fp_rate, (int, float)):
            errors.append(f"features['{name}']: false_positive_rate sayisal veya null olmali")
    return errors


def validate_weekly_review_schema(payload: Any) -> list[str]:
    """`export_weekly_observability_json.py::export_review_json`'un
    urettigi TEK bir `review.json` girdisinin ASGARI alan/tip
    sozlesmesini dogrular."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return [f"review.json girdisi bir obje (dict) olmali, gozlenen tip: {type(payload).__name__}"]
    for required in ("iso_week", "generated_at", "status", "reasons"):
        if required not in payload:
            errors.append(f"zorunlu alan eksik: '{required}'")
    if "reasons" in payload and not isinstance(payload["reasons"], list):
        errors.append("'reasons' liste olmali")
    if "top_alerts" in payload and not isinstance(payload["top_alerts"], list):
        errors.append("'top_alerts' liste olmali")
    for ratio_field in ("fallback_ratio", "null_intent_ratio"):
        if ratio_field in payload and payload[ratio_field] is not None and not isinstance(payload[ratio_field], (int, float)):
            errors.append(f"'{ratio_field}' sayisal veya null olmali")
    return errors


def validate_legitimacy_report_schema(payload: Any) -> list[str]:
    """`emergency_legitimacy_core.py::write_legitimacy_report`'un
    urettigi `legitimacy_report.json`'un ASGARI alan/tip sozlesmesini
    dogrular -- ozellikle `legitimacy_status` (PASS/FAIL/SKIPPED) ve
    (varsa) `provider_evidence` blogunun sekli."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return [f"legitimacy_report.json bir obje (dict) olmali, gozlenen tip: {type(payload).__name__}"]
    if "legitimacy_status" not in payload:
        errors.append("zorunlu alan eksik: 'legitimacy_status'")
    elif payload["legitimacy_status"] not in ("PASS", "FAIL", "SKIPPED"):
        errors.append(f"gecersiz legitimacy_status: {payload['legitimacy_status']!r}")
    provider_evidence = payload.get("provider_evidence")
    if provider_evidence is not None:
        if not isinstance(provider_evidence, dict):
            errors.append("'provider_evidence' obje (dict) veya null olmali")
        else:
            for required in ("checked", "found"):
                if required not in provider_evidence:
                    errors.append(f"provider_evidence: zorunlu alan eksik: '{required}'")
    return errors


# ---------------------------------------------------------------------------
# Kanit ozeti + karar mantigi
# ---------------------------------------------------------------------------


@dataclass
class EvidenceSummary:
    runs: int = 0
    observation_days: float = 0.0
    false_positive_rate: float | None = None
    unresolved_critical: int = 0
    blockers_tripped: list[str] = field(default_factory=list)
    missing_evidence_patterns: list[str] = field(default_factory=list)
    evidence_files: list[str] = field(default_factory=list)
    skipped_evidence_count: int = 0


@dataclass
class PromotionDecision:
    feature: str
    decision: str
    fulfilled_criteria: list[str] = field(default_factory=list)
    violated_criteria: list[str] = field(default_factory=list)
    rationale: str = ""

    def __post_init__(self) -> None:
        if self.decision not in VALID_DECISIONS:
            raise ValueError(f"gecersiz karar: {self.decision!r} (gecerli: {VALID_DECISIONS})")


def evaluate_feature(feature_name: str, criteria: dict[str, Any], evidence: EvidenceSummary) -> PromotionDecision:
    """SAF karar fonksiyonu -- dosya G/C YAPMAZ, yalnizca ONCEDEN
    hesaplanmis bir `EvidenceSummary`'yi kriterlerle karsilastirir.

    Sira (ilk esleseni KAZANIR):
      1. `evidence.blockers_tripped` BOS DEGILSE -> REJECT (kosulsuz --
         hicbir sayisal kriter bunu GERI ALAMAZ).
      2. Herhangi bir sayisal/kanit kriteri KARSILANMAMISSA -> EXTEND_PILOT.
      3. TUM kriterler karsilaniyorsa -> PROMOTE.
    """
    if evidence.blockers_tripped:
        return PromotionDecision(
            feature=feature_name,
            decision=DECISION_REJECT,
            violated_criteria=[f"blocker_condition:{b}" for b in evidence.blockers_tripped],
            rationale=f"Blocker kosul(lar)i tetiklendi: {', '.join(evidence.blockers_tripped)} -- REJECT (sayisal kriterlerden BAGIMSIZ)",
        )

    fulfilled: list[str] = []
    violated: list[str] = []

    if evidence.missing_evidence_patterns:
        violated.append(f"required_evidence_paths: eksik desen(ler): {evidence.missing_evidence_patterns}")
    else:
        fulfilled.append("required_evidence_paths: tum gerekli kanit turleri en az bir kez bulundu")

    min_runs = criteria["min_runs"]
    if evidence.runs < min_runs:
        violated.append(f"min_runs: {evidence.runs} < {min_runs}")
    else:
        fulfilled.append(f"min_runs: {evidence.runs} >= {min_runs}")

    obs_min = criteria["observation_min_days"]
    if evidence.observation_days < obs_min:
        violated.append(f"observation_min_days: {evidence.observation_days:.1f} < {obs_min}")
    else:
        fulfilled.append(f"observation_min_days: {evidence.observation_days:.1f} >= {obs_min}")

    max_fp = criteria["max_false_positive_rate"]
    if evidence.false_positive_rate is None:
        violated.append("max_false_positive_rate: kanitta false_positive_rate verisi YOK (once bu veri saglanmali)")
    elif evidence.false_positive_rate > max_fp:
        violated.append(f"max_false_positive_rate: {evidence.false_positive_rate:.3f} > {max_fp}")
    else:
        fulfilled.append(f"max_false_positive_rate: {evidence.false_positive_rate:.3f} <= {max_fp}")

    max_crit = criteria["max_unresolved_critical"]
    if evidence.unresolved_critical > max_crit:
        violated.append(f"max_unresolved_critical: {evidence.unresolved_critical} > {max_crit}")
    else:
        fulfilled.append(f"max_unresolved_critical: {evidence.unresolved_critical} <= {max_crit}")

    if violated:
        return PromotionDecision(
            feature=feature_name, decision=DECISION_EXTEND_PILOT,
            fulfilled_criteria=fulfilled, violated_criteria=violated,
            rationale=f"{len(violated)} kriter henuz karsilanmadi -- pilot devam etmeli",
        )
    return PromotionDecision(
        feature=feature_name, decision=DECISION_PROMOTE,
        fulfilled_criteria=fulfilled, violated_criteria=[],
        rationale="TUM olculebilir kriterler karsilandi VE hicbir blocker tetiklenmedi",
    )


def overall_exit_code(decisions: list[PromotionDecision]) -> int:
    """Gorev tanimindan BIREBIR: 0 hepsi PROMOTE, 1 en az bir EXTEND_PILOT
    VE hicbir REJECT yoksa, 2 herhangi bir REJECT varsa."""
    if any(d.decision == DECISION_REJECT for d in decisions):
        return 2
    if any(d.decision == DECISION_EXTEND_PILOT for d in decisions):
        return 1
    return 0


# ---------------------------------------------------------------------------
# pilot_flags_state.json -- saf durum-gecisi (state transition) mantigi
# ---------------------------------------------------------------------------


def default_pilot_flags_state() -> dict[str, Any]:
    return {
        name: {"promoted": False, "promoted_at_utc": None, "promotion_report_path": None}
        for name in FEATURE_NAMES
    }


def load_pilot_flags_state(path: Path) -> dict[str, Any]:
    """`infra/monitoring/governance/pilot_flags_state.json`'i okur --
    dosya YOKSA (henuz hicbir ozellik terfi etmemis, NORMAL/varsayilan
    durum) `default_pilot_flags_state()` doner. Bozuk JSON da AYNI
    sekilde varsayilana duser (SESSIZCE -- terfi durumunun okunamamasi,
    'hicbir sey terfi etmemis' ile AYNI GUVENLI varsayima duser, ASLA
    hataya dusup zorlayici modu yanlislikla ACMAZ)."""
    if not path.exists():
        return default_pilot_flags_state()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default_pilot_flags_state()
    state = default_pilot_flags_state()
    if isinstance(loaded, dict):
        for name in FEATURE_NAMES:
            if name in loaded and isinstance(loaded[name], dict):
                state[name].update(loaded[name])
    return state


def write_pilot_flags_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def is_feature_promoted(state: dict[str, Any], feature_name: str) -> bool:
    entry = state.get(feature_name)
    return bool(entry and entry.get("promoted") is True)


def is_legitimacy_enforcement_active(state: dict[str, Any], gov_env_var_value: str | None) -> bool:
    """`GOV_EMERGENCY_LEGITIMACY_REQUIRED=1` TEK BASINA YETERLI DEGILDIR
    -- `pilot_flags_state.json`'da `emergency_legitimacy_required.promoted=true`
    OLMASI da ZORUNLUDUR (gorev kisiti: "if and only if feature decision
    ... is PROMOTE"). Bu ikili-kapi tasarimi, bir operatorun env var'i
    tek basina degistirerek gercek bir PROMOTE karari OLMADAN
    zorlayici (blocking) modu ETKINLESTIREMEMESINI GARANTI EDER."""
    return gov_env_var_value == "1" and is_feature_promoted(state, "emergency_legitimacy_required")


def apply_promotions_to_state(
    state: dict[str, Any],
    decisions: dict[str, str],
    *,
    generated_at: str,
    promotion_report_path: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """SAF durum-gecisi -- dosya G/C YAPMAZ. `decisions`: {feature_name: karar}.
    YALNIZCA `decision == PROMOTE` olan ozelliklerin durumu DEGISTIRILIR
    (gorev kisiti: "only applies flags when evaluator decision is
    PROMOTE for that feature") -- EXTEND_PILOT/REJECT olan ozellikler
    MEVCUT durumlariyla DEGISTIRILMEDEN kalir (rollback icin ONCEKI
    durumlari saklamaya GEREK yok, cunku zaten degismediler).

    Doner: (yeni_state, degisen_ozelliklerin_ESKI_degerleri) -- ikinci
    deger, rollback'in "previous_state" olarak SAKLAYACAGI anlik
    goruntudur."""
    new_state = {k: dict(v) for k, v in state.items()}
    previous_values: dict[str, dict[str, Any]] = {}

    for feature_name, decision in decisions.items():
        if decision != DECISION_PROMOTE:
            continue
        previous_values[feature_name] = dict(new_state.get(feature_name, default_pilot_flags_state()[feature_name]))
        new_state[feature_name] = {
            "promoted": True,
            "promoted_at_utc": generated_at,
            "promotion_report_path": promotion_report_path,
        }

    return new_state, previous_values


def restore_previous_state(state: dict[str, Any], previous_values: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """SAF geri-alma -- `apply_promotions_to_state`'in dondurdugu
    `previous_values` anlik goruntusunu `state` uzerine GERI YAZAR."""
    restored = {k: dict(v) for k, v in state.items()}
    for feature_name, old_value in previous_values.items():
        restored[feature_name] = dict(old_value)
    return restored


# ---------------------------------------------------------------------------
# Rapor uretimi
# ---------------------------------------------------------------------------


def render_promotion_report_md(decisions: list[PromotionDecision], *, generated_at: str) -> str:
    lines = [
        "# v1.2 Pilot Terfi (Promotion) Degerlendirme Raporu",
        "",
        f"Uretildi (UTC): {generated_at}",
        f"Genel sonuc: **{'PROMOTE' if all(d.decision == DECISION_PROMOTE for d in decisions) else ('REJECT' if any(d.decision == DECISION_REJECT for d in decisions) else 'EXTEND_PILOT')}** (exit code {overall_exit_code(decisions)})",
        "",
        "| Ozellik | Karar | Gerekce |",
        "|---|---|---|",
    ]
    for d in decisions:
        lines.append(f"| {d.feature} | **{d.decision}** | {d.rationale} |")
    lines.append("")

    for d in decisions:
        lines.append(f"## {d.feature} -- {d.decision}")
        lines.append("")
        if d.fulfilled_criteria:
            lines.append("**Karsilanan kriterler:**")
            for c in d.fulfilled_criteria:
                lines.append(f"- OK {c}")
            lines.append("")
        if d.violated_criteria:
            lines.append("**Karsilanmayan/ihlal edilen kriterler:**")
            for c in d.violated_criteria:
                lines.append(f"- X {c}")
            lines.append("")
    return "\n".join(lines) + "\n"


def write_promotion_report(decisions: list[PromotionDecision], out_dir: Path, *, generated_at: str) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md = render_promotion_report_md(decisions, generated_at=generated_at)
    md_path = out_dir / "promotion_report.md"
    md_path.write_text(md, encoding="utf-8")

    json_path = out_dir / "promotion_report.json"
    payload = {
        "generated_at": generated_at,
        "overall_exit_code": overall_exit_code(decisions),
        "decisions": [
            {
                "feature": d.feature, "decision": d.decision,
                "fulfilled_criteria": d.fulfilled_criteria, "violated_criteria": d.violated_criteria,
                "rationale": d.rationale,
            }
            for d in decisions
        ],
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"md": md_path, "json": json_path}


# ---------------------------------------------------------------------------
# Karar provasi (rehearsal) modu -- durum/bayrak MUTASYONU YAPMAZ, yalnizca
# `evaluate_feature()`'in ZATEN urettigi karari + kanit ozetini "kac
# calistirma/gun daha gerekli" seklinde SOMUT bir bicimde sunar.
# ---------------------------------------------------------------------------


def compute_rehearsal_detail(
    feature_name: str, criteria: dict[str, Any], evidence: EvidenceSummary, decision: PromotionDecision
) -> dict[str, Any]:
    """SAF -- dosya/durum G/C YAPMAZ. `evaluate_feature()`'in ZATEN
    hesapladigi karari DEGISTIRMEZ, yalnizca eksik kriterleri SOMUT
    sayilara (kac calistirma/gun daha gerekli) cevirir."""
    runs_needed = max(0, criteria["min_runs"] - evidence.runs)
    days_needed = max(0.0, criteria["observation_min_days"] - evidence.observation_days)

    max_fp = criteria["max_false_positive_rate"]
    if evidence.false_positive_rate is None:
        fp_note = "veri yok (once FPR hesaplanmali -- bkz. compute_pilot_false_positive_rate.py)"
    elif evidence.false_positive_rate > max_fp:
        fp_note = f"{evidence.false_positive_rate:.3f} > {max_fp} (esik asildi)"
    else:
        fp_note = f"{evidence.false_positive_rate:.3f} <= {max_fp} (OK)"

    return {
        "feature": feature_name,
        "decision": decision.decision,
        "rationale": decision.rationale,
        "runs_so_far": evidence.runs,
        "min_runs_required": criteria["min_runs"],
        "runs_needed": runs_needed,
        "observation_days_so_far": round(evidence.observation_days, 1),
        "observation_min_days_required": criteria["observation_min_days"],
        "days_needed": round(days_needed, 1),
        "false_positive_rate_note": fp_note,
        "unresolved_critical": evidence.unresolved_critical,
        "max_unresolved_critical": criteria["max_unresolved_critical"],
        "blockers_tripped": list(evidence.blockers_tripped),
        "skipped_evidence_count": evidence.skipped_evidence_count,
    }


def render_rehearsal_report_md(details: list[dict[str, Any]], *, generated_at: str) -> str:
    lines = [
        "# v1.2 Pilot Terfi -- Karar Provasi (Rehearsal, salt-okunur)",
        "",
        f"Uretildi (UTC): {generated_at}",
        "",
        "**BU BIR PROVA'DIR** -- `pilot_flags_state.json` VEYA baska hicbir "
        "kalici durum DEGISTIRILMEDI. Gercek bir terfi icin normal (rehearsal "
        "OLMAYAN) bir `evaluate_pilot_promotion.py` calistirmasi + "
        "`promote_pilot_flags.ps1 -Apply` GEREKIR.",
        "",
        "| Ozellik | Karar | Eksik calistirma | Eksik gun | FPR durumu |",
        "|---|---|---|---|---|",
    ]
    for d in details:
        lines.append(
            f"| {d['feature']} | **{d['decision']}** | {d['runs_needed']} "
            f"({d['runs_so_far']}/{d['min_runs_required']}) | {d['days_needed']:.1f} "
            f"({d['observation_days_so_far']:.1f}/{d['observation_min_days_required']}) "
            f"| {d['false_positive_rate_note']} |"
        )
    lines.append("")

    for d in details:
        lines.append(f"## {d['feature']} -- {d['decision']}")
        lines.append("")
        lines.append(f"- {d['rationale']}")
        if d["decision"] == "REJECT":
            lines.append(f"- Blocker(lar) tetiklendi: {', '.join(d['blockers_tripped'])} -- sayisal kriterlerden BAGIMSIZ, eksik calistirma/gun bunu GERI ALAMAZ")
        else:
            if d["runs_needed"] > 0:
                lines.append(f"- **{d['runs_needed']} calistirma daha gerekli** ({d['runs_so_far']}/{d['min_runs_required']})")
            if d["days_needed"] > 0:
                lines.append(f"- **{d['days_needed']:.1f} gun daha gozlem gerekli** ({d['observation_days_so_far']:.1f}/{d['observation_min_days_required']})")
            lines.append(f"- max_false_positive_rate: {d['false_positive_rate_note']}")
            lines.append(f"- unresolved_critical: {d['unresolved_critical']} (esik: {d['max_unresolved_critical']})")
        if d["skipped_evidence_count"]:
            lines.append(
                f"- NOT: {d['skipped_evidence_count']} kanit girdisi SKIPPED (checked=false) oldugu icin "
                "yukaridaki sayimlara DAHIL EDILMEDI -- bunlar 'kontrol edilmedi', promosyona 'katkida bulunan' kanit DEGILDIR."
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def write_rehearsal_report(details: list[dict[str, Any]], out_dir: Path, *, generated_at: str) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md = render_rehearsal_report_md(details, generated_at=generated_at)
    md_path = out_dir / "rehearsal_report.md"
    md_path.write_text(md, encoding="utf-8")

    json_path = out_dir / "rehearsal_report.json"
    payload = {"generated_at": generated_at, "rehearsal": True, "details": details}
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"md": md_path, "json": json_path}


def render_flag_apply_report_md(
    decisions_by_feature: dict[str, str], applied: dict[str, bool], *, generated_at: str, dry_run: bool
) -> str:
    lines = [
        "# Pilot Flag Terfi Uygulama (Apply) Raporu",
        "",
        f"Uretildi (UTC): {generated_at}",
        f"Mod: {'DRY-RUN (hicbir durum degistirilmedi)' if dry_run else 'APPLY (durum guncellendi)'}",
        "",
        "| Ozellik | Degerlendirme Karari | Uygulandi mi? |",
        "|---|---|---|",
    ]
    for feature, decision in decisions_by_feature.items():
        lines.append(f"| {feature} | {decision} | {'EVET' if applied.get(feature) else 'hayir'} |")
    lines.append("")
    lines.append(
        "NOT: Yalnizca degerlendirme karari `PROMOTE` olan ozellikler uygulanir "
        "(`EXTEND_PILOT`/`REJECT` olanlar MEVCUT durumlariyla degismeden kalir)."
    )
    return "\n".join(lines) + "\n"


def write_flag_apply_report(
    decisions_by_feature: dict[str, str], applied: dict[str, bool], out_dir: Path, *, generated_at: str,
    dry_run: bool, previous_state: dict[str, Any], new_state: dict[str, Any],
) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md = render_flag_apply_report_md(decisions_by_feature, applied, generated_at=generated_at, dry_run=dry_run)
    md_path = out_dir / "flag_apply_report.md"
    md_path.write_text(md, encoding="utf-8")

    json_path = out_dir / "flag_apply_report.json"
    payload = {
        "generated_at": generated_at,
        "dry_run": dry_run,
        "decisions_by_feature": decisions_by_feature,
        "applied": applied,
        "previous_state": previous_state,
        "new_state": new_state,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"md": md_path, "json": json_path}
