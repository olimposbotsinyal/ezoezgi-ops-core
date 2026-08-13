"""Alert esik kalibrasyon paketi -- son N saatlik JSONL metrik olaylarindan
ONERILEN WARN/CRIT esiklerini + gerekce + yanlis-pozitif (false-positive)
riski hesaplar.

**DURUSTLUK NOTU (kritik, okumadan kullanma):** Bu makinede GERCEK
uretim trafigi YOK. `data/metrics/*.jsonl`'deki olaylarin tamami bu
proje boyunca yapilan ELLE/SENTETIK testlerden geliyor --
`synthetic="true"` etiketli olanlar hesaplamadan KESINLIKLE haric
tutulur (aksi halde kalibrasyon kendi test verisiyle kendini kandirir).
Bu yuzden:
  - Pencerede yeterli ORNEK YOKSA (bkz. `MIN_SAMPLE_SIZE`), script
    MEVCUT `config.py` varsayilanini "kalibre edilmis" diye ONERMEZ --
    acikca `INSUFFICIENT_DATA` olarak isaretler ve mevcut varsayilani
    aynen korur (esigi rastgele/manasiz bir sayiya CEKMEZ).
  - Yeterli ornek olsa BILE, gercek go-live ONCESI kucuk bir
    orneklemdir -- "yanlis-pozitif riski" bolumu bunu HER ZAMAN acikca
    belirtir.
Gercek go-live sonrasi, ilk gercek uretim penceresi (`--hours`) biriktikce
bu script YENIDEN calistirilip esikler guncellenmelidir -- tek seferlik
bir kurulum adimi DEGILDIR.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MIN_SAMPLE_SIZE = 30  # bu esigin altinda "kalibre edilmis" oneri YAPILMAZ
WARN_MULTIPLIER = 2.0
CRIT_MULTIPLIER = 4.0

INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
CALIBRATED = "CALIBRATED"


@dataclass
class CalibrationSuggestion:
    alert_name: str
    metric_basis: str
    sample_size: int
    observed_rate: float | None
    current_warn: float
    current_crit: float
    suggested_warn: float
    suggested_crit: float
    status: str  # CALIBRATED | INSUFFICIENT_DATA
    rationale: str
    false_positive_risk: str


def _is_synthetic(event: Any) -> bool:
    return str(event.labels.get("synthetic", "")).lower() == "true"


def _sum_counter(events: list[Any], name: str, *, label_filter: dict[str, str] | None = None) -> float:
    total = 0.0
    for e in events:
        if e.metric_type != "counter" or e.name != name or _is_synthetic(e):
            continue
        if label_filter and any(e.labels.get(k) != v for k, v in label_filter.items()):
            continue
        total += e.value
    return total


def calibrate_ratio_alert(
    *,
    alert_name: str,
    metric_basis: str,
    numerator: float,
    denominator: float,
    current_warn: float,
    current_crit: float,
) -> CalibrationSuggestion:
    sample_size = int(denominator)
    if sample_size < MIN_SAMPLE_SIZE:
        return CalibrationSuggestion(
            alert_name=alert_name,
            metric_basis=metric_basis,
            sample_size=sample_size,
            observed_rate=None,
            current_warn=current_warn,
            current_crit=current_crit,
            suggested_warn=current_warn,
            suggested_crit=current_crit,
            status=INSUFFICIENT_DATA,
            rationale=(
                f"Pencerede yalnizca {sample_size} ornek var (esik: {MIN_SAMPLE_SIZE}) -- "
                "istatistiksel olarak anlamli bir kalibrasyon icin yetersiz. Mevcut "
                "config.py varsayilani AYNEN korunuyor."
            ),
            false_positive_risk=(
                "Degerlendirilemedi (veri yetersiz) -- gercek trafik biriktikce yeniden calistirin."
            ),
        )

    observed_rate = numerator / denominator if denominator else 0.0
    suggested_warn = max(observed_rate * WARN_MULTIPLIER, current_warn)
    suggested_crit = max(observed_rate * CRIT_MULTIPLIER, current_crit)

    return CalibrationSuggestion(
        alert_name=alert_name,
        metric_basis=metric_basis,
        sample_size=sample_size,
        observed_rate=observed_rate,
        current_warn=current_warn,
        current_crit=current_crit,
        suggested_warn=suggested_warn,
        suggested_crit=suggested_crit,
        status=CALIBRATED,
        rationale=(
            f"Gozlenen oran={observed_rate:.4f} ({numerator:.0f}/{denominator:.0f}, "
            f"{sample_size} ornek). Oneri: WARN=gozlenen*{WARN_MULTIPLIER}x, "
            f"CRIT=gozlenen*{CRIT_MULTIPLIER}x -- ikisi de mevcut varsayilanin ALTINA "
            "DUSURULMEZ (guvenlik marji, kucuk orneklemde asiri-hassas esik riskine karsi)."
        ),
        false_positive_risk=(
            f"KUCUK ORNEKLEM ({sample_size} ornek, tek bir gozlem penceresi) -- bu bir "
            "GERCEK uretim dagilim istatistigi DEGILDIR, yalnizca bu penceredeki tek bir "
            "gozlemdir. Onerilen esikler go-live sonrasi ilk gercek trafik birikince "
            "YENIDEN degerlendirilmelidir; simdiden AYNEN uygulanmasi (ozellikle "
            "multiplier'in kucuk oldugu durumlarda) yanlis-pozitif riskini artirabilir."
        ),
    )


def calibrate_all(
    events: list[Any],
    *,
    current_null_intent_warn: float,
    current_null_intent_crit: float,
    current_fallback_spike_multiplier: float,
) -> list[CalibrationSuggestion]:
    requests_total = _sum_counter(events, "model_gateway_requests_total")
    null_intent_total = _sum_counter(events, "model_gateway_null_intent_total")
    fallback_total = _sum_counter(events, "model_gateway_fallback_total")
    restrictions_total = _sum_counter(events, "model_gateway_restrictions_total")
    preflight_total = _sum_counter(events, "model_gateway_preflight_total")
    preflight_unknown = _sum_counter(
        events, "model_gateway_preflight_total", label_filter={"status": "UNKNOWN"}
    )
    restricted_persistent = _sum_counter(
        events,
        "model_gateway_restrictions_total",
        label_filter={"reason_code": "PRIMARY_RESTRICTED_CPU_UNVERIFIED"},
    )

    return [
        calibrate_ratio_alert(
            alert_name="HIGH_NULL_INTENT_RATE",
            metric_basis="model_gateway_null_intent_total / model_gateway_requests_total",
            numerator=null_intent_total,
            denominator=requests_total,
            current_warn=current_null_intent_warn,
            current_crit=current_null_intent_crit,
        ),
        calibrate_ratio_alert(
            alert_name="FALLBACK_SPIKE",
            metric_basis="model_gateway_fallback_total / model_gateway_requests_total",
            numerator=fallback_total,
            denominator=requests_total,
            current_warn=current_fallback_spike_multiplier,
            current_crit=current_fallback_spike_multiplier,
        ),
        calibrate_ratio_alert(
            alert_name="PRIMARY_RESTRICTED_PERSISTENT",
            metric_basis=(
                "model_gateway_restrictions_total{reason_code=PRIMARY_RESTRICTED_CPU_UNVERIFIED} "
                "/ model_gateway_restrictions_total"
            ),
            numerator=restricted_persistent,
            denominator=restrictions_total,
            current_warn=1.0,
            current_crit=1.0,
        ),
        calibrate_ratio_alert(
            alert_name="PREFLIGHT_UNKNOWN_PERSISTENT",
            metric_basis="model_gateway_preflight_total{status=UNKNOWN} / model_gateway_preflight_total",
            numerator=preflight_unknown,
            denominator=preflight_total,
            current_warn=1.0,
            current_crit=1.0,
        ),
    ]


def render_calibration_md(
    suggestions: list[CalibrationSuggestion],
    *,
    generated_at: str,
    window_hours: int,
    total_events_in_window: int,
) -> str:
    lines = [
        "# Alert Esik Kalibrasyonu -- Model Gateway",
        "",
        f"Uretildi (UTC): {generated_at}",
        f"Pencere: son {window_hours} saat ({total_events_in_window} toplam olay, sentetik haric)",
        "",
        "> **DURUSTLUK NOTU:** bkz. `scripts/ops/calibrate_alert_thresholds.py` modul "
        "docstring'i -- bu makinede gercek uretim trafigi yoksa/azsa, oneriler "
        "`INSUFFICIENT_DATA` olarak isaretlenir ve mevcut varsayilanlar korunur. "
        "Bu, tek seferlik bir kurulum adimi degildir -- gercek trafik biriktikce "
        "yeniden calistirilmalidir.",
        "",
        "| Alert | Durum | Ornek sayisi | Gozlenen oran | Mevcut WARN | Onerilen WARN | Mevcut CRIT | Onerilen CRIT |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in suggestions:
        rate_str = f"{s.observed_rate:.4f}" if s.observed_rate is not None else "n/a"
        lines.append(
            f"| {s.alert_name} | {s.status} | {s.sample_size} | {rate_str} | "
            f"{s.current_warn} | {s.suggested_warn:.4f} | {s.current_crit} | {s.suggested_crit:.4f} |"
        )

    lines += ["", "## Gerekce + yanlis-pozitif riski (her alert icin)", ""]
    for s in suggestions:
        lines += [
            f"### {s.alert_name}",
            f"- Metrik temeli: `{s.metric_basis}`",
            f"- Gerekce: {s.rationale}",
            f"- Yanlis-pozitif riski: {s.false_positive_risk}",
            "",
        ]

    return "\n".join(lines)


def write_calibration_report(suggestions: list[CalibrationSuggestion], out_dir: Path, **kwargs: Any) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    md = render_calibration_md(suggestions, **kwargs)
    out_path = out_dir / "calibration.md"
    out_path.write_text(md, encoding="utf-8")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Model gateway alert esik kalibrasyonu")
    parser.add_argument("--hours", type=int, default=24, help="Geriye donuk kac saatlik pencere (varsayilan 24)")
    parser.add_argument("--jsonl-path", default=None, help="Varsayilan: config.metrics_jsonl_path")
    parser.add_argument("--output-dir", default=None, help="Varsayilan: reports/alert_calibration_<UTC>/")
    args = parser.parse_args()

    from model_gateway import metrics_aggregate
    from model_gateway.config import load_config

    config = load_config()
    jsonl_path = Path(args.jsonl_path) if args.jsonl_path else Path(config.metrics_jsonl_path)

    events = metrics_aggregate.read_recent_events(jsonl_path, window_minutes=args.hours * 60)
    non_synthetic_events = [e for e in events if not _is_synthetic(e)]

    suggestions = calibrate_all(
        non_synthetic_events,
        current_null_intent_warn=config.alert_null_intent_warn,
        current_null_intent_crit=config.alert_null_intent_crit,
        current_fallback_spike_multiplier=config.alert_fallback_spike_multiplier,
    )

    generated_at = datetime.now(timezone.utc).isoformat()
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = Path("reports") / f"alert_calibration_{ts}"

    out_path = write_calibration_report(
        suggestions,
        out_dir,
        generated_at=generated_at,
        window_hours=args.hours,
        total_events_in_window=len(non_synthetic_events),
    )

    print(render_calibration_md(
        suggestions, generated_at=generated_at, window_hours=args.hours,
        total_events_in_window=len(non_synthetic_events),
    ))
    print(f"calibration_md={out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
