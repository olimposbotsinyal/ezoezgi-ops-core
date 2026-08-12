"""T6 -- alias_matcher.py testleri (en az 15 vaka, pozitif/negatif)."""

from __future__ import annotations

import pytest

from alias_matcher import detect_alias, normalize_word

ALIASES = ["ezo", "ezgi"]


@pytest.mark.parametrize(
    "text, expected",
    [
        # --- Pozitif vakalar ---
        ("Ezo, bugünkü harcamaları göster", "ezo"),
        ("ezgi bana yardım et", "ezgi"),
        ("EZGİ neredesin?", "ezgi"),  # noktali buyuk İ
        ("Merhaba Ezo!", "ezo"),  # sondaki unlem
        ("ezo?", "ezo"),
        ("  Ezgi   selam", "ezgi"),  # fazladan bosluk
        ("(ezo) bir şey sor", "ezo"),  # parantez
        ("Ezgi.", "ezgi"),  # nokta
        ("EZO acil durum", "ezo"),  # tamamen buyuk harf
        ("selam ezgi,", "ezgi"),  # cumle sonunda, virgullu
        ("ezgi ezo ikisi de var", "ezgi"),  # ilk gecen alias donmeli
        ("\tEzo\n", "ezo"),  # tab/newline whitespace
        # --- Negatif vakalar ---
        ("merhaba nasılsın", None),  # alias yok
        ("ezocan geldi", None),  # substring, tam kelime degil
        ("bu ezgiyi çok sevdim", None),  # ek almis kelime, tam eslesme degil
        ("", None),  # bos string
        ("1234 ezo5", None),  # rakam eklenmis, tam eslesme degil
        ("Ez o boyle bir alias yok", None),  # kelime bolunmus
        ("EZGİYE söyle", None),  # ek almis + buyuk harf birlikte
    ],
)
def test_detect_alias(text, expected):
    assert detect_alias(text, ALIASES) == expected


def test_detect_alias_with_no_aliases_configured():
    assert detect_alias("Ezo selam", []) is None


def test_detect_alias_aliases_list_itself_mixed_case():
    # aliases listesi de karisik/buyuk harfli verilse dahi calismali.
    assert detect_alias("ezo selam", ["Ezo", "EZGI"]) == "ezo"


def test_normalize_word_turkish_capital_i_with_dot():
    assert normalize_word("EZGİ") == "ezgi"


def test_normalize_word_strips_surrounding_punctuation():
    assert normalize_word("«Ezo!»") == "ezo"
