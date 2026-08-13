#!/usr/bin/env python
"""Ops Suite E2E demo -- PLAN.md T27/T28 (S5, BACKLOG.md B044 SECURITY P0).

`python -m ops_suite.server`'i GERCEK bir ayri OS alt-surecinde
(subprocess) baslatir -- `TestClient` KULLANMAZ, boylece gercek
entrypoint'in GERCEKTEN calistigini kanitlar (bkz. `tests/test_ops_suite_api.py`/
`test_ops_suite_ws.py` zaten `TestClient` ile surec-ici dogrulama yapiyor;
bu script TAMAMLAYICI bir GERCEK-surec kanitidir).

Adimlar (hepsi gercek HTTP/dosya G/C, hicbiri fabrike edilmez):
  1) Sunucunun ayaga kalkmasini bekler (`/api/agents` polling).
  2) Mocked-TR "sesli komut" -- echo (dusuk risk, otomatik izinli).
  3) Mocked-TR "sesli komut" -- dosya silme (irreversible, onay bekler).
  4) Onay kuyrugunda GERCEKTEN gorunup gorunmedigini kontrol eder.
  5) [B044] Token OLMADAN onay denemesi -- GERCEKTEN 401 ile reddedilir.
  6) [B044] Bir delege token'i ILE (config'inde `approve:irreversible`
     OLSA BILE) onay denemesi -- owner-root-guard GERCEKTEN 403 ile
     reddeder (defense-in-depth, bkz. `ops_suite/identity.py`).
  7) Sahibi (owner) token'i ile onaylar -- GERCEKTEN 200 doner.
  8) Onaylar, kuyruktan GERCEKTEN silinip silinmedigini kontrol eder.
  9) `data/audit/audit.log.jsonl`'de eslesen, YENI kimlik alanlarini
     (`actor_id`/`auth_method`/`authority_source`/`decision_scope`)
     ICEREN bir kayit ARANIR.
  10) `/api/agents` anlik goruntusu alinir.

Kanit: `reports/ops_suite_demo_<UTC>/evidence.json`+`.md`. Ses/GSM/kamera/
tarayici gibi bu ortamda OLMAYAN donanimlar ACIKCA `NOT_COLLECTED` olarak
isaretlenir -- hicbir sonuc FABRIKE EDILMEZ (gorev kisiti).

**Demo kimlikleri (owner/delegate) GERCEK KISILER DEGILDIR** -- yalnizca bu
kosum icin uretilen, rastgele token'li, gecici bir config dosyasina
yazilir (`<evidence_dir>/demo_identities.json`, TOKEN DEGERLERI DOSYADA
TUTULMAZ -- yalnizca `token_env_var` referansi; gercek token'lar sadece
alt-surecin ortam degiskenlerinde yasar, evidence'a hicbir zaman yazilmaz)."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
OPS_SUITE_BACKEND_SRC = REPO_ROOT / "apps" / "ops-suite" / "backend" / "src"
DEFAULT_PORT = 8420
AUDIT_LOG_PATH = REPO_ROOT / "data" / "audit" / "audit.log.jsonl"

DEMO_OWNER_TOKEN_ENV_VAR = "OPS_SUITE_DEMO_OWNER_TOKEN"
DEMO_DELEGATE_TOKEN_ENV_VAR = "OPS_SUITE_DEMO_DELEGATE_TOKEN"

NOT_COLLECTED = [
    {"item": "real_browser_rendering", "reason": "bu ortamda tarayici-otomasyon araci YOK -- bkz. docs/BACKLOG.md B039"},
    {"item": "real_microphone_speaker_audio", "reason": "ses donanimi (mikrofon/hoparlor/gercek TTS) bu ortamda YOK -- bkz. docs/BACKLOG.md B040"},
    {"item": "real_gsm_sim_call_flow", "reason": "GSM modem/SIM donanimi YOK, services/gsm-gateway hala bos -- bkz. docs/BACKLOG.md B040/B043"},
    {"item": "real_camera_gesture_input", "reason": "kamera donanimi YOK, services/gesture-vision hala bos -- bkz. docs/BACKLOG.md B040"},
]


def _wait_for_server(base_url: str, *, timeout_sec: float = 15.0) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            urllib.request.urlopen(base_url + "/api/agents", timeout=1.0)
            return True
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.2)
    return False


def _http_get(url: str, *, headers: dict[str, str] | None = None) -> Any:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_post(url: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None) -> Any:
    data = json.dumps(payload).encode("utf-8")
    all_headers = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=all_headers, method="POST")
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_post_expect_error(url: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None) -> tuple[int, Any]:
    """`_http_post` gibi ama BASARISIZLIK BEKLENEN (4xx) cagrilar icin --
    `HTTPError`'i YAKALAR ve `(status_code, response_body)` doner (raise
    ETMEZ). Beklenmedik bir basari (2xx) veya ag hatasi ise CAGIRANA
    firlatilir -- yalnizca 4xx/5xx'i "beklenen sonuc" olarak ele alir."""
    data = json.dumps(payload).encode("utf-8")
    all_headers = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=all_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return resp.status, body
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            body = {"raw": raw.decode("utf-8", errors="replace")}
        return exc.code, body


def _write_demo_identity_config(out_dir: Path) -> Path:
    """B044 -- bu E2E kosusu icin GECICI, GERCEK-OLMAYAN bir owner+delegate
    kimlik config'i yazar. Token DEGERLERI dosyaya YAZILMAZ -- yalnizca
    hangi ortam degiskeninde bulunacaklari (`token_env_var`)."""
    config = {
        "schema_version": 1,
        "owner": {
            "actor_id": "ops_suite_demo_owner",
            "display_name": "Demo Owner (yalniz bu E2E kosusu icin -- GERCEK bir kisi DEGIL)",
            "token_env_var": DEMO_OWNER_TOKEN_ENV_VAR,
        },
        "delegates": [
            {
                "actor_id": "ops_suite_demo_delegate",
                "display_name": "Demo Delegate (yalniz bu E2E kosusu icin -- GERCEK bir kisi DEGIL)",
                "token_env_var": DEMO_DELEGATE_TOKEN_ENV_VAR,
                # BILEREK approve:irreversible DAHIL -- root-guard'in bunu
                # config'ten BAGIMSIZ reddettigini kanitlamak icin (bkz. adim 6).
                "scopes": ["approve:low", "approve:medium", "approve:high", "approve:irreversible", "reject"],
            }
        ],
    }
    path = out_dir / "demo_identities.json"
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _subprocess_env(port: int, *, identity_config_path: Path, owner_token: str, delegate_token: str) -> dict[str, str]:
    env = dict(os.environ)
    # NOT: bu liste, pyproject.toml'daki [tool.pytest.ini_options].pythonpath
    # ile AYNI olmalidir -- ops_suite gercek bir subprocess olarak calistigi
    # icin pytest'in kendi pythonpath enjeksiyonundan YARARLANAMAZ, aynisini
    # ELLE tekrarlar.
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
        "not_collected": NOT_COLLECTED,
    }

    identity_config_path = _write_demo_identity_config(out_dir)
    owner_token = secrets.token_urlsafe(24)
    delegate_token = secrets.token_urlsafe(24)
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    delegate_headers = {"Authorization": f"Bearer {delegate_token}"}

    proc = subprocess.Popen(
        [sys.executable, "-m", "ops_suite.server"],
        cwd=str(REPO_ROOT),
        env=_subprocess_env(port, identity_config_path=identity_config_path, owner_token=owner_token, delegate_token=delegate_token),
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

        echo_response = _http_post(base_url + "/api/voice/command", {"input_tr": "Ezo, echo ile 'merhaba' yaz"})
        _record(
            "voice_command_echo", echo_response.get("result_en", {}).get("status") == "ok",
            request=echo_response,
        )

        delete_response = _http_post(base_url + "/api/voice/command", {"input_tr": "Ezo, tüm dosyaları sil"})
        request_id = delete_response.get("request_id")
        _record(
            "voice_command_irreversible",
            delete_response.get("result_en", {}).get("status") == "WAITING_APPROVAL",
            response=delete_response,
        )

        pending = _http_get(base_url + "/api/approvals?status=pending")
        found_pending = any(p.get("request_id") == request_id for p in pending)
        _record("approvals_pending_check", found_pending, pending=pending)

        # --- B044 (SECURITY P0) -- kimlik dogrulama/yetkilendirme, GERCEK HTTP ---

        status_no_token, body_no_token = _http_post_expect_error(
            base_url + f"/api/approvals/{request_id}/approve", {}
        )
        _record(
            "approve_rejected_without_token", status_no_token == 401,
            status_code=status_no_token, response=body_no_token,
        )

        status_delegate, body_delegate = _http_post_expect_error(
            base_url + f"/api/approvals/{request_id}/approve", {}, headers=delegate_headers
        )
        _record(
            "approve_rejected_delegate_root_guard", status_delegate == 403,
            status_code=status_delegate, response=body_delegate,
            note="delegate config'inde approve:irreversible OLMASINA RAGMEN root-guard reddetti",
        )

        # Reddedilen denemeler kararı GERCEKLESTIRMEMIS olmali -- hala bekliyor.
        pending_after_denied_attempts = _http_get(base_url + "/api/approvals?status=pending")
        still_pending = any(p.get("request_id") == request_id for p in pending_after_denied_attempts)
        _record("approvals_still_pending_after_denied_attempts", still_pending, pending=pending_after_denied_attempts)

        approve_response = _http_post(
            base_url + f"/api/approvals/{request_id}/approve",
            {"note": "E2E demo onayi (scripts/ops_suite_demo.py)"},
            headers=owner_headers,
        )
        _record(
            "approve_decision",
            approve_response.get("decision") == "approved" and approve_response.get("authority_source") == "owner",
            response=approve_response,
        )

        pending_after = _http_get(base_url + "/api/approvals?status=pending")
        cleared = not any(p.get("request_id") == request_id for p in pending_after)
        _record("approvals_cleared_check", cleared, pending_after=pending_after)

        audit_lines = AUDIT_LOG_PATH.read_text(encoding="utf-8").splitlines() if AUDIT_LOG_PATH.exists() else []
        matching_audit = []
        for line in audit_lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("request_id") == request_id:
                matching_audit.append(record)
        decided_record = next((r for r in matching_audit if r.get("status") == "APPROVED"), {})
        decided_details = decided_record.get("details", {})
        has_identity_fields = all(
            field in decided_details for field in ("actor_id", "auth_method", "authority_source", "decision_scope")
        )
        _record(
            "audit_log_check", len(matching_audit) >= 2 and has_identity_fields,
            matching_records=matching_audit,
        )

        agents = _http_get(base_url + "/api/agents")
        _record("agents_snapshot", isinstance(agents, list) and len(agents) > 0, agents=agents)

    finally:
        proc.terminate()
        try:
            stdout, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, _ = proc.communicate()
        evidence["server_log_tail"] = (stdout or "")[-4000:]

    return evidence


def _render_markdown(evidence: dict[str, Any]) -> str:
    lines = [
        "# Ops Suite E2E Demo Kaniti",
        "",
        f"Uretildi (UTC): {evidence['generated_at']}",
        f"base_url: {evidence['base_url']}",
        f"Genel sonuc: **{'PASS' if evidence['overall_ok'] else 'FAIL'}**",
        "",
    ]
    for step in evidence["steps"]:
        status = "OK" if step["ok"] else "FAIL"
        lines.append(f"## {step['step']} -- {status}")
        lines.append("")
        detail = {k: v for k, v in step.items() if k not in ("step", "ok")}
        lines.append("```json")
        lines.append(json.dumps(detail, indent=2, ensure_ascii=False))
        lines.append("```")
        lines.append("")
    lines.append("## NOT_COLLECTED")
    lines.append("")
    lines.append("Bu ortamda gercek donanim/tarayici olmadigi icin ASLA fabrike EDILMEDI:")
    lines.append("")
    for item in evidence["not_collected"]:
        lines.append(f"- **{item['item']}**: {item['reason']}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = REPO_ROOT / "reports" / f"ops_suite_demo_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    evidence = run_demo(out_dir=out_dir)

    (out_dir / "evidence.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "evidence.md").write_text(_render_markdown(evidence), encoding="utf-8")

    for step in evidence["steps"]:
        print(f"[{'OK' if step['ok'] else 'FAIL'}] {step['step']}")
    print(f"NOT_COLLECTED: {len(evidence['not_collected'])} madde (bkz. evidence.md)")
    print(f"genel_sonuc={'PASS' if evidence['overall_ok'] else 'FAIL'}")
    print(f"evidence_dir={out_dir}")

    return 0 if evidence["overall_ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
