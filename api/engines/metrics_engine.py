"""
MetricsEngine — advanced runtime observability (LOT A).

In-memory, lock-protected counters and rolling latency/data-age stats.
Pure additive module: nothing here changes the public API or the trading path,
it only *observes* it. Wired into api/index.py background loops.

Compatibility note: the legacy flat counters in `metrics_state` (index.py)
remain untouched and are still returned by GET /api/metrics.
"""
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional
import threading

# Rolling window sizes (bounded memory)
MAX_SAMPLES = 500


class MetricsEngine:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.start_time = datetime.now()

        # Counters
        self.total_scans = 0
        self.total_errors = 0
        self.signals_generated_by_strategy: Dict[str, int] = {}
        self.signals_blocked_by_strategy: Dict[str, int] = {}
        self.orders_by_mode: Dict[str, int] = {"DEMO": 0, "REAL": 0}

        # v3.3: REAL execution safety counters
        self.order_state_unknown = 0
        self.naked_positions = 0
        self.notification_failures = 0
        self.reconcile_runs = 0
        self.reconcile_closed = 0
        self.order_intents_total = 0

        # Rolling series (bounded deques)
        self._series: Dict[str, deque] = {
            "scan_ms": deque(maxlen=MAX_SAMPLES),
            "execution_ms": deque(maxlen=MAX_SAMPLES),
            "data_age_ms": deque(maxlen=MAX_SAMPLES),
            "reconcile_ms": deque(maxlen=MAX_SAMPLES),
            "broker_latency_ms": deque(maxlen=MAX_SAMPLES),
        }

        # Last values
        self.last_scan_duration_ms: Optional[float] = None
        self.last_execution_ms: Optional[float] = None
        self.last_data_age_ms: Optional[float] = None
        self.last_reconcile_ms: Optional[float] = None
        self.last_broker_latency_ms: Optional[float] = None
        self.last_scan_timestamp: Optional[str] = None
        self.trades_above_min_score = 0
        self.max_concurrent_seen = 0
        self.institutional_idle_ticks = 0
        self.institutional_exec_ticks = 0

    # ------------------------------------------------------------------ #
    # Recording                                                           #
    # ------------------------------------------------------------------ #
    def record_scan(self, duration_s: float, results: Optional[List[Dict[str, Any]]] = None) -> None:
        """Record one full universe scan: duration + aggregate data ages + signals generated."""
        with self._lock:
            self.total_scans += 1
            duration_ms = float(duration_s) * 1000.0
            self._series["scan_ms"].append(duration_ms)
            self.last_scan_duration_ms = duration_ms
            self.last_scan_timestamp = datetime.now().isoformat()

            if results:
                ages = [float(r["data_age_ms"]) for r in results
                        if isinstance(r.get("data_age_ms"), (int, float))]
                if ages:
                    self._series["data_age_ms"].extend(ages)
                    self.last_data_age_ms = max(ages)
                for r in results:
                    if not r.get("tradable"):
                        continue
                    sig = r.get("signal_data") or {}
                    strat = sig.get("strategy") or "structure"
                    self._incr(self.signals_generated_by_strategy, strat)

    def record_execution(self, strategy: str, mode: str, success: bool,
                         latency_ms: Optional[float] = None) -> None:
        """Record one order attempt routed to DEMO or REAL execution."""
        with self._lock:
            if latency_ms is not None:
                self._series["execution_ms"].append(float(latency_ms))
                self.last_execution_ms = float(latency_ms)
            mode_key = "REAL" if str(mode).upper() == "REAL" else "DEMO"
            self.orders_by_mode[mode_key] = self.orders_by_mode.get(mode_key, 0) + 1
            if success:
                self._incr(self.signals_generated_by_strategy, strategy or "structure")
            else:
                self._incr(self.signals_blocked_by_strategy, strategy or "structure")

    def record_signal_blocked(self, strategy: str) -> None:
        """Record a signal blocked at execution/risk stage (used by the scanner loop)."""
        with self._lock:
            self._incr(self.signals_blocked_by_strategy, strategy or "structure")

    def record_institutional(self, intent_code: str, n_active: int = 0, trades_above: int = 0) -> None:
        with self._lock:
            self.max_concurrent_seen = max(self.max_concurrent_seen, int(n_active or 0))
            if trades_above:
                self.trades_above_min_score += int(trades_above)
            code = str(intent_code or "").upper()
            if code == "IDLE":
                self.institutional_idle_ticks += 1
            elif code == "EXECUTING":
                self.institutional_exec_ticks += 1

    def record_error(self) -> None:
        with self._lock:
            self.total_errors += 1

    # ------------------------------------------------------------------ #
    # v3.3: REAL execution safety metrics                                  #
    # ------------------------------------------------------------------ #
    def record_order_state_unknown(self, symbol: Optional[str] = None) -> None:
        """An order send outcome could not be determined (no auto retry)."""
        with self._lock:
            self.order_state_unknown += 1

    def record_naked(self, symbol: Optional[str] = None) -> None:
        """A position lost (or never had) exchange SL/TP protection."""
        with self._lock:
            self.naked_positions += 1

    def record_notification_failure(self, event: Optional[str] = None) -> None:
        with self._lock:
            self.notification_failures += 1

    def record_reconcile(self, duration_ms: float, open_trades: int = 0,
                         closed: int = 0) -> None:
        """One reconciliation pass: lag (duration) + outcome counters."""
        with self._lock:
            self.reconcile_runs += 1
            self.reconcile_closed += int(closed or 0)
            self._series["reconcile_ms"].append(float(duration_ms))
            self.last_reconcile_ms = float(duration_ms)

    def record_broker_latency(self, broker_id: str, latency_ms: float) -> None:
        """Per-broker RPC latency (balance/positions/fetches)."""
        with self._lock:
            self._series["broker_latency_ms"].append(float(latency_ms))
            self.last_broker_latency_ms = float(latency_ms)

    def record_order_intent(self) -> None:
        with self._lock:
            self.order_intents_total += 1

    # ------------------------------------------------------------------ #
    # Snapshot                                                            #
    # ------------------------------------------------------------------ #
    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "total_scans": self.total_scans,
                "total_errors": self.total_errors,
                "signals_generated_by_strategy": dict(self.signals_generated_by_strategy),
                "signals_blocked_by_strategy": dict(self.signals_blocked_by_strategy),
                "orders_by_mode": dict(self.orders_by_mode),
                "latency": {
                    "scan_last_ms": self._round(self.last_scan_duration_ms),
                    "scan_avg_ms": self._round(self._avg("scan_ms")),
                    "execution_last_ms": self._round(self.last_execution_ms),
                    "execution_avg_ms": self._round(self._avg("execution_ms")),
                    "execution_max_ms": self._round(self._max("execution_ms")),
                },
                "data_age": {
                    "last_ms": self.last_data_age_ms,
                    "avg_ms": self._round(self._avg("data_age_ms")),
                    "max_ms": self._max("data_age_ms"),
                    "samples": len(self._series["data_age_ms"]),
                },
                # ---- v3.3: REAL execution safety ----
                "real_safety": {
                    "order_state_unknown": self.order_state_unknown,
                    "naked_positions": self.naked_positions,
                    "notification_failures": self.notification_failures,
                    "order_intents_total": self.order_intents_total,
                    "reconcile": {
                        "runs": self.reconcile_runs,
                        "closed": self.reconcile_closed,
                        "last_ms": self._round(self.last_reconcile_ms),
                        "avg_ms": self._round(self._avg("reconcile_ms")),
                        "max_ms": self._round(self._max("reconcile_ms")),
                    },
                    "broker_latency": {
                        "last_ms": self._round(self.last_broker_latency_ms),
                        "avg_ms": self._round(self._avg("broker_latency_ms")),
                        "max_ms": self._round(self._max("broker_latency_ms")),
                    },
                },
                "last_scan_timestamp": self.last_scan_timestamp,
                "institutional": {
                    "trades_above_min_score": self.trades_above_min_score,
                    "max_concurrent_seen": self.max_concurrent_seen,
                    "institutional_idle_ticks": self.institutional_idle_ticks,
                    "institutional_exec_ticks": self.institutional_exec_ticks,
                },
            }

    # ------------------------------------------------------------------ #
    # Internals                                                           #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _incr(counter: Dict[str, int], key: str) -> None:
        counter[key] = counter.get(key, 0) + 1

    def _avg(self, name: str) -> Optional[float]:
        series = self._series[name]
        if not series:
            return None
        return sum(series) / len(series)

    def _max(self, name: str) -> Optional[float]:
        series = self._series[name]
        return max(series) if series else None

    @staticmethod
    def _round(value: Optional[float]) -> Optional[float]:
        return round(value, 2) if value is not None else None
