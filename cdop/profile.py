"""Schema inference: what kind of values does each source column hold? The scores feed the mapper as evidence and
the CLI shows them to the reviewer."""
from __future__ import annotations

import re
from datetime import datetime

COUNTRY_TOKENS = {"us", "usa", "united states", "ca", "can", "canada", "gb", "gbr", "uk", "united kingdom", "de",
                  "deu", "germany", "fr", "fra", "france", "nl", "nld", "netherlands", "se", "swe", "sweden", "no",
                  "nor", "norway", "es", "esp", "spain", "it", "ita", "italy", "dk", "dnk", "denmark", "fi", "fin",
                  "finland", "ie", "irl", "ireland", "au", "aus", "australia", "tr", "tur", "turkey", "ch", "che",
                  "switzerland", "at", "aut", "austria", "be", "bel", "belgium", "pl", "pol", "poland"}
CURRENCIES = {"usd", "eur", "gbp", "sek", "nok", "dkk", "chf", "cad", "aud", "jpy", "try"}
BOOL_TOKENS = {"y", "n", "yes", "no", "true", "false", "t", "f", "1", "0", "x", "active", "inactive"}
DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%d.%m.%Y", "%d %b %Y", "%b %d %Y", "%Y/%m/%d", "%d-%b-%Y"]
TS_FORMATS = ["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
              "%Y%m%d%H%M%S", "%m/%d/%Y %H:%M"]
RX = {
    "email": re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$"),
    "integer": re.compile(r"^-?\d+$"),
    "decimal": re.compile(r"^[$€£]?\s?-?\d{1,3}(,\d{3})*(\.\d+)?$|^[$€£]?\s?-?\d+\.\d+$"),
    "phone": re.compile(r"^\+?[\d\s\-().]{7,20}$"),
    "ip": re.compile(r"^\d{1,3}(\.\d{1,3}){3}$"),
    "name_like": re.compile(r"^[A-Z][a-z'\-]+(\s[A-Z][A-Za-z'\-]+){1,3}$"),
}


def _parses(v, fmts):
    for f in fmts:
        try:
            datetime.strptime(v, f)
            return True
        except ValueError:
            pass
    return False


def _shape(v: str) -> str:
    return re.sub(r"[a-z]", "a", re.sub(r"[A-Z]", "A", re.sub(r"\d", "9", v)))


def profile_column(name: str, values: list) -> dict:
    vals = [str(v).strip() for v in values if v is not None and str(v).strip() != ""]
    n = len(values)
    out = {"column": name, "rows": n, "null_rate": round(1 - len(vals) / n, 3) if n else 1.0,
           "distinct": len(set(vals)), "distinct_ratio": round(len(set(vals)) / len(vals), 3) if vals else 0.0,
           "samples": list(dict.fromkeys(vals))[:4], "scores": {}}
    if not vals:
        out["inferred_type"] = "empty"
        return out
    frac = lambda pred: round(sum(1 for v in vals if pred(v)) / len(vals), 3)
    low = [v.lower() for v in vals]
    s = {
        "email": frac(lambda v: bool(RX["email"].match(v))),
        "integer": frac(lambda v: bool(RX["integer"].match(v))),
        "decimal": frac(lambda v: bool(RX["decimal"].match(v)) and not RX["integer"].match(v)),
        "phone": frac(lambda v: bool(RX["phone"].match(v)) and sum(c.isdigit() for c in v) >= 7
                      and not RX["integer"].match(v) and "." not in v and not _parses(v, DATE_FORMATS)),
        "ip": frac(lambda v: bool(RX["ip"].match(v))),
        "date": frac(lambda v: _parses(v, DATE_FORMATS)),
        "timestamp": frac(lambda v: _parses(v, TS_FORMATS)),
        "country": round(sum(1 for v in low if v in COUNTRY_TOKENS) / len(vals), 3),
        "currency": round(sum(1 for v in low if v in CURRENCIES) / len(vals), 3),
        "name_like": frac(lambda v: bool(RX["name_like"].match(v))),
    }
    distinct_low = set(low) | ({""} if out["null_rate"] > 0 else set())
    s["boolean"] = 1.0 if distinct_low <= BOOL_TOKENS | {""} and len(distinct_low) <= 3 else 0.0
    shapes = [(len(v), any(c.isalpha() for c in v), any(c.isdigit() for c in v), " " in v) for v in vals]
    top_shape = max(set(shapes), key=shapes.count)
    shape_share = shapes.count(top_shape) / len(shapes)
    has_digit = sum(1 for v in vals if any(c.isdigit() for c in v)) / len(vals)
    s["id_like"] = round(shape_share * has_digit, 3) if (out["distinct_ratio"] >= 0.5 or out["distinct"] >= 15) and not top_shape[3] \
        and s["decimal"] < 0.5 and s["ip"] < 0.5 and s["phone"] < 0.5 and s["timestamp"] < 0.5 and s["date"] < 0.5 else 0.0
    s["category"] = 1.0 if out["distinct"] <= 12 and (out["distinct_ratio"] <= 0.3 or len(vals) < 20) \
        and s["boolean"] == 0 else 0.0
    s["text"] = frac(lambda v: any(c.isalpha() for c in v) and " " in v)
    out["scores"] = {k: v for k, v in s.items() if v > 0}
    out["shape"] = _shape(vals[shapes.index(top_shape)]) if shape_share >= 0.6 else "mixed"
    out["inferred_type"] = max(out["scores"], key=lambda k: (out["scores"][k], _PRIORITY.index(k))) \
        if out["scores"] else "string"
    return out


_PRIORITY = ["text", "name_like", "category", "integer", "id_like", "decimal", "phone", "date", "timestamp",
             "currency", "country", "ip", "boolean", "email"]


def profile(columns: dict[str, list]) -> list[dict]:
    return [profile_column(c, v) for c, v in columns.items()]
