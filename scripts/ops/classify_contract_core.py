"""`classify()` (services/tr-en-bridge/src/ollama_nlu.py) sozlesme
(contract) checksum'i icin saf mantik -- dosya G/C YOK, yalnizca
KAYNAK KOD METNINDEN AST ile imza cikarimi + checksum hesaplama.

**Neden AST, neden regex/tam-dosya-hash DEGIL?** `classify()`'in
gorunur ARAYUZU (parametre adlari, tip-ipuclari, opsiyonel/zorunlu
olma durumu, donus tipi) DEGISMEDIGI surece, docstring/yorum/ic
implementasyon degisiklikleri bu checksum'i asla ETKILEMEMELIDIR --
aksi halde her masum bir yorum duzeltmesi bile 'sozlesme degisti'
yanlis alarmi uretir. AST kullanarak yalnizca GERCEK imza yapisini
kanonik bir metne cevirip hash'liyoruz.

**Parametre VARSAYILAN DEGERLERI nicin DEGERLERIYLE DEGIL, yalnizca
VARLIKLARIYLA temsil ediliyor?** Bir varsayilan degerin (ornegin
`max_attempts=2` -> `max_attempts=3`) degismesi bir DAVRANIS
degisikligidir, ARAYUZ (sozlesme) degisikligi DEGILDIR -- parametre
hala ayni adda, ayni tipte, hala OPSIYONEL. Bu yuzden yalnizca
'<default>' yer tutucusu kullanilir, gercek deger DEGIL."""

from __future__ import annotations

import ast
import hashlib


def extract_function_signature(source: str, function_name: str) -> str | None:
    """`source` (bir Python dosyasinin TAM metni) icinde `function_name`
    adli fonksiyonu AST ile bulur, KANONIK bir imza metni doner (bulunamazsa
    `None`). Modul duzeyinde VE ic ice (nested) tanimlari kapsar (ast.walk)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            args = node.args
            parts: list[str] = []
            for a in args.args:
                ann = ast.unparse(a.annotation) if a.annotation else ""
                parts.append(f"{a.arg}:{ann}")
            defaults_start = len(args.args) - len(args.defaults)
            for i in range(len(args.defaults)):
                parts[defaults_start + i] += "=<default>"

            for a in args.kwonlyargs:
                ann = ast.unparse(a.annotation) if a.annotation else ""
                parts.append(f"*,{a.arg}:{ann}")
            for i, d in enumerate(args.kw_defaults):
                if d is not None:
                    idx = len(args.args) + i
                    if idx < len(parts):
                        parts[idx] += "=<default>"

            returns = ast.unparse(node.returns) if node.returns else ""
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            return f"{prefix} {function_name}({', '.join(parts)}) -> {returns}"

    return None


def compute_contract_checksum(signature_text: str) -> str:
    return hashlib.sha256(signature_text.encode("utf-8")).hexdigest()


def check_contract_drift(current_signature: str | None, baseline_checksum: str) -> tuple[bool, str]:
    """Doner: (degisti_mi, aciklama). `current_signature=None` -- fonksiyon
    KAYNAK KODDA hic BULUNAMADI -- bu da bir sozlesme ihlalidir (fonksiyon
    KALDIRILMIS/YENIDEN ADLANDIRILMIS olabilir)."""
    baseline_checksum = baseline_checksum.strip().lower()
    if current_signature is None:
        return True, "classify() fonksiyonu kaynak kodda BULUNAMADI (kaldirilmis/yeniden adlandirilmis olabilir)"
    current_checksum = compute_contract_checksum(current_signature)
    if current_checksum == baseline_checksum:
        return False, "classify() sozlesmesi (imza) baseline ile eslesiyor"
    return True, (
        f"classify() sozlesmesi (imza) DEGISMIS: beklenen checksum={baseline_checksum}, "
        f"gozlenen checksum={current_checksum}, gozlenen imza={current_signature!r}"
    )
