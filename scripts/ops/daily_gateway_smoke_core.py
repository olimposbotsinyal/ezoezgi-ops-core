"""Daily gateway smoke -- cekirdek yardimci (platform-bagimsiz Python).

`scripts/ops/daily_gateway_smoke.ps1` bu modulu cagirir. Ayri tutulmasinin
nedeni: mantigin gercek subprocess/dosya sistemi yan etkilerinden
(rapor klasoru olusturma vb.) ayri, testable birimler halinde
kalabilmesi -- testler `run_daily_smoke()`'u sahte router/config ile
cagirip yalnizca DONUS DEGERINI (exit code + sonuc sozlugu) kontrol eder,
gercek Ollama/dosya sistemi gerektirmez.

Kontroller (sirayla):
  1) config sanity (strict/remote bayraklari)
  2) preflight sonuc anlik goruntusu
  3) saglayici saglik anlik goruntusu
  4) tek bir sentetik classify() probu (yikici olmayan, sabit fixture)
  5) audit append dogrulamasi
  6) kanit paketi: reports/daily_smoke_<UTC>/ altina

Cikis kodlari:
  0 -> saglikli
  1 -> bozulmus ama calisiyor (ornegin CPU-verify kapisi kasitli olarak
       Ollama'yi kisitliyor -- STRICT modda beklenen davranis)
  2 -> aksiyon gerekiyor (audit yazilamiyor, hicbir saglayici saglikli
       degil, veya null-intent aciklanamayan bir nedenle olustu)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

EXIT_HEALTHY = 0
EXIT_DEGRADED = 1
EXIT_ACTION_REQUIRED = 2

SMOKE_PROBE_PROMPT = "Ezo, echo ile smoke test yaz"
SMOKE_PROBE_KNOWN_INTENTS = ["RUN_ECHO", "SHOW_DAILY_SPENDING", "RUN_DELETE_FILE"]


@dataclass
class SmokeResult:
    exit_code: int
    findings: list[str] = field(default_factory=list)
    config_sanity: dict[str, Any] = field(default_factory=dict)
    preflight_snapshot: dict[str, Any] = field(default_factory=dict)
    provider_health_snapshot: dict[str, Any] = field(default_factory=dict)
    classify_probe: dict[str, Any] = field(default_factory=dict)
    audit_verification: dict[str, Any] = field(default_factory=dict)
    metrics_snapshot: dict[str, Any] = field(default_factory=dict)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_config_sanity(config: Any, is_remote_allowed_fn: Callable[[], tuple[bool, str]]) -> dict[str, Any]:
    """Deliverable #4.1 -- strict/remote bayraklarinin tutarliligi."""
    findings: list[str] = []
    critical = False

    if config.remote_policy_gate != "required":
        findings.append(
            f"KRITIK: remote_policy_gate={config.remote_policy_gate!r} -- 'required' olmali, "
            "politika kapisi zayiflatilmis olabilir"
        )
        critical = True

    if config.remote_enabled:
        allowed, reason = is_remote_allowed_fn()
        findings.append(
            f"REMOTE_ENABLED=true (politika izni: {allowed}, sebep: {reason}) -- bilinclimi kontrol edin"
        )

    if config.ollama_cpu_verify_enabled and not config.ollama_cpu_verify_strict:
        findings.append(
            "OLLAMA_CPU_VERIFY_STRICT=false -- dogrulanamayan CPU modu bile Ollama'nin "
            "denenmesine izin veriyor (kasitli olabilir, ama not edilmeli)"
        )

    if not findings:
        findings.append("Config sanity: sorun bulunamadi")

    return {"findings": findings, "critical": critical, "checked_at": _now_iso()}


def check_provider_health(router: Any) -> dict[str, Any]:
    """Deliverable #4.3 -- saglayici saglik anlik goruntusu."""
    health = router.healthcheck_all()
    return {
        name: {"healthy": status.healthy, "detail": status.detail}
        for name, status in health.items()
    }


def check_preflight_snapshot(
    verify_fn: Callable[..., Any], config: Any, ollama_healthy: bool
) -> dict[str, Any]:
    """Deliverable #4.2 -- preflight sonuc anlik goruntusu."""
    if not config.ollama_cpu_verify_enabled:
        return {"enabled": False, "note": "OLLAMA_CPU_VERIFY_ENABLED=false, kontrol atlandi"}

    result = verify_fn(
        ollama_healthy=ollama_healthy,
        base_url=config.ollama_host,
        marker_file=config.ollama_cpu_marker_file,
        methods=config.ollama_cpu_verify_methods,
        timeout_ms=config.ollama_cpu_verify_timeout_ms,
    )
    return {
        "enabled": True,
        "status": result.status,
        "reason_code": result.reason_code,
        "evidence": result.evidence,
        "checked_at": result.checked_at,
        "strict_mode": config.ollama_cpu_verify_strict,
    }


def run_synthetic_classify_probe(classify_fn: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    """Deliverable #4.4 -- yikici olmayan, sabit bir classify() probu.

    `classify_fn`, `ollama_nlu.classify`'in kendisi (veya testte bir
    sahtesi) olmali. Yalnizca SINIFLANDIRMA yapar -- hicbir gercek komut
    calistirmaz (bkz. modul docstring'i).
    """
    t0 = time.monotonic()
    result = classify_fn(SMOKE_PROBE_PROMPT, SMOKE_PROBE_KNOWN_INTENTS)
    elapsed_ms = (time.monotonic() - t0) * 1000.0

    got_real_response = bool(result.get("raw"))
    return {
        "prompt": SMOKE_PROBE_PROMPT,
        "result": result,
        "elapsed_ms": round(elapsed_ms, 2),
        "got_real_response": got_real_response,
        "checked_at": _now_iso(),
    }


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with path.open("r", encoding="utf-8") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _tail_lines(path: Path, n: int) -> list[str]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        return [ln.rstrip("\n") for ln in lines[-n:]]
    except OSError:
        return []


def run_daily_smoke(
    *,
    config: Any,
    router: Any,
    verify_fn: Callable[..., Any],
    classify_fn: Callable[..., dict[str, Any]],
    is_remote_allowed_fn: Callable[[], tuple[bool, str]],
    metrics_snapshot_fn: Callable[[], dict[str, Any]],
    audit_log_path: Path,
) -> SmokeResult:
    """Tum smoke kontrollerini calistirir, tek bir SmokeResult doner.

    Hicbir parametre icin gercek I/O bu fonksiyonun DISINDA yapilir --
    caller (ps1 script veya testler) router/config/verify_fn/classify_fn'i
    hazirlar. Bu, testlerin gercek Ollama/dosya sistemi olmadan
    calisabilmesini saglar.
    """
    findings: list[str] = []

    config_sanity = check_config_sanity(config, is_remote_allowed_fn)
    findings.extend(config_sanity["findings"])

    provider_health = check_provider_health(router)
    ollama_healthy = bool(provider_health.get("ollama", {}).get("healthy"))

    preflight_snapshot = check_preflight_snapshot(verify_fn, config, ollama_healthy)

    lines_before = _count_lines(audit_log_path)
    classify_probe = run_synthetic_classify_probe(classify_fn)
    lines_after = _count_lines(audit_log_path)
    audit_appended = lines_after > lines_before
    audit_verification = {
        "lines_before": lines_before,
        "lines_after": lines_after,
        "appended": audit_appended,
        "tail": _tail_lines(audit_log_path, 10),
    }

    metrics_snapshot = metrics_snapshot_fn()

    # --- Karar mantigi -------------------------------------------------
    any_enabled_provider_healthy = any(
        h.get("healthy") for name, h in provider_health.items() if _provider_enabled(config, name)
    )

    if config_sanity["critical"]:
        exit_code = EXIT_ACTION_REQUIRED
        findings.append("EXIT=2: config sanity kritik bulgu icin action required")
    elif not audit_appended:
        exit_code = EXIT_ACTION_REQUIRED
        findings.append("EXIT=2: audit log'a yeni satir eklenmedi -- audit zinciri kirik olabilir")
    elif classify_probe["got_real_response"]:
        exit_code = EXIT_HEALTHY
        findings.append("EXIT=0: sentetik prob gercek bir model yaniti aldi")
    elif not any_enabled_provider_healthy:
        exit_code = EXIT_ACTION_REQUIRED
        findings.append("EXIT=2: hicbir etkin saglayici saglikli degil")
    elif (
        preflight_snapshot.get("enabled")
        and preflight_snapshot.get("status") != "VERIFIED"
        and config.ollama_cpu_verify_strict
    ):
        exit_code = EXIT_DEGRADED
        findings.append(
            "EXIT=1: null-intent, CPU-verify kapisinin STRICT modda kasitli kisitlamasindan "
            "kaynaklaniyor (beklenen davranis, bkz. MODEL_FALLBACK_RUNBOOK.md)"
        )
    else:
        exit_code = EXIT_ACTION_REQUIRED
        findings.append("EXIT=2: null-intent, bilinen/beklenen bir nedenle aciklanamiyor")

    return SmokeResult(
        exit_code=exit_code,
        findings=findings,
        config_sanity=config_sanity,
        preflight_snapshot=preflight_snapshot,
        provider_health_snapshot=provider_health,
        classify_probe=classify_probe,
        audit_verification=audit_verification,
        metrics_snapshot=metrics_snapshot,
    )


def _provider_enabled(config: Any, name: str) -> bool:
    if name == "ollama":
        return bool(config.ollama_enabled)
    if name == "local_alt":
        return bool(config.local_alt_enabled)
    if name == "remote":
        return bool(config.remote_enabled)
    return False


def render_summary_md(result: SmokeResult, *, generated_at: str) -> str:
    lines = [
        "# Daily Gateway Smoke -- Ozet",
        "",
        f"- Uretim zamani (UTC): {generated_at}",
        f"- **Cikis kodu: {result.exit_code}** "
        f"({'HEALTHY' if result.exit_code == 0 else 'DEGRADED' if result.exit_code == 1 else 'ACTION_REQUIRED'})",
        "",
        "## Bulgular",
        "",
    ]
    for f in result.findings:
        lines.append(f"- {f}")
    lines += [
        "",
        "## Sentetik classify() probu",
        "",
        f"- Prompt: `{result.classify_probe.get('prompt')}`",
        f"- Gercek model yaniti alindi mi: {result.classify_probe.get('got_real_response')}",
        f"- Gecikme: {result.classify_probe.get('elapsed_ms')} ms",
        f"- Sonuc: `{result.classify_probe.get('result')}`",
        "",
        "## Saglayici sagligi",
        "",
    ]
    for name, health in result.provider_health_snapshot.items():
        lines.append(f"- `{name}`: healthy={health.get('healthy')} ({health.get('detail') or '-'})")
    lines += [
        "",
        "## Preflight (CPU-verify)",
        "",
        f"```json\n{json.dumps(result.preflight_snapshot, indent=2, ensure_ascii=False)}\n```",
        "",
        "## Audit dogrulamasi",
        "",
        f"- Once: {result.audit_verification.get('lines_before')} satir, "
        f"Sonra: {result.audit_verification.get('lines_after')} satir, "
        f"Eklendi: {result.audit_verification.get('appended')}",
        "",
    ]
    return "\n".join(lines) + "\n"


def write_evidence_bundle(result: SmokeResult, output_dir: Path, generated_at: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "summary.md").write_text(
        render_summary_md(result, generated_at=generated_at), encoding="utf-8"
    )
    (output_dir / "metrics_snapshot.json").write_text(
        json.dumps(result.metrics_snapshot, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (output_dir / "preflight_snapshot.json").write_text(
        json.dumps(result.preflight_snapshot, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (output_dir / "classify_probe.json").write_text(
        json.dumps(result.classify_probe, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    audit_tail_path = output_dir / "audit_tail.jsonl"
    audit_tail_path.write_text(
        "\n".join(result.audit_verification.get("tail", [])) + "\n", encoding="utf-8"
    )
