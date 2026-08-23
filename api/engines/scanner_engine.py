import asyncio
import inspect
import pandas as pd
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

from .diagnostic_engine import DiagnosticEngine
from .provider_capabilities import classify_quote_status, looks_like_quota_error
from .scan_contract import placeholder_row

logger = logging.getLogger("ScannerEngine")


class ScannerEngine:
    """
    Market Scanner: analyzes the whole universe with concurrency control
    and attaches a full diagnostic to every non-tradable result.

    The automatic path is RSI-only. RSI needs OHLCV + ticker — never an
    order book, recent trades or cross-quotes. One market error cannot
    stop the rest of the scan.
    """

    def __init__(self, data_engine: Any, analysis_engine: Any, signal_engine: Any,
                 news_engine: Any, max_concurrent: int = 8):
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
        strategy_name = str(signal.get("strategy") or "").lower()
        is_rsi_strategy = strategy_name == "rsi"
        # RSI is a reversal strategy, not a trend/structure strategy. Keep the
        # execution and market-safety checks below, but do not make RSI fail
        # merely because the market is ranging or lacks HH/HL structure.
        not_range = True if is_rsi_strategy else ltf_analysis.get("market_state") != "RANGE"
        trend_valid = True if is_rsi_strategy else ltf_analysis.get("trend") not in (None, "NEUTRAL")
        structure_valid = True if is_rsi_strategy else bool(ltf_analysis.get("is_hh") or ltf_analysis.get("is_ll"))
        signal_valid = signal.get("status") == "SIGNAL_DETECTED"
        spread = float((ticker or {}).get("spread", 0) or 0)
        last = float((ticker or {}).get("last", 0) or 0)
        spread_pct = (spread / last * 100) if last else 0.0
        spread_valid = (not ticker) or spread_pct <= self.max_spread_pct
        volume = float((ticker or {}).get("volume", 0) or 0)
        # RSI may trade markets whose free feed has no usable volume (spot FX).
        # Zero volume is not liquidity when a last price exists.
        if is_rsi_strategy:
            liquidity_valid = bool(ticker and last > 0)
        else:
            liquidity_valid = volume > 0
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
            strategy_info={"strategy": signal.get("strategy", "rsi"), "score": signal.get("score", 0)}
        )

    async def _safe_fetch(self, coro, default, label: str, symbol: str):
        try:
            result = await coro
            if isinstance(result, Exception):
                raise result
            return result
        except asyncio.TimeoutError:
            logger.warning("Scanner timeout (%s/%s)", symbol, label)
            return default
        except Exception as exc:
            if looks_like_quota_error(exc):
                logger.warning("Provider quota exceeded (%s/%s)", symbol, label)
            else:
                logger.debug("Provider failed (%s/%s): %s", symbol, label, exc)
            return default

    async def scan_asset(self, symbol: str, semaphore: asyncio.Semaphore,
                         strategy_mode: Optional[str] = None) -> Dict[str, Any]:
        """Full analysis of one asset; automatic scans are RSI-only in v2.9."""
        del strategy_mode
        strategy_mode = "rsi"
        async with semaphore:
            info = self.data.universe.get_info(symbol) if self.data and self.data.universe else None
            if not info:
                return {"symbol": symbol, "asset_class": "UNKNOWN", "status": "UNKNOWN_SYMBOL",
                        "tradable": False, "reason": "Not in universe",
                        "strategy": "rsi", "signal": "NO_TRADE",
                        "realtime_source": False, "block_reason": "DATA_UNAVAILABLE"}

            try:
                # RSI path: OHLCV + ticker only. Order book / trades / cross
                # quotes are unused and would burn free-tier quota.
                fetched = await asyncio.wait_for(asyncio.gather(
                    self._safe_fetch(
                        self.data.fetch_ohlcv(symbol, timeframe="1m", limit=60),
                        pd.DataFrame(), "ohlcv", symbol),
                    self._safe_fetch(
                        self.data.fetch_ticker(symbol),
                        None, "ticker", symbol),
                    return_exceptions=True,
                ), timeout=15.0)
                df_ltf = fetched[0] if not isinstance(fetched[0], Exception) else pd.DataFrame()
                ticker = fetched[1] if not isinstance(fetched[1], Exception) else None
                if not isinstance(df_ltf, pd.DataFrame):
                    df_ltf = pd.DataFrame()

                if df_ltf.empty or not ticker:
                    row = placeholder_row(
                        symbol, info,
                        status="DATA_UNAVAILABLE",
                        reason="Missing data",
                        block_reason="DATA_UNAVAILABLE",
                    )
                    row["diagnosis"] = {
                        "main_blocker": "DATA_VALID",
                        "main_reason": "No market data available",
                        "checks": {"DATA_VALID": "FAIL"},
                    }
                    return row

                classified = classify_quote_status(
                    ticker,
                    ticker.get("source") or getattr(self.data.layer, "market_source_state", {}).get(symbol, {}).get("provider_id"),
                )
                status = classified["status"]

                ltf_analysis: Dict[str, Any] = {"trend": "NEUTRAL", "market_id": symbol}
                if self.analysis is not None:
                    try:
                        ltf_analysis = self.analysis.identify_structure(df_ltf)
                        ltf_analysis["market_id"] = symbol
                    except Exception as exc:
                        logger.debug("Structure analysis skipped (%s): %s", symbol, exc)

                asset_currency = None
                if info.get("asset_class") == "FOREX":
                    asset_currency = info["display_symbol"].split("/")[0]
                try:
                    news_status = await asyncio.wait_for(
                        self.news.check_trading_allowed(
                            asset_currency=asset_currency, asset_class=info.get("asset_class")),
                        timeout=10.0)
                except asyncio.TimeoutError:
                    logger.warning("Calendar timeout (%s)", symbol)
                    if hasattr(self.news, "unavailable_status"):
                        news_status = self.news.unavailable_status(
                            asset_class=info.get("asset_class"), title="Calendar timeout")
                    else:
                        news_status = {"trading_allowed": False, "day_ok": True,
                                       "session_ok": True, "news_ok": False,
                                       "blocking_event": {"title": "Calendar timeout"},
                                       "next_events": [], "status": "DATA_UNAVAILABLE"}

                signal = self.signal.generate_signal(
                    ltf_analysis, news_status, df_ltf, strategy_mode=strategy_mode,
                    market_id=symbol)
                signal["display_symbol"] = info.get("display_symbol")

                diagnosis = self._build_diagnosis(symbol, info, ticker, df_ltf, ltf_analysis,
                                                  news_status, signal)

                data_age_ms = classified.get("data_age_ms")
                if data_age_ms is None and isinstance(ticker.get("timestamp"), (int, float)):
                    data_age_ms = max(0, int(datetime.now().timestamp() * 1000) - int(ticker["timestamp"]))

                if hasattr(self.data, "is_quote_realtime"):
                    realtime = bool(self.data.is_quote_realtime(symbol, ticker))
                elif hasattr(self.data, "is_realtime_capable"):
                    realtime = bool(
                        self.data.is_realtime_capable(symbol) and classified.get("realtime")
                    )
                else:
                    realtime = bool(classified.get("realtime"))
                block_reason = signal.get("block_reason") or diagnosis.get("main_blocker")
                if news_status.get("status") == "DATA_UNAVAILABLE" and not news_status.get("news_ok", True):
                    block_reason = "CALENDAR_UNAVAILABLE"
                elif not realtime:
                    # Delayed quotes stay visible but are never auto-tradable.
                    if signal.get("status") == "SIGNAL_DETECTED":
                        block_reason = "NON_REALTIME_SOURCE"

                tradable = (
                    signal.get("status") == "SIGNAL_DETECTED"
                    and signal.get("tradable", True) is not False
                    and realtime
                )

                return {
                    "symbol": symbol,
                    "display_symbol": info.get("display_symbol"),
                    "strategy": "rsi",
                    "direction": signal.get("direction") or ltf_analysis.get("trend"),
                    "asset_class": info.get("asset_class"),
                    "name": info.get("name"),
                    "price": float(ticker.get("last", 0) or 0),
                    "change": float(ticker.get("change_24h", 0) or 0),
                    "spread": float(ticker.get("spread", 0) or 0),
                    "volume": float(ticker.get("volume", 0) or 0),
                    "status": status,
                    "data_age_ms": data_age_ms,
                    "realtime_source": bool(realtime),
                    "active_source": ticker.get("source"),
                    "underlying": info.get("underlying", symbol),
                    "trend": ltf_analysis.get("trend"),
                    "structure": ltf_analysis.get("is_hh") and "HH/HL" or
                                 (ltf_analysis.get("is_ll") and "LH/LL" or "Neutral"),
                    "market_state": ltf_analysis.get("market_state"),
                    "volatility": ltf_analysis.get("volatility"),
                    "market_status": self.data.universe.get_market_status(symbol),
                    "news_risk": "High" if not news_status.get("news_ok", True) else "Low",
                    "signal": signal.get("status") or "NO_TRADE",
                    "score": int(signal.get("score", 0) or 0),
                    "tradable": bool(tradable),
                    "reason": signal.get("reason", ltf_analysis.get("market_state")),
                    "block_reason": block_reason,
                    "signal_data": signal,
                    "diagnosis": diagnosis,
                    "unavailable_policy": news_status.get("unavailable_policy"),
                }
            except Exception as e:
                logger.warning("Scanner Error (%s): %s", symbol, e)
                row = placeholder_row(
                    symbol, info, status="ERROR", reason=str(e),
                    block_reason="PROVIDER_ERROR" if not looks_like_quota_error(e) else "PROVIDER_QUOTA_EXCEEDED",
                )
                return row

    async def scan_all(self, strategy_mode: Optional[str] = None,
                       progress_callback: Any = None) -> List[Dict[str, Any]]:
        """Scan incrementally, always completing realtime crypto before tradfi.

        Automatic scanning is deliberately RSI-only; the argument is retained
        for API compatibility with older callers. ``progress_callback`` receives ``(result, completed, total)`` after every
        symbol.  It may be synchronous or asynchronous; this keeps partial
        results visible while a slow Yahoo class is still being processed.
        """
        del strategy_mode
        strategy_mode = "rsi"
        start_time = datetime.now()
        symbols = self.data.universe.get_all_ids()
        crypto = [symbol for symbol in symbols
                  if (self.data.universe.get_info(symbol) or {}).get("asset_class") == "CRYPTO"]
        tradfi = [symbol for symbol in symbols if symbol not in set(crypto)]
        semaphore = asyncio.Semaphore(self.max_concurrent)
        results: List[Dict[str, Any]] = []
        completed = 0

        async def scan_phase(phase: List[str]) -> None:
            nonlocal completed
            if not phase:
                return
            if hasattr(self.data, "prepare_scan_cycle"):
                try:
                    await asyncio.wait_for(self.data.prepare_scan_cycle(phase), timeout=25.0)
                except Exception as exc:
                    logger.warning("prepare_scan_cycle failed: %s", exc)
            async def _one(symbol: str) -> Dict[str, Any]:
                try:
                    return await self.scan_asset(symbol, semaphore, strategy_mode=strategy_mode)
                except Exception as exc:
                    logger.warning("Scanner Error (%s): %s", symbol, exc)
                    info = self.data.universe.get_info(symbol) or {}
                    return placeholder_row(
                        symbol, info, status="ERROR", reason=str(exc),
                        block_reason="PROVIDER_ERROR",
                    )

            pending = {asyncio.create_task(_one(symbol)): symbol for symbol in phase}
            done_set, _ = await asyncio.wait(pending.keys())
            # Preserve crypto-first phase order while remaining resilient.
            by_symbol = {}
            for task in done_set:
                symbol = pending[task]
                try:
                    by_symbol[symbol] = task.result()
                except Exception as exc:
                    info = self.data.universe.get_info(symbol) or {}
                    by_symbol[symbol] = placeholder_row(
                        symbol, info, status="ERROR", reason=str(exc),
                        block_reason="PROVIDER_ERROR",
                    )
            for symbol in phase:
                result = by_symbol[symbol]
                results.append(result)
                completed += 1
                if progress_callback:
                    callback_result = progress_callback(result, completed, len(symbols))
                    if inspect.isawaitable(callback_result):
                        await callback_result

        await scan_phase(crypto)
        await scan_phase(tradfi)
        self.last_scan_duration = (datetime.now() - start_time).total_seconds()
        return results
