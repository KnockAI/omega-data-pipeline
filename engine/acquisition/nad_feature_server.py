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

    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        url = self.layer_url + "?" + urllib.parse.urlencode({**params, "f": "json"})
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with self.opener(url, timeout=60) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if "error" in payload:
                    code = int(payload["error"].get("code", 0))
                    if code in (429, 500, 502, 503, 504) and attempt < self.retries:
                        self.sleep(min(30, 2 ** attempt)); continue
                    raise FeatureServerError(str(payload["error"]))
                return payload
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code not in (429, 500, 502, 503, 504) or attempt >= self.retries:
                    raise FeatureServerError(f"HTTP {exc.code}") from exc
                self.sleep(min(30, 2 ** attempt))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                last = exc
                if attempt >= self.retries:
                    raise FeatureServerError(str(exc)) from exc
                self.sleep(min(30, 2 ** attempt))
        raise FeatureServerError(str(last))

    def metadata(self) -> dict[str, Any]:
        return self._get({})

    def ids(self, where: str) -> list[int]:
        payload = self._get({"where": where, "returnIdsOnly": "true"})
        return [int(value) for value in payload.get("objectIds", [])]

    def records(self, object_ids: list[int], *, out_fields: str = "*") -> list[dict[str, Any]]:
        if len(object_ids) > MAX_OBJECT_IDS:
            raise ValueError("ArcGIS objectIds request exceeds 2000-record ceiling")
        if not object_ids:
            return []
        payload = self._get({"objectIds": ",".join(str(i) for i in object_ids),
                             "outFields": out_fields, "returnGeometry": "true"})
        return [dict(feature.get("attributes", {}), **({"__geometry": feature.get("geometry")} if feature.get("geometry") else {}))
                for feature in payload.get("features", [])]


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

    def ingest(self, states: list[str] | None = None, *, generation: str = "r23-20260630-featureserver",
               counties: dict[str, list[str]] | None = None) -> dict[str, Any]:
        metadata = self.client.metadata()
        metadata["_layer_url"] = self.client.layer_url
        edit = metadata.get("editingInfo", {}).get("lastEditDate") or metadata.get("serviceItemId")
        checkpoint_path = self.workdir / f"{generation}.checkpoint.json"
        state = json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else {"completed": [], "records": []}
        done = set(state.get("completed", [])); records = list(state.get("records", [])); ordinal = len(records)
        states = states or DEFAULT_STATES
        state_field = _field(metadata, "State", "STATE") or "State"
        county_field = _field(metadata, "County", "COUNTY") or "County"
        partition_keys = [(s, c) for s in states for c in (counties or {}).get(s, [None])]
        for state_code, county in partition_keys:
            partition = f"{state_code}:{county or '*'}"
            if partition in done: continue
            where = f"{state_field} = '{state_code.replace(chr(39), chr(39)*2)}'"
            if county:
                where += f" AND {county_field} = '{county.replace(chr(39), chr(39)*2)}'"
            ids = self.client.ids(where)
            for start in range(0, len(ids), MAX_OBJECT_IDS):
                for raw in self.client.records(ids[start:start + MAX_OBJECT_IDS]):
                    ordinal += 1
                    try: records.append(_canonical(raw, ordinal, metadata))
                    except (ValueError, TypeError): continue
            done.add(partition)
            _atomic(checkpoint_path, {"generation": generation, "source_edit": edit, "completed": sorted(done), "records": records})
        if len(done) != len(partition_keys):
            raise FeatureServerError("incomplete partition checkpoint")
        if self.client.metadata().get("editingInfo", {}).get("lastEditDate") not in (None, edit):
            raise FeatureServerError("source edit timestamp changed during ingest; mixed snapshot refused")
        source_record_count = len(records)
        unique: dict[str, dict[str, Any]] = {record["place_id"]: record for record in records}
        records = list(unique.values())
        digest = hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        counts = Counter({"source_records": source_record_count, "canonical_records": len(records), "duplicates": source_record_count - len(unique)})
        manifest = {"source_id": SOURCE_ID, "release": RELEASE, "compiled": COMPILED_DATE, "transport": "official_arcgis_featureserver",
                    "layer_url": self.client.layer_url, "source_edit": edit, "generation": generation, "fingerprint_sha256": digest,
                    "rights": "CLEAR_FOR_NON_MAILING_USE", "mailing_list_export": "DENIED", "counts": dict(counts),
                    "records_by_state": dict(Counter(r["state"] for r in records)), "records_by_county": dict(Counter(r["county"] or "__UNKNOWN__" for r in records)),
                    "created_at": datetime.now(timezone.utc).isoformat()}
        _atomic(self.workdir / f"{generation}.manifest.json", manifest)
        _atomic(self.workdir / "active-featureserver.json", {"generation": generation, "fingerprint_sha256": digest, "source_edit": edit})
        _atomic(checkpoint_path, {"generation": generation, "source_edit": edit, "completed": sorted(done), "records": records, "complete": True})
        return manifest


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--workdir", type=Path, required=True); parser.add_argument("--state", action="append")
    args = parser.parse_args(); print(json.dumps(NADFeatureServerIngest(args.workdir).ingest(args.state), indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
