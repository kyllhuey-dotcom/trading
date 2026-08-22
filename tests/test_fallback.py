import pytest
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
    
    # Priority order is binance -> bybit -> gate, so the higher-priority
    # "bybit" provider is attempted first. Make it fail to exercise the
    # fallback to the lower-priority "gate" provider.
    primary = MockProvider("Primary", fail=True)
    backup = MockProvider("Backup", fail=False)

    layer.register_provider("bybit", primary)
    layer.register_provider("gate", backup)

    # btc_usdt lists both gate and bybit as providers in the universe
    quotes = await layer.get_all_quotes(["btc_usdt"], universe)

    assert len(quotes) == 1
    assert primary.call_count == 1
    assert backup.call_count == 1
