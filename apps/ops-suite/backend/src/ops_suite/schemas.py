"""Ops Suite domain modeli (PLAN.md T21, DECISIONS.md ADR-015).

Uc temel sema:
  - `AgentPresence` -- bir ajanin O ANKI durumu (heartbeat/status_resolver
    tarafindan URETILIR, mutasyona UGRAMAZ -- her okuma yeni bir anlik
    goruntudur).
  - `TaskLifecycleEvent` -- bir gorevin `bridge.translate_and_extract()`'tan
    `orchestrator.handle_task()`'a kadar gectigi TEK bir asama olayi
    (append-only bir olay akisinin elemanidir, bir "durum" DEGIL).
  - `AssistantPresenceEvent` -- asistanin (EzoEzgi'nin kendisinin) o anki
    "sunum" durumu (dinliyor/dusunuyor/konusuyor/politika-engelledi).

**Durustluk ilkesi (bkz. docs/AGENT_PRESENCE_STATE_MODEL.md):** hicbir
saha/ajan icin GERCEK bir calisan surec/heartbeat OLMADAN "idle" veya
"working" durumu FABRIKE EDILMEZ -- calisir kod/heartbeat kaniti olmayan
bir ajan HER ZAMAN `state="offline"` + `detail="not_implemented"` doner
(bkz. `status_resolver.py::KNOWN_AGENT_IDS`)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

AGENT_STATES = ("working", "idle", "blocked", "awaiting_approval", "offline")

TASK_LIFECYCLE_STATES = (
    "received",
    "translating",
    "risk_checked",
    "awaiting_approval",
    "routed",
    "executing",
    "completed",
    "failed",
    "rejected",
)

ASSISTANT_STATES = ("idle", "listening", "thinking", "speaking", "blocked_policy")


def _validate_choice(value: str, valid: tuple[str, ...], *, field_name: str) -> None:
    if value not in valid:
        raise ValueError(f"gecersiz {field_name}: {value!r} (gecerli degerler: {valid})")


@dataclass(frozen=True)
class AgentPresence:
    """Bir ajanin heartbeat/status_resolver tarafindan hesaplanmis O ANKI
    anlik goruntusu. `agent_id` sabit bir taksonomiden gelir (bkz.
    `docs/IDENTITY_AND_DELEGATION_POLICY.md`)."""

    agent_id: str
    display_name: str
    state: str
    last_heartbeat_ts: str | None = None
    last_task_id: str | None = None
    detail: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        _validate_choice(self.state, AGENT_STATES, field_name="state")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TaskLifecycleEvent:
    """Tek bir gorevin (request_id ile izlenen) yasam dongusundeki TEK bir
    asama gecisi -- append-only bir akisin elemanidir; ayni request_id icin
    birden fazla `TaskLifecycleEvent` uretilebilir (received -> ... ->
    completed/failed/rejected)."""

    request_id: str
    state: str
    task_en: str | None = None
    risk_level: str | None = None
    agent_id: str | None = None
    original_tr: str | None = None
    timestamp: str = ""
    detail: str = ""

    def __post_init__(self) -> None:
        _validate_choice(self.state, TASK_LIFECYCLE_STATES, field_name="state")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AssistantPresenceEvent:
    """Asistanin (EzoEzgi) o anki sunum durumu -- bir konusma/gorev akisiyla
    iliskilendirilebilir (`related_request_id`) ama bagimsiz da yayinlanabilir
    (ornegin `state="idle"`)."""

    assistant_id: str
    state: str
    utterance_tr: str | None = None
    related_request_id: str | None = None
    timestamp: str = ""

    def __post_init__(self) -> None:
        _validate_choice(self.state, ASSISTANT_STATES, field_name="state")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ApprovalQueueEntry:
    """`approval_queue.py::ApprovalQueueStore.list_pending()`'in donus
    seklidir -- Ops Suite API/WS katmaninin serialize ettigi sema burada
    sabitlenir (bkz. `events.py::TOPIC_APPROVAL_QUEUE`)."""

    request_id: str
    alias: str | None
    task: str | None
    risk_level: str | None
    original_tr: str | None
    submitted_at: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
