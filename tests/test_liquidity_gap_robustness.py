"""
LOT D — Robustesse liquidity gap.

Covers:
- logical stop placement (SL under the last supportive liquidity cluster);
- widened spread blocking;
- thin-zone confirmation via top-level volume profile (discounted score);
- volume zone metadata;
- percentage fallback when the logical stop is disabled;
- defensive handling of malformed books;
- regression of the legacy behavior (kept in tests/test_liquidity_gap.py).
"""
import pandas as pd
import pytest

from api.engines.signal_engine import SignalEngine
from api.engines.strategies.liquidity_gap import LiquidityGapStrategy


def _df():
    return pd.DataFrame({'Close': [100] * 20})


# --------------------------------------------------------------------------- #
# 1. Logical stop placement                                                   #
# --------------------------------------------------------------------------- #
def test_buy_signal_logical_stop_under_bid_cluster():
    strategy = LiquidityGapStrategy(gap_threshold_pct=0.3)
    orderbook = {
        'bids': [[100.0, 50], [99.5, 50]],   # strong cluster at 100.0
        'asks': [[100.2, 10], [102.0, 5]],   # 1.8% hole above
    }
    res = strategy.generate_signal("btc_usdt", _df(), orderbook=orderbook)
    assert res["status"] == "SIGNAL_DETECTED"
    assert res["direction"] == "BUY"
    assert res["metadata"]["sl_type"] == "logical"
    # SL sits just under the bid cluster (100.0 - 0.05% buffer), not a fixed 1%
    assert res["sl"] < 100.0
    assert res["sl"] == pytest.approx(100.0 * 0.9995, abs=1e-9)
    assert res["sl"] < res["entry"]
    # TP keeps the 2R structure relative to the logical risk distance
    risk = res["entry"] - res["sl"]
    assert res["tp"] == pytest.approx(res["entry"] + 2 * risk, abs=1e-9)


def test_sell_signal_logical_stop_above_ask_cluster():
    strategy = LiquidityGapStrategy(gap_threshold_pct=0.3)
    orderbook = {
        'bids': [[99.9, 10], [98.0, 5]],     # 1.9% hole below
        'asks': [[100.0, 50], [100.5, 50]],  # strong cluster at 100.0
    }
    res = strategy.generate_signal("btc_usdt", _df(), orderbook=orderbook)
    assert res["status"] == "SIGNAL_DETECTED"
    assert res["direction"] == "SELL"
    assert res["metadata"]["sl_type"] == "logical"
    assert res["sl"] > 100.0
    assert res["sl"] == pytest.approx(100.0 * 1.0005, abs=1e-9)
    assert res["sl"] > res["entry"]
    risk = res["sl"] - res["entry"]
    assert res["tp"] == pytest.approx(res["entry"] - 2 * risk, abs=1e-9)


def test_percentage_fallback_when_logical_stop_disabled():
    strategy = LiquidityGapStrategy(gap_threshold_pct=0.3, logical_stop=False)
    orderbook = {
        'bids': [[100.0, 50], [99.5, 50]],
        'asks': [[100.2, 10], [102.0, 5]],
    }
    res = strategy.generate_signal("btc_usdt", _df(), orderbook=orderbook)
    assert res["status"] == "SIGNAL_DETECTED"
    assert res["metadata"]["sl_type"] == "pct_fallback"
    assert res["sl"] == pytest.approx(res["entry"] * 0.99, abs=1e-9)


# --------------------------------------------------------------------------- #
# 2. Spread widening blocks gap trading                                       #
# --------------------------------------------------------------------------- #
def test_widened_spread_blocks_signal():
    strategy = LiquidityGapStrategy(gap_threshold_pct=0.3, max_spread_pct=0.5)
    orderbook = {
        'bids': [[100.0, 10], [99.0, 10]],
        'asks': [[101.5, 10], [102.0, 10]],  # 1.5% spread + hole
    }
    res = strategy.generate_signal("btc_usdt", _df(), orderbook=orderbook)
    assert res["status"] == "NO_TRADE"
    assert "Spread too wide" in res["reason"]
    assert res["metadata"]["spread_pct"] > 0.5


# --------------------------------------------------------------------------- #
# 3. Thin-zone confirmation (volume profile)                                  #
# --------------------------------------------------------------------------- #
def test_thick_side_discounts_score():
    strategy = LiquidityGapStrategy(gap_threshold_pct=0.3)
    orderbook = {
        'bids': [[100.0, 10], [99.9, 10]],    # thin bids (20)
        'asks': [[100.1, 50], [101.0, 50]],   # thick asks (100) with a hole
    }
    res = strategy.generate_signal("btc_usdt", _df(), orderbook=orderbook)
    assert res["status"] == "SIGNAL_DETECTED"  # gap is big enough to survive the discount
    assert res["metadata"]["thin_confirmed"] is False
    assert res["score"] == 75  # 100 base * 0.75
    assert "discounted" in res["reason"]


def test_thick_side_discount_can_kill_weak_signal():
    strategy = LiquidityGapStrategy(gap_threshold_pct=0.3)
    orderbook = {
        'bids': [[100.0, 10], [99.9, 10]],
        'asks': [[100.1, 50], [100.55, 50]],  # 0.45% hole → base 67 → 50 after discount
    }
    res = strategy.generate_signal("btc_usdt", _df(), orderbook=orderbook)
    assert res["status"] == "NO_TRADE"
    assert "Weak liquidity gap" in res["reason"]
    assert res["metadata"]["thin_confirmed"] is False


def test_volume_profile_metadata():
    strategy = LiquidityGapStrategy(gap_threshold_pct=0.3)
    orderbook = {
        'bids': [[100.0, 30], [99.5, 20]],
        'asks': [[100.2, 5], [102.0, 5]],
    }
    res = strategy.generate_signal("btc_usdt", _df(), orderbook=orderbook)
    meta = res["metadata"]
    assert meta["bid_vol"] == 50
    assert meta["ask_vol"] == 10
    assert meta["ask_share"] == pytest.approx(10 / 60, abs=1e-3)  # rounded to 3 decimals
    assert meta["thin_confirmed"] is True


# --------------------------------------------------------------------------- #
# 4. Defensive handling                                                       #
# --------------------------------------------------------------------------- #
def test_malformed_book_levels_do_not_crash():
    strategy = LiquidityGapStrategy(gap_threshold_pct=0.3)
    # Levels missing the volume field → volumes treated as 0, still tradeable
    orderbook = {
        'bids': [[100.0], [99.9]],
        'asks': [[100.1], [101.0]],
    }
    res = strategy.generate_signal("btc_usdt", _df(), orderbook=orderbook)
    assert res["status"] == "SIGNAL_DETECTED"
    assert res["metadata"]["bid_vol"] == 0 and res["metadata"]["ask_vol"] == 0


def test_single_level_books_never_signal():
    strategy = LiquidityGapStrategy(gap_threshold_pct=0.01)
    orderbook = {'bids': [[100.0, 10]], 'asks': [[100.1, 10]]}
    res = strategy.generate_signal("btc_usdt", _df(), orderbook=orderbook)
    assert res["status"] == "NO_TRADE"  # no consecutive levels → no gap possible


# --------------------------------------------------------------------------- #
# 5. SignalEngine pass-through (liquidity mode)                               #
# --------------------------------------------------------------------------- #
def test_signal_engine_liquidity_mode_passes_orderbook():
    engine = SignalEngine(min_score=50)
    orderbook = {
        'bids': [[100.0, 50], [99.5, 50]],
        'asks': [[100.2, 10], [102.0, 5]],
    }
    res = engine.generate_signal({"status": "VALID", "market_id": "btc_usdt"},
                                 {"trading_allowed": True}, _df(),
                                 strategy_mode="liquidity", market_id="btc_usdt",
                                 orderbook=orderbook)
    assert res["status"] == "SIGNAL_DETECTED"
    assert res["strategy"] == "liquidity"
    assert res["market_id"] == "btc_usdt"
    assert res["metadata"]["sl_type"] == "logical"
