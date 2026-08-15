"""
Socrata Building Permits — city/county permit datasets.
License: Varies by municipality (generally Public Domain / Open Data)
Cadence: Daily or weekly (depends on city)

Each city has its own Socrata domain and dataset ID.
Configure via PERMIT_DATASETS env var (JSON array) or use KNOWN_DATASETS below.
Format: [{"domain": "data.cityofchicago.org", "dataset_id": "ydr8-5enu", "city": "Chicago, IL"}]
"""
import os
import json
import requests
from pipeline.base import BaseSourceAdapter
from pipeline.db import upsert

SOCRATA_API = "https://{domain}/resource/{dataset_id}.json"
PAGE_SIZE = 5000

KNOWN_DATASETS = [
    {"domain": "data.cityofchicago.org",    "dataset_id": "ydr8-5enu", "city": "Chicago, IL"},
    {"domain": "data.lacity.org",           "dataset_id": "yv23-pmwf", "city": "Los Angeles, CA"},
    {"domain": "data.sfgov.org",            "dataset_id": "i98e-djp9", "city": "San Francisco, CA"},
    {"domain": "data.seattle.gov",          "dataset_id": "76t5-zqzr", "city": "Seattle, WA"},
    {"domain": "data.austintexas.gov",      "dataset_id": "3syk-w9eu", "city": "Austin, TX"},
    {"domain": "opendata.lasvegasnevada.gov","dataset_id": "ycm5-pmxs", "city": "Las Vegas, NV"},
]


class BuildingPermitsAdapter(BaseSourceAdapter):
    source_id = "building_permits"
    source_name = "Socrata Building Permits"
    license = "Open Data (varies)"

    def __init__(self, datasets: list[dict] | None = None):
        raw = os.environ.get("PERMIT_DATASETS")
        if raw:
            try:
                self.datasets = json.loads(raw)
            except json.JSONDecodeError:
                self.datasets = KNOWN_DATASETS
        else:
            self.datasets = datasets or KNOWN_DATASETS
        self.app_token = os.environ.get("SOCRATA_APP_TOKEN", "")

    def discover(self) -> list[str]:
        return [
            SOCRATA_API.format(domain=d["domain"], dataset_id=d["dataset_id"])
            for d in self.datasets
        ]

    def fetch(self, url: str) -> bytes:
        return b""

    def parse(self, raw: bytes, url: str) -> object:
        return None

    def normalize(self, parsed: object) -> list[dict]:
        return []

    def validate(self, records):
        valid = [r for r in records if r.get("city") and r.get("permit_number")]
        return valid, []

    def load(self, records: list[dict], conn) -> int:
        return upsert(conn, "omega_building_permits",
                      records,
                      ["city", "permit_number"])

    def _fetch_dataset(self, domain: str, dataset_id: str, city: str) -> list[dict]:
        url = SOCRATA_API.format(domain=domain, dataset_id=dataset_id)
        headers = {}
        if self.app_token:
            headers["X-App-Token"] = self.app_token

        records = []
        offset = 0
        while True:
            params = {"$limit": PAGE_SIZE, "$offset": offset, "$order": ":id"}
            resp = requests.get(url, params=params, headers=headers, timeout=30)
            resp.raise_for_status()
            rows = resp.json()
            if not rows:
                break
            for row in rows:
                records.append({
                    "city":           city,
                    "permit_number":  str(row.get("permit_number") or row.get("permit_num") or row.get("id", "")),
                    "permit_type":    str(row.get("permit_type") or row.get("work_type") or ""),
                    "issue_date":     str(row.get("issue_date") or row.get("issued_date") or ""),
                    "status":         str(row.get("status") or row.get("permit_status") or ""),
                    "estimated_cost": _safe_float(row, "estimated_cost", "cost"),
                    "address":        str(row.get("site_address") or row.get("address") or ""),
                    "latitude":       _safe_float(row, "latitude", "lat"),
                    "longitude":      _safe_float(row, "longitude", "lon", "lng"),
                    "raw":            json.dumps(row),
                })
            offset += PAGE_SIZE
            if len(rows) < PAGE_SIZE:
                break
        return records

    def run(self, conn):
        print(f"\n=== {self.source_name} ===")
        self._ensure_table(conn)

        for ds in self.datasets:
            domain     = ds["domain"]
            dataset_id = ds["dataset_id"]
            city       = ds["city"]
            print(f"  {city}: fetching from {domain}...")
            try:
                records = self._fetch_dataset(domain, dataset_id, city)
            except Exception as e:
                print(f"  WARNING: {city} fetch failed: {e}")
                continue
            valid, _ = self.validate(records)
            n = self.load(valid, conn)
            print(f"  {city}: loaded {n} permits → omega_building_permits")

    def _ensure_table(self, conn):
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS omega_building_permits (
                    id              BIGSERIAL PRIMARY KEY,
                    city            TEXT     NOT NULL,
                    permit_number   TEXT     NOT NULL,
                    permit_type     TEXT,
                    issue_date      TEXT,
                    status          TEXT,
                    estimated_cost  NUMERIC(14,2),
                    address         TEXT,
                    latitude        NUMERIC(10,6),
                    longitude       NUMERIC(10,6),
                    raw             JSONB,
                    ingested_at     TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (city, permit_number)
                );
                CREATE INDEX IF NOT EXISTS idx_permits_city
                    ON omega_building_permits(city);
                CREATE INDEX IF NOT EXISTS idx_permits_issue_date
                    ON omega_building_permits(issue_date);
            """)
        conn.commit()


def _safe_float(row: dict, *keys) -> float | None:
    for k in keys:
        v = row.get(k)
        if v is not None:
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
    return None
