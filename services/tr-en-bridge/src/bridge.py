"""TR-EN koprusu (PLAN.md T7, Faz 1 B031 ile gercek NLU entegrasyonu).

`translate_and_extract`'in imzasi/cikti seklini (detected_alias/task_en/
original_tr/confidence) HICBIR ZAMAN degistirmez -- orchestrator ve
scripts/e2e_demo.py bu sozlesmeye gore yazildi. Siniflandirma kaynagi
`NLU_PROVIDER` ortam degiskeni ile secilir:

  - `mock` (varsayilan, guvenli): anahtar kelime tabanli, agsiz.
  - `ollama`: `ollama_nlu.classify()` uzerinden gercek model cagrisi;
    sonuc, bu modulun bilinen task_en degerlerine (asagida `_KNOWN_TASKS`)
    esler. Ollama herhangi bir nedenle basarisiz/tanimsiz sonuc verirse
    (servis kapali, timeout, bozuk cevap) SESSIZCE mock siniflandirmaya
    duser -- bridge asla bu yuzden exception firlatmaz veya cokmez.

`generate_tr_response`, orchestrator'dan donen EN sonucunu kullaniciya
gosterilecek TR metne cevirir (EN->TR yonu, hala mock/sablon tabanli).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from alias_matcher import detect_alias
from ollama_nlu import classify as ollama_classify

logger = logging.getLogger("bridge")

TASK_RUN_ECHO = "RUN_ECHO"
TASK_SHOW_DAILY_SPENDING = "SHOW_DAILY_SPENDING"
TASK_RUN_DELETE_FILE = "RUN_DELETE_FILE"
TASK_UNKNOWN = "UNKNOWN"

_KNOWN_TASKS = [TASK_RUN_ECHO, TASK_SHOW_DAILY_SPENDING, TASK_RUN_DELETE_FILE]

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

# --- NLU provider secimi (Faz 1, B031) -------------------------------------

NLU_PROVIDER_MOCK = "mock"
NLU_PROVIDER_OLLAMA = "ollama"
DEFAULT_NLU_PROVIDER = NLU_PROVIDER_MOCK
_VALID_NLU_PROVIDERS = (NLU_PROVIDER_MOCK, NLU_PROVIDER_OLLAMA)


def _get_nlu_provider() -> str:
    """`NLU_PROVIDER` ortam degiskenini okur; gecersiz/tanimsiz deger
    guvenli varsayilana (`mock`) duser -- asla bilinmeyen bir saglayiciya
    yonlendirmez."""
    provider = os.getenv("NLU_PROVIDER", DEFAULT_NLU_PROVIDER).strip().lower()
    if provider not in _VALID_NLU_PROVIDERS:
        logger.warning(
            "Geçersiz NLU_PROVIDER=%r, güvenli varsayılana (%s) dönülüyor.",
            provider,
            DEFAULT_NLU_PROVIDER,
        )
        return DEFAULT_NLU_PROVIDER
    return provider


def translate_and_extract(input_tr: str, aliases: list[str]) -> dict[str, Any]:
    """TR girdiyi EN task'a cevirir (saglayici: `NLU_PROVIDER`, bkz. modul dosyasi).

    Donen sozluk alanlari: detected_alias, task_en, original_tr, confidence.
    Bu sozlesme saglayiciya bakilmaksizin ayni kalir.
    """
    detected_alias = detect_alias(input_tr, aliases)
    task_en, confidence = _classify(input_tr)

    return {
        "detected_alias": detected_alias,
        "task_en": task_en,
        "original_tr": input_tr,
        "confidence": confidence,
    }


def _classify(input_tr: str) -> tuple[str, float]:
    """Aktif NLU sağlayıcısına göre sınıflandırma yapar.

    `ollama` sağlayıcısı seçiliyse önce `ollama_nlu.classify()` denenir;
    sonuç tanımsız (`UNKNOWN`) ya da herhangi bir hata durumundaysa
    -- en tutarlı/en az riskli davranış olduğu için -- sessizce mock
    sınıflandırmaya (`_mock_classify`) düşülür.
    """
    if _get_nlu_provider() == NLU_PROVIDER_OLLAMA:
        try:
            canonical = ollama_classify(input_tr, known_intents=_KNOWN_TASKS)
            intent = canonical.get("intent")
            confidence = canonical.get("confidence")
            if intent in _KNOWN_TASKS and isinstance(confidence, (int, float)):
                return intent, float(confidence)
        except Exception as exc:  # ollama_classify zaten güvenli; ekstra savunma
            logger.warning("Ollama NLU çağrısı başarısız, mock'a fallback: %s", exc)

    return _mock_classify(input_tr)


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
