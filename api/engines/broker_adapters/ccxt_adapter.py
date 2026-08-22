from .base_adapter import BrokerAdapter
from typing import Dict, Any, List, Optional
import ccxt.async_support as ccxt
import os
import logging
from datetime import datetime

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
                 passphrase: Optional[str] = None):
        self.exchange_id = exchange_id.lower()
        self.api_key = api_key or os.getenv("BROKER_API_KEY")
        self.api_secret = api_secret or os.getenv("BROKER_API_SECRET")
        self.passphrase = passphrase or os.getenv("BROKER_API_PASSPHRASE")
        self.sandbox = os.getenv("BROKER_SANDBOX", "false").lower() == "true"
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
            await self.client.load_markets()  # real connectivity + credential check
            logger.info(f"CCXT {self.exchange_id}: connected ({len(self.client.markets)} markets)")
            return True
        except Exception as e:
            logger.error(f"CCXT {self.exchange_id} Connection Error: {e}")
            if self.client:
                await self.client.close()
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
        try:
            order = await self.client.create_order(symbol, 'market', side.lower(), quantity)
            result = {
                "success": True,
                "broker_order_id": order.get("id"),
                "status": order.get("status", "FILLED"),
                "filled": order.get("filled"),
                "average": order.get("average"),
                "side": side.lower(),
                "symbol": symbol,
                "timestamp": datetime.now().isoformat(),
            }

            # Attach SL/TP protection orders (best-effort, exchange dependent)
            hedge_side = 'sell' if side.lower() == 'buy' else 'buy'
            try:
                if tp:
                    tp_order = await self.client.create_order(symbol, 'limit', hedge_side, quantity, tp)
                    result["tp_order_id"] = tp_order.get("id")
                if sl:
                    sl_order = await self.client.create_order(symbol, 'stop_loss', hedge_side, quantity, None, sl)
                    result["sl_order_id"] = sl_order.get("id")
            except Exception as e:
                logger.warning(f"SL/TP attachment failed ({self.exchange_id}): {e}")
                result["sl_tp_warning"] = str(e)

            return result
        except Exception as e:
            logger.error(f"Broker execution error ({self.exchange_id}): {e}")
            return {"success": False, "reason": f"BROKER_EXECUTION_ERROR: {str(e)}"}

    async def close_all_positions(self) -> Dict[str, Any]:
        """Emergency exit: close all open positions + cancel open orders."""
        result: Dict[str, Any] = {"closed_positions": 0, "cancelled_orders": 0, "errors": []}
        if not self.client:
            return result
        try:
            for p in await self.get_positions():
                contracts = p.get("contracts")
                if contracts is None:
                    continue
                contracts = float(contracts)
                if contracts <= 0:
                    continue
                hedge = 'sell' if str(p.get("side", "")).lower() == 'long' else 'buy'
                await self.client.create_order(p["symbol"], 'market', hedge, abs(contracts),
                                               {'reduceOnly': True})
                result["closed_positions"] += 1
        except Exception as e:
            result["errors"].append(f"close positions: {e}")
        try:
            for o in await self.client.fetch_open_orders():
                await self.client.cancel_order(o["id"], o.get("symbol"))
                result["cancelled_orders"] += 1
        except Exception as e:
            result["errors"].append(f"cancel orders: {e}")
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
