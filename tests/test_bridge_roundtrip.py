"""T9 -- TR -> EN -> TR mock round-trip testi."""

from __future__ import annotations

from bridge import TASK_RUN_ECHO, TASK_UNKNOWN, generate_tr_response, translate_and_extract

ALIASES = ["ezo", "ezgi"]


def test_echo_command_roundtrip():
    # 1) TR -> EN (bridge, mock siniflandirma)
    extracted = translate_and_extract("Ezo, echo ile merhaba yaz", ALIASES)

    assert extracted["detected_alias"] == "ezo"
    assert extracted["task_en"] == TASK_RUN_ECHO
    assert extracted["original_tr"] == "Ezo, echo ile merhaba yaz"
    assert extracted["confidence"] > 0

    # 2) EN task -> (orchestrator/tool-call yerine burada mock sonuc) -> TR sablon
    mock_result_en = {"value": "Merhaba"}
    tr_response = generate_tr_response(extracted["task_en"], mock_result_en)

    assert tr_response == "Merhaba yazdırıldı."


def test_spending_command_roundtrip():
    extracted = translate_and_extract("Ezgi, bugünkü harcamaları göster", ALIASES)

    assert extracted["detected_alias"] == "ezgi"
    assert extracted["task_en"] == "SHOW_DAILY_SPENDING"

    tr_response = generate_tr_response(extracted["task_en"], {"amount": "150 TL"})

    assert "150 TL" in tr_response


def test_unknown_command_still_produces_tr_response():
    extracted = translate_and_extract("Ezo, hava durumu nasıl?", ALIASES)

    assert extracted["detected_alias"] == "ezo"
    assert extracted["task_en"] == TASK_UNKNOWN

    tr_response = generate_tr_response(extracted["task_en"])

    assert isinstance(tr_response, str) and len(tr_response) > 0


def test_command_without_alias_has_no_detected_alias():
    extracted = translate_and_extract("echo ile merhaba yaz", ALIASES)

    assert extracted["detected_alias"] is None
    assert extracted["task_en"] == TASK_RUN_ECHO
