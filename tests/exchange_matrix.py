"""v3.3 — offline multi-exchange CONTRACT mocks (testnet matrix).

One base class simulates the order contract of an exchange (create order,
clientOrderId, fetch order, open/closed orders, trades, stops, reduceOnly,
cancel, full/partial fills, fees, timeout, rejected/canceled/expired,
price & quantity precision). Four subclasses document the specific stop
contract of Binance, Bybit, OKX and Gate — the same mapping as
``CCXTAdapter._stop_order_args``.

Every mock is offline: no network, deterministic.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional


class ExchangeContractMock:
    """Contract mock shared by all four exchanges (offline)."""

    exchange_id = "base"
    lot_size = 0.0001
    tick_size = 0.1
    min_notional = 10.0
    default_price = 100.0

    def __init__(self, fill_ratio: float = 1.0, fee_pct: float = 0.001,
                 has_fetch_positions: bool = True,
                 fail: Optional[set] = None,
                 timeout_after: Optional[float] = None):
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.open_orders: Dict[str, Dict[str, Any]] = {}
        self.closed_orders: Dict[str, Dict[str, Any]] = {}
        self.trades: List[Dict[str, Any]] = []
        self.client_order_ids: Dict[str, str] = {}
        self.create_calls: List[Dict[str, Any]] = []
        self.cancel_calls: List[str] = []
        self.fetch_order_calls: List[str] = []
        self.closed_flag = False
        self.sandbox = False
        self._seq = 0
        self.fail = set(fail or set())
        self.timeout_after = timeout_after
        self.fill_ratio = fill_ratio
        self.fee_pct = fee_pct
        self.has = {"fetchPositions": has_fetch_positions}
        self.markets: Dict[str, Any] = {}
        self.positions: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #
    async def load_markets(self):
        return self.markets

    async def close(self):
        self.closed_flag = True

    def set_sandbox_mode(self, enabled: bool):
        self.sandbox = bool(enabled)

    def _maybe_fail(self, what: str):
        if what in self.fail or "timeout" in self.fail:
            if "timeout" in self.fail:
                raise asyncio.TimeoutError(f"{self.exchange_id} timed out")
            raise RuntimeError(f"{self.exchange_id} {what} failed")
        if self.timeout_after is not None:
            raise asyncio.TimeoutError(f"{self.exchange_id} slow (mock)")

    def _next_id(self) -> str:
        self._seq += 1
        return f"{self.exchange_id.upper()}-{self._seq:04d}"

    # ------------------------------------------------------------------ #
    # Precision contract                                                   #
    # ------------------------------------------------------------------ #
    def check_precision(self, symbol: str, price: Optional[float], amount: float) -> None:
        """Exchange contract: amount must respect lot_size, price tick_size,
        and the notional must exceed min_notional. Violations raise (the
        exchange would reject the order)."""
        if amount is None or amount <= 0:
            raise ValueError(f"{self.exchange_id}: invalid amount {amount}")
        if self.lot_size and amount < self.lot_size:
            raise ValueError(f"{self.exchange_id}: amount {amount} below lot_size {self.lot_size}")
        steps = amount / self.lot_size
        if abs(steps - round(steps)) > 1e-6:
            raise ValueError(f"{self.exchange_id}: amount {amount} not a multiple of lot_size {self.lot_size}")
        if price is not None:
            if self.tick_size and price < self.tick_size:
                raise ValueError(f"{self.exchange_id}: price {price} below tick_size")
            ticks = price / self.tick_size
            if abs(ticks - round(ticks)) > 1e-6:
                raise ValueError(f"{self.exchange_id}: price {price} not on tick_size {self.tick_size}")
        notional = amount * (price if price else self.default_price)
        if notional < self.min_notional:
            raise ValueError(f"{self.exchange_id}: notional {notional} below min_notional {self.min_notional}")

    # ------------------------------------------------------------------ #
    # Stop contract per exchange                                           #
    # ------------------------------------------------------------------ #
    def check_stop_contract(self, order_type: str, params: Dict[str, Any]) -> None:
        """Assert the exchange-specific stop parameter contract."""
        if self.exchange_id == "binance":
            if order_type in ("STOP_MARKET", "TAKE_PROFIT_MARKET"):
                if "stopPrice" not in params:
                    raise ValueError("binance STOP_MARKET requires stopPrice")
                if order_type == "STOP_MARKET" and params.get("reduceOnly") is not True:
                    raise ValueError("binance stop must be reduceOnly")
        elif self.exchange_id == "bybit":
            if "triggerPrice" in params:
                if params.get("reduceOnly") is not True:
                    raise ValueError("bybit stop must be reduceOnly")
                if "triggerDirection" not in params:
                    raise ValueError("bybit stop requires triggerDirection")
        elif self.exchange_id == "okx":
            if "stopLossPrice" in params:
                if params.get("reduceOnly") is not True:
                    raise ValueError("okx stop must be reduceOnly")
        else:
            if order_type == "stop_loss":
                if "stopPrice" not in params or "triggerPrice" not in params:
                    raise ValueError(f"{self.exchange_id}: stop_loss requires stopPrice+triggerPrice")
                if params.get("reduceOnly") is not True:
                    raise ValueError(f"{self.exchange_id}: stop_loss must be reduceOnly")

    # ------------------------------------------------------------------ #
    # Order API                                                            #
    # ------------------------------------------------------------------ #
    async def create_order(self, symbol: str, order_type: str, side: str,
                           amount: float, price: Optional[float] = None,
                           params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self._maybe_fail("create")
        params = params or {}
        client_order_id = params.get("clientOrderId")
        if client_order_id:
            if client_order_id in self.client_order_ids:
                raise RuntimeError(f"duplicate clientOrderId {client_order_id}")
            self.client_order_ids[client_order_id] = self._next_id()
        self.check_precision(symbol, price, amount)
        self.check_stop_contract(order_type, params)
        oid = self.client_order_ids.get(client_order_id) or self._next_id()
        base_price = price if price is not None else self.default_price
        # Realistic contract: market orders fill immediately (by fill_ratio);
        # limit orders and stop orders REST on the book (fill 0, status open)
        # until triggered — the tests seed the triggered state when needed.
        rests = order_type in ("limit", "stop_loss", "STOP_MARKET",
                               "TAKE_PROFIT_MARKET") or price is not None
        filled = 0.0 if rests else float(amount) * self.fill_ratio
        average = base_price
        fee_cost = filled * average * self.fee_pct
        status = "closed" if filled >= float(amount) - 1e-12 else "open"
        order = {
            "id": oid,
            "clientOrderId": client_order_id,
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "amount": float(amount),
            "price": price,
            "filled": filled,
            "average": average,
            "status": status,
            "fee": {"cost": fee_cost, "currency": "USDT"},
            "timestamp": int(time.time() * 1000),
            "info": {"clientOrderId": client_order_id},
        }
        self.orders[oid] = order
        self.create_calls.append({
            "symbol": symbol, "type": order_type, "side": side,
            "amount": amount, "price": price, "params": dict(params),
            "clientOrderId": client_order_id,
        })
        if status == "closed":
            self.closed_orders[oid] = order
        else:
            self.open_orders[oid] = order
        if filled > 0:
            self.trades.append({
                "id": f"T-{oid}",
                "order": oid,
                "side": side,
                "amount": filled,
                "price": average,
                "fee": dict(order["fee"]),
                "timestamp": order["timestamp"],
                "info": {"clientOrderId": client_order_id},
            })
        return dict(order)

    async def fetch_order(self, order_id: str, symbol: Optional[str] = None) -> Dict[str, Any]:
        self._maybe_fail("fetch_order")
        self.fetch_order_calls.append(order_id)
        if order_id in self.orders:
            return dict(self.orders[order_id])
        # Many exchanges accept the clientOrderId in fetch_order.
        if order_id in self.client_order_ids:
            oid = self.client_order_ids[order_id]
            if oid in self.orders:
                return dict(self.orders[oid])
        raise RuntimeError(f"Order {order_id} not found")

    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        self._maybe_fail("open_orders")
        return [dict(o) for o in self.open_orders.values()]

    async def fetch_closed_orders(self, symbol: Optional[str] = None,
                                  limit: int = 50) -> List[Dict[str, Any]]:
        self._maybe_fail("closed_orders")
        return [dict(o) for o in self.closed_orders.values()][:limit]

    async def fetch_trades(self, symbol: Optional[str] = None,
                           limit: int = 50) -> List[Dict[str, Any]]:
        self._maybe_fail("trades")
        return list(self.trades)[-limit:]

    async def fetch_positions(self) -> List[Dict[str, Any]]:
        self._maybe_fail("positions")
        return list(self.positions)

    async def fetch_balance(self) -> Dict[str, Any]:
        self._maybe_fail("balance")
        return {"total": {"USDT": 10000.0}}

    async def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> Dict[str, Any]:
        self._maybe_fail("cancel")
        order = self.open_orders.pop(order_id, None)
        if order is None:
            if order_id in self.closed_orders:
                raise RuntimeError(f"Order {order_id} already closed — cannot cancel")
            raise RuntimeError(f"Order {order_id} not found — cannot cancel")
        order = dict(order)
        order["status"] = "canceled"
        order["info"]["cancelSource"] = "client"
        self.closed_orders[order_id] = order
        self.orders[order_id] = order
        self.cancel_calls.append(order_id)
        return order

    # ------------------------------------------------------------------ #
    # Test helpers                                                         #
    # ------------------------------------------------------------------ #
    def seed_order(self, oid: str, client_order_id: Optional[str] = None,
                   status: str = "open", filled: float = 0.0,
                   average: float = 100.0, amount: float = 1.0,
                   symbol: str = "BTC/USDT") -> Dict[str, Any]:
        """Insert an order as if the exchange had created it (for lookup tests)."""
        order = {
            "id": oid,
            "clientOrderId": client_order_id,
            "symbol": symbol,
            "side": "buy",
            "type": "market",
            "amount": amount,
            "price": None,
            "filled": filled,
            "average": average,
            "status": status,
            "fee": {"cost": filled * average * self.fee_pct, "currency": "USDT"},
            "timestamp": int(time.time() * 1000),
            "info": {"clientOrderId": client_order_id},
        }
        self.orders[oid] = order
        if client_order_id:
            self.client_order_ids[client_order_id] = oid
        if status == "closed":
            self.closed_orders[oid] = order
        else:
            self.open_orders[oid] = order
        if filled > 0:
            self.trades.append({
                "id": f"T-{oid}", "order": oid, "side": "buy",
                "amount": filled, "price": average,
                "fee": dict(order["fee"]), "timestamp": order["timestamp"],
                "info": {"clientOrderId": client_order_id},
            })
        return order


class BinanceMock(ExchangeContractMock):
    exchange_id = "binance"
    lot_size = 0.001
    tick_size = 0.01
    min_notional = 5.0


class BybitMock(ExchangeContractMock):
    exchange_id = "bybit"
    lot_size = 0.001
    tick_size = 0.01
    min_notional = 5.0


class OKXMock(ExchangeContractMock):
    exchange_id = "okx"
    lot_size = 0.001
    tick_size = 0.01
    min_notional = 5.0


class GateMock(ExchangeContractMock):
    exchange_id = "gate"
    lot_size = 0.0001
    tick_size = 0.1
    min_notional = 10.0


def make_mock(exchange_id: str, **kwargs) -> ExchangeContractMock:
    registry = {
        "binance": BinanceMock, "bybit": BybitMock,
        "okx": OKXMock, "gate": GateMock,
    }
    if exchange_id not in registry:
        raise KeyError(f"Unknown exchange in matrix: {exchange_id}")
    return registry[exchange_id](**kwargs)
