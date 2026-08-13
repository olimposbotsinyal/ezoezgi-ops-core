"""`run_promotion_evidence_pack.ps1`'in SON adimi -- `.ps1`'in SIRAYLA
cagirdigi python CLI adimlarindan topladigi sonuclari (`--steps-json-path`
-- bir JSON dizi, her eleman `{"step","exit_code","status","evidence_path"}`)
+ auto-rollback OZ-taramasini (`promotion_evidence_pack_core.collect_auto_rollback_evidence`)
birlestirip `reports/promotion_candidate_<UTC>/runpack_index.json`+
`runpack_summary.md` yazar.

Bu script'in KENDISI hicbir kalici durumu degistirmez -- yalnizca
ONCEDEN calisan adimlarin ciktilarini OKUR/BIRLESTIRIR."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT_DEFAULT = Path("d:/Projects/ezoezgi-ops")


def main() -> int:
    parser = argparse.ArgumentParser(description="Promotion evidence runpack bundler (son adim)")
    parser.add_argument("--repo-root", default=str(REPO_ROOT_DEFAULT))
    parser.add_argument("--steps-json-path", required=True, help="Onceki adimlarin sonuc listesini iceren JSON dosyasi")
    parser.add_argument("--incident-id", default=None)
    parser.add_argument("--evaluator-exit-code", type=int, default=None)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    repo_root = Path(args.repo_root)
    sys.path.insert(0, str(repo_root / "scripts" / "ops"))
    from promotion_evidence_pack_core import build_runpack_index, collect_auto_rollback_evidence, write_runpack_bundle

    try:
        # `utf-8-sig`: `.ps1` orkestratoru bu dosyayi Windows PowerShell
        # 5.1'in `Set-Content -Encoding utf8`'iyle yazar -- bu, (PS 7+'in
        # aksine) DAIMA bir UTF-8 BOM ekler; `utf-8-sig` BOM varsa
        # SESSIZCE atar, YOKSA (ornegin testlerde BOM'suz yazilmis bir
        # dosya) da SORUNSUZ okur -- iki durumda da dogru calisir.
        steps = json.loads(Path(args.steps_json_path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"HATA: adim sonuclari dosyasi okunamadi/ayristirilamadi ({args.steps_json_path}): {exc}", file=sys.stderr)
        return 2
    if not isinstance(steps, list):
        print(f"HATA: adim sonuclari bir liste olmali, gozlenen tip: {type(steps).__name__}", file=sys.stderr)
        return 2

    on, off, total = collect_auto_rollback_evidence(repo_root)
    steps.append(
        {
            "step": "auto_rollback_evidence_scan",
            "exit_code": 0,
            "status": "OBSERVATIONAL",
            "evidence_path": None,
            "notes": f"GOZLEMSEL tarama (gercek bir apply/rollback TETIKLEMEZ) -- "
            f"auto_rollback ON={on}, OFF={off} (toplam {total} apply_report.json)",
        }
    )

    generated_at = datetime.now(timezone.utc).isoformat()
    index_payload = build_runpack_index(
        steps, generated_at=generated_at, incident_id=args.incident_id, evaluator_exit_code=args.evaluator_exit_code
    )
    paths = write_runpack_bundle(index_payload, Path(args.output_dir))

    print(f"auto_rollback_evidence: ON={on} OFF={off} (toplam {total} apply_report.json)")
    print(f"promotion_readiness_outlook={index_payload['promotion_readiness_outlook']}")
    print(f"runpack_index={paths['index']}")
    print(f"runpack_summary={paths['summary']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
