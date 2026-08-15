"""
CDC/ATSDR Social Vulnerability Index 2022 — county level.
Source: ArcGIS FeatureServer (official ATSDR host)
Layer 1: SVI2022 US county
License: Public Domain
"""
import math
import requests
from pipeline.base import BaseSourceAdapter, H3_R7
from pipeline.db import upsert

SVI_SERVICE = (
    "https://services3.arcgis.com/ZvidGQkLaDJxRSJ2/arcgis/rest/services"
    "/CDC_ATSDR_Social_Vulnerability_Index_2022_USA/FeatureServer/1/query"
)
PAGE_SIZE = 1000

# State abbreviation → FIPS mapping for query filter
FIPS_TO_ABBR = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA",
    "08": "CO", "09": "CT", "10": "DE", "11": "DC", "12": "FL",
    "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN",
    "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME",
    "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS",
    "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
    "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
    "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
    "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI",
    "56": "WY",
}


def _polygon_centroid(rings: list) -> tuple[float, float] | tuple[None, None]:
    """Compute centroid of first ring in Web Mercator, convert to WGS84."""
    if not rings:
        return None, None
    ring = rings[0]
    if not ring:
        return None, None
    # average of vertices (fast approximation)
    x = sum(pt[0] for pt in ring) / len(ring)
    y = sum(pt[1] for pt in ring) / len(ring)
    # Web Mercator (EPSG:3857) → WGS84
    lon = math.degrees(x / 6378137.0)
    lat = math.degrees(2 * math.atan(math.exp(y / 6378137.0)) - math.pi / 2)
    return round(lat, 6), round(lon, 6)


class SVIAdapter(BaseSourceAdapter):
    source_id = "cdc_svi_2022"
    source_name = "CDC/ATSDR Social Vulnerability Index 2022"
    license = "Public Domain"

    def __init__(self, state_fips: list[str]):
        self.state_fips = state_fips

    def discover(self) -> list[str]:
        return [SVI_SERVICE]

    def fetch(self, url: str) -> bytes:
        return b""

    def parse(self, raw: bytes, url: str):
        return None

    def normalize(self, parsed) -> list[dict]:
        return []

    def validate(self, records):
        valid = [r for r in records if r.get("fips") and len(r["fips"]) == 5]
        return valid, []

    def load(self, records: list[dict], conn) -> int:
        return upsert(conn, "world_vulnerability", records, ["fips", "svi_version"])

    def _fetch_state(self, abbr: str) -> list[dict]:
        records = []
        offset = 0
        while True:
            params = {
                "where":           f"ST_ABBR='{abbr}'",
                "outFields":       "*",
                "returnGeometry":  "true",
                "f":               "json",
                "resultRecordCount": PAGE_SIZE,
                "resultOffset":    offset,
            }
            resp = requests.get(SVI_SERVICE, params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            features = data.get("features", [])
            if not features:
                break
            for feat in features:
                a = feat.get("attributes", {})
                geo = feat.get("geometry", {})
                rings = geo.get("rings", [])
                lat, lon = _polygon_centroid(rings)
                h3r7 = self.h3_point(lat, lon, H3_R7) if lat and lon else None

                def sf(v):
                    try:
                        f = float(v)
                        return None if f < 0 else f
                    except (TypeError, ValueError):
                        return None

                records.append({
                    "source_id":   self.source_id,
                    "svi_version": "2022",
                    "source_year": 2022,
                    "fips":        str(a.get("FIPS", "")).zfill(5),
                    "state":       str(a.get("STATE", "")),
                    "county":      str(a.get("COUNTY", "")),
                    "location":    str(a.get("LOCATION", "")),
                    "area_sqmi":   sf(a.get("AREA_SQMI")),
                    "total_pop":   int(a["E_TOTPOP"]) if a.get("E_TOTPOP") is not None else None,
                    "spl_themes":  sf(a.get("SPL_THEMES")),
                    "rpl_themes":  sf(a.get("RPL_THEMES")),
                    "rpl_theme1":  sf(a.get("RPL_THEME1")),
                    "rpl_theme2":  sf(a.get("RPL_THEME2")),
                    "rpl_theme3":  sf(a.get("RPL_THEME3")),
                    "rpl_theme4":  sf(a.get("RPL_THEME4")),
                    "h3_r7":       h3r7,
                })
            offset += PAGE_SIZE
            if len(features) < PAGE_SIZE:
                break
        return records

    def run(self, conn):
        print(f"\n=== {self.source_name} ===")
        for fips in self.state_fips:
            abbr = FIPS_TO_ABBR.get(fips.zfill(2))
            if not abbr:
                print(f"  Unknown state FIPS {fips}, skipping")
                continue
            print(f"  State {abbr} ({fips})...")
            try:
                records = self._fetch_state(abbr)
            except Exception as e:
                print(f"  WARNING: SVI fetch failed for {abbr}: {e}")
                continue
            valid, invalid = self.validate(records)
            print(f"  {abbr}: valid={len(valid)} invalid={len(invalid)}")
            n = self.load(valid, conn)
            print(f"  loaded {n} rows → world_vulnerability")
