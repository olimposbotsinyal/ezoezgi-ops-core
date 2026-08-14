"""Gercek uvicorn giris noktasi (PLAN.md T24/T28/T35) -- `python -m ops_suite.server`.

Varsayilan olarak yalnizca loopback'e (`127.0.0.1`) baglanir -- disaridan
erisim GEREKMEZ (v0, tek-kullanicili yerel kontrol merkezi). Port
`OPS_SUITE_PORT` ortam degiskeniyle degistirilebilir.

`OPS_SUITE_IDENTITY_CONFIG_PATH` (opsiyonel) -- `identity.py::IdentityStore`
icin `config/ops_suite_identities.json` disinda bir dosya kullanmak
gerektiginde (ornegin `scripts/ops_suite_demo.py`'nin gecici/demo-amacli
owner+delegate kimlikleriyle GERCEK bearer-token akisini uctan uca
kanitlamasi icin) -- bkz. BACKLOG.md B044.

`OPS_SUITE_DATA_DIR` (opsiyonel) -- verilirse `data/approvals/`/`data/audit/`/
`data/presence/`/`data/identity/` YERINE
`{OPS_SUITE_DATA_DIR}/approvals|audit|presence|identity/` kullanilir.
GERCEK bir uvicorn sureci baslatan ama izole olmayan
tuketiciler (ornegin `apps/ops-suite/e2e/`'nin Playwright testleri)
BUNU KULLANMALIDIR -- aksi halde her test kosusu, projenin GERCEK
`data/approvals/approval_queue.jsonl`/`data/presence/agent_presence.jsonl`
dosyalarina kalici kayitlar biriktirir (bkz. PLAN.md T36/T39 -- bu,
gercek bir E2E kosusunda GERCEKTEN kesfedilen bir hataydi, sessizce
atlanmadi)."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from audit_logger import AuditLogger

from ops_suite.app import create_app
from ops_suite.approval_queue import ApprovalQueueStore
from ops_suite.assistant_presence import AssistantPresenceTracker
from ops_suite.heartbeat import HeartbeatTracker
from ops_suite.identity import DEFAULT_IDENTITY_CONFIG_PATH, IdentityStore, TokenRevocationStore
from ops_suite.presence_store import PresenceStore
from ops_suite.voice_bridge import VoiceBridge

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8420

_identity_config_path = os.environ.get("OPS_SUITE_IDENTITY_CONFIG_PATH", str(DEFAULT_IDENTITY_CONFIG_PATH))

_data_dir_override = os.environ.get("OPS_SUITE_DATA_DIR")
if _data_dir_override:
    _data_dir = Path(_data_dir_override)
    _approval_queue_path = _data_dir / "approvals" / "approval_queue.jsonl"
    _audit_log_path = _data_dir / "audit" / "audit.log.jsonl"
    _presence_log_path = _data_dir / "presence" / "agent_presence.jsonl"
    # B051 (BACKLOG.md B051, PLAN.md T50) -- izole kosularin (Playwright/
    # scripts/ops_suite_demo.py) GERCEK `data/identity/token_revocations.jsonl`
    # dosyasina yazmasini ONLER -- T35/T39/T44'te GERCEKTEN yasanan veri-
    # kirlenmesi hata sinifinin AYNISI, bu sefer BASTAN engellendi.
    _token_revocation_path = _data_dir / "identity" / "token_revocations.jsonl"
    _identity_store = IdentityStore.from_config_path(
        _identity_config_path, revocation_store=TokenRevocationStore(_token_revocation_path),
    )
    _heartbeat_tracker = HeartbeatTracker()
    _assistant_presence = AssistantPresenceTracker()
    _approval_queue = ApprovalQueueStore(_approval_queue_path)
    _audit_logger = AuditLogger(_audit_log_path)
    _presence_store = PresenceStore(_presence_log_path)
    _voice_bridge = VoiceBridge(
        audit_log_path=_audit_log_path, approval_queue=_approval_queue,
        assistant_presence=_assistant_presence, heartbeat_tracker=_heartbeat_tracker,
    )
    app = create_app(
        identity_store=_identity_store, heartbeat_tracker=_heartbeat_tracker,
        approval_queue=_approval_queue, assistant_presence=_assistant_presence,
        voice_bridge=_voice_bridge, audit_logger=_audit_logger, presence_store=_presence_store,
    )
else:
    _identity_store = IdentityStore.from_config_path(_identity_config_path)
    app = create_app(identity_store=_identity_store)


def main() -> None:
    host = os.environ.get("OPS_SUITE_HOST", DEFAULT_HOST)
    port = int(os.environ.get("OPS_SUITE_PORT", str(DEFAULT_PORT)))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
