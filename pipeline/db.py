import os
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()


def get_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def apply_schema(conn, schema_path: str = "schema/world_tables.sql"):
    with open(schema_path) as f:
        sql = f.read()
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    print("Schema applied.")


def get_zip5_h3_map(conn) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute("SELECT zip5, h3_cell, precision_class, mapping_method FROM omega_zip5_h3")
        return {row[0]: {"h3_cell": row[1], "precision_class": row[2], "mapping_method": row[3]} for row in cur.fetchall()}


def upsert(conn, table: str, records: list[dict], conflict_cols: list[str],
           batch_size: int = 2000):
    if not records:
        return 0
    cols = list(records[0].keys())
    conflict = ", ".join(conflict_cols)
    update = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c not in conflict_cols)
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s "
        f"ON CONFLICT ({conflict}) DO UPDATE SET {update}"
    )
    total = 0
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        values = [[r[c] for c in cols] for r in batch]
        with conn.cursor() as cur:
            execute_values(cur, sql, values, template=None, page_size=500)
        conn.commit()
        total += len(batch)
    return total


def insert_many(conn, table: str, records: list[dict]):
    if not records:
        return 0
    cols = list(records[0].keys())
    values = [[r[c] for c in cols] for r in records]
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s ON CONFLICT DO NOTHING"
    with conn.cursor() as cur:
        execute_values(cur, sql, values, page_size=500)
    conn.commit()
    return len(records)
