"""B036 upstream watch check.

Ollama upstream issue #17716'daki yeni aktiviteyi kontrol eder, varsa
tetikleyici tipini sinifllandirir ve upstream_watch_log.md'ye bir satir
ekler. Hicbir zaman runtime deneyi (ollama serve, /api/generate vb.)
CALISTIRMAZ -- yalnizca GitHub API'sini okur ve dosyaya yazar.

Kullanim:
    python scripts/watch_b036_upstream.py

Cikis kodu:
    0 -> kontrol tamamlandi (NONE/DIAGNOSTIC_REQUEST/PATCH_REFERENCE/
         NEW_RELEASE_HINT hangisi olursa olsun -- bunlarin hicbiri "hata"
         degildir)
    1 -> beklenmedik/sert hata (dosya sistemi vb.)

Ag erisimi yoksa veya GitHub API basarisiz olursa "CHECK_FAILED_NETWORK"
yazdirilir ve exit code yine 0 olur (bu bir "hard failure" degil, gecici
bir kontrol basarisizligidir -- runner script'i bunu ayirt eder).
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ISSUE_OWNER = "ollama"
ISSUE_REPO = "ollama"
ISSUE_NUMBER = 17716
ISSUE_URL = f"https://github.com/{ISSUE_OWNER}/{ISSUE_REPO}/issues/{ISSUE_NUMBER}"
OUR_COMMENT_ID = 5275320830  # bu projenin postaladigi takip yorumu (bkz. B036 watch protokolu)

API_ISSUE_URL = f"https://api.github.com/repos/{ISSUE_OWNER}/{ISSUE_REPO}/issues/{ISSUE_NUMBER}"
API_COMMENTS_URL = f"https://api.github.com/repos/{ISSUE_OWNER}/{ISSUE_REPO}/issues/{ISSUE_NUMBER}/comments"

INCIDENT_DIR = Path("reports/runtime_incident_20260813T004855Z")
WATCH_LOG_PATH = INCIDENT_DIR / "upstream_watch_log.md"

ISTANBUL_OFFSET = timedelta(hours=3)
REQUEST_TIMEOUT_SECONDS = 15

# Siniflandirma anahtar kelimeleri -- oncelik sirasiyla kontrol edilir:
# once PATCH_REFERENCE, sonra NEW_RELEASE_HINT, sonra DIAGNOSTIC_REQUEST.
PATCH_REFERENCE_KEYWORDS = [
    "pull request", " pr #", "commit ", "fixed in", "cherry-pick",
    "patch attached", "here's a patch", "opened a fix",
]
NEW_RELEASE_HINT_KEYWORDS = [
    "released in", "available in v", "upgrade to v", "new release",
    "changelog", "ships in v", "fixed in v",
]
DIAGNOSTIC_REQUEST_KEYWORDS = [
    "can you try", "please run", "please share", "can you share",
    "set the following", "enable debug", "ollama_debug",
    "collect the log", "share the log", "reproduce with", "could you test",
    "what happens if", "try setting",
]

VALID_TRIGGERS = ("NONE", "DIAGNOSTIC_REQUEST", "PATCH_REFERENCE", "NEW_RELEASE_HINT")


def istanbul_now_label() -> str:
    now_ist = datetime.now(timezone.utc) + ISTANBUL_OFFSET
    return now_ist.strftime("%Y-%m-%d %H:%M") + " (+03:00)"


def fetch_json(url: str) -> object:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "ezoezgi-ops-b036-watch",
        },
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


def classify_comment_body(body: str) -> str:
    text = (body or "").lower()
    if any(k in text for k in PATCH_REFERENCE_KEYWORDS):
        return "PATCH_REFERENCE"
    if any(k in text for k in NEW_RELEASE_HINT_KEYWORDS):
        return "NEW_RELEASE_HINT"
    if any(k in text for k in DIAGNOSTIC_REQUEST_KEYWORDS):
        return "DIAGNOSTIC_REQUEST"
    return "NONE"


def find_new_activity(issue: dict, comments: list[dict]) -> dict:
    """Bizim yorumumuzdan (OUR_COMMENT_ID) sonra, baska bir kullanicidan
    gelen en yeni yorumu bulur. Yoksa activity_detected=False doner.
    """
    reporter_login = (issue.get("user") or {}).get("login")
    candidates = [
        c
        for c in comments
        if c.get("id") != OUR_COMMENT_ID
        and c.get("id", 0) > OUR_COMMENT_ID
        and (c.get("user") or {}).get("login") != reporter_login
    ]
    if not candidates:
        return {"activity_detected": False}

    candidates.sort(key=lambda c: c.get("created_at", ""))
    latest = candidates[-1]
    body = latest.get("body", "") or ""
    trigger = classify_comment_body(body)
    return {
        "activity_detected": True,
        "trigger": trigger,
        "comment_id": latest.get("id"),
        "comment_url": latest.get("html_url"),
        "author_login": (latest.get("user") or {}).get("login"),
        "author_association": latest.get("author_association"),
        "created_at": latest.get("created_at"),
        "body_snippet": body.strip().replace("\n", " ")[:160],
    }


def escape_table_cell(text: str) -> str:
    return (text or "").replace("|", "\\|")


def read_last_watch_log_row() -> str | None:
    if not WATCH_LOG_PATH.exists():
        return None
    lines = [ln for ln in WATCH_LOG_PATH.read_text(encoding="utf-8").splitlines() if ln.strip().startswith("|")]
    data_rows = [ln for ln in lines if not set(ln.replace("|", "").strip()) <= {"-", " "}]
    if len(data_rows) <= 1:  # yalnizca header var
        return None
    return data_rows[-1]


def append_watch_log_row(*, checker: str, activity: str, summary: str, action: str, evidence: str) -> bool:
    """Satiri ekler. Ayni run icinde iki kez cagrilirsa (idempotency guard)
    ikinci cagriyi atlar. True donerse satir eklendi, False donerse atlandi.
    """
    if not hasattr(append_watch_log_row, "_already_appended_this_run"):
        append_watch_log_row._already_appended_this_run = False  # type: ignore[attr-defined]
    if append_watch_log_row._already_appended_this_run:  # type: ignore[attr-defined]
        print("SKIPPED_DUPLICATE (bu calistirmada zaten bir satir eklendi)")
        return False

    ts = istanbul_now_label()
    row = (
        f"| {ts} | {checker} | {activity} | {escape_table_cell(summary)} "
        f"| {escape_table_cell(action)} | {escape_table_cell(evidence)} |\n"
    )
    with WATCH_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(row)
    append_watch_log_row._already_appended_this_run = True  # type: ignore[attr-defined]
    return True


def main() -> int:
    try:
        issue = fetch_json(API_ISSUE_URL)
        comments = fetch_json(API_COMMENTS_URL)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        print("CHECK_FAILED_NETWORK")
        print(f"detay: {exc}")
        return 0

    if not isinstance(issue, dict) or not isinstance(comments, list):
        print("CHECK_FAILED_NETWORK")
        print("detay: beklenmeyen API yaniti sekli")
        return 0

    result = find_new_activity(issue, comments)

    if not result["activity_detected"]:
        summary = "Yeni maintainer/ucuncu-taraf yorumu bulunamadi (yalnizca bizim yorumumuz veya daha eskisi mevcut)."
        action = "NO_ACTION"
        evidence = ISSUE_URL
        appended = append_watch_log_row(
            checker="watch_b036_upstream.py",
            activity="H",
            summary=summary,
            action=action,
            evidence=evidence,
        )
        print("TRIGGER=NONE")
        print(f"activity_detected=HAYIR")
        print(f"action={action}")
        print(f"appended_row={'EVET' if appended else 'HAYIR (duplicate)'}")
        return 0

    trigger = result["trigger"]
    assert trigger in VALID_TRIGGERS

    action_map = {
        "NONE": "NO_ACTION",
        "DIAGNOSTIC_REQUEST": "Trigger A: hedefli veri toplama gerekiyor (bkz. validation_on_trigger.md)",
        "PATCH_REFERENCE": "Trigger B: 3-senaryolu dogrulama gerekiyor (bkz. validation_on_trigger.md)",
        "NEW_RELEASE_HINT": "Trigger C: tam 5-senaryolu matris gerekiyor (bkz. validation_on_trigger.md)",
    }
    summary = (
        f"Yeni yorum tespit edildi (id={result['comment_id']}, yazar={result['author_login']}, "
        f"association={result['author_association']}): \"{result['body_snippet']}\""
    )
    action = action_map[trigger]

    appended = append_watch_log_row(
        checker="watch_b036_upstream.py",
        activity="E",
        summary=summary,
        action=action,
        evidence=result["comment_url"] or ISSUE_URL,
    )

    print(f"TRIGGER={trigger}")
    print("activity_detected=EVET")
    print(f"comment_id={result['comment_id']}")
    print(f"author={result['author_login']} (association={result['author_association']})")
    print(f"action={action}")
    print(f"appended_row={'EVET' if appended else 'HAYIR (duplicate)'}")
    print()
    print("ONEMLI: Bu script hicbir runtime deneyi calistirmadi.")
    print("Deney calistirmak icin validation_on_trigger.md'deki ilgili Trigger bolumunu MANUEL takip edin.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
