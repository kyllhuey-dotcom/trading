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
from api.engines.notification_engine import NotificationEngine
from api.models import StatusResponse, NewsStatus, AnalysisResult, SignalResult, DiagnosisReport

from typing import Optional, List, Dict, Any
import os
import asyncio
import json
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler

# --- 1. Institutional Logging ---
os.makedirs("data", exist_ok=True)
log_formatter = logging.Formatter(
    '%(asctime)s [%(levelname)s] %(name)s: %(message)s')
file_handler = RotatingFileHandler(
    "data/trading_bot.log", maxBytes=5*1024*1024, backupCount=5)
file_handler.setFormatter(log_formatter)
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)

logging.basicConfig(level=logging.INFO, handlers=[
                    file_handler, console_handler])
logger = logging.getLogger("QuantumTradePro")

# --- 2. System Core Initialization ---
app = FastAPI(title="Quantum Trade Pro Institutional")
db_manager = DatabaseManager()
data_engine = DataEngine()
analysis_engine = AnalysisEngine()
news_engine = NewsEngine()
news_aggregator = NewsAggregator()
signal_engine = SignalEngine()
risk_engine = RiskEngine()
portfolio_engine = PortfolioEngine(db_manager=db_manager)
notification_engine = NotificationEngine()
demo_execution = ExecutionEngine(portfolio=portfolio_engine, db_manager=db_manager,
                                 risk_engine=risk_engine, universe=data_engine.universe, notification_engine=notification_engine)
broker_connector = BrokerConnector()
broker_connector.universe = data_engine.universe
execution_router = ExecutionRouter(
    demo_adapter=demo_execution, broker_connector=broker_connector)
state_machine = StateMachine()
scanner_engine = ScannerEngine(
    data_engine, analysis_engine, signal_engine, news_engine)
diagnostic_engine = DiagnosticEngine()

state_lock = asyncio.Lock()

# --- 3. Global Bot State ---
bot_state = {
    "mode": "DEMO",
    "armed": False,
    "equity": 0.0,
    "balance": 0.0,
    "drawdown": 0.0,
    "is_running": False,
    "latest_scan": [],
    "active_trades": [],
    "engine_stats": {"markets": 0, "scanned": 0, "signals": 0, "tradable": 0},
    "selected_market": "btc_usdt"
}

metrics_state = {
    "total_scans": 0, "total_trades": 0, "total_errors": 0,
    "signals_by_strategy": {"structure": 0, "arbitrage": 0, "tape": 0, "liquidity": 0},
    "start_time": datetime.now()
}

# --- 4. WebSocket Manager ---


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except:
                pass


manager = ConnectionManager()

# --- 5. MICRO-LOOPS (The Quantum Engine Core) ---


async def micro_loop_capital():
    """Sync balance and equity every 1s."""
    while True:
        try:
            async with state_lock:
                mode = bot_state["mode"]
                if mode == "REAL":
                    balances = await broker_connector.get_all_balances()
                    total = sum(b.get("total_usdt", 0.0)
                                for b in balances.values() if "total_usdt" in b)
                    portfolio_engine.set_balance("REAL", total)

                bot_state["balance"] = portfolio_engine.get_balance(mode)
                active = demo_execution.active_positions
                unrealized_pnl = sum(p.get("pnl", 0.0) for p in active)
                bot_state["equity"] = bot_state["balance"] + unrealized_pnl
                bot_state["active_trades"] = active

                # Global Risk Guard
                safety = risk_engine.check_global_safety(
                    bot_state["equity"], portfolio_engine.get_daily_pnl(mode))
                if not safety["safe"]:
                    logger.error(f"RISK GUARD: {safety['reason']}")
                    await emergency_stop_logic()
        except Exception as e:
            logger.error(f"Capital Loop Error: {e}")
        await asyncio.sleep(1)


async def micro_loop_scanner():
    """High-frequency market scanner every 5s."""
    while True:
        try:
            if bot_state["is_running"]:
                metrics_state["total_scans"] += 1
                results = await scanner_engine.scan_all()
                async with state_lock:
                    bot_state["latest_scan"] = results
                    bot_state["engine_stats"].update({
                        "markets": len(results),
                        "scanned": len([r for r in results if r.get("status") == "LIVE"]),
                        "signals": len([r for r in results if r.get("score", 0) >= 80]),
                        "tradable": len([r for r in results if r.get("tradable")])
                    })

                # BACKGROUND AUTO-TRADE
                if bot_state["armed"]:
                    for res in results:
                        if res.get("tradable") and len(bot_state["active_trades"]) < 10:
                            # Verify if already in position
                            if any(p["symbol"] == res["symbol"] for p in bot_state["active_trades"]):
                                continue

                            ticker = await data_engine.fetch_ticker(res["symbol"])
                            if not ticker:
                                continue

                            sig = res.get("signal_data")
                            risk_data = risk_engine.calculate_position_size(
                                balance=bot_state["balance"], entry=sig["entry"], stop_loss=sig["sl"],
                                direction=sig["direction"], symbol=res["symbol"], active_positions=bot_state["active_trades"]
                            )

                            if risk_data.get("allowed"):
                                exec_res = await execution_router.execute(bot_state["mode"], sig, risk_data, ticker)
                                if exec_res.get("success"):
                                    metrics_state["total_trades"] += 1
                                    strat = sig.get("strategy", "structure")
                                    metrics_state["signals_by_strategy"][strat] += 1
                                    asyncio.create_task(notification_engine.notify("ORDER_OPEN", {
                                                        "symbol": res["symbol"], "entry_price": sig["entry"], "quantity": risk_data["quantity"]}))

        except Exception as e:
            logger.error(f"Scanner Loop Error: {e}")
        await asyncio.sleep(5)


async def micro_loop_management():
    """Active trade management (SL/TP/Trailing) every 200ms."""
    while True:
        try:
            active = bot_state["active_trades"]
            if active:
                symbols = [t["symbol"] for t in active]
                quotes = await data_engine.layer.get_all_quotes(symbols, data_engine.universe)
                tickers = {q.symbol: q.model_dump() for q in quotes}
                await demo_execution.update_active_positions(bot_state["mode"], tickers)
        except Exception as e:
            logger.error(f"Management Loop Error: {e}")
        await asyncio.sleep(0.2)


async def micro_loop_broadcaster():
    """System pulse broadcaster every 200ms."""
    while True:
        try:
            payload = {
                "type": "ACCOUNT_STREAM",
                "timestamp": int(datetime.now().timestamp() * 1000),
                "balance": bot_state["balance"],
                "equity": bot_state["equity"],
                "active_trades": bot_state["active_trades"],
                "is_running": bot_state["is_running"],
                "armed": bot_state["armed"],
                "mode": bot_state["mode"],
                "status": state_machine.current_state.value,
                "stats": bot_state["engine_stats"]
            }
            await manager.broadcast(json.dumps(payload))

            # Sub-second market price for selected
            await data_engine.broadcast_market_update(bot_state["selected_market"])
        except Exception as e:
            logger.error(f"Broadcaster Loop Error: {e}")
        await asyncio.sleep(0.2)

# --- 6. API Endpoints ---


@app.on_event("startup")
async def startup_event():
    logger.info("MASTER ENGINE STARTING...")
    await broker_connector.initialize_from_db(db_manager)
    # Initialize scanner once
    bot_state["latest_scan"] = await scanner_engine.scan_all()

    # Start all micro-loops
    asyncio.create_task(micro_loop_capital())
    asyncio.create_task(micro_loop_scanner())
    asyncio.create_task(micro_loop_management())
    asyncio.create_task(micro_loop_broadcaster())
    logger.info("ALL MICRO-LOOPS SYNCHRONIZED.")


@app.get("/api/status")
async def get_status(market_id: str = "btc_usdt"):
    bot_state["selected_market"] = market_id
    info = data_engine.universe.get_info(market_id)
    ticker = await data_engine.fetch_ticker(market_id)

    # Simple status for UI
    return {
        "is_running": bot_state["is_running"],
        "mode": bot_state["mode"],
        "armed": bot_state["armed"],
        "balance": bot_state["balance"],
        "equity": bot_state["equity"],
        "daily_pnl": portfolio_engine.get_daily_pnl(bot_state["mode"]),
        "selected_market": market_id,
        "asset_info": info,
        "ticker": ticker,
        "stats": bot_state["engine_stats"]
    }


@app.get("/api/history")
async def get_history(mode: str = "DEMO"):
    return db_manager.get_history(mode=mode)


@app.get("/api/scanner")
async def get_scanner():
    return {"assets": bot_state["latest_scan"]}


@app.get("/api/markets")
async def get_markets():
    return await data_engine.get_market_overview()


@app.get("/api/settings")
async def get_settings():
    with db_manager._get_connection() as conn:
        rows = conn.execute("SELECT * FROM settings").fetchall()
        return {row["key"]: row["value"] for row in rows}


@app.post("/api/settings")
async def save_settings(new_settings: Dict[str, str]):
    with db_manager._get_connection() as conn:
        for k, v in new_settings.items():
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, v))
        conn.commit()
    return {"success": True}


@app.post("/api/start")
async def start_bot():
    bot_state["is_running"] = True
    return {"success": True}


@app.post("/api/stop")
async def stop_bot():
    bot_state["is_running"] = False
    return {"success": True}


@app.post("/api/arm")
async def arm_bot():
    bot_state["armed"] = not bot_state["armed"]
    return {"armed": bot_state["armed"]}


@app.post("/api/mode")
async def toggle_mode():
    bot_state["mode"] = "REAL" if bot_state["mode"] == "DEMO" else "DEMO"
    return {"mode": bot_state["mode"]}


@app.post("/api/emergency-stop")
async def emergency_stop_api():
    await emergency_stop_logic()
    return {"success": True}


async def emergency_stop_logic():
    bot_state["is_running"] = False
    bot_state["armed"] = False
    demo_execution.clear_active_positions(bot_state["mode"])
    await broker_connector.close_all_positions()
    logger.critical("EMERGENCY STOP EXECUTED.")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.get("/")
async def read_index(): return FileResponse('public/index.html')
app.mount("/", StaticFiles(directory="public"), name="public")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
