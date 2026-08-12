# bitmart_al_sat.py dosyamız buradan başlamaktadır
import time
import json
import hmac
import math
from typing import List, Dict, Any, Optional
import aiohttp
from data.olimpos_data import *
from config.constants import *
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackContext
import hashlib
from settings.execution_plan_types import ExecutionPlan

from datetime import datetime
import base64
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
    return provider_get_symbol_meta(exchange="bitmart", symbol=core_sym)


def validate_bitmart_symbol(symbol: str) -> str:
    try:
        # Semboldeki 1000 ön ekini kaldır
        if symbol.startswith('1000'):
            symbol = symbol[4:]

        # Boşlukları kaldır, büyük harfe çevir
        normalized_symbol = (
            symbol
            .replace(' ', '')  # Boşlukları temizle
        )

        # Sadece sondaki P'yi kaldır
        if normalized_symbol.endswith('P'):
            normalized_symbol = normalized_symbol[:-1]

        # Büyük harfe çevir
        normalized_symbol = normalized_symbol.upper()

        # USDT çiftini kontrol et
        if not normalized_symbol.endswith('USDT'):
            normalized_symbol += 'USDT'

        # BitMart formatına çevir
        bitmart_symbol = f"{normalized_symbol}"

        # Detaylı log
        logger.info(f"""
        🔄 Sembol Normalizasyonu:
           - Gelen Sembol: {symbol}
           - Normalize Sembol: {normalized_symbol}
           - BitMart Sembol Formatı: {bitmart_symbol}
        """)

        return bitmart_symbol

    except Exception as e:
        logger.error(f"Sembol normalizasyon hatası: {e}")
        return symbol


def get_bitmart_signature(secret_key: str, timestamp: str, method: str, request_path: str, body: str = '') -> str:
    message = timestamp + method.upper() + request_path + body
    mac = hmac.new(
        secret_key.encode('utf-8'),
        message.encode('utf-8'),
        digestmod=hashlib.sha256
    )
    return base64.b64encode(mac.digest()).decode('utf-8')


async def get_bitmart_account_balance(api_key: str, secret_key: str) -> float:
    base_url = "https://api-cloud.bitmart.com"
    endpoints_try = [
        "/spot/v1/wallet",
        "/spot/v1/account",
    ]

    def _is_ok_code(v) -> bool:
        try:
            return str(v) == "1000" or int(v) == 1000
        except Exception:
            return False

    for endpoint in endpoints_try:
        try:
            timestamp = str(int(time.time() * 1000))
            signature = get_bitmart_signature(secret_key, timestamp, "GET", endpoint)

            headers = {
                "X-BM-KEY": api_key,
                "X-BM-SIGN": signature,
                "X-BM-TIMESTAMP": timestamp,
                "Content-Type": "application/json",
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(f"{base_url}{endpoint}", headers=headers) as response:
                    ctype = (response.headers.get("Content-Type") or "").lower()
                    if "application/json" not in ctype:
                        body_txt = await response.text()
                        logger.error(
                            f"[BITMART_ACC_HTTP_NONJSON] endpoint={endpoint} status={response.status} "
                            f"ctype={ctype} body={body_txt[:200]}"
                        )
                        continue
                    result = await response.json()

            if not _is_ok_code(result.get("code")):
                logger.error(f"[BITMART_ACC_FAIL] endpoint={endpoint} resp={str(result)[:400]}")
                continue

            data = result.get("data") or {}
            wallet = data.get("wallet", []) or []
            if isinstance(wallet, dict):
                wallet = [wallet]
            if not isinstance(wallet, list):
                continue

            for item in wallet:
                item_coin = item.get("coin")
                if item_coin is None:
                    item_coin = item.get("id")

                if str(item_coin or "").upper() == "USDT":
                    try:
                        return float(item.get("available", 0) or 0)
                    except Exception:
                        return 0.0

            return 0.0

        except Exception as e:
            logger.error(f"[BITMART_ACC_BAL_ERR] endpoint={endpoint} err={e}", exc_info=True)
            continue

    return 0.0


async def bitmart_execute_plan(plan: ExecutionPlan, user: dict) -> Dict[str, Any]:
    """
    ExecutionPlan'ı BitMart spot üzerinde uygular.
    - Spot: gerçek SHORT yok (margin/borrow ayrı ürün). Bu yüzden SHORT plan reddedilir.
    - plan.contracts büyütülmez.
    """
    try:
        api_key = user.get("api_key")
        secret_key = user.get("secret_key")
        if not api_key or not secret_key:
            return {"success": False, "error": "bitmart api_key/secret_key yok"}

        symbol_in = getattr(plan, "symbol_exchange", None) or getattr(plan, "symbol_core", "") or ""
        symbol = validate_bitmart_symbol(symbol_in)

        side_u = str(plan.side).upper().strip()
        if side_u not in ("LONG", "SHORT"):
            return {"success": False, "error": f"invalid side: {plan.side}"}

        # ✅ Spotta SHORT yok: güvenli davran
        if side_u == "SHORT":
            return {"success": False, "error": "bitmart_spot_short_not_supported"}

        open_side = "buy"  # LONG

        try:
            amount = float(plan.contracts)
        except Exception:
            amount = 0.0
        if amount <= 0:
            return {"success": False, "error": "plan.contracts<=0"}

        # OPEN market
        base_url = "https://api-cloud.bitmart.com"
        endpoint = "/spot/v1/submit_order"
        timestamp = str(int(time.time() * 1000))

        open_params = {
            "symbol": symbol,
            "side": open_side,
            "type": "market",
            "size": str(amount)
        }

        body = json.dumps(open_params, separators=(",", ":"))
        sig = get_bitmart_signature(secret_key, timestamp, "POST", endpoint, body)

        headers = {
            "X-BM-KEY": api_key,
            "X-BM-SIGN": sig,
            "X-BM-TIMESTAMP": timestamp,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(base_url + endpoint, headers=headers, data=body) as resp:
                open_resp = await resp.json()

        if open_resp.get("code") != "1000":
            return {"success": False, "error": open_resp, "open_order": open_resp}

        # SL/TP kapalıysa bitir
        if not bool(getattr(plan, "sl_tp_emir", False)):
            return {
                "success": True,
                "open_order": open_resp,
                "position_id": (open_resp.get("data") or {}).get("order_id"),
                "tp_orders": [],
                "sl_order": None
            }

        tp_orders: List[Dict[str, Any]] = []
        sl_order: Optional[Dict[str, Any]] = None

        # SL
        if getattr(plan, "sl_price", None):
            sl_order = await create_stop_loss_order(
                symbol=symbol,
                side=open_side,  # open side
                amount=float(amount),
                stop_price=float(plan.sl_price),
                current_price=float(getattr(plan, "entry_price", 0) or 0),
                api_key=api_key,
                secret_key=secret_key
            )

        # TP
        for t in (plan.tp_structs or []):
            if not isinstance(t, dict):
                continue
            if t.get("_mode") == "SINYAL_TP" and t.get("volume") is None:
                continue

            price = t.get("price")
            if price is None:
                continue

            vol = t.get("volume")
            tp_amount = float(amount if vol is None else vol)
            if tp_amount <= 0:
                continue

            tp = await create_take_profit_order(
                symbol=symbol,
                side=open_side,
                amount=tp_amount,
                take_profit_price=float(price),
                api_key=api_key,
                secret_key=secret_key
            )
            tp_orders.append(tp)

        return {
            "success": True,
            "open_order": open_resp,
            "position_id": (open_resp.get("data") or {}).get("order_id"),
            "tp_orders": tp_orders,
            "sl_order": sl_order
        }

    except Exception as e:
        logger.error(f"[BITMART_EXEC_PLAN_ERR] {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def round_price_to_precision(price: float, symbol: str) -> float:
    """
    Fiyatı borsanın kabul ettiği hassasiyete yuvarlar
    """
    try:
        # Önce string'e çevir ve bilimsel gösterimi engelle
        price_str = "{:.8f}".format(price)
        # Noktadan sonraki kısmı al
        decimal_part = price_str.split('.')[1] if '.' in price_str else ''

        # BitMart için standart hassasiyet: 0.001 (3 decimal)
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


async def get_bitmart_min_trade_amount(symbol: str, api_key: str, secret_key: str) -> float:
    try:
        base_url = "https://api-cloud.bitmart.com"
        endpoint = "/spot/v1/symbols"
        timestamp = str(int(time.time() * 1000))

        signature = get_bitmart_signature(
            secret_key,
            timestamp,
            "GET",
            endpoint
        )

        headers = {
            "X-BM-KEY": api_key,
            "X-BM-SIGN": signature,
            "X-BM-TIMESTAMP": timestamp,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(
                    f"{base_url}{endpoint}",
                    headers=headers
            ) as response:
                result = await response.json()

                if result.get('code') == '1000':
                    symbol_info = next((item for item in result.get('data', []) if item['symbol'] == symbol), None)

                    if symbol_info:
                        min_trade_amount = float(symbol_info.get('min_trade_amount', 5.0))
                        return min_trade_amount

                logger.error(f"❌ Minimum işlem tutarı bulunamadı")
                return 5.0

    except Exception as e:
        logger.error(f"❌ Minimum işlem tutarı sorgulama hatası: {str(e)}")
        return 5.0


async def create_stop_loss_order(
    symbol: str,
    side: str,               # open side: LONG/SHORT/buy/sell
    amount: float,
    stop_price: float,
    current_price: float,    # log amaçlı
    api_key: str,
    secret_key: str,
) -> Dict[str, Any]:
    try:
        normalized_symbol = validate_bitmart_symbol(symbol)

        open_side = _bitmart_normalize_side(side)   # buy/sell
        stop_side = "sell" if open_side == "buy" else "buy"

        try:
            amt_f = float(amount)
        except Exception:
            amt_f = 0.0
        if amt_f <= 0:
            return {"success": False, "error": "amount<=0"}

        sp = float(stop_price)
        sp_q = _bitmart_round_price(normalized_symbol, sp)

        stop_loss_params = {
            "symbol": normalized_symbol,
            "side": stop_side,
            "type": "stop_market",
            "stop_price": str(sp_q),
            "size": str(amt_f)
        }

        base_url = "https://api-cloud.bitmart.com"
        timestamp = str(int(time.time() * 1000))
        endpoint = "/spot/v1/submit_order"

        body = json.dumps(stop_loss_params, separators=(",", ":"))
        signature = get_bitmart_signature(secret_key, timestamp, "POST", endpoint, body)

        headers = {
            "X-BM-KEY": api_key,
            "X-BM-SIGN": signature,
            "X-BM-TIMESTAMP": timestamp,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(base_url + endpoint, headers=headers, data=body) as response:
                result = await response.json()

        if result.get("code") == "1000":
            return {"success": True, "data": result, "stop_price": sp_q}

        return {"success": False, "error": result.get("msg", "unknown_error"), "raw": result}

    except Exception as e:
        logger.error(f"[BITMART_SL_ERR] {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def calculate_stop_loss_price(
        entry_price: float,
        side: str,
        symbol: str,
        stop_loss: Optional[float] = None
) -> Optional[float]:
    """
    Stop loss fiyatını hesaplar
    """
    try:
        # Varsayılan risk yüzdesi
        risk_percentage = 0.02  # %2 risk

        if stop_loss:
            return stop_loss

        # Long pozisyon için stop loss hesaplama
        if side.lower() == 'buy':
            calculated_stop_loss = entry_price * (1 - risk_percentage)
        # Short pozisyon için stop loss hesaplama
        else:
            calculated_stop_loss = entry_price * (1 + risk_percentage)

        # Fiyatı yuvarlama
        rounded_stop_loss = round_price_to_precision(calculated_stop_loss, symbol)

        logger.info(f"""
        🎯 Stop Loss Hesaplama:
        - Giriş Fiyatı: {entry_price}
        - Taraf: {side}
        - Risk Yüzdesi: %{risk_percentage * 100}
        - Hesaplanan Stop Loss: {rounded_stop_loss}
        """)

        return rounded_stop_loss

    except Exception as e:
        logger.error(f"Stop Loss Hesaplama Hatası: {e}")
        return None


async def modify_position_orders(
        symbol: str,
        side: str,
        api_key: str,
        secret_key: str,
        passphrase: str,
        user_id: int,
        stop_loss: Optional[float] = None,
        take_profits: Optional[List[float]] = None
) -> Dict[str, Any]:
    try:
        normalized_symbol = validate_bitmart_symbol(symbol)

        # Mevcut pozisyonu kontrol et
        # BitMart'ta doğrudan pozisyon kontrolü farklı olacak
        # Şimdilik boş bırakıyorum, gerekirse API'ye özel kontrol eklenebilir

        # Mevcut emirleri iptal et
        await cancel_all_orders(
            symbol=normalized_symbol,
            api_key=api_key,
            secret_key=secret_key,
        )

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
                amount=1,  # BitMart için miktar gerekli
                stop_price=stop_loss,
                current_price=stop_loss,  # Geçerli fiyat
                api_key=api_key,
                secret_key=secret_key,
            )
            results["stop_loss"] = sl_result

        # Take profit emirleri oluştur
        if take_profits:
            tp_results = []
            for tp_price in take_profits:
                tp_result = await create_take_profit_order(
                    symbol=normalized_symbol,
                    side=side,
                    amount=1,  # BitMart için miktar gerekli
                    take_profit_price=tp_price,
                    api_key=api_key,
                    secret_key=secret_key,
                )
                tp_results.append(tp_result)

            results["take_profits"] = tp_results

        return results

    except Exception as e:
        logger.error(f"Pozisyon emirleri güncelleme hatası: {e}")
        return {"success": False, "error": str(e)}


async def cancel_all_orders(
        symbol: str,
        api_key: str,
        secret_key: str,
) -> Dict[str, Any]:
    try:
        base_url = "https://api-cloud.bitmart.com"
        endpoint = "/spot/v1/cancel_orders"
        timestamp = str(int(time.time() * 1000))

        params = {
            "symbol": symbol
        }

        signature = get_bitmart_signature(
            secret_key,
            timestamp,
            "POST",
            endpoint,
            json.dumps(params)
        )

        headers = {
            "X-BM-KEY": api_key,
            "X-BM-SIGN": signature,
            "X-BM-TIMESTAMP": timestamp,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                    f"{base_url}{endpoint}",
                    headers=headers,
                    data=json.dumps(params)
            ) as response:
                result = await response.json()

                if result.get('code') == '1000':
                    logger.info(f"✅ Tüm emirler başarıyla iptal edildi - {symbol}")
                    return {"success": True, "data": result.get('data')}
                else:
                    logger.error(f"Emir iptal hatası: {result}")
                    return {"success": False, "error": result.get('msg')}

    except Exception as e:
        logger.error(f"Emir iptal işlemi hatası: {e}")
        return {"success": False, "error": str(e)}


async def bitmart_close_position(
    symbol: str,
    side: str,                    # mevcut pozisyon yönü (LONG/SHORT) ya da open side (buy/sell)
    api_key: str,
    secret_key: str,
    user_id: Optional[str] = None,
    lot: Optional[float] = None,
) -> Dict[str, Any]:
    try:
        normalized_symbol = validate_bitmart_symbol(symbol)

        # spot close: genelde SELL (elde coin varsa)
        open_side = _bitmart_normalize_side(side)
        close_side = "sell" if open_side == "buy" else "buy"

        # lot yoksa base coin bakiyesini kullan
        if lot is None or float(lot or 0) <= 0:
            base_coin = _bitmart_base_coin_from_symbol(normalized_symbol)
            avail = await _bitmart_fetch_spot_balance_amount(api_key, secret_key, base_coin)
            lot = avail

        try:
            lot_f = float(lot or 0)
        except Exception:
            lot_f = 0.0

        if lot_f <= 0:
            return {"success": False, "error": "close_amount<=0 (balance/lot yok)"}

        close_params = {
            "symbol": normalized_symbol,
            "side": close_side,
            "type": "market",
            "size": str(lot_f)
        }

        base_url = "https://api-cloud.bitmart.com"
        timestamp = str(int(time.time() * 1000))
        endpoint = "/spot/v1/submit_order"

        body = json.dumps(close_params, separators=(",", ":"))
        signature = get_bitmart_signature(secret_key, timestamp, "POST", endpoint, body)

        headers = {
            "X-BM-KEY": api_key,
            "X-BM-SIGN": signature,
            "X-BM-TIMESTAMP": timestamp,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(base_url + endpoint, headers=headers, data=body) as close_response:
                close_result = await close_response.json()

        ok = close_result.get("code") == "1000"
        return {
            "success": ok,
            "data": close_result,
            "closed_amount": lot_f,
            "symbol": normalized_symbol
        }

    except Exception as e:
        logger.error(f"[BITMART_CLOSE_ERR] {e}", exc_info=True)
        return {"success": False, "error": str(e)}



# Take Profit için yeni bir fonksiyon
async def create_take_profit_order(
    symbol: str,
    side: str,                 # open side: LONG/SHORT/buy/sell
    amount: float,
    take_profit_price: float,
    api_key: str,
    secret_key: str,
) -> Dict[str, Any]:
    try:
        normalized_symbol = validate_bitmart_symbol(symbol)

        open_side = _bitmart_normalize_side(side)
        close_side = "sell" if open_side == "buy" else "buy"

        try:
            amt_f = float(amount)
        except Exception:
            amt_f = 0.0
        if amt_f <= 0:
            return {"success": False, "error": "amount<=0"}

        tp = float(take_profit_price)
        tp_q = _bitmart_round_price(normalized_symbol, tp)

        tp_params = {
            "symbol": normalized_symbol,
            "side": close_side,
            "type": "limit",
            "price": str(tp_q),
            "size": str(amt_f)
        }

        base_url = "https://api-cloud.bitmart.com"
        timestamp = str(int(time.time() * 1000))
        endpoint = "/spot/v1/submit_order"

        body = json.dumps(tp_params, separators=(",", ":"))
        signature = get_bitmart_signature(secret_key, timestamp, "POST", endpoint, body)

        headers = {
            "X-BM-KEY": api_key,
            "X-BM-SIGN": signature,
            "X-BM-TIMESTAMP": timestamp,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(base_url + endpoint, headers=headers, data=body) as resp:
                rj = await resp.json()

        return {"success": rj.get("code") == "1000", "data": rj, "price": tp_q}

    except Exception as e:
        logger.error(f"[BITMART_TP_ERR] {e}", exc_info=True)
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
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            return True
        except Exception as e:
            logger.error(f"❌ Bildirim gönderme hatası: {str(e)}")
            return False

def _bitmart_normalize_side(side: str) -> str:
    """
    BitMart spot order side: 'buy' veya 'sell'
    Girdi: LONG/SHORT/buy/sell
    """
    s = (side or "").strip().lower()
    if s in ("buy", "sell"):
        return s
    if s in ("long",):
        return "buy"
    if s in ("short",):
        return "sell"
    raise ValueError(f"invalid side: {side}")


def _bitmart_base_coin_from_symbol(symbol: str) -> str:
    """
    BitMart spot symbol çoğunlukla 'BTCUSDT' gibi.
    'BTC_USDT' / 'BTC-USDT' gibi gelirse de normalize eder.
    """
    s = (symbol or "").upper().strip().replace("-", "").replace("_", "").replace("/", "")
    if s.endswith("USDT"):
        return s[:-4]
    return s


def _bitmart_round_price(symbol: str, price: float) -> float:
    """
    Fiyatı meta(price_step/decimals) ile yuvarlar.
    Meta yoksa 3 decimal fallback.
    """
    meta = get_symbol_meta(symbol) or {}
    step = meta.get("price_step")
    dec = meta.get("price_decimals")

    try:
        p = float(price)
    except Exception:
        return float(price)

    # fallback
    if not step or not dec:
        return round(p, 3)

    try:
        step_f = float(step)
        dec_i = int(dec)
        if step_f <= 0:
            return round(p, dec_i)
        # step'e floor
        return math.floor(p / step_f) * step_f
    except Exception:
        return round(p, 3)


async def _bitmart_fetch_spot_balance_amount(api_key: str, secret_key: str, coin: str) -> float:
    """
    Spot bakiyeden belirli coin'in available miktarını döndürür.
    code bazen int(1000), bazen str("1000") gelebilir.
    wallet item coin key'i bazen 'coin', bazen 'id'.
    """
    base_url = "https://api-cloud.bitmart.com"

    # En doğru endpoint: /spot/v1/wallet
    endpoints_try = [
        "/spot/v1/wallet",
        "/spot/v1/account",   # bazı hesaplarda yok, ama dursun
    ]

    coin_u = str(coin or "").upper().strip()
    if not coin_u:
        return 0.0

    def _is_ok_code(v) -> bool:
        try:
            return str(v) == "1000" or int(v) == 1000
        except Exception:
            return False

    for endpoint in endpoints_try:
        try:
            timestamp = str(int(time.time() * 1000))
            signature = get_bitmart_signature(secret_key, timestamp, "GET", endpoint)

            headers = {
                "X-BM-KEY": api_key,
                "X-BM-SIGN": signature,
                "X-BM-TIMESTAMP": timestamp,
                "Content-Type": "application/json",
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(f"{base_url}{endpoint}", headers=headers) as response:
                    ctype = (response.headers.get("Content-Type") or "").lower()

                    if "application/json" not in ctype:
                        body_txt = await response.text()
                        logger.error(
                            f"[BITMART_BAL_HTTP_NONJSON] endpoint={endpoint} status={response.status} "
                            f"ctype={ctype} body={body_txt[:200]}"
                        )
                        continue

                    result = await response.json()

            if not _is_ok_code(result.get("code")):
                logger.error(f"[BITMART_BAL_FAIL] endpoint={endpoint} resp={str(result)[:400]}")
                continue

            data = result.get("data") or {}
            wallet = data.get("wallet", []) or []

            # wallet dict gelirse list'e çevir
            if isinstance(wallet, dict):
                wallet = [wallet]

            if not isinstance(wallet, list):
                logger.error(f"[BITMART_BAL_WALLET_BADTYPE] endpoint={endpoint} type={type(wallet)}")
                continue

            for item in wallet:
                # BitMart /spot/v1/wallet -> 'id'
                item_coin = item.get("coin")
                if item_coin is None:
                    item_coin = item.get("id")

                if str(item_coin or "").upper() == coin_u:
                    try:
                        return float(item.get("available", 0) or 0)
                    except Exception:
                        return 0.0

            logger.warning(f"[BITMART_BAL_NOT_FOUND] endpoint={endpoint} coin={coin_u}")
            return 0.0

        except Exception as e:
            logger.error(f"[BITMART_BAL_FETCH_ERR] endpoint={endpoint} coin={coin_u} err={e}", exc_info=True)
            continue

    return 0.0



async def handle_emergency_close_callback(update: Update, _context: CallbackContext) -> State:
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
        close_result = await bitmart_close_position(
            symbol=symbol,
            side=side,
            api_key=api_key,
            secret_key=secret_key,
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
