"""
LOT Q — Capital-aware profiles, small-account support & audit-driven optimization.

Covers:
- bracket resolution for 1$ / 10$ / 50$ / 100$ accounts;
- profile overrides contain the tunable parameters;
- audit-driven recommendations (no data vs. a synthetic profit audit);
- RiskEngine small-capital sizing (removal of the hard-coded $10 notional floor);
- SignalEngine ATR stop multiplier wiring into SL/TP.
"""
import json
import pandas as pd
import pytest

from api.engines.capital_profiles import (
    BRACKETS, resolve_bracket, profile_overrides, bracket_summary,
    _expectancy_pct, recommend_from_audit, HEALTH_TARGETS,
)
from api.engines.risk_engine import RiskEngine
from api.engines.signal_engine import SignalEngine
from api.engines.settings_schema import validate_settings, SETTINGS_SPEC


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


# --------------------------------------------------------------------------- #
# 5. resolve_bracket — None / negative edge cases                              #
# --------------------------------------------------------------------------- #
def test_resolve_bracket_none_and_negative():
    assert resolve_bracket(None).name == "MICRO"
    assert resolve_bracket(-1.0).name == "MICRO"
    assert resolve_bracket(-1_000_000.0).name == "MICRO"


# --------------------------------------------------------------------------- #
# 6. bracket_summary                                                           #
# --------------------------------------------------------------------------- #
def test_bracket_summary_matches_brackets():
    summary = bracket_summary()
    assert len(summary) == len(BRACKETS) == 3
    assert [b["name"] for b in summary] == ["MICRO", "RETAIL", "STANDARD"]
    # asdict must mirror the dataclass fields 1:1
    for b, s in zip(BRACKETS, summary):
        assert s["name"] == b.name
        assert s["min_balance"] == b.min_balance
        assert s["max_balance"] == b.max_balance
        assert s["atr_stop_multiplier"] == b.atr_stop_multiplier
        assert s["min_trade_notional"] == b.min_trade_notional


# --------------------------------------------------------------------------- #
# 7. _expectancy_pct                                                           #
# --------------------------------------------------------------------------- #
def test_expectancy_pct_formula():
    # E = win_rate% × avg_win − loss_rate% × |avg_loss|
    assert _expectancy_pct(50.0, 2.0, 1.0) == pytest.approx(0.5)
    assert _expectancy_pct(100.0, 1.0, 1.0) == pytest.approx(1.0)
    assert _expectancy_pct(0.0, 1.0, 1.0) == pytest.approx(-1.0)
    assert _expectancy_pct(40.0, 3.0, 1.0) == pytest.approx(0.6)
    # no win and no loss data → 0 (guard against division-by-zero ambiguity)
    assert _expectancy_pct(50.0, 0.0, 0.0) == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# 8. recommend_from_audit — every optimization branch                          #
# --------------------------------------------------------------------------- #
def _audit(pnl, win_rate, avg_win, avg_loss, cost_leaks=0, trades=10, wins=None,
           modes=("DEMO",)):
    """Build a synthetic profit-audit payload for one strategy across modes."""
    wins = int(round(trades * win_rate / 100)) if wins is None else wins
    by_strategy = {
        "structure": {
            "trades": trades, "wins": wins, "losses": trades - wins,
            "win_rate": win_rate, "net_pnl": pnl,
            "avg_win": avg_win, "avg_loss": avg_loss,
            "realized_rr": (avg_win / avg_loss) if avg_loss > 0 else None,
            "expectancy_per_trade": pnl / trades if trades else 0.0,
            "cost_leaks": cost_leaks, "verdict": "?",
        },
    }
    return {
        "modes": {m: {"closed_trades": trades, "net_pnl": pnl,
                      "by_strategy": by_strategy} for m in modes},
        "total_closed_trades": trades * len(modes),
        "total_net_pnl": pnl * len(modes),
    }


def test_recommend_from_audit_widen_take_profit():
    # Win rate healthy, PnL positive, but realized RR too low → widen TP.
    rec = recommend_from_audit(_audit(pnl=1.0, win_rate=50.0, avg_win=1.0,
                                      avg_loss=1.0, cost_leaks=0), 50.0)
    s = rec["per_strategy"]["structure"]
    assert s["verdict"] == "PROFITABLE"
    assert s["action"] == "WIDEN_TAKE_PROFIT"


def test_recommend_from_audit_tighten_cost_filter():
    # Healthy RR, but trades with fee leaks → tighten the cost filter.
    rec = recommend_from_audit(_audit(pnl=1.0, win_rate=60.0, avg_win=2.0,
                                      avg_loss=1.0, cost_leaks=2), 50.0)
    s = rec["per_strategy"]["structure"]
    assert s["action"] == "TIGHTEN_COST_FILTER"


def test_recommend_from_audit_keep():
    # Fully healthy strategy → keep and scale.
    rec = recommend_from_audit(_audit(pnl=1.0, win_rate=60.0, avg_win=2.0,
                                      avg_loss=1.0, cost_leaks=0), 50.0)
    s = rec["per_strategy"]["structure"]
    assert s["action"] == "KEEP"
    assert s["expectancy"] > 0
    assert rec["health_verdict"] == "HEALTHY"


def test_recommend_from_audit_review():
    # Breakeven, no leaks, no RR data, non-positive expectancy → REVIEW.
    rec = recommend_from_audit(_audit(pnl=0.0, win_rate=50.0, avg_win=0.0,
                                      avg_loss=0.0, cost_leaks=0), 50.0)
    s = rec["per_strategy"]["structure"]
    assert s["verdict"] == "BREAKEVEN"
    assert s["action"] == "REVIEW"


def test_recommend_from_audit_aggregates_multi_modes():
    # Same strategy present in two modes: trades/PnL are summed, best tracked.
    rec = recommend_from_audit(
        _audit(pnl=1.0, win_rate=60.0, avg_win=2.0, avg_loss=1.0,
               cost_leaks=0, trades=10, modes=("DEMO", "REAL")), 100.0)
    s = rec["per_strategy"]["structure"]
    assert s["trades"] == 20
    assert s["net_pnl"] == pytest.approx(2.0)
    assert rec["best_strategy"]["name"] == "structure"
    assert rec["health_verdict"] == "HEALTHY"


def test_recommend_from_audit_needs_tailoring():
    # Worst win rate in [35, 45) → NEEDS_TAILORING.
    rec = recommend_from_audit(_audit(pnl=-1.0, win_rate=40.0, avg_win=1.0,
                                      avg_loss=1.0, cost_leaks=0), 50.0)
    assert rec["health_verdict"] == "NEEDS_TAILORING"


# --------------------------------------------------------------------------- #
# 9. settings_schema — new fields, clamp, enum                                 #
# --------------------------------------------------------------------------- #
def test_settings_schema_has_lot_q_fields():
    for key in ("min_account_balance", "min_trade_notional",
                "atr_stop_multiplier", "capital_profile_mode"):
        assert key in SETTINGS_SPEC


def test_settings_schema_clamps_min_max():
    cleaned, errors = validate_settings({"min_account_balance": "0.001",
                                         "max_leverage": "999"})
    assert cleaned["min_account_balance"] == "0.5"   # clamped to min
    assert cleaned["max_leverage"] == "100"          # clamped to max
    assert any("clamped" in e for e in errors)


def test_settings_schema_enum_generic_message():
    cleaned, errors = validate_settings({"capital_profile_mode": "bogus"})
    assert cleaned["capital_profile_mode"] == "manual"
    joined = " ".join(errors)
    assert "invalid language" not in joined        # misleading wording gone
    assert "manual" in joined and "auto" in joined  # valid choices listed


# --------------------------------------------------------------------------- #
# 10. set_atr_stop_multiplier — bounds & rejection                             #
# --------------------------------------------------------------------------- #
def test_set_atr_stop_multiplier_bounds():
    engine = SignalEngine()
    engine.set_atr_stop_multiplier(0.1)
    assert engine.atr_stop_multiplier == pytest.approx(0.1)
    engine.set_atr_stop_multiplier(10.0)
    assert engine.atr_stop_multiplier == pytest.approx(10.0)
    engine.set_atr_stop_multiplier(2.5)
    assert engine.atr_stop_multiplier == pytest.approx(2.5)


def test_set_atr_stop_multiplier_rejects_out_of_range():
    engine = SignalEngine()
    assert engine.atr_stop_multiplier == pytest.approx(1.5)  # default
    engine.set_atr_stop_multiplier(0.05)   # below lower bound
    assert engine.atr_stop_multiplier == pytest.approx(1.5)
    engine.set_atr_stop_multiplier(10.5)   # above upper bound
    assert engine.atr_stop_multiplier == pytest.approx(1.5)
    engine.set_atr_stop_multiplier("abc")  # non-numeric
    assert engine.atr_stop_multiplier == pytest.approx(1.5)
    engine.set_atr_stop_multiplier(None)   # None
    assert engine.atr_stop_multiplier == pytest.approx(1.5)
