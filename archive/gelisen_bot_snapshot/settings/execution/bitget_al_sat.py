# bitget_al_sat.py dosyamız buradan başlamaktadır
from typing import List, Dict, Any, Optional, Union
import aiohttp
import json
import time
import math
import hmac
from data.olimpos_data import *
from config.constants import *
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from settings.execution_plan_types import ExecutionPlan
from telegram.ext import CallbackContext
from datetime import datetime
from urllib.parse import urlencode
import base64
from analytics.timeline_hooks import on_sl_move
from signal_merkezi import ACTIVE_OPEN_POSITIONS, normalize_symbol_for_key
from core.symbol_resolver import normalize_core_symbol
from settings.execution.symbol_meta_provider import get_symbol_meta as provider_get_symbol_meta


# === BITGET TRAILING GLOBAL ===
TRAILING_POSITION_OWNERS_BITGET = {}  # position_id -> {user_id, exchange, symbol}
_BITGET_CLIENT_CACHE = {}  # user_id -> (api_key, secret_key, passphrase)

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


def get_symbol_meta(symbol: str):
    core_sym = normalize_core_symbol(symbol)
    return provider_get_symbol_meta(exchange="bitget", symbol=core_sym)


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
            if mode and str(mode).upper() in ('PCT', 'ATR'):
                try:
                    return str(mode).upper(), float(param) if param is not None else None
                except:
                    return str(mode).upper(), None
        return None, None
    except Exception as e:
        logger.error(f"[TRAIL_FETCH_SETTING_ERR] user={user_id} ex={exchange} {e}")
        return None, None


async def _bitget_refresh_position_amount(api_key, secret_key, passphrase, normalized_symbol):
    """
    Tek pozisyon miktarını çeker (açık pozisyon yoksa 0 döner).
    """
    try:
        data = await get_bitget_position(normalized_symbol, api_key, secret_key, passphrase)
        if not data:
            return 0
        # Bitget tek yön hold: total veya holdVol benzeri anahtar olabilir.
        # Senin get_bitget_position datasında 'total' alanını kullandık yukarıda.
        size = float(data.get('total', 0) or data.get('holdVol', 0) or 0)
        return size
    except Exception as e:
        logger.error(f"[BITGET_REFRESH_POS_ERR] {e}")
        return 0


async def _bitget_replace_stop_loss(api_key, secret_key, passphrase, normalized_symbol, side, new_sl_price, amount):
    """
    Basit yaklaşım: tüm plan/stop emirlerini iptal edip yeni stop loss planı oluşturur.
    Not: TP planları da iptal olabilir; daha ince seçici iptal istersen geliştirebilirsin.
    """
    try:
        # Mevcut bütün planları/limitleri iptal (sadece stop'u iptal etmek istersen refine et)
        await cancel_all_orders(normalized_symbol, api_key, secret_key, passphrase)
        # Yeni stop
        r = await create_stop_loss_order(
            symbol=normalized_symbol.replace("_UMCBL",""),
            side=side,
            amount=amount,
            stop_price=new_sl_price,
            api_key=api_key,
            secret_key=secret_key,
            passphrase=passphrase,
            current_price=new_sl_price  # burada current_price param zorunlu olduğu için kullanıyoruz
        )
        if r.get('success'):
            logger.info(f"[BITGET_TRAIL_NEW_SL] sym={normalized_symbol} price={new_sl_price}")
            return True
        logger.warning(f"[BITGET_TRAIL_NEW_SL_FAIL] resp={r}")
        return False
    except Exception as e:
        logger.error(f"[BITGET_TRAIL_REPLACE_ERR] {e}", exc_info=True)
        return False


def build_bitget_dynamic_update_sl_adapter():
    """
    TrailingSupervisor generic loop tarafından çağrılacak.
    Bu dosyanın içindeki global cache/map'i direkt kullanır (self-import yok).
    """

    async def _adapter(position_id: int, symbol: str, new_sl_price: float) -> bool:
        owner = TRAILING_POSITION_OWNERS_BITGET.get(position_id)
        if not owner:
            return False

        user_id = owner["user_id"]

        if user_id not in _BITGET_CLIENT_CACHE:
            row = db_operation(
                "SELECT api_key, secret_key, passphrase FROM api_key "
                "WHERE user_id=? AND LOWER(exchange)='bitget'",
                (user_id,),
                operation="select",
                fetch=True
            )
            if not row or not row[0]:
                logger.warning(f"[BITGET_TRAIL_NO_API] user={user_id}")
                return False

            _BITGET_CLIENT_CACHE[user_id] = row[0]  # (api_key, secret_key, passphrase)

        api_key, secret_key, passphrase = _BITGET_CLIENT_CACHE[user_id]

        try:
            normalized_symbol = validate_bitget_symbol(symbol)           # BTCUSDT_UMCBL
            amount = await _bitget_refresh_position_amount(api_key, secret_key, passphrase, normalized_symbol)

            if amount <= 0:
                logger.info(f"[BITGET_TRAIL_NO_POS] user={user_id} symbol={symbol}")
                return False

            side = owner.get("side", "LONG")
            ok = await _bitget_replace_stop_loss(
                api_key, secret_key, passphrase,
                normalized_symbol, side, float(new_sl_price), amount
            )
            if ok:
                try:
                    core_symbol = normalized_symbol.replace("_UMCBL", "")   # BTCUSDT
                    norm_sym_key = normalize_symbol_for_key(core_symbol)
                    state = ACTIVE_OPEN_POSITIONS.get((int(user_id), "bitget", norm_sym_key))
                    on_sl_move(int(user_id), "bitget", norm_sym_key, float(new_sl_price), "TRAIL", state)
                except Exception as hook_err:
                    logger.warning(f"[BITGET_TRAIL_SL_MOVE_HOOK_WARN] user={user_id} sym={symbol} err={hook_err}")
            return bool(ok)

        except Exception as e:
            logger.error(f"[BITGET_TRAIL_ADAPTER_ERR] {e}", exc_info=True)
            return False
    return _adapter

def validate_bitget_symbol(symbol: str) -> str:
    try:
        s = (symbol or "").replace(" ", "")
        if not s:
            return symbol

        # Zaten Bitget contract formatındaysa dokunma
        if s.upper().endswith("_UMCBL"):
            return s.upper()

        # Semboldeki 1000 ön ekini kaldır (sadece base başında)
        if s.startswith("1000"):
            s = s[4:]

        normalized_symbol = s

        # Sadece sondaki P'yi kaldır
        if normalized_symbol.upper().endswith("P"):
            normalized_symbol = normalized_symbol[:-1]

        normalized_symbol = normalized_symbol.upper()

        # USDT çiftini kontrol et
        if not normalized_symbol.endswith("USDT"):
            normalized_symbol += "USDT"

        bitget_symbol = f"{normalized_symbol}_UMCBL"

        logger.info(f"""
        🔄 Sembol Normalizasyonu:
           - Gelen Sembol: {symbol}
           - Normalize Sembol: {normalized_symbol}
           - Bitget Sembol Formatı: {bitget_symbol}
        """)

        return bitget_symbol

    except Exception as e:
        logger.error(f"Sembol normalizasyon hatası: {e}", exc_info=True)
        return symbol


def get_bitget_signature(secret_key: str, timestamp: str, method: str, request_path: str, body: str = '') -> str:
    message = timestamp + method.upper() + request_path + body
    mac = hmac.new(
        secret_key.encode('utf-8'),
        message.encode('utf-8'),
        digestmod=hashlib.sha256
    )
    return base64.b64encode(mac.digest()).decode('utf-8')


async def get_bitget_account_balance(api_key: str, secret_key: str, passphrase: str) -> float:
    try:
        async with aiohttp.ClientSession() as session:
            base_url = "https://api.bitget.com"
            # Endpoint'i düzeltelim
            endpoint = "/api/mix/v1/account/account"

            # productType parametresini ekleyelim
            params = {
                "symbol": "BTCUSDT_UMCBL",  # Herhangi bir UMCBL kontratı
                "marginCoin": "USDT"
            }

            timestamp = str(int(time.time() * 1000))

            # Query string oluştur
            query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
            full_endpoint = f"{endpoint}?{query_string}"

            # İmza oluşturma
            signature = get_bitget_signature(
                secret_key,
                timestamp,
                "GET",
                full_endpoint,
                ""
            )

            headers = {
                "ACCESS-KEY": api_key,
                "ACCESS-SIGN": signature,
                "ACCESS-TIMESTAMP": timestamp,
                "ACCESS-PASSPHRASE": passphrase,
                "Content-Type": "application/json"
            }

            logger.info(f"🔍 Bakiye Sorgusu Gönderiliyor:")

            async with session.get(
                    f"{base_url}{full_endpoint}",
                    headers=headers
            ) as response:
                result = await response.json()
                logger.info(f"📊 Bakiye Sorgu Sonucu: {json.dumps(result, indent=2)}")

                if result.get('code') == '00000':
                    data = result.get('data', {})
                    available = float(data.get('available', 0))
                    total = float(data.get('usdtEquity', 0))  # usdtEquity kullan
                    frozen = float(data.get('locked', 0))

                    logger.info(f"💰 Bakiye Detayları:")
                    logger.info(f"   - Toplam Bakiye: {total} USDT")
                    logger.info(f"   - Kullanılabilir: {available} USDT")
                    logger.info(f"   - Dondurulmuş: {frozen} USDT")

                    return total  # Toplam bakiyeyi döndür
                else:
                    logger.error(f"❌ Bakiye sorgusu başarısız:")
                    logger.error(f"   - Hata Kodu: {result.get('code')}")
                    logger.error(f"   - Hata Mesajı: {result.get('msg')}")
                    return 0

    except Exception as e:
        logger.error(f"❌ Bakiye sorgulama hatası: {str(e)}")
        logger.error("Detaylı Hata", exc_info=True)
        return 0


def round_price_to_precision(price: float, symbol: str) -> float:
    """
    Fiyatı borsanın kabul ettiği hassasiyete yuvarlar
    """
    try:
        # Önce string'e çevir ve bilimsel gösterimi engelle
        price_str = "{:.8f}".format(price)
        # Noktadan sonraki kısmı al
        decimal_part = price_str.split('.')[1] if '.' in price_str else ''

        # Bitget için standart hassasiyet: 0.001 (3 decimal)
        rounded_price = round(float(price_str), 3)

        # 0.001'in katı olmasını sağla
        multiplier = 1000  # 0.001 için çarpan
        rounded_price = round(rounded_price * multiplier) / multiplier

        logger.debug(f"""
        Fiyat Yuvarlama:
        - Orijinal: {price}
        - String: {price_str}
        - Decimal: {decimal_part}
        - Yuvarlanmış: {rounded_price}
        """)

        return rounded_price

    except Exception as e:
        logger.error(f"Fiyat yuvarlama hatası: {e}")
        # Hata durumunda en azından 3 decimal'e yuvarla
        return round(price, 3)


async def validate_trade_conditions(
        symbol: str,
        side: str,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        passphrase: Optional[str] = None
) -> Dict[str, Any]:
    try:
        # Bakiye sorgulama
        balance = await get_bitget_account_balance(api_key, secret_key, passphrase)

        if balance <= 0:
            logger.error(f"❌ Hesapta bakiye yok: {balance} USDT")
            return {"success": False, "error": "Hesapta bakiye bulunmuyor"}

        return {"success": True, "balance": balance}

    except Exception as e:
        logger.error(f"Ön kontrol hatası: {e}")
        return {"success": False, "error": str(e)}


async def get_bitget_min_trade_amount(symbol: str, api_key: str, secret_key: str, passphrase: str) -> float:
    try:
        base_url = "https://api.bitget.com"
        endpoint = "/api/mix/v1/market/contracts"
        timestamp = str(int(time.time() * 1000))

        params = {
            "symbol": symbol,
            "productType": "UMCBL"
        }

        signature = get_bitget_signature(
            secret_key,
            timestamp,
            "GET",
            endpoint,
            ""
        )

        headers = {
            "ACCESS-KEY": api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": passphrase,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{base_url}{endpoint}",
                headers=headers,
                params=params
            ) as response:
                result = await response.json()
                if result.get('code') == '00000':
                    contract_info = result.get('data', [{}])[0]
                    min_trade_amount = float(contract_info.get('minTradeAmount', 5.0))
                    return min_trade_amount
                else:
                    logger.error(f"❌ Kontrat bilgisi alınamadı: {result}")
                    return 5.0  # Varsayılan değer

    except Exception as e:
        logger.error(f"❌ Kontrat bilgisi sorgulama hatası: {str(e)}")
        return 5.0  # Varsayılan değer


async def bitget_execute_plan(plan: ExecutionPlan, user: dict) -> Dict[str, Any]:
    """
    ExecutionPlan'ı Bitget UMCBL üzerinde uygular.
    Kuralları burada üretmez; sadece planı uygular.
    ÖNEMLİ:
      - plan.contracts değerini ASLA yukarı yuvarlamaz/büyütmez.
      - min trade / %50 cap kuralları plan builder + legacy wrapper'dadır.
    """
    try:
        api_key = user.get("api_key")
        secret_key = user.get("secret_key")
        passphrase = user.get("passphrase")

        if not api_key or not secret_key or not passphrase:
            return {"success": False, "error": "bitget api_key/secret_key/passphrase yok"}

        # symbol
        symbol_in = getattr(plan, "symbol_exchange", None) or getattr(plan, "symbol_core", None) or ""
        normalized_symbol = validate_bitget_symbol(symbol_in)  # -> BTCUSDT_UMCBL

        side_u = str(plan.side).upper().strip()
        if side_u not in ("LONG", "SHORT"):
            return {"success": False, "error": f"invalid side: {plan.side}"}

        # margin/leverage
        try:
            margin_mode = "CROSSED" if str(plan.margin).upper() == "CROSS" else "ISOLATED"
            await set_margin_type(normalized_symbol, margin_mode, api_key, secret_key, passphrase)
        except Exception as e:
            logger.warning(f"[BITGET_SET_MARGIN_WARN] {e}")

        try:
            await set_leverage(normalized_symbol, int(plan.leverage), side_u, api_key, secret_key, passphrase)
        except Exception as e:
            logger.warning(f"[BITGET_SET_LEV_WARN] {e}")

        # OPEN (market) - plan.contracts => Bitget size (int contracts)
        try:
            contracts_f = float(plan.contracts)
        except (TypeError, ValueError):
            return {"success": False, "error": "plan.contracts invalid"}

        # Executor büyütmez: sadece int'e indirger (plan zaten step/min uyumlu üretmeli)
        size = int(contracts_f)
        if size <= 0:
            return {"success": False, "error": f"plan.contracts<=0 (contracts={plan.contracts})"}

        open_side = f"open_{side_u.lower()}"

        position_params = {
            "symbol": normalized_symbol,
            "marginCoin": "USDT",
            "size": str(size),
            "side": open_side,
            "orderType": "market",
            "timeInForceValue": "normal"
        }

        base_url = "https://api.bitget.com"
        position_endpoint = "/api/mix/v1/order/placeOrder"
        timestamp = str(int(time.time() * 1000))
        body = json.dumps(position_params, separators=(",", ":"))
        signature = get_bitget_signature(secret_key, timestamp, "POST", position_endpoint, body)

        headers = {
            "ACCESS-KEY": api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": passphrase,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(base_url + position_endpoint, headers=headers, data=body) as resp:
                open_resp = await resp.json()

        if open_resp.get("code") != "00000":
            return {"success": False, "error": open_resp, "open_order": open_resp}

        # position fetch (gerçek açılan miktar)
        pos = await get_bitget_position(normalized_symbol, api_key, secret_key, passphrase)
        try:
            opened_amount = float(pos.get("total", 0) or pos.get("holdVol", 0) or 0)
        except Exception:
            opened_amount = 0.0

        if opened_amount <= 0:
            # fallback: açtığımız size (büyütme yok)
            opened_amount = float(size)

        # SL/TP kapalıysa bitir
        if not bool(plan.sl_tp_emir):
            return {
                "success": True,
                "data": open_resp,
                "position_id": (open_resp.get("data") or {}).get("orderId"),
                "opened_amount": opened_amount,
                "tp_orders": [],
                "sl_order": None
            }

        # mevcut emirleri iptal (basit güvenli yaklaşım)
        try:
            await cancel_all_orders(normalized_symbol, api_key, secret_key, passphrase)
        except Exception as e:
            logger.warning(f"[BITGET_CANCEL_ALL_WARN] {e}")

        # TP: plan.tp_structs
        tp_result = {"success": True, "orders": []}
        try:
            tp_result = await _bitget_place_plan_tp_orders(
                normalized_symbol=normalized_symbol,
                side=side_u,
                tp_structs=list(plan.tp_structs or []),
                opened_amount=opened_amount,
                api_key=api_key,
                secret_key=secret_key,
                passphrase=passphrase
            )
        except Exception as e:
            logger.error(f"[BITGET_EXEC_TP_ERR] {e}", exc_info=True)

        # SL: plan.sl_price
        sl_result = None
        try:
            if getattr(plan, "sl_price", None):
                sl_result = await create_stop_loss_order(
                    symbol=normalized_symbol.replace("_UMCBL", ""),
                    side=side_u,
                    amount=opened_amount,
                    stop_price=float(plan.sl_price),
                    api_key=api_key,
                    secret_key=secret_key,
                    passphrase=passphrase,
                    current_price=float(plan.entry_price)
                )
                # INITIAL hook
                try:
                    if sl_result and isinstance(sl_result, dict) and sl_result.get("success"):
                        core_symbol = normalized_symbol.replace("_UMCBL", "")
                        norm_sym_key = normalize_symbol_for_key(core_symbol)
                        uid_src = user.get("user_id") if isinstance(user, dict) else None
                        uid_val = uid_src if uid_src is not None else getattr(plan, "user_id", None)
                        uid_int = int(uid_val) if uid_val is not None else None
                        if uid_int is not None:
                            state = ACTIVE_OPEN_POSITIONS.get((uid_int, "bitget", norm_sym_key))
                            on_sl_move(uid_int, "bitget", norm_sym_key, float(plan.sl_price), "INITIAL", state)
                except Exception as hook_err:
                    logger.warning(
                        f"[BITGET_SL_MOVE_INITIAL_HOOK_WARN] user={user.get('user_id') if isinstance(user, dict) else plan.user_id} "
                        f"sym={normalized_symbol} err={hook_err}"
                    )
        except Exception as e:
            logger.error(f"[BITGET_EXEC_SL_ERR] {e}", exc_info=True)

        return {
            "success": True,
            "data": open_resp,
            "position_id": (open_resp.get("data") or {}).get("orderId"),
            "opened_amount": opened_amount,
            "tp_orders": tp_result.get("orders", []),
            "sl_order": sl_result,
            "tp_success": bool(tp_result.get("success", False))
        }

    except Exception as e:
        logger.error(f"[BITGET_EXEC_PLAN_ERR] {e}", exc_info=True)
        return {"success": False, "error": str(e)}


async def _bitget_place_plan_tp_orders(
    *,
    normalized_symbol: str,
    side: str,  # "LONG" / "SHORT"
    tp_structs: List[Dict[str, Any]],
    opened_amount: float,
    api_key: str,
    secret_key: str,
    passphrase: str
) -> Dict[str, Any]:
    """
    Plan'dan gelen TP emirlerini Bitget plan/placePlan ile oluşturur.

    ÖNEMLİ:
      - Bu fonksiyon planın belirlediği volume'u büyütmez.
      - volume None ise (override): opened_amount ile %100 kapatır.
      - volume varsa: int(volume) kullanır (floor mantığı).
    """
    result: Dict[str, Any] = {"success": False, "orders": [], "errors": []}

    if not tp_structs:
        return {"success": True, "orders": []}

    side_u = str(side or "").upper().strip()
    if side_u not in ("LONG", "SHORT"):
        return {"success": False, "error": f"invalid side: {side}"}

    close_side = f"close_{side_u.lower()}"

    endpoint = "/api/mix/v1/plan/placePlan"
    base_url = "https://api.bitget.com"

    async with aiohttp.ClientSession() as session:
        for idx, t in enumerate(tp_structs, start=1):
            try:
                if not isinstance(t, dict):
                    continue

                # Plan bazı modlarda _mode koyabiliyor; Bitget executor volume istiyor.
                if t.get("_mode") == "SINYAL_TP" and (t.get("volume") is None):
                    # volume yoksa burada TP koymak riskli; atla.
                    # (İstersen plan builder'ı Bitget için volume üretecek şekilde zorlayalım.)
                    continue

                price = t.get("price")
                if price is None:
                    continue
                tp_price = float(price)

                vol = t.get("volume")
                if vol is None:
                    vol_f = float(opened_amount)
                else:
                    vol_f = float(vol)

                tp_amount = int(vol_f)  # büyütme yok
                if tp_amount <= 0:
                    continue

                tp_price_q = round_price_to_precision(tp_price, normalized_symbol.replace("_UMCBL", ""))

                order_params = {
                    "symbol": normalized_symbol,
                    "marginCoin": "USDT",
                    "size": str(tp_amount),
                    "triggerPrice": str(tp_price_q),
                    "executePrice": str(tp_price_q),
                    "side": close_side,
                    "orderType": "limit",
                    "triggerType": "mark_price",
                    "timeInForceValue": "post_only",
                    "reduceOnly": True
                }

                timestamp = str(int(time.time() * 1000))
                body = json.dumps(order_params, separators=(",", ":"))

                signature = get_bitget_signature(secret_key, timestamp, "POST", endpoint, body)
                headers = {
                    "ACCESS-KEY": api_key,
                    "ACCESS-SIGN": signature,
                    "ACCESS-TIMESTAMP": timestamp,
                    "ACCESS-PASSPHRASE": passphrase,
                    "Content-Type": "application/json"
                }

                async with session.post(f"{base_url}{endpoint}", headers=headers, data=body) as resp:
                    rj = await resp.json()

                if rj.get("code") == "00000":
                    result["orders"].append({
                        "order_number": idx,
                        "price": tp_price_q,
                        "amount": tp_amount,
                        "order_id": (rj.get("data") or {}).get("orderId")
                    })
                else:
                    raise Exception(f"TP create failed: {rj}")

            except Exception as e:
                result["errors"].append({"order_number": idx, "error": str(e)})

    result["success"] = len(result["orders"]) > 0
    return result


def get_bitget_margin_type(user_id: str) -> str:
    try:
        margin_query = """
        SELECT margin FROM ayarlar 
        WHERE user_id = ? AND LOWER(exchange) = 'bitget'
        """
        margin_result = db_operation(margin_query, (user_id,), fetch=True)
        return margin_result[0][0] if margin_result and margin_result[0] else "CROSSED"
    except Exception as e:
        logger.error(f"Margin type çekme hatası: {e}")
        return "CROSSED"


async def set_margin_type(symbol: str, margin_type: str, api_key: str, secret_key: str, passphrase: str):
    try:
        base_url = "https://api.bitget.com"
        endpoint = "/api/mix/v1/account/setMarginMode"
        timestamp = str(int(time.time() * 1000))

        params = {
            "symbol": symbol,
            "marginCoin": "USDT",
            "marginMode": margin_type  # CROSSED veya ISOLATED
        }

        body = json.dumps(params)
        signature = get_bitget_signature(secret_key, timestamp, "POST", endpoint, body)

        headers = {
            "ACCESS-KEY": api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": passphrase,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                    base_url + endpoint,
                    headers=headers,
                    data=body
            ) as response:
                result = await response.json()
                return result

    except Exception as e:
        logger.error(f"Margin type ayarlama hatası: {str(e)}")
        return None


async def set_leverage(symbol: str, leverage: int, side: str, api_key: str, secret_key: str, passphrase: str):
    try:
        base_url = "https://api.bitget.com"
        endpoint = "/api/mix/v1/account/setLeverage"
        timestamp = str(int(time.time() * 1000))

        params = {
            "symbol": symbol,
            "marginCoin": "USDT",
            "leverage": str(leverage),
            "holdSide": side.lower()
        }

        body = json.dumps(params)
        signature = get_bitget_signature(
            secret_key,
            timestamp,
            "POST",
            endpoint,
            body
        )

        headers = {
            "ACCESS-KEY": api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": passphrase,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                    base_url + endpoint,
                    headers=headers,
                    data=body
            ) as response:
                result = await response.json()

                if result.get('code') != '00000':
                    logger.error(f"❌ Kaldıraç Ayarlama Hatası: {result}")
                    return {
                        "success": False,
                        "error": f"Kaldıraç Ayarlama Hatası: {result}"
                    }

                return {
                    "success": True,
                    "data": result
                }

    except Exception as e:
        logger.error(f"❌ Kaldıraç Ayarlama İşlemi Hatası: {str(e)}")
        return {
            "success": False,
            "error": f"Kaldıraç Ayarlama İşlemi Hatası: {str(e)}"
        }


async def get_bitget_position(
        symbol: str,
        api_key: str,
        secret_key: str,
        passphrase: str
) -> Dict:
    try:
        base_url = "https://api.bitget.com"
        endpoint = "/api/mix/v1/position/singlePosition"
        timestamp = str(int(time.time() * 1000))

        params = {
            "symbol": symbol,
            "marginCoin": "USDT"
        }

        # Params'ı query string'e çevir
        query_string = urlencode(params)
        full_endpoint = f"{endpoint}?{query_string}"

        signature = get_bitget_signature(
            secret_key,
            timestamp,
            "GET",
            full_endpoint,
            ""
        )

        headers = {
            "ACCESS-KEY": api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": passphrase,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}{full_endpoint}", headers=headers) as response:
                result = await response.json()

                logger.info(f"""
                🔍 Pozisyon Sorgu Detayları:
                - Endpoint: {full_endpoint}
                - Timestamp: {timestamp}
                - Headers: {headers}
                - Sonuç Kodu: {result.get('code')}
                - Mesaj: {result.get('msg')}
                """)

                if result.get('code') != '00000':
                    return {}

                data = result.get('data', {})
                if isinstance(data, list):
                    data = data[0] if data else {}
                return data
    except Exception as e:
        logger.error(f"❌ Pozisyon sorgulama hatası: {str(e)}")
        return {}


def calculate_take_profit_prices(
        entry_price: float,
        side: PositionSide,
        tp_percentages: List[float],
        signal_tp_prices: Optional[List[float]] = None,
        position_amount: Optional[float] = None  # Yeni eklenen parametre
) -> List[float]:
    """
    Take profit fiyatlarını hesaplar

    Args:
        entry_price (float): Giriş fiyatı
        side (PositionSide): Pozisyon yönü
        tp_percentages (List[float]): Take profit yüzdeleri
        signal_tp_prices (Optional[List[float]]): Sinyalden gelen TP fiyatları
        position_amount (Optional[float]): Açılan pozisyon miktarı

    Returns:
        List[float]: Hesaplanan take profit fiyatları
    """
    # Eğer sinyal TP fiyatları varsa onları kullan
    if signal_tp_prices:
        return signal_tp_prices

    take_profit_prices = []

    for percentage in tp_percentages:
        if side == PositionSide.LONG:
            tp_price = entry_price * (1 + (percentage / 100))
        else:  # SHORT
            tp_price = entry_price * (1 - (percentage / 100))

        # Her hesaplanan TP fiyatını listeye ekle
        take_profit_prices.append(tp_price)

    # Pozisyon miktarı için ek kontrol ve log
    if position_amount is not None:
        logger.info(f"""
        🎯 Take Profit Hesaplama Detayları:
        💰 Pozisyon Miktarı: {position_amount}
        📈 Giriş Fiyatı: {entry_price}
        📊 Hesaplanan TP Fiyatları: {take_profit_prices}
        🔀 Pozisyon Yönü: {side}
        """)

    return take_profit_prices


async def create_take_profit_orders(
        exchange,
        symbol: str,
        side: Union[str, PositionSide],
        current_price: float,
        order_amount: float,
        user_id: str,
        api_key: str,
        secret_key: str,
        passphrase: str,
        signal_tp_prices: Optional[List[float]] = None
) -> Dict[str, Any]:
    try:
        # Side'ı PositionSide enum'una dönüştür
        if isinstance(side, str):
            side = PositionSide(side.upper())

        # Sembol normalizasyonu
        normalized_symbol = validate_bitget_symbol(symbol)

        # TP yüzdelerini al
        db_tp_percentages = get_tp_percentages(user_id) or [20.0, 20.0, 20.0, 20.0, 20.0]

        # TP fiyatlarını hesapla
        if not signal_tp_prices:
            signal_tp_prices = calculate_take_profit_prices(
                entry_price=current_price,
                side=side,
                tp_percentages=db_tp_percentages,
                position_amount=order_amount
            )

        result = {
            'success': False,
            'orders': [],
            'errors': []
        }

        min_lot_size = 1

        # Her bir TP için emir oluştur
        for i, (tp_price, tp_percentage) in enumerate(zip(signal_tp_prices, db_tp_percentages), 1):
            try:
                tp_amount = max(
                    round(order_amount * (tp_percentage / 100), 8),
                    min_lot_size
                )
                tp_amount_int = int(tp_amount)

                order_params = {
                    "symbol": normalized_symbol,
                    "marginCoin": "USDT",
                    "size": str(tp_amount_int),
                    "triggerPrice": str(round_price_to_precision(tp_price, symbol)),
                    "executePrice": str(round_price_to_precision(tp_price, symbol)),  # Tetikleme fiyatını ekle
                    "side": f"close_{side.value.lower()}",
                    "orderType": "limit",
                    "triggerType": "mark_price",
                    "timeInForceValue": "post_only",
                    "reduceOnly": True
                }

                timestamp = str(int(time.time() * 1000))
                endpoint = "/api/mix/v1/plan/placePlan"

                body = json.dumps(order_params)
                signature = get_bitget_signature(
                    secret_key,
                    timestamp,
                    "POST",
                    endpoint,
                    body
                )

                headers = {
                    "ACCESS-KEY": api_key,
                    "ACCESS-SIGN": signature,
                    "ACCESS-TIMESTAMP": timestamp,
                    "ACCESS-PASSPHRASE": passphrase,
                    "Content-Type": "application/json"
                }

                async with aiohttp.ClientSession() as session:
                    async with session.post(
                            f"https://api.bitget.com{endpoint}",
                            headers=headers,
                            data=body
                    ) as response:
                        tp_order = await response.json()

                        if tp_order.get('code') == '00000':
                            result['orders'].append({
                                'order_number':i,
                                'percentage':tp_percentage,
                                'price':tp_price,
                                'amount':tp_amount_int,
                                'order_id':(tp_order.get('data') or {}).get('orderId')
                            })
                            logger.info(f"✅ TP{i} limit emri başarıyla oluşturuldu")
                        else:
                            raise Exception(f"TP limit emri oluşturma hatası: {tp_order}")

            except Exception as e:
                logger.error(f"❌ TP{i} limit emri oluşturma hatası: {str(e)}")
                result['errors'].append({
                    'order_number': i,
                    'percentage': tp_percentage,
                    'error': str(e)
                })

        result['success'] = len(result['orders']) > 0
        return result

    except Exception as e:
        logger.error(f"❌ Take Profit limit emirleri oluşturma hatası: {str(e)}")
        return {
            'success': False,
            'error': str(e)
        }


def handle_bitget_order_error(error_response):
    error_code = error_response.get('code')
    error_message = error_response.get('message', '')

    error_map = {
        '40762': f'Yetersiz Bakiye - İşlem tutarı ({error_message})',
        '40763': 'Minimum İşlem Limiti Altında',
        '45010': 'Geçersiz Lot Büyüklüğü',
        '45019': 'İşlem Miktarı Minimum Değerin Altında',
        '45020': 'İşlem Miktarı Maksimum Değerin Üstünde'
    }

    detailed_error = error_map.get(error_code, 'Bilinmeyen Hata')

    logger.error(f"""
    ❌ İşlem Hatası Detayları:
    🔢 Hata Kodu: {error_code}
    📝 Hata Mesajı: {error_message}
    ℹ️ Açıklama: {detailed_error}
    """)

    return {
        "success": False,
        "error": detailed_error,
        "code": error_code,
        "original_message": error_message
    }


def calculate_stop_loss_price(
        entry_price: float,
        side: str,
        symbol: str,  # symbol parametresi eklendi
        stop_loss: Optional[str] = None,
        sl_percentage: Optional[Union[float, str]] = None
) -> float:
    """
    Stop loss fiyatını Bitget için hesaplar

    Args:
        entry_price (float): Giriş fiyatı
        side (str): İşlem yönü (LONG/SHORT)
        symbol (str): İşlem sembolü
        stop_loss (Optional[str]): Stop loss fiyatı
        sl_percentage (Optional[Union[float, str]]): Stop loss yüzdesi
    """
    try:
        if stop_loss:
            return round_price_to_precision(float(stop_loss), symbol)

        if sl_percentage:
            # Yüzde işaretini kaldır ve float'a çevir
            if isinstance(sl_percentage, str):
                sl_percentage = float(sl_percentage.replace('%', ''))

            # Long ve Short için SL hesaplama
            if side.upper() == "LONG":
                sl_price = entry_price * (1 - (sl_percentage / 100))
            else:
                sl_price = entry_price * (1 + (sl_percentage / 100))

            return round_price_to_precision(sl_price, symbol)

        return 0.0

    except Exception as e:
        logger.error(f"Stop loss hesaplama hatası: {e}")
        return 0.0


async def create_stop_loss_order(
        symbol: str,
        side: str,
        amount: float,
        stop_price: float,
        api_key: str,
        secret_key: str,
        passphrase: str,
        current_price: float  # Mevcut fiyatı parametre olarak ekleyelim
) -> Dict[str, Any]:
    try:
        normalized_symbol = validate_bitget_symbol(symbol)

        # Stop loss emir parametreleri
        order_params = {
            "symbol": normalized_symbol,
            "marginCoin": "USDT",
            "size": str(int(amount)),  # Tüm miktarı kullan
            "triggerPrice": str(round_price_to_precision(stop_price, symbol)),
            "executePrice": str(round_price_to_precision(stop_price, symbol)),  # Tetikleme fiyatını ekle
            "side": f"close_{side.lower()}",
            "orderType": "limit",
            "triggerType": "mark_price",
            "timeInForceValue": "post_only",
            "reduceOnly": True,
            "presetStopType": "loss"
        }

        # API isteği için hazırlık
        base_url = "https://api.bitget.com"
        endpoint = "/api/mix/v1/plan/placePlan"
        timestamp = str(int(time.time() * 1000))

        body = json.dumps(order_params)
        signature = get_bitget_signature(
            secret_key,
            timestamp,
            "POST",
            endpoint,
            body
        )

        headers = {
            "ACCESS-KEY": api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": passphrase,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                    f"{base_url}{endpoint}",
                    headers=headers,
                    data=body
            ) as response:
                result = await response.json()

                if result.get('code') == '00000':
                    logger.info(f"""
                    ✅ Stop Loss Limit Emri Oluşturuldu:
                    🔄 Sembol: {normalized_symbol}
                    💰 Miktar: {amount} (Tüm Pozisyon)
                    🎯 Tetikleme Fiyatı: {stop_price}
                    """)
                    return {"success": True, "data": result.get('data')}
                else:
                    logger.error(f"Stop loss limit emri oluşturma hatası: {result}")
                    return {"success": False, "error": result.get('msg')}

    except Exception as e:
        logger.error(f"Stop loss limit emri oluşturma hatası: {e}")
        return {"success": False, "error": str(e)}


async def modify_position_orders(
        symbol: str,
        side: str,
        api_key: str,
        secret_key: str,
        passphrase: str,
        user_id: int,  # user_id parametresi eklendi
        stop_loss: Optional[float] = None,
        take_profits: Optional[List[float]] = None
) -> Dict[str, Any]:
    """
    Mevcut pozisyonun stop loss ve take profit emirlerini günceller

    Args:
        symbol (str): İşlem sembolü
        side (str): İşlem yönü
        api_key (str): API anahtarı
        secret_key (str): API gizli anahtarı
        passphrase (str): API parolası
        user_id (int): Kullanıcı ID'si
        stop_loss (Optional[float]): Stop loss fiyatı
        take_profits (Optional[List[float]]): Take profit fiyatları listesi
    """
    try:
        normalized_symbol = validate_bitget_symbol(symbol)

        # Mevcut pozisyonu kontrol et
        position = await get_bitget_position(normalized_symbol, api_key, secret_key, passphrase)

        if not position or float(position.get('total', 0)) <= 0:
            return {"success": False, "error": "Açık pozisyon bulunamadı"}

        # Mevcut emirleri iptal et
        await cancel_all_orders(normalized_symbol, api_key, secret_key, passphrase)

        results = {
            "success": True,
            "stop_loss": None,
            "take_profits": []
        }

        # Yeni stop loss emri oluştur
        if stop_loss:
            sl_result = await create_stop_loss_order(
                symbol=normalized_symbol,
                side=side,
                current_price=float(position['averageOpenPrice']),
                amount=float(position['total']),
                stop_price=stop_loss,
                api_key=api_key,
                secret_key=secret_key,
                passphrase=passphrase
            )
            results["stop_loss"] = sl_result

        # Yeni take profit emirleri oluştur
        if take_profits:
            tp_result = await create_take_profit_orders(
                exchange=None,  # Bitget için gerekli değil
                symbol=normalized_symbol,
                side=side,
                current_price=float(position['averageOpenPrice']),
                order_amount=float(position['total']),
                user_id=str(user_id),
                api_key=api_key,
                secret_key=secret_key,
                passphrase=passphrase,
                signal_tp_prices=take_profits
            )
            results["take_profits"] = tp_result

        return results

    except Exception as e:
        logger.error(f"Pozisyon emirleri güncelleme hatası: {e}")
        return {"success": False, "error": str(e)}


async def cancel_all_orders(
        symbol: str,
        api_key: str,
        secret_key: str,
        passphrase: str
) -> Dict[str, Any]:
    """
    Tüm bekleyen emirleri iptal eder
    """
    try:
        base_url = "https://api.bitget.com"
        endpoint = "/api/mix/v1/order/cancelAllOrders"
        timestamp = str(int(time.time() * 1000))

        params = {
            "symbol": symbol,
            "marginCoin": "USDT"
        }

        body = json.dumps(params)
        signature = get_bitget_signature(
            secret_key,
            timestamp,
            "POST",
            endpoint,
            body
        )

        headers = {
            "ACCESS-KEY": api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": passphrase,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                    f"{base_url}{endpoint}",
                    headers=headers,
                    data=body
            ) as response:
                result = await response.json()

                if result.get('code') == '00000':
                    logger.info(f"✅ Tüm emirler başarıyla iptal edildi - {symbol}")
                    return {"success": True, "data": result.get('data')}
                else:
                    logger.error(f"Emir iptal hatası: {result}")
                    return {"success": False, "error": result.get('msg')}

    except Exception as e:
        logger.error(f"Emir iptal işlemi hatası: {e}")
        return {"success": False, "error": str(e)}


async def bitget_close_position(
    symbol: str,
    side: str,
    api_key: str,
    secret_key: str,
    passphrase: str,
    user_id: Optional[str] = None,
    lot: Optional[float] = None,
) -> Dict[str, Any]:
    try:
        normalized_symbol = validate_bitget_symbol(symbol)

        # 1) Açık pozisyon qty çek
        pos = await get_bitget_position(normalized_symbol, api_key, secret_key, passphrase)
        try:
            qty = float(pos.get("total", 0) or pos.get("holdVol", 0) or 0)
        except Exception:
            qty = 0.0

        if qty <= 0:
            return {"success": False, "error": "no_open_position", "data": pos}

        # 2) lot verilmişse büyütmeden uygula (floor + min)
        if lot is not None:
            try:
                lot_f = float(lot)
                if lot_f > 0:
                    qty = min(qty, lot_f)
            except Exception:
                pass

        size_int = int(math.floor(qty))
        if size_int <= 0:
            return {"success": False, "error": "close_size_zero", "qty": qty, "lot": lot}

        # 3) Side normalize (LONG/SHORT)
        side_u = str(side or "").upper().strip()
        close_side = "close_long" if side_u in ("LONG", "BUY") else "close_short"

        base_url = "https://api.bitget.com"
        timestamp = str(int(time.time() * 1000))
        close_endpoint = "/api/mix/v1/order/placeOrder"

        close_params = {
            "symbol": normalized_symbol,
            "marginCoin": "USDT",
            "side": close_side,
            "orderType": "market",
            "size": str(size_int)
        }

        close_body = json.dumps(close_params, separators=(",", ":"))
        close_signature = get_bitget_signature(secret_key, timestamp, "POST", close_endpoint, close_body)

        close_headers = {
            "ACCESS-KEY": api_key,
            "ACCESS-SIGN": close_signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": passphrase,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(base_url + close_endpoint, headers=close_headers, data=close_body) as r:
                close_result = await r.json()

        return {
            "success": close_result.get("code") == "00000",
            "data": close_result,
            "closed_size": size_int
        }

    except Exception as e:
        logger.error(f"[BITGET_CLOSE_ERR] {e}", exc_info=True)
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
            f"🔔 BİTGET İşlem Bildirimi\n\n"
            f"📊 Sembol: {signal_dict['symbol']}\n"
            f"{position_emoji} Pozisyon: {signal_dict['position_type']}\n"
            f"💵 Giriş Fiyatları: {signal_dict['entry_points']}\n"
            f"🛑 Stop Loss: {signal_dict['stop_loss']}\n"
            f"🎯 Hedefler:\n{format_take_profits(signal_dict['take_profits'])}\n\n"
            f"💰 Lot: {signal_dict.get('lot', 'Belirtilmemiş')}\n"
            f"⚡ Kaldıraç: {signal_dict.get('leverage', '10')}x\n\n"
            f"📝 Sonuç: {result_emoji} "
            f"{'Başarılı' if position_result['success'] else 'Başarısız'}\n"
            f"⏰ İşlem Zamanı: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"ℹ️ Detay: {position_result.get('error', 'İşlem tamamlandı')}"
        )
        # Acil Kapatma Butonu için inline keyboard oluştur
        keyboard = [
            [
                InlineKeyboardButton(
                    "🚨 ACİL KAPAT 🚨",
                    callback_data=json.dumps({
                        "action": "emergency_close",
                        'symbol': signal_dict.get('symbol'),
                        'side': signal_dict.get('position_type'),
                        "user_id": user_id
                    })
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Bot instance'ını al
        bot_manager = await TelegramBotManager.get_instance()

        # Direkt user_id'yi kullanarak bildirim ve butonu gönder
        if await bot_manager.send_notification(
                chat_id=user_id,
                message=notification_message,
                reply_markup=reply_markup
        ):
            logger.info(f"✅ Bitget Bildirim başarıyla gönderildi - Kullanıcı ID: {user_id}")
            return True
        else:
            logger.error(f"❌ Bildirim gönderilemedi - Kullanıcı ID: {user_id}")
            return False

    except Exception as e:
        logger.error(f"❌ Bildirim gönderme hatası: {str(e)}")
        return False


async def handle_emergency_close_callback(update: Update, _context: CallbackContext) -> State:
    query = None
    try:
        query = update.callback_query
        await query.answer()

        # JSON formatındaki callback_data'yı parse et
        callback_data = json.loads(query.data)

        # Gerekli bilgileri çıkart
        action = callback_data.get('action')
        if action != 'emergency_close':
            return State.MAIN_MENU

        symbol = callback_data.get('symbol')
        side = callback_data.get('side')
        user_id = callback_data.get('user_id')

        # Kullanıcı bilgilerini al
        user_query = """
        SELECT api_key, secret_key, passphrase 
        FROM api_key 
        WHERE user_id = ?
        """
        user_result = db_operation(user_query, (user_id,), fetch=True)

        if not user_result:
            await query.edit_message_text("❌ Kullanıcı bilgileri bulunamadı!")
            return State.MAIN_MENU

        api_key, secret_key, passphrase = user_result[0]

        # Pozisyonu kapat
        close_result = await bitget_close_position(
            symbol=symbol,
            side=side,
            api_key=api_key,
            secret_key=secret_key,
            passphrase=passphrase,
            user_id=str(user_id)
        )

        # Sonucu bildir
        if close_result.get('success'):
            await query.edit_message_text("🟢 Pozisyon başarıyla kapatıldı!")
        else:
            await query.edit_message_text(
                f"🔴 Hata: {close_result.get('error', 'Bilinmeyen Hata')}"
            )

        return State.MAIN_MENU

    except Exception as e:
        logger.error(f"Acil kapatma hatası: {e}")
        await query.edit_message_text("❌ İşlem sırasında hata oluştu!")
        return State.MAIN_MENU
