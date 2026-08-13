"""v1.2 pilot ozelliklerinin YANLIŞ-POZİTİF ORANI (false_positive_rate)
hesaplama -- saf mantik (dosya G/C YOK).

**Neden bir "adjudikasyon" (manual review) dosyasi GEREKIYOR?**
Bir sinyalin (ornegin bir chain-matching BROKEN_CHAIN bulgusu, bir
auto-rollback tetiklenmesi, bir legitimacy FAIL'i) GERCEKTEN yanlis-
pozitif mi yoksa dogru bir uyari mi oldugunu OTOMATIK olarak bilemeyiz
-- bu bir INSAN YARGISI gerektirir. Bu modul, HAM sinyalleri (otomatik
sayilabilir) ile bu sinyallerin bir INSAN tarafindan degerlendirilmis
(`infra/monitoring/governance/pilot_fpr_adjudications.json`) ALT
KUMESINI birlestirerek bir ORAN hesaplar -- yalnizca YETERLI sayida
sinyal DEGERLENDIRILMISSE (bkz. `MIN_ADJUDICATED_FOR_RATE`).

**"Yetersiz veri" ile "sifir yanlis-pozitif" ASLA KARISTIRILMAZ** --
degerlendirilmis sinyal sayisi esigin ALTINDAysa, oran HER ZAMAN `None`
+ `STATUS_INSUFFICIENT_DATA` doner, ASLA `0.0` FABRIKE EDILMEZ."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

STATUS_COMPUTED = "COMPUTED"
STATUS_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

VERDICT_TRUE_POSITIVE = "CONFIRMED_TRUE_POSITIVE"
VERDICT_FALSE_POSITIVE = "CONFIRMED_FALSE_POSITIVE"
VALID_VERDICTS = (VERDICT_TRUE_POSITIVE, VERDICT_FALSE_POSITIVE)

CONFIDENCE_LOW = "LOW"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_HIGH = "HIGH"

# Esikler -- KASITLI olarak muhafazakar (dusuk ornek boyutunda YUKSEK
# guven ASLA iddia edilmez). `calibrate_alert_thresholds.py`'nin cok
# pencereli guven skorlama ilkesiyle AYNI ruh.
MIN_ADJUDICATED_FOR_RATE = 3
CONFIDENCE_MEDIUM_THRESHOLD = 10
CONFIDENCE_HIGH_THRESHOLD = 30


@dataclass
class Signal:
    """Otomatik olarak SAYILABILEN, potansiyel olarak degerlendirilmesi
    GEREKEN tek bir olay (ornegin bir BROKEN_CHAIN bulgusu, bir
    auto-rollback tetiklenmesi, bir legitimacy FAIL'i)."""

    feature: str
    signal_id: str
    evidence_path: str


@dataclass
class Adjudication:
    feature: str
    signal_id: str
    verdict: str
    adjudicated_by: str
    adjudicated_at_utc: str
    notes: str = ""

    def __post_init__(self) -> None:
        if self.verdict not in VALID_VERDICTS:
            raise ValueError(f"gecersiz verdict: {self.verdict!r} (gecerli: {VALID_VERDICTS})")


@dataclass
class FeatureFprResult:
    total_signals: int = 0
    adjudicated_signals: int = 0
    confirmed_false_positives: int = 0
    false_positive_rate: float | None = None
    confidence_band: str | None = None
    status: str = STATUS_INSUFFICIENT_DATA


def confidence_band_for_sample_size(adjudicated_signals: int) -> str | None:
    if adjudicated_signals < MIN_ADJUDICATED_FOR_RATE:
        return None
    if adjudicated_signals >= CONFIDENCE_HIGH_THRESHOLD:
        return CONFIDENCE_HIGH
    if adjudicated_signals >= CONFIDENCE_MEDIUM_THRESHOLD:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


def compute_feature_fpr(signals: list[Signal], adjudications: list[Adjudication]) -> FeatureFprResult:
    """Belirli bir OZELLIGE ait sinyaller + adjudikasyonlardan FPR
    hesaplar. `signals` VE `adjudications` cagiran taraf tarafindan ZATEN
    o ozellige FILTRELENMIS olmalidir (bu fonksiyon `feature` alanina
    BAKMAZ -- yalnizca `signal_id` ile eslestirir)."""
    adjudication_by_signal_id = {a.signal_id: a for a in adjudications}
    matched = [adjudication_by_signal_id[s.signal_id] for s in signals if s.signal_id in adjudication_by_signal_id]

    total_signals = len(signals)
    adjudicated_signals = len(matched)
    confirmed_false_positives = sum(1 for a in matched if a.verdict == VERDICT_FALSE_POSITIVE)

    if adjudicated_signals < MIN_ADJUDICATED_FOR_RATE:
        return FeatureFprResult(
            total_signals=total_signals,
            adjudicated_signals=adjudicated_signals,
            confirmed_false_positives=confirmed_false_positives,
            false_positive_rate=None,
            confidence_band=None,
            status=STATUS_INSUFFICIENT_DATA,
        )

    rate = confirmed_false_positives / adjudicated_signals
    return FeatureFprResult(
        total_signals=total_signals,
        adjudicated_signals=adjudicated_signals,
        confirmed_false_positives=confirmed_false_positives,
        false_positive_rate=rate,
        confidence_band=confidence_band_for_sample_size(adjudicated_signals),
        status=STATUS_COMPUTED,
    )


def validate_adjudications_file(payload: Any) -> list[str]:
    """`pilot_fpr_adjudications.json`'un yapisini dogrular -- hata
    mesajlari listesi doner (bos = gecerli). ASLA exception firlatmaz."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return [f"adjudikasyon dosyasi bir obje (dict) olmali, gozlenen tip: {type(payload).__name__}"]
    entries = payload.get("adjudications")
    if not isinstance(entries, list):
        return ["'adjudications' alani eksik veya liste degil"]
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"adjudications[{i}]: obje (dict) degil")
            continue
        for required in ("feature", "signal_id", "verdict", "adjudicated_by", "adjudicated_at_utc"):
            if required not in entry:
                errors.append(f"adjudications[{i}]: zorunlu alan eksik: '{required}'")
        if "verdict" in entry and entry["verdict"] not in VALID_VERDICTS:
            errors.append(f"adjudications[{i}]: gecersiz verdict: {entry['verdict']!r}")
    return errors


def validate_adjudication_traceability(adjudications: list[Adjudication], signals: list[Signal]) -> list[str]:
    """Her adjudikasyonun GERCEK bir sinyale (feature+signal_id
    eslesmesi) karsilik geldigini VE `adjudicated_by` alaninin bos/anonim
    OLMADIGINI dogrular -- gorev kisiti: "keep entries traceable to
    concrete evidence ids/paths", "no anonymous/manual ambiguous
    entries". Hata mesajlari listesi doner (bos = gecerli), ASLA
    exception firlatmaz.

    Bu, `validate_adjudications_file`'dan (yapi/sema kontrolu) FARKLI
    bir katmandir -- semasi GECERLI bir adjudikasyon yine de bir HAYALET
    (hicbir gercek sinyalle eslesmeyen) veya ANONIM (izlenemeyen bir
    kaynak) girdi OLABILIR; bu fonksiyon SPESIFIK olarak bunlari
    yakalar."""
    errors: list[str] = []
    signal_keys = {(s.feature, s.signal_id) for s in signals}
    for a in adjudications:
        if (a.feature, a.signal_id) not in signal_keys:
            errors.append(
                f"adjudikasyon ({a.feature}, {a.signal_id}) hicbir GERCEK sinyalle eslesmiyor "
                "-- izlenemeyen/hayalet bir girdi"
            )
        if not a.adjudicated_by or not a.adjudicated_by.strip():
            errors.append(
                f"adjudikasyon ({a.feature}, {a.signal_id}): 'adjudicated_by' bos/anonim "
                "-- izlenebilir bir kaynak GEREKIR"
            )
    return errors


def parse_adjudications(payload: dict[str, Any]) -> list[Adjudication]:
    """`validate_adjudications_file` ile ONCEDEN dogrulanmis bir
    payload'dan `Adjudication` nesneleri insa eder. Gecersiz/eksik
    girdiler SESSIZCE atlanir (savunma amacli -- cagiran taraf zaten
    `validate_adjudications_file` ile dogrulamis olmalidir)."""
    result: list[Adjudication] = []
    for entry in payload.get("adjudications", []):
        if not isinstance(entry, dict):
            continue
        try:
            result.append(
                Adjudication(
                    feature=entry["feature"],
                    signal_id=entry["signal_id"],
                    verdict=entry["verdict"],
                    adjudicated_by=entry["adjudicated_by"],
                    adjudicated_at_utc=entry["adjudicated_at_utc"],
                    notes=entry.get("notes", ""),
                )
            )
        except (KeyError, ValueError):
            continue
    return result


# ---------------------------------------------------------------------------
# Rapor uretimi
# ---------------------------------------------------------------------------


def render_fpr_summary_md(results: dict[str, FeatureFprResult], *, generated_at: str) -> str:
    lines = [
        "# v1.2 Pilot Yanlis-Pozitif Orani (False Positive Rate) Ozeti",
        "",
        f"Uretildi (UTC): {generated_at}",
        "",
        "| Ozellik | Toplam Sinyal | Degerlendirilen | Onayli Yanlis-Pozitif | Oran | Guven | Durum |",
        "|---|---|---|---|---|---|---|",
    ]
    for feature, r in results.items():
        rate_str = f"{r.false_positive_rate:.3f}" if r.false_positive_rate is not None else "n/a"
        band_str = r.confidence_band or "n/a"
        lines.append(
            f"| {feature} | {r.total_signals} | {r.adjudicated_signals} | {r.confirmed_false_positives} "
            f"| {rate_str} | {band_str} | {r.status} |"
        )
    lines.append("")
    lines.append(
        f"**NOT:** Bir ozellik icin en az {MIN_ADJUDICATED_FOR_RATE} DEGERLENDIRILMIS sinyal "
        "olmadan oran HESAPLANMAZ (`INSUFFICIENT_DATA` -- ASLA sifir varsayilmaz)."
    )
    return "\n".join(lines) + "\n"


def write_fpr_summary(results: dict[str, FeatureFprResult], out_dir, *, generated_at: str) -> dict[str, Any]:
    import json
    from pathlib import Path

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md = render_fpr_summary_md(results, generated_at=generated_at)
    md_path = out_dir / "fpr_summary.md"
    md_path.write_text(md, encoding="utf-8")

    json_path = out_dir / "fpr_summary.json"
    payload = {
        "generated_at": generated_at,
        "features": {
            feature: {
                "total_signals": r.total_signals,
                "adjudicated_signals": r.adjudicated_signals,
                "confirmed_false_positives": r.confirmed_false_positives,
                "false_positive_rate": r.false_positive_rate,
                "confidence_band": r.confidence_band,
                "status": r.status,
            }
            for feature, r in results.items()
        },
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"md": md_path, "json": json_path}
