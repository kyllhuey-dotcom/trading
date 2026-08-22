from .base_strategy import BaseStrategy
from typing import Dict, Any, Optional, List
import pandas as pd
from datetime import datetime

class LiquidityGapStrategy(BaseStrategy):
    """
    Détecte les zones de faible liquidité et anticipe les mouvements rapides.
    Taux de réussite cible : 75-85 %.

    LOT D hardening:
    - finer gap detection: price holes + spread widening (blocking) +
      top-level volume profile (thin-zone confirmation);
    - anticipatory signal with a LOGICAL stop: the SL is placed just below
      the last liquidity cluster on the supportive side (or above it for
      shorts) instead of an arbitrary fixed percentage;
    - backward compatible (same threshold/signature and reason phrases).
    """
    def __init__(self, gap_threshold_pct: float = 0.3,
                 max_spread_pct: float = 0.5,
                 thin_side_ratio: float = 0.55,
                 min_score: int = 60,
                 sl_buffer_pct: float = 0.05,
                 logical_stop: bool = True):
        self.gap_threshold_pct = gap_threshold_pct
        self.max_spread_pct = max_spread_pct
        self.thin_side_ratio = thin_side_ratio
        self.min_score = min_score
        self.sl_buffer_pct = sl_buffer_pct
        self.logical_stop = logical_stop

    # ------------------------------------------------------------------ #
    # Detection helpers                                                   #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _max_gap(levels: List[List[float]], direction: str) -> float:
        """Biggest consecutive price hole (%) among the first 15 levels."""
        max_gap = 0.0
        n = min(15, len(levels))
        for i in range(n - 1):
            try:
                p0, p1 = float(levels[i][0]), float(levels[i + 1][0])
            except (TypeError, IndexError, ValueError):
                continue
            if p0 <= 0:
                continue
            gap = abs(p1 - p0) / p0 * 100
            if gap > max_gap:
                max_gap = gap
        return max_gap

    @staticmethod
    def _side_volume(levels: List[List[float]]) -> float:
        """Total size resting on the first 15 levels (defensive vs missing sizes)."""
        total = 0.0
        for level in levels[:15]:
            try:
                total += float(level[1])
            except (TypeError, IndexError, ValueError):
                continue
        return total

    def generate_signal(self, market_id: str, df: pd.DataFrame, orderbook: Optional[Dict[str, Any]] = None, trades: Optional[List[Dict[str, Any]]] = None, cross_quotes: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        if not orderbook or 'bids' not in orderbook or 'asks' not in orderbook or not orderbook['bids'] or not orderbook['asks']:
            return {"status": "NO_TRADE", "reason": "No orderbook data", "score": 0}

        bids = orderbook['bids']
        asks = orderbook['asks']

        # 1. Spread Analysis (widened spread = poor liquidity → block)
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        spread_pct = ((best_ask - best_bid) / best_bid) * 100 if best_bid > 0 else 0.0
        if spread_pct > self.max_spread_pct:
            return {
                "status": "NO_TRADE",
                "reason": f"Spread too wide for gap trading ({spread_pct:.2f}%)",
                "score": 0,
                "metadata": {"spread_pct": round(spread_pct, 3),
                             "max_spread_pct": self.max_spread_pct},
            }

        # 2. Gap detection in Bids/Asks (holes in the book, first 15 levels)
        max_bid_gap = self._max_gap(bids, "BID")
        max_ask_gap = self._max_gap(asks, "ASK")

        # 3. Volume profile: which side is thin? (thin side gets swept first)
        bid_vol = self._side_volume(bids)
        ask_vol = self._side_volume(asks)
        total_vol = bid_vol + ask_vol
        ask_share = (ask_vol / total_vol) if total_vol > 0 else 0.5
        bid_share = (bid_vol / total_vol) if total_vol > 0 else 0.5

        # 4. Candidate direction + scoring
        score = 0
        direction = "BUY"
        reason = "No significant liquidity gap"
        signal_gap = 0.0
        thin_share = 0.5

        if max_ask_gap > self.gap_threshold_pct and max_ask_gap > max_bid_gap:
            direction = "BUY"
            signal_gap = max_ask_gap
            thin_share = ask_share
            reason = f"Liquidity Gap in Asks: {max_ask_gap:.2f}%"
        elif max_bid_gap > self.gap_threshold_pct:
            direction = "SELL"
            signal_gap = max_bid_gap
            thin_share = bid_share
            reason = f"Liquidity Gap in Bids: {max_bid_gap:.2f}%"
        else:
            return {"status": "NO_TRADE", "reason": "No significant liquidity gap", "score": 0}

        base_score = min(100, int(signal_gap * 150))

        # 5. Thin-zone confirmation: the gapped side must be the *thin* side.
        # A gap behind a thick wall is unlikely to be swept — contradiction.
        thin_confirmed = thin_share <= self.thin_side_ratio
        if thin_confirmed:
            score = base_score
        else:
            score = int(base_score * 0.75)

        if score < self.min_score:
            return {
                "status": "NO_TRADE",
                "reason": "Weak liquidity gap",
                "score": score,
                "metadata": {
                    "max_bid_gap": round(max_bid_gap, 3),
                    "max_ask_gap": round(max_ask_gap, 3),
                    "spread_pct": round(spread_pct, 3),
                    "bid_vol": bid_vol,
                    "ask_vol": ask_vol,
                    "thin_confirmed": thin_confirmed,
                },
            }

        # 6. Anticipatory signal with a LOGICAL stop: SL just under the last
        #    supportive liquidity cluster (or above it for shorts).
        current_price = (best_bid + best_ask) / 2
        sl_type = "pct_fallback"
        if self.logical_stop:
            if direction == "BUY":
                logical_sl = best_bid * (1 - self.sl_buffer_pct / 100)
                if logical_sl < current_price:
                    sl = logical_sl
                    sl_type = "logical"
                else:
                    sl = current_price * 0.99
            else:
                logical_sl = best_ask * (1 + self.sl_buffer_pct / 100)
                if logical_sl > current_price:
                    sl = logical_sl
                    sl_type = "logical"
                else:
                    sl = current_price * 1.01
        else:
            sl = current_price * (0.99 if direction == "BUY" else 1.01)

        risk_dist = abs(current_price - sl)
        if risk_dist <= 0:
            sl = current_price * (0.99 if direction == "BUY" else 1.01)
            risk_dist = abs(current_price - sl)
            sl_type = "pct_fallback"

        tp = current_price + (risk_dist * 2.0) if direction == "BUY" else current_price - (risk_dist * 2.0)

        return {
            "status": "SIGNAL_DETECTED",
            "direction": direction,
            "score": score,
            "confidence": float(min(100, base_score)),
            "reason": reason + ("" if thin_confirmed else " (thick side — discounted)"),
            "entry": current_price,
            "sl": round(float(sl), 8),
            "tp": round(float(tp), 8),
            "timestamp": datetime.now().isoformat(),
            "metadata": {
                "max_bid_gap": round(max_bid_gap, 3),
                "max_ask_gap": round(max_ask_gap, 3),
                "spread_pct": round(spread_pct, 3),
                "bid_vol": bid_vol,
                "ask_vol": ask_vol,
                "ask_share": round(ask_share, 3),
                "bid_share": round(bid_share, 3),
                "thin_confirmed": thin_confirmed,
                "sl_type": sl_type,
                "logical_sl": round(float(sl), 8),
                "signal_gap_pct": round(signal_gap, 3),
            }
        }
