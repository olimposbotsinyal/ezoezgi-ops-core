"""Hafif circuit breaker -- provider basina ardisik hata sayaci.

Bellek-ici (in-process) durum tutar; kalicilastirilmis bir store degildir --
surec yeniden baslarsa sifirlanir. Bu, tek bir calisma zamani sureci icin
"art arda N kez basarisiz oldu, bir sure daha deneme" korumasi saglamak
icindir; kalici/denetlenebilir kayit ihtiyaci zaten audit log (data/audit/
audit.log.jsonl) tarafindan karsilaniyor.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class _ProviderState:
    consecutive_failures: int = 0
    opened_at: float | None = None


class CircuitBreaker:
    def __init__(self, fail_threshold: int, reset_seconds: int) -> None:
        self._fail_threshold = fail_threshold
        self._reset_seconds = reset_seconds
        self._state: dict[str, _ProviderState] = {}

    def _get(self, provider_name: str) -> _ProviderState:
        if provider_name not in self._state:
            self._state[provider_name] = _ProviderState()
        return self._state[provider_name]

    def record_success(self, provider_name: str) -> None:
        self._state[provider_name] = _ProviderState()

    def record_failure(self, provider_name: str) -> None:
        state = self._get(provider_name)
        state.consecutive_failures += 1
        if state.consecutive_failures >= self._fail_threshold and state.opened_at is None:
            state.opened_at = time.monotonic()

    def is_open(self, provider_name: str) -> bool:
        state = self._get(provider_name)
        if state.opened_at is None:
            return False
        elapsed = time.monotonic() - state.opened_at
        if elapsed >= self._reset_seconds:
            # Reset penceresi doldu -- devreyi yariya ac (tekrar denenebilir),
            # sayaci sifirla.
            self._state[provider_name] = _ProviderState()
            return False
        return True

    def snapshot(self) -> dict[str, dict[str, float | int | None]]:
        return {
            name: {
                "consecutive_failures": s.consecutive_failures,
                "opened_at": s.opened_at,
            }
            for name, s in self._state.items()
        }
