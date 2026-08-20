from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
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
from typing import Optional
import os
import asyncio
from datetime import datetime

app = FastAPI(title="Trading Agent API")
data_engine = DataEngine()
analysis_engine = AnalysisEngine()
news_engine = NewsEngine()
signal_engine = SignalEngine()
risk_engine = RiskEngine(max_risk_pct=1.0)
portfolio_engine = PortfolioEngine()
demo_execution = ExecutionEngine(portfolio=portfolio_engine)
broker_connector = BrokerConnector()
execution_router = ExecutionRouter(demo_adapter=demo_execution, broker_connector=broker_connector)
state_machine = StateMachine()
scanner_engine = ScannerEngine(data_engine, analysis_engine, signal_engine, news_engine)

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
    Rule 25: Continuous Auto Scan background task.
    """
    while True:
        if bot_state["is_running"]:
            try:
                results = await scanner_engine.scan_all()
                bot_state["latest_scan"] = results
                
                # Update Engine Stats (Rule 7)
                bot_state["engine_stats"]["markets"] = len(data_engine.catalog.get_all_symbols())
                bot_state["engine_stats"]["scanned"] = len([r for r in results if r.get("status") != "ERROR"])
                bot_state["engine_stats"]["analyzing"] = len([r for r in results if r.get("trend") != "NEUTRAL"])
                bot_state["engine_stats"]["signals"] = len([r for r in results if r.get("signal") == "SIGNAL_DETECTED"])
                bot_state["engine_stats"]["tradable"] = len([r for r in results if r.get("tradable")])
                
            except Exception as e:
                print(f"Auto-Scan Loop Error: {e}")
        
        await asyncio.sleep(30) # Wait between full cycles to respect rate limits

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(auto_scan_loop())

@app.get("/api/status")
async def get_status(symbol: str = "BTC/USDT"):
    # 1. Fetch metadata (Rule 10, 20)
    info = data_engine.catalog.get_info(symbol)
    if not info:
         # Fallback to default
         symbol = "BTC/USDT"
         info = data_engine.catalog.get_info(symbol)
    
    asset_class = info.get("asset_class", "CRYPTO")
    
    # 2. Check News/Calendar (Rules 15, 18)
    news_status = await news_engine.check_trading_allowed(asset_class=asset_class)
    
    # 3. Broker Connection Status (Rule 29)
    broker_info = broker_connector.get_status()
    
    # Bot Life Cycle Management (Lot 2)
    if broker_connector.emergency_stop_active:
        state_machine.transition_to(BotState.EMERGENCY_STOP)
    elif not bot_state["is_running"]:
        state_machine.transition_to(BotState.STOPPED)
    else:
        state_machine.transition_to(BotState.RUNNING)

    # 4. Fetch Real Data for selected symbol (Rule 1, 3, 40)
    df_ltf = await data_engine.fetch_ohlcv(symbol, timeframe='1m', limit=100)
    ticker = await data_engine.fetch_ticker(symbol)

    if not ticker or df_ltf.empty:
        if bot_state["is_running"]: state_machine.transition_to(BotState.ERROR)
        return {
            **bot_state, 
            "status": state_machine.current_state, 
            "status_display": "DATA ERROR", 
            "news": news_status, 
            "selected_symbol": symbol,
            "broker_info": broker_info
        }

    # 5. Market Analysis (Rule 9, 31, 34)
    # HTF analysis for bias
    df_htf = await data_engine.fetch_ohlcv(symbol, timeframe='15m', limit=50)
    htf_analysis = analysis_engine.identify_structure(df_htf)
    # LTF analysis for execution
    analysis = analysis_engine.identify_structure(df_ltf, htf_bias=htf_analysis.get("trend"))
    analysis["df_preview"] = df_ltf.tail(40).to_dict('records')
    
    # 6. Live Position Update (Rule 27, 30)
    await demo_execution.update_active_positions(bot_state["mode"], {symbol: ticker})
    active_trade = demo_execution.active_positions[0] if demo_execution.active_positions else None
    
    signal = {"status": "NO_TRADE", "reason": "System Stopped"}
    risk_data = {"allowed": False}
    balance = portfolio_engine.get_balance(bot_state["mode"])

    # 7. THE TRADING ORCHESTRATOR (Rule 55, 56)
    if bot_state["is_running"] and state_machine.current_state != BotState.EMERGENCY_STOP:
        signal = signal_engine.generate_signal(analysis, news_status, df_ltf)
        signal["symbol"] = symbol
        
        if signal["status"] == "SIGNAL_DETECTED":
            risk_data = risk_engine.calculate_position_size(balance=balance, entry=signal["entry"], stop_loss=signal["sl"])
            
            # Rule 56: Check all flags
            can_execute = (
                bot_state["armed"] and 
                risk_data["allowed"] and 
                not active_trade and
                news_status["trading_allowed"] and
                analysis.get("market_state") != "RANGE"
            )

            if can_execute:
                await execution_router.execute(bot_state["mode"], signal, risk_data, ticker)
        
        # Sub-states for Running
        if active_trade:
            state_machine.transition_to(BotState.POSITION_OPEN)
        elif signal["status"] == "SIGNAL_DETECTED":
            state_machine.transition_to(BotState.SIGNAL_DETECTED)
        else:
            state_machine.transition_to(BotState.RUNNING)

    bot_state["equity"] = balance + (active_trade["pnl"] if active_trade else 0)
    
    return {
        **bot_state,
        "status": state_machine.current_state,
        "status_display": state_machine.current_state.value,
        "balance": balance,
        "news": news_status,
        "analysis": analysis,
        "signal": signal,
        "risk": risk_data,
        "active_trade": active_trade,
        "history": portfolio_engine.history[-15:],
        "stats": portfolio_engine.get_stats(),
        "broker_info": broker_info,
        "selected_symbol": symbol,
        "asset_info": info,
        "best_setups": sorted([r for r in bot_state["latest_scan"] if r.get("score", 0) > 0], key=lambda x: x["score"], reverse=True)[:5]
    }

@app.post("/api/start")
async def start_bot():
    if broker_connector.emergency_stop_active:
        return {"success": False, "message": "Emergency Stop Active. Reset required."}
    
    # Pre-start checks (Rule 4)
    # Check data freshness for a reference asset (e.g., BTC/USDT)
    ticker = await data_engine.fetch_ticker("BTC/USDT")
    if not ticker or (datetime.now().timestamp() * 1000 - ticker['timestamp'] > 60000):
        state_machine.transition_to(BotState.ERROR)
        return {"success": False, "message": "Market Data Stale or Unavailable. Cannot Start."}
    
    bot_state["is_running"] = True
    state_machine.transition_to(BotState.RUNNING)
    return {"success": True, "status": state_machine.current_state, "message": "Bot Started successfully."}

@app.post("/api/stop")
async def stop_bot():
    bot_state["is_running"] = False
    state_machine.transition_to(BotState.STOPPED)
    return {"success": True, "status": state_machine.current_state, "message": "Bot Stopped successfully."}

@app.get("/api/markets/categories")
async def get_market_categories():
    return data_engine.catalog.get_categories()

@app.get("/api/scanner")
async def get_scanner():
    """
    Rule 35: Returns latest results from the background auto-scan loop.
    """
    return {"assets": bot_state["latest_scan"]}

@app.get("/api/markets")
async def get_markets():
    """
    Returns categorized market data. Uses latest scan for speed if available.
    """
    if bot_state["latest_scan"]:
        overview = {cat: [] for cat in data_engine.catalog.get_categories()}
        for item in bot_state["latest_scan"]:
            overview[item["asset_class"]].append(item)
        return overview
    return await data_engine.get_market_overview()

@app.get("/api/ohlcv/{symbol:path}")
async def get_ohlcv(symbol: str, timeframe: str = '1m', limit: int = 100):
    df = await data_engine.fetch_ohlcv(symbol, timeframe, limit)
    return df.to_dict('records')

@app.post("/api/emergency-stop")
async def emergency_stop():
    broker_connector.trigger_emergency_stop()
    bot_state["is_running"] = False
    bot_state["armed"] = False
    return {"status": "STOPPED"}

@app.post("/api/emergency-stop/reset")
async def reset_emergency():
    broker_connector.reset_emergency_stop()
    return {"status": "RESET_SUCCESSFUL"}

@app.post("/api/mode")
async def toggle_mode():
    new_mode = "REAL" if bot_state["mode"] == "DEMO" else "DEMO"
    success, msg = await broker_connector.set_mode(new_mode)
    if success:
        bot_state["mode"] = new_mode
    return {"success": success, "mode": bot_state["mode"], "message": msg}

@app.post("/api/arm")
async def arm_bot():
    bot_state["armed"] = not bot_state["armed"]
    return {"armed": bot_state["armed"]}

@app.get("/api/demo/account")
async def get_demo_account():
    return {
        "balance": portfolio_engine.get_balance("DEMO"),
        "equity": bot_state["equity"] if bot_state["mode"] == "DEMO" else portfolio_engine.get_balance("DEMO"),
        "currency": "EUR"
    }

@app.post("/api/demo/account")
async def set_demo_balance(amount: float):
    if amount < 10.0:
        return {"success": False, "message": "Minimum balance is 10.00 EUR"}
    portfolio_engine.set_balance("DEMO", amount)
    # Recalculate equity if in demo mode
    if bot_state["mode"] == "DEMO":
        active_trade = demo_execution.active_positions[0] if demo_execution.active_positions else None
        bot_state["equity"] = amount + (active_trade["pnl"] if active_trade else 0)
    return {"success": True, "balance": amount}

@app.post("/api/demo/reset")
async def reset_demo_account():
    demo_execution.clear_active_positions("DEMO")
    portfolio_engine.reset_history()
    # Reset balance to a default or keep current? Rule 7 says "remettre le capital choisi"
    # We'll keep the current chosen balance but clear trades.
    current_balance = portfolio_engine.get_balance("DEMO")
    bot_state["equity"] = current_balance
    return {"success": True, "message": "Demo account reset successfully."}

@app.get("/")
async def read_index():
    return FileResponse('public/index.html')

app.mount("/", StaticFiles(directory="public"), name="public")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
