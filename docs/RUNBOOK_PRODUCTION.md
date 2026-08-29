# RUNBOOK PRODUCTION — Quantum Trade Pro v3.3.0

> **REAL is experimental. A successful testnet campaign is required before
> any real ARM. No profitability guarantee.**

## 1. Prérequis de production

| Variable | Obligatoire | Description |
|---|---|---|
| `APP_ENV` | oui | `production` (fail-fast sinon développement) |
| `ADMIN_API_KEY` | oui | Clé admin ≥ 16 caractères, non placeholder |
| `FERNET_KEY` | oui | Clé Fernet valide (32 octets url-safe base64) |
| `DB_PATH` | non | Défaut `data/quantum_trade.db` (sur le volume persistant) |
| `LOG_FILE` | non | Défaut `data/trading_bot.jsonl` (NDJSON, rotation 5×5 Mo) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | recommandé | Alertes NAKED / ORDER_STATE_UNKNOWN |
| `DISCORD_WEBHOOK_URL` | optionnel | Canal d'alertes secondaire |
| `RATE_LIMIT_PER_MINUTE` / `RATE_LIMIT_MUTATIONS_PER_MINUTE` | non | 1200 / 300 par défaut |

Générer une clé Fernet :

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

En `APP_ENV=production`, l'application **refuse de démarrer** si
`ADMIN_API_KEY` ou `FERNET_KEY` est manquant ou manifestement faible
(contrôle à l'import, avant la première requête).

## 2. Démarrage / arrêt

```bash
# Démarrage (Railway: python3 -m api.index)
python3 -m api.index            # bind 0.0.0.0:PORT

# Arrêt gracieux
kill -TERM <pid>                # les loops asyncio sont annulées, les
                                # clients CCXT/HTTP et la DB sont fermés
```

L'arrêt gracieux : annule les tâches de fond, ferme chaque adapter CCXT
(`await client.close()`), ferme le moteur de données de marché. Un trade REAL
resté OPEN survit au redémarrage (DB persistante) et est de nouveau
rapparié au broker au boot (`initialize_from_db`).

## 3. Healthchecks

| Probe | Port | Sémantique |
|---|---|---|
| `GET /healthz` | 200 | **Liveness** : le processus répond |
| `GET /readyz` | 200/503 | **Readiness** : DB accessible ET configuration production valide |
| WebSocket `/ws` | HEARTBEAT 15 s | Clients prudents : watchdog 90 s → reconnexion |

Railway : `healthcheckPath: /healthz`, volume persistant `quantum-data`
monté sur `/app/data` (DB + logs + rapports testnet).

## 4. Sauvegarde / restauration

```bash
# Backup (WAL checkpoint + copie atomique vérifiée)
python3 scripts/backup_db.py backup --db data/quantum_trade.db
# → data/backups/quantum_trade_YYYYmmdd_HHMMSS.db (+ .sha256)

# Vérification d'une copie
python3 scripts/backup_db.py verify --file data/backups/quantum_trade_....db

# Restauration SUR COPIE (jamais par-dessus la DB vivante en cours)
python3 scripts/backup_db.py restore --file data/backups/....db \
    --to /tmp/restore/quantum_trade.db
```

Recommandation : cron quotidien `backup` + rétention 14 jours ; la copie est
testée par `verify` (intégrité du schéma + lecture d'une ligne).

## 5. Procédures d'incident

### 5.1 Alerte `NAKED` (position sans protection SL/TP)

1. Vérifier l'audit : `SELECT * FROM audit_logs WHERE action IN
   ('REAL_CLOSE_NAKED','PROTECTION_LOST') ORDER BY id DESC;`
2. Vérifier le compte broker (positions + ordres ouverts manuellement).
3. Si le prix est au niveau SL/TP : le backstop logiciel envoie déjà une
   clôture reduce-only chaque tick ; sinon fermer manuellement.
4. Ne JAMAIS ré-armer tant que la position n'est pas résolue.

### 5.2 Alerte `ORDER_STATE_UNKNOWN`

1. L'ordre **n'est jamais retrayé automatiquement** — vérifier sur le
   terminal de l'exchange via le `clientOrderId` (format `QTP-…`).
2. Table `order_intents` : statut `ORDER_STATE_UNKNOWN` + `error`.
3. Si l'ordre a fillé : créer la ligne de position manuellement (idempotent
   grâce au `client_order_id`) ou laisser la réconciliation la retrouver.
4. Si l'ordre n'existe pas : position absente — documenter et clôturer
   l'audit.

### 5.3 Réconciliation en échec prolongé

`RECONCILE_FAILING` est notifié après 10 ticks consécutifs (≈10 s) puis
toutes les 60 s. Vérifier la connectivité broker, `GET /api/brokers`
(`runtime_status`), et les logs `REAL_RECONCILE_FAILED`.

### 5.4 Emergency stop

`POST /api/emergency-stop` : chaque trade REAL OPEN reçoit une clôture
unitaire via son broker ; le DB est fermé uniquement après confirmation.
Résultat par position : `CLOSED_CONFIRMED / FAILED / ORDER_STATE_UNKNOWN /
MANUAL_ACTION_REQUIRED` (audit `EMERGENCY_STOP`). Relire l'audit pour tout
`MANUAL_ACTION_REQUIRED`. Reset : `POST /api/emergency-reset`.

## 6. Observabilité

- Logs NDJSON : `data/trading_bot.jsonl` (champs structurés, secrets
  redacts : `api_key`, `token`, `Bearer …`).
- Métriques : `GET /api/metrics` → `real_safety` :
  `order_state_unknown`, `naked_positions`, `notification_failures`,
  `reconcile.{last,avg,max}_ms`, `broker_latency`, `data_age`.
- Events structurés principaux : `ORDER_EXECUTED`, `REAL_ORDER_OPEN`,
  `REAL_RECONCILE`, `REAL_CLOSE_NAKED`, `ORDER_STATE_UNKNOWN`,
  `PROTECTION_LOST`, `PROTECTION_STATE_UNKNOWN`, `EMERGENCY_STOP`,
  `NO_EXECUTION_DIAGNOSIS`.

## 7. Déploiement (Railway)

1. Branch `main` mergée, suite verte (`bash scripts/validate.sh`).
2. Variables d'environnement §1 dans le service Railway.
3. Volume persistant `quantum-data` → `/app/data` (déjà dans railway.json).
4. Déployer ; vérifier `GET /healthz` puis `GET /readyz` (200).
5. Avant tout ARM REAL : **campagne testnet terminée**
   (docs/TESTNET_MATRIX.md) + revue manuelle de la configuration.

## 8. Ce qui reste extérieur au système

- Les clés d'API broker vivent dans la DB (chiffrées FERNET) ou l'environnement ;
  ne jamais les committer (validate.sh scanne les secrets en dur).
- La disponibilité des providers de données (Yahoo/DELAYED n'est JAMAIS
  auto-exécutée) ; la cascade de providers et les fallbacks sont
  documentés dans docs/data_sources.md.
