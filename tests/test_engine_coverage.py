"""
LOT G — Coverage of critical engines (offline, fully mocked).

Sections: ScannerEngine (mock data engine), ExecutionRouter, SignalEngine
structure paths, ExecutionEngine lifecycle, DiagnosticEngine, MarketUniverse,
DataEngine fetch orchestration, ccxt providers via FakeExchange, DB manager.
"""
import asyncio

import pandas as pd
import pytest

from api.engines.analysis_engine import AnalysisEngine
from api.engines.data_engine import DataEngine
from api.engines.data_providers.binance_provider import BinanceProvider
from api.engines.data_providers.bybit_provider import BybitProvider
from api.engines.data_providers.gate_provider import GateProvider
from api.engines.db_manager import DatabaseManager
from api.engines.diagnostic_engine import DiagnosticEngine
from api.engines.execution_engine import ExecutionEngine
from api.engines.execution_router import ExecutionRouter
from api.engines.market_universe import MarketUniverse
from api.engines.portfolio_engine import PortfolioEngine
from api.engines.risk_engine import RiskEngine
from api.engines.scanner_engine import ScannerEngine
from api.engines.signal_engine import SignalEngine

from tests.mocks import (FailingExchange, FakeDataEngine, FakeExchange, FakeNewsEngine,
                         build_ohlcv, build_orderbook, build_quote, build_ticker,
                         build_trades, ticker_model)


# --------------------------------------------------------------------------- #
# 1. ScannerEngine (fully mocked data)                                        #
# --------------------------------------------------------------------------- #
def _small_universe():
    u = MarketUniverse()
    u.universe = {"btc_usdt": u.universe["btc_usdt"],
                  "eth_usdt": u.universe["eth_usdt"],
                  "eur_usd": u.universe["eur_usd"]}
    return u


def _scanner(data_engine=None, news=None):
    data = data_engine or FakeDataEngine(_small_universe())
    return ScannerEngine(data, AnalysisEngine(), SignalEngine(min_score=80),
                         news or FakeNewsEngine(allowed=True), max_concurrent=2)


@pytest.mark.asyncio
async def test_scanner_scan_asset_full_path():
    scanner = _scanner()
    sem = asyncio.Semaphore(2)
    res = await scanner.scan_asset("btc_usdt", sem)
    assert res["symbol"] == "btc_usdt"
    assert res["asset_class"] == "CRYPTO"
    assert res["status"] == "LIVE"
    assert "diagnosis" in res and "checks" in res["diagnosis"]
    assert res["realtime_source"] is True
    assert "signal_data" in res and "tradable" in res
    assert res["data_age_ms"] is not None


@pytest.mark.asyncio
async def test_scanner_scan_asset_non_crypto():
    scanner = _scanner()
    sem = asyncio.Semaphore(2)
    res = await scanner.scan_asset("eur_usd", sem)
    assert res["symbol"] == "eur_usd"
    assert res["realtime_source"] is False


@pytest.mark.asyncio
async def test_scanner_unknown_symbol_and_data_unavailable():
    scanner = _scanner()
    sem = asyncio.Semaphore(2)
    res_unknown = await scanner.scan_asset("__nope__", sem)
    assert res_unknown["status"] == "UNKNOWN_SYMBOL"
    assert res_unknown["tradable"] is False

    class EmptyData(FakeDataEngine):
        async def fetch_ohlcv(self, market_id, timeframe="1m", limit=100):
            return pd.DataFrame()

    scanner_empty = _scanner(EmptyData(_small_universe()))
    res_empty = await scanner_empty.scan_asset("btc_usdt", sem)
    assert res_empty["status"] == "DATA_UNAVAILABLE"
    assert res_empty["tradable"] is False


@pytest.mark.asyncio
async def test_scanner_blocking_news_blocks_signal():
    scanner = _scanner(news=FakeNewsEngine(allowed=False))
    sem = asyncio.Semaphore(2)
    res = await scanner.scan_asset("btc_usdt", sem)
    assert res["news_risk"] == "High"
    assert res["tradable"] is False


@pytest.mark.asyncio
async def test_scanner_scan_all_and_settings():
    scanner = _scanner()
    scanner.apply_settings({"max_spread_pct": "0.9"})
    assert scanner.max_spread_pct == 0.9
    scanner.apply_settings({"max_spread_pct": "not-a-float"})
    assert scanner.max_spread_pct == 0.9  # invalid value ignored
    results = await scanner.scan_all()
    assert len(results) == 3
    assert scanner.last_scan_duration >= 0


# --------------------------------------------------------------------------- #
# 2. ExecutionRouter                                                          #
# --------------------------------------------------------------------------- #
class FakeDemoAdapter:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = 0

    async def execute_order(self, mode, signal, risk, ticker):
        self.calls += 1
        if self.fail:
            return {"success": False, "reason": "DEMO_FAIL"}
        return {"success": True, "position": {"id": "P-1", "mode": mode}}


class FakeBrokerConnector:
    def __init__(self):
        self.calls = 0

    async def execute(self, signal, risk):
        self.calls += 1
        return {"success": True, "position": {"id": "R-1"}}


def _router(demo_fail: bool = False):
    return ExecutionRouter(demo_adapter=FakeDemoAdapter(fail=demo_fail),
                           broker_connector=FakeBrokerConnector())


def _signal(market_id="btc_usdt", direction="BUY"):
    return {"market_id": market_id, "display_symbol": "BTC/USDT",
            "direction": direction, "entry": 100.0, "sl": 99.0, "tp": 102.0}


@pytest.mark.asyncio
async def test_router_demo_execution_and_history():
    router = _router()
    res = await router.execute("DEMO", _signal(),
                               {"quantity": 1.0, "leverage": 1.0, "estimated_fees": 0.1},
                               build_ticker())
    assert res["success"] is True
    assert res["client_order_id"].startswith("ORD-")
    assert router.order_history[-1]["mode"] == "DEMO"
    assert router.last_order_timestamp is not None


@pytest.mark.asyncio
async def test_router_throttles_rapid_orders():
    router = _router()
    sig, risk = _signal(), {"quantity": 1.0, "leverage": 1.0, "estimated_fees": 0.1}
    assert (await router.execute("DEMO", sig, risk, build_ticker()))["success"] is True
    throttled = await router.execute("DEMO", sig, risk, build_ticker())
    assert throttled["success"] is False
    assert "throttled" in throttled["reason"]


@pytest.mark.asyncio
async def test_router_real_routing():
    router = _router()
    res = await router.execute("REAL", _signal(),
                               {"quantity": 1.0, "leverage": 1.0, "estimated_fees": 0.1},
                               build_ticker())
    assert res["success"] is True
    assert router.broker.calls == 1
    assert router.demo.calls == 0


@pytest.mark.asyncio
async def test_router_failure_recorded_and_history_capped():
    router = _router(demo_fail=True)
    res = await router.execute("DEMO", _signal(),
                               {"quantity": 1.0, "leverage": 1.0, "estimated_fees": 0.1},
                               build_ticker())
    assert res["success"] is False
    assert router.order_history[-1]["success"] is False
    assert router.order_history[-1]["reason"] == "DEMO_FAIL"

    router = _router()
    router.order_history = [{"filler": True}] * 501
    await router.execute("DEMO", _signal(),
                         {"quantity": 1.0, "leverage": 1.0, "estimated_fees": 0.1},
                         build_ticker())
    assert len(router.order_history) == 500  # bounded in-memory audit


# --------------------------------------------------------------------------- #
# 3. SignalEngine — structure strategy paths                                  #
# --------------------------------------------------------------------------- #
def _analysis(**overrides):
    base = {"status": "VALID", "trend": "BULLISH", "market_state": "TREND",
            "momentum": 1.0, "is_hh": True, "is_hl": True, "is_lh": False, "is_ll": False,
            "bos": True, "choch": False, "htf_bias": "BULLISH",
            "last_low": 98.0, "last_high": 101.0, "market_id": "btc_usdt"}
    base.update(overrides)
    return base


def test_signal_structure_full_conviction():
    engine = SignalEngine(min_score=80)
    res = engine.generate_signal(_analysis(), {"trading_allowed": True},
                                 build_ohlcv(), market_id="btc_usdt",
                                 strategy_mode="structure")
    assert res["status"] == "SIGNAL_DETECTED"
    assert res["direction"] == "BUY"
    assert res["score"] >= 80
    assert res["strategy"] == "structure"
    assert res["risk_reward"] == 2.0
    assert res["atr"] > 0


def test_signal_neutral_trend_blocks():
    engine = SignalEngine(min_score=80)
    res = engine.generate_signal(_analysis(trend="NEUTRAL", bos=False, choch=False),
                                 {"trading_allowed": True}, build_ohlcv(),
                                 market_id="btc_usdt", strategy_mode="structure")
    assert res["status"] == "NO_TRADE"
    assert "No clear trend" in res["reason"]


def test_signal_neutral_with_choch_picks_direction():
    engine = SignalEngine(min_score=80)
    df = build_ohlcv()  # last close above last_high → BUY
    res = engine.generate_signal(_analysis(trend="NEUTRAL", choch=True, bos=False),
                                 {"trading_allowed": True}, df, market_id="btc_usdt",
                                 strategy_mode="structure")
    assert res["status"] in ("SIGNAL_DETECTED", "NO_TRADE")
    if res["status"] == "SIGNAL_DETECTED":
        assert res["direction"] == "BUY"


def test_signal_insufficient_ohlcv():
    engine = SignalEngine(min_score=80)
    tiny = pd.DataFrame({'High': [1, 2, 3], 'Low': [1, 1, 1], 'Close': [1, 2, 3]})
    res = engine.generate_signal(_analysis(), {"trading_allowed": True}, tiny,
                                 market_id="btc_usdt", strategy_mode="structure")
    assert res["status"] == "NO_TRADE"
    assert "Insufficient" in res["reason"]


def test_signal_invalid_analysis():
    engine = SignalEngine(min_score=80)
    res = engine.generate_signal({"status": "INVALID"}, {"trading_allowed": True},
                                 build_ohlcv(), market_id="btc_usdt",
                                 strategy_mode="structure")
    assert res["status"] == "NO_TRADE"
    assert "Invalid" in res["reason"]


def test_signal_multi_strategy_mode():
    engine = SignalEngine(min_score=80)
    engine.set_active_strategies(["structure", "tape"])
    # Legacy strategy modules remain directly testable, but are not selected
    # by the automatic active-strategy list.
    res = engine.generate_signal(_analysis(), {"trading_allowed": True},
                                 build_ohlcv(), market_id="btc_usdt",
                                 strategy_mode="structure",
                                 orderbook=build_orderbook(), trades=build_trades())
    assert res["status"] == "SIGNAL_DETECTED"
    assert res["strategy"] == "structure"

    # With no structural setup → clean NO_TRADE
    res_none = engine.generate_signal(_analysis(trend="NEUTRAL", bos=False, choch=False),
                                      {"trading_allowed": True}, build_ohlcv(),
                                      market_id="btc_usdt", strategy_mode="structure")
    assert res_none["status"] == "NO_TRADE"


def test_signal_active_strategy_validation_and_min_score():
    engine = SignalEngine(min_score=80)
    engine.set_active_strategies(["nope", "structure"])
    assert engine.active_strategy_names == ["rsi"]
    engine.set_active_strategies(["tape", "liquidity", "arbitrage"])
    assert engine.active_strategy_names == ["rsi"]
    engine.set_active_strategies([])
    assert engine.active_strategy_names == ["rsi"]
    engine.set_min_score(90)
    assert engine.min_score == 90


# --------------------------------------------------------------------------- #
# 4. ExecutionEngine lifecycle (temp DB)                                      #
# --------------------------------------------------------------------------- #
@pytest.fixture()
def exec_env(tmp_path):
    db = DatabaseManager(str(tmp_path / "exec.db"))
    with db._get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('sim_latency_ms', '0')")
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('sim_rejection_prob', '0.0')")
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('trailing_stop_active', 'true')")
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('partial_tp_ratio', '1.0')")
    portfolio = PortfolioEngine(db)
    risk = RiskEngine()

    class Universe:
        def __init__(self, status="OPEN"):
            self.status = status

        def get_market_status(self, s):
            return self.status

    universe = Universe()
    engine = ExecutionEngine(portfolio, db, risk, universe)
    return engine, db, portfolio, universe


@pytest.mark.asyncio
async def test_execution_missing_market_id_and_no_price(exec_env):
    engine, db, portfolio, universe = exec_env
    res = await engine.execute_order("DEMO", {"direction": "BUY"},
                                     {"quantity": 1.0, "leverage": 1.0, "estimated_fees": 0},
                                     build_ticker())
    assert res["success"] is False and res["reason"] == "MISSING_MARKET_ID"

    res = await engine.execute_order("DEMO", {"market_id": "btc_usdt", "direction": "BUY"},
                                     {"quantity": 1.0, "leverage": 1.0, "estimated_fees": 0},
                                     {})
    assert res["success"] is False and res["reason"] == "NO_PRICE_AVAILABLE"


@pytest.mark.asyncio
async def test_execution_market_closed_and_duplicate(exec_env):
    engine, db, portfolio, universe = exec_env
    universe.status = "CLOSED"
    res = await engine.execute_order("DEMO", {"market_id": "btc_usdt", "direction": "BUY",
                                              "sl": 90, "tp": 110},
                                     {"quantity": 1.0, "leverage": 1.0, "estimated_fees": 0},
                                     build_ticker())
    assert res["success"] is False and res["reason"] == "MARKET_CLOSED"

    universe.status = "OPEN"
    sig = {"market_id": "btc_usdt", "direction": "BUY", "sl": 90, "tp": 110}
    risk_data = {"quantity": 1.0, "leverage": 1.0, "estimated_fees": 0}
    assert (await engine.execute_order("DEMO", sig, risk_data, build_ticker()))["success"] is True
    dup = await engine.execute_order("DEMO", sig, risk_data, build_ticker())
    assert dup["success"] is False
    assert "already open" in dup["reason"]


@pytest.mark.asyncio
async def test_execution_sell_uses_bid_and_simulated_rejection(exec_env):
    engine, db, portfolio, universe = exec_env
    sig = {"market_id": "eth_usdt", "display_symbol": "ETH/USDT", "direction": "SELL",
           "sl": 110, "tp": 90}
    risk_data = {"quantity": 1.0, "leverage": 1.0, "estimated_fees": 0}
    res = await engine.execute_order("DEMO", sig, risk_data,
                                     {"bid": 99.0, "ask": 100.0, "last": 99.5})
    assert res["success"] is True
    assert res["position"]["entry_price"] < 99.0  # bid minus slippage

    with db._get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('sim_rejection_prob', '1.0')")
    rej = await engine.execute_order("DEMO", {"market_id": "sol_usdt", "direction": "BUY",
                                              "sl": 90, "tp": 110}, risk_data, build_ticker())
    assert rej["success"] is False and rej["reason"] == "SIMULATED_BROKER_REJECTION"


@pytest.mark.asyncio
async def test_execution_position_lifecycle_tp_hit(exec_env):
    engine, db, portfolio, universe = exec_env
    with db._get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('trailing_stop_active', 'false')")
    portfolio.set_balance("DEMO", 10000.0)
    # entry ≈ 100.15 (ask + slippage), SL 90 → risk ≈ 10.15 → 2R TP = 120.45
    sig = {"market_id": "btc_usdt", "direction": "BUY", "sl": 90, "tp": 125,
           "atr": 2.0, "strategy": "structure", "score": 85}
    risk_data = {"quantity": 1.0, "leverage": 1.0, "estimated_fees": 0}
    opened = await engine.execute_order("DEMO", sig, risk_data, build_ticker(last=100.0))
    assert opened["success"] is True

    # Price runs through the 1:1 partial TP (≈110.3) → locks 50%, SL to break-even
    updated = await engine.update_active_positions("DEMO", {"btc_usdt": build_ticker(last=112.0)})
    assert len(updated) == 0  # still open after partial TP
    pos = engine.active_positions[0]
    assert pos["quantity"] == 0.5
    assert pos["metadata"]["partial_tp_hit"] is True
    assert pos["sl"] == pos["entry_price"]  # break-even

    # Price continues to the final TP
    closed = await engine.update_active_positions("DEMO", {"btc_usdt": build_ticker(last=126.0)})
    assert len(closed) == 1
    assert closed[0]["metadata"]["close_reason"] == "TP_HIT"
    assert engine.active_positions == []


@pytest.mark.asyncio
async def test_execution_clear_active_positions(exec_env):
    engine, db, portfolio, universe = exec_env
    sig = {"market_id": "btc_usdt", "direction": "BUY", "sl": 90, "tp": 110}
    risk_data = {"quantity": 1.0, "leverage": 1.0, "estimated_fees": 0}
    await engine.execute_order("DEMO", sig, risk_data, build_ticker())
    closed = engine.clear_active_positions("DEMO")
    assert len(closed) == 1
    assert engine.active_positions == []


# --------------------------------------------------------------------------- #
# 5. DiagnosticEngine + MarketUniverse + DataEngine orchestration             #
# --------------------------------------------------------------------------- #
def test_diagnostic_engine_priority_order():
    diag = DiagnosticEngine()
    report = diag.diagnose(
        symbol="btc_usdt", data_valid=False, day_allowed=True, session_allowed=True,
        news_clear=True, market_open=False, not_range=True, trend_valid=True,
        structure_valid=True, signal_valid=True, spread_valid=True, liquidity_valid=True,
        risk_valid=True, leverage_valid=True, broker_valid=True, system_armed=True,
        reasons={"DATA_VALID": "No data", "MARKET_OPEN": "Closed"},
        strategy_info={"strategy": "structure", "score": 0})
    assert report["main_blocker"] == "DATA_VALID"
    assert "MARKET_OPEN" in report["secondary_blockers"]
    assert report["checks"]["DATA_VALID"] == "FAIL"
    assert report["strategy"] == "structure"


def test_diagnostic_engine_all_pass():
    diag = DiagnosticEngine()
    report = diag.diagnose(
        symbol="btc_usdt", data_valid=True, day_allowed=True, session_allowed=True,
        news_clear=True, market_open=True, not_range=True, trend_valid=True,
        structure_valid=True, signal_valid=True, spread_valid=True, liquidity_valid=True,
        risk_valid=True, leverage_valid=True, broker_valid=True, system_armed=True,
        reasons={}, strategy_info=None)
    assert report["main_blocker"] == "NONE"
    assert report["secondary_blockers"] == []
    assert report["strategy"] == "structure" and report["score"] == 0


def test_market_universe_api():
    u = MarketUniverse()
    ids = u.get_all_ids()
    assert "btc_usdt" in ids and "eur_usd" in ids
    assert u.get_categories() == u.ASSET_CLASSES
    assert u.get_info("btc_usdt")["asset_class"] == "CRYPTO"
    assert u.get_info("nope") is None
    assert all(i["asset_class"] == "FOREX" for i in u.get_by_class("FOREX"))
    assert u.get_market_status("btc_usdt") == "OPEN"  # crypto always open
    assert u.get_market_status("nope") == "UNAVAILABLE"
    forex = u.get_market_status("eur_usd")
    assert forex in ("OPEN", "CLOSED")
    assert u.map_to_provider("btc_usdt", "gate") == "BTC/USDT"
    assert u.map_to_broker("btc_usdt", "gate") == "BTC/USDT"
    assert u.map_to_provider("nope", "gate") is None


class _FakeLayer:
    async def get_all_quotes(self, ids, catalog):
        return [ticker_model()]

    async def get_ohlcv(self, mid, tf, limit, catalog):
        return build_ohlcv()

    async def get_order_book(self, mid, catalog):
        return build_orderbook()

    async def get_trades(self, mid, catalog):
        return build_trades()

    async def get_cross_quotes(self, mid, catalog):
        return [build_quote("gate", 100.0), build_quote("bybit", 100.1)]


@pytest.mark.asyncio
async def test_data_engine_fetch_orchestration_with_fake_layer():
    engine = DataEngine()
    engine.layer = _FakeLayer()
    engine.universe = _small_universe()

    ticker = await engine.fetch_ticker("btc_usdt")
    assert ticker is not None and ticker["last"] > 0
    df = await engine.fetch_ohlcv("btc_usdt", "1m", 50)
    assert isinstance(df, pd.DataFrame) and not df.empty
    ob = await engine.fetch_order_book("btc_usdt")
    assert "bids" in ob
    trades = await engine.fetch_trades("btc_usdt")
    assert trades
    cross = await engine.fetch_cross_quotes("btc_usdt")
    assert len(cross) >= 2

    overview = await engine.get_market_overview()
    assert isinstance(overview, dict) and "CRYPTO" in overview

    # freshness gate
    assert engine.is_fresh(build_ticker(age_ms=100), "CRYPTO") is True
    assert engine.is_fresh(build_ticker(age_ms=60000), "CRYPTO") is False
    assert engine.is_fresh(None, "CRYPTO") is False


# --------------------------------------------------------------------------- #
# 6. CCXT providers via FakeExchange (offline)                                #
# --------------------------------------------------------------------------- #
def _patch_exchange(provider, exchange):
    provider.exchange = exchange
    return provider


@pytest.mark.asyncio
async def test_providers_quote_ohlcv_book_trades_offline():
    for provider_cls in (GateProvider, BybitProvider, BinanceProvider):
        provider = _patch_exchange(provider_cls(), FakeExchange())
        q = await provider.get_quote("BTC/USDT")
        assert q is not None and q.last == 100.0 and q.status == "LIVE"

        df = await provider.get_ohlcv("BTC/USDT", "1m", 10)
        assert not df.empty and list(df.columns) == \
            ['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume']

        ob = await provider.get_order_book("BTC/USDT")
        assert ob and "bids" in ob
        trades = await provider.get_recent_trades("BTC/USDT")
        assert trades and trades[0]["side"] == "buy"

        symbols = await provider.get_symbols()
        assert symbols == []  # FakeExchange has empty markets

        health = await provider.health_check()
        assert health["status"] == "ONLINE"
        assert health["latency_ms"] >= 0
        await provider.close()


@pytest.mark.asyncio
async def test_providers_failure_paths_offline():
    for provider_cls in (GateProvider, BybitProvider, BinanceProvider):
        provider = _patch_exchange(provider_cls(), FailingExchange())
        assert await provider.get_quote("BTC/USDT") is None
        assert await provider.get_ohlcv("BTC/USDT", "1m", 10) is not None
        health = await provider.health_check()
        assert health["status"] == "ERROR"
        assert "message" in health


# --------------------------------------------------------------------------- #
# 7. DB manager round-trips (temp DB)                                         #
# --------------------------------------------------------------------------- #
def test_db_settings_brokers_wallets_audit(tmp_path):
    db = DatabaseManager(str(tmp_path / "dbm.db"))

    # Settings round-trip
    db.save_settings({"min_signal_score": "85", "active_strategies": "structure,tape"})
    settings = db.get_settings()
    assert settings["min_signal_score"] == "85"
    db.set_setting("foo", "bar")
    assert db.get_settings()["foo"] == "bar"

    # Audit + signal archive
    db.log_audit("INFO", "TEST_ACTION", "hello", {"k": "v"})
    db.archive_signal({"market_id": "btc_usdt", "strategy": "tape", "entry": 1.0},
                      "EXECUTED", "")

    # Broker config lifecycle (plaintext — no FERNET in tests)
    db.save_broker_config("b1", "gate", "K", "S", None)
    assert len(db.get_all_broker_configs()) == 1
    assert db.get_active_broker_configs()[0]["broker_id"] == "b1"
    assert any(b["exchange_id"] == "gate" for b in db.get_broker_public_list())
    assert db.set_broker_active("b1", False) is True
    assert db.get_active_broker_configs() == []
    assert db.delete_broker("b1") is True
    assert db.delete_broker("missing") is False

    # Wallets
    db.save_wallet("w1", "METAMASK", "0x123", "mainnet")
    assert any(w["wallet_id"] == "w1" for w in db.get_wallets())
    assert db.delete_wallet("w1") is True

    # Balances + trades + history
    db.set_balance("DEMO", 5000.0)
    assert db.get_balance("DEMO") == 5000.0
    db.update_balance("DEMO", -10.0)
    assert db.get_balance("DEMO") == 4990.0
    db.save_trade({"mode": "DEMO", "symbol": "btc_usdt", "direction": "BUY",
                   "entry_price": 100.0, "quantity": 1.0, "sl": 99.0, "tp": 102.0,
                   "status": "CLOSED", "pnl": 5.0, "metadata": {"strategy": "structure"}})
    assert len(db.get_all_trades("DEMO")) == 1
    assert len(db.get_history("DEMO")) == 1
    db.delete_all_trades("DEMO")
    assert db.get_all_trades("DEMO") == []


def test_db_encryption_roundtrip(tmp_path, monkeypatch):
    import base64
    import os
    monkeypatch.setenv("FERNET_KEY", base64.urlsafe_b64encode(b"0" * 32).decode())
    db = DatabaseManager(str(tmp_path / "enc.db"))
    cipher = db.encrypt("secret-value")
    assert cipher and cipher != "secret-value"
    assert db.decrypt(cipher) == "secret-value"
    assert db.encrypt(None) is None
    assert db.decrypt(None) is None
    assert os.environ["FERNET_KEY"]  # monkeypatch active
