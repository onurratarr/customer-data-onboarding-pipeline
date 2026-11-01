"""The customer's orders REST API (mock) and a paginated client with retries.

MockOrdersAPI serves the synthetic order history as it looked at `as_of`, with `updated_since` filtering, page
pagination, bearer auth and deterministic transient faults (503s and 429s with Retry-After). It can be called
in-process or served over real HTTP (`python -m cdop.cli serve-api`); the client is the same either way."""
from __future__ import annotations

import json
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import synth


class MockOrdersAPI:
    def __init__(self, events_path: Path, as_of: datetime, fail_every: int = 5, throttle_every: int = 7):
        self.source = synth.load_source("halvorsen")
        self.events = json.loads(Path(events_path).read_text())
        self.as_of = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
        self.fail_every, self.throttle_every = fail_every, throttle_every
        self.requests = 0
        self._lock = threading.Lock()
        self._cache = None

    def _records(self):
        if self._cache is not None:
            return self._cache
        out = []
        for ev in self.events:
            evs = [(datetime.fromisoformat(t), s) for t, s in ev["events"] if datetime.fromisoformat(t) <= self.as_of]
            if not evs:
                continue
            rec = dict(ev["order"], status=evs[-1][1], updated_at=evs[-1][0], order_ts=datetime.fromisoformat(ev["order_ts"]))
            row = synth.render(self.source, "orders", [rec], seed=rec["n"])[0]
            row["qty"] = int(row["qty"])
            if ev["bad_status"]:
                row["state"] = "ON_HOLD"
            out.append(row)
        self._cache = sorted(out, key=lambda r: (r["modified"], r["id"]))
        return self._cache

    def handle(self, method: str, path: str, query: dict, headers: dict) -> tuple[int, dict, bytes]:
        with self._lock:
            self.requests += 1
            n = self.requests
        if not headers.get("Authorization", "").startswith("Bearer ") or len(headers["Authorization"]) <= 7:
            return 401, {}, b'{"error": "missing bearer token"}'
        if method != "GET" or path != "/v2/orders":
            return 404, {}, b'{"error": "not found"}'
        if self.fail_every and n % self.fail_every == 0:
            return 503, {}, b'{"error": "upstream unavailable"}'
        if self.throttle_every and n % self.throttle_every == 0:
            return 429, {"Retry-After": "0.01"}, b'{"error": "rate limited"}'
        page = int(query.get("page", 1))
        size = min(int(query.get("page_size", 100)), 250)
        recs = self._records()
        if query.get("updated_since"):
            since = query["updated_since"]
            recs = [r for r in recs if r["modified"] >= since]
        chunk = recs[(page - 1) * size: page * size]
        body = {"data": chunk, "page": page, "page_size": size, "total": len(recs),
                "next_page": page + 1 if page * size < len(recs) else None}
        return 200, {"Content-Type": "application/json"}, json.dumps(body).encode()


class InProcessTransport:
    def __init__(self, api: MockOrdersAPI):
        self.api = api

    def get(self, path, query, headers):
        return self.api.handle("GET", path, query, headers)


class UrllibTransport:
    """Real HTTP using only the standard library."""

    def __init__(self, base_url: str, timeout: float = 10):
        self.base_url, self.timeout = base_url.rstrip("/"), timeout

    def get(self, path, query, headers):
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(query)}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()


class TransientError(Exception):
    pass


@dataclass
class FetchStats:
    requests: int = 0
    retries: int = 0
    pages: int = 0
    records: int = 0
    statuses: dict = field(default_factory=dict)


class PaginatedClient:
    RETRY_STATUSES = {429, 500, 502, 503, 504}

    def __init__(self, transport, token: str | None = None, max_retries: int = 5, backoff_base: float = 0.02,
                 sleep=time.sleep, seed: int = 0):
        self.transport, self.max_retries, self.backoff_base, self.sleep = transport, max_retries, backoff_base, sleep
        self.token = token or os.environ.get("SOURCE_API_TOKEN", "demo-token")
        self.rng = random.Random(seed)
        self.stats = FetchStats()

    def _get(self, path, query):
        for attempt in range(self.max_retries + 1):
            self.stats.requests += 1
            try:
                status, headers, body = self.transport.get(path, query, {"Authorization": f"Bearer {self.token}"})
            except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
                status, headers, body = None, {}, str(e).encode()
            self.stats.statuses[str(status)] = self.stats.statuses.get(str(status), 0) + 1
            if status == 200:
                return json.loads(body)
            if status is not None and status not in self.RETRY_STATUSES:
                raise RuntimeError(f"GET {path} failed with {status}: {body[:200]!r}")
            if attempt == self.max_retries:
                raise TransientError(f"GET {path} still failing after {self.max_retries} retries (last: {status})")
            self.stats.retries += 1
            retry_after = {k.lower(): v for k, v in headers.items()}.get("retry-after")
            delay = float(retry_after) if retry_after else self.backoff_base * 2 ** attempt * (1 + self.rng.random())
            self.sleep(delay)

    def fetch_all(self, path="/v2/orders", updated_since: str | None = None, page_size: int = 100) -> list[dict]:
        out, page = [], 1
        while page:
            q = {"page": page, "page_size": page_size}
            if updated_since:
                q["updated_since"] = updated_since
            body = self._get(path, q)
            out += body["data"]
            self.stats.pages += 1
            page = body["next_page"]
        self.stats.records = len(out)
        return out


def serve(api: MockOrdersAPI, host="127.0.0.1", port=8765) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            status, headers, body = api.handle("GET", u.path, q, dict(self.headers))
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
