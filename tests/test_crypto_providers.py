import pytest
import socket
import ccxt
from api.engines.data_providers.binance_provider import BinanceProvider
from api.engines.data_providers.gate_provider import GateProvider
from api.engines.data_providers.bybit_provider import BybitProvider


def _require_network():
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3)
    except OSError:
        pytest.skip("network unavailable")


def _provider_error_skip(e: Exception) -> None:
    """Rate limits / geo-blocks / timeouts are environment issues, not code bugs."""
    if isinstance(e, (ccxt.RateLimitExceeded, ccxt.NetworkError, ccxt.ExchangeNotAvailable,
                      ccxt.RequestTimeout, ccxt.AuthenticationError, ccxt.PermissionDenied)):
        pytest.skip(f"provider unavailable: {type(e).__name__}")


@pytest.mark.network
async def test_binance_quote():
    _require_network()
    b = BinanceProvider()
    try:
        q = await b.get_quote('BTC/USDT')
        if q is None:
            pytest.skip("Binance unavailable from this network (geo-block or rate limit)")
        assert q.last > 0
        assert q.status == "LIVE"
    finally:
        await b.close()


@pytest.mark.network
async def test_gate_quote():
    _require_network()
    g = GateProvider()
    try:
        q = await g.get_quote('BTC/USDT')
        if q is None:
            pytest.skip("Gate unavailable (rate limit or maintenance)")
        assert q.last > 0
    finally:
        await g.close()


@pytest.mark.network
async def test_bybit_quote():
    _require_network()
    b = BybitProvider()
    try:
        q = await b.get_quote('BTC/USDT')
        if q is None:
            pytest.skip("Bybit unavailable (rate limit or maintenance)")
        assert q.last > 0
    finally:
        await b.close()
