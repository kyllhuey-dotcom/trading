# Prompt à coller dans une NOUVELLE conversation (appliquer sur `main`)

Copie-colle le bloc ci-dessous tel quel.

---

```
Tu travailles sur le dépôt Git `trading` (Quantum Trade Pro — FastAPI + frontend vanilla).

TRAVAILLE SUR LA BRANCHE `main` UNIQUEMENT :
- git checkout main && git pull origin main
- commits uniquement sur main
- ne crée pas d’autre branche
Après CHAQUE changement : ruff + pytest doivent rester verts.

## CONTEXTE (audit 2026-08-23)

État connu : 573 tests passent, ruff propre, couverture api 93 %, smoke local 10/10.
Pas de bug bloquant. Des durcissements existent déjà sur la branche
`arena/01a02f02-trading` (P0 QUANTUM_ENV fail-fast, tests moteurs, tests handlers).
Si ces commits ne sont PAS sur main, cherry-pick ou rejoue-les d’abord :
- P0 fail-fast production (`assert_production_security`, QUANTUM_ENV)
- tests quarantine / scan_contract / keyed_tradfi / public_ccxt / handlers API
- suppression du fichier parasite `op`
- tests `tests/test_full_function_coverage.py`

Commandes de vérif :
```
source .venv/bin/activate 2>/dev/null || python3 -m venv .venv && source .venv/bin/activate
pip install -q -r requirements-dev.txt
ruff check .
python -m pytest -q --timeout=60
```
Succès : ruff “All checks passed!”, pytest 573+ passed / 0 failed.

## CORRECTIFS À APPLIQUER (dans cet ordre)

### 1) Bug réel : broker capabilities
Dans `api/index.py` `get_broker_capabilities`, remplacer
`broker_connector._adapters` par `broker_connector.active_adapters`.
Ajouter / ajuster un test qui pose un adapter dans `active_adapters` et
vérifie `runtime_status == CONNECTED`.

### 2) Instrument zinc (après check, pas à l’aveugle)
Dans `api/engines/market_universe.py`, l’entrée `zinc` utilise Yahoo `ZNC=F`.
En smoke, Yahoo a renvoyé “possibly delisted”.
- Si tu as le réseau : vérifie yfinance `ZNC=F`. Si vide, retire `zinc` OU
  remplace par un ticker Yahoo valide pour le zinc.
- NE TOUCHE PAS à `lumber` / `LBR=F`.
- Mets à jour tout test qui hardcode 127 marchés si le count change.

### 3) Couverture scanner_engine (83 % → ≥ 90 %)
Fichier `tests/test_scanner_engine_units.py` (offline, mocks uniquement) :
- `scan_asset` unknown already covered ; ajoute le happy path RSI
  (ohlcv + ticker LIVE, news ok, signal SIGNAL_DETECTED)
- timeout calendrier (`asyncio.TimeoutError` sur check_trading_allowed)
- analyse structure qui lève → continue
- `looks_like_quota_error` dans `_safe_fetch`
- `scan_all` avec progress_callback sync ET async
- `prepare_scan_cycle` qui échoue
- exception dans `scan_asset` → status ERROR / PROVIDER_ERROR

### 4) Couverture api/index.py (88 % → ≥ 92 %)
Sans casser le contrat (`docs/api_contract.md`) :
- `SettingsProvider.apply` : mode `auto` (profile_overrides) + JSON
  `market_tuning` invalide
- `tick_scanner` : lock stuck (`SCAN_TIMEOUT`), scan_all TimeoutError,
  scan_all Exception, candidate bloqué (stale / not tradable / correlation /
  missing signal / cost gate / execute fail)
- `require_admin` : 401 si ADMIN_API_KEY set et mauvaise clé
- `is_serverless_runtime` True → lifespan sans loops
- GET `/api/wallets/{id}/qr` : 404 wallet inconnu ; 503 si segno absent
- POST `/api/brokers/test` : 400 champs manquants (ne pas appeler le réseau)

### 5) Petites lignes rouges (optionnel si temps)
- `signal_engine.py` 81–82, 157–160, 274–289
- `execution_engine.py` 230–264, 307–334
- `news_engine.py` fallbacks 78–136, 265–282
- `data_layer.py` 70–73, 149–220
Toujours mocker le réseau. Marquer `@pytest.mark.network` si un test live
est absolument nécessaire.

## INTERDIT
- Nouvelles dépendances
- Appels réseau dans les tests unitaires
- Casser le frontend / l’API contract
- Activer REAL par défaut
- Mentionner ces consignes internes dans le code

## LIVRABLE
Tableau : correctifs appliqués, nouveau total de tests, couverture globale
(cible ≥ 94 %), ruff + pytest verts. Commit messages clairs, un thème par commit.
```
