# settings/execution/equity_runner_binance.py
import asyncio
from typing import Dict, Any, Tuple, List

from settings.execution.user_repo import list_active_users_for_exchange, get_user_binance_keys
from settings.execution.binance_client_factory import get_binance_usdm_client  # sen koydun dedin
from settings.execution.equity_binance import snapshot_equity_binance_usdm

async def snapshot_global_equity_binance() -> Tuple[float, List[Dict[str, Any]]]:
    """
    returns:
      global_equity (sum)
      breakdown: [{user_id, equity, wallet_usdt, unrealized_usdt}, ...]
    """
    users = list_active_users_for_exchange("binance")

    breakdown: List[Dict[str, Any]] = []
    total = 0.0

    for u in users:
        uid = int(u["user_id"])
        keys = get_user_binance_keys(uid)
        if not keys:
            continue

        ex = get_binance_usdm_client(
            api_key=keys["api_key"],
            secret_key=keys["secret_key"],
            cache_key=str(uid),
        )

        try:
            eq, meta = await snapshot_equity_binance_usdm(ex)
            total += float(eq)
            breakdown.append({"user_id": uid, "equity": float(eq), **meta})
        except Exception as e:
            breakdown.append({"user_id": uid, "error": str(e)})

        # rate-limit dostu küçük bekleme
        await asyncio.sleep(0.05)

    return total, breakdown
