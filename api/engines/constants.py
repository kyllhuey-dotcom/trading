"""
Shared constants for v2.8 — the inviolable execution floor and health targets.

AUTO_EXECUTION_SCORE_FLOOR is the absolute minimum score for ANY automated
execution path. No setting, profile, tuning, regime, or API call may lower
the effective threshold below this value. Manual orders remain available
with explicit user confirmation.

v2.8: the floor is raised from 80 to 84 to improve selectivity, and the
default number of simultaneous executions per scan is raised from 1 to 3.
A higher score is a *selectivity* filter — never a win-rate probability.
"""

# The absolute minimum score for automatic execution. Inviolable.
AUTO_EXECUTION_SCORE_FLOOR: int = 84

# Realistic health targets (NOT a 99% win rate).
# These are the minimum criteria for a system to be considered healthy.
HEALTH_TARGETS = {
    "min_win_rate_pct": 45.0,
    "min_net_rr": 1.5,
    "min_expectancy": 0.0,          # must be strictly > 0
    "min_profit_factor": 1.3,
}

# Maximum number of new positions per scan cycle (default).
# v2.8: simultaneous executions are allowed — the top-N opportunities that
# pass every gate individually may all be executed in the same cycle.
DEFAULT_MAX_NEW_POSITIONS_PER_SCAN: int = 3

# Bounds for max_new_positions_per_scan setting.
MAX_NEW_POSITIONS_BOUNDS = (1, 3)

# Opportunity TTL (seconds) — how long an opportunity_id remains valid.
DEFAULT_OPPORTUNITY_TTL_S: float = 30.0

# Minimum trades before a statistical verdict (quarantine system).
MIN_TRADES_FOR_STRONG_VERDICT: int = 30

# Bayesian shrinkage prior for strategy/market reliability.
SHRINKAGE_PRIOR_WIN_RATE: float = 0.45
SHRINKAGE_PRIOR_TRADES: int = 10
