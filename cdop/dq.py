"""Data quality checks from models/schema.yml: not_null, unique, accepted_values, relationships (referential
integrity), expression_is_true and source freshness, each with a severity. Writes a Markdown + JSON report."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import yaml

from . import ROOT

SCHEMA = ROOT / "models" / "schema.yml"


def _test_sql(model, column, name, args):
    if name == "unique":
        return f"SELECT {column} FROM {model} WHERE {column} IS NOT NULL GROUP BY {column} HAVING count(*) > 1"
    if name == "not_null":
        return f"SELECT {column} FROM {model} WHERE {column} IS NULL"
    if name == "accepted_values":
        vals = ", ".join("'" + str(v).replace("'", "''") + "'" for v in args["values"])
        return f"SELECT {column} FROM {model} WHERE {column} IS NOT NULL AND {column} NOT IN ({vals})"
    if name == "relationships":
        return (f"SELECT c.{column} FROM {model} c LEFT JOIN {args['to']} p ON c.{column} = p.{args['field']} "
                f"WHERE c.{column} IS NOT NULL AND p.{args['field']} IS NULL")
    if name == "expression_is_true":
        return f"SELECT {column} FROM {model} WHERE NOT ({args['expression']})"
    raise ValueError(f"unknown test {name}")


def run_checks(wh, as_of_end: datetime, schema_path: Path = SCHEMA) -> list[dict]:
    schema = yaml.safe_load(schema_path.read_text())
    results = []
    for src in schema.get("sources", []):
        for t in src["tables"]:
            f = t.get("freshness")
            if not f:
                continue
            table = f"{src['name']}_{t['name']}"
            latest = wh.scalar(f"SELECT max({f['loaded_at_field']}) FROM {table}")
            lag = None if latest is None else (as_of_end - latest).total_seconds() / 3600
            status = "fail" if lag is None or lag > f["error_after_hours"] else \
                "warn" if lag > f["warn_after_hours"] else "pass"
            results.append({"check": f"freshness:{table}.{f['loaded_at_field']}", "model": table,
                            "severity": "error", "status": status, "failing_rows": 0 if status == "pass" else 1,
                            "detail": f"latest {latest}, lag {lag:.1f} h (warn > {f['warn_after_hours']} h, "
                                      f"error > {f['error_after_hours']} h)" if lag is not None else "no rows"})
    for m in schema["models"]:
        for col in m.get("columns", []):
            for t in col.get("tests", []):
                name, args = (t, {}) if isinstance(t, str) else next(iter(t.items()))
                args = args or {}
                severity = args.get("severity", "error")
                sql = _test_sql(m["name"], col["name"], name, args)
                n = wh.scalar(f"SELECT count(*) FROM ({sql}) x")
                sample = [str(r[0]) for r in wh.query(f"SELECT * FROM ({sql}) x LIMIT 3")] if n else []
                results.append({"check": f"{name}:{m['name']}.{col['name']}", "model": m["name"],
                                "severity": severity, "failing_rows": n,
                                "status": "pass" if n == 0 else ("warn" if severity == "warn" else "fail"),
                                "detail": ("examples: " + ", ".join(sample)) if sample else ""})
    return results


def summarize(results):
    return {s: sum(1 for r in results if r["status"] == s) for s in ("pass", "warn", "fail")}


def write_report(results, run: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "dq_results.json").write_text(json.dumps(results, indent=2, default=str))
    s = summarize(results)
    icon = {"pass": "✅", "warn": "⚠️", "fail": "❌"}
    lines = [f"# Data quality report: run `{run['run_id']}` (as of {run['as_of']})", "",
             f"**{s['pass']} passed · {s['warn']} warnings · {s['fail']} failures** "
             f"(synthetic data)", "", "| check | severity | status | failing rows | detail |", "|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| `{r['check']}` | {r['severity']} | {icon[r['status']]} {r['status']} | "
                     f"{r['failing_rows']} | {r['detail']} |")
    p = out_dir / "dq_report.md"
    p.write_text("\n".join(lines) + "\n")
    return p
