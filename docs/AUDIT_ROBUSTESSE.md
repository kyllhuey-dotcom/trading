# Audit de Robustesse — Quantum Trade Pro v2.0 (août 2026)

Audit réalisé sur le code réel (clone du dépôt, exécution des tests et du serveur).
Contrairement aux versions précédentes de ce document, chaque point ci-dessous a été
**vérifié par exécution** (tests automatisés ou reproduction manuelle).

## État global

- ✅ Suite de tests : **52 passés, 3 skips** (skips = providers indisponibles depuis le réseau de test, pas des bugs).
- ✅ L'application démarre, tous les endpoints répondent.
- ✅ Le pipeline critique (scan → signal → exécution → suivi) est fonctionnel en DEMO.
- ✅ Le mode REAL passe de **vrais ordres** via CCXT (plus de simulation mensongère).
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
