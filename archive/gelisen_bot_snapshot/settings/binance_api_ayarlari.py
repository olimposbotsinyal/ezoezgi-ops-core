# Binance API ayarları dosyamız burdan başlıyor
from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceRequestException
import time
import asyncio
import sys

if sys.platform.startswith('win'):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from telegram.ext import ContextTypes
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from data.olimpos_data import db_operation, format_date
from logger_config import setup_logging
from config.constants import State
from datetime import datetime, timedelta
import hmac
import hashlib
import aiohttp
import json
from urllib.parse import urlencode
from typing import Any, Dict

logger = setup_logging('binance_api_ayarlari_logları')
# logger.info("Binance API ayarları başlatıldı.")


async def binance_ayarlari_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    logger.info("Binance API Ayarları Menüsü Gösteriliyor.")
    _ = context  # Bu satır uyarıyı engeller

    query = update.callback_query
    if query:
        await query.answer()

    keyboard = [
        [InlineKeyboardButton("Bakiye Kontrol", callback_data="binance_balance")],
        [InlineKeyboardButton("İşlem Geçmişi", callback_data="binance_trade_history")],
        [InlineKeyboardButton("Açık Emirler", callback_data="binance_open_orders")],
        [InlineKeyboardButton("Aktif Pozisyonlar", callback_data="binance_positions")],
        [InlineKeyboardButton("Ana Menü", callback_data="main_menu")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    if query:
        await query.edit_message_text("Binance API İşlemleri", reply_markup=reply_markup)
    else:
        await update.message.reply_text("Binance API İşlemleri", reply_markup=reply_markup)

    logger.info("Binance menüsü gösterildi.")
    return State.BINANCE_MENU


async def get_binance_server_time():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.binance.com/api/v3/time") as response:
                data = await response.json()
                server_time = data['serverTime']
                current_time = int(time.time() * 1000)

                # Zaman farkını hesapla
                time_diff = abs(server_time - current_time)

                logger.info(f"Sunucu Zamanı: {server_time}")
                logger.info(f"Geçerli Zaman: {current_time}")
                logger.info(f"Zaman Farkı: {time_diff} ms")

                return server_time
    except Exception as e:
        logger.error(f"Sunucu zamanı alınamadı: {e}")
        return int(time.time() * 1000)


class BinanceTimeManager:
    def __init__(self):
        self.time_offset = 0
        self.last_sync_time = None

    async def sync_time(self):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://api.binance.com/api/v3/time") as response:
                    data = await response.json()
                    server_time = data['serverTime']
                    current_time = int(time.time() * 1000)
                    self.time_offset = server_time - current_time
                    self.last_sync_time = datetime.now()
                    logger.info(f"Binance zaman senkronizasyonu: Offset = {self.time_offset} ms")
        except Exception as e:
            logger.error(f"Zaman senkronizasyonu hatası: {e}")
            self.time_offset = 0

    def get_adjusted_timestamp(self):
        # Her 5 dakikada bir yeniden senkronize et
        if (not self.last_sync_time or
                (datetime.now() - self.last_sync_time).total_seconds() > 300):
            asyncio.create_task(self.sync_time())

        return int((time.time() * 1000) + self.time_offset)


# Global olarak bir örnek oluştur
binance_time_manager = BinanceTimeManager()


async def get_api_key(user_id, exchange):
    query = """
        SELECT api_key, secret_key
        FROM api_key
        WHERE user_id = ? AND exchange = ?
    """
    params = (user_id, exchange)
    result = db_operation(query, params, operation='select', fetch=True, fetch_all=False)

    if result and isinstance(result, (list, tuple)) and len(result) >= 2:
        return {
            'api_key': result[0],
            'secret_key': result[1]
        }
    return None


def get_binance_signature(secret_key: str, query_string: str) -> str:
    """Binance API için imza oluşturur. query_string timestamp dahil olmalı."""
    return hmac.new(
        secret_key.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()


async def handle_binance_actions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    query = update.callback_query
    await query.answer()

    full_action = query.data
    logger.info(f"handle_binance_actions çağrıldı. Action: {full_action}")

    user_id = query.from_user.id
    logger.info(f"Kullanıcı ID: {user_id}, İşlem: {full_action}")

    if full_action in ["select_exchange_binance", "binance_menu"]:
        return await binance_ayarlari_menu(update, context)
    elif full_action == "main_menu":
        logger.info("Ana menüye dönüş talep edildi.")
        from Olimpos_Cripto_Bot import show_main_menu
        return await show_main_menu(update, context)

    action = full_action.split('_', 1)[-1]  # 'binance_balance' -> 'balance'
    logger.info(f"İşlenecek action: {action}")

    try:
        api_info = await get_api_key(user_id, 'binance')
        logger.info(f"API bilgileri alındı: {api_info is not None}")

        if not api_info:
            logger.warning(f"Kullanıcı {user_id} için API bilgisi bulunamadı.")
            await query.edit_message_text("Henüz kayıtlı API hesabınız bulunmamaktadır.")
            return State.MAIN_MENU

        api_key = api_info["api_key"]
        secret_key = api_info["secret_key"]

        # Kullanıcı bilgilerini al ve borsa bilgilerini güncelle
        borsa_info = get_borsa_info(user_id)
        if not borsa_info:
            # Yeni borsa bilgisi ekle
            add_borsa_info(user_id, 'binance', 0, 0, 0, 0, 0, 0)

        if action == "balance":
            logger.info("Bakiye kontrolü başlatılıyor...")
            result = await get_binance_balance(api_key, secret_key, user_id)

            # Sonucu kontrol et
            if result and len(result) == 2:
                balance_message, reply_markup = result
                await query.edit_message_text(balance_message, reply_markup=reply_markup, parse_mode='HTML')
            else:
                # Hata durumunda kullanıcıya bilgi ver
                error_message = result[0] if result else "Bakiye bilgileri alınamadı."
                await query.edit_message_text(error_message, parse_mode='HTML')

        elif action == "trade_history":
            logger.info(f"Kullanıcı {user_id} için işlem geçmişi talep edildi.")
            trade_history, reply_markup = await get_binance_trade_history(api_key, secret_key)
            await query.edit_message_text(trade_history, reply_markup=reply_markup, parse_mode='HTML')

        elif action == "open_orders":
            logger.info(f"Kullanıcı {user_id} için açık emirler talep edildi.")
            open_orders, reply_markup = await get_binance_open_orders(api_key, secret_key)
            await query.edit_message_text(open_orders, reply_markup=reply_markup, parse_mode='HTML')

        elif action == "positions":
            logger.info(f"Kullanıcı {user_id} için aktif pozisyonlar talep edildi.")
            spot_positions, futures_positions = await get_binance_positions(api_key, secret_key)

            await query.edit_message_text("<b>Binance Pozisyonlarınız:</b>", parse_mode='HTML')

            # Spot pozisyonları gönder
            if spot_positions:
                await context.bot.send_message(chat_id=update.effective_chat.id, text="<b>Spot Pozisyonları:</b>",
                                               parse_mode='HTML')
                for position in spot_positions:
                    positions_message = (
                        f"<b>Sembol:</b> {position['symbol']}\n"
                        f"<b>Açık Miktar:</b> {position['free_amount']}\n"
                    )
                    keyboard = [
                        [InlineKeyboardButton(
                            f"{position['symbol']} Pozisyonunu Kapat",
                            callback_data=position['callback_data']
                        )],
                        [InlineKeyboardButton(
                            f"{position['symbol']} Pozisyonunu Tersine Çevir",
                            callback_data=f'reverse_spot_position_{position["symbol"]}_'
                                          f'{position["free_amount"].replace(".", "_")}'
                        )]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await context.bot.send_message(chat_id=update.effective_chat.id, text=positions_message,
                                                   reply_markup=reply_markup, parse_mode='HTML')
            else:
                await context.bot.send_message(chat_id=update.effective_chat.id,
                                               text="Açık spot pozisyonunuz bulunmamaktadır.", parse_mode='HTML')

            # Vadeli pozisyonları gönder
            if futures_positions:
                await context.bot.send_message(chat_id=update.effective_chat.id, text="<b>Vadeli Pozisyonları:</b>",
                                               parse_mode='HTML')
                for position in futures_positions:
                    positions_message = (
                        f"<b>Sembol:</b> {position['symbol']}\n"
                        f"<b>Pozisyon Miktarı:</b> {position['position_amount']}\n"
                        f"<b>Ortalama Fiyat:</b> {position['entry_price']}\n"
                        f"<b>Kar/Zarar:</b> {position['unrealized_profit']}\n"
                    )
                    keyboard = [
                        [InlineKeyboardButton(
                            f"{position['symbol']} Pozisyonunu Kapat", callback_data=position['callback_data']
                        )],
                        [InlineKeyboardButton(
                            f"{position['symbol']} Pozisyonunu Tersine Çevir",
                            callback_data=f'reverse_futures_position_'
                                          f'{position["symbol"]}_{position["position_amount"].replace(".", "_")}'
                        )]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await context.bot.send_message(chat_id=update.effective_chat.id, text=positions_message,
                                                   reply_markup=reply_markup, parse_mode='HTML')
            else:
                await context.bot.send_message(chat_id=update.effective_chat.id,
                                               text="Açık vadeli pozisyonunuz bulunmamaktadır.", parse_mode='HTML')

            # En alta genel menü butonlarını ekle
            keyboard = [
                [InlineKeyboardButton("Binance Menüsü", callback_data='binance_menu')],
                [InlineKeyboardButton("Ana Menü", callback_data='main_menu')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Seçenekler:",
                                           reply_markup=reply_markup)

            logger.info(f"handle_binance_actions tamamlandı.")
            return State.BINANCE_MENU

        elif full_action.startswith("close_spot_position_"):
            # Spot pozisyonu kapatma işlemi
            parts = full_action.split("_")
            symbol = parts[3]
            amount = float(parts[4].replace("_", "."))  # Nokta geri eklenir
            result = await close_spot_position(api_key, secret_key, symbol, amount)
            await query.edit_message_text(result, parse_mode='HTML')

        elif full_action.startswith("close_futures_position_"):
            # Vadeli pozisyonu kapatma işlemi
            parts = full_action.split("_")
            symbol = parts[3]
            position_amount = float(parts[4].replace("_", "."))  # Nokta geri eklenir
            result = await close_futures_position(api_key, secret_key, symbol, position_amount)
            await query.edit_message_text(result, parse_mode='HTML')

        elif full_action == "detailed_performance":
            detailed_report = await get_detailed_performance_report(api_key, secret_key, user_id)
            keyboard = [
                [InlineKeyboardButton("Ana Menü", callback_data='main_menu')],
                [InlineKeyboardButton("Binance Menüsü", callback_data='binance_menu')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(detailed_report, reply_markup=reply_markup, parse_mode='HTML')
            return State.BINANCE_MENU

        else:
            await query.edit_message_text("Bilinmeyen bir işlem talep edildi.")
        return State.BINANCE_MENU

    except Exception as e:
        logger.error(f"Binance işlemleri sırasında hata: {str(e)}. Kullanıcı ID: {user_id}, İşlem: {full_action}",
                     exc_info=True)
        await query.edit_message_text(f"Bir hata oluştu: {str(e)}")
        return State.MAIN_MENU


async def calculate_comprehensive_performance(user_id, api_key, secret_key):
    _ = user_id

    try:
        # Deposit ve withdraw işlemlerini çek
        deposits = await get_binance_deposits(api_key, secret_key)
        withdrawals = await get_binance_withdrawals(api_key, secret_key)

        # Spot ve futures bakiyelerini al
        spot_balance = await get_spot_balance(api_key, secret_key)
        futures_balance = await get_futures_balance(api_key, secret_key)

        # Toplam deposit miktarı
        total_deposits = sum(float(deposit.get('amount', 0)) for deposit in deposits)

        # Toplam withdraw miktarı
        total_withdrawals = sum(float(withdrawal.get('amount', 0)) for withdrawal in withdrawals)

        # Toplam bakiye hesaplama
        current_total_balance = (
            spot_balance.get('total_usdt_value', 0) +
            futures_balance.get('total_usdt_value', 0)
        )

        # Net yatırım hesaplama
        net_investment = total_deposits - total_withdrawals

        # Gerçekleşmemiş kar/zarar hesaplama
        unrealized_pnl = current_total_balance - net_investment if net_investment > 0 else current_total_balance

        # Performans yüzdesi hesaplama
        performance_percentage = (
            (unrealized_pnl / (net_investment if net_investment > 0 else 1)) * 100
        )

        # Spot ve vadeli USDT bakiyelerini al
        spot_usdt_balance = spot_balance.get('usdt_balance', 0)
        futures_usdt_balance = futures_balance.get('usdt_balance', 0)

        # Performans raporu
        performance_report = f"""
            📊 Kapsamlı Performans Raporu:
            💰 Toplam Yatırım: {total_deposits:.2f} USDT
            💸 Toplam Çekim: {total_withdrawals:.2f} USDT
            💵 Net Yatırım: {net_investment:.2f} USDT

            🔄 Mevcut Toplam Bakiye: {current_total_balance:.2f} USDT
            📈 Gerçekleşmemiş Kar/Zarar: {unrealized_pnl:.2f} USDT

            📊 Performans Yüzdesi: %{performance_percentage:.2f}

            Spot Bakiye: {spot_usdt_balance:.2f} USDT
            Vadeli Bakiye: {futures_usdt_balance:.2f} USDT
            """

        # Performans metrikleri
        performance_metrics = {
            'total_deposits': total_deposits,
            'total_withdrawals': total_withdrawals,
            'net_investment': net_investment,
            'current_total_balance': current_total_balance,
            'unrealized_pnl': unrealized_pnl,
            'performance_percentage': performance_percentage,
            'spot_usdt_balance': spot_usdt_balance,
            'futures_usdt_balance': futures_usdt_balance
        }

        return performance_metrics, performance_report

    except Exception as e:
        logger.error(f"Performans hesaplama hatası: {str(e)}")
        return None, None


async def get_binance_deposits(api_key, secret_key):
    try:
        # Sunucu zamanını al
        server_time = await get_binance_server_time()

        # Parametreleri hazırla
        params = {
            'timestamp': server_time,
            'recvWindow': 5000
        }

        # Query string oluştur
        query_string = '&'.join([f"{key}={value}" for key, value in params.items()])

        # İmzayı oluştur
        signature = get_binance_signature(secret_key, query_string)

        # Tam URL'yi hazırla
        url = f"https://api.binance.com/sapi/v1/capital/deposit/hisrec?{query_string}&signature={signature}"

        # Header'ları hazırla
        headers = {'X-MBX-APIKEY': api_key}

        # Asenkron istek gönder
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                # Yanıtı kontrol et
                if response.status == 200:
                    deposits = await response.json()
                else:
                    logger.error(f"Deposit çekme hatası. Durum kodu: {response.status}")
                    return []

        # Detaylı log
        logger.info(f"Toplam deposit sayısı: {len(deposits)}")

        # Depositleri veritabanına kaydet
        save_deposits_to_db(deposits)

        return deposits

    except aiohttp.ClientError as client_error:
        logger.error(f"Ağ hatası: {client_error}")
        return []
    except Exception as e:
        logger.error(f"Deposit çekme genel hatası: {str(e)}")
        return []


async def get_binance_withdrawals(api_key, secret_key):
    try:
        server_time = await get_binance_server_time()

        params = {
            'timestamp': server_time,
            'recvWindow': 5000
        }

        query_string = '&'.join([f"{key}={value}" for key, value in params.items()])
        signature = get_binance_signature(secret_key, query_string)

        url = f"https://api.binance.com/sapi/v1/capital/withdraw/history?{query_string}&signature={signature}"

        headers = {'X-MBX-APIKEY': api_key}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                withdrawals = await response.json()

        # Detaylı log ekledik
        logger.info(f"Withdrawal sayısı: {len(withdrawals)}")

        # Withdrawal bilgilerini veritabanına kaydet
        await save_withdrawals_to_db(withdrawals)

        return withdrawals

    except Exception as e:
        logger.error(f"Withdrawal çekme hatası: {str(e)}")
        return []


def get_telegram_user_id(update=None):
    """
    Telegram user_id'yi farklı kaynaklardan almaya çalış
    """
    try:
        # Parametre olarak gelen update kontrolü
        if update and hasattr(update, 'effective_user'):
            user_id = update.effective_user.id
            logger.info(f"User ID update parametresinden alındı: {user_id}")
            return user_id

        # Global update nesnesinden kontrol
        if 'update' in globals():
            global_update = globals()['update']
            if hasattr(global_update, 'effective_user'):
                user_id = global_update.effective_user.id
                logger.info(f"User ID global update nesnesinden alındı: {user_id}")
                return user_id

        # Global değişkenlerden user_id kontrolü
        if 'user_id' in globals():
            global_user_id = globals()['user_id']
            logger.info(f"User ID global değişkenlerden alındı: {global_user_id}")
            return global_user_id

        # Son çare olarak API key tablosundan çek
        query = "SELECT user_id, username FROM api_key WHERE exchange = 'binance' LIMIT 1"
        result = db_operation(query, operation='select', fetch=True)

        if result and result[0]:
            user_id = result[0][0]
            username = result[0][1] if len(result[0]) > 1 else 'Bilinmeyen'
            logger.info(f"User ID API key tablosundan alındı. ID: {user_id}, Username: {username}")
            return user_id

        # Hiçbir yöntemle user_id bulunamadı
        logger.warning("Herhangi bir kaynaktan user_id alınamadı!")
        return 1  # En son çare olarak varsayılan ID

    except Exception as e:
        logger.error(f"Telegram user_id alınırken kritik hata: {e}", exc_info=True)
        return 1


def get_username_by_user_id(user_id):
    """
    Verilen user_id için username'i API key tablosundan al
    """
    try:
        query = "SELECT username FROM api_key WHERE user_id = ? AND exchange = 'binance'"
        result = db_operation(query, (user_id,), operation='select', fetch=True)

        if result and result[0]:
            return result[0][0]  # İlk sonucun username'ini al

        return ''  # Username bulunamazsa boş string dön

    except Exception as e:
        logger.error(f"Username alınamadı: {e}")
        return ''


def save_deposits_to_db(deposits):
    try:
        user_id = get_telegram_user_id() or 1
        username = get_username_by_user_id(user_id)

        upsert_query = """
        INSERT INTO binance_deposits (
            user_id, username, coin, amount, network, status, insert_time, transaction_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (transaction_id) DO UPDATE SET
            user_id = EXCLUDED.user_id,
            username = EXCLUDED.username,
            coin = EXCLUDED.coin,
            amount = EXCLUDED.amount,
            network = EXCLUDED.network,
            status = EXCLUDED.status,
            insert_time = EXCLUDED.insert_time
        """

        saved = 0
        for d in deposits:
            txid = str(d.get("txId", "")).strip()
            if not txid:
                continue

            params = (
                int(user_id),
                str(username or ""),
                str(d.get("coin", "")).strip(),
                float(d.get("amount", 0) or 0),
                str(d.get("network", "")).strip(),
                str(d.get("status", "")).strip(),
                datetime.fromtimestamp((d.get("insertTime", 0) or 0) / 1000),
                txid
            )

            r = db_operation(upsert_query, params, operation="insert")
            if r is not None:
                saved += 1

        logger.info(f"{saved} adet deposit upsert edildi.")

    except Exception as e:
        logger.error(f"Deposit kaydetme hatası: {e}", exc_info=True)


async def save_withdrawals_to_db(withdrawals, telegram_user_id=None):
    if not withdrawals:
        logger.info("Kayıt edilecek withdrawal bulunamadı.")
        return

    # user_id belirle
    if not telegram_user_id:
        telegram_user_id = get_telegram_user_id() or 1

    username = get_username_by_user_id(telegram_user_id)

    # PostgreSQL UPSERT (txId unique)
    upsert_query = """
    INSERT INTO binance_withdrawals (
        user_id, username, coin, amount, network, status, apply_time, transaction_id, transaction_fee
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (transaction_id) DO UPDATE SET
        user_id = EXCLUDED.user_id,
        username = EXCLUDED.username,
        coin = EXCLUDED.coin,
        amount = EXCLUDED.amount,
        network = EXCLUDED.network,
        status = EXCLUDED.status,
        apply_time = EXCLUDED.apply_time,
        transaction_fee = EXCLUDED.transaction_fee
    """

    saved = 0
    for w in withdrawals:
        try:
            txid = str(w.get("txId", "")).strip()
            if not txid:
                continue  # txId yoksa unique mantığı bozulur

            params = (
                int(telegram_user_id),
                str(username or ""),
                str(w.get("coin", "")).strip(),
                float(w.get("amount", 0) or 0),
                str(w.get("network", "")).strip(),
                str(w.get("status", "")).strip(),
                format_date(w.get("applyTime")),      # senin olimpos_data.py içindeki format_date
                txid,
                float(w.get("transactionFee", 0) or 0),
            )

            r = db_operation(upsert_query, params, operation="insert")
            if r is not None:
                saved += 1

        except Exception as e:
            logger.warning(f"Withdrawal kayıt hatası: {e} | data={w}")

    logger.info(f"{saved} adet withdrawal upsert edildi.")


async def get_detailed_performance_report(api_key, secret_key, user_id):
    _ = user_id

    try:
        # Deposit ve withdraw işlemlerini çek
        deposits = await get_binance_deposits(api_key, secret_key)
        withdrawals = await get_binance_withdrawals(api_key, secret_key)

        logger.info(f"Deposit sayısı: {len(deposits)}")
        logger.info(f"Withdrawal sayısı: {len(withdrawals)}")

        # Toplam deposit miktarı
        total_deposits = sum(float(deposit.get('amount', 0)) for deposit in deposits)

        # Toplam withdraw miktarı
        total_withdrawals = sum(float(withdrawal.get('amount', 0)) for withdrawal in withdrawals)

        # Spot ve futures bakiyelerini al
        spot_balance = await get_spot_balance(api_key, secret_key)
        futures_balance = await get_futures_balance(api_key, secret_key)

        # Toplam bakiye hesaplama
        current_total_balance = (
                spot_balance.get('total_usdt_value', 0) +
                futures_balance.get('total_usdt_value', 0)
        )

        # Net yatırım hesaplama
        net_investment = total_deposits - total_withdrawals

        # Performans hesaplama
        # Eğer net yatırım 0 ise, mevcut bakiyeyi performans olarak kabul et
        unrealized_pnl = current_total_balance - net_investment if net_investment > 0 else current_total_balance
        performance_percentage = (unrealized_pnl / (net_investment if net_investment > 0 else 1) * 100)

        # Son 5 deposit ve withdrawalları sırala
        sorted_deposits = sorted(
            deposits,
            key=lambda x: x.get('insertTime', 0) if 'insertTime' in x else x.get('time', 0),
            reverse=True
        )[:10]

        sorted_withdrawals = sorted(
            withdrawals,
            key=lambda x: x.get('applyTime', 0) if 'applyTime' in x else x.get('time', 0),
            reverse=True
        )[:10]

        # Detaylı performans raporu
        detailed_report = f"""
            📊 Detaylı Performans Raporu 📊

            💰 Toplam Yatırımlar: {total_deposits:.2f} USDT
            💸 Toplam Çekimler: {total_withdrawals:.2f} USDT

            📈 Net Yatırım: {net_investment:.2f} USDT
            🏦 Mevcut Toplam Bakiye: {current_total_balance:.2f} USDT

            🔄 Gerçekleşmemiş Kar/Zarar: {unrealized_pnl:.2f} USDT
            📊 Performans Yüzdesi: %{performance_percentage:.2f}

            💵 Spot Bakiye: {spot_balance.get('total_usdt_value', 0):.2f} USDT
            💰 Vadeli Bakiye: {futures_balance.get('total_usdt_value', 0):.2f} USDT

            🔍 Son İşlemler:
            Son 5 Gelen Coin:
            {format_last_transactions(sorted_deposits, 10)}

            Son 5 Gönderilen Coin:
            {format_last_transactions(sorted_withdrawals, 10)}
            """
        return detailed_report

    except Exception as e:
        logger.error(f"Detaylı rapor alınırken hata: {e}", exc_info=True)
        return "Rapor alınırken bir hata oluştu."


def format_last_transactions(transactions, limit=5):
    if not transactions:
        return "İşlem bulunamadı."

    formatted_txns = []
    for txn in transactions[:limit]:
        # Farklı API yanıtları için esnek alan çekme
        amount = txn.get('amount', '0')
        coin = txn.get('coin', 'USDT')

        # Tarih bilgisini çekme
        if 'insertTime' in txn:
            timestamp = txn['insertTime'] / 1000
        elif 'applyTime' in txn:
            # String tarih ise
            if isinstance(txn['applyTime'], str):
                try:
                    timestamp = datetime.strptime(txn['applyTime'], '%Y-%m-%d %H:%M:%S').timestamp()
                except ValueError:
                    timestamp = datetime.now().timestamp()
            else:
                timestamp = txn['applyTime'] / 1000
        else:
            timestamp = datetime.now().timestamp()

        formatted_txns.append(
            f"- Miktar: {amount} {coin} "
            f"| Tarih: {datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')}"
        )

    return "\n".join(formatted_txns) if formatted_txns else "İşlem bulunamadı."


async def get_binance_balance(api_key, secret_key, user_id, retry_count=2):
    try:
        logger.info(f"Binance bakiyesi alınıyor. Kalan deneme hakkı: {retry_count}")

        # Ayarlanmış zaman damgasını al
        server_time = binance_time_manager.get_adjusted_timestamp()

        # Spot ve vadeli bakiye çekme işlemleri için ortak bir fonksiyon
        async def fetch_balance(base_url, endpoint, params, headers):
            params = dict(params)
            params["timestamp"] = str(server_time)  # <-- sadece burası yeter
            query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
            signature = get_binance_signature(secret_key, query_string)
            url = f"{base_url}{endpoint}?{query_string}&signature={signature}"

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    return await response.json()

        # Spot bakiye parametreleri
        spot_base_url = "https://api.binance.com"
        spot_endpoint = "/api/v3/account"
        spot_params = {'recvWindow': 10000}
        spot_headers = {"X-MBX-APIKEY": api_key}

        # Vadeli bakiye parametreleri
        futures_base_url = "https://fapi.binance.com"
        futures_endpoint = "/fapi/v2/balance"
        futures_params = {
            'timestamp': server_time,
            'recvWindow': 10000
        }
        futures_headers = {"X-MBX-APIKEY": api_key}

        # Spot ve vadeli bakiyeleri paralel çek
        spot_data, futures_data = await asyncio.gather(
            fetch_balance(spot_base_url, spot_endpoint, spot_params, spot_headers),
            fetch_balance(futures_base_url, futures_endpoint, futures_params, futures_headers)
        )

        # Hata kontrolleri
        def check_error(data, source):
            if isinstance(data, dict) and 'code' in data:
                error_code = data.get('code')
                error_message = data.get('msg', f'Bilinmeyen {source} hatası')

                if error_code == -1021:
                    logger.warning(f"{source} zaman damgası hatası. Kalan deneme: {retry_count}")
                    asyncio.create_task(binance_time_manager.sync_time())

                    if retry_count > 0:
                        return None  # Yeniden deneme
                    else:
                        raise Exception(f"{source} bağlantısında sürekli zaman damgası hatası")

                raise Exception(f"{source} Hata: {error_message}")
            return data

        # Hata kontrollerini yap
        try:
            spot_data = check_error(spot_data, "Spot")
            futures_data = check_error(futures_data, "Vadeli")
        except Exception as e:
            logger.error(str(e))
            return str(e), None

        # Eğer herhangi bir veri None ise yeniden dene
        if spot_data is None or futures_data is None:
            if retry_count > 0:
                await asyncio.sleep(1)
                return await get_binance_balance(api_key, secret_key, user_id, retry_count - 1)
            else:
                return "Binance bağlantısında sürekli zaman damgası hatası", None

        # Performans raporunu hesapla
        performance_metrics, performance_report = await calculate_comprehensive_performance(
            user_id, api_key, secret_key
        )

        # Spot ve vadeli bakiyeleri birleştir
        balance_message = "<b>Binance Bakiyeniz:</b>\n" + "---------------------------------\n"

        # Spot bakiyeleri kontrol et
        spot_total_balance = 0.0
        spot_usdt_balance = 0.0
        for account in spot_data.get('balances', []):
            if float(account.get('free', 0)) > 0 or float(account.get('locked', 0)) > 0:
                free_balance = float(account.get('free', 0))
                locked_balance = float(account.get('locked', 0))
                total_balance = free_balance + locked_balance

                # USDT bakiyesini ayrıca yakala
                if account['asset'] == 'USDT':
                    spot_usdt_balance = total_balance

                balance_message += (
                    f"<pre>"
                    f"{'Coin:':<5}{account['asset']}\n"
                    f"{'Serbest:':<10}{free_balance:<2.4f}\n"
                    f"{'Kilitli:':<10}{locked_balance:<2.4f}\n"
                    f"{'Toplam:':<10}{total_balance:<2.4f}\n"
                    f"</pre>"
                    f"---------------------------------\n"
                )
                spot_total_balance += total_balance

        # Vadeli bakiyeleri kontrol et
        futures_usdt_balance = 0.0
        futures_total_balance = 0.0
        for account in futures_data:
            balance = float(account['balance'])
            if account['asset'] == 'USDT':
                futures_usdt_balance = balance
                balance_message += (
                    f"<pre>"
                    f"{'Vadeli:':<5}USDT\n"
                    f"{'Bakiye:':<10}{futures_usdt_balance:.4f}\n"
                    f"</pre>"
                    f"---------------------------------\n"
                )
            futures_total_balance += balance

        # Toplam USDT bakiyesini hesapla
        total_usdt = (
            performance_metrics['current_total_balance']
            if performance_metrics
            else (spot_usdt_balance + futures_usdt_balance)
        )

        # Performans raporunu mesaja ekle
        balance_message += (
            f"<b>Toplam Varlıklar:</b> {total_usdt:<2.4f} USDT\n"
            f"Spot Toplam: {spot_total_balance:<2.4f} USDT\n"
            f"Vadeli Toplam: {futures_total_balance:<2.4f} USDT\n"
        )

        if performance_report:
            balance_message += f"\n{performance_report}"

        # Butonları oluştur
        keyboard = [
            [InlineKeyboardButton("Detaylı Performans", callback_data="detailed_performance")],
            [InlineKeyboardButton("Binance Menüsü", callback_data='binance_menu')],
            [InlineKeyboardButton("Ana Menü", callback_data='main_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        logger.info("Binance bakiyesi ve butonlar hazırlandı.")
        return balance_message, reply_markup

    except Exception as e:
        logger.error(f"Bakiye çekme genel hatası: {str(e)}", exc_info=True)
        return "Bakiye bilgileri alınırken bir hata oluştu.", None


def add_borsa_info(user_id, exchange, spot, vadeli, marjin, fonlama, kazan, bot):
    query = """
    INSERT INTO borsa_info (user_id, exchange, spot, vadeli, marjin, fonlama, kazan, bot)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """
    params = (user_id, exchange.lower(), spot, vadeli, marjin, fonlama, kazan, bot)
    return db_operation(query, params, operation='insert')


def get_borsa_info(user_id):
    query = "SELECT * FROM borsa_info WHERE user_id = ?"
    return db_operation(query, (user_id,), operation='select', fetch=True)


async def get_binance_trade_history(api_key: str, secret_key: str):
    try:
        logger.info("Binance işlem geçmişi alınıyor...")

        headers = {"X-MBX-APIKEY": api_key}

        end_time = datetime.now()
        start_time = end_time - timedelta(days=1)

        server_time = await get_binance_server_time()

        url = "https://fapi.binance.com/fapi/v1/userTrades"

        # ✅ Tipi baştan genişlet: Dict[str, Any] (ya da Dict[str, str | int])
        params_base: Dict[str, Any] = {
            "startTime": int(start_time.timestamp() * 1000),
            "endTime": int(end_time.timestamp() * 1000),
            "timestamp": int(server_time),
            "recvWindow": 5000,
        }

        query_string = urlencode(params_base)
        signature = hmac.new(
            secret_key.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        params_signed: Dict[str, Any] = dict(params_base)
        params_signed["signature"] = signature

        full_url = f"{url}?{urlencode(params_signed)}"

        async with aiohttp.ClientSession() as session:
            async with session.get(full_url, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"İşlem geçmişi HTTP hatası: {response.status} | {error_text}")

                    keyboard = [
                        [InlineKeyboardButton("Ana Menü", callback_data="main_menu")],
                        [InlineKeyboardButton("Binance Menüsü", callback_data="binance_menu")],
                    ]
                    return "İşlem geçmişi alınırken hata oluştu.", InlineKeyboardMarkup(keyboard)

                orders = await response.json()

        if isinstance(orders, dict) and "code" in orders:
            logger.error(f"Binance API hatası: {orders.get('code')} | {orders.get('msg')}")
            keyboard = [
                [InlineKeyboardButton("Ana Menü", callback_data="main_menu")],
                [InlineKeyboardButton("Binance Menüsü", callback_data="binance_menu")],
            ]
            return (
                f"İşlem geçmişi alınırken hata oluştu: {orders.get('msg', 'Bilinmeyen hata')}",
                InlineKeyboardMarkup(keyboard),
            )

        trade_history = "Sembol\tİşlem Tipi\tFiyat (USDT)\tMiktar\tToplam (USDT)\tTarih\n"
        trade_history += "-" * 80 + "\n"

        total_profit_loss = 0.0

        for order in orders:
            order_time = (order.get("time", 0) or 0) / 1000
            symbol = order.get("symbol", "")
            side = order.get("side", "")
            order_type = "Alış" if side == "BUY" else "Satış"

            price = float(order.get("price", 0) or 0)
            qty = float(order.get("qty", 0) or 0)
            total = price * qty
            date = datetime.fromtimestamp(order_time).strftime("%Y-%m-%d %H:%M:%S") if order_time else "-"

            total_profit_loss += (-total if order_type == "Alış" else total)

            trade_history += (
                f"{symbol:<10}\t{order_type:<10}\t"
                f"{price:<12.4f}\t{qty:<10.2f}\t{total:<12.2f}\t{date}\n"
            )

        trade_history += "-" * 80 + "\n"
        trade_history += f"Toplam Kar/Zarar: {total_profit_loss:.2f} USDT\n"

        keyboard = [
            [InlineKeyboardButton("Ana Menü", callback_data="main_menu")],
            [InlineKeyboardButton("Binance Menüsü", callback_data="binance_menu")],
        ]
        return trade_history, InlineKeyboardMarkup(keyboard)

    except Exception as e:
        logger.error(f"İşlem geçmişi alınırken hata oluştu: {str(e)}", exc_info=True)
        keyboard = [
            [InlineKeyboardButton("Ana Menü", callback_data="main_menu")],
            [InlineKeyboardButton("Binance Menüsü", callback_data="binance_menu")],
        ]
        return f"Bir hata oluştu: {str(e)}", InlineKeyboardMarkup(keyboard)

async def get_binance_open_orders(api_key, secret_key):
    try:
        logger.info("Binance açık emirler alınıyor...")
        # Sunucudan zaman damgasını al
        server_time = await get_binance_server_time()

        # Spot açık emirleri almak için
        spot_base_url = "https://api.binance.com"
        spot_endpoint = "/api/v3/openOrders"
        timestamp = str(server_time)

        spot_params = {
            "timestamp": timestamp
        }

        spot_query_string = '&'.join([f"{key}={value}" for key, value in spot_params.items()])
        spot_signature = get_binance_signature(secret_key, spot_query_string)

        spot_headers = {
            "X-MBX-APIKEY": api_key,
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{spot_base_url}{spot_endpoint}?{spot_query_string}&signature="
                f"{spot_signature}",
                    headers=spot_headers) as spot_response:
                spot_data = await spot_response.json()

                if 'code' in spot_data:
                    logger.error(f"Spot açık emirler alınırken hata oluştu: {spot_data['msg']}")
                    return f"Hata: {spot_data['msg']}", None

                open_orders = "<b>Binance Açık Emirler (Spot):</b>\n\n"
                for order in spot_data:
                    order_time = order['time'] / 1000  # Zamanı milisaniyeden saniyeye çevir
                    open_orders += (
                        f"<pre>"
                        f"Sembol: {order['symbol']}\n"
                        f"Emir Tipi: {'Alış' if order['side'] == 'BUY' else 'Satış'}\n"
                        f"Fiyat: {order['price']} USDT\n"
                        f"Miktar: {order['origQty']} {order['symbol'].split('USDT')[0]}\n"
                        f"Tarih: {datetime.fromtimestamp(order_time).strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"</pre>"
                        f"------------------------\n"
                    )

                # Vadeli açık emirleri almak için
                futures_base_url = "https://fapi.binance.com"
                futures_endpoint = "/fapi/v1/openOrders"

                futures_params = {
                    "timestamp": timestamp
                }

                futures_query_string = '&'.join([f"{key}={value}" for key, value in futures_params.items()])
                futures_signature = get_binance_signature(secret_key, futures_query_string)

                async with session.get(
                    f"{futures_base_url}{futures_endpoint}?"
                    f"{futures_query_string}&signature={futures_signature}",
                        headers=spot_headers) as futures_response:
                    futures_data = await futures_response.json()

                    if 'code' in futures_data:
                        logger.error(f"Vadeli açık emirler alınırken hata oluştu: {futures_data['msg']}")
                        return f"Hata: {futures_data['msg']}", None

                    open_orders += "<b>Binance Açık Emirler (Vadeli):</b>\n\n"
                    for order in futures_data:
                        order_time = order['time'] / 1000  # Zamanı milisaniyeden saniyeye çevir
                        open_orders += (
                            f"<pre>"
                            f"Sembol: {order['symbol']}\n"
                            f"Emir Tipi: {'Alış' if order['side'] == 'BUY' else 'Satış'}\n"
                            f"Fiyat: {order['price']} USDT\n"
                            f"Miktar: {order['origQty']} {order['symbol'].split('USDT')[0]}\n"
                            f"Tarih: {datetime.fromtimestamp(order_time).strftime('%Y-%m-%d %H:%M:%S')}\n"
                            f"</pre>"
                            f"------------------------\n"
                        )

                keyboard = [
                    [InlineKeyboardButton("Ana Menü", callback_data='main_menu')],
                    [InlineKeyboardButton("Binance Menüsü", callback_data='binance_menu')]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                return open_orders, reply_markup

    except Exception as e:
        logger.error(f"Açık emirler alınırken hata oluştu: {str(e)}")
        return "Bir hata oluştu.", None


async def get_binance_positions(api_key, secret_key):
    try:
        logger.info("Binance pozisyonları alınıyor...")
        server_time = await get_binance_server_time()

        # Spot pozisyonları almak için
        spot_base_url = "https://api.binance.com"
        spot_endpoint = "/api/v3/account"
        spot_params = f'timestamp={server_time}'
        spot_signature = get_binance_signature(secret_key, spot_params)

        async with aiohttp.ClientSession() as session:
            # Spot hesap bilgilerini çek
            async with session.get(
                    f"{spot_base_url}{spot_endpoint}?{spot_params}&signature={spot_signature}",
                    headers={"X-MBX-APIKEY": api_key}) as spot_response:
                spot_data = await spot_response.json()

                if 'code' in spot_data:
                    logger.error(f"Spot pozisyonlar alınırken hata oluştu: {spot_data['msg']}")
                    return [], []

                spot_positions = []
                for account in spot_data.get('balances', []):
                    free = float(account['free'])
                    locked = float(account['locked'])
                    total_balance = free + locked

                    # USDT, BNB ve çok düşük miktarları hariç tut
                    if (total_balance > 0.0001 and
                            account['asset'] not in ['USDT', 'BNB'] and
                            not account['asset'].startswith('LD')):
                        symbol = account['asset'] + 'USDT'
                        spot_positions.append({
                            'symbol': symbol,
                            'free_amount': str(total_balance),
                            'asset': account['asset'],
                            'callback_data': f'close_spot_position_{symbol}_{str(total_balance).replace(".", "_")}'
                        })

            # Vadeli pozisyonları almak için
            futures_base_url = "https://fapi.binance.com"
            futures_endpoint = "/fapi/v2/positionRisk"
            futures_params = {
                "timestamp": str(server_time)
            }
            futures_query_string = '&'.join([f"{key}={value}" for key, value in futures_params.items()])
            futures_signature = get_binance_signature(secret_key, futures_query_string)

            async with session.get(
                    f"{futures_base_url}{futures_endpoint}?{futures_query_string}&signature={futures_signature}",
                    headers={"X-MBX-APIKEY": api_key}) as futures_response:
                positions_data = await futures_response.json()

                if 'code' in positions_data:
                    logger.error(f"Vadeli pozisyonlar alınırken hata oluştu: {positions_data['msg']}")
                    return spot_positions, []

                futures_positions = []
                for position in positions_data:
                    position_amount = float(position['positionAmt'])

                    # Sadece açık pozisyonları ekle
                    if abs(position_amount) > 0.0001:
                        futures_positions.append({
                            'symbol': position['symbol'],
                            'position_amount': position['positionAmt'],
                            'entry_price': position['entryPrice'],
                            'unrealized_profit': position.get('unrealizedProfit', '0'),
                            'leverage': position.get('leverage', '1'),
                            'margin_type': position.get('marginType', 'CROSSED'),
                            'side': 'LONG' if position_amount > 0 else 'SHORT',
                            'liquidation_price': position.get('liquidationPrice', '0'),
                            'isolated_wallet': position.get('isolatedWallet', '0'),
                            'callback_data': f'close_futures_position_'
                                             f'{position["symbol"]}_{position["positionAmt"].replace(".", "_")}'
                        })

        logger.info(f"Spot Pozisyonları: {len(spot_positions)}, Futures Pozisyonları: {len(futures_positions)}")
        return spot_positions, futures_positions

    except Exception as e:
        logger.error(f"Pozisyonlar alınırken genel hata: {str(e)}", exc_info=True)
        return [], []


async def close_spot_position(api_key, secret_key, symbol, amount):
    try:
        logger.info(f"{symbol} sembolü için spot pozisyon kapatılıyor...")
        # Spot pozisyon kapatma işlemleri

        # Sunucudan zaman damgasını al
        server_time = await get_binance_server_time()
        base_url = "https://api.binance.com"
        endpoint = "/api/v3/order"

        params = {
            "symbol": symbol,
            "side": "SELL",
            "type": "MARKET",
            "quantity": amount,
            "timestamp": str(server_time)
        }

        query_string = '&'.join([f"{key}={value}" for key, value in params.items()])
        signature = get_binance_signature(secret_key, query_string)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                    f"{base_url}{endpoint}?{query_string}&signature={signature}",
                    headers={"X-MBX-APIKEY": api_key}) as response:
                result = await response.json()

                if 'code' in result:
                    logger.error(f"Spot pozisyon kapatma işlemi sırasında hata oluştu: {result['msg']}")
                    return f"Hata: {result['msg']}"

                logger.info(f"Spot pozisyon başarıyla kapatıldı: {result}")
                return f"{symbol} sembolündeki spot pozisyonunuz başarıyla kapatıldı."

    except Exception as e:
        logger.error(f"Spot pozisyon kapatılırken hata oluştu: {str(e)}")
        return f"Bir hata oluştu: {str(e)}"


async def close_futures_position(api_key, secret_key, symbol, position_amount):
    try:
        logger.info(f"{symbol} sembolü için vadeli pozisyon kapatılıyor...")

        # Sunucudan zaman damgasını al
        server_time = await get_binance_server_time()
        futures_base_url = "https://fapi.binance.com"
        endpoint = "/fapi/v1/order"

        # Pozisyon kapatma emri için gerekli parametreler
        params = {
            "symbol": symbol,
            "side": "SELL" if float(position_amount) > 0 else "BUY",  # Pozisyon miktarına göre emir yönü belirlenir
            "type": "MARKET",
            "quantity": abs(float(position_amount)),  # Pozisyon miktarının mutlak değeri
            "timestamp": str(server_time)
        }

        # İmza oluştur
        query_string = '&'.join([f"{key}={value}" for key, value in params.items()])
        signature = get_binance_signature(secret_key, query_string)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                    f"{futures_base_url}{endpoint}?{query_string}&signature={signature}",
                    headers={"X-MBX-APIKEY": api_key}) as response:
                result = await response.json()

                if 'code' in result:
                    logger.error(f"Vadeli pozisyon kapatma işlemi sırasında hata oluştu: {result['msg']}")
                    return f"Hata: {result['msg']}"

                logger.info(f"Vadeli pozisyon başarıyla kapatıldı: {result}")
                return f"{symbol} sembolündeki vadeli pozisyonunuz başarıyla kapatıldı."

    except Exception as e:
        logger.error(f"Vadeli pozisyon kapatılırken hata oluştu: {str(e)}")
        return f"Bir hata oluştu: {str(e)}"


async def update_binance_user_balances():
    try:
        query = "SELECT user_id, api_key, secret_key FROM api_key WHERE exchange = 'binance'"
        users = db_operation(query, operation='select', fetch=True)

        logger.info(f"Toplam {len(users)} kullanıcı için bakiye güncellemesi yapılacak")

        # Her kullanıcı için ayrı ayrı işlem yap
        for user in users:
            user_id, api_key, secret_key = user

            logger.info(f"Kullanıcı {user_id} için bakiye güncelleme başlatıldı")

            try:
                # Spot ve Futures bakiye bilgilerini çek
                spot_response = await get_spot_balance(api_key, secret_key, None)
                futures_response = await get_futures_balance(api_key, secret_key, None)

                # Log ekledik
                logger.info(f"Spot Bakiye: {spot_response}")
                logger.info(f"Futures Bakiye: {futures_response}")

                # Toplam bakiye bilgilerini hazırla
                balance_info = {
                    'spot_balances': spot_response.get('balances', []),
                    'spot_total_usdt': spot_response.get('total_usdt_value', 0),
                    'futures_balances': futures_response.get('balances', []),
                    'futures_positions': futures_response.get('positions', []),
                    'futures_total_usdt': futures_response.get('total_usdt_value', 0)
                }

                # Veritabanına kaydet
                if await save_binance_balance_to_db(user_id, balance_info):
                    logger.info(f"Kullanıcı {user_id} için Binance bakiyesi güncellendi.")
                else:
                    logger.error(f"Kullanıcı {user_id} için bakiye güncellenemedi.")

            except Exception as user_error:
                logger.error(f"Kullanıcı {user_id} için bakiye çekme hatası: {str(user_error)}", exc_info=True)

            logger.info(f"Kullanıcı {user_id} için bakiye güncelleme tamamlandı")

    except Exception as e:
        logger.error(f"Tüm kullanıcı bakiyeleri güncellenirken hata: {str(e)}", exc_info=True)


async def get_spot_balance(api_key, secret_key, outer_session=None):
    try:
        logger.info("Spot bakiye çekme başlatıldı")

        # Sunucu zamanını al
        server_time = await get_binance_server_time()

        # Spot bakiye kontrol URL'si
        base_url = "https://api.binance.com"
        endpoint = "/api/v3/account"

        # İstek parametreleri
        params = {
            'timestamp': server_time,
            'recvWindow': 5000
        }

        # Query string oluştur
        query_string = '&'.join([f"{key}={value}" for key, value in params.items()])
        signature = get_binance_signature(secret_key, query_string)

        # Tam URL
        full_url = f"{base_url}{endpoint}?{query_string}&signature={signature}"

        # Headers
        headers = {
            'X-MBX-APIKEY': api_key
        }

        # Oturum yönetimi ve veri çekme
        async def fetch_balances(current_session):
            async with current_session.get(full_url, headers=headers) as response:
                return await response.json()

        # Oturum yönetimi
        if outer_session is None:
            async with aiohttp.ClientSession() as new_session:
                data = await fetch_balances(new_session)
        else:
            data = await fetch_balances(outer_session)

        # Bakiyesi olan coinleri filtrele
        balances = []
        usdt_balance = 0.0

        for balance in data.get('balances', []):
            free = float(balance['free'])
            locked = float(balance['locked'])

            if free > 0 or locked > 0:
                balance_info = {
                    'asset': balance['asset'],
                    'free': str(free),
                    'locked': str(locked)
                }
                balances.append(balance_info)

                # USDT bakiyesini ayrıca yakala
                if balance['asset'] == 'USDT':
                    usdt_balance = free + locked

        # USDT değerini hesapla
        spot_value_result = calculate_total_spot_value(balances)

        logger.info(f"Spot Bakiye Bilgileri: {len(balances)} adet coin")

        return {
            'balances': balances,
            'total_usdt_value': spot_value_result['total_usdt_value'],
            'usdt_balance': usdt_balance
        }

    except Exception as e:
        logger.error(f"Spot Bakiye Çekme Hatası: {str(e)}")
        return {
            'balances': [],
            'total_usdt_value': 0,
            'usdt_balance': 0
        }


def calculate_total_spot_value(balances):
    try:
        client = Client()
        total_usdt = 0
        usdt_balance = 0

        # USDT bakiyesini önce kontrol et
        usdt_balances = [b for b in balances if b['asset'] == 'USDT']
        if usdt_balances:
            usdt_balance = float(usdt_balances[0]['free']) + float(usdt_balances[0]['locked'])
            total_usdt += usdt_balance

        for balance in balances:
            # USDT'yi zaten işledik, atla
            if balance['asset'] == 'USDT':
                continue

            # Sıfır bakiyeli varlıkları atla
            balance_amount = float(balance['free']) + float(balance['locked'])
            if balance_amount == 0:
                continue

            # Diğer varlıklar için fiyat hesaplaması
            current_symbol = None
            try:
                current_symbol = balance['asset'] + 'USDT'

                # Güncel fiyatı al
                ticker = client.get_symbol_ticker(symbol=current_symbol)
                price = float(ticker['price'])

                # USDT cinsinden değeri hesapla
                usdt_value = balance_amount * price
                total_usdt += usdt_value
            except (BinanceAPIException, BinanceRequestException):
                # Sembol bulunamazsa veya API hatası varsa atla
                continue
            except ValueError:
                # Sayısal dönüşüm hatası durumunda atla
                continue
            except Exception as inner_error:
                # Beklenmedik bir hata durumunda log at ve atla
                symbol_log = current_symbol or f"Unknown-{balance['asset']}"
                logger.warning(f"Sembol {symbol_log} için hesaplama hatası: {str(inner_error)}")
                continue

        return {
            'balances': balances,
            'total_usdt_value': total_usdt,
            'usdt_balance': usdt_balance
        }

    except Exception as e:
        logger.error(f"Spot Değeri Hesaplama Hatası: {str(e)}")
        return {
            'balances': balances,
            'total_usdt_value': 0,
            'usdt_balance': 0
        }


async def get_futures_balance(api_key, secret_key, outer_session=None):
    try:
        # Ayarlanmış zaman damgasını al
        server_time = binance_time_manager.get_adjusted_timestamp()

        # Futures bakiye kontrol URL'si
        base_url = "https://fapi.binance.com"
        endpoint = "/fapi/v2/balance"  # V2 endpoint'i kullanılıyor

        # İstek parametreleri
        params = {
            'timestamp': server_time,
            'recvWindow': 5000
        }

        # Query string oluştur
        query_string = '&'.join([f"{key}={value}" for key, value in params.items()])
        signature = get_binance_signature(secret_key, query_string)

        # Tam URL
        full_url = f"{base_url}{endpoint}?{query_string}&signature={signature}"

        # Headers
        headers = {
            'X-MBX-APIKEY': api_key
        }

        # Oturum yönetimi ve veri çekme
        async def fetch_balances(current_session):
            try:
                async with current_session.get(full_url, headers=headers) as response:
                    # HTTP yanıt kodunu kontrol et
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Futures bakiye çekme HTTP hatası: {response.status}, {error_text}")
                        return None

                    return await response.json()
            except aiohttp.ClientError as client_error:
                logger.error(f"Futures bakiye ağ hatası: {client_error}")
                return None

        # Pozisyonları çekme
        async def fetch_positions(current_session):
            try:
                positions_url = f"{base_url}/fapi/v2/positionRisk"
                async with current_session.get(
                        f"{positions_url}?{query_string}&signature={signature}",
                        headers=headers
                ) as positions_response:
                    # HTTP yanıt kodunu kontrol et
                    if positions_response.status != 200:
                        error_text = await positions_response.text()
                        logger.error(f"Futures pozisyon çekme HTTP hatası: {positions_response.status}, {error_text}")
                        return None

                    return await positions_response.json()
            except aiohttp.ClientError as client_error:
                logger.error(f"Futures pozisyon ağ hatası: {client_error}")
                return None

        # Oturum yönetimi
        if outer_session is None:
            async with aiohttp.ClientSession() as new_session:
                data = await fetch_balances(new_session)
                positions_data = await fetch_positions(new_session)
        else:
            data = await fetch_balances(outer_session)
            positions_data = await fetch_positions(outer_session)

        # Veri ve pozisyon çekme hatası kontrolü
        if data is None or positions_data is None:
            logger.error("Futures bakiye veya pozisyon verisi alınamadı")
            return {
                'balances': [],
                'positions': [],
                'total_usdt_value': 0,
                'usdt_balance': 0
            }

        # Hata kontrolü
        if isinstance(data, dict) and 'code' in data:
            error_code = data.get('code')
            error_message = data.get('msg', 'Bilinmeyen hata')

            # Zaman damgası hatası için özel işlem
            if error_code == -1021:
                logger.warning("Vadeli bakiye zaman damgası hatası. Yeniden deneniyor...")

            logger.error(f"Futures bakiye çekme hatası: {error_message}")
            return {
                'balances': [],
                'positions': [],
                'total_usdt_value': 0,
                'usdt_balance': 0
            }

        # Bakiyesi olan coinleri filtrele
        balances = []
        usdt_balance = 0.0

        # Veri yapısını kontrol et ve dönüştür
        if isinstance(data, list):
            for balance in data:
                try:
                    # Güvenli dönüşüm
                    balance_amount = float(balance.get('balance', 0))

                    if balance_amount > 0:
                        balance_info = {
                            'asset': balance.get('asset', ''),
                            'balance': str(balance_amount),
                            'cross_wallet_balance': balance.get('crossWalletBalance', '0'),
                            'available_balance': balance.get('availableBalance', '0')
                        }
                        balances.append(balance_info)

                        # USDT bakiyesini kesin olarak yakala
                        if balance.get('asset') == 'USDT':
                            usdt_balance = balance_amount
                except (ValueError, TypeError) as e:
                    logger.warning(f"Bakiye dönüşüm hatası: {e}")
                    continue

        # Pozisyonları işle
        positions = []
        if isinstance(positions_data, list):
            for position in positions_data:
                try:
                    # Pozisyon miktarı sıfır değilse işle
                    position_amount = float(position.get('positionAmt', 0))
                    if abs(position_amount) > 0.0001:
                        position_info = {
                            'symbol': position.get('symbol', ''),
                            'amount': str(position_amount),
                            'entry_price': position.get('entryPrice', '0'),
                            'unrealized_profit': position.get('unrealizedProfit', '0'),
                            'margin_type': position.get('marginType', 'CROSSED'),
                            'leverage': position.get('leverage', '1'),
                            'side': 'LONG' if position_amount > 0 else 'SHORT',
                            'liquidation_price': position.get('liquidationPrice', '0')
                        }
                        positions.append(position_info)
                except (ValueError, TypeError) as e:
                    logger.warning(f"Pozisyon işleme hatası: {e}")
                    continue

        # USDT değerini hesapla
        try:
            total_futures_usdt = calculate_total_futures_usdt_value(balances, positions)
        except Exception as calc_error:
            logger.error(f"Futures USDT değeri hesaplama hatası: {calc_error}")
            total_futures_usdt = 0

        logger.info(f"Futures Bakiye Bilgileri: {len(balances)} adet coin, {len(positions)} adet pozisyon")

        return {
            'balances': balances,
            'positions': positions,
            'total_usdt_value': total_futures_usdt,
            'usdt_balance': usdt_balance
        }

    except Exception as e:
        logger.error(f"Futures Bakiye Çekme Genel Hatası: {str(e)}", exc_info=True)
        return {
            'balances': [],
            'positions': [],
            'total_usdt_value': 0,
            'usdt_balance': 0
        }


def calculate_total_futures_usdt_value(balances, positions=None):
    try:
        client = Client()  # USDT değerlerini hesaplamak için
        total_usdt = 0

        for balance in balances:
            current_symbol = None
            try:
                current_symbol = balance['asset'] + 'USDT'
                # Güncel fiyatı al
                ticker = client.get_symbol_ticker(symbol=current_symbol)
                price = float(ticker['price'])
                balance_amount = float(balance['balance'])

                # USDT cinsinden değeri hesapla
                usdt_value = balance_amount * price
                total_usdt += usdt_value
            except (BinanceAPIException, BinanceRequestException):
                # Sembol bulunamazsa veya API hatası varsa atla
                continue
            except ValueError:
                # Sayısal dönüşüm hatası durumunda atla
                continue
            except Exception as inner_error:
                # Beklenmedik bir hata durumunda log at ve atla
                symbol_log = current_symbol or f"Unknown-{balance['asset']}"
                logger.warning(f"Sembol {symbol_log} için hesaplama hatası: {str(inner_error)}")
                continue

        # Pozisyonların USDT değerini de hesapla (isteğe bağlı)
        if positions:
            for position in positions:
                current_symbol = None
                try:
                    current_symbol = position['symbol']
                    amount = float(position['amount'])
                    entry_price = float(position.get('entry_price', 0))

                    # Pozisyonun USDT cinsinden değerini hesapla
                    position_usdt_value = abs(amount * entry_price)
                    total_usdt += position_usdt_value
                except ValueError:
                    # Sayısal dönüşüm hatası durumunda atla
                    continue
                except Exception as inner_error:
                    # Beklenmedik bir hata durumunda log at ve atla
                    symbol_log = current_symbol or f"Unknown-{position.get('symbol', 'Position')}"
                    logger.warning(f"Pozisyon {symbol_log} için hesaplama hatası: {str(inner_error)}")
                    continue

        return total_usdt

    except Exception as e:
        logger.error(f"Futures USDT Değeri Hesaplama Hatası: {str(e)}")
        return 0


async def save_binance_balance_to_db(user_id, balance_info):
    try:
        # Username'i api_key tablosundan al
        query_username = """
            SELECT username 
            FROM api_key 
            WHERE user_id = ? AND exchange = 'binance'
            LIMIT 1
        """
        username_result = db_operation(query_username, (user_id,), operation='select', fetch=True)

        if not username_result:
            logger.error(f"Kullanıcı ID {user_id} için Binance API bilgisi bulunamadı.")
            return False

        username = username_result[0][0]

        # USDT bakiyesini daha güvenli bir şekilde yakala
        spot_usdt = balance_info.get('spot_total_usdt', 0)
        futures_usdt = balance_info.get('futures_total_usdt', 0)

        # Spot ve vadeli USDT bakiyelerini manuel olarak kontrol et
        spot_usdt_balance = next(
            (float(balance.get('free', 0)) + float(balance.get('locked', 0))
             for balance in balance_info.get('spot_balances', [])
             if balance.get('asset') == 'USDT'),
            0
        )

        futures_usdt_balance = next(
            (float(balance.get('balance', 0))
             for balance in balance_info.get('futures_balances', [])
             if balance.get('asset') == 'USDT'),
            0
        )

        # Log ekle
        logger.info(f"Spot USDT: {spot_usdt}, Spot Manuel USDT: {spot_usdt_balance}")
        logger.info(f"Futures USDT: {futures_usdt}, Futures Manuel USDT: {futures_usdt_balance}")

        # Toplam USDT bakiyesi için manuel bakiyeleri kullan
        total_usdt = spot_usdt_balance + futures_usdt_balance

        # Spot bakiyeleri
        spot_balances_text = json.dumps(balance_info.get('spot_balances', []))

        # Vadeli bakiyeleri
        futures_balances_text = json.dumps(balance_info.get('futures_balances', []))

        # Vadeli pozisyonlar
        futures_positions_text = json.dumps(balance_info.get('futures_positions', []))

        # İlk kayıt kontrolü
        query_initial = """
            SELECT ilk_toplam 
            FROM borsa_info 
            WHERE user_id = ? AND exchange = 'binance'
        """
        initial_total = db_operation(query_initial, (user_id,), operation='select', fetch=True)

        # İlk toplam değeri belirle
        if not initial_total or initial_total[0][0] is None or float(initial_total[0][0]) == 0:
            ilk_toplam = total_usdt
            kar_zarar_toplam = 0
            kar_zarar = "NÖTR"
        else:
            ilk_toplam = float(initial_total[0][0])
            kar_zarar_toplam = total_usdt - ilk_toplam
            kar_zarar = "KAR" if kar_zarar_toplam > 0 else "ZARAR"

        # Kayıt veya güncelleme işlemi
        if not initial_total:
            # İlk kayıt
            query = """
            INSERT INTO borsa_info (
                user_id, username, exchange,
                spot, vadeli,
                guncel_toplam, ilk_toplam,
                kar_zarar_toplam, kar_zarar,
                spot_balances, futures_balances, futures_positions,
                son_guncelleme
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """
            params = (
                user_id, username, 'binance',
                spot_usdt_balance, futures_usdt_balance,
                total_usdt, ilk_toplam,
                kar_zarar_toplam, kar_zarar,
                spot_balances_text, futures_balances_text, futures_positions_text
            )

            db_operation(query, params, operation='insert')
        else:
            # Güncelleme
            query = """
            UPDATE borsa_info SET 
                username = ?,
                spot = ?,
                vadeli = ?,
                guncel_toplam = ?,
                ilk_toplam = ?,
                kar_zarar_toplam = ?,
                kar_zarar = ?,
                spot_balances = ?,
                futures_balances = ?,
                futures_positions = ?,
                son_güncelleme = CURRENT_TIMESTAMP
            WHERE user_id = ? AND exchange = 'binance'
            """
            params = (
                username,
                spot_usdt_balance, futures_usdt_balance,
                total_usdt, ilk_toplam,
                kar_zarar_toplam, kar_zarar,
                spot_balances_text, futures_balances_text, futures_positions_text,
                user_id
            )

            db_operation(query, params, operation='update')

        logger.info(f"Kullanıcı {user_id} için Binance bakiyesi başarıyla kaydedildi.")
        return True

    except Exception as e:
        logger.error(f"Binance bakiye kaydetme hatası: {str(e)}", exc_info=True)
        return False
