"""Ajan/tool registry (PLAN.md T11).

`task_en` degerini (ör. "RUN_ECHO") bir handler fonksiyonuna esler. Handler
imzasi: `handler(task: dict) -> dict`.
"""

from __future__ import annotations

from typing import Any, Callable

Handler = Callable[[dict[str, Any]], dict[str, Any]]


class HandlerNotFoundError(KeyError):
    """Registry'de kayitli olmayan bir task_en cagrildiginda firlatilir."""


class Registry:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    def register(self, task_name: str, handler: Handler) -> None:
        self._handlers[task_name] = handler

    def get(self, task_name: str) -> Handler:
        try:
            return self._handlers[task_name]
        except KeyError as exc:
            raise HandlerNotFoundError(task_name) from exc

    def has(self, task_name: str) -> bool:
        return task_name in self._handlers

    def registered_tasks(self) -> list[str]:
        return list(self._handlers)


def build_default_registry() -> Registry:
    """Baslangic registry'si -- su an yalnizca `RUN_ECHO` kayitli.

    Ileride yeni ajan/tool eklemek, buraya yeni bir `registry.register(...)`
    satiri eklemekten ibarettir (bkz. MASTER_ROADMAP.md §3).
    """
    from echo_runner import run_echo

    registry = Registry()
    registry.register("RUN_ECHO", run_echo)
    return registry
