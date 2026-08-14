#!/usr/bin/env python
"""Guvenlik Sertlestirme Sprint-1 icin GERCEK kanit yakalama (PLAN.md
T50/T51/T52, BACKLOG.md B051/B052/B053, SECURITY).

`scripts/ops_suite_demo.py` ile AYNI desen -- `python -m ops_suite.server`'i
GERCEK bir ayri OS alt-surecinde baslatir, `TestClient` KULLANMAZ (o zaten
`tests/test_ops_suite_api.py`'de surec-ici olarak yapiliyor; bu script
TAMAMLAYICI bir GERCEK-surec kanitidir). **Farkli olarak** (ops_suite_demo.py'nin
aksine) `OPS_SUITE_DATA_DIR` ile TAMAMEN izole edilir -- B051'in
`data/identity/token_revocations.jsonl`'u GERCEK/kalici bir dosya
oldugu icin, bu GECICI demo kimliklerinin GERCEK proje verisine
SIZMAMASI icin (T35/T39/T44'un AYNI sinif veri-kirlenmesi hatasindan
ders alinarak, BASTAN onlendi).

Adimlar (hepsi gercek HTTP/dosya G/C, hicbiri fabrike edilmez):
  1) Sunucunun ayaga kalkmasini bekler.
  2) [B051] Owner kendi kimligini dogrular (whoami).
  3) [B051] Owner, bir delegate'in token'ini rotate eder -- YENI token doner.
  4) [B051] ESKI delegate token'i ARTIK GERCEKTEN reddedilir (401,
     reason_code=AUTH_TOKEN_REVOKED) -- rotasyonun KALICI etkisi.
  5) [B051] YENI token GERCEKTEN calisir.
  6) [B051] Delegate, BASKA bir actor'u rotate etmeye CALISIR -- owner-only
     guard GERCEKTEN 403 ile reddeder.
  7) [B052] Ayni actor (owner), identity_admin kategorisinde ust uste
     rotate cagirarak GERCEK hiz sinirina ULASIR -- 429, yapilandirilmis
     govde (reason_code/retry_after_seconds).
  8) [B053] Izole audit log dosyasi GERCEKTEN okunur -- adim 4/6/7'nin
     UCU DE (401/403/429) audit'e YAZILDIGI, standardize
     `auth_decision.{actor,scope,decision,reason_code}` alanlariyla
     dogrulanir. Ayrica HICBIR kaydin ham token DEGERI ICERMEDIGI
     kontrol edilir.

Kanit: `reports/security_hardening_<UTC>/evidence.{json,md}`. Bu sprint
TAMAMEN backend/kutuphane seviyesinde oldugu icin (frontend/tarayici
YOK) -- NOT_COLLECTED listesi BOS (bu, onceki frontend-agirlikli
sprintlerden FARKLI, gercek bir durum, fabrike edilmis bir "tam kapsama"
iddiasi DEGIL)."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
OPS_SUITE_BACKEND_SRC = REPO_ROOT / "apps" / "ops-suite" / "backend" / "src"
DEFAULT_PORT = 8430

DEMO_OWNER_TOKEN_ENV_VAR = "OPS_SUITE_SEC_DEMO_OWNER_TOKEN"
DEMO_DELEGATE_TOKEN_ENV_VAR = "OPS_SUITE_SEC_DEMO_DELEGATE_TOKEN"


def _wait_for_server(base_url: str, *, timeout_sec: float = 15.0) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            urllib.request.urlopen(base_url + "/api/agents", timeout=1.0)
            return True
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.2)
    return False


def _http_get_expect(url: str, *, headers: dict[str, str] | None = None) -> tuple[int, Any]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        body = json.loads(raw.decode("utf-8")) if raw else {}
        return exc.code, body


def _http_post_expect(url: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None) -> tuple[int, Any]:
    data = json.dumps(payload).encode("utf-8")
    all_headers = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=all_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        body = json.loads(raw.decode("utf-8")) if raw else {}
        return exc.code, body


def _write_demo_identity_config(out_dir: Path) -> Path:
    """B051 -- bu kosum icin GECICI, GERCEK-OLMAYAN bir owner+delegate
    kimlik config'i. Token DEGERLERI dosyaya YAZILMAZ (ADR-010/B044 ile
    AYNI ilke)."""
    config = {
        "schema_version": 1,
        "owner": {
            "actor_id": "sec_demo_owner",
            "display_name": "Sec-Sprint Demo Owner (yalniz bu kosum icin -- GERCEK bir kisi DEGIL)",
            "token_env_var": DEMO_OWNER_TOKEN_ENV_VAR,
        },
        "delegates": [
            {
                "actor_id": "sec_demo_delegate",
                "display_name": "Sec-Sprint Demo Delegate (yalniz bu kosum icin -- GERCEK bir kisi DEGIL)",
                "token_env_var": DEMO_DELEGATE_TOKEN_ENV_VAR,
                "scopes": ["approve:low", "reject"],
            }
        ],
    }
    path = out_dir / "sec_demo_identities.json"
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _subprocess_env(port: int, *, identity_config_path: Path, data_dir: Path, owner_token: str, delegate_token: str) -> dict[str, str]:
    env = dict(os.environ)
    pythonpath_entries = [
        str(OPS_SUITE_BACKEND_SRC),
        str(REPO_ROOT / "apps" / "orchestrator" / "src"),
        str(REPO_ROOT / "services" / "tr-en-bridge" / "src"),
        str(REPO_ROOT / "services" / "model-gateway" / "src"),
        str(REPO_ROOT / "tools" / "cli-runner" / "src"),
        str(REPO_ROOT / "tools"),
    ]
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries + ([existing] if existing else []))
    env["OPS_SUITE_PORT"] = str(port)
    env["OPS_SUITE_IDENTITY_CONFIG_PATH"] = str(identity_config_path)
    env["OPS_SUITE_DATA_DIR"] = str(data_dir)
    env[DEMO_OWNER_TOKEN_ENV_VAR] = owner_token
    env[DEMO_DELEGATE_TOKEN_ENV_VAR] = delegate_token
    return env


def run_demo(*, port: int = DEFAULT_PORT, out_dir: Path) -> dict[str, Any]:
    base_url = f"http://127.0.0.1:{port}"
    evidence: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "steps": [],
        "overall_ok": True,
        "not_collected": [],  # bu sprint tamamen backend -- gercek, fabrike edilmemis bos liste
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    identity_config_path = _write_demo_identity_config(out_dir)
    isolated_data_dir = Path(tempfile.mkdtemp(prefix="ops-suite-sec-demo-data-"))
    owner_token = secrets.token_urlsafe(24)
    delegate_token = secrets.token_urlsafe(24)
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    delegate_headers = {"Authorization": f"Bearer {delegate_token}"}

    proc = subprocess.Popen(
        [sys.executable, "-m", "ops_suite.server"],
        cwd=str(REPO_ROOT),
        env=_subprocess_env(
            port, identity_config_path=identity_config_path, data_dir=isolated_data_dir,
            owner_token=owner_token, delegate_token=delegate_token,
        ),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
    )

    def _record(step: str, ok: bool, **details: Any) -> None:
        evidence["steps"].append({"step": step, "ok": ok, **details})
        if not ok:
            evidence["overall_ok"] = False

    try:
        server_up = _wait_for_server(base_url)
        _record("server_startup", server_up, base_url=base_url)
        if not server_up:
            return evidence

        # --- B051: rotate happy path + eski token GERCEKTEN reddedilir ---

        status, whoami_owner = _http_get_expect(base_url + "/api/whoami", headers=owner_headers)
        _record("owner_whoami", status == 200 and whoami_owner.get("authority_source") == "owner", status_code=status, response=whoami_owner)

        status, rotate_response = _http_post_expect(base_url + "/api/identity/sec_demo_delegate/rotate", {}, headers=owner_headers)
        new_delegate_token = rotate_response.get("new_token")
        _record(
            "rotate_delegate_token", status == 200 and bool(new_delegate_token) and new_delegate_token != delegate_token,
            status_code=status, response={"actor_id": rotate_response.get("actor_id")},  # ham token DEGERI evidence'a YAZILMAZ
        )

        status, old_token_response = _http_get_expect(base_url + "/api/whoami", headers=delegate_headers)
        _record(
            "old_delegate_token_rejected_after_rotation", status == 401,
            status_code=status, response=old_token_response,
        )

        status, new_token_response = _http_get_expect(
            base_url + "/api/whoami", headers={"Authorization": f"Bearer {new_delegate_token}"}
        )
        _record(
            "new_delegate_token_works", status == 200 and new_token_response.get("actor_id") == "sec_demo_delegate",
            status_code=status, response=new_token_response,
        )

        # --- B051: delegate owner-only rotate/revoke denemesi -- 403 ---

        status, denied_response = _http_post_expect(
            base_url + "/api/identity/sec_demo_owner/rotate", {},
            headers={"Authorization": f"Bearer {new_delegate_token}"},
        )
        _record("delegate_cannot_rotate_owner_only_guard", status == 403, status_code=status, response=denied_response)

        # --- B052: identity_admin kategorisinde GERCEK hiz sinirina ulasma ---
        # Varsayilan esik 20/60sn -- 21 ardisik rotate cagrisi (ayni actor:
        # owner) sinira ULASIR. Hedef actor'u DEGISTIRMEK onemli degil --
        # rate limit anahtari CAGIRANIN (owner) actor_id'sine gore.

        rate_limit_hit = False
        last_status = None
        last_body = None
        for _ in range(21):
            last_status, last_body = _http_post_expect(base_url + "/api/identity/sec_demo_delegate/rotate", {}, headers=owner_headers)
            if last_status == 429:
                rate_limit_hit = True
                break
        _record(
            "identity_admin_rate_limit_triggered", rate_limit_hit and isinstance(last_body, dict) and last_body.get("detail", {}).get("reason_code") == "RATE_LIMITED",
            status_code=last_status, response=last_body,
        )

        # --- B053: izole audit log -- 401/403/429 + basari, standardize alanlarla ---

        audit_log_path = isolated_data_dir / "audit" / "audit.log.jsonl"
        audit_records = []
        if audit_log_path.exists():
            for line in audit_log_path.read_text(encoding="utf-8").splitlines():
                try:
                    audit_records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        reason_codes_seen = {
            r["details"]["auth_decision"]["reason_code"]
            for r in audit_records
            if isinstance(r.get("details"), dict) and isinstance(r["details"].get("auth_decision"), dict)
        }
        expected_reason_codes = {"AUTH_TOKEN_REVOKED", "AUTHZ_OWNER_ONLY", "RATE_LIMITED", "OK"}
        _record(
            "audit_log_contains_all_standardized_reason_codes",
            expected_reason_codes.issubset(reason_codes_seen),
            expected=sorted(expected_reason_codes), seen=sorted(reason_codes_seen), total_records=len(audit_records),
        )

        raw_audit_text = audit_log_path.read_text(encoding="utf-8") if audit_log_path.exists() else ""
        no_raw_tokens_leaked = all(
            tok not in raw_audit_text for tok in (owner_token, delegate_token, new_delegate_token)
        )
        _record("audit_log_contains_no_raw_token_values", no_raw_tokens_leaked)

        revocation_log_path = isolated_data_dir / "identity" / "token_revocations.jsonl"
        revocation_text = revocation_log_path.read_text(encoding="utf-8") if revocation_log_path.exists() else ""
        _record(
            "revocation_log_persists_hash_not_raw_token",
            revocation_log_path.exists() and delegate_token not in revocation_text and '"token_hash"' in revocation_text,
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(isolated_data_dir, ignore_errors=True)

    return evidence


def _write_evidence(evidence: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "evidence.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# Güvenlik Sertleştirme Sprint-1 -- Gerçek Kanıt (B051/B052/B053, PLAN.md T50/T51/T52)",
        "",
        f"Üretildi (UTC): {evidence['generated_at']}",
        f"Genel sonuç: **{'PASS' if evidence['overall_ok'] else 'FAIL'}**",
        "",
        "## NOT_COLLECTED",
        "",
        "Bu sprint tamamen backend/kütüphane seviyesindedir (frontend/tarayıcı yok) -- toplanamayan bir kanıt YOK." if not evidence["not_collected"] else "",
        "",
        "## Adımlar",
        "",
    ]
    for step in evidence["steps"]:
        lines.append(f"### {step['step']} -- {'OK' if step['ok'] else 'FAIL'}")
        lines.append("")
        detail = {k: v for k, v in step.items() if k not in ("step", "ok")}
        lines.append("```json")
        lines.append(json.dumps(detail, indent=2, ensure_ascii=False))
        lines.append("```")
        lines.append("")
    (out_dir / "evidence.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    out_dir = REPO_ROOT / "reports" / f"security_hardening_{ts}"
    evidence = run_demo(out_dir=out_dir)
    _write_evidence(evidence, out_dir)

    for step in evidence["steps"]:
        print(f"[{'OK' if step['ok'] else 'FAIL'}] {step['step']}")
    print(f"genel_sonuc={'PASS' if evidence['overall_ok'] else 'FAIL'}")
    print(f"evidence_dir={out_dir}")
    return 0 if evidence["overall_ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
