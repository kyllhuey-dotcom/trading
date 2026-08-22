import pytest
import socket
import ccxt
import ccxt.async_support as ccxt_async


def _require_network():
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3)
    except OSError:
        pytest.skip("network unavailable")


@pytest.mark.network
async def test_gate_ticker():
    _require_network()
    gate = ccxt_async.gate({'enableRateLimit': True})
    try:
        try:
            t = await gate.fetch_ticker('BTC/USDT')
        except (ccxt.RateLimitExceeded, ccxt.NetworkError, ccxt.ExchangeNotAvailable,
                ccxt.RequestTimeout, ccxt.AuthenticationError, ccxt.PermissionDenied) as e:
            pytest.skip(f"Gate unavailable: {type(e).__name__}")
            return
        assert t['last'] and t['last'] > 0
    finally:
        await gate.close()


@pytest.mark.network
async def test_bybit_ticker():
    _require_network()
    bybit = ccxt_async.bybit({'enableRateLimit': True})
    try:
        try:
            t = await bybit.fetch_ticker('BTC/USDT')
        except (ccxt.RateLimitExceeded, ccxt.NetworkError, ccxt.ExchangeNotAvailable,
                ccxt.RequestTimeout) as e:
            pytest.skip(f"Bybit unavailable: {type(e).__name__}")
            return
        assert t['last'] and t['last'] > 0
    finally:
        await bybit.close()
