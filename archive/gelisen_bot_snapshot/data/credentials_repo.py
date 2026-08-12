# data/credentials_repo.py
from data.olimpos_data import db_operation

def list_user_exchange_credentials():
    # Sistemde key tanımlı tüm user-exchange çiftleri
    q = """
    SELECT user_id, exchange, username, api_key, secret_key, passphrase
    FROM api_key
    """
    rows = db_operation(q, operation="select", fetch=True, fetch_all=True) or []
    out = []
    for r in rows:
        out.append({
            "user_id": int(r[0]),
            "exchange": str(r[1]).lower(),
            "username": r[2],
            "api_key": r[3],
            "secret_key": r[4],
            "passphrase": r[5],
        })
    return out
