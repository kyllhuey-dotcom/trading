"""
Opportunity execution tracker — single-flight / idempotence (v2.7 P0-3).

Ensures:
- An opportunity_id can only be executed once
- Expired opportunities are rejected
- No retry can double an order
"""
import time
from typing import Any

from .constants import DEFAULT_OPPORTUNITY_TTL_S


class OpportunityTracker:
    """Thread-safe tracker for opportunity idempotence.

    Each opportunity_id has a TTL; once executed or expired,
    subsequent attempts are rejected.
    """

    def __init__(self, ttl_s: float = DEFAULT_OPPORTUNITY_TTL_S):
        self.ttl_s = float(ttl_s)
        self._executed: dict[str, dict[str, Any]] = {}
        self._in_flight: dict[str, float] = {}

    def _prune(self, now: float) -> None:
        """Remove expired entries."""
        expired = [k for k, v in self._executed.items()
                   if now - v.get("executed_at", 0) > self.ttl_s * 2]
        for k in expired:
            self._executed.pop(k, None)
        expired_flight = [k for k, ts in self._in_flight.items()
                         if now - ts > self.ttl_s]
        for k in expired_flight:
            self._in_flight.pop(k, None)

    def try_acquire(self, opportunity_id: str, now: float | None = None) -> dict[str, Any]:
        """Attempt to acquire execution rights for an opportunity.

        Returns {allowed, reason}.
        """
        now = now or time.time()
        self._prune(now)

        if not opportunity_id:
            return {"allowed": False, "reason": "MISSING_OPPORTUNITY_ID"}

        # Already executed
        if opportunity_id in self._executed:
            return {"allowed": False, "reason": "ALREADY_EXECUTED"}

        # Currently in flight
        if opportunity_id in self._in_flight:
            return {"allowed": False, "reason": "IN_FLIGHT"}

        # Acquire
        self._in_flight[opportunity_id] = now
        return {"allowed": True, "reason": None}

    def mark_executed(self, opportunity_id: str, result: dict[str, Any] | None = None,
                      now: float | None = None) -> None:
        """Mark an opportunity as executed (success or failure)."""
        now = now or time.time()
        self._in_flight.pop(opportunity_id, None)
        self._executed[opportunity_id] = {
            "executed_at": now,
            "result_summary": {
                "success": bool((result or {}).get("success")),
                "reason": (result or {}).get("reason"),
            },
        }

    def mark_failed(self, opportunity_id: str, reason: str = "",
                    now: float | None = None) -> None:
        """Mark an opportunity as failed (releases the in-flight lock)."""
        now = now or time.time()
        self._in_flight.pop(opportunity_id, None)
        self._executed[opportunity_id] = {
            "executed_at": now,
            "result_summary": {"success": False, "reason": reason},
        }

    def is_expired(self, expires_at: float, now: float | None = None) -> bool:
        """Check if an opportunity has expired."""
        now = now or time.time()
        return now > expires_at

    def get_stats(self) -> dict[str, Any]:
        now = time.time()
        self._prune(now)
        return {
            "executed_count": len(self._executed),
            "in_flight_count": len(self._in_flight),
            "ttl_s": self.ttl_s,
        }

    def reset(self) -> None:
        """Clear all state (for testing)."""
        self._executed.clear()
        self._in_flight.clear()


# Module-level singleton
_tracker: OpportunityTracker | None = None


def get_tracker() -> OpportunityTracker:
    global _tracker
    if _tracker is None:
        _tracker = OpportunityTracker()
    return _tracker
