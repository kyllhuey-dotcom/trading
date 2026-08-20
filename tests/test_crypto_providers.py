import asyncio
from api.engines.data_providers.binance_provider import BinanceProvider
from api.engines.data_providers.gate_provider import GateProvider

async def test():
    print("Testing Binance...")
    b = BinanceProvider()
    q_b = await b.get_quote('BTC/USDT')
    print(f"Binance BTC/USDT: {q_b}")
    await b.close()
    
    print("\nTesting Gate...")
    g = GateProvider()
    q_g = await g.get_quote('BTC/USDT')
    print(f"Gate BTC/USDT: {q_g}")
    await g.close()

if __name__ == "__main__":
    asyncio.run(test())
