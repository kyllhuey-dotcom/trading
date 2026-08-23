"""Offline unit tests targeting remaining uncovered functions/branches."""
import asyncio
import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

from api.engines.capital_profiles import recommend_from_audit, resolve_bracket
from api.engines.data_health import DataHealthMonitor
from api.engines.data_providers.binance_provider import BinanceProvider
from api.engines.data_providers.bybit_provider import BybitProvider
from api.engines.data_providers.gate_provider import GateProvider
from api.engines.data_providers.yahoo_provider import YahooProvider
from api.engines.market_universe import MarketUniverse
from api.engines.provider_capabilities import (
    capabilities_for,
    classify_quote_status,
    looks_like_quota_error,
)
from api.engines.radar import (
    filter_assets,
    format_data_age,
    prepare_radar,
    sort_assets,
)
from api.engines.scanner_engine import ScannerEngine
from api.json_logging import JsonFormatter, _json_default, structured_log


# ---- Yahoo (mocked, no network) ------------------------------------------- #
def _ohlcv_frame(n=16):
    rows = []
    base = 1_700_000_000_000
    for i in range(n):
        rows.append([base + i * 60_000, 10.0, 11.0, 9.0, 10.5, 100.0])
    return pd.DataFrame(rows, columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"])


def test_yahoo_normalize_and_symbol_frame():
    yp = YahooProvider("FOREX")
    assert yp._normalize_history(pd.DataFrame()).empty
    assert yp._normalize_history(None).empty
    bad = pd.DataFrame({"Close": [1.0]})
    assert yp._normalize_history(bad).empty
    good = pd.DataFrame({
        "Date": [pd.Timestamp("2024-01-01")],
        "Open": [1], "High": [2], "Low": [0.5], "Close": [1.5], "Volume": [10],
    })
    norm = yp._normalize_history(good)
    assert not norm.empty
    small_ts = pd.DataFrame({
        "ts": [1_700_000_000],
        "Open": [1], "High": [2], "Low": [0.5], "Close": [1.5], "Volume": [10],
    })
    assert not yp._normalize_history(small_ts).empty

    empty = yp._symbol_frame(pd.DataFrame(), "A")
    assert empty.empty
    single = pd.DataFrame({"Open": [1], "Close": [1]})
    assert list(yp._symbol_frame(single, "A").columns) == ["Open", "Close"]
    arrays = [["AAPL", "AAPL", "MSFT", "MSFT"], ["Open", "Close", "Open", "Close"]]
    mi = pd.MultiIndex.from_arrays(arrays)
    df = pd.DataFrame([[1, 2, 3, 4]], columns=mi)
    framed = yp._symbol_frame(df, "AAPL")
    assert "Close" in framed.columns
    framed2 = yp._symbol_frame(df.swaplevel(0, 1, axis=1), "MSFT")
    assert not framed2.empty
    assert yp._resample_15m(pd.DataFrame()).empty
    resampled = yp._resample_15m(_ohlcv_frame(30))
    assert len(resampled) == 2


async def test_yahoo_quote_ohlcv_health_mocked():
    yp = YahooProvider("FOREX")
    frame = _ohlcv_frame()
    yp._cache_frame("EURUSD=X", frame)
    assert (await yp.get_quote("EURUSD=X")).status == "DELAYED"
    assert not (await yp.get_ohlcv("EURUSD=X", "1m", 5)).empty
    health = await yp.health_check()
    assert health["status"] == "ONLINE" and health.get("cached")

    yp2 = YahooProvider("FOREX")
    yp2._record_failure("X")
    yp2._failure_state["X"] = (3, time.time())
    assert yp2._in_cooldown("X")
    assert await yp2.get_quote("X") is None
    assert (await yp2.get_ohlcv("X")).empty

    yp3 = YahooProvider("FOREX")
    yp3._cache_frame("Z", pd.DataFrame())  # records failure
    with patch.object(yp3, "prepare_cycle", AsyncMock()), \
         patch.object(yp3, "_single_history", AsyncMock(return_value=pd.DataFrame())):
        assert await yp3.get_quote("Z") is None

    yp4 = YahooProvider("FOREX")
    with patch.object(yp4, "prepare_cycle", AsyncMock()), \
         patch.object(yp4, "_single_history", AsyncMock(return_value=frame)):
        q = await yp4.get_quote("AAA")
        assert q is not None
        df = await yp4.get_ohlcv("AAA", "1h", 3)
        assert not df.empty

    async def boom(*a, **k):
        raise RuntimeError("yf")

    yp5 = YahooProvider("FOREX")
    with patch("api.engines.data_providers.yahoo_provider.asyncio.to_thread", boom):
        assert (await yp5._single_history("EURUSD=X")).empty

    with patch("api.engines.data_providers.yahoo_provider.yf.download", side_effect=RuntimeError("dl")):
        await yp5.prepare_cycle(["EURUSD=X"])
        assert "EURUSD=X" in yp5._failure_state

    assert await yp.get_symbols() == []


# ---- Market universe hours ------------------------------------------------- #
def test_market_universe_status_branches(monkeypatch):
    u = MarketUniverse()
    assert u.get_market_status("nope") == "UNAVAILABLE"
    assert u.get_market_status("btc_usdt") == "OPEN"
    assert u.get_categories()
    assert u.get_by_class("CRYPTO")
    assert u.map_to_provider("btc_usdt", "gate")
    assert u.map_to_broker("btc_usdt", "gate") is not None
    assert u.map_to_provider("nope", "gate") is None
    assert u.map_to_broker("nope", "gate") is None

    class FakeDT:
        def __init__(self, weekday, hour, minute=0):
            self._wd = weekday
            self.hour = hour
            self.minute = minute

        def weekday(self):
            return self._wd

    def fake_now(tz):
        return FakeDT(*fake_now.params)

    fake_now.params = (5, 12)  # Saturday
    monkeypatch.setattr(
        "api.engines.market_universe.datetime",
        type("D", (), {"now": staticmethod(fake_now)}),
    )
    assert u.get_market_status("eur_usd") == "CLOSED"
    fake_now.params = (6, 10)
    assert u.get_market_status("eur_usd") == "CLOSED"
    fake_now.params = (4, 23)
    assert u.get_market_status("eur_usd") == "CLOSED"
    fake_now.params = (2, 12)
    assert u.get_market_status("eur_usd") == "OPEN"

    fake_now.params = (5, 12)
    assert u.get_market_status("aapl") == "CLOSED"
    fake_now.params = (1, 10, 0)
    assert u.get_market_status("aapl") == "OPEN"
    fake_now.params = (1, 8, 0)
    assert u.get_market_status("aapl") == "CLOSED"

    fake_now.params = (5, 12)
    assert u.get_market_status("dax") == "CLOSED"
    fake_now.params = (1, 10)
    assert u.get_market_status("dax") == "OPEN"
    fake_now.params = (1, 8)
    assert u.get_market_status("dax") == "CLOSED"

    fake_now.params = (5, 12)
    assert u.get_market_status("nikkei_225") == "CLOSED"
    fake_now.params = (1, 10)
    assert u.get_market_status("nikkei_225") == "OPEN"
    fake_now.params = (1, 16)
    assert u.get_market_status("nikkei_225") == "CLOSED"

    fake_now.params = (5, 12)
    assert u.get_market_status("gold") == "CLOSED"
    fake_now.params = (1, 17)
    assert u.get_market_status("gold") == "CLOSED"
    fake_now.params = (1, 12)
    assert u.get_market_status("gold") == "OPEN"

    # BONDS / ETFS fall through to CLOSED
    assert u.get_market_status("spy") == "CLOSED"


# ---- Provider capabilities ------------------------------------------------- #
def test_provider_capabilities_classify():
    assert capabilities_for("yahoo_forex")["provider_id"] == "yahoo"
    assert capabilities_for("unknown-x")["realtime_capable"] is False
    assert looks_like_quota_error("Thank you for using Alpha Vantage")
    assert classify_quote_status(None)["status"] == "DATA_UNAVAILABLE"
    assert classify_quote_status({"status": "ERROR", "timestamp": "bad"})["status"] == "ERROR"
    assert classify_quote_status({"status": "LIVE", "reason": "quota exceeded", "timestamp": 1})["reason"] == "PROVIDER_QUOTA_EXCEEDED"
    now = int(time.time() * 1000)
    assert classify_quote_status({"status": "DELAYED", "source": "Yahoo", "timestamp": now})["status"] == "DELAYED"
    assert classify_quote_status({"status": "LIVE", "source": "gate", "timestamp": now, "last": 1})["status"] == "LIVE"
    stale = classify_quote_status(
        {"status": "OK", "source": "gate", "timestamp": now - 20 * 60 * 1000, "last": 1},
        "gate", now_ms=now,
    )
    assert stale["status"] == "STALE"
    delayed_caps = classify_quote_status({"last": 1, "source": "twelvedata", "timestamp": now}, "twelvedata")
    assert delayed_caps["status"] == "DELAYED"
    live_last = classify_quote_status({"last": 10, "source": "binance", "timestamp": now}, "binance")
    assert live_last["status"] == "LIVE"
    missing = classify_quote_status({"source": "binance", "timestamp": now}, "binance")
    assert missing["status"] == "DATA_UNAVAILABLE"


# ---- Radar ----------------------------------------------------------------- #
def test_radar_helpers():
    assert format_data_age(None) == "—"
    assert format_data_age("x") == "—"
    assert format_data_age(500).endswith("ms")
    assert format_data_age(2500).endswith("s")
    assert format_data_age(120_000).endswith("m")
    assets = [
        {"score": None, "name": "z"},
        {"score": 90, "name": "a", "asset_class": "CRYPTO", "realtime_source": True, "underlying": "u"},
        {"score": 70, "name": "b", "asset_class": "FOREX", "realtime_source": False, "underlying": "u",
         "data_age_ms": "bad"},
    ]
    assert sort_assets(assets, "score", True)[0]["score"] in (90, None)
    assert filter_assets(assets, "ge80")
    assert filter_assets(assets, "ge90")
    assert filter_assets(assets, "crypto")
    assert filter_assets(assets, "live")
    rows = prepare_radar(assets, sort="score", order="asc", filter_mode="all", live_only=True)
    assert all(r.get("realtime_source") for r in rows)


# ---- Data health guess class ---------------------------------------------- #
def test_data_health_guess_class():
    m = DataHealthMonitor({})
    assert m._guess_class("gate") == "CRYPTO"
    assert m._guess_class("twelvedata") == "TRADFI"
    assert m._guess_class("yahoo_forex") == "FOREX"
    assert m._guess_class("yahoo_indices") == "INDICES"
    assert m._guess_class("yahoo_commodities") == "COMMODITIES"
    assert m._guess_class("custom") == "MIXED"


# ---- Capital audit recommendations ---------------------------------------- #
def test_recommend_from_audit_all_verdicts():
    assert resolve_bracket(-1).name == "MICRO"
    empty = recommend_from_audit(None, 5)
    assert empty["health_verdict"].startswith("N/A")
    assert recommend_from_audit({"modes": {}}, 5)["per_strategy"] == {}

    audit = {
        "modes": {
            "DEMO": {
                "by_strategy": {
                    "rsi": {
                        "trades": 20, "wins": 4, "net_pnl": -10,
                        "avg_win": 2, "avg_loss": 3, "cost_leaks": 0,
                    },
                    "tape": {
                        "trades": 10, "wins": 6, "net_pnl": 5,
                        "avg_win": 1, "avg_loss": 2, "cost_leaks": 0,
                    },
                    "arb": {
                        "trades": 8, "wins": 5, "net_pnl": 4,
                        "avg_win": 3, "avg_loss": 1, "cost_leaks": 2,
                    },
                    "ok": {
                        "trades": 12, "wins": 8, "net_pnl": 20,
                        "avg_win": 4, "avg_loss": 1, "cost_leaks": 0,
                    },
                    "zero": {"trades": 0},
                    "review": {
                        "trades": 5, "wins": 3, "net_pnl": 0,
                        "avg_win": 0, "avg_loss": 0, "cost_leaks": 0,
                    },
                }
            }
        }
    }
    rec = recommend_from_audit(audit, 100)
    assert rec["per_strategy"]["rsi"]["action"] == "DISABLE_OR_RAISE_SELECTIVITY"
    assert rec["per_strategy"]["tape"]["action"] == "WIDEN_TAKE_PROFIT"
    assert rec["per_strategy"]["arb"]["action"] == "TIGHTEN_COST_FILTER"
    assert rec["per_strategy"]["ok"]["action"] == "KEEP"
    assert rec["per_strategy"]["review"]["action"] == "REVIEW"
    assert rec["health_verdict"] in ("UNHEALTHY", "NEEDS_TAILORING", "HEALTHY")


# ---- Crypto provider exception paths -------------------------------------- #
class BoomEx:
    async def load_markets(self):
        raise RuntimeError("x")

    async def fetch_ticker(self, *a, **k):
        raise RuntimeError("x")

    async def fetch_ohlcv(self, *a, **k):
        raise RuntimeError("x")

    async def fetch_order_book(self, *a, **k):
        raise RuntimeError("x")

    async def fetch_trades(self, *a, **k):
        raise RuntimeError("x")

    async def close(self):
        return None


async def test_ccxt_named_providers_error_paths():
    for cls in (BinanceProvider, BybitProvider, GateProvider):
        p = cls.__new__(cls)
        p.exchange = BoomEx()
        p.source_name = "X"
        p._last_health_check = {}
        assert await p.get_symbols() == []
        assert await p.get_quote("BTC/USDT") is None
        assert (await p.get_ohlcv("BTC/USDT")).empty
        assert await p.get_order_book("BTC/USDT") is None
        assert await p.get_recent_trades("BTC/USDT") is None
        health = await p.health_check()
        assert health["status"] == "ERROR"
        await p.close()


# ---- Scanner + logging ----------------------------------------------------- #
async def test_scanner_unknown_and_invalid_settings():
    universe = MagicMock()
    universe.get_info.return_value = None
    universe.get_all_ids.return_value = []
    data = MagicMock()
    data.universe = universe
    sc = ScannerEngine(data, MagicMock(), MagicMock(), MagicMock())
    sc.apply_settings({"max_spread_pct": "nope"})
    row = await sc.scan_asset("zzz", asyncio.Semaphore(1))
    assert row["status"] == "UNKNOWN_SYMBOL"
    assert await sc.scan_all() == []


def test_json_logging_helpers():
    assert _json_default({1, 2}) == [1, 2] or set(_json_default({1, 2})) == {1, 2}
    assert isinstance(_json_default(datetime(2024, 1, 1)), str)
    assert isinstance(_json_default(object()), str)
    fmt = JsonFormatter()
    rec = type("R", (), {})()
    rec.created = time.time()
    rec.levelname = "INFO"
    rec.name = "t"
    rec.getMessage = lambda: "hi"
    rec.exc_info = None
    rec.module = rec.funcName = rec.lineno = rec.process = rec.threadName = rec.pathname = None
    rec.__dict__.update({"name": "t", "custom": 1, "_skip": True})
    line = fmt.format(rec)
    assert "hi" in line
    log = MagicMock()
    structured_log(log, 20, "m", name="reserved", foo=1)
    log.log.assert_called()
