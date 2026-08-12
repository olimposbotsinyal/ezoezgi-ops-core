# chart_renderer.py

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
import pandas as pd
from typing import List, Any, Optional, Dict, Union, Tuple, TYPE_CHECKING, cast
import logging
import math, io
from datetime import datetime, timezone

# Proje içi importlar
from config_service import ConfigService
from data.olimpos_data import get_user_settings
from io import BytesIO
import os

try:
    import talib as ta
    from talib import MA_Type

    HAS_TALIB = True
except Exception as exc_init:
    logging.error(f"Hata: {exc_init}")
    HAS_TALIB = False

if TYPE_CHECKING:
    from telegram import Bot

import numpy as np
import json

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    force=True
)

logger = logging.getLogger(__name__)

# JSON Yükleme Fonksiyonunu Güncelleyin (Dosyanın başlarında)
def load_json_settings():
    try:
        # DÜZELTME: Dosya 'config' klasörü içinde aranıyor
        path = os.path.join("config", "olimpos_tarama_ayarlari.json")
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            # Yedek olarak ana dizine de bakalım
            if os.path.exists("olimpos_tarama_ayarlari.json"):
                with open("olimpos_tarama_ayarlari.json", 'r', encoding='utf-8') as f:
                    return json.load(f)
            return {}
    except Exception as e:
        logging.error(f"JSON Ayarları Yüklenemedi: {e}")
        return {}


# Yardımcı Fonksiyon: Asset Yükle
def load_asset_image(image_name):
    """assets klasöründen resim yükler"""
    try:
        path = os.path.join("assets", image_name)
        if os.path.exists(path):
            return Image.open(path).convert("RGBA")
        else:
            logging.warning(f"Asset bulunamadı: {path}")
            return None
    except Exception as e:
        logging.error(f"Asset yükleme hatası ({image_name}): {e}")
        return None


TF_TR_LABELS = {
    '1m':'1 Dakikalık', '5m':'5 Dakikalık', '15m':'15 Dakikalık',
    '30m':'30 Dakikalık', '1h':'1 Saatlik', '4h':'4 Saatlik', '1d':'Günlük'
}


def tf_to_tr_label(tf: str) -> str:
    return TF_TR_LABELS.get(tf, tf)


class ChartRenderer:
    def __init__(self) -> None:
        self.width = 1200
        self.height = 900
        self.theme = "dark"
        self.figsize = (self.width / 100, self.height / 100)  # Matplotlib için inch cinsinden
        self.entry_arrow_data = {}  # {symbol: (entry_index, entry_price)}
        self._apply_chart_config()

    _last_signal_meta: dict = {}

    # REFAKTÖR: __init__ içinde config yüklemesi yapılacak.

    # ChartRenderer sınıfına ekle

    def set_dimensions(self, width: int = 1200, height: int = 900):
        self.width = width
        self.height = height

    def set_entry_arrow(self, symbol: str, entry_index: int, entry_price: float):
        """Giriş okunu kaydet"""
        self.entry_arrow_data[symbol] = (entry_index, entry_price)

    def get_entry_arrow(self, symbol: str) -> Optional[Tuple[int, float]]:
        """Kayıtlı giriş okunu getir"""
        return self.entry_arrow_data.get(symbol)

    def _get_font(self, size: int = 12, bold: bool = False) -> ImageFont.FreeTypeFont:
        try:
            font_path = self.font_path_bold if bold else self.font_path_regular
            if font_path and os.path.exists(font_path):
                if bold:
                    return ImageFont.truetype(font_path, size)
                return ImageFont.truetype(font_path, size)
        except (IOError, OSError) as exc_font:
            logging.error(f"Hata: {exc_font}")
        return ImageFont.load_default()

    @staticmethod
    def _compute_trailing_stop_from_hits(
        signal_type: str,
        entry_price: float,
        targets: List[float],
        target_hits: Optional[List[bool]],
        default_stop: float
    ) -> Optional[float]:
        """
        Görsel stop taşıma:
          - TP1 vuruldu -> Stop = Entry (BE)
          - TP2 vuruldu -> Stop = TP1
          - TP3 vuruldu -> Stop = TP2 ...
        """
        try:
            if not target_hits or not targets:
                return None

            # son vurulan hedef indexi (0-based)
            last_hit = None
            for i, h in enumerate(target_hits[:len(targets)]):
                if h:
                    last_hit = i

            if last_hit is None:
                return None  # hiç hedef vurulmadı

            # yeni stop seviyesi
            if last_hit == 0:
                new_sl = float(entry_price)
            else:
                new_sl = float(targets[last_hit - 1])

            # güvenlik: nan/inf olmasın
            if not math.isfinite(new_sl) or new_sl <= 0:
                return None

            # çok küçük farklarda default_stop'u koru (opsiyonel)
            if default_stop and math.isfinite(float(default_stop)):
                if abs(new_sl - float(default_stop)) / max(1e-12, abs(float(default_stop))) < 1e-6:
                    return float(default_stop)

            return new_sl
        except Exception:
            return None

    def render(self,
            symbol: str,
            df: pd.DataFrame,
            signal_type: str,
            entry_price: float,
            targets: List[float],
            stop_loss: float,
            patterns=None,
            width: int = 1350,
            height: int = 720,
            timeframe: str = None,
            strategy_meta: Optional[Dict[str, Any]] = None,
            alarm_id: Optional[str] = None,
            signal_id: Optional[str] = None,
            entry_index: Optional[int] = None,
            signal_time: Optional[Union[str, pd.Timestamp]] = None,
            target_hits: Optional[List[bool]] = None,
            targets_hit_times: Optional[List[Optional[Union[str, pd.Timestamp]]]] = None,
            prev_stop_loss: Optional[float] = None

    ):
        """
        Geliştirilmiş render metodu.
        DÜZELTME: Grafik 200 muma kırpıldığında, entry_index'in göreceli pozisyonu
                  yeniden hesaplanarak 'Giriş Oku' hatası düzeltildi.
        """
        from config_service import ConfigService
        dims = ConfigService.charts().get('dimensions', {}) or {}
        width = int(dims.get('default_width', 1200))
        height = int(dims.get('event_chart_height', 600))  # Olay kartları için daha kısa grafik

        def _none(reason: str, extra: Optional[dict] = None):
            try:
                payload = {
                    "reason":reason,
                    "symbol":symbol,
                    "tf":timeframe,
                    "signal_id":signal_id,
                    "df_len_in":(len(df) if df is not None else None),
                    "df_cols_in":(list(df.columns) if isinstance(df, pd.DataFrame) else None),
                }
                if extra and isinstance(extra, dict):
                    payload.update(extra)
                logging.error(f"[RENDER_RETURNED_NONE] {payload}")
            except Exception:
                logging.error(
                    f"[RENDER_RETURNED_NONE] reason={reason} symbol={symbol} tf={timeframe} signal_id={signal_id}")
            return None

        try:
            if df is None or df.empty or len(df) < 25:
                logging.warning("[ChartRenderer] Yetersiz veri")
                return _none("DF_EMPTY_OR_TOO_SHORT(<25)")

            df2 = df.copy()
            # --- SL güncellemesi meta'dan geliyorsa uygula (chart'ta yeni stop görünsün) ---
            try:
                m = strategy_meta or {}
                # olası anahtar isimleri (senin event_meta'da sl_old/sl_new var)
                sl_new = m.get("sl_new") or m.get("stop_loss_new")
                sl_old = m.get("sl_old") or m.get("stop_loss_old")

                if sl_new is not None:
                    # prev_stop_loss yoksa sl_old'u ona koy
                    if prev_stop_loss is None and sl_old is not None:
                        prev_stop_loss = float(sl_old)
                    # stop_loss'u yeni değerle override et
                    stop_loss = float(sl_new)
            except Exception:
                pass

            # --- TP'lere göre görsel stop taşıma (sl_new yoksa) ---
            try:
                m = strategy_meta or {}
                sl_new_present = (m.get("sl_new") is not None) or (m.get("stop_loss_new") is not None)

                if (not sl_new_present) and (target_hits is not None) and (targets is not None):
                    computed_sl = self._compute_trailing_stop_from_hits(
                        signal_type=str(signal_type),
                        entry_price=float(entry_price),
                        targets=list(targets),
                        target_hits=list(target_hits),
                        default_stop=float(stop_loss) if stop_loss is not None else 0.0
                    )
                    if computed_sl is not None and math.isfinite(computed_sl):
                        # Eski stop'u gri göstermek için prev_stop_loss'u set et
                        if prev_stop_loss is None:
                            prev_stop_loss = float(stop_loss)
                        stop_loss = float(computed_sl)
            except Exception:
                pass

            # --- GÜVENLİ INDEX DÖNÜŞÜMÜ (FIX: datetime kolonunu tercih et + timestamp unit inference) ---
            def _infer_ts_unit(ts_series: pd.Series) -> str:
                """
                Epoch unit tahmini:
                  - s  ~ 1e9
                  - ms ~ 1e12
                  - us ~ 1e15
                  - ns ~ 1e18
                """
                try:
                    s = pd.to_numeric(ts_series, errors="coerce").dropna()
                    if s.empty:
                        return "ms"
                    v = float(s.median())
                    av = abs(v)
                    if av >= 1e17:
                        return "ns"
                    if av >= 1e14:
                        return "us"
                    if av >= 1e11:
                        return "ms"
                    return "s"
                except Exception:
                    return "ms"

            before_idx = len(df2)

            # 1) Öncelik: datetime kolonu (sende zaten datetime64[ns, UTC])
            if "datetime" in df2.columns:
                dt = pd.to_datetime(df2["datetime"], utc=True, errors="coerce")
                df2.index = dt

            # 2) Fallback: timestamp kolonu (unit otomatik tahmin)
            elif "timestamp" in df2.columns:
                unit = _infer_ts_unit(df2["timestamp"])
                df2.index = pd.to_datetime(df2["timestamp"], unit=unit, utc=True, errors="coerce")

            # 3) Fallback: mevcut index'i datetime'a çevir
            else:
                df2.index = pd.to_datetime(df2.index, utc=True, errors="coerce")

            # NaT temizle + sırala
            df2 = df2[~df2.index.isna()]
            df2 = df2.sort_index()

            after_idx = len(df2)
            if after_idx == 0:
                try:
                    ts_preview = None
                    if "timestamp" in df.columns:
                        s0 = pd.to_numeric(df["timestamp"], errors="coerce").dropna()
                        if not s0.empty:
                            ts_preview = {"min":float(s0.min()), "max":float(s0.max())}
                    logging.error(
                        f"[RENDER_INDEX_ALL_NAT] symbol={symbol} tf={timeframe} signal_id={signal_id} "
                        f"before_idx={before_idx} after_idx={after_idx} ts_preview={ts_preview} "
                        f"datetime_dtype={str(df.get('datetime').dtype) if isinstance(df, pd.DataFrame) and 'datetime' in df.columns else None}"
                    )
                except Exception:
                    pass
                return _none("INDEX_ALL_NAT_AFTER_PARSE", {"before_idx":before_idx, "after_idx":after_idx})

            if after_idx < 25:
                return _none("TOO_FEW_ROWS_AFTER_INDEX_CLEAN", {"before_idx":before_idx, "after_idx":after_idx})

            # --- YENİ: GİRİŞ OKU İNDEKS DÜZELTMESİ ---
            original_len = len(df2)
            render_candle_count = int(ConfigService.get("charts.rendering.candle_count", 150))

            if entry_index is not None and original_len > render_candle_count:
                candles_to_drop = original_len - render_candle_count
                new_entry_index = entry_index - candles_to_drop
            else:
                new_entry_index = entry_index

            if len(df2) > render_candle_count:
                df2 = df2.iloc[-render_candle_count:]

            if timeframe is None:
                timeframe = self._detect_timeframe_from_index(df2.index)

            base_cols = ['open', 'high', 'low', 'close']
            for c in base_cols:
                if c not in df2.columns:
                    logging.error(f"[ChartRenderer] Eksik kolon: {c}")
                    return _none(f"MISSING_COL:{c}")

                df2[c] = pd.to_numeric(df2[c], errors='coerce')

            # Dropna teşhis logu (kritik)
            before_dropna = len(df2)
            na_counts_before = {}
            try:
                na_counts_before = {c:int(df2[c].isna().sum()) for c in base_cols}
            except Exception:
                na_counts_before = {}

            df2 = df2.dropna(subset=base_cols)
            after_dropna = len(df2)

            logging.info(
                f"[RENDER_DF_CLEAN] symbol={symbol} tf={timeframe} signal_id={signal_id} "
                f"before_dropna={before_dropna} after_dropna={after_dropna} na_counts={na_counts_before}"
            )

            if after_dropna < 10:
                return _none(
                    f"TOO_FEW_CANDLES_AFTER_DROPNA(n={after_dropna})",
                    {"before_dropna":before_dropna, "after_dropna":after_dropna, "na_counts":na_counts_before}
                )

            # Bollinger Bantlarını hesapla
            try:
                if HAS_TALIB:
                    closes = df2['close'].values.astype(float)
                    bb_u, bb_m, bb_l = ta.BBANDS(closes, timeperiod=20, nbdevup=2, nbdevdn=2, matype=MA_Type.SMA)
                    df2['bb_upper'] = bb_u
                    df2['bb_middle'] = bb_m
                    df2['bb_lower'] = bb_l
            except Exception as e_bb:
                logging.warning(f"[ChartRenderer] Bollinger Bandı hesaplanamadı: {e_bb}")

            img = Image.new('RGBA', (width, height), (14, 18, 30, 255))
            draw = ImageDraw.Draw(img, 'RGBA')
            font_small = self._get_font(12, False)
            font_bold = self._get_font(16, True)

            # --- FİLİGRAN ---
            try:
                watermark_path = "assets/olimpos_logo.png"
                if os.path.exists(watermark_path):
                    watermark = Image.open(watermark_path).convert("RGBA")
                    wm_width = int(width * 0.5)
                    ratio = wm_width / watermark.width
                    wm_height = int(watermark.height * ratio)
                    watermark_resized = watermark.resize((wm_width, wm_height), Image.Resampling.LANCZOS)

                    alpha = watermark_resized.getchannel('A')
                    alpha = alpha.point(lambda i:i * 60 // 255)
                    watermark_resized.putalpha(alpha)

                    wm_x = (width - wm_width) // 2
                    wm_y = (height - wm_height) // 2
                    img.paste(watermark_resized, (wm_x, wm_y), watermark_resized)
            except Exception as wm_err:
                logging.warning(f"Filigran eklenirken hata: {wm_err}")
            # --- FİLİGRAN SONU ---

            left = 80
            right = 90
            top = 15
            bottom = 70
            usable_w = int(width - left - right)
            usable_h = int(height - top - bottom)
            # Index'i güvenli şekilde tek kolona çıkar (çakışma yok)
            candles = df2[base_cols].reset_index(drop=False)
            # reset_index ürettiği kolon adı bazen "index" bazen index.name (örn "datetime") olur.
            # Biz bunu tek standarda indiriyoruz: "timestamp_dt"

            first_col = candles.columns[0]
            if first_col != "timestamp":
                candles = candles.rename(columns={first_col:"timestamp"})

            n = int(len(candles))
            if n < 10:
                # normalde yukarıda yakalanır; yine de güvenlik
                return _none(f"TOO_FEW_CANDLES_POST_RESET(n={n})")

            low_series = candles['low']
            high_series = candles['high']
            pmin = float(low_series.min())
            pmax = float(high_series.max())

            if pmin == pmax:
                pmin *= 0.99
                pmax *= 1.01
            rng_base = float(pmax - pmin)

            # --- overlay fiyatlarını ölçeğe dahil et (ENTRY / SL / PREV_SL / TARGETS) ---
            overlay_prices: list[float] = []

            try:
                overlay_prices.append(float(entry_price))
            except Exception:
                pass

            try:
                overlay_prices.append(float(stop_loss))
            except Exception:
                pass

            try:
                if prev_stop_loss is not None:
                    overlay_prices.append(float(prev_stop_loss))
            except Exception:
                pass

            for t in (targets or [])[:5]:
                try:
                    overlay_prices.append(float(t))
                except Exception:
                    pass

            # overlay fiyatları üzerinden pmin/pmax genişlet
            for val in overlay_prices:
                if not isinstance(val, (int, float)) or not math.isfinite(float(val)):
                    continue
                val_float = float(val)
                pmin = float(min(pmin, val_float - rng_base * 0.08))
                pmax = float(max(pmax, val_float + rng_base * 0.10))

            mid_price = (pmax + pmin) / 2.0
            if mid_price > 0:
                rel_range = (pmax - pmin) / mid_price
                min_rel = 0.007
                if rel_range < min_rel:
                    expand = mid_price * min_rel / 2.0
                    pmax = mid_price + expand
                    pmin = mid_price - expand

            def y_price(price_value: float) -> float:
                v_float = float(price_value)
                rng = float(pmax - pmin)
                if rng == 0.0:
                    return float(top + usable_h / 2.0)
                return float(top + (1.0 - (v_float - pmin) / rng) * usable_h)

            # Eksen (sağ fiyat etiketleri + grid)
            self._draw_price_axis(
                draw, int(width), int(height),
                int(left), int(right), int(top), int(bottom),
                float(pmin), float(pmax), font_small, y_price
            )

            def x_index(current_idx: int) -> int:
                ci = int(current_idx)
                denom = float(max(1, n - 1))
                return int(left + int((float(ci) / denom) * float(usable_w)))

            # Giriş oku # YENİ:
            if new_entry_index is None and signal_time:
                new_entry_index = self._find_index_by_time(candles, signal_time)

            # Mumlar
            for idx_i in range(len(candles)):
                row = candles.iloc[idx_i]
                try:
                    o_val = float(row['open'])
                    c_val = float(row['close'])
                    hi_val = float(row['high'])
                    lo_val = float(row['low'])
                except (ValueError, TypeError):
                    continue

                x_pos = int(x_index(idx_i))
                color = (0, 200, 120, 255) if c_val >= o_val else (230, 70, 70, 255)
                y_hi = float(y_price(hi_val))
                y_lo = float(y_price(lo_val))
                draw.line([(float(x_pos), y_hi), (float(x_pos), y_lo)], fill=color, width=1)
                draw.rectangle(
                    [float(x_pos - 2), float(y_price(max(o_val, c_val))), float(x_pos + 2),
                        float(y_price(min(o_val, c_val)))],
                    fill=color, outline=color
                )
            if new_entry_index is not None:
                self._draw_entry_arrow(
                    draw, signal_type, int(new_entry_index), entry_price,
                    candles, x_index, y_price
                )

                # Hedef vurulma işaretleri
            if target_hits and targets and targets_hit_times:
                self._draw_target_marks(
                    draw, targets, target_hits, targets_hit_times, candles,
                    left, usable_w, n, usable_h, top, pmin, pmax, y_price, signal_type, x_index
                )

            # Bollinger çiz
            if HAS_TALIB and all(c in df2.columns for c in ['bb_upper', 'bb_middle', 'bb_lower']):
                bb_col_up = (255, 180, 60, 150)
                bb_col_mid = (120, 190, 255, 150)

                def _plot_line(series: Optional[pd.Series], line_color, line_width=2):
                    prev_xy = None
                    if series is None or series.empty:
                        return
                    for idx_l, val in series.reset_index(drop=True).items():
                        if pd.isna(val):
                            prev_xy = None
                            continue

                        x_pos_l = x_index(cast(int, idx_l))
                        y_pos_l = y_price(float(val))
                        if prev_xy is not None:
                            draw.line([prev_xy, (x_pos_l, y_pos_l)], fill=line_color, width=line_width)
                        prev_xy = (x_pos_l, y_pos_l)

                _plot_line(df2.get('bb_upper'), bb_col_up, 1)
                _plot_line(df2.get('bb_middle'), bb_col_mid, 1)
                _plot_line(df2.get('bb_lower'), bb_col_up, 1)

            # Giriş/Stop/Hedef çizgileri
            used_y_coords: set[float] = set()

            def label_line(price: float, text: str, color: tuple, y_px_offset: float = 0.0):
                if not isinstance(price, (int, float)) or not math.isfinite(price):
                    return

                base_y = y_price(price) + float(y_px_offset)
                y_pos = base_y
                attempts = 0
                while any(abs(y_pos - used_y) < 14 for used_y in used_y_coords) and attempts < 10:
                    y_pos += 12 * (-1 if attempts % 2 == 0 else 1)
                    attempts += 1
                used_y_coords.add(y_pos)

                for start_x in range(left, width - right, 14):
                    end_x = min(start_x + 7, width - right)
                    draw.line([(start_x, y_pos), (end_x, y_pos)], fill=color, width=1)

                label_text = f"{text}: {self._format_price(price)}"
                text_bbox = draw.textbbox((0, 0), label_text, font=font_small)
                text_width = text_bbox[2] - text_bbox[0]
                box_x = left + 15
                draw.rectangle((box_x - 5, y_pos - 10, box_x + text_width + 5, y_pos + 10), fill=(20, 28, 45, 180))
                draw.text((box_x, y_pos - 8), label_text, fill=color, font=font_small)

            # --- ENTRY / STOP / TARGET çizgileri ---
            label_line(entry_price, "Giriş", (255, 255, 0, 255))

            # ✅ Eski stop (varsa) - gri ve "Eski Stop" etiketi
            try:
                if prev_stop_loss is not None:
                    prev_sl = float(prev_stop_loss)
                    if math.isfinite(prev_sl) and prev_sl > 0:
                        label_line(prev_sl, "Eski Stop", (170, 170, 170, 200))
            except Exception:
                pass

            # ✅ Yeni stop - BE ise görsel ayrıştırma (girişle karışmasın)
            be_overlap = False
            try:
                ep = float(entry_price)
                sl = float(stop_loss)
                if ep > 0 and sl > 0:
                    # entry ile stop çok yakınsa "BE" say
                    be_overlap = abs(sl - ep) / ep < 0.0002  # 0.02%
            except Exception:
                be_overlap = False

            stop_label = "Stop(BE)" if be_overlap else "Stop"
            # BE ise stop çizgisini birkaç piksel kaydır (fiyat etiketi gerçek kalır)
            stop_offset_px = 8.0 if be_overlap else 0.0
            label_line(float(stop_loss), stop_label, (255, 80, 80, 255), y_px_offset=stop_offset_px)

            # targets
            for i, target_price in enumerate(targets or []):
                label_line(float(target_price), f"Hedef {i + 1}", (120, 255, 120, 255))

            # Patternler
            norm_patterns = self._normalize_patterns(patterns)
            chart_cfg = ConfigService.get('strategy.chart', {})
            draw_patterns = bool(chart_cfg.get('draw_patterns', True))
            show_neckline = bool(chart_cfg.get('show_neckline', True))

            if draw_patterns and norm_patterns:
                for pat_item in norm_patterns[:3]:
                    pname = str(pat_item.get('name') or pat_item.get('pattern') or '').lower()
                    if "double" in pname:
                        self._draw_double_structure(draw, pat_item, candles, x_index, y_price, font_small,
                            force_neck=show_neckline)

                    lines = pat_item.get('lines') or pat_item.get('raw_lines') or []
                    for ln in lines:
                        pts = ln.get('points') if isinstance(ln, dict) else None
                        if not pts:
                            continue
                        prev_xy_local: Optional[tuple[float, float]] = None
                        for pt in pts:
                            idx_local: Optional[int] = None
                            price_local: Optional[float] = None
                            if isinstance(pt, (tuple, list)) and len(pt) >= 2:
                                idx_local = int(pt[0])
                                price_local = float(pt[1])
                            elif isinstance(pt, dict) and 'idx' in pt and 'price' in pt:
                                idx_local = int(pt['idx'])
                                price_local = float(pt['price'])
                            if idx_local is None or price_local is None:
                                continue
                            if idx_local < 0 or idx_local >= n:
                                continue
                            x_local = float(x_index(int(idx_local)))
                            y_local = float(y_price(float(price_local)))
                            if prev_xy_local is not None:
                                draw.line([prev_xy_local, (x_local, y_local)],
                                    fill=ln.get('color', (190, 190, 80, 160)), width=2)
                            prev_xy_local = (x_local, y_local)

                    if show_neckline:
                        br = pat_item.get('breakout_level')
                        try:
                            if isinstance(br, (int, float)) and math.isfinite(float(br)):
                                yb: float = float(y_price(float(br)))
                                for breakout_start_x in range(80, int(width) - 90, 20):
                                    draw.line(
                                        [(float(breakout_start_x), yb),
                                            (float(min(breakout_start_x + 8, int(width) - 90)), yb)],
                                        fill=(255, 170, 0, 110), width=2
                                    )
                        except (ValueError, TypeError):
                            pass

                names = " | ".join([str(p.get('name') or p.get('pattern') or '') for p in norm_patterns[:3]])
                if names:
                    draw.rectangle((float(width // 2 - 200), 16.0, float(width // 2 + 200), 38.0),
                        fill=(30, 55, 95, 160))
                    draw.text((float(width // 2 - 188), 19.0), f"Formasyon: {names}", fill="white", font=font_small)

            # Time axis
            if 'timestamp' not in candles.columns and 'index' in candles.columns and len(candles) > 0:
                first_idx_val = candles['index'].iloc[0]
                if isinstance(first_idx_val, pd.Timestamp):
                    candles = candles.rename(columns={'index':'timestamp'})

            self._draw_time_axis(draw, candles, int(left), int(right), int(top), int(bottom), int(usable_w), font_small,
                x_index)

            # R:R
            rr_text = ""
            if isinstance(stop_loss, (int, float)) and targets:
                try:
                    risk = abs(float(entry_price) - float(stop_loss))
                    reward = abs(float(targets[0]) - float(entry_price))
                    if risk > 0 and reward > 0:
                        rr = reward / risk
                        rr_text = f" R:R {rr:.2f}"
                except (ValueError, TypeError, ZeroDivisionError) as exc_rr:
                    logging.error(f"Hata: {exc_rr}")

            title_col = "lime" if (signal_type or "").upper() == "LONG" else "red"
            draw.text((10.0, 4.0), f"{symbol} ({timeframe}) {signal_type.upper()}{rr_text}", fill=title_col,
                font=font_bold)

            # Meta overlay
            meta = strategy_meta or {}
            conf = (meta.get('confidence_index') or meta.get('confidence') or self._last_signal_meta.get(symbol,
                {}).get('confidence_index'))
            strat_id = meta.get('strategy_id') or meta.get('id') or meta.get('strategy')
            strat_class = meta.get('strategy_class')
            alarm_txt = f"AlarmID: {alarm_id}" if alarm_id else ""
            signal_txt = f"SignalID: {signal_id}" if signal_id else ""
            strat_line_parts = [p for p in [strat_id, strat_class, alarm_txt, signal_txt] if p]
            if conf is not None:
                strat_line_parts.append(f"Conf:{conf}")
            if strat_line_parts:
                overlay_text = " | ".join(str(p) for p in strat_line_parts)
                draw.rectangle((0.0, float(height - 26), float(width), float(height)), fill=(25, 45, 80, 200))
                draw.text((10.0, float(height - 22)), overlay_text, fill="white", font=font_small)

            buf = BytesIO()
            img.save(buf, format='PNG')
            buf.seek(0)
            return buf

        except Exception as exc_render:
            logging.error(f"[ChartRenderer] render hata: {repr(exc_render)}", exc_info=True)
            return None

    def _apply_chart_config(self) -> None:
        """
        JSON konfigürasyonundan tüm chart ayarlarını yükler ve sınıf değişkenlerine atar.
        Bu metot, ChartRenderer başlatıldığında çağrılır.
        """
        try:
            charts_config = ConfigService.get('charts', {})
            if not charts_config:
                logging.error("ConfigService'den 'charts' ayarları alınamadı. Varsayılanlar kullanılıyor.")
                return

            # Boyutlar
            dimensions = charts_config.get('dimensions', {})
            self.width = int(dimensions.get("default_width", 1200))
            self.height = int(dimensions.get("default_height", 1200))
            self.event_chart_height = int(dimensions.get("event_chart_height", 600))

            # Metin Ayarları
            text_settings = charts_config.get('text_settings', {})
            self.font_size_main = int(text_settings.get("main_font_size", 26))
            self.font_path_regular = text_settings.get("font_path_regular")
            self.font_path_bold = text_settings.get("font_path_bold")

            # Görsel Yolları
            self.image_paths = charts_config.get('images', {})
            self.pnl_tiers_config = charts_config.get('pnl_tiers', [])

            # Render Ayarları
            rendering = charts_config.get('rendering', {})
            self.jpeg_quality = int(rendering.get("jpeg_quality", 90))
            self.theme = rendering.get("theme", "dark")

            logging.info("✅ ChartRenderer konfigürasyonu JSON'dan başarıyla yüklendi.")

        except Exception as e:
            logging.error(f"❌ ChartRenderer config yükleme hatası: {e}", exc_info=True)

    @staticmethod
    def _ensure_aware(dt: Any) -> Optional[datetime]:
        if dt is None: return None
        if isinstance(dt, datetime):
            return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        if isinstance(dt, str):
            try:
                return pd.to_datetime(dt).to_pydatetime().replace(tzinfo=timezone.utc)
            except Exception:
                return None
        return None

    def _human_duration(self, start_dt: Any, end_dt: Optional[Any] = None) -> str:
        start_dt_aware = self._ensure_aware(start_dt)
        if not start_dt_aware: return "-"
        end_dt = self._ensure_aware(end_dt) or datetime.now(timezone.utc)
        delta = end_dt - start_dt_aware
        secs = int(delta.total_seconds())
        if secs < 0: return "0sn"
        if secs < 60: return f"{secs}sn"
        mins, secs = divmod(secs, 60)
        if mins < 60: return f"{mins}dk"
        hours, mins = divmod(mins, 60)
        if hours < 24: return f"{hours}sa {mins}dk"
        days, hours = divmod(hours, 24)
        return f"{days}g {hours}sa"

    @staticmethod
    def _fmt_price_dynamic(v: float) -> str:
        if v is None: return "-"
        v = float(v)
        av = abs(v)
        if av >= 100: return f"{v:.2f}"
        if av >= 1: return f"{v:.4f}"
        if av >= 0.1: return f"{v:.6f}"
        return f"{v:.8f}"

    @staticmethod
    def _effective_stop_from_signal(
        signal: dict,
        *,
        event_meta: Optional[dict] = None
    ) -> Tuple[float, str]:
        """
        Tek gerçek kaynak: Kart/metinlerde hangi stop yazılacak?
        Öncelik:
          1) event_meta.sl_new (varsa)
          2) target_hits -> görsel trailing stop (TP1=BE, TP2=TP1...)
          3) signal.stop_loss (ilk stop)

        Dönüş: (stop_value, label)  label: "Stop" veya "Stop(BE)"
        """
        entry = float(signal.get("entry_price") or 0.0)
        base_stop = float(signal.get("stop_loss") or 0.0)

        # 1) event_meta ile gelen SL update varsa onu kullan
        if isinstance(event_meta, dict):
            sl_new = event_meta.get("sl_new") or event_meta.get("stop_loss_new")
            if sl_new is not None:
                try:
                    slv = float(sl_new)
                    if slv > 0 and math.isfinite(slv):
                        # BE etiketi tespiti
                        if entry > 0 and abs(slv - entry) / entry < 0.0002:
                            return slv, "Stop(BE)"
                        return slv, "Stop"
                except Exception:
                    pass

        # 2) TP hitlerinden “görsel trailing stop” hesapla
        try:
            targets = (signal.get("targets") or [])[:5]
            hits = signal.get("targets_hit") or []
            sig_type = str(signal.get("signal_type") or "LONG")

            computed = ChartRenderer._compute_trailing_stop_from_hits(
                signal_type=sig_type,
                entry_price=entry,
                targets=[float(t) for t in targets],
                target_hits=[bool(x) for x in hits],
                default_stop=base_stop
            )
            if computed is not None and float(computed) > 0 and math.isfinite(float(computed)):
                slv = float(computed)
                if entry > 0 and abs(slv - entry) / entry < 0.0002:
                    return slv, "Stop(BE)"
                return slv, "Stop"
        except Exception:
            pass

        # 3) fallback: ilk stop
        if entry > 0 and base_stop > 0 and abs(base_stop - entry) / entry < 0.0002:
            return base_stop, "Stop(BE)"
        return base_stop, "Stop"


    @staticmethod
    def _load_icon(path: str, size: tuple[int, int] = (36, 36)) -> Optional[Image.Image]:
        if path and os.path.exists(path):
            try:
                return Image.open(path).convert('RGBA').resize(size)
            except Exception:
                return None
        return None

    async def build_signal_template_text(self, signal: dict, current_price: Optional[float] = None,
            user_id: Optional[int] = None):
        direction = signal.get('signal_type', 'LONG')
        sym = signal.get('symbol', '?')
        leverage = 1.0
        if user_id:
            exchange_name = (signal.get('meta', {}) or {}).get('exchange', 'mexc')
            settings = get_user_settings(user_id, exchange_name)
            if settings and settings.get('leverage'):
                leverage = float(settings['leverage'])

        entry = signal.get('entry_price', 0)
        upnl = 0.0
        if current_price is not None and entry != 0:
            upnl = ((current_price - entry) / entry * 100) if direction == 'LONG' else (
                        (entry - current_price) / entry * 100)

        stop_val, stop_label = self._effective_stop_from_signal(signal, event_meta=None)

        lines = [
            f"Sembol  : {sym}",
            f"[{direction} | {signal.get('strategy_id', 'V1').upper()}]YENİ SİNYAL",
            f"Sinyal ID: {signal.get('signal_id', '?')}",
            f"UPNL: {upnl:+.2f}% (x{int(leverage):.0f} = {upnl * leverage:+.2f}%)",
            "",
            f"Giriş   : {self._fmt_price_dynamic(entry)}",
            f"{stop_label:<7}: {self._fmt_price_dynamic(stop_val)}",
            "Hedefler:",
        ]

        targets = signal.get('targets', [])[:5]
        hits = signal.get('targets_hit', [])
        hit_times = signal.get('targets_hit_times', [])
        signal_time = self._ensure_aware(signal.get('signal_time'))

        # Hedef satırları döngüsü (alarm_strateji.py içinde build_signal_template_text fonksiyonu)
        lines_targets = []
        for idx, t in enumerate(targets, 1):
            is_hit = idx <= len(hits) and hits[idx - 1]
            icon = "✅" if is_hit else "⭕"

            # Ham yüzde
            raw_pct = ((t - entry) / entry * 100) if entry else 0
            if direction == 'SHORT': raw_pct = -raw_pct

            # Kaldıraçlı PNL
            lev_pnl = raw_pct * leverage

            # Süre
            duration_str = ""
            if is_hit and idx <= len(hit_times) and hit_times[idx - 1]:
                hit_time = self._ensure_aware(hit_times[idx - 1])
                if hit_time and signal_time:
                    duration_str = f" ({self._human_duration(signal_time, hit_time)})"

            # FORMAT: T1: Fiyat (%PNL) (Süre)
            lines_targets.append(f"{icon} T{idx}: {self._fmt_price_dynamic(t)} (%{lev_pnl:.2f}){duration_str}")

        exit_type = signal.get('exit_type')
        durum = "STOP" if exit_type == 'STOP' else "Tamamlandı" if exit_type == 'TARGET_FINAL' else "Aktif"
        total_dur = self._human_duration(signal_time) if signal_time else "-"

        lines.extend([
            "",
            f"Durum   : {durum}",
            f"Açılış  : {signal_time.strftime('%H:%M:%S') if signal_time else '-'} (Süre: {total_dur})"
        ])
        return "\n".join(lines)

    async def render_main_with_targets(
            self,
            signal: dict,
            current_price: float = None,
            user_id: int = None,
            chart_buf: BytesIO = None,
            only_chart: bool = False
    ):
        """
        Ana görsel oluşturucu. JSON ayarlarını kullanır.
        only_chart=True ise sadece grafiği döndürür.

        - Hedef ikonları event kartı ile aynı: okey1.png / beklenen_hedef1.png / stop3.png
        - STOP olunca vurulmayan hedefler stop3.png ile işaretlenir
        - Vurulan hedeflerde süre gösterimi
        - En altta "Açılış" satırı sade: saat + (Süre: ...)
        """
        try:
            # AYARLARI YÜKLE
            settings = load_json_settings()

            # JSON Boyutları
            canvas_w = int(settings.get('chart_width', 1400))
            chart_h = int(settings.get('chart_height', 700))
            panel_h = int(settings.get('main_panel_height', 700))
            total_h = chart_h + panel_h

            # 1) Grafik Yükle
            chart_img = None
            if chart_buf:
                try:
                    chart_buf.seek(0)
                    chart_img = Image.open(chart_buf).convert("RGB")
                except Exception as e:
                    logging.error(f"[RENDER_MAIN] Chart open error: {e}")

            if not chart_img and signal.get('chart_buf_raw'):
                try:
                    chart_img = Image.open(BytesIO(signal['chart_buf_raw'])).convert("RGB")
                except Exception:
                    pass

            if not chart_img:
                return None

            # --- SAF GRAFİK MODU ---
            if only_chart:
                chart_img = chart_img.resize((canvas_w, chart_h))
                out = BytesIO()
                chart_img.save(out, format="JPEG", quality=95)
                out.seek(0)
                return out

            # --- ANA MESAJ OLUŞTURMA ---
            base = Image.new("RGB", (canvas_w, total_h), (13, 14, 18))

            # Grafiği yerleştir
            chart_img = chart_img.resize((canvas_w, chart_h))
            base.paste(chart_img, (0, 0))

            # MASKOT (Ayı/Boğa)
            direction = signal.get('signal_type', 'LONG')
            mascot_img = None
            try:
                img_name = "long1.png" if direction == "LONG" else "short1.png"
                mascot_img = load_asset_image(img_name)
            except Exception:
                mascot_img = None

            if mascot_img:
                target_h = panel_h - 20
                ratio = target_h / mascot_img.height
                target_w = int(mascot_img.width * ratio)
                mascot_img = mascot_img.resize((target_w, target_h))

                mascot_x = canvas_w - target_w - 50
                mascot_y = chart_h + 10
                base.paste(mascot_img, (mascot_x, mascot_y), mascot_img)

            # METİNLER
            draw = ImageDraw.Draw(base)

            def get_font(name, size):
                try:
                    return ImageFont.truetype(os.path.join("assets", name), size)
                except Exception:
                    try:
                        return ImageFont.truetype("arial.ttf", size)
                    except Exception:
                        return ImageFont.load_default()

            font_title = get_font("arialbd.ttf", 40)
            font_subtitle = get_font("arial.ttf", 28)
            font_bold = get_font("arialbd.ttf", 28)
            font_text = get_font("arial.ttf", 26)

            x = 50
            y = chart_h + 40
            line_spacing = 40

            # Veriler
            symbol = signal.get('symbol', 'UNKNOWN')
            strategy = str(signal.get('strategy_id', 'V1')).upper()
            sig_id = signal.get('signal_id', '---')
            entry = float(signal.get('entry_price') or 0)
            stop, stop_label = self._effective_stop_from_signal(signal, event_meta=None)

            leverage = 20
            try:
                if user_id:
                    user_settings = get_user_settings(user_id, 'mexc')
                    if user_settings:
                        leverage = int(float(user_settings.get('leverage', 20)))
            except Exception:
                pass

            # Başlık
            title_text = f"[{direction} | {strategy}] SİNYAL"
            title_color = (0, 255, 0) if direction == 'LONG' else (255, 50, 50)
            draw.text((x, y), title_text, font=font_title, fill=title_color)
            y += line_spacing + 10

            # Sembol & ID
            draw.text((x, y), f"Sembol : {symbol}", font=font_subtitle, fill=(220, 220, 220))
            y += line_spacing
            draw.text((x, y), f"Sinyal ID: {sig_id}", font=font_subtitle, fill=(180, 180, 180))
            y += line_spacing

            # UPNL
            curr_upnl = 0.0
            if current_price is not None and entry > 0:
                if direction == 'LONG':
                    curr_upnl = (current_price - entry) / entry * 100
                else:
                    curr_upnl = (entry - current_price) / entry * 100
            lev_upnl = curr_upnl * leverage

            upnl_color = (0, 255, 0) if lev_upnl >= 0 else (255, 50, 50)
            upnl_text = f"UPNL: %{lev_upnl:.2f}"
            draw.text((x, y), upnl_text, font=font_bold, fill=upnl_color)

            lev_text = f" (x{leverage})"
            w_upnl = draw.textlength(upnl_text, font=font_bold)
            draw.text((x + w_upnl + 10, y), lev_text, font=font_bold, fill=(255, 215, 0))
            y += line_spacing + 20

            # Giriş / Stop
            draw.text((x, y), f"Giriş : {entry}", font=font_text, fill=(255, 255, 255))
            y += line_spacing
            stop_dist_raw = abs(entry - stop) / entry * 100 if entry else 0.0
            stop_dist_lev = stop_dist_raw * leverage

            draw.text(
                (x, y),
                f"{stop_label} : {stop} (%{stop_dist_lev:.2f})",
                font=font_text,
                fill=(255, 100, 100)
            )

            y += line_spacing + 10

            # Hedefler
            draw.text((x, y), "Hedefler:", font=font_bold, fill=(255, 255, 255))
            y += line_spacing

            targets = (signal.get('targets', []) or [])[:5]
            hits = signal.get('targets_hit', []) or []
            hit_times = signal.get('targets_hit_times', []) or []

            # STOP durumu tespiti
            exit_type = signal.get('exit_type')
            stop_loss_hit = bool(signal.get('stop_loss_hit'))
            is_stop_state = (exit_type == 'STOP') or stop_loss_hit

            # Sinyal zamanı (helper'larla)
            signal_time_aware = self._ensure_aware(signal.get('signal_time'))

            # İKONLAR (event kartıyla birebir aynı isimler)
            icon_ok = load_asset_image("okey1.png")
            icon_wait = load_asset_image("beklenen_hedef1.png")
            icon_stop = load_asset_image("stop3.png")

            if icon_ok:
                icon_ok = icon_ok.resize((40, 40))
            if icon_wait:
                icon_wait = icon_wait.resize((40, 40))
            if icon_stop:
                icon_stop = icon_stop.resize((40, 40))

            for i, t in enumerate(targets):
                is_hit = (i < len(hits) and bool(hits[i]))

                # ikon + renk seçimi
                current_icon = icon_wait
                text_color = (180, 180, 180)

                if is_hit:
                    current_icon = icon_ok
                    text_color = (0, 255, 0)
                elif is_stop_state:
                    current_icon = icon_stop
                    text_color = (120, 120, 120)

                # yüzde / pnl
                raw_pct = 0.0
                if entry > 0:
                    if direction == 'LONG':
                        raw_pct = (float(t) - entry) / entry * 100
                    else:
                        raw_pct = (entry - float(t)) / entry * 100
                lev_pnl = raw_pct * leverage

                # süre (vurulduysa) -> sınıf helper'ı ile
                dur_str = ""
                if is_hit and i < len(hit_times) and hit_times[i] and signal_time_aware:
                    hit_time_aware = self._ensure_aware(hit_times[i])
                    if hit_time_aware:
                        dur_str = f" ({self._human_duration(signal_time_aware, hit_time_aware)})"

                # ikon bas + metni bas
                text_x = x
                if current_icon:
                    base.paste(current_icon, (x, y + 2), current_icon)
                    text_x = x + 55

                line_text = f"T{i + 1}: {t} (%{lev_pnl:.2f}){dur_str}"
                draw.text((text_x, y), line_text, font=font_text, fill=text_color)
                y += line_spacing

            # Durum
            y += 20
            is_active = bool(signal.get('active', True))
            status = "Aktif" if is_active else "Kapalı"
            draw.text((x, y), f"Durum : {status}", font=font_text, fill=(255, 255, 255))
            y += line_spacing

            # Açılış : saat + Süre (helper kullanarak, kalabalık yok)
            if signal_time_aware:
                open_clock = signal_time_aware.strftime("%H:%M:%S")
                total_dur = self._human_duration(signal_time_aware)
                open_line = f"Açılış : {open_clock} (Süre: {total_dur})"
            else:
                open_line = "Açılış : - (Süre: -)"

            draw.text((x, y), open_line, font=font_text, fill=(180, 180, 180))
            # ℹ️ Hesap satırı (TP/SL metodunu göster)
            calc = (signal.get("meta") or {}).get("calc_method") or {}
            tp_m = str(calc.get("tp") or "").strip()
            sl_m = str(calc.get("sl") or "").strip()

            if tp_m or sl_m:
                y += line_spacing
                info_line = f"ℹ️ Hesap: Hedefler({tp_m or '-'}) | SL({sl_m or '-'})"
                draw.text((x, y), info_line, font=font_text, fill=(160, 160, 160))

            out = BytesIO()
            base.save(out, format="JPEG", quality=95)
            out.seek(0)
            return out

        except Exception as e:
            logging.error(f"Render Main Error: {e}", exc_info=True)
            return None

    @staticmethod
    async def render_event_card(signal: dict, event_type: str, event_meta: dict, user_id: int = None,
            chart_buf: BytesIO = None):
        """
        JSON Ayarlı, Rozetli, Maskotlu ve Hesaplamalı Olay Kartı.
        GÜNCELLEME:
        1. Kaynak metni 'AI COIN TARAMASI' veya 'STRATEJİ BAZLI TARAMA' olarak düzeltildi.
        2. Kâr rozetleri (Tier) sadece 'FINAL' olayında görünür hale getirildi.
        """
        try:
            # 1. AYARLARI YÜKLE (JSON'dan Boyutları Al)
            settings = load_json_settings()

            # JSON'da yoksa varsayılan değerler
            canvas_w = int(settings.get('chart_width', 1500))
            chart_h = int(settings.get('chart_height', 750))
            panel_h = int(settings.get('panel_height', 750))  # Panel yüksekliği
            total_h = chart_h + panel_h

            # 2. GRAFİĞİ HAZIRLA
            chart_img = None
            if chart_buf:
                try:
                    chart_buf.seek(0)
                    chart_img = Image.open(chart_buf).convert("RGB")
                except: pass

            if not chart_img and signal.get('chart_buf_raw'):
                try:
                    chart_img = Image.open(BytesIO(signal['chart_buf_raw'])).convert("RGB")
                except: pass

            if not chart_img:
                return None

            # Kırpma ve Boyutlandırma (JSON boyutlarına göre)
            if chart_img.height > (chart_h + 50):
                chart_img = chart_img.crop((0, 0, chart_img.width, chart_h))

            if chart_img.width != canvas_w or chart_img.height != chart_h:
                chart_img = chart_img.resize((canvas_w, chart_h))

            # 3. CANVAS OLUŞTUR
            base = Image.new("RGB", (canvas_w, total_h), (10, 12, 16))  # Koyu zemin
            base.paste(chart_img, (0, 0))

            draw = ImageDraw.Draw(base)

            # 4. VERİLERİ HAZIRLA
            symbol = signal.get('symbol', 'UNKNOWN')

            # --- DÜZELTME 1: KAYNAK METNİ ---
            raw_source = str(signal.get('source', '')).lower()
            if 'ai' in raw_source:
                source_text = "Kaynak: AI COIN TARAMASI"
            else:
                source_text = "Kaynak: STRATEJİ BAZLI TARAMA"
            # --------------------------------
            direction = signal.get('signal_type', 'LONG')
            strategy_ver = signal.get('strategy_id', 'V1')
            sig_id = signal.get('signal_id', '---')
            entry = float(signal.get('entry_price') or 0)
            stop, stop_label = ChartRenderer._effective_stop_from_signal(signal, event_meta=event_meta)

            # Kaldıraç
            leverage = 20
            try:
                if user_id:
                    user_settings = get_user_settings(user_id, 'mexc')
                    if user_settings:
                        leverage = int(float(user_settings.get('leverage', 20)))
            except: pass

            # --- MASKOT EKLEME (Ayı/Boğa) ---
            mascot_img = None
            try:
                img_name = "long1.png" if direction == "LONG" else "short1.png"
                mascot_img = load_asset_image(img_name)
            except: pass

            if mascot_img:
                # Panelin yüksekliğine göre ayarla
                target_mascot_h = panel_h - 40
                ratio = target_mascot_h / mascot_img.height
                target_mascot_w = int(mascot_img.width * ratio)
                mascot_img = mascot_img.resize((target_mascot_w, target_mascot_h))

                # Sağ alt köşeye, panelin içine yerleştir
                m_x = canvas_w - target_mascot_w - 20
                m_y = chart_h + 20
                base.paste(mascot_img, (m_x, m_y), mascot_img)

            # Fontlar
            def get_font(name, size):
                try:
                    return ImageFont.truetype(os.path.join("assets", name), size)
                except:
                    try: return ImageFont.truetype("arial.ttf", size)
                    except: return ImageFont.load_default()

            font_header = get_font("arialbd.ttf", 55)
            font_sub = get_font("arial.ttf", 32)
            font_bold = get_font("arialbd.ttf", 36)
            font_list = get_font("arial.ttf", 34)
            font_meta = get_font("arial.ttf", 28)
            font_event_title = get_font("arialbd.ttf", 48)
            font_profit = get_font("arialbd.ttf", 40)

            # 5. PANEL ÇİZİMİ
            current_y = chart_h + 40
            left_margin = 50

            # A) SEMBOL
            draw.text((left_margin, current_y), f"{symbol}", font=font_header, fill="white")
            current_y += 65

            # B) KAYNAK (Düzeltilmiş Metin)
            draw.text((left_margin, current_y), source_text, font=font_sub, fill="lightgray")
            current_y += 45

            # C) YÖN ve VERSİYON
            dir_color = (0, 255, 0) if direction == 'LONG' else (255, 50, 50)
            draw.text((left_margin, current_y), f"{direction} | {strategy_ver}", font=font_bold, fill=dir_color)
            current_y += 45

            # D) SİNYAL ID
            draw.text((left_margin, current_y), f"ID: {sig_id}", font=font_sub, fill="gray")
            current_y += 45

            # E) KALDIRAÇ
            draw.text((left_margin, current_y), f"Kaldıraç: x{leverage}", font=font_bold, fill=(255, 215, 0))
            current_y += 60

            # F) OLAY BAŞLIĞI ve KAR HESAPLAMA
            target_num = event_meta.get('target_num', 0)
            targets = signal.get('targets', [])

            # ✅ Öncelik: event_meta’dan gelenler
            final_profit = None

            try:
                if event_type in ("TARGET", "FINAL"):
                    if isinstance(event_meta, dict) and "target_pnl_lev" in event_meta:
                        final_profit = float(event_meta.get("target_pnl_lev"))
                    elif isinstance(event_meta, dict) and "pnl_lev" in event_meta:
                        final_profit = float(event_meta.get("pnl_lev"))
                else:  # STOP
                    if isinstance(event_meta, dict) and "pnl_lev" in event_meta:
                        final_profit = float(event_meta.get("pnl_lev"))
            except Exception:
                final_profit = None

            # Fallback: eski hesap (event_meta gelmezse)
            if final_profit is None:
                calculated_profit = 0.0
                if event_type in ('TARGET', 'FINAL') and target_num > 0 and len(targets) >= target_num:
                    hit_price = targets[target_num - 1]
                    if entry > 0:
                        if direction == 'LONG':
                            calculated_profit = (hit_price - entry) / entry * 100 * leverage
                        else:
                            calculated_profit = (entry - hit_price) / entry * 100 * leverage
                elif event_type == 'STOP':
                    if entry > 0:
                        if direction == 'LONG':
                            calculated_profit = (stop - entry) / entry * 100 * leverage
                        else:
                            calculated_profit = (entry - stop) / entry * 100 * leverage
                final_profit = float(calculated_profit)

            event_text_part1 = ""
            event_text_part2 = ""
            event_color = "white"

            if event_type == 'STOP':
                event_text_part1 = "STOP LOSS OLDU"
                event_text_part2 = f" | ZARAR: %{final_profit:.2f}"
                event_color = (255, 50, 50)
            elif event_type in ('TARGET', 'FINAL'):
                # Eğer FINAL ise başlığı ona göre düzenle
                if event_type == 'FINAL':
                    event_text_part1 = f"FİNAL HEDEF VURULDU"
                else:
                    event_text_part1 = f"HEDEF {target_num} VURULDU"

                event_text_part2 = f" | KAR: %{final_profit:.2f}"
                event_color = (0, 255, 0)

            draw.text((left_margin, current_y), event_text_part1, font=font_event_title, fill=event_color)
            w_part1 = draw.textlength(event_text_part1, font=font_event_title)
            draw.text((left_margin + w_part1, current_y + 8), event_text_part2, font=font_profit, fill=event_color)

            current_y += 60

            # G) HEDEFLER LİSTESİ
            hits = signal.get('targets_hit', [])
            hit_times = signal.get('targets_hit_times', [])
            signal_time = signal.get('signal_time')
            if isinstance(signal_time, str):
                try: signal_time = datetime.fromisoformat(signal_time)
                except: pass

            icon_ok = load_asset_image("okey1.png")
            icon_wait = load_asset_image("beklenen_hedef1.png")
            icon_stop = load_asset_image("stop3.png")

            if icon_ok: icon_ok = icon_ok.resize((40, 40))
            if icon_wait: icon_wait = icon_wait.resize((40, 40))
            if icon_stop: icon_stop = icon_stop.resize((40, 40))

            for i, t in enumerate(targets):
                is_hit = (i < len(hits) and hits[i])

                current_icon = icon_wait
                text_color = (180, 180, 180)

                if is_hit:
                    current_icon = icon_ok
                    text_color = (0, 255, 0)
                elif event_type == 'STOP':
                    current_icon = icon_stop
                    text_color = (100, 100, 100)

                raw_pct = 0.0
                if entry > 0:
                    if direction == 'LONG': raw_pct = (t - entry) / entry * 100
                    else: raw_pct = (entry - t) / entry * 100
                lev_pnl = raw_pct * leverage

                dur_str = ""
                if is_hit and i < len(hit_times) and hit_times[i]:
                    try:
                        ht = hit_times[i]
                        if isinstance(ht, str): ht = datetime.fromisoformat(ht)
                        if signal_time and ht:
                            diff = ht - signal_time
                            days = diff.days
                            seconds = diff.seconds
                            hours = seconds // 3600
                            minutes = (seconds % 3600) // 60
                            if days > 0: dur_str = f"({days}g {hours}s)"
                            elif hours > 0: dur_str = f"({hours}s {minutes}dk)"
                            else: dur_str = f"({minutes}dk)"
                    except: pass

                if current_icon:
                    base.paste(current_icon, (left_margin, current_y + 5), current_icon)

                line_text = f"T{i + 1}: {t} (%{lev_pnl:.2f}) {dur_str}"
                draw.text((left_margin + 50, current_y), line_text, font=font_list, fill=text_color)
                current_y += 50

            current_y += 20

            # H) FOOTER
            draw.line([(left_margin, current_y), (canvas_w - left_margin, current_y)], fill="gray", width=2)
            current_y += 20

            # ✅ SL güncellemesi (sadece TARGET/FINAL ve meta varsa)
            try:
                if event_type in ("TARGET", "FINAL") and isinstance(event_meta, dict):
                    if "sl_old" in event_meta and "sl_new" in event_meta:
                        sl_old = float(event_meta.get("sl_old") or 0.0)
                        sl_new = float(event_meta.get("sl_new") or 0.0)
                        sl_rule = str(event_meta.get("sl_rule") or "")
                        if sl_old > 0 and sl_new > 0 and sl_old != sl_new:
                            draw.text(
                                (left_margin, current_y),
                                f"SL Güncelleme: {ChartRenderer._fmt_price_dynamic(sl_old)} → {ChartRenderer._fmt_price_dynamic(sl_new)} {sl_rule}",
                                font=font_sub,
                                fill=(255, 215, 0)  # altın sarısı
                            )
                            current_y += 40
            except Exception:
                pass

            draw.text((left_margin, current_y), f"{stop_label}: {stop}  |  Giriş: {entry}", font=font_bold, fill="white")
            current_y += 45

            s_time_str = signal_time.strftime("%Y-%m-%d %H:%M") if isinstance(signal_time, datetime) else "-"
            alarm_id = signal.get('alarm_id', '-')
            draw.text((left_margin, current_y), f"Alarm ID: {alarm_id} | Tarih: {s_time_str}", font=font_meta,
                fill="lightgray")
            current_y += 40

            is_active = signal.get('active', True)
            status_text = "Pozisyon Aktif Takip Ediliyor" if is_active else "Pozisyon Kapalı"
            status_color = (0, 255, 0) if is_active else (255, 50, 50)
            draw.text((left_margin, current_y), status_text, font=font_bold, fill=status_color)

            # 6. ROZET (TIER) - SADECE FINALDE
            # --- DÜZELTME 2: ROZET SADECE FINALDE GELSİN ---
            badge_img = None
            if event_type == 'STOP':
                b_name = "long_stop_loss1.png" if direction == 'LONG' else "short_stop_loss1.png"
                badge_img = load_asset_image(b_name)
            elif event_type == 'FINAL':  # Sadece FINAL ise kâr rozeti koy
                if final_profit >= 150:
                    b_name = "final_long_tier3.png" if direction == 'LONG' else "final_short_tier3.png"
                    badge_img = load_asset_image(b_name)
                elif final_profit >= 100:
                    b_name = "final_long_tier2.png" if direction == 'LONG' else "final_short_tier2.png"
                    badge_img = load_asset_image(b_name)
                elif final_profit >= 50:
                    b_name = "final_long_tier1.png" if direction == 'LONG' else "final_short_tier1.png"
                    badge_img = load_asset_image(b_name)
            # ------------------------------------------------

            if badge_img:
                if badge_img.width > 300:
                    ratio = 300 / badge_img.width
                    badge_img = badge_img.resize((300, int(badge_img.height * ratio)))

                b_x = canvas_w - badge_img.width - 30
                b_y = chart_h + 30
                base.paste(badge_img, (b_x, b_y), badge_img)

            out = BytesIO()
            base.save(out, format="JPEG", quality=95)
            out.seek(0)
            return out

        except Exception as e:
            logging.error(f"Render Event Card Error: {e}", exc_info=True)
            return None

    async def compose_event_composite(self, signal: dict, event_type: str, target_index: Optional[int] = None) ->\
            Optional[BytesIO]:
        """
        Sinyal olayı için composite görsel oluşturur (TARGET/FINAL/STOP).
        DÜZELTME:
        - 'The truth value of a DataFrame is ambiguous' hatası giderildi.
        - 'getsize' yerine 'getbbox' kullanıldı.
        - 'tz' hatası için _ensure_aware çağrıları düzeltildi.
        - Kullanılmayan parametreler ve değişkenler kaldırıldı.
        """   
        canvas_width = int(ConfigService.get('charts.dimensions.default_width', 1200))
        canvas_height = int(ConfigService.get('charts.dimensions.default_height', 1200))
        chart_height = int(ConfigService.get('charts.dimensions.event_chart_height', 600))
        panel_height = canvas_height - chart_height
        final_image = Image.new('RGB', (canvas_width, canvas_height))

        try:
            symbol_val = signal.get('symbol', '')
            timeframe_val = signal.get('timeframe', '15m')
            direction_val = signal.get('signal_type', 'LONG')

            # --- OHLCV VERİSİ ---
            # --- KÖK NEDEN DÜZELTMESİ ---
            # Bu fonksiyonun görevi yeni bir grafik çizmek DEĞİL, sinyalde saklanan
            # ve `monitor_active_signals` tarafından güncellenen `chart_buf_raw` verisini kullanmaktır.
            # Bu sayede her zaman en güncel grafik olay kartına eklenir.
            chart_img = None
            if signal.get('chart_buf_raw'):
                try:
                    chart_img = Image.open(BytesIO(signal['chart_buf_raw'])).convert('RGB')
                    chart_img = chart_img.resize((canvas_width, chart_height))
                except (IOError, UnidentifiedImageError) as img_err:
                    logging.error(f"Olay kartı için `chart_buf_raw` verisinden grafik açılamadı: {img_err}")

            if chart_img is None:
                chart_img = Image.new('RGB', (canvas_width, chart_height), (20, 20, 25))
                draw_fallback = ImageDraw.Draw(chart_img)
                fallback_font = self._get_font(24, bold=True)
                draw_fallback.text((50, chart_height // 2), "Grafik verisi bulunamadı.", fill=(150, 150, 160),
                    font=fallback_font)

            # --- ARKA PLAN VE PANEL ---
            bg_path_key = f"charts.images.signal_background_{str(direction_val).lower()}"
            bg_path = ConfigService.get(bg_path_key)
            if bg_path and os.path.exists(bg_path):
                background = Image.open(bg_path).convert('RGB').resize((canvas_width, panel_height))
            else:
                fallback_color = (0, 40, 0) if direction_val == 'LONG' else (40, 0, 0)
                background = Image.new('RGB', (canvas_width, panel_height), fallback_color)

            panel = Image.new('RGBA', (canvas_width, panel_height), (0, 0, 0, 180))
            draw = ImageDraw.Draw(panel)

            # --- FONT VE METİN AYARLARI ---
            f_title = self._get_font(int(ConfigService.get('charts.text_settings.main_font_size', 32)), bold=True)
            f_sub = self._get_font(int(ConfigService.get('charts.text_settings.template_font_size', 26)), bold=True)
            f_info = self._get_font(int(ConfigService.get('charts.text_settings.template_font_size', 24)))
            f_small = self._get_font(int(ConfigService.get('charts.text_settings.template_font_size', 20)))

            # --- BAŞLIK BİLGİLERİ ---
            strat = str(signal.get('strategy_id', 'V1')).upper()
            sig_id = signal.get('signal_id', '?')
            header = f"{symbol_val} ({direction_val}) {strat}"
            draw.text((40, 20), header, font=f_title, fill=(255, 255, 255))
            draw.text((40, 60), f"Signal ID: {sig_id}", font=f_sub, fill=(200, 200, 200))

            # --- OLAY BAŞLIĞI ---
            if event_type == 'TARGET':
                subtitle = f"Hedef T{target_index} Vuruldu"
                sub_color = (100, 255, 100)
            elif event_type == 'FINAL':
                subtitle = "TÜM HEDEFLER → FINAL"
                sub_color = (100, 255, 100)
            else:  # STOP
                subtitle = "STOP LOSS"
                sub_color = (255, 100, 100)
            draw.text((40, 100), subtitle, font=f_sub, fill=sub_color)

            # --- PNL HESAPLAMA ---
            entry_price = float(signal.get('entry_price', 0))
            leverage = 1.0
            user_id = signal.get('user_id')
            if user_id:
                exchange_name = (signal.get('meta', {}) or {}).get('exchange', 'mexc')
                settings = get_user_settings(user_id, exchange_name)
                if settings and settings.get('leverage'):
                    leverage = float(settings['leverage'])

            current_price = signal.get('last_price_on_event', entry_price)
            targets = signal.get('targets', [])
            hits = signal.get('targets_hit', [])

            # Eğer bir hedef vurulduysa, o hedefin fiyatını `current_price` olarak kullan
            if event_type == 'TARGET' and target_index is not None and target_index <= len(targets):
                current_price = targets[target_index - 1]
            elif event_type == 'FINAL' and any(hits) and targets:
                try:
                    last_hit_idx = max([i for i, hit in enumerate(hits) if hit])
                    current_price = targets[last_hit_idx]
                except (ValueError, IndexError):
                    pass  # Fallback to last_price_on_event

            upnl = ((current_price - entry_price) / entry_price * 100) if entry_price else 0
            if direction_val == 'SHORT': upnl = -upnl
            upnl_leveraged = upnl * leverage

            # --- HEDEF LİSTESİ ÇİZİMİ ---
            start_y = 140
            row_height = 55
            icon_size = (int(ConfigService.get('charts.dimensions.icon_size', [36, 36])[0]),
                int(ConfigService.get('charts.dimensions.icon_size', [36, 36])[1]))
            icon_hit = self._load_icon(self.image_paths.get("target_hit_icon"), icon_size)
            icon_pending = self._load_icon(self.image_paths.get("target_pending_icon"), icon_size)
            icon_stop = self._load_icon(self.image_paths.get("stop_icon"), icon_size)

            for i, target in enumerate(targets[:5]):
                y_pos = start_y + i * row_height
                is_hit = i < len(hits) and hits[i]
                is_stop_event = event_type == 'STOP'

                icon_to_draw = icon_hit if is_hit else (icon_stop if is_stop_event else icon_pending)
                if icon_to_draw:
                    panel.paste(icon_to_draw, (40, y_pos), icon_to_draw)

                target_pct = ((target - entry_price) / entry_price * 100) if entry_price else 0
                if direction_val == 'SHORT': target_pct = -target_pct
                target_text = f"T{i + 1} {self._fmt_price_dynamic(target)} ({target_pct:+.2f}%)"
                draw.text((90, y_pos + 5), target_text, font=f_info, fill=(255, 255, 255))

                duration = "-"
                hit_times = signal.get('targets_hit_times', [])
                signal_time = self._ensure_aware(signal.get('signal_time'))
                if is_hit and i < len(hit_times) and hit_times[i] and signal_time:
                    hit_time = self._ensure_aware(hit_times[i])
                    if hit_time:
                        delta = hit_time - signal_time
                        if delta.total_seconds() > 0:
                            duration = self._human_duration(signal_time, hit_time)
                draw.text((90, y_pos + 30), f"Süre: {duration}", font=f_small, fill=(180, 180, 180))

            # --- ALT BİLGİLER ---
            info_y = start_y + len(targets[:5]) * row_height + 20
            draw.text((40, info_y), f"UPNL: {upnl:+.2f}% (x{int(leverage):.0f} = {upnl_leveraged:+.2f}%)", font=f_info,
                fill=(255, 255, 255))
            draw.text((40, info_y + 30), f"Giriş: {self._fmt_price_dynamic(entry_price)}", font=f_small,
                fill=(220, 220, 220))
            stop_val, stop_label = self._effective_stop_from_signal(signal, event_meta=None)
            draw.text((40, info_y + 50), f"{stop_label}: {self._fmt_price_dynamic(stop_val)}", font=f_small,
                fill=(220, 220, 220))

            draw.text((40, info_y + 70), f"Kaldıraç: x{int(leverage):.0f}", font=f_small, fill=(220, 220, 220))

            # --- ROZET EKLEME ---
            badge_to_draw = None
            if event_type == 'FINAL':
                badge_path = self._select_final_image(signal)
                if badge_path: badge_to_draw = self._load_icon(badge_path)
            elif event_type == 'STOP':
                badge_path = self.image_paths.get(f"stop_overlay_{direction_val.lower()}")
                if badge_path: badge_to_draw = self._load_icon(badge_path)

            if badge_to_draw:
                badge_cfg = ConfigService.get('charts.event_card_overlays.final_badge', {})
                size = int(badge_cfg.get('size', {}).get('value', 200))
                pos_x_offset = int(badge_cfg.get('pos_x_offset', {}).get('value', -220))
                pos_y_offset = int(badge_cfg.get('pos_y_offset', {}).get('value', 20))
                badge_resized = badge_to_draw.resize((size, size))
                pos_x = canvas_width + pos_x_offset
                pos_y = chart_height + pos_y_offset
                final_image.paste(badge_resized, (pos_x, pos_y), badge_resized)

            # --- GÖRSELİ BİRLEŞTİRME ---
            final_image.paste(background, (0, chart_height))
            final_image.paste(panel, (0, chart_height), panel)

            output = BytesIO()
            final_image.save(output, format='JPEG', quality=int(ConfigService.get('charts.rendering.jpeg_quality', 95)))
            output.seek(0)
            return output

        except Exception as e:
            logging.error(f"Event composite oluşturma hatası: {e}", exc_info=True)
            return None

    def _select_final_image(self, signal: dict) -> Optional[str]:
        """Final hedef görselini PNL'e göre seçer."""
        direction = signal.get('signal_type', 'LONG')
        realized = signal.get('realized_net_pct', 0.0)

        chosen_tier = None
        for tier in self.pnl_tiers_config:
            max_pct = tier.get('max_pct')
            if max_pct is None or realized <= max_pct:
                chosen_tier = tier
                break
        if not chosen_tier and self.pnl_tiers_config:
            chosen_tier = self.pnl_tiers_config[-1]

        if chosen_tier:
            key = chosen_tier.get('long_image_key') if direction == 'LONG' else chosen_tier.get('short_image_key')
            return self.image_paths.get(key)
        return self.image_paths.get("logo")  # Fallback  

    async def send_event_image(
            self,
            bot: 'Bot',
            signal: dict,
            event_type: str,   
            target_index: Optional[int] = None,   
            caption: Optional[str] = None,
    ):
        """
        Oluşturulan olay kartını (hedef/stop/final) ilgili kanallara gönderir.
        """
        try:
            # Olay kartı görselini oluştur
            buf = await self.compose_event_composite(signal, event_type, target_index)   
            if not buf:
                logging.error(f"[{event_type}] için olay kartı görseli oluşturulamadı.")
                return

            # Ana sinyal mesajının gönderildiği kanalları ve mesaj ID'lerini al
            main_messages = signal.get('main_messages', [])
            if not main_messages:
                logging.warning(f"[{event_type}] Olay kartı gönderilecek ana mesaj bulunamadı.")
                return

            # Her bir ana mesaja yanıt olarak olay kartını gönder
            for mm in main_messages:
                channel_id = mm.get('channel_id')
                message_id = mm.get('message_id')
                if not channel_id or not message_id:
                    continue

                buf.seek(0)   
                await bot.send_photo(chat_id=channel_id, photo=buf, caption=caption, reply_to_message_id=message_id)
        except Exception as e:
            logging.error(f"[_send_event_image genel hata] {e}", exc_info=True)   

    @staticmethod
    def render_minimal(
            symbol: str,
            direction: str,
            entry_price: float,
            stop_loss: float,
            targets: list,
            timeframe: str,
            width: int = 1350,
            height: int = 520,
            df: Optional[pd.DataFrame] = None) -> Optional[BytesIO]:
        """  
        Stabil minimal üst grafik: df varsa mum, yoksa EP/SL/TP bandı.
        Başarısızlıkta bile JPEG buffer döner.
        """   
        w, h = int(width), int(height)
        bg = (24, 26, 32)
        try:
            im = Image.new("RGB", (w, h), bg)
            draw = ImageDraw.Draw(im)

            # Güvenli padding
            pad_l, pad_r, pad_t, pad_b = 80, 36, 46, 50
            x0, y0 = pad_l, pad_t
            x1, y1 = w - pad_r, h - pad_b
            cw, ch = x1 - x0, y1 - y0

            # Grid
            grid = (50, 54, 62)
            for grid_i in range(9):
                xx = x0 + int(grid_i * cw / 8.0)
                draw.line([(xx, y0), (xx, y1)], fill=grid, width=1)
            for grid_j in range(5):
                yy = y0 + int(grid_j * ch / 4.0)
                draw.line([(x0, yy), (x1, yy)], fill=grid, width=1)

            # Font
            try:
                f_title = ImageFont.truetype("arialbd.ttf", 20)
                f_axis = ImageFont.truetype("arial.ttf", 15)
            except IOError:
                f_title, f_axis = ImageFont.load_default(), ImageFont.load_default()

            # Başlık
            title = f"{symbol} | {timeframe} | {direction.upper()}"
            draw.text((12, 10), title, font=f_title, fill=(220, 220, 220))

            # Bounds
            def bounds() -> tuple[float, float]:
                ymin = ymax = None
                if df is not None and not df.empty:
                    try:
                        highs = df['high'].astype(float).values
                        lows = df['low'].astype(float).values
                        ymin, ymax = float(np.nanmin(lows)), float(np.nanmax(highs))
                    except (ValueError, KeyError):
                        pass
                if ymin is None or ymax is None:
                    series = [entry_price, stop_loss] + list(targets or [])
                    series = [float(v) for v in series if isinstance(v, (int, float))]
                    if not series:
                        return 0.0, 1.0
                    ymin, ymax = min(series), max(series)
                span = ymax - ymin
                if span <= 1e-9:
                    span = max(1e-6, abs(ymax) * 0.05 + 1e-6)
                return ymin - span * 0.05, ymax + span * 0.05

            y_min, y_max = bounds()

            def y2p(v: float) -> float:
                if abs(y_max - y_min) < 1e-9:
                    return float(y1)
                return float(y1 - ((v - y_min) / (y_max - y_min)) * ch)

            def x2p(i1: int, n: int) -> int:
                if n <= 1:
                    return x0
                return int(x0 + i1 * cw / (n - 1))

            # Mumlar
            if df is not None and not df.empty:
                bull = (80, 200, 120)
                bear = (220, 90, 90)
                wick = (200, 200, 200)
                num_candles = len(df)
                body_w = max(3, int(cw / max(60.0, float(num_candles))))
                for candle_i in range(num_candles):
                    try:
                        o = float(df['open'].iloc[candle_i])
                        h = float(df['high'].iloc[candle_i])
                        l = float(df['low'].iloc[candle_i])
                        c = float(df['close'].iloc[candle_i])
                        x = x2p(candle_i, num_candles)
                        draw.line([(x, y2p(l)), (x, y2p(h))], fill=wick, width=1)
                        yo, yc = y2p(o), y2p(c)
                        rect_top, rect_bot = min(yo, yc), max(yo, yc)
                        if rect_top == rect_bot:
                            rect_bot += 1
                        draw.rectangle((x - body_w // 2, rect_top, x + body_w // 2, rect_bot),
                            fill=(bull if c >= o else bear))
                    except (ValueError, IndexError):
                        continue

            # EP/SL/TP çizgileri
            col_entry: tuple[int, int, int] = (120, 190, 255)
            col_stop: tuple[int, int, int] = (255, 120, 120)
            col_tgt: tuple[int, int, int] = (255, 210, 90)
            th = 2

            def tag_line(y, color, text, align_right=False):
                draw.line([(x0, y), (x1, y)], fill=color, width=th)
                tx = (x1 - 74) if align_right else (x0 + 8)
                draw.text((tx, y - 12), text, font=f_axis, fill=color)

            if isinstance(entry_price, (int, float)):
                tag_line(y2p(float(entry_price)), col_entry, "ENTRY", align_right=True)
            if isinstance(stop_loss, (int, float)):
                tag_line(y2p(float(stop_loss)), col_stop, "STOP", align_right=True)
            for i, t in enumerate(targets or []):
                if isinstance(t, (int, float)):
                    yy = y2p(float(t))
                    draw.line([(x0, yy), (x1, yy)], fill=col_tgt, width=1)
                    draw.text((x0 + 8, yy - 12), f"T{i + 1}", font=f_axis, fill=col_tgt)

            # Y ekseni etiketleri
            y_mid = (y_min + y_max) / 2.0
            for label_price_value, yy in [(y_min, y1), (y_mid, (y0 + y1) // 2), (y_max, y0)]:
                label = f"{label_price_value:.6f}" if abs(label_price_value) < 1 else f"{label_price_value:.4f}"
                draw.text((10, yy - 10), label, font=f_axis, fill=(200, 200, 200))

            buf = BytesIO()
            im.save(buf, format="JPEG", quality=90)
            buf.seek(0)
            return buf
        except (ValueError, TypeError, AttributeError) as e:
            logging.error(f"[render_minimal] fatal {e}", exc_info=True)
            # Hata durumunda boş bir görsel döndür
            try:
                buf = BytesIO()
                Image.new("RGB", (w, h), bg).save(buf, format="JPEG", quality=85)
                buf.seek(0)
                return buf
            except (ValueError, TypeError) as e_inner:
                logging.error(f"Hata: {e_inner}")
                logging.error(
                    f"[RENDER_RETURNED_NONE] reason=... symbol={symbol} tf={timeframe} df_len={len(df) if df is not None else None}")
                return None

    @staticmethod
    def _find_index_by_time(candles: pd.DataFrame, event_time: Union[str, pd.Timestamp, datetime]) -> Optional[int]:
        """
        Belirtilen zamana en yakın mumun indeksini bulur.
        DÜZELTME: Hem standart datetime hem de Pandas Timestamp nesnelerini doğru işler.  
                  Gereksiz .to_series() çağrısı kaldırıldı ve .idxmin() kullanıldı.  
        """
        try:
            if "timestamp" not in candles.columns:
                if "datetime" in candles.columns:
                    candles = candles.rename(columns={"datetime":"timestamp"})
                elif isinstance(candles.index, pd.DatetimeIndex):
                    candles = candles.reset_index().rename(columns={"index":"timestamp"})
                else:
                    return None

            # Adım 1: Gelen event_time'ı her zaman UTC-aware bir Pandas Timestamp'e çevir.
            event_dt = pd.to_datetime(event_time, utc=True, errors='coerce')

            if pd.isna(event_dt):
                logging.error(f"[find_index_by_time] Geçersiz zaman formatı: {event_time}")
                return None

            # Adım 2: DataFrame'in timestamp sütununu da UTC-aware datetime nesnelerine dönüştür.
            candle_times = pd.to_datetime(candles["timestamp"], errors="coerce", utc=True)
            valid = candle_times.dropna()
            if valid.empty:
                return None

            event_dt = pd.to_datetime(event_time, utc=True, errors="coerce")
            if pd.isna(event_dt):
                return None

            # floor (pad): event_dt'den küçük/eşit en son bar
            pos = valid.searchsorted(event_dt, side="right") - 1
            pos = int(max(0, min(pos, len(valid) - 1)))

            closest_idx = valid.index[pos]
            return int(closest_idx)


        except Exception as e:
            logging.error(f"Zamana göre indeks bulma hatası: {e}", exc_info=True)
            return None

    @staticmethod   
    def _draw_entry_arrow(draw, signal_type: str, entry_index: int, entry_price: float, candles: pd.DataFrame,
            x_index_cb, y_price_cb):   
        """
        Giriş mumuna mavi ok çiz.  
        REFAKTÖR: Parametreler sadeleştirildi. x_index ve y_price callback'leri kullanılıyor.  
        """
        try:
            n = len(candles)
            # DÜZELTME: entry_index'in geçerli bir aralıkta olup olmadığını kontrol et.
            # 'n' mum grafiğindeki toplam mum sayısıdır.
            if entry_index < 0 or entry_index >= n:
                logging.warning(
                    f"[Giriş Oku] entry_index ({entry_index}) grafik sınırları ({n}) dışında. Ok çizilmeyecek.")
                return

            x_pos = x_index_cb(entry_index)

            # Mumun low/high değerini .at[] ile güvenli bir şekilde al.
            candle_low = float(candles.at[entry_index, 'low'])
            candle_high = float(candles.at[entry_index, 'high'])

            # Sinyal yönüne göre okun dikey pozisyonunu ayarla
            arrow_size = 12
            if signal_type.upper() == 'LONG':
                # LONG: Mumun altına yerleştir
                y_anchor: float = y_price_cb(candle_low) + arrow_size + 6
                points = [
                    (float(x_pos), float(y_anchor) - arrow_size),
                    (float(x_pos) - arrow_size // 2, float(y_anchor)),
                    (float(x_pos) + arrow_size // 2, float(y_anchor))
                ]
                text_y_offset: float = -arrow_size * 2.5
            else:  # SHORT
                # SHORT: Mumun üstüne yerleştir
                y_anchor: float = y_price_cb(candle_high) - arrow_size - 6
                points = [
                    (float(x_pos), float(y_anchor) + arrow_size),
                    (float(x_pos) - arrow_size // 2, float(y_anchor)),
                    (float(x_pos) + arrow_size // 2, float(y_anchor))
                ]
                text_y_offset = arrow_size * 1.5

            # Mavi ok çizimi
            draw.polygon(points, fill=(0, 120, 255, 255))

            # Ok etiketi
            draw.text((x_pos - 15, y_anchor + text_y_offset), "GİRİŞ", fill=(0, 120, 255, 255),
                font=ImageFont.load_default())  # Basit font kullanımı

        except Exception as e:
            logging.error(f"Giriş ok çizme hatası: {e}", exc_info=True)

    def _draw_target_marks(
            self,
            draw: ImageDraw.ImageDraw,
            targets: List[float],
            target_hits: List[bool],
            targets_hit_times: List[Optional[Union[str, pd.Timestamp]]],
            candles: pd.DataFrame,
            left: int,
            usable_w: int,
            n: int,
            _usable_h: int,
            _top: int,
            _pmin: float,
            _pmax: float,
            y_price_cb: Any,
            signal_type: str,
            x_index_cb: Any
    ) -> None:
        """Hedef vurulma işaretlerini çiz (mavi giriş oku mantığıyla)."""
        try:
            if not targets or not target_hits or not targets_hit_times:
                return

            is_long = (signal_type or "").upper() == "LONG"
            arrow_size = 12  # mavi ok boyutu ile aynı

            for i, (target, hit, hit_time) in enumerate(zip(targets, target_hits, targets_hit_times)):
                if i >= 5:  # İlk 5 hedef
                    break

                # Sadece vurulan ve zamanı belli olan hedefleri çiz
                if not hit or hit_time is None:
                    continue

                # Hedefin vurulduğu zamana göre mumu bul
                target_index = self._find_index_by_time(candles, hit_time)
                if target_index is None:
                    continue

                # Güvenlik: index sınırları
                if int(target_index) < 0 or int(target_index) >= int(n):
                    continue

                # X: tek kaynak callback
                try:
                    x_pos = int(x_index_cb(int(target_index)))
                except Exception:
                    # fallback: eski yöntem
                    x_pos = int(left + (int(target_index) / max(1, n - 1)) * usable_w)

                # Mumun low/high değerleri
                try:
                    candle_low = float(candles.at[int(target_index), "low"])
                except Exception:
                    candle_low = None

                try:
                    candle_high = float(candles.at[int(target_index), "high"])
                except Exception:
                    candle_high = None

                # Y anchor: mavi giriş oku mantığı
                # LONG: low altına, SHORT: high üstüne
                if is_long:
                    base_y = float(y_price_cb(candle_low if candle_low is not None else float(target)))
                    y_anchor = base_y + arrow_size + 6  # mumun altına doğru
                    arrow_points = [
                        (float(x_pos), float(y_anchor) - arrow_size),  # uç (yukarı)
                        (float(x_pos) - arrow_size // 2, float(y_anchor)),  # sol taban
                        (float(x_pos) + arrow_size // 2, float(y_anchor)),  # sağ taban
                    ]
                    label_xy = (float(x_pos) + 10.0, float(y_anchor) - arrow_size * 2.2)
                else:
                    base_y = float(y_price_cb(candle_high if candle_high is not None else float(target)))
                    y_anchor = base_y - arrow_size - 6  # mumun üstüne doğru
                    arrow_points = [
                        (float(x_pos), float(y_anchor) + arrow_size),  # uç (aşağı)
                        (float(x_pos) - arrow_size // 2, float(y_anchor)),  # sol taban
                        (float(x_pos) + arrow_size // 2, float(y_anchor)),  # sağ taban
                    ]
                    label_xy = (float(x_pos) + 10.0, float(y_anchor) + arrow_size * 1.6)

                # Sarı ok (vurulan hedef)
                draw.polygon(arrow_points, fill=(255, 255, 0, 220))

                # Hedef etiketi (sarı)
                draw.text(
                    label_xy,
                    f"H{i + 1}",
                    fill=(255, 255, 0, 230),
                    font=self._get_font(10, True)
                )

        except Exception as e:
            logging.error(f"Hedef işaretleme hatası: {e}", exc_info=True)

    @staticmethod
    def _format_price(p: float) -> str:
        if p >= 100:
            return f"{p:.2f}"
        if p >= 1:
            return f"{p:.4f}"
        return f"{p:.6f}"

    @staticmethod   
    def _detect_timeframe_from_index(index: pd.Index) -> str:   
        try:
            if index is None or len(index) < 5:
                return "?"
            # DatetimeIndex'e güvenli çeviri
            if isinstance(index, pd.DatetimeIndex):
                dt_index = index
            else:
                dt_index = pd.to_datetime(index, errors='coerce')
                if not isinstance(dt_index, pd.DatetimeIndex):
                    return "?"
            # NaT'leri temizle
            dt_index = dt_index[~dt_index.isna()]
            if len(dt_index) < 2:
                return "?"
            # HATA 2 DÜZELTİLDİ: np.mean() int bekliyor, float döndürüyor
            # Index.astype int64 döner, numpy array değil
            vals_ns_array = dt_index.view("int64")
            vals_ns_array = np.asarray(vals_ns_array, dtype=np.int64)
            if vals_ns_array.size < 2:
                return "?"
            diff_ns = np.diff(vals_ns_array)
            if diff_ns.size == 0:
                return "?"
            # int() ile sarmalama
            avg_minutes = float(np.mean(diff_ns)) / 1e9 / 60.0

            candidates = {
                1:"1m", 3:"3m", 5:"5m", 15:"15m", 30:"30m",
                60:"1h", 120:"2h", 240:"4h", 360:"6h",
                720:"12h", 1440:"1d"
            }
            best_key = min(candidates.keys(), key=lambda k:abs(k - avg_minutes))
            if abs(best_key - avg_minutes) < 1.5:
                return candidates[best_key]
            if avg_minutes >= 60:
                hours = int(round(avg_minutes / 60.0))
                return f"{hours}h"
            return f"{int(round(avg_minutes))}m"
        except (AttributeError, TypeError, ValueError) as exc_tf:
            logging.error(f"Timeframe tespit hatası: {exc_tf}")
            return "?"

    def _draw_price_axis(self, draw: ImageDraw.ImageDraw, width: int, _height: int, left: int, right: int, _top: int,
            _bottom: int,
            pmin: float, pmax: float, font_small, y_price_cb, ticks: int = 8):
        try:   
            pmin_f = float(pmin)
            pmax_f = float(pmax)
        except (ValueError, TypeError):
            return
        ticks_i = int(max(2, ticks))
        for ti in range(ticks_i):
            frac = float(ti) / float(max(1, ticks_i - 1))
            val_f = float(pmin_f + frac * (pmax_f - pmin_f))
            y_pos = float(y_price_cb(val_f))
            draw.line([(float(left), y_pos), (float(width - right), y_pos)], fill=(40, 50, 70, 120), width=1)
            txt = self._format_price(val_f)
            draw.text((float(width - right + 6), float(y_pos - 7)), txt, fill="white", font=font_small)

    @staticmethod   
    def _draw_time_axis(draw: ImageDraw.ImageDraw, candles: pd.DataFrame, left: int, right: int, _top: int, bottom: int,
            usable_w: int, font_small, x_index_cb):   
        _ = usable_w  # kullanılmıyor  
        try:
            n = int(len(candles))
            if n == 0:
                return
            width_total = int(draw.im.size[0])
            height_total = int(draw.im.size[1])
            axis_y = int(height_total - bottom)
            draw.line([(float(left), float(axis_y)), (float(width_total - right), float(axis_y))],
                fill=(80, 80, 95, 200), width=1)

            max_labels = 10
            step = int(max(1, n // max_labels))

            for idx_i in range(0, n, step):
                row = candles.iloc[int(idx_i)]
                ts = row.get('timestamp') if 'timestamp' in row else None
                if not isinstance(ts, pd.Timestamp):
                    try:
                        ts = pd.to_datetime(ts, errors='coerce')
                    except (ValueError, TypeError):
                        ts = None
                if ts is None or pd.isna(ts):
                    continue
                x_pos = int(x_index_cb(int(idx_i)))
                draw.line([(float(x_pos), float(axis_y)), (float(x_pos), float(axis_y + 6))],
                    fill=(130, 130, 140, 200), width=1)
                lab = ts.strftime("%d %H:%M")
                draw.text((float(x_pos) - 32.0, float(axis_y) + 8.0), lab, fill="gray", font=font_small)
        except (ValueError, TypeError, AttributeError) as exc_time:
            logging.error(f"[TIME_AXIS_ERR] {exc_time}")

    def _draw_double_structure(self, draw: ImageDraw.ImageDraw, pattern_dict: dict, candles: pd.DataFrame,
            x_index_cb: Any, y_price_cb: Any, font_small: Any, force_neck: bool = True) -> None:
        # Nokta listesini normalize edip tuple[int, float] yapalım
        pts_raw = pattern_dict.get('points') or []
        pts: List[tuple[int, float]] = []
        for pt in pts_raw:
            if isinstance(pt, (tuple, list)) and len(pt) >= 2:
                try:
                    pts.append((int(pt[0]), float(pt[1])))
                except (ValueError, TypeError):
                    continue
            elif isinstance(pt, dict):
                if 'idx' in pt and 'price' in pt:
                    try:
                        pts.append((int(pt['idx']), float(pt['price'])))
                    except (ValueError, TypeError):
                        continue
                elif 'i' in pt and 'p' in pt:
                    try:
                        pts.append((int(pt['i']), float(pt['p'])))
                    except (ValueError, TypeError):
                        continue

        pts = sorted(pts, key=lambda x:int(x[0]))[:4]
        n_candles = int(len(candles))
        if len(pts) >= 2:
            prev_xy: Optional[tuple[float, float]] = None
            for (idx_i, price_v) in pts:
                ii = int(idx_i)
                if ii < 0 or ii >= n_candles:
                    continue
                x_pos = float(x_index_cb(ii))
                y_pos = float(y_price_cb(float(price_v)))
                draw.ellipse((x_pos - 5.0, y_pos - 5.0, x_pos + 5.0, y_pos + 5.0),
                    outline=(255, 255, 255, 230), width=2)
                if prev_xy is not None:
                    draw.line([prev_xy, (x_pos, y_pos)], fill=(180, 180, 255, 200), width=2)
                prev_xy = (x_pos, y_pos)

        neckline = pattern_dict.get('neckline') or pattern_dict.get('breakout_level')
        try:
            if force_neck and isinstance(neckline, (int, float)) and math.isfinite(float(neckline)):
                yb = float(y_price_cb(float(neckline)))
                width_total = int(draw.im.size[0])
                for neck_start_x in range(80, width_total - 90, 20):   
                    draw.line([(float(neck_start_x), yb), (float(min(neck_start_x + 8, width_total - 90)), yb)],
                        fill=(255, 200, 0, 140), width=2)
                draw.text((85.0, yb - 14.0), f"Neckline {self._format_price(float(neckline))}",
                    fill="orange", font=font_small)
        except (ValueError, TypeError) as neck_err:
            logging.debug(f"Neckline çizimi hatası: {neck_err}")

    @staticmethod   
    def _normalize_patterns(patterns: Optional[List[Any]]) -> List[Dict[str, Any]]:
        out = []   
        if not patterns:
            return out
        for p in patterns:
            try:
                if isinstance(p, dict):
                    out.append(p)
                else:
                    d = {}
                    for attr in ['name', 'pattern', 'lines', 'raw_lines', 'breakout_level', 'points']:
                        if hasattr(p, attr):
                            d[attr if attr != 'pattern' else 'pattern'] = getattr(p, attr)
                    if d:
                        if 'name' not in d and 'pattern' in d:
                            d['name'] = d['pattern']
                        out.append(d)
            except (AttributeError, TypeError) as exc_norm:
                logging.error(f"Hata: {exc_norm}")
                continue
        return out

    def create_enhanced_technical_chart(   
            self,
            symbol: str,
            analysis_data: Dict[str, Any],
            signal_type: str,
            entry_price: float,   
            targets: Union[float, List[float]],
            stop_loss: Optional[Union[float, List[float]]] = None,
            input_dataframe: Optional[pd.DataFrame] = None,
            chart_width: int = 1200,
            chart_height: int = 720,
            margin_percent: float = 0.06
    ) -> Optional[io.BytesIO]:
        try:
            if not HAS_TALIB:
                logging.error("talib bulunamadı, teknik grafik oluşturulamıyor.")
                return None

            if input_dataframe is None or input_dataframe.empty:
                logging.warning("grafik: veri yok.")
                return None
            try:
                entry_price = float(entry_price)
                if not math.isfinite(entry_price) or entry_price <= 0:
                    return None
            except (TypeError, ValueError):
                return None

            df = input_dataframe.copy()
            if not isinstance(df.index, pd.DatetimeIndex):
                if 'timestamp' in df.columns:
                    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', errors='coerce')
                    df.set_index('timestamp', inplace=True)
                else:
                    df.index = pd.to_datetime(df.index, errors='coerce')
            df = df[~df.index.isna()]
            df = df.sort_index()

            if len(df) > 180:
                df = df.iloc[-180:]

            needed = ['open', 'high', 'low', 'close', 'volume']
            for c in needed:
                if c not in df.columns:
                    logging.error("Eksik kolon: %s", c)
                    return None
                df[c] = pd.to_numeric(df[c], errors='coerce')
            df = df[needed].dropna()
            if len(df) < 30:
                logging.warning("yetersiz bar (<30)")
                return None

            # Targets normalize
            if isinstance(targets, (int, float, np.number)):
                targets = [float(targets)]
            else:
                _t: list[float] = []
                for t_val in (targets or []):
                    try:
                        t_float = float(t_val)
                        if math.isfinite(t_float):
                            _t.append(t_float)
                    except (ValueError, TypeError):
                        pass
                targets = _t

            # Stop loss normalize
            if isinstance(stop_loss, list):
                stop_loss = stop_loss[0] if stop_loss else None
            if isinstance(stop_loss, (int, float, np.number)):
                try:
                    stop_loss = float(stop_loss)
                    if not math.isfinite(stop_loss):
                        stop_loss = None
                except (ValueError, TypeError):
                    stop_loss = None
            else:
                stop_loss = None

            signal_type = (signal_type or '').upper()

            # Göstergeler
            try:
                closes = df['close'].values.astype(float)
                highs = df['high'].values.astype(float)
                lows = df['low'].values.astype(float)
                bb_u, bb_m, bb_l = ta.BBANDS(closes, timeperiod=20, nbdevup=2, nbdevdn=2, matype=MA_Type.SMA)
                df['bb_upper'] = bb_u
                df['bb_middle'] = bb_m
                df['bb_lower'] = bb_l

                macd_line, macd_sig, macd_hist = ta.MACD(closes, 12, 26, 9)
                df['macd_line'] = macd_line
                df['macd_signal'] = macd_sig
                df['macd_hist'] = macd_hist

                df['atr'] = ta.ATR(highs, lows, closes, timeperiod=14)

                rsi = ta.RSI(closes, timeperiod=14)
                df['rsi_raw'] = rsi
                period = 14
                rsi_series = pd.Series(rsi, index=df.index)
                stoch_rsi_vals: list[float] = []
                for idx_sr in range(len(rsi_series)):
                    if idx_sr < period:
                        stoch_rsi_vals.append(np.nan)
                        continue
                    window = rsi_series.iloc[idx_sr - period + 1:idx_sr + 1]
                    rmin = float(window.min())
                    rmax = float(window.max())
                    if not math.isfinite(rmin) or not math.isfinite(rmax):
                        stoch_rsi_vals.append(np.nan)
                        continue
                    if abs(rmax - rmin) < 1e-9:
                        stoch_rsi_vals.append(stoch_rsi_vals[-1] if stoch_rsi_vals else 0.5)
                    else:
                        stoch_rsi_vals.append(float(rsi_series.iloc[idx_sr] - rmin) / (rmax - rmin))
                stoch_rsi = pd.Series(stoch_rsi_vals, index=df.index)
                k = stoch_rsi.rolling(3).mean()
                d = k.rolling(3).mean()
                df['stoch_rsi_k'] = k
                df['stoch_rsi_d'] = d
            except (ImportError, AttributeError, ValueError) as ind_err:
                logging.error("indikatör hata: %s", ind_err)

            sr_data = analysis_data.get('support_resistance', {}) if analysis_data else {}
            supports = sr_data.get('support', []) or []
            resistances = sr_data.get('resistance', []) or []

            w = max(1000, chart_width)
            h = max(600, chart_height)
            price_h = int(h * 0.58)
            combo_h = int(h * 0.20)
            macd_h = h - (price_h + combo_h)
            combo_y0 = price_h
            macd_y0 = price_h + combo_h

            img = Image.new('RGBA', (w, h), (14, 18, 30, 255))
            draw = ImageDraw.Draw(img, 'RGBA')
            try:
                font_small = self._get_font(12, bold=False)
                font_bold = self._get_font(15, bold=True)
            except (ValueError, TypeError) as exc_font2:
                logging.error(f"Hata: {exc_font2}")
                font_small = ImageFont.load_default()
                font_bold = ImageFont.load_default()

            def safe_f(sv) -> bool:
                try:
                    vv = float(sv)
                    return math.isfinite(vv)
                except (ValueError, TypeError):
                    return False

            # Series.min() scalar döner
            low_series_min = df['low'].min()
            high_series_max = df['high'].max()
            pmin = float(low_series_min)
            pmax = float(high_series_max)
            midp = (pmin + pmax) / 2.0
            if midp > 0:
                rel = (pmax - pmin) / midp
                if rel < 0.01:
                    expand = midp * 0.005
                    pmin = midp - expand
                    pmax = midp + expand

            if pmin == pmax:
                pmin *= 0.99
                pmax *= 1.01
            rng = pmax - pmin
            pmin -= rng * margin_percent
            pmax += rng * margin_percent

            all_overlay_prices: list[float] = []
            all_overlay_prices.extend([float(t) for t in (targets or []) if isinstance(t, (int, float))])
            if isinstance(stop_loss, (int, float)):
                all_overlay_prices.append(float(stop_loss))
            all_overlay_prices.append(float(entry_price))
            all_overlay_prices.extend([float(s) for s in supports[:3] if isinstance(s, (int, float))])
            all_overlay_prices.extend([float(r) for r in resistances[:3] if isinstance(r, (int, float))])
            _overlay_clean = [p for p in all_overlay_prices if isinstance(p, (int, float)) and math.isfinite(p)]
            if _overlay_clean:
                _min_overlay = min(_overlay_clean)
                _max_overlay = max(_overlay_clean)
                pmin = min(pmin, _min_overlay) - rng * 0.02
                pmax = max(pmax, _max_overlay) + rng * 0.04

            left_margin = 80
            right_margin = 70
            usable_w = w - left_margin - right_margin
            candles = df[['open', 'high', 'low', 'close']].reset_index()
            if 'index' in candles.columns and len(candles) > 0:
                first_idx_candle = candles['index'].iloc[0]
                if isinstance(first_idx_candle, pd.Timestamp):
                    candles = candles.rename(columns={'index':'timestamp'})
            n = len(candles)

            def y_price(yp_val: float) -> float:
                if not safe_f(yp_val) or abs(pmax - pmin) < 1e-9:
                    return 10 + (price_h - 30.0) / 2.0
                return 10 + (1.0 - ((float(yp_val) - pmin) / (pmax - pmin))) * (price_h - 30.0)

            # HATA 3 DÜZELTİLDİ: Series[Any] hatası - row.at[] kullan
            for idx_i in range(len(candles)):
                try:
                    row = candles.iloc[idx_i]
                    # .at[] ile scalar erişim
                    o_val = float(row.at['open']) if 'open' in row.index else float(row['open'].item())
                    h_val = float(row.at['high']) if 'high' in row.index else float(row['high'].item())
                    l_val = float(row.at['low']) if 'low' in row.index else float(row['low'].item())
                    c_val = float(row.at['close']) if 'close' in row.index else float(row['close'].item())
                except (ValueError, TypeError, KeyError) as exc_rows:
                    logging.error(f"Mum verisi okuma hatası: {exc_rows}")
                    continue
                x_pos = left_margin + int(float(idx_i) / max(1.0, float(n - 1)) * usable_w)
                body_col = (0, 210, 120, 255) if c_val >= o_val else (230, 70, 70, 255)
                draw.line([(x_pos, int(y_price(h_val))), (x_pos, int(y_price(l_val)))], fill=body_col, width=1)
                draw.rectangle([x_pos - 2, int(y_price(max(o_val, c_val))), x_pos + 2, int(y_price(min(o_val, c_val)))],
                    fill=body_col, outline=body_col)

            if all(k in df.columns for k in ['bb_upper', 'bb_middle', 'bb_lower']):
                bb_cfg = ConfigService.get('charts.bollinger_colors', {})
                bb_col_up = tuple(bb_cfg.get('upper', (255, 180, 60, 255)))
                bb_col_mid = tuple(bb_cfg.get('middle', (120, 190, 255, 255)))
                bb_col_low = tuple(bb_cfg.get('lower', (255, 180, 60, 255)))

                def _plot_line(series: pd.Series, color, line_width=2):
                    prev_xy = None
                    arr_ser = series.reset_index(drop=True)
                    for idx_l in range(len(arr_ser)):
                        v = arr_ser.iloc[idx_l]
                        if not safe_f(v):
                            continue
                        x_pos_l = left_margin + int(float(idx_l) / max(1.0, float(n - 1)) * usable_w)
                        y_pos_l = y_price(float(v))
                        if prev_xy:
                            draw.line([prev_xy, (x_pos_l, y_pos_l)], fill=color, width=line_width)
                        prev_xy = (x_pos_l, y_pos_l)

                _plot_line(df['bb_upper'], bb_col_up, 2)
                _plot_line(df['bb_middle'], bb_col_mid, 2)
                _plot_line(df['bb_lower'], bb_col_low, 2)

                def dash_line(dl_y: float, color):
                    dl_step = 14
                    for dash_start in range(left_margin, w - right_margin, dl_step):
                        draw.line([(dash_start, dl_y), (min(dash_start + 8, w - right_margin), dl_y)], fill=color,
                            width=2)

                max_s = ConfigService.get('charts.max_support_levels', 3)
                max_r = ConfigService.get('charts.max_resistance_levels', 3)

                for idx_s, s_val in enumerate(supports[:max_s]):
                    if safe_f(s_val):
                        yy = y_price(float(s_val))
                        dash_line(yy, (40, 200, 100, 200))
                        try:
                            draw.text((8, yy - 8), f"Destek{idx_s + 1} {float(s_val):.4f}", fill="lightgreen",
                                font=font_small)
                        except (ValueError, TypeError):
                            pass
                for idx_r, r_val in enumerate(resistances[:max_r]):
                    if safe_f(r_val):
                        yy = y_price(float(r_val))
                        dash_line(yy, (220, 150, 40, 200))
                        try:
                            draw.text((8, yy - 8), f"Direnç{idx_r + 1} {float(r_val):.4f}", fill="orange",
                                font=font_small)
                        except (ValueError, TypeError):
                            pass

                _label_used_y: set[float] = set()

                def label_line(label_price: float, text: str, color):
                    if not safe_f(label_price):
                        return
                    p_yy = y_price(float(label_price))
                    while any(abs(p_yy - oy) < 14 for oy in _label_used_y):
                        p_yy += 12
                    _label_used_y.add(p_yy)
                    dash_line(p_yy, color)
                    tx = 10
                    ty = p_yy - 7
                    bg_w = 120
                    draw.rectangle([tx - 4, ty - 2, tx + bg_w, ty + 13], fill=(20, 28, 45, 180))
                    draw.text((tx, ty), f"{text} {float(label_price):.4f}", fill=color[:3], font=font_small)

                price_ticks = 7   
                for ti in range(price_ticks):
                    tick_val = pmin + (float(ti) / (price_ticks - 1.0)) * (pmax - pmin)
                    yy = y_price(tick_val)
                    draw.text((w - right_margin + 8, yy - 7), f"{float(tick_val):.4f}", fill="white", font=font_small)

                label_line(entry_price, "Giriş", (255, 255, 0, 255))
                if isinstance(stop_loss, (int, float)):
                    label_line(float(stop_loss), "Stop Loss", (255, 70, 70, 255))
                for ti, tg in enumerate(list(targets)[:5]):
                    label_line(float(tg), f"Hedef{ti + 1}", (120, 255, 120, 255))

                tf_label = self._detect_timeframe_from_index(df.index)

                title_color = "lime" if signal_type == "LONG" else "red"
                draw.text((10, 8), f"{symbol} ({tf_label}) {signal_type}", fill=title_color, font=font_bold)

                # Zaman etiketleri
                time_index = df.index
                if not isinstance(time_index, pd.DatetimeIndex):
                    time_index = pd.to_datetime(time_index, errors='coerce')
                time_index = time_index[~time_index.isna()]
                max_labels = min(12, max(1, len(time_index) // 3 + 2))
                step = max(1, int(len(time_index) / max_labels))
                for idx_t in range(0, len(time_index), step):
                    ts = time_index[idx_t]
                    if not isinstance(ts, pd.Timestamp):
                        continue
                    x_pos = left_margin + int(float(idx_t) / max(1.0, float(n - 1)) * usable_w)
                    draw.line([(x_pos, price_h - 1), (x_pos, price_h + 5)], fill=(90, 90, 90, 160), width=1)
                    ts_txt = ts.strftime("%d %H:%M")
                    draw.text((x_pos - 28, price_h + 6), ts_txt, fill="gray", font=font_small)

                draw.line([(0, price_h), (w, price_h)], fill=(70, 70, 70, 255), width=1)
                draw.line([(0, macd_y0), (w, macd_y0)], fill=(70, 70, 70, 255), width=1)

                k_series = df['stoch_rsi_k'].ffill()
                d_series = df['stoch_rsi_d'].ffill()
                atr_series = df['atr'].ffill()
                if len(atr_series.dropna()) > 5:
                    atr_min_val = atr_series.min()
                    atr_max_val = atr_series.max()   
                    atr_min = float(atr_min_val)
                    atr_max = float(atr_max_val)
                    if abs(atr_max - atr_min) < 1e-9:
                        atr_max += 1e-9
                    atr_norm = (atr_series - atr_min) / (atr_max - atr_min)
                else:
                    atr_norm = atr_series * 0

                def y_combo(yc_val: float) -> float:
                    top2 = combo_y0 + 10
                    bottom2 = combo_y0 + combo_h - 25
                    return bottom2 - float(yc_val) * (bottom2 - top2)

                for lvl, col in [(0.2, (0, 160, 0, 120)), (0.5, (160, 160, 160, 90)), (0.8, (200, 80, 0, 120))]:
                    yy = y_combo(float(lvl))
                    draw.line([(left_margin, yy), (w - right_margin, yy)], fill=col, width=1)

                prev_xy_atr = None
                for idx_a in range(len(atr_norm)):
                    atr_val = atr_norm.iloc[idx_a]
                    if not safe_f(atr_val):
                        continue
                    x_pos = left_margin + int(float(idx_a) / max(1.0, float(n - 1)) * usable_w)
                    y_pos = y_combo(float(atr_val) * 0.95)
                    if prev_xy_atr:
                        draw.line([prev_xy_atr, (x_pos, y_pos)], fill=(255, 140, 0, 150), width=2)
                    prev_xy_atr = (x_pos, y_pos)
                draw.text((10, combo_y0 + 4), "StochRSI + ATR", fill="white", font=font_bold)

                prev_k = None
                prev_d = None
                for idx_sr in range(len(k_series)):
                    kv = k_series.iloc[idx_sr]
                    dv = d_series.iloc[idx_sr]
                    if safe_f(kv):
                        x_pos = left_margin + int(float(idx_sr) / max(1.0, float(n - 1)) * usable_w)
                        yk = y_combo(float(kv))
                        if prev_k:
                            draw.line([prev_k, (x_pos, yk)], fill=(90, 200, 255, 255), width=2)
                        prev_k = (x_pos, yk)
                    if safe_f(dv):
                        x_pos = left_margin + int(float(idx_sr) / max(1.0, float(n - 1)) * usable_w)
                        yd = y_combo(float(dv))
                        if prev_d:
                            draw.line([prev_d, (x_pos, yd)], fill=(255, 255, 120, 220), width=2)
                        prev_d = (x_pos, yd)

                if HAS_TALIB:
                    macd_line = df['macd_line'].ffill()
                    macd_sig = df['macd_signal'].ffill()
                    macd_hist = df['macd_hist'].ffill()
                    if len(macd_line.dropna()) > 5:
                        macd_line_min = macd_line.min()
                        macd_sig_min = macd_sig.min()
                        macd_hist_min = macd_hist.min()
                        macd_line_max = macd_line.max()
                        macd_sig_max = macd_sig.max()
                        macd_hist_max = macd_hist.max()
                        macd_min = float(min(float(macd_line_min), float(macd_sig_min), float(macd_hist_min)))
                        macd_max = float(max(float(macd_line_max), float(macd_sig_max), float(macd_hist_max)))
                        if macd_min == macd_max:
                            macd_min -= 0.5
                            macd_max += 0.5
                    else:
                        macd_min, macd_max = -1.0, 1.0

                    def y_macd(ym_val: float) -> float:
                        top3 = macd_y0 + 10
                        bottom3 = macd_y0 + macd_h - 25
                        if abs(macd_max - macd_min) < 1e-9:
                            return (top3 + bottom3) / 2.0
                        return bottom3 - (float(ym_val) - macd_min) / (macd_max - macd_min) * (bottom3 - top3)

                    bar_w = max(1, int(usable_w / max(60.0, float(n))))
                    for idx_m in range(len(macd_hist)):
                        hist_val = macd_hist.iloc[idx_m]
                        if not safe_f(hist_val):
                            continue
                        x_pos = left_margin + int(float(idx_m) / max(1.0, float(n - 1)) * usable_w)
                        y0_macd = y_macd(0.0)
                        yv = y_macd(float(hist_val))
                        col = (0, 200, 120, 230) if float(hist_val) >= 0 else (220, 80, 80, 230)
                        draw.rectangle(
                            [x_pos - bar_w // 2, int(min(y0_macd, yv)), x_pos + bar_w // 2, int(max(y0_macd, yv))],
                            fill=col)

                    prev_l = None
                    prev_s = None
                    for idx_ml in range(len(macd_line)):
                        mlv = macd_line.iloc[idx_ml]
                        msv = macd_sig.iloc[idx_ml]
                        if safe_f(mlv):
                            x_pos = left_margin + int(float(idx_ml) / max(1.0, float(n - 1)) * usable_w)
                            yl = y_macd(float(mlv))
                            if prev_l:
                                draw.line([prev_l, (x_pos, yl)], fill=(0, 255, 255, 255), width=2)
                            prev_l = (x_pos, yl)
                        if safe_f(msv):
                            x_pos = left_margin + int(float(idx_ml) / max(1.0, float(n - 1)) * usable_w)
                            ys = y_macd(float(msv))
                            if prev_s:
                                draw.line([prev_s, (x_pos, ys)], fill=(255, 255, 0, 220), width=2)
                            prev_s = (x_pos, ys)
                    draw.text((10, macd_y0 + 4), "MACD", fill="white", font=font_bold)

                    try:
                        if analysis_data and ConfigService.get('strategy.chart', {}).get('draw_patterns', True):
                            patterns_det = analysis_data.get('pattern_detected', [])
                            if patterns_det:
                                base_df = df.reset_index()
                                if 'index' in base_df.columns and len(base_df) > 0:
                                    first_base_idx = base_df['index'].iloc[0]
                                    if isinstance(first_base_idx, pd.Timestamp):
                                        base_df = base_df.rename(columns={'index':'timestamp'})

                                def _last_slice(lookback: int) -> pd.DataFrame:
                                    lookback = int(lookback)
                                    return base_df.iloc[-min(lookback, len(base_df)):]

                                def _line_from_points(ptlist, color=(200, 200, 50, 180), line_width=2):
                                    prev_xy_local = None
                                    for t_idx, p_val in ptlist:
                                        if isinstance(t_idx, (int, np.integer)):
                                            x_idx = int(t_idx)
                                        else:
                                            if 'timestamp' in base_df.columns and isinstance(t_idx,
                                                    (pd.Timestamp, np.datetime64)):
                                                try:
                                                    matching_indices = base_df.index[
                                                        base_df['timestamp'] == pd.Timestamp(t_idx)]
                                                    if len(matching_indices) > 0:
                                                        first_match = matching_indices[0]
                                                        x_idx = first_match
                                                    else:
                                                        continue
                                                except (ValueError, TypeError, IndexError):
                                                    continue
                                            else:
                                                continue
                                        if x_idx < 0 or x_idx >= len(base_df):
                                            continue
                                        x_pos_l = left_margin + int(
                                            float(x_idx) / max(1.0, float(n - 1)) * usable_w)
                                        y_pos_l = y_price(float(p_val))
                                        if prev_xy_local:
                                            draw.line([prev_xy_local, (x_pos_l, y_pos_l)], fill=color,
                                                width=line_width)
                                        prev_xy_local = (int(x_pos_l), int(y_pos_l))

                                def _regression_line(slice_df: pd.DataFrame, coll: str):
                                    yss = slice_df[coll].values.astype(float)  # 'yss' is used below
                                    xs = np.arange(len(yss))
                                    if len(xs) < 3:
                                        return []
                                    ml, b = np.polyfit(xs, yss, 1)
                                    pts_loc = []
                                    for i_loc in (0, len(xs) - 1):
                                        pts_loc.append((i_loc, float(ml * i_loc + b)))
                                    return pts_loc

                                for p_obj in patterns_det[:2]:
                                    pname = p_obj.get('pattern')
                                    if not pname:
                                        continue
                                    if 'Triangle' in pname:
                                        sl_df = _last_slice(ConfigService.get('charts.pattern_lookback.triangle', 50))
                                        up_pts = _regression_line(sl_df, 'high')
                                        dn_pts = _regression_line(sl_df, 'low')
                                        if up_pts:
                                            _line_from_points(up_pts, (255, 180, 0, 220), line_width=2)
                                        if dn_pts:
                                            _line_from_points(dn_pts, (0, 200, 140, 220), line_width=2)
                                    elif pname == 'Rectangle':
                                        sl_df = _last_slice(ConfigService.get('charts.pattern_lookback.rectangle', 30))
                                        rect_high_max = sl_df['high'].max()
                                        rect_low_min = sl_df['low'].min()
                                        top_v = float(rect_high_max)
                                        bot_v = float(rect_low_min)
                                        if top_v > bot_v:  # 'x0_rect' is used below
                                            x0_rect = left_margin + int(
                                                0 / max(1.0, float(n - 1)) * usable_w)  # 'x1_rect' is used below
                                            x1_rect = left_margin + int(
                                                (len(sl_df) - 1) / max(1.0, float(n - 1)) * usable_w)
                                            y_top_rect = y_price(top_v)
                                            y_bot_rect = y_price(bot_v)
                                            draw.rectangle((x0_rect, int(y_top_rect), x1_rect, int(y_bot_rect)),
                                                outline=(180, 180, 255, 180), width=2)   
                                    elif 'Wedge' in pname:
                                        sl_df = _last_slice(ConfigService.get('charts.pattern_lookback.wedge', 50))
                                        up_pts = _regression_line(sl_df, 'high')
                                        dn_pts = _regression_line(sl_df, 'low')
                                        if up_pts:
                                            _line_from_points(up_pts, (255, 120, 50, 220), line_width=2)
                                        if dn_pts:
                                            _line_from_points(dn_pts, (255, 120, 50, 220), line_width=2)
                                    elif 'Double Top' in pname or 'Double Bottom' in pname:
                                        arr_db = df.tail(60).reset_index()   
                                        if 'index' in arr_db.columns and len(arr_db) > 0:
                                            first_arr_idx = arr_db['index'].iloc[0]
                                            if isinstance(first_arr_idx, pd.Timestamp):
                                                arr_db = arr_db.rename(columns={'index':'timestamp'})
                                        prices = arr_db['high'].values if 'Top' in pname else arr_db['low'].values
                                        ex_idx: list[int] = []
                                        for ii in range(2, len(prices) - 2):
                                            if 'Top' in pname:
                                                if prices[ii] > prices[ii - 1] and prices[ii] > prices[ii + 1]:
                                                    ex_idx.append(ii)
                                            else:
                                                if prices[ii] < prices[ii - 1] and prices[ii] < prices[ii + 1]:
                                                    ex_idx.append(ii)
                                        for ei in ex_idx[:2]:
                                            x_pos2 = left_margin + int(
                                                float(ei) / max(1.0, float(n - 1)) * usable_w)
                                            extreme_price = float(
                                                arr_db['high'].iloc[ei]) if 'Top' in pname else float(
                                                arr_db['low'].iloc[ei])
                                            y_pos2 = y_price(extreme_price)
                                            draw.ellipse((x_pos2 - 6, int(y_pos2) - 6, x_pos2 + 6, int(y_pos2) + 6),
                                                outline=(255, 255, 255, 230), width=2)
                                    elif 'Breakout' in pname:
                                        sl_df = _last_slice(40)
                                        if len(sl_df) > 10:
                                            prev_high_series = sl_df['high'].iloc[:-5]
                                            prev_low_series = sl_df['low'].iloc[:-5]
                                            prev_high = float(prev_high_series.max())
                                            prev_low = float(prev_low_series.min())
                                        else:
                                            prev_high = None
                                            prev_low = None
                                        last_close = float(sl_df['close'].iloc[-1])
                                        if prev_high and last_close > prev_high:
                                            yy = y_price(prev_high)
                                            draw.line([(left_margin, yy), (w - right_margin, yy)],
                                                fill=(255, 255, 0, 200), width=2)
                                        if prev_low and last_close < prev_low:
                                            yy = y_price(prev_low)
                                            draw.line([(left_margin, yy), (w - right_margin, yy)],
                                                fill=(255, 0, 0, 200), width=2)
                                if patterns_det:
                                    names = ", ".join([str(p.get('pattern', '')) for p in patterns_det[:2]])
                                    draw.rectangle((w // 2 - 140, 30, w // 2 + 140, 52), fill=(30, 50, 90, 180))
                                    draw.text((w // 2 - 130, 34), f"Formasyon: {names}", fill="white",
                                        font=font_small)
                    except (ValueError, TypeError, AttributeError) as pattern_err:
                        logging.debug(f"Pattern çizim hata: {pattern_err}", exc_info=True)

                    try:
                        meta = self._last_signal_meta.get(symbol, {})
                        conf = meta.get('confidence_index')
                        dirc = meta.get('directional', signal_type)
                        if conf is not None:   
                            draw.rectangle([0, h - 26, w, h], fill=(25, 45, 80, 200))
                            draw.text((10, h - 22), f"CONF {conf}/100 | {dirc}", fill="white", font=font_small)
                    except (ValueError, TypeError) as error:
                        logging.error(f"Hata: {error}")

                    buf = io.BytesIO()
                    img.save(buf, format='PNG')
                    buf.seek(0)
                    return buf
        except Exception as exc_render:
            logging.error(f"[ChartRenderer] create_enhanced_technical_chart hata: {exc_render}", exc_info=True)
        return None
