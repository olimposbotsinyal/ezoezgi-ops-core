# analytics/segment_stats.py
import os
import json
import math
import threading
from datetime import datetime, timezone
from typing import Dict, Tuple, Optional, List, Any
import logging


class SegmentStatsManager:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def get_segment_manager(cls):
        """
        Singleton örneğini döndüren sınıf metodu
        Eski kodlarla uyumluluk için get_segment_manager() olarak bırakıldı
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self,
                 filepath: str = "analytics/segment_stats.jsonl",
                 ema_alpha: float = 0.25,
                 cold_start_expected_r: float = 1.10,
                 min_trades_for_confidence: int = 5):
        # Birden fazla kez çağrılmasını önlemek için
        if not hasattr(self, 'initialized'):
            self.filepath = filepath
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            self.alpha = ema_alpha
            self.cold_start = cold_start_expected_r
            self.min_trades = min_trades_for_confidence
            self._lock = threading.Lock()
            self.cache: Dict[Tuple[str, str], Dict] = {}
            self._load_existing()
            self.initialized = True

    def _load_existing(self):
        if not os.path.exists(self.filepath):
            return
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        k = (rec['strategy_id'], rec['segment_key'])
                        self.cache[k] = {
                            'count': rec.get('count', 0),
                            'ema_exp_r': rec.get('ema_exp_r', self.cold_start),
                            'r_sum': rec.get('r_sum', 0.0),
                            'r2_sum': rec.get('r2_sum', 0.0),
                            'last_update': rec.get('timestamp')
                        }

                    except Exception as e:
                        logging.error(f"Hata: {e}")

                        continue
        except Exception as e:
            logging.error(f"[SegmentStats] load hata: {e}")

    # Orijinal isim (senin kodunda get_expected_r)
    def get_expected_r(self, strategy_id: str, segment_key: str) -> float:
        k = (strategy_id, segment_key)
        data = self.cache.get(k)
        if not data:
            return self.cold_start
        if data['count'] < self.min_trades:
            w = data['count'] / self.min_trades
            return round(self.cold_start * (1 - w) + data['ema_exp_r'] * w, 3)
        return round(data['ema_exp_r'], 3)

    def update(self, strategy_id: str, segment_key: str, realized_r: float):
        """
        realized_r: R multiple (negatif olabilir)
        """
        if not segment_key:
            return
        k = (strategy_id, segment_key)
        with self._lock:
            data = self.cache.setdefault(k, {
                'count': 0,
                'ema_exp_r': self.cold_start,
                'r_sum': 0.0,
                'r2_sum': 0.0,
                'last_update': None
            })
            prev = data['ema_exp_r']
            new_ema = prev + self.alpha * (realized_r - prev)
            data['ema_exp_r'] = new_ema
            data['count'] += 1
            data['r_sum'] += realized_r
            data['r2_sum'] += realized_r * realized_r
        data['last_update'] = datetime.now(timezone.utc).isoformat()
        self._append_event(strategy_id, segment_key, data, realized_r)

    def _append_event(self, strategy_id, segment_key, state, realized_r):
        rec = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'strategy_id': strategy_id,
            'segment_key': segment_key,
            'count': state['count'],
            'ema_exp_r': round(state['ema_exp_r'], 4),
            'last_realized_R': round(realized_r, 4),
            'r_sum': round(state['r_sum'], 4),
            'r2_sum': round(state['r2_sum'], 4)
        }
        try:
            with open(self.filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            logging.error(f"[SegmentStats] append hata: {e}")

    def segment_std(self, strategy_id, segment_key) -> Optional[float]:
        data = self.cache.get((strategy_id, segment_key))
        if not data or data['count'] < 2:
            return None
        mean = data['r_sum'] / data['count']
        mean2 = data['r2_sum'] / data['count']
        var = max(0.0, mean2 - mean * mean)
        return math.sqrt(var)

    def get_top_segments(self, by: str = 'count', limit: int = 5) -> List[Tuple[Tuple[str, str], Dict[str, Any]]]:
        """
        En iyi performans gösteren segmentleri belirli bir metriğe göre sıralar.
        Args:
            by (str): Sıralama metriği ('count', 'ema_exp_r', 'r_sum').
            limit (int): Döndürülecek en iyi segment sayısı.
        Returns:
            list: ((strategy_id, segment_key), data_dict) formatında bir liste.
        """
        if not self.cache:
            return []

        # Cache'i belirtilen metriğe göre sırala
        try:
            sorted_segments = sorted(
                self.cache.items(),
                key=lambda item: item[1].get(by, 0),
                reverse=True
            )
        except (TypeError, ValueError) as e:
            logging.error(f"[SegmentStats] get_top_segments sıralama hatası: {e}")
            return []

        return sorted_segments[:limit]

    def get_optimization_suggestions(self, min_trades: int = 5) -> List[Dict[str, str]]:
        """
        Segment performansını analiz eder ve optimizasyon önerileri sunar.
        """
        suggestions = []
        if not self.cache:
            return suggestions

        for (sid, key), data in self.cache.items():
            count = data.get('count', 0)
            # DÜZELTME: Ortalama PnL'yi r_sum ve count'tan hesapla
            avg_pnl = (data.get('r_sum', 0.0) / count) * 100 if count > 0 else 0.0

            if count < min_trades:
                continue

            # Kötü performans gösteren segmentler için öneri
            if avg_pnl < -0.5:  # Ortalamada %0.5'ten fazla zarar
                suggestion_text = (
                    f"Bu segmentin ortalama PnL'si ({avg_pnl:.2f}%) negatif. "
                    f"Bu segmente özel alarm eşiklerini (örn: min_conf) yükseltmeyi veya "
                    f"bu segmentten gelen alarmları manuel olarak onaylamayı düşünün."
                )
                suggestions.append({
                    'strategy_id':sid,
                    'segment_key':key,
                    'status':'Kötü Performans',
                    'suggestion':suggestion_text
                })

            # İyi performans gösteren segmentler için öneri
            elif avg_pnl > 1.0:  # Ortalamada %1'den fazla kar
                suggestion_text = (
                    f"Bu segment istikrarlı bir şekilde karlı ({avg_pnl:.2f}%). "
                    f"Mevcut ayarları koruyun ve performansını izlemeye devam edin."
                )
                suggestions.append({
                    'strategy_id':sid,
                    'segment_key':key,
                    'status':'İyi Performans',
                    'suggestion':suggestion_text
                })

        return suggestions


# Eski kodlarla uyumluluk için global fonksiyon
def get_segment_manager():
    """
    Global kapsamda çağrılabilecek fonksiyon
    SegmentStatsManager singleton örneğini döndürür
    """
    return SegmentStatsManager.get_segment_manager()
