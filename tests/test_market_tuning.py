"""
LOT R — per-market tuning, regime adaptation & capital feasibility tests.

Covers the audit methodology requested by the user:
1. per-market / per-asset-class parameter baselines;
2. markets that work with different capital levels (1 $ → 50 $+);
3. regime-based risk adaptation (conservative in volatile markets);
4. audit-driven per-market optimization (entry threshold / SL / TP);
5. SignalEngine integration (effective thresholds, per-market SL/TP);
6. settings wiring (regime_adaptation_enabled, market_tuning JSON).
"""
import json

import pandas as pd
import pytest

from api.engines import market_tuning as mt
from api.engines.market_universe import MarketUniverse
from api.engines.signal_engine import SignalEngine
from api.engines.settings_schema import validate_settings, ensure_defaults


# --------------------------------------------------------------------------- #
# 1. Asset-class baselines & default tuning                                    #
# --------------------------------------------------------------------------- #
def test_every_asset_class_has_a_tuning():
    for klass in MarketUniverse.ASSET_CLASSES:
        assert klass in mt.ASSET_CLASS_TUNING
        t = mt.ASSET_CLASS_TUNING[klass]
        lo, hi = mt.BOUNDS["min_score"]
        assert lo <= t["min_score"] <= hi
        assert 0.5 <= t["risk_reward"] <= 10.0
        assert 0.1 <= t["atr_stop_multiplier"] <= 10.0


def test_build_default_tuning_covers_the_whole_universe():
    universe = MarketUniverse()
    tuning = mt.build_default_tuning(universe)
    ids = universe.get_all_ids()
    assert len(tuning) == len(ids)
    # crypto and forex must NOT share the same baseline (different natures)
    assert tuning["btc_usdt"]["risk_reward"] != tuning["eur_usd"]["risk_reward"]
    assert tuning["eur_usd"]["min_score"] > tuning["btc_usdt"]["min_score"]


def test_resolve_market_tuning_clamps_and_ignores_unknown():
    tuning = {"btc_usdt": {"min_score": 250, "risk_reward": "oops"}}
    resolved = mt.resolve_market_tuning("btc_usdt", tuning)
    assert resolved["min_score"] == 99          # clamped to BOUNDS
    assert "risk_reward" not in resolved        # invalid value dropped
    assert mt.resolve_market_tuning("unknown", tuning) == {}
    assert mt.resolve_market_tuning(None, tuning) == {}


# --------------------------------------------------------------------------- #
# 2. Capital feasibility (which markets work at which capital level)          #
# --------------------------------------------------------------------------- #
def test_min_capital_for_uses_notional_over_leverage():
    # min_order 10 $ @ 10x with 1.2 safety margin -> 1.2 $
    assert mt.min_capital_for({"min_order": 10.0, "leverage_max": 100}, leverage_cap=10) == pytest.approx(1.2)
    # leverage capped by the instrument itself
    assert mt.min_capital_for({"min_order": 10.0, "leverage_max": 2}, leverage_cap=10) == pytest.approx(6.0)


def test_feasibility_by_capital_tier():
    universe = MarketUniverse()
    # 1 $ account: crypto micro notional fits, forex micro lot (1000 $) does not
    f1 = mt.markets_feasible_for_capital(1.0, universe)
    assert f1["asset_classes"]["CRYPTO"]["class_feasible"] is True
    assert f1["asset_classes"]["FOREX"]["class_feasible"] is False
    # 50 $+ account: more classes become reachable
    f50 = mt.markets_feasible_for_capital(50.0, universe)
    crypto_feasible_1 = f1["asset_classes"]["CRYPTO"]["markets_feasible"]
    crypto_feasible_50 = f50["asset_classes"]["CRYPTO"]["markets_feasible"]
    assert crypto_feasible_50 >= crypto_feasible_1
    for entry in f50["asset_classes"].values():
        assert entry["markets_total"] >= entry["markets_feasible"]
        assert len(entry["cheapest"]) <= 8


# --------------------------------------------------------------------------- #
# 3. Regime adaptation (volatile -> conservative, stable -> engaged)           #
# --------------------------------------------------------------------------- #
def test_regime_of_maps_analysis_labels():
    assert mt.regime_of("HIGH") == "VOLATILE"
    assert mt.regime_of("LOW") == "QUIET"
    assert mt.regime_of("MEDIUM") == "NORMAL"
    assert mt.regime_of(None) == "NORMAL"      # fail-neutral
    assert mt.regime_of("garbage") == "NORMAL"


def test_regime_adjustments_direction():
    volatile = mt.regime_adjustments("VOLATILE")
    quiet = mt.regime_adjustments("QUIET")
    neutral = mt.regime_adjustments("NORMAL")
    assert volatile["min_score_delta"] > 0 and volatile["atr_multiplier_factor"] > 1.0
    assert quiet["min_score_delta"] < 0 and quiet["atr_multiplier_factor"] < 1.0
    assert neutral == {"min_score_delta": 0, "atr_multiplier_factor": 1.0, "style": "neutral"}


def test_signal_engine_effective_min_score_market_and_regime():
    engine = SignalEngine(min_score=80)
    engine.set_market_tuning({"btc_usdt": {"min_score": 85}, "eur_usd": {"min_score": 82}})
    # per-market override (v2.8: floor is 84, so values below 84 are clamped)
    assert engine.effective_min_score("btc_usdt") == 85
    assert engine.effective_min_score("eur_usd") == 84   # 82 < 84 -> clamped to floor
    assert engine.effective_min_score("unknown_market") == 84  # falls back to clamped global
    # regime adjustment on top (volatile raises, quiet lowers, both clamped to floor)
    assert engine.effective_min_score("btc_usdt", "VOLATILE") == 90
    assert engine.effective_min_score("eur_usd", "QUIET") == 84  # 84 - 3 = 81, clamped to 84
    assert engine.effective_min_score("unknown_market", "NORMAL") == 84
    # disabled adaptation -> regime has no effect
    engine.set_regime_adaptation(False)
    assert engine.effective_min_score("btc_usdt", "VOLATILE") == 85


def test_signal_engine_effective_rr_and_atr_stop():
    engine = SignalEngine(min_score=80, risk_reward=2.0, atr_stop_multiplier=1.5)
    engine.set_market_tuning({"eur_usd": {"risk_reward": 1.8, "atr_stop_multiplier": 2.0}})
    assert engine.effective_risk_reward("eur_usd") == 1.8
    assert engine.effective_risk_reward("btc_usdt") == 2.0
    assert engine.effective_atr_stop_multiplier("eur_usd", "NORMAL") == 2.0
    # volatile regime widens the stop (conservative), quiet tightens slightly
    assert engine.effective_atr_stop_multiplier("eur_usd", "VOLATILE") == pytest.approx(2.5)
    assert engine.effective_atr_stop_multiplier("eur_usd", "QUIET") == pytest.approx(1.8)
    assert engine.effective_atr_stop_multiplier("btc_usdt", "VOLATILE") == pytest.approx(1.875)
    # invalid per-market values fall back to the global setting
    bad = SignalEngine(min_score=80)
    bad.set_market_tuning({"btc_usdt": {"risk_reward": 42}})
    assert bad.effective_risk_reward("btc_usdt") == 2.0


# --------------------------------------------------------------------------- #
# 4. Audit-driven per-market optimization                                      #
# --------------------------------------------------------------------------- #
def _market_stats(n, wins, pnl, avg_win=10.0, avg_loss=5.0, rr=None, leaks=0):
    losses = n - wins
    return {
        "trades": n, "wins": wins, "losses": losses,
        "win_rate": round(wins / n * 100, 1), "net_pnl": pnl,
        "avg_win": avg_win, "avg_loss": avg_loss,
        "realized_rr": rr if rr is not None else (avg_win / avg_loss if losses else None),
        "expectancy_per_trade": round(pnl / n, 2),
        "cost_leaks": leaks,
    }


def test_insufficient_data_never_tunes_on_noise():
    rec = mt.recommend_for_market("btc_usdt", _market_stats(3, 1, 2.0), balance=5.0)
    assert rec["verdict"] == "INSUFFICIENT_DATA"
    assert rec["action"] == "OBSERVE"
    assert rec["params"]["min_score"] >= 84  # v2.8: floor is 84
    assert mt.recommend_for_market("btc_usdt", None)["verdict"] == "INSUFFICIENT_DATA"


def test_losing_market_gets_entry_threshold_raised():
    stats = _market_stats(35, 10, -25.0)  # ~29% win rate
    rec = mt.recommend_for_market("doge_usdt", stats, balance=5.0)
    assert rec["verdict"] == "LOSING"
    assert rec["action"] == "QUARANTINE_OR_RAISE_SELECTIVITY"
    # MICRO bracket min score is 85 -> losing market pushed to 95
    assert rec["params"]["min_score"] == 95


def test_tight_tp_market_gets_wider_take_profit():
    stats = _market_stats(35, 20, 8.0, avg_win=6.0, avg_loss=5.0, rr=1.2)
    rec = mt.recommend_for_market("btc_usdt", stats, balance=120.0)
    assert rec["verdict"] == "TP_TOO_TIGHT"
    assert rec["action"] == "WIDEN_TAKE_PROFIT"
    assert rec["params"]["risk_reward"] > 2.0       # widened TP
    assert rec["params"]["atr_stop_multiplier"] > 1.5  # relaxed stop


def test_cost_leak_market_gets_tighter_cost_filter():
    stats = _market_stats(35, 20, 4.0, leaks=3)
    rec = mt.recommend_for_market("pepe_usdt", stats, balance=25.0)
    assert rec["verdict"] == "COST_LEAK"
    assert rec["params"]["max_cost_ratio"] == 0.4


def test_profitable_market_is_kept_and_gently_scaled():
    stats = _market_stats(40, 25, 60.0, avg_win=8.0, avg_loss=4.0)
    rec = mt.recommend_for_market("eth_usdt", stats, balance=25.0)
    assert rec["verdict"] == "PROFITABLE"
    assert rec["action"] == "KEEP_AND_SCALE"
    # v2.8: floor is 84, so relaxed threshold stays at 84 (not below)
    assert rec["params"]["min_score"] >= 84


def test_build_tuning_from_audit_merges_class_defaults_and_overrides():
    universe = MarketUniverse()
    per_market = {
        "doge_usdt": _market_stats(35, 10, -25.0),          # losing -> raised threshold
        "eth_usdt": _market_stats(40, 25, 60.0, avg_win=8.0, avg_loss=4.0),  # profitable
        "shib_usdt": _market_stats(2, 2, 1.0),             # noise -> ignored
    }
    tuning = mt.build_tuning_from_audit(per_market, 5.0, universe)
    assert len(tuning) == len(universe.get_all_ids())
    base = mt.ASSET_CLASS_TUNING["CRYPTO"]
    # losing market: MICRO bracket (5 $) min score is 85 -> raised to 95
    assert tuning["doge_usdt"]["min_score"] == 95
    assert tuning["shib_usdt"]["min_score"] == base["min_score"]       # untouched
    # no audit at all -> pure class defaults
    assert mt.build_tuning_from_audit(None, 5.0, universe)["eur_usd"]["min_score"] == 85


# --------------------------------------------------------------------------- #
# 5. SignalEngine integration end-to-end                                       #
# --------------------------------------------------------------------------- #
def _df():
    rows = 40
    data = {
        "High": [110 + (i % 5) for i in range(rows)],
        "Low": [100 - (i % 5) for i in range(rows)],
        "Close": [105 + 0.2 * i for i in range(rows)],   # steady uptrend
        "Volume": [1000 + 10 * i for i in range(rows)],
    }
    return pd.DataFrame(data)


def _analysis(volatility="MEDIUM", trend="BULLISH"):
    return {
        "status": "VALID", "market_id": "btc_usdt", "trend": trend, "htf_bias": trend,
        "momentum": 0.5, "is_hh": True, "is_hl": True, "is_lh": False, "is_ll": False,
        "bos": True, "choch": False, "last_high": 104.0, "last_low": 103.0,
        "market_state": "TREND", "volatility": volatility,
    }


NEWS_OK = {"trading_allowed": True, "day_ok": True, "session_ok": True, "news_ok": True}


def test_structure_signal_uses_per_market_tp_and_stop():
    engine = SignalEngine(min_score=80, risk_reward=2.0, atr_stop_multiplier=1.5)
    engine.set_market_tuning({"btc_usdt": {"risk_reward": 3.0, "atr_stop_multiplier": 2.0}})
    res = engine.generate_signal(_analysis(), NEWS_OK, _df(), market_id="btc_usdt")
    assert res["status"] == "SIGNAL_DETECTED"
    entry, sl, tp = res["entry"], res["sl"], res["tp"]
    risk = entry - sl
    assert abs((tp - entry) / risk - 3.0) < 1e-9          # exactly the tuned 3R TP
    assert res["risk_reward"] == 3.0
    assert res["atr_stop_multiplier"] == 2.0
    assert res["market_tuning_applied"] is True
    # the tuned market uses a wider stop than the same setup untuned
    plain = SignalEngine(min_score=80, risk_reward=2.0, atr_stop_multiplier=1.5)
    res_plain = plain.generate_signal(_analysis(), NEWS_OK, _df(), market_id="btc_usdt")
    assert (entry - sl) > (res_plain["entry"] - res_plain["sl"])


def test_volatile_regime_blocks_marginal_signal_and_widens_stop():
    engine = SignalEngine(min_score=80, risk_reward=2.0, atr_stop_multiplier=1.5)
    # QUIET regime: same score passes with a slightly relaxed threshold
    res_quiet = engine.generate_signal(_analysis(volatility="LOW"), NEWS_OK, _df(), market_id="btc_usdt")
    assert res_quiet["status"] == "SIGNAL_DETECTED"
    assert res_quiet["regime"] == "QUIET"

    # VOLATILE regime raises the entry threshold (+5) and widens the stop
    res_vol = engine.generate_signal(_analysis(volatility="HIGH"), NEWS_OK, _df(), market_id="btc_usdt")
    assert res_vol["regime"] == "VOLATILE"
    if res_vol["status"] == "SIGNAL_DETECTED":
        assert res_vol["min_score_applied"] == 89   # 84 floor + 5 volatile
        assert res_vol["atr_stop_multiplier"] == pytest.approx(1.875)
        # wider stop -> bigger risk distance for the same price
        assert (res_vol["entry"] - res_vol["sl"]) >= (res_quiet["entry"] - res_quiet["sl"])
    else:
        assert "Below minimum score (90/89)" in res_vol["reason"]


def test_regime_adaptation_can_be_disabled():
    engine = SignalEngine(min_score=80)
    engine.set_regime_adaptation(False)
    res = engine.generate_signal(_analysis(volatility="HIGH"), NEWS_OK, _df(), market_id="btc_usdt")
    assert res["regime"] == "NORMAL"   # adaptation off -> neutral label
    if res["status"] == "SIGNAL_DETECTED":
        assert res["min_score_applied"] == 84


def test_per_market_threshold_blocks_low_score_market_only():
    engine = SignalEngine(min_score=80)
    # doge requires 95 — a marginal 80-84 score setup must be rejected there
    engine.set_market_tuning({"doge_usdt": {"min_score": 95}})
    res_doge = engine.generate_signal(_analysis(), NEWS_OK, _df(), market_id="doge_usdt")
    assert res_doge["status"] == "NO_TRADE"
    assert "95" in res_doge["reason"]
    # same setup stays valid on an untuned market
    res_btc = engine.generate_signal(_analysis(), NEWS_OK, _df(), market_id="btc_usdt")
    assert res_btc["status"] == "SIGNAL_DETECTED"


# --------------------------------------------------------------------------- #
# 6. Settings wiring                                                           #
# --------------------------------------------------------------------------- #
def test_settings_schema_accepts_new_lot_r_keys():
    cleaned, errors = validate_settings({
        "regime_adaptation_enabled": "true",
        "market_tuning": json.dumps({"btc_usdt": {"min_score": 82}}),
    })
    assert errors == []
    assert cleaned["regime_adaptation_enabled"] == "true"
    assert json.loads(cleaned["market_tuning"])["btc_usdt"]["min_score"] == 82
    defaults = ensure_defaults({})
    assert defaults["regime_adaptation_enabled"] == "true"
    assert json.loads(defaults["market_tuning"]) == {}


def test_settings_apply_pushes_tuning_into_signal_engine(monkeypatch):
    import api.index as app_index

    settings = {
        "min_signal_score": "80",
        "regime_adaptation_enabled": "false",
        "market_tuning": json.dumps({"btc_usdt": {"min_score": 90}}),
        "capital_profile_mode": "manual",
    }
    monkeypatch.setattr(app_index.settings_provider, "get", lambda: dict(settings))
    app_index.settings_provider.apply()

    engine = app_index.signal_engine
    assert engine.regime_adaptation_enabled is False
    # full universe of class defaults + the explicit btc override
    assert engine.market_tuning["btc_usdt"]["min_score"] == 90
    assert engine.market_tuning["eur_usd"]["min_score"] == 85   # FOREX class default
    assert len(engine.market_tuning) == len(app_index.data_engine.universe.get_all_ids())
    # invalid JSON must not crash the hot-reload (class defaults kept)
    monkeypatch.setattr(app_index.settings_provider, "get",
                        lambda: {**settings, "market_tuning": "{not json"})
    app_index.settings_provider.apply()
    assert engine.market_tuning["btc_usdt"]["min_score"] == 84  # back to class default
    app_index.settings_provider.invalidate()
