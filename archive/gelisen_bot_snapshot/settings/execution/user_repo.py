# settings/execution/user_repo.py
from typing import List, Dict, Any
from data.olimpos_data import db_operation

def list_active_users_for_exchange(exchange: str) -> List[Dict[str, Any]]:
    """
    Exchange bazlı aktif user listesi.
    Kaynak: user_channel_info (senin aktif/pasif + süre kontrolün burada)
    """
    ex = (exchange or "").lower()

    q = """
    SELECT DISTINCT uci.user_id, uci.username
    FROM user_channel_info uci
    WHERE LOWER(uci.exchange) = LOWER(%s)
      AND uci.aktif_pasif = 'Aktif'
      AND COALESCE(uci.super_admin_pasif, 0) = 0
      AND (
            uci.end_date IS NULL
            OR CAST(uci.end_date AS TIMESTAMP) > CURRENT_TIMESTAMP
          )
    """
    rows = db_operation(q, (ex,), operation="select", fetch=True, fetch_all=True) or []
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append({"user_id": int(r[0]), "username": r[1]})
    return out

def get_user_binance_keys(user_id: int) -> Dict[str, Any] | None:
    """
    api_key tablosundan binance keylerini alır.
    """
    q = """
    SELECT api_key, secret_key
    FROM api_key
    WHERE user_id = %s AND LOWER(exchange) = 'binance'
    """
    row = db_operation(q, (user_id,), operation="select", fetch=True, fetch_all=False)
    if not row or len(row) < 2:
        return None
    return {"api_key": row[0], "secret_key": row[1]}
