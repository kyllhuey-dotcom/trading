from .db_manager import DatabaseManager
from datetime import datetime
from typing import List, Dict, Any, Optional
import asyncio

class ExecutionEngine:
    """
    Simulates DEMO execution using real prices (Rule 27).
    Prepares interface for REAL mode (Lot 8).
    Handles forced exits on market close (Lot 27).
    """
    def __init__(self, portfolio: Any, db_manager: DatabaseManager, risk_engine: Any, universe: Any, notification_engine: Any = None):
        self.portfolio = portfolio
        self.db = db_manager
        self.risk = risk_engine
        self.universe = universe
        self.notifications = notification_engine

    @property
    def active_positions(self):
        return self.db.get_active_positions()

    async def execute_order(self, mode: str, signal: Dict[str, Any], risk: Dict[str, Any], ticker: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes order using real Bid/Ask prices.
        Includes realistic paper trading simulation (Lot 13).
        """
        mid = signal.get("market_id")
        
        # Load settings for simulation
        with self.db._get_connection() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            settings = {r["key"]: r["value"] for r in rows}
            
        latency_ms = int(settings.get("sim_latency_ms", "100"))
        slippage_pct = float(settings.get("sim_slippage_pct", "0.05"))
        rejection_prob = float(settings.get("sim_rejection_prob", "0.01"))

        # 1. Simuler Rejet Aléatoire
        import random
        if random.random() < rejection_prob:
            return {"success": False, "reason": "SIMULATED_BROKER_REJECTION"}

        # 2. Simuler Latence
        await asyncio.sleep(latency_ms / 1000.0)

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

        # Realistic slippage (configurable Lot 13)
        slippage = slippage_pct / 100.0
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
            "pnl": - (risk["estimated_fees"] / 2),
            "metadata": {
                "atr": signal.get("atr", 0),
                "strategy": signal.get("strategy", "structure")
            }
        }

        self.db.save_trade(position)
        return {"success": True, "position": position}

    async def update_active_positions(self, mode: str, tickers: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Update unrealized P&L and check exits (SL/TP) with real prices.
        Includes Trailing Stop, Partial TP and Break-even (Lot 9).
        """
        closed_trades = []
        active = self.db.get_active_positions(mode)
        
        # Load settings for advanced management
        with self.db._get_connection() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            settings = {r["key"]: r["value"] for r in rows}
            
        ts_active = settings.get("trailing_stop_active", "false").lower() == "true"
        ts_dist_atr = float(settings.get("trailing_stop_distance_atr", "1.5"))
        partial_tp_ratio = float(settings.get("partial_tp_ratio", "1.0")) # 1:1 RR for partial
        
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
            metadata = pos.get("metadata") or {}

            # 1. Check if market is still open (Lot 27 Rule)
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

            # 2. PARTIAL TAKE PROFIT (Lot 9)
            # Close 50% at 1:1 RR if not already done
            risk_dist = abs(pos["entry_price"] - pos["sl"])
            if risk_dist > 0 and not metadata.get("partial_tp_hit"):
                if (pos["direction"] == "BUY" and current_exit_price >= pos["entry_price"] + (risk_dist * partial_tp_ratio)) or \
                   (pos["direction"] == "SELL" and current_exit_price <= pos["entry_price"] - (risk_dist * partial_tp_ratio)):
                    
                    # Close 50%
                    close_qty = pos["quantity"] / 2
                    partial_pnl = (risk_dist * partial_tp_ratio) * close_qty
                    
                    # Update Balance and Position
                    self.portfolio.update_balance(mode, partial_pnl)
                    pos["quantity"] -= close_qty
                    metadata["partial_tp_hit"] = True
                    metadata["partial_pnl"] = partial_pnl
                    
                    # BREAK-EVEN: Move SL to entry
                    pos["sl"] = pos["entry_price"]
                    metadata["break_even_active"] = True
                    
                    # Log Audit
                    self.db.log_audit("INFO", "PARTIAL_TP", f"Hit Partial TP on {pos['symbol']}, SL moved to Break-even.")

            # 3. TRAILING STOP (Lot 9)
            # If TS active, move SL in favor of price
            if ts_active:
                # We need ATR for distance. Metadata should have saved ATR from signal.
                atr = metadata.get("atr", risk_dist / 2) # Fallback to risk/2
                if pos["direction"] == "BUY":
                    new_sl = current_exit_price - (atr * ts_dist_atr)
                    if new_sl > pos["sl"]:
                        pos["sl"] = float(new_sl)
                else:
                    new_sl = current_exit_price + (atr * ts_dist_atr)
                    if new_sl < pos["sl"] or pos["sl"] == 0:
                        pos["sl"] = float(new_sl)

            # 4. FINAL SL / TP check
            hit_sl = (pos["direction"] == "BUY" and current_exit_price <= pos["sl"]) or \
                     (pos["direction"] == "SELL" and current_exit_price >= pos["sl"])
            hit_tp = (pos["direction"] == "BUY" and current_exit_price >= pos["tp"]) or \
                     (pos["direction"] == "SELL" and current_exit_price <= pos["tp"])

            if hit_sl or hit_tp:
                pos["status"] = "CLOSED"
                pos["exit_price"] = float(current_exit_price)
                pos["close_time"] = datetime.now().isoformat()
                pos["pnl"] -= (pos["fees"]) 
                metadata["close_reason"] = "SL_HIT" if hit_sl else "TP_HIT"
                pos["metadata"] = metadata
                
                self.portfolio.update_balance(mode, pos["pnl"])
                if pos["pnl"] < 0:
                    self.risk.last_loss_time = datetime.now()
                self.db.save_trade(pos)
                closed_trades.append(pos)
                
                # Notification (Lot 10)
                if self.notifications:
                    asyncio.create_task(self.notifications.notify("ORDER_CLOSE", pos))
            else:
                pos["metadata"] = metadata
                self.db.save_trade(pos)
        
        return closed_trades

    def clear_active_positions(self, mode: str):
        # Mark all active as CLOSED or just delete? Better to mark as CLOSED/CANCELLED
        active = self.db.get_active_positions(mode)
        for pos in active:
            pos["status"] = "CLOSED"
            pos["close_time"] = datetime.now().isoformat()
            self.db.save_trade(pos)
