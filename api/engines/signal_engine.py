from typing import Dict, Any, Optional
from datetime import datetime
import pandas as pd

class SignalEngine:
    def __init__(self, min_score: int = 70):
        self.min_score = min_score

    def generate_signal(self, analysis: Dict[str, Any], news_status: Dict[str, Any], df: pd.DataFrame) -> Dict[str, Any]:
        """
        Génère un signal d'ultra-scalping basé sur l'analyse technique et les contraintes de sécurité.
        """
        # 1. Vérification des pré-requis de sécurité (Checklist obligatoire Rule 9)
        if not news_status["trading_allowed"]:
            return {"status": "NO_TRADE", "reason": "Security constraints (News/Day)"}
        
        if analysis.get("is_range", True):
            return {"status": "NO_TRADE", "reason": "Market in Range"}
        
        if analysis.get("trend") == "NEUTRAL":
            return {"status": "NO_TRADE", "reason": "No clear trend"}

        # 2. Identification de la direction
        direction = "BUY" if analysis["trend"] == "BULLISH" else "SELL"
        current_price = df['Close'].iloc[-1]
        
        # 3. Calcul du Score de Qualité (Rule 20)
        score = 0
        score += 20 if analysis.get("status") == "VALID" else 0
        score += 20 if analysis.get("trend") != "NEUTRAL" else 0
        score += 15 if abs(analysis.get("momentum", 0)) > 0.05 else 5
        score += 15 if analysis.get("bos") else 0 # Bonus pour cassure de structure
        
        # Simulation Liquidité (sera affiné avec le carnet d'ordre plus tard)
        score += 15 
        
        # Qualité entrée (basé sur le pullback ou accélération)
        score += 15

        if score < self.min_score:
            return {"status": "NO_TRADE", "reason": f"Low quality score: {score}/{self.min_score}"}

        # 4. Définition des niveaux (Ultra-Scalping)
        # On utilise l'ATR ou une fraction de la volatilité pour SL/TP
        volatility = df['High'].iloc[-20:].max() - df['Low'].iloc[-20:].min()
        atr_sim = volatility / 5
        
        if direction == "BUY":
            entry = current_price
            stop_loss = entry - atr_sim
            take_profit = entry + (atr_sim * 1.5) # R/R 1.5
        else:
            entry = current_price
            stop_loss = entry + atr_sim
            take_profit = entry - (atr_sim * 1.5)

        return {
            "status": "SIGNAL_DETECTED",
            "direction": direction,
            "symbol": "BTC/USDT",
            "entry": float(entry),
            "sl": float(stop_loss),
            "tp": float(take_profit),
            "score": score,
            "trend": analysis["trend"],
            "motif": f"Ultra-scalping {direction} détecté sur continuation de tendance avec momentum.",
            "timestamp": datetime.now().isoformat()
        }
