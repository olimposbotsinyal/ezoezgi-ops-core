from telegram.ext import ContextTypes
from data.olimpos_data import *
from logger_config import setup_logging
from config.constants import State
from urllib.parse import urlencode
import time
import hmac
import aiohttp
import hashlib
import json
import asyncio

# Özel bir logger oluşturun ve yapılandırın
logger = setup_logging('bitmart_api_ayarlari_logları')


def get_bitmart_signature(secret_key, timestamp, method, request_path, params=None):
    """
    BitMart API imzası oluşturur
    """
    try:
        # Parametreleri URL encode et (eğer varsa)
        query_string = ''
        if params:
            query_string = urlencode(params)

        # İmza mesajı oluşturma
        message_formats = [
            f"{timestamp}#{method.upper()}#{request_path}{('?' + query_string) if query_string else ''}",
            f"{timestamp}{method.upper()}{request_path}{query_string}",
            f"{method.upper()}{request_path}{timestamp}{query_string}"
        ]

        for message in message_formats:
            try:
                # HMAC-SHA256 ile imzalama
                signature = hmac.new(
                    secret_key.encode('utf-8'),
                    message.encode('utf-8'),
                    hashlib.sha256
                ).hexdigest()

                return signature
            except Exception as format_error:
                logger.warning(f"İmza oluşturma formatı başarısız: {str(format_error)}")

        raise ValueError("Hiçbir imza formatı çalışmadı")

    except Exception as e:
        logger.error(f"BitMart imza oluşturma hatası: {str(e)}")
        raise


def get_bitmart_v2_signature(secret_key, timestamp, method, endpoint, body_str=''):
    """
    BitMart V2 API için imzalama
    """
    message = timestamp + '#' + method + '#' + endpoint
    if body_str:
        message += '#' + body_str

    signature = hmac.new(secret_key.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()
    return signature


async def get_api_key(user_id, exchange):
    """
    Kullanıcının API bilgilerini veritabanından alır
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


async def run_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await bitmart_ayarlari_menu(update, context)


async def bitmart_ayarlari_menu(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> State:
    query = update.callback_query
    if query:
        await query.answer()

    keyboard = [
        [InlineKeyboardButton("Bakiye Kontrol", callback_data="bitmart_balance")],
        [InlineKeyboardButton("İşlem Geçmişi", callback_data="bitmart_trade_history")],
        [InlineKeyboardButton("Açık Emirler", callback_data="bitmart_open_orders")],
        [InlineKeyboardButton("Aktif Pozisyonlar", callback_data="bitmart_positions")],
        [InlineKeyboardButton("Ana Menü", callback_data="main_menu")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    if query:
        await query.edit_message_text("BitMart API İşlemleri", reply_markup=reply_markup)
    else:
        await update.message.reply_text("BitMart API İşlemleri", reply_markup=reply_markup)

    return State.BITMART_MENU


async def handle_bitmart_actions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    try:
        query = update.callback_query
        await query.answer()

        full_action = query.data
        logger.info(f"handle_bitmart_actions çağrıldı. Full Action: {full_action}")

        user_id = query.from_user.id
        logger.info(f"Kullanıcı ID: {user_id}, İşlem: {full_action}")

        # BitMart ile ilgili tüm actionları kontrol et
        if full_action.startswith("bitmart_"):
            # Action'ı ayıkla (bitmart_ önekini çıkar)
            action = full_action.split('_', 1)[-1]
            logger.info(f"Tespit edilen action: {action}")

            try:
                # API bilgilerini al
                api_info = await get_api_key(user_id, 'bitmart')
                logger.info(f"API bilgileri alındı: {api_info is not None}")

                if not api_info:
                    logger.warning(f"Kullanıcı {user_id} için API bilgisi bulunamadı.")
                    await query.edit_message_text("Henüz kayıtlı BitMart API hesabınız bulunmamaktadır.")
                    return State.MAIN_MENU

                api_key = api_info["api_key"]
                secret_key = api_info["secret_key"]

                # Farklı actionlar için işlemler
                if action == "balance":
                    logger.info("Balance action tetiklendi")
                    balance_message, reply_markup = await get_bitmart_balance(
                        api_key, secret_key, user_id
                    )

                    await query.edit_message_text(
                        balance_message,
                        reply_markup=reply_markup,
                        parse_mode='HTML'
                    )

                elif action == "trade_history":
                    logger.info("Trade History action tetiklendi")
                    trade_history, reply_markup = await get_bitmart_trade_history(
                        api_key, secret_key
                    )
                    await query.edit_message_text(
                        trade_history,
                        reply_markup=reply_markup,
                        parse_mode='HTML'
                    )

                elif action == "open_orders":
                    logger.info("Open Orders action tetiklendi")
                    message, reply_markup = await get_bitmart_open_orders(
                        api_key, secret_key, user_id
                    )
                    await query.edit_message_text(
                        message,
                        reply_markup=reply_markup,
                        parse_mode='HTML'
                    )

                elif action == "positions":
                    logger.info("Positions action tetiklendi")
                    positions_message, reply_markup = await get_bitmart_positions(
                        api_key, secret_key, user_id
                    )
                    await query.edit_message_text(
                        positions_message,
                        reply_markup=reply_markup,
                        parse_mode='HTML'
                    )

                elif action == "menu":
                    logger.info("BitMart Menüsü açılıyor")
                    return await bitmart_ayarlari_menu(update, context)

                return State.BITMART_MENU

            except Exception as action_error:
                logger.error(f"BitMart action işleme hatası: {str(action_error)}", exc_info=True)
                await query.edit_message_text(f"İşlem sırasında hata oluştu: {str(action_error)}")
                return State.MAIN_MENU

        # Ana menü ve diğer genel kontroller
        elif full_action == "main_menu":
            logger.info("Ana menüye dönüş talep edildi.")
            from Olimpos_Cripto_Bot import show_main_menu
            return await show_main_menu(update, context)

        # Menü kontrolleri
        elif full_action == "select_exchange_bitmart":
            logger.info("BitMart exchange menüsü açılıyor")
            return await bitmart_ayarlari_menu(update, context)

        # Tanınmayan action
        else:
            logger.warning(f"Tanınmayan callback_data: {full_action}")
            await query.edit_message_text("Geçersiz işlem. Lütfen tekrar deneyin.")
            return State.MAIN_MENU

    except Exception as main_error:
        logger.error(f"Handle BitMart Actions genel hata: {str(main_error)}", exc_info=True)
        logger.error(f"Hata Detayları: {main_error.__traceback__}")

        try:
            await query.edit_message_text("Bir hata oluştu. Lütfen daha sonra tekrar deneyin.")
        except Exception as edit_error:
            logger.error(f"Mesaj düzenleme hatası: {str(edit_error)}")

        return State.MAIN_MENU


async def get_bitmart_balance(api_key, secret_key, user_id=None):
    try:
        logger.info(f"get_bitmart_balance fonksiyonu başladı")
        logger.debug(f"Kullanıcı ID: {user_id}")
        logger.debug(f"API Key (kısmi): {api_key[:5]}...")

        # Endpoint listesi (güncel dokümantasyona göre)
        spot_base_url = "https://api-cloud.bitmart.com"
        futures_base_url = "https://api-cloud-v2.bitmart.com"

        endpoints = {
            'wallet': {
                'base_url': spot_base_url,
                'path': "/account/v1/wallet"
            },
            'futures': {
                'base_url': futures_base_url,
                'path': "/contract/private/assets-detail"
            }
        }

        # Detaylı bakiye hesaplama
        balance_details = {
            'spot': {'available': 0.0, 'frozen': 0.0, 'total': 0.0},
            'futures': {
                'available': 0.0,
                'frozen': 0.0,
                'total': 0.0,
                'margin_balance': 0.0,
                'position_value': 0.0
            },
            'total_usdt': 0.0,
            'balances': []
        }

        # Bakiyeleri geçici olarak saklamak için listeler
        spot_balances = []
        futures_balances = []

        # API hata mesajları
        api_error_messages = []

        # Endpoint kontrolü için döngü
        for account_type, endpoint_info in endpoints.items():
            try:
                base_url = endpoint_info['base_url']
                endpoint = endpoint_info['path']
                method = "GET"
                timestamp = str(int(time.time() * 1000))

                # İmzalama işlemi
                if account_type == 'futures':
                    # V2 API imzalama
                    message = timestamp + "#" + method + "#" + endpoint + "#"
                    signature = hmac.new(
                        secret_key.encode('utf-8'),
                        message.encode('utf-8'),
                        hashlib.sha256
                    ).hexdigest()
                else:
                    # V1 API imzalama
                    params_for_sign = timestamp + method + endpoint
                    signature = hmac.new(
                        secret_key.encode('utf-8'),
                        params_for_sign.encode('utf-8'),
                        hashlib.sha256
                    ).hexdigest()

                headers = {
                    "X-BM-KEY": api_key,
                    "X-BM-SIGN": signature,
                    "X-BM-TIMESTAMP": timestamp,
                    "Content-Type": "application/json",
                    "Accept": "application/json"
                }

                logger.info(f"{account_type.upper()} API çağrısı yapılıyor: {base_url}{endpoint}")

                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{base_url}{endpoint}", headers=headers) as response:
                        logger.info(f"{account_type.upper()} API yanıt durumu: {response.status}")

                        if response.status != 200:
                            logger.warning(f"{account_type.upper()} API çağrısı başarısız: {response.status}")
                            api_error_messages.append(
                                f"<pre>"
                                f"{'Hesap:':<8}{account_type.upper()}\n"
                                f"{'Durum:':<8}Bağlantı Hatası\n"
                                f"{'Kod:':<8}{response.status}\n"
                                f"</pre>"
                            )
                            continue

                        response_text = await response.text()
                        logger.debug(f"{account_type.upper()} API yanıt içeriği: {response_text}")

                        try:
                            data = await response.json()
                        except Exception as json_error:
                            logger.error(f"{account_type.upper()} JSON parse hatası: {str(json_error)}")
                            api_error_messages.append(
                                f"<pre>"
                                f"{'Hesap:':<8}{account_type.upper()}\n"
                                f"{'Durum:':<8}JSON Hatası\n"
                                f"{'Hata:':<8}{str(json_error)}\n"
                                f"</pre>"
                            )
                            continue

                        # Wallet hesabı işleme (Spot)
                        if account_type == 'wallet':
                            if data.get('code') != 1000:
                                logger.warning(f"WALLET Geçersiz yanıt kodu: {data.get('code')}")
                                api_error_messages.append(
                                    f"<pre>"
                                    f"{'Hesap:':<8}WALLET\n"
                                    f"{'Durum:':<8}API Hatası\n"
                                    f"{'Kod:':<8}{data.get('code')}\n"
                                    f"{'Mesaj:':<8}{data.get('message', 'Bilinmeyen hata')}\n"
                                    f"</pre>"
                                )
                                continue

                            balances = data.get('data', {}).get('wallet', [])
                            for account in balances:
                                coin = account.get('coin', '')
                                available = float(account.get('available', 0))
                                frozen = float(account.get('frozen', 0))
                                total = available + frozen

                                # Toplam hesaplamalara ekle
                                balance_details['spot']['available'] += available
                                balance_details['spot']['frozen'] += frozen
                                balance_details['spot']['total'] += total

                                if coin.upper() == 'USDT':
                                    balance_details['total_usdt'] += total

                                # Bakiyeleri toplarken listeye ekle (toplam > 0 olanlar)
                                if total > 0:
                                    spot_balances.append({
                                        'coin': coin,
                                        'available': available,
                                        'frozen': frozen,
                                        'total': total
                                    })

                                # Tüm bakiyeleri veritabanı için ekle
                                balance_details['balances'].append({
                                    'coin': coin,
                                    'available': available,
                                    'frozen': frozen,
                                    'total': total,
                                    'type': 'wallet'
                                })

                        # Futures hesabı işleme (V2 API formatına göre)
                        elif account_type == 'futures':
                            # BitMart V2 API'nin başarı kodu 1000'dir
                            if data.get('code') != 1000:
                                error_code = data.get('code')
                                error_msg = data.get('message', 'Bilinmeyen hata')
                                logger.warning(f"FUTURES Geçersiz yanıt kodu: {error_code}")

                                api_error_messages.append(
                                    f"<pre>"
                                    f"{'Hesap:':<8}FUTURES\n"
                                    f"{'Durum:':<8}API Hatası\n"
                                    f"{'Kod:':<8}{error_code}\n"
                                    f"{'Mesaj:':<8}{error_msg}\n"
                                    f"</pre>"
                                )
                                continue

                            # V2 API'nin yanıt formatı bir dizi içeriyor
                            futures_data_list = data.get('data', [])

                            if not futures_data_list:
                                logger.warning("FUTURES API yanıtında veri bulunamadı")
                                api_error_messages.append(
                                    f"<pre>"
                                    f"{'Hesap:':<8}FUTURES\n"
                                    f"{'Durum:':<8}Veri Yok\n"
                                    f"</pre>"
                                )
                                continue

                            # USDT ve diğer para birimleri için verileri işle
                            for currency_data in futures_data_list:
                                currency = currency_data.get('currency', '')

                                # Tüm değerleri float'a çeviriyoruz
                                available_balance = float(currency_data.get('available_balance', 0))
                                frozen_balance = float(currency_data.get('frozen_balance', 0))
                                position_deposit = float(currency_data.get('position_deposit', 0))
                                equity = float(currency_data.get('equity', 0))
                                unrealized = float(currency_data.get('unrealized', 0))

                                # Para birimini işle
                                if currency == 'USDT':
                                    # USDT için Futures bakiyelerini güncelle
                                    balance_details['futures']['available'] = available_balance
                                    balance_details['futures']['frozen'] = frozen_balance
                                    balance_details['futures']['total'] = equity
                                    balance_details['futures']['margin_balance'] = equity
                                    balance_details['futures']['position_value'] = position_deposit

                                    # Toplam USDT'ye ekle
                                    balance_details['total_usdt'] += equity

                                # Para birimini balances listesine ekle
                                balance_details['balances'].append({
                                    'coin': currency,
                                    'available': available_balance,
                                    'frozen': frozen_balance,
                                    'total': equity,
                                    'type': 'futures'
                                })

                                # Sadece sıfırdan büyük bakiyeleri vadeli listesine ekle
                                if equity > 0:
                                    futures_balances.append({
                                        'currency': currency,
                                        'available': available_balance,
                                        'frozen': frozen_balance,
                                        'equity': equity,
                                        'position_deposit': position_deposit,
                                        'unrealized': unrealized
                                    })

            except Exception as endpoint_error:
                logger.warning(f"{account_type.upper()} Endpoint hatası: {str(endpoint_error)}")
                api_error_messages.append(
                    f"<pre>"
                    f"{'Hesap:':<8}{account_type.upper()}\n"
                    f"{'Durum:':<8}İşlem Hatası\n"
                    f"{'Hata:':<8}{str(endpoint_error)}\n"
                    f"</pre>"
                )

        # ----- MESAJLARI BİRLEŞTİRME VE DÜZENLEME -----
        balance_message = "<b>BitMart Bakiye Detayları</b>\n\n"

        # Hata mesajlarını ekle
        if api_error_messages:
            balance_message += "<b>API Hataları:</b>\n"
            balance_message += "\n".join(api_error_messages)
            balance_message += "\n\n"

        # SPOT BAKİYELERİ
        balance_message += "<b>🔹 SPOT BAKİYELERİ</b>\n"
        if spot_balances:
            # En yüksek bakiyeden sırala
            spot_balances.sort(key=lambda x: x['total'], reverse=True)

            # Tablo başlık
            balance_message += "<pre>"
            balance_message += f"{'Para Birimi':<10}{'Kullanılabilir':<15}{'Dondurulmuş':<15}{'Toplam':<10}\n"
            balance_message += "-" * 50 + "\n"

            # Para birimleri
            for balance in spot_balances:
                balance_message += (f"{balance['coin']:<10}"
                                    f"{balance['available']:<15.8f}"
                                    f"{balance['frozen']:<15.8f}"
                                    f"{balance['total']:<10.8f}\n")

            balance_message += "</pre>\n\n"
        else:
            balance_message += "<i>Spot hesabınızda bakiye bulunamadı.</i>\n\n"

        # VADELİ BAKİYELERİ
        balance_message += "<b>🔹 VADELİ BAKİYELERİ</b>\n"
        if futures_balances:
            # En yüksek bakiyeden sırala
            futures_balances.sort(key=lambda x: x['equity'], reverse=True)

            # Tablo başlık
            balance_message += "<pre>"
            balance_message += f"{'Para':<8}{'Kullanılabilir':<15}{'Dondurulmuş':<15}{'Toplam':<10}{'P.Teminatı':<12}\n"
            balance_message += "-" * 60 + "\n"

            # Para birimleri
            for balance in futures_balances:
                balance_message += (f"{balance['currency']:<8}"
                                    f"{balance['available']:<15.8f}"
                                    f"{balance['frozen']:<15.8f}"
                                    f"{balance['equity']:<10.8f}"
                                    f"{balance['position_deposit']:<12.8f}\n")

            balance_message += "</pre>\n\n"
        else:
            balance_message += "<i>Vadeli hesabınızda bakiye bulunamadı.</i>\n\n"

        # ÖZET BAKİYE
        balance_message += "<b>🔹 ÖZET BAKİYE</b>\n"
        balance_message += "<pre>"
        balance_message += f"{'SPOT:':<12}Kullanılabilir: {balance_details['spot']['available']:.8f}\n"
        balance_message += f"{'':<12}Dondurulmuş: {balance_details['spot']['frozen']:.8f}\n"
        balance_message += f"{'':<12}Toplam: {balance_details['spot']['total']:.8f}\n\n"

        balance_message += f"{'VADELİ:':<12}Kullanılabilir: {balance_details['futures']['available']:.8f}\n"
        balance_message += f"{'':<12}Dondurulmuş: {balance_details['futures']['frozen']:.8f}\n"
        balance_message += f"{'':<12}Toplam: {balance_details['futures']['total']:.8f}\n\n"

        balance_message += f"{'TOPLAM USDT:':<12}{balance_details['total_usdt']:.8f}\n"
        balance_message += "</pre>"

        # Kullanıcı bakiyesini kaydetme
        if user_id:
            try:
                # Bakiye kaydetme fonksiyonunu güncelliyoruz
                if isinstance(user_id, int):
                    user_id = str(user_id)

                await save_bitmart_balance_to_db(user_id, balance_details)
            except Exception as save_error:
                logger.error(f"Bakiye kaydetme hatası: {str(save_error)}")

        # Klavye oluşturma
        keyboard = [
            [InlineKeyboardButton("BitMart Menüsü", callback_data='bitmart_menu')],
            [InlineKeyboardButton("Ana Menü", callback_data='main_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        return balance_message, reply_markup

    except Exception as e:
        logger.error(f"BitMart bakiye alma hatası", exc_info=True)
        keyboard = [
            [InlineKeyboardButton("BitMart Menüsü", callback_data='bitmart_menu')],
            [InlineKeyboardButton("Ana Menü", callback_data='main_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        return f"Hata: {str(e)}", reply_markup


async def get_bitmart_trade_history(api_key, secret_key):
    try:
        # Hem spot hem vadeli işlem geçmişini alıp birleştireceğiz
        spot_message, spot_data = await get_bitmart_spot_history(api_key, secret_key)
        futures_message, futures_data = await get_bitmart_futures_history(api_key, secret_key)

        # Hata kontrolü
        if "Hata:" in spot_message and "Hata:" in futures_message:
            return f"Spot ve Vadeli işlem geçmişi alınamadı.\n{spot_message}\n{futures_message}", None

        # Sonuçları birleştir
        combined_message = "<b>BitMart İşlem Geçmişi</b>\n\n"
        combined_message += "<b>== SPOT İŞLEMLER ==</b>\n"
        combined_message += spot_message
        combined_message += "\n<b>== VADELİ İŞLEMLER ==</b>\n"
        combined_message += futures_message

        keyboard = [
            [InlineKeyboardButton("BitMart Menüsü", callback_data='bitmart_menu')],
            [InlineKeyboardButton("Ana Menü", callback_data='main_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        return combined_message, reply_markup

    except Exception as e:
        logger.error(f"BitMart işlem geçmişi alma hatası: {str(e)}", exc_info=True)
        return f"Hata: {str(e)}", None


async def get_bitmart_spot_history(api_key, secret_key):
    try:
        spot_base_url = "https://api-cloud.bitmart.com"
        endpoint = "/spot/v1/trades"
        method = "GET"
        timestamp = str(int(time.time() * 1000))

        # Spot işlemleri için parametreler
        params = {
            "limit": 10,  # Son 10 işlemi göster
            "N": 10  # Bazı BitMart API'leri N parametresi kullanıyor
        }

        # Query string oluştur
        query_string = urlencode(params)
        full_endpoint = f"{endpoint}?{query_string}"

        signature = get_bitmart_signature(secret_key, timestamp, method, full_endpoint)

        headers = {
            "X-BM-KEY": api_key,
            "X-BM-SIGN": signature,
            "X-BM-TIMESTAMP": timestamp,
            "Content-Type": "application/json"
        }

        url = f"{spot_base_url}{full_endpoint}"
        logger.info(f"BitMart Spot Trade History API isteği: {url}")

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"BitMart Spot API Hatası: Status={response.status}, Response={error_text}")
                    return "İşlem geçmişi bulunamadı.", []

                data = await response.json()
                logger.debug(f"BitMart Spot API Yanıtı: {data}")

                if data.get('code') == 1000:
                    trades = data.get('data', [])

                    if not trades:
                        return "Spot işlem geçmişi bulunamadı.", []

                    trade_message = ""
                    for trade in trades[:10]:
                        symbol = trade.get('symbol', '')
                        side = "Alış" if trade.get('side', '').lower() == 'buy' else "Satış"
                        price = float(trade.get('price', 0))
                        qty = float(trade.get('size', 0)) if 'size' in trade else float(trade.get('amount', 0))
                        timestamp_ms = int(trade.get('timestamp', 0))
                        date_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp_ms / 1000))

                        trade_message += (
                            f"<pre>"
                            f"Sembol: {symbol}\n"
                            f"İşlem Tipi: {side}\n"
                            f"Fiyat: {price} USDT\n"
                            f"Miktar: {qty}\n"
                            f"Zaman: {date_str}\n"
                            f"</pre>"
                            f"------------------------\n"
                        )

                    return trade_message, trades
                else:
                    error_msg = data.get('message', 'Bilinmeyen hata')
                    logger.error(f"BitMart Spot API Yanıt Hatası: {error_msg}")
                    return "Spot işlem geçmişi bulunamadı.", []

    except Exception as e:
        logger.error(f"BitMart spot işlem geçmişi alma hatası: {str(e)}", exc_info=True)
        return f"Spot işlem geçmişi hatası: {str(e)}", []


async def get_bitmart_futures_history(api_key, secret_key):
    try:
        futures_base_url = "https://api-cloud-v2.bitmart.com"
        endpoint = "/contract/v2/order/history"  # V2 API'yi kullan
        method = "GET"
        timestamp = str(int(time.time() * 1000))

        # Vadeli işlemler için parametreler
        params = {
            "limit": 10,  # Son 10 işlemi göster
        }

        # Query string oluştur
        query_string = urlencode(params)
        full_endpoint = f"{endpoint}?{query_string}"

        signature = get_bitmart_signature(secret_key, timestamp, method, full_endpoint)

        headers = {
            "X-BM-KEY": api_key,
            "X-BM-SIGN": signature,
            "X-BM-TIMESTAMP": timestamp,
            "Content-Type": "application/json"
        }

        url = f"{futures_base_url}{full_endpoint}"
        logger.info(f"BitMart Futures Trade History API isteği: {url}")

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"BitMart Futures API Hatası: Status={response.status}, Response={error_text}")
                    return "Vadeli işlem geçmişi bulunamadı.", []

                data = await response.json()
                logger.debug(f"BitMart Futures API Yanıtı: {data}")

                if data.get('code') == 1000:
                    trades = data.get('data', {}).get('orders', [])

                    if not trades:
                        return "Vadeli işlem geçmişi bulunamadı.", []

                    trade_message = ""
                    for trade in trades[:10]:
                        symbol = trade.get('symbol', '')
                        # V2 API'de side değeri farklı olabilir
                        side = "Alış" if str(trade.get('side')) in ['1', 'buy', 'BUY'] else "Satış"
                        price = float(trade.get('price', 0))
                        qty = float(trade.get('size', 0)) if 'size' in trade else float(trade.get('quantity', 0))
                        timestamp_ms = int(trade.get('create_time', 0))
                        date_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp_ms / 1000))

                        trade_message += (
                            f"<pre>"
                            f"Sembol: {symbol}\n"
                            f"İşlem Tipi: {side}\n"
                            f"Fiyat: {price} USDT\n"
                            f"Miktar: {qty}\n"
                            f"Zaman: {date_str}\n"
                            f"</pre>"
                            f"------------------------\n"
                        )

                    return trade_message, trades
                else:
                    error_msg = data.get('message', 'Bilinmeyen hata')
                    logger.error(f"BitMart Futures API Yanıt Hatası: {error_msg}")
                    return "Vadeli işlem geçmişi bulunamadı.", []

    except Exception as e:
        logger.error(f"BitMart vadeli işlem geçmişi alma hatası: {str(e)}", exc_info=True)
        return f"Vadeli işlem geçmişi hatası: {str(e)}", []


async def get_bitmart_open_orders(api_key, secret_key, user_id):
    try:
        base_url = "https://api-cloud.bitmart.com"
        endpoint = "/contract/v1/order/open-orders"  # Güncel endpoint
        method = "GET"
        timestamp = str(int(time.time() * 1000))

        signature = get_bitmart_signature(secret_key, timestamp, method, endpoint)

        headers = {
            "X-BM-KEY": api_key,
            "X-BM-SIGN": signature,
            "X-BM-TIMESTAMP": timestamp,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}{endpoint}", headers=headers) as response:
                if response.status != 200:
                    return f"Hata: Açık emirler alınamadı (Durum: {response.status})", None

                data = await response.json()

                if data.get('code') == 1000:
                    open_orders = data.get('data', {}).get('orders', [])

                    orders_message = "<b>BitMart Açık Emirler:</b>\n\n"
                    keyboard = []

                    if not open_orders:
                        orders_message += "Açık emir bulunamadı.\n"
                    else:
                        for order in open_orders:
                            symbol = order.get('symbol', '')
                            side = "Alış" if order.get('side') == 1 else "Satış"
                            price = float(order.get('price', 0))
                            size = float(order.get('size', 0))
                            timestamp_ms = int(order.get('create_time', 0))
                            date_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp_ms / 1000))
                            order_id = order.get('order_id', '')

                            orders_message += (
                                f"<pre>"
                                f"Sembol: {symbol}\n"
                                f"Emir Tipi: {side}\n"
                                f"Fiyat: {price} USDT\n"
                                f"Miktar: {size}\n"
                                f"Zaman: {date_str}\n"
                                f"</pre>"
                                f"------------------------\n"
                            )

                            # Her emir için iptal butonu
                            keyboard.append([
                                InlineKeyboardButton(
                                    f"{symbol} {side} Emrini İptal Et",
                                    callback_data=f"bitmart_cancel_order_{order_id}"
                                )
                            ])

                    # Menü butonlarını ekle
                    keyboard.extend([
                        [InlineKeyboardButton("BitMart Menüsü", callback_data='bitmart_menu')],
                        [InlineKeyboardButton("Ana Menü", callback_data='main_menu')]
                    ])

                    reply_markup = InlineKeyboardMarkup(keyboard)
                    return orders_message, reply_markup
                else:
                    error_message = f"Hata: {data.get('message', 'Bilinmeyen hata')}"
                    logger.warning(f"BitMart açık emirler API hatası: {error_message}")
                    keyboard = [
                        [InlineKeyboardButton("BitMart Menüsü", callback_data='bitmart_menu')],
                        [InlineKeyboardButton("Ana Menü", callback_data='main_menu')]
                    ]
                    return error_message, InlineKeyboardMarkup(keyboard)

    except Exception as e:
        logger.error(f"BitMart açık emirleri alma hatası: {str(e)}", exc_info=True)
        keyboard = [
            [InlineKeyboardButton("BitMart Menüsü", callback_data='bitmart_menu')],
            [InlineKeyboardButton("Ana Menü", callback_data='main_menu')]
        ]
        return f"Hata: {str(e)}", InlineKeyboardMarkup(keyboard)


async def cancel_bitmart_order(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> State:
    """BitMart'ta açık emri iptal eder"""
    try:
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id
        callback_data = query.data

        # Callback data'dan emir ID'sini ayıkla
        order_id = callback_data.split('_')[-1]

        logger.info(f"BitMart emir iptali: Kullanıcı {user_id}, Emir ID {order_id}")

        # API bilgilerini al
        api_info = await get_api_key(user_id, 'bitmart')

        if not api_info:
            await query.edit_message_text("API bilgileriniz bulunamadı.")
            return State.MAIN_MENU

        api_key = api_info["api_key"]
        secret_key = api_info["secret_key"]

        # BitMart emir iptal isteği gönder
        base_url = "https://api-cloud.bitmart.com"
        endpoint = f"/contract/v1/order/cancel"
        method = "POST"
        timestamp = str(int(time.time() * 1000))

        # İstek gövdesi
        body = {
            "order_id": order_id
        }

        body_str = json.dumps(body)

        signature = get_bitmart_signature(secret_key, timestamp, method, endpoint, body_str)

        headers = {
            "X-BM-KEY": api_key,
            "X-BM-SIGN": signature,
            "X-BM-TIMESTAMP": timestamp,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(f"{base_url}{endpoint}", headers=headers, data=body_str) as response:
                if response.status != 200:
                    await query.edit_message_text(f"Emir iptal isteği başarısız oldu. Durum kodu: {response.status}")
                    return State.BITMART_MENU

                data = await response.json()

                if data.get('code') == 1000:
                    await query.edit_message_text("Emir başarıyla iptal edildi!")

                    # Emir listesini güncelle
                    await asyncio.sleep(1)  # API'nin güncellenmesi için kısa bir bekleme
                    orders_message, reply_markup = await get_bitmart_open_orders(api_key, secret_key, user_id)
                    await query.edit_message_text(orders_message, reply_markup=reply_markup, parse_mode='HTML')
                else:
                    await query.edit_message_text(
                        f"Emir iptal isteği başarısız: {data.get('message', 'Bilinmeyen hata')}")

        return State.BITMART_MENU

    except Exception as e:
        logger.error(f"BitMart emir iptal hatası: {str(e)}", exc_info=True)
        try:
            await query.edit_message_text(f"Emir iptal edilirken hata oluştu: {str(e)}")
        except:
            pass
        return State.BITMART_MENU


async def get_bitmart_positions(api_key, secret_key, user_id=None):
    try:
        base_url = "https://api-cloud.bitmart.com"
        endpoint = "/contract/v1/position"  # Güncel endpoint
        method = "GET"
        timestamp = str(int(time.time() * 1000))

        signature = get_bitmart_signature(secret_key, timestamp, method, endpoint)

        headers = {
            "X-BM-KEY": api_key,
            "X-BM-SIGN": signature,
            "X-BM-TIMESTAMP": timestamp,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}{endpoint}", headers=headers) as response:
                if response.status != 200:
                    logger.warning(f"Pozisyon API çağrısı başarısız: {response.status}")
                    positions_message = "<b>BitMart Pozisyonları:</b>\n\n"
                    positions_message += "Pozisyon bilgisi alınamadı. Durum kodu: " + str(response.status)
                else:
                    try:
                        data = await response.json()

                        if data.get('code') != 1000:
                            positions_message = "<b>BitMart Pozisyonları:</b>\n\n"
                            positions_message += f"API Hatası: {data.get('message', 'Bilinmeyen hata')}"
                            logger.warning(f"API Hatası: {data}")
                        else:
                            positions = data.get('data', {}).get('positions', [])

                            positions_message = "<b>BitMart Aktif Pozisyonlar:</b>\n\n"

                            if not positions:
                                positions_message += "Aktif pozisyonunuz bulunmamaktadır.\n"
                            else:
                                for position in positions:
                                    symbol = position.get('symbol', '')
                                    position_type = "LONG" if position.get('position_type') == 1 else "SHORT"
                                    size = float(position.get('hold_volume', 0))
                                    entry_price = float(position.get('open_price', 0))
                                    mark_price = float(position.get('mark_price', 0))
                                    unrealized_pnl = float(position.get('unrealized_pnl', 0))

                                    positions_message += (
                                            f"<pre>"
                                            f"{'Sembol:':<12}{symbol}\n"
                                            f"{'Pozisyon:':<12}{position_type}\n"
                                            f"{'Miktar:':<12}{size}\n"
                                            f"{'Giriş Fiyatı:':<12}{entry_price}\n"
                                            f"{'Mark Fiyatı:':<12}{mark_price}\n"
                                            f"{'Kar/Zarar:':<12}{unrealized_pnl}\n"
                                            f"</pre>"
                                            f"-" * 30 + "\n"
                                    )

                    except Exception as json_error:
                        logger.error(f"Pozisyon JSON parse hatası: {str(json_error)}")
                        positions_message = "<b>BitMart Pozisyonları:</b>\n\n"
                        positions_message += f"Veri işleme hatası: {str(json_error)}"

        keyboard = [
            [InlineKeyboardButton("BitMart Menüsü", callback_data='bitmart_menu')],
            [InlineKeyboardButton("Ana Menü", callback_data='main_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        return positions_message, reply_markup

    except Exception as e:
        logger.error(f"BitMart pozisyonları alma hatası: {str(e)}")
        return f"Hata: {str(e)}", None


async def save_bitmart_balance_to_db(user_id, balance_details):
    try:
        # Değişkenleri varsayılan değerlerle başlat
        spot_available = 0.0
        spot_total = 0.0
        futures_available = 0.0
        futures_total = 0.0
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

        # BitMart için bakiye hesaplama
        if isinstance(balance_details, dict):
            # Spot bakiye hesaplama - kullanılabilir ve toplam
            spot_balances = balance_details.get('spot', {})
            spot_available = float(spot_balances.get('available', 0))
            spot_total = float(spot_balances.get('total', 0))

            # Futures bakiye hesaplama - kullanılabilir ve toplam
            futures_balances = balance_details.get('futures', {})
            futures_available = float(futures_balances.get('available', 0))
            futures_total = float(futures_balances.get('total', 0))

            # Toplam bakiye (USDT cinsinden)
            toplam_bakiye = float(balance_details.get('total_usdt', 0))

            logger.info(
                f"Spot: Kullanılabilir={spot_available:.4f}, Toplam={spot_total:.4f} | "
                f"Futures: Kullanılabilir={futures_available:.4f}, Toplam={futures_total:.4f} | "
                f"Toplam USDT={toplam_bakiye:.4f}"
            )

        # Mevcut kaydı kontrol et
        query_check = """
        SELECT ilk_toplam
        FROM borsa_info 
        WHERE user_id = ? AND exchange = 'bitmart'
        """
        check_result = db_operation(query_check, (user_id,), operation='select', fetch=True)

        # İlk toplam ve güncel toplam için varsayılan değerler
        ilk_toplam = toplam_bakiye
        guncel_toplam = toplam_bakiye

        if not check_result or not check_result[0]:
            # İlk kayıt - İlk toplam ve güncel toplam aynı olacak
            query = """
            INSERT INTO borsa_info (
                user_id, username, exchange, spot, vadeli,
                marjin, fonlama, kazan, bot, guncel_toplam, ilk_toplam, son_güncelleme
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """
            params = (
                user_id, username, 'bitmart', spot_total, futures_total,
                spot_available, futures_available, 0.0, 0.0,
                toplam_bakiye, ilk_toplam
            )
            db_operation(query, params, operation='insert')
            logger.info(f"Yeni BitMart kaydı oluşturuldu: {params}")
        else:
            # Mevcut kayıt varsa
            mevcut_ilk_toplam = float(check_result[0][0] or 0)

            # İlk toplam boş veya sıfır ise ilk toplama yaz
            if mevcut_ilk_toplam == 0:
                ilk_toplam = toplam_bakiye
            else:
                ilk_toplam = mevcut_ilk_toplam

            # Kar/Zarar hesaplama
            kar_zarar = toplam_bakiye - ilk_toplam
            kar_zarar_str = "KAR" if kar_zarar > 0 else "ZARAR"

            query = """
            UPDATE borsa_info SET 
                username = COALESCE(?, username),
                spot = ?, 
                vadeli = ?, 
                marjin = COALESCE(marjin, ?), 
                fonlama = COALESCE(fonlama, ?), 
                kazan = COALESCE(kazan, ?), 
                bot = COALESCE(bot, ?), 
                guncel_toplam = ?,
                ilk_toplam = ?,
                kar_zarar_toplam = ?, 
                kar_zarar = ?,
                son_güncelleme = CURRENT_TIMESTAMP
            WHERE user_id = ? AND exchange = 'bitmart'
            """
            params = (
                username,
                spot_total,
                futures_total,
                0.0,  # Marjin
                0.0,  # Fonlama
                toplam_bakiye,  # Kazan
                0,  # Bot
                toplam_bakiye,  # Güncel toplam her zaman yeni bakiye
                ilk_toplam,
                kar_zarar,
                kar_zarar_str,
                user_id
            )

            db_operation(query, params, operation='update')
            logger.info(f"BitMart bakiye kaydı güncellendi: Toplam={toplam_bakiye:.4f}, Kar/Zarar={kar_zarar:.4f}")

        logger.info(f"Kullanıcı {user_id} ({username}) için BitMart bakiyesi kaydedildi: {toplam_bakiye:.4f}")
        return True

    except Exception as e:
        logger.error(f"BitMart bakiye kaydetme hatası: {str(e)}", exc_info=True)
        return False


async def update_bitmart_user_balances():
    try:
        # Tüm BitMart API bilgisi olan kullanıcıları al
        query = "SELECT user_id, api_key, secret_key, passphrase FROM api_key WHERE exchange = 'bitmart'"
        users = db_operation(query, operation='select', fetch=True)

        if not users:
            logger.warning("BitMart API bilgisi olan kullanıcı bulunamadı.")
            return False

        basarili_guncelleme = 0
        basarisiz_guncelleme = 0

        # Her kullanıcı için bakiye güncelleme
        for user in users:
            user_id = None
            try:
                user_id, api_key, secret_key, passphrase = user

                # API bilgilerini kontrol et
                if not all([api_key, secret_key]):
                    logger.warning(f"Kullanıcı {user_id} için eksik API bilgileri.")
                    basarisiz_guncelleme += 1
                    continue

                # Kullanıcı bakiyesini güncelle
                balance_message, _ = await get_bitmart_balance(api_key, secret_key, user_id)

                # Başarılı güncelleme sayısını artır
                basarili_guncelleme += 1
                logger.info(f"Kullanıcı {user_id} için bakiye güncellendi.")

            except Exception as user_error:
                basarisiz_guncelleme += 1
                error_msg = \
                    f"Kullanıcı {user_id if user_id else 'bilinmeyen'} için güncelleme hatası: {str(user_error)}"
                logger.error(error_msg)
                continue

        logger.info(f"Toplam {basarili_guncelleme} kullanıcı başarıyla güncellendi, {basarisiz_guncelleme} başarısız.")
        return True

    except Exception as e:
        logger.error(f"Tüm kullanıcı bakiyeleri güncellenirken hata: {str(e)}")
        return False
