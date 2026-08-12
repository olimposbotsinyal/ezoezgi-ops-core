# settings/execution/bybit_al_sat.py

import time
import json
import hmac
from typing import Dict, Any, Optional, List
import aiohttp
from data.olimpos_data import *
from config.constants import *
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackContext
from datetime import datetime
import hashlib  # EKSİKTİ
from settings.execution_plan_types import ExecutionPlan
from analytics.timeline_hooks import on_sl_move
from signal_merkezi import ACTIVE_OPEN_POSITIONS, normalize_symbol_for_key
from settings.execution.symbol_meta_provider import get_symbol_meta as _generic_meta
from core.symbol_resolver import normalize_core_symbol
from settings.execution.symbol_meta_provider import get_symbol_meta as provider_get_symbol_meta



logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def get_symbol_meta(symbol: str):
    core_sym = normalize_core_symbol(symbol)
    return provider_get_symbol_meta(exchange="bybit", symbol=core_sym)


# === TRAILING AYAR OKUMA YARDIMCI ===
def _fetch_trailing_setting(user_id: int, exchange: str):
    """
    ayarlar tablosundan trailing_mode, trailing_param döndürür.
    exchange küçük harf girilmeli.
    """
    try:
        row = db_operation(
            "SELECT trailing_mode, trailing_param FROM ayarlar WHERE user_id=? AND LOWER(exchange)=?",
            (user_id, exchange.lower()),
            operation='select',
            fetch=True
        )
        if row and row[0]:
            mode, param = row[0]
            if mode and str(mode).upper() in ('PCT','ATR'):
                try:
                    return str(mode).upper(), float(param) if param is not None else None
                except (TypeError, ValueError):
                    return str(mode).upper(), None
        return None, None
    except Exception as e:
        logger.error(f"[TRAIL_FETCH_SETTING_ERR] user={user_id} ex={exchange} {e}")
        return None, None


# === BYBIT TRAILING GLOBAL ===
TRAILING_POSITION_OWNERS_BYBIT = {}  # position_id -> {user_id, exchange, symbol, side}
_BYBIT_KEY_CACHE = {}  # user_id -> (api_key, secret_key)


async def _bybit_position_size(api_key, secret_key, symbol):
    """
    Basit yöntem: mevcut pozisyonu unified endpoint ile çekmek gerekir; burada minimal bir örnek veriyoruz.
    Dilersen geliştirebilirsin.
    """
    try:
        # Burada sadece CCXT ile fetch_positions alternatifi önerilebilir; basit placeholder:
        # Gelişmiş sürümde unified position endpoint çağır.
        return None  # Pozisyon miktarı bilinmiyorsa None döner, trail update atlanır.
    except Exception as e:
        logger.error(f"[BYBIT_POS_SIZE_ERR] {e}")
        return None


async def _bybit_cancel_all_stops(api_key: str, secret_key: str, symbol: str) -> bool:
    """
    Bybit V5: /v5/order/cancel-all
    category=linear, symbol=...
    Not: Bu çağrı tüm açık emirleri silebilir (kaba yaklaşım).
    """
    try:
        recv_window = "5000"
        ts = str(int(time.time() * 1000))

        endpoint = "/v5/order/cancel-all"
        url = f"https://api.bybit.com{endpoint}"

        body_obj = {"category": "linear", "symbol": symbol}
        body = json.dumps(body_obj, separators=(",", ":"))  # payload aynen bu olacak

        sig = get_bybit_v5_signature(
            secret_key,
            timestamp=ts,
            api_key=api_key,
            recv_window=recv_window,
            payload=body
        )

        headers = {
            "X-BAPI-API-KEY": api_key,
            "X-BAPI-SIGN": sig,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv_window,
            "X-BAPI-SIGN-TYPE": "2",
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, data=body) as resp:
                rj = await resp.json()

        # retCode=0 başarı
        ok = (rj.get("retCode") == 0)
        if not ok:
            logger.warning(f"[BYBIT_CANCEL_ALL_FAIL] sym={symbol} resp={rj}")
        return ok

    except Exception as e:
        logger.error(f"[BYBIT_CANCEL_STOPS_ERR] {e}", exc_info=True)
        return False


async def _bybit_place_new_sl(api_key, secret_key, symbol, side, qty, new_sl_price):
    """
    Yeni SL emri oluştur (Limit trigger şeklinde).
    """
    try:
        if qty is None:
            return False
        # stop emrini daha önce yazdığın create_bybit_stop_loss fonksiyonunu kullan:
        opp_side = side  # side parametresi LONG/SHORT geldi; create fonksiyon side'ı LONG/SHORT bekliyor
        r = await create_bybit_stop_loss(
            symbol=symbol,
            side=opp_side,
            stop_price=new_sl_price,
            quantity=qty,
            api_key=api_key,
            secret_key=secret_key
        )
        return r.get('success', False)

    except Exception as e:
        logger.error(f"[BYBIT_NEW_SL_ERR] {e}")
        return False


def build_bybit_dynamic_update_sl_adapter():
    async def _adapter(position_id: int, symbol: str, new_sl_price: float) -> bool:
        owner = TRAILING_POSITION_OWNERS_BYBIT.get(position_id)
        if not owner:
            return False
        user_id = owner['user_id']
        if user_id not in _BYBIT_KEY_CACHE:
            row = db_operation("SELECT api_key, secret_key FROM api_key "
                               "WHERE user_id=? AND LOWER(exchange)='bybit'",
                               (user_id,), operation='select', fetch=True)
            if not row or not row[0]:
                logger.warning(f"[BYBIT_TRAIL_NO_API] user={user_id}")
                return False
            _BYBIT_KEY_CACHE[user_id] = row[0]
        api_key, secret_key = _BYBIT_KEY_CACHE[user_id]
        try:
            # (İsteğe bağlı) pozisyon miktarı çekilebilir.
            qty = None  # Geliştirmek istersen _bybit_position_size çağır
            await _bybit_cancel_all_stops(api_key, secret_key, symbol)
            ok = await _bybit_place_new_sl(api_key, secret_key, symbol, owner.get('side', 'LONG'),
                qty if qty else owner.get('qty'), new_sl_price)
            if ok:
                logger.info(f"[BYBIT_TRAIL_UPDATED] user={user_id} symbol={symbol} price={new_sl_price}")
                # SL_MOVE hook
                try:
                    norm_sym_key = normalize_symbol_for_key(symbol)  # symbol zaten normalize edilmiş ("BTCUSDT")
                    state = ACTIVE_OPEN_POSITIONS.get((int(user_id), "bybit", norm_sym_key))
                    on_sl_move(int(user_id), "bybit", norm_sym_key, float(new_sl_price), "TRAIL", state)
                except Exception as hook_err:
                    logger.warning(f"[BYBIT_TRAIL_SL_MOVE_HOOK_WARN] user={user_id} sym={symbol} err={hook_err}")
            else:
                logger.warning(f"[BYBIT_TRAIL_UPDATE_FAIL] user={user_id} symbol={symbol}")
            return ok

        except Exception as e:
            logger.error(f"[BYBIT_TRAIL_ADAPTER_ERR] {e}", exc_info=True)
            return False
    return _adapter


async def bybit_update_stop_loss(
    user_id: int,
    symbol_core: str,              # "BTCUSDT"
    new_sl_price: float,
    reason: str = "TRAIL",         # "TRAIL" | "BE" | "MANUAL" | "RULE"
    position_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Bybit SL güncellemesi:
      - Tüm mevcut trigger/stop emirlerini iptal eder (cancel-all)
      - Pozisyon miktarını owner map veya API ile bulur
      - Yeni SL emri (create_bybit_stop_loss) oluşturur
      - Başarıda on_sl_move çağırır ve ACTIVE_OPEN_POSITIONS state günceller
    Not: position_id opsiyonel; varsa TRAILING_POSITION_OWNERS_BYBIT üzerinden owner çözümü yapılır.
    """
    try:
        # api key al
        row = db_operation("SELECT api_key, secret_key FROM api_key WHERE user_id=? AND LOWER(exchange)='bybit'",
                           (int(user_id),), operation='select', fetch=True)
        if not row or not row[0]:
            return {"success": False, "error": "api_keys_not_found"}
        api_key, secret_key = row[0][0], row[0][1]

        symbol = validate_bybit_symbol(symbol_core)

        # qty: owner map veya API
        qty: Optional[float] = None
        if position_id is not None:
            owner = TRAILING_POSITION_OWNERS_BYBIT.get(int(position_id)) or {}
            if owner and owner.get("user_id") == int(user_id) and validate_bybit_symbol(owner.get("symbol","")) == symbol:
                try:
                    q_owner = owner.get("qty")
                    qty = float(q_owner) if q_owner is not None else None
                except Exception:
                    qty = None
        if qty is None:
            qty = await _bybit_fetch_position_qty(api_key, secret_key, symbol)
        if not qty or qty <= 0:
            return {"success": False, "error": "no_open_position_or_qty_unknown"}

        # cancel-all
        await _bybit_cancel_all_stops(api_key, secret_key, symbol)

        # new SL
        side_guess = "LONG"  # yön bilinmiyorsa LONG varsayımı; gerçek yön bilinirse owner['side'] kullanılabilir
        if position_id is not None:
            side_guess = (TRAILING_POSITION_OWNERS_BYBIT.get(int(position_id)) or {}).get("side","LONG")
        ok = await _bybit_place_new_sl(api_key, secret_key, symbol, side_guess, float(qty), float(new_sl_price))
        if not ok:
            return {"success": False, "error": "place_failed"}

        # hook
        try:
            norm_sym_key = normalize_symbol_for_key(symbol)
            state = ACTIVE_OPEN_POSITIONS.get((int(user_id), "bybit", norm_sym_key))
            on_sl_move(int(user_id), "bybit", norm_sym_key, float(new_sl_price), reason or "TRAIL", state)
        except Exception as hook_err:
            logger.warning(f"[BYBIT_UPD_SL_HOOK_WARN] uid={user_id} sym={symbol} err={hook_err}")

        logger.info(f"[BYBIT_UPD_SL_OK] uid={user_id} sym={symbol} qty={qty} stop={new_sl_price}")
        return {"success": True, "data": {"symbol": symbol, "qty": float(qty), "new_sl_price": float(new_sl_price)}}
    except Exception as e:
        logger.error(f"[BYBIT_UPD_SL_ERR] uid={user_id} sym={symbol_core} err={e}", exc_info=True)
        return {"success": False, "error": str(e)}


async def bybit_set_stop_loss_v5(
    *,
    symbol: str,
    side: str,  # "LONG" / "SHORT" (bilgi amaçlı)
    stop_loss: float,
    api_key: str,
    secret_key: str
) -> Dict[str, Any]:
    """
    Bybit V5 - gerçek SL set eder:
      POST /v5/position/trading-stop
    """
    try:
        recv_window = "5000"
        ts = str(int(time.time() * 1000))

        endpoint = "/v5/position/trading-stop"
        url = f"https://api.bybit.com{endpoint}"

        body_obj = {
            "category": "linear",
            "symbol": symbol,
            "stopLoss": str(float(stop_loss)),
            # opsiyonel:
            # "slTriggerBy": "LastPrice",
        }
        body = json.dumps(body_obj, separators=(",", ":"))

        sig = get_bybit_v5_signature(
            secret_key,
            timestamp=ts,
            api_key=api_key,
            recv_window=recv_window,
            payload=body
        )

        headers = {
            "X-BAPI-API-KEY": api_key,
            "X-BAPI-SIGN": sig,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv_window,
            "X-BAPI-SIGN-TYPE": "2",
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, data=body) as resp:
                rj = await resp.json()

        if rj.get("retCode") == 0:
            return {"success": True, "data": rj}

        logger.warning(f"[BYBIT_SET_SL_FAIL] sym={symbol} resp={rj}")
        return {"success": False, "error": rj}
    except Exception as e:
        logger.error(f"[BYBIT_SET_SL_ERR] sym={symbol} err={e}", exc_info=True)
        return {"success": False, "error": str(e)}


def validate_bybit_symbol(symbol: str) -> str:
    try:
        if symbol.startswith('1000'):
            symbol = symbol[4:]

        normalized_symbol = symbol.replace(' ', '').upper()

        if normalized_symbol.endswith('P'):
            normalized_symbol = normalized_symbol[:-1]

        if not normalized_symbol.endswith('USDT'):
            normalized_symbol += 'USDT'

        logger.info(f"""
        🔄 Sembol Normalizasyonu:
        - Gelen Sembol: {symbol}
        - Normalize Sembol: {normalized_symbol}
        """)

        return normalized_symbol

    except Exception as e:
        logger.error(f"Sembol normalizasyon hatası: {e}")
        return symbol


def _bybit_build_query(params: Dict[str, Any]) -> str:
    """
    Bybit V5 GET querystring için alfabetik sıralı 'k=v&k2=v2' üretir.
    None olanları atar.
    """
    items = []
    for k in sorted(params.keys()):
        v = params.get(k)
        if v is None:
            continue
        items.append(f"{k}={v}")
    return "&".join(items)


def get_bybit_v5_signature(
    secret_key: str,
    *,
    timestamp: str,
    api_key: str,
    recv_window: str,
    payload: str
) -> str:
    """
    Bybit V5 signature:
      sign = HMAC_SHA256(secret, timestamp + apiKey + recvWindow + payload)
    payload:
      - GET  => querystring (k=v&k2=v2)
      - POST => raw json string (aynen gönderdiğin)
    """
    prehash = f"{timestamp}{api_key}{recv_window}{payload}"
    return hmac.new(
        secret_key.encode("utf-8"),
        prehash.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


async def get_bybit_account_balance(api_key: str, secret_key: str, symbol: str) -> float:
    try:
        recv_window = "5000"
        ts = str(int(time.time() * 1000))

        query_params = {"category":"linear", "symbol":symbol}
        query = _bybit_build_query(query_params)

        sig = get_bybit_v5_signature(
            secret_key,
            timestamp=ts,
            api_key=api_key,
            recv_window=recv_window,
            payload=query
        )

        headers = {
            "X-BAPI-API-KEY":api_key,
            "X-BAPI-SIGN":sig,
            "X-BAPI-TIMESTAMP":ts,
            "X-BAPI-RECV-WINDOW":recv_window,
            "X-BAPI-SIGN-TYPE":"2",
            "Content-Type":"application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(
                    "https://api.bybit.com/v5/account/wallet-balance",
                    headers=headers,
                    params={"accountType": "UNIFIED"}
            ) as response:
                result = await response.json()

                if result.get("retCode") == 0:
                    balance = float(result["result"]["list"][0]["totalWalletBalance"])
                    logger.info(f"💰 Hesap Bakiyesi: {balance} USDT")
                    return balance
                else:
                    logger.error(f"Bakiye alma hatası: {result}")
                    return 0.0

    except Exception as e:
        logger.error(f"Bakiye sorgulama hatası: {str(e)}")
        return 0.0


async def set_bybit_leverage(
    symbol: str,
    leverage: int,
    api_key: str,
    secret_key: str
) -> Dict[str, Any]:
    """
    Bybit V5: /v5/position/set-leverage
    """
    try:
        recv_window = "5000"
        ts = str(int(time.time() * 1000))

        endpoint = "/v5/position/set-leverage"
        url = f"https://api.bybit.com{endpoint}"

        body_obj = {
            "category": "linear",
            "symbol": symbol,
            # Bybit bazı hesaplarda buyLeverage/sellLeverage ister.
            # Basit kullanım:
            "buyLeverage": str(leverage),
            "sellLeverage": str(leverage),
        }
        body = json.dumps(body_obj, separators=(",", ":"))

        sig = get_bybit_v5_signature(
            secret_key,
            timestamp=ts,
            api_key=api_key,
            recv_window=recv_window,
            payload=body
        )

        headers = {
            "X-BAPI-API-KEY": api_key,
            "X-BAPI-SIGN": sig,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv_window,
            "X-BAPI-SIGN-TYPE": "2",
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, data=body) as response:
                result = await response.json()

        if result.get("retCode") == 0:
            logger.info(f"✅ Kaldıraç ayarlandı: {leverage}x")
            return {"success": True, "data": result}

        logger.error(f"Kaldıraç ayarlama hatası: {result}")
        return {"success": False, "error": result}

    except Exception as e:
        logger.error(f"Kaldıraç ayarlama hatası: {str(e)}", exc_info=True)
        return {"success": False, "error": str(e)}



async def _bybit_place_plan_tp_orders(
    *,
    symbol: str,
    side: str,  # "LONG"/"SHORT"
    tp_structs: List[Dict[str, Any]],
    opened_qty: float,
    api_key: str,
    secret_key: str
) -> Dict[str, Any]:
    """
    plan.tp_structs -> Bybit V5 /v5/order/create ile TP limit reduceOnly emirleri.

    ÖNEMLİ:
      - volume None => opened_qty (%100)
      - volume varsa => float(volume) (büyütme yok)
    """
    result: Dict[str, Any] = {"success": False, "orders": [], "errors": []}

    if not tp_structs:
        return {"success": True, "orders": []}

    side_u = str(side or "").upper().strip()
    if side_u not in ("LONG", "SHORT"):
        return {"success": False, "error": f"invalid side: {side}"}

    close_side = "Sell" if side_u == "LONG" else "Buy"

    recv_window = "5000"
    endpoint = "/v5/order/create"
    url = f"https://api.bybit.com{endpoint}"

    async with aiohttp.ClientSession() as session:
        for idx, t in enumerate(tp_structs, start=1):
            try:
                if not isinstance(t, dict):
                    continue
                if t.get("_mode") == "SINYAL_TP" and t.get("volume") is None:
                    # plan volume üretmiyorsa executor TP yazmasın (risksiz)
                    continue

                price = t.get("price")
                if price is None:
                    continue
                tp_price = float(price)

                vol = t.get("volume")
                tp_qty = float(opened_qty if vol is None else vol)
                if tp_qty <= 0:
                    continue

                ts = str(int(time.time() * 1000))
                body_obj = {
                    "category": "linear",
                    "symbol": symbol,
                    "side": close_side,
                    "orderType": "Limit",
                    "qty": str(tp_qty),
                    "price": str(tp_price),
                    "reduceOnly": True,
                    "timeInForce": "GTC"
                }
                body = json.dumps(body_obj, separators=(",", ":"))

                sig = get_bybit_v5_signature(
                    secret_key,
                    timestamp=ts,
                    api_key=api_key,
                    recv_window=recv_window,
                    payload=body
                )

                headers = {
                    "X-BAPI-API-KEY": api_key,
                    "X-BAPI-SIGN": sig,
                    "X-BAPI-TIMESTAMP": ts,
                    "X-BAPI-RECV-WINDOW": recv_window,
                    "X-BAPI-SIGN-TYPE": "2",
                    "Content-Type": "application/json"
                }

                async with session.post(url, headers=headers, data=body) as resp:
                    rj = await resp.json()

                if rj.get("retCode") == 0:
                    result["orders"].append({"order_number": idx, "price": tp_price, "qty": tp_qty, "data": rj})
                else:
                    raise Exception(f"TP create failed: {rj}")

            except Exception as e:
                result["errors"].append({"order_number": idx, "error": str(e)})

    result["success"] = len(result["orders"]) > 0
    return result


async def _bybit_fetch_position_qty(api_key: str, secret_key: str, symbol: str) -> Optional[float]:
    """
    Bybit V5: /v5/position/list (category=linear&symbol=...)
    Pozisyon qty bulur.
    """
    try:
        recv_window = "5000"
        ts = str(int(time.time() * 1000))

        endpoint = "/v5/position/list"
        url = f"https://api.bybit.com{endpoint}"

        query_params = {"category": "linear", "symbol": symbol}
        query = _bybit_build_query(query_params)

        sig = get_bybit_v5_signature(
            secret_key,
            timestamp=ts,
            api_key=api_key,
            recv_window=recv_window,
            payload=query
        )

        headers = {
            "X-BAPI-API-KEY": api_key,
            "X-BAPI-SIGN": sig,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv_window,
            "X-BAPI-SIGN-TYPE": "2",
            "Content-Type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=query_params) as resp:
                rj = await resp.json()

        if rj.get("retCode") != 0:
            logger.warning(f"[BYBIT_POS_LIST_FAIL] sym={symbol} resp={rj}")
            return None

        lst = (((rj.get("result") or {}).get("list")) or [])
        for p in lst:
            try:
                size = float(p.get("size", 0) or 0)
            except Exception:
                size = 0.0
            if size > 0:
                return size

        return 0.0

    except Exception as e:
        logger.warning(f"[BYBIT_POS_FETCH_WARN] {e}", exc_info=True)
        return None


async def bybit_execute_plan(plan: ExecutionPlan, user: dict) -> Dict[str, Any]:
    """
    ExecutionPlan'ı Bybit V5 linear üzerinde uygular.
    - Kurallar burada üretilmez.
    - plan.contracts büyütülmez.
    """
    try:
        api_key = user.get("api_key")
        secret_key = user.get("secret_key")
        if not api_key or not secret_key:
            return {"success": False, "error": "bybit api_key/secret_key yok"}

        symbol = validate_bybit_symbol(getattr(plan, "symbol_exchange", None) or getattr(plan, "symbol_core", "") or "")
        side_u = str(plan.side).upper().strip()
        if side_u not in ("LONG", "SHORT"):
            return {"success": False, "error": f"invalid side: {plan.side}"}

        # leverage
        try:
            lev_res = await set_bybit_leverage(symbol, int(plan.leverage), api_key, secret_key)
            if not lev_res.get("success"):
                return {"success": False, "error": lev_res.get("error", lev_res)}
        except Exception as e:
            logger.warning(f"[BYBIT_SET_LEV_WARN] {e}")

        # OPEN market
        try:
            qty = float(plan.contracts)
        except Exception:
            qty = 0.0
        if qty <= 0:
            return {"success": False, "error": "plan.contracts<=0"}

        recv_window = "5000"
        ts = str(int(time.time() * 1000))

        endpoint = "/v5/order/create"
        url = f"https://api.bybit.com{endpoint}"

        body_obj = {
            "category": "linear",
            "symbol": symbol,
            "side": "Buy" if side_u == "LONG" else "Sell",
            "orderType": "Market",
            "qty": str(qty),
        }
        body = json.dumps(body_obj, separators=(",", ":"))

        sig = get_bybit_v5_signature(
            secret_key,
            timestamp=ts,
            api_key=api_key,
            recv_window=recv_window,
            payload=body
        )

        headers = {
            "X-BAPI-API-KEY": api_key,
            "X-BAPI-SIGN": sig,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv_window,
            "X-BAPI-SIGN-TYPE": "2",
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, data=body) as resp:
                open_resp = await resp.json()

        if open_resp.get("retCode") != 0:
            return {"success": False, "error": open_resp, "open_order": open_resp}

        # opened qty (gerçek pozisyon miktarı)
        opened_qty = await _bybit_fetch_position_qty(api_key, secret_key, symbol)
        if not opened_qty or opened_qty <= 0:
            opened_qty = qty  # fallback

        # SL/TP kapalıysa bitir
        if not bool(plan.sl_tp_emir):
            return {
                "success": True,
                "data": open_resp,
                "position_id": (open_resp.get("result") or {}).get("orderId"),
                "opened_qty": opened_qty,
                "tp_orders": [],
                "sl_order": None
            }

        # Eski stop/trigger iptali (kaba: cancel-all)
        try:
            await _bybit_cancel_all_stops(api_key, secret_key, symbol)
        except Exception as e:
            logger.warning(f"[BYBIT_CANCEL_ALL_WARN] {e}")

        # TP
        tp_result = {"success": True, "orders": []}
        try:
            tp_result = await _bybit_place_plan_tp_orders(
                symbol=symbol,
                side=side_u,
                tp_structs=list(plan.tp_structs or []),
                opened_qty=float(opened_qty),
                api_key=api_key,
                secret_key=secret_key
            )
        except Exception as e:
            logger.error(f"[BYBIT_EXEC_TP_ERR] {e}", exc_info=True)

        # SL
        sl_result = None
        try:
            if getattr(plan, "sl_price", None):
                sl_result = await create_bybit_stop_loss(
                    symbol=symbol,
                    side=side_u,
                    stop_price=float(plan.sl_price),
                    quantity=float(opened_qty),
                    api_key=api_key,
                    secret_key=secret_key
                )
                # INITIAL hook
                try:
                    if sl_result and isinstance(sl_result, dict) and sl_result.get("success"):
                        norm_sym_key = normalize_symbol_for_key(symbol)
                        state = ACTIVE_OPEN_POSITIONS.get((int(plan.user_id), "bybit", norm_sym_key))
                        on_sl_move(int(plan.user_id), "bybit", norm_sym_key, float(plan.sl_price), "INITIAL", state)
                except Exception as hook_err:
                    logger.warning(f"[BYBIT_SL_MOVE_INITIAL_HOOK_WARN] user={plan.user_id} sym={symbol} err={hook_err}")
        except Exception as e:
            logger.error(f"[BYBIT_EXEC_SL_ERR] {e}", exc_info=True)

        return {
            "success": True,
            "data": open_resp,
            "position_id": (open_resp.get("result") or {}).get("orderId"),
            "opened_qty": opened_qty,
            "tp_orders": tp_result.get("orders", []),
            "sl_order": sl_result,
            "tp_success": bool(tp_result.get("success", False))
        }

    except Exception as e:
        logger.error(f"[BYBIT_EXEC_PLAN_ERR] {e}", exc_info=True)
        return {"success": False, "error": str(e)}


async def create_bybit_stop_loss(
    symbol: str,
    side: str,
    stop_price: float,
    quantity: float,   # artık kullanılmasa da imzayı bozmuyoruz
    api_key: str,
    secret_key: str
) -> Dict[str, Any]:
    _ = quantity
    symbol = validate_bybit_symbol(symbol)
    return await bybit_set_stop_loss_v5(
        symbol=symbol,
        side=side,
        stop_loss=float(stop_price),
        api_key=api_key,
        secret_key=secret_key
    )



async def bybit_close_position(
    symbol: str,
    side: str,              # "LONG"/"SHORT" veya "BUY"/"SELL" gelebilir
    api_key: str,
    secret_key: str,
    lot: Optional[float] = None
) -> Dict[str, Any]:
    try:
        symbol = validate_bybit_symbol(symbol)

        # açık qty bul
        qty = await _bybit_fetch_position_qty(api_key, secret_key, symbol)
        if qty is None:
            return {"success": False, "error": "position_qty_fetch_failed"}
        if qty <= 0:
            return {"success": False, "error": "no_open_position"}

        # lot verilmişse onu kullan (ama qty’den büyük olmasın)
        if lot is not None:
            try:
                lot_f = float(lot)
                if lot_f > 0:
                    qty = min(qty, lot_f)
            except Exception:
                pass

        side_u = str(side or "").upper().strip()
        # LONG kapamak için Sell, SHORT kapamak için Buy
        close_side = "Sell" if side_u in ("LONG", "BUY") else "Buy"

        recv_window = "5000"
        ts = str(int(time.time() * 1000))
        endpoint = "/v5/order/create"
        url = f"https://api.bybit.com{endpoint}"

        body_obj = {
            "category": "linear",
            "symbol": symbol,
            "side": close_side,
            "orderType": "Market",
            "qty": str(float(qty)),
            "reduceOnly": True,
        }
        body = json.dumps(body_obj, separators=(",", ":"))

        sig = get_bybit_v5_signature(
            secret_key,
            timestamp=ts,
            api_key=api_key,
            recv_window=recv_window,
            payload=body
        )

        headers = {
            "X-BAPI-API-KEY": api_key,
            "X-BAPI-SIGN": sig,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv_window,
            "X-BAPI-SIGN-TYPE": "2",
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, data=body) as response:
                rj = await response.json()

        if rj.get("retCode") == 0:
            return {"success": True, "data": rj}

        return {"success": False, "error": rj}

    except Exception as e:
        logger.error(f"[BYBIT_CLOSE_ERR] {e}", exc_info=True)
        return {"success": False, "error": str(e)}



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

    async def send_notification(
            self,
            chat_id: int,
            message: str,
            reply_markup: Optional[InlineKeyboardMarkup] = None
    ) -> bool:
        try:
            if not self._initialized or not self._bot:
                self._initialize()
                if not self._initialized:
                    raise Exception("Bot başlatılamadı")

            await self._bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            return True
        except Exception as e:
            logger.error(f"❌ Bildirim gönderme hatası: {str(e)}")
            return False


async def send_trade_notification(
        user_id: int,
        signal_dict: dict,
        position_result: dict
):
    try:
        position_emoji = "🟢" if signal_dict['position_type'].upper() == "LONG" else "🔴"
        result_emoji = "✅" if position_result['success'] else "❌"

        notification_message = (
            f"🔔 BYBIT İşlem Bildirimi\n\n"
            f"📊 Sembol: {signal_dict['symbol']}\n"
            f"{position_emoji} Pozisyon: {signal_dict['position_type']}\n"
            f"💵 Giriş Fiyatı: {signal_dict['entry_points']}\n"
            f"🛑 Stop Loss: {signal_dict['stop_loss']}\n"
            f"🎯 Take Profit: {signal_dict['take_profits']}\n"
            f"💰 Lot: {signal_dict.get('lot', 'Belirtilmemiş')}\n"
            f"⚡ Kaldıraç: {signal_dict.get('leverage', '10')}x\n\n"
            f"📝 Sonuç: {result_emoji} "
            f"{'Başarılı' if position_result['success'] else 'Başarısız'}\n"
            f"⏰ İşlem Zamanı: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "🚨 ACİL KAPAT 🚨",
                    callback_data=json.dumps({
                        "action": "emergency_close",
                        "symbol": signal_dict['symbol'],
                        "side": signal_dict['position_type'],
                        "user_id": user_id
                    })
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        bot_manager = await TelegramBotManager.get_instance()
        return await bot_manager.send_notification(user_id, notification_message, reply_markup)

    except Exception as e:
        logger.error(f"❌ Bildirim gönderme hatası: {str(e)}")
        return False


async def handle_emergency_close_callback(update: Update, _context: CallbackContext) -> None:
    query = None

    try:
        query = update.callback_query
        if not query:
            return
        await query.answer()

        data = json.loads(query.data)
        if data.get("action") != "emergency_close":
            return

        user_id = data.get("user_id")
        symbol = data.get("symbol")
        side = data.get("side")

        user_query = """
        SELECT api_key, secret_key FROM api_key 
        WHERE user_id = ? AND exchange = 'bybit'
        """
        user_result = db_operation(user_query, (user_id,), fetch=True)

        if not user_result:
            await query.edit_message_text("❌ API bilgileri bulunamadı!")
            return

        api_key, secret_key = user_result[0]

        close_result = await bybit_close_position(
            symbol=symbol,
            side=side,
            api_key=api_key,
            secret_key=secret_key
        )

        if close_result.get("success"):
            await query.edit_message_text("✅ Pozisyon başarıyla kapatıldı!")
        else:
            await query.edit_message_text(f"❌ Hata: {close_result.get('error', 'Bilinmeyen hata')}")

    except Exception as e:
        logger.error(f"❌ Acil kapatma hatası: {str(e)}", exc_info=True)
        if query:
            try:
                await query.edit_message_text("❌ İşlem sırasında hata oluştu!")
            except Exception:
                pass