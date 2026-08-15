"""Census Building Permits Survey national baseline (no Socrata token)."""
import csv, io, requests
from pipeline.geospatial import coordinate_h3
from pipeline.db import upsert

BPS_URL = "https://www.census.gov/construction/bps/txt/tb2u2024.txt"

class NationalPermitsAdapter:
    source_id = "census_bps"
    source_name = "Census Building Permits Survey"
    def __init__(self, year=2024, fetcher=requests.get): self.year, self.fetcher = year, fetcher
    def fetch(self):
        response = self.fetcher(BPS_URL, timeout=60)
        response.raise_for_status()
        return response.content
    def normalize(self, rows):
        out=[]
        for row in rows:
            geoid = str(row.get("GEO_ID") or row.get("geoid") or "").strip()
            if not geoid: continue
            out.append({"source":"census_bps", "permit_id":f"{self.year}:{geoid}", "permit_type":"units_authorized", "status":"reported", "issued_at":f"{self.year}-01-01", "value":row.get("UNITS"), "jurisdiction":row.get("NAME"), "source_precision":"aggregate_geography", "source_vintage":str(self.year), "raw_reference":BPS_URL})
        return out
    def load(self, conn, records):
        return upsert(conn, "omega_permits_normalized", records, ["source", "permit_id"])
