# API Contract — Quantum Trade Pro v2.0

Ce document décrit l'API **réelle** (synchronisé avec `api/index.py`).
Toutes les réponses sont en JSON. Les endpoints marqués 🔐 exigent l'en-tête
`X-API-Key: <ADMIN_API_KEY>` lorsque la variable `ADMIN_API_KEY` est définie
sur le serveur (sinon HTTP 401).

## 1. Système

### `GET /healthz`
```json
{ "status": "OK", "state": "STOPPED", "uptime_s": 1234 }
```

### `GET /api/status?market_id=btc_usdt`
Réponse complète (contrat stable — toujours ces clés, même si les données
sont indisponibles, auquel cas `status_display = "DATA ERROR"`) :
```json
{
  "status": "STOPPED",            // état de la machine à états
  "status_display": "ONLINE",     // ONLINE | DEGRADED | DATA ERROR
  "is_running": false,
  "mode": "DEMO",                 // DEMO | REAL
  "armed": false,
  "balance": 10000.0, "equity": 10000.0,
  "daily_pnl": 0.0, "drawdown": 0.0,
  "demo_balance": 10000.0, "real_balance": 0.0,
  "selected_market": "btc_usdt",
  "asset_info": { "display_symbol": "BTC/USDT", "asset_class": "CRYPTO", "leverage_max": 100, "...": "..." },
  "ticker": { "symbol": "BTC/USDT", "last": 60000.0, "bid": "...", "ask": "...", "status": "LIVE", "timestamp": 1710000000000 },
  "news": { "trading_allowed": true, "day_ok": true, "session_ok": true, "news_ok": true,
            "blocking_event": null, "next_events": [], "timestamp": 1710000000000 },
  "analysis": { "status": "VALID", "trend": "BULLISH", "market_state": "TRENDING",
                "is_hh": true, "is_hl": true, "bos": false, "choch": false,
                "momentum": 0.5, "atr": 100.0, "indicators": { "rsi": 55.0, "ema8": "...", "ema21": "...", "ema_cross": "BULLISH" } },
  "signal": { "status": "SIGNAL_DETECTED", "direction": "BUY", "score": 85,
              "entry": 60000.0, "sl": 59000.0, "tp": 62000.0, "strategy": "structure",
              "market_id": "btc_usdt", "reason": "..." },
  "diagnosis": { "symbol": "btc_usdt", "main_blocker": "NONE", "main_reason": "...",
                 "checks": { "DATA_VALID": "PASS", "...": "FAIL" }, "secondary_blockers": [] },
  "active_trades": [], "history": [],
  "stats": { "markets": 127, "scanned": 127, "signals": 0, "tradable": 0 },
  "performance": { "total_trades": 0, "win_rate": 0, "profit_factor": 0, "total_pnl": 0 },
  "broker_info": { "connected_brokers": [], "broker_count": 0, "emergency_stop": false },
  "broker_connected": false,
  "best_setups": [],
  "execution_intent": { "code": "IDLE", "message": "Waiting for institutional setup ≥ 80" }
}
```

### Contrôles du bot 🔐
| Endpoint | Corps | Réponse |
|---|---|---|
| `POST /api/start` | — | `{ success, state, armed }` — START scanne sans armer ; `arm_on_start_demo=true` (réglage opt-in, défaut `false`) arme en DEMO uniquement, jamais en REAL |
| `POST /api/stop` | — | `{ success, state }` |
| `POST /api/arm` | — | `{ armed }` |
| `POST /api/mode` | — | `{ success, mode, message }` (refus si aucun broker connecté) |
| `POST /api/emergency-stop` | — | `{ success, state }` |
| `POST /api/emergency-reset` | — | `{ success, state }` |

## 2. Marchés & scanner

### `GET /api/markets`
Objet par classe d'actif (`CRYPTO`, `FOREX`, `INDICES`, `COMMODITIES`, `STOCKS`,
`FUTURES`, `BONDS`, `ETFS`) ; chaque entrée : `market_id`, `display_symbol`,
`name`, `price` (= `last`), `bid/ask/spread/volume`, `market_status`, `leverage_max`.

### `POST /api/scanner/trigger` 🔐
Déclenche un scan RSI contrôlé. Refuse un second scan simultané
(`SCAN_IN_PROGRESS`). Ne contourne aucune protection.

### `GET /api/scanner?sort=score&order=desc&filter=all`
```json
{ "assets": [ { "symbol": "btc_usdt", "asset_class": "CRYPTO", "price": 60000.0,
                "score": 85, "tradable": true, "trend": "BULLISH", "signal_data": {...},
                "diagnosis": {...}, "reason": "..." } ],
  "duration_s": 3.4, "sort": "score", "order": "desc", "filter": "all" }
```

### `POST /api/execute-signal` 🔐
`{ "market_id": "btc_usdt" }` — refuse score < min_signal_score / delayed Yahoo / déjà ouvert.

### `GET /api/orderbook?market_id=`
`{ market_id, display_symbol, bids, asks, available, data_age_ms, realtime_source }`

### `GET /api/ohlcv?market_id=&timeframe=1m&limit=60`
`{ candles:[{t,o,h,l,c,v}], bos, choch, last_high, last_low, trend }`

### `GET /api/diagnose?market_id=btc_usdt`
```json
{ "market_id": "btc_usdt", "diagnosis": {...}, "signal": {...}, "news": {...} }
```

## 3. Compte & trading

### `GET /api/history?mode=DEMO&limit=100`
Liste de trades fermés (plus récents d'abord).

### `GET /api/performance?mode=DEMO`
```json
{ "mode": "DEMO", "overall": { "total_trades": 10, "win_rate": 60.0, "profit_factor": 1.5,
                               "total_pnl": 120.0, "avg_win": 25.0, "avg_loss": -15.0 },
  "expectancy": 9.0, "by_strategy": { "structure": { "total_trades": 10, "win_rate": 60.0, "net_pnl": 120.0 } },
  "daily_pnl": 0.0, "timestamp": "..." }
```

### `GET /api/optimization?mode=DEMO` — audit & optimisation live (v2.5)
```json
{ "balance": 5.0,
  "capital_profile": { "mode": "auto", "bracket": "MICRO", "balance": 5.0, "applied": true },
  "recommended_settings": { "max_risk_pct": 1.0, "min_signal_score": 85, "risk_reward_ratio": 2.5 },
  "regime_adaptation_enabled": true,
  "market_feasibility": { "balance": 5.0, "asset_classes": { "CRYPTO": { "class_feasible": true, "markets_feasible": 36, "markets_total": 36, "min_capital_estimate": 0.6 },
                                                            "FOREX": { "class_feasible": false } } },
  "market_tuning": { "btc_usdt": { "min_score": 80, "risk_reward": 2.5, "atr_stop_multiplier": 1.5 } },
  "best_markets": [ { "market_id": "btc_usdt", "trades": 12, "win_rate": 66.7, "net_pnl": 48.0 } ],
  "worst_markets": [ { "market_id": "doge_usdt", "trades": 14, "win_rate": 28.6, "net_pnl": -15.0 } ] }
```
- `market_feasibility` : marchés réellement portables au solde courant (REAL) ;
- `market_tuning` : seuil d'entrée / TP / stop appliqués par marché ;
- `best_markets` / `worst_markets` : top/flop des marchés tradés (trades fermés).

### `GET /api/metrics`
Compteurs + durée de scan + derniers ordres (`recent_orders`).

### `POST /api/order` 🔐 — ordre manuel
```json
{ "market_id": "btc_usdt", "direction": "BUY", "quantity": 0.1,
  "sl": 59000.0, "tp": 62000.0, "override_risk": false }
```
- SL/TP optionnels (sinon : 1.5 ATR / 2R).
- Le risque (quantité × distance SL) est bloqué s'il dépasse `max_risk_pct` du
  solde, sauf `override_risk: true`.
- Réponse : `{ success, position }` ou `{ success: false, reason }`.

### `POST /api/demo/reset` 🔐
Ferme les positions démo, efface le journal démo, remet 10 000 €.
Réponse : `{ success, balance }`.

### `POST /api/demo/balance` 🔐
`{ "balance": 50000.0 }` → `{ success, balance }`.

### `POST /api/backtest` 🔐
```json
{ "market_id": "btc_usdt", "timeframe": "1h", "limit": 300,
  "strategy": "structure", "initial_balance": 10000.0 }
```
→ rapport (P&L net avec frais, win rate, trades).

## 4. Réglages

### `GET /api/settings`
```json
{ "max_risk_pct": "1.0", "max_leverage": "20", "max_daily_loss_pct": "3.0",
  "cool_down_mins": "30", "max_open_positions": "10", "language": "en", "trailing_stop_active": "true",
  "max_spread_pct": "0.5", "min_signal_score": "84", "risk_reward_ratio": "2.0",
  "trailing_stop_distance_atr": "1.5", "emergency_stop_drawdown_pct": "10.0",
  "auto_arm_on_startup": "false", "active_strategies": "rsi",
  "sim_latency_ms": "100", "sim_slippage_pct": "0.05", "sim_rejection_prob": "0.01",
  "partial_tp_ratio": "1.0", "scan_interval_seconds": "20", "peak_balance": "0",
  "regime_adaptation_enabled": "true", "market_tuning": "{}" }
```
- `market_tuning` : JSON par marché, ex.
  `{"doge_usdt": {"min_score": 95}, "eur_usd": {"risk_reward": 3.0, "atr_stop_multiplier": 2.5}}`
  (fusionné sur les lignes de base par classe d'actifs — voir `/api/optimization`).
- `regime_adaptation_enabled` : adaptation conservatrice aux marchés volatils
  (seuil +5, stop ×1.25) / engagement modéré sur marchés stables (seuil −3).

### `POST /api/settings` 🔐
Corps = dictionnaire `{ clé: valeur }`. Les réglages sont appliqués **à chaud**
(risque, seuil de score, stratégies actives, scanner).
Réponse : `{ success: true, applied, errors, message: "Parameters deployed live" }`.

## 5. Brokers & wallets

### `GET /api/brokers`
```json
{ "brokers": [ { "broker_id": "Binance Main", "exchange_id": "binance", "is_active": 1, "mode": null } ],
  "status": { "connected_brokers": [], "broker_count": 0, "emergency_stop": false } }
```

### `POST /api/brokers` 🔐
```json
{ "broker_id": "Binance Main", "exchange_id": "binance",
  "api_key": "...", "api_secret": "...", "api_passphrase": null }
```
→ `{ success, broker_id, connected }` (les identifiants sont chiffrés au repos).

### `POST /api/brokers/{broker_id}/toggle` 🔐 — `{ "is_active": true }`
### `DELETE /api/brokers/{broker_id}` 🔐
### `GET /api/wallets` — `{ "wallets": [...] }`
### `POST /api/wallets` 🔐 — `{ "wallet_id": "...", "provider": "METAMASK", "address": "0x...", "network": "mainnet" }`
### `DELETE /api/wallets/{wallet_id}` 🔐

## 6. Données & infos

| Endpoint | Description |
|---|---|
| `GET /api/health` | Santé des providers (`{ providers: [...] }`) |
| `GET /api/news` | Actualités agrégées (cache 5 min) |

## 7. WebSocket `/ws`

Messages poussés par le serveur :
- `{ "type": "ACCOUNT_STREAM", balance, equity, daily_pnl, drawdown, active_trades, is_running, armed, mode, stats, status }` (toutes les 1 s)
- `{ "type": "MARKET_UPDATE", market_id, display_symbol, price, status, timestamp, data_age_ms }` (toutes les 1 s)
- `{ "type": "SCAN_COMPLETED", duration_s, stats }` (après chaque scan)
