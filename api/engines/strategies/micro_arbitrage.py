from .base_strategy import BaseStrategy
from typing import Dict, Any, Optional, List
import pandas as pd
from datetime import datetime

class MicroArbitrageStrategy(BaseStrategy):
    """
    Exploite les différences de prix entre plateformes (Gate / Bybit / Binance).
    Taux de réussite cible : 80-90 %.
    """
    def __init__(self, threshold_pct: float = 0.15):
        self.threshold_pct = threshold_pct

    def generate_signal(self, market_id: str, df: pd.DataFrame, orderbook: Optional[Dict[str, Any]] = None, trades: Optional[List[Dict[str, Any]]] = None, cross_quotes: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        if not cross_quotes or len(cross_quotes) < 2:
            return {"status": "NO_TRADE", "reason": "Insufficient cross-provider data", "score": 0}

        # Find min and max prices among providers
        valid_quotes = [q for q in cross_quotes if q.get("last") and q["last"] > 0]
        if len(valid_quotes) < 2:
            return {"status": "NO_TRADE", "reason": "Insufficient valid quotes", "score": 0}

        min_quote = min(valid_quotes, key=lambda x: x["last"])
        max_quote = max(valid_quotes, key=lambda x: x["last"])

        diff_pct = ((max_quote["last"] - min_quote["last"]) / min_quote["last"]) * 100

        if diff_pct >= self.threshold_pct:
            # We assume the first quote is the primary one
            primary_price = valid_quotes[0]["last"]
            
            prices = [q["last"] for q in valid_quotes]
            avg_price = sum(prices) / len(prices)
            
            if primary_price < avg_price:
                direction = "BUY"
            else:
                direction = "SELL"
            
            # Score proportional to spread
            score = min(100, int((diff_pct / self.threshold_pct) * 60 + 30))
                
            return {
                "status": "SIGNAL_DETECTED",
                "direction": direction,
                "score": score,
                "reason": f"Arbitrage Opportunity: {diff_pct:.2f}% spread detected",
                "entry": primary_price,
                "sl": primary_price * (0.998 if direction == "BUY" else 1.002), # Very tight for arb
                "tp": primary_price * (1.002 if direction == "BUY" else 0.998),
                "timestamp": datetime.now().isoformat(),
                "metadata": {
                    "spread_pct": diff_pct,
                    "providers": [q.get("provider", "unknown") for q in valid_quotes]
                }
            }

        return {"status": "NO_TRADE", "reason": f"Spread too low ({diff_pct:.3f}%)", "score": 0}
