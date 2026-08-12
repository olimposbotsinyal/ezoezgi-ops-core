# sinyal_merkezi.py
from logger_config import setup_logging
from settings.execution_settings_plan import build_execution_plan
from core.execution_portfolio_gate import can_open_position
from analytics.user_trade_recorder import append_user_trade_event
from settings.execution.symbol_meta_provider import get_symbol_meta as provider_get_symbol_meta
from core.risk_kill_switch import can_open_entry
import re
from typing import List, Union, Optional, Dict, Any, Callable
from data.olimpos_data import db_operation, get_user_settings
import asyncio
import traceback
import inspect
import math
from telegram import Update
from typing import Awaitable, Protocol
import logging
from settings.trailing_manager import TRAILING_MANAGER
from settings.execution.mexc_al_sat import TRAILING_POSITION_OWNERS as TRAILING_POSITION_OWNERS_MEXC



class PlanExecutor(Protocol):
    def __call__(self, plan: Any, user: Dict[str, Any]) -> Awaitable[Dict[str, Any]]: ...

logger = setup_logging('signal_merkezi')
logging.getLogger('signal_merkezi').setLevel(logging.INFO)
logging.getLogger(__name__).info("[signal_merkezi] module import OK")

USER_TRADE_SEMAPHORE = asyncio.Semaphore(8)  # aynı anda max 8 kullanıcı emri
ACTIVE_OPEN_POSITIONS = {}
# key: (user_id, exchange, symbol) -> {'side': 'LONG','opened_at': ts}


def _parse_trailing_mode_param(x: Any) -> tuple[Optional[str], Optional[float]]:
    """
    plan.terial_stop gibi değerlerden mode/param üretir.
    Ör:
      "TRAILING_2.0" -> ("TRAILING", 2.0)
      "TRAILING" -> ("TRAILING", None)
      None -> (None, None)
    """
    if x is None:
        return None, None
    s = str(x).strip()
    if not s:
        return None, None

    # "TRAILING_2.0" / "TRAILING-2.0" / "TRAILING:2.0"
    for sep in ("_", "-", ":", "|"):
        if sep in s:
            a, b = s.split(sep, 1)
            mode = a.strip().upper() or None
            try:
                param = float(b.strip())
            except Exception:
                param = None
            return mode, param

    return s.upper(), None


def _safe_trailing_register(pid: int, state: dict):
    """
    TRAILING_MANAGER API farklı olabilir diye güvenli register.

    DÜZELTME:
    - trailing kapalıysa register ETME (no-op True).
    - TrailingManager.register() imzası symbol/param istiyorsa otomatik besle.
    """
    try:
        pid = int(pid)

        st = state or {}

        # --- trailing kapalı mı? (no-op) ---
        raw_mode = (
            st.get("trailing_mode")
            or st.get("terial_stop")
            or st.get("trailing")
            or st.get("mode")
        )
        raw_s = ("" if raw_mode is None else str(raw_mode)).strip().upper()

        # KAPALI varyantları
        if raw_s in ("", "KAPALI", "OFF", "NONE", "0", "FALSE", "NO"):
            return True

        side = (st.get("side") or "").upper().strip() or None

        symbol = st.get("symbol") or st.get("symbol_core") or st.get("symbol_exchange")
        symbol = (str(symbol).strip() if symbol is not None else None) or None

        entry_price = st.get("entry_price")
        try:
            entry_price = float(entry_price) if entry_price is not None else None
        except Exception:
            entry_price = None

        mode, param = _parse_trailing_mode_param(raw_mode)

        # ✅ TrailingManager uyumu: "TRAILING" -> "PCT"
        if mode == "TRAILING":
            mode = "PCT"

        # mode/param yoksa: register etmeye çalışma
        if not mode or param is None:
            return True

        reg = getattr(TRAILING_MANAGER, "register", None)
        if callable(reg):
            sig = None
            try:
                sig = inspect.signature(reg)
            except Exception:
                sig = None

            if sig is not None:
                params = list(sig.parameters.keys())

                # 1) state isteyen imzalar
                wants_state = ("state" in params) or ("position" in params) or ("data" in params)
                if wants_state:
                    try:
                        return reg(pid, st)
                    except TypeError:
                        for k in ("state", "position", "data"):
                            if k in sig.parameters:
                                return reg(pid, **{k: st})
                        raise

                # 2) keyword arg imzaları: pid/position_id + symbol + side + mode + param + entry_price
                kwargs = {}

                if "pid" in sig.parameters:
                    kwargs["pid"] = pid
                if "position_id" in sig.parameters:
                    kwargs["position_id"] = pid
                if "positionId" in sig.parameters:
                    kwargs["positionId"] = pid

                # bazı manager'lar symbol ister
                if "symbol" in sig.parameters and symbol is not None:
                    kwargs["symbol"] = symbol

                if "side" in sig.parameters and side is not None:
                    kwargs["side"] = side

                if "mode" in sig.parameters and mode is not None:
                    kwargs["mode"] = mode

                # kritik: param
                if "param" in sig.parameters and param is not None:
                    kwargs["param"] = float(param)

                if "entry_price" in sig.parameters and entry_price is not None:
                    kwargs["entry_price"] = entry_price

                # state’i de destekliyorsa ver
                if "state" in sig.parameters:
                    kwargs["state"] = st

                if kwargs:
                    return reg(**kwargs)

                # 3) positional fallback (en yaygın varyantlar)
                # register(pid, symbol, param, ...)
                try:
                    return reg(pid, symbol, param)
                except TypeError:
                    # register(pid, side, mode, param, entry_price)
                    return reg(pid, side, mode, param, entry_price)

            # signature alınamadıysa: önce state, sonra positional
            try:
                return reg(pid, st)
            except TypeError:
                try:
                    return reg(pid, symbol, param)
                except TypeError:
                    return reg(pid, side, mode, param, entry_price)

        # Diğer olası isimler
        for name in ("add_position", "register_position"):
            fn = getattr(TRAILING_MANAGER, name, None)
            if callable(fn):
                return fn(pid, st)

        # fallback: direct dict
        positions = getattr(TRAILING_MANAGER, "positions", None)
        if isinstance(positions, dict):
            positions[pid] = st
            return True

        logger.error("[TRAILING_REGISTER_ERR] TRAILING_MANAGER uygunsuz: positions dict yok, register yok")
        return False

    except Exception as e:
        logger.error(f"[TRAILING_REGISTER_ERR] pid={pid} err={e}", exc_info=True)
        return False


def _safe_trailing_unregister(pid: int):
    try:
        pid = int(pid)

        if hasattr(TRAILING_MANAGER, "unregister") and callable(getattr(TRAILING_MANAGER, "unregister")):
            return TRAILING_MANAGER.unregister(pid)

        if hasattr(TRAILING_MANAGER, "remove_position") and callable(getattr(TRAILING_MANAGER, "remove_position")):
            return TRAILING_MANAGER.remove_position(pid)

        if hasattr(TRAILING_MANAGER, "unregister_position") and callable(getattr(TRAILING_MANAGER, "unregister_position")):
            return TRAILING_MANAGER.unregister_position(pid)

        positions = getattr(TRAILING_MANAGER, "positions", None)
        if isinstance(positions, dict):
            positions.pop(pid, None)
            return True

        return True
    except Exception as e:
        logger.error(f"[TRAILING_UNREGISTER_ERR] pid={pid} err={e}", exc_info=True)
        return False


def _get_mexc_web_key_safe(user_id: int) -> str:
    """
    get_mexc_web_key bazen bu modülde import edilmemiş olabiliyor.
    Önce settings.execution.mexc_al_sat içinden dener, yoksa boş döner.
    """
    try:
        mod = __import__("settings.execution.mexc_al_sat", fromlist=["get_mexc_web_key"])
        fn = getattr(mod, "get_mexc_web_key", None)
        if callable(fn):
            return (fn(int(user_id)) or "").strip()
    except Exception:
        pass
    return ""

def get_symbol_meta_fn_for_exchange(exchange: str) -> Callable[[str], Optional[Dict[str, Any]]]:
    """
    Plan builder'ın beklediği imza: (symbol: str) -> Optional[dict]

    Öncelik:
      1) GET_SYMBOL_META_FN[exchange] (local meta)
      2) provider_get_symbol_meta(exchange=..., symbol=...)
    """
    ex = (exchange or "").lower().strip()
    local_fn = GET_SYMBOL_META_FN.get(ex, _default_get_symbol_meta)

    def _resolver(symbol_any: str) -> Optional[Dict[str, Any]]:
        # 1) local
        try:
            r = local_fn(symbol_any)
            if r:
                return r
        except Exception as e:
            logger.warning(f"[LOCAL_META_ERR] ex={ex} sym={symbol_any} err={e}")

        # 2) provider
        try:
            return provider_get_symbol_meta(exchange=ex, symbol=symbol_any)
        except Exception as e:
            logger.error(f"[PROVIDER_META_ERR] ex={ex} sym={symbol_any} err={e}", exc_info=True)
            return None

    return _resolver


from core.symbol_resolver import normalize_core_symbol

def normalize_symbol_for_key(symbol: str) -> str:
    return normalize_core_symbol(symbol)


def _safe_import_symbol_meta(module_path: str, attr_candidates: List[str]):
    """
    module_path içinden sembol meta fonksiyonunu güvenli import eder.
    attr_candidates sırasıyla denenir: ['get_symbol_meta_x', 'get_symbol_meta'] gibi.
    Bulamazsa None döner.
    """
    try:
        mod = __import__(module_path, fromlist=['*'])
    except Exception as e:
        logger.warning(f"[SYMBOL_META_IMPORT_FAIL] module={module_path} err={e}")
        return None

    for attr in attr_candidates:
        fn = getattr(mod, attr, None)
        if callable(fn):
            logger.info(f"[SYMBOL_META_IMPORT_OK] module={module_path} fn={attr}")
            return fn
    logger.warning(f"[SYMBOL_META_IMPORT_MISSING] module={module_path} attrs={attr_candidates}")
    return None


def _default_get_symbol_meta(symbol: str) -> Optional[dict]:
    # None yerine log basmak debug için iyi
    logger.warning(f"[GET_SYMBOL_META_DEFAULT] meta yok: {symbol}")
    return None


def _default_calculate_take_profits(
    *,
    entry_price: float,
    side: str,
    tp_percentages: Optional[List[float]] = None,
    signal_tp_prices: Optional[List[float]] = None
) -> List[float]:
    """
    Fallback:
    - Sinyal TP verdiyse onu kullan
    - Yoksa yüzde ile üret (varsayılan 5x %2)
    """
    if signal_tp_prices:
        return signal_tp_prices
    tp_percentages = tp_percentages or [2, 2, 2, 2, 2]
    out = []
    s = (side or "").upper()
    for p in tp_percentages:
        if s == "LONG":
            out.append(entry_price * (1 + float(p)/100))
        else:
            out.append(entry_price * (1 - float(p)/100))
    return out

# Exchange'e özel symbol meta fonksiyonlarını bağla (varsa)
get_symbol_meta_binance = _safe_import_symbol_meta(
    "settings.execution.binance_al_sat",
    ["get_symbol_meta_binance", "get_symbol_meta"]
)

get_symbol_meta_mexc = _safe_import_symbol_meta(
    "settings.execution.mexc_al_sat",
    ["get_symbol_meta_mexc", "get_symbol_meta"]
)

get_symbol_meta_bybit = _safe_import_symbol_meta(
    "settings.execution.bybit_al_sat",
    ["get_symbol_meta_bybit", "get_symbol_meta"]
)

get_symbol_meta_bitget = _safe_import_symbol_meta(
    "settings.execution.bitget_al_sat",
    ["get_symbol_meta_bitget", "get_symbol_meta"]
)

get_symbol_meta_okx = _safe_import_symbol_meta(
    "settings.execution.okx_al_sat",
    ["get_symbol_meta_okx", "get_symbol_meta"]
)

get_symbol_meta_bitmart = _safe_import_symbol_meta(
    "settings.execution.bitmart_al_sat",
    ["get_symbol_meta_bitmart", "get_symbol_meta"]
)


GET_SYMBOL_META_FN = {
    "binance": get_symbol_meta_binance or _default_get_symbol_meta,
    "mexc": get_symbol_meta_mexc or _default_get_symbol_meta,
    "bybit": get_symbol_meta_bybit or _default_get_symbol_meta,
    "bitget": get_symbol_meta_bitget or _default_get_symbol_meta,
    "okx": get_symbol_meta_okx or _default_get_symbol_meta,
    "bitmart": get_symbol_meta_bitmart or _default_get_symbol_meta,
}


def _default_compute_notional_to_contracts_fn(
    *,
    entry_price: float,
    fixed_notional_usdt: float,
    amount_step: float = 1.0,
    min_amount: float = 0.0
) -> float:
    """
    build_execution_plan'in çağırdığı imza ile birebir uyumlu default qty hesaplayıcı.
    """
    try:
        entry_price = float(entry_price or 0)
        fixed_notional_usdt = float(fixed_notional_usdt or 0)
        if entry_price <= 0 or fixed_notional_usdt <= 0:
            return 0.0

        qty = fixed_notional_usdt / entry_price

        step = float(amount_step or 1.0)
        if step > 0:
            qty = math.floor(qty / step) * step

        if min_amount and qty < float(min_amount):
            return 0.0

        return float(qty)
    except Exception:
        return 0.0


def build_plan_compat(
    *,
    user: Dict[str, Any],
    signal_dict: Dict[str, Any],
    settings: Dict[str, Any],
    exchange: str,
    get_symbol_meta_fn: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
    compute_notional_to_contracts_fn: Optional[Callable[..., float]] = None,
    calculate_take_profits_fn: Optional[Callable[..., Any]] = None,
) -> Any:
    """
    build_execution_plan için geriye/ileri uyumlu wrapper (kesin sürüm).

    - build_execution_plan imzasını inspect eder
    - sadece kabul ettiği kwargs'ları gönderir
    - get_symbol_meta_fn verilmediyse exchange-aware resolver üretir
    """
    ex = (exchange or user.get("exchange") or settings.get("exchange") or "").lower().strip()

    # Resolver seç
    if get_symbol_meta_fn is None:
        get_symbol_meta_fn = get_symbol_meta_fn_for_exchange(ex)

    # Default fonksiyonlar
    if compute_notional_to_contracts_fn is None:
        compute_notional_to_contracts_fn = _default_compute_notional_to_contracts_fn
    if calculate_take_profits_fn is None:
        calculate_take_profits_fn = _default_calculate_take_profits

    fn = build_execution_plan
    sig = inspect.signature(fn)
    accepted = set(sig.parameters.keys())

    kwargs: Dict[str, Any] = {}

    if "user" in accepted:
        kwargs["user"] = user
    if "signal_dict" in accepted:
        kwargs["signal_dict"] = signal_dict
    if "settings" in accepted:
        kwargs["settings"] = settings

    if "get_symbol_meta_fn" in accepted:
        kwargs["get_symbol_meta_fn"] = get_symbol_meta_fn
    if "compute_notional_to_contracts_fn" in accepted:
        kwargs["compute_notional_to_contracts_fn"] = compute_notional_to_contracts_fn
    if "calculate_take_profits_fn" in accepted:
        kwargs["calculate_take_profits_fn"] = calculate_take_profits_fn

    if "exchange" in accepted:
        kwargs["exchange"] = ex

    try:
        return fn(**kwargs)
    except TypeError as te:
        logger.error(
            f"[BUILD_PLAN_COMPAT_TYPE_ERR] ex={ex} err={te} kwargs={list(kwargs.keys())}",
            exc_info=True
        )
        raise
    except Exception as e:
        logger.error(f"[BUILD_PLAN_COMPAT_ERR] ex={ex} err={e}", exc_info=True)
        raise


async def get_current_price(symbol: str) -> Optional[dict]:
    from price_manager import MultiExchangePriceManager

    # MultiExchangePriceManager örneğini oluştur
    price_manager = MultiExchangePriceManager()

    try:
        # Fiyatları fetch et
        prices = await price_manager.fetch_price(symbol)

        # Eğer hiç fiyat bulunamazsa
        if not prices:
            logger.error(f"Hiçbir exchange'den fiyat alınamadı: {symbol}")
            return None

        # Geçerli fiyatları filtrele
        valid_prices = [
            (exchange, data)
            for exchange, data in prices.items()
            if data['price'] != float('inf') and data['price'] > 0
        ]

        if not valid_prices:
            logger.error(f"Geçerli fiyat bulunamadı: {symbol}")
            return None

        # Fiyatlara göre sırala
        sorted_prices = sorted(
            valid_prices,
            key=lambda x: x[1]['price']
        )

        # En düşük fiyatlı exchange
        best_price = sorted_prices[0]

        # Fiyat normalizasyonu
        price = best_price[1]['price']

        # Bilimsel gösterimden kaçın
        if isinstance(price, str):
            price = float(price)

        # Çok küçük fiyatları düzelt
        if abs(price) < 1e-8:
            price = float(f"{price:.8f}")

        # En iyi fiyat için detaylı bilgi
        logger.info("\n🏆 En İyi Giriş Fiyatı:")
        logger.info(f"Exchange: {best_price[0].upper()}")
        logger.info(f"Sinyal MErkezi Sembol: {best_price[1]['symbol']}")
        logger.info(f"Fiyat: {price:.8f}")

        return {
            'price': price,
            'used_symbol': best_price[1]['symbol']
        }

    except Exception as error1:
        logger.error(f"Fiyat alma sürecinde genel hata: {error1}")
        return None


def auto_round_price(price: Optional[float]) -> Optional[float]:
    """
    Fiyatı büyüklüğüne göre otomatik olarak uygun ondalıkta yuvarlar.
    """
    if price is None:
        return None
    try:
        price = float(price)
    except (TypeError, ValueError):
        return None
    if price >= 100000:
        dec = 0
    elif price >= 10000:
        dec = 1
    elif price >= 1000:
        dec = 2
    elif price >= 100:
        dec = 2
    elif price >= 10:
        dec = 3
    elif price >= 1:
        dec = 4
    elif price >= 0.1:
        dec = 5
    elif price >= 0.01:
        dec = 6
    elif price >= 0.001:
        dec = 8
    elif price >= 0.0001:
        dec = 8
    else:
        dec = 12
    return round(price, dec)


def auto_round_price_str(price: float) -> str:
    val = auto_round_price(price)
    dec = str(val)[::-1].find('.')
    return f"{val:.{dec}f}"


async def parse_signal(
        signal_text,
        active_channel=None,
        sender_email=None,
        active_channels=None,
        context=None
):
    """
    Email'den gelen ham sinyal metnini parse eden gelişmiş fonksiyon
    """
    if not signal_text or not isinstance(signal_text, str):
        logger.warning("❌ Geçersiz sinyal metni")
        return None

    logger.info(f"📝 Parse_signal fonksiyonu çağrıldı. Gelen metin: {signal_text}")

    def extract_channel_ids(channels):
        try:
            return [
                channel.get('channel_id')
                for channel in (channels or [])
                if channel and channel.get('channel_id')
            ]
        except Exception as extract_error:
            logger.error(f"Channel ID çıkarma hatası: {extract_error}")
            return []

    signal_dict = {
        'signal_type': None,
        'symbol': None,
        'original_symbol': None,
        'position_type': None,
        'entry_points': [],
        'stop_loss': None,
        'take_profits': [],
        'close_price': None,
        'active_channel': active_channel,
        'sender_email': sender_email,
        'active_channels': active_channels,
        'channel_ids': extract_channel_ids(active_channels)
    }

    try:
        lines = [line.strip() for line in signal_text.split('\n') if line.strip()]

        detected_symbol = None
        sinyal_tipi_bulundu = False
        sembol_bulundu = False

        for line in lines:
            if line.startswith('SİNYAL TİPİ:'):
                try:
                    signal_type_val = line.split(':')[1].strip().upper()
                    if signal_type_val in ['OPEN', 'CLOSE']:
                        signal_dict['signal_type'] = signal_type_val
                        sinyal_tipi_bulundu = True
                        if detected_symbol:
                            logger.info(f"Mevcut sembol: {detected_symbol}")
                        logger.info(f"🟢 {signal_type_val} SİNYALİ Tespit Edildi!")
                except Exception as sinyal_tipi_error:
                    logger.error(f"Sinyal tipi tespit hatası: {sinyal_tipi_error}")

            if line.startswith('Sembol:'):
                try:
                    detected_symbol = clean_symbol_format(line.split(':')[1].strip())
                    if not detected_symbol:
                        logger.warning("⚠️ Boş sembol tespit edildi")
                        continue
                    signal_dict['original_symbol'] = detected_symbol
                    signal_dict['symbol'] = f"{detected_symbol}USDT"
                    sembol_bulundu = True
                    logger.info(f"✅ Sembol: {signal_dict['symbol']}")
                except Exception as sembol_error:
                    logger.error(f"Sembol işleme hatası: {sembol_error}")

            if line.startswith('Pozisyon Tipi:'):
                try:
                    position_type = line.split(':')[1].strip().upper()
                    if position_type in ['LONG', 'SHORT']:
                        signal_dict['position_type'] = position_type
                        logger.info(f"🔍 Pozisyon Tipi: {position_type}")
                except Exception as position_type_error:
                    logger.error(f"Pozisyon tipi tespit hatası: {position_type_error}")

            if signal_dict['signal_type'] == 'OPEN':
                if line.startswith('Giriş Fiyatları:'):
                    try:
                        entry_points = [auto_round_price(float(point.strip())) for point in
                                        re.findall(r'(\d+\.\d+)', line)]
                        signal_dict['entry_points'].extend(entry_points)
                    except Exception as error2:
                        logger.error(f"Giriş noktaları parse edilemedi: {error2}")

                elif line.startswith('Stop Loss:'):
                    try:
                        signal_dict['stop_loss'] = auto_round_price(float(re.findall(r'(\d+\.\d+)', line)[0]))
                    except Exception as error3:
                        logger.error(f"Stop Loss parse edilemedi: {error3}")

                elif line.startswith('Hedef Fiyatlar:'):
                    try:
                        target_prices = [auto_round_price(float(price.strip())) for price in
                                         re.findall(r'(\d+\.\d+)', line)]
                        signal_dict['take_profits'].extend(target_prices)
                    except Exception as error4:
                        logger.error(f"Hedef fiyatlar parse edilemedi: {error4}")

            elif signal_dict['signal_type'] == 'CLOSE':
                if line.startswith('Kapanış Fiyatı:'):
                    try:
                        signal_dict['close_price'] = auto_round_price(float(re.findall(r'(\d+\.\d+)', line)[0]))
                    except Exception as error5:
                        logger.error(f"Kapanış fiyatı parse edilemedi: {error5}")

        if signal_dict['signal_type'] == 'OPEN':
            logger.info("🟢 AÇILIŞ SİNYALİ DETAYLARI:")
            logger.info(f"🔖 Sembol: {signal_dict['symbol']}")
            logger.info(f"📈 Pozisyon Tipi: {signal_dict['position_type']}")
            logger.info(f"💰 Giriş Fiyatları: {signal_dict['entry_points']}")
            logger.info(f"🛑 Stop Loss: {signal_dict['stop_loss']}")
            logger.info(f"🎯 Take Profits: {signal_dict['take_profits']}")

        elif signal_dict['signal_type'] == 'CLOSE':
            logger.info("🔴 KAPANIŞ SİNYALİ DETAYLARI:")
            logger.info(f"🔖 Sembol: {signal_dict['symbol']}")
            logger.info(f"📈 Pozisyon Tipi: {signal_dict['position_type']}")
            logger.info(f"🏁 Kapanış Fiyatı: {signal_dict['close_price']}")

        if not sinyal_tipi_bulundu or not sembol_bulundu:
            logger.warning("⚠️ Geçerli bir sinyal bulunamadı")
            return None

        if signal_dict['signal_type'] == 'OPEN':
            logger.info("🟢 Açılış sinyali için işlem başlatılıyor")
            await process_signal_for_users(
                channel_ids=signal_dict['channel_ids'],
                signal_dict=signal_dict,
                context=context

            )
        elif signal_dict['signal_type'] == 'CLOSE':
            logger.info("🔴 Kapanış sinyali için işlem başlatılıyor")
            await process_close_signal_for_users(
                channel_ids=signal_dict['channel_ids'],
                signal_dict=signal_dict,
                context=context
            )
        return signal_dict

    except Exception as error6:
        logger.error(f"❌ Sinyal Parse Hatası: {str(error6)}")
        logger.error(f"Hata detayı: {traceback.format_exc()}")
        logger.error(f"İşlenmeye çalışılan sinyal metni: {signal_text}")
        return None


def clean_symbol_format(symbol):
    """
    Semboldeki gereksiz uzantıları temizleyen yardımcı fonksiyon
    """
    return symbol.replace('.P', '').replace('.USDT', '').upper()


async def process_signal_for_users(channel_ids, signal_dict, context=None, target_exchange: str | None = None):
    """
    OPEN sinyallerini batch işler.

    KURAL (GÜNCEL):
    - Sinyal hangi kanaldan geldiyse SADECE o kanalın kullanıcıları işlenir (tek origin).
    - Kullanıcı aynı kanala birden çok borsa bağladıysa: tüm bağlı/aktif borsalar fan-out edilir.
    - target_exchange sadece "sinyalin geldiği borsa etiketi" olarak loglanır.
      Eğer illa sadece target_exchange çalışsın istersen:
        signal_dict["strict_exchange"] = True
        ve target_exchange dolu gönder.

    Not:
    - "?" placeholder SQLite uyumludur; db_operation bunu %s'e çeviriyor.
    """
    _ = context

    # -------------------------
    # Basic validation
    # -------------------------
    if not channel_ids or not signal_dict:
        logger.error("❌ Geçersiz girdi: channel_ids veya signal_dict boş")
        return []

    if (signal_dict.get("signal_type") or "").upper() != "OPEN":
        logger.warning("[PROCESS_SIGNAL_FOR_USERS] signal_type OPEN değil -> atlanıyor")
        return []

    required = ["symbol", "position_type", "entry_points", "stop_loss"]
    missing = [k for k in required if signal_dict.get(k) is None]
    if missing:
        logger.error(f"[PROCESS_OPEN_INVALID] missing={missing}")
        return []

    # -------------------------
    # Helper: safe_db_query normalize
    # -------------------------
    def _rows(x):
        if x is None:
            return []
        if isinstance(x, list):
            return x
        return [x]

    # -------------------------
    # Determine origin channel (TEK ORIGIN - kritik)
    # -------------------------
    origin = (
        signal_dict.get("origin_channel_id")
        or signal_dict.get("source_channel_id")
        or signal_dict.get("channel_id")
        or signal_dict.get("chat_id")
    )

    origin_id = None
    if origin is not None:
        try:
            origin_id = int(origin)
        except Exception:
            origin_id = None
            logger.warning(f"[PROCESS_SIGNAL_ORIGIN_BAD] origin={origin} -> int'e çevrilemedi")

    # Origin yoksa fallback: channel_ids içinden ilk geçerli id
    if origin_id is None:
        try:
            processed_ids = process_channel_ids(channel_ids)
            origin_id = int(processed_ids[0]) if processed_ids else None
            logger.warning(
                f"[PROCESS_SIGNAL_NO_ORIGIN] origin yok. "
                f"fallback_origin={origin_id} raw_channel_ids={channel_ids}"
            )
        except Exception:
            origin_id = None

    if origin_id is None:
        logger.error("[PROCESS_SIGNAL_NO_ORIGIN] origin_id belirlenemedi -> işlem atlanıyor")
        return []

    allowed_channel_ids = [origin_id]

    # -------------------------
    # Filters (parametreli)
    # -------------------------
    # Senin sisteminde 1 = işlem yapabilir gibi kullanıyorsun (get_user_trading_settings de böyle).
    SUPER_ADMIN_ALLOW_VALUE = 1  # ✅ 1 işlem yapabilir

    # ✅ GÜNCEL: target_exchange varsayılan olarak FİLTRE DEĞİL.
    # Sadece strict_exchange=True ise filtre uygula.
    strict_exchange = bool(signal_dict.get("strict_exchange", False))

    ex_filter_sql = ""
    ex_params = []
    target_ex_norm = (str(target_exchange).lower().strip() if target_exchange else None)

    if strict_exchange and target_ex_norm:
        ex_filter_sql = " AND LOWER(uci.exchange) = LOWER(?) "
        ex_params.append(target_ex_norm)

    base_where_sql = f"""
        ABS(uci.channel_id) = ABS(?)
        AND COALESCE(uci.super_admin_pasif, 0) = ?
        AND LOWER(uci.aktif_pasif) = 'aktif'
        AND (uci.end_date IS NULL OR CAST(uci.end_date AS TIMESTAMP) > CURRENT_TIMESTAMP)
        {ex_filter_sql}
    """.strip()

    base_params = [int(origin_id), int(SUPER_ADMIN_ALLOW_VALUE)] + ex_params

    logger.info(
        f"[PROCESS_OPEN_ROUTE] origin={origin_id} strict_exchange={strict_exchange} "
        f"signal_exchange={target_ex_norm} allowed_channels={allowed_channel_ids}"
    )

    # -------------------------
    # Fast "any user exists" check
    # -------------------------
    active_users_check_query = f"""
        SELECT 1
        FROM user_channel_info uci
        WHERE {base_where_sql}
        LIMIT 1
    """.strip()

    active_user_exists = safe_db_query(active_users_check_query, params=tuple(base_params))
    if active_user_exists is None:
        logger.error("[PROCESS_SIGNAL_DB_ERR] Active user check sorgusu çalışmadı, işlem atlanıyor")
        return []

    if not _rows(active_user_exists):
        logger.warning(
            f"[PROCESS_SIGNAL_SKIP] origin={origin_id} için aktif kullanıcı bulunamadı. "
            f"strict_exchange={strict_exchange} signal_exchange={target_ex_norm}"
        )
        return []

    # -------------------------
    # Fetch users (parametreli)
    # -------------------------
    users_query = f"""
        SELECT
            uci.user_id,
            uci.username,
            LOWER(uci.exchange) as exchange,
            uci.api_key,
            uci.secret_key,
            ays.leverage,
            ays.margin,
            uci.channel_id,
            uci.passphrase,
            uci.second_api_key,
            uci.trading_password,
            ays.maks_emir,
            ays.lot
        FROM user_channel_info uci
        JOIN ayarlar ays
          ON uci.user_id = ays.user_id
         AND LOWER(uci.exchange)=LOWER(ays.exchange)
        WHERE {base_where_sql}
    """.strip()

    try:
        users_raw = safe_db_query(users_query, params=tuple(base_params))
        users = _rows(users_raw)
    except Exception as e:
        logger.error(f"[PROCESS_SIGNAL_USERS_QUERY_ERR] {e}", exc_info=True)
        return []

    if not users:
        logger.warning(f"[PROCESS_SIGNAL_NO_USERS] origin={origin_id} için kullanıcı listesi boş")
        return []

    # -------------------------
    # ✅ DEDUPE: (ABS(channel_id), user_id, exchange)
    # -------------------------
    seen = set()
    deduped = []
    for r in users:
        try:
            uid = int(r[0])
            ex = str(r[2] or "").lower().strip()
            ch = int(r[7])
            key = (abs(ch), uid, ex)
            if key in seen:
                continue
            seen.add(key)
        except Exception:
            pass
        deduped.append(r)

    users = deduped

    # -------------------------
    # Process per-user (fan-out already achieved by DB rows)
    # -------------------------
    processed_users = []

    sym = signal_dict.get("symbol")
    side = (signal_dict.get("position_type") or "").upper().strip()
    try:
        eps = signal_dict.get("entry_points") or []
        entry0 = eps[0] if eps else None
    except Exception:
        entry0 = None

    for row in users:
        try:
            uinfo = {
                "user_id": row[0],
                "username": row[1],
                "exchange": row[2],
                "api_key": row[3],
                "secret_key": row[4],
                "leverage": row[5],
                "margin": row[6],
                "channel_id": row[7],
                "passphrase": row[8],
                "second_api_key": row[9],
                "trading_password": row[10],
                "maks_emir": row[11],
                "lot": row[12],
            }

            ex = str(uinfo.get("exchange") or "").lower().strip()

            api_key = (uinfo.get("api_key") or "").strip()
            secret_key = (uinfo.get("secret_key") or "").strip()
            passphrase = (uinfo.get("passphrase") or "").strip()

            # Credential doğrulama (mevcut davranış korunuyor)
            if ex == "mexc":
                if not passphrase:
                    passphrase = _get_mexc_web_key_safe(int(uinfo["user_id"]))
                    uinfo["passphrase"] = passphrase
                if not passphrase:
                    raise ValueError("MEXC için web_key (passphrase) eksik")

            elif ex == "okx":
                if not api_key or not secret_key or not passphrase:
                    raise ValueError("OKX için api_key/secret_key/passphrase eksik")

            else:
                if not api_key or not secret_key:
                    raise ValueError("API anahtarları eksik (api_key/secret_key)")

            logger.info(
                f"[OPEN_USER_TRY] origin={origin_id} user={uinfo['user_id']} ex={ex} sym={sym} "
                f"side={side} entry0={entry0} channel_id={uinfo.get('channel_id')} "
                f"lev={uinfo.get('leverage')} margin={uinfo.get('margin')} signal_ex={target_ex_norm}"
            )

            result = await process_user_signal(uinfo, signal_dict)
            if result is None:
                result = {"success": False, "message": "process_user_signal returned None"}

            ok = bool(result.get("success", False))
            msg = (result.get("message") or result.get("error") or "").strip()

            logger.info(
                f"[OPEN_USER_RES] origin={origin_id} user={uinfo['user_id']} ex={ex} sym={sym} "
                f"success={ok} msg='{msg}'"
            )

            processed_users.append(
                {
                    "user_id": uinfo["user_id"],
                    "username": uinfo["username"],
                    "channel_id": uinfo["channel_id"],
                    "exchange": ex,
                    "success": ok,
                    "message": msg,
                }
            )

        except Exception as ue:
            logger.error(f"[OPEN_USER_ERR] origin={origin_id} err={ue}", exc_info=True)
            processed_users.append(
                {
                    "user_id": row[0] if len(row) > 0 else None,
                    "username": row[1] if len(row) > 1 else "Bilinmeyen",
                    "channel_id": row[7] if len(row) > 7 else None,
                    "exchange": (row[2] or "").lower() if len(row) > 2 else None,
                    "success": False,
                    "message": str(ue),
                }
            )

    # -------------------------
    # Batch summary
    # -------------------------
    succ = sum(1 for p in processed_users if p.get("success"))

    reason_map = {}
    for p in processed_users:
        if p.get("success"):
            continue
        m = (p.get("message") or "").strip()
        key = m if m else "unknown_fail_no_message"
        reason_map[key] = reason_map.get(key, 0) + 1

    logger.info(
        f"[OPEN_BATCH_RESULT] origin={origin_id} symbol={sym} ok={succ}/{len(processed_users)} "
        f"fail_reasons={reason_map} active_count={len(ACTIVE_OPEN_POSITIONS)} "
        f"signal_exchange={target_ex_norm} strict_exchange={strict_exchange} allowed_channels={allowed_channel_ids}"
    )

    return processed_users


async def handle_external_open_signal(open_sig: dict, context=None) -> Dict[str, Any]:
    """
    Dış kaynaktan gelen OPEN sinyalini alır ve sadece doğru (origin) kanala bağlı
    aktif kullanıcılar için process_signal_for_users'e forward eder.

    Çok kullanıcılı/çok borsalı tasarım:
    - Payload valid ise => accepted=True
    - Hiç kullanıcı yoksa veya herkes fail olursa => bu FATAL değildir.
      opened=0 ile raporlanır.
    """
    try:
        if not isinstance(open_sig, dict) or not open_sig:
            logger.error(f"[EXT_OPEN_INVALID] payload boş/invalid: {open_sig}")
            return {
                "accepted": False,
                "opened_any": False,
                "attempted": 0,
                "opened": 0,
                "results": [],
                "fail_reasons": {"payload_invalid": 1},
                "error": "payload_invalid",
            }

        # -------------------------
        # Helpers
        # -------------------------
        def _to_int_or_none(x):
            try:
                if x is None:
                    return None
                if isinstance(x, bool):
                    return None
                if isinstance(x, str):
                    x = x.strip().strip("'\"")
                    if not x:
                        return None
                return int(x)
            except Exception:
                return None

        def _to_float_or_none(x):
            try:
                if x is None:
                    return None
                if isinstance(x, bool):
                    return None
                if isinstance(x, str):
                    x = x.strip().strip("'\"")
                    if not x:
                        return None
                return float(x)
            except Exception:
                return None

        def _get_allowed_signal_channels_safe() -> list[int]:
            try:
                from config_service import ConfigService
                chans = ConfigService.get("SIGNAL_CHANNEL_IDS", None)
                if chans:
                    out = []
                    for c in chans:
                        ci = _to_int_or_none(c)
                        if ci is not None:
                            out.append(ci)
                    out = sorted(set(out))
                    if out:
                        return out
            except Exception:
                pass

            try:
                from strategies.alarm_strateji import OlimposStrategy
                chans = getattr(OlimposStrategy, "channel_ids", None) or []
                out = []
                for c in chans:
                    ci = _to_int_or_none(c)
                    if ci is not None:
                        out.append(ci)
                out = sorted(set(out))
                return out
            except Exception:
                return []

        # -------------------------
        # Required fields
        # -------------------------
        symbol = open_sig.get("symbol")
        direction = open_sig.get("direction") or open_sig.get("side")
        entry = open_sig.get("entry_price") if "entry_price" in open_sig else open_sig.get("entry")
        stop = open_sig.get("stop_loss") if "stop_loss" in open_sig else open_sig.get("sl")
        targets = open_sig.get("targets") or open_sig.get("take_profits") or open_sig.get("tps") or []
        ex = (open_sig.get("exchange") or open_sig.get("ex") or "").lower().strip()

        missing = []
        if not symbol: missing.append("symbol")
        if not direction: missing.append("direction")
        if entry is None: missing.append("entry_price")
        if stop is None: missing.append("stop_loss")
        if not targets: missing.append("targets")
        if not ex: missing.append("exchange")

        if missing:
            logger.error(f"[EXT_OPEN_INVALID] eksik={missing} payload={open_sig}")
            return {
                "accepted": False,
                "opened_any": False,
                "attempted": 0,
                "opened": 0,
                "results": [],
                "fail_reasons": {"missing_fields": 1},
                "error": f"missing_fields:{','.join(missing)}",
            }

        direction_u = str(direction).upper().strip()
        if direction_u not in ("LONG", "SHORT"):
            logger.error(f"[EXT_OPEN_INVALID] direction invalid: {direction} payload={open_sig}")
            return {
                "accepted": False,
                "opened_any": False,
                "attempted": 0,
                "opened": 0,
                "results": [],
                "fail_reasons": {"bad_direction": 1},
                "error": "bad_direction",
            }

        entry_f = _to_float_or_none(entry)
        stop_f = _to_float_or_none(stop)

        if entry_f is None or entry_f <= 0:
            logger.error(f"[EXT_OPEN_INVALID] entry_price invalid: {entry} payload={open_sig}")
            return {
                "accepted": False,
                "opened_any": False,
                "attempted": 0,
                "opened": 0,
                "results": [],
                "fail_reasons": {"bad_entry": 1},
                "error": "bad_entry",
            }
        if stop_f is None or stop_f <= 0:
            logger.error(f"[EXT_OPEN_INVALID] stop_loss invalid: {stop} payload={open_sig}")
            return {
                "accepted": False,
                "opened_any": False,
                "attempted": 0,
                "opened": 0,
                "results": [],
                "fail_reasons": {"bad_stop": 1},
                "error": "bad_stop",
            }

        tps: list[float] = []
        try:
            if isinstance(targets, (list, tuple)):
                for t in targets:
                    tf = _to_float_or_none(t)
                    if tf is not None and tf > 0:
                        tps.append(tf)
            else:
                tf = _to_float_or_none(targets)
                if tf is not None and tf > 0:
                    tps.append(tf)
        except Exception:
            tps = []

        if not tps:
            logger.error(f"[EXT_OPEN_INVALID] targets parse edilemedi: {targets} payload={open_sig}")
            return {
                "accepted": False,
                "opened_any": False,
                "attempted": 0,
                "opened": 0,
                "results": [],
                "fail_reasons": {"bad_targets": 1},
                "error": "bad_targets",
            }

        logger.info(f"[EXT_OPEN_IN] raw_symbol={symbol} dir={direction_u} entry={entry_f} tp_count={len(tps)} ex={ex}")

        # -------------------------
        # Origin channel resolution
        # -------------------------
        allowed_channels = _get_allowed_signal_channels_safe()
        allowed_set = set(allowed_channels)

        origin_ch = (
            open_sig.get("origin_channel_id")
            or open_sig.get("source_channel_id")
            or open_sig.get("channel_id")
            or open_sig.get("chat_id")
        )
        origin_id = _to_int_or_none(origin_ch)

        strict_origin = False  # Çok kullanıcılı yapı için override daha pratik (sende loglarda da böyle)
        if origin_id is None:
            if strict_origin:
                logger.error(f"[EXT_OPEN_INVALID] origin_channel_id missing payload_keys={list(open_sig.keys())}")
                return {
                    "accepted": False,
                    "opened_any": False,
                    "attempted": 0,
                    "opened": 0,
                    "results": [],
                    "fail_reasons": {"missing_origin": 1},
                    "error": "missing_origin",
                }
            if not allowed_channels:
                logger.error("[EXT_OPEN_INVALID] origin yok ve allowed_channels boş (konfigürasyon eksik)")
                return {
                    "accepted": False,
                    "opened_any": False,
                    "attempted": 0,
                    "opened": 0,
                    "results": [],
                    "fail_reasons": {"no_allowed_channels": 1},
                    "error": "no_allowed_channels",
                }
            logger.warning(f"[EXT_OPEN_ORIGIN_MISSING_OVERRIDE] origin=None -> using_allowed={allowed_channels[0]}")
            origin_id = int(allowed_channels[0])

        if allowed_channels and origin_id not in allowed_set:
            logger.warning(
                f"[EXT_OPEN_ORIGIN_OVERRIDE] got_origin={origin_id} not_in_allowed={allowed_channels} "
                f"-> using_allowed={allowed_channels[0]}"
            )
            origin_id = int(allowed_channels[0])

        channel_ids = [origin_id]

        # -------------------------
        # Build signal_dict
        # -------------------------
        from core.symbol_resolver import normalize_core_symbol
        norm_symbol = normalize_core_symbol(str(symbol))

        signal_dict = {
            "signal_type": "OPEN",
            "symbol": norm_symbol,
            "position_type": direction_u,
            "entry_points": [entry_f],
            "stop_loss": stop_f,
            "take_profits": tps,

            "origin_channel_id": origin_id,
            "source_channel_id": origin_id,
            "channel_id": origin_id,

            "exchange": ex,
            "alarm_id": open_sig.get("alarm_id"),
            "signal_id": open_sig.get("signal_id"),
            "meta": open_sig.get("meta") if isinstance(open_sig.get("meta"), dict) else {},
        }

        logger.info(
            f"[EXT_OPEN_FORWARD] {norm_symbol} {direction_u} -> process_signal_for_users "
            f"origin={origin_id} channels={channel_ids} ex={ex}"
        )

        processed_users = await process_signal_for_users(
            channel_ids=channel_ids,
            signal_dict=signal_dict,
            context=context,
            target_exchange=ex
        )

        # -------------------------
        # Summary (non-fatal)
        # -------------------------
        results = processed_users if isinstance(processed_users, list) else []
        attempted = len(results)
        opened = sum(1 for r in results if isinstance(r, dict) and r.get("success"))

        fail_reasons: Dict[str, int] = {}
        for r in results:
            if not isinstance(r, dict) or r.get("success"):
                continue
            m = (r.get("message") or "unknown_fail").strip()
            fail_reasons[m] = fail_reasons.get(m, 0) + 1

        # ✅ accepted=True: payload işlenmiş demektir; opened=0 olabilir (normal)
        return {
            "accepted": True,
            "opened_any": opened > 0,
            "attempted": attempted,
            "opened": opened,
            "results": results,
            "fail_reasons": fail_reasons,
            "origin_channel_id": origin_id,
            "exchange": ex,
            "symbol": norm_symbol,
            "alarm_id": open_sig.get("alarm_id"),
            "signal_id": open_sig.get("signal_id"),
        }

    except Exception as e:
        logger.error(f"[EXT_OPEN_ERR] {e}", exc_info=True)
        return {
            "accepted": False,
            "opened_any": False,
            "attempted": 0,
            "opened": 0,
            "results": [],
            "fail_reasons": {"exception": 1},
            "error": str(e),
        }


async def handle_external_close_signal(close_sig: dict, context=None):
    try:
        symbol = close_sig.get('symbol')
        direction = close_sig.get('direction')
        last_price = close_sig.get('last_price')

        if not (symbol and direction and last_price):
            logger.error(f"[EXT_CLOSE_INVALID] payload={close_sig}")
            return False

        logger.info(
            f"[EXT_CLOSE_IN] raw_symbol={symbol} dir={direction} "
            f"exit={close_sig.get('exit_type')} last={last_price}"
        )

        channel_ids = close_sig.get('channel_ids')
        if not channel_ids:
            try:
                from strategies.alarm_strateji import OlimposStrategy
                channel_ids = OlimposStrategy.channel_ids
            except Exception as e:
                logging.error(f"Hata: {e}")
                channel_ids = []

        # Optional: dış kaynak close sinyali exchange de gönderebilir (mexc/binance vb.)
        target_exchange = (close_sig.get("exchange") or close_sig.get("ex") or "")
        target_exchange = target_exchange.lower().strip() or None

        signal_dict = {
            'signal_type': 'CLOSE',
            'symbol': symbol.replace('/USDT', 'USDT'),
            'position_type': direction.upper(),
            'close_price': last_price,

            # İstersen journaling/debug için:
            'exit_type': close_sig.get('exit_type'),
            'exit_reason': close_sig.get('reason') or close_sig.get('exit_reason'),
        }

        logger.info(
            f"[EXT_CLOSE_FORWARD] {symbol} {direction} {close_sig.get('exit_type')} "
            f"-> process_close_signal_for_users target_exchange={target_exchange}"
        )
        signal_dict["source_channel_id"] = channel_ids[0] if channel_ids else None

        await process_close_signal_for_users(
            channel_ids=channel_ids,
            signal_dict=signal_dict,
            context=context,
            target_exchange=target_exchange
        )
        return True

    except Exception as e:
        logger.error(f"[EXT_CLOSE_ERR] {e}", exc_info=True)
        return False


async def process_user_signal(user: dict, signal_dict: dict) -> dict:
    """
    Tek bir kullanıcı için sinyal işleme (PLAN-ONLY).

    OPEN:
      - settings zorunlu
      - plan zorunlu (build_plan_compat)
      - executor zorunlu (exchange_execute_plan)
      - gate + kill-switch (entry) uygulanır
      - başarı -> ACTIVE_OPEN_POSITIONS + OPENED event

    CLOSE:
      - settings zorunlu (tutarlılık için)
      - plan zorunlu (state'ten ClosePlan üretilir; build_execution_plan CLOSE desteklemese bile)
      - executor zorunlu (exchange_execute_plan)
      - kill-switch CLOSE'u engellemez (exit serbest)
      - başarı -> ACTIVE_OPEN_POSITIONS pop + CLOSED event
    """
    try:
        import time
        from types import SimpleNamespace

        exchange = (user.get("exchange") or "").lower().strip()
        if not exchange:
            return {"success": False, "message": "exchange boş"}

        signal_type = (signal_dict.get("signal_type") or "").upper().strip()
        if signal_type not in ("OPEN", "CLOSE"):
            return {"success": False, "message": f"Bilinmeyen sinyal tipi: {signal_type}"}

        # --- extra security params (bazı borsalarda lazım) ---
        # Not: executor'lar user dict'inden zaten okuyabilir; burada sadece user objesi normalize ediliyor.
        if user.get("passphrase") is not None:
            user["passphrase"] = (user.get("passphrase") or "").strip()
        if user.get("api_key") is not None:
            user["api_key"] = (user.get("api_key") or "").strip()
        if user.get("secret_key") is not None:
            user["secret_key"] = (user.get("secret_key") or "").strip()

        # ------------------------------------------------------------
        # Helpers
        # ------------------------------------------------------------
        EXECUTOR_IMPORTS = {
            "mexc": ("settings.execution.mexc_al_sat", "mexc_execute_plan"),
            "binance": ("settings.execution.binance_al_sat", "binance_execute_plan"),
            "bybit": ("settings.execution.bybit_al_sat", "bybit_execute_plan"),
            "bitget": ("settings.execution.bitget_al_sat", "bitget_execute_plan"),
            "okx": ("settings.execution.okx_al_sat", "okx_execute_plan"),
            "bitmart": ("settings.execution.bitmart_al_sat", "bitmart_execute_plan"),
        }

        def _friendly_plan_build_error(err: Exception) -> str:
            s = str(err or "")

            # LOT_YETERSIZ_%50_CAP: ... min_required≈919.7000 max_allowed≈750.0000 ... (entry=919.7 min_amount=1.0 step=1.0)
            if "LOT_YETERSIZ_%50_CAP" in s:
                def _grab(name: str):
                    m = re.search(rf"{name}=([0-9]+(?:\.[0-9]+)?)", s)
                    return float(m.group(1)) if m else None

                margin_target = _grab("margin_target")
                target_notional = _grab("target_notional")
                entry = _grab("entry")
                min_amount = _grab("min_amount")
                max_allowed = _grab("max_allowed")
                min_required = None

                # min_required≈919.7000 gibi “≈” içeriyor; ayrı yakala
                mreq = re.search(r"min_required[≈~=]?([0-9]+(?:\.[0-9]+)?)", s)
                if mreq:
                    try:
                        min_required = float(mreq.group(1))
                    except Exception:
                        min_required = None

                parts = ["OPEN plan oluşturulamadı (minimum lot / risk cap çakışması)."]
                if entry is not None:
                    parts.append(f"Entry≈{entry:.4f}")
                if min_amount is not None:
                    parts.append(f"Min lot={min_amount:g}")
                if min_required is not None:
                    parts.append(f"Min notional≈{min_required:.2f} USDT")
                if max_allowed is not None:
                    parts.append(f"Max allowed≈{max_allowed:.2f} USDT")
                if target_notional is not None:
                    parts.append(f"Target notional={target_notional:.2f} USDT")
                if margin_target is not None:
                    parts.append(f"Margin target={margin_target:.2f} USDT")

                parts.append("Çözüm: lot/entry_amount artır veya bu sembolü auto-trade dışı bırak.")
                return " | ".join(parts)

            # Diğer hatalar: ham mesajı kısalt
            return (s[:220] + "…") if len(s) > 220 else s

        def _get_plan_executor(ex: str) -> Optional[PlanExecutor]:
            ex = (ex or "").lower().strip()
            spec = EXECUTOR_IMPORTS.get(ex)
            if not spec:
                return None
            module_path, fn_name = spec
            try:
                mod = __import__(module_path, fromlist=[fn_name])
                fn = getattr(mod, fn_name, None)
                return fn if callable(fn) else None
            except Exception as e:
                logger.error(
                    f"[EXECUTOR_IMPORT_ERR] ex={ex} module={module_path} fn={fn_name} err={e}",
                    exc_info=True
                )
                return None

        def _plan_to_dict(plan_obj) -> dict:
            # log/debug/journaling için
            try:
                from dataclasses import asdict
                return asdict(plan_obj)
            except Exception:
                d: Dict[str, Any] = {}
                for k in (
                    "action",
                    "user_id", "username", "exchange",
                    "symbol_core", "symbol_exchange",
                    "side",
                    "entry_price", "exit_price",
                    "signal_stop_loss", "signal_take_profits",
                    "close_price",
                    "leverage", "margin",
                    "lot_notional", "contracts",
                    "position_id",
                    "sl_tp_emir", "sl_price", "tp_structs",
                    "terial_stop", "maliyet_cek",
                    "trailing_mode", "trailing_param",
                    "debug"
                ):
                    d[k] = getattr(plan_obj, k, None)

                meta = getattr(plan_obj, "meta", None)
                if meta:
                    d["meta"] = {
                        "price_step": getattr(meta, "price_step", None),
                        "amount_step": getattr(meta, "amount_step", None),
                        "price_decimals": getattr(meta, "price_decimals", None),
                        "amount_decimals": getattr(meta, "amount_decimals", None),
                        "min_amount": getattr(meta, "min_amount", None),
                        "ccxt_symbol": getattr(meta, "ccxt_symbol", None),
                    }
                return d

        def _plan_to_summary(plan_obj) -> dict:
            d = _plan_to_dict(plan_obj)

            tp_structs = d.get("tp_structs") or []
            tp_prices: List[float] = []
            tp_fracs: List[float] = []

            for t in tp_structs:
                if not isinstance(t, dict):
                    continue
                p = t.get("price") or t.get("tp_price")
                f = t.get("close_frac") or t.get("fraction") or t.get("qty_frac") or t.get("percent")

                if p is not None:
                    try:
                        tp_prices.append(float(p))
                    except Exception:
                        pass
                if f is not None:
                    try:
                        fv = float(f)
                        if fv > 1.0:
                            fv = fv / 100.0
                        tp_fracs.append(fv)
                    except Exception:
                        pass

            ssum = sum(tp_fracs) if tp_fracs else 0.0
            if ssum > 0:
                tp_fracs = [x / ssum for x in tp_fracs]

            return {
                "tp_prices": tp_prices,
                "tp_fractions": tp_fracs,
                "sl_price": d.get("sl_price"),
                "signal_stop_loss": d.get("signal_stop_loss"),
                "exit_price": d.get("exit_price") or d.get("close_price"),
                "trailing_mode": d.get("trailing_mode"),
                "trailing_param": d.get("trailing_param"),
                "terial_stop": d.get("terial_stop"),
                "maliyet_cek": d.get("maliyet_cek"),
                "fees": (d.get("debug") or {}).get("fees") if isinstance(d.get("debug"), dict) else None,
                "meta": (d.get("meta") or {}),
            }

        def _validate_open_plan(plan_obj) -> Optional[str]:
            sym_core = getattr(plan_obj, "symbol_core", None)
            sym_ex = getattr(plan_obj, "symbol_exchange", None)
            side = getattr(plan_obj, "side", None)
            entry_price = getattr(plan_obj, "entry_price", None)

            if not sym_core:
                return "plan_invalid: symbol_core boş"
            if not sym_ex:
                return "plan_invalid: symbol_exchange boş"
            if not side:
                return "plan_invalid: side boş"
            if entry_price is None:
                return "plan_invalid: entry_price boş"

            contracts = getattr(plan_obj, "contracts", None)
            lot_notional = getattr(plan_obj, "lot_notional", None)
            logger.info(
                f"[EXECUTOR_CALL_OPEN] ex={exchange} user={user.get('user_id')} "
                f"sym={getattr(plan_obj, 'symbol_exchange', None)}"
            )

            try:
                if contracts is not None and float(contracts) > 0:
                    return None
            except Exception:
                pass

            try:
                if lot_notional is not None and float(lot_notional) > 0:
                    return None
            except Exception:
                pass

            return "plan_invalid: contracts/lot_notional boş veya 0"

        def _validate_close_plan(plan_obj) -> Optional[str]:
            sym_core = getattr(plan_obj, "symbol_core", None)
            sym_ex = getattr(plan_obj, "symbol_exchange", None)
            side = getattr(plan_obj, "side", None)

            if not sym_core:
                return "close_plan_invalid: symbol_core boş"
            if not sym_ex:
                return "close_plan_invalid: symbol_exchange boş"
            if not side:
                return "close_plan_invalid: side boş"

            # exit price opsiyonel: bazı borsalar market close yapar, fiyat sadece log içindir.
            return None

        def _build_close_plan_from_state(
            *,
            user_id: int,
            username: str,
            exchange: str,
            signal_dict: dict
        ):
            """
            CLOSE planı ACTIVE_OPEN_POSITIONS kaydından üretilir.
            Bu sayede CLOSE her zaman 'açan planın' gerçek sembolü/position_id'si ile kapanır.
            """
            sym_in = signal_dict.get("symbol")
            if not sym_in:
                return None, "close_plan_build_err: signal symbol boş"

            symbol_core_key = normalize_symbol_for_key(sym_in)
            key = (int(user_id), exchange, symbol_core_key)
            st = ACTIVE_OPEN_POSITIONS.get(key)
            if not st:
                return None, f"close_plan_build_err: ACTIVE_OPEN_POSITIONS kaydı yok key={key}"

            # mümkün olduğunca state'ten oku (plan sadakati)
            position_id = st.get("positionId")
            symbol_exchange = st.get("symbol_exchange")

            # state'te yoksa son çare: core'dan üret
            if not symbol_exchange:
                # build_execution_plan'in ürettiği standarda göre exchange'e çevirmeyi tercih et
                # burada sadece mexc özel case:
                if exchange == "mexc" and symbol_core_key.endswith("USDT"):
                    symbol_exchange = f"{symbol_core_key[:-4]}_USDT"
                else:
                    symbol_exchange = symbol_core_key

            side = st.get("side") or (signal_dict.get("position_type") or "").upper()
            exit_price = signal_dict.get("close_price")

            # ClosePlan: executor attribute erişimi yapabilsin diye SimpleNamespace
            plan = SimpleNamespace(
                action="CLOSE",
                user_id=int(user_id),
                username=username,
                exchange=exchange,
                symbol_core=symbol_core_key,
                symbol_exchange=symbol_exchange,
                side=side,
                exit_price=exit_price,
                close_price=exit_price,
                position_id=position_id,

                # state'ten (log/journal için)
                leverage=st.get("leverage"),
                margin=st.get("margin"),
                lot_notional=st.get("lot_notional"),
                contracts=st.get("contracts"),
                debug={"from_state": True}
            )

            # plan_summary'yi de ekleyelim ki CLOSED event'te TP/SL bilgisi korunabilsin
            plan_summary = st.get("plan_summary") or {}
            return plan, None

        # ------------------------------------------------------------
        # Executor + Settings (ikisi de zorunlu)
        # ------------------------------------------------------------
        executor = _get_plan_executor(exchange)
        if executor is None:
            return {"success":False, "message":f"PLAN-ONLY: executor yok ex={exchange}"}

        if not inspect.iscoroutinefunction(executor):
            return {"success":False, "message":f"PLAN-ONLY: executor async değil ex={exchange}"}

        try:
            settings = get_user_settings(user.get("user_id"), exchange)
        except Exception as e:
            logger.error(
                f"[GET_USER_SETTINGS_ERR] user={user.get('user_id')} ex={exchange} err={e}",
                exc_info=True
            )
            settings = None

        if not settings:
            return {"success": False, "message": f"PLAN-ONLY: settings yok ex={exchange}"}

        # =========================
        # OPEN (PLAN-ONLY)
        # =========================
        if signal_type == "OPEN":
            # plan build (tek kaynak)
            try:
                plan = build_plan_compat(
                    user=user,
                    signal_dict=signal_dict,
                    settings=settings,
                    exchange=exchange
                )
            except Exception as pe:
                friendly = _friendly_plan_build_error(pe)
                logger.error(f"[PLAN_BUILD_ERR][OPEN] ex={exchange} err={pe}", exc_info=True)
                return {"success":False, "message":f"OPEN plan_build_err ex={exchange} | {friendly}"}

            if plan is None:
                return {"success": False, "message": f"OPEN plan None ex={exchange}"}

            why_invalid = _validate_open_plan(plan)
            if why_invalid:
                logger.error(f"[PLAN_INVALID][OPEN] ex={exchange} why={why_invalid}")
                return {"success": False, "message": f"OPEN {why_invalid}"}

            # Gate (maks_emir vb.)
            maks_emir_val = settings.get("maks_emir", user.get("maks_emir"))
            allow, reason = can_open_position(
                plan=plan,
                active_open_positions=ACTIVE_OPEN_POSITIONS,
                maks_emir=maks_emir_val
            )
            if not allow:
                return {"success": False, "message": f"open_blocked: {reason}"}

            # Kill-switch: sadece entry engel
            allow_entry, why = can_open_entry()
            if not allow_entry:
                return {"success": False, "message": f"kill_switch_blocked: {why}"}

            # EXECUTE (retry + timeout)
            max_retries = 3
            timeout_seconds = 30
            result = None

            async with USER_TRADE_SEMAPHORE:
                for attempt in range(max_retries):
                    try:
                        result = await asyncio.wait_for(executor(plan, user), timeout=timeout_seconds)
                        if result is not None:
                            break
                        if attempt < max_retries - 1:
                            await asyncio.sleep(1)
                    except asyncio.TimeoutError:
                        logger.warning(
                            f"[OPEN_EXEC_TIMEOUT] ex={exchange} user={user.get('username','UNKNOWN')} "
                            f"attempt={attempt + 1}/{max_retries}"
                        )
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2)
                    except Exception as call_error:
                        logger.error(
                            f"[OPEN_EXEC_ERR] ex={exchange} user={user.get('username','UNKNOWN')} "
                            f"attempt={attempt + 1}/{max_retries} err={call_error}",
                            exc_info=True
                        )
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2)

            if result is None:
                return {"success": False, "message": f"OPEN executor_result None after {max_retries} retries"}

            if not bool(result.get("success")):
                err = (result.get("error") or result.get("message") or "open_failed")
                err = str(err)
                if len(err) > 240:
                    err = err[:240] + "…"
                return {"success":False, "message":err}

            # plan debug ekleri
            try:
                result["execution_plan"] = _plan_to_dict(plan)
                result["execution_plan_summary"] = _plan_to_summary(plan)
            except Exception:
                pass

            position_id = result.get("position_id") or (result.get("data") or {}).get("positionId")

            # SADECE position_id varsa state + event
            if position_id:
                plan_summary = result.get("execution_plan_summary") or _plan_to_summary(plan)
                plan_symbol = getattr(plan, "symbol_core", None) or signal_dict.get("symbol")
                norm_symbol_key = normalize_symbol_for_key(plan_symbol)

                ACTIVE_OPEN_POSITIONS[(int(user["user_id"]), exchange, norm_symbol_key)] = {
                    "side": getattr(plan, "side", (signal_dict.get("position_type") or "").upper()),
                    "opened_at": time.time(),
                    "positionId": position_id,
                    "plan_summary": plan_summary,
                    "signal_id": signal_dict.get("signal_id"),
                    "alarm_id": signal_dict.get("alarm_id"),
                    "entry_price": getattr(plan, "entry_price", (signal_dict.get("entry_points") or [None])[0]),
                    "symbol_exchange": getattr(plan, "symbol_exchange", None),
                    "leverage": getattr(plan, "leverage", user.get("leverage")),
                    "margin": getattr(plan, "margin", user.get("margin")),
                    "lot_notional": getattr(plan, "lot_notional", user.get("lot")),
                    "contracts": getattr(plan, "contracts", None),
                }
                # ✅ MEXC TRAILING OWNER MAP (UnifiedTrailingSupervisor için şart)
                try:
                    if exchange == "mexc":
                        pid_owner = int(position_id)
                        # plan_symbol zaten core (BTCUSDT)
                        TRAILING_POSITION_OWNERS_MEXC[pid_owner] = {
                            "user_id":int(user["user_id"]),
                            "exchange":"mexc",
                            "side":str(getattr(plan, "side", (signal_dict.get("position_type") or "LONG"))).upper(),
                            "symbol_core":norm_symbol_key,  # BTCUSDT
                            "symbol_exchange":getattr(plan, "symbol_exchange", None),  # BTC_USDT
                        }
                        logger.info(f"[MEXC_OWNER_SET] pid={pid_owner} user={user['user_id']} sym={norm_symbol_key}")
                except Exception as e:
                    logger.warning(f"[MEXC_OWNER_SET_ERR] pid={position_id} err={e}", exc_info=True)

                # ✅ UNIFIED TRAILING REGISTER
                try:
                    pid_int = int(position_id)
                except Exception:
                    pid_int = None

                if pid_int:
                    # plan'dan trailing bilgilerini yakala (loglarda terial_stop="TRAILING_2.0" görünüyor)
                    plan_terial_stop = getattr(plan, "terial_stop", None)
                    plan_trailing_mode = getattr(plan, "trailing_mode", None)
                    plan_trailing_param = getattr(plan, "trailing_param", None)

                    trailing_state = {
                        "exchange":exchange,
                        "symbol":plan_symbol,  # core symbol
                        "side":getattr(plan, "side", (signal_dict.get("position_type") or "").upper()),
                        "entry_price":getattr(plan, "entry_price", (signal_dict.get("entry_points") or [None])[0]),
                        "stop_loss_initial":(
                                plan_summary.get("signal_stop_loss")
                                or plan_summary.get("sl_price")
                                or signal_dict.get("stop_loss")
                        ),
                        "created_at":time.time(),

                        # ✅ TrailingManager API uyumu için ek alanlar
                        "terial_stop":plan_terial_stop,
                        "trailing_mode":plan_trailing_mode,
                        "trailing_param":plan_trailing_param,
                    }
                    terial = str(trailing_state.get("terial_stop") or "").strip().upper()
                    tmode = trailing_state.get("trailing_mode")
                    tparam = trailing_state.get("trailing_param")

                    if terial not in ("KAPALI", "OFF", "NONE", "") or (tmode and tparam is not None):
                        ok_reg = _safe_trailing_register(pid_int, trailing_state)
                    else:
                        ok_reg = True  # trailing kapalı => no-op
                    logger.info(
                        f"[TRAILING_REGISTER] pid={pid_int} ex={exchange} sym={plan_symbol} "
                        f"side={trailing_state.get('side')} terial_stop={plan_terial_stop} "
                        f"trailing_mode={plan_trailing_mode} trailing_param={plan_trailing_param} ok={ok_reg}"
                    )

                append_user_trade_event(int(user["user_id"]), exchange, {
                    "event": "OPENED",
                    "symbol_core": plan_symbol,
                    "symbol_exchange": getattr(plan, "symbol_exchange", None),
                    "side": getattr(plan, "side", (signal_dict.get("position_type") or "").upper()),
                    "entry_price": getattr(plan, "entry_price", (signal_dict.get("entry_points") or [None])[0]),
                    "stop_price_initial": plan_summary.get("signal_stop_loss") or plan_summary.get("sl_price"),
                    "tp_prices": plan_summary.get("tp_prices"),
                    "tp_fractions": plan_summary.get("tp_fractions"),
                    "leverage": getattr(plan, "leverage", user.get("leverage")),
                    "margin": getattr(plan, "margin", user.get("margin")),
                    "lot_notional": getattr(plan, "lot_notional", user.get("lot")),
                    "contracts": getattr(plan, "contracts", None),
                    "alarm_id": signal_dict.get("alarm_id"),
                    "signal_id": signal_dict.get("signal_id"),
                    "position_id": position_id,
                })

            return result

        # =========================
        # CLOSE (PLAN-ONLY)
        # =========================
        # Kill-switch CLOSE'u engellemez (exit serbest) -> burada can_open_entry çağırmıyoruz
        # Close planı state'ten üret (açan planın symbol_exchange / positionId'sini kullan)
        close_plan, cerr = _build_close_plan_from_state(
            user_id=int(user.get("user_id")),
            username=user.get("username", "UNKNOWN"),
            exchange=exchange,
            signal_dict=signal_dict
        )
        if close_plan is None:
            return {"success": False, "message": cerr or f"CLOSE plan_build_failed ex={exchange}"}

        why_invalid = _validate_close_plan(close_plan)
        if why_invalid:
            logger.error(f"[PLAN_INVALID][CLOSE] ex={exchange} why={why_invalid}")
            return {"success": False, "message": f"CLOSE {why_invalid}"}

        # EXECUTE (retry + timeout)
        max_retries = 3
        timeout_seconds = 30
        result = None

        async with USER_TRADE_SEMAPHORE:
            for attempt in range(max_retries):
                try:
                    result = await asyncio.wait_for(executor(close_plan, user), timeout=timeout_seconds)
                    if result is not None:
                        break
                    if attempt < max_retries - 1:
                        await asyncio.sleep(1)
                except asyncio.TimeoutError:
                    logger.warning(
                        f"[CLOSE_EXEC_TIMEOUT] ex={exchange} user={user.get('username','UNKNOWN')} "
                        f"attempt={attempt + 1}/{max_retries}"
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2)
                except Exception as call_error:
                    logger.error(
                        f"[CLOSE_EXEC_ERR] ex={exchange} user={user.get('username','UNKNOWN')} "
                        f"attempt={attempt + 1}/{max_retries} err={call_error}",
                        exc_info=True
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2)

        if result is None:
            return {"success": False, "message": f"CLOSE executor_result None after {max_retries} retries"}

        if not bool(result.get("success")):
            return {"success": False, "message": result.get("error", "close_failed")}

        # state + CLOSED event
        symbol_core_key = getattr(close_plan, "symbol_core", None) or normalize_symbol_for_key(signal_dict.get("symbol"))
        key = (int(user["user_id"]), exchange, symbol_core_key)
        current_state = ACTIVE_OPEN_POSITIONS.get(key, {}) or {}
        plan_summary = current_state.get("plan_summary") or {}

        close_price = signal_dict.get("close_price")
        try:
            append_user_trade_event(int(user["user_id"]), exchange, {
                "event": "CLOSED",
                "symbol_core": symbol_core_key,
                "side": getattr(close_plan, "side", (signal_dict.get("position_type") or "").upper()),
                "exit_price": close_price,
                "exit_type": result.get("exit_type") or "MANUAL",
                "exit_subtype": result.get("exit_subtype"),
                "position_id": current_state.get("positionId") or getattr(close_plan, "position_id", None),

                "tp_fractions": plan_summary.get("tp_fractions"),
                "tp_prices": plan_summary.get("tp_prices"),
                "stop_price_effective": plan_summary.get("sl_price"),

                "leverage": current_state.get("leverage"),
                "margin": current_state.get("margin"),
                "lot_notional": current_state.get("lot_notional"),
                "contracts": current_state.get("contracts"),
                "alarm_id": current_state.get("alarm_id"),
                "signal_id": current_state.get("signal_id"),

                "success": bool(result.get("success")),
                "realized_net_pct": result.get("realized_net_pct"),
                "realized_gross_pct": result.get("realized_gross_pct"),
                "realized_fees_pct": result.get("realized_fees_pct"),
            })
        except Exception as re1:
            logger.warning(f"[USER_TRADE_CLOSE_LOG_WARN] user={user.get('user_id')} ex={exchange} err={re1}")

        # başarılıysa state temizle
        if bool(result.get("success")):
            # önce state'ten pid al
            pid_to_remove = current_state.get("positionId") or getattr(close_plan, "position_id", None)
            try:
                if pid_to_remove:
                    _safe_trailing_unregister(int(pid_to_remove))
                    logger.info(f"[TRAILING_UNREGISTER] pid={pid_to_remove} ex={exchange} sym={symbol_core_key}")
            except Exception as e:
                logger.warning(f"[TRAILING_UNREGISTER_WARN] pid={pid_to_remove} err={e}")

            ACTIVE_OPEN_POSITIONS.pop(key, None)

        # debug attach
        try:
            result["execution_plan"] = _plan_to_dict(close_plan)
            result["execution_plan_summary"] = _plan_to_summary(close_plan)
        except Exception:
            pass

        return result

    except Exception as error1:
        msg = f"{user.get('username', 'UNKNOWN')} için sinyal işleme hatası: {error1}"
        logger.error(msg, exc_info=True)
        return {"success": False, "message": msg}


async def process_close_signal_for_users(channel_ids, signal_dict, context=None, target_exchange: str | None = None):
    """
    CLOSE sinyallerini batch işler (PLAN-ONLY CLOSE).

    KURAL:
    - Kapatma denemesi SADECE ACTIVE_OPEN_POSITIONS’ta o (user_id, exchange, symbol) için state varsa yapılır.
    - Kapatma işlemi process_user_signal(..., CLOSE) üzerinden gider (plan-only).
    - Böylece "tüm borsalarda kör kapatmayı deneme" biter.
    """
    _ = context
    logger.info("[CLOSE_BATCH_START]")

    if not channel_ids or not signal_dict:
        logger.error("[CLOSE_BATCH_INVALID] channel_ids veya signal_dict boş")
        return []

    if (signal_dict.get('signal_type') or '').upper() != 'CLOSE':
        logger.warning("[CLOSE_BATCH_SKIP] signal_type CLOSE değil -> atlandı")
        return []

    sym_in = signal_dict.get('symbol')
    if not sym_in:
        logger.error("[CLOSE_BATCH_INVALID] symbol boş")
        return []

    processed_ids = process_channel_ids(channel_ids)
    if not processed_ids:
        logger.warning("[CLOSE_BATCH_SKIP] processed channel id listesi boş")
        return []

    # CLOSE tarafında da kanal bazlı user listesine ihtiyacımız var,
    # çünkü user credentials (api_key/secret/passphrase/leverage/margin/lot...) buradan geliyor.
    users = await get_user_trading_settings(processed_ids)
    if not users:
        logger.warning("[CLOSE_BATCH_SKIP] kapatma için kullanıcı yok")
        return []

    norm_symbol_key = normalize_symbol_for_key(sym_in)
    side = (signal_dict.get('position_type') or '').upper().strip()
    close_price = signal_dict.get('close_price')

    # target_exchange normalize
    target_ex = (target_exchange or "").lower().strip() if target_exchange else None

    processed_users: List[Dict[str, Any]] = []

    # yardımcı: get_user_trading_settings çıktısını normalize et
    # beklenen kolonlar:
    # 0 user_id, 1 username, 2 exchange, 3 api_key, 4 secret_key,
    # 5 leverage, 6 margin, 7 lot, 8 maks_emir, 9 channel_id, 10 passphrase
    for user_row in (users or []):
        try:
            user_id = int(user_row[0])
            username = user_row[1]
            exchange = str(user_row[2] or "").lower().strip()

            if not exchange:
                processed_users.append({
                    "user_id": user_id,
                    "username": username,
                    "exchange": None,
                    "success": False,
                    "message": "close_skip: exchange boş"
                })
                continue

            # Eğer dışarıdan exchange hedefi geldiyse, sadece o exchange
            if target_ex and exchange != target_ex:
                continue

            # ✅ En kritik filtre: state yoksa kapatma deneme
            key = (user_id, exchange, norm_symbol_key)
            current_state = ACTIVE_OPEN_POSITIONS.get(key, {}) or {}
            if not current_state:
                # Gürültüyü azaltmak için INFO yeterli
                logger.info(f"[CLOSE_SKIP_NO_STATE] user={user_id} ex={exchange} sym={norm_symbol_key}")
                continue

            api_key = (user_row[3] or "").strip() if len(user_row) > 3 and user_row[3] else ""
            secret_key = (user_row[4] or "").strip() if len(user_row) > 4 and user_row[4] else ""
            passphrase = (user_row[10] or "").strip() if len(user_row) > 10 and user_row[10] else ""

            # OPEN ile aynı credential doğrulamaları (tutarlılık)
            if exchange == "mexc":
                if not passphrase:
                    passphrase = _get_mexc_web_key_safe(int(user_id))
                if not passphrase:
                    raise ValueError("MEXC için web_key (passphrase) eksik")
            elif exchange == "okx":
                if not api_key or not secret_key or not passphrase:
                    raise ValueError("OKX için api_key/secret_key/passphrase eksik")
            else:
                if not api_key or not secret_key:
                    raise ValueError("API anahtarları eksik (api_key/secret_key)")

            # process_user_signal'ın beklediği user dict formatına çevir
            uinfo = {
                "user_id": user_id,
                "username": username,
                "exchange": exchange,
                "api_key": api_key,
                "secret_key": secret_key,
                "passphrase": passphrase,

                # plan tarafında/journaling’de kullanılabilir
                "leverage": user_row[5] if len(user_row) > 5 else None,
                "margin": user_row[6] if len(user_row) > 6 else None,
                "lot": user_row[7] if len(user_row) > 7 else None,
                "maks_emir": user_row[8] if len(user_row) > 8 else None,
                "channel_id": user_row[9] if len(user_row) > 9 else None,
            }

            logger.info(
                f"[CLOSE_USER_TRY][PLAN_ONLY] user={user_id} ex={exchange} sym={norm_symbol_key} "
                f"side={side} close_price={close_price} positionId={current_state.get('positionId')}"
            )

            # ✅ Tek yürütme noktası: plan-only close
            async with USER_TRADE_SEMAPHORE:
                result = await process_user_signal(uinfo, signal_dict)

            if result is None:
                result = {"success": False, "message": "process_user_signal returned None"}

            ok = bool(result.get("success", False))
            msg = (result.get("message") or result.get("error") or "").strip()

            logger.info(
                f"[CLOSE_USER_RES][PLAN_ONLY] user={user_id} ex={exchange} sym={norm_symbol_key} "
                f"success={ok} msg='{msg}'"
            )

            processed_users.append({
                "user_id": user_id,
                "username": username,
                "exchange": exchange,
                "success": ok,
                "message": msg,
            })

        except Exception as e:
            # Bu kullanıcı için close atlanır/hata
            uid = None
            uname = "Bilinmeyen"
            exv = None
            try:
                uid = int(user_row[0]) if user_row and len(user_row) > 0 else None
                uname = user_row[1] if user_row and len(user_row) > 1 else "Bilinmeyen"
                exv = (user_row[2] or "").lower() if user_row and len(user_row) > 2 else None
            except Exception:
                pass

            logger.error(f"[CLOSE_USER_ERR][PLAN_ONLY] user={uid} ex={exv} err={e}", exc_info=True)
            processed_users.append({
                "user_id": uid,
                "username": uname,
                "exchange": exv,
                "success": False,
                "message": str(e),
            })

    succ = sum(1 for p in processed_users if p.get("success"))
    logger.info(
        f"[CLOSE_BATCH_RESULT][PLAN_ONLY] symbol={norm_symbol_key} ok={succ}/{len(processed_users)} "
        f"remaining_active={len(ACTIVE_OPEN_POSITIONS)} target_exchange={target_ex}"
    )
    return processed_users


def process_channel_ids(channel_ids):
    out = []
    if not isinstance(channel_ids, list):
        channel_ids = [channel_ids]

    for cid in channel_ids:
        try:
            if cid is None:
                continue

            if isinstance(cid, str):
                cid = cid.strip().strip("'\"").replace(" ", "")
                if not cid:
                    continue

            val = int(cid)

            # Telegram chat id formatında "işaret kayboldu" bug'ı: dev pozitifleri negatife çek
            if val > 10**10:
                fixed = -abs(val)
                logger.warning(f"[CHANNEL_ID_POSITIVE_FIX] cid={val} -> {fixed}")
                val = fixed

            out.append(val)

        except Exception as e:
            logger.error(f"Channel ID dönüşüm hatası: {cid} - {e}", exc_info=True)

    return sorted(set(out))


async def get_user_trading_settings(channel_ids):
    logger.info("[GET_USER_TRADING_SETTINGS] start")

    processed_ids = process_channel_ids(channel_ids)
    if not processed_ids:
        logger.warning("[GET_USER_TRADING_SETTINGS] processed_ids boş")
        return []

    # IN için placeholder üret
    placeholders = ",".join(["?"] * len(processed_ids))

    query = f"""
        SELECT 
            uci.user_id,
            uci.username,
            LOWER(uci.exchange) as exchange,
            uci.api_key,
            uci.secret_key,
            ays.leverage,
            ays.margin,
            ays.lot,
            ays.maks_emir,
            uci.channel_id,
            uci.passphrase
        FROM user_channel_info uci
        JOIN ayarlar ays 
          ON uci.user_id = ays.user_id 
         AND LOWER(uci.exchange) = LOWER(ays.exchange)
        WHERE
            ABS(uci.channel_id) IN ({placeholders})
          AND LOWER(uci.aktif_pasif)='aktif'
          AND COALESCE(uci.super_admin_pasif, 0) = 1
          AND (uci.end_date IS NULL OR CAST(uci.end_date AS TIMESTAMP) > CURRENT_TIMESTAMP)
    """

    try:
        users = safe_db_query(query, params=tuple(processed_ids), fetch=True, fetch_all=True)
        return users or []
    except Exception as e:
        logger.error(f"[GET_USER_TRADING_SETTINGS_ERR] {e}", exc_info=True)
        return []


def safe_db_query(query, params=None, operation='select', fetch=True, fetch_all: bool = True):
    """
    db_operation wrapper.
    - fetch_all=True => tüm satırlar (list) beklenir
    - fetch_all=False => tek satır (tuple/row) beklenir
    """
    try:
        return db_operation(
            query,
            params or (),
            operation=operation,
            fetch=fetch,
            fetch_all=fetch_all
        )
    except Exception as error9:
        logger.error(f"Veritabanı sorgu hatası: {error9}", exc_info=True)
        logger.error(f"Sorgu: {query}")
        logger.error(f"Parametreler: {params}")
        return None


def calculate_entry_prices(
        current_price: float,
        entry_range: float,
        position_type: str
) -> List[float]:
    range_multiplier = entry_range / 100
    lower_price = current_price * (1 - range_multiplier)
    upper_price = current_price * (1 + range_multiplier)
    entry_price = (lower_price + upper_price) / 2
    return [auto_round_price(entry_price)]


def calculate_stop_loss(
        current_price: Union[float, str],
        sl_percent: Union[float, str],
        position_type: str
) -> float:
    try:
        current_price = float(current_price)
        sl_percent = float(sl_percent)

        if position_type.upper() == 'LONG':
            stop_loss = current_price * (1 - (sl_percent / 100))
        elif position_type.upper() == 'SHORT':
            stop_loss = current_price * (1 + (sl_percent / 100))
        else:
            raise ValueError(f"Geçersiz pozisyon tipi: {position_type}")

        return auto_round_price(stop_loss)

    except Exception as error10:
        logger.error(f"Stop loss hesaplama hatası: {error10}")
        logger.error(f"Parametreler - Current Price: {current_price}, SL %: {sl_percent}, Tip: {position_type}")
        raise ValueError(f"Stop loss hesaplanamadı: {str(error10)}")

def calculate_take_profits(
        current_price: float,
        tp_count: int,
        tp_range: float,
        position_type: str
) -> List[float]:
    take_profits = []
    range_multiplier = tp_range / 100
    for i in range(tp_count):
        if position_type == 'LONG':
            tp_price = current_price * (1 + (range_multiplier * (i + 1)))
        else:
            tp_price = current_price * (1 - (range_multiplier * (i + 1)))
        take_profits.append(auto_round_price(tp_price))
    return take_profits

async def is_admin(context, chat_id: int, user_id: int) -> bool:
    try:
        query = "SELECT level FROM admin_users WHERE user_id = ?"
        result = db_operation(query, (user_id,), operation='select', fetch=True)
        if result and result[0] and result[0][0] in [0, 1]:
            return True
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ['creator', 'administrator']
    except Exception as error11:
        logger.error(f"Admin kontrolü hatası: {error11}")
        return False

def format_prices(prices: List[float]) -> str:
    return "\n".join([f"➡️ {auto_round_price_str(price)}" for price in prices])

def format_targets(targets: List[float]) -> str:
    return "\n".join([f"🎯 Hedef {i + 1}: {auto_round_price_str(price)}" for i, price in enumerate(targets)])

def format_results(results: List[dict]) -> str:
    if not results:
        return "   • Henüz işlem yapılmadı"
    formatted_output = []
    for result in results:
        status = '✅ Başarılı' if result.get('success', False) else '❌ Başarısız'
        username = None
        if 'username' in result:
            username = result['username']
        elif 'user_data' in result and isinstance(result['user_data'], dict):
            username = result['user_data'].get('username')
        username = username or 'Bilinmeyen Kullanıcı'
        output_line = f"   • {username}: {status}"
        formatted_output.append(output_line)
    return '\n'.join(formatted_output)

async def signal_handler(update: Update, context):
    try:
        message = update.message or update.channel_post
        if not message:
            logger.error("Mesaj objesi bulunamadı")
            if update.effective_chat:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="❌ Mesaj alınamadı!"
                )
            return
        if not update.effective_chat:
            logger.error("Chat objesi bulunamadı")
            return

        chat_type = update.effective_chat.type
        if chat_type not in ['group', 'supergroup', 'channel']:
            await message.reply_text("❌ Bu komut sadece grup ve kanallarda kullanılabilir!")
            return

        user_id = None
        if chat_type == 'channel':
            try:
                admins = await context.bot.get_chat_administrators(update.effective_chat.id)
                if admins:
                    user_id = admins[0].user.id
            except Exception as admin_error:
                logger.error(f"Kanal yöneticileri alınamadı: {admin_error}")
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="❌ Yetki kontrolü yapılamadı!"
                )
                return
        else:
            if update.effective_user:
                user_id = update.effective_user.id
            elif message.sender_chat:
                user_id = message.sender_chat.id

        if not user_id:
            error_text = "❌ Kullanıcı kimliği belirlenemedi!"
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=error_text
            )
            return

        if not await is_admin(context, update.effective_chat.id, user_id):
            error_text = "❌ Bu komutu kullanma yetkiniz yok!"
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=error_text
            )
            return

        command_parts = message.text.split()

        if len(command_parts) != 8:
            help_text = (
                "❌ Hatalı komut formatı!\n"
                "Doğru format: /signal [open/close] [buy/sell] [symbol] "
                "[sl_yuzde] [entry_aralik_yuzde] [tp_adet] [tp_ler_arasi_yuzde]\n\n"
                "Örnek: /signal open buy BTCUSDT 1 3 5 1"
            )
            if update.channel_post:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=help_text)
            else:
                await message.reply_text(help_text)
            return

        _, signal_type, side, symbol, sl_percent, entry_range, tp_count, tp_range = command_parts

        signal_type = signal_type.upper()
        if signal_type not in ['OPEN', 'CLOSE']:
            raise ValueError("Geçersiz sinyal tipi! 'open' veya 'close' kullanın.")

        side = side.upper()
        if side not in ['BUY', 'SELL']:
            raise ValueError("Geçersiz işlem yönü! 'buy' veya 'sell' kullanın.")

        symbol = symbol.upper()
        if not symbol.endswith('USDT'):
            symbol = f"{symbol}USDT"

        try:
            sl_percent = float(sl_percent)
            entry_range = float(entry_range)
            tp_count = int(tp_count)
            tp_range = float(tp_range)
            if not (0 < sl_percent <= 100):
                raise ValueError("Stop loss yüzdesi 0-100 arasında olmalıdır")
            if not (0 < entry_range <= 100):
                raise ValueError("Giriş aralığı yüzdesi 0-100 arasında olmalıdır")
            if not (0 < tp_count <= 10):
                raise ValueError("Take profit hedef sayısı 1-10 arasında olmalıdır")
            if not (0 < tp_range <= 100):
                raise ValueError("Take profit aralığı yüzdesi 0-100 arasında olmalıdır")
        except (ValueError, TypeError) as val_err:
            error_text = f"❌ Parametre hatası: {str(val_err)}"
            if update.channel_post:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=error_text)
            else:
                await message.reply_text(error_text)
            logger.error(f"Parametre dönüşüm hatası: {val_err}")
            return

        position_type = 'LONG' if side == 'BUY' else 'SHORT'

        try:
            price_data = await get_current_price(symbol)
            if not price_data or price_data['price'] <= 0:
                raise ValueError("Geçersiz fiyat alındı")
            current_price = price_data['price']
            used_symbol = price_data['used_symbol']
            logger.info(f"Mevcut fiyat alındı: {current_price} - {used_symbol}")
        except Exception as price_err:
            error_text = f"❌ Fiyat alınamadı: {str(price_err)}"
            if update.channel_post:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=error_text)
            else:
                await message.reply_text(error_text)
            logger.error(f"Fiyat alma hatası: {price_err}")
            return

        try:
            entry_prices = calculate_entry_prices(current_price, entry_range, position_type)
            logger.info(f"Giriş fiyatları hesaplandı: {entry_prices}")
        except Exception as entry_err:
            error_text = f"❌ Giriş fiyatları hesaplanamadı: {str(entry_err)}"
            if update.channel_post:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=error_text)
            else:
                await message.reply_text(error_text)
            logger.error(f"Giriş fiyatları hesaplama hatası: {entry_err}")
            return

        try:
            stop_loss = calculate_stop_loss(current_price, sl_percent, position_type)
            logger.info(f"Stop loss hesaplandı: {stop_loss}")
        except Exception as sl_err:
            error_text = f"❌ Stop loss hesaplanamadı: {str(sl_err)}"
            if update.channel_post:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=error_text)
            else:
                await message.reply_text(error_text)
            logger.error(f"Stop loss hesaplama hatası: {sl_err}")
            return

        try:
            take_profits = calculate_take_profits(current_price, tp_count, tp_range, position_type)
            logger.info(f"Take profit hedefleri: {take_profits}")
        except Exception as tp_err:
            error_text = f"❌ Take profit hedefleri hesaplanamadı: {str(tp_err)}"
            if update.channel_post:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=error_text)
            else:
                await message.reply_text(error_text)
            logger.error(f"Take profit hesaplama hatası: {tp_err}")
            return

        signal_dict = {
            'signal_type': signal_type,
            'symbol': symbol,
            'used_symbol': used_symbol,
            'position_type': position_type,
            'entry_points': entry_prices,
            'stop_loss': stop_loss,
            'take_profits': take_profits,
            'current_price': current_price
        }

        channel_ids = [update.effective_chat.id]
        results = await process_signal_for_users(channel_ids, signal_dict)

        message_text = create_signal_message(signal_dict, results)

        try:
            if update.channel_post:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=message_text,
                    parse_mode='HTML'
                )
            else:
                await message.reply_text(message_text, parse_mode='HTML')
            logger.info(f"Sinyal mesajı başarıyla gönderildi - Chat ID: {update.effective_chat.id}")
        except Exception as send_err:
            logger.error(f"Mesaj gönderme hatası: {send_err}")
            error_text = "❌ Sinyal mesajı gönderilemedi"
            if update.channel_post:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=error_text)
            else:
                await message.reply_text(error_text)

    except Exception as error12:
        error_message = f"❌ Genel hata oluştu: {str(error12)}"
        try:
            if update.effective_chat:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=error_message
                )
        except Exception as final_err:
            logger.error(f"Son hata mesajı gönderme hatası: {final_err}")
        logger.error(f"Signal handler genel hata: {error12}")
        logger.error(traceback.format_exc())

def create_signal_message(signal_dict: dict, results: List[dict]) -> str:
    message = f"""
📊 <b>{signal_dict['signal_type']} SİNYALİ</b>

🔹 Sembol: {signal_dict['symbol']}
📈 Yön: {signal_dict['position_type']}

💰 Giriş Fiyatları:
{format_prices(signal_dict['entry_points'])}

🛑 Stop Loss: {signal_dict['stop_loss']:.8f}

🎯 Hedefler:
{format_targets(signal_dict['take_profits'])}

👥 İşlem Sonuçları:
{format_results(results)}
"""
    return message