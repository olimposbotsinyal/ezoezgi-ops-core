# api_action_settings.py
import importlib
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
# from logger_config import setup_logging
from config.constants import *
from data.olimpos_data import db_operation

# logger = setup_logging('api_action_settings logları')
# logger.info("Bu bir bilgi mesajıdır.")


async def show_exchange_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    # logger.info("Borsa seçim menüsü gösteriliyor.")

    user_id = update.effective_user.id

    # Kullanıcının kayıtlı borsalarını al
    user_exchanges = db_operation("SELECT DISTINCT exchange FROM api_key WHERE user_id = ?", (user_id,),
                                  operation='select', fetch=True)

    keyboard = []
    for exchange in user_exchanges:
        keyboard.append(
            [InlineKeyboardButton(exchange[0].capitalize(), callback_data=f"select_exchange_{exchange[0].lower()}")])

    if not keyboard:
        await update.callback_query.edit_message_text(
            "Henüz kayıtlı bir API anahtarınız yok. Lütfen önce bir API ekleyin.")
        return

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        "Lütfen bir borsa seçin:",
        reply_markup=reply_markup
    )
    # logger.info("Kullanıcıdan borsa seçimi bekleniyor.")

    # Nereden geldiğimizi context'e kaydedelim
    context.user_data['previous_menu'] = update.callback_query.data


async def handle_exchange_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    query = update.callback_query
    if not query:
        # logger.error("Callback query bulunamadı.")
        return State.MAIN_MENU

    await query.answer()

    if '_exchange_' not in query.data:
        # logger.error(f"Geçersiz callback data: {query.data}")
        await query.edit_message_text("Bir hata oluştu. Lütfen tekrar deneyin.")
        return State.MAIN_MENU

    _, selected_exchange = query.data.split('_exchange_')
    # logger.info(f"Seçilen borsa: {selected_exchange}")

    last_action = context.user_data.get('last_action', '')
    # logger.info(f"Son işlem: {last_action}")

    if last_action == 'strateji_ayarlari':
        # Strateji ayarları sayfasına yönlendir
        from strategies.strateji_ayarlari import handle_strategy_settings
        context.user_data['selected_exchange'] = selected_exchange
        await handle_strategy_settings(update, context)
        return State.STRATEGY_SETTINGS
    else:
        module_name = f"{selected_exchange}_api_ayarlari"
        function_name = f"handle_{selected_exchange}_actions"

        try:
            module = importlib.import_module(module_name)
            handle_function = getattr(module, function_name)
            return await handle_function(update, context)
        except (ImportError, AttributeError) as e:
            #  logger.error(f"Hata oluştu: {str(e)}")
            await query.edit_message_text(f"Üzgünüm, bir hata oluştu. Lütfen daha sonra tekrar deneyin.")
            return State.MAIN_MENU
