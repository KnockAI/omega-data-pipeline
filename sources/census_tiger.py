"""
Census TIGER/Line — administrative boundaries.
Downloads state-level shapefiles for counties, tracts, VTDs.
Source: https://www2.census.gov/geo/tiger/TIGER2023/
License: Public Domain
"""
import io
import zipfile
import requests
import geopandas as gpd
from tqdm import tqdm
from shapely.geometry import mapping
from pipeline.base import BaseSourceAdapter, H3_R7, H3_R9
from pipeline.db import upsert

TIGER_BASE = "https://www2.census.gov/geo/tiger/TIGER2023"

# (layer_name, boundary_type, url_template with {fips})
LAYERS = [
    ("COUNTY",  "county",       f"{TIGER_BASE}/COUNTY/tl_2023_us_county.zip"),
    ("TRACT",   "census_tract", None),   # per-state
    ("VTD",     "precinct",     None),   # per-state (voting districts)
    ("BG",      "block_group",  None),   # per-state
]

STATE_LAYERS = {
    "census_tract": f"{TIGER_BASE}/TRACT/tl_2023_{{fips}}_tract.zip",
    "precinct":     f"{TIGER_BASE}/VTD/tl_2023_{{fips}}_vtd20.zip",
    "block_group":  f"{TIGER_BASE}/BG/tl_2023_{{fips}}_bg.zip",
}


class CensusTigerAdapter(BaseSourceAdapter):
    source_id = "census_tiger_2023"
    source_name = "Census TIGER/Line 2023"
    license = "Public Domain"

    def __init__(self, state_fips: list[str]):
        self.state_fips = state_fips  # e.g. ["26", "12"]

    def discover(self) -> list[tuple[str, str]]:
        urls = [("county", f"{TIGER_BASE}/COUNTY/tl_2023_us_county.zip")]
        for fips in self.state_fips:
            for btype, template in STATE_LAYERS.items():
                urls.append((btype, template.format(fips=fips)))
        return urls

    def fetch(self, url: str) -> bytes:
        if self.is_cached(url):
            return self.read_cache(url)
        print(f"  Downloading {url} ...")
        resp = requests.get(url, stream=True, timeout=180)
        resp.raise_for_status()
        data = resp.content
        self.write_cache(url, data)
        return data

    def parse(self, raw: bytes, url: str) -> gpd.GeoDataFrame:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            shp = [n for n in zf.namelist() if n.endswith(".shp")][0]
            zf.extractall("/tmp/tiger_extract")
        return gpd.read_file(f"/tmp/tiger_extract/{shp}")

    def normalize(self, gdf: gpd.GeoDataFrame, boundary_type: str) -> list[dict]:
        if gdf.crs and gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(epsg=4326)

        records = []
        for _, row in tqdm(gdf.iterrows(), total=len(gdf), desc=f"  H3 {boundary_type}"):
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue

            geojson = mapping(geom)
            try:
                h3_r7 = self.h3_polyfill(geojson, H3_R7)
                h3_r9 = self.h3_polyfill(geojson, H3_R9)
            except Exception:
                h3_r7, h3_r9 = [], []

            # Normalize geometry to MultiPolygon WKT
            if geom.geom_type == "Polygon":
                from shapely.geometry import MultiPolygon
                geom = MultiPolygon([geom])

            rec = {
                "source_id":         self.source_id,
                "source_entity_id":  str(row.get("GEOID", row.get("GEOID20", ""))),
                "boundary_type":     boundary_type,
                "name":              str(row.get("NAME", row.get("NAMELSAD", ""))),
                "geoid":             str(row.get("GEOID", row.get("GEOID20", ""))),
                "state_fips":        str(row.get("STATEFP", row.get("STATEFP20", "")))[:2],
                "county_fips":       str(row.get("COUNTYFP", row.get("COUNTYFP20", "")))[:3],
                "fips_code":         str(row.get("GEOID", row.get("GEOID20", ""))),
                "h3_r7":             h3_r7,
                "h3_r9":             h3_r9,
                "geometry":          f"SRID=4326;{geom.wkt}",
                "area_sqkm":         geom.area * 111_320 ** 2 / 1_000_000,
                "source_version":    "2023",
                "observed_at":       "2023-01-01",
            }
            records.append(rec)
        return records

    def validate(self, records):
        valid = [r for r in records if r["geoid"] and r["geometry"]]
        return valid, [r for r in records if r not in valid]

    def load(self, records: list[dict], conn) -> int:
        return upsert(conn, "world_boundaries", records, ["geoid", "boundary_type"])

    def run(self, conn):
        print(f"\n=== {self.source_name} ===")
        for btype, url in self.discover():
            try:
                raw = self.fetch(url)
                gdf = self.parse(raw, url)
                normalized = self.normalize(gdf, btype)
                valid, invalid = self.validate(normalized)
                print(f"  {btype}: valid={len(valid)} invalid={len(invalid)}")
                n = self.load(valid, conn)
                print(f"  loaded {n} rows → world_boundaries [{btype}]")
            except Exception as e:
                print(f"  ERROR {url}: {e}")
