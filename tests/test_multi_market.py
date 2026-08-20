import asyncio
from api.engines.data_engine import DataEngine

async def test():
    print("Testing DataEngine for Multi-Market...")
    engine = DataEngine()
    
    # Test Forex
    print("\nFetching EUR/USD Quote...")
    q_fx = await engine.fetch_ticker("EURUSD=X")
    print(f"Quote: {q_fx}")
    
    # Test Index
    print("\nFetching S&P 500 Quote...")
    q_idx = await engine.fetch_ticker("^GSPC")
    print(f"Quote: {q_idx}")
    
    # Test Commodity
    print("\nFetching Gold Quote...")
    q_cmd = await engine.fetch_ticker("GC=F")
    print(f"Quote: {q_cmd}")
    
    await engine.shutdown()

if __name__ == "__main__":
    asyncio.run(test())
