# strategies/alarm_system/handlers.py

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, error as telegram_error
from telegram.ext import CallbackContext
from config.constants import State
from collections.abc import Iterable
import math
from typing import TYPE_CHECKING, List, Any, Optional, Dict
from config_service import ConfigService
from datetime import datetime, timezone, timedelta
import os
from telegram import Message
from telegram.constants import ParseMode
import asyncio
import numpy as np

# TYPE_CHECKING bloğunda kullanılacak import'lar
if TYPE_CHECKING:
    from strategies.alarm_strateji import OlimposStrategy
    from StrategyAdaptiveTuner import StrategyAdaptiveTuner, AlarmRaporManager
    from analytics.performance_report import PerformanceReport
else:
    # Runtime'da bu türler gerekmiyorsa boş tanımlamalar yapılabilir
    AlarmMessageRef = Dict[str, int]
    SignalData = Dict[str, Any]
    OlimposStrategy = Any

logger = logging.getLogger(__name__)

# DÜZELTME: tf_to_tr_label fonksiyonu, referans hatasını çözmek için buraya taşındı.
TF_TR_LABELS = {
    '1m':'1 Dakikalık', '5m':'5 Dakikalık', '15m':'15 Dakikalık',
    '30m':'30 Dakikalık', '1h':'1 Saatlik', '4h':'4 Saatlik', '1d':'Günlük'
}

# Strateji otomatik tarama sırası (start_strategy_setup ile kullanılacak)
STRATEGY_TF_SEQUENCE_DEFAULT = ["5m", "15m", "1h", "4h", "1d"]
BOOTSCAN_MAX_TOTAL_DEFAULT = 15  # bu butondan başlatılan taramalarda global max alarm


def tf_to_tr_label(tf: str) -> str:
    """Zaman aralığı kodunu Türkçe etiketine çevirir."""
    return TF_TR_LABELS.get(tf, tf)


import time


def build_progress_bar(pct: float, width: int = 10) -> str:
    pct = max(0.0, min(1.0, float(pct)))
    filled = int(round(width * pct))
    empty = width - filled
    return f"[{'█' * filled}{'░' * empty}] {int(pct * 100)}%"


def format_dual_progress(v1_pct: float | None, v2_pct: float | None,
        v1_label: str = "V1", v2_label: str = "V2") -> str:
    lines = []
    if v1_pct is not None:
        lines.append(f"{v1_label}: {build_progress_bar(v1_pct)}")
    if v2_pct is not None:
        lines.append(f"{v2_label}: {build_progress_bar(v2_pct)}")
    return "\n".join(lines)

async def _run_bootstrap_multi_tf_scan(
    cls: 'OlimposStrategy',
    chat_id: int,
    message_id: int,
    user_id: int,
    context: CallbackContext,
    tf_seq: list[str],
):
    bot = context.bot

    # global cap ayarları (add_alarm_debug bunu okuyacak)
    context.user_data["boot_scan"] = True
    context.user_data["boot_scan_added_count"] = 0
    context.user_data["boot_scan_max_total"] = int(ConfigService.get("scans.boot_scan.max_total", BOOTSCAN_MAX_TOTAL_DEFAULT) or BOOTSCAN_MAX_TOTAL_DEFAULT)

    exchange_name = str(context.user_data.get("exchange", "Bilinmiyor")).upper()

    async def _edit(text: str):
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

    try:
        from strategies.alarm_system.scanning import do_ai_scan, _do_strategy_scan

        for tf in tf_seq:
            # global cap dolduysa kır
            if int(context.user_data.get("boot_scan_added_count", 0)) >= int(context.user_data.get("boot_scan_max_total", 0)):
                break

            await _edit(
                f"🚀 **Çoklu TF Tarama**\n"
                f"🏦 Borsa: `{exchange_name}`\n"
                f"⏰ TF: `{tf}`\n\n"
                f"1) 🤖 AI Coin (V1+V2)\n"
                f"2) 📐 Strateji (V1+V2)\n\n"
                f"📌 Global Limit: `{context.user_data['boot_scan_added_count']}/{context.user_data['boot_scan_max_total']}`\n"
                f"⏳ Başlatılıyor..."
            )

            # --- AI COIN: v1, v2 ---
            for sid in ("v1", "v2"):
                if int(context.user_data.get("boot_scan_added_count", 0)) >= int(context.user_data.get("boot_scan_max_total", 0)):
                    break
                await do_ai_scan(cls, tf, sid, 100, chat_id, context, user_id, progress_callback=None)

            # --- STRATEGY SCAN: v1, v2 ---
            for sid in ("v1", "v2"):
                if int(context.user_data.get("boot_scan_added_count", 0)) >= int(context.user_data.get("boot_scan_max_total", 0)):
                    break
                await _do_strategy_scan(cls, tf, sid, 100, chat_id, context, user_id, progress_callback=None)

        await _edit(
            f"✅ **Çoklu TF Tarama Tamamlandı**\n"
            f"🏦 Borsa: `{exchange_name}`\n"
            f"📌 Kurulan Alarm: `{context.user_data.get('boot_scan_added_count', 0)}`\n\n"
            f"Aktif alarmlar listeleniyor..."
        )
        await asyncio.sleep(0.5)
        await show_active_alarms(cls, update=None, context=context, chat_id=chat_id, edit_message_id=message_id)

    except Exception as e:
        await _edit(f"❌ Tarama hatası: `{type(e).__name__}`")
        logger.error(f"[BOOTSCAN_ERR] {e}", exc_info=True)
    finally:
        # flag temizle
        context.user_data.pop("boot_scan", None)


class ProgressMessage:
    def __init__(self, bot, chat_id: int, title: str, throttle_sec: float = 1.0):
        self.bot = bot
        self.chat_id = chat_id
        self.title = title
        self.message_id: int | None = None
        self.last_update = 0.0
        self.throttle_sec = throttle_sec

    async def init(self, subtitle: str = ""):
        text = f"{self.title}{('\n' + subtitle) if subtitle else ''}"
        msg = await self.bot.send_message(chat_id=self.chat_id, text=text)
        self.message_id = msg.message_id

    async def update(self, body: str):
        if not self.message_id:
            return
        now = time.time()
        if now - self.last_update < self.throttle_sec:
            return
        self.last_update = now
        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.message_id,
                text=f"{self.title}\n{body}"
            )
        except Exception as e:
            logger.debug(f"[PROGRESS_EDIT] {e}")

    async def finalize_and_delete(self):
        if not self.message_id:
            return
        try:
            await self.bot.delete_message(chat_id=self.chat_id, message_id=self.message_id)
        except Exception as e:
            logger.debug(f"[PROGRESS_DELETE] {e}")
        self.message_id = None


async def _send_long_text(bot, chat_id: int, text: str, reply_markup=None, chunk_size: int = 4000,
        parse_mode: Optional[str] = None):
    """Uzun metinleri Telegram limitlerine uygun şekilde böler ve gönderir."""
    if len(text) <= 4096:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        return

    parts = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]

    for i, part in enumerate(parts):
        current_reply_markup = reply_markup if i == len(parts) - 1 else None
        await bot.send_message(chat_id=chat_id, text=part, reply_markup=current_reply_markup, parse_mode=parse_mode)


# --- YENİ: Hatalı lambda'ları düzeltmek için yardımcı async fonksiyonlar ---
async def _handle_ai_tf_selection(update: Update, context: CallbackContext, tf: str):
    """AI taraması için TF seçildiğinde user_data'yı günceller ve strateji menüsünü gösterir."""
    q = update.callback_query
    if q:
        try: await q.answer()
        except:
            pass
    logger.info(f"[AI_TF_SELECT] tf={tf}")
    context.user_data['ai_scan_tf'] = tf
    return await show_strategy_selection_menu(update, context, 'ai')


# --- YENİ: Hatalı lambda'ları düzeltmek için yardımcı async fonksiyonlar ---
async def _handle_strat_tf_selection(update: Update, context: CallbackContext, tf: str):
    """Strateji taraması için TF seçildiğinde user_data'yı günceller ve strateji menüsünü gösterir."""
    if context.user_data:
        context.user_data['strat_scan_tf'] = tf
    return await show_strategy_selection_menu(update, context, 'strat')


async def _handle_tfth_kind_selection(cls, update: Update, context: CallbackContext, tf: str):
    """TF Eşikleri menüsü için TF seçildiğinde user_data'yı günceller ve tür menüsünü gösterir."""
    if context.user_data:
        context.user_data['tfth_selected_tf'] = tf
    return await show_tf_thresholds_kind(cls, update, context, tf)


async def _handle_symbol_selection(cls, update: Update, context: CallbackContext, symbol: str):
    """Sembol seçildiğinde user_data'yı günceller ve zaman aralığı menüsünü gösterir."""
    if context.user_data:
        context.user_data['selected_symbol'] = symbol
    # select_timeframe bir coroutine olduğu için await ile çağrılmalıdır.
    return await select_timeframe(cls, update, context)


# 1. _handle_timeframe_selection GÜNCELLEME
async def _handle_timeframe_selection(cls, u: Update, c: CallbackContext, data: str, strategy_id: Optional[str]):
    """
    Zaman aralığı seçildiğinde user_data'yı günceller ve STRATEJİ SEÇİM MENÜSÜNÜ gösterir.
    """
    timeframe = data.replace('timeframe_', '')
    if c.user_data:
        c.user_data['selected_timeframe'] = timeframe

    # DÜZELTME: Alarmı hemen eklemek yerine strateji soruyoruz
    return await show_manual_strategy_menu(cls, u, c, timeframe)


# 2. YENİ FONKSİYON: Strateji Seçim Menüsü
async def show_manual_strategy_menu(cls, update: Update, context: CallbackContext, timeframe: str):
    """Manuel alarm için strateji seçim ekranı."""
    symbol_raw = context.user_data.get('selected_symbol', '?')
    symbol = cls.to_display_symbol(symbol_raw) if hasattr(cls, "to_display_symbol") else symbol_raw

    kb = [
        [InlineKeyboardButton("V1 Stratejisi", callback_data='manual_strat_v1')],
        [InlineKeyboardButton("V2 Stratejisi", callback_data='manual_strat_v2')],
        [InlineKeyboardButton("🔙 Geri", callback_data='back_to_timeframe_select')]
    ]

    text = (
        f"📊 **Sembol:** {symbol}\n"
        f"⏰ **Zaman:** {timeframe}\n\n"
        f"Hangi strateji ile sinyal üretilsin?"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.MARKDOWN)
    else:
        await context.bot.send_message(update.effective_chat.id, text, reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.MARKDOWN)

    return State.ALARM_SETUP


# 3. YENİ FONKSİYON: Strateji Seçimi ve Alarm Ekleme
async def _handle_manual_strat_selection(cls, u: Update, c: CallbackContext, data: str):
    """Strateji seçildikten sonra alarmı ekler."""
    strategy_id = data.replace('manual_strat_', '')

    symbol = c.user_data.get('selected_symbol')
    timeframe = c.user_data.get('selected_timeframe')
    user_id = u.effective_user.id if u.effective_user else None

    # Meta verisi (Manuel kaynaklı olduğunu belirtelim)
    meta = {'source':'manual', 'manual_add':True}

    # Alarmı Ekle
    result = await add_alarm_debug(cls, c, symbol, timeframe, strategy_id, user_id, source_meta=meta)

    if result:
        symbol_disp = cls.to_display_symbol(symbol) if hasattr(cls, "to_display_symbol") else symbol

        await u.callback_query.edit_message_text(
            f"✅ **Alarm Kuruldu!**\n\nSembol: {symbol_disp}\nStrateji: {strategy_id.upper()}\nZaman: {timeframe}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Alarm Menüsü", callback_data='back_to_alarm_menu')]]),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await u.callback_query.edit_message_text(
            "❌ Alarm kurulamadı (Limit veya Geçersiz Sembol).",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri", callback_data='back_to_alarm_menu')]])
        )
    return State.ALARM_SETUP


PARAM_PATHS_BY_GROUP = {
    'core':[
        'strategy.min_volume_ratio',
        'strategy.momentum_threshold',
        'strategy.momentum_period',
        'strategy.atr_multiplier'],
    'ai':['strategy.ai.min_confidence', 'strategy.ai.min_volume_ratio', 'strategy.ai.boost_add'],
    'mt':['strategy.multi_timeframe_confirmation.enabled', 'strategy.multi_timeframe_confirmation.require_alignment',
        'strategy.multi_timeframe_confirmation.min_trend_strength_pct'],

    # ✅ YENİ MİMARİ: legacy 'scans.futures_min_volume_usdt' ve 'scans.ai_global.*' kaldırıldı
    # Not: Bu menü genel param edit menüsü; TF bazlı ayarların edit'i zaten TFTH menüsünde yapılıyor.
    # Burada yalnız genel scan ayarları gösteriyoruz.
    'scan':[
        'scans.stale_alarm_minutes'
    ],

    'risk':['strategy.stop_loss', 'strategy.take_profit1', 'strategy.take_profit2', 'strategy.take_profit3',
        'strategy.take_profit4', 'strategy.take_profit5']
}



async def show_performance_menu(cls, update: Update, context: CallbackContext):
    """Performans analizi için alt menüyü gösterir."""
    _ = cls, context
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("AI Güvenine Göre PnL", callback_data='perf_report_ai_confidence')],
        [InlineKeyboardButton("Teknik Skora Göre PnL", callback_data='perf_report_technical_score')],
        [InlineKeyboardButton("Momentuma Göre PnL", callback_data='perf_report_momentum')],
        [InlineKeyboardButton("🔙 Ayarlara Geri Dön", callback_data='show_settings')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        text="📈 Performans Analizi\n\nLütfen analiz etmek istediğiniz metriği seçin:",
        reply_markup=reply_markup
    )
    return State.ALARM_SETUP


async def handle_performance_report(cls, update: Update, context: CallbackContext):
    """Belirli bir özelliğe göre performans raporu oluşturur ve gönderir."""
    _ = cls
    query = update.callback_query
    await query.answer("Rapor oluşturuluyor, lütfen bekleyin...")

    feature_map = {
        'perf_report_ai_confidence':'ai_confidence',
        'perf_report_technical_score':'technical_score',
        'perf_report_momentum':'momentum',
    }

    feature_name = feature_map.get(query.data)
    if not feature_name:
        await query.edit_message_text("Geçersiz rapor türü.")
        return State.ALARM_SETUP

    if query.message and isinstance(query.message, Message):
        try:
            from .analytics import PerformanceReport
            pr = PerformanceReport()
            image_buf = pr.plot_stats_by_feature(feature_name=feature_name, bins=8)

            if image_buf:
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=image_buf,
                    caption=f"📊 '{feature_name.replace('_', ' ').title()}' metriğine göre Ortalama PnL Dağılımı"
                )
                await query.message.reply_text("Başka bir rapor görüntülemek ister misiniz?",
                    reply_markup=query.message.reply_markup)
            else:
                await query.message.reply_text(f"'{feature_name}' için rapor oluşturulacak yeterli veri bulunamadı.")

        except Exception as e:
            logger.error(f"Performans raporu oluşturulurken hata: {e}", exc_info=True)
            try:
                await query.message.reply_text("Rapor oluşturulurken bir hata oluştu.")
            except Exception as reply_err:
                logger.error(f"Performans raporu hata mesajı gönderilemedi: {reply_err}")
    else:
        logger.warning(
            "handle_performance_report: query.message erişilebilir değil, kullanıcıya bildirim gönderiliyor.")
        await context.bot.send_message(chat_id=query.from_user.id,
            text="Rapor oluşturulurken bir hata oluştu: Mesaj içeriği alınamadı.")

    return State.ALARM_SETUP


async def show_autotune_summary(cls, update: Update, context: CallbackContext):
    """
    Auto-tune mekanizmasının son analiz raporunu ve önerilerini gösterir.
    """
    query = update.callback_query
    await query.answer("Auto-tune özeti oluşturuluyor...")

    try:
        from StrategyAdaptiveTuner import StrategyAdaptiveTuner
        # DÜZELTME: get_summary metodu artık bir coroutine olabilir, await ile çağırıyoruz.
        summary_text = await StrategyAdaptiveTuner.get_summary()

        if not summary_text:
            summary_text = "Henüz analiz edilecek yeterli veri yok veya auto-tune kapalı."

        keyboard = [
            [InlineKeyboardButton("🔙 Geri", callback_data='back_to_alarm_menu')]
        ]
        await query.edit_message_text(
            text=f"🧠 **Auto-Tune Analiz Özeti**\n\n{summary_text}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"Auto-tune özeti alınırken hata: {e}", exc_info=True)
        await query.edit_message_text("❌ Özet alınırken bir hata oluştu.")

    return State.ALARM_SETUP


async def show_param_optimization(cls, update: Update, context: CallbackContext):
    """
    Haftalık parametre optimizasyonunu çalıştırır ve önerileri gösterir.
    """
    query = update.callback_query
    await query.answer("Parametre optimizasyonu çalıştırılıyor...")

    try:
        # DÜZELTME: run_weekly_optimizer senkron bir metot olabilir, to_thread ile çalıştır.
        opt_result = await asyncio.to_thread(cls.run_weekly_optimizer, lookback_days=7)

        text = "💡 **Parametre Optimizasyon Önerileri**\n\n"
        if opt_result and opt_result.get('status') == 'ok' and opt_result.get('suggestions'):
            for suggestion in opt_result['suggestions']:
                param = suggestion.get('param', '?')
                action = suggestion.get('action', '?')
                reason = suggestion.get('reason', '-')
                text += f"🔹 **Parametre:** `{param}`\n"
                text += f"   **Aksiyon:** `{action}`\n"
                text += f"   **Sebep:** {reason}\n\n"
        else:
            text += "Şu an için uygulanabilir bir parametre önerisi bulunamadı."

        keyboard = [[InlineKeyboardButton("🔙 Geri", callback_data='show_settings')]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"Parametre optimizasyonu hatası: {e}", exc_info=True)
        await query.edit_message_text("❌ Optimizasyon sırasında bir hata oluştu.")

    return State.ALARM_SETUP


async def show_segment_stats(cls, update: Update, context: CallbackContext):
    """
    En iyi ve en kötü performans gösteren piyasa segmentlerini raporlar.
    """
    query = update.callback_query
    await query.answer("Segment istatistikleri hesaplanıyor...")

    try:
        from analytics.segment_stats import get_segment_manager
        sm = get_segment_manager()

        text = "📊 **Piyasa Segmenti Performans Raporu**\n\n"

        # En çok sinyal üretenler
        top_by_count = sm.get_top_segments(by='count', limit=3)
        text += "📈 **En Çok Sinyal Üreten Segmentler:**\n"
        if top_by_count:
            for (sid, key), data in top_by_count:
                text += f"  - `{sid}:{key}` (Sinyal: {data.get('count', 0)}, PnL: {data.get('avg_pnl_pct', 0):.2f}%)\n"
        else:
            text += "  - _Veri yok_\n"

        # En karlı segmentler
        top_by_pnl = sm.get_top_segments(by='pnl', limit=3)
        text += "\n💰 **En Karlı Segmentler (Ort. PnL):**\n"
        if top_by_pnl:
            for (sid, key), data in top_by_pnl:
                text += f"  - `{sid}:{key}` (PnL: {data.get('avg_pnl_pct', 0):.2f}%, Sinyal: {data.get('count', 0)})\n"
        else:
            text += "  - _Veri yok_\n"

        # En yüksek potansiyelli (R) segmentler
        top_by_r = sm.get_top_segments(by='ema_exp_r', limit=3)
        text += "\n🎯 **En Yüksek Potansiyelli Segmentler (EMA Exp. R):**\n"
        if top_by_r:
            for (sid, key), data in top_by_r:
                text += f"  - `{sid}:{key}` (R: {data.get('ema_exp_r', 0):.2f}, Sinyal: {data.get('count', 0)})\n"
        else:
            text += "  - _Veri yok_\n"

        keyboard = [[InlineKeyboardButton("🔙 Geri", callback_data='show_settings')]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    except (ImportError, AttributeError, Exception) as e:
        logger.error(f"Segment istatistikleri alınırken hata: {e}", exc_info=True)
        await query.edit_message_text("❌ Segment istatistikleri alınamadı.")

    return State.ALARM_SETUP


async def show_segment_optimization(cls, update: Update, context: CallbackContext):
    """
    Segment bazlı optimizasyon önerileri sunar.
    """
    query = update.callback_query
    await query.answer("Segment optimizasyonu çalıştırılıyor...")

    try:
        from analytics.segment_stats import get_segment_manager
        sm = get_segment_manager()

        text = "🛠️ **Segment Optimizasyon Önerileri**\n\n"
        suggestions = sm.get_optimization_suggestions()

        if suggestions:
            for suggestion in suggestions:
                text += f"🔹 **Segment:** `{suggestion['strategy_id']}:{suggestion['segment_key']}`\n"
                text += f"   **Durum:** {suggestion['status']}\n"
                text += f"   **Öneri:** {suggestion['suggestion']}\n\n"
        else:
            text += "Şu an için uygulanabilir bir segment optimizasyon önerisi bulunamadı."

        keyboard = [[InlineKeyboardButton("🔙 Geri", callback_data='show_settings')]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    except (ImportError, AttributeError, Exception) as e:
        logger.error(f"Segment optimizasyonu sırasında hata: {e}", exc_info=True)
        await query.edit_message_text("❌ Segment optimizasyonu sırasında bir hata oluştu.")

    return State.ALARM_SETUP


async def handle_dashboard_period_report(cls, update: Update, context: CallbackContext):
    """
    Dashboard üzerindeki periyot (Günlük, Haftalık, Aylık, Yıllık, Excel) butonlarını yönetir.
    """
    query = update.callback_query
    data = query.data

    # Butona tıklandığını Telegram'a bildir (Dönen yuvarlak kalksın)
    try: await query.answer()
    except: pass

    from analytics.performance_report import PerformanceReport
    pr = PerformanceReport()

    # ---------------------------------------------------------
    # 1. GÜNLÜK RAPOR
    # ---------------------------------------------------------
    if data == 'perf_day':
        pr.merge_active_signals()
        df_filtered = pr.filter_period('day')
        pr.df = df_filtered
        table_text = pr.generate_detailed_telegram_table()
        kb = [[InlineKeyboardButton("🔙 Menüye Dön", callback_data='performance_dashboard')]]
        await _send_report_chunks(update, context, table_text, kb)
        return State.ALARM_SETUP

    # ---------------------------------------------------------
    # 2. HAFTALIK RAPOR
    # ---------------------------------------------------------
    elif data == 'perf_week':
        now = datetime.now()
        day_of_month = now.day
        week_number = (day_of_month - 1) // 7 + 1
        if week_number > 4: week_number = 4

        pr.load_specific_period(now.year, now.month, week_number)
        table_text = pr.generate_detailed_telegram_table()
        header_info = f"📅 <b>BU HAFTA ({week_number}. Hafta)</b>\n"
        table_text = header_info + table_text

        kb = [[InlineKeyboardButton("🔙 Menüye Dön", callback_data='performance_dashboard')]]
        await _send_report_chunks(update, context, table_text, kb)
        return State.ALARM_SETUP

    # ---------------------------------------------------------
    # 3. AYLIK RAPOR (Sadece Geçerli Haftalar)
    # ---------------------------------------------------------
    elif data == 'perf_month':
        now = datetime.now()
        day_of_month = now.day
        current_week = (day_of_month - 1) // 7 + 1
        if current_week > 4: current_week = 4
        return await _show_month_weeks_menu(update, now.year, now.month, max_week=current_week)

    # ---------------------------------------------------------
    # 4. YILLIK RAPOR (Sadece Veri Olan Aylar)
    # ---------------------------------------------------------
    elif data == 'perf_year':
        now = datetime.now()
        available_months = pr.get_available_months(now.year)
        # Yıllık rapor modunda olduğumuzu belirtmek için mode='view'
        return await _show_year_months_menu(update, now.year, available_months=available_months, mode='view')

    # ---------------------------------------------------------
    # 5. ÖZEL ARALIK (Arşiv Gezgini)
    # ---------------------------------------------------------
    elif data == 'perf_range':
        archive_msg = pr.archive_old_signals()
        years = pr.get_available_years()

        if not years:
            await query.edit_message_text(f"⚠️ Veri yok.\n{archive_msg}",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 Geri", callback_data='performance_dashboard')]]))
            return State.ALARM_SETUP

        kb = []
        for y in years:
            # Arşiv modunda olduğumuzu belirtmek için prefix 'perf_year_'
            kb.append([InlineKeyboardButton(f"📂 {y}", callback_data=f'perf_year_{y}')])
        kb.append([InlineKeyboardButton("🔙 Geri", callback_data='performance_dashboard')])

        await query.edit_message_text(
            f"🗄️ <b>Arşiv Gezgini</b>\nℹ️ {archive_msg}\nYıl seçiniz:",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML
        )
        return State.ALARM_SETUP

    # ---------------------------------------------------------
    # 6. EXCEL RAPORU (ADIM 1: YIL SEÇİMİ)
    # ---------------------------------------------------------
    elif data == 'perf_excel':
        years = pr.get_available_years()
        if not years:
            await query.edit_message_text("⚠️ Excel için veri bulunamadı.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 Geri", callback_data='performance_dashboard')]]))
            return State.ALARM_SETUP

        kb = []
        for y in years:
            kb.append([InlineKeyboardButton(f"📊 {y} Excel", callback_data=f'excel_year_{y}')])
        kb.append([InlineKeyboardButton("🔙 Geri", callback_data='performance_dashboard')])

        await query.edit_message_text("📊 <b>Excel Raporu</b>\nHangi yılın raporunu istiyorsunuz?",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        return State.ALARM_SETUP

    # ---------------------------------------------------------
    # NAVİGASYON VE AKSİYONLAR
    # ---------------------------------------------------------

    # A) EXCEL İÇİN YIL SEÇİLDİ -> AY SEÇİMİ (ADIM 2)
    elif data.startswith('excel_year_'):
        year = int(data.split('_')[2])
        available_months = pr.get_available_months(year)
        # Excel modunda olduğumuzu belirtmek için mode='excel'
        return await _show_year_months_menu(update, year, available_months=available_months, mode='excel')

    # B) EXCEL İÇİN AY SEÇİLDİ -> RAPOR OLUŞTUR VE GÖNDER (ADIM 3)
    elif data.startswith('excel_month_'):
        parts = data.split('_')
        year = int(parts[2])
        month = int(parts[3])

        # 1. TEMİZLİK: Eski menüyü sil
        try: await query.delete_message()
        except: pass

        # 2. BİLGİLENDİRME: Geçici mesaj gönder
        status_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⏳ <b>Excel raporu hazırlanıyor, lütfen bekleyin...</b>",
            parse_mode=ParseMode.HTML
        )

        # 3. VERİ YÜKLEME VE OLUŞTURMA
        pr.load_specific_period(year, month)
        # Eğer seçilen ay şimdiki aysa, aktif işlemleri de dahil et
        now = datetime.now()
        if year == now.year and month == now.month:
            pr.merge_active_signals()

        file_path = pr.export_excel_pro()

        # 4. GÖNDERİM VE SON TEMİZLİK
        if file_path and os.path.exists(file_path):
            # Dosyayı gönder
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=open(file_path, 'rb'),
                filename=os.path.basename(file_path),
                caption=f"📊 <b>{month}/{year} Performans Raporu</b>",
                parse_mode=ParseMode.HTML
            )

            # Sunucudaki dosyayı sil
            pr.cleanup_file(file_path)

            # "Hazırlanıyor" mesajını sil
            try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=status_msg.message_id)
            except: pass

            # Temiz bir "Geri Dön" butonu gönder
            kb = [[InlineKeyboardButton("🔙 Ana Menüye Dön", callback_data='performance_dashboard')]]
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="✅ Rapor başarıyla oluşturuldu.",
                reply_markup=InlineKeyboardMarkup(kb)
            )
        else:
            # Hata durumunda
            try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=status_msg.message_id)
            except: pass

            kb = [[InlineKeyboardButton("🔙 Geri", callback_data=f'excel_year_{year}')]]
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Rapor oluşturulamadı veya bu ay için veri yok.",
                reply_markup=InlineKeyboardMarkup(kb)
            )

        return State.ALARM_SETUP

    # C) ARŞİV İÇİN YIL SEÇİLDİ -> AY LİSTESİ
    elif data.startswith('perf_year_'):
        year = int(data.split('_')[2])
        available_months = pr.get_available_months(year)
        return await _show_year_months_menu(update, year, available_months=available_months, mode='view')

    # D) AY SEÇİLDİ -> HAFTA LİSTESİ
    elif data.startswith('perf_month_'):
        parts = data.split('_')
        year = int(parts[2])
        month = int(parts[3])

        now = datetime.now()
        if year == now.year and month == now.month:
            day_of_month = now.day
            current_week = (day_of_month - 1) // 7 + 1
            if current_week > 4: current_week = 4
            return await _show_month_weeks_menu(update, year, month, max_week=current_week)
        else:
            return await _show_month_weeks_menu(update, year, month, max_week=4)

    # E) HAFTA SEÇİLDİ -> TABLO GÖSTERİMİ
    elif data.startswith('perf_week_'):
        parts = data.split('_')
        year = int(parts[2])
        month = int(parts[3])
        week = int(parts[4])

        target_week = week if week > 0 else None
        pr.load_specific_period(year, month, target_week)
        table_text = pr.generate_detailed_telegram_table()

        # Geri butonu mantığı
        now = datetime.now()
        if year == now.year and month == now.month:
            kb = [[InlineKeyboardButton("🔙 Menüye Dön", callback_data='performance_dashboard')]]
        else:
            kb = [[InlineKeyboardButton("🔙 Haftalara Dön", callback_data=f'perf_month_{year}_{month}')]]

        await _send_report_chunks(update, context, table_text, kb)
        return State.ALARM_SETUP

    return State.ALARM_SETUP


# --- YARDIMCI FONKSİYONLAR (GÜNCELLENMİŞ) ---
async def _show_year_months_menu(update, year, available_months=None, mode='view'):
    """
    Yılın SADECE VERİ OLAN aylarını gösteren menü.
    mode='view' -> Normal raporlama (perf_month_...)
    mode='excel' -> Excel raporlama (excel_month_...)
    """
    month_names = {1:'Ocak', 2:'Şubat', 3:'Mart', 4:'Nisan', 5:'Mayıs', 6:'Haziran',
        7:'Temmuz', 8:'Ağustos', 9:'Eylül', 10:'Ekim', 11:'Kasım', 12:'Aralık'}

    kb = []

    # Veri olan aylar yoksa boş liste
    if not available_months:
        loop_list = []
    else:
        loop_list = available_months

    # Excel moduysa ve şu anki yılsa, en üste "Bu Ay" butonu ekle
    now = datetime.now()
    if mode == 'excel' and year == now.year:
        m_name = month_names.get(now.month, str(now.month))
        kb.append(
            [InlineKeyboardButton(f"📥 Bu Ayın Raporu ({m_name})", callback_data=f'excel_month_{year}_{now.month}')])

    row = []
    for m in loop_list:
        # Eğer Excel modundaysak ve "Bu Ay" butonunu zaten yukarı eklediysek, tekrar ekleme
        if mode == 'excel' and year == now.year and m == now.month:
            continue

        btn_text = f"📅 {month_names.get(m, str(m))}"

        # Callback data moda göre değişir
        if mode == 'excel':
            cb_data = f"excel_month_{year}_{m}"
        else:
            cb_data = f"perf_month_{year}_{m}"

        row.append(InlineKeyboardButton(btn_text, callback_data=cb_data))

        if len(row) == 2:
            kb.append(row)
            row = []
    if row: kb.append(row)

    # Geri Butonları
    if mode == 'excel':
        kb.append([InlineKeyboardButton("🔙 Yıllara Dön", callback_data='perf_excel')])
        title = f"📊 <b>{year} Excel Raporu</b>\nAy seçiniz:"
    else:
        if year == now.year:
            kb.append([InlineKeyboardButton("🔙 Menüye Dön", callback_data='performance_dashboard')])
        else:
            kb.append([InlineKeyboardButton("🔙 Yıllara Dön", callback_data='perf_range')])
        title = f"📂 <b>{year} Yılı Kayıtları</b>\nAy seçiniz:"

    await update.callback_query.edit_message_text(
        title,
        reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML
    )
    return State.ALARM_SETUP


async def _show_month_weeks_menu(update, year, month, max_week=4):
    """
    Ayın haftalarını gösteren menü.
    """
    month_names = {1:'Ocak', 2:'Şubat', 3:'Mart', 4:'Nisan', 5:'Mayıs', 6:'Haziran',
        7:'Temmuz', 8:'Ağustos', 9:'Eylül', 10:'Ekim', 11:'Kasım', 12:'Aralık'}
    m_name = month_names.get(month, str(month))

    kb = []
    # Haftaları listele
    for w in range(1, max_week + 1):
        label = f"{w}. Hafta"
        if w == 4: label += " (22-Son)"
        else: label += f" ({(w - 1) * 7 + 1}-{w * 7})"

        kb.append([InlineKeyboardButton(label, callback_data=f'perf_week_{year}_{month}_{w}')])

    # Tüm Ayı Göster butonu
    kb.append([InlineKeyboardButton("Tüm Ayı Göster", callback_data=f'perf_week_{year}_{month}_0')])

    # Geri Butonu Mantığı
    now = datetime.now()
    if year == now.year:
        # Eğer içinde bulunduğumuz yılsa, 'Yıllık Rapor' menüsüne (Aylara) dön
        kb.append([InlineKeyboardButton(f"🔙 {year} Aylarına Dön", callback_data='perf_year')])
    else:
        # Eğer geçmiş bir yılsa, o yılın arşiv menüsüne dön
        kb.append([InlineKeyboardButton(f"🔙 {year} Aylarına Dön", callback_data=f'perf_year_{year}')])

    await update.callback_query.edit_message_text(
        f"📅 <b>{m_name} {year}</b>\nHafta seçiniz:",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML
    )
    return State.ALARM_SETUP


async def _send_report_chunks(update, context, text, kb):
    """
    Uzun raporları parçalayarak gönderir.
    """
    query = update.callback_query

    # Eğer metin limiti aşmıyorsa direkt düzenle
    if len(text) <= 4096:
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        return

    # Limiti aşıyorsa parçala
    lines = text.split('\n')
    chunks = []
    current_chunk = ""

    for line in lines:
        if len(current_chunk) + len(line) + 5 > 4000:
            chunks.append(current_chunk)
            current_chunk = line + "\n"
        else:
            current_chunk += line + "\n"
    if current_chunk: chunks.append(current_chunk)

    # İlk parçayı mevcut mesajı düzenleyerek gönder
    await query.edit_message_text(chunks[0], parse_mode=ParseMode.HTML)

    # Diğer parçaları yeni mesaj olarak gönder
    for i in range(1, len(chunks)):
        is_last = (i == len(chunks) - 1)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=chunks[i],
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(kb) if is_last else None
        )


async def handle_excel_export(cls, update: Update, context: CallbackContext):
    """
    Excel raporunu oluşturur ve belge olarak gönderir.
    Markdown hatasını önlemek için HTML formatı kullanılır.
    """
    query = update.callback_query
    chat_id = update.effective_chat.id

    # Kullanıcıya işlem yapıldığını hissettir
    try:
        await query.answer("Excel raporu hazırlanıyor, lütfen bekleyin...")
        await context.bot.send_chat_action(chat_id=chat_id, action="upload_document")
    except: pass

    try:
        from analytics.performance_report import PerformanceReport
        pr = PerformanceReport()

        # Raporu oluştur (Tarih verilmezse otomatik bu ayı alır)
        # İleride tarih seçimi eklemek istersek buraya parametre geçeceğiz.
        file_path = pr.export_excel_pro()

        if file_path and os.path.exists(file_path):
            # Dosya ismi için güvenli format (boşluksuz)
            timestamp_file = datetime.now().strftime("%Y%m%d_%H%M")
            # Mesajda göstermek için şık format
            timestamp_display = datetime.now().strftime("%d.%m.%Y %H:%M")

            filename = f"Olimpos_Rapor_{timestamp_file}.xlsx"

            # HTML formatı kullanarak _ hatasını engelliyoruz
            caption_text = (
                f"📊 <b>Detaylı Performans Raporu</b>\n"
                f"📅 Tarih: {timestamp_display}\n"
                f"ℹ️ <i>Tarih aralığı seçilmediği için bu ayın verileri derlendi.</i>"
            )

            with open(file_path, 'rb') as f:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=f,
                    filename=filename,
                    caption=caption_text,
                    parse_mode='HTML'  # Markdown yerine HTML kullanıyoruz
                )

            # Temizlik: Dosyayı sunucudan sil
            try:
                os.remove(file_path)
            except: pass
        else:
            await context.bot.send_message(chat_id=chat_id,
                text="⚠️ Rapor oluşturulacak veri bulunamadı veya dosya oluşturma hatası.")

    except Exception as e:
        logger.error(f"Excel gönderme hatası: {e}")
        # Hata mesajını kullanıcıya bildir
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Bir hata oluştu: {str(e)}")


async def handle_alarm_action(cls, update: Update, context: CallbackContext,
        strategy_id: Optional[str] = None):
    """
    Tüm alarm sistemi callback'lerini işleyen merkezi yönlendirici.
    """
    query = update.callback_query
    if not query:
        logger.error("❌ Callback query bulunamadı, işlem iptal edildi.")
        return State.MAIN_MENU

    # Her butona basıldığında "yükleniyor" animasyonunu göstermek için
    if query.data not in ['dash_refresh', 'noop']:
        try: await query.answer()
        except: pass

    # HATA ÇÖZÜMÜ: query.data None gelebilir, string'e çeviriyoruz.
    data = str(query.data) if query.data else "noop"

    logger.info(f"Callback yönlendiriliyor: {data}")

    # --- ÖZEL DURUM 1: EXCEL RAPORU ---
    if data == 'perf_excel':
        await handle_excel_export(cls, update, context)
        return State.ALARM_SETUP

    # --- ÖZEL DURUM 2: ANA MENÜYE DÖNÜŞ (DÜZELTME) ---
    if data == 'main_menu':
        # Sadece State.MAIN_MENU döndür.
        # Mesaj gönderme ve ekranı yenileme işini Olimpos_Cripto_Bot.py yapacak.
        return State.MAIN_MENU

    # Rota sözlüğü
    routes = {
        'show_settings':lambda u, c:show_settings(cls, u, c),
        'back_to_alarm_menu':lambda u, c:show_alarm_menu(cls, u, c),
        'ai_scan':lambda u, c:show_ai_tf_menu(u, c),
        'ai_strategy_scan':lambda u, c:do_strategy_scan_menu(cls, u, c),
        'top_gainers':lambda u, c:show_symbols(cls, u, c, 'gainers'),
        'top_losers':lambda u, c:show_symbols(cls, u, c, 'losers'),
        'other_symbols':lambda u, c:show_symbols(cls, u, c, 'others'),
        'active_alarms':lambda u, c:show_active_alarms(cls, u, c),
        'clear_all_alarms': lambda u, c: _clear_all_alarms(cls, u, c),

        # AKTİF ALARMLAR MENÜSÜNDEKİ BUTONLAR
        'start_ai_strategy':lambda u, c:toggle_strategy_wrapper(cls, u, c, True),
        'stop_strategy':lambda u, c:toggle_strategy_wrapper(cls, u, c, False),
        # ALARM KURULUM MENÜSÜNDEKİ BUTONLAR
        'start_strategy_setup':lambda u, c:toggle_strategy_from_setup(cls, u, c, True),
        'stop_strategy_setup':lambda u, c:toggle_strategy_from_setup(cls, u, c, False),
        'retrain_ai':lambda u, c:handle_retrain_ai(cls, u, c),
        'system_stats':lambda u, c:show_system_stats(cls, u, c),
        'performance_dashboard':lambda u, c:show_performance_dashboard(cls, u, c),
        'show_performance_menu':lambda u, c:show_performance_menu(cls, u, c),
        'param_menu':lambda u, c:show_param_menu(cls, u, c),
        'back_to_symbol_select':lambda u, c:show_symbols(cls, u, c, c.user_data.get('last_symbol_type', 'others')),
        'back_to_timeframe_select':lambda u, c:select_timeframe(cls, u, c),
        'show_tuner_mode_menu':lambda u, c:show_tuner_mode_menu(cls, u, c),
        'show_tf_thresholds_menu':lambda u, c:show_tf_thresholds_menu(cls, u, c),
        'autotune_summary':lambda u, c:show_autotune_summary(cls, u, c),
        'tfth_ai_edit':lambda u, c:show_tfth_ai_edit(cls, u, c),
        'tfth_strat_edit':lambda u, c:show_tfth_strat_edit(cls, u, c),
        'param_optimize':lambda u, c:show_param_optimization(cls, u, c),
        'segment_stats':lambda u, c:show_segment_stats(cls, u, c),
        'segment_optimize':lambda u, c:show_segment_optimization(cls, u, c),
        'select_strategy':lambda u, c:u.callback_query.answer("Bu özellik yakında eklenecektir.", show_alert=True),
        'strategy_versions':lambda u, c:u.callback_query.answer("Bu özellik yakında eklenecektir.", show_alert=True),
        'alarm_setup_menu':lambda u, c:u.callback_query.answer("Bu özellik yakında eklenecektir.", show_alert=True),
        'risk_menu': lambda u, c: show_risk_menu(cls, u, c),
        'risk_enable': lambda u, c: set_risk_enabled(cls, u, c, True),
        'risk_disable': lambda u, c: set_risk_enabled(cls, u, c, False),
        'risk_set_dd': lambda u, c: ask_risk_dd_limit(cls, u, c),
        'risk_reset_daily':lambda u, c:handle_risk_reset_daily(cls, u, c),

    }

    # Prefix bazlı rotalar
    prefix_routes = {
        'perf_':lambda u, c:handle_dashboard_period_report(cls, u, c),
        'perf_report_':lambda u, c:handle_performance_report(cls, u, c),
        'param_group_':lambda u, c:show_param_group(cls, u, c, data.replace('param_group_', '')),
        'edit_param_':lambda u, c:ask_new_param_value(cls, u, c, data.replace('edit_param_', '')),
        'select_symbol_':lambda u, c:_handle_symbol_selection(cls, u, c, data.replace('select_symbol_', '')),
        'timeframe_':lambda u, c:_handle_timeframe_selection(cls, u, c, data, strategy_id),
        'remove_alarm_id_': lambda u, c: remove_alarm_by_id(cls, u, c, data.replace('remove_alarm_id_', '')),
        'remove_alarm_':    lambda u, c: remove_alarm(cls, u, c, data.replace('remove_alarm_', '')),

        'ai_tf_':lambda u, c:_handle_ai_tf_selection(u, c, data.replace('ai_tf_', '')),
        'ai_strat_':lambda u, c:do_ai_scan_wrapper(cls, u, c, data.replace('ai_strat_', '')),
        'strat_scan_tf_':lambda u, c:_handle_strat_tf_selection(u, c, data.replace('strat_scan_tf_', '')),
        'strat_strat_':lambda u, c:do_strategy_scan_wrapper(cls, u, c, data.replace('strat_strat_', '')),
        'manual_strat_':lambda u, c:_handle_manual_strat_selection(cls, u, c, data),
        'set_tuner_':lambda u, c:handle_set_tuner_mode(cls, u, c, data.replace('set_tuner_', '')),
        'tfth_tf_':lambda u, c:_handle_tfth_kind_selection(cls, u, c, data.replace('tfth_tf_', '')),
        'tfth_ai_edit_':lambda u, c:ask_for_tfth_value(cls, u, c, 'ai', data.replace('tfth_ai_edit_', '')),
        'tfth_strat_edit_':lambda u, c:ask_for_tfth_value(cls, u, c, 'strat', data.replace('tfth_strat_edit_', '')),

    }

    try:
        # Tam eşleşme var mı?
        if data in routes:
            return await routes[data](update, context)

        # Prefix eşleşmesi var mı?
        for prefix, handler in prefix_routes.items():
            if data.startswith(prefix):
                return await handler(update, context)

        # Hiçbiri eşleşmediyse
        logger.warning(f"⚠️ Bilinmeyen alarm callback: {data}")
        await query.edit_message_text("Bilinmeyen işlem.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri", callback_data='back_to_alarm_menu')]]))
        return State.ALARM_SETUP

    except Exception as e:
        logger.error(f"❌ handle_alarm_action içinde hata ({data}): {e}", exc_info=True)
        await query.edit_message_text("İşlem sırasında bir hata oluştu. Lütfen tekrar deneyin.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri", callback_data='back_to_alarm_menu')]]))
        return State.ALARM_SETUP

async def remove_alarm_by_id(cls, update: Update, context: CallbackContext, alarm_id: str):
    try:
        alarm_id = str(alarm_id or "").strip()
        if not alarm_id:
            await update.callback_query.edit_message_text("❌ Geçersiz alarm_id.")
            return State.ALARM_SETUP

        # converted ise silme (UI zaten buton koymuyor ama ek güvenlik)
        for a in (cls.active_symbols or []):
            if isinstance(a, dict) and str(a.get("alarm_id")) == alarm_id:
                if str(a.get("status") or "").lower() == "converted":
                    try:
                        await update.callback_query.answer("⛔ Sinyale dönmüş alarm silinemez.", show_alert=True)
                    except Exception:
                        pass
                    return await show_active_alarms(cls, update, context)
                break

        before = len(cls.active_symbols or [])
        cls.active_symbols = [
            a for a in (cls.active_symbols or [])
            if not (isinstance(a, dict) and str(a.get("alarm_id")) == alarm_id)
        ]

        if len(cls.active_symbols) < before:
            try:
                cls.save_active_signals(force=True)
            except Exception:
                pass

        return await show_active_alarms(cls, update, context)

    except Exception as e:
        logger.error(f"[REMOVE_ALARM_BY_ID_ERR] {e}", exc_info=True)
        return await show_active_alarms(cls, update, context)

async def _clear_all_alarms(cls, update: Update, context: CallbackContext):
    cls.active_symbols.clear()
    try:
        cls.save_active_signals(force=True)
    except Exception:
        pass

    q = update.callback_query
    if q:
        await q.edit_message_text(
            "✅ Tüm alarmlar silindi.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri", callback_data="back_to_alarm_menu")]])
        )
    return State.ALARM_SETUP


async def show_strategy_selection_menu(update: Update, context: CallbackContext, scan_type_prefix: str):
    """AI veya Strateji taraması için strateji seçim menüsünü gösterir."""
    logger.info(
        f"[STRAT_SELECT_MENU] scan_type={scan_type_prefix} tf={context.user_data.get(f'{scan_type_prefix}_scan_tf')}")
    query = update.callback_query
    await query.answer()

    tf = context.user_data.get(f'{scan_type_prefix}_scan_tf')
    if not tf:
        await query.edit_message_text("Zaman aralığı bilgisi bulunamadı. Lütfen tekrar deneyin.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri", callback_data='back_to_alarm_menu')]]))
        return State.ALARM_SETUP

    back_cb = 'ai_scan' if scan_type_prefix == 'ai' else 'ai_strategy_scan'
    keyboard = [
        [InlineKeyboardButton("V1", callback_data=f'{scan_type_prefix}_strat_v1')],
        [InlineKeyboardButton("V2", callback_data=f'{scan_type_prefix}_strat_v2')],
        [InlineKeyboardButton("Her İkisi de", callback_data=f'{scan_type_prefix}_strat_both')],
        [InlineKeyboardButton("🔙 Geri", callback_data=back_cb)]
    ]
    await query.edit_message_text(f"TF: {tf}\nHangi strateji ile tarama yapılsın?",
        reply_markup=InlineKeyboardMarkup(keyboard))
    return State.ALARM_SETUP


async def show_risk_menu(cls, update: Update, context: CallbackContext):
    _ = cls, context
    from core.risk_kill_switch import get_state, refresh_from_config

    refresh_from_config()
    st = get_state()

    e0 = float(st.e0 or 0.0)
    last = float(st.last_equity or 0.0)

    dd_pct = 0.0
    if e0 > 0:
        dd_pct = ((last - e0) / e0) * 100.0

    status = "🔴 KILL-SWITCH ON (OPEN ENGELLİ)" if st.kill_on else "🟢 NORMAL"
    enabled_txt = "✅ Aktif" if st.enabled else "⛔ Kapalı"

    text = (
        "🛡 <b>Risk / Kill-Switch</b>\n\n"
        f"Durum: <b>{status}</b>\n"
        f"Sistem: <b>{enabled_txt}</b>\n"
        f"Günlük DD Limiti: <b>-%{st.dd_limit_pct:.2f}</b>\n"
        f"E0 (Gün Başı): <b>{e0:.2f}</b>\n"
        f"Son Equity: <b>{last:.2f}</b>\n"
        f"DD: <b>{dd_pct:.2f}%</b>\n"
        f"Sebep: <code>{st.reason or '-'}</code>\n\n"
        "Not: Kill-switch tetiklenince sadece <b>OPEN</b> engellenir. <b>CLOSE</b> serbest."
    )

    kb = [
        [InlineKeyboardButton("🔄 Yenile", callback_data="risk_menu")],
        [
            InlineKeyboardButton("🟢 Aktifleştir", callback_data="risk_enable"),
            InlineKeyboardButton("🔴 Kapat", callback_data="risk_disable"),
        ],
        [InlineKeyboardButton("✏️ Günlük DD Limitini Değiştir", callback_data="risk_set_dd")],
        [InlineKeyboardButton("🔁 Günlük Reset (Kill Off)", callback_data="risk_reset_daily")],
        [InlineKeyboardButton("🔙 Ayarlara Dön", callback_data="show_settings")]
    ]

    # Risk menüsü “nerede” açık bilgisini sakla (DD limit input sonrası otomatik güncellemek için)
    try:
        if update.callback_query and update.callback_query.message:
            context.user_data['risk_menu_chat_id'] = update.callback_query.message.chat_id
            context.user_data['risk_menu_message_id'] = update.callback_query.message.message_id
    except Exception:
        pass

    await update.callback_query.edit_message_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML
    )
    return State.ALARM_SETUP


async def set_risk_enabled(cls, update: Update, context: CallbackContext, enabled: bool):
    _ = cls, context
    try:
        ConfigService.set("risk.kill_switch_enabled", bool(enabled))
        try:
            await update.callback_query.answer("✅ Güncellendi")
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[RISK_ENABLE_ERR] {e}", exc_info=True)
        try:
            await update.callback_query.answer("❌ Güncellenemedi", show_alert=True)
        except Exception:
            pass

    return await show_risk_menu(cls, update, context)


async def ask_risk_dd_limit(cls, update: Update, context: CallbackContext):
    _ = cls
    try:
        cur = float(ConfigService.get("risk.daily_dd_limit_pct", 30.0))
    except Exception:
        cur = 30.0

    context.user_data['pending_risk_edit'] = 'risk.daily_dd_limit_pct'

    # input ekranını nerede açtık? (sonrasında orayı güncelleyeceğiz)
    try:
        if update.callback_query and update.callback_query.message:
            context.user_data['risk_menu_chat_id'] = update.callback_query.message.chat_id
            context.user_data['risk_menu_message_id'] = update.callback_query.message.message_id
    except Exception:
        pass

    await update.callback_query.edit_message_text(
        text=(
            "✏️ <b>Günlük DD Limiti</b>\n\n"
            f"Mevcut: <b>%{cur:.2f}</b>\n\n"
            "Yeni değeri yüzde olarak yazın.\n"
            "Örn: <code>30</code> veya <code>25.5</code>"
        ),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ İptal", callback_data="risk_menu")]
        ]),
        parse_mode=ParseMode.HTML
    )
    return State.WAITING_FOR_PARAM_VALUE


async def handle_risk_reset_daily(cls, update: Update, context: CallbackContext):
    _ = cls

    # ✅ ADMIN-ONLY KONTROLÜ (TAM YERİ: FONKSİYONUN EN BAŞI)
    from config.constants import ADMIN_USER_ID
    uid = update.effective_user.id if update.effective_user else None
    if uid != ADMIN_USER_ID:
        try:
            await update.callback_query.answer("⛔ Yetkiniz yok.", show_alert=True)
        except Exception:
            pass
        return await show_risk_menu(cls, update, context)

    from core.risk_kill_switch import get_state, reset_daily, refresh_from_config

    # 1) EquityService'i bot_data üzerinden bul
    eq = None
    try:
        eq = context.application.bot_data.get('equity_service')  # type: ignore
    except Exception:
        eq = None

    # 2) last_equity (önce EquityService, yoksa kill-switch state)
    last_equity = None
    try:
        if eq and getattr(eq, "daily", None) and getattr(eq.daily, "state", None):
            last_equity = getattr(eq.daily.state, "last_equity", None)
    except Exception:
        last_equity = None

    refresh_from_config()
    st = get_state()
    if not last_equity:
        last_equity = st.last_equity

    if not last_equity or float(last_equity) <= 0:
        try:
            await update.callback_query.answer(
                "⚠️ Equity henüz hazır değil. 1-2 dakika sonra tekrar deneyin.",
                show_alert=True
            )
        except Exception:
            pass
        return await show_risk_menu(cls, update, context)

    last_equity = float(last_equity)

    # 3) DailySummaryManager e0 reset (varsa)
    try:
        if eq and getattr(eq, "daily", None) and getattr(eq.daily, "state", None):
            eq.daily.state.e0 = last_equity
    except Exception as e:
        logger.warning(f"[RISK_RESET_DAILY_DAILYSM_ERR] {e}")

    # 4) Kill-switch reset (kill_off + e0 = last_equity)
    try:
        reset_daily(e0=last_equity)
    except Exception as e:
        logger.error(f"[RISK_RESET_DAILY_KILLSW_ERR] {e}", exc_info=True)
        try:
            await update.callback_query.answer("❌ Reset başarısız", show_alert=True)
        except Exception:
            pass
        return await show_risk_menu(cls, update, context)

    try:
        await update.callback_query.answer("✅ Günlük reset yapıldı (Kill Off)")
    except Exception:
        pass

    return await show_risk_menu(cls, update, context)


async def show_ai_tf_menu(update: Update, context: CallbackContext):
    _ = context
    logger.info("[AI_SCAN] TF seçim menüsü gösteriliyor")
    query = update.callback_query
    await query.answer()

    tfs = ConfigService.get("scans.timeframes", None)
    if not isinstance(tfs, list) or not tfs:
        tfs = ["15m", "1h", "4h"]

    allowed = {'1m', '5m', '15m', '30m', '1h', '4h', '1d'}
    tfs = [str(tf).strip() for tf in tfs if str(tf).strip() in allowed]
    if not tfs:
        tfs = ["15m", "1h", "4h"]

    kb = []
    row = []
    for tf in tfs:
        btn_text = tf_to_tr_label(tf)  # ✅ Türkçe etiket
        row.append(InlineKeyboardButton(btn_text, callback_data=f'ai_tf_{tf}'))
        if len(row) == 3:
            kb.append(row)
            row = []
    if row:
        kb.append(row)

    kb.append([InlineKeyboardButton("🔙 Geri", callback_data='back_to_alarm_menu')])

    await query.edit_message_text("TF seçin...", reply_markup=InlineKeyboardMarkup(kb))
    return State.ALARM_SETUP


async def do_strategy_scan_menu(cls, update: Update, context: CallbackContext):
    """Strateji bazlı tarama için zaman aralığı ve strateji seçim menüsünü gösterir."""
    _ = cls, context

    tfs = ConfigService.get("scans.timeframes", None)
    if not isinstance(tfs, list) or not tfs:
        tfs = ["15m", "1h", "4h"]

    allowed = {'1m', '5m', '15m', '30m', '1h', '4h', '1d'}
    tfs = [str(tf).strip() for tf in tfs if str(tf).strip() in allowed]
    if not tfs:
        tfs = ["15m", "1h", "4h"]

    keyboard = []
    row = []
    for tf in tfs:
        btn_text = tf_to_tr_label(tf)  # ✅ Türkçe etiket
        row.append(InlineKeyboardButton(btn_text, callback_data=f'strat_scan_tf_{tf}'))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("🔙 Geri", callback_data='back_to_alarm_menu')])

    await update.callback_query.edit_message_text(
        "Strateji bazlı tarama için zaman aralığı seçin:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return State.ALARM_SETUP


async def _run_ai_scan_task(cls: 'OlimposStrategy', chat_id: int, message_id: int, user_id: int, tf: str, strategy: str,
        context: CallbackContext):
    """
    AI taramasını detaylı loading bar ile yürütür ve Aktif Alarmlara yönlendirir.
    """
    bot = context.bot

    async def _safe_send_message_local(target_chat_id: int, text: str):
        try:
            try:
                chat_obj = await bot.get_chat(target_chat_id)
                if getattr(chat_obj, "is_bot", False):
                    logger.error(f"[AI_SCAN_TASK_SEND_SKIP_BOT_TARGET] chat_id={target_chat_id} is_bot=True")
                    return
            except Exception as e:
                logger.warning(f"[AI_SCAN_TASK_GET_CHAT_WARN] chat_id={target_chat_id} err={e}")

            await bot.send_message(chat_id=target_chat_id, text=text)
        except Exception as e:
            msg = str(e)
            if "bots can't send messages to bots" in msg or "Forbidden" in msg:
                logger.error(f"[AI_SCAN_TASK_SEND_FORBIDDEN] chat_id={target_chat_id} err={e}")
                return
            logger.error(f"[AI_SCAN_TASK_SEND_ERR] chat_id={target_chat_id} err={e}", exc_info=True)

    try:
        from strategies.alarm_system.scanning import do_ai_scan

        context.user_data['scan_session'] = {
            'chat_id':chat_id,
            'started_at':datetime.now(timezone.utc).isoformat(),
            'results':{},
        }

        # Her taramada kanal-tekilleştirme setini sıfırlamak istersen:
        cls._scan_notified_keys = set()

        strategies_to_run = ['v1', 'v2'] if strategy == 'both' else [strategy]
        exchange_name = context.user_data.get('exchange', 'Bilinmiyor').upper()

        # Detaylı Bar Oluşturucu (Strateji taramasındakiyle aynı)
        def make_bar(pct, width=15):
            filled = int(round(width * pct))
            empty = width - filled
            return f"[{'█' * filled}{'░' * empty}] %{int(pct * 100)}"

        last_update_time = 0

        async def progress_handler(done, total):
            nonlocal last_update_time
            import time
            now = time.time()
            if now - last_update_time < 1.2 and done < total: return
            last_update_time = now

            pct = done / total if total > 0 else 0
            current_strat_name = strategies_to_run[
                current_strat_index].upper() if strategies_to_run else strategy.upper()

            # Strateji taramasıyla AYNI format
            text = (
                f"🤖 **AI Coin Taraması**\n"
                f"🏦 Borsa: `{exchange_name}`\n"
                f"⏰ Zaman: `{tf}`\n"
                f"🎯 Strateji: `{current_strat_name}` taranıyor...\n\n"
                f"{make_bar(pct)}\n\n"
                f"🔍 İşlenen: {done}/{total} sembol"
            )
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text,
                    parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass

        current_strat_index = 0
        for i, sid in enumerate(strategies_to_run):
            current_strat_index = i
            await do_ai_scan(cls, tf, sid, 100, chat_id, context, user_id, progress_callback=progress_handler)

        # Bitiş Mesajı
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id,
            text="✅ **AI Tarama Tamamlandı!**\n\nSonuçlar listeleniyor...", parse_mode=ParseMode.MARKDOWN)
        await asyncio.sleep(0.5)

        # DİREKT AKTİF ALARMLARA GİT
        await show_active_alarms(cls, update=None, context=context, chat_id=chat_id, edit_message_id=message_id)

    except Exception as e:
        logger.error(f"AI tarama hatası: {e}", exc_info=True)
        try:
            await _safe_send_message_local(chat_id, f"❌ Tarama hatası: {e}")
        except Exception:
            pass


async def do_ai_scan_wrapper(cls: 'OlimposStrategy', update: Update, context: CallbackContext, strategy: str):
    query = update.callback_query
    if not query: return State.ALARM_SETUP
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    await query.answer()
    tf = context.user_data.get('ai_scan_tf')
    if not tf:
        await query.edit_message_text("Zaman seçilmedi.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri", callback_data='ai_scan')]]))
        return State.ALARM_SETUP

    # Loading mesajını başlat
    msg = await query.edit_message_text(
        text="⏳ **Başlatılıyor...**",
        parse_mode=ParseMode.MARKDOWN
    )

    # message_id'yi task'a gönder
    asyncio.create_task(_run_ai_scan_task(cls, chat_id, msg.message_id, user_id, tf, strategy, context))
    return State.ALARM_SETUP


async def _run_strategy_scan_task(cls: 'OlimposStrategy', chat_id: int, message_id: int, user_id: int, tf: str,
        strategy: str, context: CallbackContext):
    """
    Strateji taramasını detaylı bar ile yürütür ve Aktif Alarmlara yönlendirir.
    """
    bot = context.bot

    try:
        from strategies.alarm_system.scanning import _do_strategy_scan

        context.user_data['scan_session'] = {
            'chat_id':chat_id,
            'started_at':datetime.now(timezone.utc).isoformat(),
            'results':{},
        }

        cls._scan_notified_keys = set()

        strategies_to_run = ['v1', 'v2'] if strategy == 'both' else [strategy]
        exchange_name = context.user_data.get('exchange', 'Bilinmiyor').upper()

        def make_bar(pct, width=15):
            filled = int(round(width * pct))
            empty = width - filled
            return f"[{'█' * filled}{'░' * empty}] %{int(pct * 100)}"

        last_update_time = 0

        async def progress_handler(done, total):
            nonlocal last_update_time
            import time
            now = time.time()
            if now - last_update_time < 1.2 and done < total: return
            last_update_time = now

            pct = done / total if total > 0 else 0
            current_strat_name = strategies_to_run[
                current_strat_index].upper() if strategies_to_run else strategy.upper()

            text = (
                f"📐 **Strateji Taraması**\n"
                f"🏦 Borsa: `{exchange_name}`\n"
                f"⏰ Zaman: `{tf}`\n"
                f"🎯 Strateji: `{current_strat_name}` taranıyor...\n\n"
                f"{make_bar(pct)}\n\n"
                f"🔍 İşlenen: {done}/{total} sembol"
            )
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text,
                    parse_mode=ParseMode.MARKDOWN)
            except Exception: pass

        current_strat_index = 0
        for i, sid in enumerate(strategies_to_run):
            current_strat_index = i
            await _do_strategy_scan(cls, tf, sid, 100, chat_id, context, user_id, progress_callback=progress_handler)

        # Bitiş Mesajı
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id,
            text="✅ **Strateji Taraması Tamamlandı!**\n\nListeye geçiliyor...", parse_mode=ParseMode.MARKDOWN)
        await asyncio.sleep(0.5)

        # YÖNLENDİRME DÜZELTİLDİ: Direkt Aktif Alarmlar Menüsüne
        await show_active_alarms(cls, update=None, context=context, chat_id=chat_id, edit_message_id=message_id)

    except Exception as e:
        logger.error(f"Strateji tarama hatası: {e}", exc_info=True)
        try: await bot.send_message(chat_id=chat_id, text=f"❌ Tarama hatası: {e}")
        except: pass


async def do_strategy_scan_wrapper(cls: 'OlimposStrategy', update: Update, context: CallbackContext, strategy: str):
    query = update.callback_query
    if not query: return State.ALARM_SETUP
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    await query.answer()
    tf = context.user_data.get('strat_scan_tf')
    if not tf:
        await query.edit_message_text("Zaman seçilmedi.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri", callback_data='ai_strategy_scan')]]))
        return State.ALARM_SETUP

    # Loading mesajını başlat
    msg = await query.edit_message_text(
        text="⏳ **Başlatılıyor...**",
        parse_mode=ParseMode.MARKDOWN
    )

    # message_id'yi task'a gönder
    asyncio.create_task(_run_strategy_scan_task(cls, chat_id, msg.message_id, user_id, tf, strategy, context))
    return State.ALARM_SETUP


async def show_param_menu(cls, update: Update, context: CallbackContext):
    """Genel strateji parametre gruplarını gösterir."""
    _ = cls, context
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("Core Ayarlar", callback_data='param_group_core')],
        [InlineKeyboardButton("AI Ayarları", callback_data='param_group_ai')],
        [InlineKeyboardButton("Risk Ayarları", callback_data='param_group_risk')],
        [InlineKeyboardButton("Multi-Timeframe", callback_data='param_group_mt')],
        [InlineKeyboardButton("🔙 Ayarlara Geri Dön", callback_data='show_settings')]
    ]
    await query.edit_message_text("Düzenlemek istediğiniz parametre grubunu seçin:",
        reply_markup=InlineKeyboardMarkup(keyboard))
    return State.ALARM_SETUP


async def show_param_group(cls, update: Update, context: CallbackContext, group: str):
    """Seçilen gruptaki parametreleri ve güncel değerlerini gösterir."""
    _ = cls, context
    query = update.callback_query
    await query.answer()

    param_paths = PARAM_PATHS_BY_GROUP.get(group)
    if not param_paths:
        await query.edit_message_text("Geçersiz parametre grubu.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri", callback_data='param_menu')]]))
        return State.ALARM_SETUP

    text = f"*{group.upper()} Parametreleri*\n\n"
    keyboard = []
    for path in param_paths:
        # Değeri doğrudan ConfigService'den alıyoruz. Bu, tek yetkili kaynak olmasını sağlar.
        value = ConfigService.get(path)
        value_str = f"{value:.4f}" if isinstance(value, float) else str(value)
        param_name = path.split('.')[-1]
        text += f"`{param_name}`: *{value_str}*\n"
        # Callback için yolu güvenli hale getir (noktaları __ ile değiştir)
        callback_path = path.replace('.', '__')
        keyboard.append([InlineKeyboardButton(f"Düzenle: {param_name}", callback_data=f'edit_param_{callback_path}')])

    keyboard.append([InlineKeyboardButton("🔙 Gruplara Geri Dön", callback_data='param_menu')])

    # Mesajı düzenle veya yeni mesaj gönder
    if query.message:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    else:
        await update.effective_chat.send_message(text, reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN)

    return State.ALARM_SETUP


async def ask_new_param_value(cls, update: Update, context: CallbackContext, param_path: str):
    """Kullanıcıdan yeni parametre değeri girmesini ister."""
    _ = cls
    query = update.callback_query
    await query.answer()
    context.user_data['pending_param_edit'] = param_path

    # Değeri ConfigService'den al
    current_value = ConfigService.get(param_path)

    await query.edit_message_text(
        f"*{param_path}* için yeni değeri girin.\n\nMevcut Değer: `{current_value}`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ İptal", callback_data='param_menu')]])
    )
    return State.WAITING_FOR_PARAM_VALUE


async def handle_param_value_input(cls, update: Update, context: CallbackContext):
    """Kullanıcının girdiği yeni runtime parametre değerini işler ve kaydeder."""
    param_path = context.user_data.get('pending_param_edit')
    if not param_path:
        return State.ALARM_SETUP
    # --- ✅ RISK (KILL-SWITCH) DD LIMIT EDIT AKIŞI ---
    pending_risk = context.user_data.get('pending_risk_edit')
    if pending_risk == 'risk.daily_dd_limit_pct':
        new_val_str = (update.message.text or "").strip()
        try:
            val = float(new_val_str.replace(',', '.'))
            val = abs(val)

            # mantık koruması (istersen aralığı değiştir)
            if val < 0.1 or val > 90:
                await update.message.reply_text("❌ Limit 0.1 ile 90 arasında olmalı.")
                return State.WAITING_FOR_PARAM_VALUE

            ConfigService.set("risk.daily_dd_limit_pct", val)

            # input flag temizle
            context.user_data.pop('pending_risk_edit', None)

            # kullanıcının yazdığı mesajı temizlemek istersen:
            try:
                await update.message.delete()
            except Exception:
                pass

            # --- ✅ OTOMATİK RISK MENÜSÜNÜ YENİLE ---
            chat_id = context.user_data.get('risk_menu_chat_id')
            message_id = context.user_data.get('risk_menu_message_id')

            # 1) önce varsa mevcut menü mesajını edit etmeyi dene
            if chat_id and message_id:
                try:
                    # sahte bir Update üretmeden doğrudan bot ile edit yapacağız:
                    from core.risk_kill_switch import get_state, refresh_from_config
                    refresh_from_config()
                    st = get_state()

                    e0 = float(st.e0 or 0.0)
                    last = float(st.last_equity or 0.0)
                    dd_pct = ((last - e0) / e0) * 100.0 if e0 > 0 else 0.0

                    status = "🔴 KILL-SWITCH ON (OPEN ENGELLİ)" if st.kill_on else "🟢 NORMAL"
                    enabled_txt = "✅ Aktif" if st.enabled else "⛔ Kapalı"

                    text = (
                        "🛡 <b>Risk / Kill-Switch</b>\n\n"
                        f"Durum: <b>{status}</b>\n"
                        f"Sistem: <b>{enabled_txt}</b>\n"
                        f"Günlük DD Limiti: <b>-%{st.dd_limit_pct:.2f}</b>\n"
                        f"E0 (Gün Başı): <b>{e0:.2f}</b>\n"
                        f"Son Equity: <b>{last:.2f}</b>\n"
                        f"DD: <b>{dd_pct:.2f}%</b>\n"
                        f"Sebep: <code>{st.reason or '-'}</code>\n\n"
                        "Not: Kill-switch tetiklenince sadece <b>OPEN</b> engellenir. <b>CLOSE</b> serbest."
                    )

                    kb = [
                        [InlineKeyboardButton("🔄 Yenile", callback_data="risk_menu")],
                        [
                            InlineKeyboardButton("🟢 Aktifleştir", callback_data="risk_enable"),
                            InlineKeyboardButton("🔴 Kapat", callback_data="risk_disable"),
                        ],
                        [InlineKeyboardButton("✏️ Günlük DD Limitini Değiştir", callback_data="risk_set_dd")],
                        [InlineKeyboardButton("🔙 Ayarlara Dön", callback_data="show_settings")]
                    ]

                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=text,
                        reply_markup=InlineKeyboardMarkup(kb),
                        parse_mode=ParseMode.HTML
                    )
                    return State.ALARM_SETUP

                except Exception as e:
                    logger.warning(f"[RISK_MENU_EDIT_FAIL] {e}")

            # 2) edit olmadıysa yeni menü mesajı gönder
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"✅ Günlük DD limiti güncellendi: %{val:.2f}\n\n🛡 Risk menüsü açılıyor...",
                parse_mode=ParseMode.HTML
            )

            # yeni mesajla risk menüsünü aç (yine bot.send_message ile)
            # Burada doğrudan “risk_menu” içeriğini basıyoruz:
            from core.risk_kill_switch import get_state, refresh_from_config
            refresh_from_config()
            st = get_state()

            e0 = float(st.e0 or 0.0)
            last = float(st.last_equity or 0.0)
            dd_pct = ((last - e0) / e0) * 100.0 if e0 > 0 else 0.0

            status = "🔴 KILL-SWITCH ON (OPEN ENGELLİ)" if st.kill_on else "🟢 NORMAL"
            enabled_txt = "✅ Aktif" if st.enabled else "⛔ Kapalı"

            text = (
                "🛡 <b>Risk / Kill-Switch</b>\n\n"
                f"Durum: <b>{status}</b>\n"
                f"Sistem: <b>{enabled_txt}</b>\n"
                f"Günlük DD Limiti: <b>-%{st.dd_limit_pct:.2f}</b>\n"
                f"E0 (Gün Başı): <b>{e0:.2f}</b>\n"
                f"Son Equity: <b>{last:.2f}</b>\n"
                f"DD: <b>{dd_pct:.2f}%</b>\n"
                f"Sebep: <code>{st.reason or '-'}</code>\n\n"
                "Not: Kill-switch tetiklenince sadece <b>OPEN</b> engellenir. <b>CLOSE</b> serbest."
            )

            kb = [
                [InlineKeyboardButton("🔄 Yenile", callback_data="risk_menu")],
                [
                    InlineKeyboardButton("🟢 Aktifleştir", callback_data="risk_enable"),
                    InlineKeyboardButton("🔴 Kapat", callback_data="risk_disable"),
                ],
                [InlineKeyboardButton("✏️ Günlük DD Limitini Değiştir", callback_data="risk_set_dd")],
                [InlineKeyboardButton("🔙 Ayarlara Dön", callback_data="show_settings")]
            ]

            msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=text,
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )

            # yeni message id’yi sakla
            context.user_data['risk_menu_chat_id'] = msg.chat_id
            context.user_data['risk_menu_message_id'] = msg.message_id

            return State.ALARM_SETUP

        except Exception as e:
            await update.message.reply_text(f"❌ Geçersiz değer: {new_val_str}\nHata: {e}")
            return State.WAITING_FOR_PARAM_VALUE

    new_val_str = update.message.text.strip()
    try:
        # Mevcut değeri ve tipini al
        current_value = ConfigService.get(param_path)
        value_type = type(current_value)

        # Gelen değeri doğru tipe çevir
        if value_type == bool:
            new_val = new_val_str.lower() in ['true', '1', 'evet', 'on', 'açık']
        elif value_type == int:
            new_val = int(new_val_str)
        elif value_type == float:
            new_val = float(new_val_str.replace(',', '.'))
        else:  # str vb.
            new_val = value_type(new_val_str)

        # Değeri ConfigService üzerinden ayarla
        ConfigService.set(param_path, new_val)

        # Değişikliği kalıcı hale getir
        cls.save_runtime_strategy()
        logging.info(f"✅ Çalışma zamanı stratejisi '{param_path}' değişikliği sonrası kaydedildi.")

        await update.message.reply_text(
            f"✅ Ayar güncellendi:\n`{param_path}` = `{new_val}`",
            parse_mode=ParseMode.MARKDOWN
        )
        # Hangi gruba ait olduğunu bul
        group = 'core'  # Varsayılan
        for g, paths in PARAM_PATHS_BY_GROUP.items():
            if param_path in paths:
                group = g
                break

        # Yeni mesaj göndererek menüyü yenile
        await update.message.delete()  # Kullanıcının girdiği değeri sil
        return await show_param_group(cls, update, context, group)

    except (ValueError, TypeError) as e:
        await update.message.reply_text(
            f"❌ Geçersiz değer: '{new_val_str}'. Lütfen doğru formatta bir değer girin.\nHata: {e}")
        return State.WAITING_FOR_PARAM_VALUE  # Tekrar değer girmesini bekle
    except Exception as e:
        logging.error(f"Parametre değeri kaydedilirken hata: {e}", exc_info=True)
        await update.message.reply_text("❌ Ayar kaydedilirken beklenmedik bir hata oluştu.")
        context.user_data.pop('pending_param_edit', None)
        return State.ALARM_SETUP


async def handle_tfth_value_input(cls, update: Update, context: CallbackContext):
    """
    Kullanıcının girdiği yeni TF Eşik değerini işler ve kaydeder.
    """
    pending_edit = context.user_data.get('pending_tfth_edit')
    if not pending_edit:
        return State.ALARM_SETUP

    new_val_str = update.message.text.strip()
    try:
        # Değeri float'a çevir
        val = float(new_val_str.replace(',', '.'))

        # ConfigService için tam yolu oluştur
        edit_type = pending_edit['type']
        tf = pending_edit['tf']
        key = pending_edit['key']

        if edit_type == 'ai':
            path = f"scans.tf_profiles.{tf}.ai_scan.{key}.value"
        else:  # 'strat'
            sid = pending_edit['sid']
            param_key = key.split('_', 1)[1]  # 'v1_min_score' -> 'min_score'
            path = f"scans.tf_profiles.{tf}.strategy_scan.{sid}.{param_key}.value"

        # Değeri ConfigService üzerinden ayarla ve kaydet
        ConfigService.set_manual(path, val)
        if not ConfigService.save_manual_config():
            await update.message.reply_text("❌ Ayar kalıcı olarak kaydedilirken bir hata oluştu.")
            return State.ALARM_SETUP

        # Başarı mesajı gönder
        file_path = "config/olimpos_tarama_ayarlari.json"
        await update.message.reply_text(
            f"✅ Ayar güncellendi ve `{os.path.basename(file_path)}` dosyasına kaydedildi:\n\n`{path}` = `{val}`",
            parse_mode=ParseMode.MARKDOWN
        )

        # --- ANA DÜZELTME: MENÜ GÜNCELLEME ---
        # 1. Kullanıcının girdiği mesajı silerek arayüzü temizle.
        await update.message.delete()

        # 2. Callback query'den gelen orijinal mesajı sil. Bu, "Message to edit not found" hatasını önler
        #    ve bir sonraki adımda menünün yeni bir mesajla gönderilmesini sağlar.
        if context.user_data.get('last_tfth_message_id'):
            try:
                await context.bot.delete_message(chat_id=update.effective_chat.id,
                    message_id=context.user_data['last_tfth_message_id'])
            except Exception as e:
                logger.warning(f"Eski TFTH menü mesajı silinemedi: {e}")

        # 3. Menüyü yeniden çizmek için ilgili fonksiyonu çağır.
        #    ConfigService.save_manual_config() içinde load(force=True) çağrıldığı için
        #    bu fonksiyonlar artık güncel verileri okuyacaktır.
        if edit_type == 'ai':
            return await show_tfth_ai_edit(cls, update, context)
        else:
            return await show_tfth_strat_edit(cls, update, context)

    except (ValueError, TypeError) as e:
        await update.message.reply_text(
            f"Geçersiz değer: '{new_val_str}'. Lütfen sayısal bir değer girin.\n\nHata: {e}")
        return State.MANUAL_SYMBOL_INPUT
    except Exception as e:
        logging.error(f"TF Eşik değeri kaydedilirken hata: {e}", exc_info=True)
        await update.message.reply_text("❌ Ayar kaydedilirken beklenmedik bir hata oluştu.")
        return State.ALARM_SETUP
    finally:
        context.user_data.pop('pending_tfth_edit', None)


async def ask_for_tfth_value(cls, update: Update, context: CallbackContext, edit_type: str, key: str):
    """
    Kullanıcıdan TF Eşik değeri girmesini ister.
    """
    # DÜZELTME: 'sid' değişkeni, sadece 'strat' durumunda tanımlandığı için
    # 'referenced before assignment' hatasını önlemek amacıyla başlangıçta None olarak ayarlandı.
    sid = None
    _ = cls
    tf = context.user_data.get('tfth_selected_tf')
    if not tf:
        await update.callback_query.edit_message_text("❌ Zaman aralığı bilgisi kayboldu. Lütfen tekrar deneyin.")
        return State.ALARM_SETUP

    # Düzenlenecek parametrenin tam yolunu ve mevcut değerini al
    if edit_type == 'ai':
        path = f"scans.tf_profiles.{tf}.ai_scan.{key}.value"
    else:  # 'strat'
        # 'v1_min_score' -> ['v1', 'min_score']
        sid, param_key = key.split('_', 1)
        path = f"scans.tf_profiles.{tf}.strategy_scan.{sid}.{param_key}.value"

    current_value = ConfigService.get(path, "N/A")

    context.user_data['pending_tfth_edit'] = {
        'type':edit_type,
        'tf':tf,
        'key':key,
        'sid':sid if edit_type == 'strat' else None
    }

    await update.callback_query.edit_message_text(
        f"*{path}* için yeni değeri girin.\n\nMevcut Değer: `{current_value}`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ İptal", callback_data=f'tfth_tf_{tf}')]])
    )
    return State.MANUAL_SYMBOL_INPUT


def _format_version_detail(version_data: dict) -> str:
    """Verilen versiyon verisini istenen detaylı formatta metne dönüştürür."""
    vid = version_data.get('version_id')
    ts = version_data.get('timestamp', '').split('T')[0]
    reason_data = version_data.get('reason', {})

    if not isinstance(reason_data, dict) or reason_data.get('source') != 'auto_tune':
        return f"--- v{vid} ({ts}) ---\n*Manuel Değişiklik*\n`{str(reason_data)}`\n"

    text = f"--- v{vid} (Auto-Tune @ {ts}) ---\n"
    reports = reason_data.get('reports', [])

    if not reports:
        return text + "_Analiz raporu bulunamadı._\n"

    for report in reports:
        exchange = report.get('exchange', '?').upper()
        tf = report.get('timeframe', '?')
        scan_type_tr = "AI Coin Tarama" if report.get('scan_type') == 'ai_scan' else "Strateji Bazlı Tarama"
        strategy_id = report.get('strategy_id', '?').upper()

        text += f"\n`Borsa: {exchange} | Zaman: {tf} | Tip: {scan_type_tr}`\n"
        text += f"----------------------------------------\n"

        # Tarama Analizi
        scan_analysis = report.get('analysis', {}).get('scan_analysis', {})
        text += f"*{strategy_id} Tarama Analizi*\n"
        text += f"  - Kurulan Alarm: {scan_analysis.get('alarms_set', 0)}\n"
        text += f"  - Sinyale Dönen: {scan_analysis.get('signals_converted', 0)}\n"

        # Kar/Zarar Analizi
        pnl_analysis = report.get('analysis', {}).get('pnl_analysis', {})
        text += f"*{strategy_id} Kar/Zarar Analizi*\n"
        text += f"  - Final Sinyal: {pnl_analysis.get('final_signals', 0)}\n"
        text += f"  - Stop Loss: {pnl_analysis.get('stop_loss_signals', 0)}\n"
        text += f"  - Net Kar: {pnl_analysis.get('avg_net_pct', 0.0):+.2f}%\n"

        # Parametre Değişiklikleri
        changes = report.get('changes', [])
        if not changes:
            text += f"*{strategy_id} Parametre Değişiklikleri*\n"
            text += "  - _Değişiklik yapılmadı, performans izleniyor._\n"
        else:
            text += f"*{strategy_id} Parametre Değişiklikleri*\n"
            for change in changes:
                param_name = ".".join(change.get('param', '?').split('.')[-2:])
                old_val = change.get('old', 0)
                new_val = change.get('new', 0)
                reason = change.get('reason', 'Performans optimizasyonu.')

                text += f"  - Parametre: `{param_name}`\n"
                text += f"  - Değer: `{old_val:.4f}` → `{new_val:.4f}`\n"
                text += f"  - Sebep: {reason}\n"

    return text


# show_alarm_menu
async def show_alarm_menu(cls, update: Optional[Update], context: CallbackContext) -> "State":
    """
    Alarm sistemi ana menüsünü gösterir.
    DÜZELTME: Model eğitim zamanları UTC'den TR saatine (UTC+3) çevrilerek gösteriliyor.
    YENİ: `update` nesnesi olmadan da çağrılabilir, bu durumda her zaman yeni mesaj gönderir.
    """
    # --- YENİ: STRATEJİ BAŞLAT/DURDUR BUTONU ---
    if cls.is_running:
        strategy_button = InlineKeyboardButton("🛑 Stratejiyi Durdur (Çalışıyor)", callback_data='stop_strategy_setup')
    else:
        strategy_button = InlineKeyboardButton("🚀 Stratejiyi Başlat (Durdu)", callback_data='start_strategy_setup')
    # --- YENİ KOD SONU ---

    try:
        cq, msg, chat_id, message_id = (None, None, None, None)
        if update:
            cq, msg, chat_id, message_id = cls._extract_update_primitives(update)
        if cq:
            try:
                await cq.answer()
            except Exception as e_ans:
                logging.debug(f"[CQ_ANSWER_FAIL] {e_ans}")

        # --- YENİ: DİNAMİK MODEL KONTROLÜ VE SAAT DÜZELTMESİ ---
        exchange_name = context.user_data.get('exchange', 'mexc')  # Varsayılan olarak 'mexc'

        model_files_to_check = {
            f'{exchange_name}_random_forest':f'{exchange_name}_random_forest_model.pkl',
            f'{exchange_name}_xgboost':f'{exchange_name}_xgboost_model.pkl',
            f'{exchange_name}_lightgbm':f'{exchange_name}_lightgbm_model.pkl',
            f'{exchange_name}_gradient_boost':f'{exchange_name}_gradient_boost_model.pkl',
            f'{exchange_name}_scaler':f'{exchange_name}_scaler.pkl',
        }

        # Eğitim zamanını al ve TR saatine çevir
        last_train_dt = cls.get_last_train_time_for_exchange(exchange_name)
        trained_at_str = None

        if last_train_dt:
            # Eğer zaman dilimi bilgisi yoksa UTC kabul et
            if last_train_dt.tzinfo is None:
                last_train_dt = last_train_dt.replace(tzinfo=timezone.utc)

            # Türkiye saati (UTC+3) için sabit offset kullanıyoruz (pytz zorunluluğunu kaldırmak için)
            tr_timezone = timezone(timedelta(hours=3))
            tr_time = last_train_dt.astimezone(tr_timezone)
            trained_at_str = tr_time.strftime("%Y-%m-%d %H:%M:%S")

        # Dosyaları kontrol et ve durumu belirle
        model_details = []
        active_model_count = 0
        models_directory = os.path.join(os.getcwd(), 'models')

        for model_name, filename in model_files_to_check.items():
            file_path = os.path.join(models_directory, filename)
            file_exists = os.path.exists(file_path)
            is_valid = file_exists and os.path.getsize(file_path) > 100

            if is_valid:
                active_model_count += 1

            model_details.append({
                "name":model_name,
                "status":is_valid,
                "trained_at":trained_at_str  # Düzeltilmiş TR saati
            })

        metadata_path = os.path.join(models_directory, 'metadata.json')
        metadata_exists = os.path.exists(metadata_path) and os.path.getsize(metadata_path) > 0
        model_details.append({
            "name":"metadata",
            "status":metadata_exists,
            "trained_at":trained_at_str
        })
        # --- DİNAMİK KONTROL SONU ---

        try:
            logging.info(f"Model dizini: {models_directory}")
        except Exception as directory_error:
            logging.error(f"Model dizini alma hatası: {directory_error}", exc_info=True)

        def _fmt_td(td: timedelta) -> str:
            total_seconds = max(0.0, float(td.total_seconds()))
            total_seconds_i = int(total_seconds)
            hours = total_seconds_i // 3600
            minutes = (total_seconds_i % 3600) // 60
            return f"{hours} sa {minutes} dk"

        def _build_training_hint(last_train_dt: Optional[datetime], trained_ok: bool) -> str:
            """
            24 saatlik periyoda göre:
            - eğitim yoksa: uyarı
            - eğitim varsa ve 24s dolmadıysa: kalan süre
            - dolduysa: gecikme
            """
            cycle = timedelta(hours=24)
            now_utc = datetime.now(timezone.utc)

            if not trained_ok or not last_train_dt:
                return "⚠️ AI modeli hazır değil. Tarama yapmadan önce eğitim önerilir."

            # tz-aware yap
            if last_train_dt.tzinfo is None:
                last_train_dt = last_train_dt.replace(tzinfo=timezone.utc)

            age = now_utc - last_train_dt
            if age < cycle:
                remaining = cycle - age
                return (f"💡 Sonraki periyodik eğitime yaklaşık *{_fmt_td(remaining)}* var.\n"
                        f" (En erken {_fmt_td(remaining)} Otomatik Eğitilecektir.)")

            overdue = age - cycle
            return f"💡 Model eğitimi gecikti: *{_fmt_td(overdue)}*. Yeniden eğitim önerilir."

        ai_model_status = "✅ Var" if active_model_count > 0 else "❌ Yok"
        total_models = len(model_files_to_check)
        ai_trained_status = "✅ Eğitimli" if active_model_count == total_models else "❌ Kısmi/Eğitimsiz"
        trained_ok = (active_model_count == total_models) and bool(last_train_dt)
        exchange_status = "✅" if getattr(cls, "exchange", None) else "🔄"
        active_signals = getattr(cls, "active_signals", []) or []
        active_signals_count = sum(1 for s in active_signals if isinstance(s, dict) and s.get("active"))
        training_hint = _build_training_hint(last_train_dt, trained_ok)

        # Klavye
        keyboard = [
            [InlineKeyboardButton("🎓 AI Modelini Eğit", callback_data='retrain_ai')],
            [InlineKeyboardButton("🔍 AI Coin Taraması", callback_data='ai_scan')],
            [InlineKeyboardButton("🧠 Strateji Bazlı Tarama", callback_data='ai_strategy_scan')],
            [InlineKeyboardButton("📈 En Çok Yükselenler", callback_data='top_gainers')],
            [InlineKeyboardButton("📉 En Çok Düşenler", callback_data='top_losers')],
            [InlineKeyboardButton("💰 Diğer Semboller", callback_data='other_symbols')],
            [InlineKeyboardButton("⏰️ Aktif Alarmlar", callback_data='active_alarms')],
            [InlineKeyboardButton("📡 Aktif Sinyaller", callback_data='performance_dashboard')],
            [InlineKeyboardButton("⚙️ Ayarlar", callback_data='show_settings')],
            [InlineKeyboardButton("🧠 Auto-tune Özet", callback_data='autotune_summary')],
            [strategy_button],
            [InlineKeyboardButton("🏠 Ana Menü", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Model detay metni
        model_status_text = f"\n🤖 Model Detayları ({exchange_name.upper()}):\n"
        for model in model_details:
            status_emoji = "✅" if model['status'] else "❌"
            trained_at_text = f" - Eğitim: {model['trained_at']}" if model['trained_at'] else ""
            model_status_text += f"{status_emoji} {model['name'].replace('_', ' ')}{trained_at_text}\n"

        # Mesaj metni
        message_text = (
            f"🤖 AI ALARM SİSTEMİ\n\n"
            f"🏦 Borsa: {exchange_name.upper()} {exchange_status}\n"
            f"🧠 AI Model: {ai_model_status}\n"
            f"🎓 Eğitim Durumu: {ai_trained_status}\n"
            f"🤖 Aktif Model: {active_model_count}/{len(model_files_to_check)}\n"
            f"📊 Alarmlar: {len(getattr(cls, 'active_symbols', []))}\n"
            f"📈 Aktif Sinyaller: {active_signals_count}\n"
            f"{model_status_text}\n"
            f"{training_hint}\n\n"
            f"Seçiminizi yapın:"
        )

        # Mesajı düzenle/gönder
        if cq and message_id:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
            except telegram_error.BadRequest as e:
                if "Message to delete not found" not in str(e):
                    logging.warning(f"Eski alarm menüsü silinemedi: {e}")
            except Exception as del_err:
                logging.error(f"Mesaj silinirken beklenmedik hata: {del_err}")

        await cls._send_long_text(context.bot, chat_id, message_text, reply_markup=reply_markup)

        return State.ALARM_SETUP

    except Exception as e:
        logging.error(f"❌ show_alarm_menu hatası: {e}", exc_info=True)
        error_message = ("🚨 Alarm menüsü yüklenirken bir hata oluştu.\n"
                         f"Hata Detayı: {str(e)}\n"
                         "Lütfen daha sonra tekrar deneyin veya destek alın.")
        try:
            if update.callback_query:
                await update.callback_query.answer(error_message, show_alert=True)
            elif update.message:
                await update.message.reply_text(error_message)
        except Exception as notify_error:
            logging.error(f"Hata bildirim hatası: {notify_error}", exc_info=True)
        return State.ALARM_SETUP


# show_settings
async def show_settings(cls, update: Update, context):
    from ..alarm_strateji import OlimposStrategy
    cls = OlimposStrategy

    try:
        # --- Temel Durum ---
        ai_status = "✅ Aktif" if cls._ai_model and getattr(cls._ai_model, "is_trained", False) else "❌ Pasif"
        strategy_status = "🟢 Çalışıyor" if cls.is_running else "🔴 Durdu"
        exchange_name = (context.user_data.get("exchange") or "Bilinmiyor").upper()

        # --- Tuner ve Rejim ---
        tuner_mode = ConfigService.control_mode().upper()
        market_regime = getattr(cls, "_market_regime", "Bilinmiyor")

        # --- Son Optimizasyon ve Segment (koru) ---
        optimizer_suggestion = "Veri Yok"
        try:
            opt_result = cls.run_weekly_optimizer(lookback_days=7)
            if opt_result and opt_result.get("status") == "ok" and opt_result.get("suggestions"):
                first_suggestion = opt_result["suggestions"][0]
                optimizer_suggestion = f"{first_suggestion.get('param','?')} -> {first_suggestion.get('action','?')}"
        except Exception as e:
            logging.error(f"[SHOW_SETTINGS_OPT_ERR] {e}")

        segment_stat = "Veri Yok"
        try:
            from analytics.segment_stats import get_segment_manager
            sm = get_segment_manager()  # type: ignore
            top_segment = sm.get_top_segments(by="count", limit=1)
            if top_segment:
                (sid, seg_key), data = top_segment[0]
                segment_stat = f"{sid}:{seg_key} (n={data.get('count', 0)}, R={data.get('ema_exp_r', 0.0):.2f})"
        except (ImportError, AttributeError, Exception) as e:
            logging.error(f"[SHOW_SETTINGS_SEG_ERR] {e}")

        # --- Menü metni ---
        message_text = (
            "⚙️ SISTEM AYARLARI\n\n"
            f"🏦 Borsa: {exchange_name} | 🚀 Strateji: {strategy_status}\n"
            f"🧠 AI Durumu: {ai_status} | 📈 Aktif Alarmlar: {len(getattr(cls, 'active_symbols', []) or [])}\n"
            "──────────────────\n"
            f"🔧 Tuner Kipi: {tuner_mode}\n"
            f"🌊 Piyasa Rejimi: {market_regime}\n"
            "──────────────────\n"
            f"💡 Son Öneri: {optimizer_suggestion}\n"
            f"📊 Popüler Segment: {segment_stat}\n"
        )

        # --- Keyboard ---
        keyboard = [
            [InlineKeyboardButton("🔄 AI Modelini Yeniden Eğit", callback_data="retrain_ai")],
            [InlineKeyboardButton("🛡 Risk / Kill-Switch", callback_data="risk_menu")],
            [InlineKeyboardButton("📈 Performans Analizi", callback_data="show_performance_menu")],
            [InlineKeyboardButton("📊 Sistem İstatistikleri", callback_data="system_stats")],
            [InlineKeyboardButton("🧭 Tuner Kipi", callback_data="show_tuner_mode_menu")],
            [InlineKeyboardButton("⚙️ Manuel Strateji Ayarları", callback_data="show_tf_thresholds_menu")],
            [InlineKeyboardButton("📊 Param Önerisi", callback_data="param_optimize")],
            [InlineKeyboardButton("📊 Segment İstatistikleri", callback_data="segment_stats")],
            [InlineKeyboardButton("🛠 Segment Optimize", callback_data="segment_optimize")],
            [InlineKeyboardButton("🔙 Geri", callback_data="back_to_alarm_menu")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Callback'ı ACK et (loading kalsın istemeyiz)
        await update.callback_query.answer()

        chat_id = update.effective_chat.id

        # 1) Eski menü mesajını sil (başaramazsa sorun değil)
        try:
            await update.callback_query.message.delete()
        except Exception as e:
            logging.warning(f"[SHOW_SETTINGS_DELETE_MENU_WARN] {e}")


        # 3) En son menüyü YENİ mesaj olarak gönder -> butonlar en altta kalır
        await context.bot.send_message(
            chat_id=chat_id,
            text=message_text[:4096],
            reply_markup=reply_markup,
            disable_web_page_preview=True
        )
        return State.ALARM_SETUP
    except Exception as e:
        logging.error(f"❌ show_settings'de hata: {str(e)}", exc_info=True)
        try:
            await update.callback_query.edit_message_text("❌ Ayarlar yüklenirken hata oluştu.")
        except Exception:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Ayarlar yüklenirken hata oluştu.")
        return State.ALARM_SETUP


# show_tuner_mode_menu
async def show_tuner_mode_menu(cls, update, context: CallbackContext):
    _ = cls
    kb = [[InlineKeyboardButton("🔧 MANUAL", callback_data='set_tuner_manual')],
        [InlineKeyboardButton("🤖 AUTO", callback_data='set_tuner_auto')],
        [InlineKeyboardButton("🔙 Geri", callback_data='show_settings')]]
    await update.callback_query.edit_message_text("Tuner Kipi Seçin:", reply_markup=InlineKeyboardMarkup(kb))
    _ = context
    return State.ALARM_SETUP


# handle_set_tuner_mode
async def handle_set_tuner_mode(cls, update, context: CallbackContext, mode):
    _ = cls, context
    # DÜZELTME: Döngüsel importu önlemek için yerel import kullan
    from StrategyAdaptiveTuner import StrategyAdaptiveTuner
    try:
        ConfigService.set("control_mode.active", mode)
        ConfigService.save()
        # Tuner a bildir
        try:
            if hasattr(StrategyAdaptiveTuner, "set_mode"):
                StrategyAdaptiveTuner.set_mode(mode)

        except Exception as e:
            logging.error(f"Hata: {e}")

        pass
        await update.callback_query.edit_message_text(f"✅ Tuner kipi güncellendi: {mode.upper()}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri", callback_data='show_settings')]]))
        return State.ALARM_SETUP
    except Exception as e:
        logging.error(f"[SET_TUNER_MODE_ERR] {e}")
        await update.callback_query.edit_message_text("❌ Tuner kipi güncellenemedi.")
        return State.ALARM_SETUP


# show_tf_thresholds_menu
async def show_tf_thresholds_menu(cls, update, context):
    _ = cls, context

    tfs = ConfigService.get("scans.timeframes", None)
    if not isinstance(tfs, list) or not tfs:
        tfs = ["15m", "1h", "4h"]

    allowed = {'1m', '5m', '15m', '30m', '1h', '4h', '1d'}
    tfs = [str(tf).strip() for tf in tfs if str(tf).strip() in allowed]
    if not tfs:
        tfs = ["15m", "1h", "4h"]

    kb = []
    row = []
    for tf in tfs:
        btn_text = tf_to_tr_label(tf)  # ✅ Türkçe etiket
        row.append(InlineKeyboardButton(btn_text, callback_data=f'tfth_tf_{tf}'))
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)

    kb.append([InlineKeyboardButton("🔙 Geri", callback_data='show_settings')])

    await update.callback_query.edit_message_text(
        "TF seçin:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return State.ALARM_SETUP


# show_tf_thresholds_kind
async def show_tf_thresholds_kind(cls, update, context, tf):
    _ = cls
    context.user_data['tfth_selected_tf'] = tf
    kb = [[InlineKeyboardButton("🧠 AI Eşikleri", callback_data='tfth_ai_edit')],
        [InlineKeyboardButton("📐 Strateji Eşikleri", callback_data='tfth_strat_edit')],
        [InlineKeyboardButton("🔙 Geri", callback_data='show_tf_thresholds_menu')]]
    await update.callback_query.edit_message_text(f"TF: {tf}\nNe düzenlemek istersiniz?",
        reply_markup=InlineKeyboardMarkup(kb))
    return State.ALARM_SETUP


# show_tfth_ai_edit
async def show_tfth_ai_edit(cls, update, context):
    """
    DÜZELTME: Bu fonksiyon artık hem aktif (AUTO) hem de düzenlenebilir (MANUAL)
    ayarları ayrı ayrı gösterir. Butonlar her zaman MANUAL ayarları düzenler.
    """
    _ = cls
    from ..alarm_strateji import OlimposStrategy
    cls = OlimposStrategy

    tf = context.user_data.get('tfth_selected_tf')
    if not tf:
        await update.callback_query.edit_message_text("Zaman aralığı seçilmemiş, lütfen geri dönüp tekrar deneyin.")
        return State.ALARM_SETUP

    active_mode = ConfigService.control_mode()

    # 1. Aktif (geçerli) ayarları al (auto veya manual olabilir)
    active_settings = cls.get_ai_thresholds_from_config(tf)

    # 2. Sadece manuel (düzenlenebilir) ayarları al
    manual_min_conf = ConfigService.get_manual(f"scans.tf_profiles.{tf}.ai_scan.min_conf.value",
        active_settings['min_conf'])
    manual_min_potential = ConfigService.get_manual(f"scans.tf_profiles.{tf}.ai_scan.min_potential_pct.value",
        active_settings['min_potential_pct'])
    manual_min_vol_ratio = ConfigService.get_manual(f"scans.tf_profiles.{tf}.ai_scan.min_volume_ratio.value",
        active_settings['min_volume_ratio'])
    manual_min_vol_usd = ConfigService.get_manual(f"scans.tf_profiles.{tf}.ai_scan.min_volume_usd.value",
        active_settings['min_volume_usd'])

    text = (
        f"*{tf} Zaman Aralığı - AI Scan Ayarları*\n\n"
        f"⚙️ *Şu Anki Geçerli Ayarlar (Mod: {active_mode.upper()})*\n"
        f"────────────────────────\n"
        f"  - Min. Güven: `{active_settings['min_conf']:.2f}`\n"
        f"  - Min. Potansiyel: `{active_settings['min_potential_pct']:.2f}%`\n"
        f"  - Min. Hacim Oranı: `{active_settings['min_volume_ratio']:.2f}`\n"
        f"  - Min. Hacim (USD): `{active_settings['min_volume_usd']:,.0f}`\n\n"
        f"✏️ *Düzenlenebilir Manuel Ayarlar*\n"
        f"────────────────────────\n"
        f"  - Min. Güven: `{manual_min_conf:.2f}`\n"
        f"  - Min. Potansiyel: `{manual_min_potential:.2f}%`\n"
        f"  - Min. Hacim Oranı: `{manual_min_vol_ratio:.2f}`\n"
        f"  - Min. Hacim (USD): `{manual_min_vol_usd:,.0f}`\n\n"
        f"Aşağıdan düzenlemek istediğiniz manuel ayarı seçin."
    )

    kb = [
        [InlineKeyboardButton(f"Min. Güven ({manual_min_conf:.2f})", callback_data='tfth_ai_edit_min_conf')],
        [InlineKeyboardButton(f"Min. Potansiyel ({manual_min_potential:.2f}%)",
            callback_data='tfth_ai_edit_min_potential_pct')],
        [InlineKeyboardButton(f"Min. Hacim USD ({manual_min_vol_usd:,.0f})",
            callback_data='tfth_ai_edit_min_volume_usd')],
        [InlineKeyboardButton(f"Min. Hacim Oranı ({manual_min_vol_ratio:.2f})",
            callback_data='tfth_ai_edit_min_volume_ratio')],
        [InlineKeyboardButton("🔙 Geri", callback_data=f'tfth_tf_{tf}')]
    ]
    reply_markup = InlineKeyboardMarkup(kb)

    if update.callback_query and update.callback_query.message:
        # DÜZELTME: Mesaj ID'sini sakla, böylece değer girildikten sonra silinebilir.
        context.user_data['last_tfth_message_id'] = update.callback_query.message.message_id
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    elif update.message and update.effective_chat:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    return State.ALARM_SETUP


# show_tfth_strat_edit
async def show_tfth_strat_edit(cls, update, context):
    _ = cls
    from ..alarm_strateji import OlimposStrategy
    cls = OlimposStrategy

    tf = context.user_data.get('tfth_selected_tf')
    if not tf:
        await update.callback_query.edit_message_text("Zaman aralığı seçilmemiş, lütfen geri dönüp tekrar deneyin.")
        return State.ALARM_SETUP

    active_mode = ConfigService.control_mode()

    # 1. Aktif ayarları al
    active_v1 = cls.get_strat_thresholds_from_config(tf, 'v1')
    active_v2 = cls.get_strat_thresholds_from_config(tf, 'v2')

    # 2. Sadece manuel ayarları al
    manual_v1_min_score = ConfigService.get_manual(f"scans.tf_profiles.{tf}.strategy_scan.v1.min_score.value",
        active_v1['min_score'])
    manual_v2_min_score = ConfigService.get_manual(f"scans.tf_profiles.{tf}.strategy_scan.v2.min_score.value",
        active_v2['min_score'])

    text = (
        f"*{tf} Zaman Aralığı - Strateji Scan Ayarları*\n\n"
        f"⚙️ *Şu Anki Geçerli Ayarlar (Mod: {active_mode.upper()})*\n"
        f"────────────────────────\n"
        f"  - V1 Min. Skor: `{active_v1['min_score']}`\n"
        f"  - V2 Min. Skor: `{active_v2['min_score']}`\n\n"
        f"✏️ *Düzenlenebilir Manuel Ayarlar*\n"
        f"────────────────────────\n"
        f"  - V1 Min. Skor: `{manual_v1_min_score}`\n"
        f"  - V2 Min. Skor: `{manual_v2_min_score}`\n\n"
        f"Aşağıdan düzenlemek istediğiniz manuel ayarı seçin."
    )

    kb = [
        [InlineKeyboardButton(f"V1 Min Skor ({manual_v1_min_score})", callback_data='tfth_strat_edit_v1_min_score')],
        [InlineKeyboardButton(f"V2 Min Skor ({manual_v2_min_score})", callback_data='tfth_strat_edit_v2_min_score')],
        [InlineKeyboardButton("🔙 Geri", callback_data=f'tfth_tf_{tf}')]
    ]
    reply_markup = InlineKeyboardMarkup(kb)

    if update.callback_query and update.callback_query.message:
        context.user_data['last_tfth_message_id'] = update.callback_query.message.message_id
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    elif update.message and update.effective_chat:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    return State.ALARM_SETUP


# show_performance_dashboard
async def show_performance_dashboard(cls, update, context):
    """
    GELİŞMİŞ DASHBOARD
    - İlk ekranda doğrudan tam sayfa (cache varsa) gelir
    - Arkada fiyatlar güncellenip yeniden render edilebilir (Yenile tuşu)
    - Sayfalama destekli
    """
    try:
        if update.callback_query:
            chat_id = update.callback_query.message.chat.id
            is_callback = True
        else:
            chat_id = update.effective_chat.id
            is_callback = False

        # Cache varsa hemen kullan
        try:
            ticker_map = await cls.get_cached_tickers()

        except Exception as e:
            logging.error(f"Hata: {e}")
            ticker_map = {}

        # 24s anchored kapanan sinyaller
        closed = cls.load_recent_closed_signals(
            hours=24,
            anchor_hour=cls.DASHBOARD_ANCHOR_HOUR
        )
        logging.info(f"[DASH_CLOSED_DEBUG] closed_len={len(closed)} anchor={cls.DASHBOARD_ANCHOR_HOUR}")

        # Sayfaları oluştur
        pages, summary = cls._build_dashboard_pages(ticker_map, closed)
        cls._dashboard_pages[chat_id] = {
            'pages':pages,
            'current':0,
            'generated_at':datetime.now(timezone.utc)
        }

        # İlk mesaj
        if is_callback:
            msg = await update.callback_query.edit_message_text(
                text=pages[0][:4096],
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📅 Günlük", callback_data='perf_day'),
                        InlineKeyboardButton("🗓 Haftalık", callback_data='perf_week')],
                    [InlineKeyboardButton("📆 Aylık", callback_data='perf_month'),
                        InlineKeyboardButton("📈 Yıllık", callback_data='perf_year')],
                    [InlineKeyboardButton("🎯 Özel Aralık", callback_data='perf_range'),
                        InlineKeyboardButton("📤 Excel", callback_data='perf_excel')],
                    [InlineKeyboardButton("♻️ Yenile", callback_data='dash_refresh'),
                        InlineKeyboardButton("🔙 Geri", callback_data='back_to_alarm_menu')]
                ])
            )
        else:
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text=pages[0][:4096],
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📅 Günlük", callback_data='perf_day'),
                        InlineKeyboardButton("🗓 Haftalık", callback_data='perf_week')],
                    [InlineKeyboardButton("📆 Aylık", callback_data='perf_month'),
                        InlineKeyboardButton("📈 Yıllık", callback_data='perf_year')],
                    [InlineKeyboardButton("🎯 Özel Aralık", callback_data='perf_range'),
                        InlineKeyboardButton("📤 Excel", callback_data='perf_excel')],
                    [InlineKeyboardButton("♻️ Yenile", callback_data='dash_refresh'),
                        InlineKeyboardButton("🔙 Geri", callback_data='back_to_alarm_menu')]
                ])
            )
        cls._last_dashboard_message[chat_id] = msg.message_id

        # Eğer 1'den fazla sayfa varsa navigation ekle
        if len(pages) > 1:
            await cls.show_dashboard_page(context, chat_id, 0)

        return State.ALARM_SETUP

    except Exception as e:
        logging.error(f"Dashboard gösterim hatası: {e}")
        try:
            if update.callback_query:
                await update.callback_query.edit_message_text("❌ Dashboard hata oluştu.")
            else:
                await context.bot.send_message(chat_id=update.effective_chat.id,
                    text="❌ Dashboard hata oluştu.")

        except Exception as e:
            logging.error(f"Hata: {e}")
            pass
        return State.ALARM_SETUP


# show_dashboard_page
async def show_dashboard_page(cls, context, chat_id: int, page_index: int):
    data = cls._dashboard_pages.get(chat_id)
    if not data:
        return
    pages = data['pages']
    if not pages:
        return
    if page_index < 0:
        page_index = 0
    if page_index >= len(pages):
        page_index = len(pages) - 1
    data['current'] = page_index
    text = pages[page_index]
    if len(text) > 4096:
        text = text[:4090] + "..."

    # Navigasyon butonları
    nav = []
    if len(pages) > 1:
        logging.info(f"[DASH_NAV] next from page={data['current']}")
        left = InlineKeyboardButton("⬅️", callback_data='dash_prev') if page_index > 0 else (
            InlineKeyboardButton("·", callback_data='noop'))
        right = InlineKeyboardButton("➡️", callback_data='dash_next') if page_index < len(pages) - 1 else (
            InlineKeyboardButton("·", callback_data='noop'))

        nav.append([left, InlineKeyboardButton(f"{page_index + 1}/{len(pages)}", callback_data='noop'), right])

    nav.append([InlineKeyboardButton("♻️ Yenile", callback_data='dash_refresh'),
        InlineKeyboardButton("🔙 Geri", callback_data='back_to_alarm_menu')])
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=cls._last_dashboard_message.get(chat_id),
            text=text[:4096],

            reply_markup=InlineKeyboardMarkup(nav)
        )
    except Exception as e:
        logging.error(f"Dashboard sayfa güncelle hatası: {e}")


# show_alarm_reports
async def show_alarm_reports(cls, update: Update, _context):
    _ = cls
    """
    Alarm raporlarını göster - Kısa ve öz versiyon
    """
    try:
        # Rapor yöneticisini oluştur
        from StrategyAdaptiveTuner import AlarmRaporManager
        rapor_manager = AlarmRaporManager()

        # Mevcut ay ve yıl
        current_year = datetime.now(timezone.utc).year
        current_month = datetime.now(timezone.utc).month

        # Aylık raporları al
        aylik_raporlar = rapor_manager.get_aylik_raporlar(current_year, current_month)

        # Rapor mesajı oluştur
        message_text = f"📊 ALARM RAPORLARI - {current_year} {current_month}. Ay\n\n"

        if aylik_raporlar:
            # Toplam performans hesapla
            toplam_kar = 0
            toplam_zarar = 0
            toplam_sinyal = len(aylik_raporlar)
            tamamlanan_hedefler = 0

            message_text += "🔍 ÖZET RAPOR:\n\n"

            for i, rapor in enumerate(aylik_raporlar[:5], 1):  # İlk 5 raporu göster
                sembol = rapor.get('sembol', 'Bilinmiyor')
                sinyal_turu = rapor.get('sinyal_turu', 'Bilinmiyor')
                hedefler = rapor.get('hedefler', [])
                hedef_vuruslari = rapor.get('hedef_vuruslari', [False] * len(hedefler))

                # Tamamlanan hedef sayısı
                tamamlanan_hedef_sayisi = sum(hedef_vuruslari)
                tamamlanan_hedefler += tamamlanan_hedef_sayisi

                # Kar/zarar bilgileri
                kar_zararlari = rapor.get('kar_zararlari', {})
                kar = kar_zararlari.get('toplam_kar', 0)
                zarar = kar_zararlari.get('toplam_zarar', 0)
                toplam_kar += kar
                toplam_zarar += zarar

                # Kısa rapor formatı
                message_text += (
                    f"{i}. 🔍 {sembol} ({sinyal_turu})\n"
                    f"   🎯 Hedefler: {tamamlanan_hedef_sayisi}/{len(hedefler)}\n"
                    f"   💰 Kar: %{kar:.2f} | 📉 Zarar: %{zarar:.2f}\n\n"
                )

            # Performans özeti - Daha kısa
            message_text += "📈 PERFORMANS:\n"
            message_text += f"   💰 Toplam Kar: %{toplam_kar:.2f}\n"
            message_text += f"   📉 Toplam Zarar: %{toplam_zarar:.2f}\n"
            message_text += f"   🎯 Toplam Hedef: {tamamlanan_hedefler}\n"

            # Tüm raporları görmek için uyarı
            if toplam_sinyal > 5:
                message_text += f"\n⚠️ Toplam {toplam_sinyal} rapor içinden ilk 5'i gösteriliyor.\n"

        else:
            message_text += "📋 Henüz rapor bulunmamaktadır.\n"
            message_text += "💡 Alarm hedefleri takip ediliyor.\n"

        keyboard = [
            [
                InlineKeyboardButton("📊 Haftalık", callback_data='weekly_reports'),
                InlineKeyboardButton("📈 Detaylar", callback_data='monthly_details')
            ],
            [InlineKeyboardButton("🔙 Geri", callback_data='back_to_alarm_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Mesaj uzunluğunu kontrol et
        if len(message_text) > 4096:
            message_text = message_text[:4000] + "... (Devamı için detaylı rapora bakın)"

        await update.callback_query.edit_message_text(
            text=message_text,
            reply_markup=reply_markup
        )

        return State.ALARM_SETUP

    except Exception as e:
        logging.error(f"❌ show_alarm_reports hatası: {str(e)}")
        try:
            await update.callback_query.edit_message_text("❌ Raporlar yüklenirken hata oluştu.")
        except (TypeError, ValueError):
            # Eğer mesaj düzenlenemezse yeni mesaj gönder
            await update.message.reply_text("❌ Raporlar yüklenirken hata oluştu.")
        return State.ALARM_SETUP


# handle_retrain_ai
async def handle_retrain_ai(cls, update: Update, context: CallbackContext):
    """
    AI modelini yeniden eğitme işlemini yönet
    """
    query = update.callback_query

    # --- DÜZELTME: İlk mesaja Ana Menü butonu eklendi ---
    keyboard_loading = [[InlineKeyboardButton("🏠 Ana Menü", callback_data="main_menu")]]
    reply_markup_loading = InlineKeyboardMarkup(keyboard_loading)

    await query.edit_message_text("🎓 AI modeli yeniden eğitiliyor...\nBotu kullanmaya devam edebilirsiniz.",
        reply_markup=reply_markup_loading)

    # DÜZELTME: Context'ten borsa adını al ve doğru parametre olarak gönder.
    exchange_name = context.user_data.get('exchange', 'mexc')
    user_id = update.effective_user.id if update.effective_user else None
    # Dinamik eğitim - Artık borsa adını ve kullanıcı ID'sini alıyor
    success = await cls.train_ai_model_dynamic(exchange=exchange_name, triggered_by_user_id=user_id)

    def _check_scaler_status(ai_model):
        return '✅' if (
                ai_model
                and ai_model.scaler
                and hasattr(ai_model.scaler, 'mean_')
        ) else '❌'

    def _get_total_model_count(ai_model):
        return len(ai_model.models) if ai_model else 0

    if success:
        # AI model durumu kontrol et
        active_model_count = sum(
            1 for m in cls._ai_model.models.values() if m is not None
        ) if cls._ai_model else 0

        await query.edit_message_text(
            "✅ AI modeli başarıyla yeniden eğitildi!\n\n"
            f"🎯 Eğitim Sonucu:\n"
            f"• Toplam Model: {_get_total_model_count(cls._ai_model)}\n"
            f"• Eğitilmiş Model: {active_model_count}\n"
            f"• Scaler: {_check_scaler_status(cls._ai_model)}\n\n"
            "Model başarıyla güncellendi!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔍 AI Coin Taraması", callback_data='ai_scan')],
                [InlineKeyboardButton("🔙 Ayarlara Dön", callback_data='show_settings')],
                [InlineKeyboardButton("🏠 Ana Menü", callback_data="main_menu")]
            ])
        )

    else:
        await query.edit_message_text(
            "❌ AI model yeniden eğitimi başarısız!\n\n"
            "Lütfen daha sonra tekrar deneyin.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Tekrar Dene", callback_data='retrain_ai')],
                [InlineKeyboardButton("🔙 Ayarlara Dön", callback_data='show_settings')],
                [InlineKeyboardButton("🏠 Ana Menü", callback_data="main_menu")]
            ])
        )

    return State.ALARM_SETUP


# show_system_stats
async def show_system_stats(cls, update: Update, context):
    """
    Sistem istatistiklerini göster
    """
    _ = context  # Context kullanılır durumda

    try:
        # AI cache bilgileri
        ai_cache_info = "Yok"
        if cls.ai_scan_cache:
            cache_time = cls.ai_scan_cache.get('timestamp')
            if isinstance(cache_time, datetime):
                diff = datetime.now(timezone.utc) - cache_time
                mins = int(diff.total_seconds() // 60)
                secs = int(diff.total_seconds() % 60)
                ai_cache_info = f"{cache_time.strftime('%d.%m %H:%M:%S')} (≈{mins}dk {secs}s önce)"

        # İşlenmiş sinyal sayısı
        processed_signals_count = len(cls.processed_signals)

        message_text = f"📊 SİSTEM İSTATİSTİKLERİ\n\n"
        message_text += f"🤖 AI Sistemi:\n"
        message_text += (f"• Model Durumu: "
                         f"{'Eğitimli' if cls._ai_model and cls._ai_model.is_trained else 'Eğitimsiz'}\n")
        message_text += f"• Son AI Taraması: {ai_cache_info}\n"
        message_text += f"• İşlenmiş Sinyal: {processed_signals_count}\n\n"

        message_text += f"📈 Alarm Sistemi:\n"
        message_text += f"• Aktif Alarmlar: {len(cls.active_symbols)}\n"
        message_text += (f"• AI Önerili: "
                         f"{sum(1 for alarm in cls.active_symbols if alarm.get('ai_suggested', False))}\n")
        message_text += (f"• Manuel Eklenen: "
                         f"{sum(1 for alarm in cls.active_symbols if not alarm.get('ai_suggested', False))}\n\n")

        message_text += f"🔄 Strateji Durumu:\n"
        message_text += f"• Durum: {'🟢 Aktif' if cls.is_running else '🔴 Pasif'}\n"
        message_text += f"• Kanal Sayısı: {len(cls.channel_ids)}\n"
        message_text += f"• Borsa Bağlantısı: {'✅ Aktif' if cls.exchange else '❌ Pasif'}\n\n"

        message_text += f"⚙️ Performans:\n"
        message_text += f"• Timeframe Sayısı: {len(cls.timeframes)}\n"
        message_text += f"• Cache Durumu: {'✅ Dolu' if cls.ai_scan_cache else '❌ Boş'}\n"

        keyboard = [
            [InlineKeyboardButton("🔄 Yenile", callback_data='system_stats')],
            [InlineKeyboardButton("🔙 Geri", callback_data='show_settings')]
        ]
        await update.callback_query.edit_message_text(
            text=message_text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return State.ALARM_SETUP

    except Exception as e:
        logging.error(f"❌ show_system_stats'de hata: {str(e)}")
        try:
            await update.callback_query.edit_message_text(
                "❌ İstatistikler yüklenirken hata oluştu.\nTekrar deneyin.",

                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Yeniden Dene", callback_data='system_stats')],
                    [InlineKeyboardButton("🔙 Geri", callback_data='show_settings')]
                ])
            )

        except Exception as e:
            logging.error(f"Hata: {e}")
            pass
        return State.ALARM_SETUP


# show_active_alarms
async def show_active_alarms(
        cls,
        update: Optional[Update],
        context: CallbackContext,
        chat_id: Optional[int] = None,
        edit_message_id: Optional[int] = None) -> "State":
    """
    Aktif alarmları listeler (active_symbols) + Son tarama sonuçlarını ayrı gösterir (scan_session.results).

    İSTENEN DAVRANIŞ:
    - Aktif sinyallerin tamamı ASLA ayrı bir bölümde listelenmez.
    - Alarm satırı / Tarama satırı için: eğer o sembol aktif sinyallerdeyse
      "⏱️ Sinyal Üretimi Bekleniyor" yerine işlemde bilgisi gösterilir.
      LONG: 🟢⬆️, SHORT: 🔴⬇️
      ID, Açılış, Süre, TP durumları ve realized PnL gösterilir.
    - Scan results'ta row['in_trade'] flag'ine bağımlı kalınmaz.
    """
    try:
        bot = context.bot

        # --- Chat/Message tespiti ---
        query = None
        if update and update.callback_query:
            query = update.callback_query
            try:
                await query.answer()
            except Exception:
                pass
            chat_id = query.message.chat.id
            edit_message_id = query.message.message_id
        elif update and update.effective_chat:
            chat_id = update.effective_chat.id

        if not chat_id:
            chat_id = (context.user_data or {}).get('user_id')
            if not chat_id:
                return State.ALARM_SETUP

        active_alarms = getattr(cls, "active_symbols", []) or []
        active_signals = getattr(cls, "active_signals", []) or []

        # --- Son tarama sonuçları (scan_session) ---
        scan_session = (context.user_data or {}).get('scan_session')
        scan_started = None
        scan_results: list[dict] = []
        if isinstance(scan_session, dict):
            scan_started = scan_session.get('started_at')
            r = scan_session.get('results')
            if isinstance(r, dict):
                scan_results = [x for x in r.values() if isinstance(x, dict)]

        # Hiçbir şey yoksa boş ekran
        if not active_alarms and not scan_results:
            text = (
                "📋 **Aktif Alarm Listeniz Boş**\n\n"
                "Yeni fırsatlar yakalamak için tarama yapın veya manuel olarak alarm ekleyin."
            )
            kb = [
                [InlineKeyboardButton("➕ Manuel Ekle", callback_data='other_symbols')],
                [InlineKeyboardButton("🔍 AI Taraması", callback_data='ai_scan')],
                [InlineKeyboardButton("🧠 Strateji Taraması", callback_data='ai_strategy_scan')],
                [InlineKeyboardButton("🔙 Geri", callback_data='back_to_alarm_menu')]
            ]
            if edit_message_id:
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=edit_message_id,
                        text=text,
                        reply_markup=InlineKeyboardMarkup(kb),
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        reply_markup=InlineKeyboardMarkup(kb),
                        parse_mode=ParseMode.MARKDOWN
                    )
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode=ParseMode.MARKDOWN
                )
            return State.ALARM_SETUP

        # ---------------------------------------------------------
        # Telegram limit koruması
        # ---------------------------------------------------------
        TG_LIMIT = 4096
        SAFE_MARGIN = 240
        max_len = TG_LIMIT - SAFE_MARGIN
        parts: list[str] = []

        def _try_add(block: str) -> bool:
            current = "".join(parts)
            if len(current) + len(block) > max_len:
                return False
            parts.append(block)
            return True

        # ---------------------------------------------------------
        # Helper'lar: Trade görünümü
        # ---------------------------------------------------------
        def _trade_badge(sig: dict) -> str:
            direction = str(sig.get("signal_type", "LONG")).upper()
            return "🟢⬆️" if direction == "LONG" else "🔴⬇️"

        def _tp_marks(sig: dict, max_tp: int = 5) -> str:
            hits = sig.get("targets_hit") or []
            if not isinstance(hits, list) or not hits:
                return "TP: —"
            marks = "".join("✅" if bool(h) else "⭕" for h in hits[:max_tp])
            return f"TP: {marks}"

        def _realized_pct(sig: dict) -> float:
            try:
                entry = float(sig.get("entry_price") or 0.0)
                if entry <= 0:
                    return 0.0
                direction = str(sig.get("signal_type", "LONG")).upper()
                targets = sig.get("targets") or []
                hits = sig.get("targets_hit") or []
                if not isinstance(targets, list) or not isinstance(hits, list):
                    return 0.0

                realized = 0.0
                for i, t in enumerate(targets):
                    if i >= len(hits) or not hits[i]:
                        break
                    t = float(t)
                    if direction == "LONG":
                        realized += (t - entry) / entry * 100.0
                    else:
                        realized += (entry - t) / entry * 100.0
                return float(realized)
            except Exception:
                return 0.0

        def _format_trade_status(sig: dict) -> str:
            badge = _trade_badge(sig)
            sig_id = sig.get("signal_id") or sig.get("id") or "N/A"
            sig_time = cls._ensure_aware(sig.get("signal_time"))
            sig_time_str = sig_time.strftime("%H:%M") if sig_time else "--:--"
            dur_str = cls._human_duration(sig_time, datetime.now(timezone.utc)) if sig_time else "-"
            tp = _tp_marks(sig)
            realized = _realized_pct(sig)
            return (
                f"{badge} İşlemde | ID: `{sig_id}`\n"
                f"└ Açılış: `{sig_time_str}` | Süre: `{dur_str}` | {tp} | Realized: `{realized:+.2f}%`"
            )

        # ---------------------------------------------------------
        # Aktif sinyal map'leri (SADECE eşleştirme için)
        # 1) (norm_symbol, strategy_id) -> signal
        # 2) (norm_symbol, timeframe)  -> signal
        # ---------------------------------------------------------
        active_signals_map_by_sym_sid: dict[tuple[str, str], dict] = {}
        active_signals_map_by_sym_tf: dict[tuple[str, str], dict] = {}

        for s in active_signals:
            try:
                if not isinstance(s, dict) or not s.get("active"):
                    continue
                s_sym = cls.normalize_symbol(s.get("symbol", ""))
                s_tf = str(s.get("timeframe") or "").strip()
                s_sid = str(s.get("strategy_id") or "").lower().strip()
                if s_sym and s_sid:
                    active_signals_map_by_sym_sid[(s_sym, s_sid)] = s
                if s_sym and s_tf:
                    active_signals_map_by_sym_tf[(s_sym, s_tf)] = s
            except Exception:
                continue

        # ---------------------------------------------------------
        # Alarm listesinde bulunan anahtarlar: (norm_symbol, timeframe)
        # Scan results'ta bunları tekrar göstermeyeceğiz
        # ---------------------------------------------------------
        alarm_keys_set: set[tuple[str, str]] = set()
        for a in active_alarms:
            if not isinstance(a, dict):
                continue
            a_sym = cls.normalize_symbol(a.get("symbol", ""))
            a_tf = str(a.get("timeframe") or "").strip()
            if a_sym and a_tf:
                alarm_keys_set.add((a_sym, a_tf))

        # ---------------------------------------------------------
        # Başlık
        # ---------------------------------------------------------
        header = (
            f"📋 **Aktif Alarmlar ({len(active_alarms)})**\n"
            f"🔎 **Son Tarama Sonuçları:** {len(scan_results)}\n\n"
        )
        parts.append(header)

        alarm_buttons: list[list[InlineKeyboardButton]] = []
        skipped_alarm_rows = 0
        skipped_scan_rows = 0

        # ---------------------------------------------------------
        # 1) GERÇEK ALARMLAR (active_symbols)
        # ---------------------------------------------------------
        for i, alarm in enumerate(active_alarms):
            if not isinstance(alarm, dict):
                continue

            symbol = alarm.get('symbol', 'UNKNOWN')
            timeframe = str(alarm.get('timeframe', '15m') or '15m').strip()

            raw_strat_id = str(alarm.get('strategy_id') or alarm.get('strategy_hint') or 'V1')
            strategy_id_disp = raw_strat_id.upper()
            strategy_id_key = raw_strat_id.lower().strip()

            alarm_id = alarm.get('alarm_id', 'N/A')

            meta = alarm.get("meta", {}) or {}
            source = meta.get('source', 'manual')
            if source == 'ai_scan':
                source_line = "🤖 AI Coin Tarama"
            elif source == 'strategy_scan':
                source_line = "📐 Strateji Bazlı Tarama"
            else:
                source_line = "👤 Manuel Ekleme"

            clean_symbol = cls.to_display_symbol(symbol) if hasattr(cls, "to_display_symbol") else str(symbol).split(':')[0]

            created_at = cls._ensure_aware(alarm.get('created_at') or alarm.get('signal_time'))
            created_str = created_at.strftime('%H:%M') if created_at else "--:--"

            info_line = f"`{strategy_id_disp}` | `{timeframe}` | **{clean_symbol}** | `{created_str}` | `{alarm_id}`"

            # Meta satırı
            meta_parts = []
            if source == 'ai_scan':
                if 'ai_confidence' in meta:
                    meta_parts.append(f"AI: {meta['ai_confidence']:.2f}")
                if 'potential_pct' in meta:
                    pot = (meta.get('potential_pct') or 0.0) * 100
                    meta_parts.append(f"Pot: %{pot:.1f}")
                if 'technical_score' in meta:
                    meta_parts.append(f"Teknik: {meta['technical_score']:.0f}")
                if 'volume_usd' in meta:
                    meta_parts.append(f"Hacim: ${meta['volume_usd']:,.0f}")
            elif source == 'strategy_scan':
                if 'v1_score' in meta:
                    meta_parts.append(f"V1: {meta['v1_score']:.1f}")
                if 'v2_score' in meta:
                    meta_parts.append(f"V2: {meta['v2_score']:.1f}")
                if 'volume_usd' in meta:
                    meta_parts.append(f"Hacim: ${meta['volume_usd']:,.0f}")

            meta_line = f"└ {' | '.join(meta_parts)}" if meta_parts else ""

            # ✅ İşlemde mi? (önce sym+strategy, sonra sym+tf)
            norm_alarm_sym = cls.normalize_symbol(symbol)
            signal_obj = active_signals_map_by_sym_sid.get((norm_alarm_sym, strategy_id_key))
            if not signal_obj:
                signal_obj = active_signals_map_by_sym_tf.get((norm_alarm_sym, timeframe))

            is_in_trade = signal_obj is not None
            status_line = _format_trade_status(signal_obj) if is_in_trade else "⏱️ Sinyal Üretimi Bekleniyor"

            block = f"{source_line}\n{info_line}\n"
            if meta_line:
                block += f"{meta_line}\n"
            block += f"{status_line}\n"
            block += "──────────────────────────────\n"

            if not _try_add(block):
                skipped_alarm_rows += 1
                continue

            # Sil butonu sadece sinyal bekleyen alarm için
            if not is_in_trade:
                alarm_buttons.append(
                    [InlineKeyboardButton(f"🗑️ {clean_symbol} Sil", callback_data=f'remove_alarm_id_{alarm_id}')]
                )

        # ---------------------------------------------------------
        # 2) SON TARAMA SONUÇLARI (sil butonsuz)
        # ---------------------------------------------------------
        if scan_results:
            filtered_scan = []
            for row in scan_results:
                try:
                    rsym = cls.normalize_symbol(row.get("symbol", ""))
                    rtf = str(row.get("timeframe") or "").strip()
                    if rsym and rtf and (rsym, rtf) in alarm_keys_set:
                        continue
                    filtered_scan.append(row)
                except Exception:
                    continue

            filtered_scan = sorted(
                filtered_scan,
                key=lambda x: str(x.get('created_at') or ''),
                reverse=True
            )[:20]

            ts_disp = "--:--"
            try:
                dt = cls._ensure_aware(scan_started)
                ts_disp = dt.strftime("%H:%M") if dt else "--:--"
            except Exception:
                pass

            if filtered_scan:
                if not _try_add(f"\n🔎 **Son Tarama Sonuçları** (başlangıç: `{ts_disp}`)\n──────────────────────────────\n"):
                    skipped_scan_rows += len(filtered_scan)
                else:
                    for row in filtered_scan:
                        try:
                            sym = str(row.get('symbol') or 'UNKNOWN')
                            norm_sym = cls.normalize_symbol(sym)
                            tf = str(row.get('timeframe') or '15m').strip()

                            sid_raw = str(row.get('strategy_id') or 'v1')
                            sid_key = sid_raw.lower().strip()
                            sid_disp = sid_raw.upper()

                            alarm_id = row.get('alarm_id', 'N/A')

                            created_at = cls._ensure_aware(row.get('created_at'))
                            created_str = created_at.strftime('%H:%M') if created_at else "--:--"

                            clean_symbol = cls.to_display_symbol(sym) if hasattr(cls, "to_display_symbol") else sym.split(':')[0]

                            src = str(row.get("source") or "")
                            if src == "ai_scan":
                                source_line = "🤖 AI Coin Tarama (Tarama Sonucu)"
                            elif src == "strategy_scan":
                                source_line = "📐 Strateji Bazlı Tarama (Tarama Sonucu)"
                            else:
                                source_line = "🧾 Tarama Sonucu"

                            meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
                            meta_parts = []
                            if src == "ai_scan":
                                if 'ai_confidence' in meta:
                                    meta_parts.append(f"AI: {meta['ai_confidence']:.2f}")
                                if 'potential_pct' in meta:
                                    pot = (meta.get('potential_pct') or 0.0) * 100
                                    meta_parts.append(f"Pot: %{pot:.1f}")
                                if 'technical_score' in meta:
                                    meta_parts.append(f"Teknik: {meta['technical_score']:.0f}")
                                if 'volume_usd' in meta:
                                    meta_parts.append(f"Hacim: ${meta['volume_usd']:,.0f}")
                            elif src == "strategy_scan":
                                if 'v1_score' in meta:
                                    meta_parts.append(f"V1: {meta['v1_score']:.1f}")
                                if 'v2_score' in meta:
                                    meta_parts.append(f"V2: {meta['v2_score']:.1f}")
                                if 'volume_usd' in meta:
                                    meta_parts.append(f"Hacim: ${meta['volume_usd']:,.0f}")
                            meta_line = f"└ {' | '.join(meta_parts)}" if meta_parts else ""

                            # ✅ row.in_trade'a bağlı kalma: map'ten bul
                            sig_obj = None
                            if norm_sym:
                                sig_obj = active_signals_map_by_sym_tf.get((norm_sym, tf))
                                if not sig_obj:
                                    sig_obj = active_signals_map_by_sym_sid.get((norm_sym, sid_key))

                            status_line = _format_trade_status(sig_obj) if sig_obj else "⏱️ Sinyal Üretimi Bekleniyor"

                            info_line = f"`{sid_disp}` | `{tf}` | **{clean_symbol}** | `{created_str}` | `{alarm_id}`"

                            block = f"{source_line}\n{info_line}\n"
                            if meta_line:
                                block += f"{meta_line}\n"
                            block += f"{status_line}\n"
                            block += "──────────────────────────────\n"

                            if not _try_add(block):
                                skipped_scan_rows += 1
                                continue

                        except Exception:
                            continue

        # Kırpma olduysa bilgi
        if skipped_alarm_rows or skipped_scan_rows:
            tail = "\n⚠️ _Telegram mesaj limiti nedeniyle liste kısaltıldı._\n"
            if skipped_alarm_rows:
                tail += f"• Gösterilemeyen alarm satırı: **{skipped_alarm_rows}**\n"
            if skipped_scan_rows:
                tail += f"• Gösterilemeyen tarama satırı: **{skipped_scan_rows}**\n"
            _try_add(tail)

        message_text = "".join(parts)

        # ---------------------------------------------------------
        # Kontrol butonları
        # ---------------------------------------------------------
        if cls.is_running:
            strategy_button = InlineKeyboardButton("🛑 Stratejiyi Durdur (Çalışıyor)", callback_data='stop_strategy')
        else:
            strategy_button = InlineKeyboardButton("▶️ Stratejiyi Başlat (Durdu)", callback_data='start_ai_strategy')

        control_rows = [
            [InlineKeyboardButton("➕ Manuel Ekle", callback_data='other_symbols')],
            [InlineKeyboardButton("🔍 AI Taraması", callback_data='ai_scan')],
            [InlineKeyboardButton("🧠 Strateji Taraması", callback_data='ai_strategy_scan')],
            [strategy_button],
            [
                InlineKeyboardButton("🗑️ Tümünü Sil", callback_data='clear_all_alarms'),
                InlineKeyboardButton("🔙 Geri", callback_data='back_to_alarm_menu')
            ]
        ]

        full_keyboard = alarm_buttons + control_rows
        reply_markup = InlineKeyboardMarkup(full_keyboard)

        if edit_message_id:
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=edit_message_id,
                    text=message_text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                await bot.send_message(
                    chat_id=chat_id,
                    text=message_text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN
                )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=message_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )

        return State.ALARM_SETUP

    except Exception as e:
        logging.error(f"show_active_alarms hatası: {e}", exc_info=True)
        return State.ALARM_SETUP


async def toggle_strategy_wrapper(cls, update: Update, context: CallbackContext, should_start: bool):
    """
    Stratejiyi başlatır/durdurur ve EKRANI YENİLER (Başka menüye gitmez).
    """
    query = update.callback_query

    if should_start:
        if cls.is_running:
            await query.answer("⚠️ Strateji zaten çalışıyor.", show_alert=True)
        else:
            # Arka planda başlat (Loop'a girmemesi için create_task)
            asyncio.create_task(cls.run_ai_strategy(context))
            # UI'ın güncellenmesi için kısa bir bekleme (loop'un is_running=True yapması için)
            await asyncio.sleep(0.5)
            await query.answer("✅ Strateji Başlatıldı!")
    else:
        if not cls.is_running:
            await query.answer("⚠️ Strateji zaten durmuş.", show_alert=True)
        else:
            # Stop işlemi (cls.stop_strategy fonksiyonunu kullanabilirsin veya direkt bayrağı indir)
            cls.is_running = False
            cls.run_ai_strategy_active = False
            await query.answer("🛑 Strateji Durduruluyor...")
            # Loop'un durması için biraz bekle
            await asyncio.sleep(0.5)

    # EKRANI YENİLE (Start/Stop butonu metni değişecek)
    # Burası önemli: `show_active_alarms` çağırarak mevcut ekranda kalıyoruz.
    return await show_active_alarms(cls, update, context)


# show_symbols
async def show_symbols(cls, update: 'Update', context, symbol_type: str):
    """
    Diğer Semboller: Yalnızca USDT-settled futures; sembol formatı bozulmadan, hacim/price güvenli.
    AI son tarama ikonlarını (BUY=🚀, SELL=📉, HOLD=⚡) gösterir.
    DÜZELTME: İç fonksiyondaki parametre adı, dış kapsamdaki değişkenle çakışmaması için değiştirildi.
    """
    try:
        if not cls.exchange:
            await cls.initialize_exchange(context)

        context.user_data['last_symbol_type'] = symbol_type
        await update.callback_query.edit_message_text(f"📊 {symbol_type.title()} listesi yükleniyor...")

        futures_symbols = await cls.get_futures_symbols_only()
        logging.info(f"🔍 Toplam futures sembol sayısı: {len(futures_symbols)}")

        async def safe_fetch_tickers():
            try:
                cached_tickers = await cls.get_cached_tickers()
                return cached_tickers if isinstance(cached_tickers, dict) else {}
            except Exception as error:
                logging.error(f"Ticker alma hatası: {error}")
                return {}

        tickers = await safe_fetch_tickers()
        logging.info(f"📊 Toplam ticker sayısı: {len(tickers)}")

        filtered_symbols = []

        # ✅ YENİ MİMARİ: minimum hacim eşiklerini scans.tf_profiles.<tf>.ai_scan.min_volume_usd.value'dan oku
        tf_candidate = ConfigService.get("CANDIDATE_SELECTION.timeframe_candidate", "15m")
        tf_candidate = str(tf_candidate or "15m").strip()

        # ai_scan min_volume_usd aktif değeri (manual/auto ayrımını ConfigService yapıyorsa ai_settings(tf) kullan)
        try:
            ai_th = cls.get_ai_thresholds_from_config(tf_candidate)  # scans.tf_profiles.<tf>.ai_scan
            min_vol = float(ai_th.get("min_volume_usd", 2_000_000) or 2_000_000)
        except Exception:
            min_vol = 2_000_000.0

        logging.info(f"💰 Minimum hacim (tf={tf_candidate}): {min_vol:,.0f} USDT")

        for symbol_data in futures_symbols:
            try:
                symbol_name = symbol_data['symbol'] if isinstance(symbol_data, dict) else str(symbol_data)

                t = tickers.get(symbol_name)
                if not isinstance(t, dict) or not t:
                    base_spot = symbol_name.split(':')[0] if ':' in symbol_name else symbol_name
                    t = tickers.get(base_spot, {})

                vol = float(cls._safe_ticker_volume_usd(t))
                last_price = float((t or {}).get('last') or (t or {}).get('close') or 0.0)
                change_24h = float((t or {}).get('percentage') or 0.0)

                if vol >= min_vol:
                    filtered_symbols.append({
                        'symbol':symbol_name,
                        'volume_24h_usdt':vol,
                        'price':last_price,
                        'change_24h':change_24h,
                        'market_type':'futures'
                    })
            except Exception as e:
                logging.error(f"Sembol filtreleme hatası: {symbol_data} - {e}")

        # Sıralama
        if symbol_type == 'gainers':
            filtered_symbols.sort(key=lambda x:x['change_24h'], reverse=True)
            title = "🟢 EN ÇOK YÜKSELENLER"
            filtered_symbols = filtered_symbols[:25]
        elif symbol_type == 'losers':
            filtered_symbols.sort(key=lambda x:x['change_24h'])
            title = "🔴 EN ÇOK DÜŞENLER"
            filtered_symbols = filtered_symbols[:25]
        else:
            filtered_symbols.sort(key=lambda x:x['volume_24h_usdt'], reverse=True)
            title = "💰 YÜKSEK HACİMLİLER"
            filtered_symbols = filtered_symbols[:40]

        if not filtered_symbols:
            await update.callback_query.edit_message_text(
                "❌ Kriterlere uyan vadeli işlem coini bulunamadı.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Yeniden Dene", callback_data='other_symbols')],
                    [InlineKeyboardButton("🔙 Geri", callback_data='back_to_alarm_menu')]
                ])
            )
            return State.ALARM_SETUP

        ai_meta_map = {}
        if cls._ai_model and cls._ai_model.is_trained:
            logging.info(f"🤖 {len(filtered_symbols)} sembol için anlık AI potansiyel analizi başlatılıyor...")

            # DÜZELTME: Parametre adı 's_data' olarak değiştirildi.
            async def analyze_symbol_potential(s_data):
                try:
                    ai_result = await cls._analyze_single_coin_with_real_ai_safe(s_data['symbol'], timeframe='15m')
                    if ai_result and ai_result.get('direction'):
                        return s_data['symbol'], ai_result.get('direction')
                except Exception as e1:
                    logging.debug(f"Anlık AI analizi hatası ({s_data['symbol']}): {e1}")
                return s_data['symbol'], None

            analysis_results = await asyncio.gather(*[analyze_symbol_potential(s) for s in filtered_symbols])
            ai_meta_map = {symbol:direction for symbol, direction in analysis_results if direction}
            logging.info(f"✅ Anlık AI analizi tamamlandı. {len(ai_meta_map)} potansiyelli sembol bulundu.")

        def icon_of(signal_code):
            if signal_code == "BUY":
                return "🚀"
            if signal_code == "SELL":
                return "📉"
            return ""

        keyboard: List[List[InlineKeyboardButton]] = []
        for item in filtered_symbols:
            try:
                sym = item['symbol']
                change_24h = item.get('change_24h', 0.0)
                direction_icon = "🟢" if change_24h > 0 else "🔴" if change_24h < 0 else "⚪"
                last_signal = ai_meta_map.get(sym)
                ai_icon = icon_of(last_signal)
                sym_disp = cls.to_display_symbol(sym) if hasattr(cls, "to_display_symbol") else sym
                button_text = f"{direction_icon} {sym_disp} {ai_icon}"

                keyboard.append([InlineKeyboardButton(button_text.strip(),
                    callback_data=f"select_symbol_{sym}")])
            except Exception as e:
                logging.error(f"Buton oluşturma hatası: {item} - {e}")

        keyboard.append([InlineKeyboardButton("🔙 Geri", callback_data='back_to_alarm_menu')])

        await update.callback_query.edit_message_text(
            text=f"{title}\n\n🔮 Toplam: {len(filtered_symbols)}\n💎 Min hacim: {int(min_vol):,} "
                 f"USDT\n\nAlarm kurmak için bir sembol seçin:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return State.SYMBOL_SELECT
    except Exception as e:
        logging.error(f"❌ show_symbols genel hatası: {str(e)}", exc_info=True)
        await update.callback_query.edit_message_text(
            "❌ Vadeli işlem coinleri yüklenirken genel bir hata oluştu.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Yeniden Dene", callback_data='other_symbols')],
                [InlineKeyboardButton("🔙 Geri", callback_data='back_to_alarm_menu')]
            ])
        )
        return State.ALARM_SETUP


# select_timeframe
async def select_timeframe(cls, update: Update, context):
    """
    Context koruma ile timeframe seçimi (Türkçe etiket + ⭐ AI önerisi tutarlı)
    """
    try:
        symbol = context.user_data.get('selected_symbol')

        # CONTEXT KORUMA - Symbol yoksa geri yönlendir
        if not symbol:
            logger.error("❌ Selected symbol context'te bulunamadı!")
            logger.info(f"🔍 DEBUG - Mevcut context: {context.user_data}")

            await update.callback_query.edit_message_text(
                "❌ Sembol bilgisi kayboldu. Lütfen tekrar seçin.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Sembol Seçimi", callback_data='other_symbols')
                ]])
            )
            return State.SYMBOL_SELECT

        # Symbol type'ı context'e kaydet (geri dönüş için)
        context.user_data['last_symbol_type'] = context.user_data.get('last_symbol_type', 'others')

        # CONTEXT'İ KORU - Tekrar kaydet
        context.user_data['selected_symbol'] = symbol

        logger.info(f"🔍 DEBUG - Timeframe seçimi için context: {context.user_data}")

        # AI önerisi al (eğer mevcut ise)
        ai_recommended_timeframe = "15m"  # Varsayılan
        ai_recommendation = ""

        if cls._ai_model and cls._ai_model.is_trained:
            try:
                best_timeframe = await cls.get_ai_recommended_timeframe(symbol)
                if best_timeframe:
                    ai_recommended_timeframe = str(best_timeframe).strip()
                    ai_recommendation = f"\n🤖 AI Önerisi: {tf_to_tr_label(ai_recommended_timeframe)}"
            except Exception as ai_error:
                logger.error(f"❌ AI timeframe önerisi hatası: {ai_error}")

        # ✅ Kullanılacak TF listesi (dinamik: scans.timeframes; fallback sabit)
        tfs = ConfigService.get("scans.timeframes", None)
        if not isinstance(tfs, list) or not tfs:
            tfs = ['1m', '5m', '15m', '30m', '1h', '4h', '1d']

        allowed = {'1m', '5m', '15m', '30m', '1h', '4h', '1d'}
        tfs = [str(tf).strip() for tf in tfs if str(tf).strip() in allowed]
        if not tfs:
            tfs = ['15m', '1h', '4h']

        # Keyboard oluştur (2 sütun)
        keyboard = []
        for i in range(0, len(tfs), 2):
            row = []
            for j in range(2):
                if i + j < len(tfs):
                    tf_code = tfs[i + j]
                    tf_label = tf_to_tr_label(tf_code)  # ✅ tek kaynak: Türkçe etiket

                    # ⭐ AI önerisi
                    if tf_code == ai_recommended_timeframe:
                        button_text = f"{tf_label} ⭐"
                    else:
                        button_text = tf_label

                    row.append(InlineKeyboardButton(button_text, callback_data=f'timeframe_{tf_code}'))
            keyboard.append(row)

        # Geri buton
        keyboard.append([InlineKeyboardButton("🔙 Geri", callback_data='back_to_symbol_select')])
        reply_markup = InlineKeyboardMarkup(keyboard)

        message_text = f"📊 Sembol: {symbol}\n\n"
        message_text += "⏰ Zaman dilimi seçin:"
        message_text += ai_recommendation
        message_text += f"\n\n⭐ Önerilen: {tf_to_tr_label(ai_recommended_timeframe)}"

        await update.callback_query.edit_message_text(
            text=message_text,
            reply_markup=reply_markup
        )

        logger.info(f"✅ DEBUG - Timeframe seçimi gösterildi: {symbol}")
        return State.TIMEFRAME_SELECT

    except Exception as e:
        logger.error(f"❌ select_timeframe DEBUG hatası: {str(e)}", exc_info=True)
        await update.callback_query.edit_message_text("❌ Bir hata oluştu.")
        return State.ALARM_SETUP


# remove_alarm
async def remove_alarm(cls, update: Update, context, index_or_id):
    """
    Geriye dönük uyum:
    - Eski akış: index (int) ile siler
    - Yeni akış: alarm_id (str, ör. 'ALM-...') ile siler
    """
    _ = context
    try:
        if cls.active_symbols is None:
            cls.active_symbols = []

        val = str(index_or_id).strip()

        # 1) alarm_id formatıysa -> ID ile sil
        if val.upper().startswith("ALM-"):
            before = len(cls.active_symbols)
            cls.active_symbols = [
                a for a in cls.active_symbols
                if not (isinstance(a, dict) and str(a.get("alarm_id")) == val)
            ]
            if len(cls.active_symbols) < before:
                try:
                    cls.save_active_signals(force=True)
                except Exception:
                    pass
            return await show_active_alarms(cls, update, context)

        # 2) değilse index gibi parse etmeyi dene
        idx = int(val)
        if 0 <= idx < len(cls.active_symbols):
            removed_alarm = cls.active_symbols.pop(idx)
            logging.info(f"🗑️ Alarm silindi: {removed_alarm}")
            try:
                cls.save_active_signals(force=True)
            except Exception:
                pass
            return await show_active_alarms(cls, update, context)

        await update.callback_query.edit_message_text("❌ Geçersiz alarm indeksi/ID.")
        return State.ALARM_SETUP

    except Exception as e:
        logging.error(f"❌ remove_alarm'da hata: {str(e)}", exc_info=True)
        await update.callback_query.edit_message_text("❌ Alarm silinirken hata oluştu.")
        return State.ALARM_SETUP


# add_alarm_debug
async def add_alarm_debug(
    cls, context, symbol: str, timeframe: str, strategy_id: str, user_id: int, source_meta: dict = None
):
    """
    Watchlist/Alarm kaydı: EP/SL/Targets ile birlikte kanal bildirimi.
    True/False döner. Kullanıcıya bireysel bildirim göndermez, yalnızca kanallara mesaj yollar.

    Davranış:
    - Dedupe anahtarı: (user_id, normalized_symbol, tf)  -> v1/v2 aynı anahtar
    - Converted alarm varsa replace yok.
    - ALARM_DEDUPE_MODE:
        - REJECT: duplicate varsa direkt false
        - REPLACE_IF_BETTER: meta kalite daha iyiyse eskisini kaldırıp yenisini kurar
    - Limitler yeni mimari: scans.tf_profiles.<tf> üzerinden
    - Manual alarmlar limitten muaftır.
    """

    import math
    from datetime import datetime, timezone

    ccxt_symbol = ""
    # ✅ BOOTSCAN global cap (handlers.py start butonu ile tetiklenen tarama)
    try:
        if (context.user_data or {}).get("boot_scan"):
            max_total = int((context.user_data or {}).get("boot_scan_max_total", BOOTSCAN_MAX_TOTAL_DEFAULT) or BOOTSCAN_MAX_TOTAL_DEFAULT)
            cur = int((context.user_data or {}).get("boot_scan_added_count", 0) or 0)
            if cur >= max_total:
                return False
    except Exception:
        pass

    # ---------- normalize + basic guards ----------
    norm_symbol_to_add = cls.normalize_symbol(symbol)
    if not norm_symbol_to_add:
        logging.error(f"[ADD_ALARM_SKIP] Geçersiz sembol: {symbol}")
        return False

    tf = str(timeframe or "").strip()
    if not tf:
        tf = "15m"
    uid = int(user_id)

    # v1/v2 aynı anahtara düşsün (strategy_id yok)
    alarm_key = (uid, norm_symbol_to_add, tf)

    # ---------- lock for concurrent scans ----------
    lock = getattr(cls, "_alarm_locks", None)
    if lock is None:
        class _Dummy:
            async def __aenter__(self): return self
            async def __aexit__(self, exc_type, exc, tb): return False
        lock_ctx = _Dummy()
    else:
        lock_ctx = cls._alarm_locks[alarm_key]

    # ---------- scan_session helpers ----------
    def _scan_session_get():
        try:
            ss = (context.user_data or {}).get("scan_session")
            if isinstance(ss, dict) and ss.get("chat_id"):
                return ss
        except Exception:
            pass
        return None

    def _scan_session_put_row(row_key: str, row: dict):
        ss = _scan_session_get()
        if not ss:
            return
        results = ss.get("results")
        if not isinstance(results, dict):
            ss["results"] = {}
            results = ss["results"]
        results[row_key] = row  # overwrite -> tekilleştirir

    # ---------- state checks ----------
    def _has_active_signal_same_symbol_tf() -> bool:
        """Aktif sinyal varsa: replace/silme yok; ayrıca active_symbols'a persist etmeyebiliriz."""
        try:
            for s in (getattr(cls, "active_signals", None) or []):
                if not isinstance(s, dict):
                    continue
                if not s.get("active"):
                    continue
                if str(s.get("timeframe") or "").strip() != tf:
                    continue
                if cls.normalize_symbol(s.get("symbol", "")) == norm_symbol_to_add:
                    return True
        except Exception:
            pass
        return False

    def _find_existing_alarm_candidate():
        """active_symbols içindeki mevcut alarm adayını bul (V1/V2 fark etmez)."""
        try:
            for a in (getattr(cls, "active_symbols", None) or []):
                if not isinstance(a, dict):
                    continue
                if int(a.get("user_id") or uid) != uid:
                    continue
                if str(a.get("timeframe") or "").strip() != tf:
                    continue
                if cls.normalize_symbol(a.get("symbol", "")) != norm_symbol_to_add:
                    continue

                if str(a.get("status") or "").lower() == "converted":
                    return ("CONVERTED", a)
                return ("CANDIDATE", a)
        except Exception:
            pass
        return (None, None)

    def _norm_potential(x) -> float:
        try:
            v = float(x or 0.0)
            # 8.4 gibi % geldiyse 0.084'e indir
            if 1.0 < abs(v) <= 300.0:
                v = v / 100.0
            return float(v)
        except Exception:
            return 0.0

    def _quality_tuple(meta: dict) -> tuple:
        """
        Küçük tuple = daha iyi.
        """
        m = meta or {}
        ai_conf = float(m.get("ai_confidence", 0.0) or 0.0)
        tech = float(m.get("technical_score", 0.0) or 0.0)
        vr = float(m.get("volume_ratio", 1.0) or 1.0)

        pot = _norm_potential(m.get("potential_pct", m.get("strat_potential", 0.0)))
        vscore = float(m.get("v1_score", m.get("v2_score", m.get("score", 0.0))) or 0.0)

        # yüksek daha iyi -> negatifle
        return (-ai_conf, -pot, -tech, -vr, -vscore)

    # persist kararları
    skip_persist = False
    allow_persist = True

    async with lock_ctx:
        # 1) aktif sinyal var mı? -> varsa listeye yazma
        skip_persist = _has_active_signal_same_symbol_tf()
        if skip_persist:
            logging.info(f"[ADD_ALARM_ACTIVE_SIGNAL_MODE] {symbol} ({tf}) aktif sinyal var → listeye ekleme yok")
            allow_persist = False
        else:
            allow_persist = True

        # 2) dedupe/replace (sadece persist modunda)
        if allow_persist:
            existing_kind, existing_alarm = _find_existing_alarm_candidate()

            if existing_kind == "CONVERTED":
                logging.info(f"[ADD_ALARM_SKIP_CONVERTED] {symbol} ({tf}) converted alarm var → replace yok")
                return False

            if existing_kind == "CANDIDATE" and isinstance(existing_alarm, dict):
                mode = str(getattr(cls, "ALARM_DEDUPE_MODE", "REPLACE_IF_BETTER")).upper()

                if mode == "REJECT":
                    logging.info(f"[ADD_ALARM_DUPLICATE_SKIP] {symbol} ({tf}) aday alarm var → REJECT")
                    return False

                new_q = _quality_tuple(source_meta or {})
                old_q = _quality_tuple(
                    existing_alarm.get("meta") if isinstance(existing_alarm.get("meta"), dict) else {}
                )

                if not (new_q < old_q):
                    logging.info(f"[ADD_ALARM_DUPLICATE_SKIP_NOT_BETTER] {symbol} ({tf}) yeni daha iyi değil → skip")
                    return False

                # yeni daha iyi → eskisini kaldır
                cls.active_symbols = [a for a in (cls.active_symbols or []) if a is not existing_alarm]
                try:
                    cls.save_active_signals(force=True)
                except Exception:
                    pass
                logging.info(f"[ADD_ALARM_REPLACED] {symbol} ({tf}) eski alarm kaldırıldı, yenisi kuruluyor")

    # ---------------- main flow ----------------
    try:
        logging.info(f"[ADD_ALARM_START] {symbol} tf={tf} strategy={strategy_id}")

        # günlük sayaç reset
        today = datetime.now(timezone.utc).date()
        if getattr(cls, "alarm_counter_date", None) != today:
            cls.alarm_counter_date = today
            cls.alarm_counter = 0
            logging.info("[ADD_ALARM_RESET] Günlük sayaç sıfırlandı")

        # symbol normalize + ccxt symbol
        norm_symbol = cls.normalize_symbol(symbol)
        if not norm_symbol:
            logging.error(f"[ADD_ALARM_SKIP] Geçersiz sembol: {symbol}")
            return False

        ccxt_symbol = cls.to_ccxt_symbol(norm_symbol, prefer_futures=True)
        if not ccxt_symbol:
            logging.error(f"[ADD_ALARM_SKIP] CCXT sembol üretilemedi: {norm_symbol}")
            return False

        # ---------- source / manual flag ----------
        meta = source_meta or {}
        if not isinstance(meta, dict):
            meta = {}

        source = "strategy_scan" if meta.get("strategy_scan") else ("ai_scan" if meta.get("ai_scan") else str(meta.get("source") or "auto"))
        if source not in ("manual", "ai_scan", "strategy_scan", "auto"):
            # normalize
            source = "manual" if meta.get("manual_add") else "auto"

        is_manual = (source == "manual") or bool(meta.get("manual_add"))

        # ---------- limit (new architecture) ----------
        # Manual alarm limit muafiyeti
        limit = None
        if not is_manual:
            tf0 = str(tf or "15m").strip()
            sid = "v1" if str(strategy_id).lower() == "v1" else "v2"
            mode = "strategy_scan" if meta.get("strategy_scan") else "ai_scan"

            try:
                tf_profile = ConfigService.tf_profile(tf0, {}) or {}
            except Exception:
                tf_profile = {}

            if mode == "ai_scan":
                ai = (tf_profile.get("ai_scan") or {})
                limits = (ai.get("limits") or {})
                limit = limits.get(sid)
            else:
                st = (tf_profile.get("strategy_scan") or {})
                block = (st.get(sid) or {})
                limit = block.get("limit")

            try:
                limit = int(float(limit))
            except Exception:
                limit = 3

        # limit sayımı: yalnız auto kaynaklar için, ilgili source+sid için
        if allow_persist and (not is_manual) and (limit is not None):
            want_source = "strategy_scan" if meta.get("strategy_scan") else "ai_scan"
            want_sid = "v1" if str(strategy_id).lower() == "v1" else "v2"

            def _alarm_source(a: dict) -> str:
                m = a.get("meta") if isinstance(a.get("meta"), dict) else {}
                return str(m.get("source") or "").strip()

            def _alarm_sid(a: dict) -> str:
                s = a.get("strategy_id") or a.get("strategy_hint") or ""
                s = str(s).lower().strip()
                return "v1" if s == "v1" else "v2"

            active_count = sum(
                1 for a in (getattr(cls, "active_symbols", None) or [])
                if isinstance(a, dict)
                and a.get("active", True)
                and _alarm_source(a) == want_source
                and _alarm_sid(a) == want_sid
            )
            if active_count >= int(limit):
                logging.warning(f"[ADD_ALARM_SKIP_LIMIT] {want_source}/{want_sid} limiti doldu ({active_count}/{limit})")
                return False

        max_backoff = float(getattr(cls, "MAX_BACKOFF_SEC", 8.0))

        # ---------- price fetch ----------
        current_price = 0.0
        try:
            ticker = await cls._retry_async(
                cls.exchange.fetch_ticker,
                ccxt_symbol,
                retries=2,
                base_delay=0.6,
                max_delay=max_backoff
            )
            current_price = safe_price_from_ticker(ticker, 0.0)
        except Exception as price_err:
            logging.error(f"[ADD_ALARM_PRICE_ERR] {ccxt_symbol}: {price_err}")
            current_price = safe_float(meta.get("entry_price"), 0.0)

        # fallback: 1m last close
        if current_price <= 0:
            try:
                df_fallback = await cls.fetch_ohlcv_with_retry(ccxt_symbol, timeframe="1m", max_retries=2, timeout=10)
                if df_fallback is not None and not df_fallback.empty:
                    current_price = float(df_fallback["close"].iloc[-1])
                    logging.info(f"[ADD_ALARM_PRICE_FALLBACK] {ccxt_symbol} EP via 1m close -> {current_price}")
            except Exception as e_fb:
                logging.error(f"[ADD_ALARM_PRICE_FALLBACK_ERR] {ccxt_symbol}: {e_fb}")

        if current_price <= 0:
            logging.error(f"[ADD_ALARM_SKIP] {ccxt_symbol} fiyat bulunamadı")
            return False

        # ---------- direction / entry / SL / TP ----------
        direction = str(meta.get("direction", meta.get("signal_type", "LONG"))).upper()
        if direction not in ("LONG", "SHORT"):
            direction = "LONG"

        entry_price = safe_float(meta.get("entry_price"), current_price)
        stop_loss = safe_float(meta.get("stop_loss"), 0.0)

        sl_method = "Bilinmiyor"

        # Stop Loss: stratejiden gelmediyse config safety net
        if stop_loss <= 0:
            try:
                val = ConfigService.get("strategy.stop_loss", 3.0)
                def_sl_pct = float(val)
            except Exception:
                def_sl_pct = 3.0

            if direction == "SHORT":
                stop_loss = entry_price * (1 + (def_sl_pct / 100.0))
            else:
                stop_loss = entry_price * (1 - (def_sl_pct / 100.0))

            sl_method = f"Güvenlik Ağı (%{def_sl_pct})"
        else:
            mode0 = meta.get("target_mode")
            if mode0 == "atr":
                sl_method = "ATR"
            elif mode0 == "percent":
                sl_method = "Sabit %"
            elif meta.get("weighted"):
                sl_method = "ATR (V1)"
            else:
                sl_method = "Strateji"

        # Targets
        raw_targets = meta.get("targets")
        targets: list[float] = []
        tp_method = "Bilinmiyor"

        is_empty_targets = (
            raw_targets is None
            or (isinstance(raw_targets, (list, tuple, set)) and len(raw_targets) == 0)
            or (isinstance(raw_targets, str) and len(raw_targets.strip()) == 0)
        )

        if is_empty_targets:
            try:
                tp_pcts = [
                    float(ConfigService.get("strategy.take_profit1", 5.0)) / 100.0,
                    float(ConfigService.get("strategy.take_profit2", 10.0)) / 100.0,
                    float(ConfigService.get("strategy.take_profit3", 20.0)) / 100.0,
                    float(ConfigService.get("strategy.take_profit4", 35.0)) / 100.0,
                    float(ConfigService.get("strategy.take_profit5", 50.0)) / 100.0,
                ]
            except Exception:
                tp_pcts = [0.05, 0.10, 0.20, 0.35, 0.50]

            if direction == "SHORT":
                targets = [entry_price * (1 - p) for p in tp_pcts]
            else:
                targets = [entry_price * (1 + p) for p in tp_pcts]

            tp_method = "Güvenlik Ağı (JSON Ayarları)"
        else:
            try:
                targets = ensure_float_list(raw_targets, [])
                if not targets:
                    tp_pcts = [0.05, 0.10, 0.20, 0.35, 0.50]
                    if direction == "SHORT":
                        targets = [entry_price * (1 - p) for p in tp_pcts]
                    else:
                        targets = [entry_price * (1 + p) for p in tp_pcts]
                    tp_method = "Güvenlik Ağı (Fallback)"
                else:
                    mode0 = meta.get("target_mode")
                    if mode0 == "atr":
                        tp_method = "ATR"
                    elif mode0 == "percent":
                        tp_method = "Sabit %"
                    elif meta.get("fallback_target") == "fib":
                        tp_method = "Fibonacci"
                    elif meta.get("weighted"):
                        tp_method = "ATR (V1)"
                    else:
                        tp_method = "Strateji"
            except Exception:
                targets = []

        # type safety
        try:
            entry_price = float(entry_price or 0.0)
            stop_loss = float(stop_loss or 0.0)
        except (TypeError, ValueError):
            entry_price = 0.0
            stop_loss = 0.0

        # SL sanity
        if direction == "LONG":
            if stop_loss >= entry_price and entry_price > 0:
                stop_loss = entry_price * 0.97
        else:
            if stop_loss <= entry_price and entry_price > 0:
                stop_loss = entry_price * 1.03

        # Targets clean
        clean_targets: list[float] = []
        for t in targets:
            try:
                if isinstance(t, (dict, list, tuple, set)):
                    continue
                s = str(t).strip().replace(",", ".")
                fv = float(s)
                if math.isfinite(fv):
                    clean_targets.append(fv)
            except (TypeError, ValueError):
                continue
        targets = clean_targets
        # ---------- indicators extract (AI/Strategy scan meta taşıma) ----------
        indicators = meta.get("indicators")
        if not isinstance(indicators, dict):
            indicators = {}

        # ---------- meta_block ----------
        meta_block = {
            "calc_method": {"sl": sl_method, "tp": tp_method},
            "ai_confidence": safe_float(meta.get("ai_confidence"), 0.70),
            "source": source if source in ("manual", "ai_scan", "strategy_scan") else "auto",
            "exchange": (context.user_data or {}).get("exchange", "mexc"),
            "technical_score": safe_float(meta.get("technical_score"), 0.0),
            "volume_usd": safe_float(meta.get("volume_usd"), 0.0),
            "volume_ratio": safe_float(meta.get("volume_ratio"), 1.0),
            "momentum": safe_float(meta.get("momentum"), 0.0),
            "compression": safe_float(meta.get("compression"), 0.0),
            "delay_quality": safe_float(meta.get("delay_quality"), 0.0),
            "mt_align": bool(meta.get("mt_align", False)),
            "structure_ok": bool(meta.get("structure_ok", False)),
            "direction": direction,
            "timeframe": tf,
            "strategy_id": strategy_id,

            # ✅ En kritik satır: formatlayıcı bunun üzerinden okuyacak
            "indicators":indicators,

            # Teknik göstergeler (varsa)
            "adx": safe_float(meta.get("adx"), 0.0),
            "rsi": safe_float(meta.get("rsi"), 50.0),
            "stoch_k": safe_float(meta.get("stoch_k"), 50.0),
            "stoch_d": safe_float(meta.get("stoch_d"), 50.0),
            "bb_width": safe_float(meta.get("bb_width"), 0.0),
            "momentum_tension": safe_float(meta.get("momentum_tension"), 0.0),
            "obv_slope": safe_float(meta.get("obv_slope"), 0.0),
            "obv_status": str(meta.get("obv_status", "⚪️ Nötr")),
            # ✅ Yerel trend/regime: AI scan için ana kaynak indicators['local_trend']
            "local_regime":str(
                meta.get("local_regime")
                or meta.get("local_trend")
                or indicators.get("local_trend")
                or "⚪️ Bilinmiyor"
            ),
        }

        # skor alanları
        if str(strategy_id).lower() == "v1":
            meta_block["v1_score"] = safe_float(meta.get("v1_score", meta.get("score", 0.0)), 0.0)
        else:
            meta_block["v2_score"] = safe_float(meta.get("v2_score", meta.get("score", 0.0)), 0.0)

        # pot alanları (normalize)
        if meta_block["source"] == "ai_scan":
            pot_val = safe_float(meta.get("potential_pct"), 0.0)
            try:
                if 1.0 < abs(pot_val) <= 300.0:
                    pot_val = pot_val / 100.0
            except Exception:
                pot_val = 0.0
            meta_block["potential_pct"] = pot_val
        elif meta_block["source"] == "strategy_scan":
            sp = meta.get("strat_potential", meta.get("strat_potential_pct", None))
            if sp is not None:
                try:
                    sp = float(sp or 0.0)
                    if 1.0 < abs(sp) <= 300.0:
                        sp = sp / 100.0
                except Exception:
                    sp = 0.0
                meta_block["strat_potential"] = sp
        # ---------- Global BTC Regime (tek kaynak) ----------
        try:
            # Bu fonksiyon kendi içinde interval ile cache’li çalışıyor (_last_regime_check)
            btc_regime_tr = await cls._get_market_regime(context)  # "Yükseliş" / "Düşüş" / "Yatay"
            meta_block["global_btc_regime"] = str(btc_regime_tr)
            meta_block["global_btc_regime_src"] = str(ConfigService.get("market_regime.timeframe", "1d"))
            meta_block["global_btc_regime_ts"] = datetime.now(timezone.utc).isoformat()
        except Exception as e:
            logging.warning(f"[GLOBAL_BTC_REGIME_WARN] {e}")
            meta_block["global_btc_regime"] = "Bilinmiyor"

        # ---------- alarm_id ----------
        try:
            alarm_id = cls.next_alarm_id()
        except Exception:
            alarm_id = f"ALM-{int(datetime.now(timezone.utc).timestamp())}"

        now_iso = datetime.now(timezone.utc).isoformat()

        # ---------- signal_data ----------
        signal_data: dict = {
            "alarm_id":alarm_id,
            "symbol":norm_symbol,  # ✅ core
            "core_symbol":norm_symbol,  # ✅ core
            "ccxt_symbol":ccxt_symbol,  # ✅ ccxt ayrı
            "display_symbol":cls.to_display_symbol(norm_symbol) if hasattr(cls, "to_display_symbol") else norm_symbol,
            "normalized_symbol":norm_symbol,
            "timeframe": tf,
            "strategy_id": strategy_id,
            "signal_type": direction,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "targets": targets,
            "targets_hit": [False] * len(targets),
            "stop_loss_hit": False,
            "active": True,
            "signal_time": now_iso,
            "created_at": now_iso,
            "message_ids": [],
            "original_text": "",
            "source": meta_block["source"],
            "meta": meta_block,
            "user_id": uid,
        }

        # ---------- scan_session row (UI için) ----------
        row_key = f"{norm_symbol}|{tf}"
        _scan_session_put_row(row_key, {
            "symbol":norm_symbol,  # ✅ core (UI bunu display’e çeviriyor zaten)
            "ccxt_symbol":ccxt_symbol,  # ✅ ayrı
            "timeframe": tf,
            "strategy_id": strategy_id,
            "source": meta_block["source"],
            "alarm_id": alarm_id,
            "created_at": now_iso,
            "in_trade": bool(skip_persist),  # eski alan; UI bunu kullanmasa da dursun
            "meta": meta_block,
        })

        # ---------- normalize signal dict ----------
        try:
            _msg_backup = signal_data.get("message_ids", [])
            cls.normalize_signal_dict(signal_data)
            signal_data["message_ids"] = _msg_backup

            signal_data["entry_price"] = float(signal_data.get("entry_price") or 0.0)
            signal_data["stop_loss"] = float(signal_data.get("stop_loss") or 0.0)

            final_targets = []
            for x in (signal_data.get("targets") or []):
                try:
                    if isinstance(x, (dict, list, tuple, set)):
                        continue
                    s = str(x).strip().replace(",", ".")
                    fx = float(s)
                    if math.isfinite(fx):
                        final_targets.append(fx)
                except (TypeError, ValueError):
                    continue
            signal_data["targets"] = final_targets
        except Exception as _cast_err:
            logging.error(f"[ADD_ALARM_CAST_ERR] {symbol} {repr(_cast_err)}")
            return False

        # ---------- persist ----------
        if allow_persist:
            if not hasattr(cls, "active_symbols") or cls.active_symbols is None:
                cls.active_symbols = []
            cls.active_symbols.append(signal_data)
            try:
                cls.save_active_signals()
            except Exception:
                pass
            logging.info(f"[ADD_ALARM_SUCCESS] {symbol} eklendi (ID: {alarm_id}) Time: {now_iso}")
        else:
            logging.info(f"[ADD_ALARM_SUCCESS_NO_PERSIST] {symbol} aktif sinyal var → listeye eklenmedi (ID: {alarm_id})")

        # meta log
        try:
            cls._log_alarm_meta(norm_symbol, tf, strategy_id, meta_block["source"], meta_block)
        except Exception:
            pass

        # Adaptive Tuner log (varsa)
        try:
            from StrategyAdaptiveTuner import StrategyAdaptiveTuner
            StrategyAdaptiveTuner.log_scan_event(
                event_type="alarm_kuruldu",
                exchange=signal_data.get("meta", {}).get("exchange", "mexc"),
                tf=tf,
                scan_type=meta_block["source"],
                strategy_id=strategy_id,
                symbol=ccxt_symbol,
                alarm_id=alarm_id,
                control_mode=ConfigService.control_mode()
            )
        except Exception:
            pass

        # ---------- notify channels (dedupe) ----------
        try:
            notify_key = (norm_symbol, tf)

            sent = getattr(cls, "_scan_notified_keys", None)
            if sent is None:
                cls._scan_notified_keys = set()
                sent = cls._scan_notified_keys

            if notify_key in sent:
                return True
            sent.add(notify_key)

            valid_channels = await cls.validate_channel_ids(context)
            if valid_channels:
                disp_sym = cls.to_display_symbol(norm_symbol) if hasattr(cls, "to_display_symbol") else norm_symbol

                message_text = await _format_alarm_message_v2(
                    cls=cls,
                    symbol=disp_sym,
                    timeframe=tf,
                    source=meta_block["source"],
                    strategy=strategy_id,
                    source_meta=meta_block,
                    context=context
                )

                for channel_id in valid_channels:
                    try:
                        msg = await cls._retry_async(
                            context.bot.send_message,
                            chat_id=channel_id,
                            text=message_text,
                            parse_mode="Markdown",
                            disable_web_page_preview=True,
                            retries=3,
                            base_delay=0.8,
                            max_delay=max_backoff
                        )
                        signal_data["message_ids"].append({
                            "chat_id": int(channel_id),
                            "message_id": int(getattr(msg, "message_id", 0))
                        })
                    except Exception as send_err:
                        logging.error(f"[ADD_ALARM_SEND_ERR] {channel_id}: {send_err}")

                if signal_data["message_ids"]:
                    try:
                        cls.save_active_signals(force=True)
                    except Exception:
                        pass

        except Exception as notify_err:
            logging.error(f"[ADD_ALARM_NOTIFY_ERR] {notify_err}")
        # ✅ BOOTSCAN sayacı arttır (yalnız başarılı alarm için)
        try:
            if (context.user_data or {}).get("boot_scan"):
                context.user_data["boot_scan_added_count"] = int(context.user_data.get("boot_scan_added_count", 0) or 0) + 1
        except Exception:
            pass

        return True

    except Exception as main_err:
        logging.error(f"[ADD_ALARM_ERR] {symbol}: {repr(main_err)}", exc_info=True)
        return False


# ✅ YENİ FONKSİYON: Mesaj Formatı (Candidate Verilerini Kullanıyor)
async def _format_alarm_message_v2(
        cls,
        symbol: str,
        timeframe: str,
        source: str,
        strategy: str,
        source_meta: Optional[Dict[str, Any]] = None,
        context: Optional[Any] = None
) -> str:
    """
    Alarm mesajını formatla.
    - Teknik göstergeler için: source_meta['indicators'] varsa onu tek kaynak olarak kullanır.
    - Trend kanıtı: EMA200 + ADX + TF net biçimde gösterilir.
    - Karşılaştırma: ADX filtresi uygulanmış NET trendler üzerinden yapılır.
    - Fear&Greed API'den çekilir.
    """
    try:
        if source_meta is None or not isinstance(source_meta, dict):
            source_meta = {}

        # --- DISPLAY SYMBOL (UI için) ---
        try:
            display_symbol = cls.to_display_symbol(symbol) if hasattr(cls, "to_display_symbol") else str(symbol)
        except Exception:
            display_symbol = str(symbol)

        # ---------------------------------------------------------
        # ✅ BTC trend snapshot cache + helper (cls üzerinde saklanır)
        # ---------------------------------------------------------
        async def _get_btc_trend_cached(_tf: str, ttl_sec: float = 90.0) -> dict:
            """
            BTC için EMA200 + ADX hesaplar. ttl içinde cache döner.
            Cache cls üzerinde tutulur: cls._btc_trend_cache
            """
            import time as _time

            tf0 = str(_tf or "1d").strip()
            now = _time.time()

            cache = getattr(cls, "_btc_trend_cache", None)
            if not isinstance(cache, dict):
                cache = {"ts": 0.0, "tf": None, "ema200": 0.0, "adx": 0.0}
                setattr(cls, "_btc_trend_cache", cache)

            if cache.get("tf") == tf0 and (now - float(cache.get("ts") or 0.0)) < float(ttl_sec or 0.0):
                return dict(cache)

            out = {"ts": now, "tf": tf0, "ema200": 0.0, "adx": 0.0}

            # pandas / talib opsiyonel
            try:
                import pandas as pd  # type: ignore
            except Exception:
                cache.update(out)
                return dict(cache)

            try:
                import talib as ta  # type: ignore
            except Exception:
                cache.update(out)
                return dict(cache)

            # BTC sembol çöz (önce futures, sonra spot)
            try:
                btc_sym = (
                    cls.to_ccxt_symbol("BTCUSDT", prefer_futures=True)
                    or cls.to_ccxt_symbol("BTC/USDT", prefer_futures=True)
                    or "BTC/USDT"
                )
            except Exception:
                btc_sym = "BTC/USDT"

            # OHLCV çek (fallback: spot)
            df = None
            try:
                df = await cls.fetch_ohlcv_with_retry(btc_sym, timeframe=tf0, max_retries=2, timeout=12)
            except Exception:
                df = None

            if df is None or getattr(df, "empty", True) or len(df) < 30:
                # spot fallback dene
                try:
                    df = await cls.fetch_ohlcv_with_retry("BTC/USDT", timeframe=tf0, max_retries=2, timeout=12)
                except Exception:
                    df = None

            if df is None or getattr(df, "empty", True) or len(df) < 30:
                cache.update(out)
                return dict(cache)

            # Kapanmış mum kullan
            try:
                df_closed = df.iloc[:-1].copy() if len(df) >= 3 else df.copy()
            except Exception:
                df_closed = df

            # standardize (DataFrame or DataFrame yapma!)
            try:
                _std = cls._standardize_ohlcv_df(df_closed)
                if _std is not None:
                    df_closed = _std
            except Exception:
                pass

            try:
                close = df_closed["close"].astype(float).values
                high = df_closed["high"].astype(float).values
                low = df_closed["low"].astype(float).values

                # period adapt
                ema_p = 200 if len(df_closed) > 201 else max(20, len(df_closed) - 1)
                adx_p = 14 if len(df_closed) > 16 else max(7, len(df_closed) - 2)

                ema_arr = ta.EMA(close, timeperiod=int(ema_p))
                adx_arr = ta.ADX(high, low, close, timeperiod=int(adx_p))

                ema_s = pd.Series(ema_arr).dropna()
                adx_s = pd.Series(adx_arr).dropna()

                if not ema_s.empty:
                    out["ema200"] = float(ema_s.iloc[-1])
                if not adx_s.empty:
                    out["adx"] = float(adx_s.iloc[-1])

            except Exception:
                pass

            cache.update(out)
            return dict(cache)

        # ---------- Indicators (preferred: indicators block) ----------
        indicators = source_meta.get("indicators")
        if not isinstance(indicators, dict):
            indicators = {}

        # ---------- Numeric fields (prefer indicators, fallback to root meta) ----------
        adx = safe_float(indicators.get("adx", source_meta.get("adx")), 0.0)
        rsi = safe_float(indicators.get("rsi", source_meta.get("rsi")), 50.0)
        stoch_k = safe_float(indicators.get("stoch_k", source_meta.get("stoch_k")), 50.0)
        stoch_d = safe_float(indicators.get("stoch_d", source_meta.get("stoch_d")), 50.0)
        bb_width = safe_float(indicators.get("bb_width", source_meta.get("bb_width")), 0.0)
        momentum_tension = safe_float(indicators.get("momentum_tension", source_meta.get("momentum_tension")), 0.0)
        volume_ratio = safe_float(indicators.get("volume_ratio", source_meta.get("volume_ratio")), 1.0)
        volume_usd = safe_float(source_meta.get("volume_usd"), safe_float(indicators.get("volume_usd"), 0.0))
        obv_status = str(indicators.get("obv_status", source_meta.get("obv_status", "⚪️ Nötr")) or "⚪️ Nötr")

        # ---------- Status text fields (if provided) ----------
        adx_status_txt = str(indicators.get("adx_status") or "").strip()
        rsi_status_txt = str(indicators.get("rsi_status") or "").strip()
        bb_status_txt = str(indicators.get("bb_status") or "").strip()
        stoch_status_txt = str(indicators.get("stoch_status") or "").strip()
        momentum_status_txt = str(indicators.get("momentum_status") or "").strip()
        volume_status_txt = str(indicators.get("volume_status") or "").strip()

        # ---------- AI fields ----------
        ai_confidence = safe_float(source_meta.get("ai_confidence"), 0.85)
        technical_score = safe_float(source_meta.get("technical_score"), 80.0)

        potential_pct = safe_float(source_meta.get("potential_pct", source_meta.get("strat_potential", 0.0)), 0.0)
        try:
            if 1.0 < abs(potential_pct) <= 300.0:
                potential_pct = potential_pct / 100.0
        except Exception:
            potential_pct = 0.0

        # ---------- Fear & Greed ----------
        fear_greed = 50
        try:
            fear_greed_data = await _fetch_fear_greed_index()
            if fear_greed_data and isinstance(fear_greed_data, dict):
                fear_greed = int(fear_greed_data.get("value", 50))
        except Exception as e:
            logging.debug(f"[FEAR_GREED_ERR] {e}, varsayılan 50 kullanılıyor")
            fear_greed = 50

        if fear_greed < 25:
            fear_emoji, fear_text = "😨", "Korku"
        elif fear_greed < 45:
            fear_emoji, fear_text = "😟", "Endişe"
        elif fear_greed < 55:
            fear_emoji, fear_text = "😐", "Nötr"
        elif fear_greed < 75:
            fear_emoji, fear_text = "😊", "Açgözlülük"
        else:
            fear_emoji, fear_text = "🤑", "Aşırı Açgözlülük"

        # ---------- Lightweight derived statuses (if missing) ----------
        if not adx_status_txt:
            if adx > 50:
                adx_status_txt = "🚀 Çok Güçlü"
            elif adx > 40:
                adx_status_txt = "💪 Güçlü"
            elif adx > 30:
                adx_status_txt = "⚡️ Orta"
            elif adx > 20:
                adx_status_txt = "📊 Zayıf"
            else:
                adx_status_txt = "💤 Çok Zayıf"

        if not rsi_status_txt:
            if rsi > 80:
                rsi_status_txt = "🔴 Aşırı Alım"
            elif rsi > 70:
                rsi_status_txt = "🟡 Alım"
            elif rsi < 20:
                rsi_status_txt = "🟢 Aşırı Satım"
            elif rsi < 30:
                rsi_status_txt = "🟡 Satım"
            elif rsi > 55:
                rsi_status_txt = "📈 Yükseliş"
            elif rsi < 45:
                rsi_status_txt = "📉 Düşüş"
            else:
                rsi_status_txt = "⚪️ Nötr"

        if not stoch_status_txt:
            if stoch_k > 80 or stoch_d > 80:
                stoch_status_txt = "🔴 Aşırı Alım"
            elif stoch_k < 20 or stoch_d < 20:
                stoch_status_txt = "🟢 Aşırı Satım"
            elif stoch_k > 65 or stoch_d > 65:
                stoch_status_txt = "🟡 Alım"
            elif stoch_k < 35 or stoch_d < 35:
                stoch_status_txt = "🟡 Satım"
            else:
                stoch_status_txt = "✅ Normal"

        if not bb_status_txt:
            if bb_width < 0.005:
                bb_status_txt = "🔐 Çok Sıkı"
            elif bb_width < 0.01:
                bb_status_txt = "🔐 Sıkışma"
            elif bb_width < 0.03:
                bb_status_txt = "📊 Normal"
            elif bb_width < 0.06:
                bb_status_txt = "📈 Geniş"
            else:
                bb_status_txt = "🚀 Çok Geniş"

        if not momentum_status_txt:
            if momentum_tension > 5.0:
                momentum_status_txt = "🚀 Çok Yüksek"
            elif momentum_tension > 2.0:
                momentum_status_txt = "📊 Yüksek"
            elif momentum_tension > 0.5:
                momentum_status_txt = "✅ Normal"
            else:
                momentum_status_txt = "💤 Düşük"

        if not volume_status_txt:
            if volume_ratio > 2.0:
                volume_status_txt = "📊 Çok Yüksek"
            elif volume_ratio > 1.5:
                volume_status_txt = "📊 Yüksek"
            elif volume_ratio > 1.0:
                volume_status_txt = "✅ Normal"
            else:
                volume_status_txt = "⚪️ Normal"

        # ---------------------------------------------------------
        # ✅ Trend kanıt satırları (EMA200 + ADX + TF)
        # ---------------------------------------------------------
        ADX_MIN_TREND = float(ConfigService.get("trend.adx_min_trend", 15.0) or 15.0)

        def _net_trend_from_raw(raw: str, adx_val: float) -> str:
            raw0 = str(raw or "").strip() or "❓ Bilinmiyor"
            if (raw0.startswith("📈") or raw0.startswith("📉")) and (adx_val > 0) and (adx_val < ADX_MIN_TREND):
                return "⚪️ Nötr"
            return raw0

        def _trend_line(label: str, sym: str, tf: str, ema200: float, adx_val: float, net_tr: str) -> str:
            ema_txt = f"{ema200:.4f}" if (ema200 and ema200 > 0) else "N/A"
            adx_txt = f"{adx_val:.1f}" if (adx_val and adx_val > 0) else "N/A"
            return (
                f"• {label} ({sym}, {tf}):\n"
                f"EMA200={ema_txt} | ADX={adx_txt} → {net_tr}"
            )

        trend_snapshot = source_meta.get("trend_snapshot")
        if not isinstance(trend_snapshot, dict):
            trend_snapshot = {}

        snap_g = trend_snapshot.get("global") if isinstance(trend_snapshot.get("global"), dict) else {}
        snap_l = trend_snapshot.get("local") if isinstance(trend_snapshot.get("local"), dict) else {}

        # ----- LOCAL (coin) -----
        local_tf = str((snap_l.get("tf") if snap_l else None) or timeframe or "15m").strip()
        local_ema200 = safe_float(
            (snap_l.get("ema200") if snap_l else None),
            safe_float(indicators.get("ema_200"), 0.0)
        )
        local_adx = safe_float(
            (snap_l.get("adx") if snap_l else None),
            safe_float(indicators.get("adx"), adx)
        )
        local_raw = (
            (snap_l.get("raw_trend") if snap_l else None)
            or indicators.get("local_trend")
            or source_meta.get("local_trend")
            or source_meta.get("local_regime")
            or "❓ Bilinmiyor"
        )
        local_net = (snap_l.get("net_trend") if snap_l else None) or _net_trend_from_raw(local_raw, local_adx)
        local_line = _trend_line("Yerel Trend", display_symbol, local_tf, local_ema200, local_adx, local_net)

        # ----- GLOBAL (BTC) -----
        btc_regime = str(source_meta.get("global_btc_regime") or "").strip()
        if not btc_regime and context is not None:
            try:
                btc_regime = str(await cls._get_market_regime(context))
            except Exception:
                btc_regime = ""

        if btc_regime.lower().startswith("yüks"):
            global_raw = "📈 Yükseliş"
        elif btc_regime.lower().startswith("düş"):
            global_raw = "📉 Düşüş"
        elif btc_regime.lower().startswith("yat"):
            global_raw = "➡️ Yatay"
        else:
            global_raw = "❓ Bilinmiyor"

        global_tf = str(
            (snap_g.get("tf") if snap_g else None)
            or source_meta.get("global_btc_regime_src")
            or "1d"
        ).strip()

        global_ema200 = safe_float(
            (snap_g.get("ema200") if snap_g else None),
            safe_float(source_meta.get("btc_ema200"), 0.0)
        )
        global_adx = safe_float(
            (snap_g.get("adx") if snap_g else None),
            safe_float(source_meta.get("btc_adx"), 0.0)
        )

        # ✅ Eğer snapshot yoksa / değerler boşsa BTC EMA/ADX'yi anlık doldur
        if (global_ema200 <= 0) or (global_adx <= 0):
            try:
                btc_snap = await _get_btc_trend_cached(global_tf, ttl_sec=90.0)
                if global_ema200 <= 0:
                    global_ema200 = safe_float(btc_snap.get("ema200"), 0.0)
                if global_adx <= 0:
                    global_adx = safe_float(btc_snap.get("adx"), 0.0)
            except Exception:
                pass

        global_net = (snap_g.get("net_trend") if snap_g else None) or _net_trend_from_raw(global_raw, global_adx)
        global_line = _trend_line("Global Trend", "BTC", global_tf, global_ema200, global_adx, global_net)

        # ---------------------------------------------------------
        # ✅ TrendAnalyzer (NET trend ile karşılaştır)
        # ---------------------------------------------------------
        try:
            from strategies.trend_analyzer import TrendAnalyzer
            trend_pack = TrendAnalyzer.compare_trends(global_net, local_net)
        except Exception:
            trend_pack = {
                "scenario": "❓ BİLİNMEYEN DURUM",
                "recommendation": "BEKLEME",
                "risk_level": "🔴 Yüksek",
                "action": "WAIT",
                "confidence": 0.20,
                "description": "Trend verileri yetersiz."
            }

        scenario_lines = (
            f"{trend_pack.get('scenario', '-')}\n"
            f"{trend_pack.get('recommendation', '-')}\n"
            f"Risk: {trend_pack.get('risk_level', '-')} // Güven: %{float(trend_pack.get('confidence', 0.0)) * 100:.0f}"
        )
        # ---------- Labels ----------
        tf_labels = {
            "1m": "1 Dakikalık", "5m": "5 Dakikalık", "15m": "15 Dakikalık",
            "30m": "30 Dakikalık", "1h": "1 Saatlik", "4h": "4 Saatlik", "1d": "Günlük"
        }
        tf_label = tf_labels.get(str(timeframe), str(timeframe))
        source_name = "AI TARAMASI" if str(source) == "ai_scan" else "STRATEJİ TARAMASI"
        if isinstance(strategy, str):
            strategy_clean = strategy.lower().replace("v", "").strip() or strategy.lower().strip()
            strategy_name = f"V{strategy_clean.upper()}"
            strategy_key = strategy.lower().strip()
        else:
            strategy_name = f"V{strategy}"
            strategy_key = str(strategy).lower().strip()

        # ---------- Message ----------
        message = f"""🚨 *YENİ ALARM* - *{display_symbol}*
──────────────────────────────────
📡 Kaynak   : {source_name}
🎯 Strateji : {strategy_name}
🕒 Zaman    : {tf_label}

🧭 *TREND KANITI (EMA200 + ADX)*
{global_line}
{local_line}\n

🧩 *REJİM KARŞILAŞTIRMASI (NET TREND)*
{scenario_lines}
• Korku/Açgözlülük  : {fear_greed} {fear_emoji} ({fear_text})

"""

        # Strategy-specific block
        if strategy_key in ("v2", "2"):
            message += f"""🐺 *TREND & SIKIŞMA VERİLERİ*
• Trend Gücü (ADX)  : {adx:.1f} ({adx_status_txt})
• Bant Genişliği    : {bb_width:.4f} {bb_status_txt}
• Stoch K Değeri    : {stoch_k:.1f} {stoch_status_txt}
• Stoch D Değeri    : {stoch_d:.1f}
• RSI Durumu        : {rsi:.1f} {rsi_status_txt}

"""
        elif strategy_key in ("v1", "1"):
            message += f"""🚀 *MOMENTUM GÖSTERGELERİ*
• Hacim Oranı       : {volume_ratio:.2f}x {volume_status_txt}
• Momentum Tension  : {momentum_tension:.2f}% {momentum_status_txt}
• StochRSI K        : {stoch_k:.1f} {stoch_status_txt}
• OBV Durumu        : {obv_status}
• RSI Durumu        : {rsi:.1f} {rsi_status_txt}
"""
        else:
            message += f"""📊 *TEKNİK GÖSTERGELER*
• ADX (Trend Gücü)  : {adx:.1f} ({adx_status_txt})
• RSI               : {rsi:.1f} {rsi_status_txt}
• Stoch K           : {stoch_k:.1f} {stoch_status_txt}
• BB Genişliği      : {bb_width:.4f} {bb_status_txt}
• Momentum          : {momentum_tension:.2f}% {momentum_status_txt}
• Hacim Oranı       : {volume_ratio:.2f}x {volume_status_txt}
"""

        # Footer
        message += f"""🧠 *AI VE HACİM VERİLERİ*
• AI Güven          : %{ai_confidence * 100:.1f}
• Teknik Skor       : {technical_score:.1f}/100
• Potansiyel        : {potential_pct * 100:.4f}%
• 24h Hacim         : {format_usd_short(volume_usd)}

⚠️ Bu bir izleme alarmıdır. Sinyal şartları oluşursa otomatik paylaşılacaktır."""
        return message

    except Exception as e:
        logger.error(f"[FORMAT_ALARM_ERR] {symbol}: {e}", exc_info=True)
        return f"""🚨 *YENİ ALARM* - {symbol}
──────────────────────────────────
📡 Kaynak   : {source}
🎯 Strateji : {strategy}
🕒 Zaman    : {timeframe}

⚠️ Mesaj formatlanırken hata oluştu: {str(e)[:100]}"""


# ✅ YENİ FONKSİYON: Korku/Açgözlülük API'den Çek
async def _fetch_fear_greed_index() -> Optional[Dict[str, Any]]:
    """
    Alternative.me API'den Fear & Greed Index'i çek
    Yanıt: {"value": 50, "value_classification": "Neutral"}
    """
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(
                    'https://api.alternative.me/fng/?limit=1',
                    timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('data') and len(data['data']) > 0:
                        return {
                            'value':int(data['data'][0].get('value', 50)),
                            'classification':data['data'][0].get('value_classification', 'Neutral')
                        }
    except Exception as e:
        logging.debug(f"[FEAR_GREED_API_ERR] {e}")

    return None


def format_usd_short(value: float) -> str:
    """Para birimini kısa formatta göster"""
    try:
        abs_v = abs(float(value or 0.0))
        if abs_v >= 1_000_000_000:
            return f"{value / 1_000_000_000:.2f}B $"
        if abs_v >= 1_000_000:
            return f"{value / 1_000_000:.2f}M $"
        if abs_v >= 1_000:
            return f"{value / 1_000:.2f}K $"
        return f"{value:.2f} $"
    except Exception:
        return "0.00 $"


def safe_float(x: Any, default: Optional[float] = 0.0) -> float:
    """Güvenli float dönüşümü"""
    try:
        if x is None:
            return default
        val = float(x)
        if np.isnan(val) or np.isinf(val):
            return default
        return val
    except (TypeError, ValueError):
        return default

def safe_price_from_ticker(ticker: dict, default=0.0) -> float:
    # CCXT ticker örneği: {'symbol': 'BTC/USDT', 'last': 65000.0, 'close': 65000.0, ...}
    if not isinstance(ticker, dict):
        return float(default)
    for key in ('last', 'close', 'ask', 'bid'):
        if key in ticker:
            v = safe_float(ticker.get(key))
            if v > 0:
                return v
    # Bazı borsalar info içinde döndürür
    info = ticker.get('info')
    if isinstance(info, dict):
        return safe_float(info.get('last') or info.get('close') or info.get('price'), default)
    return float(default)


def ensure_float_list(x: Any, fallback: List[float]) -> List[float]:
    """
    x: tek bir sayı, str sayı, iterable sayı listesi veya karışık tipler olabilir.
    Dönüş: Sadece float'lardan oluşan bir liste.
    Kural:
      - x dict ise fallback.
      - Iterable ise her elemanı float'a çevir; herhangi biri çevrilemezse fallback.
      - Tek değer ise float'a çevir; çevrilemezse fallback.
      - NaN/Inf değerler tespit edilirse fallback.
    """
    try:
        # Dict kesinlikle kabul edilmez
        if isinstance(x, dict):
            return list(fallback)

        def to_float_or_none(value: Any) -> Optional[float]:
            try:
                parsed = float(value)
            except (TypeError, ValueError) as err:
                logging.debug(f"to_float_or_none parse error: {err}")
                return None

            # NaN/Inf kontrolleri
            if math.isnan(parsed) or math.isinf(parsed):
                return None

            return parsed

        # Iterable ise (str/bytes hariç)
        if isinstance(x, Iterable) and not isinstance(x, (str, bytes)):
            out: List[float] = []
            for v in x:
                fv = to_float_or_none(v)
                if fv is None:
                    return list(fallback)
                out.append(fv)
            # Boş liste gelirse fallback'e dönmek isteyebilirsiniz; iş kuralına göre:
            return out if out else list(fallback)

        # Tek bir değer
        fv = to_float_or_none(x)
        if fv is None:
            return list(fallback)
        return [fv]

    except (TypeError, ValueError) as e:
        logging.error(f"[ensure_float_list] Hata: {e}")
        return list(fallback)


async def show_strategy_status_report(cls, update, context):
    """
    Alarm Kurulum menüsünden strateji başlatıldığında gösterilecek rapor ekranı.
    """
    active_alarms = getattr(cls, "active_symbols", [])
    active_signals = getattr(cls, "active_signals", [])

    waiting_alarms_count = len(active_alarms)
    active_signals_count = len([s for s in active_signals if s.get('active')])

    # HATA ÇÖZÜLDÜ: Değişken adı düzeltildi
    status_header = "🟢 **Strateji Çalışıyor**" if cls.is_running else "🔴 **Strateji Durduruldu**"

    start_time = getattr(cls, 'strategy_start_time', None)
    uptime_str = "-"
    if cls.is_running and start_time:
        from datetime import datetime, timezone
        uptime_str = cls._human_duration(start_time, datetime.now(timezone.utc))
    elif cls.is_running:
        uptime_str = "Yeni Başladı"
    # ✅ hangi TF'ler taranıyor?
    tf_seq = getattr(cls, "timeframes", None)
    if not isinstance(tf_seq, list) or not tf_seq:
        tf_seq = context.user_data.get("strategy_tf_sequence")
    tf_seq_txt = ", ".join(tf_seq) if isinstance(tf_seq, list) and tf_seq else "5m"

    text = (
        f"📊 **Strateji Durum Raporu**\n\n"
        f"{status_header}\n"
        f"⏱️ Geçen Süre: `{uptime_str}`\n"
        f"🧭 TF Sırası: `{tf_seq_txt}`\n\n"
        f"📈 **Anlık Durum:**\n"
        f"• Sinyal Bekleyen Alarm: `{waiting_alarms_count}`\n"
        f"• Takip Edilen Sinyal: `{active_signals_count}`\n\n"
        f"💡 _Strateji arka planda çalışmaya devam ediyor._"
    )

    kb = [[InlineKeyboardButton("🔙 Geri", callback_data='back_to_alarm_menu')]]
    reply_markup = InlineKeyboardMarkup(kb)

    query = update.callback_query
    if query:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    else:
        await context.bot.send_message(update.effective_chat.id, text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

    return State.ALARM_SETUP


async def toggle_strategy_from_setup(cls, update, context, should_start):
    """Alarm Kurulum menüsü için strateji başlat/durdur işlemi."""
    query = update.callback_query

    if should_start:
        if not cls.is_running:
            from datetime import datetime, timezone

            # ✅ TF sırasını belirle (istersen ConfigService'den override edebilirsin)
            tf_seq = STRATEGY_TF_SEQUENCE_DEFAULT.copy()

            # güvenlik: sadece izinli TF'ler
            allowed = {'1m', '5m', '15m', '30m', '1h', '4h', '1d'}
            tf_seq = [tf for tf in tf_seq if str(tf).strip() in allowed]
            if not tf_seq:
                tf_seq = ["5m"]

            # ✅ Stratejinin kullanacağı TF'leri set et (run_ai_strategy bunu kullanıyorsa otomatik çoklu-TF olur)
            try:
                cls.timeframes = tf_seq
            except Exception:
                pass

            # ✅ UI/debug için context'e de yaz
            try:
                context.user_data['strategy_tf_sequence'] = tf_seq
            except Exception:
                pass

            cls.strategy_start_time = datetime.now(timezone.utc)

            # Arka planda başlat
            asyncio.create_task(cls.run_ai_strategy(context))
            # ✅ Multi-TF bootstrap scan (AI+Strat V1/V2)
            try:
                tf_seq = STRATEGY_TF_SEQUENCE_DEFAULT.copy()
                msg = await query.edit_message_text("⏳ **Çoklu TF Tarama başlatılıyor...**", parse_mode=ParseMode.MARKDOWN)
                asyncio.create_task(_run_bootstrap_multi_tf_scan(
                    cls=cls,
                    chat_id=update.effective_chat.id,
                    message_id=msg.message_id,
                    user_id=update.effective_user.id,
                    context=context,
                    tf_seq=tf_seq
                ))
            except Exception as _bs_err:
                logger.error(f"[BOOTSCAN_START_ERR] {_bs_err}", exc_info=True)

            await asyncio.sleep(0.5)

            await query.answer(f"✅ Strateji Başlatıldı! TF: {', '.join(tf_seq)}")
        else:
            await query.answer("⚠️ Zaten çalışıyor.", show_alert=True)

        return await show_strategy_status_report(cls, update, context)

    # STOP
    if cls.is_running:
        cls.is_running = False
        cls.run_ai_strategy_active = False
        await query.answer("🛑 Durduruluyor...")
        await asyncio.sleep(0.5)
    else:
        await query.answer("⚠️ Zaten durmuş.", show_alert=True)

    return await show_alarm_menu(cls, update, context)

