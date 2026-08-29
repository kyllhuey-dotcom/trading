"""v3.3 — Universal broker connector with honest REAL execution.

- Real execution through CCXT adapters (orders + SL/TP protection)
- Protection state machine: an order ID alone never proves liveness
- Durable idempotence: order intentions persisted before every REAL order
- Partial fills: only the positive delta is accounted (idempotent)
- NAKED window: cancel ok + hedge failed → trade stays OPEN + NAKED
- Emergency stop: per-position unit closes, confirmed before DB close
"""
from typing import Dict, Any, Optional, List
from datetime import datetime
import logging
import uuid
import time
import inspect

from .broker_adapters.ccxt_adapter import CCXTAdapter
from .broker_adapters.primexbt_adapter import PrimeXBTAdapter
from .market_universe import MarketUniverse
from . import protection_state
from . import pnl_engine

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
        self.metrics = None
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
                        "provider": wdata.get("provider"),
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
        """Find (broker_id, adapter, broker_symbol) for a market, first match wins.

        Only explicit ``broker_symbols`` are used for execution: a data
        provider mapping is NOT a broker mapping (e.g. eur_usd has only a
        yahoo_forex provider — a REAL order with Gate must fail with
        UNSUPPORTED_SYMBOL).
        """
        info = self.universe.get_info(market_id)
        if not info:
            return None
        for bid, adapter in self.active_adapters.items():
            broker_symbol = info.get("broker_symbols", {}).get(adapter.exchange_id)
            if broker_symbol:
                return bid, adapter, broker_symbol
        return None

    def _intent_update(self, client_order_id: Optional[str], status: str,
                       broker_order_id: Optional[str] = None,
                       error: Optional[str] = None) -> None:
        if self.db is None or not client_order_id:
            return
        try:
            self.db.update_order_intent(client_order_id, status=status,
                                        broker_order_id=broker_order_id, error=error)
        except Exception as exc:
            logger.warning("Order intent update failed (%s): %s", client_order_id, exc)

    async def _notify(self, event: str, data: Dict[str, Any]) -> None:
        """Notify without ever blocking or breaking the main flow (v3.3).

        ``self.notifier`` stays optional (None by default); every call is
        wrapped so a dead notification channel cannot take down execution.
        """
        if self.notifier is None:
            return
        try:
            await self.notifier.notify(event, data)
        except Exception as exc:
            logger.warning("Notification %s failed: %s", event, exc)
            if self.metrics is not None:
                try:
                    self.metrics.record_notification_failure(event)
                except Exception:
                    pass

    async def _record_state_unknown(self, client_order_id: str, symbol: str,
                                    broker_id: str, error: str) -> None:
        """Audit + notify an ORDER_STATE_UNKNOWN (no automatic retry)."""
        if self.db is not None:
            try:
                self.db.log_audit(
                    "CRITICAL", "ORDER_STATE_UNKNOWN",
                    f"Order {client_order_id} ({symbol}) send outcome unknown — "
                    f"manual reconciliation required. No automatic retry.",
                    {"client_order_id": client_order_id, "broker_id": broker_id,
                     "error": str(error)[:300]})
            except Exception as exc:
                logger.warning("Audit log failed: %s", exc)
        if self.metrics is not None:
            try:
                self.metrics.record_order_state_unknown(symbol)
            except Exception:
                pass
        await self._notify("ORDER_STATE_UNKNOWN", {
            "message": f"Order {client_order_id} ({symbol}): state unknown — "
                       f"check the exchange manually. No automatic retry.",
            "symbol": symbol, "client_order_id": client_order_id})

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
        # v3.3 durable idempotence: persist the intention BEFORE sending.
        # A restart can then prove whether an order was already sent.
        if self.db is not None:
            try:
                self.db.save_order_intent({
                    "client_order_id": client_order_id,
                    "broker_id": bid,
                    "symbol": market_id,
                    "side": signal.get("direction"),
                    "quantity": risk["quantity"],
                    "created_at": datetime.now().isoformat(),
                    "status": "PENDING_SEND",
                })
            except Exception as exc:
                logger.warning("Could not persist order intent %s: %s",
                               client_order_id, exc)

        order_kwargs = dict(symbol=broker_symbol, side=signal["direction"].lower(),
                            quantity=risk["quantity"], sl=signal.get("sl"), tp=signal.get("tp"))
        try:
            if "client_order_id" in inspect.signature(adapter.execute_order).parameters:
                order_kwargs["client_order_id"] = client_order_id
        except (TypeError, ValueError):
            pass
        try:
            res = await adapter.execute_order(**order_kwargs)
        except Exception as exc:
            # The adapter raised instead of returning a result. Reconcile
            # before giving up — NEVER send a second order.
            found = None
            finder = getattr(adapter, "find_order_by_client_id", None)
            if callable(finder):
                try:
                    found = await finder(client_order_id, broker_symbol)
                except Exception:
                    found = None
            if found is not None and adapter is not None \
                    and hasattr(adapter, "_result_from_found_order"):
                try:
                    res = adapter._result_from_found_order(
                        found, signal["direction"].lower(), broker_symbol, client_order_id)
                except Exception:
                    res = None
            if res is None:
                self._intent_update(client_order_id, "ORDER_STATE_UNKNOWN", error=str(exc))
                await self._record_state_unknown(client_order_id, broker_symbol, bid, exc)
                return {"success": False, "reason": "ORDER_STATE_UNKNOWN",
                        "client_order_id": client_order_id}
            # fall through with the recovered order (no double send)
        else:
            if res.get("reason") == "ORDER_STATE_UNKNOWN":
                self._intent_update(client_order_id, "ORDER_STATE_UNKNOWN",
                                    error=res.get("error"))
                await self._record_state_unknown(client_order_id, broker_symbol, bid,
                                                 res.get("error") or "unknown")
                return res

        self._intent_update(client_order_id,
                            "CONFIRMED" if res.get("success") or
                            res.get("reason") == "SL_TP_ATTACH_FAILED_NAKED"
                            else "FAILED",
                            broker_order_id=res.get("broker_order_id"),
                            error=None if res.get("success") else res.get("reason"))

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
                "last_accounted_filled": 0.0,
            }
            if naked or not (res.get("tp_order_id") or res.get("sl_order_id")):
                # No exchange protection attached (or recovered after an
                # ambiguous error): the position is NAKED by construction.
                metadata["sl_tp_failed"] = True
                metadata["protection_status"] = protection_state.NAKED
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
            level = "CRITICAL" if metadata.get("sl_tp_failed") else "INFO"
            self.db.log_audit(level, "REAL_ORDER_OPEN",
                              f"REAL {signal['direction']} {broker_symbol} qty={quantity} via {bid}"
                              + (" [NAKED: SL/TP attach failed]" if metadata.get("sl_tp_failed") else ""),
                              {"position_id": position["id"], "broker_order_id": res.get("broker_order_id"),
                               "client_order_id": client_order_id})
            if metadata.get("sl_tp_failed") and self.metrics is not None:
                try:
                    self.metrics.record_naked(market_id)
                except Exception:
                    pass
            res["position"] = position
        elif res.get("reason") == "SL_TP_ATTACH_FAILED_FLATTENED" and self.db is not None:
            self.db.log_audit("WARNING", "REAL_ORDER_FLATTENED",
                              f"REAL {signal['direction']} {broker_symbol}: SL/TP attach failed, "
                              f"position flattened immediately — nothing persisted OPEN",
                              {"broker_order_id": res.get("broker_order_id")})
        if naked:
            await self._notify("SL_TP_ATTACH_FAILED_NAKED", {
                "message": f"NAKED position {broker_symbol}: SL/TP attach failed — "
                           f"manual action required",
                "symbol": market_id})
        return res

    # ------------------------------------------------------------------ #
    # Position management & emergency                                     #
    # ------------------------------------------------------------------ #
    async def close_all_positions(self) -> Dict[str, Any]:
        """Emergency stop: close every position on every connected broker.

        v3.3: per-position unit closes are preferred (see
        ``emergency_close_all``); this bulk path remains as a last-resort
        fallback for broker-side state not tracked in the DB.
        """
        results: Dict[str, Any] = {}
        for bid, adapter in self.active_adapters.items():
            try:
                results[bid] = await adapter.close_all_positions()
            except Exception as e:
                results[bid] = {"success": False, "error": str(e)}
        return results

    async def _cancel_protection_orders(self, adapter: Any, meta: Dict[str, Any],
                                        broker_symbol: str,
                                        exclude_order_id: Optional[str] = None) -> List[str]:
        """Cancel TP then SL. Returns the IDs ACTUALLY cancelled.

        - missing IDs are ignored;
        - ``exclude_order_id`` is never cancelled (e.g. a filled protection);
        - each cancel is individually try/except'ed with a WARNING on failure;
        - a failure NEVER blocks the close — the caller proceeds regardless.
        """
        cancelled: List[str] = []
        if adapter is None:
            return cancelled
        for order_id in (meta.get("tp_order_id"), meta.get("sl_order_id")):
            if not order_id or order_id == exclude_order_id:
                continue
            try:
                ok = await adapter.cancel_order(order_id, broker_symbol)
                if ok is False:
                    raise RuntimeError("cancel_order returned False")
                cancelled.append(order_id)
            except Exception as exc:
                logger.warning("Protection cancel failed (%s/%s): %s",
                               broker_symbol, order_id, exc)
        return cancelled

    async def _refresh_protection_state(self, trade: Dict[str, Any], meta: Dict[str, Any],
                                        adapter: Any, broker_symbol: Optional[str]) -> Dict[str, Any]:
        """Check the SL/TP orders on the exchange and update the trade metadata.

        Returns a decision dict for reconciliation:
          state: FILLED_FULL | FILLED_PARTIAL | NAKED | UNKNOWN | ALIVE | UNCHECKED
          exit_price / fees / filled / kind / filled_order_id
        """
        decision = {"state": "UNCHECKED", "checked": False, "exit_price": None,
                    "fees": 0.0, "filled": 0.0, "kind": None,
                    "filled_order_id": None}
        fetch_status = getattr(adapter, "fetch_order_status", None) if adapter else None
        tp_id, sl_id = meta.get("tp_order_id"), meta.get("sl_order_id")
        if not tp_id and not sl_id:
            # No protection orders were ever attached.
            decision["state"] = protection_state.NAKED if meta.get("sl_tp_failed") else "UNCHECKED"
            return decision
        if not callable(fetch_status) or not broker_symbol:
            return decision

        statuses: Dict[str, Optional[Dict[str, Any]]] = {}
        errors = 0
        for kind, order_id in (("tp", tp_id), ("sl", sl_id)):
            if not order_id:
                statuses[kind] = {"status": None, "filled": 0.0, "average": None, "fees": 0.0}
                continue
            try:
                status = await fetch_status(order_id, broker_symbol)
            except Exception as exc:
                logger.warning("Protection status check failed (%s/%s): %s",
                               broker_symbol, order_id, exc)
                status = None
            # Per the adapter contract, a None result IS an error.
            if status is None:
                errors += 1
            statuses[kind] = status

        now = time.time()
        previous_status = str(meta.get("protection_status") or "").upper()
        decision["checked"] = True
        if errors and all(v is None for v in statuses.values()):
            # Could not determine the state at all.
            meta["protection_error_count"] = int(meta.get("protection_error_count") or 0) + 1
            if meta["protection_error_count"] >= protection_state.MAX_CONSECUTIVE_ERRORS:
                if previous_status != protection_state.UNKNOWN:
                    meta["protection_status"] = protection_state.UNKNOWN
                    meta["protection_uncertain"] = True
                    if self.db is not None:
                        self.db.log_audit(
                            "CRITICAL", "PROTECTION_STATE_UNKNOWN",
                            f"Protection state of {trade.get('symbol')} unknown after "
                            f"{meta['protection_error_count']} consecutive failed checks",
                            {"position_id": trade.get("id")})
                    await self._notify("POSITION_UNKNOWN", {
                        "message": f"Position {trade.get('symbol')}: protection state "
                                   f"UNKNOWN after repeated failed checks — verify manually.",
                        "symbol": trade.get("symbol")})
            decision["state"] = "UNCHECKED"
            return decision

        # At least one check succeeded: reset the error counter, stamp freshness.
        meta["protection_error_count"] = 0
        meta["protection_checked_at"] = now
        meta.pop("protection_uncertain", None)

        # The protection was attached to the ORIGINAL quantity. After partial
        # accounting the trade's quantity shrank — comparing the fill against
        # the residual would falsely declare the position fully closed.
        base_qty = float(meta.get("requested_quantity") or 0) or \
            float(trade.get("quantity") or 0) + \
            float(meta.get("last_accounted_filled") or 0)
        tol = pnl_engine.lot_tolerance()
        for kind, order_id in (("tp", tp_id), ("sl", sl_id)):
            status = statuses.get(kind)
            if status is None:
                continue
            norm = protection_state.normalize_order_status(status.get("status"))
            meta[f"{kind}_order_status"] = norm
            try:
                filled = float(status.get("filled") or 0.0)
            except (TypeError, ValueError):
                filled = 0.0
            if norm == protection_state.FILLED:
                if filled >= base_qty - tol:
                    decision = {"state": "FILLED_FULL",
                                "exit_price": status.get("average"),
                                "fees": status.get("fees") or 0.0,
                                "filled": filled, "kind": kind,
                                "filled_order_id": order_id}
                else:
                    decision = {"state": "FILLED_PARTIAL",
                                "exit_price": status.get("average"),
                                "fees": status.get("fees") or 0.0,
                                "filled": filled, "kind": kind,
                                "filled_order_id": order_id}
                meta["filled_protection"] = kind
                meta["filled_protection_order_id"] = order_id
                break
        else:
            # Neither protection fully/partially filled.
            for kind in ("tp", "sl"):
                norm = meta.get(f"{kind}_order_status")
                if norm in (protection_state.CANCELED, protection_state.EXPIRED,
                            protection_state.REJECTED):
                    if previous_status != protection_state.NAKED:
                        meta["protection_status"] = protection_state.NAKED
                        if self.db is not None:
                            self.db.log_audit(
                                "CRITICAL", "PROTECTION_LOST",
                                f"Protection {kind} of {trade.get('symbol')} is {norm} — "
                                f"position has NO exchange protection",
                                {"position_id": trade.get("id")})
                        await self._notify("PROTECTION_LOST", {
                            "message": f"Position {trade.get('symbol')}: {kind.upper()} "
                                       f"protection is {norm} — NAKED, manual action required.",
                            "symbol": trade.get("symbol")})
                    if self.metrics is not None:
                        try:
                            self.metrics.record_naked(trade.get("symbol"))
                        except Exception:
                            pass
                    decision["state"] = protection_state.NAKED
                    break
            if decision["state"] != protection_state.NAKED:
                partial_kind = next(
                    (kind for kind in ("tp", "sl")
                     if meta.get(f"{kind}_order_status")
                     == protection_state.PARTIALLY_FILLED), None)
                if partial_kind:
                    # A PARTIALLY_FILLED protection order: the filled part is
                    # accounted by reconcile (positive delta only) and the
                    # residual stays protected (liveness ALIVE for the
                    # backstop) — the order still rests on the exchange.
                    partial_status = statuses.get(partial_kind) or {}
                    try:
                        partial_filled = float(partial_status.get("filled") or 0.0)
                    except (TypeError, ValueError):
                        partial_filled = 0.0
                    meta["protection_status"] = protection_state.PARTIALLY_FILLED
                    decision = {"state": "FILLED_PARTIAL",
                                "exit_price": partial_status.get("average"),
                                "fees": partial_status.get("fees") or 0.0,
                                "filled": partial_filled, "kind": partial_kind,
                                "filled_order_id": tp_id if partial_kind == "tp"
                                else sl_id}
                else:
                    meta["protection_status"] = protection_state.OPEN
                    decision["state"] = "ALIVE"
        return decision

    def _apply_partial_fill(self, trade: Dict[str, Any], meta: Dict[str, Any],
                            decision: Dict[str, Any]) -> None:
        """Account a PARTIAL protection fill: only the positive delta.

        - PnL realized on filled_delta = broker_filled - last_accounted_filled
        - the matching fee share is accumulated (no double counting)
        - the residual quantity is reduced; the position stays OPEN
        """
        direction = str(trade.get("direction") or "BUY").upper()
        entry = float(trade.get("entry_price") or 0)
        broker_filled = float(decision.get("filled") or 0.0)
        last = float(meta.get("last_accounted_filled") or 0.0)
        delta = pnl_engine.fill_delta(broker_filled, last)
        tol = pnl_engine.lot_tolerance()
        if delta > tol and decision.get("exit_price"):
            exit_px = float(decision["exit_price"])
            gross = pnl_engine.gross_pnl(direction, entry, exit_px, delta)
            fee_share = pnl_engine.fee_portion(decision.get("fees") or 0.0,
                                               delta, broker_filled)
            trade["pnl"] = float(trade.get("pnl") or 0.0) + pnl_engine.net_pnl(gross, fee_share)
            trade["fees"] = float(trade.get("fees") or 0.0) + fee_share
            trade["quantity"] = float(trade.get("quantity") or 0.0) - delta
            meta["last_accounted_filled"] = broker_filled
            meta["partial_realized"] = True
            if self.db is not None:
                self.db.log_audit(
                    "WARNING", "REAL_PARTIAL_PROTECTION_FILL",
                    f"{trade.get('symbol')}: {decision.get('kind').upper()} protection "
                    f"partially filled {delta} @ {exit_px} — residual "
                    f"{trade['quantity']} stays OPEN",
                    {"position_id": trade.get("id")})
        # A consumed protection (FILLED) leaves the residual NAKED; a
        # PARTIALLY_FILLED order still protects the residual (ALIVE above).
        if decision.get("kind") and str(meta.get(f"{decision.get('kind')}_order_status")) \
                == protection_state.FILLED:
            meta["protection_status"] = protection_state.NAKED

    async def reconcile_positions(self) -> List[Dict[str, Any]]:
        """
        Synchronize DB 'REAL' OPEN trades with actual broker positions.
        Returns positions that were closed on the broker side.

        v3.3: protection state machine — an ID alone never proves a close or
        a liveness; partial fills are accounted on the positive delta only;
        authoritative closes without a confirmed price use
        CLOSED_PRICE_PENDING (no price fabrication).
        """
        if self.db is None:
            return []
        t0 = time.time()
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

            decision = await self._refresh_protection_state(
                trade, meta, adapter, broker_symbol)

            # Persist the refreshed protection state (NAKED / UNKNOWN / OPEN /
            # PARTIALLY_FILLED / error counters) even when nothing closes —
            # the next tick and the software backstop must see it.
            if decision.get("checked"):
                trade["metadata"] = meta
                self.db.save_trade(trade)

            reason = None
            exit_price = None
            if decision["state"] == "FILLED_FULL":
                reason = "PROTECTION_FILLED"
                exit_price = decision.get("exit_price")
            elif bid and bid in live_symbols and broker_symbol not in live_symbols[bid]:
                reason = "BROKER_RECONCILED_CLOSE"
            elif decision["state"] == "FILLED_PARTIAL":
                # The protection partially filled: account the delta, reduce
                # the residual, keep the trade OPEN (never a full close).
                self._apply_partial_fill(trade, meta, decision)
                trade["metadata"] = meta
                self.db.save_trade(trade)
                continue

            if reason:
                filled_id = decision.get("filled_order_id")
                cancelled = await self._cancel_protection_orders(
                    adapter, meta, broker_symbol, exclude_order_id=filled_id)
                if filled_id:
                    # The protection that filled is excluded from cancellation;
                    # only the SIBLING (the other protection) is cancelled.
                    sibling_id = next(
                        (oid for oid in (meta.get("tp_order_id"), meta.get("sl_order_id"))
                         if oid and oid != filled_id), None)
                    if sibling_id:
                        meta["sibling_order_id"] = sibling_id
                        meta["sibling_cancel_status"] = \
                            "CANCELED" if sibling_id in cancelled else "FAILED"
                trade["status"] = "CLOSED"
                trade["close_time"] = datetime.now().isoformat()
                if exit_price is not None:
                    trade["exit_price"] = float(exit_price)
                    qty = float(trade.get("quantity") or 0)
                    entry = float(trade.get("entry_price") or 0)
                    gross = pnl_engine.gross_pnl(str(trade.get("direction") or "BUY"),
                                                 entry, float(exit_price), qty)
                    exit_fees = float(decision.get("fees") or 0.0)
                    # All fees (entry already accumulated + this fill's fees)
                    # are deducted exactly once: net = gross - fees.
                    total_fees = float(trade.get("fees") or 0.0) + exit_fees
                    trade["fees"] = total_fees
                    trade["pnl"] = pnl_engine.net_pnl(gross, total_fees)
                else:
                    # v3.3: authoritative absence WITHOUT a confirmed close
                    # price — never invent a price.
                    trade["exit_price"] = None
                    meta["close_state"] = pnl_engine.CLOSED_PRICE_PENDING
                trade["metadata"] = {**meta, "close_reason": reason,
                                     "cancelled_protection": cancelled}
                if decision.get("kind"):
                    trade["metadata"]["filled_protection"] = decision["kind"]
                self.db.save_trade(trade)
                self.db.log_audit("WARNING", "REAL_RECONCILE",
                                  f"Position {trade['symbol']} closed by broker reconciliation"
                                  + (f" ({pnl_engine.CLOSED_PRICE_PENDING})"
                                     if exit_price is None else ""))
                closed.append(trade)
        if self.metrics is not None:
            try:
                self.metrics.record_reconcile((time.time() - t0) * 1000.0,
                                              len(db_open), len(closed))
            except Exception:
                pass
        return closed

    async def close_position(self, market_id: str) -> Dict[str, Any]:
        """v3.1 P0-5 / v3.3: close ONE REAL open position through its broker.

        The DB is only marked CLOSED after the broker confirmed the reduce-only
        market order. An adapter failure leaves the DB untouched (still OPEN).

        v3.3 NAKED window: the protections are cancelled BEFORE the hedge. If
        at least one cancel succeeded and then the hedge failed, the trade
        stays OPEN with sl_tp_failed=True + protection_status=NAKED, a
        CRITICAL audit and a CRITICAL notification — the next tick treats the
        position as unprotected.
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
            if cancelled:
                # v3.3: protections are gone, the hedge failed — NAKED window.
                meta["sl_tp_failed"] = True
                meta["protection_cancelled_before_close"] = True
                meta["protection_status"] = protection_state.NAKED
                meta["cancelled_protection"] = cancelled
                meta["close_failure_error"] = res.get("reason") or "BROKER_CLOSE_FAILED"
                meta["close_failure_at"] = datetime.now().isoformat()
                trade["metadata"] = meta
                self.db.save_trade(trade)
                self.db.log_audit(
                    "CRITICAL", "REAL_CLOSE_NAKED",
                    f"Protections cancelled ({cancelled}) but the hedge FAILED for "
                    f"{trade.get('symbol')} — position NAKED, stays OPEN. "
                    f"Error: {meta['close_failure_error']}",
                    {"position_id": trade.get("id"), "error": meta["close_failure_error"]})
                if self.metrics is not None:
                    try:
                        self.metrics.record_naked(trade.get("symbol"))
                    except Exception:
                        pass
                await self._notify("HEDGE_FAILED_AFTER_CANCEL", {
                    "message": f"CRITICAL: position {trade.get('symbol')} lost its SL/TP "
                               f"(cancelled) and the close order FAILED — NAKED, "
                               f"manual action required.",
                    "symbol": trade.get("symbol")})
                return {"success": False, "reason": res.get("reason") or "BROKER_CLOSE_FAILED",
                        "naked": True, "position_id": trade.get("id")}
            # Fail-honest: no broker confirmation → the DB stays OPEN.
            return {"success": False,
                    "reason": res.get("reason") or "BROKER_CLOSE_FAILED"}

        # v3.3 partial fills: never close a full trade when filled < quantity.
        qty = float(trade.get("quantity") or 0)
        try:
            broker_filled = float(res.get("filled") or 0.0)
        except (TypeError, ValueError):
            broker_filled = 0.0
        if 0.0 < broker_filled < qty - pnl_engine.lot_tolerance():
            fill = pnl_engine.normalize_fill(res)
            delta = pnl_engine.fill_delta(broker_filled,
                                          float(meta.get("last_accounted_filled") or 0.0))
            if delta > pnl_engine.lot_tolerance() and fill.get("average"):
                direction = str(trade.get("direction") or "BUY").upper()
                entry = float(trade.get("entry_price") or 0)
                gross = pnl_engine.gross_pnl(direction, entry, float(fill["average"]), delta)
                trade["pnl"] = float(trade.get("pnl") or 0.0) + \
                    pnl_engine.net_pnl(gross, fill.get("fees") or 0.0)
                trade["fees"] = float(trade.get("fees") or 0.0) + float(fill.get("fees") or 0.0)
                trade["quantity"] = qty - delta
                meta["last_accounted_filled"] = broker_filled
                meta["close_state"] = "PARTIALLY_CLOSED"
                trade["metadata"] = meta
                self.db.save_trade(trade)
                return {"success": True, "partial": True, "position": trade,
                        "filled_delta": delta,
                        "exit_price": fill.get("average")}

        trade["status"] = "CLOSED"
        trade["close_time"] = datetime.now().isoformat()
        exit_price = float(res.get("average") or trade.get("exit_price") or trade.get("entry_price") or 0)
        trade["exit_price"] = exit_price
        entry, qty_final = float(trade.get("entry_price") or 0), float(trade.get("quantity") or 0)
        gross = pnl_engine.gross_pnl(str(trade.get("direction") or "BUY"),
                                     entry, exit_price, qty_final)
        trade["pnl"] = pnl_engine.net_pnl(gross, float(trade.get("fees") or 0))
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

    async def emergency_close_all(self) -> Dict[str, Any]:
        """v3.3 honest emergency stop.

        Walks EVERY REAL OPEN trade in the DB, sends a UNIT close through its
        broker, waits for the result, and closes the DB row ONLY after
        confirmation. Returns per-position verdicts:

            CLOSED_CONFIRMED / FAILED / ORDER_STATE_UNKNOWN / MANUAL_ACTION_REQUIRED

        Reconciliation must run AFTER this (a spot get_positions() == [] never
        proves a close).
        """
        per_position: List[Dict[str, Any]] = []
        if self.db is not None:
            for trade in self.db.get_active_positions("REAL"):
                symbol = trade.get("symbol")
                try:
                    res = await self.close_position(symbol)
                except Exception as exc:
                    res = {"success": False, "reason": f"EXCEPTION: {exc}"}
                if res.get("success"):
                    verdict = "CLOSED_CONFIRMED"
                elif res.get("reason") == "ORDER_STATE_UNKNOWN":
                    verdict = "ORDER_STATE_UNKNOWN"
                elif res.get("naked"):
                    verdict = "MANUAL_ACTION_REQUIRED"
                else:
                    verdict = "FAILED"
                per_position.append({
                    "position_id": trade.get("id"),
                    "symbol": symbol,
                    "status": verdict,
                    "detail": res.get("reason") or None,
                })
        return {
            "positions": per_position,
            "closed_confirmed": sum(1 for p in per_position
                                    if p["status"] == "CLOSED_CONFIRMED"),
            "total": len(per_position),
        }

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
