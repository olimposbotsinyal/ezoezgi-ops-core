"""Go-live gate degerlendirme cekirdegi -- saf mantik, testable.

`scripts/ops/run_observability_gates.ps1`, gercek kontrolleri (HTTP
istekleri, sentetik sinyal enjeksiyonu, pytest calistirma, Alertmanager
erisilebilirligi) kendisi yapar (bkz. `verify_alert_pipeline.ps1` ile
ayni ayrim ilkesi), sonra HAM sonuclari (basari sayisi, mode->gorunurluk
haritasi, pytest exit code vb.) bu modulun `evaluate_gate_*`
fonksiyonlarina gecirir -- boylece "hangi ham girdi hangi GateResult'a
karsilik gelir" mantigi, gercek bir sunucu/Alertmanager calistirmadan,
sabit girdilerle deterministik test edilebilir.

Her gate bir `GateResult` uretir: PASS / PARTIAL / FAIL / SKIPPED.
Genel exit code haritasi (`overall_exit_code`) -- gorev tanimindaki
"0 pass, 1 partial, 2 fail" sozlesmesi:
  - Herhangi bir gate FAIL ise -> 2
  - FAIL yok ama en az bir PARTIAL/SKIPPED varsa -> 1
  - Hepsi PASS ise -> 0
SKIPPED, PARTIAL ile AYNI exit-code kovasina dusuyor (yasal/mecburi bir
neden hicbir kontrolun yapilamadigi anlamina gelir -- bu, "her sey
yolunda" (0) ile ayni SAYILAMAZ, ama tek basina "acikca basarisiz" (2)
de degildir).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

STATUS_PASS = "PASS"
STATUS_PARTIAL = "PARTIAL"
STATUS_FAIL = "FAIL"
STATUS_SKIPPED = "SKIPPED"

VALID_STATUSES = (STATUS_PASS, STATUS_PARTIAL, STATUS_FAIL, STATUS_SKIPPED)

EXIT_PASS = 0
EXIT_PARTIAL = 1
EXIT_FAIL = 2


@dataclass
class GateResult:
    name: str
    status: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(f"gecersiz gate durumu: {self.status!r} (gecerli: {VALID_STATUSES})")


def overall_exit_code(results: list[GateResult]) -> int:
    if any(r.status == STATUS_FAIL for r in results):
        return EXIT_FAIL
    if any(r.status in (STATUS_PARTIAL, STATUS_SKIPPED) for r in results):
        return EXIT_PARTIAL
    return EXIT_PASS


def overall_status(results: list[GateResult]) -> str:
    return {EXIT_PASS: STATUS_PASS, EXIT_PARTIAL: STATUS_PARTIAL, EXIT_FAIL: STATUS_FAIL}[
        overall_exit_code(results)
    ]


# ---------------------------------------------------------------------------
# Gate A: /metrics kullanilabilirligi (24s pencere, non-prod'da simulasyon)
# ---------------------------------------------------------------------------


def evaluate_gate_a_metrics_availability(
    successes: int, samples: int, *, simulated: bool, window_label: str
) -> GateResult:
    availability = (successes / samples) if samples else 0.0
    if availability >= 1.0:
        status = STATUS_PASS
    elif availability >= 0.99:
        status = STATUS_PARTIAL
    else:
        status = STATUS_FAIL

    mode_note = (
        "SIMULASYON modu (kisa ornek pencere, GERCEK 24s gozlem DEGIL)"
        if simulated
        else "GERCEK gozlem penceresi"
    )
    detail = (
        f"{mode_note} [{window_label}] -- {successes}/{samples} basarili "
        f"(kullanilabilirlik={availability:.2%})"
    )
    return GateResult(
        "A_metrics_availability",
        status,
        detail,
        evidence={
            "successes": successes,
            "samples": samples,
            "availability": availability,
            "simulated": simulated,
            "window_label": window_label,
        },
    )


# ---------------------------------------------------------------------------
# Gate B: scrape basari orani >= %99
# ---------------------------------------------------------------------------


def evaluate_gate_b_scrape_success(successes: int, samples: int, *, simulated: bool) -> GateResult:
    rate = (successes / samples) if samples else 0.0
    if rate >= 0.99:
        status = STATUS_PASS
    elif rate >= 0.95:
        status = STATUS_PARTIAL
    else:
        status = STATUS_FAIL

    mode_note = "SIMULASYON (yerel tekrar istek)" if simulated else "GERCEK Prometheus scrape kaydi"
    detail = f"{mode_note} -- scrape basari orani={rate:.2%} ({successes}/{samples}), esik >=%99"
    return GateResult(
        "B_scrape_success_rate",
        status,
        detail,
        evidence={"successes": successes, "samples": samples, "rate": rate, "simulated": simulated},
    )


# ---------------------------------------------------------------------------
# Gate C: 4 sentetik alert modu /metrics'te gorunur mu
# ---------------------------------------------------------------------------


def evaluate_gate_c_synthetic_alerts(mode_visibility: dict[str, bool]) -> GateResult:
    total = len(mode_visibility)
    visible = [m for m, v in mode_visibility.items() if v]
    missing = [m for m, v in mode_visibility.items() if not v]

    if total > 0 and len(visible) == total:
        status = STATUS_PASS
    elif visible:
        status = STATUS_PARTIAL
    else:
        status = STATUS_FAIL

    detail = f"{len(visible)}/{total} sentetik mod /metrics'te gorunur oldu"
    if missing:
        detail += f" -- eksik: {missing}"
    return GateResult(
        "C_synthetic_alerts_visible",
        status,
        detail,
        evidence={"mode_visibility": mode_visibility, "visible": visible, "missing": missing},
    )


# ---------------------------------------------------------------------------
# Gate D: Alertmanager alma yolu -- gercek Alertmanager v3 v2 API'siyle
# (2026-08-13'te prometheus v3.13.2 + alertmanager v0.33.1 ile elle
# dogrulandi, bkz. reports/gate_d_real_validation_*/ ve
# docs/ops/MONITORING_STACK_RUNBOOK.md "Gate D gercek dogrulama") elle
# dogrulanmis, gercek bir kurulumla calisir.
# ---------------------------------------------------------------------------


def parse_alertmanager_v2_payload(payload: Any) -> list[str]:
    """Alertmanager `GET /api/v2/alerts` yanitindan (bir liste) her
    alert'in `labels.alertname` degerini cikarir. Beklenmeyen sekil
    (liste degil, `labels`/`alertname` eksik vb.) icin o girdi
    SESSIZCE atlanir -- tum fonksiyon ASLA exception firlatmaz
    (`run_observability_gates.ps1`'in PowerShell'deki esdegeri ile
    ayni, kanit amaciyla Python tarafinda ayrica test edilir)."""
    if not isinstance(payload, list):
        return []
    names: list[str] = []
    for entry in payload:
        try:
            names.append(entry["labels"]["alertname"])
        except (KeyError, TypeError):
            continue
    return names


def evaluate_gate_d_alertmanager_receive(
    *, alertmanager_installed: bool, alertmanager_reachable: bool, alert_received: bool
) -> GateResult:
    evidence = {
        "alertmanager_installed": alertmanager_installed,
        "alertmanager_reachable": alertmanager_reachable,
        "alert_received": alert_received,
    }
    if not alertmanager_installed:
        return GateResult(
            "D_alertmanager_receive_path",
            STATUS_SKIPPED,
            "Alertmanager bu makinede kurulu degil -- gate calistirilamadi "
            "(bkz. docs/ops/MONITORING_STACK_RUNBOOK.md 'Kurulum')",
            evidence=evidence,
        )
    if not alertmanager_reachable:
        return GateResult(
            "D_alertmanager_receive_path",
            STATUS_FAIL,
            "Alertmanager kurulu ama calisan surece erisilemedi",
            evidence=evidence,
        )
    if not alert_received:
        return GateResult(
            "D_alertmanager_receive_path",
            STATUS_FAIL,
            "Alertmanager erisilebilir ama sentetik alert alinmadi -- routing sorunlu olabilir",
            evidence=evidence,
        )
    return GateResult(
        "D_alertmanager_receive_path",
        STATUS_PASS,
        "Alertmanager alma yolu uctan uca dogrulandi",
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# Gate E: classify regresyon smoke (mevcut test suite'inin ilgili alt kumesi)
# ---------------------------------------------------------------------------


def evaluate_gate_e_classify_regression(pytest_exit_code: int, tests_summary: str) -> GateResult:
    status = STATUS_PASS if pytest_exit_code == 0 else STATUS_FAIL
    detail = f"classify-ilgili regresyon testleri exit_code={pytest_exit_code} -- {tests_summary}"
    return GateResult(
        "E_classify_regression_smoke",
        status,
        detail,
        evidence={"pytest_exit_code": pytest_exit_code, "tests_summary": tests_summary},
    )


# ---------------------------------------------------------------------------
# Rapor uretimi
# ---------------------------------------------------------------------------


def render_gate_report_md(results: list[GateResult], *, generated_at: str, mode: str) -> str:
    lines = [
        "# Go-Live Gate Raporu -- Model Gateway Observability",
        "",
        f"Uretildi (UTC): {generated_at}",
        f"Calisma modu: {mode}",
        f"Genel sonuc: **{overall_status(results)}** (exit code {overall_exit_code(results)})",
        "",
        "| Gate | Durum | Aciklama |",
        "|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r.name} | {r.status} | {r.detail} |")

    lines += ["", "## Gate kanitlari (ham veri)", ""]
    for r in results:
        if r.evidence:
            lines.append(f"### {r.name}")
            lines.append("```json")
            lines.append(json.dumps(r.evidence, indent=2, ensure_ascii=False))
            lines.append("```")
            lines.append("")

    return "\n".join(lines) + "\n"


def write_gate_results_json(results: list[GateResult], path: Path, *, generated_at: str, mode: str) -> None:
    payload = {
        "generated_at": generated_at,
        "mode": mode,
        "overall_status": overall_status(results),
        "overall_exit_code": overall_exit_code(results),
        "gates": [asdict(r) for r in results],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def parse_gate_results_json(path: Path) -> dict[str, Any]:
    """Var olan bir `gate_results.json` dosyasini okuyup temel semayi
    dogrular -- `build_observability_signoff.py`'nin tukettigi ayni
    format. Eksik alan/bozuk JSON icin `ValueError` firlatir (sert hata
    -- signoff dossier'in sessizce yanlis veriyle devam etmesindense
    acikca basarisiz olmasi tercih edildi)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"gate_results.json okunamadi/ayristirilamadi: {exc}") from exc

    required = {"generated_at", "mode", "overall_status", "overall_exit_code", "gates"}
    missing = required - data.keys()
    if missing:
        raise ValueError(f"gate_results.json eksik alanlar: {sorted(missing)}")
    return data
