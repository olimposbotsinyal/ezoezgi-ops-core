"""`reports/weekly_observability_<YYYY-WW>/review.md`'yi (bkz.
`weekly_review_core.py::render_review_entry_md` -- BU PROJENIN KENDI
SABIT bicimi) yapisal `review.json` (JSONL -- her satir bir haftalik
calistirma girdisidir) GIRDILERINE donusturur.

**Neden markdown PARSE EDILIYOR (genel olarak bu projede KACINILAN bir
teknik)?** Cunku bu, ANLASILMAZ/degisken bir DIS format DEGIL -- BU
PROJENIN kendi `render_review_entry_md()` fonksiyonunun urettigi, SABIT
ve BASIT bir bicimdir (biz kontrol ediyoruz). Regex, o TEK bilinen
sekle karsi yazilmistir -- gelecekte `render_review_entry_md()`
degisirse, bu script'in de GUNCELLENMESI gerekir (bkz. asagidaki
ENTRY_PATTERN yorumu).

`incidents_count` alani markdown'dan CIKARILMAZ (review.md hic
kaydetmiyor) -- bunun yerine EXPORT ANINDA `reports/incidents/`
altindaki dizin sayisi TAZE olarak hesaplanir.

Cikti: `reports/weekly_observability_<YYYY-WW>/review.json` -- HER
calistirma icin BIR SATIR (append-only degil, TAM YENIDEN URETIM --
markdown'daki TUM girdiler her calistirmada YENIDEN parse edilip TAM
dosya YAZILIR, boylece markdown SONRADAN elle duzeltilirse JSON de
senkron kalir).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT_DEFAULT = Path("d:/Projects/ezoezgi-ops")

# `weekly_review_core.py::render_review_entry_md`'nin URETTIGI TEK
# bilinen bicime karsi yazildi -- o fonksiyon degisirse BU DA
# GUNCELLENMELIDIR.
ENTRY_PATTERN = re.compile(
    r"## (?P<iso_week>\S+) -- (?P<generated_at>\S+)\n"
    r"\n"
    r"\*\*Durum: (?P<status>\w+)\*\*\n"
    r"\n"
    r"(?P<reasons_block>(?:- .+\n)*)"
    r"\n"
    r"- Fallback orani \(son 7 gun\): (?P<fallback>.+)\n"
    r"- Null-intent orani \(son 7 gun\): (?P<null_intent>.+)\n"
    r"- Ust alertler: (?P<top_alerts>.+)\n"
)


def _parse_ratio(text: str) -> float | None:
    text = text.strip()
    if text.startswith("n/a"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_top_alerts(text: str) -> list[str]:
    text = text.strip()
    if text == "yok":
        return []
    return [a.strip() for a in text.split(",") if a.strip()]


def parse_review_markdown(markdown_text: str) -> list[dict[str, Any]]:
    """`review.md` icerigini yapisal girdi listesine cevirir -- eslesme
    BULUNAMAZSA (bos dosya, beklenmedik bicim) BOS LISTE doner, ASLA
    exception FIRLATMAZ (hata durumunda SESSIZCE eksik veri, fabrike
    edilmis veri DEGIL)."""
    entries: list[dict[str, Any]] = []
    for m in ENTRY_PATTERN.finditer(markdown_text):
        reasons = [
            line[2:].strip() for line in m.group("reasons_block").splitlines() if line.startswith("- ")
        ]
        entries.append(
            {
                "iso_week": m.group("iso_week"),
                "generated_at": m.group("generated_at"),
                "status": m.group("status"),
                "reasons": reasons,
                "fallback_ratio": _parse_ratio(m.group("fallback")),
                "null_intent_ratio": _parse_ratio(m.group("null_intent")),
                "top_alerts": _parse_top_alerts(m.group("top_alerts")),
            }
        )
    return entries


def count_recent_incidents(repo_root: Path, *, iso_week: str) -> int:
    """`reports/incidents/*/` altindaki paket sayisini doner --
    HAFTAYA GORE FILTRELEMEZ (dizin adlarinda guvenilir bir tarih
    formati garanti edilmedigi icin), TUM BIRIKMIS olay paketlerinin
    TOPLAM sayisini verir (bilgilendirici baglam)."""
    incidents_dir = repo_root / "reports" / "incidents"
    if not incidents_dir.exists():
        return 0
    return sum(1 for p in incidents_dir.iterdir() if p.is_dir())


def export_review_json(review_md_path: Path, *, repo_root: Path) -> list[dict[str, Any]]:
    if not review_md_path.exists():
        return []
    markdown_text = review_md_path.read_text(encoding="utf-8")
    entries = parse_review_markdown(markdown_text)

    iso_week = review_md_path.parent.name.replace("weekly_observability_", "")
    incidents_count = count_recent_incidents(repo_root, iso_week=iso_week)
    for entry in entries:
        entry["incidents_count"] = incidents_count

    return entries


def write_review_json(entries: list[dict[str, Any]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + ("\n" if entries else ""), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Haftalik gozden gecirme markdown'ini yapisal JSON'a donusturur")
    parser.add_argument("--repo-root", default=str(REPO_ROOT_DEFAULT))
    parser.add_argument(
        "--review-md-path", default=None,
        help="Belirli bir review.md yolu. Verilmezse, TUM reports/weekly_observability_*/review.md dosyalari islenir.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root)

    if args.review_md_path:
        targets = [Path(args.review_md_path)]
    else:
        targets = sorted(repo_root.glob("reports/weekly_observability_*/review.md"))

    if not targets:
        print("Hicbir review.md bulunamadi -- islenecek bir sey yok.")
        return 0

    for review_md_path in targets:
        entries = export_review_json(review_md_path, repo_root=repo_root)
        out_path = review_md_path.parent / "review.json"
        write_review_json(entries, out_path)
        print(f"{review_md_path} -> {out_path} ({len(entries)} girdi)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
