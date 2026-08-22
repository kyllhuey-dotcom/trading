"""
Basic sliding-window rate limiting (LOT H).

Per-client (IP) sliding window over 60 seconds:
- one budget for read endpoints (GET/HEAD);
- a stricter budget for mutations (POST/PUT/PATCH/DELETE);
- overflow answers HTTP 429 with a JSON body (never a bare reject);
- bounded memory: expired windows are pruned and the client table is capped.

Configurable via env:
- RATE_LIMIT_PER_MINUTE          (default 1200)
- RATE_LIMIT_MUTATIONS_PER_MINUTE(default 300)
- RATE_LIMIT_WINDOW_S            (default 60)
"""
import threading
import time
from collections import deque
from typing import Any, Deque, Dict

_MAX_TRACKED_CLIENTS = 5000


class SlidingWindowRateLimiter:
    def __init__(self, requests_per_minute: int = 1200,
                 mutations_per_minute: int = 300,
                 window_s: float = 60.0,
                 clock: Any = time.monotonic):
        self.requests_per_minute = max(1, int(requests_per_minute))
        self.mutations_per_minute = max(1, int(mutations_per_minute))
        self.window_s = max(1.0, float(window_s))
        self.clock = clock
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, client_id: str, is_mutation: bool = False) -> bool:
        limit = self.mutations_per_minute if is_mutation else self.requests_per_minute
        now = self.clock()
        with self._lock:
            window = self._hits.setdefault(client_id, deque())
            # Drop timestamps outside the window
            while window and now - window[0] >= self.window_s:
                window.popleft()
            if len(window) >= limit:
                return False
            window.append(now)
            # Bound the client table
            if len(self._hits) > _MAX_TRACKED_CLIENTS:
                self._hits = {k: v for k, v in self._hits.items() if v}
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def tracked_clients(self) -> int:
        with self._lock:
            return len([k for k, v in self._hits.items() if v])
