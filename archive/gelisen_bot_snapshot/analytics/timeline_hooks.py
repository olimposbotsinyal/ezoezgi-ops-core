# analytics/timeline_hooks.py
from __future__ import annotations
import logging
from typing import Dict, Any
from analytics.user_trade_recorder import append_user_trade_event

logger = logging.getLogger(__name__)

def on_sl_move(user_id: int, exchange: str, symbol_key: str, new_sl: float, reason: str, state: Dict[str, Any]):
    """
    SL değiştiğinde hem ACTIVE_OPEN_POSITIONS state’ini günceller
    hem user rapor jsonl’ye SL_MOVE event yazar.
    state: ACTIVE_OPEN_POSITIONS[(user_id, exchange, symbol_key)]
    """
    try:
        # 1) State güncelle: plan_summary.sl_price ve stop_price_effective
        if isinstance(state, dict):
            ps = state.get("plan_summary") or {}
            try:
                ps["sl_price"] = float(new_sl)
            except Exception:
                pass
            state["plan_summary"] = ps
            try:
                state["stop_price_effective"] = float(new_sl)
            except Exception:
                pass

        # 2) User rapor: SL_MOVE event
        append_user_trade_event(int(user_id), exchange, {
            "event": "SL_MOVE",
            "symbol_core": symbol_key,
            "new_sl": float(new_sl),
            "reason": str(reason or "TRAIL"),
        })
    except Exception as e:
        logger.warning(f"[SL_MOVE_HOOK_ERR] user={user_id} ex={exchange} sym={symbol_key} err={e}")
