"""Remote saglayici icin policy gate -- policies/risk/tool_risk_policy.yaml.

Bu dosya, CLI-tool risk seviyelerinin (RUN_ECHO/RUN_DELETE_FILE vb.) yani
sira, remote model saglayicisinin kullanimina izin verilip verilmedigini
tanimlayan `remote_model_policy` bolumunu de barindirir (bkz. dosyanin
kendisindeki yorum). Politika dosyasi okunamazsa/bozuksa GUVENLI TARAF
secilir: remote KULLANILMAZ (allowed=False) -- asla varsayilan olarak
izin verilmez.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("model_gateway.policy")

DEFAULT_POLICY_PATH = Path("policies/risk/tool_risk_policy.yaml")


def is_remote_allowed(policy_path: Path | None = None) -> tuple[bool, str]:
    """Remote saglayicinin kullanimina politika izin veriyor mu?

    Donen: (allowed: bool, detail: str). Dosya yok/bozuk/bolum eksikse
    guvenli varsayilan (allowed=False) doner -- asla sessizce izin vermez.
    """
    path = policy_path or DEFAULT_POLICY_PATH
    if not path.exists():
        return False, f"Politika dosyasi bulunamadi ({path}); guvenli varsayilan: remote kapali"

    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("Politika dosyasi okunamadi (%s), remote kapali sayiliyor: %s", path, exc)
        return False, f"Politika dosyasi okunamadi: {exc}; guvenli varsayilan: remote kapali"

    if not isinstance(data, dict):
        return False, "Politika dosyasi beklenen sekilde degil; guvenli varsayilan: remote kapali"

    remote_policy: dict[str, Any] = data.get("remote_model_policy") or {}
    if not isinstance(remote_policy, dict):
        return False, "remote_model_policy bolumu tanimsiz; guvenli varsayilan: remote kapali"

    allowed = bool(remote_policy.get("allowed", False))
    reason = str(remote_policy.get("reason", ""))
    if allowed:
        return True, reason or "Politika remote kullanimina izin veriyor"
    return False, reason or "Politika remote kullanimina izin vermiyor"
