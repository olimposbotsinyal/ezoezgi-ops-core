# OKX için yardımcı fonksiyonlar
import uuid
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from data.olimpos_data import db_operation
from logger_config import setup_logging
from config.constants import State
import hmac
import hashlib
import base64
import datetime
import json
import aiohttp
import asyncio

# Özel bir logger oluşturun
logger = setup_logging('okx_api_ayarlari_logları')


async def safe_okx_api_call(func, *args, **kwargs):
    """
    OKX API çağrıları için güvenli yeniden deneme mekanizması
    """
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


async def run_okx_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ayarlar menüsünü çalıştır"""
    return await okx_ayarlari_menu(update, context)


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


async def okx_ayarlari_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    """OKX ayarları menüsünü göster"""
    _ = context  # Bu satır uyarıyı engeller

    logger.info("OKX_Api_Ayarlari Menüsü Gösteriliyor.")
    query = update.callback_query
    if query:
        await query.answer()

    keyboard = [
        [InlineKeyboardButton("Bakiye Kontrol", callback_data="okx_balance")],
        [InlineKeyboardButton("İşlem Geçmişi", callback_data="okx_trade_history")],
        [InlineKeyboardButton("Açık Emirler", callback_data="okx_open_orders")],
        [InlineKeyboardButton("Aktif Pozisyonlar", callback_data="okx_positions")],
        [InlineKeyboardButton("Ana Menü", callback_data="main_menu")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    if query:
        await query.edit_message_text("OKX API İşlemleri", reply_markup=reply_markup)
    else:
        await update.message.reply_text("OKX API İşlemleri", reply_markup=reply_markup)

    logger.info("OKX menüsü gösterildi.")
    return State.OKX_MENU


async def handle_okx_actions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    """OKX API işlemlerini ele alır"""
    # İlk olarak query değişkenini fonksiyon başında tanımlayalım
    query = None

    try:
        query = update.callback_query
        await query.answer()

        full_action = query.data
        logger.info(f"handle_okx_actions çağrıldı. Action: {full_action}")

        user_id = query.from_user.id
        logger.info(f"Kullanıcı ID: {user_id}, İşlem: {full_action}")

        # Temel menü navigasyon kontrolleri
        if full_action == "select_exchange_okx":
            return await okx_ayarlari_menu(update, context)
        elif full_action == "okx_menu":
            return await okx_ayarlari_menu(update, context)
        elif full_action == "main_menu":
            logger.info("Ana menüye dönüş talep edildi.")
            from Olimpos_Cripto_Bot import show_main_menu
            return await show_main_menu(update, context)

        # Ana aksiyon türünü belirle
        action = full_action.split('_', 1)[-1]
        logger.info(f"İşlenecek action: {action}")

        # API bilgilerini al
        api_info = await get_api_key(user_id, 'okx')
        logger.info(f"API bilgileri alındı: {api_info is not None}")

        if not api_info:
            logger.warning(f"Kullanıcı {user_id} için API bilgisi bulunamadı.")
            await query.edit_message_text("Henüz kayıtlı API hesabınız bulunmamaktadır.")
            return State.MAIN_MENU

        api_key = api_info["api_key"]
        secret_key = api_info["secret_key"]
        passphrase = api_info["passphrase"]

        # OKXClient oluştur
        okx_client = OKXClient(api_key, secret_key, passphrase)

        if action == "balance":
            balance_message, reply_markup = await okx_client.get_okx_balance()
            await query.edit_message_text(balance_message, reply_markup=reply_markup, parse_mode='HTML')

        elif action == "detailed_balance":
            detailed_balance, reply_markup = await okx_client.get_okx_detailed_balance()
            await query.edit_message_text(detailed_balance, reply_markup=reply_markup, parse_mode='HTML')

        elif action == "trade_history":
            trade_history, reply_markup = await get_okx_trade_history(api_key, secret_key, passphrase)
            await query.edit_message_text(trade_history, reply_markup=reply_markup, parse_mode='HTML')

        elif action == "open_orders":
            logger.info(f"Kullanıcı {user_id} için açık emirler talep edildi.")
            message = await show_okx_open_orders(user_id)

            keyboard = [
                [InlineKeyboardButton("OKX Menüsü", callback_data='okx_menu')],
                [InlineKeyboardButton("Ana Menü", callback_data='main_menu')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(
                message,
                reply_markup=reply_markup,
                parse_mode='HTML'
            )

        elif action == "positions":
            logger.info(f"Kullanıcı {user_id} için açık pozisyonlar talep edildi.")
            positions = await get_okx_detailed_positions(api_key, secret_key, passphrase)

            if positions:
                positions_message = "<b>OKX Açık Pozisyonlar:</b>\n\n"
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
                    InlineKeyboardButton("OKX Menüsü", callback_data='okx_menu')
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
                    InlineKeyboardButton("OKX Menüsü", callback_data='okx_menu')
                ]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(
                    "Açık pozisyonunuz bulunmamaktadır.",
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )

        elif full_action.startswith("close_okx_position_"):
            try:
                parts = full_action.split("_")
                symbol = parts[3]  # BTCUSDT
                position_amount = float(parts[4].replace("_", "."))  # 0.01

                api_info = await get_api_key(user_id, 'okx')
                if not api_info:
                    await query.edit_message_text("API bilgileri bulunamadı.")
                    return State.MAIN_MENU

                # Pozisyonu kapat
                result = await close_okx_position(
                    api_info['api_key'],
                    api_info['secret_key'],
                    api_info['passphrase'],
                    symbol,
                    position_amount
                )

                keyboard = [
                    [InlineKeyboardButton("Pozisyonları Yenile", callback_data='okx_positions')],
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
                logger.error(f"Pozisyon kapatma işleminde hata: {str(e)}", exc_info=True)
                await query.edit_message_text(f"İşlem sırasında hata oluştu: {str(e)}")

        # OKX menüsüne dön
        return State.OKX_MENU

    except aiohttp.ClientConnectionError as conn_error:
        logger.error(f"OKX API bağlantı hatası: {conn_error}", exc_info=True)
        if query:  # Null kontrolü ekle
            await query.edit_message_text(
                "OKX API'ye bağlanırken bir hata oluştu. Lütfen internet bağlantınızı kontrol edin."
            )
        return State.MAIN_MENU

    except aiohttp.ClientResponseError as resp_error:
        logger.error(f"OKX API yanıt hatası: {resp_error}", exc_info=True)
        if query:  # Null kontrolü ekle
            await query.edit_message_text(
                f"OKX API yanıt hatası: {resp_error.status} - {resp_error.message}"
            )
        return State.MAIN_MENU

    except json.JSONDecodeError as json_error:
        logger.error(f"API yanıtı JSON ayrıştırma hatası: {json_error}", exc_info=True)
        if query:  # Null kontrolü ekle
            await query.edit_message_text(
                "OKX API yanıtı işlenemedi. Geçersiz JSON formatı."
            )
        return State.MAIN_MENU

    except Exception as e:
        error_id = uuid.uuid4()
        logger.error(f"OKX işlemleri sırasında beklenmeyen hata ID[{error_id}]: {str(e)}", exc_info=True)
        if query:  # Null kontrolü ekle
            await query.edit_message_text(
                f"İşlem sırasında bir hata oluştu (Ref: {error_id}). Lütfen daha sonra tekrar deneyin."
            )
        return State.MAIN_MENU


class OKXClient:
    """
    OKX API istekleri için yardımcı sınıf
    """

    def __init__(self, api_key, secret_key, passphrase):
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.base_url = "https://www.okx.com"

    async def _generate_signature(self, timestamp, method, request_path, body=''):
        """OKX API imza oluşturma"""
        if body:
            message = f"{timestamp}{method}{request_path}{body}"
        else:
            message = f"{timestamp}{method}{request_path}"

        signature = base64.b64encode(
            hmac.new(
                self.secret_key.encode('utf-8'),
                message.encode('utf-8'),
                hashlib.sha256
            ).digest()
        ).decode('utf-8')

        return signature

    async def _make_request(self, method, endpoint, params=None, data=None):
        """API isteği gönderme"""
        timestamp = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

        # URL parametreleri hazırlama
        url = f"{self.base_url}{endpoint}"
        if params:
            query_string = '&'.join([f'{k}={v}' for k, v in params.items()])
            url = f"{url}?{query_string}"
            path = f"{endpoint}?{query_string}"
        else:
            path = endpoint

        # İstek gövdesi hazırlama
        body_str = ''
        if data:
            body_str = json.dumps(data)

        # İmza oluşturma
        signature = await self._generate_signature(timestamp, method, path, body_str)

        # Headers hazırlama
        headers = {
            'OK-ACCESS-KEY': self.api_key,
            'OK-ACCESS-SIGN': signature,
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': self.passphrase,
            'Content-Type': 'application/json'
        }

        # İsteği gönderme
        try:
            async with aiohttp.ClientSession() as session:
                if method == 'GET':
                    async with session.get(url, headers=headers) as response:
                        result = await response.json()
                        return result
                elif method == 'POST':
                    async with session.post(url, headers=headers, json=data) as response:
                        result = await response.json()
                        return result
        except Exception as e:
            logger.error(f"API isteği sırasında hata: {str(e)}", exc_info=True)
            return None

    async def get_account_balance(self):
        """Hesap bakiyesini çeker"""
        try:
            balance_endpoints = [
                {'type': 'SPOT', 'endpoint': '/api/v5/account/balance'},
                {'type': 'SWAP', 'endpoint': '/api/v5/account/balance'}
            ]

            all_balances = []
            for account in balance_endpoints:
                params = {'type': account['type']} if account['type'] != 'SPOT' else {}
                balance_result = await self._make_request('GET', account['endpoint'], params=params)

                logger.info(f"{account['type']} bakiye çağrısı sonucu: {balance_result}")

                if balance_result and balance_result.get('code') == '0':
                    for data in balance_result.get('data', []):
                        # Her hesap için type bilgisini ekleyelim
                        for detail in data.get('details', []):
                            detail['type'] = account['type']
                        all_balances.append(data)

            logger.info(f"Tüm bakiye verileri: {all_balances}")
            return all_balances
        except Exception as e:
            logger.error(f"OKX bakiye çekme hatası: {str(e)}", exc_info=True)
            return None

    async def get_okx_balance_data(self):
        """Bakiye verilerini formatlı şekilde döndürür"""
        try:
            balance_data = await self.get_account_balance()

            if not balance_data:
                logger.warning("Bakiye verisi boş geldi")
                return None

            # Ana bakiyeyi doğrudan al
            total_usdt_value = float(balance_data[0].get('totalEq', 0))

            detailed_balances = {
                'spot': {
                    'total': float(balance_data[0].get('totalEq', 0)) if balance_data[0].get('details', []) and
                                                                         balance_data[0]['details'][0].get(
                                                                             'type') == 'SPOT' else 0,
                    'available': float(balance_data[0].get('details', [{}])[0].get('availBal', 0)) if balance_data[
                                                                                                          0].get(
                        'details', []) and balance_data[0]['details'][0].get('type') == 'SPOT' else 0,
                    'frozen': float(balance_data[0].get('details', [{}])[0].get('frozenBal', 0)) if balance_data[0].get(
                        'details', []) and balance_data[0]['details'][0].get('type') == 'SPOT' else 0,
                    'coins': {}
                },
                'futures': {
                    'total': float(balance_data[1].get('totalEq', 0)) if len(balance_data) > 1 and balance_data[1].get(
                        'details', []) and balance_data[1]['details'][0].get('type') == 'SWAP' else 0,
                    'available': float(balance_data[1].get('details', [{}])[0].get('availBal', 0)) if len(
                        balance_data) > 1 and balance_data[1].get('details', []) and balance_data[1]['details'][0].get(
                        'type') == 'SWAP' else 0,
                    'frozen': float(balance_data[1].get('details', [{}])[0].get('frozenBal', 0)) if len(
                        balance_data) > 1 and balance_data[1].get('details', []) and balance_data[1]['details'][0].get(
                        'type') == 'SWAP' else 0,
                    'coins': {}
                }
            }

            account_type_map = {
                'SPOT': 'spot',
                'SWAP': 'futures'
            }

            for account in balance_data:
                details = account.get('details', [])

                for detail in details:
                    coin = detail.get('ccy', '')
                    available = float(detail.get('availBal', 0))
                    frozen = float(detail.get('frozenBal', 0))
                    total = float(detail.get('eq', 0))
                    account_type_str = detail.get('type', '')

                    # Hesap türünü belirle
                    account_type = account_type_map.get(account_type_str, 'spot')

                    # Sadece bakiyesi olan coinleri ekle
                    if total > 0 and account_type in detailed_balances:
                        # Coin bazında detayları güncelle
                        if coin not in detailed_balances[account_type]['coins']:
                            detailed_balances[account_type]['coins'][coin] = {
                                'total': total,
                                'available': available,
                                'frozen': frozen
                            }
                        else:
                            # Eğer coin zaten varsa, değerlerini topla
                            detailed_balances[account_type]['coins'][coin]['total'] += total
                            detailed_balances[account_type]['coins'][coin]['available'] += available
                            detailed_balances[account_type]['coins'][coin]['frozen'] += frozen

            return {
                'total_usdt_value': total_usdt_value,
                'balances': detailed_balances
            }

        except Exception as e:
            logger.error(f"OKX bakiye verisi hazırlanırken hata: {str(e)}", exc_info=True)
            return None

    async def get_okx_balance(self):
        """Formatlı OKX bakiye mesajı oluşturur"""
        try:
            balance_data = await self.get_okx_balance_data()

            if not balance_data:
                keyboard = [
                    [InlineKeyboardButton("OKX Menüsü", callback_data='okx_menu')],
                    [InlineKeyboardButton("Ana Menü", callback_data='main_menu')]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                return "OKX bakiye bilgisi alınamadı.", reply_markup

            total_usdt_value = balance_data['total_usdt_value']
            balances = balance_data['balances']

            balance_message = "<b>OKX Bakiye Özeti:</b>\n" + "---------------------------------\n"
            balance_message += f"<b>Toplam Varlıklar:</b> {total_usdt_value:.4f} USDT\n\n"

            # Hesap türleri için özet
            account_types = [
                ('spot', 'Spot Hesap'),
                ('futures', 'Vadeli Hesap')
            ]

            for account_type, account_name in account_types:
                balance_info = balances[account_type]

                balance_message += f"<b>{account_name}:</b>\n"
                balance_message += f"<pre>"
                balance_message += f"Toplam: {balance_info['total']:.4f} USDT\n"
                balance_message += f"Kullanılabilir: {balance_info['available']:.4f} USDT\n"
                balance_message += f"Dondurulmuş: {balance_info['frozen']:.4f} USDT\n"
                balance_message += f"</pre>\n"

                # En büyük 3 coin
                top_coins = sorted(
                    balance_info['coins'].items(),
                    key=lambda x: x[1]['total'],
                    reverse=True
                )[:3]

                for coin, coin_data in top_coins:
                    balance_message += f"<pre>"
                    balance_message += f"{coin}: {coin_data['total']:.4f}\n"
                    balance_message += f"  Kullanılabilir: {coin_data['available']:.4f}\n"
                    balance_message += f"  Dondurulmuş: {coin_data['frozen']:.4f}\n"
                    balance_message += f"</pre>"

                balance_message += "---------------------------------\n"

            keyboard = [
                [InlineKeyboardButton("Detaylı Bakiye", callback_data='okx_detailed_balance')],
                [InlineKeyboardButton("OKX Menüsü", callback_data='okx_menu')],
                [InlineKeyboardButton("Ana Menü", callback_data='main_menu')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            return balance_message, reply_markup

        except Exception as e:
            logger.error(f"OKX bakiyesi alınırken hata: {str(e)}", exc_info=True)
            keyboard = [
                [InlineKeyboardButton("OKX Menüsü", callback_data='okx_menu')],
                [InlineKeyboardButton("Ana Menü", callback_data='main_menu')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            return f"<b>Bakiye alınırken hata oluştu:</b>\n{str(e)}", reply_markup

    async def get_okx_detailed_balance(self):
        """OKX detaylı bakiye bilgilerini gösterir"""
        try:
            balance_data = await self.get_okx_balance_data()

            if not balance_data:
                keyboard = [
                    [InlineKeyboardButton("OKX Menüsü", callback_data='okx_menu')],
                    [InlineKeyboardButton("Ana Menü", callback_data='main_menu')]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                return "OKX bakiye bilgisi alınamadı.", reply_markup

            total_usdt_value = balance_data['total_usdt_value']
            balances = balance_data['balances']

            detailed_message = "<b>OKX Detaylı Bakiye:</b>\n"
            detailed_message += f"<b>Toplam Varlıklar:</b> {total_usdt_value:.4f} USDT\n\n"

            account_types = [
                ('spot', 'Spot Hesap'),
                ('futures', 'Vadeli Hesap')
            ]

            for account_type, account_name in account_types:
                balance_info = balances[account_type]

                detailed_message += f"<b>{account_name}:</b>\n"
                detailed_message += f"<pre>"
                detailed_message += f"Toplam: {balance_info['total']:.4f} USDT\n"
                detailed_message += f"Kullanılabilir: {balance_info['available']:.4f} USDT\n"
                detailed_message += f"Dondurulmuş: {balance_info['frozen']:.4f} USDT\n"
                detailed_message += f"</pre>\n"

                # Tüm coinlerin detayları
                detailed_message += f"<b>{account_name} Coin Detayları:</b>\n"
                sorted_coins = sorted(
                    balance_info['coins'].items(),
                    key=lambda x: x[1]['total'],
                    reverse=True
                )

                for coin, coin_data in sorted_coins:
                    detailed_message += f"<pre>"
                    detailed_message += f"{coin}:\n"
                    detailed_message += f"  Toplam: {coin_data['total']:.6f}\n"
                    detailed_message += f"  Kullanılabilir: {coin_data['available']:.6f}\n"
                    detailed_message += f"  Dondurulmuş: {coin_data['frozen']:.6f}\n"
                    detailed_message += f"</pre>"

                detailed_message += "---------------------------------\n"

            keyboard = [
                [InlineKeyboardButton("Özet Bakiye", callback_data='okx_balance')],
                [InlineKeyboardButton("OKX Menüsü", callback_data='okx_menu')],
                [InlineKeyboardButton("Ana Menü", callback_data='main_menu')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            return detailed_message, reply_markup

        except Exception as e:
            logger.error(f"Detaylı bakiye alınırken hata: {str(e)}", exc_info=True)
            keyboard = [
                [InlineKeyboardButton("OKX Menüsü", callback_data='okx_menu')],
                [InlineKeyboardButton("Ana Menü", callback_data='main_menu')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            return f"<b>Detaylı bakiye alınırken hata oluştu:</b>\n{str(e)}", reply_markup

    async def get_okx_open_positions(self):
        """OKX açık pozisyonlarını çeker"""
        endpoint = '/api/v5/account/positions'
        params = {'instType': 'SWAP'}

        result = await self._make_request('GET', endpoint, params=params)
        return result


async def get_okx_trade_history(api_key, secret_key, passphrase, limit=20):
    """OKX işlem geçmişini çeker"""
    try:
        timestamp = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

        # SPOT işlem geçmişi
        spot_endpoint = '/api/v5/trade/fills'
        spot_params = {'limit': str(limit), 'instType': 'SPOT'}

        # SPOT imza oluştur
        spot_path = f"{spot_endpoint}?{'&'.join([f'{k}={v}' for k, v in spot_params.items()])}"
        spot_message = f"{timestamp}GET{spot_path}"
        spot_signature = base64.b64encode(
            hmac.new(
                secret_key.encode('utf-8'),
                spot_message.encode('utf-8'),
                hashlib.sha256
            ).digest()
        ).decode('utf-8')

        # FUTURES işlem geçmişi
        futures_endpoint = '/api/v5/trade/fills'
        futures_params = {'limit': str(limit), 'instType': 'SWAP'}

        # FUTURES imza oluştur
        futures_path = f"{futures_endpoint}?{'&'.join([f'{k}={v}' for k, v in futures_params.items()])}"
        futures_message = f"{timestamp}GET{futures_path}"
        futures_signature = base64.b64encode(
            hmac.new(
                secret_key.encode('utf-8'),
                futures_message.encode('utf-8'),
                hashlib.sha256
            ).digest()
        ).decode('utf-8')

        # Ortak headers
        headers = {
            'OK-ACCESS-KEY': api_key,
            'OK-ACCESS-SIGN': spot_signature,
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': passphrase,
            'Content-Type': 'application/json'
        }

        base_url = "https://www.okx.com"

        async with aiohttp.ClientSession() as session:
            # SPOT işlem geçmişi al
            spot_trades = []
            headers['OK-ACCESS-SIGN'] = spot_signature
            async with session.get(f"{base_url}{spot_path}", headers=headers) as response:
                spot_data = await response.json()
                logger.info(f"SPOT Trade History Response: {spot_data}")
                if spot_data.get('code') == '0':
                    spot_trades = spot_data.get('data', [])

            # FUTURES işlem geçmişi al
            futures_trades = []
            headers['OK-ACCESS-SIGN'] = futures_signature
            async with session.get(f"{base_url}{futures_path}", headers=headers) as response:
                futures_data = await response.json()
                logger.info(f"FUTURES Trade History Response: {futures_data}")
                if futures_data.get('code') == '0':
                    futures_trades = futures_data.get('data', [])

        # Mesajları oluştur
        spot_message = "<b>SPOT İşlem Geçmişi:</b>\n\n"
        if spot_trades:
            for trade in spot_trades[:10]:  # İlk 10 işlemi göster
                spot_message += (
                    f"<pre>"
                    f"Sembol: {trade.get('instId', 'N/A')}\n"
                    f"İşlem Tipi: {'Alış' if trade.get('side') == 'buy' else 'Satış'}\n"
                    f"Fiyat: {trade.get('px', '0')} USDT\n"
                    f"Miktar: {trade.get('sz', '0')}\n"
                    f"Zaman: "
                    f"{datetime.datetime.fromtimestamp(int(trade.get('ts', '0')) / 1000).strftime('%Y-%m-%d %H:%M:%S')}"
                    f"\n"
                    f"</pre>"
                    f"------------------------\n"
                )
        else:
            spot_message += "Spot işlem geçmişi bulunamadı.\n\n"

        futures_message = "\n<b>VADELİ İşlem Geçmişi:</b>\n\n"
        if futures_trades:
            for trade in futures_trades[:10]:  # İlk 10 işlemi göster
                futures_message += (
                    f"<pre>"
                    f"Sembol: {trade.get('instId', 'N/A')}\n"
                    f"İşlem Tipi: {'Alış' if trade.get('side') == 'buy' else 'Satış'}\n"
                    f"Fiyat: {trade.get('px', '0')} USDT\n"
                    f"Miktar: {trade.get('sz', '0')}\n"
                    f"Zaman: "
                    f"{datetime.datetime.fromtimestamp(int(trade.get('ts', '0')) / 1000).strftime('%Y-%m-%d %H:%M:%S')}"
                    f"\n"
                    f"</pre>"
                    f"------------------------\n"
                )
        else:
            futures_message += "Vadeli işlem geçmişi bulunamadı.\n"

        message = spot_message + futures_message

        keyboard = [
            [InlineKeyboardButton("OKX Menüsü", callback_data='okx_menu')],
            [InlineKeyboardButton("Ana Menü", callback_data='main_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        return message, reply_markup

    except Exception as e:
        logger.error(f"İşlem geçmişi alınırken hata: {str(e)}", exc_info=True)
        keyboard = [
            [InlineKeyboardButton("OKX Menüsü", callback_data='okx_menu')],
            [InlineKeyboardButton("Ana Menü", callback_data='main_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        return f"<b>İşlem geçmişi alınırken hata oluştu:</b>\n{str(e)}", reply_markup


async def get_okx_open_orders(api_key, secret_key, passphrase):
    """OKX açık emirlerini çeker"""
    try:
        timestamp = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

        # Spot açık emirler
        spot_endpoint = '/api/v5/trade/orders-pending'
        spot_params = {'instType': 'SPOT'}

        # Spot imza oluştur
        spot_path = f"{spot_endpoint}?{'&'.join([f'{k}={v}' for k, v in spot_params.items()])}"
        spot_message = f"{timestamp}GET{spot_path}"
        spot_signature = base64.b64encode(
            hmac.new(
                secret_key.encode('utf-8'),
                spot_message.encode('utf-8'),
                hashlib.sha256
            ).digest()
        ).decode('utf-8')

        # Vadeli açık emirler
        futures_endpoint = '/api/v5/trade/orders-pending'
        futures_params = {'instType': 'SWAP'}

        # Vadeli imza oluştur
        futures_path = f"{futures_endpoint}?{'&'.join([f'{k}={v}' for k, v in futures_params.items()])}"
        futures_message = f"{timestamp}GET{futures_path}"
        futures_signature = base64.b64encode(
            hmac.new(
                secret_key.encode('utf-8'),
                futures_message.encode('utf-8'),
                hashlib.sha256
            ).digest()
        ).decode('utf-8')

        base_url = "https://www.okx.com"

        async with aiohttp.ClientSession() as session:
            # Spot emirleri al
            spot_orders = []
            headers = {
                'OK-ACCESS-KEY': api_key,
                'OK-ACCESS-SIGN': spot_signature,
                'OK-ACCESS-TIMESTAMP': timestamp,
                'OK-ACCESS-PASSPHRASE': passphrase,
                'Content-Type': 'application/json'
            }

            async with session.get(f"{base_url}{spot_path}", headers=headers) as response:
                spot_data = await response.json()
                logger.info(f"SPOT Open Orders Response: {spot_data}")
                if spot_data.get('code') == '0':
                    spot_orders = spot_data.get('data', [])

            # Vadeli emirleri al
            futures_orders = []
            headers['OK-ACCESS-SIGN'] = futures_signature

            async with session.get(f"{base_url}{futures_path}", headers=headers) as response:
                futures_data = await response.json()
                logger.info(f"FUTURES Open Orders Response: {futures_data}")
                if futures_data.get('code') == '0':
                    futures_orders = futures_data.get('data', [])

            # Tüm emirleri bir araya getir
            all_orders = {
                'spot': spot_orders,
                'futures': futures_orders
            }

            return all_orders

    except Exception as e:
        logger.error(f"Açık emirler alınırken hata: {str(e)}", exc_info=True)
        return None


async def show_okx_open_orders(user_id):
    """OKX açık emirlerini gösterir"""
    try:
        api_info = await get_api_key(user_id, 'okx')
        if not api_info:
            return "API bilgileri bulunamadı."

        orders = await get_okx_open_orders(
            api_info['api_key'],
            api_info['secret_key'],
            api_info['passphrase']
        )

        if not orders:
            return "Açık emirler alınırken bir hata oluştu."

        message = "📊 AÇIK EMİRLER\n\n"

        # Spot emirleri göster
        if orders.get('spot'):
            message += "🔵 SPOT EMİRLER:\n"
            for order in orders['spot']:
                message += f"<pre>"
                message += f"Sembol: {order.get('instId')}\n"
                message += f"Tip: {'Alış' if order.get('side') == 'buy' else 'Satış'}\n"
                message += f"Fiyat: {order.get('px')}\n"
                message += f"Miktar: {order.get('sz')}\n"
                message += f"Emir Tipi: {order.get('ordType')}\n"
                message += f"</pre>---------------\n"

        # Vadeli emirleri göster
        if orders.get('futures'):
            message += "\n🔴 VADELİ EMİRLER:\n"
            for order in orders['futures']:
                message += f"<pre>"
                message += f"Sembol: {order.get('instId')}\n"
                message += f"Tip: {'Alış' if order.get('side') == 'buy' else 'Satış'}\n"
                message += f"Fiyat: {order.get('px')}\n"
                message += f"Miktar: {order.get('sz')}\n"
                message += f"Emir Tipi: {order.get('ordType')}\n"
                message += f"</pre>---------------\n"

        if not orders.get('spot') and not orders.get('futures'):
            message = "Açık emir bulunamadı."

        return message

    except Exception as e:
        logger.error(f"Açık emirleri gösterme hatası: {str(e)}", exc_info=True)
        return "Açık emirleri gösterirken bir hata oluştu."


async def get_okx_detailed_positions(api_key, secret_key, passphrase):
    """OKX detaylı pozisyonları çeker"""
    try:
        timestamp = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
        endpoint = '/api/v5/account/positions'
        params = {'instType': 'SWAP'}

        path = f"{endpoint}?{'&'.join([f'{k}={v}' for k, v in params.items()])}"
        message = f"{timestamp}GET{path}"
        signature = base64.b64encode(
            hmac.new(
                secret_key.encode('utf-8'),
                message.encode('utf-8'),
                hashlib.sha256
            ).digest()
        ).decode('utf-8')

        headers = {
            'OK-ACCESS-KEY': api_key,
            'OK-ACCESS-SIGN': signature,
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': passphrase,
            'Content-Type': 'application/json'
        }

        base_url = "https://www.okx.com"

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}{path}", headers=headers) as response:
                data = await response.json()
                logger.info(f"Positions API Response: {data}")

                if data.get('code') == '0':
                    positions = []
                    for position in data.get('data', []):
                        pos_size = float(position.get('pos', '0'))
                        if pos_size != 0:  # Sadece aktif pozisyonları ekle
                            try:
                                symbol = position.get('instId', '')
                                entry_price = float(position.get('avgPx', '0'))
                                current_price = float(position.get('markPx', '0'))
                                unrealized_pnl = float(position.get('upl', '0'))

                                # PnL yüzdesini hesapla
                                position_value = abs(pos_size) * entry_price
                                pnl_percentage = (unrealized_pnl / position_value) * 100 if position_value != 0 else 0

                                positions.append({
                                    'symbol': symbol,
                                    'side': 'LONG' if position.get('posSide') == 'long' else 'SHORT',
                                    'quantity': str(abs(pos_size)),
                                    'entry_price': f"{entry_price:.4f}",
                                    'current_price': f"{current_price:.4f}",
                                    'unrealized_pnl': f"{unrealized_pnl:.2f}",
                                    'pnl_percentage': f"{pnl_percentage:.2f}",
                                    'margin_mode': position.get('mgnMode', ''),
                                    'leverage': position.get('lever', ''),
                                    'liquidation_price': position.get('liqPx', '0'),
                                    'margin': position.get('imr', '0'),
                                    'callback_data':
                                        f'close_okx_position_{symbol}_{str(abs(pos_size)).replace(".", "_")}'
                                })
                            except (ValueError, TypeError) as e:
                                logger.error(f"Pozisyon verisi işlenirken hata: {str(e)}", exc_info=True)
                                continue

                    return positions
                else:
                    logger.error(f"Position API Error: {data.get('msg', 'Unknown error')}")
                    return []

    except Exception as e:
        logger.error(f"Pozisyon alımında hata: {str(e)}", exc_info=True)
        return []


async def close_okx_position(api_key, secret_key, passphrase, symbol, amount):
    """OKX pozisyon kapatma"""
    try:
        timestamp = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
        base_url = "https://www.okx.com"

        # Önce pozisyon bilgilerini al
        positions_endpoint = '/api/v5/account/positions'
        positions_params = {'instType': 'SWAP', 'instId': symbol}
        positions_path = f"{positions_endpoint}?{'&'.join([f'{k}={v}' for k, v in positions_params.items()])}"

        positions_message = f"{timestamp}GET{positions_path}"
        positions_signature = base64.b64encode(
            hmac.new(
                secret_key.encode('utf-8'),
                positions_message.encode('utf-8'),
                hashlib.sha256
            ).digest()
        ).decode('utf-8')

        positions_headers = {
            'OK-ACCESS-KEY': api_key,
            'OK-ACCESS-SIGN': positions_signature,
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': passphrase,
            'Content-Type': 'application/json'
        }

        async with aiohttp.ClientSession() as session:
            # Pozisyon bilgisini al
            async with session.get(f"{base_url}{positions_path}", headers=positions_headers) as positions_response:
                positions_data = await positions_response.json()
                logger.info(f"Positions data: {positions_data}")

                if positions_data.get('code') != '0':
                    return f"Pozisyon bilgisi alınamadı: {positions_data.get('msg', 'Bilinmeyen hata')}"

                positions = positions_data.get('data', [])
                if not positions:
                    return "Pozisyon bulunamadı"

                # İlk pozisyonu al
                position = positions[0]
                pos_side = position.get('posSide')

                # Pozisyon yönüne göre işlem tarafını belirle
                side = "sell" if pos_side == "long" else "buy"

                # Pozisyon kapatma emri parametreleri
                order_params = {
                    "instId": symbol,
                    "tdMode": "cross",  # veya "isolated"
                    "side": side,
                    "ordType": "market",
                    "sz": str(amount),
                    "posSide": pos_side,
                }

                # Yeni timestamp al
                timestamp = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
                order_endpoint = "/api/v5/trade/order"
                body_str = json.dumps(order_params)

                order_message = f"{timestamp}POST{order_endpoint}{body_str}"
                order_signature = base64.b64encode(
                    hmac.new(
                        secret_key.encode('utf-8'),
                        order_message.encode('utf-8'),
                        hashlib.sha256
                    ).digest()
                ).decode('utf-8')

                order_headers = {
                    'OK-ACCESS-KEY': api_key,
                    'OK-ACCESS-SIGN': order_signature,
                    'OK-ACCESS-TIMESTAMP': timestamp,
                    'OK-ACCESS-PASSPHRASE': passphrase,
                    'Content-Type': 'application/json'
                }

                # Pozisyon kapatma emrini gönder
                async with session.post(f"{base_url}{order_endpoint}",
                                        headers=order_headers,
                                        json=order_params) as order_response:
                    result = await order_response.json()
                    logger.info(f"Close position result: {result}")

                    if result.get('code') == '0':
                        return f"<b>{symbol} pozisyonu başarıyla kapatıldı!</b>"
                    else:
                        return f"Hata: {result.get('msg', 'Bilinmeyen hata')}"

    except Exception as e:
        logger.error(f"Pozisyon kapatma hatası: {str(e)}", exc_info=True)
        return f"İşlem hatası: {str(e)}"


async def update_okx_user_balances():
    """
    Tüm kullanıcıların OKX bakiyelerini günceller
    """
    try:
        # Veritabanından OKX kullanıcılarını çek
        query = """
        SELECT 
            user_id, 
            api_key, 
            secret_key, 
            passphrase 
        FROM api_key 
        WHERE 
            exchange = 'okx' 
            AND api_key IS NOT NULL 
            AND secret_key IS NOT NULL
        """

        users = db_operation(query, operation='select', fetch=True)

        if not users:
            logger.warning("OKX API bilgisi olan kullanıcı bulunamadı.")
            return

        logger.info(f"Toplam {len(users)} kullanıcı için OKX bakiye güncellemesi yapılacak")

        for user in users:
            user_id = None  # Değişkeni döngü dışında tanımla
            try:
                user_id, api_key, secret_key, passphrase = user

                # API bilgilerini kontrol et
                if not all([api_key, secret_key, passphrase]):
                    logger.warning(f"Kullanıcı {user_id} için eksik API bilgileri.")
                    continue

                logger.info(f"Kullanıcı {user_id} için OKX bakiye güncelleme başlatıldı")

                # OKX Client oluştur
                okx_client = OKXClient(api_key, secret_key, passphrase)

                # Bakiye bilgilerini çek
                balance_info = await okx_client.get_okx_balance_data()

                if not balance_info:
                    logger.warning(f"Kullanıcı {user_id} için bakiye bilgisi alınamadı")
                    continue

                # Bakiye bilgilerini kaydet
                result = await save_okx_balance_to_db(
                    user_id,
                    balance_info
                )

                if result:
                    logger.info(f"Kullanıcı {user_id} için OKX bakiyesi güncellendi.")
                else:
                    logger.warning(f"Kullanıcı {user_id} için bakiye güncellemesi başarısız.")

            except Exception as user_error:
                error_msg = (f"Kullanıcı "
                             f"{user_id if user_id else 'bilinmeyen'} için güncelleme hatası: {str(user_error)}")
                logger.error(error_msg, exc_info=True)
                continue

    except Exception as e:
        logger.error(f"Tüm kullanıcı bakiyeleri güncellenirken hata: {str(e)}", exc_info=True)


async def save_okx_balance_to_db(user_id, balances):
    """OKX bakiyesini veritabanına kaydeder"""
    try:
        # Değişkenleri varsayılan değerlerle başlat
        spot_bakiye = 0.0
        vadeli_bakiye = 0.0
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
        total_usdt_value = balances.get('total_usdt_value', 0)
        detailed_balances = balances.get('balances', {})

        # Bakiye detaylarını hesapla
        spot_balances = {}
        futures_balances = {}

        # Spot ve Vadeli bakiyeleri hesapla
        spot_bakiye = detailed_balances.get('spot', {}).get('total', 0)
        vadeli_bakiye = detailed_balances.get('futures', {}).get('total', 0)

        # Spot ve Vadeli coin detaylarını al
        for account_type in ['spot', 'futures']:
            coins = detailed_balances.get(account_type, {}).get('coins', {})
            balances_dict = spot_balances if account_type == 'spot' else futures_balances

            for coin, coin_data in coins.items():
                balances_dict[coin] = {
                    'available': coin_data.get('available', 0),
                    'frozen': coin_data.get('frozen', 0),
                    'total': coin_data.get('total', 0)
                }

        # Toplam bakiyeyi hesapla
        toplam_bakiye = total_usdt_value

        # Mevcut kaydı kontrol et
        query_check = """
        SELECT ilk_toplam, guncel_toplam 
        FROM borsa_info 
        WHERE user_id = ? AND exchange = 'okx'
        """
        check_result = db_operation(query_check, (user_id,), operation='select', fetch=True)

        # İlk toplam ve güncel toplam için varsayılan değerler
        ilk_toplam = toplam_bakiye
        guncel_toplam = toplam_bakiye

        # API bilgilerini tekrar al
        api_info = await get_api_key(user_id, 'okx')
        if not api_info:
            logger.error(f"Kullanıcı {user_id} için API bilgileri alınamadı")
            return False

        # Pozisyonları da al
        okx_client = OKXClient(
            api_info["api_key"],
            api_info["secret_key"],
            api_info["passphrase"]
        )
        positions_response = await okx_client.get_okx_open_positions()
        futures_positions = positions_response.get('data', []) if positions_response else []

        if not check_result or check_result[0][0] is None:
            # İlk kayıt - İlk toplam ve güncel toplam aynı olacak
            query = """
            INSERT INTO borsa_info (
                user_id, username, exchange, spot, vadeli, 
                guncel_toplam, ilk_toplam, spot_balances, 
                futures_balances, futures_positions, son_güncelleme
            ) VALUES (?, ?, 'okx', ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """
            params = (
                user_id, username, spot_bakiye, vadeli_bakiye,
                guncel_toplam, ilk_toplam,
                json.dumps(spot_balances), json.dumps(futures_balances),
                json.dumps(futures_positions)
            )
            db_operation(query, params, operation='insert')
            logger.info(f"Yeni OKX kayıt oluşturuldu: {params}")
        else:
            # Mevcut kayıt varsa
            mevcut_ilk_toplam = float(check_result[0][0] or 0)
            mevcut_guncel_toplam = float(check_result[0][1] or 0)

            # İlk toplam boş ise ilk toplama yaz
            if mevcut_ilk_toplam == 0:
                ilk_toplam = toplam_bakiye
            else:
                ilk_toplam = mevcut_ilk_toplam

            # Güncel toplam güncellenir
            # Eğer mevcut güncel toplam varsa ve yeni toplam bakiyeden farklıysa
            if mevcut_guncel_toplam != 0 and mevcut_guncel_toplam != toplam_bakiye:
                # Güncel toplam ve kar/zarar hesaplamasında mevcut güncel toplamı kullan
                guncel_toplam = mevcut_guncel_toplam
                logger.info(f"Mevcut güncel toplam kullanıldı: {guncel_toplam}")

            # Kar/Zarar hesaplama
            kar_zarar = toplam_bakiye - ilk_toplam
            kar_zarar_str = "KAR" if kar_zarar > 0 else "ZARAR"

            query = """
            UPDATE borsa_info SET 
                username = ?, spot = ?, vadeli = ?, 
                guncel_toplam = ?, ilk_toplam = ?, 
                kar_zarar_toplam = ?, kar_zarar = ?, 
                spot_balances = ?, futures_balances = ?, 
                futures_positions = ?, son_güncelleme = CURRENT_TIMESTAMP
            WHERE user_id = ? AND exchange = 'okx'
            """
            params = (
                username, spot_bakiye, vadeli_bakiye,
                toplam_bakiye, ilk_toplam,
                kar_zarar, kar_zarar_str,
                json.dumps(spot_balances), json.dumps(futures_balances),
                json.dumps(futures_positions), user_id
            )

            db_operation(query, params, operation='update')
            logger.info(f"OKX kayıt güncellendi: {params}")

        logger.info(f"Kullanıcı {user_id} ({username}) için OKX bakiyesi kaydedildi: {toplam_bakiye}")
        return True

    except Exception as e:
        logger.error(f"OKX bakiye kaydetme hatası: {str(e)}", exc_info=True)
        return False
