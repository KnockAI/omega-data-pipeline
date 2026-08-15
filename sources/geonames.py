"""
GeoNames US — place name resolution layer.
Source: https://download.geonames.org/export/dump/US.zip
License: CC BY 4.0

Processes in streaming chunks to avoid loading 2.2M rows into RAM.
"""
import io
import zipfile
import requests
from pipeline.base import BaseSourceAdapter, H3_R7, H3_R9
from pipeline.db import upsert

SOURCE_URL = "https://download.geonames.org/export/dump/US.zip"

COLUMNS = [
    "geoname_id", "name", "ascii_name", "alternate_names",
    "latitude", "longitude", "feature_class", "feature_code",
    "country_code", "cc2", "admin1_code", "admin2_code",
    "admin3_code", "admin4_code", "population", "elevation_m",
    "dem", "timezone", "modification_date",
]

WANTED_CLASSES = {"A", "P", "S", "H", "L", "T"}
CHUNK_SIZE = 10_000


class GeoNamesUSAdapter(BaseSourceAdapter):
    source_id = "geonames_us"
    source_name = "GeoNames United States"
    license = "CC BY 4.0"

    def discover(self) -> list[str]:
        return [SOURCE_URL]

    def fetch(self, url: str) -> bytes:
        if self.is_cached(url):
            print(f"  [cache] {url}")
            return self.read_cache(url)
        print(f"  Downloading {url} ...")
        resp = requests.get(url, stream=True, timeout=120)
        resp.raise_for_status()
        data = resp.content
        self.write_cache(url, data)
        return data

    def parse(self, raw: bytes, url: str) -> list[dict]:
        return []

    def normalize(self, parsed: list[dict]) -> list[dict]:
        return []

    def validate(self, records):
        return records, []

    def load(self, records: list[dict], conn) -> int:
        return upsert(conn, "world_geonames", records, ["geoname_id"])

    def _normalize_row(self, row: dict) -> dict | None:
        try:
            lat = float(row["latitude"])
            lon = float(row["longitude"])
        except (ValueError, TypeError):
            return None
        return {
            "geoname_id":        int(row["geoname_id"]),
            "name":              row["name"],
            "ascii_name":        row["ascii_name"] or None,
            "latitude":          lat,
            "longitude":         lon,
            "feature_class":     row["feature_class"] or None,
            "feature_code":      row["feature_code"] or None,
            "country_code":      row["country_code"] or None,
            "admin1_code":       row["admin1_code"] or None,
            "admin2_code":       row["admin2_code"] or None,
            "admin3_code":       row["admin3_code"] or None,
            "admin4_code":       row["admin4_code"] or None,
            "population":        int(row["population"]) if row["population"] else 0,
            "elevation_m":       int(row["elevation_m"]) if row["elevation_m"] else None,
            "timezone":          row["timezone"] or None,
            "h3_r7":             self.h3_point(lat, lon, H3_R7),
            "h3_r9":             self.h3_point(lat, lon, H3_R9),
            "modification_date": row["modification_date"] or None,
        }

    def run(self, conn):
        print(f"\n=== {self.source_name} ===")
        raw = self.fetch(SOURCE_URL)

        total_loaded = 0
        chunk = []

        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            with zf.open("US.txt") as f:
                for line in f:
                    parts = line.decode("utf-8").rstrip("\n").split("\t")
                    if len(parts) < 19:
                        continue
                    row = dict(zip(COLUMNS, parts))
                    if row["feature_class"] not in WANTED_CLASSES:
                        continue
                    rec = self._normalize_row(row)
                    if rec is None:
                        continue
                    chunk.append(rec)
                    if len(chunk) >= CHUNK_SIZE:
                        n = self.load(chunk, conn)
                        total_loaded += n
                        print(f"  loaded {total_loaded:,} rows so far...", flush=True)
                        chunk = []

        if chunk:
            n = self.load(chunk, conn)
            total_loaded += n

        print(f"  loaded {total_loaded:,} rows → world_geonames")
