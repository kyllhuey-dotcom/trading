import ccxt
import yfinance as yf
import pandas as pd
from datetime import datetime
import asyncio
from typing import Dict, Any, List

class DataEngine:
    def __init__(self):
        self.crypto_exchange = ccxt.binance()
        # On définit quelques symboles de référence
        self.symbols = {
            "CRYPTO": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
            "FOREX": ["EURUSD=X", "GBPUSD=X", "USDJPY=X"],
            "COMMODITIES": ["GC=F", "CL=F"], # Gold, Crude Oil
            "INDICES": ["^GSPC", "^IXIC"] # S&P 500, Nasdaq
        }

    async def fetch_crypto_price(self, symbol: str) -> Dict[str, Any]:
        try:
            ticker = self.crypto_exchange.fetch_ticker(symbol)
            return {
                "symbol": symbol,
                "price": ticker['last'],
                "change": ticker['percentage'],
                "timestamp": ticker['timestamp'],
                "type": "LIVE DATA",
                "source": "Binance"
            }
        except Exception as e:
            return {"error": str(e), "symbol": symbol}

    async def fetch_yfinance_price(self, symbol: str) -> Dict[str, Any]:
        try:
            # yfinance est synchrone, on l'exécute dans un thread pour ne pas bloquer l'event loop
            ticker = await asyncio.to_thread(yf.Ticker, symbol)
            data = await asyncio.to_thread(ticker.history, period="1d", interval="1m")
            if data.empty:
                return {"error": "No data found", "symbol": symbol}
            
            last_price = data['Close'].iloc[-1]
            prev_close = data['Open'].iloc[0]
            change_pct = ((last_price - prev_close) / prev_close) * 100
            
            return {
                "symbol": symbol,
                "price": last_price,
                "change": change_pct,
                "timestamp": int(datetime.now().timestamp() * 1000),
                "type": "LIVE DATA",
                "source": "Yahoo Finance"
            }
        except Exception as e:
            return {"error": str(e), "symbol": symbol}

    async def fetch_crypto_ohlcv(self, symbol: str, timeframe: str = '1m', limit: int = 100) -> pd.DataFrame:
        try:
            ohlcv = self.crypto_exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
            return df
        except Exception as e:
            print(f"Error fetching OHLCV for {symbol}: {e}")
            return pd.DataFrame()

    async def get_market_overview(self) -> Dict[str, List[Dict[str, Any]]]:
        tasks = []
        for s in self.symbols["CRYPTO"]:
            tasks.append(self.fetch_crypto_price(s))
        
        # On peut rajouter les autres si besoin, mais restons sur crypto pour la démo
        results = await __import__('asyncio').gather(*tasks)
        return {"CRYPTO": results}

if __name__ == "__main__":
    engine = DataEngine()
    async def test():
        data = await engine.get_market_overview()
        print(data)
    asyncio.run(test())
