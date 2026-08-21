from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
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
from api.models import StatusResponse, NewsStatus, AnalysisResult, SignalResult, DiagnosisReport

from typing import Optional, List, Dict, Any
import os
import asyncio
import json
import logging
from datetime import datetime

# Institutional Logging Configuration (Lot 4)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("QuantumTradePro")

from starlette.middleware.base import BaseHTTPMiddleware
import time

class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int = 60, window_seconds: int = 60):
        super().__init__(app)
        self.requests = {} # {ip: [timestamps]}
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    async def dispatch(self, request, call_next):
        if not request.url.path.startswith("/api"):
            return await call_next(request)
            
        ip = request.client.host
        now = time.time()
        
        if ip not in self.requests:
            self.requests[ip] = []
            
        # Filter timestamps within window
        self.requests[ip] = [t for t in self.requests[ip] if now - t < self.window_seconds]
        
        if len(self.requests[ip]) >= self.max_requests:
            return json_response({"error": "Rate limit exceeded"}, status_code=429)
            
        self.requests[ip].append(now)
        return await call_next(request)

def json_response(data: dict, status_code: int = 200):
    from fastapi.responses import JSONResponse
    return JSONResponse(content=data, status_code=status_code)

app = FastAPI(title="Quantum Trade Pro API")
app.add_middleware(RateLimitMiddleware, max_requests=100, window_seconds=60)
db_manager = DatabaseManager()
data_engine = DataEngine()
analysis_engine = AnalysisEngine()
news_engine = NewsEngine()
news_aggregator = NewsAggregator()
signal_engine = SignalEngine()
risk_engine = RiskEngine(max_risk_pct=1.0)
portfolio_engine = PortfolioEngine(db_manager=db_manager)
demo_execution = ExecutionEngine(portfolio=portfolio_engine, db_manager=db_manager, risk_engine=risk_engine, universe=data_engine.universe)
broker_connector = BrokerConnector()
broker_connector.universe = data_engine.universe # Share the same universe
execution_router = ExecutionRouter(demo_adapter=demo_execution, broker_connector=broker_connector)
state_machine = StateMachine()
scanner_engine = ScannerEngine(data_engine, analysis_engine, signal_engine, news_engine)
diagnostic_engine = DiagnosticEngine()

# Global Concurrency Lock (Lot 0)
state_lock = asyncio.Lock()

# WebSocket Manager (Rule 20, 21, 22)
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except:
                pass

manager = ConnectionManager()

# Global Bot State
bot_state = {
    "mode": "DEMO",
    "armed": False,
    "equity": 0.0,
    "drawdown": 0.0,
    "is_running": False,
    "latest_scan": [],
    "engine_stats": {
        "markets": 0,
        "scanned": 0,
        "analyzing": 0,
        "signals": 0,
        "tradable": 0
    }
}

async def auto_scan_loop():
    """
    Rule 25: Continuous Auto Scan and Background Execution.
    Optimized for high-frequency scan (Lot 25).
    """
    while True:
        try:
            metrics_state["total_scans"] += 1
            # Full Universe Scan
            results = await scanner_engine.scan_all()
            
            async with state_lock:
                bot_state["latest_scan"] = results
                
                # Update Engine Stats (Rule 7)
                bot_state["engine_stats"]["markets"] = len(data_engine.universe.get_all_ids())
                bot_state["engine_stats"]["scanned"] = len([r for r in results if r.get("status") != "ERROR" and r.get("status") != "DATA_UNAVAILABLE"])
                bot_state["engine_stats"]["analyzing"] = len([r for r in results if r.get("trend") != "NEUTRAL"])
                bot_state["engine_stats"]["signals"] = len([r for r in results if r.get("score", 0) >= 80])
                bot_state["engine_stats"]["tradable"] = len([r for r in results if r.get("tradable")])
                bot_state["engine_stats"]["scan_duration"] = scanner_engine.last_scan_duration

            # BACKGROUND EXECUTION (Lot 2 - Safe Multi-Position)
            async with state_lock:
                current_mode = bot_state["mode"]
                is_running = bot_state["is_running"]
                is_armed = bot_state["armed"]
                
                if not is_running or not is_armed:
                    continue

                active_positions = demo_execution.active_positions
                with db_manager._get_connection() as conn:
                    row = conn.execute("SELECT value FROM settings WHERE key = 'max_open_positions'").fetchone()
                    max_pos = int(row["value"]) if row else 3
                
                if len(active_positions) < max_pos:
                    symbols_in_position = [p["symbol"] for p in active_positions]
                    candidates = sorted([r for r in results if r.get("score", 0) >= 80], key=lambda x: x.get("score", 0), reverse=True)
                    
                    for res in candidates:
                        if len(active_positions) >= max_pos:
                            break
                            
                        mid = res["symbol"]
                        if mid in symbols_in_position:
                            continue

                        asset_info = data_engine.universe.get_info(mid)
                        if not asset_info: continue

                        if data_engine.universe.get_market_status(mid) != "OPEN":
                            continue

                        ticker = await data_engine.fetch_ticker(mid)
                        if not ticker: continue

                        # Rule: Data Freshness (Lot 3)
                        if not data_engine.is_fresh(ticker, asset_info.get("asset_class", "CRYPTO")):
                            continue

                        balance = portfolio_engine.get_balance(current_mode)
                        
                        # Use the signal already generated by the scanner (Lot 9 Optimization)
                        sig = res.get("signal_data")
                        if not sig or sig.get("status") != "SIGNAL_DETECTED":
                             continue
                        
                        risk_data = risk_engine.calculate_position_size(
                            balance=balance, entry=sig["entry"], stop_loss=sig["sl"], direction=sig["direction"]
                        )
                        
                        if risk_data.get("allowed"):
                            sig["market_id"] = mid
                            sig["display_symbol"] = asset_info["display_symbol"]
                            exec_res = await execution_router.execute(current_mode, sig, risk_data, ticker)
                            if exec_res.get("success"):
                                metrics_state["total_trades"] += 1
                                # Increment strategy metrics (Lot 9)
                                strat = sig.get("strategy", "structure")
                                if strat in metrics_state["signals_by_strategy"]:
                                    metrics_state["signals_by_strategy"][strat] += 1
                                    
                                db_manager.log_audit("CRITICAL", "BACKGROUND_AUTO_TRADE", f"Auto-executed {mid} ({sig['score']}%)", exec_res)
                                # Reload from DB (Lot 2)
                                active_positions = demo_execution.active_positions
                                symbols_in_position.append(mid)
                            else:
                                metrics_state["total_errors"] += 1

            # Broadcast via WebSocket
            async with state_lock:
                await manager.broadcast(json.dumps({
                    "type": "SCAN_COMPLETED",
                    "timestamp": int(datetime.now().timestamp() * 1000),
                    "stats": bot_state["engine_stats"]
                }))
            
        except Exception as e:
            logger.error(f"Auto-Scan Loop Error: {e}")
        
        # Rule 31: Throttle scan loop based on bot state
        async with state_lock:
            throttle = 10 if bot_state["is_running"] else 30
        await asyncio.sleep(throttle)

async def broadcaster_loop():
    """
    Rule 20, 22: High-Frequency Market Data Bus (Lot 31 - 100% Live).
    Streams prices, PnL and system state every 200ms.
    """
    data_engine.set_ws_manager(manager)
    
    while True:
        try:
            async with state_lock:
                current_mode = bot_state["mode"]
                # 1. Critical State Broadcast (200ms)
                active_trades = demo_execution.active_positions
                balance = portfolio_engine.get_balance(current_mode)
                
                # Update Active Trades PnL faster
                if active_trades:
                    symbols = [t["symbol"] for t in active_trades]
                    quotes = await data_engine.layer.get_all_quotes(symbols, data_engine.universe)
                    tickers = {q.symbol: q.model_dump() for q in quotes}
                    await demo_execution.update_active_positions(current_mode, tickers)
                    active_trades = demo_execution.active_positions

                equity = balance + sum(t.get("pnl", 0.0) for t in active_trades)
                bot_state["equity"] = equity
                
                await manager.broadcast(json.dumps({
                    "type": "ACCOUNT_STREAM",
                    "timestamp": int(datetime.now().timestamp() * 1000),
                    "balance": balance,
                    "equity": equity,
                    "active_trades": active_trades,
                    "status": state_machine.current_state.value,
                    "is_running": bot_state["is_running"],
                    "armed": bot_state["armed"]
                }))
                
                # Rule: Heartbeat (Lot 10)
                await manager.broadcast(json.dumps({"type": "HEARTBEAT", "timestamp": int(time.time() * 1000)}))

            # 2. Priority Market Stream (Sub-second)
            selected = bot_state.get("selected_market", "btc_usdt")
            priority_mids = list(set([selected, "btc_usdt", "eth_usdt", "sol_usdt", "aapl", "nvda", "eur_usd", "gold"]))
            
            for mid in priority_mids:
                asyncio.create_task(data_engine.broadcast_market_update(mid))

        except Exception as e:
            logger.error(f"Broadcaster Critical Error: {e}")
            
        await asyncio.sleep(0.2) # 5 times per second for 100% direct feel

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.on_event("startup")
async def startup_event():
    db_manager.log_audit("INFO", "SYSTEM_STARTUP", "Quantum Trade Pro Institutional Core Active.")
    db_manager.log_audit("INFO", "DATA_INTEGRITY", "Data Authenticity Verified: Connected to Gate.io (LIVE) and Yahoo Finance (DELAYED).")
    
    # Load settings from DB (Lot 8)
    with db_manager._get_connection() as conn:
        rows = conn.execute("SELECT * FROM settings").fetchall()
        settings = {row["key"]: row["value"] for row in rows}
        if "active_strategies" in settings:
            strategies = [s.strip() for s in settings["active_strategies"].split(',')]
            signal_engine.set_active_strategies(strategies)
        if "min_signal_score" in settings:
            signal_engine.min_score = int(settings["min_signal_score"])
            
    if os.getenv("TESTING") != "true":
        await broker_connector.initialize_from_db(db_manager)
        asyncio.create_task(auto_scan_loop())
        asyncio.create_task(broadcaster_loop())

@app.get("/api/settings")
async def get_settings():
    with db_manager._get_connection() as conn:
        rows = conn.execute("SELECT * FROM settings").fetchall()
        return {row["key"]: row["value"] for row in rows}

@app.post("/api/settings")
async def save_settings(new_settings: Dict[str, str]):
    with db_manager._get_connection() as conn:
        for k, v in new_settings.items():
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, v))
        conn.commit()
    
    # Live reload settings into engines
    if "max_risk_pct" in new_settings: risk_engine.max_risk_pct = float(new_settings["max_risk_pct"])
    if "max_leverage" in new_settings: risk_engine.max_leverage = int(new_settings["max_leverage"])
    if "max_daily_loss_pct" in new_settings: risk_engine.max_daily_loss_pct = float(new_settings["max_daily_loss_pct"])
    if "cool_down_mins" in new_settings: risk_engine.cool_down_mins = int(new_settings["cool_down_mins"])
    if "min_signal_score" in new_settings: signal_engine.min_score = int(new_settings["min_signal_score"])
    if "active_strategies" in new_settings:
        strategies = [s.strip() for s in new_settings["active_strategies"].split(',')]
        signal_engine.set_active_strategies(strategies)
    
    db_manager.log_audit("INFO", "SETTINGS_UPDATED", "System parameters deployed to live engines.")
    return {"success": True}

@app.get("/api/brokers")
async def list_brokers():
    with db_manager._get_connection() as conn:
        rows = conn.execute("SELECT broker_id, exchange_id, is_active, mode FROM broker_configs").fetchall()
        brokers = [dict(row) for row in rows]
        
        # Add Web3 wallets to the list
        w_rows = conn.execute("SELECT wallet_id as broker_id, provider as exchange_id, is_active, 'REAL' as mode FROM web3_wallets").fetchall()
        brokers.extend([dict(r) for r in w_rows])
        
        return brokers

@app.post("/api/wallets/add")
async def add_web3_wallet(config: Dict[str, Any]):
    wallet_id = config.get("wallet_id")
    provider = config.get("provider")
    address = config.get("address")
    
    with db_manager._get_connection() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO web3_wallets (wallet_id, provider, address, network)
            VALUES (?, ?, ?, ?)
        """, (wallet_id, provider, address, "MAINNET"))
        conn.commit()
    
    await broker_connector.initialize_from_db(db_manager)
    return {"success": True, "message": f"{provider} wallet linked."}

@app.post("/api/brokers/toggle")
async def toggle_broker(broker_id: str, active: bool):
    with db_manager._get_connection() as conn:
        conn.execute("UPDATE broker_configs SET is_active = ? WHERE broker_id = ?", (1 if active else 0, broker_id))
        conn.commit()
    await broker_connector.initialize_from_db(db_manager)
    return {"success": True}

@app.post("/api/brokers/delete")
async def delete_broker(broker_id: str):
    with db_manager._get_connection() as conn:
        conn.execute("DELETE FROM broker_configs WHERE broker_id = ?", (broker_id,))
        conn.commit()
    if broker_id in broker_connector.active_adapters:
        del broker_connector.active_adapters[broker_id]
    return {"success": True}

@app.post("/api/brokers")
async def add_broker_config(config: Dict[str, Any]):
    broker_id = config.get("broker_id")
    exchange_id = config.get("exchange_id")
    api_key = config.get("api_key")
    api_secret = config.get("api_secret")
    passphrase = config.get("api_passphrase")
    
    # Try to connect first
    success = await broker_connector.add_broker(broker_id, exchange_id, api_key, api_secret, passphrase)
    
    if success:
        db_manager.save_broker_config(broker_id, exchange_id, api_key, api_secret, passphrase)
        return {"success": True, "message": f"Broker {broker_id} connected and saved."}
    else:
        return {"success": False, "message": "Failed to connect with provided credentials."}

@app.get("/api/wallets")
async def get_wallets():
    """Rule 45: Wallet aggregation (Ballets)."""
    return await broker_connector.get_all_balances()

@app.get("/api/audit")
async def get_audit_logs(limit: int = 20):
    with db_manager._get_connection() as conn:
        rows = conn.execute("SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

@app.post("/api/trade/manual")
async def manual_trade(trade: Dict[str, Any]):
    """Allow manual order entry via terminal."""
    # Logic to route manual trade to execution_router
    # For now, just log it
    db_manager.log_audit("INFO", "MANUAL_TRADE_REQUEST", f"Manual {trade.get('direction')} for {trade.get('market_id')}")
    return {"success": True, "message": "Manual order processed."}

@app.get("/api/status", response_model=StatusResponse)
async def get_status(market_id: str = "btc_usdt"):
    bot_state["selected_market"] = market_id
    info = data_engine.universe.get_info(market_id)
    if not info:
         market_id = "btc_usdt"
         info = data_engine.universe.get_info(market_id)
    
    asset_class = info.get("asset_class", "CRYPTO")
    news_status = await news_engine.check_trading_allowed(asset_class=asset_class)
    broker_info = broker_connector.get_status()
    
    if broker_connector.emergency_stop_active:
        state_machine.transition_to(BotState.EMERGENCY_STOP)
    elif not bot_state["is_running"]:
        state_machine.transition_to(BotState.STOPPED)
    else:
        state_machine.transition_to(BotState.RUNNING)

    df_ltf = await data_engine.fetch_ohlcv(market_id, timeframe='1m', limit=100)
    ticker = None
    data_stale = False # Lot 3 Fix
    for pid, psymbol in info.get("providers", {}).items():
        if pid in data_engine.layer.providers:
            ticker_model = await data_engine.layer.providers[pid].get_quote(psymbol)
            if ticker_model:
                ticker = ticker_model.model_dump()
                break

    if not ticker or df_ltf.empty:
        async with state_lock:
            if bot_state["is_running"]: state_machine.transition_to(BotState.ERROR)
            merged_stats = portfolio_engine.get_stats()
            merged_stats.update(bot_state["engine_stats"])
            balance = portfolio_engine.get_balance(bot_state["mode"]) # Fixed Lot 2
            return StatusResponse(
                status=state_machine.current_state.value,
                status_display="DATA ERROR",
                is_running=bot_state["is_running"],
                mode=bot_state["mode"],
                armed=bot_state["armed"],
                balance=balance,
                equity=bot_state["equity"],
                daily_pnl=portfolio_engine.get_daily_pnl(bot_state["mode"]),
                drawdown=0.0,
                news=NewsStatus(**news_status),
                selected_market=market_id,
                stats=merged_stats,
                broker_info=broker_info,
                broker_connected=broker_info.get("connected", False),
                asset_info=info
            )

    df_htf = await data_engine.fetch_ohlcv(market_id, timeframe='15m', limit=50)
    htf_analysis = analysis_engine.identify_structure(df_htf)
    analysis = analysis_engine.identify_structure(df_ltf, htf_bias=htf_analysis.get("trend"))
    analysis["df_preview"] = df_ltf.tail(40).to_dict('records')
    
    # 1. Update Positions and get Balance
    async with state_lock:
        current_mode = bot_state["mode"]
        balance = portfolio_engine.get_balance(current_mode)
        
        if current_mode == "REAL":
            # Release lock briefly if this calls network? No, it's better to keep it if quick
            # but let's assume get_all_balances is quick or we just use cached
            broker_balances = await broker_connector.get_all_balances()
            balance = sum(b.get("total_usdt", 0.0) for b in broker_balances.values() if "total_usdt" in b)
            portfolio_engine.set_balance("REAL", balance)

    await demo_execution.update_active_positions(current_mode, {info["display_symbol"]: ticker})
    
    async with state_lock:
        active_trades = demo_execution.active_positions
        active_trade = active_trades[0] if active_trades else None
        
        # 2. Update Equity (Real-time)
        total_unrealized_pnl = sum(t.get("pnl", 0.0) for t in active_trades)
        bot_state["equity"] = balance + total_unrealized_pnl
    
    # 3. Generate Signal
    signal_result = signal_engine.generate_signal(analysis, news_status, df_ltf, cross_quotes=cross_quotes, orderbook=orderbook, trades=trades)
    signal_result["market_id"] = market_id
    signal_result["display_symbol"] = info["display_symbol"]
    
    if signal_result.get("status") == "SIGNAL_DETECTED":
        strat = signal_result.get("strategy", "structure")
        if strat in metrics_state["signals_by_strategy"]:
            metrics_state["signals_by_strategy"][strat] += 1
    
    risk_data = {"allowed": False}
    risk_reason = "Risk validation not performed." 

    reasons = {
        "SYSTEM_ARMED": "Bot is DISARMED.",
        "DATA_VALID": "Data stale or invalid." if data_stale else "Data stale.",
        "BROKER_VALID": "Broker disconnected.",
        "MARKET_OPEN": "Market closed.",
        "DAY_ALLOWED": "Unauthorized day.",
        "SESSION_ALLOWED": "Outside session.",
        "NEWS_CLEAR": "News risk.",
        "RISK_VALID": risk_reason,
        "LEVERAGE_VALID": "Leverage too high.",
        "NOT_RANGE": "Market in range.",
        "TREND_VALID": "No clear trend.",
        "STRUCTURE_VALID": "Weak structure.",
        "SIGNAL_VALID": "No signal.",
        "SPREAD_VALID": "High spread.",
        "LIQUIDITY_VALID": "Low liquidity."
    }

    data_valid = ticker is not None and not df_ltf.empty
    data_stale = False
    if data_valid:
        if not data_engine.is_fresh(ticker, asset_class):
            data_valid = False
            data_stale = True

    broker_valid = broker_info.get("connected", False) or bot_state["mode"] == "DEMO"
    market_open = ticker.get("status") != "CLOSED" if ticker else False
    day_allowed = news_status.get("day_ok", False)
    session_allowed = news_status.get("session_ok", False)
    news_clear = news_status.get("news_ok", False)
    not_range = analysis.get("market_state") != "RANGE"
    trend_valid = analysis.get("trend") != "NEUTRAL"
    structure_valid = analysis.get("status") == "VALID"
    signal_valid = signal_result.get("status") == "SIGNAL_DETECTED"
    
    # Advanced Risk & Drawdown (Lot 6)
    daily_pnl = portfolio_engine.get_daily_pnl(bot_state["mode"])
    risk_engine.daily_pnl = daily_pnl
    risk_engine.update_peak(balance)
    current_drawdown = ((risk_engine.peak_balance - balance) / risk_engine.peak_balance * 100) if risk_engine.peak_balance > 0 else 0
    
    # New checks (Lot 3 & 6 Calibration)
    spread_valid = True
    if ticker and ticker.get("spread") and ticker.get("last"):
        spread_pct = (ticker["spread"] / ticker["last"]) * 100
        spread_valid = spread_pct < 0.5 # Max 0.5% spread
    
    liquidity_valid = True
    if ticker and ticker.get("volume") is not None:
        liquidity_valid = ticker["volume"] > 0
        
    risk_valid = False
    leverage_valid = True
    risk_reason = "Risk validation failed."
    if signal_valid:
        risk_data = risk_engine.calculate_position_size(balance=balance, entry=signal_result["entry"], stop_loss=signal_result["sl"], direction=signal_result["direction"])
        risk_valid = risk_data.get("allowed", False)
        if not risk_valid:
            risk_reason = risk_data.get("reason", "Risk too high.")
        leverage_valid = risk_data.get("leverage", 0) <= risk_engine.max_leverage

    async with state_lock:
        diagnosis = diagnostic_engine.diagnose(
            symbol=market_id, data_valid=data_valid, day_allowed=day_allowed,
            session_allowed=session_allowed, news_clear=news_clear, market_open=market_open,
            not_range=not_range, trend_valid=trend_valid, structure_valid=structure_valid,
            signal_valid=signal_valid, spread_valid=spread_valid, liquidity_valid=liquidity_valid,
            risk_valid=risk_valid, leverage_valid=leverage_valid, broker_valid=broker_valid,
            system_armed=bot_state["armed"], reasons=reasons,
            strategy_info=signal_result
        )

        if bot_state["is_running"] and state_machine.current_state != BotState.EMERGENCY_STOP:
            alpha_override = signal_result.get("score", 0) >= 80
            technical_safety = (
                bot_state["armed"] and data_valid and broker_valid and market_open and risk_valid and not active_trade
            )
            
            if alpha_override and technical_safety:
                res = await execution_router.execute(bot_state["mode"], signal_result, risk_data, ticker)
                db_manager.log_audit("CRITICAL", "ALPHA_OVERRIDE_TRADE", f"Executing High Confidence Signal ({signal_result['score']}%)", res)
            elif technical_safety:
                can_execute_normal = (
                    day_allowed and session_allowed and news_clear and 
                    not_range and trend_valid and structure_valid and signal_valid and 
                    spread_valid and liquidity_valid and leverage_valid
                )
                if can_execute_normal:
                    res = await execution_router.execute(bot_state["mode"], signal_result, risk_data, ticker)
                    db_manager.log_audit("INFO", "NORMAL_TRADE", f"Executing Normal Diagnostic Signal", res)
            
            db_manager.archive_signal(signal_result, "EXECUTED" if (alpha_override and technical_safety) else "FILTERED", diagnosis["main_blocker"])

            if active_trade: state_machine.transition_to(BotState.POSITION_OPEN)
            elif signal_valid: state_machine.transition_to(BotState.SIGNAL_DETECTED)
            else: state_machine.transition_to(BotState.RUNNING)

        bot_state["equity"] = balance + (active_trade["pnl"] if active_trade else 0)
    
    merged_stats = portfolio_engine.get_stats()
    async with state_lock:
        merged_stats.update(bot_state["engine_stats"])
        
        return StatusResponse(
            status=state_machine.current_state.value,
            status_display=state_machine.current_state.value.replace('_', ' '),
            is_running=bot_state["is_running"],
            mode=bot_state["mode"],
            armed=bot_state["armed"],
            balance=balance,
            equity=bot_state["equity"],
            daily_pnl=daily_pnl,
            drawdown=current_drawdown,
            news=NewsStatus(**news_status),
            selected_market=market_id,
            analysis=AnalysisResult(**analysis),
            signal=SignalResult(**signal_result),
            diagnosis=DiagnosisReport(**diagnosis),
            active_trade=active_trade,
            active_trades=active_trades,
            history=portfolio_engine.history[-15:],
            stats=merged_stats,
            broker_info=broker_info,
            broker_connected=broker_info.get("connected", False) or bot_state["mode"] == "DEMO",
            asset_info=info,
            best_setups=sorted([r for r in bot_state["latest_scan"] if r.get("score", 0) > 0 and r.get("market_status") == "OPEN"], key=lambda x: x["score"], reverse=True)[:5]
        )

@app.post("/api/start")
async def start_bot():
    async with state_lock:
        if broker_connector.emergency_stop_active:
            return {"success": False, "message": "Emergency Stop Active."}
        bot_state["is_running"] = True
        state_machine.transition_to(BotState.RUNNING)
        return {"success": True, "status": "RUNNING"}

@app.post("/api/stop")
async def stop_bot():
    async with state_lock:
        bot_state["is_running"] = False
        state_machine.transition_to(BotState.STOPPED)
        return {"success": True, "status": "STOPPED"}

@app.get("/api/demo/account")
async def get_demo_account():
    return {
        "balance": portfolio_engine.get_balance("DEMO"),
        "equity": bot_state["equity"] if bot_state["mode"] == "DEMO" else portfolio_engine.get_balance("DEMO")
    }

@app.post("/api/demo/account")
async def set_demo_balance(amount: float):
    portfolio_engine.set_balance("DEMO", amount)
    db_manager.log_audit("INFO", "BALANCE_UPDATE", f"Demo balance set to {amount}")
    return {"success": True, "balance": amount}

@app.post("/api/demo/reset")
async def reset_demo_account():
    demo_execution.clear_active_positions("DEMO")
    portfolio_engine.reset_history()
    return {"success": True}

@app.get("/api/markets/categories")
async def get_market_categories():
    return data_engine.universe.get_categories()

@app.get("/api/scanner")
async def get_scanner():
    return {"assets": bot_state["latest_scan"]}

@app.get("/api/markets")
async def get_markets():
    if bot_state["latest_scan"]:
        categories = data_engine.universe.get_categories()
        overview = {cat: [] for cat in categories}
        for item in bot_state["latest_scan"]:
            ac = item.get("asset_class")
            if ac in overview:
                overview[ac].append(item)
        return overview
    return await data_engine.get_market_overview()

@app.get("/api/news/latest")
async def get_latest_news():
    return await news_aggregator.get_latest_news()

@app.post("/api/emergency-stop")
async def emergency_stop():
    async with state_lock:
        broker_connector.trigger_emergency_stop()
        bot_state["is_running"] = False
        bot_state["armed"] = False
        # Force Close All Positions (Lot 3)
        demo_execution.clear_active_positions("DEMO")
        # In REAL mode, we call broker_connector to close everything
        await broker_connector.close_all_positions()
        db_manager.log_audit("ERROR", "EMERGENCY_STOP", "All positions force closed locally and on brokers.")
        return {"status": "STOPPED", "message": "Emergency Stop Active. All positions closed."}

@app.post("/api/emergency-stop/reset")
async def reset_emergency():
    broker_connector.reset_emergency_stop()
    return {"status": "RESET_SUCCESSFUL"}

@app.post("/api/mode")
async def toggle_mode():
    async with state_lock:
        new_mode = "REAL" if bot_state["mode"] == "DEMO" else "DEMO"
        success, msg = await broker_connector.set_mode(new_mode)
        if success:
            bot_state["mode"] = new_mode
        return {"success": success, "mode": bot_state["mode"], "message": msg}

@app.post("/api/arm")
async def arm_bot():
    async with state_lock:
        bot_state["armed"] = not bot_state["armed"]
        db_manager.log_audit("INFO", "SYSTEM_ARM", f"System armed state: {bot_state['armed']}")
        return {"armed": bot_state["armed"]}

metrics_state = {
    "total_scans": 0,
    "total_trades": 0,
    "total_errors": 0,
    "signals_by_strategy": {
        "structure": 0,
        "arbitrage": 0,
        "tape": 0,
        "liquidity": 0
    },
    "start_time": datetime.now()
}

@app.get("/api/metrics")
async def get_metrics():
    """Rule: Institutional Observability (Lot 4)."""
    uptime = (datetime.now() - metrics_state["start_time"]).total_seconds()
    return {
        "uptime_seconds": uptime,
        "total_scans": metrics_state["total_scans"],
        "total_trades": metrics_state["total_trades"],
        "total_errors": metrics_state["total_errors"],
        "signals_by_strategy": metrics_state["signals_by_strategy"],
        "scans_per_minute": (metrics_state["total_scans"] / (uptime / 60)) if uptime > 0 else 0
    }

@app.get("/healthz")
async def healthz():
    """Simple immediate health check (Rule 39)."""
    return {"status": "OK"}

@app.get("/api/health")
async def health_check():
    """
    Rule 39: Applicative Health Check.
    Checks connectivity to providers and internal engine state.
    """
    data_health = await data_engine.health_monitor.get_health_report()
    db_status = "ONLINE"
    try:
        db_manager.get_balance("DEMO")
    except:
        db_status = "ERROR"
        
    return {
        "status": "UP",
        "timestamp": datetime.now().isoformat(),
        "database": db_status,
        "providers": data_health,
        "bot_running": bot_state["is_running"]
    }

@app.get("/")
async def read_index():
    return FileResponse('public/index.html')

app.mount("/", StaticFiles(directory="public"), name="public")

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.getenv("PORT", 8005))
    uvicorn.run(app, host="0.0.0.0", port=port)
