"""Gozlemlenebilirlik drift tespiti -- CLI.

`observability_drift_core.py`'nin saf karsilastirma mantigini gercek
girdilerle besler:
  - metrik semasi: `data/metrics/*.jsonl`'deki SON pencere (varsayilan
    24 saat) icindeki olaylardan cikarilir (sentetik `synthetic="true"`
    olaylar DAHIL edilir -- semaya (ad/tip/etiket anahtarlari) bakiyoruz,
    degerlere/orana degil; `always_allowed_extra_labels` zaten
    "synthetic" etiketini drift saymiyor).
  - alert kurallari: `infra/monitoring/prometheus/model_gateway_alerts.yaml`'nin
    GUNCEL SHA256'si.
  - config varsayilanlari: `GatewayConfig` dataclass alan varsayilanlari
    DOGRUDAN (ortam degiskeninden BAGIMSIZ) okunur -- `load_config()`
    DEGIL, cunku amac KOD-seviyesi drift'i (birisi varsayilan degeri
    kaynak dosyada degistirdi mi) yakalamaktir, calisma-zamani env
    override'ini degil.

Cikis kodu: 0 (drift yok), 1 (kritik olmayan drift), 2 (kritik drift).
ASLA fabrike edilmis 0 donmez.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT_DEFAULT = Path("d:/Projects/ezoezgi-ops")
DEFAULT_MANIFEST_PATH = "infra/monitoring/baseline/metrics_manifest_v1.json"
DEFAULT_CHECKSUM_PATH = "infra/monitoring/baseline/alerts_checksum_v1.txt"
DEFAULT_ALERTS_PATH = "infra/monitoring/prometheus/model_gateway_alerts.yaml"
DEFAULT_LEDGER_PATH = "infra/monitoring/baseline/approved_checksums_ledger.jsonl"


def load_baseline_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_baseline_checksum(path: Path) -> str:
    """`alerts_checksum_v1.txt`'ten, `#` ile baslayan yorum satirlarini
    ve bos satirlari atlayarak ILK gercek (hex digest) satiri okur."""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped
    raise ValueError(f"{path}: gecerli bir checksum satiri bulunamadi")


def compute_file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def observe_metrics_schema_from_jsonl(jsonl_path: Path, window_minutes: int) -> dict[str, dict[str, Any]]:
    """Son `window_minutes` icindeki JSONL olaylarindan {ad: {type, labels}}
    semasini cikarir -- her metrik adi icin GORULEN TUM etiket anahtarlarinin
    birlesimi (union) alinir (farkli olaylarda farkli alt-kumeler olabilir)."""
    from model_gateway import metrics_aggregate

    events = metrics_aggregate.read_recent_events(jsonl_path, window_minutes)
    schema: dict[str, dict[str, Any]] = {}
    for e in events:
        entry = schema.setdefault(e.name, {"type": e.metric_type, "labels": set()})
        entry["labels"].update(e.labels.keys())
    return {name: {"type": v["type"], "labels": sorted(v["labels"])} for name, v in schema.items()}


def read_config_source_defaults(repo_root: Path) -> dict[str, bool]:
    """`load_config()`'in KAYNAK-KODDA sabit-kodlanmis varsayilan
    degerlerini okur.

    ONEMLI: `GatewayConfig` dataclass'inin KENDI alan varsayilanlari
    KULLANILMAZ -- `remote_enabled` dahil bircok alanin dataclass
    seviyesinde HICBIR varsayilani yoktur (dogrudan constructor
    cagrisinda ZORUNLU alanlardir); GERCEK varsayilan davranis yalnizca
    `load_config()`'in `_get_bool(..., False)` gibi sabit-kodlanmis
    cagrilarinda tanimlidir. Bu yuzden `load_config()` GERCEKTEN
    cagirilir -- ama CAGIRAN surecin kendi ortam degiskenlerinden
    (REMOTE_ENABLED/OLLAMA_CPU_VERIFY_STRICT zaten set edilmis olabilir)
    ETKILENMEMESI icin bu iki anahtar GECICI olarak temizlenir (finally
    ile GERI YUKLENIR) ve var olmayan bir yaml yoluna isaret edilir --
    boylece yalnizca kaynak koddaki LITERAL varsayilan degerler
    gozlenir, calisma-zamani/yaml override'lari DEGIL."""
    import os

    sys.path.insert(0, str(repo_root / "services" / "model-gateway" / "src"))
    from model_gateway.config import load_config

    isolate_keys = ["REMOTE_ENABLED", "OLLAMA_CPU_VERIFY_STRICT"]
    saved = {k: os.environ.pop(k, None) for k in isolate_keys}
    try:
        config = load_config(yaml_path=Path("__nonexistent_drift_check_sentinel__.yaml"))
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v

    return {
        "remote_enabled": config.remote_enabled,
        "ollama_cpu_verify_strict": config.ollama_cpu_verify_strict,
    }


def run_drift_detection(
    *,
    repo_root: Path,
    manifest_path: Path,
    checksum_path: Path,
    alerts_path: Path,
    jsonl_path: Path,
    window_minutes: int,
    ledger_path: Path | None = None,
    use_chain_matching: bool = False,
) -> tuple[list, str]:
    from observability_drift_core import (
        check_alert_rules_checksum_drift,
        check_emergency_review_overdue_drift,
        check_remote_default_drift,
        check_strict_flag_drift,
        compare_metrics_schema,
    )
    from threshold_apply_core import load_approved_checksums, load_ledger_entries

    baseline = load_baseline_manifest(manifest_path)
    baseline_checksum = load_baseline_checksum(checksum_path)

    findings = []

    observed_schema = observe_metrics_schema_from_jsonl(jsonl_path, window_minutes)
    findings.extend(compare_metrics_schema(observed_schema, baseline))

    observed_checksum = compute_file_sha256(alerts_path)
    # `ledger_path=None` (varsayilan) -- ONAYLI DEGISIKLIK YOK sayilir, eski
    # (Commit R) davranisiyla BIREBIR ayni: baseline'dan HERHANGI bir sapma
    # dogrudan CRITICAL'dir. Bir ledger yolu verildiginde (bkz. main()'deki
    # varsayilan CLI davranisi), o defterde KAYITLI (yani GERCEKTEN
    # `apply_threshold_proposal.ps1 -Apply` ile onaylanip uygulanmis) bir
    # checksum CRITICAL'e DUSURULMEZ (bkz. check_alert_rules_checksum_drift).
    ledger_entries = load_ledger_entries(ledger_path) if ledger_path is not None else []
    approved_checksums = load_approved_checksums(ledger_path) if ledger_path is not None else []
    findings.append(check_alert_rules_checksum_drift(observed_checksum, baseline_checksum, approved_checksums))
    # v1.1: ledger'daki HER acil durum (APPROVE_EMERGENCY) girdisi icin,
    # retroaktif inceleme vadesi gecmis VE takip eden normal onay yoksa
    # CRITICAL -- bkz. check_emergency_review_overdue_drift.
    # v1.2 PILOT: `use_chain_matching=False` (varsayilan) -- v1.1 ile
    # BIREBIR AYNI davranis. True ise (GOV_EMERGENCY_CHAIN_MATCHING=1
    # veya --use-chain-matching), checksum-zinciri sureklilik kontrolu
    # devreye girer (bkz. emergency_chain_core.py).
    findings.extend(
        check_emergency_review_overdue_drift(
            ledger_entries, now=datetime.now(timezone.utc), use_chain_matching=use_chain_matching
        )
    )

    config_defaults = read_config_source_defaults(repo_root)
    findings.append(check_remote_default_drift(config_defaults["remote_enabled"], baseline))
    findings.append(check_strict_flag_drift(config_defaults["ollama_cpu_verify_strict"], baseline))

    window_label = f"son {window_minutes} dakika (metrik semasi) + guncel dosya durumu (alert/config)"
    return findings, window_label


def main() -> int:
    parser = argparse.ArgumentParser(description="Model gateway gozlemlenebilirlik drift tespiti")
    parser.add_argument("--repo-root", default=str(REPO_ROOT_DEFAULT))
    parser.add_argument("--window-minutes", type=int, default=24 * 60)
    parser.add_argument("--manifest-path", default=None)
    parser.add_argument("--checksum-path", default=None)
    parser.add_argument("--alerts-path", default=None)
    parser.add_argument("--jsonl-path", default=None)
    parser.add_argument("--ledger-path", default=None)
    parser.add_argument(
        "--use-chain-matching", action="store_true", default=None,
        help="v1.2 PILOT: checksum-zinciri sureklilik kontrolu (varsayilan: GOV_EMERGENCY_CHAIN_MATCHING "
        "ortam degiskeni, o da yoksa KAPALI/0)",
    )
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    repo_root = Path(args.repo_root)
    manifest_path = Path(args.manifest_path) if args.manifest_path else repo_root / DEFAULT_MANIFEST_PATH
    checksum_path = Path(args.checksum_path) if args.checksum_path else repo_root / DEFAULT_CHECKSUM_PATH
    alerts_path = Path(args.alerts_path) if args.alerts_path else repo_root / DEFAULT_ALERTS_PATH
    ledger_path = Path(args.ledger_path) if args.ledger_path else repo_root / DEFAULT_LEDGER_PATH
    # `--use-chain-matching` acikca gecilmediyse (None), GOV_EMERGENCY_CHAIN_MATCHING
    # ortam degiskenine bakilir -- o da yoksa varsayilan KAPALI (0/False).
    # Gorev kisiti: "Default behavior unchanged unless explicit flag enabled".
    use_chain_matching = args.use_chain_matching
    if use_chain_matching is None:
        use_chain_matching = os.environ.get("GOV_EMERGENCY_CHAIN_MATCHING", "0") == "1"

    if args.jsonl_path:
        jsonl_path = Path(args.jsonl_path)
    else:
        sys.path.insert(0, str(repo_root / "services" / "model-gateway" / "src"))
        from model_gateway.config import load_config

        jsonl_path = Path(load_config().metrics_jsonl_path)

    from observability_drift_core import overall_drift_exit_code, render_drift_report_md, write_drift_report_json

    findings, window_label = run_drift_detection(
        repo_root=repo_root,
        manifest_path=manifest_path,
        checksum_path=checksum_path,
        alerts_path=alerts_path,
        jsonl_path=jsonl_path,
        window_minutes=args.window_minutes,
        ledger_path=ledger_path,
        use_chain_matching=use_chain_matching,
    )

    generated_at = datetime.now(timezone.utc).isoformat()
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = repo_root / "reports" / f"drift_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    report_md = render_drift_report_md(findings, generated_at=generated_at, window_label=window_label)
    (out_dir / "drift_report.md").write_text(report_md, encoding="utf-8")
    write_drift_report_json(findings, out_dir / "drift_report.json", generated_at=generated_at, window_label=window_label)

    print(report_md)
    print(f"evidence_dir={out_dir}")
    return overall_drift_exit_code(findings)


if __name__ == "__main__":
    sys.exit(main())
