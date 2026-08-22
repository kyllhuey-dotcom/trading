"""
Quantum Trade Pro — Institutional Trading Application
=====================================================
Single entry point: FastAPI app + background trading loops + WebSocket bus.

v2.0 — Full API contract, authentication, live settings, real broker execution.
"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header, Depends, Body, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any

import os
import asyncio
import json
import logging
import time
from datetime import datetime

from api.json_logging import setup_json_file_handler, structured_log
from api.engines.exchange_constraints import normalize_order
from api.engines.metrics_engine import MetricsEngine
from api.rate_limit import SlidingWindowRateLimiter
from api.engines.db_manager import DatabaseManager
from api.engines.data_engine import DataEngine
from api.engines.analysis_engine import AnalysisEngine
from api.engines.news_engine import NewsEngine
from api.engines.signal_engine import SignalEngine
from api.engines.risk_engine import RiskEngine
from api.engines.execution_engine import ExecutionEngine
from api.engines.execution_router import ExecutionRouter
from api.engines.state_machine import BotState, StateMachine
from api.engines.portfolio_engine import PortfolioEngine
from api.engines.broker_connector import BrokerConnector
from api.engines.scanner_engine import ScannerEngine
from api.engines.diagnostic_engine import DiagnosticEngine
from api.engines.news_aggregator import NewsAggregator
from api.engines.notification_engine import NotificationEngine
from api.engines.backtest_engine import BacktestEngine

# --------------------------------------------------------------------------- #
# 1. Logging                                                                   #
# --------------------------------------------------------------------------- #
# Console stays human-readable; the rotating FILE handler is structured NDJSON
# so logs can be ingested by any JSON-capable tool (LOT A — observability).
os.makedirs("data", exist_ok=True)
console_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
console_handler = logging.StreamHandler()
console_handler.setFormatter(console_formatter)

json_file_handler = setup_json_file_handler(
    path=os.getenv("LOG_FILE", "data/trading_bot.jsonl"),
    max_bytes=int(os.getenv("LOG_MAX_BYTES", 5 * 1024 * 1024)),
    backup_count=int(os.getenv("LOG_BACKUP_COUNT", 5)),
)

root_logger = logging.getLogger()
root_logger.handlers.clear()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(console_handler)
root_logger.addHandler(json_file_handler)
logger = logging.getLogger("QuantumTradePro")

# --------------------------------------------------------------------------- #
# 2. Configuration                                                             #
# --------------------------------------------------------------------------- #
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")
TESTING = os.getenv("TESTING", "false").lower() == "true"
PORT = int(os.getenv("PORT", 8000))
# WebSocket heartbeat cadence (accelerated in tests so coverage stays fast)
HEARTBEAT_INTERVAL_S = float(os.getenv("HEARTBEAT_INTERVAL_S", "2.0" if TESTING else "15.0"))

# --------------------------------------------------------------------------- #
# 3. System core                                                               #
# --------------------------------------------------------------------------- #
REAL_MODE_WARNING = "Live execution still experimental – use DEMO for strategies"

app = FastAPI(title="Quantum Trade Pro", version="2.1.0", lifespan=None)

# Basic reinforced rate limiting (LOT H): sliding window per client IP,
# separate budgets for reads and mutations. Env-tunable.
rate_limiter = SlidingWindowRateLimiter(
    requests_per_minute=int(os.getenv("RATE_LIMIT_PER_MINUTE", "1200")),
    mutations_per_minute=int(os.getenv("RATE_LIMIT_MUTATIONS_PER_MINUTE", "300")),
    window_s=float(os.getenv("RATE_LIMIT_WINDOW_S", "60")),
)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path == "/healthz":
        return await call_next(request)
    client_id = request.client.host if request.client else "unknown"
    is_mutation = request.method in ("POST", "PUT", "PATCH", "DELETE")
    if not rate_limiter.allow(client_id, is_mutation):
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded — slow down",
                     "retry_after_s": int(rate_limiter.window_s)},
        )
    return await call_next(request)

db_manager = DatabaseManager()
data_engine = DataEngine()
analysis_engine = AnalysisEngine()
news_engine = NewsEngine()
news_aggregator = NewsAggregator()
signal_engine = SignalEngine(min_score=80)
risk_engine = RiskEngine()
portfolio_engine = PortfolioEngine(db_manager=db_manager)
notification_engine = NotificationEngine()
demo_execution = ExecutionEngine(portfolio=portfolio_engine, db_manager=db_manager,
                                 risk_engine=risk_engine, universe=data_engine.universe,
                                 notification_engine=notification_engine)
broker_connector = BrokerConnector(db_manager=db_manager)
broker_connector.universe = data_engine.universe
execution_router = ExecutionRouter(demo_adapter=demo_execution, broker_connector=broker_connector)
state_machine = StateMachine()
scanner_engine = ScannerEngine(data_engine, analysis_engine, signal_engine, news_engine)
diagnostic_engine = DiagnosticEngine()
backtest_engine = BacktestEngine(analysis_engine, signal_engine, risk_engine)

state_lock = asyncio.Lock()

_started_at = datetime.now()

bot_state: Dict[str, Any] = {
    "mode": "DEMO", "armed": False, "equity": 0.0, "balance": 0.0, "drawdown": 0.0,
    "is_running": False, "latest_scan": [], "active_trades": [], "best_setups": [],
    "engine_stats": {"markets": 0, "scanned": 0, "signals": 0, "tradable": 0},
    "selected_market": "btc_usdt",
}

metrics_state: Dict[str, Any] = {
    "total_scans": 0, "total_trades": 0, "total_errors": 0,
    "signals_by_strategy": {"structure": 0, "arbitrage": 0, "tape": 0, "liquidity": 0},
    "start_time": _started_at.isoformat(),
}

# Advanced observability (LOT A): latency, data age, per-strategy outcome,
# REAL vs DEMO orders. Additive — the legacy counters above are untouched.
metrics_engine = MetricsEngine()

# --------------------------------------------------------------------------- #
# 4. Live settings (reloaded from DB with TTL)                                 #
# --------------------------------------------------------------------------- #
class SettingsProvider:
    def __init__(self, db: DatabaseManager, ttl: float = 5.0):
        self.db = db
        self.ttl = ttl
        self._cache: Dict[str, str] = {}
        self._ts = 0.0

    def get(self) -> Dict[str, str]:
        now = time.time()
        if now - self._ts > self.ttl:
            try:
                self._cache = self.db.get_settings()
                self._ts = now
            except Exception as e:
                logger.warning(f"Settings reload failed: {e}")
        return self._cache

    def apply(self) -> None:
        """Push DB settings into the engines (risk, signal, scanner)."""
        s = self.get()
        risk_engine.apply_settings(s)
        try:
            signal_engine.set_min_score(int(float(s.get("min_signal_score", 80))))
        except ValueError:
            pass
        strategies = [x.strip() for x in s.get("active_strategies", "structure").split(",") if x.strip()]
        signal_engine.set_active_strategies(strategies)
        scanner_engine.apply_settings(s)


settings_provider = SettingsProvider(db_manager)

# --------------------------------------------------------------------------- #
# 5. WebSocket connection manager                                              #
# --------------------------------------------------------------------------- #
class ConnectionManager:
    """WebSocket hub: per-client metadata, dead-client pruning, heartbeat (LOT A)."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.connection_meta: Dict[WebSocket, Dict[str, Any]] = {}
        self.heartbeat_seq = 0
        self.last_heartbeat_at: Optional[str] = None
        self._meta_counter = 0

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active_connections.append(ws)
        self._meta_counter += 1
        self.connection_meta[ws] = {
            "client_id": f"ws-{self._meta_counter:04d}",
            "connected_at": datetime.now().isoformat(),
            "messages_received": 0,
        }

    def disconnect(self, ws: WebSocket):
        if ws in self.active_connections:
            self.active_connections.remove(ws)
        self.connection_meta.pop(ws, None)

    def note_activity(self, ws: WebSocket):
        meta = self.connection_meta.get(ws)
        if meta:
            meta["messages_received"] += 1

    @property
    def client_count(self) -> int:
        return len(self.active_connections)

    def heartbeat_status(self) -> Dict[str, Any]:
        return {
            "seq": self.heartbeat_seq,
            "clients": self.client_count,
            "last_sent_at": self.last_heartbeat_at,
        }

    async def send_personal(self, ws: WebSocket, message: str) -> bool:
        try:
            await ws.send_text(message)
            return True
        except Exception:
            self.disconnect(ws)
            return False

    async def broadcast(self, message: str):
        dead = []
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                dead.append(connection)
        for ws in dead:
            self.disconnect(ws)

    async def broadcast_heartbeat(self) -> Dict[str, Any]:
        """Explicit server heartbeat: proves liveness even with zero market traffic."""
        self.heartbeat_seq += 1
        self.last_heartbeat_at = datetime.now().isoformat()
        payload = {
            "type": "HEARTBEAT",
            "seq": self.heartbeat_seq,
            "server_time": self.last_heartbeat_at,
            "timestamp_ms": int(time.time() * 1000),
            "clients": self.client_count,
            "state": state_machine.current_state.value,
        }
        await self.broadcast(json.dumps(payload))
        return payload


manager = ConnectionManager()
data_engine.set_ws_manager(manager)  # wire the market-data bus to the WS layer

# --------------------------------------------------------------------------- #
# 6. Authentication                                                            #
# --------------------------------------------------------------------------- #
async def require_admin(x_api_key: Optional[str] = Header(default=None)) -> None:
    """
    Protects all mutating endpoints. If ADMIN_API_KEY is unset (dev/demo),
    access is open but a warning is logged at startup.
    """
    if not ADMIN_API_KEY:
        return
    if x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key header")


# --------------------------------------------------------------------------- #
# 7. Background micro-loops                                                    #
# --------------------------------------------------------------------------- #
async def tick_capital():
    """1s — sync balances, equity, drawdown and global safety."""
    async with state_lock:
        mode = bot_state["mode"]
        settings_provider.apply()
        if mode == "REAL":
            try:
                balances = await broker_connector.get_all_balances()
                total = sum(b.get("total_usdt", 0.0) for b in balances.values()
                            if isinstance(b, dict) and b.get("type") == "BROKER")
                portfolio_engine.set_balance("REAL", total)
            except Exception as e:
                logger.error(f"Balance sync failed: {e}")

        bot_state["balance"] = portfolio_engine.get_balance(mode)
        active = demo_execution.active_positions if mode == "DEMO" else db_manager.get_active_positions("REAL")
        unrealized = sum(p.get("pnl", 0.0) or 0.0 for p in active)
        bot_state["equity"] = bot_state["balance"] + unrealized
        bot_state["active_trades"] = active

        daily_pnl = portfolio_engine.get_daily_pnl(mode)
        bot_state["daily_pnl"] = daily_pnl
        risk_engine.daily_pnl = daily_pnl
        bot_state["drawdown"] = risk_engine.get_current_drawdown_pct(bot_state["equity"])

        safety = risk_engine.check_global_safety(bot_state["equity"], daily_pnl)
        if not safety["safe"]:
            logger.error(f"GLOBAL RISK LIMIT: {safety['reason']}")
            await emergency_stop_logic(safety["reason"])


_scan_counter = {"n": 0}

async def tick_scanner():
    """5s — rescan the universe (fast cadence when running, slow when idle) and execute signals."""
    _scan_counter["n"] += 1
    settings = settings_provider.get()
    try:
        interval = max(5, int(float(settings.get("scan_interval_seconds", "20"))))
    except ValueError:
        interval = 20

    every = max(1, interval // 5) if bot_state["is_running"] else 12  # 60s refresh when idle
    if _scan_counter["n"] % every != 0:
        return

    results = await scanner_engine.scan_all()
    async with state_lock:
        metrics_state["total_scans"] += 1
        metrics_engine.record_scan(scanner_engine.last_scan_duration, results)
        bot_state["latest_scan"] = results
        bot_state["engine_stats"].update({
            "markets": len(results),
            "scanned": len([r for r in results if r.get("status") == "LIVE"]),
            "signals": len([r for r in results if r.get("score", 0) >= 80]),
            "tradable": len([r for r in results if r.get("tradable")]),
        })
        bot_state["best_setups"] = sorted(results, key=lambda r: r.get("score", 0), reverse=True)[:5]
        armed = bot_state["armed"] and bot_state["is_running"]
        active = list(bot_state["active_trades"])
        mode = bot_state["mode"]
        balance = bot_state["balance"]

    try:
        await manager.broadcast(json.dumps({
            "type": "SCAN_COMPLETED",
            "duration_s": scanner_engine.last_scan_duration,
            "stats": bot_state["engine_stats"],
        }))
    except Exception:
        pass

    if not armed:
        return

    for res in results:
        if not res.get("tradable"):
            continue
        if len(active) >= risk_engine.max_open_positions:
            break
        if any(p["symbol"] == res["symbol"] for p in active):
            continue

        sig = res.get("signal_data") or {}
        if not sig.get("market_id") or not sig.get("entry"):
            continue

        info = data_engine.universe.get_info(res["symbol"]) or {}
        ticker = await data_engine.fetch_ticker(res["symbol"])
        if not ticker:
            continue
        if not data_engine.is_fresh(ticker, info.get("asset_class", "CRYPTO")):
            logger.warning(f"Stale ticker for {res['symbol']} — order skipped")
            continue

        # LOT F: never scalp delayed (non-realtime) data. Yahoo-sourced
        # instruments are blocked for automated execution unless explicitly
        # allowed in settings (allow_delayed_data_trading=true).
        allow_delayed = settings.get("allow_delayed_data_trading", "false").lower() == "true"
        scalp_guard = data_engine.check_scalping_allowed(res["symbol"], allow_delayed)
        if not scalp_guard["allowed"]:
            db_manager.archive_signal(sig, "BLOCKED", scalp_guard["reason"])
            metrics_engine.record_signal_blocked(sig.get("strategy", "structure"))
            structured_log(logger, logging.WARNING, "SCALPING_BLOCKED_DELAYED_DATA",
                           event="scalping_blocked_delayed_data", symbol=res["symbol"],
                           strategy=sig.get("strategy", "structure"))
            continue

        risk_data = risk_engine.calculate_position_size(
            balance, sig["entry"], sig["sl"], sig["direction"],
            symbol=res["symbol"], active_positions=active,
            market_info=info)
        strat = sig.get("strategy", "structure")
        if not risk_data.get("allowed"):
            db_manager.archive_signal(sig, "BLOCKED", risk_data.get("reason") or "risk")
            metrics_engine.record_signal_blocked(strat)
            continue

        exec_start = time.time()
        exec_res = await execution_router.execute(mode, sig, risk_data, ticker)
        exec_latency_ms = (time.time() - exec_start) * 1000.0
        if exec_res.get("success"):
            metrics_state["total_trades"] += 1
            metrics_state["signals_by_strategy"][strat] = metrics_state["signals_by_strategy"].get(strat, 0) + 1
            metrics_engine.record_execution(strat, mode, success=True, latency_ms=exec_latency_ms)
            db_manager.archive_signal(sig, "EXECUTED", "")
            structured_log(logger, logging.INFO, "ORDER_EXECUTED",
                           event="order_executed", symbol=res["symbol"], mode=mode,
                           strategy=strat, latency_ms=round(exec_latency_ms, 2))
            asyncio.create_task(notification_engine.notify("ORDER_OPEN", {
                "symbol": res["symbol"],
                "entry_price": sig["entry"],
                "quantity": risk_data["quantity"],
            }))
        else:
            metrics_engine.record_execution(strat, mode, success=False, latency_ms=exec_latency_ms)
            db_manager.archive_signal(sig, "BLOCKED", exec_res.get("reason") or "execution")
            logger.warning(f"Execution blocked for {res['symbol']}: {exec_res.get('reason')}")


async def tick_management():
    """1s — update positions (SL/TP/trailing in DEMO, reconciliation in REAL)."""
    mode = bot_state["mode"]
    active = bot_state["active_trades"]
    if not active:
        return

    symbols = [t["symbol"] for t in active]
    quotes = await data_engine.layer.get_all_quotes(symbols, data_engine.universe)
    tickers = {q.symbol: q.model_dump() for q in quotes}

    if mode == "DEMO":
        await demo_execution.update_active_positions(mode, tickers)
        async with state_lock:
            bot_state["active_trades"] = demo_execution.active_positions
    else:
        # REAL: never fake-close locally — close only via broker reconciliation.
        await broker_connector.reconcile_positions()
        for t in db_manager.get_active_positions("REAL"):
            ticker = tickers.get(t["display_symbol"]) or tickers.get(t["symbol"])
            if not ticker:
                continue
            px = (ticker.get("bid") if t["direction"] == "BUY" else ticker.get("ask")) or ticker.get("last")
            if not px:
                continue
            if t["direction"] == "BUY":
                t["pnl"] = (px - t["entry_price"]) * t["quantity"]
            else:
                t["pnl"] = (t["entry_price"] - px) * t["quantity"]
            db_manager.save_trade(t)
        async with state_lock:
            bot_state["active_trades"] = db_manager.get_active_positions("REAL")


async def tick_broadcaster():
    """1s — push account state + selected market price to WebSocket clients."""
    payload = {
        "type": "ACCOUNT_STREAM",
        "timestamp": int(datetime.now().timestamp() * 1000),
        "status": state_machine.current_state.value,
        "balance": bot_state["balance"],
        "equity": bot_state["equity"],
        "daily_pnl": bot_state.get("daily_pnl", 0.0),
        "drawdown": bot_state["drawdown"],
        "active_trades": bot_state["active_trades"],
        "is_running": bot_state["is_running"],
        "armed": bot_state["armed"],
        "mode": bot_state["mode"],
        "stats": bot_state["engine_stats"],
    }
    await manager.broadcast(json.dumps(payload))
    try:
        await data_engine.broadcast_market_update(bot_state["selected_market"])
    except Exception:
        pass


async def loop_wrapper(func, interval: float, name: str):
    while True:
        try:
            await func()
        except Exception as e:
            metrics_state["total_errors"] += 1
            metrics_engine.record_error()
            structured_log(logger, logging.ERROR, "LOOP_ERROR",
                           event="loop_error", loop=name, error=str(e))
        await asyncio.sleep(interval)


async def tick_heartbeat():
    """15s — explicit WebSocket heartbeat + dead-client pruning (LOT A)."""
    await manager.broadcast_heartbeat()


async def emergency_stop_logic(reason: str = "Manual trigger"):
    async with state_lock:
        bot_state["is_running"] = False
        bot_state["armed"] = False
    state_machine.transition_to(BotState.EMERGENCY_STOP)
    demo_execution.clear_active_positions(bot_state["mode"])
    real_close = await broker_connector.close_all_positions()
    broker_connector.trigger_emergency_stop()
    db_manager.log_audit("CRITICAL", "EMERGENCY_STOP", reason, {"real_close": real_close})
    await notification_engine.notify("EMERGENCY_STOP", {"reason": reason})
    logger.critical(f"EMERGENCY STOP EXECUTED: {reason}")


# --------------------------------------------------------------------------- #
# 8. Market snapshot (cached, single-flight)                                   #
# --------------------------------------------------------------------------- #
_snapshot_cache: Dict[str, tuple] = {}
_snapshot_lock = asyncio.Lock()
SNAPSHOT_TTL = 12.0


def _empty_snapshot(market_id: str, status_display: str) -> Dict[str, Any]:
    return {
        "status_display": status_display,
        "asset_info": data_engine.universe.get_info(market_id),
        "ticker": None,
        "news": {"trading_allowed": False, "news_ok": False, "session_ok": False, "day_ok": False,
                 "blocking_event": None, "next_events": [], "status": status_display},
        "analysis": None,
        "signal": {"status": "NO_TRADE", "reason": status_display, "score": 0, "market_id": market_id},
        "diagnosis": {
            "main_blocker": "DATA_VALID",
            "main_reason": status_display,
            # Full check contract even when no data is available (offline-safe):
            # non-evaluable checks are reported as FAIL, evaluation-dependent
            # checks are reported as VALIDATED_AT_EXECUTION.
            "checks": {
                "DATA_VALID": "FAIL",
                "DAY_ALLOWED": "FAIL",
                "SESSION_ALLOWED": "FAIL",
                "NEWS_CLEAR": "FAIL",
                "MARKET_OPEN": "FAIL",
                "NOT_RANGE": "FAIL",
                "TREND_VALID": "FAIL",
                "STRUCTURE_VALID": "FAIL",
                "SIGNAL_VALID": "FAIL",
                "SPREAD_VALID": "FAIL",
                "LIQUIDITY_VALID": "FAIL",
                "RISK_VALID": "Validated at execution time",
                "LEVERAGE_VALID": "Validated at execution time",
                "BROKER_VALID": "Validated at execution time",
                "SYSTEM_ARMED": "Validated at execution time",
            },
            "secondary_blockers": [],
        },
    }


async def _build_snapshot(market_id: str) -> Dict[str, Any]:
    info = data_engine.universe.get_info(market_id)
    if not info:
        return _empty_snapshot(market_id, "DATA ERROR")

    try:
        df_ltf, df_htf, ticker = await asyncio.wait_for(asyncio.gather(
            data_engine.fetch_ohlcv(market_id, timeframe='1m', limit=50),
            data_engine.fetch_ohlcv(market_id, timeframe='15m', limit=30),
            data_engine.fetch_ticker(market_id),
        ), timeout=20.0)
        asset_currency = info["display_symbol"].split('/')[0] if info.get("asset_class") == "FOREX" else None
        news_status = await asyncio.wait_for(
            news_engine.check_trading_allowed(asset_currency=asset_currency,
                                              asset_class=info.get("asset_class")),
            timeout=10.0)
    except asyncio.TimeoutError:
        logger.warning(f"Snapshot fetch timeout ({market_id})")
        return _empty_snapshot(market_id, "DATA ERROR")
    except Exception as e:
        logger.warning(f"Snapshot fetch error ({market_id}): {e}")
        return _empty_snapshot(market_id, "DATA ERROR")

    if not ticker:
        return _empty_snapshot(market_id, "DATA ERROR")

    htf_analysis = analysis_engine.identify_structure(df_htf) if not df_htf.empty else {"trend": "NEUTRAL"}
    ltf_analysis = analysis_engine.identify_structure(df_ltf, htf_bias=htf_analysis.get("trend"))
    ltf_analysis["market_id"] = market_id

    signal = signal_engine.generate_signal(ltf_analysis, news_status, df_ltf, market_id=market_id)
    signal["display_symbol"] = info.get("display_symbol")

    diagnosis = scanner_engine._build_diagnosis(market_id, info, ticker, df_ltf,
                                                ltf_analysis, news_status, signal)
    status_display = "ONLINE" if ticker.get("status") in ("LIVE", "DELAYED") else "DEGRADED"

    return {
        "status_display": status_display,
        "asset_info": info,
        "ticker": ticker,
        "news": news_status,
        "analysis": ltf_analysis,
        "signal": signal,
        "diagnosis": diagnosis,
    }


async def get_market_snapshot(market_id: str) -> Dict[str, Any]:
    now = time.time()
    cached = _snapshot_cache.get(market_id)
    if cached and now - cached[0] < SNAPSHOT_TTL:
        return cached[1]
    async with _snapshot_lock:
        cached = _snapshot_cache.get(market_id)
        if cached and now - cached[0] < SNAPSHOT_TTL:
            return cached[1]
        snapshot = await _build_snapshot(market_id)
        _snapshot_cache[market_id] = (time.time(), snapshot)
        return snapshot


# --------------------------------------------------------------------------- #
# 9. API endpoints                                                            #
# --------------------------------------------------------------------------- #
@app.get("/healthz")
async def healthz():
    return {
        "status": "OK",
        "state": state_machine.current_state.value,
        "uptime_s": int((datetime.now() - _started_at).total_seconds()),
    }


@app.get("/api/status")
async def get_status(market_id: str = "btc_usdt"):
    bot_state["selected_market"] = market_id
    snapshot = await get_market_snapshot(market_id)
    mode = bot_state["mode"]
    return {
        "status": state_machine.current_state.value,
        "status_display": snapshot["status_display"],
        "is_running": bot_state["is_running"],
        "mode": mode,
        "real_warning": REAL_MODE_WARNING if mode == "REAL" else None,
        "armed": bot_state["armed"],
        "balance": bot_state["balance"],
        "equity": bot_state["equity"],
        "daily_pnl": bot_state.get("daily_pnl", 0.0),
        "drawdown": bot_state["drawdown"],
        "demo_balance": portfolio_engine.get_balance("DEMO"),
        "real_balance": portfolio_engine.get_balance("REAL"),
        "selected_market": market_id,
        "asset_info": snapshot["asset_info"],
        "ticker": snapshot["ticker"],
        "news": snapshot["news"],
        "analysis": snapshot["analysis"],
        "signal": snapshot["signal"],
        "diagnosis": snapshot["diagnosis"],
        "active_trades": bot_state["active_trades"],
        "history": db_manager.get_history(mode=mode, limit=20),
        "stats": bot_state["engine_stats"],
        "performance": portfolio_engine.get_stats(mode),
        "broker_info": broker_connector.get_status(),
        "broker_connected": broker_connector.get_status()["broker_count"] > 0,
        "best_setups": bot_state["best_setups"],
    }


@app.get("/api/history")
async def get_history(mode: str = "DEMO", limit: int = 100):
    return db_manager.get_history(mode=mode, limit=limit)


@app.get("/api/scanner")
async def get_scanner():
    return {"assets": bot_state["latest_scan"], "duration_s": scanner_engine.last_scan_duration}


@app.get("/api/markets")
async def get_markets():
    overview = await data_engine.get_market_overview()
    for cat, items in overview.items():
        for item in items:
            item["price"] = item.get("last")
    return overview


@app.get("/api/settings")
async def get_settings():
    return db_manager.get_settings()


@app.post("/api/settings", dependencies=[Depends(require_admin)])
async def save_settings(new_settings: Dict[str, str] = Body(...)):
    db_manager.save_settings(new_settings)
    settings_provider.apply()
    db_manager.log_audit("INFO", "SETTINGS_UPDATED", f"Updated {len(new_settings)} settings")
    return {"success": True}


@app.post("/api/start", dependencies=[Depends(require_admin)])
async def start_bot():
    async with state_lock:
        bot_state["is_running"] = True
    state_machine.transition_to(BotState.RUNNING)
    db_manager.log_audit("INFO", "SYSTEM_START", "Bot started")
    return {"success": True, "state": state_machine.current_state.value}


@app.post("/api/stop", dependencies=[Depends(require_admin)])
async def stop_bot():
    async with state_lock:
        bot_state["is_running"] = False
    state_machine.transition_to(BotState.STOPPED)
    db_manager.log_audit("INFO", "SYSTEM_STOP", "Bot stopped")
    return {"success": True, "state": state_machine.current_state.value}


@app.post("/api/arm", dependencies=[Depends(require_admin)])
async def arm_bot():
    async with state_lock:
        bot_state["armed"] = not bot_state["armed"]
        armed = bot_state["armed"]
    db_manager.log_audit("INFO", "SYSTEM_ARM", f"System armed state: {armed}")
    return {"armed": armed}


@app.post("/api/mode", dependencies=[Depends(require_admin)])
async def toggle_mode():
    target = "REAL" if bot_state["mode"] == "DEMO" else "DEMO"
    ok, msg = await broker_connector.set_mode(target)
    if not ok:
        return {"success": False, "mode": bot_state["mode"], "message": msg}
    async with state_lock:
        bot_state["mode"] = target
    db_manager.log_audit("WARNING" if target == "REAL" else "INFO", "MODE_CHANGE",
                         f"Mode switched to {target}: {msg}")
    if target == "REAL":
        # LOT H: explicit, impossible-to-miss warning on live mode
        structured_log(logger, logging.WARNING, "MODE_SWITCHED_TO_REAL",
                       event="mode_switched_to_real", warning=REAL_MODE_WARNING)
        return {"success": True, "mode": target, "message": msg,
                "warning": REAL_MODE_WARNING}
    return {"success": True, "mode": target, "message": msg}


@app.post("/api/emergency-stop", dependencies=[Depends(require_admin)])
async def emergency_stop_api():
    await emergency_stop_logic("Manual emergency stop from UI")
    return {"success": True, "state": state_machine.current_state.value}


@app.post("/api/emergency-reset", dependencies=[Depends(require_admin)])
async def emergency_reset():
    broker_connector.reset_emergency_stop()
    async with state_lock:
        bot_state["armed"] = False
        bot_state["is_running"] = False
    state_machine.transition_to(BotState.STOPPED)
    db_manager.log_audit("INFO", "EMERGENCY_RESET", "Emergency stop reset — system idle")
    return {"success": True, "state": state_machine.current_state.value}


@app.post("/api/order", dependencies=[Depends(require_admin)])
async def manual_order(body: Dict[str, Any] = Body(...)):
    """Manual market order from the trading terminal."""
    market_id = body.get("market_id")
    direction = str(body.get("direction", "BUY")).upper()
    if direction not in ("BUY", "SELL"):
        raise HTTPException(400, "direction must be BUY or SELL")
    if not market_id or not data_engine.universe.get_info(market_id):
        raise HTTPException(400, f"Unknown market_id: {market_id}")

    ticker = await data_engine.fetch_ticker(market_id)
    if not ticker:
        return {"success": False, "reason": "No market data available"}

    entry = float(ticker.get("last") or 0)
    if entry <= 0:
        return {"success": False, "reason": "Invalid price"}

    # SL / TP defaults: 1.5 ATR stop, 2R target
    atr = 0.0
    sl = body.get("sl")
    tp = body.get("tp")
    if not sl or not tp:
        try:
            df = await data_engine.fetch_ohlcv(market_id, timeframe='1m', limit=50)
            if not df.empty:
                tr = pd_concat_tr(df)
                atr = float(tr.tail(14).mean()) if len(tr) >= 14 else 0.0
        except Exception:
            pass
    if not sl:
        if atr > 0:
            sl = entry - (atr * 1.5) if direction == "BUY" else entry + (atr * 1.5)
        else:
            sl = entry * (0.98 if direction == "BUY" else 1.02)
    if not tp:
        dist = abs(entry - sl) or (entry * 0.01)
        tp = entry + (dist * 2.0) if direction == "BUY" else entry - (dist * 2.0)
    sl, tp = float(sl), float(tp)

    quantity = float(body.get("quantity", 0))
    if quantity <= 0:
        return {"success": False, "reason": "quantity must be positive"}

    # Risk validation (configurable override for manual trades)
    dist = abs(entry - sl)
    risk_amount = quantity * dist
    max_risk_amount = bot_state["balance"] * (risk_engine.max_risk_pct / 100)
    if risk_amount > max_risk_amount and not body.get("override_risk"):
        return {"success": False,
                "reason": f"Risk {risk_amount:.2f} exceeds max {max_risk_amount:.2f} "
                          f"({risk_engine.max_risk_pct}% of balance). Lower quantity or enable override_risk."}

    info = data_engine.universe.get_info(market_id) or {}
    signal = {
        "market_id": market_id,
        "display_symbol": info.get("display_symbol", market_id),
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "atr": atr,
        "score": 100,
        "strategy": "manual",
        "setup_type": "MANUAL_ORDER",
        "timestamp": datetime.now().isoformat(),
    }
    notional = quantity * entry
    risk_data = {
        "allowed": True,
        "quantity": quantity,
        "leverage": min(notional / bot_state["balance"], risk_engine.max_leverage) if bot_state["balance"] > 0 else 1.0,
        "estimated_fees": notional * 0.001,
    }

    # LOT E: exchange-aware normalization for manual orders too — the broker
    # rejects lot/tick violations, so floor the quantity and round prices
    # before routing (both modes, for DEMO realism and REAL safety).
    normalized = normalize_order(quantity, entry, direction,
                                 sl=sl, tp=tp, info=info)
    if not normalized["allowed"]:
        return {"success": False, "reason": normalized["reason"] or "Exchange constraint violation"}
    risk_data["quantity"] = normalized["quantity"]
    risk_data["leverage"] = min(normalized["notional"] / bot_state["balance"],
                                risk_engine.max_leverage) if bot_state["balance"] > 0 else 1.0
    risk_data["estimated_fees"] = normalized["notional"] * 0.001
    signal["entry"] = normalized["entry"]
    signal["sl"] = normalized["sl"]
    signal["tp"] = normalized["tp"]
    if normalized["adjusted"]:
        risk_data["quantity_rounded"] = True
        risk_data["adjustments"] = normalized["adjustments"]

    exec_start = time.time()
    res = await execution_router.execute(bot_state["mode"], signal, risk_data, ticker)
    metrics_engine.record_execution("manual", bot_state["mode"], bool(res.get("success")),
                                    latency_ms=(time.time() - exec_start) * 1000.0)
    if res.get("success"):
        metrics_state["total_trades"] += 1
        db_manager.log_audit("INFO", "MANUAL_ORDER", f"Manual {direction} {market_id} qty={quantity}")
    return res


def pd_concat_tr(df):
    import pandas as pd
    high_low = df['High'] - df['Low']
    high_close = (df['High'] - df['Close'].shift()).abs()
    low_close = (df['Low'] - df['Close'].shift()).abs()
    return pd.concat([high_low, high_close, low_close], axis=1).max(axis=1).dropna()


@app.post("/api/demo/reset", dependencies=[Depends(require_admin)])
async def demo_reset():
    demo_execution.clear_active_positions("DEMO")
    portfolio_engine.reset_history("DEMO")
    portfolio_engine.set_balance("DEMO", 10000.0)
    risk_engine.daily_pnl = 0.0
    risk_engine.last_loss_time = None
    risk_engine.peak_balance = 10000.0
    db_manager.log_audit("INFO", "DEMO_RESET", "Demo account reset (balance 10 000, journal wiped)")
    return {"success": True, "balance": portfolio_engine.get_balance("DEMO")}


@app.post("/api/demo/balance", dependencies=[Depends(require_admin)])
async def demo_balance(body: Dict[str, Any] = Body(...)):
    amount = float(body.get("balance", 0))
    if amount < 0:
        raise HTTPException(400, "balance must be >= 0")
    portfolio_engine.set_balance("DEMO", amount)
    risk_engine.update_peak(amount)
    db_manager.log_audit("INFO", "DEMO_BALANCE", f"Demo balance provisioned to {amount}")
    return {"success": True, "balance": amount}


# ---- Brokers -------------------------------------------------------------- #
@app.get("/api/brokers")
async def get_brokers():
    return {
        "brokers": db_manager.get_broker_public_list(),
        "status": broker_connector.get_status(),
    }


@app.post("/api/brokers", dependencies=[Depends(require_admin)])
async def add_broker_api(body: Dict[str, Any] = Body(...)):
    broker_id = str(body.get("broker_id", "")).strip()
    exchange_id = str(body.get("exchange_id", "")).strip()
    api_key = str(body.get("api_key", "")).strip()
    api_secret = str(body.get("api_secret", "")).strip()
    passphrase = body.get("api_passphrase")

    if not broker_id or not exchange_id or not api_key or not api_secret:
        raise HTTPException(400, "broker_id, exchange_id, api_key and api_secret are required")

    connected = await broker_connector.add_broker(broker_id, exchange_id, api_key, api_secret, passphrase)
    if connected:
        db_manager.save_broker_config(broker_id, exchange_id, api_key, api_secret, passphrase)
        db_manager.log_audit("INFO", "BROKER_ADDED", f"Broker '{broker_id}' ({exchange_id}) connected")
        return {"success": True, "broker_id": broker_id, "connected": True}
    db_manager.log_audit("WARNING", "BROKER_ADD_FAILED",
                         f"Broker '{broker_id}' ({exchange_id}) could not connect")
    return {"success": False, "reason": "CONNECTION_FAILED",
            "message": "Could not connect with these credentials."}


@app.post("/api/brokers/{broker_id}/toggle", dependencies=[Depends(require_admin)])
async def toggle_broker_api(broker_id: str, body: Dict[str, Any] = Body(...)):
    is_active = bool(body.get("is_active", True))
    db_manager.set_broker_active(broker_id, is_active)
    if is_active:
        configs = [c for c in db_manager.get_all_broker_configs() if c["broker_id"] == broker_id]
        if configs:
            c = configs[0]
            await broker_connector.add_broker(broker_id, c["exchange_id"], c["api_key"],
                                              c["api_secret"], c["api_passphrase"])
    else:
        await broker_connector.remove_broker(broker_id)
    return {"success": True, "broker_id": broker_id, "is_active": is_active}


@app.delete("/api/brokers/{broker_id}", dependencies=[Depends(require_admin)])
async def delete_broker_api(broker_id: str):
    await broker_connector.remove_broker(broker_id)
    deleted = db_manager.delete_broker(broker_id)
    return {"success": deleted, "broker_id": broker_id}


# ---- Web3 wallets --------------------------------------------------------- #
@app.get("/api/wallets")
async def get_wallets():
    wallets = db_manager.get_wallets()
    return {"wallets": wallets}


@app.post("/api/wallets", dependencies=[Depends(require_admin)])
async def add_wallet_api(body: Dict[str, Any] = Body(...)):
    wallet_id = str(body.get("wallet_id", "")).strip()
    provider = str(body.get("provider", "METAMASK")).strip().upper()
    address = str(body.get("address", "")).strip()
    network = body.get("network")
    if not wallet_id or not address:
        raise HTTPException(400, "wallet_id and address are required")
    db_manager.save_wallet(wallet_id, provider, address, network)
    broker_connector.web3_wallets[wallet_id] = {"provider": provider, "address": address,
                                                "network": network or "mainnet"}
    db_manager.log_audit("INFO", "WALLET_ADDED", f"Web3 wallet '{wallet_id}' ({provider}) added")
    return {"success": True, "wallet_id": wallet_id}


@app.delete("/api/wallets/{wallet_id}", dependencies=[Depends(require_admin)])
async def delete_wallet_api(wallet_id: str):
    broker_connector.web3_wallets.pop(wallet_id, None)
    deleted = db_manager.delete_wallet(wallet_id)
    return {"success": deleted, "wallet_id": wallet_id}


# ---- Performance, metrics, news, health, backtest, diagnosis --------------- #
@app.get("/api/performance")
async def get_performance(mode: str = "DEMO"):
    return portfolio_engine.get_performance_report(mode)


@app.get("/api/metrics")
async def get_metrics():
    core = metrics_engine.snapshot()
    # Simulated winrate = win rate computed on CLOSED (paper/real) trades,
    # per strategy and per mode. Guarded: metrics must never 500 the endpoint.
    winrate_simulated: Dict[str, Any] = {}
    for mode in ("DEMO", "REAL"):
        try:
            winrate_simulated[mode] = portfolio_engine.get_performance_report(mode)
        except Exception as e:
            structured_log(logger, logging.WARNING, "METRICS_WINRATE_FAILED",
                           event="metrics_winrate_failed", mode=mode, error=str(e))
            winrate_simulated[mode] = None
    return {
        **metrics_state,
        "uptime_s": int((datetime.now() - _started_at).total_seconds()),
        "scanner_duration_s": scanner_engine.last_scan_duration,
        "state": state_machine.current_state.value,
        "recent_orders": execution_router.order_history[-20:],
        # ---- LOT A: advanced observability (additive fields) ----
        "signals_generated_by_strategy": core["signals_generated_by_strategy"],
        "signals_blocked_by_strategy": core["signals_blocked_by_strategy"],
        "orders_by_mode": core["orders_by_mode"],
        "winrate_simulated": winrate_simulated,
        "latency": core["latency"],
        "data_age": core["data_age"],
        "heartbeat": manager.heartbeat_status(),
        "total_errors": core["total_errors"],
    }


_news_cache: Dict[str, Any] = {"ts": 0.0, "data": []}

@app.get("/api/news")
async def get_news():
    now = time.time()
    if now - _news_cache["ts"] > 300:
        try:
            _news_cache["data"] = await news_aggregator.get_latest_news()
        except Exception as e:
            logger.warning(f"News aggregation failed: {e}")
        _news_cache["ts"] = now
    return {"news": _news_cache["data"]}


@app.get("/api/health")
async def get_health():
    report = await data_engine.health_monitor.get_health_report()
    return {"providers": report}


@app.post("/api/backtest", dependencies=[Depends(require_admin)])
async def run_backtest(body: Dict[str, Any] = Body(...)):
    market_id = body.get("market_id", "btc_usdt")
    timeframe = body.get("timeframe", "1h")
    limit = int(body.get("limit", 300))
    strategy = body.get("strategy", "structure")
    initial_balance = float(body.get("initial_balance", 10000.0))

    df = await data_engine.fetch_ohlcv(market_id, timeframe, limit)
    if df.empty:
        raise HTTPException(400, f"No OHLCV data for {market_id} ({timeframe})")
    result = await backtest_engine.run_backtest(market_id, df,
                                                initial_balance=initial_balance,
                                                strategy_mode=strategy)
    return result


@app.get("/api/diagnose")
async def diagnose(market_id: str = "btc_usdt"):
    snapshot = await get_market_snapshot(market_id)
    return {"market_id": market_id, "diagnosis": snapshot["diagnosis"],
            "signal": snapshot["signal"], "news": snapshot["news"]}


# ---- WebSocket + static ---------------------------------------------------- #
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            message = await ws.receive_text()
            manager.note_activity(ws)
            # Application-level ping/pong (LOT A): browsers/proxies can rely on
            # it even when protocol-level pings are not forwarded.
            try:
                data = json.loads(message) if message else {}
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(data, dict) and data.get("type") == "ping":
                await manager.send_personal(ws, json.dumps({
                    "type": "pong",
                    "seq": data.get("seq"),
                    "timestamp_ms": int(time.time() * 1000),
                    "server_time": datetime.now().isoformat(),
                }))
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:
        manager.disconnect(ws)


@app.get("/")
async def read_index():
    return FileResponse('public/index.html')


app.mount("/", StaticFiles(directory="public"), name="public")


# --------------------------------------------------------------------------- #
# 10. Lifespan + entry point                                                  #
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("QUANTUM TRADE PRO STARTING...")
    if not ADMIN_API_KEY:
        logger.warning("ADMIN_API_KEY not set: mutating endpoints are UNPROTECTED. "
                       "Set ADMIN_API_KEY in production.")
    try:
        await broker_connector.initialize_from_db(db_manager)
    except Exception as e:
        logger.error(f"Broker initialization failed: {e}")
    state_machine.transition_to(BotState.STOPPED)
    settings_provider.apply()

    tasks = [
        asyncio.create_task(loop_wrapper(tick_capital, 1.0, "tick_capital")),
        asyncio.create_task(loop_wrapper(tick_scanner, 5.0, "tick_scanner")),
        asyncio.create_task(loop_wrapper(tick_management, 1.0, "tick_management")),
        asyncio.create_task(loop_wrapper(tick_broadcaster, 1.0, "tick_broadcaster")),
        asyncio.create_task(loop_wrapper(tick_heartbeat, HEARTBEAT_INTERVAL_S, "tick_heartbeat")),
    ]
    yield
    logger.info("QUANTUM TRADE PRO SHUTTING DOWN...")
    for t in tasks:
        t.cancel()
    try:
        await broker_connector.shutdown()
    except Exception:
        pass
    try:
        await data_engine.shutdown()
    except Exception:
        pass


app.router.lifespan_context = lifespan

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
