from .base_strategy import BaseStrategy
from typing import Dict, Any, Optional, List
import pandas as pd
from datetime import datetime

class TapeReadingStrategy(BaseStrategy):
    """
    Analyse le flux d'exécution pour détecter les pressions institutionnelles.
    Taux de réussite cible : 75-85 %.
    """
    def __init__(self, pressure_threshold: float = 40.0):
        self.pressure_threshold = pressure_threshold

    def generate_signal(self, market_id: str, df: pd.DataFrame, orderbook: Optional[Dict[str, Any]] = None, trades: Optional[List[Dict[str, Any]]] = None, cross_quotes: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        
        # 1. Order Book Imbalance (Rule: Approximate from top 10 levels)
        imbalance = 0.0
        if orderbook and 'bids' in orderbook and 'asks' in orderbook:
            bid_vol = sum(b[1] for b in orderbook['bids'][:10]) 
            ask_vol = sum(a[1] for a in orderbook['asks'][:10])
            if bid_vol + ask_vol > 0:
                imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol) * 100

        # 2. Trade Delta (Buy vs Sell volume in last trades)
        delta = 0.0
        if trades:
            buy_vol = sum(t.get('amount', t.get('size', 0)) for t in trades if t.get('side') == 'buy')
            sell_vol = sum(t.get('amount', t.get('size', 0)) for t in trades if t.get('side') == 'sell')
            if buy_vol + sell_vol > 0:
                delta = (buy_vol - sell_vol) / (buy_vol + sell_vol) * 100

        # 3. Price Velocity & Volume Spikes
        velocity_score = 0.0
        if not df.empty and len(df) >= 5:
            price_change_pct = ((df['Close'].iloc[-1] - df['Close'].iloc[-5]) / df['Close'].iloc[-5]) * 100
            vol_avg = df['Volume'].tail(20).mean()
            vol_curr = df['Volume'].iloc[-1]
            
            if vol_curr > vol_avg * 1.3:
                velocity_score = 20.0 if price_change_pct > 0 else -20.0
            
        # Total pressure score
        pressure_score = (imbalance * 0.4) + (delta * 0.4) + velocity_score
        abs_score = abs(pressure_score)
        
        if abs_score >= self.pressure_threshold:
            direction = "BUY" if pressure_score > 0 else "SELL"
            current_price = df['Close'].iloc[-1]
            
            return {
                "status": "SIGNAL_DETECTED",
                "direction": direction,
                "score": int(min(100, abs_score + 30)), # Boost score if threshold met
                "reason": f"Institutional Pressure detected: {pressure_score:.1f}% bias",
                "entry": current_price,
                "sl": current_price * (0.992 if direction == "BUY" else 1.008),
                "tp": current_price * (1.016 if direction == "BUY" else 0.984),
                "timestamp": datetime.now().isoformat(),
                "metadata": {
                    "imbalance": imbalance,
                    "delta": delta,
                    "velocity_score": velocity_score
                }
            }

        return {"status": "NO_TRADE", "reason": f"Pressure too low ({abs_score:.1f}%)", "score": int(abs_score)}
