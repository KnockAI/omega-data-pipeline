"""
Census County Business Patterns — establishment counts and payroll by NAICS.
Source: https://api.census.gov/data/{year}/cbp
License: Public Domain (US Government)
Cadence: Annual (data year N-2 released in ~October)

Aggregated to county level. No API key required.
"""
import os
import requests
from pipeline.base import BaseSourceAdapter
from pipeline.db import upsert

CBP_API = "https://api.census.gov/data/{year}/cbp"

# NAICS codes we care about for economic health signals
NAICS_TARGETS = {
    "00": "total_all_industries",
    "23": "construction",
    "31-33": "manufacturing",
    "44-45": "retail_trade",
    "52": "finance_insurance",
    "54": "professional_scientific",
    "62": "health_care",
    "72": "accommodation_food",
}


class CBPAdapter(BaseSourceAdapter):
    source_id = "cbp_business"
    source_name = "Census County Business Patterns"
    license = "Public Domain"

    def __init__(self, state_fips: list[str], year: int = 2022):
        self.state_fips = state_fips
        self.year = year
        self.api_key = os.environ.get("CENSUS_API_KEY", "")

    def discover(self) -> list[str]:
        return [CBP_API.format(year=self.year)]

    def fetch(self, url: str) -> bytes:
        return b""

    def parse(self, raw: bytes, url: str) -> object:
        return None

    def normalize(self, parsed: object) -> list[dict]:
        return []

    def validate(self, records):
        valid = [r for r in records if r.get("county_fips") and r.get("state_fips")]
        return valid, []

    def load(self, records: list[dict], conn) -> int:
        return upsert(conn, "omega_cbp",
                      records,
                      ["state_fips", "county_fips", "naics_code", "year"])

    def _fetch_state(self, state_fips: str) -> list[dict]:
        url = CBP_API.format(year=self.year)
        records = []
        for naics, label in NAICS_TARGETS.items():
            params = {
                "get": "NAICS2017,ESTAB,EMP,PAYANN",
                "for": "county:*",
                "in": f"state:{state_fips.zfill(2)}",
                "NAICS2017": naics,
            }
            if self.api_key:
                params["key"] = self.api_key
            try:
                resp = requests.get(url, params=params, timeout=30)
                resp.raise_for_status()
                rows = resp.json()
            except Exception as e:
                print(f"  WARNING: CBP fetch failed for NAICS {naics}: {e}")
                continue
            if not rows or len(rows) < 2:
                continue
            headers = rows[0]
            for row in rows[1:]:
                d = dict(zip(headers, row))
                def _int(k):
                    try:
                        return int(d[k]) if d.get(k) not in (None, "N", "D") else None
                    except (ValueError, TypeError):
                        return None
                records.append({
                    "state_fips":    state_fips.zfill(2),
                    "county_fips":   d.get("county", "").zfill(3),
                    "naics_code":    naics,
                    "naics_label":   label,
                    "year":          self.year,
                    "establishments": _int("ESTAB"),
                    "employment":    _int("EMP"),
                    "annual_payroll_thousands": _int("PAYANN"),
                })
        return records

    def run(self, conn):
        print(f"\n=== {self.source_name} ({self.year}) ===")
        self._ensure_table(conn)

        for sfips in self.state_fips:
            print(f"  State {sfips}: fetching CBP county data...")
            try:
                records = self._fetch_state(sfips)
            except Exception as e:
                print(f"  WARNING: CBP failed for {sfips}: {e}")
                continue
            valid, _ = self.validate(records)
            n = self.load(valid, conn)
            print(f"  State {sfips}: loaded {n} rows → omega_cbp")

    def _ensure_table(self, conn):
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS omega_cbp (
                    id                         BIGSERIAL PRIMARY KEY,
                    state_fips                 CHAR(2)  NOT NULL,
                    county_fips                CHAR(3)  NOT NULL,
                    naics_code                 TEXT     NOT NULL,
                    naics_label                TEXT,
                    year                       SMALLINT NOT NULL,
                    establishments             INT,
                    employment                 INT,
                    annual_payroll_thousands   INT,
                    ingested_at                TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (state_fips, county_fips, naics_code, year)
                );
                CREATE INDEX IF NOT EXISTS idx_cbp_state_county
                    ON omega_cbp(state_fips, county_fips);
            """)
        conn.commit()
