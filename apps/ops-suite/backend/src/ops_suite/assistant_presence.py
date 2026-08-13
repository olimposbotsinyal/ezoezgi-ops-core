"""Asistanin (EzoEzgi'nin kendisinin) o anki "sunum" durumu izleyicisi
(PLAN.md T24) -- `AgentPresence`'tan AYRIDIR: bu bir ajanin degil,
KULLANICIYA GORUNEN asistan kimliginin (config/assistant.identity.json'daki
`display_name`) durumudur (bkz. docs/VOICE_FIRST_INTERACTION_POLICY.md)."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable

from ops_suite.schemas import AssistantPresenceEvent

DEFAULT_ASSISTANT_ID = "ezoezgi-core"


class AssistantPresenceTracker:
    def __init__(self, assistant_id: str = DEFAULT_ASSISTANT_ID, clock: Callable[[], float] = time.time) -> None:
        self._assistant_id = assistant_id
        self._clock = clock
        self._current: AssistantPresenceEvent | None = None

    def set_state(
        self, state: str, *, utterance_tr: str | None = None, related_request_id: str | None = None
    ) -> AssistantPresenceEvent:
        """Yeni bir durum uretir VE mevcut duruma yazar -- `state`
        gecersizse `AssistantPresenceEvent.__post_init__` `ValueError`
        firlatir, mevcut durum DEGISMEDEN kalir."""
        event = AssistantPresenceEvent(
            assistant_id=self._assistant_id, state=state, utterance_tr=utterance_tr,
            related_request_id=related_request_id,
            timestamp=datetime.fromtimestamp(self._clock(), tz=timezone.utc).isoformat(),
        )
        self._current = event
        return event

    def current(self) -> AssistantPresenceEvent:
        """Hic `set_state()` cagrilmamissa, varsayilan `"idle"` durumunu
        (SIMDIKI zaman damgasiyla) doner -- `None` DEGIL, cunku Ops Suite
        her zaman GECERLI bir asistan durumu GOSTERMELIDIR."""
        if self._current is None:
            return AssistantPresenceEvent(
                assistant_id=self._assistant_id, state="idle",
                timestamp=datetime.fromtimestamp(self._clock(), tz=timezone.utc).isoformat(),
            )
        return self._current
