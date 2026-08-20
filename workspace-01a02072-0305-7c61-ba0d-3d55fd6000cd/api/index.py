from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from api.engines.data_engine import DataEngine
from api.engines.analysis_engine import AnalysisEngine
from api.engines.news_engine import NewsEngine
from api.engines.signal_engine import SignalEngine
from api.engines.risk_engine import RiskEngine
from api.engines.execution_engine import ExecutionEngine
from api.engines.broker_connector import BrokerConnector
import os

app = FastAPI(title="Trading Agent API")
data_engine = DataEngine()
analysis_engine = AnalysisEngine()
news_engine = NewsEngine()
signal_engine = SignalEngine()
risk_engine = RiskEngine(max_risk_pct=1.0)
execution_engine = ExecutionEngine()
broker_connector = BrokerConnector()

# Simulation de l'état du bot
bot_state = {
    "status": "ANALYZING",
    "mode": "DEMO",
    "armed": False,
    "balance": 20.00,
    "pnl_daily": 0.00,
    "broker_connected": False
}

@app.get("/api/status")
async def get_status():
    news_status = await news_engine.check_trading_allowed()
    df = await data_engine.fetch_crypto_ohlcv("BTC/USDT")
    current_price = float(df['Close'].iloc[-1])
    
    # Update position check
    bot_state["broker_connected"] = await broker_connector.connector.check_connection()

    # Update active positions (Simulation)
    closed = execution_engine.update_positions(current_price)
    for c in closed:
        bot_state["balance"] += c["pnl"]
        bot_state["pnl_daily"] += c["pnl"]

    analysis = analysis_engine.identify_structure(df)
    signal = signal_engine.generate_signal(analysis, news_status, df)
    
    risk_data = {"allowed": False, "reason": "No signal"}
    if signal["status"] == "SIGNAL_DETECTED":
        risk_data = risk_engine.calculate_position_size(
            balance=bot_state["balance"],
            entry=signal["entry"],
            stop_loss=signal["sl"]
        )
        
        # Execution logic (Demo or Real)
        if bot_state["armed"] and risk_data["allowed"] and not execution_engine.active_positions:
            if bot_state["mode"] == "DEMO":
                execution_engine.open_simulated_trade(signal, risk_data)
            else:
                # REAL TRADE (Lot 10 will refine this)
                await broker_connector.execute(signal, risk_data)
    
    # CYCLE DE DÉCISION STRICT (Rule 26)
    
    # 1. DATA VALID & MARKET OPEN
    if df.empty or (datetime.now().timestamp() * 1000 - df['Timestamp'].iloc[-1] > 300000): # Si data date de + de 5 min
        bot_state["status"] = "MARKET CLOSED / DATA ERROR"
        return {
            **bot_state, 
            "news": news_status, 
            "analysis": {"status": "NO_DATA"},
            "signal": {"status": "NO_TRADE", "reason": "Market seems closed or no fresh data"}
        }

    # 2. ECONOMIC CALENDAR CLEAR & 3. ALLOWED DAY
    if not news_status["day_ok"]:
        bot_state["status"] = "NO TRADE (DAY)"
    elif not news_status["news_ok"]:
        bot_state["status"] = "NO TRADE (NEWS)"
    
    # 4. LIQUIDITY VALID (Simulé via spread/volume si dispo)
    
    # 5. MARKET NOT RANGE
    elif analysis.get("is_range"):
        bot_state["status"] = "NO TRADE (RANGE)"
        
    # 6. TREND CLEAR & 7. STRUCTURE VALID
    elif analysis.get("trend") == "NEUTRAL":
        bot_state["status"] = "ANALYZING (NO TREND)"
        
    # 8. SIGNAL VALID
    elif signal["status"] != "SIGNAL_DETECTED":
        bot_state["status"] = "WAITING (NO SIGNAL)"
        
    # 9. RISK VALID
    elif not risk_data["allowed"]:
        bot_state["status"] = "RISK LOCK"
        
    # 10. EXECUTION VALID -> TRADE
    elif execution_engine.active_positions:
        bot_state["status"] = "POSITION OPEN"
    else:
        bot_state["status"] = "READY TO TRADE"
        # Auto-execution if ARMED
        if bot_state["armed"]:
            if bot_state["mode"] == "DEMO":
                execution_engine.open_simulated_trade(signal, risk_data)
                bot_state["status"] = "POSITION OPEN"
            elif bot_state["broker_connected"]:
                await broker_connector.execute(signal, risk_data)
                bot_state["status"] = "POSITION OPEN"
    
    return {
        **bot_state, 
        "news": news_status, 
        "analysis": analysis,
        "signal": signal,
        "risk": risk_data,
        "active_trade": execution_engine.active_positions[0] if execution_engine.active_positions else None,
        "history": execution_engine.history[-10:], # 10 derniers trades
        "stats": execution_engine.get_stats(),
        "emergency_active": broker_connector.emergency_stop_active
    }

@app.post("/api/emergency-stop")
async def emergency_stop():
    broker_connector.trigger_emergency_stop()
    bot_state["armed"] = False
    bot_state["status"] = "EMERGENCY STOP"
    return {"status": "STOPPED"}

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

@app.get("/api/analysis/{symbol:path}")
async def get_analysis(symbol: str):
    df = await data_engine.fetch_crypto_ohlcv(symbol)
    if df.empty:
        return {"error": "Data unavailable"}
    return analysis_engine.identify_structure(df)

@app.get("/api/markets")
async def get_markets():
    # Note: On a besoin de recréer get_market_overview ou similaire
    # Pour ce lot, on simplifie pour le dashboard
    tasks = [data_engine.fetch_crypto_price(s) for s in data_engine.symbols["CRYPTO"]]
    results = await __import__('asyncio').gather(*tasks)
    return {"CRYPTO": results}

@app.get("/")
async def read_index():
    return FileResponse('public/index.html')

app.mount("/", StaticFiles(directory="public"), name="public")

# Montage des fichiers statiques (pour plus tard)
# app.mount("/static", StaticFiles(directory="frontend/assets"), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
