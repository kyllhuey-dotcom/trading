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
import os
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

# Global Bot State
bot_state = {
    "mode": "DEMO",
    "armed": False,
    "equity": 0.0,
    "drawdown": 0.0,
}

@app.get("/api/status")
async def get_status():
    # 1. Check News/Calendar (Rules 14, 15, 16)
    news_status = await news_engine.check_trading_allowed(asset_class="CRYPTO")
    
    # 2. Broker Connection Status (Rule 29)
    broker_info = broker_connector.get_status()
    
    # 3. Data Fetching & Integrity (Rules 3, 5, 23)
    df_ltf = await data_engine.fetch_ohlcv("BTC/USDT", timeframe='1m', limit=100)
    df_htf = await data_engine.fetch_ohlcv("BTC/USDT", timeframe='15m', limit=50)
    ticker = await data_engine.fetch_ticker("BTC/USDT")

    if not ticker or df_ltf.empty:
        state_machine.transition_to(BotState.DATA_ERROR)
        return {**bot_state, "status": state_machine.current_state, "status_display": "DATA ERROR", "news": news_status}

    # 4. Live Position Update (Rule 27)
    await demo_execution.update_active_positions(bot_state["mode"], {"BTC/USDT": ticker})
    active_trade = demo_execution.active_positions[0] if demo_execution.active_positions else None
    
    # 5. Market Analysis (Rule 9, 10, 11, 12)
    htf_analysis = analysis_engine.identify_structure(df_htf)
    analysis = analysis_engine.identify_structure(df_ltf, htf_bias=htf_analysis.get("trend"))
    analysis["df_preview"] = df_ltf.tail(30).to_dict('records')
    
    # 6. Signal Generation (Rule 17, 18, 19)
    signal = signal_engine.generate_signal(analysis, news_status, df_ltf)
    
    # 7. Risk Calculation (Rule 24, 25, 26)
    balance = portfolio_engine.get_balance(bot_state["mode"])
    bot_state["equity"] = balance + (active_trade["pnl"] if active_trade else 0)
    
    risk_data = {"allowed": False}
    if signal["status"] == "SIGNAL_DETECTED":
        risk_data = risk_engine.calculate_position_size(balance=balance, entry=signal["entry"], stop_loss=signal["sl"])
        
        # 8. THE ORCHESTRATOR - EXECUTION (Rule 33, 55, 56)
        # ARM Conditions (Rule 33)
        can_trade = (
            bot_state["armed"] and 
            not broker_connector.emergency_stop_active and
            risk_data["allowed"] and 
            not active_trade and
            news_status["trading_allowed"]
        )

        if can_trade:
            state_machine.transition_to(BotState.EXECUTING)
            exec_res = await execution_router.execute(bot_state["mode"], signal, risk_data, ticker)
            if not exec_res.get("success"):
                state_machine.transition_to(BotState.BROKER_ERROR)
    
    # 9. State Machine Transition Logic
    if broker_connector.emergency_stop_active:
        state_machine.transition_to(BotState.EMERGENCY_STOP)
    elif active_trade:
        state_machine.transition_to(BotState.POSITION_OPEN)
    elif not news_status["trading_allowed"]:
        state_machine.transition_to(BotState.NO_TRADE)
    elif analysis.get("market_state") == "RANGE":
        state_machine.transition_to(BotState.NO_TRADE)
    elif signal["status"] == "SIGNAL_DETECTED":
        state_machine.transition_to(BotState.SIGNAL_DETECTED)
    else:
        state_machine.transition_to(BotState.ANALYZING)
    
    return {
        **bot_state,
        "status": state_machine.current_state,
        "status_display": state_machine.current_state.value.replace('_', ' '),
        "balance": balance,
        "news": news_status,
        "analysis": analysis,
        "signal": signal,
        "risk": risk_data,
        "active_trade": active_trade,
        "history": portfolio_engine.history[-15:],
        "stats": portfolio_engine.get_stats(),
        "broker_info": broker_info
    }

@app.get("/api/scanner")
async def get_scanner():
    overview = await data_engine.get_market_overview()
    all_assets = []
    for cat in overview:
        for item in overview[cat]:
            item["ai_score"] = min(99, max(10, 50 + int(item.get("change", 0) * 10)))
            item["tradable"] = item["status"] == "LIVE"
            all_assets.append(item)
    return {"assets": all_assets}

@app.post("/api/emergency-stop")
async def emergency_stop():
    broker_connector.trigger_emergency_stop()
    bot_state["armed"] = False
    return {"status": "STOPPED"}

@app.post("/api/emergency-stop/reset")
async def reset_emergency():
    broker_connector.reset_emergency_stop()
    return {"status": "RESET_SUCCESSFUL"}

@app.post("/api/mode")
async def toggle_mode():
    if broker_connector.emergency_stop_active:
         return {"success": False, "message": "Emergency Stop is active. Reset required."}
    new_mode = "REAL" if bot_state["mode"] == "DEMO" else "DEMO"
    success, msg = await broker_connector.set_mode(new_mode)
    if success:
        bot_state["mode"] = new_mode
    return {"success": success, "mode": bot_state["mode"], "message": msg}

@app.post("/api/arm")
async def arm_bot():
    bot_state["armed"] = not bot_state["armed"]
    return {"armed": bot_state["armed"]}

@app.get("/")
async def read_index():
    return FileResponse('public/index.html')

app.mount("/", StaticFiles(directory="public"), name="public")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
