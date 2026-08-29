from .base_adapter import BrokerAdapter
from typing import Dict, Any, List, Optional
import ccxt.async_support as ccxt
import os
import logging
import math
from datetime import datetime

from ..exchange_constraints import parse_ccxt_market_constraints

logger = logging.getLogger("CCXTAdapter")


class CCXTAdapter(BrokerAdapter):
    """
    Real broker implementation via CCXT (Gate.io, Binance, Bybit, OKX, Kraken, ...).
    - Credentials come from the encrypted DB config (or env fallback).
    - Orders are REAL market orders with optional SL/TP protection orders.
    """

    def __init__(self, exchange_id: str = "gate",
                 api_key: Optional[str] = None,
                 api_secret: Optional[str] = None,
                 passphrase: Optional[str] = None,
                 sandbox: Optional[bool] = None):
        self.exchange_id = exchange_id.lower()
        self.api_key = api_key or os.getenv("BROKER_API_KEY")
        self.api_secret = api_secret or os.getenv("BROKER_API_SECRET")
        self.passphrase = passphrase or os.getenv("BROKER_API_PASSPHRASE")
        # v3.1 P0-4: explicit sandbox flag wins; env is only a legacy fallback.
        if sandbox is None:
            self.sandbox = os.getenv("BROKER_SANDBOX", "false").lower() == "true"
        else:
            self.sandbox = bool(sandbox)
        self.client: Optional[ccxt.Exchange] = None

    # ------------------------------------------------------------------ #
    # Lifecycle                                                           #
    # ------------------------------------------------------------------ #
    async def connect(self) -> bool:
        """Validate credentials and connectivity by loading markets."""
        if not self.api_key or not self.api_secret:
            logger.warning(f"CCXT {self.exchange_id}: missing credentials")
            return False
        try:
            exchange_class = getattr(ccxt, self.exchange_id)
            config = {
                'apiKey': self.api_key,
                'secret': self.api_secret,
                'enableRateLimit': True,
                'timeout': 15000,
            }
            if self.passphrase:
                config['password'] = self.passphrase
            self.client = exchange_class(config)
            # v3.1 P0-4: a REAL sandbox request must actually reach the
            # exchange testnet — silently trading live funds is fail-open.
            if self.sandbox:
                setter = getattr(self.client, "set_sandbox_mode", None)
                if not callable(setter):
                    logger.error("CCXT %s: sandbox requested but set_sandbox_mode "
                                 "is unavailable — refusing to connect", self.exchange_id)
                    try:
                        await self.client.close()
                    except Exception as close_exc:
                        logger.warning("CCXT %s cleanup failed: %s", self.exchange_id, close_exc)
                    self.client = None
                    return False
                try:
                    setter(True)
                except Exception as exc:
                    logger.error("CCXT %s: set_sandbox_mode(True) failed: %s",
                                 self.exchange_id, exc)
                    try:
                        await self.client.close()
                    except Exception as close_exc:
                        logger.warning("CCXT %s cleanup failed: %s", self.exchange_id, close_exc)
                    self.client = None
                    return False
            await self.client.load_markets()  # real connectivity + credential check
            logger.info(f"CCXT {self.exchange_id}: connected ({len(self.client.markets)} markets)")
            return True
        except Exception as exc:
            logger.error("CCXT %s connection error: %s", self.exchange_id, exc)
            if self.client:
                try:
                    await self.client.close()
                except Exception as close_exc:
                    logger.warning("CCXT %s cleanup failed: %s", self.exchange_id, close_exc)
            self.client = None
            return False

    async def close(self) -> None:
        if self.client:
            try:
                await self.client.close()
            finally:
                self.client = None

    # ------------------------------------------------------------------ #
    # Market data & account                                               #
    # ------------------------------------------------------------------ #
    def get_market_constraints(self, symbol: str) -> Dict[str, Any]:
        """
        LOT E: lot_size / tick_size / min_notional for one instrument,
        parsed from the CCXT markets loaded at connect() time (no network
        call — the markets table is already in memory).
        """
        if not self.client:
            return {"lot_size": None, "tick_size": None, "min_notional": None}
        market = self.client.markets.get(symbol) or {}
        return parse_ccxt_market_constraints(market)

    async def get_balance(self, asset: str = 'USDT') -> float:
        if not self.client:
            return 0.0
        try:
            balance = await self.client.fetch_balance()
            return float(balance.get('total', {}).get(asset, 0.0) or 0.0)
        except Exception as e:
            logger.warning(f"Fetch Balance Error ({self.exchange_id}): {e}")
            return 0.0

    async def get_positions(self) -> List[Dict[str, Any]]:
        """Fetch open positions (derivatives) — empty for spot-only setups."""
        if not self.client:
            return []
        try:
            if hasattr(self.client, 'fetch_positions'):
                return await self.client.fetch_positions() or []
            return []
        except Exception as e:
            logger.warning(f"Fetch Positions Error ({self.exchange_id}): {e}")
            return []

    # ------------------------------------------------------------------ #
    # Order execution                                                     #
    # ------------------------------------------------------------------ #
    async def execute_order(self, symbol: str, side: str, quantity: float,
                            sl: Optional[float] = None, tp: Optional[float] = None) -> Dict[str, Any]:
        if not self.client:
            return {"success": False, "reason": "BROKER_DISCONNECTED"}
        normalized_side = str(side).lower()
        try:
            quantity = float(quantity)
        except (TypeError, ValueError):
            return {"success": False, "reason": "INVALID_QUANTITY"}
        if normalized_side not in {"buy", "sell"}:
            return {"success": False, "reason": "INVALID_SIDE"}
        if not math.isfinite(quantity) or quantity <= 0:
            return {"success": False, "reason": "INVALID_QUANTITY"}
        try:
            order = await self.client.create_order(symbol, 'market', normalized_side, quantity)
        except Exception as e:
            logger.error(f"Broker execution error ({self.exchange_id}): {e}")
            return {"success": False, "reason": f"BROKER_EXECUTION_ERROR: {str(e)}"}

        # v3.1 P0-1: honest fill accounting — protection orders and DB
        # persistence must use the ACTUAL filled quantity, not the request.
        try:
            filled = float(order.get("filled") or quantity)
        except (TypeError, ValueError):
            filled = 0.0
        if not math.isfinite(filled) or filled <= 0:
            return {"success": False, "reason": "INVALID_FILL",
                    "broker_order_id": order.get("id")}
        average = order.get("average") or order.get("price")
        fee = order.get("fee") or {}
        try:
            fees = float(fee.get("cost") or 0) if isinstance(fee, dict) else 0.0
        except (TypeError, ValueError):
            fees = 0.0

        result = {
            "success": True,
            "broker_order_id": order.get("id"),
            "status": order.get("status", "FILLED"),
            "filled": filled,
            "average": average,
            "fees": fees,
            "side": normalized_side,
            "symbol": symbol,
            "timestamp": datetime.now().isoformat(),
        }

        # Attach SL/TP protection orders on the FILLED quantity.
        # `reduceOnly` is essential: without it, a protection order can open
        # a reverse position after the original position has already closed.
        # v3.1 P0-1 fail-close: if a protection order cannot be attached the
        # position is immediately flattened — never left naked and reported
        # as a success.
        hedge_side = 'sell' if normalized_side == 'buy' else 'buy'
        try:
            if tp is not None:
                tp_order = await self.client.create_order(
                    symbol, 'limit', hedge_side, filled, float(tp),
                    {'reduceOnly': True},
                )
                result["tp_order_id"] = tp_order.get("id")
            if sl is not None:
                sl_order = await self.client.create_order(
                    symbol, 'stop_loss', hedge_side, filled, None,
                    {'stopPrice': float(sl), 'triggerPrice': float(sl), 'reduceOnly': True},
                )
                result["sl_order_id"] = sl_order.get("id")
        except Exception as exc:
            logger.error("SL/TP attachment failed (%s): %s — flattening position",
                         self.exchange_id, exc)
            try:
                await self.client.create_order(
                    symbol, 'market', hedge_side, filled, None,
                    {'reduceOnly': True},
                )
            except Exception as flatten_exc:
                logger.critical(
                    "NAKED POSITION on %s %s: flatten failed after SL/TP error "
                    "(%s / %s) — manual intervention required",
                    self.exchange_id, symbol, exc, flatten_exc)
                return {**result, "success": False,
                        "reason": "SL_TP_ATTACH_FAILED_NAKED",
                        "flattened": False,
                        "sl_tp_warning": str(exc),
                        "flatten_error": str(flatten_exc)}
            return {**result, "success": False,
                    "reason": "SL_TP_ATTACH_FAILED_FLATTENED",
                    "flattened": True,
                    "sl_tp_warning": str(exc)}

        return result

    async def close_position(self, symbol: str, side: str, quantity: float) -> Dict[str, Any]:
        """Close ONE position with a market reduce-only hedge order.

        `side` is the ORIGINAL position side (buy/long or sell/short); the
        hedge order takes the opposite side. Same input guards as
        execute_order — never a fake success.
        """
        if not self.client:
            return {"success": False, "reason": "BROKER_DISCONNECTED"}
        normalized_side = str(side).lower()
        if normalized_side in {"long", "buy"}:
            normalized_side = "buy"
        elif normalized_side in {"short", "sell"}:
            normalized_side = "sell"
        else:
            return {"success": False, "reason": "INVALID_SIDE"}
        try:
            quantity = float(quantity)
        except (TypeError, ValueError):
            return {"success": False, "reason": "INVALID_QUANTITY"}
        if not math.isfinite(quantity) or quantity <= 0:
            return {"success": False, "reason": "INVALID_QUANTITY"}
        hedge_side = 'sell' if normalized_side == 'buy' else 'buy'
        try:
            order = await self.client.create_order(
                symbol, 'market', hedge_side, quantity, None,
                {'reduceOnly': True},
            )
            return {"success": True, "broker_order_id": order.get("id"),
                    "filled": order.get("filled"), "average": order.get("average"),
                    "symbol": symbol, "timestamp": datetime.now().isoformat()}
        except Exception as e:
            logger.error(f"Broker close error ({self.exchange_id}): {e}")
            return {"success": False, "reason": f"BROKER_CLOSE_ERROR: {str(e)}"}

    @property
    def positions_authoritative(self) -> bool:
        """True only when the exchange can actually enumerate positions.

        On spot-only setups fetch_positions is unsupported and get_positions()
        returns [] — that empty list is NOT evidence that positions were
        closed. Reconciliation must never close DB trades in that case.
        """
        if self.client is None:
            return False
        has = getattr(self.client, "has", None) or {}
        try:
            return bool(has.get("fetchPositions") or has.get("fetchPosition"))
        except AttributeError:
            return False

    async def close_all_positions(self) -> Dict[str, Any]:
        """Emergency exit: close all open positions + cancel open orders."""
        result: Dict[str, Any] = {"closed_positions": 0, "cancelled_orders": 0, "errors": []}
        if not self.client:
            return result
        try:
            positions = await self.get_positions()
        except Exception as exc:  # defensive: adapters normally return [] on failure
            positions = []
            result["errors"].append(f"fetch positions: {exc}")
        for position in positions:
            try:
                contracts = position.get("contracts")
                if contracts is None:
                    continue
                contracts = float(contracts)
                if contracts <= 0:
                    continue
                hedge = 'sell' if str(position.get("side", "")).lower() == 'long' else 'buy'
                await self.client.create_order(
                    position["symbol"], 'market', hedge, abs(contracts), None,
                    {'reduceOnly': True},
                )
                result["closed_positions"] += 1
            except Exception as exc:
                result["errors"].append(
                    f"close position {position.get('symbol', '?')}: {exc}"
                )
        try:
            open_orders = await self.client.fetch_open_orders()
        except Exception as exc:
            open_orders = []
            result["errors"].append(f"fetch open orders: {exc}")
        for order in open_orders:
            try:
                await self.client.cancel_order(order["id"], order.get("symbol"))
                result["cancelled_orders"] += 1
            except Exception as exc:
                result["errors"].append(f"cancel order {order.get('id', '?')}: {exc}")
        return result

    async def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> bool:
        if not self.client:
            return False
        try:
            await self.client.cancel_order(order_id, symbol)
            return True
        except Exception as e:
            logger.warning(f"Cancel order error ({self.exchange_id}): {e}")
            return False

    def get_status(self) -> Dict[str, Any]:
        return {
            "broker": self.exchange_id.upper(),
            "connected": self.client is not None,
            "sandbox": self.sandbox,
            "api_status": "READY" if self.client else "NO_CREDENTIALS",
        }
