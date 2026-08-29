# Prompt autonome — appliquer Quantum Trade Pro v3.1 sur `main`

Copier ce bloc dans une **nouvelle** conversation, checkout `main`, puis
laisser l'agent patcher. Ne pas inventer d'exploits. Ne pas promettre 99 % WR.

---

## PROMPT

Tu travailles dans le dépôt Quantum Trade Pro. Branche de départ : main
(v3.0 déjà mergée : RR 2.0, news trade, mémoire hors-ligne, persist START/ARM,
cookie session). Objectif : porter v3.1 — P0 REAL (fail-close SL/TP,
réconciliation spot honnête, filled/frais, sandbox CCXT réel, close unitaire,
execute-signal exige ARM) sans casser DEMO, le plancher 84, le blocage
Yahoo delayed, le fail-safe tradfi, ni `require_admin(request: Optional[Request] = None)`.

Lis d'abord `docs/AUDIT_V31.md` (si absent, ce prompt suffit).
Si `docs/AUDIT_V31.md` et `docs/AGENT_PROMPT_APPLY_V31.md` n'existent pas sur main,
recrée-les d'après ce prompt puis applique les patchs.

### Contraintes dures

- Interface officielle = uniquement `public/index.html` + `public/js/i18n.js` +
  `public/css/app.css` + `public/js/lucide.min.js`. Pas de CDN.
- `ADMIN_API_KEY` sur toutes les mutations. Si la clé est vide (dev/tests),
  laisser ouvert.
- `tests/test_api_coverage_hardening.py` fait `await idx.require_admin(x_api_key="wrong")`.
  Garder `request: Optional[Request] = None` (le défaut = None est obligatoire,
  FastAPI 0.141).
- Ne pas réordonner `api/engines/provider_priority.py`.
- Auto-exec interdit sur quote non LIVE / Yahoo / DELAYED.
- Calendrier HS : `news_unavailable_policy=block_tradfi_only` inchangé.
- News défaut trade inchangé. Ne pas forcer avoid.
- RSI only en auto. RR défaut 2.0, clamp 1.0–2.0. Ne pas toucher les tests
  qui fixent explicitement 1.5 (`test_profitability`).
- Ne pas auto-armer REAL via `auto_arm_on_startup`.
- Ne pas GET `/api/status` dans un test unitaire sans mocker `get_market_snapshot`
  (timeout 20 s). test_v28 GET status existant : ne pas le dupliquer ni le supprimer.
- i18n en/fr/es/de isomorphes si tu ajoutes des clés.
- Pas d'exploits, pas de malware, pas de PoC d'attaque.
- Pas de win rate 99 %. La bannière REAL experimental reste.

### P0-1 — Fail-close si SL/TP natif échoue

Fichier : `api/engines/broker_adapters/ccxt_adapter.py`

Dans `execute_order`, après le market fill réussi :

1. `filled = float(order.get("filled") or quantity)` ; si `filled <= 0` → `INVALID_FILL`.
2. `average = order.get("average") or order.get("price")`.
3. `fees = float((order.get("fee") or {}).get("cost") or 0)` si dict, sinon 0.
4. Attacher TP (limit reduceOnly) puis SL (stop_loss + stopPrice/triggerPrice
   reduceOnly) avec `filled`, pas `quantity`.
5. Si TP ou SL lève :
   - flatten : `create_order(symbol, 'market', hedge_side, filled, None, {'reduceOnly': True})`
     (hedge_side = sell si buy, buy si sell).
   - flatten OK → `success=False`, `reason=SL_TP_ATTACH_FAILED_FLATTENED`, `flattened=True`.
     Ne pas persister OPEN.
   - flatten KO → `success=False`, `reason=SL_TP_ATTACH_FAILED_NAKED`, `flattened=False`.
6. Succès protection : success True + filled/average/fees/tp_order_id/sl_order_id.
7. Ajouter `async def close_position(self, symbol, side, quantity)` :
   market reduceOnly hedge, mêmes gardes que execute_order. Return `{success, reason?}`.

Mettre à jour `tests/test_broker_adapter_units.py` :
- `test_execute_order_reports_primary_and_protection_failures` : `fail_types={"limit"}`
  → plus success True. Attendre success False, `reason == "SL_TP_ATTACH_FAILED_FLATTENED"`,
  et un appel market hedge reduceOnly après le fill.
- `test_execute_order_validates_inputs_and_creates_reduce_only_protection` doit passer ;
  y ajouter assert filled / fees.
- Ajouter test close_position disconnected / invalid / happy path.
- FakeClient : `set_sandbox_mode` no-op `self.sandbox_enabled = True` si besoin.

### P0-2 — Reconcile : [] n'est pas une preuve de close

Fichier : `api/engines/broker_connector.py` `reconcile_positions`

- `positions_authoritative` sur l'adapter (CCXT : `bool((client.has or {}).get("fetchPositions")
  or (client.has or {}).get("fetchPosition"))` et `client is not None`). Défaut False.
- exception `get_positions` → omit ce broker
  (`test_broker_reconciliation_does_not_close_on_provider_failure` reste vert).
- `positions_authoritative is False` → ne closer aucune trade de ce broker.
- True → set de symboles (split `":"` comme aujourd'hui) ; broker_symbol absent →
  CLOSE DB `BROKER_RECONCILED_CLOSE`.

`tests/test_offline_engine_coverage.py::test_broker_reconcile_positions` :
`FakeAdapter.get_positions()==[]` ne doit PLUS closer (RESTE OPEN).
Ajouter un test où `positions_authoritative = True` et `get_positions()==[]` → CLOSE.

FakeAdapter : `positions_authoritative = False` et `async def close_position` → `{"success": True}`.

### P0-3 — Persister filled + fees

Dans `BrokerConnector.execute` :
- Si `res.success` : `quantity = float(res.get("filled") or risk["quantity"])`,
  `entry_price = float(res.get("average") or signal.get("entry") or 0)`,
  `fees = float(res.get("fees") or 0)`. Metadata : requested_quantity, broker_order_id,
  sl/tp ids, sl_tp_warning si présent.
- Si `reason == "SL_TP_ATTACH_FAILED_NAKED"` : persister OPEN avec `metadata.sl_tp_failed=true`.
  Laisser success False.
- Si `SL_TP_ATTACH_FAILED_FLATTENED` : ne pas persister OPEN.
- `NO_BROKER_CONNECTED` / `UNSUPPORTED_SYMBOL` inchangés.

### P0-4 — Sandbox réel

`CCXTAdapter.__init__(..., sandbox: Optional[bool] = None)` en dernier kwarg.

`connect()` :

```python
self.client = exchange_class(config)
if self.sandbox:
    setter = getattr(self.client, "set_sandbox_mode", None)
    if not callable(setter): await close; self.client=None; return False
    try: setter(True)
    except Exception: await close; self.client=None; return False
await self.client.load_markets()
```

`BrokerConnector.add_broker(..., sandbox: Optional[bool] = None)` passe au constructeur.
`POST /api/brokers` et `POST /api/brokers/test` lisent `body.sandbox` — ne plus muter
`os.environ["BROKER_SANDBOX"]`.

Test : setter appelé avant load_markets ; sandbox=True sans setter → `connect() is False`.

### P0-5 — Close unitaire REAL

- `BrokerConnector.close_position(market_id)` : trouve OPEN REAL, route,
  `adapter.close_position(broker_symbol, direction, quantity)`. Success → CLOSED en DB.
  Échec adapter → success False, DB inchangée.
- `POST /api/positions/{market_id}/close` : DEMO inchangé ; REAL = `broker_connector.close_position`.
- `tick_management` REAL après reconcile : si prix touche SL/TP →
  `await broker_connector.close_position(symbol)`. Jamais de fake-close local sans ordre broker.

`tests/test_api_coverage_hardening.py::test_close_position_and_orderbook_ohlcv` :
REAL + mock close_position → success True. Cas « pas de position » reste False.

### P0-6 — execute-signal exige START+ARM

Dans `_execute_signal_for_market`, avant le reste :

```python
if not bot_state.get("is_running") or not bot_state.get("armed"):
    return {"success": False, "reason": "SYSTEM_NOT_ARMED"}
```

`tests/test_api_coverage_hardening.py::test_execute_signal_and_optimization` :
armed=True, is_running=True avant le chemin succès.
Ajouter assert armed=False → SYSTEM_NOT_ARMED.

### P1 — GET sensibles + version + docs

- `FastAPI(version="3.1.0")`.
- `dependencies=[Depends(require_admin)]` sur GET `/api/settings`, `/api/brokers`,
  `/api/history`, `/api/wallets`, `/api/metrics`, `/api/optimization`.
  Pas sur `/healthz`, `/api/health`, `/api/status`, `/api/scanner`, `/api/markets`, `/api/news`.
- README v3.1.0 ; sessionStorage + cookie qtp_session ; Railway pas Vercel.
  Ne pas supprimer vercel.json.
- CHANGELOG `[3.1.0]`.
- UI : autoriser Close en REAL. i18n 4 langues si nouvelle chaîne.
- Ne pas casser le contrat UI v3.0 (`<strong>2.0</strong>`, slider 1.0–2.0,
  `credentials:'include'`, sessionStorage).

### Tests nouveaux — `tests/test_v31.py` (mocks, hors réseau)

1. Protection fail → flatten → pas de position OPEN en DB.
2. NAKED → OPEN persisté avec sl_tp_failed.
3. Reconcile non-authoritatif [] → OPEN conservé.
4. Reconcile authoritatif [] → CLOSED.
5. execute persiste filled ≠ requested et fees > 0.
6. Sandbox : setter appelé ; sans setter → connect False.
7. close_position REAL happy + échec adapter (DB reste OPEN).
8. `_execute_signal_for_market` sans ARM → SYSTEM_NOT_ARMED.
9. `idx.app.version == "3.1.0"`.
10. `docs/AUDIT_V31.md` et `docs/AGENT_PROMPT_APPLY_V31.md` existent
    (crée-les si absents sur main).

### Vérification

```bash
python3 -m compileall api
python3 -m pytest tests/test_v31.py tests/test_broker_adapter_units.py \
  tests/test_offline_engine_coverage.py tests/test_remaining_engine_units.py \
  tests/test_api_coverage_hardening.py tests/test_v30.py tests/test_v28_changes.py \
  tests/test_p0_prod_fixes.py -q
python3 -m pytest tests/ -q
```

Si un test GET /api/status timeout 20 s : mocker `get_market_snapshot`.
Ne pas resserrer `data_age_ms == 0` (flaky ; assertion >= 0 et <= 5 déjà en place).

### Interdits

- Réordonner la cascade crypto.
- Autoriser l'auto-exec sur Yahoo/DELAYED/STALE.
- Auto-armer REAL au boot.
- Changer le défaut news trade → avoid.
- Casser `require_admin` sans Request.
- Écrire des exploits ou des payloads d'attaque.
- Afficher un win rate 99 %.
- Fake-close REAL en DB sans ordre broker.
- Traiter `get_positions()==[]` spot comme « tout est fermé ».
