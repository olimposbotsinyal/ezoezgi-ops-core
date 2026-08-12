"""TR-EN koprusu iskeleti (PLAN.md T7).

Bu asamada gercek bir ceviri/NLU modeli baglanmadi -- `translate_and_extract`
anahtar kelime tabanli bir mock siniflandirici kullanir (bkz. T8/model_client.py
gercek model baglantisi icin). Amac, orchestrator + audit + E2E zincirinin
uctan uca calistigini kanitlamak.

`generate_tr_response`, orchestrator'dan donen EN sonucunu kullaniciya
gosterilecek TR metne cevirir (EN->TR yonu, hala mock/sablon tabanli).
"""

from __future__ import annotations

from typing import Any

from alias_matcher import detect_alias

TASK_RUN_ECHO = "RUN_ECHO"
TASK_SHOW_DAILY_SPENDING = "SHOW_DAILY_SPENDING"
TASK_RUN_DELETE_FILE = "RUN_DELETE_FILE"
TASK_UNKNOWN = "UNKNOWN"

# Orchestrator'un STATUS_WAITING_APPROVAL degeriyle birebir ayni string --
# bilerek cross-import edilmedi (bridge, apps/orchestrator'a bagimli olmamali,
# bkz. MASTER_ROADMAP.md §3 servis ayrimi), bu yuzden literal olarak tutuluyor.
_STATUS_WAITING_APPROVAL = "WAITING_APPROVAL"

# (anahtar kelime, task_en, mock-confidence) -- ilk eslesen kazanir.
_KEYWORD_TASK_MAP: list[tuple[str, str, float]] = [
    ("harcama", TASK_SHOW_DAILY_SPENDING, 0.6),
    ("echo", TASK_RUN_ECHO, 0.6),
    ("sil", TASK_RUN_DELETE_FILE, 0.6),
]


def translate_and_extract(input_tr: str, aliases: list[str]) -> dict[str, Any]:
    """TR girdiyi (mock) EN task'a cevirir.

    Donen sozluk alanlari: detected_alias, task_en, original_tr, confidence.
    """
    detected_alias = detect_alias(input_tr, aliases)
    task_en, confidence = _mock_classify(input_tr)

    return {
        "detected_alias": detected_alias,
        "task_en": task_en,
        "original_tr": input_tr,
        "confidence": confidence,
    }


def _mock_classify(input_tr: str) -> tuple[str, float]:
    lowered = input_tr.lower()
    for keyword, task_en, confidence in _KEYWORD_TASK_MAP:
        if keyword in lowered:
            return task_en, confidence
    return TASK_UNKNOWN, 0.0


def generate_tr_response(task_en: str, result: dict[str, Any] | None = None) -> str:
    """Orchestrator'dan donen EN sonucu (result_en) TR sablona cevirir."""
    result = result or {}

    if result.get("status") == _STATUS_WAITING_APPROVAL:
        risk_level = result.get("risk_level", "bilinmiyor")
        return (
            f"Bu işlem yüksek riskli ({risk_level}) olduğu için onay bekliyor. "
            "Devam etmek için onay vermeniz gerekiyor."
        )

    if task_en == TASK_RUN_ECHO:
        value = result.get("value")
        return f"{value} yazdırıldı." if value else "Echo çalıştırıldı."

    if task_en == TASK_SHOW_DAILY_SPENDING:
        amount = result.get("amount")
        if amount is not None:
            return f"Bugünkü harcamalarınız: {amount}."
        return "Bugünkü harcama bilgisi bulunamadı."

    return "Anlayamadım, tekrar eder misiniz?"
