from typing import Dict, Any, Optional
from .broker_adapters.ccxt_adapter import CCXTAdapter
from .broker_adapters.primexbt_adapter import PrimeXBTAdapter

class BrokerConnector:
    """
    Orchestrateur de connexions broker (Lot 8).
    """
    def __init__(self):
        self.adapters = {
            "BINANCE": CCXTAdapter("binance"),
            "PRIMEXBT": PrimeXBTAdapter()
        }
        self.active_broker = "BINANCE"
        self.emergency_stop_active = False

    def trigger_emergency_stop(self):
        """Rule 32: Stop all, block all."""
        self.emergency_stop_active = True
        return True

    def reset_emergency_stop(self):
        """Rule 32: Requiert une action explicite."""
        self.emergency_stop_active = False
        return True

    async def set_mode(self, mode: str):
        """Rule 28 & 43: Switch sécurisé."""
        if mode == "REAL":
            if self.emergency_stop_active:
                return False, "Emergency Stop is active. Reset required."
            
            # Check broker connection before allowing REAL mode
            adapter = self.adapters.get(self.active_broker)
            if not adapter or not await adapter.connect():
                return False, f"Cannot enter LIVE: Broker {self.active_broker} not connected."
                
            return True, "Entering LIVE MODE. Real funds at risk."
        
        return True, "Mode DEMO Activated."

    async def connect(self, broker_id: str) -> bool:
        if broker_id in self.adapters:
            self.active_broker = broker_id
            return await self.adapters[broker_id].connect()
        return False

    async def execute(self, signal: Dict[str, Any], risk: Dict[str, Any]):
        adapter = self.adapters.get(self.active_broker)
        if adapter:
            return await adapter.execute_order(
                symbol=signal["symbol"],
                side=signal["direction"],
                quantity=risk["quantity"],
                sl=signal["sl"],
                tp=signal["tp"]
            )
        return {"success": False, "reason": "No active adapter"}

    def get_status(self) -> Dict[str, Any]:
        adapter = self.adapters.get(self.active_broker)
        status = adapter.get_status() if adapter else {"broker": "NONE", "connected": False}
        status["emergency_stop"] = self.emergency_stop_active
        return status
