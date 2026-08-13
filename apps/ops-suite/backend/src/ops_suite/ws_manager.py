"""Konu-tabanli, surec-ici WebSocket yayincisi (PLAN.md T24, DECISIONS.md
ADR-015 -- "event bus" harici bir arac (Redis/Kafka) DEGIL, TEK bir
uvicorn surecinin bellek-ici asyncio yayincisidir; v0 kapsami disinda
dagitik/coklu-surec bir dagitim YOKTUR)."""

from __future__ import annotations

from fastapi import WebSocket

from ops_suite.events import WSEvent, build_event


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._seq = 0

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)

    async def broadcast(self, topic: str, payload: dict) -> WSEvent:
        """`topic` gecersizse `InvalidEventError` firlatir (bkz.
        `events.py::build_event`) -- yanlis-yazilmis bir konu adiyla
        SESSIZCE yayin yapilmaz. Baglantisi kopmus bir istemciye yazma
        BASARISIZ olursa o istemci SESSIZCE baglanti listesinden
        cikarilir (diger istemcilere yayini ENGELLEMEZ)."""
        self._seq += 1
        event = build_event(topic, payload, seq=self._seq)
        raw = event.to_json()

        stale: list[WebSocket] = []
        for connection in list(self._connections):
            try:
                await connection.send_text(raw)
            except Exception:
                stale.append(connection)
        for connection in stale:
            self._connections.discard(connection)

        return event
