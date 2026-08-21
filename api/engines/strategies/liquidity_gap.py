from .base_strategy import BaseStrategy
from typing import Dict, Any, Optional, List
import pandas as pd
from datetime import datetime

class LiquidityGapStrategy(BaseStrategy):
    """
    Détecte les zones de faible liquidité et anticipe les mouvements rapides.
    Taux de réussite cible : 75-85 %.
    """
    def __init__(self, gap_threshold_pct: float = 0.3):
        self.gap_threshold_pct = gap_threshold_pct

    def generate_signal(self, market_id: str, df: pd.DataFrame, orderbook: Optional[Dict[str, Any]] = None, trades: Optional[List[Dict[str, Any]]] = None, cross_quotes: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        
        if not orderbook or 'bids' not in orderbook or 'asks' not in orderbook or not orderbook['bids'] or not orderbook['asks']:
            return {"status": "NO_TRADE", "reason": "No orderbook data", "score": 0}

        # 1. Spread Analysis
        best_bid = orderbook['bids'][0][0]
        best_ask = orderbook['asks'][0][0]
        spread_pct = ((best_ask - best_bid) / best_bid) * 100
        
        # 2. Gap detection in Bids/Asks (Search for holes in the book)
        # We look at the first 15 levels
        levels = min(15, len(orderbook['bids']), len(orderbook['asks']))
        
        max_bid_gap = 0.0
        for i in range(levels - 1):
            gap = abs(orderbook['bids'][i][0] - orderbook['bids'][i+1][0]) / orderbook['bids'][i][0] * 100
            if gap > max_bid_gap: max_bid_gap = gap

        max_ask_gap = 0.0
        for i in range(levels - 1):
            gap = abs(orderbook['asks'][i+1][0] - orderbook['asks'][i][0]) / orderbook['asks'][i][0] * 100
            if gap > max_ask_gap: max_ask_gap = gap

        # 3. Scoring
        score = 0
        direction = "BUY"
        
        # If there's a huge gap in asks, price can shoot up easily
        if max_ask_gap > self.gap_threshold_pct and max_ask_gap > max_bid_gap:
            direction = "BUY"
            score = min(100, int(max_ask_gap * 150))
            reason = f"Liquidity Gap in Asks: {max_ask_gap:.2f}%"
        elif max_bid_gap > self.gap_threshold_pct:
            direction = "SELL"
            score = min(100, int(max_bid_gap * 150))
            reason = f"Liquidity Gap in Bids: {max_bid_gap:.2f}%"
        else:
            return {"status": "NO_TRADE", "reason": "No significant liquidity gap", "score": 0}
            
        if score >= 60:
            current_price = (best_bid + best_ask) / 2
            return {
                "status": "SIGNAL_DETECTED",
                "direction": direction,
                "score": score,
                "reason": reason,
                "entry": current_price,
                "sl": current_price * (0.99 if direction == "BUY" else 1.01),
                "tp": current_price * (1.02 if direction == "BUY" else 0.98),
                "timestamp": datetime.now().isoformat(),
                "metadata": {
                    "max_bid_gap": max_bid_gap,
                    "max_ask_gap": max_ask_gap,
                    "spread_pct": spread_pct
                }
            }

        return {"status": "NO_TRADE", "reason": "Weak liquidity gap", "score": score}
