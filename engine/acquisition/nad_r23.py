"""USDOT National Address Database Release 23 baseline ingest.

The adapter is deliberately conservative: it only accepts the official R23
artifact (or a caller-supplied fixture), never synthesises rows, and refuses
mailing-list exports.  The production path streams the ZIP member so a 7.6 GB
release does not have to fit in memory.  A checkpoint is written after every
batch and the active generation is promoted only after the stream completes.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import tempfile
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, TextIO

SOURCE_ID = "usdot_nad_r23"
RELEASE = "National Address Database Release 23"
COMPILED_DATE = "2026-06-30"
METADATA_URL = "https://data.transportation.gov/api/views/fc2s-wawr"
DOWNLOAD_URL = METADATA_URL + "/download?content=true&filename=TXT.zip"


def rights_artifact() -> dict[str, Any]:
    return {
        "source_id": SOURCE_ID, "release": RELEASE, "compiled": COMPILED_DATE,
        "rights": "CLEAR_FOR_NON_MAILING_USE",
        "evidence": [
            "USDOT NAD R23 is open data and a federal-government work.",
            "17 USC 105: federal works are not subject to copyright.",
            "USDOT release terms state reuse without limitation or restriction.",
        ],
        "constraint": "NAD_MAILING_LIST_EXPORT=DENIED",
        "metadata_url": METADATA_URL, "download_url": DOWNLOAD_URL,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


def _key(row: dict[str, str], *names: str) -> str:
    lowered = {str(k).strip().lower(): (v.strip() if isinstance(v, str) else "") for k, v in row.items()}
    for name in names:
        if lowered.get(name.lower()):
            return lowered[name.lower()]
    return ""


def canonicalize(row: dict[str, str], ordinal: int) -> dict[str, Any]:
    source_record_id = _key(row, "address_id", "nad_id", "record_id", "id", "objectid")
    address = _key(row, "address", "address_line", "addressline", "street_address", "full_address")
    state = _key(row, "state", "state_code", "state_abbreviation").upper()
    county = _key(row, "county", "county_name")
    lat_s = _key(row, "latitude", "lat", "y")
    lon_s = _key(row, "longitude", "lon", "lng", "long", "x")
    if not address or not state:
        raise ValueError("address and state are required")
    lat = float(lat_s) if lat_s else None
    lon = float(lon_s) if lon_s else None
    if lat is not None and not -90 <= lat <= 90:
        raise ValueError("latitude out of bounds")
    if lon is not None and not -180 <= lon <= 180:
        raise ValueError("longitude out of bounds")
    stable = source_record_id or hashlib.sha256(
        f"{address.lower()}|{state}|{county}".encode()
    ).hexdigest()[:24]
    units = _key(row, "unit", "unit_number", "secondary_address", "suite")
    return {
        "place_id": f"nad-r23:{stable}", "source_record_id": source_record_id or f"ordinal:{ordinal}",
        "address": address, "state": state, "county": county, "unit": units or None,
        "geometry": {"lat": lat, "lon": lon} if lat is not None and lon is not None else None,
        "provenance": {"source_id": SOURCE_ID, "release": RELEASE, "compiled": COMPILED_DATE},
    }


def _text_stream(source: Path) -> tuple[TextIO, Any]:
    if source.suffix.lower() != ".zip":
        return source.open("r", encoding="utf-8-sig", errors="replace", newline=""), None
    archive = zipfile.ZipFile(source)
    members = [i for i in archive.infolist() if not i.is_dir()]
    if not members:
        archive.close(); raise ValueError("NAD archive has no data member")
    member = max(members, key=lambda x: x.file_size)
    raw = archive.open(member, "r")
    import io
    return io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline=""), archive


def _rows(handle: TextIO) -> Iterator[dict[str, str]]:
    sample = handle.read(8192)
    handle.seek(0)
    # Sniffer is unreliable for pipe-delimited files whose values contain
    # repeated punctuation. Prefer the delimiter occurring in the header.
    header = sample.splitlines()[0] if sample.splitlines() else ""
    delimiter = max((",", "|", "\t"), key=lambda item: header.count(item))
    dialect = csv.excel if delimiter == "," else csv.excel_tab
    dialect.delimiter = delimiter
    yield from csv.DictReader(handle, dialect=dialect)


class NADR23Ingest:
    def __init__(self, workdir: Path):
        self.workdir = Path(workdir); self.workdir.mkdir(parents=True, exist_ok=True)

    def ingest(self, source: Path, generation: str = "r23-20260630", batch_size: int = 10000,
               max_records: int | None = None) -> dict[str, Any]:
        if not source.exists():
            raise FileNotFoundError(f"official NAD R23 artifact unavailable: {source}")
        target = self.workdir / "generations" / generation
        target.mkdir(parents=True, exist_ok=True)
        output = target / "canonical.jsonl"
        checkpoint_path = target / "checkpoint.json"
        manifest_path = target / "manifest.json"
        # A completed generation is immutable and idempotent.
        if manifest_path.exists() and output.exists():
            return json.loads(manifest_path.read_text())
        counts = Counter(); state = Counter(); county = Counter(); authorities = Counter()
        digest = hashlib.sha256(); seen: set[str] = set(); ordinal = 0
        handle, archive = _text_stream(source)
        try:
            with output.open("w", encoding="utf-8") as out:
                for raw in _rows(handle):
                    ordinal += 1
                    if max_records is not None and counts["source_records"] >= max_records: break
                    counts["source_records"] += 1
                    try: record = canonicalize(raw, ordinal)
                    except (ValueError, TypeError): counts["rejects"] += 1; continue
                    if record["place_id"] in seen: counts["duplicates"] += 1; continue
                    seen.add(record["place_id"]); counts["canonical_records"] += 1
                    state[record["state"]] += 1; county[record["county"] or "__UNKNOWN__"] += 1
                    authorities[_key(raw, "source", "source_authority", "agency") or "NAD"] += 1
                    if record["unit"]: counts["with_units"] += 1
                    if record["geometry"]: counts["with_geometry"] += 1
                    encoded = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
                    out.write(encoded.decode()); digest.update(encoded)
                    if counts["source_records"] % batch_size == 0:
                        _atomic(checkpoint_path, {"source_records": counts["source_records"], "canonical_records": counts["canonical_records"], "generation": generation})
                out.flush(); os.fsync(out.fileno())
        finally:
            handle.close()
            if archive: archive.close()
        manifest = {"source_id": SOURCE_ID, "release": RELEASE, "compiled": COMPILED_DATE,
                    "source_path": str(source), "source_sha256": _sha256(source),
                    "canonical_sha256": digest.hexdigest(), "generation": generation,
                    "rights": "CLEAR_FOR_NON_MAILING_USE", "mailing_list_export": "DENIED",
                    "counts": dict(counts), "records_by_state": dict(state),
                    "records_by_county": dict(county), "records_by_source_authority": dict(authorities),
                    "refresh": {"enabled": True, "cadence": "release-driven", "source": DOWNLOAD_URL},
                    "created_at": datetime.now(timezone.utc).isoformat()}
        _atomic(manifest_path, manifest)
        _atomic(self.workdir / "active.json", {"source_id": SOURCE_ID, "generation": generation, "canonical_sha256": digest.hexdigest()})
        _atomic(self.workdir / "rollback.json", {"armed": True, "prior_generations": []})
        _atomic(checkpoint_path, {"complete": True, "source_records": counts["source_records"], "generation": generation})
        return manifest

    @staticmethod
    def export_mailing_list(*args: Any, **kwargs: Any) -> None:
        raise PermissionError("NAD_MAILING_LIST_EXPORT=DENIED: NAD may not be exported as a mailing list")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""): h.update(block)
    return h.hexdigest()


def _atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f: json.dump(value, f, sort_keys=True, indent=2); f.write("\n"); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def main() -> int:
    p = argparse.ArgumentParser(); p.add_argument("source", type=Path); p.add_argument("--workdir", type=Path, required=True); p.add_argument("--generation", default="r23-20260630"); p.add_argument("--max-records", type=int)
    args = p.parse_args(); print(json.dumps(NADR23Ingest(args.workdir).ingest(args.source, args.generation, max_records=args.max_records), indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
