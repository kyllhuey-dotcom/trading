"""RSI(14) reversal strategy used by the automatic execution path.

The strategy deliberately computes every input from the supplied OHLCV frame:
there is no dependency on the market analysis engine or on a provider-specific
indicator.  A signal is a deterministic setup, never a probability estimate.

When volume is absent, entirely zero or entirely NaN, volume points are
replaced by a mandatory EMA21 confirmation (+25 instead of +10). A zero
volume bar is never treated as a confirmation.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from ..constants import (
    AUTO_EXECUTION_SCORE_FLOOR,
    DEFAULT_RSI_RISK_REWARD,
    RSI_RISK_REWARD_BOUNDS,
)
from .base_strategy import BaseStrategy


class RSIMeanReversionStrategy(BaseStrategy):
    """RSI(14) oversold/overbought exit with price and MA confirmation.

    BUY requires an exact RSI cross from ``<= 30`` to ``> 30``, a green candle,
    and a higher low.  SELL is the mirrored ``>= 70`` to ``< 70`` setup.  When
    usable volume is present, the last volume must exceed its 20-bar mean.  A
    missing or entirely null/zero volume series is normal for Yahoo spot Forex;
    in that case volume points are replaced by a mandatory EMA21 confirmation.

    Scores are selectivity scores from 0 to 100, not probabilities.  Stops are
    placed beyond the latest five-bar extreme by 0.1 ATR(14), and the effective
    risk/reward is always clamped to the inclusive range 1:1–1:2. The default
    RSI target RR is 1.5 and is strictly symmetric for BUY and SELL.
    """

    RSI_PERIOD = 14
    EMA_FAST = 8
    EMA_SLOW = 21
    VOLUME_PERIOD = 20
    EXTREME_LOOKBACK = 5
    ATR_PERIOD = 14
    ATR_BUFFER = 0.1
    MIN_BARS = 40

    def __init__(self, risk_reward_ratio: float = DEFAULT_RSI_RISK_REWARD) -> None:
        self.risk_reward_ratio = DEFAULT_RSI_RISK_REWARD
        self.set_risk_reward(risk_reward_ratio)

    def set_risk_reward(self, value: Any) -> None:
        """Set the market's configured RR; the strategy itself enforces 1–2."""
        try:
            candidate = float(value)
        except (TypeError, ValueError):
            return
        if pd.notna(candidate) and candidate > 0:
            self.risk_reward_ratio = candidate

    def _effective_rr(self) -> float:
        lo, hi = RSI_RISK_REWARD_BOUNDS
        try:
            return max(lo, min(hi, float(self.risk_reward_ratio)))
        except (TypeError, ValueError):
            return DEFAULT_RSI_RISK_REWARD

    @staticmethod
    def _no_trade(reason: str, market_id: str, score: int = 0,
                  block_reason: str = "NO_TRADE",
                  direction: Optional[str] = None,
                  **metadata: Any) -> Dict[str, Any]:
        def _num(key: str) -> float:
            value = metadata.get(key)
            try:
                return float(value) if value is not None and pd.notna(value) else 0.0
            except (TypeError, ValueError):
                return 0.0

        return {
            "status": "NO_TRADE",
            "direction": direction,
            "score": int(max(0, min(100, score))),
            "reason": reason,
            "block_reason": block_reason,
            "entry": 0.0,
            "sl": 0.0,
            "tp": 0.0,
            "market_id": market_id,
            "strategy": "rsi",
            "rsi": _num("rsi"),
            "ema8": _num("ema8"),
            "ema21": _num("ema21"),
            "vol_ratio": metadata.get("vol_ratio"),
            "risk_reward": DEFAULT_RSI_RISK_REWARD,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata,
        }

    @staticmethod
    def _numeric_series(df: pd.DataFrame, column: str) -> pd.Series:
        return pd.to_numeric(df[column], errors="coerce")

    def _indicators(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Return the latest indicator values and their source series."""
        close = self._numeric_series(df, "Close")
        high = self._numeric_series(df, "High")
        low = self._numeric_series(df, "Low")

        # Wilder-style RSI: the recursive average is the usual RSI(14)
        # definition and, unlike a scalar shortcut, retains the cross series.
        delta = close.diff()
        gains = delta.clip(lower=0)
        losses = -delta.clip(upper=0)
        average_gain = gains.ewm(
            alpha=1 / self.RSI_PERIOD, adjust=False,
            min_periods=self.RSI_PERIOD,
        ).mean()
        average_loss = losses.ewm(
            alpha=1 / self.RSI_PERIOD, adjust=False,
            min_periods=self.RSI_PERIOD,
        ).mean()
        rs = average_gain / average_loss.replace(0, float("nan"))
        rsi = 100 - (100 / (1 + rs))
        # A run of only gains has RSI 100, while a run of only losses has RSI 0.
        rsi = rsi.where(~((average_loss == 0) & (average_gain > 0)), 100.0)
        rsi = rsi.where(~((average_gain == 0) & (average_loss > 0)), 0.0)

        ema8 = close.ewm(span=self.EMA_FAST, adjust=False,
                         min_periods=self.EMA_FAST).mean()
        ema21 = close.ewm(span=self.EMA_SLOW, adjust=False,
                          min_periods=self.EMA_SLOW).mean()

        true_range = pd.concat(
            [high - low,
             (high - close.shift(1)).abs(),
             (low - close.shift(1)).abs()],
            axis=1,
        ).max(axis=1)
        atr = true_range.rolling(self.ATR_PERIOD,
                                 min_periods=self.ATR_PERIOD).mean()

        volume = None
        volume_available = False
        volume_average = None
        volume_ratio = None
        if "Volume" in df.columns:
            volume = self._numeric_series(df, "Volume")
            usable_volume = volume.dropna()
            # A null/zero/NaN series is the expected Yahoo spot-Forex case. It
            # is not a failed volume confirmation: it selects the EMA21
            # fallback. A zero print is never a confirmation.
            volume_available = bool(not usable_volume.empty and
                                    (usable_volume > 0).any())
            if volume_available:
                volume_average = volume.rolling(
                    self.VOLUME_PERIOD, min_periods=self.VOLUME_PERIOD
                ).mean()
                current_volume = volume.iloc[-1]
                current_average = volume_average.iloc[-1]
                if pd.notna(current_volume) and pd.notna(current_average) and current_average > 0:
                    volume_ratio = float(current_volume / current_average)

        return {
            "close": close,
            "high": high,
            "low": low,
            "rsi": rsi,
            "ema8": ema8,
            "ema21": ema21,
            "atr": atr,
            "volume": volume,
            "volume_available": volume_available,
            "volume_average": volume_average,
            "volume_ratio": volume_ratio,
        }

    def generate_signal(
        self,
        market_id: str,
        df: pd.DataFrame,
        orderbook: Optional[Dict[str, Any]] = None,
        trades: Optional[List[Dict[str, Any]]] = None,
        cross_quotes: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        del orderbook, trades, cross_quotes  # OHLCV-only by design.

        if not isinstance(df, pd.DataFrame) or len(df) < self.MIN_BARS:
            return self._no_trade(
                f"Insufficient OHLCV data for RSI strategy ({len(df) if isinstance(df, pd.DataFrame) else 0} < {self.MIN_BARS})",
                market_id,
                block_reason="INSUFFICIENT_CANDLES",
            )

        required = {"Open", "High", "Low", "Close"}
        missing = sorted(required.difference(df.columns))
        if missing:
            return self._no_trade(
                f"Missing OHLCV columns: {', '.join(missing)}", market_id,
                block_reason="INSUFFICIENT_CANDLES",
            )

        try:
            values = self._indicators(df)
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            return self._no_trade(
                f"RSI indicator calculation error: {exc}", market_id,
                block_reason="PROVIDER_ERROR",
            )

        close = values["close"]
        high = values["high"]
        low = values["low"]
        open_price = self._numeric_series(df, "Open")
        rsi = values["rsi"]
        ema8 = values["ema8"]
        ema21 = values["ema21"]
        atr = values["atr"]

        # The decision only uses the current bar and its predecessor.  Checking
        # exactly these values prevents a late NaN from becoming an accidental
        # signal while allowing historical gaps outside the decision window.
        latest_values = [
            close.iloc[-1], close.iloc[-2], open_price.iloc[-1],
            high.iloc[-1], high.iloc[-2], low.iloc[-1], low.iloc[-2],
            rsi.iloc[-1], rsi.iloc[-2], ema8.iloc[-1], ema21.iloc[-1],
            atr.iloc[-1],
        ]
        if any(pd.isna(value) for value in latest_values):
            return self._no_trade(
                "RSI/EMA/ATR indicators are not valid on the latest bars", market_id,
                block_reason="INSUFFICIENT_CANDLES",
            )
        decision_window = pd.concat(
            [open_price, high, low, close], axis=1
        ).tail(self.EXTREME_LOOKBACK)
        if decision_window.isna().any().any():
            return self._no_trade(
                "NaN in the latest OHLC decision window", market_id,
                block_reason="INSUFFICIENT_CANDLES",
            )

        rsi_previous = float(rsi.iloc[-2])
        rsi_current = float(rsi.iloc[-1])
        ema8_current = float(ema8.iloc[-1])
        ema21_current = float(ema21.iloc[-1])
        atr_current = float(atr.iloc[-1])
        entry = float(close.iloc[-1])

        bullish_cross = rsi_previous <= 30.0 and rsi_current > 30.0
        bearish_cross = rsi_previous >= 70.0 and rsi_current < 70.0
        bullish_price = (
            float(close.iloc[-1]) > float(open_price.iloc[-1])
            and float(low.iloc[-1]) > float(low.iloc[-2])
        )
        bearish_price = (
            float(close.iloc[-1]) < float(open_price.iloc[-1])
            and float(high.iloc[-1]) < float(high.iloc[-2])
        )

        if bullish_cross and bullish_price:
            direction = "BUY"
        elif bearish_cross and bearish_price:
            direction = "SELL"
        else:
            reasons = []
            block_reason = "RSI_NO_CROSS"
            if not (bullish_cross or bearish_cross):
                reasons.append("RSI(14) did not exit oversold/overbought")
            if not (bullish_price or bearish_price):
                reasons.append("price rebound/recoil confirmation missing")
                if bullish_cross or bearish_cross:
                    block_reason = "PRICE_CONFIRMATION_MISSING"
            return self._no_trade(
                "; ".join(reasons) or "No RSI reversal setup",
                market_id,
                block_reason=block_reason,
                rsi=rsi_current,
                rsi_previous=rsi_previous,
                ema8=ema8_current,
                ema21=ema21_current,
                atr=atr_current,
            )

        ma_confirmation = (
            entry > ema21_current if direction == "BUY"
            else entry < ema21_current
        )
        alignment = (
            ema8_current > ema21_current if direction == "BUY"
            else ema8_current < ema21_current
        )

        volume_ratio = values["volume_ratio"]
        volume_available = bool(values["volume_available"])
        if volume_available:
            if volume_ratio is None or pd.isna(volume_ratio):
                return self._no_trade(
                    "Volume confirmation unavailable for the latest bar", market_id,
                    block_reason="VOLUME_CONFIRMATION_MISSING",
                    rsi=rsi_current, rsi_previous=rsi_previous,
                    ema8=ema8_current, ema21=ema21_current,
                )
            volume_confirmed = volume_ratio > 1.0
            if not volume_confirmed:
                return self._no_trade(
                    f"Volume confirmation missing ({volume_ratio:.2f}x <= 1.00x mean)",
                    market_id,
                    block_reason="VOLUME_CONFIRMATION_MISSING",
                    rsi=rsi_current, rsi_previous=rsi_previous,
                    ema8=ema8_current, ema21=ema21_current,
                    vol_ratio=round(volume_ratio, 6),
                    volume_available=True,
                )
            if not ma_confirmation:
                return self._no_trade(
                    "EMA21 confirmation missing for the RSI reversal", market_id,
                    block_reason="EMA21_CONFIRMATION_MISSING",
                    rsi=rsi_current, rsi_previous=rsi_previous,
                    ema8=ema8_current, ema21=ema21_current,
                    vol_ratio=round(volume_ratio, 6), volume_available=True,
                )
        else:
            # In the null/zero-volume fallback, MA confirmation is explicitly
            # mandatory. A zero / all-NaN volume series is NEVER a confirmation.
            volume_confirmed = ma_confirmation
            if not ma_confirmation:
                return self._no_trade(
                    "Volume unavailable: EMA21 confirmation is mandatory", market_id,
                    block_reason="EMA21_CONFIRMATION_MISSING",
                    rsi=rsi_current, rsi_previous=rsi_previous,
                    ema8=ema8_current, ema21=ema21_current,
                    vol_ratio=None, volume_available=False,
                )

        # Deterministic score. The RSI cross and price pattern are mandatory
        # components; therefore a score cannot turn a partial setup into a
        # signal.  The automatic engine applies the independent 84 floor too.
        # EMA21 is never added twice: without volume it replaces the volume
        # component (+25); with volume it contributes +10.
        score = 30 + 25
        if volume_available:
            score += 25 if volume_ratio > 1.2 else 15
            score += 10
            ma_points = 10
        else:
            score += 25
            ma_points = 25
        if alignment:
            score += 10

        last_five_low = float(low.tail(self.EXTREME_LOOKBACK).min())
        last_five_high = float(high.tail(self.EXTREME_LOOKBACK).max())
        buffer = self.ATR_BUFFER * atr_current
        if direction == "BUY":
            stop_loss = last_five_low - buffer
            risk_distance = entry - stop_loss
        else:
            stop_loss = last_five_high + buffer
            risk_distance = stop_loss - entry

        if risk_distance <= 0 or pd.isna(risk_distance):
            return self._no_trade(
                "Invalid risk distance (stop is not protective)", market_id, score,
                block_reason="RISK_BLOCKED",
                rsi=rsi_current, rsi_previous=rsi_previous,
                ema8=ema8_current, ema21=ema21_current,
                vol_ratio=volume_ratio,
            )

        # Keep partial RSI setups out of every automatic path even when the
        # strategy is called directly rather than through SignalEngine.
        if score < AUTO_EXECUTION_SCORE_FLOOR:
            return self._no_trade(
                f"Below minimum score ({score}/{AUTO_EXECUTION_SCORE_FLOOR})", market_id, score,
                block_reason="SCORE_BELOW_84",
                rsi=rsi_current, rsi_previous=rsi_previous,
                ema8=ema8_current, ema21=ema21_current,
                vol_ratio=volume_ratio, volume_available=volume_available,
                ma_confirmation=ma_confirmation, ema_alignment=alignment,
            )

        risk_reward = self._effective_rr()
        take_profit = (
            entry + risk_distance * risk_reward
            if direction == "BUY"
            else entry - risk_distance * risk_reward
        )

        metadata = {
            "rsi": round(rsi_current, 8),
            "rsi_previous": round(rsi_previous, 8),
            "ema8": round(ema8_current, 8),
            "ema21": round(ema21_current, 8),
            "volume_available": volume_available,
            "volume": (
                float(values["volume"].iloc[-1])
                if volume_available and values["volume"] is not None
                and pd.notna(values["volume"].iloc[-1]) else None
            ),
            "volume_average": (
                float(values["volume_average"].iloc[-1])
                if volume_available and values["volume_average"] is not None
                and pd.notna(values["volume_average"].iloc[-1]) else None
            ),
            "vol_ratio": round(float(volume_ratio), 8) if volume_ratio is not None else None,
            "volume_confirmed": bool(volume_confirmed),
            "ma_confirmation": bool(ma_confirmation),
            "ema_alignment": bool(alignment),
            "last_five_low": last_five_low,
            "last_five_high": last_five_high,
            "atr": round(atr_current, 8),
            "atr14": round(atr_current, 8),
            "atr_buffer": round(buffer, 8),
            "risk_distance": round(float(risk_distance), 8),
            "risk_reward": risk_reward,
            "effective_rr": risk_reward,
            "score_components": {
                "rsi_cross": 30,
                "price_rebound": 25,
                # No-volume mode replaces, rather than adds to, the volume
                # component: EMA21 contributes 25 instead of 10.
                "volume": 0 if not volume_available else (25 if volume_ratio > 1.2 else 15),
                "ema21": ma_points,
                "ema_alignment": 10 if alignment else 0,
            },
        }
        return {
            "status": "SIGNAL_DETECTED",
            "direction": direction,
            "score": int(min(100, score)),
            "reason": (
                f"RSI(14) {direction} reversal confirmed; "
                f"price pattern, EMA21 and "
                f"{'volume' if volume_available else 'MA fallback'} confirmed "
                f"(score {int(min(100, score))}/100)"
            ),
            "block_reason": None,
            "entry": entry,
            "sl": float(stop_loss),
            "tp": float(take_profit),
            "market_id": market_id,
            "strategy": "rsi",
            "timestamp": datetime.now().isoformat(),
            "rsi": rsi_current,
            "rsi_previous": rsi_previous,
            "ema8": ema8_current,
            "ema21": ema21_current,
            "vol_ratio": volume_ratio,
            "atr": atr_current,
            "risk_reward": risk_reward,
            "metadata": metadata,
        }
