"""v3.2 contract — protections cancellation, honest reconciliation, CCXT
fetch/cancel, per-exchange stop mapping, clientOrderId, PnL/fees, NAKED
alert, backstop rules, broker catalogue, version and docs.

All tests are offline (mocks only, no network)."""
from __future__ import annotations

import os
import time

import pytest

import api.index as idx
from api.engines.broker_adapters.ccxt_adapter import CCXTAdapter, _stop_order_args
from api.engines.broker_connector import BrokerConnector
from api.engines.db_manager import DatabaseManager
from api.engines import pnl_engine
from api.engines import protection_state


# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #
class SimpleClient:
    """Minimal CCXT-like client with order lookup support (offline)."""

    def __init__(self, fill=1.0, average=100.0, fee=0.25, has=None,
                 fail_types=None):
        self.markets = {}
        self.create_calls = []
        self.cancel_calls = []
        self.closed = False
        self.fill = fill
        self.average = average
        self.fee = fee
        self.has = has or {}
        self.fail_types = set(fail_types or set())
        self.orders = {}
        self.client_ids = {}
        self.open_orders = []
        self.closed_orders = []
        self.trades = []

    async def load_markets(self):
        return self.markets

    async def close(self):
        self.closed = True

    def seed(self, order_id, client_order_id=None, status="closed",
             filled=None, average=None, symbol="BTC/USDT"):
        order = {
            "id": order_id, "clientOrderId": client_order_id, "status": status,
            "filled": self.fill if filled is None else filled,
            "average": self.average if average is None else average,
            "fee": {"cost": self.fee, "currency": "USDT"},
            "symbol": symbol,
            "info": {"clientOrderId": client_order_id},
        }
        self.orders[order_id] = order
        if client_order_id:
            self.client_ids[client_order_id] = order_id
        (self.closed_orders if status == "closed" else self.open_orders).append(order)
        if order["filled"]:
            self.trades.append({
                "id": f"T-{order_id}", "order": order_id, "side": "buy",
                "amount": order["filled"], "price": order["average"],
                "fee": dict(order["fee"]),
                "timestamp": int(time.time() * 1000),
                "info": {"clientOrderId": client_order_id},
            })
        return order

    async def create_order(self, *args):
        self.create_calls.append(args)
        symbol, order_type, side, amount, price, params = (list(args) + [None] * 6)[:6]
        if order_type in self.fail_types:
            raise RuntimeError(f"failed {order_type}")
        params = params or {}
        client_order_id = params.get("clientOrderId")
        oid = f"{order_type}-{len(self.create_calls)}"
        order = {
            "id": oid, "clientOrderId": client_order_id, "status": "closed",
            "filled": self.fill, "average": self.average,
            "fee": {"cost": self.fee, "currency": "USDT"}, "symbol": symbol,
            "info": {"clientOrderId": client_order_id},
        }
        self.orders[oid] = order
        if client_order_id:
            self.client_ids[client_order_id] = oid
        self.closed_orders.append(order)
        if order["filled"]:
            self.trades.append({
                "id": f"T-{oid}", "order": oid, "side": side,
                "amount": order["filled"], "price": order["average"],
                "fee": dict(order["fee"]),
                "timestamp": int(time.time() * 1000),
                "info": {"clientOrderId": client_order_id},
            })
        return order

    async def fetch_order(self, order_id, symbol=None):
        if order_id in self.orders:
            return dict(self.orders[order_id])
        if order_id in self.client_ids:
            oid = self.client_ids[order_id]
            if oid in self.orders:
                return dict(self.orders[oid])
        raise RuntimeError(f"Order {order_id} not found")

    async def fetch_open_orders(self, symbol=None):
        return [dict(o) for o in self.open_orders]

    async def fetch_closed_orders(self, symbol=None, limit=50):
        return [dict(o) for o in self.closed_orders][:limit]

    async def fetch_trades(self, symbol=None, limit=50):
        return list(self.trades)[-limit:]

    async def cancel_order(self, order_id, symbol=None):
        self.cancel_calls.append(order_id)
        return {"id": order_id, "status": "canceled"}


class ScriptedAdapter:
    """Fake broker with a call log and scripted protection statuses."""

    exchange_id = "gate"
    positions_authoritative = False

    def __init__(self, close_result=None, fetch_status_map=None,
                 positions=None, cancel_result=True, cancel_raises=(),
                 execute_result=None):
        self.calls = []
        self.cancelled = []
        self.close_result = close_result or {"success": True, "average": 108.0}
        self.fetch_status_map = fetch_status_map or {}
        self.positions = positions
        self.cancel_result = cancel_result
        self.cancel_raises = set(cancel_raises)
        self.execute_result = execute_result or {"success": True, "filled": 1.0,
                                                 "average": 100.0, "fees": 0.5,
                                                 "tp_order_id": "TP-1",
                                                 "sl_order_id": "SL-1"}

    async def connect(self):
        return True

    async def close(self):
        pass

    async def get_balance(self, asset="USDT"):
        return 0.0

    async def get_positions(self):
        if self.positions is None:
            raise RuntimeError("positions unavailable")
        return list(self.positions)

    async def execute_order(self, symbol, side, quantity, sl=None, tp=None,
                            client_order_id=None):
        self.calls.append(("execute", symbol))
        return dict(self.execute_result)

    async def close_position(self, symbol, side, quantity):
        self.calls.append(("close", symbol))
        return dict(self.close_result)

    async def close_all_positions(self):
        return {"closed_positions": 0}

    async def cancel_order(self, order_id, symbol):
        self.calls.append(("cancel", order_id))
        if order_id in self.cancel_raises:
            raise RuntimeError(f"cancel failed for {order_id}")
        if self.cancel_result is False:
            return False
        self.cancelled.append(order_id)
        return True

    async def fetch_order_status(self, order_id, symbol):
        self.calls.append(("fetch_status", order_id))
        value = self.fetch_status_map.get(order_id)
        if isinstance(value, Exception):
            raise value
        return dict(value) if value is not None else None


class SpyNotifier:
    def __init__(self):
        self.events = []

    async def notify(self, event, data):
        self.events.append((event, data))


def _open_trade(db, trade_id="R-V32", symbol="btc_usdt", direction="BUY",
                entry=100.0, qty=1.0, fees=0.5, tp_id="TP-1", sl_id="SL-1",
                extra_meta=None, display_symbol=None):
    display = display_symbol or symbol.upper().replace("_", "/")
    meta = {
        "broker_id": "b1", "broker_symbol": display,
        "tp_order_id": tp_id, "sl_order_id": sl_id,
    }
    if extra_meta:
        meta.update(extra_meta)
    db.save_trade({
        "id": trade_id, "mode": "REAL", "symbol": symbol,
        "display_symbol": display, "direction": direction,
        "entry_price": entry, "quantity": qty, "sl": 95.0, "tp": 110.0,
        "fees": fees, "status": "OPEN", "pnl": 0.0, "metadata": meta,
    })


@pytest.fixture()
def connector(tmp_path):
    db = DatabaseManager(str(tmp_path / "v32.db"))
    return BrokerConnector(db_manager=db), db


# --------------------------------------------------------------------------- #
# 1. TP and SL cancelled before the hedge                                      #
# --------------------------------------------------------------------------- #
async def test_tp_and_sl_cancelled_before_hedge(connector):
    c, db = connector
    adapter = ScriptedAdapter()
    c.active_adapters["b1"] = adapter
    _open_trade(db)
    res = await c.close_position("btc_usdt")
    assert res["success"] is True
    actions = [a[0] for a in adapter.calls]
    assert actions.index("cancel") < actions.index("close")
    assert "TP-1" in adapter.cancelled and "SL-1" in adapter.cancelled
    assert adapter.calls.index(("cancel", "TP-1")) < \
        adapter.calls.index(("cancel", "SL-1")) < adapter.calls.index(("close", "BTC/USDT"))


# --------------------------------------------------------------------------- #
# 2. Cancel fails — the close still continues                                  #
# --------------------------------------------------------------------------- #
async def test_cancel_failure_close_continues(connector):
    c, db = connector
    adapter = ScriptedAdapter(cancel_raises={"TP-1", "SL-1"})
    c.active_adapters["b1"] = adapter
    _open_trade(db)
    res = await c.close_position("btc_usdt")
    assert res["success"] is True
    assert res["position"]["metadata"]["cancelled_protection"] == []
    assert db.get_active_positions("REAL") == []


# --------------------------------------------------------------------------- #
# 3. Authoritative reconciliation cancels the protections                      #
# --------------------------------------------------------------------------- #
async def test_reconcile_authoritative_cancels_protections(connector):
    c, db = connector
    adapter = ScriptedAdapter(positions=[])
    adapter.positions_authoritative = True
    c.active_adapters["b1"] = adapter
    _open_trade(db)
    closed = await c.reconcile_positions()
    assert len(closed) == 1
    meta = closed[0]["metadata"]
    assert meta["close_reason"] == "BROKER_RECONCILED_CLOSE"
    assert sorted(meta["cancelled_protection"]) == ["SL-1", "TP-1"]


# --------------------------------------------------------------------------- #
# 4. Flatten cancels the already-created TP                                    #
# --------------------------------------------------------------------------- #
async def test_flatten_cancels_tp(connector):
    c, db = connector
    adapter = CCXTAdapter("gate", "k", "s")
    client = SimpleClient(fail_types={"stop_loss"})  # TP ok, SL fails
    adapter.client = client
    c.active_adapters["b1"] = adapter
    signal = {"market_id": "btc_usdt", "direction": "BUY", "entry": 100.0,
              "sl": 95.0, "tp": 110.0, "strategy": "rsi"}
    res = await c.execute(signal, {"quantity": 1.0, "leverage": 1.0})
    assert res["success"] is False
    assert res["reason"] == "SL_TP_ATTACH_FAILED_FLATTENED"
    assert client.cancel_calls == [res["tp_order_id"]]  # TP cancelled before flatten
    last = client.create_calls[-1]
    assert last == ("BTC/USDT", "market", "sell", 1.0, None, {"reduceOnly": True})


# --------------------------------------------------------------------------- #
# 5. TP fill on spot closes the trade HONESTLY (with the real price)           #
# --------------------------------------------------------------------------- #
async def test_tp_fill_spot_closes_honestly(connector):
    c, db = connector
    adapter = ScriptedAdapter(fetch_status_map={
        "TP-1": {"status": "closed", "filled": 1.0, "average": 110.0, "fees": 0.3},
        "SL-1": {"status": "open", "filled": 0.0, "average": None, "fees": 0.0},
    })
    c.active_adapters["b1"] = adapter
    _open_trade(db)  # entry 100, qty 1, entry fees 0.5
    closed = await c.reconcile_positions()
    assert len(closed) == 1
    t = closed[0]
    assert t["status"] == "CLOSED"
    assert t["exit_price"] == pytest.approx(110.0)
    # gross 10.0, total fees 0.5 + 0.3 = 0.8 → net 9.2
    assert t["pnl"] == pytest.approx(9.2)
    assert t["fees"] == pytest.approx(0.8)
    assert t["metadata"]["filled_protection"] == "tp"


# --------------------------------------------------------------------------- #
# 6. The sibling protection is cancelled after a fill                          #
# --------------------------------------------------------------------------- #
async def test_sibling_sl_cancelled_after_tp_fill(connector):
    c, db = connector
    adapter = ScriptedAdapter(fetch_status_map={
        "TP-1": {"status": "closed", "filled": 1.0, "average": 110.0, "fees": 0.3},
        "SL-1": {"status": "open", "filled": 0.0, "average": None, "fees": 0.0},
    })
    c.active_adapters["b1"] = adapter
    _open_trade(db)
    closed = await c.reconcile_positions()
    assert len(closed) == 1
    # Only the SL (sibling) is cancelled — the filled TP is excluded.
    assert adapter.cancelled == ["SL-1"]
    assert closed[0]["metadata"]["sibling_cancel_status"] == "CANCELED"


# --------------------------------------------------------------------------- #
# 7. open / None / error → NOTHING is closed                                   #
# --------------------------------------------------------------------------- #
async def test_open_none_error_never_close(connector):
    c, db = connector

    # a) protection still OPEN
    adapter = ScriptedAdapter(fetch_status_map={
        "TP-1": {"status": "open", "filled": 0.0, "average": None, "fees": 0.0},
        "SL-1": {"status": "open", "filled": 0.0, "average": None, "fees": 0.0},
    })
    c.active_adapters["b1"] = adapter
    _open_trade(db, trade_id="R-V32A")
    assert await c.reconcile_positions() == []
    trade_a = db.get_active_positions("REAL")[0]
    assert trade_a["id"] == "R-V32A"
    assert trade_a["metadata"]["protection_status"] == "OPEN"

    # b) status fetch returns None (error) → no close, error counted
    adapter2 = ScriptedAdapter()  # fetch_status_map empty → None
    c.active_adapters["b1"] = adapter2
    _open_trade(db, trade_id="R-V32B")
    assert await c.reconcile_positions() == []
    by_id = {p["id"]: p for p in db.get_active_positions("REAL")}
    assert by_id["R-V32B"]["metadata"]["protection_error_count"] == 1
    assert by_id["R-V32B"]["status"] == "OPEN"

    # c) no broker at all → no close
    c2, db2 = connector
    c2.active_adapters.clear()
    _open_trade(db2, trade_id="R-V32C")
    assert await c2.reconcile_positions() == []
    by_id2 = {p["id"]: p for p in db2.get_active_positions("REAL")}
    assert by_id2["R-V32C"]["status"] == "OPEN"


# --------------------------------------------------------------------------- #
# 8-11. Per-exchange stop mapping (v3.1 fail-close contract preserved)         #
# --------------------------------------------------------------------------- #
def test_stop_mapping_binance():
    otype, params = _stop_order_args("binance", 95.0, "sell")
    assert otype == "STOP_MARKET"
    assert params == {"stopPrice": 95.0, "reduceOnly": True}


def test_stop_mapping_bybit_sell():
    otype, params = _stop_order_args("bybit", 95.0, "sell")
    assert otype == "market"
    assert params == {"triggerPrice": 95.0, "reduceOnly": True, "triggerDirection": 2}


def test_stop_mapping_bybit_buy():
    otype, params = _stop_order_args("bybit", 105.0, "buy")
    assert otype == "market"
    assert params == {"triggerPrice": 105.0, "reduceOnly": True, "triggerDirection": 1}


def test_stop_mapping_okx():
    otype, params = _stop_order_args("okx", 95.0, "sell")
    assert otype == "market"
    assert params == {"stopLossPrice": 95.0, "reduceOnly": True}


def test_stop_mapping_default():
    otype, params = _stop_order_args("gate", 95.0, "sell")
    assert otype == "stop_loss"
    assert params == {"stopPrice": 95.0, "triggerPrice": 95.0, "reduceOnly": True}


# --------------------------------------------------------------------------- #
# 12. clientOrderId is transmitted to the exchange                             #
# --------------------------------------------------------------------------- #
async def test_client_order_id_transmitted():
    adapter = CCXTAdapter("gate", "k", "s")
    client = SimpleClient()
    adapter.client = client
    res = await adapter.execute_order("BTC/USDT", "buy", 1.0, sl=95.0, tp=110.0,
                                      client_order_id="QTP-123-abc")
    assert res["success"] is True
    market_call = client.create_calls[0]
    assert market_call[5].get("clientOrderId") == "QTP-123-abc"


# --------------------------------------------------------------------------- #
# 13. Order recovered after a send exception (no second order)                 #
# --------------------------------------------------------------------------- #
async def test_order_recovered_after_exception(connector):
    import unittest.mock as mock

    import api.engines.broker_connector as bc_module

    c, db = connector
    adapter = CCXTAdapter("gate", "k", "s")
    client = SimpleClient()
    expected_id = "QTP-1700000000123-abcdef"
    # The order DID land on the exchange (recovered by lookup):
    client.seed("ORD-77", client_order_id=expected_id, status="closed",
                filled=1.0, average=101.0)

    async def flaky_create(*args):
        client.create_calls.append(args)
        raise RuntimeError("timeout after create")

    client.create_order = flaky_create
    adapter.client = client
    c.active_adapters["b1"] = adapter

    class FakeUUID:
        hex = "abcdef0123456789abcdef0123456789"

    with mock.patch.object(bc_module.time, "time", return_value=1700000000.123), \
         mock.patch.object(bc_module.uuid, "uuid4", return_value=FakeUUID()):
        res = await c.execute(
            {"market_id": "btc_usdt", "direction": "BUY", "entry": 100.0,
             "sl": 95.0, "tp": 110.0, "strategy": "rsi"},
            {"quantity": 1.0, "leverage": 1.0})
    assert res["success"] is True
    assert res.get("recovered_after_error") is True
    assert len(client.create_calls) == 1  # exactly ONE create_order attempt
    positions = db.get_active_positions("REAL")
    assert len(positions) == 1
    assert positions[0]["metadata"]["client_order_id"] == expected_id
    intent = db.get_order_intent(expected_id)
    assert intent is not None and intent["status"] == "CONFIRMED"


# --------------------------------------------------------------------------- #
# 14/15. PnL & fees BUY / SELL                                                  #
# --------------------------------------------------------------------------- #
def test_pnl_fees_buy():
    gross = pnl_engine.gross_pnl("BUY", 100.0, 110.0, 2.0)
    assert gross == pytest.approx(20.0)
    assert pnl_engine.net_pnl(gross, 1.5) == pytest.approx(18.5)


def test_pnl_fees_sell():
    gross = pnl_engine.gross_pnl("SELL", 100.0, 90.0, 3.0)
    assert gross == pytest.approx(30.0)
    assert pnl_engine.net_pnl(gross, 2.0) == pytest.approx(28.0)
    with pytest.raises(ValueError):
        pnl_engine.gross_pnl("HOLD", 1, 2, 3)


# --------------------------------------------------------------------------- #
# 16. NAKED alert is notified                                                   #
# --------------------------------------------------------------------------- #
async def test_naked_alert(connector):
    c, db = connector
    spy = SpyNotifier()
    c.notifier = spy
    adapter = CCXTAdapter("gate", "k", "s")
    client = SimpleClient()
    real_create = client.create_order
    state = {"n": 0}

    async def create_then_fail(*args):
        state["n"] += 1
        if state["n"] == 1:
            return await real_create(*args)  # market fill ok
        raise RuntimeError("exchange down")

    client.create_order = create_then_fail
    adapter.client = client
    c.active_adapters["b1"] = adapter
    res = await c.execute(
        {"market_id": "btc_usdt", "direction": "BUY", "entry": 100.0,
         "sl": 95.0, "tp": 110.0, "strategy": "rsi"},
        {"quantity": 1.0, "leverage": 1.0})
    assert res["success"] is False
    assert res["reason"] == "SL_TP_ATTACH_FAILED_NAKED"
    events = [e for e, _ in spy.events]
    assert "SL_TP_ATTACH_FAILED_NAKED" in events


# --------------------------------------------------------------------------- #
# 17. notifier stays optional (None)                                            #
# --------------------------------------------------------------------------- #
async def test_notifier_none(connector):
    c, db = connector
    assert c.notifier is None
    adapter = CCXTAdapter("gate", "k", "s")
    client = SimpleClient()
    real_create = client.create_order
    state = {"n": 0}

    async def create_then_fail(*args):
        state["n"] += 1
        if state["n"] == 1:
            return await real_create(*args)
        raise RuntimeError("exchange down")

    client.create_order = create_then_fail
    adapter.client = client
    c.active_adapters["b1"] = adapter
    # Must not raise even though notifier is None.
    res = await c.execute(
        {"market_id": "btc_usdt", "direction": "BUY", "entry": 100.0,
         "sl": 95.0, "tp": 110.0, "strategy": "rsi"},
        {"quantity": 1.0, "leverage": 1.0})
    assert res["reason"] == "SL_TP_ATTACH_FAILED_NAKED"


# --------------------------------------------------------------------------- #
# 18. Backstop must NOT fire while the protection is confirmed alive           #
# --------------------------------------------------------------------------- #
def test_backstop_with_live_protection():
    meta = {"protection_status": "OPEN", "protection_checked_at": time.time(),
            "sl_order_id": "SL-1", "tp_order_id": "TP-1"}
    assert protection_state.protection_liveness(meta) == "ALIVE"
    assert protection_state.backstop_allowed(meta) is False


def test_backstop_stale_open_allowed():
    meta = {"protection_status": "OPEN",
            "protection_checked_at": time.time() - 3600,
            "sl_order_id": "SL-1", "tp_order_id": "TP-1"}
    assert protection_state.backstop_allowed(meta) is True  # ID alone ≠ alive


# --------------------------------------------------------------------------- #
# 19. Backstop fires when sl_tp_failed                                          #
# --------------------------------------------------------------------------- #
def test_backstop_sl_tp_failed():
    meta = {"sl_tp_failed": True, "sl_order_id": "SL-1"}
    assert protection_state.protection_liveness(meta) == "NAKED"
    assert protection_state.backstop_allowed(meta) is True


# --------------------------------------------------------------------------- #
# 20. Gate + eur_usd → UNSUPPORTED_SYMBOL (data provider ≠ broker)             #
# --------------------------------------------------------------------------- #
async def test_gate_eur_usd_unsupported_symbol(connector):
    c, db = connector
    adapter = ScriptedAdapter()
    adapter.exchange_id = "gate"
    c.active_adapters["b1"] = adapter
    res = await c.execute(
        {"market_id": "eur_usd", "direction": "BUY", "entry": 1.1,
         "sl": 1.09, "tp": 1.12, "strategy": "manual"},
        {"quantity": 1000.0, "leverage": 1.0})
    assert res["success"] is False
    assert res["reason"] == "UNSUPPORTED_SYMBOL: eur_usd"
    assert adapter.calls == []  # no order ever sent


# --------------------------------------------------------------------------- #
# 21. Version & documentation                                                  #
# --------------------------------------------------------------------------- #
def test_version_and_documentation():
    assert idx.app.version == "3.3.0"
    for doc in ("docs/AUDIT_V32.md", "docs/AUDIT_V33.md",
                "docs/RUNBOOK_PRODUCTION.md", "docs/TESTNET_MATRIX.md"):
        assert os.path.exists(doc), doc
    readme = open("README.md", encoding="utf-8").read().lower()
    assert "3.3.0" in readme
    assert "real is experimental" in readme
    assert "no profitability guarantee" in readme or "aucune garantie" in readme
