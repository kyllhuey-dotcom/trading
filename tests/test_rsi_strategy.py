"""v2.9 contract tests for the RSI-14 automatic strategy."""

import pandas as pd
import pytest

from api.engines.signal_engine import SignalEngine
from api.engines.strategies.rsi_mean_reversion import RSIMeanReversionStrategy


def _reversal_frame(direction="BUY", volume="high", n=40):
    """Make a deterministic oversold/overbought exit on the final bar."""
    if direction == "BUY":
        closes = [100.0 - i * 0.8 for i in range(n - 1)] + [100.0]
        opens = closes.copy()
        opens[-1] = 85.0
        highs = [close + 1.0 for close in closes]
        lows = [close - 1.0 for close in closes]
        lows[-1] = 89.0
    else:
        closes = [50.0 + i * 0.8 for i in range(n - 1)] + [50.0]
        opens = closes.copy()
        opens[-1] = 65.0
        highs = [close + 1.0 for close in closes]
        lows = [close - 1.0 for close in closes]
        highs[-1] = 61.0

    if volume == "high":
        volumes = [100.0] * (n - 1) + [500.0]
    elif volume == "moderate":
        volumes = [100.0] * (n - 1) + [105.0]
    elif volume == "low":
        volumes = [100.0] * n
    else:
        volumes = None

    data = {"Open": opens, "High": highs, "Low": lows, "Close": closes}
    if volumes is not None:
        data["Volume"] = volumes
    return pd.DataFrame(data)


def test_exact_bullish_rsi_exit_and_price_rebound():
    result = RSIMeanReversionStrategy().generate_signal("btc_usdt", _reversal_frame("BUY"))
    assert result["status"] == "SIGNAL_DETECTED"
    assert result["direction"] == "BUY"
    assert result["rsi_previous"] <= 30 < result["rsi"]
    assert result["metadata"]["ma_confirmation"] is True


def test_exact_bearish_rsi_exit_and_price_recoil():
    result = RSIMeanReversionStrategy().generate_signal("btc_usdt", _reversal_frame("SELL"))
    assert result["status"] == "SIGNAL_DETECTED"
    assert result["direction"] == "SELL"
    assert result["rsi_previous"] >= 70 > result["rsi"]


def test_price_pattern_is_required():
    frame = _reversal_frame("BUY")
    frame.loc[39, "Open"] = frame.loc[39, "Close"] + 1.0
    result = RSIMeanReversionStrategy().generate_signal("btc_usdt", frame)
    assert result["status"] == "NO_TRADE"
    assert "price" in result["reason"]


def test_volume_confirmation_and_ma_fallback():
    strategy = RSIMeanReversionStrategy()
    low_volume = strategy.generate_signal("btc_usdt", _reversal_frame("BUY", "low"))
    assert low_volume["status"] == "NO_TRADE"
    assert low_volume["metadata"].get("volume_available") is True

    no_volume_frame = _reversal_frame("BUY", None)
    # A strong final close also gives the optional EMA8/EMA21 alignment bonus.
    no_volume_frame.loc[39, ["Open", "High", "Low", "Close"]] = [90.0, 111.0, 99.0, 110.0]
    no_volume = strategy.generate_signal("eur_usd", no_volume_frame)
    assert no_volume["status"] == "SIGNAL_DETECTED"
    assert no_volume["metadata"]["volume_available"] is False
    assert no_volume["metadata"]["volume_confirmed"] is True
    assert no_volume["metadata"]["vol_ratio"] is None

    null_volume_frame = no_volume_frame.copy()
    null_volume_frame["Volume"] = 0.0
    null_volume = strategy.generate_signal("eur_usd", null_volume_frame)
    assert null_volume["status"] == "SIGNAL_DETECTED"
    assert null_volume["metadata"]["volume_available"] is False


def test_stops_use_five_bar_extreme_and_atr_buffer():
    strategy = RSIMeanReversionStrategy()
    frame = _reversal_frame("BUY")
    result = strategy.generate_signal("btc_usdt", frame)
    values = strategy._indicators(frame)
    expected_sl = frame["Low"].tail(5).min() - 0.1 * values["atr"].iloc[-1]
    assert result["sl"] == pytest.approx(expected_sl)
    assert result["entry"] == frame["Close"].iloc[-1]

    sell_frame = _reversal_frame("SELL")
    sell = strategy.generate_signal("btc_usdt", sell_frame)
    sell_values = strategy._indicators(sell_frame)
    expected_sell_sl = sell_frame["High"].tail(5).max() + 0.1 * sell_values["atr"].iloc[-1]
    assert sell["sl"] == pytest.approx(expected_sell_sl)


def test_take_profit_is_clamped_to_one_to_two_risk():
    strategy = RSIMeanReversionStrategy(risk_reward_ratio=5.0)
    result = strategy.generate_signal("btc_usdt", _reversal_frame("BUY"))
    assert result["risk_reward"] == 2.0
    assert result["tp"] == pytest.approx(result["entry"] + 2 * (result["entry"] - result["sl"]))

    strategy.set_risk_reward(0.2)
    result = strategy.generate_signal("btc_usdt", _reversal_frame("BUY"))
    assert result["risk_reward"] == 1.0


def test_nan_indicator_is_a_clean_no_trade():
    frame = _reversal_frame("BUY")
    frame.loc[39, "Close"] = float("nan")
    result = RSIMeanReversionStrategy().generate_signal("btc_usdt", frame)
    assert result["status"] == "NO_TRADE"
    assert "indicators" in result["reason"] or "NaN" in result["reason"]


def test_partial_score_and_insufficient_data_are_safe():
    partial = RSIMeanReversionStrategy().generate_signal(
        "btc_usdt", _reversal_frame("BUY", "moderate"))
    # The moderate-volume setup without EMA alignment is below the 84 floor.
    assert partial["score"] < 84
    assert partial["status"] == "NO_TRADE"

    short = RSIMeanReversionStrategy().generate_signal("btc_usdt", _reversal_frame("BUY", n=39))
    assert short["status"] == "NO_TRADE"
    assert "Insufficient" in short["reason"]


def test_signal_engine_applies_news_and_volatile_score_gates(monkeypatch):
    engine = SignalEngine(min_score=84)
    frame = _reversal_frame("BUY")
    blocked = engine.generate_signal(
        {"market_id": "btc_usdt", "volatility": "MEDIUM"},
        {"trading_allowed": False}, frame, strategy_mode="rsi", market_id="btc_usdt")
    assert blocked["status"] == "NO_TRADE"
    assert blocked["news_blocked"] is True

    fake = {"status": "SIGNAL_DETECTED", "strategy": "rsi", "market_id": "btc_usdt", "score": 84,
            "entry": 100.0, "sl": 99.0, "tp": 102.0}
    monkeypatch.setattr(engine.strategies["rsi"], "generate_signal", lambda **_: dict(fake))
    volatile = engine.generate_signal(
        {"market_id": "btc_usdt", "volatility": "HIGH"},
        {"trading_allowed": True}, frame, strategy_mode="rsi", market_id="btc_usdt")
    assert volatile["status"] == "NO_TRADE"
    assert volatile["min_score_applied"] == 89

    fake["score"] = 83
    monkeypatch.setattr(engine.strategies["rsi"], "generate_signal", lambda **_: dict(fake))
    below_floor = engine.generate_signal(
        {"market_id": "btc_usdt", "volatility": "MEDIUM"},
        {"trading_allowed": True}, frame, strategy_mode="rsi", market_id="btc_usdt")
    assert below_floor["status"] == "NO_TRADE"
    assert below_floor["min_score_applied"] == 84
