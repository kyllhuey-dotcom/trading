"""
Unit tests for core (primary + secondary) pure functions.

These tests are fully offline — no network, no FastAPI startup — and exercise
the helper logic the trading engines depend on:
- order_types        : order normalization, fill rules, risk sizing
- rate_limit         : sliding-window limiter (reads vs mutations, window roll)
- exchange_constraints: rounding primitives & CCXT constraint parsing
- settings_schema    : validation, clamping, defaults, bool/enum coercion
- state_machine      : bot lifecycle states
- capital_profiles   : bracket resolution, overrides, expectancy
- market_hub         : sparkline, enrichment, sorting
- json_logging       : JSON formatter and structured logger
- data_layer         : failure cooldown escalation & quote caching
- provider_priority  : deterministic provider ordering
"""
import asyncio
import json
import logging

import pytest

from api.engines.order_types import (
    normalize_order_type,
    should_fill_now,
    risk_based_quantity,
    serialize_pending,
)
from api.rate_limit import SlidingWindowRateLimiter
from api.engines.exchange_constraints import (
    ceil_to_step,
    floor_to_step,
    round_to_tick,
    round_protective,
    constraints_from_info,
    parse_ccxt_market_constraints,
)
from api.engines.settings_schema import (
    SETTINGS_SPEC,
    validate_settings,
    ensure_defaults,
)
from api.engines.state_machine import StateMachine, BotState
from api.engines.capital_profiles import (
    resolve_bracket,
    profile_overrides,
    bracket_summary,
    _expectancy_pct,
)
from api.engines.market_hub import (
    _real_sparkline,
    enrich_market_item,
    sort_hub_items,
    enrich_overview,
)
from api.json_logging import JsonFormatter, structured_log
from api.engines.provider_priority import prioritize_providers


# --------------------------------------------------------------------------- #
# order_types                                                                 #
# --------------------------------------------------------------------------- #
class TestOrderTypes:
    def test_normalize_known_types(self):
        assert normalize_order_type("limit") == "LIMIT"
        assert normalize_order_type(" STOP ") == "STOP"
        assert normalize_order_type("market") == "MARKET"

    def test_normalize_unknown_falls_back_to_market(self):
        assert normalize_order_type("iceberg") == "MARKET"
        assert normalize_order_type(None) == "MARKET"
        assert normalize_order_type("") == "MARKET"

    def test_market_always_fills(self):
        assert should_fill_now("MARKET", "BUY", 1.0) is True
        assert should_fill_now("market", "SELL", 9999.0) is True

    @pytest.mark.parametrize("direction,last,expected", [
        ("BUY", 99.99, True),   # last <= limit
        ("BUY", 100.0, True),
        ("BUY", 100.01, False),
        ("SELL", 100.01, True),  # last >= limit
        ("SELL", 99.99, False),
    ])
    def test_limit_fill_rules(self, direction, last, expected):
        assert should_fill_now("LIMIT", direction, last, limit_price=100.0) is expected

    @pytest.mark.parametrize("direction,last,expected", [
        ("BUY", 100.01, True),   # last >= stop
        ("BUY", 99.99, False),
        ("SELL", 99.99, True),   # last <= stop
        ("SELL", 100.01, False),
    ])
    def test_stop_fill_rules(self, direction, last, expected):
        assert should_fill_now("STOP", direction, last, stop_price=100.0) is expected

    def test_limit_stop_without_trigger_price_does_not_fill(self):
        assert should_fill_now("LIMIT", "BUY", 1.0) is False
        assert should_fill_now("STOP", "SELL", 1.0) is False

    def test_invalid_last_returns_false(self):
        assert should_fill_now("MARKET", "BUY", "not-a-price") is False

    def test_risk_based_quantity_math(self):
        # 1% of 10000 = 100 risk; 1 point distance -> 100 units
        assert risk_based_quantity(10_000, 1.0, 100, 99) == pytest.approx(100.0)
        # wider stop -> smaller size
        assert risk_based_quantity(10_000, 1.0, 100, 95) == pytest.approx(20.0)

    def test_risk_based_quantity_guards(self):
        assert risk_based_quantity(10_000, 1.0, 100, 100) == 0.0  # no distance
        assert risk_based_quantity(0, 1.0, 100, 99) == 0.0       # no balance
        assert risk_based_quantity("x", 1.0, 100, 99) == 0.0     # bad input
        assert risk_based_quantity(-5, 1.0, 100, 99) == 0.0

    def test_serialize_pending_defaults_and_fields(self):
        order = {"id": "O1", "market_id": "btc_usdt", "direction": "BUY",
                 "order_type": "LIMIT", "limit_price": 100.0, "quantity": 2.0}
        s = serialize_pending(order)
        assert s["id"] == "O1"
        assert s["status"] == "PENDING"
        assert s["stop_price"] is None
        # explicit status preserved
        order["status"] = "QUEUED"
        assert serialize_pending(order)["status"] == "QUEUED"


# --------------------------------------------------------------------------- #
# SlidingWindowRateLimiter                                                    #
# --------------------------------------------------------------------------- #
class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class TestRateLimiter:
    def test_allows_up_to_read_limit_then_blocks(self):
        clk = FakeClock()
        rl = SlidingWindowRateLimiter(requests_per_minute=3,
                                      mutations_per_minute=1, window_s=60, clock=clk)
        assert rl.allow("ip1") is True
        assert rl.allow("ip1") is True
        assert rl.allow("ip1") is True
        assert rl.allow("ip1") is False

    def test_mutation_budget_is_separate(self):
        clk = FakeClock()
        rl = SlidingWindowRateLimiter(requests_per_minute=10,
                                      mutations_per_minute=1, window_s=60, clock=clk)
        assert rl.allow("ip1", is_mutation=True) is True
        assert rl.allow("ip1", is_mutation=True) is False
        # reads still allowed (shared counter in this implementation)
        assert rl.allow("ip1", is_mutation=False) is True

    def test_window_expires(self):
        clk = FakeClock()
        rl = SlidingWindowRateLimiter(requests_per_minute=2,
                                      mutations_per_minute=2, window_s=60, clock=clk)
        assert rl.allow("ip1") is True
        assert rl.allow("ip1") is True
        assert rl.allow("ip1") is False
        clk.advance(61)
        assert rl.allow("ip1") is True

    def test_clients_are_independent(self):
        clk = FakeClock()
        rl = SlidingWindowRateLimiter(requests_per_minute=1, window_s=60, clock=clk)
        assert rl.allow("a") is True
        assert rl.allow("b") is True
        assert rl.allow("a") is False

    def test_reset_clears_state(self):
        clk = FakeClock()
        rl = SlidingWindowRateLimiter(requests_per_minute=1, window_s=60, clock=clk)
        rl.allow("a")
        assert rl.tracked_clients() == 1
        rl.reset()
        assert rl.tracked_clients() == 0
        assert rl.allow("a") is True


# --------------------------------------------------------------------------- #
# exchange_constraints                                                        #
# --------------------------------------------------------------------------- #
class TestExchangeConstraints:
    def test_floor_and_ceil_to_step(self):
        assert floor_to_step(1.23456, 0.001) == pytest.approx(1.234)
        assert ceil_to_step(1.2341, 0.001) == pytest.approx(1.235)
        assert floor_to_step(20.0, 7.0) == 14.0
        assert ceil_to_step(21.0, 7.0) == 21.0

    def test_round_to_tick_half_up(self):
        assert round_to_tick(1.2345, 0.01) == pytest.approx(1.23)
        assert round_to_tick(1.235, 0.01) == pytest.approx(1.24)

    def test_protective_rounding_direction(self):
        # BUY floors (SL moves away from entry downward)
        assert round_protective(1.239, 0.01, "BUY") == pytest.approx(1.23)
        # SELL ceils (SL moves away upward)
        assert round_protective(1.231, 0.01, "SELL") == pytest.approx(1.24)
        assert round_protective(None, 0.01, "BUY") is None

    def test_zero_step_passthrough(self):
        assert floor_to_step(1.5, 0) == 1.5
        assert ceil_to_step(1.5, 0) == 1.5
        assert round_to_tick(1.5, 0) == 1.5

    def test_constraints_from_info(self):
        info = {"lot_size": 0.0001, "tick_size": 0.1, "min_order": 10.0}
        c = constraints_from_info(info)
        assert c == {"lot_size": 0.0001, "tick_size": 0.1, "min_notional": 10.0}
        assert constraints_from_info({}) is None
        assert constraints_from_info(None) is None

    def test_parse_ccxt_market_constraints(self):
        market = {"precision": {"amount": 0.001, "price": 0.01},
                  "limits": {"cost": {"min": 5.0}}}
        c = parse_ccxt_market_constraints(market)
        assert c == {"lot_size": 0.001, "tick_size": 0.01, "min_notional": 5.0}
        # empty / None
        assert parse_ccxt_market_constraints(None) == {
            "lot_size": None, "tick_size": None, "min_notional": None}

    def test_normalize_order_quantity_floor_and_notional_gate(self):
        from api.engines.exchange_constraints import normalize_order
        info = {"lot_size": 0.1, "tick_size": 0.5, "min_order": 50.0}
        # qty 0.25 -> floor to 0.2; entry 100.3 -> round to 100.5; notional 20.1 < 50
        res = normalize_order(0.25, 100.3, "BUY", sl=99.0, tp=102.0, info=info)
        assert res["quantity"] == pytest.approx(0.2)
        assert res["entry"] == pytest.approx(100.5)
        assert res["allowed"] is False
        assert "minimum" in res["reason"]
        assert res["adjusted"] is True
        assert "quantity_floored_to_lot" in res["adjustments"]

    def test_normalize_order_no_constraints_passthrough(self):
        from api.engines.exchange_constraints import normalize_order
        res = normalize_order(1.0, 100.0)
        assert res["allowed"] is True
        assert res["adjusted"] is False
        assert res["notional"] == 100.0


# --------------------------------------------------------------------------- #
# settings_schema                                                             #
# --------------------------------------------------------------------------- #
class TestSettingsSchema:
    def test_clamp_numeric_bounds(self):
        cleaned, errors = validate_settings({"max_open_positions": "1000"})
        assert int(cleaned["max_open_positions"]) == SETTINGS_SPEC["max_open_positions"]["max"]
        assert any("clamped" in e for e in errors)

    def test_invalid_value_uses_default(self):
        cleaned, errors = validate_settings({"max_risk_pct": "abc"})
        assert cleaned["max_risk_pct"] == SETTINGS_SPEC["max_risk_pct"]["default"]
        assert any("invalid" in e for e in errors)

    def test_bool_coercion(self):
        cleaned, _ = validate_settings({"trailing_stop_active": "yes"})
        assert cleaned["trailing_stop_active"] == "true"
        cleaned, _ = validate_settings({"auto_arm_on_startup": "0"})
        assert cleaned["auto_arm_on_startup"] == "false"

    def test_enum_invalid_defaults(self):
        cleaned, errors = validate_settings({"language": "jp"})
        assert cleaned["language"] == "en"
        assert errors

    def test_unknown_key_passed_through(self):
        cleaned, _ = validate_settings({"custom_flag": "42"})
        assert cleaned["custom_flag"] == "42"

    def test_ensure_defaults_fills_missing_only(self):
        out = ensure_defaults({"max_risk_pct": "2.5"})
        assert out["max_risk_pct"] == "2.5"
        assert out["max_leverage"] == SETTINGS_SPEC["max_leverage"]["default"]
        # empty values also get defaulted
        out = ensure_defaults({"max_leverage": ""})
        assert out["max_leverage"] == SETTINGS_SPEC["max_leverage"]["default"]


# --------------------------------------------------------------------------- #
# state_machine                                                               #
# --------------------------------------------------------------------------- #
class TestStateMachine:
    def test_initial_state(self):
        assert StateMachine().current_state == BotState.STOPPED

    def test_transitions_update_state(self):
        sm = StateMachine()
        sm.transition_to(BotState.RUNNING)
        assert sm.current_state == BotState.RUNNING
        sm.transition_to(BotState.ERROR)
        assert sm.current_state == BotState.ERROR
        sm.transition_to(BotState.EMERGENCY_STOP)
        assert sm.current_state == BotState.EMERGENCY_STOP

    def test_states_are_strings(self):
        # Enum is str-based for JSON serialization
        assert BotState.RUNNING.value == "RUNNING"
        assert isinstance(BotState.RUNNING, str)


# --------------------------------------------------------------------------- #
# capital_profiles                                                            #
# --------------------------------------------------------------------------- #
class TestCapitalProfiles:
    def test_bracket_resolution_monotonic(self):
        micro = resolve_bracket(5)
        retail = resolve_bracket(25)
        standard = resolve_bracket(5_000)
        assert micro.name == "MICRO"
        assert retail.name == "RETAIL"
        assert standard.name == "STANDARD"
        # larger accounts are allowed more open positions / risk
        assert standard.max_positions > micro.max_positions

    def test_negative_or_none_balance_treated_as_zero(self):
        assert resolve_bracket(-100).min_balance == 0
        assert resolve_bracket(None).min_balance == 0

    def test_profile_overrides_keys(self):
        overrides = profile_overrides(10_000)
        for key in ("bracket", "risk_pct", "max_leverage", "max_open_positions",
                    "min_signal_score", "risk_reward_ratio"):
            assert key in overrides

    def test_bracket_summary_is_list_of_dicts(self):
        summary = bracket_summary()
        assert isinstance(summary, list) and summary
        assert "name" in summary[0] and "risk_pct" in summary[0]

    def test_expectancy_formula(self):
        # 50% win, 2R win / 1R loss -> +0.5R expectancy
        assert _expectancy_pct(50, 2.0, 1.0) == pytest.approx(0.5)
        # no trades -> 0
        assert _expectancy_pct(0, 0.0, 0.0) == 0.0


# --------------------------------------------------------------------------- #
# market_hub                                                                  #
# --------------------------------------------------------------------------- #
class TestMarketHub:
    def test_sparkline_real_only(self):
        # v3.3.2 (D1): no synthetic sparkline — real closes pass through,
        # garbage is dropped, nothing is invented.
        assert _real_sparkline([100.0, 101.0, 99.0]) == [100.0, 101.0, 99.0]
        assert _real_sparkline([100.0, "x", None, 0.0, 101.0]) == [100.0, 101.0]
        assert _real_sparkline(None) == []
        assert _real_sparkline([]) == []

    def test_enrich_never_fabricates_price_or_sparkline(self):
        # v3.3.2 (D1/D2): no price → None (not 0.0); no real sparkline → [].
        item = enrich_market_item(
            {"market_id": "btc_usdt", "last": None},
            {"btc_usdt": {"score": 88, "trend": "BULLISH", "strategy": "rsi"}})
        assert item["price"] is None
        assert item["sparkline"] == []
        item2 = enrich_market_item(
            {"market_id": "btc_usdt", "last": 10},
            {"btc_usdt": {"sparkline": [9.0, 10.0], "sparkline_stale": False}})
        assert item2["price"] == 10
        assert item2["sparkline"] == [9.0, 10.0]
        assert item2["sparkline_stale"] is False

    def test_enrich_uses_scan_data(self):
        item = enrich_market_item(
            {"market_id": "btc_usdt", "last": 10},
            {"btc_usdt": {"score": 88, "trend": "BULLISH", "strategy": "rsi"}})
        assert item["score"] == 88
        assert item["trend"] == "BULLISH"
        assert item["strategy"] == "rsi"

    def test_sort_hub_items(self):
        items = [{"score": 1, "volume": 9, "display_symbol": "a"},
                 {"score": 9, "volume": 1, "display_symbol": "b"}]
        assert sort_hub_items(items, "score", True)[0]["score"] == 9
        assert sort_hub_items(items, "volume", True)[0]["volume"] == 9
        # invalid key falls back to score
        assert sort_hub_items(items, "bogus", True)[0]["score"] == 9
        # ascending symbol sort
        names = [i["display_symbol"] for i in sort_hub_items(items, "symbol", False)]
        assert names == ["a", "b"]

    def test_enrich_overview_groups_and_sorts(self):
        overview = {"CRYPTO": [
            {"market_id": "a", "last": 1.0, "score": 10},
            {"market_id": "b", "last": 2.0, "score": 20},
        ]}
        out = enrich_overview(overview, scan=[], sort="score", order="desc")
        assert list(out.keys()) == ["CRYPTO"]
        assert out["CRYPTO"][0]["score"] == 20


# --------------------------------------------------------------------------- #
# json_logging                                                                #
# --------------------------------------------------------------------------- #
class TestJsonLogging:
    def test_formatter_emits_valid_json(self):
        fmt = JsonFormatter()
        record = logging.LogRecord(
            name="t", level=logging.INFO, pathname=__file__, lineno=1,
            msg="hello %s", args=("world",), exc_info=None)
        payload = json.loads(fmt.format(record))
        assert payload["message"] == "hello world"
        assert payload["level"] == "INFO"

    def test_formatter_serializes_extra_fields_and_exc(self):
        fmt = JsonFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            record = logging.LogRecord(
                name="t", level=logging.ERROR, pathname=__file__, lineno=1,
                msg="failed", args=(), exc_info=sys.exc_info())
        payload = json.loads(fmt.format(record))
        assert "ValueError" in payload["exc_info"]
        assert payload["level"] == "ERROR"

    def test_structured_log_accepts_kwargs(self, tmp_path):
        log_path = tmp_path / "out.jsonl"
        handler = logging.FileHandler(log_path)
        handler.setFormatter(JsonFormatter())
        lg = logging.getLogger("test_structured_unit")
        lg.handlers = [handler]
        lg.setLevel(logging.INFO)
        structured_log(lg, logging.INFO, "trade done", market="btc_usdt", qty=1.5)
        handler.flush()
        line = log_path.read_text().strip().splitlines()[-1]
        payload = json.loads(line)
        assert payload["message"] == "trade done"
        assert payload["market"] == "btc_usdt"
        assert payload["qty"] == 1.5


# --------------------------------------------------------------------------- #
# provider_priority                                                           #
# --------------------------------------------------------------------------- #
class TestProviderPriority:
    def test_orders_binance_bybit_gate(self):
        items = [("gate", "G"), ("binance", "B"), ("bybit", "Y")]
        ordered = [pid for pid, _ in prioritize_providers(items)]
        assert ordered == ["binance", "bybit", "gate"]

    def test_unknown_providers_sorted_last_stable(self):
        items = [("zeta", "Z"), ("binance", "B"), ("alpha", "A")]
        ordered = [pid for pid, _ in prioritize_providers(items)]
        assert ordered[0] == "binance"
        assert set(ordered[1:]) == {"zeta", "alpha"}

    def test_empty_and_dict_items(self):
        assert prioritize_providers([]) == []
        assert prioritize_providers(None) == []
        d = {"bybit": "Y", "binance": "B"}
        ordered = [pid for pid, _ in prioritize_providers(d.items())]
        assert ordered == ["binance", "bybit"]


# --------------------------------------------------------------------------- #
# data_layer: failure cooldown + quote cache (offline)                       #
# --------------------------------------------------------------------------- #
class _StubProvider:
    def __init__(self, fail: bool = False, raises: bool = False):
        self.fail = fail
        self.raises = raises
        self.calls = 0

    async def get_quote(self, symbol):
        self.calls += 1
        if self.raises:
            raise RuntimeError("provider down")
        if self.fail:
            return None
        from api.engines.data_providers.base_provider import TickerModel
        return TickerModel(symbol=symbol, asset_class="CRYPTO", exchange="stub",
                           timestamp=0, last=100.0, source="stub", status="LIVE")


class _StubCatalog:
    def __init__(self, providers):
        self._info = {"m1": {"providers": providers}}

    def get_info(self, mid):
        return self._info.get(mid)


class TestDataLayerCooldown:
    def test_failure_cooldown_skips_provider(self):
        from api.engines.data_layer import DataLayer
        layer = DataLayer()
        layer.failure_cooldown = 300
        failing = _StubProvider(fail=True)
        layer.register_provider("gate", failing)
        catalog = _StubCatalog({"gate": "BTC/USDT"})

        async def run():
            await layer.get_all_quotes(["m1"], catalog)
            await layer.get_all_quotes(["m1"], catalog)
        asyncio.run(run())
        # first call attempts; second call must skip the provider (in cooldown)
        assert failing.calls == 1

    def test_exception_path_records_failure(self):
        from api.engines.data_layer import DataLayer
        layer = DataLayer()
        raising = _StubProvider(raises=True)
        layer.register_provider("gate", raising)
        catalog = _StubCatalog({"gate": "BTC/USDT"})

        async def run():
            r1 = await layer.get_all_quotes(["m1"], catalog)
            r2 = await layer.get_all_quotes(["m1"], catalog)
            return r1, r2
        r1, r2 = asyncio.run(run())
        assert r1 == [] and r2 == []
        assert raising.calls == 1

    def test_cooldown_escalation_doubles(self):
        from api.engines.data_layer import DataLayer
        layer = DataLayer()
        layer.failure_cooldown = 100
        layer._record_failure("k")
        layer._record_failure("k")
        layer._record_failure("k")
        # 100 * 2^(3-1) = 400
        assert layer._cooldown_for("k") == 400
        layer._record_success("k")
        assert layer._cooldown_for("k") == 100

    def test_quote_cache_returns_within_ttl(self):
        from api.engines.data_layer import DataLayer
        layer = DataLayer()
        layer._quote_cache_ttl = 10.0
        stub = _StubProvider()
        layer.register_provider("gate", stub)
        catalog = _StubCatalog({"gate": "BTC/USDT"})

        async def run():
            await layer.get_all_quotes(["m1"], catalog)
            await layer.get_all_quotes(["m1"], catalog)
        asyncio.run(run())
        assert stub.calls == 1  # second hit served from cache
