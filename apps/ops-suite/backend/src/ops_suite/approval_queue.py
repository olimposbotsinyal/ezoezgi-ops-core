"""Onay kuyrugu kalici deposu (PLAN.md T23, DECISIONS.md ADR-016).

`approval_stub.py`'nin durumsuz `WAITING_APPROVAL` donusunu GERCEK,
sorgulanabilir bir kuyruga genellestirir -- `orchestrator.py`,
`approval_stub.py`, `risk_engine.py` HICBIRI DEGISTIRILMEZ (bkz.
`scripts/e2e_demo.py`'nin ayni "sar, dokunma" deseni).

`data/approvals/approval_queue.jsonl`'a append-only (ADR-009 deseni) iki
kayit turu yazilir: `SUBMITTED` (onay bekleyen bir task bildirildiginde)
ve `DECIDED` (onaylandi/reddedildiginde). `list_pending()` dosyayi
YENIDEN-OYNATARAK (replay) SUBMITTED-eksi-DECIDED farkini hesaplar --
ayrica bir "durum" alani TUTULMAZ, TEK dogruluk kaynagi append-only
gunluktur.

**Kapsam notu (ADR-016):** bu, ADR-011'in (finans yururlugu erteleme)
VEYA PLAN.md T20'nin (finans-ozel onay CLI simulasyonu, T19'a bagli)
YENIDEN ACILMASI DEGILDIR -- herhangi bir `high`/`irreversible` task
icin genel/yeniden-kullanilabilir altyapidir (T17/T18'in ayni istisnasi)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ops_suite.schemas import ApprovalQueueEntry

DEFAULT_APPROVAL_QUEUE_PATH = Path("data/approvals/approval_queue.jsonl")

RECORD_TYPE_SUBMITTED = "SUBMITTED"
RECORD_TYPE_DECIDED = "DECIDED"

DECISION_APPROVED = "approved"
DECISION_REJECTED = "rejected"
VALID_DECISIONS = (DECISION_APPROVED, DECISION_REJECTED)


class UnknownRequestIdError(KeyError):
    """`decide()` bilinmeyen (hic `submit()` edilmemis) bir `request_id`
    icin cagrildiginda firlatilir -- SESSIZCE bir kayit UYDURULMAZ."""


class AlreadyDecidedError(ValueError):
    """`decide()` ZATEN karara baglanmis bir `request_id` icin TEKRAR
    cagrildiginda firlatilir -- bir kararin SESSIZCE UZERINE YAZILMASINI
    ONLER."""


class ApprovalQueueStore:
    def __init__(self, queue_path: str | Path = DEFAULT_APPROVAL_QUEUE_PATH) -> None:
        self._queue_path = Path(queue_path)
        self._queue_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def queue_path(self) -> Path:
        return self._queue_path

    def _append(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False)
        with self._queue_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _read_all(self) -> list[dict[str, Any]]:
        if not self._queue_path.exists():
            return []
        records = []
        for line in self._queue_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records

    def submit(
        self,
        *,
        request_id: str,
        alias: str | None,
        task: str | None,
        risk_level: str | None,
        original_tr: str | None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Bir `WAITING_APPROVAL` task'ini kuyruga EKLER. Cagiran taraf
        (Ops Suite API katmani / `voice_bridge.py`) bunu, `orchestrator.handle_task()`
        `status="WAITING_APPROVAL"` dondurdugunde cagirir -- bu store'un
        KENDISI orchestrator'i hic bilmez/cagirmaz."""
        record = {
            "record_type": RECORD_TYPE_SUBMITTED,
            "request_id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "alias": alias,
            "task": task,
            "risk_level": risk_level,
            "original_tr": original_tr,
            "details": details or {},
        }
        self._append(record)
        return record

    def get_pending_entry(self, request_id: str) -> ApprovalQueueEntry | None:
        """`list_pending()`'ten TEK bir kaydi bulur -- yetkilendirme
        kararindan ONCE (`identity.py::authorize_decision`) gereken
        `risk_level`'i almak icin kullanilir. Bulunamazsa `None` doner
        (hata FIRLATMAZ -- cagiran taraf, `decide()`'in kendi
        `UnknownRequestIdError`'ini zaten firlatacaktir)."""
        for entry in self.list_pending():
            if entry.request_id == request_id:
                return entry
        return None

    def list_pending(self) -> list[ApprovalQueueEntry]:
        """Gunlugu BASTAN OYNATIR -- SUBMITTED'i, ayni `request_id`'ye
        sahip bir DECIDED kaydi VARSA cikarir. Bozuk/silinmis dosya =
        bos kuyruk (hata FIRLATMAZ)."""
        records = self._read_all()
        submitted: dict[str, dict[str, Any]] = {}
        decided_ids: set[str] = set()
        for r in records:
            if r.get("record_type") == RECORD_TYPE_SUBMITTED and "request_id" in r:
                submitted[r["request_id"]] = r
            elif r.get("record_type") == RECORD_TYPE_DECIDED and "request_id" in r:
                decided_ids.add(r["request_id"])

        pending = []
        for request_id, r in submitted.items():
            if request_id in decided_ids:
                continue
            pending.append(
                ApprovalQueueEntry(
                    request_id=request_id, alias=r.get("alias"), task=r.get("task"),
                    risk_level=r.get("risk_level"), original_tr=r.get("original_tr"),
                    submitted_at=r.get("timestamp", ""), details=r.get("details") or {},
                )
            )
        return pending

    def decide(
        self,
        request_id: str,
        decision: str,
        *,
        actor_id: str,
        auth_method: str,
        authority_source: str,
        decision_scope: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        """`request_id` icin bir DECIDED kaydi ekler. `request_id` hic
        `submit()` edilmemisse `UnknownRequestIdError`, ZATEN karara
        baglanmissa `AlreadyDecidedError` firlatir -- `decision`
        `VALID_DECISIONS` disindaysa `ValueError`.

        `actor_id`/`auth_method`/`authority_source`/`decision_scope`
        (PLAN.md T28, BACKLOG.md B044) -- cagiran taraf (Ops Suite API
        katmani) bunlari `identity.py::IdentityStore.authenticate()` +
        `authorize_decision()`'dan GERCEKTEN dogrulanmis bir kimlikten
        turetmis OLMALIDIR; bu store kendisi kimlik dogrulamasi YAPMAZ,
        yalnizca sonucu kalici kaydeder (tek sorumluluk ayrimi)."""
        if decision not in VALID_DECISIONS:
            raise ValueError(f"gecersiz decision: {decision!r} (gecerli: {VALID_DECISIONS})")

        records = self._read_all()
        submitted_ids = {r["request_id"] for r in records if r.get("record_type") == RECORD_TYPE_SUBMITTED and "request_id" in r}
        decided_ids = {r["request_id"] for r in records if r.get("record_type") == RECORD_TYPE_DECIDED and "request_id" in r}

        if request_id not in submitted_ids:
            raise UnknownRequestIdError(f"bilinmeyen request_id: {request_id!r} -- hic submit() edilmemis")
        if request_id in decided_ids:
            raise AlreadyDecidedError(f"request_id={request_id!r} ZATEN karara baglanmis -- tekrar decide() edilemez")

        record = {
            "record_type": RECORD_TYPE_DECIDED,
            "request_id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "decision": decision,
            "actor_id": actor_id,
            "auth_method": auth_method,
            "authority_source": authority_source,
            "decision_scope": decision_scope,
            "note": note,
        }
        self._append(record)
        return record
