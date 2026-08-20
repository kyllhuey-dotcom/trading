import numpy as np
import pandas as pd
from typing import Dict, List, Any, Optional

class AnalysisEngine:
    def __init__(self, window: int = 5):
        self.window = window # Fenêtre pour détecter les pivots (fractals)

    def _detect_pivots(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df['is_high'] = False
        df['is_low'] = False

        for i in range(self.window, len(df) - self.window):
            # Sommet local (Pivot High)
            if df['High'].iloc[i] == df['High'].iloc[i-self.window : i+self.window+1].max():
                df.at[df.index[i], 'is_high'] = True
            # Creux local (Pivot Low)
            if df['Low'].iloc[i] == df['Low'].iloc[i-self.window : i+self.window+1].min():
                df.at[df.index[i], 'is_low'] = True
        return df

    def identify_structure(self, df: pd.DataFrame, htf_bias: str = "NEUTRAL") -> Dict[str, Any]:
        """
        Analyse la structure du marché professionnelle (Rule 9).
        Detect HH, HL, LH, LL, BOS, CHoCH, Range.
        """
        if len(df) < self.window * 4:
            return {"status": "INSUFFICIENT_DATA", "market_state": "UNDEFINED"}

        df_pivots = self._detect_pivots(df)
        
        # Get list of pivots
        highs = df_pivots[df_pivots['is_high']][['High', 'Timestamp']].rename(columns={'High': 'price'})
        lows = df_pivots[df_pivots['is_low']][['Low', 'Timestamp']].rename(columns={'Low': 'price'})

        if len(highs) < 2 or len(lows) < 2:
            return {"status": "WEAK_STRUCTURE", "market_state": "TRANSITION", "trend": "NEUTRAL"}

        # 1. Structure Points Classification
        last_h = highs['price'].iloc[-1]
        prev_h = highs['price'].iloc[-2]
        last_l = lows['price'].iloc[-1]
        prev_l = lows['price'].iloc[-2]

        is_hh = last_h > prev_h
        is_hl = last_l > prev_l
        is_lh = last_h < prev_h
        is_ll = last_l < prev_l

        # 2. Trend Determination (Rule 11)
        current_trend = "NEUTRAL"
        if is_hh and is_hl:
            current_trend = "BULLISH"
        elif is_lh and is_ll:
            current_trend = "BEARISH"

        # 3. BOS & CHoCH Detection (Rule 9)
        current_price = df['Close'].iloc[-1]
        bos = False
        choch = False
        
        # BOS: Continuation of trend
        if current_trend == "BULLISH" and current_price > last_h:
            bos = True
        elif current_trend == "BEARISH" and current_price < last_l:
            bos = True
            
        # CHoCH: Change of Character (Trend reversal signal)
        if current_trend == "BULLISH" and current_price < last_l:
            choch = True
        elif current_trend == "BEARISH" and current_price > last_h:
            choch = True

        # 4. Range Engine (Rule 12)
        # Combine Pivot Structure + Volatility Compression
        price_std = df['Close'].tail(20).std()
        atr = (df['High'] - df['Low']).tail(20).mean()
        
        # Compression filter
        is_compressed = price_std < (atr * 1.5)
        
        # Structural range: price bouncing between same highs/lows
        is_structural_range = (abs(last_h - prev_h) / last_h < 0.001) and (abs(last_l - prev_l) / last_l < 0.001)

        market_state = "TRENDING"
        if current_trend == "NEUTRAL" or is_structural_range or is_compressed:
            market_state = "RANGE"
        elif choch:
            market_state = "TRANSITION"

        # 5. Momentum
        momentum = float(df['Close'].pct_change(self.window).iloc[-1] * 100)

        return {
            "status": "VALID",
            "market_state": market_state,
            "trend": current_trend,
            "htf_bias": htf_bias,
            "is_hh": bool(is_hh),
            "is_hl": bool(is_hl),
            "is_lh": bool(is_lh),
            "is_ll": bool(is_ll),
            "bos": bos,
            "choch": choch,
            "momentum": momentum,
            "last_high": float(last_h),
            "last_low": float(last_l),
            "volatility": "High" if price_std > atr else ("Medium" if price_std > atr * 0.5 else "Low")
        }
