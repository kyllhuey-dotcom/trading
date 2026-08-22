"""
LOT Q — Capital-aware profiles, small-account support & audit-driven optimization.

Covers:
- bracket resolution for 1$ / 10$ / 50$ / 100$ accounts;
- profile overrides contain the tunable parameters;
- audit-driven recommendations (no data vs. a synthetic profit audit);
- RiskEngine small-capital sizing (removal of the hard-coded $10 notional floor);
- SignalEngine ATR stop multiplier wiring into SL/TP.
"""
import pandas as pd
import pytest

from api.engines.capital_profiles import (
    BRACKETS, resolve_bracket, profile_overrides, recommend_from_audit,
)
from api.engines.risk_engine import RiskEngine
from api.engines.signal_engine import SignalEngine


# --------------------------------------------------------------------------- #
# 1. Bracket resolution                                                       #
# --------------------------------------------------------------------------- #
def test_resolve_bracket_boundaries():
    assert resolve_bracket(0.0).name == "MICRO"
    assert resolve_bracket(1.0).name == "MICRO"
    assert resolve_bracket(9.99).name == "MICRO"
    assert resolve_bracket(10.0).name == "RETAIL"
    assert resolve_bracket(49.99).name == "RETAIL"
    assert resolve_bracket(50.0).name == "STANDARD"
    assert resolve_bracket(100000.0).name == "STANDARD"
    assert resolve_bracket(-5.0).name == "MICRO"  # clamp to 0


def test_profile_overrides_keys_and_values():
    ov = profile_overrides(1.0)
    assert ov["bracket"] == "MICRO"
    for key in ("risk_pct", "max_leverage", "max_open_positions",
                "min_signal_score", "risk_reward_ratio", "atr_stop_multiplier",
                "min_trade_notional", "max_cost_ratio"):
        assert key in ov
    # Micro accounts are the most selective (highest min score, fewest positions).
    micro, standard = profile_overrides(1.0), profile_overrides(100.0)
    assert micro["min_signal_score"] >= standard["min_signal_score"]
    assert micro["max_open_positions"] <= standard["max_open_positions"]
    assert micro["max_leverage"] <= standard["max_leverage"]


def test_brackets_sorted_and_inclusive():
    assert BRACKETS[0].min_balance <= BRACKETS[1].min_balance <= BRACKETS[2].min_balance
    # every real balance falls in exactly one bracket (no gaps)
    for bal in (0.5, 5.0, 10.5, 42.0, 50.0, 5000.0):
        b = resolve_bracket(bal)
        assert b.min_balance <= bal < b.max_balance


# --------------------------------------------------------------------------- #
# 2. Audit-driven optimization                                                #
# --------------------------------------------------------------------------- #
def test_recommend_from_audit_no_data():
    rec = recommend_from_audit(None, 5.0)
    assert rec["bracket"] == "MICRO"
    assert rec["per_strategy"] == {}
    assert rec["recommended_settings"]["min_signal_score"] == 85
    assert "N/A" in rec["health_verdict"]


def test_recommend_from_audit_uses_synthetic_stats():
    stats = {
        "modes": {
            "DEMO": {
                "closed_trades": 20,
                "net_pnl": -10.0,
                "by_strategy": {
                    "structure": {
                        "trades": 20, "wins": 4, "losses": 16,
                        "win_rate": 20.0, "net_pnl": -10.0,
                        "avg_win": 2.0, "avg_loss": 1.0,
                        "realized_rr": 2.0, "expectancy_per_trade": -0.5,
                        "cost_leaks": 3, "verdict": "LOSING",
                    },
                },
            },
        },
        "total_closed_trades": 20,
        "total_net_pnl": -10.0,
    }
    rec = recommend_from_audit(stats, 50.0)
    assert rec["bracket"] == "STANDARD"
    s = rec["per_strategy"]["structure"]
    assert s["verdict"] == "LOSING"
    assert s["action"] == "DISABLE_OR_RAISE_SELECTIVITY"
    # best strategy tracks the best (only) strategy
    assert rec["best_strategy"]["name"] == "structure"
    assert rec["health_verdict"] == "UNHEALTHY"


# --------------------------------------------------------------------------- #
# 3. RiskEngine small-capital support                                          #
# --------------------------------------------------------------------------- #
def test_risk_min_account_balance():
    re = RiskEngine(min_account_balance=1.0)
    res = re.calculate_position_size(balance=0.5, entry=100, stop_loss=95)
    assert res["allowed"] is False
    assert "Balance below minimum" in res["reason"]


def test_risk_small_capital_sizing_allowed():
    # A $5 account, risk 1%, entry 100 SL 95 -> notional = 1.0.
    # With the new default min_trade_notional the order is NOT blocked at $10.
    re = RiskEngine(min_account_balance=1.0, min_trade_notional=1.0)
    res = re.calculate_position_size(balance=5.0, entry=100.0, stop_loss=95.0)
    assert res["allowed"] is True
    assert res["quantity"] == pytest.approx(0.01, abs=1e-9)
    assert res["estimated_fees"] >= 0.0


def test_risk_min_trade_notional_blocks_tiny_notional():
    # With a stricter min_trade_notional the same $5 order is rejected.
    re = RiskEngine(min_account_balance=1.0, min_trade_notional=10.0)
    res = re.calculate_position_size(balance=5.0, entry=100.0, stop_loss=95.0)
    assert res["allowed"] is False
    assert res["reason"] == "Order size too small"


def test_risk_apply_settings_min_floors():
    re = RiskEngine(min_account_balance=5.0, min_trade_notional=20.0)
    re.apply_settings({"min_account_balance": "0.5", "min_trade_notional": "1.0"})
    assert re.min_account_balance == 0.5
    assert re.min_trade_notional == 1.0
    # and the lower floor now accepts the $5 order
    res = re.calculate_position_size(balance=5.0, entry=100.0, stop_loss=95.0)
    assert res["allowed"] is True


# --------------------------------------------------------------------------- #
# 4. SignalEngine ATR stop multiplier                                          #
# --------------------------------------------------------------------------- #
def _valid_analysis():
    return {
        "status": "VALID", "trend": "BULLISH", "htf_bias": "BULLISH",
        "is_hh": True, "is_hl": True, "momentum": 1.0, "bos": True,
        "last_high": 110.0, "last_low": 90.0, "market_id": "btc_usdt",
    }


def _df():
    # High 110 / Low 90 / Close 100, rising volume -> score 100, ATR = 20.
    return pd.DataFrame({
        "High": [110.0] * 20,
        "Low": [90.0] * 20,
        "Close": [100.0] * 20,
        "Volume": [1000.0] * 19 + [2000.0],
    })


def test_signal_atr_stop_multiplier_widens_stop():
    engine = SignalEngine(min_score=80, risk_reward=2.0)
    res = engine.generate_signal(_valid_analysis(), {"trading_allowed": True}, _df())
    assert res["status"] == "SIGNAL_DETECTED"
    atr = res["atr"]

    engine_wide = SignalEngine(min_score=80, risk_reward=2.0, atr_stop_multiplier=3.0)
    res_wide = engine_wide.generate_signal(_valid_analysis(), {"trading_allowed": True}, _df())
    assert res_wide["atr"] == pytest.approx(atr)
    # Larger ATR multiplier = further stop (more distance to the trade).
    assert (res_wide["entry"] - res_wide["sl"]) > (res["entry"] - res["sl"])
