# mexc_al_sat.py

from data.olimpos_data import db_operation
from config.constants import BOT_TOKEN
import json
import time
import hashlib
from typing import List, Dict, Any, Union, Optional
from datetime import datetime
from curl_cffi import requests
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackContext
import ccxt
import logging
import math
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from settings.trailing_manager import TRAILING_MANAGER
import asyncio
from analytics.timeline_hooks import on_sl_move
from settings.execution_plan_types import ExecutionPlan
from core.symbol_resolver import normalize_core_symbol
from settings.execution.symbol_meta_provider import get_symbol_meta as provider_get_symbol_meta


# === DİNAMİK TRAILING OWNER / CLIENT CACHE ===
TRAILING_POSITION_OWNERS: Dict[int, Dict[str, Any]] = {}
_MEXC_CLIENT_CACHE: Dict[int, Any] = {}

# --- MEXC PATCH CONFIG ---
MIN_TP_DIFF_PERCENT = 0.05        # Eski 0.5 yerine
MAX_TP_COUNT = 5                  # Güvenlik
TP_SIDE_MAP = {'LONG': 4, 'SHORT': 2}  # Close side
OPEN_SIDE_MAP = {'LONG': 1, 'SHORT': 3}
MARKET_CACHE_EXPIRE_SECONDS = 300
_last_market_load = 0
_markets = None
OPEN_ORDER_TYPE = 5          # 1 = Limit, 5 = Market
POSITION_WAIT_SECONDS = 10    # Limit açılışta maksimum bekleme
POSITION_POLL_INTERVAL = 0.4  # Saniye

STOPORDER_ENDPOINT = "/stoporder/place/v2"
STOPORDER_CANCEL_ENDPOINT = "/stoporder/cancel/v2"

MEXC_PL_PRICE_PROTECT = "1"
MEXC_PL_PROFIT_LOSS_VOL_TYPE = "SEPARATE"
MEXC_PL_VOL_TYPE = 1
MEXC_PL_TAKE_PROFIT_REVERSE = 2
MEXC_PL_STOP_LOSS_REVERSE = 2
MEXC_PL_DEFAULT_TREND = "1"  # profitTrend / lossTrend
# === STOP ORDER STATE (anti-duplicate) ===
STOP_FETCH_FAILS = {}          # position_id -> ardışık fetch fail sayısı
LAST_PLACED_STOP = {}          # position_id -> {"id": str, "price": float, "ts": float}
REQUIRE_LIST_SUCCESS = True    # True iken: liste yoksa stop güncelleme yok (dupliceyi keser)
MAX_CONSEC_FETCH_FAIL = 2      # Bu kadar ardışık 404 sonra skip
MIN_REPLACE_INTERVAL = 45      # (sn) Aynı pozisyona yeni SL koymadan önce minimum süre (liste
# --- LOT / TP SAFETY CONFIG ---
MAX_NOTIONAL_FACTOR = 8.0          # final_notional hedef_marjın 8 katını aşarsa işlemi iptal (isteğe göre değiştir)
ALLOW_FORCE_SINGLE = True           # True ise 1 kontratla işlem açılmasına izin ver (tek TP ile)
SINGLE_UNIT_TP_CHOICE = "LAST"      # 'LAST' veya 'FIRST'
MIN_UNITS_PER_TP = 1                # Her TP için minimum tam kontrat
LOG_NOTIONAL_ABORT = True           # Notional aşımı iptalinde log üret
# === HATA HARİTASI ve RUNTIME OVERRIDE ===
MEXC_ERROR_MAP = {
    7008: "Minimum kontrat miktarı/tutarı kuralı",
    2015: "Fiyat veya miktar tick (precision) hatası",
    510:  "Rate limit - kısa süre bekleyip yeniden deneyin",
    5003: "Geçersiz stop veya ters emir parametresi",
    8819: "Kaldıraç limiti aşımı - pozisyon hacmi çok yüksek",
    2051: "Tek emir maksimum adet sınırı",
    1002: "Bu kontrat için işlem yapılamaz - sembol devre dışı",
    2006: "Kaldıraç aralık dışı (min/max leverage limiti)",

}

RUNTIME_MIN_AMOUNT = {}  # symbol -> override_min_amount (float)
RUNTIME_LAST_ERROR = {}  # debug amaçlı
# Yüksek fiyatlı + amount_step=1 sembollerde notional şişmesini sınırlama
DYNAMIC_NOTIONAL_RULES = [
    {"price_min": 100, "factor": 2.5},
    {"price_min": 50,  "factor": 3.0},
    {"price_min": 20,  "factor": 4.0},
]
MIN_SL_UPDATE_INTERVAL = 20.0         # trailing SL yeniden yerleşim minimal saniye 0,5 idi 20,0 yaptım
MIN_INITIAL_TRAIL_DELAY = 5          # Pozisyon açıldıktan sonra trailing’in ilk güncellemesine minimum gecikme (sn)
SL_CANCEL_RETRY = 2                  # Stop listesi boş dönerse şu kadar ek deneme
SL_CANCEL_RETRY_SLEEP = 0.4          # Denemeler arası bekleme
MIN_SL_IMPROVE_TICKS = 5             # (İsteğe bağlı) Çok ufak farklarda gereksiz güncelleme engeli 0 idi 5 yaptım
CANCEL_MODE = "before"               # "before" veya "after" (yeni stop önce / sonra konulsun)
ALLOW_WORSE_SL = False               # False: LONG’da SL aşağı inmez, SHORT’ta yukarı çıkmaz
MIN_SL_STEP_RATIO = 0.0002           # %0.02 gibi çok küçük oynaklıkta spam engeli (parametrik)
TRAIL_LOOP_INTERVAL = 2.0            # Supervisor periyodu (sn)
RETRY_FETCH_STOPS = 3
RETRY_SLEEP = 0.4
ENABLE_TRAILING = True
STOP_FAIL_LOG_STEPS = {1, 2, 3, 5, 8, 13, 21, 34, 55, 89}  # Fibonacci approach
# === TRAILING İYİLEŞTİRME EK SABİTLERİ ===
FORCE_IMPROVE_TICKS = 8              # Mevcut SL’den en az 8 tick iyileşme yoksa yeni SL deneme
TRAIL_MIN_PROFIT_PCT = 0.25          # İlk trailing güncellemesi için min kar (yönüne göre %)
INITIAL_TRAIL_DELAY_DEFAULT = 45     # Pozisyon açıldıktan sonra ilk trailing uyarlamasına minimum gecikme (sn)
REQUIRE_LIST_FOR_CANCEL = True
ORPHAN_CLEANUP_INTERVAL = 180
MIN_FORCE_BLIND_SECONDS = 180     # Liste hiç gelmezse şu kadar saniye sonra 1 defa kör update'e izin
LOG_THROTTLE = {"near_same": {}, "skip_interval": {}}
ORPHAN_STOP_IDS = {}  # position_id -> {"all":set([...]), "last_keep": id, "last_cleanup": ts, "last_blind": ts}


# Logging Yapılandırması
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("../../mexc_api.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    import signal_merkezi as sm  # sadece IDE için


def _get_active_state_safe(user_id: int, exchange: str, symbol_any: str):
    """
    Circular import yaşamamak için ACTIVE_OPEN_POSITIONS'e lazy erişim.
    symbol_any: BTCUSDT veya BTC_USDT gelebilir; normalize_symbol_for_key core'a çeker.
    """
    try:
        sm = __import__("signal_merkezi", fromlist=["ACTIVE_OPEN_POSITIONS", "normalize_symbol_for_key"])
        norm = sm.normalize_symbol_for_key(symbol_any)
        return sm.ACTIVE_OPEN_POSITIONS.get((int(user_id), str(exchange).lower().strip(), norm))
    except Exception:
        return None


def get_symbol_meta(symbol: str):
    core_sym = normalize_core_symbol(symbol)
    return provider_get_symbol_meta(exchange="mexc", symbol=core_sym)



def _try_plan_to_dict(plan: Any) -> Dict[str, Any]:
    """
    Planı güvenli şekilde dict'e çevir.
    Öncelik: settings.execution_plan_serialization.execution_plan_to_dict
    """
    try:
        from settings.execution_plan_serialization import execution_plan_to_dict
        d = execution_plan_to_dict(plan)  # type: ignore
        if isinstance(d, dict):
            return d
    except Exception:
        pass

    # fallback
    try:
        d2 = vars(plan)
        if isinstance(d2, dict):
            return d2
    except Exception:
        pass

    return {"_plan_str": str(plan), "_type": str(type(plan))}


def log_plan_in_mexc_executor(plan: Any, tag: str = "MEXC_PLAN_IN") -> None:
    try:
        d = _try_plan_to_dict(plan)
        logger.info(
            f"[{tag}] user={d.get('user_id')} ex={d.get('exchange')} "
            f"sym={d.get('symbol_exchange')} side={d.get('side')} lev={d.get('leverage')} lot_notional={d.get('lot_notional')}"
        )
        logger.info(f"[{tag}_JSON]\n" + json.dumps(d, ensure_ascii=False, indent=2))
    except Exception as e:
        logger.warning(f"[{tag}_LOG_ERR] {e}", exc_info=True)


def _redact_value(k: str, v: Any) -> Any:
    """Log redaction: api key/passphrase/secret vb. maskele."""
    key = (k or "").lower()
    if any(s in key for s in ("api", "secret", "pass", "token", "key", "auth", "cookie", "sign")):
        if v is None:
            return None
        s = str(v)
        if len(s) <= 8:
            return "***"
        return s[:4] + "***" + s[-2:]
    return v


def _safe_serialize(obj: Any, *, max_list: int = 30, max_str: int = 5000) -> Any:
    """
    Plan objesi gibi JSON'lanamayan nesneleri güvenli şekilde dict'e çevirir.
    - dataclass -> asdict benzeri yaklaşım (varsayılan __dict__)
    - list/dict recursion + limit
    """
    try:
        if obj is None:
            return None

        if isinstance(obj, (int, float, bool)):
            return obj

        if isinstance(obj, str):
            if len(obj) > max_str:
                return obj[:max_str] + "...(truncated)"
            return obj

        if isinstance(obj, (list, tuple, set)):
            out = []
            for i, it in enumerate(list(obj)[:max_list]):
                out.append(_safe_serialize(it, max_list=max_list, max_str=max_str))
            if len(obj) > max_list:
                out.append(f"...(+{len(obj)-max_list} more)")
            return out

        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                ks = str(k)
                out[ks] = _safe_serialize(v, max_list=max_list, max_str=max_str)
            return out

        # dataclass / pydantic benzeri
        if hasattr(obj, "model_dump"):  # pydantic v2
            return _safe_serialize(obj.model_dump(), max_list=max_list, max_str=max_str)
        if hasattr(obj, "dict"):  # pydantic v1
            try:
                return _safe_serialize(obj.dict(), max_list=max_list, max_str=max_str)
            except Exception:
                pass

        # normal class
        if hasattr(obj, "__dict__"):
            return _safe_serialize(vars(obj), max_list=max_list, max_str=max_str)

        return str(obj)

    except Exception as e:
        return {"_serialize_error": str(e), "_type": str(type(obj))}


def log_execution_plan(plan: ExecutionPlan, *, tag: str = "MEXC_PLAN_IN", level: int = logging.INFO) -> None:
    """
    Gelen planı 'tek yerden' düzgün loglamak için.
    """
    try:
        raw = _safe_serialize(plan)
        if isinstance(raw, dict):
            # redaction uygula
            safe = {k: _redact_value(k, v) for k, v in raw.items()}
        else:
            safe = raw

        # Pretty JSON (okunabilir) + tek satır kısa özet
        short_user = (safe.get("user_id") if isinstance(safe, dict) else None)
        short_sym = (safe.get("symbol_exchange") if isinstance(safe, dict) else None)
        short_side = (safe.get("side") if isinstance(safe, dict) else None)

        logger.log(level, f"[{tag}] user={short_user} sym={short_sym} side={short_side}")
        logger.log(level, f"[{tag}_JSON]\n{json.dumps(safe, ensure_ascii=False, indent=2)}")

    except Exception as e:
        logger.warning(f"[{tag}_LOG_ERR] {e}", exc_info=True)


def _safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

def _safe_int(x, default=0) -> int:
    try:
        return int(float(x))
    except Exception:
        return default

def _margin_type_from_open_type(open_type: int) -> str:
    """
    MEXC openType mapping'i burada normalize edeceğiz.
    Not: Sende şu an openType=2 gönderiliyor.
    Eğer UI'da bunun CROSS/ISOLATED karşılığını kesinleştirdiysen burada sabitle.
    """
    # Varsayılan (senin mevcut yorumuna göre güncelleyebilirsin):
    # 1 -> CROSS, 2 -> ISOLATED  (örnek)
    if open_type == 2:
        return "CROSS"
    if open_type == 1:
        return "ISOLATED"
    return f"UNKNOWN({open_type})"

def _compute_open_vol_from_target(
    symbol: str,
    entry_price: float,
    target_notional_usdt: float,
    meta: Optional[Dict[str, Any]],
) -> float:
    if entry_price <= 0 or target_notional_usdt <= 0:
        return 0.0

    contract_size = 1.0
    if meta:
        try:
            contract_size = float(meta.get("contract_size", 1.0) or 1.0)
        except Exception:
            contract_size = 1.0
    if contract_size <= 0:
        contract_size = 1.0

    # ✅ contractSize'lı doğru formül
    raw_vol = float(target_notional_usdt) / (float(entry_price) * contract_size)

    if meta:
        vol_q = round_amount_step(
            raw_vol,
            float(meta.get("amount_step", 1.0) or 1.0),
            int(meta.get("amount_decimals", 0) or 0),
            float(meta.get("min_amount", 1.0) or 1.0),
        )
        return float(vol_q)

    return float(raw_vol)


def _calc_expected_from_vol(entry_price: float, vol: float, leverage: int, contract_size: float = 1.0) -> Dict[str, float]:
    lev = max(1, int(leverage))
    cs = float(contract_size) if contract_size and contract_size > 0 else 1.0
    notional = float(vol) * cs * float(entry_price)
    margin = notional / lev
    return {"notional": notional, "margin": margin}


def _plan_target_notional(plan: ExecutionPlan) -> float:
    try:
        margin_budget = float(getattr(plan, "lot_notional", 0.0) or 0.0)
        lev = float(getattr(plan, "leverage", 1) or 1)
        return margin_budget * lev
    except Exception:
        return 0.0


async def mexc_execute_plan(plan: ExecutionPlan, user: dict) -> dict:
    # ✅ Gelen planı tam snapshot logla (maskeli)
    log_plan_in_mexc_executor(plan, tag="MEXC_PLAN_IN")
    log_execution_plan(plan, tag="MEXC_PLAN_IN")


    # --- SAFE basics ---
    user_id: int = _safe_int(getattr(plan, "user_id", None), 0)
    symbol = str(getattr(plan, "symbol_exchange", "") or "").strip()
    if not symbol:
        return {"success":False, "error":"symbol boş"}

    symbol = symbol.upper().replace("/", "_").replace("-", "_")
    if symbol.endswith("USDT") and not symbol.endswith("_USDT"):
        symbol = symbol[:-4] + "_USDT"

    if not symbol:
        return {"success": False, "error": "symbol boş"}

    side_raw = getattr(plan, "side", None)
    side_u: str = str(side_raw or "LONG").upper().strip()
    if side_u not in OPEN_SIDE_MAP:
        logger.warning(f"[MEXC_PLAN_BAD_SIDE] user={user_id} sym={symbol} side={side_u} -> fallback LONG")
        side_u = "LONG"

    # web key
    u = user or {}
    passphrase = u.get("passphrase") or get_mexc_web_key(user_id)
    if not passphrase:
        return {"success": False, "error": "web_key yok"}

    client = all_api(web_key=passphrase)

    # --- META ---
    try:
        meta_any = get_symbol_meta(symbol)
        meta: Dict[str, Any] = meta_any if isinstance(meta_any, dict) else {}
    except Exception:
        meta = {}

    # contract_size: önce meta, yoksa plan.debug (build_execution_plan bunu basıyor)
    cs_raw = meta.get("contract_size", meta.get("contractSize", None))
    if cs_raw is None:
        try:
            dbg = getattr(plan, "debug", None) or {}
            if isinstance(dbg, dict):
                cs_raw = dbg.get("contract_size", None)
        except Exception:
            cs_raw = None

    contract_size: float = _safe_float(cs_raw, 1.0)
    if contract_size <= 0:
        contract_size = 1.0

    # --- openType (CROSS=2 kesin) ---
    plan_margin_u: str = str(getattr(plan, "margin", "") or "").upper().strip()
    open_type_value: int = 2 if plan_margin_u == "CROSS" else 1

    # --- SAFE plan inputs ---
    entry_raw = getattr(plan, "entry_price", None)
    entry: float = _safe_float(entry_raw, 0.0)
    if entry <= 0:
        logger.warning(f"[MEXC_PLAN_BAD_ENTRY] user={user_id} sym={symbol} entry={entry_raw}")
        return {"success": False, "error": "entry_price_invalid"}

    lev_raw = getattr(plan, "leverage", None)
    lev: int = max(1, _safe_int(lev_raw, 1))

    lot_raw = getattr(plan, "lot_notional", None)
    target_margin: float = _safe_float(lot_raw, 0.0)
    if target_margin <= 0:
        logger.warning(f"[MEXC_PLAN_BAD_MARGIN] user={user_id} sym={symbol} lot_notional={lot_raw}")
        return {"success": False, "error": "lot_notional_invalid"}

    # ✅ Tek kaynak notional: lot_notional * leverage
    target_notional: float = float(target_margin) * float(lev)

    # --- VOL ENFORCE + PRECHECK ---
    try:
        enforced_vol_raw = _compute_open_vol_from_target(symbol, entry, target_notional, meta)
        enforced_vol: float = _safe_float(enforced_vol_raw, 0.0)

        if enforced_vol <= 0:
            logger.warning(
                f"[MEXC_VOL_ENFORCE_ABORT] user={user_id} sym={symbol} entry={entry} "
                f"target_notional={target_notional} enforced_vol={enforced_vol} meta={meta} debug={getattr(plan,'debug',None)}"
            )
            return {"success": False, "error": "vol_compute_failed"}

        exp = _calc_expected_from_vol(entry, enforced_vol, lev, contract_size=contract_size)

        tol_abs = 1.0
        tol_pct = 0.05
        tol = max(tol_abs, target_margin * tol_pct)

        if abs(exp["margin"] - target_margin) > tol:
            logger.warning(
                f"[MEXC_PRECHECK_MARGIN_DEVIATION_ABORT] user={user_id} sym={symbol} side={side_u} "
                f"entry={entry} lev={lev} contract_size={contract_size} "
                f"target_margin={target_margin:.6f} exp_margin={exp['margin']:.6f} diff={abs(exp['margin']-target_margin):.6f} tol={tol:.6f} "
                f"target_notional={target_notional:.6f} exp_notional={exp['notional']:.6f} "
                f"enforced_vol={enforced_vol} meta={meta} debug={getattr(plan,'debug',None)}"
            )
            return {"success": False, "error": "target_margin_not_achievable_with_steps"}

    except Exception as e:
        logger.error(f"[MEXC_VOL_ENFORCE_ERR] user={user_id} sym={symbol} err={e}", exc_info=True)
        return {"success": False, "error": "vol_enforce_error"}
    exec_norm = {
        "user_id": user_id,
        "symbol": symbol,
        "side": side_u,
        "plan_margin": plan_margin_u,
        "openType": open_type_value,
        "entry": entry,
        "leverage": lev,
        "target_margin_usdt(lot_notional)": target_margin,
        "target_notional_usdt": target_notional,
        "enforced_vol": enforced_vol,
        "contract_size": contract_size,
        "meta": meta,
        "plan_debug": getattr(plan, "debug", None),
    }
    logger.info("[MEXC_EXEC_NORM]\n" + json.dumps(exec_norm, ensure_ascii=False, indent=2))

    # --- OPEN payload ---
    open_payload: Dict[str, Any] = {
        "symbol": symbol,
        "side": int(OPEN_SIDE_MAP.get(side_u, OPEN_SIDE_MAP["LONG"])),
        "vol": float(enforced_vol),
        "leverage": int(lev),
        "price": float(entry),
        "type": int(OPEN_ORDER_TYPE),
        "openType": int(open_type_value),
    }
    plan_norm = {
        "user_id": user_id,
        "symbol": symbol,
        "side": side_u,
        "margin_mode": plan_margin_u,
        "openType": open_type_value,
        "entry": entry,
        "leverage": lev,
        "target_margin": target_margin,
        "target_notional": target_notional,
        "enforced_vol": enforced_vol,
        "contract_size": contract_size,
        "meta": meta,
    }
    logger.info("[MEXC_PLAN_NORM]\n" + json.dumps(plan_norm, ensure_ascii=False, indent=2))

    # ---- PLAN/HEDEF log ----
    logger.info(
        f"[MEXC_EXEC_PLAN] user={user_id} sym={symbol} side={side_u} "
        f"plan_margin={plan_margin_u} openType={open_type_value} lot_margin={target_margin} lev={lev} entry={entry} "
        f"target_notional={target_notional} enforced_vol={enforced_vol} contract_size={contract_size} "
        f"meta={meta} debug={getattr(plan,'debug',None)}"
    )

    # ---- PRE-FLIGHT log (contract_size'lı) ----
    try:
        qty = float(open_payload["vol"])
        est_notional = qty * contract_size * entry
        est_margin = est_notional / float(lev)
        logger.info(
            f"[MEXC_PLAN_PRECHECK] user={user_id} sym={symbol} side={side_u} "
            f"entry={entry} qty={qty} lev={lev} contract_size={contract_size} "
            f"est_notional={est_notional:.6f} est_margin={est_margin:.6f} target_margin={target_margin:.6f}"
        )
    except Exception as e:
        logger.warning(f"[MEXC_PLAN_PRECHECK_ERR] user={user_id} sym={symbol} err={e}")

    # --------------- OPEN ---------------
    logger.info(f"[MEXC_OPEN_PAYLOAD] {open_payload}")

    open_resp = await asyncio.wait_for(
        asyncio.to_thread(client.place_order, open_payload),
        timeout=30
    )
    logger.info(f"[MEXC_OPEN_RESP] {open_resp}")

    # --- Auto-fix: leverage out of range -> retry once with exchange max ---
    if (not open_resp or not bool(open_resp.get("success"))) and (open_resp or {}).get("code") == 2006:
        ext = (open_resp or {}).get("_extend") or {}
        max_lev = _safe_int(ext.get("max"), 0)
        min_lev = _safe_int(ext.get("min"), 1)

        if max_lev > 0:
            new_lev = max(min_lev, min(int(lev), int(max_lev)))

            # Eğer gerçekten değişecekse 1 kez retry
            if new_lev != int(lev):
                logger.warning(
                    f"[MEXC_LEVERAGE_CLAMP_RETRY] user={user_id} sym={symbol} "
                    f"old_lev={lev} new_lev={new_lev} allowed=[{min_lev},{max_lev}]"
                )

                lev = int(new_lev)
                target_notional = float(target_margin) * float(lev)

                enforced_vol_retry = _compute_open_vol_from_target(symbol, entry, target_notional, meta)
                enforced_vol_retry = _safe_float(enforced_vol_retry, 0.0)

                if enforced_vol_retry > 0:
                    open_payload_retry = dict(open_payload)
                    open_payload_retry["leverage"] = int(lev)
                    open_payload_retry["vol"] = float(enforced_vol_retry)

                    # Precheck log (isteğe bağlı ama çok faydalı)
                    exp2 = _calc_expected_from_vol(entry, float(enforced_vol_retry), lev, contract_size=contract_size)
                    logger.info(
                        f"[MEXC_RETRY_PRECHECK] user={user_id} sym={symbol} side={side_u} "
                        f"entry={entry} lev={lev} vol={enforced_vol_retry} "
                        f"est_notional={exp2['notional']:.6f} est_margin={exp2['margin']:.6f} target_margin={target_margin:.6f}"
                    )

                    logger.info(f"[MEXC_OPEN_PAYLOAD_RETRY] {open_payload_retry}")
                    open_resp = await asyncio.wait_for(
                        asyncio.to_thread(client.place_order, open_payload_retry),
                        timeout=30
                    )
                    logger.info(f"[MEXC_OPEN_RESP_RETRY] {open_resp}")
                else:
                    logger.warning(
                        f"[MEXC_LEVERAGE_CLAMP_RETRY_ABORT] user={user_id} sym={symbol} "
                        f"reason=enforced_vol_retry<=0 new_lev={lev} target_notional={target_notional}"
                    )
    # Final fail
    if not open_resp or not bool(open_resp.get("success")):
        err = (open_resp or {}).get("error") or (open_resp or {}).get("message") or "open_failed"
        return {"success":False, "open_order":open_resp, "error":err, "code":(open_resp or {}).get("code")}

    # --------------- POSITION ---------------
    pos = wait_for_position(client, symbol)
    logger.info(f"[MEXC_POSITION] {pos}")

    if not pos:
        return {"success": True, "open_order": open_resp, "position_id": None,
                "tp_orders": [], "sl_order": None, "error": "position_not_found"}

    position_id = (pos or {}).get("positionId")

    # --------- (2.2) IM sapma kontrolü ----------
    try:
        im = _safe_float((pos or {}).get("im"), 0.0)

        abs_tol = 1.0
        pct_tol = 0.05
        tol = max(abs_tol, target_margin * pct_tol)
        diff = abs(im - target_margin)

        if diff > tol:
            logger.warning(
                f"[MEXC_IM_DEVIATION] user={user_id} sym={symbol} posId={position_id} "
                f"im={im:.6f} target_im={target_margin:.6f} diff={diff:.6f} tol={tol:.6f} "
                f"lev={lev} openType={pos.get('openType')} payload={open_payload} pos={pos}"
            )
        else:
            logger.info(
                f"[MEXC_IM_OK] user={user_id} sym={symbol} posId={position_id} "
                f"im={im:.6f} target_im={target_margin:.6f} diff={diff:.6f} tol={tol:.6f}"
            )
    except Exception as e:
        logger.warning(f"[MEXC_IM_CHECK_ERR] user={user_id} sym={symbol} posId={position_id} err={e}")

    # --------- (2.3) openType/margin uyumsuzluğu ----------
    try:
        pos_open_type = _safe_int((pos or {}).get("openType"), -1)
        pos_margin = _margin_type_from_open_type(pos_open_type)

        if plan_margin_u and not str(pos_margin).startswith("UNKNOWN") and pos_margin != plan_margin_u:
            logger.warning(
                f"[MEXC_MARGIN_MISMATCH] user={user_id} sym={symbol} posId={position_id} "
                f"plan_margin={plan_margin_u} pos_margin={pos_margin} pos_openType={pos_open_type} "
                f"payload={open_payload} pos={pos}"
            )
        else:
            logger.info(
                f"[MEXC_MARGIN_MATCH] user={user_id} sym={symbol} posId={position_id} "
                f"plan_margin={plan_margin_u} pos_openType={pos_open_type} pos_margin={pos_margin}"
            )
    except Exception as e:
        logger.warning(f"[MEXC_MARGIN_CHECK_ERR] user={user_id} sym={symbol} posId={position_id} err={e}")

    opened_volume = _safe_float((pos or {}).get("holdVol"), 0.0)

    # sl_tp_emir off
    if not bool(getattr(plan, "sl_tp_emir", False)):
        return {"success": True, "open_order": open_resp, "position_id": position_id,
                "tp_orders": [], "sl_order": None}

    # cancel old TP/SL
    try:
        await _cancel_all_open_tp_sl_orders(client, _safe_int(position_id, 0), symbol)
    except Exception:
        pass

    # TP
    tp_results: List[Dict[str, Any]] = []
    try:
        tp_structs = getattr(plan, "tp_structs", None) or []
        if opened_volume > 0 and tp_structs:
            tp_structs_native = []

            plan_meta = getattr(plan, "meta", None)
            amount_step = _safe_float(getattr(plan_meta, "amount_step", None), 1.0)
            amount_decimals = _safe_int(getattr(plan_meta, "amount_decimals", None), 0)
            min_amount = _safe_float(getattr(plan_meta, "min_amount", None), 1.0)

            for t in tp_structs:
                if not isinstance(t, dict):
                    continue

                price = _safe_float(t.get("price"), 0.0)
                if price <= 0:
                    continue

                vol = t.get("volume", None)
                pct = t.get("percent", None)

                if vol is None:
                    pct_f = _safe_float(pct, 0.0)
                    if pct_f <= 0:
                        continue
                    raw_vol = float(opened_volume) * (pct_f / 100.0)
                else:
                    raw_vol = _safe_float(vol, 0.0)

                vol_real = round_amount_step(raw_vol, amount_step, amount_decimals, min_amount)
                vol_int = int(vol_real)
                if vol_int <= 0:
                    continue

                tp_structs_native.append({
                    "price": f"{price:.8f}",
                    "volume": vol_int,
                    "percent": _safe_float(pct, 0.0)
                })

            if tp_structs_native:
                tp_results = client.place_native_multi_tp(_safe_int(position_id, 0), symbol, tp_structs_native)

    except Exception as e:
        logger.error(f"[MEXC_EXEC_TP_ERR] user={user_id} err={e}", exc_info=True)

    # SL
    sl_result: Optional[Dict[str, Any]] = None
    try:
        sl_price = _safe_float(getattr(plan, "sl_price", None), 0.0)
        if sl_price > 0 and opened_volume > 0:
            sl_result = client.place_native_stop_loss(_safe_int(position_id, 0), symbol, float(sl_price), opened_volume)

            try:
                if sl_result and isinstance(sl_result, dict) and sl_result.get("success"):
                    sym_any = getattr(plan, "symbol_core", None) or symbol
                    state = _get_active_state_safe(int(user_id), "mexc", sym_any)

                    # on_sl_move'a sembol olarak core key göndermek istiyoruz:
                    sm = __import__("signal_merkezi", fromlist=["normalize_symbol_for_key"])
                    norm_sym = sm.normalize_symbol_for_key(sym_any)

                    on_sl_move(int(user_id), "mexc", norm_sym, float(sl_price), "INITIAL", state)

            except Exception as e:
                logger.warning(f"[MEXC_SL_MOVE_INITIAL_HOOK_ERR] user={user_id} sym={symbol} err={e}")

    except Exception as e:
        logger.error(f"[MEXC_EXEC_SL_ERR] user={user_id} err={e}", exc_info=True)

    return {
        "success": True,
        "open_order": open_resp,
        "position_id": position_id,
        "tp_orders": tp_results,
        "sl_order": sl_result
    }


def _fetch_trailing_setting(user_id: int, exchange: str):
    """
    ayarlar tablosundan trailing_mode, trailing_param döndürür.
    exchange küçük harf girilmeli.
    """
    try:
        row = db_operation(
            "SELECT trailing_mode, trailing_param FROM ayarlar WHERE user_id=? AND LOWER(exchange)=?",
            (user_id, exchange.lower()),
            operation='select',
            fetch=True
        )
        if row and row[0]:
            mode, param = row[0]
            if mode and str(mode).upper() in ('PCT', 'ATR'):
                try:
                    return str(mode).upper(), float(param) if param is not None else None
                except (ValueError, TypeError) as e:
                    logging.error(f"Trailing parametresi float'a çevrilemedi: {e}")

                    return str(mode).upper(), None
        return None, None
    except Exception as e:
        logger.error(f"[TRAIL_FETCH_SETTING_ERR] user={user_id} ex={exchange} {e}")
        return None, None


def fetch_volatility_trailing_config(user_id: int, exchange: str):
    """
    dyn_vol_schema JSON parser:
      method: ATR_RATIO veya ATR_PCTL
      ATR_RATIO alanları: short_period, long_period, bands: [{ratio_max, multiplier}]
      ATR_PCTL alanları: window, min_samples, bands: [{pctl_max, multiplier}],
                         smoothing:{enabled,alpha,type}, regime_map, regime_be
    """
    try:
        row = db_operation("""
            SELECT dyn_vol_enabled, dyn_vol_schema
            FROM ayarlar
            WHERE user_id=? AND LOWER(exchange)=LOWER(?)
        """, (user_id, exchange), operation='select', fetch=True, fetch_all=False)
        if not row:
            return {"enabled": False}

        enabled = bool(row[0])
        if not enabled:
            return {"enabled": False}

        raw = row[1]
        if not raw:
            return {"enabled": False}

        try:
            conf = json.loads(raw)

        except json.JSONDecodeError as e:
            logger.error(f"[DYN_VOL_SCHEMA_JSON_ERR] user={user_id}, Hata: {e}")
            return {"enabled": False}

        method = (conf.get("method") or "ATR_RATIO").upper()

        base = {
            "enabled": True,
            "method": method,
            "cooldown_sec": int(conf.get("cooldown_sec", 60)),
            "stack_with_dynamic": bool(conf.get("stack_with_dynamic", True)),
            "min_param": float(conf.get("min_param", 0.0)),
            "max_param": float(conf.get("max_param", 999)),
            "vol_log": bool(conf.get("vol_log", True))
        }

        if method == "ATR_RATIO":
            short_p = int(conf.get("short_period", 14))
            long_p = int(conf.get("long_period", 50))
            bands = conf.get("bands", [])
            clean = []
            for b in bands:
                if "ratio_max" in b and "multiplier" in b:
                    clean.append({
                        "ratio_max": float(b["ratio_max"]),
                        "multiplier": float(b["multiplier"])
                    })
            clean.sort(key=lambda x: x["ratio_max"])
            base.update({
                "short_period": short_p,
                "long_period": long_p,
                "bands": clean
            })

        elif method == "ATR_PCTL":
            bands = conf.get("bands", [])
            clean = []
            for b in bands:
                if "pctl_max" in b and "multiplier" in b:
                    clean.append({
                        "pctl_max": float(b["pctl_max"]),
                        "multiplier": float(b["multiplier"])
                    })
            clean.sort(key=lambda x: x["pctl_max"])
            base.update({
                "window": int(conf.get("window", 300)),
                "min_samples": int(conf.get("min_samples", 80)),
                "bands": clean,
                "smoothing": conf.get("smoothing", {}),
                "regime_map": conf.get("regime_map", {}),
                "regime_be": conf.get("regime_be", {})
            })

        return base
    except Exception as e:
        logger.warning(f"[DYN_VOL_CONF_FALLBACK] {e}")
        return {"enabled": False}


def _load_markets_cached():
    global _last_market_load, _markets
    now = int(time.time())
    if _markets is None or now - _last_market_load > MARKET_CACHE_EXPIRE_SECONDS:
        ex = ccxt.mexc({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })
        _markets = ex.load_markets()
        _last_market_load = now
        logger.info(f"[MEXC_MARKETS_REFRESH] count={len(_markets)}")
    return _markets


_SYMBOL_META_CACHE = {}


def quantize_price_for_symbol(symbol: str,
                              raw_price: float,
                              side: str = None,
                              intent: str = None,
                              mode: str = "floor") -> float:
    """
    symbol için tick (price_step) bazında fiyatı kurala uygun hale getirir.
    intent: "TP" / "SL" / None (ileri yönsel strateji eklemek istersen)
    mode:
      - "floor": her zaman aşağı
      - "ceil" : yukarı
      - "auto" : side + intent'e göre yönsel
    """
    meta = get_symbol_meta(symbol)
    if not meta:
        # Meta yoksa yine de 6 hane ile gönderelim
        return float(f"{raw_price:.6f}")

    step = meta['price_step']
    dec = meta['price_decimals']

    # Yönsel mod (ileride istersen aç)
    if mode == "auto" and side and intent:
        # LONG pozisyon:
        #   TP => ceil (tetiklenmeme riskini azaltmak için)
        #   SL => floor (erken tetiklenmesin)
        # SHORT pozisyon:
        #   TP => floor
        #   SL => ceil
        up = False
        if side.upper() == "LONG" and intent == "TP":
            up = True
        elif side.upper() == "SHORT" and intent == "SL":
            up = True
        mode = "ceil" if up else "floor"

    d_raw = Decimal(str(raw_price))
    d_step = Decimal(str(step))
    ratio = d_raw / d_step

    if mode == "ceil":
        q = ratio.to_integral_value(rounding=ROUND_UP)
    else:
        q = ratio.to_integral_value(rounding=ROUND_DOWN)

    quant = q * d_step
    return float(f"{quant:.{dec}f}")


def round_price(value: float, precision: int) -> float:
    fmt = f"{{:.{precision}f}}"
    return float(fmt.format(value))


def round_by_step(value: float, step: float, decimals: int, mode: str = 'floor') -> float:
    """
    step bazlı (tick veya lot) yuvarlama.
    mode: 'floor' (aşağı) veya 'ceil' (yukarı)
    """
    if step <= 0:
        fmt = f"{{:.{decimals}f}}"
        return float(fmt.format(value))
    dv = Decimal(str(value))
    st = Decimal(str(step))
    ratio = dv / st
    if mode == 'ceil':
        q = ratio.to_integral_value(rounding=ROUND_UP)
    else:  # floor varsayılan
        q = ratio.to_integral_value(rounding=ROUND_DOWN)
    res = q * st
    fmt = f"{{:.{decimals}f}}"
    return float(fmt.format(res))


def round_price_step(price: float, step: float, decimals: int) -> float:
    return round_by_step(price, step, decimals, mode='floor')


def round_amount_step(amount: float, step: float, decimals: int, min_amount: float) -> float:
    amt = round_by_step(amount, step, decimals, mode='floor')
    if amt < min_amount:
        # min_amount zaten step katı olmalı; değilse tekrar floor uygula
        if step > 0 and (min_amount / step) % 1 != 0:
            amt = round_by_step(min_amount, step, decimals, mode='ceil')
        else:
            amt = min_amount
    return amt


def md5(value: str) -> str:
    """MD5 hash hesaplama"""
    try:
        logger.debug(f"MD5 hash hesaplanıyor: {value}")
        return hashlib.md5(value.encode('utf-8')).hexdigest()
    except Exception as e:
        logger.error(f"MD5 hash hesaplanırken hata: {e}")
        raise


def mexc_crypto(key: str, obj: Dict) -> Dict[str, str]:
    """MEXC için imza ve zaman damgası oluşturma"""
    try:
        date_now = str(int(time.time() * 1000))
        g = md5(key + date_now)[7:]
        s = json.dumps(obj, separators=(',', ':'))
        sign = md5(date_now + s + g)

        logger.debug(f"[CRYPTO_SIGN] ts={date_now}")
        return {'time': date_now, 'sign': sign}
    except Exception as e:
        logger.error(f"Crypto imzası oluşturulurken hata: {e}")
        raise


def get_mexc_web_key(user_id: int) -> Optional[str]:
    """
    Kullanıcının MEXC bilgilerini veritabanından çeker - DÜZELTME: Çift kayıt sorunu çözüldü
    """
    try:
        query = """
            SELECT DISTINCT passphrase
            FROM user_channel_info 
            WHERE user_id = ? AND LOWER(exchange) = 'mexc'
            LIMIT 1
            """

        logger.info(f"Web key çekiliyor - Kullanıcı ID: {user_id}")

        result = db_operation(query, (user_id,), fetch=True)

        if result and len(result) > 0 and result[0]:
            web_key = result[0][0]  # İlk kaydın ilk sütunu
            logger.info(f"Web key bulundu (ilk 5 karakter): {web_key[:5]}...")
            return web_key

        logger.warning(f"Kullanıcı {user_id} için MEXC web_key bilgileri bulunamadı!")
        return None

    except Exception as e:
        logger.error(f"MEXC web_key bilgileri alma hatası. "
                     f"Kullanıcı ID: {user_id}, Hata: {e}",
            exc_info=True)
        return None


def all_api(web_key: str):

    try:
        # Fonksiyon başlangıcında log
        logger.info(f"MEXC API istemcisi oluşturma işlemi başlatıldı. "
                    f"Web Key (ilk 5 karakter): {web_key[:5]}...")

        # Web key ile yeni bir MEXC API istemcisi oluştur
        mexc_client = MEXCAPIClient(web_key)

        # Başarılı oluşturma log'u
        logger.info(f"MEXC API istemcisi başarıyla oluşturuldu. "
                    f"Web Key (ilk 5 karakter): {web_key[:5]}...")

        return mexc_client

    except Exception as e:
        # Detaylı hata log'u
        logger.error(f"MEXC API istemcisi oluşturma hatası. "
                     f"Web Key (ilk 5 karakter): {web_key[:5]}, "
                     f"Hata: {e}",
                     exc_info=True)  # Tam hata izleme bilgisi için
        raise  # Hatayı yeniden yükselt


def interpret_mexc_error(resp: Dict, symbol: str) -> str:
    """
    MEXC hata yanıtını yorumlar, runtime min_amount override yapar (7008 için).
    """
    code = resp.get('code')
    msg = resp.get('message', '')
    if code in MEXC_ERROR_MAP:
        base_text = MEXC_ERROR_MAP[code]
    else:
        base_text = f"Bilinmeyen hata (code={code})"
    # 7008 => min emir kuralı
    if code == 7008:
        ext_val = None
        try:
            ext_val = resp.get('_extend', {}).get('value')

        except (AttributeError, TypeError) as e:
            logger.error(f"MEXC hata mesajından 'extend' okunurken hata: {e}")
            pass

        if ext_val:
            # Doğrudan min kontrat adedi olabilir
            prev = RUNTIME_MIN_AMOUNT.get(symbol)
            if not prev or float(ext_val) > float(prev):
                RUNTIME_MIN_AMOUNT[symbol] = float(ext_val)
                logger.warning(f"[RUNTIME_MIN_AMOUNT_UPDATE] {symbol} -> {ext_val}")
            base_text += f" (Borsa min kontrat: {ext_val})"
    # Kaydet
    RUNTIME_LAST_ERROR[symbol] = {
        "when": time.time(),
        "code": code,
        "msg": msg,
        "human": base_text
    }
    return base_text


def wait_for_position(
        client,
        symbol: str,
        max_wait: float = POSITION_WAIT_SECONDS,
        interval: float = POSITION_POLL_INTERVAL
) -> Optional[Dict]:
    """
    Açılış emri sonrası pozisyonun gerçekten oluşmasını bekler.
    holdVol > 0 olduğunda pozisyon döner.
    """
    start = time.time()
    while time.time() - start <= max_wait:
        try:
            data = client.get_open_positions()
            if data.get('success'):
                for p in data.get('data', []):
                    # Bazı dönen kayıtlarda sembol tam eşleşmeli (ör. MAV_USDT)
                    if p.get('symbol') == symbol and float(p.get('holdVol', 0)) > 0:
                        logger.info(f"[MEXC_POS_READY] symbol={symbol} holdVol={p.get('holdVol')}")
                        return p

            logger.info(f"[MEXC_WAIT_POS] symbol={symbol} still_not_ready sleep={interval}")
            time.sleep(interval)

        except Exception as e:
            logger.warning(f"[MEXC_WAIT_POS_ERR] {e}")
            time.sleep(interval)

    logger.warning(f"[MEXC_WAIT_POS_TIMEOUT] symbol={symbol} waited={max_wait}s")
    return None


async def _cancel_all_open_tp_sl_orders(client: 'MEXCAPIClient', position_id: int, symbol: str) -> bool:
    """
    Belirtilen pozisyon ID'si ve sembol için tüm açık TP/SL emirlerini iptal eder.
    """
    try:
        open_stop_orders = _mexc_fetch_open_stop_orders(client, position_id=position_id, symbol=symbol)
        if not open_stop_orders:
            logger.info(f"[CANCEL_ALL_TP_SL] pid={position_id} sym={symbol} için açık TP/SL emri bulunamadı.")
            return True

        logger.info(
            f"[CANCEL_ALL_TP_SL] pid={position_id} sym={symbol} için {len(open_stop_orders)} adet açık TP/SL emri iptal ediliyor.")
        success_count = 0
        for order in open_stop_orders:
            order_id = order.get("stopOrderId") or order.get("id") or order.get("orderId")
            if order_id:
                if _mexc_cancel_stop_order(client, str(order_id)):
                    success_count += 1
                await asyncio.sleep(0.1)  # Rate limit için küçük bir bekleme

        if success_count == len(open_stop_orders):
            logger.info(
                f"[CANCEL_ALL_TP_SL_OK] pid={position_id} sym={symbol} tüm TP/SL emirleri başarıyla iptal edildi.")
            return True
        else:
            logger.warning(
                f"[CANCEL_ALL_TP_SL_PARTIAL] pid={position_id} sym={symbol} {success_count}/{len(open_stop_orders)} adet TP/SL emri iptal edildi.")
            return False

    except Exception as e:
        logger.error(
            f"[CANCEL_ALL_TP_SL_ERR] pid={position_id} sym={symbol} için TP/SL emirleri iptal edilirken hata: {e}",
            exc_info=True)
        return False


class MEXCAPIClient:
    base_url = 'https://futures.mexc.com/api/v1/private'
    USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36')

    def __init__(self, web_key: str):
        """API sınıfı başlatıcısı"""
        self.web_key = web_key
        self.base_headers = {
            'User-Agent': self.USER_AGENT,
            'Authorization': web_key,
            'Cookie': f"u_id={web_key}"
        }
        logger.info(f"API sınıfı başlatıldı. Web Key: {web_key[:5]}...")
        # TP ve SL emirleri için yeni özellikler ekleyelim
        self.tp_orders = []
        self.sl_order = None
        self.total_tp_count = 0  # Toplam TP sayısını takip etmek için
        self.order_id = None  # Yeni eklenen özellik

    @staticmethod
    def _log_api_call(method, endpoint, payload=None):
        """API çağrılarını loglamak için yardımcı metod"""
        logger.info(f"API Çağrısı - Metod: {method}, Endpoint: {endpoint}")

        if payload:
            logger.debug(f"Payload: {payload}")

    def get_open_positions(self) -> Dict:
        """Açık pozisyonları alma - GELİŞTİRİLMİŞ"""
        endpoints_to_try = [
            '/position/open_positions',
            '/api/v1/private/position/open_positions',
            '/position/list'
        ]

        for endpoint in endpoints_to_try:
            try:
                self._log_api_call("GET", endpoint)

                response = requests.get(
                    self.base_url.replace('/api/v1/private', '') + endpoint,
                    headers=self.base_headers,
                    timeout=15
                )

                if response.status_code!=200:
                    logger.warning(f"[GET_POSITIONS_HTTP_ERR] endpoint={endpoint} status={response.status_code}")
                    continue

                result = response.json()

                if result.get('success', False):
                    position_count = len(result.get('data', []))
                    logger.info(f"[GET_POSITIONS_OK] endpoint={endpoint} count={position_count}")
                    return result
                else:
                    logger.warning(f"[GET_POSITIONS_FAIL] endpoint={endpoint} result={result}")
                    continue

            except Exception as e:
                logger.error(f"[GET_POSITIONS_ERR] endpoint={endpoint} error={e}")
                continue

        # Tüm endpointler başarısız
        logger.error("[GET_POSITIONS_ALL_FAILED]")
        return {
            "success": False,
            "error": "Tüm pozisyon endpointleri başarısız"
        }

    def place_order(self, obj: Dict) -> Dict:
        try:
            price_val = float(obj["price"])
            vol_val = float(obj["vol"])
            symbol = obj["symbol"]

            meta = get_symbol_meta(symbol)

            # ✅ KRİTİK: meta yoksa yanlış precision ile emir göndermeyelim
            if not meta:
                logger.warning(f"[MEXC_META_MISSING_ABORT] symbol={symbol} price={price_val} vol={vol_val}")
                return {"success":False, "code":-1, "message":"meta_not_found"}

            # --- price quantize ---
            price_val = round_price_step(price_val, meta["price_step"], meta["price_decimals"])
            price_fmt = f"{price_val:.{meta['price_decimals']}f}"

            # --- vol quantize ---
            # (senin mevcut davranışını koruyorum: floor int)
            # İstersen bunu round_amount_step ile de yapabiliriz ama şimdilik minimum değişiklik:
            vol_val = float(int(math.floor(vol_val)))

            base = {
                "symbol":symbol,
                "side":obj["side"],
                "openType":obj.get("openType", 2),
                "type":obj.get("type", 5),  # default market
                "vol":str(vol_val),
                "leverage":str(obj.get("leverage", 10)),
                "price":price_fmt,
                "priceProtect":"0",
            }

            logger.info(f"[MEXC_OPEN_REQ] {base}")

            signature = mexc_crypto(self.web_key, base)
            headers = {
                **self.base_headers,
                "Content-Type":"application/json",
                "x-mxc-sign":signature["sign"],
                "x-mxc-nonce":signature["time"],
            }

            response = requests.post(
                f"{self.base_url}/order/create",
                headers=headers,
                json=base,
                timeout=10
            )

            if response is None:
                logger.error("[MEXC_PLACE_ORDER] HTTP response is None")
                return {"success":False, "error":"HTTP response None"}

            if response.status_code != 200:
                logger.error(f"[MEXC_PLACE_ORDER] HTTP {response.status_code}: {response.text[:200]}")
                return {"success":False, "error":f"HTTP {response.status_code}", "http_status":response.status_code}

            result = response.json()
            if result is None:
                logger.error("[MEXC_PLACE_ORDER] JSON parse result is None")
                return {"success":False, "error":"JSON parse None"}

            if not result.get("success"):
                human = interpret_mexc_error(result, symbol)
                logger.warning(
                    f"[MEXC_ORDER_FAIL] symbol={symbol} code={result.get('code')} human='{human}' raw={result}"
                )

                if result.get("code") == 7008:
                    new_min = RUNTIME_MIN_AMOUNT.get(symbol)
                    if new_min and float(new_min) > float(vol_val):
                        logger.warning(
                            f"[RECALC_LOT_TOO_SMALL] symbol={symbol} required_min={new_min} tried={vol_val}"
                        )
                return result

            logger.info(f"[MEXC_OPEN_ORDER_RESP] {json.dumps(result, indent=2)}")
            return result

        except Exception as e:
            logger.error(f"[MEXC_PLACE_ORDER_ERR] {e}", exc_info=True)
            return {"success":False, "error":str(e)}

    def _native_stoporder_headers(self, payload: Dict) -> Dict:
        sig = mexc_crypto(self.web_key, payload)
        headers = {
            **self.base_headers,
            'Content-Type': 'application/json',
            'x-mxc-sign': sig['sign'],
            'x-mxc-nonce': sig['time']
        }
        return headers


    @staticmethod
    def build_base_pl_payload(position_id: int,
                              include_reverses: bool = True,
                              price_protect: str = MEXC_PL_PRICE_PROTECT) -> Dict:
        """
        Native (sniff format) profit/loss order base payload.
        """
        base = {
            "positionId": position_id,
            "profitLossVolType": MEXC_PL_PROFIT_LOSS_VOL_TYPE,
            "volType": MEXC_PL_VOL_TYPE,
            "priceProtect": price_protect,
            # Sunucu UI'si body'de ts gönderiyor (opsiyonel de olabilir)
            "ts": int(time.time() * 1000)
        }
        if include_reverses:
            base["takeProfitReverse"] = MEXC_PL_TAKE_PROFIT_REVERSE
            base["stopLossReverse"] = MEXC_PL_STOP_LOSS_REVERSE
        return base


    def place_native_take_profit(self,
                                 position_id: int,
                                 symbol: str,
                                 tp_price: float,
                                 tp_volume: float,
                                 trend: str = MEXC_PL_DEFAULT_TREND,
                                 meta: Optional[Dict] = None) -> Dict:
        if meta is None:
            meta = get_symbol_meta(symbol)
        if meta:
            tp_price = quantize_price_for_symbol(symbol, tp_price, mode="floor")
        payload = self.build_base_pl_payload(position_id)
        payload.update({
            "takeProfitPrice": tp_price,
            "takeProfitVol": int(tp_volume),
            "profitTrend": trend
        })
      
        logger.info(f"[MEXC_NATIVE_TP_REQ] {payload}")
        headers = self._native_stoporder_headers(payload)
        resp = requests.post(f"{self.base_url}{STOPORDER_ENDPOINT}", headers=headers, json=payload, timeout=10)
        try:
            rj = resp.json()

        except json.JSONDecodeError as e:
            logger.error(f"JSON decode hatası (place_native_take_profit): {e}")

            rj = {"http_status":resp.status_code, "raw":resp.text[:200]}
        if rj.get("success"):
            logger.info(f"[MEXC_NATIVE_TP_OK] price={tp_price} vol={tp_volume} id={rj.get('data')}")
        else:
            logger.warning(f"[MEXC_NATIVE_TP_FAIL] status={resp.status_code} body={rj}")
        return rj


    def place_native_stop_loss(self,
                               position_id: int, symbol: str,
                               sl_price: float, sl_volume: float,
                               trend: str = MEXC_PL_DEFAULT_TREND, 
                               meta: Optional[Dict] = None) -> Dict:
        if meta is None:
            meta = get_symbol_meta(symbol)
        if meta:
            sl_price = quantize_price_for_symbol(symbol, sl_price, mode="floor")

        payload = self.build_base_pl_payload(position_id)
        payload.update({
            "stopLossPrice": sl_price,
            "stopLossVol": int(sl_volume),
            "lossTrend": trend
        })

        # mhash kullanımı sniff edilen isteklerde görünmüyor, şimdilik kapalı
        logger.info(f"[MEXC_NATIVE_SL_REQ] {payload}")
        headers = self._native_stoporder_headers(payload)
        resp = requests.post(f"{self.base_url}{STOPORDER_ENDPOINT}", headers=headers, json=payload, timeout=10)
        try:
            rj = resp.json()

        except json.JSONDecodeError as e:
            logger.error(f"JSON decode hatası (place_native_stop_loss): {e}")

            rj = {"http_status":resp.status_code, "raw":resp.text[:200]}
        if rj.get("success"):
            logger.info(f"[MEXC_NATIVE_SL_OK] price={sl_price} vol={sl_volume} id={rj.get('data')}")
        else:
            logger.warning(f"[MEXC_NATIVE_SL_FAIL] status={resp.status_code} body={rj}")
        return rj


    # -------------------- INTERNAL URL HELPERS --------------------
    def _full_url(self, endpoint: str) -> str:
        """
        endpoint şu formatlardan biri olabilir:
          /api/v1/private/stop-order/list/open
          api/v1/private/stop-order/list/open
          /position/open_positions  (base_url zaten /api/v1/private içerir)
        Güvenli şekilde tam URL üretir.
        """
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint
        # Tam private kök: https://futures.mexc.com
        root = "https://futures.mexc.com"
        if endpoint.startswith("/api/"):
            return root + endpoint
        if endpoint.startswith("/"):
            # base_url = .../api/v1/private
            return self.base_url + endpoint
        # çıplak verildiyse
        return self.base_url + '/' + endpoint


    def _signed_headers(self, payload: Dict) -> Dict:
        sig = mexc_crypto(self.web_key, payload)
        return {
            **self.base_headers,
            'Content-Type': 'application/json',
            'x-mxc-sign': sig['sign'],
            'x-mxc-nonce': sig['time']
        }


    # -------------------- GENERIC PRIVATE GET/POST --------------------
    def private_get(self, endpoint: str, params: Optional[Dict] = None) -> Dict:
        """
        Stop order list vs. gibi GET istekleri için.
        MEXC web (sniff) formatında biz gene body imzalıyoruz. Burada basit yaklaşım:
        - params yoksa {} al
        - ts ekle, imzala
        - query string ile gönder (bazı endpointler body beklemiyor)
        """
        try:
            p = dict(params) if params else {}
            # ts ekleyelim (imza algoritmandaki obj içine giriyor)
            if "ts" not in p:
                p["ts"] = int(time.time() * 1000)
            headers = self._signed_headers(p)
            # GET’te query olarak gönderelim
            url = self._full_url(endpoint)
            resp = requests.get(url, headers=headers, params=p, timeout=10)
            return resp.json()
        except Exception as e:
            logger.error(f"[PRIVATE_GET_ERR] endpoint={endpoint} err={e}")
            return {"success": False, "error": str(e)}


    def private_post(self, endpoint: str, data: Optional[Dict] = None) -> Dict:
        try:
            payload = dict(data) if data else {}
            if "ts" not in payload:
                payload["ts"] = int(time.time() * 1000)
            headers = self._signed_headers(payload)
            url = self._full_url(endpoint)
            resp = requests.post(url, headers=headers, json=payload, timeout=10)
            return resp.json()
        except Exception as e:
            logger.error(f"[PRIVATE_POST_ERR] endpoint={endpoint} err={e}")
            return {"success": False, "error": str(e)}


    def place_native_multi_tp(self,
                              position_id: int,
                              symbol: str,
                              tp_structs: List[Dict],
                              sleep_between: float = 0.25,
                              meta: Optional[Dict] = None) -> List[Dict]:
        if meta is None:
            meta = get_symbol_meta(symbol)
        results = []
        for idx, tp in enumerate(tp_structs, start=1):
            price = float(tp["price"])
            vol = int(tp["volume"])
            price_q = quantize_price_for_symbol(symbol, price, mode="floor")
            logger.info(f"[MEXC_NATIVE_TP_BATCH] idx={idx} price_raw={price} price_q={price_q} vol={vol}")
            r = self.place_native_take_profit(
                position_id=position_id,
                symbol=symbol,
                tp_price=price_q,
                tp_volume=vol,
                meta=meta
            )
            results.append(r)
            time.sleep(sleep_between)
        return results


def get_sl_tp_settings(user_id: int, exchange: str) -> Dict[str, Any]:
    """
    Kullanıcının SL/TP ayarlarını veritabanından çeker.
    Dönen dict hiçbir koşulda None içermez ve normalize edilmiştir.
    """
    default_settings: Dict[str, Any] = {
        "sl_tp_emir": "off",
        "stop_loss": "off",             # fixed/percentage/off
        "terial_stop": "kapalı",        # kapalı/TRAILING_2.0 vb.
        "sl_percentage": 0.0,
        "take_profit": "SINYAL_TP",     # SINYAL_TP veya "1".."10"
        "maliyet_cek": "0",             # "0".."10"
        "tp_percentages": [0.0] * 10
    }

    query = """
    SELECT 
        sl_tp_emir, stop_loss, terial_stop, sl_percentage,
        take_profit, maliyet_cek,
        tp1, tp2, tp3, tp4, tp5, tp6, tp7, tp8, tp9, tp10
    FROM ayarlar
    WHERE user_id = ? AND LOWER(exchange) = LOWER(?)
    """

    def _to_int_user(v) -> int:
        try:
            return int(float(v))
        except Exception:
            return 0

    def _parse_float_pct(v) -> float:
        try:
            txt = str(v).strip().replace('%', '').replace(',', '.')
            if not txt:
                return 0.0
            return float(txt)
        except (ValueError, TypeError):
            return 0.0

    def _norm_sl_tp_emir(v) -> str:
        mode_txt = str(v or "").strip().lower()
        return "on" if mode_txt in ("on", "1", "true", "aktif", "open") else "off"

    def _norm_stop_loss(v) -> str:
        sl_txt = str(v or "").strip().lower()
        if sl_txt in ("fixed", "sabit"):
            return "fixed"
        if sl_txt in ("percentage", "percent", "yuzde", "yüzde"):
            return "percentage"
        return "off"

    def _norm_take_profit(v) -> str:
        tp_txt = str(v or "").strip().upper()
        if tp_txt == "SINYAL_TP":
            return "SINYAL_TP"
        try:
            n = int(float(tp_txt))
            n = max(1, min(10, n))
            return str(n)
        except (ValueError, TypeError):
            return "SINYAL_TP"

    def _norm_maliyet_cek(v) -> str:
        val_txt = str(v or "").strip()
        try:
            n = int(float(val_txt))
            n = max(0, min(10, n))
            return str(n)
        except (ValueError, TypeError):
            return "0"

    def _norm_tp_list(vals) -> list:
        out = []
        for val in vals:
            p = _parse_float_pct(val)
            p = max(0.0, min(100.0, p))
            out.append(p)
        # her zaman 10 adet
        if len(out) < 10:
            out += [0.0] * (10 - len(out))
        return out[:10]

    try:
        uid = _to_int_user(user_id)
        ex = str(exchange or "").strip().lower()
        if not ex:
            return default_settings

        logger.info(f"SL/TP ayarları çekiliyor - Kullanıcı: {uid}, Borsa: {ex}")

        result = db_operation(query, (uid, ex), fetch=True)
        if not result or not result[0]:
            return default_settings

        row = result[0]

        tp_percentages = _norm_tp_list(row[6:16])

        # toplam > 100 ise normalize (senin eski davranışın korunuyor)
        s = sum(tp_percentages)
        if s > 100.0 and s > 0:
            tp_percentages = [p * (100.0 / s) for p in tp_percentages]

        sl_percent = _parse_float_pct(row[3])
        sl_percent = max(0.0, min(100.0, sl_percent))

        return {
            "sl_tp_emir": _norm_sl_tp_emir(row[0]),
            "stop_loss": _norm_stop_loss(row[1]),
            "terial_stop": str(row[2]) if row[2] else default_settings["terial_stop"],
            "sl_percentage": sl_percent,
            "take_profit": _norm_take_profit(row[4]),
            "maliyet_cek": _norm_maliyet_cek(row[5]),
            "tp_percentages": tp_percentages
        }

    except Exception as e:
        logger.error(
            f"SL/TP ayarları çekilirken hata (UserID: {user_id}, Exchange: {exchange}): {str(e)}",
            exc_info=True
        )
        return default_settings


def calculate_take_profits(
    signal_tps: List[Union[float, str]],
    tp_percentages: List[Union[float, str]],
    side: str = "LONG",
    entry_price: Optional[float] = None,
    calculated_lot: Optional[float] = None,
    amount_step: float = 1.0,
    amount_decimals: int = 0,
    min_amount: float = 1.0
) -> List[Dict[str, Union[float, str]]]:
    """
    TP dağıtım mantığı:
    - Fiyat filtreleme (yön + MIN_TP_DIFF_PERCENT)
    - step bazlı dağıtım (amount_step)
    - total_units == 0 -> []
    - total_units == 1 -> tek TP (FIRST/LAST)
    - total_units < tp_count -> TP sayısını total_units'e indir (her TP min 1 unit)
    - total_units >= tp_count -> yüzdeye göre dağıt, toplam volume'u step toleransında tam kapat
    """
    try:
        if not signal_tps:
            return []

        if entry_price is None or calculated_lot is None:
            return []

        try:
            entry = float(entry_price)
            if entry <= 0:
                return []
        except (TypeError, ValueError):
            return []

        side_u = (side or "LONG").upper()

        # --- step/min normalize ---
        try:
            step = float(amount_step) if amount_step is not None else 1.0
        except (TypeError, ValueError):
            step = 1.0
        if step <= 0:
            step = 1.0

        try:
            min_amt = float(min_amount) if min_amount is not None else step
        except (TypeError, ValueError):
            min_amt = step
        if min_amt <= 0:
            min_amt = step

        # total_lot normalize
        try:
            total_lot = float(calculated_lot)
        except (TypeError, ValueError):
            return []
        if total_lot <= 0:
            return []

        # --- Ham TP fiyatlarını float'a çevir ---
        raw_prices: List[float] = []
        for tp in signal_tps[:MAX_TP_COUNT]:
            try:
                raw_prices.append(float(tp))
            except (ValueError, TypeError) as e:
                logger.debug(f"[TP_PARSE_SKIP] tp={tp} err={e}")
                continue
        if not raw_prices:
            return []

        # --- Yön + minimum fark filtresi ---
        filtered: List[float] = []
        for p in raw_prices:
            if p <= 0:
                continue
            dp = abs(p - entry) / entry * 100
            if side_u == "LONG" and p > entry and dp >= MIN_TP_DIFF_PERCENT:
                filtered.append(p)
            elif side_u == "SHORT" and p < entry and dp >= MIN_TP_DIFF_PERCENT:
                filtered.append(p)

        if not filtered:
            logger.warning("[MEXC_TP_FILTER] fark yetersiz -> tüm sinyal TP fiyatları kullanılacak")
            filtered = raw_prices[:]

        filtered = sorted(filtered) if side_u == "LONG" else sorted(filtered, reverse=True)
        filtered = filtered[:MAX_TP_COUNT]

        # --- total_units hesapla (step bazlı) ---
        total_units = int(round(total_lot / step))
        if total_units <= 0:
            logger.warning(f"[MEXC_TP_SKIP] total_units=0 lot={total_lot} step={step}")
            return []

        total_lot_q = total_units * step

        # --- Tek kontrat durumu ---
        if total_units == 1:
            chosen_price = filtered[-1] if str(SINGLE_UNIT_TP_CHOICE).upper() == "LAST" else filtered[0]
            vol_real = round_amount_step(step, step, amount_decimals, min_amt)
            return [{
                "price": f"{float(chosen_price):.8f}",
                "volume": float(vol_real),
                "percent": 100.0
            }]

        # --- Birim sayısı TP sayısından az ise: TP sayısını düşür ---
        if total_units < len(filtered):
            use_tp_count = total_units
            reduced_prices = filtered[:use_tp_count]
            logger.warning(
                f"[MEXC_TP_UNIT_REDUCE] units={total_units} requested_tp={len(filtered)} -> use={use_tp_count}"
            )

            tp_structs: List[Dict[str, Union[float, str]]] = []
            remaining_units = total_units

            for i, price in enumerate(reduced_prices):
                vol_units = 1 if i < use_tp_count - 1 else remaining_units
                remaining_units -= vol_units

                vol_real = round_amount_step(vol_units * step, step, amount_decimals, min_amt)
                tp_structs.append({
                    "price": f"{float(price):.8f}",
                    "volume": float(vol_real),
                    "percent": 0.0
                })

            total_vol = sum(float(t["volume"]) for t in tp_structs)
            if total_vol > 0:
                for t in tp_structs:
                    t["percent"] = (float(t["volume"]) / total_vol) * 100.0

            return tp_structs

        # --- Yeterli birim var -> yüzdelere göre dağıtım ---
        def _parse_pct(pct_in: Union[float, str]) -> Optional[float]:
            try:
                pct_txt = str(pct_in).strip().replace('%', '').replace(',', '.')
                if not pct_txt:
                    return None

                pct_val = float(pct_txt)
                if pct_val <= 0:
                    return None

                return max(0.0, min(100.0, pct_val))
            except (ValueError, TypeError):
                return None

        cfg_perc: List[float] = []
        for x in (tp_percentages or []):
            v = _parse_pct(x)
            if v is not None:
                cfg_perc.append(v)

        tp_count = len(filtered)
        if not cfg_perc:
            cfg_perc = [100.0 / tp_count] * tp_count
        else:
            cfg_perc = cfg_perc[:tp_count]
            if len(cfg_perc) < tp_count:
                remain = tp_count - len(cfg_perc)
                remaining_pct = max(0.0, 100.0 - sum(cfg_perc))
                rest_each = (remaining_pct / remain) if remain > 0 else 0.0
                cfg_perc += [rest_each] * remain

        total_p = sum(cfg_perc)
        if total_p <= 0:
            cfg_perc = [100.0 / tp_count] * tp_count
        else:
            cfg_perc = [p * (100.0 / total_p) for p in cfg_perc]

        tp_structs: List[Dict[str, Union[float, str]]] = []
        for price, pct in zip(filtered, cfg_perc):
            raw_part = total_lot_q * pct / 100.0
            vol_share = round_amount_step(raw_part, step, amount_decimals, min_amt)
            if vol_share <= 0:
                vol_share = min_amt
            tp_structs.append({
                "price": f"{float(price):.8f}",
                "volume": float(vol_share),
                "percent": float(pct)
            })

        cur_sum = sum(float(t["volume"]) for t in tp_structs)
        diff = total_lot_q - cur_sum

        if tp_structs and abs(diff) >= (step * 0.5):
            last = tp_structs[-1]
            new_last = float(last["volume"]) + diff

            if new_last < min_amt:
                short = min_amt - new_last
                new_last = min_amt
                if len(tp_structs) >= 2:
                    prev = tp_structs[-2]
                    prev_new = float(prev["volume"]) - short
                    if prev_new < min_amt:
                        prev_new = min_amt
                        logger.warning("[MEXC_TP_DIFF_FIX] diff çok büyük; min_amount sınırına takıldı")
                    prev["volume"] = float(round_amount_step(prev_new, step, amount_decimals, min_amt))

            last["volume"] = float(round_amount_step(new_last, step, amount_decimals, min_amt))

        return tp_structs

    except Exception as e:
        logger.error(f"[TP_CALC_ERR] {e}", exc_info=True)
        return []

def calculate_stop_loss(
        entry_price: float,
        side: str,
        stop_loss_type: str,
        sl_value: Optional[float] = None,
        sl_percentage: Optional[float] = None,        
        total_position_value: Optional[float] = None) -> Optional[Dict[str, str]]:
    try:
        # Detaylı giriş logları
        logger.info(f"""
        🔍 Stop Loss Hesaplama Girdi Parametreleri:
        - Giriş Fiyatı: {entry_price}
        - Taraf: {side}
        - SL Tipi: {stop_loss_type}
        - SL Değeri: {sl_value}
        - SL Yüzdesi: {sl_percentage}        
        - Toplam Pozisyon Değeri: {total_position_value}
        """)

        # Stop Loss kapalıysa
        if stop_loss_type == "off":
            logger.info("Stop Loss kapalı")
            return None


        # Sabit SL varsa
        if stop_loss_type == "fixed" and sl_value:
            logger.info(f"Sabit SL kullanılıyor: {sl_value}")
            if total_position_value:
                return {
                    "price": f"{float(sl_value):.8f}",
                    "quantity": f"{total_position_value:.4f}",
                    "triggerType": 1
                }
            return None

        # Yüzde bazlı SL hesaplama
        if stop_loss_type == "percentage" and sl_percentage:
            try:
                # Taraf bazlı multiplier hesaplama
                if side.upper() == "LONG":
                    multiplier = 1 - (sl_percentage / 100)
                    logger.info(f"LONG için multiplier: {multiplier}")
                else:  # SHORT
                    multiplier = 1 + (sl_percentage / 100)
                    logger.info(f"SHORT için multiplier: {multiplier}")

                # SL fiyatını hesapla
                sl_price = entry_price * multiplier
                sl_price = round(sl_price, 8)  # 8 haneye yuvarla

                logger.info(f"""
                🛑 Stop Loss Detayları:
                - Hesaplanan SL Fiyatı: {sl_price:.8f}
                - Multiplier: {multiplier}
                - SL Yüzdesi: {sl_percentage}%
                - Toplam Değer: {total_position_value:.4f} USDT
                """)

                if total_position_value:
                    return {
                        "price": f"{sl_price:.8f}",
                        "quantity": f"{total_position_value:.4f}",
                        "triggerType": 1
                    }

            except Exception as e:
                logger.error(f"SL yüzde hesaplama hatası: {e}")
                return None

        logger.warning("Stop Loss hesaplanamadı veya gerekli parametreler eksik")
        return None

    except Exception as e:
        logger.error(f"Stop Loss hesaplama hatası: {e}", exc_info=True)
        return None


def get_user_balance(user_id: int, exchange: str) -> float:
    """
    Kullanıcının belirtilen borsadaki bakiyesini döndürür

    Args:
        user_id (int): Kullanıcı ID'si
        exchange (str): Borsa adı (örn. 'mexc')

    Returns:
        float: Kullanıcının bakiyesi, hata durumunda 0.0
    """
    try:
        # Web key al
        web_key = get_mexc_web_key(user_id)

        if not web_key:
            logger.error(f"Kullanıcı {user_id} için web key bulunamadı")
            return 0.0

        # MEXC API istemcisini başlat
        mexc_client = all_api(web_key)

        # Bakiye bilgisini almak için API çağrısı yapılabilir (get_open_positions'dan)
        # Bu örnek için basit bir yaklaşım kullanıyoruz
        # Gerçek uygulamada MEXC API'nin bakiye sorgulama endpointini kullanmalısınız
        # Örnek olarak, açık pozisyonları sorgulayarak bakiye bilgisini alabiliriz
        positions_result = mexc_client.get_open_positions()

        if positions_result.get('success') and '_extend' in positions_result:
            available_balance = positions_result.get('_extend', {}).get('available', 0.0)
            logger.info(f"Kullanıcı {user_id} için mevcut bakiye: {available_balance}")
            return float(available_balance)

        logger.warning(f"Kullanıcı {user_id} için bakiye bilgisi alınamadı")
        return 0.0

    except Exception as e:
        logger.error(f"Bakiye alma hatası: {e}")
        return 0.0


async def mexc_close_position(
    symbol: str,
    side: str,  # Bildirim/format için; mümkünse pozisyondan teyit edilir
    user_id: Optional[int] = None,
    lot: Optional[float] = None,
    passphrase: Optional[str] = None,         # web_key buradan gelebilir
    close_price: Optional[float] = None       # bazı merkezler gönderiyor; kullanmasak da kabul ediyoruz
) -> Dict[str, Any]:
    try:
        if not user_id:
            return {"success": False, "error": "user_id yok"}

        uid = int(user_id)

        # web_key önceliği: passphrase -> DB
        web_key = passphrase or get_mexc_web_key(uid)
        if not web_key:
            logger.error("Web key bulunamadı!")
            return {"success": False, "error": "Web key bulunamadı"}

        trader = all_api(web_key=web_key)

        sym = str(symbol or "").strip()
        if not sym:
            return {"success": False, "error": "symbol boş"}

        # Sembolü MEXC native formatına yaklaştır (BTCUSDT -> BTC_USDT)
        sym_u = sym.upper().replace("/", "_").replace("-", "_")
        if sym_u.endswith("USDT") and not sym_u.endswith("_USDT"):
            sym_u = sym_u[:-4] + "_USDT"
        sym = sym_u

        # Pozisyonu çek (hem lot hem de gerçek yön tespiti için)
        pos_side_u = str(side or "").upper().strip()
        pos_hold_vol: float = 0.0

        open_positions = trader.get_open_positions()
        if open_positions.get("success") and open_positions.get("data"):
            for position in open_positions["data"]:
                if str(position.get("symbol", "")).upper() != sym:
                    continue

                # holdVol
                try:
                    pos_hold_vol = float(position.get("holdVol", 0) or 0)
                except Exception:
                    pos_hold_vol = 0.0

                # yön tespiti (alan adları MEXC response’a göre değişebilir)
                # En azından birkaç olası alanı deniyoruz:
                raw_dir = (
                    position.get("positionType")
                    or position.get("posSide")
                    or position.get("side")
                    or position.get("holdSide")
                    or ""
                )
                raw_dir_u = str(raw_dir).upper()

                if "SHORT" in raw_dir_u:
                    pos_side_u = "SHORT"
                elif "LONG" in raw_dir_u:
                    pos_side_u = "LONG"

                break  # sembol bulundu

        # lot belirtilmemişse pozisyondan al
        if not lot or float(lot or 0) <= 0:
            lot = pos_hold_vol

        try:
            lot_f = float(lot or 0)
        except Exception:
            lot_f = 0.0

        if lot_f <= 0:
            return {"success": False, "error": "Kapatılacak lot bulunamadı (holdVol=0?)"}

        # Close side mapping (KRİTİK FIX)
        close_side = TP_SIDE_MAP.get(pos_side_u)
        if close_side is None:
            # Güvenli fallback: parametre side'a göre dene, o da yoksa LONG varsay
            close_side = TP_SIDE_MAP.get(str(side or "").upper().strip(), 4)

        close_params = {
            "flashClose": False,
            "symbol": sym,
            "side": int(close_side),   # ✅ LONG/SHORT’a göre
            "openType": 1,             # mevcut kodunuzu korudum
            "type": 5,                 # market
            "vol": int(math.floor(lot_f)),  # büyütme yok: floor
            "leverage": 20,
            "priceProtect": "0"
        }

        if int(close_params["vol"]) <= 0:
            return {"success": False, "error": "close vol <= 0 (floor sonrası)"}

        result = trader.place_order(close_params)

        if result and result.get("success"):
            logger.info(
                f"[MEXC_CLOSE_OK] symbol={sym} pos_side={pos_side_u} close_side={close_side} "
                f"closed_vol={close_params['vol']} user={uid} ts={datetime.now()}"
            )

            # Bildirim (mevcut yapını koruyorum)
            try:
                await send_trade_notification(
                    user_id=uid,
                    signal_dict={
                        "symbol": sym,
                        "position_type": pos_side_u,
                        "close_position": True,
                        "lot": float(close_params["vol"]),
                        "entry_points": [],
                        "take_profits": [],
                        "stop_loss": None,
                        "close_price": close_price
                    },
                    position_result={
                        "success": True,
                        "data": result.get("data")
                    }
                )
            except Exception as _notify_err:
                logger.warning(f"[MEXC_CLOSE_NOTIFY_WARN] user={uid} err={_notify_err}")

            return {
                "success": True,
                "data": result.get("data"),
                "closed_amount": float(close_params["vol"]),
                "position_side": pos_side_u
            }

        err_txt = (result or {}).get("error") or (result or {}).get("message") or "Bilinmeyen hata"
        logger.error(f"[MEXC_CLOSE_FAIL] symbol={sym} user={uid} err={err_txt} raw={result}")
        return {"success": False, "error": err_txt, "raw": result}

    except Exception as e:
        logger.error(f"[MEXC_CLOSE_ERR] {e}", exc_info=True)

        try:
            if user_id:
                await send_trade_notification(
                    user_id=int(user_id),
                    signal_dict={
                        "symbol": symbol,
                        "position_type": str(side or "").upper(),
                        "close_position": True,
                        "entry_points": [],
                        "take_profits": [],
                        "stop_loss": None,
                        "close_price": close_price,
                        "error": str(e)
                    },
                    position_result={"success": False, "error": str(e)}
                )
        except Exception:
            pass

        return {"success": False, "error": str(e)}


def fetch_break_even_config(user_id: int, exchange: str):
    try:
        row = db_operation("""
            SELECT be_enabled, be_trigger_type, be_trigger_value, be_offset_pct
            FROM ayarlar
            WHERE user_id=? AND LOWER(exchange)=LOWER(?)
        """, (user_id, exchange), operation='select', fetch=True, fetch_all=False)
        if not row:
            return {"enabled": False}
        be_enabled = bool(row[0])
        trig_type = (row[1] or 'ATR').upper()
        trig_val = float(row[2]) if row[2] not in (None, '') else (2.0 if trig_type == 'ATR' else 1.0)
        offset_pct = float(row[3]) if row[3] not in (None, '') else 0.0
        return {
            "enabled": be_enabled,
            "trigger_type": 'ATR' if trig_type not in ('ATR', 'PCT') else trig_type,
            "trigger_value": trig_val,
            "offset_pct": offset_pct
        }
    except Exception as e:
        # OperationalError dahil -> kolon yoksa
        logger.warning(f"[BREAK_EVEN_CONF_FALLBACK] user={user_id} ex={exchange} err={e}")
        return {"enabled": False}


def fetch_tp_break_even_config(user_id: int, exchange: str):
    try:
        row = db_operation("""
            SELECT be_tp_enabled, be_tp_count, be_tp_progressive
            FROM ayarlar
            WHERE user_id=? AND LOWER(exchange)=LOWER(?)
        """, (user_id, exchange), operation='select', fetch=True, fetch_all=False)
        if not row:
            return {"enabled": False}
        enabled = bool(row[0])
        count = int(row[1]) if row[1] not in (None, '') else 1
        progressive = bool(row[2])
        return {
            "enabled": enabled,
            "count": max(1, count),
            "progressive": progressive
        }
    except Exception as e:
        logger.error(f"[TP_BE_CONF_ERR] {e}")
        return {"enabled": False}


def fetch_dynamic_trailing_config(user_id: int, exchange: str):
    try:
        row = db_operation("""
            SELECT dyn_trail_enabled, dyn_trail_schema
            FROM ayarlar
            WHERE user_id=? AND LOWER(exchange)=LOWER(?)
        """, (user_id, exchange), operation='select', fetch=True, fetch_all=False)
        if not row:
            return {"enabled": False}
        enabled = bool(row[0])
        if not enabled:
            return {"enabled": False}
        schema_raw = row[1]
        if not schema_raw:
            return {"enabled": False}
        try:
            schema = json.loads(schema_raw)

        except json.JSONDecodeError as e:
            logger.error(f"[DYN_TRAIL_SCHEMA_JSON_ERR] user={user_id}, Hata: {e}")
            return {"enabled": False}

        # Normalize
        steps = schema.get("steps", [])
        tp_steps = schema.get("tp_steps", [])
        # filtre
        steps = [s for s in steps if "trigger_pct" in s and "new_param" in s]
        tp_steps = [s for s in tp_steps if "tp_count" in s and "new_param" in s]
        steps.sort(key=lambda x: float(x["trigger_pct"]))
        tp_steps.sort(key=lambda x: int(x["tp_count"]))
        return {
            "enabled": True,
            "mode": (schema.get("mode") or "AUTO").upper(),
            "steps": steps,
            "tp_steps": tp_steps,
            "min_param": float(schema.get("min_param", 0.0))
        }
    except Exception as e:
        logger.warning(f"[DYN_TRAIL_CONF_FALLBACK] user={user_id} ex={exchange} err={e}")
        return {"enabled": False}


def build_mexc_dynamic_update_sl_adapter():
    def _adapter(position_id: int, symbol_core: str, new_sl_price: float) -> bool:
        owner = TRAILING_POSITION_OWNERS.get(position_id)
        if not owner:
            logger.warning(f"[ADAPTER_NO_OWNER] pid={position_id}")
            return False

        user_id = owner["user_id"]
        trail_state = TRAILING_MANAGER.positions.get(position_id)
        side = (trail_state or {}).get("side", "LONG")

        try:
            # Client cache - her seferinde yeniden oluştur (web key sorunu için)
            web_key = get_mexc_web_key(user_id)
            if not web_key:
                logger.warning(f"[ADAPTER_NO_WEBKEY] user={user_id}")
                return False

            # Cache'i temizle ve yeniden oluştur
            if user_id in _MEXC_CLIENT_CACHE:
                del _MEXC_CLIENT_CACHE[user_id]

            _MEXC_CLIENT_CACHE[user_id] = all_api(web_key=web_key)
            client = _MEXC_CLIENT_CACHE[user_id]

            # Önce pozisyon hacmini kontrol et
            vol = _fetch_position_volume(client, position_id)
            if not vol or vol <= 0:
                logger.warning(f"[ADAPTER_NO_VOL_FAST] pid={position_id}")

                # Pozisyon kapalıysa trailing'i kaldır
                if position_id in TRAILING_MANAGER.positions:
                    logger.info(f"[TRAILING_REMOVE_CLOSED] pid={position_id}")
                    TRAILING_MANAGER.remove(position_id)
                    if position_id in TRAILING_POSITION_OWNERS:
                        del TRAILING_POSITION_OWNERS[position_id]

                return False

            # Volume güncelle
            TRAILING_MANAGER.update_position_volume(position_id, vol, min_step=1.0)

            # Stop listesi çek (opsiyonel - başarısız olsa da devam et)
            stops = _mexc_fetch_open_stop_orders(client, position_id=position_id, symbol=symbol_core)
            fetched_ok = bool(stops)

            if fetched_ok:
                logger.debug(f"[ADAPTER_STOP_LIST_OK] pid={position_id} count={len(stops)}")
                STOP_FETCH_FAILS[position_id] = 0

                # Eski SL'leri iptal et
                sl_orders = [s for s in stops if s.get("stopLossPrice") is not None]
                for s in sl_orders:
                    sid = str(s.get("stopOrderId") or s.get("id") or s.get("orderId") or s.get("data"))
                    if sid and sid!="None":
                        _mexc_cancel_stop_order(client, sid)
                        time.sleep(0.1)
            else:
                # Liste çekilemedi ama devam et
                fail_count = STOP_FETCH_FAILS.get(position_id, 0) + 1
                STOP_FETCH_FAILS[position_id] = fail_count

                if fail_count in STOP_FAIL_LOG_STEPS:
                    logger.warning(f"[STOP_LIST_FAIL_COUNT] pid={position_id} cnt={fail_count}")

            # Yeni SL yerleştir
            q_price = quantize_price_for_symbol(symbol_core, new_sl_price, intent="SL", side=side, mode="floor")
            oid = _mexc_place_stop_loss(client, position_id, symbol_core, side, q_price, vol)

            if oid:
                try:
                    # timeline hook + state (circular import yok)
                    state = _get_active_state_safe(int(user_id), "mexc", symbol_core)

                    sm = __import__("signal_merkezi", fromlist=["normalize_symbol_for_key"])
                    norm_sym_key = sm.normalize_symbol_for_key(symbol_core)

                    on_sl_move(int(user_id), "mexc", norm_sym_key, float(q_price), "TRAIL", state)
                except Exception as hook_err:
                    logger.warning(f"[DYN_ADAPTER_SL_MOVE_HOOK_WARN] user={user_id} pid={position_id} err={hook_err}")

                logger.info(f"[TRAIL_FAST_OK] user={user_id} pid={position_id} side={side} price={q_price} vol={vol}")
                return True

            else:
                logger.warning(f"[TRAIL_FAST_FAIL] user={user_id} pid={position_id}")
                return False

        except Exception as e:
            logger.error(f"[TRAIL_ADAPTER_FAST_ERR] pid={position_id} err={e}", exc_info=True)
            return False

    return _adapter


# === MEXC STOP ORDER YARDIMCI FONKSİYONLARI (TRAILING ADAPTER) ===
# === SL GÜNCELLEME ANA FONKSİYONU (MEXC) ===
async def mexc_update_stop_loss(
    user_id: int,
    symbol_core: str,          # "BTCUSDT" gibi core key
    position_id: int,
    new_sl_price: float,
    reason: str = "TRAIL",     # "TRAIL" | "BE" | "MANUAL" | "RULE"
    exchange: str = "mexc"
) -> Dict[str, Any]:
    """
    MEXC için SL güncellemesi:
      - Stop listesi çekilir (mümkünse)
      - CANCEL_MODE'a göre eski stop(lar) iptal edilir
      - Yeni SL yerleştirilir
      - Başarı durumunda timeline hook on_sl_move çağrılır
    """
    try:
        # 1) Web key ve client
        web_key = get_mexc_web_key(int(user_id))
        if not web_key:
            return {"success": False, "error": "web_key yok"}
        client = all_api(web_key=web_key)

        # 2) Sembol formatı (MEXC native için "BTC_USDT")
        core_u = str(symbol_core or "").upper().strip()
        if not core_u:
            return {"success": False, "error": "symbol_core boş"}

        symbol_ex = core_u
        if symbol_ex.endswith("USDT") and not symbol_ex.endswith("_USDT"):
            symbol_ex = symbol_ex[:-4] + "_USDT"
        symbol_ex = symbol_ex.replace("/", "_").replace("-", "_")

        # 3) Pozisyon hacmi (SL volume için)
        vol = _fetch_position_volume(client, int(position_id))
        if not vol or float(vol) <= 0:
            logger.warning(f"[MEXC_UPD_SL_ABORT] pid={position_id} vol_not_found")
            return {"success": False, "error": "position_volume_not_found"}

        # 4) Side tespiti (KRİTİK FIX: sabit LONG değil)
        side_u = "LONG"
        try:
            owner = TRAILING_POSITION_OWNERS.get(int(position_id)) or {}
            if owner.get("user_id") == int(user_id) and owner.get("side"):
                side_u = str(owner.get("side")).upper().strip()
        except Exception:
            pass
        if side_u not in ("LONG", "SHORT"):
            side_u = "LONG"

        # 5) stop listesi (best-effort)
        stops = _mexc_fetch_open_stop_orders(client, position_id=int(position_id), symbol=symbol_ex)
        fetched_ok = bool(stops)
        if fetched_ok:
            STOP_FETCH_FAILS[int(position_id)] = 0
        else:
            STOP_FETCH_FAILS[int(position_id)] = STOP_FETCH_FAILS.get(int(position_id), 0) + 1
            if STOP_FETCH_FAILS[int(position_id)] in STOP_FAIL_LOG_STEPS:
                logger.warning(f"[STOP_LIST_FAIL_COUNT] pid={position_id} cnt={STOP_FETCH_FAILS[int(position_id)]}")

        stop_loss_orders = [s for s in (stops or []) if s.get("stopLossPrice") is not None]

        # 6) Yeni SL fiyatını quantize et (auto: side+intent’e göre ceil/floor)
        q_price = quantize_price_for_symbol(
            symbol_ex,
            float(new_sl_price),
            intent="SL",
            side=side_u,
            mode="auto"
        )

        # 7) CANCEL_MODE stratejisi
        if CANCEL_MODE == "before":
            # önce iptal
            for o in stop_loss_orders:
                sid = o.get("stopOrderId") or o.get("id") or o.get("orderId")
                if sid:
                    _mexc_cancel_stop_order(client, str(sid))
                    time.sleep(0.08)

            oid = _mexc_place_stop_loss(client, int(position_id), symbol_ex, side_u, float(q_price), float(vol))
            if not oid:
                return {"success": False, "error": "place_sl_failed"}

        else:
            # önce yeni SL
            oid = _mexc_place_stop_loss(client, int(position_id), symbol_ex, side_u, float(q_price), float(vol))
            if not oid:
                return {"success": False, "error": "place_sl_failed"}

            # sonra iptal
            for o in stop_loss_orders:
                sid = o.get("stopOrderId") or o.get("id") or o.get("orderId")
                if sid:
                    _mexc_cancel_stop_order(client, str(sid))
                    time.sleep(0.08)

        # 8) Hook
        try:
            state = _get_active_state_safe(int(user_id), exchange, core_u)

            sm = __import__("signal_merkezi", fromlist=["normalize_symbol_for_key"])
            norm_sym_key = sm.normalize_symbol_for_key(core_u)

            on_sl_move(int(user_id), exchange, norm_sym_key, float(q_price), reason or "TRAIL", state)

        except Exception as hook_err:
            logger.warning(f"[MEXC_UPD_SL_HOOK_WARN] user={user_id} sym={core_u} err={hook_err}")

        logger.info(
            f"[MEXC_UPD_SL_OK] user={user_id} pid={position_id} sym={symbol_ex} "
            f"side={side_u} price={q_price} reason={reason}"
        )
        return {
            "success": True,
            "data": {
                "position_id": int(position_id),
                "symbol": symbol_ex,
                "side": side_u,
                "new_sl_price": float(q_price)
            }
        }

    except Exception as e:
        logger.error(
            f"[MEXC_UPD_SL_ERR] user={user_id} pid={position_id} sym={symbol_core} err={e}",
            exc_info=True
        )
        return {"success": False, "error": str(e)}



def _mexc_fetch_open_stop_orders(client, position_id: int = None, symbol: str = None) -> List[Dict]:
    """
    Stop order listesi - GELİŞTİRİLMİŞ: Daha fazla endpoint ve hata toleransı
    """
    endpoints_try = [
        ("POST", "/stoporder/list/open/v2"),
        ("GET", "/stoporder/list/open/v2"),
        ("POST", "/stoporder/list/v2"),
        ("GET", "/stoporder/list/v2"),
        ("POST", "/api/v1/private/stoporder/list/open/v2"),
        ("GET", "/api/v1/private/stoporder/list/open/v2"),
        ("POST", "/order/list/stop_limit"),  # Alternatif endpoint
        ("GET", "/order/list/stop_limit")
    ]

    base_payload: Dict[str, Any] = {"pageNum": 1, "pageSize": 50}

    # Position ID varsa payload'a ekle
    if position_id:
        base_payload["positionId"] = str(position_id)

    # Symbol varsa payload'a ekle
    if symbol:
        s = str(symbol or "").upper().replace("/", "_").replace("-", "_")
        if s.endswith("USDT") and not s.endswith("_USDT"):
            s = s[:-4] + "_USDT"
        base_payload["symbol"] = s

    for method, ep in endpoints_try:
        try:
            logger.debug(f"[STOP_LIST_TRY] method={method} endpoint={ep} pid={position_id}")

            if method=="POST":
                resp = client.private_post(ep, data=base_payload)
            else:
                resp = client.private_get(ep, params=base_payload)

            if not resp:
                logger.debug(f"[STOP_LIST_NULL] method={method} endpoint={ep}")
                continue

            if not resp.get("success"):
                logger.debug(f"[STOP_LIST_FAIL] method={method} endpoint={ep} code={resp.get('code')}")
                continue

            # Veri çıkarma - çoklu format desteği
            data = resp.get("data", {})
            items = []

            # Farklı response formatlarını dene
            for key in ["resultList", "orders", "list", "data", "stopOrders"]:
                if key in data and isinstance(data[key], list):
                    items = data[key]
                    break

            # Direkt data list ise
            if not items and isinstance(data, list):
                items = data

            if not items:
                logger.debug(f"[STOP_LIST_EMPTY_DATA] method={method} endpoint={ep}")
                continue

            # Filtreleme
            sym_norm = None
            if symbol:
                s = symbol.upper().replace("/", "_").replace("-", "_")
                if s.endswith("USDT") and not s.endswith("_USDT"):
                    s = s[:-4] + "_USDT"
                sym_norm = s

            filtered_items = []
            for item in items:
                # Position ID filtresi
                if position_id:
                    item_pos_id = item.get("positionId")
                    if str(item_pos_id)!=str(position_id):
                        continue

                # Symbol filtresi
                if sym_norm:
                    item_symbol = (item.get("symbol") or "").upper()
                    if sym_norm not in item_symbol:
                        continue

                filtered_items.append(item)

            logger.info(f"[STOP_LIST_SUCCESS] method={method} endpoint={ep} pid={position_id} count={len(filtered_items)}")
            return filtered_items

        except Exception as e:
            logger.warning(f"[STOP_LIST_EXCEPTION] method={method} endpoint={ep} err={e}")
            continue

    logger.warning(f"[STOP_LIST_ALL_FAILED] pid={position_id} symbol={symbol}")
    return []


def _mexc_cancel_stop_order(client, stop_id: str) -> bool:
    try:
        payload = {"stopOrderId": stop_id}
        resp = client.private_post(STOPORDER_CANCEL_ENDPOINT, data=payload)
        if resp.get("success"):
            logger.info(f"[STOP_CANCEL_OK] id={stop_id}")
            return True
        logger.warning(f"[STOP_CANCEL_FAIL] id={stop_id} resp={resp}")
        return False
    except Exception as e:
        logger.warning(f"[STOP_CANCEL_ERR] id={stop_id} err={e}")
        return False


def _mexc_place_stop_loss(client,
                          position_id: int,
                          symbol: str,
                          side: str,
                          stop_price: float,
                          volume: float) -> Optional[str]:
    try:
        payload = {
            "positionId": position_id,
            "profitLossVolType": "SEPARATE",
            "volType": 1,
            "priceProtect": "1",
            "ts": int(time.time() * 1000),
            "takeProfitReverse": 2,
            "stopLossReverse": 2,
            "stopLossPrice": float(stop_price),
            "stopLossVol": int(volume),
            "lossTrend": "1"
        }
        stop_price_q = quantize_price_for_symbol(symbol, float(stop_price), intent="SL", side=side, mode="floor")
        payload["stopLossPrice"] = stop_price_q
        resp = client.private_post(STOPORDER_ENDPOINT, data=payload)
        if resp.get("success"):
            oid = str(resp.get("data"))
            logger.info(f"[PLACE_SL_OK] pid={position_id} price={stop_price_q} vol={volume} id={oid}")
            LAST_PLACED_STOP[position_id] = {
                "id": oid,
                "price": stop_price_q,
                "ts": time.time()
            }
            STOP_FETCH_FAILS[position_id] = 0
            # (EK) İlk yerleşim veya güncelleme sonrası gereksiz erken tekrarları engellemek için
            # timestamp zaten tutuluyor.
            return oid

        logger.warning(f"[PLACE_SL_FAIL] pid={position_id} resp={resp}")
        return None
    except Exception as e:
        logger.error(f"[PLACE_SL_ERR] {e}", exc_info=True)
        return None


def _fetch_position_volume(client, position_id: int) -> Optional[float]:
    """
    Pozisyon hacmini çeker - GELİŞTİRİLMİŞ: Çoklu endpoint desteği
    """
    endpoints_to_try = [
        "/position/open_positions",
        "/api/v1/private/position/open_positions",
        "/position/list"
    ]

    for endpoint in endpoints_to_try:
        try:
            logger.debug(f"[FETCH_POS_VOL_TRY] endpoint={endpoint} pid={position_id}")

            # GET isteği dene
            resp = client.private_get(endpoint)

            if not resp:
                logger.warning(f"[FETCH_POS_VOL_NULL] endpoint={endpoint}")
                continue

            if not resp.get("success"):
                logger.warning(f"[FETCH_POS_VOL_FAIL] endpoint={endpoint} resp={resp}")
                continue

            positions = resp.get("data", [])
            if not isinstance(positions, list):
                logger.warning(f"[FETCH_POS_VOL_INVALID_DATA] endpoint={endpoint}")
                continue

            # Pozisyon ID'ye göre ara
            for pos in positions:
                pos_id = pos.get("positionId")
                if str(pos_id)==str(position_id):
                    hold_vol = float(pos.get("holdVol", 0))
                    if hold_vol > 0:
                        logger.info(f"[FETCH_POS_VOL_OK] pid={position_id} vol={hold_vol} endpoint={endpoint}")
                        return hold_vol
                    else:
                        logger.warning(f"[FETCH_POS_VOL_ZERO] pid={position_id} endpoint={endpoint}")
                        return None

            logger.warning(f"[FETCH_POS_VOL_NOT_FOUND] pid={position_id} endpoint={endpoint} total_positions={len(positions)}")

        except Exception as e:
            logger.error(f"[FETCH_POS_VOL_ERR] endpoint={endpoint} pid={position_id} err={e}")
            continue

    logger.error(f"[FETCH_POS_VOL_ALL_FAILED] pid={position_id}")
    return None


def build_mexc_trailing_sl_adapter(user_client_factory,
                                   user_id: int,
                                   leverage: int = 20):
    """
    Dışarıya adapter_update_sl_fn döndürür:
    adapter(pid, symbol, new_sl_price, side) -> bool
    user_client_factory(): her çağrıda imzalı client verir (veya reuse).
    """

    def _adapter(position_id: int, symbol: str, new_sl_price: float, side: str) -> bool:
        client = user_client_factory()
        # 1) Stop listesi çek – retry
        stops = _mexc_fetch_open_stop_orders(client, position_id=position_id, symbol=symbol)
        if not stops:
            for _ in range(SL_CANCEL_RETRY):
                time.sleep(SL_CANCEL_RETRY_SLEEP)
                stops = _mexc_fetch_open_stop_orders(client, position_id=position_id, symbol=symbol)
                if stops:
                    break

        logger.info(f"[TRAIL_UPD_STOP_LIST] pid={position_id} sym={symbol} fetched={len(stops)}")

        # StopLoss kayıtlarını ayıkla
        stop_loss_orders = []
        for s in stops:
            if s.get("stopLossPrice") is not None:
                stop_loss_orders.append(s)

        # CANCEL_MODE = "before"
        if CANCEL_MODE == "before":
            for o in stop_loss_orders:
                sid = o.get("stopOrderId") or o.get("id") or o.get("orderId")
                if sid:
                    _mexc_cancel_stop_order(client, sid)

            # Place new
            # Volume: pozisyon hacmini yeniden fetch etmek isterseniz burada yapın
            vol = _fetch_position_volume(client, position_id)
            if not vol:
                logger.warning(f"[TRAIL_UPD_ABORT] pid={position_id} vol_not_found")
                return False
            oid = _mexc_place_stop_loss(client, position_id, symbol, side, new_sl_price, vol)
            if oid:
                try:
                    state = _get_active_state_safe(int(user_id), "mexc", symbol)

                    sm = __import__("signal_merkezi", fromlist=["normalize_symbol_for_key"])
                    norm_sym_key = sm.normalize_symbol_for_key(symbol)

                    # burada gönderilecek fiyat: bu fonksiyonda quantize etmediysen new_sl_price'ı gönder
                    on_sl_move(int(user_id), "mexc", norm_sym_key, float(new_sl_price), "TRAIL", state)

                except Exception as hook_err:
                    logger.warning(f"[TRAIL_ADAPTER_HOOK_WARN] user={user_id} pid={position_id} err={hook_err}")

                logger.info(f"[MEXC_TRAIL_UPDATED] user={user_id} pid={position_id} price={new_sl_price}")
                return True

            return False

        else:  # "after" stratejisi örneği
            vol = _fetch_position_volume(client, position_id)
            if not vol:
                logger.warning(f"[TRAIL_UPD_ABORT] pid={position_id} vol_not_found")
                return False
            oid_new = _mexc_place_stop_loss(client, position_id, symbol, side, new_sl_price, vol)
            if not oid_new:
                return False
            # Eski stopları iptal
            for o in stop_loss_orders:
                sid = o.get("stopOrderId") or o.get("id") or o.get("orderId")
                if sid:
                    _mexc_cancel_stop_order(client, sid)
            logger.info(f"[MEXC_TRAIL_UPDATED] user={user_id} pid={position_id} price={new_sl_price}")
            try:
                state = _get_active_state_safe(int(user_id), "mexc", symbol)

                sm = __import__("signal_merkezi", fromlist=["normalize_symbol_for_key"])
                norm_sym_key = sm.normalize_symbol_for_key(symbol)

                on_sl_move(int(user_id), "mexc", norm_sym_key, float(new_sl_price), "TRAIL", state)
            except Exception as hook_err:
                logger.warning(f"[TRAIL_ADAPTER_HOOK_WARN] user={user_id} pid={position_id} err={hook_err}")

            return True

    return _adapter


class TelegramBotManager:
    _instance = None
    _initialized = False
    _bot = None

    def __init__(self):
        if not TelegramBotManager._initialized:
            self._initialize()

    @property
    def bot(self):
        """Protected _bot member için kontrollü erişim"""
        return self._bot

    @classmethod
    def _initialize(cls):
        if not cls._initialized:
            try:
                cls._bot = Bot(BOT_TOKEN)
                cls._initialized = True
                logger.info("✅ Telegram Bot başarıyla başlatıldı")
            except Exception as e:
                logger.error(f"❌ Telegram Bot başlatma hatası: {str(e)}")
                cls._initialized = False

    @classmethod
    async def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def verify_chat_access(self, chat_id):
        return await self._bot.get_chat(chat_id)

    async def send_notification(
            self,
            chat_id: int,
            message: str,
            reply_markup: Optional[InlineKeyboardMarkup] = None
    ) -> bool:
        try:
            if not self._initialized or not self._bot:
                self._initialize()
                if not self._initialized:
                    raise Exception("Bot başlatılamadı")

            await self._bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            return True
        except Exception as e:
            logger.error(f"❌ Bildirim gönderme hatası: {str(e)}")
            return False


async def send_trade_notification(user_id: int, signal_dict: dict, position_result: dict) -> bool:
    try:
        bot_manager = await TelegramBotManager.get_instance()

        # ✅ Hedef her zaman DM
        dm_chat_id = int(user_id)

        # ✅ Kanal var mı? Sadece kontrol amaçlı (istersen log/iş kuralı için)
        has_channel = False
        channel_id = None
        try:
            channel_query = """
            SELECT channel_id FROM user_channel_info 
            WHERE user_id = ? AND LOWER(exchange) = 'mexc'
            LIMIT 1
            """
            channel_result = db_operation(channel_query, (user_id,), fetch=True)

            if channel_result and channel_result[0] and channel_result[0][0]:
                has_channel = True
                channel_id = channel_result[0][0]

                # İstersen kanalı doğrula (zorunlu değil)
                # await bot_manager.verify_chat_access(int(channel_id))

        except Exception as e:
            # Kanal kontrol hatası DM göndermeyi engellemesin
            logger.warning(f"Kanal kontrol hatası (DM devam): {str(e)}")

        notification_message = format_notification_message(signal_dict, position_result)
        reply_markup = create_emergency_button(user_id, signal_dict)

        # ✅ Her koşulda DM'e gönder
        success = await bot_manager.send_notification(dm_chat_id, notification_message, reply_markup)

        if success:
            logger.info(
                f"Bildirim DM gönderildi - User ID: {user_id}, DM Chat ID: {dm_chat_id}, "
                f"channel_exists={has_channel}, channel_id={channel_id}"
            )
        return success

    except Exception as e:
        logger.error(f"Bildirim gönderme hatası: {str(e)}", exc_info=True)
        return False


def format_notification_message(signal_dict: dict, position_result: dict) -> str:
    """Bildirim mesajını formatlar"""
    position_emoji = "🟢" if signal_dict.get('position_type', '').upper() == "LONG" else "🔴"
    result_emoji = "✅" if position_result.get('success', False) else "❌"

    return (
        f"🔔 MEXC İşlem Bildirimi\n\n"
        f"📊 Sembol: {signal_dict.get('symbol', 'Belirtilmemiş')}\n"
        f"{position_emoji} Pozisyon: {signal_dict.get('position_type', 'Belirtilmemiş')}\n"
        f"💵 Giriş Fiyatları: {signal_dict.get('entry_points', [])}\n"
        f"🛑 Stop Loss: {signal_dict.get('stop_loss', 'Belirtilmemiş')}\n"
        f"🎯 Hedefler:\n{format_take_profits(signal_dict.get('take_profits'))}\n\n"
        f"💰 Lot: {signal_dict.get('lot', 'Belirtilmemiş')}\n"
        f"⚡ Kaldıraç: {signal_dict.get('leverage', '10')}x\n\n"
        f"📝 Sonuç: {result_emoji} "
        f"{'Başarılı' if position_result.get('success', False) else 'Başarısız'}\n"
        f"⏰ İşlem Zamanı: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"ℹ️ Detay: {position_result.get('error', 'İşlem tamamlandı')}"
    )


def format_take_profits(take_profits) -> str:
    """TP listesini formatlar"""
    if isinstance(take_profits, list):
        return "\n".join([f"{i + 1}. Hedef = {tp}" for i, tp in enumerate(take_profits)])
    return take_profits or "Belirtilmemiş"


def create_emergency_button(user_id: int, signal_dict: dict) -> InlineKeyboardMarkup:
    """Acil kapatma butonu oluşturur"""
    keyboard = [[
        InlineKeyboardButton(
            "🚨 ACİL KAPAT 🚨",
            callback_data=json.dumps({
                "a": "ec",  # action yerine
                "s": signal_dict.get('symbol'),
                "d": signal_dict.get('position_type')[0],  # L veya S
                "u": user_id
            })
        )
    ]]
    return InlineKeyboardMarkup(keyboard)


async def handle_emergency_close_callback(update: Update, _context: CallbackContext):
    query = None
    try:
        query = update.callback_query
        if query is None:
            return

        await query.answer()

        # 1) JSON parse
        try:
            data = json.loads(query.data or "{}")
        except Exception:
            await query.edit_message_text("❌ Geçersiz callback verisi (JSON okunamadı).")
            return

        # 2) Action kontrol
        if data.get("a") != "ec":
            return  # bu handler sadece emergency close içindir

        # 3) Symbol
        symbol = (data.get("s") or "").strip()
        if not symbol:
            await query.edit_message_text("❌ Sembol bilgisi boş.")
            return

        # 4) Side (L/S)
        side_short = (data.get("d") or "").upper().strip()
        if side_short not in ("L", "S"):
            await query.edit_message_text("❌ Yön bilgisi hatalı (L/S yok).")
            return
        side = "LONG" if side_short == "L" else "SHORT"

        # 5) user_id
        try:
            user_id = int(data.get("u"))
        except Exception:
            await query.edit_message_text("❌ Kullanıcı bilgisi okunamadı.")
            return

        # 6) web_key kontrol (isteğe bağlı ama hızlı fail için iyi)
        web_key = get_mexc_web_key(int(user_id))
        if not web_key:
            await query.edit_message_text("❌ Web anahtarı (web_key) bulunamadı!")
            return

        # 7) Kapatma işlemi
        close_result = await mexc_close_position(
            symbol=symbol,
            side=side,
            user_id=user_id
        )

        if close_result.get("success"):
            await query.edit_message_text("🟢 Pozisyon kapatıldı")
        else:
            await query.edit_message_text(
                f"🔴 Kapatma hatası: {close_result.get('error', 'Bilinmeyen')}"
            )
        return

    except Exception as e:
        logger.error(f"[EMERGENCY_CLOSE_ERR] {e}", exc_info=True)
        if query:
            try:
                await query.edit_message_text("❌ Acil kapatma hatası!")
            except Exception as inner_e:
                logger.error(f"Acil kapatma sonrası mesaj düzenleme hatası: {inner_e}")
        return
