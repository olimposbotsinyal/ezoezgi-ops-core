"""T14 -- resmi E2E kabul testi (PLAN.md T14 kabul kriteri).

Iki senaryo:
  1) Normal: "Ezo, echo ile 'merhaba' yaz" -> "Merhaba yazdırıldı." + audit
     kaydinda status=ok, risk_level=low.
  2) Onay bekleyen (high/irreversible): "Ezo, tüm dosyaları sil" ->
     WAITING_APPROVAL + TR yanitinda "onay" gecen bir mesaj + audit kaydinda
     status=WAITING_APPROVAL, risk_level=irreversible.

Bu test, `scripts/e2e_demo.py`'nin gercekten kullandigi varsayilan
config/policy/audit yollarini (projenin gercek dosyalarini) kullanir --
amac, teslim edilen demo script'in bizzat kendisinin dogru calistigini
kanitlamaktir (izole/mock bir kopyasini degil).
"""

from __future__ import annotations

import json

from e2e_demo import AUDIT_LOG_PATH, run_e2e_demo


def _last_audit_record() -> dict:
    lines = AUDIT_LOG_PATH.read_text(encoding="utf-8").splitlines()
    return json.loads(lines[-1])


def test_echo_command_produces_expected_tr_response_and_audit_entry():
    outcome = run_e2e_demo("Ezo, echo ile 'merhaba' yaz")

    assert outcome["tr_response"] == "Merhaba yazdırıldı."
    assert outcome["result_en"]["status"] == "ok"
    assert outcome["result_en"]["risk_level"] == "low"
    assert outcome["extracted"]["detected_alias"] == "ezo"

    record = _last_audit_record()
    assert record["task"] == "RUN_ECHO"
    assert record["status"] == "ok"
    assert record["risk_level"] == "low"
    assert record["alias"] == "ezo"


def test_irreversible_command_waits_for_approval_and_is_audited():
    outcome = run_e2e_demo("Ezo, tüm dosyaları sil")

    assert outcome["result_en"]["status"] == "WAITING_APPROVAL"
    assert outcome["result_en"]["risk_level"] == "irreversible"
    assert "onay" in outcome["tr_response"].lower()

    record = _last_audit_record()
    assert record["task"] == "RUN_DELETE_FILE"
    assert record["status"] == "WAITING_APPROVAL"
    assert record["risk_level"] == "irreversible"


def test_default_input_used_when_none_provided_matches_documented_example():
    # scripts/e2e_demo.py DEFAULT_INPUT_TR ile ayni varsayimi test eder.
    outcome = run_e2e_demo("Ezo, echo ile merhaba yaz")

    assert outcome["result_en"]["status"] == "ok"
    assert outcome["tr_response"] == "Merhaba yazdırıldı."
