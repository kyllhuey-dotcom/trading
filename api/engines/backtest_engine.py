import copy
import math
from typing import Any, Dict

import pandas as pd

from .analysis_engine import AnalysisEngine
from .risk_engine import RiskEngine
from .signal_engine import SignalEngine


class BacktestEngine:
    """
    Simple historical backtesting engine (Lot 12).
    Validates strategies against past OHLCV data.
    """

    REQUIRED_COLUMNS = {"High", "Low", "Close"}

    def __init__(self, analysis_engine: AnalysisEngine, signal_engine: SignalEngine,
                 risk_engine: RiskEngine):
        self.analysis = analysis_engine
        self.signal = signal_engine
        self.risk = risk_engine

    async def run_backtest(self, symbol: str, df: pd.DataFrame,
                           initial_balance: float = 10000.0,
                           strategy_mode: str = "structure") -> Dict[str, Any]:
        """Run an isolated, conservative bar-by-bar backtest."""
        if not isinstance(df, pd.DataFrame) or df.empty or len(df) < 50:
            return {"error": "Insufficient data for backtest"}
        missing_columns = sorted(self.REQUIRED_COLUMNS.difference(df.columns))
        if missing_columns:
            return {"error": f"Missing OHLCV columns: {', '.join(missing_columns)}"}
        try:
            initial_balance = float(initial_balance)
        except (TypeError, ValueError):
            return {"error": "initial_balance must be a positive finite number"}
        if not math.isfinite(initial_balance) or initial_balance <= 0:
            return {"error": "initial_balance must be a positive finite number"}

        # Backtests must never mutate live risk state (peak balance, loss streak,
        # cooldown). A shallow copy is sufficient because RiskEngine state is
        # scalar-only.
        risk_engine = copy.copy(self.risk)
        risk_engine.daily_pnl = 0.0
        risk_engine.last_loss_time = None
        risk_engine.consecutive_losses = 0
        risk_engine.peak_balance = initial_balance

        balance = initial_balance
        trades = []
        active_position = None

        # Ensure 'Timestamp' exists without turning a RangeIndex into all zeros.
        if "Timestamp" not in df.columns:
            df = df.copy()
            if isinstance(df.index, pd.DatetimeIndex):
                df["Timestamp"] = df.index.astype("int64") // 10**6
            else:
                df["Timestamp"] = range(len(df))

        # Start at bar 50 to have enough indicator history.
        for i in range(50, len(df)):
            current_df = df.iloc[:i + 1]
            current_bar = df.iloc[i]

            # Update an active position using intrabar High/Low. If both SL and
            # TP are touched in one candle, assume SL first (conservative and
            # deterministic without lower-timeframe data).
            if active_position:
                high = float(current_bar["High"])
                low = float(current_bar["Low"])
                if active_position["direction"] == "BUY":
                    hit_sl = low <= active_position["sl"]
                    hit_tp = high >= active_position["tp"]
                else:
                    hit_sl = high >= active_position["sl"]
                    hit_tp = low <= active_position["tp"]

                if hit_sl or hit_tp:
                    exit_price = active_position["sl"] if hit_sl else active_position["tp"]
                    if active_position["direction"] == "BUY":
                        pnl = (exit_price - active_position["entry"]) * active_position["qty"]
                    else:
                        pnl = (active_position["entry"] - exit_price) * active_position["qty"]
                    # Round-trip fees: 0.05% taker fee on entry and exit.
                    fees = (
                        active_position["entry"] * active_position["qty"]
                        + exit_price * active_position["qty"]
                    ) * 0.0005
                    pnl -= fees

                    balance += pnl
                    active_position["status"] = "CLOSED"
                    active_position["exit"] = exit_price
                    active_position["pnl"] = round(pnl, 6)
                    active_position["fees"] = round(fees, 6)
                    active_position["close_time"] = str(current_bar.name)
                    trades.append(active_position)
                    risk_engine.register_closed_trade(pnl)
                    active_position = None

            # Look for a new signal when flat.
            if not active_position:
                analysis = self.analysis.identify_structure(current_df)
                analysis["market_id"] = symbol
                news_status = {
                    "trading_allowed": True,
                    "news_ok": True,
                    "day_ok": True,
                    "session_ok": True,
                }
                signal = self.signal.generate_signal(
                    analysis,
                    news_status,
                    current_df,
                    strategy_mode=strategy_mode,
                    market_id=symbol,
                )

                if signal.get("status") == "SIGNAL_DETECTED":
                    risk_data = risk_engine.calculate_position_size(
                        balance=balance,
                        entry=signal["entry"],
                        stop_loss=signal["sl"],
                        direction=signal["direction"],
                    )
                    if risk_data.get("allowed"):
                        active_position = {
                            "symbol": symbol,
                            "direction": signal["direction"],
                            "entry": signal["entry"],
                            "sl": signal["sl"],
                            "tp": signal["tp"],
                            "qty": risk_data["quantity"],
                            "open_time": str(current_bar.name),
                            "status": "OPEN",
                            "strategy": strategy_mode,
                        }

        wins = [trade for trade in trades if trade["pnl"] > 0]
        win_rate = (len(wins) / len(trades) * 100) if trades else 0.0
        total_pnl = balance - initial_balance
        return {
            "symbol": symbol,
            "initial_balance": initial_balance,
            "final_balance": round(balance, 2),
            "total_pnl": round(total_pnl, 2),
            "win_rate": round(win_rate, 2),
            "total_trades": len(trades),
            "trades": trades[-50:],
            "open_position": active_position,
        }
