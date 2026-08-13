"""Artimli (incremental) metrik aggregator icin kalici durum (state).

`model_gateway.metrics_aggregate.IncrementalAggregator`, her JSONL
dosyasinin ne kadarinin zaten okundugunu (byte offset) ve pencere-ici
(window-relevant) tamponlanmis olaylari bu modul araciligiyla diske
yazar/okur -- boylece surec yeniden baslatildiginda bile TUM gecmisi
bastan taramak yerine, onceki oturumdan kaldigi yerden devam edebilir.

Dosya kimligi (`file_identity`): dosya ADI yerine `(st_dev, st_ino)`
kullanilir -- bu, `JsonlAppendSink`'in rotasyon mekanizmasi (RENAME,
kopyalama degil) ile birlikte sorunsuz calisir: bir dosya rotate
edildiginde (adi degisir) icerigi/inode'u DEGISMEZ, bu yuzden offset
takibi rotasyona karsi OTOMATIK OLARAK dogru kalir -- ayri bir
"rotasyon algilama" mantigina gerek yoktur. Windows NTFS'de
`os.stat().st_ino`, CPython 3.5+'ta `GetFileInformationByHandle` ile
dolduruldugundan gercek, kalici bir dosya kimligidir (rename'den
etkilenmez).

Bozuk/eksik state dosyasi SERT bir hata DEGILDIR -- `load_state()` bu
durumda `None` doner, cagiran (aggregator) bunu "bos durumdan basla"
(kontrollu yeniden insa / auto-heal) olarak yorumlar -- veri kaybina
yol acmaz, yalnizca bir sonraki `refresh()` cagrisinin mevcut
dosyalari bastan okumasina (bir kereye mahsus) neden olur.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from model_gateway.metrics_sink import MetricEvent

logger = logging.getLogger("model_gateway.metrics_index")

STATE_VERSION = 1


@dataclass
class FileOffset:
    path: str  # yalnizca tanilama/debug icin -- kimlik icin kullanilmaz
    offset: int


@dataclass
class AggregatorState:
    window_minutes: int
    files: dict[str, FileOffset] = field(default_factory=dict)
    events: list[MetricEvent] = field(default_factory=list)
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": STATE_VERSION,
            "window_minutes": self.window_minutes,
            "updated_at": self.updated_at,
            "files": {
                key: {"path": fo.path, "offset": fo.offset} for key, fo in self.files.items()
            },
            "events": [
                {
                    "ts": e.ts,
                    "type": e.metric_type,
                    "name": e.name,
                    "labels": e.labels,
                    "value": e.value,
                }
                for e in self.events
            ],
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "AggregatorState":
        files = {
            str(key): FileOffset(path=str(v["path"]), offset=int(v["offset"]))
            for key, v in (data.get("files") or {}).items()
        }
        events = [MetricEvent.from_dict(e) for e in (data.get("events") or [])]
        return AggregatorState(
            window_minutes=int(data.get("window_minutes", 0)),
            files=files,
            events=events,
            updated_at=float(data.get("updated_at", 0.0)),
        )


def identity_from_stat(st: os.stat_result) -> str:
    """`file_identity()` ile AYNI format -- zaten elde bir `stat()`
    sonucu varsa (ornegin `IncrementalAggregator._ingest_new_lines()`
    icinde) ikinci bir `stat()` cagrisindan kacinmak icin ayri
    fonksiyon olarak disari acilir."""
    return f"{st.st_dev}:{st.st_ino}"


def file_identity(path: Path) -> str | None:
    """`(st_dev, st_ino)` tabanli kalici dosya kimligi. Dosya yoksa/stat
    basarisiz olursa `None` doner (SERT hata degil -- cagiran bunu
    "henuz yok" olarak yorumlar)."""
    try:
        st = path.stat()
    except OSError:
        return None
    return identity_from_stat(st)


def load_state(state_path: Path) -> AggregatorState | None:
    """State dosyasini okur. Eksik/bozuk/beklenmeyen sema -- HEPSI icin
    `None` doner (SERT hata degil), asla exception firlatmaz."""
    try:
        if not state_path.exists():
            return None
        with state_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
            logger.warning("metrics aggregator state semasi taniniyor degil -- yeniden insa ediliyor")
            return None
        return AggregatorState.from_dict(data)
    except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        logger.warning("metrics aggregator state dosyasi bozuk (%s) -- yeniden insa ediliyor (auto-heal)", exc)
        return None


def save_state(state_path: Path, state: AggregatorState) -> bool:
    """State dosyasini ATOMIK olarak yazar (tmp dosya + rename) -- yarim
    yazilmis bir state dosyasinin bir sonraki okumada bozuk gorunup
    auto-heal tetiklemesi disinda baska bir riski yoktur. Best-effort:
    basarisizlikta False doner, exception firlatmaz (state kaybı,
    yalnizca bir sonraki calisma icin performans maliyeti dogurur,
    dogrulugu etkilemez)."""
    try:
        state.updated_at = time.time()
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = state_path.with_suffix(state_path.suffix + f".tmp{os.getpid()}")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, ensure_ascii=False)
        os.replace(tmp_path, state_path)
        return True
    except OSError as exc:
        logger.warning("metrics aggregator state yazilamadi: %s", exc)
        return False
