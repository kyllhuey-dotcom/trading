"""v3.3.2 — 100 % REAL data campaign (D1–D4) + transient-only read retry.

Covers:
- persisted (last-good) OHLCV carries the ``stale`` marker (D3);
- fresh provider frames are explicitly marked not-stale (D3);
- RSI is REFUSED on stale persisted candles (D3);
- a persisted quote is classified STALE, never LIVE (D3);
- the scanner row is STALE when its candles are cached and carries the
  REAL 1h closes as sparkline (D1/D3);
- the realtime data audit math: pools, tolerances, freshness, PASS/FAIL.
"""
from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import MagicMock

import pandas as pd
import pytest

from api.engines.data_layer import DataLayer, ohlcv_is_stale
from api.engines.provider_capabilities import classify_quote_status
from api.engines.strategies.rsi_mean_reversion import RSIMeanReversionStrategy
from tests.mocks import FakeDataEngine, FakeNewsEngine, MarketUniverse
from api.engines.scanner_engine import ScannerEngine
from tests.test_rsi_strategy import _reversal_frame


# --------------------------------------------------------------------------- #
# D3 — persisted candles carry the stale marker                                #
# --------------------------------------------------------------------------- #
class _FakeDB:
    def __init__(self, ohlcv_rows=None, quote_payload=None):
        self.ohlcv_rows = ohlcv_rows
        self.quote_payload = quote_payload
        self.saved = []

    def save_last_ohlcv(self, market_id, timeframe, rows, saved_at=None):
        self.saved.append((market_id, timeframe, rows))

    def load_last_ohlcv(self, market_id, timeframe, max_age_s):
        return self.ohlcv_rows

    def save_last_quote(self, market_id, payload, saved_at=None):
        pass

    def load_last_quote(self, market_id, max_age_s):
        return self.quote_payload


def _candle_rows(n=5, price=100.0, age_s=3600.0):
    now = time.time()
    rows = []
    for i in range(n):
        ts = int((now - age_s + i * 60) * 1000)
        rows.append({"Timestamp": ts, "Open": price, "High": price + 1,
                     "Low": price - 1, "Close": price + i * 0.1, "Volume": 10.0})
    return rows


def test_persisted_ohlcv_is_marked_stale():
    layer = DataLayer()
    rows = _candle_rows(age_s=3600.0)
    layer.attach_persistence(_FakeDB(ohlcv_rows=rows))
    frame = layer._load_persisted_ohlcv("btc_usdt", "1m")
    assert frame is not None and len(frame) == 5
    assert frame.attrs["stale"] is True
    assert frame.attrs["stale_reason"] == "persisted_cache"
    assert ohlcv_is_stale(frame) is True
    # the LAST candle is 3600 - 4*60 = 3360 s old (± slack for test runtime)
    assert 3300.0 <= frame.attrs["candles_age_s"] <= 3450.0


def test_fresh_provider_frame_is_marked_not_stale():
    layer = DataLayer()

    class _Provider:
        async def get_ohlcv(self, symbol, timeframe, limit):
            df = pd.DataFrame(_candle_rows(age_s=60.0))
            df.attrs["stale"] = False
            return df

    u = MarketUniverse()
    layer.register_provider("gate", _Provider())
    frame = asyncio.run(layer.get_ohlcv("btc_usdt", "1m", 30, u))
    assert not frame.empty
    assert frame.attrs.get("stale") is False
    assert ohlcv_is_stale(frame) is False


def test_ohlcv_is_stale_helper_never_raises():
    assert ohlcv_is_stale(None) is False
    assert ohlcv_is_stale(pd.DataFrame({"Close": [1.0]})) is False
    frame = pd.DataFrame({"Close": [1.0]})
    frame.attrs["stale"] = True
    assert ohlcv_is_stale(frame) is True


# --------------------------------------------------------------------------- #
# D3 — RSI is refused on stale persisted real data                             #
# --------------------------------------------------------------------------- #
def test_rsi_refused_on_stale_persisted_candles():
    strategy = RSIMeanReversionStrategy()
    frame = _reversal_frame("BUY")  # a PERFECT signal frame…
    fresh = strategy.generate_signal("btc_usdt", frame)
    assert fresh["status"] == "SIGNAL_DETECTED"

    frame.attrs["stale"] = True
    frame.attrs["candles_age_s"] = 7200.0
    stale = strategy.generate_signal("btc_usdt", frame)
    assert stale["status"] == "NO_TRADE"
    assert stale["block_reason"] == "STALE_DATA"
    assert "stale" in stale["reason"].lower()
    assert stale["metadata"]["stale_age_s"] == 7200.0


def test_classify_persisted_quote_is_stale_never_live():
    now_ms = int(time.time() * 1000)
    # cached 10 minutes ago: the pre-v3.3.2 age fallback used to call this
    # LIVE (realtime provider, < 15 min, has a price). It is STALE now.
    quote = {"status": "STALE", "source": "binance (cached)", "last": 60000.0,
             "timestamp": now_ms - 10 * 60 * 1000}
    classified = classify_quote_status(quote, "binance")
    assert classified["status"] == "STALE"
    assert classified["tradable"] is False
    assert classified["realtime"] is False
    # three days cached stays STALE too
    quote_old = dict(quote, timestamp=now_ms - 3 * 24 * 3600 * 1000)
    assert classify_quote_status(quote_old, "binance")["status"] == "STALE"
    # a LIVE quote from a realtime feed is still LIVE
    quote_live = {"status": "LIVE", "source": "binance", "last": 60000.0,
                  "timestamp": now_ms - 2000}
    assert classify_quote_status(quote_live, "binance")["status"] == "LIVE"


# --------------------------------------------------------------------------- #
# D1/D3 — scanner: STALE row + REAL 1h sparkline                                #
# --------------------------------------------------------------------------- #
def _small_universe():
    u = MarketUniverse()
    u.universe = {"btc_usdt": u.universe["btc_usdt"]}
    return u


class _StaleDataEngine(FakeDataEngine):
    """1m OHLCV restored from the persisted cache; 1h OHLCV fresh."""

    def __init__(self, universe, ohlcv_1h_closes):
        super().__init__(universe)
        self._ohlcv_1h = pd.DataFrame({
            "Timestamp": [int(time.time() * 1000) - (24 - i) * 3600 * 1000
                          for i in range(len(ohlcv_1h_closes))],
            "Open": ohlcv_1h_closes, "High": ohlcv_1h_closes,
            "Low": ohlcv_1h_closes, "Close": ohlcv_1h_closes,
            "Volume": [10.0] * len(ohlcv_1h_closes),
        })

    async def fetch_ohlcv(self, market_id, timeframe="1m", limit=100):
        if timeframe == "1h":
            return self._ohlcv_1h.tail(limit).reset_index(drop=True)
        frame = self.ohlcv.copy()
        frame.attrs["stale"] = True
        frame.attrs["stale_reason"] = "persisted_cache"
        frame.attrs["candles_age_s"] = 3600.0
        return frame


def _scanner(data):
    analysis = MagicMock()
    analysis.identify_structure = MagicMock(
        return_value={"trend": "BULLISH", "market_state": "TREND"})
    signal = MagicMock()
    signal.generate_signal = MagicMock(return_value={
        "status": "NO_TRADE", "strategy": "rsi", "score": 0,
        "direction": None, "entry": 0.0, "sl": 0.0, "tp": 0.0,
        "block_reason": "STALE_DATA", "reason": "RSI refused on stale",
    })
    return ScannerEngine(data, analysis, signal,
                         FakeNewsEngine(allowed=True), max_concurrent=2)


@pytest.mark.asyncio
async def test_scan_row_stale_when_candles_cached():
    data = _StaleDataEngine(_small_universe(),
                            ohlcv_1h_closes=[float(60000 + i) for i in range(30)])
    res = await _scanner(data).scan_asset("btc_usdt", asyncio.Semaphore(1))
    assert res["status"] == "STALE"
    assert res["block_reason"] == "STALE_DATA"
    assert res["tradable"] is False
    assert res["signal"] == "NO_TRADE"
    # D1: the sparkline is the REAL latest 24 hourly closes
    assert res["sparkline"] == [float(60000 + i) for i in range(6, 30)]
    assert res["sparkline_stale"] is False


@pytest.mark.asyncio
async def test_scan_row_sparkline_empty_without_data():
    data = _StaleDataEngine(_small_universe(), ohlcv_1h_closes=[])
    res = await _scanner(data).scan_asset("btc_usdt", asyncio.Semaphore(1))
    assert res["sparkline"] == []
    # no fabricated price either (D2): the mock ticker has a last, so the
    # row price is the REAL ticker last — assert it equals it exactly.
    assert res["price"] == data.ticker["last"]


def test_scan_row_price_never_fabricated():
    """D2: a ticker without a last price yields price None, not 0.0."""
    data = _StaleDataEngine(_small_universe(),
                            ohlcv_1h_closes=[float(60000 + i) for i in range(30)])
    data.ticker["last"] = None

    async def run():
        return await _scanner(data).scan_asset("btc_usdt", asyncio.Semaphore(1))

    res = asyncio.run(run())
    assert res["price"] is None


@pytest.mark.asyncio
async def test_scan_row_negative_change_is_real_data():
    """D2: a negative 24h change is preserved (only the price is > 0)."""
    data = _StaleDataEngine(_small_universe(),
                            ohlcv_1h_closes=[float(60000 + i) for i in range(30)])
    data.ticker["change_24h"] = -3.25
    res = await _scanner(data).scan_asset("btc_usdt", asyncio.Semaphore(1))
    assert res["change"] == -3.25
    assert res["price"] == data.ticker["last"]


# --------------------------------------------------------------------------- #
# §3 — realtime data audit math (recorded payload shapes, offline)             #
# --------------------------------------------------------------------------- #
# These tests feed the audit the REAL API response shapes (recorded fixtures)
# to validate the POOL MATH and the PASS/FAIL verdicts offline. They are NOT
# a substitute for the live campaign — the live PASS runs in
# .github/workflows/realtime-data-audit.yml.
import scripts.realtime_data_audit as audit  # noqa: E402


class _FakeResp:
    def __init__(self, payload=None, text=None, status_code=200):
        self._payload = payload
        self._text = text
        self.status_code = status_code

    def json(self):
        return self._payload

    @property
    def text(self):
        return self._text if self._text is not None else json.dumps(self._payload or {})


def _good_payloads():
    now = time.time()
    def _iso(ts):
        from datetime import datetime, timezone
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "binance": {"lastPrice": "60000.50", "closeTime": int((now - 1) * 1000)},
        "gate": {"result": [{"last": "60000.10", "timestamp": int(now - 1)}]},
        "bybit": {"result": {"list": [{"lastPrice": "60000.30",
                                       "time": int((now - 2) * 1000)}]}},
        "okx": {"data": [{"last": "60000.20", "ts": int((now - 1) * 1000)}]},
        "kraken": {"result": {"XBTUSD": [["60000.90", "0.5", str(now - 0.5), "b", "", 0]],
                              "last": now - 0.5}},
        "coinbase": {"data": {"price": "60001.00", "time": _iso(now - 1)}},
        "coingecko": {"bitcoin": {"usd": 60010.0, "eur": 55565.0,
                                  "last_updated_at": {"usd": int(now - 20)}}},
        "yahoo": {"chart": {"result": [{"meta": {
            "symbol": "AAPL", "exchangeName": "NMS", "regularMarketPrice": 193.5,
            "regularMarketTime": int(now - 900)}}]}},
        "stooq": None,  # text CSV, built below
        "frankfurter": {"base": "EUR", "date": "2026-08-28",
                        "rates": {"USD": 1.0805}},
    }


def _fake_get(payloads):
    stooq_text = ("Symbol,Date,Time,Open,High,Low,Close,Volume\n"
                  "AAPL,2026-08-28,16:00:00,190.0,195.0,189.0,193.6,1000000")

    def _get(url, params=None, headers=None):
        if audit.BINANCE_URL in url:
            return _FakeResp(payloads["binance"])
        if audit.GATE_URL in url:
            return _FakeResp(payloads["gate"])
        if audit.BYBIT_URL in url:
            return _FakeResp(payloads["bybit"])
        if audit.OKX_URL in url:
            return _FakeResp(payloads["okx"])
        if audit.KRAKEN_URL in url:
            return _FakeResp(payloads["kraken"])
        if audit.COINBASE_URL in url:
            return _FakeResp(payloads["coinbase"])
        if audit.COINGECKO_URL.split("?")[0] in url:
            return _FakeResp(payloads["coingecko"])
        if audit.YAHOO_URL.split("?")[0] in url:
            return _FakeResp(payloads["yahoo"])
        if audit.STOOQ_URL in url:
            return _FakeResp(text=stooq_text)
        if audit.FRANKFURTER_URL in url:
            return _FakeResp(payloads["frankfurter"])
        raise AssertionError(f"unexpected URL {url}")

    return _get


def test_audit_pass_on_agreeing_feeds(monkeypatch):
    payloads = _good_payloads()
    monkeypatch.setattr(audit, "_get", _fake_get(payloads))
    report = audit.run_audit(max_age_s=10.0)
    assert report["overall_status"] == "PASS", json.dumps(report, indent=1)
    for name, entry in report["endpoints"].items():
        assert entry["status"] == "PASS", (name, entry.get("detail"))
    assert report["checks"]["crypto_usdt_pool"]["status"] == "PASS"
    assert report["checks"]["crypto_usd_pool"]["status"] == "PASS"
    assert report["checks"]["cross_reference_coingecko"]["status"] == "PASS"
    assert report["checks"]["cross_reference_pools"]["status"] == "PASS"
    assert report["checks"]["tradfi_aapl"]["status"] == "PASS"
    assert report["checks"]["fx_reference_eurusd"]["status"] == "PASS"


def test_audit_fails_when_feeds_disagree_beyond_tolerance(monkeypatch):
    payloads = _good_payloads()
    # Gate 0.5 % away from the other USDT feeds (> 0.10 % tolerance)
    payloads["gate"]["result"][0]["last"] = "60300.00"
    monkeypatch.setattr(audit, "_get", _fake_get(payloads))
    report = audit.run_audit(max_age_s=10.0)
    assert report["overall_status"] == "FAIL"
    check = report["checks"]["crypto_usdt_pool"]
    assert check["status"] == "FAIL"
    assert check["value_pct"] > audit.TOLERANCES["crypto_max_dev_pct"]


def test_audit_fails_on_stale_exchange_feed(monkeypatch):
    payloads = _good_payloads()
    now = time.time()
    payloads["binance"]["closeTime"] = int((now - 45) * 1000)  # 45 s old
    monkeypatch.setattr(audit, "_get", _fake_get(payloads))
    report = audit.run_audit(max_age_s=10.0)
    assert report["overall_status"] == "FAIL"
    assert report["endpoints"]["binance"]["status"] == "FAIL"
    assert "age" in report["endpoints"]["binance"]["detail"]


def test_audit_fails_on_unreachable_endpoint(monkeypatch):
    payloads = _good_payloads()
    real_get = _fake_get(payloads)

    def _get(url, params=None, headers=None):
        if audit.OKX_URL in url:
            raise ConnectionError("network unreachable")
        return real_get(url, params=params, headers=headers)

    monkeypatch.setattr(audit, "_get", _get)
    report = audit.run_audit(max_age_s=10.0)
    assert report["overall_status"] == "FAIL"
    assert report["endpoints"]["okx"]["status"] == "ERROR"
    assert "network unreachable" in report["endpoints"]["okx"]["detail"]


def test_audit_tolerances_are_never_relaxed():
    # The campaign's numeric contract — locked by test.
    assert audit.TOLERANCES["crypto_max_dev_pct"] == 0.10
    assert audit.TOLERANCES["cross_reference_max_pct"] == 1.00
    assert audit.TOLERANCES["tradfi_max_pct"] == 0.70
    assert audit.TOLERANCES["fx_reference_max_pct"] == 0.70
    assert audit.DEFAULT_MAX_AGE_S == 10.0
