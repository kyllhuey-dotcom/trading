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
            tasks.append(self.providers[pid].health_check())
        
        results = await asyncio.gather(*tasks)
        
        report = []
        for i, res in enumerate(results):
            pid = provider_ids[i]
            # Standardizing report (Rule 39)
            report.append({
                "provider_id": pid,
                "asset_class": self._guess_class(pid),
                "status": res.get("status", "UNKNOWN"),
                "latency_ms": res.get("latency_ms", 0),
                "last_update": res.get("last_update", datetime.now().isoformat()),
                "error": res.get("message")
            })
        return report

    def _guess_class(self, pid: str) -> str:
        if "crypto" in pid or "gate" in pid or "binance" in pid:
            return "CRYPTO"
        if "forex" in pid:
            return "FOREX"
        if "indices" in pid:
            return "INDICES"
        if "commodities" in pid:
            return "COMMODITIES"
        return "MIXED"
