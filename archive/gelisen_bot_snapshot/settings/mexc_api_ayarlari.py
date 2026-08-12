# mexc_api_ayarlari dosyamız burdan başlıyor
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext

from data.olimpos_data import db_operation
from logger_config import setup_logging
from config.constants import *
import telegram
import uuid

import time
import hmac
import hashlib
import aiohttp
import json
import asyncio
from typing import Optional, Tuple, Dict
import ccxt.async_support as ccxtasync  # Async desteği için
import ccxt

# Özel bir logger oluşturun ve yapılandırın
logger = setup_logging('mexc_api_ayarlari_logları')
# logger.info("Bu bir bilgi mesajıdır.")


def some_function():
    logger.info("Bu bir bilgi mesajıdır.")


async def run_settings(update: Update, context: CallbackContext):
    await mexc_ayarlari_menu(update, context)


async def mexc_ayarlari_menu(update: Update, _context: CallbackContext) -> State:
    logger.info("mexc_Api_Ayarlari Menüsü Gösteriliyor.")
    query = update.callback_query
    if query:
        await query.answer()

    keyboard = [
        [InlineKeyboardButton("Bakiye Kontrol", callback_data="mexc_balance")],
        [InlineKeyboardButton("İşlem Geçmişi", callback_data="mexc_trade_history")],
        [InlineKeyboardButton("Açık Emirler", callback_data="mexc_open_orders")],
        [InlineKeyboardButton("Aktif Pozisyonlar", callback_data="mexc_positions")],
        [InlineKeyboardButton("Ana Menü", callback_data="main_menu")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    if query:
        await query.edit_message_text("mexc API İşlemleri", reply_markup=reply_markup)
    else:
        await update.message.reply_text("mexc API İşlemleri", reply_markup=reply_markup)

    logger.info("mexc menüsü gösterildi.")
    return State.MEXC_MENU

async def get_mexc_meta(exchange, symbol: str) -> Optional[Dict]:
    """
    DÜZELTME: Bu fonksiyon, MEXC için vadeli işlem sembolünü akıllıca bulur.
    - Artık gereksiz yere `load_markets` çağırmaz.
    - Olası tüm sembol formatlarını (LTC_USDT, LTC/USDT:USDT) deneyerek doğru eşleşmeyi bulur.
    """
    if not exchange or not symbol:
        return None

    # Temel sembolü ve karşıt birimi çıkar (örn: LTC/USDT -> LTC, USDT)
    base = symbol.split('/')[0]
    quote = "USDT"

    # MEXC için olası vadeli işlem formatları
    candidates = [
        f"{base}_{quote}",          # Ana format: LTC_USDT
        f"{base.upper()}_{quote}", # Büyük harf formatı
        f"{base}/{quote}:USDT"      # CCXT'nin bazen kullandığı format
    ]

    # Piyasaları al (zaten yüklenmiş olmalı)
    markets = getattr(exchange, 'markets', {})
    if not markets:
        logger.error("[GET_MEXC_META] Borsa piyasaları yüklenmemiş!")
        return None

    # Adayları dene
    for candidate_symbol in candidates:
        if candidate_symbol in markets:
            logger.debug(f"[GET_MEXC_META] Eşleşme bulundu: {symbol} -> {candidate_symbol}")
            return markets[candidate_symbol]

    logger.warning(f"[GET_MEXC_META] {symbol} için MEXC vadeli işlem piyasası bulunamadı.")
    return None

async def handle_mexc_actions(update: Update, context: CallbackContext) -> State:
    # Query nesnesini kontrol et
    query = update.callback_query
    if not query:
        logger.warning("Callback query bulunamadı")
        return State.MAIN_MENU

    try:
        # Query'yi yanıtla
        await query.answer()

        # Tam action ve kullanıcı bilgilerini al
        full_action = query.data
        user_id = query.from_user.id

        # Detaylı logging
        logger.info(f"MEXC Action İşleniyor - Kullanıcı: {user_id}, Action: {full_action}")

        # Özel menü ve ana menü yönlendirmeleri
        if full_action in ["select_exchange_mexc", "mexc_menu"]:
            return await mexc_ayarlari_menu(update, context)
        # DÜZELTME: show_main_menu'yü doğrudan çağırmak yerine, ConversationHandler'ın
        # bu durumu işlemesi için State.MAIN_MENU döndürülüyor.
        elif full_action == "main_menu":
            return State.MAIN_MENU
        # Action türünü belirle
        action = full_action.split('_', 1)[-1]  # 'mexc_balance' -> 'balance'
        logger.info(f"İşlenecek action türü: {action}")
        from strategies.alarm_strateji import OlimposStrategy

        # API bilgilerini kontrol et
        api_info = await get_api_key(user_id, 'mexc')
        if not api_info:
            logger.warning(f"Kullanıcı {user_id} için MEXC API bilgisi bulunamadı.")
            await query.edit_message_text("Henüz kayıtlı MEXC API hesabınız bulunmamaktadır.")
            return State.MAIN_MENU

        # API bilgilerini çıkar
        api_key = api_info["api_key"]
        secret_key = api_info["secret_key"]

        # --- YENİ MANTIK: Mevcut bağlantıyı kullan veya yeniden başlat ---
        # Bakiye kontrolü gibi işlemler için her zaman güncel ve doğrulanmış bir bağlantı gerekir.
        # initialize_exchange, zaten bir bağlantı varsa onu yeniden kullanır, yoksa sıfırdan kurar.
        is_initialized = await OlimposStrategy.initialize_exchange(
            user_id=user_id,
            exchange_name='mexc',
            api_key=api_key,
            secret_key=secret_key,
            passphrase=api_info.get("passphrase"),
            context=context
        )
        if not is_initialized:
            logger.info("Mevcut MEXC bağlantısı yok, yeniden başlatılıyor...")
            await query.edit_message_text("MEXC borsasına bağlanılamadı. Lütfen API ayarlarınızı kontrol edin.")
            return State.MAIN_MENU

        # Sonuç değişkenlerini tanımla
        result_markup = None
        result_message = ""
        _ = result_message
        action_performed = False

        # Action türüne göre işlem yap
        try:
            if action == "trade_history":
                # DÜZELTME: Fonksiyona artık exchange nesnesi gönderiliyor.
                result_message, result_markup = await get_mexc_trade_history(OlimposStrategy.exchange)  # type: ignore

            elif action == "open_orders":
                result_message, result_markup = await get_mexc_open_orders(OlimposStrategy.exchange)

            elif action == "positions":
                result_message, result_markup = await get_mexc_positions(OlimposStrategy.exchange)

            else:
                logger.warning(f"Bilinmeyen MEXC action: {action}")
                await query.edit_message_text("Geçersiz bir işlem seçtiniz.")
                return State.MAIN_MENU
            # Eğer bir işlem yapıldıysa işaretle
            if result_message or result_markup:
                action_performed = True

        except ccxt.NetworkError as network_error:
            logger.error(f"Ağ hatası: {network_error}")
            result_message = "Ağ bağlantısı hatası. Lütfen internet bağlantınızı kontrol edin."

        except ccxt.AuthenticationError as auth_error:
            logger.error(f"Kimlik doğrulama hatası: {auth_error}")
            result_message = "API kimlik doğrulama hatası. Lütfen API anahtarlarınızı kontrol edin."

        except ccxt.ExchangeError as exchange_error:
            logger.error(f"Exchange hatası: {exchange_error}")
            result_message = f"Exchange işlem hatası: {str(exchange_error)}"

        except (ValueError, TypeError) as value_error:  # Spesifik hata türlerini belirtiyoruz
            logger.error(f"MEXC {action} veri tipi hatası: {value_error}", exc_info=True)
            result_message = f"Veri işleme hatası: {str(value_error)}"
        except KeyError as key_error:  # Anahtar hatası için özel yakalama
            logger.error(f"MEXC {action} anahtar hatası: {key_error}", exc_info=True)
            result_message = "Gerekli veri bilgisi bulunamadı."
        # DÜZELTME: Sadece bir işlem yapıldıysa mesajı düzenle
        if action_performed:
            await query.edit_message_text(
                result_message,
                reply_markup=result_markup,
                parse_mode='HTML'
            )

        logger.info(f"MEXC {action} işlemi tamamlandı")
        return State.MEXC_MENU

    except telegram.error.TelegramError as telegram_error:
        # Telegram API ile ilgili hatalar
        logger.error(f"Telegram API hatası: {telegram_error}", exc_info=True)

        try:
            if query:
                await query.edit_message_text(
                    "Telegram ile iletişim sırasında bir hata oluştu. Lütfen daha sonra tekrar deneyin."
                )
        except telegram.error.TelegramError:  # Spesifik olarak Telegram hatası yakalıyoruz
            logger.error("Mesaj düzenleme sırasında ikinci bir Telegram hatası oluştu")

        return State.MAIN_MENU

    except KeyError as key_error:
        # Anahtar hataları (eksik veri erişimi)
        logger.error(f"Veri erişim hatası: {key_error}", exc_info=True)

        try:
            if query:
                await query.edit_message_text(
                    "Veri işlenirken bir hata oluştu. Gerekli bilgiler eksik veya hatalı."
                )
        except telegram.error.TelegramError as msg_error:  # Genel Exception yerine özel hata
            logger.error(f"Mesaj gönderme hatası: {msg_error}")

        return State.MAIN_MENU

    except Exception as global_error:
        error_id = uuid.uuid4()
        logger.critical(
            f"MEXC action beklenmeyen hata [ID: {error_id}]: {global_error}",
            exc_info=True
        )
        try:
            if query:
                await query.edit_message_text(
                    f"Beklenmedik bir hata oluştu. Hata referans numarası: {error_id}"
                )
        except telegram.error.TelegramError:
            pass
        return State.MAIN_MENU


async def _fetch_mexc_tickers_fallback() -> Optional[Dict]:
    """
    DÜZELTME: MEXC için ccxt'nin fetch_tickers() metodunun None döndürme sorununu
    çözmek için doğrudan public API endpoint'ini kullanan bir yedek fonksiyon.
    Bu, 'NoneType' object has no attribute 'keys' hatasını engeller.
    """
    url = "https://api.mexc.com/api/v3/ticker/24hr"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=15) as response:
                if response.status == 200:
                    data = await response.json()
                    if isinstance(data, list):
                        # ccxt formatına dönüştür: {'BTC/USDT': {...}, ...}
                        tickers = {
                            item['symbol'].replace('_', '/'): {
                                'symbol': item['symbol'].replace('_', '/'),
                                'last': float(item.get('lastPrice', 0)),
                                'quoteVolume': float(item.get('quoteVolume', 0)),
                                'percentage': (float(item.get('priceChangePercent', 0)) * 100)
                                if item.get('priceChangePercent') else 0.0,
                                'info': item
                            } for item in data if isinstance(item, dict) and 'symbol' in item
                        }
                        logger.info(f"✅ MEXC Fallback ile {len(tickers)} ticker başarıyla çekildi.")
                        return tickers
    except Exception as e:
        logger.error(f"❌ MEXC Fallback Ticker çekme hatası: {e}")
    return None


async def get_api_key(user_id, exchange):
    query = """
        SELECT api_key, secret_key
        FROM api_key
        WHERE user_id = ? AND exchange = ?
    """
    params = (user_id, exchange)

    # Veritabanı işlemini ayrı bir thread'de çalıştır
    result = await asyncio.to_thread(
        db_operation,
        query,
        params,
        operation='select',
        fetch=True,
        fetch_all=False  # Tek satır beklediğimizi belirtiyoruz
    )

    if result and isinstance(result, (list, tuple)) and len(result) >= 2:
        return {
            'api_key': result[0],
            'secret_key': result[1]
        }
    return None


class MEXCClient:
    def __init__(
            self,
            api_key,
            secret_key,
            passphrase=None,
            base_url='https://api.mexc.com',
            timeout=10,
            retries=3
    ):
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.base_url = base_url
        self.timeout = timeout
        self.retries = retries

    def _generate_signature(self, timestamp, method, request_path, params=None):
        try:
            # MEXC'nin imza oluşturma mekanizması
            sorted_params = sorted(params.items(), key=lambda x: x[0]) if params else []
            query_string = '&'.join([f"{k}={v}" for k, v in sorted_params])

            message = f"{timestamp}{method.upper()}{request_path}"
            if query_string:
                message += f"?{query_string}"

            signature = hmac.new(
                self.secret_key.encode('utf-8'),
                message.encode('utf-8'),
                hashlib.sha256
            ).hexdigest().upper()

            return signature
        except Exception as e:
            logger.error(f"İmza oluşturma hatası: {e}")
            raise

    async def _make_request(self, method, endpoint, params=None):
        # Detaylı hata yakalama ve log mekanizması
        # Endpoint ve parametreleri doğrulama
        timestamp = str(int(time.time() * 1000))
        params = params or {}
        params['timestamp'] = timestamp

        # İmza oluşturma
        signature = self._generate_signature(timestamp, method, endpoint, params)

        headers = {
            "MX-API-KEY": self.api_key,
            "MX-SIGN": signature,
            "MX-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        # Endpoint'leri kontrol et
        if endpoint not in [
            '/api/v3/spot/account',  # Spot hesap
            '/api/v1/contract/account',  # Futures hesap
            '/api/v1/contract/order/history',  # İşlem geçmişi
            '/api/v1/contract/order/open',  # Açık emirler
            '/api/v1/contract/position'  # Pozisyonlar
        ]:
            logger.error(f"Geçersiz endpoint: {endpoint}")
            return {'success': False, 'message': 'Geçersiz endpoint'}

        # Request mekanizması
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                        f"{self.base_url}{endpoint}",
                        headers=headers,
                        params=params
                ) as response:
                    response_text = await response.text()

                    # Detaylı hata kontrolü
                    if response.status != 200:
                        logger.warning(f"API Hata Yanıtı: {response_text}")
                        return {
                            'success': False,
                            'code': response.status,
                            'message': response_text
                        }

                    return {
                        'success': True,
                        'data': json.loads(response_text)
                    }

            except Exception as e:
                logger.error(f"Request Error: {e}", exc_info=True)
                return {
                    'success': False,
                    'code': 'REQUEST_ERROR',
                    'message': str(e)
                }

    async def get_account_balance(self):
        # MEXC Spot hesap bakiyesi için güncel endpoint
        endpoint = '/api/v3/spot/account'  # Endpoint'i güncelledik
        return await self._make_request('GET', endpoint)

    async def get_futures_account_balance(self):
        # MEXC Futures hesap bakiyesi için güncel endpoint
        endpoint = '/api/v1/contract/account'
        return await self._make_request('GET', endpoint)

    async def get_trade_history(self, symbol='BTCUSDT', page_size=20, page_no=1):
        endpoint = '/api/v1/contract/order/history'
        params = {
            'symbol': symbol,
            'pageSize': page_size,
            'pageNo': page_no
        }
        return await self._make_request('GET', endpoint, params)

    async def get_open_orders(self, symbol='BTCUSDT'):
        endpoint = '/api/v1/contract/order/open'
        params = {
            'symbol': symbol
        }
        return await self._make_request('GET', endpoint, params)

    async def get_positions(self, symbol='BTCUSDT'):
        endpoint = '/api/v1/contract/position'
        params = {
            'symbol': symbol
        }
        return await self._make_request('GET', endpoint, params)


async def get_coin_price(exchange, coin):
    try:
        # USDT için özel işlem
        if coin.upper() == 'USDT':
            return 1.0

        # Öncelikle MEXC üzerinden fiyat çekme
        try:
            market_pairs = [  # type: ignore
                f'{coin}/USDT',
                f'{coin.upper()}/USDT',
                f'{coin.lower()}/USDT'
            ]

            for pair in market_pairs:
                if pair in exchange.markets:
                    ticker = await exchange.fetch_ticker(pair)  # await eklendi
                    return ticker['last']
        except Exception as mexc_error:
            logger.warning(f"MEXC'den {coin} fiyatı alınamadı: {mexc_error}")
            return 0

    except Exception as e:
        logger.error(f"{coin} fiyatı alınamadı: {e}")
        return 0


async def process_balance(balance_data, account_type, exchange, user_id):
    total_usdt_value = 0
    usable_coins = {}
    unusable_coins = {}
    error_details = {}

    # Bakiye verilerini işleme
    for coin, details in balance_data['total'].items():
        try:
            # Detaylı hata yakalama ve raporlama
            if details is None or details < 1e-6:
                continue

            # Coin adını düzelt
            coin_name = coin.split('.')[0]  # Nokta varsa kaldır

            # Kullanılabilir ve kullanımdaki bakiyeler
            available = balance_data['free'].get(coin, 0)
            used = balance_data['used'].get(coin, 0)
            total = details  # Zaten bakiyenin toplam değerine sahibiz

            # Fiyat al
            last_price = await get_coin_price(exchange, coin_name)

            # Fiyat alınamazsa detaylı bilgi kaydet
            if last_price <= 0:
                error_details[coin_name] = {
                    'Hata': 'Fiyat alınamadı',
                    'Olası Sebepler': [
                        'Coin MEXC borsasında işlem görmüyor',
                        'API erişim sorunu',
                        'Coin çifti bulunamadı'
                    ]
                }
                last_price = 1  # Varsayılan fiyat

            # USDT değerlerini hesapla
            total_usdt = total * last_price
            available_usdt = available * last_price
            used_usdt = used * last_price

            total_usdt_value += total_usdt

            usable_coins[coin_name] = {
                'Toplam': total,
                'Kullanılabilir': available,
                'Kullanımda': used,
                'Toplam_USDT': total_usdt,
                'Kullanılabilir_USDT': available_usdt,
                'Kullanımda_USDT': used_usdt,
                'Son_Fiyat': last_price
            }

        except Exception as e:
            error_details[coin] = {
                'Hata': str(e),
                'Detay': f"İşleme sırasında beklenmedik bir hata oluştu"
            }
            unusable_coins[coin] = details

    # Hata detaylarını JSON olarak kaydet
    if error_details:
        with open(f'{user_id}_{account_type}_error_log.json', 'w') as f:
            json.dump(error_details, f, indent=4)

    return total_usdt_value, usable_coins, unusable_coins, error_details


async def get_mexc_balance(user_id: int) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """
    DÜZELTME: Bu fonksiyon artık API anahtarları almaz.
    Bunun yerine OlimposStrategy.exchange üzerindeki mevcut bağlantıyı kullanır.
    """
    from strategies.alarm_strateji import OlimposStrategy
    try:
        # DÜZELTME: Her zaman ana exchange nesnesini kullan. Bu, zaman senkronizasyonu gibi
        # initialize_exchange içinde yapılan ayarların korunmasını sağlar.
        exchange = OlimposStrategy.exchange
        if not exchange or not getattr(exchange, 'markets'):
            raise ConnectionError("Borsa bağlantısı hazır değil veya piyasalar yüklenmemiş.")

        logger.info(f"MEXC Bakiye Çekme İşlemi Başladı - Kullanıcı ID: {user_id} (Mevcut bağlantı kullanılıyor)")

        # Spot ve futures bakiyeleri al
        logger.info("Spot bakiye çekme işlemi başlatılıyor")
        spot_balance = await exchange.fetch_balance()
        logger.info(f"Spot bakiye çekildi: {spot_balance is not None}")

        logger.info("Futures bakiye çekme işlemi başlatılıyor")
        futures_balance = await exchange.fetch_balance(params={'type': 'swap'})
        logger.info("Futures bakiye çekildi")

        # Bakiye verilerini işle
        spot_total = spot_balance.get('total', {})
        futures_total = futures_balance.get('total', {})

        # Sadece total USDT değerlerini al
        spot_usdt = spot_total.get('USDT', 0.0)
        futures_usdt = futures_total.get('USDT', 0.0)

        # Mesaj oluştur
        balance_message = (
            f"<b>📊 MEXC Hesap Bakiyeleri (Kullanıcı: {user_id})</b>\n\n"
            f"<b>💰 SPOT:</b> {spot_usdt:.2f} USDT\n"
            f"<b>⚡ VADELİ:</b> {futures_usdt:.2f} USDT\n"
            f"<b>───────────────</b>\n"
            f"<b>📈 TOPLAM:</b> {spot_usdt + futures_usdt:.2f} USDT"
        )

        keyboard = [
            [InlineKeyboardButton("MEXC Menüsü", callback_data='mexc_menu')],
            [InlineKeyboardButton("Ana Menü", callback_data='main_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        logger.info("MEXC bakiye çekme ve kaydetme işlemi başarıyla tamamlandı")

        # Veritabanına kaydetme işlemini de buraya taşıyalım
        await save_mexc_balance_to_db(
            user_id,
            {'total':spot_usdt},
            {'total':futures_usdt}
        )
        return balance_message, reply_markup


    except (ccxt.AuthenticationError, ccxt.ExchangeError, ConnectionError, Exception) as e:
        logger.error(f"Detaylı bakiye alımında beklenmedik hata: {e}", exc_info=True)
        return f"Bakiye alımında hata oluştu: {e}", None


async def get_mexc_trade_history(exchange: ccxtasync.Exchange) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """
    DÜZELTME: Bu fonksiyon artık API anahtarları almaz.
    Bunun yerine doğrudan bir exchange nesnesi alır.
    """
    try:
        # Mevcut exchange nesnesini kullan
        exchange.options['defaultType'] = 'swap'
        # Son 20 işlemi çek
        trades = await exchange.fetch_my_trades('BTC_USDT', limit=20)

        trade_history = "<b>MEXC İşlem Geçmişi:</b>\n\n"

        if not trades:
            trade_history += "Henüz bir işlem geçmişi bulunmamaktadır."
        else:
            for trade in trades:
                symbol = trade.get('symbol')
                side = 'Alış' if trade.get('side') == 'buy' else 'Satış'
                price = trade.get('price')
                amount = trade.get('amount')
                total = trade.get('cost', 0.0)
                timestamp = trade.get('datetime')

                trade_history += (
                    f"<pre>"
                    f"Sembol: {symbol}\n"
                    f"İşlem Tipi: {side}\n"
                    f"Fiyat: {price} USDT\n"
                    f"Miktar: {amount} {symbol.split('/')[0] if symbol else ''}\n"
                    f"Toplam: {total:.2f} USDT\n"
                    f"Tarih: {timestamp}\n"
                    f"</pre>"
                    f"------------------------\n"
                )

        keyboard = [
            [InlineKeyboardButton("Ana Menü", callback_data='main_menu')],
            [InlineKeyboardButton("MEXC Menüsü", callback_data='mexc_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        return trade_history, reply_markup

    except Exception as e:
        logger.error(f"CCXT İşlem Geçmişi Hatası: {e}")
        return f"İşlem geçmişi alınamadı: {e}", None


async def get_mexc_open_orders(exchange: ccxtasync.Exchange) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    try:
        exchange.options['defaultType'] = 'swap'
        open_orders = await exchange.fetch_open_orders('BTC_USDT')

        open_orders_text = "<b>MEXC Açık Emirler:</b>\n\n"

        if not open_orders:
            open_orders_text += "Açık emir bulunmamaktadır."
        else:
            for order in open_orders: # DÜZELTME: Metin oluşturma döngünün içine taşındı.
                symbol = order.get('symbol')
                side = 'Alış' if order.get('side') == 'buy' else 'Satış'
                price = order.get('price')
                amount = order.get('amount')
                order_type = order.get('type')
                timestamp = order.get('datetime')

                open_orders_text += (
                    f"<pre>"
                    f"Sembol: {symbol}\n"
                    f"Emir Tipi: {side}\n" # type: ignore
                    f"Fiyat: {price} USDT\n" # type: ignore
                    f"Miktar: {amount} {symbol.split('/')[0]}\n"
                    f"Emir Türü: {order_type}\n"
                    f"Tarih: {timestamp}\n"
                    f"</pre>"
                    f"------------------------\n"
                )

        keyboard = [
            [InlineKeyboardButton("Ana Menü", callback_data='main_menu')],
            [InlineKeyboardButton("MEXC Menüsü", callback_data='mexc_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        return open_orders_text, reply_markup

    except Exception as e:
        logger.error(f"CCXT Açık Emirler Hatası: {e}")
        return f"Açık emirler alınamadı: {e}", None


async def get_mexc_positions(exchange: ccxtasync.Exchange) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    try:
        exchange.options['defaultType'] = 'swap'

        # Açık pozisyonları çek
        positions = await exchange.fetch_positions(['BTC_USDT'])

        active_positions = "<b>MEXC Aktif Pozisyonlar:</b>\n\n"

        if not positions:
            active_positions += "Aktif pozisyon bulunmamaktadır."
        else:
            for position in positions:  # type: ignore
                symbol = position.get('symbol')
                side = position.get('side')
                contracts = position.get('contracts', 0.0)
                entry_price = position.get('entryPrice')
                unrealized_pnl = position.get('unrealizedPnl')

                active_positions += (
                    f"<pre>"
                    f"Sembol: {symbol}\n"
                    f"Pozisyon Yönü: {side}\n"
                    f"Miktar: {contracts}\n"
                    f"Giriş Fiyatı: {entry_price} USDT\n"
                    f"Kar/Zarar: {unrealized_pnl} USDT\n"
                    f"</pre>"
                    f"------------------------\n"
                )

        keyboard = [
            [InlineKeyboardButton("Ana Menü", callback_data='main_menu')],
            [InlineKeyboardButton("MEXC Menüsü", callback_data='mexc_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        return active_positions, reply_markup

    except Exception as e:
        logger.error(f"CCXT Pozisyonlar Hatası: {e}")
        return f"Aktif pozisyonlar alınamadı: {e}", None


async def save_mexc_balance_to_db(user_id, spot_balance, futures_balance):
    """
    PostgreSQL Uyumlu: Bakiye kaydetme fonksiyonu.
    """
    try:
        # 1. Kullanıcı adını al
        query_username = """
            SELECT username 
            FROM admin_users 
            WHERE user_id = %s 
            AND username IS NOT NULL 
            AND username != ''
        """
        username_result = await asyncio.to_thread(
            db_operation,
            query_username,
            params=(str(user_id),),
            operation='select',
            fetch=True,
            fetch_all=True
        )

        if not username_result or not username_result[0]:
            username = "İsimsiz Kullanıcı"
        else:
            username = username_result[0][0]

        # Hesap türlerine göre bakiyeleri hesapla
        spot_bakiye = float(spot_balance.get('total', 0.0))
        vadeli_bakiye = float(futures_balance.get('total', 0.0))
        marjin_bakiye = 0.0
        fonlama_bakiye = 0.0
        kazan_bakiye = 0.0
        bot_bakiye = 0.0

        toplam_bakiye = spot_bakiye + vadeli_bakiye + marjin_bakiye + fonlama_bakiye + kazan_bakiye + bot_bakiye

        # 2. İlk toplamı kontrol et
        query_initial = "SELECT ilk_toplam FROM borsa_info WHERE user_id = %s AND exchange = 'mexc'"
        initial_total = await asyncio.to_thread(
            db_operation,
            query_initial,
            params=(str(user_id),),
            operation='select',
            fetch=True,
            fetch_all=True
        )

        if not initial_total:
            # İLK KAYIT
            query = """
            INSERT INTO borsa_info (
                user_id, username, exchange, spot, vadeli, marjin, 
                fonlama, kazan, bot, guncel_toplam, ilk_toplam, son_guncelleme
            ) VALUES (%s, %s, 'mexc', %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """
            params = (str(user_id), username, spot_bakiye, vadeli_bakiye, marjin_bakiye,
                fonlama_bakiye, kazan_bakiye, bot_bakiye, toplam_bakiye, toplam_bakiye)

            await asyncio.to_thread(
                db_operation,
                query,
                params=params,
                operation='insert',
                fetch=False,
                fetch_all=False
            )
            logger.info(f"Yeni MEXC bakiye kaydı oluşturuldu (User: {user_id})")
        else:
            # GÜNCELLEME
            ilk_toplam = float(initial_total[0][0])
            kar_zarar = toplam_bakiye - ilk_toplam
            kar_zarar_str = "KAR" if kar_zarar > 0 else "ZARAR"

            query = """
            UPDATE borsa_info SET 
                username = %s, spot = %s, vadeli = %s, marjin = %s, 
                fonlama = %s, kazan = %s, bot = %s, guncel_toplam = %s,
                kar_zarar_toplam = %s, kar_zarar = %s, son_guncelleme = NOW()
            WHERE user_id = %s AND exchange = 'mexc'
            """
            params = (username, spot_bakiye, vadeli_bakiye, marjin_bakiye,
                fonlama_bakiye, kazan_bakiye, bot_bakiye, toplam_bakiye,
                kar_zarar, kar_zarar_str, str(user_id))

            await asyncio.to_thread(
                db_operation,
                query,
                params=params,
                operation='update',
                fetch=False,
                fetch_all=False
            )
            logger.info(f"MEXC bakiye kaydı güncellendi (User: {user_id})")

        return True

    except Exception as e:
        logger.error(f"MEXC bakiye kaydetme hatası: {str(e)}")
        return False


async def get_mexc_account_balances(api_key, secret_key):
    """
    MEXC hesap bakiyelerini çeker.
    HATA YÖNETİMİ: Eğer API hatası varsa None döner (0 dönmez).
    """
    exchange = None
    try:
        exchange = ccxtasync.mexc({
            'apiKey':api_key,
            'secret':secret_key,
            'enableRateLimit':True,
            'options':{'defaultType':'spot'}
        })

        await exchange.load_markets()

        # Spot Bakiye
        try:
            spot_balance_data = await exchange.fetch_balance()
        except Exception as e:
            logger.error(f"MEXC Spot Bakiye Hatası (API Key kontrol edin): {e}")
            # Eğer spot çekemiyorsak, muhtemelen API Key bozuktur. İşlemi durdur.
            return None

        # Futures Bakiye
        try:
            futures_balance_data = await exchange.fetch_balance(params={'type':'swap'})
        except Exception as e:
            logger.warning(f"MEXC Vadeli Bakiye çekilemedi (İzinleri kontrol edin): {e}")
            futures_balance_data = {'total':{}}  # Vadeli yoksa boş kabul et

        # Spot Hesaplama
        spot_total_usdt = 0
        spot_balances = {}
        if spot_balance_data and 'total' in spot_balance_data:
            for coin, amount in spot_balance_data['total'].items():
                if amount > 0:
                    if coin == 'USDT':
                        spot_total_usdt += amount
                        spot_balances[coin] = amount
                    else:
                        try:
                            ticker_symbol = f"{coin}/USDT"
                            if ticker_symbol in exchange.markets:
                                ticker = await exchange.fetch_ticker(ticker_symbol)
                                spot_total_usdt += amount * ticker['last']
                        except Exception:
                            pass

        # Futures Hesaplama
        futures_total_usdt = 0
        futures_balances = {}
        if futures_balance_data and 'total' in futures_balance_data:
            for coin, amount in futures_balance_data['total'].items():
                if amount > 0:
                    if coin == 'USDT':
                        futures_total_usdt += amount
                        futures_balances[coin] = amount
                    else:
                        try:
                            ticker_symbol = f"{coin}/USDT"
                            if ticker_symbol in exchange.markets:
                                ticker = await exchange.fetch_ticker(ticker_symbol)
                                futures_total_usdt += amount * ticker['last']
                        except Exception:
                            pass

        return {
            'spot':{'total':spot_total_usdt, 'balances':spot_balances},
            'futures':{'total':futures_total_usdt, 'balances':futures_balances},
            'total':spot_total_usdt + futures_total_usdt
        }

    except Exception as e:
        # Genel bağlantı hatası veya Auth hatası
        logger.error(f"MEXC Kritik API Hatası: {str(e)}")
        return None  # Hata varsa None dön, 0 dönme!

    finally:
        if exchange:
            await exchange.close()


async def update_mexc_user_balances(context: CallbackContext = None):
    """
    Tüm kullanıcıların MEXC bakiyelerini periyodik olarak günceller.
    FINAL SÜRÜM: Debug logları temizlendi, güvenlik önlemleri aktif.
    """
    try:
        # 1. Kullanıcıları Çek
        query = """
            SELECT user_id, api_key, secret_key 
            FROM api_key 
            WHERE exchange = 'mexc'
        """

        users = await asyncio.to_thread(
            db_operation,
            query,
            params=(),
            operation='select',
            fetch=True,
            fetch_all=True
        )

        if not users:
            # logger.warning("MEXC API bilgisi olan kullanıcı bulunamadı.")
            return

        processed_count = 0
        error_count = 0

        for user in users:
            user_id = None
            try:
                user_id_raw, api_key, secret_key = user

                # --- FİLTRELEME VE GÜVENLİK ---
                # ID kontrolü (String gelirse ve 'mx' içerirse atla)
                if str(user_id_raw).lower().startswith('mx'):
                    continue
                if not str(user_id_raw).isdigit():
                    continue

                user_id = str(user_id_raw)

                # Anahtar kontrolü
                if not api_key or not secret_key:
                    continue

                # 2. Bakiye Bilgilerini Al
                # .strip() ile olası boşlukları temizliyoruz (Hayat kurtarır)
                balances = await get_mexc_account_balances(api_key.strip(), secret_key.strip())

                # --- KRİTİK KORUMA ---
                # Eğer API hatası varsa (None dönerse), veritabanını bozma, pas geç.
                if balances is None:
                    logger.warning(f"Kullanıcı {user_id} için API hatası (Key geçersiz olabilir). Güncelleme atlandı.")
                    error_count += 1
                    continue

                # 3. Veritabanına Kaydet
                success = await save_mexc_balance_to_db(
                    user_id,
                    balances['spot'],
                    balances['futures']
                )

                if success:
                    processed_count += 1
                else:
                    error_count += 1

                # Rate limit dostu bekleme
                await asyncio.sleep(0.5)

            except Exception as user_error:
                error_count += 1
                logger.error(f"Kullanıcı {user_id} güncelleme hatası: {user_error}")
                continue

        logger.info(f"MEXC bakiye güncelleme tamamlandı: {processed_count} başarılı, {error_count} hatalı")

    except Exception as e:
        logger.error(f"Tüm MEXC kullanıcı bakiyeleri güncellenirken genel hata: {str(e)}")


