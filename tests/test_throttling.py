import pytest
import asyncio
from api.engines.data_layer import DataLayer
from api.engines.market_universe import MarketUniverse

class FailingProvider:
    def __init__(self):
        self.call_count = 0
    async def get_quote(self, symbol):
        self.call_count += 1
        return None
    async def health_check(self):
        return {"status": "ONLINE"}

@pytest.mark.asyncio
async def test_data_layer_throttling():
    layer = DataLayer()
    layer.failure_cooldown = 1 # 1 second for test
    universe = MarketUniverse()
    
    provider = FailingProvider()
    layer.register_provider("yahoo_commodities", provider)
    
    # First call - should call provider
    await layer.get_all_quotes(["gold"], universe)
    assert provider.call_count == 1
    
    # Second call immediately - should be throttled
    await layer.get_all_quotes(["gold"], universe)
    assert provider.call_count == 1
    
    # Wait for cooldown
    await asyncio.sleep(1.1)
    
    # Third call - should call provider again
    await layer.get_all_quotes(["gold"], universe)
    assert provider.call_count == 2
