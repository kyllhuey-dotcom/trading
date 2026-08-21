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

    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        high_low = df['High'] - df['Low']
        high_close = (df['High'] - df['Close'].shift()).abs()
        low_close = (df['Low'] - df['Close'].shift()).abs()
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = ranges.max(axis=1)
        return float(true_range.tail(period).mean())

    def identify_structure(self, df: pd.DataFrame, htf_bias: str = "NEUTRAL") -> Dict[str, Any]:
        """
        Professional market structure analysis (Rule 9).
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
        last_h = float(highs['price'].iloc[-1])
        prev_h = float(highs['price'].iloc[-2])
        last_l = float(lows['price'].iloc[-1])
        prev_l = float(lows['price'].iloc[-2])

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
        current_price = float(df['Close'].iloc[-1])
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
        atr = self._calculate_atr(df)
        price_std = float(df['Close'].tail(20).std())
        
        # Compression filter: ATR shrinking and price within tight band
        is_compressed = price_std < (atr * 0.8)
        
        # Structural range: price bouncing between same highs/lows (within 0.1% for Forex, more for Crypto)
        # Using a relative threshold based on ATR
        is_structural_range = (abs(last_h - prev_h) < atr * 0.5) and (abs(last_l - prev_l) < atr * 0.5)

        market_state = "TRENDING"
        if is_structural_range or is_compressed or current_trend == "NEUTRAL":
            market_state = "RANGE"
        elif choch:
            market_state = "TRANSITION"

        # 5. Momentum
        momentum = float(df['Close'].pct_change(self.window).iloc[-1] * 100)

        # 6. Technical Indicators (Lot 13 - Pro Terminal)
        # RSI
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = float(100 - (100 / (1 + rs)).iloc[-1]) if not loss.iloc[-1] == 0 else 50.0

        # EMAs
        ema8 = float(df['Close'].ewm(span=8).mean().iloc[-1])
        ema21 = float(df['Close'].ewm(span=21).mean().iloc[-1])

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
            "last_high": last_h,
            "last_low": last_l,
            "atr": atr,
            "volatility": "HIGH" if price_std > atr * 1.5 else ("MEDIUM" if price_std > atr * 0.5 else "LOW"),
            "indicators": {
                "rsi": rsi,
                "ema8": ema8,
                "ema21": ema21,
                "ema_cross": "BULLISH" if ema8 > ema21 else "BEARISH"
            }
        }
