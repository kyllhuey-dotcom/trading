import asyncio
from api.engines.news_engine import NewsEngine

async def test():
    print("Testing NewsEngine (Economic Calendar)...")
    engine = NewsEngine()
    
    status = await engine.check_trading_allowed(asset_class="CRYPTO")
    print(f"Trading Allowed: {status['trading_allowed']}")
    print(f"Day OK: {status['day_ok']}")
    print(f"News OK: {status['news_ok']}")
    
    if status['next_events']:
        print("\nNext Events:")
        for e in status['next_events']:
            print(f"- {e['time']} {e['currency']} {e['impact']}: {e['event']} (F: {e['forecast']}, P: {e['previous']})")
    else:
        print("\nNo upcoming high impact events.")

if __name__ == "__main__":
    asyncio.run(test())
