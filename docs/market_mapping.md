# Market Mapping Strategy

This document describes the symbol mapping architecture used by Quantum Trade (Rule 14).

## Architecture
We use a three-tier mapping system to ensure independence between the UI, data providers, and execution brokers.

1.  **Internal ID**: A unique, snake_case identifier (e.g., `btc_usdt`, `eur_usd`, `gold`). This is used as the primary key throughout the backend and for active market selection.
2.  **Provider Symbol**: The symbol required by a specific data provider (e.g., `BTC/USDT` for Binance/CCXT, `EURUSD=X` for Yahoo Finance).
3.  **Broker Symbol**: The symbol required for order execution (e.g., `XAUUSD` for gold on PrimeXBT).

## Current Mappings

| Internal ID | Asset Class | Display | Provider (ID:Symbol) | Broker (ID:Symbol) |
| ----------- | ----------- | ------- | -------------------- | ------------------ |
| `btc_usdt`  | CRYPTO      | BTC/USDT| binance:BTC/USDT     | primexbt:BTC/USDT  |
| `eur_usd`   | FOREX       | EUR/USD | yahoo_forex:EURUSD=X | primexbt:EURUSD    |
| `gold`      | COMMODITIES | GOLD    | yahoo_commodities:GC=F| primexbt:XAUUSD   |
| `spx`       | INDICES     | S&P 500 | yahoo_indices:^GSPC  | primexbt:SPX       |

## Maintenance
New instruments must be added to `api/engines/market_catalog.py`. The `MarketCatalog` class provides utility methods to perform cross-tier mapping.
