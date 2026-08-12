"""Hot-reloading loader for config/assistant.identity.json (PLAN.md T5).

Polling tabanli: her get_config() cagrisinda dosyanin mtime'i kontrol edilir,
degismisse yeniden okunup dogrulanir. Restart gerekmez (bkz.
alias_update_policy.requires_restart: false). Okuma/validasyon basarisiz
olursa (dosya silinmis, bozuk JSON, eksik alan vb.) son gecerli config
korunur ve bir uyari loglanir -- crash olmaz.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("config_loader")

REQUIRED_FIELDS = (
    "assistant_id",
    "display_name",
    "wake_aliases",
    "language_mode",
    "alias_update_policy",
)


class ConfigValidationError(ValueError):
    """Config dosyasi semaya uymadiginda firlatilir."""


def _validate(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise ConfigValidationError("Config bir JSON objesi olmali")

    missing = [f for f in REQUIRED_FIELDS if f not in data]
    if missing:
        raise ConfigValidationError(f"Eksik alan(lar): {', '.join(missing)}")

    aliases = data["wake_aliases"]
    if not isinstance(aliases, list) or not aliases:
        raise ConfigValidationError("wake_aliases bos olmayan bir liste olmali")
    if not all(isinstance(a, str) and a.strip() for a in aliases):
        raise ConfigValidationError("wake_aliases yalnizca bos olmayan string icermeli")

    if not isinstance(data["language_mode"], dict):
        raise ConfigValidationError("language_mode bir obje olmali")
    if not isinstance(data["alias_update_policy"], dict):
        raise ConfigValidationError("alias_update_policy bir obje olmali")


class ConfigLoader:
    """`assistant.identity.json` icin polling tabanli hot-reload loader."""

    def __init__(self, config_path: str | Path) -> None:
        self._path = Path(config_path)
        self._config: dict[str, Any] | None = None
        self._mtime: float | None = None
        self._reload(initial=True)

    def _reload(self, initial: bool = False) -> None:
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
            _validate(data)
        except (OSError, json.JSONDecodeError, ConfigValidationError) as exc:
            if initial or self._config is None:
                raise ConfigValidationError(
                    f"Ilk config yuklemesi basarisiz ({self._path}): {exc}"
                ) from exc
            logger.warning(
                "Config yeniden yuklenemedi (%s), son gecerli config korunuyor: %s",
                self._path,
                exc,
            )
            return

        self._config = data
        try:
            self._mtime = self._path.stat().st_mtime
        except OSError:
            self._mtime = None

    def get_config(self) -> dict[str, Any]:
        """Guncel config'i dondurur; dosya degismisse once yeniden yukler."""
        try:
            current_mtime = self._path.stat().st_mtime
        except OSError as exc:
            logger.warning(
                "Config dosyasina erisilemedi, son gecerli config korunuyor: %s", exc
            )
            assert self._config is not None
            return self._config

        if self._mtime is None or current_mtime != self._mtime:
            self._reload()

        assert self._config is not None
        return self._config

    def get_wake_aliases(self) -> list[str]:
        return list(self.get_config()["wake_aliases"])
