"""
Quantum Trade Pro — Institutional Trading Application
=====================================================
Single entry point: FastAPI app + background trading loops + WebSocket bus.

v2.0 — Full API contract, authentication, live settings, real broker execution.
"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header, Depends, Body, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any

import os
import asyncio
import json
import logging
import math
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
from api.engines.radar import prepare_radar
from api.engines.market_hub import enrich_overview
from api.engines.scan_contract import (
    classify_block_reason, merge_universe_rows, summarize_scan,
)
from api.engines.provider_capabilities import PROVIDER_CAPABILITIES
from api.engines.order_types import normalize_order_type, risk_based_quantity
from api.engines.settings_schema import validate_settings, ensure_defaults
from api.engines.capital_profiles import resolve_bracket, profile_overrides
from api.engines.institutional_executor import select_candidates, describe_intent
from api.engines import market_tuning as market_tuning_engine
from api.engines.constants import (
    AUTO_EXECUTION_SCORE_FLOOR,
    DEFAULT_MAX_NEW_POSITIONS_PER_SCAN,
    DEFAULT_RSI_RISK_REWARD,
)
from api.engines.opportunity_ranker import rank_opportunities
from api.engines.opportunity_tracker import get_tracker
from api.engines.cost_calculator import compute_trade_costs, costs_pass_gate
from api.engines.quarantine import get_quarantine_manager

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

app = FastAPI(title="Quantum Trade Pro", version="2.9.1", lifespan=None)

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
news_engine = NewsEngine(db_manager=db_manager)
news_aggregator = NewsAggregator()
signal_engine = SignalEngine(min_score=AUTO_EXECUTION_SCORE_FLOOR)
risk_engine = RiskEngine()
risk_engine.universe = data_engine.universe
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
    "scanning": False, "scan_progress_count": 0,
    "scan_progress_total": len(data_engine.universe.get_all_ids()),
    "scan_started_at": None, "last_scan_completed_at": None,
    "execution_intent": {"code": "STOPPED", "message": "System stopped"},
    "engine_stats": {"markets": 0, "scanned": 0, "signals": 0, "tradable": 0},
    "selected_market": "btc_usdt",
    "capital_profile": {"mode": "manual", "bracket": None, "balance": 0.0, "applied": False},
    "last_block_reason": "SYSTEM_NOT_RUNNING",
    "scan_error": None,
}


metrics_state: Dict[str, Any] = {
    "total_scans": 0, "total_trades": 0, "total_errors": 0,
    "signals_by_strategy": {"rsi": 0, "structure": 0, "arbitrage": 0, "tape": 0, "liquidity": 0},
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

    def invalidate(self) -> None:
        self._ts = 0.0

    def get(self) -> Dict[str, str]:
        now = time.time()
        if now - self._ts > self.ttl:
            try:
                self._cache = ensure_defaults(self.db.get_settings())
                self._ts = now
            except Exception as e:
                logger.warning(f"Settings reload failed: {e}")
        return self._cache

    def apply(self) -> None:
        """Push DB settings into the engines (risk, signal, scanner)."""
        s = self.get()
        risk_engine.apply_settings(s)
        try:
            signal_engine.set_min_score(int(float(s.get("min_signal_score", AUTO_EXECUTION_SCORE_FLOOR))))
        except ValueError:
            pass
        # LOT P: wire the profit levers that were previously dead settings
        signal_engine.set_risk_reward(s.get("risk_reward_ratio", DEFAULT_RSI_RISK_REWARD))
        signal_engine.set_atr_stop_multiplier(s.get("atr_stop_multiplier", 1.5))
        signal_engine.set_alpha_override(s.get("alpha_override_enabled", "false").lower() == "true")
        try:
            signal_engine.set_cost_params(
                fee_pct=float(s.get("fee_pct", 0.05)),
                slippage_pct=float(s.get("sim_slippage_pct", 0.05)),
                max_cost_ratio=float(s.get("max_cost_ratio", 0.5)))
        except ValueError:
            pass

        # Capital-aware profile (small-account support).
        #   manual -> the user's explicit settings always win (only reported).
        #   auto   -> the bracket overrides the risk/signal tuning params so the
        #             bot adapts itself to the size of the account.
        mode = s.get("capital_profile_mode", "manual").lower()
        balance = bot_state.get("balance", 0.0) or 0.0
        bracket = resolve_bracket(balance)
        if mode == "auto":
            ov = profile_overrides(balance)
            risk_engine.max_risk_pct = float(ov["risk_pct"])
            risk_engine.max_leverage = float(ov["max_leverage"])
            risk_engine.max_open_positions = int(ov["max_open_positions"])
            risk_engine.min_trade_notional = float(ov["min_trade_notional"])
            signal_engine.set_min_score(int(ov["min_signal_score"]))
            signal_engine.set_risk_reward(ov["risk_reward_ratio"])
            signal_engine.set_atr_stop_multiplier(ov["atr_stop_multiplier"])
            try:
                signal_engine.set_cost_params(max_cost_ratio=float(ov["max_cost_ratio"]))
            except ValueError:
                pass
        bot_state["capital_profile"] = {
            "mode": mode,
            "bracket": bracket.name,
            "balance": balance,
            "applied": mode == "auto",
        }

        strategies = [x.strip() for x in s.get("active_strategies", "structure").split(",") if x.strip()]
        signal_engine.set_active_strategies(strategies)

        # LOT R — per-market tuning + volatility-regime adaptation:
        #   defaults per asset class, refined by the `market_tuning` setting
        #   (JSON overrides produced by `scripts/profit_audit.py --json` +
        #   `market_tuning.build_tuning_from_audit`).
        signal_engine.set_regime_adaptation(s.get("regime_adaptation_enabled", "true").lower() == "true")
        tuning_map = market_tuning_engine.build_default_tuning(data_engine.universe)
        try:
            import json as _json
            overrides = _json.loads(s.get("market_tuning", "{}") or "{}")
            if isinstance(overrides, dict):
                for mid, ov in overrides.items():
                    if isinstance(ov, dict) and mid in tuning_map:
                        tuning_map[mid].update(ov)
        except (ValueError, TypeError):
            logger.warning("Invalid market_tuning JSON in settings — using class defaults")
        signal_engine.set_market_tuning(tuning_map)
        bot_state["regime_adaptation_enabled"] = signal_engine.regime_adaptation_enabled

        scanner_engine.apply_settings(s)
        news_engine.apply_settings(s)
        bot_state["language"] = s.get("language", "en")


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
    """1s — sync balances, equity, drawdown and global safety.
    
    P0: never await network calls while holding state_lock — copy the state,
    fetch outside, then write back under a short lock.
    """
    # Phase 1: snapshot state under fast lock
    async with state_lock:
        mode = bot_state["mode"]
        settings_provider.apply()
        eq_state = {
            "mode": mode,
            "balance": portfolio_engine.get_balance(mode),
        }
    # Phase 2: network calls outside any lock
    if mode == "REAL":
        try:
            balances = await broker_connector.get_all_balances()
            total = sum(b.get("total_usdt", 0.0) for b in balances.values()
                        if isinstance(b, dict) and b.get("type") == "BROKER")
            portfolio_engine.set_balance("REAL", total)
            eq_state["balance"] = total
        except Exception as e:
            logger.error(f"Balance sync failed: {e}")
    active = demo_execution.active_positions if mode == "DEMO" else db_manager.get_active_positions("REAL")
    unrealized = sum(p.get("pnl", 0.0) or 0.0 for p in active)
    equity = eq_state["balance"] + unrealized
    daily_pnl = portfolio_engine.get_daily_pnl(mode)
    # Phase 3: short lock to write results
    safe = True
    safety_reason = ""
    async with state_lock:
        bot_state["balance"] = eq_state["balance"]
        bot_state["equity"] = equity
        bot_state["active_trades"] = list(active)
        bot_state["daily_pnl"] = daily_pnl
        risk_engine.daily_pnl = daily_pnl
        bot_state["drawdown"] = risk_engine.get_current_drawdown_pct(equity)
        safety = risk_engine.check_global_safety(equity, daily_pnl)
        safe = safety.get("safe", True)
        if not safe:
            safety_reason = safety.get("reason", "Global risk limit")
            logger.error("GLOBAL RISK LIMIT: %s", safety_reason)
    # Phase 4: emergency stop outside lock (it acquires its own lock)
    if not safe:
        await emergency_stop_logic(safety_reason)


_scan_counter = {"n": 0}
SCAN_LOCK_STALE_S = 90.0
SCAN_ALL_TIMEOUT_S = 120.0


def is_serverless_runtime() -> bool:
    """Vercel / Lambda cannot host a permanent asyncio scanner loop."""
    return bool(
        os.getenv("VERCEL")
        or os.getenv("AWS_LAMBDA_FUNCTION_NAME")
        or os.getenv("FUNCTIONS_WORKER_RUNTIME")
    )


def _scan_is_stuck() -> bool:
    started = bot_state.get("scan_started_at")
    return bool(
        bot_state.get("scanning")
        and started
        and (time.time() - float(started) > SCAN_LOCK_STALE_S)
    )


def persist_latest_scan() -> None:
    try:
        db_manager.save_scanner_cache({
            "latest_scan": bot_state.get("latest_scan") or [],
            "engine_stats": bot_state.get("engine_stats") or {},
            "scan_progress_count": bot_state.get("scan_progress_count"),
            "scan_progress_total": bot_state.get("scan_progress_total"),
            "last_scan_completed_at": bot_state.get("last_scan_completed_at"),
            "last_block_reason": bot_state.get("last_block_reason"),
        })
    except Exception as exc:
        logger.debug("Scanner cache persist failed: %s", exc)


def restore_latest_scan() -> None:
    try:
        cached = db_manager.load_scanner_cache()
    except Exception as exc:
        logger.debug("Scanner cache load failed: %s", exc)
        return
    if not cached:
        return
    rows = cached.get("latest_scan") or []
    if rows and not bot_state.get("latest_scan"):
        bot_state["latest_scan"] = rows
        bot_state["engine_stats"] = cached.get("engine_stats") or bot_state["engine_stats"]
        bot_state["scan_progress_count"] = cached.get("scan_progress_count") or len(rows)
        bot_state["scan_progress_total"] = cached.get("scan_progress_total") or len(rows)
        bot_state["last_scan_completed_at"] = cached.get("last_scan_completed_at")
        bot_state["last_block_reason"] = cached.get("last_block_reason")


def _scanner_payload(sort="score", order="desc", filter_mode="all", live_only=False):
    universe_rows = merge_universe_rows(
        bot_state.get("latest_scan") or [], data_engine.universe,
        missing_reason="DATA_UNAVAILABLE",
    )
    assets = prepare_radar(universe_rows, sort=sort, order=order,
                           filter_mode=filter_mode, live_only=live_only)
    completed_at = bot_state.get("last_scan_completed_at")
    age_s = max(0.0, time.time() - completed_at) if completed_at else None
    total = len(data_engine.universe.get_all_ids())
    completed = int(bot_state.get("scan_progress_count") or 0)
    summary = summarize_scan(universe_rows, total)
    last_iso = (
        datetime.fromtimestamp(completed_at).isoformat() if completed_at else None
    )
    return {
        "assets": assets,
        "duration_s": scanner_engine.last_scan_duration,
        "sort": sort,
        "order": order,
        "filter": filter_mode,
        "live_only": live_only,
        "scanning": bool(bot_state.get("scanning")),
        "progress": f"{completed}/{total}",
        "progress_count": completed,
        "progress_total": total,
        "last_scan_age_s": round(age_s, 3) if age_s is not None else None,
        "last_scan": last_iso,
        "active_strategy": "rsi",
        "strategy_name": "RSI-14 Reversal",
        "risk_reward_rsi": DEFAULT_RSI_RISK_REWARD,
        "scan_error": bot_state.get("scan_error"),
        "block_reason": bot_state.get("last_block_reason"),
        "last_block_reason": bot_state.get("last_block_reason"),
        "excluded": (bot_state.get("opportunity_ranking") or {}).get("excluded", []),
        "news_unavailable_policy": settings_provider.get().get(
            "news_unavailable_policy", "block_tradfi_only"),
        **summary,
    }


# P0-3 (2026-08-23): execution observability. When an armed + running scan
# executes nothing, publish the REAL blocking reason instead of leaving
# last_block_reason permanently None (or stale) in production.
_BLOCK_REASON_ALIASES = {
    "SCORE_BELOW_FLOOR": "SCORE_BELOW_84",
    "NO_SIGNAL_DETECTED": "NO_RSI_SIGNAL",
    "DATA_STALE": "STALE_DATA",
    "COST_GATE_BLOCKED": "COST_GATE",
    "COST_CALCULATION_FAILED": "COST_GATE",
}


def _normalize_block_reason(raw: Any) -> str:
    head = str(raw or "").split("(", 1)[0].strip().upper()
    return _BLOCK_REASON_ALIASES.get(head, head)


# Outage-level scanner reasons that explain the absence of signal itself —
# they outrank the generic NO_RSI_SIGNAL diagnosis.
_SYSTEM_LEVEL_REASONS = {
    "CALENDAR_UNAVAILABLE", "NON_REALTIME_SOURCE", "PROVIDER_ERROR",
    "PROVIDER_QUOTA_EXCEEDED", "DATA_UNAVAILABLE", "STALE_DATA",
}


def _diagnose_no_execution_reason(rows: list, opportunity_result: Dict[str, Any],
                                  skip_reasons: list) -> Optional[str]:
    """Derive why an armed + running scan cycle executed nothing."""
    # Freshest signal: per-candidate failures collected at execution time.
    if skip_reasons:
        return _normalize_block_reason(skip_reasons[-1])
    # Scanner-level block reasons on actual signal candidates (non-realtime
    # source, news/session, …) tell the true story.
    for row in rows:
        if ((row.get("signal_data") or {}).get("status") == "SIGNAL_DETECTED"
                and row.get("block_reason")):
            return _normalize_block_reason(row["block_reason"])
    # System-level outage shared by the scanned universe (calendar down,
    # provider failure…) explains why no signal exists at all.
    counts: Dict[str, int] = {}
    for row in rows:
        normalized = _normalize_block_reason(row.get("block_reason"))
        if normalized in _SYSTEM_LEVEL_REASONS:
            counts[normalized] = counts.get(normalized, 0) + 1
    if counts:
        return max(counts.items(), key=lambda kv: kv[1])[0]
    # Otherwise aggregate the ranker's gate exclusions (score floor, costs,
    # spread, quarantine, …) — most frequent reason wins.
    for item in (opportunity_result or {}).get("excluded") or []:
        for reason in item.get("gate_reasons") or []:
            counts[_normalize_block_reason(reason)] = (
                counts.get(_normalize_block_reason(reason), 0) + 1)
    if counts:
        return max(counts.items(), key=lambda kv: kv[1])[0]
    has_signal = any(
        (row.get("signal_data") or {}).get("status") == "SIGNAL_DETECTED"
        for row in rows)
    if not has_signal:
        return "NO_RSI_SIGNAL"
    return None if (opportunity_result or {}).get("all_candidates") else "RANKER_EMPTY"


def _publish_no_execution_reason(auto_results: list,
                                 opportunity_result: Dict[str, Any],
                                 skip_reasons: list,
                                 cycle_scan_error: Optional[str] = None) -> None:
    """Set bot_state['last_block_reason'] for an armed+running 0-execution scan."""
    rows = [r for r in (auto_results or []) if isinstance(r, dict)]
    if not rows:
        # Empty scan (timeout / provider outage): never wipe the existing
        # SCAN_TIMEOUT / PROVIDER_ERROR reason with a misleading one.
        return
    if cycle_scan_error:
        # Provider-level failure of this cycle outranks per-row diagnostics.
        return
    reason = _diagnose_no_execution_reason(rows, opportunity_result, skip_reasons)
    if reason:
        bot_state["last_block_reason"] = reason
        persist_latest_scan()


async def tick_scanner(force: bool = False):
    """Rescan immediately at boot, publishing each completed symbol."""
    _scan_counter["n"] += 1
    settings = settings_provider.get()
    try:
        interval = max(5, int(float(settings.get("scan_interval_seconds", "30"))))
    except ValueError:
        # Preserve the historical invalid-value fallback for compatibility.
        interval = 20

    every = max(1, interval // 5) if bot_state["is_running"] else 12
    if not force and _scan_counter["n"] % every != 0:
        return
    if bot_state.get("scanning") and not _scan_is_stuck():
        return
    if _scan_is_stuck():
        logger.warning("SCAN_TIMEOUT — resetting stuck scanner lock")
        bot_state["scanning"] = False
        bot_state["scan_error"] = "SCAN_TIMEOUT"
        bot_state["last_block_reason"] = "SCAN_TIMEOUT"

    total = len(data_engine.universe.get_all_ids())
    first_scan = bot_state.get("last_scan_completed_at") is None
    bot_state.update({
        "scanning": True,
        # P0-3: per-cycle error tracking starts clean so a stale error from a
        # previous cycle never masks the current diagnosis.
        "scan_error": None,
        "scan_progress_count": 0,
        "scan_progress_total": total,
        "scan_started_at": time.time(),
    })
    if first_scan:
        bot_state["latest_scan"] = []

    async def publish_progress(result, completed, progress_total):
        current = list(bot_state.get("latest_scan") or [])
        symbol = result.get("symbol")
        current = [row for row in current if row.get("symbol") != symbol]
        current.append(result)
        async with state_lock:
            bot_state["latest_scan"] = current
            bot_state["scan_progress_count"] = completed
            bot_state["scan_progress_total"] = progress_total
            bot_state["best_setups"] = prepare_radar(current)[:5]

    results = []
    try:
        results = await asyncio.wait_for(
            scanner_engine.scan_all(progress_callback=publish_progress),
            timeout=SCAN_ALL_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.warning("SCAN_TIMEOUT — scan_all exceeded %ss", SCAN_ALL_TIMEOUT_S)
        bot_state["scan_error"] = "SCAN_TIMEOUT"
        bot_state["last_block_reason"] = "SCAN_TIMEOUT"
        results = list(bot_state.get("latest_scan") or [])
    except Exception as exc:
        logger.error("Scanner Error (scan_all): %s", exc)
        bot_state["scan_error"] = "PROVIDER_ERROR"
        bot_state["last_block_reason"] = "PROVIDER_ERROR"
        results = list(bot_state.get("latest_scan") or [])
    finally:
        bot_state["scanning"] = False

    # P0-3: remember this cycle's provider-level error before the state block
    # resets scan_error — an execution diagnostic must never mask it.
    cycle_scan_error = bot_state.get("scan_error")

    # Defence in depth: even if a legacy/custom scanner is injected, only an
    # explicit RSI signal may enter the automatic ranking/execution pipeline.
    auto_results = [
        result for result in (results or [])
        if str((result.get("signal_data") or {}).get("strategy", "")).lower() == "rsi"
    ]

    async with state_lock:
        metrics_state["total_scans"] += 1
        metrics_engine.record_scan(scanner_engine.last_scan_duration, results)
        merged = merge_universe_rows(results, data_engine.universe)
        bot_state["latest_scan"] = merged
        bot_state["scan_progress_count"] = len(merged)
        bot_state["scan_progress_total"] = total
        bot_state["last_scan_completed_at"] = time.time()
        bot_state["scan_error"] = None
        summary = summarize_scan(merged, total)
        bot_state["engine_stats"].update({
            "markets": summary["markets_total"],
            "scanned": summary["markets_processed"],
            "signals": summary["rsi_signals"],
            "tradable": summary["markets_tradable"],
            "unavailable": summary["markets_unavailable"],
            "errors": summary["markets_error"],
        })
        persist_latest_scan()
        bot_state["best_setups"] = prepare_radar(merged)[:5]
        armed = bot_state["armed"] and bot_state["is_running"]
        active = list(bot_state["active_trades"])
        mode = bot_state["mode"]
        balance = bot_state["balance"]
        running = bot_state["is_running"]

    try:
        min_score = float(settings.get("min_signal_score", AUTO_EXECUTION_SCORE_FLOOR))
    except ValueError:
        min_score = float(AUTO_EXECUTION_SCORE_FLOOR)
    # v2.7: enforce the inviolable floor
    min_score = max(float(AUTO_EXECUTION_SCORE_FLOOR), min_score)
    
    # v2.7 P0-2: Use OpportunityRanker to select the single best opportunity
    quarantine_mgr = get_quarantine_manager()
    opportunity_result = rank_opportunities(
        auto_results,
        active_symbols={p.get("symbol") for p in active},
        max_new_positions=int(settings.get("max_new_positions_per_scan", DEFAULT_MAX_NEW_POSITIONS_PER_SCAN)),
        fee_pct=float(settings.get("fee_pct", 0.05)),
        slippage_pct=float(settings.get("sim_slippage_pct", 0.05)),
        max_spread_pct=float(settings.get("max_spread_pct", 0.5)),
        quarantined={f"{k[0]}:{k[1]}" for k in quarantine_mgr.get_quarantined()},
    )
    bot_state["opportunity_ranking"] = opportunity_result
    
    candidates = select_candidates(auto_results, min_score, {p.get("symbol") for p in active},
                                   risk_engine.max_open_positions)
    intent = describe_intent(running, bot_state["armed"], len(candidates), len(active),
                             risk_engine.max_open_positions, min_score)
    bot_state["execution_intent"] = intent
    metrics_engine.record_institutional(intent.get("code"), n_active=len(active))

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

    # v2.8: simultaneous executions — the top-N ranked opportunities that each
    # pass every gate individually may be executed in the same scan cycle.
    # The idempotence tracker protects against duplicates, the correlation
    # guard blocks stacking two positions on the same underlying, and the
    # scan loop keeps running afterwards (continuous trading).
    try:
        max_new = int(float(settings.get("max_new_positions_per_scan",
                                         DEFAULT_MAX_NEW_POSITIONS_PER_SCAN)))
    except (TypeError, ValueError):
        max_new = DEFAULT_MAX_NEW_POSITIONS_PER_SCAN
    max_new = max(1, min(3, max_new))
    candidates_to_execute = (opportunity_result.get("all_candidates") or [])[:max_new]
    if not candidates_to_execute:
        # P0-3: armed + running + ranker empty → publish the real reason
        # (score floor, calendar, non-realtime source, no RSI signal, …).
        _publish_no_execution_reason(auto_results, opportunity_result, [],
                                     cycle_scan_error)
        return

    tracker = get_tracker()
    executed_symbols: list = []
    skip_reasons: list = []
    # The ranked payload intentionally holds metrics, not every raw scan flag —
    # cross-check execution-critical flags (e.g. `tradable`) against the raw
    # scan rows the ranking was built from.
    raw_by_symbol = {r.get("symbol"): r for r in (auto_results or [])}

    for res in candidates_to_execute:
        # Stop when the portfolio is full — each candidate is gated individually.
        if len(active) >= risk_engine.max_open_positions:
            break

        # v2.7 P0-3: opportunity TTL + idempotence single-flight.
        opp_id = res.get("opportunity_id")
        expires_at = res.get("expires_at", 0)
        if tracker.is_expired(expires_at):
            logger.info(f"Opportunity {opp_id} expired — skipping execution")
            skip_reasons.append("OPPORTUNITY_EXPIRED")
            continue
        acquire_result = tracker.try_acquire(opp_id)
        if not acquire_result.get("allowed"):
            logger.info(f"Opportunity {opp_id} not acquired: {acquire_result.get('reason')}")
            skip_reasons.append(acquire_result.get("reason") or "ALREADY_TRACKED")
            continue

        raw = raw_by_symbol.get(res.get("symbol")) or {}
        if not res.get("tradable", raw.get("tradable")):
            tracker.mark_failed(opp_id, "NOT_TRADABLE")
            skip_reasons.append("NON_REALTIME_SOURCE" if not res.get("realtime_source")
                                else "NOT_TRADABLE")
            continue
        if any(p["symbol"] == res["symbol"] for p in active):
            tracker.mark_failed(opp_id, "POSITION_ALREADY_OPEN")
            skip_reasons.append("POSITION_ALREADY_OPEN")
            continue

        # v2.8: correlation guard — never two simultaneous positions on the
        # same underlying asset (e.g. btc_usdt + btc_eur), even within one scan.
        corr = risk_engine.check_correlation(res["symbol"], active)
        if not corr.get("allowed"):
            tracker.mark_failed(opp_id, corr.get("reason") or "CORRELATION_RISK")
            skip_reasons.append(corr.get("reason") or "CORRELATION_RISK")
            logger.info(f"Correlation guard blocked {res['symbol']}: {corr.get('reason')}")
            continue

        sig = res.get("signal_data") or {}
        if not sig.get("market_id") or not sig.get("entry"):
            tracker.mark_failed(opp_id, "MISSING_SIGNAL_DATA")
            skip_reasons.append("MISSING_SIGNAL_DATA")
            continue

        # v2.7 P0-3: Revalidate signal before execution (re-fetch ticker/orderbook)
        info = data_engine.universe.get_info(res["symbol"]) or {}
        ticker = await data_engine.fetch_ticker(res["symbol"])
        if not ticker:
            tracker.mark_failed(opp_id, "NO_TICKER")
            skip_reasons.append("NO_TICKER")
            continue
        if not data_engine.is_fresh(ticker, info.get("asset_class", "CRYPTO")):
            logger.warning(f"Stale ticker for {res['symbol']} — order skipped")
            tracker.mark_failed(opp_id, "STALE_DATA")
            skip_reasons.append("STALE_DATA")
            continue

        # LOT F: never scalp delayed (non-realtime) data
        allow_delayed = settings.get("allow_delayed_data_trading", "false").lower() == "true"
        scalp_guard = data_engine.check_scalping_allowed(res["symbol"], allow_delayed)
        if not scalp_guard["allowed"]:
            db_manager.archive_signal(sig, "BLOCKED", scalp_guard["reason"])
            metrics_engine.record_signal_blocked(sig.get("strategy", "structure"))
            tracker.mark_failed(opp_id, scalp_guard["reason"])
            skip_reasons.append(scalp_guard["reason"])
            continue

        # v2.7 P0-4: Compute and validate costs
        costs = compute_trade_costs(
            entry=sig["entry"], sl=sig["sl"], tp=sig["tp"],
            fee_pct=float(settings.get("fee_pct", 0.05)),
            slippage_pct=float(settings.get("sim_slippage_pct", 0.05)),
            spread=float(ticker.get("spread", 0) or 0),
            bid=ticker.get("bid"),
            ask=ticker.get("ask"),
        )
        cost_gate = costs_pass_gate(costs)
        if not cost_gate.get("allowed"):
            db_manager.archive_signal(sig, "BLOCKED", cost_gate.get("reason", "cost"))
            tracker.mark_failed(opp_id, cost_gate.get("reason"))
            skip_reasons.append(cost_gate.get("reason") or "COST_GATE")
            logger.warning(f"Cost gate failed for {res['symbol']}: {cost_gate.get('reason')}")
            continue

        risk_data = risk_engine.calculate_position_size(
            balance, sig["entry"], sig["sl"], sig["direction"],
            symbol=res["symbol"], active_positions=active,
            market_info=info)
        strat = sig.get("strategy", "structure")
        if not risk_data.get("allowed"):
            db_manager.archive_signal(sig, "BLOCKED", risk_data.get("reason") or "risk")
            metrics_engine.record_signal_blocked(strat)
            tracker.mark_failed(opp_id, risk_data.get("reason"))
            skip_reasons.append(risk_data.get("reason") or "RISK_GATE")
            continue

        exec_start = time.time()
        exec_res = await execution_router.execute(mode, sig, risk_data, ticker)
        exec_latency_ms = (time.time() - exec_start) * 1000.0
        if exec_res.get("success"):
            metrics_state["total_trades"] += 1
            metrics_state["signals_by_strategy"][strat] = metrics_state["signals_by_strategy"].get(strat, 0) + 1
            metrics_engine.record_execution(strat, mode, success=True, latency_ms=exec_latency_ms)
            metrics_engine.record_institutional("EXECUTING", n_active=len(active) + 1, trades_above=1)
            db_manager.archive_signal(sig, "EXECUTED", "")
            tracker.mark_executed(opp_id, exec_res)
            structured_log(logger, logging.INFO, "ORDER_EXECUTED",
                           event="order_executed", symbol=res["symbol"], mode=mode,
                           strategy=strat, latency_ms=round(exec_latency_ms, 2),
                           opportunity_id=opp_id)
            info_pos = data_engine.universe.get_info(res["symbol"]) or {}
            underlying = (
                res.get("underlying")
                or raw.get("underlying")
                or info_pos.get("underlying")
                or res["symbol"]
            )
            if exec_res.get("position"):
                pos = dict(exec_res["position"])
                pos.setdefault("symbol", res["symbol"])
                pos.setdefault("underlying", underlying)
                active.append(pos)
            else:
                active.append({"symbol": res["symbol"], "underlying": underlying})
            executed_symbols.append(res["symbol"])
            asyncio.create_task(notification_engine.notify("ORDER_OPEN", {
                "symbol": res["symbol"],
                "entry_price": sig["entry"],
                "quantity": risk_data["quantity"],
            }))
        else:
            metrics_engine.record_execution(strat, mode, success=False, latency_ms=exec_latency_ms)
            db_manager.archive_signal(sig, "BLOCKED", exec_res.get("reason") or "execution")
            tracker.mark_failed(opp_id, exec_res.get("reason"))
            skip_reasons.append(exec_res.get("reason") or "EXECUTION_REJECTED")
            logger.warning(f"Execution blocked for {res['symbol']}: {exec_res.get('reason')}")

    if executed_symbols:
        # v2.8: publish the newly opened positions immediately (the DB-fed
        # management tick remains authoritative every second), then keep
        # scanning — the loop never stops while is_running && armed.
        async with state_lock:
            bot_state["active_trades"] = list(active)
            # P0-3: executions happened — the previous block reason is stale.
            bot_state["last_block_reason"] = None
        persist_latest_scan()
        structured_log(logger, logging.INFO, "SCAN_CYCLE_EXECUTIONS",
                       event="scan_cycle_executions", count=len(executed_symbols),
                       symbols=",".join(executed_symbols), mode=mode)
    elif armed and running:
        # P0-3: candidates existed but every gate refused them — surface the
        # actual refusal (STALE_DATA, COST_GATE, RISK_*, …) to /api/status
        # and /api/opportunities instead of an always-None last_block_reason.
        _publish_no_execution_reason(auto_results, opportunity_result, skip_reasons,
                                     cycle_scan_error)
        structured_log(logger, logging.INFO, "NO_EXECUTION_DIAGNOSIS",
                       event="no_execution_diagnosis",
                       reason=bot_state.get("last_block_reason"),
                       skip_reasons=skip_reasons[-5:])


async def tick_management():
    """1s — update positions (SL/TP/trailing in DEMO, reconciliation in REAL)."""
    mode = bot_state["mode"]
    active = bot_state["active_trades"]
    pending_ids = [p.get("market_id") for p in getattr(demo_execution, "pending_orders", []) if p.get("market_id")]
    symbols = list({*(t["symbol"] for t in active), *pending_ids})
    if not symbols:
        return

    quotes = await data_engine.layer.get_all_quotes(symbols, data_engine.universe)
    tickers = {q.symbol: q.model_dump() for q in quotes}
    # Also index by market_id/display symbol, but only when the quote actually
    # belongs to that market. The previous nested loop assigned the last quote
    # to every position in a multi-symbol portfolio.
    for mid in symbols:
        info = data_engine.universe.get_info(mid) or {}
        display_symbol = info.get("display_symbol")
        provider_symbols = set((info.get("providers") or {}).values())
        expected_symbols = {mid, display_symbol, *provider_symbols}
        for quote in quotes:
            if quote.symbol not in expected_symbols:
                continue
            dumped = quote.model_dump()
            tickers[mid] = dumped
            if display_symbol:
                tickers[display_symbol] = dumped
            break

    if mode == "DEMO":
        await demo_execution.process_pending_orders(mode, tickers)
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
    refresh_execution_intent()
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
        "signal": {"status": "NO_TRADE", "reason": status_display, "score": 0,
                   "market_id": market_id, "strategy": "rsi"},
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
                # RSI reversal does not require trend/structure/range checks;
                # the data gate above is the meaningful blocker when offline.
                "NOT_RANGE": "PASS",
                "TREND_VALID": "PASS",
                "STRUCTURE_VALID": "PASS",
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

    # The public snapshot is also an automatic signal surface: never let a
    # configured legacy strategy leak into Trade Terminal execution.
    signal = signal_engine.generate_signal(
        ltf_analysis, news_status, df_ltf, strategy_mode="rsi", market_id=market_id)
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


def _count_trades_today(mode: str) -> int:
    """v2.8: number of trades executed today (opened OR closed today)."""
    today = datetime.now().date().isoformat()
    seen: set = set()
    try:
        for t in db_manager.get_history(mode=mode, limit=1000):
            ts_open = str(t.get("open_time") or "")
            ts_close = str(t.get("close_time") or "")
            if ts_open.startswith(today) or ts_close.startswith(today):
                seen.add(str(t.get("id") or ts_open))
        for p in bot_state.get("active_trades") or []:
            ts_open = str(p.get("open_time") or "")
            if ts_open.startswith(today):
                seen.add(str(p.get("id") or p.get("symbol") or ts_open))
    except Exception:
        pass
    return len(seen)


def _next_scan_in_s(settings: Dict[str, str]) -> tuple:
    """v2.8: (scan_interval_s, next_scan_in_s or None when paused)."""
    try:
        interval = max(5, int(float(settings.get("scan_interval_seconds", "30"))))
    except (TypeError, ValueError):
        interval = 30
    last_scan = bot_state.get("last_scan_completed_at")
    if bot_state["is_running"] and last_scan:
        remaining = max(0, int(round(interval - (time.time() - last_scan))))
        return interval, min(remaining, interval)
    return interval, None


@app.get("/api/status")
async def get_status(market_id: str = "btc_usdt"):
    bot_state["selected_market"] = market_id
    snapshot = await get_market_snapshot(market_id)
    mode = bot_state["mode"]
    # v2.8: continuous-trading indicators for the UI badge strip.
    scan_interval_s, next_scan_in_s = _next_scan_in_s(settings_provider.get())
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
        "execution_intent": bot_state.get("execution_intent") or {
            "code": "STOPPED", "message": "System stopped"},
        "calendar": news_engine.provider.get_state(),
        "language": bot_state.get("language", "en"),
        "capital_profile": bot_state.get("capital_profile"),
        "opportunity_ranking": bot_state.get("opportunity_ranking"),
        # v2.8: continuous-trading strip (badge, daily executions, next scan)
        "trading_active": bool(bot_state["is_running"] and bot_state["armed"]),
        "trades_today": _count_trades_today(mode),
        "scan_interval_s": scan_interval_s,
        "next_scan_in_s": next_scan_in_s,
        "active_strategy": "rsi",
        "strategy_name": "RSI-14 Reversal",
        "risk_reward_rsi": DEFAULT_RSI_RISK_REWARD,
        "news_unavailable_policy": news_engine.news_unavailable_policy,
        "block_reason": classify_block_reason(
            running=bot_state["is_running"],
            armed=bot_state["armed"],
            scanning=bool(bot_state.get("scanning")),
            scan_timeout=bot_state.get("last_block_reason") == "SCAN_TIMEOUT",
            ticker=snapshot.get("ticker"),
            signal=snapshot.get("signal"),
            news=snapshot.get("news"),
            diagnosis=snapshot.get("diagnosis"),
            delayed=not data_engine.check_scalping_allowed(market_id).get("allowed"),
        ),
        "last_block_reason": bot_state.get("last_block_reason"),
        "excluded": (bot_state.get("opportunity_ranking") or {}).get("excluded", []),
        "scanner": {
            "scanning": bool(bot_state.get("scanning")),
            "progress": f"{bot_state.get('scan_progress_count', 0)}/{bot_state.get('scan_progress_total', 0)}",
            "last_scan_age_s": (
                max(0.0, time.time() - bot_state["last_scan_completed_at"])
                if bot_state.get("last_scan_completed_at") else None
            ),
            "last_scan": (
                datetime.fromtimestamp(bot_state["last_scan_completed_at"]).isoformat()
                if bot_state.get("last_scan_completed_at") else None
            ),
            "error": bot_state.get("scan_error"),
            **summarize_scan(bot_state.get("latest_scan") or [],
                             int(bot_state.get("scan_progress_total") or 0)),
        },
    }


@app.get("/api/history")
async def get_history(mode: str = "DEMO", limit: int = 100):
    return db_manager.get_history(mode=mode, limit=limit)


@app.get("/api/scanner")
async def get_scanner(sort: str = "score", order: str = "desc", filter: str = "all",
                      live_only: bool = False):
    return _scanner_payload(sort=sort, order=order, filter_mode=filter, live_only=live_only)


@app.post("/api/scanner/trigger", dependencies=[Depends(require_admin)])
async def trigger_scanner():
    """Protected on-demand scan. Refuses a second concurrent scan."""
    if bot_state.get("scanning") and not _scan_is_stuck():
        payload = _scanner_payload()
        payload.update({"success": False, "reason": "SCAN_IN_PROGRESS"})
        return payload
    asyncio.create_task(tick_scanner(force=True))
    payload = _scanner_payload()
    payload.update({"success": True, "reason": None, "scanning": True})
    return payload


@app.get("/api/markets")
async def get_markets(sort: str = "score", order: str = "desc"):
    overview = await data_engine.get_market_overview()
    for items in overview.values():
        for item in items:
            item["price"] = item.get("last")
    return enrich_overview(overview, bot_state.get("latest_scan") or [], sort=sort, order=order)


@app.get("/api/settings")
async def get_settings():
    return ensure_defaults(db_manager.get_settings())


@app.post("/api/settings", dependencies=[Depends(require_admin)])
async def save_settings(new_settings: Dict[str, str] = Body(...)):
    cleaned, errors = validate_settings(new_settings)
    db_manager.save_settings(cleaned)
    settings_provider.invalidate()
    settings_provider.apply()
    db_manager.log_audit("INFO", "SETTINGS_UPDATED", f"Updated {len(cleaned)} settings")
    return {"success": True, "applied": cleaned, "errors": errors,
            "message": "Parameters deployed live"}


def refresh_execution_intent(n_candidates: int = 0) -> Dict[str, Any]:
    intent = describe_intent(
        bot_state["is_running"], bot_state["armed"], n_candidates,
        len(bot_state.get("active_trades") or []), risk_engine.max_open_positions,
        signal_engine.min_score,
    )
    bot_state["execution_intent"] = intent
    return intent


@app.post("/api/start", dependencies=[Depends(require_admin)])
async def start_bot():
    settings = settings_provider.get()
    # P1 (2026-08-23): START means "scan" — arming stays a separate explicit
    # step. Optional DEMO-only convenience: arm_on_start_demo=true arms the
    # paper engine on START. REAL mode is NEVER auto-armed, and production
    # auto_arm_on_startup stays false by default.
    arm_demo = str(settings.get("arm_on_start_demo", "false")).lower() == "true"
    async with state_lock:
        bot_state["is_running"] = True
        if arm_demo and bot_state["mode"] == "DEMO":
            bot_state["armed"] = True
    state_machine.transition_to(BotState.RUNNING)
    refresh_execution_intent()
    if arm_demo and bot_state["mode"] == "DEMO" and bot_state["armed"]:
        db_manager.log_audit("INFO", "SYSTEM_START",
                             "Bot started — DEMO auto-armed (arm_on_start_demo)")
    else:
        db_manager.log_audit("INFO", "SYSTEM_START", "Bot started")
    # P0: start triggers an immediate scan if not already scanning
    if not bot_state.get("scanning"):
        asyncio.create_task(tick_scanner(force=True))
    return {"success": True, "state": state_machine.current_state.value,
            "armed": bool(bot_state["armed"])}


@app.post("/api/stop", dependencies=[Depends(require_admin)])
async def stop_bot():
    async with state_lock:
        bot_state["is_running"] = False
    state_machine.transition_to(BotState.STOPPED)
    refresh_execution_intent()
    db_manager.log_audit("INFO", "SYSTEM_STOP", "Bot stopped")
    return {"success": True, "state": state_machine.current_state.value}


@app.post("/api/arm", dependencies=[Depends(require_admin)])
async def arm_bot():
    async with state_lock:
        bot_state["armed"] = not bot_state["armed"]
        armed = bot_state["armed"]
    refresh_execution_intent()
    db_manager.log_audit("INFO", "SYSTEM_ARM", f"System armed state: {armed}")
    # P0: arming while running triggers an immediate scan
    if armed and bot_state.get("is_running") and not bot_state.get("scanning"):
        asyncio.create_task(tick_scanner(force=True))
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

    try:
        entry = float(ticker.get("last") or 0)
    except (TypeError, ValueError, OverflowError):
        entry = 0.0
    if not math.isfinite(entry) or entry <= 0:
        return {"success": False, "reason": "Invalid price"}

    # SL / TP defaults: 1.5 ATR stop, 2R target
    atr = 0.0
    try:
        sl = float(body["sl"]) if body.get("sl") not in (None, "") else None
        tp = float(body["tp"]) if body.get("tp") not in (None, "") else None
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(400, "sl and tp must be numeric") from None
    if sl is None or tp is None:
        try:
            df = await data_engine.fetch_ohlcv(market_id, timeframe='1m', limit=50)
            if not df.empty:
                tr = pd_concat_tr(df)
                atr = float(tr.tail(14).mean()) if len(tr) >= 14 else 0.0
        except Exception:
            pass
    if sl is None:
        if atr > 0:
            sl = entry - (atr * 1.5) if direction == "BUY" else entry + (atr * 1.5)
        else:
            sl = entry * (0.98 if direction == "BUY" else 1.02)
    if tp is None:
        dist = abs(entry - sl) or (entry * 0.01)
        tp = entry + (dist * 2.0) if direction == "BUY" else entry - (dist * 2.0)

    order_type = normalize_order_type(body.get("order_type"))
    try:
        limit_price = (float(body["limit_price"])
                       if body.get("limit_price") not in (None, "") else None)
        stop_price = (float(body["stop_price"])
                      if body.get("stop_price") not in (None, "") else None)
        quantity = float(body.get("quantity", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(400, "quantity and trigger prices must be numeric") from None
    if not all(math.isfinite(value) for value in (
        quantity, sl, tp, *(value for value in (limit_price, stop_price) if value is not None)
    )):
        raise HTTPException(400, "order values must be finite")
    if order_type == "LIMIT" and limit_price is None:
        raise HTTPException(400, "limit_price is required for a LIMIT order")
    if order_type == "STOP" and stop_price is None:
        raise HTTPException(400, "stop_price is required for a STOP order")
    if body.get("risk_based") and quantity <= 0:
        sl_for_size = float(sl)
        quantity = risk_based_quantity(bot_state["balance"], risk_engine.max_risk_pct, entry, sl_for_size)
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
        "order_type": order_type,
        "limit_price": float(limit_price) if limit_price not in (None, "") else None,
        "stop_price": float(stop_price) if stop_price not in (None, "") else None,
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
    risk_engine.consecutive_losses = 0
    risk_engine.peak_balance = 10000.0
    db_manager.log_audit("INFO", "DEMO_RESET", "Demo account reset (balance 10 000, journal wiped)")
    return {"success": True, "balance": portfolio_engine.get_balance("DEMO")}


@app.post("/api/demo/balance", dependencies=[Depends(require_admin)])
async def demo_balance(body: Dict[str, Any] = Body(...)):
    try:
        amount = float(body.get("balance", 0))
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(400, "balance must be a finite number >= 0") from None
    if not math.isfinite(amount) or amount < 0:
        raise HTTPException(400, "balance must be a finite number >= 0")
    portfolio_engine.set_balance("DEMO", amount)
    risk_engine.update_peak(amount)
    db_manager.log_audit("INFO", "DEMO_BALANCE", f"Demo balance provisioned to {amount}")
    return {"success": True, "balance": amount}


# ---- Brokers -------------------------------------------------------------- #
@app.get("/api/brokers")
async def get_brokers():
    # v2.8: enrich each broker row with its runtime snapshot (cached 30 s,
    # network calls only for active brokers, fail-safe to INACTIVE/ERROR).
    brokers = db_manager.get_broker_public_list()
    for broker in brokers:
        try:
            broker["runtime"] = await broker_connector.runtime_snapshot(
                str(broker.get("broker_id")))
        except Exception:
            broker["runtime"] = {"runtime_status": "ERROR"}
    return {
        "brokers": brokers,
        "status": broker_connector.get_status(),
    }


@app.post("/api/brokers/test", dependencies=[Depends(require_admin)])
async def test_broker_connection_api(body: Dict[str, Any] = Body(...)):
    """v2.8: dry-run a broker connection WITHOUT persisting anything.

    Lets the UI validate credentials (and measure latency) before the user
    saves the broker. A sandbox toggle is honoured exactly like at save time.
    """
    exchange_id = str(body.get("exchange_id", "")).strip()
    api_key = str(body.get("api_key", "")).strip()
    api_secret = str(body.get("api_secret", "")).strip()
    passphrase = body.get("api_passphrase") or None
    sandbox = bool(body.get("sandbox", False))

    if not exchange_id or not api_key or not api_secret:
        raise HTTPException(400, "exchange_id, api_key and api_secret are required")

    import os as _os
    from api.engines.broker_adapters.ccxt_adapter import CCXTAdapter
    from api.engines.broker_adapters.primexbt_adapter import PrimeXBTAdapter

    _os.environ["BROKER_SANDBOX"] = "true" if sandbox else "false"
    if exchange_id.upper() == "PRIMEXBT":
        adapter = PrimeXBTAdapter(api_key, api_secret, passphrase)
    else:
        adapter = CCXTAdapter(exchange_id, api_key, api_secret, passphrase)

    start = time.monotonic()
    connected = await adapter.connect()
    latency_ms = int((time.monotonic() - start) * 1000)
    try:
        await adapter.close()
    except Exception:
        pass
    db_manager.log_audit("INFO", "BROKER_CONNECTION_TEST",
                         f"Connection test for {exchange_id} (sandbox={sandbox}): "
                         f"{'OK' if connected else 'FAILED'} in {latency_ms}ms")
    return {"success": bool(connected), "exchange_id": exchange_id,
            "latency_ms": latency_ms, "sandbox": sandbox,
            "message": "Connection OK" if connected else "Could not connect with these credentials"}


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
    broker_connector.invalidate_runtime_cache(broker_id)
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
    broker_connector.invalidate_runtime_cache(broker_id)
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
    broker_connector.invalidate_runtime_cache(broker_id)
    deleted = db_manager.delete_broker(broker_id)
    return {"success": deleted, "broker_id": broker_id}


# ---- v2.8: per-position manual close (DEMO) ------------------------------- #
@app.post("/api/positions/{market_id}/close", dependencies=[Depends(require_admin)])
async def close_position_api(market_id: str):
    """Close ONE open position at market (user-initiated).

    DEMO mode closes locally through the demo execution engine. REAL mode
    never fake-closes locally (positions are reconciled with the broker) —
    use the emergency stop to flatten REAL exposure.
    """
    mode = bot_state["mode"]
    active = [p for p in (bot_state.get("active_trades") or [])
              if market_id in (p.get("symbol"), p.get("market_id"), p.get("display_symbol"))]
    if not active:
        return {"success": False, "reason": f"No open position for {market_id}"}
    if mode != "DEMO":
        return {"success": False,
                "reason": "REAL mode: positions are closed on the broker "
                          "(use Emergency Stop to flatten all exposure)."}
    ticker = await data_engine.fetch_ticker(market_id)
    if not ticker or not ticker.get("last"):
        return {"success": False, "reason": "No market data available to price the exit"}
    closed = demo_execution.close_position(mode, market_id, float(ticker["last"]))
    if not closed:
        return {"success": False, "reason": "Position not found in the DEMO engine"}
    bot_state["active_trades"] = demo_execution.active_positions
    db_manager.log_audit("INFO", "MANUAL_CLOSE",
                         f"Position {market_id} closed at market ({ticker['last']})")
    return {"success": True, "symbol": market_id,
            "exit_price": float(ticker["last"]),
            "pnl": closed.get("pnl"), "net_pnl": closed.get("net_pnl")}


# ---- Web3 wallets (watch-only) -------------------------------------------- #
@app.get("/api/wallets")
async def get_wallets():
    """v2.7 P1-10: Wallets are watch-only. Never presented as signing-capable."""
    wallets = db_manager.get_wallets()
    # Ensure all wallets are marked as watch-only
    for w in wallets:
        w["type"] = "WATCH_ONLY"
        w["signing_capable"] = False
        w["can_execute"] = False
        # Mask address by default (show first 6 + last 4)
        addr = w.get("address", "")
        if len(addr) > 10:
            w["address_masked"] = f"{addr[:6]}...{addr[-4:]}"
        else:
            w["address_masked"] = addr
    return {"wallets": wallets, "note": "All wallets are watch-only. No signing or execution capability."}


@app.post("/api/wallets", dependencies=[Depends(require_admin)])
async def add_wallet_api(body: Dict[str, Any] = Body(...)):
    """v2.7 P1-10: Add a watch-only wallet address.
    
    Validates address format per chain type. Never stores private keys or seeds.
    """
    wallet_id = str(body.get("wallet_id", "")).strip()
    provider = str(body.get("provider", "WATCH_ONLY")).strip().upper()
    address = str(body.get("address", "")).strip()
    network = body.get("network", "mainnet")
    chain_type = str(body.get("chain_type", "ethereum")).strip().lower()
    
    if not wallet_id or not address:
        raise HTTPException(400, "wallet_id and address are required")
    
    # Basic address validation per chain type
    # v2.8: polygon/bsc share the EVM 0x format like ethereum
    if chain_type in ("ethereum", "polygon", "bsc"):
        if not (address.startswith("0x") and len(address) == 42):
            raise HTTPException(400, "Invalid EVM address format (must be 0x + 40 hex chars)")
    elif chain_type == "solana":
        if not (32 <= len(address) <= 44 and address.isalnum()):
            raise HTTPException(400, "Invalid Solana address format (base58, 32-44 chars)")
    elif chain_type == "bitcoin":
        if not (26 <= len(address) <= 62):
            raise HTTPException(400, "Invalid Bitcoin address format")
    
    db_manager.save_wallet(wallet_id, provider, address, network)
    broker_connector.web3_wallets[wallet_id] = {
        "provider": provider, "address": address,
        "network": network, "chain_type": chain_type,
        "type": "WATCH_ONLY",
    }
    db_manager.log_audit("INFO", "WALLET_ADDED",
                         f"Watch-only wallet '{wallet_id}' ({chain_type}/{network}) added")
    return {"success": True, "wallet_id": wallet_id, "type": "WATCH_ONLY"}


@app.delete("/api/wallets/{wallet_id}", dependencies=[Depends(require_admin)])
async def delete_wallet_api(wallet_id: str):
    broker_connector.web3_wallets.pop(wallet_id, None)
    deleted = db_manager.delete_wallet(wallet_id)
    return {"success": deleted, "wallet_id": wallet_id}


@app.get("/api/wallet-balances")
async def get_wallet_balances():
    """v2.8: best-effort watch-only wallet balances (public chain APIs).

    Every wallet stays watch-only; when a chain API is unreachable the entry
    simply reports balance=None so the UI can show '—'."""
    try:
        import asyncio
        balances = await asyncio.wait_for(broker_connector.get_all_balances(), timeout=10.0)
    except Exception:
        balances = {}
    wallets = {wid: data for wid, data in (balances or {}).items()
               if (data or {}).get("type") == "WEB3"}
    return {"wallets": wallets, "synced_at": datetime.now().isoformat()}


@app.get("/api/wallets/{wallet_id}/qr")
async def get_wallet_qr(wallet_id: str):
    """v2.8: QR code (SVG) of a watch-only address, generated server-side.

    No runtime CDN: `segno` (pure Python) renders the SVG locally."""
    wallets = {w.get("wallet_id"): w for w in db_manager.get_wallets()}
    wallet = wallets.get(wallet_id)
    if not wallet:
        raise HTTPException(404, f"Unknown wallet: {wallet_id}")
    try:
        import io
        import segno
    except ImportError:
        raise HTTPException(503, "QR generation requires the 'segno' package") from None
    buffer = io.BytesIO()
    segno.make(str(wallet.get("address", "")), error="m").save(
        buffer, kind="svg", xmldecl=False, svgclass=None,
        lineclass=None, dark="#1d1d1f", light=None, border=2, scale=6)
    return Response(content=buffer.getvalue().decode("utf-8"), media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


# ---- Performance, metrics, news, health, backtest, diagnosis --------------- #
@app.get("/api/performance")
async def get_performance(mode: str = "DEMO"):
    return portfolio_engine.get_performance_report(mode)


@app.get("/api/optimization")
async def get_optimization(mode: str = "DEMO"):
    """Audit & optimization dashboard data (LOT R).

    Live answer to the audit methodology:
    - current capital bracket + the settings it recommends;
    - which markets / asset classes are feasible at the current balance
      (1 $ → 50 $+), and which are realtime vs delayed;
    - the per-market tuning currently applied (entry threshold, SL, TP);
    - the top/bottom closed-trade markets in this mode (most profitable
      markets vs markets needing improvement).
    """
    balance = bot_state.get("balance", 0.0) or 0.0
    feasibility = market_tuning_engine.markets_feasible_for_capital(
        balance, data_engine.universe, leverage_cap=risk_engine.max_leverage)
    best_markets, worst_markets = [], []
    try:
        history = db_manager.get_history(mode=mode, limit=500)
        per_market: Dict[str, Dict[str, Any]] = {}
        for t in history or []:
            mid = t.get("symbol") or t.get("market_id") or "?"
            pnl = float(t.get("pnl") or 0.0)
            m = per_market.setdefault(mid, {"trades": 0, "wins": 0, "pnl": 0.0})
            m["trades"] += 1
            m["pnl"] += pnl
            if pnl > 0:
                m["wins"] += 1
        ranked = sorted(
            ({"market_id": mid, "trades": m["trades"],
              "win_rate": round(m["wins"] / m["trades"] * 100, 1) if m["trades"] else 0.0,
              "net_pnl": round(m["pnl"], 2)} for mid, m in per_market.items()),
            key=lambda x: x["net_pnl"])
        worst_markets = ranked[:5]
        best_markets = list(reversed(ranked[-5:]))
    except Exception as e:
        structured_log(logger, logging.WARNING, "OPTIMIZATION_HISTORY_FAILED",
                       event="optimization_history_failed", error=str(e))
    return {
        "balance": balance,
        "capital_profile": bot_state.get("capital_profile"),
        "recommended_settings": profile_overrides(balance),
        "regime_adaptation_enabled": signal_engine.regime_adaptation_enabled,
        "market_feasibility": feasibility,
        "market_tuning": signal_engine.market_tuning,
        "best_markets": best_markets,
        "worst_markets": worst_markets,
    }


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
        "institutional": core.get("institutional"),
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
    return {
        "providers": report,
        "markets": data_engine.get_market_source_health(),
        "calendar": news_engine.provider.get_state(),
        "provider_capabilities": PROVIDER_CAPABILITIES,
        "news_unavailable_policy": news_engine.news_unavailable_policy,
    }


@app.post("/api/backtest", dependencies=[Depends(require_admin)])
async def run_backtest(body: Dict[str, Any] = Body(...)):
    market_id = str(body.get("market_id", "btc_usdt"))
    timeframe = str(body.get("timeframe", "1h"))
    strategy = str(body.get("strategy", "structure")).lower()
    try:
        limit = int(body.get("limit", 300))
        initial_balance = float(body.get("initial_balance", 10000.0))
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(400, "limit and initial_balance must be numeric") from None
    if limit < 50 or limit > 5000:
        raise HTTPException(400, "limit must be between 50 and 5000")
    if not math.isfinite(initial_balance) or initial_balance <= 0:
        raise HTTPException(400, "initial_balance must be a positive finite number")
    if strategy not in {"rsi", "structure", "arbitrage", "tape", "liquidity"}:
        raise HTTPException(400, "Unknown backtest strategy")

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
    block = classify_block_reason(
        running=bot_state["is_running"],
        armed=bot_state["armed"],
        scanning=bool(bot_state.get("scanning")),
        ticker=snapshot.get("ticker"),
        signal=snapshot.get("signal"),
        news=snapshot.get("news"),
        diagnosis=snapshot.get("diagnosis"),
        delayed=not data_engine.check_scalping_allowed(market_id).get("allowed"),
    )
    return {"market_id": market_id, "diagnosis": snapshot["diagnosis"],
            "signal": snapshot["signal"], "news": snapshot["news"],
            "block_reason": block,
            "last_block_reason": bot_state.get("last_block_reason"),
            "last_scan_age_s": (
                max(0.0, time.time() - bot_state["last_scan_completed_at"])
                if bot_state.get("last_scan_completed_at") else None
            ),
            "active_strategy": "rsi",
            "risk_reward_rsi": DEFAULT_RSI_RISK_REWARD}


async def _execute_signal_for_market(market_id: str) -> Dict[str, Any]:
    if not market_id or not data_engine.universe.get_info(market_id):
        raise HTTPException(400, f"Unknown market_id: {market_id}")
    settings = settings_provider.get()
    try:
        min_score = float(settings.get("min_signal_score", AUTO_EXECUTION_SCORE_FLOOR))
    except ValueError:
        min_score = float(AUTO_EXECUTION_SCORE_FLOOR)
    # v2.7: enforce the inviolable floor regardless of settings
    min_score = max(float(AUTO_EXECUTION_SCORE_FLOOR), min_score)
    scan_hit = next((a for a in (bot_state.get("latest_scan") or []) if a.get("symbol") == market_id), None)
    sig = (scan_hit or {}).get("signal_data") if scan_hit else None
    if not sig:
        snap = await get_market_snapshot(market_id)
        sig = snap.get("signal") or {}
    # Automatic execution is RSI-only in v2.9. Manual orders use their
    # dedicated endpoint and are intentionally not treated as signals.
    if str(sig.get("strategy", "")).lower() != "rsi":
        return {"success": False, "reason": "Only RSI strategy signals are auto-executable"}
    score = float((scan_hit or {}).get("score") or sig.get("score") or 0)
    if score < min_score:
        return {"success": False, "reason": f"Score {score} below min_signal_score {min_score} (floor {AUTO_EXECUTION_SCORE_FLOOR})"}
    if sig.get("status") != "SIGNAL_DETECTED" or not sig.get("entry"):
        return {"success": False, "reason": "No SIGNAL_DETECTED / missing data"}
    # v2.7 P0-5: check tradable flag (arbitrage is not auto-executable)
    if sig.get("tradable") is False:
        return {"success": False, "reason": sig.get("main_reason", "NOT_AUTO_TRADABLE")}
    allow_delayed = settings.get("allow_delayed_data_trading", "false").lower() == "true"
    scalp = data_engine.check_scalping_allowed(market_id, allow_delayed)
    if not scalp["allowed"]:
        return {"success": False, "reason": scalp["reason"]}
    active = bot_state.get("active_trades") or []
    if any(p.get("symbol") == market_id for p in active):
        return {"success": False, "reason": "Position already open"}
    info = data_engine.universe.get_info(market_id) or {}
    ticker = await data_engine.fetch_ticker(market_id)
    if not ticker:
        return {"success": False, "reason": "No market data available"}
    # v2.7 P0-4: cost gate
    costs = compute_trade_costs(
        entry=sig["entry"], sl=sig["sl"], tp=sig["tp"],
        fee_pct=float(settings.get("fee_pct", 0.05)),
        slippage_pct=float(settings.get("sim_slippage_pct", 0.05)),
        spread=float(ticker.get("spread", 0) or 0),
    )
    cost_gate = costs_pass_gate(costs)
    if not cost_gate.get("allowed"):
        return {"success": False, "reason": cost_gate.get("reason")}
    risk_data = risk_engine.calculate_position_size(
        bot_state["balance"], sig["entry"], sig["sl"], sig.get("direction", "BUY"),
        symbol=market_id, active_positions=active, market_info=info)
    if not risk_data.get("allowed"):
        return {"success": False, "reason": risk_data.get("reason") or "risk"}
    return await execution_router.execute(bot_state["mode"], sig, risk_data, ticker)


@app.post("/api/execute-signal", dependencies=[Depends(require_admin)])
async def execute_signal_api(body: Dict[str, Any] = Body(...)):
    market_id = body.get("market_id")
    if not market_id:
        raise HTTPException(400, "market_id is required")
    return await _execute_signal_for_market(str(market_id))


@app.get("/api/opportunities")
async def get_opportunities():
    """v2.7 P0-2: Returns the current opportunity ranking."""
    ranking = bot_state.get("opportunity_ranking") or {}
    tracker = get_tracker()
    return {
        **ranking,
        "tracker_stats": tracker.get_stats(),
        "floor": AUTO_EXECUTION_SCORE_FLOOR,
        "last_block_reason": bot_state.get("last_block_reason"),
        "engine_stats": bot_state.get("engine_stats"),
        "markets_total": (bot_state.get("engine_stats") or {}).get("markets", 0),
        "excluded": ranking.get("excluded", []),
    }


@app.get("/api/broker-capabilities")
async def get_broker_capabilities():
    """v2.7 P1-9: Returns broker capabilities from the catalogue."""
    brokers = db_manager.get_broker_public_list()
    capabilities = []
    for broker in brokers:
        cap = {
            "broker_id": broker.get("broker_id"),
            "exchange_id": broker.get("exchange_id"),
            "is_active": broker.get("is_active"),
            "passphrase_required": broker.get("exchange_id") in ("okx", "kucoin"),
            "spot_supported": True,
            "futures_supported": broker.get("exchange_id") in ("binance", "bybit", "okx", "gate", "bitget"),
            "sandbox_supported": broker.get("exchange_id") in ("binance", "bybit", "okx", "bitget", "kucoin"),
            "native_sl_tp": broker.get("exchange_id") in ("binance", "bybit", "okx"),
            "reduce_only": True,
            "fetch_positions": True,
            "fetch_orders": True,
            "fetch_fees": True,
            "runtime_status": "UNKNOWN",
            "sandbox": False,
            "latency_ms": None,
            "last_ok": None,
            "last_error_code": None,
            "balance": None,
            "permissions": [],
            "routable_markets_count": 0,
            "open_positions_count": 0,
            "open_orders_count": 0,
        }
        # Try to get runtime status from broker_connector
        adapter = broker_connector.active_adapters.get(broker.get("broker_id"))
        if adapter:
            cap["runtime_status"] = "CONNECTED" if getattr(adapter, "_connected", False) else "DISCONNECTED"
            cap["sandbox"] = getattr(adapter, "sandbox", False)
        capabilities.append(cap)
    return {
        "capabilities": capabilities,
        "total_brokers": len(brokers),
        "connected_brokers": len([c for c in capabilities if c["runtime_status"] == "CONNECTED"]),
    }


@app.get("/api/orderbook")
async def get_orderbook(market_id: str = "btc_usdt"):
    info = data_engine.universe.get_info(market_id)
    if not info:
        raise HTTPException(400, f"Unknown market_id: {market_id}")
    book = await data_engine.fetch_order_book(market_id)
    ticker = await data_engine.fetch_ticker(market_id)
    now_ms = int(time.time() * 1000)
    age = None
    if ticker and ticker.get("timestamp"):
        age = max(0, now_ms - int(ticker["timestamp"]))
    bids = (book or {}).get("bids") or []
    asks = (book or {}).get("asks") or []
    return {
        "market_id": market_id,
        "display_symbol": info.get("display_symbol"),
        "bids": bids[:15],
        "asks": asks[:15],
        "available": bool(bids or asks),
        "data_age_ms": age,
        "realtime_source": data_engine.is_realtime_capable(market_id),
    }


@app.get("/api/ohlcv")
async def get_ohlcv(market_id: str = "btc_usdt", timeframe: str = "1m", limit: int = 60):
    info = data_engine.universe.get_info(market_id)
    if not info:
        raise HTTPException(400, f"Unknown market_id: {market_id}")
    df = await data_engine.fetch_ohlcv(market_id, timeframe=timeframe, limit=limit)
    candles = []
    if df is not None and not getattr(df, "empty", True):
        cols = {c.lower(): c for c in df.columns}
        tcol = cols.get("timestamp") or cols.get("time")
        for _, row in df.tail(limit).iterrows():
            tval = row[tcol] if tcol else 0
            try:
                t_i = int(tval)
            except Exception:
                t_i = 0
            candles.append({
                "t": t_i,
                "o": float(row[cols.get("open", "Open")]),
                "h": float(row[cols.get("high", "High")]),
                "l": float(row[cols.get("low", "Low")]),
                "c": float(row[cols.get("close", "Close")]),
                "v": float(row[cols.get("volume", "Volume")]) if (cols.get("volume") or "Volume" in df.columns) else 0.0,
            })
    analysis = analysis_engine.identify_structure(df) if df is not None and not getattr(df, "empty", True) else {}
    return {
        "market_id": market_id,
        "timeframe": timeframe,
        "candles": candles,
        "bos": bool(analysis.get("bos")),
        "choch": bool(analysis.get("choch")),
        "last_high": analysis.get("last_high") or (candles[-1]["h"] if candles else None),
        "last_low": analysis.get("last_low") or (candles[-1]["l"] if candles else None),
        "trend": analysis.get("trend"),
    }


# ---- WebSocket + static ---------------------------------------------------- #
_serverless_ws_warned = {"done": False}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    if is_serverless_runtime():
        # P0-4 (2026-08-23): on a serverless runtime the heartbeat/broadcast
        # loops are disabled — a connected WS client will never receive
        # HEARTBEAT or stream frames and must poll /api/status instead.
        # Say it once, clearly, instead of letting the client look broken.
        if not _serverless_ws_warned["done"]:
            _serverless_ws_warned["done"] = True
            logger.warning(
                "SERVERLESS RUNTIME: WebSocket heartbeat/broadcast loops are "
                "disabled — no HEARTBEAT will be sent on /ws. Clients stay "
                "connected but MUST poll GET /api/status for live state."
            )
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
def apply_startup_automation(settings: Dict[str, str]) -> None:
    """Apply persisted startup intent without ever enabling REAL mode."""
    auto_arm = str(settings.get("auto_arm_on_startup", "false")).lower() == "true"
    auto_start = str(settings.get("auto_start_on_startup", "false")).lower() == "true"
    # Arming implies starting: an armed-but-stopped process was the original
    # production ambiguity. auto_start alone intentionally remains unarmed.
    bot_state["armed"] = auto_arm
    bot_state["is_running"] = auto_arm or auto_start
    if bot_state["is_running"]:
        state_machine.transition_to(BotState.RUNNING)
    else:
        state_machine.transition_to(BotState.STOPPED)
    bot_state["execution_intent"] = describe_intent(
        bot_state["is_running"], bot_state["armed"], 0,
        len(bot_state.get("active_trades") or []), risk_engine.max_open_positions,
        signal_engine.min_score,
    )


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
    apply_startup_automation(settings_provider.get())
    restore_latest_scan()

    if is_serverless_runtime():
        logger.warning(
            "Serverless runtime detected — background scanner loops are disabled. "
            "Use Railway (python3 -m api.index) or POST /api/scanner/trigger."
        )
        tasks = []
    else:
        tasks = [
            # Do not await the whole universe: expose progress while startup stays
            # responsive. Crypto rows are emitted first by ScannerEngine.
            asyncio.create_task(tick_scanner(force=True)),
            asyncio.create_task(loop_wrapper(tick_capital, 1.0, "tick_capital")),
            asyncio.create_task(loop_wrapper(tick_scanner, 5.0, "tick_scanner")),
            asyncio.create_task(loop_wrapper(tick_management, 1.0, "tick_management")),
            asyncio.create_task(loop_wrapper(tick_broadcaster, 1.0, "tick_broadcaster")),
            asyncio.create_task(loop_wrapper(tick_heartbeat, HEARTBEAT_INTERVAL_S, "tick_heartbeat")),
        ]
    try:
        yield
    finally:
        logger.info("QUANTUM TRADE PRO SHUTTING DOWN...")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await broker_connector.shutdown()
        except Exception as exc:
            logger.warning("Broker shutdown failed: %s", exc)
        try:
            await data_engine.shutdown()
        except Exception as exc:
            logger.warning("Market-data shutdown failed: %s", exc)


app.router.lifespan_context = lifespan

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
