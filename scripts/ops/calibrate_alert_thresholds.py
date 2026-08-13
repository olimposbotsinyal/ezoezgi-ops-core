"""Alert esik kalibrasyon paketi v1 -- 24h/7d/14d pencerelerinden
ONERILEN WARN/CRIT esiklerini + guven skoru + gerekce + yanlis-pozitif
(false-positive) riski + degisiklik-etkisi notu hesaplar.

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
  - Yeterli ornek olsa BILE, bu bir GUVEN SKORU (LOW/MEDIUM/HIGH) ile
    birlikte raporlanir -- yalnizca birden fazla pencere (24h/7d/14d)
    YETERLI VE TUTARLI veri gosterirse HIGH verilir.
  - **POLITIKA KORUMASI: bu script ASLA config'i otomatik degistirmez.**
    Yalnizca bir ONERI dosyasi (`proposed_threshold_patch.yaml`) uretir
    -- uygulamak HER ZAMAN bir operatorun elle, bilerek yaptigi ayri bir
    adimdir.
Gercek go-live sonrasi, ilk gercek uretim penceresi biriktikce bu script
YENIDEN calistirilip esikler yeniden degerlendirilmelidir -- tek
seferlik bir kurulum adimi DEGILDIR.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MIN_SAMPLE_SIZE = 30  # bu esigin altinda "kalibre edilmis" oneri YAPILMAZ
ROBUST_SAMPLE_SIZE = MIN_SAMPLE_SIZE * 5  # bu ustunde ornek sayisi "YETERLI" sayilir (SINIRDA degil)
WARN_MULTIPLIER = 2.0
CRIT_MULTIPLIER = 4.0
CONSISTENCY_RELATIVE_SPREAD_THRESHOLD = 0.5  # pencereler arasi goreli fark bu esigin altindaysa "tutarli"

INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
CALIBRATED = "CALIBRATED"

CONFIDENCE_LOW = "LOW"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_HIGH = "HIGH"

WINDOWS_HOURS = {"24h": 24, "7d": 24 * 7, "14d": 24 * 14}

# Alert -> ilgili config.py ortam degiskeni(leri). `None` -- bu alert'in
# su an ayri, ozel bir esik ortam degiskeni YOK (kod-ici sabit deger) --
# oneri raporunda gorunur ama proposed_threshold_patch.yaml'a GIRMEZ
# (uygulanacak somut bir ortam degiskeni olmadigindan).
ALERT_ENV_VAR_MAP: dict[str, dict[str, str | None]] = {
    "HIGH_NULL_INTENT_RATE": {"warn": "ALERT_NULL_INTENT_WARN", "crit": "ALERT_NULL_INTENT_CRIT"},
    "FALLBACK_SPIKE": {"warn": "ALERT_FALLBACK_SPIKE_MULTIPLIER", "crit": "ALERT_FALLBACK_SPIKE_MULTIPLIER"},
    "PRIMARY_RESTRICTED_PERSISTENT": {"warn": None, "crit": None},
    "PREFLIGHT_UNKNOWN_PERSISTENT": {"warn": None, "crit": None},
}


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


@dataclass
class AlertCalibrationV1:
    alert_name: str
    metric_basis: str
    primary_window: str
    primary_suggestion: CalibrationSuggestion
    window_suggestions: dict[str, CalibrationSuggestion]
    confidence: str
    confidence_rationale: str
    sample_adequacy: str
    change_impact: str
    env_vars: dict[str, str] = field(default_factory=dict)


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


def describe_sample_adequacy(sample_size: int) -> str:
    if sample_size < MIN_SAMPLE_SIZE:
        return f"YETERSIZ ({sample_size} ornek < esik {MIN_SAMPLE_SIZE})"
    if sample_size < ROBUST_SAMPLE_SIZE:
        return f"SINIRDA ({sample_size} ornek -- calisir ama kucuk, esik: {ROBUST_SAMPLE_SIZE}+ 'YETERLI' icin)"
    return f"YETERLI ({sample_size} ornek >= {ROBUST_SAMPLE_SIZE})"


def describe_change_impact(current: float, suggested: float) -> str:
    if suggested == current:
        return "Onerilen deger mevcut varsayilanla AYNI -- degisiklik gerekmiyor/onerilmiyor."
    direction = "GEVSETIR (daha az hassas, daha az alarm)" if suggested > current else "SIKILASTIRIR (daha hassas, daha fazla alarm)"
    pct = abs(suggested - current) / current * 100 if current else None
    pct_str = f"~%{pct:.0f}" if pct is not None else "N/A (mevcut deger 0)"
    return (
        f"Mevcut degerden {pct_str} farkli -- esigi {direction}. Uygulanirsa, gozlenen "
        "pencere(ler)deki oran esige daha yakin/uzak bir tetikleme noktasi olusturur -- "
        "gercek etkiyi olcmek icin uygulama SONRASI en az bir pencere daha gozlemleyin."
    )


def score_confidence(window_suggestions: dict[str, CalibrationSuggestion]) -> tuple[str, str]:
    """`window_suggestions`: {"24h": ..., "7d": ..., "14d": ...}. Yalnizca
    `CALIBRATED` (yeterli ornekli) pencereler dikkate alinir. Doner:
    (confidence, gerekce)."""
    calibrated = {w: s for w, s in window_suggestions.items() if s.status == CALIBRATED}
    if not calibrated:
        return CONFIDENCE_LOW, "Hicbir pencerede yeterli ornek yok (hepsi INSUFFICIENT_DATA)."
    if len(calibrated) == 1:
        (only_window,) = calibrated.keys()
        return (
            CONFIDENCE_LOW,
            f"Yalnizca '{only_window}' penceresinde yeterli veri var -- capraz-pencere "
            "tutarlilik dogrulamasi yapilamadi.",
        )

    rates = [s.observed_rate for s in calibrated.values() if s.observed_rate is not None]
    max_rate, min_rate = max(rates), min(rates)
    relative_spread = (max_rate - min_rate) / max_rate if max_rate else 0.0

    if relative_spread <= CONSISTENCY_RELATIVE_SPREAD_THRESHOLD:
        return (
            CONFIDENCE_HIGH,
            f"{len(calibrated)} pencerede ({', '.join(sorted(calibrated))}) yeterli ornek VE "
            f"tutarli gozlenen oranlar (pencereler-arasi goreli fark=%{relative_spread * 100:.0f}).",
        )
    return (
        CONFIDENCE_MEDIUM,
        f"{len(calibrated)} pencerede yeterli ornek var ama oranlar TUTARSIZ "
        f"(goreli fark=%{relative_spread * 100:.0f}, esik=%{CONSISTENCY_RELATIVE_SPREAD_THRESHOLD * 100:.0f}) "
        "-- trafik deseni degisiyor olabilir, dikkatli yorumlayin.",
    )


def _pick_primary_window(window_suggestions: dict[str, CalibrationSuggestion]) -> str:
    """En BUYUK (en cok veri kapsayan) pencereyi tercih eder -- CALIBRATED
    olanlar arasinda en genis pencere (14d > 7d > 24h); hicbiri CALIBRATED
    degilse en genis pencere (yine de INSUFFICIENT_DATA olarak kalir,
    ama en fazla baglami tasir)."""
    preference_order = ["14d", "7d", "24h"]
    calibrated_order = [w for w in preference_order if window_suggestions[w].status == CALIBRATED]
    if calibrated_order:
        return calibrated_order[0]
    return preference_order[0]


def calibrate_multi_window(
    events_by_window: dict[str, list[Any]],
    *,
    current_null_intent_warn: float,
    current_null_intent_crit: float,
    current_fallback_spike_multiplier: float,
) -> list[AlertCalibrationV1]:
    per_window_suggestions: dict[str, list[CalibrationSuggestion]] = {
        window: calibrate_all(
            events,
            current_null_intent_warn=current_null_intent_warn,
            current_null_intent_crit=current_null_intent_crit,
            current_fallback_spike_multiplier=current_fallback_spike_multiplier,
        )
        for window, events in events_by_window.items()
    }

    alert_names = [s.alert_name for s in next(iter(per_window_suggestions.values()))]
    results: list[AlertCalibrationV1] = []
    for idx, alert_name in enumerate(alert_names):
        window_suggestions = {window: suggestions[idx] for window, suggestions in per_window_suggestions.items()}
        primary_window = _pick_primary_window(window_suggestions)
        primary = window_suggestions[primary_window]
        confidence, confidence_rationale = score_confidence(window_suggestions)

        env_map = ALERT_ENV_VAR_MAP.get(alert_name, {"warn": None, "crit": None})
        env_vars: dict[str, str] = {}
        if primary.status == CALIBRATED:
            # Yalnizca GERCEKTEN mevcut degerden FARKLI oneriler patch'e
            # girer -- "mevcutla ayni degeri ayarlayin" gibi anlamsiz/
            # kafa karistirici bir oneri operatore GOSTERILMEZ.
            if env_map.get("warn") and primary.suggested_warn != primary.current_warn:
                env_vars[env_map["warn"]] = f"{primary.suggested_warn:.4f}"
            if (
                env_map.get("crit")
                and env_map.get("crit") != env_map.get("warn")
                and primary.suggested_crit != primary.current_crit
            ):
                env_vars[env_map["crit"]] = f"{primary.suggested_crit:.4f}"

        results.append(
            AlertCalibrationV1(
                alert_name=alert_name,
                metric_basis=primary.metric_basis,
                primary_window=primary_window,
                primary_suggestion=primary,
                window_suggestions=window_suggestions,
                confidence=confidence,
                confidence_rationale=confidence_rationale,
                sample_adequacy=describe_sample_adequacy(primary.sample_size),
                change_impact=describe_change_impact(primary.current_warn, primary.suggested_warn),
                env_vars=env_vars,
            )
        )
    return results


def render_calibration_v1_md(
    results: list[AlertCalibrationV1], *, generated_at: str, window_event_counts: dict[str, int]
) -> str:
    lines = [
        "# Alert Esik Kalibrasyonu v1 -- Model Gateway",
        "",
        f"Uretildi (UTC): {generated_at}",
        f"Pencere olay sayilari (sentetik haric): "
        + ", ".join(f"{w}={n}" for w, n in window_event_counts.items()),
        "",
        "> **DURUSTLUK NOTU + POLITIKA:** bkz. `scripts/ops/calibrate_alert_thresholds.py` "
        "modul docstring'i. Bu script ASLA config'i otomatik degistirmez -- yalnizca "
        "`proposed_threshold_patch.yaml` adinda bir ONERI dosyasi uretir, uygulamak "
        "operatorun ELLE yaptigi ayri bir karardir.",
        "",
        "| Alert | Durum (birincil pencere) | Birincil pencere | Guven | Ornek yeterliligi | Mevcut WARN | Onerilen WARN | Mevcut CRIT | Onerilen CRIT |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        p = r.primary_suggestion
        lines.append(
            f"| {r.alert_name} | {p.status} | {r.primary_window} | {r.confidence} | "
            f"{r.sample_adequacy} | {p.current_warn} | {p.suggested_warn:.4f} | "
            f"{p.current_crit} | {p.suggested_crit:.4f} |"
        )

    lines += ["", "## Pencereler arasi karsilastirma", "", "| Alert | 24h durum/oran | 7d durum/oran | 14d durum/oran |", "|---|---|---|---|"]
    for r in results:
        cells = []
        for w in ("24h", "7d", "14d"):
            s = r.window_suggestions[w]
            rate = f"{s.observed_rate:.4f}" if s.observed_rate is not None else "n/a"
            cells.append(f"{s.status} ({rate})")
        lines.append(f"| {r.alert_name} | {cells[0]} | {cells[1]} | {cells[2]} |")

    lines += ["", "## Ayrinti (her alert icin)", ""]
    for r in results:
        p = r.primary_suggestion
        lines += [
            f"### {r.alert_name}",
            f"- Metrik temeli: `{r.metric_basis}`",
            f"- Guven skoru: **{r.confidence}** -- {r.confidence_rationale}",
            f"- Ornek boyutu yeterliligi: {r.sample_adequacy}",
            f"- Gerekce (birincil pencere: {r.primary_window}): {p.rationale}",
            f"- Yanlis-pozitif riski: {p.false_positive_risk}",
            f"- Degisiklik-etkisi notu: {r.change_impact}",
            f"- Uygulanabilir ortam degiskeni: {', '.join(r.env_vars.keys()) if r.env_vars else 'YOK (bu alert icin ayri bir esik ortam degiskeni henuz tanimli degil)'}",
            "",
        ]

    return "\n".join(lines)


def build_calibration_v1_json(
    results: list[AlertCalibrationV1], *, generated_at: str, window_event_counts: dict[str, int]
) -> dict[str, Any]:
    def _suggestion_dict(s: CalibrationSuggestion) -> dict[str, Any]:
        return asdict(s)

    return {
        "version": 1,
        "generated_at": generated_at,
        "window_event_counts": window_event_counts,
        "alerts": [
            {
                "alert_name": r.alert_name,
                "metric_basis": r.metric_basis,
                "primary_window": r.primary_window,
                "primary_suggestion": _suggestion_dict(r.primary_suggestion),
                "window_suggestions": {w: _suggestion_dict(s) for w, s in r.window_suggestions.items()},
                "confidence": r.confidence,
                "confidence_rationale": r.confidence_rationale,
                "sample_adequacy": r.sample_adequacy,
                "change_impact": r.change_impact,
                "env_vars": r.env_vars,
            }
            for r in results
        ],
    }


def render_proposed_patch_yaml(results: list[AlertCalibrationV1], *, generated_at: str) -> str:
    lines = [
        "# ONERI DOSYASI -- OTOMATIK UYGULANMAZ.",
        "#",
        "# Bu dosya scripts/ops/calibrate_alert_thresholds.py tarafindan uretilen bir",
        "# ONERIDIR. Uygulamak icin bir operator bu degerleri ELLE inceleyip",
        "# ONAYLADIKTAN SONRA, ilgili ortam degiskenlerini KENDISI ayarlamalidir",
        "# (config/model_gateway.yaml veya ortam degiskeni olarak). Bu dosyanin",
        "# varligi/okunmasi HICBIR ZAMAN config'i degistirmez.",
        f"generated_at: \"{generated_at}\"",
        "policy: NEVER_AUTO_APPLY",
        "status: PROPOSAL_ONLY_NOT_APPLIED",
        "proposals:",
    ]
    any_proposal = False
    for r in results:
        if not r.env_vars or r.primary_suggestion.status != CALIBRATED:
            continue
        any_proposal = True
        lines.append(f"  - alert_name: {r.alert_name}")
        lines.append(f"    confidence: {r.confidence}")
        lines.append(f"    primary_window: {r.primary_window}")
        lines.append("    env_vars:")
        for k, v in r.env_vars.items():
            lines.append(f'      {k}: "{v}"')
        lines.append("")
    if not any_proposal:
        lines.append("  [] # bu calistirmada uygulanabilir bir oneri yok (yetersiz veri veya esik ortam degiskeni tanimli degil)")

    return "\n".join(lines) + "\n"


def write_calibration_v1_report(
    results: list[AlertCalibrationV1], out_dir: Path, *, generated_at: str, window_event_counts: dict[str, int]
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)

    md = render_calibration_v1_md(results, generated_at=generated_at, window_event_counts=window_event_counts)
    md_path = out_dir / "calibration_v1.md"
    md_path.write_text(md, encoding="utf-8")

    import json

    json_path = out_dir / "calibration_v1.json"
    json_path.write_text(
        json.dumps(
            build_calibration_v1_json(results, generated_at=generated_at, window_event_counts=window_event_counts),
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    patch_path = out_dir / "proposed_threshold_patch.yaml"
    patch_path.write_text(render_proposed_patch_yaml(results, generated_at=generated_at), encoding="utf-8")

    return {"md": md_path, "json": json_path, "patch": patch_path}


def main() -> int:
    parser = argparse.ArgumentParser(description="Model gateway alert esik kalibrasyonu v1 (24h/7d/14d)")
    parser.add_argument("--jsonl-path", default=None, help="Varsayilan: config.metrics_jsonl_path")
    parser.add_argument("--output-dir", default=None, help="Varsayilan: reports/alert_calibration_<UTC>/")
    args = parser.parse_args()

    from model_gateway import metrics_aggregate
    from model_gateway.config import load_config

    config = load_config()
    jsonl_path = Path(args.jsonl_path) if args.jsonl_path else Path(config.metrics_jsonl_path)

    largest_window_minutes = WINDOWS_HOURS["14d"] * 60
    all_events = metrics_aggregate.read_recent_events(jsonl_path, window_minutes=largest_window_minutes)
    non_synthetic_events = [e for e in all_events if not _is_synthetic(e)]

    import time

    now = time.time()
    events_by_window: dict[str, list[Any]] = {}
    for window, hours in WINDOWS_HOURS.items():
        cutoff = now - hours * 3600
        events_by_window[window] = [e for e in non_synthetic_events if e.ts >= cutoff]

    results = calibrate_multi_window(
        events_by_window,
        current_null_intent_warn=config.alert_null_intent_warn,
        current_null_intent_crit=config.alert_null_intent_crit,
        current_fallback_spike_multiplier=config.alert_fallback_spike_multiplier,
    )

    window_event_counts = {w: len(events) for w, events in events_by_window.items()}
    generated_at = datetime.now(timezone.utc).isoformat()

    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = Path("reports") / f"alert_calibration_{ts}"

    paths = write_calibration_v1_report(
        results, out_dir, generated_at=generated_at, window_event_counts=window_event_counts
    )

    print(render_calibration_v1_md(results, generated_at=generated_at, window_event_counts=window_event_counts))
    print(f"calibration_md={paths['md']}")
    print(f"calibration_json={paths['json']}")
    print(f"proposed_patch={paths['patch']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
