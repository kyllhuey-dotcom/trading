"""
LOT A — Observability & advanced metrics.

Covers:
- MetricsEngine counters / rolling stats (scans, signals, executions, data age)
- JSON structured logging (parseable NDJSON + rotation handler config)
- WebSocket heartbeat + application-level ping/pong
- GET /api/metrics enriched payload (backwards-compatible)
- Empty-snapshot diagnosis keeps the full check contract (offline-safe)
"""
import asyncio
import json
import logging
import os

os.environ["TESTING"] = "true"

import pytest
from fastapi.testclient import TestClient

from api.engines.metrics_engine import MetricsEngine
from api.json_logging import JsonFormatter, setup_json_file_handler, structured_log
from api.index import app, manager

client = TestClient(app)


# --------------------------------------------------------------------------- #
# 1. MetricsEngine (pure unit)                                                #
# --------------------------------------------------------------------------- #
def test_metrics_engine_scan_recording():
    m = MetricsEngine()
    results = [
        {"tradable": True, "signal_data": {"strategy": "tape"}, "data_age_ms": 120},
        {"tradable": False, "signal_data": {"strategy": "structure"}, "data_age_ms": 80},
        {"tradable": True, "signal_data": {"strategy": "tape"}, "data_age_ms": 400},
        {"tradable": True, "signal_data": {"strategy": "arbitrage"}, "data_age_ms": None},
    ]
    m.record_scan(1.5, results)
    snap = m.snapshot()

    assert snap["total_scans"] == 1
    assert snap["signals_generated_by_strategy"] == {"tape": 2, "arbitrage": 1}
    assert snap["latency"]["scan_last_ms"] == 1500.0
    assert snap["latency"]["scan_avg_ms"] == 1500.0
    assert snap["data_age"]["last_ms"] == 400
    assert snap["data_age"]["max_ms"] == 400
    assert snap["data_age"]["avg_ms"] == round((120 + 80 + 400) / 3, 2)
    assert snap["data_age"]["samples"] == 3
    assert snap["last_scan_timestamp"] is not None


def test_metrics_engine_execution_recording():
    m = MetricsEngine()
    m.record_execution("arbitrage", "DEMO", True, latency_ms=12.5)
    m.record_execution("tape", "REAL", False, latency_ms=250.0)
    m.record_execution("tape", "real", True, latency_ms=60.0)  # mode case-insensitive
    snap = m.snapshot()

    assert snap["orders_by_mode"] == {"DEMO": 1, "REAL": 2}
    assert snap["signals_generated_by_strategy"] == {"arbitrage": 1, "tape": 1}
    assert snap["signals_blocked_by_strategy"] == {"tape": 1}
    assert snap["latency"]["execution_max_ms"] == 250.0
    assert snap["latency"]["execution_avg_ms"] == round((12.5 + 250 + 60) / 3, 2)


def test_metrics_engine_blocked_signal_and_errors():
    m = MetricsEngine()
    m.record_signal_blocked("liquidity")
    m.record_error()
    m.record_error()
    snap = m.snapshot()
    assert snap["signals_blocked_by_strategy"] == {"liquidity": 1}
    assert snap["total_errors"] == 2


def test_metrics_engine_empty_state():
    m = MetricsEngine()
    snap = m.snapshot()
    assert snap["total_scans"] == 0
    assert snap["orders_by_mode"] == {"DEMO": 0, "REAL": 0}
    assert snap["latency"]["scan_avg_ms"] is None
    assert snap["data_age"]["samples"] == 0


# --------------------------------------------------------------------------- #
# 2. JSON structured logging                                                  #
# --------------------------------------------------------------------------- #
def test_json_formatter_emits_parseable_ndjson(tmp_path):
    logger = logging.getLogger("test_json_formatter_ndjson")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    path = str(tmp_path / "app.jsonl")
    handler = setup_json_file_handler(path, max_bytes=4096, backup_count=2)
    logger.addHandler(handler)
    try:
        logger.info("hello %s", "world", extra={"event": "test_event", "value": 42})
    finally:
        logger.removeHandler(handler)
        handler.close()

    line = open(path, encoding="utf-8").read().strip().splitlines()[-1]
    data = json.loads(line)
    assert data["message"] == "hello world"
    assert data["level"] == "INFO"
    assert data["logger"] == "test_json_formatter_ndjson"
    assert data["event"] == "test_event"
    assert data["value"] == 42
    assert "timestamp" in data and "module" in data


def test_json_formatter_includes_exception_info():
    formatter = JsonFormatter()
    try:
        1 / 0
    except ZeroDivisionError:
        record = logging.LogRecord(
            "exc_test", logging.ERROR, __file__, 1, "division failed", None,
            exc_info=__import__("sys").exc_info())
    payload = json.loads(formatter.format(record))
    assert payload["message"] == "division failed"
    assert "ZeroDivisionError" in payload["exc_info"]


def test_json_formatter_serializes_non_json_values():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        "ser_test", logging.INFO, __file__, 1, "odd values", None, exc_info=None)
    record.extra_set = {1, 2, 3}  # type: ignore[attr-defined]
    record.extra_tuple = (1, 2)  # type: ignore[attr-defined]
    payload = json.loads(formatter.format(record))
    assert payload["extra_set"] == [1, 2, 3]
    assert payload["extra_tuple"] == [1, 2]


def test_structured_log_guards_reserved_names():
    logger = logging.getLogger("structured_log_test")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    records = []

    class CaptureHandler(logging.Handler):
        def emit(self, record):
            records.append(record)

    capture = CaptureHandler()
    logger.addHandler(capture)
    try:
        structured_log(logger, logging.INFO, "event happened", event="order", msg="clobber")
    finally:
        logger.removeHandler(capture)

    assert records[-1].event == "order"
    # "msg" collides with LogRecord: prefixed instead of raising
    assert records[-1].field_msg == "clobber"
    assert records[-1].getMessage() == "event happened"


# --------------------------------------------------------------------------- #
# 3. WebSocket heartbeat + ping/pong                                         #
# --------------------------------------------------------------------------- #
class FakeWebSocket:
    def __init__(self):
        self.sent: list = []
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def send_text(self, message: str):
        self.sent.append(message)


async def test_connection_manager_heartbeat_broadcast():
    manager.active_connections.clear()
    manager.connection_meta.clear()
    ws = FakeWebSocket()
    await manager.connect(ws)
    assert ws.accepted

    payload = await manager.broadcast_heartbeat()
    assert payload["type"] == "HEARTBEAT"
    assert payload["clients"] == 1

    delivered = json.loads(ws.sent[-1])
    assert delivered["type"] == "HEARTBEAT"
    assert delivered["seq"] == payload["seq"]
    assert delivered["server_time"] == payload["server_time"]
    assert "state" in delivered

    status = manager.heartbeat_status()
    assert status["clients"] == 1
    assert status["seq"] == payload["seq"]
    assert status["last_sent_at"] == payload["server_time"]

    manager.disconnect(ws)
    assert manager.client_count == 0
    assert manager.heartbeat_status()["clients"] == 0


class DeadWebSocket(FakeWebSocket):
    async def send_text(self, message: str):
        raise RuntimeError("connection lost")


async def test_connection_manager_prunes_dead_clients():
    manager.active_connections.clear()
    manager.connection_meta.clear()
    alive, dead = FakeWebSocket(), DeadWebSocket()
    await manager.connect(alive)
    await manager.connect(dead)

    await manager.broadcast('{"type": "ACCOUNT_STREAM"}')
    assert manager.client_count == 1  # dead connection pruned


def test_ws_application_ping_pong():
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "ping", "seq": 42})
        msg = ws.receive_json()
        assert msg["type"] == "pong"
        assert msg["seq"] == 42
        assert "timestamp_ms" in msg and "server_time" in msg


# --------------------------------------------------------------------------- #
# 4. Enriched /api/metrics (backwards-compatible)                             #
# --------------------------------------------------------------------------- #
def test_api_metrics_enriched_payload():
    response = client.get("/api/metrics")
    assert response.status_code == 200
    data = response.json()

    # Legacy fields still present (public contract)
    for key in ("total_scans", "total_trades", "signals_by_strategy",
                "start_time", "uptime_s", "scanner_duration_s",
                "state", "recent_orders"):
        assert key in data, f"missing legacy key {key}"

    # New LOT A fields
    for key in ("signals_generated_by_strategy", "signals_blocked_by_strategy",
                "orders_by_mode", "winrate_simulated", "latency", "data_age",
                "heartbeat"):
        assert key in data, f"missing enriched key {key}"

    assert set(data["orders_by_mode"].keys()) == {"DEMO", "REAL"}
    assert set(data["winrate_simulated"].keys()) == {"DEMO", "REAL"}
    assert "scan_avg_ms" in data["latency"] and "execution_max_ms" in data["latency"]
    assert "avg_ms" in data["data_age"] and "samples" in data["data_age"]
    assert data["heartbeat"]["clients"] >= 0


def test_empty_snapshot_keeps_full_diagnosis_contract():
    """Even with no market data, the diagnosis must expose every check key."""
    response = client.get("/api/status?market_id=__nonexistent__")
    assert response.status_code == 200
    diagnosis = response.json().get("diagnosis") or {}
    checks = diagnosis.get("checks", {})
    for expected in ("DATA_VALID", "RISK_VALID", "LEVERAGE_VALID",
                     "BROKER_VALID", "SYSTEM_ARMED", "SIGNAL_VALID"):
        assert expected in checks, f"missing diagnosis check {expected}"
