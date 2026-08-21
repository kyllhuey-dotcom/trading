from typing import Dict, Any, Optional, List
from datetime import datetime

class RiskEngine:
    def __init__(self, 
                 max_risk_pct: float = 1.0, 
                 max_leverage: int = 20, 
                 min_account_balance: float = 10.0,
                 max_daily_loss_pct: float = 3.0,
                 max_drawdown_pct: float = 5.0):
        self.max_risk_pct = max_risk_pct
        self.max_leverage = max_leverage
        self.min_account_balance = min_account_balance
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_drawdown_pct = max_drawdown_pct
        self.daily_pnl = 0.0
        self.peak_balance = 0.0
        self.last_loss_time: Optional[datetime] = None
        self.cool_down_mins = 30

    def update_peak(self, balance: float):
        if balance > self.peak_balance:
            self.peak_balance = balance

    def check_global_safety(self, balance: float, daily_pnl: float) -> Dict[str, Any]:
        self.update_peak(balance)
        current_drawdown = ((self.peak_balance - balance) / self.peak_balance * 100) if self.peak_balance > 0 else 0
        
        if current_drawdown > self.max_drawdown_pct:
            return {"safe": False, "reason": f"Max Drawdown Limit Hit ({current_drawdown:.2f}%)"}
            
        if daily_pnl < -(balance * (self.max_daily_loss_pct / 100)):
            return {"safe": False, "reason": f"Daily Loss Limit Hit ({daily_pnl:.2f})"}
            
        return {"safe": True}

    def check_correlation(self, symbol: str, active_positions: List[Dict[str, Any]]) -> Dict[str, Any]:
        base_asset = symbol.split('_')[0] if '_' in symbol else symbol
        symbol_count = sum(1 for p in active_positions if (p.get("symbol", "").split('_')[0] == base_asset))
        
        if symbol_count >= 1:
            return {"allowed": False, "reason": f"Correlation Risk: {base_asset}"}
        if len(active_positions) >= 10:
             return {"allowed": False, "reason": "Max Concurrent Positions"}
        return {"allowed": True}

    def calculate_position_size(self, balance: float, entry: float, stop_loss: float, direction: str = "BUY", fee_pct: float = 0.05, symbol: str = "unknown", active_positions: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        if active_positions is not None:
            corr = self.check_correlation(symbol, active_positions)
            if not corr["allowed"]: return corr

        risk_amount = balance * (self.max_risk_pct / 100)
        dist = abs(entry - stop_loss)
        if dist == 0: return {"allowed": False, "reason": "Zero SL distance"}

        qty = risk_amount / dist
        notional = qty * entry
        lev = notional / balance
        
        if lev > self.max_leverage:
            qty = (balance * self.max_leverage) / entry
            lev = self.max_leverage
            notional = qty * entry

        return {
            "allowed": notional >= 10.0,
            "quantity": float(qty),
            "leverage": float(lev),
            "estimated_fees": float(notional * (fee_pct / 100) * 2),
            "reason": "Order size too small" if notional < 10.0 else None
        }
