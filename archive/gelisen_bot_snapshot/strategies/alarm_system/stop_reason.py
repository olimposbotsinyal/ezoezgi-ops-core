# strategies/alarm_system/stop_reason.py
from __future__ import annotations
from typing import Any, Dict, Tuple


# Etiketler (alarm_strateji tarafında rapor ve tuner için standart)
STOP_REASON_LABELS = [
    "trend_reversal",      # trend tersine dönmesi / karşı trend
    "volatility_spike",    # ani volatilite (sert dalga)
    "range_fakeout",       # yatay piyasada sahte kırılım
    "entry_too_tight",     # stop mesafesi çok dar (kolay stop)
    "other"                # diğer / sınıflandırılamadı
]


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return float(default)
        return float(x)
    except Exception:
        return float(default)


def _safe_str(x: Any, default: str = "") -> str:
    try:
        if x is None:
            return default
        return str(x)
    except Exception:
        return default


def _pct(a: float, b: float) -> float:
    """
    a/b oranını yüzdeye çevirir. (b=0 ise 0 döner)
    """
    try:
        if b == 0:
            return 0.0
        return (a / b) * 100.0
    except Exception:
        return 0.0


def _get(meta: Dict[str, Any], *keys: str, default=None):
    """
    meta içinde olası birden fazla anahtardan ilk bulunanı döndürür.
    """
    if not isinstance(meta, dict):
        return default
    for k in keys:
        if k in meta and meta.get(k) is not None:
            return meta.get(k)
    return default


def classify_stop_reason(
    symbol: str,
    timeframe: str,
    direction: str,
    entry_price: float,
    stop_loss: float,
    market_regime: str,
    meta_at_open: Dict[str, Any] | None = None,
    meta_at_close: Dict[str, Any] | None = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    STOP nedenini kural tabanlı (şeffaf) şekilde sınıflandırır.

    Bu fonksiyon KESİNLİKLE:
    - Ağ çağrısı yapmaz (OHLCV çekmez)
    - async değildir (log_trade_outcome ile uyumlu)
    - "kanıt" döndürür: kararın neden verildiği anlaşılır

    Parametre Açıklamaları (çok basit):
    - entry_price: işlemin açıldığı fiyat
    - stop_loss: işlemin stop fiyatı
    - market_regime: botun Global 1D rejim kararı ("Yükseliş"/"Düşüş"/"Yatay")
    - meta_at_open: sinyal açılırken kaydedilen meta (rejim trendleri, skorlar vb.)
    - meta_at_close: sinyal kapanırken sinyalin meta'sı (varsa)

    Dönen:
    - label: STOP nedeni etiketi (trend_reversal / volatility_spike / range_fakeout / entry_too_tight / other)
    - evidence: kullanıcıya gösterilebilir kanıt sözlüğü
    """
    meta_at_open = meta_at_open if isinstance(meta_at_open, dict) else {}
    meta_at_close = meta_at_close if isinstance(meta_at_close, dict) else {}

    sym = _safe_str(symbol, "?")
    tf = _safe_str(timeframe, "?")
    side = _safe_str(direction, "LONG").upper()
    mr = _safe_str(market_regime, "Yatay")

    entry = _safe_float(entry_price, 0.0)
    sl = _safe_float(stop_loss, 0.0)

    # --- Temel metrikler ---
    sl_distance = abs(entry - sl) if entry > 0 and sl > 0 else 0.0
    sl_pct = _pct(sl_distance, entry) if entry > 0 else 0.0

    # Trend meta (monitor_symbols içinde eklediğin değerler)
    global_regime_1d = _safe_str(_get(meta_at_open, "global_regime_1d", default=mr), mr)  # 'Yükseliş'/'Düşüş'/'Yatay'
    btc_tf_trend = _safe_str(_get(meta_at_open, "global_btc_trend_tf", default="NEUTRAL"), "NEUTRAL").upper()  # UP/DOWN/NEUTRAL
    coin_1d_trend = _safe_str(_get(meta_at_open, "local_coin_trend_1d", default="NEUTRAL"), "NEUTRAL").upper()
    coin_tf_trend = _safe_str(_get(meta_at_open, "local_coin_trend_tf", default="NEUTRAL"), "NEUTRAL").upper()

    # Basit sinyal kalitesi (varsa)
    ai_conf = _safe_float(_get(meta_at_open, "ai_confidence", "confidence_index", default=0.0), 0.0)
    tech_score = _safe_float(_get(meta_at_open, "technical_score", "score", default=0.0), 0.0)

    # Volatilite / sıkışma benzeri meta alanları (varsa)
    # Not: Bu alanlar her zaman yok, yoksa 0 kabul ediyoruz.
    compression = _safe_float(_get(meta_at_open, "compression", default=0.0), 0.0)
    momentum = _safe_float(_get(meta_at_open, "momentum", default=0.0), 0.0)
    volume_ratio = _safe_float(_get(meta_at_open, "volume_ratio", default=0.0), 0.0)

    # --- Kanıt sözlüğü (çok anlaşılır açıklamalarla) ---
    evidence: Dict[str, Any] = {
        "sembol": sym,
        "zaman_dilimi": tf,
        "yon": "LONG (yukarı)" if side == "LONG" else "SHORT (aşağı)",
        "global_rejim_1g": global_regime_1d,
        "btc_kisa_trend": btc_tf_trend,
        "coin_1g_trend": coin_1d_trend,
        "coin_kisa_trend": coin_tf_trend,
        "stop_mesafesi_yuzde": round(sl_pct, 3),
        "ai_guven": round(ai_conf, 3),
        "teknik_skor": round(tech_score, 3),
        "hacim_orani": round(volume_ratio, 3),
        "momentum": round(momentum, 3),
        "sikisma": round(compression, 3),
        "kural_aciklamalari": [],   # hangi kural neden çalıştı
        "puanlama": {},             # etiket puanları (şeffaf)
    }

    # --- Puan bazlı karar (şeffaf) ---
    scores = {
        "trend_reversal": 0,
        "volatility_spike": 0,
        "range_fakeout": 0,
        "entry_too_tight": 0,
        "other": 0,
    }

    # 1) Entry-too-tight (stop çok dar)
    # Basit yorum: stop mesafesi %0.6 altı ise çok dar sayabiliriz (özellikle 15m/30m gibi kısa TF)
    # TF’ye göre daha akıllı eşik:
    tf_lower = tf.lower()
    if tf_lower in ("1m", "5m"):
        tight_thr = 0.45
    elif tf_lower in ("15m",):
        tight_thr = 0.60
    elif tf_lower in ("30m",):
        tight_thr = 0.75
    elif tf_lower in ("1h",):
        tight_thr = 0.90
    else:
        tight_thr = 1.10

    if sl_pct > 0 and sl_pct <= tight_thr:
        scores["entry_too_tight"] += 3
        evidence["kural_aciklamalari"].append(
            f"Stop çok dar: stop mesafesi %{sl_pct:.2f} (eşik: %{tight_thr:.2f}). "
            "Bu, küçük bir fiyat dalgasında bile stop olma riskini artırır."
        )

    # 2) Trend reversal / karşı trend
    # LONG iken global 1g düşüş + coin kısa düşüş gibi durumlar
    if side == "LONG":
        if global_regime_1d == "Düşüş":
            scores["trend_reversal"] += 3
            evidence["kural_aciklamalari"].append(
                "Karşı-trend LONG: Global 1g rejim 'Düşüş'. "
                "Genel piyasa aşağı eğilimdeyken LONG denemeleri daha risklidir."
            )
        if btc_tf_trend == "DOWN":
            scores["trend_reversal"] += 1
            evidence["kural_aciklamalari"].append(
                "BTC kısa vade düşüş (DOWN): Kısa vadede piyasa baskısı stop riskini artırır."
            )
        if coin_tf_trend == "DOWN":
            scores["trend_reversal"] += 2
            evidence["kural_aciklamalari"].append(
                "Coin kısa vade düşüş (DOWN): İşlem açıldıktan sonra kısa vadeli trend aşağı."
            )

    if side == "SHORT":
        if global_regime_1d == "Yükseliş":
            scores["trend_reversal"] += 3
            evidence["kural_aciklamalari"].append(
                "Karşı-trend SHORT: Global 1g rejim 'Yükseliş'. "
                "Genel piyasa yukarı eğilimdeyken SHORT denemeleri daha risklidir."
            )
        if btc_tf_trend == "UP":
            scores["trend_reversal"] += 1
            evidence["kural_aciklamalari"].append(
                "BTC kısa vade yükseliş (UP): Kısa vadede yukarı baskı stop riskini artırır."
            )
        if coin_tf_trend == "UP":
            scores["trend_reversal"] += 2
            evidence["kural_aciklamalari"].append(
                "Coin kısa vade yükseliş (UP): İşlem açıldıktan sonra kısa vadeli trend yukarı."
            )

    # 3) Range fakeout (yatay + düşük momentum + düşük hacim/teyit)
    # Burada OHLCV yok; o yüzden meta üzerinden "momentum düşük" ve "global yatay" ve "volume_ratio düşük" gibi
    if global_regime_1d == "Yatay":
        # momentum 0’a yakınsa (RSI 50 civarı gibi düşün)
        if abs(momentum) < 0.15:
            scores["range_fakeout"] += 2
            evidence["kural_aciklamalari"].append(
                "Yatay piyasa + düşük momentum: fiyat güçlü bir itki göstermiyor, sahte kırılım (fakeout) riski artar."
            )
        if volume_ratio > 0 and volume_ratio < 0.9:
            scores["range_fakeout"] += 1
            evidence["kural_aciklamalari"].append(
                "Hacim oranı düşük: son dönem hacim, geçmişe göre zayıf. "
                "Zayıf hacimli kırılımlar sık stop ile sonuçlanabilir."
            )
        if compression > 0.75:
            scores["range_fakeout"] += 1
            evidence["kural_aciklamalari"].append(
                "Sıkışma yüksek: fiyat dar bantta. Dar bant kırılımlarında fakeout riski vardır."
            )

    # 4) Volatility spike (ani sert hareket)
    # Yine OHLCV yok; meta üzerinden 'is_crash' vb. varsa veya stop çok geniş değilken hızlı stop olmuşsa.
    # Biz burada, global_regime_info gibi bir şey meta'da varsa okuyabiliriz.
    # Sende meta'da global_regime (UP/DOWN) var; is_crash parent alarm meta'sında olabilir.
    is_crash = bool(_get(meta_at_open, "is_crash", default=False))
    if is_crash:
        scores["volatility_spike"] += 3
        evidence["kural_aciklamalari"].append(
            "BTC 'crash' bayrağı açık: piyasa çok sert düşüşte. Bu, ani volatilite (volatility spike) demektir."
        )

    # Stop çok dar + düşük güven + kısa TF => spike ile stop ihtimali
    if tf_lower in ("1m", "5m", "15m") and sl_pct > 0 and sl_pct <= tight_thr and ai_conf > 0 and ai_conf < 0.72:
        scores["volatility_spike"] += 1
        evidence["kural_aciklamalari"].append(
            "Kısa zaman dilimi + dar stop + düşük AI güven: ani dalgalar stop'a hızlı götürebilir."
        )

    # 5) Eğer hiçbir şey güçlü değilse "other"
    # Ayrıca kalite çok düşükse "other" ağırlığı ver
    if ai_conf > 0 and ai_conf < 0.60 and tech_score > 0 and tech_score < 55:
        scores["other"] += 1
        evidence["kural_aciklamalari"].append(
            "AI güven ve teknik skor düşük: net bir kategoriye girmeyen zayıf sinyal olabilir."
        )

    # Son karar
    evidence["puanlama"] = dict(scores)

    # En yüksek puanı seç
    best_label = max(scores.items(), key=lambda kv: kv[1])[0]
    if scores[best_label] <= 0:
        best_label = "other"

    # Etiket açıklaması (insan dili)
    label_tr = {
        "trend_reversal": "Trend Tersine Dönüş / Karşı-Trend",
        "volatility_spike": "Ani Volatilite (Sert Dalga)",
        "range_fakeout": "Yatay Piyasada Sahte Kırılım (Fakeout)",
        "entry_too_tight": "Stop Çok Dar (Kolay Stop)",
        "other": "Diğer / Belirsiz",
    }.get(best_label, best_label)

    evidence["stop_nedeni_etiketi"] = best_label
    evidence["stop_nedeni_aciklama"] = label_tr

    # Kullanıcı dostu kısa özet
    # (loglarda/raporlarda tek satır hızlı okumalık)
    evidence["kisa_ozet"] = (
        f"{label_tr}. Stop mesafesi %{sl_pct:.2f}. "
        f"Global: {global_regime_1d}, BTC kısa: {btc_tf_trend}, Coin kısa: {coin_tf_trend}."
    )

    return best_label, evidence
