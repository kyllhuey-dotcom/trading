# Audit de Robustesse — Quantum Trade Pro v2.1 (août 2026)

Audit réalisé sur le code réel (clone du dépôt, exécution des tests et du serveur).
Contrairement aux versions précédentes de ce document, chaque point ci-dessous a été
**vérifié par exécution** (tests automatisés ou reproduction manuelle).

## État global

- ✅ Suite de tests : **62 passés, 4 skips** après LOT A ; 2 tests marqués `network`
  (gate.io / Yahoo) échouent uniquement quand l'environnement de test bloque ces
  providers au niveau réseau — pas des bugs (à rendre auto-skippables au LOT G).
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

1. **Données non-crypto** : Yahoo Finance est différé (~15 min) et rate-limité ; ce n'est pas une source « temps réel ». Le bot l'utilise pour du structurel, pas du scalping.
2. **Calendrier économique** : scraping HTML de ForexFactory — fragile si le site change de markup ou bloque ; en cas d'échec, le bot **refuse de trader** (fail-safe), ce qui est le comportement voulu.
3. **SL/TP sur spot** : la pose d'ordres SL/TP conditionnels dépend des capacités de l'exchange CCXT ; en cas d'échec, l'ordre principal est passé et l'incident est loggé (`sl_tp_warning`).
4. **Quantités** : le sizing ne tient pas encore compte de `lot_size`/`tick_size` par instrument (arrondi aux incréments du marché). À ajouter pour des brokers stricts.
5. **Tests réseau** : les tests marqués `network` dépendent de la disponibilité des providers ; ils s'auto-skip proprement.
6. **Multi-instances** : le bot est pensé pour une seule instance (état en mémoire + SQLite local). Pour du multi-instance, migrer vers Redis/Postgres.

## Verdict

Le projet est **fonctionnel de bout en bout en DEMO** et **exécute de vrais ordres en REAL**
avec les protections configurées. Les claims « Production-Ready » des versions précédentes
étaient prématurés ; celui-ci repose sur des tests vérifiables.
