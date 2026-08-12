# settings/execution_settings_plan.py
from __future__ import annotations
from typing import Any, Dict, List, Optional, Callable
from settings.execution_plan_types import ExecutionPlan, SymbolMeta
import math
import json
import logging

logger = logging.getLogger(__name__)

def apply_symbol_alias(symbol_core: str, exchange: str) -> str:
    """
    symbol_core: BTCUSDT gibi core format
    exchange: binance/mexc...
    dönüş: alias uygulanmış core format
    """
    ex = (exchange or "").lower().strip()

    # ConfigService sende başka yerdeyse import yolunu ona göre ayarla
    try:
        from config_service import ConfigService
        aliases = ConfigService.get(f"exchange_profiles.{ex}.symbol_aliases", {}) or {}
    except Exception:
        aliases = {}

    s = normalize_symbol_core(symbol_core)
    mapped = aliases.get(s)
    if not mapped:
        return s

    return normalize_symbol_core(mapped)


def normalize_symbol_core(symbol: str) -> str:
    s = (symbol or "").upper().strip()
    s = s.split(":", 1)[0]
    s = s.replace("/USDT", "USDT").replace("/", "")
    if s.endswith("_USDT"):
        s = s.replace("_USDT", "USDT")
    return s


def symbol_for_exchange(symbol_core: str, exchange: str) -> str:
    ex = (exchange or "").lower().strip()
    base = symbol_core.replace("USDT", "")
    if ex == "mexc":
        return f"{base}_USDT"
    if ex == "bitget":
        return f"{symbol_core}_UMCBL"
    if ex == "okx":
        # OKX executor validate_okx_symbol zaten dönüştürüyor ama plan.exchange symbolu da düzgün taşısın
        return symbol_core.replace("USDT", "-USDT")
    # bybit/binance default: BTCUSDT
    return symbol_core


def normalize_margin(v: Any) -> str:
    """
    DB/ayarlar.margin alanından gelen marjin tipini normalize eder.
    Çıktı sadece: 'CROSS' veya 'ISOLATED'
    """
    raw = str(v or "CROSS").strip().upper()

    # Türkçe İ/ı normalize
    raw = raw.replace("İ", "I").replace("ı", "I")

    if not raw:
        return "CROSS"

    raw2 = raw.replace("-", "_").replace(" ", "_")

    # CROSS varyantları
    if raw2 in ("CROSS", "CROSSED", "CROSS_MARGIN", "CROSSMARGIN"):
        return "CROSS"

    # ISOLATED varyantları
    if raw2 in ("ISOLATED", "ISOLATE", "ISO", "ISOLATION", "ISOLATED_MARGIN"):
        return "ISOLATED"

    # İçerik yakalama
    if "ISOL" in raw2:
        return "ISOLATED"
    if "CROSS" in raw2:
        return "CROSS"

    return "CROSS"


def _as_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _as_int(x: Any, default: int = 0) -> int:
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return default


def _resolve_trade_notional_usdt(settings: Dict[str, Any], user: Dict[str, Any]) -> float:

    v = _as_float(settings.get("lot", None), 0.0)
    if v <= 0:
        v = _as_float(user.get("lot", None), 0.0)
    return v

def _round_to_step_floor(x: float, step: float) -> float:
    if step <= 0:
        return x
    return math.floor(x / step) * step

def _round_to_step_ceil(x: float, step: float) -> float:
    if step <= 0:
        return x
    return math.ceil(x / step) * step


def apply_lot_esneme_kurali(
    *,
    target_notional: float,
    entry_price: float,
    min_amount: float,
    amount_step: float,
    max_uplift_pct: float = 50.0
) -> Dict[str, float]:
    """
    Kural:
      - target_notional ile dene
      - min_amount için gereken notional > target ise, en fazla %50 artır.
      - Aşarsa reject (bu fonksiyon reject etmez, sadece gerekli değerleri döndürür)

    Dönüş:
      {
        "target_notional": ...,
        "max_allowed_notional": ...,
        "min_required_notional": ...,
        "effective_notional": ...
      }
    """
    target_notional = _as_float(target_notional, 0.0)
    entry_price = _as_float(entry_price, 0.0)
    min_amount = _as_float(min_amount, 0.0)
    amount_step = _as_float(amount_step, 1.0)

    if entry_price <= 0 or target_notional <= 0:
        return {
            "target_notional": target_notional,
            "max_allowed_notional": target_notional,
            "min_required_notional": 0.0,
            "effective_notional": target_notional
        }

    # min_amount step'e otursun (yukarı)
    if min_amount <= 0:
        min_amount = amount_step if amount_step > 0 else 1.0
    min_amount = _round_to_step_ceil(min_amount, amount_step)

    min_required_notional = entry_price * min_amount
    max_allowed_notional = target_notional * (1.0 + max_uplift_pct / 100.0)

    effective_notional = target_notional
    if min_required_notional > target_notional:
        # min'i karşılamak için notional yükseltmemiz gerekiyor
        effective_notional = min_required_notional

    return {
        "target_notional": float(target_notional),
        "max_allowed_notional": float(max_allowed_notional),
        "min_required_notional": float(min_required_notional),
        "effective_notional": float(effective_notional),
    }


def _tp_structs_equal(signal_tps: List[float]) -> List[Dict[str, Any]]:
    if not signal_tps:
        return []
    n = len(signal_tps)
    pct = 100.0 / n
    out = [{"price": float(p), "percent": pct} for p in signal_tps]
    s = sum(x["percent"] for x in out)
    out[-1]["percent"] += (100.0 - s)
    return out


def build_execution_plan(
    *,
    user: Dict[str, Any],
    signal_dict: Dict[str, Any],
    settings: Dict[str, Any],
    get_symbol_meta_fn: Callable[[str], Optional[Dict[str, Any]]],
    compute_notional_to_contracts_fn: Callable[..., float],
    calculate_take_profits_fn: Callable[..., List[float]],
) -> ExecutionPlan:

    user_id = int(user["user_id"])
    username = str(user.get("username") or settings.get("username") or f"user_{user_id}")
    exchange = str(user.get("exchange") or settings.get("exchange") or "").lower().strip()

    side = (signal_dict.get("position_type") or "").upper().strip()
    if side not in ("LONG", "SHORT"):
        raise ValueError(f"invalid side: {side}")

    symbol_core_raw = normalize_symbol_core(signal_dict.get("symbol"))
    symbol_core = apply_symbol_alias(symbol_core_raw, exchange)
    symbol_ex = symbol_for_exchange(symbol_core, exchange)

    entry_points = signal_dict.get("entry_points") or []
    entry_price = _as_float(entry_points[0] if entry_points else signal_dict.get("entry_price"), 0.0)
    if entry_price <= 0:
        raise ValueError("entry_price invalid")

    signal_stop = signal_dict.get("stop_loss")
    signal_tps = [float(x) for x in (signal_dict.get("take_profits") or []) if x is not None]

    # ============================================================
    # DB ayarlar.lot => margin_usdt (işleme ayrılan teminat hedefi)
    # ============================================================
    margin_usdt = _resolve_trade_notional_usdt(settings, user)
    if margin_usdt <= 0:
        margin_usdt = 10.0  # default margin

    leverage = _as_int(settings.get("leverage", user.get("leverage", 10)), 10)
    leverage = max(1, leverage)

    # margin_type (CROSSED/CROSS vs ISOLATED) -> CROSS/ISOLATED
    margin = normalize_margin(settings.get("margin", user.get("margin", "CROSS")))

    # =========================
    # META
    # =========================
    meta_raw = get_symbol_meta_fn(symbol_ex) or get_symbol_meta_fn(symbol_core)
    if not meta_raw:
        raise ValueError(f"symbol meta not found for {symbol_ex} (core={symbol_core})")

    # Futures/contract için kritik alanlar:
    # - contract_size: 1 kontrat kaç base coin (örn BNB'de 0.01)
    # - min_notional/min_cost: minimum order amount (USDT) (örn 9.134)
    contract_size = _as_float(
        meta_raw.get("contract_size", meta_raw.get("contractSize", 1.0)),
        1.0
    )
    if contract_size <= 0:
        contract_size = 1.0

    min_notional_usdt = _as_float(
        meta_raw.get("min_notional", meta_raw.get("min_cost", meta_raw.get("minCost", 0.0))),
        0.0
    )

    meta = SymbolMeta(
        price_step=_as_float(meta_raw.get("price_step"), 0.0001),
        amount_step=_as_float(meta_raw.get("amount_step"), 1.0),
        price_decimals=_as_int(meta_raw.get("price_decimals"), 4),
        amount_decimals=_as_int(meta_raw.get("amount_decimals"), 0),
        min_amount=_as_float(meta_raw.get("min_amount"), _as_float(meta_raw.get("amount_step"), 1.0)),
        ccxt_symbol=meta_raw.get("ccxt_symbol"),
    )

    # ============================================================
    # ✅ İSTENEN MODEL:
    #   - DB lot = MARGIN hedefi (USDT)
    #   - NOTIONAL hedefi = margin_usdt * leverage
    #   - %50 sigorta min kontrat için NOTIONAL üstünde çalışır
    # ============================================================
    target_margin_usdt = float(margin_usdt)
    target_notional_usdt = float(target_margin_usdt * float(leverage))
    max_allowed_notional = float(target_notional_usdt * 1.5)  # %50 esneme tavanı

    # --- contracts hesap parametreleri (step/min align) ---
    # Buradaki min_amount/amount_step "contract" adedi içindir (futures)
    min_amount = float(meta.min_amount or meta.amount_step or 1.0)
    amount_step = float(meta.amount_step or 1.0)
    if amount_step <= 0:
        amount_step = 1.0

    min_units = math.ceil(min_amount / amount_step)
    min_amount_step_aligned = min_units * amount_step  # <-- contract sayısı

    # ============================================================
    # ✅ DÜZELTME: min_required_notional contract_size ile hesaplanmalı
    # ============================================================
    min_required_by_contract = float(entry_price * (min_amount_step_aligned * contract_size))

    # Borsa "Minimum Order Amount" veriyorsa onu da zorunlu alt limit yap
    min_required_notional = float(max(min_required_by_contract, min_notional_usdt or 0.0))

    effective_notional = float(target_notional_usdt)
    uplift_used_notional = 0.0

    if min_required_notional > target_notional_usdt:
        if min_required_notional > max_allowed_notional:
            raise ValueError(
                "LOT_YETERSIZ_%50_CAP: "
                f"margin_target={target_margin_usdt:.4f} "
                f"target_notional={target_notional_usdt:.4f} "
                f"min_required≈{min_required_notional:.4f} "
                f"max_allowed≈{max_allowed_notional:.4f} "
                f"(entry={entry_price} min_amount={min_amount_step_aligned} step={amount_step} "
                f"contract_size={contract_size} min_notional={min_notional_usdt}) "
                f"symbol={symbol_ex}"
            )
        effective_notional = float(min_required_notional)
        uplift_used_notional = float(effective_notional - target_notional_usdt)

    # ============================================================
    # contracts hesabı:
    # compute_notional_to_contracts_fn genelde "qty" döndürüyor.
    # Futures için qty "contract" olmalı.
    # Bunun için entry_price yerine entry_price*contract_size kullanıyoruz.
    # Böylece: contracts ≈ notional / (entry*contract_size)
    # ============================================================
    contracts = compute_notional_to_contracts_fn(
        entry_price=float(entry_price * contract_size),
        fixed_notional_usdt=effective_notional,
        amount_step=amount_step,
        min_amount=min_amount_step_aligned
    )
    contracts = _as_float(contracts, 0.0)

    if contracts <= 0:
        contracts = float(min_amount_step_aligned)

    if contracts <= 0:
        raise ValueError(
            "contracts calc failed: "
            f"margin_target={target_margin_usdt} "
            f"target_notional={target_notional_usdt} "
            f"effective_notional={effective_notional} "
            f"entry={entry_price} step={amount_step} min={min_amount_step_aligned} "
            f"contract_size={contract_size} symbol={symbol_ex}"
        )

    # =========================
    # SL/TP settings
    # =========================
    sl_tp_emir = str(settings.get("sl_tp_emir", "off")).lower() == "on"

    sl_mode = str(settings.get("stop_loss", "off")).lower().strip()
    sl_pct = _as_float(settings.get("sl_percentage", 0.0), 0.0)

    sl_price: Optional[float] = None
    if sl_tp_emir:
        if sl_mode == "fixed":
            sl_price = _as_float(signal_stop, 0.0) or None
        elif sl_mode == "percentage" and sl_pct > 0:
            if side == "LONG":
                sl_price = entry_price * (1.0 - sl_pct / 100.0)
            else:
                sl_price = entry_price * (1.0 + sl_pct / 100.0)

    tp_structs: List[Dict[str, Any]] = []
    tp_mode = str(settings.get("take_profit", "SINYAL_TP")).upper().strip()

    if sl_tp_emir and not signal_tps:
        try:
            signal_tps = calculate_take_profits_fn(
                entry_price=entry_price,
                side=side,
                tp_percentages=None,
                signal_tp_prices=None
            )
        except (TypeError, ValueError):
            signal_tps = []

    if sl_tp_emir and signal_tps:
        if tp_mode != "SINYAL_TP":
            n = _as_int(tp_mode, 1)
            n = max(1, min(10, n))
            if len(signal_tps) >= n:
                tp_structs = [{"price": float(signal_tps[n - 1]), "percent": 100.0}]
        else:
            tp_structs = _tp_structs_equal([float(x) for x in signal_tps])

    plan = ExecutionPlan(
        user_id=user_id,
        username=username,
        exchange=exchange,
        symbol_core=symbol_core,
        symbol_exchange=symbol_ex,
        side=side,  # type: ignore
        entry_price=entry_price,
        signal_stop_loss=_as_float(signal_stop, 0.0) or None,
        signal_take_profits=signal_tps,
        leverage=leverage,
        margin=margin,

        # lot_notional = margin bütçesi
        lot_notional=float(target_margin_usdt),

        # contracts = contract adedi
        contracts=contracts,

        sl_tp_emir=sl_tp_emir,
        sl_price=sl_price if sl_tp_emir else None,
        tp_structs=tp_structs if sl_tp_emir else [],
        terial_stop=str(settings.get("terial_stop", "off")),
        maliyet_cek=str(settings.get("maliyet_cek", "off")),
        meta=meta,
        debug={
            "tp_mode": tp_mode,
            "sl_mode": sl_mode,
            "sl_pct": sl_pct,

            "margin_usdt_target": float(target_margin_usdt),
            "notional_usdt_target": float(target_notional_usdt),

            "effective_notional_usdt": float(effective_notional),
            "uplift_used_notional": float(uplift_used_notional),

            "leverage": int(leverage),
            "margin_mode": margin,

            "min_required_notional": float(min_required_notional),
            "max_allowed_notional": float(max_allowed_notional),
            "min_amount_step_aligned": float(min_amount_step_aligned),
            "amount_step": float(amount_step),

            # yeni debug alanları
            "contract_size": float(contract_size),
            "min_notional_usdt": float(min_notional_usdt),
            "min_required_by_contract": float(min_required_by_contract),

            "symbol_core_raw":symbol_core_raw,
            "symbol_core_aliased":symbol_core,
            "symbol_exchange_final":symbol_ex,
        }
    )

    try:
        from settings.execution_plan_serialization import execution_plan_to_dict
        logger.info("[EXEC_PLAN_PRETTY]\n" + json.dumps(execution_plan_to_dict(plan), ensure_ascii=False, indent=2))
    except Exception as e:
        logger.warning(f"[EXEC_PLAN_PRETTY_ERR] {e}")

    return plan


