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
VALID_DECISIONS = (DECISION_APPROVE, DECISION_REJECT, DECISION_NEEDS_DATA)

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
) -> dict[str, Any]:
    if decision not in VALID_DECISIONS:
        raise ValueError(f"gecersiz karar: {decision!r} (gecerli: {VALID_DECISIONS})")

    return {
        "proposal_id": proposal["proposal_id"],
        "reviewer": reviewer,
        "decision": decision,
        "rationale": rationale,
        "approved_at_utc": approved_at_utc or datetime.now(timezone.utc).isoformat(),
        "linked_proposal_checksum": proposal["checksum"],
    }


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
    if decision != DECISION_APPROVE:
        reasons.append(f"review karari APPROVE degil: {decision!r}")

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
