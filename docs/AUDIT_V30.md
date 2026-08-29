# Audit Quantum Trade Pro v3.0 — robustesse, données, news, sécurité

Date : 2026-08-29. Code de référence : branche de travail `arena/01a04d6b-trading`.
Ce document est honnête. **Aucune promesse de win rate 99 %.** Le score ≥ 84
est un filtre de sélectivité, pas une probabilité.

## Objectif opérateur (FR)

1. Données de marché fiables (Kraken, OKX, etc.).
2. Mémoire persistée même hors ligne.
3. Positions 24/7, risque 1:2, levier inchangé.
4. Trader 30 min et/ou 1h avant et après les annonces économiques importantes.
5. Continuer jusqu’à désactivation manuelle (START/ARM persistés).
6. Durcir UI/API au plus haut niveau raisonnable **sans** livrer d’exploits.
7. Livrer un audit + un prompt autonome pour appliquer les correctifs sur `main`.

## Ce que v3.0 a déjà câblé

| Sujet | État | Fichiers |
|---|---|---|
| RR RSI défaut **2.0**, clamp 1.0–2.0 | Fait | `constants.py`, `settings_schema.py`, `db_manager.py` (seed + migrate `1.5→2.0`), `rsi_mean_reversion.py` |
| News window `trade` / `avoid` / `ignore` (défaut **trade**) | Fait | `news_engine.py`, `settings_schema.py` |
| Persist quotes + OHLCV 7 jours | Fait | `db_manager.last_*`, `data_layer.attach_persistence` |
| Fallback REST Kraken/OKX (après CCXT) | Fait | `exchange_rest.py`, `kraken_provider.py`, `okx_provider.py` |
| Runtime START/ARM persisté | Fait | `persist_runtime_intent`, `apply_startup_automation` |
| Cookie HttpOnly `qtp_session`, HMAC, lockout, CSP | Fait | `api/security.py`, `require_admin`, `/api/login` |
| Docs/OpenAPI masqués hors `TESTING` | Fait | `FastAPI(docs_url=None)` |
| UI radar 2.0, slider max 2.0, sessionStorage, `credentials:'include'` | Fait | `public/index.html`, `public/js/i18n.js` |
| Mutations toujours `require_admin` | Fait | `api/index.py` |

Protections **conservées** : DEMO vs REAL, Yahoo delayed bloqué pour l’auto-exec
(`allow_delayed_data_trading=false`), calendrier fail-safe tradfi
(`news_unavailable_policy=block_tradfi_only`), plancher 84, sizing à risque
fixe, anti-martingale, corrélation, `ADMIN_API_KEY` sur POST/PUT/DELETE.

## Risques restants (à traiter ensuite, pas des régressions v3.0)

### P0 — données & exécution

1. **Cascade crypto inchangée** : Binance → Bybit → OKX → Kraken → Coinbase → Gate.
   Les REST Kraken/OKX ne s’activent que si **CCXT du même exchange** échoue.
   Si Binance répond avec une quote douteuse, Kraken n’est jamais consulté.
   *Fix* : ne pas réordonner sauf incident mesuré ; plutôt marquer `STALE` /
   `ERROR` plus tôt et laisser le cooldown passer au suivant.
2. **Auto-exec refuse les quotes `STALE`** (`is_quote_realtime` exige `LIVE` +
   âge 30s crypto / 60s tradfi). La mémoire hors-ligne sert le radar/UI, **pas**
   l’exécution. C’est voulu. Ne pas assouplir.
3. **GET `/api/status`, `/api/settings`, `/api/scanner`, `/api/markets` restent
   publics**. Seules les mutations sont gated. Un attaquant réseau local lit
   l’état. *Fix optionnel* : wrapper les GET sensibles dans `require_admin`
   **uniquement** si `ADMIN_API_KEY` est set **et** `TESTING` est false. Les
   tests appellent `await require_admin(x_api_key="wrong")` sans `Request` —
   **garder** `request: Optional[Request] = None`.
4. **`capital_profiles` propose encore RR 2.5 / 3.0**. Le schéma clamp à 2.0
   pour RSI. En `capital_profile_mode=auto`, `SignalEngine.effective_risk_reward`
   reclamp 1.0–2.0. Documenter, ne pas élargir le slider UI.

### P1 — news

5. Mode **`trade`** (défaut) : les High-impact *importants* (CPI, NFP, FOMC,
   GDP, rate decision…) **n’empêchent plus** l’entrée. La fenêtre 30/60 reste
   mesurée (`in_news_window`). Mode **`avoid`** = ancien blocage. Mode
   **`ignore`** = jamais de bloc calendrier. Outage calendrier : CRYPTO ok,
   tradfi bloqué (`block_tradfi_only`).
6. `is_important_event` : High **sans titre** = important (fail-safe
   « trader autour »). High « Building Permits » = non important.
7. Ne **pas** supprimer `avoid`. L’opérateur peut revenir au fail-safe.

### P1 — runtime persist

8. `persist_runtime_state=true` (défaut) + `runtime_intent_saved` après
   start/stop/arm/mode/emergency. Au boot, si saved → restore
   `is_running` / `armed` / `mode`. Sinon `auto_*` (défaut false).
9. REAL restauré n’est **pas** auto-armé par `auto_arm_on_startup`. Un
   `runtime_armed=true` persisté peut rester True en REAL — c’est l’intention
   opérateur. Sans broker, l’exécution échoue `NO_BROKER_CONNECTED`.
10. Le scanner loop tourne toujours (même `is_running=false`) ; l’**exécution**
    exige `armed && is_running`. Persist `is_running` est donc critique.

### P1 — sécurité (baseline, pas un pentest)

11. Cookie `HttpOnly`, `SameSite=Lax`, `Secure` hors TESTING. HMAC-SHA256,
    `hmac.compare_digest`, lockout 8 échecs / 300s, CSP restrictive,
    `X-Frame-Options: DENY`, HSTS si HTTPS.
12. **Limites assumées** : pas de SSO, pas de WAF, AuthGuard in-memory
    (reset au restart, pas partagé multi-process), GET publics, WS auth
    skip si `TESTING=true`. Ne pas écrire de PoC d’exploit.
13. UI : clé admin dans **sessionStorage** (plus localStorage). Cookie de
    session via `POST /api/login` + `credentials:'include'`. Migration
    one-shot localStorage → sessionStorage.
14. OpenAPI/docs **off** sauf `TESTING=true`.

### P2 — tests / docs

15. Les tests structure (`test_profitability.test_risk_reward_setting_changes_take_profit`)
    qui **fixent explicitement** RR 1.5 doivent rester à 1.5.
16. `test_offline_engine_coverage.test_news_engine_check_trading_allowed`
    doit appeler `set_window_mode("avoid")` avant d’attendre un blocage.
17. i18n : 4 langues, mêmes clés (`newsWindow*`, `persistRuntime*`).

## Procédure de vérif

```bash
python3 -m compileall api
python3 -m pytest tests/test_v30.py tests/test_v29_rsi.py tests/test_rsi_strategy.py \
  tests/test_scanner_reliability.py tests/test_offline_engine_coverage.py \
  tests/test_p0_rsi15_prod.py -q
```

Critères de santé inchangés : win rate ≥ 45 %, RR net ≥ 1.5, espérance > 0,
profit factor ≥ 1.3. **Pas de 99 %.**
