# settings/execution/equity_service.py
import asyncio
from typing import Optional
from datetime import datetime, timezone

from settings.execution.daily_summary_manager import DailySummaryManager
from settings.execution.equity_runner_binance import snapshot_global_equity_binance
from core.risk_kill_switch import refresh_from_config, update_equity, reset_daily
import contextlib

class EquityService:
    def __init__(self, daily_path: str, interval_sec: float = 60.0):
        self.daily = DailySummaryManager(path_jsonl=daily_path)
        self.interval_sec = float(interval_sec)
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    async def run_forever(self):
        while not self._stop.is_set():
            try:
                refresh_from_config()

                equity, breakdown = await snapshot_global_equity_binance()

                # settings/execution/equity_service.py (içeride)
                before_day = self.daily.state.day_key if self.daily.state else None
                self.daily.on_equity_tick(equity, dt_utc=datetime.now(timezone.utc))
                after_day = self.daily.state.day_key if self.daily.state else None

                if after_day is not None and before_day != after_day:
                    reset_daily(self.daily.state.e0)

                update_equity(equity, e0=(self.daily.state.e0 if self.daily.state else None))

            except Exception as e:
                print("[EQUITY_SERVICE_ERR]", e)

            await asyncio.sleep(self.interval_sec)

    def start(self):
        if self._task and not self._task.done():
            return self._task
        self._stop.clear()
        self._task = asyncio.create_task(self.run_forever(), name="equity_service_task")
        return self._task

    async def stop(self):
        self._stop.set()
        if self._task and not self._task.done():
            # Önce nazikçe bitmesini bekle
            try:
                await asyncio.wait_for(self._task, timeout=5)
                return
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                return
            except Exception:
                return

            # Timeout olduysa cancel et
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
