# core/candidate_selector.py dosyamız buradadır

from __future__ import annotations
from dataclasses import dataclass, field
import asyncio
from typing import List, Dict, Any, Optional, Tuple
import logging
import pandas as pd
from strategies.technical_indicators import TechnicalIndicators
import time
from collections import OrderedDict

# TA-Lib Kontrolü
try:
    import talib

    HAS_TALIB = True
except ImportError:
    talib = None
    HAS_TALIB = False
    logging.warning("⚠️ TA-Lib yüklü değil!")


cs_logger = logging.getLogger("candidate_selector")


@dataclass
class Candidate:
    """Aday coin veri sınıfı"""
    symbol: str
    strategy_id: Optional[str] = None
    candidate_score: float = 0.0
    ai_confidence: float = 0.0
    movement_potential: float = 0.0
    volume_ratio: float = 1.0
    compression_ratio: Optional[float] = None
    momentum_tension: Optional[float] = None
    segment_key: str = "default"
    expected_r_pre_signal: float = 0.0
    meta: Dict[str, Any] = field(default_factory=dict)


class CandidateSelector:
    """Strateji için aday coinleri seçen sınıf"""

    def __init__(self, market_data_service, ai_model, logger, params: dict):
        self.md = market_data_service
        self.ai = ai_model
        self.logger = logger
        self.params = params
        self.p_sel = params.get('CANDIDATE_SELECTION', {})
        # LRU cache: key -> (ts, df)
        self._ohlcv_cache: "OrderedDict[Tuple[str, str, int], Tuple[float, pd.DataFrame]]" = OrderedDict()

        # Maks cache boyutu (config’ten)
        self._ohlcv_cache_max_items = int(self.p_sel.get("ohlcv_cache_max_items", 800) or 800)
        # Not: 800 genelde iyi başlangıç. Çok sembol/timeframe kullanıyorsan 1500–3000’e çekebilirsin.
        # İsteğe bağlı: periyodik temizlik
        self._ohlcv_cache_purge_every = int(self.p_sel.get("ohlcv_cache_purge_every", 200) or 200)
        self._ohlcv_cache_get_calls = 0
        # __init__ içinde:
        self._tickers_cache_ts: float = 0.0
        self._tickers_cache_data: Dict[str, Any] = {}
        # TTL'ler (istersen config'e de taşırsın)
        self._tickers_ttl_sec = int(self.p_sel.get("tickers_ttl_sec", 10) or 10)
        self._ohlcv_ttl_sec = int(self.p_sel.get("ohlcv_ttl_sec", 20) or 20)

        if not self.p_sel.get('timeframe_candidate'):
            self.p_sel['timeframe_candidate'] = '15m'

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        """Sembolü normalize et (analiz/filtre amaçlı; RAW fetch sembolüne dokunmaz)."""
        s = str(symbol or "").strip().upper()

        if not s:
            return "UNKNOWN/USDT"

        # Futures RAW: "BTC/USDT:USDT" -> "BTC/USDT"
        if ":" in s:
            s = s.split(":", 1)[0].strip()

        # Bazı kaynaklar "BTC-USDT" veya "BTC_USDT" gibi gelebilir
        if "-" in s and "/" not in s:
            parts = [p for p in s.split("-") if p]
            if len(parts) == 2:
                s = f"{parts[0]}/{parts[1]}"
        if "_" in s and "/" not in s:
            parts = [p for p in s.split("_") if p]
            if len(parts) == 2:
                s = f"{parts[0]}/{parts[1]}"

        # Zaten "BASE/QUOTE" formundaysa bırak
        if "/" in s:
            return s

        # "BTCUSDT" gibi bitişik format
        if s.endswith("USDT") and len(s) > 4:
            base = s[:-4]
            return f"{base}/USDT"

        # Default: quote USDT varsay
        return f"{s}/USDT"

    async def _get_tickers_cached(self) -> Dict[str, Any]:
        now = time.time()
        if self._tickers_cache_data and (now - self._tickers_cache_ts) < self._tickers_ttl_sec:
            return self._tickers_cache_data

        data = await self.md.fetch_tickers_async()
        self._tickers_cache_data = data or {}
        self._tickers_cache_ts = now
        return self._tickers_cache_data

    async def _get_ohlcv_cached(self, raw_symbol: str, timeframe: str, limit: int) -> Optional[pd.DataFrame]:
        now = time.time()
        key = (raw_symbol, timeframe, int(limit))

        self._ohlcv_cache_get_calls += 1
        if self._ohlcv_cache_get_calls % 500 == 0:
            logging.info(f"[OHLCV_CACHE] size={len(self._ohlcv_cache)}/{self._ohlcv_cache_max_items}")

        # (A) Ara sıra TTL-expired temizliği
        if self._ohlcv_cache_purge_every > 0 and (self._ohlcv_cache_get_calls % self._ohlcv_cache_purge_every == 0):
            try:
                expired_keys = []
                for k, (ts, _) in self._ohlcv_cache.items():
                    if (now - ts) >= self._ohlcv_ttl_sec:
                        expired_keys.append(k)
                for k in expired_keys:
                    self._ohlcv_cache.pop(k, None)
            except Exception:
                # Cache temizliği hiçbir zaman analiz akışını bozmasın
                pass

        # (B) Cache hit
        hit = self._ohlcv_cache.get(key)
        if hit is not None:
            ts, df = hit
            if (now - ts) < self._ohlcv_ttl_sec and isinstance(df, pd.DataFrame) and not df.empty:
                # LRU: en son kullanılanı sona taşı
                self._ohlcv_cache.move_to_end(key, last=True)
                return df

            # TTL dolmuş/bozuksa kaldır
            self._ohlcv_cache.pop(key, None)

        # (C) Cache miss -> API fetch
        df = await self.md.fetch_ohlcv_async(raw_symbol, timeframe=timeframe, limit=limit)

        if isinstance(df, pd.DataFrame) and not df.empty:
            # LRU insert
            self._ohlcv_cache[key] = (now, df)
            self._ohlcv_cache.move_to_end(key, last=True)

            # (D) Boyut limiti: en eskiyi at
            while len(self._ohlcv_cache) > self._ohlcv_cache_max_items:
                self._ohlcv_cache.popitem(last=False)

        return df

    async def build_base_universe(self) -> List[str]:
        """Borsadaki uygun pariteleri filtrele (Futures uyumlu, CCXT ham sembol korunur)."""
        try:
            tickers = await self._get_tickers_cached()
            clean_candidates: List[str] = []

            blacklist = [
                'USDC', 'USDP', 'TUSD', 'FDUSD', 'BUSD', 'DAI',
                'EUR', 'GBP', 'TRY', 'PAXG', 'UP', 'DOWN', 'BEAR', 'BULL'
            ]

            logging.info(f"🔍 [SELECTOR] Borsa Ticker Sayısı: {len(tickers)}")

            for sym, t in (tickers or {}).items():
                try:
                    if not isinstance(sym, str):
                        continue

                    raw_sym = sym.strip()
                    norm_sym = self._normalize_symbol(raw_sym)

                    # "BASE/QUOTE" parse
                    if "/" not in norm_sym:
                        continue

                    base_coin, quote_coin = norm_sym.split("/", 1)
                    base_coin = (base_coin or "").strip().upper()
                    quote_coin = (quote_coin or "").strip().upper()

                    # Sadece USDT quote
                    if quote_coin != "USDT":
                        continue

                    if not base_coin:
                        continue

                    # Blacklist
                    if base_coin in blacklist:
                        continue
                    if '3L' in base_coin or '3S' in base_coin:
                        continue

                    # Likidite filtresi (quote volume USDT bazlı)
                    try:
                        qv = float((t or {}).get('quoteVolume') or (t or {}).get('quote_volume') or 0)
                    except Exception:
                        qv = 0.0

                    if qv < 2_000_000:
                        continue

                    # Kritik: RAW sembolü sakla (CCXT fetch key ile birebir)
                    clean_candidates.append(raw_sym)

                except Exception as e:
                    logging.debug(f"[BUILD_UNIVERSE_SYMBOL_ERR] {sym}: {e}")
                    continue

            clean_candidates = list(set(clean_candidates))
            logging.info(f"✅ [SELECTOR] Temiz Coin (RAW symbols): {len(clean_candidates)}")
            return clean_candidates

        except Exception as e:
            logging.error(f"[BUILD_UNIVERSE_ERR] {e}", exc_info=True)
            return []

    @staticmethod
    def _compute_features(df: pd.DataFrame, sym: str) -> Dict[str, Any]:
        """
        ✅ GÜNCELLENMIŞ: TechnicalIndicators modülünü kullan
        """
        try:
            # TechnicalIndicators sınıfını kullan
            indicators = TechnicalIndicators.calculate_all(df, sym)

            logging.info(
                f"[{sym}] Göstergeler Hesaplandı: "
                f"ADX={indicators['adx']:.1f}, "
                f"Trend={indicators['local_trend']}"
            )

            return indicators

        except Exception as e:
            logging.error(f"[COMPUTE_FEATURES_FATAL] {sym}: {e}", exc_info=True)
            return TechnicalIndicators.get_default_indicators()

    @staticmethod
    def _compute_candidate_score(strategy_id: str, meta: Dict[str, Any]) -> float:
        """Strateji tipine göre aday skorunu hesapla"""
        try:
            score = 0.0

            if strategy_id == 'v1':
                # V1: Momentum odaklı
                momentum = float(meta.get('momentum_tension', 0.0) or 0.0)
                if momentum > 5.0:
                    score += 25
                elif momentum > 2.0:
                    score += 20
                elif momentum > 0.5:
                    score += 10

                vol_ratio = float(meta.get('volume_ratio', 1.0) or 1.0)
                if vol_ratio > 1.5:
                    score += 25
                elif vol_ratio > 1.2:
                    score += 20
                elif vol_ratio > 1.0:
                    score += 10

                rsi = float(meta.get('rsi', 50.0) or 50.0)
                if 40 <= rsi <= 70:
                    score += 20
                elif 30 <= rsi <= 80:
                    score += 10

                stoch_k = float(meta.get('stoch_k', 50.0) or 50.0)
                if 20 <= stoch_k <= 80:
                    score += 20

                ai_conf = float(meta.get('ai_confidence', 0.5) or 0.5)
                if ai_conf > 0.7:
                    score += 10
                elif ai_conf > 0.6:
                    score += 5

            elif strategy_id == 'v2':
                # V2: Trend & Sıkışma odaklı
                adx = float(meta.get('adx', 0.0) or 0.0)
                if adx > 30:
                    score += 25
                elif adx > 25:
                    score += 20
                elif adx > 20:
                    score += 10

                # ✅ SIKISMA MANTIĞI (bb_width düşükse daha iyi)
                # Not: bb_width gerçekten "band width" ise sıkışma = düşük değer.
                # Aşırı düşük (neredeyse 0) veri hatası da olabilir, o yüzden 0'a yakınsa puanı sınırlıyoruz.
                bb_width = float(meta.get('bb_width', 0.0) or 0.0)

                if bb_width <= 0:
                    # veri yok / hesaplanmadı
                    score += 0
                elif bb_width < 0.003:
                    # aşırı sıkışma (çok nadir) veya ölçüm sapması: puanı tam basmıyoruz
                    score += 12
                elif bb_width <= 0.01:
                    score += 20
                elif bb_width <= 0.02:
                    score += 15
                elif bb_width <= 0.05:
                    score += 10
                else:
                    score += 0

                rsi = float(meta.get('rsi', 50.0) or 50.0)
                if 40 <= rsi <= 60:
                    score += 20
                elif 35 <= rsi <= 65:
                    score += 10

                stoch_k = float(meta.get('stoch_k', 50.0) or 50.0)
                if 30 <= stoch_k <= 70:
                    score += 20

                ai_conf = float(meta.get('ai_confidence', 0.5) or 0.5)
                if ai_conf > 0.7:
                    score += 15
                elif ai_conf > 0.6:
                    score += 10

            return float(min(100.0, max(0.0, score)))

        except Exception as e:
            logging.error(f"[COMPUTE_SCORE_ERR] {e}", exc_info=True)
            return 0.0

    async def select_candidates_for(self, strategy_id: str) -> List[Candidate]:
        logging.info("[CANDIDATE_SELECTOR_USED] select_candidates_for() CALLED")

        """Belirtilen strateji için aday coinleri seç"""
        try:
            logging.info(f"[CANDIDATE_SELECT_START] strategy={strategy_id}")

            base_universe = await self.build_base_universe()
            if not base_universe:
                logging.warning("[CANDIDATE_SELECT] Temel evren boş")
                return []

            # Tracker (ceza/ödül)
            try:
                from strategies.alarm_system.analytics import SymbolPerformanceTracker
                tracker = SymbolPerformanceTracker()
            except Exception as tr_err:
                tracker = None
                logging.warning(f"[CANDIDATE_SELECT] SymbolPerformanceTracker import/init hata: {tr_err}")

            min_volume_usd = 2_000_000
            candidates_raw: List[Dict[str, Any]] = []

            # Opsiyonel: çok cezalı sembolleri tamamen ele
            min_penalty_factor = float(self.p_sel.get("min_penalty_factor", 0.0) or 0.0)

            try:
                tickers = await self._get_tickers_cached()
            except Exception as e:
                logging.error(f"[CANDIDATE_SELECT_TICKER_ERR] {e}")
                tickers = {}
            try:
                from strategies.alarm_system.analytics import FeatureEdgeCalibrator
                edge_cal = FeatureEdgeCalibrator()
            except Exception as ec_err:
                edge_cal = None
                logging.warning(f"[CANDIDATE_SELECT] FeatureEdgeCalibrator init hata: {ec_err}")

            for sym in base_universe:
                try:
                    ticker = (tickers or {}).get(sym, {})
                    try:
                        qv = float(ticker.get('quoteVolume') or ticker.get('quote_volume') or 0)
                    except Exception:
                        qv = 0.0

                    if qv < min_volume_usd:
                        continue

                    candidates_raw.append({
                        'symbol':sym,
                        'volume_usd':qv,
                        'ticker':ticker
                    })
                except Exception as e:
                    logging.debug(f"[CANDIDATE_SELECT_SYMBOL_ERR] {sym}: {e}")
                    continue

            candidates_raw.sort(key=lambda x:x['volume_usd'], reverse=True)
            candidates_raw = candidates_raw[:100]

            logging.info(f"[CANDIDATE_SELECT] {len(candidates_raw)} aday seçildi (top-100 by volume)")

            sem = asyncio.Semaphore(8)

            async def analyze_candidate(cand_data: Dict[str, Any]) -> Optional[Candidate]:

                try:
                    async with sem:
                        raw_symbol = cand_data['symbol']

                        # OHLCV çek
                        try:
                            ohlcv_df = await self._get_ohlcv_cached(
                                raw_symbol,
                                timeframe=self.p_sel.get('timeframe_candidate', '15m'),
                                limit=300
                            )

                        except Exception as e1:
                            logging.debug(f"[CANDIDATE_OHLCV_ERR] {raw_symbol}: {e1}")
                            return None

                        if ohlcv_df is None:
                            logging.debug(f"[CANDIDATE_OHLCV_EMPTY] {raw_symbol}")
                            return None

                        if isinstance(ohlcv_df, pd.DataFrame):
                            df = ohlcv_df.copy()
                        else:
                            try:
                                df = pd.DataFrame(
                                    ohlcv_df,
                                    columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
                                )
                            except Exception:
                                logging.debug(f"[CANDIDATE_OHLCV_BADTYPE] {raw_symbol} type={type(ohlcv_df)}")
                                return None

                        if df is None or len(df) < 50:
                            logging.debug(f"[CANDIDATE_OHLCV_INSUFFICIENT] {raw_symbol}")
                            return None

                        if 'close' not in df.columns:
                            logging.debug(f"[CANDIDATE_OHLCV_NO_CLOSE] {raw_symbol}")
                            return None

                        tech_features = self._compute_features(df, raw_symbol)
                        if float(tech_features.get('close', 0) or 0) <= 0:
                            logging.debug(f"[CANDIDATE_INVALID_PRICE] {raw_symbol}")
                            return None

                        # AI tahmini
                        ai_confidence = 0.5
                        potential_pct = 0.0
                        try:
                            if self.ai is not None:
                                ai_prediction = self.ai.predict(df) if hasattr(self.ai, 'predict') else None
                                if ai_prediction and isinstance(ai_prediction, dict):
                                    ai_confidence = float(ai_prediction.get('confidence', 0.5))
                                    potential_pct = float(ai_prediction.get('potential', 0.0))
                        except Exception as ai_err:
                            logging.debug(f"[CANDIDATE_AI_ERR] {raw_symbol}: {ai_err}")

                        clean_symbol = self._normalize_symbol(raw_symbol)

                        meta = {
                            'raw_symbol':raw_symbol,
                            'clean_symbol':clean_symbol,
                            'adx':tech_features.get('adx', 0.0),
                            'rsi':tech_features.get('rsi', 50.0),
                            'stoch_k':tech_features.get('stoch_k', 50.0),
                            'stoch_d':tech_features.get('stoch_d', 50.0),
                            'bb_width':tech_features.get('bb_width', 0.0),
                            'volume_ratio':tech_features.get('volume_ratio_20', 1.0),
                            'momentum_tension':tech_features.get('momentum_tension', 0.0),
                            'obv_slope':tech_features.get('obv_slope', 0.0),
                            'local_regime':tech_features.get(
                                'local_regime',
                                tech_features.get('local_trend', 'Bilinmiyor')
                            ),
                            'obv_status':tech_features.get('obv_status', '⚪️ Nötr'),
                            'volume_usd':cand_data.get('volume_usd', 0.0),
                            'ai_confidence':ai_confidence,
                            'potential_pct':potential_pct,
                            'timeframe':self.p_sel.get('timeframe_candidate', '15m'),
                        }
                        # lookback config’ten gelsin:
                        lookback = int(self.p_sel.get("diversify_lookback_bars", 120) or 120)

                        closes = pd.to_numeric(df["close"], errors="coerce").dropna().iloc[-lookback:]
                        rets = closes.pct_change().dropna()
                        meta["rets"] = rets
                        # Ham skor
                        raw_score = self._compute_candidate_score(strategy_id, meta)
                        # Reward/Penalty uygula
                        penalty_factor = 1.0
                        priority_bonus = 0.0
                        rp_status = "neutral"
                        rp_reason = "tracker_disabled"
                        rp_action = "allow"

                        if tracker is not None:
                            try:
                                # İstersen core'a çevir:
                                # health = tracker.get_symbol_health(clean_symbol.replace("/", ""))
                                health = tracker.get_symbol_health(clean_symbol)  # "SOL/USDT"
                                if health and isinstance(health, dict):
                                    rp_status = str(health.get("status", "neutral"))
                                    rp_reason = str(health.get("reason", ""))
                                    rp_action = str(health.get("action", "allow"))
                                    penalty_factor = float(health.get("penalty_factor", 1.0) or 1.0)
                                    priority_bonus = float(health.get("priority_bonus", 0.0) or 0.0)
                            except Exception as rp_err:
                                rp_status = "neutral"
                                rp_reason = f"tracker_err:{rp_err}"
                                rp_action = "allow"
                                penalty_factor = 1.0
                                priority_bonus = 0.0

                        # Hard gate: blacklist
                        if rp_action == "blacklist_skip":
                            return None

                        # Opsiyonel: çok cezalı sembolleri tamamen ele
                        if min_penalty_factor > 0 and penalty_factor < min_penalty_factor:
                            return None

                        edge_factor = 1.0
                        if edge_cal is not None:
                            try:
                                edge_factor = float(edge_cal.get_edge_factor(meta))
                            except Exception:
                                edge_factor = 1.0

                        base_score = float(raw_score) * float(edge_factor)
                        final_score = base_score * float(penalty_factor) + float(priority_bonus)
                        final_score = float(min(100.0, max(0.0, final_score)))

                        meta["edge_factor"] = float(edge_factor)
                        meta["technical_score_raw"] = float(raw_score)
                        meta["rp_status"] = rp_status
                        meta["rp_reason"] = rp_reason
                        meta["rp_action"] = rp_action
                        meta["rp_factor"] = float(penalty_factor)
                        meta["priority_bonus"] = float(priority_bonus)
                        meta["technical_score"] = float(final_score)

                        candidate = Candidate(
                            symbol=raw_symbol,
                            strategy_id=strategy_id,
                            candidate_score=final_score,
                            ai_confidence=ai_confidence,
                            movement_potential=potential_pct,
                            volume_ratio=meta['volume_ratio'],
                            compression_ratio=meta['bb_width'],
                            momentum_tension=meta['momentum_tension'],
                            segment_key="default",
                            expected_r_pre_signal=0.0,
                            meta=meta
                        )
                        logging.info(
                            f"[CANDIDATE_ANALYZED] {clean_symbol} | raw={raw_score:.1f} "
                            f"rp={penalty_factor:.2f} edge={edge_factor:.2f} "
                            f"final={final_score:.1f} | {rp_status} {rp_reason}"
                        )

                        logging.info(
                            f"[CANDIDATE_ANALYZED] {clean_symbol} ({raw_symbol}) | "
                            f"ScoreRaw={raw_score:.1f} RP={penalty_factor:.2f} Score={final_score:.1f} | "
                            f"RP={rp_status} ({rp_reason})"
                        )

                        return candidate

                except Exception as e1:
                    logging.error(f"[CANDIDATE_ANALYZE_ERR] {cand_data.get('symbol', '?')}: {e1}", exc_info=True)
                    return None

            results = await asyncio.gather(
                *[analyze_candidate(c) for c in candidates_raw],
                return_exceptions=False
            )

            candidates_list = [c for c in results if c is not None]
            candidates_list.sort(key=lambda c:c.candidate_score, reverse=True)

            top_k = int(self.p_sel.get("top_k_candidates", 30) or 30)
            corr_th = float(self.p_sel.get("diversify_corr_threshold", 0.92) or 0.92)
            lookback = int(self.p_sel.get("diversify_lookback_bars", 120) or 120)

            diversified = self._diversify_by_correlation(
                candidates_list,
                corr_threshold=corr_th,
                lookback_bars=lookback,
            )

            candidates_list = diversified[:top_k]

            logging.info(f"[CANDIDATE_SELECT_DONE] {len(candidates_list)} aday analiz edildi")

            return candidates_list

        except Exception as e:
            logging.error(f"[CANDIDATE_SELECT_FATAL] {e}", exc_info=True)
            return []

    @staticmethod
    def _diversify_by_correlation(
            candidates: List[Candidate],
            corr_threshold: float,
            lookback_bars: int = 120,
    ) -> List[Candidate]:
        """
        Skora göre sıralı adaylardan, birbirine çok korele olanları eler.
        Returns tabanlı: c.meta["rets"] (pd.Series) kullanır.
        Greedy seçim: en iyiyi al, sonra geleni mevcut seçilenlerle karşılaştır.
        """
        if not candidates:
            return []
        selected: List[Candidate] = []
        selected_returns: List[pd.Series] = []
        min_points = 20  # korelasyon hesaplamak için en az ortak nokta
        for c in candidates:
            try:
                rets = c.meta.get("rets")

                # Returns yoksa (veya bozuksa) filtre uygulamadan geç
                if not isinstance(rets, pd.Series) or rets.empty:
                    selected.append(c)
                    continue

                # Lookback uygula (güvenlik için)
                if len(rets) > lookback_bars:
                    rets = rets.iloc[-lookback_bars:]

                # NaN temizliği
                rets = pd.to_numeric(rets, errors="coerce").dropna()
                if len(rets) < min_points:
                    selected.append(c)
                    continue

                # Seçilmişlerle korelasyon kontrolü
                ok = True
                dropped_by = None
                dropped_corr = None

                for i, sr in enumerate(selected_returns):
                    aligned = pd.concat([rets, sr], axis=1).dropna()
                    if len(aligned) < min_points:
                        continue
                    corr = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
                    if corr is not None and corr >= corr_threshold:
                        ok = False
                        dropped_by = selected[i].symbol if i < len(selected) else "UNKNOWN"
                        dropped_corr = float(corr)
                        break

                if ok:
                    selected.append(c)
                    selected_returns.append(rets)
                else:
                    cs_logger.debug(
                        f"[DIVERSIFY_DROP] drop={c.symbol} corr={dropped_corr:.4f} "
                        f"th={corr_threshold} vs_selected={dropped_by}"
                    )

            except Exception:
                # Hata olursa filtreleme yüzünden aday kaybetme; olduğu gibi al
                selected.append(c)
        logging.info(
            f"[DIVERSIFY_SUMMARY] in={len(candidates)} out={len(selected)} "
            f"dropped={len(candidates) - len(selected)} th={corr_threshold} lookback={lookback_bars}"
        )
        return selected
