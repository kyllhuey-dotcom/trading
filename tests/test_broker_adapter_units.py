"""Offline unit tests for every concrete CCXT/PrimeXBT adapter operation."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.engines.broker_adapters.ccxt_adapter import CCXTAdapter
from api.engines.broker_adapters.primexbt_adapter import PrimeXBTAdapter


class FakeClient:
    def __init__(self):
        self.markets = {
            "BTC/USDT": {
                "precision": {"amount": 0.001, "price": 0.01},
                "limits": {"cost": {"min": 5}},
            }
        }
        self.calls = []
        self.closed = False
        self.positions = []
        self.open_orders = []
        self.fail_types = set()
        self.fail_cancel_ids = set()
        self.has = {}
        self.sandbox_enabled = False

    def set_sandbox_mode(self, enabled):
        self.sandbox_enabled = bool(enabled)

    async def load_markets(self):
        return self.markets

    async def close(self):
        self.closed = True

    async def fetch_balance(self):
        return {"total": {"USDT": "123.45"}}

    async def fetch_positions(self):
        return list(self.positions)

    async def create_order(self, *args):
        self.calls.append(args)
        order_type = args[1]
        symbol = args[0]
        if order_type in self.fail_types or symbol in self.fail_types:
            raise RuntimeError(f"failed {order_type}")
        return {
            "id": f"{order_type}-1",
            "status": "closed",
            "filled": args[3],
            "average": 100.0,
            "fee": {"cost": 0.12, "currency": "USDT"},
        }

    async def fetch_open_orders(self):
        return list(self.open_orders)

    async def cancel_order(self, order_id, symbol=None):
        if order_id in self.fail_cancel_ids:
            raise RuntimeError("cancel failed")
        self.calls.append(("cancel", order_id, symbol))


async def test_connect_success_close_and_constraints(monkeypatch):
    client = FakeClient()
    import api.engines.broker_adapters.ccxt_adapter as module

    monkeypatch.setattr(module.ccxt, "fake", lambda config: client, raising=False)
    adapter = CCXTAdapter("fake", "key", "secret", "passphrase")
    assert await adapter.connect() is True
    assert adapter.client is client
    assert adapter.get_market_constraints("BTC/USDT") == {
        "lot_size": 0.001,
        "tick_size": 0.01,
        "min_notional": 5.0,
    }
    await adapter.close()
    assert client.closed is True
    assert adapter.client is None
    assert adapter.get_market_constraints("BTC/USDT")["lot_size"] is None


async def test_connect_rejects_missing_credentials_and_handles_failures(monkeypatch):
    assert await CCXTAdapter("fake", None, None).connect() is False

    class BrokenClient(FakeClient):
        async def load_markets(self):
            raise RuntimeError("authentication failed")

        async def close(self):
            self.closed = True
            raise RuntimeError("close failed too")

    client = BrokenClient()
    import api.engines.broker_adapters.ccxt_adapter as module

    monkeypatch.setattr(module.ccxt, "broken", lambda config: client, raising=False)
    adapter = CCXTAdapter("broken", "key", "secret")
    assert await adapter.connect() is False
    assert client.closed is True
    assert adapter.client is None

    unknown = CCXTAdapter("not_a_real_exchange", "key", "secret")
    assert await unknown.connect() is False


async def test_balance_and_positions_success_disconnected_and_error():
    adapter = CCXTAdapter("fake", "key", "secret")
    assert await adapter.get_balance() == 0.0
    assert await adapter.get_positions() == []

    client = FakeClient()
    client.positions = [{"symbol": "BTC/USDT", "contracts": 1}]
    adapter.client = client
    assert await adapter.get_balance() == pytest.approx(123.45)
    assert await adapter.get_positions() == client.positions

    client.fetch_balance = AsyncMock(side_effect=RuntimeError("down"))
    client.fetch_positions = AsyncMock(side_effect=RuntimeError("down"))
    assert await adapter.get_balance() == 0.0
    assert await adapter.get_positions() == []

    adapter.client = SimpleNamespace()
    assert await adapter.get_positions() == []


async def test_execute_order_validates_inputs_and_creates_reduce_only_protection():
    adapter = CCXTAdapter("fake", "key", "secret")
    assert (await adapter.execute_order("BTC/USDT", "buy", 1))["reason"] == "BROKER_DISCONNECTED"

    client = FakeClient()
    adapter.client = client
    assert (await adapter.execute_order("BTC/USDT", "hold", 1))["reason"] == "INVALID_SIDE"
    for quantity in (0, -1, float("nan"), "not-a-number"):
        assert (await adapter.execute_order("BTC/USDT", "buy", quantity))["reason"] == "INVALID_QUANTITY"

    result = await adapter.execute_order("BTC/USDT", "BUY", 2, sl=95, tp=110)
    assert result["success"] is True
    assert result["tp_order_id"] == "limit-1"
    assert result["sl_order_id"] == "stop_loss-1"
    # v3.1 P0-1: honest fill accounting exposed to the connector
    assert result["filled"] == 2.0
    assert result["average"] == 100.0
    assert result["fees"] == 0.12
    market, take_profit, stop_loss = client.calls
    assert market == ("BTC/USDT", "market", "buy", 2.0)
    assert take_profit[-1] == {"reduceOnly": True}
    assert stop_loss[-1] == {
        "stopPrice": 95.0,
        "triggerPrice": 95.0,
        "reduceOnly": True,
    }


async def test_execute_order_reports_primary_and_protection_failures():
    adapter = CCXTAdapter("fake", "key", "secret")
    client = FakeClient()
    adapter.client = client
    client.fail_types.add("limit")
    # v3.1 P0-1 fail-close: a protection failure is NO LONGER a success —
    # the position is flattened with a reduce-only market hedge.
    result = await adapter.execute_order("BTC/USDT", "sell", 1, sl=105, tp=90)
    assert result["success"] is False
    assert result["reason"] == "SL_TP_ATTACH_FAILED_FLATTENED"
    assert result["flattened"] is True
    assert "failed limit" in result["sl_tp_warning"]
    # The flatten order is a market hedge (buy vs the original sell),
    # reduce-only, on the FILLED quantity.
    flatten = client.calls[-1]
    assert flatten == ("BTC/USDT", "market", "buy", 1.0, None, {"reduceOnly": True})

    client.fail_types = {"market"}
    result = await adapter.execute_order("BTC/USDT", "buy", 1)
    assert result["success"] is False
    assert "BROKER_EXECUTION_ERROR" in result["reason"]


async def test_execute_order_naked_when_flatten_also_fails():
    adapter = CCXTAdapter("fake", "key", "secret")
    client = FakeClient()
    adapter.client = client

    original_create = client.create_order
    state = {"n": 0}

    async def create_order(*args):
        # 1st call: market fill OK; 2nd: TP limit fails; 3rd: flatten fails
        state["n"] += 1
        if state["n"] == 1:
            return await original_create(*args)
        raise RuntimeError(f"boom {args[1]}")

    client.create_order = create_order
    result = await adapter.execute_order("BTC/USDT", "buy", 1, sl=95, tp=110)
    assert result["success"] is False
    assert result["reason"] == "SL_TP_ATTACH_FAILED_NAKED"
    assert result["flattened"] is False


async def test_execute_order_invalid_fill():
    adapter = CCXTAdapter("fake", "key", "secret")

    class ZeroFillClient(FakeClient):
        async def create_order(self, *args):
            # A negative/absurd fill must never fall back to the requested
            # quantity — that would attach protection on a phantom position.
            return {"id": "m-1", "status": "closed", "filled": -1, "average": None}

    adapter.client = ZeroFillClient()
    result = await adapter.execute_order("BTC/USDT", "buy", 1, sl=95, tp=110)
    assert result["success"] is False
    assert result["reason"] == "INVALID_FILL"


async def test_close_position_disconnected_invalid_and_happy_path():
    adapter = CCXTAdapter("fake", "key", "secret")
    # Disconnected
    res = await adapter.close_position("BTC/USDT", "buy", 1)
    assert res["success"] is False and res["reason"] == "BROKER_DISCONNECTED"

    client = FakeClient()
    adapter.client = client
    # Invalid inputs
    assert (await adapter.close_position("BTC/USDT", "hold", 1))["reason"] == "INVALID_SIDE"
    for quantity in (0, -3, float("nan"), "oops"):
        res = await adapter.close_position("BTC/USDT", "buy", quantity)
        assert res["reason"] == "INVALID_QUANTITY"

    # Happy path: market reduce-only hedge (sell closes a long)
    res = await adapter.close_position("BTC/USDT", "BUY", 2)
    assert res["success"] is True
    assert client.calls[-1] == ("BTC/USDT", "market", "sell", 2.0, None, {"reduceOnly": True})

    # Broker error → honest failure
    client.fail_types.add("market")
    res = await adapter.close_position("BTC/USDT", "sell", 1)
    assert res["success"] is False
    assert "BROKER_CLOSE_ERROR" in res["reason"]


async def test_sandbox_setter_called_before_load_markets(monkeypatch):
    import api.engines.broker_adapters.ccxt_adapter as module

    class SandboxClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.order = []

        def set_sandbox_mode(self, enabled):
            self.order.append("sandbox")
            self.sandbox_enabled = bool(enabled)

        async def load_markets(self):
            self.order.append("load_markets")
            return self.markets

    client = SandboxClient()
    monkeypatch.setattr(module.ccxt, "sbx", lambda config: client, raising=False)
    adapter = CCXTAdapter("sbx", "key", "secret", sandbox=True)
    assert await adapter.connect() is True
    assert client.order == ["sandbox", "load_markets"]
    assert client.sandbox_enabled is True


async def test_sandbox_without_setter_refuses_connect(monkeypatch):
    import api.engines.broker_adapters.ccxt_adapter as module

    class NoSandboxClient(FakeClient):
        set_sandbox_mode = None  # not callable

    client = NoSandboxClient()
    monkeypatch.setattr(module.ccxt, "nosbx", lambda config: client, raising=False)
    adapter = CCXTAdapter("nosbx", "key", "secret", sandbox=True)
    assert await adapter.connect() is False
    assert adapter.client is None
    assert client.closed is True


async def test_close_all_positions_uses_correct_ccxt_signature_and_continues():
    adapter = CCXTAdapter("fake", "key", "secret")
    assert await adapter.close_all_positions() == {
        "closed_positions": 0,
        "cancelled_orders": 0,
        "errors": [],
    }

    client = FakeClient()
    client.positions = [
        {"symbol": "NONE", "side": "long", "contracts": None},
        {"symbol": "ZERO", "side": "long", "contracts": 0},
        {"symbol": "BAD", "side": "long", "contracts": 1},
        {"symbol": "SHORT", "side": "short", "contracts": 2},
    ]
    client.fail_types.add("BAD")
    client.open_orders = [
        {"id": "bad-cancel", "symbol": "BTC/USDT"},
        {"id": "good-cancel", "symbol": "ETH/USDT"},
    ]
    client.fail_cancel_ids.add("bad-cancel")
    adapter.client = client

    result = await adapter.close_all_positions()
    assert result["closed_positions"] == 1
    assert result["cancelled_orders"] == 1
    assert len(result["errors"]) == 2
    close_call = next(call for call in client.calls if call[0] == "SHORT")
    assert close_call == (
        "SHORT", "market", "buy", 2.0, None, {"reduceOnly": True}
    )


async def test_cancel_order_and_status():
    adapter = CCXTAdapter("fake", "key", "secret")
    assert await adapter.cancel_order("one") is False
    assert adapter.get_status()["connected"] is False

    client = FakeClient()
    adapter.client = client
    assert await adapter.cancel_order("one", "BTC/USDT") is True
    client.fail_cancel_ids.add("two")
    assert await adapter.cancel_order("two") is False
    status = adapter.get_status()
    assert status["broker"] == "FAKE"
    assert status["connected"] is True
    assert status["api_status"] == "READY"


async def test_primexbt_status_and_constructor():
    adapter = PrimeXBTAdapter("key", "secret", "pass")
    assert adapter.exchange_id == "primexbt"
    status = adapter.get_status()
    assert status["broker"] == "PRIMEXBT"
    assert "Futures/CFD" in status["note"]
