import pytest
from api.engines.notification_engine import NotificationEngine

@pytest.mark.asyncio
async def test_notification_no_blocking():
    # Test with invalid config
    notif = NotificationEngine(telegram_token="invalid", telegram_chat_id="invalid")
    
    # This should not raise exception or block
    await notif.notify("SIGNAL", {"symbol": "BTC", "direction": "BUY", "price": 50000, "score": 90, "strategy": "test"})
    assert True

@pytest.mark.asyncio
async def test_notification_formatting():
    notif = NotificationEngine()
    # Mock send_telegram to check formatting
    msg_sent = []
    async def mock_send(msg): msg_sent.append(msg)
    notif.send_telegram = mock_send
    notif.enabled = True
    
    await notif.notify("ORDER_OPEN", {"symbol": "ETH", "entry_price": 2500, "quantity": 1.5})
    assert "POSITION OPENED" in msg_sent[0]
    assert "ETH" in msg_sent[0]
    assert "2500" in msg_sent[0]
