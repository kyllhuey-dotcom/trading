import asyncio
from api.engines.news_engine import NewsEngine

async def test():
    print("Testing NewsEngine (Economic Calendar Scraper)...")
    engine = NewsEngine()
    
    status = await engine.check_trading_allowed(asset_class="CRYPTO")
    print(f"Trading Allowed: {status['trading_allowed']}")
    print(f"Day OK: {status['day_ok']}")
    print(f"News OK: {status['news_ok']}")
    print(f"Data Status: {status.get('status')}")
    
    if status['next_events']:
        print(f"\nFound {len(status['next_events'])} events:")
        for e in status['next_events']:
            print(f"- {e['date']} {e['time']} {e['currency']} {e['impact']}: {e['event']}")
    
    # Dump raw cache to see what's inside
    print(f"\nTotal events in cache: {len(engine.provider.cache)}")
    if engine.provider.cache:
        print("First event sample:")
        print(engine.provider.cache[0])

if __name__ == "__main__":
    asyncio.run(test())
