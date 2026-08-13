"""Ollama CPU-only calisma zamani dogrulamasi -- kanit temelli, sihirli degil.

**Onemli durustluk notu (bu modulun tum tasarim felsefesi):** Bu modul,
calisan bir `ollama serve` surecinin GERCEKTEN `OLLAMA_VULKAN=false` ile
baslatildigini KESIN OLARAK kanitlayamaz. Python'dan/PowerShell'den harici,
zaten calisan bir surecin ortam degiskenlerini okumenin genel, guvenilir,
ayricalik gerektirmeyen bir yolu yok (bkz. docs/ops/MODEL_FALLBACK_RUNBOOK.md
"Bilinen sinirlamalar"). Bu yuzden burada KANIT TOPLANIR, KESINLIK IDDIA
EDILMEZ:

  - POZITIF kanit: operatorun kendi elleriyle olusturdugu bir marker dosyasi
    ("ben CPU-only modunu dogruladim" beyani) -- otomatik degil, operator
    onayina dayanir.
  - NEGATIF kanit: Windows Event Viewer'da yakin zamanli bir `llama-server.exe`
    / `0xc0000005` cokus kaydi (bkz. B036 triage,
    reports/runtime_incident_20260813T004855Z/event_viewer_crash_entries.txt)
    VEYA `/api/ps` uzerinden VRAM kullanan yuklu bir model -- ikisi de
    Vulkan/GPU'nun fiilen kullanildigina dair somut kanit.
  - Kanit YOKSA (ne pozitif ne negatif): SIGNAL_NOT_AVAILABLE / UNKNOWN
    donulur -- "muhtemelen iyidir" varsayilmaz.
"""

from __future__ import annotations

import json
import logging
import platform
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("model_gateway.runtime_verify")

STATUS_VERIFIED = "VERIFIED"
STATUS_UNVERIFIED = "UNVERIFIED"
STATUS_UNKNOWN = "UNKNOWN"

REASON_CPU_MODE_VERIFIED = "CPU_MODE_VERIFIED"
REASON_OLLAMA_UNREACHABLE = "OLLAMA_UNREACHABLE"
REASON_SIGNAL_NOT_AVAILABLE = "SIGNAL_NOT_AVAILABLE"
REASON_ENV_MISMATCH = "ENV_MISMATCH"
REASON_CHECK_ERROR = "CHECK_ERROR"

# Operator marker dosyasinin "taze" sayilacagi azami yas -- bu bir gercek
# calisma zamani dogrulamasi degil, bir operator beyani oldugu icin makul
# derecede uzun tutuldu (24 saat). Ayri bir zorunlu env degiskeni EKLEMEDEN
# (config yuzeyini task'in istedigi listeyle sinirli tutmak icin) kod-ici
# sabit olarak tanimlandi.
MARKER_MAX_AGE_SECONDS = 24 * 3600

# Event Viewer sorgusunun geriye donuk bakacagi pencere -- cache TTL'den
# (varsayilan 60s) daha genis, cunku bir onceki cokus hala gecerli bir
# uyari sinyalidir.
CRASH_LOOKBACK_MINUTES = 30

CRASH_PROVIDER_NAME = "Application Error"
CRASH_PROCESS_MARKER = "llama-server.exe"
CRASH_SIGNATURE = "0xc0000005"


@dataclass
class VerificationResult:
    status: str
    reason_code: str
    evidence: dict[str, Any] = field(default_factory=dict)
    checked_at: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_marker_file(marker_path: str) -> dict[str, Any]:
    """Operator marker dosyasi kontrolu -- POZITIF kanit kaynagi.

    Donen sozlukte `present` (bool) ve `fresh` (bool | None) var. Dosya
    yoksa present=False. Varsa ama okunamiyorsa/yasi belirlenemiyorsa
    fresh=None (sinyal yok, hata degil).
    """
    path = Path(marker_path)
    if not path.exists():
        return {"present": False, "fresh": False, "path": str(path)}

    try:
        age_seconds = time.time() - path.stat().st_mtime
    except OSError as exc:
        return {"present": True, "fresh": None, "path": str(path), "error": str(exc)}

    fresh = age_seconds <= MARKER_MAX_AGE_SECONDS
    return {"present": True, "fresh": fresh, "age_seconds": round(age_seconds, 1), "path": str(path)}


def _check_recent_crash_evidence(timeout_seconds: float) -> dict[str, Any]:
    """Windows Event Viewer'da yakin zamanli bilinen B036 cokus imzasini arar
    -- NEGATIF kanit kaynagi. Platform-safe: Windows disinda calismaz."""
    if platform.system() != "Windows":
        return {"available": False, "reason": "yalnizca Windows'ta calisir"}

    ps_script = (
        "$events = Get-WinEvent -FilterHashtable @{"
        f"LogName='Application'; ProviderName='{CRASH_PROVIDER_NAME}'; "
        f"StartTime=(Get-Date).AddMinutes(-{CRASH_LOOKBACK_MINUTES})"
        "} -ErrorAction SilentlyContinue; "
        f"$matches = $events | Where-Object {{ $_.Message -match [regex]::Escape('{CRASH_PROCESS_MARKER}') -and $_.Message -match [regex]::Escape('{CRASH_SIGNATURE}') }}; "
        "($matches | Measure-Object).Count"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"available": False, "reason": f"powershell calisitirilamadi: {exc}"}

    if result.returncode != 0:
        return {"available": False, "reason": f"powershell exit code {result.returncode}"}

    output = (result.stdout or "").strip()
    try:
        count = int(output) if output else 0
    except ValueError:
        return {"available": False, "reason": f"beklenmeyen powershell ciktisi: {output!r}"}

    return {"available": True, "crash_count": count, "lookback_minutes": CRASH_LOOKBACK_MINUTES}


def _check_http_diagnostic(base_url: str, timeout_seconds: float) -> dict[str, Any]:
    """`/api/ps` (yuklu modeller) uzerinden zayif bir sinyal toplar.

    Hicbir model yuklu degilse bilgi yok (bu normal ve beklenen bir durum --
    preflight anda henuz hicbir inference yapilmamis olabilir). Bir model
    yuklu VE `size_vram > 0` ise bu GPU/Vulkan kullanildiginin somut
    kanitidir (NEGATIF sinyal).
    """
    try:
        req = urllib.request.Request(f"{base_url.rstrip('/')}/api/ps", method="GET")
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        return {"available": False, "reason": str(exc)}

    models = body.get("models", []) if isinstance(body, dict) else []
    if not models:
        return {"available": True, "loaded_models": 0, "any_vram_used": False}

    vram_values = [m.get("size_vram", 0) for m in models if isinstance(m, dict)]
    return {
        "available": True,
        "loaded_models": len(models),
        "any_vram_used": any(isinstance(v, (int, float)) and v > 0 for v in vram_values),
    }


def verify_ollama_cpu_mode(
    *,
    ollama_healthy: bool,
    base_url: str,
    marker_file: str,
    methods: tuple[str, ...],
    timeout_ms: int,
) -> VerificationResult:
    """Ollama'nin CPU-only modda calistigina dair kanit toplar ve
    VERIFIED/UNVERIFIED/UNKNOWN sonucuna varir. Hicbir zaman exception
    firlatmaz -- beklenmeyen bir hata CHECK_ERROR/UNKNOWN'a duser.

    `ollama_healthy`, caller (router) tarafindan zaten yapilmis olan
    health check sonucudur -- burada tekrarlanmaz (gereksiz agirlik/cifte
    network cagrisini onlemek icin).
    """
    checked_at = _now_iso()
    deadline = time.monotonic() + max(timeout_ms, 1) / 1000.0

    try:
        if not ollama_healthy:
            return VerificationResult(
                status=STATUS_UNVERIFIED,
                reason_code=REASON_OLLAMA_UNREACHABLE,
                evidence={"ollama_healthy": False},
                checked_at=checked_at,
            )

        evidence: dict[str, Any] = {}
        crash_found = False
        vram_used = False
        marker_fresh = False

        # "marker" once calistirilir -- neredeyse maliyetsiz bir dosya
        # sistemi kontrolu (tek bir stat() cagrisi), zaman butcesinden
        # pay almasi/diger yavas problar tarafindan ac birakilmasi
        # (starvation) anlamsiz olurdu. Bu, en guvenilir pozitif kanit
        # kaynagi oldugu icin ozellikle onemli.
        if "marker" in methods:
            result = _check_marker_file(marker_file)
            evidence["marker"] = result
            marker_fresh = bool(result.get("fresh"))

        for method in methods:
            if method == "marker":
                continue  # zaten yukarida calistirildi

            remaining = deadline - time.monotonic()
            if remaining <= 0.05:
                evidence[method] = {"skipped": True, "reason": "zaman butcesi doldu"}
                continue

            if method == "process":
                result = _check_recent_crash_evidence(remaining)
                evidence["process"] = result
                if result.get("available") and result.get("crash_count", 0) > 0:
                    crash_found = True
            elif method == "http":
                result = _check_http_diagnostic(base_url, remaining)
                evidence["http"] = result
                if result.get("available") and result.get("any_vram_used"):
                    vram_used = True
            else:
                evidence[method] = {"skipped": True, "reason": "bilinmeyen yontem"}

        if crash_found or vram_used:
            return VerificationResult(
                status=STATUS_UNVERIFIED,
                reason_code=REASON_ENV_MISMATCH,
                evidence=evidence,
                checked_at=checked_at,
            )

        if marker_fresh:
            return VerificationResult(
                status=STATUS_VERIFIED,
                reason_code=REASON_CPU_MODE_VERIFIED,
                evidence=evidence,
                checked_at=checked_at,
            )

        return VerificationResult(
            status=STATUS_UNKNOWN,
            reason_code=REASON_SIGNAL_NOT_AVAILABLE,
            evidence=evidence,
            checked_at=checked_at,
        )
    except Exception as exc:  # beklenmeyen herhangi bir hata -- guvenli tarafa dus
        logger.warning("CPU-mode dogrulamasi beklenmedik hatayla basarisiz: %s", exc)
        return VerificationResult(
            status=STATUS_UNKNOWN,
            reason_code=REASON_CHECK_ERROR,
            evidence={"error": str(exc)},
            checked_at=checked_at,
        )
