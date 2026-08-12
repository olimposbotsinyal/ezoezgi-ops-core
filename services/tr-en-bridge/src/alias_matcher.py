"""Wake-alias eslestirme (PLAN.md T6).

"Ezo" / "Ezgi" gibi wake-alias'larin bir TR cumle icinde dogru yakalanmasini
saglar. Kurallar:
  - case-insensitive (Ezo/ezo/EZO hepsi eslesir)
  - kelimenin bas/son noktalama isaretleri temizlenir ("Ezo," -> "ezo")
  - turkce karakter toleransi: "İ" (noktali buyuk I) -> "i", "I" (noktasiz
    buyuk I) -> "ı" -- boylece "EZGİ" de "ezgi" ile eslesir.

Eslesme, kelime bazli tam eslesmedir (substring degil): "ezgiyi" veya
"ezocan" gibi kelimeler alias olarak sayilmaz.
"""

from __future__ import annotations

import re

_TR_UPPER_TO_LOWER = str.maketrans(
    {
        "İ": "i",
        "I": "ı",
        "Ç": "ç",
        "Ğ": "ğ",
        "Ö": "ö",
        "Ş": "ş",
        "Ü": "ü",
    }
)

# Kelime basi/sonundaki noktalama/sembolleri temizler (harf, rakam, alt cizgi
# ve turkce karakterler "kelime" sayilir -- \w Python 3'te unicode farkindadir).
_PUNCT_STRIP_RE = re.compile(r"^[^\w]+|[^\w]+$", re.UNICODE)

_WORD_SPLIT_RE = re.compile(r"\s+", re.UNICODE)


def normalize_word(word: str) -> str:
    """Tek bir kelimeyi karsilastirmaya hazir hale getirir.

    Turkce buyuk harfleri once ozel esleme ile kucultur (str.lower()'in
    "İ" -> "i̇" (bilesik nokta) hatasindan kacinmak icin), sonra genel
    lower() uygulanir, en sonda bas/son noktalama temizlenir.
    """
    translated = word.translate(_TR_UPPER_TO_LOWER)
    lowered = translated.lower()
    return _PUNCT_STRIP_RE.sub("", lowered)


def detect_alias(text: str, aliases: list[str]) -> str | None:
    """Metindeki ilk wake-alias'i (soldan saga) dondurur, yoksa None.

    Donen deger, `aliases` listesindeki normalize edilmis (kucuk harf)
    formdur -- orijinal metindeki yazim (buyuk/kucuk, noktalama) degil.
    """
    if not text or not aliases:
        return None

    normalized_aliases = {normalize_word(a): normalize_word(a) for a in aliases if a}

    for token in _WORD_SPLIT_RE.split(text.strip()):
        normalized_token = normalize_word(token)
        if normalized_token in normalized_aliases:
            return normalized_aliases[normalized_token]

    return None


def has_alias(text: str, aliases: list[str]) -> bool:
    return detect_alias(text, aliases) is not None
