# AUDIT V3.3 — REAL execution hardening, idempotence, exploitability

Date: 2026-08-29 — Version: **3.3.0**

> **REAL is experimental. A successful testnet campaign is required before any
> real ARM. No profitability guarantee.**

## Scope

v3.3 closes the remaining blind spots of the REAL execution path that v3.1/v3.2
had identified: the FastAPI import failure, unprotected mutations on the new
FastAPI, protection liveness, idempotence, partial fills, honest PnL, the
NAKED window after a failed close, the emergency stop, production security and
production exploitability.

## 1. FastAPI 0.141.1 startup — FIXED

`from api.index import app` raised `FastAPIError: Invalid args for response
field! ... Optional[starlette.requests.Request]` because FastAPI 0.141.x cannot
build a Pydantic field from `Optional[Request]` when analysing
`Depends(require_admin)`.

Fix: routes now wire a FastAPI-safe wrapper with an explicit `Request` typed
parameter:

```python
async def require_admin_dependency(
    request: Request,
    x_api_key: Optional[str] = Header(default=None),
) -> None:
    await require_admin(request=request, x_api_key=x_api_key)
```

The public contract of `require_admin` is **unchanged**: unit tests still call
`await idx.require_admin(x_api_key="wrong")` directly. `tests/test_v33.py`
asserts that every mutation route is protected and that the direct call
contract is preserved.

## 2. Protection state machine (the ID alone is dead)

New module `api/engines/protection_state.py`. A protection is ALIVE only if
its status is OPEN/PARTIALLY_FILLED **and** was confirmed on the exchange
within a freshness window (90 s). Normalized statuses:

```
OPEN  PARTIALLY_FILLED  FILLED  CANCELED  EXPIRED  REJECTED  UNKNOWN  NAKED
```

Metadata written per trade:

```
protection_status protection_checked_at protection_error_count
sl_order_status   tp_order_status       filled_protection
filled_protection_order_id              sibling_order_id
sibling_cancel_status                   last_accounted_filled
sl_tp_failed        protection_uncertain  protection_cancelled_before_close
```

Rules implemented in `BrokerConnector._refresh_protection_state`:

- OPEN confirmed recently → ALIVE, no software backstop;
- CANCELED / EXPIRED / REJECTED → **NAKED**: DB is NOT closed, CRITICAL audit
  + `PROTECTION_LOST` notification (manual action required);
- fetch error → `protection_error_count` incremented; after
  `MAX_CONSECUTIVE_ERRORS` (3) the state is **UNKNOWN**, audited (CRITICAL)
  and notified (`POSITION_UNKNOWN`);
- FILLED with `filled >= original_quantity - lot_tolerance` → honest close
  with the exchange price, the **sibling** protection is cancelled (the filled
  one is excluded);
- FILLED with `filled < original_quantity` → partial fill accounting (see §4);
- a stale OPEN (not confirmed recently) no longer blocks the backstop
  indefinitely — the backstop close is reduce-only, so it can never
  double-hedge: if the exchange protection fills first, the hedge is rejected.

The software backstop in `tick_management` (index.py) now uses
`protection_state.backstop_allowed(meta)` instead of "an ID exists".

## 3. NAKED window after a failed close (`close_position`)

The protections are cancelled **before** the hedge order. If at least one
cancel succeeded and then the hedge failed:

- the trade **stays OPEN** (never a fake success);
- `sl_tp_failed=True`, `protection_cancelled_before_close=True`,
  `protection_status="NAKED"`;
- cancelled IDs, the error and the timestamp are saved;
- CRITICAL audit `REAL_CLOSE_NAKED` + `HEDGE_FAILED_AFTER_CANCEL`
  notification (CRITICAL);
- the next tick treats the position as unprotected (backstop allowed).

## 4. Partial fills & PnL/fees (no double counting)

New module `api/engines/pnl_engine.py`:

```
BUY : gross_pnl = (exit_price - entry_price) * filled_quantity
SELL: gross_pnl = (entry_price - exit_price) * filled_quantity
net_pnl = gross_pnl - fees
```

- A trade is **never** fully closed when `filled < quantity` (lot tolerance);
- only the positive delta is accounted:
  `filled_delta = broker_filled - last_accounted_filled` (reconcile is
  idempotent — re-running with the same broker state accounts zero);
- entry fees, SL fees, TP fees, partial-fill fees and manual-close fees are
  all accumulated exactly once in `trade["fees"]`, and `pnl = gross - fees`;
- authoritative absence **without** a confirmed close price →
  `metadata.close_state = "CLOSED_PRICE_PENDING"`: the price is never
  fabricated, the PnL is finalized only after the fills are retrieved.

## 5. Durable idempotence

- Every REAL order first persists an **order intention** in a new backward
  compatible `order_intents` table (`client_order_id` PK, broker, symbol,
  side, quantity, `created_at`, status `PENDING_SEND` →
  `SENT`/`CONFIRMED`/`FAILED`/`ORDER_STATE_UNKNOWN`).
- `client_order_id` format: `QTP-{millis}-{uuid6}`, transmitted as
  `{"clientOrderId": ...}` to the exchange.
- After a send exception the adapter searches the order in: (1) fetch order
  by client ID, (2) open orders, (3) closed orders, (4) recent trades,
  (5) CCXT-surfaced exchange APIs. A recovered order is reconciled — **a
  second order is never sent**.
- When the state cannot be determined: `ORDER_STATE_UNKNOWN` → CRITICAL audit
  + notification + **no automatic retry** + no duplicate position.

`CCXTAdapter.cancel_order` now has a **single definition** (a duplicated
method used to shadow it and mask cancel failures).

## 6. Emergency stop, honest

`BrokerConnector.emergency_close_all()` walks every REAL OPEN trade in the
DB, sends a **unit close** through its broker, waits for the result and
closes the DB row **only after confirmation**. Per-position verdicts:

```
CLOSED_CONFIRMED / FAILED / ORDER_STATE_UNKNOWN / MANUAL_ACTION_REQUIRED
```

Only afterwards does reconciliation run — on spot, `get_positions() == []`
never proves a close (v3.1 rule preserved).

## 7. Notifications

`BrokerConnector.notifier` stays optional (default `None`). All new events
are dispatched through a try/except'd `_notify` helper — a dead channel never
blocks the main flow:

```
SL_TP_ATTACH_FAILED_NAKED  PROTECTION_LOST  POSITION_UNKNOWN
HEDGE_FAILED_AFTER_CANCEL  ORDER_STATE_UNKNOWN  RECONCILE_FAILING
```

`NotificationEngine.failure_count` is exposed in `/api/metrics`
(`notification_failures`).

## 8. Production security

- `APP_ENV=development|test|production` (default development).
- **Fail-fast at import time** in production only: startup is refused when
  `ADMIN_API_KEY` or `FERNET_KEY` is missing or manifestly weak
  (`api.security.is_weak_key` / `assert_production_ready`). Dev/test keep the
  open behaviour (`ADMIN_API_KEY=""` stays open, existing suite intact).
- Constant-time key comparison (`hmac.compare_digest`) — asserted in tests.
- Secret redaction in the structured JSON logs
  (`api.json_logging`): sensitive field names are redacted, `Bearer …` /
  `api_key=…` patterns in messages are redacted.
- Cookies Secure/HttpOnly/SameSite=Lax, session TTL, rate limiting,
  WebSocket auth, no CDN, no hard-coded keys (validate.sh gate).
- `GET /readyz` readiness probe: 503 when the DB is unavailable or the
  production configuration is invalid (`/healthz` stays liveness).
- `uploads/*.csv` audited: they are generated Railway deploy logs (no user
  data, no credentials) → removed from Git tracking, `uploads/` added to
  `.gitignore`, files kept on disk.

## 9. Multi-exchange contract matrix

- `tests/exchange_matrix.py`: offline contract mocks for **Binance, Bybit,
  OKX, Gate** (create order, clientOrderId, fetch order, open/closed orders,
  trades, stop contract, reduceOnly, cancel, full/partial fills, fees,
  timeout, rejected/canceled/expired, price/quantity precision).
- `scripts/testnet_broker_matrix.py`: **opt-in** live campaign
  (`CONFIRM_TESTNET=true`), sandbox-only, credentials from the environment
  only, minimal size, cleanup at the end, secret-scrubbed timestamped JSON
  report in `data/`. It never falls back to live. **No testnet credentials
  are available in this sandbox — the external campaign remains REQUIRED
  before any real ARM** (see docs/TESTNET_MATRIX.md).

## 10. Exploitability

- liveness `/healthz`, readiness `/readyz` (DB + production config);
- DB migrations are backward compatible (`CREATE TABLE IF NOT EXISTS`);
- `scripts/backup_db.py`: WAL checkpoint + atomic copy + verify/restore on a
  copy (tested offline);
- graceful shutdown: loop cancellation, CCXT client close, market-data close;
- bounded retries with jitter in the data cascade (provider layer);
- ambiguous orders are **never** retried; correlation ID =
  `client_order_id` (persisted + logged);
- structured NDJSON logs with redaction;
- new metrics: `ORDER_STATE_UNKNOWN`, `NAKED`, reconcile lag, broker
  latency, quote age, notification failures (`/api/metrics.real_safety`);
- a single scanner loop (lock) — two trading loops cannot run concurrently;
- restart recovery: persisted START/ARM/mode + REAL OPEN trades survive a
  restart and are reconciled again (tested);
- Railway: persistent volume on `/app/data` (railway.json), healthcheck on
  `/healthz` (see docs/RUNBOOK_PRODUCTION.md).

## Version & documentation

- `app.version = "3.3.0"`, README v3.3.0, CHANGELOG v3.3.0, API contract
  updated, runbook + testnet matrix written.

## Honest status

- The standard suite is fully **offline** (mocks only).
- The testnet campaign (scripts/testnet_broker_matrix.py) could NOT be run
  here: no exchange testnet credentials are available in this environment,
  and simulating the result would be dishonest. The mocks cover the contract
  surface, but a real campaign on Binance/Bybit/OKX/Gate testnets is still
  required.
- **REAL remains experimental. No profitability guarantee.**
