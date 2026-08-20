"""Official USDOT NAD R23 ArcGIS failover transport.

The bulk TXT distribution remains the preferred release artifact.  This module
keeps national acquisition moving when that artifact is unavailable by using
the official NAD FeatureServer, with IDs-first queries and bounded object-id
fetches.  It deliberately does not claim a release is complete until every
requested partition is checkpointed and the source edit timestamp is stable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .nad_r23 import canonicalize

SOURCE_ID = "usdot_nad_r23_arcgis"
RELEASE = "National Address Database Release 23"
COMPILED_DATE = "2026-06-30"
LAYER_URL = "https://services.arcgis.com/xOi1kZaI0eWDREZv/ArcGIS/rest/services/Address_Points_from_National_Address_Database_view/FeatureServer/0"
MAX_OBJECT_IDS = 2000
QUERY_PATH = "/query"
DEFAULT_STATES = (
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY"
).split()


class FeatureServerError(RuntimeError):
    pass


def _atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


class FeatureServerClient:
    def __init__(self, layer_url: str = LAYER_URL, *, opener: Callable[..., Any] | None = None,
                 retries: int = 4, sleep: Callable[[float], None] = time.sleep):
        self.layer_url = layer_url.rstrip("/")
        self.opener = opener or urllib.request.urlopen
        self.retries = retries
        self.sleep = sleep

    def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        url = endpoint + "?" + urllib.parse.urlencode({**params, "f": "json"})
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with self.opener(url, timeout=60) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if "error" in payload:
                    code = int(payload["error"].get("code", 0))
                    if code in (429, 500, 502, 503, 504) and attempt < self.retries:
                        # ArcGIS publishes a per-minute request-unit quota and
                        # explicitly asks clients to retry 429s after 60s.
                        # Honor that window instead of exhausting fast retries
                        # and turning throttling into a false hard failure.
                        self.sleep(60 if code == 429 else min(30, 2 ** attempt)); continue
                    raise FeatureServerError(str(payload["error"]))
                return payload
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code not in (429, 500, 502, 503, 504) or attempt >= self.retries:
                    raise FeatureServerError(f"HTTP {exc.code}") from exc
                self.sleep(60 if exc.code == 429 else min(30, 2 ** attempt))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                last = exc
                if attempt >= self.retries:
                    raise FeatureServerError(str(exc)) from exc
                self.sleep(min(30, 2 ** attempt))
        raise FeatureServerError(str(last))

    def _request_layer(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request(self.layer_url, params or {})

    def _request_query(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._request(self.layer_url + QUERY_PATH, params)

    def metadata(self) -> dict[str, Any]:
        return self._request_layer()

    def ids(self, where: str) -> list[int]:
        payload = self._request_query({"where": where, "returnIdsOnly": "true"})
        return [int(value) for value in payload.get("objectIds", [])]

    def records(self, object_ids: list[int], *, out_fields: str = "*") -> list[dict[str, Any]]:
        if len(object_ids) > MAX_OBJECT_IDS:
            raise ValueError("ArcGIS objectIds request exceeds 2000-record ceiling")
        if not object_ids:
            return []
        payload = self._request_query({"objectIds": ",".join(str(i) for i in object_ids),
                             "outFields": out_fields, "returnGeometry": "true"})
        return [dict(feature.get("attributes", {}), **({"__geometry": feature.get("geometry")} if feature.get("geometry") else {}))
                for feature in payload.get("features", [])]

    def query_page(self, *, where: str, last_object_id: int, upper_object_id: int | None = None,
                   out_fields: str = "*") -> list[dict[str, Any]]:
        clause = f"({where}) AND OBJECTID > {int(last_object_id)}"
        if upper_object_id is not None:
            clause += f" AND OBJECTID <= {int(upper_object_id)}"
        payload = self._request_query({"where": clause, "outFields": out_fields,
                                       "returnGeometry": "true", "orderByFields": "OBJECTID ASC",
                                       "resultRecordCount": MAX_OBJECT_IDS})
        return [dict(feature.get("attributes", {}), **({"__geometry": feature.get("geometry")} if feature.get("geometry") else {}))
                for feature in payload.get("features", [])]

    def statistics(self) -> dict[str, Any]:
        # This public NAD view accepts countOnly and single min/max statistics,
        # but rejects a combined count/min/max outStatistics request. Keep one
        # stable result shape while using the service's supported operations.
        count_payload = self._request_query({"where": "1=1", "returnCountOnly": "true"})
        values: dict[str, Any] = {"object_count": count_payload.get("count")}
        for kind, name in (("min", "min_object_id"), ("max", "max_object_id")):
            payload = self._request_query({"where": "1=1", "outStatistics": json.dumps([{
                "statisticType": kind, "onStatisticField": "OBJECTID", "outStatisticFieldName": name,
            }]), "returnGeometry": "false"})
            features = payload.get("features") or []
            if not features:
                raise FeatureServerError(f"missing NAD {kind} OBJECTID statistic")
            values[name] = features[0].get("attributes", {}).get(name)
        return {"features": [{"attributes": values}]}


def _field(metadata: dict[str, Any], *candidates: str) -> str | None:
    fields = {str(item.get("name", "")).lower(): str(item.get("name")) for item in metadata.get("fields", [])}
    for candidate in candidates:
        if candidate.lower() in fields:
            return fields[candidate.lower()]
    return None


def _canonical(row: dict[str, Any], ordinal: int, metadata: dict[str, Any]) -> dict[str, Any]:
    # ArcGIS attributes are copied into the NAD TXT canonicalizer's vocabulary.
    aliases = {
        "address_id": ("UUID", "NAD_ID", "DataSet_ID", "OBJECTID"),
        "address": ("Address", "Full_Address", "AddressLine", "AddNo_Full", "FullAddr", "Full_Address_Line"),
        "state": ("State", "STATE"),
        "county": ("County", "COUNTY"),
        "latitude": ("Latitude", "LAT", "Y"),
        "longitude": ("Longitude", "LONG", "LON", "X"),
        "unit": ("Unit", "Unit_Number", "Secondary_Address", "Suite", "Unit_Num", "SubAddress"),
    }
    normalized = dict(row)
    for target, names in aliases.items():
        for name in names:
            if name in row and row[name] not in (None, ""):
                normalized[target] = str(row[name]); break
    geometry = row.get("__geometry") or {}
    if "latitude" not in normalized and geometry.get("y") is not None:
        normalized["latitude"] = str(geometry["y"])
    if "longitude" not in normalized and geometry.get("x") is not None:
        normalized["longitude"] = str(geometry["x"])
    result = canonicalize(normalized, ordinal)
    result["provenance"].update({"transport": "official_arcgis_featureserver", "layer_url": metadata.get("_layer_url", LAYER_URL)})
    return result


class NADFeatureServerIngest:
    def __init__(self, workdir: Path, client: FeatureServerClient | None = None):
        self.workdir = Path(workdir); self.workdir.mkdir(parents=True, exist_ok=True)
        self.client = client or FeatureServerClient()

    def ingest_oid_range(self, lower: int, upper: int, *, generation: str = "r23-20260630-oid") -> dict[str, Any]:
        """Stream one deterministic OBJECTID shard using keyset pagination."""
        if upper <= lower:
            raise ValueError("OID shard upper bound must exceed lower bound")
        metadata = self.client.metadata(); metadata["_layer_url"] = self.client.layer_url
        edit = metadata.get("editingInfo", {}).get("lastEditDate") or metadata.get("serviceItemId")
        checkpoint_path = self.workdir / f"{generation}.checkpoint.json"
        records_path = self.workdir / f"{generation}.canonical.jsonl"
        checkpoint = json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else {"last_object_id": lower, "source_records": 0, "canonical_records": 0, "rejects": 0, "completed": False}
        last = int(checkpoint.get("last_object_id", lower)); ordinal = int(checkpoint.get("source_records", 0))
        if checkpoint.get("completed") and records_path.exists():
            return json.loads((self.workdir / f"{generation}.manifest.json").read_text())
        with records_path.open("a", encoding="utf-8") as output:
            while True:
                page = self.client.query_page(where="1=1", last_object_id=last, upper_object_id=upper)
                if not page: break
                object_ids = [int(row.get("OBJECTID")) for row in page if row.get("OBJECTID") is not None]
                if object_ids != sorted(set(object_ids)) or (object_ids and object_ids[0] <= last):
                    raise FeatureServerError("non-monotonic or replayed OBJECTID page")
                for raw in page:
                    ordinal += 1
                    try:
                        output.write(json.dumps(_canonical(raw, ordinal, metadata), sort_keys=True, separators=(",", ":")) + "\n")
                        checkpoint["canonical_records"] = int(checkpoint.get("canonical_records", 0)) + 1
                    except (ValueError, TypeError):
                        checkpoint["rejects"] = int(checkpoint.get("rejects", 0)) + 1
                    checkpoint["source_records"] = int(checkpoint.get("source_records", 0)) + 1
                output.flush(); last = max(object_ids); checkpoint["last_object_id"] = last
                _atomic(checkpoint_path, {"generation": generation, "lower": lower, "upper": upper, "source_edit": edit, **checkpoint})
        if self.client.metadata().get("editingInfo", {}).get("lastEditDate") not in (None, edit):
            raise FeatureServerError("source edit timestamp changed during OID shard")
        digest = hashlib.sha256(); unique: set[str] = set(); by_state: Counter[str] = Counter(); by_county: Counter[str] = Counter()
        with records_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                digest.update(line.encode()); record = json.loads(line); unique.add(record["place_id"])
                by_state[record["state"]] += 1; by_county[record["county"] or "__UNKNOWN__"] += 1
        source_total = int(checkpoint.get("source_records", 0)); rejects = int(checkpoint.get("rejects", 0))
        manifest = {"source_id": SOURCE_ID, "release": RELEASE, "compiled": COMPILED_DATE, "transport": "official_arcgis_featureserver",
                    "layer_url": self.client.layer_url, "source_edit": edit, "generation": generation, "lower": lower, "upper": upper,
                    "last_object_id": last, "fingerprint_sha256": digest.hexdigest(), "rights": "CLEAR_FOR_NON_MAILING_USE",
                    "mailing_list_export": "DENIED", "counts": {"source_records": source_total, "canonical_records": len(unique),
                    "duplicates": max(0, source_total - rejects - len(unique)), "rejects": rejects},
                    "records_by_state": dict(by_state), "records_by_county": dict(by_county), "created_at": datetime.now(timezone.utc).isoformat()}
        _atomic(self.workdir / f"{generation}.manifest.json", manifest)
        _atomic(checkpoint_path, {"generation": generation, "lower": lower, "upper": upper, "source_edit": edit, "last_object_id": last, **checkpoint, "completed": True})
        return manifest

    def ingest(self, states: list[str] | None = None, *, generation: str = "r23-20260630-featureserver",
               counties: dict[str, list[str]] | None = None) -> dict[str, Any]:
        metadata = self.client.metadata()
        metadata["_layer_url"] = self.client.layer_url
        edit = metadata.get("editingInfo", {}).get("lastEditDate") or metadata.get("serviceItemId")
        checkpoint_path = self.workdir / f"{generation}.checkpoint.json"
        records_path = self.workdir / f"{generation}.canonical.jsonl"
        state = json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else {"completed": [], "source_records": 0, "canonical_records": 0, "duplicates": 0, "rejects": 0}
        done = set(state.get("completed", [])); ordinal = int(state.get("source_records", 0))
        records_path.parent.mkdir(parents=True, exist_ok=True)
        states = states or DEFAULT_STATES
        state_field = _field(metadata, "State", "STATE") or "State"
        county_field = _field(metadata, "County", "COUNTY") or "County"
        partition_keys = [(s, c) for s in states for c in (counties or {}).get(s, [None])]
        with records_path.open("a", encoding="utf-8") as output:
          for state_code, county in partition_keys:
            partition = f"{state_code}:{county or '*'}"
            if partition in done: continue
            where = f"{state_field} = '{state_code.replace(chr(39), chr(39)*2)}'"
            if county:
                where += f" AND {county_field} = '{county.replace(chr(39), chr(39)*2)}'"
            ids = self.client.ids(where)
            partition_counts = Counter()
            for start in range(0, len(ids), MAX_OBJECT_IDS):
                for raw in self.client.records(ids[start:start + MAX_OBJECT_IDS]):
                    ordinal += 1; partition_counts["source_records"] += 1
                    try: record = _canonical(raw, ordinal, metadata)
                    except (ValueError, TypeError): partition_counts["rejects"] += 1; continue
                    output.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
                    partition_counts["canonical_records"] += 1
            output.flush()
            done.add(partition)
            for key, value in partition_counts.items(): state[key] = int(state.get(key, 0)) + value
            _atomic(checkpoint_path, {"generation": generation, "source_edit": edit, "completed": sorted(done), **{key: int(value) for key, value in state.items() if key in {"source_records", "canonical_records", "duplicates", "rejects"}}})
        if len(done) != len(partition_keys):
            raise FeatureServerError("incomplete partition checkpoint")
        if self.client.metadata().get("editingInfo", {}).get("lastEditDate") not in (None, edit):
            raise FeatureServerError("source edit timestamp changed during ingest; mixed snapshot refused")
        digest = hashlib.sha256(); unique: set[str] = set(); by_state: Counter[str] = Counter(); by_county: Counter[str] = Counter()
        with records_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                record = json.loads(line); digest.update(line.encode()); unique.add(record["place_id"])
                by_state[record["state"]] += 1; by_county[record["county"] or "__UNKNOWN__"] += 1
        source_total = int(state.get("source_records", 0)); reject_total = int(state.get("rejects", 0))
        counts = Counter({"source_records": source_total, "canonical_records": len(unique), "duplicates": max(0, source_total - reject_total - len(unique)), "rejects": reject_total})
        manifest = {"source_id": SOURCE_ID, "release": RELEASE, "compiled": COMPILED_DATE, "transport": "official_arcgis_featureserver",
                    "layer_url": self.client.layer_url, "source_edit": edit, "generation": generation, "fingerprint_sha256": digest.hexdigest(),
                    "rights": "CLEAR_FOR_NON_MAILING_USE", "mailing_list_export": "DENIED", "counts": dict(counts),
                    "records_by_state": dict(by_state), "records_by_county": dict(by_county),
                    "created_at": datetime.now(timezone.utc).isoformat()}
        _atomic(self.workdir / f"{generation}.manifest.json", manifest)
        _atomic(self.workdir / "active-featureserver.json", {"generation": generation, "fingerprint_sha256": digest.hexdigest(), "source_edit": edit})
        _atomic(checkpoint_path, {"generation": generation, "source_edit": edit, "completed": sorted(done), **{key: int(value) for key, value in state.items() if key in {"source_records", "canonical_records", "duplicates", "rejects"}}, "complete": True})
        return manifest


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--workdir", type=Path, required=True); parser.add_argument("--state", action="append"); parser.add_argument("--lower", type=int); parser.add_argument("--upper", type=int); parser.add_argument("--generation", default="r23-20260630-featureserver")
    args = parser.parse_args(); ingest = NADFeatureServerIngest(args.workdir)
    if args.lower is not None or args.upper is not None:
        if args.lower is None or args.upper is None: parser.error("--lower and --upper must be supplied together")
        result = ingest.ingest_oid_range(args.lower, args.upper, generation=args.generation)
    else:
        result = ingest.ingest(args.state, generation=args.generation)
    print(json.dumps(result, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
