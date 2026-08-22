from .base_strategy import BaseStrategy
from typing import Dict, Any, Optional, List
import pandas as pd
from datetime import datetime

class TapeReadingStrategy(BaseStrategy):
    """
    Analyse le flux d'exécution pour détecter les pressions institutionnelles.
    Taux de réussite cible : 75-85 %.

    LOT C hardening:
    - depth-weighted orderbook imbalance (nearby levels weigh more);
    - proportional (signed) price velocity instead of a binary ±20;
    - dynamic pressure threshold driven by ATR volatility: calmer markets get
      a more sensitive threshold, wild markets require stronger conviction
      (clamped between min/max, falls back to the base threshold when OHLCV
      is unavailable — full backward compatibility);
    - conviction multiplier: aligned components reinforce the signal,
      conflicting components dampen it.
    """
    def __init__(self, pressure_threshold: float = 40.0,
                 min_pressure_threshold: float = 15.0,
                 max_pressure_threshold: float = 60.0,
                 volatility_lookback: int = 14,
                 reference_atr_pct: float = 0.3,
                 dynamic_threshold: bool = True):
        self.pressure_threshold = pressure_threshold
        self.min_pressure_threshold = min_pressure_threshold
        self.max_pressure_threshold = max_pressure_threshold
        self.volatility_lookback = volatility_lookback
        self.reference_atr_pct = reference_atr_pct
        self.dynamic_threshold = dynamic_threshold

    # ------------------------------------------------------------------ #
    # Flow approximations                                                 #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _weighted_imbalance(orderbook: Optional[Dict[str, Any]]) -> float:
        """Depth-weighted orderbook imbalance over the top 10 levels.

        Nearby levels (which would be consumed first) weigh more than far ones.
        Falls back to the legacy uniform weighting for single-level books.
        """
        if not orderbook or 'bids' not in orderbook or 'asks' not in orderbook:
            return 0.0
        bids = orderbook.get('bids') or []
        asks = orderbook.get('asks') or []

        def weighted_sum(levels: List[Any]) -> float:
            total = 0.0
            for i, level in enumerate(levels[:10]):
                try:
                    volume = float(level[1])
                except (TypeError, IndexError, ValueError):
                    continue
                total += volume / (1.0 + 0.35 * i)
            return total

        bid_vol = weighted_sum(bids)
        ask_vol = weighted_sum(asks)
        if bid_vol + ask_vol <= 0:
            return 0.0
        return (bid_vol - ask_vol) / (bid_vol + ask_vol) * 100

    @staticmethod
    def _trade_delta(trades: Optional[List[Dict[str, Any]]]) -> float:
        """Signed buy-vs-sell volume delta (percentage, -100..100)."""
        if not trades:
            return 0.0
        buy_vol = sum(t.get('amount', t.get('size', 0)) or 0
                      for t in trades if t.get('side') == 'buy')
        sell_vol = sum(t.get('amount', t.get('size', 0)) or 0
                       for t in trades if t.get('side') == 'sell')
        if buy_vol + sell_vol <= 0:
            return 0.0
        return (buy_vol - sell_vol) / (buy_vol + sell_vol) * 100

    @staticmethod
    def _price_velocity(df: pd.DataFrame) -> float:
        """Proportional signed velocity: price change scaled + volume spike bonus."""
        if df is None or df.empty or len(df) < 5 or 'Close' not in df.columns:
            return 0.0
        closes = df['Close'].dropna()
        if len(closes) < 2:
            return 0.0
        last, ref = float(closes.iloc[-1]), float(closes.iloc[-5])
        if ref <= 0:
            return 0.0
        price_change_pct = ((last - ref) / ref) * 100
        velocity = max(-30.0, min(30.0, price_change_pct * 15.0))

        if 'Volume' in df.columns and len(df) >= 5:
            volumes = df['Volume'].dropna()
            if len(volumes) >= 2:
                avg = float(volumes.tail(20).mean())
                curr = float(volumes.iloc[-1])
                if avg > 0 and curr > avg * 1.3:
                    velocity += 10.0 if price_change_pct > 0 else -10.0
        return max(-40.0, min(40.0, velocity))

    # ------------------------------------------------------------------ #
    # Volatility-driven dynamic threshold                                 #
    # ------------------------------------------------------------------ #
    def _atr_pct(self, df: pd.DataFrame) -> Optional[float]:
        """ATR(14) as % of close. Returns None when OHLCV is unavailable."""
        if df is None or df.empty or len(df) < self.volatility_lookback + 1:
            return None
        for col in ('High', 'Low', 'Close'):
            if col not in df.columns:
                return None
        try:
            high_low = df['High'] - df['Low']
            high_close = (df['High'] - df['Close'].shift()).abs()
            low_close = (df['Low'] - df['Close'].shift()).abs()
            true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            atr = float(true_range.dropna().tail(self.volatility_lookback).mean())
        except Exception:
            return None
        close = float(df['Close'].iloc[-1])
        if close <= 0 or atr <= 0 or pd.isna(atr):
            return None
        return (atr / close) * 100

    def _dynamic_threshold(self, atr_pct: Optional[float]) -> float:
        if not self.dynamic_threshold or atr_pct is None or self.reference_atr_pct <= 0:
            return self.pressure_threshold
        ratio = atr_pct / self.reference_atr_pct
        scaled = self.pressure_threshold * max(0.5, min(2.0, ratio))
        return max(self.min_pressure_threshold,
                   min(self.max_pressure_threshold, scaled))

    @staticmethod
    def _sign(value: float) -> int:
        return 1 if value > 0 else (-1 if value < 0 else 0)

    def generate_signal(self, market_id: str, df: pd.DataFrame, orderbook: Optional[Dict[str, Any]] = None, trades: Optional[List[Dict[str, Any]]] = None, cross_quotes: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        # 1. Order Book Imbalance (depth-weighted over the top 10 levels)
        imbalance = self._weighted_imbalance(orderbook)

        # 2. Trade Delta (Buy vs Sell volume in last trades)
        delta = self._trade_delta(trades)

        # 3. Price Velocity & Volume Spikes (proportional, signed)
        velocity_score = self._price_velocity(df)

        # 4. Conviction: aligned components reinforce, conflicts dampen
        signs = [self._sign(v) for v in (imbalance, delta, velocity_score) if self._sign(v) != 0]
        if len(signs) == 3 and len(set(signs)) == 1:
            conviction = 1.15
        elif len(signs) >= 2 and len(set(signs)) > 1:
            conviction = 0.85
        else:
            conviction = 1.0

        # Total pressure score
        pressure_score = (imbalance * 0.4) + (delta * 0.4) + velocity_score
        pressure_score *= conviction

        # 5. Dynamic threshold (volatility-aware)
        atr_pct = self._atr_pct(df)
        threshold = self._dynamic_threshold(atr_pct)

        abs_score = abs(pressure_score)

        if abs_score >= threshold:
            direction = "BUY" if pressure_score > 0 else "SELL"
            current_price = df['Close'].iloc[-1] if df is not None and not df.empty and 'Close' in df.columns else None

            if current_price is None or current_price <= 0:
                return {"status": "NO_TRADE",
                        "reason": "No price reference for tape reading", "score": int(abs_score)}

            return {
                "status": "SIGNAL_DETECTED",
                "direction": direction,
                "score": int(min(100, abs_score + 30)), # Boost score if threshold met
                "reason": f"Institutional Pressure detected: {pressure_score:.1f}% bias "
                          f"(threshold {threshold:.1f}%)",
                "entry": current_price,
                "sl": current_price * (0.992 if direction == "BUY" else 1.008),
                "tp": current_price * (1.016 if direction == "BUY" else 0.984),
                "timestamp": datetime.now().isoformat(),
                "metadata": {
                    "imbalance": round(imbalance, 2),
                    "delta": round(delta, 2),
                    "velocity_score": round(velocity_score, 2),
                    "conviction": conviction,
                    "threshold": round(threshold, 2),
                    "atr_pct": round(atr_pct, 2) if atr_pct is not None else None,
                    "pressure_score": round(pressure_score, 2),
                }
            }

        return {
            "status": "NO_TRADE",
            "reason": f"Pressure too low ({abs_score:.1f}% < threshold {threshold:.1f}%)",
            "score": int(abs_score),
            "metadata": {
                "imbalance": round(imbalance, 2),
                "delta": round(delta, 2),
                "velocity_score": round(velocity_score, 2),
                "conviction": conviction,
                "threshold": round(threshold, 2),
                "atr_pct": round(atr_pct, 2) if atr_pct is not None else None,
                "pressure_score": round(pressure_score, 2),
            },
        }
