# strategies/alarm_system/analytics.py

import json
import os
from io import BytesIO
from typing import Any, Optional, Dict, Tuple
from datetime import datetime, timedelta, timezone
import pandas as pd
from dataclasses import dataclass
from config_service import ConfigService
import logging


logger = logging.getLogger(__name__)

# Base class importları
try:
    from analytics.performance_report import PerformanceReport as BasePerformanceReport
except Exception:
    class BasePerformanceReport:
        @staticmethod
        def trades_df() -> pd.DataFrame:
            return pd.DataFrame()

# ✅ AlarmRaporManager tek kaynak: StrategyAdaptiveTuner.py
try:
    from StrategyAdaptiveTuner import AlarmRaporManager  # gerçek sınıf
except Exception as e:
    logger.warning(f"[analytics] AlarmRaporManager import edilemedi: {e}")

    # Fallback (boş kalmasın; en azından hata verince anlaşılır olsun)
    class AlarmRaporManager:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(f"AlarmRaporManager yüklenemedi: {e}")

Bucket = Tuple[str, str]  # (scan_type, version)

def get_quota_profile_for_tf(tf: str) -> dict:
    """
    olimpos_tarama_ayarlari.json -> scans.tf_profiles[tf] içinden
    quota/limitleri normalize eder.
    """
    p = ConfigService.tf_profile(tf)

    max_total = int(p.get("max_active_alarms", 0) or 0)

    ai = p.get("ai_scan", {}) or {}
    st = p.get("strategy_scan", {}) or {}

    ai_enabled = bool(ai.get("enabled", False))
    st_enabled = bool(st.get("enabled", False))

    limits: Dict[Bucket, int] = {}

    # ai_scan.limits.v1/v2
    ai_limits = (ai.get("limits", {}) or {})
    if ai_enabled:
        for v in ("v1", "v2"):
            limits[("ai_scan", v)] = int(ai_limits.get(v, 0) or 0)
    else:
        limits[("ai_scan", "v1")] = 0
        limits[("ai_scan", "v2")] = 0

    # strategy_scan.v1.limit / v2.limit
    if st_enabled:
        for v in ("v1", "v2"):
            vv = (st.get(v, {}) or {})
            limits[("strategy_scan", v)] = int(vv.get("limit", 0) or 0)
    else:
        limits[("strategy_scan", "v1")] = 0
        limits[("strategy_scan", "v2")] = 0

    bucket_sum = sum(limits.values())
    if max_total and bucket_sum > max_total:
        logging.warning(
            f"[QUOTA_CONFIG] tf={tf} bucket_sum={bucket_sum} > max_total={max_total}. "
            f"Config uyumsuz; bucket limitleriyle devam."
        )

    return {
        "tf": tf,
        "max_total": max_total,
        "limits": limits,
        "enabled": {"ai_scan": ai_enabled, "strategy_scan": st_enabled},
        "raw": p,
    }


def count_active_alarms_by_bucket(active_alarms: list, tf: str) -> Dict[Bucket, int]:
    counts: Dict[Bucket, int] = {
        ("ai_scan", "v1"): 0,
        ("ai_scan", "v2"): 0,
        ("strategy_scan", "v1"): 0,
        ("strategy_scan", "v2"): 0,
    }
    for a in active_alarms:
        if a.get("timeframe") != tf:
            continue
        if a.get("status") not in (None, "active"):
            continue
        key = (a.get("scan_type"), a.get("version"))
        if key in counts:
            counts[key] += 1
    return counts


def compute_missing(profile: dict, counts: Dict[Bucket, int]) -> Dict[Bucket, int]:
    missing: Dict[Bucket, int] = {}
    for bucket, lim in profile["limits"].items():
        need = int(lim) - int(counts.get(bucket, 0))
        if need > 0:
            missing[bucket] = need
    return missing

class PerformanceReport(BasePerformanceReport):
    """
    Performans raporlama sınıfı.
    BasePerformanceReport'tan miras alır ve detaylı analiz metodları ekler.
    """

    def get_stats_by_feature(self, feature_name: str, bins: int = 5) -> Optional[pd.DataFrame]:
        """
        Belirli bir meta özelliğine göre (örn: 'ai_confidence') performans istatistiklerini gruplar.
        """
        # 1. Adım: trades_df verisini güvenli bir şekilde al
        df_trades: Optional[pd.DataFrame] = None

        # Özelliği/Metodu al (self.trades_df)
        attr = getattr(self, 'trades_df', None)

        # Metot ise çağır, DataFrame ise direkt kullan
        if callable(attr):
            try:
                result = attr()
                if isinstance(result, pd.DataFrame):
                    df_trades = result
            except Exception as e:
                logger.error(f"[PerfReport] trades_df çağrılırken hata: {e}")
                return None
        elif isinstance(attr, pd.DataFrame):
            df_trades = attr
        else:
            logger.error("[PerfReport] 'trades_df' metodu veya özelliği bulunamadı.")
            return None

        # Veri kontrolü
        if df_trades is None or df_trades.empty or 'meta_at_open' not in df_trades.columns:
            logger.warning("[PerfReport] Analiz için 'meta_at_open' içeren işlem verisi bulunamadı.")
            return None

        df = df_trades.copy()

        # PnL kolonunu normalize et (öncelik: effective_lev -> effective -> net -> pnl)
        if 'realized_effective_lev' in df.columns:
            df['unified_pnl_pct'] = pd.to_numeric(df['realized_effective_lev'], errors='coerce')
        elif 'realized_effective_pct' in df.columns:
            df['unified_pnl_pct'] = pd.to_numeric(df['realized_effective_pct'], errors='coerce')
        elif 'realized_net_pct' in df.columns:
            df['unified_pnl_pct'] = pd.to_numeric(df['realized_net_pct'], errors='coerce')
        elif 'pnl_pct' in df.columns:
            df['unified_pnl_pct'] = pd.to_numeric(df['pnl_pct'], errors='coerce')
        else:
            logger.error("[PerfReport] PnL metriği bulunamadı.")
            return None

        # Özelliği çıkar
        def _extract(meta):
            if isinstance(meta, dict):
                return meta.get(feature_name)
            return None

        df[feature_name] = df['meta_at_open'].apply(_extract)
        df[feature_name] = pd.to_numeric(df[feature_name], errors='coerce')

        # Sonsuz değerleri temizle
        df.replace([float('inf'), float('-inf')], pd.NA, inplace=True)
        df.dropna(subset=[feature_name, 'unified_pnl_pct'], inplace=True)

        if df.empty:
            logger.warning(f"[PerfReport] '{feature_name}' özelliği için geçerli veri bulunamadı.")
            return None

        # Binning (Gruplama)
        try:
            q = max(1, min(bins, df[feature_name].nunique()))
            if q <= 1:
                df[f'{feature_name}_bin'] = df[feature_name].astype(str)
            else:
                try:
                    df[f'{feature_name}_bin'] = pd.qcut(df[feature_name], q=q, duplicates='drop')
                except ValueError:
                    # qcut başarısız olursa cut dene
                    df[f'{feature_name}_bin'] = pd.cut(df[feature_name], bins=min(bins, 5), duplicates='drop')
        except Exception as e:
            logger.error(f"[PerfReport] Binning hatası: {e}")
            return None

        # İstatistikleri hesapla
        try:
            stats = df.groupby(f'{feature_name}_bin', observed=False).agg(
                trade_count=('symbol', 'count'),
                avg_pnl=('unified_pnl_pct', 'mean'),
                total_pnl=('unified_pnl_pct', 'sum'),
                win_rate=('unified_pnl_pct', lambda s: (pd.to_numeric(s, errors='coerce') > 0).sum() /
                                                       s.count() * 100 if s.count() > 0 else 0)

            ).reset_index()

            stats.rename(columns={f'{feature_name}_bin':'feature_range'}, inplace=True)

            # Yuvarlama
            for c in ('avg_pnl', 'total_pnl', 'win_rate'):
                if c in stats.columns:
                    stats[c] = pd.to_numeric(stats[c], errors='coerce').fillna(0.0).round(2)

            return stats
        except Exception as e:
            logger.error(f"[PerfReport] Groupby hatası: {e}")
            return None

    def plot_stats_by_feature(self, feature_name: str, bins: int = 10) -> Optional[BytesIO]:
        """
        İstatistikleri grafiğe döker.
        """
        stats_df = self.get_stats_by_feature(feature_name, bins)
        if stats_df is None or stats_df.empty:
            return None

        import matplotlib.pyplot as plt
        import seaborn as sns

        try:
            stats_df['feature_range'] = stats_df['feature_range'].astype(str)

            fig, ax1 = plt.subplots(figsize=(12, 7))

            sns.barplot(
                data=stats_df,
                x='feature_range',
                y='avg_pnl',
                hue='feature_range',
                palette='viridis',
                dodge=False,
                ax=ax1,
                legend=False
            )

            ax1.set_title(f"Ortalama PnL vs. {feature_name.replace('_', ' ').title()}", fontsize=16)
            ax1.set_ylabel("Ortalama Net PnL (%)", color='b')
            plt.setp(ax1.get_xticklabels(), rotation=45, ha='right')
            plt.tight_layout()

            buf = BytesIO()
            plt.savefig(buf, format='PNG', dpi=140, bbox_inches='tight')
            buf.seek(0)
            plt.close(fig)
            return buf
        except Exception as e:
            logger.error(f"[PerfReport] Grafik çizim hatası: {e}")
            return None


class SymbolPerformanceTracker:
    """
    Sembollerin sağlık durumunu (Health Check) ve Ödül/Ceza (Reward/Penalty) sistemini yönetir.
    """
    _instance = None
    _log_file_path: str = "analytics/signal_performance_logs.jsonl"
    _cache: Dict[str, Any] = {}
    _last_load_time: Optional[datetime] = None

    # ✅ NEW: blacklist persistence
    _blacklist_path: str = "analytics/symbol_blacklist.json"
    _blacklist_cache: Dict[str, Dict[str, Any]] = {}
    _blacklist_last_load: Optional[datetime] = None

    # ✅ NEW: policy knobs (istersen config_service ile de besleriz)
    _blacklist_ttl_sec: int = 30  # dosyayı 30 sn cachele
    _min_trades_for_wr_penalty: int = 5

    # ✅ NEW: streak policy tables
    # stop_streak -> (action, penalty_factor, cooldown_hours, reason_template)
    _STOP_POLICY = {
        1:("penalize", 0.90, 0, "⛔ 1 ardışık stop"),
        2:("penalize", 0.75, 0, "⛔ 2 ardışık stop"),
        3:("blacklist_skip", 0.60, 6, "⛔ 3 ardışık stop → blacklist"),
        4:("blacklist_skip", 0.55, 12, "⛔ 4 ardışık stop → blacklist"),
        5:("blacklist_skip", 0.50, 24, "⛔ 5 ardışık stop → blacklist"),
    }
    # 6+ için fallback
    _STOP_POLICY_FALLBACK = ("blacklist_skip", 0.45, 48, "⛔ {n} ardışık stop → blacklist")

    # win_streak -> (action, penalty_factor, priority_bonus, reason_template)
    _WIN_POLICY = {
        2:("boost", 1.03, 1.0, "🔥 2 seri kazanç"),
        3:("boost", 1.06, 2.0, "🔥 3 seri kazanç"),
        5:("boost", 1.10, 4.0, "🔥 5 seri kazanç"),
    }
    _WIN_POLICY_FALLBACK = ("boost", 1.10, 4.0, "🔥 {n} seri kazanç")

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SymbolPerformanceTracker, cls).__new__(cls)
            cls._instance._load_data()
        return cls._instance

    # ---------------------------------------------------------------------
    # ✅ BLACKLIST IO
    # ---------------------------------------------------------------------
    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _parse_iso_dt(s: str) -> Optional[datetime]:
        try:
            if not s:
                return None
            # "2025-12-26T02:30:00Z" veya "+00:00"
            ss = s.strip()
            if ss.endswith("Z"):
                ss = ss[:-1] + "+00:00"
            dt = datetime.fromisoformat(ss)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    @staticmethod
    def _sym_clean(symbol: Any) -> str:
        core = SymbolPerformanceTracker._norm_symbol_core(symbol)
        return core or str(symbol or "").strip().upper()

    def _load_blacklist(self, force: bool = False) -> Dict[str, Dict[str, Any]]:
        now = self._utc_now()
        if (not force) and self._blacklist_last_load and (
                now - self._blacklist_last_load).total_seconds() < self._blacklist_ttl_sec:
            return self._blacklist_cache

        path = self._blacklist_path
        if not os.path.exists(path):
            self._blacklist_cache = {}
            self._blacklist_last_load = now
            return self._blacklist_cache

        try:
            with open(path, "r", encoding="utf-8") as f:
                obj = json.load(f)
            if not isinstance(obj, dict):
                obj = {}
        except Exception as e:
            logger.warning(f"[BLACKLIST] read failed: {e}")
            obj = {}

        # ✅ expire cleanup
        cleaned: Dict[str, Dict[str, Any]] = {}
        for k, v in obj.items():
            if not isinstance(v, dict):
                continue
            sym = self._sym_clean(k)
            until_dt = self._parse_iso_dt(str(v.get("until", "") or ""))
            if until_dt and until_dt > now:
                cleaned[sym] = v
            # süresi dolanlar otomatik düşer

        self._blacklist_cache = cleaned
        self._blacklist_last_load = now
        return self._blacklist_cache

    def _save_blacklist(self) -> None:
        os.makedirs(os.path.dirname(self._blacklist_path), exist_ok=True)
        try:
            with open(self._blacklist_path, "w", encoding="utf-8") as f:
                json.dump(self._blacklist_cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[BLACKLIST] save failed: {e}")

    def _is_blacklisted(self, symbol: str) -> Dict[str, Any]:
        bl = self._load_blacklist()
        sym = self._sym_clean(symbol)
        item = bl.get(sym)
        if not isinstance(item, dict):
            return {"blacklisted":False}

        until_dt = self._parse_iso_dt(str(item.get("until", "") or ""))
        if not until_dt:
            return {"blacklisted":False}

        now = self._utc_now()
        if until_dt <= now:
            # expire
            try:
                del bl[sym]
                self._blacklist_cache = bl
                self._save_blacklist()
            except Exception:
                pass
            return {"blacklisted":False}

        return {
            "blacklisted":True,
            "cooldown_until":until_dt.isoformat().replace("+00:00", "Z"),
            "reason":str(item.get("reason", "BLACKLIST")) or "BLACKLIST",
            "streak":int(item.get("streak", 0) or 0),
        }

    def _blacklist_add(self, symbol: str, hours: int, reason: str, streak: int) -> Dict[str, Any]:
        sym = self._sym_clean(symbol)
        until_dt = self._utc_now() + timedelta(hours=max(1, int(hours)))
        bl = self._load_blacklist()
        bl[sym] = {
            "until":until_dt.isoformat().replace("+00:00", "Z"),
            "reason":reason,
            "streak":int(streak),
            "ts":self._utc_now().isoformat().replace("+00:00", "Z"),
        }
        self._blacklist_cache = bl
        self._save_blacklist()

        logger.warning(f"[BLACKLIST_ADD] sym={sym} until={bl[sym]['until']} streak={streak} reason={reason}")
        return {"blacklisted":True, "cooldown_until":bl[sym]["until"]}

    @staticmethod
    def _norm_symbol_core(symbol: Any) -> Optional[str]:
        """
        Her şeyi core anahtara indir:
          - "SOL/USDT:USDT" -> "SOLUSDT"
          - "SOL/USDT"      -> "SOLUSDT"
          - "SOLUSDT"       -> "SOLUSDT"
        """
        try:
            s = str(symbol or "").strip().upper()
            if not s:
                return None

            # futures suffix
            if ":" in s:
                s = s.split(":", 1)[0].strip()

            # unify separators
            s = s.replace("-", "/").replace("_", "/")

            # slash format -> core
            if "/" in s:
                parts = [p for p in s.split("/") if p]
                if len(parts) >= 2:
                    base, quote = parts[0], parts[1]
                    # tracker USDT odaklı
                    if quote != "USDT":
                        quote = "USDT"
                    s = f"{base}{quote}"

            if s.endswith("USDT") and len(s) > 4:
                return s

            return None
        except Exception:
            return None

    # ---------------------------------------------------------------------
    # ✅ EXISTING: get_stats() stays (senin mevcut fonksiyonun)
    # ---------------------------------------------------------------------
    def _load_data(self):
        """
        Kapanan işlemlerden sembol bazında istatistikleri (Win/Loss, Streak vb.) hesaplar.

        Öncelik:
          1) closed_signals_state.json (senin botun için ana gerçek kaynak)
          2) analytics/signal_performance_logs.jsonl (fallback)
        """
        # 15 dk cache
        if self._last_load_time and (datetime.now(timezone.utc) - self._last_load_time) < timedelta(minutes=15):
            return

        # Öncelikli kaynak: closed_signals_state.json
        primary_path = ConfigService.get("reporting.save_paths.signals_closed") or os.path.join(
            "analytics", "closed_signals_state.json"
        )
        fallback_path = self._log_file_path  # mevcut davranışı bozmamak için

        path_to_use: Optional[str] = None
        mode: Optional[str] = None  # "json" | "jsonl"

        if os.path.exists(primary_path):
            path_to_use = primary_path
            mode = "json"
        elif os.path.exists(fallback_path):
            path_to_use = fallback_path
            mode = "jsonl"
        else:
            return

        logger.info(f"[SymbolTracker] Sembol performans verileri güncelleniyor... kaynak={path_to_use} mode={mode}")

        symbol_stats: Dict[str, Dict[str, Any]] = {}

        def _norm_sym(s: Any) -> Optional[str]:
            """
            Her şeyi TEK anahtara indir:
              - SOL/USDT:USDT -> SOLUSDT
              - SOL/USDT      -> SOLUSDT
              - SOLUSDT       -> SOLUSDT
            """
            try:
                if not s:
                    return None
                ss = str(s).strip().upper()

                # settle suffix at
                if ":" in ss:
                    ss = ss.split(":", 1)[0].strip()

                # slash/underscore/dash temizle
                ss = ss.replace("-", "/").replace("_", "/")
                if "/" in ss:
                    parts = [p for p in ss.split("/") if p]
                    if len(parts) >= 2:
                        base, quote = parts[0], parts[1]
                        if quote != "USDT":
                            # bu tracker USDT odaklı; farklı quote gelirse yine de dene
                            quote = "USDT"
                        ss = f"{base}{quote}"

                # zaten core ise dokunma; değilse USDT ile bitmiyorsa None
                if ss.endswith("USDT") and len(ss) > 4:
                    return ss
                return None
            except Exception:
                return None

        def _ensure_stats(sym1: str) -> Dict[str, Any]:
            if sym1 not in symbol_stats:
                symbol_stats[sym1] = {
                    "trades":0,
                    "wins":0,
                    "losses":0,
                    "total_pnl":0.0,
                    "consecutive_losses":0,
                    "consecutive_wins":0,
                    "last_outcome_win":True,

                    # ✅ NEW: ağırlıklı outcome
                    "win_equiv":0.0,  # WIN_TARGET=1.0, WIN_TRAIL=0.5
                    "loss_equiv":0.0,  # LOSS=1.0

                    # (opsiyonel, sonra lazım olacak)
                    "trail_wins":0,  # kârlı STOP sayacı
                    "total_pnl_usdt_est":0.0  # notional * pnl% (yaklaşık)
                }
            return symbol_stats[sym1]

        try:
            records: list[dict] = []

            if mode == "json":
                # closed_signals_state.json genelde liste olur
                with open(path_to_use, "r", encoding="utf-8") as f:
                    try:
                        obj = json.load(f)
                    except json.JSONDecodeError:
                        obj = None

                if isinstance(obj, list):
                    records = [r for r in obj if isinstance(r, dict)]
                elif isinstance(obj, dict):
                    # Bazı sistemler dict içinde list tutabilir
                    for k in ("closed_signals", "signals", "data"):
                        vv = obj.get(k)
                        if isinstance(vv, list):
                            records = [r for r in vv if isinstance(r, dict)]
                            break

                    # (çok nadir) -> dict value'ları trade kaydı ise listeye çek
                    if not records:
                        vals = list(obj.values())
                        if vals and all(isinstance(x, dict) for x in vals):
                            records = vals
                else:
                    records = []

            else:
                # jsonl fallback: signal_close satırlarını oku
                with open(path_to_use, "r", encoding="utf-8") as f:
                    for line in f:
                        line = (line or "").strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(rec, dict):
                            continue
                        if rec.get("type") != "signal_close":
                            continue
                        records.append(rec)

            for rec in records:
                # symbol
                symbol_raw = (
                        rec.get("symbol") or rec.get("coin") or rec.get("pair")
                        or rec.get("raw_symbol") or rec.get("ccxt_symbol") or rec.get("market")
                )
                sym = _norm_sym(symbol_raw)
                if not sym:
                    continue

                stats = _ensure_stats(sym)
                stats["trades"] += 1

                exit_type = str(rec.get("exit_type", "") or "").upper().strip()

                # pnl
                try:
                    src = (
                        rec.get("realized_effective_lev")
                        if rec.get("realized_effective_lev") is not None else
                        rec.get("realized_effective_pct")
                        if rec.get("realized_effective_pct") is not None else
                        rec.get("realized_net_pct")
                        if rec.get("realized_net_pct") is not None else
                        rec.get("pnl_pct", 0.0)
                    )
                    pnl = float(src or 0.0)
                except (ValueError, TypeError):
                    pnl = 0.0

                stats["total_pnl"] += pnl

                is_win = pnl > 0.0

                # --- notional -> pnl_usdt_est (yaklaşık) ---
                meta = rec.get("meta_at_open") or rec.get("meta") or {}
                notional = None
                if isinstance(meta, dict):
                    notional = meta.get("notional_usdt") or meta.get("lot_usdt") or meta.get("lot")

                try:
                    notional = float(notional) if notional is not None else None
                except (TypeError, ValueError):
                    notional = None

                if notional is not None:
                    stats["total_pnl_usdt_est"] += float(notional) * (float(pnl) / 100.0)

                # --- outcome -> streak + weighted win_rate ---
                if exit_type in ("TARGET_FINAL", "TARGET"):
                    if is_win:
                        # WIN_TARGET
                        stats["wins"] += 1
                        stats["win_equiv"] += 1.0

                        stats["consecutive_losses"] = 0
                        stats["consecutive_wins"] = (stats["consecutive_wins"] + 1) if stats["last_outcome_win"] else 1
                        stats["last_outcome_win"] = True
                    else:
                        # TARGET etiketi ama net negatif -> LOSS
                        stats["losses"] += 1
                        stats["loss_equiv"] += 1.0

                        stats["consecutive_wins"] = 0
                        stats["consecutive_losses"] = (stats["consecutive_losses"] + 1) if (
                            not stats["last_outcome_win"]) else 1
                        stats["last_outcome_win"] = False

                elif exit_type == "STOP":
                    if is_win:
                        # ✅ WIN_TRAIL: kârla stop (TP sonrası trailing vb.)
                        stats["wins"] += 1
                        stats["win_equiv"] += 0.5
                        stats["trail_wins"] += 1

                        stats["consecutive_losses"] = 0
                        stats["consecutive_wins"] = (stats["consecutive_wins"] + 1) if stats["last_outcome_win"] else 1
                        stats["last_outcome_win"] = True
                    else:
                        # LOSS_STOP
                        stats["losses"] += 1
                        stats["loss_equiv"] += 1.0

                        stats["consecutive_wins"] = 0
                        stats["consecutive_losses"] = (stats["consecutive_losses"] + 1) if (
                            not stats["last_outcome_win"]) else 1
                        stats["last_outcome_win"] = False
                else:
                    # diğer kapanış türleri: streak bozmak istemiyorsan pass
                    pass

            # finalize stats
            for _sym, st in symbol_stats.items():
                trades = int(st.get("trades", 0) or 0)
                if trades > 0:
                    win_equiv = float(st.get("win_equiv", 0.0) or 0.0)
                    st["win_rate"] = win_equiv / trades  # 0..1
                    st["avg_pnl"] = float(st.get("total_pnl", 0.0) or 0.0) / trades
                else:
                    st["win_rate"] = 0.0
                    st["avg_pnl"] = 0.0

            self._cache = symbol_stats
            self._last_load_time = datetime.now(timezone.utc)
            logger.info(f"[SymbolTracker] {len(self._cache)} sembol analiz edildi.")

        except Exception as e:
            logger.error(f"[SymbolTracker] Veri yükleme hatası: {e}", exc_info=True)

    def get_stats(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Sembol istatistiğini döndürür.
        Cache anahtarları core format: "SOLUSDT".
        Bu yüzden gelen input hangi format olursa olsun core'a normalize eder.
        """
        if not symbol:
            return None

        # Cache güncel değilse tazele
        try:
            self._load_data()
        except Exception:
            pass

        # 1) Direkt hit (bazı legacy yerler core gönderiyor olabilir)
        s_raw = str(symbol).strip().upper()
        hit = self._cache.get(s_raw)
        if hit:
            return hit

        # 2) Core normalize ile hit
        s_core = self._norm_symbol_core(s_raw)
        if s_core:
            hit = self._cache.get(s_core)
            if hit:
                return hit

        # 3) Futures RAW -> ":" kırp -> tekrar core dene (ekstra tolerans)
        if ":" in s_raw:
            s2 = s_raw.split(":", 1)[0].strip()
            hit = self._cache.get(s2)
            if hit:
                return hit
            s2c = self._norm_symbol_core(s2)
            if s2c:
                hit = self._cache.get(s2c)
                if hit:
                    return hit

        return None

    # ---------------------------------------------------------------------
    # ✅ Backward compatible adapters (eski kod record_loss/record_win çağırıyorsa kırılmasın)
    # ---------------------------------------------------------------------
    def record_loss(self, symbol: str, reason: str = "", meta: Optional[dict] = None) -> None:
        """
        Eski modüller record_loss çağırabilir. Bu tracker artık 'log okuyup health üretme' odaklı.
        O yüzden burada sadece:
          - gerekiyorsa blacklist'i tetiklemek için cache refresh (opsiyonel)
          - asla exception fırlatmamak
        """
        try:
            _ = meta
            sym = self._sym_clean(symbol)
            logger.info(f"[SymbolTracker][record_loss] sym={sym} reason={reason}")
            # Not: streak/blacklist hesabı dosyadan türediği için burada anlık yazma yok.
            # Eğer istersen ileride burada ayrı bir event log yazabiliriz.
            return
        except Exception:
            return

    def record_win(self, symbol: str, reason: str = "", meta: Optional[dict] = None) -> None:
        try:
            _ = meta
            sym = self._sym_clean(symbol)
            logger.info(f"[SymbolTracker][record_win] sym={sym} reason={reason}")
            return
        except Exception:
            return
    # ---------------------------------------------------------------------
    # ✅ Public helpers (backward-compatible)
    # ---------------------------------------------------------------------
    def is_blacklisted(self, symbol: str) -> bool:
        """
        alarm_strateji._handle_signal_outcome gibi eski çağrılar için.
        """
        try:
            st = self._is_blacklisted(symbol)
            return bool(st.get("blacklisted", False))
        except Exception:
            return False

    def get_blacklist_state(self, symbol: str) -> Dict[str, Any]:
        """
        Detay lazım olursa: reason, cooldown_until, streak...
        """
        try:
            return self._is_blacklisted(symbol)
        except Exception:
            return {"blacklisted": False}

    def refresh_blacklist(self, force: bool = False) -> None:
        """
        Dışarıdan cache tazelemek istersen.
        """
        try:
            self._load_blacklist(force=force)
        except Exception:
            return

    def evaluate_and_apply_health_policy(self, symbol: str) -> Dict[str, Any]:
        """
        ÖNEMLİ:
        - get_symbol_health() hem health döndürür hem de gerekiyorsa blacklist yazar.
        - alarm_strateji tarafı artık LOSS sonrası bunu çağırarak blacklist'i gerçekten tetikleyebilir.
        """
        try:
            return self.get_symbol_health(symbol)
        except Exception:
            return {
                "status": "neutral",
                "action": "allow",
                "penalty_factor": 1.0,
                "priority_bonus": 0.0,
                "reason": "health_eval_error",
                "blacklisted": False,
                "cooldown_until": "",
                "stop_streak": 0,
                "win_streak": 0,
            }

    def get_symbol_health(self, symbol: str) -> Dict[str, Any]:
        """
        Health + Action + Cooldown + Priority Bonus

        Not:
        - stats yoksa (cache'de hiç yok): bot bu sembolde henüz kapanmış trade üretmemiştir.
        - trades < 2 ise: geçmiş var ama istatistik için yetersiz örnek.
        """
        # Cache’i olabildiğince güncel tut
        try:
            self._load_data()
        except Exception:
            pass

        # Debug: cache örnekleri
        try:
            logger.info(f"[HEALTH_LOOKUP_DBG] in={symbol} cache_keys_sample={list(self._cache.keys())[:5]}")
        except Exception:
            pass

        # Sembolü core anahtara indir (blacklist + stats lookup tutarlı olsun)
        try:
            core = self._norm_symbol_core(symbol)
        except Exception:
            core = None
        sym_key = core or str(symbol or "").strip().upper()

        # 0) blacklist hard gate
        bl_state = self._is_blacklisted(sym_key)
        if isinstance(bl_state, dict) and bl_state.get("blacklisted"):
            return {
                "status":"bad",
                "action":"blacklist_skip",
                "penalty_factor":0.0,  # skor çarpanı anlamsız; zaten skip
                "priority_bonus":0.0,
                "reason":str(bl_state.get("reason", "BLACKLIST")) or "BLACKLIST",
                "cooldown_until":str(bl_state.get("cooldown_until", "")) or "",
                "stop_streak":int(bl_state.get("streak", 0) or 0),
                "win_streak":0,
                "blacklisted":True,
            }

        # 1) stats lookup
        stats = self.get_stats(sym_key)

        try:
            logger.info(
                f"[HEALTH_DBG] in={symbol} core={core} stats_trades={(stats or {}).get('trades')} cache_size={len(self._cache)}"
            )
        except Exception:
            pass

        # 1A) cache’de hiç kayıt yok -> (A) Bot bu sembolde kapanmış trade üretmemiş
        if not stats:
            return {
                "status":"neutral",
                "action":"allow",
                "penalty_factor":1.0,
                "priority_bonus":0.0,
                "reason":"Geçmiş yok: Bu sembolde henüz kapanmış işlem bulunmuyor",
                "cooldown_until":"",
                "stop_streak":0,
                "win_streak":0,
                "blacklisted":False,
            }

        # 1B) trade sayısı az
        trades = int(stats.get("trades", 0) or 0)
        if trades < 2:
            return {
                "status":"neutral",
                "action":"allow",
                "penalty_factor":1.0,
                "priority_bonus":0.0,
                "reason":"Yetersiz örnek: trades<2",
                "cooldown_until":"",
                "stop_streak":int(stats.get("consecutive_losses", 0) or 0),
                "win_streak":int(stats.get("consecutive_wins", 0) or 0),
                "blacklisted":False,
            }

        # metrikler
        win_rate = float(stats.get("win_rate", 0.0) or 0.0)  # 0..1
        avg_pnl = float(stats.get("avg_pnl", 0.0) or 0.0)
        stop_streak = int(stats.get("consecutive_losses", 0) or 0)
        win_streak = int(stats.get("consecutive_wins", 0) or 0)

        # 2) STOP STREAK policy (öncelikli)
        if stop_streak >= 1:
            if stop_streak in self._STOP_POLICY:
                pol_action, pol_factor, cooldown_h, pol_reason = self._STOP_POLICY[stop_streak]
            else:
                pol_action, pol_factor, cooldown_h, reason_tpl = self._STOP_POLICY_FALLBACK
                pol_reason = str(reason_tpl).format(n=stop_streak)

            # 3+ ise blacklist
            if pol_action == "blacklist_skip" and int(cooldown_h or 0) > 0:
                bl = self._blacklist_add(sym_key, int(cooldown_h), pol_reason, stop_streak)
                return {
                    "status":"bad",
                    "action":"blacklist_skip",
                    "penalty_factor":float(pol_factor),
                    "priority_bonus":0.0,
                    "reason":pol_reason,
                    "cooldown_until":str((bl or {}).get("cooldown_until", "")) or "",
                    "stop_streak":stop_streak,
                    "win_streak":win_streak,
                    "blacklisted":True,
                }

            # 1-2 ise soft penalty
            return {
                "status":"bad",
                "action":"penalize",
                "penalty_factor":float(pol_factor),
                "priority_bonus":0.0,
                "reason":pol_reason,
                "cooldown_until":"",
                "stop_streak":stop_streak,
                "win_streak":win_streak,
                "blacklisted":False,
            }

        # 3) WIN policy (ödül)
        if win_streak >= 2:
            if win_streak in self._WIN_POLICY:
                pol_action, pol_factor, pol_bonus, pol_reason = self._WIN_POLICY[win_streak]
            else:
                pol_action, pol_factor, pol_bonus, reason_tpl = self._WIN_POLICY_FALLBACK
                pol_reason = str(reason_tpl).format(n=win_streak)

            return {
                "status":"good",
                "action":"boost",
                "penalty_factor":float(pol_factor),
                "priority_bonus":float(pol_bonus),
                "reason":pol_reason,
                "cooldown_until":"",
                "stop_streak":stop_streak,
                "win_streak":win_streak,
                "blacklisted":False,
            }

        # 4) Düşük WR cezası (streak yoksa)
        if trades >= int(self._min_trades_for_wr_penalty or 0) and win_rate < 0.35:
            return {
                "status":"bad",
                "action":"penalize",
                "penalty_factor":0.85,
                "priority_bonus":0.0,
                "reason":f"📉 Düşük WR (%{win_rate * 100:.0f})",
                "cooldown_until":"",
                "stop_streak":stop_streak,
                "win_streak":win_streak,
                "blacklisted":False,
            }

        # 5) Yüksek performans bonusu (streak yoksa)
        if trades >= 5 and win_rate >= 0.70 and avg_pnl > 1.0:
            return {
                "status":"good",
                "action":"boost",
                "penalty_factor":1.05,
                "priority_bonus":1.0,
                "reason":f"⭐ Yüksek Performans (WR %{win_rate * 100:.0f})",
                "cooldown_until":"",
                "stop_streak":stop_streak,
                "win_streak":win_streak,
                "blacklisted":False,
            }

        # 6) default
        return {
            "status":"neutral",
            "action":"allow",
            "penalty_factor":1.0,
            "priority_bonus":0.0,
            "reason":"Nötr",
            "cooldown_until":"",
            "stop_streak":stop_streak,
            "win_streak":win_streak,
            "blacklisted":False,
        }


@dataclass
class _EdgeTable:
    bins: list[Tuple[float, float]]          # [(lo, hi), ...]
    avg_pnl: list[float]                     # her bin için ort pnl (yüzde)
    count: list[int]                         # örnek sayısı

class FeatureEdgeCalibrator:
    """
    Kapanan işlemlerden feature aralıklarına göre 'edge' (beklenen performans) çıkarır.
    Çıktı: 0.90 - 1.10 arası çarpan (varsayılan 1.0).
    """
    _instance = None
    _cache: dict[str, _EdgeTable] = {}
    _last_load_time: Optional[datetime] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(FeatureEdgeCalibrator, cls).__new__(cls)
        return cls._instance

    @staticmethod
    def _load_closed_df() -> pd.DataFrame:
        path = "closed_signals_state.json"
        if not os.path.exists(path):
            return pd.DataFrame()

        try:
            with open(path, "r", encoding="utf-8") as f:
                obj = json.load(f)
            if not isinstance(obj, list):
                return pd.DataFrame()
            df = pd.DataFrame(obj)
            return df
        except Exception:
            return pd.DataFrame()

    def refresh(self, ttl_min: int = 30) -> None:
        now = datetime.now(timezone.utc)
        if self._last_load_time and (now - self._last_load_time) < timedelta(minutes=ttl_min):
            return

        df = self._load_closed_df()
        if df is None or df.empty:
            self._cache = {}
            self._last_load_time = now
            return

        # pnl (öncelik: effective_lev -> effective -> net -> pnl_pct)
        pnl_col = None
        for c in ("realized_effective_lev", "realized_effective_pct", "realized_net_pct", "pnl_pct"):
            if c in df.columns:
                pnl_col = c
                break

        if pnl_col is None:
            self._cache = {}
            self._last_load_time = now
            return

        df[pnl_col] = pd.to_numeric(df[pnl_col], errors="coerce")
        df.dropna(subset=[pnl_col], inplace=True)

        # meta_at_open (yoksa edge üretemeyiz)
        if "meta_at_open" not in df.columns:
            self._cache = {}
            self._last_load_time = now
            return

        # Çıkarmak istediğimiz feature’lar (senin meta yapına uyumlu)
        features = [
            "ai_confidence",
            "volume_ratio",
            "bb_width",
            "adx",
            "momentum_tension",
        ]

        cache: dict[str, _EdgeTable] = {}

        def _extract(meta, k):
            if isinstance(meta, dict):
                return meta.get(k)
            return None

        for feat in features:
            tmp = df.copy()
            tmp[feat] = tmp["meta_at_open"].apply(lambda m: _extract(m, feat))
            tmp[feat] = pd.to_numeric(tmp[feat], errors="coerce")
            tmp.dropna(subset=[feat, pnl_col], inplace=True)

            if tmp.empty or tmp[feat].nunique() < 3:
                continue

            # Quantile binning: 5 bin (az veri varsa otomatik düşer)
            q = min(5, int(tmp[feat].nunique()))
            if q < 2:
                continue

            try:
                tmp["_bin"] = pd.qcut(tmp[feat], q=q, duplicates="drop")
            except Exception:
                continue

            g = tmp.groupby("_bin", observed=False).agg(
                avg_pnl=(pnl_col, "mean"),
                n=(pnl_col, "count")
            ).reset_index()

            bins: list[Tuple[float, float]] = []
            avg: list[float] = []
            cnt: list[int] = []

            for _, row in g.iterrows():
                b = row["_bin"]
                # Interval tipinden sınırları çek
                lo = float(getattr(b, "left", None))
                hi = float(getattr(b, "right", None))
                bins.append((lo, hi))
                avg.append(float(row["avg_pnl"]))
                cnt.append(int(row["n"]))

            cache[feat] = _EdgeTable(bins=bins, avg_pnl=avg, count=cnt)

        self._cache = cache
        self._last_load_time = now

    def get_edge_factor(
        self,
        meta: dict[str, Any],
        min_samples_per_bin: int = 12,
        clamp_lo: float = 0.90,
        clamp_hi: float = 1.10,
    ) -> float:
        """
        Meta içindeki feature değerlerine göre edge çarpanı döndürür.
        Basit toplama: her feature için küçük bir katkı üretir, sonra clamp eder.
        """
        self.refresh()

        if not self._cache:
            return 1.0

        # Her feature katkısını küçük tut
        total_adj = 0.0
        used = 0

        for feat, tab in self._cache.items():
            try:
                v = float(meta.get(feat))  # meta’da yoksa exception
            except Exception:
                continue

            # v hangi bin’de?
            idx = None
            for i, (lo, hi) in enumerate(tab.bins):
                if lo <= v <= hi:
                    idx = i
                    break
            if idx is None:
                continue

            if tab.count[idx] < min_samples_per_bin:
                continue

            # avg_pnl yüzdelik; bunu küçük bir çarpana çevir
            # Örn avg_pnl=+1.5% -> +0.015 * 0.6 = +0.009 => 0.9% boost
            pnl = tab.avg_pnl[idx] / 100.0
            contrib = max(-0.03, min(0.03, pnl * 0.6))  # katkıyı sınırla
            total_adj += contrib
            used += 1

        if used == 0:
            return 1.0

        factor = 1.0 + (total_adj / max(1, used))
        return float(max(clamp_lo, min(clamp_hi, factor)))


def run_weekly_optimizer(cls, lookback_days=7) -> Dict[str, Any]:
    """
    Basit parametre öneri sistemi.
    """
    logging.info(f"Haftalık optimizer başlatıldı. Gün sayısı: {lookback_days}")

    if not os.path.exists(cls._signal_log_file):
        return {"status":"no_data", "suggestions":[]}

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    opens = []
    closes = []

    try:
        with open(cls._signal_log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line: continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts = rec.get('timestamp')
                if not ts: continue

                try:
                    ts_dt = cls._ensure_aware(ts)
                except (ValueError, TypeError):
                    continue

                if ts_dt < cutoff: continue

                if rec.get('type') == 'signal_open':
                    opens.append(rec)
                elif rec.get('type') == 'signal_close':
                    closes.append(rec)
    except Exception as e:
        logging.error(f"Optimizer log okuma hatası: {e}")
        return {"status":"read_error", "suggestions":[]}

    # Sinyalleri eşleştir
    performance = []
    for close in closes:
        symbol = close.get('symbol')
        direction = close.get('direction')

        # İlgili açılış sinyalini bul
        related = [o for o in opens if o['symbol'] == symbol and o['direction'] == direction]
        if related:
            open_rec = related[-1]
            performance.append({
                'symbol':symbol,
                'pnl_pct':float(close.get('pnl_pct', 0)),
                'exit_type':close.get('exit_type'),
                'volume_ratio':open_rec.get('volume_ratio'),
                'confidence':open_rec.get('confidence_index')
            })

    if not performance:
        return {"status":"no_performance", "suggestions":[]}

    # İstatistikler
    total_perf = len(performance)
    stop_count = sum(1 for p in performance if p.get('exit_type') == 'STOP')
    target_count = sum(1 for p in performance if p.get('exit_type') in ('TARGET', 'TARGET_FINAL'))

    avg_pnl = sum(p['pnl_pct'] for p in performance) / total_perf
    stop_ratio = stop_count / total_perf
    target_ratio = target_count / total_perf

    suggestions = []

    if stop_ratio > 0.5:
        suggestions.append({
            'param':'min_volume_ratio',
            'action':'increase',
            'reason':f"Stop oranı yüksek (%{stop_ratio * 100:.1f}), hacim filtresini artır."
        })

    if target_ratio > 0.6 and total_perf < 5:
        suggestions.append({
            'param':'min_volume_ratio',
            'action':'decrease',
            'reason':"Başarı yüksek ama sinyal az, hacim filtresini gevşet."
        })

    if avg_pnl < 0.5 and stop_ratio < 0.3:
        suggestions.append({
            'param':'momentum_threshold',
            'action':'decrease',
            'reason':"Sinyaller güvenli ama kâr düşük, momentum eşiğini düşür."
        })

    return {
        "status":"ok",
        "stats":{
            "avg_pnl":avg_pnl,
            "stop_ratio":stop_ratio,
            "target_ratio":target_ratio,
            "sample_size":total_perf
        },
        "suggestions":suggestions
    }


def export_alarm_summary_for_autotune(
        cls, start_dt: Optional[str] = None,
        end_dt: Optional[str] = None,
        out_csv: str = "analytics/alarms_autotune_summary.csv"):
    _ = cls
    path = os.path.join("analytics", "alarms_meta_log.jsonl")
    if not os.path.exists(path):
        return None

    rows = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                    ts = obj.get("ts")
                    if start_dt and ts < start_dt: continue
                    if end_dt and ts > end_dt: continue

                    if obj.get("source") in ("ai_scan", "strategy_scan"):
                        meta = obj.get("meta", {})
                        rows.append({
                            "ts":ts,
                            "source":obj.get("source"),
                            "strategy_id":obj.get("strategy_id"),
                            "symbol":obj.get("symbol"),
                            "ai_confidence":meta.get("ai_confidence"),
                            "technical_score":meta.get("technical_score"),
                            "volume_usd":meta.get("volume_usd")
                        })
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        logger.error(f"Export hatası: {e}")
        return None

    if not rows:
        return None

    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_csv(out_csv, index=False, encoding="utf-8")
    return out_csv


def compute_strategy_fit_score(
        cls,
        meta: dict[str, Any],
        strategy_id: str,
        timeframe: str,
        settings: dict[str, Any],
        mode: str,
) -> float:
    _ = cls
    try:
        # --- helpers aynı kalsın ---
        def _norm_potential(x: Any) -> float:
            try:
                v = float(x or 0.0)
                av = abs(v)
                if 1.0 < av <= 300.0:
                    return v / 100.0
                return v
            except (ValueError, TypeError):
                return 0.0

        mode_lc = (mode or "").lower()
        if mode_lc == "ai_scan":
            pot = _norm_potential(meta.get("potential_pct", meta.get("movement_potential", 0.0)))
        else:
            pot = _norm_potential(meta.get("strat_potential", meta.get("strat_potential_pct", 0.0)))

        def sf(val, default=0.0):
            try:
                return float(val or default)
            except (ValueError, TypeError):
                return default

        ai = sf(meta.get("ai_confidence", meta.get("confidence")), 0.0)
        vr = sf(meta.get("volume_ratio"), 1.0)
        mom = sf(meta.get("momentum", meta.get("momentum_abs")), 0.0)
        comp = sf(meta.get("compression", meta.get("compression_ratio")), 0.0)
        delay_q = sf(meta.get("delay_quality"), 0.0)

        mt = 1.0 if (meta.get("mt_align") or meta.get("trend_alignment")) else 0.0
        struct_ok = 1.0 if meta.get("structure_ok") else 0.0

        # ---------------------------------------------------------
        # ✅ NEW: weights resolve (settings -> tf_profile -> defaults)
        # ---------------------------------------------------------
        section = "ai_scan" if mode_lc == "ai_scan" else "strategy_scan"

        # 1) mevcut davranış: settings içinden dene
        w = (settings or {}).get(section, {}).get("weights", {}).get(strategy_id, {}) if isinstance(settings, dict) else {}

        # 2) ✅ fallback: ConfigService.tf_profile(timeframe) içinden dene
        if not w:
            try:
                tfp = ConfigService.tf_profile(timeframe) or {}
                if section == "ai_scan":
                    w = (tfp.get("ai_scan", {}) or {}).get("weights", {}).get(strategy_id, {}) or {}
                else:
                    # iki olası yapı: strategy_scan.weights.v?  veya strategy_scan.v?.weights
                    st = tfp.get("strategy_scan", {}) or {}
                    w = (st.get("weights", {}) or {}).get(strategy_id, {}) or (st.get(strategy_id, {}) or {}).get("weights", {}) or {}
            except Exception:
                w = {}

        # 3) ✅ defaults: weights yoksa sistem susmasın
        if not w:
            if strategy_id == "v1":
                w = {
                    "ai_conf": 0.25,
                    "potential": 0.20,
                    "volume_ratio": 0.15,
                    "momentum": 0.20,
                    "mt_align": 0.10,
                    "volatility_ok": 0.10,
                }
            else:  # v2
                w = {
                    "ai_conf": 0.20,
                    "potential": 0.20,
                    "compression": 0.20,
                    "volume_ratio": 0.15,
                    "delay": 0.10,
                    "structure_ok": 0.15,
                }

        # ---------------------------------------------------------
        # normalize hesapları aynı kalsın
        # ---------------------------------------------------------
        def clamp(x): return max(0.0, min(1.0, x))

        tf_min = 60
        if timeframe.endswith('m'):
            tf_min = int(timeframe[:-1])
        elif timeframe.endswith('h'):
            tf_min = int(timeframe[:-1]) * 60

        mom_den = 0.015 if tf_min <= 15 else 0.03
        comp_den = 1.0 if tf_min <= 15 else 0.6

        pot_norm = clamp(pot / 0.08)
        vr_norm = clamp((vr - 0.8) / 0.6)
        mom_norm = clamp(mom / mom_den)
        comp_norm = clamp(comp / comp_den)
        delay_norm = clamp(1.0 - delay_q)

        if strategy_id == "v1":
            score = (
                w.get("ai_conf", 0) * ai +
                w.get("potential", 0) * pot_norm +
                w.get("volume_ratio", 0) * vr_norm +
                w.get("momentum", 0) * mom_norm +
                w.get("mt_align", 0) * mt +
                w.get("volatility_ok", 0) * struct_ok
            )
        else:
            score = (
                w.get("ai_conf", 0) * ai +
                w.get("potential", 0) * pot_norm +
                w.get("compression", 0) * comp_norm +
                w.get("volume_ratio", 0) * vr_norm +
                w.get("delay", 0) * delay_norm +
                w.get("structure_ok", 0) * struct_ok
            )

        return round(score * 100, 1)

    except Exception as e:
        logger.error(f"[FIT_SCORE_ERR] {e}")
        return 0.0


def diversify_and_limit(
        cls,
        candidates: list[dict[str, Any]],
        settings: dict[str, Any],
        timeframe: str,
        strategy_id: str,
) -> list[dict[str, Any]]:
    _ = cls
    try:
        div = settings.get("diversification", {})
        if not div.get("enable", True):
            return candidates

        tf_min = 60
        if timeframe.endswith('m'): tf_min = int(timeframe[:-1])

        base_max = int(div.get("max_per_sector", 2))
        delta = 1 if tf_min <= 15 else (-1 if tf_min >= 240 else 0)
        max_per_sector = max(1, base_max + delta)

        buckets = {}
        for c in candidates:
            key = str(c.get("sector") or c.get("corr_key") or "GEN")
            buckets.setdefault(key, []).append(c)

        out = []
        score_key = f"{strategy_id}_score"

        for key, arr in buckets.items():
            arr.sort(key=lambda x:float(x.get(score_key, 0) or 0), reverse=True)
            out.extend(arr[:max_per_sector])

        limits = settings.get("ai_scan", {}).get("limits", {})
        max_total = int(limits.get("max_total", 0))

        if 0 < max_total < len(out):
            out = out[:max_total]

        return out
    except Exception as e:
        logger.error(f"[DIVERSIFY_ERR] {e}")
        return candidates


def cross_deduplicate(cls, v1_list: list, v2_list: list, epsilon: float = 0.02) -> tuple[list, list]:
    _ = cls
    try:
        v1_map = {x['symbol']:x for x in v1_list}
        v2_map = {x['symbol']:x for x in v2_list}

        common = set(v1_map.keys()) & set(v2_map.keys())

        final_v1 = [x for x in v1_list if x['symbol'] not in common]
        final_v2 = [x for x in v2_list if x['symbol'] not in common]

        for sym in common:
            c1 = v1_map[sym]
            c2 = v2_map[sym]

            s1 = float(c1.get("v1_score", 0))
            s2 = float(c2.get("v2_score", 0))

            if s1 > s2 + epsilon:
                final_v1.append(c1)
            elif s2 > s1 + epsilon:
                final_v2.append(c2)
            else:
                conf1 = float(c1.get("ai_confidence", 0))
                conf2 = float(c2.get("ai_confidence", 0))

                if conf1 >= conf2:
                    final_v1.append(c1)
                else:
                    final_v2.append(c2)

        return final_v1, final_v2
    except Exception as e:
        logger.error(f"[CROSS_DEDUP_ERR] {e}")
        return v1_list, v2_list


def performance_period_summary(cls, period: str = "day", date=None, start=None, end=None):
    """
    Belirtilen periyoda göre performans özeti çıkarır.
    """
    try:
        closed_signals = cls.get_closed_signals()
        empty_res = {'trades':0, 'win_rate':0, 'avg_pnl':0, 'total_pnl':0, 'best':0, 'worst':0}, pd.DataFrame()

        if not closed_signals:
            return empty_res

        now = datetime.now(timezone.utc)

        if period == 'day':
            ref_date = date or now.date()
            start_dt = datetime(ref_date.year, ref_date.month, ref_date.day, tzinfo=timezone.utc)
            end_dt = start_dt + timedelta(days=1)
        elif period == 'week':
            ref_date = date or now.date()
            start_dt = datetime(ref_date.year, ref_date.month, ref_date.day, tzinfo=timezone.utc) - timedelta(
                days=ref_date.weekday())
            end_dt = start_dt + timedelta(weeks=1)
        elif period == 'month':
            ref_date = date or now.date()
            start_dt = datetime(ref_date.year, ref_date.month, 1, tzinfo=timezone.utc)
            if start_dt.month == 12:
                end_dt = datetime(start_dt.year + 1, 1, 1, tzinfo=timezone.utc)
            else:
                end_dt = datetime(start_dt.year, start_dt.month + 1, 1, tzinfo=timezone.utc)
        elif period == 'range' and start and end:
            start_dt = cls._ensure_aware(start)
            end_dt = cls._ensure_aware(end)
        else:
            return empty_res

        filtered = []
        for sig in closed_signals:
            ct = sig.get('closed_time')
            if not ct: continue

            try:
                ct_dt = cls._ensure_aware(ct)
                if start_dt <= ct_dt < end_dt:
                    filtered.append(sig)
            except (ValueError, TypeError):
                continue

        if not filtered:
            return empty_res

        df = pd.DataFrame(filtered)
        pnl_col = None
        for c in ("realized_effective_lev", "realized_effective_pct", "realized_net_pct", "pnl_pct"):
            if c in df.columns:
                pnl_col = c
                break
        if pnl_col is None:
            pnl_col = "pnl_pct"
            df[pnl_col] = 0.0

        df[pnl_col] = pd.to_numeric(df[pnl_col], errors='coerce').fillna(0.0)

        total = len(df)
        wins = df[df['exit_type'] == 'TARGET_FINAL'].shape[0]

        summary = {
            'trades':total,
            'win_rate':round((wins / total * 100), 2) if total > 0 else 0,
            'avg_pnl': round(df[pnl_col].mean(), 2),
            'total_pnl': round(df[pnl_col].sum(), 2),
            'best': round(df[pnl_col].max(), 2),
            'worst': round(df[pnl_col].min(), 2)
        }

        return summary, df

    except Exception as e:
        logger.error(f"Performans özeti hatası: {e}", exc_info=True)
        return {'trades':0, 'win_rate':0, 'avg_pnl':0, 'total_pnl':0, 'best':0, 'worst':0}, pd.DataFrame()
