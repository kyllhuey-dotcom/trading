from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import logging

logger = logging.getLogger("RiskEngine")


class RiskEngine:
    """
    Risk Engine (Rules: money management, protection).
    - Position sizing based on cash risk (% of balance / SL distance)
    - Leverage cap
    - SL side validation (BUY -> SL must be below entry, SELL -> SL must be above entry)
    - Daily loss limit enforced at ORDER level (not only at tick level)
    - Cool-down after a losing trade
    - Max concurrent positions + correlation filter
    - Global drawdown protection (peak balance persisted across restarts via settings)
    """

    def __init__(self,
                 max_risk_pct: float = 1.0,
                 max_leverage: int = 20,
                 min_account_balance: float = 10.0,
                 max_daily_loss_pct: float = 3.0,
                 max_drawdown_pct: float = 5.0,
                 max_open_positions: int = 10,
                 cool_down_mins: int = 30):
        self.max_risk_pct = max_risk_pct
        self.max_leverage = max_leverage
        self.min_account_balance = min_account_balance
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_drawdown_pct = max_drawdown_pct
        self.max_open_positions = max_open_positions
        self.cool_down_mins = cool_down_mins
        self.daily_pnl = 0.0
        self.peak_balance = 0.0
        self.last_loss_time: Optional[datetime] = None

    # ------------------------------------------------------------------ #
    # Configuration                                                       #
    # ------------------------------------------------------------------ #
    def apply_settings(self, settings: Dict[str, str]) -> None:
        """Reload risk parameters from the bot settings table (live re-configuration)."""
        try:
            self.max_risk_pct = float(settings.get("max_risk_pct", self.max_risk_pct))
            self.max_leverage = float(settings.get("max_leverage", self.max_leverage))
            self.max_daily_loss_pct = float(settings.get("max_daily_loss_pct", self.max_daily_loss_pct))
            self.cool_down_mins = int(float(settings.get("cool_down_mins", self.cool_down_mins)))
            self.max_open_positions = int(float(settings.get("max_open_positions", self.max_open_positions)))
            self.max_drawdown_pct = float(settings.get("emergency_stop_drawdown_pct", self.max_drawdown_pct))
            persisted_peak = float(settings.get("peak_balance", 0.0) or 0.0)
            if persisted_peak > self.peak_balance:
                # Restore peak across restarts so drawdown protection survives redeploys
                self.peak_balance = persisted_peak
        except (TypeError, ValueError) as e:
            logger.warning(f"Invalid risk setting ignored: {e}")

    # ------------------------------------------------------------------ #
    # Trade lifecycle                                                     #
    # ------------------------------------------------------------------ #
    def register_closed_trade(self, pnl: float) -> None:
        """Called by the execution engine whenever a trade closes (win or loss)."""
        self.daily_pnl += pnl
        if pnl < 0:
            self.last_loss_time = datetime.now()

    def update_peak(self, balance: float) -> None:
        if balance > self.peak_balance:
            self.peak_balance = balance

    def get_current_drawdown_pct(self, balance: float) -> float:
        if self.peak_balance <= 0:
            return 0.0
        return ((self.peak_balance - balance) / self.peak_balance) * 100

    def check_global_safety(self, balance: float, daily_pnl: float) -> Dict[str, Any]:
        """Global circuit-breaker checks (executed by the capital tick loop)."""
        self.update_peak(balance)
        self.daily_pnl = daily_pnl  # sync with portfolio (covers restarts)
        current_drawdown = self.get_current_drawdown_pct(balance)

        if current_drawdown > self.max_drawdown_pct:
            return {"safe": False, "reason": f"Max Drawdown Limit Hit ({current_drawdown:.2f}%)"}

        if daily_pnl < -(balance * (self.max_daily_loss_pct / 100)):
            return {"safe": False, "reason": f"Daily Loss Limit Hit ({daily_pnl:.2f})"}

        return {"safe": True}

    # ------------------------------------------------------------------ #
    # Pre-trade checks                                                    #
    # ------------------------------------------------------------------ #
    def check_correlation(self, symbol: str, active_positions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Block re-entry on the same base asset + cap total open positions."""
        if not active_positions:
            active_positions = []
        base_asset = symbol.split('_')[0] if '_' in symbol else symbol
        symbol_count = sum(1 for p in active_positions if (p.get("symbol", "").split('_')[0] == base_asset))

        if symbol_count >= 1:
            return {"allowed": False, "reason": f"Correlation Risk: {base_asset} already open"}
        if len(active_positions) >= self.max_open_positions:
            return {"allowed": False, "reason": f"Max Concurrent Positions reached ({self.max_open_positions})"}
        return {"allowed": True}

    # ------------------------------------------------------------------ #
    # Position sizing                                                     #
    # ------------------------------------------------------------------ #
    def calculate_position_size(self,
                                balance: float,
                                entry: float,
                                stop_loss: float,
                                direction: str = "BUY",
                                fee_pct: float = 0.05,
                                symbol: str = "unknown",
                                active_positions: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        Full pre-trade validation + sizing. Returns {allowed, reason, ...}.
        Every blocking check returns a precise reason (never a bare boolean).
        """
        # 1. Account balance floor
        if balance < self.min_account_balance:
            return {"allowed": False, "reason": f"Balance below minimum ({self.min_account_balance})", "risk_pct": self.max_risk_pct}

        # 2. Correlation + max concurrent positions
        corr = self.check_correlation(symbol, active_positions or [])
        if not corr["allowed"]:
            corr["risk_pct"] = self.max_risk_pct
            return corr

        # 3. Daily loss limit — enforced here, at ORDER level
        if self.daily_pnl < -(balance * (self.max_daily_loss_pct / 100)):
            return {"allowed": False,
                    "reason": f"Max Daily Loss reached ({self.daily_pnl:.2f} / -{self.max_daily_loss_pct}%)",
                    "risk_pct": self.max_risk_pct}

        # 4. Cool-down after a losing trade
        if self.last_loss_time:
            elapsed = (datetime.now() - self.last_loss_time).total_seconds() / 60.0
            if elapsed < self.cool_down_mins:
                remaining = round(self.cool_down_mins - elapsed, 1)
                return {"allowed": False,
                        "reason": f"Cool-down active after loss ({remaining} min remaining)",
                        "risk_pct": self.max_risk_pct}

        # 5. SL side validation — the SL must protect, not guarantee a loss
        if direction == "BUY" and stop_loss >= entry:
            return {"allowed": False,
                    "reason": f"Invalid SL for BUY: stop ({stop_loss}) must be below entry ({entry})",
                    "risk_pct": self.max_risk_pct}
        if direction == "SELL" and stop_loss <= entry:
            return {"allowed": False,
                    "reason": f"Invalid SL for SELL: stop ({stop_loss}) must be above entry ({entry})",
                    "risk_pct": self.max_risk_pct}

        # 6. Zero-distance guard
        dist = abs(entry - stop_loss)
        if dist <= 0:
            return {"allowed": False, "reason": "Zero SL distance", "risk_pct": self.max_risk_pct}

        # 7. Sizing: risk a fixed % of balance, then cap by leverage
        risk_amount = balance * (self.max_risk_pct / 100)
        qty = risk_amount / dist
        notional = qty * entry
        lev = notional / balance

        if lev > self.max_leverage:
            qty = (balance * self.max_leverage) / entry
            lev = self.max_leverage
            notional = qty * entry

        notional_ok = notional >= 10.0
        return {
            "allowed": notional_ok,
            "quantity": float(qty),
            "leverage": float(lev),
            "risk_pct": self.max_risk_pct,
            "estimated_fees": float(notional * (fee_pct / 100) * 2),
            "reason": None if notional_ok else "Order size too small"
        }
