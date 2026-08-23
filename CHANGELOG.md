# Changelog - Quantum Trade Pro

## [2.7.0] - 2026-08-23 — Espérance positive : corrections structurelles

### Résumé
v2.7 corrige les causes structurelles qui empêchaient de mesurer et d'obtenir une espérance positive. Le score n'est jamais présenté comme une probabilité. Critères de santé : win rate ≥ 45 %, RR net ≥ 1.5, espérance > 0, profit factor ≥ 1.3.

### P0-1 — Plancher d'exécution 80 inviolable
- `AUTO_EXECUTION_SCORE_FLOOR = 80` dans `api/engines/constants.py`
- `SignalEngine` : `set_min_score()` et `effective_min_score()` clampent à 80–99
- `settings_schema.py` : `min_signal_score` borné 80–99 (au lieu de 50–99)
- `capital_profiles.py` : STANDARD de 75 à 80 ; `market_tuning.py` : borne min 50 → 80
- Régime calme ne peut jamais descendre sous 80
- `select_candidates()` et `/api/execute-signal` : plancher appliqué même avec config malveillante

### P0-2 — Meilleure opportunité principale
- `api/engines/opportunity_ranker.py` : classement pur et testé
- Exclusions : score < 80, signal non détecté, news/session, donnée périmée, spread/liquidité, RR net < 1.5, quarantaine, corrélation
- Métriques exposées : `estimated_net_rr`, `cost_to_risk`, `rank_score`, `rank_reasons`, `strategy_reliability` (shrinkage bayésien)
- `primary_opportunity` unique, `secondary_opportunities` visibles mais non exécutées
- `max_new_positions_per_scan` défaut 1, borné 1–3
- Endpoints : `/api/opportunities`, ajout dans `/api/status`

### P0-3 — Exécution LIVE sans attendre Yahoo
- `api/engines/opportunity_tracker.py` : single-flight/idempotence
- `opportunity_id` unique, TTL 30 s, aucun retry ne double un ordre
- Revalidation du signal avant exécution (re-fetch ticker + fraîcheur)

### P0-4 — Coûts et RR nets corrects
- `api/engines/cost_calculator.py` : formules cohérentes (entry/round_trip/cost_to_risk/net_rr)
- Porte de coûts sur TOUTES les stratégies
- Post-fill : recalcul RR net, refus si dégradé sous 1.5
- Fixtures BTC, petit token, forex, action

### P0-5 — Faux arbitrage directionnel
- `micro_arbitrage` : `tradable=False`, `main_reason = ARBITRAGE_REQUIRES_ATOMIC_TWO_LEG_EXECUTOR`
- Suppression des cibles non prouvées des docstrings

### P0-6 — Comptabilité fiable
- Positions : `initial_quantity`, `remaining_quantity`, `entry_fees`, `exit_fees`, `slippage_cost`, `funding_cost`, `partial_realized_pnl`, `gross_pnl`, `net_pnl`, `realized_r_multiple`, `opportunity_id`
- TP partiel ne remet pas le circuit breaker à zéro
- `register_closed_trade` appelé avec le résultat net final uniquement

### P1-7 — Quarantaine statistique
- `api/engines/quarantine.py` : min 30 trades avant verdict (au lieu de 10)
- Quarantaine auto : espérance ≤ 0, PF < 1, RR net < 1.5
- Visible, explicable, réversible, jamais de martingale

### P1-9 — Brokers fiables
- `/api/broker-capabilities` : exchange_id, passphrase, spot/futures, sandbox, SL/TP natifs, reduce_only, runtime_status, latency, permissions, routable_markets

### P1-10 — Wallets watch-only
- Wallets toujours `type=WATCH_ONLY`, `signing_capable=false`, `can_execute=false`
- Validation d'adresse par chaîne (Ethereum/Solana/Bitcoin)
- Aucun stockage de clé privée

### Tests
- **438 tests passés / 6 skips réseau** (390 → 438)
- **48 nouveaux tests** v2.7 (plancher 80, ranker, tracker, coûts, arbitrage, quarantaine)
- Ruff propre sur tous les nouveaux modules
- Aucune protection existante affaiblie

## [2.6.0] - 2026-08-22 — Correctifs des 8 causes racines de production

### Fiabilité et sûreté
- **Calendrier économique sans point unique de panne** : priorité au flux JSON
  Fair Economy, fallback vers le scraper HTML ForexFactory, puis dernier
  calendrier normalisé persisté dans SQLite (durée de validité maximale : 7
  jours). `/api/status` et `/api/health` exposent source, statut et âge.
- Nouveau réglage chaud `news_unavailable_policy` : `block_all` (**défaut
  fail-safe**), `block_tradfi_only` ou `allow_all`. Les timeouts scanner suivent
  la même politique au lieu de contourner le moteur news.
- **Données crypto sans clé** : cascade déterministe Binance → Bybit → OKX →
  Kraken → Coinbase → Gate, avec cooldown/backoff existant conservé. Chaque
  crypto dispose d'au moins trois mappings possibles.
- **Tradfi optionnel** : TwelveData et Finnhub ne sont enregistrés que lorsque
  `TWELVEDATA_API_KEY` / `FINNHUB_API_KEY` sont définies ; rate limiter isolé
  par provider ; Yahoo reste le fallback sans clé.
- `/api/health` rapporte aussi, pour chacun des 127 marchés, la source active,
  l'âge de la donnée et le nombre de sources enregistrées.

### Scanner, démarrage et données
- Premier scan lancé immédiatement au startup et publié **incrémentalement** :
  phase crypto temps réel en premier, classes Yahoo ensuite. `/api/scanner`
  ajoute `scanning`, `progress` (`n/127`) et `last_scan_age_s`.
- `auto_start_on_startup` ajouté (défaut `false`) ;
  `auto_arm_on_startup=true` arme désormais **et démarre**. L'intention
  normalisée `STOPPED` / `WAITING_SETUP` / `EXECUTING` est visible dans le
  header et `/api/status` (anciens codes détaillés conservés pour compatibilité).
- Yahoo Finance est groupé par classe d'actifs et par cycle : cache ticker/1m
  60 s, 15m 5 min, dérivation 15m locale et backoff par symbole. Intervalle de
  scan par défaut porté de 20 à 30 s.
- Garde anti-données-différées inchangée : le badge n'autorise pas l'exécution ;
  `allow_delayed_data_trading=false` reste le défaut.

### Dashboard et qualité des vues
- Suppression des trois CDN runtime : Tailwind compilé dans
  `public/css/app.css`, Lucide dans `public/js/lucide.min.js`, police système et
  garde défensive sur chaque `lucide.createIcons`. `GET /` ne référence aucune
  ressource HTTP(S) externe.
- Compteur d'échecs `fetchAPI` avec bannière **API INJOIGNABLE** après trois
  échecs consécutifs ; bannière **CALENDRIER HORS LIGNE — exécution bloquée
  (fail-safe)** lorsque la politique bloque réellement.
- Radar jamais muet : skeleton `Scan en cours n/127…`, badges LIVE / DIFFÉRÉ
  ~15 min, filtre « Live seulement » et raison principale de refus par ligne.
  Les mêmes badges et filtre sont présents dans le Market Hub.
- Champ `underlying` sur tous les instruments ; suppression de `gc_f`, `es_f`
  et `nq_f` qui dupliquaient gold, spx et nasdaq, remplacés par trois
  sous-jacents liquides indépendants pour conserver 127 lignes uniques.
  Radar et Hub ont en plus une déduplication défensive, temps réel puis
  fraîcheur en priorité.
- i18n en/fr/es/de : dictionnaires strictement isomorphes (> 150 clés), `t(key)`
  dans les rendus dynamiques, plus de 160 hooks `data-i18n`, réapplication après rendu
  et langue serveur appliquée au chargement.

### Protections préservées et tests
- Aucune modification des protections de sizing à risque fixe,
  anti-martingale, profils de capital, tuning par marché, restrictions de
  session/news ou garde de fraîcheur. Cibles inchangées : **win rate ≥ 45 %**,
  **RR ≥ 1,5**, **espérance positive** — aucune promesse de 99 %.
- `tests/test_v26_root_causes.py` ajoute 15 tests hors réseau : JSON/HTML/cache
  7 jours/politiques, batch Yahoo/cache, parsing/cascade/activation providers,
  priorité crypto, progression, auto-start/arm, doublons Hub/Radar, autonomie
  du dashboard et parité i18n.
- Suite complète : **390 passés / 6 skips réseau / 0 échec** ; Ruff propre.

## [2.5.0] - 2026-08-22 — LOT R : Audit & optimisation PAR MARCHÉ + adaptation au régime

### Added (audit par marché — méthodologie demandée)
- **`api/engines/market_tuning.py`** (nouveau) : optimisation des paramètres **par
  marché financier** et par classe d'actifs — seuil d'entrée (`min_score`),
  take-profit (`risk_reward`), stop-loss (`atr_stop_multiplier`), filtre de
  coûts (`max_cost_ratio`).
  - `ASSET_CLASS_TUNING` : lignes de base par classe (CRYPTO ≠ FOREX ≠ BONDS…),
    `build_default_tuning(universe)` : 1 entrée par instrument (127).
  - **Adaptation au régime de volatilité** : `regime_of` / `regime_adjustments` —
    marché **VOLATILE → conservateur** (seuil d'entrée +5, stop élargi ×1.25,
    donc position réduite à risque égal), marché **stable/QUIET → légèrement
    plus engagé** (seuil −3, stop ×0.90), borné 50–99.
  - **Faisabilité par capital** : `min_capital_for` / `markets_feasible_for_capital`
    — quels marchés fonctionnent à 1 $, 5 $, 50 $+ (marge ≈ min_notional /
    levier, mêmes champs `min_order`/`leverage_max` que le moteur de risque).
  - **Recommandations pilotées par l'audit, par marché** : `recommend_for_market`
    (verdict LOSING / TP_TOO_TIGHT / COST_LEAK / PROFITABLE → action
    `QUARANTINE_OR_RAISE_SELECTIVITY`, `WIDEN_TAKE_PROFIT`,
    `TIGHTEN_COST_FILTER`, `KEEP_AND_SCALE`) + `build_tuning_from_audit` qui
    produit la carte `market_tuning` prête à coller dans `/api/settings`.
    Honnêteté statistique : **aucun verdict avant 10 trades fermés**.
- **`SignalEngine`** : `set_market_tuning`, `set_regime_adaptation`,
  `effective_min_score` / `effective_risk_reward` / `effective_atr_stop_multiplier`
  — le seuil d'entrée, le SL et le TP appliqués dépendent désormais du marché
  (audit) et du régime de volatilité. Le signal expose `regime`,
  `min_score_applied`, `atr_stop_multiplier`, `market_tuning_applied`.
- **Réglages chauds** : `regime_adaptation_enabled` (défaut `true`) et
  `market_tuning` (JSON par marché, défaut `{}`) — appliqués à chaud par
  `SettingsProvider.apply()` (défauts de classe fusionnés avec les overrides).
- **`GET /api/optimization`** (nouveau) : tranche de capital + réglages
  recommandés, faisabilité des marchés au solde courant, tuning par marché
  appliqué, top/flop marchés (trades fermés du mode demandé).
- **`scripts/profit_audit.py`** : agrégation **par marché**, **par classe
  d'actifs** et **par période mensuelle** (gains vs pertes dans le temps),
  classement des marchés, flag `--json`, et bloc « PER-MARKET OPTIMIZATION »
  avec le JSON `market_tuning` prêt à appliquer.
- **`scripts/optimize_params.py`** : section faisabilité des marchés au capital
  donné (levier plafonné par le profil de tranche).

### Tests & qualité
- **`tests/test_market_tuning.py`** (nouveau) : 21 tests — lignes de base par
  classe, faisabilité par tranche de capital (1 $ / 50 $), régimes, tuning par
  marché piloté par l'audit, intégration `SignalEngine`, réglages à chaud.
- Suite complète : **375 tests passés / 6 skips réseau / 0 échec**.

## [2.4.1] - 2026-08-22 — Audit intégral, sûreté d'exécution et tests exhaustifs

### Fixed
- Ordres SL/TP CCXT envoyés avec `reduceOnly` et paramètres `stopPrice`/`triggerPrice` corrects ; fermeture d'urgence corrigée (le dictionnaire `reduceOnly` était auparavant passé comme prix).
- Réconciliation REAL fail-safe : une panne de `fetch_positions` ne clôture plus à tort toutes les positions locales.
- Remplacement d'un broker sans fuite de connexion et fermeture/cancel d'urgence poursuivie même si un ordre individuel échoue.
- Association multi-symboles corrigée dans `tick_management` : chaque position reçoit désormais son propre ticker au lieu du dernier ticker du lot.
- Backtest isolé de l'état de risque live, sorties basées sur High/Low intrabar et hypothèse conservatrice lorsque SL et TP sont touchés sur la même bougie.
- Validation stricte des quantités, prix, SL/TP, directions, valeurs non finies, soldes et paramètres de backtest.
- Agrégateur de news résilient aux pannes partielles, déduplication normalisée et tri fiable des dates RFC/ISO.
- Arrêt FastAPI garanti par `finally`, avec annulation **et attente** de toutes les tâches de fond.
- Scripts `check_db.py`, `optimize_params.py`, `profit_audit.py` et `smoke_test.py` rendus importables, validés et testables sans effets de bord.

### Tests & qualité
- Nouveaux tests unitaires hors réseau pour les adaptateurs brokers, les providers de news, les scripts, les endpoints et les boucles de fond.
- **355 tests passés, 6 skips réseau, 0 échec** ; toutes les **368 fonctions exécutables** sont exercées.
- Couverture avec branches : **89,10 % globale** (`api` + `scripts`) ; couverture moteurs : **92,18 %**.
- Pipeline renforcé : compilation, Ruff, `pip check`, scan de secrets, suite complète (y compris `test_lot2_data.py`), portes de couverture 85 % globale / 80 % moteurs.

## [2.4.0] - 2026-08-22 — LOT Q : Petits capitaux + profils capital-aware

### Added (petit capital & optimisation)
- **`api/engines/capital_profiles.py`** : tranches de capital **MICRO (0–10 $)**,
  **RETAIL (10–50 $)**, **STANDARD (≥ 50 $)** avec risk %, RR, score min, positions
  max, levier max, stop ATR, notional min. Fonctions `resolve_bracket`,
  `profile_overrides`, `recommend_from_audit` + cibles réalistes
  (win rate ≥ 45 %, RR ≥ 1.5, espérance ≥ +0.5R, profit factor ≥ 1.3).
- **`capital_profile_mode`** (`manual`/`auto`) : en `auto`, le bot **sur-ride**
  `max_risk_pct`, `max_leverage`, `max_open_positions`, `min_signal_score`,
  `risk_reward_ratio`, `atr_stop_multiplier`, `min_trade_notional`,
  `max_cost_ratio` selon le solde. `capital_profile` exposé dans `/api/status`.
- **Petit capital** : `min_account_balance` et `min_trade_notional` configurables
  (défaut **1 $**) via `/api/settings`. **Suppression du plancher `10.0` codé en
  dur** dans `RiskEngine.calculate_position_size` — un compte de 1 $ à 10 $
  peut désormais trader (DEMO pleinement ; REAL soumis aux min-notional d'échange).
- **Stop ATR paramétrable** : `atr_stop_multiplier` (défaut 1.5, stop plus large
  en MICRO) câblé dans `SignalEngine` (`set_atr_stop_multiplier`).
- **Optimisation pilotée par l'audit** : `scripts/profit_audit.py` affiche des
  recommandations par stratégie (`DISABLE_OR_RAISE_SELECTIVITY`,
  `WIDEN_TAKE_PROFIT`, `TIGHTEN_COST_FILTER`, `KEEP`) ; nouveau script
  `scripts/optimize_params.py <balance>`.

### Tests
- `tests/test_capital_profiles.py` (10 tests) — tranches, petit capital, stop ATR,
  optimisation. Suite complète : **244 passés / 2 échecs pré-existants / 6 skips réseau**.

## [2.3.0] - 2026-08-22 — UI desk + institutional ≥80

- **Global Radar** : tri score DESC/ASC, filtres ≥80/≥90/crypto, highlight institutionnel, TRADE 1-clic (`POST /api/execute-signal`).
- **Market Hub** : scores live, sparklines, tri score/volume/variation, cartes glassmorphism.
- **Trade Terminal** : MARKET/LIMIT/STOP, risk-based size, order book, bougies + BOS/CHoCH, toasts.
- **i18n** : en/fr/es/de fonctionnel (`public/js/i18n.js`).
- **Hot-reload** : `validate_settings` / `ensure_defaults`, `POST /api/settings` message `Parameters deployed live`.
- **≥80 only** : `select_candidates` + `execution_intent` (IDLE/EXECUTING/FULL), jusqu'à 10 positions.
- **Throttle per-symbol** (plus de lock global 5s).
- Endpoints additifs : `/api/execute-signal`, `/api/orderbook`, `/api/ohlcv`, query `sort/order/filter` scanner, `execution_intent` dans `/api/status`.
- `max_open_positions` défaut nouvelles DB `"10"`, `language` hot-reload.
- `MARKET_UPDATE` + `data_age_ms`. Priorité providers Binance → Bybit → Gate.

## [2.2.0] - 2026-08-22 — LOT P : Rentabilité (fuites d'espérance corrigées)

### Fixed (fuites de rentabilité)
- **Seuil de score global** : `min_signal_score` s'applique désormais à **toutes** les stratégies. Avant, tape/liquidity/arbitrage pouvaient exécuter des signaux de score 60-70 alors que le seuil était 80 (fuite de sélectivité majeure).
- **`risk_reward_ratio` réellement appliqué** : le réglage existait dans les settings mais était codé en dur à 2.0 dans le SignalEngine. Il pilote maintenant le TP.
- **Filtre coûts/volatilité** : les signaux dont les coûts aller-retour (frais + slippage ≈ 0,2 %) dépassent `max_cost_ratio` (0,5 par défaut) × la distance de risque sont bloqués — ces trades sont mathématiquement perdants en espérance. Réglable via settings (`fee_pct`, `sim_slippage_pct`, `max_cost_ratio`).
- **Alpha override opt-in** : le bypass des filtres RANGE pour les scores ≥ 80 est désactivé par défaut (`alpha_override_enabled=false`) ; la restriction news/session reste **toujours** appliquée (jamais de trade pendant les news à fort impact).

### Added (protections)
- **Circuit breaker séries de pertes** : après `max_consecutive_losses` (3 par défaut) pertes d'affilée, le bot se met automatiquement en pause (bloqué au niveau de l'ordre). Un gain remet le compteur à zéro.
- **Scaling anti-martingale** : le risque est réduit après les pertes (100 % → 75 % → 50 %), jamais augmenté.
- **Time stop** : sortie automatique des positions qui trainent (`max_trade_duration_minutes`, 0 = désactivé par défaut).
- **`scripts/profit_audit.py`** : audit de rentabilité sur n'importe quelle base (y compris un export de ton volume Railway) — win rate, espérance, RR réalisé, PnL par stratégie, détection des trades « fuites de coûts ».

### Tests
- `tests/test_profitability.py` (19 tests) — chaque fuite est verrouillée par un test.
- Suite complète : **204 passés / 6 skips réseau / 0 échec**.

## [2.1.0] - 2026-08-22 — Amélioration continue (lots A → H)

### Observabilité (LOT A)
- `/api/metrics` enrichi (additif) : signaux générés/bloqués par stratégie, winrate simulé par mode/stratégie, latence scan/exécution, âge des données, ordres REAL vs DEMO, heartbeat WS.
- Logging JSON structuré (NDJSON) avec rotation (`api/json_logging.py` + helper `structured_log`).
- Heartbeat WebSocket dédié + ping/pong applicatif + watchdog frontend ; diagnostic complet même hors-ligne.

### Stratégies (LOTs B, C, D)
- **Arbitrage** : timeout par provider, fraîcheur + synchronisation des quotes, score de confiance 0-100.
- **Tape reading** : imbalance pondéré par la profondeur, velocity proportionnelle, seuil dynamique piloté par l'ATR, multiplicateur de conviction.
- **Liquidity gap** : détection spread élargi + zones de faible volume, confirmation « côté mince », stop logique sous le dernier cluster de liquidité.

### Risque & exécution (LOT E)
- Sizing exchange-aware : lot_size/tick_size/min_notional (MarketUniverse + CCXT markets), arrondis Decimal floor (jamais au-dessus du risque), SL/TP protecteurs, ordres manuels normalisés.

### Données (LOT F)
- Fallback providers : timeouts stricts partout, cooldown à escalade exponentielle (5 min → 60 min).
- **Garde anti-scalping sur données différées** : Yahoo (~15 min de retard) bloqué pour l'exécution auto (`NON_REALTIME_SOURCE`), opt-out `allow_delayed_data_trading`.
- Health check précis : ONLINE/DEGRADED/SLOW/ERROR par latence + historique par provider.

### Tests & CI (LOT G)
- Couverture **api/engines : 83 %** (porte CI 80 %) ; mocks complets hors réseau (`tests/mocks.py`).
- Tests réseau auto-skippables ; plus aucun test n'écrit dans `data/` ; code mort supprimé.
- `validate.sh` : portes 60 % (api) + 80 % (engines), scan secrets, check d'entrée.

### Production (LOT H)
- **Message REAL explicite** : `real_warning` dans `/api/status`, warning au basculement REAL + bannière frontend « LIVE EXECUTION STILL EXPERIMENTAL — USE DEMO FOR STRATEGIES ».
- **Rate limiting renforcé** : sliding window par IP (lectures 1200/min, mutations 300/min par défaut, réglable), 429 JSON.
- `.env.example` documenté ; suite finale **185 passés / 6 skips / 0 échec**.

## [2.0.0] - 2026-08-22 — Refonte complète (audit + corrections)
### Fixed (critique)
- **market_id propagé dans tous les signaux** — l'auto-trading était impossible (chaque ordre rejeté `MARKET_CLOSED`).
- **Mode REAL réel** : ordres market via CCXT + SL/TP de protection + position persistée + réconciliation broker. (Avant : ordre simulé présenté comme "FILLED".)
- **CCXTAdapter** : signature `(exchange_id, api_key, api_secret, passphrase)` corrigée (TypeError à l'ajout d'un broker).
- **Emergency stop REAL** : `close_all_positions` implémenté sur tous les adaptateurs.
- **RiskEngine** : validation du sens du SL, daily loss limit au niveau de l'ordre, cool-down appliqué, max positions depuis les settings, pic de drawdown persistant.
- **Endpoints manquants réimplémentés** : `/api/markets`, `/api/brokers`, `/api/wallets`, `/api/performance`, `/api/metrics`, `/api/news`, `/api/health`, `/api/backtest`, `/api/diagnose`, `/api/order`, `/api/demo/*`.
- **Crash JS corrigé** : `/api/status` renvoie le contrat complet ; frontend défensif.
- **Réglages appliqués à chaud** : risque, seuil de score, stratégies actives, scanner (cache TTL 5 s).
- **Tests** : 52 passés / 3 skips réseau ; suite isolée (DB temporaire) ; nouveaux tests de régression P0.

### Security
- Authentification `X-API-Key` (ADMIN_API_KEY) sur tous les endpoints mutables.
- Secrets brokers chiffrés Fernet au repos (préfixe `enc:v1:`), erreurs de décryptage loggées.
- `.gitignore` complet ; DB, logs, uploads et artefacts sensibles retirés du dépôt.

### Changed
- Boucles : fréquences assainies, timeouts stricts sur tous les appels réseau.
- SQLite : connexions toujours fermées, ordre déterministe, `busy_timeout`.
- Providers : Binance enregistré comme 3ᵉ fallback crypto ; logging structuré.
- Code mort supprimé (`crypto_provider.py`, `market_catalog.py`) ; state machine, diagnostic engine, news aggregator, backtest branchés sur l'API.
- Railway : volume `/app/data` persistant, healthcheck 30 s.
- Documentation entièrement resynchronisée avec le code (README, contrat API, audits honnêtes).

## [1.4.0] - 2026-08-21
### Added
- **Advanced Position Management**: Dynamic Trailing Stop, Partial Take-Profit (50%), and automatic Break-even.
- **Correlation Filtering**: RiskEngine now blocks highly correlated trades to prevent over-exposure.
- **Notification System**: Real-time Telegram and Discord alerts for signals and trade events.
- **Performance Dashboard**: New `/api/performance` endpoint with detailed strategy metrics (Winrate, PF, Expectancy).
- **Backtesting Engine**: Historical validation framework for strategies.
- **Realistic Paper Trading**: Simulated latency, slippage, and order rejections in DEMO mode.
- **Graceful Shutdown**: Proper closure of all broker connections and data streams.

### Improved
- Structured logging with automatic file rotation.
- Enhanced API metrics and observability.

## [1.3.0] - 2026-08-21
### Added
- **Multi-Strategy Core**: Implemented a modular framework supporting "multi" mode.
- **Micro-Arbitrage Strategy**: High-confidence strategy exploiting price discrepancies between Gate/Bybit/Binance.
- **Tape Reading Strategy**: Institutional-grade order flow analysis (Imbalance + Trade Delta).
- **Liquidity Gap Strategy**: Predictive scalping on order book holes.
- **Diagnostic Upgrade**: The engine now details which strategy generated the signal and why.
- **WebSocket Heartbeat**: Regular PING/PONG style broadcast to monitor connection health.

### Improved
- **Execution Efficiency**: Scanner now returns full signal data, avoiding redundant calculations in the background loop.
- **Emergency Stop**: Now triggers global `close_all_positions` across all connected broker adapters.
- **Metrics**: Added strategy-specific signal tracking to `/api/metrics`.

### Fixed
- Missing `add_broker` method in `BrokerConnector`.
- Concurrency hardening for `bot_state` access in background loops.

## [1.2.0] - 2026-08-21
### Hardened
- **Signal Engine**: Prevented KeyError on incomplete OHLCV data and hardened ATR calculation.
- **Concurrency**: Implemented global `state_lock` to prevent race conditions on shared bot state and active positions.
- **Security**: Added at-rest encryption for broker API secrets using Fernet.
- **Safety**: Mandatory data age verification (CRYPTO < 5s, Other < 60s) before trading.
- **Resilience**: Emergency Stop now force-closes all local positions.
- **SQLite**: Enabled WAL mode and busy timeout for better concurrent performance.

### Added
- **Observability**: New `/api/metrics` endpoint and structured logging.
- **Rate Limiting**: Basic IP-based rate limiting for API endpoints.
- **CI/CD**: Enhanced `scripts/validate.sh` with coverage requirements and added `scripts/smoke_test.py`.
- **UI**: Added "BETA" warning for REAL mode.

### Fixed
- Fixed `UnboundLocalError` in `/api/status` logic.
- Reduced log noise by implementing a failure cache for delisted tickers.
- Synchronized multiple background loops through a unified async lock.

## [1.1.0] - 2026-08-21
### Added
- **Multi-source Redundancy**: Added Bybit as a backup crypto provider to Gate.io.
- **Automated Fallback**: DataLayer now automatically retries on backup providers if the primary source fails.
- **Institutional Healthcheck**: New `/api/health` endpoint providing detailed system and provider connectivity status.
- **Validation Pipeline**: Added `scripts/validate.sh` to ensure code quality and safety before deployment.
- **Railway Configuration**: Added `railway.json` with explicit healthcheck path and start commands.
- **Unit & Integration Tests**: Added test suite covering RiskEngine math and API stability.

### Fixed
- **CRITICAL**: Fixed `UnboundLocalError: risk_reason` in `/api/status` endpoint.
- **Security**: Verified and ensured all API secrets are read from environment variables.
- **Stability**: Refactored background loops to be test-aware, preventing hangs during CI.

### Security
- Audit performed: 0 hardcoded secrets found.
- All dependencies pinned in `requirements.txt`.
