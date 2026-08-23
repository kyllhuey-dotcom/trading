"""Unit tests for QuarantineManager (offline)."""
from api.engines.quarantine import QuarantineManager, get_quarantine_manager


def test_record_winning_trades_no_quarantine():
    mgr = QuarantineManager(min_trades=5)
    for _ in range(5):
        mgr.record_trade("btc_usdt", "rsi", pnl=10.0, risk_amount=5.0)
    assert not mgr.is_quarantined("btc_usdt", "rsi")
    assert mgr.get_quarantined() == set()


def test_negative_expectancy_quarantines():
    mgr = QuarantineManager(min_trades=3)
    mgr.record_trade("eth_usdt", "rsi", pnl=-8.0, risk_amount=4.0)
    mgr.record_trade("eth_usdt", "rsi", pnl=-2.0, risk_amount=4.0)
    mgr.record_trade("eth_usdt", "rsi", pnl=-1.0, risk_amount=4.0)
    assert mgr.is_quarantined("eth_usdt", "rsi")
    stats = mgr.get_stats()
    assert stats["total_quarantined"] == 1
    assert stats["quarantined_pairs"][0]["market"] == "eth_usdt"
    assert "negative_expectancy" in stats["quarantined_pairs"][0]["reason"]


def test_already_quarantined_skips_recheck():
    mgr = QuarantineManager(min_trades=2)
    mgr.record_trade("x", "rsi", pnl=-1, risk_amount=1)
    mgr.record_trade("x", "rsi", pnl=-1, risk_amount=1)
    first = dict(mgr.quarantined[("x", "rsi")])
    mgr.record_trade("x", "rsi", pnl=-5, risk_amount=1)
    assert mgr.quarantined[("x", "rsi")]["trades"] == first["trades"]


def test_low_profit_factor_and_net_rr():
    mgr = QuarantineManager(min_trades=4)
    # Mix of small wins and larger losses -> PF < 1 and low RR possible
    mgr.record_trade("sol", "rsi", pnl=1.0, risk_amount=2.0)
    mgr.record_trade("sol", "rsi", pnl=1.0, risk_amount=2.0)
    mgr.record_trade("sol", "rsi", pnl=-3.0, risk_amount=2.0)
    mgr.record_trade("sol", "rsi", pnl=-3.0, risk_amount=2.0)
    assert mgr.is_quarantined("sol", "rsi")


def test_all_wins_profit_factor_zero_path():
    mgr = QuarantineManager(min_trades=2)
    mgr.record_trade("aaa", "rsi", pnl=5, risk_amount=1)
    mgr.record_trade("aaa", "rsi", pnl=5, risk_amount=1)
    # win_rate == 1 => profit_factor computed as 0; expectancy > 0 so no quarantine
    assert not mgr.is_quarantined("aaa", "rsi")


def test_release_and_reset():
    mgr = QuarantineManager(min_trades=1)
    mgr.record_trade("m", "s", pnl=-1, risk_amount=1)
    assert mgr.release_quarantine("m", "s") is True
    assert mgr.release_quarantine("m", "s") is False
    mgr.record_trade("m", "s", pnl=-1, risk_amount=1)
    mgr.reset()
    assert mgr.get_stats()["total_tracked"] == 0
    assert mgr.get_stats()["total_quarantined"] == 0


def test_get_quarantine_manager_singleton():
    a = get_quarantine_manager()
    b = get_quarantine_manager()
    assert a is b
