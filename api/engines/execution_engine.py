from .db_manager import DatabaseManager
from datetime import datetime
from typing import List, Dict, Any, Optional

class ExecutionEngine:
    """
    Simulates DEMO execution using real prices (Rule 27).
    Prepares interface for REAL mode (Lot 8).
    Handles forced exits on market close (Lot 27).
    """
    def __init__(self, portfolio: Any, db_manager: DatabaseManager, risk_engine: Any, universe: Any):
        self.portfolio = portfolio
        self.db = db_manager
        self.risk = risk_engine
        self.universe = universe

    @property
    def active_positions(self):
        return self.db.get_active_positions()

    async def execute_order(self, mode: str, signal: Dict[str, Any], risk: Dict[str, Any], ticker: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes order using real Bid/Ask prices.
        Checks market status before opening.
        """
        mid = signal.get("market_id")
        
        # Rule: Never open position if market is closed
        if self.universe.get_market_status(mid) != "OPEN":
            return {"success": False, "reason": "MARKET_CLOSED"}

        active = self.active_positions
        # Check if already in this symbol
        if any(p["symbol"] == signal.get("market_id") for p in active):
            return {"success": False, "reason": "Position already open for this asset"}

        # Simulated fill based on real bid/ask
        entry_price = ticker.get('ask') if signal["direction"] == "BUY" else ticker.get('bid')
        if not entry_price: entry_price = ticker.get('last')

        # Realistic slippage (0.01%)
        slippage = 0.0001 
        if signal["direction"] == "BUY":
            entry_price *= (1 + slippage)
        else:
            entry_price *= (1 - slippage)

        position = {
            "id": f"SIM-{int(datetime.now().timestamp())}",
            "mode": mode,
            "symbol": signal.get("market_id"),
            "display_symbol": signal.get("display_symbol"),
            "direction": signal["direction"],
            "entry_price": float(entry_price),
            "quantity": risk["quantity"],
            "sl": signal["sl"],
            "tp": signal["tp"],
            "leverage": risk["leverage"],
            "fees": risk["estimated_fees"] / 2,
            "open_time": datetime.now().isoformat(),
            "status": "OPEN",
            "pnl": - (risk["estimated_fees"] / 2)
        }

        self.db.save_trade(position)
        return {"success": True, "position": position}

    async def update_active_positions(self, mode: str, tickers: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Update unrealized P&L and check exits (SL/TP) with real prices.
        """
        closed_trades = []
        active = self.db.get_active_positions(mode)
        
        for pos in active:
            ticker = tickers.get(pos["display_symbol"]) or tickers.get(pos["symbol"])
            if not ticker: continue
            
            # Exit price: BUY -> sell at BID. SELL -> buy at ASK.
            current_exit_price = ticker.get('bid') if pos["direction"] == "BUY" else ticker.get('ask')
            if not current_exit_price: current_exit_price = ticker.get('last')
            
            # PnL Calculation
            if pos["direction"] == "BUY":
                pnl = (current_exit_price - pos["entry_price"]) * pos["quantity"]
            else:
                pnl = (pos["entry_price"] - current_exit_price) * pos["quantity"]
            
            pos["pnl"] = float(pnl)

            # 1. Check if market is still open (Lot 27 Rule)
            # If market is closed, we force close the position at last known price
            if self.universe.get_market_status(pos["symbol"]) != "OPEN":
                pos["status"] = "CLOSED"
                pos["exit_price"] = float(current_exit_price)
                pos["close_time"] = datetime.now().isoformat()
                pos["pnl"] -= (pos["fees"]) 
                pos["metadata"] = {"close_reason": "MARKET_CLOSED_PROTECTION"}
                
                self.portfolio.update_balance(mode, pos["pnl"])
                self.db.save_trade(pos)
                closed_trades.append(pos)
                continue

            # 2. SL / TP check
            hit_sl = (pos["direction"] == "BUY" and current_exit_price <= pos["sl"]) or \
                     (pos["direction"] == "SELL" and current_exit_price >= pos["sl"])
            hit_tp = (pos["direction"] == "BUY" and current_exit_price >= pos["tp"]) or \
                     (pos["direction"] == "SELL" and current_exit_price <= pos["tp"])

            if hit_sl or hit_tp:
                pos["status"] = "CLOSED"
                pos["exit_price"] = float(current_exit_price)
                pos["close_time"] = datetime.now().isoformat()
                pos["pnl"] -= (pos["fees"]) 
                
                self.portfolio.update_balance(mode, pos["pnl"])
                if pos["pnl"] < 0:
                    self.risk.last_loss_time = datetime.now()
                self.db.save_trade(pos)
                closed_trades.append(pos)
            else:
                # Update latent PnL in DB? Optional, but good for persistence
                self.db.save_trade(pos)
        
        return closed_trades

    def clear_active_positions(self, mode: str):
        # Mark all active as CLOSED or just delete? Better to mark as CLOSED/CANCELLED
        active = self.db.get_active_positions(mode)
        for pos in active:
            pos["status"] = "CLOSED"
            pos["close_time"] = datetime.now().isoformat()
            self.db.save_trade(pos)
