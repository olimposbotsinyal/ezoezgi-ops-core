# bybit_api_ayarlari dosyamız burdan başlıyor
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from telegram.ext import ContextTypes
from data.olimpos_data import db_operation
from logger_config import setup_logging
from config.constants import State
import json
import time
import hmac
import hashlib
import aiohttp
import urllib
import urllib.parse
import requests
import asyncio
import traceback

# Özel bir logger oluşturun ve yapılandırın
logger = setup_logging('bybit_api_ayarlari_logları')
# logger.info("Bu bir bilgi mesajıdır.")


def some_function():
    logger.info("Bu bir bilgi mesajıdır.")


async def run_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await bybit_ayarlari_menu(update, context)


def get_server_time():
    """
    Bybit sunucu zamanını alma
    """
    try:
        response = requests.get("https://api.bybit.com/v5/market/time")
        if response.status_code == 200:
            return response.json().get('time', int(time.time() * 1000))
        else:
            return int(time.time() * 1000)
    except Exception as e:
        logger.error(f"Sunucu zamanı alınırken hata: {str(e)}")
        return int(time.time() * 1000)


async def safe_bybit_api_call(func, *args, **kwargs):
    """API çağrılarını güvenli şekilde yap"""
    try:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as retry_error:
                logger.warning(f"API çağrısı hatası, deneme {attempt + 1}: {str(retry_error)}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)  # Yeniden denemeden önce bekle

        logger.error("Tüm API çağrısı denemeleri başarısız oldu")
        return None
    except Exception as e:
        logger.critical(f"Kritik API hatası: {str(e)}")
        return None


async def bybit_ayarlari_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    _ = context  # Bu satır uyarıyı engeller

    logger.info("bybit_Api_Ayarlari Menüsü Gösteriliyor.")
    query = update.callback_query
    if query:
        await query.answer()

    keyboard = [
        [InlineKeyboardButton("Bakiye Kontrol", callback_data="bybit_balance")],
        [InlineKeyboardButton("İşlem Geçmişi", callback_data="bybit_trade_history")],
        [InlineKeyboardButton("Açık Emirler", callback_data="bybit_open_orders")],
        [InlineKeyboardButton("Aktif Pozisyonlar", callback_data="bybit_positions")],
        [InlineKeyboardButton("Ana Menü", callback_data="main_menu")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    if query:
        await query.edit_message_text("bybit API İşlemleri", reply_markup=reply_markup)
    else:
        await update.message.reply_text("bybit API İşlemleri", reply_markup=reply_markup)

    logger.info("bybit menüsü gösterildi.")
    return State.BYBIT_MENU


def get_bybit_signature(secret_key, api_key, timestamp, recv_window, query_string):
    """
    Bybit V5 API için doğru imza oluşturma

    Algoritma: HMAC_SHA256(timestamp + api_key + recv_window + query_string, secret_key)
    """
    # Bybit V5 API için doğru formatta imza string'i oluştur
    signature_payload = f"{timestamp}{api_key}{recv_window}{query_string}"

    # Debug için payload logla
    logger.info(f"🔍 İmza için oluşturulan payload: {signature_payload}")

    # HMAC-SHA256 imzası oluştur
    signature = hmac.new(
        secret_key.encode('utf-8'),
        signature_payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    return signature


async def handle_bybit_actions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    query = update.callback_query
    await query.answer()

    full_action = query.data
    logger.info(f"handle_bybit_actions çağrıldı. Action: {full_action}")

    user_id = query.from_user.id
    logger.info(f"Kullanıcı ID: {user_id}, İşlem: {full_action}")

    # Rate limiting kontrolü ekle
    if not await check_rate_limit(user_id):
        await query.edit_message_text(
            "Çok fazla istek gönderdiniz. Lütfen biraz bekleyin.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Ana Menü", callback_data='main_menu')
            ]])
        )
        return State.MAIN_MENU

    if full_action == "select_exchange_bybit":
        return await bybit_ayarlari_menu(update, context)
    elif full_action == "bybit_menu":
        return await bybit_ayarlari_menu(update, context)
    elif full_action == "main_menu":
        logger.info("Ana menüye dönüş talep edildi.")
        from Olimpos_Cripto_Bot import show_main_menu
        return await show_main_menu(update, context)

    action = full_action.split('_', 1)[-1]
    logger.info(f"İşlenecek action: {action}")

    try:
        api_info = await get_api_key(user_id, 'bybit')
        logger.info(f"API bilgileri alındı: {bool(api_info)}")

        if not api_info:
            logger.warning(f"Kullanıcı {user_id} için API bilgisi bulunamadı.")
            await query.edit_message_text(
                "Henüz kayıtlı API hesabınız bulunmamaktadır.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Ana Menü", callback_data='main_menu')
                ]])
            )
            return State.MAIN_MENU

        api_key = api_info["api_key"]
        secret_key = api_info["secret_key"]

        # Mevcut işlem işleyicileri...
        if action == "balance":
            logger.info("Bakiye kontrolü başlatılıyor...")
            try:
                balance_message, reply_markup = await get_bybit_balance(api_key, secret_key)
                await query.edit_message_text(
                    balance_message,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
            except Exception as e:
                logger.error(f"Bakiye sorgulama hatası: {str(e)}")
                await handle_api_error(query, e)

        elif action == "trade_history":
            logger.info(f"Kullanıcı {user_id} için işlem geçmişi talep edildi.")
            try:
                trade_history, reply_markup = await safe_bybit_api_call(
                    get_bybit_trade_history, api_key, secret_key
                )
                await query.edit_message_text(
                    trade_history,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
            except Exception as e:
                logger.error(f"İşlem geçmişi sorgulama hatası: {str(e)}")
                await handle_api_error(query, e)

        elif action == "open_orders":
            logger.info("Açık emirler sorgulanıyor...")
            try:
                open_orders_message, reply_markup = await safe_bybit_api_call(
                    get_bybit_open_orders, api_key, secret_key
                )
                await query.edit_message_text(
                    open_orders_message,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
            except Exception as e:
                logger.error(f"Açık emirler sorgulama hatası: {str(e)}")
                await handle_api_error(query, e)

        elif full_action.startswith("close_bybit_position_"):
            try:
                parts = full_action.split("_")
                if len(parts) < 5:
                    raise ValueError("Geçersiz pozisyon kapatma formatı")

                symbol = parts[3]
                size = float(parts[4].replace("_", "."))

                # Pozisyon büyüklüğü kontrolü
                if size <= 0:
                    raise ValueError("Geçersiz pozisyon büyüklüğü")

                # Pozisyon kapatma işlemi öncesi onay mesajı
                confirmation_message = (
                    f"⚠️ <b>DİKKAT</b> ⚠️\n\n"
                    f"Aşağıdaki pozisyonu kapatmak üzeresiniz:\n"
                    f"Sembol: {symbol}\n"
                    f"Miktar: {size}\n\n"
                    f"Bu işlem geri alınamaz. Devam etmek istiyor musunuz?"
                )

                keyboard = [
                    [
                        InlineKeyboardButton("Evet, Kapat", callback_data=f"confirm_close_{full_action}"),
                        InlineKeyboardButton("İptal", callback_data="bybit_positions")
                    ]
                ]
                await query.edit_message_text(
                    confirmation_message,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='HTML'
                )
                return State.BYBIT_MENU

            except Exception as e:
                logger.error(f"Pozisyon kapatma işleminde hata: {str(e)}")
                await handle_api_error(query, e)

        return State.BYBIT_MENU

    except Exception as e:
        logger.error(f"Bybit işlemleri sırasında hata: {str(e)}", exc_info=True)
        await handle_api_error(query, e)
        return State.MAIN_MENU


async def check_rate_limit(user_id: int) -> bool:
    """Kullanıcı için rate limiting kontrolü"""
    # Rate limiting mantığını buraya ekleyin
    return True


async def handle_api_error(query: CallbackQuery, error: Exception):
    """API hatalarını standart bir şekilde işle"""
    error_message = (
        f"İşlem sırasında bir hata oluştu:\n{str(error)}\n\n"
        "Lütfen daha sonra tekrar deneyin."
    )
    keyboard = [[
        InlineKeyboardButton("Ana Menü", callback_data='main_menu'),
        InlineKeyboardButton("Bybit Menüsü", callback_data='bybit_menu')
    ]]
    await query.edit_message_text(
        error_message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )


async def get_api_key(user_id, exchange):
    """
    Veritabanından kullanıcı API bilgilerini çeker
    """
    query = """
        SELECT api_key, secret_key, passphrase
        FROM api_key
        WHERE user_id = ? AND exchange = ?
    """
    params = (user_id, exchange)
    result = db_operation(query, params, operation='select', fetch=True, fetch_all=False)

    if result and isinstance(result, (list, tuple)) and len(result) >= 3:
        return {
            'api_key': result[0],
            'secret_key': result[1],
            'passphrase': result[2]
        }
    return None


async def get_bybit_balance(api_key, secret_key):
    try:
        logger.info("🚀 Bybit bakiyesi alma işlemi başlatılıyor...")

        base_url = "https://api.bybit.com"
        # Sadece UNIFIED hesap türünü kullan
        account_types = ["UNIFIED"]

        timestamp = str(int(time.time() * 1000))
        recv_window = "5000"

        # Bakiye detayları için boş sözlük
        balance_details = {
            "UNIFIED": {"total_equity": 0, "available_balance": 0, "margin_balance": 0, "wallet_balance": 0},
            "CONTRACT": {"total_equity": 0, "available_balance": 0},
            "SPOT": {"total_equity": 0, "available_balance": 0}
        }

        for account_type in account_types:
            try:
                params = {
                    "accountType": account_type,
                    "timestamp": timestamp,
                    "recv_window": recv_window
                }

                query_string = '&'.join([f"{k}={urllib.parse.quote(str(v))}" for k, v in sorted(params.items())])
                signature = get_bybit_signature(secret_key, api_key, timestamp, recv_window, query_string)

                headers = {
                    "X-BAPI-API-KEY": api_key,
                    "X-BAPI-SIGN": signature,
                    "X-BAPI-TIMESTAMP": timestamp,
                    "X-BAPI-RECV-WINDOW": recv_window,
                    "Content-Type": "application/json"
                }

                url = f"{base_url}/v5/account/wallet-balance?{query_string}"

                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers=headers) as response:
                        response_text = await response.text()
                        logger.info(f"API Yanıtı: {response_text}")  # Tam yanıtı logla

                        try:
                            data = json.loads(response_text)

                            if data.get("retCode") == 0:
                                result_list = data['result'].get('list', [])

                                if result_list:
                                    result = result_list[0]
                                    coins = result.get('coin', [])

                                    # Güvenli float dönüşümü
                                    def safe_float(value, default=0):
                                        try:
                                            # Boş string veya None kontrolü
                                            return float(value) if value and value != '' else default
                                        except (ValueError, TypeError):
                                            return default

                                    # Unified hesap detayları
                                    balance_details['UNIFIED'] = {
                                        "total_equity": safe_float(result.get('totalEquity')),
                                        "available_balance": safe_float(result.get('totalAvailableBalance')),
                                        "margin_balance": safe_float(result.get('totalMarginBalance')),
                                        "wallet_balance": safe_float(result.get('totalWalletBalance'))
                                    }

                                    # Coin detaylarını kontrol et
                                    if coins:
                                        coin_details = coins[0]

                                        # Kontrat (Futures) hesabı detayları
                                        balance_details['CONTRACT'] = {
                                            "total_equity": safe_float(coin_details.get('totalPositionIM')),
                                            # Eğer availableToWithdraw boşsa, wallet balance kullan
                                            "available_balance": safe_float(
                                                coin_details.get('availableToWithdraw')) or safe_float(
                                                coin_details.get('walletBalance'))
                                        }

                                        # Spot hesabı detayları
                                        balance_details['SPOT'] = {
                                            "total_equity": safe_float(coin_details.get('equity')),
                                            # Eğer availableToBorrow boşsa, wallet balance kullan
                                            "available_balance": safe_float(
                                                coin_details.get('availableToBorrow')) or safe_float(
                                                coin_details.get('walletBalance'))
                                        }

                                    # Log detayları
                                    logger.info("🔍 İşlenen Bakiye Detayları:")
                                    for account, details in balance_details.items():
                                        logger.info(f"{account}: {details}")

                                else:
                                    logger.warning("❗ Sonuç listesi boş!")
                            else:
                                logger.error(f"❌ API Hatası: {data.get('retMsg', 'Bilinmeyen Hata')}")

                        except Exception as parse_error:
                            logger.error(f"❌ Veri İşleme Hatası: {str(parse_error)}")
                            logger.error(f"❌ Hata Detayları: {traceback.format_exc()}")

            except Exception as endpoint_error:
                logger.error(f"❌ Endpoint Hatası: {str(endpoint_error)}")

        # Telegram mesaj formatı
        message = f"""🏦 *Bybit Hesap Bakiyesi* 🏦

        📊 *Birleşik (Unified) Hesap:*
        💰 Toplam Varlık: `{balance_details['UNIFIED']['total_equity']:.2f} USDT`
        💸 Kullanılabilir Bakiye: `{balance_details['UNIFIED']['available_balance']:.2f} USDT`
        🔒 Bloke Bakiye: `{balance_details['UNIFIED']['wallet_balance'] - balance_details['UNIFIED']['available_balance']:.2f} USDT`

        🔮 *Vadeli (Futures) Hesap:*
        💰 Toplam Varlık: `{balance_details['CONTRACT']['total_equity']:.2f} USDT`
        💸 Kullanılabilir Bakiye: `{balance_details['CONTRACT']['available_balance']:.2f} USDT`

        💱 *Spot Hesap:*
        💸 Kullanılabilir Bakiye: `{balance_details['SPOT']['available_balance']:.2f} USDT`

        *Toplam Bakiye:* `{balance_details['UNIFIED']['total_equity']:.2f} USDT`
        """

        # Klavye düğmeleri
        keyboard = [
            [InlineKeyboardButton("Detaylı Bakiye", callback_data='bybit_detailed_balance')],
            [InlineKeyboardButton("Bybit Menüsü", callback_data='bybit_menu')],
            [InlineKeyboardButton("Ana Menü", callback_data='main_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        return message, reply_markup

    except Exception as e:
        error_message = f"❌ Genel Bakiye Alma Hatası: {str(e)}"
        error_keyboard = [
            [InlineKeyboardButton("Tekrar Dene", callback_data='bybit_balance')],
            [InlineKeyboardButton("API Ayarları", callback_data='bybit_api_settings')]
        ]
        error_reply_markup = InlineKeyboardMarkup(error_keyboard)
        return error_message, error_reply_markup


async def get_bybit_trade_history(api_key, secret_key):
    try:
        base_url = "https://api.bybit.com"
        endpoint = "/v5/execution/list"
        method = "GET"
        timestamp = str(int(time.time() * 1000))
        recv_window = "5000"

        # Spot işlemleri için parametreler
        spot_params = {
            "category": "spot",
            "limit": "20",
            "api_key": api_key,
            "timestamp": timestamp,
            "recv_window": recv_window
        }

        # Vadeli işlemleri için parametreler
        futures_params = {
            "category": "linear",
            "limit": "20",
            "api_key": api_key,
            "timestamp": timestamp,
            "recv_window": recv_window
        }

        # Ortak headers
        headers = {
            "X-BAPI-API-KEY": api_key,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": recv_window,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            # Spot işlemleri al
            spot_signature = get_bybit_signature(secret_key, timestamp, method, endpoint, spot_params)
            headers["X-BAPI-SIGN"] = spot_signature
            spot_url = f"{base_url}{endpoint}?{urllib.parse.urlencode(spot_params)}"

            async with session.get(spot_url, headers=headers) as response:
                spot_data = await response.json()
                logger.info(f"Spot işlemler yanıtı: {spot_data}")

            # Vadeli işlemleri al
            futures_signature = get_bybit_signature(secret_key, timestamp, method, endpoint, futures_params)
            headers["X-BAPI-SIGN"] = futures_signature
            futures_url = f"{base_url}{endpoint}?{urllib.parse.urlencode(futures_params)}"

            async with session.get(futures_url, headers=headers) as response:
                futures_data = await response.json()
                logger.info(f"Vadeli işlemler yanıtı: {futures_data}")

        trade_history = "<b>Bybit İşlem Geçmişi:</b>\n\n"

        # Spot işlemleri ekle
        if spot_data.get("retCode") == 0:
            spot_trades = spot_data.get("result", {}).get("list", [])
            trade_history += "<b>SPOT İşlemler:</b>\n"

            if spot_trades:
                for trade in spot_trades[:10]:
                    trade_history += (
                        f"<pre>"
                        f"Sembol: {trade.get('symbol', '')}\n"
                        f"İşlem Tipi: {trade.get('side', '')}\n"
                        f"Fiyat: {trade.get('price', '')} USDT\n"
                        f"Miktar: {trade.get('qty', '')}\n"
                        f"Zaman: "
                        f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(trade.get('execTime', '0')) / 1000))}"
                        f"\n"
                        f"</pre>"
                        f"------------------------\n"
                    )
            else:
                trade_history += "Spot işlem bulunamadı.\n\n"

        # Vadeli işlemleri ekle
        if futures_data.get("retCode") == 0:
            futures_trades = futures_data.get("result", {}).get("list", [])
            trade_history += "\n<b>VADELİ İşlemler:</b>\n"

            if futures_trades:
                for trade in futures_trades[:10]:
                    trade_history += (
                        f"<pre>"
                        f"Sembol: {trade.get('symbol', '')}\n"
                        f"İşlem Tipi: {trade.get('side', '')}\n"
                        f"Fiyat: {trade.get('price', '')} USDT\n"
                        f"Miktar: {trade.get('qty', '')}\n"
                        f"Zaman: "
                        f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(trade.get('execTime', '0')) / 1000))}"
                        f"\n"
                        f"</pre>"
                        f"------------------------\n"
                    )
            else:
                trade_history += "Vadeli işlem bulunamadı.\n"

        keyboard = [
            [InlineKeyboardButton("İşlemleri Yenile", callback_data='bybit_trade_history')],
            [InlineKeyboardButton("Ana Menü", callback_data='main_menu')],
            [InlineKeyboardButton("Bybit Menüsü", callback_data='bybit_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        return trade_history, reply_markup

    except Exception as e:
        logger.error(f"İşlem geçmişi alınırken hata: {str(e)}")
        keyboard = [
            [InlineKeyboardButton("Ana Menü", callback_data='main_menu')],
            [InlineKeyboardButton("Bybit Menüsü", callback_data='bybit_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        return f"Bir hata oluştu: {str(e)}", reply_markup


async def get_bybit_open_orders(api_key, secret_key):
    try:
        base_url = "https://api.bybit.com"
        endpoint = "/v5/order/realtime"
        method = "GET"
        timestamp = str(int(time.time() * 1000))
        recv_window = "5000"

        # Spot emirleri için parametreler
        spot_params = {
            "category": "spot",
            "recv_window": recv_window,
            "timestamp": timestamp
        }

        # Vadeli emirleri için parametreler
        futures_params = {
            "category": "linear",
            "recv_window": recv_window,
            "timestamp": timestamp
        }

        # Ortak headers
        headers = {
            "X-BAPI-API-KEY": api_key,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": recv_window,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            # Spot emirleri al
            spot_signature = get_bybit_signature(secret_key, timestamp, method, endpoint, spot_params)
            headers["X-BAPI-SIGN"] = spot_signature
            spot_url = f"{base_url}{endpoint}?{urllib.parse.urlencode(spot_params)}"

            async with session.get(spot_url, headers=headers) as response:
                spot_data = await response.json()
                logger.info(f"Spot emirler yanıtı: {spot_data}")

            # Vadeli emirleri al
            futures_signature = get_bybit_signature(secret_key, timestamp, method, endpoint, futures_params)
            headers["X-BAPI-SIGN"] = futures_signature
            futures_url = f"{base_url}{endpoint}?{urllib.parse.urlencode(futures_params)}"

            async with session.get(futures_url, headers=headers) as response:
                futures_data = await response.json()
                logger.info(f"Vadeli emirler yanıtı: {futures_data}")

        # Mesaj oluşturma kısmı...
        open_orders = "<b>Bybit Açık Emirler:</b>\n\n"

        # Spot emirleri işle
        if spot_data.get("retCode") == 0:
            spot_orders = spot_data.get("result", {}).get("list", [])
            open_orders += "<b>Spot Emirler:</b>\n"

            if spot_orders:
                for order in spot_orders:
                    open_orders += (
                        f"<pre>"
                        f"Sembol: {order.get('symbol', '')}\n"
                        f"Yön: {order.get('side', '')}\n"
                        f"Fiyat: {order.get('price', '')} USDT\n"
                        f"Miktar: {order.get('qty', '')}\n"
                        f"Emir Tipi: {order.get('orderType', '')}\n"
                        f"Durum: {order.get('orderStatus', '')}\n"
                        f"</pre>"
                        f"------------------------\n"
                    )
            else:
                open_orders += "Açık spot emir bulunamadı.\n\n"

        # Vadeli emirleri işle
        if futures_data.get("retCode") == 0:
            futures_orders = futures_data.get("result", {}).get("list", [])
            open_orders += "\n<b>Vadeli Emirler:</b>\n"

            if futures_orders:
                for order in futures_orders:
                    open_orders += (
                        f"<pre>"
                        f"Sembol: {order.get('symbol', '')}\n"
                        f"Yön: {order.get('side', '')}\n"
                        f"Fiyat: {order.get('price', '')} USDT\n"
                        f"Miktar: {order.get('qty', '')}\n"
                        f"Emir Tipi: {order.get('orderType', '')}\n"
                        f"Durum: {order.get('orderStatus', '')}\n"
                        f"</pre>"
                        f"------------------------\n"
                    )
            else:
                open_orders += "Açık vadeli emir bulunamadı.\n"

        keyboard = [
            [InlineKeyboardButton("Emirleri Yenile", callback_data='bybit_open_orders')],
            [InlineKeyboardButton("Ana Menü", callback_data='main_menu')],
            [InlineKeyboardButton("Bybit Menüsü", callback_data='bybit_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        return open_orders, reply_markup

    except Exception as e:
        logger.error(f"Açık emirler alınırken hata oluştu: {str(e)}")
        raise


async def close_bybit_position(api_key, secret_key, symbol, size):
    try:
        base_url = "https://api.bybit.com"
        endpoint = "/v5/order/create"

        timestamp = str(int(time.time() * 1000))

        # Önce pozisyon yönünü almak için pozisyonları sorgula
        position_endpoint = "/v5/position/list"
        position_params = {
            "category": "linear",
            "symbol": symbol,
            "api_key": api_key,
            "timestamp": timestamp
        }

        position_signature = get_bybit_signature(secret_key, timestamp, "POST", position_endpoint, position_params)

        headers = {
            "X-BAPI-API-KEY": api_key,
            "X-BAPI-SIGN": position_signature,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": "5000",
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            position_query = '&'.join(
                [f"{k}={urllib.parse.quote(str(v))}" for k, v in sorted(position_params.items()) if k != 'sign'])
            position_query += f"&sign={position_signature}"

            async with session.get(f"{base_url}{position_endpoint}?{position_query}", headers=headers) as response:
                position_data = await response.json()

                if position_data.get("retCode") == 0:
                    positions = position_data.get("result", {}).get("list", [])

                    if not positions:
                        return "Pozisyon bulunamadı", None

                    position = next((p for p in positions if p.get("symbol") == symbol), None)

                    if not position:
                        return f"{symbol} için pozisyon bulunamadı", None

                    # Pozisyonun ters yönünü belirle
                    side = "Sell" if position.get("side") == "Buy" else "Buy"

                    # Yeni timestamp oluştur
                    timestamp = str(int(time.time() * 1000))

                    # Pozisyonu kapatmak için sipariş parametrelerini hazırla
                    order_params = {
                        "category": "linear",
                        "symbol": symbol,
                        "side": side,
                        "orderType": "Market",
                        "qty": str(size),
                        "reduceOnly": True,
                        "api_key": api_key,
                        "timestamp": timestamp
                    }

                    # Sipariş için imza oluştur
                    order_signature = get_bybit_signature(secret_key, timestamp, "POST", endpoint, order_params)

                    headers_order = {
                        "X-BAPI-API-KEY": api_key,
                        "X-BAPI-SIGN": order_signature,
                        "X-BAPI-TIMESTAMP": timestamp,
                        "X-BAPI-RECV-WINDOW": "5000",
                        "Content-Type": "application/json"
                    }

                    order_body = {k: v for k, v in order_params.items() if k not in ['api_key', 'timestamp', 'sign']}

                    async with session.post(f"{base_url}{endpoint}", json=order_body,
                                            headers=headers_order) as order_response:
                        result = await order_response.json()

                        if result.get("retCode") == 0:
                            success_message = f"<b>{symbol} pozisyonu başarıyla kapatıldı!</b>"
                            keyboard = [
                                [InlineKeyboardButton("Pozisyonları Yenile", callback_data='bybit_positions')],
                                [InlineKeyboardButton("Ana Menü", callback_data='main_menu')],
                                [InlineKeyboardButton("Bybit Menüsü", callback_data='bybit_menu')]
                            ]
                            reply_markup = InlineKeyboardMarkup(keyboard)
                            return success_message, reply_markup
                        else:
                            error_message = f"Pozisyon kapatılamadı: {result.get('retMsg', 'Bilinmeyen hata')}"
                            keyboard = [
                                [InlineKeyboardButton("Ana Menü", callback_data='main_menu')],
                                [InlineKeyboardButton("Bybit Menüsü", callback_data='bybit_menu')]
                            ]
                            reply_markup = InlineKeyboardMarkup(keyboard)
                            return error_message, reply_markup

    except Exception as e:
        logger.error(f"Pozisyon kapatılırken hata oluştu: {str(e)}")
        keyboard = [
            [InlineKeyboardButton("Ana Menü", callback_data='main_menu')],
            [InlineKeyboardButton("Bybit Menüsü", callback_data='bybit_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        return f"Bir hata oluştu: {str(e)}", reply_markup


async def get_bybit_positions(api_key, secret_key):
    try:
        base_url = "https://api.bybit.com"
        endpoint = "/v5/position/list"

        timestamp = str(int(time.time() * 1000))
        params = {
            "category": "linear",  # Vadeli işlemler için
            "settleCoin": "USDT",  # USDT-bazlı kontratlar
            "api_key": api_key,
            "timestamp": timestamp
        }

        signature = get_bybit_signature(secret_key, timestamp, "GET", endpoint, params)
        params["sign"] = signature

        headers = {
            "X-BAPI-API-KEY": api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-TIMESTAMP": timestamp,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}{endpoint}", params=params, headers=headers) as response:
                data = await response.json()

                if data["retCode"] == 0:
                    positions = data.get("result", {}).get("list", [])
                    positions_message = "<b>Bybit Açık Pozisyonlar:</b>\n\n"

                    # Sadece açık pozisyonları filtrele (size > 0)
                    open_positions = [p for p in positions if float(p.get("size", 0)) > 0]

                    if open_positions:
                        for position in open_positions:
                            symbol = position.get("symbol", "")
                            side = position.get("side", "")
                            size = float(position.get("size", 0))
                            entry_price = float(position.get("entryPrice", 0))
                            mark_price = float(position.get("markPrice", 0))
                            pnl = float(position.get("unrealisedPnl", 0))

                            # PnL yüzdesi hesapla
                            position_value = entry_price * size
                            pnl_percentage = (pnl / position_value * 100) if position_value > 0 else 0

                            positions_message += (
                                f"<pre>"
                                f"Sembol: {symbol}\n"
                                f"Yön: {side}\n"
                                f"Miktar: {size}\n"
                                f"Giriş Fiyatı: {entry_price} USDT\n"
                                f"Güncel Fiyat: {mark_price} USDT\n"
                                f"Kar/Zarar: {pnl:.4f} USDT ({pnl_percentage:.2f}%)\n"
                                f"Kaldıraç: {position.get('leverage', '0')}x\n"
                                f"</pre>"
                                f"------------------------\n"
                            )
                        # Pozisyonlar için düğmeleri ekle
                        keyboard = []
                        for pos in open_positions:
                            symbol = pos.get("symbol", "")
                            size = float(pos.get("size", 0))
                            callback_data = f"close_bybit_position_{symbol}_{str(size).replace('.', '_')}"
                            keyboard.append(
                                [InlineKeyboardButton(f"{symbol} Pozisyonunu Kapat", callback_data=callback_data)])

                        keyboard.append([InlineKeyboardButton("Ana Menü", callback_data='main_menu')])
                        keyboard.append([InlineKeyboardButton("Bybit Menüsü", callback_data='bybit_menu')])
                        reply_markup = InlineKeyboardMarkup(keyboard)

                        return positions_message, reply_markup
                    else:
                        positions_message = "Şu anda açık pozisyonunuz bulunmamaktadır."
                        keyboard = [
                            [InlineKeyboardButton("Ana Menü", callback_data='main_menu')],
                            [InlineKeyboardButton("Bybit Menüsü", callback_data='bybit_menu')]
                        ]
                        reply_markup = InlineKeyboardMarkup(keyboard)
                        return positions_message, reply_markup
                else:
                    error_message = f"API hatası: {data.get('retMsg', 'Bilinmeyen hata')}"
                    keyboard = [
                        [InlineKeyboardButton("Ana Menü", callback_data='main_menu')],
                        [InlineKeyboardButton("Bybit Menüsü", callback_data='bybit_menu')]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    return error_message, reply_markup

    except Exception as e:
        logger.error(f"Pozisyonlar alınırken hata oluştu: {str(e)}")
        keyboard = [
            [InlineKeyboardButton("Ana Menü", callback_data='main_menu')],
            [InlineKeyboardButton("Bybit Menüsü", callback_data='bybit_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        return f"Bir hata oluştu: {str(e)}", reply_markup


async def get_bybit_detailed_positions(api_key, secret_key):
    """Bybit detaylı pozisyon bilgilerini getiren fonksiyon"""
    try:
        base_url = "https://api.bybit.com"
        endpoint = "/v5/position/list"

        timestamp = str(int(time.time() * 1000))
        params = {
            "category": "linear",  # Vadeli işlemler için
            "settleCoin": "USDT",  # USDT tabanlı kontratlar
            "api_key": api_key,
            "timestamp": timestamp
        }

        signature = get_bybit_signature(secret_key, timestamp, "GET", endpoint, params)

        headers = {
            "X-BAPI-API-KEY": api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": "5000",
            "Content-Type": "application/json"
        }

        query_string = '&'.join([
            f"{k}={urllib.parse.quote(str(params[k]))}"
            for k in sorted(params.keys())
            if k != 'sign'
        ])
        query_string += f"&sign={signature}"

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}{endpoint}?{query_string}", headers=headers) as response:
                data = await response.json()
                logger.info(f"Position API Response: {data}")

                if data.get('retCode') == 0:
                    positions = []
                    all_positions = data.get('result', {}).get('list', [])

                    # Açık pozisyonları filtrele (size > 0)
                    open_positions = [p for p in all_positions if float(p.get('size', 0)) > 0]

                    for position in open_positions:
                        try:
                            symbol = position.get('symbol', '')
                            size = float(position.get('size', 0))
                            side = position.get('side', '')
                            entry_price = float(position.get('entryPrice', 0))
                            market_price = float(position.get('markPrice', 0))
                            unrealized_pnl = float(position.get('unrealisedPnl', 0))

                            # PnL yüzdesini hesapla
                            position_value = entry_price * size
                            pnl_percentage = (unrealized_pnl / position_value * 100) if position_value > 0 else 0

                            positions.append({
                                'symbol': symbol,
                                'side': side,
                                'quantity': str(size),
                                'entry_price': f"{entry_price:.4f}",
                                'current_price': f"{market_price:.4f}",
                                'unrealized_pnl': f"{unrealized_pnl:.2f}",
                                'pnl_percentage': f"{pnl_percentage:.2f}",
                                'leverage': position.get('leverage', '0'),
                                'margin_mode': 'Çapraz' if position.get('tradeMode') == 0 else 'İzole',
                                'liquidation_price': f"{float(position.get('liqPrice', 0)):.4f}",
                                'margin': position.get('positionIM', '0'),
                                'callback_data': f'close_bybit_position_{symbol}_{str(size).replace(".", "_")}'
                            })
                        except (ValueError, TypeError) as e:
                            logger.error(f"Pozisyon verisi işlenirken hata: {str(e)}")
                            continue

                    return positions
                else:
                    logger.error(f"Position API Error: {data.get('retMsg', 'Unknown error')}")
                    return []

    except Exception as e:
        logger.error(f"Pozisyon alımında hata: {str(e)}")
        return []


async def save_bybit_balance_to_db(user_id, balances):
    try:
        # Değişkenleri varsayılan değerlerle başlat
        spot_bakiye = 0.0
        vadeli_bakiye = 0.0
        birlesik_bakiye = 0.0
        toplam_bakiye = 0.0

        # Kullanıcı adını veritabanından al
        query_username = """
            SELECT username 
            FROM admin_users 
            WHERE user_id = ? 
            AND username IS NOT NULL 
            AND username != ''
        """
        username_result = db_operation(query_username, (user_id,), operation='select', fetch=True)

        if not username_result or not username_result[0]:
            logger.warning(f"Kullanıcı {user_id} için username bulunamadı")
            username = "İsimsiz Kullanıcı"
        else:
            username = username_result[0][0]

        logger.info(f"Kullanıcı bilgisi bulundu: ID={user_id}, İsim={username}")

        # Bakiyeleri hesapla
        if isinstance(balances, list) and balances:
            result = balances[0]

            # Unified hesap detayları
            birlesik_bakiye = float(result.get('totalWalletBalance', 0) or 0)
            toplam_bakiye = float(result.get('totalAvailableBalance', 0) or 0)

            # Spot ve vadeli bakiyeler için totalAvailableBalance kullan
            spot_bakiye = toplam_bakiye
            vadeli_bakiye = toplam_bakiye

        # Mevcut kaydı kontrol et
        query_check = """
        SELECT COUNT(*) as kayit_sayisi, ilk_toplam 
        FROM borsa_info 
        WHERE user_id = ? AND exchange = 'bybit'
        """
        check_result = db_operation(query_check, (user_id,), operation='select', fetch=True)

        if check_result and check_result[0][0] == 0:
            # Hiç kayıt yoksa yeni kayıt oluştur
            query = """
            INSERT INTO borsa_info (
                user_id, username, exchange, spot, vadeli, marjin, 
                fonlama, kazan, bot, guncel_toplam, ilk_toplam, son_güncelleme
            ) VALUES (?, ?, 'bybit', ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """
            params = (user_id, username, spot_bakiye, vadeli_bakiye, 0.0,
                      0.0, birlesik_bakiye, 0, toplam_bakiye, toplam_bakiye)
            db_operation(query, params, operation='insert')
            logger.info(f"Yeni Bybit kaydı oluşturuldu: {params}")
            ilk_toplam = toplam_bakiye
        else:
            # Mevcut kayıt varsa
            ilk_toplam = float(check_result[0][1])

            # Güncelleme
            kar_zarar = toplam_bakiye - ilk_toplam
            kar_zarar_str = "KAR" if kar_zarar > 0 else "ZARAR"

            query = """
            UPDATE borsa_info SET 
                username = COALESCE(?, username),
                spot = ?, 
                vadeli = ?, 
                marjin = COALESCE(marjin, ?), 
                fonlama = COALESCE(fonlama, ?), 
                kazan = ?, 
                bot = COALESCE(bot, ?), 
                guncel_toplam = ?,
                kar_zarar_toplam = ?, 
                kar_zarar = ?, 
                son_güncelleme = CURRENT_TIMESTAMP
            WHERE user_id = ? AND exchange = 'bybit'
            """
            params = (username,
                      spot_bakiye,
                      vadeli_bakiye,
                      0.0,  # Marjin
                      0.0,  # Fonlama
                      birlesik_bakiye,
                      0,  # Bot
                      toplam_bakiye,
                      kar_zarar,
                      kar_zarar_str,
                      user_id)

            db_operation(query, params, operation='update')
            logger.info(f"Bybit bakiye kaydı güncellendi: {params}")

        logger.info(f"Kullanıcı {user_id} ({username}) için Bybit bakiyesi kaydedildi: {toplam_bakiye}")
        return True

    except Exception as e:
        logger.error(f"Bybit bakiye kaydetme hatası: {str(e)}")
        return False


async def update_bybit_user_balances():
    """Tüm kullanıcıların Bybit bakiyelerini günceller"""
    try:
        # Kullanıcıları getir
        query = "SELECT user_id, api_key, secret_key FROM api_key WHERE exchange = 'bybit'"
        users = db_operation(query, operation='select', fetch=True)

        if not users:
            logger.warning("Bybit API bilgisi olan kullanıcı bulunamadı.")
            return

        for user in users:
            user_id = None
            balances = []  # Burayı ekledik
            try:
                user_id, api_key, secret_key = user

                # API bilgilerini kontrol et
                if not all([api_key, secret_key]):
                    logger.warning(f"Kullanıcı {user_id} için eksik API bilgileri.")
                    continue

                # API isteği için değerleri hazırla
                base_url = "https://api.bybit.com"
                endpoint = "/v5/account/wallet-balance"
                timestamp = str(int(time.time() * 1000))
                recv_window = "5000"

                # İstek parametreleri - API KEY BURADA OLMAMALI!
                params = {
                    "accountType": "UNIFIED",
                    "timestamp": timestamp,
                    "recv_window": recv_window
                }

                # Parametreleri sırala ve query string oluştur
                query_string = '&'.join([f"{k}={urllib.parse.quote(str(v))}" for k, v in sorted(params.items())])

                # İmza oluştur - yeni formata göre
                signature = get_bybit_signature(secret_key, api_key, timestamp, recv_window, query_string)

                # API isteği için başlıklar
                headers = {
                    "X-BAPI-API-KEY": api_key,
                    "X-BAPI-SIGN": signature,
                    "X-BAPI-TIMESTAMP": timestamp,
                    "X-BAPI-RECV-WINDOW": recv_window,
                    "Content-Type": "application/json"
                }

                # İstek URL'si
                url = f"{base_url}{endpoint}?{query_string}"

                # API isteğini gerçekleştir
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers=headers) as response:
                        data = await response.json()

                        # API yanıtını logla
                        logger.info(f"Kullanıcı {user_id} için API Yanıtı: {json.dumps(data, indent=2)}")

                        if data.get('retCode') == 0:
                            balances = data.get('result', {}).get('list', [])

                            # Bakiye bilgilerini logla
                            logger.info(f"Kullanıcı {user_id} için Bakiye Detayları: {json.dumps(balances, indent=2)}")

                            await save_bybit_balance_to_db(user_id, balances)
                            logger.info(f"Kullanıcı {user_id} için Bybit bakiyesi güncellendi.")
                        else:
                            logger.error(f"Kullanıcı {user_id} için API yanıt hatası: {data}")

            except Exception as user_error:
                error_msg = (f"Kullanıcı "
                             f"{user_id if user_id else 'bilinmeyen'} için güncelleme hatası: {str(user_error)}")
                logger.error(error_msg)

    except Exception as e:
        logger.error(f"Tüm kullanıcı bakiyeleri güncellenirken hata: {str(e)}")
