"""Esik degisikligi onay (approval) is akisi -- saf mantik.

`scripts/ops/generate_threshold_proposals.py`, `create_threshold_review_record.py`,
`apply_threshold_proposal.ps1`, `rollback_threshold_apply.ps1` bu modulu
kullanir -- proposal/review/apply-uygunluk mantigi, gercek dosya
sistemi/config yazma islemlerinden BAGIMSIZ, deterministik test edilir
(bkz. `observability_gates_core.py`/`observability_drift_core.py` ile
AYNI ayrim ilkesi).

Sema dogrulama NEDEN kendi yazdigimiz (hand-rolled)?
Bu projede harici `jsonschema` paketi KURULU DEGIL ve eklenmedi (bkz.
proje geneli stdlib-yalniz felsefesi -- FastAPI/Flask/uvicorn de yok,
`serve_metrics.py` stdlib `http.server` kullaniyor). `validate_against_schema()`,
`infra/monitoring/governance/threshold_proposal_schema_v1.json`'un yalnizca
`required` + `properties[].type`/`enum` alt-kumesini dogrular -- tam
JSON Schema uyumu iddia edilmez, ama bu gorevin ihtiyaci olan
"zorunlu alan var mi, tip/enum dogru mu" sorularini dogru cevaplar.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DECISION_APPROVE = "APPROVE"
DECISION_REJECT = "REJECT"
DECISION_NEEDS_DATA = "NEEDS_DATA"
DECISION_APPROVE_EMERGENCY = "APPROVE_EMERGENCY"
VALID_DECISIONS = (DECISION_APPROVE, DECISION_REJECT, DECISION_NEEDS_DATA, DECISION_APPROVE_EMERGENCY)

# `APPROVE` ile AYNI sekilde apply'a izin veren kararlar -- APPROVE_EMERGENCY
# EK OLARAK `validate_emergency_fields()`'i de gecmek ZORUNDADIR (bkz.
# check_apply_eligibility).
APPROVED_DECISIONS = (DECISION_APPROVE, DECISION_APPROVE_EMERGENCY)

# Acil durum (APPROVE_EMERGENCY) icin ZORUNLU alanlar -- gorev tanimindan
# BIREBIR: incident_id, justification, timebox_hours (maks 24), retro_review_due_utc.
EMERGENCY_REQUIRED_FIELDS = ("incident_id", "justification", "timebox_hours", "retro_review_due_utc")
EMERGENCY_MAX_TIMEBOX_HOURS = 24

_TYPE_MAP: dict[str, Any] = {
    "string": str,
    "number": (int, float),
    "object": dict,
    "array": list,
    "boolean": bool,
}


def validate_against_schema(instance: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """`required` alanlarinin varligini + `properties`'teki `type`/`enum`
    kisitlarini kontrol eder. Hata mesajlari listesini doner -- BOS
    LISTE = gecerli. Asla exception firlatmaz (bozuk/eksik `instance`
    bile olsa kontrollu bir hata listesi uretir)."""
    errors: list[str] = []
    if not isinstance(instance, dict):
        return [f"instance bir obje (dict) olmali, gozlenen tip: {type(instance).__name__}"]

    for field_name in schema.get("required", []):
        if field_name not in instance:
            errors.append(f"zorunlu alan eksik: '{field_name}'")

    properties = schema.get("properties", {})
    for field_name, value in instance.items():
        prop_schema = properties.get(field_name)
        if not prop_schema:
            continue
        expected_type = prop_schema.get("type")
        if expected_type in _TYPE_MAP and not isinstance(value, _TYPE_MAP[expected_type]):
            errors.append(
                f"'{field_name}' alani tip uyusmazligi: beklenen={expected_type}, "
                f"gozlenen={type(value).__name__}"
            )
        enum = prop_schema.get("enum")
        if enum and value not in enum:
            errors.append(f"'{field_name}' alani gecersiz deger: {value!r} (gecerli: {enum})")

    return errors


def compute_checksum(data: dict[str, Any], *, exclude_keys: tuple[str, ...] = ("checksum",)) -> str:
    """Verilen sozlugun (belirtilen alanlar HARIC) anahtar-sirali
    (`sort_keys=True`) kanonik JSON serilestirmesinin SHA256 hex
    digest'i -- alan sirasindan BAGIMSIZ, deterministik."""
    filtered = {k: v for k, v in data.items() if k not in exclude_keys}
    canonical = json.dumps(filtered, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_proposal(
    *,
    alert_name: str,
    source_window: str,
    current_values: dict[str, float],
    proposed_values: dict[str, float],
    confidence: str,
    sample_adequacy: str,
    risk_note: str,
    evidence_paths: list[str],
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Semaya uygun bir proposal sozlugu insa eder -- `checksum` alani
    EN SON, tum diger alanlar belirlendikten SONRA hesaplanir (kendi
    kendine referans etmemesi icin `compute_checksum` zaten `checksum`
    anahtarini haric tutar)."""
    generated_at_utc = generated_at_utc or datetime.now(timezone.utc).isoformat()
    ts_compact = generated_at_utc.replace(":", "").replace("-", "").split(".")[0]
    proposal_id = f"{alert_name}-{ts_compact}"

    proposal: dict[str, Any] = {
        "proposal_id": proposal_id,
        "generated_at_utc": generated_at_utc,
        "source_window": source_window,
        "alert_name": alert_name,
        "current_values": current_values,
        "proposed_values": proposed_values,
        "confidence": confidence,
        "sample_adequacy": sample_adequacy,
        "risk_note": risk_note,
        "evidence_paths": evidence_paths,
    }
    proposal["checksum"] = compute_checksum(proposal)
    return proposal


def build_review_record(
    *,
    reviewer: str,
    decision: str,
    rationale: str,
    proposal: dict[str, Any],
    approved_at_utc: str | None = None,
    incident_id: str | None = None,
    justification: str | None = None,
    timebox_hours: float | None = None,
    retro_review_due_utc: str | None = None,
    legitimacy_report_path: str | None = None,
) -> dict[str, Any]:
    """`incident_id`/`justification`/`timebox_hours`/`retro_review_due_utc`
    yalnizca `decision == DECISION_APPROVE_EMERGENCY` ise kayda EKLENIR --
    normal APPROVE/REJECT/NEEDS_DATA icin review_record.json'un ALANLARI
    (Commit S/T ile AYNI sekil) DEGISMEZ, bu da mevcut proposal/review
    artefaktlariyla geriye donuk uyumlulugu korur.

    `legitimacy_report_path` (v1.2 PILOT, opsiyonel, HERHANGI bir karar
    turu icin): `check_emergency_legitimacy.py`'nin ürettigi rapora bir
    BAGLANTI/REFERANSTIR -- yalnizca BILGI AMACLIDIR, `check_apply_eligibility`
    tarafindan OKUNMAZ/DOGRULANMAZ (henuz zorlayici degil, bkz. docs
    'Promotion criteria from pilot to enforced'). `None` ise (varsayilan)
    kayda HIC EKLENMEZ -- mevcut kayitlarla BIREBIR ayni sekil korunur."""
    if decision not in VALID_DECISIONS:
        raise ValueError(f"gecersiz karar: {decision!r} (gecerli: {VALID_DECISIONS})")

    record: dict[str, Any] = {
        "proposal_id": proposal["proposal_id"],
        "reviewer": reviewer,
        "decision": decision,
        "rationale": rationale,
        "approved_at_utc": approved_at_utc or datetime.now(timezone.utc).isoformat(),
        "linked_proposal_checksum": proposal["checksum"],
    }
    if decision == DECISION_APPROVE_EMERGENCY:
        record["incident_id"] = incident_id
        record["justification"] = justification
        record["timebox_hours"] = timebox_hours
        record["retro_review_due_utc"] = retro_review_due_utc
    if legitimacy_report_path is not None:
        record["legitimacy_report_path"] = legitimacy_report_path
    return record


def validate_emergency_fields(review_record: dict[str, Any]) -> list[str]:
    """`review_record.decision == DECISION_APPROVE_EMERGENCY` icin
    ZORUNLU alanlarin varligini/gecerliligini kontrol eder. Hata
    mesajlari listesi doner (bos = gecerli). `check_apply_eligibility`
    tarafindan APPLY ONCESI cagirilir -- review_record.json elle
    duzenlenmis/bozuk olsa bile GUVENLI (asla exception firlatmaz)."""
    errors: list[str] = []

    incident_id = review_record.get("incident_id")
    if not isinstance(incident_id, str) or not incident_id.strip():
        errors.append("APPROVE_EMERGENCY icin 'incident_id' zorunlu ve bos olamaz")

    justification = review_record.get("justification")
    if not isinstance(justification, str) or not justification.strip():
        errors.append("APPROVE_EMERGENCY icin 'justification' zorunlu ve bos olamaz")

    timebox_hours = review_record.get("timebox_hours")
    if isinstance(timebox_hours, bool) or not isinstance(timebox_hours, (int, float)):
        errors.append(f"APPROVE_EMERGENCY icin 'timebox_hours' sayisal olmali (gozlenen: {timebox_hours!r})")
    elif not (0 < timebox_hours <= EMERGENCY_MAX_TIMEBOX_HOURS):
        errors.append(
            f"APPROVE_EMERGENCY icin 'timebox_hours' (0, {EMERGENCY_MAX_TIMEBOX_HOURS}] araliginda olmali "
            f"(gozlenen: {timebox_hours})"
        )

    retro_raw = review_record.get("retro_review_due_utc")
    if not retro_raw or not isinstance(retro_raw, str):
        errors.append("APPROVE_EMERGENCY icin 'retro_review_due_utc' zorunlu ve eksik/bos olamaz")
    else:
        try:
            retro_dt = datetime.fromisoformat(retro_raw)
            if retro_dt.tzinfo is None:
                errors.append(f"'retro_review_due_utc' saat dilimi (timezone) bilgisi icermeli: {retro_raw!r}")
            else:
                approved_raw = review_record.get("approved_at_utc")
                if isinstance(approved_raw, str) and approved_raw:
                    try:
                        approved_dt = datetime.fromisoformat(approved_raw)
                        if retro_dt <= approved_dt:
                            errors.append(
                                f"'retro_review_due_utc' ({retro_raw}) 'approved_at_utc' ({approved_raw}) "
                                "ile AYNI veya ONDAN ONCE olamaz -- gelecege donuk bir vade olmali"
                            )
                    except ValueError:
                        pass  # approved_at_utc'nin kendisi bozuksa bu, retro alanini gecersiz kilmaz
        except ValueError:
            errors.append(f"'retro_review_due_utc' gecersiz ISO8601 tarih: {retro_raw!r}")

    return errors


@dataclass
class ApplyEligibility:
    eligible: bool
    reasons: list[str] = field(default_factory=list)


def check_apply_eligibility(
    proposal: dict[str, Any], review_record: dict[str, Any], schema: dict[str, Any]
) -> ApplyEligibility:
    """Uygulamadan (apply) ONCE cagirilmasi ZORUNLU olan tum kontroller
    -- gorev kisitinin ("no auto-apply without explicit approval
    artifact") yapisal karsiligi. Herhangi bir kontrol basarisiz olursa
    `eligible=False` + tum nedenler doner (ilk basarisizlikta DURMAZ --
    inceleyen kisiye TUM sorunlari BIRDEN gosterir)."""
    reasons: list[str] = []

    schema_errors = validate_against_schema(proposal, schema)
    if schema_errors:
        reasons.extend(f"proposal semasi gecersiz: {e}" for e in schema_errors)

    decision = review_record.get("decision")
    if decision not in APPROVED_DECISIONS:
        reasons.append(f"review karari APPROVE degil (APPROVE_EMERGENCY de kabul edilir): {decision!r}")
    elif decision == DECISION_APPROVE_EMERGENCY:
        reasons.extend(validate_emergency_fields(review_record))

    if review_record.get("proposal_id") != proposal.get("proposal_id"):
        reasons.append(
            f"review_record.proposal_id ({review_record.get('proposal_id')!r}) proposal.proposal_id "
            f"({proposal.get('proposal_id')!r}) ile eslesmiyor"
        )

    if "checksum" in proposal:
        recomputed = compute_checksum(proposal)
        linked = review_record.get("linked_proposal_checksum")
        if linked != recomputed:
            reasons.append(
                f"checksum uyusmazligi: review_record.linked_proposal_checksum={linked!r} != "
                f"proposal'in GERCEK (yeniden hesaplanmis) checksum'i={recomputed!r} -- proposal "
                "review'dan SONRA degismis/tahrif edilmis olabilir"
            )

    return ApplyEligibility(eligible=not reasons, reasons=reasons)
