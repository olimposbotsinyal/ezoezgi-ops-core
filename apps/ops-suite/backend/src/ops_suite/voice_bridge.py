"""Voice/UI baglama katmani (PLAN.md T25, DECISIONS.md ADR-015).

**Ses donanimi (mikrofon/hoparlor) bu ortamda YOKTUR** -- bu yuzden "ses
girdisi" MOCKED TR METIN olarak alinir. `bridge.translate_and_extract()`
zaten HAM SES DEGIL, METIN alir -- bu yuzden bu modulun SOZLESMESI
(girdi: TR metin) gercek STT (B004) eklendiginde DEGISMEZ, yalnizca
girdi KAYNAGI (klavye/API -> mikrofon) degisir (bkz.
docs/VOICE_FIRST_INTERACTION_POLICY.md).

`orchestrator.py`/`bridge.py`/`audit_logger.py`/`risk_engine.py`/
`config_loader.py` HICBIRI DEGISTIRILMEZ -- bu modul onlari SARAR, AYNI
`scripts/e2e_demo.py::run_e2e_demo()` deseniyle (bkz. o dosya)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from approval_stub import STATUS_WAITING_APPROVAL
from audit_logger import AuditLogger, DEFAULT_AUDIT_LOG_PATH
from bridge import generate_tr_response, translate_and_extract
from config_loader import ConfigLoader
from orchestrator import STATUS_ERROR, STATUS_NO_HANDLER, STATUS_OK, Orchestrator
from registry import build_default_registry
from risk_engine import DEFAULT_POLICY_PATH, RiskEngine

from ops_suite.approval_queue import ApprovalQueueStore
from ops_suite.assistant_presence import AssistantPresenceTracker
from ops_suite.heartbeat import HeartbeatTracker
from ops_suite.schemas import TaskLifecycleEvent
from ops_suite.status_resolver import KNOWN_LIVE_AGENTS

DEFAULT_CONFIG_PATH = Path("config/assistant.identity.json")
ORCHESTRATOR_AGENT_ID = "orchestrator"
# status_resolver.py'deki KANONIK ismi yeniden kullanir -- "Orchestrator"
# dizesini burada IKINCI bir yerde YINELEMEMEK icin (iki yerin birbirinden
# SAPMASI riskini onler).
ORCHESTRATOR_DISPLAY_NAME = KNOWN_LIVE_AGENTS[ORCHESTRATOR_AGENT_ID]

_STATUS_TO_LIFECYCLE_STATE = {
    STATUS_OK: "completed",
    STATUS_NO_HANDLER: "failed",
    STATUS_ERROR: "failed",
    STATUS_WAITING_APPROVAL: "awaiting_approval",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class VoiceBridge:
    def __init__(
        self,
        *,
        config_path: str | Path = DEFAULT_CONFIG_PATH,
        risk_policy_path: str | Path = DEFAULT_POLICY_PATH,
        audit_log_path: str | Path = DEFAULT_AUDIT_LOG_PATH,
        approval_queue: ApprovalQueueStore | None = None,
        assistant_presence: AssistantPresenceTracker | None = None,
        heartbeat_tracker: HeartbeatTracker | None = None,
    ) -> None:
        self._config = ConfigLoader(config_path)
        self._orchestrator = Orchestrator(build_default_registry(), risk_engine=RiskEngine(risk_policy_path))
        self._audit_logger = AuditLogger(audit_log_path)
        self._approval_queue = approval_queue or ApprovalQueueStore()
        self._assistant_presence = assistant_presence or AssistantPresenceTracker()
        self._heartbeat = heartbeat_tracker

    def handle_voice_command(self, input_tr: str) -> dict[str, Any]:
        """Tek bir mocked-TR-metin "ses" komutunu uctan uca isler. Donen
        sozluk: `request_id`, `tr_response`, `extracted` (bridge ciktisi),
        `result_en` (orchestrator ciktisi), `events` (`list[TaskLifecycleEvent]`
        -- WS yayini icin sirali asama gecisleri), `approval_submission`
        (WAITING_APPROVAL ise doldurulur, aksi halde `None`)."""
        request_id = str(uuid.uuid4())
        events: list[TaskLifecycleEvent] = []

        self._assistant_presence.set_state("listening", utterance_tr=input_tr, related_request_id=request_id)
        events.append(TaskLifecycleEvent(request_id=request_id, state="received", original_tr=input_tr, timestamp=_now_iso()))

        aliases = self._config.get_wake_aliases()
        extracted = translate_and_extract(input_tr, aliases)
        events.append(
            TaskLifecycleEvent(
                request_id=request_id, state="translating", task_en=extracted["task_en"],
                original_tr=input_tr, timestamp=_now_iso(),
            )
        )

        self._assistant_presence.set_state("thinking", related_request_id=request_id)
        if self._heartbeat is not None:
            self._heartbeat.record(
                ORCHESTRATOR_AGENT_ID, declared_state="working", last_task_id=request_id,
                display_name=ORCHESTRATOR_DISPLAY_NAME,
            )

        result_en = self._orchestrator.handle_task(extracted)
        risk_level = result_en.get("risk_level")
        events.append(
            TaskLifecycleEvent(
                request_id=request_id, state="risk_checked", task_en=extracted["task_en"],
                risk_level=risk_level, original_tr=input_tr, timestamp=_now_iso(),
            )
        )

        approval_submission: dict[str, Any] | None = None
        status = result_en.get("status", "unknown")
        lifecycle_state = _STATUS_TO_LIFECYCLE_STATE.get(status, "failed")

        if status == STATUS_WAITING_APPROVAL:
            approval_submission = self._approval_queue.submit(
                request_id=request_id, alias=extracted["detected_alias"], task=extracted["task_en"],
                risk_level=risk_level, original_tr=input_tr,
                details={"confidence": extracted.get("confidence")},
            )
            self._assistant_presence.set_state("blocked_policy", related_request_id=request_id)
        else:
            self._assistant_presence.set_state("thinking", related_request_id=request_id)

        events.append(
            TaskLifecycleEvent(
                request_id=request_id, state=lifecycle_state, task_en=extracted["task_en"],
                risk_level=risk_level, agent_id=ORCHESTRATOR_AGENT_ID, original_tr=input_tr, timestamp=_now_iso(),
            )
        )

        if self._heartbeat is not None:
            self._heartbeat.record(
                ORCHESTRATOR_AGENT_ID, declared_state="idle", last_task_id=request_id,
                display_name=ORCHESTRATOR_DISPLAY_NAME,
            )

        self._audit_logger.log(
            alias=extracted["detected_alias"], task=extracted["task_en"], status=status,
            risk_level=risk_level or "unknown", request_id=request_id,
            details={"original_tr": input_tr, "confidence": extracted.get("confidence"), "result_en": result_en},
        )

        tr_response = generate_tr_response(extracted["task_en"], result_en)
        self._assistant_presence.set_state("speaking", utterance_tr=tr_response, related_request_id=request_id)

        return {
            "request_id": request_id,
            "tr_response": tr_response,
            "extracted": extracted,
            "result_en": result_en,
            "events": events,
            "approval_submission": approval_submission,
        }
