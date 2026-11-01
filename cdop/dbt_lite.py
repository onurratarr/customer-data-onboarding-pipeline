"""A tiny dbt-style runner: {{ ref() }} / {{ source() }} resolution, DAG ordering, materialisation by layer and
a lineage diagram. It covers only what this project needs; the SQL files use dbt syntax and the dialect shared by
DuckDB and Postgres, so they run on either adapter."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from graphlib import TopologicalSorter
from pathlib import Path

from . import ROOT

REF = re.compile(r"\{\{\s*ref\(\s*['\"](\w+)['\"]\s*\)\s*\}\}")
SOURCE = re.compile(r"\{\{\s*source\(\s*['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"]\s*\)\s*\}\}")
MATERIALIZATION = {"staging": "view", "intermediate": "view", "marts": "table"}
MODELS_DIR = ROOT / "models"


@dataclass
class Model:
    name: str
    layer: str
    raw_sql: str
    refs: set = field(default_factory=set)
    sources: set = field(default_factory=set)

    @property
    def sql(self):
        s = REF.sub(lambda m: m.group(1), self.raw_sql)
        return SOURCE.sub(lambda m: f"{m.group(1)}_{m.group(2)}", s).strip().rstrip(";")

    @property
    def materialized(self):
        return MATERIALIZATION.get(self.layer, "view")


def discover(models_dir: Path = MODELS_DIR) -> dict[str, Model]:
    models = {}
    for p in sorted(models_dir.glob("*/*.sql")):
        raw = p.read_text()
        models[p.stem] = Model(p.stem, p.parent.name, raw, set(REF.findall(raw)),
                               {f"{a}_{b}" for a, b in SOURCE.findall(raw)})
    for m in models.values():
        missing = m.refs - set(models)
        if missing:
            raise ValueError(f"{m.name} refs unknown model(s): {sorted(missing)}")
    return models


def order(models: dict[str, Model]) -> list[str]:
    return list(TopologicalSorter({n: sorted(m.refs) for n, m in sorted(models.items())}).static_order())


def run_models(wh, models: dict[str, Model] | None = None) -> list[dict]:
    models = models or discover()
    seq = order(models)
    existing = wh.tables()
    for name in reversed(seq):
        if name in existing:
            wh.execute(f"DROP {models[name].materialized.upper()} IF EXISTS {name} CASCADE")
    out = []
    for name in seq:
        m = models[name]
        wh.execute(f"CREATE {m.materialized.upper()} {name} AS {m.sql}")
        out.append({"model": name, "layer": m.layer, "materialized": m.materialized,
                    "rows": wh.scalar(f"SELECT count(*) FROM {name}")})
    return out


def lineage_mermaid(models: dict[str, Model] | None = None) -> str:
    models = models or discover()
    lines = ["flowchart LR"]
    for layer in ("landing", "staging", "intermediate", "marts"):
        names = sorted({s for m in models.values() for s in m.sources}) if layer == "landing" else \
            [n for n, m in models.items() if m.layer == layer]
        lines.append(f"    subgraph {layer}")
        lines += [f"        {n}" for n in names]
        lines.append("    end")
    for n, m in models.items():
        lines += [f"    {d} --> {n}" for d in sorted(m.sources | m.refs)]
    return "\n".join(lines)
