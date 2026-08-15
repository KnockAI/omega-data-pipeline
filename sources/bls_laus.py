"""
BLS LAUS — Local Area Unemployment Statistics.
Source: https://api.bls.gov/publicAPI/v2/timeseries/data/
License: Public Domain (US Government)
Cadence: Monthly (data lags ~4 weeks)

No API key required for basic access (500 req/day limit).
Set BLS_API_KEY in .env for 500→50k req/day upgrade (free registration).
"""
import os
import time
import requests
from datetime import date
from pipeline.base import BaseSourceAdapter
from pipeline.db import upsert
from pipeline.geospatial import safe_backoff

BLS_API = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# LAUS county series format: LAUCN + FIPS(5) + 000000 + measure code
# County FIPS is 5 digits; LAUS pads to 7 with leading zeros.
# We fetch unemployment rate (03), employment level (05), labor force (06).
MEASURE_CODES = {
    "03": "unemployment_rate",
    "05": "employment",
    "06": "labor_force",
}


def _county_series_id(state_fips: str, county_fips: str, measure: str) -> str:
    fips5 = state_fips.zfill(2) + county_fips.zfill(3)
    return f"LAUCN{fips5}00000000{measure}"


class BLSLAUSAdapter(BaseSourceAdapter):
    source_id = "bls_laus"
    source_name = "BLS Local Area Unemployment Statistics"
    license = "Public Domain"

    def __init__(self, state_fips: list[str]):
        self.state_fips = state_fips
        self.api_key = os.environ.get("BLS_API_KEY", "")
        # BLS LAUS lags ~4 weeks; use prior year as end to ensure data exists
        self.end_year = date.today().year - 1
        self.start_year = self.end_year - 1

    def discover(self) -> list[str]:
        return [BLS_API]

    def fetch(self, url: str) -> bytes:
        return b""

    def parse(self, raw: bytes, url: str) -> object:
        return None

    def normalize(self, parsed: object) -> list[dict]:
        return []

    def validate(self, records):
        valid = [r for r in records if r.get("county_fips") and r.get("year")]
        return valid, []

    def load(self, records: list[dict], conn) -> int:
        return upsert(conn, "omega_bls_laus", records,
                      ["state_fips", "county_fips", "year", "period", "measure"])

    def _fetch_series(self, series_ids: list[str]) -> list[dict]:
        payload = {
            "seriesid": series_ids,
            "startyear": str(self.start_year),
            "endyear": str(self.end_year),
        }
        if self.api_key:
            payload["registrationkey"] = self.api_key
        last_error = None
        for attempt in range(1, 4):
            try:
                resp = requests.post(BLS_API, json=payload, timeout=30)
                if resp.status_code == 429:
                    time.sleep(safe_backoff(attempt)); continue
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") != "REQUEST_SUCCEEDED":
                    msg = str(data.get("message", data))
                    if "rate" in msg.lower() or "limit" in msg.lower():
                        time.sleep(safe_backoff(attempt)); continue
                    raise RuntimeError(f"BLS API error: {msg}")
                time.sleep(0.5)
                return data.get("Results", {}).get("series", [])
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                last_error = exc; time.sleep(safe_backoff(attempt))
        raise RuntimeError(f"BLS request failed after bounded retries: {last_error}")

    def _counties_for_state(self, state_fips: str, conn=None) -> list[str]:
        """Return list of county FIPS (3-digit) for a state via Census API."""
        sfips = state_fips.zfill(2)
        if conn is not None:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT county_fips FROM world_boundaries WHERE boundary_type='county' AND state_fips=%s AND county_fips IS NOT NULL", (sfips,))
                local = [str(row[0]).zfill(3) for row in cur.fetchall()]
            if local:
                return sorted(local)
        key = os.environ.get("CENSUS_API_KEY", "")
        url = f"https://api.census.gov/data/2020/dec/pl?get=NAME&for=county:*&in=state:{sfips}"
        if key:
            url += f"&key={key}"
        last_error = None
        for attempt in range(1, 4):
            try:
                resp = requests.get(url, timeout=30)
                resp.raise_for_status()
                rows = resp.json()
                break
            except (requests.RequestException, ValueError) as exc:
                last_error = exc; time.sleep(safe_backoff(attempt))
        else:
            raise RuntimeError(f"Census county lookup failed after bounded retries: {last_error}")
        # rows[0] is header: ["NAME","state","county"]
        return [row[2] for row in rows[1:]]

    def run(self, conn):
        print(f"\n=== {self.source_name} ({self.start_year}-{self.end_year}) ===")
        self._ensure_table(conn)

        for sfips in self.state_fips:
            print(f"  State {sfips}: fetching county list...")
            try:
                counties = self._counties_for_state(sfips, conn)
            except Exception as e:
                print(f"  WARNING: could not fetch counties for {sfips}: {e}")
                continue
            print(f"  Found {len(counties)} counties")

            # BLS accepts 50 series per request
            records = []
            for measure_code, measure_name in MEASURE_CODES.items():
                series_ids = [_county_series_id(sfips, c, measure_code) for c in counties]
                for i in range(0, len(series_ids), 50):
                    batch = series_ids[i:i + 50]
                    batch_counties = counties[i:i + 50]
                    try:
                        series_list = self._fetch_series(batch)
                    except Exception as e:
                        print(f"  WARNING: BLS fetch failed for batch: {e}")
                        continue
                    for series, county_fips in zip(series_list, batch_counties):
                        for obs in series.get("data", []):
                            try:
                                records.append({
                                    "state_fips":   sfips.zfill(2),
                                    "county_fips":  county_fips.zfill(3),
                                    "year":         int(obs["year"]),
                                    "period":       obs["period"],  # M01-M12 or Q01-Q04
                                    "measure":      measure_name,
                                    "value":        float(obs["value"]) if obs.get("value") not in (None, "-") else None,
                                    "footnotes":    str(obs.get("footnotes", "")),
                                    "source_version": "v2",
                                })
                            except (ValueError, KeyError):
                                pass

            valid, _ = self.validate(records)
            n = self.load(valid, conn)
            print(f"  State {sfips}: loaded {n} rows → omega_bls_laus")

    def _ensure_table(self, conn):
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS omega_bls_laus (
                    id              BIGSERIAL PRIMARY KEY,
                    state_fips      CHAR(2)  NOT NULL,
                    county_fips     CHAR(3)  NOT NULL,
                    year            SMALLINT NOT NULL,
                    period          CHAR(3)  NOT NULL,  -- M01-M12
                    measure         TEXT     NOT NULL,  -- unemployment_rate, employment, labor_force
                    value           NUMERIC(10,2),
                    footnotes       TEXT,
                    source_version  TEXT,
                    ingested_at     TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (state_fips, county_fips, year, period, measure)
                );
                CREATE INDEX IF NOT EXISTS idx_bls_laus_state_county
                    ON omega_bls_laus(state_fips, county_fips);
                CREATE INDEX IF NOT EXISTS idx_bls_laus_year_period
                    ON omega_bls_laus(year, period);
            """)
        conn.commit()
