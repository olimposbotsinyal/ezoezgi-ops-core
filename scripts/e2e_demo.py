#!/usr/bin/env python
"""E2E demo -- PLAN.md T14 (TR komut -> EN task -> tool call -> TR yanit).

Akis: TR input al -> bridge ile EN task/alias cikar -> orchestrator risk
seviyesini kontrol eder (T13) -> izinliyse cli-runner'i (T12) calistirir,
degilse WAITING_APPROVAL doner -> audit log'a yazar -> TR yanit yazdirir.

Kullanim:
    python scripts/e2e_demo.py "Ezo, echo ile merhaba yaz"
    python scripts/e2e_demo.py "Ezo, tüm dosyaları sil"   (onay bekleyen senaryo)

Argument verilmezse varsayilan ornek cumle kullanilir.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

for _src_dir in (
    REPO_ROOT / "apps" / "orchestrator" / "src",
    REPO_ROOT / "services" / "tr-en-bridge" / "src",
    REPO_ROOT / "tools" / "cli-runner" / "src",
):
    sys.path.insert(0, str(_src_dir))

from audit_logger import AuditLogger  # noqa: E402
from bridge import generate_tr_response, translate_and_extract  # noqa: E402
from config_loader import ConfigLoader  # noqa: E402
from orchestrator import Orchestrator  # noqa: E402
from registry import build_default_registry  # noqa: E402
from risk_engine import RiskEngine  # noqa: E402

DEFAULT_INPUT_TR = "Ezo, echo ile merhaba yaz"
CONFIG_PATH = REPO_ROOT / "config" / "assistant.identity.json"
RISK_POLICY_PATH = REPO_ROOT / "policies" / "risk" / "tool_risk_policy.yaml"
AUDIT_LOG_PATH = REPO_ROOT / "data" / "audit" / "audit.log.jsonl"


def run_e2e_demo(
    input_tr: str,
    *,
    config_path: Path = CONFIG_PATH,
    risk_policy_path: Path = RISK_POLICY_PATH,
    audit_log_path: Path = AUDIT_LOG_PATH,
) -> dict[str, Any]:
    """Tek bir TR komutu icin uctan uca zinciri calistirir.

    Donen sozluk: `tr_response`, `extracted` (bridge ciktisi), `result_en`
    (orchestrator ciktisi, `status`/`risk_level` dahil) alanlarini icerir.
    """
    config = ConfigLoader(config_path)
    aliases = config.get_wake_aliases()

    extracted = translate_and_extract(input_tr, aliases)

    orchestrator = Orchestrator(
        build_default_registry(), risk_engine=RiskEngine(risk_policy_path)
    )
    result_en = orchestrator.handle_task(extracted)

    audit_logger = AuditLogger(audit_log_path)
    audit_logger.log(
        alias=extracted["detected_alias"],
        task=extracted["task_en"],
        status=result_en.get("status", "unknown"),
        risk_level=result_en.get("risk_level") or "unknown",
        details={
            "original_tr": extracted["original_tr"],
            "confidence": extracted["confidence"],
            "result_en": result_en,
        },
    )

    tr_response = generate_tr_response(extracted["task_en"], result_en)

    return {"tr_response": tr_response, "extracted": extracted, "result_en": result_en}


def main() -> None:
    # Windows konsollari varsayilan olarak UTF-8 kullanmayabilir (cp1252 vb.);
    # bu durumda turkce karakterler mojibake olarak gorunur (veri bozulmaz,
    # yalnizca ekrana yazdirma etkilenir). Mumkunse stdout'u UTF-8'e zorla.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    input_tr = " ".join(sys.argv[1:]) or DEFAULT_INPUT_TR

    print(f"[TR girdi] {input_tr}")
    outcome = run_e2e_demo(input_tr)
    result_en = outcome["result_en"]
    print(f"[TR yanit] {outcome['tr_response']}")
    print(f"[risk] {result_en.get('risk_level')}  [status] {result_en.get('status')}")
    print(f"[audit log] {AUDIT_LOG_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
