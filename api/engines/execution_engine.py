import json
import os
from datetime import datetime
from typing import List, Dict, Any

class ExecutionEngine:
    def __init__(self, storage_path: str = "data/trades.json"):
        self.storage_path = storage_path
        self.active_positions = []
        self.history = []
        self._load_data()

    def _load_data(self):
        if os.path.exists(self.storage_path) and os.path.getsize(self.storage_path) > 0:
            try:
                with open(self.storage_path, 'r') as f:
                    data = json.load(f)
                    self.active_positions = data.get("active", [])
                    self.history = data.get("history", [])
            except:
                pass

    def _save_data(self):
        with open(self.storage_path, 'w') as f:
            json.dump({
                "active": self.active_positions,
                "history": self.history
            }, f)

    def open_simulated_trade(self, signal: Dict[str, Any], risk: Dict[str, Any]) -> Dict[str, Any]:
        if self.active_positions:
            return {"success": False, "reason": "Position already open"}
        
        trade = {
            "id": datetime.now().strftime("%Y%m%d%H%M%S"),
            "symbol": signal["symbol"],
            "direction": signal["direction"],
            "entry": signal["entry"],
            "sl": signal["sl"],
            "tp": signal["tp"],
            "quantity": risk["quantity"],
            "leverage": risk["leverage"],
            "notional": risk["notional_value"],
            "open_time": datetime.now().isoformat(),
            "status": "OPEN",
            "pnl": 0.0
        }
        self.active_positions.append(trade)
        self._save_data()
        return {"success": True, "trade": trade}

    def get_stats(self) -> Dict[str, Any]:
        if not self.history:
            return {
                "total_trades": 0,
                "win_rate": 0,
                "profit_factor": 0,
                "total_pnl": 0,
                "avg_win": 0,
                "avg_loss": 0
            }
        
        wins = [t["pnl"] for t in self.history if t["pnl"] > 0]
        losses = [t["pnl"] for t in self.history if t["pnl"] <= 0]
        
        total_trades = len(self.history)
        win_rate = (len(wins) / total_trades) * 100 if total_trades > 0 else 0
        total_gain = sum(wins)
        total_loss = abs(sum(losses))
        profit_factor = total_gain / total_loss if total_loss > 0 else total_gain
        
        return {
            "total_trades": total_trades,
            "win_rate": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "total_pnl": round(total_gain - total_loss, 2),
            "avg_win": round(total_gain / len(wins), 2) if wins else 0,
            "avg_loss": round(total_loss / len(losses), 2) if losses else 0
        }
