"""Model gateway olay (incident) kanit paketleyicisi -- cekirdek yardimci.

`scripts/ops/package_gateway_incident.ps1` bu modulu cagirir. Ayri
tutulmasinin nedeni: secret-masking gibi guvenlik-kritik mantigin
gercek dosya sistemi/git yan etkilerinden ayri, testable birimler
halinde kalabilmesi.

Kapsam (bkz. gorev tanimi "Incident evidence bundler"):
  - ilgili loglar
  - audit dilimi (zaman penceresine gore)
  - router/preflight anlik goruntuleri
  - config parmak izi (SECRETS MASKELI)
  - git SHA + son bilinen-iyi tag'den bu yana degisen dosyalar
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

MASK_VALUE = "***MASKED***"

# Bir anahtar adi bu desenlerden birini (case-insensitive) iceriyorsa
# degeri maskelenir. Genis tutuldu (kasitli, yalniz-hata guvenli taraf) --
# yanlislikla gercek olmayan bir "secret" maskelense bile zarar yok, ama
# gercek bir secret maskelenMEmesi kabul edilemez.
SECRET_KEY_PATTERNS = (
    re.compile(r"key", re.IGNORECASE),
    re.compile(r"token", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"password", re.IGNORECASE),
    re.compile(r"credential", re.IGNORECASE),
    re.compile(r"auth", re.IGNORECASE),
)


def _looks_like_secret_key(key: str) -> bool:
    return any(p.search(key) for p in SECRET_KEY_PATTERNS)


def mask_secrets(data: dict[str, Any]) -> dict[str, Any]:
    """Anahtar adi secret-benzeri bir desenle eslesen her deger maskelenir.

    Deger `None`/bos ise oldugu gibi birakilir (maskelenecek bir sey yok,
    ve "REMOTE_API_KEY tanimli degil" gibi tanilamalarin degeri korunur).
    Iç ice sozlukler icin recursive calisir.
    """
    masked: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            masked[key] = mask_secrets(value)
        elif _looks_like_secret_key(key) and value:
            masked[key] = MASK_VALUE
        else:
            masked[key] = value
    return masked


def build_config_fingerprint(config_dict: dict[str, Any], env_dict: dict[str, str]) -> dict[str, Any]:
    """Config alanlari + ilgili ortam degiskenlerinin maskeli bir goruntusu."""
    return {
        "config": mask_secrets(config_dict),
        "env": mask_secrets(env_dict),
    }


def collect_audit_slice(audit_log_path: Path, start_iso: str, end_iso: str) -> list[dict[str, Any]]:
    """`data/audit/audit.log.jsonl`'den [start_iso, end_iso] araligindaki
    kayitlari filtreler. Bozuk/ayristirilamayan satirlar sessizce atlanir
    (kanit paketleme, uretim akisini asla bozmamali)."""
    if not audit_log_path.exists():
        return []

    sliced: list[dict[str, Any]] = []
    with audit_log_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = record.get("timestamp", "")
            if start_iso <= ts <= end_iso:
                sliced.append(record)
    return sliced


def get_git_sha(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else "UNKNOWN"
    except (subprocess.TimeoutExpired, OSError):
        return "UNKNOWN"


def get_changed_files_since(repo_root: Path, ref: str) -> list[str]:
    """`ref` (tag/sha) ile HEAD arasinda degisen dosyalar. `ref` gecersiz/
    bulunamazsa bos liste doner (hata firlatmaz -- kanit paketleme en
    kotu durumda eksik bilgi icerir, asla cokmez)."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{ref}..HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return []
        return [ln for ln in result.stdout.splitlines() if ln.strip()]
    except (subprocess.TimeoutExpired, OSError):
        return []


def render_incident_summary(
    *,
    generated_at: str,
    window_start: str,
    window_end: str,
    git_sha: str,
    changed_files: list[str],
    audit_slice: list[dict[str, Any]],
    provider_health: dict[str, Any],
    preflight_snapshot: dict[str, Any],
) -> str:
    lines = [
        "# Model Gateway Incident Evidence Bundle",
        "",
        f"- Uretim zamani (UTC): {generated_at}",
        f"- Zaman penceresi: {window_start} .. {window_end}",
        f"- Git SHA: `{git_sha}`",
        f"- Son bilinen-iyi referanstan bu yana degisen dosya sayisi: {len(changed_files)}",
        "",
        "## Audit dilimi ozeti",
        "",
        f"- Kayit sayisi: {len(audit_slice)}",
    ]
    statuses: dict[str, int] = {}
    for r in audit_slice:
        statuses[r.get("status", "?")] = statuses.get(r.get("status", "?"), 0) + 1
    for status, count in sorted(statuses.items()):
        lines.append(f"  - {status}: {count}")

    lines += [
        "",
        "## Saglayici sagligi (paketleme aninda)",
        "",
    ]
    for name, health in provider_health.items():
        lines.append(f"- `{name}`: {health}")

    lines += [
        "",
        "## Preflight anlik goruntusu (paketleme aninda)",
        "",
        f"```json\n{json.dumps(preflight_snapshot, indent=2, ensure_ascii=False)}\n```",
        "",
        "## Degisen dosyalar",
        "",
    ]
    for f in changed_files[:200]:
        lines.append(f"- `{f}`")
    if len(changed_files) > 200:
        lines.append(f"- ... ve {len(changed_files) - 200} dosya daha")

    return "\n".join(lines) + "\n"


def write_incident_bundle(
    output_dir: Path,
    *,
    summary_md: str,
    audit_slice: list[dict[str, Any]],
    config_fingerprint: dict[str, Any],
    provider_health: dict[str, Any],
    preflight_snapshot: dict[str, Any],
    changed_files: list[str],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "summary.md").write_text(summary_md, encoding="utf-8")
    (output_dir / "audit_slice.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in audit_slice) + ("\n" if audit_slice else ""),
        encoding="utf-8",
    )
    (output_dir / "config_fingerprint.json").write_text(
        json.dumps(config_fingerprint, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (output_dir / "provider_health_snapshot.json").write_text(
        json.dumps(provider_health, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (output_dir / "preflight_snapshot.json").write_text(
        json.dumps(preflight_snapshot, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (output_dir / "changed_files.txt").write_text("\n".join(changed_files) + "\n", encoding="utf-8")
