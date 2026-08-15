"""Census ZCTA centroid -> canonical H3 r9 bridge for ZIP-only sources."""
import csv, io, zipfile, requests
from pipeline.base import BaseSourceAdapter
from pipeline.db import upsert
from pipeline.geospatial import coordinate_h3

GAZETTEER_URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_zcta_national.zip"

class ZIP5H3Adapter(BaseSourceAdapter):
    source_id = "census_zcta_gazetteer"
    source_name = "Census ZCTA Gazetteer ZIP5 H3 bridge"
    license = "Public Domain"
    def discover(self): return [GAZETTEER_URL]
    def fetch(self, url):
        response = requests.get(url, timeout=60); response.raise_for_status(); return response.content
    def parse(self, raw, url):
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            name = archive.namelist()[0]
            text = io.TextIOWrapper(archive.open(name), encoding="utf-8-sig")
            rows = []
            for row in csv.DictReader(text, delimiter="\t"):
                rows.append({str(k).strip(): str(v).strip() for k, v in row.items()})
            return rows
    def normalize(self, rows):
        records=[]
        for row in rows:
            zip5 = str(row.get("GEOID", "")).strip().zfill(5)
            cell = coordinate_h3(row.get("INTPTLAT"), row.get("INTPTLONG"), 9)
            if len(zip5) != 5 or not cell: continue
            records.append({"zip5":zip5, "state":None, "centroid_lat":float(row["INTPTLAT"]), "centroid_lng":float(row["INTPTLONG"]), "h3_cell":cell, "h3_resolution":9, "mapping_method":"zcta_centroid", "precision_class":"zcta_approximation", "source":"census_zcta_gazetteer", "source_vintage":"2024"})
        return records
    def validate(self, records): return [r for r in records if r["h3_resolution"] == 9], []
    def load(self, records, conn): return upsert(conn, "omega_zip5_h3", records, ["zip5"])
    def run(self, conn):
        rows=self.parse(self.fetch(GAZETTEER_URL), GAZETTEER_URL)
        valid, invalid=self.validate(self.normalize(rows)); n=self.load(valid, conn)
        print(f"ZCTA bridge: fetched={len(rows)} valid={len(valid)} rejected={len(invalid)} upserted={n}")
