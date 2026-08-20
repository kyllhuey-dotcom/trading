# API Contract - Quantum Trade Pro

All endpoints return JSON and require no authentication for public data. Private endpoints (orders, account) will use API Key headers in production.

## 1. System Endpoints

### GET `/api/status`
Returns the global bot state and analysis for a specific market.
- **Query Params**: `market_id` (default: `btc_usdt`)
- **Response**: `StatusResponse`

### POST `/api/start`
Starts the trading engine and auto-scanner.
- **Response**: `{ success: bool, status: str, message: str }`

### POST `/api/stop`
Stops the trading engine.
- **Response**: `{ success: bool, status: str, message: str }`

### POST `/api/emergency-stop`
Immediate halt of all systems.
- **Response**: `{ status: "STOPPED" }`

## 2. Market Data Endpoints

### GET `/api/markets/categories`
Returns the list of available asset classes.

### GET `/api/markets`
Returns a categorized list of all markets with latest quotes.

### GET `/api/scanner`
Returns the results of the latest global market scan.

### GET `/api/ohlcv/{market_id}`
Returns historical candles for an asset.

## 3. Account Endpoints

### GET `/api/demo/account`
Returns demo account balance and equity.

### POST `/api/demo/account`
Updates the initial demo balance.
- **Body**: `{ amount: float }`

### POST `/api/demo/reset`
Wipes history and resets demo account.
