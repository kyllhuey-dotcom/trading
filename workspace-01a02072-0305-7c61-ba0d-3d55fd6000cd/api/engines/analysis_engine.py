import numpy as np
import pandas as pd
from typing import Dict, List, Any, Optional

class AnalysisEngine:
    def __init__(self, window: int = 5):
        self.window = window # Fenêtre pour détecter les pics (fractals)

    def identify_structure(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Analyse la structure du marché à partir d'un DataFrame OHLCV.
        """
        if len(df) < self.window * 2 + 1:
            return {"status": "INSUFFICIENT_DATA"}

        df = df.copy()
        df['is_high'] = False
        df['is_low'] = False

        # Identification des Fractals (sommets et creux locaux)
        for i in range(self.window, len(df) - self.window):
            # Sommet local
            if df['High'].iloc[i] == df['High'].iloc[i-self.window : i+self.window+1].max():
                df.at[df.index[i], 'is_high'] = True
            # Creux local
            if df['Low'].iloc[i] == df['Low'].iloc[i-self.window : i+self.window+1].min():
                df.at[df.index[i], 'is_low'] = True

        highs = df[df['is_high']]
        lows = df[df['is_low']]

        if len(highs) < 2 or len(lows) < 2:
            return {"status": "WEAK_STRUCTURE", "trend": "NEUTRAL"}

        # Analyse des derniers points
        last_h = highs['High'].iloc[-1]
        prev_h = highs['High'].iloc[-2]
        last_l = lows['Low'].iloc[-1]
        prev_l = lows['Low'].iloc[-2]

        structure_points = []
        
        # Classification
        current_trend = "NEUTRAL"
        is_range = False
        
        # Logique de Tendance
        is_hh = last_h > prev_h
        is_hl = last_l > prev_l
        is_lh = last_h < prev_h
        is_ll = last_l < prev_l

        if is_hh and is_hl:
            current_trend = "BULLISH"
        elif is_lh and is_ll:
            current_trend = "BEARISH"
        else:
            current_trend = "NEUTRAL"

        # DÉTECTION DE RANGE (Règle 7)
        # 1. Vérification de la directionalité (ADX simplifié ou déviation)
        price_std = df['Close'].tail(20).std()
        price_mean = df['Close'].tail(20).mean()
        cv = (price_std / price_mean) * 100 # Coefficient de variation

        # 2. Absence de tendance claire ou volatilité trop faible
        if current_trend == "NEUTRAL" or cv < 0.1: # Seuil arbitraire de compression
            is_range = True

        # Détection BOS (Break of Structure)
        current_price = df['Close'].iloc[-1]
        bos_detected = False
        if current_trend == "BULLISH" and current_price > last_h:
            bos_detected = True
        elif current_trend == "BEARISH" and current_price < last_l:
            bos_detected = True

        return {
            "status": "VALID",
            "trend": current_trend,
            "is_range": is_range,
            "last_high": float(last_h),
            "prev_high": float(prev_h),
            "last_low": float(last_l),
            "prev_low": float(prev_l),
            "is_hh": bool(is_hh),
            "is_hl": bool(is_hl),
            "is_lh": bool(is_lh),
            "is_ll": bool(is_ll),
            "bos": bos_detected,
            "momentum": float(df['Close'].pct_change(self.window).iloc[-1] * 100),
            "volatility_index": float(cv)
        }
