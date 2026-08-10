"""
CDC/ATSDR Social Vulnerability Index 2022.
Downloads state CSV files directly.
Source: https://svi.cdc.gov/data-and-tools-download.html
License: Public Domain
"""
import io
import requests
import pandas as pd
from pipeline.base import BaseSourceAdapter, H3_R7
from pipeline.db import upsert

# 2022 SVI CSV by state (state FIPS → download URL pattern)
SVI_BASE = "https://svi.cdc.gov/Documents/Data/2022_SVI_Data/CSV/States_Counties"
SVI_STATES = {
    "26": ("Michigan",  f"{SVI_BASE}/Michigan.csv"),
    "12": ("Florida",   f"{SVI_BASE}/Florida.csv"),
}


class SVIAdapter(BaseSourceAdapter):
    source_id = "cdc_svi_2022"
    source_name = "CDC/ATSDR Social Vulnerability Index 2022"
    license = "Public Domain"

    def __init__(self, state_fips: list[str]):
        self.state_fips = state_fips

    def discover(self) -> list[str]:
        return [SVI_STATES[f][1] for f in self.state_fips if f in SVI_STATES]

    def fetch(self, url: str) -> bytes:
        if self.is_cached(url):
            return self.read_cache(url)
        print(f"  Downloading {url} ...")
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        data = resp.content
        self.write_cache(url, data)
        return data

    def parse(self, raw: bytes, url: str) -> pd.DataFrame:
        return pd.read_csv(io.BytesIO(raw), dtype=str, low_memory=False)

    def normalize(self, df: pd.DataFrame) -> list[dict]:
        records = []
        for _, row in df.iterrows():
            fips = str(row.get("FIPS", "")).zfill(11)
            if not fips or fips == "00000000000":
                continue

            def safe_float(v):
                try:
                    f = float(v)
                    return None if f == -999 else f
                except (TypeError, ValueError):
                    return None

            def safe_int(v):
                try:
                    return int(float(v))
                except (TypeError, ValueError):
                    return None

            # Centroid for H3 (SVI provides lat/lon)
            lat = safe_float(row.get("E_TOTPOP"))  # not lat — use geometry centroid approach
            lat_val = safe_float(row.get("LATITUDE", row.get("LAT")))
            lon_val = safe_float(row.get("LONGITUDE", row.get("LON")))
            h3r7 = self.h3_point(lat_val, lon_val, H3_R7) if lat_val and lon_val else None

            records.append({
                "source_id":     self.source_id,
                "svi_version":   "2022",
                "source_year":   2022,
                "fips":          fips,
                "state":         str(row.get("STATE", "")),
                "county":        str(row.get("COUNTY", "")),
                "location":      str(row.get("LOCATION", "")),
                "area_sqmi":     safe_float(row.get("AREA_SQMI")),
                "total_pop":     safe_int(row.get("E_TOTPOP")),
                "spl_themes":    safe_float(row.get("SPL_THEMES")),
                "rpl_themes":    safe_float(row.get("RPL_THEMES")),
                "rpl_theme1":    safe_float(row.get("RPL_THEME1")),
                "rpl_theme2":    safe_float(row.get("RPL_THEME2")),
                "rpl_theme3":    safe_float(row.get("RPL_THEME3")),
                "rpl_theme4":    safe_float(row.get("RPL_THEME4")),
                "h3_r7":         h3r7,
            })
        return records

    def validate(self, records):
        valid = [r for r in records if r["fips"] and len(r["fips"]) == 11]
        return valid, [r for r in records if r not in valid]

    def load(self, records: list[dict], conn) -> int:
        return upsert(conn, "world_vulnerability", records, ["fips", "svi_version"])

    def run(self, conn):
        print(f"\n=== {self.source_name} ===")
        for fips in self.state_fips:
            if fips not in SVI_STATES:
                print(f"  No SVI URL configured for FIPS {fips}")
                continue
            name, url = SVI_STATES[fips]
            raw = self.fetch(url)
            df = self.parse(raw, url)
            normalized = self.normalize(df)
            valid, invalid = self.validate(normalized)
            print(f"  {name}: valid={len(valid)} invalid={len(invalid)}")
            n = self.load(valid, conn)
            print(f"  loaded {n} rows → world_vulnerability")
