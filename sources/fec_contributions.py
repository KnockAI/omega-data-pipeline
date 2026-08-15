"""
FEC Bulk Contributions — individual contributions to federal committees.
Source: https://www.fec.gov/data/bulk-data-files/
License: Public Domain (US Government)
Cadence: Quarterly (Q1=April, Q2=July, Q3=October, Q4=January)

Populates: fec_records in Knock prod DB (keyed by h3_index_9).
ZIP5 → H3 centroid mapping uses TIGER ZIP-tract crosswalk stored locally.

FEC_CYCLE env var: "2024" or "2026" (default: current even year)
No API key required. Bulk files are public.
"""
import os
import csv
import zipfile
import io
import requests
from datetime import date
from pipeline.base import BaseSourceAdapter
from pipeline.db import upsert

FEC_BULK_BASE = "https://www.fec.gov/files/bulk-downloads"

# itcont.txt columns (pipe-delimited, no header)
FEC_COLUMNS = [
    "cmte_id", "amndt_ind", "rpt_tp", "transaction_pgi", "image_num",
    "transaction_tp", "entity_tp", "name", "city", "state", "zip_code",
    "employer", "occupation", "transaction_dt", "transaction_amt",
    "other_id", "tran_id", "file_num", "memo_cd", "memo_text", "sub_id",
]


def _even_year() -> int:
    y = date.today().year
    return y if y % 2 == 0 else y - 1


class FECContributionsAdapter(BaseSourceAdapter):
    source_id = "fec_contributions"
    source_name = "FEC Individual Contributions"
    license = "Public Domain"

    def __init__(self, zip5_to_h3: dict[str, str]):
        """
        zip5_to_h3: mapping of ZIP5 → H3 index (res 9).
        Build this from the census TIGER ZIP-tract crosswalk or pass a preloaded dict.
        """
        self.zip5_to_h3 = zip5_to_h3
        self.unmatched_zip5 = 0
        cycle = os.environ.get("FEC_CYCLE", str(_even_year()))
        self.cycle = cycle  # e.g. "2026"
        self.url = f"{FEC_BULK_BASE}/{cycle}/indiv{cycle[2:]}.zip"

    def discover(self) -> list[str]:
        return [self.url]

    def fetch(self, url: str) -> bytes:
        resp = requests.get(url, stream=True, timeout=120)
        resp.raise_for_status()
        return resp.content

    def parse(self, raw: bytes, url: str) -> list[dict]:
        rows = []
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            name = next(n for n in z.namelist() if n.endswith(".txt"))
            with z.open(name) as f:
                reader = csv.reader(io.TextIOWrapper(f, encoding="latin-1"), delimiter="|")
                for line in reader:
                    if len(line) < len(FEC_COLUMNS):
                        continue
                    rows.append(dict(zip(FEC_COLUMNS, line)))
        return rows

    def normalize(self, parsed: list[dict]) -> list[dict]:
        records = []
        for row in parsed:
            zip5 = (row.get("zip_code") or "")[:5]
            mapping = self.zip5_to_h3.get(zip5)
            if not mapping:
                self.unmatched_zip5 += 1
                continue
            if isinstance(mapping, str):
                mapping = {"h3_cell": mapping, "precision_class": "zcta_approximation", "mapping_method": "legacy"}
            try:
                amt = int(float(row["transaction_amt"]))
            except (ValueError, TypeError):
                continue
            if amt <= 0:
                continue
            try:
                dt_raw = row.get("transaction_dt", "")
                # FEC format: MMDDYYYY
                if len(dt_raw) == 8:
                    contrib_date = f"{dt_raw[4:]}-{dt_raw[:2]}-{dt_raw[2:4]}"
                else:
                    continue
            except Exception:
                continue
            records.append({
                "h3_index_9":       mapping["h3_cell"],
                "committee_id":     row.get("cmte_id", "")[:9],
                "amount_cents":     amt * 100,
                "contribution_date": contrib_date,
                "zip5":             zip5,
                "cycle":            self.cycle,
                "precision_class": mapping["precision_class"],
                "mapping_method": mapping["mapping_method"],
                "source_vintage": self.cycle,
            })
        return records

    def validate(self, records):
        valid = [r for r in records if r.get("h3_index_9") and r.get("contribution_date")]
        return valid, []

    def load(self, records: list[dict], conn) -> int:
        return upsert(conn, "fec_records",
                      records,
                      ["h3_index_9", "committee_id", "contribution_date", "zip5"])

    def run(self, conn):
        print(f"\n=== {self.source_name} (cycle {self.cycle}) ===")
        self._ensure_table(conn)
        print(f"  Downloading {self.url} ...")
        try:
            raw = self.fetch(self.url)
        except Exception as e:
            print(f"  ERROR: {e}")
            return
        print(f"  Parsing {len(raw):,} bytes...")
        parsed = self.parse(raw, self.url)
        print(f"  Parsed {len(parsed):,} raw rows")
        records = self.normalize(parsed)
        print(f"  Normalized {len(records):,} H3-mapped records")
        valid, _ = self.validate(records)
        n = self.load(valid, conn)
        print(f"  Loaded {n} rows → fec_records; unmatched_zip5={self.unmatched_zip5}")

    def _ensure_table(self, conn):
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS fec_records (
                id BIGSERIAL PRIMARY KEY, h3_index_9 TEXT NOT NULL,
                committee_id TEXT NOT NULL, amount_cents BIGINT NOT NULL,
                contribution_date DATE NOT NULL, zip5 CHAR(5), cycle TEXT NOT NULL,
                precision_class TEXT NOT NULL DEFAULT 'zcta_approximation',
                mapping_method TEXT, source_vintage TEXT,
                ingested_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (h3_index_9, committee_id, contribution_date, zip5)
            );""")
        conn.commit()
