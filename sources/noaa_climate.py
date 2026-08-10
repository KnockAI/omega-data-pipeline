"""
NOAA Climate Data Online (CDO) — weather station data.
Token: set NOAA_CDO_TOKEN in .env
API docs: https://www.ncdc.noaa.gov/cdo-web/webservices/v2
License: Public Domain (US Government)
"""
import os
import time
import requests
from datetime import date, timedelta
from tqdm import tqdm
from pipeline.base import BaseSourceAdapter, H3_R7, H3_R9
from pipeline.db import upsert

BASE_URL = "https://www.ncdc.noaa.gov/cdo-web/api/v2"

# Core elements to ingest per station
ELEMENTS = ["TMAX", "TMIN", "PRCP", "SNOW", "SNWD", "AWND"]


class NOAAClimateAdapter(BaseSourceAdapter):
    source_id = "noaa_cdo"
    source_name = "NOAA Climate Data Online"
    license = "Public Domain"

    def __init__(self, state_fips: list[str], dataset: str = "GHCND",
                 start_date: str = "2020-01-01", end_date: str = None):
        self.state_fips = state_fips
        self.dataset = dataset
        self.start_date = start_date
        self.end_date = end_date or str(date.today() - timedelta(days=1))
        self.token = os.environ.get("NOAA_CDO_TOKEN", "")
        if not self.token:
            raise ValueError("NOAA_CDO_TOKEN not set in environment")
        self.headers = {"token": self.token}

    def discover(self) -> list[str]:
        return [f"{BASE_URL}/stations"]

    def _get(self, endpoint: str, params: dict) -> dict:
        resp = requests.get(f"{BASE_URL}/{endpoint}", headers=self.headers,
                            params=params, timeout=30)
        resp.raise_for_status()
        time.sleep(0.25)  # CDO rate limit: 5 req/sec
        return resp.json()

    def fetch_stations(self, state_fips: str) -> list[dict]:
        """Fetch all GHCND stations in a state."""
        stations = []
        offset = 1
        limit = 1000
        # NOAA uses state abbreviations, not FIPS — map common ones
        fips_to_state = {"26": "MI", "12": "FL", "01": "AL", "02": "AK"}
        state_abbr = fips_to_state.get(state_fips, state_fips)
        while True:
            data = self._get("stations", {
                "datasetid": self.dataset,
                "locationid": f"FIPS:{state_fips}",
                "limit": limit,
                "offset": offset,
            })
            results = data.get("results", [])
            stations.extend(results)
            meta = data.get("metadata", {}).get("resultset", {})
            if offset + limit > meta.get("count", 0):
                break
            offset += limit
        return stations

    def fetch_data(self, station_id: str, start: str, end: str) -> list[dict]:
        """Fetch daily observations for one station over a date range."""
        observations = []
        offset = 1
        limit = 1000
        while True:
            try:
                data = self._get("data", {
                    "datasetid":  self.dataset,
                    "stationid":  station_id,
                    "startdate":  start,
                    "enddate":    end,
                    "datatypeid": ",".join(ELEMENTS),
                    "units":      "metric",
                    "limit":      limit,
                    "offset":     offset,
                })
            except Exception:
                break
            results = data.get("results", [])
            observations.extend(results)
            meta = data.get("metadata", {}).get("resultset", {})
            if offset + limit > meta.get("count", 0):
                break
            offset += limit
        return observations

    def fetch(self, url: str) -> bytes:
        return b""  # Not used directly — uses fetch_stations/fetch_data

    def parse(self, raw: bytes, url: str) -> object:
        return None

    def normalize(self, parsed: object) -> list[dict]:
        return []

    def validate(self, records):
        return records, []

    def load(self, records: list[dict], conn) -> int:
        return upsert(conn, "world_climate", records,
                      ["station_id", "dataset", "date", "element"])

    def normalize_observation(self, obs: dict, station: dict) -> dict:
        lat = station.get("latitude")
        lon = station.get("longitude")
        return {
            "source_id":    self.source_id,
            "station_id":   obs["station"],
            "station_name": station.get("name"),
            "dataset":      self.dataset,
            "date":         obs["date"][:10],
            "h3_r7":        self.h3_point(lat, lon, H3_R7) if lat and lon else None,
            "h3_r9":        self.h3_point(lat, lon, H3_R9) if lat and lon else None,
            "latitude":     lat,
            "longitude":    lon,
            "element":      obs["datatype"],
            "value":        obs["value"],
            "unit":         "metric",
            "mflag":        obs.get("fl_m"),
            "qflag":        obs.get("fl_q"),
            "sflag":        obs.get("fl_s"),
            "source_version": "v2",
        }

    def run(self, conn):
        print(f"\n=== {self.source_name} ({self.dataset}) ===")
        for fips in self.state_fips:
            print(f"  State FIPS {fips}: fetching stations...")
            stations = self.fetch_stations(fips)
            print(f"  Found {len(stations)} stations")

            for station in tqdm(stations, desc=f"  State {fips}"):
                sid = station["id"]
                obs_raw = self.fetch_data(sid, self.start_date, self.end_date)
                if not obs_raw:
                    continue
                records = [self.normalize_observation(o, station) for o in obs_raw]
                self.load(records, conn)

        print("  NOAA climate data loaded → world_climate")
