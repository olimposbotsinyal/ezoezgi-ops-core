# core/risk_kill_switch.py
from __future__ import annotations
from dataclasses import dataclass
from threading import Lock
from typing import Optional, Dict, Any
import time
from config_service import ConfigService

@dataclass
class KillSwitchState:
    enabled: bool = True                 # sistem aktif mi
    kill_on: bool = False                # kill tetiklenmiş mi
    reason: str = ""
    dd_limit_pct: float = 5.0            # örn: günlük max -%5
    e0: Optional[float] = None           # gün başı equity (TR 00:00)
    last_equity: Optional[float] = None
    last_ts: float = 0.0

_LOCK = Lock()
_STATE = KillSwitchState()
_LAST_REFRESH_TS = 0.0


def refresh_from_config() -> KillSwitchState:
    """
    Config'ten kill-switch ayarlarını alır.
    Not: ConfigService.init() çağrılmamış olabilir, güvenli başlat.
    """
    global _LAST_REFRESH_TS
    now = time.time()
    if now - _LAST_REFRESH_TS < 2.0:
        return get_state()

    _LAST_REFRESH_TS = now

    try:
        if not getattr(ConfigService, "_initialized", False):
            ConfigService.init()

        # Manuel dosya değiştiyse reload
        ConfigService.hot_reload_if_changed()

        enabled = bool(ConfigService.get("risk.kill_switch_enabled", True))
        dd = float(ConfigService.get("risk.daily_dd_limit_pct", 30.0))
        dd = abs(dd)
    except Exception as e:
        enabled = True
        dd = 30.0
        # logging.warning(f"[KILL_SWITCH_CONFIG_ERR] {e}")
    with _LOCK:
        _STATE.enabled = enabled
        _STATE.dd_limit_pct = dd
        return KillSwitchState(**_STATE.__dict__)

def get_state() -> KillSwitchState:
    with _LOCK:
        return KillSwitchState(**_STATE.__dict__)

def update_equity(equity: float, *, e0: Optional[float] = None, ts: Optional[float] = None) -> KillSwitchState:
    """
    EquityService bunu çağırır.
    """
    now = ts or time.time()
    with _LOCK:
        if e0 is not None:
            _STATE.e0 = float(e0)
        _STATE.last_equity = float(equity)
        _STATE.last_ts = float(now)

        # dd kontrol
        if _STATE.enabled and _STATE.e0 and _STATE.e0 > 0:
            dd_pct = ( (_STATE.last_equity - _STATE.e0) / _STATE.e0 ) * 100.0
            if dd_pct <= -abs(_STATE.dd_limit_pct):
                _STATE.kill_on = True
                _STATE.reason = f"DAILY_DD_LIMIT dd={dd_pct:.2f}% limit=-{abs(_STATE.dd_limit_pct):.2f}%"
        return KillSwitchState(**_STATE.__dict__)

def reset_daily(e0: float) -> KillSwitchState:
    """
    TR 00:00 rollover’da çağrılır (DailySummaryManager ile birlikte).
    """
    with _LOCK:
        _STATE.e0 = float(e0)
        _STATE.kill_on = False
        _STATE.reason = ""
        return KillSwitchState(**_STATE.__dict__)

def can_open_entry() -> tuple[bool, str]:
    """
    Sinyal OPEN girişlerinde çağrılır.
    CLOSE/exit için bu fonksiyon kullanılmaz (serbest).
    """
    refresh_from_config()
    with _LOCK:
        if not _STATE.enabled:
            return True, "kill_switch_disabled"
        if _STATE.kill_on:
            return False, _STATE.reason or "kill_switch_on"
        return True, "ok"
