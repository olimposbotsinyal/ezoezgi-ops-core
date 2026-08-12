# core/strategy_manager.py
from __future__ import annotations

import logging
import threading
from typing import Dict, Type, Optional, Any, List, Tuple

from strategies.base_strategy import BaseStrategy

try:
    from config_service import ConfigService  # optional
except Exception:
    ConfigService = None

logger = logging.getLogger(__name__)


class StrategyManager:
    """
    Registry + Factory (singleton-per-scope) for strategies.

    Monitoring.py expects:
      - StrategyManager.register(StrategyV1/StrategyV2)
      - StrategyManager.get("v1"/"v2", params=...)
    """

    _strategies: Dict[str, Type[BaseStrategy]] = {}
    _instances: Dict[Tuple[str, str], BaseStrategy] = {}  # key=(scope_key, strategy_id)
    _active_id: Optional[str] = None

    _lock = threading.RLock()

    # -----------------------
    # Helpers
    # -----------------------
    @staticmethod
    def _norm_id(strategy_id: str) -> str:
        sid = str(strategy_id or "").strip().lower()
        return sid

    @staticmethod
    def _norm_scope(scope_key: Any) -> str:
        # default: single global scope
        if scope_key is None:
            return "global"
        return str(scope_key)

    # -----------------------
    # Registry
    # -----------------------
    @classmethod
    def register(cls, strategy_cls: Type[BaseStrategy]) -> None:
        try:
            if not issubclass(strategy_cls, BaseStrategy):
                logger.warning("Sınıf %s BaseStrategy'den türetilmemiş.", getattr(strategy_cls, "__name__", strategy_cls))
                return

            sid = getattr(strategy_cls, "strategy_id", None)
            if not sid:
                # Try instantiate to discover id
                try:
                    temp = strategy_cls(strategy_id="temp", params={})
                    sid = getattr(temp, "strategy_id", None)
                except Exception:
                    sid = getattr(strategy_cls, "__name__", "unknown").lower()

            sid = cls._norm_id(sid)
            if not sid:
                logger.error("Strateji id boş olamaz: %s", strategy_cls)
                return

            with cls._lock:
                cls._strategies[sid] = strategy_cls

            logger.info("✅ Strateji Kaydedildi: %s (%s)", sid, strategy_cls.__name__)

        except Exception as e:
            logger.error("Strateji kayıt hatası (%s): %s", strategy_cls, e, exc_info=True)

    @classmethod
    def list_ids(cls) -> List[str]:
        with cls._lock:
            return sorted(cls._strategies.keys())

    @classmethod
    def debug_dump(cls) -> Dict[str, Any]:
        with cls._lock:
            return {
                "registered": sorted(cls._strategies.keys()),
                "active": cls._active_id,
                "instances": len(cls._instances),
                "instance_keys_preview": list(cls._instances.keys())[:10],
            }

    @classmethod
    def set_active(cls, strategy_id: str) -> bool:
        sid = cls._norm_id(strategy_id)
        with cls._lock:
            if sid in cls._strategies:
                cls._active_id = sid
                return True
        return False

    @classmethod
    def active(cls) -> Optional[Type[BaseStrategy]]:
        with cls._lock:
            if cls._active_id is None and cls._strategies:
                cls._active_id = next(iter(cls._strategies.keys()))
            if cls._active_id and cls._active_id in cls._strategies:
                return cls._strategies[cls._active_id]
        return None

    # -----------------------
    # Factory / Singleton-per-scope
    # -----------------------
    @classmethod
    def get(
        cls,
        strategy_id: str,
        params: Optional[dict] = None,
        *,
        scope_key: Any = None,
        refresh_params: bool = True,
        recreate_on_param_change: bool = False,
    ) -> Optional[BaseStrategy]:
        """
        Returns strategy instance.

        - scope_key:
            Use user_id (or exchange+user) if you need per-user instances.
            If omitted -> single global instance per strategy_id.

        - refresh_params:
            If instance exists, update instance.params with incoming params.

        - recreate_on_param_change:
            If True, instance recreated when params dict changes (shallow compare).
            (Safer for strategies that cache things in __init__ based on params.)
        """
        sid = cls._norm_id(strategy_id)
        if not sid:
            return None

        scope = cls._norm_scope(scope_key)
        key = (scope, sid)
        safe_params = params if isinstance(params, dict) else {}

        with cls._lock:
            inst = cls._instances.get(key)
            if inst is not None:
                if refresh_params:
                    try:
                        if recreate_on_param_change:
                            old = getattr(inst, "params", None)
                            if isinstance(old, dict) and old != safe_params:
                                cls._instances.pop(key, None)
                                inst = None
                            else:
                                inst.params = dict(safe_params)
                        else:
                            inst.params = dict(safe_params)
                    except Exception:
                        # worst case: ignore param refresh, keep instance alive
                        pass

                if inst is not None:
                    return inst

            strategy_cls = cls._strategies.get(sid)
            if not strategy_cls:
                logger.warning("[STRAT_GET_MISS] sid=%s registered=%s", sid, list(cls._strategies.keys()))
                return None

            try:
                new_inst = strategy_cls(strategy_id=sid, params=dict(safe_params))
                cls._instances[key] = new_inst
                return new_inst
            except Exception as e:
                logger.error("Strateji başlatma hatası (%s): %s", sid, e, exc_info=True)
                return None

    # -----------------------
    # Maintenance
    # -----------------------
    @classmethod
    def clear_instances(cls, *, scope_key: Any = None, strategy_id: Optional[str] = None) -> int:
        """
        Clears cached instances. Returns number removed.
        """
        scope = cls._norm_scope(scope_key)
        sid = cls._norm_id(strategy_id) if strategy_id is not None else None

        with cls._lock:
            keys = list(cls._instances.keys())
            removed = 0
            for k in keys:
                k_scope, k_sid = k
                if (scope_key is not None and k_scope != scope):
                    continue
                if (sid is not None and k_sid != sid):
                    continue
                cls._instances.pop(k, None)
                removed += 1
            return removed

    @classmethod
    def ensure_registered_defaults(cls) -> None:
        """
        Pushes default_params into ConfigService if supported.
        Safe no-op if ConfigService missing.
        """
        if ConfigService is None:
            return

        with cls._lock:
            items = list(cls._strategies.items())

        for sid, s_cls in items:
            defaults = getattr(s_cls, "default_params", {})
            if not isinstance(defaults, dict) or not defaults:
                continue

            try:
                if hasattr(ConfigService, "update_strategy_params"):
                    ConfigService.update_strategy_params(sid, defaults)
            except Exception as e:
                logger.warning("%s config update hatası: %s", sid, e)
