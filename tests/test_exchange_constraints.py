"""
LOT E — Sizing & exchange constraints.

Covers:
- decimal-safe flooring / rounding primitives (never round quantities UP);
- protective SL/TP rounding;
- constraint extraction from MarketUniverse info and CCXT market structures;
- order normalization (lot/tick/min notional gates);
- RiskEngine integration (optional market_info, backward compatible);
- CCXTAdapter.get_market_constraints parsing (offline, no network).
"""
from types import SimpleNamespace

import pytest

from api.engines.broker_adapters.ccxt_adapter import CCXTAdapter
from api.engines.exchange_constraints import (
    ceil_to_step, constraints_from_info, floor_to_step, normalize_order,
    parse_ccxt_market_constraints, round_protective, round_to_tick,
)
from api.engines.risk_engine import RiskEngine


# --------------------------------------------------------------------------- #
# 1. Rounding primitives                                                      #
# --------------------------------------------------------------------------- #
def test_floor_to_step_basics():
    assert floor_to_step(1.23456, 0.001) == pytest.approx(1.234)
    assert floor_to_step(1.99, 1.0) == 1.0
    assert floor_to_step(20.0, 7.0) == 14.0  # floor, never ceil
    assert floor_to_step(0.004, 0.01) == 0.0


def test_floor_to_step_float_safety():
    # Classic float artifact: 0.3 stored as 0.30000000000000004
    assert floor_to_step(0.30000000000000004, 0.1) == pytest.approx(0.3)
    assert floor_to_step(2.675, 0.01) == pytest.approx(2.67)
    # No step → passthrough
    assert floor_to_step(1.2345, 0) == 1.2345
    assert floor_to_step(1.2345, None) == 1.2345


def test_round_to_tick_and_ceil():
    assert round_to_tick(100.005, 0.01) == pytest.approx(100.01)  # half-up
    assert round_to_tick(100.004, 0.01) == pytest.approx(100.00)
    assert ceil_to_step(1.001, 0.01) == pytest.approx(1.01)
    assert ceil_to_step(1.0, 0.01) == pytest.approx(1.0)


def test_round_protective_directions():
    tick = 0.01
    # BUY: floor (SL lower = more margin, TP toward entry)
    assert round_protective(95.006, tick, "BUY") == pytest.approx(95.00)
    assert round_protective(105.006, tick, "BUY") == pytest.approx(105.00)
    # SELL: ceil (SL higher = more margin, TP toward entry)
    assert round_protective(105.004, tick, "SELL") == pytest.approx(105.01)
    assert round_protective(95.004, tick, "SELL") == pytest.approx(95.01)
    assert round_protective(None, tick, "BUY") is None


# --------------------------------------------------------------------------- #
# 2. Constraint extraction                                                    #
# --------------------------------------------------------------------------- #
def test_constraints_from_info():
    info = {"tick_size": 0.1, "lot_size": 0.0001, "min_order": 10.0}
    c = constraints_from_info(info)
    assert c == {"lot_size": 0.0001, "tick_size": 0.1, "min_notional": 10.0}
    assert constraints_from_info(None) is None
    assert constraints_from_info({}) is None
    partial = constraints_from_info({"lot_size": 0.01})
    assert partial == {"lot_size": 0.01, "tick_size": None, "min_notional": None}


def test_parse_ccxt_market_constraints():
    market = {
        "precision": {"amount": 1e-8, "price": 0.01},
        "limits": {"cost": {"min": 5.0}, "amount": {"min": 1e-8}},
    }
    c = parse_ccxt_market_constraints(market)
    assert c == {"lot_size": 1e-8, "tick_size": 0.01, "min_notional": 5.0}
    assert parse_ccxt_market_constraints(None) == \
        {"lot_size": None, "tick_size": None, "min_notional": None}
    assert parse_ccxt_market_constraints({})["lot_size"] is None


# --------------------------------------------------------------------------- #
# 3. Order normalization                                                      #
# --------------------------------------------------------------------------- #
def test_normalize_order_floors_and_rounds():
    info = {"tick_size": 0.01, "lot_size": 0.1, "min_order": 10.0}
    res = normalize_order(quantity=2.347, entry=100.006, direction="BUY",
                          sl=99.006, tp=101.012, info=info)
    assert res["allowed"] is True
    assert res["quantity"] == pytest.approx(2.3)          # floored to 0.1
    assert res["entry"] == pytest.approx(100.01)          # tick rounded
    assert res["sl"] == pytest.approx(99.00)              # protective floor
    assert res["tp"] == pytest.approx(101.01)             # toward entry
    assert res["notional"] == pytest.approx(2.3 * 100.01)
    assert res["adjusted"] is True
    assert "quantity_floored_to_lot" in res["adjustments"]


def test_normalize_order_sell_protective_ceiling():
    info = {"tick_size": 0.01, "lot_size": 0.1}
    res = normalize_order(quantity=1.0, entry=100.0, direction="SELL",
                          sl=100.506, tp=98.504, info=info)
    assert res["sl"] == pytest.approx(100.51)  # ceil → more margin
    assert res["tp"] == pytest.approx(98.51)   # ceil → toward entry


def test_normalize_order_min_notional_gate():
    info = {"tick_size": 0.01, "lot_size": 0.1, "min_order": 50.0}
    res = normalize_order(quantity=0.2, entry=100.0, direction="BUY",
                          sl=99.0, tp=102.0, info=info)
    assert res["allowed"] is False
    assert "below instrument minimum" in res["reason"]


def test_normalize_order_quantity_rounds_to_zero():
    info = {"lot_size": 0.01}
    res = normalize_order(quantity=0.004, entry=100.0, direction="BUY", info=info)
    assert res["allowed"] is False
    assert "rounds to zero" in res["reason"]


def test_normalize_order_passthrough_without_constraints():
    res = normalize_order(quantity=2.34567, entry=100.0, direction="BUY",
                          sl=99.0, tp=102.0, info=None)
    assert res["allowed"] is True
    assert res["quantity"] == 2.34567
    assert res["entry"] == 100.0
    assert res["adjusted"] is False
    assert res["adjustments"] == []


# --------------------------------------------------------------------------- #
# 4. RiskEngine integration                                                   #
# --------------------------------------------------------------------------- #
def test_risk_engine_floors_quantity_to_lot():
    risk = RiskEngine(max_risk_pct=1.0, max_leverage=20)
    # 1000 balance, entry 100, SL 95 → risk 10 → qty 2.0. lot 0.07 → floor 1.96
    res = risk.calculate_position_size(balance=1000.0, entry=100.0, stop_loss=95.0,
                                       market_info={"lot_size": 0.07, "min_order": 10.0})
    assert res["allowed"] is True
    assert res["quantity"] == pytest.approx(1.96)
    assert res["quantity_rounded"] is True
    assert res["leverage"] == pytest.approx(1.96 * 100 / 1000)
    assert res["market_constraints"]["lot_size"] == 0.07


def test_risk_engine_min_notional_blocked():
    risk = RiskEngine(max_risk_pct=1.0, max_leverage=20)
    # 100 balance → risk 1 → qty 0.2 @ 100 → notional 20 < min 50
    res = risk.calculate_position_size(balance=100.0, entry=100.0, stop_loss=95.0,
                                       market_info={"lot_size": 0.001, "min_order": 50.0})
    assert res["allowed"] is False
    assert "below instrument minimum" in res["reason"]


def test_risk_engine_lot_rounds_to_zero_blocked():
    risk = RiskEngine(max_risk_pct=1.0, max_leverage=20)
    # 100 balance → qty 0.2; lot 1.0 → floors to 0
    res = risk.calculate_position_size(balance=100.0, entry=100.0, stop_loss=95.0,
                                       market_info={"lot_size": 1.0})
    assert res["allowed"] is False
    assert "minimum lot size" in res["reason"]


def test_risk_engine_without_market_info_unchanged():
    """Backward compatibility: no market_info → legacy behavior, no constraint keys."""
    risk = RiskEngine(max_risk_pct=1.0, max_leverage=20)
    res = risk.calculate_position_size(balance=1000.0, entry=100.0, stop_loss=95.0)
    assert res["allowed"] is True
    assert res["quantity"] == pytest.approx(2.0)
    assert "market_constraints" not in res
    assert "quantity_rounded" not in res


# --------------------------------------------------------------------------- #
# 5. CCXTAdapter parsing (offline)                                            #
# --------------------------------------------------------------------------- #
def test_ccxt_adapter_constraints_from_loaded_markets():
    adapter = CCXTAdapter(exchange_id="gate")
    adapter.client = SimpleNamespace(markets={
        "BTC/USDT": {
            "precision": {"amount": 1e-8, "price": 0.01},
            "limits": {"cost": {"min": 5.0}},
        }
    })
    c = adapter.get_market_constraints("BTC/USDT")
    assert c == {"lot_size": 1e-8, "tick_size": 0.01, "min_notional": 5.0}
    # Unknown symbol → all None
    assert adapter.get_market_constraints("NOPE/USDT") == \
        {"lot_size": None, "tick_size": None, "min_notional": None}


def test_ccxt_adapter_constraints_without_client():
    adapter = CCXTAdapter(exchange_id="gate")
    assert adapter.get_market_constraints("BTC/USDT") == \
        {"lot_size": None, "tick_size": None, "min_notional": None}
