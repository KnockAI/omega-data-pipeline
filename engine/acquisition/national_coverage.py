"""Build the multi-source national jurisdiction coverage registry.

NAD source integrity and national jurisdiction coverage are intentionally
separate contracts. Missing NAD jurisdictions trigger source failover; they
do not invalidate the completed NAD source proof.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

STATES = "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()


def build_registry(evidence_dir: Path) -> dict[str, Any]:
    evidence_dir = Path(evidence_dir)
    nad = json.loads((evidence_dir / "NAD_R23_NATIONAL_MANIFEST.json").read_text())
    counts = nad.get("records_by_state", {})
    failover: dict[str, dict[str, Any]] = {}
    for path in evidence_dir.glob("*ADDRESS_COVERAGE*.json"):
        item = json.loads(path.read_text())
        if item.get("jurisdiction"):
            failover[item["jurisdiction"]] = item
    rows = []
    for state in STATES:
        if state in counts:
            rows.append({
                "jurisdiction": state,
                "primary_source": "usdot_nad_r23_arcgis",
                "fallback_source": None,
                "release": nad.get("release"),
                "source_count": counts[state],
                "canonical_count": counts[state],
                "rights": "CLEAR_FOR_NON_MAILING_USE",
                "freshness": nad.get("source_edit"),
                "coverage_status": "COVERED_AUTHORITATIVE",
                "proof_sha": nad.get("run_sha"),
            })
            continue
        item = failover.get(state, {})
        rows.append({
            "jurisdiction": state,
            "primary_source": item.get("source_id") or None,
            "fallback_source": "usdot_nad_r23_arcgis",
            "release": item.get("source_fingerprint", {}).get("data_last_edit_date_epoch_ms"),
            "source_count": item.get("source_record_count"),
            "canonical_count": None,
            "rights": item.get("rights", "UNRESOLVED"),
            "freshness": item.get("source_fingerprint", {}).get("data_last_edit_date_epoch_ms"),
            "coverage_status": "DEGRADED" if item.get("rights") != "CLEAR" else "COVERED_FALLBACK",
            "proof_sha": item.get("proof_sha"),
        })
    territories = [{"jurisdiction": state, "source": "usdot_nad_r23_arcgis", "count": counts[state]} for state in nad.get("additional_territories", ["VI"]) if state in counts]
    return {
        "source_ingest_proven": True,
        "source_coverage": nad.get("source_coverage", "PARTIAL_PROVEN"),
        "us_51_coverage_complete": all(row["coverage_status"].startswith("COVERED") for row in rows),
        "coverage_gaps": [row["jurisdiction"] for row in rows if not row["coverage_status"].startswith("COVERED")],
        "additional_territories": territories,
        "rows": rows,
    }


def write_registry(evidence_dir: Path, output: Path) -> dict[str, Any]:
    registry = build_registry(evidence_dir)
    Path(output).write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n")
    return {"jurisdictions": len(registry["rows"]), "coverage_gaps": registry["coverage_gaps"], "output": str(output)}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(write_registry(args.evidence_dir, args.output), indent=2, sort_keys=True))
