from typing import Dict, Any, Optional
from datetime import datetime

class RiskEngine:
    """
    Advanced Risk Engine (Rule 24, 25, 26, 36).
    Handles position sizing, leverage, capital protection, and drawdown management.
    """
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
        
        # State tracking (persisted via DatabaseManager in Lot 5 context if needed)
        self.daily_pnl = 0.0
        self.peak_balance = 0.0
        self.last_loss_time: Optional[datetime] = None
        self.cool_down_mins = 30

    def calculate_position_size(self, balance: float, entry: float, stop_loss: float, fee_pct: float = 0.05) -> Dict[str, Any]:
        """
        Calculates position size and leverage with advanced safety checks.
        """
        # Update peak balance for drawdown calculation
        if balance > self.peak_balance:
            self.peak_balance = balance

        # 0. COOL DOWN PROTECTION
        if self.last_loss_time:
            diff = (datetime.now() - self.last_loss_time).total_seconds() / 60
            if diff < self.cool_down_mins:
                return {"allowed": False, "reason": f"Cool down active ({int(self.cool_down_mins - diff)}m left)"}

        # 1. FAIL-SAFE: Account Balance (Rule 25)
        if balance < self.min_account_balance:
            return {"allowed": False, "reason": f"Account balance too low: {balance:.2f} < {self.min_account_balance}"}

        # 2. DRAWDOWN PROTECTION (Rule 36)
        current_drawdown = ((self.peak_balance - balance) / self.peak_balance * 100) if self.peak_balance > 0 else 0
        if current_drawdown > self.max_drawdown_pct:
            return {"allowed": False, "reason": f"Max Drawdown reached: {current_drawdown:.2f}% > {self.max_drawdown_pct}%"}

        # 3. DAILY LOSS PROTECTION (Rule 36)
        if self.daily_pnl < -(balance * (self.max_daily_loss_pct / 100)):
            return {"allowed": False, "reason": f"Max Daily Loss reached: {self.daily_pnl:.2f}€"}

        # 4. CALCULATE CASH RISK
        risk_amount = balance * (self.max_risk_pct / 100)
        
        # 5. STOP LOSS DISTANCE
        price_risk_per_unit = abs(entry - stop_loss)
        if price_risk_per_unit == 0:
            return {"allowed": False, "reason": "Invalid Stop Loss distance (Zero)"}

        # 6. QUANTITY & NOTIONAL VALUE
        quantity = risk_amount / price_risk_per_unit
        notional_value = quantity * entry
        
        # 7. LEVERAGE SAFETY (Rule 26)
        required_leverage = notional_value / balance
        
        if required_leverage > self.max_leverage:
            # Scale down position to fit max leverage
            safe_leverage = self.max_leverage
            notional_value = balance * safe_leverage
            quantity = notional_value / entry
            required_leverage = safe_leverage
            actual_risk_amount = quantity * price_risk_per_unit
            actual_risk_pct = (actual_risk_amount / balance) * 100
        else:
            actual_risk_pct = self.max_risk_pct
            actual_risk_amount = risk_amount

        # 8. MINIMUM ORDER SIZE (Rule 25)
        if notional_value < 10.0:
             return {
                "allowed": False, 
                "reason": f"Order size too small ({notional_value:.2f}€). Min: 10€",
                "notional": notional_value
             }

        # 9. ESTIMATION DES FRAIS (Rule 21)
        estimated_fees = notional_value * (fee_pct / 100) * 2

        return {
            "allowed": True,
            "balance": balance,
            "risk_amount": float(actual_risk_amount),
            "risk_pct": float(actual_risk_pct),
            "quantity": float(quantity),
            "notional_value": float(notional_value),
            "leverage": float(required_leverage),
            "estimated_fees": float(estimated_fees),
            "max_allowed_leverage": self.max_leverage,
            "current_drawdown": float(current_drawdown)
        }

    def update_peak(self, balance: float):
        if balance > self.peak_balance:
            self.peak_balance = balance

    def reset_daily(self):
        self.daily_pnl = 0.0
