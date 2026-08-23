"""
v2.7 comprehensive tests for all P0 and P1 changes (v2.8: floor raised to 84).

Tests:
- P0-1: Score floor 84 inviolable (every path)
- P0-2: Opportunity ranker (single best selection)
- P0-3: Idempotence / single-flight / TTL
- P0-4: Cost and net RR calculations
- P0-5: Arbitrage not auto-tradable
- P0-6: Trade accounting
- P1-7: Quarantine (min 30 trades)
- P1-9: Broker capabilities
"""
import time

import pytest

from api.engines.capital_profiles import BRACKETS
from api.engines.constants import AUTO_EXECUTION_SCORE_FLOOR
from api.engines.cost_calculator import (
    compute_trade_costs,
    costs_pass_gate,
    recompute_after_fill,
)
from api.engines.institutional_executor import describe_intent, select_candidates
from api.engines.market_tuning import (
    BOUNDS,
    MIN_TRADES_FOR_VERDICT,
    recommend_for_market,
)
from api.engines.opportunity_ranker import rank_opportunities
from api.engines.opportunity_tracker import OpportunityTracker
from api.engines.settings_schema import SETTINGS_SPEC, validate_settings
from api.engines.signal_engine import SignalEngine
from api.engines.strategies.micro_arbitrage import MicroArbitrageStrategy


# ============================================================================
# P0-1: SCORE FLOOR 80 INVIOLABLE
# ============================================================================
class TestScoreFloorInviolable:
    """Score 83 must be refused everywhere, 84 accepted only with all gates."""

    def test_constant_is_84(self):
        assert AUTO_EXECUTION_SCORE_FLOOR == 84

    def test_signal_engine_set_min_score_clamped_to_84(self):
        engine = SignalEngine(min_score=84)
        engine.set_min_score(50)
        assert engine.min_score == 84
        engine.set_min_score(83)
        assert engine.min_score == 84
        engine.set_min_score(84)
        assert engine.min_score == 84
        engine.set_min_score(85)
        assert engine.min_score == 85
        engine.set_min_score(99)
        assert engine.min_score == 99

    def test_effective_min_score_never_below_84(self):
        engine = SignalEngine(min_score=84)
        engine.set_market_tuning({"test": {"min_score": 50}})
        assert engine.effective_min_score("test") >= 84
        engine.set_market_tuning({"test": {"min_score": 83}})
        assert engine.effective_min_score("test") >= 84
        # Quiet regime should not lower below 84
        engine.set_market_tuning({"test": {"min_score": 84}})
        assert engine.effective_min_score("test", "QUIET") >= 84

    def test_settings_schema_min_signal_score_bounds(self):
        spec = SETTINGS_SPEC["min_signal_score"]
        assert spec["min"] == 84
        assert spec["max"] == 99
        # Validate: 77 should be clamped to 84
        cleaned, _errors = validate_settings({"min_signal_score": "77"})
        assert int(cleaned["min_signal_score"]) == 84
        # Validate: 83 should be clamped to 84
        cleaned, _errors = validate_settings({"min_signal_score": "83"})
        assert int(cleaned["min_signal_score"]) == 84
        # Validate: 85 should pass
        cleaned, _errors = validate_settings({"min_signal_score": "85"})
        assert int(cleaned["min_signal_score"]) == 85

    def test_all_capital_brackets_at_least_84(self):
        for bracket in BRACKETS:
            assert bracket.min_score >= 84, f"{bracket.name} min_score {bracket.min_score} < 84"

    def test_standard_profile_min_score_at_least_84(self):
        standard = next(b for b in BRACKETS if b.name == "STANDARD")
        assert standard.min_score >= 84

    def test_market_tuning_bounds_min_score_at_least_84(self):
        lo, hi = BOUNDS["min_score"]
        assert lo >= 84
        assert hi == 99

    def test_select_candidates_enforces_floor(self):
        results = [
            {"symbol": "a", "score": 83, "tradable": True,
             "signal_data": {"market_id": "a", "entry": 100, "status": "SIGNAL_DETECTED"}},
            {"symbol": "b", "score": 84, "tradable": True,
             "signal_data": {"market_id": "b", "entry": 100, "status": "SIGNAL_DETECTED"}},
        ]
        cands = select_candidates(results, 50, set(), 10)  # malicious min_score=50
        assert len(cands) == 1
        assert cands[0]["symbol"] == "b"

    def test_execute_signal_refuses_83(self):
        """Score 83 must be refused even with malicious config at 50."""
        results = [
            {"symbol": "a", "score": 83, "tradable": True,
             "signal_data": {"market_id": "a", "entry": 100, "status": "SIGNAL_DETECTED"}},
        ]
        cands = select_candidates(results, 50, set(), 10)
        assert len(cands) == 0

    def test_min_trades_for_strong_verdict_at_least_30(self):
        assert MIN_TRADES_FOR_VERDICT >= 30


# ============================================================================
# P0-2: OPPORTUNITY RANKER
# ============================================================================
class TestOpportunityRanker:
    """Single best opportunity selection."""

    def _make_candidate(self, symbol, score, tradable=True, signal_status="SIGNAL_DETECTED",
                        entry=100, sl=95, tp=110, spread=0.01, volume=1_000_000,
                        data_age_ms=10, realtime=True):
        return {
            "symbol": symbol,
            "asset_class": "CRYPTO",
            "status": "LIVE",
            "score": score,
            "tradable": tradable,
            "spread": spread,
            "volume": volume,
            "data_age_ms": data_age_ms,
            "realtime_source": realtime,
            "signal_data": {
                "market_id": symbol,
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "direction": "BUY",
                "strategy": "structure",
                "status": signal_status,
                "regime": "NORMAL",
            },
            "diagnosis": {
                "checks": {
                    "NEWS_CLEAR": "PASS",
                    "SESSION_ALLOWED": "PASS",
                    "DAY_ALLOWED": "PASS",
                    "MARKET_OPEN": "PASS",
                    "LIQUIDITY_VALID": "PASS",
                }
            },
        }

    def test_primary_opportunity_selected(self):
        results = [
            self._make_candidate("a", 85),
            self._make_candidate("b", 90),
            self._make_candidate("c", 84),
        ]
        ranking = rank_opportunities(results)
        assert ranking["primary_opportunity"] is not None
        assert ranking["primary_opportunity"]["symbol"] == "b"
        assert ranking["total_passing"] == 3

    def test_score_below_84_excluded(self):
        results = [
            self._make_candidate("a", 83),
            self._make_candidate("b", 84),
        ]
        ranking = rank_opportunities(results)
        assert ranking["total_passing"] == 1
        assert ranking["primary_opportunity"]["symbol"] == "b"

    def test_no_signal_detected_excluded(self):
        results = [
            self._make_candidate("a", 90, signal_status="NO_TRADE"),
            self._make_candidate("b", 85),
        ]
        ranking = rank_opportunities(results)
        assert ranking["total_passing"] == 1
        assert ranking["primary_opportunity"]["symbol"] == "b"

    def test_not_tradable_excluded(self):
        results = [
            self._make_candidate("a", 90, tradable=False),
            self._make_candidate("b", 85),
        ]
        ranking = rank_opportunities(results)
        assert ranking["total_passing"] == 1
        assert ranking["primary_opportunity"]["symbol"] == "b"

    def test_position_already_open_excluded(self):
        results = [
            self._make_candidate("a", 90),
            self._make_candidate("b", 85),
        ]
        ranking = rank_opportunities(results, active_symbols={"a"})
        assert ranking["total_passing"] == 1
        assert ranking["primary_opportunity"]["symbol"] == "b"

    def test_opportunity_id_stable_within_cycle(self):
        cycle_ts = time.time()
        results = [self._make_candidate("a", 90)]
        r1 = rank_opportunities(results, cycle_ts=cycle_ts)
        r2 = rank_opportunities(results, cycle_ts=cycle_ts)
        assert r1["primary_opportunity"]["opportunity_id"] == r2["primary_opportunity"]["opportunity_id"]

    def test_opportunity_has_expires_at(self):
        results = [self._make_candidate("a", 90)]
        ranking = rank_opportunities(results)
        assert "expires_at" in ranking["primary_opportunity"]
        assert ranking["primary_opportunity"]["expires_at"] > time.time()

    def test_secondary_opportunities(self):
        results = [
            self._make_candidate("a", 90),
            self._make_candidate("b", 85),
            self._make_candidate("c", 84),
        ]
        ranking = rank_opportunities(results)
        assert len(ranking["secondary_opportunities"]) == 2

    def test_max_new_positions_per_scan_default_3(self):
        # v2.8: simultaneous executions are the default (up to 3 per scan)
        results = [self._make_candidate("a", 90)]
        ranking = rank_opportunities(results)
        assert ranking["max_new_positions_per_scan"] == 3

    def test_max_new_positions_bounded(self):
        results = [self._make_candidate("a", 90)]
        r0 = rank_opportunities(results, max_new_positions=0)
        assert r0["max_new_positions_per_scan"] == 1
        r5 = rank_opportunities(results, max_new_positions=5)
        assert r5["max_new_positions_per_scan"] == 3


# ============================================================================
# P0-3: IDEMPOTENCE / SINGLE-FLIGHT / TTL
# ============================================================================
class TestOpportunityTracker:
    """Single-flight and idempotence protection."""

    def test_acquire_succeeds_first_time(self):
        tracker = OpportunityTracker()
        result = tracker.try_acquire("opp-1")
        assert result["allowed"] is True

    def test_double_acquire_fails(self):
        tracker = OpportunityTracker()
        tracker.try_acquire("opp-1")
        result = tracker.try_acquire("opp-1")
        assert result["allowed"] is False
        assert result["reason"] == "IN_FLIGHT"

    def test_executed_cannot_be_reacquired(self):
        tracker = OpportunityTracker()
        tracker.try_acquire("opp-1")
        tracker.mark_executed("opp-1")
        result = tracker.try_acquire("opp-1")
        assert result["allowed"] is False
        assert result["reason"] == "ALREADY_EXECUTED"

    def test_expired_opportunity(self):
        tracker = OpportunityTracker(ttl_s=1.0)
        now = time.time()
        assert tracker.is_expired(now - 1) is True
        assert tracker.is_expired(now + 10) is False

    def test_mark_failed_releases_lock(self):
        tracker = OpportunityTracker()
        tracker.try_acquire("opp-1")
        tracker.mark_failed("opp-1", "TEST")
        # After failure, the opportunity is marked as executed (failed)
        result = tracker.try_acquire("opp-1")
        assert result["allowed"] is False
        assert result["reason"] == "ALREADY_EXECUTED"

    def test_empty_id_rejected(self):
        tracker = OpportunityTracker()
        result = tracker.try_acquire("")
        assert result["allowed"] is False

    def test_stats(self):
        tracker = OpportunityTracker()
        tracker.try_acquire("opp-1")
        tracker.mark_executed("opp-1")
        stats = tracker.get_stats()
        assert stats["executed_count"] == 1
        assert stats["in_flight_count"] == 0

    def test_reset(self):
        tracker = OpportunityTracker()
        tracker.try_acquire("opp-1")
        tracker.mark_executed("opp-1")
        tracker.reset()
        assert tracker.get_stats()["executed_count"] == 0


# ============================================================================
# P0-4: COSTS AND NET RR CORRECT
# ============================================================================
class TestCostCalculator:
    """Correct cost and net RR calculations."""

    def test_basic_cost_calcation(self):
        costs = compute_trade_costs(entry=100, sl=95, tp=110, fee_pct=0.05, slippage_pct=0.05)
        assert costs["valid"] is True
        assert costs["risk_distance"] == 5.0
        assert costs["gross_rr"] == 2.0
        assert costs["net_rr"] < costs["gross_rr"]
        assert costs["round_trip_cost"] > 0

    def test_net_rr_gate_passes(self):
        costs = compute_trade_costs(entry=100, sl=95, tp=110, fee_pct=0.05, slippage_pct=0.05)
        gate = costs_pass_gate(costs)
        assert gate["allowed"] is True

    def test_net_rr_gate_fails_when_costs_eat_edge(self):
        # Very tight TP with high fees
        costs = compute_trade_costs(entry=100, sl=95, tp=101, fee_pct=5.0, slippage_pct=5.0)
        gate = costs_pass_gate(costs)
        assert gate["allowed"] is False

    def test_cost_to_risk_gate(self):
        costs = compute_trade_costs(entry=100, sl=99.9, tp=110, fee_pct=5.0, slippage_pct=5.0)
        gate = costs_pass_gate(costs, max_cost_to_risk=0.5)
        assert gate["allowed"] is False

    def test_btc_fixture(self):
        """BTC at $60000, SL $59000, TP $63000."""
        costs = compute_trade_costs(entry=60000, sl=59000, tp=63000,
                                     fee_pct=0.04, slippage_pct=0.02)
        assert costs["valid"] is True
        assert costs["risk_distance"] == 1000.0
        assert costs["gross_rr"] == 3.0
        assert costs["net_rr"] > 1.5

    def test_small_token_fixture(self):
        """Token at $0.001, SL $0.0009, TP $0.0013."""
        costs = compute_trade_costs(entry=0.001, sl=0.0009, tp=0.0013,
                                     fee_pct=0.1, slippage_pct=0.1)
        assert costs["valid"] is True
        assert costs["risk_distance"] == pytest.approx(0.0001)
        assert costs["gross_rr"] == pytest.approx(3.0)

    def test_forex_fixture(self):
        """EUR/USD at 1.1000, SL 1.0950, TP 1.1150."""
        costs = compute_trade_costs(entry=1.1000, sl=1.0950, tp=1.1150,
                                     fee_pct=0.01, slippage_pct=0.01)
        assert costs["valid"] is True
        assert costs["risk_distance"] == pytest.approx(0.005)
        assert costs["gross_rr"] == pytest.approx(3.0)

    def test_stock_fixture(self):
        """Stock at $150, SL $145, TP $165."""
        costs = compute_trade_costs(entry=150, sl=145, tp=165,
                                     fee_pct=0.05, slippage_pct=0.02)
        assert costs["valid"] is True
        assert costs["risk_distance"] == 5.0
        assert costs["gross_rr"] == 3.0

    def test_recompute_after_fill_ok(self):
        result = recompute_after_fill(fill_price=100.1, original_sl=95, original_tp=110,
                                       fee_pct=0.05, slippage_pct=0.05, direction="BUY")
        assert result["action"] == "OK"

    def test_recompute_after_fill_degraded(self):
        # Fill much worse, TP distance shrinks
        result = recompute_after_fill(fill_price=109, original_sl=95, original_tp=110,
                                       fee_pct=0.05, slippage_pct=0.05, direction="BUY")
        assert result["action"] in ("DEGRADED", "REFUSE")

    def test_invalid_entry(self):
        costs = compute_trade_costs(entry=0, sl=95, tp=110)
        assert costs["valid"] is False

    def test_zero_risk_distance(self):
        costs = compute_trade_costs(entry=100, sl=100, tp=110)
        assert costs["valid"] is False

    def test_spread_included(self):
        costs_no_spread = compute_trade_costs(entry=100, sl=95, tp=110, fee_pct=0.05, slippage_pct=0.05)
        costs_with_spread = compute_trade_costs(entry=100, sl=95, tp=110, fee_pct=0.05, slippage_pct=0.05, spread=0.5)
        assert costs_with_spread["round_trip_cost"] > costs_no_spread["round_trip_cost"]
        assert costs_with_spread["net_rr"] < costs_no_spread["net_rr"]


# ============================================================================
# P0-5: ARBITRAGE NOT AUTO-TRADABLE
# ============================================================================
class TestArbitrageNotTradable:
    """Micro-arbitrage must not be auto-executable."""

    def test_arbitrage_signal_has_tradable_false(self):
        strategy = MicroArbitrageStrategy()
        cross_quotes = [
            {"provider": "binance", "last": 100.0, "timestamp": time.time() * 1000},
            {"provider": "bybit", "last": 101.0, "timestamp": time.time() * 1000},
        ]
        import pandas as pd
        df = pd.DataFrame()
        result = strategy.generate_signal("btc_usdt", df, cross_quotes=cross_quotes)
        if result.get("status") == "SIGNAL_DETECTED":
            assert result.get("tradable") is False
            assert result.get("main_reason") == "ARBITRAGE_REQUIRES_ATOMIC_TWO_LEG_EXECUTOR"

    def test_no_profitability_claims_in_docstring(self):
        doc = MicroArbitrageStrategy.__doc__ or ""
        assert "80-90" not in doc
        assert "75-85" not in doc


# ============================================================================
# P1-7: QUARANTINE STATISTICAL
# ============================================================================
class TestQuarantine:
    """Minimum 30 trades before verdict."""

    def test_min_trades_for_verdict_is_30(self):
        assert MIN_TRADES_FOR_VERDICT >= 30

    def test_insufficient_data_returns_observe(self):
        stats = {"trades": 5, "wins": 3, "net_pnl": 10.0, "win_rate": 60.0}
        rec = recommend_for_market("test", stats, balance=100.0)
        assert rec["verdict"] == "INSUFFICIENT_DATA"
        assert rec["action"] == "OBSERVE"

    def test_sufficient_data_can_get_verdict(self):
        stats = {"trades": 35, "wins": 20, "losses": 15, "net_pnl": 50.0,
                 "win_rate": 57.1, "avg_win": 10.0, "avg_loss": 5.0,
                 "realized_rr": 2.0, "expectancy_per_trade": 1.43, "cost_leaks": 0}
        rec = recommend_for_market("test", stats, balance=100.0)
        assert rec["verdict"] != "INSUFFICIENT_DATA"


# ============================================================================
# Integration: Score floor in all paths
# ============================================================================
class TestScoreFloorIntegration:
    """End-to-end tests ensuring 83 is never executed."""

    def test_describe_intent_shows_floor(self):
        intent = describe_intent(True, True, 0, 0, 10, 50)  # malicious min_score=50
        assert "84" in intent["message"]

    def test_select_candidates_with_malicious_config(self):
        """Even with min_score=50, candidates below 84 are excluded."""
        results = [
            {"symbol": "a", "score": 50, "tradable": True,
             "signal_data": {"market_id": "a", "entry": 100, "status": "SIGNAL_DETECTED"}},
            {"symbol": "b", "score": 83, "tradable": True,
             "signal_data": {"market_id": "b", "entry": 100, "status": "SIGNAL_DETECTED"}},
            {"symbol": "c", "score": 84, "tradable": True,
             "signal_data": {"market_id": "c", "entry": 100, "status": "SIGNAL_DETECTED"}},
        ]
        cands = select_candidates(results, 50, set(), 10)
        assert len(cands) == 1
        assert cands[0]["symbol"] == "c"
