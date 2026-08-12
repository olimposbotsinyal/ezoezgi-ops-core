# strategies/alarm_system/symbols.py
from __future__ import annotations
from typing import Optional
import logging
from core.strategy_manager import StrategyManager as SMRef
# Stratejileri Kaydet
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


def normalize_symbol(cls, symbol: str) -> str:
    """
    Çekirdek sembol normalizasyonu.
    ÇIKTI: BASEUSDT (ayraçsız), USDT.P -> USDT indirgenir.
    Örn: '4/USDT:USDT' -> '4USDT', '1000SEMBOL/USDT' -> '1000SEMBOLUSDT'
    """
    try:
        if not isinstance(symbol, str):
            return ''
        s = symbol.strip().upper()
        if not s:
            return ''

        s = s.replace(' ', '')
        s = s.strip('/:_-')

        # ':SETTLE' bölümünü at (örn ':USDT')
        left = s.split(':', 1)[0]

        # Ayraç normalizasyonu
        left = left.replace('-', '/').replace('_', '/').strip('/')

        base, quote = '', ''
        if '/' in left:
            parts = [p for p in left.split('/') if p]
            if len(parts) >= 2:
                base, quote = parts[0], parts[1]
            elif len(parts) == 1:
                base = parts[0]
        else:
            # Sonda USDT.P veya USDT var mı?
            if left.endswith('USDT.P') and left != 'USDT.P':
                base = left[:-6]
                quote = 'USDT'
            elif left.endswith('USDT') and left != 'USDT':
                base = left[:-4]
                quote = 'USDT'
            else:
                return ''

        if not base or not quote:
            return ''
        if base == quote:
            return ''

        # Quote'u USDT sabitle
        quote = 'USDT'
        return f"{base}{quote}"
    except Exception as e:
        logging.error(f"normalize_symbol err: {repr(e)}")
        return ''


def to_ccxt_symbol(cls, core_or_any: str, prefer_futures: bool = True) -> Optional[str]:
    """
    Girdi: çekirdek (BASEUSDT) veya herhangi bir format (örn: BASE/USDT:USDT).
    Çıktı: CCXT 'markets' içinde bulunan geçerli sembol anahtarı.
    """
    try:
        ex = getattr(cls, "exchange", None)

        # ✅ ex yoksa da değişkenler tanımlı kalsın (static analyzer mutlu)
        markets = getattr(ex, "markets", None) or {} if ex else {}
        symbols = getattr(ex, "symbols", None) or [] if ex else []

        # ✅ 0) Zaten CCXT market key ise direkt dön
        s0 = str(core_or_any or "").strip()
        if s0 and (s0 in markets or s0 in symbols):
            return s0

        core = cls.normalize_symbol(core_or_any)
        if not core or not core.endswith("USDT") or len(core) <= 4:
            return None

        base = core[:-4]
        quote = "USDT"

        # exchange yokken: "tahmini" format döndür
        if not ex:
            return f"{base}/{quote}:{quote}" if prefer_futures else f"{base}/{quote}"

        has = getattr(ex, "has", None) or {}
        has_swap = bool(has.get("swap") or has.get("future"))

        fut_candidates = [
            f"{base}/{quote}:{quote}",  # MEXC/BYBIT
            f"{base}/{quote}",  # Binance USDT-M (bazı durumlar)
            f"{base}-{quote}-SWAP",  # OKX
            f"{base}_{quote}",
        ]
        spot_candidates = [
            f"{base}/{quote}",
            f"{base}-{quote}",
            f"{base}_{quote}",
        ]

        def first_in(md, lst, cands):
            for c in cands:
                if c in md or c in lst:
                    return c
            return None

        if prefer_futures and has_swap:
            found = first_in(markets, symbols, fut_candidates)
            if found:
                return found

        found = first_in(markets, symbols, spot_candidates)
        if found:
            return found

        if (not prefer_futures) or (not has_swap):
            found = first_in(markets, symbols, fut_candidates)
            if found:
                return found

        # Son çare: MEXC/BYBIT stili tahmin
        return f"{base}/{quote}:{quote}" if prefer_futures else f"{base}/{quote}"

    except Exception as e:
        logging.error(f"[TO_CCXT_SYMBOL_ERR] {e}", exc_info=True)
        return None


def to_display_symbol(cls, any_symbol: str, quote: str = "USDT") -> str:
    """
    UI/Telegram/görsel üzerinde gösterilecek format:
      - XLMUSDT -> XLM/USDT
      - XLM/USDT:USDT -> XLM/USDT
      - XLM-USDT-SWAP -> XLM/USDT
    """
    try:
        core = cls.normalize_symbol(any_symbol)  # -> BASEUSDT
        if not core or not core.endswith(quote) or len(core) <= len(quote):
            # fallback: hiç dokunma
            return str(any_symbol or "")
        base = core[:-len(quote)]
        return f"{base}/{quote}"
    except Exception:
        return str(any_symbol or "")


def has_futures_market(cls, any_symbol: str) -> bool:
    ex = getattr(cls, "exchange", None)
    if not ex:
        return False
    markets = getattr(ex, "markets", None) or {}
    symbols = getattr(ex, "symbols", None) or []

    s = str(any_symbol or "").strip()
    s2 = cls.to_ccxt_symbol(s, prefer_futures=True) or s

    ok = (s2 in markets) or (s2 in symbols)
    if not ok:
        logging.warning(f"No futures market for {s} (resolved={s2})")
    return ok


def to_machine_symbol(cls, raw_symbol: str, prefer_futures: bool = True) -> str:
    """
    Router/Signal Merkezi için sembol: slash'sız çekirdek + opsiyonel :SETTLE
    - Örn: BTC/USDT:USDT -> BTCUSDT:USDT
    - Örn: BTC/USDT -> BTCUSDT (spot)
    """
    ccxt_sym = cls.to_ccxt_symbol(raw_symbol, prefer_futures=prefer_futures)
    if not ccxt_sym:
        return ''
    if ':' in ccxt_sym:
        left, settle = ccxt_sym.split(':', 1)
    else:
        left, settle = ccxt_sym, ''
    if '/' in left:
        base, quote = left.split('/', 1)
        core = f"{base}{quote}"
    else:
        core = left
    return f"{core}:{settle}" if settle else core


def core_to_machine_symbol(cls, core_symbol: str, prefer_futures: bool = True) -> Optional[str]:
    """
    core_symbol: 'BASEUSDT'
    Dönen: CCXT markets dict anahtarında bulunan sembol (ör. 'BASE/USDT:USDT', 'BASE/USDT', 'BASE-USDT-SWAP').
    Futures mevcutsa onu tercih eder.
    """
    try:
        if not core_symbol or not core_symbol.endswith('USDT') or len(core_symbol) <= 4:
            return None
        base = core_symbol[:-4]
        quote = 'USDT'

        ex = getattr(cls, 'exchange', None)
        if not ex:
            return None

        markets = getattr(ex, 'markets', None) or {}
        symbols = getattr(ex, 'symbols', []) or []

        spot_candidates = [f"{base}/{quote}", f"{base}-{quote}", f"{base}_{quote}", ]
        fut_candidates = [f"{base}/{quote}:{quote}",  # MEXC/BYBIT
            f"{base}/{quote}",  # Binance USDT-M
            f"{base}-{quote}-SWAP",  # OKX
            f"{base}_{quote}", ]

        def first_in_dict(md, cands):
            for c in cands:
                if c in md:
                    return c
            return None

        def first_in_list(lst, cands):
            sset = set(lst)
            for c in cands:
                if c in sset:
                    return c
            return None

        has_swap = False
        try:
            has = getattr(ex, 'has', {}) or {}
            has_swap = bool(has.get('swap') or has.get('future'))

        except Exception as e:
            logging.error(f"Hata: {e}")

        pass

        if prefer_futures and has_swap:
            found = first_in_dict(markets, fut_candidates) or first_in_list(symbols, fut_candidates)
            if found:
                return found

        found = first_in_dict(markets, spot_candidates) or first_in_list(symbols, spot_candidates)
        if found:
            return found

        if not prefer_futures or not has_swap:
            found = first_in_dict(markets, fut_candidates) or first_in_list(symbols, fut_candidates)
            if found:
                return found

        return None
    except Exception as e:
        logging.error(f"[CORE2MACHINE_ERR] {e}")
        return None


def to_signal_center_symbol(cls, raw_symbol: str, exchange: str = 'mexc') -> str:
        """
        DÜZELTİLDİ: Detaylı log + özel mapping desteği

        Args:
            raw_symbol: BTC/USDT, BTCUSDT, BTC/USDT:USDT
            exchange: mexc, binance, okx, bybit, bitget

        Returns:
            Signal merkezi için format
        """
        try:
            if not raw_symbol:
                return ''

            # Özel mapping kontrolü
            if not hasattr(cls, 'SPECIAL_SYMBOL_MAP'):
                cls._init_special_symbol_mappings()

            exchange = exchange.lower()
            normalized = cls.normalize_symbol(raw_symbol)
            if normalized in cls.SPECIAL_SYMBOL_MAP:
                normalized = cls.SPECIAL_SYMBOL_MAP[normalized]
                logging.debug(f"[SIGNAL_CENTER_MAP] {raw_symbol} -> {normalized}")
            if exchange == 'mexc':
                # MEXC: BTC/USDT:USDT -> BTCUSDT:USDT
                ccxt_sym = cls.to_ccxt_symbol(normalized, True) or ''

                if ':' in ccxt_sym:
                    left, settle = ccxt_sym.split(':', 1)
                    # Slash'ları temizle
                    base_quote = left.replace('/', '')
                    result = f"{base_quote}:{settle}"
                else:
                    result = ccxt_sym.replace('/', '')

                logging.debug(
                    f"[TO_SIGNAL_CENTER] "
                    f"raw={raw_symbol} "
                    f"normalized={normalized} "
                    f"ccxt={ccxt_sym} "
                    f"result={result}"
                )

                return result

            elif exchange == 'binance':
                # Binance: BTC/USDT:USDT -> BTCUSDT
                ccxt_sym = cls.to_ccxt_symbol(normalized, True) or ''
                if ':' in ccxt_sym:
                    left, _ = ccxt_sym.split(':', 1)
                    return left.replace('/', '')
                else:
                    return ccxt_sym.replace('/', '')

            # DÜZELTME: Her borsa için ayrı 'elif' bloğu kullanarak mantık çakışmasını önle.
            elif exchange in ('okx', 'bybit', 'bitget'):
                # Diğer borsalar: BTC/USDT:USDT -> BTC-USDT-SWAP
                ccxt_sym = cls.to_ccxt_symbol(normalized, True) or ''
                if ':' in ccxt_sym and '/' in ccxt_sym:  # Örn: 'BTC/USDT:USDT'
                    left, _ = ccxt_sym.split(':', 1)
                    base_quote = left.replace('/', '-')
                    return f"{base_quote}-SWAP"
                elif '/' in ccxt_sym:  # Örn: 'BTC/USDT' (Binance stili)
                    return ccxt_sym.replace('/', '-')
                else:  # Zaten doğru formatta olabilir
                    return ccxt_sym.replace('/', '-')
            else:
                # Varsayılan
                return cls.to_machine_symbol(normalized, True)

        except Exception as e:
            logging.error(f"[TO_SIGNAL_CENTER_ERR] {raw_symbol}: {e}")
            return raw_symbol
