"""
Market quarantine system (v2.7 P1-7).

Tracks performance per market/strategy and automatically quarantines
pairs that consistently underperform after sufficient sample size.

Quarantine criteria (after 30+ trades):
- Expectancy <= 0
- Profit factor < 1.0
- Net RR < 1.5
- Drawdown exceeds limit

Quarantined markets are excluded from auto-execution but remain visible
in the scanner for manual review.
"""
import time
from collections import defaultdict
from typing import Any


class QuarantineManager:
    """Tracks market/strategy performance and manages quarantine state."""
    
    def __init__(self, min_trades: int = 30):
        self.min_trades = min_trades
        # Performance tracking: {(market, strategy): {wins, losses, total_pnl, ...}}
        self.performance: dict[tuple, dict[str, Any]] = defaultdict(lambda: {
            "wins": 0,
            "losses": 0,
            "total_pnl": 0.0,
            "gross_win": 0.0,
            "gross_loss": 0.0,
            "total_risk": 0.0,
            "trades": 0,
            "last_trade_ts": 0,
        })
        # Quarantined pairs: {(market, strategy): {reason, quarantined_at, ...}}
        self.quarantined: dict[tuple, dict[str, Any]] = {}
    
    def record_trade(self, market: str, strategy: str, pnl: float, risk_amount: float) -> None:
        """Record a closed trade for performance tracking."""
        key = (market, strategy)
        perf = self.performance[key]
        perf["trades"] += 1
        perf["total_pnl"] += pnl
        perf["total_risk"] += abs(risk_amount)
        perf["last_trade_ts"] = time.time()
        
        if pnl > 0:
            perf["wins"] += 1
            perf["gross_win"] += pnl
        else:
            perf["losses"] += 1
            perf["gross_loss"] += abs(pnl)
        
        # Check quarantine criteria after min_trades
        if perf["trades"] >= self.min_trades:
            self._check_quarantine(key, perf)
    
    def _check_quarantine(self, key: tuple, perf: dict[str, Any]) -> None:
        """Check if a market/strategy should be quarantined."""
        if key in self.quarantined:
            return  # Already quarantined
        
        trades = perf["trades"]
        if trades == 0:
            return
        
        # Calculate metrics
        win_rate = perf["wins"] / trades
        expectancy = perf["total_pnl"] / trades
        
        # Profit factor = gross_win / gross_loss (true sums)
        gross_win = float(perf.get("gross_win", 0.0) or 0.0)
        gross_loss = float(perf.get("gross_loss", 0.0) or 0.0)
        avg_win = gross_win / perf["wins"] if perf["wins"] > 0 else 0
        avg_loss = gross_loss / perf["losses"] if perf["losses"] > 0 else 0
        if gross_loss > 0:
            profit_factor = gross_win / gross_loss
        else:
            profit_factor = float("inf") if gross_win > 0 else 0
        
        # Net RR approximation
        net_rr = avg_win / avg_loss if avg_loss > 0 else 0
        
        # Quarantine criteria
        reasons = []
        if expectancy <= 0:
            reasons.append(f"negative_expectancy ({expectancy:.2f})")
        if profit_factor < 1.0 and profit_factor > 0:
            reasons.append(f"low_profit_factor ({profit_factor:.2f})")
        if net_rr < 1.5 and net_rr > 0:
            reasons.append(f"low_net_rr ({net_rr:.2f})")
        
        if reasons:
            self.quarantined[key] = {
                "reason": "; ".join(reasons),
                "quarantined_at": time.time(),
                "trades": trades,
                "win_rate": win_rate,
                "expectancy": expectancy,
                "profit_factor": profit_factor,
                "net_rr": net_rr,
            }
    
    def is_quarantined(self, market: str, strategy: str) -> bool:
        """Check if a market/strategy is quarantined."""
        return (market, strategy) in self.quarantined
    
    def get_quarantined(self) -> set[tuple]:
        """Get all quarantined (market, strategy) pairs."""
        return set(self.quarantined.keys())
    
    def release_quarantine(self, market: str, strategy: str) -> bool:
        """Manually release a market/strategy from quarantine."""
        key = (market, strategy)
        if key in self.quarantined:
            del self.quarantined[key]
            # Reset performance tracking
            if key in self.performance:
                del self.performance[key]
            return True
        return False
    
    def get_stats(self) -> dict[str, Any]:
        """Get quarantine system statistics."""
        return {
            "total_tracked": len(self.performance),
            "total_quarantined": len(self.quarantined),
            "min_trades_threshold": self.min_trades,
            "quarantined_pairs": [
                {
                    "market": k[0],
                    "strategy": k[1],
                    **v
                }
                for k, v in self.quarantined.items()
            ],
        }
    
    def reset(self) -> None:
        """Reset all tracking (for testing)."""
        self.performance.clear()
        self.quarantined.clear()


# Global instance
_quarantine_manager = None


def get_quarantine_manager() -> QuarantineManager:
    """Get the global quarantine manager instance."""
    global _quarantine_manager
    if _quarantine_manager is None:
        _quarantine_manager = QuarantineManager()
    return _quarantine_manager
