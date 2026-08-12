# binance_client_factory.py
import ccxt

_BINANCE_CLIENTS = {}  # key: user_id -> ccxt instance

def get_binance_usdm_client(api_key: str, secret_key: str, *, cache_key: str):
    if cache_key in _BINANCE_CLIENTS:
        return _BINANCE_CLIENTS[cache_key]

    ex = ccxt.binanceusdm({
        "apiKey": api_key,
        "secret": secret_key,
        "enableRateLimit": True,
        "options": {"defaultType": "future"},
    })
    try:
        ex.load_markets()
    except Exception:
        pass

    _BINANCE_CLIENTS[cache_key] = ex
    return ex
