"""v3.3 contract — protection state machine, NAKED window after close
failure, durable idempotence, partial fills, PnL/fees, honest emergency
stop, restart recovery, protected mutations, production fail-fast, secret
redaction.

All tests are offline (mocks only, no network)."""
from __future__ import annotations

import json
import logging
import time

import pytest

import api.index as idx
from api.engines.broker_adapters.ccxt_adapter import CCXTAdapter
from api.engines.broker_connector import BrokerConnector
from api.engines.db_manager import DatabaseManager
from api.engines import pnl_engine
from api.engines import protection_state
from api.json_logging import JsonFormatter
from api.security import (
    ProductionConfigError,
    api_key_matches,
    assert_production_ready,
    is_weak_key,
    production_config_errors,
)
from cryptography.fernet import Fernet

from tests.test_v32 import ScriptedAdapter, SimpleClient, SpyNotifier, _open_trade


# --------------------------------------------------------------------------- #
# Fixtures & helpers                                                           #
# --------------------------------------------------------------------------- #
@pytest.fixture()
def connector(tmp_path):
    db = DatabaseManager(str(tmp_path / "v33.db"))
    return BrokerConnector(db_manager=db), db


# --------------------------------------------------------------------------- #
# 1-3. ID present but canceled / expired / rejected → NAKED (no fake close)    #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status", ["canceled", "expired", "rejected"])
async def test_id_present_but_dead_protection_is_naked(connector, status):
    c, db = connector
    spy = SpyNotifier()
    c.notifier = spy
    adapter = ScriptedAdapter(fetch_status_map={
        "TP-1": {"status": status, "filled": 0.0, "average": None, "fees": 0.0},
        "SL-1": {"status": "open", "filled": 0.0, "average": None, "fees": 0.0},
    })
    c.active_adapters["b1"] = adapter
    _open_trade(db)
    closed = await c.reconcile_positions()
    assert closed == []  # an ID alone (dead) never closes the DB
    trade = db.get_active_positions("REAL")[0]
    assert trade["status"] == "OPEN"
    assert trade["metadata"]["protection_status"] == "NAKED"
    assert trade["metadata"]["tp_order_status"] == status.upper()
    assert any(e == "PROTECTION_LOST" for e, _ in spy.events)


# --------------------------------------------------------------------------- #
# 4. OPEN recently confirmed → protection alive, no backstop                   #
# --------------------------------------------------------------------------- #
async def test_open_recently_confirmed_is_alive(connector):
    c, db = connector
    adapter = ScriptedAdapter(fetch_status_map={
        "TP-1": {"status": "open", "filled": 0.0, "average": None, "fees": 0.0},
        "SL-1": {"status": "open", "filled": 0.0, "average": None, "fees": 0.0},
    })
    c.active_adapters["b1"] = adapter
    _open_trade(db)
    assert await c.reconcile_positions() == []
    trade = db.get_active_positions("REAL")[0]
    meta = trade["metadata"]
    assert meta["protection_status"] == "OPEN"
    assert meta["protection_checked_at"] > 0
    assert protection_state.backstop_allowed(meta) is False


# --------------------------------------------------------------------------- #
# 5. Repeated check errors → UNKNOWN + audit + notification                    #
# --------------------------------------------------------------------------- #
async def test_repeated_errors_unknown(connector):
    c, db = connector
    spy = SpyNotifier()
    c.notifier = spy
    adapter = ScriptedAdapter()  # fetch_order_status → None (error) always
    c.active_adapters["b1"] = adapter
    _open_trade(db)
    for _ in range(protection_state.MAX_CONSECUTIVE_ERRORS):
        assert await c.reconcile_positions() == []
    trade = db.get_active_positions("REAL")[0]
    meta = trade["metadata"]
    assert meta["protection_status"] == "UNKNOWN"
    assert meta["protection_error_count"] == protection_state.MAX_CONSECUTIVE_ERRORS
    assert any(e == "POSITION_UNKNOWN" for e, _ in spy.events)
    # CRITICAL audit entry written
    with db._get_connection() as conn:
        rows = conn.execute(
            "SELECT action FROM audit_logs WHERE action = 'PROTECTION_STATE_UNKNOWN'"
        ).fetchall()
    assert len(rows) >= 1


# --------------------------------------------------------------------------- #
# 6. No fake-close on UNKNOWN                                                  #
# --------------------------------------------------------------------------- #
async def test_no_fake_close_on_unknown(connector):
    c, db = connector
    adapter = ScriptedAdapter()
    c.active_adapters["b1"] = adapter
    _open_trade(db)
    for _ in range(5):
        assert await c.reconcile_positions() == []
    assert len(db.get_active_positions("REAL")) == 1
    assert db.get_active_positions("REAL")[0]["status"] == "OPEN"


# --------------------------------------------------------------------------- #
# 7-9. Hedge failed AFTER the cancels → NAKED window                           #
# --------------------------------------------------------------------------- #
async def test_hedge_failed_after_cancel(connector):
    c, db = connector
    adapter = ScriptedAdapter(close_result={"success": False,
                                            "reason": "BROKER_CLOSE_ERROR: down"})
    c.active_adapters["b1"] = adapter
    _open_trade(db)
    res = await c.close_position("btc_usdt")
    assert res["success"] is False
    assert res["naked"] is True
    assert "cancel" in [a[0] for a in adapter.calls]
    assert ("close", "BTC/USDT") in adapter.calls


async def test_trade_stays_open_and_naked(connector):
    c, db = connector
    adapter = ScriptedAdapter(close_result={"success": False,
                                            "reason": "BROKER_CLOSE_ERROR: down"})
    c.active_adapters["b1"] = adapter
    _open_trade(db)
    await c.close_position("btc_usdt")
    trade = db.get_active_positions("REAL")[0]
    assert trade["status"] == "OPEN"  # never a fake success
    meta = trade["metadata"]
    assert meta["sl_tp_failed"] is True
    assert meta["protection_status"] == "NAKED"
    assert meta["protection_cancelled_before_close"] is True
    assert sorted(meta["cancelled_protection"]) == ["SL-1", "TP-1"]
    assert meta["close_failure_error"] == "BROKER_CLOSE_ERROR: down"
    assert meta["close_failure_at"]
    # The next tick sees the position as unprotected (backstop allowed):
    assert protection_state.backstop_allowed(meta) is True


async def test_critical_notification_and_audit(connector):
    c, db = connector
    spy = SpyNotifier()
    c.notifier = spy
    adapter = ScriptedAdapter(close_result={"success": False,
                                            "reason": "BROKER_CLOSE_ERROR: down"})
    c.active_adapters["b1"] = adapter
    _open_trade(db)
    await c.close_position("btc_usdt")
    assert any(e == "HEDGE_FAILED_AFTER_CANCEL" for e, _ in spy.events)
    with db._get_connection() as conn:
        rows = conn.execute(
            "SELECT level, action FROM audit_logs WHERE action = 'REAL_CLOSE_NAKED'"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["level"] == "CRITICAL"


# --------------------------------------------------------------------------- #
# 10-13. Idempotence: order recovered in closed orders / trades,               #
#        ORDER_STATE_UNKNOWN, no automatic retry                               #
# --------------------------------------------------------------------------- #
async def _run_ambiguous_send(c, db, client, expected_id, seed_order):
    import unittest.mock as mock

    import api.engines.broker_connector as bc_module

    if seed_order is not None:
        client.seed(**seed_order)

    async def flaky_create(*args):
        client.create_calls.append(args)
        raise RuntimeError("timeout after create")

    client.create_order = flaky_create
    adapter = CCXTAdapter("gate", "k", "s")
    adapter.client = client
    c.active_adapters["b1"] = adapter

    class FakeUUID:
        hex = "abcdef0123456789abcdef0123456789"

    with mock.patch.object(bc_module.time, "time", return_value=1700000000.456), \
         mock.patch.object(bc_module.uuid, "uuid4", return_value=FakeUUID()):
        res = await c.execute(
            {"market_id": "btc_usdt", "direction": "BUY", "entry": 100.0,
             "sl": 95.0, "tp": 110.0, "strategy": "rsi"},
            {"quantity": 1.0, "leverage": 1.0})
    return res, expected_id


async def test_order_found_in_closed_orders(connector):
    """Exchange where fetch_order(clientOrderId) fails: lookup falls through
    to open orders (empty) then closed orders."""
    c, db = connector

    class StrictClient(SimpleClient):
        async def fetch_order(self, order_id, symbol=None):
            if order_id in self.orders:
                return dict(self.orders[order_id])
            raise RuntimeError("strict exchange id required")

    client = StrictClient()
    expected_id = "QTP-1700000000456-abcdef"
    # Seed ONLY in closed orders (not resolvable by client id fetch):
    order = {
        "id": "ORD-CLOSED-1", "clientOrderId": expected_id, "status": "closed",
        "filled": 1.0, "average": 102.0, "fee": {"cost": 0.3, "currency": "USDT"},
        "symbol": "BTC/USDT", "info": {"clientOrderId": expected_id},
    }
    client.closed_orders.append(order)
    res, _ = await _run_ambiguous_send(c, db, client, expected_id, None)
    assert res["success"] is True
    assert res.get("recovered_after_error") is True
    assert db.get_order_intent(expected_id)["status"] == "CONFIRMED"


async def test_order_found_in_trades(connector):
    """Nothing in orders/open/closed — the fill is only visible in trades."""
    c, db = connector

    class TradesClient(SimpleClient):
        async def fetch_order(self, order_id, symbol=None):
            raise RuntimeError("order lookup unavailable")

        async def fetch_closed_orders(self, symbol=None, limit=50):
            raise RuntimeError("closed orders unavailable")

    client = TradesClient()
    expected_id = "QTP-1700000000456-abcdef"
    client.trades.append({
        "id": "T-1", "order": "ORD-T-1", "side": "buy", "amount": 1.0,
        "price": 101.0, "fee": {"cost": 0.3, "currency": "USDT"},
        "timestamp": int(time.time() * 1000),
        "info": {"clientOrderId": expected_id},
    })
    res, _ = await _run_ambiguous_send(c, db, client, expected_id, None)
    assert res["success"] is True
    assert res["average"] == pytest.approx(101.0)
    positions = db.get_active_positions("REAL")
    assert len(positions) == 1


async def test_order_state_unknown(connector):
    c, db = connector
    spy = SpyNotifier()
    c.notifier = spy
    client = SimpleClient()
    expected_id = "QTP-1700000000456-abcdef"
    res, _ = await _run_ambiguous_send(c, db, client, expected_id, None)
    assert res["success"] is False
    assert res["reason"] == "ORDER_STATE_UNKNOWN"
    intent = db.get_order_intent(expected_id)
    assert intent is not None
    assert intent["status"] == "ORDER_STATE_UNKNOWN"
    with db._get_connection() as conn:
        rows = conn.execute(
            "SELECT level FROM audit_logs WHERE action = 'ORDER_STATE_UNKNOWN'"
        ).fetchall()
    assert rows and rows[0]["level"] == "CRITICAL"
    assert any(e == "ORDER_STATE_UNKNOWN" for e, _ in spy.events)
    # No position created in double:
    assert db.get_active_positions("REAL") == []


async def test_no_auto_retry_on_ambiguous_order(connector):
    c, db = connector
    client = SimpleClient()
    await _run_ambiguous_send(c, db, client, "QTP-x", None)
    assert len(client.create_calls) == 1  # the order was sent exactly once


# --------------------------------------------------------------------------- #
# 14-19. Partial fills                                                         #
# --------------------------------------------------------------------------- #
def _partial_setup(connector, direction="BUY", filled=0.5, average=95.0, fees=0.1):
    c, db = connector
    kind_id = "SL-1" if direction == "BUY" else "TP-1"
    other_id = "TP-1" if direction == "BUY" else "SL-1"
    adapter = ScriptedAdapter(fetch_status_map={
        kind_id: {"status": "closed", "filled": filled, "average": average,
                  "fees": fees},
        other_id: {"status": "open", "filled": 0.0, "average": None, "fees": 0.0},
    })
    c.active_adapters["b1"] = adapter
    _open_trade(db, direction=direction, entry=100.0, qty=1.0, fees=0.5)
    return c, db, adapter


async def test_partial_fill_keeps_open(connector):
    c, db, _ = _partial_setup(connector)
    closed = await c.reconcile_positions()
    assert closed == []  # NEVER a full close on filled < quantity
    trade = db.get_active_positions("REAL")[0]
    assert trade["status"] == "OPEN"


async def test_residual_quantity(connector):
    c, db, _ = _partial_setup(connector, filled=0.35)
    await c.reconcile_positions()
    trade = db.get_active_positions("REAL")[0]
    assert trade["quantity"] == pytest.approx(0.65)
    assert trade["metadata"]["last_accounted_filled"] == pytest.approx(0.35)
    assert trade["metadata"]["partial_realized"] is True
    assert trade["metadata"]["protection_status"] == "NAKED"  # SL consumed


async def test_partial_pnl_buy(connector):
    c, db, _ = _partial_setup(connector, direction="BUY", filled=0.5,
                              average=95.0, fees=0.1)
    await c.reconcile_positions()
    trade = db.get_active_positions("REAL")[0]
    # gross (95-100)*0.5 = -2.5 ; net = -2.5 - 0.1 = -2.6
    assert trade["pnl"] == pytest.approx(-2.6)


async def test_partial_pnl_sell(connector):
    c, db, _ = _partial_setup(connector, direction="SELL", filled=0.5,
                              average=95.0, fees=0.1)
    await c.reconcile_positions()
    trade = db.get_active_positions("REAL")[0]
    # gross (100-95)*0.5 = 2.5 ; net = 2.5 - 0.1 = 2.4
    assert trade["pnl"] == pytest.approx(2.4)


async def test_partial_fees_accumulated_once(connector):
    c, db, _ = _partial_setup(connector, filled=0.5, fees=0.1)
    await c.reconcile_positions()
    trade = db.get_active_positions("REAL")[0]
    assert trade["fees"] == pytest.approx(0.5 + 0.1)  # entry + partial leg


async def test_idempotent_reconcile(connector):
    c, db, _ = _partial_setup(connector, filled=0.5, average=95.0, fees=0.1)
    await c.reconcile_positions()
    first = db.get_active_positions("REAL")[0]
    snapshot = (first["pnl"], first["fees"], first["quantity"])
    # Second reconciliation pass: same broker state → zero new delta.
    await c.reconcile_positions()
    second = db.get_active_positions("REAL")[0]
    assert (second["pnl"], second["fees"], second["quantity"]) == snapshot
    assert second["metadata"]["last_accounted_filled"] == pytest.approx(0.5)


# --------------------------------------------------------------------------- #
# 20. Only the sibling is cancelled after a protection fill                    #
# --------------------------------------------------------------------------- #
async def test_sibling_only_cancelled(connector):
    c, db = connector
    adapter = ScriptedAdapter(fetch_status_map={
        "SL-1": {"status": "closed", "filled": 1.0, "average": 95.0, "fees": 0.2},
        "TP-1": {"status": "open", "filled": 0.0, "average": None, "fees": 0.0},
    })
    c.active_adapters["b1"] = adapter
    _open_trade(db)
    closed = await c.reconcile_positions()
    assert len(closed) == 1
    assert adapter.cancelled == ["TP-1"]  # filled SL is never cancelled
    assert closed[0]["metadata"]["filled_protection"] == "sl"


# --------------------------------------------------------------------------- #
# 21. Authoritative close WITH a confirmed price                               #
# --------------------------------------------------------------------------- #
async def test_authoritative_close_with_price(connector):
    c, db = connector
    adapter = ScriptedAdapter(fetch_status_map={
        "TP-1": {"status": "closed", "filled": 1.0, "average": 110.0, "fees": 0.3},
        "SL-1": {"status": "open", "filled": 0.0, "average": None, "fees": 0.0},
    })
    c.active_adapters["b1"] = adapter
    _open_trade(db)
    closed = await c.reconcile_positions()
    assert len(closed) == 1
    assert closed[0]["exit_price"] == pytest.approx(110.0)
    assert closed[0]["pnl"] == pytest.approx((110.0 - 100.0) * 1.0 - 0.8)


# --------------------------------------------------------------------------- #
# 22. Authoritative close WITHOUT a price → CLOSED_PRICE_PENDING               #
# --------------------------------------------------------------------------- #
async def test_authoritative_close_without_price(connector):
    c, db = connector
    adapter = ScriptedAdapter(positions=[])
    adapter.positions_authoritative = True
    c.active_adapters["b1"] = adapter
    _open_trade(db, tp_id=None, sl_id=None)  # no protection orders to check
    closed = await c.reconcile_positions()
    assert len(closed) == 1
    t = closed[0]
    assert t["exit_price"] is None  # NEVER a fabricated price
    assert t["pnl"] == pytest.approx(0.0)  # PnL finalised later
    assert t["metadata"]["close_state"] == pnl_engine.CLOSED_PRICE_PENDING


# --------------------------------------------------------------------------- #
# 23. Emergency stop: per-position unit closes (spot honest)                   #
# --------------------------------------------------------------------------- #
async def test_emergency_stop_spot_unitary(connector):
    c, db = connector
    adapter = ScriptedAdapter(close_result={"success": True, "average": 101.0})
    adapter.positions_authoritative = False  # spot: [] proves nothing
    c.active_adapters["b1"] = adapter
    _open_trade(db, trade_id="R-A", symbol="btc_usdt")
    _open_trade(db, trade_id="R-B", symbol="eth_usdt")
    result = await c.emergency_close_all()
    assert result["total"] == 2
    assert result["closed_confirmed"] == 2
    assert all(p["status"] == "CLOSED_CONFIRMED" for p in result["positions"])
    assert [a for a in adapter.calls if a[0] == "close"] == \
        [("close", "BTC/USDT"), ("close", "ETH/USDT")]
    assert db.get_active_positions("REAL") == []


async def test_emergency_stop_failure_verdicts(connector):
    c, db = connector
    adapter = ScriptedAdapter(close_result={"success": False,
                                            "reason": "BROKER_CLOSE_ERROR: down"})
    c.active_adapters["b1"] = adapter
    _open_trade(db, tp_id=None, sl_id=None)  # no cancels → plain FAILED
    result = await c.emergency_close_all()
    assert result["positions"][0]["status"] == "FAILED"
    assert db.get_active_positions("REAL")[0]["status"] == "OPEN"


# --------------------------------------------------------------------------- #
# 24. Restart with an OPEN trade (recovery)                                    #
# --------------------------------------------------------------------------- #
async def test_restart_with_open_trade(tmp_path):
    db = DatabaseManager(str(tmp_path / "v33_restart.db"))
    # Pre-restart state: an OPEN REAL trade + its protections.
    _open_trade(db)

    # "Process restart": a brand-new connector instance (fresh memory).
    c2 = BrokerConnector(db_manager=db)
    assert len(c2.db.get_active_positions("REAL")) == 1  # state survived

    # Live protection → still OPEN after the first post-restart reconcile.
    adapter = ScriptedAdapter(fetch_status_map={
        "TP-1": {"status": "open", "filled": 0.0, "average": None, "fees": 0.0},
        "SL-1": {"status": "open", "filled": 0.0, "average": None, "fees": 0.0},
    })
    c2.active_adapters["b1"] = adapter
    assert await c2.reconcile_positions() == []
    trade = db.get_active_positions("REAL")[0]
    assert trade["metadata"]["protection_status"] == "OPEN"

    # Then the SL fills on the exchange → honest close with price.
    adapter.fetch_status_map["SL-1"] = {
        "status": "closed", "filled": 1.0, "average": 95.0, "fees": 0.2}
    closed = await c2.reconcile_positions()
    assert len(closed) == 1
    assert closed[0]["exit_price"] == pytest.approx(95.0)
    assert db.get_active_positions("REAL") == []


# --------------------------------------------------------------------------- #
# 25. Every mutation is protected                                              #
# --------------------------------------------------------------------------- #
def test_all_mutations_protected():
    from fastapi.routing import APIRoute

    intentional_exceptions = {"/api/login", "/api/logout"}
    protected, unguarded = [], []
    for route in idx.app.routes:
        if not isinstance(route, APIRoute):
            continue
        methods = set(getattr(route, "methods", None) or set())
        if not (methods & {"POST", "PUT", "PATCH", "DELETE"}):
            continue
        has_dep = any(
            getattr(d, "dependency", None) is idx.require_admin_dependency
            for d in route.dependencies)
        if has_dep:
            protected.append(route.path)
        elif route.path not in intentional_exceptions:
            unguarded.append(route.path)
    # The whole mutation surface (start/stop/arm/mode/settings/orders/…)
    assert len(protected) >= 20, f"too few protected routes: {protected}"
    assert unguarded == [], f"UNPROTECTED mutations: {unguarded}"


async def test_require_admin_direct_calls_still_work():
    """The public contract of require_admin is preserved for unit tests."""
    from fastapi import HTTPException
    # With ADMIN_API_KEY unset (test default) access stays open.
    await idx.require_admin(x_api_key="wrong")  # must not raise
    # With a key set, a wrong key must raise 401.
    old = idx.ADMIN_API_KEY
    idx.ADMIN_API_KEY = "unit-test-key-1234567890"
    try:
        with pytest.raises(HTTPException) as exc:
            await idx.require_admin(x_api_key="wrong")
        assert exc.value.status_code == 401
        await idx.require_admin(x_api_key="unit-test-key-1234567890")
    finally:
        idx.ADMIN_API_KEY = old


# --------------------------------------------------------------------------- #
# 26. Production fail-fast + readiness                                         #
# --------------------------------------------------------------------------- #
def test_production_fail_fast():
    # development/test: missing keys are allowed (open dev mode)
    assert production_config_errors("development", "", "") == []
    # production: missing keys → hard errors
    errs = production_config_errors("production", "", "")
    assert any("ADMIN_API_KEY" in e for e in errs)
    assert any("FERNET_KEY" in e for e in errs)
    # production: weak keys rejected
    good_fernet = Fernet.generate_key().decode()
    errs = production_config_errors("production", "qtp-admin-key-1", good_fernet)
    assert any("weak" in e for e in errs)
    # production: valid strong keys pass
    strong = "pr0d-s3cr3t-" + "x" * 24
    assert production_config_errors("production", strong, good_fernet) == []
    # assert_production_ready raises in production, not in dev
    with pytest.raises(ProductionConfigError):
        assert_production_ready("production", "", "")
    assert_production_ready("development", "", "")
    # weak-key heuristics
    assert is_weak_key("short") is True
    assert is_weak_key("aaaaaaaaaaaaaaaa") is True
    assert is_weak_key("qtp-admin-key-123456789") is True
    assert is_weak_key(strong) is False


def test_readyz_endpoints(monkeypatch):
    from fastapi.testclient import TestClient

    client = TestClient(idx.app)
    monkeypatch.setenv("APP_ENV", "development")
    # healthy dev config → ready
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["ready"] is True

    # invalid production config → 503
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    monkeypatch.delenv("FERNET_KEY", raising=False)
    r = client.get("/readyz")
    assert r.status_code == 503
    assert any("ADMIN_API_KEY" in p for p in r.json()["problems"])

    # DB unavailable → 503
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(idx.db_manager, "db_path", "/nonexistent_dir_xyz/x.db")
    r = client.get("/readyz")
    assert r.status_code == 503
    assert any(p.startswith("db_unavailable") for p in r.json()["problems"])


# --------------------------------------------------------------------------- #
# 27. Secret redaction in structured logs                                      #
# --------------------------------------------------------------------------- #
def test_secret_redaction_in_logs():
    fmt = JsonFormatter()

    def _record(message, **extra):
        rec = logging.LogRecord("t", logging.INFO, "f.py", 1, message, None, None)
        for k, v in extra.items():
            setattr(rec, k, v)
        return fmt.format(rec)

    out = _record("Broker connect api_key=SECRETVALUE123 and Bearer abcdef12345678")
    json.loads(out)  # must be valid JSON
    assert "SECRETVALUE123" not in out
    assert "abcdef12345678" not in out
    assert "***REDACTED***" in out

    out2 = _record("order sent", api_key="sup3r-s3cr3t-key-value",
                   x_api_token="tok_1234567890")
    payload2 = json.loads(out2)
    assert payload2["api_key"] == "***REDACTED***"
    assert payload2["x_api_token"] == "***REDACTED***"
    assert "sup3r-s3cr3t-key-value" not in out2

    # Non-sensitive fields are untouched.
    out3 = _record("scan done", symbol="btc_usdt", score=88)
    payload3 = json.loads(out3)
    assert payload3["symbol"] == "btc_usdt"
    assert payload3["score"] == 88


def test_api_key_constant_time_compare():
    import inspect
    src = inspect.getsource(api_key_matches)
    assert "compare_digest" in src  # timing-safe comparison
    assert api_key_matches("same-key-value-123", "same-key-value-123") is True
    assert api_key_matches("other-key-value-123", "same-key-value-123") is False
    assert api_key_matches("", "same-key-value-123") is False
    assert api_key_matches("same-key-value-123", "") is False
