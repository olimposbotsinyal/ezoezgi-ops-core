# strategies/alarm_system/persistence.py

import os, json, asyncio
import logging
from datetime import datetime, timezone, timedelta
import copy

logger = logging.getLogger(__name__)

ANALYTICS_DIR = os.path.join(os.path.dirname(__file__), "analytics")
ACTIVE_ALARMS_FILE = os.path.join(ANALYTICS_DIR, "active_alarms_state.json")
SCHEDULER_FILE = os.path.join(ANALYTICS_DIR, "scan_scheduler_state.json")
_ALARM_ID_LOCK = asyncio.Lock()
_SIGNAL_ID_LOCK = asyncio.Lock()

def _json_default(o):
    try:
        import numpy as np
        if isinstance(o, (np.integer, np.floating)):
            return o.item()
    except Exception:
        pass

    try:
        import pandas as pd
        if isinstance(o, pd.Timestamp):
            return o.to_pydatetime().isoformat()
    except Exception:
        pass

    if isinstance(o, datetime):
        return o.isoformat()

    if isinstance(o, (bytes, bytearray)):
        return f"<bytes:{len(o)}>"

    if isinstance(o, (set, tuple)):
        return list(o)

    return str(o)


def _to_int_id(x):
    """
    Telegram ID'leri için güvenli int dönüşümü.
    Kabul: int, "123", 123.0
    Red: None, "abc", 123.4, NaN, inf
    """
    if x is None:
        return None

    # bool int'in subclass'ıdır; yanlışlıkla True/False gelirse istemeyiz
    if isinstance(x, bool):
        return None

    # zaten int
    if isinstance(x, int):
        return x

    # float -> sadece .0 ise kabul
    if isinstance(x, float):
        if x != x:  # NaN
            return None
        if x in (float("inf"), float("-inf")):
            return None
        if x.is_integer():
            return int(x)
        return None

    # string
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return None
        # "123.0" gibi gelirse:
        try:
            f = float(s)
            if f != f or f in (float("inf"), float("-inf")):
                return None
            if f.is_integer():
                return int(f)
            return None
        except Exception:
            return None

    # diğer tipler (np.int64 vs.) -> int denenebilir
    try:
        return int(x)
    except Exception:
        return None


def _coerce_message_refs(rec: dict) -> None:
    mm = rec.get("main_messages")
    if isinstance(mm, list):
        out = []
        for item in mm:
            if not isinstance(item, dict):
                continue
            ch = _to_int_id(item.get("channel_id"))
            mid = _to_int_id(item.get("message_id"))
            if ch is None or mid is None:
                continue
            out.append({"channel_id": ch, "message_id": mid})
        rec["main_messages"] = out

    msg_ids = rec.get("message_ids")
    if isinstance(msg_ids, list):
        out = []
        for item in msg_ids:
            if not isinstance(item, dict):
                continue
            ch = _to_int_id(item.get("chat_id"))
            mid = _to_int_id(item.get("message_id"))
            if ch is None or mid is None:
                continue
            out.append({"chat_id": ch, "message_id": mid})
        rec["message_ids"] = out


def _atomic_write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=_json_default)
    os.replace(tmp, path)


async def next_alarm_id(counter_path: str = os.path.join("alarm_raporlari", "alarm_counter.json")) -> str:
    """
    Benzersiz alarm id üretir: ALM-YYYYMMDD-XXX
    Counter tek dosyada tutulur ve atomik yazılır.
    """
    async with _ALARM_ID_LOCK:
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        state = {"date": today, "current_id": 0}

        if os.path.exists(counter_path):
            try:
                with open(counter_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f) or {}
                # eski format: {"current_id": 341}
                if "date" not in loaded:
                    loaded["date"] = today
                state.update(loaded)
            except Exception:
                pass

        if state.get("date") != today:
            state = {"date": today, "current_id": 0}

        state["current_id"] = int(state.get("current_id") or 0) + 1
        _atomic_write_json(counter_path, state)

        return f"ALM-{today}-{state['current_id']:03d}"


async def next_signal_id(alarm_id: str, seq_path: str = os.path.join("alarm_raporlari", "signal_seq_by_alarm.json")) -> str:
    """
    Alarm bazlı benzersiz signal id: SIG-{alarm_id}-{NN}
    """
    async with _SIGNAL_ID_LOCK:
        data = {}
        if os.path.exists(seq_path):
            try:
                with open(seq_path, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception:
                data = {}

        key = str(alarm_id)
        n = int(data.get(key) or 0) + 1
        data[key] = n
        _atomic_write_json(seq_path, data)

        return f"SIG-{alarm_id}-{n:02d}"


def _safe_json_load(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        if not raw:
            return default
        return json.loads(raw)
    except Exception as e:
        logger.error(f"[JSON_LOAD_ERR] path={path} err={e}")
        try:
            bak = path + ".corrupt.bak"
            with open(bak, "w", encoding="utf-8") as bf:
                bf.write(raw if isinstance(raw, str) else "")
            logger.warning(f"[JSON_BACKUP] path={path} -> {bak}")
        except Exception:
            pass
        return default


def _load_list(path: str) -> list:
    data = _safe_json_load(path, default=[])
    return data if isinstance(data, list) else []


def _load_dict(path: str) -> dict:
    data = _safe_json_load(path, default={})
    return data if isinstance(data, dict) else {}


def _save_list(path: str, data: list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _save_dict(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_active_alarms() -> list:
    return _load_list(ACTIVE_ALARMS_FILE)


def save_active_alarms(alarms: list) -> None:
    _save_list(ACTIVE_ALARMS_FILE, alarms)


def load_scheduler_state() -> dict:
    return _load_dict(SCHEDULER_FILE)


def save_scheduler_state(state: dict) -> None:
    _save_dict(SCHEDULER_FILE, state)


def save_active_alarms_from_cls(cls) -> None:
    try:
        alarms = []
        for a in getattr(cls, "active_symbols", []) or []:
            if isinstance(a, dict):
                alarms.append(a)
        save_active_alarms(alarms)
    except Exception as e:
        logger.error(f"[SAVE_ACTIVE_ALARMS_ERR] {e}", exc_info=True)


def load_active_alarms_into_cls(cls) -> None:
    try:
        raw = load_active_alarms()
        cls.active_symbols = raw if isinstance(raw, list) else []
    except Exception as e:
        logger.error(f"[LOAD_ACTIVE_ALARMS_ERR] {e}", exc_info=True)
        cls.active_symbols = []


# save_active_signals
def save_active_signals(cls, force: bool = False):
    try:
        # Throttle (2 sn içinde tekrar kaydetme)
        now = datetime.now(timezone.utc)
        if not force and getattr(cls, "_last_active_save", None) and (now - cls._last_active_save).total_seconds() < 2:
            return
        cls._last_active_save = now

        # Dizini oluştur
        os.makedirs(os.path.dirname(cls._active_signals_file), exist_ok=True)

        data = []
        for s in getattr(cls, "active_signals", []):
            rec = s.copy()

            # Ephemeral: chart_buf_raw (bytes) JSON a yazılmamalı
            if 'chart_buf_raw' in rec:
                rec.pop('chart_buf_raw', None)

            # Datetime alanlarını ISO formatına çevir
            for date_field in ['signal_time', 'closed_time', 'stop_time']:
                if isinstance(rec.get(date_field), datetime):
                    rec[date_field] = rec[date_field].isoformat()

            # targets_hit_times için ISO format
            if isinstance(rec.get('targets_hit_times'), list):
                rec['targets_hit_times'] = [
                    (t.isoformat() if isinstance(t, datetime) else t)
                    for t in rec['targets_hit_times']
                ]
            _coerce_message_refs(rec)
            data.append(rec)

        # Geçici dosyaya yaz
        tmp = cls._active_signals_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=_json_default)
            f.flush()
            os.fsync(f.fileno())
        # Atomik dosya değişimi
        os.replace(tmp, cls._active_signals_file)
    except Exception as e:
        logging.exception(
            f"[SAVE_ACTIVE_SIGNALS_FATAL] file={getattr(cls, '_active_signals_file', None)} "
            f"count={len(getattr(cls, 'active_signals', []) or [])} err={e}"
        )


# load_active_signals
def load_active_signals(cls):
    try:
        # Dizini oluştur
        os.makedirs(os.path.dirname(cls._active_signals_file), exist_ok=True)

        # Dosya yoksa işlemi sonlandır
        if not os.path.exists(cls._active_signals_file):
            logging.info("🔄 Yüklenecek aktif sinyal dosyası yok (ilk çalıştırma).")
            return

        # Dosyayı güvenli bir şekilde oku
        try:
            with open(cls._active_signals_file, "r", encoding="utf-8") as f:
                raw_data = f.read().strip()  # Baştan ve sondan boşlukları temizle

                # Dosya boş mu kontrol et
                if not raw_data:
                    logging.warning("⚠️ Aktif sinyal dosyası boş!")
                    return

                # JSON parsing için detaylı hata yakalama
                try:
                    raw = json.loads(raw_data)
                except json.JSONDecodeError as json_err:
                    logging.error(f"❌ JSON Parsing Hatası: {json_err}")
                    logging.error(f"❌ Hatalı JSON içeriği:\n{raw_data}")

                    # Dosyayı yedekle
                    backup_file = cls._active_signals_file + ".bak"
                    with open(backup_file, "w", encoding="utf-8") as backup:
                        backup.write(raw_data)

                    logging.warning(f"⚠️ Bozuk dosya {backup_file} olarak yedeklendi")
                    return

                # Liste kontrolü
                if not isinstance(raw, list):
                    logging.error("❌ JSON verisi liste formatında değil")
                    return

                restored = []
                for r in raw:
                    try:
                        # Sinyalleri normalize et
                        if hasattr(cls, "normalize_signal_dict") and callable(cls.normalize_signal_dict):
                            cls.normalize_signal_dict(r)

                        # Tarih alanlarını güvenli bir şekilde parse et
                        for date_field in ['signal_time', 'closed_time', 'stop_time']:
                            if date_field in r and isinstance(r[date_field], str):
                                try:
                                    r[date_field] = cls._ensure_aware(r[date_field])
                                except Exception as e:
                                    logging.warning(f"{date_field} parse hatası: {e}")
                                    r[date_field] = datetime.now(timezone.utc) if date_field == 'signal_time' else None

                        # targets_hit_times parse
                        if 'targets_hit_times' in r:
                            parsed_times = []
                            for t in r.get('targets_hit_times', []):
                                if isinstance(t, str):
                                    try:
                                        parsed_times.append(cls._ensure_aware(t))
                                    except Exception as e:
                                        logging.warning(f"targets_hit_times parse hatası: {e}")
                                        parsed_times.append(None)
                                elif isinstance(t, datetime):
                                    parsed_times.append(t)
                                else:
                                    parsed_times.append(None)
                            r['targets_hit_times'] = parsed_times
                        else:
                            r['targets_hit_times'] = [None] * len(r.get('targets', []))

                        if 'main_messages' not in r or not isinstance(r.get('main_messages'), list):
                            r['main_messages'] = []

                        restored.append(r)

                    except Exception as signal_error:
                        logging.error(f"Sinyal işleme hatası: {signal_error}")
                        continue

                # Aktif sinyalleri güncelle
                cls.active_signals = restored
                logging.info(f"🔄 {len(restored)} aktif sinyal diskteki kayıttan YÜKLENDİ")

        except IOError as io_err:
            logging.error(f"❌ Dosya okuma hatası: {io_err}")
        except Exception as e:
            logging.error(f"Active signals yükleme genel hatası: {e}", exc_info=True)

    except Exception as main_err:
        logging.error(f"❌ load_active_signals ana hatası: {main_err}", exc_info=True)


# _append_closed_signal
def append_closed_signal(cls, signal: dict):
    """
    Kapanan sinyali closed_signals_state.json dosyasına ekler (atomik).
    STOP veya TARGET_FINAL tekrar finalize edilirse replace (aynı signal_id).
    """
    try:
        os.makedirs(os.path.dirname(cls._closed_signals_file), exist_ok=True)
        path = cls._closed_signals_file

        # Kayıt kopyası
        rec = signal.copy()

        # Datetime alanları ISO
        for fld in ['signal_time', 'closed_time', 'stop_time']:
            if isinstance(rec.get(fld), datetime):
                rec[fld] = rec[fld].isoformat()

        # targets_hit_times normalizasyon
        if isinstance(rec.get('targets_hit_times'), list):
            norm = []
            for t in rec['targets_hit_times']:
                if isinstance(t, datetime):
                    norm.append(t.isoformat())
                else:
                    norm.append(t)
            rec['targets_hit_times'] = norm

        # closed_time yoksa ekle (UTC)
        if not rec.get('closed_time'):
            rec['closed_time'] = datetime.now(timezone.utc).isoformat()

        # Açılış anı meta'sını koru
        rec["meta_at_open"] = copy.deepcopy(signal.get("meta", {}) or {})

        # --- ENRICHMENT: PnL audit fields ---
        tp_scheme = _get_tp_scheme_from_signal_or_config(signal)

        # execution model
        rec["execution_model"] = rec.get("execution_model") or "SIM_PARTIALS"

        # tp_scheme kaydı
        rec["tp_scheme"] = tp_scheme

        # pnl calc sürümü
        rec["pnl_calc_version"] = rec.get("pnl_calc_version") or "v1"

        # breakdown gross (efektif stop önceliği ile)
        breakdown = _calc_close_breakdown(signal, tp_scheme)
        if breakdown:
            rec["close_breakdown"] = breakdown
            rec["realized_gross_pct"] = breakdown.get("gross_pct")
        # ✅ DB leverage + lev alanları
        try:
            meta = rec.get("meta") if isinstance(rec.get("meta"), dict) else {}
            uid = rec.get("user_id") or (meta.get("user_id") if isinstance(meta, dict) else None)
            ex = (meta.get("exchange") if isinstance(meta, dict) else None) or "mexc"
            from data.olimpos_data import get_user_settings
            u = get_user_settings(int(uid), str(ex).lower().strip()) if uid else None
            lev = float((u or {}).get("leverage") or 1.0)
        except Exception:
            lev = 1.0

        rec["leverage_used"] = float(max(1.0, lev))

        try:
            if rec.get("realized_effective_pct") is not None:
                rec["realized_effective_lev"] = float(rec["realized_effective_pct"]) * rec["leverage_used"]
        except Exception:
            pass
        try:
            if rec.get("realized_gross_pct") is not None:
                rec["realized_gross_lev"] = float(rec["realized_gross_pct"]) * rec["leverage_used"]
        except Exception:
            pass

        try:
            if rec.get("realized_effective_pct") is not None:
                rec["realized_effective_lev"] = float(rec["realized_effective_pct"]) * float(lev)
        except Exception:
            pass

        # (opsiyonel) geriye uyumluluk: realized_net_pct'yi effective_lev'e eşitle
        # Böylece analytics/dash anında düzelir.
        try:
            if rec.get("realized_effective_lev") is not None:
                rec["realized_net_pct"] = float(rec["realized_effective_lev"])
        except Exception:
            pass

        # fees model (şimdilik 0; sonra ConfigService’den bağlanabilir)
        rec["fees_model"] = rec.get("fees_model") or {"fee_rate_bps": 0, "slippage_bps": 0}
        # Eğer realized_fees_pct hesaplanmadıysa 0.0
        if rec.get("realized_fees_pct") is None:
            rec["realized_fees_pct"] = 0.0

        # exit_subtype sınıflandırma (timeline yoksa heuristik)
        rec["exit_subtype"] = rec.get("exit_subtype") or _classify_exit_subtype(rec)

        # ✅ realized_effective_pct: autotune'un tek referansı (DOSYAYA YAZMADAN ÖNCE)
        gross = None
        try:
            bd = rec.get("close_breakdown") or {}
            if isinstance(bd, dict) and bd.get("gross_pct") is not None:
                gross = float(bd.get("gross_pct"))
            elif rec.get("realized_gross_pct") is not None:
                gross = float(rec.get("realized_gross_pct"))
        except Exception:
            gross = None

        fees = 0.0
        try:
            fees = float(rec.get("realized_fees_pct") or 0.0)
        except Exception:
            fees = 0.0

        if gross is not None:
            rec["realized_effective_pct"] = gross - fees
        else:
            # fallback
            rec["realized_effective_pct"] = float(rec.get("realized_net_pct") or 0.0)

        # Eski kayıtları oku
        existing = []
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        existing = json.loads(content)
                    else:
                        existing = []
                if not isinstance(existing, list):
                    existing = []
            except Exception as e:
                logging.error(f"[CLOSED_READ_ERR] {e}")
                existing = []

        # Replace mantığı (final tiplerde aynı signal_id tekrar gelirse)
        final_types = {'STOP', 'TARGET_FINAL'}
        if rec.get('exit_type') in final_types:
            replaced = False
            for i, old in enumerate(existing):
                if old.get('signal_id') == rec.get('signal_id') and old.get('exit_type') in final_types:
                    existing[i] = rec
                    replaced = True
                    break
            if not replaced:
                existing.append(rec)
        else:
            existing.append(rec)

        # Limit (gerekiyorsa)  (mevcut davranış korunur)
        if len(existing) > 1500:
            existing = existing[-1000:]

        # Yaz - atomik
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2, default=_json_default)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

        # PNL_MISMATCH kontrolü (enrichment SONRASI)
        try:
            net = rec.get("realized_net_pct")
            if net is not None:
                net = float(net)
                gross2 = rec.get("realized_gross_pct")
                if gross2 is not None:
                    gross2 = float(gross2)
                    diff = abs(net - gross2)
                    if diff > 0.5:  # eşik ileride ayarlanır
                        logging.warning(
                            f"[PNL_MISMATCH] sig={rec.get('signal_id')} net={net:.4f} gross={gross2:.4f} "
                            f"exit={rec.get('exit_type')} sym={rec.get('symbol')}"
                        )
        except Exception:
            pass

        # Doğrulama
        if not os.path.exists(path):
            logging.error("[CLOSED_VERIFY_FAIL] closed_signals_state.json oluşmadı!")
        else:
            logging.info(
                f"[CLOSED_APPEND_OK] total={len(existing)} "
                f"last={rec.get('signal_id')} exit={rec.get('exit_type')}"
            )

    except Exception as e:
        logging.error(f"[CLOSED_APPEND_FATAL] {e}")


# sync_id_counters
def sync_id_counters(cls):
    """
    Mevcut aktif + kapalı kayıtlardan bugünün en büyük alarm_id ve
    her alarm için en büyük signal sıra numarasını senkronize eder.
    (Restart sonrası duplicate ID üretimini engeller.)
    """
    try:
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        max_alarm_seq = 0

        def _extract_alarm_seq(alarm_id: str):
            # Format: ALM-YYYYMMDD-###
            try:
                parts = alarm_id.split('-')
                if len(parts) == 3 and parts[0] == 'ALM' and parts[1].isdigit() and parts[2].isdigit():
                    if parts[1] == today:
                        return int(parts[2])
            except Exception as error:
                logging.error(f"Hata: {error}")
                pass
            return None

        # 1) Aktif sinyallerden alarm id topla
        alarm_ids = set()
        for s in getattr(cls, 'active_signals', []):
            aid = s.get('alarm_id')
            if isinstance(aid, str):
                alarm_ids.add(aid)

        # 2) Kapalı sinyaller
        closed_path = cls._closed_signals_file
        if os.path.exists(closed_path):
            try:
                with open(closed_path, "r", encoding="utf-8") as f:
                    arr = json.load(f)
                for r in arr:
                    aid = r.get('alarm_id')
                    if isinstance(aid, str):
                        alarm_ids.add(aid)
            except Exception as e:
                logging.error(f"Hata: {e}")
                pass

        # 3) Aktif alarm listesi (active_symbols) - (her ihtimale)
        for a in getattr(cls, 'active_symbols', []):
            aid = a.get('alarm_id')
            if isinstance(aid, str):
                alarm_ids.add(aid)

        # Alarm seq max
        for aid in alarm_ids:
            seq = _extract_alarm_seq(aid)
            if seq and seq > max_alarm_seq:
                max_alarm_seq = seq

        # 4) Signal per alarm en büyük index (SIG-ALM-YYYYMMDD-###-NN)
        signal_counter_map = {}

        def _extract_signal_seq(sig_id: str):
            # Format: SIG-ALM-YYYYMMDD-###-NN
            try:
                parts = sig_id.split('-')
                # ['SIG','ALM','YYYYMMDD','###','NN']
                if (len(parts) == 5 and parts[0] == 'SIG' and parts[1] == 'ALM' and parts[2].isdigit()
                        and parts[3].isdigit() and parts[4].isdigit()):
                    if parts[2] == today:
                        alarm_key = f"ALM-{parts[2]}-{parts[3]}"
                        return alarm_key, int(parts[4])
            except Exception as error:
                logging.error(f"Hata: {error}")
                pass
            return None, None

        def _scan_signal_id(sig_id):
            if not sig_id:
                return
            alarm_key, seq_num = _extract_signal_seq(sig_id)
            if alarm_key and seq_num is not None:
                prev = signal_counter_map.get(alarm_key, 0)
                if seq_num > prev:
                    signal_counter_map[alarm_key] = seq_num

        for s in getattr(cls, 'active_signals', []):
            _scan_signal_id(s.get('signal_id'))
        if os.path.exists(closed_path):
            try:
                with open(closed_path, "r", encoding="utf-8") as f:
                    arr = json.load(f)
                for r in arr:
                    _scan_signal_id(r.get('signal_id'))
            except Exception as e:
                logging.error(f"Hata: {e}")
                pass

        # 5) Sayaçları güncelle
        cls.alarm_counter_date = today
        cls.alarm_counter = max_alarm_seq
        if not hasattr(cls, "signal_counters"):
            cls.signal_counters = {}
        cls.signal_counters.update(signal_counter_map)

        logging.info(f"[ID_SYNC] Bugün max alarm seq={max_alarm_seq}, "
                     f"{len(signal_counter_map)} alarm için signal seq senkronize edildi.")
    except Exception as e:
        logging.error(f"ID senkronizasyon hatası: {e}")


# get_closed_signals
def get_closed_signals(cls):
    """Kapalı sinyaller güvenli erişim wrapper’ı"""
    path = cls._closed_signals_file
    if not os.path.exists(path):
        return []  # Dosya yoksa boş liste döndür
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                logging.warning(f"Kapalı sinyal dosyası ({path}) boş.")
                return []
            data = json.loads(content)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, IOError) as e:
        logging.error(f"[CLOSED_SIG_WRAPPER_ERR] Kapalı sinyal dosyası okunamadı veya bozuk: {e}")
        return []  # Hata durumunda her zaman boş liste döndür


# load_recent_closed_signals
def load_recent_closed_signals(cls,  hours=24, anchor_hour=None):
    """
    Son X saat kapanan sinyalleri getirir.
    anchor_hour (0-23) verildiyse pencere 'anchor_hour->anchor_hour' 24s blok olarak alınır.
    Örn: anchor_hour=3 ve şu an 11:20 ise: [bugün 03:00, yarın 03:00) henüz dolmadı → start = bugün 03:00
         anchor_hour=3 ve şu an 01:10 ise:
         bir önceki gün 03:00 → bugün 03:00 aralığı (çünkü henüz anchor'a gelinmedi)
    """
    try:
        path = cls._closed_signals_file
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            arr = json.load(f)
        if not isinstance(arr, list):
            return []
        if os.path.exists(path):
            if os.path.getsize(path) == 0:
                logging.warning("[CLOSED_JSON] Dosya boş, atlanıyor.")
                return []

        now = datetime.now(timezone.utc)

        if anchor_hour is not None:
            # Bugünün anchor'ı
            today_anchor = now.replace(hour=anchor_hour, minute=0, second=0, microsecond=0)
            if now >= today_anchor:
                start = today_anchor
            else:
                start = today_anchor - timedelta(days=1)
            end = start + timedelta(hours=24)
        else:
            end = now
            start = now - timedelta(hours=hours)

        out = []
        for r in arr:
            ct = r.get('closed_time')
            if not ct:
                continue
            try:
                ct_dt = cls._ensure_aware(ct)
                if not ct_dt:
                    continue
            except Exception as e:
                logging.error(f"Hata: {e}")
                continue
            if start <= ct_dt < end:
                out.append(r)
        # En yeni üstte olsun
        out.sort(key=lambda x: x.get('closed_time', ''), reverse=True)

        # PATCH FALLBACK: Anchor penceresi boşsa 24 saat klasik aralığı tekrar dene
        if not out and anchor_hour is not None:
            try:
                start_fb = now - timedelta(hours=24)
                fb = []
                for r in arr:
                    ct2 = r.get('closed_time')
                    if not ct2:
                        continue
                    try:
                        ct_dt2 = cls._ensure_aware(ct2)
                    except Exception as e:
                        logging.error(f"Hata: {e}")
                        continue
                    if start_fb <= ct_dt2 <= now:
                        fb.append(r)
                fb.sort(key=lambda x: x.get('closed_time', ''), reverse=True)
                if fb:
                    logging.info("[CLOSED_FALLBACK] Anchor boştu fallback 24s kullanıldı.")
                    return fb
            except Exception as _fb_e:
                logging.error(f"[CLOSED_FALLBACK_ERR] {_fb_e}")

        return out
    except Exception as e:
        logging.error(f"load_recent_closed_signals hata: {e}")
    return []


def _get_tp_scheme_from_signal_or_config(signal: dict) -> list[float]:
    # kaç TP var? (close_breakdown ile aynı sayıda olmalı)
    targets = signal.get("targets") or []
    max_tps = len(targets) if isinstance(targets, list) else 0

    # 0) ✅ DB (user settings) önceliği
    try:
        meta = signal.get("meta") or {}
        user_id = signal.get("user_id") or meta.get("user_id")
        exchange = meta.get("exchange") or signal.get("exchange") or "mexc"
        fr_db = _get_user_tp_scheme_from_db(user_id, exchange, max_tps=max_tps)
        if isinstance(fr_db, list) and fr_db:
            return [float(x) for x in fr_db]
    except Exception:
        pass

    # 0.5) execution_plan_summary öncelik (sende vardı) ...
    eps = signal.get("execution_plan_summary") or signal.get("execution_plan") or {}
    fractions = None
    if isinstance(eps, dict):
        fractions = eps.get("tp_fractions")
        # execution_plan.tp_structs'tan türetme
        if not fractions and eps.get("tp_structs"):
            fr = []
            for t in eps.get("tp_structs") or []:
                if isinstance(t, dict):
                    f = t.get("close_frac") or t.get("fraction") or t.get("qty_frac") or t.get("percent")
                    if f is not None:
                        try:
                            fv = float(f)
                            if fv > 1.0:
                                fv = fv / 100.0
                            fr.append(fv)
                        except Exception:
                            continue
            if fr:
                s = sum(fr)
                if s > 0:
                    fractions = [x/s for x in fr]
    if isinstance(fractions, list) and fractions:
        return [float(x) for x in fractions]

    # 1) Sinyal üstünden geliyorsa (ideal)
    meta = signal.get("meta") or {}
    scheme = meta.get("tp_scheme") or meta.get("tp_fractions")
    if isinstance(scheme, list) and scheme and all(isinstance(x, (int, float)) for x in scheme):
        s = float(sum(scheme))
        if s > 0:
            return [float(x)/s for x in scheme]

    # 2) ConfigService’den (alarm_strateji.py zaten ConfigService kullanıyor)
    try:
        from config_service import ConfigService
        p = ConfigService.get("TP_SCHEME", None)
        if isinstance(p, list) and p:
            s = float(sum(p))
            if s > 0:
                return [float(x)/s for x in p]
    except Exception:
        pass

    # 3) Fallback: eşit böl
    targets = signal.get("targets") or []
    n = len(targets) if isinstance(targets, list) else 0
    if n > 0:
        return [1.0/n] * n
    return []


def _calc_close_breakdown(signal: dict, tp_scheme: list[float]) -> dict:
    entry = float(signal.get("entry_price") or 0.0)
    # Efektif stop önceliği
    stop_eff = signal.get("stop_price_effective")
    stop = float(stop_eff if stop_eff else (signal.get("stop_loss") or 0.0))
    targets = signal.get("targets") or []
    hits = signal.get("targets_hit") or []
    # Burada side bilgisi sinyal yönünü temsil etmeli; yanlışlıkla signal_type kullanma
    side = (
            signal.get("signal_type")
            or signal.get("direction")
            or signal.get("side")
            or signal.get("position_type")
            or "LONG"
    )
    side = str(side).upper().strip()
    if side not in ("LONG", "SHORT"):
        side = "LONG"

    if entry <= 0 or not isinstance(targets, list) or not isinstance(hits, list):
        return {}

    # normalize lengths
    n = min(len(targets), len(hits), len(tp_scheme))
    targets = targets[:n]
    hits = hits[:n]
    tp_scheme = tp_scheme[:n]

    def pct_move(price: float) -> float:
        try:
            p = float(price)
            if p <= 0 or entry <= 0:
                return 0.0
            if side == "SHORT":
                return (entry - p) / entry * 100.0
            return (p - entry) / entry * 100.0
        except Exception:
            return 0.0
    legs = []
    used_frac = 0.0
    gross = 0.0

    for i in range(n):
        if hits[i]:
            pm = pct_move(targets[i])
            try:
                frac = float(tp_scheme[i])
            except Exception:
                frac = 0.0
            contrib = frac * pm
            used_frac += frac
            gross += contrib
            legs.append({
                "type": f"TP{i+1}",
                "price": float(targets[i]),
                "pct_move": pm,
                "frac": frac,
                "pnl_pct_contrib": contrib
            })

    # stop leg only if stop was hit OR exit_type is STOP
    stop_hit = bool(signal.get("stop_loss_hit")) or (str(signal.get("exit_type") or "").upper() == "STOP")
    if stop_hit:
        remaining = max(0.0, 1.0 - used_frac)
        pm_sl = pct_move(stop) if stop > 0 else 0.0
        contrib_sl = remaining * pm_sl
        gross += contrib_sl
        legs.append({
            "type": "STOP",
            "price": float(stop),
            "pct_move": pm_sl,
            "frac": remaining,
            "pnl_pct_contrib": contrib_sl
        })

    return {"legs": legs, "gross_pct": gross}


def _classify_exit_subtype(signal: dict) -> str:
    exit_type = str(signal.get("exit_type") or "").upper()

    # ✅ Öncelik: effective → gross → net
    pnl = None
    try:
        if signal.get("realized_effective_pct") is not None:
            pnl = float(signal.get("realized_effective_pct"))
        else:
            bd = signal.get("close_breakdown") or {}
            if isinstance(bd, dict) and bd.get("gross_pct") is not None:
                pnl = float(bd.get("gross_pct"))
            elif signal.get("realized_gross_pct") is not None:
                pnl = float(signal.get("realized_gross_pct"))
            elif signal.get("realized_net_pct") is not None:
                pnl = float(signal.get("realized_net_pct"))
    except Exception:
        pnl = None

    hits = signal.get("targets_hit") or []
    any_tp = any(bool(x) for x in hits) if isinstance(hits, list) else False

    if exit_type.startswith("TARGET"):
        return "TARGET_FINAL_FULL"

    if exit_type == "STOP":
        if pnl is not None:
            if pnl > 0:
                return "STOP_TRAIL_PROFIT" if any_tp else "STOP_PROFIT"
            if -0.02 < pnl < 0.02:
                return "STOP_BREAK_EVEN"
        return "STOP_AFTER_TP" if any_tp else "STOP_LOSS"

    return "OTHER"

def _parse_pct(x) -> float:
    try:
        if x is None:
            return 0.0
        # "20" / "20.0" / 20
        v = float(str(x).strip().replace("%", "").replace(",", "."))
        if v != v or v in (float("inf"), float("-inf")):
            return 0.0
        return max(0.0, v)
    except Exception:
        return 0.0


def _get_user_tp_scheme_from_db(user_id: int | None, exchange: str | None, max_tps: int) -> list[float] | None:
    """
    DB ayarlar.tp1..tp10 => yüzdeler (20,20,...)
    Çıkış: fractions list (0..1) ve toplamı 1.
    """
    if not user_id or not exchange or max_tps <= 0:
        return None

    try:
        from data.olimpos_data import get_user_settings
        s = get_user_settings(int(user_id), str(exchange).lower().strip())
    except Exception:
        s = None

    if not isinstance(s, dict):
        return None

    pcts: list[float] = []
    for i in range(1, min(10, int(max_tps)) + 1):
        pcts.append(_parse_pct(s.get(f"tp{i}")))

    if not pcts:
        return None

    total = sum(pcts)
    if total <= 0:
        return None

    # normalize -> fractions
    fr = [p / total for p in pcts]

    # Eğer max_tps 5 ama db 10 verdiyse: sadece gereken kadar kırp
    fr = fr[:max_tps]

    # yeniden normalize (kırptıysak)
    s2 = sum(fr)
    if s2 > 0:
        fr = [x / s2 for x in fr]

    return fr


def _get_user_leverage_from_db(user_id: int | None, exchange: str | None) -> float:
    if not user_id or not exchange:
        return 1.0
    try:
        from data.olimpos_data import get_user_settings
        s = get_user_settings(int(user_id), str(exchange).lower().strip())
        if isinstance(s, dict) and s.get("leverage") is not None:
            lev = float(str(s.get("leverage")).strip().replace(",", "."))
            if lev != lev or lev in (float("inf"), float("-inf")):
                return 1.0
            return max(1.0, lev)
    except Exception:
        pass
    return 1.0
