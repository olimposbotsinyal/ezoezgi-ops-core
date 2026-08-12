# settings/execution/equity_binance.py
import asyncio
from typing import Any, Dict, Tuple
import ccxt

def _usdt_wallet_from_balance(bal: Dict[str, Any]) -> float:
    # En stabil: total.USDT (yoksa free+used)
    total = (bal.get("total") or {}).get("USDT")
    if total is not None:
        return float(total)
    free = (bal.get("free") or {}).get("USDT") or 0.0
    used = (bal.get("used") or {}).get("USDT") or 0.0
    return float(free) + float(used)

def _unrealized_from_positions(positions: Any) -> float:
    s = 0.0
    for p in positions or []:
        upnl = p.get("unrealizedPnl")
        if upnl is None:
            info = p.get("info") or {}
            # Binance USDM raw alanlar
            upnl = info.get("unRealizedProfit") or info.get("unrealizedProfit")
        if upnl is None:
            continue
        try:
            s += float(upnl)
        except Exception:
            pass
    return s

async def snapshot_equity_binance_usdm(ex: ccxt.Exchange) -> Tuple[float, Dict[str, float]]:
    """
    returns: (equity, meta)
      equity = wallet_usdt + unrealized_usdt
    """
    bal = await asyncio.to_thread(ex.fetch_balance, {"type": "future"})
    wallet_usdt = _usdt_wallet_from_balance(bal)

    positions = await asyncio.to_thread(ex.fetch_positions)
    unrealized_usdt = _unrealized_from_positions(positions)

    equity = wallet_usdt + unrealized_usdt
    return equity, {"wallet_usdt": wallet_usdt, "unrealized_usdt": unrealized_usdt}
