# core/cache_utils.py
from __future__ import annotations

from collections import OrderedDict
import time as _time


class LRUCacheTTL:
    """
    OrderedDict tabanlı: key -> (ts_mono, value)
    - get(): TTL check + LRU touch
    - set(): write + LRU touch + size-cap + periyodik GC
    - gc(): TTL sweep + size-cap
    """

    def __init__(self, max_items: int, gc_every: int = 200, max_entry_age_sec: float | None = None):
        self._d = OrderedDict()
        self._set_count = 0
        self.max_items = int(max_items) if max_items else 1000
        self.gc_every = int(gc_every) if gc_every else 200
        self.max_entry_age_sec = float(max_entry_age_sec) if max_entry_age_sec is not None else None

    def __len__(self):
        return len(self._d)

    def gc(self, now_mono: float | None = None):
        try:
            d = self._d
            if not d:
                return

            if now_mono is None:
                now_mono = _time.monotonic()

            # TTL sweep (global max_age varsa)
            if self.max_entry_age_sec is not None:
                max_age = float(self.max_entry_age_sec)
                while d:
                    k0, (ts0, _v0) = next(iter(d.items()))
                    if (now_mono - float(ts0)) > max_age:
                        d.popitem(last=False)
                    else:
                        break

            # size cap
            while len(d) > int(self.max_items):
                d.popitem(last=False)

        except Exception:
            return

    def get(self, key, ttl_sec: float):
        try:
            d = self._d
            rec = d.get(key)
            if not rec:
                return None

            ts_mono, val = rec
            if (_time.monotonic() - float(ts_mono)) <= float(ttl_sec):
                try:
                    d.move_to_end(key, last=True)
                except Exception:
                    pass
                return val

            # TTL geçti
            try:
                d.pop(key, None)
            except Exception:
                pass
            return None
        except Exception:
            return None

    def set(self, key, val):
        try:
            d = self._d
            now = _time.monotonic()
            d[key] = (now, val)
            try:
                d.move_to_end(key, last=True)
            except Exception:
                pass

            self._set_count += 1
            if self.gc_every > 0 and (self._set_count % self.gc_every == 0):
                self.gc(now_mono=now)

            # hard cap
            while len(d) > int(self.max_items):
                d.popitem(last=False)

        except Exception:
            return


class DedupGuardTTL:
    """
    key -> (last_seen_ts:int, seen_mono:float)
    Aynı key için aynı ts tekrar gelirse False döner.
    TTL + max_items + LRU.
    """

    def __init__(self, ttl_sec: float, max_items: int = 20000, gc_every: int = 500):
        self._d = OrderedDict()
        self._set_count = 0
        self.ttl_sec = float(ttl_sec)
        self.max_items = int(max_items)
        self.gc_every = int(gc_every)

    def gc(self, now_mono: float | None = None):
        try:
            d = self._d
            if not d:
                return
            if now_mono is None:
                now_mono = _time.monotonic()

            ttl = float(self.ttl_sec)

            # TTL sweep
            while d:
                _k, (_ts, _seen_m) = next(iter(d.items()))
                if (now_mono - float(_seen_m)) > ttl:
                    d.popitem(last=False)
                else:
                    break

            # size cap
            while len(d) > int(self.max_items):
                d.popitem(last=False)

        except Exception:
            return

    def should_process(self, key, ts: int) -> bool:
        try:
            if ts is None:
                return False

            now = _time.monotonic()
            self._set_count += 1
            if self.gc_every > 0 and (self._set_count % self.gc_every == 0):
                self.gc(now_mono=now)

            d = self._d
            rec = d.get(key)
            if rec is not None:
                prev_ts, _prev_seen = rec
                if int(prev_ts) == int(ts):
                    try:
                        d.move_to_end(key, last=True)
                    except Exception:
                        pass
                    return False

            d[key] = (int(ts), now)
            try:
                d.move_to_end(key, last=True)
            except Exception:
                pass

            while len(d) > int(self.max_items):
                d.popitem(last=False)

            return True

        except Exception:
            return True
