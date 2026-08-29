# Quantum Trade Pro

Bot de trading multi-marchés : données réelles, exécution papier réaliste, exécution réelle via CCXT, gestion de risque institutionnelle, dashboard web temps réel.

> **v3.3.0** — REAL is experimental. A successful testnet campaign is
> required before any real ARM. No profitability guarantee. (Campagne
> testnet réussie obligatoire avant tout ARM réel ; aucune garantie de
> rentabilité.)
>
> **v3.3.0** — Démarrage FastAPI 0.141.x réparé (`require_admin_dependency`),
> **toutes** les mutations protégées (test d'inspection des routes),
> machine d'état des protections SL/TP (un ID seul ne prouve plus rien :
> `OPEN/PARTIALLY_FILLED/FILLED/CANCELED/EXPIRED/REJECTED/UNKNOWN/NAKED`),
> fenêtre **NAKED** après close échoué (protections annulées + hedge en échec
> → trade OPEN, audit CRITICAL, notification), **idempotence durable**
> (intentions d'ordres persistées, `clientOrderId`, ordre retrouvé via
> fetch/open/closed/trades, `ORDER_STATE_UNKNOWN` sans retry automatique),
> **fills partiels** (delta positif seul, réconciliation idempotente, jamais
> de close complet si `filled < quantity`), PnL/frais sans double comptage
> (`CLOSED_PRICE_PENDING` si pas de prix confirmé), emergency stop **par
> position** (confirmé avant close DB), fail-fast `APP_ENV=production`
> (ADMIN_API_KEY/FERNET_KEY), readiness `/readyz`, backup/restore, redaction
> des secrets dans les logs, métriques REAL (`ORDER_STATE_UNKNOWN`, NAKED,
> reconcile lag), matrice testnet multi-exchange (Binance/Bybit/OKX/Gate).
>
> **v3.2.0** — REAL reste expérimental ; campagne testnet obligatoire avant tout ARM réel.
>
> **v3.2.0** — P0 REAL : **fail-close SL/TP** (échec d'attache → flatten
> immédiat reduceOnly, jamais de position nue silencieuse), réconciliation
> spot honnête (`get_positions()==[]` ne vaut close que si l'exchange sait
> énumérer les positions), persistance du `filled` réel + frais broker,
> **sandbox CCXT réel** (`set_sandbox_mode` avant `load_markets`, refus si
> indisponible), close unitaire REAL via ordre broker confirmé,
> `execute-signal` exige START+ARM. GET sensibles (`/api/settings`,
> `/api/brokers`, `/api/history`, `/api/wallets`, `/api/metrics`,
> `/api/optimization`) protégés par `ADMIN_API_KEY`. Déploiement cible :
> **Railway** (le mode serverless ne porte pas les boucles asyncio).
> La clé UI vit en **sessionStorage** + cookie de session HttpOnly
> `qtp_session` (`credentials:'include'`). L'exécution REAL reste
> **expérimentale** — aucun win rate de 99 % n'est promis.
>
> **v3.0** — RR défaut **2.0** (clamp 1.0–2.0), fenêtre news `trade`
> (défaut), mémoire hors-ligne 7 jours (quotes/OHLCV), persist START/ARM,
> cookie session HMAC + lockout, docs OpenAPI masqués hors TESTING.
>
> **v2.9.2** — Auto **RSI-14 only**, RR 1.5 (clamp 1.0–2.0). LIVE/DELAYED
> basé sur le timestamp (jamais le nom du provider). Scan timeout 120s.
> Corrélation par `underlying`. Objectifs réalistes : WR ≥45 %, RR net ≥1.5,
> PF ≥1.3, espérance >0. Le score est un filtre de sélectivité.
>
> **v2.6** — Robustesse production : calendrier économique JSON → HTML → cache SQLite 7 jours avec politique de panne explicite, dashboard 100 % local (aucun CDN), scan immédiat et incrémental crypto-first, intention d'exécution visible, Yahoo batché/caché, six sources crypto sans clé, providers tradfi optionnels, déduplication par sous-jacent et i18n en/fr/es/de complet. Suite : **390 passés / 6 skips réseau / 0 échec**.
>
> Les objectifs restent réalistes : **win rate ≥ 45 %**, **RR réalisé ≥ 1,5** et **espérance positive**. Aucun taux de réussite de 99 % n'est promis.

> **v2.5** — Audit & optimisation **par marché** : tuning par instrument/classe d'actifs (seuil d'entrée, SL, TP), adaptation au régime de volatilité (conservateur en marché volatil), faisabilité des marchés par capital (1 $ → 50 $+), audit par marché/classe/période et `GET /api/optimization`.

> **v2.4.1** — Audit intégral : sûreté CCXT/reduce-only, réconciliation broker fail-safe, backtest intrabar, validation stricte des ordres, agrégation news résiliente et couverture de toutes les fonctions exécutables.

> **v2.4** — Profils de capital MICRO/RETAIL/STANDARD et optimisation pilotée par l'audit.

> **v2.3** — Desk radar/hub/terminal, i18n, exécution institutionnelle ≥80 jusqu'à 10 slots, throttle per-symbol.

---

## ✨ Fonctionnalités

| Domaine | Détails |
|---|---|
| **Marchés** | 127 sous-jacents uniques : crypto en cascade Binance → Bybit → OKX → Kraken → Coinbase → Gate (API publiques sans clé) ; tradfi via TwelveData/Finnhub si leurs clés sont présentes, puis Yahoo batché/caché en fallback différé |
| **Stratégies** | **Exécution auto : RSI-14 uniquement.** Structure, tape, liquidity et arbitrage restent importables/testables (backtest explicite) mais ne sont jamais exécutées automatiquement. |
| **Optimisation par marché** | Tuning **par instrument et par classe d'actifs** (seuil d'entrée, TP, stop ATR) affiné par l'audit des trades ; adaptation au **régime de volatilité** (conservateur en marché volatil : seuil +5, stop ×1,25 → position réduite à risque égal) ; faisabilité des marchés par niveau de capital (1 $ → 50 $+) via `/api/optimization` |
| **Gestion de risque** | Sizing par % de risque, plafond de levier, validation du sens du SL, limite de perte quotidienne au niveau de l'ordre, cool-down après perte, filtre de corrélation, max positions, drawdown global persistant, **circuit breaker séries de pertes + risque réduit après pertes (anti-martingale)**, filtre coûts/volatilité |
| **Exécution** | Mode DEMO : papier réaliste (latence, slippage, rejets simulés) sur prix réels. Mode REAL : vrais ordres market via CCXT avec ordres SL/TP de protection, sizing arrondi aux contraintes d'exchange (lot/tick/min_notional), avertissement « experimental » explicite |
| **Gestion de positions** | Partial TP 50 % au 1:1 → break-even, trailing stop ATR, fermeture forcée hors session, réconciliation broker en mode REAL |
| **Filtres** | Calendrier économique multi-sources (Fair Economy JSON, ForexFactory HTML, dernier calendrier SQLite ≤ 7 jours), politique de panne `block_tradfi_only` par défaut (crypto 24/7 reste tradable, tradfi bloqué), sessions, fraîcheur des données, spread max |
| **Alertes** | Telegram + Discord (ouvertures, fermetures, emergency stop) |
| **Dashboard** | Interface autonome sans CDN : Tailwind compilé, Lucide local, polices système ; scanner immédiat/incrémental avec progression, badges LIVE/DIFFÉRÉ, raison de refus, filtres live, intention STOPPED/WAITING_SETUP/EXECUTING et bannières calendrier/API |
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
- Variables à définir sur Railway : `APP_ENV=production` (fail-fast), `ADMIN_API_KEY` (obligatoire, ≥ 16 caractères), `FERNET_KEY` (obligatoire, clé Fernet valide). `TWELVEDATA_API_KEY` et `FINNHUB_API_KEY` sont optionnelles ; sans elles Yahoo reste le fallback tradfi.
- En `APP_ENV=production`, le démarrage est **refusé** si une clé manque ou est manifestement faible (avant la première requête).
- Probes : `GET /healthz` (liveness) et `GET /readyz` (readiness — 503 si DB indisponible ou configuration production invalide).
- Runbook complet : [`docs/RUNBOOK_PRODUCTION.md`](docs/RUNBOOK_PRODUCTION.md) (backup/restore via `scripts/backup_db.py`, incidents NAKED / ORDER_STATE_UNKNOWN, emergency stop).
- Le calendrier persistant et la base de trades partagent le volume SQLite. La politique de panne se règle via `news_unavailable_policy=block_all|block_tradfi_only|allow_all` (`block_tradfi_only` par défaut : si le calendrier est HS, la crypto continue et le tradfi reste bloqué).
- `auto_start_on_startup=true` démarre sans armer ; `auto_arm_on_startup=true` arme **et** démarre. Les deux restent `false` par défaut.
- Healthcheck léger : `GET /healthz`. Détail providers/marchés/calendrier : `GET /api/health`.

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
    ├── data_providers/      # 6 crypto publics + TwelveData/Finnhub optionnels + Yahoo batché
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
    ├── news_engine.py       # Calendrier JSON/HTML/cache SQLite + politique fail-safe
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
| GET | `/api/status?market_id=` | État complet + analyse + signal + diagnostic + intention + état calendrier/scanner |
| GET | `/api/markets` | Vue dédupliquée des marchés par classe d'actif avec source/âge |
| GET | `/api/scanner` | Scan incrémental : `scanning`, `progress`, `last_scan_age_s`, filtre `live_only` |
| GET | `/api/history?mode=` | Journal des trades fermés |
| GET | `/api/performance?mode=` | Stats, espérance, par stratégie |
| GET | `/api/optimization?mode=` | Audit/optimisation live : tranche de capital, faisabilité des marchés au solde, tuning par marché, top/flop marchés |
| GET | `/api/metrics` | Compteurs système + ordres récents |
| GET | `/healthz` | Liveness (processus) |
| GET | `/readyz` | Readiness v3.3 (503 si DB indisponible ou config production invalide) |
| GET | `/api/health` | Santé providers + source active/âge/nombre de sources par marché + calendrier |
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
