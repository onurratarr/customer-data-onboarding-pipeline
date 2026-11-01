"""Warehouse adapters. DuckDB (file) by default; Postgres when WAREHOUSE_URL=postgresql://... is set.
Model SQL is kept to the dialect both engines share."""
from __future__ import annotations

import csv
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

LANDING_DDL = {
    "landing_customers": """customer_id VARCHAR, email VARCHAR, full_name VARCHAR, company_name VARCHAR,
        phone VARCHAR, country_code VARCHAR, segment VARCHAR, signup_date DATE, is_active BOOLEAN""",
    "landing_products": """sku VARCHAR, product_name VARCHAR, category VARCHAR, unit_price DECIMAL(12,2),
        currency VARCHAR, is_discontinued BOOLEAN""",
    "landing_orders": """order_id VARCHAR, customer_id VARCHAR, sku VARCHAR, quantity INTEGER,
        unit_price DECIMAL(12,2), order_ts TIMESTAMP, status VARCHAR, updated_at TIMESTAMP""",
}
META_DDL = "_load_id VARCHAR, _source VARCHAR, _source_row INTEGER, _load_seq INTEGER"
SYSTEM_DDL = {
    "_quarantine": """run_id VARCHAR, load_id VARCHAR, entity VARCHAR, source VARCHAR, row_number INTEGER,
        errors VARCHAR, raw VARCHAR, raw_sha VARCHAR""",
    "_file_registry": """sha256 VARCHAR PRIMARY KEY, path VARCHAR, entity VARCHAR, load_id VARCHAR,
        run_id VARCHAR, rows_in INTEGER, rows_loaded INTEGER, rows_quarantined INTEGER""",
    "_watermarks": "source_key VARCHAR PRIMARY KEY, value VARCHAR, run_id VARCHAR",
    "_runs": """run_id VARCHAR PRIMARY KEY, as_of DATE, started_at TIMESTAMP, finished_at TIMESTAMP,
        status VARCHAR, mapping_versions VARCHAR, summary VARCHAR""",
    "_audit_log": "run_id VARCHAR, seq INTEGER, ts TIMESTAMP, step VARCHAR, entity VARCHAR, detail VARCHAR",
    "_dq_results": """run_id VARCHAR, check_name VARCHAR, model VARCHAR, severity VARCHAR, status VARCHAR,
        failing_rows INTEGER, detail VARCHAR""",
}


class Warehouse:
    kind = "base"
    param = "?"

    def q(self, sql: str) -> str:
        return sql if self.param == "?" else sql.replace("?", self.param)

    def init_schema(self):
        for t, cols in LANDING_DDL.items():
            self.execute(f"CREATE TABLE IF NOT EXISTS {t} ({cols}, {META_DDL})")
        for t, cols in SYSTEM_DDL.items():
            self.execute(f"CREATE TABLE IF NOT EXISTS {t} ({cols})")

    def scalar(self, sql, params=()):
        r = self.query(sql, params)
        return r[0][0] if r else None

    def tables(self) -> set[str]:
        return {r[0] for r in self.query(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = " + self.schema_expr)}


class DuckDBWarehouse(Warehouse):
    kind = "duckdb"
    schema_expr = "'main'"

    def __init__(self, path: str | Path):
        import duckdb
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(self.path)

    def execute(self, sql, params=()):
        self.con.execute(sql, list(params))

    def query(self, sql, params=()):
        return self.con.execute(sql, list(params)).fetchall()

    def columns(self, sql, params=()):
        cur = self.con.execute(sql, list(params))
        return [d[0] for d in cur.description], cur.fetchall()

    @contextmanager
    def transaction(self):
        self.con.execute("BEGIN TRANSACTION")
        try:
            yield
            self.con.execute("COMMIT")
        except BaseException:
            self.con.execute("ROLLBACK")
            raise

    def bulk_insert(self, table, cols, rows):
        if not rows:
            return
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as f:
            w = csv.writer(f)
            for r in rows:
                w.writerow(["\\N" if v is None else v for v in r])
        try:
            self.con.execute(f"COPY {table} ({', '.join(cols)}) FROM '{f.name}' (HEADER false, NULLSTR '\\N')")
        finally:
            os.unlink(f.name)

    def close(self):
        self.con.close()


class PostgresWarehouse(Warehouse):
    kind = "postgres"
    param = "%s"
    schema_expr = "current_schema()"

    def __init__(self, url: str):
        import psycopg  # optional dependency: pip install -r requirements-postgres.txt
        self.url = url
        self.con = psycopg.connect(url, autocommit=True)
        self._tx = None

    def execute(self, sql, params=()):
        with self.con.cursor() as cur:
            cur.execute(self.q(sql), list(params) or None)

    def query(self, sql, params=()):
        with self.con.cursor() as cur:
            cur.execute(self.q(sql), list(params) or None)
            return cur.fetchall()

    def columns(self, sql, params=()):
        with self.con.cursor() as cur:
            cur.execute(self.q(sql), list(params) or None)
            return [d.name for d in cur.description], cur.fetchall()

    @contextmanager
    def transaction(self):
        with self.con.transaction():
            yield

    def bulk_insert(self, table, cols, rows):
        if not rows:
            return
        with self.con.cursor() as cur:
            with cur.copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN") as cp:
                for r in rows:
                    cp.write_row(r)

    def close(self):
        self.con.close()


def connect(workdir: Path | None = None) -> Warehouse:
    url = os.environ.get("WAREHOUSE_URL", "")
    if url.startswith(("postgresql://", "postgres://")):
        wh = PostgresWarehouse(url)
    else:
        from .paths import workdir as wd
        wh = DuckDBWarehouse(url.removeprefix("duckdb:///") if url else (workdir or wd()) / "warehouse.duckdb")
    wh.init_schema()
    return wh
