# Quantum Trade Pro

Bot de trading multi-marchés : données réelles, exécution papier réaliste, exécution réelle via CCXT, gestion de risque institutionnelle, dashboard web temps réel.

> **v2.5** — Audit & optimisation **par marché** : tuning par instrument/classe d'actifs (seuil d'entrée, SL, TP), adaptation au régime de volatilité (conservateur en marché volatil), faisabilité des marchés par capital (1 $ → 50 $+), audit par marché/classe/période et `GET /api/optimization`. Suite : **375 passés / 6 skips réseau / 0 échec**.

> **v2.4.1** — Audit intégral : sûreté CCXT/reduce-only, réconciliation broker fail-safe, backtest intrabar, validation stricte des ordres, agrégation news résiliente et couverture de toutes les fonctions exécutables.

> **v2.4** — Profils de capital MICRO/RETAIL/STANDARD et optimisation pilotée par l'audit.

> **v2.3** — Desk radar/hub/terminal, i18n, exécution institutionnelle ≥80 jusqu'à 10 slots, throttle per-symbol.

---

## ✨ Fonctionnalités

| Domaine | Détails |
|---|---|
| **Marchés** | 127 instruments : crypto (Gate/Bybit/Binance avec fallback), forex, indices, matières premières, actions, futures, obligations, ETF (Yahoo Finance) |
| **Stratégies** | Structure (BOS/CHoCH, HH/HL/LH/LL), micro-arbitrage inter-plateformes, tape reading (imbalance + delta), liquidity gap (order book) |
| **Optimisation par marché** | Tuning **par instrument et par classe d'actifs** (seuil d'entrée, TP, stop ATR) affiné par l'audit des trades ; adaptation au **régime de volatilité** (conservateur en marché volatil : seuil +5, stop ×1,25 → position réduite à risque égal) ; faisabilité des marchés par niveau de capital (1 $ → 50 $+) via `/api/optimization` |
| **Gestion de risque** | Sizing par % de risque, plafond de levier, validation du sens du SL, limite de perte quotidienne au niveau de l'ordre, cool-down après perte, filtre de corrélation, max positions, drawdown global persistant, **circuit breaker séries de pertes + risque réduit après pertes (anti-martingale)**, filtre coûts/volatilité |
| **Exécution** | Mode DEMO : papier réaliste (latence, slippage, rejets simulés) sur prix réels. Mode REAL : vrais ordres market via CCXT avec ordres SL/TP de protection, sizing arrondi aux contraintes d'exchange (lot/tick/min_notional), avertissement « experimental » explicite |
| **Gestion de positions** | Partial TP 50 % au 1:1 → break-even, trailing stop ATR, fermeture forcée hors session, réconciliation broker en mode REAL |
| **Filtres** | Calendrier économique (news à fort impact), sessions de marché, fraîcheur des données, spread max |
| **Alertes** | Telegram + Discord (ouvertures, fermetures, emergency stop) |
| **Dashboard** | Interface web : scanner global, terminal de trading manuel, positions, journal, brokers/wallets, réglages en direct (appliqués à chaud), diagnostic de décision par marché, WebSocket temps réel |
| **Sécurité** | Authentification par clé API sur tous les endpoints mutables, secrets brokers chiffrés au repos (Fernet), rate limiting par IP (sliding window), audit log complet, bannière d'avertissement en mode REAL |

## 🚀 Démarrage rapide

```bash
# 1. Dépendances
pip install -r requirements.txt

# 2. (Optionnel mais recommandé) Configuration
cp .env.example .env        # puis remplissez ADMIN_API_KEY et FERNET_KEY
export $(cat .env | xargs)  # ou utilisez votre gestionnaire d'env

# 3. Lancement
python3 -m api.index
# → http://localhost:8000
```

### Production (Railway)

- Le volume `/app/data` (configuré dans `railway.json`) persiste la base SQLite entre les déploiements.
- Variables à définir sur Railway : `ADMIN_API_KEY` (obligatoire), `FERNET_KEY` (obligatoire si vous connectez un broker).
- Healthcheck : `GET /healthz`.

## 🧪 Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -q
```

- La suite est **isolée** (base de données de test temporaire — votre base de prod n'est jamais touchée).
- Les tests marqués `network` s'auto-skip si le réseau ou le provider est indisponible.
- Couverture : **89 % globale avec branches** (`api` + `scripts`, porte CI 85 %) et **92 % sur `api/engines`** (porte CI 80 %). Toutes les fonctions exécutables sont exercées au moins une fois.
- Les données Yahoo (différées ~15 min) ne sont **pas** utilisées pour l'exécution automatique (garde anti-scalping, opt-out `allow_delayed_data_trading`).
- **Audit de rentabilité** : `python3 scripts/profit_audit.py [chemin/vers/quantum_trade.db] [balance]` → win rate, espérance, RR réalisé et PnL **par stratégie ET par marché**, par classe d'actifs et **par période mensuelle** (gains vs pertes), classement des marchés + détection des trades dont les frais dépassaient le risque + **recommandations d'optimisation par marché** (seuil d'entrée / SL / TP) avec le JSON `market_tuning` prêt à appliquer. Flag `--json` pour l'export.
- **Petit capital (1 $ → 50 $+)** : `min_account_balance` et `min_trade_notional` sont configurables (défaut 1 $), et `capital_profile_mode=auto` fait choisir automatiquement au bot le profil adapté au solde (MICRO / RETAIL / STANDARD) — plus de plancher codé en dur à 10 $.
- **Optimiseur de paramètres** : `python3 scripts/optimize_params.py <balance>` → profil de tranche + réglages recommandés + **marchés faisables à ce capital**.
- **Optimisation par marché** : réglage à chaud `market_tuning` (JSON par marché, produit par l'audit) + `regime_adaptation_enabled` (adaptation conservatrice aux marchés volatils). Vue live : `GET /api/optimization`.

## 🔐 Sécurité

- **Auth** : si `ADMIN_API_KEY` est défini, tous les endpoints POST/DELETE exigent l'en-tête `X-API-Key`. Sans cette variable, l'accès est ouvert (dév uniquement) — un warning est loggé au démarrage.
- **Secrets brokers** : chiffrés AES (Fernet) dans SQLite si `FERNET_KEY` est défini. Sans clé, stockage en clair avec warning.
- Le frontend conserve la clé admin en `localStorage` et la renvoie à chaque requête mutante.

## 🧭 Architecture

```
api/
├── index.py                 # FastAPI + boucles de trading + endpoints + WS
├── models.py                # Contrats Pydantic
└── engines/
    ├── data_engine.py       # Orchestration des providers (fallback)
    ├── data_layer.py        # Couche d'accès données (fallback + cooldown)
    ├── data_health.py       # Monitoring des providers
    ├── data_providers/      # gate / bybit / binance / yahoo
    ├── market_universe.py   # 127 instruments + heures de marché
    ├── analysis_engine.py   # Structure de marché, ATR, RSI, EMA
    ├── signal_engine.py     # Scoring 0-100 + SL/TP ATR
    ├── strategies/          # arbitrage / tape / liquidity / base
    ├── risk_engine.py       # Sizing, limites, corrélation, cooldown
    ├── capital_profiles.py  # Tranches MICRO/RETAIL/STANDARD + optimisation audit
    ├── market_tuning.py     # Tuning par marché/classe + régimes + faisabilité capital
    ├── execution_engine.py  # Exécution papier (DEMO)
    ├── execution_router.py  # Routage DEMO/REAL + anti-doublon
    ├── broker_connector.py  # Agrégation brokers + wallets web3
    ├── broker_adapters/     # CCXT réel + PrimeXBT
    ├── portfolio_engine.py  # Balances, historique, stats
    ├── scanner_engine.py    # Scan de l'univers + diagnostic
    ├── news_engine.py       # Calendrier économique (ForexFactory)
    ├── news_aggregator.py   # Fils d'actualités (RSS)
    ├── backtest_engine.py   # Backtest historique (frais inclus)
    ├── diagnostic_engine.py # Diagnostic de décision par marché
    ├── notification_engine.py
    ├── state_machine.py     # États du bot
    └── db_manager.py        # SQLite (chiffrement + audit)
```

## 📡 API

Contrat complet : [`docs/api_contract.md`](docs/api_contract.md). Résumé :

| Méthode | Endpoint | Description |
|---|---|---|
| GET | `/api/status?market_id=` | État complet + analyse + signal + diagnostic + news |
| GET | `/api/markets` | Vue de tous les marchés par classe d'actif |
| GET | `/api/scanner` | Dernier scan global |
| GET | `/api/history?mode=` | Journal des trades fermés |
| GET | `/api/performance?mode=` | Stats, espérance, par stratégie |
| GET | `/api/optimization?mode=` | Audit/optimisation live : tranche de capital, faisabilité des marchés au solde, tuning par marché, top/flop marchés |
| GET | `/api/metrics` | Compteurs système + ordres récents |
| GET | `/api/health` | Santé des providers de données |
| GET | `/api/news` | Actualités agrégées |
| GET | `/api/brokers` / `/api/wallets` | Connexions |
| POST | `/api/start` `/api/stop` `/api/arm` `/api/mode` | Contrôles du bot 🔐 |
| POST | `/api/emergency-stop` `/api/emergency-reset` | Sécurité 🔐 |
| POST | `/api/order` | Ordre manuel 🔐 |
| POST | `/api/demo/reset` `/api/demo/balance` | Gestion du capital démo 🔐 |
| POST | `/api/settings` | Réglages appliqués à chaud 🔐 |
| POST | `/api/brokers` + toggle/delete, `/api/wallets` + delete | Gestion des connexions 🔐 |
| POST | `/api/backtest` | Backtest à la demande 🔐 |
| WS | `/ws` | Flux temps réel (compte + marché + scans) |

🔐 = nécessite `X-API-Key` quand `ADMIN_API_KEY` est configuré.

## ⚠️ Avertissement

Ce logiciel est fourni à but éducatif. Le trading à effet de levier comporte un risque élevé de perte en capital. En mode REAL, lisez et comprenez le code de `broker_adapters/ccxt_adapter.py`, testez longuement en DEMO, et n'investissez que ce que vous pouvez vous permettre de perdre.
