"""Ajan presence anlik goruntusunun kalici (restart-sonrasi) saklanmasi --
minimal dilim (PLAN.md T39, BACKLOG.md B041, DECISIONS.md ADR-022).

**Kapsam (v0.1, BILEREK minimal):** SON bilinen `AgentPresence` anlik
goruntusu, `data/approvals/`/`data/audit/` ile AYNI JSONL append-only
desenine (ADR-009/ADR-016) yazilir -- her satir bir "yaz" olayidir, var
olan satirlar ASLA degistirilmez/silinmez. Baslangicta dosya BASTAN SONA
okunur; HER `agent_id` icin dosyadaki EN SON satir kazanir ("last write
wins" -- bkz. `load_latest()`).

**Bilinen sinirlama -- bu bir CACHE'tir, OTORITE DEGIL:** Sunucu
CALISIRKEN `HeartbeatTracker`'in bellek-ici durumu HER ZAMAN tek
dogruluk kaynagidir (timeout-tabanli offline dususu gibi TURETILMIS
mantik, diskteki DONUK bir anlik goruntude YOKTUR). Bu dosya YALNIZCA
sunucu SIFIRDAN baslarken "en son ne biliyorduk" icin bir baslangic
degeri saglar -- `app.py::_seed_heartbeat_from_presence_store()`,
persisted `last_heartbeat_ts`'i (ORIJINAL haliyle, "simdi" ile
DEGISTIRMEDEN) `HeartbeatTracker.record(ts=...)`'e verir; boylece
`resolve_state()`'in VAROLAN zaman-asimi mantigi, restart ONCESI/SONRASI
FARK ETMEKSIZIN AYNI sekilde calisir -- restart'tan bu yana
`timeout_seconds`'tan fazla gecmisse dogal olarak `offline` doner,
GECMEDIYSE son bilinen durum GECERLI SAYILIR (ozel bir "restart"
durum kodu YOKTUR).

**Cakisma cozumu kurali (BILEREK basit, v0.1):** Baslangicta tohumlama
YALNIZCA `HeartbeatTracker`'da HENUZ HICBIR kaydi OLMAYAN `agent_id`'ler
icin uygulanir -- DI ile ONCEDEN doldurulmus bir tracker'in (ornegin
testlerde) durumu ASLA SESSIZCE UZERINE YAZILMAZ."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ops_suite.schemas import AgentPresence

DEFAULT_PRESENCE_LOG_PATH = Path("data/presence/agent_presence.jsonl")


class PresenceStore:
    def __init__(self, log_path: str | Path = DEFAULT_PRESENCE_LOG_PATH) -> None:
        self._log_path = Path(log_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def log_path(self) -> Path:
        return self._log_path

    def append(self, presence: AgentPresence) -> None:
        """Bir anlik goruntu satiri EKLER (append-only) -- var olan
        satirlar ASLA degistirilmez/silinmez (ADR-009 ile ayni ilke)."""
        line = json.dumps(presence.to_dict(), ensure_ascii=False)
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def load_latest(self) -> dict[str, dict[str, Any]]:
        """Dosyayi BASTAN SONA okur, HER `agent_id` icin EN SON (dosyada
        en alttaki) kaydi doner -- "last write wins". Dosya yoksa/bozuksa
        BOS sozluk doner (hata FIRLATMAZ -- sunucu baslangicini asla
        engellemez)."""
        if not self._log_path.exists():
            return {}
        latest: dict[str, dict[str, Any]] = {}
        for line in self._log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            agent_id = record.get("agent_id")
            if agent_id:
                latest[agent_id] = record
        return latest
