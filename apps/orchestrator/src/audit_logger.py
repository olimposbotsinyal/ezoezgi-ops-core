"""Genel amacli audit logger (PLAN.md T17, ADR-009).

Yalnizca finans degil, tum riskli/izlenmesi gereken aksiyonlar burada
kayit altina alinir. `data/audit/audit.log.jsonl` dosyasina append-only,
JSONL formatinda (satir basina bir JSON objesi) yazilir -- var olan
satirlar asla degistirilmez/silinmez, yalnizca yeni satir eklenir.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_AUDIT_LOG_PATH = Path("data/audit/audit.log.jsonl")

REQUIRED_RECORD_FIELDS = (
    "timestamp",
    "request_id",
    "alias",
    "task",
    "risk_level",
    "status",
    "details",
)


class AuditLogger:
    """`data/audit/audit.log.jsonl`'a append-only kayit yazan basit logger."""

    def __init__(self, log_path: str | Path = DEFAULT_AUDIT_LOG_PATH) -> None:
        self._log_path = Path(log_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def log_path(self) -> Path:
        return self._log_path

    def log(
        self,
        *,
        alias: str | None,
        task: str | None,
        status: str,
        risk_level: str = "low",
        details: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Bir audit kaydi uretir ve dosyaya append eder; yazilan kaydi dondurur."""
        record: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": request_id or str(uuid.uuid4()),
            "alias": alias,
            "task": task,
            "risk_level": risk_level,
            "status": status,
            "details": details or {},
        }

        line = json.dumps(record, ensure_ascii=False)
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

        return record
