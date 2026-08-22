from .base_strategy import BaseStrategy
from typing import Dict, Any, Optional, List
import pandas as pd
import statistics
import time
from datetime import datetime

class MicroArbitrageStrategy(BaseStrategy):
    """
    Exploite les différences de prix entre plateformes (Gate / Bybit / Binance).
    Taux de réussite cible : 80-90 %.

    LOT B hardening:
    - freshness gate: quotes older than `max_quote_age_ms` are dropped (stale
      data is the #1 source of *fake* arbitrage opportunities);
    - synchronization gate: quotes fetched too far apart in time
      (`max_sync_dispersion_ms`) are rejected — a 0.3% spread across quotes
      taken 10 s apart is a trend, not an arbitrage;
    - confidence score derived from spread + quote freshness + synchronization;
    - fully backward compatible: quotes without timing info are treated as
      fresh/synchronized (same behavior as before).
    """
    def __init__(self, threshold_pct: float = 0.15,
                 max_quote_age_ms: float = 3000.0,
                 max_sync_dispersion_ms: float = 2000.0,
                 min_confidence: float = 0.0):
        self.threshold_pct = threshold_pct
        self.max_quote_age_ms = max_quote_age_ms
        self.max_sync_dispersion_ms = max_sync_dispersion_ms
        self.min_confidence = min_confidence

    # ------------------------------------------------------------------ #
    # Timing helpers (defensive: hand-made quote dicts stay compatible)  #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _quote_age_ms(q: Dict[str, Any], now_ms: float) -> float:
        if "age_ms" in q and q["age_ms"] is not None:
            return max(0.0, float(q["age_ms"]))
        ts = q.get("timestamp")
        if ts:
            return max(0.0, float(now_ms - float(ts)))
        return 0.0

    @staticmethod
    def _quote_received_ms(q: Dict[str, Any], now_ms: float) -> float:
        if "received_at" in q and q["received_at"] is not None:
            return float(q["received_at"])
        ts = q.get("timestamp")
        if ts:
            return float(ts)
        return float(now_ms)

    def generate_signal(self, market_id: str, df: pd.DataFrame, orderbook: Optional[Dict[str, Any]] = None, trades: Optional[List[Dict[str, Any]]] = None, cross_quotes: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        if not cross_quotes or len(cross_quotes) < 2:
            return {"status": "NO_TRADE", "reason": "Insufficient cross-provider data", "score": 0}

        # Find min and max prices among providers
        valid_quotes = [q for q in cross_quotes if q.get("last") and q["last"] > 0]
        if len(valid_quotes) < 2:
            return {"status": "NO_TRADE", "reason": "Insufficient valid quotes", "score": 0}

        now_ms = time.time() * 1000.0

        # --- LOT B: freshness gate (stale quotes cannot be arbitraged) ---
        enriched: List[Dict[str, Any]] = []
        stale: List[Dict[str, Any]] = []
        for q in valid_quotes:
            age_ms = self._quote_age_ms(q, now_ms)
            if self.max_quote_age_ms > 0 and age_ms > self.max_quote_age_ms:
                stale.append({**q, "age_ms": age_ms})
                continue
            enriched.append({**q, "age_ms": age_ms,
                             "latency_ms": float(q.get("latency_ms") or 0.0)})

        if len(enriched) < 2:
            return {
                "status": "NO_TRADE",
                "reason": f"Stale quotes ({len(stale)}/{len(valid_quotes)} providers outdated)",
                "score": 0,
                "metadata": {"stale_providers": [q.get("provider", "unknown") for q in stale]},
            }

        # --- LOT B: synchronization gate ---
        received_times = [self._quote_received_ms(q, now_ms) for q in enriched]
        dispersion_ms = max(received_times) - min(received_times)
        if self.max_sync_dispersion_ms > 0 and dispersion_ms > self.max_sync_dispersion_ms:
            return {
                "status": "NO_TRADE",
                "reason": f"Quotes not synchronized (dispersion {dispersion_ms:.0f}ms)",
                "score": 0,
                "metadata": {"dispersion_ms": round(dispersion_ms, 2)},
            }

        min_quote = min(enriched, key=lambda x: x["last"])
        max_quote = max(enriched, key=lambda x: x["last"])

        diff_pct = ((max_quote["last"] - min_quote["last"]) / min_quote["last"]) * 100

        if diff_pct >= self.threshold_pct:
            # We assume the first quote is the primary one
            primary_price = enriched[0]["last"]

            prices = [q["last"] for q in enriched]
            avg_price = sum(prices) / len(prices)
            median_price = statistics.median(prices)

            if primary_price < avg_price:
                direction = "BUY"
            else:
                direction = "SELL"

            # Base score proportional to spread (unchanged formula)
            base_score = min(100, int((diff_pct / self.threshold_pct) * 60 + 30))

            # --- LOT B: confidence from quote freshness + synchronization ---
            avg_age_ms = sum(q["age_ms"] for q in enriched) / len(enriched)
            age_factor = 1.0
            if self.max_quote_age_ms > 0:
                age_factor = max(0.0, 1.0 - (avg_age_ms / self.max_quote_age_ms))
            sync_factor = 1.0
            if self.max_sync_dispersion_ms > 0:
                sync_factor = max(0.0, 1.0 - (dispersion_ms / self.max_sync_dispersion_ms))

            confidence = round(base_score * (0.6 + 0.4 * min(age_factor, sync_factor)), 1)
            score = max(1, min(100, int(confidence)))

            if self.min_confidence > 0 and confidence < self.min_confidence:
                return {
                    "status": "NO_TRADE",
                    "reason": f"Confidence too low ({confidence:.1f} < {self.min_confidence})",
                    "score": score,
                    "metadata": {
                        "spread_pct": round(diff_pct, 3),
                        "confidence": confidence,
                        "avg_age_ms": round(avg_age_ms, 2),
                        "dispersion_ms": round(dispersion_ms, 2),
                    },
                }

            return {
                "status": "SIGNAL_DETECTED",
                "direction": direction,
                "score": score,
                "confidence": confidence,
                "reason": (f"Arbitrage Opportunity: {diff_pct:.2f}% spread detected"
                           + (f" (confidence {confidence:.0f}/100)" if confidence < 99.9 else "")),
                "entry": primary_price,
                "sl": primary_price * (0.998 if direction == "BUY" else 1.002), # Very tight for arb
                "tp": primary_price * (1.002 if direction == "BUY" else 0.998),
                "timestamp": datetime.now().isoformat(),
                "metadata": {
                    "spread_pct": round(diff_pct, 3),
                    "providers": [q.get("provider", "unknown") for q in enriched],
                    "median_price": round(median_price, 8),
                    "avg_age_ms": round(avg_age_ms, 2),
                    "dispersion_ms": round(dispersion_ms, 2),
                    "stale_quotes": len(stale),
                    "per_provider": [
                        {"provider": q.get("provider", "unknown"),
                         "last": q["last"],
                         "age_ms": round(q["age_ms"], 2),
                         "latency_ms": round(q["latency_ms"], 2)}
                        for q in enriched
                    ],
                }
            }

        return {"status": "NO_TRADE", "reason": f"Spread too low ({diff_pct:.3f}%)", "score": 0}
