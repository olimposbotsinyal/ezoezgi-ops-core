# settings/execution/daily_summary_manager.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional, Dict, Any
import json
import os

TR_TZ = ZoneInfo("Europe/Istanbul")

@dataclass
class DailyState:
    day_key: str                  # "YYYY-MM-DD" (TR)
    e0: float                     # gün başlangıç equity (TR 00:00 civarı)
    eclose: float                 # gün içinde sürekli güncellenen son equity
    last_update_ts: float         # unix

class DailySummaryManager:
    def __init__(self, path_jsonl: str):
        self.path_jsonl = path_jsonl
        self.state: Optional[DailyState] = None

    @staticmethod
    def _tr_day_key(dt_utc: datetime) -> str:
        tr = dt_utc.astimezone(TR_TZ)
        return tr.strftime("%Y-%m-%d")

    def _append_jsonl(self, obj: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(self.path_jsonl) or ".", exist_ok=True)
        with open(self.path_jsonl, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def on_equity_tick(self, equity: float, *, dt_utc: Optional[datetime] = None) -> None:
        """
        Her equity ölçümünde çağır:
        - gün değiştiyse: dünkü satırı finalize et, bugünü başlat (e0)
        - aynı gündeyse: eclose güncelle
        """
        dt_utc = dt_utc or datetime.now(timezone.utc)
        day_key = self._tr_day_key(dt_utc)
        ts = dt_utc.timestamp()

        if self.state is None:
            # ilk init
            self.state = DailyState(day_key=day_key, e0=float(equity), eclose=float(equity), last_update_ts=ts)
            return

        if day_key != self.state.day_key:
            # Gün rollover: dünkü satırı yaz
            self._append_jsonl({
                "day": self.state.day_key,
                "e0": self.state.e0,
                "eclose": self.state.eclose,
                "updated_at_utc": datetime.fromtimestamp(self.state.last_update_ts, tz=timezone.utc).isoformat(),
            })
            # Bugünü başlat
            self.state = DailyState(day_key=day_key, e0=float(equity), eclose=float(equity), last_update_ts=ts)
            return

        # aynı gün: eclose güncelle
        self.state.eclose = float(equity)
        self.state.last_update_ts = ts
