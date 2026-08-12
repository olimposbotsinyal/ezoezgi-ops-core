# patterns/pattern_engine.py
import numpy as np
import pandas as pd
from .pattern_shapes import PatternShape
import logging


# Logging konfigürasyonu
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s: %(message)s'
)

# Global logger
logger = logging.getLogger(__name__)


class PatternEngine:
    def __init__(self, df: pd.DataFrame, lookback=60):
        self.df = df.tail(lookback).copy()
        self.results = []
        self._pivots = None

    def run(self):
        if self.df is None or self.df.empty or len(self.df) < 20:
            return []
        self._pivots = self._extract_pivots()
        self._detect_triangles()
        self._detect_rectangle()
        self._detect_double_tops_bottoms()
        self._detect_breakout_consolidation()
        self._rank()
        return self.results

    def _extract_pivots(self, window=3):
        highs = self.df['high'].values
        lows = self.df['low'].values
        pivot_highs = []
        pivot_lows = []
        for i in range(window, len(self.df)-window):
            seg_high = highs[i-window:i+window+1]
            seg_low = lows[i-window:i+window+1]
            if highs[i] == seg_high.max():
                pivot_highs.append((i, highs[i]))
            if lows[i] == seg_low.min():
                pivot_lows.append((i, lows[i]))
        return {'highs': pivot_highs, 'lows': pivot_lows}

    def _detect_triangles(self):
        # Basit yakınsayan üst / alt trend
        hs = self._pivots['highs'][-8:]
        ls = self._pivots['lows'][-8:]
        if len(hs) < 3 or len(ls) < 3:
            return
        # line fit
        xh = np.array([p[0] for p in hs])
        yh = np.array([p[1] for p in hs])
        xl = np.array([p[0] for p in ls])
        yl = np.array([p[1] for p in ls])
        try:
            mh, bh = np.polyfit(xh, yh, 1)
            ml, bl = np.polyfit(xl, yl, 1)

        except Exception as e:
            logging.error(f"Hata: {e}")

            return
        # Koşul: Üst çizgi düşüyor + alt çizgi yükseliyor
        if all([mh < 0, ml > 0]):
            # Kesişim (apex)
            if (mh - ml) != 0:
                apex_x = (bl - bh) / (mh - ml)
            else:
                apex_x = max(xh.max(), xl.max()) + 20
            start = min(xh.min(), xl.min())
            curr = len(self.df) - 1
            progress = (curr - start) / (apex_x - start) if apex_x > start else 0.5
            progress = float(max(0.0, min(1.0, progress)))
            last_price = self.df['close'].iloc[-1]
            upper_now = mh * curr + bh
            lower_now = ml * curr + bl
            breakout = False
            breakout_level = None
            if last_price > upper_now * 1.002:
                breakout = True
                breakout_level = upper_now
            elif last_price < lower_now * 0.998:
                breakout = True
                breakout_level = lower_now
            height = (yh.max() - yl.min())
            if height <= 0:
                height = (upper_now - lower_now)
            tgt = []
            if breakout and breakout_level:
                direction_up = last_price > upper_now
                if direction_up:
                    tgt.append(round(breakout_level + height * 0.6, 8))
                else:
                    tgt.append(round(breakout_level - height * 0.6, 8))
            pat = PatternShape(
                name="SymTriangle",
                bullish=True,
                confidence=0.75,
                progress=progress,
                breakout=breakout,
                breakout_confirmed=breakout,
                breakout_level=breakout_level,
                target_levels=tgt,
                lines=[
                    {'points': [(int(xh.min()), mh * xh.min() + bh), (int(xh.max()), mh * xh.max() + bh)],
                     'color': (255, 180, 0, 180)},
                    {'points': [(int(xl.min()), ml * xl.min() + bl), (int(xl.max()), ml * xl.max() + bl)],
                     'color': (0, 200, 140, 180)}
                ],
                pivot_points=hs + ls
            )
            self.results.append(pat)

    def _detect_rectangle(self, min_bars=12):
        tail = self.df.tail(40)
        highs = tail['high'].values
        lows = tail['low'].values
        top = highs.max()
        bot = lows.min()
        rng = top - bot
        if rng <= 0:
            return
        middle_band = tail['close'].between(bot + rng * 0.2, top - rng * 0.2).mean()
        # basit varyans kontrolü
        if middle_band > 0.6:
            progress = len(tail) / max(min_bars, 1)
            progress = min(1.0, progress)
            last_price = tail['close'].iloc[-1]
            breakout = False
            breakout_level = None

            # Breakout ve yön tespiti
            if last_price > top * 1.002:
                breakout = True
                breakout_level = top
                bullish = True
            elif last_price < bot * 0.998:
                breakout = True
                breakout_level = bot
                bullish = False
            else:
                # Breakout yoksa varsayılan değer
                bullish = True

            tgt = []
            if breakout and breakout_level:
                direction_up = last_price > top
                if direction_up:
                    tgt.append(round(breakout_level + rng, 8))
                else:
                    tgt.append(round(breakout_level - rng, 8))

            self.results.append(PatternShape(
                name="Rectangle",
                # bullish parametresini hesaplanan değerle güncelle
                bullish=bullish,
                confidence=0.65,
                progress=progress,
                breakout=breakout,
                breakout_confirmed=breakout,
                breakout_level=breakout_level,
                target_levels=tgt,
                lines=[
                    {'points': [(len(self.df) - len(tail), top), (len(self.df) - 1, top)],
                     'color': (180, 180, 255, 160)},
                    {'points': [(len(self.df) - len(tail), bot), (len(self.df) - 1, bot)],
                     'color': (180, 180, 255, 160)}
                ]
            ))

    def _detect_double_tops_bottoms(self):
        arr = self.df
        if len(arr) < 40:
            return
        highs = arr['high'].values
        lows = arr['low'].values
        # Double Top
        peaks = []
        for i in range(2, len(highs)-2):
            if highs[i] > highs[i-1] and highs[i] > highs[i+1]:
                peaks.append((i, highs[i]))
        if len(peaks) >= 2:
            peaks.sort(key=lambda x: x[1], reverse=True)
            p1, p2 = peaks[0], peaks[1]
            price_diff = abs(p1[1]-p2[1])/max(p1[1], p2[1])
            time_diff = abs(p1[0]-p2[0])
            if price_diff < 0.025 and time_diff > 3:
                neckline = arr['low'].iloc[min(p1[0], p2[0]):max(p1[0], p2[0]) + 1].min()
                last = arr['close'].iloc[-1]
                breakout = last < neckline * 0.995
                tgt = []
                if breakout:
                    height = ((p1[1] + p2[1]) / 2 - neckline)
                    tgt.append(round(neckline - height * 0.9, 8))
                self.results.append(PatternShape(
                    name="DoubleTop",
                    bullish=False,
                    confidence=0.70,
                    progress=1.0 if breakout else 0.8,
                    breakout=breakout,
                    breakout_confirmed=breakout,
                    breakout_level=neckline,
                    target_levels=tgt,
                    pivot_points=[p1, p2]
                ))
        # Double Bottom
        troughs = []
        for i in range(2, len(lows)-2):
            if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
                troughs.append((i, lows[i]))
        if len(troughs) >= 2:
            troughs.sort(key=lambda x: x[1])
            t1, t2 = troughs[0], troughs[1]
            price_diff = abs(t1[1]-t2[1])/min(t1[1], t2[1])
            time_diff = abs(t1[0]-t2[0])
            if price_diff < 0.025 and time_diff > 3:
                neckline = arr['high'].iloc[min(t1[0], t2[0]):max(t1[0], t2[0]) + 1].max()
                last = arr['close'].iloc[-1]
                breakout = last > neckline * 1.005
                tgt = []
                if breakout:
                    height = (neckline - (t1[1] + t2[1]) / 2)
                    tgt.append(round(neckline + height * 0.9, 8))
                self.results.append(PatternShape(
                    name="DoubleBottom",
                    bullish=True,
                    confidence=0.70,
                    progress=1.0 if breakout else 0.8,
                    breakout=breakout,
                    breakout_confirmed=breakout,
                    breakout_level=neckline,
                    target_levels=tgt,
                    pivot_points=[t1, t2]
                ))

    def _detect_breakout_consolidation(self):
        if len(self.df) < 50:
            return
        recent = self.df.tail(25)
        prev = self.df.tail(60).head(35)
        rec_range = (recent['high']-recent['low']).mean()
        prev_range = (prev['high']-prev['low']).mean()
        if prev_range > 0 and rec_range < prev_range*0.75:
            # Sıkışma
            last = self.df['close'].iloc[-1]
            res = recent['high'].iloc[:-1].max()
            sup = recent['low'].iloc[:-1].min()
            breakout = False
            bullish = True
            level = None
            tgt = []
            if last > res*1.004:
                breakout = True
                level = res
                bullish = True
                tgt = [round(last + (res - sup), 8)]
            elif last < sup * 0.996:
                breakout = True
                level = sup
                bullish = False
                tgt = [round(last - (res - sup), 8)]
            self.results.append(PatternShape(
                name="VolatilitySqueeze",
                bullish=bullish,
                confidence=0.6,
                progress=0.9,
                breakout=breakout,
                breakout_confirmed=breakout,
                breakout_level=level,
                target_levels=tgt
            ))

    def _rank(self):
        for p in self.results:
            # Basit kalite formülü
            p.quality_score = (p.confidence*0.6 + p.progress*0.4)
        self.results.sort(key=lambda x: (x.breakout_confirmed, x.quality_score), reverse=True)
