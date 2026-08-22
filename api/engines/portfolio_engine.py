from .db_manager import DatabaseManager
from typing import List, Dict, Any, Optional
from datetime import datetime


class PortfolioEngine:
    """Balances, history and performance statistics (per-mode, never mixed)."""

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    # ------------------------------------------------------------------ #
    # Balances                                                            #
    # ------------------------------------------------------------------ #
    def get_balance(self, mode: str) -> float:
        return self.db.get_balance(mode)

    def update_balance(self, mode: str, pnl: float):
        self.db.update_balance(mode, pnl)

    def set_balance(self, mode: str, amount: float):
        self.db.set_balance(mode, amount)

    # ------------------------------------------------------------------ #
    # History                                                             #
    # ------------------------------------------------------------------ #
    @property
    def history(self):
        return self.db.get_history()

    def get_history(self, mode: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        return self.db.get_history(mode=mode, limit=limit)

    def reset_history(self, mode: str = "DEMO"):
        self.db.delete_all_trades(mode)

    def get_daily_pnl(self, mode: str) -> float:
        today = datetime.now().strftime("%Y-%m-%d")
        trades = self.db.get_all_trades(mode=mode, limit=2000)
        daily = [t for t in trades
                 if (t.get("close_time") or "").startswith(today) and t.get("status") == "CLOSED"]
        return sum(t.get("pnl", 0.0) for t in daily)

    # ------------------------------------------------------------------ #
    # Statistics                                                          #
    # ------------------------------------------------------------------ #
    def get_stats(self, mode: str = "DEMO") -> Dict[str, Any]:
        """Real statistics on CLOSED trades for ONE mode only."""
        history = [t for t in self.db.get_all_trades(mode=mode, limit=2000)
                   if t.get("status") == "CLOSED"]
        if not history:
            return {"total_trades": 0, "win_rate": 0, "profit_factor": 0,
                    "total_pnl": 0, "avg_win": 0, "avg_loss": 0}

        wins = [t["pnl"] for t in history if (t.get("pnl") or 0) > 0]
        losses = [t["pnl"] for t in history if (t.get("pnl") or 0) <= 0]

        total_trades = len(history)
        win_rate = (len(wins) / total_trades) * 100
        total_gain = sum(wins)
        total_loss = abs(sum(losses))
        profit_factor = total_gain / total_loss if total_loss > 0 else (total_gain if total_gain > 0 else 0)

        return {
            "total_trades": total_trades,
            "win_rate": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "total_pnl": round(sum(wins) + sum(losses), 2),
            "avg_win": round(total_gain / len(wins), 2) if wins else 0,
            "avg_loss": round(total_loss / len(losses), 2) if losses else 0
        }

    def get_stats_by_strategy(self, mode: str = "DEMO") -> Dict[str, Any]:
        history = [t for t in self.db.get_all_trades(mode=mode, limit=2000)
                   if t.get("status") == "CLOSED"]
        stats: Dict[str, Dict[str, Any]] = {}

        for trade in history:
            meta = trade.get("metadata") or {}
            strat = meta.get("strategy", "unknown")
            pnl = trade.get("pnl", 0.0) or 0.0
            if strat not in stats:
                stats[strat] = {"wins": 0, "losses": 0, "pnl": 0.0, "total": 0}
            stats[strat]["total"] += 1
            stats[strat]["pnl"] += pnl
            if pnl > 0:
                stats[strat]["wins"] += 1
            else:
                stats[strat]["losses"] += 1

        results = {}
        for strat, data in stats.items():
            wr = (data["wins"] / data["total"] * 100) if data["total"] > 0 else 0
            results[strat] = {
                "total_trades": data["total"],
                "win_rate": round(wr, 2),
                "net_pnl": round(data["pnl"], 2),
                "avg_pnl": round(data["pnl"] / data["total"], 2) if data["total"] > 0 else 0
            }
        return results

    def get_performance_report(self, mode: str = "DEMO") -> Dict[str, Any]:
        """Full performance report for the dashboard and notifications."""
        overall = self.get_stats(mode)
        by_strategy = self.get_stats_by_strategy(mode)

        expectancy = 0.0
        if overall["total_trades"] > 0:
            avg_win = overall.get("avg_win", 0)
            avg_loss = overall.get("avg_loss", 0)
            wr = overall["win_rate"] / 100
            expectancy = (wr * avg_win) - ((1 - wr) * abs(avg_loss))

        return {
            "mode": mode,
            "overall": overall,
            "expectancy": round(expectancy, 2),
            "by_strategy": by_strategy,
            "daily_pnl": self.get_daily_pnl(mode),
            "timestamp": datetime.now().isoformat()
        }
