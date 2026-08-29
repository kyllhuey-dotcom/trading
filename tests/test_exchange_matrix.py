"""v3.3 — multi-exchange contract matrix (offline mocks).

Binance / Bybit / OKX / Gate: create order, clientOrderId, fetch order,
open/closed orders, trades, stop contract, reduceOnly, cancel, full and
partial fills, fees, timeout, rejected/canceled/expired, price & quantity
precision.
"""
from __future__ import annotations

import asyncio

import pytest

from api.engines.broker_adapters.ccxt_adapter import CCXTAdapter
from tests.exchange_matrix import (
    BinanceMock,
    GateMock,
    OKXMock,
    make_mock,
)

EXCHANGES = ["binance", "bybit", "okx", "gate"]


# --------------------------------------------------------------------------- #
# Create order + clientOrderId + fetch order + lists + trades                  #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("exchange", EXCHANGES)
async def test_create_order_with_client_order_id(exchange):
    mock = make_mock(exchange)
    res = await mock.create_order("BTC/USDT", "market", "buy", 0.5, None,
                                  {"clientOrderId": "QTP-abc123"})
    assert res["id"]
    assert res["clientOrderId"] == "QTP-abc123"
    # fetch by exchange id AND by clientOrderId
    assert (await mock.fetch_order(res["id"]))["id"] == res["id"]
    assert (await mock.fetch_order("QTP-abc123"))["id"] == res["id"]
    # closed (fully filled) → visible in closed orders + trades
    assert any(o["id"] == res["id"] for o in await mock.fetch_closed_orders())
    assert any(t["order"] == res["id"] for t in await mock.fetch_trades())
    # duplicate clientOrderId is rejected (idempotence at the exchange level)
    with pytest.raises(RuntimeError):
        await mock.create_order("BTC/USDT", "market", "buy", 0.5, None,
                                {"clientOrderId": "QTP-abc123"})


@pytest.mark.parametrize("exchange", EXCHANGES)
async def test_full_fill_fees(exchange):
    mock = make_mock(exchange, fill_ratio=1.0, fee_pct=0.001)
    res = await mock.create_order("BTC/USDT", "market", "buy", 1.0)
    assert res["status"] == "closed"
    assert res["filled"] == pytest.approx(1.0)
    assert res["average"] == pytest.approx(100.0)
    assert res["fee"]["cost"] == pytest.approx(1.0 * 100.0 * 0.001)


@pytest.mark.parametrize("exchange", EXCHANGES)
async def test_partial_fill_stays_open(exchange):
    mock = make_mock(exchange, fill_ratio=0.4)
    res = await mock.create_order("BTC/USDT", "market", "buy", 1.0)
    assert res["status"] == "open"
    assert res["filled"] == pytest.approx(0.4)
    assert any(o["id"] == res["id"] for o in await mock.fetch_open_orders())
    assert not any(o["id"] == res["id"] for o in await mock.fetch_closed_orders())


# --------------------------------------------------------------------------- #
# Stop contract per exchange (adapter mapping vs exchange mock contract)       #
# --------------------------------------------------------------------------- #
async def _adapter_order(exchange, sl, hedge_side, tp=None):
    mock = make_mock(exchange)
    adapter = CCXTAdapter(exchange, "k", "s")
    adapter.client = mock
    res = await adapter.execute_order("BTC/USDT", "buy" if hedge_side == "sell"
                                      else "sell", 1.0, sl=sl, tp=tp)
    assert res["success"] is True, res
    return mock, res


@pytest.mark.parametrize("exchange", EXCHANGES)
async def test_stop_contract_rejected_when_misused(exchange):
    mock = make_mock(exchange)
    if exchange == "binance":
        with pytest.raises(ValueError):
            await mock.create_order("BTC/USDT", "STOP_MARKET", "sell", 1.0, None,
                                    {"stopPrice": 95.0})  # missing reduceOnly
        with pytest.raises(ValueError):
            await mock.create_order("BTC/USDT", "STOP_MARKET", "sell", 1.0, None,
                                    {"reduceOnly": True})  # missing stopPrice
    elif exchange == "bybit":
        with pytest.raises(ValueError):
            await mock.create_order("BTC/USDT", "market", "sell", 1.0, None,
                                    {"triggerPrice": 95.0, "reduceOnly": True})
    elif exchange == "okx":
        with pytest.raises(ValueError):
            await mock.create_order("BTC/USDT", "market", "sell", 1.0, None,
                                    {"stopLossPrice": 95.0})
    else:
        with pytest.raises(ValueError):
            await mock.create_order("BTC/USDT", "stop_loss", "sell", 1.0, None,
                                    {"stopPrice": 95.0})  # missing triggerPrice


@pytest.mark.parametrize("exchange", EXCHANGES)
async def test_adapter_stop_obeys_exchange_contract(exchange):
    # BUY position → hedge side is "sell"; the adapter must send exactly the
    # parameters the exchange mock accepts.
    mock, res = await _adapter_order(exchange, sl=95.0, hedge_side="sell", tp=110.0)
    stop_calls = [c for c in mock.create_calls if "stop" in c["type"].lower()
                  or "trigger" in c["params"] or "stopPrice" in c["params"]
                  or "stopLossPrice" in c["params"] or "triggerPrice" in c["params"]]
    assert stop_calls, f"no stop order sent to {exchange}"
    for c in stop_calls:
        assert c["params"].get("reduceOnly") is True, f"{exchange} stop not reduceOnly"
    assert res["sl_order_id"] is not None
    assert res["tp_order_id"] is not None


# --------------------------------------------------------------------------- #
# Cancel contract                                                              #
# --------------------------------------------------------------------------- #
async def test_cancel_open_and_closed_orders():
    mock = GateMock(fill_ratio=0.5)  # partial → open order
    res = await mock.create_order("BTC/USDT", "market", "buy", 1.0)
    ok = await mock.cancel_order(res["id"], "BTC/USDT")
    assert ok["status"] == "canceled"
    assert res["id"] in mock.cancel_calls
    assert not any(o["id"] == res["id"] for o in await mock.fetch_open_orders())
    with pytest.raises(RuntimeError):
        await mock.cancel_order(res["id"], "BTC/USDT")  # already closed


# --------------------------------------------------------------------------- #
# Failure contract: timeout / rejected / canceled / expired                    #
# --------------------------------------------------------------------------- #
async def test_timeout_contract():
    mock = BinanceMock(fail={"timeout"})
    with pytest.raises(asyncio.TimeoutError):
        await mock.create_order("BTC/USDT", "market", "buy", 1.0)
    mock2 = GateMock(timeout_after=5.0)  # slow exchange
    with pytest.raises(asyncio.TimeoutError):
        await mock2.fetch_order("whatever")


async def test_adapter_timeout_is_order_state_unknown():
    """An adapter-level timeout with an unfindable order → honest UNKNOWN."""
    adapter = CCXTAdapter("binance", "k", "s")
    mock = BinanceMock(fail={"create", "fetch_order", "open_orders",
                             "closed_orders", "trades"})
    adapter.client = mock
    res = await adapter.execute_order("BTC/USDT", "buy", 1.0,
                                      client_order_id="QTP-timeout")
    assert res["success"] is False
    assert res["reason"] == "ORDER_STATE_UNKNOWN"


async def test_status_contract_canceled_expired_rejected():
    for status in ("canceled", "expired", "rejected"):
        mock = OKXMock()
        mock.seed_order("ord-1", status=status)
        fetched = await mock.fetch_order("ord-1")
        assert fetched["status"] == status
        from api.engines import protection_state
        assert protection_state.normalize_order_status(fetched["status"]) == status.upper()


# --------------------------------------------------------------------------- #
# Price & quantity precision                                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("exchange", EXCHANGES)
async def test_precision_contract(exchange):
    mock = make_mock(exchange)
    # below lot size
    with pytest.raises(ValueError):
        mock.check_precision("BTC/USDT", 100.0, mock.lot_size / 2)
    # not a multiple of the lot size
    with pytest.raises(ValueError):
        mock.check_precision("BTC/USDT", 100.0, mock.lot_size * 1.37)
    # price off the tick
    with pytest.raises(ValueError):
        mock.check_precision("BTC/USDT", 100.123456, mock.lot_size * 10)
    # notional below the minimum
    with pytest.raises(ValueError):
        mock.check_precision("BTC/USDT", mock.tick_size * 2, mock.lot_size)
    # a valid order passes (1 BTC notional = 100 USDT ≥ min_notional)
    mock.check_precision("BTC/USDT", 100.0, mock.lot_size * 10000)


# --------------------------------------------------------------------------- #
# Adapter ↔ mock integration per exchange                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("exchange", EXCHANGES)
async def test_adapter_full_flow_per_exchange(exchange):
    mock = make_mock(exchange, fill_ratio=1.0)
    adapter = CCXTAdapter(exchange, "k", "s")
    adapter.client = mock
    res = await adapter.execute_order("BTC/USDT", "buy", 1.0, sl=95.0, tp=110.0,
                                      client_order_id=f"QTP-{exchange}")
    assert res["success"] is True
    assert res["filled"] == pytest.approx(1.0)
    assert res["fees"] > 0
    # fetch the entry order back
    status = await adapter.fetch_order_status(res["broker_order_id"], "BTC/USDT")
    assert status is not None
    assert status["client_order_id"] == f"QTP-{exchange}"
    # cancel the resting TP
    assert await adapter.cancel_order(res["tp_order_id"], "BTC/USDT") is True
    st = await adapter.fetch_order_status(res["tp_order_id"], "BTC/USDT")
    assert st["status"] == "canceled"
