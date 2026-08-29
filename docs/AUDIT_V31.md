# Audit Quantum Trade Pro v3.1 — P0 REAL : fail-close, réconciliation honnête, sandbox réel

Date : 2026-08-29. Base : `main` avec v3.0 mergée (RR 2.0, news trade, mémoire
hors-ligne, persist START/ARM, cookie session). Ce document est honnête.
**Aucune promesse de win rate 99 %.** Le plancher 84 est un filtre de
sélectivité, pas une probabilité. La bannière « Live execution still
experimental » reste affichée en mode REAL.

## Périmètre v3.1 — P0 REAL uniquement

v3.0 a durci les données, la mémoire et l'auth. v3.1 corrige les six
dettes P0 du chemin d'exécution REAL, sans toucher DEMO, la cascade
crypto, le blocage Yahoo delayed ni le fail-safe calendrier tradfi.

### P0-1 — Fail-close si SL/TP natif échoue

**Avant** : `CCXTAdapter.execute_order` posait le market fill, puis attachait
TP (limit reduceOnly) et SL (stop reduceOnly) en *best-effort*. Si l'attache
échouait, le résultat restait `success=True` avec un simple `sl_tp_warning` —
une position REAL restait ouverte **sans protection** et était persistée OPEN.

**Après** (`api/engines/broker_adapters/ccxt_adapter.py`) :

1. `filled = float(order.get("filled") or quantity)` ; `filled <= 0` ou non
   fini → `INVALID_FILL` (jamais d'attache sur une quantité fantôme).
2. `average = order.get("average") or order.get("price")` ;
   `fees = float((order.get("fee") or {}).get("cost") or 0)` si dict.
3. TP puis SL sont attachés sur **`filled`**, pas sur la quantité demandée.
4. Si TP ou SL lève : **flatten immédiat** —
   `create_order(symbol, 'market', hedge_side, filled, None, {'reduceOnly': True})`.
   - Flatten OK → `success=False`, `reason=SL_TP_ATTACH_FAILED_FLATTENED`,
     `flattened=True`. Rien n'est persisté OPEN.
   - Flatten KO → `success=False`, `reason=SL_TP_ATTACH_FAILED_NAKED`,
     `flattened=False` + log CRITICAL. Le connector persiste OPEN avec
     `metadata.sl_tp_failed=true` pour que l'opérateur et la réconciliation
     gèrent la position nue — mais le résultat reste un échec.
5. Succès protégé → `success=True` + `filled`/`average`/`fees`/
   `tp_order_id`/`sl_order_id`.
6. Nouveau `close_position(symbol, side, quantity)` : hedge market
   reduceOnly, mêmes gardes d'entrée (`BROKER_DISCONNECTED`, `INVALID_SIDE`,
   `INVALID_QUANTITY`), échec broker → `BROKER_CLOSE_ERROR`.

### P0-2 — Réconciliation spot honnête : `[]` n'est pas une preuve de close

**Avant** : `reconcile_positions` traitait `get_positions() == []` comme
« tout est fermé ». Sur un compte **spot** (pas de `fetchPositions`),
`get_positions()` renvoie toujours `[]` → chaque trade REAL OPEN était
fermé en DB à tort (`BROKER_RECONCILED_CLOSE`) alors que la position
existait toujours sur l'exchange.

**Après** (`api/engines/broker_connector.py`) :

- `CCXTAdapter.positions_authoritative` :
  `bool((client.has or {}).get("fetchPositions") or (client.has or {}).get("fetchPosition"))`
  et `client is not None`. Défaut `False` sur `BrokerAdapter`.
- Adapter non authoritatif → **aucune** clôture DB pour ce broker.
- Exception `get_positions()` → broker omis de la réconciliation (inchangé,
  `test_broker_reconciliation_does_not_close_on_provider_failure` reste vert).
- Authoritatif → set des symboles live (split `":"`), symbole absent →
  CLOSE DB `BROKER_RECONCILED_CLOSE`.

### P0-3 — Persister filled + frais réels

**Avant** : la position DB stockait `quantity = risk["quantity"]` (demandé,
pas exécuté) et `fees = 0.0`.

**Après** (`BrokerConnector.execute`) : `quantity = float(res.filled or
risk["quantity"])`, `entry_price = float(res.average or signal.entry or 0)`,
`fees = float(res.fees or 0)`. Metadata : `requested_quantity`,
`broker_order_id`, `tp_order_id`/`sl_order_id`, `sl_tp_warning`.
`SL_TP_ATTACH_FAILED_NAKED` → OPEN persisté avec `sl_tp_failed=true`
(succès reste False). `SL_TP_ATTACH_FAILED_FLATTENED` → rien d'OPEN.
`NO_BROKER_CONNECTED` / `UNSUPPORTED_SYMBOL` inchangés.

### P0-4 — Sandbox CCXT réel

**Avant** : le flag sandbox mutait `os.environ["BROKER_SANDBOX"]` (racy,
process-wide) et n'appelait **jamais** `set_sandbox_mode` — un test
« sandbox » pouvait trader en LIVE.

**Après** : `CCXTAdapter.__init__(..., sandbox: Optional[bool] = None)` en
dernier kwarg. Dans `connect()`, si `self.sandbox` :
`set_sandbox_mode(True)` est appelé **avant** `load_markets()` ; setter
absent ou levée → close + `connect() is False` (fail-close, jamais LIVE par
accident). `BrokerConnector.add_broker(..., sandbox=None)` propage.
`POST /api/brokers` et `POST /api/brokers/test` lisent `body.sandbox` — plus
aucune mutation d'environnement.

### P0-5 — Close unitaire REAL

**Avant** : `POST /api/positions/{market_id}/close` refusait en REAL
(« use Emergency Stop »). Le tick REAL ne pouvait pas fermer une position
dont le SL/TP logiciel était touché.

**Après** : `BrokerConnector.close_position(market_id)` — trouve la trade
REAL OPEN, route vers l'adapter, `adapter.close_position(broker_symbol,
direction, quantity)`. Broker OK → CLOSED en DB (`MANUAL_CLOSE`,
`close_order_id`). Broker KO → `success=False`, **DB inchangée** (jamais de
fake-close). L'endpoint REAL délègue au connector ; DEMO inchangé.
`tick_management` REAL, après réconciliation : prix touche SL/TP →
`await broker_connector.close_position(symbol)` (ordre broker réel, la DB
n'est CLOSED qu'après confirmation).

### P0-6 — execute-signal exige START+ARM

`_execute_signal_for_market` refuse désormais immédiatement si
`not bot_state["is_running"] or not bot_state["armed"]` →
`{"success": False, "reason": "SYSTEM_NOT_ARMED"}`. Le bouton manuel obéit
à la même machine d'état que le scanner. `auto_arm_on_startup` n'arme
toujours pas REAL tout seul.

## P1 — GET sensibles + version + docs

- `FastAPI(version="3.1.0")`.
- `require_admin` sur GET `/api/settings`, `/api/brokers`, `/api/history`,
  `/api/wallets`, `/api/metrics`, `/api/optimization`. Restent publics :
  `/healthz`, `/api/health`, `/api/status`, `/api/scanner`, `/api/markets`,
  `/api/news`. Clé vide (dev/tests) → ouvert, comme avant.
- UI : le bouton Close fonctionne désormais aussi en REAL (le backend route
  vers le broker).
- README v3.1.0, CHANGELOG `[3.1.0]`.

## Protections conservées (non négociables)

- Cascade crypto **non réordonnée** (`provider_priority.py`).
- Auto-exec interdit sur quote non LIVE / Yahoo / DELAYED / STALE.
- Calendrier HS : `news_unavailable_policy=block_tradfi_only`.
- News défaut `trade` (pas de forçage `avoid`).
- RSI only en auto, RR défaut 2.0 clampé 1.0–2.0, plancher 84.
- `require_admin(request: Optional[Request] = None)` (tests l'appellent sans
  Request, FastAPI 0.141).
- Pas d'auto-arm REAL au boot ; bannière REAL experimental conservée.
- Aucun exploit, aucun payload d'attaque, aucun win rate 99 %.

## Tests

`tests/test_v31.py` (mocks, hors réseau) : flatten sans OPEN en DB, NAKED
persisté `sl_tp_failed`, reconcile non-authoritatif vs authoritatif,
filled/fees persistés, sandbox setter/refus, close REAL happy + échec
(DB reste OPEN), `SYSTEM_NOT_ARMED`, version 3.1.0, présence des docs.
Mises à jour : `test_broker_adapter_units.py`, `test_offline_engine_coverage.py`,
`test_api_coverage_hardening.py`, `test_v30.py` (version), `test_v29_rsi.py`
(arm avant exec).
