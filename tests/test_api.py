from datetime import datetime, timezone

import pytest

from cdop import api


def _api(demo_data, **kw):
    return api.MockOrdersAPI(demo_data / "data/halvorsen/api/order_events.json",
                             datetime(2026, 6, 21, 23, 59, 59, tzinfo=timezone.utc), **kw)


def test_client_retries_and_paginates(demo_data):
    slept = []
    m = _api(demo_data)
    c = api.PaginatedClient(api.InProcessTransport(m), sleep=slept.append)
    recs = c.fetch_all(page_size=100)
    assert len(recs) == len({r["id"] for r in recs}) == 2600
    assert c.stats.retries > 0 and c.stats.statuses.get("503") and c.stats.statuses.get("429")
    assert 0.01 in slept  # Retry-After honoured
    clean = api.PaginatedClient(api.InProcessTransport(_api(demo_data, fail_every=0, throttle_every=0)))
    assert clean.fetch_all(page_size=100) == recs and clean.stats.retries == 0


def test_incremental_since_is_inclusive(demo_data):
    c = api.PaginatedClient(api.InProcessTransport(_api(demo_data)), sleep=lambda _: None)
    allr = c.fetch_all()
    wm = sorted(r["modified"] for r in allr)[-10]
    since = c.fetch_all(updated_since=wm)
    assert len(since) == sum(1 for r in allr if r["modified"] >= wm) >= 10


def test_auth_and_give_up(demo_data):
    m = _api(demo_data)
    assert m.handle("GET", "/v2/orders", {}, {})[0] == 401
    with pytest.raises(api.TransientError):
        api.PaginatedClient(api.InProcessTransport(_api(demo_data, fail_every=1)), max_retries=2,
                            sleep=lambda _: None).fetch_all()


def test_real_http_round_trip(demo_data):
    srv = api.serve(_api(demo_data), port=0)
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        c = api.PaginatedClient(api.UrllibTransport(url), sleep=lambda _: None)
        recs = c.fetch_all(page_size=250)
        assert len(recs) == 2600 and c.stats.retries > 0
        bad = api.PaginatedClient(api.UrllibTransport(url), token="", sleep=lambda _: None)
        bad.token = ""
        with pytest.raises(RuntimeError, match="401"):
            bad.fetch_all()
    finally:
        srv.shutdown()
