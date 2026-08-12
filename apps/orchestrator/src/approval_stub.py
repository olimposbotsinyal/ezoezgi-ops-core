"""Onay (approval) stub -- PLAN.md T13.

Risk seviyesi `high`/`irreversible` olan task'lar icin yururlugu otomatik
durdurup "onay bekliyor" durumunu simule eder -- gercek bir onay/red
mekanizmasi (kullanici arayuzu, bekleme suresi, cift onay) degil, yalnizca
bu durumun orchestrator akisinda dogru tetiklendigini kanitlayan bir stub.
Gercek onay akisi (CLI approve/reject) MASTER_ROADMAP.md §8 / Faz 4
kapsaminda ayrica ele alinacak.
"""

from __future__ import annotations

STATUS_AUTO_ALLOWED = "AUTO_ALLOWED"
STATUS_WAITING_APPROVAL = "WAITING_APPROVAL"

AUTO_ALLOW_LEVELS = ("low", "medium")
REQUIRES_APPROVAL_LEVELS = ("high", "irreversible")


def check_approval(risk_level: str) -> str:
    """Risk seviyesine gore otomatik izin mi onay mi gerektigini dondurur."""
    if risk_level in REQUIRES_APPROVAL_LEVELS:
        return STATUS_WAITING_APPROVAL
    return STATUS_AUTO_ALLOWED
