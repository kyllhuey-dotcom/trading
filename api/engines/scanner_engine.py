import asyncio
import pandas as pd
from typing import Dict, Any, List
from datetime import datetime

class ScannerEngine:
    """
    Market Scanner (Rule 14, 35, 19).
    Scans and analyzes all instruments from the catalog.
    """
    def __init__(self, data_engine: Any, analysis_engine: Any, signal_engine: Any, news_engine: Any):
        self.data = data_engine
        self.analysis = analysis_engine
        self.signal = signal_engine
        self.news = news_engine

    async def scan_asset(self, symbol: str) -> Dict[str, Any]:
        """
        Full analysis of a single asset.
        """
        try:
            # 1. Fetch metadata
            info = self.data.catalog.get_info(symbol)
            
            # 2. Fetch Data (Rule 4)
            # For scanner, we might want faster timeframes or fewer candles
            df_ltf = await self.data.fetch_ohlcv(symbol, timeframe='1m', limit=50)
            df_htf = await self.data.fetch_ohlcv(symbol, timeframe='15m', limit=30)
            ticker = await self.data.fetch_ticker(symbol)
            
            if df_ltf.empty or not ticker:
                return {
                    "symbol": symbol,
                    "asset_class": info.get("asset_class"),
                    "status": "DATA_UNAVAILABLE",
                    "tradable": False,
                    "reason": "Missing data"
                }

            # 3. Market Analysis (Rule 9, 12, 17)
            htf_analysis = self.analysis.identify_structure(df_htf)
            ltf_analysis = self.analysis.identify_structure(df_ltf, htf_bias=htf_analysis.get("trend"))
            
            # 4. News Risk (Rule 15)
            news_status = await self.news.check_trading_allowed(asset_class=info.get("asset_class"))
            
            # 5. Signal calculation (Even if not tradable, we calculate a score for Ranking)
            # We temporarily bypass the fail-safe for scoring purposes
            raw_signal = self.signal.generate_signal(ltf_analysis, {"trading_allowed": True}, df_ltf)
            score = raw_signal.get("score", 0)
            
            # Real signal check
            signal = self.signal.generate_signal(ltf_analysis, news_status, df_ltf)
            
            return {
                "symbol": symbol,
                "asset_class": info.get("asset_class"),
                "name": info.get("name"),
                "price": ticker.get("last"),
                "change": ticker.get("change"),
                "spread": ticker.get("spread"),
                "volume": ticker.get("volume"),
                "status": ticker.get("status"),
                "trend": ltf_analysis.get("trend"),
                "structure": ltf_analysis.get("is_hh") and "HH/HL" or (ltf_analysis.get("is_ll") and "LH/LL" or "Neutral"),
                "market_state": ltf_analysis.get("market_state"),
                "volatility": ltf_analysis.get("volatility"),
                "news_risk": "High" if not news_status["news_ok"] else "Low",
                "signal": signal.get("status"),
                "score": score,
                "tradable": signal.get("status") == "SIGNAL_DETECTED" and news_status["trading_allowed"],
                "reason": signal.get("reason", ltf_analysis.get("market_state"))
            }
        except Exception as e:
            print(f"Scanner Error ({symbol}): {e}")
            return {"symbol": symbol, "status": "ERROR", "tradable": False}

    async def scan_all(self) -> List[Dict[str, Any]]:
        symbols = self.data.catalog.get_all_symbols()
        # Scan in small batches to respect rate limits (Rule 31)
        batch_size = 3
        all_results = []
        
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i+batch_size]
            tasks = [self.scan_asset(s) for s in batch]
            results = await asyncio.gather(*tasks)
            all_results.extend(results)
            # Throttling
            await asyncio.sleep(0.2)
            
        return all_results
