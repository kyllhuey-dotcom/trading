from typing import Dict, Any, Optional, List
import pandas as pd
from datetime import datetime
from .strategies.micro_arbitrage import MicroArbitrageStrategy
from .strategies.tape_reading import TapeReadingStrategy
from .strategies.liquidity_gap import LiquidityGapStrategy

class SignalEngine:
    def __init__(self, min_score: int = 80):
        self.min_score = min_score
        self.strategies = {
            "arbitrage": MicroArbitrageStrategy(),
            "tape": TapeReadingStrategy(),
            "liquidity": LiquidityGapStrategy()
        }
        self.active_strategy_names = ["structure"]

    def set_active_strategies(self, strategy_list: List[str]):
        self.active_strategy_names = strategy_list

    def generate_signal(self, analysis: Dict[str, Any], news_status: Dict[str, Any], df: pd.DataFrame, 
                        strategy_mode: Optional[str] = None, cross_quotes: Optional[List[Dict[str, Any]]] = None,
                        orderbook: Optional[Dict[str, Any]] = None, trades: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        Ultra-scalping deterministic signal generation (Rule 17, 18, 19).
        Calculates a score from 0-100 based on technical confluence.
        """
        # If strategy_mode is not specified, use the list of active strategies
        if not strategy_mode:
            strategy_mode = "multi" if len(self.active_strategy_names) > 1 else self.active_strategy_names[0]

        if strategy_mode == "multi":
            results = []
            for mode in self.active_strategy_names:
                res = self.generate_signal(analysis, news_status, df, strategy_mode=mode, 
                                          cross_quotes=cross_quotes, orderbook=orderbook, trades=trades)
                if res.get("status") == "SIGNAL_DETECTED":
                    results.append(res)
            
            if not results:
                return {"status": "NO_TRADE", "reason": "No strategy generated a signal", "score": 0}
            
            # Return the best signal
            best_res = max(results, key=lambda x: x.get("score", 0))
            best_res["multi_strategy"] = True
            best_res["all_signals"] = [r["reason"] for r in results]
            return best_res

        # If strategy_mode is a specific custom strategy
        if strategy_mode in self.strategies:
            market_id = analysis.get("market_id", "unknown")
            res = self.strategies[strategy_mode].generate_signal(
                market_id=market_id, 
                df=df, 
                cross_quotes=cross_quotes,
                orderbook=orderbook,
                trades=trades
            )
            res["strategy"] = strategy_mode
            return res

        # Default: structure strategy
        if analysis.get("status") != "VALID":
            return {"status": "NO_TRADE", "reason": "Invalid Market Analysis", "score": 0}

        # --- 1. Scoring Logic (Rule 17) ---
        score = 0
        
        # A. Structural Confluence (Max 30)
        # HH/HL for Bullish, LH/LL for Bearish
        is_bullish_struct = analysis.get("is_hh") and analysis.get("is_hl")
        is_bearish_struct = analysis.get("is_lh") and analysis.get("is_ll")
        
        if (analysis.get("trend") == "BULLISH" and is_bullish_struct) or \
           (analysis.get("trend") == "BEARISH" and is_bearish_struct):
            score += 30
        elif analysis.get("trend") != "NEUTRAL":
            score += 15

        # B. Trend Alignment (Max 20)
        # Alignment between LTF (analysis) and HTF bias
        if analysis.get("trend") == analysis.get("htf_bias") and analysis.get("trend") != "NEUTRAL":
            score += 20
        elif analysis.get("htf_bias") == "NEUTRAL" and analysis.get("trend") != "NEUTRAL":
            score += 10

        # C. Momentum & Volume (Max 30)
        mom = abs(analysis.get("momentum", 0))
        # Higher score for moderate momentum, penalize extreme spikes (potential exhaustion)
        if 0.1 < mom < 2.0:
            score += 15
        elif 2.0 <= mom < 5.0:
            score += 10
            
        # Volume confirmation
        avg_vol = df['Volume'].tail(20).mean() if 'Volume' in df.columns else 0
        curr_vol = df['Volume'].iloc[-1] if 'Volume' in df.columns else 0
        if avg_vol > 0:
            vol_ratio = curr_vol / avg_vol
            if vol_ratio > 1.2: score += 15
            elif vol_ratio > 0.8: score += 5

        # D. Trigger Quality (Max 20)
        # BOS (Break of Structure) is a strong entry trigger
        if analysis.get("bos"):
            score += 20
        # CHoCH (Change of Character) is a transition signal, less points for direct entry
        elif analysis.get("choch"):
            score += 10

        # --- 2. Trade Execution Logic ---
        # Rule: Validate DataFrame completeness before ATR/SL/TP calculation (Lot 1)
        required_cols = ['High', 'Low', 'Close']
        if df is None or df.empty or not all(col in df.columns for col in required_cols) or len(df) < 15:
            return {"status": "NO_TRADE", "reason": "Insufficient OHLCV data for ATR", "score": score}

        direction = "BUY" if analysis["trend"] == "BULLISH" else "SELL"
        if analysis["trend"] == "NEUTRAL":
            # If trend is neutral but we have a CHoCH, we might anticipate a reversal
            if analysis.get("choch"):
                 # Determine direction from price vs last pivots
                 curr_price = df['Close'].iloc[-1]
                 direction = "BUY" if curr_price > analysis.get("last_high", 0) else "SELL"
            else:
                 return {"status": "NO_TRADE", "reason": "No clear trend or trigger", "score": score}

        current_price = float(df['Close'].iloc[-1])
        
        # Professional ATR-based SL/TP (Rule 20) - Hardened (Lot 1)
        try:
            high_low = df['High'] - df['Low']
            high_close = (df['High'] - df['Close'].shift()).abs()
            low_close = (df['Low'] - df['Close'].shift()).abs()
            ranges = pd.concat([high_low, high_close, low_close], axis=1)
            true_range = ranges.max(axis=1)
            # Handle NaNs and ensure sufficient tail for mean
            atr_series = true_range.dropna()
            if len(atr_series) < 14:
                return {"status": "NO_TRADE", "reason": "Insufficient valid data for ATR", "score": score}
            atr = float(atr_series.tail(14).mean())
        except Exception as e:
            return {"status": "NO_TRADE", "reason": f"ATR Calculation Error: {str(e)}", "score": score}
        
        if atr <= 0 or pd.isna(atr):
            return {"status": "NO_TRADE", "reason": "Invalid ATR value", "score": score}

        if direction == "BUY":
            # SL below last low with ATR buffer
            stop_loss = min(analysis["last_low"], current_price - (atr * 1.5))
            risk_dist = current_price - stop_loss
            # Professional R/R: 1.5 minimum
            take_profit = current_price + (risk_dist * 2.0)
        else:
            # SL above last high with ATR buffer
            stop_loss = max(analysis["last_high"], current_price + (atr * 1.5))
            risk_dist = stop_loss - current_price
            take_profit = current_price - (risk_dist * 2.0)

        # Validation of Risk/Reward parameters
        if risk_dist <= 0 or atr <= 0:
            return {"status": "NO_TRADE", "reason": "Invalid Risk parameters (Zero range)", "score": score}

        # --- 3. Final Filtering (Rules 15, 18, 19, 55, 56) ---
        
        # Alpha Override Protocol (Lot 15)
        # If score >= 80, we bypass structural and news filters
        alpha_override = score >= 80
        
        # Basic failure reasons
        reasons = []
        if score < self.min_score: reasons.append(f"Low score ({score}/{self.min_score})")
        
        # These are bypassed if alpha_override is active
        if not alpha_override:
            if analysis.get("market_state") == "RANGE": reasons.append("Market in Range")
            if not news_status.get("trading_allowed"): reasons.append("News/Session restricted")
        
        is_detected = len(reasons) == 0 or alpha_override
        
        return {
            "status": "SIGNAL_DETECTED" if is_detected else "NO_TRADE",
            "direction": direction,
            "entry": current_price,
            "sl": float(stop_loss),
            "tp": float(take_profit),
            "score": int(score),
            "alpha_override": alpha_override,
            "setup_type": "ALPHA_OVERRIDE" if alpha_override else ("BOS_REENTRANCE" if analysis.get("bos") else "STRUCTURE_FOLLOW"),
            "confidence": "CRITICAL" if score >= 90 else ("HIGH" if score >= 80 else "MEDIUM"),
            "reason": ", ".join(reasons) if (not is_detected and not alpha_override) else (f"ALPHA OVERRIDE: {direction} (Score {score})" if alpha_override else f"{direction} signal confirmed (Score {score})"),
            "timestamp": datetime.now().isoformat(),
            "atr": atr,
            "risk_reward": 2.0
        }
