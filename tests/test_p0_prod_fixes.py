"""Production outage fixes (2026-08-23) — 0 trades + WS disconnects in prod.

P0-1  Calendar outage default `block_tradfi_only` (crypto keeps trading).

Subsequent sections (P0-2/P0-3/P0-4/P1) are appended with their fixes —
one theme per commit, every commit stays green offline.
"""
from unittest.mock import AsyncMock

import pytest

from api.engines.db_manager import DatabaseManager
from api.engines.news_engine import NewsEngine
from api.engines.opportunity_ranker import rank_opportunities
from api.engines.settings_schema import SETTINGS_SPEC, ensure_defaults
import api.index as idx


# --------------------------------------------------------------------------- #
# P0-1 — Calendar outage: default block_tradfi_only
# --------------------------------------------------------------------------- #
def test_settings_schema_default_is_block_tradfi_only():
    """P0-1: the schema default no longer blocks crypto on calendar outage."""
    assert SETTINGS_SPEC["news_unavailable_policy"]["default"] == "block_tradfi_only"
    assert SETTINGS_SPEC["news_unavailable_policy"]["choices"] == (
        "block_all", "block_tradfi_only", "allow_all")


def test_news_engine_default_policy_is_block_tradfi_only():
    engine = NewsEngine()
    assert engine.news_unavailable_policy == "block_tradfi_only"


@pytest.mark.asyncio
async def test_calendar_outage_default_crypto_allowed_forex_blocked():
    """P0-1: calendar HS + default policy → CRYPTO news_ok True, FOREX False."""
    engine = NewsEngine()  # no explicit policy: production default path
    assert engine.news_unavailable_policy == "block_tradfi_only"
    engine.provider.fetch_events = AsyncMock(return_value=[])

    crypto = await engine.check_trading_allowed(asset_class="CRYPTO")
    assert crypto["status"] == "DATA_UNAVAILABLE"
    assert crypto["news_ok"] is True          # crypto trades during outage
    assert crypto["trading_allowed"] is True   # 24/7 market, session always ok

    forex = await engine.check_trading_allowed(asset_class="FOREX")
    assert forex["status"] == "DATA_UNAVAILABLE"
    assert forex["news_ok"] is False
    assert forex["trading_allowed"] is False   # tradfi stays fail-safe


@pytest.mark.asyncio
async def test_calendar_outage_block_all_still_blocks_crypto():
    """P0-1: allow_all unchanged; explicit block_all keeps blocking crypto."""
    engine = NewsEngine(unavailable_policy="block_all")
    engine.provider.fetch_events = AsyncMock(return_value=[])
    crypto = await engine.check_trading_allowed(asset_class="CRYPTO")
    assert crypto["news_ok"] is False
    assert crypto["trading_allowed"] is False

    allow = NewsEngine(unavailable_policy="allow_all")
    allow.provider.fetch_events = AsyncMock(return_value=[])
    assert (await allow.check_trading_allowed(asset_class="FOREX"))["news_ok"] is True


def test_db_seed_and_migration_use_block_tradfi_only(tmp_path):
    """P0-1: fresh seeds AND existing block_all DBs land on block_tradfi_only."""
    fresh = DatabaseManager(str(tmp_path / "fresh.db"))
    assert fresh.get_settings()["news_unavailable_policy"] == "block_tradfi_only"

    legacy = DatabaseManager(str(tmp_path / "legacy.db"))
    legacy.set_setting("news_unavailable_policy", "block_all")  # v2.9 seed
    legacy.set_setting("strategy_marker", "keep-me")
    legacy = DatabaseManager(str(tmp_path / "legacy.db"))  # re-init → migration
    settings = legacy.get_settings()
    assert settings["news_unavailable_policy"] == "block_tradfi_only"
    assert settings["strategy_marker"] == "keep-me"  # other rows untouched
    # An explicit operator choice (non-seed value) is preserved as-is.
    legacy.set_setting("news_unavailable_policy", "allow_all")
    assert DatabaseManager(str(tmp_path / "legacy.db")).get_settings()[
        "news_unavailable_policy"] == "allow_all"


def test_ensure_defaults_fills_block_tradfi_only():
    out = ensure_defaults({})
    assert out["news_unavailable_policy"] == "block_tradfi_only"


# --------------------------------------------------------------------------- #
# P0-2 — WS client: watchdog + backoff (frontend contract)
# --------------------------------------------------------------------------- #
def test_ws_client_updates_heartbeat_on_any_message_and_backoff():
    """P0-2: lastHeartbeat on ANY valid JSON message; backoff 1s/3s/10s/15s."""
    html = open("public/index.html", encoding="utf-8").read()

    # Heartbeat liveness is recorded for every valid JSON message, not only
    # type === 'HEARTBEAT' (ACCOUNT_STREAM every 1s must keep the WS alive).
    onmessage = html.split("ws.onmessage", 1)[1].split("};", 1)[0]
    assert "JSON.parse(event.data)" in onmessage
    assert onmessage.index("lastHeartbeat = Date.now()") < onmessage.index(
        "data.type === 'HEARTBEAT'"), "liveness must be set before type dispatch"

    # Reconnect backoff: 1s, 3s, 10s, capped at 15s (not a fixed 3s).
    assert "WS_RECONNECT_STEPS_MS" in html
    assert "[1000, 3000, 10000, 15000]" in html


def test_server_pong_and_streams_unchanged_contract():
    """P0-2: the server-side /ws contract is untouched (ping→pong, streams)."""
    source = open("api/index.py", encoding="utf-8").read()
    assert '"type": "pong"' in source
    assert "HEARTBEAT" in source
    assert "ACCOUNT_STREAM" in source
    assert "SCAN_COMPLETED" in source
    data_engine_source = open("api/engines/data_engine.py", encoding="utf-8").read()
    assert "MARKET_UPDATE" in data_engine_source


# --------------------------------------------------------------------------- #
# P0-3 — ranker copies tradable + symbol flags
# --------------------------------------------------------------------------- #
def _rankable_row(**overrides):
    from datetime import datetime
    row = {
        "symbol": "btc_usdt",
        "display_symbol": "BTC/USDT",
        "asset_class": "CRYPTO",
        "status": "LIVE",
        "active_source": "binance",
        "underlying": "BTC",
        "market_status": "OPEN",
        "score": 90,
        "spread": 0.01,
        "data_age_ms": 500,
        "tradable": True,
        "realtime_source": True,
        "block_reason": None,
        "signal_data": {
            "status": "SIGNAL_DETECTED", "strategy": "rsi", "direction": "BUY",
            "entry": 100.0, "sl": 98.0, "tp": 103.5, "market_id": "btc_usdt",
            "score": 90, "timestamp": datetime.now().timestamp() * 1000,
        },
    }
    row.update(overrides)
    return row


def test_ranker_copies_tradable_and_symbol_flags():
    """P0-3: all_candidates carry tradable + symbol flags from the raw row."""
    out = rank_opportunities([_rankable_row()])
    assert out["total_passing"] == 1
    cand = out["all_candidates"][0]
    assert cand["tradable"] is True
    assert cand["status"] == "LIVE"
    assert cand["active_source"] == "binance"
    assert cand["underlying"] == "BTC"
    assert cand["market_status"] == "OPEN"
    assert cand["display_symbol"] == "BTC/USDT"
    assert cand["asset_class"] == "CRYPTO"


def test_ranker_tradable_false_flags_survive_when_other_gates_pass():
    row = _rankable_row(tradable=False)
    out = rank_opportunities([row])
    assert out["total_passing"] == 0
    reasons = out["excluded"][0]["gate_reasons"]
    assert any(r.startswith("NOT_TRADABLE") for r in reasons)


# --------------------------------------------------------------------------- #
# P0-3 — tick_scanner exposes the real last_block_reason
# --------------------------------------------------------------------------- #
def _arm_engine():
    """Put the shared app in the armed+running prod state."""
    idx.bot_state["armed"] = True
    idx.bot_state["is_running"] = True
    idx.bot_state["active_trades"] = []


@pytest.mark.asyncio
async def test_tick_scanner_armed_no_signal_sets_block_reason(monkeypatch):
    """P0-3: armed + running + 0 executions → last_block_reason != None."""
    async def fake_scan_all(*args, **kwargs):
        return [dict(_rankable_row(), score=90,
                     signal_data={"status": "NO_TRADE", "strategy": "rsi",
                                  "reason": "No RSI cross", "block_reason": None,
                                  "market_id": "btc_usdt"})]

    monkeypatch.setattr(idx.scanner_engine, "scan_all", fake_scan_all)
    _arm_engine()
    try:
        await idx.tick_scanner(force=True)
        reason = idx.bot_state.get("last_block_reason")
        assert reason is not None
        assert reason not in ("", "None")
        assert reason == "NO_RSI_SIGNAL"
    finally:
        idx.bot_state["armed"] = False
        idx.bot_state["is_running"] = False


@pytest.mark.asyncio
async def test_tick_scanner_armed_calendar_outage_reason(monkeypatch):
    """P0-3: calendar-blocked universe → CALENDAR_UNAVAILABLE (not None)."""
    sig = _rankable_row()["signal_data"]
    row = _rankable_row(
        news_risk="High", block_reason="CALENDAR_UNAVAILABLE", tradable=False,
        score=90,
        signal_data={**sig, "status": "NO_TRADE",
                     "block_reason": "CALENDAR_UNAVAILABLE"},
        diagnosis={"checks": {"NEWS_CLEAR": "FAIL"}},
    )

    async def fake_scan_all(*args, **kwargs):
        return [row]

    monkeypatch.setattr(idx.scanner_engine, "scan_all", fake_scan_all)
    _arm_engine()
    try:
        await idx.tick_scanner(force=True)
        assert idx.bot_state.get("last_block_reason") == "CALENDAR_UNAVAILABLE"
    finally:
        idx.bot_state["armed"] = False
        idx.bot_state["is_running"] = False


@pytest.mark.asyncio
async def test_tick_scanner_scan_timeout_not_wiped_when_results_empty(monkeypatch):
    """P0-3: after a scan_all timeout with empty results, keep SCAN_TIMEOUT."""
    import asyncio

    async def hanging_scan_all(*args, **kwargs):
        await asyncio.sleep(3600)

    monkeypatch.setattr(idx.scanner_engine, "scan_all", hanging_scan_all)
    monkeypatch.setattr(idx, "SCAN_ALL_TIMEOUT_S", 0.05)
    idx.bot_state["latest_scan"] = []
    _arm_engine()
    try:
        await idx.tick_scanner(force=True)
        assert idx.bot_state.get("last_block_reason") == "SCAN_TIMEOUT"
    finally:
        idx.bot_state["armed"] = False
        idx.bot_state["is_running"] = False


def test_status_and_opportunities_expose_last_block_reason():
    from fastapi.testclient import TestClient
    idx.bot_state["last_block_reason"] = "NO_RSI_SIGNAL"
    client = TestClient(idx.app)
    status = client.get("/api/status?market_id=btc_usdt").json()
    opps = client.get("/api/opportunities").json()
    assert status["last_block_reason"] == "NO_RSI_SIGNAL"
    assert opps["last_block_reason"] == "NO_RSI_SIGNAL"


# --------------------------------------------------------------------------- #
# P0-4 — Serverless: watchdog off client-side, clear server log
# --------------------------------------------------------------------------- #
def test_serverless_ws_watchdog_disabled_client_side():
    """P0-4: 2 OK /status polls + 0 HEARTBEAT → watchdog must not close."""
    html = open("public/index.html", encoding="utf-8").read()
    assert "wsStatusPollsOk" in html
    assert "wsHeartbeatSeen" in html
    watchdog = html.split("ws.watchdog = setInterval", 1)[1].split("}, 15000)", 1)[0]
    assert "wsHeartbeatSeen" in watchdog and "wsStatusPollsOk" in watchdog
    assert "return" in watchdog  # early-return: do NOT close the socket


def test_serverless_runtime_logs_disabled_ws_heartbeat(monkeypatch):
    """P0-4: server-side log states that WS heartbeats are serverless-absent."""
    monkeypatch.setenv("VERCEL", "1")
    assert idx.is_serverless_runtime() is True
    source = open("api/index.py", encoding="utf-8").read()
    assert "SERVERLESS RUNTIME: WebSocket heartbeat" in source
    assert "poll GET /api/status" in source


def test_persistent_runtime_keeps_heartbeat_loop():
    """P0-4: non-serverless runs keep the heartbeat loop (contract)."""
    import os
    saved = {k: os.environ.get(k) for k in ("VERCEL", "AWS_LAMBDA_FUNCTION_NAME",
                                            "FUNCTIONS_WORKER_RUNTIME")}
    for k in saved:
        os.environ.pop(k, None)
    try:
        assert idx.is_serverless_runtime() is False
        source = open("api/index.py", encoding="utf-8").read()
        assert "loop_wrapper(tick_heartbeat, HEARTBEAT_INTERVAL_S" in source
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
