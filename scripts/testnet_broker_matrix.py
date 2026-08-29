#!/usr/bin/env python3
"""v3.3 — OPT-IN testnet broker matrix campaign (Binance/Bybit/OKX/Gate).

Honesty rules (non-negotiable):
- Runs ONLY when CONFIRM_TESTNET=true is set in the environment;
- Sandbox/testnet ONLY: the adapter must confirm `sandbox=True` before any
  order; if the exchange refuses sandbox mode the campaign ABORTS (it never
  falls back to live);
- Credentials are read ONLY from the environment (never from files/DB):
      BINANCE_API_KEY / BINANCE_API_SECRET
      BYBIT_API_KEY   / BYBIT_API_SECRET
      OKX_API_KEY     / OKX_API_SECRET / OKX_API_PASSPHRASE
      GATE_API_KEY    / GATE_API_SECRET
- Minimal size: one order per exchange at the catalogue minimum notional;
- Cleanup: every open order and position is closed/cancelled at the end;
- The JSON report scrubs every secret (only masked keys are written);
- Timestamped report: data/testnet_matrix_YYYYmmdd_HHMMSS.json.

Usage:
    CONFIRM_TESTNET=true OKX_API_KEY=... OKX_API_SECRET=... OKX_API_PASSPHRASE=... \
        python3 scripts/testnet_broker_matrix.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import logging

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("testnet_matrix")

MATRIX = ("binance", "bybit", "okx", "gate")

# Minimal campaign notional per exchange (USD) — tiny on purpose.
MIN_NOTIONAL_USD = {
    "binance": 10.0, "bybit": 10.0, "okx": 10.0, "gate": 10.0,
}

# Symbols per exchange (the first tradable of the app catalogue).
SYMBOL = {
    "binance": "BTC/USDT", "bybit": "BTC/USDT",
    "okx": "BTC/USDT", "gate": "BTC/USDT",
}

_SENSITIVE_RE = re.compile(
    r"(?i)(api[_-]?key|api[_-]?secret|passphrase|secret|token|password)")


def _scrub(value: Any) -> Any:
    """Recursively mask anything that looks like a credential."""
    if isinstance(value, dict):
        return {k: ("***" if _SENSITIVE_RE.search(str(k)) else _scrub(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    if isinstance(value, str) and _SENSITIVE_RE.search(value) and len(value) > 6:
        head = value[:4]
        return f"{head}***"
    return value


def _mask_key(key: Optional[str]) -> Optional[str]:
    if not key:
        return None
    return f"{key[:4]}***{key[-2:]}" if len(key) > 8 else "***"


@dataclass
class ExchangeReport:
    exchange: str
    sandbox: bool = False
    connected: bool = False
    created_order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    fetch_order_status: Optional[str] = None
    open_orders_seen: int = 0
    closed_orders_seen: int = 0
    trades_seen: int = 0
    stop_created: Optional[str] = None
    stop_cancelled: bool = False
    cancelled_orders: List[str] = field(default_factory=list)
    positions_closed: int = 0
    errors: List[str] = field(default_factory=list)
    duration_ms: int = 0


def _env(prefix: str) -> Dict[str, Optional[str]]:
    up = prefix.upper()
    return {
        "api_key": os.getenv(f"{up}_API_KEY"),
        "api_secret": os.getenv(f"{up}_API_SECRET"),
        "passphrase": os.getenv(f"{up}_API_PASSPHRASE"),
    }


async def _run_one(exchange_id: str, report: ExchangeReport) -> None:
    from api.engines.broker_adapters.ccxt_adapter import CCXTAdapter

    creds = _env(exchange_id)
    if not creds["api_key"] or not creds["api_secret"]:
        report.errors.append("missing environment credentials")
        return
    logger.info("[%s] connecting (sandbox only)...", exchange_id)
    adapter = CCXTAdapter(exchange_id, creds["api_key"], creds["api_secret"],
                          creds.get("passphrase"), sandbox=True)
    t0 = time.monotonic()
    connected = await adapter.connect()
    report.connected = connected
    if not connected:
        report.errors.append("connection failed in sandbox mode")
        return
    # Hard gate: the adapter MUST be in sandbox mode. Never live.
    if not bool(getattr(adapter, "sandbox", False)):
        report.errors.append("ABORT: adapter not confirmed in sandbox mode")
        await adapter.close()
        return
    report.sandbox = True
    client = adapter.client
    symbol = SYMBOL[exchange_id]
    try:
        markets = getattr(client, "markets", {}) or {}
        market = markets.get(symbol) or {}
        try:
            min_amount = float(market.get("limits", {}).get("amount", {}).get("min") or 0)
        except (TypeError, ValueError, AttributeError):
            min_amount = 0.0
        # Minimal quantity: the exchange minimum or enough for the minimal
        # notional, whichever is larger (kept tiny).
        price = None
        try:
            ticker = await client.fetch_ticker(symbol)
            price = float((ticker or {}).get("last") or 0) or 100.0
        except Exception as exc:
            report.errors.append(f"ticker: {exc}")
            price = 100.0
        qty = min_amount if min_amount > 0 else MIN_NOTIONAL_USD[exchange_id] / price
        qty = float(client.amount_to_precision(symbol, qty))
        if float(qty) <= 0:
            report.errors.append("computed minimal quantity is zero")
            return
        client_order_id = f"QTP-TNM-{int(time.time() * 1000)}-{exchange_id.upper()[:4]}"
        order = await client.create_order(
            symbol, "market", "buy", float(qty), None,
            {"clientOrderId": client_order_id})
        report.created_order_id = order.get("id")
        report.client_order_id = client_order_id
        logger.info("[%s] order %s created (qty=%s)",
                    exchange_id, order.get("id"), qty)
        try:
            fetched = await client.fetch_order(order.get("id"), symbol)
            report.fetch_order_status = fetched.get("status")
        except Exception as exc:
            report.errors.append(f"fetch_order: {exc}")
        try:
            report.open_orders_seen = len(await client.fetch_open_orders(symbol) or [])
        except Exception as exc:
            report.errors.append(f"fetch_open_orders: {exc}")
        try:
            fetch_closed = getattr(client, "fetch_closed_orders", None)
            if callable(fetch_closed):
                report.closed_orders_seen = len(
                    await fetch_closed(symbol, limit=20) or [])
        except Exception as exc:
            report.errors.append(f"fetch_closed_orders: {exc}")
        try:
            report.trades_seen = len(await client.fetch_trades(symbol, limit=20) or [])
        except Exception as exc:
            report.errors.append(f"fetch_trades: {exc}")

        # Stop order with the exchange-specific contract (reduceOnly).
        from api.engines.broker_adapters.ccxt_adapter import _stop_order_args
        stop_price = client.price_to_precision(
            symbol, price * 0.95) if price else None
        if stop_price:
            stop_type, stop_params = _stop_order_args(exchange_id,
                                                      float(stop_price), "sell")
            try:
                stop = await client.create_order(symbol, stop_type, "sell",
                                                 float(qty), None, stop_params)
                report.stop_created = stop.get("id")
            except Exception as exc:
                report.errors.append(f"stop_create: {exc}")
    except Exception as exc:
        report.errors.append(f"campaign: {exc}")
    finally:
        # Cleanup: cancel open orders + close positions (testnet hygiene).
        try:
            for open_order in await client.fetch_open_orders(symbol) or []:
                try:
                    await client.cancel_order(open_order["id"], symbol)
                    report.cancelled_orders.append(open_order["id"])
                except Exception as exc:
                    report.errors.append(f"cancel {open_order.get('id')}: {exc}")
        except Exception as exc:
            report.errors.append(f"cleanup open orders: {exc}")
        try:
            fetch_positions = getattr(client, "fetch_positions", None)
            if callable(fetch_positions):
                for position in await fetch_positions() or []:
                    contracts = position.get("contracts")
                    if not contracts:
                        continue
                    side = str(position.get("side", "")).lower()
                    hedge = "sell" if side == "long" else "buy"
                    await client.create_order(
                        position["symbol"], "market", hedge, float(contracts),
                        None, {"reduceOnly": True})
                    report.positions_closed += 1
        except Exception as exc:
            report.errors.append(f"cleanup positions: {exc}")
        await adapter.close()
    report.duration_ms = int((time.monotonic() - t0) * 1000)


async def run_campaign(exchanges: List[str]) -> Dict[str, Any]:
    reports: Dict[str, ExchangeReport] = {}
    for exchange_id in exchanges:
        report = ExchangeReport(exchange=exchange_id)
        await _run_one(exchange_id, report)
        reports[exchange_id] = report
        logger.info("[%s] done (errors=%s)", exchange_id, report.errors)
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exchanges", default=",".join(MATRIX),
                        help="comma-separated subset of: " + ",".join(MATRIX))
    parser.add_argument("--report-dir", default="data")
    args = parser.parse_args()

    if os.getenv("CONFIRM_TESTNET", "").lower() != "true":
        logger.error("REFUSED: set CONFIRM_TESTNET=true to run the testnet "
                     "campaign (this script NEVER runs otherwise).")
        raise SystemExit(2)

    unknown = [e for e in args.exchanges.split(",") if e not in MATRIX]
    if unknown:
        logger.error("Unknown exchanges: %s (allowed: %s)",
                     unknown, ",".join(MATRIX))
        raise SystemExit(2)

    reports = asyncio.run(run_campaign([e for e in args.exchanges.split(",")]))
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "mode": "TESTNET_ONLY (sandbox enforced, no live fallback)",
        "exchanges": {},
        "overall_status": "PASS" if all(
            r.connected and not r.errors for r in reports.values()) else "FAIL",
    }
    for exchange_id, report in reports.items():
        summary["exchanges"][exchange_id] = _scrub({
            "sandbox": report.sandbox,
            "connected": report.connected,
            "created_order_id": report.created_order_id,
            "client_order_id": report.client_order_id,
            "fetch_order_status": report.fetch_order_status,
            "open_orders_seen": report.open_orders_seen,
            "closed_orders_seen": report.closed_orders_seen,
            "trades_seen": report.trades_seen,
            "stop_created": report.stop_created,
            "stop_cancelled": report.stop_cancelled,
            "cancelled_orders": report.cancelled_orders,
            "positions_closed": report.positions_closed,
            "errors": report.errors,
            "duration_ms": report.duration_ms,
            "credentials": {"api_key": _mask_key(_env(exchange_id)["api_key"]),
                            "api_secret": _mask_key(_env(exchange_id)["api_secret"]),
                            "passphrase": _mask_key(_env(exchange_id)["passphrase"])},
        })

    os.makedirs(args.report_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(args.report_dir, f"testnet_matrix_{stamp}.json")
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nReport written to {report_path}")
    if summary["overall_status"] != "PASS":
        print("Campaign did not fully pass — see errors above. "
              "A passing campaign is REQUIRED before any real ARM.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
