from typing import Dict, Any, Optional
from .broker_adapters.ccxt_adapter import CCXTAdapter
from .broker_adapters.primexbt_adapter import PrimeXBTAdapter
from .market_universe import MarketUniverse

class BrokerConnector:
    """
    Universal Broker Connector (Lot 12).
    Dynamically initializes any CCXT-supported exchange or legacy broker adapter.
    """
    def __init__(self):
        self.universe = MarketUniverse()
        self.active_adapters = {}
        self.emergency_stop_active = False

    async def initialize_from_db(self, db_manager: Any):
        """Load and connect all active brokers from the database."""
        with db_manager._get_connection() as conn:
            rows = conn.execute("SELECT * FROM broker_configs WHERE is_active = 1").fetchall()
            for row in rows:
                await self.add_broker(
                    broker_id=row["broker_id"],
                    exchange_id=row["exchange_id"],
                    api_key=row["api_key"],
                    api_secret=row["api_secret"],
                    passphrase=row["api_passphrase"]
                )

    async def add_broker(self, broker_id: str, exchange_id: str, api_key: str, api_secret: str, passphrase: str = None):
        """Initialize a new CCXT adapter dynamically."""
        from .broker_adapters.ccxt_adapter import CCXTAdapter
        
        adapter = CCXTAdapter(exchange_id=exchange_id)
        adapter.api_key = api_key
        adapter.api_secret = api_secret
        # Passphrase for exchanges like KuCoin/OKX
        if hasattr(adapter, 'passphrase'): adapter.passphrase = passphrase
        
        success = await adapter.connect()
        if success:
            self.active_adapters[broker_id] = adapter
        return success

    async def get_all_balances(self) -> Dict[str, float]:
        """Aggregate balances from all connected wallets (wallets/ballets)."""
        balances = {}
        for bid, adapter in self.active_adapters.items():
            balances[bid] = await adapter.get_balance()
        return balances

    def trigger_emergency_stop(self):
        self.emergency_stop_active = True
        return True

    def reset_emergency_stop(self):
        self.emergency_stop_active = False
        return True

    async def set_mode(self, mode: str):
        if mode == "REAL":
            if self.emergency_stop_active:
                return False, "Emergency Stop active."
            if not self.active_adapters:
                return False, "No active broker connected. Please add a broker in settings."
            return True, "LIVE MODE active."
        return True, "DEMO MODE active."

    async def execute(self, signal: Dict[str, Any], risk: Dict[str, Any]):
        if not self.active_adapters:
            return {"success": False, "reason": "NO_BROKER_CONNECTED"}
        
        # Strategy: Execute on the first available broker for now
        # Institutional improvement: Could select broker based on liquidity or specific asset mapping
        broker_id = list(self.active_adapters.keys())[0]
        adapter = self.active_adapters[broker_id]
        
        market_id = signal.get("market_id")
        broker_symbol = self.universe.map_to_broker(market_id, adapter.exchange_id)
        
        if not broker_symbol:
            return {"success": False, "reason": f"UNSUPPORTED_SYMBOL: {market_id}"}
            
        return await adapter.execute_order(
            symbol=broker_symbol,
            side=signal["direction"].lower(),
            quantity=risk["quantity"],
            sl=signal["sl"],
            tp=signal["tp"]
        )

    def get_status(self) -> Dict[str, Any]:
        return {
            "connected_brokers": list(self.active_adapters.keys()),
            "broker_count": len(self.active_adapters),
            "emergency_stop": self.emergency_stop_active
        }
