"""
LOT H — Production polish.

Covers:
- SlidingWindowRateLimiter (reads vs mutations budgets, window expiry, 429);
- rate-limit middleware behavior (healthz exempt, 429 JSON body);
- REAL-mode warning: /api/status real_warning field + /api/mode warning.
"""
import asyncio
import os

os.environ["TESTING"] = "true"

import pytest
from fastapi.testclient import TestClient

from api.index import app, rate_limit_middleware, rate_limiter
from api.rate_limit import SlidingWindowRateLimiter

client = TestClient(app)


# --------------------------------------------------------------------------- #
# 1. Rate limiter unit                                                        #
# --------------------------------------------------------------------------- #
class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_rate_limiter_budgets_and_window():
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(requests_per_minute=3,
                                       mutations_per_minute=2, window_s=60.0,
                                       clock=clock)
    assert limiter.allow("a") is True
    assert limiter.allow("a") is True
    assert limiter.allow("a") is True
    assert limiter.allow("a") is False  # 4th read blocked

    assert limiter.allow("b", is_mutation=True) is True
    assert limiter.allow("b", is_mutation=True) is True
    assert limiter.allow("b", is_mutation=True) is False  # mutation budget stricter

    # Different clients don't share budgets
    assert limiter.allow("c") is True

    # Window expiry frees the budget
    clock.advance(61.0)
    assert limiter.allow("a") is True

    limiter.reset()
    assert limiter.tracked_clients() == 0


# --------------------------------------------------------------------------- #
# 2. Middleware behavior                                                      #
# --------------------------------------------------------------------------- #
class FakeRequest:
    def __init__(self, path="/api/metrics", method="GET", host="1.2.3.4"):
        self.url = type("U", (), {"path": path})()
        self.method = method
        self.client = type("C", (), {"host": host})()


async def test_middleware_blocks_when_budget_exhausted(monkeypatch):
    clock = FakeClock()
    tiny = SlidingWindowRateLimiter(requests_per_minute=2, mutations_per_minute=1,
                                    window_s=60.0, clock=clock)
    monkeypatch.setattr("api.index.rate_limiter", tiny)

    calls = {"n": 0}

    async def call_next(request):
        calls["n"] += 1
        return type("R", (), {"status_code": 200})()

    assert (await rate_limit_middleware(FakeRequest(), call_next)).status_code == 200
    assert (await rate_limit_middleware(FakeRequest(), call_next)).status_code == 200
    blocked = await rate_limit_middleware(FakeRequest(), call_next)
    assert blocked.status_code == 429
    assert calls["n"] == 2  # third request never reached the endpoint


async def test_middleware_healthz_exempt_and_mutations_stricter(monkeypatch):
    clock = FakeClock()
    tiny = SlidingWindowRateLimiter(requests_per_minute=100, mutations_per_minute=1,
                                    window_s=60.0, clock=clock)
    monkeypatch.setattr("api.index.rate_limiter", tiny)

    async def call_next(request):
        return type("R", (), {"status_code": 200})()

    # healthz never rate-limited
    for _ in range(5):
        res = await rate_limit_middleware(FakeRequest(path="/healthz"), call_next)
        assert res.status_code == 200

    # mutations: 1 allowed, then 429
    assert (await rate_limit_middleware(FakeRequest(method="POST"), call_next)).status_code == 200
    assert (await rate_limit_middleware(FakeRequest(method="POST"), call_next)).status_code == 429


# --------------------------------------------------------------------------- #
# 3. REAL-mode warning                                                        #
# --------------------------------------------------------------------------- #
def test_status_real_warning_is_none_in_demo():
    # Without auth configured, mutating endpoints are open; mode stays DEMO.
    response = client.get("/api/status?market_id=__nonexistent__")
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "DEMO"
    assert data["real_warning"] is None


def test_mode_toggle_returns_warning_in_real(monkeypatch):
    from api import index as idx
    idx.rate_limiter.reset()

    async def fake_set_mode(mode):
        return True, "LIVE MODE active."

    monkeypatch.setattr(idx.broker_connector, "set_mode", fake_set_mode)
    # Switch to REAL → warning present
    res = client.post("/api/mode")
    assert res.status_code == 200
    data = res.json()
    assert data["mode"] == "REAL"
    assert "experimental" in data["warning"]

    # Switch back to DEMO → no warning, REAL mode fields cleared
    res2 = client.post("/api/mode")
    assert res2.json()["mode"] == "DEMO"
    assert "warning" not in res2.json()


def test_real_warning_constant_is_explicit():
    from api.index import REAL_MODE_WARNING
    assert "DEMO" in REAL_MODE_WARNING and "experimental" in REAL_MODE_WARNING.lower()
