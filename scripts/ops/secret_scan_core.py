"""Depo genelinde SIR (secret) sizintisi icin deterministik, desen-tabanli
tarama -- saf mantik (dosya/git G/C YOK, `(yol, icerik)` ciftleri alir).

**Bu, GERCEK bir secret-tarama servisi (TruffleHog/GitLeaks vb.) DEGILDIR**
-- bilinen, YAYGIN kimlik bilgisi SEKILLERINI (AWS anahtarlari, ozel
anahtar bloklari, genel `api_key=...` atamalari vb.) arayan, KUCUK ve
ACIK bir desen kumesidir (bkz. `infra/monitoring/governance/secret_scan_patterns_v1.json`).
`evaluate_pilot_promotion.py`'nin `secrets_committed` blocker kontrolu
tarafindan kullanilir.

Bulunan bir esleme ASLA ham/tam degerini raporlamaz -- yalnizca
kisaltilmis bir on-izleme (`matched_snippet`) tutulur, boylece bu
modulun KENDI ciktisi bile yanlislikla bir sirri sizdirmaz."""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_DENYLIST_PATTERNS: dict[str, str] = {
    "aws_access_key_id": r"AKIA[0-9A-Z]{16}",
    "generic_api_key_assignment": r"(?i)(api[_-]?key)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]",
    "generic_secret_assignment": r"(?i)(secret|password|token)\s*[:=]\s*['\"][^'\"\s]{8,}['\"]",
    "private_key_block": r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    "slack_token": r"xox[baprs]-[0-9A-Za-z-]{10,}",
}

DEFAULT_ALLOWLIST_MARKERS: list[str] = [
    "REDACTED",
    "EXAMPLE",
    "example",
    "dummy",
    "mock",
    "fake",
    "placeholder",
    "<default>",
    "xxx",
    "***",
    "YOUR_",
    "CHANGEME",
    "test-token",
    "test_token",
]

DEFAULT_ALLOWLIST_PATHS: list[str] = [".venv/", "node_modules/", ".git/", "archive/"]


@dataclass(frozen=True)
class SecretFinding:
    path: str
    line_number: int
    pattern_name: str
    matched_snippet: str


def _is_allowlisted_line(line: str, allowlist_markers: list[str]) -> bool:
    return any(marker in line for marker in allowlist_markers)


def _is_allowlisted_path(path: str, allowlist_paths: list[str]) -> bool:
    return any(path.startswith(p) for p in allowlist_paths)


def _mask(snippet: str) -> str:
    if len(snippet) <= 6:
        return "***"
    return snippet[:6] + "***"


def scan_files_for_secrets(
    files: list[tuple[str, str]],
    *,
    denylist_patterns: dict[str, str] | None = None,
    allowlist_markers: list[str] | None = None,
    allowlist_paths: list[str] | None = None,
) -> list[SecretFinding]:
    """`files`: `[(goreli_yol, dosya_icerigi), ...]`. Saf -- gercek dosya
    sistemine/git'e ERISMEZ (cagiran taraf dosyalari ONCEDEN okur).
    Allowlist'teki bir isaretci (`REDACTED`/`mock`/`example` vb.) AYNI
    SATIRDA varsa, o satir SESSIZCE atlanir -- bu projenin KENDI stub/
    ornek kodunun (ornegin `emergency_legitimacy_core.py`'deki PILOT
    stub'lari) yanlislikla tetiklenmesini onler."""
    patterns = denylist_patterns or DEFAULT_DENYLIST_PATTERNS
    markers = allowlist_markers or DEFAULT_ALLOWLIST_MARKERS
    allow_paths = allowlist_paths or DEFAULT_ALLOWLIST_PATHS
    compiled = {name: re.compile(pattern) for name, pattern in patterns.items()}

    findings: list[SecretFinding] = []
    for path, content in files:
        if _is_allowlisted_path(path, allow_paths):
            continue
        for i, line in enumerate(content.splitlines(), start=1):
            if _is_allowlisted_line(line, markers):
                continue
            for name, regex in compiled.items():
                match = regex.search(line)
                if match:
                    findings.append(
                        SecretFinding(path=path, line_number=i, pattern_name=name, matched_snippet=_mask(match.group(0)))
                    )
    return findings


def load_scan_config(config: dict) -> tuple[dict[str, str], list[str], list[str]]:
    """`secret_scan_patterns_v1.json`'un icerigini ayristirir. Eksik
    alanlar icin VARSAYILANLARA duser (ASLA exception firlatmaz)."""
    denylist = config.get("denylist_patterns")
    if not isinstance(denylist, dict) or not denylist:
        denylist = DEFAULT_DENYLIST_PATTERNS
    allowlist_markers = config.get("allowlist_markers")
    if not isinstance(allowlist_markers, list):
        allowlist_markers = DEFAULT_ALLOWLIST_MARKERS
    allowlist_paths = config.get("allowlist_paths")
    if not isinstance(allowlist_paths, list):
        allowlist_paths = DEFAULT_ALLOWLIST_PATHS
    return denylist, allowlist_markers, allowlist_paths
