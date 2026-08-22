"""
LOT Q — Audit scripts (profit_audit.py & optimize_params.py).

Covers:
- analyze_db: empty / mixed / missing table / missing file / invalid metadata
  JSON / negative PnL / cost leaks;
- print_report, print_recommendations, main (profit_audit.py);
- load_audit, main (optimize_params.py).
"""
import json
import os
import sqlite3
import sys

import pytest

# Same import strategy as tests/test_profitability.py: the scripts live in
# scripts/ and are not a package, so add the directory to sys.path first.
SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS_DIR)

import profit_audit  # noqa: E402
import optimize_params  # noqa: E402


TRADES_SCHEMA = """
CREATE TABLE trades (
    id TEXT PRIMARY KEY,
    mode TEXT,
    symbol TEXT,
    direction TEXT,
    entry_price REAL,
    sl REAL,
    pnl REAL,
    status TEXT,
    metadata TEXT
)
"""


def _make_db(path, rows=()):
    """Create a SQLite DB with the trades table and insert the given rows."""
    con = sqlite3.connect(str(path))
    con.execute(TRADES_SCHEMA)
    for r in rows:
        con.execute(
            "INSERT INTO trades (id, mode, symbol, direction, entry_price, sl, "
            "pnl, status, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", r)
    con.commit()
    con.close()


def _row(tid, mode="DEMO", strategy="structure", pnl=1.0,
         entry=100.0, sl=99.0, status="CLOSED"):
    return (tid, mode, "btc_usdt", "BUY", entry, sl, pnl, status,
            json.dumps({"strategy": strategy}))


# --------------------------------------------------------------------------- #
# analyze_db                                                                   #
# --------------------------------------------------------------------------- #
def test_analyze_db_empty(tmp_path):
    db = tmp_path / "empty.db"
    _make_db(db)  # table exists, but no closed trades
    stats = profit_audit.analyze_db(str(db))
    assert "error" not in stats
    assert stats["total_closed_trades"] == 0
    assert stats["total_net_pnl"] == 0.0
    assert stats["modes"] == {}


def test_analyze_db_missing_file(tmp_path):
    # A missing .db is auto-created empty by sqlite3 → the trades table is
    # absent, so analyze_db returns an error instead of crashing.
    stats = profit_audit.analyze_db(str(tmp_path / "nope.db"))
    assert "error" in stats
    assert "cannot read trades table" in stats["error"]


def test_analyze_db_unopenable_path(tmp_path):
    # A directory is not a valid SQLite file → connect itself fails.
    stats = profit_audit.analyze_db(str(tmp_path))
    assert "error" in stats
    assert "cannot open database" in stats["error"]


def test_analyze_db_missing_table(tmp_path):
    db = tmp_path / "no_trades.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE foo (id INTEGER)")
    con.commit()
    con.close()
    stats = profit_audit.analyze_db(str(db))
    assert "error" in stats
    assert "cannot read trades table" in stats["error"]


def test_analyze_db_mixed_modes_and_strategies(tmp_path):
    db = tmp_path / "mixed.db"
    _make_db(db, [
        _row("t1", mode="DEMO", strategy="structure", pnl=1.0),
        _row("t2", mode="DEMO", strategy="structure", pnl=-0.5),
        _row("t3", mode="DEMO", strategy="tape", pnl=0.5),
        _row("t4", mode="REAL", strategy="tape", pnl=-0.2),
    ])
    stats = profit_audit.analyze_db(str(db))
    assert "error" not in stats
    assert stats["total_closed_trades"] == 4
    assert stats["total_net_pnl"] == pytest.approx(0.8)

    demo = stats["modes"]["DEMO"]
    assert demo["closed_trades"] == 3
    structure = demo["by_strategy"]["structure"]
    assert structure["trades"] == 2
    assert structure["wins"] == 1 and structure["losses"] == 1
    assert structure["win_rate"] == 50.0
    assert structure["net_pnl"] == pytest.approx(0.5)
    assert demo["by_strategy"]["tape"]["trades"] == 1


def test_analyze_db_invalid_metadata_json(tmp_path):
    db = tmp_path / "badmeta.db"
    _make_db(db, [( "t1", "DEMO", "btc_usdt", "BUY", 100.0, 99.0, 1.0,
                   "CLOSED", "{this is not valid json")])
    stats = profit_audit.analyze_db(str(db))
    assert "error" not in stats
    # malformed metadata falls back to {} → strategy "unknown", no crash
    assert "unknown" in stats["modes"]["DEMO"]["by_strategy"]
    assert stats["total_closed_trades"] == 1


def test_analyze_db_negative_pnl_losing(tmp_path):
    db = tmp_path / "losing.db"
    _make_db(db, [
        _row("t1", pnl=-1.0),
        _row("t2", pnl=-2.0),
        _row("t3", pnl=-0.5),
    ])
    stats = profit_audit.analyze_db(str(db))
    s = stats["modes"]["DEMO"]["by_strategy"]["structure"]
    assert s["wins"] == 0 and s["losses"] == 3
    assert s["win_rate"] == 0.0
    assert s["net_pnl"] == pytest.approx(-3.5)
    assert s["verdict"] == "LOSING"


def test_analyze_db_cost_leaks(tmp_path):
    db = tmp_path / "leaks.db"
    # entry 100 / sl 99.99 → risk 0.01% → assumed 0.20% costs >> 50% of risk
    leaky = _row("t1", pnl=0.1, entry=100.0, sl=99.99)
    normal = _row("t2", pnl=0.1, entry=100.0, sl=99.0)
    _make_db(db, [leaky, normal])
    stats = profit_audit.analyze_db(str(db))
    s = stats["modes"]["DEMO"]["by_strategy"]["structure"]
    assert s["cost_leaks"] == 1


# --------------------------------------------------------------------------- #
# print_report / print_recommendations / main                                  #
# --------------------------------------------------------------------------- #
def test_print_report_error(capsys):
    profit_audit.print_report({"error": "boom"})
    out = capsys.readouterr().out
    assert "ERROR: boom" in out


def test_print_report_with_data(tmp_path, capsys):
    db = tmp_path / "report.db"
    _make_db(db, [_row("t1", pnl=1.0), _row("t2", pnl=-0.5)])
    stats = profit_audit.analyze_db(str(db))
    profit_audit.print_report(stats)
    out = capsys.readouterr().out
    assert "QUANTUM TRADE PRO" in out
    assert "TOTAL closed trades: 2" in out


def test_print_recommendations_with_data(tmp_path, capsys):
    db = tmp_path / "rec.db"
    _make_db(db, [_row("t1", pnl=1.0), _row("t2", pnl=-0.5)])
    stats = profit_audit.analyze_db(str(db))
    profit_audit.print_recommendations(stats, balance=50.0)
    out = capsys.readouterr().out
    assert "AUDIT-DRIVEN OPTIMIZATION" in out
    assert "STANDARD" in out  # 50$ → STANDARD bracket
    assert "Recommended settings" in out


def test_profit_audit_main(tmp_path, capsys, monkeypatch):
    db = tmp_path / "main.db"
    _make_db(db, [_row("t1", pnl=1.0)])
    monkeypatch.setattr(sys, "argv", ["profit_audit.py", str(db), "50.0"])
    profit_audit.main()
    out = capsys.readouterr().out
    assert "PROFIT AUDIT" in out
    assert "AUDIT-DRIVEN OPTIMIZATION" in out


# --------------------------------------------------------------------------- #
# optimize_params — load_audit / main                                          #
# --------------------------------------------------------------------------- #
def test_load_audit_valid(tmp_path):
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"modes": {}, "total_closed_trades": 0}))
    assert optimize_params.load_audit(str(audit)) == {
        "modes": {}, "total_closed_trades": 0}


def test_load_audit_none_and_missing(tmp_path):
    assert optimize_params.load_audit(None) is None
    assert optimize_params.load_audit(str(tmp_path / "missing.json")) is None


def test_load_audit_invalid_json(tmp_path):
    audit = tmp_path / "bad.json"
    audit.write_text("{invalid json")
    assert optimize_params.load_audit(str(audit)) is None


def test_optimize_params_main(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["optimize_params.py", "50.0"])
    optimize_params.main()
    out = capsys.readouterr().out
    assert "CAPITAL-AWARE PARAMETER OPTIMIZER" in out
    assert "STANDARD" in out


def test_optimize_params_main_with_audit(tmp_path, capsys, monkeypatch):
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({
        "modes": {"DEMO": {"closed_trades": 1, "net_pnl": -1.0,
                           "by_strategy": {"structure": {
                               "trades": 1, "wins": 0, "losses": 1,
                               "win_rate": 0.0, "net_pnl": -1.0,
                               "avg_win": 0.0, "avg_loss": 1.0,
                               "realized_rr": None, "expectancy_per_trade": -1.0,
                               "cost_leaks": 0, "verdict": "LOSING"}}}},
        "total_closed_trades": 1, "total_net_pnl": -1.0,
    }))
    monkeypatch.setattr(sys, "argv", ["optimize_params.py", "5.0", str(audit)])
    optimize_params.main()
    out = capsys.readouterr().out
    assert "CAPITAL-AWARE PARAMETER OPTIMIZER" in out
    assert "MICRO" in out
    assert "Audit-driven recommendations" in out
