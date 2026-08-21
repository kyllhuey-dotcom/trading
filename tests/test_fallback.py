import pytest
import asyncio
from api.engines.data_layer import DataLayer
from api.engines.market_universe import MarketUniverse

class MockProvider:
    def __init__(self, name, fail=False):
        self.name = name
        self.fail = fail
        self.call_count = 0

    async def get_quote(self, symbol):
        self.call_count += 1
        if self.fail:
            raise Exception(f"Mock failure in {self.name}")
        return type('obj', (object,), {'symbol': symbol, 'last': 100, 'dict': lambda: {}})

@pytest.mark.asyncio
async def test_data_layer_fallback():
    layer = DataLayer()
    universe = MarketUniverse()
    
    # Register a failing primary and a working backup
    primary = MockProvider("Primary", fail=True)
    backup = MockProvider("Backup", fail=False)
    
    layer.register_provider("gate", primary)
    layer.register_provider("bybit", backup)
    
    # btc_usdt has gate as primary and bybit as backup in my updated universe
    quotes = await layer.get_all_quotes(["btc_usdt"], universe)
    
    assert len(quotes) == 1
    assert primary.call_count == 1
    assert backup.call_count == 1
