"""Evaluate the mapping step against the hand-labelled gold mappings in sources/*.yaml (synthetic data).

precision = correct proposed mappings / proposed mappings
recall    = correct proposed mappings / gold (non-null) mappings
Decoy columns (gold null) count against precision when the mapper maps them anyway.
Thresholded rows show what an auto-accept policy at that confidence would give (the rest goes to a human)."""
from __future__ import annotations

import json
from pathlib import Path

from . import ROOT, synth
from .mapping import get_mapper, propose_source

THRESHOLDS = (0.6, 0.8, 0.9)
EVAL_DIR = ROOT / "eval"


def _metrics(items, threshold=0.0):
    proposed = [i for i in items if i["target"] and i["confidence"] >= threshold]
    correct = [i for i in proposed if i["target"] == i["gold"]]
    gold = [i for i in items if i["gold"]]
    decoys = [i for i in items if not i["gold"]]
    return {"columns": len(items), "gold_mappings": len(gold), "proposed": len(proposed), "correct": len(correct),
            "precision": round(len(correct) / len(proposed), 3) if proposed else None,
            "recall": round(len(correct) / len(gold), 3) if gold else None,
            "decoys": len(decoys), "decoys_left_unmapped": sum(1 for i in decoys if not i["target"]
                                                               or i["confidence"] < threshold)}


def run_eval(mapper=None) -> dict:
    mapper = mapper or get_mapper()
    items = []
    for src_path in sorted(synth.SOURCES_DIR.glob("*.yaml")):
        src = synth.load_source(src_path.stem)
        cols = {e: synth.eval_columns(src, e) for e in src["entities"]}
        props = propose_source(mapper, cols)
        for entity, spec in src["entities"].items():
            gold = {c["column"]: c.get("gold") for c in spec["columns"]}
            for p in props[entity]:
                items.append({"source": src["name"], "split": src["split"], "entity": entity,
                              "column": p.source_column, "gold": gold[p.source_column], "target": p.target,
                              "confidence": p.confidence})
    out = {"label": "SYNTHETIC DATA: hand-labelled gold mappings for fictional sources; not a client benchmark",
           "mapper": mapper.name, "splits": {}, "sources": {}, "errors": []}
    for split in ("dev", "heldout"):
        sub = [i for i in items if i["split"] == split]
        out["splits"][split] = {"all": _metrics(sub), **{f">={t}": _metrics(sub, t) for t in THRESHOLDS}}
    for name in sorted({i["source"] for i in items}):
        sub = [i for i in items if i["source"] == name]
        out["sources"][name] = {"split": sub[0]["split"], **_metrics(sub)}
    out["errors"] = [i for i in items if i["target"] != i["gold"]]
    out["items"] = items
    return out


def render_md(r: dict) -> str:
    def pct(x):
        return "n/a" if x is None else f"{x:.1%}"
    lines = ["# Mapping eval results (synthetic data)", "",
             f"> {r['label']}. Mapper: `{r['mapper']}`. Regenerate with `python -m cdop eval-mapping --write`.", "",
             "| split | policy | columns | gold mappings | proposed | correct | precision | recall | decoys left unmapped |",
             "|---|---|---|---|---|---|---|---|---|"]
    for split, rows in r["splits"].items():
        for policy, m in rows.items():
            lines.append(f"| {split} | {'all proposals' if policy == 'all' else 'auto-accept conf ' + policy} | "
                         f"{m['columns']} | {m['gold_mappings']} | {m['proposed']} | {m['correct']} | "
                         f"{pct(m['precision'])} | {pct(m['recall'])} | {m['decoys_left_unmapped']}/{m['decoys']} |")
    lines += ["", "## Per source (all proposals)", "", "| source | split | columns | precision | recall | decoys left unmapped |",
              "|---|---|---|---|---|---|"]
    for name, m in r["sources"].items():
        lines.append(f"| {name} | {m['split']} | {m['columns']} | {pct(m['precision'])} | {pct(m['recall'])} | "
                     f"{m['decoys_left_unmapped']}/{m['decoys']} |")
    lines += ["", "## Every disagreement with the gold mapping", ""]
    if not r["errors"]:
        lines.append("None.")
    else:
        lines += ["| source | entity | column | gold | proposed | confidence |", "|---|---|---|---|---|---|"]
        lines += [f"| {e['source']} | {e['entity']} | `{e['column']}` | {e['gold'] or '(unmapped)'} | "
                  f"{e['target'] or '(unmapped)'} | {e['confidence']:.2f} |" for e in r["errors"]]
    return "\n".join(lines) + "\n"


def write(r: dict, out_dir: Path = EVAL_DIR):
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps({k: v for k, v in r.items() if k != "items"}, indent=2) + "\n")
    (out_dir / "RESULTS.md").write_text(render_md(r))


def check(r: dict, out_dir: Path = EVAL_DIR) -> bool:
    """True if committed results match a fresh run (reproducibility gate)."""
    committed = json.loads((out_dir / "results.json").read_text())
    fresh = json.loads(json.dumps({k: v for k, v in r.items() if k != "items"}))
    return committed == fresh
