# daily_reset.py
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

TR = ZoneInfo("Europe/Istanbul")

STATE_PATH = Path("state/daily_state.json")

def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"last_date_tr": None}

def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

def now_tr_date_str():
    return datetime.now(tz=TR).date().isoformat()

def is_reset_window():
    # TR 00:00 civarı (örn ilk 2 dakika)
    now = datetime.now(tz=TR)
    return now.hour == 0 and now.minute < 2

def maybe_write_daily_summary(write_daily_summary_func):
    """
    write_daily_summary_func() -> dict (daily_summary satırı)
    """
    state = load_state()
    today = now_tr_date_str()

    if not is_reset_window():
        return

    if state.get("last_date_tr") == today:
        return  # already written for today

    row = write_daily_summary_func()

    # append JSONL
    Path("daily_summary.jsonl").open("a", encoding="utf-8").write(json.dumps(row, ensure_ascii=False) + "\n")

    state["last_date_tr"] = today
    save_state(state)
