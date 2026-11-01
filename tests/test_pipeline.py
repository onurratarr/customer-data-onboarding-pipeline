import csv
import json
import os
from datetime import datetime

import pytest

from cdop import dbt_lite, dq, pipeline
from conftest import no_sleep, open_wh


def test_day1_flags_quarantined_product_then_day2_fix_succeeds(demo_runs):
    _, _, (d1, d2, d2b) = demo_runs
    assert d1["status"] == "failed_dq"
    assert any(f.startswith("relationships:stg_orders.sku") for f in d1["dq_failures"])
    assert d2["status"] == "success" and d2b["status"] == "success"
    assert d2["dq"]["fail"] == 0
    assert any(w.startswith("relationships:stg_orders.customer_id") for w in d2["dq_warnings"])


def test_quarantine_has_reasons_and_raw_rows(demo_runs):
    _, wh, (d1, *_) = demo_runs
    loads = {(l["entity"], l["source"]): l for l in d1["loads"]}
    cust = loads[("customers", "data/halvorsen/drops/2026-06-14/customers.csv")]
    assert cust["rows_in"] == cust["loaded"] + cust["quarantined"] and cust["quarantined"] > 0
    rows = wh.query("SELECT entity, errors, raw FROM _quarantine")
    errors = " ".join(r[1] for r in rows)
    for reason in ("email", "missing required value", "country", "not a number", "quantity", "status"):
        assert reason in errors, reason
    assert all(json.loads(r[2]) for r in rows)


def test_rerun_is_idempotent(demo_runs):
    _, wh, (_, d2, d2b) = demo_runs
    assert all(l.get("skipped") for l in d2b["loads"] if l["entity"] != "orders")
    assert d2b["models"] == d2["models"] and d2b["quarantine_total"] == d2["quarantine_total"]
    assert wh.scalar("SELECT count(*) FROM landing_orders") == wh.scalar(
        "SELECT count(DISTINCT order_id) FROM landing_orders")
    assert wh.scalar("SELECT count(*) FROM _file_registry") == 4


def test_incremental_orders_pick_up_status_changes(demo_runs):
    _, wh, (d1, d2, d2b) = demo_runs
    o1, o2, o3 = (next(l for l in r["loads"] if l["entity"] == "orders") for r in (d1, d2, d2b))
    assert o1["updated_since"] is None and o2["updated_since"] and o3["updated_since"] > o2["updated_since"]
    assert o2["rows_in"] < o1["rows_in"] and o3["rows_in"] <= 2
    assert o1["api_retries"] > 0
    # orders placed before day 1 whose later status change only arrived in the day-2 pull
    changed = wh.scalar("SELECT count(*) FROM landing_orders WHERE order_ts < '2026-06-14' "
                        "AND updated_at > '2026-06-15'")
    assert changed > 0


def test_customer_updates_take_latest_drop(demo_runs):
    work, wh, _ = demo_runs
    with (work / "data/halvorsen/drops/2026-06-21/customers.csv").open() as f:
        row = next(csv.DictReader(f))
    seg = wh.scalar("SELECT segment FROM stg_customers WHERE customer_id = ?", (row["Cust #"],))
    assert seg == row["Tier"].strip().lower().replace("-", "_").replace(" ", "_")


def test_schema_drift_is_logged_and_ignored(demo_runs):
    _, wh, (_, d2, _) = demo_runs
    rows = wh.query("SELECT detail FROM _audit_log WHERE run_id = ? AND step = 'schema_drift'", (d2["run_id"],))
    assert rows and "Account Manager" in rows[0][0]
    assert "Account Manager" not in [c for c, in wh.query(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'landing_customers'")]


def test_audit_trail_and_run_log(demo_runs):
    work, wh, runs = demo_runs
    assert [r[0] for r in wh.query("SELECT run_id FROM _runs ORDER BY started_at")] == \
        ["2026-06-14-r01", "2026-06-21-r01", "2026-06-21-r02"]
    steps = [r[0] for r in wh.query("SELECT step FROM _audit_log WHERE run_id = '2026-06-14-r01' ORDER BY seq")]
    assert steps[0] == "run_started" and steps[-1] == "run_finished" and "dq_checked" in steps
    log = (work / "runs/2026-06-14-r01/run_log.jsonl").read_text().splitlines()
    assert len(log) == len(steps)
    assert (work / "runs/2026-06-21-r01/dq_report.md").read_text().startswith("# Data quality report")


def test_marts(demo_runs):
    _, wh, _ = demo_runs
    assert wh.scalar("SELECT count(*) FROM fct_orders") == wh.scalar("SELECT count(*) FROM stg_orders")
    assert wh.scalar("SELECT count(*) FROM fct_orders WHERE NOT product_known") == 0
    total = wh.scalar("SELECT sum(revenue) FROM mart_monthly_revenue")
    assert total == wh.scalar("SELECT sum(recognized_revenue) FROM fct_orders")
    cols = [c for c, in wh.query("SELECT column_name FROM information_schema.columns "
                                 "WHERE table_name = 'dim_customers'")]
    assert "email" not in cols and "phone" not in cols


def test_missing_required_column_fails_run(fresh_work):
    p = fresh_work / "data/halvorsen/drops/2026-06-14/customers.csv"
    with p.open(newline="") as f:
        rows = list(csv.DictReader(f))
    with p.open("w", newline="") as f:
        w = csv.DictWriter(f, [c for c in rows[0] if c != "E-mail Address"], extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    wh = open_wh(fresh_work)
    s = pipeline.run("2026-06-14", wh, fresh_work, sleep=no_sleep)
    assert s["status"] == "error" and "E-mail Address" in s["error"]
    assert wh.scalar("SELECT count(*) FROM landing_customers") == 0
    wh.close()


def test_no_approved_mapping_fails(fresh_work, tmp_path):
    wh = open_wh(fresh_work)
    s = pipeline.run("2026-06-14", wh, fresh_work, mappings_root=tmp_path / "none")
    assert s["status"] == "error" and "no approved mapping" in s["error"]
    wh.close()


def test_freshness_check(demo_runs):
    _, wh, _ = demo_runs
    fr = lambda end: next(r for r in dq.run_checks(wh, end) if r["check"].startswith("freshness"))["status"]
    assert fr(datetime(2026, 6, 22)) == "pass"
    assert fr(datetime(2026, 6, 25)) == "warn"
    assert fr(datetime(2026, 7, 15)) == "fail"


def test_lineage_and_order():
    models = dbt_lite.discover()
    order = dbt_lite.order(models)
    assert order.index("stg_orders") < order.index("int_orders_enriched") < order.index("fct_orders")
    assert order.index("fct_orders") < order.index("mart_monthly_revenue")
    m = dbt_lite.lineage_mermaid(models)
    assert "landing_orders --> stg_orders" in m and "fct_orders --> mart_monthly_revenue" in m


@pytest.mark.skipif(not os.environ.get("CDOP_TEST_PG_URL"), reason="set CDOP_TEST_PG_URL to run Postgres tests")
def test_postgres_matches_duckdb(demo_runs, fresh_work):
    from cdop.warehouse import PostgresWarehouse
    _, duck, _ = demo_runs
    pg = PostgresWarehouse(os.environ["CDOP_TEST_PG_URL"])
    for t in sorted(pg.tables()):
        pg.execute(f"DROP TABLE IF EXISTS {t} CASCADE") if t.startswith(("_", "landing_")) else None
    for v in ("mart_monthly_revenue", "fct_orders", "dim_customers", "dim_products", "int_orders_enriched",
              "stg_orders", "stg_customers", "stg_products"):
        kind = "TABLE" if v in ("mart_monthly_revenue", "fct_orders", "dim_customers", "dim_products") else "VIEW"
        pg.execute(f"DROP {kind} IF EXISTS {v} CASCADE")
    pg.init_schema()
    runs = [pipeline.run(d, pg, fresh_work, sleep=no_sleep) for d in ("2026-06-14", "2026-06-21", "2026-06-21")]
    assert [r["status"] for r in runs] == ["failed_dq", "success", "success"]
    for q in ("SELECT count(*) FROM fct_orders", "SELECT sum(revenue) FROM mart_monthly_revenue",
              "SELECT count(*) FROM _quarantine", "SELECT count(*) FROM dim_customers"):
        assert pg.scalar(q) == duck.scalar(q), q
    pg.close()
