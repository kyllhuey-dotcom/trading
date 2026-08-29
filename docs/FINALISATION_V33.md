# FINALISATION v3.3.1 — 2026-08-29

## Ce qui est terminé et validé (100 % local)

| Vérification | Résultat exact |
|---|---|
| Suite complète (`tests/ test_lot2_data.py`) | **792 passed, 6 skipped** (skips = réseau externe uniquement) |
| Couverture branche `--cov=api --cov=scripts --cov-branch` | **85,79 %** (seuil 85 respecté, aucun skip ajouté, aucune assertion retirée) |
| `ruff check .` | **All checks passed** |
| `compileall` (api, scripts, tests, test_lot2_data) | **OK** |
| `scripts/validate.sh` (pipeline complet) | **VALIDATION SUCCESSFUL — Ready for deployment** (50 routes) |
| Smoke serveur live | boot OK, `x-correlation-id` écho/assainissement OK, UI servie (89 occurrences `notranslate`), banner présent |

## Fix « Google Translate » (flicker / valeurs corrompues)

Diagnostic : l'app réécrit les zones dynamiques toutes les 2 s
(`innerText`/`innerHTML`) pendant que Google Translate maintient son propre
mapping nœud↔traduction sur les MÊMES nœuds texte → conflit de DOM :
flicker, textes/valeurs corrompus, contenu qui saute.

Correctif appliqué (contrainte respectée : uniquement les 4 fichiers
`public/`, aucun CDN, parité i18n fr/en/es/de intacte — 284 clés × 4) :

1. **`class="notranslate"`** (clé d'exclusion respectée par Google Translate)
   sur TOUTES les zones de données dynamiques : prix, PnL, scores,
   timestamps, IDs, statuts, nombres formatés — 70 éléments statiques +
   les 9 options de filtre marché (leur comparaison `innerText` cassait sous
   traduction).
2. **Conteneurs réécrits en `innerHTML`** (lignes scanner/hub/historique/
   positions/audit/news/brokers…) portent `notranslate` : leurs enfants sont
   couverts par la zone, donc Google Translate n'y touche jamais.
3. **Réécritures `className`** (badges de statut, PnL, trend, arm label…)
   réinjectent toutes `notranslate` — sinon la classe sautait au premier
   poll. Un test de régression le vérifie pour CHAQUE assignation.
4. **Rendus = réécritures complètes** (`innerHTML =`/`textContent =`,
   jamais `+=` ni `insertAdjacentHTML`) : un DOM muté par le traducteur est
   remplacé, jamais fusionné.
5. **`document.documentElement.lang`** posé au chargement et à chaque
   changement de langue (via `applyLanguage`) — évite la double traduction.
6. **Re-render i18n sans chevauchement** : aucun élément `data-i18n` n'est
   `notranslate` (et inversement) — les traductions UI et les zones de
   données vivent sur des nœuds disjoints.
7. Tests de régression : `tests/test_ui_notranslate.py` (10 tests statiques
   sur le contrat DOM/JS exact).

## Campagne testnet — LA SEULE ÉTAPE MANQUANTE

Sortie réseau du sandbox : **HTTP 000** sur les 4 endpoints
(testnet.binance.vision, api-testnet.bybit.com, okx.com, api.gateio.ws).
Campagne **non exécutée** — aucun résultat simulé.

À exécuter depuis une machine avec internet, avec TES clés testnet (créées
par toi sur les portails officiels listés dans `docs/TESTNET_KEYS_GUIDE.md`,
permissions trading uniquement, JAMAIS withdrawal) :

```bash
export CONFIRM_TESTNET=true
export BINANCE_API_KEY=...  BINANCE_API_SECRET=...
export BYBIT_API_KEY=...    BYBIT_API_SECRET=...
export OKX_API_KEY=...      OKX_API_SECRET=...  OKX_API_PASSPHRASE=...
export GATE_API_KEY=...     GATE_API_SECRET=...

# pré-vol réseau (doit répondre HTTP 200 partout) :
curl -m 8 https://testnet.binance.vision/api/v3/ping
curl -m 8 https://api-testnet.bybit.com/v5/market/time
curl -m 8 https://www.okx.com/api/v5/public/time
curl -m 8 https://api.gateio.ws/api/v4/spot/time

python3 scripts/testnet_broker_matrix.py
# → data/testnet_matrix_YYYYmmdd_HHMMSS.json (secrets masqués)
# succès = "overall_status": "PASS", chaque exchange connected+sandbox+0 erreur
```

## REAL — statut

**REAL reste expérimental.** La convention du repo (TESTNET_MATRIX.md) impose
un PASS testnet documenté avant toute sortie d'expérimental. Le rapport JSON
PASS (extrait, secrets masqués) devra être consigné dans cette section.
