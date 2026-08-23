from typing import Dict, Any, List, Optional
from .data_providers.base_provider import MarketDataProvider
import asyncio
from datetime import datetime

# Latency thresholds for the precision status ladder (LOT F)
DEGRADED_LATENCY_MS = 1000.0   # answers, but slow
SLOW_LATENCY_MS = 3000.0       # too slow for ultra-scalping


class DataHealthMonitor:
    """
    Rule 39, 45: Observability and Health Monitoring for Data Providers.

    LOT F precision:
    - per-provider latency normalization (ONLINE → DEGRADED → SLOW → ERROR);
    - per-provider history: total checks, consecutive failures, last OK;
    - one slow provider can never block the whole report (timeouts + gather).
    """
    def __init__(self, providers: Dict[str, MarketDataProvider]):
        self.providers = providers
        self.provider_state: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def normalize_status(raw_status: Optional[str], latency_ms: float) -> str:
        raw = str(raw_status or "UNKNOWN").upper()
        if raw in ("ONLINE", "OK"):
            if latency_ms > SLOW_LATENCY_MS:
                return "SLOW"
            if latency_ms > DEGRADED_LATENCY_MS:
                return "DEGRADED"
            return "ONLINE"
        if raw in ("ERROR", "OFFLINE"):
            return "ERROR"
        return raw

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
            state = self.provider_state.setdefault(
                pid, {"consecutive_failures": 0, "checks": 0, "last_ok": None})
            state["checks"] += 1
            
            if isinstance(res, Exception):
                status = "ERROR"
                message = str(res)
                latency = 0.0
            else:
                message = res.get("message")
                latency = float(res.get("latency_ms", 0) or 0)
                status = self.normalize_status(res.get("status"), latency)

            if status in ("ONLINE", "DEGRADED", "SLOW"):
                state["consecutive_failures"] = 0
                state["last_ok"] = datetime.now().isoformat()
            else:
                state["consecutive_failures"] += 1

            # Standardizing report (Rule 39) + LOT F precision fields
            report.append({
                "provider_id": pid,
                "asset_class": self._guess_class(pid),
                "status": status,
                "latency_ms": latency,
                "last_update": datetime.now().isoformat(),
                "error": message,
                "checks": state["checks"],
                "consecutive_failures": state["consecutive_failures"],
                "last_ok": state["last_ok"],
            })
        return report

    def _guess_class(self, pid: str) -> str:
        pid_lower = pid.lower()
        if ("crypto" in pid_lower or "gate" in pid_lower or "binance" in pid_lower
                or "bybit" in pid_lower or "okx" in pid_lower or "kraken" in pid_lower
                or "coinbase" in pid_lower):
            return "CRYPTO"
        if "twelvedata" in pid_lower or "finnhub" in pid_lower:
            return "TRADFI"
        if "forex" in pid_lower:
            return "FOREX"
        if "indices" in pid_lower:
            return "INDICES"
        if "commodities" in pid_lower:
            return "COMMODITIES"
        return "MIXED"
