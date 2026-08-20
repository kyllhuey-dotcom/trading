import yfinance as yf
import pandas as pd
import asyncio
from .base_provider import BaseDataProvider, MarketData
from datetime import datetime
from typing import Optional

class YahooProvider(BaseDataProvider):
    def __init__(self, asset_class: str):
        self.asset_class = asset_class

    async def fetch_ticker(self, symbol: str) -> Optional[MarketData]:
        try:
            ticker = await asyncio.to_thread(yf.Ticker, symbol)
            # yfinance doesn't have a simple "ticker" for real-time without history or expensive API
            # We use history to get the last price
            data = await asyncio.to_thread(ticker.history, period="1d", interval="1m")
            if data.empty:
                return None
            
            last_price = float(data['Close'].iloc[-1])
            md = MarketData(
                symbol=symbol,
                asset_class=self.asset_class,
                last=last_price,
                timestamp=int(data.index[-1].timestamp() * 1000),
                source="Yahoo Finance"
            )
            md.open = float(data['Open'].iloc[-1])
            md.high = float(data['High'].iloc[-1])
            md.low = float(data['Low'].iloc[-1])
            md.close = last_price # Fixed: was null
            md.volume = float(data['Volume'].iloc[-1])
            
            return md
        except Exception as e:
            print(f"YahooProvider Error ({symbol}): {e}")
            return None

    async def fetch_ohlcv(self, symbol: str, timeframe: str = '1m', limit: int = 100) -> pd.DataFrame:
        try:
            # Map timeframes for yfinance
            tf_map = {'1m': '1m', '5m': '5m', '15m': '15m', '1h': '1h', '1d': '1d'}
            yf_tf = tf_map.get(timeframe, '1m')
            
            ticker = await asyncio.to_thread(yf.Ticker, symbol)
            # Period calculation based on limit and timeframe
            # Simple approximation
            data = await asyncio.to_thread(ticker.history, period="5d" if limit > 100 else "1d", interval=yf_tf)
            if data.empty:
                return pd.DataFrame()
            
            df = data.reset_index()
            # Standardize column names
            df = df.rename(columns={df.columns[0]: 'Timestamp', 'Open': 'Open', 'High': 'High', 'Low': 'Low', 'Close': 'Close', 'Volume': 'Volume'})
            df['Timestamp'] = df['Timestamp'].apply(lambda x: int(x.timestamp() * 1000))
            
            # Convert to native types
            for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                df[col] = df[col].astype(float)
                
            return df.tail(limit)
        except Exception as e:
            print(f"YahooProvider OHLCV Error: {e}")
            return pd.DataFrame()
