"""
olimpos_channel.py

Bu modül, Olimpos bot'unun kanal yönetimi işlevlerini içerir.
Kanal ekleme, silme, listeleme ve kanallara mesaj gönderme gibi
işlevler bu modülde tanımlanmıştır.
"""
from telegram import Update
from telegram.ext import ContextTypes
from data.olimpos_data import *
from config.constants import *
from logger_config import setup_logging
from typing import Tuple
logger = setup_logging('olimpos_channel logları')
# logger.info("Bu bir bilgi mesajıdır.")


async def channel_menu(update: Update, _context: CallbackContext) -> State:
    """Bot kanalları menüsünü yönetir"""
    logger.info(f"olimpos_admin_Gelen callback verisi: {update.callback_query.data}")
    logger.info(f"Gelen update detayları: {update}")

    keyboard = [
        [InlineKeyboardButton("Kanal Ekle", callback_data='add_channel')],
        [InlineKeyboardButton("Kanal Sil", callback_data='delete_channel')],
        [InlineKeyboardButton("Tüm Kanallar", callback_data='list_channels')],
        [InlineKeyboardButton("Kanallara Mesaj Gönder", callback_data='send_message_to_all_channels')],
        [InlineKeyboardButton("Kullanıcılara Mesaj Gönder", callback_data='send_message_to_users')],
        [InlineKeyboardButton("🔙 Admin Menüsüne Dön", callback_data='admin_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    message_text = (
        "📢 Bot Kanalları Menüsü\n\n"
        "Bu menüden yapabileceğiniz işlemler:\n"
        "• Yeni kanal ekleme\n"
        "• Mevcut kanalları silme\n"
        "• Kanal listesini görüntüleme\n"
        "• Toplu mesaj gönderme\n"
        "\nLütfen bir işlem seçin."
    )

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(
            text=message_text,
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            text=message_text,
            reply_markup=reply_markup
        )

    return State.CHANNEL_MENU


async def handle_channel_buttons(update: Update, context: CallbackContext) -> int:
    # logger.info("channel butonları işleniyor.")
    query = update.callback_query
    await query.answer()
    # logger.info(f"olimpos_Channel callback verisi: {query.data}")

    handlers = {
        'channel_menu': channel_menu,
        'add_channel': prepare_add_channel,
        'delete_channel': prepare_delete_channel,
        'list_channels': list_channels,
        'send_message_to_channel': send_message_to_channel,
        'send_message_to_users': send_message_to_users,
        'send_message_to_all_channels': send_message_to_all_channels,
        'back_to_channel_menu': channel_menu,
    }

    if query.data in handlers:
        # logger.info(f"olimpos_Channel menü callback verisi: {query.data}")
        return await handlers[query.data](update, context)

    if query.data == 'back_to_admin_menu':
        from olimpos_admin import admin_menu
        return await admin_menu(update, ContextTypes.DEFAULT_TYPE)

    # Özel durumlar için kontroller
    if query.data.startswith('send_message_'):
        channel_id = query.data.split('_')[-1]
        return await send_message_to_channel(update, context, channel_id)

    elif query.data == 'send_message_to_all_channels':
        # Tüm kanallara mesaj gönderme işlemi için
        return await send_message_to_all_channels(update, context)

    elif query.data.startswith('delete_channel_'):
        return await process_delete_channel(update, context)

    # logger.warning(f"Olimpos_channel Unhandled callback data: {query.data}")
    await query.edit_message_text("Olimpos_Channel Geçersiz işlem. Lütfen tekrar deneyin.")

    return State.CHANNEL_MENU


async def prepare_add_channel(update: Update, _context: CallbackContext) -> State:
    try:
        await update.callback_query.answer()

        keyboard = [[InlineKeyboardButton("Geri", callback_data='back_to_channel_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.edit_message_text(
            "Lütfen eklemek istediğiniz kanalın:\n"
            "- Kullanıcı adını (@kanal_adi)\n"
            "- Kanal ID'sini\n"
            "- veya Gizli Kanal Bağlantısını (https://t.me/+...)\n"
            "gönderin:",
            reply_markup=reply_markup
        )
        return State.ADD_CHANNEL
    except Exception as e:
        logger.error(f"Hata prepare_add_channel'da: {str(e)}")
        keyboard = [[InlineKeyboardButton("Ana Menüye Dön", callback_data='back_to_channel_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.edit_message_text(
            "Kanal ekleme işleminde hata oluştu.",
            reply_markup=reply_markup
        )
        return State.CHANNEL_MENU


async def check_channel_permissions(chat, context: CallbackContext) -> Tuple[bool, str]:
    """
    Botun bir kanaldaki yetkilerini kontrol eder.
    Returns:
        Tuple[bool, str]: (Yetki Var mı, Açıklama Mesajı)
    """
    try:
        bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)

        if bot_member.status not in ['administrator', 'creator']:
            return False, "Bot kanala yönetici olarak eklenmemiş."

        # Gerekli yetkileri ve Türkçe açıklamalarını tanımla
        required_permissions = {
            'can_post_messages':'Mesaj Gönderme',
            'can_edit_messages':'Mesaj Düzenleme',
            'can_delete_messages':'Mesaj Silme'
        }

        missing_permissions = []
        # Botun sahip olduğu yetkileri kontrol et
        for perm, desc in required_permissions.items():
            # Creator her zaman tüm yetkilere sahiptir.
            if bot_member.status == 'creator':
                continue
            # Yönetici ise yetkileri kontrol et
            if not getattr(bot_member, perm, False):
                missing_permissions.append(desc)

        if missing_permissions:
            return False, f"Eksik yetkiler: {', '.join(missing_permissions)}"

        return True, "Tüm temel yönetici yetkileri mevcut."

    except BadRequest as e:
        return False, f"Kanala erişilemiyor veya bot üye değil. Hata: {e.message}"
    except Exception as e:
        logger.error(f"Yetki kontrolü sırasında beklenmedik hata: {e}", exc_info=True)
        return False, f"Beklenmedik bir hata oluştu: {e}"


async def process_add_channel(update: Update, context: CallbackContext) -> State:
    """
    Kanal ekleme işlemini gerçekleştiren fonksiyon.

    Args:
        update (Update): Telegram update nesnesi
        context (ContextTypes.DEFAULT_TYPE): Telegram context nesnesi

    Returns:
        State: İşlem sonrası durum
    """
    try:
        input_text = update.message.text.strip()
        chat = None

        # Gizli kanal bağlantısı kontrolü
        if "https://t.me/+" in input_text:
            try:
                # Sadece hash kısmını al
                invite_hash = input_text.split('+')[-1]

                try:
                    # Doğrudan join link ile dene
                    chat = await context.bot.get_chat(f"https://t.me/joinchat/{invite_hash}")
                except BadRequest:
                    try:
                        # Hash ile dene
                        chat = await context.bot.get_chat(invite_hash)
                    except BadRequest:
                        # Chat ID ile dene
                        try:
                            updates = await context.bot.get_updates(limit=100)
                            for update in updates:
                                if update.message and update.message.chat:
                                    if update.message.chat.type in ['channel', 'supergroup']:
                                        try:
                                            member = await context.bot.get_chat_member(
                                                update.message.chat.id,
                                                context.bot.id
                                            )
                                            if member.status in ['administrator', 'creator']:
                                                chat = update.message.chat
                                                break

                                        except Exception as e:
                                            logging.error(f"Hata: {e}")
                                            continue

                        except Exception as e:
                            logging.error(f"Hata: {e}")
                            pass

                if not chat:
                    raise BadRequest("Kanal bulunamadı")

            except Exception as e:
                logger.error(f"Gizli kanal hatası: {str(e)}")
                keyboard = [[
                    InlineKeyboardButton("Tekrar Dene", callback_data='add_channel'),
                    InlineKeyboardButton("Ana Menüye Dön", callback_data='main_menu')
                ]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(
                    "❌ Gizli kanal bağlantısı işlenirken hata oluştu!\n\n"
                    "Lütfen şu adımları takip edin:\n"
                    "1. Botu önce kanala ekleyin\n"
                    "2. Botu yönetici yapın\n"
                    "3. Tüm yönetici yetkilerini verin\n"
                    "4. Kanalın ID'sini (-100 ile başlayan) kullanarak tekrar deneyin",
                    reply_markup=reply_markup
                )
                return State.CHANNEL_MENU

        else:
            # Normal kanal ID veya kullanıcı adı işleme
            try:
                if input_text.startswith('-100'):
                    channel_id = input_text
                elif input_text.startswith('@'):
                    channel_id = input_text
                else:
                    try:
                        numeric_value = int(input_text.replace('-', ''))
                        channel_id = f"-100{numeric_value}"
                    except ValueError:
                        channel_id = f"@{input_text.lstrip('@')}"

                chat = await context.bot.get_chat(channel_id)

            except BadRequest as e:
                keyboard = [[
                    InlineKeyboardButton("Tekrar Dene", callback_data='add_channel'),
                    InlineKeyboardButton("Ana Menüye Dön", callback_data='main_menu')
                ]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(
                    f"❌ Kanal bulunamadı veya erişilemiyor!\n\n"
                    "Lütfen şunları kontrol edin:\n"
                    "1. Bot'un kanala ekli olduğunu\n"
                    "2. Bot'un yönetici olduğunu\n"
                    "3. Kanal adı/ID'sinin doğruluğunu\n\n"
                    f"Teknik Hata: {str(e)}",
                    reply_markup=reply_markup
                )
                return State.CHANNEL_MENU

        # Veritabanına kaydet
        try:
            channel_username = chat.username if hasattr(chat, 'username') else None

            if add_channel(
                    str(chat.id),
                    chat.title,
                    channel_username,
                    update.effective_user.id
            ):
                # YENİ: Yetki kontrolü yap ve sonucu kullanıcıya bildir.
                has_perms, perm_message = await check_channel_permissions(chat, context)
                perm_icon = "✅" if has_perms else "⚠️"

                # Başarılı ekleme mesajı
                keyboard = [[
                    InlineKeyboardButton("Kanal Menüsüne Dön", callback_data='channel_menu')
                ]]
                reply_markup = InlineKeyboardMarkup(keyboard)

                success_message = (
                    f"✅ Kanal başarıyla eklendi!\n\n"
                    f"📌 Kanal Bilgileri:\n"
                    f"• İsim: {chat.title}\n"
                    f"• ID: {chat.id}\n"
                    f"• Tip: {chat.type.capitalize()}\n\n"
                    f"{perm_icon} Yetki Durumu: {perm_message}"
                 )

                if channel_username:
                    success_message += f"\n• Kullanıcı Adı: @{channel_username}"

                await update.message.reply_text(
                    success_message,
                    reply_markup=reply_markup
                )
                return State.CHANNEL_MENU
            else:
                raise Exception("Veritabanı işlemi başarısız oldu")

        except Exception as e:
            logger.error(f"Veritabanı hatası: {str(e)}")
            keyboard = [[
                InlineKeyboardButton("Tekrar Dene", callback_data='add_channel'),
                InlineKeyboardButton("Ana Menüye Dön", callback_data='main_menu')
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "❌ Veritabanına kaydedilirken bir hata oluştu. Lütfen tekrar deneyin.",
                reply_markup=reply_markup
            )
            return State.CHANNEL_MENU

    except Exception as e:
        logger.error(f"Genel hata: {str(e)}")
        keyboard = [[
            InlineKeyboardButton("Tekrar Dene", callback_data='add_channel'),
            InlineKeyboardButton("Ana Menüye Dön", callback_data='main_menu')
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"❌ Beklenmeyen bir hata oluştu:\n{str(e)}\n\n"
            "Lütfen tekrar deneyin veya farklı bir kanal eklemeyi deneyin.",
            reply_markup=reply_markup
        )
        return State.CHANNEL_MENU


async def prepare_delete_channel(update: Update, _context: CallbackContext) -> State:
    try:
        channels = get_all_channels()
        keyboard = [
            [InlineKeyboardButton(channel['channel_name'], callback_data=f'delete_channel_{channel["channel_id"]}')]
            for channel in channels
        ]
        keyboard.append([InlineKeyboardButton("Geri", callback_data='back_to_channel_menu')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.edit_message_text("Silmek istediğiniz kanalı seçin:", reply_markup=reply_markup)
        return State.DELETE_CHANNEL
    except Exception as e:
        logger.error(f"Hata prepare_delete_channel'da: {str(e)}")
        await update.callback_query.edit_message_text("Kanal silme işleminde hata oluştu.")
        return State.CHANNEL_MENU


async def process_delete_channel(update: Update, context: CallbackContext) -> State:
    query = update.callback_query
    channel_id = query.data.split('_')[-1]  # Callback data'dan channel_id'yi alıyoruz
    await query.answer()

    if channel_id == 'back_to_channel_menu':
        return await channel_menu(update, context)

    try:
        channel_id = int(channel_id)  # channel_id'yi int'e çeviriyoruz
        delete_channel(channel_id)  # Bu, olimpos_data modülünden gelen fonksiyon

        keyboard = [[InlineKeyboardButton("Ana Menüye Dön", callback_data='back_to_channel_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            f"{channel_id} ID'li kanal başarıyla silindi.",
            reply_markup=reply_markup
        )
    except ValueError:
        keyboard = [[InlineKeyboardButton("Ana Menüye Dön", callback_data='back_to_channel_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            "Geçersiz kanal ID'si.",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Kanal silinirken hata oluştu: {str(e)}")

        keyboard = [[InlineKeyboardButton("Ana Menüye Dön", callback_data='back_to_channel_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            "Kanal silinirken bir hata oluştu. Lütfen daha sonra tekrar deneyin.",
            reply_markup=reply_markup
        )

    return State.CHANNEL_MENU


async def list_channels(update: Update, _context: CallbackContext) -> State:
    try:
        channels = get_all_channels()
        if not channels:
            await update.callback_query.edit_message_text("Henüz hiç kanal eklenmemiş.")
            return State.CHANNEL_MENU

        # Ana başlık mesajını düzenle
        if update.callback_query:
            await update.callback_query.edit_message_text("Olimpos Cripto Botunun yönettiği Tüm Kanal Aşağıdadır")

        # Her kanal için ayrı bir mesaj gönder
        for i, channel in enumerate(channels):
            # İstenen formatta mesaj metnini oluştur
            message_text = (
                "Olimpos Cripto Botunun Yönettiği Kanallardan " f"{i + 1}. Kanal" " " "Aşağıdadır\n\n"
                f"{channel['channel_name']} (Kanal_id : {channel['channel_id']} )"
            )

            # Butonları oluştur
            keyboard = [
                [
                    InlineKeyboardButton("Mesaj Yolla", callback_data=f"send_message_{channel['channel_id']}"),
                    InlineKeyboardButton("Sil", callback_data=f"delete_{channel['channel_id']}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            # Yeni mesajı gönder
            await update.effective_chat.send_message(
                text=message_text,
                reply_markup=reply_markup
            )

        # En sona tek bir geri butonu ekle
        back_keyboard = [[InlineKeyboardButton("🔙 Geri", callback_data='back_to_channel_menu')]]
        await update.effective_chat.send_message("İşlemler tamamlandı.", reply_markup=InlineKeyboardMarkup(back_keyboard))

        return State.CHANNEL_MENU
    except Exception as e:
        logger.error(f"Hata list_channels'da: {str(e)}")
        await update.message.reply_text("Kanal listeleme işleminde hata oluştu.")
        return State.CHANNEL_MENU


async def send_message_to_channel(update: Update, context: CallbackContext, channel_id: str = None) -> State:
    if channel_id:
        context.user_data['target_channel'] = int(channel_id)
        await update.callback_query.edit_message_text("Lütfen göndermek istediğiniz mesajı yazın:")
    else:
        await update.callback_query.edit_message_text("Lütfen tüm kanallara göndermek istediğiniz mesajı yazın:")
    return State.SEND_MESSAGE_TO_CHANNEL


async def process_send_message_to_channel(update: Update, context: CallbackContext) -> State:
    try:
        message = update.message.text
        channel_id = context.user_data['target_channel']

        if not message.strip():  # Mesajın boş olup olmadığını kontrol et
            await update.message.reply_text("Gönderilecek mesaj boş olamaz. Lütfen bir mesaj yazın.")
            return State.SEND_MESSAGE_TO_CHANNEL

        # Mesajı gönder
        await context.bot.send_message(chat_id=channel_id, text=message)
        await update.message.reply_text("Mesaj gönderildi!")

        return await list_channels(update, context)
    except Exception as e:
        logger.error(f"Mesaj gönderilirken hata oluştu: {str(e)}")
        await update.message.reply_text("Mesaj gönderilirken bir hata oluştu.")
        return await list_channels(update, context)


async def send_message_to_all_channels(update: Update, _context: CallbackContext) -> State:
    await update.callback_query.edit_message_text("Lütfen tüm kanallara göndermek istediğiniz mesajı yazın:")
    return State.SEND_MESSAGE_TO_ALL_CHANNELS


async def process_send_message_to_all_channels(update: Update, context: CallbackContext) -> State:
    try:
        message = update.message.text
        channels = get_all_channels()

        if not channels:
            await update.message.reply_text("Henüz hiç kanal eklenmemiş.")
            return await channel_menu(update, context)

        success_count = 0
        fail_count = 0
        for channel in channels:
            try:
                await context.bot.send_message(chat_id=channel['channel_id'], text=message)
                success_count += 1
            except Exception as e:
                logger.error(f"Mesaj gönderilirken hata oluştu (Kanal: {channel['channel_name']}): {str(e)}")
                fail_count += 1

        result_message = f"Mesaj gönderme işlemi.\n"
        result_message += f"Başarılı: {success_count}\n"
        result_message += f"Başarısız: {fail_count}"
        await update.message.reply_text(result_message)
    except Exception as e:
        logger.error(f"Tüm kanallara mesaj gönderilirken hata oluştu: {str(e)}")
        await update.message.reply_text("Tüm kanallara mesaj gönderme işleminde hata oluştu.")

    # Burada doğrudan channel_menu fonksiyonunu çağırmak yerine, yeni bir mesaj gönderiyoruz
    keyboard = [[InlineKeyboardButton("Kanal Menüsüne Dön", callback_data='back_to_channel_menu')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("İşlem tamamlandı.", reply_markup=reply_markup)

    return State.CHANNEL_MENU


async def send_message_to_users(update: Update, _context: CallbackContext) -> State:
    keyboard = [
        [InlineKeyboardButton("Admin Kullanıcılar", callback_data='send_admin_users')],
        [InlineKeyboardButton("Normal Kullanıcılar", callback_data='send_normal_users')],
        [InlineKeyboardButton("Geri", callback_data='back_to_channel_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    message = (
        "📩 Mesaj Gönderme Menüsü\n\n"
        "Mesaj göndermek istediğiniz kullanıcı türünü seçin:\n\n"
        "⚠️ Önemli Notlar:\n"
        "• Kullanıcının botu başlatmış (/start) olması gerekir\n"
        "• Bot ile etkileşime geçmemiş kullanıcılara mesaj gönderilemez\n"
        "• Mesaj gönderimi başarısız olursa detaylı hata bilgisi verilecektir"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text=message,
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            text=message,
            reply_markup=reply_markup
        )

    return State.SEND_MESSAGE_TO_USERS


async def send_admin_users(update: Update, _context: CallbackContext) -> State:
    # Benzersiz admin kullanıcıları al
    admin_users = get_all_admin_users()

    # Benzersiz kullanıcı listesi oluştur
    unique_users = {}
    for user in admin_users:
        user_id = user['user_id']
        if user_id not in unique_users:
            unique_users[user_id] = user

    if not unique_users:
        await update.callback_query.edit_message_text("Hiç admin kullanıcı bulunamadı.")
        return State.SEND_MESSAGE_TO_USERS

    # Benzersiz kullanıcılardan klavye oluştur
    keyboard = [
        [InlineKeyboardButton(f"Admin: {user['username']}", callback_data=f"send_message_admin_{user['user_id']}")]
        for user in unique_users.values()
    ]
    keyboard.append([InlineKeyboardButton("Geri", callback_data='back_to_send_message_menu')])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text(
        text="Mesaj göndermek istediğiniz admin kullanıcıyı seçin:",
        reply_markup=reply_markup
    )
    return State.SEND_MESSAGE_TO_ADMIN_USERS


async def process_send_message_to_admin(update: Update, context: CallbackContext) -> State:
    query = update.callback_query
    try:
        user_id = int(query.data.split('_')[3])  # Burada 3. indeksi kontrol edin
        context.user_data['target_user'] = user_id
        await query.edit_message_text("Lütfen göndermek istediğiniz mesajı yazın:")
        return State.SEND_MESSAGE_TO_ADMIN_USER
    except Exception as e:
        logger.error(f"Hata process_send_message_to_admin'da: {str(e)}")
        await query.edit_message_text("Admin kullanıcıya mesaj gönderme işleminde hata oluştu.")
        return State.CHANNEL_MENU


async def process_send_message_to_admin_user(update: Update, context: CallbackContext) -> State:
    message = update.message.text
    user_id = context.user_data['target_user']

    if not message.strip():
        await update.message.reply_text("Gönderilecek mesaj boş olamaz. Lütfen bir mesaj yazın.")
        return await send_admin_users(update, context)

    try:
        await context.bot.send_message(chat_id=user_id, text=message)
        await update.message.reply_text("Mesaj başarıyla gönderildi!")
    except Exception as e:
        logger.error(f"Mesaj gönderilirken hata oluştu: {str(e)}")
        await update.message.reply_text("Mesaj gönderilirken bir hata oluştu.")

    return await send_admin_users(update, context)


async def send_normal_users(update: Update, _context: CallbackContext) -> State:
    query = update.callback_query

    # Benzersiz normal kullanıcıları al
    normal_users = get_all_normal_users()

    # Benzersiz kullanıcı listesi oluştur
    unique_users = {}
    for user in normal_users:
        user_id = user['user_id']
        if user_id not in unique_users:
            unique_users[user_id] = user

    if not unique_users:
        await query.edit_message_text("Hiç normal kullanıcı bulunamadı.")
        return State.SEND_MESSAGE_TO_NORMAL_USERS

    try:
        # Benzersiz kullanıcılardan klavye oluştur
        keyboard = [
            [InlineKeyboardButton(user['username'], callback_data=f'send_message_user_{user["user_id"]}')]
            for user in unique_users.values()
        ]
        keyboard.append([InlineKeyboardButton("Geri", callback_data='back_to_send_message_users')])

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            text="Mesaj göndermek istediğiniz normal kullanıcıyı seçin:",
            reply_markup=reply_markup
        )

        return State.SEND_MESSAGE_TO_NORMAL_USERS
    except Exception as e:
        logger.error(f"Hata send_normal_users'da: {str(e)}")
        await query.edit_message_text("Normal kullanıcıları listeleme işleminde hata oluştu.")
        return State.CHANNEL_MENU


async def process_send_message_to_selected_user(update: Update, context: CallbackContext) -> State:
    message = update.message.text
    user_id = context.user_data.get('target_user')

    if not message.strip():  # Mesajın boş olup olmadığını kontrol et
        await update.message.reply_text("Gönderilecek mesaj boş olamaz. Lütfen bir mesaj yazın.")
        return await send_message_to_users(update, context)

    if user_id is not None:
        try:
            await context.bot.send_message(chat_id=user_id, text=message)
            await update.message.reply_text("Mesaj başarıyla gönderildi!")
        except Exception as e:
            logger.error(f"Mesaj gönderilirken hata oluştu: {str(e)}")
            await update.message.reply_text("Mesaj gönderilirken bir hata oluştu.")
    else:
        await update.message.reply_text("Geçersiz kullanıcı seçildi. Lütfen tekrar deneyin.")

    return await send_message_to_users(update, context)


async def process_send_message_to_user(update: Update, context: CallbackContext) -> State:
    try:
        message = update.message.text
        user_id = context.user_data.get('target_user')

        if not message.strip():
            await update.message.reply_text("Gönderilecek mesaj boş olamaz. Lütfen bir mesaj yazın.")
            return State.WAITING_FOR_MESSAGE

        if user_id:
            try:
                # Kullanıcı ID'sini integer'a çevir
                user_id = int(user_id)

                try:
                    # Önce kullanıcının bot ile etkileşimde olup olmadığını kontrol et
                    chat = await context.bot.get_chat(user_id)
                    await context.bot.send_message(chat_id=user_id, text=message)
                    await update.message.reply_text(
                        "✅ Mesaj başarıyla gönderildi!\n"
                        f"Alıcı: {chat.full_name if hasattr(chat, 'full_name') else chat.title}"
                    )
                except BadRequest as e:
                    if "Chat not found" in str(e):
                        await update.message.reply_text(
                            "❌ Mesaj gönderilemedi!\n\n"
                            "Nedeni: Kullanıcı henüz bot ile etkileşime geçmemiş.\n"
                            "Çözüm: Kullanıcının önce botu başlatması (/start) gerekiyor."
                        )
                    else:
                        await update.message.reply_text(
                            f"❌ Mesaj gönderilemedi!\n\n"
                            f"Hata: {str(e)}"
                        )
                    logger.error(f"Mesaj gönderme hatası (user_id: {user_id}): {str(e)}")
            except ValueError:
                await update.message.reply_text(
                    "❌ Geçersiz kullanıcı ID'si.\n"
                    "Lütfen tekrar kullanıcı seçin."
                )
        else:
            await update.message.reply_text(
                "❌ Hedef kullanıcı seçilmedi.\n"
                "Lütfen önce bir kullanıcı seçin."
            )

        # Kullanıcı seçme menüsüne geri dön
        return await send_message_to_users(update, context)

    except Exception as e:
        logger.error(f"Mesaj gönderme işleminde beklenmeyen hata: {str(e)}", exc_info=True)
        await update.message.reply_text(
            "❌ Beklenmeyen bir hata oluştu.\n"
            "Lütfen daha sonra tekrar deneyin."
        )
        return await send_message_to_users(update, context)


async def process_send_message_to_users(update: Update, context: CallbackContext) -> State:
    if update.callback_query:
        query = update.callback_query
        await query.answer()

        callback_data = query.data
        if callback_data == 'send_admin_users':
            return await send_admin_users(update, context)
        elif callback_data == 'send_normal_users':
            return await send_normal_users(update, context)
        elif callback_data.startswith('send_message_admin_'):
            user_id = callback_data.split('_')[-1]
            context.user_data['target_user'] = user_id
            await query.edit_message_text("Lütfen admin kullanıcıya göndermek istediğiniz mesajı yazın:")
            return State.WAITING_FOR_MESSAGE
        elif callback_data.startswith('send_message_user_'):
            user_id = callback_data.split('_')[-1]
            context.user_data['target_user'] = user_id
            await query.edit_message_text("Lütfen normal kullanıcıya göndermek istediğiniz mesajı yazın:")
            return State.WAITING_FOR_MESSAGE
        elif callback_data == 'back_to_send_message_menu':
            return await send_message_to_users(update, context)
        elif callback_data == 'back_to_channel_menu':
            return await channel_menu(update, context)
        else:
            await query.edit_message_text("Geçersiz işlem. Lütfen tekrar deneyin.")
            return await send_message_to_users(update, context)

    elif update.message:
        message = update.message.text
        user_id = context.user_data.get('target_user')

        if not message.strip():
            await update.message.reply_text("Gönderilecek mesaj boş olamaz. Lütfen bir mesaj yazın.")
            return State.WAITING_FOR_MESSAGE

        if user_id is not None:
            try:
                await context.bot.send_message(chat_id=user_id, text=message)
                await update.message.reply_text("Mesaj başarıyla gönderildi!")
            except Exception as e:
                logger.error(f"Mesaj gönderilirken hata oluştu: {str(e)}")
                await update.message.reply_text("Mesaj gönderilirken bir hata oluştu.")
        else:
            await update.message.reply_text("Geçersiz kullanıcı seçildi. Lütfen tekrar deneyin.")

        # Kullanıcı listesinin bulunduğu menüye geri dön
        return await send_message_to_users(update, context)

    return State.SEND_MESSAGE_TO_USERS


async def process_send_all_users(update: Update, context: CallbackContext) -> State:
    message = update.message.text
    users = get_all_users()

    success_count = 0
    fail_count = 0
    not_started_count = 0

    for user in users:
        try:
            await context.bot.send_message(chat_id=user['user_id'], text=message)
            success_count += 1
        except BadRequest as e:
            if "Chat not found" in str(e):
                not_started_count += 1
            else:
                fail_count += 1
            logger.error(f"Mesaj gönderme hatası (user: {user['username']}): {str(e)}")
        except Exception as e:
            fail_count += 1
            logger.error(f"Beklenmeyen hata (user: {user['username']}): {str(e)}")

    status_message = (
        f"📊 Mesaj Gönderim Raporu:\n"
        f"✅ Başarılı: {success_count}\n"
        f"❌ Başarısız: {fail_count}\n"
        f"⚠️ Bot'u başlatmamış: {not_started_count}\n"
        f"📝 Toplam: {len(users)}"
    )

    await update.message.reply_text(status_message)
    return await send_message_to_users(update, context)


async def error_handler(update: Update, context: CallbackContext):
    logger.error(f"Hata oluştu: {context.error}", exc_info=True)
    await update.message.reply_text("Bir hata oluştu. Lütfen daha sonra tekrar deneyin.")
