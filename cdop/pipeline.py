"""One onboarding run for a given `as_of` date:

1. read the latest *approved* mapping per entity (never an unreviewed proposal)
2. CSV drops <= as_of that were not loaded before (sha256 file registry -> re-runs skip them)
3. orders from the REST API since the last watermark (paginated, retried), upserted by order_id
4. map -> contract-validate -> land valid rows, quarantine bad ones (idempotent by row hash)
5. rebuild staging -> intermediate -> marts, run data quality checks, write report + audit log
"""
from __future__ import annotations

import csv
import hashlib
import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import api as api_mod
from . import dbt_lite, dq, review
from .contracts import FIELDS, validate_rows
from .mapping import CANONICAL
from .paths import workdir

SOURCE = "halvorsen"
CSV_ENTITIES = ("customers", "products")


class PipelineError(RuntimeError):
    pass


class Audit:
    def __init__(self, wh, run_id, log_path: Path):
        self.wh, self.run_id, self.seq, self.path = wh, run_id, 0, log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.events = []

    def __call__(self, step, entity=None, **detail):
        self.seq += 1
        ts = datetime.now(timezone.utc).replace(tzinfo=None)
        ev = {"run_id": self.run_id, "seq": self.seq, "ts": ts.isoformat(timespec="seconds"), "step": step,
              "entity": entity, **detail}
        self.events.append(ev)
        with self.path.open("a") as f:
            f.write(json.dumps(ev, default=str) + "\n")
        self.wh.execute("INSERT INTO _audit_log VALUES (?, ?, ?, ?, ?, ?)",
                        (self.run_id, self.seq, ts, step, entity, json.dumps(detail, default=str)))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _row_sha(entity, row) -> str:
    return hashlib.sha256((entity + json.dumps(row, sort_keys=True, default=str)).encode()).hexdigest()[:20]


def _apply_mapping(rows: list[dict], mapping: dict[str, str]) -> list[dict]:
    return [{tgt: r.get(src) for src, tgt in mapping.items()} for r in rows]


def _check_drift(entity, columns: list[str], doc: dict, audit) -> None:
    known = set(doc["source_columns"])
    new = [c for c in columns if c not in known]
    gone = [c for c in known if c not in columns]
    mapping = review.mapping_dict(doc)
    required = [f for f, s in CANONICAL[entity]["fields"].items() if s.get("required")]
    lost_required = [c for c in gone if mapping.get(c) in required]
    if new or gone:
        audit("schema_drift", entity, new_columns=new, missing_columns=gone, mapping_version=doc["version"],
              action="failed" if lost_required else "new columns ignored until mapped and reviewed")
    if lost_required:
        raise PipelineError(f"{entity}: source no longer has column(s) {lost_required} mapped to required fields; "
                            f"re-run `map` + `review` for {doc['source']}/{entity}")


def _land(wh, entity, valid, bad, raw_rows, load_id, source, run_id, seq, upsert_key=None):
    cols = FIELDS[entity]
    table = f"landing_{entity}"
    rows = [[v.get(c) for c in cols] + [load_id, source, i + 1, seq] for i, v in valid]
    if upsert_key and rows:
        wh.execute(f"CREATE TEMP TABLE IF NOT EXISTS _batch_{entity} AS SELECT * FROM {table} WHERE 1 = 0")
        wh.execute(f"DELETE FROM _batch_{entity}")
        wh.bulk_insert(f"_batch_{entity}", cols + ["_load_id", "_source", "_source_row", "_load_seq"], rows)
        wh.execute(f"DELETE FROM {table} WHERE {upsert_key} IN (SELECT {upsert_key} FROM _batch_{entity})")
        wh.execute(f"INSERT INTO {table} SELECT * FROM _batch_{entity}")
    else:
        wh.bulk_insert(table, cols + ["_load_id", "_source", "_source_row", "_load_seq"], rows)
    existing = {r[0] for r in wh.query("SELECT raw_sha FROM _quarantine WHERE entity = ?", (entity,))}
    q_rows, seen = [], set()
    for b in bad:
        raw = raw_rows[b["row_number"] - 1]
        sha = _row_sha(entity, raw)
        if sha in existing or sha in seen:
            continue
        seen.add(sha)
        q_rows.append([run_id, load_id, entity, source, b["row_number"], "; ".join(b["errors"]),
                       json.dumps(raw, default=str), sha])
    wh.bulk_insert("_quarantine", ["run_id", "load_id", "entity", "source", "row_number", "errors", "raw",
                                   "raw_sha"], q_rows)
    return len(rows), len(q_rows)


def _next_seq(wh):
    vals = [wh.scalar(f"SELECT max(_load_seq) FROM landing_{e}") for e in ("customers", "products", "orders")]
    return max([v for v in vals if v is not None] or [0]) + 1


def run(as_of: str, wh, work: Path | None = None, transport=None, sleep=time.sleep, mappings_root=None) -> dict:
    work = work or workdir()
    day = date.fromisoformat(as_of)
    as_of_end = datetime.combine(day, datetime.max.time()).replace(microsecond=0)
    n = (wh.scalar("SELECT count(*) FROM _runs WHERE as_of = ?", (day,)) or 0) + 1
    run_id = f"{as_of}-r{n:02d}"
    run_dir = work / "runs" / run_id
    audit = Audit(wh, run_id, run_dir / "run_log.jsonl")
    started = datetime.now(timezone.utc).replace(tzinfo=None)
    wh.execute("INSERT INTO _runs VALUES (?, ?, ?, NULL, 'running', NULL, NULL)", (run_id, day, started))
    summary = {"run_id": run_id, "as_of": as_of, "loads": [], "warehouse": wh.kind}
    try:
        docs = {}
        for entity in CANONICAL:
            doc = review.latest(SOURCE, entity, mappings_root)
            if not doc:
                raise PipelineError(f"no approved mapping for {SOURCE}/{entity}: run `map` then `review` first")
            docs[entity] = doc
        summary["mapping_versions"] = {e: d["version"] for e, d in docs.items()}
        audit("run_started", as_of=as_of, mapping_versions=summary["mapping_versions"], warehouse=wh.kind)

        drops = sorted(p for p in (work / "data" / SOURCE / "drops").glob("*") if p.is_dir() and p.name <= as_of)
        for drop in drops:
            for entity in CSV_ENTITIES:
                path = drop / f"{entity}.csv"
                if not path.exists():
                    continue
                sha = _sha(path)
                rel = str(path.relative_to(work))
                if wh.scalar("SELECT count(*) FROM _file_registry WHERE sha256 = ?", (sha,)):
                    audit("file_skipped", entity, path=rel, reason="already loaded (same sha256)")
                    summary["loads"].append({"entity": entity, "source": rel, "skipped": True})
                    continue
                with path.open(newline="") as f:
                    reader = csv.DictReader(f)
                    raw = list(reader)
                    header = reader.fieldnames or []
                _check_drift(entity, header, docs[entity], audit)
                valid, bad = validate_rows(entity, _apply_mapping(raw, review.mapping_dict(docs[entity])))
                good_idx = _valid_with_index(valid, bad, len(raw))
                load_id = f"{run_id}:{entity}:{drop.name}"
                with wh.transaction():
                    loaded, quarantined = _land(wh, entity, good_idx, bad, raw, load_id, rel, run_id, _next_seq(wh))
                    wh.execute("INSERT INTO _file_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               (sha, rel, entity, load_id, run_id, len(raw), loaded, quarantined))
                audit("file_loaded", entity, path=rel, rows_in=len(raw), rows_loaded=loaded,
                      rows_quarantined=len(bad), new_quarantine_rows=quarantined)
                summary["loads"].append({"entity": entity, "source": rel, "rows_in": len(raw), "loaded": loaded,
                                         "quarantined": len(bad)})

        # orders from the REST API, incremental by watermark
        wm_key = f"{SOURCE}:api:/v2/orders"
        since = wh.scalar("SELECT value FROM _watermarks WHERE source_key = ?", (wm_key,))
        if transport is None:
            mock = api_mod.MockOrdersAPI(work / "data" / SOURCE / "api" / "order_events.json",
                                         as_of_end.replace(tzinfo=timezone.utc))
            transport = api_mod.InProcessTransport(mock)
        client = api_mod.PaginatedClient(transport, sleep=sleep)
        records = client.fetch_all(updated_since=since)
        st = client.stats
        audit("api_fetched", "orders", updated_since=since, records=st.records, pages=st.pages,
              requests=st.requests, retries=st.retries,
              statuses=st.statuses)
        _check_drift("orders", list(dict.fromkeys(k for r in records for k in r)) if records else
                     docs["orders"]["source_columns"], docs["orders"], audit)
        valid, bad = validate_rows("orders", _apply_mapping(records, review.mapping_dict(docs["orders"])))
        good_idx = _valid_with_index(valid, bad, len(records))
        load_id = f"{run_id}:orders:api"
        with wh.transaction():
            loaded, quarantined = _land(wh, "orders", good_idx, bad, records, load_id, "api:/v2/orders", run_id,
                                        _next_seq(wh), upsert_key="order_id")
            if records:
                new_wm = max(r[docs_key(docs['orders'], 'updated_at')] for r in records)
                wh.execute("DELETE FROM _watermarks WHERE source_key = ?", (wm_key,))
                wh.execute("INSERT INTO _watermarks VALUES (?, ?, ?)", (wm_key, new_wm, run_id))
        audit("api_loaded", "orders", rows_in=len(records), rows_upserted=loaded, rows_quarantined=len(bad),
              new_quarantine_rows=quarantined,
              watermark=wh.scalar("SELECT value FROM _watermarks WHERE source_key = ?", (wm_key,)))
        summary["loads"].append({"entity": "orders", "source": "api:/v2/orders", "updated_since": since,
                                 "rows_in": len(records), "loaded": loaded, "quarantined": len(bad),
                                 "api_requests": st.requests, "api_retries": st.retries, "pages": st.pages})

        built = dbt_lite.run_models(wh)
        audit("models_built", models={b["model"]: b["rows"] for b in built})
        summary["models"] = {b["model"]: b["rows"] for b in built}

        results = dq.run_checks(wh, as_of_end)
        wh.bulk_insert("_dq_results", ["run_id", "check_name", "model", "severity", "status", "failing_rows",
                                       "detail"],
                       [[run_id, r["check"], r["model"], r["severity"], r["status"], r["failing_rows"], r["detail"]]
                        for r in results])
        summary["dq"] = dq.summarize(results)
        summary["dq_warnings"] = [f"{r['check']} ({r['failing_rows']})" for r in results if r["status"] == "warn"]
        summary["dq_failures"] = [f"{r['check']} ({r['failing_rows']})" for r in results if r["status"] == "fail"]
        report = dq.write_report(results, summary, run_dir)
        summary["dq_report"] = str(report.relative_to(work))
        summary["quarantine_total"] = wh.scalar("SELECT count(*) FROM _quarantine")
        status = "failed_dq" if summary["dq"]["fail"] else "success"
        audit("dq_checked", **summary["dq"], report=summary["dq_report"])
    except Exception as e:
        status = "error"
        summary["error"] = f"{type(e).__name__}: {e}"
        audit("run_failed", error=summary["error"])
    summary["status"] = status
    audit("run_finished", status=status)
    wh.execute("UPDATE _runs SET finished_at = ?, status = ?, mapping_versions = ?, summary = ? WHERE run_id = ?",
               (datetime.now(timezone.utc).replace(tzinfo=None), status,
                json.dumps(summary.get("mapping_versions")), json.dumps(summary, default=str), run_id))
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    return summary


def docs_key(doc, target):
    return next(c["source_column"] for c in doc["columns"] if c["target"] == target)


def _valid_with_index(valid, bad, n):
    bad_rows = {b["row_number"] for b in bad}
    idx = [i for i in range(n) if i + 1 not in bad_rows]
    return list(zip(idx, valid))
