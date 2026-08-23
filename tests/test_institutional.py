from fastapi.testclient import TestClient
from api.engines.institutional_executor import select_candidates, describe_intent
from api.engines.metrics_engine import MetricsEngine
from api.index import app

client = TestClient(app)


def _row(sym, score, tradable=True, extra=None):
    r = {"symbol": sym, "score": score, "tradable": tradable,
         "signal_data": {"market_id": sym, "entry": 1.0, "sl": 0.9, "strategy": "rsi"}}
    if extra:
        r.update(extra)
    return r


def test_select_best_first_slots_and_filters():
    rows = [
        _row("a", 90), _row("b", 95), _row("c", 70),
        _row("d", 88, tradable=False), _row("e", 91),
    ]
    c = select_candidates(rows, 80, {"e"}, 2)
    assert [x["symbol"] for x in c] == ["b"]  # 1 slot left, best first, e already open


def test_intent_codes():
    assert describe_intent(False, True, 1, 0, 10, 80)["code"] == "STOPPED"
    assert describe_intent(True, False, 1, 0, 10, 80)["code"] == "DISARMED"
    assert describe_intent(True, True, 0, 10, 10, 80)["code"] == "FULL"
    idle = describe_intent(True, True, 0, 0, 10, 80)
    assert idle["code"] == "IDLE" and "84" in idle["message"]
    assert describe_intent(True, True, 2, 1, 10, 80)["code"] == "EXECUTING"


def test_status_and_metrics_additive():
    r = client.get("/api/status?market_id=__nonexistent__")
    assert r.status_code == 200
    assert "execution_intent" in r.json()
    m = client.get("/api/metrics")
    assert m.status_code == 200
    assert "institutional" in m.json()


def test_metrics_counters():
    me = MetricsEngine()
    me.record_institutional("IDLE", n_active=2)
    me.record_institutional("EXECUTING", n_active=4, trades_above=1)
    snap = me.snapshot()["institutional"]
    assert snap["institutional_idle_ticks"] == 1
    assert snap["institutional_exec_ticks"] == 1
    assert snap["max_concurrent_seen"] == 4
    assert snap["trades_above_min_score"] == 1
