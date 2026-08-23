from typing import Dict, Any, Optional, List
from datetime import datetime
import logging
import uuid
import time

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
                         api_secret: str, passphrase: Optional[str] = None) -> bool:
        """Create the right adapter, connect it and keep it live."""
        if exchange_id.upper() == "PRIMEXBT":
            adapter = PrimeXBTAdapter(api_key, api_secret, passphrase)
        else:
            adapter = CCXTAdapter(exchange_id, api_key, api_secret, passphrase)

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
        res = await adapter.execute_order(
            symbol=broker_symbol,
            side=signal["direction"].lower(),
            quantity=risk["quantity"],
            sl=signal.get("sl"),
            tp=signal.get("tp"),
        )

        if res.get("success") and self.db is not None:
            position = {
                "id": f"REAL-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}",
                "mode": "REAL",
                "symbol": market_id,
                "display_symbol": signal.get("display_symbol") or market_id,
                "direction": signal["direction"],
                "entry_price": float(res.get("average") or signal.get("entry") or 0),
                "exit_price": None,
                "quantity": risk["quantity"],
                "sl": signal.get("sl"),
                "tp": signal.get("tp"),
                "leverage": risk["leverage"],
                "fees": 0.0,
                "pnl": 0.0,
                "open_time": datetime.now().isoformat(),
                "close_time": None,
                "status": "OPEN",
                "metadata": {
                    "strategy": signal.get("strategy", "structure"),
                    "broker_id": bid,
                    "broker_symbol": broker_symbol,
                    "broker_order_id": res.get("broker_order_id"),
                    "tp_order_id": res.get("tp_order_id"),
                    "sl_order_id": res.get("sl_order_id"),
                    "sl_tp_warning": res.get("sl_tp_warning"),
                },
            }
            self.db.save_trade(position)
            self.db.log_audit("INFO", "REAL_ORDER_OPEN",
                              f"REAL {signal['direction']} {broker_symbol} qty={risk['quantity']} via {bid}",
                              {"position_id": position["id"], "broker_order_id": res.get("broker_order_id")})
            res["position"] = position
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

        closed = []
        for trade in db_open:
            meta = trade.get("metadata") or {}
            bid = meta.get("broker_id")
            broker_symbol = meta.get("broker_symbol")
            if bid and bid in live_symbols and broker_symbol not in live_symbols[bid]:
                trade["status"] = "CLOSED"
                trade["close_time"] = datetime.now().isoformat()
                trade["metadata"] = {**meta, "close_reason": "BROKER_RECONCILED_CLOSE"}
                self.db.save_trade(trade)
                self.db.log_audit("WARNING", "REAL_RECONCILE",
                                  f"Position {trade['symbol']} no longer on broker — closed in DB")
                closed.append(trade)
        return closed

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
