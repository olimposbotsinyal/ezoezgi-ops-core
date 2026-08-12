# settings/trailing_price_feed.py
from __future__ import annotations

import time
import threading
from typing import Any, Dict, Optional


class TrailingPriceFeed:
    """
    alarm_strateji içindeki price_map'i burada cache'leriz.
    Trailing loop sadece buradan okur.
    """
    def __init__(self):
        self._lock = threading.RLock()
        self._tickers: Dict[str, Dict[str, Any]] = {}
        self._ts: float = 0.0

    def update(self, price_map: Dict[str, Any]) -> None:
        if not isinstance(price_map, dict):
            return
        with self._lock:
            self._tickers = dict(price_map)
            self._ts = time.time()

    def get_last_price(self, symbol: str) -> Optional[float]:
        if not symbol:
            return None

        with self._lock:
            tickers = self._tickers

        # 1) direkt
        t = tickers.get(symbol)

        # 2) BTC/USDT:USDT -> BTC/USDT
        if t is None and ":" in symbol:
            t = tickers.get(symbol.split(":", 1)[0])

        # 3) BTC/USDT -> BTC/USDT:USDT
        if t is None and ":" not in symbol:
            t = tickers.get(symbol + ":USDT")

        # 4) BTCUSDT -> BTC/USDT veya BTC/USDT:USDT
        if t is None and symbol.endswith("USDT") and "/" not in symbol:
            t = tickers.get(symbol[:-4] + "/USDT") or tickers.get(symbol[:-4] + "/USDT:USDT")

        if not isinstance(t, dict):
            return None

        val = t.get("last") or t.get("close")
        if val is None:
            info = t.get("info") or {}
            if isinstance(info, dict):
                val = info.get("lastPrice") or info.get("last") or info.get("price")

        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None


TRAILING_PRICE_FEED = TrailingPriceFeed()
