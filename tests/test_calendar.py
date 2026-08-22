import pytest
import socket
from api.engines.news_engine import NewsEngine


def _require_network():
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3)
    except OSError:
        pytest.skip("network unavailable")


@pytest.mark.network
async def test_news_engine_status():
    _require_network()
    engine = NewsEngine()
    status = await engine.check_trading_allowed(asset_class="CRYPTO")

    # The status must always carry the full contract
    for key in ("trading_allowed", "day_ok", "news_ok", "session_ok", "next_events", "timestamp"):
        assert key in status, f"missing key {key}"

    # Crypto is 24/7 -> session must be allowed
    assert status["session_ok"] is True
    assert status["day_ok"] is True


@pytest.mark.network
async def test_session_filter_crypto_24_7():
    _require_network()
    engine = NewsEngine()
    sf = engine.session_filter
    for day in range(7):  # every day of the week
        res = sf.is_trading_allowed(asset_class="CRYPTO")
        assert res["session_ok"] is True
