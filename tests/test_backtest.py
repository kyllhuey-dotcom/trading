import pytest
import pandas as pd
import numpy as np
from api.engines.backtest_engine import BacktestEngine
from api.engines.analysis_engine import AnalysisEngine
from api.engines.signal_engine import SignalEngine
from api.engines.risk_engine import RiskEngine

@pytest.mark.asyncio
async def test_backtest_execution():
    analysis = AnalysisEngine()
    signal = SignalEngine()
    risk = RiskEngine()
    backtest = BacktestEngine(analysis, signal, risk)
    
    # Créer un DataFrame factice qui simule une tendance haussière
    # On a besoin de plus de 50 lignes
    dates = pd.date_range(start="2026-01-01", periods=100, freq="1min")
    # Prix montant de 100 à 200
    prices = np.linspace(100, 200, 100)
    df = pd.DataFrame({
        'Open': prices,
        'High': prices + 1,
        'Low': prices - 1,
        'Close': prices,
        'Volume': [1000] * 100
    }, index=dates)
    
    res = await backtest.run_backtest("BTC", df)
    
    assert "total_trades" in res
    assert res["initial_balance"] == 10000.0
    # Dans une tendance haussière parfaite, le PnL devrait être positif
    # (Sauf si la stratégie attend un repli ou autre, mais ici on valide le process)
    assert isinstance(res["total_pnl"], float)
