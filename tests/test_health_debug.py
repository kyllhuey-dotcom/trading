import ccxt.async_support as ccxt
import asyncio

async def test():
    gate = ccxt.gate()
    try:
        print("Testing Gate.io status...")
        # try:
        #     s = await gate.fetch_status()
        #     print(f"Status: {s}")
        # except Exception as e:
        #     print(f"fetch_status failed: {e}")
        
        print("Testing Gate.io ticker...")
        t = await gate.fetch_ticker('BTC/USDT')
        print(f"Ticker: {t['last']}")
    except Exception as e:
        print(f"Gate.io error: {e}")
    finally:
        await gate.close()

    bybit = ccxt.bybit()
    try:
        print("\nTesting Bybit ticker...")
        t = await bybit.fetch_ticker('BTC/USDT')
        print(f"Ticker: {t['last']}")
    except Exception as e:
        print(f"Bybit error: {e}")
    finally:
        await bybit.close()

if __name__ == "__main__":
    asyncio.run(test())
