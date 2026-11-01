"""Synthetic data. One seeded canonical dataset is rendered into each source system's "dialect" (header names,
value formats, extra columns) as described in sources/*.yaml. The demo source additionally gets realistic mess:
bad rows, duplicates, orphan references, schema drift in the second drop. Everything here is invented."""
from __future__ import annotations

import csv
import json
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from . import ROOT

SOURCES_DIR = ROOT / "sources"
FIRST = ["Ava", "Liam", "Maja", "Noah", "Ines", "Jonas", "Clara", "Mateo", "Freya", "Omar", "Lea", "Hugo", "Nora",
         "Elias", "Sofia", "Arjun", "Mila", "Theo", "Zara", "Felix", "Ida", "Kai", "Lena", "Ravi", "Selin", "Tomas"]
LAST = ["Berg", "Novak", "Okafor", "Lindqvist", "Moreau", "Tanaka", "Silva", "Kowalski", "Haugen", "Reyes",
        "Fischer", "Brennan", "Aydin", "Costa", "Dahl", "Vos", "Petrov", "Nakamura", "Quinn", "Olsen"]
CO_A = ["Granite", "Northwind", "Bluefjord", "Summit", "Alder", "Cobalt", "Ridgeway", "Pinecrest", "Harbor", "Lumen"]
CO_B = ["Outfitters", "Expeditions", "Trading", "Supply", "Retail", "Sports", "Adventures", "Goods", "Co-op"]
COUNTRIES = {"US": ("USA", "United States"), "CA": ("CAN", "Canada"), "GB": ("GBR", "United Kingdom"),
             "DE": ("DEU", "Germany"), "FR": ("FRA", "France"), "NL": ("NLD", "Netherlands"),
             "SE": ("SWE", "Sweden"), "NO": ("NOR", "Norway")}
DIAL = {"US": "1", "CA": "1", "GB": "44", "DE": "49", "FR": "33", "NL": "31", "SE": "46", "NO": "47"}
CATEGORIES = ["Backpacks", "Tents", "Footwear", "Apparel", "Climbing", "Accessories"]
PRODUCT_WORDS = {"Backpacks": ["Trailblazer 45L Pack", "Ridge Daypack 22L", "Summit 65L Expedition Pack"],
                 "Tents": ["Fjord 2P Tent", "Basecamp 4P Tent", "Ultralite Bivy"],
                 "Footwear": ["Granite Hiking Boot", "Scree Trail Runner", "Glacier Insulated Boot"],
                 "Apparel": ["Storm Shell Jacket", "Merino Base Layer", "Down Hoodie", "Softshell Pant"],
                 "Climbing": ["Alpine Harness", "Dynamic Rope 60m", "Locking Carabiner"],
                 "Accessories": ["Trekking Poles", "Headlamp 400lm", "Water Filter", "Dry Bag 20L"]}
SEGMENTS = ["enterprise", "mid_market", "smb"]
SEG_STYLE = {"title": {"enterprise": "Enterprise", "mid_market": "Mid-Market", "smb": "SMB"},
             "codes": {"enterprise": "ENT", "mid_market": "MM", "smb": "SMB"},
             "lower": {"enterprise": "enterprise", "mid_market": "mid-market", "smb": "smb"}}
STATUS_STYLE = {"upper": str.upper, "title": str.title, "lower": str.lower,
                "codes": lambda s: {"placed": "OPEN", "shipped": "SHPD", "delivered": "DLVD",
                                    "cancelled": "CANC", "returned": "RET"}[s]}
BOOL_STYLE = {"yn": ("Y", "N"), "true_false": ("true", "false"), "one_zero": ("1", "0"), "x_blank": ("X", "")}
DEMO_AS_OF = ["2026-06-14", "2026-06-21"]


def load_source(name: str) -> dict:
    return yaml.safe_load((SOURCES_DIR / f"{name}.yaml").read_text())


def all_sources() -> list[dict]:
    return [yaml.safe_load(p.read_text()) for p in sorted(SOURCES_DIR.glob("*.yaml"))]


# ---------------------------------------------------------------- canonical dataset
def canonical(seed: int = 11, n_customers: int = 400, n_products: int = 40, end: str = DEMO_AS_OF[-1]) -> dict:
    rng = random.Random(seed)
    end_d = date.fromisoformat(end)
    customers = []
    for n in range(1, n_customers + 1):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        company = f"{rng.choice(CO_A)} {rng.choice(CO_B)}"
        cc = rng.choices(list(COUNTRIES), weights=[30, 8, 12, 14, 8, 8, 10, 10])[0]
        signup = date(2022, 1, 1) + timedelta(days=rng.randrange((end_d - date(2022, 1, 1)).days - 200))
        customers.append({"n": n, "full_name": f"{first} {last}", "company_name": company,
                          "email": f"{first}.{last}{n}@{company.split()[0].lower()}-{cc.lower()}.example".lower(),
                          "phone": str(rng.randrange(2, 10)) + "".join(str(rng.randrange(10)) for _ in range(9)), "country_code": cc,
                          "segment": rng.choices(SEGMENTS, weights=[15, 35, 50])[0], "signup_date": signup,
                          "is_active": rng.random() < 0.9})
    products = []
    for n in range(1, n_products + 1):
        cat = CATEGORIES[(n - 1) % len(CATEGORIES)]
        base = PRODUCT_WORDS[cat][(n - 1) // len(CATEGORIES) % len(PRODUCT_WORDS[cat])]
        name = base if n <= len(CATEGORIES) * len(PRODUCT_WORDS[cat]) else f"{base} v{n}"
        products.append({"n": n, "product_name": name, "category": cat,
                         "unit_price": round(rng.uniform(9, 1400), 2) if cat != "Accessories" else round(rng.uniform(5, 120), 2),
                         "currency": rng.choices(["USD", "EUR", "GBP"], weights=[80, 12, 8])[0],
                         "is_discontinued": rng.random() < 0.1})
    orders = []
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    span = int((datetime.combine(end_d, datetime.max.time(), tzinfo=timezone.utc) - start).total_seconds())
    for n in range(1, 2601):
        ts = start + timedelta(seconds=rng.randrange(span))
        cust = rng.choice(customers)
        prod = rng.choice(products)
        events = [(ts, "placed")]
        r = rng.random()
        if r < 0.07:
            events.append((ts + timedelta(hours=rng.randrange(1, 48)), "cancelled"))
        else:
            ship = ts + timedelta(hours=rng.randrange(12, 96))
            dlv = ship + timedelta(hours=rng.randrange(48, 168))
            events += [(ship, "shipped"), (dlv, "delivered")]
            if r > 0.95:
                events.append((dlv + timedelta(days=rng.randrange(5, 20)), "returned"))
        orders.append({"n": n, "customer_n": cust["n"], "product_n": prod["n"], "quantity": rng.choices(
            [1, 2, 3, 4, 5, 10], weights=[50, 22, 10, 8, 6, 4])[0], "unit_price": prod["unit_price"],
            "order_ts": ts, "events": events})
    return {"customers": customers, "products": products, "orders": orders}


def order_state(order: dict, as_of: datetime) -> dict | None:
    seen = [e for e in order["events"] if e[0] <= as_of]
    if not seen:
        return None
    return {**order, "status": seen[-1][1], "updated_at": seen[-1][0]}


# ---------------------------------------------------------------- rendering
def _fmt_date(d, style, rng):
    if isinstance(d, datetime):
        dt = d
    else:
        dt = datetime(d.year, d.month, d.day, rng.randrange(7, 19), rng.randrange(60), rng.randrange(60),
                      tzinfo=timezone.utc)
    if style == "iso_z":
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    if style == "mixed_dates":
        style = rng.choice(["%Y-%m-%d", "%m/%d/%Y", "%d %b %Y"])
    return dt.strftime(style)


def _money(v, style):
    return f"${v:,.2f}" if style == "dollar_commas" else f"{v:.2f}" if style == "plain" else str(v)


def _decoy(kind, rng, row_n):
    if kind == "free_text":
        return rng.choice(["", "", "", "call before delivery", "prefers email", "net 30 terms", "VIP - expedite",
                           "check credit hold", "migrated from old system"])
    if kind == "legacy_id":
        return f"LG-{rng.randrange(10**5, 10**6)}"
    if kind == "person_name":
        return f"{rng.choice(FIRST)} {rng.choice(LAST)}"
    if kind == "float_small":
        return f"{rng.uniform(0.1, 6):.2f}"
    if kind == "user_id_code":
        return "005" + "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(12))
    if kind == "money":
        return f"{rng.choice([5, 10, 25, 50, 100]) * 1000:.2f}"
    if kind == "code_short":
        return rng.choice(["US10", "DE20", "GB30", "NL40", "google", "newsletter", "1000", "2000"])
    if kind == "ip":
        return ".".join(str(rng.randrange(1, 255)) for _ in range(4))
    if kind == "int_small":
        return str(rng.randrange(0, 500))
    if kind == "uom":
        return rng.choice(["EA", "PC", "SET", "PR"])
    raise ValueError(kind)


def _value(field, rec, styles, ids, rng):
    style = styles.get(field)
    if field == "customer_id":
        return ids["customer"].format(n=rec["customer_n"] if "customer_n" in rec else rec["n"])
    if field == "sku":
        return ids["sku"].format(n=rec["product_n"] if "product_n" in rec else rec["n"])
    if field == "order_id":
        return ids["order"].format(n=rec["n"])
    v = rec[field]
    if field == "phone":
        c = rec["country_code"]
        return f"{v[:3]}-{v[3:6]}-{v[6:]}" if style == "us_dashes" else f"+{DIAL[c]} {v[:3]} {v[3:6]} {v[6:]}"
    if field == "country_code":
        if style == "mixed":
            style = rng.choice(["iso2", "iso2", "names", "lower", "iso3"])
        return {"iso2": v, "iso3": COUNTRIES[v][0], "names": COUNTRIES[v][1], "lower": v.lower()}[style]
    if field == "segment":
        return SEG_STYLE[style or "lower"][v]
    if field == "status":
        return STATUS_STYLE[style or "lower"](v)
    if field in ("signup_date", "order_ts", "updated_at"):
        return _fmt_date(v, style or "%Y-%m-%d", rng)
    if field in ("is_active", "is_discontinued"):
        return BOOL_STYLE[style or "true_false"][0 if v else 1]
    if field == "unit_price":
        return _money(v, style)
    if field == "category":
        return {"title": v, "upper": v.upper(), "lower": v.lower()}[style or "title"]
    if field == "quantity":
        return str(v)
    return v


def render(source: dict, entity: str, records: list[dict], seed: int = 3) -> list[dict]:
    spec = source["entities"][entity]
    rng = random.Random(f"{source['name']}:{entity}:{seed}")
    out = []
    for rec in records:
        row = {}
        for col in spec["columns"]:
            row[col["column"]] = (_value(col["gold"], rec, spec.get("styles", {}), source["ids"], rng)
                                  if col["gold"] else _decoy(col["decoy"], rng, rec["n"]))
        out.append(row)
    return out


def columns_of(rows: list[dict]) -> dict[str, list]:
    cols = list(rows[0]) if rows else []
    return {c: [r.get(c) for r in rows] for c in cols}


def eval_columns(source: dict, entity: str, seed: int = 11) -> dict[str, list]:
    """Sample of one source entity, rendered in that source's dialect, as {column: values}."""
    data = canonical(seed)
    as_of = datetime(2026, 6, 21, 23, 59, tzinfo=timezone.utc)
    recs = {"customers": data["customers"][:80], "products": data["products"],
            "orders": [s for o in data["orders"][:150] if (s := order_state(o, as_of))]}[entity]
    return columns_of(render(source, entity, recs))


# ---------------------------------------------------------------- the demo customer's mess
def write_demo(root: Path, seed: int = 11) -> dict:
    """Write the Halvorsen demo: two CSV drops and the event history behind the mock orders API."""
    src = load_source("halvorsen")
    data = canonical(seed)
    rng = random.Random(seed)
    d1, d2 = (date.fromisoformat(d) for d in DEMO_AS_OF)
    custs = data["customers"]
    # the last 25 customers are "new" in the second drop; 15 existing ones get a segment change
    for c in custs[-25:]:
        c["signup_date"] = d1 + timedelta(days=1 + c["n"] % 7)
    new_ids = {c["n"] for c in custs[-25:]}
    for o in data["orders"]:
        if o["customer_n"] in new_ids and o["order_ts"].date() <= d1:
            o["customer_n"] = custs[o["n"] % 300]["n"]

    rows1 = render(src, "customers", custs[:-25])
    _mess_customers(rows1, rng)
    changed = [dict(c, segment=SEGMENTS[(SEGMENTS.index(c["segment"]) + 1) % 3]) for c in custs[10:25]]
    rows2 = render(src, "customers", changed + custs[-25:], seed=4)
    for r in rows2:
        r["Account Manager"] = _decoy("person_name", rng, 0)  # schema drift: a column the reviewed mapping never saw
    prows = render(src, "products", data["products"])
    fixed = dict(prows[7])
    prows[7]["List Price"] = "call for price"                        # unparseable -> quarantined on day 1

    base = root / "halvorsen"
    _write_csv(base / "drops" / DEMO_AS_OF[0] / "customers.csv", rows1)
    _write_csv(base / "drops" / DEMO_AS_OF[0] / "products.csv", prows)
    _write_csv(base / "drops" / DEMO_AS_OF[1] / "customers.csv", rows2)
    _write_csv(base / "drops" / DEMO_AS_OF[1] / "products.csv", [fixed])   # customer re-sends the corrected row

    events = []
    for o in data["orders"]:
        rec = {"n": o["n"], "customer_n": o["customer_n"], "product_n": o["product_n"], "quantity": o["quantity"],
               "unit_price": o["unit_price"]}
        if o["n"] % 397 == 0:
            rec["quantity"] = 0                      # contract violation -> quarantine
        if o["n"] % 311 == 0:
            rec["customer_n"] = 9000 + o["n"]        # customer we never received -> orphan (DQ warning)
        events.append({"order": rec, "order_ts": o["order_ts"].isoformat(),
                       "events": [[t.isoformat(), s] for t, s in o["events"]],
                       "bad_status": o["n"] % 523 == 0})
    (base / "api").mkdir(parents=True, exist_ok=True)
    (base / "api" / "order_events.json").write_text(json.dumps(events))
    return {"customers_drop1": len(rows1), "customers_drop2": len(rows2), "products": len(prows), "products_drop2": 1,
            "orders_total": len(events)}


def _mess_customers(rows, rng):
    for i, r in enumerate(rows):
        if i % 23 == 0:
            r["E-mail Address"] = "  " + r["E-mail Address"].upper() + " "      # fixable: normalised by contract
        if i % 61 == 5:
            r["E-mail Address"] = rng.choice(["n/a", "", "unknown@"])           # invalid -> quarantine
        if i % 97 == 7:
            r["Cust #"] = ""                                                    # missing key -> quarantine
        if i % 89 == 3:
            r["Created On"] = "TBD"                                             # unparseable -> quarantine
    rows.insert(40, dict(rows[12]))
    rows.insert(90, dict(rows[55]))                                             # exact duplicates
    rows[130]["Ctry"] = "Atlantis"                                              # unknown country -> quarantine


def _write_csv(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
