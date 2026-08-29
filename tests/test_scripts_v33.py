"""v3.3 — unit tests for scripts/backup_db.py and scripts/testnet_broker_matrix.py.

The testnet campaign itself requires real testnet credentials (see
docs/TESTNET_MATRIX.md); these tests exercise every branch of the scripts
offline: the CONFIRM_TESTNET gate, the sandbox-only adapter flow (mocked),
credential scrubbing, report writing and the backup/verify/restore rules.
"""
import json
import os
import sqlite3
import sys

import pytest

from scripts import backup_db, testnet_broker_matrix as tnm

ALL_TABLES = sorted(backup_db.EXPECTED_TABLES)
STOP_PARAM_KEYS = ("stopPrice", "triggerPrice", "stopLossPrice")


def _make_db(path, extra_table: str = None) -> str:
    conn = sqlite3.connect(str(path))
    try:
        for name in ALL_TABLES:
            if name == extra_table:
                continue
            conn.execute(f"CREATE TABLE IF NOT EXISTS {name} (id INTEGER)")
        conn.execute("INSERT INTO trades (id) VALUES (1)")
        conn.execute("INSERT INTO trades (id) VALUES (2)")
        conn.commit()
    finally:
        conn.close()
    return str(path)


# --------------------------------------------------------------------------- #
# backup_db                                                                    #
# --------------------------------------------------------------------------- #

def test_backup_verify_restore_roundtrip(tmp_path, capsys):
    db = _make_db(tmp_path / "live.db")
    out = tmp_path / "backups"
    dest = backup_db.cmd_backup(db, str(out))
    assert os.path.exists(dest)
    assert os.path.exists(dest + ".sha256")
    capsys.readouterr()

    assert backup_db.cmd_verify(dest) is True
    out_text = capsys.readouterr().out
    assert "sha256 ok" in out_text and "schema ok" in out_text
    assert "2 trade rows" in out_text

    restored = str(tmp_path / "restored" / "copy.db")
    assert backup_db.cmd_restore(dest, restored) is True
    assert os.path.exists(restored)
    # restoring again refuses to overwrite the existing copy
    assert backup_db.cmd_restore(dest, restored) is False
    assert "refusing to overwrite" in capsys.readouterr().err
    # restoring onto the source file itself is refused
    assert backup_db.cmd_restore(dest, dest) is False
    assert "source file" in capsys.readouterr().err


def test_backup_missing_db_exits(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        backup_db.cmd_backup(str(tmp_path / "nope.db"), str(tmp_path / "out"))
    assert exc.value.code == 1
    assert "DB not found" in capsys.readouterr().err


def test_verify_corrupted_sha256(tmp_path, capsys):
    db = _make_db(tmp_path / "live.db")
    dest = backup_db.cmd_backup(db, str(tmp_path / "out"))
    with open(dest + ".sha256", "w", encoding="utf-8") as fh:
        fh.write("0" * 64 + "  " + os.path.basename(dest) + "\n")
    capsys.readouterr()
    assert backup_db.cmd_verify(dest) is False
    assert "sha256 mismatch" in capsys.readouterr().err


def test_verify_missing_tables(tmp_path, capsys):
    db = _make_db(tmp_path / "live.db", extra_table="order_intents")
    dest = backup_db.cmd_backup(db, str(tmp_path / "out"))
    os.remove(dest + ".sha256")  # verify must still check the schema
    capsys.readouterr()
    assert backup_db.cmd_verify(dest) is False
    assert "missing tables" in capsys.readouterr().err


def test_verify_missing_file(tmp_path, capsys):
    assert backup_db.cmd_verify(str(tmp_path / "ghost.db")) is False
    assert "file not found" in capsys.readouterr().err


def test_backup_db_main_cli(tmp_path, capsys, monkeypatch):
    db = _make_db(tmp_path / "live.db")
    out_dir = str(tmp_path / "out")
    monkeypatch.setattr(sys, "argv",
                        ["backup_db.py", "backup", "--db", db, "--out", out_dir])
    backup_db.main()
    dests = [p for p in os.listdir(out_dir) if p.endswith(".db")]
    assert len(dests) == 1
    dest = os.path.join(out_dir, dests[0])

    monkeypatch.setattr(sys, "argv", ["backup_db.py", "verify", "--file", dest])
    with pytest.raises(SystemExit) as ok_exc:
        backup_db.main()
    assert ok_exc.value.code == 0

    monkeypatch.setattr(sys, "argv",
                        ["backup_db.py", "verify", "--file", str(tmp_path / "x.db")])
    with pytest.raises(SystemExit) as exc:
        backup_db.main()
    assert exc.value.code == 1

    monkeypatch.setattr(sys, "argv",
                        ["backup_db.py", "restore", "--file", dest,
                         "--to", str(tmp_path / "restore.db")])
    with pytest.raises(SystemExit) as restore_exc:
        backup_db.main()
    assert restore_exc.value.code == 0
    assert os.path.exists(tmp_path / "restore.db")


# --------------------------------------------------------------------------- #
# testnet_broker_matrix — scrubbing / masking / env                            #
# --------------------------------------------------------------------------- #

def test_scrub_masks_credentials_recursively():
    payload = {
        "api_key": "abcdef123456",        # sensitive key -> masked wholesale
        "api_secret": "s",
        "note": "set your api-secret properly",  # sensitive-looking string
        "nested": {
            "api_secret": {"deep": "x"},  # dict under sensitive key -> "***"
            "trades": [{"api_key": "k" * 20}, "ok"],
        },
        "normal": {"value": 3},
    }
    out = tnm._scrub(payload)
    assert out["api_key"] == "***"
    assert out["api_secret"] == "***"
    assert out["note"].startswith("set ") and "***" in out["note"]
    assert out["nested"]["api_secret"] == "***"
    assert out["nested"]["trades"][0] == {"api_key": "***"}
    assert out["nested"]["trades"][1] == "ok"
    assert out["normal"] == {"value": 3}


def test_scrub_safe_string_unchanged():
    assert tnm._scrub({"a": "hello"}) == {"a": "hello"}
    assert tnm._scrub("short") == "short"  # not sensitive-looking
    assert tnm._scrub([1, "x"]) == [1, "x"]


def test_mask_key_variants():
    assert tnm._mask_key(None) is None
    assert tnm._mask_key("short") == "***"
    assert tnm._mask_key("abcdefghij") == "abcd***ij"


def test_env_reads_prefix(monkeypatch):
    monkeypatch.setenv("OKX_API_KEY", "k1")
    monkeypatch.setenv("OKX_API_SECRET", "s1")
    monkeypatch.setenv("OKX_API_PASSPHRASE", "p1")
    assert tnm._env("okx") == {"api_key": "k1", "api_secret": "s1",
                               "passphrase": "p1"}
    monkeypatch.delenv("GATE_API_KEY", raising=False)
    assert tnm._env("gate")["api_key"] is None


# --------------------------------------------------------------------------- #
# testnet_broker_matrix — adapter flow (fully mocked, sandbox only)            #
# --------------------------------------------------------------------------- #

class FakeCCXTClient:
    def __init__(self, fail: str = ""):
        self.fail = fail
        self.markets = {"BTC/USDT": {"limits": {"amount": {"min": 0.001}}}}
        self.created = []

    async def fetch_ticker(self, symbol):
        if "ticker" in self.fail:
            raise RuntimeError("ticker down")
        return {"last": 10000.0}

    async def create_order(self, symbol, otype, side, qty, price, params=None):
        self.created.append((symbol, otype, side, qty, params or {}))
        return {"id": f"ord-{len(self.created)}", "status": "open"}

    async def fetch_order(self, oid, symbol):
        if "fetch_order" in self.fail:
            raise RuntimeError("fetch_order down")
        return {"id": oid, "status": "closed"}

    async def fetch_open_orders(self, symbol, **kw):
        return [{"id": "open-1", "symbol": symbol}]

    async def fetch_closed_orders(self, symbol, limit=None):
        return [{"id": "c-1"}, {"id": "c-2"}]

    async def fetch_trades(self, symbol, limit=None):
        return [{"id": "t-1"}]

    async def fetch_positions(self):
        return [{"symbol": "BTC/USDT", "side": "long", "contracts": 0.001}]

    async def cancel_order(self, oid, symbol):
        return {"id": oid}

    def amount_to_precision(self, symbol, qty):
        return f"{float(qty):.5f}"

    def price_to_precision(self, symbol, price):
        return f"{float(price):.2f}"


class FakeAdapter:
    def __init__(self, sandbox: bool, connected: bool = True):
        self.sandbox = sandbox
        self.connected_flag = connected
        self.client = FakeCCXTClient()
        self.closed = False

    async def connect(self):
        return self.connected_flag

    async def close(self):
        self.closed = True


def _patch_adapter(monkeypatch, adapter):
    from api.engines.broker_adapters import ccxt_adapter
    monkeypatch.setattr(ccxt_adapter, "CCXTAdapter", lambda *a, **kw: adapter)


def _set_creds(monkeypatch, exchange: str):
    monkeypatch.setenv(f"{exchange.upper()}_API_KEY", "k")
    monkeypatch.setenv(f"{exchange.upper()}_API_SECRET", "s")


async def test_run_one_missing_credentials(monkeypatch):
    monkeypatch.delenv("OKX_API_KEY", raising=False)
    monkeypatch.delenv("OKX_API_SECRET", raising=False)
    report = tnm.ExchangeReport(exchange="okx")
    await tnm._run_one("okx", report)
    assert report.errors == ["missing environment credentials"]
    assert report.connected is False


async def test_run_one_refuses_non_sandbox(monkeypatch):
    _set_creds(monkeypatch, "okx")
    adapter = FakeAdapter(sandbox=False)
    _patch_adapter(monkeypatch, adapter)
    report = tnm.ExchangeReport(exchange="okx")
    await tnm._run_one("okx", report)
    assert "ABORT: adapter not confirmed in sandbox mode" in report.errors
    assert adapter.closed is True


async def test_run_one_connection_failure(monkeypatch):
    _set_creds(monkeypatch, "okx")
    adapter = FakeAdapter(sandbox=True, connected=False)
    _patch_adapter(monkeypatch, adapter)
    report = tnm.ExchangeReport(exchange="okx")
    await tnm._run_one("okx", report)
    assert report.errors == ["connection failed in sandbox mode"]


async def test_run_one_happy_path_sandbox(monkeypatch):
    _set_creds(monkeypatch, "okx")
    adapter = FakeAdapter(sandbox=True)
    _patch_adapter(monkeypatch, adapter)
    report = tnm.ExchangeReport(exchange="okx")
    await tnm._run_one("okx", report)

    assert report.errors == []
    assert report.sandbox is True
    assert report.connected is True
    assert report.created_order_id == "ord-1"
    assert report.client_order_id.startswith("QTP-TNM-")
    assert report.fetch_order_status == "closed"
    assert report.open_orders_seen == 1
    assert report.closed_orders_seen == 2
    assert report.trades_seen == 1
    # okx stop: market order with stopLossPrice + reduceOnly
    assert report.stop_created == "ord-2"
    stop_calls = [c for c in adapter.client.created
                  if any(k in c[4] for k in STOP_PARAM_KEYS)]
    assert len(stop_calls) == 1 and stop_calls[0][4].get("reduceOnly") is True

    # cleanup: open order cancelled + long position closed reduce-only
    assert report.cancelled_orders == ["open-1"]
    assert report.positions_closed == 1
    close_calls = [c for c in adapter.client.created
                   if (c[4] or {}).get("reduceOnly")
                   and not any(k in c[4] for k in STOP_PARAM_KEYS)]
    assert len(close_calls) == 1 and close_calls[0][2] == "sell"  # long -> sell
    assert adapter.closed is True


async def test_run_one_partial_failures(monkeypatch):
    _set_creds(monkeypatch, "bybit")
    adapter = FakeAdapter(sandbox=True)
    adapter.client = FakeCCXTClient(fail="ticker fetch_order")

    # make the bybit stop (triggerPrice param) fail to exercise the branch
    real_create = adapter.client.create_order

    async def failing_create(symbol, otype, side, qty, price, params=None):
        if (params or {}).get("triggerPrice") is not None:
            raise RuntimeError("stop create failed")
        return await real_create(symbol, otype, side, qty, price, params)

    adapter.client.create_order = failing_create
    _patch_adapter(monkeypatch, adapter)

    report = tnm.ExchangeReport(exchange="bybit")
    await tnm._run_one("bybit", report)
    assert "ticker: ticker down" in report.errors
    assert "fetch_order: fetch_order down" in report.errors
    assert "stop_create: stop create failed" in report.errors
    # the campaign survives: entry order created, cleanup still ran
    assert report.connected is True
    assert report.created_order_id == "ord-1"
    assert report.cancelled_orders == ["open-1"]
    assert report.positions_closed == 1


async def test_run_campaign_collects_reports(monkeypatch):
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    reports = await tnm.run_campaign(["binance"])
    assert set(reports) == {"binance"}
    assert reports["binance"].errors == ["missing environment credentials"]


# --------------------------------------------------------------------------- #
# testnet_broker_matrix — CLI gates + report writing                           #
# --------------------------------------------------------------------------- #

def test_main_refused_without_confirm(monkeypatch, caplog):
    monkeypatch.delenv("CONFIRM_TESTNET", raising=False)
    monkeypatch.setattr(sys, "argv", ["testnet_broker_matrix.py"])
    with pytest.raises(SystemExit) as exc:
        tnm.main()
    assert exc.value.code == 2
    assert "REFUSED" in caplog.text


def test_main_refuses_unknown_exchange(monkeypatch, caplog):
    monkeypatch.setenv("CONFIRM_TESTNET", "true")
    monkeypatch.setattr(sys, "argv",
                        ["testnet_broker_matrix.py",
                         "--exchanges", "binance,kraken"])
    with pytest.raises(SystemExit) as exc:
        tnm.main()
    assert exc.value.code == 2
    assert "Unknown exchanges" in caplog.text


def _fake_reports(status_pass: bool):
    ok = tnm.ExchangeReport(exchange="binance", sandbox=True, connected=True)
    if not status_pass:
        ok.errors.append("something failed")
    ok.created_order_id = "ord-1"
    return {"binance": ok}


def test_main_writes_scrubbed_report_pass(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CONFIRM_TESTNET", "true")
    monkeypatch.setenv("BINANCE_API_KEY", "supersecretkey123")
    monkeypatch.setenv("BINANCE_API_SECRET", "superscretsecret123")
    monkeypatch.setattr(sys, "argv",
                        ["testnet_broker_matrix.py", "--exchanges", "binance",
                         "--report-dir", str(tmp_path)])

    async def fake_run(exchanges):
        return _fake_reports(status_pass=True)

    monkeypatch.setattr(tnm, "run_campaign", fake_run)
    tnm.main()

    reports = [p for p in os.listdir(tmp_path)
               if p.startswith("testnet_matrix_")]
    assert len(reports) == 1
    data = json.loads((tmp_path / reports[0]).read_text(encoding="utf-8"))
    assert data["overall_status"] == "PASS"
    assert data["mode"].startswith("TESTNET_ONLY")
    blob = json.dumps(data)
    assert "supersecretkey123" not in blob
    assert "superscretsecret123" not in blob
    # credential entries are fully masked in the persisted report
    assert data["exchanges"]["binance"]["credentials"]["api_key"] == "***"


def test_main_failing_campaign_exits_1(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CONFIRM_TESTNET", "true")
    monkeypatch.setenv("BINANCE_API_KEY", "k")
    monkeypatch.setenv("BINANCE_API_SECRET", "s")
    monkeypatch.setattr(sys, "argv",
                        ["testnet_broker_matrix.py", "--exchanges", "binance",
                         "--report-dir", str(tmp_path)])

    async def fake_run(exchanges):
        return _fake_reports(status_pass=False)

    monkeypatch.setattr(tnm, "run_campaign", fake_run)
    with pytest.raises(SystemExit) as exc:
        tnm.main()
    assert exc.value.code == 1
    assert "REQUIRED before any real ARM" in capsys.readouterr().out
