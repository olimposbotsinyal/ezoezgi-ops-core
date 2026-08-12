#!/usr/bin/env python
"""NLU değerlendirme aracı (Faz 1, B031 completion gate).

Türkçe golden veri seti üzerinde `mock` ve `ollama` NLU sağlayıcılarını
karşılaştırır; intent accuracy, entity match rate, parse error rate,
fallback rate ve p50/p95 latency metriklerini hesaplar, markdown rapor
üretir (`reports/nlu_eval_<YYYYMMDD>.md`).

Ollama bu makinede kurulu/çalışır değilse (health check başarısız), ollama
sütunu **uydurulmaz** — "N/A, sebep: ..." olarak işaretlenir; yalnızca mock
için gerçek sonuçlar üretilir.

Kullanım:
    python tools/eval_nlu.py
    python tools/eval_nlu.py --providers mock
    python tools/eval_nlu.py --golden tests/fixtures/nlu_golden_tr.jsonl --out reports/custom.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

for _src_dir in (REPO_ROOT / "services" / "tr-en-bridge" / "src",):
    sys.path.insert(0, str(_src_dir))

import bridge  # noqa: E402
from model_client import OllamaModelClient  # noqa: E402
from ollama_nlu import classify as ollama_classify  # noqa: E402

DEFAULT_GOLDEN_PATH = REPO_ROOT / "tests" / "fixtures" / "nlu_golden_tr.jsonl"
DEFAULT_REPORT_DIR = REPO_ROOT / "reports"
KNOWN_INTENTS = [bridge.TASK_RUN_ECHO, bridge.TASK_SHOW_DAILY_SPENDING, bridge.TASK_RUN_DELETE_FILE]

# ollama_nlu.OllamaModelClient basarisiz oldugunda ("fallback": True) bu
# onekle baslayan bir metin doner (bkz. model_client.py::_mock_fallback) --
# bunu, servisin gercekten erisilemez oldugunu (parse hatasindan ayirt
# ederek) tespit etmek icin kullaniyoruz.
_FALLBACK_PREFIX = "[mock] "

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)

ACCEPTANCE_CRITERIA: dict[str, tuple[float, str]] = {
    "intent_accuracy": (0.90, "min"),
    "entity_match_rate": (0.85, "min"),
    "parse_error_rate": (0.02, "max"),
    "fallback_rate": (0.05, "max"),
    "latency_p95": (2.5, "max"),
}


# --- Veri modelleri ---------------------------------------------------------


@dataclass
class GoldenSample:
    text: str
    expected_intent: str
    expected_entities: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    risk_level: str | None = None


@dataclass
class SampleResult:
    sample: GoldenSample
    predicted_intent: str
    predicted_entities: dict[str, Any]
    confidence: float
    latency_seconds: float
    parse_error: bool
    fallback: bool


@dataclass
class ProviderMetrics:
    provider: str
    available: bool
    total_samples: int
    intent_correct: int
    intent_accuracy: float
    entity_applicable: int
    entity_correct: int
    entity_match_rate: float | None
    parse_errors: int
    parse_error_rate: float
    fallbacks: int
    fallback_rate: float
    latency_p50: float
    latency_p95: float
    unavailable_reason: str = ""


# --- Golden set yukleme ------------------------------------------------------


def load_golden_set(path: Path) -> list[GoldenSample]:
    """`path`'teki JSONL golden veri setini okur ve dogrular.

    Her satir en az `text` ve `expected_intent` alanlarini icermeli; aksi
    halde acik bir `ValueError` firlatilir (satir numarasi dahil).
    """
    samples: list[GoldenSample] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: geçersiz JSON: {exc}") from exc

            if not isinstance(data, dict):
                raise ValueError(f"{path}:{line_no}: her satır bir JSON objesi olmalı")
            if "text" not in data or not data["text"]:
                raise ValueError(f"{path}:{line_no}: 'text' alanı zorunlu ve boş olamaz")
            if "expected_intent" not in data or not data["expected_intent"]:
                raise ValueError(f"{path}:{line_no}: 'expected_intent' alanı zorunlu")

            samples.append(
                GoldenSample(
                    text=data["text"],
                    expected_intent=data["expected_intent"],
                    expected_entities=data.get("expected_entities") or {},
                    notes=data.get("notes", ""),
                    risk_level=data.get("risk_level"),
                )
            )

    if not samples:
        raise ValueError(f"{path}: golden set boş")

    return samples


# --- Siniflandirma calistirma -----------------------------------------------


def _run_mock(sample: GoldenSample) -> SampleResult:
    start = time.perf_counter()
    task_en, confidence = bridge._mock_classify(sample.text)
    latency = time.perf_counter() - start
    return SampleResult(
        sample=sample,
        predicted_intent=task_en,
        predicted_entities={},  # mock hicbir zaman entity uretmez (bkz. bridge.py)
        confidence=confidence,
        latency_seconds=latency,
        parse_error=False,
        fallback=False,
    )


def _has_parseable_json(raw: str) -> bool:
    match = _JSON_BLOCK_RE.search(raw or "")
    if not match:
        return False
    try:
        json.loads(match.group(0))
        return True
    except json.JSONDecodeError:
        return False


def _run_ollama(sample: GoldenSample, client: OllamaModelClient) -> SampleResult:
    start = time.perf_counter()
    canonical = ollama_classify(sample.text, KNOWN_INTENTS, client=client)
    latency = time.perf_counter() - start

    raw = canonical.get("raw") or ""
    is_fallback = raw.startswith(_FALLBACK_PREFIX)
    is_parse_error = (not is_fallback) and not _has_parseable_json(raw)

    return SampleResult(
        sample=sample,
        predicted_intent=canonical.get("intent", "UNKNOWN"),
        predicted_entities=canonical.get("entities") or {},
        confidence=float(canonical.get("confidence") or 0.0),
        latency_seconds=latency,
        parse_error=is_parse_error,
        fallback=is_fallback,
    )


# --- Metrik hesaplama ---------------------------------------------------------


def _entities_match(expected: dict[str, Any], actual: dict[str, Any]) -> bool | None:
    """Kritik alanlar eslesiyor mu? `expected` bossa None (uygulanamaz) doner."""
    if not expected:
        return None
    return all(actual.get(key) == value for key, value in expected.items())


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * (pct / 100)
    lower = int(k)
    upper = min(lower + 1, len(sorted_values) - 1)
    if lower == upper:
        return sorted_values[lower]
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (k - lower)


def compute_metrics(
    provider: str,
    results: list[SampleResult],
    available: bool = True,
    unavailable_reason: str = "",
) -> ProviderMetrics:
    if not available or not results:
        return ProviderMetrics(
            provider=provider,
            available=False,
            total_samples=0,
            intent_correct=0,
            intent_accuracy=0.0,
            entity_applicable=0,
            entity_correct=0,
            entity_match_rate=None,
            parse_errors=0,
            parse_error_rate=0.0,
            fallbacks=0,
            fallback_rate=0.0,
            latency_p50=0.0,
            latency_p95=0.0,
            unavailable_reason=unavailable_reason,
        )

    total = len(results)
    intent_correct = sum(1 for r in results if r.predicted_intent == r.sample.expected_intent)

    entity_checks = [_entities_match(r.sample.expected_entities, r.predicted_entities) for r in results]
    applicable = [m for m in entity_checks if m is not None]
    entity_correct = sum(1 for m in applicable if m)

    parse_errors = sum(1 for r in results if r.parse_error)
    fallbacks = sum(1 for r in results if r.fallback)
    latencies = sorted(r.latency_seconds for r in results)

    return ProviderMetrics(
        provider=provider,
        available=True,
        total_samples=total,
        intent_correct=intent_correct,
        intent_accuracy=intent_correct / total,
        entity_applicable=len(applicable),
        entity_correct=entity_correct,
        entity_match_rate=(entity_correct / len(applicable)) if applicable else None,
        parse_errors=parse_errors,
        parse_error_rate=parse_errors / total,
        fallbacks=fallbacks,
        fallback_rate=fallbacks / total,
        latency_p50=_percentile(latencies, 50),
        latency_p95=_percentile(latencies, 95),
    )


def evaluate_acceptance(metrics: ProviderMetrics) -> dict[str, dict[str, Any]]:
    """Kabul kriterlerini (ACCEPTANCE_CRITERIA) metriklere karsi degerlendirir."""
    if not metrics.available:
        return {
            name: {"status": "NOT_EVALUATED", "value": None, "threshold": threshold, "reason": metrics.unavailable_reason}
            for name, (threshold, _direction) in ACCEPTANCE_CRITERIA.items()
        }

    verdicts: dict[str, dict[str, Any]] = {}
    for name, (threshold, direction) in ACCEPTANCE_CRITERIA.items():
        value = getattr(metrics, name)
        if value is None:
            verdicts[name] = {"status": "N/A", "value": None, "threshold": threshold, "reason": "uygulanamaz (kritik entity yok)"}
            continue
        passed = (value >= threshold) if direction == "min" else (value <= threshold)
        verdicts[name] = {"status": "PASS" if passed else "FAIL", "value": value, "threshold": threshold, "reason": ""}
    return verdicts


# --- Rapor uretimi ------------------------------------------------------------


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _pct_or_na(value: float | None) -> str:
    return _pct(value) if value is not None else "N/A"


def _ms(value: float) -> str:
    return f"{value * 1000:.1f} ms"


def render_report(
    golden_path: Path,
    metrics_by_provider: dict[str, ProviderMetrics],
    verdicts_by_provider: dict[str, dict[str, dict[str, Any]]],
    generated_at: datetime,
) -> str:
    lines: list[str] = []
    lines.append("# NLU Değerlendirme Raporu — B031 Quality Gate")
    lines.append("")
    lines.append(f"- Üretim tarihi (UTC): {generated_at.isoformat(timespec='seconds')}")
    try:
        golden_rel = golden_path.relative_to(REPO_ROOT)
    except ValueError:
        golden_rel = golden_path
    lines.append(f"- Golden set: `{golden_rel}`")
    lines.append("")

    lines.append("## Metrikler (mock vs ollama)")
    lines.append("")
    lines.append("| Metrik | mock | ollama |")
    lines.append("|---|---|---|")

    mock_m = metrics_by_provider.get("mock")
    ollama_m = metrics_by_provider.get("ollama")

    def cell(m: ProviderMetrics | None, formatter) -> str:
        if m is None or not m.available:
            return "N/A"
        return formatter(m)

    lines.append(f"| Örnek sayısı | {cell(mock_m, lambda m: str(m.total_samples))} | {cell(ollama_m, lambda m: str(m.total_samples))} |")
    lines.append(f"| Intent accuracy | {cell(mock_m, lambda m: _pct(m.intent_accuracy))} | {cell(ollama_m, lambda m: _pct(m.intent_accuracy))} |")
    lines.append(f"| Entity match rate | {cell(mock_m, lambda m: _pct_or_na(m.entity_match_rate))} | {cell(ollama_m, lambda m: _pct_or_na(m.entity_match_rate))} |")
    lines.append(f"| Parse error rate | {cell(mock_m, lambda m: _pct(m.parse_error_rate))} | {cell(ollama_m, lambda m: _pct(m.parse_error_rate))} |")
    lines.append(f"| Fallback rate | {cell(mock_m, lambda m: _pct(m.fallback_rate))} | {cell(ollama_m, lambda m: _pct(m.fallback_rate))} |")
    lines.append(f"| Latency p50 | {cell(mock_m, lambda m: _ms(m.latency_p50))} | {cell(ollama_m, lambda m: _ms(m.latency_p50))} |")
    lines.append(f"| Latency p95 | {cell(mock_m, lambda m: _ms(m.latency_p95))} | {cell(ollama_m, lambda m: _ms(m.latency_p95))} |")
    lines.append("")

    for provider in ("mock", "ollama"):
        m = metrics_by_provider.get(provider)
        lines.append(f"### {provider}")
        lines.append("")
        if m is None or not m.available:
            reason = m.unavailable_reason if m else "çalıştırılmadı"
            lines.append(f"**Kullanılamıyor.** Sebep: {reason}")
            lines.append("")
            continue

        verdicts = verdicts_by_provider.get(provider, {})
        lines.append("| Kriter | Değer | Eşik | Sonuç |")
        lines.append("|---|---|---|---|")
        for name, (threshold, direction) in ACCEPTANCE_CRITERIA.items():
            v = verdicts.get(name, {})
            value = v.get("value")
            value_str = _pct_or_na(value) if name != "latency_p95" else (f"{value:.2f}s" if value is not None else "N/A")
            threshold_str = _pct(threshold) if name != "latency_p95" else f"{threshold:.2f}s"
            op = "≥" if direction == "min" else "≤"
            lines.append(f"| {name} | {value_str} | {op} {threshold_str} | {v.get('status', 'N/A')} |")
        lines.append("")

    lines.append("## Sonuç")
    lines.append("")
    if ollama_m is not None and ollama_m.available:
        ollama_verdicts = verdicts_by_provider.get("ollama", {})
        all_pass = all(v["status"] in ("PASS", "N/A") for v in ollama_verdicts.values())
        lines.append(
            "**B031 quality gate: PASS** — tüm ollama kriterleri eşikleri karşılıyor."
            if all_pass
            else "**B031 quality gate: FAIL** — en az bir ollama kriteri eşiği karşılamıyor, ayrıntı için yukarıdaki tabloya bakın."
        )
    else:
        reason = ollama_m.unavailable_reason if ollama_m else "çalıştırılmadı"
        lines.append(
            "**B031 quality gate: DEĞERLENDİRİLEMEDİ (NOT_EVALUATED)** — canlı Ollama bu ortamda "
            f"kullanılamadığı için kriterler test edilemedi. Sebep: {reason} "
            "Bu bir başarısızlık değil, bir ortam kısıtıdır; `mock` sonuçları yalnızca referans amaçlıdır "
            "(mock hiçbir zaman entity üretmez, bu yüzden entity match rate her zaman 0%'dır)."
        )
    lines.append("")

    return "\n".join(lines) + "\n"


# --- CLI -----------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NLU değerlendirme aracı (B031 quality gate)")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN_PATH)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--providers", nargs="+", choices=["mock", "ollama"], default=["mock", "ollama"])
    return parser.parse_args(argv)


def run_evaluation(golden_path: Path, providers: list[str]) -> tuple[dict[str, ProviderMetrics], dict[str, dict[str, dict[str, Any]]]]:
    golden = load_golden_set(golden_path)

    metrics_by_provider: dict[str, ProviderMetrics] = {}

    if "mock" in providers:
        mock_results = [_run_mock(s) for s in golden]
        metrics_by_provider["mock"] = compute_metrics("mock", mock_results)
    else:
        metrics_by_provider["mock"] = compute_metrics("mock", [], available=False, unavailable_reason="--providers ile devre dışı bırakıldı")

    if "ollama" in providers:
        client = OllamaModelClient()
        if client.health_check():
            ollama_results = [_run_ollama(s, client) for s in golden]
            metrics_by_provider["ollama"] = compute_metrics("ollama", ollama_results)
        else:
            metrics_by_provider["ollama"] = compute_metrics(
                "ollama",
                [],
                available=False,
                unavailable_reason=(
                    f"Ollama servisine ulaşılamadı ({client.base_url}). Kurulum: "
                    f"https://ollama.com adresinden kurup 'ollama serve' ile başlatın, "
                    f"ardından 'ollama pull {client.model}' ile modeli indirin."
                ),
            )
    else:
        metrics_by_provider["ollama"] = compute_metrics("ollama", [], available=False, unavailable_reason="--providers ile devre dışı bırakıldı")

    verdicts_by_provider = {
        provider: evaluate_acceptance(metrics) for provider, metrics in metrics_by_provider.items()
    }

    return metrics_by_provider, verdicts_by_provider


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    args = parse_args(argv)
    generated_at = datetime.now(timezone.utc)

    metrics_by_provider, verdicts_by_provider = run_evaluation(args.golden, args.providers)

    report = render_report(args.golden, metrics_by_provider, verdicts_by_provider, generated_at)

    out_path = args.out or (DEFAULT_REPORT_DIR / f"nlu_eval_{generated_at.strftime('%Y%m%d')}.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")

    print(report)
    try:
        rel_out = out_path.relative_to(REPO_ROOT)
    except ValueError:
        rel_out = out_path
    print(f"[rapor] {rel_out}")

    ollama_metrics = metrics_by_provider.get("ollama")
    if ollama_metrics is not None and ollama_metrics.available:
        ollama_verdicts = verdicts_by_provider.get("ollama", {})
        if any(v["status"] == "FAIL" for v in ollama_verdicts.values()):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
