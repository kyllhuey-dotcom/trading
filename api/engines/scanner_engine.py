import asyncio
import pandas as pd
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

from .diagnostic_engine import DiagnosticEngine

logger = logging.getLogger("ScannerEngine")


class ScannerEngine:
    """
    Market Scanner: analyzes the whole universe with concurrency control
    and attaches a full diagnostic to every non-tradable result.
    """

    def __init__(self, data_engine: Any, analysis_engine: Any, signal_engine: Any,
                 news_engine: Any, max_concurrent: int = 5):
        self.data = data_engine
        self.analysis = analysis_engine
        self.signal = signal_engine
        self.news = news_engine
        self.max_concurrent = max_concurrent
        self.last_scan_duration = 0.0
        self.diagnostic = DiagnosticEngine()
        self.max_spread_pct = 0.5

    def apply_settings(self, settings: Dict[str, str]) -> None:
        try:
            self.max_spread_pct = float(settings.get("max_spread_pct", self.max_spread_pct))
        except ValueError:
            pass

    def _build_diagnosis(self, symbol: str, info: Dict[str, Any], ticker: Optional[Dict[str, Any]],
                         df_ltf: pd.DataFrame, ltf_analysis: Dict[str, Any],
                         news_status: Dict[str, Any], signal: Dict[str, Any]) -> Dict[str, Any]:
        data_valid = bool(ticker and not df_ltf.empty)
        market_open = self.data.universe.get_market_status(symbol) == "OPEN"
        day_allowed = bool(news_status.get("day_ok"))
        session_allowed = bool(news_status.get("session_ok"))
        news_clear = bool(news_status.get("news_ok"))
        not_range = ltf_analysis.get("market_state") != "RANGE"
        trend_valid = ltf_analysis.get("trend") not in (None, "NEUTRAL")
        structure_valid = bool(ltf_analysis.get("is_hh") or ltf_analysis.get("is_ll"))
        signal_valid = signal.get("status") == "SIGNAL_DETECTED"
        spread = float(ticker.get("spread", 0) or 0)
        last = float(ticker.get("last", 0) or 0)
        spread_pct = (spread / last * 100) if last else 0.0
        spread_valid = spread_pct <= self.max_spread_pct
        liquidity_valid = float(ticker.get("volume", 0) or 0) > 0
        # Risk/leverage/broker are validated at execution time (balance & mode dependent)
        risk_valid = True
        leverage_valid = True
        broker_valid = True
        system_armed = True

        reasons = {
            "DATA_VALID": "" if data_valid else "No market data available",
            "DAY_ALLOWED": "" if day_allowed else "Trading day restricted",
            "SESSION_ALLOWED": "" if session_allowed else "Outside trading session",
            "NEWS_CLEAR": "" if news_clear else (news_status.get("blocking_event") or {}).get("title", "High-impact news"),
            "MARKET_OPEN": "" if market_open else "Market closed",
            "NOT_RANGE": "" if not_range else "Market in consolidation range",
            "TREND_VALID": "" if trend_valid else "No directional trend",
            "STRUCTURE_VALID": "" if structure_valid else "No valid market structure",
            "SIGNAL_VALID": "" if signal_valid else signal.get("reason", "No signal"),
            "SPREAD_VALID": "" if spread_valid else f"Spread too high ({spread_pct:.2f}%)",
            "LIQUIDITY_VALID": "" if liquidity_valid else "No liquidity",
            "RISK_VALID": "Validated at execution time",
            "LEVERAGE_VALID": "Validated at execution time",
            "BROKER_VALID": "Validated at execution time",
            "SYSTEM_ARMED": "Validated at execution time",
        }

        return self.diagnostic.diagnose(
            symbol=symbol,
            data_valid=data_valid,
            day_allowed=day_allowed,
            session_allowed=session_allowed,
            news_clear=news_clear,
            market_open=market_open,
            not_range=not_range,
            trend_valid=trend_valid,
            structure_valid=structure_valid,
            signal_valid=signal_valid,
            spread_valid=spread_valid,
            liquidity_valid=liquidity_valid,
            risk_valid=risk_valid,
            leverage_valid=leverage_valid,
            broker_valid=broker_valid,
            system_armed=system_armed,
            reasons=reasons,
            strategy_info={"strategy": signal.get("strategy", "structure"), "score": signal.get("score", 0)}
        )

    async def scan_asset(self, symbol: str, semaphore: asyncio.Semaphore,
                         strategy_mode: Optional[str] = None) -> Dict[str, Any]:
        """Full analysis of a single asset with concurrency control."""
        async with semaphore:
            try:
                info = self.data.universe.get_info(symbol)
                if not info:
                    return {"symbol": symbol, "asset_class": "UNKNOWN", "status": "UNKNOWN_SYMBOL",
                            "tradable": False, "reason": "Not in universe",
                            "realtime_source": False}

                # Parallel data fetch: LTF, HTF, ticker, orderbook, trades
                # (hard timeout so one hung provider can never stall the whole scan)
                df_ltf, df_htf, ticker, orderbook, trades = await asyncio.wait_for(asyncio.gather(
                    self.data.fetch_ohlcv(symbol, timeframe='1m', limit=50),
                    self.data.fetch_ohlcv(symbol, timeframe='15m', limit=30),
                    self.data.fetch_ticker(symbol),
                    self.data.fetch_order_book(symbol),
                    self.data.fetch_trades(symbol),
                ), timeout=30.0)

                # Cross quotes for arbitrage (crypto only)
                cross_quotes = None
                if info.get("asset_class") == "CRYPTO":
                    try:
                        cross_quotes = await asyncio.wait_for(
                            self.data.fetch_cross_quotes(symbol), timeout=15.0)
                    except asyncio.TimeoutError:
                        cross_quotes = None

                if df_ltf.empty or not ticker:
                    return {
                        "symbol": symbol,
                        "asset_class": info.get("asset_class"),
                        "status": "DATA_UNAVAILABLE",
                        "tradable": False,
                        "reason": "Missing data",
                        "realtime_source": self.data.is_realtime_capable(symbol),
                        "diagnosis": {
                            "main_blocker": "DATA_VALID",
                            "main_reason": "No market data available",
                            "checks": {"DATA_VALID": "FAIL"}
                        }
                    }

                # Market analysis (LTF + HTF bias)
                htf_analysis = self.analysis.identify_structure(df_htf)
                ltf_analysis = self.analysis.identify_structure(df_ltf, htf_bias=htf_analysis.get("trend"))
                ltf_analysis["market_id"] = symbol

                # News risk
                asset_currency = None
                if info.get("asset_class") == "FOREX":
                    asset_currency = info["display_symbol"].split('/')[0]
                try:
                    news_status = await asyncio.wait_for(
                        self.news.check_trading_allowed(
                            asset_currency=asset_currency, asset_class=info.get("asset_class")),
                        timeout=10.0)
                except asyncio.TimeoutError:
                    # Fail-safe: block trading when the calendar cannot be checked
                    news_status = {"trading_allowed": False, "day_ok": True, "session_ok": True,
                                   "news_ok": False, "blocking_event": {"title": "Calendar timeout"},
                                   "next_events": [], "status": "DATA_UNAVAILABLE"}

                # Ranking score (news bypassed for ranking only)
                scoring_news = news_status.copy()
                scoring_news["trading_allowed"] = True
                raw_signal = self.signal.generate_signal(
                    ltf_analysis, scoring_news, df_ltf, strategy_mode=strategy_mode,
                    market_id=symbol, cross_quotes=cross_quotes, orderbook=orderbook, trades=trades)

                # Real signal (all filters applied)
                signal = self.signal.generate_signal(
                    ltf_analysis, news_status, df_ltf, strategy_mode=strategy_mode,
                    market_id=symbol, cross_quotes=cross_quotes, orderbook=orderbook, trades=trades)

                # Attach display symbol so the execution layer can route and display
                signal["display_symbol"] = info.get("display_symbol")

                diagnosis = self._build_diagnosis(symbol, info, ticker, df_ltf, ltf_analysis,
                                                  news_status, signal)

                data_age_ms = None
                if isinstance(ticker.get("timestamp"), (int, float)):
                    data_age_ms = max(0, int(datetime.now().timestamp() * 1000) - int(ticker["timestamp"]))

                return {
                    "symbol": symbol,
                    "asset_class": info.get("asset_class"),
                    "name": info.get("name"),
                    "price": float(ticker.get("last", 0)),
                    "change": float(ticker.get("change_24h", 0) or 0),
                    "spread": float(ticker.get("spread", 0) or 0),
                    "volume": float(ticker.get("volume", 0) or 0),
                    "status": ticker.get("status"),
                    "data_age_ms": data_age_ms,
                    "realtime_source": self.data.is_realtime_capable(symbol),
                    "trend": ltf_analysis.get("trend"),
                    "structure": ltf_analysis.get("is_hh") and "HH/HL" or
                                 (ltf_analysis.get("is_ll") and "LH/LL" or "Neutral"),
                    "market_state": ltf_analysis.get("market_state"),
                    "volatility": ltf_analysis.get("volatility"),
                    "market_status": self.data.universe.get_market_status(symbol),
                    "news_risk": "High" if not news_status["news_ok"] else "Low",
                    "signal": signal.get("status"),
                    "score": int(raw_signal.get("score", 0)),
                    "tradable": signal.get("status") == "SIGNAL_DETECTED",
                    "reason": signal.get("reason", ltf_analysis.get("market_state")),
                    "signal_data": signal,
                    "diagnosis": diagnosis
                }
            except Exception as e:
                logger.warning(f"Scanner Error ({symbol}): {e}")
                return {"symbol": symbol, "status": "ERROR", "tradable": False, "reason": str(e)}

    async def scan_all(self, strategy_mode: Optional[str] = None) -> List[Dict[str, Any]]:
        start_time = datetime.now()
        symbols = self.data.universe.get_all_ids()
        semaphore = asyncio.Semaphore(self.max_concurrent)
        tasks = [self.scan_asset(s, semaphore, strategy_mode=strategy_mode) for s in symbols]
        results = await asyncio.gather(*tasks)
        self.last_scan_duration = (datetime.now() - start_time).total_seconds()
        return results
