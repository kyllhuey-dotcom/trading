import json
import os
from datetime import datetime
from typing import List, Dict, Any, Optional

class PortfolioEngine:
    """
    Gère les soldes et la performance des comptes (Rule 27, 36, 38).
    """
    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        self.accounts_file = os.path.join(data_dir, "accounts.json")
        self.history_file = os.path.join(data_dir, "history.json")
        
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)
            
        self.accounts = self._load_json(self.accounts_file, {
            "DEMO": {"balance": 1000.0, "currency": "EUR"},
            "REAL": {"balance": 0.0, "currency": "EUR"}
        })
        self.history = self._load_json(self.history_file, [])

    def _load_json(self, path: str, default: Any):
        if os.path.exists(path):
            with open(path, 'r') as f:
                try: return json.load(f)
                except: return default
        return default

    def _save_json(self, path: str, data: Any):
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)

    def get_balance(self, mode: str) -> float:
        return self.accounts.get(mode, {}).get("balance", 0.0)

    def update_balance(self, mode: str, pnl: float):
        if mode in self.accounts:
            self.accounts[mode]["balance"] += pnl
            self._save_json(self.accounts_file, self.accounts)

    def add_to_history(self, trade: Dict[str, Any]):
        self.history.append(trade)
        self._save_json(self.history_file, self.history)

    def get_stats(self) -> Dict[str, Any]:
        """
        Calcule les statistiques réelles (Rule 38).
        """
        if not self.history:
            return {"total_trades": 0, "win_rate": 0, "profit_factor": 0, "total_pnl": 0}
            
        wins = [t["pnl"] for t in self.history if t["pnl"] > 0]
        losses = [t["pnl"] for t in self.history if t["pnl"] <= 0]
        
        total_trades = len(self.history)
        win_rate = (len(wins) / total_trades) * 100
        total_gain = sum(wins)
        total_loss = abs(sum(losses))
        profit_factor = total_gain / total_loss if total_loss > 0 else total_gain
        
        return {
            "total_trades": total_trades,
            "win_rate": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "total_pnl": round(sum(wins) + sum(losses), 2),
            "avg_win": round(total_gain / len(wins), 2) if wins else 0,
            "avg_loss": round(total_loss / len(losses), 2) if losses else 0
        }
