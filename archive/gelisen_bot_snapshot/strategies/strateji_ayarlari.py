# strateji_ayarlari.py dosyası burdan başlamaktadır

import asyncio
from telegram import Update, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.constants import ParseMode
from telegram.helpers import escape

from telegram.ext import CallbackContext
from data.olimpos_data import (db_operation,
    get_all_admins, update_user_settings, get_user_settings, get_user_info, get_user_channel_info, format_date)
from datetime import datetime, timedelta, timezone
from logger_config import setup_logging
from telegram.error import BadRequest, Forbidden
import logging
from typing import Optional, Tuple, Dict, Union, List
from config.constants import State
import re


logger = setup_logging('strateji_ayarlari logları')
# logger.info("Bu bir bilgi mesajıdır.")
DATE_FORMAT_WITHOUT_SECONDS = "%Y-%m-%d %H:%M"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


# Chat bazlı kilit: aynı chat'te aynı anda 2 farklı show_strategy_settings çalışmasın
def _get_chat_lock(context: CallbackContext, chat_id: int) -> asyncio.Lock:
    locks = context.application.bot_data.setdefault("chat_locks", {})
    lock = locks.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        locks[chat_id] = lock
    return lock


async def _safe_delete_message(context: CallbackContext, chat_id: int, message_id: int) -> None:
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except (BadRequest, Forbidden):
        # BadRequest: çok eski / zaten silinmiş / silinemez
        # Forbidden: yetki yok (grup admin değil vb.)
        pass
    except Exception:
        pass


async def _safe_answer(query: Optional[CallbackQuery]) -> None:
    if not query:
        return
    try:
        await query.answer()
    except Exception:
        # zaten answered olabilir; akışı bozmayalım
        pass


async def _safe_edit_or_send(
    *,
    query: Optional[CallbackQuery],
    context: CallbackContext,
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup,
    parse_mode=ParseMode.HTML,
) -> None:
    """
    Önce edit dener; olmazsa send eder. Telegram kaynaklı BadRequest'lerde akışı düşürmez.
    """
    # 1) Edit dene
    if query and query.message:
        try:
            await asyncio.wait_for(
                query.edit_message_text(
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode
                ),
                timeout=10
            )
            return
        except BadRequest as e:
            msg = str(e).lower()
            # "message is not modified" veya "message to edit not found" gibi durumlarda send'e düş
            logger.warning(f"edit_message_text başarısız, send'e düşüyorum: {e}")
        except asyncio.TimeoutError:
            logger.warning("edit_message_text timeout oldu, send'e düşüyorum.")
        except Exception as e:
            logger.warning(f"edit_message_text beklenmedik hata, send'e düşüyorum: {e}")

    # 2) Send
    await asyncio.wait_for(
        context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        ),
        timeout=10
    )


def get_binance_server_time(client):
    try:
        server_time = client.get_server_time()
        return server_time['serverTime']
    except Exception as e:
        print(f"Sunucu zamanı alınamadı: {e}")
        return None


def parse_date(date_string: str) -> Optional[datetime]:
    """
    Verilen string'i UTC zaman dilimine sahip bir datetime nesnesine çevirir.
    Hata durumunda None döner.
    """
    if not date_string:
        return None
    try:
        # strptime ile naive bir datetime nesnesi oluştur
        naive_dt = datetime.strptime(date_string, DATE_FORMAT)
        # UTC zaman dilimini ekleyerek aware hale getir
        return naive_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            naive_dt = datetime.strptime(date_string, DATE_FORMAT_WITHOUT_SECONDS)
            return naive_dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            logger.warning(f"Geçersiz tarih formatı: {date_string}")
            return None


async def strateji_ayarlari(update: Update, context: CallbackContext) -> int:
    try:
        # Chat ID'yi güvenli bir şekilde al
        chat_id = None

        if update.callback_query and update.callback_query.message:
            chat_id = update.callback_query.message.chat.id
        elif update.message:
            chat_id = update.message.chat.id

        # Chat ID kontrolü
        if chat_id is None:
            logger.warning("Geçerli chat ID bulunamadı")
            return State.MAIN_MENU

        # Kullanıcı kimliğini al
        user_id = update.effective_user.id if update.effective_user else None

        if not user_id:
            logger.warning("Kullanıcı kimliği alınamadı")
            await context.bot.send_message(
                chat_id=chat_id,
                text="Kullanıcı bilgisi alınamadı."
            )
            return State.MAIN_MENU

        user_exchanges = db_operation(
            "SELECT DISTINCT exchange FROM user_channel_info WHERE user_id = ?",
            (user_id,),
            operation='select',
            fetch=True
        )

        if not user_exchanges:
            await context.bot.send_message(
                chat_id=chat_id,
                text="Kayıtlı borsanız bulunmamaktadır."
            )
            return State.MAIN_MENU

        keyboard = [
            [InlineKeyboardButton(exchange[0], callback_data=f"select_exchange_{exchange[0]}")]
            for exchange in user_exchanges
        ]
        keyboard.append([InlineKeyboardButton("Ana Menüye Dön", callback_data="main_menu")])

        reply_markup = InlineKeyboardMarkup(keyboard)

        # Mesaj gönderme veya güncelleme
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                "Lütfen bir borsa seçin:",
                reply_markup=reply_markup
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text="Lütfen bir borsa seçin:",
                reply_markup=reply_markup
            )

        return State.STRATEGY_WAITING_FOR_EXCHANGE_SELECTION

    except Exception as e:
        logger.error(f"Strateji ayarları hatası: {e}")

        # Chat ID'yi güvenli bir şekilde tekrar al
        error_chat_id = None

        if update.callback_query and update.callback_query.message:
            error_chat_id = update.callback_query.message.chat.id
        elif update.message:
            error_chat_id = update.message.chat.id

        # Hata mesajı gönderme
        if error_chat_id:
            try:
                await context.bot.send_message(
                    chat_id=error_chat_id,
                    text="Bir hata oluştu. Lütfen tekrar deneyin."
                )
            except Exception as send_error:
                logger.error(f"Hata mesajı gönderme hatası: {send_error}")

        return State.MAIN_MENU


async def handle_islem_turu_selection(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    selected_exchange = context.user_data.get('selected_exchange')
    islem_turu = query.data.split('_')[-1]  # 'spot' veya 'vadeli'

    # Veritabanında güncelle
    db_operation(
        "UPDATE user_channel_info SET işlem_türü = ? WHERE user_id = ? AND exchange = ?",
        (islem_turu, user_id, selected_exchange),
        operation='update'
    )

    await query.edit_message_text(f"{selected_exchange} borsası için işlem türü '{islem_turu}' olarak ayarlandı.")
    return await show_strategy_settings(update, context)


async def handle_exchange_selection(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    # Seçilen borsa bilgisini al
    selected_exchange = query.data.split('_')[-1]  # select_exchange_binance -> binance
    context.user_data['selected_exchange'] = selected_exchange

    # Borsa seçimi sonrası strateji ayarları sayfasına geç
    return await show_strategy_settings(update, context)


async def _render_strategy_menu(
    *,
    update: Update,
    context: CallbackContext,
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup
) -> None:
    """
    Strateji menüsünü mümkünse aynı mesaj üzerinde günceller.
    message_id kaybolmuşsa / edit edilemiyorsa yeni mesaj gönderir ve id'yi kaydeder.

    Özellikler:
    - 10s timeout (edit & send)
    - "Message is not modified" => başarı kabul edilir (log INFO, send'e düşmez)
    - Diğer BadRequest/Exception => log + send'e düşer
    """
    _ = update  # parametreyi imzada tutuyoruz; şu an kullanılmıyor

    msg_id = context.user_data.get("strategy_menu_msg_id")

    async def _edit_existing(mid: int) -> bool:
        try:
            logger.info(f"[MENU_RENDER] edit denenecek chat_id={chat_id} message_id={mid}")
            await asyncio.wait_for(
                context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=mid,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML
                ),
                timeout=10
            )
            logger.info(f"[MENU_RENDER] edit OK chat_id={chat_id} message_id={mid}")
            return True

        except asyncio.TimeoutError:
            logger.error(f"[MENU_RENDER] edit TIMEOUT chat_id={chat_id} message_id={mid}")
            return False

        except BadRequest as e:
            s = str(e).lower()

            # Telegram: içerik + markup aynıysa BadRequest fırlatır. Bu normal, başarı say.
            if "message is not modified" in s:
                logger.info(f"[MENU_RENDER] edit SKIP(not modified) chat_id={chat_id} message_id={mid}")
                return True

            # Diğer BadRequest'ler: message to edit not found / can't be edited / etc.
            logger.warning(f"[MENU_RENDER] edit BadRequest chat_id={chat_id} message_id={mid}: {e}")
            return False

        except Exception as e:
            logger.error(
                f"[MENU_RENDER] edit Exception chat_id={chat_id} message_id={mid}: {e}",
                exc_info=True
            )
            return False

    async def _send_new() -> None:
        logger.info(f"[MENU_RENDER] send denenecek chat_id={chat_id}")
        sent = await asyncio.wait_for(
            context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            ),
            timeout=10
        )
        context.user_data["strategy_menu_msg_id"] = sent.message_id
        logger.info(f"[MENU_RENDER] send OK chat_id={chat_id} new_message_id={sent.message_id}")

    # 1) Eğer daha önce menü mesajı kaydedildiyse edit dene
    if msg_id:
        ok = await _edit_existing(msg_id)
        if ok:
            return

    # 2) Edit olmadıysa yeni mesaj gönder ve message_id'yi kaydet
    await _send_new()


async def show_strategy_settings(update: Update, context: CallbackContext) -> int:
    logger.info(
        f"[SHOW_SETTINGS] update_id={update.update_id} "
        f"cb={update.callback_query.data if update.callback_query else None} "
        f"from={update.effective_user.id if update.effective_user else None} "
        f"chat={update.effective_chat.id if update.effective_chat else None}"
    )

    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat

    if not chat or not user:
        return State.MAIN_MENU

    chat_id = chat.id
    user_id = user.id

    # Aynı chat'te aynı anda iki çizim olmasın
    lock = _get_chat_lock(context, chat_id)

    async with lock:
        await _safe_answer(query)

        # ✅ CALLBACK DISPATCH: Menü butonları buraya düşüyorsa doğru ekrana yönlendir
        cb = (query.data if query else "") or ""

        # TerialStop menüsü
        if cb.startswith("set_terial_stop_"):
            return await show_terial_stop_settings(update, context)

        selected_exchange = context.user_data.get('selected_exchange')
        if not selected_exchange:
            try:
                await context.bot.send_message(chat_id=chat_id, text="Lütfen önce bir borsa seçin.")
            except Exception as e:
                logger.error(f"show_strategy_settings: borsa seç uyarısı gönderilemedi: {e}")
            return State.STRATEGY_SETTINGS

        try:
            # DB çağrılarını thread'e taşı (event-loop kilitlenmesin)
            user_info = await get_user_info(user_id)

            user_channel_info = await asyncio.to_thread(
                get_user_channel_info, user_id, selected_exchange
            )

            # Kanal yoksa hata menüsü
            if not user_channel_info:
                keyboard = [[InlineKeyboardButton("🔙 Ana Menüye Dön", callback_data="main_menu")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                error_text = (
                    f"🚫 <b>{escape(selected_exchange.upper())}</b> için kanal atamanız bulunmuyor.\n\n"
                    "Lütfen bir yönetici ile iletişime geçerek bu borsa için kanal ataması talep edin."
                )

                # ✅ BURASI DEĞİŞTİ: _safe_edit_or_send yerine _render_strategy_menu
                await _render_strategy_menu(
                    update=update,
                    context=context,
                    chat_id=chat_id,
                    text=error_text,
                    reply_markup=reply_markup
                )
                return State.STRATEGY_SETTINGS

            # Borsa bakiye bilgilerini çek
            borsa_bakiye_query = """
                SELECT 
                    username, 
                    spot, 
                    vadeli, 
                    guncel_toplam, 
                    kar_zarar_toplam, 
                    kar_zarar
                FROM borsa_info 
                WHERE user_id = ? AND exchange = ?
            """

            borsa_bakiye_result = await asyncio.to_thread(
                db_operation,
                borsa_bakiye_query,
                (user_id, selected_exchange),
                'select',
                True  # fetch=True
            )

            settings = await asyncio.to_thread(get_user_settings, user_id, selected_exchange)
            if not settings:
                await context.bot.send_message(chat_id=chat_id, text="Strateji ayarları yüklenemedi.")
                return State.MAIN_MENU

            # ---- Formatlama
            aktif_pasif = user_channel_info.get('aktif_pasif', 'Pasif') if isinstance(user_channel_info, dict) else 'Pasif'
            start_date_str = user_channel_info.get('start_date', '') if isinstance(user_channel_info, dict) else ''
            end_date_str = user_channel_info.get('end_date', '') if isinstance(user_channel_info, dict) else ''
            start_date = parse_date(start_date_str)
            end_date = parse_date(end_date_str)
            remaining_time = end_date - datetime.now(timezone.utc) if end_date else timedelta(0)

            spot_bakiye = vadeli_bakiye = toplam_bakiye = kar_zarar = 0.0
            kar_zarar_durum = "🔵"

            if borsa_bakiye_result:
                bakiye_info = borsa_bakiye_result[0]
                spot_bakiye = float(bakiye_info[1] or 0.0)
                vadeli_bakiye = float(bakiye_info[2] or 0.0)
                toplam_bakiye = float(bakiye_info[3] or 0.0)
                kar_zarar = float(bakiye_info[4] or 0.0)
                if bakiye_info[5] == 'KAR':
                    kar_zarar_durum = "🟢 ⬆️"
                elif bakiye_info[5] == 'ZARAR':
                    kar_zarar_durum = "🔴 ⬇️"

            username = user_info.get('username', 'N/A') if isinstance(user_info, dict) else 'N/A'
            channel_name = user_channel_info.get('channel_name', 'N/A') if isinstance(user_channel_info, dict) else 'N/A'

            message_text = (
                f"👤 <b>KULLANICI BİLGİLERİ</b>\n\n"
                f"▫️ <b>Kullanıcı Adı:</b> {escape(str(username))}\n"
                f"▫️ <b>Başlangıç:</b> {escape(format_date(start_date) if start_date else 'N/A')}\n"
                f"▫️ <b>Bitiş:</b> {escape(format_date(end_date) if end_date else 'N/A')}\n"
                f"▫️ <b>Kalan Süre:</b> {remaining_time.days} gün, {remaining_time.seconds // 3600} saat\n"
                f"▫️ <b>Kanal:</b> {escape(str(channel_name))}\n"
                f"▫️ <b>Durum:</b> {'Aktif' if aktif_pasif == 'Aktif' else 'Pasif'}\n\n"
                f"🏦 <b>BORSA: {escape(selected_exchange.upper())}</b>\n\n"
                f"▫️ <b>Spot Bakiye:</b> <code>{spot_bakiye:,.4f}</code> USDT\n"
                f"▫️ <b>Vadeli Bakiye:</b> <code>{vadeli_bakiye:,.4f}</code> USDT\n"
                f"▫️ <b>Toplam Bakiye:</b> <code>{toplam_bakiye:,.4f}</code> USDT\n"
                f"▫️ <b>Kar/Zarar:</b> <code>{abs(kar_zarar):,.4f}</code> USDT {kar_zarar_durum}\n\n"
                "🛠️ <b>Strateji Ayarları:</b>"
            )

            if remaining_time.days <= 0 and aktif_pasif == 'Pasif':
                message_text += "\n\n⚠️ <b>Aboneliğinizin süresi bitmiştir. Lütfen yetkili adminler ile iletişime geçin.</b>"

            oto_trade_status = get_oto_trade_status_from_channel_info(user_channel_info)
            keyboard_list = create_settings_keyboard(settings, selected_exchange, user_id, remaining_time.days,
                oto_trade_status)
            keyboard_list.append([InlineKeyboardButton("🏠 Ana Menüye Dön", callback_data="main_menu")])
            reply_markup = InlineKeyboardMarkup(keyboard_list)

            # ✅ BURASI DEĞİŞTİ: menüyü sabit message_id üstünden yönet
            await _render_strategy_menu(
                update=update,
                context=context,
                chat_id=chat_id,
                text=message_text,
                reply_markup=reply_markup
            )

            return State.STRATEGY_SETTINGS

        except asyncio.TimeoutError:
            logger.error("show_strategy_settings: Telegram/IO timeout", exc_info=True)
            try:
                await context.bot.send_message(chat_id=chat_id, text="İşlem zaman aşımına uğradı. Tekrar deneyin.")
            except Exception:
                pass
            return State.STRATEGY_SETTINGS

        except Exception as e:
            logger.error(f"show_strategy_settings içinde kritik hata: {e}", exc_info=True)
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="Strateji ayarları menüsü yüklenirken bir hata oluştu. Lütfen tekrar deneyin.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="main_menu")]])
                )
            except Exception:
                pass
            return State.MAIN_MENU


def get_oto_trade_status_from_channel_info(user_channel_info: dict) -> bool:
    if isinstance(user_channel_info, dict):
        return user_channel_info.get('aktif_pasif', 'Pasif') == 'Aktif'
    return False


def create_settings_keyboard(settings, selected_exchange, user_id, remaining_days, oto_trade_status: bool):
    def create_button(key: str, label: str) -> InlineKeyboardButton:
        value = settings.get(key, '0')

        # Float değerleri güvenli bir şekilde işle
        if isinstance(value, float):
            value = str(value)

        if key == 'lot':
            try:
                # Lot ayarı için özel işleme
                if value is None or value == 'Ayarlanmadı':
                    return InlineKeyboardButton(f"{label}: Ayarlanmadı", callback_data=f"edit_{key}")

                # USDT'yi çıkar ve virgülü noktaya çevir
                if ' USDT' in value:
                    value = value.replace(' USDT', '')
                value = value.replace(',', '.')

                # Lot değerini float olarak al
                lot_value = float(value)

                # Eğer lot_percentage ayarı varsa, yüzde bilgisini de göster
                lot_percentage = settings.get('lot_percentage', '0') if isinstance(settings, dict) else '0'

                # Yüzde bilgisi varsa ve '0' değilse
                if lot_percentage != '0':
                    # '%' işaretini çıkar ve boşlukları temizle
                    percentage = str(lot_percentage).replace('%', '').strip()

                    # Yüzde değerini float'a çevir
                    try:
                        percentage_float = float(percentage)
                        return InlineKeyboardButton(
                            f"{label}: %{percentage_float:.0f} Lot = {lot_value:.2f} USDT",
                            callback_data=f"edit_{key}"
                        )
                    except ValueError:
                        # Yüzde dönüşümü başarısız olursa
                        return InlineKeyboardButton(
                            f"{label}: {lot_value:.2f} USDT",
                            callback_data=f"edit_{key}"
                        )
                else:
                    # Yüzde bilgisi yoksa sadece lot değerini göster
                    return InlineKeyboardButton(
                        f"{label}: {lot_value:.2f} USDT",
                        callback_data=f"edit_{key}"
                    )

            except (ValueError, TypeError):
                return InlineKeyboardButton(f"{label}: Ayarlanmadı", callback_data=f"edit_{key}")

        elif key == 'leverage':
            try:
                # Leverage değerini güvenli bir şekilde işle
                if isinstance(value, str) and 'X' in value:
                    value = value.replace('X', '')
                leverage = float(value)
                value = f"{leverage}x"
                # Leverage için özel callback data oluştur
                return InlineKeyboardButton(f"{label}: {value}", callback_data=f"set_leverage_{selected_exchange}")
            except (ValueError, TypeError):
                return InlineKeyboardButton(f"{label}:"
                                            f"Ayarlanmadı", callback_data=f"set_leverage_{selected_exchange}")

        elif key == 'stop_loss':
            if value == 'fixed':
                value = 'Sinyal Değeri Kullan'
            elif value == 'percentage':
                sl_percentage = settings.get('sl_percentage', 0) if isinstance(settings, dict) else 0
                value = f'Yüzdesel (%{sl_percentage})'
            elif value == 'off':
                value = 'Kapalı'
            else:
                value = '0'

        elif key == 'margin':
            # Margin için daha detaylı ve açık bir buton
            margin_modes = {
                'CROSSED': 'Crossed Margin',
                'ISOLATED': 'Isolated Margin'
            }
            display_value = margin_modes.get(value, value)
            return InlineKeyboardButton(
                f"{display_value}",
                callback_data=f"set_margin_{selected_exchange}"
            )

        elif key.startswith('tp'):
            value = f"{value}" if value != 'Ayarlanmadı' else value
        elif key == 'take_profit':
            if isinstance(value, int):
                value = f"TP{value} Gelince %100 Çık"
            elif isinstance(value, float):
                value = f"Özel Çıkış: %{value}"
            else:
                value = str(value)
        elif key == 'terial_stop':
            parsed = parse_terial_stop(value)
            if parsed["mode"] == "OFF":
                value = "Kapalı"
            elif parsed["mode"] == "TP_CHAIN":
                value = f"TP Bazlı (TP_{parsed.get('param', 1)})"
            elif parsed["mode"] == "PCT":
                value = f"Trailing %{parsed.get('param', 0)}"
            elif parsed["mode"] == "ATR":
                value = f"ATR x{parsed.get('param', 0)}"
            else:
                value = f"Açık ({value})"
        elif key == 'sl_tp_emir':
            value = 'KULLAN' if value == 'on' else 'KULLANMA'
        elif key == 'maliyet_cek':
            if value == '0':
                value = 'off'
            elif value.isdigit() and 1 <= int(value) <= 10:
                value = f'TP{value}'
            else:
                value = '0'
        elif key == 'maks_emir':
            if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
                value = f"{value} Adet"
            else:

                value = '0'

        return InlineKeyboardButton(
            f"{label}: {value}",
            callback_data=f"set_{key}_{selected_exchange}"

        )
    keyboard = [
        [create_button('lot', 'Lot'), create_button('margin', 'Margin')],
        [create_button('leverage', 'Kaldıraç')],
        [InlineKeyboardButton("📧 E-posta Ayarları", callback_data=f"email_settings_{selected_exchange}")],
        [InlineKeyboardButton("Çıkış Yüzdeleri", callback_data="none")],
        [create_button('tp1', 'TP1'), create_button('tp2', 'TP2'),
         create_button('tp3', 'TP3'), create_button('tp4', 'TP4')],
        [create_button('tp5', 'TP5'), create_button('tp6', 'TP6'),
         create_button('tp7', 'TP7'), create_button('tp8', 'TP8')],
        [create_button('tp9', 'TP9'), create_button('tp10', 'TP10')],
        [create_button('stop_loss', 'Stop Loss'),
         create_button('take_profit', 'TakeProfit')],
        [create_button('terial_stop', 'TerialStop')],
        [create_button('maks_emir', 'Maks Emir'),
         create_button('sl_tp_emir', 'SL-TP Emirleri'),
         create_button('maliyet_cek', 'Maliyetine Çek')],
    ]

    if remaining_days <= 0 and not oto_trade_status:
        oto_trade_button = InlineKeyboardButton(
            "Oto Trade: Pasif (Admin İletişim)",
            callback_data=f"admin_contact_{selected_exchange}"
        )
    else:
        oto_trade_button = InlineKeyboardButton(
            f"Oto Trade: {'Aktif' if oto_trade_status else 'Pasif'}",
            callback_data=f"toggle_oto_trade_{selected_exchange}"
        )
    keyboard.append([oto_trade_button])
    logger.info(f"Settings keyboard created for user {user_id} and exchange {selected_exchange}")
    return keyboard


def create_admin_contact_keyboard() -> List[List[InlineKeyboardButton]]:
    admin_contacts = get_all_admins()
    return [
        [InlineKeyboardButton(f"@{admin['username']}", url=f"https://t.me/{admin['username']}")]
        for admin in admin_contacts
    ]


async def admin_contact(update: Update, context: CallbackContext) -> int:

    _ = context
    query = update.callback_query
    await query.answer("Abonelik süreniz dolmuştur. Lütfen admin ile iletişime geçin.")
    admin_keyboard = create_admin_contact_keyboard()
    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(admin_keyboard))
    return State.STRATEGY_SETTINGS


async def back_to_strategy_settings(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    return await show_strategy_settings(update, context)


async def strategy_settings(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    return await show_strategy_settings(update, context)


async def set_email_settings(update: Update, context: CallbackContext) -> int:
    _ = context
    query = update.callback_query
    await query.answer()

    selected_exchange = query.data.split('_')[-1]

    keyboard = [
        [InlineKeyboardButton("Sistem Mailini Kullan",
                              callback_data=f"use_system_email_{selected_exchange}")],
        [InlineKeyboardButton("Özel Mail Adresi Kullan",
                              callback_data=f"use_custom_email_{selected_exchange}")],
        [InlineKeyboardButton("Geri", callback_data="back_to_strategy_settings")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "📧 E-posta Ayarları\n\n"
        "1️⃣ Sistem Maili:\n"
        "- Mail: olimpos.bot.sinyal@gmail.com\n"
        "- Otomatik kurulum\n\n"
        "2️⃣ Özel Mail:\n"
        "- Kendi Gmail hesabınızı kullanın\n"
        "- App Password gerekli",
        reply_markup=reply_markup
    )

    return State.EMAIL_SETTINGS


async def handle_system_email(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    if not query:
        logger.error("Callback query is missing")
        return State.EMAIL_SETTINGS

    await query.answer()

    user_id = update.effective_user.id
    selected_exchange = query.data.split('_')[-1]

    system_email = {
        'EMAIL_USERNAME': 'olimpos.bot.sinyal@gmail.com',
        'EMAIL_PASSWORD': 'yoox ncpv ncqb iojp',
        'IMAP_SERVER': 'imap.gmail.com'
    }

    try:
        # Veritabanında ayarlar tablosunu güncelle
        query_str = """
        UPDATE ayarlar 
        SET EMAIL_USERNAME = ?, 
            EMAIL_PASSWORD = ?, 
            IMAP_SERVER = ?
        WHERE user_id = ? AND exchange = ?
        """
        db_operation(
            query_str,
            (system_email['EMAIL_USERNAME'],
             system_email['EMAIL_PASSWORD'],
             system_email['IMAP_SERVER'],
             user_id,
             selected_exchange),
            operation='update'
        )

        # Başarılı mesajı ve strateji ayarlarına dönüş butonu
        keyboard = [[InlineKeyboardButton("Strateji Ayarlarına Dön", callback_data="strategy_settings")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            "✅ Sistem mail hesabı başarıyla ayarlandı!",
            reply_markup=reply_markup
        )

        # Strateji ayarlarına dön
        context.user_data['selected_exchange'] = selected_exchange
        return State.STRATEGY_SETTINGS

    except Exception as e:
        logger.error(f"Error setting system email: {e}")
        try:
            keyboard = [[InlineKeyboardButton("Tekrar Dene", callback_data=f"email_settings_{selected_exchange}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(
                "❌ Bir hata oluştu. Lütfen tekrar deneyin.",
                reply_markup=reply_markup
            )
        except Exception as inner_e:
            logger.error(f"Error sending error message: {inner_e}")

        return State.EMAIL_SETTINGS


async def handle_custom_email(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    selected_exchange = query.data.split('_')[-1]
    context.user_data['selected_exchange'] = selected_exchange

    await query.edit_message_text(
        "📧 Lütfen Gmail adresinizi girin:\n\n"
        "Not: Gmail hesabınızda 2FA aktif olmalı ve App Password oluşturmalısınız."
    )
    return State.WAITING_CUSTOM_EMAIL


async def handle_custom_email_input(update: Update, context: CallbackContext) -> int:
    email = update.message.text

    # Email formatını kontrol et
    if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
        await update.message.reply_text(
            "❌ Geçersiz email formatı!\n"
            "Lütfen geçerli bir Gmail adresi girin."
        )
        return State.WAITING_CUSTOM_EMAIL

    # Email'i context'e kaydet
    context.user_data['temp_email'] = email

    keyboard = [
        [InlineKeyboardButton("App Password Oluştur",
                              url="https://myaccount.google.com/apppasswords")],
        [InlineKeyboardButton("Geri", callback_data="back_to_email_settings")]
    ]

    await update.message.reply_text(
        "✅ Email adresi kaydedildi.\n\n"
        "🔑 Şimdi App Password'ünüzü girin:\n"
        "(Gmail'den aldığınız 16 haneli şifre)",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    return State.WAITING_CUSTOM_EMAIL_PASSWORD


async def handle_custom_email_password(update: Update, context: CallbackContext) -> int:
    password = update.message.text
    email = context.user_data.get('temp_email')
    user_id = update.effective_user.id
    selected_exchange = context.user_data.get('selected_exchange')

    # Şifre formatını kontrol et
    if not re.match(r'^[a-zA-Z0-9]{4}\s[a-zA-Z0-9]{4}\s[a-zA-Z0-9]{4}\s[a-zA-Z0-9]{4}$', password):
        await update.message.reply_text(
            "❌ Geçersiz App Password formatı!\n"
            "Şifre 16 karakter olmalı ve 4'er gruplar halinde boşlukla ayrılmalı.\n"
            "Örnek: abcd efgh ijkl mnop"
        )
        return State.WAITING_CUSTOM_EMAIL_PASSWORD

    try:
        # Veritabanında ayarlar tablosunu güncelle
        query = """
        UPDATE ayarlar 
        SET EMAIL_USERNAME = ?, 
            EMAIL_PASSWORD = ?, 
            IMAP_SERVER = ?
        WHERE user_id = ? AND exchange = ?
        """
        db_operation(
            query,
            (email, password, 'imap.gmail.com', user_id, selected_exchange),
            operation='update'
        )

        keyboard = [[InlineKeyboardButton("Strateji Ayarlarına Dön",
                                          callback_data="strategy_settings")]]

        await update.message.reply_text(
            "✅ Email ayarları başarıyla kaydedildi!\n\n"
            f"📧 {email}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        # Temp email'i temizle
        if 'temp_email' in context.user_data:
            del context.user_data['temp_email']

        return State.STRATEGY_SETTINGS

    except Exception as e:
        logger.error(f"Email ayarları kaydedilirken hata: {e}")
        await update.message.reply_text("❌ Bir hata oluştu. Lütfen tekrar deneyin.")
        return State.WAITING_CUSTOM_EMAIL_PASSWORD


async def back_to_email_settings(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    return await set_email_settings(update, context)


async def set_lot(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await _safe_answer(query)

    chat = update.effective_chat
    if not chat:
        return State.MAIN_MENU

    chat_id = chat.id
    selected_exchange = context.user_data.get('selected_exchange')
    user_id = update.effective_user.id if update.effective_user else None

    logger.info(f"[LOT] set_lot açıldı user_id={user_id} exchange={selected_exchange}")

    if not selected_exchange or not user_id:
        await context.bot.send_message(chat_id=chat_id, text="Borsa seçimi bulunamadı.")
        return State.STRATEGY_SETTINGS

    try:
        sql_query = """
            SELECT lot_percentage, lot
            FROM ayarlar
            WHERE user_id = ? AND exchange = ?
        """
        params = (user_id, selected_exchange)

        user_settings = await asyncio.to_thread(db_operation, sql_query, params, 'select', True)

        if user_settings and len(user_settings) > 0:
            row = user_settings[0]
            lot_percentage = row[0] if row[0] is not None else 'Ayarlanmadı'
            lot = row[1] if row[1] is not None else 'Ayarlanmadı'
        else:
            lot_percentage = 'Ayarlanmadı'
            lot = 'Ayarlanmadı'

        # Format
        try:
            lot_percentage_fmt = f"%{float(str(lot_percentage).replace('%', '').replace(',', '.')):.2f}" \
                if lot_percentage != 'Ayarlanmadı' else 'Ayarlanmadı'
        except Exception:
            lot_percentage_fmt = 'Ayarlanmadı'

        try:
            lot_fmt = f"{float(str(lot).replace(',', '.')):.2f} USDT" if lot != 'Ayarlanmadı' else 'Ayarlanmadı'
        except Exception:
            lot_fmt = 'Ayarlanmadı'

        total_account_value = 'Hesaplanamadı'

    except Exception as e:
        logger.error(f"[LOT] set_lot DB hata: {e}", exc_info=True)
        lot_percentage_fmt = 'Ayarlanmadı'
        lot_fmt = 'Ayarlanmadı'
        total_account_value = 'Hesaplanamadı'

    keyboard = [
        [
            InlineKeyboardButton(
                f"Sayı Girişi {f'({lot_fmt})' if lot_fmt != 'Ayarlanmadı' else ''}",
                callback_data=f"lot_number_{selected_exchange}"
            ),
            InlineKeyboardButton(
                f"Yüzde Girişi {f'({lot_percentage_fmt})' if lot_percentage_fmt != 'Ayarlanmadı' else ''}",
                callback_data=f"lot_percentage_{selected_exchange}"
            )
        ],
        [InlineKeyboardButton("Geri", callback_data="back_to_strategy_settings")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    explanation = (
        f"🔍 {escape(selected_exchange)} borsası için lot belirleme yöntemini seçin:\n\n"
        "• Sayı Girişi: Direkt USDT miktarı girebilirsiniz\n"
        "• Yüzde Girişi: Toplam hesap değerinizin yüzdesini kullanabilirsiniz\n\n"
        f"💰 Toplam Hesap Değeri: {total_account_value}\n\n"
        "Mevcut Ayarlar:\n"
        f"• Lot Miktarı: {escape(lot_fmt)}\n"
        f"• Lot Yüzdesi: {escape(lot_percentage_fmt)}\n"
    )

    # ✅ Kritik: edit_message_text yerine SABİT menü mesajına çiz
    await _render_strategy_menu(
        update=update,
        context=context,
        chat_id=chat_id,
        text=explanation,
        reply_markup=reply_markup
    )

    return State.LOT_SELECTION_METHOD


def _process_account_value(value: Optional[Union[str, float, int, tuple, list]]) -> float:
    """Hesap değerini işleme yardımcı fonksiyonu"""
    if value is not None:
        if isinstance(value, (tuple, list)):
            value = value[0] if value else 0

        try:
            return float(value or 0)
        except (ValueError, TypeError):
            return 0
    return 0


async def handle_lot_input(update: Update, context: CallbackContext) -> int:
    user_input = update.message.text.replace(',', '.')
    user_id = update.effective_user.id
    selected_exchange = context.user_data.get('selected_exchange')

    try:
        lot_amount = float(user_input)

        # Validate lot as a positive number
        if not isinstance(lot_amount, (int, float)) or lot_amount <= 0:
            raise ValueError("Lot amount must be a positive number.")

        # Round to 2 decimal places
        lot_amount = round(lot_amount, 2)

        # Sadece sayıyı kaydet, USDT ekleme
        update_user_settings(user_id, selected_exchange, {
            'lot': lot_amount,  # Sadece sayı
            'lot_percentage': '0'  # Lot sayısı girildiğinde yüzdeyi sıfırla
        })

        keyboard = [
            [InlineKeyboardButton("Strateji Ayarlarına Dön", callback_data="strategy_settings")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"Lot miktarı {lot_amount:.2f} olarak ayarlandı.",
            reply_markup=reply_markup
        )
        return State.STRATEGY_SETTINGS
    except ValueError as e:
        await update.message.reply_text(str(e))
        return State.LOT_NUMBER_INPUT


async def confirm_lot_amount(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    try:
        # Callback datadan lot miktarını çıkar
        lot_amount = float(query.data.split('_')[-1])
        user_id = query.from_user.id
        selected_exchange = context.user_data.get('selected_exchange')

        # Log the exchange along with other details if needed
        logger.info(f"Kullanıcı ID: {user_id}, Seçilen Borsa: "
                    f"{selected_exchange}, Onaylanan Lot Miktarı: {lot_amount:.2f} USDT")

        keyboard = [
            [InlineKeyboardButton("Strateji Ayarlarına Dön", callback_data="strategy_settings")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            f"✅ Lot miktarı {lot_amount:.2f} USDT olarak onaylandı ve kaydedildi.",
            reply_markup=reply_markup
        )

        return State.STRATEGY_SETTINGS

    except Exception as e:
        logger.error(f"Lot onayında hata: {e}")
        await query.edit_message_text("Bir hata oluştu. Lütfen tekrar deneyin.")
        return State.LOT_NUMBER_INPUT


async def handle_lot_selection_method(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await _safe_answer(query)

    chat = update.effective_chat
    if not chat:
        return State.MAIN_MENU
    chat_id = chat.id

    callback_data = query.data if query else ""
    selected_exchange = context.user_data.get('selected_exchange')

    logger.info(f"[LOT] handle_lot_selection_method tetiklendi data={callback_data} exchange={selected_exchange}")

    if not selected_exchange:
        await context.bot.send_message(chat_id=chat_id, text="Önce bir borsa seçmeniz gerekiyor.")
        return State.STRATEGY_SETTINGS

    if callback_data.startswith('lot_number_'):
        text = "Lot için USDT miktarını girin:"
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("Geri", callback_data="back_to_lot_selection")]
        ])

        # ✅ Kritik: edit_message_text yerine SABİT menü mesajına çiz
        await _render_strategy_menu(
            update=update,
            context=context,
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup
        )
        return State.LOT_NUMBER_INPUT

    if callback_data.startswith('lot_percentage_'):
        return await handle_lot_percentage_selection(update, context)

    logger.warning(f"[LOT] Beklenmedik callback_data: {callback_data}")
    await context.bot.send_message(chat_id=chat_id, text="Geçersiz seçim yapıldı.")
    return State.STRATEGY_SETTINGS


async def handle_lot_percentage_selection(update: Update, context: CallbackContext) -> int:
    """
    Lot yüzdesi seçim ekranını yönetir

    Args:
        update (Update): Telegram update nesnesi
        context (ContextTypes.DEFAULT_TYPE): Bot context'i

    Returns:
        int: Sonraki conversation state'i
    """
    query = update.callback_query
    await query.answer()

    selected_exchange = context.user_data.get('selected_exchange')
    user_id = update.effective_user.id

    # Standart yüzde seçenekleri
    percentage_options = [10, 20, 30, 40, 50]

    keyboard = [
        [InlineKeyboardButton(f"%{percentage}", callback_data=f"lot_percentage_{percentage}_{selected_exchange}")
         for percentage in percentage_options],
        [InlineKeyboardButton("Özel Yüzde Gir", callback_data=f"lot_percentage_custom_{selected_exchange}")],
        [InlineKeyboardButton("Strateji Ayarlarına Dön", callback_data="strategy_settings")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Kullanıcı hesap bilgilerini al
    user_settings = get_user_settings(user_id, selected_exchange)
    vadeli_hesap_degeri = _get_vadeli_hesap_degeri(user_id, selected_exchange) or 0.0

    explanation = (
        f"🔍 {selected_exchange} için Lot Yüzde Seçimi\n\n"
        f"💰 Toplam Hesap Değeri: {vadeli_hesap_degeri:.2f} USDT\n\n"
        "Lot Yüzdesi Seçenekleri:\n"
        "• Risk yönetimi için %1-%50 arası önerilir\n"
        "• Hesap büyüklüğüne göre ayarlayın\n\n"
        "Mevcut Ayar: "
        f"{user_settings.get('lot_percentage', 'Ayarlanmamış')}"
    )

    try:
        await query.edit_message_text(
            text=explanation,
            reply_markup=reply_markup
        )
    except BadRequest:
        await context.bot.send_message(
            chat_id=query.message.chat.id,
            text=explanation,
            reply_markup=reply_markup
        )

    return State.LOT_PERCENTAGE_SELECTION


async def handle_lot_percentage_input(update: Update, context: CallbackContext) -> int:
    """
    Kullanıcının lot yüzdesi girişini işler

    Args:
        update (Update): Telegram update nesnesi
        context (ContextTypes.DEFAULT_TYPE): Bot context'i

    Returns:
        int: Sonraki conversation state'i
    """
    query = update.callback_query
    await query.answer()

    # Callback data'dan yüzde ve exchange bilgisini çıkar
    extraction_result = extract_percentage(query.data)

    if extraction_result is None:
        await query.edit_message_text(
            "❌ Yüzde seçiminde hata oluştu. Lütfen tekrar deneyin.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Geri Dön", callback_data="back_to_lot_selection")]
            ])
        )
        return State.LOT_PERCENTAGE_SELECTION

    percentage, selected_exchange = extraction_result
    user_id = query.from_user.id

    # Onay klavyesi
    keyboard = [
        [
            InlineKeyboardButton("✅ Onayla", callback_data=f"confirm_lot_percentage_{percentage}_{selected_exchange}"),
            InlineKeyboardButton("❌ İptal", callback_data="back_to_lot_selection")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Hesap bilgilerini al
    vadeli_hesap_degeri = _get_vadeli_hesap_degeri(user_id, selected_exchange) or 0.0
    lot_value = vadeli_hesap_degeri * (percentage / 100)

    explanation = (
        f"🔍 Lot Yüzdesi Seçimi Onayı\n\n"
        f"📊 Seçilen Yüzde: %{percentage}\n"
        f"💰 Toplam Hesap Değeri: {vadeli_hesap_degeri:.2f} USDT\n"
        f"💸 İşlem Lot Değeri: {lot_value:.2f} USDT\n\n"
        "Seçiminizi onaylayın veya geri dönün."
    )

    try:
        await query.edit_message_text(
            text=explanation,
            reply_markup=reply_markup
        )
    except BadRequest:
        await context.bot.send_message(
            chat_id=query.message.chat.id,
            text=explanation,
            reply_markup=reply_markup
        )

    return State.LOT_PERCENTAGE_CONFIRMATION


async def save_lot_percentage(user_id: int, exchange: str, lot_percentage: float) -> bool:
    try:
        # Vadeli hesap değerini al
        vadeli_hesap_query = """
        SELECT 
            CASE 
                WHEN vadeli IS NULL THEN '0'
                WHEN vadeli = '' THEN '0'
                WHEN vadeli LIKE '%%,%%' THEN REPLACE(vadeli, ',', '.')
                ELSE vadeli 
            END as vadeli_deger
        FROM borsa_info 
        WHERE user_id = ? AND LOWER(exchange) = LOWER(?)
        """

        vadeli_hesap_result = db_operation(
            vadeli_hesap_query,
            (user_id, exchange),
            operation='select',
            fetch=True
        )

        # Vadeli hesap değerini al
        vadeli_hesap_degeri = float(vadeli_hesap_result[0][0]) if vadeli_hesap_result else 0.0

        # Lot miktarını hesapla
        lot_amount = round(vadeli_hesap_degeri * (lot_percentage / 100), 2)

        # Lot miktarını USDT formatında kaydet
        update_query = """
        UPDATE ayarlar 
        SET 
            lot = ?, 
            lot_percentage = ?
        WHERE user_id = ? AND exchange = ?
        """

        result = db_operation(
            update_query,
            (f"{lot_amount}",
                f"%{lot_percentage}",
                user_id,
                exchange
             ),
            operation='update'
        )

        # Log kaydet
        logger.info(
            f"Lot yüzdesi kaydedildi - "
            f"User ID: {user_id}, "
            f"Exchange: {exchange}, "
            f"Yüzde: %{lot_percentage}, "
            f"Lot Miktarı: {lot_amount} USDT, "
            f"Toplam Hesap Değeri: {vadeli_hesap_degeri} USDT"
        )

        # Sonucu kontrol et
        if result is not None:
            logger.info(f"Lot yüzdesi güncellendi: User ID {user_id}, Exchange {exchange}, Yüzde {lot_percentage}")
            return True
        else:
            logger.warning(f"Lot yüzdesi güncellenemedi: User ID {user_id}, Exchange {exchange}")
            return False

    except Exception as e:
        logger.error(f"Lot yüzdesi kaydetme hatası: {e}")
        return False


async def handle_percentage_error(query: CallbackQuery, context: CallbackContext) -> None:
    """Yüzde çıkarma hatası durumunda özel işlem"""
    # Örnek kullanım: Kullanıcı verilerini kaydetme
    context.user_data['lot_selection_error'] = True

    await query.edit_message_text(
        "❌ Lot yüzdesi seçiminde bir hata oluştu. "
        "Lütfen tekrar seçim yapın.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Lot Seçimine Geri Dön", callback_data="back_to_lot_selection")]
        ])
    )


async def safe_message_send(query: CallbackQuery, context: CallbackContext, user_id: int, text: str,
                            reply_markup: InlineKeyboardMarkup) -> bool:
    """Güvenli mesaj gönderme mekanizması"""
    try:
        # Önce query üzerinden güncellemeyi dene
        await query.edit_message_text(
            text,
            reply_markup=reply_markup
        )
        return True
    except Exception as query_error:
        logger.warning(f"Query mesaj güncelleme hatası: {query_error}")

        try:
            # Alternatif olarak bot üzerinden mesaj gönder
            await context.bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=reply_markup
            )
            return True
        except Exception as bot_error:
            logger.critical(f"Alternatif mesaj gönderme hatası: {bot_error}")
            return False


def calculate_lot_amount(hesap_degeri: float, percentage: float, min_lot: float = 10.0) -> float:
    """Güvenli lot hesaplama"""
    try:
        lot = max(hesap_degeri * (percentage / 100), 0)
        return max(round(lot, 2), min_lot)
    except Exception as e:
        logger.error(f"Lot hesaplama hatası: {e}")
        return 0


def extract_percentage(callback_data: str) -> Optional[Tuple[int, str]]:
    try:
        parts = callback_data.split('_')

        # Özel yüzde kontrolü
        if 'custom' in parts:
            exchange_candidates = [p for p in parts if p in ['binance', 'bybit', 'okx', 'bitmart', 'bitget', 'mexc']]
            return None if not exchange_candidates else (None, exchange_candidates[0])

        # Mevcut yüzde çıkarma mantığı
        percentage_candidates = [int(p) for p in parts if p.isdigit() and 1 <= int(p) <= 100]
        exchange_candidates = [p for p in parts if p in ['binance', 'bybit', 'okx', 'bitmart', 'bitget', 'mexc']]

        if not percentage_candidates or not exchange_candidates:
            logger.warning(f"Geçersiz callback data: {callback_data}")
            return None

        percentage = percentage_candidates[0]
        exchange = exchange_candidates[0]

        return percentage, exchange

    except Exception as e:
        logger.error(f"Yüzde çıkarma hatası: {e}")
        return None


async def handle_custom_lot_percentage(update: Update, context: CallbackContext) -> int:
    # Remove the redundant second assignment or use the variable meaningfully
    selected_exchange = context.user_data.get('selected_exchange')

    # Log the selected exchange information
    logger.info(f"Seçili Borsa: {selected_exchange}")

    if update.callback_query:
        query = update.callback_query
        try:
            await query.answer()
        except Exception as e:
            logger.warning(f"Query answer error: {e}")

    # Eğer callback query varsa mesajı güncelle, yoksa yeni mesaj gönder
    try:
        if update.callback_query:
            await update.callback_query.edit_message_text(
                "Özel yüzde değerini girin (1-100 arası, örn: 3.5):",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Geri", callback_data="back_to_lot_percentage_selection")],
                ])
            )
        else:
            await update.effective_chat.send_message(
                "Özel yüzde değerini girin (1-100 arası, örn: 3.5):",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Geri", callback_data="back_to_lot_percentage_selection")],
                ])
            )
    except Exception as e:
        logger.error(f"Error in custom lot percentage: {e}")
        try:
            # Fallback mekanizması
            await update.effective_chat.send_message(
                "Özel yüzde değerini girin (1-100 arası, örn: 3.5):",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Geri", callback_data="back_to_lot_percentage_selection")],
                ])
            )
        except Exception as fallback_error:
            logger.critical(f"Complete fallback error: {fallback_error}")

    return State.LOT_CUSTOM_PERCENTAGE_INPUT


async def handle_custom_lot_percentage_input(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    selected_exchange = context.user_data.get('selected_exchange')

    # Mesaj kontrolü
    if not update.message or not update.message.text:
        await update.effective_chat.send_message(
            "Geçersiz giriş. Lütfen bir sayı girin.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "Geri",
                    callback_data="back_to_lot_percentage_selection"
                )]
            ])
        )
        return State.LOT_CUSTOM_PERCENTAGE_INPUT

    try:
        # Convert input to float, replacing comma with dot
        percentage = float(update.message.text.replace(',', '.'))

        # Validate percentage
        if percentage <= 0 or percentage > 100:
            await update.message.reply_text(
                "❌ Hata: Yüzde 1 ile 100 arasında olmalıdır.\n"
                "Lütfen 1-100 arasında bir sayı girin.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "Geri",
                        callback_data="back_to_lot_percentage_selection"
                    )]
                ])
            )
            return State.LOT_CUSTOM_PERCENTAGE_INPUT

        # Get account value
        vadeli_hesap_degeri = _get_vadeli_hesap_degeri(user_id, selected_exchange) or 0.0

        # Calculate lot amount
        lot_amount = round((vadeli_hesap_degeri * percentage) / 100, 2)
        # BURASI DEĞİŞTİ - Lot percentage'ı da veritabanına kaydet
        update_query = """
              UPDATE ayarlar 
              SET lot = ?, lot_percentage = ?
              WHERE user_id = ? AND exchange = ?
              """
        db_operation(
            update_query,
            (
                f"{lot_amount:.2f}",  # Lot miktarı
                f"{percentage}",  # Yüzde işareti olmadan kaydet
                user_id,
                selected_exchange
            ),
            operation='update'
        )

        keyboard = [
            [InlineKeyboardButton(
                f"%{percentage} Lot = {lot_amount:.2f} USDT ONAYLAYIN",  # İstediğiniz format
                callback_data=f"confirm_custom_percentage_{percentage}"
            )],
            [InlineKeyboardButton(
                "Geri",
                callback_data="back_to_lot_percentage_selection"
            )]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"📊 Yüzde Bazlı Lot Analizi:\n"
            f"Toplam Hesap Değeri: {vadeli_hesap_degeri:.2f} USDT\n"
            f"Seçilen Yüzde: %{percentage}\n"
            f"Hesaplanan Lot Miktarı: {lot_amount:.2f} USDT\n"
            "Devam etmek istiyor musunuz?",
            reply_markup=reply_markup
        )

        return State.LOT_PERCENTAGE_CONFIRMATION

    except ValueError:
        await update.message.reply_text(
            "❌ Hata: Geçersiz giriş.\n"
            "Lütfen 1-100 arasında bir ondalık sayı girin (örn: 3.5).",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "Geri",
                    callback_data="back_to_lot_percentage_selection"
                )]
            ])
        )
        return State.LOT_CUSTOM_PERCENTAGE_INPUT


async def confirm_custom_lot_percentage(update: Update, context: CallbackContext) -> int:
    query = update.callback_query

    if not query:
        logger.error("Callback query is None")
        return State.LOT_PERCENTAGE_SELECTION

    try:
        await query.answer()
    except Exception as e:
        logger.warning(f"Query answer error: {e}")

    try:
        # Callback datadan yüzde değerini çıkar
        percentage = float(query.data.split('_')[-1])
        user_id = query.from_user.id
        selected_exchange = context.user_data.get('selected_exchange', 'binance')

        # Vadeli hesap değerini al
        vadeli_hesap_degeri = _get_vadeli_hesap_degeri(user_id, selected_exchange) or 0.0

        # Lot miktarını hesapla
        lot_amount = round(vadeli_hesap_degeri * (percentage / 100), 2)

        # Veritabanı güncelleme - sadece lot miktarını kaydet
        update_user_settings(
            user_id,
            selected_exchange,
            {'lot': lot_amount}  # Sadece sayı kaydedilecek
        )

        keyboard = [
            [InlineKeyboardButton("Strateji Ayarlarına Dön", callback_data="strategy_settings")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Mesajı güncelle
        await query.edit_message_text(
            f"✅ Lot miktarı hesap değerinin %{percentage}'i olarak onaylandı.\n"
            f"Hesaplanan Lot: {lot_amount:.2f} USDT\n"
            f"Toplam Hesap Değeri: {vadeli_hesap_degeri:.2f} USDT",
            reply_markup=reply_markup
        )

        return State.STRATEGY_SETTINGS

    except Exception as e:
        logger.error(f"Özel lot yüzde onayında hata: {e}")
        try:
            # Mesajı güncelle veya yeni mesaj gönder
            if query.message:
                await query.edit_message_text(
                    "Bir hata oluştu. Lütfen tekrar deneyin.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("Geri", callback_data="back_to_lot_percentage_selection")]
                    ])
                )
            else:
                # Eğer mesaj yoksa, botun kendisine mesaj gönder
                await context.bot.send_message(
                    chat_id=query.from_user.id,
                    text="Bir hata oluştu. Lütfen tekrar deneyin.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("Geri", callback_data="back_to_lot_percentage_selection")]
                    ])
                )
        except Exception as fallback_error:
            logger.critical(f"Hata mesajı gönderme başarısız: {fallback_error}")

        return State.LOT_PERCENTAGE_SELECTION


async def handle_lot_number_input(update: Update, context: CallbackContext) -> int:
    message = update.message
    user_id = update.effective_user.id
    selected_exchange = context.user_data.get('selected_exchange')

    try:
        lot_amount = float(message.text.replace(',', '.'))

        vadeli_hesap_degeri = _get_vadeli_hesap_degeri(user_id, selected_exchange) or 0.0

        if lot_amount > vadeli_hesap_degeri:
            keyboard = [[InlineKeyboardButton("Lot Ayarlarına Geri Dön", callback_data="back_to_lot_selection")]]
            await message.reply_text(
                f"❌ Yetersiz Bakiye!\n"
                f"Toplam Hesap Değeri: {vadeli_hesap_degeri:.2f} USDT\n"
                f"Girilen Lot Miktarı: {lot_amount:.2f} USDT\n"
                "İşleme giriş yapılamaz.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return State.LOT_NUMBER_INPUT

        # DB güncelle
        update_query = """
        UPDATE ayarlar 
        SET lot = ?, lot_percentage = '0'
        WHERE user_id = ? AND exchange = ?
        """
        db_operation(
            update_query,
            (f"{lot_amount:.2f}", user_id, selected_exchange),
            operation='update'
        )

        # ✅ Kullanıcı girdisini temizle
        await _safe_delete_message(context, message.chat.id, message.message_id)

        # ✅ Direkt strateji ayarları menüsünü yeniden çiz
        await show_strategy_settings(update, context)
        return State.STRATEGY_SETTINGS


    except ValueError:
        await message.reply_text(
            "Geçersiz lot miktarı. Lütfen sayısal bir değer girin.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Geri", callback_data="back_to_lot_selection")]
            ])
        )
        return State.LOT_NUMBER_INPUT


def _get_maks_emir_sayisi(user_id: int, selected_exchange: str) -> int:
    try:
        result = db_operation(
            "SELECT maks_emir FROM ayarlar WHERE user_id = ? AND exchange = ?",
            (user_id, selected_exchange),
            operation='select',
            fetch_all=False
        )
        return int(result[0]) if result else 5
    except Exception as e:
        logger.error(f"Maks emir sayısı alınırken hata: {e}")
        return 5


async def confirm_lot_percentage(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    # Özel yüzde kontrolü
    if 'custom_percentage' in query.data:
        return await confirm_custom_lot_percentage(update, context)

    extraction_result = extract_percentage(query.data)

    if extraction_result is None:
        await query.edit_message_text(
            "❌ Onay işleminde hata oluştu. Lütfen tekrar deneyin.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Lot Seçimine Geri Dön", callback_data="back_to_lot_selection")]
            ])
        )
        return State.LOT_PERCENTAGE_SELECTION

    percentage, selected_exchange = extraction_result
    user_id = query.from_user.id

    # Vadeli hesap değerini al
    vadeli_hesap_degeri = _get_vadeli_hesap_degeri(user_id, selected_exchange) or 0.0

    # Lot miktarını hesapla
    lot_amount = round(vadeli_hesap_degeri * (percentage / 100), 2)

    # Kullanıcı ayarlarını güncelle
    update_result = update_user_settings(
        user_id,
        selected_exchange,
        {
            'lot_percentage': percentage,  # Sadece sayı
            'lot': lot_amount  # Sadece sayı
        }
    )

    if update_result:
        explanation = (
            f"✅ Başarılı!\n"
            f"📊 {selected_exchange} için lot ayarları güncellendi:\n"
            f"• Lot Yüzdesi: %{percentage}\n"
            f"• Hesaplanan Lot Miktarı: {lot_amount:.2f} USDT\n"
            f"• Toplam Hesap Değeri: {vadeli_hesap_degeri:.2f} USDT\n"
            "Strateji ayarlarına devam etmek için aşağıdaki butonu kullanın."
        )

        keyboard = [
            [InlineKeyboardButton("Strateji Ayarları", callback_data="strategy_settings")],
            [InlineKeyboardButton("Lot Seçimine Geri Dön", callback_data="back_to_lot_selection")],

        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await query.edit_message_text(
                text=explanation,
                reply_markup=reply_markup
            )
        except BadRequest:
            await context.bot.send_message(
                chat_id=query.message.chat.id,
                text=explanation,
                reply_markup=reply_markup
            )

        return State.STRATEGY_SETTINGS
    else:
        await query.edit_message_text(
            "❌ Ayar güncellemesinde hata oluştu. Lütfen tekrar deneyin.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Geri Dön", callback_data="back_to_lot_selection")]
            ])
        )
        return State.LOT_PERCENTAGE_SELECTION


def _get_vadeli_hesap_degeri(user_id: int, selected_exchange: str) -> float:
    try:
        logger.info(f"Vadeli hesap değeri sorgusu - User ID: {user_id}, Exchange: {selected_exchange}")

        query = """
        SELECT 
            CASE 
                WHEN vadeli IS NULL THEN '0'
                WHEN vadeli::text = '' THEN '0'
                WHEN vadeli::text LIKE '%%,%%' THEN REPLACE(vadeli::text, ',', '.')
                ELSE vadeli::text
            END AS vadeli_deger
        FROM borsa_info 
        WHERE user_id = ? AND LOWER(exchange) = LOWER(?)
        LIMIT 1
        """

        result = db_operation(
            query,
            (user_id, selected_exchange),
            operation='select',
            fetch_all=False
        )

        logger.info(f"Dönen ham değer: {result}")

        if not result:
            logger.warning("Vadeli hesap değeri bulunamadı.")
            return 0.0

        # fetchone -> (value,)
        value = result[0]
        value_str = str(value).replace(',', '.').strip()

        try:
            return float(value_str) if value_str else 0.0
        except (ValueError, TypeError) as e:
            logger.warning(f"Vadeli hesap değeri float'a çevrilemedi: {value_str}, Hata: {e}")
            return 0.0

    except Exception as e:
        logger.error(f"Vadeli hesap değeri alınırken hata: {e}", exc_info=True)
        return 0.0


async def update_user_lot_settings(user_id: int, exchange: str, lot_amount: float, lot_percentage: float) -> bool:
    try:
        await asyncio.to_thread(db_operation,
            """
            UPDATE ayarlar 
            SET lot = ?, lot_percentage = ? 
            WHERE user_id = ? AND exchange = ?
            """,
            (f"{lot_amount:.2f}", f"{lot_percentage}", user_id, exchange),
            operation='update'
        )
        return True
    except Exception as e:
        logger.error(f"Lot ayarları güncellenirken hata: {e}")
        return False


async def back_to_lot_selection(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await _safe_answer(query)

    chat = update.effective_chat
    if not chat:
        return State.MAIN_MENU
    chat_id = chat.id

    selected_exchange = context.user_data.get('selected_exchange')
    if not selected_exchange:
        await context.bot.send_message(chat_id=chat_id, text="Lütfen önce bir borsa seçin.")
        return State.MAIN_MENU

    keyboard = [
        [
            InlineKeyboardButton("Lot Sayısı Girişi", callback_data=f"lot_number_{selected_exchange}"),
            InlineKeyboardButton("Lot Yüzdesi Girişi", callback_data=f"lot_percentage_{selected_exchange}")
        ],
        [InlineKeyboardButton("Geri", callback_data="back_to_strategy_settings")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await _render_strategy_menu(
        update=update,
        context=context,
        chat_id=chat_id,
        text="Lot seçim metodunu seçin:",
        reply_markup=reply_markup
    )
    return State.LOT_SELECTION_METHOD


async def safe_edit_message(
        query: CallbackQuery,
        text: str,
        reply_markup: Optional[InlineKeyboardMarkup] = None
) -> bool:
    """
    Mesajı güvenli bir şekilde güncellemeye çalışır

    :param query: Callback query
    :param text: Mesaj metni
    :param reply_markup: İnline klavye markup
    :return: İşlem başarılı ise True, değilse False
    """
    try:
        # Mesajı güncellemeye çalış
        await query.edit_message_text(
            text=text,
            reply_markup=reply_markup
        )
        return True
    except BadRequest as e:
        # Mesaj zaten güncel ise sessizce geç
        if "Mesaj değiştirilmedi" in str(e):
            logger.info("Mesaj zaten güncel")
            return True

        # Diğer BadRequest hatalarını logla
        logger.warning(f"Mesaj güncelleme hatası: {e}")

        # Alternatif olarak yeni mesaj gönderme
        try:
            # query.message'ın varlığını ve türünü kontrol et
            if query.message and hasattr(query.message, 'reply_text'):
                await query.message.reply_text(
                    text=text,
                    reply_markup=reply_markup
                )
                return True
            else:
                # Eğer reply_text kullanılamıyorsa, bot üzerinden mesaj gönder
                chat_id = query.message.chat_id if query.message and hasattr(query.message,
                                                                             'chat_id') else query.from_user.id

                await query.get_bot().send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup
                )
                return True
        except Exception as reply_error:
            logger.error(f"Yeni mesaj gönderme hatası: {reply_error}")
            return False
    except Exception as e:
        # Beklenmedik diğer hatalar
        logger.error(f"Mesaj güncelleme sırasında beklenmedik hata: {e}")
        return False


async def set_leverage(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    selected_exchange = context.user_data.get('selected_exchange')

    if not selected_exchange:
        await query.edit_message_text("Lütfen önce bir borsa seçin.")
        return State.MAIN_MENU

    # Mevcut ayarları al
    current_settings = get_user_settings(user_id, selected_exchange)
    current_leverage = current_settings.get('leverage', '25') if current_settings else '25'

    message = (
        f"{selected_exchange} borsası için kaldıraç ayarı\n"
        f"Mevcut Kaldıraç: {current_leverage}x\n"
        "Lütfen kaldıraç değerini girin:"
    )

    keyboard = [
        [InlineKeyboardButton("Geri", callback_data="back_to_strategy_settings")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    chat = update.effective_chat
    if not chat:
        return State.MAIN_MENU

    await _render_strategy_menu(
        update=update,
        context=context,
        chat_id=chat.id,
        text=message,
        reply_markup=reply_markup
    )
    return State.WAITING_LEVERAGE_INPUT


async def handle_leverage_input(update: Update, context: CallbackContext) -> int:
    user_input = update.message.text
    user_id = update.effective_user.id
    selected_exchange = context.user_data.get('selected_exchange')

    if not selected_exchange:
        await update.message.reply_text("Lütfen önce bir borsa seçin.")
        return State.MAIN_MENU

    try:
        # Kaldıraç değerini integer'a çevir
        leverage = int(user_input)

        # Kaldıraç sınırını kontrol et
        if leverage <= 0 or leverage > 125:
            raise ValueError("Kaldıraç 1 ile 125 arasında olmalıdır.")

        # Veritabanında leverage'ı güncelle
        update_user_settings(user_id, selected_exchange, {'leverage':leverage})

        # ✅ Kullanıcının yazdığı mesajı temizle (daha düzgün UI)
        try:
            await _safe_delete_message(context, update.message.chat.id, update.message.message_id)
        except Exception:
            pass

        # ✅ Aynı "strateji menü" mesajını güncelle
        await show_strategy_settings(update, context)
        return State.STRATEGY_SETTINGS



    except ValueError as e:
        # kullanıcı mesajını temizle

        try:
            await _safe_delete_message(context, update.message.chat.id, update.message.message_id)

        except Exception:
            pass

        chat = update.effective_chat

        if not chat:
            return State.WAITING_LEVERAGE_INPUT
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("Geri", callback_data="back_to_strategy_settings")]
        ])
        await _render_strategy_menu(
            update=update,
            context=context,
            chat_id=chat.id,
            text=f"❌ Hata: {escape(str(e))}\n\nLütfen 1 ile 125 arasında bir sayı girin.",
            reply_markup=reply_markup
        )
        return State.WAITING_LEVERAGE_INPUT


async def set_margin(update: Update, context: CallbackContext) -> int: # type: ignore
    _=context
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("CROSSED / ÇAPRAZ", callback_data="margin_crossed")],
        [InlineKeyboardButton("ISOLATED / İZOLE", callback_data="margin_isolated")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Lütfen margin türünü seçin:", reply_markup=reply_markup)
    return State.MARGIN_SELECTION


async def handle_margin_selection(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    selected_exchange = context.user_data.get('selected_exchange')

    if not selected_exchange:
        await query.edit_message_text("Lütfen önce bir borsa seçin.")
        return State.MAIN_MENU

    margin_type = 'CROSSED' if query.data == 'margin_crossed' else 'ISOLATED'

    try:
        # Veritabanında margin türünü güncelle
        db_operation(
            """
            UPDATE ayarlar 
            SET margin = ? 
            WHERE user_id = ? AND exchange = ?
            """,
            (margin_type, user_id, selected_exchange),
            operation='update'
        )

        await query.edit_message_text(f"Margin türü {margin_type} olarak ayarlandı.")
        return await show_strategy_settings(update, context)

    except Exception as e:
        logger.error(f"Margin ayarı sırasında hata: {e}")
        await query.edit_message_text(f"Hata oluştu: {str(e)}")
        return State.MARGIN_SELECTION


async def set_tp(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    callback_data = query.data
    tp_number = int(callback_data.split('_')[1][2:])
    selected_exchange = callback_data.split('_')[-1]

    context.user_data['selected_exchange'] = selected_exchange
    context.user_data['current_tp'] = tp_number

    current_settings = get_user_settings(user_id, selected_exchange) or {}
    tp_values = {}
    total_percentage = 0

    for i in range(1, 11):
        tp_value = current_settings.get(f'tp{i}', '%0')
        if tp_value is None:
            tp_value = '%0'
        if isinstance(tp_value, str):
            tp_value = tp_value.replace('%', '').strip()
        try:
            tp_values[f'tp{i}'] = int(tp_value)
            total_percentage += tp_values[f'tp{i}']
        except ValueError:
            tp_values[f'tp{i}'] = 0

    current_tp_value = tp_values.get(f'tp{tp_number}', 0)
    remaining_percentage = 100 - total_percentage + current_tp_value

    message = f"{selected_exchange} borsası için TP{tp_number} değerini ayarlayın.\n"
    message += f"Mevcut Değer: %{current_tp_value}\n"
    message += f"Toplam TP Yüzdesi: %{total_percentage}\n"
    message += f"Kalan Kullanılabilir Yüzde: %{remaining_percentage}\n"
    message += f"Lütfen TP{tp_number} için yeni değeri girin (örn: 10 for %10):"

    keyboard = [
        [InlineKeyboardButton("Geri", callback_data="back_to_tp_input")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(message, reply_markup=reply_markup)
    return State.TP_INPUT


async def handle_tp_input(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    tp_value = update.message.text.strip()

    # Kullanıcı girdisini doğrula
    if not tp_value.isdigit() or int(tp_value) < 0 or int(tp_value) > 100:
        keyboard = [
            [InlineKeyboardButton("Strateji Ayarlarına Dön", callback_data="strategy_settings")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Lütfen 0 ile 100 arasında geçerli bir sayı girin.",
            reply_markup=reply_markup
        )
        return State.TP_INPUT

    tp_value = int(tp_value)
    selected_exchange = context.user_data.get('selected_exchange')
    current_tp = context.user_data.get('current_tp')

    if not selected_exchange or not current_tp:
        await update.message.reply_text("Bir hata oluştu. Lütfen tekrar deneyin.")
        return State.STRATEGY_SETTINGS

    # Mevcut TP değerlerini al
    current_settings = get_user_settings(user_id, selected_exchange) or {}
    tp_values = {}
    total_percentage = 0
    for i in range(1, 11):
        tp_value_str = current_settings.get(f'tp{i}', '%0')
        if tp_value_str is None:
            tp_value_str = '%0'
        if isinstance(tp_value_str, str):
            tp_value_str = tp_value_str.replace('%', '').strip()
        try:
            tp_values[f'tp{i}'] = int(tp_value_str)
            total_percentage += tp_values[f'tp{i}']
        except ValueError:
            tp_values[f'tp{i}'] = 0

    # Mevcut TP'nin önceki değerini çıkar
    total_percentage -= tp_values.get(f'tp{current_tp}', 0)

    # Yeni toplam yüzdeyi hesapla
    new_total_percentage = total_percentage + tp_value

    # Toplam yüzdeyi kontrol et
    if new_total_percentage > 100:
        keyboard = [
            [InlineKeyboardButton("Strateji Ayarlarına Dön", callback_data="strategy_settings")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"%100 Değeri Aşıldığı için giriş yapamazsınız. "
            f"TP değerlerinin Toplamı %100 Geçemez. "
            f"(TP{current_tp}'de aşıldı, Toplam: %{new_total_percentage})",
            reply_markup=reply_markup
        )
        return State.TP_INPUT

    # Yeni TP değerini kaydet
    tp_values[f'tp{current_tp}'] = tp_value

    # %100'e ulaşıldıysa sonraki TP'leri sıfırla
    if new_total_percentage == 100:
        for i in range(current_tp + 1, 11):
            tp_values[f'tp{i}'] = 0

    # Veritabanına kaydet
    for tp, value in tp_values.items():
        query = f"UPDATE ayarlar SET {tp} = ? WHERE user_id = ? AND exchange = ?"
        db_operation(query, (f"{value}", user_id, selected_exchange), operation='update')

    # Kullanıcıya bilgi ver
    message = f"TP{current_tp} değeri %{tp_value} olarak kaydedildi.\n"
    message += f"Toplam TP yüzdesi: %{new_total_percentage}\n"
    if new_total_percentage == 100:
        message += "Not: %100'e ulaşıldığı için sonraki TP değerleri sıfırlandı."

    keyboard = [
        [InlineKeyboardButton("Strateji Ayarlarına Dön", callback_data="strategy_settings")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(message, reply_markup=reply_markup)

    # Strateji ayarlarına geri dön
    return State.STRATEGY_SETTINGS


async def back_to_tp_input(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    selected_exchange = context.user_data.get('selected_exchange')
    current_tp = context.user_data.get('current_tp')

    if not selected_exchange or not current_tp:
        await query.edit_message_text("Bir hata oluştu. Lütfen tekrar deneyin.")
        return State.STRATEGY_SETTINGS

    current_settings = get_user_settings(user_id, selected_exchange) or {}
    tp_values = {}
    total_percentage = 0

    for i in range(1, 11):
        tp_value = current_settings.get(f'tp{i}', '%0')
        if tp_value is None:
            tp_value = '%0'
        if isinstance(tp_value, str):
            tp_value = tp_value.replace('%', '').strip()
        try:
            tp_values[f'tp{i}'] = int(tp_value)
            total_percentage += tp_values[f'tp{i}']
        except ValueError:
            tp_values[f'tp{i}'] = 0

    current_tp_value = tp_values.get(f'tp{current_tp}', 0)
    remaining_percentage = 100 - total_percentage + current_tp_value

    message = f"{selected_exchange} borsası için TP{current_tp} değerini ayarlayın.\n"
    message += f"Mevcut Değer: %{current_tp_value}\n"
    message += f"Toplam TP Yüzdesi: %{total_percentage}\n"
    message += f"Kalan Kullanılabilir Yüzde: %{remaining_percentage}\n"
    message += f"Lütfen TP{current_tp} için yeni değeri girin (örn: 10 for %10):"

    keyboard = [
        [InlineKeyboardButton("Strateji Ayarlarına Dön", callback_data="strategy_settings")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(message, reply_markup=reply_markup)
    return State.TP_INPUT


async def set_stop_loss_strategy(update: Update, context: CallbackContext) -> int:
    _ = context
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("Sabit Stop Loss", callback_data="sl_fixed")],
        [InlineKeyboardButton("Yüzdesel Stop Loss", callback_data="sl_percentage")],
        [InlineKeyboardButton("Stop Loss Kapalı", callback_data="sl_off")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Stop loss stratejinizi seçin:", reply_markup=reply_markup)
    return State.STOPLOSS_SELECTION


async def handle_stop_loss_selection(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    selected_exchange = context.user_data.get('selected_exchange')
    strategy = query.data

    if strategy == "sl_fixed":
        update_user_settings(user_id, selected_exchange, {'stop_loss': 'fixed'})
        await query.edit_message_text("Sabit stop loss stratejisi seçildi. Sinyaldeki stop loss değeri kullanılacak.")
        return await show_strategy_settings(update, context)
    elif strategy == "sl_percentage":
        await query.edit_message_text("Yüzdesel stop loss için bir değer girin (örn: 2 for %2):")
        return State.STOPLOSS_PERCENTAGE
    else:  # sl_off
        update_user_settings(user_id, selected_exchange, {'stop_loss': 'off'})
        await query.edit_message_text("Stop loss kapatıldı.")
        return await show_strategy_settings(update, context)


async def handle_stop_loss_percentage(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    selected_exchange = context.user_data.get('selected_exchange')
    try:
        percentage = float(update.message.text)
        if percentage <= 0 or percentage > 100:
            raise ValueError
        update_user_settings(user_id, selected_exchange, {'stop_loss': 'percentage', 'sl_percentage': percentage})
        await update.message.reply_text(f"Yüzdesel stop loss %{percentage} olarak ayarlandı.")
        return await show_strategy_settings(update, context)
    except ValueError:
        await update.message.reply_text("Geçersiz giriş. Lütfen 0 ile 100 arasında bir sayı girin.")
        return State.STOPLOSS_PERCENTAGE


async def set_take_profit(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    selected_exchange = context.user_data.get('selected_exchange')

    if not selected_exchange:
        await query.edit_message_text("Lütfen önce bir borsa seçin.")
        return State.MAIN_MENU

    current_settings = get_user_settings(user_id, selected_exchange)
    current_tp = current_settings.get('take_profit', 'SINYAL_TP')

    keyboard = [
        [InlineKeyboardButton("Sinyal TP'lerini Kullan", callback_data=f"tp_sinyal_{selected_exchange}")],
    ]

    # TP1'den TP10'a kadar butonları oluştur
    for i in range(1, 11):
        keyboard.append([InlineKeyboardButton(f"TP{i} Gelince %100 Çık", callback_data=f"tp_{i}_{selected_exchange}")])

    keyboard.extend([
        [InlineKeyboardButton("Geri", callback_data="back_to_strategy_settings")]
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)
    message = f"Lütfen {selected_exchange} borsası için TakeProfit stratejinizi seçin:\n\n"
    message += f"Mevcut Değer: {current_tp}\n"
    message += "\nTP seçenekleri, ilgili TP seviyesine ulaşıldığında pozisyonun %100'ünün kapatılacağını belirtir."

    await query.edit_message_text(message, reply_markup=reply_markup)
    return State.SETTING_TAKE_PROFIT


async def process_take_profit(update: Update, context: CallbackContext) -> int:
    _=context
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    choice, selected_exchange = query.data.rsplit('_', 1)

    if choice == "tp_sinyal":
        value = "SINYAL_TP"
        message = "Sinyal TP'leri kullanılacak."
    elif choice.startswith("tp_") and choice[3:].isdigit():
        value = int(choice[3:])
        message = f"TP{value} geldiğinde pozisyon %100 kapatılacak."

    else:
        await query.edit_message_text("Geçersiz seçim.")
        return State.STRATEGY_SETTINGS

    update_user_settings(user_id, selected_exchange, {'take_profit': value})

    keyboard = [[InlineKeyboardButton("Strateji Ayarlarına Dön", callback_data="strategy_settings")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(f"{message}\n\nAyar kaydedildi.", reply_markup=reply_markup)
    return State.STRATEGY_SETTINGS


async def process_custom_tp(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    selected_exchange = context.user_data.get('selected_exchange')

    try:
        value = float(update.message.text)
        if value <= 0 or value > 100:
            raise ValueError

        update_user_settings(user_id, selected_exchange, {'take_profit': value})

        keyboard = [[InlineKeyboardButton("Strateji Ayarlarına Dön", callback_data="strategy_settings")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"Özel çıkış yüzdesi %{value} olarak ayarlandı.\n"
            f"Bu, emirin açılış fiyatının %{value} ilerisine takeprofit değeri yazılacağı anlamına gelir.",
            reply_markup=reply_markup
        )
        return State.STRATEGY_SETTINGS
    except ValueError:
        await update.message.reply_text("Geçersiz giriş. Lütfen 1 ile 100 arasında bir sayı girin.")
        return State.SETTING_CUSTOM_TP


async def show_terial_stop_settings(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    if not query:
        return State.STRATEGY_SETTINGS

    await _safe_answer(query)

    chat = update.effective_chat
    if not chat:
        return State.STRATEGY_SETTINGS
    chat_id = chat.id


    logging.info(f"Received callback data: {query.data}")

    parts = query.data.split('_')
    if len(parts) < 3:
        await query.edit_message_text("Geçersiz seçim formatı.")
        return State.STRATEGY_SETTINGS

    selected_exchange = parts[-1]  # Son parça her zaman borsa olacak

    context.user_data['selected_exchange'] = selected_exchange

    user_id = update.effective_user.id
    settings = get_user_settings(user_id, selected_exchange)
    current_terial_stop = settings.get('terial_stop', 'Ayarlanmamış')

    explanation = (
        f"Seçilen Borsa: {selected_exchange}\n"
        f"Mevcut Terial Stop Ayarı: {current_terial_stop}\n\n"
        "Terial Stop (Risk Yönetimi):\n"
        "- TP Bazlı: Belirli TP'ler vuruldukça SL kademeli kaydırılır.\n"
        "- Trailing Stop: Fiyat ilerledikçe SL yüzdesel olarak sürüklenir.\n"
        "- Kapat: Devre dışı.\n"
        "İleride: ATR tabanlı (volatilite adaptif) mod eklenecek.\n\n"
        "Lütfen bir strateji seçin:"
    )

    keyboard = [
        [InlineKeyboardButton("TP Bazlı", callback_data=f"set_terialstop_tp_{selected_exchange}")],
        [InlineKeyboardButton("Trailing Stop", callback_data=f"set_terialstop_trailing_{selected_exchange}")],
        [InlineKeyboardButton("Kapat", callback_data=f"set_terialstop_0_{selected_exchange}")],
        [InlineKeyboardButton("Geri", callback_data="back_to_strategy_settings")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await _render_strategy_menu(
        update=update,
        context=context,
        chat_id=chat_id,
        text=explanation,
        reply_markup=reply_markup
    )
    return State.CHOOSING_TERIAL_STOP


async def process_terial_stop(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    if not query:
        return State.STRATEGY_SETTINGS

    await _safe_answer(query)

    chat = update.effective_chat
    if not chat:
        return State.STRATEGY_SETTINGS
    chat_id = chat.id

    logging.info(f"Processing terial stop: {query.data}")

    parts = query.data.split('_')
    if len(parts) < 4:
        logging.error(f"Invalid query data format: {query.data}")
        await query.edit_message_text("Geçersiz seçim formatı.")
        return State.STRATEGY_SETTINGS

    strategy = parts[2]
    selected_exchange = parts[3]

    context.user_data['selected_exchange'] = selected_exchange
    context.user_data['selected_strategy'] = strategy

    if strategy == "0":
        update_user_settings(update.effective_user.id, selected_exchange, {'terial_stop': 'KAPALI'})
        message = "Stop loss stratejisi kapatıldı."
        keyboard = [[InlineKeyboardButton("Strateji Ayarlarına Dön", callback_data="strategy_settings")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await _render_strategy_menu(
            update=update,
            context=context,
            chat_id=chat_id,
            text=f"{message}\n\nAyar kaydedildi.",
            reply_markup=reply_markup
        )
        return State.STRATEGY_SETTINGS
    elif strategy in ["tp", "trailing"]:
        explanation = get_strategy_explanation(strategy)
        back_button = InlineKeyboardButton("Geri", callback_data=f"back_to_terial_stop_{selected_exchange}")
        reply_markup = InlineKeyboardMarkup([[back_button]])
        await _render_strategy_menu(
            update=update,
            context=context,
            chat_id=chat_id,
            text=explanation,
            reply_markup=reply_markup
        )
        return State.SETTING_TERIAL_STOP_PARAMS
    else:
        await query.edit_message_text("Geçersiz seçim.")
        return State.STRATEGY_SETTINGS


def parse_terial_stop(value: Optional[str]) -> Dict[str, Union[str, float]]:
    """
    terial_stop alanını normalize eder.
    Formatlar:
      KAPALI / OFF
      TP_n
      TRAILING_p (yüzde)
      ATR_m (gelecek)
    """
    if not value:
        return {"mode": "OFF"}
    v = str(value).upper().strip()
    if v in ("KAPALI", "OFF", "0"):
        return {"mode": "OFF"}
    if v.startswith("TP_"):
        try:
            level = int(v.split('_')[1])
            return {"mode": "TP_CHAIN", "param": level}

        except Exception as e:
            logging.error(f"Hata: {e}")

            return {"mode": "TP_CHAIN", "param": 1}
    if v.startswith("TRAILING_"):
        try:
            pct = float(v.split('_')[1])
            return {"mode": "PCT", "param": pct}

        except Exception as e:
            logging.error(f"Hata: {e}")

            return {"mode": "PCT", "param": 2.0}
    if v.startswith("ATR_"):
        try:
            mult = float(v.split('_')[1])
            return {"mode": "ATR", "param": mult}

        except Exception as e:
            logging.error(f"Hata: {e}")

            return {"mode": "ATR", "param": 2.0}
    # fallback
    return {"mode": "UNKNOWN", "raw": v}


def get_strategy_explanation(strategy: str) -> str:
    explanations = {
        "tp": (
            "TP Bazlı Stop Hareketi:\n"
            "- Seçtiğiniz seviye kadar TP vuruldukça SL bir önceki TP seviyesine veya breakeven'a taşınır.\n"
            "Örn: TP_1 seçerseniz: TP1 vurulunca SL giriş fiyatına çekilir.\n"
            "Lütfen 1-9 arası bir sayı girin."
        ),
        "trailing": (
            "Trailing Stop (Yüzdesel):\n"
            "- Fiyat lehinize ilerledikçe en yüksek (LONG) / en düşük (SHORT) referans güncellenir.\n"
            "- SL bu referansın seçtiğiniz yüzdesi kadar gerisinde sürüklenir.\n"
            "Ör: 2 => %2 trailing.\n"
            "Lütfen 0.1 - 20 arası bir yüzde değeri girin (örn: 1.5)."
        ),
    }
    return explanations.get(strategy, "Geçersiz strateji seçimi.")


async def process_terial_stop_params(update: Update, context: CallbackContext) -> int:
    user_input = update.message.text
    user_id = update.effective_user.id
    selected_exchange = context.user_data.get('selected_exchange')
    selected_strategy = context.user_data.get('selected_strategy')

    if not selected_exchange:
        await update.message.reply_text("Lütfen önce bir borsa seçin.")
        return State.STRATEGY_SETTINGS

    try:
        strategy_config = process_strategy_input(selected_strategy, user_input)
        # Çakışma kontrolü
        current = get_user_settings(user_id, selected_exchange)
        prev_val = current.get('terial_stop', 'OFF')

        # Yeni mod
        new_parsed = parse_terial_stop(strategy_config)
        old_parsed = parse_terial_stop(prev_val)

        # Eğer yeni mod TP_CHAIN ve eski mod PCT/ATR ise eskiyi override ediyoruz (zaten tek alan)
        # Ek bilgilendirme mesajı
        conflict_note = ""
        if new_parsed["mode"]=="TP_CHAIN" and old_parsed["mode"] in ("PCT", "ATR"):
            conflict_note = " (Önceki trailing kapatıldı)"
        elif new_parsed["mode"] in ("PCT", "ATR") and old_parsed["mode"]=="TP_CHAIN":
            conflict_note = " (Önceki TP bazlı stop kapatıldı)"

        update_user_settings(user_id, selected_exchange, {'terial_stop': strategy_config})
        await update.message.reply_text(
            f"{selected_strategy.upper()} bazlı Terial Stop ayarı kaydedildi: {strategy_config}{conflict_note}"
        )

    except ValueError as e:
        await update.message.reply_text(str(e))
        return State.SETTING_TERIAL_STOP_PARAMS

    keyboard = [[InlineKeyboardButton("Strateji Ayarlarına Dön", callback_data="strategy_settings")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Ayarlar kaydedildi. Ne yapmak istersiniz?", reply_markup=reply_markup)
    return State.STRATEGY_SETTINGS


def process_strategy_input(strategy: str, user_input: str) -> str:
    if strategy == 'tp':
        tp_level = int(user_input)
        if 1 <= tp_level <= 9:
            return f"TP_{tp_level}"
        else:
            raise ValueError("Lütfen 1 ile 9 arasında bir sayı girin.")
    elif strategy == 'trailing':
        trailing_percentage = float(user_input)
        if not (0.1 <= trailing_percentage <= 20):
            raise ValueError("Trailing yüzdesi 0.1 ile 20 arasında olmalıdır.")
        return f"TRAILING_{trailing_percentage}"
    else:
        raise ValueError("Geçersiz strateji seçimi.")


async def back_to_terial_stop(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    await show_terial_stop_settings(update, context)
    return State.CHOOSING_TERIAL_STOP


async def set_sl_tp_emir(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    selected_exchange = context.user_data.get('selected_exchange')
    if not selected_exchange:
        await query.edit_message_text("Lütfen önce bir borsa seçin.")
        return State.MAIN_MENU

    logger.info(f"SL-TP Emir'i değişim için ayarlama borsa seçimi : {selected_exchange}")

    user_id = query.from_user.id
    current_settings = get_user_settings(user_id, selected_exchange)
    current_sl_tp_emir = current_settings.get('sl_tp_emir', 'on') if current_settings else 'off'

    explanation = (
        f"Lütfen {selected_exchange} borsası için hesabınızda açılan pozisyonların "
        "STOPLOSS ve TAKEPROFIT emirlerinin gerçek emir olarak açılmasını isterseniz KULLAN demelisiniz. "
        "Eğer robotun emir olarak açmayıp hafızasından fiyatları takip edip belirtilen değere ulaştığında "
        "market fiyatından direk kapatmasını isterseniz KULLANMA deyiniz.\n\n"
        f"Eski Değer : {'KULLAN' if current_sl_tp_emir == 'on' else 'KULLANMA'}\n\n"
        "SL-TP Emirleri ayarını seçin:"
    )

    keyboard = [
        [InlineKeyboardButton("KULLAN", callback_data=f"sl_tp_emir_on_{selected_exchange}"),
         InlineKeyboardButton("KULLANMA", callback_data=f"sl_tp_emir_off_{selected_exchange}")],
        [InlineKeyboardButton("Geri", callback_data=f"back_to_strategy_settings_{selected_exchange}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(explanation, reply_markup=reply_markup)
    return State.SETTING_SL_TP_EMIR


async def handle_sl_tp_emir_choice(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data_parts = query.data.split('_')

    if len(data_parts) < 5:
        logger.error(f"Invalid callback data format: {query.data}")
        await query.edit_message_text("Geçersiz işlem. Lütfen tekrar deneyin.")
        return State.SETTING_SL_TP_EMIR

    choice = data_parts[3]
    selected_exchange = data_parts[4]

    logger.info(f"Handling SL-TP Emir choice for user {user_id}, exchange {selected_exchange}: {choice}")

    try:
        update_user_settings(user_id, selected_exchange, {'sl_tp_emir': choice})
        logger.info(f"Updated sl_tp_emir setting for user {user_id}, exchange {selected_exchange} to {choice}")
    except Exception as e:
        logger.error(f"Error updating sl_tp_emir setting: {e}")
        await query.edit_message_text("Ayar güncellenirken bir hata oluştu. Lütfen tekrar deneyin.")
        return State.SETTING_SL_TP_EMIR

    status_text = "Açık" if choice == "on" else "Kapalı"
    await query.edit_message_text(f"SL-TP Emirleri ayarı {status_text} olarak güncellendi.")

    # Kısa bir bekleme süresi
    await asyncio.sleep(0.1)

    # Strateji ayarları sayfasına geri dön
    return await show_strategy_settings(update, context)


async def set_maliyet_cek(update: Update, context: CallbackContext) -> int:
    query = update.callback_query

    try:
        await query.answer()
    except Exception as e:
        logger.warning(f"Query answer error: {e}")
        # Fallback mekanizması
        if update.effective_chat:
            await update.effective_chat.send_message("İşlem sırasında bir hata oluştu. Lütfen tekrar deneyin.")
        return State.MAIN_MENU

    selected_exchange = context.user_data.get('selected_exchange')
    if not selected_exchange:
        # Eğer seçili borsa yoksa
        try:
            await query.edit_message_text("Lütfen önce bir borsa seçin.")
        except Exception as e:
            logger.error(f"Message edit error: {e}")
            if update.effective_chat:
                await update.effective_chat.send_message("Lütfen önce bir borsa seçin.")
        return State.MAIN_MENU

    logger.info(f"Maliyetine Çek ayarı menüsü açıldı: {selected_exchange}")

    user_id = query.from_user.id
    current_settings = get_user_settings(user_id, selected_exchange)
    current_maliyet_cek = current_settings.get('maliyet_cek', '0') if current_settings else '0'

    explanation = (
        f"Lütfen {selected_exchange} borsası için açılan pozisyonun fiyatı belirli bir seviyeye ulaştıktan sonra "
        "bir kereye mahsus olmak üzere stoploss'u emirin açılış fiyatına çeker.\n\n"
        f"Mevcut Değer : {'KAPALI' if current_maliyet_cek == '0' else current_maliyet_cek}\n\n"
        "Maliyetine Çek ayarını seçin:"
    )

    keyboard = [
        [InlineKeyboardButton("KAPALI", callback_data=f"set_maliyet_cek_0_{selected_exchange}")],
        [InlineKeyboardButton("TP1", callback_data=f"set_maliyet_cek_1_{selected_exchange}")],
        [InlineKeyboardButton("TP2", callback_data=f"set_maliyet_cek_2_{selected_exchange}")],
        [InlineKeyboardButton("TP3", callback_data=f"set_maliyet_cek_3_{selected_exchange}")],
        [InlineKeyboardButton("TP4", callback_data=f"set_maliyet_cek_4_{selected_exchange}")],
        [InlineKeyboardButton("TP5", callback_data=f"set_maliyet_cek_5_{selected_exchange}")],
        [InlineKeyboardButton("TP6", callback_data=f"set_maliyet_cek_6_{selected_exchange}")],
        [InlineKeyboardButton("TP7", callback_data=f"set_maliyet_cek_7_{selected_exchange}")],
        [InlineKeyboardButton("TP8", callback_data=f"set_maliyet_cek_8_{selected_exchange}")],
        [InlineKeyboardButton("TP9", callback_data=f"set_maliyet_cek_9_{selected_exchange}")],
        [InlineKeyboardButton("Geri", callback_data="back_to_strategy_settings")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        # Mesajı güncelleme
        await query.edit_message_text(explanation, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Message edit error: {e}")
        # Fallback mekanizması
        if update.effective_chat:
            await update.effective_chat.send_message(
                explanation,
                reply_markup=reply_markup
            )

    return State.SETTING_MALIYET_CEK


async def handle_maliyet_cek_selection(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    data = query.data.split('_')
    if len(data) != 5 or data[0] != 'set' or data[1] != 'maliyet' or data[2] != 'cek':
        logger.error(f"Invalid callback data: {query.data}")
        return State.SETTING_MALIYET_CEK

    selected_value = data[3]
    selected_exchange = data[4]

    if not selected_exchange:
        await query.edit_message_text("Lütfen önce bir borsa seçin.")
        return State.MAIN_MENU

    user_id = query.from_user.id

    # Ayarları güncelle
    update_user_settings(user_id, selected_exchange, {'maliyet_cek': selected_value})

    logger.info(f"Maliyetine Çek ayarı güncellendi: {selected_exchange}, Değer: {selected_value}")

    # Onay mesajını düzenle
    status_text = "KAPALI" if selected_value == '0' else f"TP{selected_value}"
    await query.edit_message_text(f"Maliyetine Çek ayarı {status_text} olarak güncellendi.")

    # Kısa bir bekleme süresi
    await asyncio.sleep(0.5)

    # Strateji ayarları sayfasına geri dön
    return await show_strategy_settings(update, context)


async def set_maks_emir(update: Update, context: CallbackContext) -> int:
    _ = context
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("Lütfen maksimum emir sayısını girin (1-100 arası):")
    return State.SETTING_MAKS_EMIR


async def handle_maks_emir_input(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    selected_exchange = context.user_data.get('selected_exchange')

    if update.callback_query:
        maks_emir = update.callback_query.data
        await update.callback_query.answer()
    else:
        maks_emir = update.message.text

    try:
        maks_emir = int(maks_emir)
        if maks_emir < 1 or maks_emir > 100:
            raise ValueError("Maksimum emir sayısı 1 ile 100 arasında olmalıdır.")

        # Ayarları güncelle
        update_user_settings(user_id, selected_exchange, {'maks_emir': maks_emir})

        keyboard = [[InlineKeyboardButton("Strateji Ayarlarına Dön", callback_data="strategy_settings")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.callback_query:
            await update.callback_query.edit_message_text(
                f"Maksimum emir sayısı {maks_emir} olarak ayarlandı.",
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(
                f"Maksimum emir sayısı {maks_emir} olarak ayarlandı.",
                reply_markup=reply_markup
            )

        return State.STRATEGY_SETTINGS

    except ValueError as e:
        keyboard = [[InlineKeyboardButton("Geri", callback_data="back_to_strategy_settings")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.callback_query:
            await update.callback_query.edit_message_text(
                str(e) + "\nLütfen 1 ile 100 arasında bir sayı girin.",
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(
                str(e) + "\nLütfen 1 ile 100 arasında bir sayı girin.",
                reply_markup=reply_markup
            )

        return State.SETTING_MAKS_EMIR


# email için buton eklemeyi unutma
def update_user_email(user_id, exchange, email):
    query = "UPDATE ayarlar SET e_mail = ? WHERE user_id = ? AND exchange = ?"
    db_operation(query, (email, user_id, exchange), operation='update')
    logger.info(f"Kullanıcı e-postası güncellendi: Kullanıcı {user_id}, Borsa {exchange}, E-posta: {email}")


def format_remaining_time(days: float) -> str:
    if days >= 1:
        return f"{int(days)} gün"
    hours = days * 24
    if hours >= 1:
        minutes = (hours - int(hours)) * 60
        return f"{int(hours)} saat {int(minutes)} dakika"
    minutes = days * 24 * 60
    return f"{int(minutes)} dakika"


def update_or_insert_settings(user_id: int, selected_exchange: str, user_info: dict, user_channel_info: dict) -> bool:
    try:
        # Mevcut kaydı kontrol et
        existing_record = db_operation(
            "SELECT 1 FROM ayarlar WHERE user_id = ? AND exchange = ?",
            (user_id, selected_exchange),
            operation='select',
            fetch_all=False
        )

        if not existing_record:
            # Kayıt yoksa insert yap
            db_operation(
                """
                INSERT INTO ayarlar (
                    user_id, 
                    exchange, 
                    username, 
                    channel_id, 
                    channel_name
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    selected_exchange,
                    user_info['username'],
                    user_channel_info['channel_id'],
                    user_channel_info['channel_name']
                ),
                operation='insert'
            )
        else:
            # Kayıt varsa update yap
            db_operation(
                """
                UPDATE ayarlar 
                SET username = ?, 
                    channel_id = ?, 
                    channel_name = ?
                WHERE user_id = ? AND exchange = ?
                """,
                (
                    user_info['username'],
                    user_channel_info['channel_id'],
                    user_channel_info['channel_name'],
                    user_id,
                    selected_exchange
                ),
                operation='update'
            )

        return True
    except Exception as e:
        logger.error(f"Settings update/insert error: {e}")
        return False


async def toggle_active_status(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    user_id = update.effective_user.id
    selected_exchange = query.data.split('_')[-1]

    user_channel_info = get_user_channel_info(user_id, selected_exchange)

    if not user_channel_info:
        await query.answer("Kanal bilgisi bulunamadı.")
        return await show_strategy_settings(update, context)

    remaining_days = user_channel_info.get('remaining_days', 0) if isinstance(user_channel_info, dict) else 0
    current_status = user_channel_info.get('aktif_pasif', 'Pasif')

    if remaining_days <= 0 and current_status == 'Pasif':
        await query.answer("Abonelik süreniz dolmuş. Aktif yapamazsınız.")
        return await admin_contact(update, context)

    new_status = 'Pasif' if current_status == 'Aktif' else 'Aktif'

    try:
        db_operation(
            "UPDATE user_channel_info SET aktif_pasif = ? WHERE user_id = ? AND exchange = ?",
            (new_status, user_id, selected_exchange),
            operation='update'
        )
        user_channel_info['aktif_pasif'] = new_status
        await query.answer(f"Oto Trade {new_status} olarak güncellendi.")
    except Exception as e:
        logging.error(f"Database update failed: {e}")
        await query.answer("Güncelleme sırasında bir hata oluştu.")

    return await show_strategy_settings(update, context)
