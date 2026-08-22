"""Unit tests for every maintenance and deployment script function."""
import json
import sqlite3
from types import SimpleNamespace

from api.engines.db_manager import DatabaseManager
from scripts import check_db, optimize_params, profit_audit, smoke_test


def test_check_db_list_tables_and_main(tmp_path, capsys):
    path = tmp_path / "schema.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE beta (id INTEGER)")
        connection.execute("CREATE TABLE alpha (id INTEGER)")

    assert check_db.list_tables(str(path)) == ["alpha", "beta"]
    assert check_db.main([str(path)]) == 0
    output = capsys.readouterr().out
    assert "alpha" in output and "beta" in output

    assert check_db.main([str(tmp_path / "missing.db")]) == 1
    assert "Database not found" in capsys.readouterr().err


def test_check_db_rejects_invalid_sqlite_file(tmp_path):
    path = tmp_path / "broken.db"
    path.write_text("this is not sqlite")
    try:
        check_db.list_tables(str(path))
    except RuntimeError as exc:
        assert "Cannot inspect database" in str(exc)
    else:
        raise AssertionError("invalid SQLite file should fail")


def test_optimize_load_audit_and_main(tmp_path, monkeypatch, capsys):
    path = tmp_path / "audit.json"
    path.write_text(json.dumps({"modes": {}}))
    assert optimize_params.load_audit(None) is None
    assert optimize_params.load_audit(str(path)) == {"modes": {}}
    assert optimize_params.load_audit(str(tmp_path / "missing.json")) is None
    assert "cannot read audit JSON" in capsys.readouterr().err

    assert optimize_params.main(["5", str(path)]) == 0
    assert "CAPITAL-AWARE PARAMETER OPTIMIZER" in capsys.readouterr().out
    assert optimize_params.main(["not-a-number"]) == 2
    assert optimize_params.main(["nan"]) == 2

    monkeypatch.setattr(optimize_params, "resolve_bracket", None)
    monkeypatch.setattr(optimize_params, "profile_overrides", None)
    monkeypatch.setattr(optimize_params, "recommend_from_audit", None)
    assert optimize_params.main(["0"]) == 0
    assert "unavailable" in capsys.readouterr().out


def test_profit_row_parser_and_analyze_errors(tmp_path):
    path = tmp_path / "rows.db"
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE sample (metadata TEXT)")
        connection.execute("INSERT INTO sample VALUES ('not-json')")
        row = connection.execute("SELECT * FROM sample").fetchone()
    assert profit_audit._row_to_dict(row)["metadata"] == {}

    stats = profit_audit.analyze_db(str(tmp_path / "no-schema.db"))
    assert "cannot read trades table" in stats["error"]
    assert "error" in profit_audit.analyze_db(str(tmp_path))


def _report_stats():
    return {
        "modes": {
            "DEMO": {
                "closed_trades": 2,
                "net_pnl": 5.0,
                "by_strategy": {
                    "structure": {
                        "trades": 2,
                        "wins": 1,
                        "losses": 1,
                        "win_rate": 50.0,
                        "net_pnl": 5.0,
                        "avg_win": 10.0,
                        "avg_loss": 5.0,
                        "realized_rr": 2.0,
                        "expectancy_per_trade": 2.5,
                        "cost_leaks": 0,
                        "verdict": "PROFITABLE",
                    }
                },
            },
            "REAL": {"closed_trades": 0, "net_pnl": 0.0, "by_strategy": {}},
        },
        "total_closed_trades": 2,
        "total_net_pnl": 5.0,
        "assumed_round_trip_costs_pct": 0.2,
    }


def test_profit_report_and_recommendations(monkeypatch, capsys):
    profit_audit.print_report({"error": "broken"})
    assert "ERROR: broken" in capsys.readouterr().out

    stats = _report_stats()
    profit_audit.print_report(stats)
    output = capsys.readouterr().out
    assert "structure" in output
    assert "no closed trades" in output

    recommendation = {
        "bracket": "MICRO",
        "account_balance": 5.0,
        "targets": {
            "min_win_rate_pct": 45,
            "min_realized_rr": 1.5,
            "min_expectancy_r": 0,
            "min_profit_factor": 1.2,
        },
        "health_verdict": "HEALTHY",
        "best_strategy": {"name": "structure", "expectancy": 0.25},
        "recommended_settings": {"risk_pct": 0.5},
        "per_strategy": {
            "structure": {
                "verdict": "PROFITABLE",
                "action": "KEEP",
                "recommend": "Maintain",
            }
        },
    }
    monkeypatch.setattr(profit_audit, "recommend_from_audit", lambda stats, balance: recommendation)
    profit_audit.print_recommendations(stats, 5)
    assert "Best strategy: structure" in capsys.readouterr().out

    monkeypatch.setattr(profit_audit, "recommend_from_audit", None)
    profit_audit.print_recommendations(stats)
    assert "skipping" in capsys.readouterr().out


def test_profit_main_success_and_input_errors(tmp_path, capsys):
    db_path = tmp_path / "audit.db"
    DatabaseManager(str(db_path))
    assert profit_audit.main([str(db_path), "10"]) == 0
    assert "PROFIT AUDIT" in capsys.readouterr().out
    assert profit_audit.main([str(db_path), "bad"]) == 2
    assert profit_audit.main([str(db_path), "inf"]) == 2
    assert profit_audit.main([str(tmp_path / "missing-schema.db")]) == 1


def test_smoke_test_success_http_failure_exception_and_url(monkeypatch, capsys):
    calls = []

    def ok_get(url, timeout):
        calls.append((url, timeout))
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(smoke_test.httpx, "get", ok_get)
    assert smoke_test.run_smoke_test("https://service.test/") is True
    assert len(calls) == 10
    assert calls[0][0] == "https://service.test/healthz"

    count = {"value": 0}

    def mixed_get(url, timeout):
        count["value"] += 1
        if count["value"] == 1:
            return SimpleNamespace(status_code=503)
        if count["value"] == 2:
            raise RuntimeError("offline")
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(smoke_test.httpx, "get", mixed_get)
    assert smoke_test.run_smoke_test("http://service.test") is False
    assert "2 endpoint(s) unhealthy" in capsys.readouterr().out
    assert smoke_test.run_smoke_test("service.test") is False
    assert "must start with" in capsys.readouterr().err
