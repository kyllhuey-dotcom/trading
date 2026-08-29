# TESTNET MATRIX — v3.3.0

> **REAL is experimental. A successful testnet campaign is required before
> any real ARM. No profitability guarantee.**

## Statut honnête (v3.3.2 — 2026-08-29)

**La campagne testnet externe n'a PAS été exécutée** et son PASS n'a PAS été
obtenu : le sandbox Arena n'a ni clés testnet ni sortie réseau vers les
hostnames testnet (HTTP 000 sur les 4 endpoints de pré-vol). Simuler le
résultat serait déshonnête — **REAL reste donc expérimental.**

Deux chemins pour obtenir le PASS (les clés doivent être les vôtres, créées
par vous sur les portaux officiels, read + trading uniquement, jamais de
withdrawal) :

1. **GitHub Actions (recommandé)** : le workflow
   `testnet-campaign.yml` (livré prêt dans `docs/ci/workflows/`, à déposer
   dans `.github/workflows/` — voir `docs/CI_SETUP.md` ; manuel uniquement,
   jamais automatique) lit les clés dans les **secrets du repo** (`BINANCE_API_KEY`
   / `BINANCE_API_SECRET`, `BYBIT_API_KEY` / `BYBIT_API_SECRET`,
   `OKX_API_KEY` / `OKX_API_SECRET` / `OKX_API_PASSPHRASE`, `GATE_API_KEY` /
   `GATE_API_SECRET`), lance `scripts/testnet_broker_matrix.py` avec
   `CONFIRM_TESTNET=true` et publie le rapport scrubé en artefact.
   Actions → *Testnet campaign* → *Run workflow*.
2. **Machine avec sortie réseau** : `docs/TESTNET_KEYS_GUIDE.md` (pré-vol
   curl, export des variables, commande exacte).

Tout le reste est en place et testé hors-ligne :
- mocks contractuels complets des 4 exchanges (`tests/exchange_matrix.py`) ;
- script de campagne opt-in `scripts/testnet_broker_matrix.py` ;
- workflow CI dédié (opt-in manuel) ;
- matrice de couverture par contrat (ci-dessous).

## Exigence avant ARM REAL

La campagne doit passer sur les 4 exchanges (ou au minimum sur l'exchange
retenu) avec le statut `PASS` du rapport JSON.

## Lancer la campagne

```bash
# 1. Créer des clés TESTNET (sandbox) chez chaque exchange, sans permission
#    de withdrawal.
# 2. Définir les variables d'environnement (jamais dans un fichier commité) :
export CONFIRM_TESTNET=true
export BINANCE_API_KEY=...  BINANCE_API_SECRET=...
export BYBIT_API_KEY=...    BYBIT_API_SECRET=...
export OKX_API_KEY=...      OKX_API_SECRET=...  OKX_API_PASSPHRASE=...
export GATE_API_KEY=...     GATE_API_SECRET=...

# 3. Lancer (opt-in sans CONFIRM_TESTNET=true, le script refuse) :
python3 scripts/testnet_broker_matrix.py
# → rapport horodaté data/testnet_matrix_YYYYmmdd_HHMMSS.json (secrets épurés)
```

## Garanties du script (non-négociables)

| Règle | Implémentation |
|---|---|
| Opt-in | `CONFIRM_TESTNET=true` obligatoire, sinon exit 2 |
| Sandbox only | `sandbox=True` imposé au constructeur ; si l'adapter n'est pas confirmé en sandbox → ABORT |
| Jamais live par fallback | aucune branche de code vers la live ; tout échec de sandbox = arrêt |
| Credentials environnement uniquement | `os.getenv` uniquement ; jamais de DB/fichier |
| Taille minimale | notional minimal du catalogue (~10 USD) ou `min` exchange |
| Nettoyage fin de test | annulation de tous les ordres ouverts + clôture reduce-only de toutes les positions |
| Épuisement des secrets | rapport JSON passé par `_scrub` (masquage des champs et des valeurs sensibles) |
| Rapport horodaté | `data/testnet_matrix_YYYYmmdd_HHMMSS.json` |

## Matrice de contrat testée (mocks, offline)

Pour chaque exchange (Binance, Bybit, OKX, Gate) :

| Contrat | Test |
|---|---|
| create order | `test_create_order_with_client_order_id` |
| clientOrderId (uniqueness + lookup) | idem + `test_adapter_full_flow_per_exchange` |
| fetch order (id exchange + clientOrderId) | `test_adapter_full_flow_per_exchange` |
| open orders / closed orders / trades | `test_create_order_with_client_order_id` |
| stop — mapping exact | `test_adapter_stop_obeys_exchange_contract` + `test_stop_contract_rejected_when_misused` |
| reduceOnly sur les protections | idem (asserté sur chaque appel stop) |
| cancel (ouvert → fermé, doublon refusé) | `test_cancel_open_and_closed_orders` |
| fill complet | `test_full_fill_fees` |
| fill partiel (ordre reste open) | `test_partial_fill_stays_open` |
| frais | `test_full_fill_fees` |
| timeout | `test_timeout_contract` + `test_adapter_timeout_is_order_state_unknown` |
| rejected / canceled / expired | `test_status_contract_canceled_expired_rejected` |
| précision prix/quantité (lot/tick/min notional) | `test_precision_contract` |

Mapping de stop (contractuel, asserté dans `tests/test_v32.py` §8-11) :

```
binance: ("STOP_MARKET", {"stopPrice": sl, "reduceOnly": True})
bybit:   ("market", {"triggerPrice": sl, "reduceOnly": True,
                     "triggerDirection": 2 if hedge_side == "sell" else 1})
okx:     ("market", {"stopLossPrice": sl, "reduceOnly": True})
default: ("stop_loss", {"stopPrice": sl, "triggerPrice": sl, "reduceOnly": True})
```

## Checklist de validation de la campagne

- [ ] `overall_status: "PASS"` dans le rapport JSON ;
- [ ] chaque exchange : `connected: true`, `sandbox: true`, zéro `errors` ;
- [ ] `fetch_order_status` ∈ {open, closed} cohérente avec le fill ;
- [ ] stop créée puis annulée au cleanup ;
- [ ] aucun ordre/position résiduel après le run ;
- [ ] le rapport ne contient aucune valeur de secret (masquage vérifié).
