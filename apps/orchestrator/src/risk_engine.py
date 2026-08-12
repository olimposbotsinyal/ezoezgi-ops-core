"""Risk motoru (PLAN.md T13, MASTER_ROADMAP.md §8).

Bir `task_en` degerini `policies/risk/tool_risk_policy.yaml` uzerinden bir
risk seviyesine (`low`/`medium`/`high`/`irreversible`) esler. Politika
dosyasinda tanimli olmayan bir task, policy'nin `default_risk_level`'ina
(o da yoksa `FALLBACK_RISK_LEVEL`'a) duser -- guvenli taraf secilir, tanimsiz
bir task asla otomatik "low" sayilmaz.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_POLICY_PATH = Path("policies/risk/tool_risk_policy.yaml")

# Politika dosyasi bulunamaz/gecersizse dahi bir seviyeye ihtiyac var --
# "medium" secildi (otomatik izin verir ama en dusuk seviye kadar gevsek
# degildir); RiskEngine kurulurken policy okunamiyorsa zaten RiskPolicyError
# firlatilir, bu yalnizca policy VAR ama `default_risk_level` alani YOKSA
# devreye girer.
FALLBACK_RISK_LEVEL = "medium"

RISK_LEVELS = ("low", "medium", "high", "irreversible")


class RiskPolicyError(ValueError):
    """Policy dosyasi okunamadi veya beklenen semaya uymadiginda firlatilir."""


class RiskEngine:
    def __init__(self, policy_path: str | Path = DEFAULT_POLICY_PATH) -> None:
        self._policy_path = Path(policy_path)
        self._policy = self._load_policy()

    def _load_policy(self) -> dict[str, Any]:
        try:
            raw = self._policy_path.read_text(encoding="utf-8")
            data = yaml.safe_load(raw) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise RiskPolicyError(
                f"Risk policy okunamadi ({self._policy_path}): {exc}"
            ) from exc

        if not isinstance(data, dict):
            raise RiskPolicyError("Risk policy bir YAML objesi olmali")

        tasks = data.get("tasks", {})
        if not isinstance(tasks, dict):
            raise RiskPolicyError("Risk policy 'tasks' alani bir obje olmali")

        for task_name, level in tasks.items():
            if level not in RISK_LEVELS:
                raise RiskPolicyError(
                    f"'{task_name}' icin gecersiz risk seviyesi: {level!r} "
                    f"(gecerli degerler: {', '.join(RISK_LEVELS)})"
                )

        default_level = data.get("default_risk_level", FALLBACK_RISK_LEVEL)
        if default_level not in RISK_LEVELS:
            raise RiskPolicyError(f"Gecersiz default_risk_level: {default_level!r}")

        return data

    def get_risk(self, task_name: str) -> str:
        """Task'in risk seviyesini dondurur; tanimsizsa policy'nin varsayilanina duser."""
        tasks = self._policy.get("tasks", {})
        return tasks.get(
            task_name, self._policy.get("default_risk_level", FALLBACK_RISK_LEVEL)
        )
