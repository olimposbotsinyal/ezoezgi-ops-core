"""Acil durum (APPROVE_EMERGENCY) esik degisikliklerinin retroaktif
inceleme kapanisini, SADECE `alert_name` eslesmesi (v1.1) yerine
checksum-ZINCIRI SUREKLILIGI ile degerlendiren v1.2 PILOT mantigi.

Bu modul KASITLI olarak `observability_drift_core.check_emergency_review_overdue_drift`'in
KENDI (v1.1) mantigini DEGISTIRMEZ -- yeni, ayri bir "kapanis"
siniflandirmasi sunar; cagiran taraf (`observability_drift_core.py`,
`use_chain_matching=True` ACIKCA gecildiginde) bunu hangi DriftFinding
siddetine cevirecegine KENDISI karar verir. VARSAYILAN DAVRANIS
(`use_chain_matching=False`) v1.1 ile BIREBIR AYNI kalir -- bu SADECE
feature-flag'li bir pilot yoludur (gorev kisiti: "Default behavior
unchanged unless explicit flag enabled").

Neden checksum-zinciri onemli? v1.1'in "ayni alert_name + sonraki
timestamp" kontrolu, o alert icin TAMAMEN ALAKASIZ (ornegin farkli bir
sebeple, farkli bir esik degerine) bir normal apply'i bile yanlislikla
"takip eden onay" sayabilir -- bu YANLIS-POZITIF bir "cozuldu" sinyali
uretebilir. Checksum zinciri (`followup.old_checksum == emergency.new_checksum`)
bunu somut olarak dogrular: takip eden apply GERCEKTEN acil durumun
BIRAKTIGI dosya durumundan devam etmis mi, yoksa araya BASKA bir
degisiklik mi girmis?"""

from __future__ import annotations

from datetime import datetime
from typing import Any

CLOSURE_RESOLVED = "RESOLVED"
CLOSURE_BROKEN_CHAIN = "BROKEN_CHAIN"
CLOSURE_NO_FOLLOWUP = "NO_FOLLOWUP"

VALID_CLOSURES = (CLOSURE_RESOLVED, CLOSURE_BROKEN_CHAIN, CLOSURE_NO_FOLLOWUP)


def evaluate_chain_closure(entry: dict[str, Any], ledger_entries: list[dict[str, Any]]) -> str:
    """`entry`: vadesi gecmis (overdue) bir acil durum ledger girdisi.
    `ledger_entries`: TUM defter (bkz. `threshold_apply_core.load_ledger_entries`).
    Doner:

    - `RESOLVED`: ayni `alert_name` icin SONRAKI (`timestamp` daha
      buyuk) bir normal (`is_emergency=False`) girdi VAR VE o girdinin
      `old_checksum`'i bu `entry`'nin `new_checksum`'ine BIREBIR esit
      -- checksum zinciri KESINTISIZ, araya baska bir degisiklik
      GIRMEMIS.
    - `BROKEN_CHAIN`: ayni `alert_name` icin SONRAKI bir normal girdi
      VAR ama HICBIRININ `old_checksum`'i bu `entry`'nin `new_checksum`'i
      ile eslesmiyor -- zincir KESILMIS (araya baska, alakasiz bir
      degisiklik girmis olabilir). "Cozuldu" SAYILAMAZ ama TAMAMEN
      takipsiz de DEGIL.
    - `NO_FOLLOWUP`: ayni `alert_name` icin HICBIR sonraki normal girdi
      yok -- v1.1 ile BIREBIR AYNI durum."""
    alert_name = entry.get("alert_name")
    entry_ts = str(entry.get("timestamp", ""))
    entry_new_checksum = entry.get("new_checksum")

    same_alert_followups = [
        other
        for other in ledger_entries
        if other.get("alert_name") == alert_name
        and not other.get("is_emergency")
        and str(other.get("timestamp", "")) > entry_ts
    ]
    if not same_alert_followups:
        return CLOSURE_NO_FOLLOWUP

    chain_linked = any(f.get("old_checksum") == entry_new_checksum for f in same_alert_followups)
    return CLOSURE_RESOLVED if chain_linked else CLOSURE_BROKEN_CHAIN


def find_overdue_emergency_entries(ledger_entries: list[dict[str, Any]], *, now: datetime) -> list[dict[str, Any]]:
    """`now > retro_review_due_utc` olan TUM acil durum girdilerini
    (kapanis durumundan BAGIMSIZ) doner -- yalnizca `run_emergency_chain_trial.py`'nin
    v1.1/v1.2 karsilastirmasi icin kullanilir. `observability_drift_core.check_emergency_review_overdue_drift`'in
    KENDI (zaten test edilmis, v1.1) dongusunu DEGISTIRMEZ/TEKRAR
    KULLANMAZ -- kucuk bir mantik yinelemesi pahasina, o fonksiyonun
    davranisini KESINLIKLE etkilemeyecek sekilde IZOLE tutulur."""
    overdue: list[dict[str, Any]] = []
    for entry in ledger_entries:
        if not entry.get("is_emergency"):
            continue
        retro_raw = entry.get("retro_review_due_utc")
        if not retro_raw:
            continue
        try:
            retro_dt = datetime.fromisoformat(retro_raw)
        except (ValueError, TypeError):
            continue
        if now > retro_dt:
            overdue.append(entry)
    return overdue


def compare_matching_strategies(
    ledger_entries: list[dict[str, Any]], overdue_entries: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """`overdue_entries`'teki (vadesi zaten gecmis oldugu ONCEDEN
    belirlenmis) her acil durum girdisi icin, v1.1 (alert_name-only)
    ile v1.2 (checksum-zinciri) siniflandirmalarinin YAN YANA
    karsilastirmasini uretir -- `run_emergency_chain_trial.py`'nin
    `chain_eval.json`/`.md` kanit ciktisi tarafindan kullanilir."""
    rows: list[dict[str, Any]] = []
    for entry in overdue_entries:
        alert_name = entry.get("alert_name")
        entry_ts = str(entry.get("timestamp", ""))
        v1_1_has_followup = any(
            other.get("alert_name") == alert_name
            and not other.get("is_emergency")
            and str(other.get("timestamp", "")) > entry_ts
            for other in ledger_entries
        )
        v1_1_result = "RESOLVED" if v1_1_has_followup else "NO_FOLLOWUP"
        v1_2_result = evaluate_chain_closure(entry, ledger_entries)

        rows.append(
            {
                "proposal_id": entry.get("proposal_id"),
                "alert_name": alert_name,
                "v1_1_alert_name_only": v1_1_result,
                "v1_2_checksum_chain": v1_2_result,
                "outcome_differs": v1_1_result != v1_2_result,
            }
        )
    return rows
