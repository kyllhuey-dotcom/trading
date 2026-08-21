# Changelog - Quantum Trade Pro

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
