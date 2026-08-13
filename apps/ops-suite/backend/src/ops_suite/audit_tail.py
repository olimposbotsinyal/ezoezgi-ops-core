"""`data/audit/audit.log.jsonl`'in salt-okunur, poll-tabanli "tail"i
(PLAN.md T22) -- `AuditLogger` (apps/orchestrator/src/audit_logger.py)
dosyayi DEGISTIRMEDEN, yalnizca OKUYARAK izler; hicbir yazma islemi
YAPMAZ."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_AUDIT_LOG_PATH = Path("data/audit/audit.log.jsonl")


class AuditTailReader:
    def __init__(self, log_path: str | Path = DEFAULT_AUDIT_LOG_PATH) -> None:
        self._log_path = Path(log_path)
        self._offset = 0

    def read_new_records(self) -> list[dict[str, Any]]:
        """Son okunan konumdan (offset) itibaren YENI eklenmis satirlari
        okur, offset'i ileri tasir. Dosya henuz YOKSA bos liste doner
        (hata FIRLATMAZ -- AuditLogger ilk `.log()` cagrisina kadar dosyayi
        hic olusturmayabilir). Bozuk/yarim yazilmis bir JSON satiri
        SESSIZCE atlanir (diger satirlari ENGELLEMEZ)."""
        if not self._log_path.exists():
            return []
        records: list[dict[str, Any]] = []
        with self._log_path.open("r", encoding="utf-8") as f:
            f.seek(self._offset)
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            self._offset = f.tell()
        return records

    def read_all_records(self) -> list[dict[str, Any]]:
        """Offset'i ETKILEMEDEN (ilerletmeden) TUM dosyayi baştan okur --
        `status_resolver.py`'nin "bu ajanin/alias'in EN SON kaydi ne
        durumdaydi" sorgulari icin kullanilir."""
        if not self._log_path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in self._log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records

    def reset_offset(self) -> None:
        self._offset = 0
