from typing import Dict, Any, Optional, List
import pandas as pd
from datetime import datetime
from .strategies.micro_arbitrage import MicroArbitrageStrategy
from .strategies.tape_reading import TapeReadingStrategy
from .strategies.liquidity_gap import LiquidityGapStrategy


class SignalEngine:
    """
    Signal generation (structure + custom strategies).
    Every returned SIGNAL_DETECTED carries `market_id` and `entry`/`sl`/`tp`
    so the execution layer can always route the order.

    PROFIT HARDENING (LOT P):
    - `min_score` now gates EVERY strategy (before: custom strategies could
      emit signals below the configured threshold — quality leak);
    - `risk_reward` is applied to SL/TP (was hardcoded to 2.0 despite the
      `risk_reward_ratio` setting);
    - cost-vs-volatility filter: signals whose round-trip costs (fees +
      slippage) exceed `max_cost_ratio` × the risk distance are blocked —
      they are mathematically losing trades;
    - `alpha_override_enabled` makes the score-80 bypass of range/news
      filters opt-in (default off: never trade through high-impact news).
    """

    def __init__(self, min_score: int = 80,
                 risk_reward: float = 2.0,
                 atr_stop_multiplier: float = 1.5,
                 alpha_override_enabled: bool = False,
                 fee_pct: float = 0.05,
                 slippage_pct: float = 0.05,
                 max_cost_ratio: float = 0.5,
                 cost_filter_strategies: tuple = ("structure", "tape")):
        self.min_score = min_score
        self.risk_reward = float(risk_reward) if risk_reward and risk_reward > 0 else 2.0
        self.atr_stop_multiplier = float(atr_stop_multiplier) if atr_stop_multiplier > 0 else 1.5
        self.alpha_override_enabled = alpha_override_enabled
        self.fee_pct = float(fee_pct)
        self.slippage_pct = float(slippage_pct)
        self.max_cost_ratio = float(max_cost_ratio)
        self.cost_filter_strategies = set(cost_filter_strategies)
        self.strategies = {
            "arbitrage": MicroArbitrageStrategy(),
            "tape": TapeReadingStrategy(),
            "liquidity": LiquidityGapStrategy()
        }
        self.active_strategy_names = ["structure"]

    def set_active_strategies(self, strategy_list: List[str]) -> None:
        """Apply the configured strategy list (from bot settings)."""
        known = list(self.strategies.keys()) + ["structure"]
        valid = [s for s in strategy_list if s in known]
        self.active_strategy_names = valid or ["structure"]

    def set_min_score(self, min_score: int) -> None:
        self.min_score = min_score

    def set_risk_reward(self, risk_reward: float) -> None:
        """Wire the `risk_reward_ratio` setting into SL/TP computation (LOT P)."""
        try:
            value = float(risk_reward)
            if 0.3 <= value <= 10.0:  # sanity bounds
                self.risk_reward = value
        except (TypeError, ValueError):
            pass

    def set_atr_stop_multiplier(self, atr_stop_multiplier: float) -> None:
        """Wire the ATR stop multiplier (capital-profile aware) into SL distance."""
        try:
            value = float(atr_stop_multiplier)
            if 0.1 <= value <= 10.0:  # sanity bounds
                self.atr_stop_multiplier = value
        except (TypeError, ValueError):
            pass

    def set_alpha_override(self, enabled: bool) -> None:
        self.alpha_override_enabled = bool(enabled)

    def set_cost_params(self, fee_pct: Optional[float] = None,
                        slippage_pct: Optional[float] = None,
                        max_cost_ratio: Optional[float] = None) -> None:
        """Update the cost-vs-volatility filter parameters (LOT P)."""
        if fee_pct is not None:
            self.fee_pct = float(fee_pct)
        if slippage_pct is not None:
            self.slippage_pct = float(slippage_pct)
        if max_cost_ratio is not None:
            self.max_cost_ratio = float(max_cost_ratio)

    # ------------------------------------------------------------------ #
    # Quality gates (LOT P)                                               #
    # ------------------------------------------------------------------ #
    def _apply_quality_gates(self, res: Dict[str, Any], strategy: str) -> Dict[str, Any]:
        """Apply the global score gate + the cost-vs-volatility filter."""
        if res.get("status") != "SIGNAL_DETECTED":
            return res

        # 1. Global score gate — applies to ALL strategies (fix quality leak)
        score = int(res.get("score", 0) or 0)
        if score < self.min_score:
            res["status"] = "NO_TRADE"
            res["reason"] = f"Below minimum score ({score}/{self.min_score})"
            return res

        # 2. Cost filter: block trades whose costs would eat the edge
        if strategy in self.cost_filter_strategies:
            entry = res.get("entry")
            sl = res.get("sl")
            if entry and sl:
                risk_dist = abs(float(entry) - float(sl))
                round_trip_costs = (self.fee_pct + self.slippage_pct) * 2.0
                if risk_dist > 0:
                    cost_ratio = round_trip_costs / risk_dist
                    if cost_ratio > self.max_cost_ratio:
                        res["status"] = "NO_TRADE"
                        res["reason"] = (f"Cost ratio too high ({cost_ratio:.2f}) — "
                                         f"fees would eat the edge (risk {risk_dist:.3f}%)")
                        res["cost_blocked"] = True
        return res

    def generate_signal(self,
                        analysis: Dict[str, Any],
                        news_status: Dict[str, Any],
                        df: pd.DataFrame,
                        strategy_mode: Optional[str] = None,
                        market_id: Optional[str] = None,
                        cross_quotes: Optional[List[Dict[str, Any]]] = None,
                        orderbook: Optional[Dict[str, Any]] = None,
                        trades: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Generate a deterministic 0-100 signal for one market."""
        if market_id is None:
            market_id = analysis.get("market_id")

        # If strategy_mode is not specified, use the list of active strategies
        if not strategy_mode:
            strategy_mode = "multi" if len(self.active_strategy_names) > 1 else self.active_strategy_names[0]

        if strategy_mode == "multi":
            results = []
            for mode in self.active_strategy_names:
                res = self.generate_signal(analysis, news_status, df, strategy_mode=mode,
                                           market_id=market_id,
                                           cross_quotes=cross_quotes, orderbook=orderbook, trades=trades)
                if res.get("status") == "SIGNAL_DETECTED":
                    results.append(res)

            if not results:
                return {"status": "NO_TRADE", "reason": "No strategy generated a signal", "score": 0,
                        "market_id": market_id}

            best_res = max(results, key=lambda x: x.get("score", 0))
            best_res["multi_strategy"] = True
            best_res["all_signals"] = [r["reason"] for r in results]
            return best_res

        # Custom strategies (arbitrage / tape / liquidity)
        if strategy_mode in self.strategies:
            res = self.strategies[strategy_mode].generate_signal(
                market_id=market_id or "unknown",
                df=df,
                cross_quotes=cross_quotes,
                orderbook=orderbook,
                trades=trades
            )
            res["strategy"] = strategy_mode
            res["market_id"] = market_id
            return self._apply_quality_gates(res, strategy_mode)

        # ------------------------------------------------------------------ #
        # Default: structure strategy
        # ------------------------------------------------------------------ #
        if analysis.get("status") != "VALID":
            return {"status": "NO_TRADE", "reason": "Invalid Market Analysis", "score": 0, "market_id": market_id}

        # --- 1. Scoring Logic ---
        score = 0

        is_bullish_struct = analysis.get("is_hh") and analysis.get("is_hl")
        is_bearish_struct = analysis.get("is_lh") and analysis.get("is_ll")

        if (analysis.get("trend") == "BULLISH" and is_bullish_struct) or \
           (analysis.get("trend") == "BEARISH" and is_bearish_struct):
            score += 30
        elif analysis.get("trend") != "NEUTRAL":
            score += 15

        # Trend alignment between LTF and HTF
        if analysis.get("trend") == analysis.get("htf_bias") and analysis.get("trend") != "NEUTRAL":
            score += 20
        elif analysis.get("htf_bias") == "NEUTRAL" and analysis.get("trend") != "NEUTRAL":
            score += 10

        # Momentum
        mom = abs(analysis.get("momentum", 0) or 0)
        if 0.1 < mom < 2.0:
            score += 15
        elif 2.0 <= mom < 5.0:
            score += 10

        # Volume confirmation
        avg_vol = df['Volume'].tail(20).mean() if 'Volume' in df.columns else 0
        curr_vol = df['Volume'].iloc[-1] if 'Volume' in df.columns else 0
        if avg_vol and avg_vol > 0:
            vol_ratio = curr_vol / avg_vol
            if vol_ratio > 1.2:
                score += 15
            elif vol_ratio > 0.8:
                score += 5

        # Trigger quality: BOS / CHoCH
        if analysis.get("bos"):
            score += 20
        elif analysis.get("choch"):
            score += 10

        # --- 2. DataFrame completeness validation before SL/TP computation ---
        required_cols = ['High', 'Low', 'Close']
        if df is None or df.empty or not all(col in df.columns for col in required_cols) or len(df) < 15:
            return {"status": "NO_TRADE", "reason": "Insufficient OHLCV data for ATR",
                    "score": score, "market_id": market_id}

        direction = "BUY" if analysis["trend"] == "BULLISH" else "SELL"
        if analysis["trend"] == "NEUTRAL":
            if analysis.get("choch"):
                curr_price = df['Close'].iloc[-1]
                direction = "BUY" if curr_price > analysis.get("last_high", 0) else "SELL"
            else:
                return {"status": "NO_TRADE", "reason": "No clear trend or trigger",
                        "score": score, "market_id": market_id}

        current_price = float(df['Close'].iloc[-1])

        # --- 3. ATR-based SL/TP (hardened) ---
        try:
            high_low = df['High'] - df['Low']
            high_close = (df['High'] - df['Close'].shift()).abs()
            low_close = (df['Low'] - df['Close'].shift()).abs()
            ranges = pd.concat([high_low, high_close, low_close], axis=1)
            true_range = ranges.max(axis=1)
            atr_series = true_range.dropna()
            if len(atr_series) < 14:
                return {"status": "NO_TRADE", "reason": "Insufficient valid data for ATR",
                        "score": score, "market_id": market_id}
            atr = float(atr_series.tail(14).mean())
        except Exception as e:
            return {"status": "NO_TRADE", "reason": f"ATR Calculation Error: {str(e)}",
                    "score": score, "market_id": market_id}

        if atr <= 0 or pd.isna(atr):
            return {"status": "NO_TRADE", "reason": "Invalid ATR value",
                    "score": score, "market_id": market_id}

        if direction == "BUY":
            stop_loss = min(analysis["last_low"], current_price - (atr * self.atr_stop_multiplier))
            risk_dist = current_price - stop_loss
            take_profit = current_price + (risk_dist * self.risk_reward)
        else:
            stop_loss = max(analysis["last_high"], current_price + (atr * self.atr_stop_multiplier))
            risk_dist = stop_loss - current_price
            take_profit = current_price - (risk_dist * self.risk_reward)

        if risk_dist <= 0:
            return {"status": "NO_TRADE", "reason": "Invalid Risk parameters (Zero range)",
                    "score": score, "market_id": market_id}

        # --- 4. Final filtering ---
        # High-conviction signals (score >= 80) may trade even in range/news
        # contexts ONLY when explicitly enabled (alpha_override_enabled) —
        # trading through high-impact news is -EV for scalping.
        alpha_override = self.alpha_override_enabled and score >= 80

        reasons = []
        if score < self.min_score:
            reasons.append(f"Low score ({score}/{self.min_score})")

        # LOT P: news/session restriction is a SAFETY filter — it always
        # applies, even with alpha_override (never trade through high-impact
        # news). Alpha override only bypasses the technical RANGE filter.
        if not news_status.get("trading_allowed"):
            reasons.append("News/Session restricted")

        if not alpha_override:
            if analysis.get("market_state") == "RANGE":
                reasons.append("Market in Range")

        is_detected = len(reasons) == 0

        result = {
            "status": "SIGNAL_DETECTED" if is_detected else "NO_TRADE",
            "direction": direction,
            "entry": current_price,
            "sl": float(stop_loss),
            "tp": float(take_profit),
            "score": int(score),
            "alpha_override": alpha_override,
            "setup_type": "ALPHA_OVERRIDE" if alpha_override else ("BOS_REENTRANCE" if analysis.get("bos") else "STRUCTURE_FOLLOW"),
            "confidence": "CRITICAL" if score >= 90 else ("HIGH" if score >= 80 else "MEDIUM"),
            "reason": ", ".join(reasons) if (not is_detected and not alpha_override) else
                      (f"ALPHA OVERRIDE: {direction} (Score {score})" if alpha_override else
                       f"{direction} signal confirmed (Score {score})"),
            "timestamp": datetime.now().isoformat(),
            "atr": atr,
            "risk_reward": self.risk_reward,
            "strategy": "structure",
            "market_id": market_id
        }
        return self._apply_quality_gates(result, "structure")
