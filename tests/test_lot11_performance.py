import pytest
from api.engines.portfolio_engine import PortfolioEngine
from api.engines.db_manager import DatabaseManager

def test_performance_calculation():
    db = DatabaseManager("data/test_lot11.db")
    portfolio = PortfolioEngine(db)
    
    # Simuler des trades avec différentes stratégies
    trades = [
        {"id": "P1", "mode": "DEMO", "symbol": "BTC", "direction": "BUY", "pnl": 100.0, "status": "CLOSED", "metadata": {"strategy": "arbitrage"}},
        {"id": "P2", "mode": "DEMO", "symbol": "ETH", "direction": "SELL", "pnl": -50.0, "status": "CLOSED", "metadata": {"strategy": "arbitrage"}},
        {"id": "P3", "mode": "DEMO", "symbol": "SOL", "direction": "BUY", "pnl": 200.0, "status": "CLOSED", "metadata": {"strategy": "tape"}},
    ]
    
    for t in trades:
        db.save_trade(t)
        
    report = portfolio.get_performance_report("DEMO")
    
    assert report["overall"]["total_trades"] == 3
    assert report["overall"]["total_pnl"] == 250.0
    assert "arbitrage" in report["by_strategy"]
    assert report["by_strategy"]["arbitrage"]["win_rate"] == 50.0
    assert report["by_strategy"]["tape"]["net_pnl"] == 200.0
    assert report["expectancy"] > 0
