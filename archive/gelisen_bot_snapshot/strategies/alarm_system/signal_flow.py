# strategies/alarm_system/signal_flow.py
from __future__ import annotations
import logging
from core.strategy_manager import StrategyManager as SMRef
from telegram.ext import CallbackContext
import asyncio
from datetime import timedelta, datetime, timezone
from data.olimpos_data import get_user_notification_channel_ids, get_api_key, get_user_settings
from typing import Optional
from strategies.alarm_system import persistence as alarm_persistence
from strategies.alarm_system.analytics import AlarmRaporManager
from strategies.strategy_v1 import StrategyV1
from strategies.strategy_v2 import StrategyV2
SMRef.register(StrategyV1)
SMRef.register(StrategyV2)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    force=True
)


logger = logging.getLogger(__name__)


async def _forward_open_to_signal_merkezi(cls, signal_data: dict, context: CallbackContext):
    """
    Açılış sinyalini signal_merkezi'ne iletir.
    DÜZELTİLDİ:
      - Signal Merkezi payload['symbol'] her zaman CORE (BASEUSDT) formatında gider.
      - CCXT sembol sadece futures market kontrolü / display için kullanılır.
      - Paralel çoklu borsa kullanımında exchange seçimi korunur.
    """
    _ = context
    logging.info(
        f"[FWD_OPEN_ENTER] alarm_id={signal_data.get('alarm_id')} "
        f"signal_id={signal_data.get('signal_id')} "
        f"symbol={signal_data.get('symbol')} user_id={signal_data.get('user_id')}"
    )

    try:
        if not signal_data:
            logging.warning("[FWD_OPEN] signal_data boş")
            return

        # user_id
        user_id = signal_data.get('user_id')
        if not user_id:
            logging.warning("[FWD_OPEN] Sinyalde user_id bulunamadı, varsayılan admin kullanılacak.")
            from config.constants import ADMIN_USER_ID
            user_id = ADMIN_USER_ID

        # Kullanıcı kanal id'leri
        target_channel_ids = []
        try:
            # get_user_notification_channel_ids SYNC -> thread
            target_channel_ids = await asyncio.to_thread(get_user_notification_channel_ids, user_id)
        except Exception:
            target_channel_ids = []

        raw_sym = signal_data.get('symbol')

        # 1) Router/core sembol (Signal Merkezi için tek doğru format)
        try:
            router_symbol = cls.normalize_symbol(raw_sym)  # -> BTCUSDT
            if not router_symbol:
                logging.error(f"[FWD_OPEN_ERR] core symbol üretilemedi: raw={raw_sym}")
                return
        except Exception as norm_err:
            logging.error(f"[FWD_OPEN_NORM_ERR] {raw_sym}: {norm_err}")
            return

        # 2) Exchange seçimi (paralel borsa)
        exchange = (
                (context.user_data.get("selected_exchange") if context else None)
                or (context.user_data.get("exchange") if context else None)
                or (signal_data.get("meta", {}) or {}).get("exchange")
                or "mexc"
        )
        exchange = str(exchange).lower().strip()

        # 3) CCXT futures sembol (sadece kontrol + display için)
        ccxt_symbol = cls.to_ccxt_symbol(router_symbol, prefer_futures=True)
        if not ccxt_symbol:
            # Son çare: raw üzerinden dene
            ccxt_symbol = cls.to_ccxt_symbol(raw_sym, prefer_futures=True)

        if not ccxt_symbol:
            logging.warning(f"[FWD_OPEN_NO_CCXT] raw={raw_sym} core={router_symbol} -> ccxt_symbol üretilemedi")
            return

        # Futures kontrolü (CCXT sembol ile)
        try:
            if not cls.has_futures_market(ccxt_symbol):
                logging.warning(f"[FWD_OPEN_NO_FUT] {raw_sym} futures yok (ccxt={ccxt_symbol})")
                return
        except Exception as fut_err:
            logging.error(f"[FWD_OPEN_FUT_CHECK_ERR] raw={raw_sym} ccxt={ccxt_symbol} err={fut_err}")
            return

        # Display sembol (UI/log): BTC/USDT
        display_symbol = f"{router_symbol[:-4]}/USDT" if router_symbol.endswith("USDT") else (
                ccxt_symbol or raw_sym)

        logging.info(
            f"[FWD_OPEN_SYMBOLS] raw={raw_sym} core={router_symbol} ccxt={ccxt_symbol} "
            f"display={display_symbol} exchange={exchange}"
        )

        # Yön
        direction = str(signal_data.get("signal_type") or "LONG").upper().strip()
        if direction not in ("LONG", "SHORT"):
            direction = "LONG"

        # Fiyatlar
        entry_price = float(signal_data.get('entry_price') or 0)
        stop_loss = float(signal_data.get('stop_loss') or 0)
        targets = signal_data.get('targets') or []

        payload = {
            # ✅ Router için: CORE sembol
            'symbol':router_symbol,
            # UI/debug için:
            'display_symbol':display_symbol,
            'exchange':exchange,
            'direction':direction,
            'entry_price':entry_price,
            'stop_loss':stop_loss,
            'targets':targets,
            'channel_ids':target_channel_ids,
            "origin_channel_id":(target_channel_ids[0] if target_channel_ids else None),
            'alarm_id':signal_data.get('alarm_id'),
            'signal_id':signal_data.get('signal_id'),
            'timeframe':signal_data.get('timeframe'),
            'strategy_id':signal_data.get('strategy_id') or cls.active_strategy_id,
            'user_id':user_id
        }

        # Meta
        if isinstance(signal_data.get('meta'), dict):
            payload['meta'] = signal_data['meta']

        # Hedef yoksa otomatik oluştur (mevcut davranış korunur)
        if not payload['targets'] and entry_price > 0:
            try:
                step = 0.005 if direction == 'LONG' else -0.005
                payload['targets'] = [
                    round(entry_price * (1.0 + step * i), 8)
                    for i in range(1, 4)
                ]
                logging.debug(f"[FWD_OPEN_AUTO_TGT] {payload['targets']}")
            except Exception as tgt_err:
                logging.error(f"[FWD_OPEN_TGT_ERR] {tgt_err}")

        from signal_merkezi import handle_external_open_signal
        await handle_external_open_signal(payload)

        logging.info(
            f"[FWD_OPEN_CALL] exchange={exchange} symbol={router_symbol} display={display_symbol} direction={direction}"
        )

    except ImportError:
        logging.error("signal_merkezi.py bulunamadı veya handle_external_open_signal yok.", exc_info=True)
    except Exception as e:
        logging.error(f"Sinyal merkezi açılış iletim hatası: {e}", exc_info=True)


async def _forward_close_to_signal_merkezi(
        cls,
        signal_data: dict,
        exit_type: str,
        last_price: float,
        context: CallbackContext,
        user_id: int | None = None
):
    """
    Kapanış sinyalini signal_merkezi'ne iletir.
    DÜZELTİLDİ:
      - payload['symbol'] her zaman CORE (BASEUSDT)
      - display_symbol UI için BASE/USDT
      - paralel borsada exchange seçimi meta/context/cls üzerinden güvenli
    """
    _ = context
    try:
        raw_sym = signal_data.get('symbol')

        # CORE symbol (router için tek format)
        core_sym = cls.normalize_symbol(raw_sym)
        if not core_sym:
            logging.error(f"[FWD_CLOSE_ERR] Geçersiz sembol: raw={raw_sym}")
            return

        # User ID
        final_user_id = user_id or signal_data.get('user_id')
        if not final_user_id:
            logging.warning("[FWD_CLOSE] Sinyalde user_id bulunamadı, varsayılan admin kullanılacak.")
            from config.constants import ADMIN_USER_ID
            final_user_id = ADMIN_USER_ID

        # Kanal ID'leri
        target_channel_ids = []
        if final_user_id:
            target_channel_ids = await asyncio.to_thread(get_user_notification_channel_ids, final_user_id)

        if not target_channel_ids:
            target_channel_ids = cls.channel_ids

        # Exchange (paralel borsa)
        ex_from_meta = None
        try:
            ex_from_meta = (signal_data.get("meta", {}) or {}).get("exchange")
        except Exception:
            ex_from_meta = None

        exchange = (
                (ex_from_meta or "").strip()
                or str(getattr(cls, 'current_exchange', '') or '').strip()
                or "mexc"
        )
        exchange = exchange.lower()

        payload = {
            'symbol':core_sym,  # ✅ CORE
            'display_symbol':f"{core_sym[:-4]}/USDT" if core_sym.endswith("USDT") else core_sym,
            'exchange':exchange,

            'direction':signal_data.get('signal_type'),
            'exit_type':exit_type,
            'last_price':last_price,

            'channel_ids':target_channel_ids,
            "origin_channel_id":(target_channel_ids[0] if target_channel_ids else None),

            'alarm_id':signal_data.get('alarm_id'),
            'signal_id':signal_data.get('signal_id'),
            'timeframe':signal_data.get('timeframe'),
            'strategy_id':signal_data.get('strategy_id') or cls.active_strategy_id,
            'meta':signal_data.get('meta') if isinstance(signal_data.get('meta'), dict) else {}
        }

        from signal_merkezi import handle_external_close_signal
        await handle_external_close_signal(payload)

        logging.info(f"[FWD_CLOSE_CALL] symbol={core_sym} exit_type={exit_type} exchange={exchange}")

    except ImportError:
        logging.error("signal_merkezi.py bulunamadı veya handle_external_close_signal yok.", exc_info=True)
    except TypeError as te:
        logging.error(f"Sinyal merkezi tip hatası (await list?): {te}", exc_info=True)
    except Exception as e:
        logging.error(f"Sinyal merkezi kapanış iletim hatası: {e}", exc_info=True)


async def finalize_signal(
        cls,
        signal: dict,
        exit_type: str,
        current_price: float = None,
        context: CallbackContext = None,
        user_id: int | None = None
):
    try:
        if not signal:
            return

        # YENİ: Sinyal kapanırken o anki piyasa rejimini kaydet
        if context:  # context varsa rejim bilgisini ekle
            if 'meta' not in signal:
                signal['meta'] = {}
            signal['meta']['regime_at_close'] = await cls._get_market_regime(context)

        # DÜZELTME: Sinyal kapandığında borsadaki tüm açık emirleri iptal et.
        # Bu, "emir miktarı pozisyondan büyük" hatasını önler.
        try:
            if cls.exchange:
                # ✅ CCXT symbol seç: önce state'ten, yoksa core'dan üret
                sym_ccxt = (
                        signal.get("ccxt_symbol")
                        or cls.to_ccxt_symbol(signal.get("symbol"), prefer_futures=True)
                        or cls.to_ccxt_symbol(signal.get("core_symbol"), prefer_futures=True)
                )

                if sym_ccxt:
                    open_orders = await cls.exchange.fetch_open_orders(sym_ccxt)
                    if open_orders:
                        logging.info(f"Sinyal kapanıyor, {len(open_orders)} açık emir iptal edilecek: {sym_ccxt}")
                        for order in open_orders:
                            await cls.exchange.cancel_order(order['id'], sym_ccxt)
                            await asyncio.sleep(0.1)
                else:
                    logging.warning(
                        f"[FINALIZE] ccxt_symbol üretilemedi, open order cancel atlandı: {signal.get('symbol')}"
                    )
        except Exception as cancel_err:
            logging.error(f"Sinyal kapanırken açık emirler iptal edilemedi: {cancel_err}")

        if not signal:
            return

        # Eğer yeniden finalize edilirse, chart_buf_raw'ı temizle (replace mantığı persistence tarafında var)
        if signal.get('exit_type') in ('TARGET_FINAL', 'STOP'):
            if 'chart_buf_raw' in signal:
                del signal['chart_buf_raw']
                logging.debug(f"[FINALIZE] {signal.get('signal_id')} için chart_buf_raw temizlendi.")
            # yeniden finalize edilirse replace mantığı yine çalışacak
            pass

        # terminal alanlar
        signal['exit_type'] = exit_type
        signal['active'] = False
        # ✅ PNL burada hesaplanmaz. Tek kaynak: persistence.append_closed_signal()
        # Legacy/yanlış değer kalmasın diye temizle.
        signal["realized_net_pct"] = None

        if 'closed_time' not in signal or not signal.get('closed_time'):
            signal['closed_time'] = datetime.now(timezone.utc)

        entry = signal.get('entry_price')
        direction = signal.get('signal_type', 'LONG')
        targets = signal.get('targets', [])
        hits = signal.get('targets_hit', [])
        stop_loss = signal.get('stop_loss')

        # current_price fallback
        if current_price is None:
            if exit_type == 'TARGET_FINAL' and hits and entry:
                try:
                    last_idx = max([i for i, h in enumerate(hits) if h])
                    current_price = targets[last_idx]
                except Exception as e:
                    logging.error(f"Hata: {e}")
                    current_price = entry
            else:
                current_price = entry
    except (ValueError, TypeError, KeyError) as e:
        logging.error(f"Hata: {e}")

        # YENİ: Sinyal kapanırken o anki piyasa rejimini kaydet
        try:
            if 'meta' not in signal:
                signal['meta'] = {}
            signal['meta']['regime_at_close'] = await cls._get_market_regime(context)
        except Exception:
            pass

    finally:
        # DÜZELTME: JSON'a çevrilebilmesi için bytes tipindeki grafik verisini sil
        try:
            if isinstance(signal, dict) and 'chart_buf_raw' in signal:
                del signal['chart_buf_raw']
                logging.debug(
                    f"[FINALIZE] {signal.get('signal_id')} için chart_buf_raw kalıcı kayıttan önce silindi."
                )
        except Exception:
            pass

        # ✅ Kapalı sinyale yaz (GARANTİLİ: persistence üzerinden)
        try:
            alarm_persistence.append_closed_signal(cls, signal)
        except Exception as _append_err:
            logging.error(f"[CLOSED_APPEND_FATAL] {_append_err}", exc_info=True)

        # ✅ Alarmı yeniden taranabilir yap: converted kilidini kaldır
        try:
            sig_sym = cls.normalize_symbol(signal.get("symbol") or "")
            sig_tf = str(signal.get("timeframe") or "").strip()
            sig_sid = str(signal.get("strategy_id") or "").strip().lower()

            for a in (cls.active_symbols or []):
                if not isinstance(a, dict):
                    continue
                a_sym = cls.normalize_symbol(a.get("symbol") or a.get("core_symbol") or "")
                a_tf = str(a.get("timeframe") or "").strip()
                a_sid = str(a.get("strategy_id") or a.get("strategy_hint") or "").strip().lower()

                if a_sym == sig_sym and a_tf == sig_tf and a_sid == sig_sid:
                    if str(a.get("status") or "").lower() == "converted":
                        a["status"] = ""  # veya None
                        a["last_error"] = ""
                        a["last_attempt_ts"] = datetime.now(timezone.utc).isoformat()
                    break

            alarm_persistence.save_active_alarms_from_cls(cls)
        except Exception as _unlock_err:
            logging.debug(f"[ALARM_UNLOCK_WARN] {_unlock_err}")

        # ✅ Aktif listeden kaldır (mevcut davranış korunur)
        try:
            if signal in cls.active_signals:
                cls.active_signals.remove(signal)

            # Ek filtreleme: aynı signal_id kalmasın
            cls.active_signals = [
                s for s in cls.active_signals
                if s.get('signal_id') != signal.get('signal_id')
            ]
            logging.info(f"[FINALIZE_CLEANUP] {signal.get('signal_id')} aktif sinyal listesinden kaldırıldı.")
        except Exception:
            logging.error("Hata:")

        # ✅ Aktif state kaydet
        try:
            cls.save_active_signals(force=True)
        except Exception as e:
            logging.error(f"Hata: {e}")

        # ✅ Signal Merkezi kapanış iletimi (mevcut akış)
        try:
            asyncio.create_task(
                cls._forward_close_to_signal_merkezi(
                    signal,
                    exit_type,
                    current_price or signal.get('entry_price'),
                    context=context,
                    user_id=user_id
                )
            )
            logging.info(f"[FWD_CLOSE_SCHEDULED] {signal.get('symbol')} exit_type={exit_type}")
        except Exception as _fce:
            logging.error(f"[FINAL_FWD_ERR] {signal.get('symbol')} {_fce}")

        logging.info(
            f"[FINALIZE] signal_id={signal.get('signal_id')} "
            f"exit_type={exit_type} realized={signal.get('realized_net_pct')}"
        )


async def _update_stop_loss_on_exchange(
    cls,
    *,
    exchange_name: str,
    signal: dict,
    user_id: int,
    new_sl: float,
    reason: str
) -> dict:
    """
    Exchange-agnostic SL update router.
    Öncelik:
      1) Exchange özel executor fonksiyonu (varsa)
      2) CCXT üzerinden cls._sync_stop_to_exchange (legacy/adapter)
      3) Soft-fail: BOT_MANAGED
    """
    ex = (exchange_name or "").lower().strip()
    symbol_core = str(signal.get("core_symbol") or signal.get("symbol") or "").strip()

    if not symbol_core:
        return {"success": False, "error": "symbol_empty"}

    # --- Exchange alias normalize (özellikle Binance) ---
    # signal tarafında "binance", ccxt tarafında "binanceusdm" görebilirsin.
    def _ex_matches(ccxt_id: str, wanted: str) -> bool:
        ccxt_id = (ccxt_id or "").lower().strip()
        wanted = (wanted or "").lower().strip()
        if not ccxt_id or not wanted:
            return False
        if ccxt_id == wanted:
            return True
        # aliaslar
        if wanted == "binance" and ccxt_id in ("binanceusdm", "binance", "binancecoinm"):
            return True
        if wanted == "okx" and ccxt_id in ("okx",):
            return True
        if wanted == "bybit" and ccxt_id in ("bybit",):
            return True
        if wanted == "bitget" and ccxt_id in ("bitget",):
            return True
        if wanted == "mexc" and ccxt_id in ("mexc", "mexc3"):
            return True
        return False

    # 1) Exchange-özel executor (en güvenlisi)
    try:
        if ex == "binance":
            from settings.execution.binance_al_sat import binance_update_stop_loss
            return await binance_update_stop_loss(
                user_id=int(user_id),
                symbol_core=symbol_core,
                new_sl_price=float(new_sl),
                reason=str(reason or "TRAIL"),
            )

        if ex == "bitget":
            # from settings.execution.bitget_al_sat import bitget_update_stop_loss
            pass

        if ex == "bybit":
            # from settings.execution.bybit_al_sat import bybit_update_stop_loss
            pass

        if ex == "mexc":
            # from settings.execution.mexc_al_sat import mexc_update_stop_loss
            # return await mexc_update_stop_loss(...)
            pass

        if ex == "okx":
            # from settings.execution.okx_al_sat import okx_update_stop_loss
            pass

    except Exception as e:
        logging.warning(f"[SL_UPDATE_EXECUTOR_WARN] ex={ex} sym={symbol_core} err={e}")

    # 2) CCXT adapter (cls.exchange varsa ve doğru exchange ise)
    try:
        if getattr(cls, "exchange", None):
            ex_id = str(getattr(cls.exchange, "id", "") or "").lower().strip()
            if _ex_matches(ex_id, ex):
                await cls._sync_stop_to_exchange(
                    exchange=cls.exchange,
                    signal=signal,
                    user_id=int(user_id),
                    desired_stop=float(new_sl),
                )
                return {"success": True, "mode": "CCXT_ADAPTER", "exchange_id": ex_id}

    except Exception as e:
        logging.warning(f"[SL_UPDATE_CCXT_WARN] ex={ex} sym={symbol_core} err={e}")

    # 3) Soft fail
    return {
        "success": False,
        "error": "SERVER_SIDE_SL_UNSUPPORTED",
        "message": "Bu borsada server-side SL güncelleme desteklenmiyor veya adapter yok. SL BOT_MANAGED izlenecek.",
        "details": {"exchange": ex, "symbol_core": symbol_core, "new_sl": float(new_sl), "reason": reason},
    }


async def monitor_active_signals(cls, context: CallbackContext, user_id: int, price_map: dict | None = None):
    """
    Aktif sinyalleri takip et.
    Notlar (bu sürümün temel düzeltmeleri):
      - current_exchange_name "unbound" olamaz (default garanti)
      - Exchange init sinyal bazında yapılır (meta.exchange öncelikli)
      - Aynı turda aynı exchange için init sadece 1 kez yapılır (perf)
      - TP vurulduğunda önce state güncellenir, sonra trailing/maliyet_cek uygulanır,
        en son grafik render edilir. Böylece event kartında yeni stop görünür.
    """
    if not getattr(cls, "active_signals", None):
        await cls._manage_alarms_and_replenish(context, user_id)
        return

    signals_snapshot = list(getattr(cls, "active_signals", []) or [])
    if not signals_snapshot:
        return

    if not price_map:
        logging.warning("[MONITOR_ACTIVE] Ticker verisi alınamadı, bu tur atlanıyor.")
        return

    # Default exchange (signal meta yoksa buraya düşer) — ✅ her zaman tanımlı
    default_exchange_name = str(
        context.user_data.get("selected_exchange")
        or context.user_data.get("exchange")
        or "mexc"
    ).lower().strip() or "mexc"

    # Aynı tur içinde aynı exchange için init'i 1 kez yap
    initialized_exchanges: set[str] = set()

    rapor_manager = AlarmRaporManager()
    signals_to_remove_from_active_list: list[dict] = []

    # listeyi iterasyon sırasında güvenli tut
    for signal in list(cls.active_signals or []):
        try:
            if not isinstance(signal, dict):
                continue

            cls.normalize_signal_dict(signal)

            if not signal.get('active'):
                continue

            state_symbol = signal.get('symbol')
            if not state_symbol:
                continue

            # ✅ Sinyal bazlı exchange seç (meta.exchange öncelikli)
            sig_ex = str((signal.get("meta") or {}).get("exchange") or "").lower().strip()
            current_exchange_name = sig_ex or default_exchange_name

            # ✅ Exchange init (sadece bu turda ilk kez görüyorsak)
            if current_exchange_name not in initialized_exchanges:
                try:
                    api_info = await asyncio.to_thread(get_api_key, user_id, current_exchange_name)
                    if api_info:
                        await cls.initialize_exchange(
                            user_id=user_id,
                            exchange_name=current_exchange_name,
                            api_key=api_info['api_key'],
                            secret_key=api_info['secret_key'],
                            passphrase=api_info.get('passphrase'),
                            context=context
                        )
                        initialized_exchanges.add(current_exchange_name)
                    else:
                        logging.warning(f"[MONITOR_ACTIVE] API key yok user={user_id} ex={current_exchange_name}")
                        continue
                        # API yoksa bu exchange'e ait sinyalleri bu tur pas geç
                except Exception as ex_init_err:
                    logging.error(f"[MONITOR_ACTIVE] Exchange başlatma hatası ex={current_exchange_name}: {ex_init_err}")
                    # bu exchange'e ait sinyalleri bu tur pas geç
                    continue

            # 1) CCXT sembol üret (futures tercih)
            ccxt_sym = cls.to_ccxt_symbol(state_symbol, True)
            if not ccxt_sym:
                core_sym = cls.normalize_symbol(state_symbol)
                ccxt_sym = cls.to_ccxt_symbol(core_sym, True) if core_sym else None
            if not ccxt_sym:
                continue

            signal['ccxt_symbol'] = ccxt_sym

            # Futures market kontrolü
            try:
                if not cls.has_futures_market(ccxt_sym):
                    continue
            except Exception:
                continue

            # 2) Fiyat okuma (ccxt_sym öncelikli)
            tdata = price_map.get(ccxt_sym) if price_map else None

            if not tdata and isinstance(ccxt_sym, str) and ':' in ccxt_sym:
                base_spot = ccxt_sym.split(':', 1)[0]
                tdata = price_map.get(base_spot) if price_map else None

            if not tdata:
                tdata = price_map.get(state_symbol) if price_map else None

            if not (tdata and isinstance(tdata, dict)):
                continue

            current_price = (
                tdata.get('last')
                or tdata.get('close')
                or (tdata.get('info') or {}).get('lastPrice')
            )
            try:
                current_price = float(current_price)
            except Exception:
                continue

            signal_type = str(signal.get('signal_type') or 'LONG').upper().strip()
            if signal_type not in ("LONG", "SHORT"):
                signal_type = "LONG"

            # --- ACTIVATION GATE: entry gerçekleşmeden SL/TP yok ---
            act = signal.get("activation")
            if not isinstance(act, dict):
                act = {
                    "armed": True, "activated": False, "activated_time": None, "activation_price": None,
                    "rule": "TOUCH_ENTRY", "tolerance_pct": 0.0
                }
                signal["activation"] = act

            if act.get("armed", True) and not act.get("activated", False):
                try:
                    ep = float(signal.get("entry_price") or 0.0)
                    if ep <= 0:
                        continue
                    tol_pct = float(act.get("tolerance_pct") or 0.0)
                    tol = ep * (tol_pct / 100.0)

                    if signal_type == "LONG":
                        hit = current_price >= (ep - tol)
                    else:  # SHORT
                        hit = current_price <= (ep + tol)

                    if hit:
                        act["activated"] = True
                        act["activated_time"] = datetime.now(timezone.utc).isoformat()
                        act["activation_price"] = float(current_price)
                        signal["meta"] = signal.get("meta") if isinstance(signal.get("meta"), dict) else {}
                        signal["meta"]["activated"] = True
                    else:
                        cls.save_active_signals(force=False)
                        continue
                except Exception:
                    continue

            # 3) Peak / trough güncelle
            try:
                if str(signal.get('signal_type')).upper() == 'LONG':
                    if (signal.get('peak_price') is None) or (current_price > float(signal.get('peak_price') or 0.0)):
                        signal['peak_price'] = current_price
                else:
                    if (signal.get('trough_price') is None) or (
                        current_price < float(signal.get('trough_price') or current_price)
                    ):
                        signal['trough_price'] = current_price
            except Exception:
                pass

            targets = signal.get('targets', []) or []

            # targets_hit garanti
            if 'targets_hit' not in signal or not isinstance(signal.get('targets_hit'), list):
                signal['targets_hit'] = [False] * len(targets)
            if len(signal['targets_hit']) != len(targets):
                signal['targets_hit'] = [False] * len(targets)

            # targets_hit_times garanti
            if 'targets_hit_times' not in signal or not isinstance(signal.get('targets_hit_times'), list):
                signal['targets_hit_times'] = [None] * len(targets)
            if len(signal['targets_hit_times']) != len(targets):
                signal['targets_hit_times'] = [None] * len(targets)

            targets_hit: list[bool] = signal['targets_hit']

            # 4) HEDEF KONTROLÜ
            target_processed = False
            for i, target in enumerate(targets):
                if i >= len(targets_hit):
                    break
                if targets_hit[i]:
                    continue

                try:
                    target_f = float(target)
                except Exception:
                    continue

                if signal_type == "LONG":
                    target_hit = current_price >= target_f
                else:  # SHORT
                    target_hit = current_price <= target_f

                if not target_hit:
                    continue

                logging.info(f"[TARGET_HIT] {state_symbol} Hedef {i + 1} Vuruldu. Fiyat: {current_price}")

                # --- A) önce state güncelle (TP hit) ---
                targets_hit[i] = True
                now_utc = datetime.now(timezone.utc)
                signal['targets_hit_times'][i] = now_utc

                # --- B) stop güncelle (maliyet_cek / trailing) ---
                sl_changed = False
                sl_rule = ""
                old_sl = float(signal.get("stop_loss") or 0.0)
                new_sl = old_sl
                try:
                    sl_changed, sl_rule, old_sl, new_sl = cls.apply_tp_trailing_stop(signal, user_id=user_id)

                    # ✅ SL emrini borsaya uygula (sl_tp_emir=KULLAN ise)
                    try:
                        ex_name = current_exchange_name  # ✅ bu sinyalin exchange'i
                        u = await asyncio.to_thread(get_user_settings, user_id, ex_name) if user_id else None
                        sl_tp = str((u or {}).get("sl_tp_emir") or "").strip().lower()
                        use_exchange_sl = sl_tp in ("kullan", "on", "true", "1", "yes")

                        if sl_changed and use_exchange_sl:
                            # ✅ DİNAMİK ROUTER — tek kapı
                            try:
                                resp = await _update_stop_loss_on_exchange(
                                    cls,
                                    exchange_name=current_exchange_name,
                                    signal=signal,
                                    user_id=int(user_id),
                                    new_sl=float(new_sl),
                                    reason=f"TP{i + 1}",
                                )
                                if not (isinstance(resp, dict) and resp.get("success")):
                                    logging.warning(
                                        f"[SL_UPDATE_SOFTFAIL] ex={current_exchange_name} sym={signal.get('symbol')} resp={resp}"
                                    )
                                else:
                                    logging.info(
                                        f"[SL_UPDATE_OK] ex={current_exchange_name} sym={signal.get('symbol')} new_sl={float(new_sl)} reason=TP{i + 1}"
                                    )
                            except Exception as _route_err:
                                logging.warning(
                                    f"[SL_UPDATE_ROUTE_WARN] ex={current_exchange_name} sym={signal.get('symbol')} err={_route_err}"
                                )

                    except Exception as _sync_err:
                        logging.warning(f"[SL_SYNC_WARN] {signal.get('symbol')} err={_sync_err}")

                except Exception as _sl_err:
                    logging.warning(f"[TP_TRAIL_ERR] {state_symbol} err={_sl_err}")

                entry_ts = None
                try:
                    entry_ts = ((signal.get("activation") or {}).get("activated_time")) or signal.get("signal_time")
                except Exception:
                    entry_ts = signal.get("signal_time")

                # --- C) Grafik render: stop güncellemesinden SONRA ---
                try:
                    df = await cls.fetch_ohlcv_with_retry(ccxt_sym, signal.get('timeframe'))
                    if df is not None:
                        chart_buf = cls.chart_renderer.render(
                            symbol=cls.to_display_symbol(state_symbol),
                            df=df,
                            signal_type=signal_type,
                            entry_price=float(signal.get('entry_price') or 0.0),
                            targets=targets,
                            stop_loss=float(signal.get('stop_loss') or 0.0),
                            prev_stop_loss=old_sl,
                            timeframe=signal.get('timeframe'),
                            signal_time=entry_ts,
                            entry_index=None,
                            target_hits=list(signal.get('targets_hit') or []),
                            targets_hit_times=list(signal.get('targets_hit_times') or []),
                        )
                        if chart_buf:
                            try:
                                chart_buf.seek(0)
                            except Exception:
                                pass
                            signal['chart_buf_raw'] = chart_buf.read()
                except Exception:
                    pass

                # --- Süre hesabı ---
                time_diff_str = "-"
                try:
                    signal_time_dt = cls._ensure_aware(signal.get('signal_time'))
                    hit_time_dt = cls._ensure_aware(now_utc)
                    if signal_time_dt and hit_time_dt:
                        duration_delta = hit_time_dt - signal_time_dt
                        if duration_delta.total_seconds() < 0:
                            duration_delta = timedelta(seconds=0)
                        time_diff_str = cls._format_duration(duration_delta)
                except Exception:
                    pass

                # --- Segment stats (mevcut) ---
                try:
                    entry_v = float(signal.get('entry_price') or 0.0)
                    stop_v = float(signal.get('stop_loss') or 0.0)
                    if entry_v and stop_v:
                        risk = abs(entry_v - stop_v)
                        if risk > 0:
                            realized_r = (
                                (current_price - entry_v) / risk
                                if signal_type == "LONG"
                                else (entry_v - current_price) / risk
                            )
                            seg_key = signal.get('segment_key')
                            strat_id = signal.get('strategy_id', 'v1')
                            if seg_key:
                                from analytics.segment_stats import get_segment_manager
                                get_segment_manager().update(strat_id, seg_key, realized_r)
                except Exception:
                    pass

                # rapor kaydet
                try:
                    rapor_manager.kaydet_alarm_raporu(signal)
                except Exception:
                    pass

                # leverage bul
                lev = 1.0
                try:
                    u_settings = get_user_settings(user_id, current_exchange_name) if user_id else None
                    if u_settings and u_settings.get("leverage"):
                        lev = float(u_settings["leverage"])
                except Exception:
                    lev = 1.0

                # hedefin kendi yüzdesi (kaldıraçlı)
                try:
                    t_raw = cls._calc_raw_pct(
                        signal.get("signal_type"),
                        float(signal.get("entry_price") or 0.0),
                        float(target_f),
                    )
                    target_pnl_lev = float(t_raw) * float(lev)
                except Exception:
                    target_pnl_lev = 0.0

                p = cls.calc_realized_pnl_lev(signal, leverage=lev, current_price=current_price)

                event_meta = {
                    "target_num": i + 1,
                    "time_str": time_diff_str,
                    "last_price_on_event": current_price,
                    "pnl_lev": float(p["upnl_lev"]),
                    "realized_lev": float(p["realized_lev"]),
                    "remaining_pct": float(p["remaining_pct"]),
                    "target_pnl_lev": float(target_pnl_lev),
                }

                if sl_changed:
                    event_meta["sl_old"] = float(old_sl)
                    event_meta["sl_new"] = float(new_sl)
                    event_meta["sl_rule"] = str(sl_rule)

                is_final = (i == len(targets) - 1)
                msg_type = "FINAL" if is_final else "TARGET"

                if cls._allow_event_now(signal.get('signal_id')):
                    await cls.update_signal_messages(context, signal, msg_type, event_meta, user_id=user_id)
                    logging.info(f"📢 {state_symbol} {msg_type} kartı gönderildi.")
                else:
                    if is_final:
                        await asyncio.sleep(0.15)

                if is_final:
                    signal['exit_type'] = 'TARGET_FINAL'
                    signal['active'] = False

                    try:
                        await asyncio.to_thread(
                            cls.log_trade_outcome,
                            state_symbol,
                            signal_type,
                            'TARGET_FINAL',
                            current_price,
                            float(signal.get('entry_price') or 0.0),
                            int(sum(1 for h in (signal.get('targets_hit') or []) if h)),
                            signal.get('signal_time'),
                            exit_stage='final',
                            signal_ref=signal
                        )
                    except Exception:
                        pass

                    await cls.finalize_signal(signal, 'TARGET_FINAL', current_price, context=context, user_id=user_id)

                    if signal not in signals_to_remove_from_active_list:
                        signals_to_remove_from_active_list.append(signal)

                    await cls._handle_signal_outcome(signal, outcome_type='WIN')
                else:
                    logging.info(f"✅ Hedef {i + 1} tamamlandı, devam ediyor.")

                cls.save_active_signals(force=True)
                target_processed = True
                break  # aynı turda tek event

            if target_processed:
                continue

            # 5) STOP LOSS KONTROLÜ
            if not signal.get('stop_loss_hit', False):
                stop_loss = float(signal.get('stop_loss') or 0.0)
                stop_hit = False

                if signal_type == "LONG" and current_price <= stop_loss:
                    stop_hit = True
                elif signal_type == "SHORT" and current_price >= stop_loss:
                    stop_hit = True

                if stop_hit:
                    # grafik render (STOP için)
                    try:
                        df = await cls.fetch_ohlcv_with_retry(ccxt_sym, signal.get('timeframe'))
                        if df is not None:
                            chart_buf = cls.chart_renderer.render(
                                symbol=cls.to_display_symbol(state_symbol),
                                df=df,
                                signal_type=signal_type,
                                entry_price=float(signal.get('entry_price') or 0.0),
                                targets=targets,
                                stop_loss=float(signal.get('stop_loss') or 0.0),
                                timeframe=signal.get('timeframe'),
                                signal_time=signal.get('signal_time'),
                                entry_index=signal.get('entry_index'),
                                target_hits=list(signal.get('targets_hit') or []),
                                targets_hit_times=list(signal.get('targets_hit_times') or []),
                            )
                            if chart_buf:
                                try:
                                    chart_buf.seek(0)
                                except Exception:
                                    pass
                                signal['chart_buf_raw'] = chart_buf.read()
                    except Exception:
                        pass

                    # süre
                    dur_str = "-"
                    try:
                        st = signal.get('signal_time')
                        st_dt = cls._ensure_aware(st) if isinstance(st, str) else (
                            st if isinstance(st, datetime) else None
                        )
                        if st_dt:
                            dur_str = cls._format_duration(datetime.now(timezone.utc) - st_dt)
                    except Exception:
                        pass

                    # leverage
                    lev = 1.0
                    try:
                        u_settings = get_user_settings(user_id, current_exchange_name) if user_id else None
                        if u_settings and u_settings.get("leverage"):
                            lev = float(u_settings["leverage"])
                    except Exception:
                        lev = 1.0

                    p = cls.calc_realized_pnl_lev(signal, leverage=lev, current_price=current_price)

                    event_meta = {
                        "target_num": int(sum(1 for h in (signal.get('targets_hit') or []) if h)),
                        "time_str": dur_str,
                        "last_price_on_event": current_price,
                        "pnl_lev": float(p["upnl_lev"]),
                        "realized_lev": float(p["realized_lev"]),
                        "remaining_pct": float(p["remaining_pct"]),
                    }

                    await cls.update_signal_messages(context, signal, "STOP", event_meta, user_id=user_id)
                    logging.info(f"🛑 {state_symbol} STOP kartı gönderildi.")

                    signal['stop_time'] = datetime.now(timezone.utc)
                    signal['exit_type'] = 'STOP'
                    signal['closed_time'] = datetime.now(timezone.utc)
                    signal['active'] = False
                    signal['stop_loss_hit'] = True

                    try:
                        await asyncio.to_thread(
                            cls.log_trade_outcome,
                            state_symbol,
                            signal_type,
                            'STOP',
                            float(current_price),
                            float(signal.get('entry_price') or 0.0),
                            int(sum(1 for h in (signal.get('targets_hit') or []) if h)),
                            signal.get('signal_time'),
                            exit_stage='final',
                            signal_ref=signal
                        )
                    except Exception:
                        pass

                    await cls.finalize_signal(signal, 'STOP', current_price, context=context, user_id=user_id)

                    if signal not in signals_to_remove_from_active_list:
                        signals_to_remove_from_active_list.append(signal)

                    outcome = 'LOSS' if int(sum(1 for h in (signal.get('targets_hit') or []) if h)) == 0 else 'WIN'
                    await cls._handle_signal_outcome(signal, outcome_type=outcome)

                    cls.save_active_signals(force=True)
                    continue

        except Exception as signal_error:
            logging.error(
                f"❌ Sinyal takip hatası {signal.get('symbol', 'UNKNOWN') if isinstance(signal, dict) else 'UNKNOWN'}: {signal_error}",
                exc_info=True
            )

    # Temizlik + replenish
    if signals_to_remove_from_active_list:
        for s in signals_to_remove_from_active_list:
            try:
                if s in cls.active_signals:
                    cls.active_signals.remove(s)
            except Exception:
                pass

        cls.save_active_signals(force=True)
        logging.info(f"🧹 {len(signals_to_remove_from_active_list)} adet kapanan sinyal listeden temizlendi.")

    await cls._manage_alarms_and_replenish(context, user_id)
    cls.cleanup_old_signals(max_age_hours=172)


async def update_signal_messages(
        cls,
        context: CallbackContext,
        signal: dict,
        update_type: str,
        event_meta: dict,
        user_id: Optional[int] = None
):
    try:
        import inspect
        if inspect.isclass(cls.chart_renderer):
            cls.chart_renderer = cls.chart_renderer()
        renderer = cls.chart_renderer

        # 1) current_price belirle
        current_price = event_meta.get('last_price_on_event')
        if current_price is None and update_type in ('TARGET', 'FINAL'):
            t_num = event_meta.get('target_num', 1)
            targets = signal.get('targets', [])
            if targets and len(targets) >= t_num:
                current_price = targets[t_num - 1]

        # -----------------------------
        # ADIM A: Olay kartı (2. görsel)
        # -----------------------------
        signal_for_render = dict(signal)
        signal_for_render["symbol"] = cls.to_display_symbol(signal.get("symbol"))

        clean_chart_buf = await renderer.render_main_with_targets(
            signal=signal_for_render,
            current_price=current_price,
            user_id=user_id or signal.get('user_id'),
            only_chart=True
        )

        target_idx = None
        if update_type in ('TARGET', 'FINAL'):
            target_num = event_meta.get('target_num', 1)
            target_idx = target_num - 1

        if update_type in ("TARGET", "FINAL"):
            profit = float(event_meta.get("target_pnl_lev", event_meta.get("pnl_lev", 0.0)))
        else:
            profit = float(event_meta.get("pnl_lev", 0.0))
        time_str = event_meta.get('time_str', '-')
        # emoji
        if update_type == "STOP":
            emoji = "🛑"
        else:
            emoji = "✅"

        # title
        if update_type == "STOP":
            title = "STOP LOSS"
        elif update_type == "FINAL":
            title = f"HEDEF {event_meta.get('target_num', 1)} VURULDU (FİNAL)"
        else:
            title = f"HEDEF {event_meta.get('target_num', 1)} VURULDU"

        symbol = cls.to_display_symbol(signal.get('symbol', 'UNKNOWN'))
        extra = ""
        if update_type in ("TARGET", "FINAL"):
            if "sl_old" in event_meta and "sl_new" in event_meta:
                try:
                    old_sl = float(event_meta.get("sl_old") or 0.0)
                    new_sl = float(event_meta.get("sl_new") or 0.0)
                    rule = str(event_meta.get("sl_rule") or "")
                    extra = f"\nSL: {cls._fmt_price_dynamic(old_sl)} → {cls._fmt_price_dynamic(new_sl)} ({rule})"
                except Exception:
                    extra = "\nSL: güncellendi"

        card_caption = (
            f"{emoji} **{symbol}** - {title}\n"
            f"Net: %{profit:.2f} | Süre: {time_str}"
            f"{extra}"
        )

        if clean_chart_buf:
            await cls._send_event_image(
                context=context,
                signal=signal,
                event_type=update_type,
                target_index=target_idx,
                caption=card_caption,
                user_id=user_id,
                chart_buf_override=clean_chart_buf,
                event_meta=event_meta,  # ✅ kritik
            )

            clean_chart_buf.close()

        # ----------------------------------------
        # ADIM B: Ana mesajı güncelle (1. görsel)
        # -> Uzun metin yok
        # -> Sadece sabit caption
        # -> Hedef ikonları ana görselde olacak (B bölümünde)
        # ----------------------------------------
        main_msg_buf = await renderer.render_main_with_targets(
            signal=signal_for_render,
            current_price=current_price,
            user_id=user_id or signal.get('user_id'),
            only_chart=False
        )
        # ✅ İstediğiniz tek satır caption
        calc = (signal.get("meta") or {}).get("calc_method") or {}
        tp_src = str(calc.get("tp") or "Bilinmiyor")
        sl_src = str(calc.get("sl") or "Bilinmiyor")
        fixed_caption = f"ℹ️ Hesap: Hedefler({tp_src}) | SL({sl_src})"

        main_messages = signal.get('main_messages', [])
        if main_messages and main_msg_buf:
            from telegram import InputMediaPhoto
            for mm in main_messages:
                try:
                    main_msg_buf.seek(0)
                    await context.bot.edit_message_media(
                        chat_id=mm.get('channel_id'),
                        message_id=mm.get('message_id'),
                        media=InputMediaPhoto(media=main_msg_buf, caption=fixed_caption)
                    )
                except Exception as e:
                    logging.warning(f"Ana mesaj güncellenemedi: {e}")

        if main_msg_buf:
            main_msg_buf.close()

    except Exception as e:
        logging.error(f"update_signal_messages hatası: {e}", exc_info=True)

