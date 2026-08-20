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
from datetime import datetime

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
    "status": "ONLINE",
    "mode": "DEMO",
    "armed": False,
    "balance_demo": 1000.00,
    "balance_real": 0.00,
    "pnl_daily": 0.00,
    "broker_connected": False,
    "deposits_history": [],
    "equity": 1000.00,
    "drawdown": 0.0,
    "today_stats": {
        "pnl": 0.00,
        "trades": 0,
        "wins": 0,
        "losses": 0
    },
    "risk_status": "LOW RISK",
    "scanner_data": [
        {"asset": "BTC/USDT", "price": 59230.40, "change": 1.2, "trend": "Bullish", "structure": "HH", "volatility": "Medium", "spread": 0.1, "liquidity": "High", "ai_score": 82, "status": "Ready"},
        {"asset": "ETH/USDT", "price": 2640.15, "change": -0.5, "trend": "Bearish", "structure": "LL", "volatility": "Low", "spread": 0.05, "liquidity": "High", "ai_score": 45, "status": "No Signal"},
        {"asset": "SOL/USDT", "price": 145.60, "change": 4.8, "trend": "Bullish", "structure": "HH", "volatility": "High", "spread": 0.2, "liquidity": "Medium", "ai_score": 91, "status": "Best Setup"},
        {"asset": "GOLD", "price": 2510.40, "change": 0.1, "trend": "Neutral", "structure": "Range", "volatility": "Low", "spread": 0.3, "liquidity": "High", "ai_score": 60, "status": "No Trade"}
    ]
}

@app.get("/api/status")
async def get_status():
    news_status = await news_engine.check_trading_allowed()
    
    # Simuler des variations pour le dashboard premium
    if bot_state["status"] == "ONLINE":
        df = await data_engine.fetch_crypto_ohlcv("BTC/USDT")
        current_price = float(df['Close'].iloc[-1]) if not df.empty else 0
        
        # Mise à jour des positions
        closed = execution_engine.update_positions(current_price)
        for c in closed:
            if bot_state["mode"] == "DEMO":
                bot_state["balance_demo"] += c["pnl"]
            else:
                bot_state["balance_real"] += c["pnl"]
            
            bot_state["today_stats"]["pnl"] += c["pnl"]
            bot_state["today_stats"]["trades"] += 1
            if c["pnl"] > 0: bot_state["today_stats"]["wins"] += 1
            else: bot_state["today_stats"]["losses"] += 1

        analysis = analysis_engine.identify_structure(df)
        signal = signal_engine.generate_signal(analysis, news_status, df)
        
        active_bal = bot_state["balance_demo"] if bot_state["mode"] == "DEMO" else bot_state["balance_real"]
        bot_state["equity"] = active_bal + (execution_engine.active_positions[0]["pnl"] if execution_engine.active_positions else 0)
        
        risk_data = {"allowed": False, "reason": "No signal"}
        if signal["status"] == "SIGNAL_DETECTED":
            risk_data = risk_engine.calculate_position_size(balance=active_bal, entry=signal["entry"], stop_loss=signal["sl"])
            if bot_state["armed"] and risk_data["allowed"] and not execution_engine.active_positions:
                if bot_state["mode"] == "DEMO":
                    execution_engine.open_simulated_trade(signal, risk_data)
        
        status_display = "ANALYZING"
        if not news_status["trading_allowed"]: status_display = "TRADING PAUSED"
        elif analysis.get("is_range"): status_display = "NO TRADE (RANGE)"
        elif execution_engine.active_positions: status_display = "POSITION OPEN"
        elif signal["status"] == "SIGNAL_DETECTED": status_display = "SIGNAL READY"
        
        return {
            **bot_state,
            "status_display": status_display,
            "balance": active_bal,
            "news": news_status,
            "analysis": analysis,
            "signal": signal,
            "risk": risk_data,
            "active_trade": execution_engine.active_positions[0] if execution_engine.active_positions else None,
            "history": execution_engine.history[-15:],
            "stats": execution_engine.get_stats(),
        }
    
    return bot_state

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
