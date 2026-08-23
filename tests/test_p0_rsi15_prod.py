"""P0/P1 production fixes: RSI-only RR 1.5, timestamp LIVE, costs, correlation."""
import time

from api.engines.cost_calculator import compute_trade_costs, recompute_after_fill
from api.engines.data_engine import DataEngine
from api.engines.opportunity_ranker import _passes_all_gates
from api.engines.opportunity_tracker import IN_FLIGHT_TTL_S, OpportunityTracker
from api.engines.quarantine import QuarantineManager
from api.engines.risk_engine import RiskEngine
from api.engines.settings_schema import validate_settings
import api.index as idx


def test_is_quote_realtime_uses_timestamp_not_provider_name():
    engine = DataEngine()
    now = int(time.time() * 1000)
    live = {
        "status": "LIVE",
        "timestamp": now - 5_000,
        "source": "Binance",
        "last": 1.0,
    }
    assert engine.is_quote_realtime("btc_usdt", live) is True
    stale = dict(live, timestamp=now - 45_000)
    assert engine.is_quote_realtime("btc_usdt", stale) is False
    yahoo = dict(live, source="Yahoo Finance")
    assert engine.is_quote_realtime("btc_usdt", yahoo) is False
    delayed = dict(live, status="DELAYED")
    assert engine.is_quote_realtime("btc_usdt", delayed) is False


def test_recompute_after_fill_uses_tp_dist():
    ok = recompute_after_fill(100.1, 95, 110, direction="BUY")
    assert ok["action"] == "OK"
    refuse = recompute_after_fill(111, 95, 110, direction="BUY")
    assert refuse["action"] == "REFUSE"


def test_ranker_gates_use_real_fee():
    cand = {
        "score": 90,
        "tradable": True,
        "status": "LIVE",
        "data_age_ms": 10,
        "spread": 0.01,
        "symbol": "btc_usdt",
        "signal_data": {
            "status": "SIGNAL_DETECTED", "entry": 100, "sl": 95, "tp": 108,
            "strategy": "rsi",
        },
        "diagnosis": {"checks": {
            "NEWS_CLEAR": "PASS", "SESSION_ALLOWED": "PASS", "DAY_ALLOWED": "PASS",
            "MARKET_OPEN": "PASS", "LIQUIDITY_VALID": "PASS",
        }},
    }
    cheap = _passes_all_gates(cand, set(), fee_pct=0.05, slippage_pct=0.05)
    expensive = _passes_all_gates(cand, set(), fee_pct=5.0, slippage_pct=5.0)
    assert cheap["passes"] is True
    assert expensive["passes"] is False


def test_correlation_uses_underlying_not_split():
    risk = RiskEngine()
    risk.universe = type("U", (), {
        "get_info": staticmethod(lambda s: {
            "btc_usdt": {"underlying": "btc"},
            "btc_eur": {"underlying": "btc"},
            "eth_usdt": {"underlying": "eth"},
        }.get(s, {})),
    })()
    active = [{"symbol": "btc_usdt", "underlying": "btc"}]
    assert risk.check_correlation("btc_eur", active)["allowed"] is False
    assert risk.check_correlation("eth_usdt", active)["allowed"] is True


def test_scan_timeout_is_120():
    assert idx.SCAN_ALL_TIMEOUT_S == 120.0
    assert idx.SCAN_LOCK_STALE_S == 90.0


def test_compute_trade_costs_bid_ask():
    no = compute_trade_costs(100, 95, 110, fee_pct=0.05, slippage_pct=0.05)
    yes = compute_trade_costs(100, 95, 110, fee_pct=0.05, slippage_pct=0.05,
                              bid=99.5, ask=100.5)
    assert yes["spread_cost"] == 1.0
    assert yes["round_trip_cost"] > no["round_trip_cost"]


def test_market_tuning_json_validation():
    cleaned, errors = validate_settings({"market_tuning": "{not json"})
    assert cleaned["market_tuning"] == "{}"
    assert errors
    ok, err = validate_settings({"market_tuning": '{"btc_usdt": {"min_score": 90}}'})
    assert err == []
    assert "btc_usdt" in ok["market_tuning"]


def test_risk_reward_settings_clamped_1_2():
    cleaned, _ = validate_settings({"risk_reward_ratio": "9"})
    assert float(cleaned["risk_reward_ratio"]) == 2.0
    cleaned, _ = validate_settings({"risk_reward_ratio": "0.2"})
    assert float(cleaned["risk_reward_ratio"]) == 1.0


def test_quarantine_tracks_gross_win_loss():
    q = QuarantineManager(min_trades=2)
    q.record_trade("btc", "rsi", 10.0, 5.0)
    q.record_trade("btc", "rsi", -4.0, 5.0)
    perf = q.performance[("btc", "rsi")]
    assert perf["gross_win"] == 10.0
    assert perf["gross_loss"] == 4.0


def test_in_flight_ttl_is_120():
    assert IN_FLIGHT_TTL_S == 120.0
    tracker = OpportunityTracker(ttl_s=30.0)
    now = time.time()
    tracker._in_flight["x"] = now - 121
    tracker._prune(now)
    assert "x" not in tracker._in_flight
