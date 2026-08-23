"""Unit tests for scan_contract helpers."""
from api.engines.scan_contract import (
    classify_block_reason,
    merge_universe_rows,
    placeholder_row,
    summarize_scan,
)


class FakeUniverse:
    def __init__(self, ids):
        self.ids = ids

    def get_all_ids(self):
        return list(self.ids)

    def get_info(self, symbol):
        return {"display_symbol": symbol.upper(), "asset_class": "CRYPTO", "name": symbol}


def test_placeholder_row_defaults():
    row = placeholder_row("btc_usdt")
    assert row["tradable"] is False
    assert row["strategy"] == "rsi"
    assert row["signal_data"]["market_id"] == "btc_usdt"


def test_merge_fills_missing_and_keeps_extras():
    uni = FakeUniverse(["a", "b"])
    rows = [
        {"symbol": "a", "status": "LIVE", "score": 90, "tradable": True, "signal": "SIGNAL_DETECTED"},
        {"market_id": "orphan", "score": 1},
        {},
    ]
    merged = merge_universe_rows(rows, uni)
    symbols = [r.get("symbol") or r.get("market_id") for r in merged]
    assert "a" in symbols and "b" in symbols
    assert "orphan" in symbols
    empty = merge_universe_rows(None, None)
    assert empty == []


def test_summarize_scan_counts():
    rows = [
        {"status": "LIVE", "signal": "SIGNAL_DETECTED", "tradable": True, "score": 90},
        {"status": "DATA_UNAVAILABLE", "signal": "NO_TRADE", "tradable": False, "score": 0},
        {"status": "ERROR", "signal_data": {"status": "NO_TRADE"}, "score": 10},
        {"status": "DELAYED", "signal": "NO_TRADE", "score": 20},
    ]
    s = summarize_scan(rows, total=10)
    assert s["markets_total"] == 10
    assert s["markets_unavailable"] == 1
    assert s["markets_error"] == 1
    assert s["markets_available"] == 2
    assert s["rsi_signals"] == 1
    assert s["markets_tradable"] == 1
    assert s["signals_ge_floor"] >= 1


def test_classify_block_reason_branches():
    assert classify_block_reason(running=False) == "SYSTEM_NOT_RUNNING"
    assert classify_block_reason(armed=False) == "ENGINE_DISARMED"
    assert classify_block_reason(scan_timeout=True) == "SCAN_TIMEOUT"
    assert classify_block_reason(scanning=True) == "SCAN_IN_PROGRESS"
    assert classify_block_reason(quota=True, ticker={"last": 1}) == "PROVIDER_QUOTA_EXCEEDED"
    assert classify_block_reason(provider_error=True, ticker={"last": 1}) == "PROVIDER_ERROR"
    assert classify_block_reason(ticker=None) == "DATA_UNAVAILABLE"
    assert classify_block_reason(ticker={"last": 1}, delayed=True) == "NON_REALTIME_SOURCE"
    assert classify_block_reason(
        ticker={"last": 1}, news={"status": "DATA_UNAVAILABLE", "news_ok": False}
    ) == "CALENDAR_UNAVAILABLE"
    assert classify_block_reason(ticker={"last": 1}, news={"news_ok": False}) == "NEWS_BLOCKED"
    assert classify_block_reason(
        ticker={"last": 1}, signal={"reason": "RSI_NO_CROSS"}
    ) == "RSI_NO_CROSS"
    assert classify_block_reason(
        ticker={"last": 1}, signal={"reason": "INSUFFICIENT candles"}
    ) == "INSUFFICIENT_CANDLES"
    assert classify_block_reason(
        ticker={"last": 1}, signal={"reason": "BELOW MINIMUM SCORE 89", "min_score_applied": 89}
    ) == "VOLATILE_THRESHOLD_89"
    assert classify_block_reason(
        ticker={"last": 1}, signal={"reason": "SPREAD too wide"}
    ) == "SPREAD_TOO_HIGH"
    assert classify_block_reason(
        ticker={"last": 1}, diagnosis={"checks": {"MARKET_OPEN": "FAIL"}}
    ) == "MARKET_CLOSED"
    assert classify_block_reason(
        ticker={"last": 1}, diagnosis={"checks": {"SPREAD_VALID": "FAIL"}}
    ) == "SPREAD_TOO_HIGH"
    assert classify_block_reason(
        ticker={"last": 1}, diagnosis={"checks": {"LIQUIDITY_VALID": "FAIL"}}
    ) == "LIQUIDITY_INVALID"
    assert classify_block_reason(
        ticker={"last": 1}, diagnosis={"checks": {"NEWS_CLEAR": "FAIL"}}
    ) == "NEWS_BLOCKED"
    assert classify_block_reason(
        ticker={"last": 1}, diagnosis={"main_blocker": "CUSTOM"}
    ) == "CUSTOM"
