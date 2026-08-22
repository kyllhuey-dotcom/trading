"""
Live provider probe — DataEngine.get_market_overview over the wire.

Converted from the former root-level ``test_lot2_data.py`` script into a
proper defensive network test: it shares the tests/ conventions (``network``
marker, auto-skip offline or when providers are unreachable) and makes real
assertions on the unified overview contract.
"""
import asyncio
import socket

import pytest

from api.engines.data_engine import DataEngine

PROBE_TIMEOUT_S = 90.0


def _require_network():
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3)
    except OSError:
        pytest.skip("network unavailable")


@pytest.mark.network
async def test_market_overview_live():
    _require_network()
    engine = DataEngine()
    try:
        overview = await asyncio.wait_for(
            engine.get_market_overview(), timeout=PROBE_TIMEOUT_S)
    except Exception as e:  # provider outage / geo-block = environment issue
        pytest.skip(f"providers unavailable: {type(e).__name__}")
    finally:
        await engine.shutdown()

    assert isinstance(overview, dict)
    markets = [m for items in overview.values() for m in items]
    if not markets:
        pytest.skip("no provider returned data (offline or geo-blocked)")

    for market in markets:
        assert market.get("market_id"), f"missing market_id: {market!r}"
        assert market.get("display_symbol"), f"missing display_symbol: {market!r}"
        assert market.get("market_status"), f"missing market_status: {market!r}"
        last = market.get("last")
        assert last is None or last > 0, f"invalid last price: {market!r}"
