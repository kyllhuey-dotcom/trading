# Quantum Trade Pro - Full Audit & Refactor Plan

## 1. Current State Assessment
The prototype is a functional but inconsistent trading application. It utilizes multiple providers (Gate.io, Yahoo Finance) but suffers from high coupling between modules and a lack of formal API contracts.

### Critical Deficiencies:
- **API Consistency**: Mixed naming conventions and non-standardized response shapes.
- **State Management**: Distributed logic for bot states leads to UI/Backend desync.
- **Market Data**: Dependency on `yfinance` creates latency issues and lacks true real-time capabilities for non-crypto assets.
- **Diagnostic Logic**: Present but not fully integrated into the primary decision loops.
- **Persistence**: Using JSON files for high-frequency trading data is a risk for data integrity.

## 2. Technical Debt & Risks
- **Concurrency**: `auto_scan_loop` can conflict with active trade polling.
- **Broker Interface**: PrimeXBT adapter is a placeholder, creating a false sense of "Live" readiness.
- **Data Freshness**: Current stale check is too generic across different asset classes.

## 3. Immediate Corrections (Lot 1)
- **Unified Models**: Implementation of `api/models.py` with strict Pydantic schemas.
- **API Contract**: Formalization of endpoints in `docs/api_contract.md`.
- **Orchestrator Refactor**: Grouping logic in `api/index.py` into distinct sequential check blocks.
- **State Machine**: Transitioning to a strict enum-based machine state.

## 4. Requirement Verification
- **Trading Days**: Tuesday, Wednesday, Thursday only.
- **Timezone**: Europe/Paris.
- **Source Integrity**: Removal of any simulated data from production paths.
