# settings/execution/okx_al_sat.py

import json
import hmac
import math
from typing import Dict, Any, Optional
import aiohttp
from data.olimpos_data import *
from config.constants import *
from telegram import Bot
from datetime import datetime, timezone
import base64
import traceback
import hashlib
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
    return provider_get_symbol_meta(exchange="okx", symbol=core_sym)


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
                except:
                    return str(mode).upper(), None
        return None, None
    except Exception as e:
        logger.error(f"[TRAIL_FETCH_SETTING_ERR] user={user_id} ex={exchange} {e}")
        return None, None


# === OKX TRAILING GLOBAL ===
TRAILING_POSITION_OWNERS_OKX = {}  # position_id -> {user_id, exchange, symbol, side}
_OKX_KEY_CACHE = {}


async def _okx_fetch_position_amount(api_key, secret_key, passphrase, normalized_symbol):
    """
    open position size döndür (pozisyon yoksa 0)
    """
    try:
        pos = await get_okx_position(normalized_symbol, api_key, secret_key, passphrase)
        if not pos:
            return 0
        amt = abs(float(pos.get('pos', 0)))
        return amt
    except Exception as e:
        logger.error(f"[OKX_FETCH_POS_ERR] {e}")
        return 0


async def _okx_cancel_triggers(api_key, secret_key, passphrase, normalized_symbol):
    try:
        # Placeholder: ileride trigger listesi çekilip filtrelenebilir.
        logger.debug(f"[OKX_CANCEL_TRIGGERS] sym={normalized_symbol} api_key_present={bool(api_key)}")
        return True
    except Exception as e:
        logger.error(f"[OKX_CANCEL_TRIGGERS_ERR] {e}")
        return False



async def _okx_place_new_stop(api_key, secret_key, passphrase, normalized_symbol, side, amount, new_sl_price):
    try:
        r = await create_okx_stop_loss_order(
            symbol=normalized_symbol.replace('-USDT','USDT'),
            side=side,
            amount=amount,
            current_price=new_sl_price,
            api_key=api_key,
            secret_key=secret_key,
            passphrase=passphrase,
            stop_price=new_sl_price
        )
        return r.get('success', False)
    except Exception as e:
        logger.error(f"[OKX_NEW_SL_ERR] {e}")
        return False


def build_okx_dynamic_update_sl_adapter():

    async def _adapter(position_id: int, symbol: str, new_sl_price: float) -> bool:
        owner = TRAILING_POSITION_OWNERS_OKX.get(position_id)
        if not owner:
            return False
        user_id = int(owner['user_id'])
        if user_id not in _OKX_KEY_CACHE:
            row = db_operation("SELECT api_key, secret_key, passphrase FROM api_key "
                               "WHERE user_id=? AND LOWER(exchange)='okx'",
                (user_id,), operation='select', fetch=True)
            if not row or not row[0]:
                logger.warning(f"[OKX_TRAIL_NO_API] user={user_id}")
                return False
            _OKX_KEY_CACHE[user_id] = row[0]
        api_key, secret_key, passphrase = _OKX_KEY_CACHE[user_id]
        try:
            normalized = validate_okx_symbol(symbol)
            amt = await _okx_fetch_position_amount(api_key, secret_key, passphrase, normalized)
            if amt <= 0:
                logger.info(f"[OKX_TRAIL_NO_POS] user={user_id} symbol={symbol}")
                return False
            await _okx_cancel_triggers(api_key, secret_key, passphrase, normalized)
            ok = await _okx_place_new_stop(api_key, secret_key, passphrase,
                normalized, owner.get('side', 'long'), amt, new_sl_price)
            if ok:
                logger.info(f"[OKX_TRAIL_UPDATED] user={user_id} symbol={symbol} price={new_sl_price}")
                # SL_MOVE hook
                try:
                    # normalize_symbol_for_key OKX için core "BTCUSDT" key'i ile çalışmalı
                    core_symbol = normalized.replace("-USDT", "USDT")
                    norm_sym_key = normalize_symbol_for_key(core_symbol)
                    state = ACTIVE_OPEN_POSITIONS.get((int(user_id), "okx", norm_sym_key))
                    on_sl_move(int(user_id), "okx", norm_sym_key, float(new_sl_price), "TRAIL", state)
                except Exception as hook_err:
                    logger.warning(f"[OKX_TRAIL_SL_MOVE_HOOK_WARN] user={user_id} sym={symbol} err={hook_err}")
            else:
                logger.warning(f"[OKX_TRAIL_UPDATE_FAIL] user={user_id} symbol={symbol}")
            return ok

        except Exception as e:
            logger.error(f"[OKX_TRAIL_ADAPTER_ERR] {e}", exc_info=True)
            return False
    return _adapter


def validate_okx_symbol(symbol: str) -> str:
    try:
        s = (symbol or "").replace(" ", "")
        if not s:
            return symbol

        # 1000 prefix'i kaldır
        if s.startswith("1000"):
            s = s[4:]

        s_u = s.upper()

        # -SWAP varsa temizle (OKX'te bazı formatlar böyle gelebiliyor)
        if s_u.endswith("-SWAP"):
            s_u = s_u[:-5]

        # Sondaki P'yi kaldır
        if s_u.endswith("P"):
            s_u = s_u[:-1]

        # Zaten OKX spot formatı (BTC-USDT) ise dokunma
        if "-" in s_u and s_u.endswith("USDT"):
            # ör: BTC-USDT
            return s_u

        # BTCUSDT gibi geldiyse -> BTC-USDT
        if s_u.endswith("USDT"):
            base = s_u[:-4]
            okx_symbol = f"{base}-USDT"
        else:
            # USDT yoksa ekleyip dönüştür
            okx_symbol = f"{s_u}-USDT"

        logger.info(f"""
        🔄 Sembol Normalizasyonu:
           - Gelen Sembol: {symbol}
           - OKX Sembol Formatı: {okx_symbol}
        """)

        return okx_symbol

    except Exception as e:
        logger.error(f"Sembol normalizasyon hatası: {e}", exc_info=True)
        return symbol


def calculate_okx_signature(secret_key: str, timestamp: str, method: str, request_path: str, body: str = "") -> str:
    message = timestamp + method.upper() + request_path + body
    mac = hmac.new(
        secret_key.encode("utf-8"),
        message.encode("utf-8"),
        digestmod=hashlib.sha256
    )
    return base64.b64encode(mac.digest()).decode("utf-8")


async def get_okx_account_balance(api_key: str, secret_key: str, passphrase: str) -> float:
    try:
        base_url = "https://www.okx.com"
        endpoint = "/api/v5/account/balance"
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

        signature = calculate_okx_signature(
            secret_key,
            timestamp,
            "GET",
            endpoint
        )

        headers = {
            "OK-ACCESS-KEY": api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": passphrase,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}{endpoint}", headers=headers) as response:
                result = await response.json()

                if result.get('code') == '0':
                    data = result.get('data', [{}])[0]
                    total_balance = float(data.get('totalEq', 0))

                    logger.info(f"""
                    💰 OKX Bakiye Detayları:
                    - Toplam Bakiye: {total_balance} USDT
                    """)

                    return total_balance
                else:
                    logger.error(f"❌ Bakiye sorgusu başarısız: {result}")
                    return 0.0

    except Exception as e:
        logger.error(f"❌ Bakiye sorgulama hatası: {str(e)}")
        return 0.0


async def set_okx_leverage(
        symbol: str,
        leverage: int,
        side: str,
        api_key: str,
        secret_key: str,
        passphrase: str
) -> Dict[str, Any]:
    try:
        base_url = "https://www.okx.com"
        endpoint = "/api/v5/account/set-leverage"
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

        # Burada bir sembol listesi kontrolü yapmak yerine,
        # sembolu normalize ettiğinize güvenebilirsiniz
        params = {
            "instId": f"{validate_okx_symbol(symbol)}-SWAP",
            "lever": str(leverage),
            "mgnMode": "cross",  # veya "isolated"
            "posSide": side.lower()
        }

        body = json.dumps(params)
        signature = calculate_okx_signature(
            secret_key,
            timestamp,
            "POST",
            endpoint,
            body
        )

        headers = {
            "OK-ACCESS-KEY": api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": passphrase,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(f"{base_url}{endpoint}", headers=headers, data=body) as response:
                result = await response.json()

                # Detaylı hata bilgisi
                logger.info(f"Kaldıraç Ayarlama Yanıtı: {result}")

                if result.get('code') == '0':
                    logger.info(f"✅ Kaldıraç başarıyla ayarlandı: {leverage}x")
                    return {"success": True, "data": result.get('data', {})}
                else:
                    logger.error(f"❌ Kaldıraç ayarlama hatası: {result}")
                    return {"success": False, "error": result.get('msg', 'Bilinmeyen Hata')}

    except Exception as e:
        logger.error(f"Kaldıraç ayarlama hatası: {e}")
        return {"success": False, "error": str(e)}


async def create_okx_stop_loss_order(
    symbol: str,
    side: str,                 # "long"/"short" veya "LONG"/"SHORT"
    amount: float,
    current_price: float,      # sadece log amaçlı (plan’da entry_price)
    api_key: str,
    secret_key: str,
    passphrase: str,
    stop_price: Optional[float] = None
) -> Dict[str, Any]:
    """
    OKX SL emri (trigger):
    - stop_price plan'dan geldiyse ASLA değiştirilmez.
    - stop_price yoksa hesaplar (fallback) ama plan akışında normalde dolu olmalı.
    """
    try:
        normalized_symbol = validate_okx_symbol(symbol)

        if stop_price is None:
            stop_price = calculate_stop_loss_price(
                entry_price=float(current_price),
                side=str(side),
                symbol=symbol,
                stop_loss=None
            )

        try:
            stop_price_f = float(stop_price)
        except Exception:
            return {"success": False, "error": "stop_price invalid"}

        if stop_price_f <= 0:
            return {"success": False, "error": "stop_price<=0"}

        side_l = str(side).lower().strip()
        if side_l in ("long", "buy"):
            close_side = "sell"
            pos_side = "long"
        elif side_l in ("short", "sell"):
            close_side = "buy"
            pos_side = "short"
        else:
            return {"success":False, "error":f"invalid side: {side}"}

        # amount büyütme yok
        try:
            sz = int(float(amount))
        except Exception:
            sz = 0
        if sz <= 0:
            return {"success": False, "error": "amount<=0"}

        order_params = {
            "instId": f"{normalized_symbol}-SWAP",
            "tdMode": "cross",
            "side": close_side,
            "posSide": pos_side,
            "ordType": "trigger",
            "sz": str(sz),
            "triggerPx": str(round(stop_price_f, 2)),
            "tpTriggerPxType": "last",
            "tpOrdPxType": "last"
        }

        base_url = "https://www.okx.com"
        endpoint = "/api/v5/trade/order"
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        body = json.dumps(order_params)

        signature = calculate_okx_signature(secret_key, timestamp, "POST", endpoint, body)
        headers = {
            "OK-ACCESS-KEY": api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": passphrase,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(f"{base_url}{endpoint}", headers=headers, data=body) as response:
                result = await response.json()

        if result.get("code") == "0":
            return {"success": True, "data": result.get("data", {}), "stop_price": stop_price_f}

        logger.error(f"Stop Loss emri hatası: {result}")
        return {"success": False, "error": result.get("msg", "unknown_error"), "raw": result}

    except Exception as e:
        logger.error(f"Stop Loss emri hatası: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


async def create_okx_take_profit_orders(
        symbol: str,
        side: str,
        current_price: float,
        order_amount: float,
        user_id: str,
        api_key: str,
        secret_key: str,
        passphrase: str,
        signal_tp_prices: Optional[List[float]] = None
) -> Dict[str, Any]:
    try:
        normalized_symbol = validate_okx_symbol(symbol)

        # Sinyalden gelen TP fiyatları varsa onları kullan
        if signal_tp_prices:
            logger.info(f"Sinyalden gelen TP fiyatları: {signal_tp_prices}")

            # TP yüzdelerini sinyalden gelen fiyatlardan hesapla
            tp_percentages = [
                round(abs((tp - current_price) / current_price * 100), 2)
                for tp in signal_tp_prices
            ]
            logger.info(f"Hesaplanan TP yüzdeleri: {tp_percentages}")
        else:
            # Veritabanından TP yüzdelerini al
            tp_percentages = get_tp_percentages(user_id) or [20.0, 20.0, 20.0, 20.0, 20.0]

        result = {
            'success': False,
            'orders': [],
            'errors': []
        }

        base_url = "https://www.okx.com"
        endpoint = "/api/v5/trade/order"

        for i, (tp_price, tp_percentage) in enumerate(zip(
                signal_tp_prices or calculate_take_profit_prices(
                    entry_price=current_price,
                    side=PositionSide(side.upper()),
                    tp_percentages=tp_percentages,
                    position_amount=order_amount
                ),
                tp_percentages
        ), 1):
            try:
                # Fiyatı güvenli ayrıştır
                if signal_tp_prices is not None:
                    # Sinyal varsa tp_price float bekler
                    tp_price_value = float(tp_price)
                else:
                    # Hesaplanan yapı dict: {"price": x, "amount": y}
                    if isinstance(tp_price, dict):
                        tp_price_value = float(tp_price.get('price'))
                    else:
                        tp_price_value = float(tp_price)

                # Miktarı tamsayı kontrata çevir
                raw_amount = float(order_amount) * (float(tp_percentage) / 100.0)
                tp_amount_int = max(1, int(math.floor(raw_amount)))

                order_params = {
                    "instId":normalized_symbol + "-SWAP",
                    "tdMode":"cross",
                    "side":"sell" if side.lower() == "long" else "buy",
                    "posSide":side.lower(),
                    "ordType":"trigger",
                    "sz":str(tp_amount_int),
                    "triggerPx":str(round(tp_price_value, 2)),
                    "tpOrdPxType":"last",
                    "tpPx":"-1"
                }

                timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                body = json.dumps(order_params)
                signature = calculate_okx_signature(secret_key, timestamp, "POST", endpoint, body)

                headers = {
                    "OK-ACCESS-KEY":api_key,
                    "OK-ACCESS-SIGN":signature,
                    "OK-ACCESS-TIMESTAMP":timestamp,
                    "OK-ACCESS-PASSPHRASE":passphrase,
                    "Content-Type":"application/json"
                }

                async with aiohttp.ClientSession() as session:
                    async with session.post(f"{base_url}{endpoint}", headers=headers, data=body) as response:
                        tp_order = await response.json()

                        if tp_order.get('code') == '0':
                            result['orders'].append({
                                'order_number':i,
                                'percentage':tp_percentage,
                                'price':tp_price_value,
                                'amount':tp_amount_int,  # DÜZELTİLDİ
                                'order_id':(tp_order.get('data') or [{}])[0].get('ordId')
                            })
                        else:
                            raise Exception(f"TP limit emri oluşturma hatası: {tp_order}")

            except Exception as e:
                logger.error(f"TP{i} limit emri oluşturma hatası: {str(e)}")
                result['errors'].append({
                    'order_number': i,
                    'percentage': tp_percentage,
                    'error': str(e)
                })

        result['success'] = len(result['orders']) > 0
        return result

    except Exception as e:
        logger.error(f"Take Profit limit emirleri oluşturma hatası: {str(e)}")
        return {
            'success': False,
            'error': str(e)
        }


async def _okx_place_plan_tp_orders(
    *,
    inst_id: str,
    side: str,
    tp_structs: list,
    opened_amount: float,
    api_key: str,
    secret_key: str,
    passphrase: str,
    td_mode: str = "cross"
) -> Dict[str, Any]:
    """
    plan.tp_structs -> OKX trigger emirleri.
    volume None => opened_amount (%100)
    volume varsa => floor/int ile küçült (büyütme yok)
    """
    result: Dict[str, Any] = {"success": False, "orders": [], "errors": []}

    if not tp_structs:
        return {"success": True, "orders": []}

    side_l = str(side).lower().strip()
    if side_l in ("long", "buy"):
        close_order_side = "sell"
        pos_side = "long"
    elif side_l in ("short", "sell"):
        close_order_side = "buy"
        pos_side = "short"
    else:
        return {"success": False, "error": f"invalid side: {side}"}

    base_url = "https://www.okx.com"
    endpoint = "/api/v5/trade/order"

    async with aiohttp.ClientSession() as session:
        for idx, t in enumerate(tp_structs, start=1):
            try:
                if not isinstance(t, dict):
                    continue
                if t.get("_mode") == "SINYAL_TP" and t.get("volume") is None:
                    # plan volume üretmediyse executor TP koymasın
                    continue

                price = t.get("price")
                if price is None:
                    continue
                tp_price = float(price)

                vol = t.get("volume")
                vol_f = float(opened_amount if vol is None else vol)

                tp_amount = int(vol_f)  # büyütme yok
                if tp_amount <= 0:
                    continue

                order_params = {
                    "instId": inst_id,
                    "tdMode": td_mode,
                    "side": close_order_side,
                    "posSide": pos_side,
                    "ordType": "trigger",
                    "sz": str(tp_amount),
                    "triggerPx": str(round(tp_price, 2)),
                    "tpOrdPxType": "last",
                    "tpPx": "-1",
                    "tpTriggerPxType": "last"
                }

                timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                body = json.dumps(order_params)
                sign = calculate_okx_signature(secret_key, timestamp, "POST", endpoint, body)
                headers = {
                    "OK-ACCESS-KEY": api_key,
                    "OK-ACCESS-SIGN": sign,
                    "OK-ACCESS-TIMESTAMP": timestamp,
                    "OK-ACCESS-PASSPHRASE": passphrase,
                    "Content-Type": "application/json"
                }

                async with session.post(f"{base_url}{endpoint}", headers=headers, data=body) as resp:
                    rj = await resp.json()

                if rj.get("code") == "0":
                    data0 = (rj.get("data") or [{}])[0]
                    result["orders"].append({
                        "order_number": idx,
                        "price": tp_price,
                        "amount": tp_amount,
                        "order_id": data0.get("ordId"),
                        "raw": rj
                    })
                else:
                    raise Exception(f"TP create failed: {rj}")

            except Exception as e:
                result["errors"].append({"order_number": idx, "error": str(e)})

    result["success"] = len(result["orders"]) > 0
    return result


async def okx_execute_plan(plan: ExecutionPlan, user: dict) -> Dict[str, Any]:
    """
    ExecutionPlan'ı OKX üzerinde uygular.
    Kuralları burada üretmez; sadece planı uygular.
    """
    try:
        api_key = user.get("api_key")
        secret_key = user.get("secret_key")
        passphrase = user.get("passphrase")

        if not api_key or not secret_key or not passphrase:
            return {"success": False, "error": "okx api_key/secret_key/passphrase yok"}

        symbol_in = getattr(plan, "symbol_exchange", None) or getattr(plan, "symbol_core", "") or ""
        okx_sym = validate_okx_symbol(symbol_in)          # "BTC-USDT"
        inst_id = f"{okx_sym}-SWAP"                       # "BTC-USDT-SWAP"

        side_u = str(plan.side).upper().strip()
        if side_u == "LONG":
            pos_side = "long"
            open_side = "buy"
        elif side_u == "SHORT":
            pos_side = "short"
            open_side = "sell"
        else:
            s2 = str(plan.side).lower().strip()
            if s2 in ("long", "buy"):
                pos_side = "long"
                open_side = "buy"
            elif s2 in ("short", "sell"):
                pos_side = "short"
                open_side = "sell"
            else:
                return {"success": False, "error": f"invalid side: {plan.side}"}

        td_mode = "cross" if str(plan.margin).upper() == "CROSS" else "isolated"

        # leverage
        try:
            lev_res = await set_okx_leverage(okx_sym, int(plan.leverage), pos_side, api_key, secret_key, passphrase)
            if not lev_res.get("success"):
                return {"success": False, "error": lev_res.get("error", lev_res)}
        except Exception as e:
            logger.warning(f"[OKX_SET_LEV_WARN] {e}")

        # OPEN market (contracts büyütme yok)
        try:
            sz = int(float(getattr(plan, "contracts", 0) or 0))
        except Exception:
            sz = 0
        if sz <= 0:
            return {"success": False, "error": f"plan.contracts<=0 (contracts={getattr(plan,'contracts',None)})"}

        order_params = {
            "instId": inst_id,
            "tdMode": td_mode,
            "side": open_side,
            "posSide": pos_side,
            "ordType": "market",
            "sz": str(sz)
        }

        base_url = "https://www.okx.com"
        endpoint = "/api/v5/trade/order"
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        body = json.dumps(order_params)

        signature = calculate_okx_signature(secret_key, timestamp, "POST", endpoint, body)
        headers = {
            "OK-ACCESS-KEY": api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": passphrase,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(f"{base_url}{endpoint}", headers=headers, data=body) as resp:
                open_resp = await resp.json()

        if open_resp.get("code") != "0":
            return {"success": False, "error": open_resp, "open_order": open_resp}

        # position fetch -> opened amount
        opened_amount = 0.0
        try:
            pos = await get_okx_position(okx_sym, api_key, secret_key, passphrase)
            opened_amount = abs(float(pos.get("pos", 0) or 0))
        except Exception as e:
            logger.warning(f"[OKX_POS_FETCH_WARN] {e}")

        if opened_amount <= 0:
            opened_amount = float(sz)

        # SL/TP kapalıysa bitir
        if not bool(plan.sl_tp_emir):
            data0 = (open_resp.get("data") or [{}])[0]
            return {
                "success": True,
                "data": open_resp,
                "position_id": data0.get("ordId"),
                "opened_amount": opened_amount,
                "tp_orders": [],
                "sl_order": None
            }

        # TP
        tp_result = {"success": True, "orders": []}
        try:
            tp_result = await _okx_place_plan_tp_orders(
                inst_id=inst_id,
                side=pos_side,
                tp_structs=list(plan.tp_structs or []),
                opened_amount=float(opened_amount),
                api_key=api_key,
                secret_key=secret_key,
                passphrase=passphrase,
                td_mode=td_mode
            )
        except Exception as e:
            logger.error(f"[OKX_EXEC_TP_ERR] {e}", exc_info=True)

        # SL
        sl_result = None
        try:
            if getattr(plan, "sl_price", None):
                sl_result = await create_okx_stop_loss_order(
                    symbol=okx_sym,                 # "BTC-USDT"
                    side=pos_side,                  # "long"/"short"
                    amount=float(opened_amount),
                    current_price=float(getattr(plan, "entry_price", 0) or 0),
                    api_key=api_key,
                    secret_key=secret_key,
                    passphrase=passphrase,
                    stop_price=float(plan.sl_price)
                )
                # INITIAL hook
                try:
                    if sl_result and isinstance(sl_result, dict) and sl_result.get("success"):
                        core_symbol = okx_sym.replace("-USDT", "USDT")
                        norm_sym_key = normalize_symbol_for_key(core_symbol)
                        state = ACTIVE_OPEN_POSITIONS.get((int(plan.user_id), "okx", norm_sym_key))
                        on_sl_move(int(plan.user_id), "okx", norm_sym_key, float(plan.sl_price), "INITIAL", state)
                except Exception as hook_err:
                    logger.warning(f"[OKX_SL_MOVE_INITIAL_HOOK_WARN] user={plan.user_id} sym={okx_sym} err={hook_err}")
        except Exception as e:
            logger.error(f"[OKX_EXEC_SL_ERR] {e}", exc_info=True)

        data0 = (open_resp.get("data") or [{}])[0]
        return {
            "success": True,
            "data": open_resp,
            "position_id": data0.get("ordId"),
            "opened_amount": opened_amount,
            "tp_orders": tp_result.get("orders", []),
            "sl_order": sl_result,
            "tp_success": bool(tp_result.get("success", False))
        }

    except Exception as e:
        logger.error(f"[OKX_EXEC_PLAN_ERR] {e}", exc_info=True)
        return {"success": False, "error": str(e)}


async def okx_place_sl_algo(
    *,
    inst_id: str,         # "BTC-USDT-SWAP"
    td_mode: str,         # "cross" / "isolated"
    pos_side: str,        # "long" / "short"
    sl_trigger_px: float,
    api_key: str,
    secret_key: str,
    passphrase: str,
) -> Dict[str, Any]:
    try:
        base_url = "https://www.okx.com"
        endpoint = "/api/v5/trade/order-algo"
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

        body_obj = {
            "instId": inst_id,
            "tdMode": td_mode,
            "side": "sell" if pos_side == "long" else "buy",
            "posSide": pos_side,
            "ordType": "conditional",
            "slTriggerPx": str(round(float(sl_trigger_px), 2)),
            "slTriggerPxType": "last",
            "slOrdPx": "-1"  # market
        }
        body = json.dumps(body_obj, separators=(",", ":"))

        sign = calculate_okx_signature(secret_key, timestamp, "POST", endpoint, body)
        headers = {
            "OK-ACCESS-KEY": api_key,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": passphrase,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(f"{base_url}{endpoint}", headers=headers, data=body) as resp:
                rj = await resp.json()

        if rj.get("code") == "0":
            return {"success": True, "data": rj.get("data", []), "sl_price": float(sl_trigger_px)}

        return {"success": False, "error": rj.get("msg", "algo_failed"), "raw": rj}
    except Exception as e:
        logger.error(f"[OKX_SL_ALGO_ERR] {e}", exc_info=True)
        return {"success": False, "error": str(e)}


async def okx_update_stop_loss(
    user_id: int,
    symbol_core: str,           # "BTCUSDT"
    new_sl_price: float,
    reason: str = "TRAIL",      # "TRAIL" | "BE" | "MANUAL" | "RULE"
    position_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    OKX SL güncellemesi:
      - (Basit) mevcut trigger'ları iptal et (best-effort)
      - Açık pozisyon miktarını API ile bul
      - Yeni trigger SL emrini oluştur
      - Başarıda on_sl_move(reason) tetikler
    """
    try:
        # API anahtarlarını al
        row = db_operation("SELECT api_key, secret_key, passphrase FROM api_key WHERE user_id=? AND LOWER(exchange)='okx'",
                           (int(user_id),), operation='select', fetch=True)
        if not row or not row[0]:
            return {"success": False, "error": "api_keys_not_found"}
        api_key, secret_key, passphrase = row[0][0], row[0][1], row[0][2]

        # OKX sembol formatları
        core_symbol = symbol_core.upper()
        okx_symbol = validate_okx_symbol(core_symbol)   # "BTC-USDT"
        inst_id = f"{okx_symbol}-SWAP"

        # Açık pozisyon miktarı
        amt = await _okx_fetch_position_amount(api_key, secret_key, passphrase, okx_symbol)
        if amt is None or amt <= 0:
            return {"success": False, "error": "no_open_position"}

        # Mevcut trigger/stop iptal (best-effort)
        try:
            await _okx_cancel_triggers(api_key, secret_key, passphrase, okx_symbol)
        except Exception as e:
            logger.warning(f"[OKX_UPD_SL_CANCEL_WARN] uid={user_id} sym={okx_symbol} err={e}")

        # Yön tahmini: owner varsa oradan, yoksa long varsayımı
        side_guess = "long"
        if position_id is not None:
            owner = TRAILING_POSITION_OWNERS_OKX.get(int(position_id)) or {}
            if owner and owner.get("user_id") == int(user_id):
                side_guess = str(owner.get("side", "long")).lower()

        # Yeni SL yerleştir
        placed = await _okx_place_new_stop(api_key, secret_key, passphrase, okx_symbol, side_guess, float(amt), float(new_sl_price))
        if not placed:
            return {"success": False, "error": "place_failed"}

        # Hook + state
        try:
            norm_sym_key = normalize_symbol_for_key(core_symbol)
            state = ACTIVE_OPEN_POSITIONS.get((int(user_id), "okx", norm_sym_key))
            on_sl_move(int(user_id), "okx", norm_sym_key, float(new_sl_price), reason or "TRAIL", state)
        except Exception as hook_err:
            logger.warning(f"[OKX_UPD_SL_HOOK_WARN] uid={user_id} sym={core_symbol} err={hook_err}")

        logger.info(f"[OKX_UPD_SL_OK] uid={user_id} sym={core_symbol} amt={amt} stop={new_sl_price}")
        return {"success": True, "data": {"symbol": core_symbol, "amount": float(amt), "new_sl_price": float(new_sl_price)}}
    except Exception as e:
        logger.error(f"[OKX_UPD_SL_ERR] uid={user_id} sym={symbol_core} err={e}", exc_info=True)
        return {"success": False, "error": str(e)}


async def okx_close_position(
        symbol: str,
        side: str,
        api_key: str,
        secret_key: str,
        passphrase: str,
        close_price: Optional[float] = None,  # Varsayılan parametre sona
        user_id: Optional[str] = None,  # Varsayılan parametre sona
        **kwargs  # Ek parametreleri yakalama
) -> Dict[str, Any]:
    try:
        # Kullanılmayan parametreleri logla
        if close_price is not None:
            logger.info(f"Kapatma fiyatı belirtildi: {close_price}")

        if user_id is not None:
            logger.info(f"Kullanıcı ID'si belirtildi: {user_id}")

        if kwargs:
            logger.info(f"Ek parametreler: {kwargs}")

        normalized_symbol = validate_okx_symbol(symbol)

        # Pozisyon miktarını al
        position = await get_okx_position(
            symbol=normalized_symbol,
            api_key=api_key,
            secret_key=secret_key,
            passphrase=passphrase
        )

        if not position or float(position.get('pos', 0)) == 0:
            return {"success": False, "error": "Açık pozisyon bulunamadı"}

        # Kapatma parametreleri
        close_params = {
            "instId":normalized_symbol + "-SWAP",
            "tdMode":"cross",
            "side":"sell" if side.lower() == "long" else "buy",
            "posSide":side.lower(),
            "ordType":"market",
            "sz":str(int(math.ceil(abs(float(position.get('pos', 0) or 0)))))
        }

        # API isteği gönderme
        base_url = "https://www.okx.com"
        endpoint = "/api/v5/trade/order"
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

        body = json.dumps(close_params)
        signature = calculate_okx_signature(
            secret_key,
            timestamp,
            "POST",
            endpoint,
            body
        )

        headers = {
            "OK-ACCESS-KEY": api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": passphrase,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(f"{base_url}{endpoint}", headers=headers, data=body) as response:
                result = await response.json()

                # Detaylı log ve bilgilendirme
                log_data = {
                    "symbol": normalized_symbol,
                    "side": side,
                    "position_size": close_params["sz"],
                    "order_type": close_params["ordType"]
                }

                if result.get('code') == '0':
                    logger.info(f"✅ Pozisyon başarıyla kapatıldı: {log_data}")

                    # Kullanıcı bildirimi
                    if user_id:
                        try:
                            # Doğrudan bildirim fonksiyonunu çağırmak yerine
                            # Mevcut bir bildirim mekanizması kullanılabilir
                            notification_data = {
                                "user_id": user_id,
                                "symbol": symbol,
                                "side": side,
                                "result": result,
                                **log_data
                            }
                            logger.info(f"Bildirim verisi: {notification_data}")
                            # Örnek: await send_notification(notification_data)
                        except Exception as notify_error:
                            logger.error(f"Bildirim gönderme hatası: {notify_error}")

                    return {
                        "success": True,
                        "data": result.get('data', {}),
                        "message": "Pozisyon başarıyla kapatıldı",
                        **log_data  # Ek bilgileri sonuç içine ekle
                    }
                else:
                    logger.error(f"❌ Pozisyon kapatma hatası: {result}")
                    return {
                        "success": False,
                        "error": result.get('msg', 'Bilinmeyen Hata'),
                        "details": log_data
                    }

    except Exception as e:
        logger.error(f"OKX Pozisyon Kapatma Hatası: {e}")
        return {"success": False, "error": str(e)}


async def get_okx_position(
    symbol: str,
    api_key: str,
    secret_key: str,
    passphrase: str
) -> Dict[str, Any]:
    """
    OKX GET /api/v5/account/positions?instId=XXX
    Signature request_path içinde querystring ile üretilmelidir.
    """
    try:
        base_url = "https://www.okx.com"
        endpoint = "/api/v5/account/positions"

        inst_id = f"{validate_okx_symbol(symbol)}-SWAP"
        query = f"?instId={inst_id}"
        request_path = f"{endpoint}{query}"

        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        signature = calculate_okx_signature(secret_key, timestamp, "GET", request_path, "")

        headers = {
            "OK-ACCESS-KEY": api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": passphrase,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}{request_path}", headers=headers) as response:
                result = await response.json()

        if result.get("code") != "0":
            logger.error(f"❌ Pozisyon sorgulama hatası: {result}")
            return {}

        positions = result.get("data", []) or []
        if not positions:
            logger.info("Açık pozisyon bulunamadı")
            return {}

        position = positions[0]
        logger.info(
            f"[OKX_POS] instId={position.get('instId')} pos={position.get('pos')} avgPx={position.get('avgPx')}"
        )
        return position

    except Exception as e:
        logger.error(f"Pozisyon sorgulama hatası: {e}", exc_info=True)
        return {}



class PositionSide(Enum):
    LONG = "long"
    SHORT = "short"


def get_tp_percentages(user_id: str) -> Optional[List[float]]:
    """
    Kullanıcının take profit yüzdelerini veritabanından alma
    """
    # Varsayılan TP yüzdeleri
    default_percentages = [20.0, 20.0, 20.0, 20.0, 20.0]

    try:
        # Veritabanı sorgusu
        query = """
        SELECT tp1, tp2, tp3, tp4, tp5, tp6, tp7, tp8, tp9, tp10
        FROM ayarlar 
        WHERE user_id = ? AND LOWER(exchange) = 'okx'
        """

        # Veritabanı işlemi
        result = db_operation(query, (user_id,), fetch=True)

        # Sonucu kontrol et
        if not result or not result[0]:
            return default_percentages

        # Sıfır olmayan TP yüzdelerini al
        tp_percentages = []
        for percentage in result[0]:
            try:
                # Yüzde işaretini kaldır ve float'a çevir
                cleaned_percentage = str(percentage).replace('%', '').strip()
                float_percentage = float(cleaned_percentage)

                if float_percentage > 0:
                    tp_percentages.append(float_percentage)
            except (ValueError, TypeError):
                continue

        # Eğer hiç TP yüzdesi bulunamazsa varsayılan yüzdeleri kullan
        if not tp_percentages:
            return default_percentages

        # Maksimum 5 TP yüzdesi al
        return tp_percentages[:5]

    except Exception as e:
        logger.error(f"TP yüzdeleri alma hatası: {e}")
        return default_percentages


def calculate_stop_loss_price(
        entry_price: float,
        side: str,
        symbol: str,
        stop_loss: Optional[float] = None
) -> float:
    """
    OKX için stop loss fiyatı hesaplama

    Args:
        entry_price: Giriş fiyatı
        side: Pozisyon yönü (long/short)
        symbol: İşlem sembolü (gelecekteki genişlemeler için)
        stop_loss: Manuel olarak belirlenmiş stop loss fiyatı
    """
    try:
        # Sembole özgü ayarlamalar (opsiyonel)
        normalized_symbol = validate_okx_symbol(symbol) if symbol else None

        # Varsayılan stop loss yüzdesi
        default_stop_loss_percentage = 0.02  # %2

        # Manuel stop loss varsa onu kullan
        if stop_loss:
            return stop_loss

        # Pozisyon yönüne göre stop loss hesaplama
        if side.lower() == "long":
            calculated_stop_loss = entry_price * (1 - default_stop_loss_percentage)
        else:  # short
            calculated_stop_loss = entry_price * (1 + default_stop_loss_percentage)

        # Sembole özel hassasiyet ayarı (örnek)
        if normalized_symbol:
            # Örnek: Bazı semboller için farklı stop loss hesaplaması
            symbol_specific_adjustments = {
                "BTC": 0.015,  # BTC için farklı stop loss yüzdesi
                "ETH": 0.025  # ETH için farklı stop loss yüzdesi
            }

            # Sembole özel ayarlama
            adjusted_percentage = symbol_specific_adjustments.get(
                normalized_symbol,
                default_stop_loss_percentage
            )

            if side.lower() == "long":
                calculated_stop_loss = entry_price * (1 - adjusted_percentage)
            else:
                calculated_stop_loss = entry_price * (1 + adjusted_percentage)

        # Fiyatı yuvarla
        return round(calculated_stop_loss, 2)

    except Exception as e:
        logger.error(f"Stop loss hesaplama hatası: {e}")
        return entry_price * 0.98  # Varsayılan olarak %2 aşağı


def calculate_take_profit_prices(
        entry_price: float,
        side: PositionSide,
        tp_percentages: Optional[List[float]] = None,
        position_amount: float = 1
) -> List[Dict[str, float]]:
    """
    OKX için take profit fiyatları ve miktarları hesaplama

    Args:
        entry_price: Giriş fiyatı
        side: Pozisyon yönü
        tp_percentages: Take profit yüzdeleri
        position_amount: Toplam pozisyon miktarı

    Returns:
        Take profit fiyatları ve miktarları listesi
    """
    try:
        # Varsayılan TP yüzdeleri
        default_tp_percentages = [20.0, 20.0, 20.0, 20.0, 20.0]

        # Kullanılacak yüzdeleri belirle
        percentages = tp_percentages or default_tp_percentages

        # Her TP seviyesi için miktar hesaplama
        tp_amounts = calculate_tp_amounts(position_amount, percentages)

        # Take profit fiyatlarını hesapla
        if side == PositionSide.LONG:
            take_profit_prices = [
                {
                    "price": round(entry_price * (1 + (p / 100)), 2),
                    "amount": tp_amounts[i]
                }
                for i, p in enumerate(percentages)
            ]
        else:  # SHORT
            take_profit_prices = [
                {
                    "price": round(entry_price * (1 - (p / 100)), 2),
                    "amount": tp_amounts[i]
                }
                for i, p in enumerate(percentages)
            ]

        return take_profit_prices

    except Exception as e:
        logger.error(f"Take profit hesaplama hatası: {e}")
        return []


def calculate_tp_amounts(
        total_amount: float,
        percentages: List[float]
) -> List[float]:
    """
    Toplam pozisyon miktarını TP seviyelerine böl

    Args:
        total_amount: Toplam pozisyon miktarı
        percentages: Take profit yüzdeleri

    Returns:
        Her TP seviyesi için miktar listesi
    """
    try:
        # Toplam yüzdeyi hesapla
        total_percentage = sum(percentages)

        # Her seviye için miktar hesapla
        tp_amounts = [
            round(total_amount * (p / total_percentage), 4)
            for p in percentages
        ]

        # Yuvarlama hatalarını düzelt
        adjustment = total_amount - sum(tp_amounts)
        tp_amounts[-1] += adjustment

        return tp_amounts

    except Exception as e:
        logger.error(f"TP miktarı hesaplama hatası: {e}")
        # Eşit dağılım
        return [total_amount / len(percentages)] * len(percentages)


async def okx_send_trade_notification(
        user_id: str,
        signal_dict: Dict[str, Any],
        position_result: Dict[str, Any],
        bot: Optional[Bot] = None
) -> None:
    """
    OKX için Telegram bildirim fonksiyonu
    """
    try:
        if not bot:
            bot = Bot(token=BOT_TOKEN)

        message = f"""
    🔔 OKX İşlem Bildirimi 🔔

    📊 Sembol: {signal_dict.get('symbol', 'Bilinmeyen')}
    🏦 İşlem Türü: {signal_dict.get('position_type', 'Bilinmeyen')}

    {"✅ İşlem Başarılı" if position_result.get('success') else "❌ İşlem Başarısız"}

    {"Detaylar:" if position_result.get('success') else "Hata:"}
    {json.dumps(position_result.get('data', position_result.get('error', {})), indent=2)}

    📈 Giriş Noktaları: {signal_dict.get('entry_points', [])}
    🛑 Stop Loss: {signal_dict.get('stop_loss', 'Tanımsız')}
    🎯 Take Profit: {signal_dict.get('take_profits', [])}
    """

        # Mesajı gönder
        try:
            await bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode='HTML'
            )
        except Exception as telegram_error:
            logger.error(f"Telegram bildirim gönderme hatası: {telegram_error}")

    except Exception as e:
        logger.error(f"Bildirim oluşturma hatası: {e}")
