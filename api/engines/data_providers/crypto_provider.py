import ccxt
from .base_provider import BaseDataProvider, MarketData
import pandas as pd
from datetime import datetime
from typing import Optional

class CryptoProvider(BaseDataProvider):
    def __init__(self):
        self.exchange = ccxt.gate({
            'enableRateLimit': True,
        })

    async def fetch_ticker(self, symbol: str) -> Optional[MarketData]:
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            ts = ticker.get('timestamp') or self.exchange.milliseconds()
            md = MarketData(
                symbol=symbol,
                asset_class="CRYPTO",
                last=float(ticker['last']) if ticker.get('last') is not None else 0.0,
                timestamp=int(ts),
                source="Gate.io"
            )
            md.bid = float(ticker['bid']) if ticker.get('bid') is not None else None
            md.ask = float(ticker['ask']) if ticker.get('ask') is not None else None
            md.high = float(ticker['high']) if ticker.get('high') is not None else None
            md.low = float(ticker['low']) if ticker.get('low') is not None else None
            md.close = md.last # Populate close
            md.volume = float(ticker['baseVolume']) if ticker.get('baseVolume') is not None else None
            if md.bid is not None and md.ask is not None:
                md.spread = float(md.ask - md.bid)
            
            now_ms = int(datetime.now().timestamp() * 1000)
            md.latency_ms = now_ms - int(ts)
            
            return md
        except Exception as e:
            print(f"CryptoProvider Error ({symbol}): {e}")
            return None

    async def fetch_ohlcv(self, symbol: str, timeframe: str = '1m', limit: int = 100) -> pd.DataFrame:
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
            return df
        except Exception as e:
            print(f"CryptoProvider OHLCV Error: {e}")
            return pd.DataFrame()
