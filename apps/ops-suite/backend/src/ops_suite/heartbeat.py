"""Ajan heartbeat izleyicisi (PLAN.md T22) -- surec-ici, bellek-ici (v0
kapsami, bkz. BACKLOG.md B041 -- yeniden baslatmalar arasi kalicilik
GELECEK isidir).

Bir ajan `record()` cagirmayi BIRAKIRSA (sureç durdu/çöktü/hiç
çalışmadı), `resolve_state()` `timeout_seconds` sonra otomatik olarak
`"offline"` doner -- BASKA hicbir mekanizma GEREKMEZ, "son ne zaman
gorulduk" TEK dogruluk kaynagidir."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from ops_suite.schemas import AgentPresence

DEFAULT_TIMEOUT_SECONDS = 30.0


@dataclass
class _AgentRecord:
    last_seen_ts: float
    declared_state: str | None = None
    last_task_id: str | None = None
    display_name: str = ""
    detail: str = ""


class HeartbeatTracker:
    """`clock` parametresi test enjeksiyonu icindir (gercek `time.time`
    yerine sahte/deterministik bir saat gecirilerek, testler GERCEK bir
    uyku/zaman aşımı BEKLEMEDEN calisir)."""

    def __init__(self, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS, clock: Callable[[], float] = time.time) -> None:
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._records: dict[str, _AgentRecord] = {}

    def record(
        self,
        agent_id: str,
        *,
        declared_state: str | None = None,
        last_task_id: str | None = None,
        display_name: str | None = None,
        detail: str | None = None,
        ts: float | None = None,
    ) -> None:
        now = ts if ts is not None else self._clock()
        existing = self._records.get(agent_id)
        self._records[agent_id] = _AgentRecord(
            last_seen_ts=now,
            declared_state=declared_state if declared_state is not None else (existing.declared_state if existing else None),
            last_task_id=last_task_id if last_task_id is not None else (existing.last_task_id if existing else None),
            display_name=display_name if display_name is not None else (existing.display_name if existing else agent_id),
            detail=detail if detail is not None else (existing.detail if existing else ""),
        )

    def resolve_state(self, agent_id: str) -> str:
        """Hic `record()` edilmemis VEYA son gorulmeden bu yana
        `timeout_seconds`'tan FAZLA gecmis bir ajan icin `"offline"`
        doner -- `declared_state` yalnizca ajan hala "canli" sayilirken
        gecerlidir."""
        record = self._records.get(agent_id)
        if record is None:
            return "offline"
        if (self._clock() - record.last_seen_ts) > self._timeout_seconds:
            return "offline"
        return record.declared_state or "idle"

    def snapshot(self) -> list[AgentPresence]:
        """Suan izlenen (en az bir kez `record()` edilmis) TUM ajanlarin
        `AgentPresence` anlik goruntusunu doner. Hic izlenmemis ajanlar
        icin bkz. `status_resolver.py` (bilinen-ama-hic-heartbeat-atmamis
        ajanlari `offline`/`not_implemented` olarak AYRICA ekler)."""
        result = []
        for agent_id, record in self._records.items():
            last_heartbeat_iso = datetime.fromtimestamp(record.last_seen_ts, tz=timezone.utc).isoformat()
            result.append(
                AgentPresence(
                    agent_id=agent_id,
                    display_name=record.display_name or agent_id,
                    state=self.resolve_state(agent_id),
                    last_heartbeat_ts=last_heartbeat_iso,
                    last_task_id=record.last_task_id,
                    detail=record.detail,
                    updated_at=datetime.fromtimestamp(self._clock(), tz=timezone.utc).isoformat(),
                )
            )
        return result
