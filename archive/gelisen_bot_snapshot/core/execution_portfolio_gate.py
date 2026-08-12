# core/execution_portfolio_gate.py
from __future__ import annotations
from typing import Dict, Tuple, Any

# core/execution_portfolio_gate.py
from core.symbol_resolver import normalize_core_symbol

def normalize_symbol_for_key(symbol: str) -> str:
    return normalize_core_symbol(symbol)


def can_open_position(
    *,
    plan,  # ExecutionPlan
    active_open_positions: Dict[Tuple[int, str, str], Dict[str, Any]],
    maks_emir: int
) -> tuple[bool, str]:

    key_symbol = normalize_symbol_for_key(plan.symbol_core)
    key = (plan.user_id, plan.exchange, key_symbol)

    if key in active_open_positions:
        return False, "already_active_same_symbol"

    if maks_emir is not None:
        try:
            limit = int(float(maks_emir))
        except Exception:
            limit = 0
        if limit > 0:
            # user+exchange toplam aktif say
            cnt = sum(1 for (uid, ex, _sym) in active_open_positions.keys()
                      if uid == plan.user_id and ex == plan.exchange)
            if cnt >= limit:
                return False, f"maks_emir_limit_reached({cnt}/{limit})"

    return True, "ok"
