# olimpos_Cripto_Bot.py ANA kodumuz
from telegram import (ReplyKeyboardMarkup, KeyboardButton, Bot, Update, InlineKeyboardMarkup,
    InlineKeyboardButton, error)
import telegram
from telegram.ext import (CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler,
    filters, CallbackContext, Application, ContextTypes )
from olimpos_admin import (handle_limit_user_time, show_user_list, handle_admin_buttons, show_dynamic_exchange_menu,
    set_chart_setting_value, toggle_permission, manage_permissions_menu, handle_viewing_apis_state, handle_user_selection,
back_to_admin_menu, show_user_channel_info, add_time_to_user, process_user_delete_channel, process_time_input,
end_user_usage, admin_handle_exchange_selection,handle_channel_selection,handle_start_date, handle_end_date,
handle_duration_input, send_message_menu, get_active_exchanges)
from olimpos_channel import (handle_channel_buttons, process_add_channel, process_delete_channel,
    process_send_message_to_channel, process_send_message_to_all_channels, process_send_message_to_users)
from email_checker import EmailChecker
from signal_merkezi import signal_handler
from config.api_action_settings import handle_exchange_selection

from settings.execution.mexc_al_sat import handle_emergency_close_callback

from settings.bitget_api_ayarlari import bitget_ayarlari_menu, handle_bitget_actions, update_bitget_user_balances
from settings.okx_api_ayarlari import okx_ayarlari_menu, handle_okx_actions, update_okx_user_balances
from settings.binance_api_ayarlari import binance_ayarlari_menu, handle_binance_actions, update_binance_user_balances
from settings.mexc_api_ayarlari import mexc_ayarlari_menu, handle_mexc_actions, update_mexc_user_balances
from settings.bybit_api_ayarlari import bybit_ayarlari_menu, handle_bybit_actions, update_bybit_user_balances
from settings.bitmart_api_ayarlari import handle_bitmart_actions

from strategies.strateji_ayarlari import (handle_maliyet_cek_selection, back_to_strategy_settings,
    handle_maks_emir_input, process_terial_stop_params, handle_sl_tp_emir_choice, show_strategy_settings,
back_to_tp_input, strategy_settings, handle_stop_loss_selection, process_take_profit, process_custom_tp, handle_tp_input,
handle_stop_loss_percentage, process_terial_stop, handle_lot_selection_method, handle_margin_selection, handle_lot_percentage_input,
handle_custom_lot_percentage, handle_lot_percentage_selection, handle_custom_lot_percentage_input, confirm_lot_percentage,
confirm_custom_lot_percentage, handle_lot_number_input, back_to_lot_selection, handle_leverage_input, confirm_lot_amount,
Message, set_lot, set_leverage, set_margin, set_tp, set_stop_loss_strategy, set_take_profit, back_to_terial_stop, set_sl_tp_emir,
set_email_settings, set_maliyet_cek, set_maks_emir, toggle_active_status, admin_contact, handle_system_email, handle_custom_email,
handle_custom_email_input, back_to_email_settings, handle_custom_email_password, handle_lot_input)
from strategies.alarm_strateji import OlimposStrategy
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from typing import Union, Optional
from data.olimpos_data import (db_operation, BOT_TOKEN, ADMIN_USER_ID, sync_telegram_notification_channels,
    EXCHANGES_REQUIRING_PASSPHRASE, get_api_key, ADMIN_PASSWORD, AdminLevel, update_user_status, SUPPORTED_EXCHANGES,
delete_user as delete_user_db, create_database, )
from logger_config import setup_logging
import asyncio
import  logging
from config.constants import State
from datetime import time, datetime
import pytz
from settings.execution.equity_service import EquityService
import signal
import subprocess, sys
from pathlib import Path
import hashlib

def update_requirements_if_needed():
    today = datetime.now()
    if today.weekday() == 5:
        req = subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True)
        Path("requirements.txt").write_text(req, encoding="utf-8")
        print("requirements.txt haftalık olarak güncellendi.")


# Bot başlatılırken çağır
update_requirements_if_needed()

# setup_logging fonksiyonunu ana kodda çağırırken
logger = setup_logging('ANA_Kodumuz_main_logger')
# logger.info("ANA Kodumuz Bu bir bilgi mesajıdır. Uygulama başlatılıyor...")

# PTB uyarılarını sustur
import warnings
from telegram.warnings import PTBUserWarning
warnings.filterwarnings('ignore', category=PTBUserWarning)

# Diğer modüller için logging seviyesini ayarla
logging.getLogger('telegram').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)
# OlimposStrategy nesnesini oluştur
olimpos_strategy = OlimposStrategy()




if sys.platform.startswith('win'):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def ensure_super_admin_exists():
    # 1) username'i tek sefer üret (çift SuperAdmin_ olmasın)
    username = f"SuperAdmin_{ADMIN_USER_ID}"

    # 2) password'u hashle (admin_users ile tutarlı)
    hashed_password = hashlib.sha256(str(ADMIN_PASSWORD).encode()).hexdigest()

    # 3) PostgreSQL uyumlu UPSERT (duplicate key bitirir)
    upsert_query = """
        INSERT INTO admin_users (user_id, username, password, level)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE
        SET username = EXCLUDED.username,
            password = EXCLUDED.password,
            level = EXCLUDED.level
    """

    db_operation(
        upsert_query,
        (ADMIN_USER_ID, username, hashed_password, AdminLevel.SUPER_ADMIN.value),
        operation="insert"
    )


async def start(update: Update, context: CallbackContext) -> int:
    logging.debug(f"start fonksiyonu çağrıldı. Update: {update}, Context: {context}")

    # Kullanıcı bilgilerini daha esnek al
    user = update.effective_user
    chat_id = update.effective_chat.id

    # Kullanıcı adını dinamik olarak belirle
    user_name = (
            user.full_name or
            user.username or
            str(user.id)
    )

    try:
        # Merhaba mesajı ve dinamik kullanıcı adı
        welcome_message = f"Merhaba {user_name}! 🤖\nBot'a hoş geldiniz."

        # ReplyKeyboardMarkup oluşturma
        keyboard = [
            [KeyboardButton("🚀 Başlat"), KeyboardButton("ℹ️ Bilgi")],
            [KeyboardButton("📞 İletişim"), KeyboardButton("⚙️ Ayarlar")]
        ]
        reply_markup = ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True,
            one_time_keyboard=False,
        )

        # Mesaj gönderme işlemi
        await context.bot.send_message(
            chat_id=chat_id,
            text=welcome_message,
            reply_markup=reply_markup
        )

        # Callback query varsa yanıtla
        if update.callback_query:
            await update.callback_query.answer()

        # Ana menüyü göster
        return await show_main_menu(update, context)

    # DÜZELTME: Genel Exception yerine spesifik Telegram hatasını yakala.
    except telegram.error.TelegramError as tg_error:
        logger.error(f"Start fonksiyonunda Telegram hatası oluştu: {tg_error}")

        # Hata durumunda bile kullanıcıya bir mesaj gönder
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text="Başlatma sırasında bir Telegram hatası oluştu. Lütfen tekrar deneyin."
            )
        except Exception as send_error:
            logger.error(f"Hata mesajı gönderme hatası: {send_error}")

        return State.END


# Callback query handler eklemeniz gerekecek
async def start_bot_callback(update: Update, context) -> int:
    if update.callback_query:
        await update.callback_query.answer()
    return await show_main_menu(update, context)

# Buton tıklamalarını yakalama handler'ı
async def handle_button_click(update: Update, context) -> int:
    chat_id = update.effective_chat.id if update.effective_chat else None
    try:
        button_text = None
        # Farklı update türleri için chat_id ve button_text kontrolü
        if update.message:
            button_text = update.message.text
        elif update.callback_query:
            button_text = update.callback_query.data

        # Chat ID kontrolü
        if chat_id is None:
            logger.warning("Chat ID alınamadı")
            return State.MAIN_MENU

        # Ayarlar butonuna tıklandığında
        if button_text == "⚙️ Ayarlar":
            keyboard = [
                [InlineKeyboardButton("Borsa Strateji Ayarları", callback_data="strateji_ayarlari")],
                [InlineKeyboardButton("Ana Menüye Dön", callback_data="main_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await context.bot.send_message(
                chat_id=chat_id,
                text="Ayarlar menüsünü seçtiniz. Lütfen bir seçim yapın:",
                reply_markup=reply_markup
            )
            return State.STRATEGY_WAITING_FOR_EXCHANGE_SELECTION

        elif button_text == "🚀 Başlat":
            await context.bot.send_message(chat_id=chat_id, text="Bot başlatılıyor...")
            return await start(update, context)

        elif button_text == "ℹ️ Bilgi":
            return await info_page(update, context)

        elif button_text == "📞 İletişim":
            return await contact_page(update, context)

        return State.MAIN_MENU

    # DÜZELTME: Genel Exception yerine spesifik Telegram hatasını yakala.
    except telegram.error.TelegramError as tg_error:
        logger.error(f"Buton tıklama handler hatası: {tg_error}")
        try:  # type: ignore
            await context.bot.send_message(
                chat_id=chat_id,
                text="Bir hata oluştu. Lütfen tekrar deneyin."
            )
        except telegram.error.TelegramError as send_error:
            logger.error(f"Hata mesajı gönderme hatası: {send_error}")
        return State.MAIN_MENU

# Veritabanından admin bilgilerini dinamik olarak çeken fonksiyon
async def get_dynamic_admins(application: Application) -> dict:
    try:
        admin_query = """
            SELECT user_id, username, level
            FROM admin_users
            ORDER BY level ASC
        """
        admins_data = await asyncio.to_thread(
            db_operation, admin_query, operation='select', fetch_all=True
        )

        if not admins_data:
            print("Admin verisi bulunamadı!")
            return {}

        dynamic_admins = {}

        for user_id, db_username, level in admins_data:
            username = db_username

            # Telegram'dan kullanıcı bilgisi al (PTB)
            try:
                chat = await application.bot.get_chat(user_id)
                username = getattr(chat, "username", None) or db_username
            except Exception as user_fetch_error:
                print(f"Kullanıcı bilgileri alınamadı (user_id: {user_id}): {user_fetch_error}")

            telegram_link = f"https://t.me/{username}" if username else f"tg://user?id={user_id}"

            if level == 0:
                admin_label = "🌟 Süper Admin"
            elif level == 1:
                admin_label = "👥 Üst Düzey Admin"
            else:
                admin_label = "👤 Admin"

            dynamic_admins[str(user_id)] = {
                "username": f"@{username}" if username else f"User {user_id}",
                "telegram_link": telegram_link,
                "level": level,
                "label": admin_label
            }

        return dynamic_admins

    except Exception as error3:
        print(f"Dinamik admin bilgileri alınırken hata: {error3}")
        return {}


# İletişim sayfası fonksiyonunu güncelle
async def contact_page(update: Update, context: CallbackContext) -> int:
    try:
        # Dinamik admin bilgilerini al
        application = context.application
        dynamic_admins = await get_dynamic_admins(application)
        application.bot_data["dynamic_admins"] = dynamic_admins

        # Debug: Admin bilgilerini yazdır
        print("Gelen admin bilgileri:", dynamic_admins)

        # Admin listesi için inline keyboard oluştur
        keyboard = []

        # Debug: Döngüye giriş kontrolü
        if not dynamic_admins:
            await update.message.reply_text("Hiç admin bulunamadı.")
            return State.MAIN_MENU

        for admin_key, admin_info in dynamic_admins.items():
            # Kullanıcı adını çıkar (@ işaretini kaldır)
            display_username = admin_info.get('username', '').lstrip('@')

            # Eğer username yoksa, user_id kullan
            if not display_username:
                display_username = admin_key

            keyboard.append([
                InlineKeyboardButton(
                    f"{display_username} ({admin_info.get('label', 'Admin')})",
                    callback_data=f"contact_{admin_key}"
                )
            ])

        # Geri dön butonu ekle
        keyboard.append([
            InlineKeyboardButton("🔙 Geri Dön", callback_data="main_menu")
        ])

        # Debug: Keyboard oluşturma kontrolü
        print("Oluşturulan keyboard:", keyboard)

        reply_markup = InlineKeyboardMarkup(keyboard)

        # Mesajı gönder
        await update.message.reply_text(
            "📞 İletişim Sayfası\nLütfen iletişim kurmak istediğiniz admini seçin:",
            reply_markup=reply_markup
        )

        return State.CONTACT

    except Exception as error4:
        # Detaylı hata bilgisi
        logger.error(f"Contact page error: {str(error4)}", exc_info=True)
        await update.message.reply_text(f"Bir hata oluştu: {str(error4)}")
        return State.MAIN_MENU


async def contact_page_callback(update: Update, context: CallbackContext) -> State:
    try:
        query = update.callback_query
        if query is None:
            logger.warning("Callback query boş")
            return State.MAIN_MENU

        await query.answer()

        # Dinamik admin bilgilerini al
        application = context.application
        dynamic_admins = await get_dynamic_admins(application)
        application.bot_data["dynamic_admins"] = dynamic_admins

        if not dynamic_admins:
            await query.edit_message_text(
                "📵 Şu anda hiçbir admin bulunamadı.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Ana Menü", callback_data="main_menu")]
                ])
            )
            return State.MAIN_MENU

        # Admin seçim tuşları oluşturma
        keyboard = []
        for admin_key, admin_info in dynamic_admins.items():
            username = admin_info.get('username', admin_key)
            label = admin_info.get('label', 'Admin')

            keyboard.append([
                InlineKeyboardButton(
                    f"👤 {username} ({label})",
                    callback_data=f"contact_{admin_key}"
                )
            ])

        # Ana menü butonu ekle
        keyboard.append([
            InlineKeyboardButton("🏠 Ana Menü", callback_data="main_menu")
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            "📞 İletişim Sayfası\n\n"
            "Aşağıdaki adminlerden biriyle iletişime geçebilirsiniz:",
            reply_markup=reply_markup
        )

        return State.CONTACT

    except Exception as error5:
        logger.error(f"Contact page callback hatası: {error5}")
        return State.MAIN_MENU


async def handle_contact_selection(update: Update, context: CallbackContext) -> State:
    query = None
    try:
        query = update.callback_query
        await query.answer()

        # Seçilen admin bilgisini al
        selected_admin_id = query.data.split('_')[1]

        # Dinamik adminler sözlüğünü al
        application = context.application
        dynamic_admins = await get_dynamic_admins(application)
        application.bot_data["dynamic_admins"] = dynamic_admins
        selected_admin_info = dynamic_admins.get(selected_admin_id)

        if not selected_admin_info:
            await query.edit_message_text("Admin bilgisi bulunamadı.")
            return State.MAIN_MENU

        # Admin bilgilerini göster
        message_text = f"""
                        👤 Admin Bilgileri:
                        🔹 Kullanıcı Adı: {selected_admin_info['username']}
                        🔹 Telegram Linki: {selected_admin_info['telegram_link']}
                        🔹 Yetki Seviyesi: {selected_admin_info['label']}

                        Bu admin ile iletişime geçmek için yukarıdaki Telegram linkini kullanabilirsiniz.
                        """

        # Geri dön tuşu ekle
        keyboard = [
            [InlineKeyboardButton("🔙 Geri Dön", callback_data='main_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            message_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

        return State.MAIN_MENU  # Veya uygun bir state

    except Exception as error6:
        # Hata yakalama mekanizması
        logger.error(f"Contact selection hatası: {error6}", exc_info=True)
        if query:
            await query.edit_message_text("Bir hata oluştu. Lütfen tekrar deneyin.")
        return State.MAIN_MENU


async def info_page(update: Update, _context: CallbackContext) -> State:
    try:
        # Bot bilgilendirme metni
        info_text = """🤖 Bot Bilgilendirme

📌 Bot Genel Özellikleri:
• Kullanıcı Dostu Arayüz
• Hızlı ve Güvenilir İşlem Yönetimi
• Çoklu Dil Desteği
• Anlık Bildirim Sistemi
• Esnek ve Özelleştirilebilir Menüler

🚀 Bot Yetenekleri:
• Otomatik Yanıt Mekanizması
• Kullanıcı Yönetimi
• Detaylı Raporlama
• Kolay Entegrasyon
• Güvenli Veri İşleme

💡 Temel İşlevler:
• Hızlı Destek Alma
• Anlık Bilgilendirme
• Kolay Navigasyon
• Kullanıcı Dostu Arayüz

🔒 Güvenlik Özellikleri:
• Çoklu Yetkilendirme Seviyesi
• Güvenli Veri Saklama
• Şifreli İletişim
• Kullanıcı Aktivite Takibi

📞 Daha Detaylı Bilgi ve Destek İçin Adminlerimizle İletişime Geçebilirsiniz.
"""

        # Buton oluşturma
        keyboard = [
            [InlineKeyboardButton("👥 Adminler", callback_data="contact_page")],
            [InlineKeyboardButton("🔙 Ana Menü", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Mesajı gönderme
        await update.message.reply_text(
            info_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

        return State.INFO  # Bilgi sayfası state'i

    except Exception as error7:
        logger.error(f"Info page error: {str(error7)}", exc_info=True)
        await update.message.reply_text("Bir hata oluştu. Lütfen tekrar deneyin.")
        return State.MAIN_MENU


async def show_main_menu(update: Update, context: CallbackContext) -> State:
    try: # type: ignore
        logger.info("Ana menü gösteriliyor")

        # Kullanıcı ID'sini güvenli bir şekilde alma
        if update.effective_user:
            user_id = update.effective_user.id
        elif update.callback_query and update.callback_query.from_user:
            user_id = update.callback_query.from_user.id
        else:
            logger.error("Kullanıcı ID'si belirlenemedi")
            return State.END

        # Klavye tuşlarını oluşturma
        keyboard = [
            [InlineKeyboardButton("Borsa API Ekle", callback_data="api_add")],
            [InlineKeyboardButton("Borsa API Hesaplarım", callback_data="api_accounts")],
            [InlineKeyboardButton("Borsa Strateji Ayarları", callback_data="strateji_ayarlari")],
            [InlineKeyboardButton("Borsa Raporları Kontrol", callback_data="balance_check")]
        ]

        # ✅ DÜZELTME: Admin kontrolünü basitleştir
        is_user_admin = False
        _=is_user_admin
        try:
            # Süper admin kontrolü
            if user_id == ADMIN_USER_ID:
                is_user_admin = True
            else:
                # Veritabanından admin kontrolü
                admin_check = await asyncio.to_thread(
                    db_operation,
                    "SELECT 1 FROM admin_users WHERE user_id = ?",
                    (user_id,),
                    operation="select",
                    fetch=True,
                    fetch_all=False
                )
                is_user_admin = bool(admin_check)

            # Admin butonu ekle
            if is_user_admin:
                keyboard.append([InlineKeyboardButton("👑 Admin Menüsü", callback_data="admin_menu")])
                logger.info(f"✅ Admin butonu eklendi - User ID: {user_id}")

        except Exception as e:
            logger.error(f"❌ Admin kontrolü hatası: {e}", exc_info=True)

        reply_markup = InlineKeyboardMarkup(keyboard)

        # Karşılama mesajı
        message_text = (
            f"Olimpos Bot ANA MENU'YE Hoş Geldiniz.\n\n"
            "• Bu bot ile çeşitli kripto borsalarındaki\n "
            "• Hesaplarınızı yönetebilir,\n "
            "• Bakiyelerinizi kontrol edebilir ve\n"
            "• İşlem geçmişinizi görüntüleyebilirsiniz."
        )

        # Mesaj gönderme işlemi
        try:
            query = update.callback_query
            # --- YENİ: AKILLI GERİ BUTONU DÜZELTMESİ (Linter Uyumlu) ---
            if query and query.message:
                # DÜZELTME: `isinstance` ile güvenli tip kontrolü
                if isinstance(query.message, Message) and query.message.photo:
                    await query.message.delete()
                    await context.bot.send_message(
                        chat_id=query.message.chat_id, text=message_text, reply_markup=reply_markup
                    )
                else:

                    await query.edit_message_text(text=message_text, reply_markup=reply_markup)
            # Eğer bu bir komutla (/start) veya metin mesajıyla tetiklendiyse, yeni mesaj gönder.
            elif update.message:
                await update.message.reply_text(
                    text=message_text,
                    reply_markup=reply_markup
                )
            else:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=message_text,
                    reply_markup=reply_markup
                )
        except Exception as message_error:
            logger.error(f"❌ Mesaj gönderme hatası: {message_error}")
            return State.END

        logger.info(f"✅ Ana menü başarıyla gösterildi. Kullanıcı ID: {user_id}")
        return State.MAIN_MENU

    except telegram.error.TelegramError as error8:
        logger.error(f"❌ Ana menü gösteriminde genel hata: {error8}", exc_info=True)
        return State.END


async def handle_callback_query(update: Update, context: CallbackContext) -> Union[State, None]:
    query = update.callback_query
    if not query:
        logger.error("Callback query bulunamadı")
        return State.MAIN_MENU

    await query.answer()

    user = query.from_user
    data = query.data or ""
    logger.info(f"Callback alındı: {data}")

    try:
        # User ID'yi context'e kaydet
        if hasattr(user, 'id'):
            context.user_data['user_id'] = user.id

        # --- YENİ: ÖNCELİKLİ GENEL ROUTE'LAR ---
        # Alarm sistemine gitmeden önce genel komutları yakala.
        if data == 'main_menu':
            return await show_main_menu(update, context)

        # 1) ALARM SİSTEMİ: Erken yakalama ve zorunlu yönlendirme
        # Alarm akışına ait tüm exact ve prefix datalar burada toplanır
        alarm_exact = {
            'ai_scan', 'ai_strategy_scan', 'back_to_alarm_menu', 'show_settings',
            'active_alarms', 'top_gainers', 'top_losers', 'other_symbols',
            'clear_all_alarms', 'retrain_ai', 'system_stats', 'performance_dashboard',
            'show_performance_menu', 'param_menu', 'back_to_symbol_select',
            'back_to_timeframe_select', 'show_tuner_mode_menu', 'show_tf_thresholds_menu',
            'tfth_ai_edit', 'tfth_strat_edit', 'train_ai_model', 'alarm_reports'
        }
        alarm_prefixes = (
            'ai_tf_', 'ai_strat_', 'strat_scan_tf_', 'strat_strat_', 'perf_report_',
            'param_group_', 'edit_param_', 'select_symbol_', 'timeframe_', 'remove_alarm_',
            'set_tuner_', 'tfth_tf_', 'tfth_ai_edit_', 'tfth_strat_edit_'
        )

        if data in alarm_exact or any(data.startswith(p) for p in alarm_prefixes):
            logger.info(f"🎯 Alarm callback yönlendiriliyor: {data}")
            try:
                # Wrapper kullanalım: strategies.alarm_system.handlers ile doğru router
                result = await handle_alarm_action(update, context)
                logger.info(f"✅ Alarm callback işlemi tamamlandı. Dönüş: {result}")
                return result
            except Exception as alarm_error:
                logger.error(f"❌ Alarm callback hatası: {str(alarm_error)}", exc_info=True)
                await query.edit_message_text(
                    "❌ İşlem sırasında hata oluştu. Lütfen tekrar deneyin.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🏠 Admin Menü", callback_data="main_menu")]])
                )
                return State.MAIN_MENU

        # 2) Mevcut callback routes (genel uygulama akışınız)
        callback_routes = {
            'start_command': start,
            'main_menu': show_main_menu,
            'back_to_admin_menu': admin_menu,
            'api_add': api_add,
            'api_accounts': api_accounts,
            'strateji_ayarlari': strateji_ayarlari,
            'balance_check': balance_check,
            'admin_menu': admin_menu,
            'limit_user_time': handle_limit_user_time,
            'assign_user_channel': show_user_list,

            # Borsa menüleri
            'bitget_menu': bitget_ayarlari_menu,
            'okx_menu': okx_ayarlari_menu,
            'binance_menu': binance_ayarlari_menu,
            'mexc_menu': mexc_ayarlari_menu,
            'bybit_menu': bybit_ayarlari_menu,
            # 'bingx_menu': bingx_ayarlari_menu,
            # 'coinex_menu': coinex_ayarlari_menu,
            # 'weex_menu': weex_ayarlari_menu
        }

        # Doğrudan eşleşen callback verisi
        if data in callback_routes:
            try:
                return await callback_routes[data](update, context)
            except Exception as direct_handler_error:
                logger.error(f"Doğrudan callback handler'ında hata: {str(direct_handler_error)}", exc_info=True)
                raise

        # 3) Prefix bazlı route'lar (genel)
        prefix_routes = {
            'edit_api:': edit_api,
            'delete_api:': delete_api,
             # --- KÖK NEDEN DÜZELTMESİ: LAMBDA'LARIN YANLIŞ KULLANIMI ---
             # Lambda'lar, hedef fonksiyonlara context yerine string gönderiyordu.
             # Artık veriyi context.user_data'ya yazıp hedef fonksiyonu doğru imza ile çağırıyoruz.
             'bitget_': lambda u, c: (c.user_data.update({'action_data': data}), handle_bitget_actions(u, c)),
             'okx_': lambda u, c: (c.user_data.update({'action_data': data}), handle_okx_actions(u, c)),
             'binance_': lambda u, c: (c.user_data.update({'action_data': data}), handle_binance_actions(u, c)),
             'mexc_': lambda u, c: (c.user_data.update({'action_data': data}), handle_mexc_actions(u, c)),
             'bybit_': lambda u, c: (c.user_data.update({'action_data': data}), handle_bybit_actions(u, c)),
             # 'bingx_': lambda u, ctx: handle_bingx_actions(u, data.split('_', 1)[1]),
             # 'coinex_': lambda u, ctx: handle_coinex_actions(u, data.split('_', 1)[1]),
             # 'weex_': lambda u, ctx: handle_weex_actions(u, data.split('_', 1)[1]),
         }
        for prefix, handler in prefix_routes.items():
            if data.startswith(prefix):
                logger.info(f"Prefix eşleşen callback: {data}")
                try:
                    return await handler(update, context)
                except Exception as prefix_handler_error:
                    logger.error(f"Prefix callback handler'ında hata: {str(prefix_handler_error)}", exc_info=True)
                    raise

        # 4) Admin callback’leri (alarm akışı anahtarları BU listede DEĞİL!)
        admin_prefixes = [
            'admin_', 'back_to_admin_menu', 'select_admin_', 'admin_delete_api_',
            'assign_user_', 'show_full_user_details_', 'manage_admins', 'view_apis',
            'view_users', 'limit_user_time', 'user_details', 'assign_user_channel',
            'channel_menu'
        ]
        if any(data.startswith(prefix) for prefix in admin_prefixes) or data in admin_prefixes:
            logger.info(f"Admin callback'i yönlendirildi: {data}")
            try:
                import olimpos_admin
                return await olimpos_admin.handle_admin_buttons(update, context)
            except Exception as admin_handler_error:
                logger.error(f"Admin callback handler'ında hata: {str(admin_handler_error)}", exc_info=True)
                raise

        # 5) Bilinmeyen callback
        logger.warning(f"İşlenemeyen callback verisi: {data}")
        await query.edit_message_text(
            "Bu işlem için geçerli bir seçenek bulunamadı. Lütfen ana menüye dönün.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="main_menu")]])
        )
        return State.MAIN_MENU

    except telegram.error.TelegramError as error9:
        logger.error(f"Callback işleme ana hatası: {str(error9)}", exc_info=True)
        try:
            await query.edit_message_text(
                "Callback verisi işlenirken bir hata oluştu. Lütfen tekrar deneyin.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="main_menu")]])
            )
        except Exception as edit_error:
            logger.error(f"Hata mesajı gönderme hatası: {edit_error}")
        return State.MAIN_MENU


async def admin_menu(update: Update, context) -> State:
    # Bu fonksiyon artık handle_admin_buttons içinde birleştirildi ve
    # doğrudan çağrılmak yerine, handle_admin_buttons'ın bir parçası olarak çalışıyor.
    # Bu fonksiyonu, eski çağrıların hata vermemesi için geçici olarak bırakabilir
    # veya doğrudan handle_admin_buttons'ı çağıracak şekilde güncelleyebilirsiniz.
    return await handle_admin_buttons(update, context)


async def api_add(update: Update, context) -> State:
    logger.info("API ekleme işlemi başlatılıyor.")

    # YENİ: Merkezi ve görsel menü fonksiyonunu çağır
    # Bu değişiklik, eski basit metin tabanlı menüyü,
    # logolu, açıklamalı ve emojili yeni dinamik menü ile değiştirir.
    return await show_dynamic_exchange_menu(
        update=update,
        context=context,
        caption_text="Bakiye Kontrolü İçin Borsa Seçin",
        callback_prefix="user_action_exchange_",  # DÜZELTME: Callback prefix'i değiştirildi
        back_button_callback="main_menu"
    )


async def select_exchange(update: Update, context) -> State:
    query = update.callback_query
    await query.answer()

    selected_exchange = query.data.split('_')[2]
    if selected_exchange not in SUPPORTED_EXCHANGES:
        await query.edit_message_text("Geçersiz borsa seçimi. Lütfen listeden bir borsa seçin.")
        return State.WAITING_EXCHANGE

    context.user_data['selected_exchange'] = selected_exchange

    await query.edit_message_text(
        f"{SUPPORTED_EXCHANGES[selected_exchange]} seçildi. Lütfen API Key'inizi girin:")
    logger.info(f"Seçilen borsa: {context.user_data.get('selected_exchange')}")
    logger.info("API Key girişi bekleniyor.")
    return State.WAITING_API_KEY


async def handle_api_key(update: Update, context) -> State:
    logger.info("handle_api_key fonksiyonuna girildi")

    if update.message is None:
        logging.error("Gelen update mesajı yok!")
        return State.WAITING_API_KEY

    api_key = update.message.text.strip()
    if not api_key:
        await update.message.reply_text("Lütfen geçerli bir API Key girin.")
        return State.WAITING_API_KEY

    logger.info(f"Alınan API Key: {api_key}")
    context.user_data['api_key'] = api_key
    await update.message.reply_text("API Key kaydedildi. Şimdi lütfen Secret Key'inizi girin:")

    return State.WAITING_SECRET_KEY


async def handle_secret_key(update: Update, context) -> int:
    logger.info("handle_secret_key fonksiyonuna girildi")
    secret_key = update.message.text
    context.user_data['secret_key'] = secret_key

    selected_exchange = context.user_data.get('selected_exchange')
    if selected_exchange in EXCHANGES_REQUIRING_PASSPHRASE:
        await update.message.reply_text("Secret Key kaydedildi. Şimdi lütfen Passphrase'inizi girin:")
        return State.WAITING_PASSPHRASE
    else:
        return await save_api_info(update, context)


async def handle_passphrase(update: Update, context) -> int:
    logger.info("handle_passphrase fonksiyonuna girildi")
    passphrase = update.message.text
    context.user_data['passphrase'] = passphrase
    return await save_api_info(update, context)


async def save_api_info(update: Update, context) -> int:
    logger.info("save_api_info fonksiyonuna girildi")
    user = update.effective_user
    user_id = user.id
    username = user.full_name
    selected_exchange = context.user_data.get('selected_exchange')
    api_key = context.user_data.get('api_key')
    secret_key = context.user_data.get('secret_key')
    passphrase = context.user_data.get('passphrase')

    if not all([selected_exchange, api_key, secret_key]):
        await update.message.reply_text("Eksik bilgi. Lütfen tüm gerekli alanları doldurun.")
        return State.MAIN_MENU

    try:
        # api_key tablosuna ekleme/güncelleme
        api_key_query = '''
            INSERT OR REPLACE INTO api_key 
            (user_id, exchange, username, api_key, secret_key, passphrase) 
            VALUES (?, ?, ?, ?, ?, ?)
        '''
        api_key_params = (user_id, selected_exchange, username, api_key, secret_key, passphrase)
        # DÜZELTME: Veritabanı işlemi engelleyici olduğu için ayrı bir thread'de çalıştır.
        await asyncio.to_thread(db_operation, api_key_query, api_key_params, operation='insert')

        # user_channel_info tablosunda güncelleme
        user_channel_query = '''
            UPDATE user_channel_info 
            SET api_key = ?, secret_key = ?, passphrase = ? 
            WHERE user_id = ? AND exchange = ?
        '''
        user_channel_params = (api_key, secret_key, passphrase, user_id, selected_exchange)
        # DÜZELTME: Veritabanı işlemi engelleyici olduğu için ayrı bir thread'de çalıştır.
        await asyncio.to_thread(db_operation, user_channel_query, user_channel_params, operation='update')

        await update.message.reply_text("API bilgileriniz başarıyla kaydedildi/güncellendi.")
        context.user_data.clear()
        return await show_main_menu(update, context)

    except Exception as error10:
        logging.error(f"save_api_info'da hata: {str(error10)}", exc_info=True)
        await update.message.reply_text(
            "API bilgileriniz kaydedilirken bir hata oluştu. "
            "Lütfen daha sonra tekrar deneyin."
        )
        logger.error(
            f"API bilgisi kaydedilirken hata oluştu: "
            f"user_id={user_id}, exchange={selected_exchange}. Hata: {str(error)}"
        )
        return State.MAIN_MENU


async def api_accounts(update: Update, context) -> State:
    logger.info("api_accounts fonksiyonu çağrıldı.")

    try:
        query = update.callback_query
        if query is None or query.message is None:
            logging.error("Callback query or message is None")
            return State.MAIN_MENU

        await query.answer()

        chat_id = update.effective_chat.id
        message = await context.bot.send_message(
            chat_id=chat_id,
            text="API hesapları yükleniyor..."
        )

        user_id = update.effective_user.id
        db_query = "SELECT exchange, api_key, secret_key, passphrase FROM api_key WHERE user_id = ?"
        params = (user_id,)

        try:
            # DÜZELTME: Veritabanı işlemi engelleyici olduğu için ayrı bir thread'de çalıştır.
            apis = await asyncio.to_thread(db_operation, db_query, params, operation='select')
        except Exception as error11:
            logging.error(f"Database operation failed: {str(error11)}")
            await message.edit_text("Veritabanı işlemi sırasında bir hata oluştu. Lütfen daha sonra tekrar deneyin.")
            return State.MAIN_MENU

        if apis:
            api_messages = []
            for api in apis:
                exchange, api_key, secret_key, passphrase = api
                api_message = f"Exchange: {exchange}\n"
                if api_key:
                    api_message += f"API Key: {api_key[:5]}...\n"
                if secret_key:
                    api_message += f"Secret Key: {secret_key[:5]}...\n"
                if passphrase:
                    api_message += f"Passphrase: {passphrase[:5]}...\n"

                keyboard = [
                    [InlineKeyboardButton("Düzenle", callback_data=f"edit_api:{user_id}:{exchange}"),
                     InlineKeyboardButton("Sil", callback_data=f"delete_api:{user_id}:{exchange}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                api_messages.append(api_message)
                await message.reply_text(api_message, reply_markup=reply_markup)

            await message.reply_text("Yukarıdaki kayıtlar listelenmiştir.")

            # Geri veya Ana Menü butonunu ekliyoruz
            menu_keyboard = [
                [InlineKeyboardButton("Ana Menü", callback_data="main_menu")]
            ]
            menu_reply_markup = InlineKeyboardMarkup(menu_keyboard)
            await message.reply_text("Ana menüye dönmek için butona tıklayın:", reply_markup=menu_reply_markup)

        else:
            await message.edit_text("Kayıtlı API hesabınız bulunmamaktadır.")

        return State.MAIN_MENU
    except Exception as error12:
        logging.error(f"An error occurred in api_accounts: {str(error12)}")
        chat_id = update.effective_chat.id
        await context.bot.send_message(
            chat_id=chat_id,
            text="Bir hata oluştu. Lütfen daha sonra tekrar deneyin."
        )
        return State.MAIN_MENU


async def edit_api(update: Update, context) -> State:
    logger.info("edit_api fonksiyonu giriliyor.")
    query = update.callback_query
    await query.answer()

    user_id, exchange = query.data.split(':')[1:]

    # Mevcut API bilgilerini al
    api_info = get_api_key(user_id, exchange)
    if not api_info:
        await query.edit_message_text("API bilgisi bulunamadı.")
        return State.MAIN_MENU

    context.user_data['editing_api'] = {
        'user_id': user_id,
        'exchange': exchange,
        'current_api_key': api_info['api_key'],
        'current_secret_key': api_info['secret_key'],
        'current_passphrase': api_info['passphrase']
    }

    await query.edit_message_text(
        f"{exchange} için API bilgilerini güncelleyebilirsiniz.\n"
        "Lütfen yeni API Key'i girin (değiştirmek istemiyorsanız 'aynı' yazın):"
    )
    return State.EDIT_API_KEY


async def handle_edit_api_key(update: Update, context) -> State:
    new_api_key = update.message.text
    if new_api_key.lower() != 'aynı':
        context.user_data['editing_api']['new_api_key'] = new_api_key

    await update.message.reply_text("Şimdi yeni Secret Key'i girin (değiştirmek istemiyorsanız 'aynı' yazın):")
    return State.EDIT_SECRET_KEY


async def handle_edit_secret_key(update: Update, context) -> int:
    new_secret_key = update.message.text
    if new_secret_key.lower() != 'aynı':
        context.user_data['editing_api']['new_secret_key'] = new_secret_key

    if context.user_data['editing_api']['exchange'] in EXCHANGES_REQUIRING_PASSPHRASE:
        await update.message.reply_text("Şimdi yeni Passphrase'i girin (değiştirmek istemiyorsanız 'aynı' yazın):")
        return State.EDIT_PASSPHRASE
    else:
        return await save_edited_api(update, context)


async def handle_edit_passphrase(update: Update, context) -> int:
    new_passphrase = update.message.text
    if new_passphrase.lower() != 'aynı':
        context.user_data['editing_api']['new_passphrase'] = new_passphrase

    return await save_edited_api(update, context)


async def save_edited_api(update: Update, context) -> int:
    editing_api = context.user_data['editing_api']

    new_api_key = editing_api.get('new_api_key', editing_api['current_api_key'])
    new_secret_key = editing_api.get('new_secret_key', editing_api['current_secret_key'])
    new_passphrase = editing_api.get('new_passphrase', editing_api['current_passphrase'])

    try:
        # api_key tablosunda güncelleme
        update_api_key_query = '''
            UPDATE api_key 
            SET api_key = ?, secret_key = ?, passphrase = ? 
            WHERE user_id = ? AND exchange = ?
        '''
        update_api_key_params = (new_api_key, new_secret_key, new_passphrase,
                                 editing_api['user_id'], editing_api['exchange'])
        # DÜZELTME: Veritabanı işlemi engelleyici olduğu için ayrı bir thread'de çalıştır.
        await asyncio.to_thread(db_operation, update_api_key_query, update_api_key_params, operation='update')

        # user_channel_info tablosunda güncelleme
        update_user_channel_query = '''
            UPDATE user_channel_info 
            SET api_key = ?, secret_key = ?, passphrase = ? 
            WHERE user_id = ? AND exchange = ?
        '''
        update_user_channel_params = (new_api_key, new_secret_key, new_passphrase,
                                      editing_api['user_id'], editing_api['exchange'])
        # DÜZELTME: Veritabanı işlemi engelleyici olduğu için ayrı bir thread'de çalıştır.
        await asyncio.to_thread(db_operation, update_user_channel_query, update_user_channel_params, operation='update')

        await update.message.reply_text("API bilgileri başarıyla güncellendi.")
    except Exception as error13:
        logger.error(f"API güncelleme hatası: {str(error13)}")
        await update.message.reply_text("API bilgileri güncellenirken bir hata oluştu.")

    context.user_data.pop('editing_api', None)
    return await show_main_menu(update, context)


async def delete_api(update: Update, context) -> int:
    query = update.callback_query
    await query.answer()

    user_id = query.data.split(':')[1]  # Düzeltme: [1] olarak değiştirildi

    try:
        # Veritabanındaki tüm tabloları al
        tables_query = "SELECT name FROM sqlite_master WHERE type='table'"
        # DÜZELTME: Veritabanı işlemi engelleyici olduğu için ayrı bir thread'de çalıştır.
        tables = await asyncio.to_thread(db_operation, tables_query, (), operation='select', fetch=True)

        # Silme sonuçlarını tutacak liste
        deletion_results = []

        # Her tabloda user_id'yi ara ve sil
        for table in tables:
            table_name = table[0]

            # Tablonun kolonlarını kontrol et
            columns_query = f"PRAGMA table_info({table_name})"
            # DÜZELTME: Veritabanı işlemi engelleyici olduğu için ayrı bir thread'de çalıştır.
            columns = await asyncio.to_thread(db_operation, columns_query, (), operation='select', fetch=True)

            # user_id içeren kolonları bul
            user_id_columns = [col[1] for col in columns if 'user_id' in col[1].lower()]

            for column in user_id_columns:
                # Önce kayıt olup olmadığını kontrol et
                check_query = f"SELECT * FROM {table_name} WHERE {column} = ?"
                # DÜZELTME: Veritabanı işlemi engelleyici olduğu için ayrı bir thread'de çalıştır.
                check_result = await asyncio.to_thread(db_operation,
                    check_query,
                    (user_id,),
                    operation='select',
                    fetch=True
                )

                if check_result:
                    # Kayıt varsa silme işlemi yap
                    delete_query = f"DELETE FROM {table_name} WHERE {column} = ?"
                    # DÜZELTME: Veritabanı işlemi engelleyici olduğu için ayrı bir thread'de çalıştır.
                    await asyncio.to_thread(db_operation,
                        delete_query,
                        (user_id,),
                        operation='delete'
                    )
                    deletion_results.append(f"{table_name} tablosundan silindi - {column}: {user_id}")
                    logger.info(f"{table_name} tablosundan silindi - {column}: {user_id}")
                else:
                    deletion_results.append(f"{table_name} tablosunda {user_id} bulunamadı")
                    logger.info(f"{table_name} tablosunda {user_id} bulunamadı")

        # Sonuçları birleştir
        result_message = "\n".join(deletion_results) if deletion_results else "Silinecek kayıt bulunamadı."

        await query.edit_message_text(f"Silme işlemi tamamlandı:\n{result_message}")

    except Exception as error14:
        logger.error(f"Silme hatası: {str(error14)}")
        await query.edit_message_text("Bilgiler silinirken bir hata oluştu.")

    return await show_main_menu(update, context)


import asyncio
from telegram import Update
from telegram.ext import CallbackContext

async def delete_user(update: Update, context: CallbackContext) -> int:
    q = update.callback_query
    if not q:
        return State.MAIN_MENU

    await q.answer()

    data = q.data or ""  # "delete_user_123456"
    try:
        user_id = int(data.split("_")[-1])
    except Exception:
        await q.edit_message_text("Geçersiz kullanıcı id.")
        return State.ADMIN_MENU

    # DB fonksiyonun sync -> thread'e al
    ok = await asyncio.to_thread(delete_user_db, user_id)

    if ok:
        await q.edit_message_text(f"✅ Kullanıcı silindi: {user_id}")
    else:
        await q.edit_message_text(f"⚠️ Kullanıcı silinemedi / bulunamadı: {user_id}")

    return State.ADMIN_MENU


async def strateji_ayarlari(update: Update, context) -> State:
    """
    "Borsa Strateji Ayarları" butonu için görsel menüyü gösterir.
    """
    try:
        if update.callback_query:
            await update.callback_query.answer()

        context.user_data['last_action'] = 'strateji_ayarlari'

        # YENİ: Görsel ve dinamik borsa seçim menüsünü çağır
        await show_dynamic_exchange_menu(
            update=update,
            context=context,
            caption_text="Strateji Ayarları İçin Borsa Seçin",  # DÜZELTME: Callback prefix'i değiştirildi
            callback_prefix="user_action_exchange_",
            back_button_callback="main_menu"
        )
        # DÜZELTME: Doğru state'i döndürerek ConversationHandler'ın bir sonraki adımı beklemesini sağlıyoruz.
        return State.STRATEGY_WAITING_FOR_EXCHANGE_SELECTION

    except Exception as error15:
        logger.error(f"Strateji ayarları hatası: {error15}")
        try:
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    "Bir hata oluştu. Ana menüye dönülüyor...",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Ana Menü", callback_data="main_menu")]
                    ])
                )
            else:
                await update.message.reply_text(
                    "Bir hata oluştu. Lütfen tekrar deneyin.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🏠 Ana Menü", callback_data="main_menu")]
                    ])
                )
        except Exception as reply_error:
            logger.error(f"Hata mesajı gönderme hatası: {reply_error}")

        return State.MAIN_MENU


from strategies.alarm_system import handlers as alarm_handlers

async def handle_alarm_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ...
    try:
        # Doğrudan sınıfı geçirerek alt router’ı çağır
        # DÜZELTME: Strateji başlatma gibi uzun süren görevleri arka plana al.
        # Bu, botun kilitlenmesini ve /start gibi komutların çalışmamasını engeller.
        query = getattr(update, "callback_query", None)
        if query and query.data == 'start_ai_strategy':
            if OlimposStrategy.is_running:
                await query.answer("Strateji zaten çalışıyor.", show_alert=True)
                return State.ALARM_SETUP

            await query.edit_message_text(
                "🚀 AI Stratejisi arka planda başlatılıyor...\n"
                "Botu kullanmaya devam edebilirsiniz."
            )
            # Görevi arka planda çalıştır, botu kilitleme.
            asyncio.create_task(OlimposStrategy.run_ai_strategy(context))
            # Kullanıcıya hemen menüyü göster.
            return await OlimposStrategy.show_alarm_menu(update, context)
        else:
            # Diğer tüm alarm aksiyonları normal şekilde çalışmaya devam eder.
            result = await alarm_handlers.handle_alarm_action(OlimposStrategy, update, context)

            # --- DÜZELTME: ANA MENÜYE DÖNÜŞ KONTROLÜ ---
            # Eğer alt modülden State.MAIN_MENU dönerse, ana menüyü burada manuel olarak çağırıyoruz.
            if result == State.MAIN_MENU:
                return await show_main_menu(update, context)

            return result

    except Exception as e:
        logger.error(f"❌ handle_alarm_action yönlendirme hatası: {str(e)}", exc_info=True)
        query = getattr(update, "callback_query", None)
        if query:
            try:
                await query.edit_message_text(
                    "Bir hata oluştu. Lütfen tekrar deneyin.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="main_menu")]])
                )
            except Exception:
                pass
        return State.MAIN_MENU


async def stop_all(self):
    for task in self.tasks.values():
        if not task.done():
            task.cancel()
        self.tasks.clear()


async def balance_check(update: Update, context) -> State:
    """
    "Borsa Raporları Kontrol" butonu için görsel menüyü gösterir.
    """
    await update.callback_query.answer()
    context.user_data['last_action'] = 'balance_check'  # Son işlemi saklıyoruz

    # YENİ: Görsel ve dinamik borsa seçim menüsünü çağır
    await show_dynamic_exchange_menu(
        update=update,
        context=context,
        caption_text="Bakiye Kontrolü İçin Borsa Seçin",
        callback_prefix="user_action_exchange_",  # DÜZELTME: Callback prefix'i değiştirildi
        back_button_callback="main_menu"
    )
    # DÜZELTME: Doğru state'i döndürerek ConversationHandler'ın bir sonraki adımı beklemesini sağlıyoruz.
    return State.STRATEGY_WAITING_FOR_EXCHANGE_SELECTION


async def handle_exchange_selection_for_user_action(update: Update, context: CallbackContext) -> int:
    """
    Kullanıcının strateji ayarları veya bakiye kontrolü için seçtiği borsayı işler.
    Bu fonksiyon, admin akışından tamamen ayrıdır.
    """
    query = update.callback_query
    await query.answer()

    # Callback verisinden borsa adını al: "user_action_exchange_binance" -> "binance"
    exchange_name = query.data.split('user_action_exchange_')[1]
    context.user_data['selected_exchange'] = exchange_name

    # DÜZELTME: Akıcı bir geçiş için önce görsel menüyü sil ve geçici bir "bekleyin" mesajı gönder.
    if isinstance(query.message, Message) and query.message.photo:
        await query.message.delete()

    loading_message = await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=f"⏳ Lütfen bekleyin...\n\n**{exchange_name.upper()}** borsası ile bağlantı kuruluyor..."
    )

    last_action = context.user_data.get('last_action')

    # Kullanıcının son yaptığı işleme göre doğru fonksiyona yönlendir
    if last_action == 'strateji_ayarlari':
        # Kullanıcıyı strateji ayarları menüsüne yönlendir
        logger.info(f"Strateji ayarları için {exchange_name} seçildi. Menü gösteriliyor.")
        # DÜZELTME: "Bekleyin" mesajını sil ve yeni menüyü gönder.
        try:
            await loading_message.delete()
        except Exception:
            pass # Mesaj zaten silinmiş veya bulunamıyor olabilir, önemli değil.
        return await show_strategy_settings(update, context)

    elif last_action == 'balance_check':
        # Kullanıcıyı bakiye kontrol fonksiyonuna yönlendir
        logger.info(f"Bakiye kontrolü için {exchange_name} seçildi. Bakiye getiriliyor.")
        # DÜZELTME: handle_exchange_selection'ı doğrudan çağırıyoruz.
        # Bu fonksiyon zaten bakiye bilgilerini getirip gösterecektir.
        await loading_message.delete()
        return await handle_exchange_selection(update, context)

    else:
        # Beklenmedik bir durum
        await query.edit_message_text(
            "Beklenmedik bir durum oluştu. Lütfen tekrar deneyin.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Ana Menü", callback_data="main_menu")]])
        )
        return State.MAIN_MENU


async def periodic_check(application):
    """
    Periyodik olarak kullanıcı durumlarını kontrol eden ve güncelleyen fonksiyon.
    Bu fonksiyon, sürekli çalışır ve her saat başı kontrol yapar.
    """
    # logger.info("Periyodik kontrol başlatıldı...")

    while True:
        try:
            '''
          # logger.info("Kullanıcı durumu güncellemesi gerçekleştiriliyor...")
            '''
            await update_user_status(application)
            # logger.info("Kullanıcı durum güncellemesi tamamlandı.")
        except Exception as error16:
            logger.error(f"Periyodik kontrol sırasında hata: {str(error16)}")

        # Her saat başı kontrol et
        await asyncio.sleep(24 * 60 * 60)  # 24 saat'te 1 mesaj yolla


async def error_handler(update: Update, context) -> None:
    logging.error(f"Bir güncelleme işlenirken istisna oluştu:", exc_info=context.error)
    try:
        if update.callback_query:
            await update.callback_query.answer("Bir hata oluştu. Lütfen daha sonra tekrar deneyin.")
        elif update and update.message:
            await update.message.reply_text("Bir hata oluştu. Lütfen daha sonra tekrar deneyin.")
    except Exception as error17:
        logging.error(f"Hata işleyicisinde hata: {error17}")


async def cancel(update: Update, context) -> State:
    _=context
    # user = update.message.from_user
    # logger.info("User %s canceled the conversation.", user.first_name)
    await update.message.reply_text('İşlem iptal edildi. Ana menüye dönmek için /start komutunu kullanabilirsiniz.')
    return State.END


async def handle_text_message(update, context):
    # Kullanıcının gönderdiği metin mesajını işleyin
    user_message = update.message.text
    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Mesajınız: {user_message}")


async def unknown_callback_handler(update: Update, context) -> int:
    _=context
    query = update.callback_query
    if query:
        logger.warning(f"Unhandled callback data: {query.data}")
        await query.answer("Bu işlem şu anda mevcut değil.")
    return ConversationHandler.END


async def log_callbacks(update: Update, context):
    _=context
    query = update.callback_query
    await query.answer()
    logger.info(f"Received callback data: {query.data}")


async def update_all_user_balances(application):
    try:
        await update_binance_user_balances()
        # await update_okx_user_balances()
        # await update_bitget_user_balances()
        # await update_bybit_user_balances()
        # await update_bitmart_user_balances()
        await update_mexc_user_balances(application) # Düzeltme: application parametresi zaten doğru şekilde gönderiliyor.
        logger.info("Tüm borsa bakiyeleri başarıyla güncellendi")

    except Exception as error18:
        logger.error(f"Tüm borsaların bakiye güncellemesinde hata: {error18}", exc_info=True)


async def create_conversation_handler():
    return ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("admin", admin_menu),
            CommandHandler(
                "signal",
                signal_handler,
                filters=filters.ChatType.GROUPS | filters.ChatType.CHANNEL
            ),
            MessageHandler(
                filters.Regex('^(🚀 Başlat|/start)$'),
                start
            ),
            CallbackQueryHandler(start, pattern='^start_command$'),
            CallbackQueryHandler(start_bot_callback, pattern='^start_bot$'),
            CallbackQueryHandler(admin_menu, pattern='^admin_menu$'),
            CallbackQueryHandler(handle_alarm_action, pattern='^alarm_setup$')
        ],
        states={
            State.WAITING: [
                CallbackQueryHandler(start_bot_callback, pattern='^start_bot$')
            ],
            State.MAIN_MENU:[
                # 1) ALARM AKIŞI ÖNCE: exact ve prefix pattern'ler
                CallbackQueryHandler(
                    handle_alarm_action,
                    pattern=r'^(ai_scan|ai_strategy_scan|back_to_alarm_menu|show_settings|active_alarms|top_gainers|top_losers|other_symbols|clear_all_alarms|retrain_ai|system_stats|performance_dashboard|show_performance_menu|param_menu|back_to_symbol_select|back_to_timeframe_select|show_tuner_mode_menu|show_tf_thresholds_menu|tfth_ai_edit|tfth_strat_edit|train_ai_model|alarm_reports|risk_menu|risk_enable|risk_disable|risk_set_dd|risk_reset_daily)$'
                ),
                CallbackQueryHandler(
                    handle_alarm_action,
                    pattern=r'^(ai_tf_|ai_strat_|strat_scan_tf_|strat_strat_|perf_report_|param_group_|edit_param_|select_symbol_|timeframe_|remove_alarm_|set_tuner_|tfth_tf_|tfth_ai_edit_|tfth_strat_edit_)'
                ),

                # 2) Admin ve diğer spesifik yönlendirmeler
                CallbackQueryHandler(admin_menu, pattern=r'^admin_menu$'),
                CallbackQueryHandler(strateji_ayarlari, pattern=r'^strateji_ayarlari$'),
                CallbackQueryHandler(info_page, pattern=r'^info_page$'),
                CallbackQueryHandler(contact_page, pattern=r'^contact_page$'),
                CallbackQueryHandler(
                    handle_emergency_close_callback,
                    pattern=r'^\{.*"a"\s*:\s*"ec"'
                ),
                CallbackQueryHandler(show_main_menu, pattern=r'^main_menu$'),

                # 3) En sonda genel yakalayıcılar
                CallbackQueryHandler(handle_callback_query),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_button_click),
            ],

            State.CONTACT_ADMIN: [
                CallbackQueryHandler(contact_page, pattern=r'^contact_page$'),
                CallbackQueryHandler(show_main_menu, pattern=r'^main_menu$')
            ],
            State.INFO: [
                CallbackQueryHandler(info_page, pattern="^info_page$"),
                CallbackQueryHandler(contact_page_callback, pattern="^contact_page$"),
                CallbackQueryHandler(show_main_menu, pattern="^main_menu$")
            ],

            State.API_ADD: [CallbackQueryHandler(handle_callback_query)],
            State.WAITING_EXCHANGE: [CallbackQueryHandler(select_exchange, pattern=r'^select_exchange_')],
            State.WAITING_API_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_api_key),
                CallbackQueryHandler(handle_callback_query)
            ],
            State.WAITING_SECRET_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_secret_key)],
            State.WAITING_PASSPHRASE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_passphrase)],
            State.API_ACCOUNTS: [CallbackQueryHandler(handle_callback_query)],
            State.EDIT_API_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_api_key)],
            State.EDIT_SECRET_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_secret_key)],
            State.EDIT_PASSPHRASE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_passphrase)],
            # ANA MENÜ BİTİŞ
            State.ADMIN_MENU:[
                # DÜZELTME: Admin butonları artık sadece ADMIN_MENU state'indeyken dinleniyor.
                # Bu, diğer state'lerdeki callback'lerle çakışmasını önler.
                CallbackQueryHandler(handle_admin_buttons),
                CallbackQueryHandler(handle_callback_query),
                # Admin menüsünden ana menüye dönüşü de buraya eklemek daha güvenli olabilir.
                CallbackQueryHandler(show_main_menu, pattern=r'^main_menu$'),
            ],
            State.WAITING_FOR_CHART_SETTING_VALUE:[
                MessageHandler(filters.TEXT & ~filters.COMMAND, set_chart_setting_value),
                CallbackQueryHandler(handle_admin_buttons, pattern=r'^edit_chart_setting_'),  # Cancel/Back from input
                CallbackQueryHandler(handle_admin_buttons, pattern='^admin_menu')  # Back to admin menu
            ],
            # YENİ: Yetki düzenleme menüsü için yeni bir state ekleyin.
            State.WAITING_FOR_PERMISSION_TOGGLE:[
                CallbackQueryHandler(toggle_permission, pattern=r'^toggle_perm_'),
                CallbackQueryHandler(manage_permissions_menu, pattern=r'^manage_role_permissions$'),
            ],

            State.VIEWING_APIS_STATE: [
                CallbackQueryHandler(handle_viewing_apis_state)
            ],
            State.WAITING_FOR_USER_SELECTION: [
                CallbackQueryHandler(handle_user_selection, pattern=r'^select_user_'),
                CallbackQueryHandler(back_to_admin_menu, pattern='^back_to_admin_menu$'),
                # DÜZELTME: Borsa seçiminden kullanıcı listesine geri dönmek için handler eklendi.
                # Bu, 'assign_user_channel' callback'ini dinler, çünkü dinamik menünün geri butonu
                # bu değere ayarlanmıştır.
            ],
            # KULLANICI SÜRE AYARLARI BAŞLANGIÇ
            State.WAITING_FOR_USER_CHANNEL_INFO: [
                CallbackQueryHandler(show_user_channel_info, pattern='^show_user_info_')
            ],
            State.WAITING_USER_ACTION: [
                CallbackQueryHandler(add_time_to_user, pattern='^add_time_'),
                CallbackQueryHandler(process_user_delete_channel, pattern=r'^delete_user_channel_\d+_\d+$'),
                CallbackQueryHandler(handle_admin_buttons),

                MessageHandler(filters.TEXT & ~filters.COMMAND, process_time_input),

                CallbackQueryHandler(end_user_usage, pattern='^end_usage_'),
                CallbackQueryHandler(back_to_admin_menu, pattern='^back_to_admin_menu$')
            ],
            State.WAITING_FOR_TIME_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_time_input)],
            # KULLANICI SÜRE AYARLARI BİTİŞ
            # APİ AYARLARI MODÜLÜ BAŞLANGICI

            State.STRATEGY_WAITING_FOR_EXCHANGE_SELECTION:[
                # DÜZELTME: Bu handler'ı ConversationHandler içine, doğru state'e taşıdık.
                # Artık sadece strateji veya bakiye için borsa seçimi beklenirken çalışacak.
                # DÜZELTME: Admin kanal atama akışında borsa seçildiğinde bu handler tetiklenir.
                CallbackQueryHandler(admin_handle_exchange_selection, pattern=r'^admin_assign_exchange_'),
                # DÜZELTME: Borsa seçiminden kullanıcı listesine geri dönmek için handler eklendi.
                CallbackQueryHandler(show_user_list, pattern=r'^assign_user_channel$'),
                CallbackQueryHandler(handle_exchange_selection_for_user_action, pattern=r'^user_action_exchange_'), # Kullanıcı kendi menüsü için
                CallbackQueryHandler(handle_exchange_selection, pattern=r'^select_exchange_'), # Eski API ekleme akışı için
                # DÜZELTME: Kanal seçiminden borsa seçimine geri dönmek için handler eklendi.
                # Bu, `handle_user_selection` fonksiyonunu çağırarak borsa listesini yeniden gösterir.
                CallbackQueryHandler(handle_user_selection, pattern=r'^back_to_exchange_selection$'),
            ],

            State.BITGET_MENU: [
                CallbackQueryHandler(handle_bitget_actions),
                CallbackQueryHandler(handle_callback_query, pattern='^main_menu$'),
            ],
            State.OKX_MENU: [
                CallbackQueryHandler(handle_okx_actions),
                CallbackQueryHandler(handle_callback_query, pattern='^main_menu$'),
            ],
            State.BINANCE_MENU: [
                CallbackQueryHandler(handle_binance_actions),
                CallbackQueryHandler(handle_callback_query, pattern='^main_menu$'),
            ],
            State.MEXC_MENU: [
                CallbackQueryHandler(handle_mexc_actions),
                CallbackQueryHandler(handle_callback_query, pattern='^main_menu$'),
            ],
            State.BYBIT_MENU: [
                CallbackQueryHandler(handle_bybit_actions),
                CallbackQueryHandler(handle_callback_query, pattern='^main_menu$'),
            ],
            State.BITMART_MENU: [
                CallbackQueryHandler(handle_bitmart_actions, pattern=r'^bitmart_'),
                CallbackQueryHandler(handle_callback_query, pattern='^main_menu$'),
            ],
            # APİ AYARLARI MODÜLÜ BİTİŞİ
            # STRATEJİ AYARLARI BAŞLANGIÇ

            State.STRATEJI_AYARLARI: [
                CallbackQueryHandler(strateji_ayarlari, pattern='^strateji_ayarlari$'),
                CallbackQueryHandler(show_main_menu, pattern='^main_menu$')
            ],
            State.STRATEGY_SETTINGS: [
                CallbackQueryHandler(set_lot, pattern="^set_lot.*"),
                CallbackQueryHandler(set_lot, pattern=r'^edit_lot$'),
                CallbackQueryHandler(set_lot, pattern=r'^lot_number_.*$'),

                CallbackQueryHandler(set_leverage, pattern=r'^set_leverage.*'),
                CallbackQueryHandler(set_margin, pattern=r'^set_margin.*'),
                CallbackQueryHandler(set_tp, pattern=r'^set_tp\d+_.*'),
                CallbackQueryHandler(set_stop_loss_strategy, pattern=r"^set_stop_loss_"),
                CallbackQueryHandler(set_take_profit, pattern='^set_take_profit_'),
                CallbackQueryHandler(show_strategy_settings, pattern=r'^set_terial_stop_.*'),
                CallbackQueryHandler(back_to_terial_stop, pattern="^back_to_terial_stop$"),
                CallbackQueryHandler(set_sl_tp_emir, pattern=r'^set_sl_tp_emir_.*'),
                CallbackQueryHandler(set_email_settings, pattern=r'^email_settings_\w+$'),

                CallbackQueryHandler(set_maliyet_cek, pattern=r'^set_maliyet_cek.*'),
                CallbackQueryHandler(set_maks_emir, pattern=r'^set_maks_emir.*'),
                CallbackQueryHandler(toggle_active_status, pattern=r'^toggle_oto_trade_.*$'),
                CallbackQueryHandler(admin_contact, pattern=r'^admin_contact_.*$'),  # Yeni eklenen handler
                CallbackQueryHandler(show_strategy_settings, pattern=r'^set_select_exchange.*'),
                CallbackQueryHandler(handle_callback_query, pattern='^main_menu$'),
                CallbackQueryHandler(back_to_strategy_settings, pattern="^back_to_strategy_settings$"),
                CallbackQueryHandler(strategy_settings, pattern="^strategy_settings$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, show_strategy_settings),
            ],
            State.EMAIL_SETTINGS: [
                CallbackQueryHandler(handle_system_email, pattern=r'^use_system_email_\w+$'),
                CallbackQueryHandler(handle_custom_email, pattern=r'^use_custom_email_\w+$'),
                CallbackQueryHandler(back_to_strategy_settings, pattern=r'^back_to_strategy_settings$')],
            State.WAITING_CUSTOM_EMAIL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_email_input),
                CallbackQueryHandler(back_to_email_settings, pattern=r'^back_to_email_settings$')],
            State.WAITING_CUSTOM_EMAIL_PASSWORD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_email_password),
                CallbackQueryHandler(back_to_email_settings, pattern=r'^back_to_email_settings$')
            ],
            State.MARGIN: [CallbackQueryHandler(handle_margin_selection, pattern='^margin_')],
            State.WAITING_LOT_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_lot_input),
                CallbackQueryHandler(back_to_strategy_settings, pattern="^back_to_strategy_settings$"),
                CallbackQueryHandler(strategy_settings, pattern="^strategy_settings$"),
            ],
            State.MARGIN_SELECTION: [
                CallbackQueryHandler(handle_margin_selection, pattern='^margin_'),
            ],
            State.LOT_SELECTION_METHOD: [
                CallbackQueryHandler(handle_lot_selection_method, pattern=r'^lot_number_.*$'),
                CallbackQueryHandler(handle_lot_selection_method, pattern=r'^lot_percentage_.*$'),
                CallbackQueryHandler(back_to_strategy_settings, pattern='^back_to_strategy_settings$')
            ],
            State.LOT_PERCENTAGE_SELECTION: [
                CallbackQueryHandler(handle_lot_percentage_input, pattern=r'^lot_percentage_\d+_\w+$'),
                CallbackQueryHandler(handle_custom_lot_percentage, pattern=r'^lot_percentage_custom_\w+$'),
                CallbackQueryHandler(handle_lot_percentage_selection, pattern=r'^back_to_lot_selection$')
            ],
            State.LOT_CUSTOM_PERCENTAGE_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_lot_percentage_input)
            ],
            State.LOT_PERCENTAGE_CONFIRMATION: [
                CallbackQueryHandler(confirm_lot_percentage, pattern=r'^confirm_lot_percentage_\d+_\w+$'),
                CallbackQueryHandler(confirm_custom_lot_percentage, pattern=r'^confirm_custom_percentage_\d+(\.\d+)?$'),
                CallbackQueryHandler(handle_lot_percentage_selection, pattern=r'^back_to_lot_percentage_selection$')
            ],

            State.LOT_NUMBER_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_lot_number_input),
                CallbackQueryHandler(back_to_lot_selection, pattern='^back_to_lot_selection$')
            ],
            State.WAITING_LEVERAGE_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_leverage_input),
                CallbackQueryHandler(back_to_strategy_settings, pattern="^back_to_strategy_settings$"),
                CallbackQueryHandler(strategy_settings, pattern="^strategy_settings$"),
            ],

            # Lot onay state'leri için öneriler
            State.LOT_CONFIRMATION: [
                CallbackQueryHandler(confirm_lot_amount, pattern=r'^confirm_lot_\d+(\.\d+)?$'),
                CallbackQueryHandler(back_to_lot_selection, pattern='^back_to_lot_selection$')
            ],

            State.TP_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_tp_input),
                CallbackQueryHandler(back_to_tp_input, pattern='^back_to_tp_input$'),
                CallbackQueryHandler(strategy_settings, pattern="^strategy_settings$"),
            ],
            State.STOPLOSS_SELECTION: [CallbackQueryHandler(handle_stop_loss_selection)],
            State.STOPLOSS_PERCENTAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_stop_loss_percentage)],
            State.SETTING_TAKE_PROFIT: [
                CallbackQueryHandler(process_take_profit, pattern='^tp_'),
                CallbackQueryHandler(back_to_strategy_settings, pattern='^back_to_strategy_settings$'),
            ],
            State.SETTING_CUSTOM_TP: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_custom_tp)],

            State.SETTING_TERIAL_STOP: [
                CallbackQueryHandler(process_terial_stop, pattern=r'^terial_stop_'),
                CallbackQueryHandler(back_to_strategy_settings, pattern=r'^back_to_strategy_settings$'),
            ],
            State.CHOOSING_TERIAL_STOP: [
                CallbackQueryHandler(process_terial_stop, pattern=r'^set_terialstop_'),
                CallbackQueryHandler(back_to_strategy_settings, pattern="^back_to_strategy_settings$"),
            ],

            State.SETTING_TERIAL_STOP_PARAMS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_terial_stop_params)],
            State.SETTING_SL_TP_EMIR: [
                CallbackQueryHandler(handle_sl_tp_emir_choice, pattern=r'^sl_tp_emir_(on|off)_'),
                CallbackQueryHandler(show_strategy_settings, pattern=r'^back_to_strategy_settings_')
            ],
            State.SETTING_MALIYET_CEK: [
                CallbackQueryHandler(handle_maliyet_cek_selection, pattern=r'^set_maliyet_cek_'),
                CallbackQueryHandler(back_to_strategy_settings, pattern=r'^back_to_strategy_settings_')
            ],
            State.SETTING_MAKS_EMIR: [
                CallbackQueryHandler(handle_maks_emir_input, pattern=r'^[0-9]+$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_maks_emir_input),
            ],
            # STRATEJİ AYARLARI BİTİŞ

            State.CHANNEL_MENU: [
                CallbackQueryHandler(handle_channel_buttons),
                CallbackQueryHandler(back_to_admin_menu, pattern='^back_to_admin_menu$'),
            ],
            State.ADD_CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_add_channel)],
            State.DELETE_CHANNEL: [CallbackQueryHandler(process_delete_channel)],
            State.SEND_MESSAGE_TO_CHANNEL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_send_message_to_channel)],
            State.SEND_MESSAGE_TO_ALL_CHANNELS:
                [MessageHandler(filters.TEXT & ~filters.COMMAND, process_send_message_to_all_channels)],

            State.SEND_MESSAGE_TO_USERS: [CallbackQueryHandler(process_send_message_to_users)],
            State.SEND_MESSAGE_TO_ADMIN_USERS: [CallbackQueryHandler(process_send_message_to_users)],
            State.SEND_MESSAGE_TO_NORMAL_USERS: [CallbackQueryHandler(process_send_message_to_users)],
            State.WAITING_FOR_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_send_message_to_users)],

            State.WAITING_FOR_USER_DELETION: [CallbackQueryHandler(delete_user, pattern=r'^delete_user_')],

            State.WAITING_FOR_EXCHANGE_SELECTION:
                [CallbackQueryHandler(admin_handle_exchange_selection, pattern=r'^select_exchange_')],

            State.WAITING_FOR_CHANNEL_SELECTION:
                [CallbackQueryHandler(handle_channel_selection, pattern=r'^assign_channel_')],
            State.WAITING_FOR_START_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_start_date)],
            State.WAITING_FOR_END_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_end_date)],
            State.WAITING_FOR_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_duration_input)],
            State.SEND_MESSAGE_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, send_message_menu)],
            State.WAITING_FOR_USER_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message)],

            # ALARM KURULUMU BAŞĞANGIÇ
            State.WAITING_ALARM_ACTION:[CallbackQueryHandler(handle_alarm_action)],
            State.WAITING_SYMBOL_SELECTION:[CallbackQueryHandler(handle_alarm_action)],
            State.WAITING_TIMEFRAME_SELECTION:[CallbackQueryHandler(handle_alarm_action)],
            State.WAITING_EXCHANGE_SELECTION: [CallbackQueryHandler(handle_exchange_selection)],
            State.ALARM_SETUP: [
                # DÜZELTME: Bu state içindeki tüm callback'ler artık tek bir merkezi handler tarafından yönetilecek.
                # Bu, 'ai_tf_15m' gibi callback'lerin ConversationHandler dışına sızmasını engeller.
                CallbackQueryHandler(handle_alarm_action),
                # Diğer handler'lar kaldırıldı çünkü handle_alarm_action zaten tümünü kapsıyor.
                CallbackQueryHandler(show_main_menu, pattern='^main_menu$'),
            ],

            State.SYMBOL_SELECT:[
                CallbackQueryHandler(handle_alarm_action),
                CallbackQueryHandler(show_main_menu, pattern='^main_menu$')
            ],

            State.TIMEFRAME_SELECT:[
                CallbackQueryHandler(handle_alarm_action, pattern='^timeframe_'),
                CallbackQueryHandler(handle_alarm_action, pattern='^back_to_'),
                CallbackQueryHandler(handle_alarm_action),
                CallbackQueryHandler(show_main_menu, pattern='^main_menu$')
            ],

            State.EXCHANGE_SELECT: [
                CallbackQueryHandler(lambda u, c: handle_alarm_action(u, c)),
                CallbackQueryHandler(show_main_menu, pattern='^main_menu$')
            ],

            State.MANUAL_SYMBOL_INPUT:[MessageHandler(filters.TEXT & ~filters.COMMAND,
                lambda u, c:OlimposStrategy.handle_tfth_value_input(u, c))],

            # Chart Settings States
            State.WAITING_FOR_CHART_SETTING_EDIT: [
                CallbackQueryHandler(handle_admin_buttons, pattern=r'^edit_chart_setting_'),
                CallbackQueryHandler(handle_admin_buttons, pattern=r'^chart_setting_group_'),
                CallbackQueryHandler(handle_admin_buttons, pattern='^manage_chart_settings'),
                CallbackQueryHandler(handle_admin_buttons, pattern='^admin_menu')
            ],
            # YENİ STATE: Parametre değeri girişi için
            State.WAITING_FOR_PARAM_VALUE:[
                MessageHandler(filters.TEXT & ~filters.COMMAND, OlimposStrategy.handle_param_value_input)
            ],
            # ... diğer state'leriniz ...
            # ALARM KURULUMU BİTİŞ

        },
        fallbacks=[CommandHandler('cancel', cancel),
                   CommandHandler("start", start),
                   CommandHandler(
                       "signal",
                       signal_handler,
                       filters=filters.ChatType.GROUPS | filters.ChatType.CHANNEL
                   ),
                   CallbackQueryHandler(show_main_menu, pattern='^main_menu$'),
                   CallbackQueryHandler(lambda u, c: OlimposStrategy.show_alarm_menu(u, c), pattern='^back_to_main$'),
                   MessageHandler(filters.ALL, unknown_callback_handler)],
        per_chat=True,
        per_message=False,
        name="main_conversation",
        allow_reentry=True  # Tekrar girişe izin ver

    )


async def run_email_checker(telegram_bot: Optional[Bot] = None):
    """
    Email kontrolcüsünü başlatma fonksiyonu
    """
    try:
        if not telegram_bot:
            telegram_bot = Bot(token=BOT_TOKEN)

        email_checker = EmailChecker(telegram_bot)

        # Sürekli yeniden başlatma mekanizması
        while True:
            try:
                await email_checker.run()
            except Exception as error19:
                logger.error(f"Email kontrolcüsü çalışırken hata: {error19}")
                logger.info("Email kontrolcüsü yeniden başlatılıyor...")
                await asyncio.sleep(30)  # Hata durumunda 30 saniye bekle
    except Exception as error20:
        logger.error(f"Email kontrolcüsü başlatma hatası: {error20}")


async def scheduled_model_training(application: Application) -> None:
    """
    Her gün belirli bir saatte AI model eğitimini otomatik olarak başlatan
    zamanlanmış görev fonksiyonu.
    YENİ: Başarısız olan borsa için 3 kez yeniden deneme (Retry) mekanizması eklendi.
    """
    logger.info("⏰ Zamanlanmış model eğitimi görevi tetiklendi.")
    try:
        # Veritabanından API anahtarı kayıtlı tüm benzersiz borsaları al
        active_exchanges = await get_active_exchanges()

        if not active_exchanges:
            logger.warning("Zamanlanmış eğitim için kayıtlı borsa bulunamadı, görev atlanıyor.")
            return

        logger.info(f"🤖 Otomatik eğitim için bulunan borsalar: {[ex['name'] for ex in active_exchanges]}")

        # Application nesnesini stratejiye tanıt
        OlimposStrategy._application = application

        for exchange_data in active_exchanges:
            exchange = exchange_data['name']
            logger.info(f"🎓 Otomatik eğitim sırası: {exchange.upper()}")

            # --- RETRY MEKANİZMASI ---
            max_retries = 3
            success = False

            for attempt in range(1, max_retries + 1):
                try:
                    logger.info(f"🔄 {exchange.upper()} Eğitimi - Deneme {attempt}/{max_retries}")

                    # Eğitimi başlat
                    success = await OlimposStrategy.train_ai_model_dynamic(
                        exchange=exchange,
                        triggered_by_user_id=ADMIN_USER_ID
                    )

                    if success:
                        logger.info(f"✅ {exchange.upper()} eğitimi {attempt}. denemede BAŞARIYLA tamamlandı.")
                        break  # Başarılıysa döngüden çık
                    else:
                        logger.warning(f"⚠️ {exchange.upper()} eğitimi {attempt}. denemede başarısız oldu.")

                except Exception as e:
                    logger.error(f"❌ {exchange.upper()} eğitimi {attempt}. denemede hata aldı: {e}")

                # Eğer son deneme değilse bekle
                if attempt < max_retries:
                    wait_time = 60 * attempt  # Artan bekleme süresi (60s, 120s...)
                    logger.info(f"⏳ {wait_time} saniye sonra tekrar denenecek...")
                    await asyncio.sleep(wait_time)

            if not success:
                logger.error(
                    f"⛔ {exchange.upper()} eğitimi {max_retries} deneme sonunda BAŞARISIZ oldu. Diğer borsaya geçiliyor.")

            # Borsalar arası bekleme (Rate limit ve kaynak koruması için)
            await asyncio.sleep(15)

    except Exception as e:
        logger.error(f"❌ Zamanlanmış model eğitimi sırasında kritik hata: {e}", exc_info=True)


class OlimposBotManager:
    def __init__(self):
        self.application: Optional[Application] = None
        self.scheduler: Optional[AsyncIOScheduler] = None
        self.tasks: dict = {}
        self.equity_service: Optional[EquityService] = None   # <-- EKLE

    @staticmethod
    async def initialize_database():
        """Veritabanı başlatma ve süper admin kontrolü (Thread Safe)"""
        try:
            logger.info("📦 Veritabanı işlemleri başlatılıyor...")

            # Veritabanı oluşturma işlemi bloklayıcı olduğu için thread içinde çalıştırıyoruz
            await asyncio.to_thread(create_database)

            # Süper admin kontrolünü de thread içinde yapıyoruz
            logger.info("👤 Süper Admin kontrol ediliyor...")
            await asyncio.to_thread(ensure_super_admin_exists)

            logger.info("✅ Veritabanı ve Admin hazırlığı tamamlandı.")
        except Exception as error21:
            logger.error(f"❌ Veritabanı başlatma hatası: {error21}", exc_info=True)
            # Hata olsa bile devam etmeye çalışalım, belki veritabanı zaten hazırdır.

    async def setup_scheduler(self):
        if self.scheduler and self.scheduler.running:
            logger.warning("[SCHED] setup_scheduler çağrıldı ama scheduler zaten running!")
            return
        """Arka plan görevleri için scheduler kurulumu"""
        self.scheduler = AsyncIOScheduler(timezone=pytz.timezone('Europe/Istanbul'))
        # Periyodik görevleri ekle
        self.scheduler.add_job(
            update_all_user_balances,
            'interval',
            minutes=15,
            max_instances=1,
            coalesce=True,
            args=[self.application]
        )

        try:
            training_time = time(hour=3, minute=0, tzinfo=pytz.timezone('Europe/Istanbul'))
            self.scheduler.add_job(
                scheduled_model_training,
                'cron',
                hour=training_time.hour,
                minute=training_time.minute,
                name="daily_model_training",
                args=[self.application]
            )
            logger.info(f"✅ Otomatik model eğitimi zamanlandı: {training_time.strftime('%H:%M')}")
        except Exception as e:
            logger.error(f"❌ Scheduler hatası: {e}", exc_info=True)
        self.scheduler.start()
        logger.info(f"[SCHED] scheduler_id={id(self.scheduler)} running={self.scheduler.running}")
        logger.info("✅ Scheduler başlatıldı.")

    @staticmethod
    async def run_periodic_scans(application: Application):
        """Zamanlanmış olarak AI ve Strateji taramalarını çalıştırır."""
        logger.info("⏰ Periyodik alarm tarama döngüsü başladı...")
        context = CallbackContext(application)
        
        # Gerekli kullanıcı ve borsa bilgilerini context'e ekleyin
        # Bu bilgilerin veritabanından veya bir config'den alınması gerekebilir.
        # Şimdilik varsayılan admin ve mexc kullanıyoruz.
        from config.constants import ADMIN_USER_ID
        from data.olimpos_data import get_api_key

        user_id = ADMIN_USER_ID
        exchange_name = 'mexc' # Veya varsayılan borsanız
        api_keys = get_api_key(user_id, exchange_name)

        context.user_data['user_id'] = user_id
        context.user_data['exchange'] = exchange_name
        if api_keys:
            context.user_data.update(api_keys)

        try:
            # Yeni akıllı temizleme ve tarama fonksiyonunu çağır
            await OlimposStrategy.run_smart_scan_and_cleanup(context)
        except Exception as e:
            logger.error(f"❌ Periyodik tarama sırasında kritik hata: {e}", exc_info=True)

    async def setup_application(self):
        """Telegram bot uygulamasını kur"""
        application = Application.builder().token(BOT_TOKEN).build()

        OlimposStrategy._application = application
        OlimposStrategy.initialize_alarm_system()
        logging.info("✅ Bot ve alarm sistemi başlatıldı")

        conv_handler = await create_conversation_handler()
        application.add_handler(conv_handler)

        application.add_handler(
            MessageHandler(
                filters.ChatType.CHANNEL & filters.COMMAND,
                self.handle_channel_message
            )
        )
        application.add_handler(
            CallbackQueryHandler(
                self.log_callbacks,
                pattern=r'^(?!ai_|strat_|tfth_|param_|select_symbol_|timeframe_|remove_alarm_|set_tuner_)'
            )
        )

        dynamic_admins = await get_dynamic_admins(application)
        application.bot_data['dynamic_admins'] = dynamic_admins
        application.add_error_handler(self.error_handler)

        return application

    @staticmethod
    async def handle_channel_message(update: Update, context):
        """Kanal mesajlarını işler"""
        try:
            if update.channel_post and update.channel_post.text:
                if update.channel_post.text.startswith('/signal'):
                    await signal_handler(update, context)
        except Exception as error22:
            logger.error(f"Kanal mesajı işleme hatası: {error22}")

    async def start_background_tasks(self):
        """Arka plan görevlerini başlat"""
        try:
            self.tasks['periodic_check'] = asyncio.create_task(
                periodic_check(self.application),
                name="periodic_check_task"
            )

            # --- YENİ: GLOBAL EQUITY + DAILY SUMMARY TASK (BINANCE) ---
            self.equity_service = EquityService(
                daily_path="data/daily_summary.jsonl",
                interval_sec=60  # 60 saniyede bir equity ölç
            )
            self.tasks["equity_service"] = self.equity_service.start()
            try:
                if self.application:
                    self.application.bot_data['equity_service'] = self.equity_service
            except Exception:
                pass

            logger.info("✅ Arka plan görevleri başlatıldı")
        except Exception as error23:
            logger.error(f"Arka plan görevleri başlatma hatası: {error23}", exc_info=True)

    @staticmethod
    async def log_callbacks(update: Update, context):
        _=context
        """Callback loglaması"""
        try:
            logger.info(f"Callback alındı: {update.callback_query.data}")
        except Exception as error24:
            logger.error(f"Callback log hatası: {error24}")

    @staticmethod
    async def error_handler(_: Update, context):
        """Merkezi hata yakalama"""
        try:
            # Hata nesnesinin türünü ve detaylarını kontrol et
            error25 = context.error

            if error25 is None:
                logger.error("Boş hata nesnesi")
                return

            # Hata detaylarını daha fazla logla
            import traceback

            logger.error(f"Hata Türü: {type(error25)}")
            logger.error(f"Hata Mesajı: {str(error25)}")

            # Detaylı stack trace'i logla
            error_trace = traceback.format_exc()
            logger.error(f"Hata Detayları:\n{error_trace}")

            # Özel hata türleri için ayrıntılı işleme
            if isinstance(error25, AttributeError):
                logger.error("Özellik Hatası: Bir nesne üzerinde var olmayan bir özelliğe erişilmeye çalışıldı")
            elif isinstance(error25, TypeError):
                logger.error("Tür Hatası: Yanlış veri türü kullanıldı")
            elif isinstance(error25, ValueError):
                logger.error("Değer Hatası: Geçersiz bir değer kullanıldı")

        except Exception as error26:
            # En dış hata yakalama
            logger.error(f"Hata yakalama sırasında beklenmeyen hata: {error26}")
            import traceback
            logger.error(f"Hata yakalama hatası detayları:\n{traceback.format_exc()}")

    async def run(self):
        """Ana çalıştırma fonksiyonu"""
        try:
            # 1. Veritabanını başlat (Thread içinde)
            await self.initialize_database()

            # 2. Uygulamayı kur
            self.application = await self.setup_application()

            # 3. Scheduler'ı başlat
            await self.setup_scheduler()

            # 4. İlk senkronizasyonu yap (Thread içinde yapmak daha güvenli)
            logger.info("🔄 Telegram kanalları senkronize ediliyor...")
            await asyncio.to_thread(sync_telegram_notification_channels)

            # 5. Arka plan görevlerini başlat
            await self.start_background_tasks()

            # 6. Botu başlat (Polling)
            logger.info("🚀 Polling başlatılıyor...")
            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling(
                drop_pending_updates=True,
                allowed_updates=Update.ALL_TYPES
            )

            logger.info("✅ Olimpos Bot başarıyla çalışıyor ve mesaj bekliyor.")

            # PTB 22.5: updater.idle() yok. Sonsuz bekleme:
            self._stop_event = asyncio.Event()
            loop = asyncio.get_running_loop()

            def _request_shutdown():
                try:
                    self._stop_event.set()
                except Exception:
                    pass

            # Windows'ta add_signal_handler her zaman çalışmayabilir, o yüzden try/except
            for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
                if sig is None:
                    continue
                try:
                    loop.add_signal_handler(sig, _request_shutdown)  # type: ignore[call-arg]
                except NotImplementedError:
                    # Windows/Proactor durumları
                    pass

            try:
                await self._stop_event.wait()
            except asyncio.CancelledError:
                pass

        except Exception as error27:
            logger.error(f"❌ Bot çalıştırma hatası: {error27}", exc_info=True)
        finally:
            await self.cleanup()

    async def cleanup(self):
        # 1) equity
        if self.equity_service:
            try:
                await self.equity_service.stop()
            except Exception:
                pass

        # 2) CCXT exchange close (en kritik)
        try:
            exchanges = None
            if self.application:
                exchanges = self.application.bot_data.get("exchanges")  # dict ise
            if isinstance(exchanges, dict):
                for name, ex in exchanges.items():
                    try:
                        await ex.close()
                        logger.info(f"[CLOSE] ccxt exchange closed: {name}")
                    except Exception as e:
                        logger.warning(f"[CLOSE_ERR] {name}: {e}")
        except Exception:
            pass

        # 3) PTB shutdown
        if self.application:
            logger.info("Uygulama kapatılıyor...")
            try:
                upd = getattr(self.application, "updater", None)
                if upd:
                    await upd.stop()
            except Exception:
                pass

            await self.application.stop()
            await self.application.shutdown()

        # 4) scheduler shutdown (double log normal, aşağıda açıklıyorum)
        if self.scheduler:
            self.scheduler.shutdown(wait=False)

        # 5) tasks cancel
        for task_name, task in self.tasks.items():
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    logger.info(f"{task_name} görevi iptal edildi")
        # telebot (PyTelegramBotAPI) aiohttp session cleanup
        try:
            from telebot import asyncio_helper
            sm = getattr(asyncio_helper, "session_manager", None)
            if sm is not None:
                sess = getattr(sm, "session", None)
                if sess is not None and not sess.closed:
                    await sess.close()
                    logger.info("[CLOSE] telebot aiohttp session closed")
        except Exception as e:
            logger.warning(f"[CLOSE_ERR] telebot session close failed: {e}")

        logger.info("Tüm kaynaklar temizlendi")


async def run_bot():
    # Windows için event loop politikası
    if sys.platform.startswith('win'):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    bot_manager = OlimposBotManager()

    # Strateji sistemi ve ID sayaçlarını senkronize et
    try:
        await asyncio.to_thread(OlimposStrategy.initialize_system)
    except Exception as e:
        logger.error(f"ID sync hata: {e}")

    try:
        await bot_manager.run()
    except KeyboardInterrupt:
        print("Bot kullanıcı tarafından durduruldu.")
    except Exception as error28:
        logger.error(f"Bot çalıştırılırken hata oluştu: {error28}", exc_info=True)

if __name__ == '__main__':
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass