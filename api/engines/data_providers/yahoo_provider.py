import yfinance as yf
import pandas as pd
import asyncio
from .base_provider import MarketDataProvider, TickerModel
from datetime import datetime
from typing import List, Optional, Dict, Any

class YahooProvider(MarketDataProvider):
    """
    Data Provider for Forex, Indices and Commodities using Yahoo Finance (Rule 6, 7, 8).
    """
    def __init__(self, asset_class: str):
        self.asset_class = asset_class
        self.source_name = "Yahoo Finance"

    async def get_symbols(self) -> List[str]:
        # yfinance doesn't support discovery, symbols are defined in Catalog
        return []

    async def get_quote(self, symbol: str) -> Optional[TickerModel]:
        try:
            # yfinance is synchronous, wrap in thread
            ticker = await asyncio.to_thread(yf.Ticker, symbol)
            # Use 1d history to get the most recent data point
            data = await asyncio.to_thread(ticker.history, period="1d", interval="1m")
            
            if data.empty:
                return None

            last_row = data.iloc[-1]
            last_price = float(last_row['Close'])
            
            # Normalization (Rule 3)
            open_price = float(last_row['Open'])
            change_pct = ((last_price - open_price) / open_price * 100) if open_price else 0
            
            return TickerModel(
                symbol=symbol,
                name=symbol,
                asset_class=self.asset_class,
                exchange="Global Market",
                timestamp=int(data.index[-1].timestamp() * 1000),
                last=last_price,
                change_24h=change_pct,
                open=float(last_row['Open']),
                high=float(last_row['High']),
                low=float(last_row['Low']),
                volume=float(last_row['Volume']),
                source=self.source_name,
                status="DELAYED" # Yahoo free API is delayed by 15min (Rule 6)
            )
        except Exception as e:
            print(f"YahooProvider quote error ({symbol}): {e}")
            return None

    async def get_ohlcv(self, symbol: str, timeframe: str = '1m', limit: int = 100) -> pd.DataFrame:
        try:
            # Map timeframe to yfinance format
            tf_map = {'1m': '1m', '5m': '5m', '15m': '15m', '1h': '1h', '1d': '1d'}
            yf_tf = tf_map.get(timeframe, '1m')
            
            ticker = await asyncio.to_thread(yf.Ticker, symbol)
            # Period calculation based on limit and timeframe
            period = "1d"
            if timeframe in ['1h', '1d'] or limit > 100: period = "5d"
            
            data = await asyncio.to_thread(ticker.history, period=period, interval=yf_tf)
            if data.empty:
                return pd.DataFrame()
                
            df = data.reset_index()
            # Standardize column names
            df = df.rename(columns={df.columns[0]: 'Timestamp', 'Open': 'Open', 'High': 'High', 'Low': 'Low', 'Close': 'Close', 'Volume': 'Volume'})
            df['Timestamp'] = df['Timestamp'].apply(lambda x: int(x.timestamp() * 1000))
            
            # Ensure native float types (Rule 1, 47)
            for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                df[col] = df[col].astype(float)
                
            return df.tail(limit)
        except Exception as e:
            print(f"YahooProvider OHLCV error ({symbol}): {e}")
            return pd.DataFrame()

    async def health_check(self) -> Dict[str, Any]:
        try:
            # Check if Yahoo is responsive
            await asyncio.to_thread(yf.Ticker("EURUSD=X").history, period="1d")
            return {"provider": f"Yahoo_{self.asset_class}", "status": "ONLINE"}
        except Exception as e:
            return {"provider": f"Yahoo_{self.asset_class}", "status": "ERROR", "message": str(e)}
