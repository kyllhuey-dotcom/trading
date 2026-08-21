# Changelog - Quantum Trade Pro

## [1.4.0] - 2026-08-21
### Added
- **Advanced Position Management**: Dynamic Trailing Stop, Partial Take-Profit (50%), and automatic Break-even.
- **Correlation Filtering**: RiskEngine now blocks highly correlated trades to prevent over-exposure.
- **Notification System**: Real-time Telegram and Discord alerts for signals and trade events.
- **Performance Dashboard**: New `/api/performance` endpoint with detailed strategy metrics (Winrate, PF, Expectancy).
- **Backtesting Engine**: Historical validation framework for strategies.
- **Realistic Paper Trading**: Simulated latency, slippage, and order rejections in DEMO mode.
- **Graceful Shutdown**: Proper closure of all broker connections and data streams.

### Improved
- Structured logging with automatic file rotation.
- Enhanced API metrics and observability.

## [1.3.0] - 2026-08-21
### Added
- **Multi-Strategy Core**: Implemented a modular framework supporting "multi" mode.
- **Micro-Arbitrage Strategy**: High-confidence strategy exploiting price discrepancies between Gate/Bybit/Binance.
- **Tape Reading Strategy**: Institutional-grade order flow analysis (Imbalance + Trade Delta).
- **Liquidity Gap Strategy**: Predictive scalping on order book holes.
- **Diagnostic Upgrade**: The engine now details which strategy generated the signal and why.
- **WebSocket Heartbeat**: Regular PING/PONG style broadcast to monitor connection health.

### Improved
- **Execution Efficiency**: Scanner now returns full signal data, avoiding redundant calculations in the background loop.
- **Emergency Stop**: Now triggers global `close_all_positions` across all connected broker adapters.
- **Metrics**: Added strategy-specific signal tracking to `/api/metrics`.

### Fixed
- Missing `add_broker` method in `BrokerConnector`.
- Concurrency hardening for `bot_state` access in background loops.

## [1.2.0] - 2026-08-21
### Hardened
- **Signal Engine**: Prevented KeyError on incomplete OHLCV data and hardened ATR calculation.
- **Concurrency**: Implemented global `state_lock` to prevent race conditions on shared bot state and active positions.
- **Security**: Added at-rest encryption for broker API secrets using Fernet.
- **Safety**: Mandatory data age verification (CRYPTO < 5s, Other < 60s) before trading.
- **Resilience**: Emergency Stop now force-closes all local positions.
- **SQLite**: Enabled WAL mode and busy timeout for better concurrent performance.

### Added
- **Observability**: New `/api/metrics` endpoint and structured logging.
- **Rate Limiting**: Basic IP-based rate limiting for API endpoints.
- **CI/CD**: Enhanced `scripts/validate.sh` with coverage requirements and added `scripts/smoke_test.py`.
- **UI**: Added "BETA" warning for REAL mode.

### Fixed
- Fixed `UnboundLocalError` in `/api/status` logic.
- Reduced log noise by implementing a failure cache for delisted tickers.
- Synchronized multiple background loops through a unified async lock.

## [1.1.0] - 2026-08-21
### Added
- **Multi-source Redundancy**: Added Bybit as a backup crypto provider to Gate.io.
- **Automated Fallback**: DataLayer now automatically retries on backup providers if the primary source fails.
- **Institutional Healthcheck**: New `/api/health` endpoint providing detailed system and provider connectivity status.
- **Validation Pipeline**: Added `scripts/validate.sh` to ensure code quality and safety before deployment.
- **Railway Configuration**: Added `railway.json` with explicit healthcheck path and start commands.
- **Unit & Integration Tests**: Added test suite covering RiskEngine math and API stability.

### Fixed
- **CRITICAL**: Fixed `UnboundLocalError: risk_reason` in `/api/status` endpoint.
- **Security**: Verified and ensured all API secrets are read from environment variables.
- **Stability**: Refactored background loops to be test-aware, preventing hangs during CI.

### Security
- Audit performed: 0 hardcoded secrets found.
- All dependencies pinned in `requirements.txt`.
