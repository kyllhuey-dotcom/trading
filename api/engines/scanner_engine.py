import asyncio
import pandas as pd
from typing import Dict, Any, List
from datetime import datetime

class ScannerEngine:
    """
    Market Scanner (Rule 14, 35, 19).
    Scans and analyzes all instruments from the universe.
    """
    def __init__(self, data_engine: Any, analysis_engine: Any, signal_engine: Any, news_engine: Any, max_concurrent: int = 5):
        self.data = data_engine
        self.analysis = analysis_engine
        self.signal = signal_engine
        self.news = news_engine
        self.max_concurrent = max_concurrent
        self.last_scan_duration = 0.0

    async def scan_asset(self, symbol: str, semaphore: asyncio.Semaphore) -> Dict[str, Any]:
        """
        Full analysis of a single asset with concurrency control.
        """
        async with semaphore:
            try:
                # 1. Fetch metadata
                info = self.data.universe.get_info(symbol)
                
                # 2. Fetch Data (Rule 4)
                # Parallel fetch for LTF, HTF and Ticker
                df_ltf_task = self.data.fetch_ohlcv(symbol, timeframe='1m', limit=50)
                df_htf_task = self.data.fetch_ohlcv(symbol, timeframe='15m', limit=30)
                ticker_task = self.data.fetch_ticker(symbol)
                
                df_ltf, df_htf, ticker = await asyncio.gather(df_ltf_task, df_htf_task, ticker_task)
                
                if df_ltf.empty or not ticker:
                    return {
                        "symbol": symbol,
                        "asset_class": info.get("asset_class") if info else "UNKNOWN",
                        "status": "DATA_UNAVAILABLE",
                        "tradable": False,
                        "reason": "Missing data"
                    }

                # 3. Market Analysis (Rule 9, 12, 17)
                htf_analysis = self.analysis.identify_structure(df_htf)
                ltf_analysis = self.analysis.identify_structure(df_ltf, htf_bias=htf_analysis.get("trend"))
                
                # 4. News Risk (Rule 15)
                # Asset specific news filtering if symbol has a currency mapping
                asset_currency = None
                if info.get("asset_class") == "FOREX":
                    asset_currency = info["display_symbol"].split('/')[0]
                
                news_status = await self.news.check_trading_allowed(asset_currency=asset_currency, asset_class=info.get("asset_class"))
                
                # 5. Signal calculation (Even if not tradable, we calculate a score for Ranking)
                # Bypass fail-safe for ranking
                scoring_news = news_status.copy()
                scoring_news["trading_allowed"] = True
                raw_signal = self.signal.generate_signal(ltf_analysis, scoring_news, df_ltf)
                
                # Real signal check
                signal = self.signal.generate_signal(ltf_analysis, news_status, df_ltf)
                
                return {
                    "symbol": symbol,
                    "asset_class": info.get("asset_class"),
                    "name": info.get("name"),
                    "price": float(ticker.get("last", 0)),
                    "change": float(ticker.get("change_24h", 0) or 0),
                    "spread": float(ticker.get("spread", 0) or 0),
                    "volume": float(ticker.get("volume", 0) or 0),
                    "status": ticker.get("status"),
                    "trend": ltf_analysis.get("trend"),
                    "structure": ltf_analysis.get("is_hh") and "HH/HL" or (ltf_analysis.get("is_ll") and "LH/LL" or "Neutral"),
                    "market_state": ltf_analysis.get("market_state"),
                    "volatility": ltf_analysis.get("volatility"),
                    "market_status": self.data.universe.get_market_status(symbol),
                    "news_risk": "High" if not news_status["news_ok"] else "Low",
                    "signal": signal.get("status"),
                    "score": int(raw_signal.get("score", 0)),
                    "tradable": signal.get("status") == "SIGNAL_DETECTED",
                    "reason": signal.get("reason", ltf_analysis.get("market_state"))
                }
            except Exception as e:
                print(f"Scanner Error ({symbol}): {e}")
                return {"symbol": symbol, "status": "ERROR", "tradable": False, "reason": str(e)}

    async def scan_all(self) -> List[Dict[str, Any]]:
        start_time = datetime.now()
        symbols = self.data.universe.get_all_ids()
        
        # Rule 31: Use semaphore to limit concurrent requests
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        tasks = [self.scan_asset(s, semaphore) for s in symbols]
        results = await asyncio.gather(*tasks)
        
        self.last_scan_duration = (datetime.now() - start_time).total_seconds()
        return results
