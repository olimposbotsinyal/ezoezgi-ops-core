"""Ops Suite FastAPI uygulamasi (PLAN.md T24/T28, DECISIONS.md ADR-015/ADR-019).

`create_app(**overrides)` DI-dostu bir fabrikadir -- testler her bileseni
(tmp_path'e izole edilmis `ApprovalQueueStore`/`AuditLogger` gibi) ayrica
enjekte edebilir, gercek proje dosyalarina (`data/audit/...`,
`data/approvals/...`) dokunmadan.

Uc nokta ozeti:
  - `GET /api/agents` -- `AgentStatusResolver.resolve_all()`
  - `GET /api/assistant` -- `AssistantPresenceTracker.current()`
  - `GET /api/approvals?status=pending` -- `ApprovalQueueStore.list_pending()`
  - `POST /api/approvals/{request_id}/approve|reject` -- **kimlik dogrulama
    ZORUNLU** (`Authorization: Bearer <token>`, bkz. `identity.py`),
    `authorize_decision()` ile kapsam/owner-root-guard kontrolu, sonra
    `ApprovalQueueStore.decide()` + genisletilmis audit kaydi
    (`actor_id`/`auth_method`/`authority_source`/`decision_scope`) +
    `approval.queue` WS yayini
  - `POST /api/voice/command` -- `VoiceBridge.handle_voice_command()` +
    `task.lifecycle`/`assistant.presence`/(varsa) `approval.queue` WS yayinlari
  - `WS /ws/live` -- tum konulara abone TEK bir baglanti (v0: konu bazli
    filtreleme YOK, istemci TUM olaylari alir ve kendi filtreler)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from audit_logger import AuditLogger
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ops_suite.approval_queue import AlreadyDecidedError, ApprovalQueueStore, UnknownRequestIdError
from ops_suite.assistant_presence import AssistantPresenceTracker
from ops_suite.events import TOPIC_APPROVAL_QUEUE, TOPIC_ASSISTANT_PRESENCE, TOPIC_TASK_LIFECYCLE
from ops_suite.identity import AUTH_METHOD_BEARER, AuthenticationError, AuthorizationError, Identity, IdentityStore, authorize_decision

# apps/ops-suite/backend/src/ops_suite/app.py -> apps/ops-suite/frontend
FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"
from ops_suite.heartbeat import HeartbeatTracker
from ops_suite.status_resolver import AgentStatusResolver
from ops_suite.voice_bridge import VoiceBridge
from ops_suite.ws_manager import ConnectionManager

_bearer_scheme = HTTPBearer(auto_error=False)


class VoiceCommandRequest(BaseModel):
    input_tr: str


class ApprovalDecisionRequest(BaseModel):
    """`actor` alani BILEREK YOK -- eylemi kimin yaptigi artik istemcinin
    beyan ettigi serbest metinden DEGIL, `Authorization: Bearer <token>`
    ile dogrulanan `Identity`'den gelir (bkz. BACKLOG.md B044)."""

    note: str | None = None


def create_app(
    *,
    heartbeat_tracker: HeartbeatTracker | None = None,
    status_resolver: AgentStatusResolver | None = None,
    approval_queue: ApprovalQueueStore | None = None,
    assistant_presence: AssistantPresenceTracker | None = None,
    voice_bridge: VoiceBridge | None = None,
    connection_manager: ConnectionManager | None = None,
    audit_logger: AuditLogger | None = None,
    identity_store: IdentityStore | None = None,
) -> FastAPI:
    heartbeat_tracker = heartbeat_tracker or HeartbeatTracker()
    status_resolver = status_resolver or AgentStatusResolver(heartbeat_tracker)
    approval_queue = approval_queue or ApprovalQueueStore()
    assistant_presence = assistant_presence or AssistantPresenceTracker()
    audit_logger = audit_logger or AuditLogger()
    voice_bridge = voice_bridge or VoiceBridge(
        approval_queue=approval_queue, assistant_presence=assistant_presence, heartbeat_tracker=heartbeat_tracker,
    )
    connection_manager = connection_manager or ConnectionManager()
    identity_store = identity_store or IdentityStore.from_config_path()

    app = FastAPI(title="EzoEzgi Ops Suite", version="0.1.0")

    # Testlerin/`scripts/ops_suite_demo.py`'nin bilesenlere DOGRUDAN
    # erisebilmesi icin (ornegin gercek WS yayinini dogrulamak) -- FastAPI'nin
    # kendi `app.state`'i, ekstra bir DI framework GEREKTIRMEDEN.
    app.state.heartbeat_tracker = heartbeat_tracker
    app.state.status_resolver = status_resolver
    app.state.approval_queue = approval_queue
    app.state.assistant_presence = assistant_presence
    app.state.voice_bridge = voice_bridge
    app.state.connection_manager = connection_manager
    app.state.audit_logger = audit_logger
    app.state.identity_store = identity_store

    def _get_current_identity(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme)) -> Identity:
        token = credentials.credentials if credentials is not None else None
        try:
            return identity_store.authenticate(token)
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @app.get("/api/agents")
    def get_agents() -> list[dict[str, Any]]:
        return [p.to_dict() for p in status_resolver.resolve_all()]

    @app.get("/api/assistant")
    def get_assistant() -> dict[str, Any]:
        return assistant_presence.current().to_dict()

    @app.get("/api/approvals")
    def get_approvals(status: str = "pending") -> list[dict[str, Any]]:
        if status != "pending":
            raise HTTPException(status_code=400, detail=f"desteklenmeyen status: {status!r} (yalnizca 'pending')")
        return [e.to_dict() for e in approval_queue.list_pending()]

    @app.get("/api/whoami")
    def whoami(identity: Identity = Depends(_get_current_identity)) -> dict[str, Any]:
        """Frontend'in token'ini gondermeden once kendi kimligini
        dogrulayabilmesi icin (bkz. B044) -- `scopes` kasitli DAHIL
        EDILMEDI (sahibi icin anlamsiz, delegate icin de UI'da simdilik
        gosterilmiyor); yalnizca kim olarak KIMLIK DOGRULANDIGI donuyor."""
        return {"actor_id": identity.actor_id, "display_name": identity.display_name, "authority_source": identity.authority_source}

    async def _decide_and_broadcast(request_id: str, decision: str, body: ApprovalDecisionRequest, identity: Identity) -> dict[str, Any]:
        pending_entry = approval_queue.get_pending_entry(request_id)
        risk_level = pending_entry.risk_level if pending_entry is not None else None

        try:
            decision_scope = authorize_decision(identity, decision=decision, risk_level=risk_level)
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        try:
            record = approval_queue.decide(
                request_id, decision,
                actor_id=identity.actor_id, auth_method=AUTH_METHOD_BEARER,
                authority_source=identity.authority_source, decision_scope=decision_scope,
                note=body.note,
            )
        except UnknownRequestIdError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except AlreadyDecidedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        audit_logger.log(
            alias=None, task=None, status=decision.upper(), risk_level=risk_level or "unknown", request_id=request_id,
            details={
                "actor_id": identity.actor_id,
                "auth_method": AUTH_METHOD_BEARER,
                "authority_source": identity.authority_source,
                "decision_scope": decision_scope,
                "note": body.note,
                "source": "ops_suite_approval_endpoint",
            },
        )
        await connection_manager.broadcast(TOPIC_APPROVAL_QUEUE, {"request_id": request_id, "decision": decision})
        return record

    @app.post("/api/approvals/{request_id}/approve")
    async def approve(request_id: str, body: ApprovalDecisionRequest, identity: Identity = Depends(_get_current_identity)) -> dict[str, Any]:
        return await _decide_and_broadcast(request_id, "approved", body, identity)

    @app.post("/api/approvals/{request_id}/reject")
    async def reject(request_id: str, body: ApprovalDecisionRequest, identity: Identity = Depends(_get_current_identity)) -> dict[str, Any]:
        return await _decide_and_broadcast(request_id, "rejected", body, identity)

    @app.post("/api/voice/command")
    async def voice_command(body: VoiceCommandRequest) -> dict[str, Any]:
        outcome = voice_bridge.handle_voice_command(body.input_tr)

        for event in outcome["events"]:
            await connection_manager.broadcast(TOPIC_TASK_LIFECYCLE, event.to_dict())
        await connection_manager.broadcast(TOPIC_ASSISTANT_PRESENCE, assistant_presence.current().to_dict())
        if outcome["approval_submission"] is not None:
            await connection_manager.broadcast(TOPIC_APPROVAL_QUEUE, outcome["approval_submission"])

        return {
            "request_id": outcome["request_id"],
            "tr_response": outcome["tr_response"],
            "result_en": outcome["result_en"],
        }

    @app.websocket("/ws/live")
    async def ws_live(websocket: WebSocket) -> None:
        """v0: TEK bir konu-filtresiz akis -- istemci TUM olaylari alir
        (agent.presence/task.lifecycle/assistant.presence/approval.queue),
        kendi tarafinda `topic` alanina gore filtreler (bkz.
        `frontend/js/ws_client.js`)."""
        await connection_manager.connect(websocket)
        try:
            while True:
                await websocket.receive_text()  # istemciden gelen mesajlar v0'da yok sayilir, baglantiyi CANLI tutar
        except WebSocketDisconnect:
            connection_manager.disconnect(websocket)

    # Statik frontend v0 shell -- BUTUN spesifik @app.get/@app.post/@app.websocket
    # rotalarindan SONRA mount edilir (Starlette rotalari KAYIT SIRASINA gore
    # dener; "/api/*" ve "/ws/live" ONCE tanimlandigi icin bu mount'un ONUNE
    # GECER). Frontend klasoru (henuz) yoksa (ornegin cok kucultulmus bir
    # checkout) SESSIZCE atlanir -- backend API'nin CALISMASI frontend
    # dosyalarina BAGIMLI DEGILDIR.
    if FRONTEND_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

    return app
