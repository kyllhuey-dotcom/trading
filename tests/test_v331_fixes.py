"""v3.3.1 — regression tests for the post-v3.3 blind-spot fixes:

1. read retry + full jitter on IDEMPOTENT broker reads (never on orders);
2. request correlation IDs (X-Correlation-ID echo + audit metadata);
3. backup filename collision (same-second runs never overwrite);
4. standalone import of scripts/testnet_broker_matrix.py;
5. partial-close accounting stays honest (contract re-asserted).
"""
import asyncio
import inspect
import os
import re
import subprocess
import sys

import pytest

from api.engines.broker_adapters.ccxt_adapter import CCXTAdapter, read_with_retry
from api.engines.correlation import (
    CORRELATION_HEADER,
    audit_details,
    current_correlation_id,
    new_correlation_id,
    sanitize_correlation_id,
)


# --------------------------------------------------------------------------- #
# 1. read_with_retry — retries reads with bounded full-jitter backoff         #
# --------------------------------------------------------------------------- #

def test_read_with_retry_succeeds_after_transient_failures():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("transient")
        return "ok"

    assert asyncio.run(read_with_retry(flaky, retries=3)) == "ok"
    assert calls["n"] == 3


def test_read_with_retry_jitter_stays_bounded_and_random():
    delays = []

    async def _fail():
        raise OSError("down")

    async def fake_sleep(d):
        delays.append(d)

    async def run():
        # monkeypatch asyncio.sleep inside the helper's module scope
        saved = asyncio.sleep
        try:
            asyncio.sleep = fake_sleep
            with pytest.raises(OSError):
                await read_with_retry(_fail, retries=4,
                                      base_delay_s=0.2, max_delay_s=1.0)
        finally:
            asyncio.sleep = saved

    asyncio.run(run())
    assert len(delays) == 4  # no sleep after the final attempt
    caps = [min(1.0, 0.2 * (2 ** i)) for i in range(4)]
    for d, cap in zip(delays, caps):
        assert 0.0 <= d <= cap + 1e-9


def test_read_with_retry_reraises_last_exception():
    calls = {"n": 0}

    async def always_down():
        calls["n"] += 1
        raise ConnectionError(f"down-{calls['n']}")

    with pytest.raises(ConnectionError, match="down-3"):
        asyncio.run(read_with_retry(always_down, retries=2))
    assert calls["n"] == 3


def test_read_with_retry_non_transient_not_retried():
    """v3.3.2: a non-transient failure (auth, permission, invalid input…)
    can never succeed on a second attempt — it must surface IMMEDIATELY,
    not after a pointless retry with backoff."""
    import ccxt.async_support as ccxt_lib
    calls = {"n": 0}

    async def bad_auth():
        calls["n"] += 1
        raise ccxt_lib.AuthenticationError("invalid api key")

    with pytest.raises(ccxt_lib.AuthenticationError):
        asyncio.run(read_with_retry(bad_auth, retries=3))
    assert calls["n"] == 1  # NO retry

    calls["n"] = 0

    async def bad_input():
        calls["n"] += 1
        raise ValueError("bad input")

    with pytest.raises(ValueError):
        asyncio.run(read_with_retry(bad_input, retries=3))
    assert calls["n"] == 1


def test_read_with_retry_5xx_is_transient():
    """v3.3.2: an exchange error carrying an HTTP 5xx/429 status is
    transient and IS retried."""
    import ccxt.async_support as ccxt_lib
    calls = {"n": 0}

    class Boom(ccxt_lib.ExchangeError):
        def __init__(self, msg):
            super().__init__(msg)
            self.status_code = 502

    async def flaky_502():
        calls["n"] += 1
        if calls["n"] < 2:
            raise Boom("bad gateway")
        return "ok"

    assert asyncio.run(read_with_retry(flaky_502, retries=2)) == "ok"
    assert calls["n"] == 2


async def test_adapter_get_balance_retries_transient_failure():
    adapter = CCXTAdapter("binance", "k", "s", sandbox=True)
    calls = {"n": 0}

    class FakeClient:
        async def fetch_balance(self):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("blip")
            return {"total": {"USDT": 123.45}}

    adapter.client = FakeClient()
    assert await adapter.get_balance("USDT") == 123.45
    assert calls["n"] == 2


def test_order_mutations_are_never_auto_retried():
    """A failed send may have REACHED the exchange (ambiguous outcome):
    retrying create/cancel/close blindly could duplicate a real order.
    The connector reconciles via find_order_by_client_id instead."""
    for method in ("execute_order", "cancel_order", "close_position",
                   "close_all_positions"):
        src = inspect.getsource(getattr(CCXTAdapter, method))
        assert "read_with_retry" not in src, \
            f"{method} must not blind-retry (ORDER_STATE_UNKNOWN risk)"


async def test_fetch_order_status_uses_read_retry():
    adapter = CCXTAdapter("okx", "k", "s", sandbox=True)
    calls = {"n": 0}

    class FakeClient:
        async def fetch_order(self, order_id, symbol):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("gw timeout")
            return {"id": order_id, "status": "open", "average": None,
                    "filled": 0.0, "fee": None, "timestamp": 1}

    adapter.client = FakeClient()
    status = await adapter.fetch_order_status("o1", "BTC/USDT")
    assert status and status["status"] == "open"
    assert calls["n"] == 2


# --------------------------------------------------------------------------- #
# 2. correlation IDs                                                          #
# --------------------------------------------------------------------------- #

def test_correlation_id_format_and_uniqueness():
    ids = {new_correlation_id() for _ in range(50)}
    assert len(ids) == 50
    for cid in ids:
        assert re.fullmatch(r"qtp-[0-9a-f]{32}", cid)


def test_sanitize_correlation_id_rejects_injection():
    assert sanitize_correlation_id("abc-123_def.45") == "abc-123_def.45"
    assert sanitize_correlation_id("") == ""
    assert sanitize_correlation_id(None) == ""
    # header-splitting / log-injection attempts are refused
    assert sanitize_correlation_id("bad id\nX-Injected: 1") == ""
    assert sanitize_correlation_id("short") == ""
    assert sanitize_correlation_id("x" * 65) == ""
    assert sanitize_correlation_id("<script>") == ""


def test_audit_details_injects_current_correlation_id():
    current_correlation_id.__wrapped__ if False else None
    import api.engines.correlation as mod
    token = mod._current_correlation_id.set("qtp-" + "a" * 32)
    try:
        details = audit_details({"position_id": 7})
        assert details["correlation_id"] == "qtp-" + "a" * 32
        assert details["position_id"] == 7
        assert audit_details()["correlation_id"] == "qtp-" + "a" * 32
        assert audit_details({"correlation_id": "keep"})["correlation_id"] == "keep"
        # outside a request: honest None, never a fabricated id
        mod._current_correlation_id.set("")
        assert audit_details()["correlation_id"] is None
    finally:
        mod._current_correlation_id.reset(token)


def test_http_responses_echo_correlation_id():
    from fastapi.testclient import TestClient
    from api.index import app

    with TestClient(app) as client:
        r1 = client.get("/healthz")
        assert r1.status_code == 200
        cid = r1.headers.get(CORRELATION_HEADER)
        assert cid and re.fullmatch(r"qtp-[0-9a-f]{32}", cid)
        # a valid client-supplied ID is preserved end-to-end
        r2 = client.get("/healthz", headers={CORRELATION_HEADER: "my-trace.42"})
        assert r2.headers[CORRELATION_HEADER] == "my-trace.42"
        # an INJECTION attempt is swapped for a safe generated id
        r3 = client.get("/healthz", headers={CORRELATION_HEADER: "evil id\nX: 1"})
        echoed = r3.headers[CORRELATION_HEADER]
        assert echoed.startswith("qtp-") and "evil" not in echoed


# --------------------------------------------------------------------------- #
# 3. backup collision                                                         #
# --------------------------------------------------------------------------- #

def test_backup_same_second_runs_never_overwrite(tmp_path):
    import scripts.backup_db as backup_db

    db = tmp_path / "quantum_trade.db"
    import sqlite3
    conn = sqlite3.connect(db)
    for table in backup_db.EXPECTED_TABLES:
        conn.execute(f"CREATE TABLE {table} (id INTEGER)")
    conn.execute("INSERT INTO trades VALUES (1)")
    conn.commit()
    conn.close()

    out = tmp_path / "backups"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(backup_db.time, "strftime", lambda _fmt: "20260101_010101")
        first = backup_db.cmd_backup(str(db), str(out))
        second = backup_db.cmd_backup(str(db), str(out))

    assert first != second, "same-second backups must not collide"
    assert os.path.exists(first) and os.path.exists(second)
    assert backup_db.cmd_verify(first)
    assert backup_db.cmd_verify(second)
    # a third run picks the next free suffix, never replaces
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(backup_db.time, "strftime", lambda _fmt: "20260101_010101")
        third = backup_db.cmd_backup(str(db), str(out))
    assert third.endswith("quantum_trade_20260101_010101_2.db")


def test_next_free_path_suffixes(tmp_path):
    from scripts.backup_db import _next_free_path
    out = str(tmp_path)
    first = _next_free_path(out, "S")
    assert first.endswith("quantum_trade_S.db")
    open(first, "w").close()
    second = _next_free_path(out, "S")
    assert second.endswith("quantum_trade_S_1.db")
    open(second + ".sha256", "w").close()
    third = _next_free_path(out, "S")
    assert third.endswith("quantum_trade_S_2.db")


# --------------------------------------------------------------------------- #
# 4. standalone import of the testnet campaign script                         #
# --------------------------------------------------------------------------- #

def test_testnet_script_runs_standalone_outside_repo_root():
    """`python3 scripts/testnet_broker_matrix.py` from ANY cwd must reach the
    CONFIRM_TESTNET guard (exit 2 + REFUSED), not crash on `from api...`."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(repo_root, "scripts", "testnet_broker_matrix.py")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("BINANCE_", "BYBIT_", "OKX_", "GATE_"))}
    env["CONFIRM_TESTNET"] = ""  # refuse
    r = subprocess.run([sys.executable, script], cwd="/tmp", env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 2
    assert "REFUSED" in r.stdout + r.stderr
    # and --help works standalone too (argparse reachable)
    r_help = subprocess.run([sys.executable, script, "--help"], cwd="/tmp",
                            env=env, capture_output=True, text=True, timeout=60)
    assert r_help.returncode == 0


# --------------------------------------------------------------------------- #
# 5. partial-close accounting contract (re-asserted)                          #
# --------------------------------------------------------------------------- #

def test_partial_close_accounting_contract_still_enforced():
    from api.engines import pnl_engine

    # fill accounting: only the un-accounted delta is realized
    assert pnl_engine.fill_delta(0.5, 0.2) == pytest.approx(0.3)
    assert pnl_engine.fill_delta(0.5, 0.5) == pytest.approx(0.0)
    assert pnl_engine.is_fully_closed(0.0)
    assert not pnl_engine.is_fully_closed(0.001)
    # fees are pro-rated on the realized portion only
    assert pnl_engine.fee_portion(10.0, 0.25, 1.0) == pytest.approx(2.5)
    # normalize_fill keeps broker fields honest
    fill = pnl_engine.normalize_fill({"filled": 0.4, "average": 90.0,
                                      "fee": {"cost": 0.2}})
    assert fill["filled"] == 0.4 and fill["fees"] == 0.2
