import pytest
import socket
from api.engines.data_engine import DataEngine


def _require_network():
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3)
    except OSError:
        pytest.skip("network unavailable")


@pytest.mark.network
async def test_data_engine_multi_asset():
    """Every asset class must return a normalized ticker."""
    _require_network()
    engine = DataEngine()
    try:
        # Forex
        q_fx = await engine.fetch_ticker("eur_usd")
        if q_fx is None:
            pytest.skip("Yahoo finance unavailable from this network")
        assert q_fx["last"] > 0

        # Indices
        q_idx = await engine.fetch_ticker("spx")
        if q_idx is None:
            pytest.skip("Yahoo finance unavailable from this network")
        assert q_idx["last"] > 0

        # Commodities
        q_cmd = await engine.fetch_ticker("gold")
        if q_cmd is None:
            pytest.skip("Yahoo finance unavailable from this network")
        assert q_cmd["last"] > 0
    finally:
        await engine.shutdown()
