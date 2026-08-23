"""
LOT P — Profitability hardening.

Each test locks one profit leak shut:
1. min_signal_score now gates EVERY strategy (the quality leak);
2. risk_reward_ratio setting is actually applied to SL/TP;
3. cost-vs-volatility filter blocks mathematically losing trades;
4. alpha override is opt-in (never trade through news/range by default);
5. consecutive-loss circuit breaker + anti-martingale risk scaling;
6. time stop closes stale positions;
7. profit audit script computes correct per-strategy stats.
"""
import sys
from datetime import datetime, timedelta

import pandas as pd
import pytest

from api.engines.db_manager import DatabaseManager
from api.engines.execution_engine import ExecutionEngine
from api.engines.portfolio_engine import PortfolioEngine
from api.engines.risk_engine import RiskEngine
from api.engines.signal_engine import SignalEngine

sys.path.insert(0, "scripts")
from profit_audit import analyze_db  # noqa: E402


def _flat_df():
    return pd.DataFrame({'Close': [100.0] * 20, 'Volume': [10.0] * 20})


def _calm_df():
    """Flat closes + micro bars → tiny ATR, but analysis can still score high."""
    return pd.DataFrame({'Close': [100.0] * 30,
                         'High': [100.001] * 30,
                         'Low': [99.999] * 30,
                         'Volume': [100.0] * 30})


def _trending_df(bar_range=0.5):
    closes = [100.0 + i * 0.5 for i in range(30)]
    return pd.DataFrame({
        'Close': closes,
        'High': [c + bar_range for c in closes],
        'Low': [c - bar_range for c in closes],
        'Volume': [100.0 + i for i in range(30)],
    })


def _strong_analysis():
    return {"status": "VALID", "trend": "BULLISH", "market_state": "TREND",
            "momentum": 1.0, "is_hh": True, "is_hl": True, "is_lh": False, "is_ll": False,
            "bos": True, "choch": False, "htf_bias": "BULLISH",
            "last_low": 90.0, "last_high": 130.0, "market_id": "btc_usdt"}


def _calm_analysis():
    """Same strong analysis, but structural levels hugging the current price
    so the SL comes from the (tiny) ATR — used by the cost-filter tests."""
    a = _strong_analysis()
    a["last_low"] = 99.99
    a["last_high"] = 100.01
    return a


# --------------------------------------------------------------------------- #
# 1. Global score gate — the main quality leak                               #
# --------------------------------------------------------------------------- #
def test_min_score_gates_tape_strategy():
    """A tape signal scoring below min_score must NOT reach execution."""
    engine = SignalEngine(min_score=80)
    engine.set_active_strategies(["tape"])
    # pressure 40 → score 70 (< 80) with the fallback threshold
    orderbook = {'bids': [[100.3, 6.0]], 'asks': [[100.5, 2.0]]}
    trades = [{'side': 'buy', 'amount': 1.0}] * 3 + [{'side': 'sell', 'amount': 1.0}]
    res = engine.generate_signal({"status": "VALID", "market_id": "btc_usdt"},
                                 {"trading_allowed": True}, _flat_df(),
                                 strategy_mode="tape", market_id="btc_usdt",
                                 orderbook=orderbook, trades=trades)
    assert res["status"] == "NO_TRADE"
    assert "Below minimum score" in res["reason"]
    assert res["score"] == 70  # original score preserved for diagnostics


def test_min_score_gates_liquidity_strategy():
    engine = SignalEngine(min_score=80)
    engine.set_active_strategies(["liquidity"])
    orderbook = {'bids': [[100.0, 50], [99.5, 50]],
                 'asks': [[100.2, 10], [102.0, 5]]}  # big gap → score 100
    res_ok = engine.generate_signal({"status": "VALID", "market_id": "btc_usdt"},
                                    {"trading_allowed": True}, _flat_df(),
                                    strategy_mode="liquidity", market_id="btc_usdt",
                                    orderbook=orderbook)
    assert res_ok["status"] == "SIGNAL_DETECTED"  # above gate → passes

    # Small gap → liquidity internal score 60-ish, below the global 80 gate
    orderbook_small = {'bids': [[100.0, 50], [99.9, 50]],
                       'asks': [[100.1, 10], [100.5, 5]]}
    res_blocked = engine.generate_signal({"status": "VALID", "market_id": "btc_usdt"},
                                         {"trading_allowed": True}, _flat_df(),
                                         strategy_mode="liquidity", market_id="btc_usdt",
                                         orderbook=orderbook_small)
    assert res_blocked["status"] == "NO_TRADE"
    assert "Below minimum score" in res_blocked["reason"] or \
           "Weak liquidity gap" in res_blocked["reason"]


def test_min_score_still_allows_high_quality_custom_signals():
    engine = SignalEngine(min_score=80)
    engine.set_active_strategies(["tape"])
    # Huge aligned pressure → score 100 ≥ 80 → passes
    orderbook = {'bids': [[100.0, 9.0]], 'asks': [[101.0, 1.0]]}
    trades = [{'side': 'buy', 'amount': 9.0}, {'side': 'sell', 'amount': 1.0}]
    df = pd.DataFrame({'Close': [100.0] * 4 + [102.667]})
    res = engine.generate_signal({"status": "VALID", "market_id": "btc_usdt"},
                                 {"trading_allowed": True}, df,
                                 strategy_mode="tape", market_id="btc_usdt",
                                 orderbook=orderbook, trades=trades)
    assert res["status"] == "SIGNAL_DETECTED"
    assert res["score"] >= 80


# --------------------------------------------------------------------------- #
# 2. risk_reward_ratio actually applied                                       #
# --------------------------------------------------------------------------- #
def test_risk_reward_setting_changes_take_profit():
    engine = SignalEngine(min_score=80, risk_reward=1.5)
    res = engine.generate_signal(_strong_analysis(), {"trading_allowed": True},
                                 _trending_df(), market_id="btc_usdt",
                                 strategy_mode="structure")
    assert res["status"] == "SIGNAL_DETECTED"
    risk = res["entry"] - res["sl"]
    assert res["tp"] == pytest.approx(res["entry"] + 1.5 * risk, rel=1e-6)
    assert res["risk_reward"] == 1.5

    # Invalid values are ignored (sanity bounds)
    engine.set_risk_reward(99.0)
    assert engine.risk_reward == 1.5
    engine.set_risk_reward("abc")
    assert engine.risk_reward == 1.5


# --------------------------------------------------------------------------- #
# 3. Cost-vs-volatility filter                                                #
# --------------------------------------------------------------------------- #
def test_cost_filter_blocks_low_volatility_trade():
    """Structure signal on calm data (tiny ATR) → costs eat the edge → blocked."""
    engine = SignalEngine(min_score=80, fee_pct=0.05, slippage_pct=0.05,
                          max_cost_ratio=0.5)
    res = engine.generate_signal(_calm_analysis(), {"trading_allowed": True},
                                 _calm_df(),  # micro-ATR, but high synthetic score
                                 market_id="btc_usdt", strategy_mode="structure")
    assert res["status"] == "NO_TRADE"
    assert "Cost ratio too high" in res["reason"]
    assert res.get("cost_blocked") is True


def test_cost_filter_allows_volatile_trade():
    engine = SignalEngine(min_score=80, fee_pct=0.05, slippage_pct=0.05,
                          max_cost_ratio=0.5)
    res = engine.generate_signal(_strong_analysis(), {"trading_allowed": True},
                                 _trending_df(bar_range=2.0),  # fat ATR
                                 market_id="btc_usdt", strategy_mode="structure")
    assert res["status"] == "SIGNAL_DETECTED"
    assert not res.get("cost_blocked")


def test_cost_filter_configurable():
    engine = SignalEngine(min_score=80)
    engine.set_cost_params(max_cost_ratio=99.0)  # filter effectively off
    res = engine.generate_signal(_calm_analysis(), {"trading_allowed": True},
                                 _calm_df(),
                                 market_id="btc_usdt", strategy_mode="structure")
    assert res["status"] == "SIGNAL_DETECTED"


# --------------------------------------------------------------------------- #
# 4. Alpha override opt-in                                                    #
# --------------------------------------------------------------------------- #
def _range_analysis():
    a = _strong_analysis()
    a["market_state"] = "RANGE"
    return a


def test_alpha_override_disabled_by_default_blocks_range_trades():
    engine = SignalEngine(min_score=80)  # default: alpha_override off
    res = engine.generate_signal(_range_analysis(), {"trading_allowed": True},
                                 _trending_df(), market_id="btc_usdt",
                                 strategy_mode="structure")
    assert res["status"] == "NO_TRADE"
    assert res["alpha_override"] is False


def test_alpha_override_opt_in_allows_range_trades():
    engine = SignalEngine(min_score=80, alpha_override_enabled=True)
    res = engine.generate_signal(_range_analysis(), {"trading_allowed": True},
                                 _trending_df(), market_id="btc_usdt",
                                 strategy_mode="structure")
    assert res["status"] == "SIGNAL_DETECTED"
    assert res["alpha_override"] is True


def test_alpha_override_does_not_bypass_news_anymore():
    engine = SignalEngine(min_score=80, alpha_override_enabled=True)
    res = engine.generate_signal(_range_analysis(), {"trading_allowed": False},
                                 _trending_df(), market_id="btc_usdt",
                                 strategy_mode="structure")
    assert res["status"] == "NO_TRADE"


# --------------------------------------------------------------------------- #
# 5. Consecutive-loss circuit breaker + anti-martingale                       #
# --------------------------------------------------------------------------- #
def test_consecutive_losses_auto_pause():
    risk = RiskEngine(max_consecutive_losses=3, cool_down_mins=0)
    risk.register_closed_trade(-1.0)
    risk.register_closed_trade(-1.0)
    res = risk.calculate_position_size(balance=1000.0, entry=100.0, stop_loss=95.0)
    assert res["allowed"] is True  # 2 losses: still trading (scaled risk)
    risk.register_closed_trade(-1.0)
    res = risk.calculate_position_size(balance=1000.0, entry=100.0, stop_loss=95.0)
    assert res["allowed"] is False
    assert "consecutive losses" in res["reason"]


def test_win_resets_the_streak():
    risk = RiskEngine(max_consecutive_losses=3, cool_down_mins=0)
    risk.register_closed_trade(-1.0)
    risk.register_closed_trade(-1.0)
    risk.register_closed_trade(5.0)  # a win resets the streak
    res = risk.calculate_position_size(balance=1000.0, entry=100.0, stop_loss=95.0)
    assert res["allowed"] is True
    assert risk.consecutive_losses == 0


def test_anti_martingale_scaling():
    risk = RiskEngine(max_risk_pct=1.0, max_leverage=20, cool_down_mins=0)
    base = risk.calculate_position_size(balance=1000.0, entry=100.0, stop_loss=95.0)
    assert base["quantity"] == pytest.approx(2.0)  # full risk

    risk.register_closed_trade(-1.0)
    after_1 = risk.calculate_position_size(balance=1000.0, entry=100.0, stop_loss=95.0)
    assert after_1["quantity"] == pytest.approx(1.5)  # 75 %

    risk.register_closed_trade(-1.0)
    after_2 = risk.calculate_position_size(balance=1000.0, entry=100.0, stop_loss=95.0)
    assert after_2["quantity"] == pytest.approx(1.0)  # 50 % — never more risk


def test_risk_settings_apply_max_consecutive_losses():
    risk = RiskEngine()
    risk.apply_settings({"max_consecutive_losses": "2"})
    assert risk.max_consecutive_losses == 2


# --------------------------------------------------------------------------- #
# 6. Time stop                                                               #
# --------------------------------------------------------------------------- #
async def test_time_stop_closes_stale_position(tmp_path):
    db = DatabaseManager(str(tmp_path / "time_stop.db"))
    with db._get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('sim_latency_ms', '0')")
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('trailing_stop_active', 'false')")
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('max_trade_duration_minutes', '30')")

    class Universe:
        def get_market_status(self, s):
            return "OPEN"

    engine = ExecutionEngine(PortfolioEngine(db), db, RiskEngine(), Universe())
    stale_open = (datetime.now() - timedelta(hours=2)).isoformat()
    db.save_trade({"id": "T1", "mode": "DEMO", "symbol": "btc_usdt",
                   "display_symbol": "btc_usdt", "direction": "BUY",
                   "entry_price": 100.0, "quantity": 1.0, "sl": 90.0, "tp": 120.0,
                   "leverage": 1.0, "fees": 0.0, "open_time": stale_open,
                   "status": "OPEN", "pnl": 0.0, "metadata": {"atr": 2.0}})

    closed = await engine.update_active_positions("DEMO", {"btc_usdt": {"bid": 99.0}})
    assert len(closed) == 1
    assert closed[0]["metadata"]["close_reason"] == "TIME_STOP_EXIT"


async def test_time_stop_disabled_by_default(tmp_path):
    db = DatabaseManager(str(tmp_path / "no_time_stop.db"))

    class Universe:
        def get_market_status(self, s):
            return "OPEN"

    engine = ExecutionEngine(PortfolioEngine(db), db, RiskEngine(), Universe())
    stale_open = (datetime.now() - timedelta(hours=5)).isoformat()
    db.save_trade({"id": "T2", "mode": "DEMO", "symbol": "btc_usdt",
                   "display_symbol": "btc_usdt", "direction": "BUY",
                   "entry_price": 100.0, "quantity": 1.0, "sl": 90.0, "tp": 120.0,
                   "leverage": 1.0, "fees": 0.0, "open_time": stale_open,
                   "status": "OPEN", "pnl": 0.0, "metadata": {"atr": 2.0}})
    closed = await engine.update_active_positions("DEMO", {"btc_usdt": {"bid": 99.0}})
    assert closed == []  # setting absent → 0 → time stop off (backward compatible)


# --------------------------------------------------------------------------- #
# 7. Profit audit script                                                      #
# --------------------------------------------------------------------------- #
def _seed_trades(db):
    db.save_trade({"id": "A1", "mode": "DEMO", "symbol": "btc_usdt",
                   "display_symbol": "BTC/USDT", "direction": "BUY",
                   "entry_price": 100.0, "quantity": 1.0, "sl": 99.0, "tp": 102.0,
                   "leverage": 1.0, "fees": 0.1, "status": "CLOSED", "pnl": 1.8,
                   "metadata": {"strategy": "structure"}})
    db.save_trade({"id": "A2", "mode": "DEMO", "symbol": "eth_usdt",
                   "display_symbol": "ETH/USDT", "direction": "BUY",
                   "entry_price": 100.0, "quantity": 1.0, "sl": 99.0, "tp": 102.0,
                   "leverage": 1.0, "fees": 0.1, "status": "CLOSED", "pnl": -0.9,
                   "metadata": {"strategy": "structure"}})
    db.save_trade({"id": "A3", "mode": "DEMO", "symbol": "sol_usdt",
                   "display_symbol": "SOL/USDT", "direction": "SELL",
                   "entry_price": 100.0, "quantity": 1.0, "sl": 101.0, "tp": 98.0,
                   "leverage": 1.0, "fees": 0.1, "status": "CLOSED", "pnl": 0.5,
                   "metadata": {"strategy": "tape"}})
    db.save_trade({"id": "A4", "mode": "REAL", "symbol": "btc_usdt",
                   "display_symbol": "BTC/USDT", "direction": "BUY",
                   "entry_price": 100.0, "quantity": 1.0, "sl": 99.99, "tp": 100.5,
                   "leverage": 1.0, "fees": 0.0, "status": "CLOSED", "pnl": -0.2,
                   "metadata": {"strategy": "tape"}})


def test_profit_audit_stats(tmp_path):
    db = DatabaseManager(str(tmp_path / "audit.db"))
    _seed_trades(db)
    stats = analyze_db(str(tmp_path / "audit.db"))

    assert "error" not in stats
    assert stats["total_closed_trades"] == 4
    assert stats["total_net_pnl"] == pytest.approx(1.2)

    demo = stats["modes"]["DEMO"]
    assert demo["closed_trades"] == 3
    structure = demo["by_strategy"]["structure"]
    assert structure["trades"] == 2
    assert structure["wins"] == 1 and structure["losses"] == 1
    assert structure["win_rate"] == 50.0
    assert structure["net_pnl"] == pytest.approx(0.9)

    tape = demo["by_strategy"]["tape"]
    assert tape["trades"] == 1 and tape["net_pnl"] == pytest.approx(0.5)

    # The REAL trade has a 0.01 % SL → cost ratio = 20 → flagged as leak
    real_tape = stats["modes"]["REAL"]["by_strategy"]["tape"]
    assert real_tape["cost_leaks"] == 1


def test_profit_audit_empty_db(tmp_path):
    DatabaseManager(str(tmp_path / "empty_audit.db"))
    stats = analyze_db(str(tmp_path / "empty_audit.db"))
    assert stats["total_closed_trades"] == 0
    assert stats["modes"] == {}


def test_profit_audit_missing_db():
    stats = analyze_db("/nonexistent/path.db")
    assert "error" in stats
