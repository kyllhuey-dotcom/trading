# Data Sources Registry — v2.0

| Provider | Classe(s) | Symbole(s) | Fraîcheur | Fallback |
|---|---|---|---|---|
| Gate.io (CCXT) | CRYPTO | `BTC/USDT`, … | Temps réel | → Bybit → Binance |
| Bybit (CCXT) | CRYPTO (backup) | `BTC/USDT`, … | Temps réel | → Binance |
| Binance (CCXT) | CRYPTO (tertiaire) | `BTC/USDT`, … | Temps réel | — |
| Yahoo Finance | FOREX / INDICES / COMMODITIES / STOCKS / FUTURES / BONDS / ETFS | `EURUSD=X`, `^GSPC`, `GC=F`, `AAPL`, `ES=F`, `^TNX`, `SPY`… | ~15 min (différé) | — |

- Le mapping complet instrument → symbole provider est dans `api/engines/market_universe.py`
  (127 instruments, 8 classes d'actifs).
- Chaque marché a un mapping broker (`broker_symbols`) utilisé par le `BrokerConnector`
  pour router les ordres réels.
- Échec d'un provider → cooldown de 5 min par symbole (`DataLayer.failure_cache`).
- Santé des providers : `GET /api/health`.
