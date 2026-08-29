#!/usr/bin/env python3
"""v3.3.2 — REALTIME DATA AUDIT: the app's public feeds vs real markets.

Non-negotiable rules (same as every other campaign in this repo):
- ONLY real data: every number in the report comes from a public endpoint
  response received in this run. Nothing is fabricated, estimated, or
  "reconstructed". If an endpoint is unreachable, the check is FAIL/ERROR —
  never skipped into a PASS.
- A passing audit means: all 10 public endpoints answered, the exchange
  feeds are fresh (timestamp age <= --max-age-s, default 10 s), and the
  prices agree within the numeric tolerances below:

      crypto pool (same quote currency, pairwise vs pool median)  <= 0.10 %
      cross-reference (CoinGecko vs exchange median, pools vs each
      other)                                                       <= 1.00 %
      tradfi (Yahoo AAPL vs Stooq AAPL)                            <= 0.70 %
      FX reference (Frankfurter/ECB fixing vs CoinGecko EURUSD)    <= 0.70 %

- Exit code 0 iff overall_status == PASS (1 otherwise).
- The JSON report is timestamped: data/realtime_audit_YYYYmmdd_HHMMSS.json

Usage:
    python3 scripts/realtime_data_audit.py [--max-age-s 10] [--report-dir data]
"""
from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any, Dict, List, Optional

import requests

TIMEOUT_S = 8.0
ATTEMPTS = 2  # a measurement, not a trade: one retry, then honest failure

# --------------------------------------------------------------------------- #
# Tolerances (non-negotiable — never lower these to force a PASS)             #
# --------------------------------------------------------------------------- #
TOLERANCES = {
    "crypto_max_dev_pct": 0.10,      # within a same-currency pool
    "cross_reference_max_pct": 1.00, # CoinGecko vs median, pool vs pool
    "tradfi_max_pct": 0.70,          # Yahoo vs Stooq
    "fx_reference_max_pct": 0.70,    # Frankfurter ECB fixing vs CoinGecko FX
}
DEFAULT_MAX_AGE_S = 10.0

# --------------------------------------------------------------------------- #
# Endpoints (all public, no key)                                               #
# --------------------------------------------------------------------------- #
BINANCE_URL = "https://api.binance.com/api/v3/ticker/24hr"
GATE_URL = "https://api.gateio.ws/api/v4/spot/tickers"
BYBIT_URL = "https://api.bybit.com/v5/market/tickers"
OKX_URL = "https://www.okx.com/api/v5/market/ticker"
KRAKEN_URL = "https://api.kraken.com/0/trades"
COINBASE_URL = "https://api.coinbase.com/v2/exchange/BTC-USD/ticker"
COINGECKO_URL = ("https://api.coingecko.com/api/v3/simple/price"
                 "?ids=bitcoin&vs_currencies=usd,eur"
                 "&include_last_updated_at=true")
YAHOO_URL = ("https://query1.finance.yahoo.com/v8/finance/chart/AAPL"
             "?range=1d&interval=1d")
STOOQ_URL = "https://stooq.com/q/l/"
FRANKFURTER_URL = "https://api.frankfurter.app/latest"

UA = {"User-Agent": "Mozilla/5.0 (QuantumTradePro-realtime-audit)"}


def _get(url: str, params: Optional[Dict[str, Any]] = None,
         headers: Optional[Dict[str, str]] = None) -> requests.Response:
    """GET with a strict timeout and one retry. Raises on final failure."""
    last_exc: Optional[BaseException] = None
    for _ in range(ATTEMPTS):
        try:
            response = requests.get(
                url, params=params, headers=headers or UA, timeout=TIMEOUT_S)
            if response.status_code == 200:
                return response
            last_exc = RuntimeError(f"HTTP {response.status_code}")
        except Exception as exc:  # noqa: BLE001 — report the real cause
            last_exc = exc
        time.sleep(0.5)
    assert last_exc is not None
    raise last_exc


def _dev_pct(price: float, ref: float) -> float:
    if ref <= 0:
        return float("inf")
    return abs(price - ref) / ref * 100.0


# --------------------------------------------------------------------------- #
# Per-endpoint fetchers — each returns a dict with `price`, optional `ts_s`   #
# (market-data timestamp in epoch seconds) and the raw excerpt used.          #
# --------------------------------------------------------------------------- #
def fetch_binance(now: float) -> Dict[str, Any]:
    data = _get(BINANCE_URL, params={"symbol": "BTCUSDT"}).json()
    price = float(data["lastPrice"])
    return {"price": price, "ts_s": float(data["closeTime"]) / 1000.0,
            "raw": {"lastPrice": data["lastPrice"], "closeTime": data["closeTime"]}}


def fetch_gate(now: float) -> Dict[str, Any]:
    data = _get(GATE_URL, params={"currency_pair": "BTC_USDT"}).json()
    item = data["result"][0]
    price = float(item["last"])
    return {"price": price, "ts_s": float(item["timestamp"]),
            "raw": {"last": item["last"], "timestamp": item["timestamp"]}}


def fetch_bybit(now: float) -> Dict[str, Any]:
    data = _get(BYBIT_URL, params={"category": "spot", "symbol": "BTCUSDT"}).json()
    item = data["result"]["list"][0]
    price = float(item["lastPrice"])
    return {"price": price, "ts_s": float(item["time"]) / 1000.0,
            "raw": {"lastPrice": item["lastPrice"], "time": item["time"]}}


def fetch_okx(now: float) -> Dict[str, Any]:
    data = _get(OKX_URL, params={"instId": "BTC-USDT"}).json()
    item = data["data"][0]
    price = float(item["last"])
    return {"price": price, "ts_s": float(item["ts"]) / 1000.0,
            "raw": {"last": item["last"], "ts": item["ts"]}}


def fetch_kraken(now: float) -> Dict[str, Any]:
    # /0/trades returns `result.last` = the most recent trade time (s).
    data = _get(KRAKEN_URL, params={"pair": "XBTUSD", "count": 1}).json()
    result = data["result"]
    key = [k for k in result if k != "last"][0]
    trade = result[key][0]
    price = float(trade[0])
    return {"price": price, "ts_s": float(result["last"]),
            "raw": {"trade": trade, "last_trade_time": result["last"]},
            "pair": "XBT/USD"}


def fetch_coinbase(now: float) -> Dict[str, Any]:
    from datetime import datetime
    data = _get(COINBASE_URL).json()["data"]
    price = float(data["price"])
    ts = datetime.fromisoformat(data["time"].replace("Z", "+00:00")).timestamp()
    return {"price": price, "ts_s": ts,
            "raw": {"price": data["price"], "time": data["time"]},
            "pair": "BTC/USD"}


def fetch_coingecko(now: float) -> Dict[str, Any]:
    data = _get(COINGECKO_URL).json()["bitcoin"]
    out: Dict[str, Any] = {"usd": float(data["usd"]), "eur": float(data["eur"])}
    updated = data.get("last_updated_at") or {}
    if updated.get("usd"):
        out["ts_s"] = float(updated["usd"])
    out["raw"] = data
    return out


def fetch_yahoo(now: float) -> Dict[str, Any]:
    data = _get(YAHOO_URL,
                headers={"User-Agent": "Mozilla/5.0"}).json()
    meta = data["chart"]["result"][0]["meta"]
    price = float(meta["regularMarketPrice"])
    out: Dict[str, Any] = {"price": price, "raw": {"symbol": meta.get("symbol"),
                                                   "exchangeName": meta.get("exchangeName")}}
    if meta.get("regularMarketTime"):
        out["ts_s"] = float(meta["regularMarketTime"])
    return out


def fetch_stooq(now: float) -> Dict[str, Any]:
    response = _get(STOOQ_URL, params={"s": "aapl.us", "f": "sd2t2ohlcv", "h": "",
                                       "e": "csv"})
    lines = [ln for ln in response.text.strip().splitlines() if ln]
    if len(lines) < 2:
        raise RuntimeError("stooq returned no data row")
    cols = lines[1].split(",")
    price = float(cols[6])  # Close
    out: Dict[str, Any] = {"price": price, "quote_date": cols[1],
                           "raw": {"date": cols[1], "time": cols[2],
                                   "close": cols[6]}}
    if cols[1] and cols[1][0].isdigit():
        from datetime import datetime
        out["ts_s"] = datetime.strptime(cols[1], "%Y-%m-%d").timestamp()
    return out


def fetch_frankfurter(now: float) -> Dict[str, Any]:
    data = _get(FRANKFURTER_URL, params={"from": "EUR", "to": "USD"}).json()
    price = float(data["rates"]["USD"])
    out: Dict[str, Any] = {"price": price,
                           "raw": {"base": data.get("base"), "rates": data["rates"]}}
    if data.get("date") and data["date"][0].isdigit():
        from datetime import datetime
        out["ts_s"] = datetime.strptime(data["date"], "%Y-%m-%d").timestamp()
    return out


ENDPOINTS = (
    ("binance", fetch_binance),
    ("gate", fetch_gate),
    ("bybit", fetch_bybit),
    ("okx", fetch_okx),
    ("kraken", fetch_kraken),
    ("coinbase", fetch_coinbase),
    ("coingecko", fetch_coingecko),
    ("yahoo", fetch_yahoo),
    ("stooq", fetch_stooq),
    ("frankfurter", fetch_frankfurter),
)

# Endpoints whose market-data timestamp must be <= max_age_s (the 10 s
# freshness test). Reference feeds (CoinGecko free tier, Yahoo, Stooq,
# Frankfurter ECB fixing) are deliberately NOT subject to it: they are
# cross-reference sources, and their delay is part of their contract.
FRESHNESS_ENDPOINTS = {"binance", "gate", "bybit", "okx", "kraken", "coinbase"}


def _median(values: List[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def run_audit(max_age_s: float) -> Dict[str, Any]:
    now = time.time()
    results: Dict[str, Any] = {}
    for name, fetcher in ENDPOINTS:
        entry: Dict[str, Any] = {"status": "PASS", "detail": ""}
        try:
            fetched = fetcher(now)
        except Exception as exc:  # noqa: BLE001 — the audit reports real causes
            entry.update(status="ERROR", detail=str(exc)[:300])
            results[name] = entry
            continue
        results[name] = {**fetched, "status": "PASS", "detail": ""}
        if name in FRESHNESS_ENDPOINTS:
            ts = fetched.get("ts_s")
            if ts is None:
                entry.update(status="FAIL", detail="no market timestamp in payload")
                results[name] = entry
                continue
            age = max(0.0, now - float(ts))
            entry["age_s"] = round(age, 2)
            if age > max_age_s:
                entry.update(status="FAIL",
                             detail=f"data age {age:.1f}s > {max_age_s:.0f}s")
                results[name] = entry

    checks: Dict[str, Any] = {}

    def _add_check(name: str, value: float, limit_pct: float, detail: str) -> None:
        ok = value <= limit_pct
        checks[name] = {
            "value_pct": round(value, 6),
            "limit_pct": limit_pct,
            "status": "PASS" if ok else "FAIL",
            "detail": detail,
        }

    # --- crypto pools (same quote currency → 0.10 %) ---------------------- #
    usdt = [results[e]["price"] for e in ("binance", "gate", "bybit", "okx")
            if results[e].get("status") == "PASS" and "price" in results[e]]
    usd = [results[e]["price"] for e in ("kraken", "coinbase")
           if results[e].get("status") == "PASS" and "price" in results[e]]
    if len(usdt) >= 2:
        med = _median(usdt)
        worst = max(_dev_pct(p, med) for p in usdt)
        _add_check("crypto_usdt_pool", worst, TOLERANCES["crypto_max_dev_pct"],
                   f"max deviation vs median {med:.2f} across {len(usdt)} USDT feeds")
    else:
        checks["crypto_usdt_pool"] = {"value_pct": None,
                                      "limit_pct": TOLERANCES["crypto_max_dev_pct"],
                                      "status": "FAIL",
                                      "detail": "fewer than 2 usable USDT feeds"}
    if len(usd) >= 2:
        med = _median(usd)
        worst = max(_dev_pct(p, med) for p in usd)
        _add_check("crypto_usd_pool", worst, TOLERANCES["crypto_max_dev_pct"],
                   f"max deviation vs median {med:.2f} across {len(usd)} USD feeds")
    else:
        checks["crypto_usd_pool"] = {"value_pct": None,
                                     "limit_pct": TOLERANCES["crypto_max_dev_pct"],
                                     "status": "FAIL",
                                     "detail": "fewer than 2 usable USD feeds"}

    # --- cross-reference (1 %) --------------------------------------------- #
    cg = results.get("coingecko", {})
    if cg.get("status") == "PASS" and cg.get("usd") and usdt:
        _add_check("cross_reference_coingecko",
                   _dev_pct(cg["usd"], _median(usdt)),
                   TOLERANCES["cross_reference_max_pct"],
                   f"CoinGecko {cg['usd']:.2f} vs USDT pool median {_median(usdt):.2f}")
    else:
        checks["cross_reference_coingecko"] = {"value_pct": None,
                                               "limit_pct": TOLERANCES["cross_reference_max_pct"],
                                               "status": "FAIL",
                                               "detail": "CoinGecko or USDT pool unavailable"}
    if usdt and usd:
        _add_check("cross_reference_pools",
                   _dev_pct(_median(usd), _median(usdt)),
                   TOLERANCES["cross_reference_max_pct"],
                   f"USD pool median {_median(usd):.2f} vs USDT pool median {_median(usdt):.2f}")
    else:
        checks["cross_reference_pools"] = {"value_pct": None,
                                           "limit_pct": TOLERANCES["cross_reference_max_pct"],
                                           "status": "FAIL",
                                           "detail": "a crypto pool is missing"}

    # --- tradfi (0.7 %) ------------------------------------------------------ #
    yh, sq = results.get("yahoo", {}), results.get("stooq", {})
    if yh.get("status") == "PASS" and sq.get("status") == "PASS":
        _add_check("tradfi_aapl", _dev_pct(yh["price"], sq["price"]),
                   TOLERANCES["tradfi_max_pct"],
                   f"Yahoo {yh['price']:.2f} vs Stooq {sq['price']:.2f} (Stooq quote date {sq.get('quote_date')})")
    else:
        checks["tradfi_aapl"] = {"value_pct": None, "limit_pct": TOLERANCES["tradfi_max_pct"],
                                 "status": "FAIL",
                                 "detail": "Yahoo or Stooq unavailable"}

    # --- FX reference (0.7 %) ------------------------------------------------ #
    fx = results.get("frankfurter", {})
    if fx.get("status") == "PASS" and cg.get("status") == "PASS" and cg.get("eur"):
        cg_eur_usd = cg["usd"] / cg["eur"]
        _add_check("fx_reference_eurusd", _dev_pct(fx["price"], cg_eur_usd),
                   TOLERANCES["fx_reference_max_pct"],
                   f"Frankfurter/ECB {fx['price']:.4f} vs CoinGecko {cg_eur_usd:.4f} "
                   f"(ECB fixing is a daily reference: a volatile day may legitimately exceed it)")
    else:
        checks["fx_reference_eurusd"] = {"value_pct": None,
                                         "limit_pct": TOLERANCES["fx_reference_max_pct"],
                                         "status": "FAIL",
                                         "detail": "Frankfurter or CoinGecko FX unavailable"}

    overall = "PASS"
    for name, entry in results.items():
        if entry.get("status") != "PASS":
            overall = "FAIL"
            break
    if overall == "PASS" and any(c.get("status") != "PASS" for c in checks.values()):
        overall = "FAIL"

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "campaign": "realtime-data-audit",
        "rule": "real data only — every value below was received live from the public endpoint",
        "tolerances": TOLERANCES,
        "freshness": {"max_age_s": max_age_s,
                      "applies_to": sorted(FRESHNESS_ENDPOINTS),
                      "not_applicable": "reference feeds (coingecko free tier, yahoo, stooq, frankfurter) — delayed by contract"},
        "endpoints": results,
        "checks": checks,
        "overall_status": overall,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-age-s", type=float, default=DEFAULT_MAX_AGE_S,
                        help="freshness threshold for exchange feeds (default 10 s)")
    parser.add_argument("--report-dir", default="data")
    args = parser.parse_args()

    report = run_audit(args.max_age_s)
    payload = json.dumps(report, indent=2, ensure_ascii=False)
    print(payload)
    os.makedirs(args.report_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(args.report_dir, f"realtime_audit_{stamp}.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(payload + "\n")
    print(f"\nReport written to {path}")
    if report["overall_status"] != "PASS":
        print("AUDIT FAILED — see endpoints/checks above. "
              "A PASS requires every endpoint AND every tolerance.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
