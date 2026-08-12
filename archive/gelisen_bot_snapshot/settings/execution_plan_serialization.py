# settings/execution_plan_serialization.py
from __future__ import annotations
from typing import Any, Dict


def _safe(obj: Any, *, max_list: int = 50, max_str: int = 8000) -> Any:
    """
    JSON'a çevrilebilir güvenli yapıya dönüştür.
    - dataclass / pydantic / normal class destekler
    """
    if obj is None:
        return None

    if isinstance(obj, (int, float, bool)):
        return obj

    if isinstance(obj, str):
        return obj if len(obj) <= max_str else obj[:max_str] + "...(truncated)"

    if isinstance(obj, (list, tuple, set)):
        arr = list(obj)
        out = [_safe(x, max_list=max_list, max_str=max_str) for x in arr[:max_list]]
        if len(arr) > max_list:
            out.append(f"...(+{len(arr) - max_list} more)")
        return out

    if isinstance(obj, dict):
        return {str(k): _safe(v, max_list=max_list, max_str=max_str) for k, v in obj.items()}

    # pydantic v2
    if hasattr(obj, "model_dump"):
        try:
            return _safe(obj.model_dump(), max_list=max_list, max_str=max_str)
        except Exception:
            pass

    # pydantic v1
    if hasattr(obj, "dict"):
        try:
            return _safe(obj.dict(), max_list=max_list, max_str=max_str)
        except Exception:
            pass

    # dataclass
    try:
        from dataclasses import asdict, is_dataclass
        if is_dataclass(obj):
            return _safe(asdict(obj), max_list=max_list, max_str=max_str)
    except Exception:
        pass

    # normal class
    if hasattr(obj, "__dict__"):
        try:
            return _safe(vars(obj), max_list=max_list, max_str=max_str)
        except Exception:
            pass

    return str(obj)


def execution_plan_to_dict(plan: Any, *, limit_debug: bool = False) -> Dict[str, Any]:
    """
    Planı eksiksiz dict'e çevirir.
    limit_debug=True ise debug alanını kırpar (şişmeyi önlemek için).
    """
    d_any = _safe(plan)
    if not isinstance(d_any, dict):
        return {"_plan": d_any}

    if limit_debug and "debug" in d_any and isinstance(d_any["debug"], dict):
        # İstersen burada allowlist yapabilirsin
        allow = {
            "tp_mode", "sl_mode", "sl_pct",
            "margin_usdt_target", "notional_usdt_target",
            "effective_notional_usdt", "uplift_used_notional",
            "leverage", "margin_mode",
            "min_required_notional", "max_allowed_notional",
            "min_amount_step_aligned", "amount_step",
            "trace_id",
        }
        d_any["debug"] = {k: v for k, v in d_any["debug"].items() if k in allow}

    return d_any


def execution_plan_to_summary(plan: Any) -> Dict[str, Any]:
    # Öğrenme/sinyal bazlı kayıt için “kullanıcıdan bağımsız” özet
    tp_structs = getattr(plan, "tp_structs", None) or []
    tp_prices = []
    tp_fracs = []

    for t in tp_structs:
        if not isinstance(t, dict):
            continue
        p = t.get("price") or t.get("tp_price")
        f = t.get("close_frac") or t.get("fraction") or t.get("qty_frac") or t.get("percent")
        if p is not None:
            tp_prices.append(float(p))
        if f is not None:
            fv = float(f)
            if fv > 1.0:
                fv = fv / 100.0
            tp_fracs.append(fv)

    s = sum(tp_fracs) if tp_fracs else 0.0
    if s > 0:
        tp_fracs = [x / s for x in tp_fracs]

    meta = getattr(plan, "meta", None)

    return {
        "sl_tp_emir": bool(getattr(plan, "sl_tp_emir", False)),
        "sl_price": getattr(plan, "sl_price", None),
        "signal_stop_loss": getattr(plan, "signal_stop_loss", None),
        "tp_prices": tp_prices,
        "tp_fractions": tp_fracs,
        "terial_stop": getattr(plan, "terial_stop", "off"),
        "maliyet_cek": getattr(plan, "maliyet_cek", "off"),
        "trailing_mode": getattr(plan, "trailing_mode", None),
        "trailing_param": getattr(plan, "trailing_param", None),
        "meta": {
            "price_step": getattr(meta, "price_step", None),
            "amount_step": getattr(meta, "amount_step", None),
            "price_decimals": getattr(meta, "price_decimals", None),
            "amount_decimals": getattr(meta, "amount_decimals", None),
            "min_amount": getattr(meta, "min_amount", None),
            "ccxt_symbol": getattr(meta, "ccxt_symbol", None),
        }
    }
