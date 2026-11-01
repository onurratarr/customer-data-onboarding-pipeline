import json

import pytest
import yaml

from cdop import review, synth
from cdop.mapping import MockMapper, OpenAICompatibleMapper, Proposal, get_mapper, propose_source


def _proposals(name="halvorsen"):
    src = synth.load_source(name)
    return propose_source(MockMapper(), {e: synth.eval_columns(src, e) for e in src["entities"]})


def test_mock_mapper_is_deterministic_and_matches_dev_gold():
    a, b = _proposals(), _proposals()
    assert [p.to_dict() for p in a["orders"]] == [p.to_dict() for p in b["orders"]]
    spec = synth.load_source("halvorsen")["entities"]
    for entity, props in a.items():
        gold = {c["column"]: c.get("gold") for c in spec[entity]["columns"]}
        assert {p.source_column: p.target for p in props} == gold
        assert all(0 <= p.confidence <= 1 and p.reasons for p in props)


def test_get_mapper_env(monkeypatch):
    monkeypatch.delenv("MAPPER_BACKEND", raising=False)
    assert get_mapper().name == "mock-lexicon-v1"
    monkeypatch.setenv("MAPPER_BACKEND", "openai")
    assert get_mapper().name.startswith("openai-compatible")


def test_openai_compatible_mapper_with_fake_endpoint():
    calls = []

    def post(url, payload):
        calls.append((url, payload))
        content = json.dumps({"mappings": [
            {"source_column": "SKU#", "target": "sku", "confidence": 0.9, "reason": "id"},
            {"source_column": "Name", "target": "product_name", "confidence": 0.8, "reason": "text"},
            {"source_column": "Junk", "target": "not_a_field", "confidence": 0.9}]})
        return {"choices": [{"message": {"content": content}}]}

    m = OpenAICompatibleMapper(base_url="http://fake/v1", model="m", api_key="k", post=post)
    props = {p.source_column: p for p in m.propose("products", {"SKU#": ["A1", "A2"], "Name": ["x y", "z w"],
                                                               "Junk": ["1", "2"]})}
    assert calls[0][0] == "http://fake/v1/chat/completions"
    assert props["SKU#"].target == "sku" and props["Name"].target == "product_name"
    assert props["Junk"].target is None  # invalid field names from the model are dropped


def _review(tmp_path, entity, props, decisions=None, accept_above=0.9):
    return review.review("halvorsen", entity, props, review.decide_scripted(decisions, accept_above),
                         reviewer="test", proposed_by="mock", root=tmp_path)


def test_review_writes_versioned_files(tmp_path):
    props = _proposals()["customers"]
    p1 = _review(tmp_path, "customers", props, {"Notes": None, "Legacy Ref": None})
    assert p1.name == "customers.v001.yaml"
    assert _review(tmp_path, "customers", props, {"Notes": None, "Legacy Ref": None}) == p1  # no change, no version
    p2 = _review(tmp_path, "customers", props, {"Notes": None, "Legacy Ref": None, "Account": None,
                                                "Contact Name": "company_name"})
    doc = yaml.safe_load(p2.read_text())
    assert p2.name == "customers.v002.yaml" and doc["supersedes"] == 1
    by = {c["source_column"]: c for c in doc["columns"]}
    assert by["Contact Name"]["decision"] == "edited" and by["Contact Name"]["proposed_target"] == "full_name"
    assert by["Account"]["decision"] == "rejected" and by["Cust #"]["decision"] == "accepted"
    assert review.latest("halvorsen", "customers", tmp_path)["version"] == 2


def test_review_blocks_bad_approvals(tmp_path):
    props = _proposals()["customers"]
    with pytest.raises(review.ReviewError, match="need a human decision"):
        _review(tmp_path, "customers", props)
    with pytest.raises(review.ReviewError, match="required"):
        _review(tmp_path, "customers", props, {"Notes": None, "Legacy Ref": None, "Cust #": None})
    with pytest.raises(review.ReviewError, match="twice"):
        _review(tmp_path, "customers", props, {"Notes": "email", "Legacy Ref": None})
    with pytest.raises(review.ReviewError, match="unknown"):
        _review(tmp_path, "customers", props, {"Notes": "nope", "Legacy Ref": None})
    assert not list(tmp_path.rglob("*.yaml"))


def test_interactive_review(tmp_path):
    props = [Proposal("a", "sku", 0.9, ["r"]), Proposal("b", "product_name", 0.5, ["r"]),
             Proposal("c", "category", 0.4, ["r"]), Proposal("d", None, 0.6, ["r"]),
             Proposal("e", "currency", 0.95, ["r"]), Proposal("f", "is_discontinued", 0.9, ["r"])]
    answers = iter(["a", "e", "product_name", "r", "e", "unit_price", "", ""])
    decide = review.decide_interactive(inp=lambda _: next(answers), out=lambda *_: None)
    doc = yaml.safe_load(review.review("x", "products", props, decide, reviewer="t", root=tmp_path).read_text())
    assert review.mapping_dict(doc) == {"a": "sku", "b": "product_name", "d": "unit_price", "e": "currency",
                                        "f": "is_discontinued"}


def test_committed_demo_mappings_are_approved():
    for entity in ("customers", "products", "orders"):
        doc = review.latest("halvorsen", entity)
        assert doc and doc["status"] == "approved" and doc["reviewed_by"]
