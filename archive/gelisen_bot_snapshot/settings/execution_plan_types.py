# settings/execution_plan_types.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Literal

Side = Literal["LONG", "SHORT"]

@dataclass
class SymbolMeta:
    price_step: float
    amount_step: float
    price_decimals: int
    amount_decimals: int
    min_amount: float
    ccxt_symbol: Optional[str] = None

@dataclass
class ExecutionPlan:
    user_id: int
    username: str
    exchange: str

    symbol_core: str
    symbol_exchange: str
    side: Side

    entry_price: float
    signal_stop_loss: Optional[float]
    signal_take_profits: List[float]

    leverage: int
    margin: str
    lot_notional: float
    contracts: float

    sl_tp_emir: bool
    sl_price: Optional[float]
    tp_structs: List[Dict[str, Any]] = field(default_factory=list)

    terial_stop: str = "off"
    maliyet_cek: str = "off"
    trailing_mode: Optional[str] = None
    trailing_param: Optional[float] = None

    meta: SymbolMeta = field(default_factory=lambda: SymbolMeta(
        price_step=0.0001,
        amount_step=1.0,
        price_decimals=4,
        amount_decimals=0,
        min_amount=1.0
    ))

    debug: Dict[str, Any] = field(default_factory=dict)
