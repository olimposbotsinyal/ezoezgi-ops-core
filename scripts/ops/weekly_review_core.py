"""Haftalik gozlemlenebilirlik gozden gecirme -- saf mantik.

`scripts/ops/weekly_observability_review.ps1` bu modulu kullanir --
GREEN/YELLOW/RED durum siniflandirmasi, gercek gate/drift/metrik
verisinden BAGIMSIZ, deterministik test edilir (bkz.
`observability_gates_core.py`/`observability_drift_core.py` ile AYNI
ayrim ilkesi).

Siniflandirma onceligi (en yuksekten en dusuge, ilk eslesen kazanir):
  RED    -> gate suite FAIL (exit 2) VEYA kritik drift (exit 2) VEYA
            null-intent orani CRIT esigini asiyor
  YELLOW -> gate suite PARTIAL (exit 1) VEYA kritik-olmayan drift
            (exit 1) VEYA null-intent orani WARN esigini asiyor
  GREEN  -> yukaridakilerin hicbiri
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

STATUS_GREEN = "GREEN"
STATUS_YELLOW = "YELLOW"
STATUS_RED = "RED"


@dataclass
class WeeklyReviewResult:
    status: str
    reasons: list[str] = field(default_factory=list)
    fallback_ratio: float | None = None
    null_intent_ratio: float | None = None
    top_alerts: list[str] = field(default_factory=list)


def classify_weekly_status(
    *,
    gate_exit_code: int | None,
    drift_exit_code: int | None,
    fallback_ratio: float | None,
    null_intent_ratio: float | None,
    null_intent_warn: float,
    null_intent_crit: float,
) -> WeeklyReviewResult:
    top_alerts: list[str] = []

    red_reasons = []
    if gate_exit_code == 2:
        red_reasons.append("Go-live gate suite FAIL dondu (exit code 2) -- bkz. gate_report.md.")
    if drift_exit_code == 2:
        red_reasons.append("KRITIK gozlemlenebilirlik drift'i tespit edildi (exit code 2) -- bkz. drift_report.md.")
    if null_intent_ratio is not None and null_intent_ratio > null_intent_crit:
        red_reasons.append(
            f"null-intent orani CRIT esigini asiyor ({null_intent_ratio:.4f} > {null_intent_crit})."
        )
        top_alerts.append("HIGH_NULL_INTENT_RATE (CRIT)")
    if red_reasons:
        return WeeklyReviewResult(STATUS_RED, red_reasons, fallback_ratio, null_intent_ratio, top_alerts)

    yellow_reasons = []
    if gate_exit_code == 1:
        yellow_reasons.append("Go-live gate suite PARTIAL dondu (bazi gate'ler SKIPPED/PARTIAL).")
    if drift_exit_code == 1:
        yellow_reasons.append("Kritik olmayan gozlemlenebilirlik drift'i tespit edildi.")
    if null_intent_ratio is not None and null_intent_ratio > null_intent_warn:
        yellow_reasons.append(
            f"null-intent orani WARN esigini asiyor ({null_intent_ratio:.4f} > {null_intent_warn})."
        )
        top_alerts.append("HIGH_NULL_INTENT_RATE (WARN)")
    if yellow_reasons:
        return WeeklyReviewResult(STATUS_YELLOW, yellow_reasons, fallback_ratio, null_intent_ratio, top_alerts)

    return WeeklyReviewResult(
        STATUS_GREEN, ["Tum kontroller saglikli."], fallback_ratio, null_intent_ratio, top_alerts
    )


def render_review_entry_md(result: WeeklyReviewResult, *, generated_at: str, iso_week: str) -> str:
    def _fmt(x: float | None) -> str:
        return f"{x:.4f}" if x is not None else "n/a (yetersiz/hic veri yok)"

    lines = [
        f"## {iso_week} -- {generated_at}",
        "",
        f"**Durum: {result.status}**",
        "",
    ]
    for r in result.reasons:
        lines.append(f"- {r}")
    lines += [
        "",
        f"- Fallback orani (son 7 gun): {_fmt(result.fallback_ratio)}",
        f"- Null-intent orani (son 7 gun): {_fmt(result.null_intent_ratio)}",
        f"- Ust alertler: {', '.join(result.top_alerts) if result.top_alerts else 'yok'}",
        "",
        "---",
        "",
    ]
    return "\n".join(lines)


def append_review_entry(path: Path, entry_md: str, *, iso_week: str) -> None:
    """`review.md`'ye yeni bir girdi EKLER (var olan girdileri SILMEZ/
    UZERINE YAZMAZ) -- dosya yoksa bir baslikla birlikte olusturulur."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        header = f"# Haftalik Gozlemlenebilirlik Gozden Gecirme -- {iso_week}\n\n"
        path.write_text(header + entry_md, encoding="utf-8")
    else:
        with path.open("a", encoding="utf-8") as f:
            f.write(entry_md)


def status_to_exit_code(status: str) -> int:
    return {STATUS_GREEN: 0, STATUS_YELLOW: 1, STATUS_RED: 2}[status]
