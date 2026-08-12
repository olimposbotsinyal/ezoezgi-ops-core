# olimpos_admin.py modül dosyası buradan başlıyor
from telegram import Message, CallbackQuery
from telegram.constants import ParseMode
import html
from olimpos_channel import channel_menu
import pytz
from logger_config import *
from strategies.alarm_strateji import OlimposStrategy # Sadece gerekli olanı import et
from typing import Tuple, Optional, Any
from data.olimpos_data import * # get_user_by_id buradan gelecek
from config.constants import State, AdminLevel
import json
from config_service import ConfigService
from datetime import timedelta, timezone
import asyncio
import traceback

logger = setup_logging('olimpos_admin_logları')
# Farklı amaçlar için farklı logger'lar
admin_logger = get_admin_logger()
user_logger = get_user_management_logger()
error_logger = get_error_logger()

# --- YENİ: ROL VE YETKİ YÖNETİM SİSTEMİ (RBAC) ---

PERMISSIONS_FILE_PATH = "config/permissions.json"
_PERMISSIONS_CACHE = {}
_PERMISSIONS_LAST_MODIFIED = 0


def load_permissions(force_reload: bool = False) -> dict:
    """
    permissions.json dosyasını okur ve önbelleğe alır.
    Değişiklik varsa veya zorlanırsa dosyayı yeniden okur.
    """
    global _PERMISSIONS_CACHE, _PERMISSIONS_LAST_MODIFIED
    try:
        if not os.path.exists(PERMISSIONS_FILE_PATH):
            admin_logger.error(f"Yetki dosyası bulunamadı: {PERMISSIONS_FILE_PATH}")
            return {}

        modified_time = os.path.getmtime(PERMISSIONS_FILE_PATH)

        if force_reload or not _PERMISSIONS_CACHE or modified_time > _PERMISSIONS_LAST_MODIFIED:
            with open(PERMISSIONS_FILE_PATH, 'r', encoding='utf-8') as f:
                _PERMISSIONS_CACHE = json.load(f)
            _PERMISSIONS_LAST_MODIFIED = modified_time
            admin_logger.info("Yetki yapılandırması dosyadan yeniden yüklendi.")

        return _PERMISSIONS_CACHE
    except (json.JSONDecodeError, FileNotFoundError, Exception) as e:
        admin_logger.critical(f"Yetki dosyası okunurken kritik hata: {e}")
        return _PERMISSIONS_CACHE # Hata durumunda eski önbelleği koru

def save_permissions(permissions_data: dict) -> bool:
    """
    Yetki verisini JSON dosyasına kaydeder ve cache'i yeniler.
    """
    try:
        with open(PERMISSIONS_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(permissions_data, f, indent=2, ensure_ascii=False)
        load_permissions(force_reload=True)
        admin_logger.info("Yetki yapılandırması başarıyla kaydedildi.")
        return True
    except Exception as e:
        admin_logger.error(f"Yetki dosyası kaydedilirken hata: {e}")
        return False

async def user_has_permission(user_id: int, permission: str) -> bool:
    """
    Bir kullanıcının belirli bir yetkiye (string olarak) sahip olup olmadığını kontrol eder.
    """
    try:
        permissions_config = load_permissions()
        if not permissions_config.get("roles"):
            return False
        user_level_enum = await get_admin_level(user_id)
        # get_admin_level, bir AdminLevel enum nesnesi döndürüyor.
        # UserRole enum'unu oluşturmak için bu nesnenin tamsayı değerini (.value) kullanmalıyız.
        if not isinstance(user_level_enum, AdminLevel):
            admin_logger.error(f"get_admin_level'den beklenmedik türde veri geldi: {type(user_level_enum)}")
            return False
        user_role_name = AdminLevel(user_level_enum.value).name

        permissions_for_role = permissions_config.get("roles", {}).get(user_role_name, [])
        return permission in permissions_for_role
    except Exception as e:
        admin_logger.error(f"Yetki kontrolü hatası: user_id={user_id}, permission={permission}, error={e}")
        return False

async def get_registered_exchanges() -> list:
    """
    Veritabanındaki 'api_key' tablosundan benzersiz borsa isimlerini alır.
    AI eğitimi ve diğer dinamik borsa listeleri için kullanılır.
    """
    query = "SELECT DISTINCT exchange FROM api_key"
    # db_operation senkron olduğu için to_thread ile çalıştırıyoruz.
    results = await asyncio.to_thread(db_operation, query, operation='select', fetch_all=True)
    if not results:
        return []
    return [row[0] for row in results]


async def get_active_exchanges() -> list[dict]:
    """
    Veritabanından 'is_active = 1' olan borsaları ve logolarını alır.
    """
    query = "SELECT name, logo_url FROM exchange_status WHERE is_active = 1 ORDER BY name"
    results = await asyncio.to_thread(db_operation, query, operation='select', fetch_all=True)
    if not results:
        return []
    return [{'name':row[0], 'logo':row[1]} for row in results]


async def get_all_exchanges_status() -> list[dict]:
    """Veritabanındaki tüm borsaların durumunu ve logosunu alır."""
    query = "SELECT name, is_active, logo_url FROM exchange_status ORDER BY name"
    results = await asyncio.to_thread(db_operation, query, operation='select', fetch_all=True)
    if not results:
        return []
    return [{'name':row[0], 'is_active':bool(row[1]), 'logo':row[2]} for row in results]


async def get_ai_model_status_message() -> str:
    """
    Admin menüsü için AI model durum metni oluşturur.

    - Sadece exchange_status tablosunda is_active=1 olan borsaları listeler.
    - 24 saatlik periyodik eğitim döngüsüne göre kalan süre / gecikme bilgisini yazar.
    - Aktif borsada metadata yoksa bunu belirtir ve eğitim önerisini güçlendirir.
    """
    try:
        message_parts = ["\n\n--- 🤖 *AI Alarm Sistemi Durumu* ---\n"]

        # 1) Aktif borsaları DB'den çek (borsaları yönet ekranı ile aynı kaynak)
        active_exchanges = await get_active_exchanges()  # [{'name':..., 'logo':...}, ...]
        active_names = [ex["name"].lower().strip() for ex in active_exchanges if ex.get("name")]

        if not active_names:
            message_parts.append("⚠️ Aktif borsa bulunmuyor. (Borsaları Yönet menüsünden borsa aktif edin.)")
            try:
                active_alarms_count = OlimposStrategy.get_active_alarms_count()
                message_parts.append(f"📊 Aktif Alarmlar: {active_alarms_count}")
            except Exception:
                message_parts.append("📊 Aktif Alarmlar: Bilinmiyor")
            return "\n".join(message_parts)

        # 2) Metadata'yı oku
        metadata_path = "models/metadata.json"
        all_metadata = {}

        if os.path.exists(metadata_path) and os.path.getsize(metadata_path) > 0:
            try:
                with open(metadata_path, "r", encoding="utf-8") as f:
                    all_metadata = json.load(f) or {}
            except json.JSONDecodeError:
                logger.warning(f"{metadata_path} dosyası bozuk/okunamıyor, durum bilgisi atlanıyor.")
                all_metadata = {}
        else:
            all_metadata = {}

        message_parts.append("📜 *Borsa Bazlı Model Durumları:*")

        # Zaman hesapları
        now_utc = datetime.now(pytz.utc)
        istanbul_tz = pytz.timezone("Europe/Istanbul")
        cycle = timedelta(hours=24)

        oldest_train_time_utc = None
        missing_meta_for_active = False
        any_overdue = False
        max_overdue = timedelta(0)  # en fazla gecikme (aktifler içinde)

        # 3) Sadece aktif borsaları göster
        for ex_name in active_names:
            metadata = all_metadata.get(ex_name) or all_metadata.get(ex_name.upper()) or all_metadata.get(ex_name.lower())

            # Eski yapıda farklı anahtarlar varsa (timestamp/model_results) onları zaten kullanmıyoruz.
            if not isinstance(metadata, dict):
                metadata = {}

            ts = metadata.get("timestamp")
            if not ts:
                missing_meta_for_active = True
                message_parts.append(f"❓ *{ex_name.upper()}:* Model bilgisi yok")
                continue

            try:
                last_train_time_utc = datetime.fromisoformat(ts)
            except Exception:
                missing_meta_for_active = True
                message_parts.append(f"❓ *{ex_name.upper()}:* Zaman bilgisi okunamadı")
                continue

            # tz-aware yap
            if last_train_time_utc.tzinfo is None:
                last_train_time_utc = pytz.utc.localize(last_train_time_utc)
            else:
                last_train_time_utc = last_train_time_utc.astimezone(pytz.utc)

            age = now_utc - last_train_time_utc
            last_train_local = last_train_time_utc.astimezone(istanbul_tz)

            # Durum ikonu: <24h ✅, >=24h ⚠️
            if age < cycle:
                status_icon = "✅"
            else:
                status_icon = "⚠️"
                any_overdue = True
                overdue = age - cycle
                if overdue > max_overdue:
                    max_overdue = overdue

            # En eski eğitim zamanını bul (aktifler içinde)
            if oldest_train_time_utc is None or last_train_time_utc < oldest_train_time_utc:
                oldest_train_time_utc = last_train_time_utc

            message_parts.append(
                f"{status_icon} *{ex_name.upper()}:* Eğitimli (Son: {last_train_local.strftime('%Y-%m-%d %H:%M')})"
            )

        # 4) Öneri metni (24 saatlik periyoda göre)
        if missing_meta_for_active:
            message_parts.append(
                "\n💡 Bazı *aktif* borsalarda model bilgisi yok. "
                "Bu borsalar için en kısa sürede eğitim önerilir."
            )
        else:
            # oldest_train_time_utc aktiflerden en eski olan
            if oldest_train_time_utc:
                age_oldest = now_utc - oldest_train_time_utc

                if age_oldest < cycle:
                    remaining = cycle - age_oldest
                    total_seconds = int(remaining.total_seconds())
                    hours = total_seconds // 3600
                    minutes = (total_seconds % 3600) // 60
                    message_parts.append(
                        f"\n💡 Sonraki periyodik eğitime yaklaşık *{hours} sa {minutes} dk* var. \n"
                        f"(*En erken* {hours} sa {minutes} dk sonra eğitim önerilir.)"
                    )
                else:
                    # gecikme hesapla (en az bir tanesi zaten overdue)
                    total_seconds = int(max_overdue.total_seconds())
                    hours = total_seconds // 3600
                    minutes = (total_seconds % 3600) // 60
                    message_parts.append(
                        f"\n💡 Modellerin yeniden eğitilmesi önerilir.\n "
                        f"(Periyodik eğitim eşiği aşıldı: *{hours} sa {minutes} dk* gecikme)"
                    )
            else:
                message_parts.append("\n💡 Aktif borsalar için eğitim zamanı bilgisi bulunamadı.")

        # 5) Aktif alarm sayısı
        try:
            active_alarms_count = OlimposStrategy.get_active_alarms_count()
            message_parts.append(f"📊 Aktif Alarmlar: {active_alarms_count}")
        except Exception as e:
            logger.error(f"Aktif alarm sayısı alınırken hata: {e}")
            message_parts.append("📊 Aktif Alarmlar: Bilinmiyor")

        return "\n".join(message_parts)

    except Exception as e:
        logger.error(f"AI model durum mesajı oluşturulurken hata: {e}", exc_info=True)
        return "\n\n--- 🤖 *AI Alarm Sistemi Durumu* ---\nDurum bilgisi alınırken bir hata oluştu."


async def admin_menu(update: Optional[Update], context: CallbackContext, chat_id_override: Optional[int] = None) -> State:
    """
    Kullanıcının yetkilerine göre dinamik olarak admin menüsünü gösterir.
    DÜZELTME: Hem komutla (yeni mesaj) hem de butonla (mesaj düzenleme)
              çağrılabilmesi için dinamik hale getirildi.
    YENİ: `update` olmadan, sadece `chat_id_override` ile de çağrılabilir.
    """

    # DÜZELTME: Değişkenleri try bloğunun dışında tanımlayarak "referenced before assignment" hatasını gider.
    query: Optional[CallbackQuery] = None
    chat_id: Optional[int] = chat_id_override
    user_id: Optional[int] = None

    try:
        # Değişkenleri update nesnesinden güvenli bir şekilde doldur
        if update and update.effective_user:
            user_id = update.effective_user.id
        if update and update.effective_chat:
            chat_id = update.effective_chat.id
        if update and hasattr(update, 'callback_query') and update.callback_query:
            query = update.callback_query
            user_id = query.from_user.id

        # Eğer chat_id hala None ise, chat_id_override'ı kullan
        if chat_id is None:
            chat_id = chat_id_override

        if not user_id and chat_id:
            user_id = chat_id

        if not user_id or not chat_id:
            admin_logger.error("Admin menüsü için kullanıcı veya sohbet ID'si bulunamadı.")
            return State.MAIN_MENU

        admin_logger.info(f"Admin menüsü gösteriliyor. User ID: {user_id}, Chat ID: {chat_id}")

        # Yetki kontrolü
        if not await user_has_permission(user_id, "access_admin_menu"):
            if query:
                await query.answer("Bu alana erişim yetkiniz yok.", show_alert=True)
            elif update and update.message:
                await update.message.reply_text("Bu alana erişim yetkiniz yok.")
            return State.MAIN_MENU

        # --- DİNAMİK BUTON OLUŞTURMA (Mevcut mantık korunuyor, çok iyi!) ---
        keyboard = []
        button_meta = load_permissions().get("button_meta", {})

        # Yetkiye göre butonları ekle (JSON'daki sıraya göre)
        for perm_key, meta in button_meta.items():
            if await user_has_permission(user_id, perm_key):
                label = meta.get("label", perm_key)
                emoji = meta.get("emoji", "⚙️")
                callback_action = perm_key

                # Özel durumlar için callback_data'yı değiştir
                if perm_key == "manage_user_subscriptions":
                    callback_action = "limit_user_time"
                elif perm_key == "assign_users_to_channels":
                    callback_action = "assign_user_channel"

                keyboard.append([InlineKeyboardButton(f"{emoji} {label}", callback_data=callback_action)])

        keyboard.append([InlineKeyboardButton("Ana Menüye Dön", callback_data="main_menu")])
        # --- DİNAMİK BUTON SONU ---

        reply_markup = InlineKeyboardMarkup(keyboard)

        # --- YENİ: Zenginleştirilmiş Admin Menüsü Mesajı ---
        base_message = "Yönetim Paneline hoş geldiniz. Lütfen bir işlem seçin."
        ai_status_message = await get_ai_model_status_message()
        message_text = base_message + ai_status_message
        # --- YENİ KOD SONU ---

        # DÜZELTME: Mesajı gönderme veya düzenleme
        if query:
            await query.edit_message_text(
                text=message_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN
            )
        else:
            # Yeni mesaj gönder (komutla veya arka plandan çağrıldığında)
            await context.bot.send_message(
                chat_id=chat_id,
                text=message_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

        return State.ADMIN_MENU

    except Exception as general_error:
        admin_logger.critical(f"Admin menüsü kritik hatası: {str(general_error)}", exc_info=True)
        # DÜZELTME: query ve chat_id burada tanımlı olmayabilir, None kontrolü ekle
        try:
            # Hata durumunda kullanıcıya güvenli bir şekilde bilgi ver
            if query: # query'nin tanımlı olduğundan emin ol
                await query.edit_message_text("❌ Admin menüsü yüklenirken bir hata oluştu.")
            elif chat_id:  # chat_id'nin tanımlı olduğundan emin ol
                await context.bot.send_message(chat_id=chat_id, text="❌ Admin menüsü yüklenirken bir hata oluştu.")
        except Exception as report_error:
            admin_logger.error(f"Hata mesajı gönderilemedi: {report_error}")

        return State.MAIN_MENU


async def train_models_menu(update: Update, context: CallbackContext) -> State:
    _= context
    """AI model eğitimi için borsa seçim menüsünü gösterir."""
    query = update.callback_query
    user_id = query.from_user.id

    if not await user_has_permission(user_id, "train_ai_models_menu"):
        await query.answer("Bu işlemi yapma yetkiniz yok.", show_alert=True)
        return State.ADMIN_MENU

    await query.answer()

    active_exchanges = await get_active_exchanges()
    keyboard = [
        [InlineKeyboardButton(f"🎓 {exchange['name'].upper()}", callback_data=f"train_model_for_{exchange['name']}")]
        for exchange in active_exchanges
    ]
    keyboard.append([InlineKeyboardButton("🔙 Admin Menü", callback_data="admin_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "🤖 Hangi borsa için AI modelini eğitmek istersiniz?\n\n"
        "Seçtiğiniz borsanın verileri kullanılarak yeni bir model oluşturulacak.",
        reply_markup=reply_markup
    )
    return State.ADMIN_MENU


async def _background_training_task(exchange_name: str, user_id: int, context: CallbackContext, message_id: int,
        chat_id: int):
    """
    Arka planda eğitimi çalıştırır.
    Bittiğinde önce var olan mesajı güncellemeyi dener.
    Başarısız olursa (mesaj silinmişse) yeni bir bildirim gönderir.
    """
    try:
        # Eğitimi başlat
        success = await OlimposStrategy.train_ai_model_dynamic(
            exchange=exchange_name,
            triggered_by_user_id=user_id
        )

        if success:
            result_text = (
                f"✅ **{exchange_name.upper()}** Eğitim Tamamlandı!\n"
                f"Model güncellendi ve devreye alındı.\n"
                f"Bu mesajı silebilirsiniz."
            )
        else:
            result_text = (
                f"❌ **{exchange_name.upper()}** Eğitim Başarısız.\n"
                f"Lütfen logları kontrol edin."
            )

        # Kapatma butonu
        close_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑️ Mesajı Kapat", callback_data="delete_this_message")
        ]])

        try:
            # 1. YÖNTEM: Var olan mesajı güncellemeyi dene
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=result_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=close_markup
            )
        except Exception as edit_err:
            # Mesaj bulunamazsa (silinmişse vb.) logla ve yeni mesaj at
            logger.warning(f"Eğitim bildirim mesajı düzenlenemedi (muhtemelen silindi): {edit_err}")

            # 2. YÖNTEM: Yeni mesaj gönder (Fallback)
            await context.bot.send_message(
                chat_id=chat_id,
                text=result_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=close_markup
            )

    except Exception as e:
        logger.error(f"Arka plan eğitim hatası ({exchange_name}): {e}", exc_info=True)
        # Hata durumunda da kullanıcıya bilgi ver
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ {exchange_name.upper()} eğitimi sırasında beklenmeyen bir hata oluştu."
            )
        except:
            pass


async def start_model_training_for_exchange(update: Update, context: CallbackContext) -> State:
    """Belirli bir borsa için AI model eğitimini başlatır."""
    query = update.callback_query
    user_id = query.from_user.id

    # --- DÜZELTME: Chat ID'yi güvenli yoldan al ---
    chat_id = update.effective_chat.id

    if not await user_has_permission(user_id, "train_ai_models_menu"):
        await query.answer("Bu işlemi yapma yetkiniz yok.", show_alert=True)
        return State.ADMIN_MENU

    await query.answer()

    exchange_to_train = query.data.split("train_model_for_")[1]

    # Akıllı Eğitim Kontrolü
    last_train_time = OlimposStrategy.get_last_train_time_for_exchange(exchange_to_train)
    is_training_needed = True
    if last_train_time:
        time_since_last_train = datetime.now(timezone.utc) - last_train_time
        if time_since_last_train.total_seconds() < 3600:  # 1 saat
            is_training_needed = False

    if not is_training_needed:
        # Model güncelse uyarı ver ve menüye dön
        keyboard = [[InlineKeyboardButton("🔙 Admin Menü", callback_data="admin_menu")]]
        await query.edit_message_text(
            f"✅ {exchange_to_train.upper()} modeli zaten güncel (son 1 saat içinde eğitilmiş).",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return State.ADMIN_MENU

    # Bilgilendirme Mesajı
    loading_text = (
        f"⏳ **{exchange_to_train.upper()}** eğitimi arka planda başlatıldı...\n\n"
        "Siz diğer işlemlerinize devam edebilirsiniz. "
        "İşlem bittiğinde bu mesaj güncellenecektir."
    )

    # "Ana Menü" butonu
    keyboard = [[InlineKeyboardButton("🏠 Ana Menüye Dön", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Mesajı düzenle ve objeyi al
    sent_message = await query.edit_message_text(
        text=loading_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

    # Arka plan görevini başlat
    asyncio.create_task(
        _background_training_task(
            exchange_name=exchange_to_train,
            user_id=user_id,
            context=context,
            message_id=sent_message.message_id,
            chat_id=chat_id
        )
    )

    return State.ADMIN_MENU


async def handle_admin_buttons(update: Update, context: CallbackContext) -> State:    
    """
    Admin butonlarını işleyen ana fonksiyon
    """
    try:
        query = update.callback_query

        if not query:
            logger.error("Callback query bulunamadı")
            return State.ADMIN_MENU

        await query.answer()

        if not query.data:
            logger.error("handle_admin_buttons: Geçersiz (boş) callback query data alındı.")
            return State.ADMIN_MENU

        callback_data = query.data

        # --- DÜZELTME: Mesajı Kapat Butonu (Referans hatası giderildi) ---
        if callback_data == "delete_this_message":
            # query.message'in varlığını kontrol et
            if query.message:
                try:
                    await query.message.delete()
                except Exception as e:
                    logger.warning(f"Mesaj silinemedi: {e}")
            # State'i değiştirmeden dön
            return State.ADMIN_MENU
        # -----------------------------------------------------------------

        # Ana menü kontrolü
        if callback_data == 'main_menu':
            return State.MAIN_MENU

        # Admin handler'ları
        exact_handlers = {
            'admin_menu': admin_menu,
            'manage_admins': manage_admins,
            'view_all_apis': view_apis,
            'view_all_users': view_all_users,
            'limit_user_time': handle_limit_user_time,
            'assign_user_channel': show_user_list,
            'manage_channels': channel_menu,
            'manage_role_permissions': manage_permissions_menu,
            'manage_chart_settings': manage_chart_settings_menu,  # YENİ: Grafik ayarları menüsü
            'train_ai_models_menu': train_models_menu,
            'delete_any_user': view_all_users,  # DÜZELTME: Artık kullanıcı listesini gösteriyor
            'delete_any_api': view_apis, # DÜZELTME: Artık API listesini gösteriyor
            'setup_alarms': select_exchange_for_alarm, # DÜZELTME: Doğru fonksiyona yönlendirildi
            'manage_tuner_settings': manage_tuner_settings,  # YENİ: Tuner ayarları handler'ı
            'manage_exchanges': manage_exchanges_menu, # YENİ: Borsa yönetimi menüsü
         }

        # Prefix'li handler'lar
        prefix_handlers = {
            'select_admin_':select_admin,
            'admin_delete_api_':admin_delete_api,
            'user_details_':show_user_details,
            'show_full_user_details_':show_full_user_details,
            'select_exchange_':admin_handle_exchange_selection,
            'delete_user_':delete_user,
            'chart_setting_group_':select_chart_setting_group,
            'set_role_':set_user_role,
            'edit_chart_setting_':ask_new_chart_setting_value,
            'alarm_exchange_':handle_exchange_selection_for_alarm,
            'perm_role_':select_permission_for_role,
            'toggle_perm_':toggle_permission,
            'train_model_for_':start_model_training_for_exchange,
            'confirm_delete_user_':confirm_delete_user,
            'toggle_exchange_':toggle_exchange_status,
            'show_user_info_':show_user_channel_info,
        }

        if callback_data in exact_handlers:
            try:
                result = await exact_handlers[callback_data](update, context)
                return result if isinstance(result, State) else State.ADMIN_MENU
            except Exception as handler_error:
                logger.error(f"❌ Handler çalıştırma hatası: {str(handler_error)}", exc_info=True)
                await query.edit_message_text(
                    "❌ İşlem sırasında hata oluştu.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Admin Menü", callback_data='admin_menu')]
                    ])
                )
                return State.ADMIN_MENU

        # Prefix'li handler kontrolü
        for prefix, handler in prefix_handlers.items():
            if callback_data.startswith(prefix):
                logger.info(f"✅ Prefix handler bulundu: {prefix} - {callback_data}")
                try:
                    result = await handler(update, context)
                    logger.info(f"✅ Prefix handler başarıyla çalıştırıldı. Dönüş: {result}")
                    return result if isinstance(result, State) else State.ADMIN_MENU
                except Exception as prefix_error:
                    logger.error(f"❌ Prefix handler hatası: {str(prefix_error)}", exc_info=True)
                    await query.edit_message_text(
                        "❌ İşlem sırasında hata oluştu.",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔙 Admin Menü", callback_data='admin_menu')]
                        ])
                    )
                    return State.ADMIN_MENU

        # Geri butonu kontrolü
        if callback_data.startswith('back_to_admin_menu'):
            logger.info(f"✅ Admin menüye dönüş: {callback_data}")
            result = await admin_menu(update, context)
            return result if isinstance(result, State) else State.ADMIN_MENU

        # Bilinmeyen callback verisi
        logger.warning(f"⚠️ İşlenemeyen callback data: {callback_data}")
        await query.edit_message_text(
            "❌ Geçersiz işlem. Lütfen tekrar deneyin.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Admin Menü", callback_data='admin_menu')]
            ])
        )
        return State.ADMIN_MENU

    except Exception as e:
        logger.error(f"❌ handle_admin_buttons genel hatası: {str(e)}", exc_info=True)
        try:
            await update.callback_query.edit_message_text(
                "❌ Sistem hatası oluştu.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Admin Menü", callback_data='admin_menu')]
                ])
            )
        except Exception as edit_error:
            logger.error(f"❌ Hata mesajı gönderme hatası: {edit_error}")
        return State.ADMIN_MENU


# --- YENİ: ROL YETKİ YÖNETİM MENÜSÜ FONKSİYONLARI ---
async def manage_permissions_menu(update: Update, context: CallbackContext) -> State:
    _ = context
    """Süper adminin rolleri seçebileceği menüyü gösterir."""
    user_id = update.effective_user.id
    if not await user_has_permission(user_id, "manage_role_permissions"):
        await update.callback_query.answer("Bu alana erişim yetkiniz yok.", show_alert=True)
        return State.ADMIN_MENU

    keyboard = []
    # UserRole enum'undaki rolleri dinamik olarak al
    for role in AdminLevel:
        if role == AdminLevel.USER: continue # Kullanıcı rolünün yetkisi düzenlenemez
        keyboard.append([InlineKeyboardButton(f"Rol: {role.name}", callback_data=f"perm_role_{role.name}")])

    keyboard.append([InlineKeyboardButton("🔙 Admin Menü", callback_data='admin_menu')])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text(
        "Yetkilerini düzenlemek istediğiniz rolü seçin:",
        reply_markup=reply_markup
    )
    return State.ADMIN_MENU


async def select_permission_for_role(update: Update, context: CallbackContext) -> State:
    """Bir rol için mevcut yetkileri ve durumlarını gösterir."""
    query = update.callback_query
    user_id = query.from_user.id
    if not await user_has_permission(user_id, "manage_role_permissions"):
        await query.answer("Bu alana erişim yetkiniz yok.", show_alert=True)
        return State.ADMIN_MENU

    # Rol adını öncelikle context'ten al, yoksa callback'ten parse et.
    if query.data.startswith('perm_role_'):
        role_name = query.data.split('perm_role_')[1]
        context.user_data['editing_role'] = role_name
    else:
        role_name = context.user_data.get('editing_role')

    if not role_name:
        await query.edit_message_text("Hata: Düzenlenen rol bulunamadı. Lütfen menüye geri dönüp tekrar deneyin.")
        return State.ADMIN_MENU

    permissions_config = load_permissions()
    all_permissions_meta = permissions_config.get("button_meta", {})
    role_permissions = set(permissions_config.get("roles", {}).get(role_name, []))

    keyboard = []
    for perm_key, meta in all_permissions_meta.items():
        # Süper adminin kendi yetkilerini kısıtlamasını engelle
        if role_name == "SUPER_ADMIN" and perm_key == "manage_role_permissions":
            continue

        status_icon = "✅" if perm_key in role_permissions else "❌"
        button_text = f"{status_icon} {meta.get('label', perm_key)}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"toggle_perm_{perm_key}")])

    keyboard.append([InlineKeyboardButton("🔙 Rol Seçimine Dön", callback_data='manage_role_permissions')])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        f"*{role_name}* rolü için yetkileri düzenleyin:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

    # DÜZELTME: ConversationHandler'a bir sonraki adımda ne beklemesi gerektiğini söyle.
    # Bu state, 'toggle_perm_' veya 'manage_role_permissions' butonlarına basılmasını bekler.
    return State.ADMIN_MENU


async def toggle_permission(update: Update, context: CallbackContext) -> State:
    """Bir rol için belirli bir yetkiyi açar veya kapatır."""
    query = update.callback_query
    user_id = query.from_user.id
    if not await user_has_permission(user_id, "manage_role_permissions"):
        await query.answer("Bu alana erişim yetkiniz yok.", show_alert=True)
        return State.ADMIN_MENU

    permission_to_toggle = query.data.split('toggle_perm_')[1]
    role_name = context.user_data.get('editing_role')

    if not role_name:
        await query.edit_message_text("Hata: Düzenlenen rol bulunamadı. Lütfen tekrar deneyin.")
        return await manage_permissions_menu(update, context)

    permissions_config = load_permissions()
    role_permissions = set(permissions_config.get("roles", {}).get(role_name, []))

    if permission_to_toggle in role_permissions:
        role_permissions.remove(permission_to_toggle)
    else:
        role_permissions.add(permission_to_toggle)

    permissions_config["roles"][role_name] = sorted(list(role_permissions))
    save_permissions(permissions_config) # DÜZELTME: Coroutine'i await et

    # DÜZELTME: Menüyü yeniden çizmek için select_permission_for_role fonksiyonunu çağır
    # ve onun döndürdüğü State'i geri döndür. Bu, akışın aynı state'de kalmasını sağlar.
    return await select_permission_for_role(update, context)


# --- YETKİ YÖNETİMİ SONU ---
async def manage_tuner_settings(update: Update, context: CallbackContext) -> State:   
    _= context
    """AI Tuner ayarları menüsünü gösterir (GEÇİCİ)."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(   
        "🔧 *Tuner Ayarları* 🔧\n\n"
        "Bu özellik şu anda geliştirme aşamasındadır.\n"
        "Yakında strateji performansını otomatik olarak optimize etmenizi sağlayacak.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin Menü", callback_data="admin_menu")]]),
        parse_mode=ParseMode.MARKDOWN
    )
    return State.ADMIN_MENU

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
from io import BytesIO
import textwrap
import os


async def show_dynamic_exchange_menu(update: Update, context: CallbackContext, caption_text: str,
        callback_prefix: str, back_button_callback: str) -> State:
    """
    Tüm borsa seçim menüleri için merkezi, dinamik ve görsel bir menü oluşturur.
    """
    query = update.callback_query
    if not query:
        logger.error("show_dynamic_exchange_menu: Callback query bulunamadı.")
        return State.ADMIN_MENU

    await query.answer()

    active_exchanges = await get_active_exchanges()
    if not active_exchanges:
        await query.edit_message_text(
            "İşlem yapılabilecek aktif borsa bulunmuyor.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Geri", callback_data=back_button_callback)
            ]])
        )
        return State.ADMIN_MENU

    # Borsa özellikleri ve emojileri (Kısaltıldı, orijinal kodunuzdaki gibi kalsın)
    exchange_features = {
        'binance': 'Yüksek hacim ve en geniş coin yelpazesi.',
        'mexc': 'Düşük komisyon oranları ve yeni listelemeler.',
        'bybit': 'Gelişmiş vadeli işlem araçları ve yüksek likidite.',
        'okx': 'Kapsamlı finansal servisler ve güçlü altyapı.',
        'bitget': 'Copy trading ve yenilikçi ürünler.',
        'coinex': 'Düşük komisyonlar ve kendi tokeni (CET).',
        'bingx': 'Sosyal trading ve düşük başlangıç limitleri.',
        'weex': 'Hızlı ve güvenli vadeli işlem platformu.'
    }
    exchange_emojis = {
        'binance': '🔶', 'mexc': '🟢', 'bybit': '🟣', 'okx': '🔵',
        'bitget': '🔷', 'coinex': '⚪', 'bingx': '🟠', 'weex': '🔴'
    }
    # --- GÖRSEL OLUŞTURMA ---
    try:
        # --- 1. ADIM: GÜVENİLİR DOSYA YOLLARI ---
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = script_dir

        # --- 2. ADIM: FONT VE GÖRSEL AYARLARI ---
        # ÖNERİ: Daha modern ve Türkçe karakter destekli Poppins fontunu kullanalım.
        # Bu fontları 'assets/fonts/' klasörüne eklediğinizden emin olun.
        font_title_path = os.path.join(project_root, 'assets', 'fonts', 'Poppins-Bold.ttf')
        font_body_path = os.path.join(project_root, 'assets', 'fonts', 'Poppins-Regular.ttf')
        font_title = ImageFont.truetype(font_title_path, 20)
        font_body = ImageFont.truetype(font_body_path, 18)

        canvas_width = 400
        padding = 15
        line_height = 120
        canvas_height = padding * 2 + len(active_exchanges) * line_height # Yükseklik dinamik kalıyor.

        # --- 3. ADIM: GÖRSELİ OLUŞTURMA ---
        # Arka plan görselini yükle
        background_path = os.path.join(project_root, 'assets','borsa_logo_100x100', 'arkaplan.png')
        img = Image.open(background_path).convert("RGBA")
        img = img.resize((canvas_width, canvas_height))

        draw = ImageDraw.Draw(img)

        # Başlığı yaz
        draw.text((padding, padding // 2), caption_text, font=font_title, fill=(52, 52, 52))

        current_y = padding + 40

        # Borsaları ve logoları çiz
        for i, exchange in enumerate(active_exchanges):
            name = exchange['name']
            logo_path = os.path.join(project_root, 'assets', 'borsa_logo_100x100', f'{name}_logo.png')
            feature = exchange_features.get(name, 'Güvenilir ve hızlı işlem platformu.')

            # Logoyu yükle ve çiz
            try:
                logo_img = Image.open(logo_path).convert("RGBA")
                logo_img.thumbnail((80, 80))
                img.paste(logo_img, (padding, current_y), logo_img)
            except FileNotFoundError:
                # Logo bulunamazsa, yerine bir daire çiz
                draw.ellipse((padding, current_y, padding + 10, current_y + 50), fill=(30, 30, 50), outline=(50, 50, 90))
                draw.text((padding + 25, current_y + 15), "?", font=font_title, fill=(150, 150, 150))
                logger.warning(f"Logo dosyası bulunamadı: {logo_path}")

            # Metinleri çiz
            text_x = padding + 90
            draw.text((text_x, current_y + -5), f"{i + 1}. {name.upper()}", font=font_title, fill=(52, 52, 52))

            wrapped_text = textwrap.wrap(feature, width=30) # Genişliğe uygun metin kaydırma
            for j, line in enumerate(wrapped_text): # DÜZELTME: 'i' yerine 'j' kullanıldı
                draw.text((text_x, current_y + 25 + (j * 25)), line, font=font_body, fill=(52, 52, 52))

            current_y += line_height

        # --- 4. ADIM: GÖRSELİ BYTE'A ÇEVİRME ---
        image_buffer = BytesIO()
        img.save(image_buffer, format='PNG')
        image_buffer.seek(0)

    except (IOError, FileNotFoundError, UnidentifiedImageError, Exception) as e:
        error_message = "Menü oluşturulurken bir hata oluştu (Görsel dosyaları eksik veya bozuk olabilir)."
        logger.error(f"Dinamik menü oluşturma hatası: {e}", exc_info=True)
        return await _send_error_and_go_back(query, context, error_message, back_button_callback)

    # --- YENİ: Buton Genişliklerini Sabitleme Mantığı ---
    # 1. Önce tüm buton metinlerini oluştur
    button_texts = [f"{exchange_emojis.get(ex['name'], '🚨')} {ex['name'].upper()}" for ex in active_exchanges]

    # 2. En uzun metnin uzunluğunu bul
    max_len = 0
    if button_texts:
        max_len = max(len(text) for text in button_texts)

    # 3. Butonları oluştururken metinleri boşlukla doldur
    keyboard = [
        [InlineKeyboardButton(
            # Metni ortalamak için sağına ve soluna boşluk ekle
            text.center(max_len + 4), # +4 ek boşluk payı
            callback_data=f"{callback_prefix}{active_exchanges[i]['name']}"
        )]
        for i, text in enumerate(button_texts)
    ]

    keyboard.append([InlineKeyboardButton("🔙 Geri", callback_data=back_button_callback)])
    reply_markup = InlineKeyboardMarkup(keyboard)

    # --- Mesajı Gönder ---
    try:
        # --- DÜZELTME: Mesaj silme işlemi güvenli hale getirildi ---
        if query and query.message:
            try:
                await query.message.delete()
            except Exception:
                pass  # Mesaj zaten silinmişse görmezden gel

        # Chat ID'yi güvenli al
        chat_id_to_send = query.message.chat_id if query.message else query.from_user.id

        await context.bot.send_photo(
            chat_id=chat_id_to_send,
            photo=image_buffer,
            reply_markup=reply_markup
        )
    except Exception as send_error:
        error_message = "Menü gönderilirken bir hata oluştu. Lütfen tekrar deneyin."
        logger.error(f"Dinamik menü gönderme hatası: {send_error}", exc_info=True)
        return await _send_error_and_go_back(query, context, error_message, back_button_callback)

    return State.ADMIN_MENU

async def select_exchange_for_alarm(update: Update, context: CallbackContext) -> State:   
    """
    Alarm kurulumu için borsa seçimi
    """
    query = update.callback_query
    user_id = query.from_user.id

    if not await user_has_permission(user_id, "setup_alarms"):
        await query.answer("Bu işlemi yapma yetkiniz yok.", show_alert=True)
        return State.ADMIN_MENU

    # DÜZELTME: Menülerin üst üste yığılmasını önlemek için mevcut mesajı silmek yerine,
    # doğrudan görsel menüyü oluşturup gönderiyoruz. show_dynamic_exchange_menu
    # fonksiyonu, metin mesajından görsele geçerken eski mesajı kendisi yönetecek.
    await show_dynamic_exchange_menu(
        update=update,
        context=context,
        caption_text="Alarm Kurulumu İçin Borsa Seçin",
        callback_prefix="alarm_exchange_",
        back_button_callback="admin_menu"    
    )


async def handle_exchange_selection_for_alarm(update: Update, context: CallbackContext) -> State:
    """
    Kullanıcının alarm kurmak için seçtiği borsayı işler.
    Bu fonksiyon, API anahtarlarını güvenli bir şekilde alıp borsa bağlantısını kurar
    ve ardından alarm menüsünü gösterir.

    DÜZELTMELER:
    - API anahtarları doğrudan parametre olarak gönderiliyor
    - Detaylı hata yönetimi ve loglama
    - Güvenli mesaj silme ve düzenleme
    - Bağlantı başarısız olsa bile kullanıcıya bilgi veriliyor
    """
    query = update.callback_query
    if not query:
        logger.error("handle_exchange_selection_for_alarm: Callback query bulunamadı.")
        return State.ADMIN_MENU

    loading_message = None
    original_message_deleted = False

    try:
        await query.answer()

        # Chat ID'yi güvenli şekilde al
        try:
            chat_id = query.message.chat_id if isinstance(query.message, Message) else query.from_user.id
        except AttributeError:
            chat_id = update.effective_chat.id

        # Exchange adını parse et
        try:
            exchange_name = query.data.split('alarm_exchange_')[1].lower().strip()
        except (IndexError, AttributeError) as parse_err:
            logger.error(f"❌ Exchange adı parse edilemedi: {query.data} - Hata: {parse_err}")
            await query.edit_message_text(
                "❌ Geçersiz borsa seçimi. Lütfen tekrar deneyin.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Geri", callback_data='back_to_admin_menu')
                ]])
            )
            return State.ADMIN_MENU

        user_id = update.effective_user.id
        logger.info(f"✅ Exchange seçildi: {exchange_name} - User ID: {user_id}")

        # Önceki görsel menüyü sil (varsa)
        try:
            if isinstance(query.message, Message):
                if query.message.photo:
                    await query.message.delete()
                    original_message_deleted = True
                    logger.debug("✅ Önceki görsel menü silindi")
        except Exception as delete_err:
            logger.warning(f"⚠️ Önceki mesaj silinemedi (önemli değil): {delete_err}")

        # "Bekleyin" mesajı gönder
        try:
            loading_message = await context.bot.send_message(
                chat_id=chat_id,
                text=f"⏳ **Lütfen bekleyin...**\n\n"
                     f"🔄 **{exchange_name.upper()}** borsası ile bağlantı kuruluyor...\n"
                     f"📡 API anahtarları doğrulanıyor...",
                parse_mode=ParseMode.MARKDOWN,
            )
            logger.debug("✅ Yükleme mesajı gönderildi")
        except Exception as loading_err:
            logger.error(f"❌ Yükleme mesajı gönderilemedi: {loading_err}")
            # Yükleme mesajı gönderilemezse, orijinal mesajı düzenle
            if not original_message_deleted:
                try:
                    await query.edit_message_text(
                        f"⏳ {exchange_name.upper()} bağlantısı kuruluyor..."
                    )
                except Exception:
                    pass

        # 1. API bilgilerini veritabanından al
        try:
            api_info = await asyncio.to_thread(get_api_key, user_id, exchange_name)

            if not api_info:
                logger.error(f"❌ {exchange_name} için API bilgisi bulunamadı (user: {user_id})")
                error_text = (
                    f"❌ **{exchange_name.upper()}** için kayıtlı API anahtarınız bulunmuyor.\n\n"
                    f"Lütfen önce API anahtarlarınızı ekleyin."
                )

                if loading_message:
                    await loading_message.edit_text(
                        text=error_text,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("🔑 API Ayarları", callback_data='api_settings'),
                            InlineKeyboardButton("🔙 Geri", callback_data='back_to_admin_menu')
                        ]])
                    )
                else:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=error_text,
                        parse_mode=ParseMode.MARKDOWN
                    )
                return State.ADMIN_MENU

            logger.info(f"✅ API bilgileri veritabanından alındı: {exchange_name}")

            # API anahtarlarını güvenli şekilde kontrol et
            api_key = str(api_info.get('api_key', '')).strip()
            secret_key = str(api_info.get('secret_key', '')).strip()
            passphrase = str(api_info.get('passphrase', '')).strip() if api_info.get('passphrase') else None

            logger.info(f"✅ API Key mevcut: {'Evet' if api_key else 'Hayır'}")
            logger.info(f"✅ Secret Key mevcut: {'Evet' if secret_key else 'Hayır'}")
            logger.info(f"✅ Passphrase mevcut: {'Evet' if passphrase else 'Hayır'}")

            # API anahtarlarının uzunluğunu kontrol et
            if not api_key or not secret_key:
                logger.error(f"❌ {exchange_name} için API anahtarları eksik")
                error_text = (
                    f"❌ **{exchange_name.upper()}** için API anahtarları eksik.\n\n"
                    f"Lütfen API ayarlarınızı kontrol edin."
                )

                if loading_message:
                    await loading_message.edit_text(
                        text=error_text,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("🔑 API Ayarları", callback_data='api_settings'),
                            InlineKeyboardButton("🔙 Geri", callback_data='back_to_admin_menu')
                        ]])
                    )
                return State.ADMIN_MENU

            if len(api_key) < 10 or len(secret_key) < 10:
                logger.error(f"❌ {exchange_name} için API anahtarları çok kısa (muhtemelen geçersiz)")
                error_text = (
                    f"❌ **{exchange_name.upper()}** için API anahtarları geçersiz görünüyor.\n\n"
                    f"API Key uzunluğu: {len(api_key)}\n"
                    f"Secret Key uzunluğu: {len(secret_key)}\n\n"
                    f"Lütfen API anahtarlarınızı kontrol edin."
                )

                if loading_message:
                    await loading_message.edit_text(
                        text=error_text,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("🔑 API Ayarları", callback_data='api_settings'),
                            InlineKeyboardButton("🔙 Geri", callback_data='back_to_admin_menu')
                        ]])
                    )
                return State.ADMIN_MENU

        except Exception as api_fetch_err:
            logger.error(f"❌ API bilgileri alınırken hata: {api_fetch_err}", exc_info=True)
            error_text = "❌ API bilgileri alınırken bir hata oluştu. Lütfen tekrar deneyin."

            if loading_message:
                await loading_message.edit_text(text=error_text)
            else:
                await context.bot.send_message(chat_id=chat_id, text=error_text)
            return State.ADMIN_MENU

        # 2. Exchange'i başlat
        try:
            logger.info(f"🔄 {exchange_name} exchange başlatılıyor...")

            # Yükleme mesajını güncelle
            if loading_message:
                try:
                    await loading_message.edit_text(
                        text=f"⏳ **Lütfen bekleyin...**\n\n"
                             f"🔄 **{exchange_name.upper()}** bağlantısı kuruluyor...\n"
                             f"🔑 API anahtarları doğrulanıyor...\n"
                             f"📊 Market bilgileri yükleniyor...",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception:
                    pass

            connection_successful = await OlimposStrategy.initialize_exchange(
                user_id=user_id,
                exchange_name=exchange_name,
                api_key=api_key,
                secret_key=secret_key,
                passphrase=passphrase,
                context=context,
            )

            logger.info(f"✅ Exchange başlatma sonucu: {connection_successful}")

        except Exception as init_err:
            logger.error(f"❌ Exchange başlatma hatası: {init_err}", exc_info=True)
            connection_successful = False

        # 3. "Bekleyin" mesajını sil
        if loading_message:
            try:
                await loading_message.delete()
                logger.debug("✅ Yükleme mesajı silindi")
            except Exception as del_err:
                logger.warning(f"⚠️ Yükleme mesajı silinemedi: {del_err}")
            finally:
                loading_message = None

        # 4. Sonuç mesajı göster
        if connection_successful:
            # Başarılı bağlantı mesajı
            success_message = await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ **{exchange_name.upper()}** borsasına başarıyla bağlanıldı!\n\n"
                     f"📊 Market bilgileri yüklendi.\n"
                     f"🎯 Alarm sistemi hazır.",
                parse_mode=ParseMode.MARKDOWN,
            )

            # Kısa bir süre sonra başarı mesajını sil
            await asyncio.sleep(2)
            try:
                await success_message.delete()
            except Exception:
                pass

            logger.info(f"✅ {exchange_name} bağlantısı başarılı - Alarm menüsü gösteriliyor")

        else:
            # Bağlantı başarısız - Uyarı mesajı göster
            warning_text = (
                f"⚠️ **{exchange_name.upper()}** bağlantısında sorun oluştu.\n\n"
                f"**Olası Nedenler:**\n"
                f"• API anahtarları geçersiz\n"
                f"• API izinleri eksik\n"
                f"• Ağ bağlantısı sorunu\n"
                f"• Borsa geçici olarak erişilemez\n\n"
                f"**Çözüm Önerileri:**\n"
                f"1. API anahtarlarınızı kontrol edin\n"
                f"2. API izinlerini doğrulayın (Spot Trading gerekli)\n"
                f"3. Birkaç dakika sonra tekrar deneyin\n\n"
                f"Alarm menüsünü görüntüleyebilirsiniz, ancak bazı özellikler çalışmayabilir."
            )

            await context.bot.send_message(
                chat_id=chat_id,
                text=warning_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔑 API Ayarları", callback_data='api_settings'),
                    InlineKeyboardButton("🔄 Tekrar Dene", callback_data=f'alarm_exchange_{exchange_name}')
                ], [
                    InlineKeyboardButton("📊 Alarm Menüsü", callback_data='show_alarm_menu'),
                    InlineKeyboardButton("🔙 Geri", callback_data='back_to_admin_menu')
                ]])
            )

            logger.warning(f"⚠️ {exchange_name} bağlantısı başarısız - Kullanıcıya uyarı gösterildi")
            return State.ADMIN_MENU

        # 5. Alarm menüsünü göster
        try:
            # Context'e exchange bilgisini kaydet
            context.user_data['selected_exchange'] = exchange_name
            context.user_data['exchange_initialized'] = connection_successful

            return await OlimposStrategy.show_alarm_menu(update, context)

        except Exception as menu_err:
            logger.error(f"❌ Alarm menüsü gösterilirken hata: {menu_err}", exc_info=True)
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Alarm menüsü gösterilirken bir hata oluştu.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Ana Menü", callback_data='back_to_admin_menu')
                ]])
            )
            return State.ADMIN_MENU

    except Exception as e:
        logger.error(f"❌ handle_exchange_selection_for_alarm genel hatası: {str(e)}", exc_info=True)

        # Hata mesajı hazırla
        error_text = (
            "❌ **Borsa bağlantısı sırasında beklenmeyen bir hata oluştu.**\n\n"
            "Lütfen daha sonra tekrar deneyin veya destek ile iletişime geçin."
        )

        # Hata mesajını göster
        try:
            if loading_message:
                await loading_message.edit_text(
                    text=error_text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔄 Tekrar Dene", callback_data='alarm_setup'),
                        InlineKeyboardButton("🔙 Ana Menü", callback_data='back_to_admin_menu')
                    ]])
                )
            elif query and not original_message_deleted:
                await query.edit_message_text(
                    text=error_text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔄 Tekrar Dene", callback_data='alarm_setup'),
                        InlineKeyboardButton("🔙 Ana Menü", callback_data='back_to_admin_menu')
                    ]])
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=error_text,
                    parse_mode=ParseMode.MARKDOWN
                )
        except Exception as report_error:
            logger.error(f"❌ Hata mesajı gönderilemedi: {report_error}")

        return State.ADMIN_MENU


async def manage_exchanges_menu(update: Update, context: CallbackContext) -> State:
    """Süper Admin için borsa etkinleştirme/devre dışı bırakma menüsünü gösterir."""
    _ = context
    query = update.callback_query
    if not await user_has_permission(query.from_user.id, "manage_exchanges"):
        await query.answer("Bu işlemi sadece Süper Admin yapabilir.", show_alert=True)
        return State.ADMIN_MENU

    await query.answer()
    all_exchanges = await get_all_exchanges_status()

    keyboard = []
    for ex in all_exchanges:
        status_icon = "✅" if ex['is_active'] else "❌"
        button_text = f"{status_icon} {ex['name'].upper()}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"toggle_exchange_{ex['name']}")])

    keyboard.append([InlineKeyboardButton("🔙 Admin Menü", callback_data="admin_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "⚙️ *Borsa Yönetim Paneli*\n\n"
        "Kullanıcıların göreceği ve otomatik eğitim yapılacak borsaları seçin:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    return State.ADMIN_MENU


async def toggle_exchange_status(update: Update, context: CallbackContext) -> State:
    """Bir borsanın aktif/pasif durumunu değiştirir."""
    query = update.callback_query
    if not query:
        return State.ADMIN_MENU

    if not await user_has_permission(query.from_user.id, "manage_exchanges"):
        await query.answer("Bu işlemi sadece Süper Admin yapabilir.", show_alert=True)
        return State.ADMIN_MENU

    await query.answer()

    exchange_name = query.data.split('toggle_exchange_')[1].strip()

    # ✅ PostgreSQL integer(0/1) için toggle
    # Seçenek A (en temiz): CASE
    update_query = """
        UPDATE exchange_status
        SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END
        WHERE name = ?
    """

    try:
        await asyncio.to_thread(db_operation, update_query, (exchange_name,), operation='update')
    except Exception as e:
        logger.error(f"toggle_exchange_status DB hatası: {e}", exc_info=True)
        # Kullanıcıya uyarı ver, menüyü bozmadan kal
        try:
            await query.answer("Veritabanı güncellemesi başarısız oldu.", show_alert=True)
        except Exception:
            pass
        return State.ADMIN_MENU

    # Menüyü yenile
    try:
        return await manage_exchanges_menu(update, context)
    except BadRequest as e:
        # ✅ "Message is not modified" gelirse patlatma, sessiz geç
        if "Message is not modified" in str(e):
            try:
                await query.answer("Güncellendi.", show_alert=False)
            except Exception:
                pass
            return State.ADMIN_MENU
        raise


async def manage_admins(update: Update, context: CallbackContext) -> State:
    logger.info("Admin yönetimi işlemi başlatıldı.")
    user_id = update.effective_user.id
    _ = context  # Bu satır uyarıyı engeller

    if not await is_super_admin(user_id):
        logger.warning(f"Kullanıcı {user_id} ❌ süper admin değil.")
        await update.callback_query.answer("Bu işlemi sadece süper admin yapabilir.", show_alert=True)   
        return State.ADMIN_MENU

    result = await add_admin(update, context)
    return result if isinstance(result, State) else State.ADMIN_MENU


async def add_admin(update: Update, context: CallbackContext) -> State:
    _ = context
    logger.info("Admin ekleme/listeleme işlemi başlatıldı.")
    query = update.callback_query   
    await query.answer()

    all_users = get_all_users()
    admin_users = get_all_admins()

    admin_dict = {user['user_id']: user['level'] for user in admin_users}

    # DÜZELTME: AdminLevel.USER.value yerine doğrudan AdminLevel.USER kullan
    sorted_users = sorted(
        all_users,
        key=lambda u: (admin_dict.get(u['user_id'], AdminLevel.USER.value), u['username'] or str(u['user_id']))
    )

    keyboard: list[list[InlineKeyboardButton]] = []
    for user in sorted_users:
        user_id = user['user_id']
        username = user['username']

        # DÜZELTME: AdminLevel.USER.value yerine AdminLevel.USER.value
        current_level = admin_dict.get(user_id, AdminLevel.USER.value)
        admin_icon = get_admin_icon(current_level)
        try:
            role_name = AdminLevel(current_level).name
        except ValueError:
            role_name = "Kullanıcı"
        status = f"{admin_icon} {role_name}"

        display_name = username or str(user_id)
        keyboard.append([InlineKeyboardButton(f"{status} {display_name}",
                                             callback_data=f'select_admin_{user_id}')])

    keyboard.append([InlineKeyboardButton("Geri", callback_data='admin_menu')])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text("Admin yapmak veya durumunu değiştirmek istediğiniz kullanıcıyı seçin:",   
                                  reply_markup=reply_markup)

    return State.ADMIN_MENU


async def select_admin(update: Update, context: CallbackContext) -> State:
    logger.info("Rol atama için kullanıcı seçme işlemi başlatıldı.")
    query = update.callback_query   
    await query.answer()

    selected_user_id = int(query.data.split('_')[-1])
    context.user_data['selected_user_id_for_role'] = selected_user_id

    if await is_super_admin(selected_user_id):
        await query.edit_message_text(   
            "👑 Süper Admin'in rolü değiştirilemez.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri", callback_data='manage_admins')]])
        )
        return State.ADMIN_MENU

    current_level_enum = await get_admin_level(selected_user_id)
    current_role_name = AdminLevel(current_level_enum.value).name

    keyboard = []
    for role in AdminLevel:
        if role == AdminLevel.SUPER_ADMIN:
            continue

        # DÜZELTME: role.value doğrudan kullan
        role_icon = get_admin_icon(role.value)
        keyboard.append([InlineKeyboardButton(
            f"{role_icon} {role.name.replace('_', ' ').title()}",
            callback_data=f"set_role_{selected_user_id}_{role.name}"
        )])

    keyboard.append([InlineKeyboardButton("🔙 Kullanıcı Listesine Dön", callback_data='manage_admins')])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(   
        f"Kullanıcı ID: `{selected_user_id}`\n"
        f"Mevcut Rol: `{current_role_name}`\n\n"
        "Lütfen yeni bir rol seçin:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    return State.ADMIN_MENU


def get_admin_icon(level: int) -> str:
    """Admin seviyesine göre icon döndürür."""
    # DÜZELTME: AdminLevel enum değerlerini doğrudan kullan
    icons = {
        AdminLevel.SUPER_ADMIN.value: "👑",
        AdminLevel.MANAGER.value: "💼",
        AdminLevel.ADMIN.value: "🥇",
        AdminLevel.USER.value: "👤"
    }
    return icons.get(level, "👤")


# --- YENİ: GRAFİK AYARLARI YÖNETİMİ ---
async def manage_chart_settings_menu(update: Update, context: CallbackContext) -> State:
    """Grafik ayarlarını yönetmek için menüyü gösterir."""
    _ = context
    user_id = update.effective_user.id
    if not await user_has_permission(user_id, "manage_chart_settings"):
        await update.callback_query.answer("Bu alana erişim yetkiniz yok.", show_alert=True)
        return State.ADMIN_MENU

    keyboard = [
        [InlineKeyboardButton("Boyut Ayarları", callback_data="chart_setting_group_dimensions")],
        [InlineKeyboardButton("Metin Ayarları", callback_data="chart_setting_group_text_settings")],
        [InlineKeyboardButton("Görsel Yolları", callback_data="chart_setting_group_images")],
        [InlineKeyboardButton("PNL Tier Ayarları", callback_data="chart_setting_group_pnl_tiers")],
        [InlineKeyboardButton("Render Ayarları", callback_data="chart_setting_group_rendering")],
        # YENİ: Olay kartı ayarları için yeni buton eklendi.
        [InlineKeyboardButton("🖼️ Olay Kartı Rozetleri", callback_data="chart_setting_group_event_card_overlays")],
        [InlineKeyboardButton("🔙 Admin Menü", callback_data='admin_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text(   
        "Grafik ayarlarını yönetmek istediğiniz grubu seçin:",
        reply_markup=reply_markup
    )
    return State.ADMIN_MENU


async def select_chart_setting_group(update: Update, context: CallbackContext) -> State:
    """
    Seçilen grafik ayar grubunun detaylarını gösterir.
    DÜZELTME: Hem CallbackQuery (buton) hem de Message (metin) ile çağrılabilmesi için
              dinamik bir mesaj gönderme mantığı eklendi.
    """
    query = update.callback_query
    message = update.message   
    user_id = update.effective_user.id

    if not await user_has_permission(user_id, "manage_chart_settings"):
        if query:
            await query.answer("Bu alana erişim yetkiniz yok.", show_alert=True)
        else:
            await message.reply_text("Bu alana erişim yetkiniz yok.")   
        return State.ADMIN_MENU

    # group_name'i al: ya butondan ya da context'ten (set_chart_setting_value'dan sonra)
    if query and query.data.startswith('chart_setting_group_'):
        group_name = query.data.split('chart_setting_group_')[1]
        context.user_data['editing_chart_group'] = group_name
    else:
        group_name = context.user_data.get('editing_chart_group')

    if not group_name:
        error_text = "Hata: Ayar grubu bilgisi bulunamadı."
        if query:
            await query.edit_message_text(error_text)   
        elif message:
            await message.reply_text(error_text)
        return State.ADMIN_MENU

    params_to_show = []
    title = group_name.replace('_', ' ').title()

    if group_name == 'event_card_overlays':
        title = "Olay Kartı Rozet Ayarları"
        badge_cfg = ConfigService.get('charts.event_card_overlays.final_badge', {})
        params_to_show = [
            ('charts.event_card_overlays.final_badge.size.value', badge_cfg.get('size', {}).get('value')),
            ('charts.event_card_overlays.final_badge.pos_x_offset.value', badge_cfg.get('pos_x_offset', {}).get('value')),
            ('charts.event_card_overlays.final_badge.pos_y_offset.value', badge_cfg.get('pos_y_offset', {}).get('value')),
        ]
    else:
        chart_settings = ConfigService.get(f"charts.{group_name}", {})
        if isinstance(chart_settings, list):
            for i, item in enumerate(chart_settings):
                if "max_pct" in item:
                    max_pct_text = f"Max: {item.get('max_pct')}" if item.get('max_pct') is not None else "Max: Sınırsız"
                    params_to_show.append((f"charts.{group_name}.{i}", max_pct_text))
        elif isinstance(chart_settings, dict):
            for key, value in chart_settings.items():
                if key == "help": continue
                full_path = f"charts.{group_name}.{key}"
                if isinstance(value, dict) and "value" in value:
                    params_to_show.append((f"{full_path}.value", value.get("value")))
                else:
                    params_to_show.append((full_path, value))

    keyboard = []
    text = f"*{title}* ayarları:\n\n"
    path_map = {}

    for i, (path, value) in enumerate(params_to_show):
        short_key = f"key_{i}"
        path_map[short_key] = path
        display_key = path.split('.')[-2] if path.endswith('.value') else path.split('.')[-1]
        text += f"• {display_key}: `{value}`\n"
        keyboard.append([InlineKeyboardButton(f"Değiştir: {display_key}", callback_data=f"edit_chart_setting_{short_key}")])

    context.user_data['chart_setting_path_map'] = path_map
    keyboard.append([InlineKeyboardButton("🔙 Geri", callback_data='manage_chart_settings')])
    reply_markup = InlineKeyboardMarkup(keyboard)

    # DÜZELTME: Mesajı nasıl göndereceğimize karar ver
    if query:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)   
    elif message:
        await message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)   

    return State.WAITING_FOR_CHART_SETTING_EDIT


async def _send_error_and_go_back(query: Optional[CallbackQuery], context: CallbackContext, error_message: str,
        back_callback: str) -> State:
    """Hata mesajı gönderir ve bir önceki menüye dönmek için buton gösterir."""
    try:
        if query:
            await query.edit_message_text(
                error_message,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri", callback_data=back_callback)]])
            )
    except Exception as e:
        logger.error(f"Hata mesajı gönderilirken ek hata oluştu: {e}")
    return State.ADMIN_MENU


async def ask_new_chart_setting_value(update: Update, context: CallbackContext) -> State:
    """
    Kullanıcıdan yeni ayar değerini ister.
    DÜZELTME: JSON dosyasındaki 'help' ve 'bounds' alanlarını okuyarak
              kullanıcıya dinamik ve açıklayıcı bir yardım metni gösterir.
    """
    query = update.callback_query
    user_id = query.from_user.id   
    if not await user_has_permission(user_id, "manage_chart_settings"):
        await query.answer("Bu alana erişim yetkiniz yok.", show_alert=True)   
        return State.ADMIN_MENU

    # Gelen kısa anahtarı al
    short_key = query.data.replace('edit_chart_setting_', '')   

    # Context'teki haritadan tam yolu bul
    path_map = context.user_data.get('chart_setting_path_map', {})
    full_path = path_map.get(short_key)

    if not full_path:
        await query.edit_message_text("Hata: Ayar yolu bulunamadı. Lütfen menüye geri dönüp tekrar deneyin.")
        return State.ADMIN_MENU   

    context.user_data['editing_chart_setting_path'] = full_path

    current_value = ConfigService.get(full_path)

    # --- YENİ: Dinamik Yardım Metni Oluşturma ---

    # Ayarın bir üst objesini (meta verileri içeren) al
    parent_path = ".".join(full_path.split('.')[:-1])
    param_meta = ConfigService.get(parent_path, {})

    help_text = param_meta.get('help')
    bounds = param_meta.get('bounds')
    display_key = full_path.split('.')[-2] if full_path.endswith('.value') else full_path.split('.')[-1]

    # Mesajı satır satır oluştur
    message_lines = [
        f"*{display_key.replace('_', ' ').title()}* ayarı için yeni değeri girin.",
        f"Mevcut değer: `{current_value}`"
    ]

    if help_text:
        message_lines.append(f"\nℹ️ *Açıklama:* {help_text}")

    if bounds and isinstance(bounds, list) and len(bounds) == 2:
        message_lines.append(f"🔢 *Geçerli Aralık:* `{bounds[0]}` ile `{bounds[1]}` arası.")

    # Genel talimatı ekle
    message_lines.append("\n_Lütfen yeni değeri girin._")

    final_message = "\n".join(message_lines)
    # --- YENİ MANTIK SONU ---

    await query.edit_message_text(   
        text=final_message,
        parse_mode=ParseMode.MARKDOWN
    )
    return State.WAITING_FOR_CHART_SETTING_VALUE


async def set_chart_setting_value(update: Update, context: CallbackContext) -> State:
    user_id = update.effective_user.id 
    if not await user_has_permission(user_id, "manage_chart_settings"):
        await update.message.reply_text("Bu alana erişim yetkiniz yok.")
        return await admin_menu(update, context)

    setting_path = context.user_data.get('editing_chart_setting_path')
    new_value_raw = update.message.text

    if not setting_path:
        await update.message.reply_text("Hata: Ayar bilgileri eksik. Lütfen tekrar deneyin.")
        # Geri dönülecek menüyü belirle
        group_name = context.user_data.get('editing_chart_group', 'dimensions')
        update.callback_query = type('FakeQuery', (),
            {'data':f"chart_setting_group_{group_name}", 'message':update.message, 'from_user':update.effective_user})()
        return await select_chart_setting_group(update, context)

    # Değer tipine göre dönüştürme
    try:
        if new_value_raw.lower() == 'true':
            new_value = True
        elif new_value_raw.lower() == 'false':
            new_value = False
        elif new_value_raw.lstrip('-').replace('.', '', 1).isdigit():
            if '.' in new_value_raw:
                new_value = float(new_value_raw)
            else:
                new_value = int(new_value_raw)
        else:
            new_value = new_value_raw
    except ValueError:
        await update.message.reply_text("Geçersiz değer formatı. Lütfen doğru formatta girin.")   
        return State.WAITING_FOR_CHART_SETTING_VALUE

    # ConfigService üzerinden ayarı güncelle ve kaydet
    ConfigService.set(setting_path, new_value)
    ConfigService.save()
    OlimposStrategy.apply_chart_config()

    display_key = setting_path.split('.')[-2] if setting_path.endswith('.value') else setting_path.split('.')[-1]
    await update.message.reply_text(f"'{display_key}' ayarı başarıyla '{new_value}' olarak güncellendi.")

    return await select_chart_setting_group(update, context)


async def view_apis(update: Update, context: CallbackContext) -> State:  # ✅ context parametresi eklendi
    logger.info("Tüm API'ler görüntüleniyor.")
    _ = context  # Kullanılmadığı için uyarıyı engelle

    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    is_admin_user = await is_admin(user_id)

    if is_admin_user:
        query_str = """
            SELECT id, user_id, username, exchange, api_key, secret_key, passphrase 
            FROM api_key
        """
        apis = await asyncio.to_thread(db_operation, query_str, operation='select', fetch=True)
    else:
        query_str = """
            SELECT id, user_id, username, exchange, api_key, secret_key, passphrase 
            FROM api_key 
            WHERE user_id = ?
        """
        apis = await asyncio.to_thread(db_operation, query_str, (user_id,), operation='select', fetch=True)

    if not apis:
        logger.warning("Henüz hiç API kaydedilmemiş.")
        await query.edit_message_text("Henüz hiç API kaydedilmemiş.")
        return State.ADMIN_MENU

    await query.edit_message_text("API listesi:")
    for api in apis:   
        api_info = (
            f"<pre>"
            f"{'ID:':<15}{api[0]}\n"
            f"{'User ID:':<15}{api[1]}\n"
            f"{'Kullanıcı Adı:':<15}{api[2]}\n"
            f"{'Borsa:':<15}{api[3]}\n"
            f"{'API Key:':<15}{api[4][:5]}...\n"
        )

        if api[3] in EXCHANGES_REQUIRING_SECRET_KEY:
            api_info += f"{'Secret Key:':<15}{api[5][:5] if api[5] else ''}...\n"

        if api[3] in EXCHANGES_REQUIRING_PASSPHRASE:
            api_info += f"{'Passphrase:':<15}{api[6][:5] if api[6] else ''}...\n"

        api_info += "</pre>"

        keyboard = [
            [InlineKeyboardButton("Sil", callback_data=f'admin_delete_api_{api[0]}')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.effective_message.reply_text(api_info, reply_markup=reply_markup, parse_mode='HTML')

    # Geri butonunu ekleyelim
    back_keyboard = [[InlineKeyboardButton("Geri", callback_data='back_to_admin_menu')]]
    back_markup = InlineKeyboardMarkup(back_keyboard)
    await update.effective_message.reply_text(   
        "Tüm API'ler listelendi.\n""Admin Menüsüne Dönünüş", reply_markup=back_markup)

    return State.ADMIN_MENU


async def admin_delete_api(update: Update, context: CallbackContext) -> State:

    query = update.callback_query   
    await query.answer()

    # API ID'sini almak için '_' kullanıyoruz
    api_id = int(query.data.split('_')[3])   

    try:
        # API'yi veritabanından alma
        api_info = await asyncio.to_thread(db_operation, "SELECT user_id FROM api_key WHERE id = ?", (api_id,),
            operation='select', fetch=True)
        if not api_info:
            await query.answer("API bulunamadı.")
            return State.ADMIN_MENU   
        if not api_info:
            await query.answer("API bulunamadı.")
            return State.ADMIN_MENU   

        user_id = update.effective_user.id
        api_user_id = api_info[0][0]   

        # Kullanıcının admin ve süper admin olup olmadığını kontrol et
        is_super_admin_user = await is_super_admin(user_id)
        is_admin_user = await is_admin(user_id)

        # Süper admin kontrolü
        if is_super_admin_user:
            # Süper adminler tüm API'leri silebilir
            await asyncio.to_thread(db_operation, "DELETE FROM api_key WHERE id = ?", (api_id,), operation='delete')
            await query.edit_message_text(f"Süper Admin: API (ID: {api_id}) başarıyla silindi.")   
            await view_apis(update, context)  # API listesini yeniden göster
            return State.ADMIN_MENU

        # Admin kontrolü
        if is_admin_user:
            # Adminler sadece kendi API'lerini ve diğer kullanıcıların API'lerini silebilir
            if user_id != api_user_id:
                await query.answer(
                    "Bu API'yi silme yetkiniz yok. Sadece kendi API'lerinizi ve "
                    "kullanıcıların API'lerini silebilirsiniz.")
                return State.ADMIN_MENU

        # Kullanıcı kontrolü: Normal kullanıcılar sadece kendi API'lerini silebilir
        if user_id != api_user_id:
            await query.answer("Bu API'yi silme yetkiniz yok. Sadece kendi API'lerinizi silebilirsiniz.")
            return State.ADMIN_MENU

        # API'yi sil
        await asyncio.to_thread(db_operation, "DELETE FROM api_key WHERE id = ?", (api_id,), operation='delete')
        await query.edit_message_text(f"API (ID: {api_id}) başarıyla silindi.")   

        # API listesini yeniden göster
        await view_apis(update, context)
        return State.ADMIN_MENU  # ✅ Buraya eklendi

    except Exception as e:
        logger.error(f"API silme hatası: {str(e)}", exc_info=True)
        await query.answer("Bir hata oluştu. Lütfen tekrar deneyin.")
        return State.ADMIN_MENU


async def handle_viewing_apis_state(update: Update, context: CallbackContext) -> State:
    query = update.callback_query
    if query.data.startswith('admin_delete_api_'):   
        return await admin_delete_api(update, context)
    # Diğer olası durumları ele alın
    return State.ADMIN_MENU


async def api_search(update: Update, context: CallbackContext) -> State:
    logger.info("API arama işlemi başlatıldı.")
    await update.callback_query.answer()   
    await update.effective_message.reply_text("Lütfen aramak istediğiniz kelime ya da cümleyi girin:")   
    context.user_data['state'] = State.WAITING_FOR_SEARCH_TERM  # Durumu güncelle
    return State.WAITING_FOR_SEARCH_TERM


async def process_search(update: Update, context: CallbackContext):
    _= context
    logger.info("process_search fonksiyonu çağrıldı.")
    
    search_term = update.message.text.strip().lower()  # Küçük harfe çevir
    logger.info(f"Gelen arama terimi: {search_term}")

    apis = await asyncio.to_thread(search_apis_in_database, search_term)

    if apis:
        for api in apis:
            message = format_api_message(api)
            await update.message.reply_text(message)   

        await update.message.reply_text("Ana menüye dönmek için 'Ana Menü' butonuna tıklayın.",   
                                        reply_markup=get_main_menu_buttons())
    else:
        await update.message.reply_text("Arama kriterine uygun kayıt bulunamadı.")   


def search_apis_in_database(search_term):
    query = """
        SELECT ak.id, ak.user_id, ak.username, ak.exchange, ak.api_key, ak.secret_key, ak.passphrase, 
               ak.channel_name, ak.active_days
        FROM api_key ak
        LEFT JOIN admin_users au ON ak.user_id = au.user_id
        WHERE LOWER(ak.username) LIKE LOWER(?) OR LOWER(ak.exchange) 
        LIKE LOWER(?) OR LOWER(ak.channel_name) LIKE LOWER(?)
    """
    params = (f"%{search_term}%", f"%{search_term}%", f"%{search_term}%")
    result = db_operation(query, params, operation='select', fetch=True, fetch_all=True)  # Bu zaten thread-safe olmalı
    return result


def format_api_message(api):
    api_id, user_id, username, exchange, api_key, secret_key, passphrase, channel_name, active_days = api
    message = f"Kullanıcı: {username} (ID: {user_id})\n"
    message += f"Exchange: {exchange}\n"
    message += f"Kanal: {channel_name}\n"
    message += f"Aktif Gün: {active_days}\n"
    if api_key:
        message += f"API Key: {api_key[:5]}...\n"
    if secret_key:
        message += f"Secret Key: {secret_key[:5]}...\n"
    if passphrase:
        message += f"Passphrase: {passphrase[:5]}...\n"
    return message


def get_main_menu_buttons():
    keyboard = [
        [InlineKeyboardButton("Ana Menü", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


async def view_all_users(update: Update, context: CallbackContext) -> State:
    _ = context
    logger.info("Tüm kullanıcılar görüntüleniyor.")
    query = update.callback_query   
    await query.answer()

    users = db_operation(
        """
        SELECT
            ak.user_id,
            MAX(ak.username) AS username,
            CASE
                WHEN COALESCE(MAX(au.level), 99) = 0 THEN ?
                WHEN COALESCE(MAX(au.level), 99) = 1 THEN ?
                WHEN COALESCE(MAX(au.level), 99) = 2 THEN ?
                ELSE ?
            END AS role,
            COUNT(DISTINCT ak.exchange) AS exchange_count
        FROM api_key ak
        LEFT JOIN admin_users au ON ak.user_id = au.user_id
        GROUP BY ak.user_id
        ORDER BY username ASC
        """,
        (AdminLevel.SUPER_ADMIN.name, AdminLevel.MANAGER.name, AdminLevel.ADMIN.name, AdminLevel.USER.name),
        operation='select',
        fetch=True,
        fetch_all=True
    )

    if not users:
        logger.warning("Henüz hiç kullanıcı bulunmamaktadır.")
        await query.edit_message_text("Henüz hiç kullanıcı bulunmamaktadır.")   
        return State.ADMIN_MENU

    await query.edit_message_text("Kullanıcı listesi:")   
    for user in users:   
        user_id, username, role, exchange_count = user
        icon = "👑" if role == AdminLevel.SUPER_ADMIN.name else "💼" if role == AdminLevel.MANAGER.name else "🥇" if role == AdminLevel.ADMIN.name else "👤"

        user_info = (
            f"{icon} User ID: {user_id}\n"
            f"👤 Kullanıcı Adı: {username or 'Belirtilmemiş'}\n"
            f"🏅 Rol: {role}\n"
            f"💱 Borsa Sayısı: {exchange_count}\n"
        )

        keyboard = [
            [
                InlineKeyboardButton("📋 Detaylar", callback_data=f'show_full_user_details_{user_id}'),
                InlineKeyboardButton("🗑️ Sil", callback_data=f'delete_user_{user_id}'),
                # DÜZELTME: Silme onayı için bu butonu kullanacağız
            ]
            
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.effective_message.reply_text(user_info, reply_markup=reply_markup)   

    back_keyboard = [[InlineKeyboardButton("Geri", callback_data='back_to_admin_menu')]]  # Değiştirildi
    back_markup = InlineKeyboardMarkup(back_keyboard)
    await update.effective_message.reply_text("Tüm kullanıcılar listelendi.", reply_markup=back_markup)   

    return State.ADMIN_MENU


async def delete_user(update: Update, context: CallbackContext) -> State:
    _ = context
    """Kullanıcı silme işleminin ilk adımı: Onay isteme."""
    query = update.callback_query
    await query.answer()
    
    user_id_to_delete = int(query.data.split('_')[2])  # Kullanıcı ID'sini al
    logger.info(f"Kullanıcı silme işlemi başlatıldı: Kullanıcı ID: {user_id_to_delete}")

    try:
        # Kullanıcının süper admin olup olmadığını kontrol et
        if not await is_super_admin(update.effective_user.id):
            logger.warning(f"Kullanıcı {update.effective_user.id} bu işlemi yapamaz.")

            await query.edit_message_text("Bu işlemi sadece süper admin yapabilir.")
            return State.ADMIN_MENU

        user_info = await get_user_info(user_id_to_delete)
        username = user_info.get('username', f"ID: {user_id_to_delete}") if user_info else f"ID: {user_id_to_delete}"

        keyboard = [
            [InlineKeyboardButton(f"✅ EVET, {username} kullanıcısını SİL",
                callback_data=f"confirm_delete_user_{user_id_to_delete}")],
            [InlineKeyboardButton("❌ HAYIR, Vazgeç", callback_data="view_all_users")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            f"⚠️ *DİKKAT!* ⚠️\n\n`{username}` (ID: `{user_id_to_delete}`) kullanıcısını ve bu kullanıcıya ait *TÜM VERİLERİ* "
            f"(API anahtarları, kanal üyelikleri, ayarlar vb.) kalıcı olarak silmek istediğinizden emin misiniz?\n\n"
            "Bu işlem geri alınamaz!",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        return State.ADMIN_MENU

    except Exception as e:
        logger.error(f"Kullanıcı silme onayı sırasında hata: {str(e)}")
        await query.edit_message_text("Kullanıcı silme işlemi başlatılırken bir hata oluştu.")
        return State.ADMIN_MENU


async def confirm_delete_user(update: Update, context: CallbackContext) -> State:    
    """Kullanıcı silme işlemini onaylar ve gerçekleştirir (PostgreSQL)."""
    query = update.callback_query    
    await query.answer()    

    try:
        user_id_to_delete = int(query.data.split('_')[-1])    
    except Exception:
        await query.edit_message_text("❌ Geçersiz kullanıcı ID.")
        return State.ADMIN_MENU

    logger.info(f"Kullanıcı silme onayı alındı: Kullanıcı ID: {user_id_to_delete}")

    try:
        # Yetki kontrolü
        if not await is_super_admin(update.effective_user.id):    
            logger.warning(f"Yetkisiz silme denemesi: {update.effective_user.id}")    
            await query.edit_message_text("Bu işlemi sadece süper admin yapabilir.")
            return State.ADMIN_MENU

        # Silme işlemi (PostgreSQL): merkezi delete_user fonksiyonunu kullan
        ok = await asyncio.to_thread(delete_user, user_id_to_delete)

        if ok:
            await query.edit_message_text(f"✅ Kullanıcı (ID: {user_id_to_delete}) ve ilişkili tüm veriler silindi.")
        else:
            await query.edit_message_text(
                f"❌ Kullanıcı (ID: {user_id_to_delete}) silinemedi. Logları kontrol edin."
            )
            return State.ADMIN_MENU

        # Kullanıcı listesini yeniden göster
        await asyncio.sleep(2)
        return await view_all_users(update, context)

    except Exception as e:
        logger.error(f"Kullanıcı silinirken hata: {str(e)}", exc_info=True)
        await query.edit_message_text(f"Kullanıcı silinirken bir hata oluştu: {e}")
        return State.ADMIN_MENU


async def show_user_details(update: Update, context: CallbackContext) -> State:
    logger.info("Kullanıcı detayları görüntüleniyor.")
    query = update.callback_query # Kullanılmayan 'e' değişkeni kaldırıldı
    if not query and update.message:
        await update.message.reply_text("Kullanıcı detayları alınamadı.")   
        return State.ADMIN_MENU

    await query.answer()
    
    caller_id = query.from_user.id

    try:
        caller_admin_level = await get_admin_level(caller_id)
    except Exception as level_error:
        logger.error(f"Admin seviyesi alınırken hata: {str(level_error)}")
        await query.edit_message_text("Yetki kontrolünde hata oluştu.")
        return State.ADMIN_MENU

    if caller_admin_level not in [AdminLevel.SUPER_ADMIN, AdminLevel.ADMIN, AdminLevel.MANAGER]:
        # Güvenli mesaj gönderme
        try:
            if query.message:   
                await query.edit_message_text("Bu sayfayı görüntüleme yetkiniz yok.")
            else:
                await context.bot.send_message(
                    chat_id=caller_id,
                    text="Bu sayfayı görüntüleme yetkiniz yok."
                )
        except Exception as permission_error:
            logger.error(f"Yetki hatası mesajı gönderilirken hata: {str(permission_error)}")
        return State.ADMIN_MENU

    # Kullanıcı listesi için detaylı sorgu
    users_query = """
    WITH UserStats AS (
        SELECT 
            ak.user_id, 
            ak.username, 
            CASE 
                WHEN au.level = 0 THEN ?  -- SUPER_ADMIN
                WHEN au.level = 1 THEN ?  -- MANAGER
                WHEN au.level = 2 THEN ?  -- ADMIN
                ELSE ?  -- USER
            END as role,
            COUNT(DISTINCT ak.exchange) as exchange_count,
            MAX(uci.aktif_pasif) as aktif_pasif,
            MAX(uci.remaining_days) as remaining_days
        FROM api_key ak
        LEFT JOIN admin_users au ON ak.user_id = au.user_id
        LEFT JOIN user_channel_info uci ON ak.user_id = uci.user_id
        GROUP BY ak.user_id, ak.username, au.level
    )
    SELECT 
        user_id, 
        username, 
        role, 
        exchange_count, 
        aktif_pasif, 
        remaining_days
    FROM UserStats
    ORDER BY 
        CASE role 
            WHEN ? THEN 1 
            WHEN ? THEN 2 
            ELSE 3 
        END,
        username
    """

    try:
        users = db_operation(
            users_query,
            (
                AdminLevel.SUPER_ADMIN.name,
                AdminLevel.ADMIN.name,
                AdminLevel.USER.name,
                AdminLevel.SUPER_ADMIN.name,
                AdminLevel.ADMIN.name
            ),
            operation='select',
            fetch=True, fetch_all=True
        )
    except Exception as db_error:
        logger.error(f"Veritabanı sorgusunda hata: {str(db_error)}")
        await query.edit_message_text("Kullanıcı bilgileri alınamadı.")
        return State.ADMIN_MENU

    if not users:
        logger.warning("Henüz hiç kullanıcı bulunmamaktadır.")
        try:
            if query.message:   
                await query.edit_message_text("Henüz hiç kullanıcı bulunmamaktadır.")
            else:
                await context.bot.send_message(
                    chat_id=caller_id,
                    text="Henüz hiç kullanıcı bulunmamaktadır."
                )
        except Exception as message_error:
            logger.error(f"Mesaj gönderme hatası: {str(message_error)}")
        return State.ADMIN_MENU

    # Mesajları biriktir
    messages = []

    # Kullanıcıları listeleme
    for user in users:
        user_id, username, role, exchange_count, aktif_pasif, remaining_days = user

        # Role göre icon belirleme
        icon = (
             "👑" if role == AdminLevel.SUPER_ADMIN.name else
             "💼" if role == AdminLevel.MANAGER.name else
             "🥇" if role == AdminLevel.ADMIN.name else
             "👤"
        )

        # Aktiflik durumuna göre icon
        aktif_icon = "✅" if aktif_pasif == 'Aktif' else "❌"

        user_info = (
            f"{icon} {aktif_icon} User ID: {user_id}\n"
            f"👤 Kullanıcı Adı: {username or 'Belirtilmemiş'}\n"
            f"🏅 Rol: {role}\n"
            f"💱 Borsa Sayısı: {exchange_count}\n"
            f"⏳ Kalan Gün: {remaining_days or 'Bilinmiyor'}"
        )

        # Detay ve silme butonları
        keyboard = [
            [
                InlineKeyboardButton("📋 Detaylar", callback_data=f'show_full_user_details_{user_id}'),
                InlineKeyboardButton("🗑️ Sil", callback_data=f'delete_user_{user_id}')
            ]
        ]

        messages.append((user_info, keyboard))

    # Mesajları gönderme
    try:
        for i, (user_info, keyboard) in enumerate(messages):
            reply_markup = InlineKeyboardMarkup(keyboard)

            if i == 0:
                # İlk mesajı güncelle
                if query.message:   
                    await query.edit_message_text(user_info, reply_markup=reply_markup)
                else:
                    await context.bot.send_message(
                        chat_id=caller_id,
                        text=user_info,
                        reply_markup=reply_markup
                    )
            else:
                # Sonraki mesajları gönder
                await context.bot.send_message(
                    chat_id=caller_id,
                    text=user_info,
                    reply_markup=reply_markup
                )

        # Geri dön butonu
        back_keyboard = [
            [InlineKeyboardButton("🏠 Admin Menüye Dön", callback_data="back_to_admin_menu")]
        ]
        back_markup = InlineKeyboardMarkup(back_keyboard)

        await context.bot.send_message(
            chat_id=caller_id,
            text="👆 Kullanıcı işlemleri",
            reply_markup=back_markup
        )

    except Exception as send_error:
        logger.error(f"Kullanıcı detayları gönderiminde hata: {str(send_error)}")
        try:
            await context.bot.send_message(
                chat_id=caller_id,
                text="Kullanıcı detayları gösterilirken bir hata oluştu."
            )
        except Exception as final_error:
            logger.error(f"Son hata mesajı gönderme başarısız: {str(final_error)}")

    return State.ADMIN_MENU


async def show_full_user_details(update: Update, context: CallbackContext) -> State:
    logger.info("Kullanıcı tam detayları görüntüleniyor.")
    _ = context  # Bu satır uyarıyı engeller

    query = update.callback_query
    if not query and update.message:
        await update.message.reply_text("Kullanıcı detayları alınamadı.")
        return State.ADMIN_MENU

    await query.answer()

    # Kullanıcı ID'sini al
    user_id = int(query.data.split('_')[-1])
    logger.info(f"Kullanıcı tam detayları için sorgulama yapılıyor: user_id={user_id}")   

    # Çağıranın admin seviyesini kontrol et
    caller_id = query.from_user.id
    caller_admin_level = await get_admin_level(caller_id)

    # Detaylı kullanıcı bilgileri sorgusu
    user_details_query = """
    SELECT
    uci.user_id,
    MAX(uci.username) as username,
    CASE
        WHEN COALESCE(MAX(au.level), 99) = 0 THEN ?
        WHEN COALESCE(MAX(au.level), 99) = 1 THEN ?
        ELSE ?
    END as role,
    COUNT(DISTINCT ak.exchange) as exchange_count,
    MAX(uci.aktif_pasif) as aktif_pasif,
    COUNT(DISTINCT uci.channel_name) as channel_count
    FROM user_channel_info uci
    LEFT JOIN admin_users au ON uci.user_id = au.user_id
    LEFT JOIN api_key ak ON uci.user_id = ak.user_id
    WHERE uci.user_id = ?
    GROUP BY uci.user_id

    """

    fetched_user_details = db_operation(
        user_details_query,
        (
            AdminLevel.SUPER_ADMIN.name,
            AdminLevel.MANAGER.name,
            AdminLevel.USER.name,
            user_id
        ),
        operation='select',
        fetch=True, fetch_all=True
    )

    if not fetched_user_details:
        await query.edit_message_text("👤 Kullanıcı bulunamadı. 🚫")
        return State.ADMIN_MENU

    # Kullanıcı genel bilgileri
    user_row = fetched_user_details[0]
    (user_id, username, role, exchange_count, aktif_pasif, channel_count) = user_row

    # Borsa ve kanal detayları sorgusu
    details_query = """
    SELECT
        bi.exchange,
        COALESCE(bi.spot, 0) as spot_balance,
        COALESCE(bi.vadeli, 0) as futures_balance,
        COALESCE(bi.marjin, 0) as margin_balance,
        COALESCE(bi.kar_zarar_toplam, 0) as total_profit_loss,
        STRING_AGG(DISTINCT uci.channel_name, ',') as channels,
        STRING_AGG(DISTINCT uci.aktif_pasif, ',') as channel_statuses
    FROM borsa_info bi
    LEFT JOIN user_channel_info uci
      ON bi.user_id = uci.user_id AND bi.exchange = uci.exchange
    WHERE bi.user_id = ?
    GROUP BY bi.exchange, bi.spot, bi.vadeli, bi.marjin, bi.kar_zarar_toplam
    """

    details = db_operation(details_query, (user_id,), operation='select', fetch=True, fetch_all=True)

    # Detayları formatlama
    result_text = "🔬 Kullanıcı Tam Detayları\n\n"

    # Kullanıcı genel bilgileri - Artık fetched_user_details kullanılıyor
    result_text += f"👤 Kullanıcı Adı: {username or 'Belirtilmemiş'}\n"
    result_text += f"🆔 User ID: {user_id}\n"
    result_text += f"🏅 Rol: {role}\n"
    result_text += f"💱 Toplam Borsa Sayısı: {exchange_count}\n"
    result_text += f"📺 Toplam Kanal Sayısı: {channel_count}\n"

    # Kullanıcı aktif/pasif durumu
    if aktif_pasif == 'Aktif':
        result_text += f"✅ Genel Durum: {aktif_pasif}\n\n"
    else:
        result_text += f"❌ Genel Durum: {aktif_pasif}\n\n"
    # Sadece süper admin için detaylı bilgiler
    if caller_admin_level == AdminLevel.SUPER_ADMIN:
        total_balance = 0
        total_profit_loss = 0

        # Kanal Detayları
        result_text += "📺 Kanal Detayları:\n"
        for row in details:
            (exchange, _, _, _, _, channels, channel_statuses) = row

            if channels:
                channel_list = channels.split(',')
                status_list = channel_statuses.split(',')

                result_text += f"🏦 {exchange}:\n"
                for channel, status in zip(channel_list, status_list):
                    if status == 'Aktif':
                        result_text += f"   ✅ {channel}\n"
                    else:
                        result_text += f"   ❌ {channel}\n"

        # Borsa Detayları
        result_text += "\n💰 Borsa Detayları:\n"
        processed_exchanges = set()
        for row in details:
            (exchange, spot, futures, margin, profit_loss, _, _) = row

            if exchange not in processed_exchanges:
                processed_exchanges.add(exchange)

                result_text += f"🏦 {exchange}:\n"
                result_text += f"   📊 Spot Bakiye: {spot:.2f} USDT\n"
                result_text += f"   📊 Vadeli Bakiye: {futures:.2f} USDT\n"
                result_text += f"   📊 Marjin Bakiye: {margin:.2f} USDT\n"

                # Kar/Zarar gösterimi
                if profit_loss > 0:
                    result_text += f"   💹 Kar: +{profit_loss:.2f} USDT 🟢⬆️\n\n"
                elif profit_loss < 0:
                    result_text += f"   💹 Zarar: {profit_loss:.2f} USDT 🔴⬇️\n\n"
                else:
                    result_text += f"   💹 Kar/Zarar: {profit_loss:.2f} USDT\n\n"

                total_balance += float(spot or 0) + float(futures or 0) + float(margin or 0)
                total_profit_loss += float(profit_loss or 0)

        result_text += f"💰 Toplam Bakiye: {total_balance:.2f} USDT\n"

        # Toplam Kar/Zarar gösterimi
        if total_profit_loss > 0:
            result_text += f"💹 Toplam Kar: +{total_profit_loss:.2f} USDT 🟢⬆️\n"
        elif total_profit_loss < 0:
            result_text += f"💹 Toplam Zarar: {total_profit_loss:.2f} USDT 🔴⬇️\n"
        else:
            result_text += f"💹 Toplam Kar/Zarar: {total_profit_loss:.2f} USDT\n"

    # Geri dön butonu
    keyboard = [
        [InlineKeyboardButton("🔙 Geri", callback_data=f'back_to_admin_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(result_text, reply_markup=reply_markup)

    return State.ADMIN_MENU


async def show_user_list(update: Update, context: CallbackContext) -> State:
    """Kullanıcıları kanal ataması için listeleyen menüyü gösterir."""
    query = update.callback_query
    if query:
        await query.answer()
    logger.info("Kullanıcı listesi gösteriliyor (kanal atama akışı).")
    users = db_operation(
        """
        WITH latest_uci AS (
            SELECT DISTINCT ON (uci.user_id)
                uci.user_id,
                uci.username,
                uci.updated_at AS last_update
            FROM user_channel_info uci
            ORDER BY uci.user_id, uci.updated_at DESC
        ),
        AllUsers AS (
            SELECT
                user_id,
                username,
                last_update
            FROM latest_uci

            UNION ALL

            SELECT
                ak.user_id,
                ak.username,
                TIMESTAMP '1970-01-01 00:00:00' AS last_update
            FROM api_key ak
            WHERE NOT EXISTS (
                SELECT 1 FROM latest_uci lu WHERE lu.user_id = ak.user_id
            )
        )
        SELECT
            au.user_id,
            COALESCE(au.username, 'ID: ' || au.user_id) AS username,
            COALESCE(a.level, 99) AS admin_level
        FROM AllUsers au
        LEFT JOIN admin_users a ON au.user_id = a.user_id
        ORDER BY admin_level ASC, username ASC
        """,
        operation='select',
        fetch=True,
        fetch_all=True
    )

    if not users:
        await query.edit_message_text(   
            "Sistemde kanal atanabilecek kayıtlı kullanıcı bulunmuyor.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri", callback_data='admin_menu')]])
        )
        return State.ADMIN_MENU

    keyboard = []
    for user in users:
        user_id, username, admin_level = user
        admin_icon = get_admin_icon(admin_level)
        # Sola yaslı ve daha düzenli format
        button_text = f"{admin_icon} {(username or str(user_id)).ljust(15)} │ ID: {user_id}"
        callback_data = f"select_user_{user_id}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    keyboard.append([InlineKeyboardButton("🔙 Admin Menü", callback_data='admin_menu')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    # DÜZELTME: Fotoğraflı mesajı düzenleme hatasını önlemek için akıllı mesaj gönderme/düzenleme.
    if query and query.message:
        # Eğer mevcut mesaj bir fotoğraf içeriyorsa veya metin mesajıysa,
        # onu silip yenisini göndererek temiz bir geçiş sağla.
        if isinstance(query.message, Message):
            try:
                await query.message.delete()
            except Exception:
                pass # Mesaj zaten silinmiş olabilir.
            await context.bot.send_message(
                chat_id=query.message.chat_id, text="Kanal atamak için bir kullanıcı seçin:", reply_markup=reply_markup
            )
        else:
            await query.edit_message_text("Kanal atamak için bir kullanıcı seçin:",
                reply_markup=reply_markup)
    else:
        # Fallback for when query is None or message is not available
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Kanal atamak için bir kullanıcı seçin:", reply_markup=reply_markup)

    return State.WAITING_FOR_USER_SELECTION


async def handle_user_selection(update: Update, context: CallbackContext) -> State:
    query = update.callback_query   
    await query.answer()
    user_id = int(query.data.split('_')[2])

    context.user_data['selected_user_id'] = user_id

    # DÜZELTME: Eski metin tabanlı menü yerine yeni görsel menüyü çağır.
    # Bu fonksiyon, kullanıcının kayıtlı olduğu borsaları otomatik olarak bulup listeler.
    await show_dynamic_exchange_menu(
        update=update,
        context=context,
        caption_text=f"Kullanıcı (ID: {user_id}) için Borsa Seçin",
        callback_prefix="admin_assign_exchange_",  # Yeni bir prefix kullanıyoruz
        back_button_callback="assign_user_channel"  # Kullanıcı listesine geri dön
    )
    # DÜZELTME: Borsa seçimi bir sonraki adımı tetikleyeceği için doğru state'i döndür.
    return State.STRATEGY_WAITING_FOR_EXCHANGE_SELECTION


async def admin_handle_exchange_selection(update: Update, context: CallbackContext) -> State:
    """
    SADECE ADMIN için: Bir kullanıcıya kanal atamak amacıyla borsa seçildiğinde çalışır.
    """
    query = update.callback_query   
    await query.answer()    

    # DÜZELTME: Yeni callback prefix'ine göre borsa adını al
    exchange_name = query.data.split('admin_assign_exchange_')[1]
    user_id = context.user_data.get('selected_user_id')

    if not user_id:
        logger.error("admin_handle_exchange_selection: Seçili kullanıcı ID'si context'te bulunamadı.")
        await query.edit_message_text("Kullanıcı seçimi zaman aşımına uğradı. Lütfen tekrar deneyin.")
        return State.ADMIN_MENU

    # DÜZELTME: api_id yerine user_id ve exchange_name ile bilgileri al
    api_info = get_api_key(user_id, exchange_name)
    if not api_info:
        logger.error(
            f"admin_handle_exchange_selection: API bilgisi bulunamadı. User ID: {user_id}, Borsa: {exchange_name}")
        await query.edit_message_text("Seçilen borsa için API bilgileri bulunamadı.")
        return State.ADMIN_MENU

    context.user_data['api_key'] = api_info['api_key']
    context.user_data['secret_key'] = api_info['secret_key']
    context.user_data['passphrase'] = api_info['passphrase']
    context.user_data['selected_exchange'] = exchange_name
    context.user_data['selected_exchange'] = exchange_name
    # DÜZELTME: 'await' eksikliği düzeltildi.
    user_info = await get_user_info(user_id)
    context.user_data['selected_username'] = user_info.get('username', str(user_id)) if user_info else str(user_id)

    channels = get_channel_info()
    if not channels:
        await query.edit_message_text("Henüz hiç kanal eklenmemiş.")
        return State.ADMIN_MENU

    keyboard = [
        [InlineKeyboardButton(f"{channel[2]} ({channel[3]})", callback_data=f"assign_channel_{channel[1]}")]
        for channel in channels
    ]
    keyboard.append([InlineKeyboardButton("Geri", callback_data="back_to_exchange_selection")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    message = f"Seçilen Kullanıcı: {context.user_data['selected_username']}\n"
    message += f"Seçilen Borsa: {context.user_data['selected_exchange']}\n"
    message += "Lütfen bir kanal seçin:"

    # DÜZELTME: Fotoğraflı menüden sonra metin düzenleme hatasını önlemek için mesajı silip yeniden gönder.
    try:
        chat_id_to_send = user_id
        if query and isinstance(query.message, Message):
            chat_id_to_send = query.message.chat_id
            await query.message.delete()
        await context.bot.send_message(chat_id=chat_id_to_send, text=message, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Kanal seçim menüsü gönderilirken hata: {e}")
        # Fallback
        if user_id:
            await context.bot.send_message(chat_id=user_id, text=message, reply_markup=reply_markup)

    return State.WAITING_FOR_CHANNEL_SELECTION


async def handle_channel_selection(update: Update, context: CallbackContext) -> State:
    query = update.callback_query   
    await query.answer()

    try:
        channel_id = int(query.data.split('_')[2])
        user_id = context.user_data['selected_user_id']

        # Kanal bilgilerini al
        channel_info = get_channel_info(channel_id)
        if not channel_info:
            raise ValueError("Kanal bilgisi bulunamadı.")

        channel_name, channel_username = channel_info[2], channel_info[3]

        # API bilgilerini context'ten al
        api_key = context.user_data.get('api_key')
        secret_key = context.user_data.get('secret_key')
        passphrase = context.user_data.get('passphrase')
        exchange = context.user_data.get('selected_exchange')
        username = context.user_data.get('selected_username')

        if not all([api_key, secret_key, exchange, username]):
            raise ValueError("API bilgileri eksik.")

        # Kullanıcının admin seviyesini al
        admin_level = await get_admin_level(user_id)

        # Geçici bilgileri context'e kaydet
        context.user_data['temp_channel_id'] = channel_id
        context.user_data['temp_channel_info'] = {'channel_name': channel_name, 'channel_username': channel_username}

        # Şu anki tarihi başlangıç tarihi olarak ayarla
        start_date = datetime.now()
        context.user_data['start_date'] = start_date

        # Kullanıcıdan süre bilgisi iste
        await query.edit_message_text(
            f"Seçilen Kullanıcı: {username}\n"
            f"Seçilen Borsa: {exchange}\n"
            f"Seçilen Kanal: {channel_name} ({channel_username})\n"
            f"Başlangıç Tarihi: {start_date.strftime('%Y-%m-%d %H:%M')}\n"
            f"API Anahtarı: {api_key}\n"
            f"Passphrase: {passphrase}\n"  # Örnek kullanım
            f"Admin Seviyesi: {admin_level}\n\n"
            "Lütfen süreyi girin (Gün Saat Dakika formatında, örnek: 30 0 0):"
        )
        return State.WAITING_FOR_DURATION

    except ValueError as ve:
        await query.edit_message_text(f"Hata: {str(ve)}\nLütfen tekrar deneyin.")
    except Exception as e:
        logger.error(f"handle_channel_selection'da hata: {str(e)}")
        logger.error(f"Hata detayları: {traceback.format_exc()}")
        await query.edit_message_text("Bir sistem hatası oluştu. Lütfen daha sonra tekrar deneyin.")

    return State.ADMIN_MENU


async def handle_duration_input(update: Update, context: CallbackContext) -> State:
    user_input = update.message.text   
    try:
        days, hours, minutes = map(int, user_input.split())
        duration = timedelta(days=days, hours=hours, minutes=minutes)

        # Context'ten gerekli bilgileri al
        user_id = context.user_data['selected_user_id']
        channel_id = context.user_data['temp_channel_id']
        channel_info = context.user_data['temp_channel_info']
        start_date = context.user_data['start_date']
        end_date = start_date + duration

        exchange = context.user_data.get('selected_exchange')
        username = context.user_data.get('selected_username')
        api_key = context.user_data.get('api_key')
        secret_key = context.user_data.get('secret_key')
        passphrase = context.user_data.get('passphrase')
        admin_level = await get_admin_level(user_id)

        # Veritabanına kaydet
        save_user_channel_info(
            user_id=user_id,
            channel_id=channel_id,
            api_key=api_key,
            secret_key=secret_key,
            passphrase=passphrase,
            exchange=exchange,
            username=username,
            channel_name=channel_info['channel_name'],
            channel_username=channel_info['channel_username'],
            start_date=format_date(start_date),
            end_date=format_date(end_date),
            status="Aktif",
            admin_level=admin_level.value
        )

        # Kullanıcıya detaylı mesaj gönder
        message = (
            f"🌟 *Kanal Atama Bildirimi* 🌟\n\n"
            f"📢 *Kanal:* {channel_info['channel_name']} ({channel_info['channel_username']})\n"
            f"📅 *Başlangıç Tarihi:* {format_date(start_date)}\n"
            f"⏰ *Bitiş Tarihi:* {format_date(end_date)}\n"
            f"⏳ *Toplam Süre:* {days} gün {hours} saat {minutes} dakika\n"
            f"🔄 *Durum:* Aktif\n"
            f"💹 *Borsa:* {exchange}\n\n"
            f"*Kanala başarıyla atandınız. İyi çalışmalar!*"
        )

        await context.bot.send_message(
            chat_id=user_id,
            text=message,
            parse_mode='Markdown'
        )

        # Admin'e bilgi ver ve admin menüsüne dön
        keyboard = [[InlineKeyboardButton("Admin Menü", callback_data="admin_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(   
            "Kullanıcı başarıyla kanala atandı ve bilgilendirme mesajı gönderildi.",
            reply_markup=reply_markup
        )
        return State.ADMIN_MENU

    except ValueError:
        await update.message.reply_text("Geçersiz giriş. Lütfen 'Gün Saat Dakika' formatında girin (örnek: 30 0 0).")
        return State.WAITING_FOR_DURATION   
    except Exception as e:
        logger.error(f"handle_duration_input'da hata: {str(e)}")
        logger.error(f"Hata detayları: {traceback.format_exc()}")
        await update.message.reply_text("Bir sistem hatası oluştu. Lütfen daha sonra tekrar deneyin.")   
        return State.ADMIN_MENU


def validate_date(date_string):
    try:
        datetime.strptime(date_string, '%Y-%m-%d')
        return True
    except ValueError:
        return False


async def handle_start_date(update: Update, context: CallbackContext) -> State:
    start_date = update.message.text   
    try:
        datetime.strptime(start_date, "%Y-%m-%d")
    except ValueError:
        await update.message.reply_text("Geçersiz tarih formatı. Lütfen YYYY-MM-DD formatında girin:")   
        return State.WAITING_FOR_START_DATE

    context.user_data['temp_start_date'] = start_date
    await update.message.reply_text("Lütfen bitiş tarihini girin (YYYY-MM-DD formatında):")   
    return State.WAITING_FOR_END_DATE


async def handle_end_date(update: Update, context: CallbackContext) -> State:
    end_date = update.message.text   
    try:
        datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        await update.message.reply_text("Geçersiz tarih formatı. Lütfen YYYY-MM-DD formatında girin:")
        return State.WAITING_FOR_END_DATE

    # Tüm bilgileri kaydet
    user_id = context.user_data['selected_user_id']
    channel_id = context.user_data['temp_channel_id']
    api_info = context.user_data['temp_api_info']
    channel_info = context.user_data['temp_channel_info']
    start_date = context.user_data['temp_start_date']
    admin_level = await get_admin_level(user_id)

    current_date = datetime.now().date()
    status = "aktif" if current_date <= datetime.strptime(end_date, "%Y-%m-%d").date() else "pasif"

    save_user_channel_info(
        user_id,
        channel_id,
        api_info['api_key'],
        api_info['secret_key'],
        api_info['passphrase'],
        api_info['exchange'],
        api_info['username'],
        channel_info['channel_name'],
        channel_info['channel_username'],
        start_date,
        end_date,
        status,
        admin_level.value
    )

    await update.message.reply_text("İşlem başarıyla tamamlandı! Şimdi kullanıcıya mesaj gönderebilirsiniz.")   
    return State.WAITING_FOR_USER_MESSAGE


async def handle_user_message(update: Update, context: CallbackContext) -> State:
    message = update.message.text   
    user_id = context.user_data['selected_user_id']

    try:
        await context.bot.send_message(chat_id=user_id, text=message)
        await update.message.reply_text("Mesaj başarıyla gönderildi!")   
    except Exception as e:
        await update.message.reply_text(f"Mesaj gönderilirken bir hata oluştu: {str(e)}")

    return await admin_menu(update, context)


async def handle_channel_assignment(update: Update, context: CallbackContext) -> State:
    query = update.callback_query   
    await query.answer()

    channel_id, user_id = map(int, query.data.split('_')[2:])

    try:
        # Kullanıcıyı kanala atama işlemi
        assign_user_to_channel(user_id, channel_id)
        await query.edit_message_text(f"Kullanıcı (ID: {user_id}) başarıyla kanala atandı.")
    except Exception as e:
        logger.error(f"Kullanıcı kanala atanırken hata: {str(e)}")
        await query.edit_message_text("Kullanıcı kanala atanırken bir hata oluştu.")

    return await admin_menu(update, context)


async def handle_user_channel_assignment(update: Update, context: CallbackContext) -> State:
    query = update.callback_query   
    await query.answer()

    user_id = query.data.split('_')[-1]   

    # Kullanıcı ID'sini saklayın
    context.user_data['current_user_id'] = user_id

    # Kullanıcının kanallarını alın
    channels = db_operation(
        "SELECT channel_id, channel_name FROM bot_channels",
        operation='select',
        fetch=True
    )

    if not channels:
        await query.answer("Kayıtlı kanal bulunmamaktadır.")
        return State.ADMIN_MENU

    keyboard = [
        [InlineKeyboardButton(channel[1], callback_data=f'assign_channel_{user_id}_{channel[0]}')]
        for channel in channels
    ]
    keyboard.append([InlineKeyboardButton("Geri", callback_data='admin_menu')])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Lütfen bir kanal seçin:", reply_markup=reply_markup)   

    return State.WAITING_FOR_USER_CHANNEL_ASSIGNMENT  # Yeni durumu kullanın


async def assign_user(update: Update, context: CallbackContext) -> State:
    query = update.callback_query
    await query.answer()
    
    logger.info(f"Gelen callback verisi: {query.data}")

    parts = query.data.split('_')
    if len(parts) != 3 or parts[0] != 'assign':
        logger.error(f"Beklenmedik callback verisi alındı: {query.data}")
        await query.edit_message_text("Hata: Geçersiz işlem.")
        return State.ADMIN_MENU

    user_id = parts[2]
    context.user_data['current_user_id'] = user_id
    # Devam eden işlemler...
    # Kullanıcının borsa bilgilerini al
    exchanges = db_operation(
        "SELECT exchange FROM api_key WHERE user_id = ?",
        (user_id,),
        operation='select',
        fetch=True
    )

    if not exchanges:
        await query.answer("Bu kullanıcının kayıtlı API key'i bulunmamaktadır.")
        return State.ADMIN_MENU

    keyboard = [
        [InlineKeyboardButton(exchange, callback_data=f'select_exchange_{user_id}_{exchange}')]
        for exchange in exchanges
    ]
    keyboard.append([InlineKeyboardButton("Geri", callback_data='admin_menu')])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text("Lütfen bir borsa seçin:", reply_markup=reply_markup)

    return State.WAITING_FOR_EXCHANGE_SELECTION


async def assign_channel_to_user(update: Update, context: CallbackContext) -> int:
    query = update.callback_query   
    await query.answer()

    channel_id = query.data.split('_')[-1]   
    user_id = context.user_data.get('selected_user_id')
    exchange = context.user_data.get('selected_exchange')

    logger.info(f"Atama işlemi başlatılıyor: Kullanıcı ID: {user_id}, Borsa: {exchange}, Kanal ID: {channel_id}")

    if not user_id or not exchange:
        logger.error("Kullanıcı ID veya borsa bilgisi eksik")
        await query.edit_message_text("Bir hata oluştu. Lütfen işlemi baştan başlatın.")
        return State.ADMIN_MENU

    try:
        # Veritabanı işlemi
        query_str = """
        INSERT OR REPLACE INTO user_channel_assignments (user_id, exchange, channel_id)
        VALUES (?, ?, ?)
        """
        db_operation(query_str, (user_id, exchange, channel_id), operation='insert')

        logger.info(f"Kullanıcı {user_id} başarıyla {channel_id} kanalına atandı (Borsa: {exchange})")
        await query.edit_message_text(
            f"Kullanıcı başarıyla kanala atandı!\n"
            f"Kullanıcı ID: {user_id}\n"
            f"Kanal ID: {channel_id}\n"
            f"Borsa: {exchange}"
        )
        return State.ADMIN_MENU
    except Exception as e:
        logger.error(f"Kanal atama sırasında hata: {str(e)}")
        await query.edit_message_text("Kanal atama sırasında bir hata oluştu. Lütfen daha sonra tekrar deneyin.")
        return State.ADMIN_MENU


async def select_channel_for_user(update: Update, context: CallbackContext):
    query = update.callback_query   
    user_id = int(query.data.split('_')[2])   
    context.user_data['assign_user_id'] = user_id
    
    channels = db_operation("SELECT channel_id, channel_name FROM bot_channels", operation='select', fetch=True)

    keyboard = [
        [InlineKeyboardButton(f"{channel[1]}", callback_data=f'assign_channel_{channel[0]}')]
        for channel in channels
    ]
    keyboard.append([InlineKeyboardButton("Geri", callback_data='admin_menu')])

    reply_markup = InlineKeyboardMarkup(keyboard)   
    await update.effective_message.reply_text(
        "Kullanıcıyı hangi kanala atamak istiyorsunuz?",
        reply_markup=reply_markup
    )


async def confirm_user_channel_assignment(update: Update, context: CallbackContext) -> State:   
    query = update.callback_query   
    user_id = context.user_data['current_user_id']  # Kullanıcı ID'sini al
    channel_id = query.data.split('_')[2]   

    # Kullanıcıyı kanala atma işlemi
    db_operation("UPDATE bot_channels SET user_id = ? WHERE channel_id = ?",   
                 (user_id, channel_id), operation='update')

    await update.effective_message.reply_text(
        f"Kullanıcı (ID: {user_id}) başarıyla kanala atandı (Kanal ID: {channel_id}).")
    return State.ADMIN_MENU


# KULLANICI SÜRE AYARLARI BAŞLANGIÇ FONKSİYONU
async def handle_limit_user_time(update: Update, context: CallbackContext):
    _ = context
    query = update.callback_query   
    await query.answer()

    users_info = get_users_channel_info()
    keyboard = []
    for user in users_info:
        button_text = f"{user['username']} ({user['user_id']})"
        callback_data = f"show_user_info_{user['user_id']}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])

    keyboard.append([InlineKeyboardButton("Geri", callback_data='admin_menu')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Lütfen bir kullanıcı seçin:", reply_markup=reply_markup)   

    return State.WAITING_FOR_USER_CHANNEL_INFO


# ADMİN MENÜSÜNDE KULLANICI SÜRE AYARLARINI TIKLADIĞIMIZDA BENZERSİZ LİSTESİNİ GETİRİR
def get_users_channel_info():
    logger.info("get_users_channel_info fonksiyonu çağrıldı.")

    query = """
    SELECT user_id, username
    FROM user_channel_info
    """

    result = db_operation(query, operation='select', fetch=True)

    # Benzersiz user_id'leri saklamak için bir sözlük oluştur
    unique_users = {}

    if result:
        for row in result:
            user_id = row[0]
            username = row[1]

            # Eğer user_id daha önce eklenmemişse, ekleyin
            if user_id not in unique_users:
                unique_users[user_id] = username
                logger.info(f"Kullanıcı bilgisi alındı: user_id={user_id}, username={username}")

        logger.info(f"Toplam {len(unique_users)} benzersiz kullanıcı kanalı bilgisi alındı.")
    else:
        logger.warning("Kullanıcı kanalı bilgisi bulunamadı.")
    return [{'user_id': user_id, 'username': username} for user_id, username in unique_users.items()]


def get_user_channel_info(user_id):
    logger.info(f"get_user_channel_info fonksiyonu çağrıldı. Kullanıcı ID: {user_id}")
    query = """
    SELECT username, admin_level, exchange, channel_name, start_date, end_date, aktif_pasif
    FROM user_channel_info
    WHERE user_id = ?
    """
    result = db_operation(query, (user_id,), operation='select', fetch=True)
    if result:
        logger.info(f"Kullanıcı ID: {user_id} için kanal bilgisi alındı: {result}")
    else:
        logger.warning(f"Kullanıcı ID: {user_id} için kanal bilgisi bulunamadı.")
    return result


async def show_user_channel_info(update: Update, context: CallbackContext):
    _ = context
    query = update.callback_query   
    await query.answer()
    user_id = query.data.split('_')[3]   

    user_info = get_user_channel_details(user_id)
    if not user_info:
        await query.edit_message_text("Bu kullanıcı için kayıt bulunamadı.")
        return State.END

    messages, keyboards = format_user_channel_info(user_info, user_id)

    # Her kayıt için ayrı bir mesaj gönder
    for message, keyboard in zip(messages, keyboards):
        try:
            await update.effective_message.reply_text(   
                text=message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Mesaj gönderme hatası: {e}")
            # Hata durumunda parse_mode olmadan gönder
            await update.effective_message.reply_text(   
                text=message,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    return State.WAITING_USER_ACTION

# Admin menüsünde kullanıcı süre ayarlarına tıkladıktan sonra
# gelen listede seçilen kullanıcının kanal bilgilerini getirir
def get_user_channel_details(user_id):
    try:
        query = """
        SELECT 
            id,
            username, 
            user_id, 
            exchange, 
            channel_name, 
            start_date, 
            end_date, 
            admin_level, 
            aktif_pasif,
            super_admin_pasif
        FROM user_channel_info 
        WHERE user_id = ?
        ORDER BY id ASC
        """

        params = (int(user_id),)
        results = db_operation(query, params, operation='select', fetch=True, fetch_all=True)

        logger.info(f"get_user_channel_details sonucu: {results}")

        if not results:
            logger.warning(f"Kullanıcı ID {user_id} için hiç kayıt bulunamadı")

        return results

    except Exception as e:
        logger.error(f"Kanal detayları alınırken hata: {e}", exc_info=True)
        return []


def process_user_channels(user_id):
    """
    Kullanıcı kanallarını işleme fonksiyonu
    """
    channels = get_user_channel_details(user_id)

    if not channels:
        logger.warning(f"Kullanıcı {user_id} için kanal bulunamadı")
        return None

    processed_channels = []
    for channel in channels:
        # Kanal bilgilerini işleme
        processed_channel = {
            'username': channel[0],
            'user_id': channel[1],
            'exchange': channel[2],
            'channel_name': channel[3],
            'start_date': channel[4],
            'end_date': channel[5],
            'admin_level': channel[6],
            'status': channel[7]
        }
        processed_channels.append(processed_channel)

    return processed_channels


def escape_markdown(text: str) -> str:
    """
    Markdown'da özel karakterleri escape eden fonksiyon

    Args:
        text (str): Escape edilecek metin

    Returns:
        str: Escape edilmiş metin
    """
    if not isinstance(text, str):
        text = str(text)

    escape_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in escape_chars:
        text = text.replace(char, f'\\{char}')
    return text


def format_user_channel_info(user_info: List[Tuple], user_id: str):
    messages = []
    keyboards = []

    for user in user_info:
        try:
            # ARTIK id ilk kolonda
            (row_id, name, uid, platform, channel_name, start_date,
             end_date, admin_level, aktif_pasif, super_admin_pasif) = user

            # Tarih hesapları (mevcut mantığını korudum)
            if not end_date or str(end_date).strip() == '':
                start_datetime_naive = datetime.strptime(start_date, '%Y-%m-%d %H:%M:%S')
                end_datetime = start_datetime_naive + timedelta(days=30)
                end_date = end_datetime.strftime('%Y-%m-%d %H:%M:%S')
            else:
                end_datetime = datetime.strptime(end_date, '%Y-%m-%d %H:%M:%S')

            end_datetime_aware = pytz.utc.localize(end_datetime)
            now_aware = datetime.now(pytz.utc)
            remaining_days = max(0, (end_datetime_aware - now_aware).days)

            status_icon = '🔴' if str(aktif_pasif).lower() == 'pasif' else '🟢'
            status_color = 'Pasif' if str(aktif_pasif).lower() == 'pasif' else 'Aktif'

            platform_icons = {
                'binance': '🔶',
                'bitget': '🔷',
                'bybit': '🟣',
                'mexc': '🟠',
                'okx': '🔵',
            }
            platform_icon = platform_icons.get(str(platform).lower(), '💹')

            message = f"""
👤 *Kullanıcı Kanal Detayları*
{platform_icon} Platform: *{escape_markdown(platform)}*
📢 Kanal Adı: *{escape_markdown(channel_name)}*
👤 İsim: {escape_markdown(name)}
📅 Başlangıç: `{start_date}`
⏰ Bitiş: `{end_date}`
🕒 Kalan Gün: *{remaining_days}*
{status_icon} Durum: *{status_color}*
🆔 Kayıt ID: `{row_id}`
""".strip()

            # ✅ idx yerine row_id kullanıyoruz
            keyboard = [
                [
                    InlineKeyboardButton("⏳ Süre Yönet", callback_data=f"add_time_{user_id}_{row_id}"),
                    InlineKeyboardButton("⏹️ Kullanımı Sonlandır", callback_data=f"end_usage_{user_id}_{row_id}")
                ],
                [
                    InlineKeyboardButton("❌ Kanalı Sil", callback_data=f"delete_user_channel_{user_id}_{row_id}")
                ],
                [
                    InlineKeyboardButton("🏠 Admin Menü", callback_data="back_to_admin_menu")
                ]
            ]

            messages.append(message)
            keyboards.append(keyboard)

        except Exception as e:
            logger.error(f"Kullanıcı bilgileri işlenirken hata: {user} - {e}", exc_info=True)

    return messages, keyboards


async def process_user_delete_channel(update: Update, context: CallbackContext):
    _ = context

    query = update.callback_query   
    await query.answer()

    def parse_callback_data(callback_data):
        """Callback verisini güvenli bir şekilde parse et"""
        try:
            parts = callback_data.split('_')

            # Detaylı callback formatı kontrolü
            if (len(parts) >= 4 and
                    parts[0] == 'delete' and
                    parts[1] == 'user' and
                    parts[2] == 'channel' and
                    parts[3].isdigit()):
                return {
                    'user_id': int(parts[3]),
                    'record_idx': int(parts[4]) if len(parts) > 4 else 4,
                    'is_valid': True
                }

            return {'is_valid': False}

        except Exception as error:
            logger.error(f"Callback parse hatası: {error}")
            return {'is_valid': False}

    try:
        # Callback verisini parse et   
        parsed_data = parse_callback_data(query.data)
        logger.info(f"Delete Channel Callback Verisi Parse: {parsed_data}")

        # Geçersiz callback kontrolü
        if not parsed_data['is_valid']:
            logger.error(f"Geçersiz callback formatı: {query.data}")
            await query.edit_message_text("❌ Geçersiz işlem")
            return State.ADMIN_MENU

        user_id = parsed_data['user_id']
        record_idx = parsed_data['record_idx']

        logger.info(f"Kanal silme işlemi - User ID: {user_id}, Record Index: {record_idx}")

        # Silinecek kanalı bul - Gelişmiş sorgu
        user_channel_query = """
        SELECT 
            id, 
            channel_id, 
            channel_name, 
            exchange,
            start_date,
            end_date
        FROM user_channel_info 
        WHERE user_id = ? 
        ORDER BY id 
        LIMIT 1 OFFSET ?
        """

        channel_to_delete = db_operation(
            user_channel_query,
            (user_id, record_idx),
            operation='select',
            fetch=True,
            fetch_all=False
        )

        if not channel_to_delete:
            logger.warning(f"Silinecek kanal bulunamadı: user_id={user_id}, index={record_idx}")
            await query.edit_message_text("❌ Kanal bulunamadı")
            return State.ADMIN_MENU

        # Gelişmiş silme işlemi
        delete_query = """
        DELETE FROM user_channel_info 
        WHERE id = ?
        """

        result = db_operation(
            delete_query,
            (channel_to_delete[0],),
            operation='delete'
        )

        if result is not None and result > 0:
            # Silme başarılı - Detaylı bilgilendirme
            channel_details = {
                'name': channel_to_delete[2] or "Bilinmeyen Kanal",
                'exchange': channel_to_delete[3] or "Bilinmeyen Borsa",
                'start_date': channel_to_delete[4],
                'end_date': channel_to_delete[5]
            }

            message = (
                f"✅ *Kanal Silme İşlemi Başarılı*\n\n"
                f"🗑️ Silinen Kanal: {escape_markdown(channel_details['name'])}\n"
                f"💹 Borsa: {escape_markdown(channel_details['exchange'])}\n"
                f"📅 Başlangıç Tarihi: {channel_details['start_date']}\n"
                f"📅 Bitiş Tarihi: {channel_details['end_date']}\n"
                f"👤 Kullanıcı ID: {user_id}"
            )

            # Geri ve Admin Menü butonları ekle
            keyboard = [
                [
                    InlineKeyboardButton("📋 Admin Menü", callback_data="admin_menu")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(
                message,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )

            # Gelişmiş log kaydı
            logger.info(
                "Kanal silindi",
                extra={
                    'user_id': user_id,
                    'channel_name': channel_details['name'],
                    'exchange': channel_details['exchange']
                }
            )
            return State.ADMIN_MENU

        else:
            logger.error(f"Kanal silme işlemi başarısız: user_id={user_id}")
            await query.edit_message_text("❌ Kanal silme işlemi başarısız")
            return State.ADMIN_MENU

    except Exception as e:
        # Gelişmiş hata yakalama ve log
        logger.error(
            "Kanal silme işleminde genel hata",
            extra={
                'error': str(e),
                'callback_data': query.data
            },
            exc_info=True
        )
        await query.edit_message_text("❌ Bir hata oluştu")
        return State.ADMIN_MENU


# admin panelinde süre ekle süre çıkart buton tarafından çağırılan fonksiyonu
async def add_time_to_user(update: Update, context: CallbackContext):
    query = update.callback_query    
    await query.answer()

    # callback: add_time_{user_id}_{row_id}
    user_id_raw, row_id_raw = query.data.split('_')[2:]    

    try:
        user_id = int(user_id_raw)
        row_id = int(row_id_raw)
    except ValueError:
        await query.edit_message_text("❌ Geçersiz seçim.")
        return State.ADMIN_MENU

    context.user_data['current_user_id'] = user_id
    context.user_data['current_row_id'] = row_id

    await query.edit_message_text("Lütfen eklemek/eksiltmek istediğiniz süreyi gün olarak girin (ör: 30 veya -100):")
    return State.WAITING_FOR_TIME_INPUT

def _permission_denied_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Kayıtları Yenile", callback_data=f"show_user_info_{user_id}")],
        [InlineKeyboardButton("👤 Kullanıcı Listesi", callback_data="limit_user_time")],
        [InlineKeyboardButton("🏠 Admin Menü", callback_data="admin_menu")],
    ])


async def process_time_input(update: Update, context: CallbackContext):
    try:
        # 0) Input
        try:
            days_to_add = int(update.message.text.strip())
            logging.info(f"Giriş başarılı. Gün değişimi: {days_to_add}")
        except Exception:
            await update.message.reply_text(
                "Geçersiz giriş. Lütfen sadece sayı girin (örn: 30 veya -100)."
            )
            return State.WAITING_FOR_TIME_INPUT

        operator_user_id = int(update.effective_user.id)
        user_id = int(context.user_data["current_user_id"])
        row_id = int(context.user_data["current_row_id"])

        logging.info(f"İşlem Yapan Kullanıcı ID: {operator_user_id}")
        logging.info(f"Hedef Kullanıcı ID: {user_id}, Kayıt ID: {row_id}")

        # 1) Hedef kaydı id ile çek
        check_query = """
        SELECT end_date, aktif_pasif, admin_level, super_admin_pasif,
               username, channel_name, channel_username, exchange
        FROM user_channel_info
        WHERE id = ? AND user_id = ?
        """
        result = db_operation(check_query, (row_id, user_id), operation="select", fetch=True)

        if not result:
            await update.message.reply_text("Kayıt bulunamadı (muhtemelen silinmiş).")
            return State.WAITING_USER_ACTION

        (
            current_end_date_raw,
            current_status,
            admin_level,
            super_admin_pasif,
            username,
            channel_name,
            channel_username,
            exchange,
        ) = result[0]

        # 2) parse_date kesin datetime olmalı
        parsed: Any = parse_date(current_end_date_raw)
        if not isinstance(parsed, datetime):
            raise TypeError(
                f"parse_date() datetime döndürmeli, gelen tip: {type(parsed)} -> {parsed!r}"
            )
        current_end_date_dt: datetime = parsed

        # 3) Operatör yetkisi
        operator_check_query = "SELECT level FROM admin_users WHERE user_id = ?"
        operator_result = db_operation(
            operator_check_query, (operator_user_id,), operation="select", fetch=True
        )

        if not operator_result:
            # Burada da butonlu, akışı kilitlemeyen mesaj verelim
            await update.message.reply_text(
                "⛔ Yetki bulunamadı. Admin rolünüz tanımlı değil.",
                reply_markup=_permission_denied_keyboard(user_id),
            )
            return State.WAITING_USER_ACTION

        operator_admin_level = operator_result[0][0] if operator_result[0][0] is not None else 99
        logging.info(f"İşlem Yapan Kullanıcı Admin Seviyesi: {operator_admin_level}")

        # --- Yetki Kontrolleri (HTML ile, parse hatası olmaz) ---
        if operator_admin_level != 0:
            role_map = {0: "Süper Admin", 1: "Manager", 2: "Admin", 99: "Kullanıcı"}
            op_role = role_map.get(int(operator_admin_level), f"Seviye {operator_admin_level}")
            target_role = role_map.get(int(admin_level) if admin_level is not None else 99, f"Seviye {admin_level}")

            # 3.1) Süper admin kilidi
            if int(super_admin_pasif) == 0:
                detailed_html = (
                    "<b>⛔ İşlem Engellendi: Süper Admin Kilidi</b>\n\n"
                    "Bu kanal kaydı, süper admin onayı olmadan değiştirilemeyecek şekilde kilitlenmiş.\n\n"
                    f"• <b>Sizin rolünüz:</b> <code>{html.escape(str(op_role))}</code> "
                    f"(level={html.escape(str(operator_admin_level))})\n"
                    f"• <b>Kaydın rol seviyesi:</b> <code>{html.escape(str(target_role))}</code> "
                    f"(admin_level={html.escape(str(admin_level))})\n"
                    f"• <b>Kilit durumu (super_admin_pasif):</b> <code>{html.escape(str(super_admin_pasif))}</code>\n\n"
                    "<b>📌 Neden engellendi?</b>\n"
                    "Bu kayıtta <code>super_admin_pasif=0</code> olduğu için, süper admin dışındaki roller süre değişikliği yapamaz.\n\n"
                    "<b>📌 Ne yapmalısınız?</b>\n"
                    "1) Süper admin bu kayıt için kilidi açmalı (<code>super_admin_pasif=1</code>).\n"
                    "2) Sonrasında süre ekleme/eksiltme işlemini tekrar deneyebilirsiniz."
                )
                await update.message.reply_text(
                    detailed_html,
                    parse_mode=ParseMode.HTML,
                    reply_markup=_permission_denied_keyboard(user_id),
                )
                return State.WAITING_USER_ACTION

            # 3.2) Seviye kuralı
            if admin_level is not None and operator_admin_level > admin_level:
                detailed_html = (
                    "<b>⛔ İşlem Engellendi: Yetki Seviyesi Yetersiz</b>\n\n"
                    "Bu kayıt, sizden daha yüksek bir admin seviyesine ait olduğu için değiştirilemiyor.\n\n"
                    f"• <b>Sizin rolünüz:</b> <code>{html.escape(str(op_role))}</code> "
                    f"(level={html.escape(str(operator_admin_level))})\n"
                    f"• <b>Kaydın seviyesi:</b> <code>{html.escape(str(target_role))}</code> "
                    f"(admin_level={html.escape(str(admin_level))})\n\n"
                    "<b>📌 Kural:</b> Daha düşük yetkili bir admin, daha yüksek seviyeli kayıtların sürelerini değiştiremez."
                )
                await update.message.reply_text(
                    detailed_html,
                    parse_mode=ParseMode.HTML,
                    reply_markup=_permission_denied_keyboard(user_id),
                )
                return State.WAITING_USER_ACTION

        # 4) Gün ekle/çıkar (negatif de çalışır)
        new_end_date_dt: datetime = current_end_date_dt + timedelta(days=days_to_add)

        now_dt: datetime = datetime.now()
        new_status: str = "Pasif" if new_end_date_dt <= now_dt else "Aktif"
        new_end_date_str: str = format_date(new_end_date_dt)

        # 5) Güncelle (id ile)
        update_query = """
        UPDATE user_channel_info
        SET end_date = ?,
            aktif_pasif = ?,
            super_admin_pasif = CASE
                WHEN ? = 'Pasif' THEN 0
                WHEN ? = 0 AND ? = 'Aktif' THEN 1
                ELSE super_admin_pasif
            END,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND user_id = ?
        """

        db_operation(
            update_query,
            (new_end_date_str, new_status, new_status, operator_admin_level, new_status, row_id, user_id),
            operation="update",
        )

        # 6) Kullanıcıya mesaj (HTML ile güvenli)
        try:
            remaining_days = max(0, (new_end_date_dt - now_dt).days)
            user_message_html = (
                "<b>🔔 Süre Güncelleme Bildirimi</b>\n\n"
                f"👤 <b>Kullanıcı:</b> {html.escape(str(username))}\n"
                f"📢 <b>Kanal:</b> {html.escape(str(channel_name))} ({html.escape(str(channel_username))})\n"
                f"💹 <b>Borsa:</b> {html.escape(str(exchange))}\n\n"
                f"⏰ <b>Önceki Bitiş:</b> <code>{html.escape(format_date(current_end_date_dt))}</code>\n"
                f"📅 <b>Yeni Bitiş:</b> <code>{html.escape(new_end_date_str)}</code>\n"
                f"🕒 <b>Değişim:</b> <code>{html.escape(str(days_to_add))}</code> gün\n"
                f"⏳ <b>Kalan Gün:</b> <code>{html.escape(str(remaining_days))}</code>\n"
                f"✅ <b>Yeni Durum:</b> <b>{html.escape(new_status)}</b>"
            )
            await context.bot.send_message(
                chat_id=user_id,
                text=user_message_html,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logging.error(f"Kullanıcıya mesaj gönderme hatası: {e}")

        await update.message.reply_text("✅ Süre güncellendi.")

        # 7) Güncel listeyi tekrar göster
        user_info = get_user_channel_details(user_id)
        messages, keyboards = format_user_channel_info(user_info, str(user_id))

        for message, keyboard in zip(messages, keyboards):
            # format_user_channel_info Markdown üretiyor; bazen patlayabilir.
            # Bu yüzden güvenli gönderim: önce Markdown dene, patlarsa düz metin gönder.
            try:
                await update.message.reply_text(
                    text=message,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown",
                )
            except Exception:
                await update.message.reply_text(
                    text=message,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )

        return State.WAITING_USER_ACTION

    except Exception as e:
        logging.error(f"process_time_input hata: {e}", exc_info=True)
        await update.message.reply_text(
            "Bir hata oluştu. Lütfen daha sonra tekrar deneyin.",
            reply_markup=_permission_denied_keyboard(int(context.user_data.get("current_user_id", 0)) or update.effective_user.id),
        )
        return State.WAITING_USER_ACTION


async def end_user_usage(update: Update, context: CallbackContext):
    query = update.callback_query    
    await query.answer()    

    # callback: end_usage_{user_id}_{record_idx}
    user_id_raw, record_idx_raw = query.data.split('_')[2:]    

    try:
        user_id = int(user_id_raw)
        record_idx = int(record_idx_raw)
    except ValueError:
        logger.error(f"end_user_usage: Geçersiz parametreler: user_id={user_id_raw}, idx={record_idx_raw}")
        await query.edit_message_text("❌ Geçersiz işlem parametresi.")
        return State.ADMIN_MENU

    try:
        # 1) Seçilen kaydın id'sini bul (PostgreSQL: rowid yok)
        row = db_operation(
            """
            SELECT id
            FROM user_channel_info
            WHERE user_id = ?
            ORDER BY id
            LIMIT 1 OFFSET ?
            """,
            (user_id, record_idx),
            operation='select',
            fetch=True,
            fetch_all=False
        )

        if not row:
            logger.warning(f"end_user_usage: Kayıt bulunamadı. user_id={user_id}, idx={record_idx}")
            await query.edit_message_text("❌ Kayıt bulunamadı.")
            return State.ADMIN_MENU

        row_id = row[0]  # fetch_all=False -> tuple döner: (id,)

        # 2) Kaydı pasife çek + bitiş tarihini şimdi yap
        now_str = format_date(datetime.now())
        updated = db_operation(
            """
            UPDATE user_channel_info
            SET aktif_pasif = 'pasif',
                end_date = ?,
                super_admin_pasif = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (now_str, row_id),
            operation='update'
        )

        if updated is None:
            await query.edit_message_text("❌ Veritabanı güncellemesi başarısız oldu.")
            return State.ADMIN_MENU

        # 3) Kullanıcıya bildirim gönder
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="Kanal kullanım süreniz sonlandırıldı. Detaylar için destek ekibiyle iletişime geçebilirsiniz."
            )
        except Exception as e:
            logger.error(f"Kullanıcıya bildirim gönderme hatası: {e}")

        # 4) Admin tarafında kullanıcı bilgilerini tekrar göster
        user_info = get_user_channel_details(user_id)
        messages, keyboards = format_user_channel_info(user_info, str(user_id))

        for message, keyboard in zip(messages, keyboards):
            await update.effective_message.reply_text(    
                text=message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )

        return State.WAITING_USER_ACTION

    except Exception as e:
        logger.error(f"Kullanım sonlandırma hatası: {e}", exc_info=True)
        await query.edit_message_text("İşlem sırasında bir hata oluştu.")
        return State.ADMIN_MENU


# KULLANICI SÜRE AYARLARI BİTİŞ FONKSİYONU
async def back_to_admin_menu(update: Update, context: CallbackContext) -> State:
    logger.info("Admin menüye dönülüyor.")
    query = update.callback_query   

    try:
        # Eğer bir callback query varsa, cevap ver
        if query:
            await query.answer()

            # Mevcut mesajı admin menüsü olarak güncelle
            return await admin_menu(update, context)

        # Eğer normal bir update ise doğrudan admin menüsüne dön
        return await admin_menu(update, context)

    except Exception as e:
        logger.error(f"Admin menüye dönülürken hata: {str(e)}")
        return State.ADMIN_MENU


def set_user_limitation(user_id: int, start_time: str, end_time: str):
    query = """
    INSERT OR REPLACE INTO user_limitations (user_id, start_date, end_date) 
    VALUES (?, ?, ?)
    """
    db_operation(query, (user_id, start_time, end_time), operation='insert')


async def select_user_for_limitation(update: Update, context: CallbackContext):
    query = update.callback_query   
    await query.answer()

    user_id = int(query.data.split('_')[-1])   
    context.user_data['selected_user_id'] = user_id

    await update.effective_message.reply_text(
        f"Kullanıcı {user_id} için sınırlama süresini gün saat dakika olarak girin "
        f"(örnek: 30 5 30):"
    )
    return State.WAITING_DURATION


def get_users_from_api_key_table():
    query = """
    SELECT DISTINCT user_id, username
    FROM api_key
    """
    result = db_operation(query, operation='select', fetch=True)
    if result:
        return [{'id': row[0], 'name': row[1]} for row in result]
    return []


async def process_duration(update: Update, context: CallbackContext) -> State:
    try:
        days, hours, minutes = map(int, update.message.text.split())   
        duration = timedelta(days=days, hours=hours, minutes=minutes)
        start_time = datetime.now()
        end_time = start_time + duration

        user_id = context.user_data['selected_user_id']

        set_user_limitation(user_id, start_time.strftime('%Y-%m-%d %H:%M:%S'),
                            end_time.strftime('%Y-%m-%d %H:%M:%S'))

        await update.message.reply_text(   
            f"Kullanıcı {user_id} için yeni sınırlama ayarlandı:\n"
            f"Serbest kullanım başlangıç: {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Serbest kullanım bitiş: {end_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Bu tarihten sonra kullanıcı sınırlandırılacak ve "
            f"yeni bir tarih girilene kadar sınırlı kalacak."
        )

        del context.user_data['selected_user_id']
        return State.END
    except ValueError:
        await update.message.reply_text(   
            "Geçersiz format. Lütfen 'gün saat dakika' şeklinde girin (örnek: 30 5 30)."
        )
        return State.WAITING_DURATION


async def set_user_time_limit(update: Update, context: CallbackContext):
    query = update.callback_query
    user_id = int(query.data.split('_')[2])   
    context.user_data['limit_user_id'] = user_id

    await update.effective_message.reply_text(   
        "Kullanım süresini 'YYYY-MM-DD YYYY-MM-DD' formatında girin (başlangıç ve bitiş tarihi):")
    return State.WAITING_USER_LIMITATION


async def confirm_user_time_limit(update: Update, context: CallbackContext):
    date_range = update.message.text.split()
    if len(date_range) != 2:
        await update.message.reply_text(   
            "Geçersiz format. Lütfen 'YYYY-MM-DD YYYY-MM-DD' şeklinde girin."
        )
        return State.WAITING_USER_LIMITATION
    start_date, end_date = date_range
    user_id = context.user_data['limit_user_id']

    try:
        start_date = datetime.strptime(start_date, "%Y-%m-%d")
        end_date = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        await update.message.reply_text(   
            "Geçersiz tarih formatı. Lütfen 'YYYY-MM-DD YYYY-MM-DD' şeklinde girin."
        )
        return State.WAITING_USER_LIMITATION

    query = """
        INSERT OR REPLACE INTO user_limitations (user_id, start_date, end_date) 
        VALUES (?, ?, ?)
    """
    db_operation(
        query,
        (user_id, start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")),
        operation='insert'
    )

    response_text = (
        f"Kullanıcı (ID: {user_id}) için süre sınırlaması başarıyla ayarlandı.\n"
        f"Başlangıç: {start_date.strftime('%Y-%m-%d')}\n"
        f"Bitiş: {end_date.strftime('%Y-%m-%d')}"
    )
    await update.message.reply_text(response_text)   
    return State.END


async def send_message_menu(update: Update, context: CallbackContext) -> State:
    keyboard = [
        [InlineKeyboardButton("Yeni Mesaj Gönder", callback_data='new_message')],
        [InlineKeyboardButton("Ana Menüye Dön", callback_data='main_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    # Örnek olarak context'i kullanma
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Mesaj menüsü açıldı.")

    await update.message.reply_text("Lütfen bir seçenek seçin:", reply_markup=reply_markup)   
    return State.SEND_MESSAGE_MENU


async def send_message_to_user(bot, user_id, message):
    try:
        await bot.send_message(chat_id=user_id, text=message)
    except Exception as e:
        logger.error(f"Mesaj gönderilirken hata oluştu: {e}")


async def cancel(update: Update, context: CallbackContext) -> State:
    _= context
    
    """Conversation'ı sonlandırır ve kullanıcıya bilgi verir."""
    user = update.message.from_user
    logger.info("User %s canceled the conversation.", user.first_name)   
    await update.message.reply_text(
        'İşlem iptal edildi. Ana menüye dönülüyor.'
    )
    return State.END


async def set_user_role(update: Update, context: CallbackContext) -> State:
    """Kullanıcının rolünü veritabanında günceller."""
    query = update.callback_query   
    await query.answer()

    # Callback data'dan user_id ve rol adını al
    parts = query.data.split('_')   
    if len(parts) < 4:  # Güvenlik kontrolü (set_role_{user_id}_{ROLE_NAME})
        logger.error(f"Geçersiz rol atama callback data: {query.data}")
        await query.edit_message_text("❌ Geçersiz rol atama işlemi.")
        return await add_admin(update, context)

    user_id_str = parts[2]
    role_name = parts[3]
    user_id = int(user_id_str)

    try:
        # String rol adını UserRole enum'una çevir
        role_to_set = AdminLevel[role_name]

        # Veritabanında admin seviyesini güncelle
        await set_admin_status(user_id, role_to_set.value)

        # DÜZELTME: get_user_info asenkron bir fonksiyon olduğu için 'await' ile çağrılmalı.
        user_info = await get_user_info(user_id)
        username = user_info.get('username', f"ID: {user_id}") if user_info else f"ID: {user_id}"

        await query.edit_message_text(   
            f"✅ Başarılı!\nKullanıcı: `{username}`\nYeni Rol: `{role_name}`",
            parse_mode=ParseMode.MARKDOWN
        )

        # Admin listesini yenilemek için geri dön
        return await add_admin(update, context)

    except (KeyError, ValueError) as e:
        logger.error(f"Rol atama hatası (geçersiz rol adı veya değer): {e}")
        await query.edit_message_text("❌ Geçersiz rol seçimi.")
        return await add_admin(update, context)

    except Exception as e:
        logger.error(f"Rol atama sırasında DB hatası: {e}", exc_info=True)
        await query.edit_message_text("❌ Rol atanırken bir hata oluştu.")
        return await add_admin(update, context)


async def error_handler(update: object, context: CallbackContext) -> None:
    logger.error(f"Bir güncelleme işlenirken hata oluştu: {context.error}")

    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = ''.join(tb_list)
    logger.error(f"Hata detayları:\n{tb_string}")

    if update and hasattr(update, 'effective_message'):
        await update.effective_message.reply_text("Üzgünüm, bir hata oluştu. Lütfen daha sonra tekrar deneyin.")
