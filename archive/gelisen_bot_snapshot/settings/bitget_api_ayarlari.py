# bitget_api_ayarlari.py
from telegram.ext import ContextTypes
from data.olimpos_data import *
from logger_config import setup_logging
from config.constants import State
import time
import hmac
import aiohttp
import base64
import json
import requests
import asyncio
import hashlib

# Özel bir logger oluşturun ve yapılandırın
logger = setup_logging('bitget_api_ayarlari_logları')
# logger.info("Bitget API ayarları başlatıldı.")


def get_server_time():
    """
    Bitget sunucu zamanını alma
    """
    try:
        response = requests.get("https://api.bitget.com/api/mix/v1/market/time")
        if response.status_code == 200:
            return response.json()['data']
        else:
            return int(time.time() * 1000)

    except (aiohttp.ClientError, ValueError) as e:
        logger.error(f"Hata: {str(e)}")
        return int(time.time() * 1000)


async def safe_bitget_api_call(func, *args, **kwargs):
    try:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as retry_error:
                logger.warning(f"API çağrısı hatası, deneme {attempt + 1}: {str(retry_error)}")
                await asyncio.sleep(2)  # Kısa bir bekleme

        logger.error("Tüm API çağrısı denemeleri başarısız oldu")
        return None
    except Exception as e:
        logger.critical(f"Kritik API hatası: {str(e)}")
        return None


def get_bitget_signature(secret_key, timestamp, method, request_path, body=''):
    """
    Bitget API imzası oluşturur

    Args:
        secret_key (str): API secret key
        timestamp (str): Unix timestamp (milliseconds)
        method (str): HTTP method (GET, POST, etc.)
        request_path (str): Request path with query parameters
        body (str): Request body (empty for GET requests)
    """
    # Mesaj oluşturma
    message = timestamp + method.upper() + request_path
    if body:
        message += body

    # HMAC-SHA256 imzası oluştur
    mac = hmac.new(
        bytes(secret_key, encoding='utf8'),
        bytes(message, encoding='utf-8'),
        digestmod='sha256'
    )

    # Base64 kodlama
    signature = base64.b64encode(mac.digest()).decode('utf-8')
    return signature


async def run_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await bitget_ayarlari_menu(update, context)


async def bitget_ayarlari_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    _ = context  # Bu satır uyarıyı engeller

    logger.info("Bitget_Api_Ayarlari Menüsü Gösteriliyor.")
    query = update.callback_query
    if query:
        await query.answer()

    keyboard = [
        [InlineKeyboardButton("Bakiye Kontrol", callback_data="bitget_balance")],
        [InlineKeyboardButton("İşlem Geçmişi", callback_data="bitget_trade_history")],
        [InlineKeyboardButton("Açık Emirler", callback_data="bitget_open_orders")],
        [InlineKeyboardButton("Aktif Pozisyonlar", callback_data="bitget_positions")],
        [InlineKeyboardButton("Ana Menü", callback_data="main_menu")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    if query:
        await query.edit_message_text("Bitget API İşlemleri", reply_markup=reply_markup)
    else:
        await update.message.reply_text("Bitget API İşlemleri", reply_markup=reply_markup)

    logger.info("Bitget menüsü gösterildi.")
    return State.BITGET_MENU


async def get_api_key(user_id, exchange):
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


async def handle_bitget_actions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    query = update.callback_query
    await query.answer()

    full_action = query.data
    logger.info(f"handle_bitget_actions çağrıldı. Action: {full_action}")

    user_id = query.from_user.id
    logger.info(f"Kullanıcı ID: {user_id}, İşlem: {full_action}")

    if full_action == "select_exchange_bitget":
        return await bitget_ayarlari_menu(update, context)
    elif full_action == "bitget_menu":
        return await bitget_ayarlari_menu(update, context)
    elif full_action == "main_menu":
        logger.info("Ana menüye dönüş talep edildi.")
        from Olimpos_Cripto_Bot import show_main_menu
        return await show_main_menu(update, context)

    action = full_action.split('_', 1)[-1]
    logger.info(f"İşlenecek action: {action}")

    try:
        api_info = await get_api_key(user_id, 'bitget')
        logger.info(f"API bilgileri alındı: {api_info is not None}")

        if not api_info:
            logger.warning(f"Kullanıcı {user_id} için API bilgisi bulunamadı.")
            await query.edit_message_text("Henüz kayıtlı API hesabınız bulunmamaktadır.")
            return State.MAIN_MENU

        api_key = api_info["api_key"]
        secret_key = api_info["secret_key"]
        passphrase = api_info["passphrase"]

        if action == "balance":
            balance_message, reply_markup = await get_bitget_balance(api_key, secret_key, passphrase, user_id)
            await query.edit_message_text(balance_message, reply_markup=reply_markup, parse_mode='HTML')

        if action == "trade_history":
            trade_history, reply_markup = await get_bitget_trade_history(api_key, secret_key, passphrase, user_id)
            await query.edit_message_text(trade_history, reply_markup=reply_markup, parse_mode='HTML')

        elif action == "open_orders":
            logger.info(f"Kullanıcı {user_id} için açık emirler talep edildi.")
            message, reply_markup = await show_open_orders(api_key, secret_key, passphrase, user_id)
            await query.edit_message_text(
                message,
                reply_markup=reply_markup,
                parse_mode='HTML'
            )

        elif full_action.startswith("bitget_cancel_"):
            parts = full_action.split('_')
            order_type = parts[2]  # spot veya futures
            order_id = parts[3]
            symbol = '_'.join(parts[4:])  # Sembolde alt çizgi varsa doğru ayırmak için

            logger.info(f"Emir iptal isteği: {order_type} {order_id} {symbol}")

            success, message = await cancel_bitget_order(api_key, secret_key, passphrase, order_type, order_id, symbol)

            if success:
                await query.edit_message_text(f"{message}\n\nGüncel emirler yükleniyor...")
                updated_message, reply_markup = await show_open_orders(api_key, secret_key, passphrase, user_id)
                # Mesajı güncelle
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=updated_message,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
            else:
                # Başarısız iptal mesajı göster
                await query.edit_message_text(f"Emir iptal edilemedi: {message}")

            return State.BITGET_MENU

        elif action == "positions":
            logger.info(f"Kullanıcı {user_id} için açık pozisyonlar talep edildi.")
            positions = await get_bitget_detailed_positions(api_key, secret_key, passphrase)

            if positions:
                positions_message = "<b>Bitget Açık Pozisyonlar:</b>\n\n"
                for position in positions:
                    positions_message += (
                         f"<pre>"
                         f"Sembol: {position['symbol']}\n"
                         f"Yön: {position['side']}\n"
                         f"Miktar: {position['quantity']}\n"
                         f"Giriş Fiyatı: {position['entry_price']} USDT\n"
                         f"Güncel Fiyat: {position['current_price']} USDT\n"
                         f"Kar/Zarar: {position['unrealized_pnl']} USDT ({position['pnl_percentage']}%)\n"
                         f"Kaldıraç: {position['leverage']}x\n"
                         f"Marjin Modu: {position['margin_mode']}\n"
                         f"Likidite Fiyatı: {position['liquidation_price']} USDT\n"
                         f"Marjin: {position['margin']} USDT\n"
                         f"</pre>"
                         f"------------------------\n"
                    )

                keyboard = []
                for position in positions:
                    keyboard.append([InlineKeyboardButton(
                        f"{position['symbol']} Pozisyonunu Kapat",
                        callback_data=position['callback_data']
                    )])
                keyboard.append([
                    InlineKeyboardButton("Ana Menü", callback_data='main_menu'),
                    InlineKeyboardButton("Bitget Menüsü", callback_data='bitget_menu')
                ])
                reply_markup = InlineKeyboardMarkup(keyboard)

                await query.edit_message_text(
                    positions_message,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
            else:
                keyboard = [[
                    InlineKeyboardButton("Ana Menü", callback_data='main_menu'),
                    InlineKeyboardButton("Bitget Menüsü", callback_data='bitget_menu')
                ]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(
                    "Açık pozisyonunuz bulunmamaktadır.",
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )

        elif full_action.startswith("close_bitget_position_"):
            try:
                parts = full_action.split("_")
                symbol = parts[3]  # XRPUSDT
                position_amount = float(parts[4])  # 11.0
                api_info = await get_api_key(user_id, 'bitget')
                if not api_info:
                    await query.edit_message_text("API bilgileri bulunamadı.")
                    return State.MAIN_MENU
                # Pozisyonu kapat
                result = await close_bitget_position(
                    api_info['api_key'],
                    api_info['secret_key'],
                    api_info['passphrase'],
                    symbol,
                    position_amount
                )
                keyboard = [
                    [InlineKeyboardButton("Pozisyonları Yenile", callback_data='bitget_positions')],
                    [InlineKeyboardButton("Ana Menü", callback_data='main_menu')]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(
                    result,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
                logger.info(f"Pozisyon kapatma sonucu: {result}")

            except Exception as e:
                logger.error(f"Pozisyon kapatma işleminde hata: {str(e)}")
                await query.edit_message_text(f"İşlem sırasında hata oluştu: {str(e)}")
        return State.BITGET_MENU

    except Exception as e:
        logger.error(f"Bitget işlemleri sırasında hata: {str(e)}", exc_info=True)
        await query.edit_message_text(f"Bir hata oluştu: {str(e)}")
        return State.MAIN_MENU


async def get_bitget_balance(api_key, secret_key, passphrase, user_id):
    try:
        logger.info("Bitget bakiyesi alınıyor...")
        base_url = "https://api.bitget.com"
        endpoint = "/api/v2/account/all-account-balance"
        method = "GET"

        timestamp = str(int(time.time() * 1000))
        signature = get_bitget_signature(secret_key, timestamp, method, endpoint)

        headers = {
            "ACCESS-KEY": api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-PASSPHRASE": passphrase,
            "ACCESS-TIMESTAMP": timestamp,
            "locale": "en-US",
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(base_url + endpoint, headers=headers) as response:
                data = await response.json()

                if data['code'] == '00000':
                    balances = data['data']
                    # Bakiyeleri veritabanına kaydet
                    await save_bitget_balance_to_db(user_id, balances)

                    total_usdt = 0.0
                    balance_message = "<b>Bitget Bakiyeniz:</b>\n" + "---------------------------------\n"

                    for account in balances:
                        account_type = {
                            "spot": "Spot Hesabı",
                            "futures": "Vadeli Hesabı",
                            "funding": "Fonlama Hesabı",
                            "earn": "Kazan Hesabı",
                            "bots": "Bot Hesabı",
                            "margin": "Marj Hesabı"
                        }.get(account['accountType'], account['accountType'])

                        usdt_balance = float(account['usdtBalance'])
                        total_usdt += usdt_balance

                        balance_message += (
                            f"<pre>"
                            f"{'Hesap Türü:':<5}{account_type}\n"
                            f"{'Bakiyeniz:':<11}{usdt_balance:<2.4f} USDT\n"
                            f"</pre>"
                        )

                    balance_message += "---------------------------------\n"
                    balance_message += f"<b>Toplam Varlıklar:</b> {total_usdt:<2.4f} USDT"

                    keyboard = [
                        [InlineKeyboardButton("Bitget Menüsü", callback_data='bitget_menu')],
                        [InlineKeyboardButton("Ana Menü", callback_data='main_menu')]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    return balance_message, reply_markup

    except Exception as e:
        logger.error(f"Hata: {e}")
        return "Hata oluştu.", None


async def get_bitget_trade_history(api_key, secret_key, passphrase, user_id):
    try:
        # Spot işlemler için
        spot_endpoint = "/api/spot/v1/trade/fills"
        timestamp = str(int(time.time() * 1000))
        spot_path = f"{spot_endpoint}"
        spot_signature = get_bitget_signature(secret_key, timestamp, "GET", spot_path)

        spot_headers = {
            'ACCESS-KEY': api_key,
            'ACCESS-SIGN': spot_signature,
            'ACCESS-TIMESTAMP': timestamp,
            'ACCESS-PASSPHRASE': passphrase,
            'Content-Type': 'application/json'
        }

        # Vadeli işlemler için
        futures_endpoint = "/api/mix/v1/order/history"
        futures_params = {
            "startTime": str(int(time.time() * 1000) - 7 * 24 * 60 * 60 * 1000),
            "endTime": str(int(time.time() * 1000)),
            "pageSize": "50"
        }

        futures_path = f"{futures_endpoint}?{'&'.join([f'{k}={v}' for k, v in futures_params.items()])}"
        futures_signature = get_bitget_signature(secret_key, timestamp, "GET", futures_path)

        futures_headers = {
            'ACCESS-KEY': api_key,
            'ACCESS-SIGN': futures_signature,
            'ACCESS-TIMESTAMP': timestamp,
            'ACCESS-PASSPHRASE': passphrase,
            'Content-Type': 'application/json'
        }

        base_url = "https://api.bitget.com"

        async with aiohttp.ClientSession() as session:
            # Spot işlemleri al
            spot_trades = []
            async with session.get(f"{base_url}{spot_path}", headers=spot_headers) as response:
                spot_data = await response.json()
                if spot_data.get('code') in ['00000', '0']:
                    spot_trades = spot_data.get('data', [])

            # Vadeli işlemleri al
            futures_trades = []
            async with session.get(f"{base_url}{futures_path}", headers=futures_headers) as response:
                futures_data = await response.json()
                if futures_data.get('code') == '00000':
                    futures_trades = futures_data.get('data', {}).get('orderList', [])

        # Mesajları oluştur
        spot_message = "<b>SPOT İşlem Geçmişi:</b>\n\n"
        if spot_trades:
            for trade in spot_trades[:10]:
                spot_message += (
                    f"<pre>"
                    f"Sembol: {trade['symbol']}\n"
                    f"İşlem Tipi: {trade['side']}\n"
                    f"Fiyat: {trade['price']} USDT\n"
                    f"Miktar: {trade['quantity']}\n"
                    f"Zaman: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(trade['cTime']) / 1000))}\n"
                    f"</pre>"
                    f"------------------------\n"
                )
        else:
            spot_message += "Spot işlem geçmişi bulunamadı.\n\n"

        futures_message = "\n<b>VADELİ İşlem Geçmişi:</b>\n\n"
        if futures_trades:
            for trade in futures_trades[:10]:
                futures_message += (
                    f"<pre>"
                    f"Sembol: {trade['symbol']}\n"
                    f"İşlem Tipi: {trade['side']}\n"
                    f"Fiyat: {trade['price']} USDT\n"
                    f"Miktar: {trade['size']}\n"
                    f"Zaman: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(trade['createTime']) / 1000))}\n"
                    f"</pre>"
                    f"------------------------\n"
                )
        else:
            futures_message += "Vadeli işlem geçmişi bulunamadı.\n"

        message = spot_message + futures_message

        keyboard = [
            [InlineKeyboardButton("Bitget Menüsü", callback_data='bitget_menu')],
            [InlineKeyboardButton("Ana Menü", callback_data='main_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        return message, reply_markup

    except Exception as e:
        logger.error(f"İşlem geçmişi alınırken hata: {str(e)}")
        keyboard = [
            [InlineKeyboardButton("Bitget Menüsü", callback_data='bitget_menu')],
            [InlineKeyboardButton("Ana Menü", callback_data='main_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        return f"Bir hata oluştu: {str(e)}", reply_markup


async def get_bitget_open_orders(api_key, secret_key, passphrase, user_id):
    try:
        base_url = "https://api.bitget.com"

        # Sunucu zamanı için yerel zamanı kullanma (artık çalıştığı için aynı kalsın)
        timestamp = str(int(time.time() * 1000))
        logger.info(f"Timestamp oluşturuldu: {timestamp}")

        async with aiohttp.ClientSession() as session:
            all_orders = {
                'spot': [],
                'futures': [],
                'positions': []
            }

            # ========== SPOT EMİRLERİ ==========
            # Spot için açık emirleri alma - tüm semboller için
            spot_endpoint = "/api/v2/spot/trade/unfilled-orders"

            # Parametre vermeden tüm açık emirleri alalım
            spot_path = spot_endpoint

            # İmzalama
            pre_hash = timestamp + "GET" + spot_path
            signature = base64.b64encode(
                hmac.new(secret_key.encode('utf-8'), pre_hash.encode('utf-8'), hashlib.sha256).digest()).decode()

            spot_headers = {
                'ACCESS-KEY': api_key,
                'ACCESS-SIGN': signature,
                'ACCESS-TIMESTAMP': timestamp,
                'ACCESS-PASSPHRASE': passphrase,
                'locale': 'en-US',
                'Content-Type': 'application/json'
            }

            spot_url = f"{base_url}{spot_path}"
            logger.info(f"Spot Açık Emirler isteği: {spot_url}")

            async with session.get(spot_url, headers=spot_headers) as response:
                spot_data = await response.json()
                logger.info(f"Spot Açık Emirler yanıtı: {spot_data}")

                if spot_data.get('code') == '00000':
                    all_orders['spot'] = spot_data.get('data', [])
                    logger.info(f"Spot açık emir sayısı: {len(all_orders['spot'])}")
                else:
                    logger.warning(f"Spot emirleri alınamadı. Hata: {spot_data.get('msg')}")

            # ========== VADELİ İŞLEMLER - AÇIK EMİRLER ==========
            # V2 API kullanarak vadeli açık emirleri alma
            futures_orders_endpoint = "/api/v2/mix/order/unfilled-orders"
            futures_orders_params = {
                'productType': 'USDT-FUTURES',  # API dokümantasyonuna göre
                'marginCoin': 'USDT'
            }

            query_string = '&'.join([f'{k}={v}' for k, v in futures_orders_params.items()])
            futures_orders_path = f"{futures_orders_endpoint}?{query_string}"

            pre_hash = timestamp + "GET" + futures_orders_path
            futures_orders_signature = base64.b64encode(
                hmac.new(secret_key.encode('utf-8'), pre_hash.encode('utf-8'), hashlib.sha256).digest()).decode()

            futures_orders_headers = {
                'ACCESS-KEY': api_key,
                'ACCESS-SIGN': futures_orders_signature,
                'ACCESS-TIMESTAMP': timestamp,
                'ACCESS-PASSPHRASE': passphrase,
                'locale': 'en-US',
                'Content-Type': 'application/json'
            }

            futures_orders_url = f"{base_url}{futures_orders_path}"
            logger.info(f"Vadeli Açık Emirler isteği: {futures_orders_url}")

            async with session.get(futures_orders_url, headers=futures_orders_headers) as response:
                futures_orders_data = await response.json()
                logger.info(f"Vadeli Açık Emirler yanıtı: {futures_orders_data}")

                if futures_orders_data.get('code') == '00000':
                    all_orders['futures'] = futures_orders_data.get('data', [])
                    logger.info(f"Vadeli açık emir sayısı: {len(all_orders['futures'])}")
                else:
                    # V1 API'yi deneme
                    v1_futures_endpoint = "/api/mix/v1/order/marginCoinCurrent"
                    v1_futures_params = {
                        'marginCoin': 'USDT',
                        'productType': 'umcbl'
                    }

                    v1_query_string = '&'.join([f'{k}={v}' for k, v in v1_futures_params.items()])
                    v1_futures_path = f"{v1_futures_endpoint}?{v1_query_string}"

                    v1_pre_hash = timestamp + "GET" + v1_futures_path
                    v1_futures_signature = base64.b64encode(
                        hmac.new(secret_key.encode('utf-8'), v1_pre_hash.encode('utf-8'),
                                 hashlib.sha256).digest()).decode()

                    v1_futures_headers = {
                        'ACCESS-KEY': api_key,
                        'ACCESS-SIGN': v1_futures_signature,
                        'ACCESS-TIMESTAMP': timestamp,
                        'ACCESS-PASSPHRASE': passphrase,
                        'Content-Type': 'application/json'
                    }

                    v1_futures_url = f"{base_url}{v1_futures_path}"
                    logger.info(f"V1 Vadeli Açık Emirler isteği: {v1_futures_url}")

                    async with session.get(v1_futures_url, headers=v1_futures_headers) as v1_response:
                        v1_futures_data = await v1_response.json()
                        logger.info(f"V1 Vadeli Açık Emirler yanıtı: {v1_futures_data}")

                        if v1_futures_data.get('code') == '00000':
                            all_orders['futures'] = v1_futures_data.get('data', [])
                            logger.info(f"V1 API'den vadeli açık emir sayısı: {len(all_orders['futures'])}")
                        else:
                            logger.warning(f"Vadeli emirler alınamadı. Hata: {v1_futures_data.get('msg')}")

            # ========== VADELİ İŞLEMLER - POZİSYONLAR ==========
            # V2 API kullanarak vadeli pozisyonları alma
            positions_endpoint = "/api/v2/mix/position/all-position"
            positions_params = {
                'productType': 'USDT-FUTURES',
                'marginCoin': 'USDT'
            }

            query_string = '&'.join([f'{k}={v}' for k, v in positions_params.items()])
            positions_path = f"{positions_endpoint}?{query_string}"

            pre_hash = timestamp + "GET" + positions_path
            positions_signature = base64.b64encode(
                hmac.new(secret_key.encode('utf-8'), pre_hash.encode('utf-8'), hashlib.sha256).digest()).decode()

            positions_headers = {
                'ACCESS-KEY': api_key,
                'ACCESS-SIGN': positions_signature,
                'ACCESS-TIMESTAMP': timestamp,
                'ACCESS-PASSPHRASE': passphrase,
                'locale': 'en-US',
                'Content-Type': 'application/json'
            }

            positions_url = f"{base_url}{positions_path}"
            logger.info(f"Vadeli Pozisyonlar isteği: {positions_url}")

            async with session.get(positions_url, headers=positions_headers) as response:
                positions_data = await response.json()
                logger.info(f"Vadeli Pozisyonlar yanıtı: {positions_data}")

                if positions_data.get('code') == '00000':
                    all_orders['positions'] = positions_data.get('data', [])
                    logger.info(f"Vadeli pozisyon sayısı: {len(all_orders['positions'])}")
                    # Açık pozisyonları filtrele (sıfır olmayan pozisyonlar)
                    open_positions = [p for p in all_orders['positions'] if float(p.get('total', 0)) != 0]
                    logger.info(f"Açık pozisyon sayısı: {len(open_positions)}")
                    all_orders['open_positions'] = open_positions
                else:
                    # V1 API'yi deneme
                    v1_positions_endpoint = "/api/mix/v1/position/allPosition"
                    v1_positions_params = {
                        'productType': 'umcbl',
                        'marginCoin': 'USDT'
                    }

                    v1_query_string = '&'.join([f'{k}={v}' for k, v in v1_positions_params.items()])
                    v1_positions_path = f"{v1_positions_endpoint}?{v1_query_string}"

                    v1_pre_hash = timestamp + "GET" + v1_positions_path
                    v1_positions_signature = base64.b64encode(
                        hmac.new(secret_key.encode('utf-8'), v1_pre_hash.encode('utf-8'),
                                 hashlib.sha256).digest()).decode()

                    v1_positions_headers = {
                        'ACCESS-KEY': api_key,
                        'ACCESS-SIGN': v1_positions_signature,
                        'ACCESS-TIMESTAMP': timestamp,
                        'ACCESS-PASSPHRASE': passphrase,
                        'Content-Type': 'application/json'
                    }

                    v1_positions_url = f"{base_url}{v1_positions_path}"
                    logger.info(f"V1 Vadeli Pozisyonlar isteği: {v1_positions_url}")

                    async with session.get(v1_positions_url, headers=v1_positions_headers) as v1_response:
                        v1_positions_data = await v1_response.json()
                        logger.info(f"V1 Vadeli Pozisyonlar yanıtı: {v1_positions_data}")

                        if v1_positions_data.get('code') == '00000':
                            all_orders['positions'] = v1_positions_data.get('data', [])
                            logger.info(f"V1 API'den vadeli pozisyon sayısı: {len(all_orders['positions'])}")
                            # Açık pozisyonları filtrele
                            open_positions = [p for p in all_orders['positions'] if float(p.get('holdSide', '0')) != 0]
                            logger.info(f"Açık pozisyon sayısı: {len(open_positions)}")
                            all_orders['open_positions'] = open_positions
                        else:
                            logger.warning(f"Vadeli pozisyonlar alınamadı. Hata: {v1_positions_data.get('msg')}")

            return all_orders

    except Exception as e:
        logger.error(f"İşlem sırasında hata oluştu: {str(e)}", exc_info=True)
        return {'spot': [], 'futures': [], 'positions': [], 'open_positions': []}


# Geçici storage için bir sözlük oluşturalım
order_cancel_callbacks = {}  # Bu değişkeni global olarak tanımlayın


async def show_open_orders(api_key, secret_key, passphrase, user_id):
    try:
        orders = await get_bitget_open_orders(api_key, secret_key, passphrase, user_id)
        if not orders:
            return "Açık emirler alınırken bir hata oluştu.", None

        message = "📊 AÇIK EMİRLER\n\n"
        keyboard = []

        # Spot emirleri göster
        if orders.get('spot'):
            message += "🔵 SPOT EMİRLER:\n"
            for i, order in enumerate(orders['spot']):
                order_id = order.get('orderId')
                symbol = order.get('symbol')

                # Emrin bilgilerini ekle
                message += f"<pre>Sembol: {symbol}\n"
                message += f"Tip: {order.get('side')}\n"
                message += f"Fiyat: {order.get('priceAvg', order.get('price', ''))}\n"
                message += f"Miktar: {order.get('size')}\n</pre>"

                # Her emrin altına kendi iptal butonunu ekle
                callback_data = f"bitget_cancel_spot_{order_id}_{symbol}"
                keyboard.append([
                    InlineKeyboardButton(f"#{i + 1} {symbol} Emrini İptal Et", callback_data=callback_data)
                ])

        # Vadeli emirleri göster
        if orders.get('futures'):
            message += "\n🔴 VADELİ EMİRLER:\n"
            start_index = len(orders.get('spot', [])) + 1
            for i, order in enumerate(orders['futures']):
                order_id = order.get('orderId')
                symbol = order.get('symbol')

                # Emrin bilgilerini ekle
                message += f"<pre>Sembol: {symbol}\n"
                message += f"Tip: {order.get('side')}\n"
                message += f"Fiyat: {order.get('price')}\n"
                message += f"Miktar: {order.get('size')}\n</pre>"

                # Her emrin altına kendi iptal butonunu ekle
                callback_data = f"bitget_cancel_futures_{order_id}_{symbol}"
                keyboard.append([
                    InlineKeyboardButton(f"#{start_index + i} {symbol} Emrini İptal Et", callback_data=callback_data)
                ])

        # Ana menü ve Bitget menüsü butonlarını en sona ekle
        keyboard.append([
            InlineKeyboardButton("Bitget Menüsü", callback_data='bitget_menu'),
            InlineKeyboardButton("Ana Menü", callback_data='main_menu')
        ])

        if not orders.get('spot') and not orders.get('futures'):
            message = "Açık emir bulunamadı."

        # Klavye markup'ı oluştur
        reply_markup = InlineKeyboardMarkup(keyboard)
        return message, reply_markup

    except Exception as e:
        logger.error(f"Açık emirleri gösterme hatası: {str(e)}", exc_info=True)
        return "Açık emirleri gösterirken bir hata oluştu.", None


async def button_callback(update, context):
    query = update.callback_query
    user_id = update.effective_user.id

    await query.answer()  # Butona tıklandığını bildir

    if query.data.startswith('c_'):  # cancel işlemi
        ref_id = query.data.split('_')[1]  # referans ID'yi al

        # Kullanıcının callback verilerini kontrol et
        if user_id not in order_cancel_callbacks or ref_id not in order_cancel_callbacks[user_id]:
            await query.edit_message_text("Bu işlem artık geçerli değil. Lütfen emirleri tekrar listeleyin.")
            return

        # Referans ID'den emir detaylarını al
        order_info = order_cancel_callbacks[user_id][ref_id]
        order_type = order_info['type']  # spot veya futures
        order_id = order_info['order_id']
        symbol = order_info['symbol']

        # API bilgilerini al - exchange parametresi ekliyorum
        api_info = await get_api_key(user_id, 'bitget')  # 'bitget' parametresi eklendi

        if not api_info:
            await query.edit_message_text(
                "API bilgileriniz bulunamadı. Lütfen /ayarla komutu ile API bilgilerinizi tanımlayın.")
            return

        # API bilgilerini ayrıştır
        api_key = api_info['api_key']
        secret_key = api_info['secret_key']
        passphrase = api_info['passphrase']

        # Emri iptal et
        success, message = await cancel_bitget_order(api_key, secret_key, passphrase, order_type, order_id, symbol)

        if success:
            # Başarılı olursa, emri iptal edildi mesajını göster
            await query.edit_message_text(f"{message}\n\nGüncel emirler yükleniyor...")

            # Güncel emirleri yükle
            new_message, reply_markup = await show_open_orders(api_key, secret_key, passphrase, user_id)

            # Mesajı güncelle
            if reply_markup:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=new_message,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
            else:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=new_message,
                    parse_mode='HTML'
                )
        else:
            # Başarısız olursa hata mesajını göster
            await query.edit_message_text(f"Emir iptal edilemedi: {message}")
    else:
        await query.edit_message_text("Geçersiz buton işlemi.")


async def cancel_bitget_order(api_key, secret_key, passphrase, order_type, order_id, symbol):
    try:
        # Timestamp hazırla
        timestamp = str(int(time.time() * 1000))

        if order_type == 'spot':
            # Spot emir iptali için
            url = "https://api.bitget.com/api/v2/spot/trade/cancel-order"

            # İstek gövdesi
            body = {
                "symbol": symbol,
                "orderId": order_id
            }

            # İmza için body'yi JSON formatına çevir
            body_str = json.dumps(body)

            # İmza mesajı: timestamp + HTTP metodu + endpoint + body
            message = timestamp + 'POST' + '/api/v2/spot/trade/cancel-order' + body_str

            # HMAC-SHA256 ile imzala
            signature = base64.b64encode(
                hmac.new(
                    secret_key.encode('utf-8'),
                    message.encode('utf-8'),
                    hashlib.sha256
                ).digest()
            ).decode('utf-8')

            # Headers oluştur
            headers = {
                'ACCESS-KEY': api_key,
                'ACCESS-SIGN': signature,
                'ACCESS-TIMESTAMP': timestamp,
                'ACCESS-PASSPHRASE': passphrase,
                'Content-Type': 'application/json'
            }

            logger.info(f"Spot emir iptali isteği: {url}, body: {body}")

            # POST isteği gönder
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=body) as response:
                    result = await response.json()
                    logger.info(f"Spot emir iptali yanıtı: {result}")

                    if result.get('code') == '00000':
                        return True, f"{symbol} emri başarıyla iptal edildi."
                    else:
                        return False, f"Hata: {result.get('msg', 'Bilinmeyen hata')}"

        elif order_type == 'futures':
            # V1 API'yi kullanarak vadeli emir iptali için
            url = "https://api.bitget.com/api/mix/v1/order/cancel-order"

            # Sembol düzeltmesi: V1 API "_UMCBL" sonekini bekliyor
            if "_UMCBL" not in symbol:
                symbol_v1 = f"{symbol}_UMCBL"
            else:
                symbol_v1 = symbol

            # İstek gövdesi - Vadeli işlemler için V1 API formatı
            body = {
                "symbol": symbol_v1,
                "orderId": order_id,
                "marginCoin": "USDT"  # Teminat para birimi
            }

            # İmza için body'yi JSON formatına çevir
            body_str = json.dumps(body)

            # İmza mesajı: timestamp + HTTP metodu + endpoint + body
            message = timestamp + 'POST' + '/api/mix/v1/order/cancel-order' + body_str

            # HMAC-SHA256 ile imzala
            signature = base64.b64encode(
                hmac.new(
                    secret_key.encode('utf-8'),
                    message.encode('utf-8'),
                    hashlib.sha256
                ).digest()
            ).decode('utf-8')

            # Headers oluştur
            headers = {
                'ACCESS-KEY': api_key,
                'ACCESS-SIGN': signature,
                'ACCESS-TIMESTAMP': timestamp,
                'ACCESS-PASSPHRASE': passphrase,
                'Content-Type': 'application/json'
            }

            logger.info(f"Vadeli emir iptali isteği (V1 API): {url}, body: {body}")

            # POST isteği gönder
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=body) as response:
                    result = await response.json()
                    logger.info(f"Vadeli emir iptali yanıtı: {result}")

                    if result.get('code') == '00000':
                        return True, f"{symbol} emri başarıyla iptal edildi."
                    else:
                        return False, f"Hata: {result.get('msg', 'Bilinmeyen hata')}"
        else:
            return False, "Geçersiz emir türü."

    except Exception as e:
        logger.error(f"Emir iptal hatası: {str(e)}", exc_info=True)
        return False, f"İşlem sırasında hata oluştu: {str(e)}"


def generate_bitget_headers(api_key, secret_key, passphrase, method, request_path, body=''):
    timestamp = str(int(time.time() * 1000))
    signature = get_bitget_signature(secret_key, timestamp, method, request_path, body)

    return {
        "ACCESS-KEY": api_key,
        "ACCESS-SIGN": signature,
        "ACCESS-TIMESTAMP": timestamp,
        "ACCESS-PASSPHRASE": passphrase,
        "Content-Type": "application/json"
    }


async def get_bitget_detailed_positions(api_key, secret_key, passphrase):
    try:
        base_url = "https://api.bitget.com"
        endpoint = "/api/mix/v1/position/allPosition"
        timestamp = str(int(time.time() * 1000))

        params = {"productType": "umcbl"}
        path = f"{endpoint}?{'&'.join([f'{k}={v}' for k, v in params.items()])}"

        signature = get_bitget_signature(secret_key, timestamp, "GET", path)

        headers = {
            "ACCESS-KEY": api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-PASSPHRASE": passphrase,
            "ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}{path}", headers=headers) as response:
                data = await response.json()
                logger.info(f"Position API Response: {data}")

                if data.get('code') == '00000':
                    positions = []
                    for position in data.get('data', []):
                        total = float(position.get('total', '0'))
                        if total != 0:
                            try:
                                symbol = position.get('symbol', '').replace('_UMCBL', '')
                                entry_price = float(position.get('averageOpenPrice', '0'))
                                current_price = float(position.get('marketPrice', '0'))
                                unrealized_pnl = float(position.get('unrealizedPL', '0'))
                                position_value = entry_price * float(total)
                                pnl_percentage = (unrealized_pnl / position_value) * 100 if position_value != 0 else 0

                                positions.append({
                                    'symbol': symbol,
                                    'side': 'LONG' if position.get('holdSide', '').lower() == 'long' else 'SHORT',
                                    'quantity': str(total),
                                    'entry_price': f"{entry_price:.4f}",
                                    'current_price': f"{current_price:.4f}",
                                    'unrealized_pnl': f"{unrealized_pnl:.2f}",
                                    'pnl_percentage': f"{pnl_percentage:.2f}",
                                    'margin_mode': 'Çapraz' if position.get('marginMode') == 'crossed' else 'İzole',
                                    'leverage': position.get('leverage', ''),
                                    'liquidation_price': f"{float(position.get('liquidationPrice', 0)):.4f}",
                                    'margin': position.get('margin', '0'),
                                    'callback_data': f'close_bitget_position_'
                                                     f'{symbol}_{str(total).replace(".", "_")}'
                                })
                            except (ValueError, TypeError) as e:
                                logger.error(f"Pozisyon verisi işlenirken hata: {str(e)}")
                                continue

                    return positions
                else:
                    logger.error(f"Position API Error: {data.get('msg', 'Unknown error')}")
                    return []

    except Exception as e:
        logger.error(f"Pozisyon alımında hata: {str(e)}")
        return []


async def close_bitget_position(api_key, secret_key, passphrase, symbol, amount):
    try:
        # Sembolü ayarla
        symbol_with_suffix = f"{symbol}_UMCBL"

        # Emir parametrelerini hazırla
        order_params = {
            "symbol": symbol_with_suffix,
            "marginCoin": "USDT",
            "orderType": "market",
            "size": str(amount),
            "productType": "umcbl"
        }

        # Önce mevcut pozisyonu kontrol et
        base_url = "https://api.bitget.com"
        position_endpoint = "/api/mix/v1/position/singlePosition"
        position_params = {
            "symbol": symbol_with_suffix,
            "marginCoin": "USDT"
        }

        timestamp = str(int(time.time() * 1000))
        position_signature = get_bitget_signature(secret_key, timestamp, "GET",
                                                  f"{position_endpoint}?symbol={symbol_with_suffix}&marginCoin=USDT",
                                                  "")

        headers = {
            "ACCESS-KEY": api_key,
            "ACCESS-SIGN": position_signature,
            "ACCESS-PASSPHRASE": passphrase,
            "ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            # Pozisyon bilgisini al
            position_url = f"{base_url}{position_endpoint}?symbol={symbol_with_suffix}&marginCoin=USDT"
            async with session.get(position_url, headers=headers) as response:
                position_data = await response.json()
                logger.info(f"Position data: {position_data}")

                if position_data.get('code') != '00000':
                    return f"Pozisyon bilgisi alınamadı: {position_data.get('msg')}"

                positions = position_data.get('data', [])
                if not positions or len(positions) == 0:
                    return "Pozisyon bulunamadı"

                # İlk pozisyonu al
                position = positions[0]

                # Pozisyon yönüne göre kapatma tarafını belirle
                hold_side = position.get('holdSide', '').lower()
                order_params["side"] = "close_long" if hold_side == "long" else "close_short"

                logger.info(f"Closing position with params: {order_params}")

                # Yeni timestamp ve imza oluştur
                timestamp = str(int(time.time() * 1000))
                order_endpoint = "/api/mix/v1/order/placeOrder"
                body_str = json.dumps(order_params)
                order_signature = get_bitget_signature(secret_key, timestamp, "POST", order_endpoint, body_str)

                headers.update({
                    "ACCESS-SIGN": order_signature,
                    "ACCESS-TIMESTAMP": timestamp
                })

                # Kapatma emrini gönder
                async with session.post(f"{base_url}{order_endpoint}", headers=headers,
                                        son=order_params) as response:

                    result = await response.json()
                    logger.info(f"Close position result: {result}")

                    if result.get('code') == '00000':
                        return f"<b>{symbol} pozisyonu başarıyla kapatıldı!</b>"
                    else:
                        return f"Hata: {result.get('msg', 'Bilinmeyen hata')}"

    except Exception as e:
        logger.error(f"Pozisyon kapatma hatası: {str(e)}")
        return f"İşlem hatası: {str(e)}"


async def get_bitget_positions(api_key, secret_key, passphrase):
    """Bitget pozisyonlarını getiren yardımcı fonksiyon"""
    endpoint = "/api/mix/v1/position/allPosition"
    timestamp = str(int(time.time() * 1000))

    signature = get_bitget_signature(secret_key, timestamp, "GET", endpoint, "")

    headers = {
        "ACCESS-KEY": api_key,
        "ACCESS-SIGN": signature,
        "ACCESS-PASSPHRASE": passphrase,
        "ACCESS-TIMESTAMP": timestamp
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://api.bitget.com{endpoint}", headers=headers) as response:
            result = await response.json()
            if result.get('code') == '00000':
                return result.get('data', [])
            return []


async def convert_bitget_position(api_key, secret_key, passphrase, symbol, amount, target_side):
    try:
        base_url = "https://api.bitget.com"
        endpoint = "/api/v2/mix/order/place-close-position"
        method = "POST"

        timestamp = str(int(time.time() * 1000))

        body = {
            "symbol": symbol,
            "marginCoin": "USDT",
            "amount": str(amount),
            "side": "close" + target_side.capitalize()
        }

        body_json = json.dumps(body)
        signature = get_bitget_signature(secret_key, timestamp, method, endpoint, body_json)

        headers = {
            "ACCESS-KEY": api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-PASSPHRASE": passphrase,
            "ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(base_url + endpoint, headers=headers, data=body_json) as response:
                data = await response.json()

                if data['code'] == '00000':
                    return f"<b>{symbol} Pozisyonu Tersine Çevrildi!</b>"
                else:
                    return f"Hata: {data['msg']}"
    except Exception as e:
        logger.error(f"Pozisyon tersine çevirme hatası: {str(e)}")
        return f"Hata: {str(e)}"


async def save_bitget_balance_to_db(user_id, balances):
    try:
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

        # Hesap türlerine göre bakiyeleri ayır
        spot_bakiye = 0.0
        vadeli_bakiye = 0.0
        marjin_bakiye = 0.0
        fonlama_bakiye = 0.0
        kazan_bakiye = 0.0
        bot_bakiye = 0.0

        # Bakiyeleri hesapla
        if isinstance(balances, list):
            for account in balances:
                if isinstance(account, dict):
                    account_type = account.get('accountType')
                    usdt_balance = float(account.get('usdtBalance', 0))

                    if account_type == 'spot':
                        spot_bakiye = usdt_balance
                    elif account_type == 'futures':
                        vadeli_bakiye = usdt_balance
                    elif account_type == 'margin':
                        marjin_bakiye = usdt_balance
                    elif account_type == 'funding':
                        fonlama_bakiye = usdt_balance
                    elif account_type == 'earn':
                        kazan_bakiye = usdt_balance
                    elif account_type == 'bots':
                        bot_bakiye = usdt_balance

        # Toplam bakiyeyi hesapla
        toplam_bakiye = spot_bakiye + vadeli_bakiye + marjin_bakiye + fonlama_bakiye + kazan_bakiye + bot_bakiye

        # İlk toplamı kontrol et
        query_initial = "SELECT ilk_toplam FROM borsa_info WHERE user_id = ? AND exchange = 'bitget'"
        initial_total = db_operation(query_initial, (user_id,), operation='select', fetch=True)

        if not initial_total:
            # İlk kayıt
            query = """
            INSERT INTO borsa_info (
                user_id, username, exchange, spot, vadeli, marjin, 
                fonlama, kazan, bot, guncel_toplam, ilk_toplam, son_güncelleme
            ) VALUES (?, ?, 'bitget', ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """
            params = (user_id, username, spot_bakiye, vadeli_bakiye, marjin_bakiye,
                      fonlama_bakiye, kazan_bakiye, bot_bakiye, toplam_bakiye, toplam_bakiye)
            db_operation(query, params, operation='insert')
            logger.info(f"Yeni kayıt oluşturuldu: {params}")
        else:
            # Güncelleme
            ilk_toplam = float(initial_total[0][0])
            kar_zarar = toplam_bakiye - ilk_toplam
            kar_zarar_str = "KAR" if kar_zarar > 0 else "ZARAR"

            query = """
            UPDATE borsa_info SET 
                username = ?, spot = ?, vadeli = ?, marjin = ?, 
                fonlama = ?, kazan = ?, bot = ?, guncel_toplam = ?,
                kar_zarar_toplam = ?, kar_zarar = ?, son_güncelleme = CURRENT_TIMESTAMP
            WHERE user_id = ? AND exchange = 'bitget'
            """
            params = (username, spot_bakiye, vadeli_bakiye, marjin_bakiye,
                      fonlama_bakiye, kazan_bakiye, bot_bakiye, toplam_bakiye,
                      kar_zarar, kar_zarar_str, user_id)

            db_operation(query, params, operation='update')
            logger.info(f"Kayıt güncellendi: {params}")

        logger.info(f"Kullanıcı {user_id} ({username}) için Bitget bakiyesi kaydedildi: {toplam_bakiye}")
        return True

    except Exception as e:
        logger.error(f"Bitget bakiye kaydetme hatası: {str(e)}")
        logger.error(f"Hata detayı: {str(e.__traceback__.tb_next)}")
        return False


async def update_bitget_user_balances():
    try:
        # Tüm kullanıcıların API bilgilerini al
        query = "SELECT user_id, api_key, secret_key, passphrase FROM api_key WHERE exchange = 'bitget'"
        users = db_operation(query, operation='select', fetch=True)

        if not users:
            logger.warning("Bitget API bilgisi olan kullanıcı bulunamadı.")
            return

        for user in users:
            user_id = None  # Değişkeni döngü dışında tanımla
            try:
                user_id, api_key, secret_key, passphrase = user

                # API bilgilerini kontrol et
                if not all([api_key, secret_key, passphrase]):
                    logger.warning(f"Kullanıcı {user_id} için eksik API bilgileri.")
                    continue

                # Bitget API'den bakiye bilgilerini al
                base_url = "https://api.bitget.com"
                endpoint = "/api/v2/account/all-account-balance"
                method = "GET"

                timestamp = str(int(time.time() * 1000))
                signature = get_bitget_signature(secret_key, timestamp, method, endpoint)

                headers = {
                    "ACCESS-KEY": api_key,
                    "ACCESS-SIGN": signature,
                    "ACCESS-PASSPHRASE": passphrase,
                    "ACCESS-TIMESTAMP": timestamp,
                    "locale": "en-US",
                    "Content-Type": "application/json"
                }

                async with aiohttp.ClientSession() as session:
                    async with session.get(base_url + endpoint, headers=headers) as response:
                        data = await response.json()

                        if data['code'] == '00000':
                            balances = data['data']
                            # Bakiyeleri veritabanına kaydet
                            await save_bitget_balance_to_db(user_id, balances)
                            logger.info(f"Kullanıcı {user_id} için bakiye güncellendi.")
                        else:
                            logger.error(f"Kullanıcı {user_id} için API yanıt hatası: {data}")
                            continue

            except Exception as user_error:
                error_msg = (f"Kullanıcı "
                             f"{user_id if user_id else 'bilinmeyen'} için güncelleme hatası: {str(user_error)}")
                logger.error(error_msg)
                continue

    except Exception as e:
        logger.error(f"Tüm kullanıcı bakiyeleri güncellenirken hata: {str(e)}")


pass
