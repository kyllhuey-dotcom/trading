# Prompt autonome — appliquer Quantum Trade Pro v3.0 sur `main`

Copier ce bloc dans une **nouvelle** conversation, checkout `main`, puis
laisser l’agent patcher. Ne pas inventer d’exploits. Ne pas promettre 99 % WR.

---

## PROMPT

Tu travailles dans le dépôt Quantum Trade Pro. Branche de départ : `main`.
Objectif : porter **v3.0** (données fiables, mémoire hors-ligne, RR 1:2,
fenêtre news tradable, persist START/ARM, durcissement auth) **sans casser**
DEMO vs REAL, le plancher 84, le blocage Yahoo delayed, le fail-safe tradfi,
ni `require_admin(request: Optional[Request]=None)`.

### Contraintes dures

- Interface officielle = uniquement `public/index.html` + `public/js/i18n.js` +
  `public/css/app.css` + `public/js/lucide.min.js`. Pas de CDN.
- `ADMIN_API_KEY` sur **toutes** les mutations. Si la clé est vide (dev/tests),
  laisser ouvert. Production doit setter la clé.
- `tests/test_api_coverage_hardening.py` fait `await idx.require_admin(x_api_key="wrong")`.
  **Garder** `request: Optional[Request] = None`.
- Ne **pas** réordonner `provider_priority.py` (binance→bybit→okx→kraken→coinbase→gate).
- Auto-exec **interdit** sur quote non `LIVE` / Yahoo / DELAYED
  (`allow_delayed_data_trading=false` par défaut).
- Calendrier HS : `news_unavailable_policy=block_tradfi_only` (CRYPTO ok, tradfi bloqué).
- RSI only en auto. RR défaut **2.0**, clamp **1.0–2.0**. Ne pas toucher les
  tests qui **fixent explicitement** 1.5 (`test_profitability`).
- i18n en/fr/es/de **isomorphes**.
- Pas d’exploits, pas de malware, pas de PoC d’attaque.

### Patchs à appliquer (checklist)

1. **`api/engines/constants.py`** : `DEFAULT_RSI_RISK_REWARD = 2.0`,
   `RSI_RISK_REWARD_BOUNDS = (1.0, 2.0)`.
2. **`api/engines/settings_schema.py`** : `risk_reward_ratio` default `"2.0"`
   max 2.0 ; ajouter
   `news_window_mode` enum avoid|trade|ignore default `trade`,
   `news_window_before_mins` 30–60 default 30,
   `news_window_after_mins` 30–60 default 60,
   `persist_runtime_state` true,
   `runtime_intent_saved` / `runtime_is_running` / `runtime_armed` false,
   `runtime_mode` DEMO.
3. **`api/engines/db_manager.py`** :
   - seed `risk_reward_ratio="2.0"` ;
   - migrate `UPDATE … value='2.0' WHERE key='risk_reward_ratio' AND value='1.5'` ;
   - `CREATE TABLE IF NOT EXISTS last_quotes (market_id TEXT PRIMARY KEY, saved_at REAL, payload_json TEXT)` ;
   - `CREATE TABLE IF NOT EXISTS last_ohlcv (market_id, timeframe, saved_at, payload_json, PRIMARY KEY(market_id, timeframe))` ;
   - méthodes `save_last_quote` / `load_last_quote` / `save_last_ohlcv` / `load_last_ohlcv` (TTL 7j).
4. **`api/engines/news_engine.py`** :
   - `is_important_event` (High + regex CPI/NFP/FOMC/GDP/rate/fed/ecb… ; High sans titre = important) ;
   - `set_window_mode` ; défaut `"trade"` ;
   - `avoid` bloque la fenêtre 30m/60m ; `trade`/`ignore` → `news_ok=True` ;
   - conserver outage `block_tradfi_only`.
5. **`api/engines/data_layer.py`** : `attach_persistence(db)` ; persist quote
   live ; restore `status=STALE` + `source += " (cached)"` si tous les
   providers ratent ; persist OHLCV success ; load OHLCV après miss.
   **No-op si `db_manager` absent** (tests cooldown-empty).
6. **`api/index.py`** : `data_engine.layer.attach_persistence(db_manager)` ;
   FastAPI `version="3.0.0"`, docs/openapi **None** sauf `TESTING` ;
   `/api/login` cookie HttpOnly SameSite=Lax Secure=not TESTING ;
   `/api/logout` ; `require_admin` timing-safe + cookie + lockout ;
   WS auth si `ADMIN_API_KEY and not TESTING` ;
   `persist_runtime_intent()` après start/stop/arm/mode/emergency ;
   `apply_startup_automation` restore si `persist_runtime_state && runtime_intent_saved` ;
   status/health exposent `news_window_mode` et `risk_reward_rsi=2.0`.
7. **Providers** : `exchange_rest.py` + `KrakenProvider`/`OKXProvider` CCXT puis REST.
   `data_engine.py` instancie déjà `crypto_okx` / `crypto_kraken`.
8. **UI** `public/index.html` : radar `<strong>2.0</strong>` ; slider
   `min=1.0 max=2.0` ; `sessionStorage` pour `qtp-api-key` (migrer depuis
   localStorage) ; `credentials:'include'` ; `POST /api/login` ;
   carte settings news window + persist runtime.
9. **i18n** : clés `newsWindowMode`, `newsWindowHelp`, `newsWindowTrade`,
   `newsWindowAvoid`, `newsWindowIgnore`, `persistRuntime`,
   `persistRuntimeHelp`, `sessionLogin` dans en/fr/es/de.
10. **Tests** :
    - `test_v29_rsi.py` / `test_rsi_strategy.py` / `test_scanner_reliability.py`
      / `test_optional_engine_reds.py` : 1.5 → 2.0 **sauf** tests structure
      qui passent 1.5 en argument.
    - `test_offline_engine_coverage.py` : `engine.set_window_mode("avoid")`
      avant d’assert `trading_allowed is False`.
    - Nouveau `tests/test_v30.py` (RR, news trade/avoid, last_* tables,
      STALE restore, runtime persist, session HMAC, login cookie, parsers
      Kraken/OKX, contrat UI).
11. **Docs** : `CHANGELOG.md` `[3.0.0]`, `.env.example` (`ADMIN_API_KEY`,
    `SESSION_TTL_S`, `AUTH_MAX_FAILURES`, news_window_mode, persist_runtime_state),
    `docs/AUDIT_V30.md`, ce fichier.

### Vérification

```bash
python3 -m compileall api
python3 -m pytest tests/test_v30.py tests/test_v29_rsi.py tests/test_rsi_strategy.py \
  tests/test_scanner_reliability.py tests/test_offline_engine_coverage.py \
  tests/test_p0_rsi15_prod.py tests/test_api_coverage_hardening.py -q
```

Si un test GET `/api/status` timeout 20s : mocker `get_market_snapshot` ou
n’assert que `app.version` / `news_engine.news_window_mode`.

### Interdits

- Réordonner la cascade crypto.
- Autoriser l’auto-exec sur Yahoo/DELAYED/STALE.
- Auto-armer REAL via `auto_arm_on_startup`.
- Casser `require_admin` sans Request.
- Écrire des exploits ou des payloads d’attaque.
- Afficher un win rate 99 %.
