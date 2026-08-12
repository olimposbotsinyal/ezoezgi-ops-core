import os
import json
import logging
import math
import re
from typing import Any, Optional, Dict, List, Tuple
from datetime import timezone, datetime, timedelta
from json import JSONDecodeError

from config_service import ConfigService

logger = logging.getLogger(__name__)


class StrategyAdaptiveTuner:
    """
    Kapanan sinyal verilerini okuyup, güvenli (guardrail'li) mikro ayarlamalar yapan adaptif katman.

    Hedefler:
    - "Gerçeğe yakın" kârlılık: fee + slippage dikkate alınır (effective pnl).
    - Düşük örnekle rastgele oynamayı engellemek: min_observations, cooldown, step limit.
    - Yanlış parametre oynamayı engellemek: scan_type'a göre doğru config path.
    - Bounds ihlallerini engellemek: bounds clamp + uyarı.
    """

    # Varsayılanlar (ConfigService ile override edilebilir)
    DEFAULT_MIN_OBSERVATIONS = 2
    DEFAULT_LOOKBACK_HOURS = 72
    DEFAULT_COOLDOWN_HOURS = 1

    # Ayar/hafıza dosyaları
    _auto_config_path = os.path.join("config", "StrategyAdaptiveTuner_ayarlari.json")
    _scan_log_path = os.path.join("analytics", "scan_performance_log.jsonl")
    _history_path = os.path.join("analytics", "auto_tune_history.jsonl")

    # -----------------------------
    # Config safe getter (dot-path tolerant)
    # -----------------------------
    @classmethod
    def _cfg_get(cls, path: str, default: Any = None) -> Any:
        """
        ConfigService.get(...) dot-path desteklemiyorsa bile çalışacak şekilde
        iki aşamalı okuma yapar:
          1) ConfigService.get(path, default)
          2) Eğer default döndüyse ve path noktalıysa, root objeden manuel traverse dener.
        """
        try:
            v = ConfigService.get(path, default)
        except Exception:
            v = default

        # Eğer değer "default" ise (ve path noktalı), manuel traversal deneyelim.
        # Not: default değeri gerçekten config'te de aynı olabilir; bu yüzden sadece "default döndü" kontrolü
        # bazen false-positive üretir. Ama pratikte dot-path sorunu yaşayan projelerde hayat kurtarır.
        if v is default and isinstance(path, str) and "." in path:
            root_key = path.split(".", 1)[0]
            try:
                root = ConfigService.get(root_key, None)
            except Exception:
                root = None

            if isinstance(root, dict):
                cur: Any = root
                rest = path.split(".", 1)[1]
                for part in rest.split("."):
                    if not isinstance(cur, dict):
                        return default
                    if part not in cur:
                        return default
                    cur = cur.get(part)
                return cur

        return v

    # -----------------------------
    # Loaders / Loggers
    # -----------------------------
    @classmethod
    def configure(cls, closed_signals_path: Optional[str] = None) -> None:
        """
        Uygulama başlangıcında çağrılabilir. Şu an sadece geriye uyumluluk için duruyor.
        Dosya yolu zaten ConfigService.get('reporting.save_paths.signals_closed') ile okunuyor.
        """
        _ = closed_signals_path
        return

    @classmethod
    def load_closed(cls) -> list[dict]:
        path = cls._cfg_get("reporting.save_paths.signals_closed", None)
        if not path or not isinstance(path, str) or not os.path.exists(path):
            logging.warning(f"[ADAPT_LOAD] Kapanan sinyal dosyası bulunamadı: {path}")
            return []

        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            # NaN/Infinity temizliği
            content = re.sub(r':\s*NaN\b', ': null', content)
            content = re.sub(r':\s*Infinity\b', ': null', content)
            content = re.sub(r':\s*-Infinity\b', ': null', content)

            arr = json.loads(content)
            return arr if isinstance(arr, list) else []
        except (OSError, JSONDecodeError) as e:
            logging.error(f"[CLOSED_SIGNALS_LOAD_ERR] {e}", exc_info=True)
            return []

    @classmethod
    def log_scan_event(
        cls,
        event_type: str,
        exchange: str,
        tf: str,
        scan_type: str,
        strategy_id: str,
        symbol: str,
        alarm_id: Optional[str] = None,
        signal_id: Optional[str] = None,
        control_mode: str = "manual",
    ) -> None:
        try:
            os.makedirs(os.path.dirname(cls._scan_log_path), exist_ok=True)
            log_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event_type": str(event_type),
                "exchange": str(exchange),
                "timeframe": str(tf),
                "scan_type": str(scan_type),
                "strategy_id": str(strategy_id),
                "symbol": str(symbol),
                "alarm_id": alarm_id,
                "signal_id": signal_id,
                "control_mode": str(control_mode),
            }
            try:
                logger.info(
                    f"[THRESH_USED] basis_len={cls._cfg_get('strategy.v2.basis_len', None)} "
                    f"delay_offset={cls._cfg_get('strategy.v2.delay_offset', None)} "
                    f"min_score={cls._cfg_get('scans.tf_profiles.15m.strategy_scan.v1.min_score.value', None)}"
                )
            except Exception:
                pass

            with open(cls._scan_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        except OSError as e:
            logging.error(f"[LOG_SCAN_EVENT_ERR] {e}", exc_info=True)

    @classmethod
    def _append_history(cls, row: dict) -> None:
        try:
            os.makedirs(os.path.dirname(cls._history_path), exist_ok=True)
            with open(cls._history_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            # history yazılamazsa sistemi durdurmayalım
            pass

    # -----------------------------
    # Helpers
    # -----------------------------
    @staticmethod
    def _ensure_aware(dt_obj: Any) -> Optional[datetime]:
        if isinstance(dt_obj, datetime):
            if dt_obj.tzinfo is None:
                return dt_obj.replace(tzinfo=timezone.utc)
            return dt_obj.astimezone(timezone.utc)

        if isinstance(dt_obj, str):
            s = dt_obj.strip()
            if not s:
                return None
            try:
                from dateutil import parser  # type: ignore

                parsed = parser.isoparse(s)
                if parsed.tzinfo is None:
                    return parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc)
            except (ValueError, TypeError, ImportError):
                return None
        return None

    @staticmethod
    def _safe_float(x: Any, default: float = 0.0) -> float:
        try:
            if x is None:
                return float(default)
            v = float(x)
            if math.isnan(v) or math.isinf(v):
                return float(default)
            return v
        except (TypeError, ValueError):
            return float(default)

    @staticmethod
    def _fmt_num(x: Any, nd: int = 4) -> str:
        try:
            if x is None:
                return "None"
            if isinstance(x, (int, float)):
                return f"{float(x):.{nd}f}"
            return str(x)
        except Exception:
            return str(x)

    @classmethod
    def _get_min_observations(cls) -> int:
        v = cls._cfg_get("tuner.min_observations", cls.DEFAULT_MIN_OBSERVATIONS)
        try:
            return int(v or cls.DEFAULT_MIN_OBSERVATIONS)
        except (TypeError, ValueError):
            return cls.DEFAULT_MIN_OBSERVATIONS

    @classmethod
    def _get_lookback_hours(cls) -> int:
        v = cls._cfg_get("tuner.lookback_hours", cls.DEFAULT_LOOKBACK_HOURS)
        try:
            return int(v or cls.DEFAULT_LOOKBACK_HOURS)
        except (TypeError, ValueError):
            return cls.DEFAULT_LOOKBACK_HOURS

    @classmethod
    def _get_cooldown_hours(cls) -> int:
        v = cls._cfg_get("tuner.cooldown_hours", cls.DEFAULT_COOLDOWN_HOURS)
        try:
            return int(v or cls.DEFAULT_COOLDOWN_HOURS)
        except (TypeError, ValueError):
            return cls.DEFAULT_COOLDOWN_HOURS

    @classmethod
    def filter_recent(cls, arr: list[dict]) -> list[dict]:
        lookback = cls._get_lookback_hours()
        window_start = datetime.now(timezone.utc) - timedelta(hours=lookback)
        out: list[dict] = []
        for r in arr:
            ts_str = r.get("closed_time")
            if not ts_str:
                continue
            ts = cls._ensure_aware(ts_str)
            if ts and ts >= window_start:
                out.append(r)
        return out

    @classmethod
    def group_by_all_dims(cls, recs: list[dict]) -> dict[str, list[dict]]:
        buckets: dict[str, list[dict]] = {}

        def _safe_str(x: Any, default: str = "") -> str:
            if x is None:
                return default
            return str(x).strip()

        for r in recs:
            meta_open = r.get("meta_at_open") or {}
            meta = r.get("meta") or {}

            if not isinstance(meta_open, dict):
                meta_open = {}
            if not isinstance(meta, dict):
                meta = {}

            exchange = _safe_str(meta.get("exchange") or meta_open.get("exchange") or "mexc", "mexc").lower()
            tf = _safe_str(r.get("timeframe") or meta.get("timeframe") or meta_open.get("timeframe") or "15m", "15m")
            scan_type = _safe_str(meta_open.get("source") or meta.get("source") or r.get("source") or "unknown", "unknown")
            sid = _safe_str(r.get("strategy_id") or r.get("strategy_hint") or "v1", "v1")

            regime = _safe_str(
                meta.get("regime_at_close")
                or meta.get("market_regime")
                or meta_open.get("regime_at_close")
                or meta_open.get("market_regime")
                or "Yatay",
                "Yatay",
            )

            key = f"{exchange}|{tf}|{scan_type}|{sid}|{regime}"
            buckets.setdefault(key, []).append(r)

        return buckets

    # -----------------------------
    # PnL: "Gerçeğe yakın" effective model
    # -----------------------------
    @classmethod
    def _fee_slippage_bps(cls, exchange: str, scan_type: str, sid: str, tf: str) -> Tuple[float, float]:
        """
        Varsayılan olarak ConfigService'den gelir.
        İstersen exchange/tf bazlı genişletebilirsin.
        """
        _ = scan_type, sid, tf
        fees = cls._cfg_get("fees_model", {}) or {}
        if not isinstance(fees, dict):
            fees = {}

        fee_bps = cls._safe_float(fees.get("fee_rate_bps"), 8.0)
        slip_bps = cls._safe_float(fees.get("slippage_bps"), 5.0)

        ex_over = cls._cfg_get(f"fees_model.exchanges.{exchange}", {}) or {}
        if isinstance(ex_over, dict) and ex_over:
            fee_bps = cls._safe_float(ex_over.get("fee_rate_bps"), fee_bps)
            slip_bps = cls._safe_float(ex_over.get("slippage_bps"), slip_bps)

        return fee_bps, slip_bps

    @classmethod
    def _gross_pct_of_closed(cls, rec: dict) -> float:
        bd = rec.get("close_breakdown") or {}
        if isinstance(bd, dict) and bd.get("gross_pct") is not None:
            return cls._safe_float(bd.get("gross_pct"), 0.0)
        if rec.get("realized_gross_pct") is not None:
            return cls._safe_float(rec.get("realized_gross_pct"), 0.0)
        if rec.get("realized_net_pct") is not None:
            return cls._safe_float(rec.get("realized_net_pct"), 0.0)
        return 0.0

    @classmethod
    def _effective_pct_of_closed(cls, rec: dict, exchange: str, scan_type: str, sid: str, tf: str) -> float:
        """
        Öncelik:
          1) realized_effective_pct (varsa)
          2) gross_pct - (fee+slippage)
        """
        if rec.get("realized_effective_pct") is not None:
            return cls._safe_float(rec.get("realized_effective_pct"), 0.0)

        gross = cls._gross_pct_of_closed(rec)
        fee_bps, slip_bps = cls._fee_slippage_bps(exchange, scan_type, sid, tf)

        # bps -> yüzde (örn 8 bps = 0.08%)
        cost_pct = (fee_bps + slip_bps) / 100.0
        return float(gross - cost_pct)

    # -----------------------------
    # Guardrails: cooldown / bounds / step
    # -----------------------------
    @classmethod
    def _cooldown_ok(cls, param_path: str) -> bool:
        """
        Son X saat içinde aynı parametre değiştiyse tekrar değiştirme.
        """
        cooldown_h = cls._get_cooldown_hours()
        if cooldown_h <= 0:
            return True

        try:
            if not os.path.exists(cls._history_path):
                return True

            cutoff = datetime.now(timezone.utc) - timedelta(hours=cooldown_h)
            # logging.info(f"[ADAPT_COOLDOWN] param={param_path} cooldown_h={cooldown_h} cutoff={cutoff.isoformat()}")

            with open(cls._history_path, "r", encoding="utf-8") as f:
                lines = f.readlines()[-300:]  # son 300 değişiklik yeter

            for ln in reversed(lines):
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    row = json.loads(ln)
                except JSONDecodeError:
                    continue

                if row.get("param") != param_path:
                    continue

                ts = cls._ensure_aware(row.get("timestamp"))
                if ts and ts >= cutoff:
                    logging.info(
                        f"[ADAPT_COOLDOWN_BLOCK] param={param_path} last_change_ts={ts.isoformat() if ts else None}")

                    return False

            return True

        except OSError:
            return True

    @staticmethod
    def _bounded(val: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, val))

    @classmethod
    def _read_bounds(cls, param_path: str) -> Optional[Tuple[float, float]]:
        """
        bounds'u config'ten okumaya çalışır.
        Örn path: scans.tf_profiles.15m.ai_scan.min_conf.value
        bounds olan obje: scans.tf_profiles.15m.ai_scan.min_conf.bounds
        """
        try:
            if param_path.endswith(".value"):
                bounds_path = param_path[:-6] + ".bounds"
            else:
                bounds_path = param_path + ".bounds"

            b = cls._cfg_get(bounds_path, None)
            if isinstance(b, list) and len(b) == 2:
                lo = float(b[0])
                hi = float(b[1])
                if lo > hi:
                    lo, hi = hi, lo
                return lo, hi
            return None
        except (TypeError, ValueError):
            return None

    @classmethod
    def _apply_bounds_if_any(cls, param_path: str, proposed: float) -> float:
        b = cls._read_bounds(param_path)
        if not b:
            return float(proposed)
        lo, hi = b
        return float(cls._bounded(float(proposed), lo, hi))

    @classmethod
    def _limit_step(cls, old: float, new: float, max_step_abs: float) -> float:
        """
        Parametreyi bir turda fazla zıplatmayı engelle.
        """
        old_f = float(old)
        new_f = float(new)
        if max_step_abs <= 0:
            return new_f
        delta = new_f - old_f
        if abs(delta) <= max_step_abs:
            return new_f
        return old_f + (max_step_abs if delta > 0 else -max_step_abs)

    # -----------------------------
    # Main
    # -----------------------------
    @classmethod
    def analyze_and_tune(cls) -> tuple[list, str, bool]:
        logging.info("[ADAPT] Adaptif ayarlama turu başlatıldı")
        retrain_request = False

        # Config hot reload
        try:
            ConfigService.hot_reload_if_changed()
        except Exception:
            pass

        min_obs = cls._get_min_observations()

        try:
            closed = cls.load_closed()
            recent = cls.filter_recent(closed)

            if not recent:
                lookback = cls._get_lookback_hours()
                msg = f"Son {lookback} saat içinde analiz edilecek kapanan sinyal bulunamadı."
                logging.info(f"[ADAPT] {msg}")
                return [], msg, retrain_request

            # 1) Rejimli gruplama
            by_group = cls.group_by_all_dims(recent)

            # 2) Fallback: rejim yüzünden küçükse rejimi düşür
            fallback_buckets: dict[str, list[dict]] = {}
            for key, recs in by_group.items():
                if len(recs) >= min_obs:
                    fallback_buckets[key] = recs
                    continue
                parts = key.split("|")
                if len(parts) == 5:
                    key2 = "|".join(parts[:4])  # exchange|tf|scan_type|sid
                    fallback_buckets.setdefault(key2, []).extend(recs)
                else:
                    fallback_buckets.setdefault(key, []).extend(recs)
            by_group = fallback_buckets

            all_changes: list[dict] = []
            report_lines: list[str] = []
            blocked_count = 0
            blocked_groups: list[str] = []
            blocked_rejects: list[dict] = []

            applied_ok = 0
            applied_fail = 0

            for group_name, recs in by_group.items():
                parts = group_name.split("|")
                if len(parts) < 4:
                    continue

                exchange = parts[0]
                tf = parts[1]
                scan_type = parts[2]
                sid = parts[3]

                if len(recs) < min_obs:
                    logging.info(f"[ADAPT] Grup '{group_name}' için yeterli örnek yok ({len(recs)}/{min_obs})")
                    continue

                effective_pnls = [
                    cls._effective_pct_of_closed(r, exchange=exchange, scan_type=scan_type, sid=sid, tf=tf)
                    for r in recs
                ]
                total = len(recs)

                wins = sum(1 for p in effective_pnls if p > 0)
                stops = sum(1 for r in recs if str(r.get("exit_type", "")).upper() == "STOP")

                win_rate = wins / total if total else 0.0
                stop_rate = stops / total if total else 0.0
                avg_eff = (sum(effective_pnls) / total) if total else 0.0

                logging.info(
                    f"[ADAPT] {group_name} | total={total} | win_rate={win_rate:.2f} | "
                    f"stop_rate={stop_rate:.2f} | avg_effective={avg_eff:.2f}%"
                )

                losing_trades = [r for r, p in zip(recs, effective_pnls) if p <= 0]

                changes: list[dict] = []
                if sid == "v1":
                    changes = cls._tune_v1(
                        group_name=group_name,
                        exchange=exchange,
                        tf=tf,
                        scan_type=scan_type,
                        stats={"win_rate": win_rate, "stop_rate": stop_rate, "avg_eff": avg_eff, "total": total},
                        losing_trades=losing_trades,
                    )
                elif sid == "v2":
                    changes = cls._tune_v2(
                        group_name=group_name,
                        exchange=exchange,
                        tf=tf,
                        scan_type=scan_type,
                        stats={"win_rate": win_rate, "stop_rate": stop_rate, "avg_eff": avg_eff, "total": total},
                        losing_trades=losing_trades,
                    )


                # Cooldown filtresi + bounds + step limit
                filtered: list[dict] = []

                reject = {
                    "bad_param":0,
                    "cooldown":0,
                    "no_change":0,
                }

                for ch in (changes or []):
                    param = ch.get("param")
                    if not param or not isinstance(param, str):
                        reject["bad_param"] += 1
                        continue

                    if not cls._cooldown_ok(param):
                        reject["cooldown"] += 1
                        continue

                    old = cls._safe_float(ch.get("old"), 0.0)
                    new = cls._safe_float(ch.get("new"), old)

                    new = cls._apply_bounds_if_any(param, new)

                    max_step = cls._safe_float(ch.get("max_step_abs"), 0.0)
                    if max_step > 0:
                        new = cls._limit_step(old, new, max_step)

                    if abs(new - old) < 1e-12:
                        reject["no_change"] += 1
                        continue

                    ch["new"] = new
                    filtered.append(ch)

                if changes:
                    blocked_now = max(0, len(changes) - len(filtered))
                    if blocked_now > 0:
                        logging.info(
                            f"[ADAPT_FILTERED_OUT] group={group_name} reject={reject} raw_changes={len(changes)}")
                        blocked_count += blocked_now
                        blocked_groups.append(group_name)
                        blocked_rejects.append(
                            {"group":group_name, "reject":reject, "raw_changes":len(changes), "blocked":blocked_now}
                        )

                if filtered:
                    report_lines.append(f"*{group_name}* için ayarlama:")
                    for ch in filtered:
                        report_lines.append(
                            f"  - `{ch['param']}`: `{cls._fmt_num(ch.get('old'), 4)}` → "
                            f"`{cls._fmt_num(ch.get('new'), 4)}` ({ch.get('reason', '')})"
                        )
                    all_changes.extend(filtered)

            # AUTO apply
            mode = str(ConfigService.control_mode() or "manual").strip().lower()

            if mode == "auto" and all_changes:
                applied_ok = 0
                applied_fail = 0

                for change in all_changes:
                    ConfigService.set_auto(change["param"], change["new"])
                    cls._append_history(
                        {
                            "timestamp":datetime.now(timezone.utc).isoformat(),
                            "param":change["param"],
                            "old":change.get("old"),
                            "new":change.get("new"),
                            "group":change.get("group"),
                            "reason":change.get("reason"),
                        }
                    )

                ConfigService.save_auto_config()
                try:
                    logger.info(
                        f"[THRESH_USED] basis_len={cls._cfg_get('strategy.v2.basis_len', None)} "
                        f"delay_offset={cls._cfg_get('strategy.v2.delay_offset', None)} "
                        f"min_score={cls._cfg_get('scans.tf_profiles.15m.strategy_scan.v1.min_score.value', None)}"
                    )
                except Exception:
                    pass

                # ✅ Apply sonrası verify (burada olmalı)
                applied_ok = 0
                applied_fail = 0
                for change in all_changes:
                    try:
                        after = cls._cfg_get(change["param"], None)
                        if isinstance(after, dict) and "value" in after:
                            after = after["value"]
                        after_f = cls._safe_float(after, float("nan"))
                        if abs(after_f - float(change["new"])) < 1e-9:
                            applied_ok += 1
                        else:
                            applied_fail += 1
                            logging.warning(
                                f"[ADAPT_VERIFY_MISMATCH] param={change['param']} wanted={change['new']} got={after}"
                            )
                    except Exception as e:
                        applied_fail += 1
                        logging.error(f"[ADAPT_VERIFY_ERR] param={change['param']} err={e}", exc_info=True)

                logging.info(f"[ADAPT_APPLY_VERIFY] ok={applied_ok} fail={applied_fail} total={len(all_changes)}")

                # Strategy version snapshot
                try:
                    from strategies.alarm_strateji import OlimposStrategy  # type: ignore

                    OlimposStrategy.save_strategy_version(
                        change_reason=json.dumps({"source": "auto_tune", "changes": all_changes}, ensure_ascii=False)
                    )
                except Exception:
                    pass
            # blocked özetini report_lines'a ekle (changes olmasa bile)
            if blocked_count > 0:
                report_lines.append(f"*Guardrail Engeli:* {blocked_count} değişiklik uygulanamadı (örn. cooldown).")
                for item in blocked_rejects[:5]:
                    report_lines.append(
                        f"  - {item['group']} reject={item['reject']} raw_changes={item['raw_changes']}")

            report_message = cls._generate_report(report_lines, all_changes, retrain_request)
            return all_changes, report_message, retrain_request

        except Exception as e:
            logging.error(f"[ADAPT_ERR] {e}", exc_info=True)
            return [], f"Otomatik ayarlama sırasında bir hata oluştu: {e}", False

    # -----------------------------
    # Tuning rules
    # -----------------------------
    @classmethod
    def _tune_v1(
        cls,
        *,
        group_name: str,
        exchange: str,
        tf: str,
        scan_type: str,
        stats: dict,
        losing_trades: list[dict],
    ) -> list[dict]:
        """
        V1 için tuning:
        - ai_scan ise: ai_scan thresholdlarını
        - strategy_scan ise: strategy_scan thresholdlarını
        ayarlar. Karıştırmaz.
        """
        _ = exchange
        changes: list[dict] = []

        win_rate = float(stats.get("win_rate", 0.0))
        stop_rate = float(stats.get("stop_rate", 0.0))
        avg_eff = float(stats.get("avg_eff", 0.0))
        total = int(stats.get("total", 0))

        scan = (scan_type or "").strip()
        tf = str(tf or "15m").strip()

        # ---- FIX: p_min_* init (linter: might be referenced before assignment) ----
        p_min_conf: Optional[str] = None
        p_min_vr: Optional[str] = None
        p_min_pot: Optional[str] = None
        p_min_score: Optional[str] = None
        p_min_vol_usd: Optional[str] = None

        # Param path'leri scan_type'a göre
        if scan == "ai_scan":
            p_min_conf = f"scans.tf_profiles.{tf}.ai_scan.min_conf.value"
            p_min_vr = f"scans.tf_profiles.{tf}.ai_scan.min_volume_ratio.value"
            p_min_pot = f"scans.tf_profiles.{tf}.ai_scan.min_potential_pct.value"
        elif scan == "strategy_scan":
            p_min_score = f"scans.tf_profiles.{tf}.strategy_scan.v1.min_score.value"
            p_min_vol_usd = f"scans.tf_profiles.{tf}.strategy_scan.v1.min_volume_usd.value"
        else:
            return []

        # KURAL A: Aşırı stop / kötü expectancy -> daha seçici ol
        if stop_rate >= 0.55 and len(losing_trades) >= 10:
            losing_metas: list[dict] = []
            for t in losing_trades:
                m = t.get("meta_at_open") or {}
                if isinstance(m, dict):
                    losing_metas.append(m)

            avg_losing_vr = 0.0
            avg_losing_mom = 0.0
            if losing_metas:
                avg_losing_vr = sum(cls._safe_float(m.get("volume_ratio"), 0.0) for m in losing_metas) / len(losing_metas)
                avg_losing_mom = sum(cls._safe_float(m.get("momentum"), 0.0) for m in losing_metas) / len(losing_metas)

            if scan == "ai_scan":
                assert p_min_conf and p_min_vr and p_min_pot  # type narrowing

                cur_conf = cls._safe_float(cls._cfg_get(p_min_conf, 0.66), 0.66)
                cur_vr = cls._safe_float(cls._cfg_get(p_min_vr, 0.8), 0.8)

                # Simplify chained comparison (linter)
                if 0 < avg_losing_vr < (cur_vr * 1.05):
                    new_vr = cur_vr + 0.05
                    changes.append(
                        {
                            "param": p_min_vr,
                            "old": cur_vr,
                            "new": new_vr,
                            "max_step_abs": 0.05,
                            "group": group_name,
                            "reason": f"Stop oranı yüksek, kayıplar düşük VR civarı (avg_vr={avg_losing_vr:.2f}) → VR filtresi sıkılaştırıldı.",
                        }
                    )
                else:
                    new_conf = cur_conf + 0.01
                    changes.append(
                        {
                            "param": p_min_conf,
                            "old": cur_conf,
                            "new": new_conf,
                            "max_step_abs": 0.01,
                            "group": group_name,
                            "reason": "Stop oranı yüksek → min_conf biraz artırıldı.",
                        }
                    )

                # Momentum filtresi: strategy.common.momentum_threshold
                cur_mth = cls._safe_float(cls._cfg_get("strategy.common.momentum_threshold", 0.1), 0.1)
                if abs(avg_losing_mom) < (cur_mth * 0.9):
                    new_mth = cur_mth * 1.05
                    changes.append(
                        {
                            "param": "strategy.common.momentum_threshold",
                            "old": cur_mth,
                            "new": new_mth,
                            "max_step_abs": 0.01,
                            "group": group_name,
                            "reason": f"Kayıplar düşük momentumda yoğun (avg_mom={avg_losing_mom:.5f}) → momentum filtresi sıkılaştırıldı.",
                        }
                    )

            elif scan == "strategy_scan":
                assert p_min_score and p_min_vol_usd  # type narrowing

                cur_score = cls._safe_float(cls._cfg_get(p_min_score, 60), 60)
                changes.append(
                    {
                        "param": p_min_score,
                        "old": cur_score,
                        "new": cur_score + 2,
                        "max_step_abs": 2.0,
                        "group": group_name,
                        "reason": "Stop oranı yüksek → strategy_scan v1 min_score artırıldı.",
                    }
                )

                cur_vol = cls._safe_float(cls._cfg_get(p_min_vol_usd, 2_000_000), 2_000_000)
                changes.append(
                    {
                        "param": p_min_vol_usd,
                        "old": cur_vol,
                        "new": cur_vol * 1.05,
                        "max_step_abs": cur_vol * 0.05,
                        "group": group_name,
                        "reason": "Stop oranı yüksek → strategy_scan v1 min_volume_usd artırıldı.",
                    }
                )

        # KURAL B: Çok iyi performans ve yeterli örnek -> biraz daha fırsat
        elif win_rate >= 0.70 and avg_eff >= 1.0 and total >= 60:
            if scan == "ai_scan":
                assert p_min_conf and p_min_pot  # type narrowing

                cur_conf = cls._safe_float(cls._cfg_get(p_min_conf, 0.66), 0.66)
                cur_pot = cls._safe_float(cls._cfg_get(p_min_pot, 1.0), 1.0)

                changes.append(
                    {
                        "param": p_min_conf,
                        "old": cur_conf,
                        "new": cur_conf - 0.005,
                        "max_step_abs": 0.01,
                        "group": group_name,
                        "reason": "Performans güçlü → daha fazla fırsat için min_conf çok az gevşetildi.",
                    }
                )
                changes.append(
                    {
                        "param": p_min_pot,
                        "old": cur_pot,
                        "new": cur_pot * 0.98,
                        "max_step_abs": 0.05,
                        "group": group_name,
                        "reason": "Performans güçlü → min_potential_pct çok az gevşetildi.",
                    }
                )

            elif scan == "strategy_scan":
                assert p_min_score  # type narrowing

                cur_score = cls._safe_float(cls._cfg_get(p_min_score, 60), 60)
                changes.append(
                    {
                        "param": p_min_score,
                        "old": cur_score,
                        "new": cur_score - 1,
                        "max_step_abs": 2.0,
                        "group": group_name,
                        "reason": "Performans güçlü → strategy_scan v1 min_score az gevşetildi.",
                    }
                )

        return changes

    @classmethod
    def _tune_v2(
        cls,
        *,
        group_name: str,
        exchange: str,
        tf: str,
        scan_type: str,
        stats: dict,
        losing_trades: list[dict],
    ) -> list[dict]:
        """
        V2 için daha doğru config path'leri:
          - strategy.v2.basis_len
          - strategy.v2.delay_offset
        """
        _ = exchange, tf, scan_type
        changes: list[dict] = []

        win_rate = float(stats.get("win_rate", 0.0))
        stop_rate = float(stats.get("stop_rate", 0.0))
        total = int(stats.get("total", 0))

        p_basis = "strategy.v2.basis_len"
        p_delay = "strategy.v2.delay_offset"

        if stop_rate >= 0.55 and len(losing_trades) >= 10:
            cur_basis = cls._safe_float(cls._cfg_get(p_basis, 2), 2)
            cur_delay = cls._safe_float(cls._cfg_get(p_delay, 0), 0)

            changes.append(
                {
                    "param": p_basis,
                    "old": cur_basis,
                    "new": cur_basis + 1,
                    "max_step_abs": 1.0,
                    "group": group_name,
                    "reason": "Stop oranı yüksek → SMMA basis_len artırıldı (gürültü azaltma).",
                }
            )
            changes.append(
                {
                    "param": p_delay,
                    "old": cur_delay,
                    "new": cur_delay + 1,
                    "max_step_abs": 1.0,
                    "group": group_name,
                    "reason": "Stop oranı yüksek → delay_offset artırıldı (daha fazla onay).",
                }
            )

        elif win_rate >= 0.75 and total >= 80:
            cur_basis = cls._safe_float(cls._cfg_get(p_basis, 2), 2)
            changes.append(
                {
                    "param": p_basis,
                    "old": cur_basis,
                    "new": cur_basis - 1,
                    "max_step_abs": 1.0,
                    "group": group_name,
                    "reason": "Performans güçlü → daha fazla sinyal için basis_len az düşürüldü.",
                }
            )

        return changes

    # -----------------------------
    # Reporting
    # -----------------------------
    @classmethod
    def _generate_report(cls, analysis_lines: list, changes: list, retrain_request: bool) -> str:
        header = [
            "🧠 *Otomatik Strateji Ayarlayıcı Raporu (Guardrail v2)*",
            "────────────────────────",
        ]
        body: list[str] = []

        if not changes:
            body.append("✅ Performans hedefler dahilinde veya yeterli güven yok. Parametre değişikliği yapılmadı.")
        else:
            control_mode = str(ConfigService.control_mode() or "manual").strip().lower()
            body.append("⚙️ *Uygulanan Değişiklikler (AUTO Mod):*" if control_mode == "auto"
            else "💡 *Değişiklik Önerileri (MANUAL Mod):*")

            for ch in changes:
                body.append(
                    f"  - `{ch.get('param')}`: `{cls._fmt_num(ch.get('old'), 4)}` → `{cls._fmt_num(ch.get('new'), 4)}` "
                    f"({ch.get('reason', '')})"
                )

        if retrain_request:
            body.append("\n🚨 *Yeniden Eğitim Önerisi*")
            body.append("AI performansı hedeflerin altına düştü. Model eğitimi öneriliyor.")

        full = header + analysis_lines + ["────────────────────────"] + body
        return "\n".join(full)

    @classmethod
    async def get_summary(cls) -> str:
        import asyncio
        try:
            changes, report_message, _ = await asyncio.to_thread(cls.analyze_and_tune)

            # ✅ Değişiklik yoksa ama guardrail yüzünden "uygulanamayan" durum varsa mesaj dön
            if not changes:
                if report_message and "Guardrail Engeli" in report_message:
                    return report_message
                return ""

            # Değişiklik varsa raporu gönderilebilir şekilde döndür
            return report_message or ""

        except Exception as e:
            logging.error(f"[GET_SUMMARY_ERR] {e}", exc_info=True)
            return f"❌ Özet oluşturulurken hata: {e}"


class AlarmRaporManager:
    def __init__(self, base_path: str = "alarm_raporlari"):
        self.base_path = base_path
        os.makedirs(base_path, exist_ok=True)

    def _get_monthly_folder(self, date: Optional[datetime] = None) -> str:
        if date is None:
            date = datetime.now(timezone.utc)
        return os.path.join(self.base_path, date.strftime("%Y-%m"))

    def _get_weekly_file(self, date: Optional[datetime] = None) -> str:
        if date is None:
            date = datetime.now(timezone.utc)
        week_start = date - timedelta(days=date.weekday())
        return os.path.join(
            self._get_monthly_folder(date),
            f"{week_start.strftime('%Y-%m-%d')}_haftalik_rapor.json",
        )

    def _clean_data_for_json(self, data: Any) -> Any:
        if isinstance(data, datetime):
            return data.isoformat()
        if isinstance(data, dict):
            return {k: self._clean_data_for_json(v) for k, v in data.items()}
        if isinstance(data, list):
            return [self._clean_data_for_json(v) for v in data]
        if isinstance(data, float):
            if math.isnan(data) or math.isinf(data):
                return None
            return data
        return data

    @staticmethod
    def _safe_load_json(filepath: str) -> List[Dict]:
        if not os.path.exists(filepath):
            return []
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

            content = re.sub(r":\s*NaN\b", ": null", content)
            content = re.sub(r":\s*Infinity\b", ": null", content)
            content = re.sub(r":\s*-Infinity\b", ": null", content)

            data = json.loads(content)
            return data if isinstance(data, list) else []
        except (OSError, JSONDecodeError) as e:
            logging.error(f"JSON okuma hatası ({filepath}): {e}")
            return []

    def kaydet_alarm_raporu(self, alarm_bilgisi: Dict[str, Any]) -> bool:
        try:
            monthly_folder = self._get_monthly_folder()
            os.makedirs(monthly_folder, exist_ok=True)
            weekly_file = self._get_weekly_file()

            mevcut_raporlar = self._safe_load_json(weekly_file)

            yeni_rapor = {
                "tarih": datetime.now(timezone.utc).isoformat(),
                "sembol": alarm_bilgisi.get("symbol", "UNKNOWN"),
                "sinyal_turu": alarm_bilgisi.get("signal_type", "UNKNOWN"),
                "giris_fiyati": alarm_bilgisi.get("entry_price", 0),
                "hedefler": alarm_bilgisi.get("targets", []),
                "hedef_vuruslari": alarm_bilgisi.get("targets_hit", [False] * 5),
                "stop_loss": alarm_bilgisi.get("stop_loss", 0),
                "stop_loss_vuruldu_mu": alarm_bilgisi.get("stop_loss_hit", False),
                "kar_zararlari": self._hesapla_kar_zarar(alarm_bilgisi),
                "aktif_mi": alarm_bilgisi.get("active", False),
                "pattern_skoru": alarm_bilgisi.get("pattern_score", 0),
                "sinyal_gucü": alarm_bilgisi.get("signal_strength", 0),
                "analiz_detaylari": alarm_bilgisi.get("chart_analysis", {}),
            }

            mevcut_raporlar.append(yeni_rapor)
            cleaned_data = self._clean_data_for_json(mevcut_raporlar)

            with open(weekly_file, "w", encoding="utf-8") as f:
                json.dump(cleaned_data, f, ensure_ascii=False, indent=2)

            return True
        except OSError as e:
            logging.error(f"❌ Alarm raporu kaydetme hatası: {e}")
            return False

    @staticmethod
    def _hesapla_kar_zarar(alarm_bilgisi: Dict[str, Any]) -> Dict[str, Any]:
        try:
            entry_price = float(alarm_bilgisi.get("entry_price", 0) or 0)
            targets = alarm_bilgisi.get("targets", [])
            targets_hit = alarm_bilgisi.get("targets_hit", [False] * 5)
            stop_loss = float(alarm_bilgisi.get("stop_loss", 0) or 0)
            signal_type = str(alarm_bilgisi.get("signal_type", "LONG") or "LONG")

            kar_zararlari: Dict[str, Any] = {
                "hedef_karlari": [],
                "toplam_kar": 0.0,
                "toplam_zarar": 0.0,
                "stop_loss_kar_zarar": 0.0,
            }

            if entry_price == 0:
                return kar_zararlari

            # targets güvenliği
            if not isinstance(targets, list):
                targets = []
            if not isinstance(targets_hit, list):
                targets_hit = [False] * 5

            for i, (target, hit) in enumerate(zip(targets, targets_hit), 1):
                try:
                    target_f = float(target or 0)
                except (TypeError, ValueError):
                    continue

                if hit and target_f > 0:
                    if signal_type.upper() == "LONG":
                        kar = abs((target_f - entry_price) / entry_price * 100)
                    else:
                        kar = abs((entry_price - target_f) / entry_price * 100)

                    kar_zararlari["hedef_karlari"].append(
                        {"hedef_no": i, "hedef_fiyati": target_f, "kar_yuzde": kar}
                    )
                    kar_zararlari["toplam_kar"] += kar

            if bool(alarm_bilgisi.get("stop_loss_hit", False)) and stop_loss > 0:
                if signal_type.upper() == "LONG":
                    zarar = abs((stop_loss - entry_price) / entry_price * 100)
                else:
                    zarar = abs((entry_price - stop_loss) / entry_price * 100)

                kar_zararlari["toplam_zarar"] = zarar
                kar_zararlari["stop_loss_kar_zarar"] = -zarar

            return kar_zararlari

        except (TypeError, ValueError) as e:
            logging.error(f"❌ Kar/zarar hesaplama hatası: {e}")
            return {
                "hedef_karlari": [],
                "toplam_kar": 0.0,
                "toplam_zarar": 0.0,
                "stop_loss_kar_zarar": 0.0,
            }

    def get_aylik_raporlar(self, yil: Optional[int] = None, ay: Optional[int] = None) -> List[Dict]:
        try:
            if yil is None:
                yil = datetime.now(timezone.utc).year
            if ay is None:
                ay = datetime.now(timezone.utc).month

            klasor = os.path.join(self.base_path, f"{yil}-{ay:02d}")
            tum_raporlar: List[Dict] = []
            if os.path.exists(klasor):
                for dosya in os.listdir(klasor):
                    if dosya.endswith("_haftalik_rapor.json"):
                        data = self._safe_load_json(os.path.join(klasor, dosya))
                        tum_raporlar.extend(data)
            return tum_raporlar
        except OSError as e:
            logging.error(f"❌ Aylık rapor alma hatası: {e}")
            return []

    def get_haftalik_raporlar(
        self,
        yil: Optional[int] = None,
        ay: Optional[int] = None,
        hafta: Optional[int] = None,
    ) -> List[Dict]:
        try:
            now = datetime.now(timezone.utc)
            if yil is None:
                yil = now.year
            if ay is None:
                ay = now.month

            if hafta is None:
                hafta_baslangic = now - timedelta(days=now.weekday())
                hafta_dosya_adi = hafta_baslangic.strftime("%Y-%m-%d")
            else:
                hafta_baslangic = datetime(yil, ay, 1, tzinfo=timezone.utc) + timedelta(weeks=hafta - 1)
                hafta_dosya_adi = hafta_baslangic.strftime("%Y-%m-%d")

            dosya_yolu = os.path.join(
                self.base_path,
                f"{yil}-{ay:02d}",
                f"{hafta_dosya_adi}_haftalik_rapor.json",
            )
            return self._safe_load_json(dosya_yolu)
        except (OSError, ValueError) as e:
            logging.error(f"❌ Haftalık rapor alma hatası: {e}")
            return []
