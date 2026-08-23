# Audit opérationnel + couverture — Quantum Trade Pro

Date : 2026-08-23  
Branche de travail actuelle : `arena/01a02f02-trading`  
Cible demandée pour la prochaine conversation : appliquer les correctifs **sur `main`**.

## Verdict

Le projet n’a **pas de bug bloquant**. La suite est verte.

| Check | Résultat |
|---|---|
| ruff | All checks passed |
| pytest | **573 passed**, 6 skipped (réseau), 0 failed |
| couverture `api` | **93 %** |
| smoke local | 10/10 endpoints 200 (session précédente) |

Ce n’est **pas** 100 % des lignes : il reste surtout des branches d’erreur / chemins rares dans `api/index.py` (boucles scanner/exécution) et `scanner_engine.py`.

## Couverture par zone (après cette session)

| Zone | Couverture | Commentaire |
|---|---|---|
| `quarantine.py` | ~95–100 % | OK |
| `scan_contract.py` | 100 % | OK |
| `keyed_tradfi_provider.py` | 100 % | OK |
| `public_ccxt_provider.py` | 97 % | 2 lignes health except |
| `yahoo_provider.py` | ~95 %+ | mocké |
| `market_universe.py` | ~98 %+ | horaires testés |
| `provider_capabilities.py` | ~98 %+ | OK |
| `capital_profiles.py` | ~98 %+ | audit recos |
| `json_logging.py` | 100 % | OK |
| `scanner_engine.py` | **83 %** | plus gros trou moteur |
| `api/index.py` | **88 %** | plus gros trou applicatif |
| `signal_engine.py` | 88 % | branches stratégies legacy |
| `execution_engine.py` | 90 % | DEMO SL/TP rares |
| `news_engine.py` | 90 % | fallbacks calendrier |
| `broker_connector.py` | 89 % | runtime snapshot / REAL |

## Écarts fonctionnels (pas des crashes)

1. **`get_broker_capabilities` lit `broker_connector._adapters`** mais le connecteur expose `active_adapters`. En prod le statut runtime reste souvent `UNKNOWN`.
2. **Zinc `ZNC=F`** : Yahoo a échoué en smoke (`possibly delisted`). À valider en live ; lumber `LBR=F` à ne pas toucher.
3. **`QUANTUM_ENV=production`** refuse le boot sans `ADMIN_API_KEY` + `FERNET_KEY`. À positionner sur Railway.
4. **Auto-exécution RSI seulement** : structure/tape/liquidity/arbitrage non exécutés automatiquement (voulu).
5. **Données Yahoo = DELAYED** : jamais scalpées sans `allow_delayed_data_trading`.
6. **Étape live (health providers ERROR → OK)** : à faire uniquement sur un déploiement avec réseau.

## Ce qui a été ajouté ici (tests, pas de changement métier)

- `tests/test_full_function_coverage.py` : Yahoo, universe hours, capabilities, radar, capital audit, providers CCXT error paths, scanner unknown, json logging.
- Tests P0/P1 déjà présents : production guard, quarantine, scan_contract, keyed tradfi, public CCXT, handlers API.

## Ce qu’il reste à corriger (code, pas seulement tests)

Priorité pour la prochaine conversation sur `main` :

1. Aligner `_adapters` → `active_adapters` dans `get_broker_capabilities`.
2. Décider de `zinc` / `ZNC=F` après un check live (retirer ou changer de ticker).
3. Couvrir les lignes rouges `scanner_engine` (scan_asset happy path + timeouts + quota) et les branches `tick_scanner` / `SettingsProvider.apply` auto-profile dans `index.py`.
4. Ne pas casser le contrat API ni le frontend.
