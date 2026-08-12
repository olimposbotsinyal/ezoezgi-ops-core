# settings/trailing_manager.py
from __future__ import annotations

import asyncio
import bisect
import logging
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# =============================================================================
# Defaults (borsaya göre dışarıdan override edilebilir)
# =============================================================================

DEFAULT_TRAIL_LOOP_INTERVAL = 3.0

# Stop spam/koruma
DEFAULT_MIN_SL_UPDATE_INTERVAL = 20.0         # saniye
DEFAULT_MIN_INITIAL_TRAIL_DELAY = 5           # saniye (pozisyon açıldıktan sonra ilk trailing güncellemesi)
DEFAULT_MIN_SL_STEP_RATIO = 0.0002            # %0.02 gibi (çok ufak iyileştirmeyi engelle)
DEFAULT_FORCE_IMPROVE_TICKS = 8               # tick bazlı minimum iyileşme
DEFAULT_ALLOW_WORSE_SL = False                # LONG: SL aşağı inmesin, SHORT: SL yukarı çıkmasın

# İlk trailing için min kâr şartı (entry’den itibaren)
DEFAULT_TRAIL_MIN_PROFIT_PCT = 0.25           # %

# ATR
DEFAULT_ATR_PERIOD = 14

# Volatility PCTL
DEFAULT_ATR_HISTORY_WINDOW = 300
DEFAULT_ATR_PCTL_MIN_SAMPLES = 80

# =============================================================================
# Dependency types
# =============================================================================

FetchPriceFn = Callable[[str], Optional[float]]
UpdateSLFn = Callable[[int, str, float], bool]
GetMetaFn = Callable[[str], Optional[Dict[str, Any]]]
QuantizeFn = Callable[[str, float, str, str], float]  # (symbol, price, side, intent) -> price
TimeFn = Callable[[], float]


# =============================================================================
# Helpers
# =============================================================================

def _safe_float(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if x is None:
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def _now(time_fn: Optional[TimeFn] = None) -> float:
    return time_fn() if time_fn else time.time()


def _quantize_floor_to_tick(price: float, tick: float) -> float:
    if tick <= 0:
        return float(price)
    d_price = Decimal(str(price))
    d_tick = Decimal(str(tick))
    q = (d_price / d_tick).to_integral_value(rounding=ROUND_DOWN) * d_tick
    return float(q)


def _quantize_ceil_to_tick(price: float, tick: float) -> float:
    if tick <= 0:
        return float(price)
    d_price = Decimal(str(price))
    d_tick = Decimal(str(tick))
    q = (d_price / d_tick).to_integral_value(rounding=ROUND_UP) * d_tick
    return float(q)


def _default_quantize(symbol: str, price: float, side: str, intent: str) -> float:
    """
    quantize_fn verilmezse kullanılır: tick bilgisi yoksa dokunmaz.
    (Tick bilgisi için get_meta_fn gerekir; bu default fonksiyon meta kullanmaz.)
    """
    _ = symbol, side, intent
    return float(price)


# =============================================================================
# Trailing Manager
# =============================================================================

@dataclass
class LoopOptions:
    interval: float = DEFAULT_TRAIL_LOOP_INTERVAL
    min_sl_update_interval: float = DEFAULT_MIN_SL_UPDATE_INTERVAL
    allow_worse_sl: bool = DEFAULT_ALLOW_WORSE_SL
    min_sl_step_ratio: float = DEFAULT_MIN_SL_STEP_RATIO
    force_improve_ticks: int = DEFAULT_FORCE_IMPROVE_TICKS
    trail_min_profit_pct: float = DEFAULT_TRAIL_MIN_PROFIT_PCT


class TrailingManager:
    """
    Ortak trailing state makinesi.
    Borsa bağımlılıkları dışarıdan verilir (fetch_price, update_sl, meta, quantize).

    positions[position_id] = {
        symbol, side, mode, param, effective_param, original_param,
        entry, highest, lowest, current_sl, last_update,
        atr_period, candles, atr,
        break_even_conf, break_even_done,
        tp_prices, tp_be_conf, filled_tp_count,
        dyn_trail_conf, dyn_applied_profit_idx, dyn_applied_tp_idx,
        vol_conf, atr_short, atr_long, atr_history, vol_smooth_mult,
        last_vol_update_ts, last_vol_multiplier,
        trail_not_before,
        initial_vol, last_vol
    }
    """

    def __init__(self, *, time_fn: Optional[TimeFn] = None):
        self.positions: Dict[int, Dict[str, Any]] = {}
        self._time_fn = time_fn

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def register(
        self,
        position_id: int,
        symbol: str,
        side: str,
        mode: str,
        param: float,
        entry_price: float,
        *,
        atr_period: int = DEFAULT_ATR_PERIOD,
        break_even_conf: Optional[Dict[str, Any]] = None,
        tp_prices: Optional[List[float]] = None,
        initial_vol: Optional[float] = None,
        tp_be_conf: Optional[Dict[str, Any]] = None,
        dynamic_trailing_conf: Optional[Dict[str, Any]] = None,
        volatility_conf: Optional[Dict[str, Any]] = None,
        initial_sl: Optional[float] = None,
        initial_trail_delay: int = DEFAULT_MIN_INITIAL_TRAIL_DELAY,
    ) -> None:
        pid = int(position_id)
        side_u = (side or "").upper().strip()
        if side_u not in ("LONG", "SHORT"):
            raise ValueError(f"invalid side: {side}")

        mode_u = (mode or "").upper().strip()
        if mode_u not in ("PCT", "ATR"):
            raise ValueError(f"invalid mode: {mode}")

        p = float(param)
        entry = float(entry_price)
        if entry <= 0:
            raise ValueError("entry_price must be > 0")

        tp_sorted: List[float] = []
        if tp_prices:
            try:
                tp_sorted = sorted([float(x) for x in tp_prices], reverse=(side_u == "SHORT"))
            except Exception:
                tp_sorted = []

        st: Dict[str, Any] = {
            "symbol": str(symbol),
            "side": side_u,
            "mode": mode_u,
            "param": p,
            "effective_param": p,
            "original_param": p,

            "entry": entry,
            "highest": entry,
            "lowest": entry,

            "current_sl": float(initial_sl) if initial_sl is not None else None,
            "last_update": 0.0,

            "atr_period": int(atr_period),
            "candles": [],
            "atr": None,

            "break_even_conf": break_even_conf or {"enabled": False},
            "break_even_done": False,

            "tp_prices": tp_sorted,
            "tp_be_conf": tp_be_conf or {"enabled": False},
            "filled_tp_count": 0,

            "dyn_trail_conf": dynamic_trailing_conf or {"enabled": False},
            "dyn_applied_profit_idx": -1,
            "dyn_applied_tp_idx": -1,

            "vol_conf": volatility_conf or {"enabled": False},
            "atr_short": None,
            "atr_long": None,
            "atr_history": [],
            "vol_smooth_mult": None,
            "vol_last_regime": None,
            "vol_regime_hits": {},
            "last_vol_update_ts": 0.0,
            "last_vol_multiplier": 1.0,

            "initial_vol": float(initial_vol) if initial_vol is not None else None,
            "last_vol": float(initial_vol) if initial_vol is not None else None,

            "trail_not_before": _now(self._time_fn) + max(0, int(initial_trail_delay)),
        }

        self.positions[pid] = st
        logger.info(
            f"[TRAIL_REGISTER] pid={pid} sym={symbol} side={side_u} mode={mode_u} "
            f"param={p} initial_sl={initial_sl} delay={initial_trail_delay}s"
        )

    def unregister(self, position_id: int) -> None:
        pid = int(position_id)
        if pid in self.positions:
            logger.info(f"[TRAIL_UNREGISTER] pid={pid}")
            self.positions.pop(pid, None)

    def remove(self, position_id: int) -> None:
        self.unregister(position_id)

    def update_price(self, position_id: int, last_price: float) -> None:
        """
        Tek bir fiyat tick’i ile internal state’i günceller (highest/lowest, ATR candle, BE vs).
        """
        pid = int(position_id)
        st = self.positions.get(pid)
        if not st:
            return

        p = float(last_price)
        if p <= 0:
            return

        side = st["side"]
        if side == "LONG":
            if p > st["highest"]:
                st["highest"] = p
            if p < st["lowest"]:
                st["lowest"] = p
        else:
            if p < st["lowest"]:
                st["lowest"] = p
            if p > st["highest"]:
                st["highest"] = p

        # ATR / vol_conf gerekiyorsa candle ekle
        if st["mode"] == "ATR" or (st.get("vol_conf") or {}).get("enabled"):
            self._append_price_as_candle(st, p)

        # Break-even & dynamic trailing
        self._check_break_even_distance(st, p)
        self._check_tp_break_even(st)
        self._maybe_apply_dynamic_trailing(st, p)
        self._maybe_update_volatility(st)

    def update_position_volume(self, position_id: int, current_vol: float, *, min_step: float = 1.0) -> None:
        """
        Hacim düşüşünden TP vuruldu varsayımı (çok kaba ama pratik).
        """
        pid = int(position_id)
        st = self.positions.get(pid)
        if not st:
            return

        vol = _safe_float(current_vol)
        if vol is None or vol <= 0:
            return

        if st.get("last_vol") is None:
            st["last_vol"] = vol
            return

        last_vol = float(st["last_vol"])
        if vol >= last_vol:
            st["last_vol"] = vol
            return

        diff = last_vol - vol
        step = max(float(min_step), 1e-9)
        steps = int(diff / step)
        if steps <= 0:
            st["last_vol"] = vol
            return

        st["filled_tp_count"] = int(st.get("filled_tp_count", 0)) + steps
        st["last_vol"] = vol
        logger.info(f"[PARTIAL_DETECT] pid={pid} vol_drop={diff} steps={steps} filled_tp={st['filled_tp_count']}")

    def calc_new_sl(self, position_id: int) -> Optional[float]:
        """
        Mevcut state'e göre yeni SL adayı üretir.
        """
        pid = int(position_id)
        st = self.positions.get(pid)
        if not st:
            return None

        mode = st["mode"]
        side = st["side"]
        param = st.get("effective_param")
        if param is None:
            param = st.get("param")
        if param is None:
            return None

        if mode == "PCT":
            pct = float(param)
            if pct <= 0:
                return None
            if side == "LONG":
                return float(st["highest"]) * (1.0 - pct / 100.0)
            return float(st["lowest"]) * (1.0 + pct / 100.0)

        if mode == "ATR":
            atr = st.get("atr")
            if atr is None:
                return None
            mult = float(param)
            if mult <= 0:
                return None
            last_close = st["candles"][-1]["c"] if st["candles"] else st["entry"]
            if side == "LONG":
                return float(last_close) - float(atr) * mult
            return float(last_close) + float(atr) * mult

        return None

    def should_update_sl(
        self,
        position_id: int,
        new_sl: float,
        meta: Optional[Dict[str, Any]],
        *,
        opts: LoopOptions,
    ) -> bool:
        pid = int(position_id)
        st = self.positions.get(pid)
        if not st:
            return False

        now = _now(self._time_fn)

        # initial delay
        if now < float(st.get("trail_not_before", 0.0) or 0.0):
            return False

        # min interval
        last_upd = float(st.get("last_update", 0.0) or 0.0)
        if last_upd != 0.0 and (now - last_upd) < float(opts.min_sl_update_interval):
            return False

        cur = st.get("current_sl")
        side = st["side"]
        entry = float(st["entry"])

        # worse SL?
        if cur is not None and not opts.allow_worse_sl:
            if side == "LONG" and new_sl <= cur:
                return False
            if side == "SHORT" and new_sl >= cur:
                return False

        # first update min profit
        if last_upd == 0.0 and entry > 0:
            if side == "LONG":
                profit_pct = (new_sl - entry) / entry * 100.0
            else:
                profit_pct = (entry - new_sl) / entry * 100.0
            if profit_pct < float(opts.trail_min_profit_pct):
                return False

        # min step ratio spam guard
        if cur is not None and cur != 0:
            if side == "LONG":
                if (new_sl - cur) / cur < float(opts.min_sl_step_ratio):
                    return False
            else:
                if (cur - new_sl) / cur < float(opts.min_sl_step_ratio):
                    return False

        # tick improve check
        tick = None
        if meta and isinstance(meta, dict):
            tick = _safe_float(meta.get("price_step"))
        if cur is not None and tick is not None and tick > 0:
            tick_diff = abs(new_sl - cur) / tick
            if tick_diff < int(opts.force_improve_ticks):
                return False

        return True

    def mark_sl_updated(self, position_id: int, sl_price: float) -> None:
        pid = int(position_id)
        st = self.positions.get(pid)
        if not st:
            return
        st["current_sl"] = float(sl_price)
        st["last_update"] = _now(self._time_fn)

    # -------------------------------------------------------------------------
    # Internal: ATR
    # -------------------------------------------------------------------------

    def _append_price_as_candle(self, st: Dict[str, Any], price: float) -> None:
        candles = st["candles"]
        candles.append({"h": price, "l": price, "c": price})
        max_keep = int(st["atr_period"]) * 3
        if len(candles) > max_keep:
            del candles[0 : len(candles) - max_keep]
        self._recalc_atr(st)

    def _recalc_atr(self, st: Dict[str, Any]) -> None:
        period = int(st["atr_period"])
        candles = st["candles"]
        if len(candles) < period + 1:
            return

        tr: List[float] = []
        for i in range(1, len(candles)):
            h = float(candles[i]["h"])
            l = float(candles[i]["l"])
            prev_c = float(candles[i - 1]["c"])
            tr_val = max(h - l, abs(h - prev_c), abs(l - prev_c))
            tr.append(tr_val)

        if len(tr) < period:
            return

        if st["atr"] is None:
            st["atr"] = sum(tr[-period:]) / period
        else:
            last_tr = tr[-1]
            st["atr"] = ((float(st["atr"]) * (period - 1)) + last_tr) / period

        # volatility conf için ek hesaplar
        vol_conf = st.get("vol_conf", {}) or {}
        if vol_conf.get("enabled"):
            method = str(vol_conf.get("method", "")).upper()
            if method == "ATR_RATIO":
                long_p = int(vol_conf.get("long_period", 50) or 50)
                if len(tr) >= long_p:
                    st["atr_long"] = sum(tr[-long_p:]) / long_p
                st["atr_short"] = float(st["atr"])
            if method in ("ATR_RATIO", "ATR_PCTL"):
                self._after_atr(st)

    def _after_atr(self, st: Dict[str, Any]) -> None:
        vol_conf = st.get("vol_conf", {}) or {}
        if not vol_conf.get("enabled"):
            return

        method = str(vol_conf.get("method", "")).upper()
        if method == "ATR_PCTL":
            atr_val = st.get("atr")
            if atr_val is None:
                return
            win = int(vol_conf.get("window", DEFAULT_ATR_HISTORY_WINDOW) or DEFAULT_ATR_HISTORY_WINDOW)
            hist = st.get("atr_history")
            if not isinstance(hist, list):
                hist = []
                st["atr_history"] = hist
            hist.append(float(atr_val))
            if len(hist) > win:
                del hist[0 : len(hist) - win]

    # -------------------------------------------------------------------------
    # Internal: Break-even
    # -------------------------------------------------------------------------

    def _check_break_even_distance(self, st: Dict[str, Any], last_price: float) -> None:
        conf = st.get("break_even_conf", {}) or {}
        if not conf.get("enabled"):
            return
        if st.get("break_even_done"):
            return

        entry = float(st["entry"])
        side = st["side"]
        trigger_type = str(conf.get("trigger_type", "ATR")).upper()
        trigger_val = float(conf.get("trigger_value", 2.0) or 2.0)
        offset_pct = float(conf.get("offset_pct", 0.0) or 0.0)

        triggered = False
        if trigger_type == "ATR":
            atr = st.get("atr")
            if atr is None:
                return
            if side == "LONG" and last_price >= entry + float(atr) * trigger_val:
                triggered = True
            elif side == "SHORT" and last_price <= entry - float(atr) * trigger_val:
                triggered = True

        elif trigger_type == "PCT":
            if side == "LONG":
                gain_pct = (last_price - entry) / entry * 100.0
                triggered = gain_pct >= trigger_val
            else:
                gain_pct = (entry - last_price) / entry * 100.0
                triggered = gain_pct >= trigger_val
        else:
            return

        if not triggered:
            return

        be_sl = self._compute_be_price(side, entry, last_price, offset_pct, st.get("current_sl"))
        st["current_sl"] = be_sl
        st["break_even_done"] = True
        st["last_update"] = 0.0  # hemen update etmeye izin ver
        logger.info(f"[BE_TRIGGER] sym={st['symbol']} side={side} be_sl={be_sl}")

    @staticmethod
    def _compute_be_price(side: str, entry: float, last_price: float, offset_pct: float, current_sl: Optional[float]) -> float:
        # entry ± offset
        if offset_pct:
            if side == "LONG":
                be = entry * (1.0 + offset_pct / 100.0)
                if be > last_price:
                    be = entry
            else:
                be = entry * (1.0 - offset_pct / 100.0)
                if be < last_price:
                    be = entry
        else:
            be = entry

        # geri çekme engeli
        if current_sl is not None:
            if side == "LONG" and be < current_sl:
                be = current_sl
            if side == "SHORT" and be > current_sl:
                be = current_sl
        return float(be)

    # -------------------------------------------------------------------------
    # Internal: TP based break-even
    # -------------------------------------------------------------------------

    def _check_tp_break_even(self, st: Dict[str, Any]) -> None:
        conf = st.get("tp_be_conf", {}) or {}
        if not conf.get("enabled"):
            return

        threshold = int(conf.get("count", 1) or 1)
        progressive = bool(conf.get("progressive", False))

        filled = int(st.get("filled_tp_count", 0) or 0)
        if filled < threshold:
            return

        side = st["side"]
        tp_list: List[float] = st.get("tp_prices", []) or []

        if not tp_list:
            # TP list yok -> entry BE
            if st.get("current_sl") != st["entry"]:
                st["current_sl"] = float(st["entry"])
                st["last_update"] = 0.0
                logger.info(f"[TP_BE] sym={st['symbol']} -> SL=entry (no tp list)")
            return

        if progressive:
            idx = min(filled - 1, len(tp_list) - 1)
            new_sl = float(tp_list[idx])
        else:
            new_sl = float(st["entry"])

        cur = st.get("current_sl")
        if cur is not None:
            if side == "LONG" and new_sl < cur:
                return
            if side == "SHORT" and new_sl > cur:
                return

        st["current_sl"] = new_sl
        st["last_update"] = 0.0
        logger.info(f"[TP_BE_APPLY] sym={st['symbol']} filled_tp={filled} progressive={progressive} new_sl={new_sl}")

    # -------------------------------------------------------------------------
    # Internal: Dynamic trailing (profit steps + tp steps)
    # -------------------------------------------------------------------------

    def _maybe_apply_dynamic_trailing(self, st: Dict[str, Any], last_price: float) -> None:
        conf = st.get("dyn_trail_conf", {}) or {}
        if not conf.get("enabled"):
            return

        side = st["side"]
        entry = float(st["entry"])
        if entry <= 0:
            return

        if side == "LONG":
            profit_pct = (last_price - entry) / entry * 100.0
        else:
            profit_pct = (entry - last_price) / entry * 100.0

        # profit-based steps
        steps = conf.get("steps", []) or []
        cur_idx = int(st.get("dyn_applied_profit_idx", -1) or -1)
        for idx, step in enumerate(steps):
            if idx <= cur_idx:
                continue
            try:
                trig = float(step.get("trigger_pct"))
                new_param = float(step.get("new_param"))
            except Exception:
                continue
            if profit_pct >= trig:
                old = st["param"]
                st["param"] = new_param
                st["dyn_applied_profit_idx"] = idx
                # effective param varsa onu da güncelle (vol_conf üstüne bindiyse sonra vol_conf tekrar ayarlar)
                st["effective_param"] = float(new_param)
                logger.info(f"[DYN_PROFIT] sym={st['symbol']} profit%={profit_pct:.2f} param {old}->{new_param}")
            else:
                break

        # tp-count steps
        tp_steps = conf.get("tp_steps", []) or []
        cur_tp_idx = int(st.get("dyn_applied_tp_idx", -1) or -1)
        filled = int(st.get("filled_tp_count", 0) or 0)

        for idx, step in enumerate(tp_steps):
            if idx <= cur_tp_idx:
                continue
            try:
                trig_tp = int(step.get("tp_count"))
                new_param = float(step.get("new_param"))
            except Exception:
                continue
            if filled >= trig_tp:
                old = st["param"]
                st["param"] = new_param
                st["effective_param"] = float(new_param)
                st["dyn_applied_tp_idx"] = idx
                logger.info(f"[DYN_TP] sym={st['symbol']} filled_tp={filled} param {old}->{new_param}")
            else:
                break

    # -------------------------------------------------------------------------
    # Internal: Volatility adaptive (ATR_RATIO / ATR_PCTL)
    # -------------------------------------------------------------------------

    def _maybe_update_volatility(self, st: Dict[str, Any]) -> None:
        conf = st.get("vol_conf", {}) or {}
        if not conf.get("enabled"):
            return

        base_param = st.get("param")
        if base_param is None:
            return

        method = str(conf.get("method", "ATR_RATIO")).upper()
        cooldown_sec = float(conf.get("cooldown_sec", 60) or 60)
        now = _now(self._time_fn)
        in_cooldown = (now - float(st.get("last_vol_update_ts", 0.0) or 0.0)) < cooldown_sec

        multiplier: Optional[float] = None

        if method == "ATR_RATIO":
            atr_s = st.get("atr_short")
            atr_l = st.get("atr_long")
            if not atr_s or not atr_l or float(atr_l) == 0.0:
                return
            ratio = float(atr_s) / float(atr_l)
            for b in (conf.get("bands", []) or []):
                try:
                    if ratio <= float(b["ratio_max"]):
                        multiplier = float(b["multiplier"])
                        break
                except Exception:
                    continue

        elif method == "ATR_PCTL":
            hist = st.get("atr_history", []) or []
            min_samples = int(conf.get("min_samples", DEFAULT_ATR_PCTL_MIN_SAMPLES) or DEFAULT_ATR_PCTL_MIN_SAMPLES)
            if len(hist) < min_samples:
                return
            cur_atr = st.get("atr")
            if cur_atr is None:
                return
            sorted_hist = sorted(float(x) for x in hist)
            pct = self._percentile_rank(sorted_hist, float(cur_atr))
            for b in (conf.get("bands", []) or []):
                try:
                    if pct <= float(b["pctl_max"]):
                        multiplier = float(b["multiplier"])
                        break
                except Exception:
                    continue
        else:
            return

        if multiplier is None:
            return

        # smoothing
        smoothing = conf.get("smoothing", {}) or {}
        if smoothing.get("enabled"):
            alpha = float(smoothing.get("alpha", 0.3) or 0.3)
            prev_sm = st.get("vol_smooth_mult")
            if prev_sm is None:
                st["vol_smooth_mult"] = float(multiplier)
            else:
                st["vol_smooth_mult"] = alpha * float(multiplier) + (1.0 - alpha) * float(prev_sm)
            eff_mult = float(st["vol_smooth_mult"])
        else:
            eff_mult = float(multiplier)

        # stacking
        if bool(conf.get("stack_with_dynamic", True)):
            new_effective = float(base_param) * eff_mult
        else:
            new_effective = float(st.get("original_param", base_param)) * eff_mult

        # clamp
        min_p = float(conf.get("min_param", 0.0) or 0.0)
        max_p = float(conf.get("max_param", 999.0) or 999.0)
        new_effective = max(min_p, min(max_p, new_effective))

        prev_eff = st.get("effective_param")
        changed = prev_eff is None or abs(float(prev_eff) - new_effective) > 1e-12

        if in_cooldown and not changed:
            return

        if changed:
            st["effective_param"] = new_effective
            st["last_vol_update_ts"] = now
            st["last_vol_multiplier"] = eff_mult
            logger.info(
                f"[VOL_APPLY] sym={st['symbol']} method={method} base={base_param} "
                f"eff_prev={prev_eff} eff_new={new_effective} raw_mult={multiplier} eff_mult={eff_mult}"
            )

    @staticmethod
    def _percentile_rank(sorted_list: List[float], value: float) -> float:
        if not sorted_list:
            return 0.0
        idx = bisect.bisect_right(sorted_list, value)
        return idx / len(sorted_list)


# Global singleton (isterseniz exchange bazlı ayrı instance’lar da yaratabilirsiniz)
TRAILING_MANAGER = TrailingManager()

# =============================================================================
# Supervisor loop (ortak)
# =============================================================================

async def trailing_loop(
    *,
    fetch_price_fn: FetchPriceFn,
    update_sl_fn: UpdateSLFn,
    get_meta_fn: Optional[GetMetaFn] = None,
    quantize_fn: Optional[QuantizeFn] = None,
    interval: float = DEFAULT_TRAIL_LOOP_INTERVAL,
    opts: Optional[LoopOptions] = None,
) -> None:
    """
    Ortak trailing supervisor:
    - price feed’den son fiyatı alır
    - state günceller (highest/lowest, ATR, BE, dynamic, volatility)
    - yeni SL adayı hesaplar
    - kurallara uygunsa borsada SL’i update eder

    Not: update_sl_fn'in içinde "pozisyon kapandıysa unregister" gibi cleanup yapmanız normaldir.
    """
    if opts is None:
        opts = LoopOptions(interval=float(interval))

    qfn = quantize_fn or _default_quantize

    while True:
        try:
            for pid, st in list(TRAILING_MANAGER.positions.items()):
                # initial delay check (manager içinde de var ama burada hızlı skip)
                if _now(TRAILING_MANAGER._time_fn) < float(st.get("trail_not_before", 0.0) or 0.0):
                    continue

                symbol = st["symbol"]
                side = st["side"]

                last_price = None
                try:
                    last_price = fetch_price_fn(symbol)
                except Exception as e:
                    logger.warning(f"[TRAIL_FETCH_ERR] pid={pid} sym={symbol} err={e}")
                    continue

                if last_price is None:
                    continue

                try:
                    last_price_f = float(last_price)
                except (TypeError, ValueError):
                    continue
                if last_price_f <= 0:
                    continue

                # state update
                TRAILING_MANAGER.update_price(pid, last_price_f)

                # candidate SL
                new_sl = TRAILING_MANAGER.calc_new_sl(pid)
                if not new_sl:
                    continue

                # meta
                meta = None
                if get_meta_fn:
                    try:
                        meta = get_meta_fn(symbol)
                    except Exception as e:
                        logger.debug(f"[TRAIL_META_ERR] sym={symbol} err={e}")
                        meta = None

                # decision
                if not TRAILING_MANAGER.should_update_sl(pid, float(new_sl), meta, opts=opts):
                    continue

                # quantize with external function (preferred)
                try:
                    q_sl = float(qfn(symbol, float(new_sl), side, "SL"))
                except Exception:
                    q_sl = float(new_sl)

                # (ek güvenlik) tick bilgisi varsa ve quantize_fn verilmediyse floor/ceil uygulayalım
                if quantize_fn is None and meta and isinstance(meta, dict):
                    tick = _safe_float(meta.get("price_step"))
                    if tick and tick > 0:
                        # SL için genelde "floor" (LONG: erken tetiklenmesin); SHORT’ta da floor/ceil tartışmalı.
                        # Burada güvenli olarak floor yapıyoruz; borsaya göre dışarıdan quantize_fn ile değiştirin.
                        q_sl = _quantize_floor_to_tick(q_sl, tick)

                # update
                ok = False
                try:
                    ok = bool(update_sl_fn(int(pid), symbol, float(q_sl)))
                except Exception as e:
                    logger.error(f"[TRAIL_UPDATE_FN_ERR] pid={pid} sym={symbol} err={e}", exc_info=True)
                    ok = False

                if ok:
                    TRAILING_MANAGER.mark_sl_updated(pid, float(q_sl))
                    logger.info(f"[TRAIL_SL_OK] pid={pid} sym={symbol} new_sl={q_sl}")

        except Exception as e:
            logger.error(f"[TRAIL_LOOP_ERR] {e}", exc_info=True)

        await asyncio.sleep(float(opts.interval))
