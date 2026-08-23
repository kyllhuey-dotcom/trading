from .db_manager import DatabaseManager
from .order_types import normalize_order_type, should_fill_now, serialize_pending
from datetime import datetime
from typing import List, Dict, Any, Optional
import asyncio
import time
import uuid
import random


class ExecutionEngine:
    """
    DEMO execution engine: simulates fills with real market prices.
    - Unique position IDs (millisecond timestamp + random suffix)
    - Metadata (strategy/ATR) preserved through the position lifecycle
    - Realistic paper trading: latency, slippage, rejections (configurable)
    - Reports realized PnL to the RiskEngine (daily loss tracking)
    """

    def __init__(self, portfolio: Any, db_manager: DatabaseManager, risk_engine: Any,
                 universe: Any, notification_engine: Any = None):
        self.portfolio = portfolio
        self.db = db_manager
        self.risk = risk_engine
        self.universe = universe
        self.notifications = notification_engine
        self.pending_orders: List[Dict[str, Any]] = []

    @property
    def active_positions(self):
        return self.db.get_active_positions()

    def _load_settings(self) -> Dict[str, str]:
        with self.db._get_connection() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            return {r["key"]: r["value"] for r in rows}

    async def execute_order(self, mode: str, signal: Dict[str, Any], risk: Dict[str, Any],
                            ticker: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes an order using real Bid/Ask prices with realistic simulation.
        Returns {"success": bool, "reason": str, "position": {...}}.
        """
        mid = signal.get("market_id")
        if not mid:
            return {"success": False, "reason": "MISSING_MARKET_ID"}

        settings = self._load_settings()
        latency_ms = int(settings.get("sim_latency_ms", "100"))
        slippage_pct = float(settings.get("sim_slippage_pct", "0.05"))
        rejection_prob = float(settings.get("sim_rejection_prob", "0.01"))

        # 1. Random simulated rejection
        if random.random() < rejection_prob:
            return {"success": False, "reason": "SIMULATED_BROKER_REJECTION"}

        # 2. Simulated latency
        await asyncio.sleep(latency_ms / 1000.0)

        # 3. Never open a position on a closed market
        if self.universe.get_market_status(mid) != "OPEN":
            return {"success": False, "reason": "MARKET_CLOSED"}

        # 4. Never double-open the same symbol
        active = self.active_positions
        if any(p["symbol"] == mid for p in active):
            return {"success": False, "reason": "Position already open for this asset"}

        order_type = normalize_order_type(signal.get("order_type"))
        last_px = ticker.get("last") or ticker.get("ask") or ticker.get("bid")
        if order_type in ("LIMIT", "STOP") and not should_fill_now(
            order_type, signal.get("direction"), last_px,
            limit_price=signal.get("limit_price"),
            stop_price=signal.get("stop_price"),
        ):
            pending = {
                "id": f"PND-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}",
                "mode": mode,
                "market_id": mid,
                "signal": signal,
                "risk": risk,
                "order_type": order_type,
                "direction": signal.get("direction"),
                "limit_price": signal.get("limit_price"),
                "stop_price": signal.get("stop_price"),
                "quantity": risk.get("quantity"),
                "status": "PENDING",
            }
            self.pending_orders.append(pending)
            return {"success": True, "pending": True, "reason": "QUEUED_PENDING_TRIGGER",
                    "order": serialize_pending(pending)}

        # 5. Fill on the real bid/ask
        fill_override = signal.get("_fill_price")
        if fill_override:
            entry_price = float(fill_override)
        else:
            entry_price = ticker.get('ask') if signal["direction"] == "BUY" else ticker.get('bid')
        if not entry_price:
            entry_price = ticker.get('last')
        if not entry_price:
            return {"success": False, "reason": "NO_PRICE_AVAILABLE"}

        # 6. Realistic slippage
        slippage = slippage_pct / 100.0
        if signal["direction"] == "BUY":
            entry_price *= (1 + slippage)
        else:
            entry_price *= (1 - slippage)

        position = {
            "id": f"SIM-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}",
            "mode": mode,
            "symbol": mid,
            "display_symbol": signal.get("display_symbol") or mid,
            "direction": signal["direction"],
            "entry_price": float(entry_price),
            "quantity": risk["quantity"],
            "sl": signal["sl"],
            "tp": signal["tp"],
            "leverage": risk["leverage"],
            "fees": risk["estimated_fees"] / 2,
            "open_time": datetime.now().isoformat(),
            "status": "OPEN",
            "pnl": -(risk["estimated_fees"] / 2),
            # v2.7 P0-6: Full trade accounting fields
            "initial_quantity": risk["quantity"],
            "remaining_quantity": risk["quantity"],
            "initial_risk_amount": abs(float(entry_price) - float(signal["sl"])) * risk["quantity"],
            "entry_fees": risk["estimated_fees"] / 2,
            "exit_fees": 0.0,
            "slippage_cost": 0.0,
            "funding_cost": 0.0,
            "partial_realized_pnl": 0.0,
            "gross_pnl": 0.0,
            "net_pnl": -(risk["estimated_fees"] / 2),
            "score_at_entry": signal.get("score", 0),
            "rank_at_entry": signal.get("rank", None),
            "opportunity_id": signal.get("opportunity_id", None),
            "metadata": {
                "atr": signal.get("atr", 0),
                "strategy": signal.get("strategy", "structure"),
                "score": signal.get("score", 0),
                "regime": signal.get("regime", "NORMAL"),
            }
        }

        self.db.save_trade(position)
        self.db.log_audit("INFO", "ORDER_OPEN",
                          f"Opened {mid} {signal['direction']} @ {position['entry_price']}",
                          {"position_id": position["id"], "mode": mode})
        return {"success": True, "position": position}

    def _close_position(self, pos: Dict[str, Any], reason: str, exit_price: float) -> None:
        """Common closing routine: recompute realized PnL, deduct all fees, persist, notify risk.
        
        v2.7 P0-6: Proper accounting for partial exits and all costs.
        - exit_fees calculated on remaining quantity
        - net_pnl = gross_pnl - all fees - slippage - funding
        - realized_r_multiple computed
        - register_closed_trade only with final net result
        """
        pos["status"] = "CLOSED"
        pos["exit_price"] = float(exit_price)
        pos["close_time"] = datetime.now().isoformat()
        
        # Compute exit fees on remaining quantity
        remaining_qty = pos.get("remaining_quantity", pos.get("quantity", 0))
        exit_notional = float(exit_price) * remaining_qty
        exit_fees = exit_notional * 0.001  # approximate 0.1% exit fee
        
        # Gross PnL on remaining quantity
        if pos["direction"] == "BUY":
            gross_pnl = (float(exit_price) - pos["entry_price"]) * remaining_qty
        else:
            gross_pnl = (pos["entry_price"] - float(exit_price)) * remaining_qty
        
        # Total costs
        entry_fees = float(pos.get("entry_fees", pos.get("fees", 0.0) or 0.0))
        slippage_cost = float(pos.get("slippage_cost", 0.0) or 0.0)
        funding_cost = float(pos.get("funding_cost", 0.0) or 0.0)
        partial_pnl = float(pos.get("partial_realized_pnl", 0.0) or 0.0)
        
        # Net PnL includes partial exits and all costs
        final_leg_pnl = gross_pnl - exit_fees
        total_net_pnl = partial_pnl + final_leg_pnl - entry_fees - slippage_cost - funding_cost
        
        # Update position fields
        pos["exit_fees"] = exit_fees
        pos["gross_pnl"] = gross_pnl + partial_pnl
        pos["net_pnl"] = total_net_pnl
        pos["pnl"] = total_net_pnl
        pos["remaining_quantity"] = 0
        
        # Compute realized R-multiple
        initial_risk = float(pos.get("initial_risk_amount", 0) or 0)
        if initial_risk > 0:
            pos["realized_r_multiple"] = round(total_net_pnl / initial_risk, 3)
        else:
            pos["realized_r_multiple"] = 0.0
        
        pos["metadata"] = {**(pos.get("metadata") or {}), "close_reason": reason}

        self.portfolio.update_balance(pos["mode"], total_net_pnl)
        # v2.7 P0-6: Only register with the final net result (not partial legs)
        self.risk.register_closed_trade(total_net_pnl)
        self.db.save_trade(pos)
        self.db.log_audit("INFO", "ORDER_CLOSE",
                          f"Closed {pos['symbol']}: {reason} (Net PnL {total_net_pnl:.2f}, R={pos['realized_r_multiple']})",
                          {"position_id": pos["id"], "reason": reason,
                           "net_pnl": total_net_pnl, "r_multiple": pos["realized_r_multiple"]})

    async def update_active_positions(self, mode: str, tickers: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Update unrealized P&L and check exits (SL/TP/trailing/partial TP)
        with real market prices.
        """
        closed_trades = []
        active = self.db.get_active_positions(mode)

        if not active:
            return closed_trades

        settings = self._load_settings()
        ts_active = settings.get("trailing_stop_active", "false").lower() == "true"
        ts_dist_atr = float(settings.get("trailing_stop_distance_atr", "1.5"))
        partial_tp_ratio = float(settings.get("partial_tp_ratio", "1.0"))
        try:
            max_duration_mins = int(float(settings.get("max_trade_duration_minutes", "0") or 0))
        except (TypeError, ValueError):
            max_duration_mins = 0

        for pos in active:
            ticker = tickers.get(pos["display_symbol"]) or tickers.get(pos["symbol"])
            if not ticker:
                continue

            current_exit_price = ticker.get('bid') if pos["direction"] == "BUY" else ticker.get('ask')
            if not current_exit_price:
                current_exit_price = ticker.get('last')
            if not current_exit_price:
                continue

            # Unrealized PnL
            if pos["direction"] == "BUY":
                pnl = (current_exit_price - pos["entry_price"]) * pos["quantity"]
            else:
                pnl = (pos["entry_price"] - current_exit_price) * pos["quantity"]
            pos["pnl"] = float(pnl)

            metadata = pos.get("metadata") or {}

            # 1. Market closed → force close (protection)
            if self.universe.get_market_status(pos["symbol"]) != "OPEN":
                self._close_position(pos, "MARKET_CLOSED_PROTECTION", current_exit_price)
                closed_trades.append(pos)
                continue

            # 1b. LOT P: time stop — a scalp that hasn't moved is a dead scalp.
            if max_duration_mins > 0:
                try:
                    open_dt = datetime.fromisoformat(str(pos.get("open_time") or ""))
                except (ValueError, TypeError):
                    open_dt = None
                if open_dt is not None:
                    elapsed_s = (datetime.now() - open_dt).total_seconds()
                    if elapsed_s > max_duration_mins * 60:
                        self._close_position(pos, "TIME_STOP_EXIT", current_exit_price)
                        closed_trades.append(pos)
                        continue

            # 2. Partial TP at 1:1 RR → lock 50%, move SL to break-even
            # v2.7 P0-6: Partial TP is NOT a full winning trade. Do NOT reset
            # the circuit breaker. The partial PnL is accumulated and only
            # registered with risk_engine when the position fully closes.
            risk_dist = abs(pos["entry_price"] - pos["sl"])
            if risk_dist > 0 and not metadata.get("partial_tp_hit"):
                hit_partial = (pos["direction"] == "BUY" and current_exit_price >= pos["entry_price"] + (risk_dist * partial_tp_ratio)) or \
                              (pos["direction"] == "SELL" and current_exit_price <= pos["entry_price"] - (risk_dist * partial_tp_ratio))
                if hit_partial:
                    close_qty = pos["quantity"] / 2
                    partial_pnl = (risk_dist * partial_tp_ratio) * close_qty
                    # Deduct proportional entry fees
                    entry_fee_portion = float(pos.get("entry_fees", 0) or 0) / 2
                    net_partial_pnl = partial_pnl - entry_fee_portion
                    self.portfolio.update_balance(mode, net_partial_pnl)
                    # v2.7 P0-6: Do NOT call risk.register_closed_trade here
                    # The partial exit is tracked but only registered at final close
                    pos["quantity"] -= close_qty
                    pos["remaining_quantity"] = pos["quantity"]
                    pos["partial_realized_pnl"] = float(pos.get("partial_realized_pnl", 0)) + net_partial_pnl
                    metadata["partial_tp_hit"] = True
                    metadata["partial_pnl"] = net_partial_pnl
                    pos["sl"] = pos["entry_price"]
                    metadata["break_even_active"] = True
                    self.db.log_audit("INFO", "PARTIAL_TP",
                                      f"Partial TP on {pos['symbol']} (+{net_partial_pnl:.2f}), SL moved to break-even.")

            # 3. Trailing stop
            if ts_active:
                atr = metadata.get("atr", risk_dist / 2) or (risk_dist / 2)
                if pos["direction"] == "BUY":
                    new_sl = current_exit_price - (atr * ts_dist_atr)
                    if new_sl > pos["sl"]:
                        pos["sl"] = float(new_sl)
                else:
                    new_sl = current_exit_price + (atr * ts_dist_atr)
                    if new_sl < pos["sl"] or pos["sl"] == 0:
                        pos["sl"] = float(new_sl)

            # 4. Final SL / TP check
            hit_sl = (pos["direction"] == "BUY" and current_exit_price <= pos["sl"]) or \
                     (pos["direction"] == "SELL" and current_exit_price >= pos["sl"])
            hit_tp = (pos["direction"] == "BUY" and current_exit_price >= pos["tp"]) or \
                     (pos["direction"] == "SELL" and current_exit_price <= pos["tp"])

            if hit_sl or hit_tp:
                self._close_position(pos, "SL_HIT" if hit_sl else "TP_HIT", current_exit_price)
                closed_trades.append(pos)
                if self.notifications:
                    asyncio.create_task(self.notifications.notify("ORDER_CLOSE", pos))
            else:
                pos["metadata"] = metadata
                self.db.save_trade(pos)

        return closed_trades

    async def process_pending_orders(self, mode: str, tickers: Dict[str, Any]) -> List[Dict[str, Any]]:
        filled = []
        still = []
        for pending in list(self.pending_orders):
            if pending.get("mode") != mode:
                still.append(pending)
                continue
            mid = pending.get("market_id")
            ticker = tickers.get(mid) or {}
            last = ticker.get("last") or ticker.get("ask") or ticker.get("bid")
            sig = dict(pending.get("signal") or {})
            if should_fill_now(pending.get("order_type"), pending.get("direction"), last,
                               limit_price=pending.get("limit_price"),
                               stop_price=pending.get("stop_price")):
                if pending.get("order_type") == "LIMIT" and pending.get("limit_price"):
                    sig["_fill_price"] = float(pending["limit_price"])
                res = await self.execute_order(mode, sig, pending.get("risk") or {}, ticker or {"last": last})
                if res.get("success") and not res.get("pending"):
                    filled.append(res)
                    continue
            still.append(pending)
        self.pending_orders = still
        return filled

    def close_position(self, mode: str, symbol: str, exit_price: float) -> Optional[Dict[str, Any]]:
        """v2.8: close ONE open position at market (user-initiated, DEMO).

        Matches on market_id/symbol or display symbol. Returns the closed
        position or None when no matching open position exists.
        """
        active = self.db.get_active_positions(mode)
        for pos in active:
            symbols = {pos.get("symbol"), pos.get("market_id"), pos.get("display_symbol")}
            if symbol in symbols:
                self._close_position(pos, "MANUAL_CLOSE", float(exit_price))
                return pos
        return None

    def clear_active_positions(self, mode: str) -> List[Dict[str, Any]]:
        """Emergency/exit-all: close every open position at last known price."""
        closed = []
        active = self.db.get_active_positions(mode)
        for pos in active:
            exit_price = pos.get("exit_price") or pos.get("entry_price")
            self._close_position(pos, "MANUAL_EXIT_ALL", exit_price)
            closed.append(pos)
        return closed
