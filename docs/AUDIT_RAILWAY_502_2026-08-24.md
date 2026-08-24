# Audit Railway 502 — 2026-08-24

## Contexte

- Service : `trading-production-1027.up.railway.app` — 502 sur toutes les routes.
- Plan Railway ~512 Mo, healthcheck `GET /healthz`, timeout 30 s, `restartPolicyType=ON_FAILURE`, `maxRetries=10`.
- Après 10 crashs le replica reste **ARRÊTÉ** → 502 persistant.
- `main` @ `8d5f916` (PR #19 + #20). En local : uvicorn démarre, `/healthz` 200 en ~10 s. La panne est spécifique à Railway.

## Logs joints (uploads/)

| Fichier | Date | Nature |
|---|---|---|
| `logs.1787252573666.csv` | 2026-08-20 | Build railpack (autre service) |
| `logs.1787254002545.csv` | 2026-08-20 | Build railpack |
| `logs.1787269907795.csv` | 2026-08-20 23:49–23:51 | **Runtime** — crash import |
| `logs.1787329823380.csv` | 2026-08-21 16:27 | **Build** nixpacks — pip conflict |

### Preuves historiques (C — traceback boot, déjà corrigé sur main)

`uploads/logs.1787269907795.csv` (réplica `d9785248`, déploiement `704cae93`) :

```
Starting Container … 2026-08-20T23:49:11
Traceback (most recent call last):
  File "/app/.venv/bin/uvicorn" …
  File "/app/api/index.py", line 7, in <module>
    from api.engines.news_engine import NewsEngine
  File "/app/api/engines/news_engine.py", line 1, in <module>
    import httpx
ModuleNotFoundError: No module named 'httpx'
```

`uploads/logs.1787329823380.csv` :

```
ERROR: Cannot install -r requirements.txt (line 1) and cryptography==44.0.1
  because these package versions have conflicting dependencies.
Build Failed: … pip install -r requirements.txt … exit code: 1
```

Ces deux causes (module manquant / conflit pip) datent du **20–21 août**. Elles ne décrivent pas l’état post-merge #19/#20 (code local OK).

### Cause racine retenue pour le 502 actuel : **(A) OOM + (B) healthcheck 30 s**

Aucun log runtime post-`8d5f916` n’est joint (pas de ligne `Killed` / `exit status 137` / `healthcheck failed` datée du 24). Combinaison retenue :

1. **(A) OOM au boot-scan** — hypothèse principale cohérente avec 512 Mo :
   - univers entier (~130+ CRYPTO/FX/FUTURES/INDICES/ETF) scanné dès le boot (`scanner_loop` → `tick_scanner(force=True)`);
   - `ScannerEngine(max_concurrent=8)` + `yf.download(..., threads=True)` dans `yahoo_provider.py`;
   - pic mémoire = univers × fetchs parallèles × threads yfinance.
2. **(B) Healthcheck 30 s** — si le process est OOM-kill ou saturé avant `/healthz`, Railway tue le replica ; 10 restarts → service arrêté → 502.

Pas de traceback Python actuel au boot (C) sur `8d5f916`.

## Correctifs appliqués

### A — Bornage mémoire (P0)

1. `yahoo_provider.py` : `yf.download(..., threads=False, group_by="ticker")`.
2. `api/index.py` : `ScannerEngine(..., max_concurrent=int(os.getenv("SCAN_MAX_CONCURRENT", "4")))`.
3. `scanner_engine.scan_all` : lots de `SCAN_BATCH_SIZE=30` + `asyncio.gather` + `gc.collect()` entre lots ; `progress_callback` toujours par symbole.
4. `scanner_loop` : lecture `VmRSS` `/proc/self/status` ; si > 420 Mo → log `MEMORY_PRESSURE`, sleep 60 s, pas de crash.
5. `railway.json` : `healthcheckTimeout` 30 → **60**.

### B — Durcissement boot (P1)

6. `lifespan` : try/except autour du boot (STARTING → création des tâches) ; traceback loggé, `tasks=[]`, API reste servie / ré-armable via `POST /api/start`.
7. Log `BOOT_READY duration_ms=…` ; `/healthz` expose aussi `"scanning"`.
8. `scanner_loop` log `SCAN_CYCLE_START rss_mb=…` à chaque cycle.

### C

Pas de nouveau traceback à corriger. `httpx` est déjà dans `requirements.txt`.

## Invariants non touchés

RSI-14 only, RR 1.5 clamp 1.0–2.0, floor 84, LIVE/DELAYED sur timestamp, pas de 99 % winrate, `scanner_loop` unique, `tick_capital` sans `emergency_stop_logic` (RISK_PAUSE), `SCAN_LOCK_STALE_S=180`, `SCAN_ALL_TIMEOUT_S=600`.
