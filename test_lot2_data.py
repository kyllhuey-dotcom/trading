import asyncio
import sys
import os

# Add the current directory to sys.path so we can import api
sys.path.append(os.getcwd())

from api.engines.data_engine import DataEngine

async def test_data():
    engine = DataEngine()
    print("Fetching market overview...")
    overview = await engine.get_market_overview()
    
    for category, markets in overview.items():
        print(f"\nCategory: {category}")
        for market in markets:
            print(f"  - {market.get('display_symbol')}: {market.get('last')} {market.get('status')} ({market.get('market_status')})")

    await engine.shutdown()

if __name__ == "__main__":
    asyncio.run(test_data())
