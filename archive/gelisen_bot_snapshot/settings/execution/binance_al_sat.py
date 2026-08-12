# settings/execution/binance_al_sat.py

from typing import Dict, List, Optional, Any, Mapping, Literal, cast
from data.olimpos_data import db_operation
from logger_config import setup_logging
from settings.execution_plan_types import ExecutionPlan
import ccxt
from ccxt.base.errors import InsufficientFunds

from config.constants import BOT_TOKEN
import time
import asyncio
import math
from telegram import Bot
from datetime import datetime
import traceback
from typing import Callable
from analytics.timeline_hooks import on_sl_move
from signal_merkezi import ACTIVE_OPEN_POSITIONS, normalize_symbol_for_key

CcxtCreateOrder = Callable[..., Dict[str, Any]]

def _ccxt_create_order(ex: Any) -> CcxtCreateOrder:
    # CCXT stub'ları dar olduğu için burada Any'ye düşüyoruz.
    return cast(CcxtCreateOrder, ex.create_order)

def _ccxt_create_market_order(ex: Any) -> Callable[..., Dict[str, Any]]:
    return cast(Callable[..., Dict[str, Any]], ex.create_market_order)

def _ccxt_create_limit_order(ex: Any) -> Callable[..., Dict[str, Any]]:
    return cast(Callable[..., Dict[str, Any]], ex.create_limit_order)

TRAILING_POSITION_OWNERS_BINANCE = {}
_BINANCE_EX_CLIENT_CACHE = {}
_BINANCE_PUBLIC_EX = None
_BINANCE_META_CACHE = {}  # symbol -> {"ts":..., "data":...}
OrderSideStr = Literal["buy", "sell"]
OrderTypeStr = Literal[
    "limit", "market",
    "STOP", "TAKE_PROFIT",
    "STOP_MARKET", "TAKE_PROFIT_MARKET",
    "stop", "take_profit",
    "stop_market", "take_profit_market",
]


# Performans ve hata izleme ayarları
logger = setup_logging('Binance_al_sat')


def _to_binance_ccxt_symbol(symbol_core: str) -> str:
    s = (symbol_core or "").strip().upper().replace("/", "").replace(":", "")
    if s.endswith("USDT") and len(s) > 4:
        base = s[:-4]
        return f"{base}/USDT:USDT"
    return s

def _to_symbol_core(any_symbol: str) -> str:
    """
    Her türlü formatı core'a çeker: BTCUSDT
    - "BTC/USDT:USDT" -> "BTCUSDT"
    - "BTC/USDT" -> "BTCUSDT"
    - "BTCUSDT:USDT" -> "BTCUSDT"
    - "BTCUSDT" -> "BTCUSDT"
    """
    s = (any_symbol or "").strip().upper()
    if not s:
        return ""

    # remove perp suffix
    s = s.replace(":USDT", "").replace(":USD", "")

    # slash format
    if "/" in s:
        base = s.split("/", 1)[0].strip()
        quote = s.split("/", 1)[1].strip()
        quote = quote.replace("USDT", "USDT")  # no-op, clarity
        # çoğu kullanım USDT
        return f"{base}USDT"

    # already like BTCUSDT
    s = s.replace("/", "").replace(":", "")
    return s


async def _binance_has_any_open_position(ex: ccxt.Exchange, symbol: str) -> tuple[bool, float]:
    try:
        positions = await asyncio.to_thread(ex.fetch_positions, [symbol])
        for p in positions or []:
            if p.get("symbol") != symbol:
                continue
            amt = _extract_position_amount(p)
            if abs(amt) > 0:
                return True, float(amt)
    except Exception:
        pass
    return False, 0.0


async def set_leverage(symbol, leverage, api_key, secret_key):
    """
    Belirli bir sembol için kaldıraç ayarlar

    Args:
        symbol (str): İşlem sembolü (örn. BTCUSDT)
        leverage (int): Kaldıraç oranı
        api_key (str): API anahtarı
        secret_key (str): Gizli anahtar

    Returns:
        dict: Ayar sonucu
    """
    try:
        exchange = create_binance_exchange(api_key=api_key, secret_key=secret_key, exchange_type="futures")
        await _ensure_time_sync(exchange)

        # Kaldıraç ayarını yap
        # Binance USDT-M Futures için doğru metodu kullan
        result = exchange.set_leverage(leverage, symbol)

        logger.info(f"{symbol} için kaldıraç {leverage} olarak ayarlandı")
        return {
            'success': True,
            'message': f"Kaldıraç {leverage} olarak güncellendi",
            'result': result
        }

    except ccxt.NetworkError as e:
        logger.error(f"Ağ hatası: {e}")
        return {'success': False, 'message': str(e)}
    except ccxt.ExchangeError as e:
        logger.error(f"Borsa hatası: {e}")
        return {'success': False, 'message': str(e)}
    except Exception as e:
        logger.error(f"Beklenmedik hata: {e}")
        return {'success': False, 'message': str(e)}


async def set_margin_type(symbol, margin_type, api_key, secret_key):
    """
    Margin tipini ayarlar (ISOLATED veya CROSS).
    Ayrıca "CROSSED" gibi varyantları CROSS'a normalize eder.
    """
    try:
        exchange = create_binance_exchange(api_key=api_key, secret_key=secret_key, exchange_type="futures")
        await _ensure_time_sync(exchange)

        mt = (str(margin_type or "").upper().strip())
        if mt in ("CROSSED", "CROSS", "CROSS_MARGIN"):
            mt = "CROSS"
        elif mt in ("ISOLATED", "ISO"):
            mt = "ISOLATED"
        else:
            # bilinmeyen gelirse logla ama yine de üst-case gönder
            logger.warning(f"[BINANCE_MARGIN_UNKNOWN] symbol={symbol} margin_type={margin_type} -> using={mt or 'CROSS'}")
            mt = mt or "CROSS"

        result = exchange.set_margin_mode(mt, symbol)

        logger.info(f"{symbol} için margin tipi {mt} olarak ayarlandı")
        return {
            'success': True,
            'message': f"Margin tipi {mt} olarak güncellendi",
            'result': result
        }

    except ccxt.NetworkError as e:
        logger.error(f"Ağ hatası: {e}")
        return {'success': False, 'message': str(e)}
    except ccxt.ExchangeError as e:
        logger.error(f"Borsa hatası: {e}")
        return {'success': False, 'message': str(e)}
    except Exception as e:
        logger.error(f"Beklenmedik hata: {e}")
        return {'success': False, 'message': str(e)}


def get_api_key_sync(user_id: int, exchange: str):
    query = """
        SELECT api_key, secret_key, passphrase
        FROM api_key
        WHERE user_id = ? AND exchange = ?
    """
    params = (user_id, exchange)
    result = db_operation(query, params, operation='select', fetch=True, fetch_all=False)
    if result and isinstance(result, (list, tuple)) and len(result) >= 2:
        return {
            "api_key": result[0],
            "secret_key": result[1],
            "passphrase": (result[2] if len(result) >= 3 else None),
        }
    return None


async def get_api_key(user_id: int, exchange: str):
    # async isteyen yerlere uyum için
    return await asyncio.to_thread(get_api_key_sync, user_id, exchange)


def create_binance_exchange(
        api_key: str,
        secret_key: str,
        exchange_type: str = 'futures'
) -> ccxt.Exchange:
    """
    Binance exchange örneği oluşturur.
    - Futures için binanceusdm kullanır.
    - Timestamp drift (-1021) için CCXT time-diff düzeltmesini açar.
    """
    try:
        common_opts = cast(Any, {
            'apiKey':api_key,
            'secret':secret_key,
            'enableRateLimit':True,
            'options':{
                'defaultType':('future' if exchange_type.lower() == 'futures' else 'spot'),
                'adjustForTimeDifference':True,
                'recvWindow':10000,
            }
        })

        if exchange_type.lower() == 'futures':
            exchange = ccxt.binanceusdm(common_opts)
        elif exchange_type.lower() == 'spot':
            exchange = ccxt.binance(common_opts)
        else:
            raise ValueError(f"Geçersiz exchange tipi: {exchange_type}")

        # Not: load_time_difference async değil; thread içinde çağıracağız (aşağıda wrapper).
        return exchange

    except Exception as e:
        logger.error(f"Exchange oluşturma hatası: {e}")
        raise

async def _ensure_time_sync(ex: ccxt.Exchange) -> None:
    """
    CCXT Binance time diff'i yükler.
    Ağ çağrısı içerdiği için thread'e atıyoruz.
    """
    try:
        await asyncio.to_thread(ex.load_time_difference)
    except Exception as e:
        logger.warning(f"[BINANCE_TIME_SYNC_WARN] {e}")


def get_symbol_meta_binance(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Binance USDT-M futures symbol meta:
      - amount_step (qty step)
      - min_amount
      - price_step
      - decimals

    Cache key: CORE (BTCUSDT)
    CCXT market lookup: CCXT (BTC/USDT:USDT)
    """
    global _BINANCE_PUBLIC_EX
    try:
        core = _to_symbol_core(symbol)
        if not core:
            return None

        now = time.time()
        cached = _BINANCE_META_CACHE.get(core)
        if cached and now - cached["ts"] < 60:
            return cached["data"]

        ccxt_symbol = _to_binance_ccxt_symbol(core)

        if _BINANCE_PUBLIC_EX is None:
            _BINANCE_PUBLIC_EX = ccxt.binanceusdm({
                "enableRateLimit": True,
                "options": {"defaultType": "future"}
            })
            _BINANCE_PUBLIC_EX.load_markets()

        m = _BINANCE_PUBLIC_EX.market(ccxt_symbol)

        price_step = None
        amount_step = None

        info = m.get("info") or {}
        filters = info.get("filters") or []
        for f in filters:
            if f.get("filterType") == "PRICE_FILTER":
                try:
                    price_step = float(f.get("tickSize"))
                except (TypeError, ValueError):
                    pass
            if f.get("filterType") in ("LOT_SIZE", "MARKET_LOT_SIZE"):
                try:
                    amount_step = float(f.get("stepSize"))
                except (TypeError, ValueError):
                    pass

        # fallback
        if not price_step:
            pprec = (m.get("precision") or {}).get("price")
            if isinstance(pprec, int):
                price_step = 10 ** (-pprec)
            else:
                price_step = 0.01

        if not amount_step:
            aprec = (m.get("precision") or {}).get("amount")
            if isinstance(aprec, int):
                amount_step = 10 ** (-aprec)
            else:
                amount_step = 0.001

        price_decimals = int(round(abs(math.log10(price_step)))) if price_step < 1 else 0
        amount_decimals = int(round(abs(math.log10(amount_step)))) if amount_step < 1 else 0

        min_amount = (m.get("limits") or {}).get("amount", {}).get("min")
        if not min_amount:
            for f in filters:
                if f.get("filterType") in ("LOT_SIZE", "MARKET_LOT_SIZE"):
                    try:
                        min_amount = float(f.get("minQty"))
                        break
                    except (TypeError, ValueError):
                        pass
        if not min_amount:
            min_amount = amount_step

        meta = {
            "ccxt_symbol": ccxt_symbol,
            "core_symbol": core,
            "price_step": float(price_step),
            "amount_step": float(amount_step),
            "price_decimals": int(price_decimals),
            "amount_decimals": int(amount_decimals),
            "min_amount": float(min_amount),
        }

        _BINANCE_META_CACHE[core] = {"ts": now, "data": meta}
        return meta

    except Exception as e:
        logger.error(f"[BINANCE_META_ERR] sym={symbol} err={e}", exc_info=True)
        return None


def _floor_to_step(x: float, step: float) -> float:
    if step <= 0:
        return x
    return math.floor(x / step) * step


def tr_error_message(error: str | None, error_code: int | None, raw: str = "") -> str:
    e = (error or "").upper()
    code = int(error_code) if error_code is not None else None
    raw_l = (raw or "").lower()

    if code == -2019 or e in ("INSUFFICIENT_MARGIN", "INSUFFICIENT_FUNDS") or "margin is insufficient" in raw_l:
        return "Yetersiz marjin: Bu işlem için kullanılabilir USDT marjini yetmedi. (Binance -2019)"
    if code == -4061 or "positionside" in raw_l:
        return "Hesap Hedge modda: positionSide zorunlu. (Binance -4061)"
    if code == -1021 or "timestamp" in raw_l or "nonce" in raw_l:
        return "Zaman senkron hatası: Binance saat farkı nedeniyle istek reddedildi. (Binance -1021)"
    return "İşlem reddedildi: Borsa isteği başarısız oldu."


async def binance_execute_plan(plan: ExecutionPlan, user: dict) -> Dict[str, Any]:
    """
    ExecutionPlan'ı Binance USDT-M futures üzerinde uygular.

    MOD (senin istediğin):
      - OPEN: sadece MARKET
      - TP: sadece LIMIT tabanlı (TAKE_PROFIT stop-limit; olmazsa LIMIT reduceOnly)
      - SL: sadece LIMIT tabanlı (STOP stop-limit; olmazsa BOT_MANAGED soft-fail)

    Not:
      - Bu fonksiyon Algo API kullanmaz.
      - TP/SL yerleştirmeyi _place_tp_with_fallback ve create_stop_loss_order yapar
        (onların da limit-only olacak şekilde ayrıca güncellenecek).
    """

    async def _binance_margin_snapshot(ex) -> dict:
        try:
            bal = await asyncio.to_thread(ex.fetch_balance)
            usdt = (bal.get("USDT") or {}) if isinstance(bal, dict) else {}
            # ccxt sürümlerine göre free/used/total değişebiliyor
            return {
                "usdt_free":usdt.get("free"),
                "usdt_used":usdt.get("used"),
                "usdt_total":usdt.get("total"),
                "raw_keys":list(usdt.keys())[:20],
            }
        except Exception as e1:
            return {"err":str(e1)}

    try:
        api_key = (user.get("api_key") or "").strip()
        secret_key = (user.get("secret_key") or "").strip()
        if not api_key or not secret_key:
            return {"success":False, "error":"binance api_key/secret_key yok"}

        # ✅ core & ccxt sembol ayrımı
        symbol_core = _to_symbol_core(plan.symbol_exchange or "")
        symbol = _to_binance_ccxt_symbol(symbol_core)  # ccxt format
        plan_side = str(plan.side or "").upper().strip()

        if not symbol_core:
            return {"success":False, "error":"plan.symbol_exchange boş"}

        if plan_side not in ("LONG", "SHORT"):
            plan_side = "LONG"

        ex = create_binance_exchange(api_key=api_key, secret_key=secret_key, exchange_type="futures")
        await _ensure_time_sync(ex)

        # leverage/margin
        try:
            await set_leverage(symbol, int(plan.leverage), api_key, secret_key)
        except Exception as e:
            logger.warning(f"[BINANCE_SET_LEV_WARN] {e}")

        try:
            await set_margin_type(symbol, str(plan.margin), api_key, secret_key)
        except Exception as e:
            logger.warning(f"[BINANCE_SET_MARGIN_WARN] {e}")

        # ✅ Exchange-level guard: pozisyon varsa açma
        has_pos, amt = await _binance_has_any_open_position(ex, symbol)
        if has_pos:
            logger.warning(f"[BINANCE_OPEN_SKIP_ALREADY_IN_POSITION] sym={symbol} amt={amt} user={plan.user_id}")
            return {
                "success": False,
                "error": "ALREADY_IN_POSITION",
                "message": f"{symbol} açık pozisyon var"
            }

        # --- OPEN (MARKET) ---
        open_side: OrderSideStr = "buy" if plan_side == "LONG" else "sell"

        qty = float(plan.contracts or 0.0)
        if qty <= 0:
            return {"success": False, "error": "plan.contracts<=0"}

        snap_before = await _binance_margin_snapshot(ex)
        logger.info(f"[BINANCE_MARGIN_SNAP_BEFORE] user={plan.user_id} sym={symbol} snap={snap_before}")

        open_order = await create_smart_order(
            exchange=ex,
            symbol=symbol,
            order_type="market",
            side=open_side,
            amount=qty,
            price=None,
            params={"reduceOnly": False}
        )
        snap_after = await _binance_margin_snapshot(ex)
        logger.info(f"[BINANCE_MARGIN_SNAP_AFTER] user={plan.user_id} sym={symbol} snap={snap_after}")

        logger.info(
            f"[BINANCE_ORDER_COST_EST] notional={qty * plan.entry_price:.4f} lev={plan.leverage} est_margin={(qty * plan.entry_price) / plan.leverage:.4f}")

        # ✅ create_smart_order soft-fail dönebilir (örn. -2019)
        if isinstance(open_order, dict) and open_order.get("success") is False:
            err = open_order.get("error") or open_order.get("message") or "open_failed"
            code = open_order.get("error_code")
            raw_error = str(open_order.get("raw_error") or open_order.get("message") or err)[:500]

            msg_tr = str(open_order.get("message_tr") or "")  # create_smart_order’dan gelirse
            if not msg_tr:
                try:
                    msg_tr = tr_error_message(str(err), int(code) if code is not None else None, raw_error)
                except Exception:
                    msg_tr = "İşlem reddedildi."

            logger.warning(f"[BINANCE_OPEN_FAIL] sym={symbol} user={plan.user_id} err={err} code={code} tr={msg_tr}")

            return {
                "success":False,
                "error":str(err),
                "error_code":code,
                "message":str(open_order.get("message") or err)[:240],
                "message_tr":msg_tr,  # ✅ Türkçe üst seviyede de var
                "raw_error":raw_error,
            }

        # --- opened qty & close side (pozisyondan doğrula) ---
        opened_qty: float = qty
        close_side: OrderSideStr = "sell" if plan_side == "LONG" else "buy"

        try:
            positions = await asyncio.to_thread(ex.fetch_positions, [symbol])
            for p in positions or []:
                if p.get("symbol") != symbol:
                    continue
                p_amt = _extract_position_amount(p)
                if abs(p_amt) > 0:
                    opened_qty = abs(float(p_amt))
                    close_side = "sell" if p_amt > 0 else "buy"
                    break
        except Exception as e:
            logger.warning(f"[BINANCE_POS_FETCH_WARN] {e}")

        # --- position id / owner map ---
        pos_id = None
        try:
            pos_id = open_order.get("id") if isinstance(open_order, dict) else None
        except Exception:
            pos_id = None

        try:
            if pos_id is not None:
                pid_int = int(pos_id)
                TRAILING_POSITION_OWNERS_BINANCE[pid_int] = {
                    "user_id":int(plan.user_id),
                    "exchange":"binance",
                    # owner map'te core saklamak daha sağlıklı
                    "symbol_exchange":_to_binance_ccxt_symbol(_to_symbol_core(symbol)),
                    "symbol_core":normalize_symbol_for_key(_to_symbol_core(symbol)),
                    "side":plan_side,
                }
        except Exception as e:
            logger.warning(f"[BINANCE_OWNER_SET_ERR] pid={pos_id} err={e}")

        # TP/SL kapalıysa burada bitir
        if not bool(plan.sl_tp_emir):
            return {
                "success": True,
                "open_order": open_order,
                "position_id": pos_id,
                "opened_qty": opened_qty,
                "tp_orders": [],
                "sl_order": None
            }

        # --- Eski reduceOnly emirleri iptal et (junk temizliği) ---
        try:
            orders = await asyncio.to_thread(ex.fetch_open_orders, symbol)
            for o in orders or []:
                reduce_only = bool(
                    o.get("reduceOnly") or
                    (isinstance(o.get("info"), dict) and (o.get("info") or {}).get("reduceOnly"))
                )
                if reduce_only:
                    oid = o.get("id")
                    if oid:
                        try:
                            await asyncio.to_thread(ex.cancel_order, oid, symbol)
                            await asyncio.sleep(0.05)
                        except Exception:
                            pass
        except Exception as e:
            logger.warning(f"[BINANCE_CANCEL_OLD_TPSL_WARN] {e}")

        # --- meta ---
        meta = cast(Dict[str, Any], (get_symbol_meta_binance(symbol_core) or {}))
        amount_step = float(meta.get("amount_step") or 0.0) or 0.0
        price_step = float(meta.get("price_step") or 0.0) or 0.0
        min_amount = float(meta.get("min_amount") or 0.0) or 0.0

        # --- helpers ---
        def _tp_volume_from_struct(t: Dict[str, Any]) -> float:
            pct = t.get("percent")
            vol = t.get("volume")
            if vol is not None:
                v = float(vol)
            elif pct is not None:
                v = float(opened_qty) * (float(pct) / 100.0)
            else:
                v = float(opened_qty)

            if amount_step > 0:
                v = _floor_to_step(v, amount_step)
            return float(v)

        # -----------------------
        # TP (LIMIT-ONLY)
        # -----------------------
        tp_orders: List[Dict[str, Any]] = []
        tp_errors: List[Dict[str, Any]] = []

        for idx, t in enumerate((plan.tp_structs or []), 1):
            try:
                if not isinstance(t, dict):
                    continue

                tp_price = t.get("price")
                if tp_price is None:
                    continue

                tp_price_f = float(tp_price)
                if tp_price_f <= 0:
                    tp_errors.append({"tp": idx, "error": "tp_price<=0"})
                    continue

                tp_vol = _tp_volume_from_struct(t)
                if tp_vol <= 0:
                    tp_errors.append({"tp": idx, "error": "tp_vol<=0_after_step"})
                    continue

                if min_amount and tp_vol < float(min_amount):
                    tp_errors.append({"tp": idx, "error": f"tp_vol_below_min (amt={tp_vol} min={min_amount})"})
                    continue

                tp = await _place_tp_with_fallback(
                    exchange=ex,
                    symbol=symbol,
                    close_side=close_side,
                    amount=float(tp_vol),
                    tp_price=float(tp_price_f),
                    price_step=float(price_step),
                )
                tp_orders.append(tp)

            except Exception as e:
                tp_errors.append({"tp": idx, "error": str(e)[:240]})

        if tp_errors:
            logger.warning(f"[BINANCE_TP_PARTIAL] sym={symbol} errors={tp_errors}")

        # -----------------------
        # SL (LIMIT-ONLY)
        # -----------------------
        if plan.sl_price is None and plan.signal_stop_loss is not None:
            try:
                plan.sl_price = float(plan.signal_stop_loss)
            except Exception:
                plan.sl_price = None

        sl_order: Optional[Dict[str, Any]] = None
        if plan.sl_price is not None and float(plan.sl_price) > 0:
            try:
                sl_resp = await create_stop_loss_order(
                    exchange=ex,
                    symbol=symbol,
                    side=close_side,
                    original_amount=float(opened_qty),
                    stop_price=float(plan.sl_price),
                    remaining_position_ratio=1.0,
                    user_id=int(plan.user_id)
                )
                sl_order = sl_resp
            except Exception as e:
                logger.warning(f"[BINANCE_EXEC_SL_WARN] {e}")

        return {
            "success": True,
            "open_order": open_order,
            "position_id": pos_id,
            "opened_qty": opened_qty,
            "tp_orders": tp_orders,
            "sl_order": sl_order
        }

    except Exception as e:
        msg = str(e)

        # -2019 / insufficient margin => WARNING, traceback yok
        if ('"code":-2019' in msg) or ("Margin is insufficient" in msg):
            logger.warning(f"[BINANCE_OPEN_SKIP_NO_MARGIN] {msg[:240]}")
            return {
                "success": False,
                "error": "INSUFFICIENT_MARGIN",
                "error_code": -2019,
                "message": msg[:240],
                "raw_error": msg[:500],
            }

        logger.error(f"[BINANCE_EXEC_PLAN_ERR] {msg}", exc_info=True)
        return {"success": False, "error": msg[:500], "message": msg[:240]}



def _extract_position_amount(pos: Mapping[str, Any]) -> float:
    for k in ("contracts", "amount", "positionAmt"):
        v = pos.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass

    info = pos.get("info") or {}
    if isinstance(info, dict):
        v = info.get("positionAmt")
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass

    return 0.0


def build_binance_dynamic_update_sl_adapter():
    """
    Binance trailing SL update adapter (SYNC) - LIMIT ONLY.

    Davranış:
      1) Pozisyonu bulur (miktar + kapatma yönü)
      2) Eski reduceOnly STOP* emirlerini iptal eder (best-effort)
      3) Yeni SL'yi STOP (stop-limit) reduceOnly olarak koyar

    Not:
      - Algo kullanılmaz.
      - STOP_MARKET kullanılmaz.
      - Bu adapter SYNC'tir (core_trailing senaryosu için).
    """

    def _adapter(position_id: int, symbol: str, new_sl_price: float) -> bool:
        owner = TRAILING_POSITION_OWNERS_BINANCE.get(int(position_id))
        if not owner:
            return False

        user_id = owner.get("user_id")
        if user_id is None:
            return False

        api_info = get_api_key_sync(int(user_id), "binance")
        if not api_info:
            return False

        api_key = str(api_info.get("api_key") or "").strip()
        secret_key = str(api_info.get("secret_key") or "").strip()
        if not api_key or not secret_key:
            logger.warning(f"[BINANCE_TRAIL_NO_KEYS] pid={position_id} user={user_id}")
            return False

        # --- symbol normalize (best effort) ---
        sym_core = _to_symbol_core(symbol)
        sym = _to_binance_ccxt_symbol(sym_core)
        if not sym_core or not sym:
            return False

        # --- CCXT client cache (pozisyon miktarı için) ---
        cache_key = str(user_id)
        if cache_key not in _BINANCE_EX_CLIENT_CACHE:
            _BINANCE_EX_CLIENT_CACHE[cache_key] = create_binance_exchange(
                api_key=api_key,
                secret_key=secret_key,
                exchange_type="futures"
            )

        ex = _BINANCE_EX_CLIENT_CACHE[cache_key]

        # time sync async ama burada SYNC adapter içindeyiz -> best effort
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_ensure_time_sync(ex))
        except Exception:
            pass

        # --- 1) Pozisyonu bul (miktar + kapatma yönü) ---
        try:
            positions = ex.fetch_positions([sym])
        except Exception as e:
            logger.error(f"[BINANCE_TRAIL_POS_FETCH_ERR] pid={position_id} user={user_id} sym={sym} err={e}")
            return False

        pos_amt: float = 0.0
        close_side: OrderSideStr = "sell"

        for p in positions or []:
            if p.get("symbol") != sym:
                continue
            amt = _extract_position_amount(p)
            if abs(amt) > 0:
                pos_amt = abs(float(amt))
                close_side = cast(OrderSideStr, ("sell" if amt > 0 else "buy"))
                break

        if pos_amt <= 0:
            logger.warning(f"[BINANCE_TRAIL_NO_POS] pid={position_id} user={user_id} sym={sym}")
            return False

        # --- 2) Eski SL emirlerini iptal et (reduceOnly STOP*) ---
        try:
            orders = ex.fetch_open_orders(sym)
            for o in orders or []:
                try:
                    reduce_only = bool(
                        o.get("reduceOnly") or
                        (isinstance(o.get("info"), dict) and (o.get("info") or {}).get("reduceOnly"))
                    )
                    otype = str(o.get("type", "")).upper()
                    if reduce_only and otype.startswith("STOP"):
                        oid = o.get("id")
                        if oid:
                            try:
                                ex.cancel_order(oid, sym)
                                time.sleep(0.05)
                            except Exception:
                                pass
                except Exception:
                    continue
        except Exception:
            pass

        tick_fallback = (abs(float(new_sl_price)) * 0.0005) or 0.0001
        trigger: float = _round_to_step(float(new_sl_price), tick_fallback)

        # --- 3) Yeni SL'yi STOP_MARKET ile koy (server-side) ---
        try:
            meta = get_symbol_meta_binance(sym_core) or {}
            meta = cast(Dict[str, Any], meta)
            price_step = float(meta.get("price_step") or 0.0) or 0.0

            tick = float(price_step or 0.0) or (abs(float(new_sl_price)) * 0.0005) or 0.0001
            trigger = _round_to_step(float(new_sl_price), tick)

            o = ex.create_order(
                sym,
                "STOP_MARKET",
                close_side,
                float(pos_amt),
                None,
                {
                    "stopPrice": float(trigger),
                    "reduceOnly": True,
                    "workingType": "MARK_PRICE",
                }
            )

            logger.info(
                f"[BINANCE_TRAIL_UPDATED_STOP_MARKET] pid={position_id} user={user_id} sym={sym} "
                f"side={close_side} amt={pos_amt} stop={trigger} id={getattr(o,'get',lambda _k:None)('id')}"
            )
            return True

        except Exception as e:
            # Native fapi fallback (SYNC)
            try:
                if not getattr(ex, "markets", None) or (sym not in (getattr(ex, "markets", {}) or {})):
                    ex.load_markets()
                m = ex.market(sym)
                req = {
                    "symbol": m["id"],
                    "side": close_side.upper(),
                    "type": "STOP_MARKET",
                    "quantity": ex.amount_to_precision(sym, float(pos_amt)),
                    "stopPrice": ex.price_to_precision(sym, float(trigger)),
                    "reduceOnly": "true",
                    "workingType": "MARK_PRICE",
                }
                o2 = cast(Any, ex).fapiPrivatePostOrder(req)
                logger.info(
                    f"[BINANCE_TRAIL_UPDATED_STOP_MARKET_NATIVE] pid={position_id} user={user_id} sym={sym} "
                    f"side={close_side} amt={pos_amt} stop={trigger} id={getattr(o2,'get',lambda _k:None)('orderId')}"
                )
                return True
            except Exception:
                logger.error(
                    f"[BINANCE_TRAIL_STOP_MARKET_ERR] pid={position_id} user={user_id} sym={sym} err={e}",
                    exc_info=True
                )
                return False

    return _adapter


class TelegramBotManager:
    _instance = None
    _initialized = False
    _bot = None

    def __init__(self):
        if not TelegramBotManager._initialized:
            self._initialize()

    @classmethod
    def _initialize(cls):
        if not cls._initialized:
            try:
                cls._bot = Bot(BOT_TOKEN)
                cls._initialized = True
                logger.info("✅ Telegram Bot başarıyla başlatıldı")
            except Exception as e:
                logger.error(f"❌ Telegram Bot başlatma hatası: {str(e)}")
                cls._initialized = False

    @classmethod
    async def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def send_binance_notification(self, chat_id: int, text: str) -> bool:
        try:
            if not self._bot:
                return False
            await asyncio.to_thread(self._bot.send_message, chat_id=chat_id, text=text)
            return True
        except Exception as e:
            logger.error(f"[TG_SEND_ERR] chat_id={chat_id} err={e}")
            return False



async def send_binance_notification(
            user_id: str,
            position_summary: dict = None,
            error_details: dict = None,
) -> bool:
    """
    Kullanıcıya bildirim gönderir. Başarılı işlem veya hata bildirimi olabilir.

    Args:
        user_id (str): Telegram kullanıcı ID'si
        position_summary (dict, optional): Başarılı işlem detayları
        error_details (dict, optional): Hata detayları
    """
    try:
        # Bot instance'ını al
        bot_manager = await TelegramBotManager.get_instance()

        if position_summary:  # Başarılı işlem bildirimi
            position_emoji = "🟢" if position_summary.get('position_type', '').upper() == "LONG" else "🔴"

            # Hedefleri formatla
            def format_take_profits(take_profits):
                if isinstance(take_profits, list):
                    formatted_targets = []
                    for idx, target in enumerate(take_profits, 1):
                        formatted_targets.append(f"{idx}. Hedef = {target}")
                    return "\n".join(formatted_targets)
                return take_profits

            notification_message = (
                f"🔔 BİNANCE İşlem Bildirimi\n\n"
                f"📊 Sembol: {position_summary.get('symbol', 'Bilinmiyor')}\n"
                f"{position_emoji} Pozisyon: {position_summary.get('position_type', 'Bilinmiyor')}\n"
                f"💵 Giriş Fiyatları: {position_summary.get('entry_points', 'Bilinmiyor')}\n"
                f"🛑 Stop Loss: {position_summary.get('stop_loss', 'Bilinmiyor')}\n"
                f"🎯 Hedefler:\n{format_take_profits(position_summary.get('take_profits', []))}\n\n"
                f"💰 Lot: {position_summary.get('lot', 'Belirtilmemiş')}\n"
                f"⚡ Kaldıraç: {position_summary.get('leverage', '10')}x\n\n"
                f"📝 Sonuç: ✅ Başarılı\n"
                f"⏰ İşlem Zamanı: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"ℹ️ Detay: İşlem başarıyla açıldı\n"
            )

        elif error_details:  # Hata bildirimi
            notification_message = (
                f"⚠️ BİNANCE İşlem Hatası\n\n"
                f"📊 İşlem Yapılmak İstenen Sembol: {error_details.get('original_symbol', 'Bilinmiyor')}\n"
                f"❌ Hata: {error_details.get('error_message', 'Bilinmeyen hata')}\n"
                f"🔄 Denenen Semboller: {', '.join(error_details.get('attempted_symbols', []))}\n"
                f"⏰ Hata Zamanı: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
        else:
            logger.error("Bildirim için gerekli veriler eksik!")
            return False

        # Bildirimi gönder
        if await bot_manager.send_binance_notification(int(user_id), notification_message):
            logger.info(f"✅ Bildirim başarıyla gönderildi - Kullanıcı ID: {user_id}")
            return True
        else:
            logger.error(f"❌ Bildirim gönderilemedi - Kullanıcı ID: {user_id}")
            return False

    except Exception as e:
        logger.error(f"❌ Bildirim gönderme hatası: {str(e)}")
        return False


async def send_binance_trade_notification(
        user_id: int,
        signal_dict: dict,
        position_result: dict
):
    """
    Kullanıcıya özel ticaret bildirimlerini gönderir
    """
    try:
        # Bildirim mesajını oluştur
        position_emoji = "🟢" if signal_dict['position_type'].upper() == "LONG" else "🔴"
        result_emoji = "✅" if position_result['success'] else "❌"

        # Hedefleri formatlayan yardımcı fonksiyon
        def format_take_profits(take_profits):
            if isinstance(take_profits, list):
                formatted_targets = []
                for idx, target in enumerate(take_profits, 1):
                    formatted_targets.append(f"{idx}. Hedef = {target}")
                return "\n".join(formatted_targets)
            return take_profits

        # Ana bildirim mesajı
        notification_message = (
            f"🔔 BİNANCE İşlem Bildirimi\n\n"
            f"📊 Sembol: {signal_dict['symbol']}\n"
            f"{position_emoji} Pozisyon: {signal_dict['position_type']}\n"
            f"💵 Giriş Fiyatları: {signal_dict['entry_points']}\n"
            f"🛑 Stop Loss: {signal_dict['stop_loss']}\n"
            f"🎯 Hedefler:\n{format_take_profits(signal_dict['take_profits'])}\n\n"
            f"💰 Lot: {signal_dict.get('lot', 'Belirtilmemiş')}\n"
            f"⚡ Kaldıraç: {signal_dict.get('leverage', '10')}x\n\n"
            f"📝 Sonuç: {result_emoji} "
            f"{'Başarılı' if position_result['success'] else 'Başarısız'}\n"
            f"ℹ️ Detay: {position_result.get('error', 'İşlem tamamlandı')}\n"
            f"⏰ İşlem Zamanı: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        # Bot instance'ını al
        bot_manager = await TelegramBotManager.get_instance()

        # Direkt user_id'yi kullanarak bildirim gönder
        if await bot_manager.send_binance_notification(user_id, notification_message):
            logger.info(f"✅ Bildirim başarıyla gönderildi - Kullanıcı ID: {user_id}")
            return True
        else:
            logger.error(f"❌ Bildirim gönderilemedi - Kullanıcı ID: {user_id}")
            return False

    except Exception as e:
        logger.error(f"❌ Bildirim gönderme hatası: {str(e)}")
        return False

class OrderCreationError(Exception):
    """Emir oluşturma sırasında oluşan hatalar için özel hata sınıfı"""

    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)


def _round_to_step(x: float, step: float) -> float:
    if step <= 0:
        return x
    return round(round(x / step) * step, 12)

def _is_binance_conditional_unsupported(err: Exception) -> bool:
    """
    Sadece gerçekten conditional desteklenmiyorsa fallback'e izin ver.
    Aksi halde gerçek bug/bağlantı/param hatalarını saklamayalım.
    """
    msg = str(err or "")
    m = msg.lower()
    return (
        "-4120" in msg  # Binance: conditional order not supported
        or "unknown order type" in m
        or "invalid ordertype" in m
        or "not supported" in m
        or "order type" in m and "not" in m and "support" in m
    )


def _stop_limit_price_buffered(
    stop_price: float,
    side: OrderSideStr,
    price_step: float
) -> float:
    """
    STOP (stop-limit) fallback için limit fiyatını "fill olasılığı yüksek" tarafa koyar.
    - SELL stop-limit: limit fiyatı stop'un biraz ALTINDA (daha agresif sat)
    - BUY stop-limit: limit fiyatı stop'un biraz ÜSTÜNDE (daha agresif al)

    Buffer: 2 tick (min 1 tick).
    """
    tick = float(price_step or 0) or 0.0
    if tick <= 0:
        tick = abs(stop_price) * 0.0005  # çok kaba fallback
        if tick <= 0:
            tick = 0.0001

    buffer_ticks = 2.0
    if side == "sell":
        px = float(stop_price) - (buffer_ticks * tick)
    else:
        px = float(stop_price) + (buffer_ticks * tick)

    return _round_to_step(px, tick)


async def _ensure_markets_loaded(exchange, symbol: str) -> None:
    try:
        if not getattr(exchange, "markets", None):
            await asyncio.to_thread(exchange.load_markets)
        else:
            # bazı ccxt sürümlerinde markets var ama boş olabiliyor
            if symbol not in (getattr(exchange, "markets", {}) or {}):
                await asyncio.to_thread(exchange.load_markets)
    except Exception:
        # best-effort; market() yine çalışabilir
        pass


async def _binance_fapi_conditional_market(
    exchange,
    *,
    symbol: str,                 # ccxt symbol: "ENSO/USDT:USDT"
    close_side: OrderSideStr,    # "sell" / "buy"
    order_type: str,             # "STOP_MARKET" / "TAKE_PROFIT_MARKET"
    amount: float,
    stop_price: float,
) -> Dict[str, Any]:
    """
    Binance USDT-M Futures native endpoint ile conditional MARKET order açar.
    CCXT typing stub'ları dar olduğu için exchange'i Any olarak kullanıyoruz.
    """
    ex_any = cast(Any, exchange)

    await _ensure_markets_loaded(ex_any, symbol)

    m = ex_any.market(symbol)  # market id: "ENSOUSDT"
    req = {
        "symbol": m["id"],
        "side": close_side.upper(),
        "type": str(order_type).upper(),
        "quantity": ex_any.amount_to_precision(symbol, float(amount)),
        "stopPrice": ex_any.price_to_precision(symbol, float(stop_price)),
        "reduceOnly": "true",
        "workingType": "MARK_PRICE",
    }

    return await asyncio.to_thread(ex_any.fapiPrivatePostOrder, req)


async def _place_tp_with_fallback(
    exchange,
    symbol: str,
    close_side: OrderSideStr,
    amount: float,
    tp_price: float,
    price_step: float,
) -> Dict[str, Any]:
    """
    TP: Öncelik conditional MARKET (TAKE_PROFIT_MARKET).
    1) CCXT create_order TAKE_PROFIT_MARKET (stopPrice)
    2) -4120 vb. durumda Binance native fapiPrivatePostOrder fallback
    3) Son çare: LIMIT reduceOnly (trigger yok)  -> istersen kapatılabilir
    """
    amount_f = float(amount)
    tp_price_f = float(tp_price)

    tick = float(price_step or 0) or 0.0
    trigger = _round_to_step(tp_price_f, tick) if tick > 0 else tp_price_f

    # 1) CCXT (unified) TAKE_PROFIT_MARKET
    try:
        o = await create_smart_order(
            exchange=exchange,
            symbol=symbol,
            order_type="TAKE_PROFIT_MARKET",
            side=close_side,
            amount=amount_f,
            price=None,
            params={
                "stopPrice": float(trigger),
                "reduceOnly": True,
                "workingType": "MARK_PRICE",
            }
        )
        if isinstance(o, dict) and o.get("success") is False:
            raise OrderCreationError(str(o.get("message") or o.get("error") or "tp_place_failed"))
        return o

    except Exception as e:
        if not _is_binance_conditional_unsupported(e):
            raise
        logger.warning(f"[BINANCE_TP_NATIVE_FALLBACK] sym={symbol} err={str(e)[:220]}")

    # 2) Native futures endpoint fallback
    try:
        o2 = await _binance_fapi_conditional_market(
            exchange,
            symbol=symbol,
            close_side=close_side,
            order_type="TAKE_PROFIT_MARKET",
            amount=amount_f,
            stop_price=float(trigger),
        )
        logger.info(
            f"[BINANCE_ORDER_OK] type=TAKE_PROFIT_MARKET sym={symbol} side={close_side} "
            f"amount={amount_f} price=MKT id={getattr(o2,'get',lambda _k:None)('orderId') or getattr(o2,'get',lambda _k:None)('id')}"
        )
        return o2
    except Exception as e2:
        if not _is_binance_conditional_unsupported(e2):
            raise
        logger.warning(f"[BINANCE_TP_LIMIT_FALLBACK] sym={symbol} err={str(e2)[:220]}")

    # 3) Son çare: LIMIT reduceOnly (trigger yok)
    o3 = await create_smart_order(
        exchange=exchange,
        symbol=symbol,
        order_type="limit",
        side=close_side,
        amount=amount_f,
        price=(_round_to_step(tp_price_f, tick) if tick > 0 else tp_price_f),
        params={"reduceOnly": True, "timeInForce": "GTC"}
    )
    if isinstance(o3, dict) and o3.get("success") is False:
        raise OrderCreationError(str(o3.get("message") or o3.get("error") or "tp_place_failed"))
    return o3


async def _place_sl_with_fallback(
    exchange,
    symbol: str,
    close_side: OrderSideStr,
    amount: float,
    stop_price: float,
    price_step: float,
) -> Dict[str, Any]:
    """
    SL: Öncelik conditional MARKET (STOP_MARKET).
    1) CCXT create_order STOP_MARKET (stopPrice)
    2) -4120 vb. durumda Binance native fapiPrivatePostOrder fallback
    3) Eğer ikisi de olmazsa: BOT_MANAGED soft-fail
    """
    amount_f = float(amount)
    stop_f = float(stop_price)

    tick = float(price_step or 0) or 0.0
    trigger = _round_to_step(stop_f, tick) if tick > 0 else stop_f

    # 1) CCXT (unified) STOP_MARKET
    try:
        o = await create_smart_order(
            exchange=exchange,
            symbol=symbol,
            order_type="STOP_MARKET",
            side=close_side,
            amount=amount_f,
            price=None,
            params={
                "stopPrice": float(trigger),
                "reduceOnly": True,
                "workingType": "MARK_PRICE",
            }
        )
        if isinstance(o, dict) and o.get("success") is False:
            raise OrderCreationError(str(o.get("message") or o.get("error") or "sl_place_failed"))
        return o

    except Exception as e:
        if not _is_binance_conditional_unsupported(e):
            raise
        logger.warning(f"[BINANCE_SL_NATIVE_FALLBACK] sym={symbol} err={str(e)[:220]}")

    # 2) Native futures endpoint fallback
    try:
        o2 = await _binance_fapi_conditional_market(
            exchange,
            symbol=symbol,
            close_side=close_side,
            order_type="STOP_MARKET",
            amount=amount_f,
            stop_price=float(trigger),
        )
        logger.info(
            f"[BINANCE_ORDER_OK] type=STOP_MARKET sym={symbol} side={close_side} "
            f"amount={amount_f} price=MKT id={getattr(o2,'get',lambda _k:None)('orderId') or getattr(o2,'get',lambda _k:None)('id')}"
        )
        return o2
    except Exception as e2:
        if not _is_binance_conditional_unsupported(e2):
            raise
        logger.warning(f"[BINANCE_SL_SERVER_SIDE_UNSUPPORTED] sym={symbol} err={str(e2)[:220]}")

    # 3) server-side SL yoksa -> bot yönetimi
    return {
        "success": False,
        "error": "SERVER_SIDE_SL_UNSUPPORTED",
        "message": "STOP_MARKET başarısız. SL bot tarafından izlenecek.",
        "details": {"symbol": symbol, "stopPrice": float(trigger), "amount": float(amount_f), "side": close_side},
    }


async def create_smart_order(
    exchange,
    symbol: str,
    order_type: OrderTypeStr,
    side: OrderSideStr,
    amount: float,
    price: Optional[float] = None,
    params: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Binance USDT-M (binanceusdm) için güvenli emir oluşturma.

    MOD:
      - OPEN market kullanılabilir (pozisyon açmak için)
      - TP/SL tarafında bu dosya STOP / TAKE_PROFIT (stop-limit) ve LIMIT kullanır
      - STOP_MARKET / TAKE_PROFIT_MARKET yok (limit-only mod)

    Özellikler:
      - -2019 / insufficient margin => soft-fail dict döndürür
      - Hedge mode (-4061 / positionSide required) => positionSide ekleyip 1 kez retry
      - price gerektiren emirlerde price yoksa OrderCreationError fırlatır
    """
    clean_params: Dict[str, Any] = dict(params or {})

    def _needs_positionside(msg: str) -> bool:
        m = (msg or "").lower()
        return ("positionside" in m) or ("position side" in m) or ("hedge mode" in m) or ("-4061" in m)

    def _pos_side_for_close_side(order_side: str) -> str:
        # Binance hedge: positionSide LONG/SHORT.
        # close emirlerinde:
        #  - SELL => LONG pozisyonu kapatır
        #  - BUY  => SHORT pozisyonu kapatır
        return "LONG" if str(order_side).lower() == "sell" else "SHORT"

    def _requires_price(ot_lower: str) -> bool:
        # limit emirleri price ister; conditional MARKET (stop_market / take_profit_market) istemez
        return ot_lower in ("limit", "stop", "take_profit")

    try:
        if side not in ("buy", "sell"):
            raise OrderCreationError(f"Geçersiz side: {side}")

        try:
            amount_f = float(amount)
        except Exception:
            raise OrderCreationError("amount float'a çevrilemedi")

        if amount_f <= 0:
            raise OrderCreationError("amount<=0")

        ot_lower = str(order_type).lower().strip()

        price_f: Optional[float] = None
        if price is not None:
            try:
                price_f = float(price)
            except Exception:
                raise OrderCreationError("price float'a çevrilemedi")

        if _requires_price(ot_lower) and price_f is None:
            raise OrderCreationError(f"{order_type} emir tipi için price zorunlu (limit-only mod)")

        async def _send_once(local_params: Dict[str, Any]) -> Dict[str, Any]:
            if ot_lower == "market":
                return await asyncio.to_thread(
                    _ccxt_create_market_order(exchange),
                    symbol, side, amount_f, local_params
                )

            if ot_lower == "limit":
                return await asyncio.to_thread(
                    _ccxt_create_limit_order(exchange),
                    symbol, side, amount_f, price_f, local_params
                )

            # Conditional / advanced types
            ot_up = str(order_type).upper()

            # STOP_MARKET / TAKE_PROFIT_MARKET gibi tiplerde price None olmalı (CCXT zaten None kabul eder)
            return await asyncio.to_thread(
                _ccxt_create_order(exchange),
                symbol,
                ot_up,
                str(side),
                amount_f,
                price_f,  # STOP_MARKET için None gelecek
                local_params
            )

        # 1) ilk deneme
        try:
            order = await _send_once(clean_params)
        except Exception as e:
            msg = str(e)
            # CCXT bazen ayrıntıyı args içinde saklar
            try:
                logger.warning(f"[BINANCE_ORDER_ERR_RAW] type={order_type} sym={symbol} side={side} args={getattr(e, 'args', None)}")
            except Exception:
                pass

            # Soft-fail: insufficient margin
            is_insufficient = isinstance(e, InsufficientFunds)
            if ('"code":-2019' in msg) or ("Margin is insufficient" in msg) or is_insufficient:
                err_code = -2019
                err_key = "INSUFFICIENT_MARGIN"
                raw_error = msg[:500]
                msg_tr = tr_error_message(err_key, err_code, raw_error)

                return {
                    "success":False,
                    "error":err_key,
                    "error_code":err_code,
                    "message":msg[:240],  # ham/kısa
                    "message_tr":msg_tr,  # ✅ Türkçe açıklama
                    "raw_error":raw_error,
                }

            # Hedge mode retry
            if _needs_positionside(msg) and ("positionSide" not in clean_params):
                retry_params = dict(clean_params)
                retry_params["positionSide"] = _pos_side_for_close_side(side)

                try:
                    order = await _send_once(retry_params)
                    logger.info(
                        f"[BINANCE_ORDER_OK_RETRY_HEDGE] type={order_type} sym={symbol} side={side} "
                        f"id={getattr(order, 'get', lambda _k: None)('id')}"
                    )
                    return order
                except Exception as e2:
                    raise OrderCreationError(f"Emir Oluşturma Hatası (hedge retry fail): {str(e2)}")

            # diğer hatalar
            raise OrderCreationError(f"Emir Oluşturma Hatası: {msg}")

        if order is None:
            raise OrderCreationError("Emir oluşturulamadı (order None)")

        logger.info(
            f"[BINANCE_ORDER_OK] type={order_type} sym={symbol} side={side} amount={amount_f} "
            f"price={(price_f if price_f is not None else 'MKT')} id={getattr(order, 'get', lambda _k: None)('id')}"
        )
        return order

    except OrderCreationError:
        raise
    except Exception as e:
        raise OrderCreationError(f"Emir Oluşturma Hatası: {str(e)}")

'''
////////////////////////////////////////stop loss başlangıç ///////////////////////////////////////////////////////////
'''

async def manage_stop_loss(
        exchange,
        symbol: str,
        user_id: int,
        position_side: str,
        entry_price: float,
        original_amount: float,
        remaining_amount: float,
        tp_hit: int = None  # Hangi TP'nin tetiklendiği (1 veya 2)
) -> Dict[str, Any]:
    try:
        # Detaylı ayar çekme sorgusu - HATALI KISMI DÜZELTİLDİ
        query = """
        SELECT 
            stop_loss, 
            terial_stop, 
            maliyet_cek, 
            sl_percentage
        FROM ayarlar 
        WHERE user_id = ? AND exchange = 'binance'
        """

        # Log eklemeleri ve hata kontrolü
        logger.info(f"Stop Loss Ayarları Çekme Sorgusu Çalıştırılıyor - Kullanıcı: {user_id}")

        settings = db_operation(
            query,
            (user_id,),
            operation='select',
            fetch=True,
            fetch_all=False
        )

        # Ayar kontrolü ve detaylı log
        if not settings:
            logger.warning(f"Kullanıcı {user_id} için ayarlar bulunamadı")
            return {
                'success': False,
                'message': 'Ayarlar bulunamadı',
                'error_code': 'SETTINGS_NOT_FOUND'
            }

        # Güvenli ayar çekme
        try:
            stop_loss_type = settings[0] if settings[0] is not None else 'off'
            terial_stop = settings[1] if settings[1] is not None else 'off'
            maliyet_cek = settings[2] if settings[2] is not None else 'off'
            sl_percentage = settings[3] if settings[3] is not None else 0.0
        except IndexError as e:
            logger.error(f"Ayar çekme hatası: {e}")
            return {
                'success': False,
                'message': 'Ayarlar alınamadı',
                'error_code': 'SETTINGS_PARSE_ERROR'
            }

        # Detaylı ayar sözlüğü
        settings_dict = {
            'stop_loss_type': stop_loss_type,
            'terial_stop': terial_stop,
            'maliyet_cek': maliyet_cek,
            'sl_percentage': sl_percentage,
        }

        # Gelişmiş log mekanizması
        logger.info(f"""
        🔄 Stop Loss Yönetimi Detayları:
        👤 Kullanıcı ID: {user_id}
        🔖 Sembol: {symbol}
        📊 Stop Loss Tipi: {stop_loss_type}
        🚦 Trailing Stop: {terial_stop}
        💰 Maliyet Çekme: {maliyet_cek}
        📈 SL Yüzdesi: %{sl_percentage}
        💵 Giriş Fiyatı: {entry_price}
        🔢 Toplam Miktar: {original_amount}
        🔽 Kalan Miktar: {remaining_amount}
        """)

        # Stop loss stratejisi seçimi
        if stop_loss_type == 'off':
            # Trailing stop kontrolü
            if terial_stop == 'on':
                return await handle_terial_stop(
                    exchange=exchange,
                    symbol=symbol,
                    position_side=position_side,
                    entry_price=entry_price,
                    remaining_amount=remaining_amount
                )
            else:
                logger.info("❌ Stop loss ve trailing stop kapalı")
                return {
                    'success': True,
                    'message': 'Stop loss devre dışı',
                    'action': 'NO_ACTION'
                }

        elif stop_loss_type == 'fixed':
            return await handle_fixed_stop_loss(
                exchange=exchange,
                symbol=symbol,
                user_id=user_id,
                position_side=position_side,
                entry_price=entry_price,
                original_amount=original_amount,
                remaining_amount=remaining_amount,
                settings=settings_dict,
                tp_hit=tp_hit
            )

        elif stop_loss_type == 'percentage':
            return await handle_percentage_stop_loss(
                exchange=exchange,
                symbol=symbol,
                position_side=position_side,
                entry_price=entry_price,
                remaining_amount=original_amount,
                settings=settings_dict
            )

        else:
            logger.warning(f"Geçersiz stop loss tipi: {stop_loss_type}")
            return {
                'success': False,
                'message': 'Geçersiz stop loss tipi',
                'error_code': 'INVALID_STOP_LOSS_TYPE'
            }

    except Exception as e:
        # Global hata yakalama
        logger.error(f"Stop Loss Yönetimi Kritik Hatası: {e}")
        return {
            'success': False,
            'message': 'Stop loss yönetiminde kritik hata',
            'error_code': 'CRITICAL_STOP_LOSS_ERROR',
            'details': str(e)
        }

# === BINANCE TRAILING ADAPTER ===
async def handle_fixed_stop_loss(
        exchange,
        symbol: str,
        user_id: int,
        position_side: str,              # LONG/SHORT bekleniyor
        entry_price: float,
        original_amount: float,
        remaining_amount: float,
        settings: Dict[str, Any],
        tp_hit: Optional[int] = None
) -> Dict[str, Any]:
    """
    Sabit stop loss yönetimi
    """
    try:
        maliyet_cek = settings.get('maliyet_cek', 'off')

        # Stop loss fiyatını belirle
        if maliyet_cek == 'off':
            stop_price = settings.get('stop_loss_price')

        elif maliyet_cek in ['1', '2']:
            tp_level = int(maliyet_cek)
            if tp_hit == tp_level:
                stop_price = entry_price
                logger.info(f"User {user_id} - TP{tp_level} tetiklendi, stop loss giriş fiyatına çekiliyor")
            else:
                stop_price = settings.get('stop_loss_price')

        else:
            raise ValueError(f"Geçersiz maliyet_cek değeri: {maliyet_cek}")

        if stop_price is None:
            raise ValueError("stop_loss_price ayarlarda yok veya None geldi")

        # Stop loss emrini oluştur (OrderSideStr olarak netleştir)
        order_side: OrderSideStr = 'sell' if position_side == 'LONG' else 'buy'
        # (PyCharm hâlâ inat ederse şu satır kesin çözer)
        order_side = cast(OrderSideStr, order_side)

        ratio = (remaining_amount / original_amount) if original_amount else 1.0

        return await create_stop_loss_order(
            exchange=exchange,
            symbol=symbol,
            side=order_side,
            original_amount=original_amount,
            stop_price=float(stop_price),
            remaining_position_ratio=float(ratio),
            user_id=user_id
        )

    except Exception as e:
        logger.error(f"User {user_id} - Fixed stop loss hatası: {e}", exc_info=True)
        return {'success': False, 'message': str(e)}


async def handle_percentage_stop_loss(
        exchange,
        symbol: str,
        position_side: str,          # LONG/SHORT bekleniyor
        entry_price: float,
        remaining_amount: float,
        settings: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Yüzdesel stop loss yönetimi
    """
    try:
        sl_percentage = float(settings.get('sl_percentage', 3))

        if position_side == 'LONG':
            stop_price = entry_price * (1 - sl_percentage / 100)
        else:  # SHORT
            stop_price = entry_price * (1 + sl_percentage / 100)

        logger.info(f"""
        📊 Yüzdesel Stop Loss Hesaplanıyor:
        💰 Giriş Fiyatı: {entry_price}
        📈 Stop Loss Yüzdesi: %{sl_percentage}
        🎯 Hesaplanan Stop Price: {stop_price}
        """)

        # Stop loss emrini oluştur (OrderSideStr olarak netleştir)
        order_side: OrderSideStr = 'sell' if position_side == 'LONG' else 'buy'
        order_side = cast(OrderSideStr, order_side)

        return await create_stop_loss_order(
            exchange=exchange,
            symbol=symbol,
            side=order_side,
            original_amount=float(remaining_amount),
            stop_price=float(stop_price),
            remaining_position_ratio=1.0
        )

    except Exception as e:
        logger.error(f"Percentage stop loss hatası: {e}", exc_info=True)
        return {'success': False, 'message': str(e)}


async def handle_terial_stop(
        exchange,
        symbol: str,
        position_side: str,
        entry_price: float,
        remaining_amount: float
) -> Dict[str, Any]:
    """
    Gelişmiş Trailing Stop Yönetimi

    Args:
        exchange: Borsa API nesnesi
        symbol: İşlem çifti (örn: 'BTC/USDT')
        position_side: Pozisyon yönü ('LONG' veya 'SHORT')
        entry_price: Giriş fiyatı
        remaining_amount: Kalan pozisyon miktarı
    """
    try:
        # Veritabanından trailing stop ayarlarını çek
        query = """
        SELECT 
            terial_stop_activation, 
            terial_stop_callback, 
            terial_stop_max_drawdown 
        FROM ayarlar 
        WHERE exchange = 'binance'
        """
        trailing_settings = db_operation(
            query,
            (),
            operation='select',
            fetch=True,
            fetch_all=False
        )

        if not trailing_settings:
            logger.warning("Trailing stop ayarları bulunamadı")
            return {
                'success': False,
                'message': 'Trailing stop ayarları eksik'
            }

        # Ayarları parse et
        activation_percentage = float(trailing_settings[0] or 0.01)  # Varsayılan %1
        callback_percentage = float(trailing_settings[1] or 0.005)  # Varsayılan %0.5
        max_drawdown = float(trailing_settings[2] or 0.02)  # Varsayılan %2

        # Mevcut piyasa fiyatını al
        ticker = await asyncio.to_thread(exchange.fetch_ticker, symbol)
        current_price = ticker['last']

        # ATR (Average True Range) hesaplaması
        ohlcv = await asyncio.to_thread(
            exchange.fetch_ohlcv,
            symbol,
            timeframe='1h',
            limit=14
        )

        # ATR hesaplama
        def calculate_atr(ohlcv_data):
            true_ranges = []
            for i in range(1, len(ohlcv_data)):
                high = ohlcv_data[i][2]
                low = ohlcv_data[i][3]
                prev_close = ohlcv_data[i - 1][4]

                true_range = max(
                    high - low,
                    abs(high - prev_close),
                    abs(low - prev_close)
                )
                true_ranges.append(true_range)

            return sum(true_ranges) / len(true_ranges) if true_ranges else 0

        atr_value = calculate_atr(ohlcv)

        logger.info(f"""
        🔄 Trailing Stop Analizi:
        📊 Sembol: {symbol}
        💰 Pozisyon: {position_side}
        📈 Giriş Fiyatı: {entry_price}
        📉 Mevcut Fiyat: {current_price}
        📊 ATR: {atr_value}
        🚨 Aktivasyon Yüzdesi: %{activation_percentage * 100}
        🔙 Geri Çekim Yüzdesi: %{callback_percentage * 100}
        📉 Maksimum Düşüş: %{max_drawdown * 100}
        """)

        # Long pozisyon trailing stop
        if position_side == 'LONG':
            # Kar yüzdesini hesapla
            profit_percentage = (current_price - entry_price) / entry_price

            # Aktivasyon kontrolü
            if profit_percentage >= activation_percentage:
                # Stop fiyatını hesapla
                stop_price = current_price * (1 - callback_percentage)

                # Maksimum düşüş kontrolü
                max_stop_price = entry_price * (1 + max_drawdown)
                stop_price = max(stop_price, max_stop_price)

                logger.info(f"""
                🎯 Long Trailing Stop Tetiklendi:
                📊 Kar Yüzdesi: %{profit_percentage * 100:.2f}
                🛑 Stop Fiyatı: {stop_price}
                """)

                # Stop loss emri oluştur
                return await create_stop_loss_order(
                    exchange=exchange,
                    symbol=symbol,
                    side='sell',
                    original_amount=remaining_amount,
                    stop_price=stop_price,
                    remaining_position_ratio=1.0
                )

        # Short pozisyon trailing stop
        elif position_side == 'SHORT':
            # Kar yüzdesini hesapla
            profit_percentage = (entry_price - current_price) / entry_price

            # Aktivasyon kontrolü
            if profit_percentage >= activation_percentage:
                # Stop fiyatını hesapla
                stop_price = current_price * (1 + callback_percentage)

                # Maksimum düşüş kontrolü
                max_stop_price = entry_price * (1 - max_drawdown)
                stop_price = min(stop_price, max_stop_price)

                logger.info(f"""
                🎯 Short Trailing Stop Tetiklendi:
                📊 Kar Yüzdesi: %{profit_percentage * 100:.2f}
                🛑 Stop Fiyatı: {stop_price}
                """)

                # Stop loss emri oluştur
                return await create_stop_loss_order(
                    exchange=exchange,
                    symbol=symbol,
                    side='buy',
                    original_amount=remaining_amount,
                    stop_price=stop_price,
                    remaining_position_ratio=1.0
                )

        # Henüz trailing stop koşulları sağlanmadı
        return {
            'success': True,
            'message': 'Trailing stop koşulları henüz sağlanmadı'
        }

    except Exception as e:
        error_message = f"Trailing stop hatası: {str(e)}"
        logger.error(f"""
        🚨 {error_message}
        📊 Sembol: {symbol}
        💰 Pozisyon: {position_side}
        📈 Giriş: {entry_price}
        💵 Miktar: {remaining_amount}
        """)
        return {
            'success': False,
            'message': error_message
        }


async def create_stop_loss_order(
        exchange,
        symbol: str,
        side: OrderSideStr,
        original_amount: float,
        stop_price: float,
        remaining_position_ratio: float = 1.0,
        user_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    SL yerleştirme (LIMIT-ONLY):
    - STOP (stop-limit) reduceOnly dener
    - Gerçekten desteklenmiyorsa (-4120) BOT_MANAGED soft-fail döner
    """

    user_info = f"(Kullanıcı ID: {user_id})" if user_id else ""

    if side not in ("buy", "sell"):
        raise ValueError(f"Geçersiz side: {side}. 'buy' veya 'sell' olmalı.")

    try:
        stop_loss_amount = float(original_amount) * float(remaining_position_ratio)
        if stop_loss_amount <= 0:
            return {"success": False, "error": "stop_loss_amount<=0"}

        meta = get_symbol_meta_binance(symbol) or {}
        meta = cast(Dict[str, Any], meta)
        price_step = float(meta.get("price_step") or 0) or 0.0

        logger.info(f"🛑 Stop Loss Emri Oluşturuluyor {user_info} sym={symbol} side={side} stop={stop_price} amt={stop_loss_amount}")

        order = await _place_sl_with_fallback(
            exchange=exchange,
            symbol=symbol,
            close_side=side,
            amount=float(stop_loss_amount),
            stop_price=float(stop_price),
            price_step=float(price_step),
        )
        # ✅ server-side SL yoksa: fatal değil
        if isinstance(order, dict) and order.get("success") is False:
            if order.get("error") == "SERVER_SIDE_SL_UNSUPPORTED":
                logger.warning(
                    f"[BINANCE_SL_BOT_MANAGED] sym={symbol} stop={stop_price} amt={stop_loss_amount} reason=-4120")
                return {
                    "success":True,
                    "order":None,
                    "details":{
                        "symbol":symbol,
                        "side":side,
                        "stop_price":float(stop_price),
                        "amount":float(stop_loss_amount),
                        "user_id":user_id,
                        "mode":"BOT_MANAGED",
                        "note":"Binance conditional SL -4120; stop bot tarafından izlenecek."
                    }
                }

            return {"success":False, "error":str(order.get("error") or "sl_place_failed")}
        # ✅ BAŞARI DURUMU: mutlaka return et
        return {"success": True, "order": order}

    except OrderCreationError as order_error:
        logger.error(f"Stop Loss Emri Oluşturma Hatası {user_info}: {order_error}")
        return {"success": False, "error": str(order_error)}

    except Exception as e:
        logger.error(f"Stop Loss Emri Oluşturma Genel Hatası {user_info}: {e}", exc_info=True)
        return {"success": False, "error": str(e), "details": {"traceback": traceback.format_exc()}}


async def binance_update_stop_loss(
    user_id: int,
    symbol_core: str,
    new_sl_price: float,
    reason: str = "TRAIL",
) -> Dict[str, Any]:
    """
    Binance USDT-M futures için SL update (LIMIT-ONLY).

    Davranış:
      1) Eski reduceOnly STOP* emirlerini iptal eder (best-effort)
      2) Açık pozisyonu bulur (miktar + kapatma yönü)
      3) Yeni SL'yi STOP (stop-limit) reduceOnly olarak koyar
      4) Timeline hook (on_sl_move) tetikler

    Not:
      - STOP_MARKET / Algo yok.
      - STOP (stop-limit) desteklenmezse _place_sl_with_fallback BOT_MANAGED soft-fail döndürebilir.
    """
    try:
        api = await get_api_key(user_id, "binance")
        if not api or not api.get("api_key") or not api.get("secret_key"):
            return {"success": False, "error": "api_key/secret_key yok"}

        ex = create_binance_exchange(
            api_key=str(api["api_key"]),
            secret_key=str(api["secret_key"]),
            exchange_type="futures",
        )
        await _ensure_time_sync(ex)

        core = _to_symbol_core(symbol_core or "")
        symbol = _to_binance_ccxt_symbol(core)
        if not core or not symbol:
            return {"success":False, "error":"symbol_empty"}

        # 1) eski STOP reduceOnly emirlerini iptal et (best-effort)
        try:
            orders = await asyncio.to_thread(ex.fetch_open_orders, symbol)
            for o in orders or []:
                try:
                    otype = str(o.get("type", "")).upper()
                    reduce_only = bool(o.get("reduceOnly")) or bool((o.get("info") or {}).get("reduceOnly"))
                    if reduce_only and otype.startswith("STOP"):
                        oid = o.get("id")
                        if oid:
                            try:
                                await asyncio.to_thread(ex.cancel_order, oid, symbol)
                                await asyncio.sleep(0.05)
                            except Exception:
                                pass
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"[BINANCE_UPD_SL_FETCH_ORDERS_WARN] uid={user_id} sym={symbol} err={e}")

        # 2) pozisyonu bul
        positions = await asyncio.to_thread(ex.fetch_positions, [symbol])

        pos_amt: float = 0.0
        close_side: OrderSideStr = "sell"
        for p in positions or []:
            if p.get("symbol") != symbol:
                continue
            amt = _extract_position_amount(p)
            if abs(amt) > 0:
                pos_amt = abs(float(amt))
                close_side = cast(OrderSideStr, ("sell" if amt > 0 else "buy"))
                break

        if pos_amt <= 0:
            return {"success": False, "error": "no_open_position"}

        meta = get_symbol_meta_binance(core) or {}
        meta = cast(Dict[str, Any], meta)
        price_step = float(meta.get("price_step") or 0.0) or 0.0

        # 3) yeni SL (STOP stop-limit) -> fallback BOT_MANAGED olabilir
        try:
            order = await _place_sl_with_fallback(
                exchange=ex,
                symbol=symbol,
                close_side=close_side,
                amount=float(pos_amt),
                stop_price=float(new_sl_price),
                price_step=float(price_step),
            )
        except Exception as e:
            logger.error(f"[BINANCE_UPD_SL_PLACE_ERR] uid={user_id} sym={symbol} err={e}", exc_info=True)
            return {"success": False, "error": "place_failed"}

        # 4) hook
        try:
            norm_sym_key = normalize_symbol_for_key(core)
            state = ACTIVE_OPEN_POSITIONS.get((int(user_id), "binance", norm_sym_key))
            on_sl_move(int(user_id), "binance", norm_sym_key, float(new_sl_price), reason or "TRAIL", state)
        except Exception as hook_err:
            logger.warning(f"[BINANCE_UPD_SL_HOOK_WARN] uid={user_id} sym={symbol} err={hook_err}")

        if isinstance(order, dict) and order.get("success") is False:
            # BOT_MANAGED gibi soft-fail burada dönebilir
            return {"success": False, "error": order.get("error") or "place_failed", "details": order}

        return {
            "success": True,
            "data": {
                "symbol": symbol,
                "new_sl_price": float(new_sl_price),
                "order_id": (order.get("id") if isinstance(order, dict) else None),
            },
        }

    except Exception as e:
        logger.error(f"[BINANCE_UPD_SL_FATAL] uid={user_id} sym={symbol_core} err={e}", exc_info=True)
        return {"success": False, "error": str(e)}

'''
//////////////////////////////////////////stop loss senaryoları bitiş /////////////////////////////////////////////
'''
async def binance_close_position(
    symbol: str,
    side: Literal['LONG', 'SHORT'],
    close_price: float,  # bilgi amaçlı (limit fiyatı hesaplamada opsiyonel referans)
    api_key: str,
    secret_key: str,
) -> Dict[str, Any]:
    """
    Binance USDT-M futures pozisyonunu LIMIT emir ile kapatır (reduceOnly).

    Kurallar:
      - MARKET emir YOK.
      - Fill olasılığını artırmak için limit fiyatına küçük buffer uygulanır:
          LONG kapat (sell)  -> mark/last'ın biraz ALTINDA
          SHORT kapat (buy)  -> mark/last'ın biraz ÜSTÜNDE
    """
    core = _to_symbol_core(symbol)
    symbol = _to_binance_ccxt_symbol(core)

    logger.info("📍 Pozisyon Kapatma İşlemi Başlatıldı (LIMIT-ONLY)")
    logger.info(f"🔖 Sembol: {symbol}")
    logger.info(f"📈 Pozisyon Yönü: {side}")
    logger.info(f"💰 Kapanış Fiyatı (bilgi): {close_price}")

    try:
        exchange = create_binance_exchange(api_key=api_key, secret_key=secret_key, exchange_type="futures")
        await _ensure_time_sync(exchange)

        # Piyasaları yüklemek min/limit kontrollerini daha sağlıklı yapar
        try:
            if not getattr(exchange, "markets", None):
                await asyncio.to_thread(exchange.load_markets)
        except Exception:
            pass

        # Açık pozisyonu bul
        positions = await asyncio.to_thread(exchange.fetch_positions, [symbol])

        position_amount: float = 0.0
        for p in positions or []:
            if p.get('symbol') != symbol:
                continue
            position_amount = _extract_position_amount(p)
            break

        if position_amount == 0.0:
            logger.warning("⚠️ Kapatılacak açık pozisyon bulunamadı!")
            return {'success': False, 'message': 'Açık pozisyon bulunamadı'}

        # LONG kapatmak için SELL, SHORT kapatmak için BUY
        order_side: OrderSideStr = 'sell' if side == 'LONG' else 'buy'
        close_qty = abs(float(position_amount))

        # Min miktar kontrolü (güvenli)
        try:
            min_amt = (
                (((getattr(exchange, "markets", {}) or {}).get(symbol) or {}).get('limits') or {})
                .get('amount') or {}
            ).get('min') or 0.0
        except Exception:
            min_amt = 0.0

        if min_amt and close_qty < float(min_amt):
            logger.warning(f"⚠️ Pozisyon miktarı minimumdan düşük! amt={close_qty} min={min_amt}")
            return {'success': False, 'message': 'Pozisyon miktarı çok düşük'}

        # Limit fiyatını belirle (mark/last)
        ticker = await asyncio.to_thread(exchange.fetch_ticker, symbol)
        ref_price = (
            float(ticker.get("mark") or 0.0) or
            float(ticker.get("last") or 0.0) or
            float(ticker.get("close") or 0.0) or
            float(close_price or 0.0)
        )

        if not ref_price or ref_price <= 0:
            return {'success': False, 'message': 'Referans fiyat alınamadı (limit close)'}

        meta = get_symbol_meta_binance(core) or {}
        meta = cast(Dict[str, Any], meta)
        price_step = float(meta.get("price_step") or 0.0) or 0.0

        # Buffer (2 tick). price_step yoksa kaba fallback.
        tick = float(price_step) if price_step > 0 else max(ref_price * 0.0005, 0.0001)
        buffer_ticks = 2.0

        if order_side == "sell":
            limit_px = _round_to_step(ref_price - buffer_ticks * tick, tick)
        else:
            limit_px = _round_to_step(ref_price + buffer_ticks * tick, tick)

        if limit_px <= 0:
            return {'success': False, 'message': 'Limit fiyat hesaplanamadı'}

        # LIMIT reduceOnly ile kapat
        close_order = await asyncio.to_thread(
            cast(Any, exchange).create_limit_order,
            symbol,
            order_side,
            float(close_qty),
            float(limit_px),
            {'reduceOnly': True, 'timeInForce': 'GTC'}
        )

        logger.info("✅ Pozisyon LIMIT ile kapatma emri gönderildi")
        return {
            'success': True,
            'message': f"{symbol} için {side} pozisyonu LIMIT ile kapatma emri gönderildi",
            'close_order': close_order,
            'closed_amount': float(close_qty),
            'limit_price': float(limit_px),
            'ref_price': float(ref_price),
        }

    except ccxt.NetworkError as e:
        logger.error(f"🚫 Ağ hatası: {e}")
        return {'success': False, 'message': f"Ağ hatası: {e}"}

    except ccxt.ExchangeError as e:
        logger.error(f"🚫 Borsa hatası: {e}")
        return {'success': False, 'message': f"Borsa hatası: {e}"}

    except Exception as e:
        logger.error(f"🚫 Beklenmedik hata: {e}", exc_info=True)
        return {'success': False, 'message': f"Beklenmedik hata: {e}"}
