"""Official NAD R23 transport failover.

The federal TXT distribution is the preferred snapshot.  This module keeps
the official ArcGIS service and its optional Create Replica/WFS transports
available when that artifact is degraded.  It never selects a mirror and it
fails closed on metadata drift or an unverified response.

The transport emits a CSV understood by :class:`NADR23Ingest`; it does not
promote data itself.  Promotion, rollback, and the non-mailing-list guard
remain in ``nad_r23.py``.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

OFFICIAL_FEATURESERVER = (
    "https://services.arcgis.com/xOi1kZaI0eWDREZv/ArcGIS/rest/services/"
    "Address_Points_from_National_Address_Database_view/FeatureServer"
)


class NADTransportError(RuntimeError):
    pass


def _json_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class NADArcGISClient:
    """Small, injectable client for the official NAD FeatureServer."""

    def __init__(self, service_url: str = OFFICIAL_FEATURESERVER,
                 opener: Callable[..., Any] | None = None, timeout: int = 30,
                 retries: int = 3):
        if not service_url.startswith("https://services.arcgis.com/"):
            raise ValueError("NAD transport accepts only the official ArcGIS host")
        self.service_url = service_url.rstrip("/")
        self.opener = opener or urllib.request.urlopen
        self.timeout = timeout
        self.retries = retries

    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        target = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                response = self.opener(target, timeout=self.timeout)
                raw = response.read() if hasattr(response, "read") else response
                payload = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
                if not isinstance(payload, dict) or "error" in payload:
                    raise NADTransportError(f"official NAD response error: {payload.get('error', payload)}")
                return payload
            except Exception as exc:  # retry transient transport/provider failures
                last = exc
                if attempt + 1 < self.retries:
                    time.sleep(0.05 * (2 ** attempt))
        raise NADTransportError(f"official NAD request failed after {self.retries} attempts: {last}")

    def metadata(self) -> tuple[dict[str, Any], dict[str, Any]]:
        service = self._get(self.service_url, {"f": "json"})
        layer = self._get(self.service_url + "/0", {"f": "json"})
        return service, layer

    def probe(self) -> dict[str, Any]:
        service, layer = self.metadata()
        capabilities = str(layer.get("capabilities", service.get("capabilities", ""))).lower()
        supported = str(layer.get("supportedQueryFormats", "")).lower()
        can_query = "query" in capabilities or bool(layer.get("supportsAdvancedQueries"))
        can_replica = bool(layer.get("supportsCreateReplica")) or "create replica" in capabilities
        result = {
            "source": "usdot_nad_r23",
            "transport": "official_arcgis_featureserver",
            "service_url": self.service_url,
            "layer_url": self.service_url + "/0",
            "service_fingerprint": _json_hash(service),
            "layer_fingerprint": _json_hash(layer),
            "compiled_release": "2026-06-30",
            "can_query": can_query,
            "can_create_replica": can_replica,
            "supported_query_formats": supported,
            "max_record_count": layer.get("maxRecordCount"),
            "fields": [f.get("name") for f in layer.get("fields", [])],
            "source_editing_info": layer.get("editingInfo", service.get("editingInfo")),
            "status": "READY" if can_query else "HOLD_UNSUPPORTED",
        }
        return result

    def create_replica_capability(self, probe: dict[str, Any]) -> bool:
        """Return metadata-backed capability only; no replica is created implicitly."""
        return bool(probe.get("can_create_replica"))

    def wfs_capabilities_url(self) -> str:
        """ArcGIS's official WFS discovery URL; caller must probe before use."""
        return self.service_url + "/WFSServer?service=WFS&request=GetCapabilities"

    def query_ids(self, where: str = "1=1") -> list[int]:
        layer = self.service_url + "/0/query"
        result = self._get(layer, {"f": "json", "where": where, "returnIdsOnly": "true"})
        ids = result.get("objectIds")
        if not isinstance(ids, list):
            raise NADTransportError("official NAD ID response missing objectIds")
        return [int(x) for x in ids]

    def query_chunk(self, ids: list[int]) -> list[dict[str, Any]]:
        if not ids:
            return []
        layer = self.service_url + "/0/query"
        result = self._get(layer, {"f": "json", "objectIds": ",".join(map(str, ids)),
                                   "outFields": "*", "returnGeometry": "true"})
        features = result.get("features")
        if not isinstance(features, list):
            raise NADTransportError("official NAD feature response missing features")
        rows: list[dict[str, Any]] = []
        for feature in features:
            attrs = feature.get("attributes") if isinstance(feature, dict) else None
            if not isinstance(attrs, dict):
                raise NADTransportError("official NAD feature missing attributes")
            geometry = feature.get("geometry") or {}
            if geometry.get("y") is not None:
                attrs.setdefault("LATITUDE", geometry.get("y"))
            if geometry.get("x") is not None:
                attrs.setdefault("LONGITUDE", geometry.get("x"))
            rows.append({str(k): v for k, v in attrs.items()})
        return rows

    def acquire_csv(self, output: Path, where: str = "1=1", chunk_size: int = 1800) -> dict[str, Any]:
        """Acquire a bounded official partition and verify metadata did not drift."""
        before = self.probe()
        if not before["can_query"]:
            raise NADTransportError("official NAD FeatureServer query capability unavailable")
        ids = self.query_ids(where)
        output.parent.mkdir(parents=True, exist_ok=True)
        rows = 0
        with output.open("w", encoding="utf-8", newline="") as handle:
            writer = None
            for start in range(0, len(ids), chunk_size):
                batch = self.query_chunk(ids[start:start + chunk_size])
                if batch and writer is None:
                    writer = csv.DictWriter(handle, fieldnames=sorted({k for row in batch for k in row}), extrasaction="ignore")
                    writer.writeheader()
                if writer:
                    for row in batch:
                        writer.writerow(row); rows += 1
        after = self.probe()
        if before["layer_fingerprint"] != after["layer_fingerprint"] or before["service_fingerprint"] != after["service_fingerprint"]:
            output.unlink(missing_ok=True)
            raise NADTransportError("official NAD metadata drifted during acquisition; partition discarded")
        return {"status": "ACQUIRED", "where": where, "object_ids": len(ids), "records": rows,
                "probe_before": before, "probe_after": after, "output": str(output),
                "source": self.service_url}


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Probe/acquire official NAD FeatureServer failover")
    parser.add_argument("--service-url", default=OFFICIAL_FEATURESERVER)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--where", default="1=1")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    client = NADArcGISClient(args.service_url)
    result = client.probe() if args.probe or args.output is None else client.acquire_csv(args.output, args.where)
    print(json.dumps(result, indent=2, sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
