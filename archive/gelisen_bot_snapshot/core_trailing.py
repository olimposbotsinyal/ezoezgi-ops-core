# core_trailing.py (GÜNCEL TAM VERSİYON)

import asyncio
import inspect
import ccxt
from logger_config import setup_logging
from settings.execution.mexc_al_sat import build_mexc_dynamic_update_sl_adapter
from settings.trailing_manager import TRAILING_MANAGER, LoopOptions

from settings.execution.binance_al_sat import build_binance_dynamic_update_sl_adapter
from settings.execution.bitget_al_sat import build_bitget_dynamic_update_sl_adapter, TRAILING_POSITION_OWNERS_BITGET
from settings.execution.bybit_al_sat import build_bybit_dynamic_update_sl_adapter, TRAILING_POSITION_OWNERS_BYBIT
from settings.execution.okx_al_sat import build_okx_dynamic_update_sl_adapter, TRAILING_POSITION_OWNERS_OKX
from settings.execution.mexc_al_sat import TRAILING_POSITION_OWNERS as TRAILING_POSITION_OWNERS_MEXC
from settings.execution.binance_al_sat import TRAILING_POSITION_OWNERS_BINANCE
import logging
from settings.execution.symbol_meta_provider import get_symbol_meta as provider_get_symbol_meta
from core.symbol_resolver import normalize_core_symbol

logger = setup_logging('trailing_supervisor_all')


# Tek bir birleşik owner resolver
def resolve_owner(position_id: int):
    if position_id in TRAILING_POSITION_OWNERS_MEXC:
        return TRAILING_POSITION_OWNERS_MEXC[position_id]
    if position_id in TRAILING_POSITION_OWNERS_BINANCE:
        return TRAILING_POSITION_OWNERS_BINANCE[position_id]
    if position_id in TRAILING_POSITION_OWNERS_BITGET:
        return TRAILING_POSITION_OWNERS_BITGET[position_id]
    if position_id in TRAILING_POSITION_OWNERS_BYBIT:
        return TRAILING_POSITION_OWNERS_BYBIT[position_id]
    if position_id in TRAILING_POSITION_OWNERS_OKX:
        return TRAILING_POSITION_OWNERS_OKX[position_id]
    return None


def _to_ccxt_symbol(exchange: str, any_symbol: str) -> str:
    core = normalize_core_symbol(any_symbol)
    base = core[:-4] if core.endswith("USDT") else core

    # Hepsini aynı standarda çekiyoruz (şimdilik)
    # Not: OKX bazı marketlerde farklı isteyebilir; resolver'ı sonra ekleriz.
    return f"{base}/USDT:USDT"


def _build_price_fetchers():
    # ccxt instance’ları her çağrıda yaratmak maliyetli, basit cache:
    cache = {}

    def mexc_price(sym):
        try:
            if 'mexc' not in cache:
                cache['mexc'] = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
            return float(cache['mexc'].fetch_ticker(sym)['last'])
        except Exception as e:
            logging.error(f"Hata: {e}")
            return None

    def binance_price(sym):
        try:
            if 'binance' not in cache:
                cache['binance'] = ccxt.binanceusdm({'enableRateLimit': True, 'options': {'defaultType': 'future'}})
                # binanceusdm ticker sembolü çoğunlukla "BTC/USDT:USDT" formatını kabul eder
            return float(cache['binance'].fetch_ticker(sym)['last'])

        except Exception as e:
            logging.error(f"Hata: {e}")

            return None

    def bitget_price(sym):
        try:
            if 'bitget' not in cache:
                cache['bitget'] = ccxt.bitget({'enableRateLimit':True, 'options':{'defaultType':'swap'}})
            return float(cache['bitget'].fetch_ticker(sym)['last'])
        except Exception as e:
            logging.error(f"Hata: {e}")
            return None

    def bybit_price(sym):
        try:
            if 'bybit' not in cache:
                cache['bybit'] = ccxt.bybit({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
            return float(cache['bybit'].fetch_ticker(sym)['last'])
        except Exception as e:
            logging.error(f"Hata: {e}")
            return None

    def okx_price(sym):
        try:
            if 'okx' not in cache:
                cache['okx'] = ccxt.okx({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
            return float(cache['okx'].fetch_ticker(sym)['last'])
        except Exception as e:
            logging.error(f"Hata: {e}")
            return None

    return {
        'mexc': mexc_price,
        'binance': binance_price,
        'bitget': bitget_price,
        'bybit': bybit_price,
        'okx': okx_price
    }

async def _call_adapter(adapter, pid: int, symbol_core: str, new_sl: float) -> bool:
    """
    adapter sync olabilir (mexc/binance), async olabilir (bitget/bybit/okx).
    Doğru şekilde çalıştırıp bool döndürür.
    """
    if inspect.iscoroutinefunction(adapter):
        return bool(await adapter(pid, symbol_core, float(new_sl)))
    return bool(await asyncio.to_thread(adapter, pid, symbol_core, float(new_sl)))


class UnifiedTrailingSupervisor:
    def __init__(self, interval=3.0):
        self.interval = interval
        self.running = False
        self.task = None
        self.adapters = {}
        self.price_fetchers = _build_price_fetchers()
        self._init_adapters()

    def _init_adapters(self):
        # Adapter fonksiyonları asenkron döndüren/çağrılan
        self.adapters['mexc'] = build_mexc_dynamic_update_sl_adapter()
        self.adapters['binance'] = build_binance_dynamic_update_sl_adapter()
        self.adapters['bitget'] = build_bitget_dynamic_update_sl_adapter()
        self.adapters['bybit'] = build_bybit_dynamic_update_sl_adapter()
        self.adapters['okx'] = build_okx_dynamic_update_sl_adapter()

    def start(self, loop):
        if self.running:
            return
        self.running = True
        self.task = loop.create_task(self._loop(), name="unified_trailing_loop")
        logger.info("[UNIFIED_TRAIL_SUPERVISOR_STARTED]")

    async def _loop(self):
        while self.running:
            logger.info(f"[UNIFIED_TRAIL_HEARTBEAT] positions={len(TRAILING_MANAGER.positions)}")
            try:
                for pid, st in list(TRAILING_MANAGER.positions.items()):
                    owner = resolve_owner(pid)

                    # owner map yoksa da trailing çalışabilsin diye boş dict'e düş
                    if not owner:
                        owner = {}

                    if not isinstance(owner, dict):
                        owner = {}

                    exchange = (owner.get("exchange") or st.get("exchange") or "").lower().strip()

                    if not exchange:
                        continue

                    pf = self.price_fetchers.get(exchange)
                    if not pf:
                        continue

                    ccxt_sym = _to_ccxt_symbol(exchange, st.get("symbol", ""))
                    price = pf(ccxt_sym)
                    if price is None:
                        continue
                    logger.info(
                        f"[TRAIL_PRICE] pid={pid} ex={exchange} st_sym={st.get('symbol')} ccxt_sym={ccxt_sym} price={price}")

                    TRAILING_MANAGER.update_price(pid, price)
                    new_sl = TRAILING_MANAGER.calc_new_sl(pid)
                    if not new_sl:
                        continue

                    core_sym = normalize_core_symbol(st.get("symbol", ""))
                    if not core_sym:
                        continue

                    meta = provider_get_symbol_meta(exchange=exchange, symbol=core_sym) or {}
                    opts = LoopOptions(interval=self.interval)

                    if not TRAILING_MANAGER.should_update_sl(pid, float(new_sl), meta, opts=opts):
                        continue

                    adapter = self.adapters.get(exchange)
                    if not adapter:
                        continue

                    ok = await _call_adapter(adapter, pid, core_sym, float(new_sl))
                    if ok:
                        TRAILING_MANAGER.mark_sl_updated(pid, float(new_sl))

            except Exception as e:
                logger.error(f"[UNIFIED_TRAIL_LOOP_ERR] {e}", exc_info=True)
            await asyncio.sleep(self.interval)


UNIFIED_TRAILING_SUPERVISOR = UnifiedTrailingSupervisor()
