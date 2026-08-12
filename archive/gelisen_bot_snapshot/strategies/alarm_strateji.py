# alarm_strateji.py.txt dosyamın tamamı buradan sonra detaylı incele vereceğin fonksiyonlar buna göre olmalı
from __future__ import annotations
import json
import copy
import asyncio
from datetime import datetime, timezone, timedelta
from telegram.ext import CallbackContext
from typing import List, Dict, Any, Optional, TypedDict, Union
from PIL import ImageFont
import joblib
import ccxt.async_support as ccxt
import aiohttp
# Telegram imports
from telegram.ext import Application, ContextTypes
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
import logging
import matplotlib
from core.strategy_manager import StrategyManager as SMRef
from collections import defaultdict
# Modül importları
from strategies.alarm_system import analytics as alarm_analytics
from strategies.alarm_system import handlers as alarm_handlers
from strategies.alarm_system import scanning as alarm_scanning
from strategies.alarm_system import persistence as alarm_persistence

from strategies.alarm_system.analytics import AlarmRaporManager
from RealAIModel import RealAIModel
from config_service import ConfigService
import os
import math
from io import BytesIO
import numpy as np
import pandas as pd
from telegram_rate_limit import safe_send_message
from data.olimpos_data import get_user_notification_channel_ids, get_user_settings, get_api_key
from config.constants import ADMIN_USER_ID, State

# Stratejileri Kaydet
from strategies.strategy_v1 import StrategyV1
from strategies.strategy_v2 import StrategyV2
SMRef.register(StrategyV1)
SMRef.register(StrategyV2)
from strategies.alarm_system import monitoring as alarm_monitoring
from strategies.alarm_system import orderbook as alarm_orderbook
from strategies.alarm_system import regime as alarm_regime
from strategies.alarm_system import symbols as alarm_symbols
from strategies.alarm_system import signal_flow as alarm_signal_flow
# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    force=True
)
logging.getLogger("candidate_selector").setLevel(logging.DEBUG)

logger = logging.getLogger(__name__)

from charts.chart_renderer import ChartRenderer

models_dir = "models"
os.makedirs(models_dir, exist_ok=True)

TF_TR_LABELS = {
    '1m':'1 Dakikalık', '5m':'5 Dakikalık', '15m':'15 Dakikalık',
    '30m':'30 Dakikalık', '1h':'1 Saatlik', '4h':'4 Saatlik', '1d':'Günlük'
}

def tf_to_tr_label(tf: str) -> str:
    return TF_TR_LABELS.get(tf, tf)


class SignalMeta(TypedDict, total=False):
    ai_confidence: float
    potential_pct: float
    technical_score: float
    volume_usd: float
    volume_ratio: float
    momentum: float
    compression: float
    delay_quality: float
    mt_align: bool
    structure_ok: bool
    direction: str
    timeframe: str
    strategy_id: str
    v1_score: float
    v2_score: float
    # --- YENİ EKLENENLER ---
    leverage: int          # Kaldıraç (Örn: 10)
    entry_amount: float    # Giriş Tutarı USDT (Örn: 100.0)
    entry_lot: float       # Giriş Lot Miktarı (Örn: 0.5)
    stop_loss: float       # Stop Fiyatı
    targets: List[float]   # Hedef Fiyatları Listesi
    market_regime: str     # Piyasa Rejimi (Örn: 'BULL', 'BEAR')
    global_regime: str     # Global Rejim (BTC)

class AlarmMessageRef(TypedDict):
    chat_id: int
    message_id: int


class SignalData(TypedDict, total=False):
    alarm_id: str
    symbol: str
    normalized_symbol: str
    core_symbol: str  # ✅ kalıcı/state için: XLMUSDT
    ccxt_symbol: str  # ✅ borsa işlemleri için: XLM/USDT:USDT (veya borsaya göre)
    display_symbol: str  # (opsiyonel cache) XLM/USDT
    timeframe: str
    strategy_id: str
    signal_type: str
    entry_price: float
    stop_loss: float
    targets: List[float]
    targets_hit: List[bool]
    stop_loss_hit: bool
    active: bool
    signal_time: str
    message_ids: list[AlarmMessageRef]
    main_messages: list[dict]   # ✅ EKLE (AlarmMessageRef benzeri)
    original_text: str
    source: str
    meta: Dict[str, object]
    targets_hit_times: List[object]
    stop_time: object
    peak_price: float
    trough_price: float


REQUIRED_SIGNAL_KEYS = {
    'alarm_id':'',
    'symbol':'',
    'normalized_symbol':'',
    'core_symbol':'',
    'ccxt_symbol':'',
    'display_symbol':'',
    'timeframe':'',
    'strategy_id':'',
    'signal_type':'LONG',
    'entry_price':0.0,
    'stop_loss':0.0,
    'targets':[],
    'targets_hit':[],
    'stop_loss_hit':False,
    'active':True,
    'signal_time':None,  # normalize içinde datetime.now atanıyor
    'message_ids':[],
    'main_messages':[],  # ✅ EKLE
    'original_text':'',
    'source':'auto',
    'meta':{},
    # normalize_signal_dict’te garanti edilen alanlar (default gerekirse buraya da koy)
    'targets_hit_times':[],
    'stop_time':None,
    'peak_price':0.0,
    'trough_price':0.0,
}


def safe_float(x: Any, default: Optional[float] = 0.0) -> Optional[float]:
    try:
        if x is None: return default
        return float(x)
    except (TypeError, ValueError): return default


def format_usd_short(value: float) -> str:
    abs_v = abs(value)
    if abs_v >= 1_000_000_000: return f"{value / 1_000_000_000:.2f}B $"
    if abs_v >= 1_000_000: return f"{value / 1_000_000:.2f}M $"
    if abs_v >= 1_000: return f"{value / 1_000:.2f}K $"
    return f"{value:.2f} $"


def _norm_mtype(x: str | None) -> str:
    s = str(x or "").strip().lower()
    if s in ("futures", "future"):
        return "futures"
    if s in ("swap", "perp", "perpetual"):
        return "futures"
    return s


class OlimposStrategy:
    """
    Ana strateji sınıfı - AI destekli coin analizi ve sinyal üretimi
    Bu sınıf tüm trading stratejilerini ve AI analizlerini yönetir
    """
    # Sınıf düzeyinde değişkenleri tanımla
    exchange: Any = None
    _exchange_restart_lock = asyncio.Lock()
    _exchange_restart_cooldown_until = 0.0
    _exchange_pool: dict[tuple[int, str], Any] = {}
    _exchange_pool_lock = asyncio.Lock()

    _ohlcv_sem = asyncio.Semaphore(4)  # 4-6 iyi
    _ticker_sem = asyncio.Semaphore(2)  # tickers daha ağır ol
    _trailing_started: bool = False

    timeframes: List[str] = ['1m', '5m', '15m', '30m', '1h', '4h', '1d']
    channel_ids: List[int] = [-1002230830838, -1002178433812, -1002238531960]
    # Global state
    active_symbols: List[Dict] = []
    active_signals: List[Dict] = []
    alarm_rapor_manager = None  # YENİ: Alarm rapor yöneticisi
    active_alarm_signals = {}  # {alarm_id: signal_data}     # YENİ: Aktif alarm ID'leri ile sinyal eşleştirmesi
    active_strategy_id = 'v1'  # varsayılan
    alarm_counter_date = None
    alarm_counter = 0
    signal_counters = {}  # {alarm_id: int}
    # Risk freni “unblock” yakalamak için: tf bazlı son durum
    _risk_blocked_last_by_tf: dict[str, bool] = {}

    # REFAKTÖR: ChartRenderer nesnesi burada oluşturulur.
    # DÜZELTME: Kaldırılan ancak hala referans verilen sabitler geri eklendi.
    # Bu değerler artık ConfigService üzerinden yönetilecek, ancak referans hatalarını önlemek için
    # geçici olarak burada tutuluyorlar.
    TEMPLATE_FONT_PATH = None  # Örnek olarak None bırakıldı, ConfigService'den okunacak.
    MAIN_VERTICAL_GAP = 18
    # Tüm görsel ayarları kendi __init__ metodu içinde ConfigService'den yükler.
    # DÜZELTME: Artık kullanılmayan veya ChartRenderer'a taşınan sabitler kaldırıldı.
    # Gerekli olanlar ConfigService üzerinden okunacak.
    chart_renderer: ChartRenderer = ChartRenderer()
    processing_symbols = set()
    _last_event_ts: dict = {}  # {signal_id: datetime}
    _last_symbol_added_ts: dict = {}  # {symbol: datetime}
    EVENT_MIN_INTERVAL_SEC = 6
    SYMBOL_ADD_COOLDOWN_SEC = 120
    MAX_EVENTS_PER_MINUTE = 8
    _event_counter_window: list = []
    _application: Optional['Application'] = None
    run_ai_strategy_active: bool = False
    allow_scans_while_running: bool = False
    _last_dashboard_message: dict = {}
    _dashboard_refresh_lock: bool = False
    DASHBOARD_ANCHOR_HOUR = 3
    DASHBOARD_PAGE_SIZE = 40
    _dashboard_pages = {}
    RATE_LIMIT_CODES = {429, 418, 510}
    MAX_BACKOFF_SEC = 8
    matplotlib.use('Agg')
    matplotlib.rcParams['font.family'] = 'DejaVu Sans'
    matplotlib.rcParams['axes.unicode_minus'] = False
    _ai_model: Optional[Any] = None
    _sentiment_analyzer: Optional[Any] = None
    _ai_coin_scanner: Optional[Any] = None
    ai_scan_cache: dict = {}
    processed_signals: set = set()
    is_running: bool = False
    _signal_log_file: str = os.path.join("analytics", "signal_performance_logs.jsonl")
    _active_signals_file: str = os.path.join("analytics", "active_signals_state.json")
    _closed_signals_file: str = os.path.join("analytics", "closed_signals_state.json")
    _settings_scan_file: str = os.path.join("config", "olimpos_tarama_ayarlari.json")
    _strategy_history_file: str = os.path.join("analytics", "strategy_history.jsonl")
    strategy: dict = {}  # Artık load_runtime_strategy içinde dolduruluyor.
    _last_signal_meta: dict = {}
    _recent_alarm_keys = {}
    _last_active_save: Optional[datetime] = None
    strategy_version_counter: int = 0
    strategy_change_buffer: list = []
    _cooldown_until: Optional[datetime] = None
    _daily_loss_tracker: dict = {'date':None, 'realized_loss_pct':0.0}
    _consecutive_losses: int = 0
    _last_daily_version = None
    runtime_strategy: dict = {}  # Artık load_runtime_strategy içinde dolduruluyor.
    exchange_api_info: dict = {}
    _ohlcv_cache_obj = None
    _trend_cache_obj = None
    _bar_guard = None
    _orderbook_sem = asyncio.Semaphore(3)  # ayardan override edeceğiz
    _orderbook_cache: dict = {}  # {(ex_id, symbol, depth): {"ts": float, "data": dict}}

    # --- YENİ: Model eğitimi yönetimi için değişkenler ---
    _last_model_train_time: Optional[datetime] = None
    _training_lock = asyncio.Lock()
    _is_training_in_progress = False

    # YENİ: Piyasa Rejimi için Değişkenler
    _market_regime: str = "Yatay"  # 'Yükseliş', 'Düşüş', 'Yatay'
    _last_regime_check: Optional[datetime] = None
    # DÜZELTME: Kaldırılan REGIME_EMA_PERIOD referansını düzeltmek için eklendi.
    # Bu değer artık ConfigService'den okunacak.
    REGIME_EMA_PERIOD = 200
    REGIME_CHECK_INTERVAL_MIN = 5  # Rejimi her 5 dakikada bir kontrol et
    REGIME_SYMBOL = "BTC/USDT"  # Rejim belirlemek için kullanılacak ana parite

    # ✅ GLOBAL DEDUPE (alarm kapısı)
    _alarm_locks = defaultdict(asyncio.Lock)  # (user_id, norm_symbol, timeframe) -> Lock
    ALARM_DEDUPE_MODE = "REPLACE_IF_BETTER"  # "REJECT" | "REPLACE_IF_BETTER"
    ALARM_ACTIVE_STATES = {"ACTIVE", "PENDING"}  # sizde state isimleri farklıysa değiştirin
    # class body içinde:
    # --- Symbols delegators (cls geçişi şart) ---

    @classmethod
    def normalize_symbol(cls, symbol: str) -> str:
        return alarm_symbols.normalize_symbol(cls, symbol)

    @classmethod
    def to_ccxt_symbol(cls, core_or_any: str, prefer_futures: bool = True) -> Optional[str]:
        return alarm_symbols.to_ccxt_symbol(cls, core_or_any, prefer_futures=prefer_futures)

    @classmethod
    def to_display_symbol(cls, any_symbol: str, quote: str = "USDT") -> str:
        return alarm_symbols.to_display_symbol(cls, any_symbol, quote=quote)

    @classmethod
    def has_futures_market(cls, any_symbol: str) -> bool:
        return alarm_symbols.has_futures_market(cls, any_symbol)

    @classmethod
    def to_machine_symbol(cls, raw_symbol: str, prefer_futures: bool = True) -> str:
        return alarm_symbols.to_machine_symbol(cls, raw_symbol, prefer_futures=prefer_futures)

    @classmethod
    def core_to_machine_symbol(cls, core_symbol: str, prefer_futures: bool = True) -> Optional[str]:
        return alarm_symbols.core_to_machine_symbol(cls, core_symbol, prefer_futures=prefer_futures)

    @classmethod
    def to_signal_center_symbol(cls, raw_symbol: str, exchange: str = "mexc") -> str:
        return alarm_symbols.to_signal_center_symbol(cls, raw_symbol, exchange=exchange)

    # --- Monitoring delegators (davranış aynı, sadece çağrı değişir) ---

    @classmethod
    async def monitor_symbols(cls, context: CallbackContext, price_map: dict | None = None):
        logging.info("[ROUTE] monitor_symbols -> alarm_monitoring.monitor_symbols")
        return await alarm_monitoring.monitor_symbols(cls, context, price_map=price_map)

    @classmethod
    def _standardize_ohlcv_df(cls, df):
        return alarm_monitoring._standardize_ohlcv_df(cls, df)

    @classmethod
    def _closed_bar_key_and_ts(cls, df_closed, ccxt_symbol: str, timeframe: str, user_id=None, exchange_id=None,
            market_type=None):
        return alarm_monitoring._closed_bar_key_and_ts(
            cls, df_closed, ccxt_symbol=ccxt_symbol, timeframe=timeframe,
            user_id=user_id, exchange_id=exchange_id, market_type=market_type
        )

    @classmethod
    async def get_exchange_for_user(cls, user_id: int, exchange_name: str, context):
        key = (int(user_id), str(exchange_name).lower().strip())
        async with cls._exchange_pool_lock:
            ex = cls._exchange_pool.get(key)

        if ex is not None:
            return ex

        api = get_api_key(user_id, exchange_name)
        if not api:
            raise RuntimeError("no api")

        # burada initialize_exchange yerine “instance döndüren” bir fonksiyon daha doğru
        # şimdilik: initialize_exchange cls.exchange set ediyor; bu yüzden refactor şart
        raise RuntimeError("need refactor: initialize_exchange must return instance instead of cls.exchange singleton")

    @classmethod
    def _init_caches_if_needed(cls):
        return alarm_monitoring._init_caches_if_needed(cls)

    @classmethod
    def _interpret_strategy_output(cls, sig_raw):
        return alarm_monitoring._interpret_strategy_output(cls, sig_raw)

    @classmethod
    def _normalize_sig_obj(cls, sig_raw: Any, df_closed: pd.DataFrame) -> Optional[dict]:
        return alarm_monitoring._normalize_sig_obj(cls, sig_raw, df_closed=df_closed)

    @classmethod
    def _rr(cls, entry: float, stop: float, target: float, direction: str) -> float:
        return alarm_monitoring._rr(entry, stop, target, direction)

    @classmethod
    def _log_no_signal_reason(cls, symbol: str, tf: str, strat_id: str, sig_payload: dict, reason: str | None = None):
        return alarm_monitoring._log_no_signal_reason(cls, symbol, tf, strat_id, sig_payload, reason)

    @classmethod
    async def _fetch_orderbook_cached(cls, ccxt_symbol: str, depth: int = 20, cfg: dict | None = None):
        return await alarm_orderbook._fetch_orderbook_cached(cls, ccxt_symbol, depth=int(depth), cfg=cfg)

    @classmethod
    def _compute_orderbook_features(cls, ob: dict) -> dict:
        return alarm_orderbook._compute_orderbook_features(cls, ob)

    @classmethod
    def _apply_orderbook_confirm(cls, cand: dict, obf: dict, direction: str, cfg: dict | None = None) -> dict:
        return alarm_orderbook._apply_orderbook_confirm(cls, cand, obf, direction=direction, cfg=cfg)

    @classmethod
    async def _confirm_signal_with_orderbook(cls, ccxt_symbol: str, direction: str, base_score: float | None, meta: dict,
            timeframe: str | None = None):
        return await alarm_orderbook._confirm_signal_with_orderbook(
            cls, ccxt_symbol=ccxt_symbol, direction=direction, base_score=base_score, meta=meta, timeframe=timeframe
        )

    @classmethod
    def _get_orderbook_cfg_for(cls, exchange_id: str | None, timeframe: str | None):
        return alarm_orderbook._get_orderbook_cfg_for(cls, exchange_id, timeframe)

    @classmethod
    def _merge_dict_deepish(cls, dst: dict, src: dict) -> dict:
        return alarm_orderbook._merge_dict_deepish(cls, dst, src)

    @classmethod
    async def _get_market_regime(cls, context: CallbackContext, **_ignored) -> str:
        return await alarm_regime._get_market_regime(cls, context)

    @classmethod
    async def _update_market_regime(
            cls,
            context: CallbackContext,
            api_key: str,
            secret_key: str,
            passphrase: Optional[str] = None,
            user_id: int = 0,
            exchange_name: str = "mexc",
    ) -> None:
        return await alarm_regime._update_market_regime(
            cls,
            context=context,
            api_key=api_key,
            secret_key=secret_key,
            passphrase=passphrase,
            user_id=user_id,
            exchange_name=exchange_name,
        )

    @classmethod
    def to_router_symbol(cls, any_symbol: str) -> str:
        """
        Signal merkezi / router için TEK format:
        - Çıktı: BASEUSDT (örn BTCUSDT)
        """
        return cls.normalize_symbol(any_symbol)

    @classmethod
    def _get_orderbook_cfg(cls) -> dict:
        cfg = (cls.runtime_strategy or {}).get("orderbook_confirm") or {}
        return cfg

    @classmethod
    def repair_state_symbols(cls) -> dict:
        """
        Eski/karışık state kayıtlarını (active_signals + active_symbols) core/ccxt/display alanlarına oturtur.
        - symbol -> core_symbol'a sabitlenir
        - ccxt_symbol doldurulur (mümkünse)
        - display_symbol doldurulur
        Dönen: küçük bir rapor dict'i
        """
        fixed_signals = 0
        fixed_alarms = 0
        ccxt_missing = 0
        alarms_ccxt_missing = 0

        # 1) active_signals
        for s in (cls.active_signals or []):
            if not isinstance(s, dict):
                continue
            before = (s.get("symbol"), s.get("core_symbol"), s.get("ccxt_symbol"))

            try:
                cls.normalize_signal_dict(s)
            except Exception:
                continue

            after = (s.get("symbol"), s.get("core_symbol"), s.get("ccxt_symbol"))
            if before != after:
                fixed_signals += 1
            if not s.get("ccxt_symbol"):
                ccxt_missing += 1

        # 2) active_symbols (alarm listesi)
        for a in (cls.active_symbols or []):
            if not isinstance(a, dict):
                continue

            raw = a.get("symbol")
            core = cls.normalize_symbol(raw)
            if not core:
                continue

            changed = False

            # symbol'u core yap
            if a.get("symbol") != core:
                a["symbol"] = core
                changed = True

            # core_symbol / display_symbol alanları yoksa ekle (opsiyonel)
            if a.get("core_symbol") != core:
                a["core_symbol"] = core
                changed = True

            disp = cls.to_display_symbol(core)
            if a.get("display_symbol") != disp:
                a["display_symbol"] = disp
                changed = True

            # ccxt_symbol doldur (alarm tarafında da faydalı)
            if not a.get("ccxt_symbol"):
                alarms_ccxt_missing += 1
                cc = cls.to_ccxt_symbol(core, prefer_futures=True)
                if cc:
                    a["ccxt_symbol"] = cc
                    changed = True

            if changed:
                fixed_alarms += 1

        # Persist et
        try:
            cls.save_active_signals(force=True)
        except Exception:
            pass

        try:
            alarm_persistence.save_active_alarms_from_cls(cls)
        except Exception:
            pass

        return {
            "fixed_signals":fixed_signals,
            "fixed_alarms":fixed_alarms,
            "signals_ccxt_missing":ccxt_missing,
            "alarms_ccxt_missing":alarms_ccxt_missing

        }

    @classmethod
    def get_ccxt_symbol_for_signal(cls, sig: dict, prefer_futures: bool = True) -> Optional[str]:
        try:
            if not isinstance(sig, dict):
                return None
            return (
                    sig.get("ccxt_symbol")
                    or cls.to_ccxt_symbol(sig.get("symbol"), prefer_futures=prefer_futures)
                    or cls.to_ccxt_symbol(sig.get("core_symbol"), prefer_futures=prefer_futures)
            )
        except Exception:
            return None

    @classmethod
    def ensure_ccxt_symbol(cls, s: dict) -> str:
        # s: candidate/signal dict
        ccxt_sym = s.get("ccxt_symbol")
        if ccxt_sym:
            return str(ccxt_sym)
        # fallback: display veya normalize
        return cls.to_display_symbol(s.get("symbol") or s.get("core_symbol") or "")

    # --- Signal flow delegators (signal_merkezi forward + lifecycle) ---

    @classmethod
    async def _forward_open_to_signal_merkezi(cls, signal_data: dict, context):
        return await alarm_signal_flow._forward_open_to_signal_merkezi(cls, signal_data,
            context)  # pyright: ignore[reportPrivateUsage]

    @classmethod
    async def _forward_close_to_signal_merkezi(cls, signal_data: dict, exit_type: str, last_price: float, context,
            user_id: int | None = None):
        return await alarm_signal_flow._forward_close_to_signal_merkezi(  # pyright: ignore[reportPrivateUsage]
            cls, signal_data, exit_type=exit_type, last_price=last_price, context=context, user_id=user_id
        )

    @classmethod
    async def finalize_signal(cls, signal: dict, exit_type: str, current_price: float = None, context=None,
            user_id: int | None = None):
        return await alarm_signal_flow.finalize_signal(
            cls, signal, exit_type=exit_type, current_price=current_price, context=context, user_id=user_id
        )

    @classmethod
    async def monitor_active_signals(cls, context, user_id: int, price_map: dict | None = None):
        return await alarm_signal_flow.monitor_active_signals(cls, context, user_id=user_id, price_map=price_map)

    @classmethod
    async def update_signal_messages(cls, context, signal: dict, update_type: str, event_meta: dict,
            user_id: int | None = None):
        return await alarm_signal_flow.update_signal_messages(
            cls, context=context, signal=signal, update_type=update_type, event_meta=event_meta, user_id=user_id
        )

    @classmethod
    def initialize_system(cls) -> None:
        """Bot başlatılırken çağrılan ana kurulum fonksiyonu."""
        if not getattr(ConfigService, "_initialized", False):
            ConfigService.init()

        cls.load_runtime_strategy()
        logging.info("[CONFIG_CHECK] control_mode=%s", ConfigService.control_mode())
        logging.info("[CONFIG_CHECK] CANDIDATE_SELECTION=%s", ConfigService.get("CANDIDATE_SELECTION", {}))
        cls.initialize_alarm_system()

        try:
            alarm_persistence.load_active_signals(cls)
            alarm_persistence.load_active_alarms_into_cls(cls)
            alarm_persistence.sync_id_counters(cls)
        except Exception as e:
            logger.error(f"Aktif sinyaller veya ID sayaçları yüklenirken hata: {e}")

        # ✅ LOAD sonrası repair
        try:
            rep = cls.repair_state_symbols()
            logging.info(f"[STATE_REPAIR] {rep}")
        except Exception as e:
            logging.warning(f"[STATE_REPAIR_ERR] {e}")

            # ✅ EKLE: active_alarm_signals mapping'i doldur
        try:
            cls.active_alarm_signals = {
                s.get("alarm_id"):s
                for s in (cls.active_signals or [])
                if isinstance(s, dict) and s.get("alarm_id")
            }
            logging.info(
                f"[ACTIVE_SIGNAL_MAP] mapped={len(cls.active_alarm_signals)} active_signals={len(cls.active_signals)}")
        except Exception as e:
            logging.error(f"[ACTIVE_SIGNAL_MAP_ERR] {e}", exc_info=True)
            cls.active_alarm_signals = {}
        # DÜZELTME: StrategyAdaptiveTuner'ı gerekli dosya yollarıyla yapılandır.
        # Bu, "[ADAPT_LOAD] Kapanan sinyal dosyası bulunamadı" uyarısını giderir.
        try:
            from StrategyAdaptiveTuner import StrategyAdaptiveTuner
            if hasattr(StrategyAdaptiveTuner, 'configure'):
                StrategyAdaptiveTuner.configure(
                    closed_signals_path=cls._closed_signals_file
                )
                logging.info("✅ StrategyAdaptiveTuner başarıyla yapılandırıldı.")
        except (ImportError, AttributeError) as config_err:
            logging.warning(f"StrategyAdaptiveTuner yapılandırılamadı: {config_err}")

    @classmethod
    def load_runtime_strategy(cls):
        """
        Dosya yoksa veya bozuksa, koddaki varsayılan 'strategy' sözlüğünü kullanır ve bir kopya oluşturur.
        """
        # DÜZELTME: Artık tüm ayarlar ConfigService tarafından yönetiliyor.
        # Strateji ayarları doğrudan ConfigService'den okunur.
        logging.info(
            f"✅ Çalışma zamanı stratejisi tamamen ConfigService tarafından yönetiliyor.")
        cls.runtime_strategy = ConfigService.get('strategy', {})
        cls.strategy = ConfigService.get('strategy', {})

    @classmethod
    def save_runtime_strategy(cls):
        """Çalışma zamanı stratejisini (runtime_strategy) dosyaya kaydeder."""
        try:
            with open(cls._settings_scan_file, 'w', encoding='utf-8') as f:
                json.dump(cls.runtime_strategy, f, indent=2, ensure_ascii=False)
            logging.info(f"💾 Çalışma zamanı stratejisi '{cls._settings_scan_file}' dosyasına kaydedildi.")
        except IOError as e:
            logging.error(f"❌ Çalışma zamanı stratejisi kaydedilemedi: {e}")

    @classmethod
    async def handle_alarm_action(cls, update: Update, context: CallbackContext):
        """
        Alarm buton akışını merkezi handlers router'a delege eder.
        """
        try:
            return await alarm_handlers.handle_alarm_action(cls, update, context)
        except Exception as e:
            logger.error(f"[OLIMPOS.handle_alarm_action] Hata: {e}", exc_info=True)
            # Güvenli fallback: ana menü
            try:
                q = getattr(update, "callback_query", None)
                if q:
                    await q.edit_message_text(
                        "❌ Bir hata oluştu. Lütfen tekrar deneyin.",
                        reply_markup=InlineKeyboardMarkup(
                            [[InlineKeyboardButton("🔙 Geri", callback_data='back_to_alarm_menu')]])
                    )
            except Exception:
                pass

    @classmethod
    async def start_trailing_loops_once(cls):
        if cls._trailing_started:
            return

        started_any = False

        # ✅ UNIFIED TRAILING
        try:
            from core_trailing import UNIFIED_TRAILING_SUPERVISOR
            loop = asyncio.get_running_loop()
            UNIFIED_TRAILING_SUPERVISOR.start(loop)
            cls._trailing_started = True

            logger.info("[TRAILING] UNIFIED trailing supervisor başlatıldı")

        except Exception as e:
            logger.error(f"[TRAILING] UNIFIED başlatılamadı: {e}", exc_info=True)

        if started_any:
            cls._trailing_started = True

    @classmethod
    async def show_alarm_menu(cls, update: Update, context: CallbackContext):
        """
        DÜZELTME: 'is_connecting' argümanı kaldırıldı. Menü artık mevcut bağlantı durumuna göre kendini çiziyor.
        """
        return await alarm_handlers.show_alarm_menu(cls, update, context)

    @classmethod
    async def show_settings(cls, update: Update, context: CallbackContext):
        return await alarm_handlers.show_settings(cls, update, context)

    @classmethod
    async def show_tuner_mode_menu(cls, update: Update, context: CallbackContext):
        return await alarm_handlers.show_tuner_mode_menu(cls, update, context)

    @classmethod
    async def handle_set_tuner_mode(cls, update: Update, context: CallbackContext, mode: str):
        return await alarm_handlers.handle_set_tuner_mode(cls, update, context, mode)

    @classmethod
    async def show_tf_thresholds_menu(cls, update: Update, context: CallbackContext):
        return await alarm_handlers.show_tf_thresholds_menu(cls, update, context)

    @classmethod
    async def show_tf_thresholds_kind(cls, update: Update, context: CallbackContext, tf: str):
        return await alarm_handlers.show_tf_thresholds_kind(cls, update, context, tf)

    @classmethod
    async def show_tfth_ai_edit(cls, update: Update, context: CallbackContext):
        return await alarm_handlers.show_tfth_ai_edit(cls, update, context)

    @classmethod
    async def show_tfth_strat_edit(cls, update: Update, context: CallbackContext):
        return await alarm_handlers.show_tfth_strat_edit(cls, update, context)

    @classmethod
    async def show_param_menu(cls, update: Update, context: CallbackContext):
        return await alarm_handlers.show_param_menu(cls, update, context)

    @classmethod
    async def show_param_group(cls, update: Update, context: CallbackContext, group: str):
        return await alarm_handlers.show_param_group(cls, update, context, group)

    @classmethod
    async def ask_new_param_value(cls, update: Update, context: CallbackContext, param_path: str):
        # ÖNEMLİ: Bu fonksiyonun ASIL İÇERİĞİ alarm_system/handlers.py dosyasındadır.
        # save_runtime_strategy() çağrısı, o dosyadaki ilgili handler'da,
        # ConfigService.set() çağrısından sonra yapılmalıdır.
        # Aşağıdaki kod, o dosyada yapılması gerekeni göstermektedir.
        return await alarm_handlers.ask_new_param_value(cls, update, context, param_path)

    @classmethod
    async def show_performance_dashboard(cls, update: Update, context: CallbackContext):
        return await alarm_handlers.show_performance_dashboard(cls, update, context)

    @classmethod
    async def show_dashboard_page(cls, context: CallbackContext, chat_id: int, page_index: int):
        return await alarm_handlers.show_dashboard_page(cls, context, chat_id, page_index)

    @classmethod
    async def show_alarm_reports(cls, update: Update, context: CallbackContext):
        return await alarm_handlers.show_alarm_reports(cls, update, context)

    @classmethod
    async def handle_retrain_ai(cls, update: Update, context: CallbackContext):
        return await alarm_handlers.handle_retrain_ai(cls, update, context)

    @classmethod
    async def show_system_stats(cls, update: Update, context: CallbackContext):
        return await alarm_handlers.show_system_stats(cls, update, context)

    @classmethod
    async def show_active_alarms(cls, update: Update, context: CallbackContext):
        return await alarm_handlers.show_active_alarms(cls, update, context)

    @classmethod
    async def show_symbols(cls, update: Update, context: CallbackContext, symbol_type: str):
        return await alarm_handlers.show_symbols(cls, update, context, symbol_type)

    @classmethod
    async def handle_tfth_value_input(cls, update: Update, context: CallbackContext):
        return await alarm_handlers.handle_tfth_value_input(cls, update, context)

    @classmethod
    async def handle_param_value_input(cls, update: Update, context: CallbackContext):
        return await alarm_handlers.handle_param_value_input(cls, update, context)

    @classmethod
    async def select_timeframe(cls, update: Update, context: CallbackContext):
        return await alarm_handlers.select_timeframe(cls, update, context)

    @classmethod
    async def remove_alarm(cls, update: Update, context: CallbackContext, index: int):
        return await alarm_handlers.remove_alarm(cls, update, context, index)

    @classmethod
    async def add_alarm_debug(
            cls: 'OlimposStrategy',
            context: CallbackContext,
            symbol: str,
            timeframe: str,  # 'timeframe' is defined above
            strategy_id: str,  # 'strategy_id' is defined above
            user_id: int,  # YENİ: Alarmı kuran kullanıcı ID'si
            source_meta: dict | None = None
    ):
        # YENİ: user_id'yi handler'a iletiyoruz
        return await alarm_handlers.add_alarm_debug(cls, context, symbol, timeframe, strategy_id, user_id, source_meta)

    # Handler yardımcı statikler (imza uyumluluğu için)
    @classmethod
    async def _get_leverage_from_db(cls, user_id: int, exchange: str, symbol: str) -> float:
        _ = symbol
        from data.olimpos_data import db_operation
        result = db_operation("SELECT leverage FROM ayarlar WHERE user_id = ? AND exchange = ?",
            (user_id, exchange), fetch=True)
        if result and result[0]:
            return float(result[0][0])
        return 1.0

    safe_price_from_ticker = staticmethod(alarm_handlers.safe_price_from_ticker)
    ensure_float_list = staticmethod(alarm_handlers.ensure_float_list)
    safe_float = staticmethod(alarm_handlers.safe_float)

    # --- Persistence wrapper'ları (sync) ---
    @classmethod
    def save_active_signals(cls, force: bool = False):
        return alarm_persistence.save_active_signals(cls, force=force)

    @classmethod
    def next_alarm_id(cls):
        raise RuntimeError("next_alarm_id legacy. monitor_symbols içinde alarm_persistence.next_alarm_id kullanılmalı.")

    @classmethod
    def next_signal_id(cls, alarm_id):
        raise RuntimeError(
            "next_signal_id legacy. monitor_symbols içinde alarm_persistence.next_signal_id kullanılmalı.")

    @classmethod
    def load_active_signals(cls):
        return alarm_persistence.load_active_signals(cls)

    @classmethod
    def _append_closed_signal(cls, signal: dict):
        return alarm_persistence.append_closed_signal(cls, signal)

    @classmethod
    def sync_id_counters(cls):
        return alarm_persistence.sync_id_counters(cls)

    @classmethod
    def get_closed_signals(cls):
        return alarm_persistence.get_closed_signals(cls)

    @classmethod
    def load_recent_closed_signals(cls, hours: int = 24, anchor_hour=None):
        return alarm_persistence.load_recent_closed_signals(cls, hours=hours, anchor_hour=anchor_hour)

    # --- Scanning wrapper'ları ---

    @classmethod
    async def do_ai_scan(cls, timeframe: str, strategy: str, limit: int, chat_id: int, context, user_id: int,
            progress_callback=None):
        """AI taramasını başlatan sarmalayıcı."""
        # Bu metot, alarm_system/scanning.py içindeki asıl fonksiyonu çağırır.
        return await alarm_scanning.do_ai_scan(cls, timeframe, strategy, limit, chat_id, context, user_id,
            progress_callback)

    @classmethod
    async def _do_strategy_scan(cls, timeframe: str, strategy: str, limit: int, chat_id: int, context, user_id: int,
            progress_callback=None):
        """Strateji taramasını başlatan sarmalayıcı."""
        return await alarm_scanning._do_strategy_scan(cls, timeframe, strategy, limit, chat_id, context, user_id,
            progress_callback)

    @classmethod
    async def filter_symbols_by_volume_futures_only(cls, tickers: dict):
        return await alarm_scanning.filter_symbols_by_volume_futures_only(cls, tickers)

    @classmethod
    async def _analyze_single_coin_with_real_ai_safe(cls, symbol: str, timeframe: str = "1h"):
        return await alarm_scanning._analyze_single_coin_with_real_ai_safe(cls, symbol, timeframe)

    @classmethod
    async def fetch_ohlcv_with_retry(
            cls,
            symbol,
            timeframe: str = "15m",
            max_retries: int = 3,
            timeout: int = 30,
            context: CallbackContext | None = None,
    ):
        df = await alarm_scanning.fetch_ohlcv_with_retry(
            cls,
            symbol,
            timeframe=timeframe,
            max_retries=max_retries,
            timeout=timeout,
            context=context,
        )
        # timestamp zaten scanning.py içinde utc'ye çevriliyor; burada tekrar şart değil
        return df

    @classmethod
    def debug_log_scan_evaluation(
            cls, symbol: str, timeframe: str, strategy_id: str, thresholds: dict, row_meta: dict,
            tag_prefix: str = "EVAL"
    ) -> None:
        return alarm_scanning.debug_log_scan_evaluation(cls, symbol, timeframe, strategy_id, thresholds, row_meta,
            tag_prefix)

    @classmethod
    async def ai_coin_scanner_only(cls, context):
        return await alarm_scanning.ai_coin_scanner_only(cls, context)

    @classmethod
    async def collect_comprehensive_training_data(cls, exchange: str, symbols_count: int = 50):
        """
        Belirtilen borsa için kapsamlı eğitim verisi toplar.
        DÜZELTME: Artık 'context' yerine 'exchange' alıyor.
        """
        return await alarm_scanning.collect_comprehensive_training_data(cls, exchange=exchange,
            symbols_count=symbols_count)

    @classmethod
    async def train_ai_model_advanced(cls, training_data, exchange: str):
        """
        Gelişmiş AI modelini eğitir ve modelleri borsaya özel olarak kaydeder.
        """
        return await alarm_scanning.train_ai_model_advanced(cls, training_data=training_data,
            exchange=exchange)  

    # YENİ: Fonksiyon imzası 'exchange' parametresini alacak şekilde güncellendi.

    @classmethod
    async def train_ai_model_dynamic(cls, exchange: str, triggered_by_user_id: Optional[int] = None):
        """scanning.py içindeki asıl eğitim fonksiyonunu çağıran sarmalayıcı metot."""
        from strategies.alarm_system import scanning as alarm_scanning_1
        # Gelen triggered_by_user_id'yi doğrudan alt fonksiyona aktar
        return await alarm_scanning_1.train_ai_model_dynamic(cls, exchange=exchange,
            triggered_by_user_id=triggered_by_user_id)

    @classmethod
    async def get_ai_recommended_timeframe(cls, symbol):
        return await alarm_scanning.get_ai_recommended_timeframe(cls, symbol)

    @classmethod
    async def safe_fetch_tickers(cls, symbols: list[str] | None = None, retries: int = 3,
            delay: float = 2.0) -> dict:
        return await alarm_scanning.safe_fetch_tickers(cls, symbols=symbols, retries=retries, delay=delay)

    @classmethod
    async def soft_restart_exchange(cls, context=None, reason: str = ""):
        return await alarm_scanning.soft_restart_exchange(cls, context=context, reason=reason)

    @classmethod
    async def get_cached_tickers(cls):
        return await alarm_scanning.get_cached_tickers(cls)

    @classmethod
    async def emergency_ticker_fallback(cls, symbol: str):
        return await alarm_scanning.emergency_ticker_fallback(cls, symbol)  

    @classmethod
    async def get_futures_symbols_only(cls):
        return await alarm_scanning.get_futures_symbols_only(cls)

    @classmethod
    def is_valid_futures_market(cls, market_info: dict) -> bool:
        return alarm_scanning.is_valid_futures_market(cls, market_info)

    @classmethod
    def parse_futures_symbol(cls, symbol: str, market_info: dict) -> dict | None:
        return alarm_scanning.parse_futures_symbol(cls, symbol, market_info)



    @classmethod
    def get_futures_from_spot(cls, spot_symbol: str) -> str:
        return alarm_scanning.get_futures_from_spot(cls, spot_symbol)

    # --- Analytics wrapper'ları ---
    @classmethod
    def run_weekly_optimizer(cls, lookback_days: int = 7):
        return alarm_analytics.run_weekly_optimizer(cls, lookback_days=lookback_days)

    @classmethod
    def export_alarm_summary_for_autotune(
            cls, start_dt: str | None = None,
            end_dt: str | None = None,
            out_csv: str = "analytics/alarms_autotune_summary.csv"):
        return alarm_analytics.export_alarm_summary_for_autotune(cls, start_dt=start_dt, end_dt=end_dt,
            out_csv=out_csv)

    @classmethod
    def compute_strategy_fit_score(cls, meta: dict, strategy_id: str, timeframe: str, settings: dict,
            mode: str) -> float:
        return alarm_analytics.compute_strategy_fit_score(cls, meta, strategy_id, timeframe, settings,
            mode)

    @classmethod
    def diversify_and_limit(cls, candidates: list[dict], settings: dict, timeframe: str, strategy_id: str) -> list[
        dict]:
        return alarm_analytics.diversify_and_limit(cls, candidates, settings, timeframe, strategy_id)

    @classmethod
    def cross_deduplicate(cls, v1_list: list, v2_list: list, epsilon: float = 0.02) -> tuple[
        list, list]: 
        return alarm_analytics.cross_deduplicate(cls, v1_list, v2_list, epsilon)

    @classmethod
    def performance_period_summary(cls, period: str = "day", date=None, start=None, end=None):
        """
        KÖK NEDEN DÜZELTMESİ: DataFrame'i metne çevir.
        Bu fonksiyon, analytics modülünden gelen DataFrame'i yakalar ve onu
        Telegram'a gönderilebilir bir metin formatına dönüştürür.
        Bu, 'Object of type DataFrame is not JSON serializable' hatasını çözer.
        """
        try:
            # Analytics modülünden ham sonucu al
            summary_data = alarm_analytics.performance_period_summary(
                cls, period=period, date=date, start=start, end=end
            )

            # Gelen veri DataFrame ise, onu formatla
            if isinstance(summary_data, pd.DataFrame):
                if summary_data.empty:
                    return f"'{period.capitalize()}' periyodu için raporlanacak veri bulunamadı."
                else:
                    # DataFrame'i Markdown formatında, ``` içine alınmış bir metne dönüştür
                    return f"```\n{summary_data.to_markdown(index=False)}\n```"
            
            # Eğer zaten bir metin veya başka bir şeyse, olduğu gibi döndür
            return summary_data or f"'{period.capitalize()}' periyodu için raporlanacak veri bulunamadı."

        except Exception as e:
            logging.error(f"performance_period_summary içinde hata: {e}", exc_info=True)
            return f"❌ Rapor oluşturulurken bir hata oluştu: {e}"

    @staticmethod
    def _ensure_aware(dt):
        """
        dt -> timezone aware datetime (UTC)
        Kabul edilen türler:
          - datetime (naive veya aware)
          - pd.Timestamp
          - ISO-8601 string (Z, +00:00 vb. içerir/ içermez)
        Hatalı girişlerde None döner.
        """
        try:
            if dt is None:
                return None

            # Pandas Timestamp ise
            if isinstance(dt, pd.Timestamp):
                return dt.to_pydatetime()

            # Zaten datetime ise
            if isinstance(dt, datetime):
                if dt.tzinfo is None:
                    return dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)

            # String ise parse et
            if isinstance(dt, str):
                s = dt.strip()
                if not s:
                    return None

                # 'Z' sonu -> UTC
                if s.endswith('Z'):
                    s = s[:-1] + '+00:00'

                # dateutil varsa kullan
                try:
                    from dateutil import parser
                    parsed = parser.isoparse(s)

                except Exception:
                    # dateutil yoksa veya parse edemezse, manuel deneme

                    # Minimal fallback: yalnızca "YYYY-MM-DDTHH:MM:SS[.fff][+/-HH:MM]" varyantlarını dener
                    try:
                        # timezone varsa
                        if '+' in s[10:] or '-' in s[10:]:
                            # Python 3.11: fromisoformat çoğunu destekler
                            parsed = datetime.fromisoformat(s)
                        else:
                            # Naive ise
                            parsed = datetime.fromisoformat(s)

                    except Exception as e:
                        logging.error(f"Hata: {e}")
                        return None

                if isinstance(parsed, datetime):
                    if parsed.tzinfo is None:
                        return parsed.replace(tzinfo=timezone.utc)
                    return parsed.astimezone(timezone.utc)

                return None

            # Diğer tipler desteklenmez
            return None

        except Exception as e:
            logging.error(f"[_ensure_aware] {e}")
            return None

    @classmethod
    def _allow_event_now(cls, signal_id: str) -> bool:
        """
        Aynı signal_id için EVENT_MIN_INTERVAL_SEC içinde tek event'e izin verir.
        Ayrıca global flood guard uygular.
        """ 
        try:
            now = datetime.now(timezone.utc)

            # Sinyal bazlı debounce
            last = cls._last_event_ts.get(signal_id)
            if last and (now - last).total_seconds() < cls.EVENT_MIN_INTERVAL_SEC:
                return False

            cls._last_event_ts[signal_id] = now

            # Global flood guard (kayan pencere 60s)
            cls._event_counter_window = [t for t in cls._event_counter_window if (now - t).total_seconds() < 60]

            if len(cls._event_counter_window) >= cls.MAX_EVENTS_PER_MINUTE:
                # pencere çok dolu → bir miktar frenle
                return False

            cls._event_counter_window.append(now)
            return True

        except Exception as e:
            logging.debug(f"[ALLOW_EVENT_ERR] {e}")
            return True

    @classmethod
    def _symbol_cooldown_ok(cls, symbol: str) -> bool:
        """
        Aynı sembolü çok hızlı tekrar eklemeyi engeller.
        """
        try:
            now = datetime.now(timezone.utc)

            last = cls._last_symbol_added_ts.get(symbol)
            if last and (now - last).total_seconds() < cls.SYMBOL_ADD_COOLDOWN_SEC:
                return False

            cls._last_symbol_added_ts[symbol] = now
            return True

        except Exception as e:
            logging.debug(f"[SYM_COOLDOWN_ERR] {e}")
            return True

    @staticmethod
    def _c(txt, color):
        # ANSI renkler (log konsolunda renklendirme)
        colors = {
            'red':'\033[31m', 'green':'\033[32m', 'yellow':'\033[33m',
            'blue':'\033[34m', 'magenta':'\033[35m', 'cyan':'\033[36m',
            'reset':'\033[0m'
        }
        pre = colors.get(color, '')
        suf = colors['reset'] if pre else ''
        return f"{pre}{txt}{suf}"

    @classmethod
    def _okfail(cls, cond: bool):
        return cls._c("OK", "green") if cond else cls._c("FAIL", "red")

    @classmethod
    def _safe_ticker_volume_usd(cls, ticker: dict):
        try:
            if not isinstance(ticker, dict) or not ticker:
                return 0.0

            info = ticker.get("info") or {}

            # YENİ: Daha geniş volume anahtar kontrolü
            volume_keys = [
                "turnover", "quoteVolume", "volValue", "quote_volume",
                "amount_quote", "value", "volUsd", "volume24h",
                "turnover24h", "vol24h", "quotevol", "qtyVolume"
            ]

            for k in volume_keys:
                v = info.get(k)
                if v is not None and str(v).strip() not in ("", "None", "0", "null"):
                    try:
                        volume = float(v)
                        if volume > 0:
                            return volume

                    except Exception as e:
                        logging.error(f"Hata: {e}")

                continue
            # CCXT standard alanlar
            quote_vol = ticker.get("quoteVolume")
            if quote_vol is not None:
                try:
                    vol = float(quote_vol)
                    if vol > 0:
                        return vol

                except Exception as e:
                    logging.error(f"Hata: {e}")

                pass

            # Türetme hesaplama
            last = float(ticker.get("last") or ticker.get("close") or info.get("lastPrice") or 0.0)
            base_v = float(ticker.get("baseVolume") or info.get("baseVolume") or info.get("vol") or 0.0)

            if last > 0 and base_v > 0:
                return base_v * last

            return 0.0

        except Exception as e:
            logging.error(f"[SAFE_VOL_ERR] {e}")
            return 0.0

    @classmethod
    def _load_scan_settings(cls) -> dict:
        """
        YENİ MİMARİ: Scan ayarlarını ConfigService üzerinden okur.
        - Derin merge / defaults yok
        - Dosyaya otomatik yazma yok
        - Geriye uyumluluk yok
        """
        try:
            # ConfigService.init() zaten initialize_system içinde çağrılıyor.
            scans = ConfigService.get("scans", {}) or {}
            if not isinstance(scans, dict):
                raise TypeError(f"ConfigService.get('scans') dict değil: {type(scans)}")

            return scans

        except Exception as e:
            logger.exception(f"_load_scan_settings failed: {e}")
            return {}

    @classmethod
    def get_alarm_limit_from_tf_profiles(cls, tf: str, strategy_id: str) -> int:
        """
        Yeni mimari: scans.tf_profiles içinden limit okur.
        strategy_id: 'v1', 'v2' gibi.
        """
        tf_profiles = getattr(cls, "tf_profiles", None)
        if not tf_profiles:
            tf_profiles = getattr(getattr(cls, "scans", None), "tf_profiles", None)

        if not tf_profiles or tf not in tf_profiles:
            raise RuntimeError(f"tf_profiles yok/TF bulunamadı: tf={tf}")

        prof = tf_profiles[tf]

        # Burayı senin yeni yapına göre eşleştir:
        # Örn: prof["str_limits"] = {"v1":3,"v2":3}
        limits = prof.get("str_limits") or prof.get("strategy_limits") or {}
        if strategy_id not in limits:
            raise RuntimeError(f"Limit bulunamadı: tf={tf} strategy_id={strategy_id}")

        return int(limits[strategy_id])

    # === PATCH B.1: TF-based threshold helpers (ConfigService) ====================
    @classmethod
    def get_ai_thresholds_from_config(cls, tf: str) -> dict:
        """
        YENİ MİMARİ:
        AI tarama eşiklerini scans.tf_profiles.<tf>.ai_scan üzerinden alır.
        - per_scan_alarm_limit kaldırıldı (limitler ai_scan.limits.v1/v2)
        """
        ai = ConfigService.ai_settings(tf) or {}
        if not isinstance(ai, dict):
            ai = {}

        def val_of(d: dict, key: str, default: Any = None) -> Any:
            v = d.get(key)
            if isinstance(v, dict) and "value" in v:
                return v["value"]
            return v if v is not None else default

        th = {
            "enabled":bool(ai.get("enabled", True)),
            "min_conf":float(val_of(ai, "min_conf", 0.66)),
            "min_potential_pct":float(val_of(ai, "min_potential_pct", 1.0)),
            "min_volume_usd":float(val_of(ai, "min_volume_usd", 2_000_000)),
            "min_volume_ratio":float(val_of(ai, "min_volume_ratio", 0.8)),
            "diversify_by_sector":bool(ai.get("diversify_by_sector", True)),
            "alt_accept_rule":ai.get("alt_accept_rule") or {"enabled":False},
        }
        return th

    @classmethod
    def get_strat_thresholds_from_config(cls, tf: str, strategy_id: str) -> dict:
        """
        YENİ MİMARİ:
        Strateji tarama eşiklerini scans.tf_profiles.<tf>.strategy_scan.v1/v2 üzerinden alır.
        - per_scan_alarm_limit kaldırıldı (limitler strategy_scan.v1.limit / v2.limit)
        """
        ss = ConfigService.strat_settings(tf) or {}
        if not isinstance(ss, dict):
            ss = {}

        sid = "v1" if str(strategy_id).lower() == "v1" else "v2"
        raw = ss.get(sid) or {}
        if not isinstance(raw, dict):
            raw = {}

        def val_of(d: dict, key: str, default: Any = None) -> Any:
            v = d.get(key)
            if isinstance(v, dict) and "value" in v:
                return v["value"]
            return v if v is not None else default

        th = {
            "enabled":bool(ss.get("enabled", True)),
            "min_score":float(val_of(raw, "min_score", 60)),
            "min_volume_usd":float(val_of(raw, "min_volume_usd", 2_000_000)),
            "weights":raw.get("weights") or {},
        }
        return th

    @classmethod
    async def dispatch_ai_scan(
            cls,
            timeframe: str,
            strategies: list,
            chat_id: int,
            user_id: int,
            context: CallbackContext,
            update: Update = None
    ):
        _ = update
        if not chat_id:
            raise ValueError("dispatch_ai_scan: chat_id zorunludur")
        import asyncio
        sent_total = 0
        created_symbols = []
        errors = []

        sset = {str(s).strip().lower() for s in (strategies or []) if str(s).strip()}
        if not sset:
            return {'sent_count':0, 'created_symbols':[], 'total_processed':0, 'errors':["strategy boş"]}

        if sset >= {'v1', 'v2'}:
            # Tek çağrıda birleşik akış
            try:
                lim = cls._get_limit('ai', 'both', timeframe=timeframe)
                res = await cls.do_ai_scan(
                    timeframe=timeframe,
                    strategy='both',
                    limit=lim,
                    chat_id=chat_id,
                    context=context,
                    user_id=user_id
                )
                if isinstance(res, dict):
                    created_symbols.extend(res.get('created_symbols', []) or [])
                    sent_total += int(res.get('sent_count', 0))
            except Exception as err:
                errors.append(f"both: {err}")

        else:
            async def run_one(s):
                try:
                    scan_limit = cls._get_limit('ai', s, timeframe=timeframe)
                    return True, await cls.do_ai_scan(
                        timeframe=timeframe,
                        strategy=s,
                        limit=scan_limit,
                        chat_id=chat_id,
                        context=context,
                        user_id=user_id
                    ), s
                except Exception as er1:
                    return False, str(er1), s

            results = await asyncio.gather(*[run_one(s) for s in sset], return_exceptions=False)
            for ok, data, sid in results:
                if ok and isinstance(data, dict):
                    created_symbols.extend(data.get('created_symbols', []) or [])
                    sent_total += int(data.get('sent_count', 0))
                else:
                    errors.append(f"{sid}: {data}")

        return {
            'sent_count':sent_total,
            'created_symbols':created_symbols,
            'total_processed':len(created_symbols),
            'errors':errors
        }

    @classmethod
    async def dispatch_strategy_scan(
            cls,
            timeframe: str,
            strategies: list,
            chat_id: int,
            user_id: int,
            context,
            update: Update = None
    ):
        _ = update

        if not chat_id:
            raise ValueError("dispatch_strategy_scan: chat_id zorunludur")

        import asyncio
        sent_total = 0
        total_symbols = 0
        strategy_results = {}
        created_symbols = []

        # normalize
        sset = [str(s).strip().lower() for s in (strategies or []) if str(s).strip()]
        if not sset:
            return {'sent_count':0, 'created_symbols':[], 'total_processed':0, 'errors':["strategy boş"]}

        async def run_one(s):
            try:
                lim = cls._get_limit('strategy', s, timeframe=timeframe)
                res = await cls._do_strategy_scan(
                    timeframe=timeframe,
                    strategy=s,
                    limit=lim,
                    chat_id=chat_id,
                    context=context,
                    user_id=user_id
                )
                return res
            except Exception as err:
                logging.error(f"[RUN_ONE] Strateji {s} için hata: {err}")
                return {'sent_count':0, 'found_symbols':[], 'strategy':s}

        results = await asyncio.gather(*[run_one(s) for s in sset], return_exceptions=False)

        for r in results:
            if not isinstance(r, dict):
                continue

            sid = r.get('strategy') or 'unknown'
            symbols = r.get('found_symbols', []) or []

            for symbol_data in symbols:
                symbol = symbol_data.get('symbol') if isinstance(symbol_data, dict) else symbol_data

                meta_data = {
                    'strategy_scan':True,
                    'source':'strategy_scan',
                    'ai_confidence':0.70,
                    'potential_pct':100.0,
                    'technical_score':symbol_data.get('technical_score', 0.0) if isinstance(symbol_data, dict) else 0.0,
                    'volume_usd':symbol_data.get('volume_usd', 0.0) if isinstance(symbol_data, dict) else 0.0,
                    'momentum':symbol_data.get('momentum', 0.0) if isinstance(symbol_data, dict) else 0.0,
                    'volume_ratio':symbol_data.get('volume_ratio', 0.0) if isinstance(symbol_data, dict) else 0.0,
                    'score':symbol_data.get('score', 0.0) if isinstance(symbol_data, dict) else 0.0
                }

                success = await cls.add_alarm_debug(
                    context=context,
                    symbol=symbol,
                    timeframe=timeframe,
                    user_id=user_id,
                    strategy_id=sid,
                    source_meta=meta_data
                )

                if success:
                    created_symbols.append(symbol)

            if symbols:
                strategy_results[sid] = [s.get('symbol') if isinstance(s, dict) else s for s in symbols]
                total_symbols += len(symbols)

            sent_total += int(r.get('sent_count', 0) or 0)

        return {
            'sent_count':sent_total,
            'created_symbols':created_symbols,
            'total_processed':len(created_symbols)
        }

    @classmethod
    def _get_limit(cls, scan_type: str, strategy: str, timeframe: Optional[str] = None) -> int:
        """
        YENİ MİMARİ:
        Limitler:
          - AI: scans.tf_profiles.<tf>.ai_scan.limits.v1/v2
          - STRATEGY: scans.tf_profiles.<tf>.strategy_scan.v1.limit / v2.limit

        scan_type:
          - 'ai' veya 'ai_scan'
          - 'strategy' veya 'strategy_scan'

        strategy:
          - 'v1' / 'v2' / 'both'
        """
        try:
            tf = str(timeframe or "15m").strip()
            tf_profile = ConfigService.tf_profile(tf, {}) or {}
            if not isinstance(tf_profile, dict):
                tf_profile = {}

            st = str(scan_type or "").strip().lower()
            sid_raw = str(strategy or "v1").strip().lower()
            sid = "v1" if sid_raw == "v1" else ("v2" if sid_raw == "v2" else "both")

            if st in ("ai", "ai_scan"):
                ai = tf_profile.get("ai_scan", {}) or {}
                limits = (ai.get("limits", {}) or {})
                v1_lim = int(limits.get("v1", 3) or 3)
                v2_lim = int(limits.get("v2", 3) or 3)
                if sid == "v1":
                    return v1_lim
                if sid == "v2":
                    return v2_lim
                # both için: fetch tarafında güvenli olsun diye max
                return max(v1_lim, v2_lim)

            if st in ("strategy", "strategy_scan"):
                sc = tf_profile.get("strategy_scan", {}) or {}
                v1_lim = int(((sc.get("v1", {}) or {}).get("limit", 3)) or 3)
                v2_lim = int(((sc.get("v2", {}) or {}).get("limit", 3)) or 3)
                if sid == "v1":
                    return v1_lim
                if sid == "v2":
                    return v2_lim
                return max(v1_lim, v2_lim)

            # bilinmeyen scan_type
            return 3

        except Exception as e:
            logging.error(f"[GET_LIMIT_ERR] {e}")
            return 3

    @classmethod
    def _get_font(cls, size=None, bold=False):  # 'cls' parametresi eklendi
        size = size or ConfigService.get('charts.text_settings.template_font_size', 28)
        font_path = cls.TEMPLATE_FONT_PATH

        # Bold için farklı bir font yolu varsa
        if bold and hasattr(cls, 'TEMPLATE_BOLD_FONT_PATH'):
            font_path = cls.TEMPLATE_BOLD_FONT_PATH

        try:
            if font_path and os.path.exists(font_path):
                return ImageFont.truetype(font_path, size=size)
        except Exception as e:
            logging.warning(f"Font yüklenemedi: {e}")

        # Fallback
        try:
            # Bold için farklı bir font kullanılabilir
            font_name = "arialbd.ttf" if bold else "arial.ttf"
            return ImageFont.truetype(font_name, size=size)
        except Exception as e:
            logging.error(f"Hata: {e}")
            return ImageFont.load_default()

    @classmethod
    def _fmt_pct(cls, p):  # 'cls' parametresi eklendi
        try:
            return f"{p:+.2f}%"

        except Exception as e:
            logging.error(f"Hata: {e}")

            return "+0.00%"

    @staticmethod
    def _fmt_price_dynamic(v: float) -> str:
        """Fiyatı dinamik olarak ondalık basamak sayısıyla formatlar."""
        try:
            if v is None:
                return "-"
            v = float(v)
            if v == 0:
                return "0"
            av = abs(v)
            if av >= 100:
                return f"{v:.2f}"
            if av >= 1:
                return f"{v:.4f}"
            if av >= 0.1:
                return f"{v:.6f}"
            if av >= 0.01:
                return f"{v:.8f}"
            # Daha küçük – anlamlı hane bul
            # ilk sıfır olmayan basamak:
            s = f"{v:.12f}".rstrip("0")
            # ".00000..." ise fallback
            # Fazla uzamaması için max 12 karakter
            return s[:14]

        except Exception as e:
            logging.error(f"Hata: {e}")

            return str(v)

    @classmethod
    def _build_main_caption(cls, signal: dict):  # 'cls' parametresi eklendi ve tip belirtildi
        # Çok kısa özet
        hits = sum(1 for h in signal.get('targets_hit', []) if h)
        total = len(signal.get('targets', []))  
        sym = cls.to_display_symbol(signal.get('symbol'))
        sig_id = signal.get('signal_id')
        direction = signal.get('signal_type')
        return f"{ConfigService.get('messaging.main_caption_prefix', '')}{direction} {sym} ({hits}/{total}) ID:{sig_id}"  # DÜZELTME: sid -> sig_id

    @classmethod
    async def build_signal_template_text(
            cls,
            signal: dict,
            context: CallbackContext,
            current_price: Optional[float] = None,
            user_id: Optional[int] = None
    ):
        """
        Sinyal verilerinden metin tabanlı bir özet oluşturur.
        DÜZELTME: Hesaplama yöntemi (ATR/Sabit/Fib) bilgisini en alta ekler.
        EK: Trend kanıtı (Global + Yerel EMA200/ADX/TF) OPEN mesajında gösterilir.
        """
        _ = context
        direction = signal.get('signal_type', 'LONG')
        sym = cls.to_display_symbol(signal.get('symbol', '?'))

        # Kaldıraç bilgisini al
        leverage = 1.0
        if user_id:
            try:
                exchange_name = (signal.get('meta', {}) or {}).get('exchange', 'mexc')
                from data.olimpos_data import get_user_settings
                settings = get_user_settings(user_id, exchange_name)
                if settings and settings.get('leverage'):
                    leverage = float(settings['leverage'])
            except Exception:
                pass

        sig_id = signal.get('signal_id', '?')
        strat = signal.get('strategy_id', 'V1').upper()
        entry = float(signal.get('entry_price') or 0)
        targets = signal.get('targets', [])[:5]
        hits = signal.get('targets_hit', [])
        hit_times = signal.get('targets_hit_times', [])

        signal_time = cls._ensure_aware(signal.get('signal_time'))
        if not signal_time:
            signal_time = datetime.now(timezone.utc)

        # Anlık UPNL
        upnl = 0.0
        if current_price is not None and entry != 0:
            if direction == 'LONG':
                upnl = (current_price - entry) / entry * 100
            else:
                upnl = (entry - current_price) / entry * 100

        # ---------------------------------------------------------
        # ✅ TREND KANITI (OPEN mesajında göster)
        # Kaynak: signal['meta']['trend_snapshot'] (monitoring.py ekliyor)
        # ---------------------------------------------------------
        trend_lines = []
        try:
            meta = signal.get("meta") or {}
            ts = meta.get("trend_snapshot") if isinstance(meta, dict) else None
            if isinstance(ts, dict):
                g = ts.get("global") if isinstance(ts.get("global"), dict) else {}
                l = ts.get("local") if isinstance(ts.get("local"), dict) else {}

                def _fmt_ema(v):
                    try:
                        v = float(v)
                        return f"{v:.4f}" if v > 0 else "N/A"
                    except Exception:
                        return "N/A"

                def _fmt_adx(v):
                    try:
                        v = float(v)
                        return f"{v:.1f}" if v > 0 else "N/A"
                    except Exception:
                        return "N/A"

                g_tf = str(g.get("tf") or "1d")
                g_ema = _fmt_ema(g.get("ema200"))
                g_adx = _fmt_adx(g.get("adx"))
                g_net = str(g.get("net_trend") or g.get("raw_trend") or "❓ Bilinmiyor")

                l_tf = str(l.get("tf") or signal.get("timeframe") or "15m")
                l_ema = _fmt_ema(l.get("ema200"))
                l_adx = _fmt_adx(l.get("adx"))
                l_net = str(l.get("net_trend") or l.get("raw_trend") or "❓ Bilinmiyor")

                # İstenen 2 satırlık format:
                trend_lines = [
                    f"🧭 Global Trend (BTC, {g_tf}): EMA200={g_ema} | ADX={g_adx} → {g_net}",
                    f"🧭 Yerel Trend ({sym}, {l_tf}): EMA200={l_ema} | ADX={l_adx} → {l_net}",
                    ""
                ]
        except Exception:
            trend_lines = []

        # Hedef satırları
        lines_targets = []
        for idx, t in enumerate(targets, 1):
            is_hit = idx <= len(hits) and hits[idx - 1]
            icon = "✅" if is_hit else "⭕"
            pct = ((t - entry) / entry * 100) if entry else 0
            pct = pct * leverage

            if direction == 'SHORT':
                pct = -pct

            duration_str = ""
            if is_hit and idx <= len(hit_times) and hit_times[idx - 1]:
                hit_time = cls._ensure_aware(hit_times[idx - 1])
                if hit_time and signal_time:
                    duration_str = f" ({cls._human_duration(signal_time, hit_time)})"

            lines_targets.append(f"{icon} T{idx}: {cls._fmt_price_dynamic(t)} (%{pct:.2f}){duration_str}")

        # Durum ve Süre
        exit_type = signal.get('exit_type')
        durum = "STOP" if exit_type == 'STOP' else "Tamamlandı" if exit_type == 'TARGET_FINAL' else "Aktif"
        total_dur = cls._human_duration(signal_time)

        # Stop Loss Yüzdesi
        stop_loss = float(signal.get('stop_loss') or 0)
        sl_pct = 0.0
        if entry > 0:
            sl_pct = abs((entry - stop_loss) / entry * 100)
            sl_pct = sl_pct * leverage

        # Ana metin bloğu
        text_lines = [
            f"[{sym} | {direction} | {strat}] SİNYAL",
            f"Sinyal ID: {sig_id}",
            f"UPNL: {upnl:+.2f}% (x{int(leverage):.0f} = {upnl * leverage:+.2f}%)",
            "",
            *trend_lines,
            f"Giriş   : {cls._fmt_price_dynamic(entry)}",
            f"Stop    : {cls._fmt_price_dynamic(stop_loss)} (%{sl_pct:.2f})",
            "Hedefler:",
            *lines_targets,
            "",
            f"Durum   : {durum}",
            f"Açılış  : {signal_time.strftime('%H:%M:%S')} (Süre: {total_dur})"
        ]

        # --- HESAPLAMA YÖNTEMİ BİLGİSİ ---
        calc_info = (signal.get('meta', {}) or {}).get('calc_method', {}) or {}
        sl_src = calc_info.get('sl', 'Bilinmiyor')
        tp_src = calc_info.get('tp', 'Bilinmiyor')

        if not (sl_src == 'Bilinmiyor' and tp_src == 'Bilinmiyor'):
            text_lines.append("")
            text_lines.append(f"ℹ️ Hesap: Hedefler({tp_src}) | SL({sl_src})")

        return "\n".join(text_lines)

    @classmethod
    async def _send_event_image(
            cls,
            context: CallbackContext,
            signal: dict,
            event_type: str,
            event_meta: dict | None = None,
            target_index: Optional[int] = None,
            caption: Optional[str] = None,
            user_id: Optional[int] = None,
            chart_buf_override: BytesIO = None
    ):
        """
        Oluşturulan olay kartını gönderir.
        DÜZELTME: 'inspect' kütüphanesi ile kesin Sınıf/Nesne ayrımı yapıldı.
        """
        try:
            # --- KESİN ÇÖZÜM: SINIF MI NESNE Mİ? ---
            import inspect

            # Eğer chart_renderer bir SINIF ise (Class), onu nesneye (Instance) çevir.
            if inspect.isclass(cls.chart_renderer):
                cls.chart_renderer = cls.chart_renderer()

            # Artık elimizde kesinlikle bir nesne var
            renderer = cls.chart_renderer
            # ---------------------------------------

            # Olay Metası Hazırla
            event_meta = dict(event_meta or {})
            if target_index is not None and "target_num" not in event_meta:
                event_meta["target_num"] = target_index + 1

            # Olay Kartını Çizdir
            buf = await renderer.render_event_card(
                signal=signal,
                event_type=event_type,
                event_meta=event_meta,
                user_id=user_id or signal.get('user_id'),
                chart_buf=chart_buf_override
            )

            if not buf:
                logging.error(f"[{event_type}] için olay kartı görseli oluşturulamadı.")
                return

            if not caption:
                caption = f"{signal.get('symbol', 'Unknown')} - {event_type}"

            # Gönderim (Reply Kısmı)
            main_messages = signal.get('main_messages', [])
            sent_successfully = False

            if main_messages:
                for mm in main_messages:
                    channel_id = mm.get('channel_id')
                    message_id = mm.get('message_id')
                    if not channel_id or not message_id:
                        continue
                    try:
                        buf.seek(0)
                        await context.bot.send_photo(
                            chat_id=channel_id,
                            photo=buf,
                            caption=caption,
                            reply_to_message_id=message_id
                        )
                        sent_successfully = True
                    except Exception as e:
                        logging.warning(f"[EV_REPLY_ERR] Yanıt verilemedi (ch={channel_id}): {e}")

            # Fallback: Yeni Mesaj Olarak Gönder
            if not sent_successfully:
                target_channels = []
                final_user_id = user_id or signal.get('user_id')

                if final_user_id:
                    try:
                        # get_user_notification_channel_ids SYNC -> event loop bloklamasın diye thread'e al
                        target_channels = await asyncio.to_thread(get_user_notification_channel_ids, final_user_id)
                    except Exception:
                        target_channels = []

                if not target_channels:
                    target_channels = cls.channel_ids

                for ch_id in target_channels:
                    try:
                        buf.seek(0)
                        await context.bot.send_photo(
                            chat_id=ch_id,
                            photo=buf,
                            caption=caption
                        )
                    except Exception as e:
                        logging.error(f"[EV_SEND_NEW_ERR] Gönderim hatası (ch={ch_id}): {e}")

            if hasattr(buf, 'close'):
                buf.close()

        except Exception as e:
            logging.error(f"[_send_event_image genel hata] {e}", exc_info=True)

    @classmethod
    def _diff_dicts(cls, old: dict, new: dict, path=""):
        changes = []
        keys = set(old.keys()) | set(new.keys())
        for k in keys:
            full = f"{path}.{k}" if path else k
            if k not in old:
                changes.append({'param':full, 'old':None, 'new':new[k], 'type':'added'})
            elif k not in new:
                changes.append({'param':full, 'old':old[k], 'new':None, 'type':'removed'})
            else:
                if isinstance(old[k], dict) and isinstance(new[k], dict):
                    changes.extend(cls._diff_dicts(old[k], new[k], full))
                else:
                    if old[k] != new[k]:
                        changes.append({'param':full, 'old':old[k], 'new':new[k], 'type':'modified'})
        return changes

    @classmethod
    def save_strategy_version(cls, change_reason: str = "manual change"):  # 'cls' parametresi eklendi
        try:
            cfg = cls.strategy.get('versioning', {})
            if not cfg.get('enabled', True):
                return
            # Var olan max id'yi dosyadan bul (yalnızca ilk çağrıda veya counter=0 iken)
            try:
                if (cls.strategy_version_counter == 0
                        and os.path.exists(cfg.get('history_file', cls._strategy_history_file))):
                    with open(cfg.get('history_file', cls._strategy_history_file), "r", encoding="utf-8") as f:
                        mx = 0
                        for ln in f:
                            try:
                                rec = json.loads(ln.strip())
                                vid = rec.get('version_id')
                                if isinstance(vid, int) and vid > mx:
                                    mx = vid

                            except Exception as _e:  # 'e' is not used
                                logging.error(f"Hata: {_e}")

                                continue
                    cls.strategy_version_counter = mx
            except Exception as _error:
                logging.debug(f"versiyon counter sync hata: {_error}")

            snapshot = copy.deepcopy(cls.strategy)
            cls.strategy_version_counter += 1
            record = {
                'version_id':cls.strategy_version_counter,
                'timestamp':datetime.now(timezone.utc).isoformat(),
                'reason':change_reason,
                'snapshot':snapshot
            }
            with open(cfg.get('history_file', cls._strategy_history_file), "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            # Trim
            cls._trim_strategy_history()
            logging.info(f"Strategy version kaydedildi v{cls.strategy_version_counter}")
        except Exception as e:
            logging.error(f"save_strategy_version hata: {e}")  # 'e' is not used

    @classmethod
    def save_strategy_version_with_snapshot(cls, change_reason: str, snapshot: dict):
        try:  
            cfg = cls.strategy.get('versioning', {})
            if not cfg.get('enabled', True):
                return

            # counter sync (mevcut koddan kopyala)
            try:
                if (cls.strategy_version_counter == 0
                        and os.path.exists(cfg.get('history_file', cls._strategy_history_file))):
                    with open(cfg.get('history_file', cls._strategy_history_file), "r", encoding="utf-8") as f:
                        mx = 0
                        for ln in f:
                            try:
                                rec = json.loads(ln.strip())
                                vid = rec.get('version_id')
                                if isinstance(vid, int) and vid > mx:
                                    mx = vid

                            except Exception as e:
                                logging.error(f"Hata: {e}")

                                continue
                        cls.strategy_version_counter = mx
            except Exception as _error:
                logging.debug(f"versiyon counter sync hata: {_error}")

            cls.strategy_version_counter += 1
            record = {
                'version_id':cls.strategy_version_counter,
                'timestamp':datetime.now(timezone.utc).isoformat(),
                'reason':change_reason,
                'snapshot':snapshot
            }

            with open(cfg.get('history_file', cls._strategy_history_file), "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

            cls._trim_strategy_history()
            logging.info(f"Strategy version kaydedildi (custom snapshot) v{cls.strategy_version_counter}")

        except Exception as e:
            logging.error(f"save_strategy_version_with_snapshot hata: {e}")

    @classmethod
    def _trim_strategy_history(cls):
        cfg = cls.strategy.get('versioning', {})
        max_versions = cfg.get('max_versions', 200)
        path = cfg.get('history_file', cls._strategy_history_file)
        if not os.path.exists(path):
            return
        try:
            lines = open(path, "r", encoding="utf-8").read().strip().splitlines()
            if len(lines) <= max_versions:
                return
            # Son max_versions satırı bırak
            trimmed = lines[-max_versions:]
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(trimmed) + "\n")
        except Exception as e:
            logging.error(f"trim history hata: {e}")

    @classmethod
    def list_strategy_versions(cls, limit=20):
        cfg = cls.strategy.get('versioning', {})
        path = cfg.get('history_file', cls._strategy_history_file)
        if not os.path.exists(path):
            return []
        out = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in reversed(f.readlines()):
                    if len(out) >= limit:
                        break
                    try:
                        rec = json.loads(line.strip())
                        out.append(rec)
                    except (TypeError, ValueError):
                        continue
        except (TypeError, ValueError):
            pass
        return out

    @classmethod
    def restore_strategy_version(cls, version_id: int) -> bool:
        cfg = cls.strategy.get('versioning', {})
        path = cfg.get('history_file', cls._strategy_history_file)
        if not os.path.exists(path):
            return False

        chosen = None
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line.strip())
                    except Exception:
                        continue
                    if rec.get('version_id') == version_id:
                        chosen = rec
                        break
        except Exception:
            return False

        if not chosen:
            return False

        snap = chosen.get("snapshot")
        if not isinstance(snap, dict):
            return False

        # ✅ 1) Belleğe al
        cls.strategy = snap
        cls.runtime_strategy = snap

        # ✅ 2) ConfigService'e uygula (AUTO/MANUAL kuralını bozmadan)
        # En güvenlisi: manual config'e yaz (çünkü bu kullanıcı aksiyonu)
        try:
            ConfigService.set_manual("strategy", snap.get("strategy", snap))
            # Eğer scans de snapshot’ta varsa:
            if "scans" in snap and isinstance(snap["scans"], dict):
                ConfigService.set_manual("scans", snap["scans"])
            ConfigService.save_manual_config()
        except Exception as e:
            logging.error(f"[RESTORE_VERSION_APPLY_ERR] {e}", exc_info=True)
            return False

        logging.info(f"Versiyon geri yüklendi ve uygulandı: v{version_id}")
        return True

    @classmethod
    def ensure_models_directory(cls, models_path: str = "models", required_files: List[str] = None,
            max_file_age_days: int = 30, min_file_size_bytes: int = 100) -> Dict[str, Any]:  
        """Model dizini kontrol - Daha esnek versiyon"""
        try:
            os.makedirs(models_path, exist_ok=True)

            # Varsayılan gerekli dosyalar
            if required_files is None:
                required_files = ["gradient_boost_model.pkl", "lightgbm_model.pkl", "metadata.pkl",
                    "random_forest_model.pkl", "scaler.pkl", "xgboost_model.pkl"]

            # DÜZELTME: Dosya silme işlemini kaldır - sadece kontrol et
            file_status = {}
            total_file_size = 0
            valid_files = 0
            old_files = 0
            small_files = 0  

            current_time = datetime.now(timezone.utc)

            # DÜZELTME: result değişkenini başlangıçta tanımla
            result = {'directory_exists':True, 'files':{}, 'total_file_size':0, 'valid_file_count':0,
                'old_file_count':0, 'small_file_count':0, 'any_file_exists':False, 'all_files_valid':False,
                'models_usable':False}

            for file_name in required_files:
                full_path = os.path.join(models_path, file_name)

                file_info = {'exists':False, 'size_bytes':0, 'age_days':float('inf'), 'is_valid':False}

                if os.path.exists(full_path):
                    # Dosya bilgileri
                    file_stat = os.stat(full_path)
                    file_size = file_stat.st_size
                    file_age = (current_time - datetime.fromtimestamp(file_stat.st_mtime)).days

                    # DÜZELTME: Scaler için özel kontrol
                    if file_name == 'scaler.pkl':  # 'joblib' is defined abov
                        if file_size >= 50:  # 50 byte yeterli
                            try:  # 'joblib' is defined above
                                with open(full_path, 'rb') as f:
                                    scaler_obj = joblib.load(f)
                                    # Scaler objesi kontrolü
                                    if hasattr(scaler_obj, 'mean_') or hasattr(scaler_obj, 'scale_'):
                                        file_info.update({'exists':True, 'size_bytes':file_size, 'age_days':file_age,
                                            'is_valid':True})
                                        valid_files += 1
                                        total_file_size += file_size
                            except Exception as scaler_error:
                                logging.warning(
                                    f"❌ Scaler dosyası geçersiz: {scaler_error}")  # 'scaler_error' is not used
                    else:
                        # Diğer dosyalar için normal kontrol
                        if file_size >= min_file_size_bytes:
                            if file_age <= max_file_age_days:
                                try:
                                    # İçerik validasyonu
                                    with open(full_path, 'rb') as f:
                                        joblib.load(f)
                                    file_info.update(
                                        {'exists':True, 'size_bytes':file_size, 'age_days':file_age,
                                            'is_valid':True})
                                    valid_files += 1  # 'valid_files' is used below
                                    total_file_size += file_size
                                except Exception as validation_error:
                                    logging.warning(
                                        f"❌ {file_name} dosyası geçersiz: {validation_error}")  # 'validation_error' is not used
                            else:
                                old_files += 1
                                logging.info(f"{file_name} dosyası eski ({file_age} gün) - Silmeden kullanılacak")
                        else:
                            small_files += 1
                            logging.info(f"ℹ️ {file_name} küçük ({file_size} byte) - Silmeden kullanılacak")

                file_status[file_name] = file_info

            # DÜZELTME: result değişkenini döngü sonunda güncelle (tüm veriler toplanmış olacak)
            result.update({'files':file_status, 'total_file_size':total_file_size, 'valid_file_count':valid_files,
                'old_file_count':old_files, 'small_file_count':small_files,
                'any_file_exists':any(info['exists'] for info in file_status.values()),
                'all_files_valid':valid_files >= 4,  # DÜZELTME: En az 4 model yeterli
                'models_usable':valid_files >= 3  # DÜZELTME: En az 3 model kullanılabilir
            })

            # Log kaydı
            logging.info(f"📦 Model dizini kontrolü: "
                         f"{valid_files} geçerli, "
                         f"{old_files} eski, "
                         f"{small_files} küçük dosya")

            return result

        except Exception as e:
            logging.error(f"❌ Models klasörü hatası: {e}")
            return {'directory_exists':False, 'files':{}, 'any_file_exists':False, 'all_files_valid':False,
                'models_usable':False}

    @classmethod
    def get_last_train_time_for_exchange(cls, exchange: str) -> Optional[datetime]:
        """
        Belirli bir borsa için son model eğitim zamanını metadata.json'dan okur.
        DÜZELTME: Dosya bulunamadığında veya JSON hatası olduğunda None döndürür.
        """
        metadata_path = "models/metadata.json"
        if not os.path.exists(metadata_path):
            logger.warning(f"Metadata dosyası bulunamadı: {metadata_path}")
            return None

        try:
            with open(metadata_path, 'r') as f:
                all_metadata = json.load(f)

            exchange_meta = all_metadata.get(str(exchange).lower())
            if exchange_meta and "timestamp" in exchange_meta:
                # Zaman damgasını UTC olarak parse et
                return datetime.fromisoformat(exchange_meta["timestamp"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            logger.error(f"'{exchange}' için son eğitim zamanı okunurken hata: {e}")
            return None
        return None

    @classmethod
    async def initialize_ai_system(cls, context):  # 'cls' parametresi eklendi
        """
        AI sistem başlatma - Daha akıllı kontrol
        """
        try:
            exchange_name = context.user_data.get('exchange', 'mexc')
            # Model dizini kontrolü
            models_check = cls.ensure_models_directory()

            # DÜZELTME: Daha esnek kontrol
            if not models_check.get('models_usable', False):
                logging.warning("⚠️ Model dosyaları yetersiz, yeniden eğitim gerekli")

                # Sadece gerçekten gerekli olduğunda eğit
                if models_check.get('valid_file_count', 0) < 2:
                    logging.info("🎓 Kapsamlı eğitim başlatılıyor...")

                    # Eğitim verisi toplama # DÜZELTME: `collect_comprehensive_training_data` doğru parametre ile çağrılıyor.
                    training_data = await cls.collect_comprehensive_training_data(
                        exchange=exchange_name)  

                    # Eğitim kontrolü
                    if training_data and len(training_data) > 50:
                        training_success = await cls.train_ai_model_advanced(training_data=training_data,
                            exchange=exchange_name)

                        if training_success:
                            logging.info("✅ Kapsamlı AI model eğitimi tamamlandı")
                        else:
                            logging.error("❌ Kapsamlı eğitim başarısız")
                    else:
                        logging.error("❌ Yetersiz eğitim verisi")
                else:
                    logging.info("ℹ️ Mevcut modeller kullanılabilir durumda")

            # Model yükleme - AI model yoksa oluştur
            if not cls._ai_model:
                cls._ai_model = RealAIModel()

            # Model yüklemeyi dene
            models_loaded = cls._ai_model.load_models()

            if not models_loaded:
                logging.warning("⚠️ Model yüklenemedi, basit eğitim yapılacak")
                exchange_name = context.user_data.get('exchange', 'mexc')
                await cls.train_ai_model_dynamic(exchange=exchange_name,
                    triggered_by_user_id=context.user_data.get('user_id'))
            else:
                logging.info("✅ AI modelleri başarıyla yüklendi")

        except Exception as e:
            logging.error(f"❌ AI sistem başlatma hatası: {e}")  # 'e' is not used

        # YENİ: Bot başlangıcında runtime stratejisini yükle
        cls.load_runtime_strategy()

    @classmethod
    def _is_rate_limit_error(cls, err: Exception) -> bool:
        # Telegram ve CCXT yaygın rate limit kod/mesajları
        try:
            msg = str(err) if err else ""
            if any(code in msg for code in ("429", "Too Many Requests", "Retry after", "RetryAfter")):
                return True
            if any(code in msg for code in ("418", "510")):
                return True
            return False
        except (ValueError, TypeError) as e:
            logging.error(f"Spesifik hata: {e}")
        return False

    @classmethod
    async def _retry_async(cls, func, *args, retries: int = 3, base_delay: float = 0.7, max_delay: float = 6.0,
            jitter: bool = True, **kwargs):  
        """
        Exponential backoff + jitter ile güvenli yeniden deneme yardımcı fonksiyonu.
        - func: awaitable callable
        - retries: maksimum deneme (ilk çağrı + retries kez retry = toplam retries+1 olabilir)
        """
        import random
        attempt = 0
        last_err = None
        while attempt <= retries:
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                last_err = e
                # Rate limit/geçici hata ise bekleyip tekrar dene
                if cls._is_rate_limit_error(e) or isinstance(e, (asyncio.TimeoutError,)):
                    delay = min(max_delay, base_delay * (2 ** attempt))
                    if jitter:
                        delay = delay * (0.75 + 0.5 * random.random())
                    logging.warning(
                        f"[RETRY] {getattr(func, '__name__', 'async_call')} attempt={attempt + 1}/{retries + 1} delay={delay:.2f}s err={e}")
                    await asyncio.sleep(delay)
                    attempt += 1
                    continue
                else:
                    # Hızlı başarısız – rate limit olmayan hata
                    raise
        # Tüm denemeler bitti
        raise last_err if last_err else RuntimeError("Unknown retry error")

    @classmethod
    async def _process_high_potential_futures_coins(cls, context, rows, limit: int = 10, ):
        _ = context
        """
        Duplicate guard eklendi.
        ÇALIŞMA MODUNDA (run_ai_strategy_active=True ve allow_scans_while_running=False) ALARM KURMA!
        """
        if not rows:
            return
        if cls.run_ai_strategy_active and not cls.allow_scans_while_running:
            logging.info("[RUN_MODE] _process_high_potential_futures_coins atlandı (alarm kurma yok)")
            return

        selected = rows[:limit]
        # Var olan anahtar seti
        existing = {
            (str(a.get('symbol', '')).strip().upper(),
                str(a.get('timeframe', '15m')).strip(),
                str(a.get('strategy_hint') or a.get('strategy_id') or 'v1').strip().lower(),
                str(a.get('market_type', 'futures')).strip().lower())
            for a in cls.active_symbols if isinstance(a, dict)
        }
        for row in selected:
            sym = str(row.get("symbol") or '').strip().upper()
            if not sym:
                continue
            tf = str(row.get("timeframe") or "1h").strip()
            strat = str(row.get("strategy_id") or "v1").strip().lower()
            key = (sym, tf, strat, 'futures')

            if key in existing:
                logger.info("[AI_SCAN_SKIP_DUP] %s %s %s", sym, tf, strat)
                continue

            if not cls._symbol_cooldown_ok(sym):
                logger.info("[AI_SCAN_COOLDOWN] %s yakın zamanda eklendi, atlanıyor", sym)
                continue

    @classmethod
    def _guard_time_diff(cls):
        try:
            if hasattr(cls.exchange, 'options'):
                td = cls.exchange.options.get('timeDifference', None)

                if td is None or not isinstance(td, int):
                    cls.exchange.options.pop('timeDifference', None)

        except Exception as e:
            logging.error(f"Hata: {e}")

            pass

    @classmethod
    def clean_active_symbols(cls):
        """
        Active symbols listesini temizle ve standardize et
        """
        try:  
            cleaned_symbols = []
            for alarm in cls.active_symbols:
                try:
                    # Dict formatında ise direkt ekle
                    if isinstance(alarm, dict) and 'symbol' in alarm:
                        cleaned_symbols.append(alarm)
                    # String formatında ise dict'e çevir
                    elif isinstance(alarm, str):
                        core = cls.normalize_symbol(alarm) or alarm
                        cleaned_symbols.append({
                            'symbol':core,
                            'timeframe':'15m',
                            'ai_suggested':False,
                            'created_at':datetime.now(timezone.utc)
                        })

                except Exception as clean_error:
                    logging.error(f"❌ Alarm temizleme hatası: {clean_error}")
                    continue

            cls.active_symbols = cleaned_symbols
            logging.info(f"✅ Active symbols listesi temizlendi: {len(cleaned_symbols)} alarm")

        except Exception as e:
            logging.error(f"❌ Active symbols temizleme hatası: {str(e)}")

    @classmethod
    def deduplicate_active_symbols(cls, verbose=True):
        """
        active_symbols içindeki tekrarları (symbol,timeframe,strategy) bazında temizler.
        Normalizasyon:
          - symbol: STRIP + UPPER
          - timeframe: strip 
          - strategy_hint: lower 
        """
        try:
            seen = set()
            new_list = []
            removed = 0
            for alarm in cls.active_symbols:
                if not isinstance(alarm, dict):
                    continue
                sym_raw = str(alarm.get('symbol', '')).strip()
                sym = cls.normalize_symbol(sym_raw) or sym_raw.strip().upper()
                tf = str(alarm.get('timeframe', '15m')).strip()
                strat = str(alarm.get('strategy_hint') or alarm.get('strategy_id') or 'v1').strip().lower()

                mtype = str(alarm.get('market_type') or 'futures').strip().lower()
                key = (sym, tf, strat, mtype)

                # Ve normalize geri yaz:
                alarm['market_type'] = mtype
                if key in seen:
                    removed += 1
                    continue

                # Normalize geri yaz
                alarm['symbol'] = sym
                alarm['timeframe'] = tf
                alarm['strategy_hint'] = strat
                alarm['strategy_id'] = strat
                seen.add(key)
                new_list.append(alarm)

            cls.active_symbols = new_list
            if verbose and removed > 0:
                logging.info(f"[DEDUP] Duplicate alarm temizliği: silinen={removed}, kalan={len(new_list)}")
        except Exception as e:
            logging.error(f"[DEDUP_ERR] {e}")

    @classmethod
    def calculate_movement_potential(cls, df, ai_prediction):
        """Hareket potansiyeli hesapla - Güvenli versiyon"""
        try:
            # DataFrame kontrolü
            if df.empty or len(df) < 2:
                logging.warning("DataFrame boş veya yetersiz veri")
                return 0.0

            # AI prediction kontrolü
            if not isinstance(ai_prediction, dict):
                logging.warning("AI prediction dict formatında değil")
                return 0.0

            # Volatilite analizi
            try:
                returns = df['close'].pct_change().dropna()
                if returns.empty:
                    volatility = 0
                else:
                    volatility = returns.std() * np.sqrt(24)  # Günlük volatilite
            except Exception as vol_error:
                logging.error(f"Volatilite hesaplama hatası: {vol_error}")
                volatility = 0

            # Hacim artışı
            try:
                if len(df) >= 20:
                    recent_volume = df['volume'].iloc[-5:].mean()
                    past_volume = df['volume'].iloc[-20:-5].mean()
                    volume_ratio = recent_volume / past_volume if past_volume > 0 else 1
                else:
                    volume_ratio = 1
            except Exception as vol_ratio_error:
                logging.error(f"Hacim oranı hesaplama hatası: {vol_ratio_error}")
                volume_ratio = 1

            # AI güven skoru
            confidence = ai_prediction.get('confidence', 0)
            if not isinstance(confidence, (int, float)):
                confidence = 0

            # Teknik momentum
            try:
                if len(df) >= 14:
                    close_values = df['close'].values.astype(float)
                    rsi = cls.calculate_rsi_safe(close_values)
                    momentum_score = abs(rsi - 50) / 50
                else:
                    momentum_score = 0
            except Exception as rsi_error:
                logging.error(f"RSI hesaplama hatası: {rsi_error}")
                momentum_score = 0

            # Potansiyel hareket hesaplaması
            base_potential = volatility * 100 * 24  # 24 saatlik potansiyel
            volume_multiplier = min(volume_ratio, 3.0)  # Max 3x çarpan
            confidence_multiplier = confidence
            momentum_multiplier = momentum_score

            total_potential = (base_potential *
                               volume_multiplier *
                               confidence_multiplier *
                               (1 + momentum_multiplier))

            # NaN kontrolü ve sınırlama
            if np.isnan(total_potential) or np.isinf(total_potential):
                total_potential = 0.0

            return min(max(total_potential, 0.0), 200.0)  # 0-200% arası sınırla

        except Exception as e:
            logging.error(f"❌ Hareket potansiyeli hesaplama hatası: {str(e)}")
            return 0.0

    @classmethod
    def calculate_rsi_safe(cls, prices: Union[List[float], np.ndarray], period: int = 14) -> float:
        """Güvenli RSI hesaplama"""
        try:
            if len(prices) < period + 1:
                return 50.0

            # prices'i np.ndarray'ye çevir (eğer list ise)
            prices_arr = np.array(prices, dtype=np.float64)  # Düzeltme: Açık float64 tipi belirt

            deltas = np.diff(prices_arr)  # float array
            gains = np.where(deltas > 0, deltas, 0.0)  # Düzeltme: 0 yerine 0.0 kullan (float tutarlılığı)
            losses = np.where(deltas < 0, -deltas, 0.0)  # Düzeltme: 0 yerine 0.0 kullan

            avg_gain = float(np.mean(gains[-period:]))  # Düzeltme: Açık float cast
            avg_loss = float(np.mean(losses[-period:]))  # Düzeltme: Açık float cast

            if avg_loss == 0:
                return 100.0

            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

            return float(
                max(0.0, min(100.0, rsi)))  # Düzeltme: 0 ve 100'ü 0.0 ve 100.0 olarak değiştir (tür tutarlılığı)

        except Exception as e:
            logging.error(f"❌ RSI hesaplama hatası: {e}")
            return 50.0

    @classmethod
    async def send_ai_scan_results(cls, context, coins: List[Dict[str, Any]]):
        """
        coins: [{'symbol': 'BTC/USDT:USDT', 'direction': 'LONG', 'confidence': 0.8,
        'movement_potential': 90.0, 'volume_24h_usdt': 123456789}, ...]
        """
        channel_id = cls.channel_ids[0] if cls.channel_ids else None
        if not channel_id:
            return

        valid = []
        for coin in coins:
            sym = cls.normalize_symbol(coin.get('symbol', ''))
            if not sym:
                continue
            if not cls.has_futures_market(sym):
                logger.warning("[FUTURES_SKIP_NO_MARKET] %s ai_scan sonrası borsada futures kontratı yok", sym)
                continue
            valid.append({**coin, 'symbol':sym})

        if not valid:
            message = "Uygun vadeli işlem kontratı bulunan coin bulunamadı."
        else:
            lines = ["Vadeli İşlem AI Tarama Sonuçları:"]
            for coin in valid:
                pot = float(coin.get('movement_potential', coin.get('potential_move', 0.0)) or 0.0)
                conf = float(coin.get('confidence', 0.0) or 0.0)
                vol = float(coin.get('volume_24h_usdt', 0.0) or 0.0)
                lines.append(f"{coin['symbol']} | Pot: {pot:.1f}% | Conf:{conf:.2f} | Vol:${vol:,.0f}")
            message = "\n".join(lines)

        try:
            max_tg = 3900  # Telegram mesaj sınırı (güvenli marj)
            if len(message) > max_tg:
                chunks = []
                parts = message.splitlines()
                cur_lines, cur_len = [], 0
                for line in parts:
                    if cur_len + len(line) + 1 > max_tg:
                        chunks.append("\n".join(cur_lines))
                        cur_lines, cur_len = [line], len(line) + 1
                    else:
                        cur_lines.append(line)
                        cur_len += len(line) + 1
                if cur_lines:
                    chunks.append("\n".join(cur_lines))
                for chunk in chunks:
                    await safe_send_message(context.bot,chat_id=channel_id, text=chunk)
            else:
                await safe_send_message(context.bot,chat_id=channel_id, text=message)
            logger.info("✅ Vadeli İşlem AI tarama mesajı gönderildi: %s", channel_id)
        except Exception as e:
            logger.error("[AI_SCAN_SEND_ERR] %r", e)

    @classmethod
    def log_signal_event(cls, symbol: str, direction: str, entry_price: float, meta: dict,
            alarm_id: Optional[str] = None, signal_id: Optional[str] = None):  
        """Sinyal açılış olayını loglar."""
        record = {
            'type':'signal_open',
            'timestamp':datetime.now(timezone.utc).isoformat(),
            'symbol':symbol,
            'alarm_id':alarm_id,
            'signal_id':signal_id,
            'direction':direction,
            'entry_price':entry_price,  # DÜZELTME: Sinyal açılışında TÜM meta verileri logla.
            'meta':meta or {},
            'runtime_params':cls.runtime_strategy  # snapshot
        }
        try:
            with open(cls._signal_log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            logging.error(f"signal log yazılamadı: {symbol} {direction} {entry_price} {meta}")

    @classmethod
    def log_trade_outcome(cls, symbol, direction, exit_type, exit_price, entry_price, targets_hit_count, opened_at_iso,
            exit_stage='final', signal_ref=None):
        """
        direction: 'LONG' / 'SHORT', exit_type: 'TARGET_FINAL' | 'STOP' | 'TARGET',
        exit_stage: 'partial' (sadece ara hedef kaydı) ya da 'final'
        """
        try:
            if opened_at_iso is None:
                if signal_ref and 'signal_time' in signal_ref:
                    opened_at_iso = signal_ref['signal_time']
                else:
                    opened_at_iso = datetime.now(timezone.utc)
            if isinstance(opened_at_iso, datetime):
                opened_at_iso = opened_at_iso.isoformat()

            # ham pnl (son fiyat bazlı)
            if direction == 'LONG':
                pnl_pct = (exit_price - entry_price) / entry_price * 100
                risk_pct = (entry_price - (
                    signal_ref.get('stop_loss', entry_price) if signal_ref else entry_price)) / entry_price * 100
            else:
                pnl_pct = (entry_price - exit_price) / entry_price * 100
                risk_pct = ((signal_ref.get('stop_loss',
                    entry_price) if signal_ref else entry_price) - entry_price) / entry_price * 100

        except Exception as e:
            logging.error(f"[LOG_TRADE_OUTCOME_CALC_ERR] {symbol} {e}")
            pnl_pct = 0.0
            risk_pct = 0.0
        record_type = 'signal_partial' if exit_stage == 'partial' else 'signal_close'

        # YARDIMCI: Açılış meta'sını open satırından bul
        def _find_open_meta_safe(alarm_id, signal_id):
            try:
                path = cls._signal_log_file
                if not os.path.exists(path):
                    return {}
                with open(path, "r", encoding="utf-8") as f1:
                    for ln in f1:
                        ln = ln.strip()
                        if not ln:
                            continue
                        try:
                            row = json.loads(ln)
                        except Exception:
                            continue
                        if row.get('type') != 'signal_open':
                            continue
                        if signal_id and row.get('signal_id') == signal_id:
                            return row.get('meta') or {}
                        if alarm_id and row.get('alarm_id') == alarm_id:
                            return row.get('meta') or {}

                return {}
            except Exception:
                return {}

        # meta_at_open'ı garanti et
        open_meta = {}
        if signal_ref and isinstance(signal_ref.get('meta'), dict):
            open_meta = signal_ref['meta']
        if not open_meta:
            open_meta = _find_open_meta_safe(
                (signal_ref.get('alarm_id') if isinstance(signal_ref, dict) else None),
                (signal_ref.get('signal_id') if isinstance(signal_ref, dict) else None)
            )

        rec = {'type':record_type, 'timestamp':datetime.now(timezone.utc).isoformat(), 'symbol':symbol,
            'direction':direction, 'exit_type':exit_type, 'pnl_pct':pnl_pct, 'targets_hit_count':targets_hit_count,
            'opened_at':opened_at_iso}

        if signal_ref:
            rec['alarm_id'] = signal_ref.get('alarm_id')
            rec['signal_id'] = signal_ref.get('signal_id')
            rec['direction'] = 'LONG' if str(direction).upper() == 'LONG' else 'SHORT'

            # DÜZELTME: 'meta_at_open' alanını her zaman ekle. Bu, performans raporları için kritiktir.
            # Sinyal kapanırken, açılış anındaki meta verilerini bu anahtar altına kopyalıyoruz.
            rec['meta_at_open'] = open_meta or {}

        # ✅ leverage: DB'den (user_id + exchange)
        lev = 1.0
        try:
            uid = None
            if isinstance(signal_ref, dict):
                uid = signal_ref.get("user_id") or signal_ref.get("meta", {}).get("user_id")
            ex = "mexc"
            try:
                ex = str((signal_ref.get("meta") or {}).get("exchange") or "mexc").lower().strip()
            except Exception:
                ex = "mexc"

            if uid:
                u = get_user_settings(int(uid), ex)  # DB
                if isinstance(u, dict) and u.get("leverage") is not None:
                    lev = float(u.get("leverage") or 1.0)
        except Exception:
            lev = 1.0

        rec["leverage_used"] = float(max(1.0, lev))

        # ✅ PNL canonical alanları (append_closed_signal ile uyum)
        # log_trade_outcome kendi realized hesabını yapmaz; varsa state'ten taşır.
        try:
            if isinstance(signal_ref, dict):
                for k in (
                    "close_breakdown",
                    "realized_gross_pct",
                    "realized_effective_pct",
                    "realized_gross_lev",
                    "realized_effective_lev",
                    "exit_subtype",
                    "tp_scheme",
                    "execution_model",
                    "pnl_calc_version",
                ):
                    if signal_ref.get(k) is not None:
                        rec[k] = signal_ref.get(k)

                # ✅ Geriye uyumluluk: realized_net_pct alanı analytics'te hâlâ kullanılıyorsa
                # Öncelik: realized_effective_lev -> realized_effective_pct -> pnl_pct
                if rec.get("realized_effective_lev") is not None:
                    rec["realized_net_pct"] = float(rec["realized_effective_lev"])
                elif rec.get("realized_effective_pct") is not None:
                    rec["realized_net_pct"] = float(rec["realized_effective_pct"])
                else:
                    rec["realized_net_pct"] = float(pnl_pct)

                # Eğer leverage'lı alanlar yok ama raw varsa burada üret (opsiyonel)
                if rec.get("realized_effective_lev") is None and rec.get("realized_effective_pct") is not None:
                    rec["realized_effective_lev"] = float(rec["realized_effective_pct"]) * float(rec["leverage_used"])
                if rec.get("realized_gross_lev") is None and rec.get("realized_gross_pct") is not None:
                    rec["realized_gross_lev"] = float(rec["realized_gross_pct"]) * float(rec["leverage_used"])
        except Exception:
            pass

        try:
            with open(cls._signal_log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            logging.error(f"[LOG_TRADE_OUTCOME_WRITE_ERR] {e}")

        if exit_stage == 'final':
            try:
                cls.save_active_signals(force=True)
            except Exception as e:
                logging.error(f"Hata: {e}")
                pass

        # Yeni eklenen log_trade_outcome meta log kısmı
        try:
            out_row = {"ts":datetime.now(timezone.utc).isoformat(), "source":"signal_outcome", "symbol":symbol,
                "direction":direction, "exit_type":exit_type, "exit_stage":exit_stage,  # ek
                "opened_at":opened_at_iso,  # ek
                "pnl_pct":rec.get("pnl_pct") if isinstance(rec, dict) else None,
                "realized_net_pct":(
                                    rec.get("realized_effective_lev")
                                    if rec.get("realized_effective_lev") is not None else
                                    rec.get("realized_effective_pct")
                                    if rec.get("realized_effective_pct") is not None else
                                    rec.get("realized_net_pct")
                                    ),
                "targets_hit_count":int(targets_hit_count or 0),
                "alarm_id":(signal_ref.get("alarm_id") if isinstance(signal_ref, dict) else None),
                "signal_id":(signal_ref.get("signal_id") if isinstance(signal_ref, dict) else None),
                "timeframe":(signal_ref.get("timeframe") if isinstance(signal_ref, dict) else None),
                "strategy_id":(signal_ref.get("strategy_id") if isinstance(signal_ref, dict) else None)}

            # sayısal alan güvenliği
            for key in ("pnl_pct", "realized_net_pct"):
                if out_row.get(key) is not None:
                    try:
                        out_row[key] = float(out_row[key])
                        if math.isnan(out_row[key]) or math.isinf(out_row[key]):
                            out_row[key] = 0.0

                    except Exception as e:
                        logging.error(f"Hata: {e}")

                        out_row[key] = 0.0

            os.makedirs("analytics", exist_ok=True)
            with open(os.path.join("analytics", "alarms_meta_log.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(out_row, ensure_ascii=False) + "\n")
        except Exception as _omerr:
            logging.debug(f"[OUTCOME_META_LOG_WARN] {_omerr}")

        try:
            if rec.get("realized_effective_lev") is not None:
                return float(rec["realized_effective_lev"])
            if rec.get("realized_effective_pct") is not None:
                return float(rec["realized_effective_pct"])
        except Exception:
            pass
        return float(pnl_pct or 0.0)

    @classmethod
    def cleanup_old_signals(cls, max_age_hours=48):
        """Eski sinyalleri temizle"""
        if not hasattr(cls, 'active_signals'):
            cls.active_signals = []
            return

        current_time = datetime.now(timezone.utc)
        initial_count = len(cls.active_signals)

        safe_list = []
        for signal in cls.active_signals:
            try:
                st = signal.get('signal_time')
                if isinstance(st, str):
                    st = cls._ensure_aware(st)
                if not isinstance(st, datetime):
                    st = current_time  # güvenli fallback
                age_sec = (current_time - st).total_seconds()
                if age_sec < (max_age_hours * 3600):
                    safe_list.append(signal)
            except Exception as _age_err:
                # Şüpheli kayıtları güvenli tarafta tut (silme)
                logging.debug(f"[CLEANUP_AGE_WARN] {signal.get('signal_id')} {_age_err}")
                safe_list.append(signal)

        cls.active_signals = safe_list
        cleaned_count = initial_count - len(cls.active_signals)
        if cleaned_count > 0:
            logging.info(f"🧹 {cleaned_count} eski sinyal temizlendi")

    @classmethod
    def normalize_signal_dict(cls, sig: dict):
        for k, v in REQUIRED_SIGNAL_KEYS.items():
            if k not in sig:
                if k == 'signal_time':
                    sig[k] = datetime.now(timezone.utc).isoformat()
                elif k == 'targets_hit' and 'targets' in sig:
                    sig[k] = [False] * len(sig['targets'])
                else:
                    # list veya dict ise deepcopy yap
                    if isinstance(v, (list, dict)):
                        sig[k] = copy.deepcopy(v)
                    else:
                        sig[k] = v
        st = sig.get('signal_time')
        if isinstance(st, datetime):
            sig['signal_time'] = st.isoformat()
        elif st is None:
            sig['signal_time'] = datetime.now(timezone.utc).isoformat()
        if not isinstance(sig.get('targets'), list):
            sig['targets'] = []
        clean_targets = []
        for t in sig.get('targets', []):
            try:
                if isinstance(t, (dict, list, tuple, set)):
                    continue
                if isinstance(t, str):
                    s = t.strip().replace(',', '.')
                    fv = float(s)
                else:
                    fv = float(t)
                if math.isfinite(fv):
                    clean_targets.append(fv)
            except (TypeError, ValueError):
                continue
        sig['targets'] = clean_targets
        if not isinstance(sig.get('targets_hit'), list):
            sig['targets_hit'] = [False] * len(sig['targets'])
        if len(sig['targets_hit']) != len(sig['targets']):
            sig['targets_hit'] = [False] * len(sig['targets'])
        tht = sig.get('targets_hit_times')
        if not isinstance(tht, list) or len(tht) != len(sig['targets']):
            sig['targets_hit_times'] = [None] * len(sig['targets'])
        if 'stop_time' not in sig:
            sig['stop_time'] = None
        for key in ('entry_price', 'stop_loss'):
            try:
                sig[key] = float(sig.get(key) or 0.0)
            except (TypeError, ValueError):
                sig[key] = 0.0
            # peak/trough: entry_price'tan türet, sonra float'a zorla
            sig['peak_price'] = sig.get('entry_price', 0.0)
        if 'trough_price' not in sig or sig.get('trough_price') is None:
            sig['trough_price'] = sig.get('entry_price', 0.0)

        for key in ('peak_price', 'trough_price'):
            try:
                sig[key] = float(sig.get(key) or 0.0)
            except (TypeError, ValueError):
                sig[key] = 0.0
        # --- SYMBOL STANDARDIZATION (STATE) ---
        try:
            raw = str(sig.get("symbol") or "")
            core = cls.normalize_symbol(sig.get("core_symbol") or raw)
            if core:
                sig["core_symbol"] = core
                sig["display_symbol"] = cls.to_display_symbol(core)

                # ccxt_symbol yoksa üret (exchange varsa markets üzerinden doğru bulur)
                if not sig.get("ccxt_symbol"):
                    cc = cls.to_ccxt_symbol(core, prefer_futures=True) or cls.to_ccxt_symbol(raw, prefer_futures=True)
                    if cc:
                        sig["ccxt_symbol"] = cc

                # ✅ State’in ana 'symbol' alanını core’a sabitle
                sig["symbol"] = core
        except Exception:
            pass

        if not isinstance(sig.get('message_ids'), list):
            sig['message_ids'] = []

        if 'main_messages' not in sig or not isinstance(sig.get('main_messages'), list):
            sig['main_messages'] = []

        # Eski kayıtlar message_ids ile geldiyse, main_messages'a kopyala (opsiyonel ama çok iyi)
        if sig.get('message_ids') and not sig.get('main_messages'):
            sig['main_messages'] = [
                {'channel_id':m.get('chat_id'), 'message_id':m.get('message_id')}
                for m in sig.get('message_ids', []) if isinstance(m, dict)
            ]

    @classmethod
    def _init_special_symbol_mappings(cls):
        """
        DİNAMİK: Özel sembol mapping'i kaldırıldı
        Artık _parse_futures_symbol dinamik olarak çarpanları tespit eder
        """
        cls.SPECIAL_SYMBOL_MAP = {}
        logging.info("✅ Dinamik sembol mapping aktif (sabit mapping kaldırıldı)")


    # ==================================================================================
    # SİNYAL İŞLEME VE İLETME
    # ==================================================================================

    @classmethod
    async def forward_signal_to_trade_executor(cls, signal_data: dict, context: CallbackContext):
        """Sinyali alım-satım modülüne iletir."""
        user_id = signal_data.get('user_id')
        if not user_id:
            logging.error(f"Sinyal için user_id bulunamadı: {signal_data.get('signal_id')}")
            return

    @staticmethod
    def _get_tp_allocations_pct(signal: dict) -> list[float]:
        """
        TP allocation yüzdeleri. Toplamı 100 olmalı.
        Öncelik: signal.meta.tp_allocations_pct
        Fallback: ConfigService.get("risk.tp_allocations_pct") veya [20,20,20,20,20]
        """
        meta = signal.get("meta") or {}
        alloc = meta.get("tp_allocations_pct")

        if isinstance(alloc, list) and alloc:
            out = []
            for x in alloc[:5]:
                try:
                    out.append(float(x))
                except Exception:
                    pass
            if out and abs(sum(out) - 100.0) < 1e-6:
                return out

        # fallback config
        cfg = ConfigService.get("risk.tp_allocations_pct", None)
        if isinstance(cfg, list) and cfg:
            out = []
            for x in cfg[:5]:
                try:
                    out.append(float(x))
                except Exception:
                    pass
            if out and abs(sum(out) - 100.0) < 1e-6:
                return out

        return [20.0, 20.0, 20.0, 20.0, 20.0]

    @staticmethod
    def _calc_raw_pct(direction: str, entry: float, price: float) -> float:
        if entry <= 0:
            return 0.0
        direction = str(direction or "LONG").upper()
        if direction == "SHORT":
            return (entry - price) / entry * 100.0
        return (price - entry) / entry * 100.0

    @classmethod
    def calc_realized_pnl_lev(cls, signal: dict, leverage: float, current_price: float | None = None) -> dict:
        """
        Returns:
          {
            "realized_raw": ...,
            "realized_lev": ...,
            "remaining_pct": ...,
            "upnl_raw": ...,
            "upnl_lev": ...,
          }
        realized: vurulan hedeflerin allocation'ı kadar kısım
        upnl: realized + kalan kısım * current_price (aktifken)
        """
        entry = float(signal.get("entry_price") or 0.0)
        stop = float(signal.get("stop_loss") or 0.0)
        direction = str(signal.get("signal_type") or "LONG").upper()
        targets = (signal.get("targets") or [])[:5]
        hits = signal.get("targets_hit") or []
        alloc = cls._get_tp_allocations_pct(signal)[:len(targets)]

        realized_raw = 0.0
        realized_alloc = 0.0

        for i, t in enumerate(targets):
            if i < len(hits) and hits[i]:
                w = alloc[i] / 100.0
                realized_alloc += w
                realized_raw += w * cls._calc_raw_pct(direction, entry, float(t))

        remaining_pct = max(0.0, 1.0 - realized_alloc)

        # Aktif UPNL: realized + kalan * (current - entry)
        if current_price is None:
            # stop/final gibi eventlerde current_price verilmezse stop'u kullan (mantıklı fallback)
            current_price = stop if stop > 0 else entry

        upnl_raw = realized_raw + remaining_pct * cls._calc_raw_pct(direction, entry, float(current_price))
        return {
            "realized_raw": realized_raw,
            "realized_lev": realized_raw * float(leverage),
            "remaining_pct": remaining_pct * 100.0,
            "upnl_raw": upnl_raw,
            "upnl_lev": upnl_raw * float(leverage),
        }

    @staticmethod
    def _is_binance_positionSide_required_error(err: Exception) -> bool:
        s = str(err).lower()
        # pratikte gelen metinler:
        keys = [
            "positionside",
            "position side",
            "hedge mode",
            "positionSide is required".lower(),
            "-4061",
        ]
        return any(k in s for k in keys)

    @classmethod
    def _round_price_to_tick(cls, exchange, symbol: str, price: float) -> float:
        try:
            m = (exchange.markets or {}).get(symbol) or {}
            prec = (m.get("precision") or {}).get("price")
            if isinstance(prec, int):
                return float(f"{price:.{prec}f}")
            # precision yoksa dokunma
            return float(price)
        except Exception:
            return float(price)

    @classmethod
    async def _fetch_position_size_binance(cls, exchange, ccxt_symbol: str, side: str) -> float:
        """
        side: 'LONG'/'SHORT' (signal_type)
        Binance futures: fetch_positions ile positionAmt yakalanır.
        One-way: positionAmt işaretli gelir (+ long, - short)
        Hedge: ayrı satırlar gelebilir (positionSide LONG/SHORT).
        """
        positions = []
        try:
            positions = await exchange.fetch_positions([ccxt_symbol])
        except Exception:
            # bazı ccxt sürümlerinde fetch_positions([sym]) yerine fetch_positions() gerekebiliyor
            positions = await exchange.fetch_positions()

        side = str(side).upper().strip()
        want_ps = "LONG" if side == "LONG" else "SHORT"

        best = 0.0

        for p in (positions or []):
            try:
                sym = p.get("symbol")
                if sym != ccxt_symbol:
                    continue

                info = p.get("info") or {}
                # hedge ise info.positionSide gelebilir
                ps = str(info.get("positionSide") or "").upper().strip()

                amt = None
                # ccxt unified:
                if p.get("contracts") is not None:
                    amt = float(p.get("contracts") or 0.0)
                    # contracts her zaman pozitif olabilir; ps ile eşleştirmek daha iyi
                else:
                    amt = float(info.get("positionAmt") or 0.0)

                # One-way: positionAmt işaretlidir
                if ps:
                    if ps == want_ps:
                        best = max(best, abs(float(info.get("positionAmt") or amt or 0.0)))
                else:
                    # ps yoksa sign ile ayır
                    if side == "LONG" and float(info.get("positionAmt") or 0.0) > 0:
                        best = max(best, abs(float(info.get("positionAmt"))))
                    if side == "SHORT" and float(info.get("positionAmt") or 0.0) < 0:
                        best = max(best, abs(float(info.get("positionAmt"))))
            except Exception:
                continue

        return float(best or 0.0)

    @classmethod
    async def _sync_stop_to_exchange(cls, exchange, signal: dict, user_id: int, desired_stop: float) -> None:
        ex_id = str(getattr(exchange, "id", "") or "").lower().strip()
        if ex_id != "binance":
            # şimdilik sadece binance; diğer borsaları aynı pattern ile ekleriz
            raise RuntimeError(f"SL sync not implemented for exchange={ex_id}")

        ccxt_sym = (
                signal.get("ccxt_symbol")
                or cls.to_ccxt_symbol(signal.get("symbol"), prefer_futures=True)
                or cls.to_ccxt_symbol(signal.get("core_symbol"), prefer_futures=True)
        )
        if not ccxt_sym:
            raise RuntimeError("ccxt_symbol bulunamadı")

        side = str(signal.get("signal_type") or "LONG").upper().strip()
        if side not in ("LONG", "SHORT"):
            side = "LONG"

        # stop'u tick’e göre düzelt
        stop_price = cls._round_price_to_tick(exchange, ccxt_sym, float(desired_stop))
        if stop_price <= 0:
            raise ValueError("desired_stop invalid")

        # Pozisyon qty
        qty = await cls._fetch_position_size_binance(exchange, ccxt_sym, side)
        if qty <= 0:
            raise RuntimeError(f"position size not found (symbol={ccxt_sym})")

        # Mevcut SL emirlerini iptal et (reduceOnly STOP_* filtre)
        try:
            open_orders = await exchange.fetch_open_orders(ccxt_sym)
            for o in (open_orders or []):
                try:
                    t = str(o.get("type") or "").upper()
                    info = o.get("info") or {}
                    ro = bool(o.get("reduceOnly") or info.get("reduceOnly") or info.get("closePosition"))
                    # Binance’ta stop emirleri genelde STOP_MARKET / STOP / TAKE_PROFIT_* vs
                    if ro and ("STOP" in t or "TAKE_PROFIT" in t):
                        await exchange.cancel_order(o["id"], ccxt_sym)
                        await asyncio.sleep(0.05)
                except Exception:
                    continue
        except Exception:
            # iptal edemediysek bile create deneyebiliriz; ama ideal olan iptal
            pass

        # Stop emri: ters yön (LONG pozisyonu SL ile kapatmak için SELL)
        order_side = "sell" if side == "LONG" else "buy"

        base_params: Dict[str, Any] = {
            "reduceOnly":True,
        }

        async def _create(one_way: bool) -> None:
            params: Dict[str, Any] = dict(base_params)
            if not one_way:
                params["positionSide"] = "LONG" if side == "LONG" else "SHORT"

            # CCXT: create_order(symbol, type, side, amount, price=None, params={...})
            # Binance futures stop-market:
            await exchange.create_order(
                symbol=ccxt_sym,
                type="STOP_MARKET",
                side=order_side,
                amount=float(qty),
                price=None,
                params={**params, "stopPrice":float(stop_price)},
            )

        # 1) One-way dene
        try:
            await _create(one_way=True)
            logging.info(f"[SL_SYNC_OK] binance one-way symbol={ccxt_sym} stop={stop_price} qty={qty}")
            return
        except Exception as e1:
            # 2) Hedge gerekiyorsa hedge ile dene
            if cls._is_binance_positionSide_required_error(e1):
                try:
                    await _create(one_way=False)
                    logging.info(f"[SL_SYNC_OK] binance hedge symbol={ccxt_sym} stop={stop_price} qty={qty}")
                    return
                except Exception as e2:
                    raise RuntimeError(f"SL sync failed (one-way then hedge). e1={e1} e2={e2}") from e2

            # başka hata: direkt patlat
            raise

    @classmethod
    def apply_tp_trailing_stop(cls, signal: dict, user_id: int | None = None):
        # Güvenlik: sinyal dict değilse çık
        if not isinstance(signal, dict):
            return False, "", 0.0, 0.0

        old_sl = float(signal.get("stop_loss") or 0.0)
        entry = float(signal.get("entry_price") or 0.0)
        hits = signal.get("targets_hit") or []
        hit_cnt = sum(1 for h in hits if h)

        side = str(signal.get("signal_type") or "LONG").upper().strip()
        if side not in ("LONG", "SHORT"):
            side = "LONG"

        # 1) maliyet_cek kaynağı: önce signal.meta, yoksa user_settings
        m = 0
        meta = signal.get("meta")
        if not isinstance(meta, dict):
            meta = {}
            signal["meta"] = meta

        try:
            m = int(meta.get("maliyet_cek") or 0)
        except Exception:
            m = 0

        if m <= 0 and user_id:
            try:
                ex = str(meta.get("exchange") or "mexc").lower().strip()
                u = get_user_settings(user_id, ex)
                if u:
                    m = int(u.get("maliyet_cek") or 0)
                    meta["maliyet_cek"] = m  # cache
            except Exception:
                pass

        new_sl = old_sl
        rule = ""

        # 2) Kural: TP1 geldiyse BE (entry)
        if m >= 1 and hit_cnt >= 1 and entry > 0:
            new_sl = entry
            rule = "BE@TP1"

        # (İstersen buraya TP2/TP3 için ek kurallar da ekleriz)

        # 3) Sadece “iyileşme” varsa uygula
        should_update = False
        if new_sl > 0:
            if side == "LONG":
                should_update = (old_sl <= 0) or (new_sl > old_sl)
            else:  # SHORT
                should_update = (old_sl <= 0) or (new_sl < old_sl)

        if should_update and abs(new_sl - old_sl) > 1e-12:
            signal["stop_loss"] = float(new_sl)  # ✅ kritik: state yaz
            meta["sl_rule"] = rule
            meta["sl_moved_at"] = datetime.now(timezone.utc).isoformat()
            return True, rule, old_sl, float(new_sl)

        return False, "", float(old_sl), float(old_sl)

    @classmethod
    async def _handle_signal_outcome(cls, signal: dict, outcome_type: str):
        """
        Sinyal bittiğinde:
        1) İlgili sembolü 'active_symbols' (Alarm) listesinden siler.
        2) Performansı kaydeder ve health policy'yi çalıştırır (blacklist tetiklenebilir).
        """
        try:
            symbol = cls.normalize_symbol(signal.get('symbol'))
            strategy_id = signal.get('strategy_id', 'v1')

            # 1) Alarmı sil (aynı sembol/strateji tekrar sinyal üretmesin)
            original_len = len(cls.active_symbols)
            cls.active_symbols = [
                a for a in cls.active_symbols
                if not (
                        cls.normalize_symbol(a.get('symbol')) == symbol and
                        ((a.get('strategy_hint') == strategy_id) or (a.get('strategy_id') == strategy_id))
                )
            ]

            if len(cls.active_symbols) < original_len:
                logging.info(f"🗑️ {symbol} ({strategy_id}) sinyali sonuçlandığı için alarm listesinden silindi.")

            # 2) Performans/Health
            from strategies.alarm_system.analytics import SymbolPerformanceTracker
            tracker = SymbolPerformanceTracker()

            if outcome_type == 'LOSS':
                logging.warning(f"📉 {symbol} başarısız oldu (Stop). Performansa işleniyor...")
                tracker.record_loss(symbol)

                # ✅ KRİTİK: blacklist'i gerçekten tetikleyen yer burası
                health = tracker.evaluate_and_apply_health_policy(symbol)

                if health.get("blacklisted"):
                    logging.warning(
                        f"🚫 {symbol} GEÇİCİ KARA LİSTE: {health.get('reason')} | until={health.get('cooldown_until')}"
                    )

            else:
                logging.info(f"📈 {symbol} başarılı oldu (Kar). Performans sıfırlanıyor.")
                tracker.record_win(symbol)

                # İstersen win sonrası health'e bak (zorunlu değil)
                # health = tracker.evaluate_and_apply_health_policy(symbol)

        except Exception as e:
            logging.error(f"[_handle_signal_outcome] Hata: {e}", exc_info=True)

    @classmethod
    def normalize_alarm_dict(cls, a: dict, default_tf: str = "15m") -> dict:
        if not isinstance(a, dict):
            return {}

        a = dict(a)

        # timeframe garanti
        a["timeframe"] = str(a.get("timeframe") or default_tf).strip()

        # meta garanti
        meta = a.get("meta")
        if not isinstance(meta, dict):
            meta = {}
        a["meta"] = meta

        # source’ı garanti et: UI “Strateji Bazlı Tarama” ise source strategy_scan olmalı
        # (field isimleri sende farklı olabilir: "scan_type", "origin" vb.)
        src = meta.get("source") or a.get("source") or meta.get("origin")
        if not src and str(a.get("title") or "").lower().find("strateji") >= 0:
            src = "strategy_scan"
        if src:
            src = str(src).strip()
            meta["source"] = src
            a["source"] = src  # geri uyumluluk: bazı yerler alarm["source"] okuyor

        # strategy_id normalize
        sid = str(a.get("strategy_id") or a.get("strategy_hint") or "v1").lower().strip()
        a["strategy_id"] = "v1" if sid == "v1" else "v2"
        a["strategy_hint"] = a["strategy_id"]  # ikisini senkron tut
        # sembol normalize (senin fonksiyonun var)
        if a.get("core_symbol"):
            a["core_symbol"] = cls.normalize_symbol(a["core_symbol"])
        elif a.get("symbol"):
            a["core_symbol"] = cls.normalize_symbol(a["symbol"])
        # market_type garanti (monitor_symbols derivative filtresi için kritik)
        mt = str(a.get("market_type") or "swap").strip().lower()
        if mt in ("futures", "future"):
            mt = "future"
        elif mt in ("perp", "perpetual", "linear_swap", "inverse_swap", "swap"):
            mt = "swap"
        elif mt == "spot":
            mt = "spot"
        else:
            # bilinmiyorsa futures tarafında kalması için swap
            mt = "swap"
        a["market_type"] = mt

        return a

    @classmethod
    def _tf_bar_open_epoch(cls, tf: str, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        tf = str(tf or "15m").strip()
        tf_min = {"15m": 15, "1h": 60, "4h": 240}.get(tf)
        if not tf_min:
            # bilinmeyen tf -> gating yapma
            return 0
        step = tf_min * 60
        epoch = int(now.timestamp())
        return (epoch // step) * step

    @classmethod
    def _should_run_replenish_on_new_bar(cls, tf: str) -> bool:
        """
        Replenish sadece yeni bar'da 1 kez çalışsın.
        State dosyası (alarm_persistence.load_scheduler_state) ile kalıcı tutulur.
        """
        try:
            now = datetime.now(timezone.utc)
            bar_open = cls._tf_bar_open_epoch(tf, now=now)
            if not bar_open:
                return True  # tf bilinmiyorsa engelleme

            st = alarm_persistence.load_scheduler_state()  # sen persistence.py'ye ekledin
            last_map = (st.get("last_replenish_bar", {}) or {})
            last = int(last_map.get(tf, 0) or 0)

            if last == bar_open:
                return False

            last_map[tf] = bar_open
            st["last_replenish_bar"] = last_map
            alarm_persistence.save_scheduler_state(st)
            return True

        except Exception as e:
            logging.warning(f"[REPLENISH_GUARD_ERR] tf={tf} err={e}")
            return True

    @classmethod
    async def _manage_alarms_and_replenish(cls, context: CallbackContext, user_id: int):
        """
        Akıllı Alarm Yönetimi - PROAKTIF (REVİZE)
        - Kotalar scans.tf_profiles.<tf> üzerinden okunur.
        - Quota sayımı sadece active_symbols üzerinden yapılır (manual hariç).
        - status == "converted" quota’ya girmez.
        - Replenish yalnızca yeni bar'da bir kez tetiklenir.
        """
        try:
            scans_root = ConfigService.get("scans", {}) or {}

            # 1) primary_tf belirle
            primary_tf = "15m"
            tfs = scans_root.get("timeframes") or []
            if isinstance(tfs, list) and tfs:
                primary_tf = str(tfs[0] or "15m")
            if getattr(cls, "active_symbols", None):
                tf_counts: dict[str, int] = {}
                for a in (cls.active_symbols or []):
                    if not isinstance(a, dict):
                        continue
                    tf = str(a.get("timeframe") or "15m").strip()
                    tf_counts[tf] = tf_counts.get(tf, 0) + 1
                if tf_counts:
                    primary_tf = max(tf_counts, key=tf_counts.get)
            # ✅ BAR-GATED REPLENISH: aynı bar'da tekrar tarama yok
            # 2) TF profilini al (scans.tf_profiles.<tf>)
            tf_profile = ConfigService.tf_profile(primary_tf, {}) or {}
            global_max = int(tf_profile.get("max_active_alarms", 12) or 12)

            scans_root = ConfigService.get("scans", {}) or {}
            stale_minutes = int(scans_root.get("stale_alarm_minutes", 120) or 120)
            stale_td = timedelta(minutes=stale_minutes)
            now = datetime.now(timezone.utc)

            # --- RISK FRENİ (TF profilinden) + UNBLOCK HIZLI TOPARLAMA ---
            risk_cfg = (tf_profile.get("risk", {}) or {}) if isinstance(tf_profile, dict) else {}
            max_active = int(risk_cfg.get("max_active_signals", 0) or 0)

            active_cnt = sum(1 for s in (cls.active_signals or []) if isinstance(s, dict) and s.get("active"))
            is_blocked_now = (0 < max_active <= active_cnt)

            prev_blocked = bool((cls._risk_blocked_last_by_tf or {}).get(primary_tf, False))
            cls._risk_blocked_last_by_tf[primary_tf] = is_blocked_now

            # Risk halen blokluyorsa hiç devam etme (mevcut davranış)
            if is_blocked_now:
                logging.info(
                    f"[REPLENISH_BLOCKED_BY_RISK] tf={primary_tf} active_signals={active_cnt} "
                    f">= max_active_signals={max_active}"
                )
                return

            # Risk bloktan yeni çıktıysak: bar-guard bypass (tek sefer)
            force_replenish_once = (prev_blocked and not is_blocked_now)

            # ✅ BAR-GATED REPLENISH: normalde aynı bar'da tekrar tarama yok
            if not force_replenish_once:
                if not cls._should_run_replenish_on_new_bar(primary_tf):
                    logging.debug(f"[REPLENISH_GUARD] tf={primary_tf} aynı bar -> replenish atlandı")
                    return
            else:
                logging.info(f"[REPLENISH_UNBLOCK] tf={primary_tf} risk freni kalktı -> bar beklemeden 1 kez replenish")

            def _norm_source(x: str) -> str:
                s = str(x or "").strip()
                if s in ("ai_scan", "strategy_scan"):
                    return s
                return "manual"

            def _norm_strat(x: str) -> str:
                v = str(x or "v1").strip().lower()
                return "v1" if v == "v1" else "v2"

            # ---------------------------------------------------------
            # 3) Bayat alarm temizliği (manual koru)
            # ---------------------------------------------------------
            if cls.active_symbols:
                kept = []
                removed = 0

                for alarm in cls.active_symbols:
                    if not isinstance(alarm, dict):
                        continue

                    meta = alarm.get("meta") if isinstance(alarm.get("meta"), dict) else {}
                    source = _norm_source(meta.get("source", "manual"))

                    if source == "manual":
                        kept.append(alarm)
                        continue
                    # alarm_strateji.py -> _manage_alarms_and_replenish içinde
                    status = str(alarm.get("status") or "").lower()
                    if status == "converted":
                        kept.append(alarm)
                        continue

                    created_at_str = alarm.get("created_at")
                    if not created_at_str:
                        alarm["created_at"] = now.isoformat()
                        kept.append(alarm)
                        continue

                    try:
                        created_at = datetime.fromisoformat(str(created_at_str))
                        if created_at.tzinfo is None:
                            created_at = created_at.replace(tzinfo=timezone.utc)

                        if now - created_at > stale_td:
                            removed += 1
                            logging.info(f"[STALE_ALARM_REMOVED] {alarm.get('symbol')} source={source}")
                        else:
                            kept.append(alarm)
                    except Exception as e:
                        logging.warning(f"[STALE_ALARM_PARSE_WARN] {alarm.get('symbol')} err={e}")
                        kept.append(alarm)

                if removed:
                    cls.active_symbols = kept
                    # ✅ doğru state'i kaydet
                    try:
                        if hasattr(cls, "save_active_symbols"):
                            cls.save_active_symbols(force=True)
                        else:
                            # fallback: persistence katmanı varsa onu kullan
                            if hasattr(alarm_persistence, "save_active_symbols"):
                                alarm_persistence.save_active_symbols(cls.active_symbols)
                    except Exception as e:
                        logging.warning(f"[ACTIVE_SYMBOLS_SAVE_WARN] err={e}")

            # ---------------------------------------------------------
            # 4) Kotalar (SADECE tf_profile'dan)
            # ---------------------------------------------------------
            ai_cfg = tf_profile.get("ai_scan", {}) or {}
            strat_cfg = tf_profile.get("strategy_scan", {}) or {}

            ai_limits = ai_cfg.get("limits", {}) or {}
            ai_v1_limit = int(ai_limits.get("v1", 3) or 3)
            ai_v2_limit = int(ai_limits.get("v2", 3) or 3)

            strat_v1_limit = int(((strat_cfg.get("v1", {}) or {}).get("limit", 3)) or 3)
            strat_v2_limit = int(((strat_cfg.get("v2", {}) or {}).get("limit", 3)) or 3)

            # ---------------------------------------------------------
            # 5) Sayım: active_symbols + active_signals (manual hariç)
            # key=(source,strat,tf)
            # ---------------------------------------------------------
            group_counts: dict[tuple[str, str, str], int] = {}

            def _inc(src: str, sid: str, tframe: str):
                key = (src, sid, tframe)
                group_counts[key] = group_counts.get(key, 0) + 1

            # 5.1 active_symbols
            for alarm in (cls.active_symbols or []):
                if not isinstance(alarm, dict):
                    continue

                tf = str(alarm.get("timeframe") or primary_tf).strip()
                meta = alarm.get("meta") if isinstance(alarm.get("meta"), dict) else {}
                source = _norm_source(meta.get("source", "manual"))
                if source == "manual":
                    continue

                # ✅ converted alarm quota’ya girmez (alarm slotu boşalmış sayılır)
                status = str(alarm.get("status") or "").lower()
                if status == "converted":
                    continue

                strat = _norm_strat(alarm.get("strategy_id") or alarm.get("strategy_hint") or "v1")
                _inc(source, strat, tf)

            def _get_cnt(src: str, sid: str, tframe: str) -> int:
                return int(group_counts.get((src, sid, tframe), 0))

            # sadece primary_tf için toplam auto
            total_auto = sum(
                cnt for (src, _st, tf), cnt in group_counts.items()
                    if tf == primary_tf and src in ("ai_scan", "strategy_scan")
            )

            # ---------------------------------------------------------
            # 6) DEBUG TABLO LOG'u
            # ---------------------------------------------------------
            active_cnt_dbg = cls.get_active_signal_count()
            max_active_dbg = max_active  # ✅ aynı TF profilinden gelen değer

            table = (
                f"\n[QUOTA_TABLE] tf={primary_tf}  total_auto={total_auto}/{global_max}\n"
                f"  active_signals(active=True): {active_cnt_dbg}/{(max_active_dbg if max_active_dbg > 0 else '∞')}\n"
                f"  strategy_scan  v1: {_get_cnt('strategy_scan', 'v1', primary_tf)}/{strat_v1_limit}"
                f"   v2: {_get_cnt('strategy_scan', 'v2', primary_tf)}/{strat_v2_limit}\n"
                f"  ai_scan        v1: {_get_cnt('ai_scan', 'v1', primary_tf)}/{ai_v1_limit}"
                f"   v2: {_get_cnt('ai_scan', 'v2', primary_tf)}/{ai_v2_limit}\n"
            )
            logging.info(table)

            # ---------------------------------------------------------
            # 7) slot hesabı + todo listesi
            # ---------------------------------------------------------
            slots = global_max - total_auto
            if slots <= 0:
                logging.info("[QUOTA] dolu -> replenish yok (scan tetiklenmez)")
                return

            todo: list[tuple[str, str, str, int]] = []  # (source,strat,tf,need)

            def _want(src: str, sid: str, tframe: str, limit: int):
                nonlocal slots
                if slots <= 0:
                    return
                cur = _get_cnt(src, sid, tframe)
                need = int(limit) - int(cur)
                if need <= 0:
                    return
                # global_max'tan kalan slots kadar slots kadar kırp
                need = min(need, slots)
                todo.append((src, sid, tframe, need))
                slots -= need

            # Öncelik: strategy sonra ai
            _want("strategy_scan", "v1", primary_tf, strat_v1_limit)
            _want("strategy_scan", "v2", primary_tf, strat_v2_limit)
            _want("ai_scan", "v1", primary_tf, ai_v1_limit)
            _want("ai_scan", "v2", primary_tf, ai_v2_limit)

            if not todo:
                logging.info("[REPLENISH] eksik grup yok")
                return

            logging.info(f"[REPLENISH] todo={todo}")

            notify_chat_id = int(context.user_data.get("chat_id") or user_id or ADMIN_USER_ID)

            # ---------------------------------------------------------
            # 8) taramaları tetikle
            # ---------------------------------------------------------
            for source, strat, tf, need_limit in todo:
                try:
                    if source == "strategy_scan":
                        asyncio.create_task(cls._do_strategy_scan(
                            timeframe=tf,
                            strategy=strat,
                            limit=need_limit,
                            chat_id=notify_chat_id,
                            context=context,
                            user_id=user_id
                        ))
                    else:
                        asyncio.create_task(cls.do_ai_scan(
                            timeframe=tf,
                            strategy=strat,
                            limit=need_limit,
                            chat_id=notify_chat_id,
                            context=context,
                            user_id=user_id
                        ))

                    await asyncio.sleep(3)

                except Exception as e:
                    logging.error(f"[REPLENISH_TASK_ERR] {source} {strat} {tf} err={e}", exc_info=True)

        except Exception as e:
            logging.error(f"[_manage_alarms_and_replenish] Hata: {e}", exc_info=True)

    @staticmethod
    def _ensure_float(x, default=None):
        # dict ise ortak anahtarlara bak
        if isinstance(x, dict):
            for k in ('price', 'value', 'val', 'v'):
                if k in x:
                    try:
                        return float(x[k])

                    except Exception as e:
                        logging.error(f"Hata: {e}")

                    break
            return default if default is not None else float('nan')

        # liste/tuple ise ilk ögeyi al
        if isinstance(x, (list, tuple)) and x:
            try:
                return float(x[0])

            except Exception as e:
                logging.error(f"Hata: {e}")

            return default if default is not None else float('nan')

        try:
            return float(x)

        except Exception as e:
            logging.error(f"Hata: {e}")

        return default if default is not None else float('nan')

    @classmethod
    async def run_smart_scan_and_cleanup(cls, context: CallbackContext):
        """
        Akıllı alarm temizleme ve periyodik tarama döngüsünü yönetir.
        DÜZELTME: context.effective_user hatası giderildi.
        """
        logging.info("🤖 Akıllı Tarama ve Temizleme Döngüsü Başladı.")

        current_alarms = list(cls.active_symbols)
        if not current_alarms:
            logging.info("Aktif alarm yok, tarama atlanıyor.")
            return

        # DÜZELTME: User ID'yi güvenli şekilde al (context.effective_user yerine)
        from config.constants import ADMIN_USER_ID

        user_id = int(context.user_data.get("user_id") or ADMIN_USER_ID)
        chat_id = int(context.user_data.get("chat_id") or user_id)

        # Alarmları grupla: (source, strategy_id, timeframe)
        scan_groups = {}
        for alarm in current_alarms:
            meta = alarm.get('meta', {})
            source = meta.get('source', 'unknown')
            strategy_id = alarm.get('strategy_id', 'v1')
            timeframe = alarm.get('timeframe', '15m')

            if source not in ['ai_scan', 'strategy_scan']:
                continue

            key = (source, strategy_id, timeframe)
            if key not in scan_groups:
                scan_groups[key] = {'old_symbols':set()}

            scan_groups[key]['old_symbols'].add(cls.normalize_symbol(alarm['symbol']))

        all_newly_found_symbols = set()

        # Her grup için taramaları yeniden çalıştır
        for (source, strategy_id, timeframe), group_data in scan_groups.items():
            logging.info(f"🔄 Tarama grubu işleniyor: {source}, {strategy_id}, {timeframe}")

            new_candidates = []
            try:
                if source == 'ai_scan':
                    result = await cls.do_ai_scan(
                        timeframe=timeframe,
                        strategy=strategy_id,
                        limit=200,
                        chat_id=chat_id,
                        context=context,
                        user_id=user_id
                    )
                    if isinstance(result, dict):
                        new_candidates = result.get('created_symbols', []) or []

                elif source == 'strategy_scan':
                    result = await cls._do_strategy_scan(
                        timeframe=timeframe,
                        strategy=strategy_id,
                        limit=200,
                        chat_id=chat_id,
                        context=context,
                        user_id=user_id
                    )

                    found_list = result.get('found_symbols', []) or []
                    new_candidates = []
                    for item in found_list:
                        sym = item.get('symbol') if isinstance(item, dict) else str(item)
                        if sym: new_candidates.append(sym)

            except Exception as e:
                logging.error(f"Tarama sırasında hata ({source}, {strategy_id}, {timeframe}): {e}", exc_info=True)
                continue

            # Yeni bulunanları normalize et
            new_symbols_set = {cls.normalize_symbol(s) for s in new_candidates}
            all_newly_found_symbols.update(new_symbols_set)

            old_symbols_set = group_data['old_symbols']
            symbols_to_remove = old_symbols_set - new_symbols_set

            if symbols_to_remove:
                logging.info(f"🧹 Temizlenecek {len(symbols_to_remove)} alarm bulundu.")
                cls.active_symbols = [
                    alarm for alarm in cls.active_symbols
                    if not (
                            alarm.get('meta', {}).get('source') == source and
                            alarm.get('strategy_id') == strategy_id and
                            alarm.get('timeframe') == timeframe and
                            cls.normalize_symbol(alarm['symbol']) in symbols_to_remove and
                            alarm.get('status') != 'converted'
                    )
                ]

        cls.deduplicate_active_symbols(verbose=True)
        cls.save_active_signals(force=True)
        logging.info("🤖 Akıllı Tarama ve Temizleme Döngüsü Tamamlandı.")

    @classmethod
    async def stop_strategy(cls, update: Update, context: CallbackContext):
        _ = context
        """Ana AI strateji döngüsünü durdurur."""
        query = update.callback_query
        if cls.is_running:
            cls.is_running = False
            logging.info("🔴 Strateji durdurma talebi alındı. Döngü bir sonraki turda sonlanacak.")

            # ✅ EKLE
            try:
                await cls.close_exchange_safe(reason="stop_strategy")
            except Exception:
                pass

            if query:
                await query.edit_message_text(
                    "🔴 Strateji durduruluyor... Lütfen bekleyin.",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("🔙 Geri", callback_data='back_to_alarm_menu')]])
                )
        else:
            logging.warning("⚠️ Strateji zaten durdurulmuş durumda.")
            if query:
                await query.edit_message_text(
                    "⚠️ Strateji zaten çalışmıyor.",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("🔙 Geri", callback_data='back_to_alarm_menu')]])
                )
        return State.ALARM_SETUP

    @classmethod
    async def run_ai_strategy(cls, context):
        """
        AI destekli strateji çalıştırma - Optimized başlangıç
        DÜZELTME: 'mode' değişkeni için güvenli bir varsayılan değer atandı ve
                  try/except blokları daha mantıklı bir şekilde yeniden yapılandırıldı.
        """
        from StrategyAdaptiveTuner import StrategyAdaptiveTuner

        try:
            cls.is_running = True
            cls.run_ai_strategy_active = True
            await cls.start_trailing_loops_once()

            logging.info("🚀 AI destekli strateji başlatıldı")

            if cls._last_daily_version is None:
                cls._last_daily_version = datetime.now(timezone.utc).date()

            await cls._check_channel_membership(context)
            await cls.validate_channel_ids(context)

            if not cls._ai_model:
                cls._ai_model = RealAIModel()
            if not cls._ai_model.is_trained:
                if not cls._ai_model.load_models():
                    from config.constants import ADMIN_USER_ID
                    logging.info("🎓 İlk kez eğitim gerekli")
                    exchange_name = context.user_data.get("exchange", "mexc")
                    uid = context.user_data.get("user_id", ADMIN_USER_ID)
                    await cls.train_ai_model_dynamic(exchange=exchange_name, triggered_by_user_id=uid)
                else:
                    logging.info("✅ Mevcut modeller yüklendi")
                    cls._ai_model.is_trained = True

            scan_counter = 0

            while cls.is_running:
                try:
                    # DÜZELTME: Mod ve ayar dosyasını her döngünün başında kontrol et.
                    # Bu, Telegram'dan yapılan değişikliklerin anında yansımasını sağlar.
                    if ConfigService.hot_reload_if_changed():
                        logging.info("🟡 Config hot-reload uygulandı")
                        cls.load_runtime_strategy()

                    # Her döngüde en güncel modu al.
                    mode = ConfigService.control_mode()

                    # --- OTOMATİK AYARLAMA DÖNGÜSÜ ---
                    # Her 3 dakikada bir (18 * 10s) çalışır
                    if scan_counter > 0 and scan_counter % 18 == 0:
                        logging.info(f"🤖 Periyodik adaptif ayarlama kontrolü (Mod: {mode.upper()})...")
                        try:
                            changes, report_message, retrain_request = StrategyAdaptiveTuner.analyze_and_tune()

                            # Eğer 'auto' moddaysak ve değişiklik varsa, uygula.
                            if mode == 'auto' and changes:
                                logging.info(f"📈 Strateji Ayarlayıcı Değişiklikleri OTOMATİK olarak uyguluyor.")
                                # Değişiklikler zaten analyze_and_tune içinde ConfigService'e yazıldı ve kaydedildi.
                                # Sadece runtime stratejisini yeniden yüklememiz yeterli.
                                cls.load_runtime_strategy()

                            # Admin'e rapor gönder (sadece değişiklik olunca)
                            if changes:
                                from config.constants import ADMIN_USER_ID
                                await safe_send_message(context.bot, chat_id=ADMIN_USER_ID, text=report_message,
                                    parse_mode=None)

                        except Exception as tune_err:
                            logging.error(f"❌ Otomatik ayarlama döngüsünde hata: {tune_err}", exc_info=True)
                        # --- OTOMATİK AYARLAMA DÖNGÜSÜ SONU ---
                    price_map = await cls.safe_fetch_tickers()

                    # NOTE: legacy price feed; unified trailing CCXT'ten fiyat çekiyor. Şimdilik korunuyor.
                    from settings.trailing_price_feed import TRAILING_PRICE_FEED
                    TRAILING_PRICE_FEED.update(price_map or {})
                    from config.constants import ADMIN_USER_ID

                    uid = context.user_data.get('user_id') or ADMIN_USER_ID
                    await cls.monitor_active_signals(context, price_map=price_map, user_id=uid)

                    if scan_counter % 6 == 0:
                        await cls.monitor_symbols(context, price_map=price_map)

                except Exception as loop_error:
                    logging.error(f"❌ Strateji döngü hatası: {loop_error}", exc_info=True)
                    await asyncio.sleep(30)

                finally:
                    scan_counter += 1
                    await asyncio.sleep(10)

        except Exception as e:
            logging.error(f"❌ AI strateji çalıştırma hatası: {str(e)}", exc_info=True)
        finally:
            cls.is_running = False
            cls.run_ai_strategy_active = False
            logging.info("🛑 AI strateji durduruldu")
            # ✅ Loop bitti -> exchange'i kapat (leak fix)
            try:
                await cls.close_exchange_safe(reason="run_ai_strategy.finally")
            except Exception:
                pass

    @classmethod
    async def _check_channel_membership(cls, context):
        """
        Kanal üyeliğini kontrol et ve geçersiz kanalları listeden çıkar.
        """
        try:
            valid_channels = []
            for channel_id in cls.channel_ids:
                try:
                    chat_member = await cls._retry_async(context.bot.get_chat_member, chat_id=channel_id,
                        user_id=context.bot.id, retries=2, base_delay=0.7, max_delay=cls.MAX_BACKOFF_SEC)

                    if chat_member.status in ['administrator', 'member', 'creator']:
                        valid_channels.append(channel_id)
                        logging.info(f"✅ {channel_id} kanalı geçerli")
                    else:
                        logging.warning(f"⚠️ Bot {channel_id} kanalında yeterli yetkiye sahip değil")

                except Exception as membership_error:
                    if "Chat not found" in str(membership_error):
                        logging.error(f"❌ {channel_id} Kanalına Bot üye değil")
                    else:
                        logging.error(f"❌ {channel_id} kanalı üyelik kontrolü hatası: {membership_error}")

            cls.channel_ids = valid_channels
            return valid_channels

        except Exception as e:
            logging.error(f"❌ Kanal üyeliği kontrolünde genel hata: {str(e)}")
            return []

    @classmethod
    async def resolve_target_channels(cls, context: Optional[ContextTypes.DEFAULT_TYPE], user_id: Optional[int]) -> List[int]:
        """
        Öncelik:
          1) DB: user notification channels
          2) cls.channel_ids fallback
        Sonra: validate + bot membership check.
        """
        chans: List[int] = []

        # 1) DB
        if user_id:
            try:
                rows = await asyncio.to_thread(get_user_notification_channel_ids, int(user_id))
                if isinstance(rows, list):
                    tmp = []
                    for c in rows:
                        try:
                            tmp.append(int(c))
                        except Exception:
                            pass
                    chans = tmp
            except Exception:
                chans = []

        # 2) fallback
        if not chans:
            chans = list(getattr(cls, "channel_ids", []) or [])

        # normalize: sadece negatif int (channel/supergroup)
        chans = [c for c in chans if isinstance(c, int) and c < 0]
        chans = sorted(set(chans))

        # membership check (context varsa)
        if context and chans:
            ok: List[int] = []
            for cid in chans:
                try:
                    cm = await cls._retry_async(context.bot.get_chat_member, chat_id=cid, user_id=context.bot.id, retries=2)
                    if cm.status in ["administrator", "member", "creator"]:
                        ok.append(cid)
                except Exception:
                    continue
            chans = ok

        return chans

    @classmethod
    async def validate_channel_ids(cls, context: Optional[ContextTypes.DEFAULT_TYPE] = None) -> List[int]:
        try:
            valid_channels = []
            channel_list = getattr(cls, "channel_ids", []) or []

            for channel_id in channel_list:
                if isinstance(channel_id, int) and channel_id < 0:
                    if context:
                        try:
                            chat_member = await cls._retry_async(
                                context.bot.get_chat_member,
                                chat_id=channel_id,
                                user_id=context.bot.id
                            )
                            if chat_member.status not in ['administrator', 'member', 'creator']:
                                logging.warning(f"⚠️ Bot {channel_id} kanalına üye değil veya yetkisi yok. Atlanıyor.")
                                continue

                        except Exception as channel_check_err:
                            logging.error(f"Kanal kontrol hatası {channel_id}: {channel_check_err}")
                            continue
                    # context yoksa sadece formatına bakıp kabul et
                    valid_channels.append(channel_id)
                else:
                    logging.warning(f"⚠️ Geçersiz kanal ID'si: {channel_id}")

            # context yoksa ve valid_channels boşsa, mevcut channel_ids’i koru
            if not context and not valid_channels and channel_list:
                valid_channels = channel_list

            cls.channel_ids = valid_channels

            if valid_channels:
                logging.info(f"✅ Geçerli kanallar: {valid_channels}")
            else:
                # ADMINE UYARI DMinin context None ise başarısız olabileceğini hesaba kat
                if context:
                    try:
                        from config.constants import ADMIN_USER_ID
                        await safe_send_message(context.bot, chat_id=ADMIN_USER_ID,
                            text="⚠️ Bot hiçbir kanala üye değil veya yetkisi yok. Lütfen kanal ID’lerini ve bot yetkisini kontrol edin.")
                    except Exception:
                        pass
                logging.warning("⚠️ Geçerli kanal bulunamadı")

            return valid_channels

        except Exception as e:
            logging.error(f"❌ Kanal ID'leri kontrol hatası: {e}")
            return getattr(cls, "channel_ids", []) or []

    @classmethod
    def initialize_alarm_system(cls):
        """Alarm sistemini başlat"""
        try:
            if not cls.alarm_rapor_manager:
                cls.alarm_rapor_manager = AlarmRaporManager()
                logging.info("✅ Alarm rapor sistemi başlatıldı")
        except Exception as e:
            logging.error(f"❌ Alarm sistemi başlatma hatası: {e}")

    @classmethod
    async def get_api_credentials_safe(cls, user_id: int, exchange_name: str) -> Optional[dict]:
        """
        Veritabanından API anahtarlarını güvenli şekilde alır.
        """
        try:
            from data.olimpos_data import get_api_key

            api_data = get_api_key(user_id, exchange_name)

            if not api_data:
                logging.error(f"❌ {exchange_name} için API bilgisi bulunamadı (user: {user_id})")
                return None

            api_key = api_data.get('api_key', '').strip()
            secret_key = api_data.get('secret_key', '').strip()
            passphrase = api_data.get('passphrase', '').strip() if api_data.get('passphrase') else None

            # Boş kontrol
            if not api_key or not secret_key:
                logging.error(f"❌ {exchange_name} için API anahtarları eksik veya boş")
                return None

            # Uzunluk kontrol
            if len(api_key) < 10 or len(secret_key) < 10:
                logging.error(f"❌ {exchange_name} için API anahtarları çok kısa (muhtemelen geçersiz)")
                return None

            return {
                'api_key':api_key,
                'secret_key':secret_key,
                'passphrase':passphrase
            }

        except Exception as e:
            logging.error(f"❌ API bilgileri alınırken hata: {e}", exc_info=True)
            return None

    @classmethod
    async def initialize_exchange(
            cls,
            user_id: int,
            exchange_name: str,
            api_key: str,
            secret_key: str,
            passphrase: Optional[str],
            context,
    ) -> bool:
        """
        Exchange'i güvenli ve hızlı başlatır.

        Düzeltmeler:
        - MEXC config bloğu yanlış indent/config override bug fix
        - Eski exchange kapatma: await close + cls.exchange=None
        - MEXC: defaultType swap, apiKey/secret market yüklemeden sonra set edilebilir
        """
        try:
            import time
            # 1) API Anahtarlarını Doğrula (bazı borsalar public için istemese de sizde uniform kural var)
            if not api_key or not secret_key:
                logging.error(f"❌ {exchange_name} için API anahtarları eksik!")
                return False

            api_key = str(api_key).strip()
            secret_key = str(secret_key).strip()

            if len(api_key) < 10 or len(secret_key) < 10:
                logging.error(f"❌ {exchange_name} için API anahtarları çok kısa (geçersiz olabilir)")
                return False

            exchange_lower = (exchange_name or "").lower().strip()
            if not exchange_lower:
                exchange_lower = "mexc"

            # 2) Mevcut bağlantı kontrolü
            if getattr(cls, "exchange", None):
                try:
                    if cls.exchange.id == exchange_lower and getattr(cls.exchange, "markets", None):
                        keys_are_same = (
                                hasattr(cls.exchange, 'apiKey') and cls.exchange.apiKey == api_key and
                                hasattr(cls.exchange, 'secret') and cls.exchange.secret == secret_key
                        )
                        if keys_are_same:
                            return True

                        # anahtar güncelle
                        try:
                            cls.exchange.apiKey = api_key
                            cls.exchange.secret = secret_key
                            if passphrase:
                                cls.exchange.password = passphrase
                            logging.info(f"✅ {exchange_name} API anahtarları güncellendi (yeni anahtar algılandı).")
                            return True
                        except Exception as update_err:
                            logging.warning(f"⚠️ API anahtar güncelleme hatası: {update_err}")
                except Exception:
                    pass
                # farklı exchange veya sorun -> kapat
                try:
                    logging.info(f"✅ Farklı bir borsa seçildi, eski {cls.exchange.id} bağlantısı kapatılıyor.")
                    await asyncio.wait_for(cls.exchange.close(), timeout=8)
                except Exception as close_error:
                    logging.warning(f"⚠️ Exchange kapatma uyarısı: {close_error}")
                finally:
                    cls.exchange = None

            # 3) Timestamp sync (sadece bazı borsalar)
            server_time_offset = 0

            if exchange_lower == 'binance':
                try:
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                        async with session.get('https://api.binance.com/api/v3/time') as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                server_time = int(data.get('serverTime', 0))
                                local_time = int(time.time() * 1000)
                                server_time_offset = server_time - local_time
                                logging.info(f"✅ Binance server time offset: {server_time_offset}ms")
                except Exception as time_error:
                    logging.warning(f"⚠️ Binance server time alma hatası: {time_error}")

            elif exchange_lower == 'okx':
                try:
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                        async with session.get('https://www.okx.com/api/v5/public/time') as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                server_time = int(data['data'][0]['ts'])
                                local_time = int(time.time() * 1000)
                                server_time_offset = server_time - local_time
                                logging.info(f"✅ OKX server time offset: {server_time_offset}ms")
                except Exception as time_error:
                    logging.warning(f"⚠️ OKX server time alma hatası: {time_error}")

            elif exchange_lower == 'bybit':
                try:
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                        async with session.get('https://api.bybit.com/v5/market/time') as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                server_time = int(((data.get('result') or {}).get('timeSecond') or 0)) * 1000
                                local_time = int(time.time() * 1000)
                                server_time_offset = server_time - local_time
                                logging.info(f"✅ Bybit server time offset: {server_time_offset}ms")
                except Exception as time_error:
                    logging.warning(f"⚠️ Bybit server time alma hatası: {time_error}")

            # 4) Base config
            config: Dict[str, Any] = {
                'apiKey':api_key,
                'secret':secret_key,
                'enableRateLimit':True,
                'timeout':30000,
                'rateLimit':200,
                'options':{
                    'adjustForTimeDifference':True,
                    'recvWindow':10000,
                    'createMarketBuyOrderRequiresPrice':False,
                }
            }

            # 5) Exchange özel ayarlar
            if exchange_lower == 'binance':
                config['defaultType'] = 'future'
                config['timeout'] = 20000
                config['rateLimit'] = 100
                if server_time_offset:
                    config['options']['timeDifference'] = server_time_offset
                logging.info("✅ Binance özel ayarları uygulandı")

            elif exchange_lower == 'mexc':
                # türev/swap
                config['options']['defaultType'] = 'swap'
                config.pop('defaultType', None)

                config['options']['adjustForTimeDifference'] = True
                config['options']['sandboxMode'] = False
                config['options'].pop('timeDifference', None)

                config['timeout'] = 30000
                config['rateLimit'] = 200

                # apiKey/secret'i load_markets sonrası set etmek istiyorsun -> burada çıkar
                config.pop('apiKey', None)
                config.pop('secret', None)

                logging.info("✅ MEXC özel ayarları uygulandı (defaultType=swap, API anahtarları sonra eklenecek)")

            elif exchange_lower == 'okx':
                config['defaultType'] = 'spot'
                config['broker'] = 'CCXT'
                if passphrase:
                    config['password'] = passphrase
                config['timeout'] = 20000
                config['rateLimit'] = 100
                if server_time_offset:
                    config['options']['timeDifference'] = server_time_offset
                logging.info("✅ OKX özel ayarları uygulandı")

            elif exchange_lower == 'bybit':
                config['defaultType'] = 'linear'
                if passphrase:
                    config['password'] = passphrase
                config['timeout'] = 20000
                config['rateLimit'] = 100
                if server_time_offset:
                    config['options']['timeDifference'] = server_time_offset
                logging.info("✅ Bybit özel ayarları uygulandı")

            elif exchange_lower == 'bitget':
                config['defaultType'] = 'spot'
                if passphrase:
                    config['password'] = passphrase
                config['timeout'] = 20000
                config['rateLimit'] = 100
                logging.info("✅ Bitget özel ayarları uygulandı")

            elif exchange_lower == 'coinex':
                config['defaultType'] = 'spot'
                config['timeout'] = 20000
                config['rateLimit'] = 150
                logging.info("✅ CoinEx özel ayarları uygulandı")

            elif exchange_lower == 'bingx':
                config['defaultType'] = 'spot'
                config['timeout'] = 20000
                config['rateLimit'] = 150
                logging.info("✅ BingX özel ayarları uygulandı")

            elif exchange_lower == 'weex':
                config['defaultType'] = 'spot'
                config['timeout'] = 25000
                config['rateLimit'] = 200
                logging.info("✅ WEEX özel ayarları uygulandı")

            else:
                config['defaultType'] = 'spot'
                if passphrase:
                    config['password'] = passphrase

            # 6) Exchange nesnesini oluştur
            try:
                exchange_class = getattr(ccxt, exchange_lower)
                cls.exchange = exchange_class(config)
                logging.info(f"✅ {exchange_name} exchange nesnesi oluşturuldu")
            except AttributeError:
                logging.error(f"❌ Exchange sınıfı bulunamadı: {exchange_name}")
                return False

            # 7) Binance timestamp sync
            if exchange_lower == 'binance':
                try:
                    await asyncio.wait_for(cls.exchange.load_time_difference(), timeout=5)
                    logging.info("✅ Binance timestamp senkronizasyonu tamamlandı")
                except Exception as sync_error:
                    logging.warning(f"⚠️ Timestamp sync hatası: {sync_error}")

            # 8) Market yükle
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    logging.info(f"⚡ Market bilgileri yükleniyor... Deneme: {attempt + 1}")

                    if exchange_lower != 'mexc':
                        try:
                            await asyncio.wait_for(cls.exchange.fetch_status(), timeout=8)
                        except Exception as status_err:
                            logging.warning(f"⚠️ Status check hatası: {status_err}")

                    await asyncio.wait_for(cls.exchange.load_markets(), timeout=25)

                    if cls.exchange.markets and len(cls.exchange.markets) > 0:
                        logging.info(
                            f"✅ {exchange_name} markets başarıyla yüklendi: {len(cls.exchange.markets)} market")
                        break

                    logging.warning(f"⚠️ Market listesi boş (Deneme {attempt + 1})")

                except ccxt.InvalidNonce as nonce_error:
                    logging.error(f"❌ Timestamp hatası (Deneme {attempt + 1}): {nonce_error}")
                    if attempt < max_retries - 1 and exchange_lower == 'binance':
                        try:
                            await cls.exchange.load_time_difference()
                            logging.info("🔄 Timestamp yeniden senkronize edildi")
                        except Exception:
                            pass
                    await asyncio.sleep(2)

                except asyncio.TimeoutError:
                    logging.warning(f"⚠️ Market yükleme timeout (Deneme {attempt + 1})")
                    await asyncio.sleep(3)

                except Exception as market_error:
                    logging.error(f"❌ Market yükleme hatası (Deneme {attempt + 1}): {market_error}")
                    await asyncio.sleep(3)

            # 9) MEXC için API key/secret'i şimdi ekle
            if exchange_lower == 'mexc':
                try:
                    cls.exchange.apiKey = api_key
                    cls.exchange.secret = secret_key
                    if passphrase:
                        cls.exchange.password = passphrase
                    logging.info("✅ MEXC API anahtarları market yüklemeden sonra eklendi")
                except Exception as mexc_key_err:
                    logging.error(f"❌ MEXC API anahtarı ekleme hatası: {mexc_key_err}")

            # 10) API test (MEXC dışı hemen, MEXC geç test)
            if exchange_lower != 'mexc':
                try:
                    logging.info("🔑 API anahtarları test ediliyor...")
                    await asyncio.wait_for(cls.exchange.fetch_balance(), timeout=15)
                    logging.info("✅ API anahtarları geçerli")
                except ccxt.AuthenticationError as auth_err:
                    logging.error(f"❌ API Kimlik Doğrulama Hatası: {auth_err}")
                    return False
                except Exception as test_err:
                    logging.warning(f"⚠️ API test uyarısı (devam ediliyor): {test_err}")

            # 11) Ticker test (safe_fetch_tickers ile)
            logging.info("🧪 Ticker test başlatılıyor...")
            ticker_test_success = False

            for test_attempt in range(2):
                try:
                    test_tickers = await cls.safe_fetch_tickers(retries=2, delay=1.0)

                    if not isinstance(test_tickers, dict) or not test_tickers:
                        logging.warning(f"⚠️ fetch_tickers boş/geçersiz (deneme {test_attempt + 1})")
                        await asyncio.sleep(2)
                        continue

                    logging.info(f"✅ Ticker test başarılı: {len(test_tickers)} sembol")
                    cls._ticker_cache = (datetime.now(timezone.utc), test_tickers)
                    cls._last_tickers = test_tickers
                    ticker_test_success = True
                    break

                except Exception as e:
                    logging.warning(f"⚠️ Ticker test hatası (deneme {test_attempt + 1}): {e}")
                    await asyncio.sleep(2)

            if not ticker_test_success:
                logging.warning("⚠️ Ticker test başarısız oldu ama exchange başlatıldı")

            # 12) MEXC geç API testi
            if exchange_lower == 'mexc':
                try:
                    logging.info("🔑 MEXC API anahtarları test ediliyor...")
                    await asyncio.wait_for(cls.exchange.fetch_balance(), timeout=15)
                    logging.info("✅ MEXC API anahtarları geçerli")
                except ccxt.AuthenticationError as auth_err:
                    logging.error(f"❌ MEXC API Kimlik Doğrulama Hatası: {auth_err}")
                    return False
                except Exception as test_err:
                    logging.warning(f"⚠️ MEXC API test uyarısı (devam ediliyor): {test_err}")

            # 13) context'e yaz
            try:
                context.user_data['api_key'] = api_key
                context.user_data['secret_key'] = secret_key
                context.user_data['passphrase'] = passphrase
                context.user_data['exchange'] = exchange_name
                context.user_data['exchange_initialized'] = True
                context.user_data['user_id'] = user_id
            except Exception:
                pass

            logging.info(f"✅ {exchange_name} başarıyla başlatıldı")
            logging.info(f"📊 Market count: {len(cls.exchange.markets) if cls.exchange.markets else 0}")
            try:
                logging.info(f"🎯 Default type: {cls.exchange.options.get('defaultType', 'N/A')}")
            except Exception:
                pass

            return True

        except Exception as unexpected_error:
            logging.error(f"❌ Beklenmeyen hata: {unexpected_error}", exc_info=True)
            return False

    @classmethod
    async def close_exchange_safe(cls, reason: str = "") -> None:
        """
        CCXT async exchange + aiohttp session leak'i önlemek için tek kapatma noktası.
        """
        ex = getattr(cls, "exchange", None)
        if ex is None:
            return

        ex_id = str(getattr(ex, "id", "") or getattr(ex, "name", "") or "?")
        try:
            logging.info(f"[EX_CLOSE] reason={reason} ex={ex_id}")
        except Exception:
            pass

        try:
            # bazı ccxt sürümlerinde session alanı bulunabiliyor
            sess = getattr(ex, "session", None)
            if sess is not None:
                try:
                    if not getattr(sess, "closed", True):
                        await asyncio.wait_for(sess.close(), timeout=3)
                except Exception:
                    pass

            await asyncio.wait_for(ex.close(), timeout=6)

        except Exception as e:
            logging.warning(f"[EX_CLOSE_WARN] ex={ex_id} err={type(e).__name__}: {e}")
        finally:
            cls.exchange = None

    @classmethod
    async def cleanup_resources(cls):
        """
        Kaynakları temizle - Connection leak önleme
        """
        try:
            cls.is_running = False

            # ✅ tek kapı
            await cls.close_exchange_safe(reason="cleanup_resources")

            cls.ai_scan_cache.clear()
            cls.processed_signals.clear()

            if hasattr(cls, '_ticker_cache'):
                delattr(cls, '_ticker_cache')

            logging.info("✅ Tüm kaynaklar temizlendi")

        except Exception as e:
            logging.error(f"❌ Kaynak temizleme hatası: {str(e)}")

    @classmethod
    def _extract_update_primitives(cls, update: Update):
        """
        CQ/Message fark etmeksizin güvenli şekilde primitive'leri çıkarır.
        DÜZELTİLDİ: Type annotation kaldırıldı
        """
        try:
            cq = getattr(update, "callback_query", None)
            msg = getattr(update, "message", None)

            source_msg = None
            if cq and getattr(cq, "message", None):
                source_msg = cq.message
            elif msg:
                source_msg = msg

            chat_id = None
            message_id = None

            if source_msg is not None:
                try:
                    chat_id = source_msg.chat_id
                except AttributeError:
                    try:
                        chat = getattr(source_msg, "chat", None)
                        chat_id = getattr(chat, "id", None)

                    except Exception as e:
                        logging.error(f"Hata: {e}")

                    pass

                try:
                    message_id = getattr(source_msg, "message_id", None)

                except Exception as e:
                    logging.error(f"Hata: {e}")

                pass

            if chat_id is None:
                try:
                    eff_chat = getattr(update, "effective_chat", None)
                    if eff_chat:
                        chat_id = getattr(eff_chat, "id", None)

                except Exception as e:
                    logging.error(f"Hata: {e}")

                pass

            if chat_id is None:
                try:
                    eff_user = getattr(update, "effective_user", None)
                    if eff_user:
                        chat_id = getattr(eff_user, "id", None)

                except Exception as e:
                    logging.error(f"Hata: {e}")
                pass
            return cq, msg, chat_id, message_id

        except Exception as e:
            logging.error(f"_extract_update_primitives err: {repr(e)}")
            return None, None, None, None

    @classmethod
    def _human_duration(cls, start_dt, end_dt=None):
        if not start_dt:
            return "-"
        try:
            start_dt_aware = cls._ensure_aware(start_dt)
            if not start_dt_aware:
                return "-"

            if end_dt is None:
                end_dt = datetime.now(timezone.utc)

            else:
                end_dt_aware = cls._ensure_aware(end_dt)
                if not end_dt_aware:
                    end_dt = datetime.now(timezone.utc)
                else:
                    end_dt = end_dt_aware

            delta = end_dt - start_dt_aware
            if delta.total_seconds() < 0:
                delta = timedelta(seconds=0)

            secs = int(delta.total_seconds())
            if secs < 60: return f"{secs}sn"
            mins = secs // 60
            hours = mins // 60
            days = hours // 24
            mins = mins % 60
            hours = hours % 24
            parts = []
            if days: parts.append(f"{days}g")
            if hours: parts.append(f"{hours}sa")
            if mins and len(parts) < 2: parts.append(f"{mins}dk")
            if not parts: parts.append(f"{secs}sn")
            return " ".join(parts)
        except Exception as e:
            logging.error(f"[HUMAN_DURATION_ERR] {type(e).__name__}: {e}")
            return "-"

    @classmethod
    def _active_signal_duration(cls, s: dict):
        return cls._human_duration(cls._ensure_aware(s.get('signal_time')), datetime.now(timezone.utc))

    @classmethod
    def _build_dashboard_pages(cls, ticker_map: dict, closed_signals: list):
        """
        SAYFALI DASHBOARD (NUMARALI, DOĞRU SÜRE, STRATEJİ GÖSTERİMİ)
        """
        active = [s for s in cls.active_signals if s.get('active')]

        unique_closed = []
        seen_keys = set()
        for cc in closed_signals:
            key = (cc.get('signal_id'), cc.get('exit_type'))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            unique_closed.append(cc)
        closed_signals = unique_closed

        lines_header = ["📊 AKTİF SİNYALLER", "────────────────────────"]

        active_hit_total = 0
        active_wait_total = 0

        if not active:
            lines_header.append("Aktif sinyal yok.")
        else:
            for idx, s in enumerate(active, 1):
                symbol_raw = s.get('symbol')
                symbol = cls.to_display_symbol(symbol_raw) if hasattr(cls, "to_display_symbol") else symbol_raw
                sig_id = s.get('signal_id', '?')
                direction = s.get('signal_type', '?')
                strat_raw = (s.get('strategy_id') or s.get('strategy_class') or "?")
                strat = f"V{strat_raw[-1].upper()}" if strat_raw.lower().startswith('v') else strat_raw.upper()
                entry = s.get('entry_price')
                targets = s.get('targets', [])
                targets_hit = s.get('targets_hit', [])

                if len(targets_hit) != len(targets):
                    targets_hit = [False] * len(targets)

                hit_cnt = sum(1 for h in targets_hit if h)
                wait_cnt = len(targets_hit) - hit_cnt
                active_hit_total += hit_cnt
                active_wait_total += wait_cnt

                last_price = entry
                # ticker_map lookup için önce raw sembolü kullan
                tdata = ticker_map.get(symbol_raw)

                # settle suffix'li ise base spot'a düş (BTC/USDT:USDT -> BTC/USDT)
                if not tdata and symbol_raw and isinstance(symbol_raw, str) and ':' in symbol_raw:
                    tdata = ticker_map.get(symbol_raw.split(':', 1)[0])

                # raw core ise (XLMUSDT) slash'lı dene
                if not tdata and symbol_raw and isinstance(symbol_raw,
                        str) and '/' not in symbol_raw and symbol_raw.endswith('USDT'):
                    tdata = ticker_map.get(symbol_raw[:-4] + '/USDT')

                # en son display ile de dene (XLM/USDT)
                if not tdata and symbol:
                    tdata = ticker_map.get(symbol)

                if isinstance(tdata, dict):
                    last_price = tdata.get('last', last_price)

                try:
                    upnl = ((last_price - entry) / entry * 100 if str(direction).upper() == 'LONG'
                            else (entry - last_price) / entry * 100) if entry and last_price and entry != 0 else 0.0
                except Exception as e:
                    logging.error(f"Hata: {e}")
                    upnl = 0.0

                duration = cls._active_signal_duration(s)
                realized, hit_durs = cls._active_realized_info(s)

                hit_part = ("Vurulan: | " + " | ".join(hit_durs)) if hit_durs else "Vurulan: -"
                tp_marks = "".join("✅" if h else "⭕" for h in targets_hit[:5]) or "—"

                lines_header.append(
                    f"{idx}) ({sig_id}) | **{symbol}** | {direction} [{strat}]\n"
                    f"  {tp_marks} | Anlık PNL: {upnl:+.2f}% | Süre: {duration}\n"
                    f"  {hit_part}\n"
                    f"{'─' * 40}")
        lines_header.append("")
        lines_header.append("🕒 SON 24 SAAT (Anchorlu)")
        lines_header.append(f"(Anchor: {cls.DASHBOARD_ANCHOR_HOUR:02d}:00 → "
                            f"{(cls.DASHBOARD_ANCHOR_HOUR + 24) % 24:02d}:00)")
        lines_header.append("────────────────────────")

        total_closed = len(closed_signals)
        tfinal = sum(1 for c in closed_signals if c.get('exit_type') == 'TARGET_FINAL')
        tstop = sum(1 for c in closed_signals if c.get('exit_type') == 'STOP')

        pnl_list = []
        closed_lines = []
        for idx, c in enumerate(closed_signals, 1):
            symbol_raw = c.get('symbol')
            symbol = cls.to_display_symbol(symbol_raw) if hasattr(cls, "to_display_symbol") else symbol_raw
            direction = c.get('signal_type', '')
            exit_type = c.get('exit_type', '')
            strat_raw = c.get('strategy_id') or c.get('strategy_class') or "?"
            targets = c.get('targets', [])
            targets_hit = c.get('targets_hit', [])
            tp_marks = "".join(
                "✅" if i < len(targets_hit) and targets_hit[i] else "⭕" for i in range(min(5, len(targets))))

            sig_id = c.get('signal_id', '?')

            strat = f"V{strat_raw[-1].upper()}" if (
                    isinstance(strat_raw, str) and strat_raw.lower().startswith('v')) else str(strat_raw).upper()

            net = c.get('realized_net_pct')
            if net is None:
                net = c.get('pnl_pct', 0.0)

            try:
                net = float(net)
                if math.isnan(net) or math.isinf(net):
                    net = 0.0
            except (ValueError, TypeError) as e:
                logging.error(f"Spesifik hata: {e}")
                net = 0.0

            pnl_list.append(net)

            closed_time = c.get('closed_time')
            time_part = ""
            closed_dt = None
            if isinstance(closed_time, str) and len(closed_time) >= 19:
                time_part = closed_time[11:16]
                try:
                    closed_dt = cls._ensure_aware(c.get('closed_time'))
                except Exception as e:
                    logging.error(f"Hata: {e}")
                    pass
            signal_time = c.get('signal_time')
            if isinstance(signal_time, str):
                try:
                    signal_time_dt = cls._ensure_aware(c.get('signal_time'))
                except Exception as e:
                    logging.error(f"Hata: {e}")
                    signal_time_dt = None
            else:
                signal_time_dt = signal_time if isinstance(signal_time, datetime) else None
            life_str = "-"
            if signal_time_dt and closed_dt:
                life_str = cls._human_duration(signal_time_dt, closed_dt)

            tag = "✅" if exit_type in ("TARGET_FINAL", "TARGET") else ("❌" if exit_type == "STOP" else "•")

            dur_line = ""
            if c.get('targets_hit_times'):
                try:
                    st_dt = signal_time_dt
                    tt = []
                    if st_dt:
                        for ti, ttime in enumerate(c['targets_hit_times']):
                            if not ttime:
                                continue
                            if isinstance(ttime, str):
                                ttime = cls._ensure_aware(ttime)
                            tt.append(f"T{ti + 1}:{cls._human_duration(st_dt, ttime)}")
                    if tt:
                        dur_line = " " + " ".join(tt[:5])
                except Exception as e:
                    logging.debug(f"Dashboard süre formatlama hatası: {e}")
                    pass

            closed_lines.append(
                f"{idx}) {tag} {exit_type} ({sig_id})\n"
                f"  TP: {tp_marks} | {direction} [{strat}] | **{symbol}**\n"
                f"  {dur_line.strip()}\n"
                f"  Net: {net:+.2f}% | Süre: {life_str} | {time_part}\n"
                f"{'─' * 40}")
        total_net = sum(pnl_list) if pnl_list else 0.0
        avg_net = (total_net / len(pnl_list)) if pnl_list else 0.0
        import statistics
        median_net = statistics.median(pnl_list) if pnl_list else 0.0
        net_status = "Pozitif" if total_net > 0 else "Negatif" if total_net < 0 else "Nötr"

        footer = ["", "📌 ÖZET", f"Aktif ({len(active)}): Vurulan {active_hit_total} | Bekleyen {active_wait_total}"]

        try:
            total_active_real = sum(cls._active_realized_info(s)[0] for s in active)
            footer.append(f"Aktif Realized: {total_active_real:+.2f}%")
        except Exception as e:
            logging.error(f"Hata: {e}")

        footer.append(f"24s Kapanış ({total_closed}): ✅ {tfinal} | ❌ {tstop}")
        footer.append(
            f"24s Net: {total_net:+.2f}% ({net_status}) | Ort:{avg_net:+.2f}% | Medyan:{median_net:+.2f}%")
        footer.append("")
        footer.append("Açıklama: Aktif satır süresi = (son hedef vurulmuşsa) o andan beri; yoksa açılıştan beri.")
        footer.append("Açıklama: Kapanan satır süresi = sinyal toplam ömrü. ✅ hedef / ❌ stop / Net = gerçekleşen.")
        footer.append("Sayfalar: ⬅️ / ➡️ | Yenile: ♻️")

        page_size = cls.DASHBOARD_PAGE_SIZE
        if not closed_lines:
            pages = ["\n".join(lines_header + ["Kapanan sinyal yok."] + footer)]
            return pages, {'total_closed':total_closed, 'tfinal':tfinal, 'tstop':tstop, 'total_net':total_net}

        chunks = [closed_lines[i:i + page_size] for i in range(0, len(closed_lines), page_size)]
        pages = []
        for i, chunk in enumerate(chunks):
            head = lines_header if i == 0 else [f"🕒 SON 24 SAAT (Sayfa {i + 1}/{len(chunks)})"]
            page_lines = head + chunk
            if i == len(chunks) - 1:
                page_lines += footer
            pages.append("\n".join(page_lines))

        return pages, {'total_closed':total_closed, 'tfinal':tfinal, 'tstop':tstop, 'total_net':total_net}

    @classmethod
    def _active_realized_info(cls, s: dict):
        """
        Aktif sinyalde şimdiye kadar vurulan hedeflerin realize ettiği toplam yüzdelik (basit toplama)
        ve vurulan hedeflerin süreleri (T1:5dk ...). Yöntem: hedef i yüzdesi
        LONG: (target-entry)/entry *100
        SHORT: (entry-target)/entry *100
        """
        try:
            entry = s.get('entry_price')
            direction = s.get('signal_type', 'LONG')
            targets = s.get('targets', [])
            hits = s.get('targets_hit', [])
            hit_times = s.get('targets_hit_times', [])
            sig_t = s.get('signal_time')
            if isinstance(sig_t, str):
                try:
                    from datetime import datetime
                    sig_t = cls._ensure_aware(sig_t)

                except Exception as e:
                    logging.error(f"Hata: {e}")

                    sig_t = None

            realized = 0.0
            hit_durations = []
            for i, (t_hit, t_price) in enumerate(zip(hits, targets)):
                if not t_hit:
                    break
                if entry and t_price:
                    if direction == 'LONG':
                        pct = (t_price - entry) / entry * 100
                    else:
                        pct = (entry - t_price) / entry * 100
                    realized += pct
                ht = hit_times[i] if i < len(hit_times) else None
                if isinstance(ht, str):
                    from datetime import datetime
                    try:
                        ht = cls._ensure_aware(ht)

                    except Exception as e:
                        logging.error(f"Hata: {e}")

                        ht = None
                if sig_t and ht:
                    delta = ht - sig_t
                    secs = int(delta.total_seconds())
                    mins = secs // 60
                    hours = mins // 60
                    mins = mins % 60
                    if hours:
                        dstr = f"{hours}sa {mins}dk"
                    elif mins:
                        dstr = f"{mins}dk"
                    else:
                        dstr = f"{secs}sn"
                    hit_durations.append(f"T{i + 1}:{dstr}")
            return realized, hit_durations

        except Exception as e:
            logging.error(f"Hata: {e}")

            return 0.0, []

    @classmethod
    def list_params_by_group(cls, group):
        r = []
        rs = cls.runtime_strategy if cls.runtime_strategy else cls.strategy

        if group == 'core':
            r = [
                ('min_volume_ratio', rs.get('min_volume_ratio')),
                ('momentum_threshold', rs.get('momentum_threshold')),
                ('momentum_period', rs.get('momentum_period')),
                ('atr_multiplier', rs.get('atr_multiplier')),
            ]

        elif group == 'ai':
            ai = rs.get('ai', {})
            r = [
                ('ai.min_confidence', ai.get('min_confidence')),
                ('ai.min_volume_ratio', ai.get('min_volume_ratio')),
                ('ai.boost_add', ai.get('boost_add')),
            ]

        elif group == 'mt':
            mt = rs.get('multi_timeframe_confirmation', {})
            r = [
                ('multi_timeframe_confirmation.enabled', mt.get('enabled')),
                ('multi_timeframe_confirmation.require_alignment', mt.get('require_alignment')),
                ('multi_timeframe_confirmation.min_trend_strength_pct', mt.get('min_trend_strength_pct')),
            ]

        elif group == 'scan':
            """
            YENİ MİMARİ:
            Legacy 'scan' ve 'ai_global' yok.
            Okuma kaynağı:
              scans.timeframes
              scans.stale_alarm_minutes
              scans.tf_profiles.<tf>.ai_scan / strategy_scan
            """
            scans = ConfigService.get("scans", {}) or {}
            if not isinstance(scans, dict):
                scans = {}

            tfs = scans.get("timeframes") or []
            if not isinstance(tfs, list) or not tfs:
                tfs = ["15m"]

            # Varsayılan: candidate timeframe (varsa) yoksa ilk timeframe
            try:
                tf_candidate = ConfigService.get("CANDIDATE_SELECTION.timeframe_candidate", None)
            except Exception:
                tf_candidate = None

            tf = str(tf_candidate or (tfs[0] if tfs else "15m")).strip()

            tf_profile = ConfigService.tf_profile(tf, {}) or {}
            if not isinstance(tf_profile, dict):
                tf_profile = {}

            ai_scan = tf_profile.get("ai_scan", {}) or {}
            strat_scan = tf_profile.get("strategy_scan", {}) or {}

            def val_of(d: dict, key: str, default=None):
                v = d.get(key)
                if isinstance(v, dict) and "value" in v:
                    return v["value"]
                return v if v is not None else default

            # AI thresholds
            r.extend([
                ("scans.timeframes", tfs),
                ("scans.stale_alarm_minutes", scans.get("stale_alarm_minutes")),
                (f"scans.tf_profiles.{tf}.max_active_alarms", tf_profile.get("max_active_alarms")),
                (f"scans.tf_profiles.{tf}.ai_scan.enabled", ai_scan.get("enabled", True)),
                (f"scans.tf_profiles.{tf}.ai_scan.min_conf", val_of(ai_scan, "min_conf")),
                (f"scans.tf_profiles.{tf}.ai_scan.min_potential_pct", val_of(ai_scan, "min_potential_pct")),
                (f"scans.tf_profiles.{tf}.ai_scan.min_volume_ratio", val_of(ai_scan, "min_volume_ratio")),
                (f"scans.tf_profiles.{tf}.ai_scan.min_volume_usd", val_of(ai_scan, "min_volume_usd")),
                (f"scans.tf_profiles.{tf}.ai_scan.limits.v1", ((ai_scan.get("limits") or {}).get("v1"))),
                (f"scans.tf_profiles.{tf}.ai_scan.limits.v2", ((ai_scan.get("limits") or {}).get("v2"))),
            ])

            # Strategy thresholds
            v1 = strat_scan.get("v1", {}) or {}
            v2 = strat_scan.get("v2", {}) or {}

            r.extend([
                (f"scans.tf_profiles.{tf}.strategy_scan.enabled", strat_scan.get("enabled", True)),
                (f"scans.tf_profiles.{tf}.strategy_scan.v1.limit", v1.get("limit")),
                (f"scans.tf_profiles.{tf}.strategy_scan.v1.min_score", val_of(v1, "min_score")),
                (f"scans.tf_profiles.{tf}.strategy_scan.v1.min_volume_usd", val_of(v1, "min_volume_usd")),
                (f"scans.tf_profiles.{tf}.strategy_scan.v2.limit", v2.get("limit")),
                (f"scans.tf_profiles.{tf}.strategy_scan.v2.min_score", val_of(v2, "min_score")),
                (f"scans.tf_profiles.{tf}.strategy_scan.v2.min_volume_usd", val_of(v2, "min_volume_usd")),
            ])

        elif group == 'risk':
            r = [
                ('stop_loss', rs.get('stop_loss')),
                ('take_profit1', rs.get('take_profit1')),
                ('take_profit2', rs.get('take_profit2')),
                ('take_profit3', rs.get('take_profit3')),
                ('take_profit4', rs.get('take_profit4')),
                ('take_profit5', rs.get('take_profit5')),
            ]

        return r

    @classmethod
    def _save_scan_settings(cls, data: dict):
        try:
            os.makedirs(os.path.dirname(cls._settings_scan_file), exist_ok=True)
            with open(cls._settings_scan_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"[SCAN_SETTINGS_SAVE_ERR] {e}")
            return False

    @classmethod
    async def _send_long_text(cls, bot, chat_id: int, text: str, reply_markup=None, chunk_size: int = 4000):
        try:
            if len(text) <= 4096:
                await OlimposStrategy._retry_async(bot.send_message, chat_id=chat_id, text=text,
                    reply_markup=reply_markup, retries=3, base_delay=0.8, max_delay=OlimposStrategy.MAX_BACKOFF_SEC)
                return

            start = 0
            while start < len(text):
                end = min(start + chunk_size, len(text))
                part = text[start:end]
                await OlimposStrategy._retry_async(bot.send_message, chat_id=chat_id, text=part,
                    reply_markup=None if start > 0 else reply_markup, retries=3, base_delay=0.8,
                    max_delay=OlimposStrategy.MAX_BACKOFF_SEC)
                start = end

        except Exception as e:
            logging.error(f"[SEND_LONG_TEXT_ERR] {e}")

    @classmethod
    def _log_alarm_meta(cls, symbol: str, timeframe: str, strategy_id: str, source: str, meta: dict):
        """
        analytics/alarms_meta_log.jsonl dosyasına tek satır event yazar.
        """
        try:
            row = {"ts":datetime.now(timezone.utc).isoformat(), "source":source, "strategy_id":strategy_id,
                "timeframe":timeframe, "symbol":symbol, "meta":{"ai_confidence":meta.get("ai_confidence"),
                    "potential_pct":meta.get("potential_pct") or meta.get("strat_potential"),
                    "technical_score":meta.get("technical_score"), "volume_usd_24h":meta.get("volume_usd"),
                    "volume_ratio":meta.get("volume_ratio"), "momentum":meta.get("momentum"),
                    "compression":meta.get("compression"), "delay_quality":meta.get("delay_quality"),
                    "mt_align":meta.get("mt_align"), "structure_ok":meta.get("structure_ok"),
                    "v1_score":meta.get("v1_score"), "v2_score":meta.get("v2_score")}}
            os.makedirs("analytics", exist_ok=True)
            path = os.path.join("analytics", "alarms_meta_log.jsonl")
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as e:
            logging.error(f"[ALARM_META_LOG_ERR] {e}")

    @classmethod
    def _format_duration(cls, duration):
        """Süreyi formatla"""  
        try:  
            total_seconds = int(duration.total_seconds())

            if total_seconds < 60:
                return f"{total_seconds} saniye"
            elif total_seconds < 3600:
                minutes = total_seconds // 60
                return f"{minutes} dakika"
            elif total_seconds < 86400:
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                return f"{hours} saat {minutes} dakika"
            else:
                days = total_seconds // 86400
                hours = (total_seconds % 86400) // 3600
                return f"{days} gün {hours} saat"
        except (AttributeError, TypeError, ValueError) as e:
            logging.error(f"Süre formatlanırken hata oluştu: {e}")
            return "Bilinmiyor"

    @classmethod
    def get_active_alarms_count(cls) -> int:
        """Aktif alarm (sinyal) sayısını döndürür."""
        return len(cls.active_signals)

    @classmethod
    def get_active_signal_count(cls) -> int:
        """Aktif (open) sinyal sayısı."""
        try:
            return sum(1 for s in (cls.active_signals or []) if isinstance(s, dict) and s.get("active"))
        except Exception:
            return 0
