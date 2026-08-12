# strategies/base_strategy.py
from __future__ import annotations
from abc import ABC, abstractmethod
import json
import os
import copy
from typing import Dict, Any, Optional
import pandas as pd
import logging

class BaseStrategy:
    id = "base"
    runtime_params_file = None
    version_history_file = None
    version_counter = 0
    runtime_params = {}
    runtime_strategy: dict = {}
    _strategy_history_file: str = "strategy_history.jsonl"
    strategy_version_counter: int = 0
    # Param değiştirme logu
    parameter_change_log: list = []
    strategy_id: str = "base"
    default_params: Dict[str, Any] = {}

    def __init__(self, strategy_id: str, params: Optional[Dict[str, Any]] = None):
        """
        Temel strateji başlatıcı.
        params: Stratejiye özel parametreler.
        """
        self.strategy_id = strategy_id
        self.params = params if params is not None else {}
        self.logger = logging.getLogger(self.__class__.__name__)

    async def analyze(self, df: pd.DataFrame, symbol: str, global_regime: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Override edilmesi gereken ana analiz metodu.
        """
        return {}

    def generate_signal(self, df: pd.DataFrame, market_regime: str = None) -> Dict[str, Any]:
        """
        Eski senkron metot (Geriye dönük uyumluluk için).
        """
        return {}

    def load_runtime_params(self):
        """
        runtime_params_file:
          - Yoksa: default kopyası
          - Boş / hatalı JSON: default + dosyayı yeniden yaz
        """
        try:
            if not self.runtime_params_file:
                self.runtime_params = copy.deepcopy(self.default_params)
                return

            # Klasörü oluştur
            os.makedirs(os.path.dirname(self.runtime_params_file), exist_ok=True)

            if not os.path.exists(self.runtime_params_file):
                # İlk kez
                self.runtime_params = copy.deepcopy(self.default_params)
                self.save_runtime_params()
                logging.info(f"[{self.__class__.__name__}] runtime param dosyası oluşturuldu")
                return

            # Dosya var → boyut kontrol (boş / çok küçük ise sıfırla)
            if os.path.getsize(self.runtime_params_file) < 5:
                raise ValueError("runtime param dosyası boş veya çok küçük")

            with open(self.runtime_params_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            merged = copy.deepcopy(self.default_params)
            if isinstance(data, dict):
                merged.update(data)
            else:
                raise ValueError("runtime param JSON dict değil")

            self.runtime_params = merged
            logging.info(f"[{self.__class__.__name__}] runtime params yüklendi")

        except Exception as e:
            logging.warning(f"[{self.__class__.__name__}] runtime params yüklenemedi ({e}) - defaults kullanılıyor")
            self.runtime_params = copy.deepcopy(self.default_params)
            # Kurtarma olarak yeniden yaz
            try:
                self.save_runtime_params()
            except Exception as save_err:
                logging.error(f"[{self.__class__.__name__}] kurtarma kaydetme hatası: {save_err}")

    def save_runtime_params(self):
        if not self.runtime_params_file:
            return
        try:
            os.makedirs(os.path.dirname(self.runtime_params_file), exist_ok=True)
            with open(self.runtime_params_file, "w", encoding="utf-8") as f:
                json.dump(self.runtime_params, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.error(f"[{self.__class__.__name__}] save_runtime_params error: {e}")

    def set_param(self, path: str, value):
        ref = self.runtime_params
        parts = path.split(".")
        for p in parts[:-1]:
            if p not in ref or not isinstance(ref[p], dict):
                ref[p] = {}
            ref = ref[p]
        ref[parts[-1]] = value
        self.save_runtime_params()

    def get_param(self, path, default=None):
        ref = self.runtime_params
        for p in path.split("."):
            if not isinstance(ref, dict) or p not in ref:
                return default
            ref = ref[p]
        return ref
