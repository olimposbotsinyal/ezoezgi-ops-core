# analytics/user_trade_recorder.py
from __future__ import annotations
import os, json, logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

def _user_report_dir(user_id: int, exchange: str) -> str:
    base = os.path.join("analytics", "user_reports", str(user_id), exchange.lower())
    os.makedirs(base, exist_ok=True)
    return base

def _user_trades_path(user_id: int, exchange: str) -> str:
    return os.path.join(_user_report_dir(user_id, exchange), "trades.jsonl")

def append_user_trade_event(user_id: int, exchange: str, record: Dict[str, Any]) -> bool:
    try:
        path = _user_trades_path(user_id, exchange)
        # timestamp yoksa ekle
        record.setdefault("ts", datetime.now(timezone.utc).isoformat())
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        logger.error(f"[USER_TRADE_APPEND_ERR] user={user_id} ex={exchange} err={e}", exc_info=True)
        return False
# analytics/user_trade_recorder.py (aynı dosyaya ek)
def _daily_summary_path(user_id: int, exchange: str) -> str:
    return os.path.join(_user_report_dir(user_id, exchange), "daily_summary.json")

def update_daily_summary(user_id: int, exchange: str, record: Dict[str, Any]) -> None:
    try:
        # Sadece CLOSED eventleri say
        if record.get("event") != "CLOSED":
            return
        path = _daily_summary_path(user_id, exchange)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        data = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        data = json.loads(content)
            except Exception:
                data = {}
        d = data.get(day) or {"trade_count": 0, "gross_sum": 0.0, "win": 0, "loss": 0}
        gp = float(record.get("realized_gross_pct") or record.get("realized_gross_pct_simple") or 0.0)
        d["trade_count"] += 1
        d["gross_sum"] += gp
        if gp > 0:
            d["win"] += 1
        elif gp < 0:
            d["loss"] += 1
        data[day] = d
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        logger.warning(f"[DAILY_SUMMARY_UPDATE_ERR] user={user_id} ex={exchange} err={e}")
