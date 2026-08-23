"""v2.9 contract tests for the RSI-14 automatic strategy."""

import pandas as pd
import pytest

from api.engines.constants import DEFAULT_RSI_RISK_REWARD
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
    elif volume == "nan":
        volumes = [float("nan")] * n
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
    assert result["strategy"] == "rsi"
    assert result["risk_reward"] == DEFAULT_RSI_RISK_REWARD


def test_exact_bearish_rsi_exit_and_price_recoil():
    result = RSIMeanReversionStrategy().generate_signal("btc_usdt", _reversal_frame("SELL"))
    assert result["status"] == "SIGNAL_DETECTED"
    assert result["direction"] == "SELL"
    assert result["rsi_previous"] >= 70 > result["rsi"]


def test_green_candle_and_higher_low_required():
    frame = _reversal_frame("BUY")
    frame.loc[39, "Open"] = frame.loc[39, "Close"] + 1.0
    result = RSIMeanReversionStrategy().generate_signal("btc_usdt", frame)
    assert result["status"] == "NO_TRADE"
    assert "price" in result["reason"]
    assert result["block_reason"] == "PRICE_CONFIRMATION_MISSING"


def test_red_candle_and_lower_high_required():
    frame = _reversal_frame("SELL")
    frame.loc[39, "Open"] = frame.loc[39, "Close"] - 1.0
    result = RSIMeanReversionStrategy().generate_signal("btc_usdt", frame)
    assert result["status"] == "NO_TRADE"
    assert result["block_reason"] == "PRICE_CONFIRMATION_MISSING"


def test_volume_confirmation_and_ma_fallback():
    strategy = RSIMeanReversionStrategy()
    low_volume = strategy.generate_signal("btc_usdt", _reversal_frame("BUY", "low"))
    assert low_volume["status"] == "NO_TRADE"
    assert low_volume["metadata"].get("volume_available") is True
    assert low_volume["block_reason"] == "VOLUME_CONFIRMATION_MISSING"

    no_volume_frame = _reversal_frame("BUY", None)
    no_volume_frame.loc[39, ["Open", "High", "Low", "Close"]] = [90.0, 111.0, 99.0, 110.0]
    no_volume = strategy.generate_signal("eur_usd", no_volume_frame)
    assert no_volume["status"] == "SIGNAL_DETECTED"
    assert no_volume["metadata"]["volume_available"] is False
    assert no_volume["metadata"]["volume_confirmed"] is True
    assert no_volume["metadata"]["vol_ratio"] is None
    assert no_volume["metadata"]["score_components"]["ema21"] == 25
    assert no_volume["metadata"]["score_components"]["volume"] == 0

    null_volume_frame = no_volume_frame.copy()
    null_volume_frame["Volume"] = 0.0
    null_volume = strategy.generate_signal("eur_usd", null_volume_frame)
    assert null_volume["status"] == "SIGNAL_DETECTED"
    assert null_volume["metadata"]["volume_available"] is False

    nan_volume_frame = no_volume_frame.copy()
    nan_volume_frame["Volume"] = float("nan")
    nan_volume = strategy.generate_signal("eur_usd", nan_volume_frame)
    assert nan_volume["status"] == "SIGNAL_DETECTED"
    assert nan_volume["metadata"]["volume_available"] is False


def test_ema8_ema21_alignment_bonus():
    strategy = RSIMeanReversionStrategy()
    frame = _reversal_frame("BUY")
    result = strategy.generate_signal("btc_usdt", frame)
    assert result["status"] == "SIGNAL_DETECTED"
    assert "ema_alignment" in result["metadata"]
    assert result["metadata"]["score_components"]["ema_alignment"] in (0, 10)


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


def test_take_profit_is_symmetric_rr_1_5_and_clamped():
    strategy = RSIMeanReversionStrategy()
    buy = strategy.generate_signal("btc_usdt", _reversal_frame("BUY"))
    assert buy["risk_reward"] == pytest.approx(1.5)
    assert buy["tp"] == pytest.approx(buy["entry"] + 1.5 * (buy["entry"] - buy["sl"]))

    sell = strategy.generate_signal("btc_usdt", _reversal_frame("SELL"))
    assert sell["risk_reward"] == pytest.approx(1.5)
    assert sell["tp"] == pytest.approx(sell["entry"] - 1.5 * (sell["sl"] - sell["entry"]))

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
    assert result["entry"] == 0.0


def test_partial_score_and_insufficient_data_are_safe():
    partial = RSIMeanReversionStrategy().generate_signal(
        "btc_usdt", _reversal_frame("BUY", "moderate"))
    assert partial["score"] < 84
    assert partial["status"] == "NO_TRADE"
    assert partial["block_reason"] == "SCORE_BELOW_84"

    short = RSIMeanReversionStrategy().generate_signal("btc_usdt", _reversal_frame("BUY", n=39))
    assert short["status"] == "NO_TRADE"
    assert "Insufficient" in short["reason"]
    assert short["block_reason"] == "INSUFFICIENT_CANDLES"


def test_score_83_refused_84_accepted_only_with_gates():
    engine = SignalEngine(min_score=84)
    fake = {"status": "SIGNAL_DETECTED", "strategy": "rsi", "market_id": "btc_usdt",
            "score": 83, "entry": 100.0, "sl": 99.0, "tp": 101.5}
    engine.strategies["rsi"].generate_signal = lambda **_: dict(fake)
    refused = engine.generate_signal(
        {"market_id": "btc_usdt", "volatility": "MEDIUM"},
        {"trading_allowed": True}, _reversal_frame("BUY"),
        strategy_mode="rsi", market_id="btc_usdt")
    assert refused["status"] == "NO_TRADE"
    assert refused["block_reason"] == "SCORE_BELOW_84"

    fake["score"] = 84
    engine.strategies["rsi"].generate_signal = lambda **_: dict(fake)
    accepted = engine.generate_signal(
        {"market_id": "btc_usdt", "volatility": "MEDIUM"},
        {"trading_allowed": True}, _reversal_frame("BUY"),
        strategy_mode="rsi", market_id="btc_usdt")
    assert accepted["status"] == "SIGNAL_DETECTED"


def test_signal_engine_applies_news_and_volatile_score_gates(monkeypatch):
    engine = SignalEngine(min_score=84)
    frame = _reversal_frame("BUY")
    blocked = engine.generate_signal(
        {"market_id": "btc_usdt", "volatility": "MEDIUM"},
        {"trading_allowed": False}, frame, strategy_mode="rsi", market_id="btc_usdt")
    assert blocked["status"] == "NO_TRADE"
    assert blocked["news_blocked"] is True
    assert blocked["block_reason"] in {"NEWS_BLOCKED", "CALENDAR_UNAVAILABLE"}

    calendar = engine.generate_signal(
        {"market_id": "btc_usdt", "volatility": "MEDIUM"},
        {"trading_allowed": False, "status": "DATA_UNAVAILABLE", "news_ok": False},
        frame, strategy_mode="rsi", market_id="btc_usdt")
    assert calendar["block_reason"] == "CALENDAR_UNAVAILABLE"

    fake = {"status": "SIGNAL_DETECTED", "strategy": "rsi", "market_id": "btc_usdt", "score": 84,
            "entry": 100.0, "sl": 99.0, "tp": 102.0}
    monkeypatch.setattr(engine.strategies["rsi"], "generate_signal", lambda **_: dict(fake))
    volatile = engine.generate_signal(
        {"market_id": "btc_usdt", "volatility": "HIGH"},
        {"trading_allowed": True}, frame, strategy_mode="rsi", market_id="btc_usdt")
    assert volatile["status"] == "NO_TRADE"
    assert volatile["min_score_applied"] == 89
    assert volatile["block_reason"] == "VOLATILE_THRESHOLD_89"
