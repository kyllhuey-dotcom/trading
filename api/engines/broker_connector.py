from typing import Dict, Any, Optional
from .broker_adapters.ccxt_adapter import CCXTAdapter
from .broker_adapters.primexbt_adapter import PrimeXBTAdapter
from .market_universe import MarketUniverse

class BrokerConnector:
    """
    Broker Connection Orchestrator (Lot 8).
    Manages multiple broker adapters and handles the transition to REAL mode.
    """
    def __init__(self):
        self.universe = MarketUniverse()
        self.adapters = {
            "GATE": CCXTAdapter("gate"),
            "PRIMEXBT": PrimeXBTAdapter()
        }
        self.active_broker = "GATE"
        self.emergency_stop_active = False

    def trigger_emergency_stop(self):
        """Rule 32: Immediate halt and blocking of all order entries."""
        self.emergency_stop_active = True
        return True

    def reset_emergency_stop(self):
        """Rule 32: Requires explicit user action to reset safety protocols."""
        self.emergency_stop_active = False
        return True

    async def set_mode(self, mode: str):
        """Rule 28 & 43: Secure toggle between DEMO and REAL."""
        if mode == "REAL":
            if self.emergency_stop_active:
                return False, "Emergency Stop is active. Reset required."
            
            # Check broker connection and credentials before allowing REAL mode
            adapter = self.adapters.get(self.active_broker)
            if not adapter:
                return False, f"Broker {self.active_broker} not found."
            
            connected = await adapter.connect()
            if not connected:
                status = adapter.get_status()
                reason = status.get("api_status", "Connection failed")
                return False, f"Cannot enter LIVE: {self.active_broker} error ({reason})."
                
            return True, f"Entering LIVE MODE on {self.active_broker}. Real funds at risk."
        
        return True, "Mode DEMO Activated."

    async def connect(self, broker_id: str) -> bool:
        """Switch active broker and attempt connection."""
        if broker_id in self.adapters:
            self.active_broker = broker_id
            return await self.adapters[broker_id].connect()
        return False

    async def execute(self, signal: Dict[str, Any], risk: Dict[str, Any]):
        """
        Rule 14: Map internal ID to broker-specific symbol before execution.
        """
        adapter = self.adapters.get(self.active_broker)
        if not adapter:
            return {"success": False, "reason": "No active adapter"}
            
        market_id = signal.get("market_id")
        broker_symbol = self.universe.map_to_broker(market_id, self.active_broker.lower())
        
        if not broker_symbol:
            return {"success": False, "reason": f"UNSUPPORTED_BROKER_SYMBOL: {market_id} on {self.active_broker}"}
            
        return await adapter.execute_order(
            symbol=broker_symbol,
            side=signal["direction"].lower(),
            quantity=risk["quantity"],
            sl=signal["sl"],
            tp=signal["tp"]
        )

    def get_status(self) -> Dict[str, Any]:
        adapter = self.adapters.get(self.active_broker)
        status = adapter.get_status() if adapter else {"broker": "NONE", "connected": False}
        status["emergency_stop"] = self.emergency_stop_active
        status["active_broker"] = self.active_broker
        status["broker_name"] = self.active_broker
        return status
