"""Small, fail-closed ingestion run registry shared by every adapter."""
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

STATUSES = {"pending", "running", "complete", "partial", "failed", "rate_limited", "blocked"}

def begin_run(conn, source_key: str, dataset_key: str, geography: str = "unknown", *, source_vintage=None, source_url=None, adapter_version="1") -> int:
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO omega_ingestion_runs
          (source_key,dataset_key,geography,status,source_vintage,source_url,adapter_version)
          VALUES (%s,%s,%s,'running',%s,%s,%s) RETURNING id""",
          (source_key, dataset_key, geography, source_vintage, source_url, adapter_version))
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id

def finish_run(conn, run_id: int, status: str, *, fetched=0, inserted=0, updated=0, rejected=0, coverage=None, freshness=None, error=None, retry_count=0):
    if status not in STATUSES or status in {"pending", "running"}:
        raise ValueError(f"invalid terminal ingestion status: {status}")
    with conn.cursor() as cur:
        cur.execute("""UPDATE omega_ingestion_runs SET completed_at=%s,status=%s,
          records_fetched=%s,records_inserted=%s,records_updated=%s,records_rejected=%s,
          coverage=%s,freshness=%s,error_summary=%s,retry_count=%s WHERE id=%s""",
          (datetime.now(timezone.utc), status, fetched, inserted, updated, rejected,
           coverage, freshness, error[:2000] if error else None, retry_count, run_id))
    conn.commit()

@contextmanager
def tracked_run(conn, source_key, dataset_key, geography="unknown", **kwargs) -> Iterator[int]:
    run_id = begin_run(conn, source_key, dataset_key, geography, **kwargs)
    try:
        yield run_id
    except Exception as exc:
        finish_run(conn, run_id, "failed", error=str(exc))
        raise
