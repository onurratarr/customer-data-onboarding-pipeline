"""Human-in-the-loop review of mapping proposals, persisted as versioned YAML mapping files.

Nothing is loaded with an unreviewed mapping: the pipeline only reads files with status `approved`. Every decision
(accepted / edited / rejected) is recorded next to what the mapper proposed and how confident it was, so the
mapping file doubles as an audit trail and as labelled data for improving the mapper."""
from __future__ import annotations

import getpass
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .mapping import CANONICAL, Proposal
from .paths import mappings_dir


def columns_sha(cols: list[str]) -> str:
    return hashlib.sha256("\x1f".join(cols).encode()).hexdigest()[:16]


def versions(source: str, entity: str, root: Path | None = None) -> list[Path]:
    d = (root or mappings_dir()) / source
    return sorted(d.glob(f"{entity}.v*.yaml"), key=lambda p: int(re.search(r"\.v(\d+)\.yaml$", p.name).group(1)))


def latest(source: str, entity: str, root: Path | None = None) -> dict | None:
    for p in reversed(versions(source, entity, root)):
        doc = yaml.safe_load(p.read_text())
        if doc.get("status") == "approved":
            doc["_path"] = str(p)
            return doc
    return None


class ReviewError(ValueError):
    pass


def decide_scripted(decisions: dict | None, accept_above: float | None):
    """Non-interactive reviewer: explicit per-column decisions win; otherwise accept if confidence >= threshold;
    anything else is left undecided, which blocks approval (a human must look at it)."""
    decisions = decisions or {}

    def decide(p: Proposal):
        if p.source_column in decisions:
            d = decisions[p.source_column]
            if d is None or d == "reject":
                return ("reject", None, "scripted decision")
            if d == p.target:
                return ("accept", d, "scripted decision")
            return ("edit", d, "scripted decision")
        if accept_above is not None and p.confidence >= accept_above:
            return ("accept", p.target, f"auto-accepted (confidence >= {accept_above})")
        return None
    return decide


def decide_interactive(inp=input, out=print):
    fields = None

    def decide(p: Proposal):
        nonlocal fields
        out(f"\n  {p.source_column!r} -> {p.target or '(no mapping)'}   confidence {p.confidence:.2f}")
        for r in p.reasons:
            out(f"      - {r}")
        if p.alternatives:
            out("      alternatives: " + ", ".join(f"{f} ({c:.2f})" for f, c in p.alternatives))
        while True:
            a = inp("  [a]ccept / [e]dit / [r]eject (no mapping) > ").strip().lower() or "a"
            if a in ("a", "accept"):
                return ("accept", p.target, "")
            if a in ("r", "reject"):
                return ("reject", None, "")
            if a in ("e", "edit"):
                t = inp("    target field (blank = no mapping): ").strip() or None
                return ("edit", t, "")
    return decide


def review(source: str, entity: str, proposals: list[Proposal], decide, reviewer: str | None = None,
           proposed_by: str = "", root: Path | None = None, now: datetime | None = None) -> Path:
    valid = set(CANONICAL[entity]["fields"])
    cols, undecided = [], []
    for p in proposals:
        d = decide(p)
        if d is None:
            undecided.append(p.source_column)
            continue
        decision, target, note = d
        if target is not None and target not in valid:
            raise ReviewError(f"{p.source_column!r}: unknown canonical field {target!r} for {entity}")
        cols.append({"source_column": p.source_column, "target": target, "proposed_target": p.target,
                     "confidence": p.confidence, "decision": {"accept": "accepted", "edit": "edited", "reject": "rejected"}[decision],
                     **({"note": note} if note else {})})
    if undecided:
        raise ReviewError(f"{len(undecided)} proposal(s) need a human decision: {undecided}")
    targets = [c["target"] for c in cols if c["target"]]
    dupes = {t for t in targets if targets.count(t) > 1}
    if dupes:
        raise ReviewError(f"canonical field(s) mapped twice: {sorted(dupes)}")
    missing = [f for f, s in CANONICAL[entity]["fields"].items() if s.get("required") and f not in targets]
    if missing:
        raise ReviewError(f"required canonical field(s) not mapped: {missing}")
    prev = latest(source, entity, root)
    mapping = {c["source_column"]: c["target"] for c in cols}
    if prev and {c["source_column"]: c["target"] for c in prev["columns"]} == mapping:
        return Path(prev["_path"])
    n = len(versions(source, entity, root)) + 1
    src_cols = [p.source_column for p in proposals]
    doc = {"source": source, "entity": entity, "version": n, "status": "approved",
           "supersedes": prev["version"] if prev else None, "proposed_by": proposed_by,
           "reviewed_by": reviewer or getpass.getuser(),
           "reviewed_at": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "source_columns": src_cols, "source_columns_sha": columns_sha(src_cols),
           "summary": {k: sum(1 for c in cols if c["decision"] == k) for k in ("accepted", "edited", "rejected")},
           "columns": cols}
    path = (root or mappings_dir()) / source / f"{entity}.v{n:03d}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))
    return path


def mapping_dict(doc: dict) -> dict[str, str]:
    return {c["source_column"]: c["target"] for c in doc["columns"] if c["target"]}
