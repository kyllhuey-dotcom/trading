# Audit de Robustesse — Quantum Trade Pro v2.1 (août 2026)

Audit réalisé sur le code réel (clone du dépôt, exécution des tests et du serveur).
Contrairement aux versions précédentes de ce document, chaque point ci-dessous a été
**vérifié par exécution** (tests automatisés ou reproduction manuelle).

## État global

- ✅ Suite de tests : **185 passés, 6 skips** (skips = tests `network` dont le
  provider est indisponible — ils s'auto-skip proprement désormais).
- ✅ Couverture : **83 % sur `api/engines`** (engines critiques), 76 % sur `api/`
  (portes CI : 80 % / 60 % dans `scripts/validate.sh`).
- ✅ L'application démarre, tous les endpoints répondent.
- ✅ Le pipeline critique (scan → signal → exécution → suivi) est fonctionnel en DEMO.
- ✅ Le mode REAL passe de **vrais ordres** via CCXT (plus de simulation mensongère).
- ✅ Observabilité avancée : `/api/metrics` enrichi, logging JSON structuré, heartbeat WS.
- ⚠️ Points de vigilance restants listés en fin de document.

## Bugs critiques corrigés (vérifiés)

| Bug | Avant | Après | Preuve |
|---|---|---|---|
| `market_id` absent des signaux | tout ordre auto rejeté `MARKET_CLOSED` | propagé par `SignalEngine` + `ScannerEngine` (toutes stratégies) | `tests/test_p0_fixes.py` |
| Ordre REAL simulé | `{"success": True, "FILLED"}` sans appel broker | `create_order` réel + ordres SL/TP + position enregistrée en DB | `ccxt_adapter.py` (lecture) |
| `CCXTAdapter.__init__` incompatible | `TypeError` à l'ajout d'un broker | signature `(exchange_id, api_key, api_secret, passphrase)` | `test_ccxt_adapter_accepts_credentials` |
| Emergency stop REAL inopérant | appel d'une méthode inexistante | `close_all_positions` implémenté sur tous les adaptateurs | `test_all_adapters_implement_close_all_positions` |
| SL du mauvais côté accepté | perte garantie possible | bloqué : "Invalid SL for BUY/SELL" | `tests/test_risk_unit.py` |
| Daily loss limit ignoré à l'ordre | vérifié seulement au tick global | vérifié dans `calculate_position_size` | `test_risk_daily_loss_limit` |
| `crypto_provider.py` cassé (ImportError) | fichier mort et invalide | supprimé (Binance est un vrai provider enregistré) | import OK |
| Endpoints fantômes du frontend | `/api/brokers`, `/api/markets`, … → 404 | tous réimplémentés | smoke test + `test_status_contract` |
| Crash JS sur `/api/status` (champ `status` absent) | le dashboard ne se mettait jamais à jour | contrat complet + JS défensif | contrat documenté |
| Réglages UI sans effet | `settings` jamais relus | rechargement TTL 5 s appliqué à chaud (risque, score, stratégies, scanner) | `test_risk_settings_reload_changes_sizing` |
| Test destructif de la DB de prod | `test_perfection` vidait `quantum_trade.db` | DB de test isolée (conftest `DB_PATH` temporaire) | conftest.py |
| Tests async jamais exécutés | "async def functions are not natively supported" | `pytest.ini` → `asyncio_mode = auto` | suite verte |
| Endpoint qui pouvait pendre | snapshot sans timeout | `asyncio.wait_for` (données 20 s, news 10 s, scan 30 s) | suite stable |
| Balances REAL fausses | ETH + USDT additionnés | USDT uniquement pour `total_usdt`, autres actifs séparés | `broker_connector.py` |
| Volume Railway absent | DB effacée à chaque déploiement | volume `/app/data` configuré | `railway.json` |
| Secrets et DB commités sur GitHub | DB, logs Railway, 7 DB de test dans le repo | `.gitignore` complet + fichiers retirés du suivi git | `git status` |

## Améliorations structurantes

### LOT A — Observabilité & métriques avancées (v2.1)

- **`/api/metrics` enrichi** (additif, anciens champs conservés) :
  - `signals_generated_by_strategy` / `signals_blocked_by_strategy` : signaux par stratégie (générés / bloqués) ;
  - `orders_by_mode` : nombre d'ordres `REAL` vs `DEMO` routés ;
  - `winrate_simulated` : taux de réussite calculé sur les trades clôturés, par mode et par stratégie (`PortfolioEngine`, jamais simulé « au doigt mouillé ») ;
  - `latency` : latence moyenne/max du scan et de l'exécution (fenêtre glissante bornée) ;
  - `data_age` : âge des données (dernier/moyen/max + échantillons), alimenté par `data_age_ms` ajouté à chaque résultat de scan ;
  - `heartbeat` : séquence, nombre de clients WS, dernier envoi.
- **Logging structuré JSON** (`api/json_logging.py`) : NDJSON avec rotation par taille
  (`data/trading_bot.jsonl`, 5 × 5 Mo), champs standard + champs `extra` personnalisés +
  `exc_info`, sérialisation défensive des valeurs non-JSON. La console reste lisible.
  Helper `structured_log()` pour des événements métier typés (ordre exécuté, erreur de boucle).
- **WebSocket heartbeat robuste** : boucle dédiée (15 s) émettant `HEARTBEAT`
  (seq / server_time / clients / state), ping/pong applicatif (`ping` → `pong`),
  nettoyage automatique des connexions mortes, métadonnées par client.
  Le frontend envoie un ping toutes les 30 s et se reconnecte si le serveur
  reste silencieux > 90 s (watchdog).
- **Diagnostic complet hors-ligne** : les snapshots « DATA ERROR » exposent
  désormais toutes les clés de vérification du contrat (dont `RISK_VALID`),
  ce qui rend la suite de tests fiable même sans réseau.
- Nouveau module `api/engines/metrics_engine.py` (verrouillé, fenêtres bornées 500 échantillons).
- Tests dédiés : `tests/test_metrics_observability.py` (15 tests).

### LOT B — Arbitrage micro-temporel

- `DataLayer.get_cross_quotes` : timeout strict par provider (5 s), cache d'échec,
  metadata de timing par quote (`latency_ms`, `received_at`, `age_ms`).
- `MicroArbitrageStrategy` : porte de fraîcheur (quotes périmées écartées),
  porte de synchronisation (dispersion max entre quotes → NO_TRADE),
  score de confiance 0-100 (spread + fraîcheur + synchro), `min_confidence` optionnel.
- Rétrocompatible : quotes sans timing = fraîches. Tests : `tests/test_arbitrage_robustness.py`.

### LOT C — Tape reading

- Imbalance pondéré par la profondeur (10 niveaux, poids décroissants),
  velocity proportionnelle signée (clamp ±40), multiplicateur de conviction
  (alignement ×1.15 / conflit ×0.85).
- **Seuil dynamique piloté par l'ATR** (clampé 15-60, fallback au seuil de
  base sans OHLCV) : marchés calmes = plus sensible, marchés violents = plus strict.
- Tests : `tests/test_tape_reading_robustness.py` (mocks orderbook/trades/volatilités).

### LOT D — Liquidity gap

- Détection enrichie : trous de prix (15 niveaux), spread élargi bloquant,
  profil de volume (confirmation « côté mince », discount 25 % sinon).
- **Stop logique anticipatif** : SL sous le dernier cluster de liquidité
  (au-dessus pour les shorts), TP en 2R, fallback % conservé.
- Tests : `tests/test_liquidity_gap_robustness.py` (13 tests + régressions).

### LOT E — Sizing & contraintes d'exchange

- Nouveau module `api/engines/exchange_constraints.py` : arrondis Decimal sûrs
  (quantité floor au lot, prix au tick, SL/TP protecteurs), portes min_notional.
- `RiskEngine.calculate_position_size(market_info=…)` (optionnel, rétrocompatible) :
  quantité floored, levier/notional recalculés, min notional vérifié.
- `CCXTAdapter.get_market_constraints` (parsing offline des marchés CCXT),
  ordres manuels normalisés (DEMO + REAL).
- Tests : `tests/test_exchange_constraints.py` (17 tests).

### LOT F — Data & providers

- `DataLayer` : timeouts par provider sur tous les chemins, cooldown d'échec à
  **escalade exponentielle** (5 min → 60 min max), reset au succès.
- **Garde anti-scalping sur données différées** : les instruments Yahoo
  (~15 min de retard) sont bloqués pour l'exécution automatique
  (`NON_REALTIME_SOURCE`), opt-out explicite `allow_delayed_data_trading`.
- Health check précis : ONLINE/DEGRADED/SLOW/ERROR selon latence, historique
  par provider (checks, échecs consécutifs, dernier OK), latence Gate/Bybit.
- Tests : `tests/test_providers_hardening.py` (9 tests offline).

### LOT G — Couverture & CI

- **Couverture engines critiques : 83 %** (objectif ≥ 80 %), avec mocks complets
  hors réseau : `tests/mocks.py` (orderbooks, trades, tickers, cross-quotes,
  exchange ccxt factice, engines factices), `tests/test_engine_coverage.py`
  (scanner, router, signal, exécution, diagnostic, univers, providers ccxt,
  DB manager) et `tests/test_offline_engine_coverage.py` (news/calendrier,
  backtest, broker connector, notifications, Yahoo, risque, sessions avec
  horloge fixée).
- **Tests réseau auto-skippables** : les tests `network` se skip proprement
  quand Gate/Yahoo/Binance sont inaccessibles (plus d'échecs d'environnement).
- **Isolation totale** : plus aucun test n'écrit dans `data/` du dépôt
  (bases temporaires `tmp_path` partout).
- **Code mort supprimé** : `data_providers/crypto_provider.py` (imports
  cassés) et `market_catalog.py` (doublon inutilisé de MarketUniverse).
- **`scripts/validate.sh` plus strict** : porte de couverture 60 % globale +
  80 % sur `api/engines`, scan de secrets, check d'entrée applicative.
- **Bybit** : `get_order_book` / `get_recent_trades` implémentés (parité Gate/Binance).

### LOT H — Polish production & documentation

- **Message REAL explicite** : `real_warning` dans `/api/status`, champ `warning`
  dans la réponse de `/api/mode`, log structuré `MODE_SWITCHED_TO_REAL` et
  bannière frontend permanente en mode REAL (« Live execution still
  experimental – use DEMO for strategies »).
- **Rate limiting basique renforcé** (`api/rate_limit.py`) : sliding window par
  IP (60 s), budget lectures 1200/min et mutations 300/min (réglables par env),
  réponse 429 JSON, `/healthz` exempté, mémoire bornée. Tests unitaires avec
  horloge injectée.
- **`.env.example`** documenté (sécurité, rate limit, logging, heartbeat, gardes).
- CHANGELOG 2.1.0 + README et audit resynchronisés avec le code réel.

- **Authentification** : `ADMIN_API_KEY` protège tous les endpoints mutables (401 sinon).
- **Chiffrement** : secrets brokers Fernet au repos, préfixe `enc:v1:` explicite, erreurs de décryptage loggées (pas de retour silencieux).
- **SQLite** : connexions fermées systématiquement (context manager), `busy_timeout`, ordre déterministe.
- **Settings live** : `SettingsProvider` (cache TTL) → RiskEngine, SignalEngine, ScannerEngine.
- **Scanner** : diagnostic complet attaché à chaque marché non tradable, timeouts stricts, logging structuré.
- **Backtest** : frais inclus (0,1 % aller-retour), accessible via `/api/backtest`.
- **State machine** : réellement câblée (start/stop/emergency) et exposée dans `/api/status`.
- **News aggregator / diagnostic engine / health monitor** : branchés sur des endpoints.
- **Frontend** : échappement HTML des données dynamiques, gestion 401 avec clé admin, tous les onglets fonctionnels (markets, brokers, wallets, positions, settings, provisioning, ordres manuels).

## Points de vigilance restants (assumés)

1. **Données non-crypto** : Yahoo Finance est différé (~15 min) et rate-limité ; ce n'est pas une source « temps réel ». **Depuis le LOT F, l'exécution automatique y est bloquée par défaut** (`NON_REALTIME_SOURCE`) — le structurel Yahoo ne sert plus qu'à l'analyse/backtest, sauf opt-out explicite `allow_delayed_data_trading=true`.
2. **Calendrier économique** : scraping HTML de ForexFactory — fragile si le site change de markup ou bloque ; en cas d'échec, le bot **refuse de trader** (fail-safe), ce qui est le comportement voulu.
3. **SL/TP sur spot** : la pose d'ordres SL/TP conditionnels dépend des capacités de l'exchange CCXT ; en cas d'échec, l'ordre principal est passé et l'incident est loggé (`sl_tp_warning`).
4. **Quantités** : ✔ résolu au LOT E — `lot_size`/`tick_size`/`min_notional` par instrument (MarketUniverse + marchés CCXT), arrondi floor + SL/TP protecteurs.
5. **Tests réseau** : les tests marqués `network` s'auto-skip proprement quand un provider est indisponible (LOT G).
6. **Multi-instances** : le bot est pensé pour une seule instance (état en mémoire + SQLite local). Pour du multi-instance, migrer vers Redis/Postgres. Le rate limiter en mémoire est donc par instance.
7. **Rate limiting** : sliding window par IP en mémoire (lectures 1200/min, mutations 300/min par défaut, réglable par env) — volontairement simple ; pour une exposition publique importante, placer un rate limiter/gateway en amont (Railway/Cloudflare).

## Verdict

Le projet est **fonctionnel de bout en bout en DEMO** et **exécute de vrais ordres en REAL**
avec les protections configurées, une observabilité avancée (métriques enrichies, logs JSON,
heartbeat WS), des stratégies durcies (fraîcheur/synchronisation/seuils dynamiques/stops
logiques), un sizing exchange-aware, une couverture de 83 % des engines critiques et un
avertissement REAL explicite. Les claims « Production-Ready » des versions précédentes
étaient prématurés ; celui-ci repose sur des tests vérifiables.
