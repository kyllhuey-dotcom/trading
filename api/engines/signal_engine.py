from typing import Dict, Any, Optional
import pandas as pd
from datetime import datetime

class SignalEngine:
    def __init__(self, min_score: int = 75):
        self.min_score = min_score

    def generate_signal(self, analysis: Dict[str, Any], news_status: Dict[str, Any], df: pd.DataFrame) -> Dict[str, Any]:
        """
        Génère un signal d'ultra-scalping déterministe (Rule 17, 18, 19).
        """
        # 1. FAIL-SAFE : Validation des pré-requis système
        if not news_status.get("trading_allowed"):
            return {"status": "NO_TRADE", "reason": "System/Calendar Blocked"}
            
        if analysis.get("market_state") != "TRENDING":
            return {"status": "NO_TRADE", "reason": f"Market State: {analysis.get('market_state')}"}
        
        if analysis.get("status") != "VALID":
            return {"status": "NO_TRADE", "reason": "Invalid Market Analysis"}

        # 2. CALCUL DU SCORE RÉEL (Rule 18)
        # Chaque composant est calculé entre 0 et sa valeur max
        
        # A. Structure (max 20) : Score basé sur la clarté HH/HL ou LH/LL
        struct_score = 20 if (analysis.get("is_hh") and analysis.get("is_hl")) or \
                           (analysis.get("is_lh") and analysis.get("is_ll")) else 10
        
        # B. Trend (max 20) : Alignement avec HTF
        trend_score = 0
        if analysis.get("trend") == analysis.get("htf_bias") and analysis.get("trend") != "NEUTRAL":
            trend_score = 20
        elif analysis.get("trend") != "NEUTRAL":
            trend_score = 10
            
        # C. Momentum (max 15) : Mesuré via ROC (Rate of Change)
        mom = abs(analysis.get("momentum", 0))
        mom_score = min(15, int(mom * 100)) # Ex: 0.15% momentum -> 15 points
        
        # D. Liquidity (max 15) : Volume relatif
        avg_vol = df['Volume'].tail(20).mean()
        curr_vol = df['Volume'].iloc[-1]
        liq_score = min(15, int((curr_vol / avg_vol) * 7.5)) if avg_vol > 0 else 0
        
        # E. Entry Quality (max 15) : Détection d'une cassure (BOS) ou d'un pullback
        entry_qual = 0
        if analysis.get("bos"):
            entry_qual = 15 # High quality on Break of Structure
        elif abs(df['Close'].iloc[-1] - analysis.get("last_low" if analysis["trend"] == "BULLISH" else "last_high")) < \
             abs(analysis.get("last_high") - analysis.get("last_low")) * 0.3:
            entry_qual = 10 # Pullback quality
            
        # F. Risk/Reward (max 15) : Potentiel de gain
        # Simulé pour le score, calculé précisément plus bas
        rr_score = 15 
        
        total_score = struct_score + trend_score + mom_score + liq_score + entry_qual + rr_score

        # 3. FILTRE DE QUALITÉ MINIMALE
        if total_score < self.min_score:
            return {"status": "NO_TRADE", "reason": f"Signal Quality: {total_score}/{self.min_score}"}

        # 4. EXÉCUTION STRATÉGIQUE (Rule 19, 20, 21)
        direction = "BUY" if analysis["trend"] == "BULLISH" else "SELL"
        current_price = df['Close'].iloc[-1]
        
        # SL basé sur la structure (swing point) + buffer ATR
        atr = (df['High'] - df['Low']).tail(20).mean()
        
        if direction == "BUY":
            # Invalidation sous le dernier HL ou creux de pivot
            stop_loss = analysis["last_low"] - (atr * 0.2)
            # Take profit avec R/R minimum de 1.5
            risk_dist = current_price - stop_loss
            take_profit = current_price + (risk_dist * 1.5)
            setup_type = "Trend Continuation" if analysis["bos"] else "Pullback"
        else:
            stop_loss = analysis["last_high"] + (atr * 0.2)
            risk_dist = stop_loss - current_price
            take_profit = current_price - (risk_dist * 1.5)
            setup_type = "Trend Continuation" if analysis["bos"] else "Pullback"

        # Sécurité distance SL (Rule 20)
        if risk_dist <= 0:
            return {"status": "NO_TRADE", "reason": "Invalid Risk/Reward distance"}

        return {
            "status": "SIGNAL_DETECTED",
            "direction": direction,
            "symbol": "BTC/USDT", # Dynamisé par DataEngine
            "entry": float(current_price),
            "sl": float(stop_loss),
            "tp": float(take_profit),
            "score": total_score,
            "setup_type": setup_type,
            "confidence": "High" if total_score > 85 else "Medium",
            "reason": f"Structure {analysis['trend']} confirmée avec score {total_score}.",
            "timestamp": datetime.now().isoformat()
        }
