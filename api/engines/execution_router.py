from typing import Dict, Any, Optional, List
from datetime import datetime

class ExecutionRouter:
    """
    Orchestrateur d'exécution (Rule 28).
    Route les ordres vers la simulation ou le broker réel.
    """
    def __init__(self, demo_adapter: Any, broker_connector: Any):
        self.demo = demo_adapter
        self.broker = broker_connector
        self.last_order_timestamp: Optional[datetime] = None
        self.order_history: List[Dict[str, Any]] = []

    async def execute(self, mode: str, signal: Dict[str, Any], risk: Dict[str, Any], ticker: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes order with protection against duplicates (Rule 31).
        Routes to simulation or real broker.
        """
        # Protection Rule 31: Idempotence (Min 5s between orders)
        now = datetime.now()
        if self.last_order_timestamp and (now - self.last_order_timestamp).total_seconds() < 5:
            return {"success": False, "reason": "Execution throttled (anti-duplication)"}

        # Unique Client Order ID
        # Use display_symbol or market_id for ID string
        sym_id = signal.get('display_symbol', signal.get('market_id', 'UNK')).replace('/','')
        client_order_id = f"ORD-{int(now.timestamp())}-{sym_id}"
        
        if mode == "DEMO":
            res = await self.demo.execute_order(mode, signal, risk, ticker)
            if res.get("success"):
                res["position"]["client_order_id"] = client_order_id
        else:
            # REAL MODE
            res = await self.broker.execute(signal, risk)
            if res.get("success"):
                res["client_order_id"] = client_order_id

        if res.get("success"):
            self.last_order_timestamp = now
            
        return res
