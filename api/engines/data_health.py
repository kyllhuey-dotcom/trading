from typing import Dict, Any, List, Optional
from .data_providers.base_provider import MarketDataProvider
import asyncio
from datetime import datetime

class DataHealthMonitor:
    """
    Rule 39, 45: Observability and Health Monitoring for Data Providers.
    """
    def __init__(self, providers: Dict[str, MarketDataProvider]):
        self.providers = providers

    async def get_health_report(self) -> List[Dict[str, Any]]:
        tasks = []
        provider_ids = list(self.providers.keys())
        for pid in provider_ids:
            # Wrap health check in a timeout to avoid blocking the whole report
            tasks.append(asyncio.wait_for(self.providers[pid].health_check(), timeout=5.0))
        
        # Use return_exceptions=True to avoid one crash breaking the whole report
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        report = []
        for i, res in enumerate(results):
            pid = provider_ids[i]
            
            if isinstance(res, Exception):
                status = "ERROR"
                message = str(res)
                latency = 0
            else:
                status = res.get("status", "UNKNOWN")
                message = res.get("message")
                latency = res.get("latency_ms", 0)

            # Standardizing report (Rule 39)
            report.append({
                "provider_id": pid,
                "asset_class": self._guess_class(pid),
                "status": status,
                "latency_ms": latency,
                "last_update": datetime.now().isoformat(),
                "error": message
            })
        return report

    def _guess_class(self, pid: str) -> str:
        pid_lower = pid.lower()
        if "crypto" in pid_lower or "gate" in pid_lower or "binance" in pid_lower or "bybit" in pid_lower:
            return "CRYPTO"
        if "forex" in pid_lower:
            return "FOREX"
        if "indices" in pid_lower:
            return "INDICES"
        if "commodities" in pid_lower:
            return "COMMODITIES"
        return "MIXED"
