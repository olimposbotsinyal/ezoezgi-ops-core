"""Acil durum (APPROVE_EMERGENCY) taleplerinin MESRUIYET on-kontrolu --
saf mantik (dosya/ag G/C yok, PILOT).

**v1.2 PILOT: bu modul HENUZ hicbir apply akisini ENGELLEMEZ** (gorev
kisiti: "does NOT hard-block APPROVE_EMERGENCY yet") -- yalnizca bir
`legitimacy_status` (PASS/FAIL/SKIPPED) + nedenler listesi uretir;
`check_emergency_legitimacy.py` bunu bir rapora yazar. `create_threshold_review_record.py`
bu raporun YOLUNU (opsiyonel `--legitimacy-report-path`) review_record.json'a
BILGI AMACLI ekleyebilir, ama `check_apply_eligibility` bunu OKUMAZ/
DOGRULAMAZ (v1.3+ "promotion" konusu, bkz. docs "Promotion criteria
from pilot to enforced").

Iki bagimsiz kontrol:
  1. **Ticket format dogrulama** -- yapilandirilabilir (varsayilan
     `OPS-\\d+`) bir regex'e uyan bir incident_id var mi?
  2. **Saglayici (provider) dogrulamasi** -- PILOT'ta GERCEK bir
     servise BAGLANMAZ, HICBIR sir/credential gerekmez: `none` (kontrol
     hic calistirilmaz), `mock`/`jira_stub` (deterministik, sabit
     "bulundu" sonucu -- yalnizca GELECEKTEKI gercek entegrasyonun
     arayuzunu/seklini gosteren bir yer tutucu)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_SKIPPED = "SKIPPED"
VALID_STATUSES = (STATUS_PASS, STATUS_FAIL, STATUS_SKIPPED)

DEFAULT_TICKET_REGEX = r"^OPS-\d+$"

PROVIDER_NONE = "none"
PROVIDER_MOCK = "mock"
PROVIDER_JIRA_STUB = "jira_stub"
PROVIDER_JIRA = "jira"
VALID_PROVIDERS = (PROVIDER_NONE, PROVIDER_MOCK, PROVIDER_JIRA_STUB, PROVIDER_JIRA)


def validate_ticket_format(ticket_id: str | None, *, pattern: str = DEFAULT_TICKET_REGEX) -> tuple[bool, str]:
    """Doner: (gecerli_mi, neden). `ticket_id` bos/None ise GECERSIZDIR
    (format kontrolu kendi icinde ATLANMAZ -- "hic ticket verilmemis"
    durumu, `evaluate_legitimacy`'de AYRI bir SKIPPED sonucuna
    donusturulur, burada DEGIL)."""
    if not ticket_id or not isinstance(ticket_id, str) or not ticket_id.strip():
        return False, "ticket_id (incident_id) bos/eksik"
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        return False, f"GOV_TICKET_REGEX gecersiz bir duzenli ifade: {exc}"
    if not compiled.match(ticket_id.strip()):
        return False, f"'{ticket_id}' desene uymuyor (beklenen: {pattern})"
    return True, "ticket format gecerli"


@dataclass
class ProviderCheckResult:
    checked: bool
    found: bool
    detail: str


def run_provider_check(ticket_id: str | None, *, provider: str) -> ProviderCheckResult:
    """PILOT STUB: `mock`/`jira_stub` GERCEK bir aga/servise BAGLANMAZ --
    deterministik, sabit bir 'bulundu' sonucu doner (yalnizca
    gelecekteki gercek entegrasyonun ARAYUZUNU/SEKLINI gostermek
    icindir, gorev kisiti: "no real secrets required in pilot").
    `none` ise kontrolun kendisi hic CALISTIRILMAZ (`checked=False`).

    **`provider='jira'` (GERCEK mod) BU FONKSIYONLA ASLA DESTEKLENMEZ**
    -- gercek bir ag cagrisi gerektirir, bu fonksiyon SAF/deterministik
    kalmalidir. GERCEK kontrol icin `legitimacy_provider_client.check_ticket_via_jira()`
    (CLI seviyesinde, `check_emergency_legitimacy.py` icinde) kullanilir
    ve sonuc `evaluate_legitimacy()`'ye `precomputed_provider_result`
    olarak GECIRILIR -- boylece bu STUB fonksiyon YANLISLIKLA gercek bir
    Jira kontrolunu FABRIKE EDEMEZ."""
    if provider == PROVIDER_NONE:
        return ProviderCheckResult(checked=False, found=False, detail="provider=none -- saglayici kontrolu atlandi")
    if provider == PROVIDER_JIRA:
        return ProviderCheckResult(
            checked=False, found=False,
            detail="provider='jira' bu STUB fonksiyonla desteklenmez -- GERCEK kontrol icin "
            "legitimacy_provider_client.check_ticket_via_jira() + precomputed_provider_result kullanilmalidir",
        )
    if provider not in VALID_PROVIDERS:
        return ProviderCheckResult(checked=False, found=False, detail=f"bilinmeyen provider: {provider!r}")
    if not ticket_id:
        return ProviderCheckResult(checked=True, found=False, detail="ticket_id verilmedi -- saglayicida aranamadi")
    return ProviderCheckResult(
        checked=True,
        found=True,
        detail=f"[PILOT STUB -- gercek baglanti/sir YOK] provider={provider}, ticket={ticket_id} 'acik' varsayildi",
    )


@dataclass
class LegitimacyResult:
    status: str
    reasons: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(f"gecersiz legitimacy_status: {self.status!r} (gecerli: {VALID_STATUSES})")


def evaluate_legitimacy(
    *,
    incident_id: str | None,
    provider: str = PROVIDER_NONE,
    ticket_regex: str = DEFAULT_TICKET_REGEX,
    precomputed_provider_result: ProviderCheckResult | None = None,
) -> LegitimacyResult:
    """PILOT (non-blocking) mesruiyet degerlendirmesi.

    - `incident_id` HIC verilmemisse (`None`): tum kontrol ATLANIR
      (`SKIPPED`) -- bu bir on-kontroldur, henuz ZORUNLU degildir.
    - `incident_id` verilmisse: ONCE format kontrolu calisir (basarisizsa
      HEMEN `FAIL`, provider kontrolune GEREK KALMAZ), SONRA (format
      gectiyse) provider kontrolu calisir -- ikisi de gecerse `PASS`.

    `precomputed_provider_result` VERILMISSE (CLI, `legitimacy_provider_client.check_ticket_via_jira()`
    ile GERCEK bir ag cagrisi yaptiktan SONRA), bu fonksiyon KENDI
    `run_provider_check()` STUB'ini CAGIRMAZ -- verilen sonucu DOGRUDAN
    kullanir. `provider='jira'` VERILMIS AMA `precomputed_provider_result`
    VERILMEMISSE, bu bir PROGRAMLAMA HATASIDIR (`ValueError`) -- bu
    fonksiyon KENDISI ASLA gercek bir Jira kontrolunu fabrike ETMEZ."""
    if incident_id is None:
        return LegitimacyResult(status=STATUS_SKIPPED, reasons=["incident_id saglanmadi -- pilot on-kontrolu atlandi"])

    reasons: list[str] = []
    format_ok, format_reason = validate_ticket_format(incident_id, pattern=ticket_regex)
    reasons.append(format_reason)
    if not format_ok:
        return LegitimacyResult(status=STATUS_FAIL, reasons=reasons)

    if precomputed_provider_result is not None:
        provider_result = precomputed_provider_result
    elif provider == PROVIDER_JIRA:
        raise ValueError(
            "provider='jira' icin GERCEK bir kontrol ONCEDEN yapilip 'precomputed_provider_result' "
            "olarak GECIRILMELIDIR -- bu fonksiyon KENDISI asla gercek bir ag cagrisi yapmaz "
            "(bkz. check_emergency_legitimacy.py)"
        )
    else:
        provider_result = run_provider_check(incident_id, provider=provider)

    reasons.append(provider_result.detail)
    if provider_result.checked and not provider_result.found:
        return LegitimacyResult(status=STATUS_FAIL, reasons=reasons)

    return LegitimacyResult(status=STATUS_PASS, reasons=reasons)


# ---------------------------------------------------------------------------
# Rapor uretimi
# ---------------------------------------------------------------------------


def render_legitimacy_report_md(
    result: LegitimacyResult, *, generated_at: str, incident_id: str | None, provider: str,
    provider_evidence: dict[str, Any] | None = None,
) -> str:
    lines = [
        "# Acil Durum Mesruiyet On-Kontrolu (PILOT, non-blocking)",
        "",
        f"Uretildi (UTC): {generated_at}",
        f"incident_id: `{incident_id}`",
        f"provider: `{provider}`",
        f"Sonuc: **{result.status}**",
        "",
        "## Nedenler",
        "",
    ]
    for r in result.reasons:
        lines.append(f"- {r}")
    if provider_evidence is not None:
        lines += [
            "",
            "## Saglayici kaniti (provider_evidence -- GERCEK kontrol)",
            "",
            f"- checked: {provider_evidence.get('checked')}",
            f"- found: {provider_evidence.get('found')}",
            f"- status_code: {provider_evidence.get('status_code')}",
            f"- attempts: {provider_evidence.get('attempts')}",
            f"- detail: {provider_evidence.get('detail')} (kimlik bilgisi degerleri ASLA burada gorunmez)",
        ]
    lines += [
        "",
        "**NOT (PILOT):** Bu kontrol HENUZ hicbir apply akisini ENGELLEMEZ "
        "-- yalnizca bilgilendiricidir. `GOV_EMERGENCY_LEGITIMACY_REQUIRED=1` "
        "olsa bile v1.2'de zorlayici DEGILDIR (bkz. docs/ops/MONITORING_STACK_RUNBOOK.md "
        "\"Promotion criteria from pilot to enforced\").",
        "",
    ]
    return "\n".join(lines) + "\n"


def write_legitimacy_report(
    result: LegitimacyResult, out_dir: Path, *, generated_at: str, incident_id: str | None, provider: str,
    provider_evidence: dict[str, Any] | None = None,
) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md = render_legitimacy_report_md(
        result, generated_at=generated_at, incident_id=incident_id, provider=provider, provider_evidence=provider_evidence
    )
    md_path = out_dir / "legitimacy_report.md"
    md_path.write_text(md, encoding="utf-8")

    json_path = out_dir / "legitimacy_report.json"
    payload = {
        "generated_at": generated_at,
        "incident_id": incident_id,
        "provider": provider,
        "legitimacy_status": result.status,
        "reasons": result.reasons,
        "provider_evidence": provider_evidence,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"md": md_path, "json": json_path}
