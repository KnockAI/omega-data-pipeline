import hashlib
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import h3 as h3lib


H3_R7 = 7
H3_R9 = 9


@dataclass
class IngestionRecord:
    source_id: str
    source_version: str
    source_url: str
    file_hash: str
    file_size_bytes: int
    format: str
    compression: Optional[str]
    object_location: str
    license: str
    row_count: int = 0
    status: str = "complete"
    error_message: Optional[str] = None


class BaseSourceAdapter(ABC):
    source_id: str
    source_name: str
    license: str

    @abstractmethod
    def discover(self) -> list[str]:
        """Return URLs or API endpoints to fetch."""
        pass

    @abstractmethod
    def fetch(self, url: str) -> bytes | None:
        """Download raw data, return bytes or None if already cached."""
        pass

    @abstractmethod
    def parse(self, raw: bytes, url: str) -> object:
        """Parse raw bytes into an intermediate representation."""
        pass

    @abstractmethod
    def normalize(self, parsed: object) -> list[dict]:
        """Convert to canonical world model schema dicts."""
        pass

    @abstractmethod
    def validate(self, records: list[dict]) -> tuple[list[dict], list[dict]]:
        """Return (valid_records, invalid_records)."""
        pass

    @abstractmethod
    def load(self, records: list[dict], conn) -> int:
        """Insert valid records into the database. Return row count."""
        pass

    # ── Utilities available to all adapters ──────────────────────────────

    def sha256(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def h3_point(self, lat: float, lon: float, resolution: int = H3_R9) -> str:
        return h3lib.latlng_to_cell(lat, lon, resolution)

    def h3_polyfill(self, geojson: dict, resolution: int = H3_R9) -> list[str]:
        """Fill a GeoJSON polygon/multipolygon with H3 cells."""
        from shapely.geometry import shape, mapping
        from shapely.ops import unary_union

        geom = shape(geojson)
        if geom.geom_type == "MultiPolygon":
            cells = set()
            for poly in geom.geoms:
                cells.update(h3lib.polygon_to_cells(mapping(poly), resolution))
            return list(cells)
        return list(h3lib.polygon_to_cells(mapping(geom), resolution))

    def provenance(self, source_version: str = None) -> dict:
        return {
            "source_id": self.source_id,
            "source_version": source_version,
            "ingested_at": datetime.utcnow().isoformat(),
        }

    def cache_path(self, url: str, base_dir: str = "./data/raw") -> str:
        safe = url.replace("://", "_").replace("/", "_").replace("?", "_")[:200]
        return os.path.join(base_dir, self.source_id, safe)

    def is_cached(self, url: str, base_dir: str = "./data/raw") -> bool:
        return os.path.exists(self.cache_path(url, base_dir))

    def read_cache(self, url: str, base_dir: str = "./data/raw") -> bytes:
        with open(self.cache_path(url, base_dir), "rb") as f:
            return f.read()

    def write_cache(self, url: str, data: bytes, base_dir: str = "./data/raw") -> str:
        path = self.cache_path(url, base_dir)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return path
