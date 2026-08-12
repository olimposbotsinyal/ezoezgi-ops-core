# core/symbol_resolver.py
from __future__ import annotations

from dataclasses import dataclass
import asyncio
import time
from typing import Optional, Dict, Tuple

# Burada "core_symbol" standardı: "BTCUSDT" (upper, separator yok)

def normalize_core_symbol(symbol: str) -> str:
    if symbol is None:
        return ""
    s = str(symbol).strip().upper()

    # Yaygın ayraçları temizle
    s = s.replace("/", "").replace("-", "").replace("_", "").replace(":", "")

    # Bazı borsalar futures postfix vs. ekleyebiliyor; burada minimal davranıyoruz.
    # Gerçek borsa formatına çevirme "to_exchange_symbol"da.
    return s


@dataclass(frozen=True)
class MetaKey:
    user_id: str
    exchange: str
    market_type: str
    core_symbol: str


class SymbolResolver:
    """
    - normalize: any -> core_symbol
    - meta: (user, exchange, market_type, core_symbol) -> dict  (TTL cache + in-flight dedupe)
    """
    def __init__(self, meta_provider_callable, ttl_seconds: int = 60):
        # meta_provider_callable(exchange, symbol, user_id?, market_type?) gibi bir fonksiyon olacak
        self._meta_provider = meta_provider_callable
        self._ttl = int(ttl_seconds)

        self._cache: Dict[MetaKey, Tuple[float, dict]] = {}
        self._inflight: Dict[MetaKey, asyncio.Task] = {}  # <-- kritik
        self._lock = asyncio.Lock()

    @staticmethod
    def normalize(symbol_any: str) -> str:
        return normalize_core_symbol(symbol_any)

    async def get_meta(
            self,
            exchange: str,
            symbol_any: str,
            user_id: Optional[str] = None,
            market_type: str = "futures",
    ) -> dict:
        user_id_s = str(user_id or "default")
        ex = str(exchange or "").lower().strip()
        mt = str(market_type or "futures").lower().strip()
        core_symbol = self.normalize(symbol_any)

        key = MetaKey(user_id=user_id_s, exchange=ex, market_type=mt, core_symbol=core_symbol)
        now = time.time()

        task: Optional[asyncio.Task] = None
        result: dict = {}

        async with self._lock:
            cached = self._cache.get(key)
            if cached:
                exp, val = cached
                if exp >= now and isinstance(val, dict):
                    return val

            task = self._inflight.get(key)

            if task is None:
                async def _run() -> dict:
                    # exchange-specific sembol üret (alias + symbol_for_exchange)
                    try:
                        from config_service import ConfigService
                        aliases = ConfigService.get(f"exchange_profiles.{ex}.symbol_aliases", {}) or {}
                    except Exception:
                        aliases = {}

                    aliased_core = aliases.get(core_symbol, core_symbol)

                    # execution_settings_plan ile aynı dönüşüm mantığı (küçük kopya, döngüsel importı önler)
                    base = str(aliased_core).upper().strip().replace("/", "").replace("-", "").replace("_", "").replace(
                        ":", "")
                    if ex == "mexc":
                        symbol_ex = f"{base.replace('USDT', '')}_USDT"
                    elif ex == "bitget":
                        symbol_ex = f"{base}_UMCBL"
                    elif ex == "okx":
                        symbol_ex = base.replace("USDT", "-USDT")
                    else:
                        symbol_ex = base  # binance/bybit default

                    data = self._meta_provider(exchange=ex, symbol=symbol_ex)
                    return data if isinstance(data, dict) else {}

                task = asyncio.create_task(_run())
                self._inflight[key] = task

        # Lock DIŞINDA await
        try:
            # task artık kesin var; ama type-checker için:
            if task is None:
                return {}
            result = await task
            if not isinstance(result, dict):
                result = {}
        except Exception:
            # Meta fetch patlarsa cache'e boş basmayalım (istersen basabiliriz)
            result = {}
            raise
        finally:
            async with self._lock:
                # cleanup: sadece aynı task ise kaldır
                cur = self._inflight.get(key)
                if cur is task:
                    if key in self._inflight:
                        del self._inflight[key]

                # Başarılı/başarısız: burada policy seçebilirsin.
                # Ben başarısızlıkta da kısa TTL ile {} cache’liyorum ki stampede olmasın.
                self._cache[key] = (time.time() + self._ttl, result)

        return result
