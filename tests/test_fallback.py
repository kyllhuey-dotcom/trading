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
    
    # LOT 7 provider priority is Binance > Bybit > Gate, so Bybit is the
    # primary for btc_usdt and Gate the backup. Register the PRIMARY (bybit)
    # as failing so the fallback to the backup (gate) actually happens.
    primary = MockProvider("Bybit", fail=True)
    backup = MockProvider("Gate", fail=False)
    
    layer.register_provider("bybit", primary)
    layer.register_provider("gate", backup)
    
    quotes = await layer.get_all_quotes(["btc_usdt"], universe)
    
    assert len(quotes) == 1
    # bybit (primary) was tried first and failed, then gate (backup) answered.
    assert primary.call_count == 1
    assert backup.call_count == 1

