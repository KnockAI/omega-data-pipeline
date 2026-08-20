"""National NAD reconciliation and coverage matrix.

This is deliberately manifest-driven: it never invents a count when a
partition has not been acquired. Missing partitions are reported as
``DEGRADED_PENDING_ACQUISITION`` so the orchestrator can keep working them.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

STATES = "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()


def build_matrix(manifest: dict[str, Any] | None) -> list[dict[str, Any]]:
    counts = (manifest or {}).get("records_by_state", {})
    rows = []
    for state in STATES:
        n = counts.get(state)
        rows.append({
            "state": state,
            "nad_records": n,
            "state_source_records": None,
            "canonical_records": n,
            "state_override_status": "NAD_BASELINE",
            "coverage_delta": None,
            "unit_delta": None,
            "conflict_rate": None,
            "source_precedence": "NAD_R23",
            "freshness": manifest.get("compiled") if manifest else None,
            "rights": "CLEAR_FOR_NON_MAILING_USE",
            "refresh_status": "ACTIVE" if n is not None else "DEGRADED_PENDING_ACQUISITION",
        })
    return rows


def write_matrix(manifest_path: Path | None, output: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text()) if manifest_path and manifest_path.exists() else None
    matrix = build_matrix(manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"source": "usdot_nad_r23", "matrix": matrix}, indent=2, sort_keys=True) + "\n")
    return {"states": len(matrix), "acquired_states": sum(row["nad_records"] is not None for row in matrix), "output": str(output)}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(write_matrix(args.manifest, args.output), indent=2))
