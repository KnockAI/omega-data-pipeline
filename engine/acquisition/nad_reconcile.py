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
    source_complete = bool((manifest or {}).get("source_integrity_proven"))
    coverage_gaps = set((manifest or {}).get("coverage_gaps", []))
    rows = []
    for state in STATES:
        n = counts.get(state)
        rows.append({
            "state": state,
            "nad_records": n,
            "state_source_records": None,
            "canonical_records": n,
            "state_override_status": "NAD_BASELINE" if n is not None else "FAILOVER_REQUIRED",
            "coverage_delta": None,
            "unit_delta": None,
            "conflict_rate": None,
            "source_precedence": "NAD_R23",
            "freshness": manifest.get("compiled") if manifest else None,
            "rights": "CLEAR_FOR_NON_MAILING_USE",
            "refresh_status": "ACTIVE" if n is not None else "DEGRADED_PENDING_ACQUISITION",
            "coverage_status": "COVERED_AUTHORITATIVE" if n is not None else ("DEGRADED" if state in coverage_gaps else "UNRESOLVED"),
        })
    return rows


def write_matrix(manifest_path: Path | None, output: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text()) if manifest_path and manifest_path.exists() else None
    matrix = build_matrix(manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "usdot_nad_r23",
        "source_ingest_proven": bool((manifest or {}).get("source_integrity_proven")),
        "source_coverage": (manifest or {}).get("source_coverage", "PARTIAL"),
        "us_51_coverage_complete": not any(row["nad_records"] is None for row in matrix),
        "coverage_gaps": (manifest or {}).get("coverage_gaps", []),
        "additional_territories": (manifest or {}).get("additional_territories", []),
        "matrix": matrix,
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return {"states": len(matrix), "acquired_states": sum(row["nad_records"] is not None for row in matrix), "output": str(output), "source_ingest_proven": payload["source_ingest_proven"], "us_51_coverage_complete": payload["us_51_coverage_complete"]}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(write_matrix(args.manifest, args.output), indent=2))
