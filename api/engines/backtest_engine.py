import pandas as pd
from typing import Dict, Any, List, Optional
from datetime import datetime
from .analysis_engine import AnalysisEngine
from .signal_engine import SignalEngine
from .risk_engine import RiskEngine

class BacktestEngine:
    """
    Simple historical backtesting engine (Lot 12).
    Validates strategies against past OHLCV data.
    """
    def __init__(self, analysis_engine: AnalysisEngine, signal_engine: SignalEngine, risk_engine: RiskEngine):
        self.analysis = analysis_engine
        self.signal = signal_engine
        self.risk = risk_engine

    async def run_backtest(self, symbol: str, df: pd.DataFrame, initial_balance: float = 10000.0, strategy_mode: str = "structure") -> Dict[str, Any]:
        """
        Runs a backtest on the provided DataFrame.
        """
        if df.empty or len(df) < 50:
            return {"error": "Insufficient data for backtest"}

        balance = initial_balance
        equity = initial_balance
        trades = []
        active_position = None
        
        # Ensure 'Timestamp' column exists for AnalysisEngine
        if 'Timestamp' not in df.columns:
             df = df.copy()
             df['Timestamp'] = df.index.astype(int) // 10**6 # Convert to ms
             
        # We start from bar 50 to have enough history for indicators
        for i in range(50, len(df)):
            current_df = df.iloc[:i+1]
            current_bar = df.iloc[i]
            
            # 1. Update Active Position
            if active_position:
                current_price = current_bar['Close']
                # SL / TP Check
                hit_sl = (active_position["direction"] == "BUY" and current_price <= active_position["sl"]) or \
                         (active_position["direction"] == "SELL" and current_price >= active_position["sl"])
                hit_tp = (active_position["direction"] == "BUY" and current_price >= active_position["tp"]) or \
                         (active_position["direction"] == "SELL" and current_price <= active_position["tp"])
                
                if hit_sl or hit_tp:
                    exit_price = active_position["sl"] if hit_sl else active_position["tp"]
                    if active_position["direction"] == "BUY":
                        pnl = (exit_price - active_position["entry"]) * active_position["qty"]
                    else:
                        pnl = (active_position["entry"] - exit_price) * active_position["qty"]
                    # Round-trip fees (0.1% of notional, realistic taker model)
                    fees = (active_position["entry"] * active_position["qty"] +
                            exit_price * active_position["qty"]) * 0.0005
                    pnl -= fees

                    balance += pnl
                    active_position["status"] = "CLOSED"
                    active_position["exit"] = exit_price
                    active_position["pnl"] = round(pnl, 6)
                    active_position["fees"] = round(fees, 6)
                    active_position["close_time"] = str(current_bar.name)
                    trades.append(active_position)
                    active_position = None

            # 2. Look for new signals if no active position
            if not active_position:
                analysis = self.analysis.identify_structure(current_df)
                analysis["market_id"] = symbol
                # Backtest assumes news is always OK for simplicity
                news_status = {"trading_allowed": True, "news_ok": True, "day_ok": True, "session_ok": True}
                
                sig = self.signal.generate_signal(analysis, news_status, current_df, strategy_mode=strategy_mode)
                
                if sig.get("status") == "SIGNAL_DETECTED":
                    risk_data = self.risk.calculate_position_size(
                        balance=balance, entry=sig["entry"], stop_loss=sig["sl"], direction=sig["direction"]
                    )
                    
                    if risk_data.get("allowed"):
                        active_position = {
                            "symbol": symbol,
                            "direction": sig["direction"],
                            "entry": sig["entry"],
                            "sl": sig["sl"],
                            "tp": sig["tp"],
                            "qty": risk_data["quantity"],
                            "open_time": str(current_bar.name),
                            "status": "OPEN",
                            "strategy": strategy_mode
                        }

        # Final Report
        win_rate = 0
        total_pnl = balance - initial_balance
        if trades:
            wins = [t for t in trades if t["pnl"] > 0]
            win_rate = (len(wins) / len(trades)) * 100

        return {
            "symbol": symbol,
            "initial_balance": initial_balance,
            "final_balance": round(balance, 2),
            "total_pnl": round(total_pnl, 2),
            "win_rate": round(win_rate, 2),
            "total_trades": len(trades),
            "trades": trades[-50:] # Last 50 trades
        }
