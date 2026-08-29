# GUIDE CLÉS TESTNET — Quantum Trade Pro v3.3

> Les clés viennent UNIQUEMENT de TES comptes testnet officiels, créés par toi.
> Jamais de clés tierces. Jamais de permission withdrawal. Rien dans Git.

## 1. Créer les clés (portails officiels)

| Exchange | Portail testnet officiel | Notes |
|---|---|---|
| Binance | https://testnet.binance.vision | Login GitHub/HK account → Generate HMAC Keys. **Spot testnet**. |
| Bybit | https://testnet.bybit.com | Compte testnet → API Management → Create New Key. **API v5**. |
| OKX | https://www.okx.com/docs-v5/en/#overview-demo-trading-services | Compte OKX → Démo Trading → API (clés démo dédiées, `x-simulated-trading: 1`). Passphrase OBLIGATOIRE. |
| Gate.io | https://www.gate.io/testnet | Compte testnet → API Keys (spot). |

Règles de création (non négociables) :
- **Permissions : lecture + spot/futures trading UNIQUEMENT. JAMAIS withdrawal.**
- IP whitelist si le portail le propose (sinon note l'IP publique de la machine qui lance la campagne).
- Note la passphrase OKX au moment de la création (elle ne se raffiche pas).

## 2. Définir les variables d'environnement (jamais dans un fichier commité)

```bash
export CONFIRM_TESTNET=true
export BINANCE_API_KEY=...  BINANCE_API_SECRET=...
export BYBIT_API_KEY=...    BYBIT_API_SECRET=...
export OKX_API_KEY=...      OKX_API_SECRET=...  OKX_API_PASSPHRASE=...
export GATE_API_KEY=...     GATE_API_SECRET=...
```

## 3. Pré-vol réseau (sans clés)

```bash
curl -m 8 https://testnet.binance.vision/api/v3/ping
curl -m 8 https://api-testnet.bybit.com/v5/market/time
curl -m 8 https://www.okx.com/api/v5/public/time
curl -m 8 https://api.gateio.ws/api/v4/spot/time
```

Chaque endpoint doit répondre HTTP 200. Un `HTTP 000` = pas de sortie réseau :
inutile de lancer la campagne avant d'avoir un accès sortant.

## 4. Lancer la campagne

```bash
# Depuis la racine du repo (l'import standalone est géré par le script) :
python3 scripts/testnet_broker_matrix.py
# → rapport horodaté data/testnet_matrix_YYYYmmdd_HHMMSS.json (secrets masqués)
```

- Sans `CONFIRM_TESTNET=true` le script refuse (exit 2).
- Sandbox only, jamais de fallback live ; échec de connexion sandbox = ABORT.
- Sous-ensemble possible : `--exchanges binance,bybit`.

## 5. En cas d'échec — causes réelles à vérifier

| Symptôme | Cause probable | Action |
|---|---|---|
| `-2015` Binance | IP non whitelisted / clé invalide | Recréer la clé avec la bonne IP |
| OKX `50102` / passphrase | Passphrase démo incorrecte | Régénérer la clé démo |
| `Permission denied` / `403` | Permission trading non cochée | Recréer la clé (trading, pas withdrawal) |
| Timeout / `HTTP 000` | Sortie réseau bloquée | Machine avec accès internet sortant |
| Solde insuffisant | Faucet testnet non alimenté | Binance : « Get Assets » sur le portal ; Bybit/OKX/Gate : faucets dédiés |

## 6. Validation

Le rapport JSON doit afficher `"overall_status": "PASS"`, chaque exchange
`connected: true`, `sandbox: true`, zéro `errors` (checklist complète :
docs/TESTNET_MATRIX.md). Ce PASS est la précondition documentée avant de sortir
REAL du statut expérimental.
