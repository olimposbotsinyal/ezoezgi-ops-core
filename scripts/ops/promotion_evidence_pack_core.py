"""`run_promotion_evidence_pack.ps1`'in SON adimi (bundling) icin SAF
mantik -- auto-rollback OFF/ON kanit sayimi, terfi gorunumu (outlook)
haritalamasi, `runpack_index.json`/`runpack_summary.md` uretimi.

Adim adim python CLI'larini SIRAYLA cagirmak (orkestrasyon) `.ps1`'in
KENDI isidir -- bu modul yalnizca "cagrilan adimlarin SONUCLARINI
TOPLAMA/BIRLESTIRME" mantigidir, boylece pytest ile GERCEK bir
PowerShell sureci baslatmadan deterministik test edilebilir (ayni
`pilot_promotion_core.py`/`pilot_fpr_core.py` ile paylasilan saf-mantik/
G-C-sarmalayici ayrim ilkesi)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUTLOOK_BY_EXIT_CODE = {0: "PROMOTE-ready", 1: "EXTEND-likely", 2: "REJECT-risk"}
OUTLOOK_UNKNOWN = "UNKNOWN"


def promotion_readiness_outlook(evaluator_exit_code: int | None) -> str:
    """`evaluate_pilot_promotion.py` cikis kodunu (0/1/2) insan-okunur
    bir gorunume cevirir. Beklenmeyen bir kod (VEYA `None` -- evaluator
    adimi hic calismadiysa) `UNKNOWN` doner -- ASLA varsayilan olarak
    iyimser bir gorunum FABRIKE EDILMEZ."""
    if evaluator_exit_code is None:
        return OUTLOOK_UNKNOWN
    return OUTLOOK_BY_EXIT_CODE.get(evaluator_exit_code, OUTLOOK_UNKNOWN)


def count_auto_rollback_evidence(apply_report_payloads: list[dict[str, Any]]) -> tuple[int, int]:
    """Doner: (ON sayisi, OFF sayisi). `auto_rollback.triggered == True`
    olanlar ON, digerleri (alan yok VEYA False) OFF sayilir.

    **Bu bir GOZLEMDIR** -- gorev metni "execute verify-fail scenarios
    (auto-rollback OFF/ON)" istese de, GERCEK bir VerifyReload FAIL/
    auto-rollback senaryosunu TETIKLEMEK potansiyel olarak durum-
    degistiren (alertmanager config'i mutasyona ugratabilen) bir islemdir
    -- 'Default runtime behavior remains conservative' gorev kisitiyla
    CELISIR. Bunun yerine, ONCEDEN var olan (`reports/v1_2_pilot_*/`,
    `reports/threshold_apply_*/`) kanit dosyalari SAYILIR -- gercek bir
    senaryo ELLE calistirilmalidir (bkz. docs/ops/MONITORING_STACK_RUNBOOK.md
    'How to run v1.2 trials safely')."""
    on = 0
    off = 0
    for payload in apply_report_payloads:
        triggered = isinstance(payload.get("auto_rollback"), dict) and payload["auto_rollback"].get("triggered") is True
        if triggered:
            on += 1
        else:
            off += 1
    return on, off


def collect_auto_rollback_evidence(repo_root: Path) -> tuple[int, int, int]:
    """`count_auto_rollback_evidence`'in dosya-sistemi sarmalayicisi --
    GERCEK `apply_report.json` dosyalarini bulur/okur, sayima besler.
    Doner: (ON, OFF, taranan_dosya_sayisi). Okunamayan/bozuk dosyalar
    SESSIZCE atlanir (sayima katilmazlar, ama diger dosyalari
    ENGELLEMEZLER)."""
    patterns = ["reports/v1_2_pilot_*/**/apply_report.json", "reports/threshold_apply_*/apply_report.json"]
    files: list[Path] = []
    for pattern in patterns:
        for f in sorted(repo_root.glob(pattern)):
            if f not in files:
                files.append(f)
    payloads: list[dict[str, Any]] = []
    for f in files:
        try:
            payloads.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    on, off = count_auto_rollback_evidence(payloads)
    return on, off, len(files)


def build_runpack_index(
    steps: list[dict[str, Any]], *, generated_at: str, incident_id: str | None, evaluator_exit_code: int | None
) -> dict[str, Any]:
    """SAF -- `steps` (her biri en az `{"step","exit_code","status","evidence_path"}`
    iceren dict'ler) + oncenden hesaplanmis degerlerden nihai
    `runpack_index.json` payload'ini insa eder. Dosya G/C YAPMAZ."""
    return {
        "generated_at_utc": generated_at,
        "incident_id": incident_id,
        "steps": steps,
        "promotion_readiness_outlook": promotion_readiness_outlook(evaluator_exit_code),
    }


def render_runpack_summary_md(index_payload: dict[str, Any]) -> str:
    lines = [
        "# Promotion Evidence Runpack Ozeti",
        "",
        f"Uretildi (UTC): {index_payload['generated_at_utc']}",
        f"incident_id: {index_payload['incident_id']}",
        f"Terfi gorunumu (outlook): **{index_payload['promotion_readiness_outlook']}**",
        "",
        "| Adim | Durum | Exit code | Kanit yolu |",
        "|---|---|---|---|",
    ]
    for s in index_payload["steps"]:
        lines.append(f"| {s['step']} | {s['status']} | {s.get('exit_code')} | {s.get('evidence_path') or '-'} |")
    lines.append("")
    lines.append(
        "NOT: `auto_rollback_evidence_scan` adimi GOZLEMSELDIR -- repo icindeki MEVCUT "
        "`apply_report.json` kanitlarini tarar, GERCEK bir VerifyReload FAIL/auto-rollback "
        "senaryosunu KENDISI TETIKLEMEZ (bu, gercek Alertmanager/promtool durumu + onayli "
        "bir proposal/review gerektiren, bilerek AYRI/elle bir islemdir -- bkz. "
        "docs/ops/MONITORING_STACK_RUNBOOK.md 'How to run v1.2 trials safely')."
    )
    lines.append("")
    lines.append(
        "**BU PAKET HICBIR KALICI DURUMU DEGISTIRMEZ** -- `pilot_flags_state.json`'a "
        "yazmaz, hicbir `-Apply`/durum-degistiren islem calistirmaz (gorev kisiti: "
        "'Default runtime behavior remains conservative')."
    )
    return "\n".join(lines) + "\n"


def write_runpack_bundle(index_payload: dict[str, Any], out_dir: Path) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path = out_dir / "runpack_index.json"
    index_path.write_text(json.dumps(index_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    summary_md = render_runpack_summary_md(index_payload)
    summary_path = out_dir / "runpack_summary.md"
    summary_path.write_text(summary_md, encoding="utf-8")
    return {"index": index_path, "summary": summary_path}
