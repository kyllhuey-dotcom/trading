from typing import Dict, Any, Optional, List
from datetime import datetime
import logging

logger = logging.getLogger("ExecutionRouter")


class ExecutionRouter:
    """
    Execution orchestrator: routes orders to the DEMO simulator or the real broker.
    - Anti-duplication throttle (min 5s between orders)
    - Unique client order id per order (idempotence marker)
    - Full order history in memory for auditing
    """

    def __init__(self, demo_adapter: Any, broker_connector: Any):
        self.demo = demo_adapter
        self.broker = broker_connector
        self.last_order_timestamp: Optional[datetime] = None
        self.order_history: List[Dict[str, Any]] = []
        self.min_interval_seconds = 5.0

    async def execute(self, mode: str, signal: Dict[str, Any], risk: Dict[str, Any],
                      ticker: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.now()

        # Anti-duplication: never fire two orders in a row within 5s
        if self.last_order_timestamp and (now - self.last_order_timestamp).total_seconds() < self.min_interval_seconds:
            return {"success": False, "reason": "Execution throttled (anti-duplication)"}

        sym_id = str(signal.get('display_symbol') or signal.get('market_id') or 'UNK').replace('/', '')
        client_order_id = f"ORD-{int(now.timestamp() * 1000)}-{sym_id}"

        if mode == "DEMO":
            res = await self.demo.execute_order(mode, signal, risk, ticker)
        else:
            res = await self.broker.execute(signal, risk)

        if res.get("success"):
            self.last_order_timestamp = now
            pos = res.get("position") or {}
            pos["client_order_id"] = client_order_id
            res["client_order_id"] = client_order_id
            self.order_history.append({
                "client_order_id": client_order_id,
                "mode": mode,
                "symbol": signal.get("market_id"),
                "direction": signal.get("direction"),
                "time": now.isoformat(),
                "success": True,
            })
            # Keep the in-memory audit bounded
            if len(self.order_history) > 500:
                self.order_history = self.order_history[-500:]
        else:
            self.order_history.append({
                "client_order_id": client_order_id,
                "mode": mode,
                "symbol": signal.get("market_id"),
                "time": now.isoformat(),
                "success": False,
                "reason": res.get("reason"),
            })
        return res
