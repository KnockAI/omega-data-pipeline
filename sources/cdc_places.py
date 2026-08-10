"""
CDC PLACES — health outcomes at census tract level.
Source: https://chronicdata.cdc.gov/resource/cwsq-ngmh.json (Socrata)
License: Public Domain
"""
import requests
from tqdm import tqdm
from pipeline.base import BaseSourceAdapter, H3_R7
from pipeline.db import upsert

SOCRATA_URL = "https://chronicdata.cdc.gov/resource/cwsq-ngmh.json"
PAGE_SIZE = 50_000


class CDCPlacesAdapter(BaseSourceAdapter):
    source_id = "cdc_places_2023"
    source_name = "CDC PLACES 2023"
    license = "Public Domain"

    def __init__(self, state_abbrs: list[str]):
        self.state_abbrs = state_abbrs  # e.g. ["MI", "FL"]

    def discover(self) -> list[str]:
        return [SOCRATA_URL]

    def fetch(self, url: str) -> bytes:
        return b""  # paginated in run()

    def _fetch_page(self, state: str, offset: int) -> list[dict]:
        params = {
            "$where":  f"stateabbr='{state}' AND geographiclevel='Census Tract'",
            "$limit":  PAGE_SIZE,
            "$offset": offset,
        }
        resp = requests.get(SOCRATA_URL, params=params, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def parse(self, raw: bytes, url: str):
        return None

    def normalize(self, row: dict) -> dict | None:
        geoid = row.get("locationid") or row.get("tractfips")
        if not geoid:
            return None
        # Derive H3 r7 from the census tract centroid (latitude/longitude in row)
        lat = row.get("latitude") or row.get("lat")
        lon = row.get("longitude") or row.get("lon")
        h3r7 = self.h3_point(float(lat), float(lon), 7) if lat and lon else None
        return {
            "source_id":          self.source_id,
            "source_geography":   "census_tract",
            "source_geography_id": str(geoid),
            "h3_r7":              h3r7,
            "year":               int(row.get("year", 2023)),
            "measure":            row.get("measureid", "").upper(),
            "category":           row.get("category"),
            "data_value":         float(row["data_value"]) if row.get("data_value") else None,
            "data_value_type":    row.get("data_value_type"),
            "confidence_low":     float(row["low_confidence_limit"]) if row.get("low_confidence_limit") else None,
            "confidence_high":    float(row["high_confidence_limit"]) if row.get("high_confidence_limit") else None,
            "methodology":        "PLACES small-area estimation model",
            "source_version":     "2023",
        }

    def validate(self, records):
        valid = [r for r in records if r and r.get("measure") and r.get("source_geography_id")]
        return valid, []

    def load(self, records: list[dict], conn) -> int:
        return upsert(conn, "world_health", records,
                      ["source_geography_id", "year", "measure"])

    def run(self, conn):
        print(f"\n=== {self.source_name} ===")
        for state in self.state_abbrs:
            offset = 0
            total = 0
            print(f"  State {state}...")
            while True:
                rows = self._fetch_page(state, offset)
                if not rows:
                    break
                normalized = [self.normalize(r) for r in rows]
                valid, _ = self.validate(normalized)
                n = self.load(valid, conn)
                total += n
                offset += PAGE_SIZE
                if len(rows) < PAGE_SIZE:
                    break
            print(f"  {state}: {total} rows → world_health")
