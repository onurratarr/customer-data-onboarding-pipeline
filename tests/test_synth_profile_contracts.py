from datetime import date, datetime
from decimal import Decimal

import pytest

from cdop import synth
from cdop.contracts import parse_bool, parse_date, parse_money, parse_ts, validate_rows
from cdop.mapping import CANONICAL
from cdop.profile import profile_column


def test_canonical_data_is_deterministic():
    a, b = synth.canonical(11), synth.canonical(11)
    assert a["orders"][:50] == b["orders"][:50] and len(a["customers"]) == 400


def test_demo_files(demo_data):
    base = demo_data / "data" / "halvorsen"
    assert (base / "drops/2026-06-14/customers.csv").exists()
    assert (base / "drops/2026-06-21/products.csv").exists()
    assert (base / "api/order_events.json").exists()


@pytest.mark.parametrize("name", ["halvorsen", "crm_export", "erp_heldout", "shop_heldout"])
def test_gold_specs_cover_every_required_field(name):
    src = synth.load_source(name)
    for entity, spec in src["entities"].items():
        gold = [c["gold"] for c in spec["columns"] if c.get("gold")]
        assert len(gold) == len(set(gold))
        required = [f for f, s in CANONICAL[entity]["fields"].items() if s.get("required")]
        assert set(required) <= set(gold)
        assert all(c.get("decoy") for c in spec["columns"] if not c.get("gold"))


def test_profile_detects_kinds():
    assert profile_column("x", ["a@b.com", "c@d.org", "e@f.net"])["inferred_type"] == "email"
    assert profile_column("x", ["2026-01-05", "2026-02-11", "2026-03-01"])["inferred_type"] == "date"
    assert profile_column("x", ["555-201-3344", "555-777-1234", "555-101-9988"])["inferred_type"] == "phone"
    qty = profile_column("qty", [str(i % 5 + 1) for i in range(100)])
    assert qty["inferred_type"] == "integer"


def test_parsers():
    assert parse_date("03/07/2026") in (date(2026, 3, 7), date(2026, 7, 3))
    assert parse_date("2026-03-07") == date(2026, 3, 7)
    assert parse_ts("2026-03-07T10:00:00Z") == datetime(2026, 3, 7, 10)
    assert parse_ts("2026-03-07T12:00:00+02:00") == datetime(2026, 3, 7, 10)
    assert parse_money("$1,234.5") == Decimal("1234.50")
    assert parse_bool("Y") is True and parse_bool("no") is False
    with pytest.raises(ValueError):
        parse_money("call for price")
    with pytest.raises(ValueError):
        parse_bool("maybe")


def _cust(**kw):
    row = {"customer_id": "C1", "email": "  A@Example.COM ", "full_name": "Ada L", "company_name": "Acme",
           "phone": "555-201-3344", "country_code": "Germany", "segment": "Mid-Market", "signup_date": "2026-01-02",
           "is_active": "Y"}
    row.update(kw)
    return row


def test_contract_normalises_good_rows():
    good, bad = validate_rows("customers", [_cust()])
    assert not bad
    assert good[0]["email"] == "a@example.com" and good[0]["country_code"] == "DE"
    assert good[0]["segment"] == "mid_market" and good[0]["is_active"] is True


def test_contract_quarantines_with_reasons():
    rows = [_cust(email="n/a"), _cust(customer_id=""), _cust(country_code="Atlantis"), _cust(signup_date="TBD"),
            _cust(segment="ENT")]  # ERP code: not a known synonym, so it must not be guessed
    good, bad = validate_rows("customers", rows)
    assert not good and [b["row_number"] for b in bad] == [1, 2, 3, 4, 5]
    assert "missing required value" in " ".join(bad[1]["errors"])
    assert "country" in " ".join(bad[2]["errors"])


def test_order_contract():
    base = {"order_id": "O1", "customer_id": "C1", "sku": "S1", "quantity": "2", "unit_price": "10",
            "order_ts": "2026-01-01T00:00:00Z", "status": "SHIPPED", "updated_at": "2026-01-02T00:00:00Z"}
    good, bad = validate_rows("orders", [base, dict(base, quantity="0"), dict(base, status="ON_HOLD")])
    assert len(good) == 1 and good[0]["status"] == "shipped" and len(bad) == 2
