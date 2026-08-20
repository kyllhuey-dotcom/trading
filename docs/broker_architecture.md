# Broker Architecture

This document describes the extensible broker adapter architecture (Rule 28, 29, 60).

## Abstraction Layer
The `BrokerAdapter` base class enforces a common interface for all execution targets.

## Execution Routing
The `ExecutionRouter` handles the logic of directing trade requests to either the:
1.  **DemoAdapter**: Real market data + Simulated execution.
2.  **LiveAdapters**: Real market data + Real broker execution.

## Adapters

### 1. CCXT Adapter
Uses the `ccxt` library to support standard crypto exchanges like Binance, Gate.io, Bybit.
- **Protocol**: REST + (Optional) WebSocket.
- **Status**: Operational for Demo/Real.

### 2. PrimeXBT Adapter
- **Protocol**: Display Only (Rule 29).
- **Official API**: Not available for retail bots in 2026.
- **Integration**: Designed as a placeholder for official direct execution when available. Current usage is for analysis and manual execution assistance.

### 3. Demo Adapter
- **Simulation**: Uses real-time Bid/Ask quotes for fills.
- **Fees**: Simulates slippage (0.01%) and broker commissions.
