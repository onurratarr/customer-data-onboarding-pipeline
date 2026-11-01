"""Command line: python -m cdop <command>. Run `python -m cdop -h` for the list."""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import api as api_mod
from . import dbt_lite, evaluate, pipeline, review, synth
from .mapping import Proposal, get_mapper, propose_source
from .paths import workdir
from .profile import profile
from .warehouse import connect

SRC = pipeline.SOURCE


def _work(a) -> Path:
    return Path(a.workdir) if a.workdir else workdir()


def _api(work: Path, as_of: str):
    if os.environ.get("SOURCE_API_URL"):
        return api_mod.UrllibTransport(os.environ["SOURCE_API_URL"])
    day = datetime.fromisoformat(as_of).replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
    return api_mod.InProcessTransport(api_mod.MockOrdersAPI(work / "data" / SRC / "api" / "order_events.json", day))


def sample_columns(work: Path, as_of: str = synth.DEMO_AS_OF[0]) -> dict[str, dict[str, list]]:
    """What a forward-deployed engineer gets on day one: the first CSV drop plus a page or two from the API."""
    out = {}
    for entity in pipeline.CSV_ENTITIES:
        with (work / "data" / SRC / "drops" / as_of / f"{entity}.csv").open(newline="") as f:
            out[entity] = synth.columns_of(list(csv.DictReader(f)))
    client = api_mod.PaginatedClient(_api(work, as_of))
    out["orders"] = synth.columns_of(client.fetch_all()[:200])
    return out


def cmd_generate(a):
    work = _work(a)
    stats = synth.write_demo(work / "data")
    print(f"wrote synthetic demo data for {synth.load_source(SRC)['company']} to {work / 'data'}")
    print(json.dumps(stats))


def cmd_profile(a):
    cols = sample_columns(_work(a))
    for entity, c in cols.items():
        if a.entity and entity != a.entity:
            continue
        print(f"\n== {entity} ({len(next(iter(c.values())))} sample rows)")
        for p in profile(c):
            print(f"  {p['column']:<18} {p['inferred_type']:<10} nulls {p['null_rate']:.0%}  "
                  f"distinct {p['distinct']:<5} e.g. {p['samples'][:3]}")


def cmd_map(a):
    work = _work(a)
    mapper = get_mapper()
    props = propose_source(mapper, sample_columns(work))
    out = work / "proposals" / SRC
    out.mkdir(parents=True, exist_ok=True)
    for entity, ps in props.items():
        (out / f"{entity}.json").write_text(json.dumps({"mapper": mapper.name, "columns_seen": [p.source_column for p in ps],
                                                        "proposals": [p.to_dict() for p in ps]}, indent=2))
        print(f"\n== {entity}  (mapper: {mapper.name})")
        for p in ps:
            print(f"  {p.source_column:<18} -> {str(p.target):<16} {p.confidence:.2f}  {'; '.join(p.reasons[:2])}")
    print(f"\nproposals written to {out}; next: python -m cdop review")


def cmd_review(a):
    work = _work(a)
    decisions = yaml.safe_load(Path(a.decisions).read_text()) if a.decisions else {}
    entities = [a.entity] if a.entity else ["customers", "products", "orders"]
    for entity in entities:
        doc = json.loads((work / "proposals" / SRC / f"{entity}.json").read_text())
        props = [Proposal(**p) for p in doc["proposals"]]
        if a.decisions or a.accept_above is not None:
            decide = review.decide_scripted((decisions or {}).get(entity), a.accept_above)
        else:
            print(f"\n=== review {SRC}/{entity}")
            decide = review.decide_interactive()
        try:
            path = review.review(SRC, entity, props, decide, reviewer=a.reviewer, proposed_by=doc["mapper"])
        except review.ReviewError as e:
            print(f"{entity}: NOT approved: {e}", file=sys.stderr)
            return 2
        d = yaml.safe_load(path.read_text())
        print(f"{entity}: approved mapping v{d['version']:03d} -> {path}  {d['summary']}")
    return 0


def _print_summary(s):
    print(f"\nrun {s['run_id']}  status={s['status']}  warehouse={s['warehouse']}  mappings={s.get('mapping_versions')}")
    for l in s["loads"]:
        if l.get("skipped"):
            print(f"  {l['entity']:<9} {l['source']:<45} skipped (already loaded)")
        else:
            extra = f"  since={l['updated_since']}  requests={l['api_requests']} retries={l['api_retries']}" \
                if "api_requests" in l else ""
            print(f"  {l['entity']:<9} {l['source']:<45} in={l['rows_in']:<5} loaded={l['loaded']:<5} "
                  f"quarantined={l['quarantined']}{extra}")
    if "models" in s:
        print("  models: " + ", ".join(f"{k}={v}" for k, v in s["models"].items()))
        print(f"  dq: {s['dq']}  report: {s['dq_report']}")
        for w in s["dq_warnings"]:
            print(f"    warn: {w}")
        for w in s["dq_failures"]:
            print(f"    FAIL: {w}")
    if "error" in s:
        print(f"  ERROR: {s['error']}")


def cmd_run(a):
    work = _work(a)
    wh = connect(work)
    try:
        s = pipeline.run(a.as_of, wh, work, transport=_api(work, a.as_of) if os.environ.get("SOURCE_API_URL") else None)
    finally:
        wh.close()
    _print_summary(s)
    return 0 if s["status"] == "success" else 1


def cmd_demo(a):
    work = _work(a)
    cmd_generate(a)
    rc = 0
    for as_of in [*synth.DEMO_AS_OF, synth.DEMO_AS_OF[-1]]:
        a.as_of = as_of
        rc = cmd_run(a)  # day 1 is expected to fail DQ (a quarantined product); the demo passes if the last run does
    wh = connect(work)
    try:
        print("\nmart_monthly_revenue (USD, all segments):")
        for r in wh.query("SELECT month, sum(orders), sum(revenue) FROM mart_monthly_revenue WHERE currency = 'USD' "
                          "GROUP BY month ORDER BY month"):
            print(f"  {r[0]}  orders={r[1]:<4} revenue={r[2]}")
    finally:
        wh.close()
    return rc


def cmd_eval(a):
    r = evaluate.run_eval()
    print(evaluate.render_md(r))
    if a.write:
        evaluate.write(r)
        print(f"wrote {evaluate.EVAL_DIR / 'RESULTS.md'}")
    if a.check:
        ok = evaluate.check(r)
        print("eval results reproduce the committed eval/results.json" if ok else
              "MISMATCH with committed eval/results.json; re-run with --write and commit")
        return 0 if ok else 1
    return 0


def cmd_serve(a):
    work = _work(a)
    day = datetime.fromisoformat(a.as_of).replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
    srv = api_mod.serve(api_mod.MockOrdersAPI(work / "data" / SRC / "api" / "order_events.json", day), port=a.port)
    print(f"mock orders API on http://127.0.0.1:{a.port}/v2/orders (as of {a.as_of}); Ctrl-C to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


def cmd_audit(a):
    wh = connect(_work(a))
    try:
        for r in wh.query("SELECT run_id, status, started_at, mapping_versions FROM _runs ORDER BY started_at"):
            print(f"{r[0]}  {r[1]:<9} {r[2]}  mappings={r[3]}")
        if a.run_id:
            for r in wh.query("SELECT seq, step, entity, detail FROM _audit_log WHERE run_id = ? ORDER BY seq",
                              (a.run_id,)):
                print(f"  {r[0]:>3} {r[1]:<14} {r[2] or '':<9} {r[3]}")
    finally:
        wh.close()


def cmd_lineage(a):
    print(dbt_lite.lineage_mermaid())


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cdop", description="Customer data onboarding pipeline (synthetic demo)")
    ap.add_argument("--workdir", help="working directory (default: $CDOP_WORKDIR or ./work)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("generate", help="write the synthetic demo source data").set_defaults(f=cmd_generate)
    p = sub.add_parser("profile", help="profile the source columns")
    p.add_argument("--entity")
    p.set_defaults(f=cmd_profile)
    sub.add_parser("map", help="propose column mappings (MAPPER_BACKEND=mock|openai)").set_defaults(f=cmd_map)
    p = sub.add_parser("review", help="accept / edit / reject proposals -> versioned mapping files")
    p.add_argument("--entity")
    p.add_argument("--decisions", help="YAML {entity: {source_column: target|null}} for scripted review")
    p.add_argument("--accept-above", type=float, help="auto-accept proposals with confidence >= this")
    p.add_argument("--reviewer")
    p.set_defaults(f=cmd_review)
    p = sub.add_parser("run", help="run the pipeline for one as-of date")
    p.add_argument("--as-of", required=True)
    p.set_defaults(f=cmd_run)
    sub.add_parser("demo", help="generate + run day 1 + run day 2 + re-run day 2").set_defaults(f=cmd_demo)
    p = sub.add_parser("eval-mapping", help="mapping precision/recall vs hand-labelled synthetic gold")
    p.add_argument("--write", action="store_true")
    p.add_argument("--check", action="store_true")
    p.set_defaults(f=cmd_eval)
    p = sub.add_parser("serve-api", help="serve the mock orders API over HTTP")
    p.add_argument("--as-of", default=synth.DEMO_AS_OF[-1])
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(f=cmd_serve)
    p = sub.add_parser("audit", help="list runs, or one run's audit trail")
    p.add_argument("--run-id")
    p.set_defaults(f=cmd_audit)
    sub.add_parser("lineage", help="print the model lineage as mermaid").set_defaults(f=cmd_lineage)
    a = ap.parse_args(argv)
    return a.f(a) or 0
