"""Run the NAD R23 acquisition ladder without serial human intervention.

Primary: official bulk TXT.zip when present. Secondary: official ArcGIS
FeatureServer partitions. Tertiary: official WFS capability (probe only until
the adapter is explicitly enabled). A failed primary never prevents the
secondary from starting.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .nad_feature_server import FeatureServerError, NADFeatureServerIngest
from .nad_r23 import NADR23Ingest
from .nad_transport import NADArcGISClient, NADTransportError


def activate(workdir: Path, bulk: Path | None = None, states: list[str] | None = None) -> dict:
    result = {"status": "BULK_TRANSPORT_DEGRADED", "attempts": [], "source_ingest_proven": False, "source_coverage": "UNVERIFIED", "us_51_coverage_complete": False}
    if bulk:
        try:
            result["bulk"] = NADR23Ingest(workdir).ingest(bulk)
            result["status"] = "NAD_R23_SOURCE_COMPLETE_PROVEN"
            result["source_ingest_proven"] = True
            result["source_coverage"] = "REQUIRES_REGISTRY_RECONCILIATION"
            return result
        except (OSError, ValueError) as exc:
            result["attempts"].append({"transport": "official_bulk", "error": str(exc)})
    try:
        probe = NADArcGISClient().probe()
        result["arcgis_probe"] = probe
        result["attempts"].append({"transport": "official_arcgis", "status": probe.get("status")})
        result["feature_server"] = NADFeatureServerIngest(workdir).ingest(states)
        result["status"] = "NAD_R23_SOURCE_COMPLETE_PROVEN"
        result["source_ingest_proven"] = True
        result["source_coverage"] = "REQUIRES_REGISTRY_RECONCILIATION"
        return result
    except (NADTransportError, FeatureServerError, OSError, ValueError) as exc:
        result["attempts"].append({"transport": "official_arcgis", "error": str(exc)})
    result["wfs"] = {"status": "READY_TO_PROBE", "url": NADArcGISClient().wfs_capabilities_url()}
    result["status"] = "PARTIALLY_OPERATIONAL_CONTINUING_AROUND_DEGRADED_SHARDS"
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--bulk", type=Path)
    parser.add_argument("--state", action="append")
    args = parser.parse_args()
    print(json.dumps(activate(args.workdir, args.bulk, args.state), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
