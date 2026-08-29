"""v3.1 contract — P0 REAL: fail-close SL/TP, honest spot reconciliation,
filled/fees persistence, real CCXT sandbox, unit close, ARM-gated execute.

All tests are offline (mocks only, no network)."""
from __future__ import annotations

import os

import pytest
from unittest.mock import AsyncMock

import api.index as idx
from api.engines.broker_adapters.ccxt_adapter import CCXTAdapter
from api.engines.broker_connector import BrokerConnector
from api.engines.db_manager import DatabaseManager


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
class FakeClient:
    """Minimal CCXT-like client for offline execution tests."""

    def __init__(self, fill=1.0, average=100.0, fee=0.25, has=None):
        self.markets = {"BTC/USDT": {}}
        self.calls = []
        self.closed = False
        self.fail_types = set()
        self.fill = fill
        self.average = average
        self.fee = fee
        self.has = has or {}

    async def load_markets(self):
        return self.markets

    async def close(self):
        self.closed = True

    async def create_order(self, *args):
        self.calls.append(args)
        order_type = args[1]
        if order_type in self.fail_types:
            raise RuntimeError(f"failed {order_type}")
        return {
            "id": f"{order_type}-1",
            "status": "closed",
            "filled": self.fill,
            "average": self.average,
            "fee": {"cost": self.fee, "currency": "USDT"},
        }

    async def fetch_positions(self):
        return []


class SpotAdapter:
    """Non-authoritative fake broker (spot): [] is not evidence of close."""
    exchange_id = "gate"
    positions_authoritative = False

    def __init__(self, execute_result=None, close_result=None):
        self._execute_result = execute_result or {"success": True}
        self._close_result = close_result or {"success": True}

    async def connect(self):
        return True

    async def close(self):
        pass

    async def get_balance(self, asset="USDT"):
        return 0.0

    async def get_positions(self):
        return []

    async def execute_order(self, symbol, side, quantity, sl=None, tp=None):
        return dict(self._execute_result)

    async def close_position(self, symbol, side, quantity):
        return dict(self._close_result)

    async def close_all_positions(self):
        return {"closed_positions": 0}


def _open_real_trade(db, trade_id="R-V31", broker_id="b1", symbol="btc_usdt"):
    db.save_trade({
        "id": trade_id, "mode": "REAL", "symbol": symbol,
        "display_symbol": "BTC/USDT", "direction": "BUY",
        "entry_price": 100.0, "quantity": 1.0, "sl": 95.0, "tp": 110.0,
        "status": "OPEN", "pnl": 0.0,
        "metadata": {"broker_id": broker_id, "broker_symbol": "BTC/USDT"},
    })


@pytest.fixture()
def connector(tmp_path):
    db = DatabaseManager(str(tmp_path / "v31.db"))
    return BrokerConnector(db_manager=db), db


SIGNAL = {"market_id": "btc_usdt", "direction": "BUY", "entry": 100.0,
          "sl": 95.0, "tp": 110.0, "strategy": "rsi"}
RISK = {"quantity": 1.0, "leverage": 1.0}


# --------------------------------------------------------------------------- #
# 1. Protection fail → flatten → nothing persisted OPEN                        #
# --------------------------------------------------------------------------- #
async def test_protection_failure_flattens_and_never_persists_open(connector):
    c, db = connector
    adapter = CCXTAdapter("gate", "k", "s")
    adapter.client = FakeClient()
    adapter.client.fail_types.add("limit")  # TP attach fails
    c.active_adapters["b1"] = adapter

    res = await c.execute(SIGNAL, RISK)
    assert res["success"] is False
    assert res["reason"] == "SL_TP_ATTACH_FAILED_FLATTENED"
    assert res["flattened"] is True
    # The flatten order is a reduce-only market hedge on the filled quantity
    flatten = adapter.client.calls[-1]
    assert flatten == ("BTC/USDT", "market", "sell", 1.0, None, {"reduceOnly": True})
    # Nothing OPEN in DB
    assert db.get_active_positions("REAL") == []


# --------------------------------------------------------------------------- #
# 2. NAKED → OPEN persisted with sl_tp_failed                                  #
# --------------------------------------------------------------------------- #
async def test_naked_position_is_persisted_open_with_flag(connector):
    c, db = connector
    adapter = CCXTAdapter("gate", "k", "s")
    client = FakeClient()

    original = client.create_order
    state = {"n": 0}

    async def create_order(*args):
        state["n"] += 1
        if state["n"] == 1:
            return await original(*args)  # market fill OK
        raise RuntimeError("exchange down")  # TP attach AND flatten fail

    client.create_order = create_order
    adapter.client = client
    c.active_adapters["b1"] = adapter

    res = await c.execute(SIGNAL, RISK)
    assert res["success"] is False
    assert res["reason"] == "SL_TP_ATTACH_FAILED_NAKED"
    assert res["flattened"] is False
    open_positions = db.get_active_positions("REAL")
    assert len(open_positions) == 1
    assert open_positions[0]["metadata"]["sl_tp_failed"] is True


# --------------------------------------------------------------------------- #
# 3/4. Reconcile: non-authoritative [] keeps OPEN; authoritative [] closes     #
# --------------------------------------------------------------------------- #
async def test_reconcile_non_authoritative_empty_keeps_open(connector):
    c, db = connector
    c.active_adapters["b1"] = SpotAdapter()
    _open_real_trade(db)
    assert await c.reconcile_positions() == []
    assert db.get_active_positions("REAL")[0]["id"] == "R-V31"


async def test_reconcile_authoritative_empty_closes(connector):
    c, db = connector
    adapter = SpotAdapter()
    adapter.positions_authoritative = True  # derivatives-like broker
    c.active_adapters["b1"] = adapter
    _open_real_trade(db)
    closed = await c.reconcile_positions()
    assert len(closed) == 1
    assert closed[0]["metadata"]["close_reason"] == "BROKER_RECONCILED_CLOSE"
    assert db.get_active_positions("REAL") == []


def test_ccxt_positions_authoritative_flag():
    adapter = CCXTAdapter("gate", "k", "s")
    assert adapter.positions_authoritative is False  # disconnected
    adapter.client = FakeClient(has={"fetchPositions": False})
    assert adapter.positions_authoritative is False  # spot-only
    adapter.client = FakeClient(has={"fetchPositions": True})
    assert adapter.positions_authoritative is True


# --------------------------------------------------------------------------- #
# 5. execute persists real filled quantity and broker fees                     #
# --------------------------------------------------------------------------- #
async def test_execute_persists_filled_and_fees(connector):
    c, db = connector
    adapter = CCXTAdapter("gate", "k", "s")
    adapter.client = FakeClient(fill=0.8, average=101.5, fee=0.42)
    c.active_adapters["b1"] = adapter

    res = await c.execute(SIGNAL, RISK)
    assert res["success"] is True
    position = db.get_active_positions("REAL")[0]
    assert position["quantity"] == pytest.approx(0.8)      # filled != requested
    assert position["entry_price"] == pytest.approx(101.5)
    assert position["fees"] == pytest.approx(0.42)          # fees > 0
    assert position["metadata"]["requested_quantity"] == 1.0


# --------------------------------------------------------------------------- #
# 6. Sandbox: setter called before load_markets; missing setter → refuse       #
# --------------------------------------------------------------------------- #
async def test_sandbox_setter_called_and_missing_setter_refuses(monkeypatch):
    import api.engines.broker_adapters.ccxt_adapter as module

    order = []

    class SandboxClient(FakeClient):
        def set_sandbox_mode(self, enabled):
            order.append(("sandbox", enabled))

        async def load_markets(self):
            order.append(("load_markets", None))
            return self.markets

    client = SandboxClient()
    monkeypatch.setattr(module.ccxt, "sbx31", lambda config: client, raising=False)
    adapter = CCXTAdapter("sbx31", "k", "s", sandbox=True)
    assert await adapter.connect() is True
    assert order[0] == ("sandbox", True)
    assert order[1][0] == "load_markets"

    class NoSetterClient(FakeClient):
        set_sandbox_mode = None  # not callable

    no_setter = NoSetterClient()
    monkeypatch.setattr(module.ccxt, "nosbx31", lambda config: no_setter, raising=False)
    strict = CCXTAdapter("nosbx31", "k", "s", sandbox=True)
    assert await strict.connect() is False
    assert strict.client is None
    assert no_setter.closed is True


# --------------------------------------------------------------------------- #
# 7. close_position REAL: happy path + adapter failure keeps DB OPEN           #
# --------------------------------------------------------------------------- #
async def test_close_position_real_happy_path(connector):
    c, db = connector
    c.active_adapters["b1"] = SpotAdapter(
        close_result={"success": True, "broker_order_id": "CL-1", "average": 108.0})
    _open_real_trade(db)
    res = await c.close_position("btc_usdt")
    assert res["success"] is True
    assert db.get_active_positions("REAL") == []
    history = db.get_history(mode="REAL", limit=5)
    assert history[0]["metadata"]["close_reason"] == "MANUAL_CLOSE"


async def test_close_position_real_adapter_failure_keeps_db_open(connector):
    c, db = connector
    c.active_adapters["b1"] = SpotAdapter(
        close_result={"success": False, "reason": "BROKER_CLOSE_ERROR: down"})
    _open_real_trade(db)
    res = await c.close_position("btc_usdt")
    assert res["success"] is False
    # Fail-honest: no broker confirmation → still OPEN in DB
    assert db.get_active_positions("REAL")[0]["id"] == "R-V31"

    # No open position at all → honest failure too
    assert (await c.close_position("__ghost__"))["success"] is False


# --------------------------------------------------------------------------- #
# 8. execute-signal requires START + ARM                                       #
# --------------------------------------------------------------------------- #
async def test_execute_signal_requires_start_and_arm():
    old_running = idx.bot_state.get("is_running")
    old_armed = idx.bot_state.get("armed")
    try:
        idx.bot_state.update(is_running=False, armed=False)
        res = await idx._execute_signal_for_market("btc_usdt")
        assert res == {"success": False, "reason": "SYSTEM_NOT_ARMED"}

        idx.bot_state.update(is_running=True, armed=False)
        res = await idx._execute_signal_for_market("btc_usdt")
        assert res["reason"] == "SYSTEM_NOT_ARMED"

        idx.bot_state.update(is_running=False, armed=True)
        res = await idx._execute_signal_for_market("btc_usdt")
        assert res["reason"] == "SYSTEM_NOT_ARMED"
    finally:
        idx.bot_state.update(is_running=old_running, armed=old_armed)


# --------------------------------------------------------------------------- #
# 9/10. Version + docs                                                         #
# --------------------------------------------------------------------------- #
def test_app_version_is_3_1_0():
    assert idx.app.version == "3.2.0"


def test_v31_docs_exist():
    assert os.path.exists("docs/AUDIT_V31.md")
    assert os.path.exists("docs/AGENT_PROMPT_APPLY_V31.md")
    audit = open("docs/AUDIT_V31.md", encoding="utf-8").read()
    for token in ("SL_TP_ATTACH_FAILED_FLATTENED", "SL_TP_ATTACH_FAILED_NAKED",
                  "positions_authoritative", "set_sandbox_mode",
                  "SYSTEM_NOT_ARMED"):
        assert token in audit, token
