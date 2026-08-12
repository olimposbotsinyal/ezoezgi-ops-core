"""Orchestrator (PLAN.md T10, T13 risk/onay entegrasyonu ile).

Bridge'in `translate_and_extract()` ciktisini alir:
  1) `risk_engine.py` ile task'in risk seviyesini belirler,
  2) `approval_stub.py` ile otomatik izin mi onay mi gerektigine karar verir
     (`high`/`irreversible` ise handler hic cagrilmaz, `WAITING_APPROVAL`
     doner),
  3) izinliyse `registry.py` uzerinden handler'i bulup cagirir.

Handler bulunamazsa, task_en bos ise veya handler exception firlatirsa
orchestrator cokmez, kontrollu bir durum sozlugu doner (bkz. audit_logger.py
ile birlikte kullanim icin `scripts/e2e_demo.py`).
"""

from __future__ import annotations

from typing import Any

from approval_stub import STATUS_WAITING_APPROVAL, check_approval
from registry import HandlerNotFoundError, Registry
from risk_engine import RiskEngine

STATUS_OK = "ok"
STATUS_NO_HANDLER = "no_handler"
STATUS_ERROR = "error"
# STATUS_WAITING_APPROVAL, approval_stub'dan import edildi (yukarida) -- burada
# tekrar tanimlanmiyor, `from orchestrator import STATUS_WAITING_APPROVAL` da
# calisir (modul seviyesinde zaten erisilebilir).


class Orchestrator:
    def __init__(self, registry: Registry, risk_engine: RiskEngine | None = None) -> None:
        self._registry = registry
        self._risk_engine = risk_engine or RiskEngine()

    def handle_task(self, extracted: dict[str, Any]) -> dict[str, Any]:
        """`bridge.translate_and_extract()` ciktisini isler.

        Donen sozluk her zaman `status` (`ok` | `no_handler` | `error` |
        `WAITING_APPROVAL`) ve -- task_en bos degilse -- `risk_level` alanlarini
        icerir.
        """
        task_en = extracted.get("task_en")

        if not task_en:
            return {"status": STATUS_NO_HANDLER, "task_en": task_en, "risk_level": None}

        risk_level = self._risk_engine.get_risk(task_en)

        if check_approval(risk_level) == STATUS_WAITING_APPROVAL:
            return {
                "status": STATUS_WAITING_APPROVAL,
                "task_en": task_en,
                "risk_level": risk_level,
            }

        try:
            handler = self._registry.get(task_en)
        except HandlerNotFoundError:
            return {"status": STATUS_NO_HANDLER, "task_en": task_en, "risk_level": risk_level}

        task_payload = {
            "task_en": task_en,
            "original_tr": extracted.get("original_tr"),
            "detected_alias": extracted.get("detected_alias"),
        }

        try:
            result = handler(task_payload)
        except Exception as exc:  # handler hatasi orchestrator'i cokertmemeli
            return {
                "status": STATUS_ERROR,
                "task_en": task_en,
                "risk_level": risk_level,
                "error": str(exc),
            }

        result_en = dict(result)
        result_en.setdefault("status", STATUS_OK)
        result_en["task_en"] = task_en
        result_en["risk_level"] = risk_level
        return result_en
