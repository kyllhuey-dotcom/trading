from typing import Dict, Any, Optional, List
from datetime import datetime
import logging
import uuid
import time
import inspect

from .broker_adapters.ccxt_adapter import CCXTAdapter
from .broker_adapters.primexbt_adapter import PrimeXBTAdapter
from .market_universe import MarketUniverse

logger = logging.getLogger("BrokerConnector")


class BrokerConnector:
    """
    Universal broker & web3 wallet connector.
    - Real execution through CCXT adapters (orders + SL/TP protection)
    - REAL trades are persisted in the DB (positions are tracked & reconciled)
    - Emergency close across all connected brokers
    """

    def __init__(self, db_manager: Optional[Any] = None):
        self.db = db_manager
        self.universe = MarketUniverse()
        self.active_adapters: Dict[str, Any] = {}
        self.web3_wallets: Dict[str, Dict[str, str]] = {}
        self.emergency_stop_active = False
        self.notifier = None
        # v2.8: per-broker runtime snapshot cache (balance/latency/status)
        self._runtime_cache: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------ #
    # v2.8: runtime observability                                          #
    # ------------------------------------------------------------------ #
    async def runtime_snapshot(self, broker_id: str, ttl_s: float = 30.0) -> Dict[str, Any]:
        """Best-effort live snapshot for one broker, cached `ttl_s` seconds.

        Status ladder: CONNECTED / DEGRADED / ERROR / INACTIVE.
        Inactive or unknown brokers never trigger network calls.
        """
        import asyncio

        adapter = self.active_adapters.get(broker_id)
        if adapter is None:
            return {"runtime_status": "INACTIVE", "latency_ms": None, "balance_usdt": None,
                    "open_positions_count": 0, "last_sync": None, "sandbox": False}

        cached = self._runtime_cache.get(broker_id)
        now = time.time()
        if cached and now - cached.get("fetched_at", 0) < ttl_s:
            return cached["snapshot"]

        snapshot: Dict[str, Any] = {
            "runtime_status": "DEGRADED", "latency_ms": None, "balance_usdt": None,
            "open_positions_count": 0, "last_sync": None, "sandbox": False,
        }
        try:
            snapshot["sandbox"] = bool(getattr(adapter, "sandbox", False))
            start = time.monotonic()
            balance = await asyncio.wait_for(adapter.get_balance("USDT"), timeout=3.0)
            snapshot["latency_ms"] = int((time.monotonic() - start) * 1000)
            snapshot["balance_usdt"] = round(float(balance or 0.0), 2)
            try:
                positions = await asyncio.wait_for(adapter.get_positions(), timeout=3.0)
                snapshot["open_positions_count"] = len(positions or [])
            except Exception:
                snapshot["open_positions_count"] = 0
            snapshot["runtime_status"] = "CONNECTED"
            snapshot["last_sync"] = datetime.now().isoformat()
        except Exception as exc:
            snapshot["runtime_status"] = "ERROR"
            snapshot["error"] = str(exc)[:120]
        self._runtime_cache[broker_id] = {"fetched_at": now, "snapshot": snapshot}
        return snapshot

    def invalidate_runtime_cache(self, broker_id: Optional[str] = None) -> None:
        if broker_id is None:
            self._runtime_cache.clear()
        else:
            self._runtime_cache.pop(broker_id, None)

    def set_db_manager(self, db_manager: Any) -> None:
        self.db = db_manager

    # ------------------------------------------------------------------ #
    # Setup                                                               #
    # ------------------------------------------------------------------ #
    async def initialize_from_db(self, db_manager: Optional[Any] = None) -> None:
        """Load and connect all active brokers + web3 wallets from the DB."""
        if db_manager is not None:
            self.db = db_manager
        if self.db is None:
            logger.warning("BrokerConnector: no DB manager, skipping initialization")
            return
        for row in self.db.get_active_broker_configs():
            await self.add_broker(
                broker_id=row["broker_id"],
                exchange_id=row["exchange_id"],
                api_key=row["api_key"],
                api_secret=row["api_secret"],
                passphrase=row["api_passphrase"]
            )
        with self.db._get_connection() as conn:
            w_rows = conn.execute("SELECT * FROM web3_wallets WHERE is_active = 1").fetchall()
            for row in w_rows:
                self.web3_wallets[row["wallet_id"]] = {
                    "provider": row["provider"],
                    "address": row["address"],
                    "network": row["network"] or "mainnet",
                }

    async def add_broker(self, broker_id: str, exchange_id: str, api_key: str,
                         api_secret: str, passphrase: Optional[str] = None,
                         sandbox: Optional[bool] = None) -> bool:
        """Create the right adapter, connect it and keep it live."""
        if exchange_id.upper() == "PRIMEXBT":
            adapter = PrimeXBTAdapter(api_key, api_secret, passphrase)
        elif sandbox is None:
            # Legacy signature kept for fake adapters in the test suite.
            adapter = CCXTAdapter(exchange_id, api_key, api_secret, passphrase)
        else:
            adapter = CCXTAdapter(exchange_id, api_key, api_secret, passphrase,
                                  sandbox=sandbox)

        success = await adapter.connect()
        if success:
            previous = self.active_adapters.get(broker_id)
            self.active_adapters[broker_id] = adapter
            if previous is not None and previous is not adapter:
                try:
                    await previous.close()
                except Exception as exc:
                    logger.warning("Could not close replaced broker '%s': %s", broker_id, exc)
            logger.info("Broker '%s' (%s) connected", broker_id, exchange_id)
        else:
            await adapter.close()
        return success

    async def remove_broker(self, broker_id: str) -> bool:
        adapter = self.active_adapters.pop(broker_id, None)
        if adapter:
            await adapter.close()
            return True
        return False

    async def shutdown(self) -> None:
        for adapter in self.active_adapters.values():
            await adapter.close()
        self.active_adapters.clear()

    # ------------------------------------------------------------------ #
    # Balances                                                            #
    # ------------------------------------------------------------------ #
    async def get_all_balances(self) -> Dict[str, Any]:
        """Aggregate balances from brokers (USDT) and web3 wallets (native asset)."""
        results: Dict[str, Any] = {}
        for bid, adapter in self.active_adapters.items():
            try:
                usdt = await adapter.get_balance("USDT")
                results[bid] = {
                    "type": "BROKER",
                    "exchange": adapter.exchange_id,
                    "total_usdt": usdt,
                    "connected": True,
                }
            except Exception as e:
                results[bid] = {"type": "BROKER", "error": f"Connection failed: {e}", "total_usdt": 0.0}

        import httpx
        async with httpx.AsyncClient() as client:
            for wid, wdata in self.web3_wallets.items():
                try:
                    balance = 0.0
                    asset = "ETH"
                    if wdata["provider"] == "METAMASK":
                        res = await client.get(
                            f"https://api.blockcypher.com/v1/eth/main/addrs/{wdata['address']}/balance",
                            timeout=5.0)
                        if res.status_code == 200:
                            balance = res.json().get("balance", 0) / 10**18  # wei -> ETH
                    else:
                        asset = "UNKNOWN"
                    results[wid] = {
                        "type": "WEB3",
                        "provider": wdata["provider"],
                        "address": f"{wdata['address'][:6]}...{wdata['address'][-4:]}",
                        "asset": asset,
                        "balance": balance,
                        "connected": True,
                    }
                except Exception as e:
                    results[wid] = {"type": "WEB3", "provider": wdata.get("provider"),
                                    "error": f"Chain sync error: {e}", "balance": 0.0}
        return results

    # ------------------------------------------------------------------ #
    # Execution                                                           #
    # ------------------------------------------------------------------ #
    def _find_route(self, market_id: str) -> Optional[tuple]:
        """Find (broker_id, adapter, broker_symbol) for a market, first match wins."""
        info = self.universe.get_info(market_id)
        if not info:
            return None
        for bid, adapter in self.active_adapters.items():
            broker_symbol = info.get("broker_symbols", {}).get(adapter.exchange_id) \
                or info.get("providers", {}).get(adapter.exchange_id)
            if broker_symbol:
                return bid, adapter, broker_symbol
        return None

    async def execute(self, signal: Dict[str, Any], risk: Dict[str, Any]) -> Dict[str, Any]:
        """Route a REAL order to the right broker and persist the position."""
        if not self.active_adapters:
            return {"success": False, "reason": "NO_BROKER_CONNECTED"}

        market_id = signal.get("market_id")
        route = self._find_route(market_id) if market_id else None
        if not route:
            return {"success": False, "reason": f"UNSUPPORTED_SYMBOL: {market_id}"}

        bid, adapter, broker_symbol = route
        client_order_id = f"QTP-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
        order_kwargs = dict(symbol=broker_symbol, side=signal["direction"].lower(),
                            quantity=risk["quantity"], sl=signal.get("sl"), tp=signal.get("tp"))
        try:
            if "client_order_id" in inspect.signature(adapter.execute_order).parameters:
                order_kwargs["client_order_id"] = client_order_id
        except (TypeError, ValueError):
            pass
        res = await adapter.execute_order(**order_kwargs)

        # v3.1 P0-3: persist what actually happened on the broker —
        # the real filled quantity, average entry and fees.
        # - success            → OPEN position with honest fill data
        # - ...FAILED_NAKED    → position exists on the broker without SL/TP:
        #                        persist OPEN with sl_tp_failed=true so the
        #                        operator/reconciliation can manage it. The
        #                        result stays success=False.
        # - ...FAILED_FLATTENED→ position was closed immediately: nothing OPEN.
        naked = res.get("reason") == "SL_TP_ATTACH_FAILED_NAKED"
        if (res.get("success") or naked) and self.db is not None:
            quantity = float(res.get("filled") or risk["quantity"])
            entry_price = float(res.get("average") or signal.get("entry") or 0)
            fees = float(res.get("fees") or 0)
            metadata = {
                "strategy": signal.get("strategy", "structure"),
                "broker_id": bid,
                "broker_symbol": broker_symbol,
                "broker_order_id": res.get("broker_order_id"),
                "client_order_id": client_order_id,
                "requested_quantity": risk["quantity"],
                "tp_order_id": res.get("tp_order_id"),
                "sl_order_id": res.get("sl_order_id"),
                "sl_tp_warning": res.get("sl_tp_warning"),
            }
            if naked:
                metadata["sl_tp_failed"] = True
            position = {
                "id": f"REAL-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}",
                "mode": "REAL",
                "symbol": market_id,
                "display_symbol": signal.get("display_symbol") or market_id,
                "direction": signal["direction"],
                "entry_price": entry_price,
                "exit_price": None,
                "quantity": quantity,
                "sl": signal.get("sl"),
                "tp": signal.get("tp"),
                "leverage": risk["leverage"],
                "fees": fees,
                "pnl": 0.0,
                "open_time": datetime.now().isoformat(),
                "close_time": None,
                "status": "OPEN",
                "metadata": metadata,
            }
            self.db.save_trade(position)
            level = "CRITICAL" if naked else "INFO"
            self.db.log_audit(level, "REAL_ORDER_OPEN",
                              f"REAL {signal['direction']} {broker_symbol} qty={quantity} via {bid}"
                              + (" [NAKED: SL/TP attach failed]" if naked else ""),
                              {"position_id": position["id"], "broker_order_id": res.get("broker_order_id")})
            res["position"] = position
        elif res.get("reason") == "SL_TP_ATTACH_FAILED_FLATTENED" and self.db is not None:
            self.db.log_audit("WARNING", "REAL_ORDER_FLATTENED",
                              f"REAL {signal['direction']} {broker_symbol}: SL/TP attach failed, "
                              f"position flattened immediately — nothing persisted OPEN",
                              {"broker_order_id": res.get("broker_order_id")})
        if naked and self.notifier is not None:
            try:
                await self.notifier.notify("ERROR", {"message":
                    f"NAKED position {broker_symbol}: SL/TP attach failed — manual action required"})
            except Exception as exc:
                logger.warning("NAKED notification failed: %s", exc)
        return res

    # ------------------------------------------------------------------ #
    # Position management & emergency                                     #
    # ------------------------------------------------------------------ #
    async def close_all_positions(self) -> Dict[str, Any]:
        """Emergency stop: close every position on every connected broker."""
        results: Dict[str, Any] = {}
        for bid, adapter in self.active_adapters.items():
            try:
                results[bid] = await adapter.close_all_positions()
            except Exception as e:
                results[bid] = {"success": False, "error": str(e)}
        return results

    async def _cancel_protection_orders(self, adapter: Any, meta: Dict[str, Any],
                                        broker_symbol: str) -> List[str]:
        cancelled = []
        for order_id in (meta.get("tp_order_id"), meta.get("sl_order_id")):
            if not order_id:
                continue
            try:
                await adapter.cancel_order(order_id, broker_symbol)
                cancelled.append(order_id)
            except Exception as exc:
                logger.warning("Protection cancel failed (%s/%s): %s", broker_symbol, order_id, exc)
        return cancelled

    async def reconcile_positions(self) -> List[Dict[str, Any]]:
        """
        Synchronize DB 'REAL' OPEN trades with actual broker positions.
        Returns positions that were closed on the broker side.
        """
        if self.db is None:
            return []
        db_open = self.db.get_active_positions(mode="REAL")
        if not db_open:
            return []

        live_symbols: Dict[str, set] = {}  # broker_id -> {normalized broker symbols}
        for bid, adapter in self.active_adapters.items():
            # v3.1 P0-2: an empty position list is only a proof of closure
            # when the adapter can actually enumerate positions (derivatives
            # with fetchPositions). On spot-only setups get_positions() is
            # always [] — closing DB trades on that would be dishonest.
            if not bool(getattr(adapter, "positions_authoritative", False)):
                logger.debug("Reconciliation: broker '%s' is not authoritative "
                             "for positions — skipping close decisions", bid)
                continue
            try:
                positions = await adapter.get_positions()
                live_symbols[bid] = set()
                for p in positions or []:
                    sym = (p.get("symbol") or "").split(":")[0]
                    if sym:
                        live_symbols[bid].add(sym)
            except Exception as exc:
                # A broker/API outage is not evidence that every position was
                # closed. Omit this broker from reconciliation and retry later.
                logger.warning("Position reconciliation skipped for '%s': %s", bid, exc)
                live_symbols.pop(bid, None)

        closed = []
        for trade in db_open:
            meta = trade.get("metadata") or {}
            bid = meta.get("broker_id")
            broker_symbol = meta.get("broker_symbol")
            adapter = self.active_adapters.get(bid) if bid else None
            reason = None
            protection_kind = None
            exit_price = None
            if bid and bid in live_symbols and broker_symbol not in live_symbols[bid]:
                reason = "BROKER_RECONCILED_CLOSE"
            elif adapter is not None and not bool(getattr(adapter, "positions_authoritative", False)):
                fetch_status = getattr(adapter, "fetch_order_status", None)
                if callable(fetch_status):
                    for kind in ("tp", "sl"):
                        order_id = meta.get(f"{kind}_order_id")
                        if not order_id:
                            continue
                        try:
                            status = await fetch_status(order_id, broker_symbol)
                        except Exception as exc:
                            logger.warning("Protection status check failed (%s): %s", order_id, exc)
                            status = None
                        if status and str(status.get("status", "")).lower() in {"closed", "filled"}:
                            reason, protection_kind = "PROTECTION_FILLED", kind
                            exit_price = status.get("average")
                            break
            if reason:
                cancelled = await self._cancel_protection_orders(adapter, meta, broker_symbol)
                trade["status"] = "CLOSED"
                trade["close_time"] = datetime.now().isoformat()
                if exit_price is not None:
                    trade["exit_price"] = float(exit_price)
                    entry, qty = float(trade.get("entry_price") or 0), float(trade.get("quantity") or 0)
                    trade["pnl"] = ((float(exit_price) - entry) * qty
                                    if str(trade.get("direction")).upper() == "BUY"
                                    else (entry - float(exit_price)) * qty)
                trade["metadata"] = {**meta, "close_reason": reason,
                                     "cancelled_protection": cancelled}
                if protection_kind:
                    trade["metadata"]["filled_protection"] = protection_kind
                self.db.save_trade(trade)
                self.db.log_audit("WARNING", "REAL_RECONCILE",
                                  f"Position {trade['symbol']} closed by broker reconciliation")
                closed.append(trade)
        return closed

    async def close_position(self, market_id: str) -> Dict[str, Any]:
        """v3.1 P0-5: close ONE REAL open position through its broker.

        The DB is only marked CLOSED after the broker confirmed the reduce-only
        market order. An adapter failure leaves the DB untouched (still OPEN).
        """
        if self.db is None:
            return {"success": False, "reason": "NO_DB"}
        trade = next(
            (t for t in self.db.get_active_positions(mode="REAL")
             if market_id in (t.get("symbol"), t.get("display_symbol"))),
            None)
        if trade is None:
            return {"success": False, "reason": f"No open REAL position for {market_id}"}

        meta = trade.get("metadata") or {}
        bid = meta.get("broker_id")
        broker_symbol = meta.get("broker_symbol")
        adapter = self.active_adapters.get(bid) if bid else None
        if adapter is None or not broker_symbol:
            route = self._find_route(trade.get("symbol"))
            if not route:
                return {"success": False, "reason": "NO_BROKER_ROUTE"}
            bid, adapter, broker_symbol = route

        cancelled = await self._cancel_protection_orders(adapter, meta, broker_symbol)
        res = await adapter.close_position(
            broker_symbol, str(trade.get("direction", "BUY")).lower(),
            float(trade.get("quantity") or 0))
        if not res.get("success"):
            # Fail-honest: no broker confirmation → the DB stays OPEN.
            return {"success": False,
                    "reason": res.get("reason") or "BROKER_CLOSE_FAILED"}

        trade["status"] = "CLOSED"
        trade["close_time"] = datetime.now().isoformat()
        exit_price = float(res.get("average") or trade.get("exit_price") or trade.get("entry_price") or 0)
        trade["exit_price"] = exit_price
        entry, qty = float(trade.get("entry_price") or 0), float(trade.get("quantity") or 0)
        trade["pnl"] = ((exit_price - entry) * qty if str(trade.get("direction")).upper() == "BUY"
                        else (entry - exit_price) * qty)
        trade["fees"] = float(trade.get("fees") or 0) + float(res.get("fees") or 0)
        trade["metadata"] = {**meta, "close_reason": "MANUAL_CLOSE",
                             "close_order_id": res.get("broker_order_id"),
                             "cancelled_protection": cancelled}
        self.db.save_trade(trade)
        self.db.log_audit("INFO", "REAL_MANUAL_CLOSE",
                          f"REAL position {trade['symbol']} closed on broker '{bid}'",
                          {"position_id": trade.get("id"),
                           "close_order_id": res.get("broker_order_id")})
        return {"success": True, "position": trade,
                "exit_price": trade.get("exit_price"),
                "broker_order_id": res.get("broker_order_id")}

    def trigger_emergency_stop(self) -> bool:
        self.emergency_stop_active = True
        return True

    def reset_emergency_stop(self) -> bool:
        self.emergency_stop_active = False
        return True

    async def set_mode(self, mode: str) -> tuple:
        if mode == "REAL":
            if self.emergency_stop_active:
                return False, "Emergency Stop active."
            if not self.active_adapters:
                return False, "No active broker connected. Please add a broker in settings."
            return True, "LIVE MODE active."
        return True, "DEMO MODE active."

    def get_status(self) -> Dict[str, Any]:
        return {
            "connected_brokers": list(self.active_adapters.keys()),
            "broker_count": len(self.active_adapters),
            "emergency_stop": self.emergency_stop_active,
        }
