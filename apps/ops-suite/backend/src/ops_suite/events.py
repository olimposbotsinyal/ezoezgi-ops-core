"""Ops Suite olay veri yolu (event bus) konu haritasi + WebSocket zarfi
(PLAN.md T21, DECISIONS.md ADR-015).

**Mimari karar (ADR-015):** "event bus" harici bir aracidan (Redis/Kafka)
DEGIL, `ws_manager.py::ConnectionManager`'in surec-ici (in-process) asyncio
tabanli bir yayincisindan ibarettir -- tek bir uvicorn sureci disinda hicbir
dagitik bilesen YOKTUR (v0 kapsami)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

TOPIC_AGENT_PRESENCE = "agent.presence"
TOPIC_TASK_LIFECYCLE = "task.lifecycle"
TOPIC_ASSISTANT_PRESENCE = "assistant.presence"
TOPIC_APPROVAL_QUEUE = "approval.queue"

ALL_TOPICS = (TOPIC_AGENT_PRESENCE, TOPIC_TASK_LIFECYCLE, TOPIC_ASSISTANT_PRESENCE, TOPIC_APPROVAL_QUEUE)


class InvalidEventError(ValueError):
    """Bilinmeyen bir konu VEYA ayristirilamayan bir zarf icin firlatilir."""


@dataclass(frozen=True)
class WSEvent:
    """Tum WebSocket yayinlarinin TEK sekli -- istemci tarafi tek bir
    `switch(topic)` ile TUM olay turlerini isleyebilir."""

    topic: str
    payload: dict[str, Any]
    seq: int = 0

    def to_json(self) -> str:
        return json.dumps({"topic": self.topic, "payload": self.payload, "seq": self.seq}, ensure_ascii=False)


def build_event(topic: str, payload: dict[str, Any], *, seq: int = 0) -> WSEvent:
    """Bilinmeyen bir `topic` icin `InvalidEventError` firlatir -- yayinci
    tarafinin YANLIS/YAZIM-HATALI bir konu adiyla SESSIZCE yayin yapmasini
    ONLER."""
    if topic not in ALL_TOPICS:
        raise InvalidEventError(f"bilinmeyen konu: {topic!r} (gecerli konular: {ALL_TOPICS})")
    return WSEvent(topic=topic, payload=payload, seq=seq)


def parse_event(raw: str) -> WSEvent:
    """`build_event`'in ureteceginin TERSI -- bir istemcinin (veya testin)
    aldigi ham WS mesaj metnini `WSEvent`'e cevirir. Bozuk JSON VEYA eksik
    alan VEYA bilinmeyen konu icin `InvalidEventError` firlatir (ASLA
    sessizce yarim/varsayilan bir olay uretmez)."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidEventError(f"gecersiz JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise InvalidEventError(f"zarf bir obje (dict) olmali, gozlenen tip: {type(data).__name__}")
    for required in ("topic", "payload"):
        if required not in data:
            raise InvalidEventError(f"zarfta zorunlu alan eksik: '{required}'")
    return build_event(data["topic"], data["payload"], seq=data.get("seq", 0))
