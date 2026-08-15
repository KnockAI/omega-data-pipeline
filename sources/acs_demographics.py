"""
Census ACS 5-Year Estimates — tract-level demographics.
Source: https://api.census.gov/data/{year}/acs/acs5
License: Public Domain (US Government)
Cadence: Annual (December release, data year N-2)

Populates: census_tract_demographics in Knock prod DB (via DATABASE_URL).
No API key required (1000 req/day per IP; CENSUS_API_KEY lifts limit).
"""
import os
import requests
from pipeline.base import BaseSourceAdapter
from pipeline.db import upsert

CENSUS_API = "https://api.census.gov/data/{year}/acs/acs5"

ACS_VARIABLES = {
    "B19013_001E": "median_household_income",
    "B01002_001E": "median_age",
    "B25003_002E": "_owner_occupied",
    "B25003_001E": "_total_occupied",
    "B15003_022E": "_bachelors",
    "B15003_023E": "_masters",
    "B15003_024E": "_professional",
    "B15003_025E": "_doctorate",
    "B15003_001E": "_edu_total",
    "B01003_001E": "total_population",
    "B25077_001E": "median_home_value",
}


class ACSAdapter(BaseSourceAdapter):
    source_id = "acs_demographics"
    source_name = "Census ACS 5-Year Tract Demographics"
    license = "Public Domain"

    def __init__(self, state_fips: list[str], year: int = 2023):
        self.state_fips = state_fips
        self.year = year
        self.api_key = os.environ.get("CENSUS_API_KEY", "")

    def discover(self) -> list[str]:
        return [CENSUS_API.format(year=self.year)]

    def fetch(self, url: str) -> bytes:
        return b""

    def parse(self, raw: bytes, url: str) -> object:
        return None

    def normalize(self, parsed: object) -> list[dict]:
        return []

    def validate(self, records):
        valid = [r for r in records if r.get("tract_fips")]
        return valid, []

    def load(self, records: list[dict], conn) -> int:
        return upsert(conn, "census_tract_demographics", records, ["tract_fips"])

    def _fetch_state(self, state_fips: str) -> list[dict]:
        vars_param = ",".join(ACS_VARIABLES.keys())
        url = CENSUS_API.format(year=self.year)
        params = {
            "get": f"NAME,{vars_param}",
            "for": "tract:*",
            "in": f"state:{state_fips.zfill(2)}",
        }
        if self.api_key:
            params["key"] = self.api_key
        resp = requests.get(url, params=params, timeout=60)
        resp.raise_for_status()
        rows = resp.json()
        if not rows or len(rows) < 2:
            return []

        headers = rows[0]
        records = []
        for row in rows[1:]:
            d = dict(zip(headers, row))
            state = d.get("state", "").zfill(2)
            county = d.get("county", "").zfill(3)
            tract = d.get("tract", "").zfill(6)
            tract_fips = state + county + tract  # 11-digit

            def _int(k):
                v = d.get(k)
                try:
                    return int(float(v)) if v and v not in ("-666666666", "-999999999") else None
                except (ValueError, TypeError):
                    return None

            owner_occ = _int("B25003_002E")
            total_occ = _int("B25003_001E")
            ba = _int("B15003_022E") or 0
            ma = _int("B15003_023E") or 0
            prof = _int("B15003_024E") or 0
            doc = _int("B15003_025E") or 0
            edu_total = _int("B15003_001E")

            records.append({
                "tract_fips":              tract_fips,
                "state_fips":              state,
                "county_fips":             county,
                "median_household_income": _int("B19013_001E"),
                "median_age":              _int("B01002_001E"),
                "pct_owner_occupied":      round(owner_occ / total_occ, 4) if owner_occ and total_occ else None,
                "pct_bachelors_plus":      round((ba + ma + prof + doc) / edu_total, 4) if edu_total else None,
                "total_population":        _int("B01003_001E"),
                "median_home_value":       _int("B25077_001E"),
                "acs_vintage":             self.year,
            })
        return records

    def run(self, conn):
        print(f"\n=== {self.source_name} ({self.year}) ===")
        self._ensure_column(conn)

        for sfips in self.state_fips:
            print(f"  State {sfips}: fetching ACS tract data...")
            try:
                records = self._fetch_state(sfips)
            except Exception as e:
                print(f"  WARNING: ACS fetch failed for {sfips}: {e}")
                continue
            valid, _ = self.validate(records)
            n = self.load(valid, conn)
            print(f"  State {sfips}: loaded {n} tracts → census_tract_demographics")

    def _ensure_column(self, conn):
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS census_tract_demographics (
                    tract_fips                TEXT PRIMARY KEY,
                    state_fips                CHAR(2),
                    county_fips               CHAR(3),
                    median_household_income   INT,
                    median_age                INT,
                    pct_owner_occupied        NUMERIC(6,4),
                    pct_bachelors_plus        NUMERIC(6,4),
                    total_population          INT,
                    median_home_value         INT,
                    acs_vintage               SMALLINT,
                    updated_at                TIMESTAMPTZ DEFAULT NOW()
                );
                ALTER TABLE census_tract_demographics
                    ADD COLUMN IF NOT EXISTS acs_vintage SMALLINT;
            """)
        conn.commit()
