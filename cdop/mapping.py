"""Column mapping proposals: source columns -> canonical fields, each with a confidence score and reasons.

MockMapper (default, offline, deterministic) combines header similarity, value-type evidence from profiling,
accepted-value overlap and foreign-key overlap with already-mapped entities. OpenAICompatibleMapper asks a real
model the same question (MAPPER_BACKEND=openai). Either way a human reviews every proposal before
anything is loaded (see review.py)."""
from __future__ import annotations

import difflib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from . import ROOT
from .contracts import SEGMENT_VALUES, STATUS_VALUES
from .lexicon import ABBREVIATIONS, STOPWORDS, SYNONYMS, TYPE_EVIDENCE
from .profile import profile

CANONICAL = yaml.safe_load((ROOT / "cdop" / "canonical.yaml").read_text())["entities"]
MIN_CONFIDENCE = 0.40
VALUE_VOCAB = {"segment": SEGMENT_VALUES, "status": STATUS_VALUES}
FK_FIELDS = {("orders", "customer_id"): "customer_id", ("orders", "sku"): "sku"}


@dataclass
class Proposal:
    source_column: str
    target: str | None
    confidence: float
    reasons: list[str] = field(default_factory=list)
    alternatives: list[list] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def tokens(header: str) -> list[str]:
    h = header.replace("#", " # ").replace("E-mail", "email").replace("e-mail", "email")
    h = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", h)
    raw = [t for t in re.split(r"[^A-Za-z0-9#]+", h.lower()) if t]
    out = [ABBREVIATIONS.get(t, t) for t in raw]
    return [t for t in out if t not in STOPWORDS] or out


def name_score(header: str, synonyms: list[str]) -> tuple[float, str]:
    h = tokens(header)
    hs = set(h)
    best, why = 0.0, ""
    for syn in synonyms:
        s = syn.split()
        if h == s:
            return 1.0, f"header matches '{syn}'"
        overlap = len(hs & set(s))
        score = 0.9 * 2 * overlap / (len(hs) + len(s)) if overlap else 0.0
        fuzzy = 0.75 * difflib.SequenceMatcher(None, " ".join(h), syn).ratio()
        sc = max(score, fuzzy if fuzzy > 0.55 else 0.0)
        if sc > best:
            best, why = sc, f"header ~ '{syn}'"
    return round(best, 3), why


def type_score(prof: dict, ftype: str) -> tuple[float, str]:
    best, why = 0.0, ""
    for kind, w in TYPE_EVIDENCE[ftype].items():
        sc = prof["scores"].get(kind, 0.0) * w
        if sc > best:
            best, why = sc, f"values look like {kind} ({prof['scores'][kind]:.0%})"
    return round(best, 3), why


def value_overlap(values: list, vocab: dict) -> float:
    vals = [str(v).strip().lower() for v in values if v not in (None, "")]
    if not vals:
        return 0.0
    return sum(1 for v in vals if v in vocab or v.replace("_", " ") in vocab) / len(vals)


class MockMapper:
    name = "mock-lexicon-v1"

    def score(self, entity, col, prof, values, known_keys):
        out = {}
        for fname, spec in CANONICAL[entity]["fields"].items():
            ns, nwhy = name_score(col, SYNONYMS[entity][fname])
            ts, twhy = type_score(prof, spec["type"])
            reasons = [r for r in (nwhy, twhy) if r]
            extra = None
            if fname in VALUE_VOCAB:
                extra = value_overlap(values, VALUE_VOCAB[fname])
                reasons.append(f"{extra:.0%} of values are known {fname} values")
            fk = FK_FIELDS.get((entity, fname))
            if fk and known_keys.get(fk):
                vals = {str(v).strip() for v in values if v not in (None, "")}
                extra = len(vals & known_keys[fk]) / len(vals) if vals else 0.0
                reasons.append(f"{extra:.0%} of values match known {fk} keys")
            conf = 0.55 * ns + 0.45 * ts if extra is None else 0.45 * ns + 0.30 * ts + 0.25 * extra
            if ts < 0.3:
                conf *= 0.4
                reasons.append("value type does not fit")
            out[fname] = (round(conf, 3), reasons)
        return out

    def propose(self, entity: str, columns: dict[str, list], known_keys: dict | None = None) -> list[Proposal]:
        known_keys = known_keys or {}
        profs = {p["column"]: p for p in profile(columns)}
        scores = {c: self.score(entity, c, profs[c], columns[c], known_keys) for c in columns}
        return assign(scores)


def assign(scores: dict[str, dict[str, tuple[float, list]]]) -> list[Proposal]:
    """Greedy one-to-one assignment by confidence; below MIN_CONFIDENCE a column stays unmapped."""
    pairs = sorted(((s[0], c, f) for c, fs in scores.items() for f, s in fs.items()), key=lambda x: (-x[0], x[1], x[2]))
    taken_c, taken_f, chosen = set(), set(), {}
    for conf, c, f in pairs:
        if conf < MIN_CONFIDENCE:
            break
        if c in taken_c or f in taken_f:
            continue
        chosen[c] = f
        taken_c.add(c)
        taken_f.add(f)
    out = []
    for c, fs in scores.items():
        ranked = sorted(fs.items(), key=lambda kv: -kv[1][0])
        alts = [[f, s[0]] for f, s in ranked[:3] if f != chosen.get(c)][:2]
        if c in chosen:
            conf, reasons = fs[chosen[c]]
            out.append(Proposal(c, chosen[c], conf, reasons, alts))
        else:
            out.append(Proposal(c, None, round(1 - ranked[0][1][0], 3) if ranked else 1.0,
                                ["no canonical field scored above threshold or best field already taken"], alts))
    return out


class OpenAICompatibleMapper:
    """Optional real model via any OpenAI-compatible /chat/completions endpoint. Same output contract."""

    def __init__(self, base_url=None, model=None, api_key=None, post=None):
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.name = f"openai-compatible:{self.model}"
        self._post = post

    def _call(self, payload):
        if self._post:
            return self._post(f"{self.base_url}/chat/completions", payload)
        import httpx  # optional dependency, only needed for this backend
        r = httpx.post(f"{self.base_url}/chat/completions", json=payload, timeout=60,
                       headers={"Authorization": f"Bearer {self.api_key}"})
        r.raise_for_status()
        return r.json()

    def propose(self, entity, columns, known_keys=None):
        profs = profile(columns)
        fields = {f: {"type": s["type"], "description": s["description"], **({"accepted": s["accepted"]}
                  if "accepted" in s else {})} for f, s in CANONICAL[entity]["fields"].items()}
        prompt = ("Map source columns to canonical fields. Use each canonical field at most once; columns that "
                  "match nothing get target null. Reply with JSON only: {\"mappings\": [{\"source_column\": ..., "
                  "\"target\": ... or null, \"confidence\": 0..1, \"reason\": ...}]}\n\n"
                  f"Canonical fields for {entity}:\n{json.dumps(fields, indent=1)}\n\nSource columns:\n"
                  + json.dumps([{"column": p["column"], "inferred_type": p["inferred_type"],
                                 "samples": p["samples"], "null_rate": p["null_rate"]} for p in profs], indent=1))
        resp = self._call({"model": self.model, "temperature": 0, "response_format": {"type": "json_object"},
                           "messages": [{"role": "system", "content": "You are a careful data engineer."},
                                        {"role": "user", "content": prompt}]})
        content = resp["choices"][0]["message"]["content"]
        items = json.loads(content[content.find("{"): content.rfind("}") + 1])["mappings"]
        valid = set(CANONICAL[entity]["fields"])
        scores = {c: {} for c in columns}
        for it in items:
            c, t = it.get("source_column"), it.get("target")
            if c in scores and t in valid:
                scores[c][t] = (round(float(it.get("confidence", 0.5)), 3), [f"model: {it.get('reason', '')}"])
        for c in scores:
            scores[c] = scores[c] or {}
        out = assign({c: fs for c, fs in scores.items()}) if any(scores.values()) else []
        seen = {p.source_column for p in out}
        out += [Proposal(c, None, 1.0, ["model proposed no target"]) for c in columns if c not in seen]
        return sorted(out, key=lambda p: list(columns).index(p.source_column))


def get_mapper():
    return OpenAICompatibleMapper() if os.environ.get("MAPPER_BACKEND", "mock") == "openai" else MockMapper()


def propose_source(mapper, entity_columns: dict[str, dict[str, list]]) -> dict[str, list[Proposal]]:
    """Propose for customers and products first, then use their proposed key columns as FK evidence for orders."""
    out, known = {}, {}
    for entity in ("customers", "products", "orders"):
        if entity not in entity_columns:
            continue
        cols = entity_columns[entity]
        out[entity] = mapper.propose(entity, cols, known)
        key = CANONICAL[entity]["key"]
        for p in out[entity]:
            if p.target == key and entity != "orders":
                known[key] = {str(v).strip() for v in cols[p.source_column] if v not in (None, "")}
    return out
