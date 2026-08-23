# Audit de rentabilité & optimisation — Quantum Trade Pro

> **Périmètre de cet audit** : le dépôt contient **aucune base de trades** (les
> `uploads/*.csv` sont des logs serveur Railway, pas des trades). L'audit porte
> donc sur **le code, les stratégies, la gestion de risque et la configuration**.
> Dès qu'une base `quantum_trade.db` (ou un export) sera fournie, lancer
> `python3 scripts/profit_audit.py <db> <balance>` pour obtenir les statistiques
> réelles + les recommandations d'optimisation.

## Addendum v2.6 — qualité des entrées avant optimisation

L'audit production du 22 août 2026 a corrigé les biais opérationnels qui
empêchaient d'obtenir un échantillon exploitable : calendrier sans SPOF, scan
immédiat, sources crypto redondantes, Yahoo batché, sous-jacents dédupliqués et
état LIVE/DIFFÉRÉ explicite. Ces changements améliorent la **disponibilité** et
la qualité des observations ; ils ne changent ni le sizing à risque fixe, ni
l'anti-martingale, ni les profils de capital, ni le tuning par marché. Les
trades différés et les news bloquantes restent refusés par défaut.

Ce document applique la méthodologie demandée au bot **Quantum Trade Pro** :

1. Audit des performances (données historiques).
2. Évaluation des stratégies **et des marchés**.
3. Analyse des risques.
4. Optimisation des paramètres (adaptée au capital 1 $ → 50 $+).

---

## 1. Analyse des données historiques

### Ce qui est en place
- **`/api/history`** et **`/api/performance`** exposent le journal des trades
  fermés et les stats (win rate, espérance, profit factor) par mode et stratégie.
- **`scripts/profit_audit.py`** agrège toutes les positions `CLOSED` et produit :
  - win rate, PnL net, avg win / avg loss, RR réalisé, espérance par trade ;
  - la détection des **fuites de coûts** (trades dont les frais aller-retour
    ≈ 0,2 % dépassent 50 % du risque — mathématiquement perdants) ;
  - un verdict `PROFITABLE / LOSING / BREAKEVEN` par stratégie.
- Le risque global (drawdown, perte journalière) est **persistant** dans les
  réglages (`peak_balance`), donc il survit aux redéploiements.

### Constat honnête
Sans base de trades, **aucune statistique réelle ne peut être calculée**. Le
premier geste à faire est d'exporter la base du volume Railway
(`/app/data/quantum_trade.db`) puis :

```bash
python3 scripts/profit_audit.py /chemin/quantum_trade.db 1000
```

En attendant, l'audit ci-dessous est **statique** (lecture du code/config).

---

## 2. Évaluation des stratégies

Le bot dispose de 4 stratégies de signal (scoring 0–100) :

| Stratégie | Logique | Point faible observé |
|---|---|---|
| **structure** | BOS/CHoCH, HH/HL/LH/LL, alignement LTF/HTF, momentum, volume | Le plus **dépendant du réglage** `min_signal_score` ; c'est la fuite de sélectivité corrigée en LOT P |
| **arbitrage** | Écart de prix inter-plateformes (Gate/Bybit/Binance) avec score de confiance | Dépend de la **fraîcheur/sync** des quotes ; filtre de coûts non appliqué par défaut |
| **tape** | Imbalance pondérée profondeur + delta + vitesse, seuil piloté par ATR | Filtre de coûts appliqué |
| **liquidity** | Gap de liquidité + confirmation « côté mince » + stop logique | Filtre de coûts **non** appliqué par défaut |

### Critère d'évaluation proposé (réaliste)
Un seul indicateur ne suffit pas. On juge une stratégie sur :

- **Win rate** : cible ≥ 45 % (un taux de 99 % n'est **pas atteignable** en réel —
  voir « cibles réalistes »).
- **RR réalisé** : ≥ 1,5 (sinon les sorties coupent les gains — trailing trop
  serré / TP trop tôt).
- **Espérance** : ≥ +0,5R par trade.
- **Profit factor** : ≥ 1,3.
- **Fuites de coûts** : 0.

---

## 3. Analyse des risques

### Protections déjà en place (LOT P + refonte v2.0)
- Sizing **risque fixe** (% du solde / distance au SL), plafond de levier.
- Validation du **sens du SL** (BUY → SL < entry, SELL → SL > entry).
- Limite de **perte journalière** appliquée au niveau de l'ordre.
- **Cool-down** après une perte.
- **Max positions** + **filtre de corrélation** (pas de ré-entrée sur le même
  sous-jacent).
- **Drawdown global persistant** (vérifié au tick capital).
- **Circuit breaker** de pertes consécutives (auto-pause) + **anti-martingale**
  (le risque *diminue* après perte : 100 % → 75 % → 50 %).
- **Time stop** (sortie des positions qui trainent).
- **Filtre coûts/volatilité** (bloquer les trades dont les frais mangent l'edge).
- Restriction **news/session** toujours appliquée (jamais de trade pendant une
  news à fort impact).
- Garde **anti-scalping sur données différées** (Yahoo bloqué par défaut).

### Limite identifiée (bloquante pour le petit capital) — **résolue**
Le moteur de risque avait un **plancher codé en dur à 10 $** :
`notional_ok = notional >= 10.0` et `min_account_balance = 10.0`. Cela rendait
**impossible** de trader avec un compte de 1 $ à 10 $.

**Correctif appliqué** (`api/engines/risk_engine.py` + `settings_schema.py`) :
- `min_account_balance` (défaut **1.0 $**) et `min_trade_notional` (défaut
  **1.0 $**) sont désormais **configurables** via `/api/settings`.
- Le plancher de notional n'est plus codé en dur : `notional >= min_trade_notional`.
- **Note honnête** : en mode REAL, la **contrainte de min_notional de
  l'exchange** (`exchange_constraints`) reste le vrai plafond — un ordre de
  quelques dollars peut être rejeté par le broker. En mode DEMO (papier) le
  petit capital fonctionne désormais pleinement.

---

## 4. Optimisation des paramètres (par tranche de capital)

### Nouveau module `api/engines/capital_profiles.py`
Un capital de 1 $ et un capital de 10 000 $ **ne doivent pas** utiliser les
mêmes paramètres. Le module définit 3 tranches :

| Tranche | Solde | Risk % | RR | Score min | Positions max | Levier max | Stop ATR | Notional min |
|---|---|---|---|---|---|---|---|---|
| **MICRO** | 0 – 10 $ | 1.0 | 2.5 | **85** | 3 | 10 | 2.0 | 1.0 $ |
| **RETAIL** | 10 – 50 $ | 1.5 | 2.5 | 80 | 5 | 15 | 1.5 | 5.0 $ |
| **STANDARD** | ≥ 50 $ | 2.0 | 3.0 | 75 | 10 | 20 | 1.5 | 10.0 $ |

Logique :
- **MICRO** : sélectivité maximale (score 85), peu de positions, levier réduit,
  **stop plus large en ATR** (2.0) pour ne pas être sorti par le bruit. Absolu
  anti-martingale.
- **STANDARD** : diversification maximale, RR plus ambitieux.

### Modes d'application
- **`capital_profile_mode = manual`** (défaut) : vos réglages explicites
  gagnent toujours ; le profil est seulement **rapporté** dans `/api/status`.
- **`capital_profile_mode = auto`** : le profil de la tranche **surcharge**
  `max_risk_pct`, `max_leverage`, `max_open_positions`, `min_signal_score`,
  `risk_reward_ratio`, `atr_stop_multiplier`, `min_trade_notional` et
  `max_cost_ratio`. Le bot **s'auto-adapte** à la taille du compte.

### Optimisation pilotée par l'audit
`scripts/profit_audit.py` affiche maintenant des **recommandations actionnables**
par stratégie (`print_recommendations`) :

- `LOSING` → `DISABLE_OR_RAISE_SELECTIVITY` (hausse du score min, renfort du
  filtre de coûts).
- `RR < 1.5` → `WIDEN_TAKE_PROFIT` (TP plus large, trailing relâché).
- `cost_leaks > 0` → `TIGHTEN_COST_FILTER` (activer le filtre sur **toutes** les
  stratégies, abaisser `max_cost_ratio`).
- `PROFITABLE` → `KEEP` (maintenir, scaler progressivement).

Un CLI dédié liste les paramètres recommandés pour un capital donné :

```bash
python3 scripts/optimize_params.py 5.0      # profil MICRO pour 5 $
python3 scripts/optimize_params.py 120.0    # profil STANDARD pour 120 $
```

---

## 5. Cibles « réalistes » vs « 99 % de réussite »

Le prompt demandé visait un **taux de réussite d'au moins 99 %**. C'est
**irréaliste** et dangereux comme objectif : il pousserait à prendre des
positions minuscules et à couper les gagnants tôt pour « assurer » le win rate,
ce qui détruit l'edge. On ne peut pas contrôler le marché, seulement
l'espérance.

**Objectifs réalistes encodés** (`HEALTH_TARGETS`) :
- win rate ≥ **45 %** ;
- RR réalisé ≥ **1.5** ;
- espérance ≥ **+0.5R** ;
- profit factor ≥ **1.3** ;
- **0 fuite de coûts**.

Ces cibles sont utilisées par le module `capital_profiles` pour émettre les
recommandations.

---

## 6. Résumé des changements (Lot Q)

| Fichier | Changement |
|---|---|
| `api/engines/capital_profiles.py` | **nouveau** : tranches de capital, `resolve_bracket`, `profile_overrides`, `recommend_from_audit`, cibles réalistes |
| `api/engines/risk_engine.py` | `min_account_balance`/`min_trade_notional` configurables (défaut 1 $), suppression du plancher `10` codé en dur |
| `api/engines/signal_engine.py` | `atr_stop_multiplier` paramétrable + `set_atr_stop_multiplier` |
| `api/engines/settings_schema.py` | nouveaux réglages `min_account_balance`, `min_trade_notional`, `atr_stop_multiplier`, `capital_profile_mode` |
| `api/index.py` | application du profil quand `capital_profile_mode=auto`, `capital_profile` dans `/api/status` |
| `scripts/profit_audit.py` | recommandations d'optimisation par stratégie |
| `scripts/optimize_params.py` | **nouveau** : CLI des paramètres recommandés par capital |
| `tests/test_capital_profiles.py` | **nouveau** : 10 tests (tranches, petit capital, stop ATR, optimisation) |

**Tests** : suite complète **244 passés / 2 échecs pré-existants / 6 skips réseau**.

---

## 7. LOT R — Audit & optimisation PAR MARCHÉ (v2.5.0)

Le Lot Q optimisait **globalement** (par tranche de capital) et jugeait les
**stratégies**. La méthodologie demande aussi d'agir **par marché financier** :
identifier les marchés les plus rentables, ceux qui fonctionnent à quel niveau
de capital, adapter l'agressivité aux conditions du marché, et optimiser
**pour chaque marché** les seuils d'entrée, les stop-loss et les take-profit.

### 7.1 Analyse des données historiques — par marché et par période

`scripts/profit_audit.py` produit désormais :

- **par marché** (`by_market`) : trades, win rate, PnL net, RR réalisé,
  espérance, fuites de coûts, verdict — au niveau du mode **et** toutes modes
  confondus, avec la classe d'actifs de chaque marché ;
- **par classe d'actifs** (`by_asset_class`) : CRYPTO vs FOREX vs INDICES… ;
- **par période mensuelle** (`by_period`) : les périodes de gains
  significatifs vs celles de pertes ;
- un **classement des marchés** (du moins rentable au plus rentable) ;
- un bloc **PER-MARKET OPTIMIZATION** : pour chaque marché jugé (≥ 10 trades
  fermés — honnêteté statistique), l'action recommandée et ses paramètres ;
- un flag **`--json`** pour exporter tout le rapport (utilisable par
  `optimize_params.py` ou pour générer la carte `market_tuning`).

```bash
python3 scripts/profit_audit.py data/quantum_trade.db 5.0        # rapport complet
python3 scripts/profit_audit.py data/quantum_trade.db 5.0 --json # export JSON
```

### 7.2 Évaluation des marchés par niveau de capital

`market_tuning.markets_feasible_for_capital(balance, universe)` estime le
**capital minimal** de chaque marché : marge ≈ `min_order` (notionnel minimum —
le même champ que le moteur de risque applique) ÷ levier effectif (plafonné par
l'instrument **et** par le profil de tranche), avec une marge de sécurité de
20 %. Résultat au 2026-08-22 :

| Classe | Capital min estimé (REAL) | À 1 $ | À 5 $ | À 50 $ |
|---|---|---|---|---|
| COMMODITIES / FUTURES / INDICES / BONDS / STOCKS / ETFS | < 0,25 $ | ✅* | ✅* | ✅* |
| CRYPTO | ~1,2 $ (min notional 10 $ @ 10–20x) | ✅ (limite) | ✅ | ✅ |
| FOREX | ~60–120 $ (micro-lot 1 000) | ❌ | ❌ | ❌ (≥ 60 $) |

\* ces classes sont sourcées Yahoo (**données différées ~15 min**) : la garde
anti-scalping les **bloque pour l'exécution automatique** tant que
`allow_delayed_data_trading=false` (défaut). En DEMO (papier) tout est
faisable. Visible en direct via `GET /api/optimization` et
`python3 scripts/optimize_params.py <solde>`.

### 7.3 Adaptation aux conditions du marché (régime de volatilité)

Demande : *« utilise des stratégies plus conservatrices lors de marchés
volatils et des stratégies plus agressives lors de marchés stables »*. C'est
maintenant câblé dans `SignalEngine` (`regime_adaptation_enabled`, défaut
`true`) à partir de l'étiquette volatilité de l'analyse (HIGH/MEDIUM/LOW) :

| Régime | Seuil d'entrée | Stop (× ATR) | Effet de risque |
|---|---|---|---|
| **VOLATILE** | **+5** (plus sélectif) | **×1.25** (élargi) | position réduite automatiquement à risque % égal |
| NORMAL | — | ×1.0 | — |
| QUIET (stable) | **−3** (floor 50) | ×0.90 | engagement modéré sur tendances propres |

Chaque signal expose `regime`, `min_score_applied` et `atr_stop_multiplier`
pour le diagnostic (visible dans `/api/status?market_id=` et le scanner).

### 7.4 Optimisation des paramètres PAR MARCHÉ

Lignes de base par classe d'actifs (`ASSET_CLASS_TUNING`), par exemple :

| Classe | min_score | TP (RR) | Stop (×ATR) | Justification |
|---|---|---|---|---|
| CRYPTO | 80 | 2.5 | 1.5 | 24/7 temps réel, volatilité native |
| FOREX | 82 | 2.0 | 1.8 | spread relatif élevé vs mouvement 1m |
| COMMODITIES | 83 | 2.2 | 1.8 | gaps de session |
| STOCKS / ETFS / BONDS | 85 | 1.8–2.0 | 2.0 | données différées, mouvements lents |

Ces lignes de base sont affinées **par l'audit** (`recommend_for_market`) :

| Verdict (≥ 10 trades) | Action | Paramètres recommandés |
|---|---|---|
| LOSING | `QUARANTINE_OR_RAISE_SELECTIVITY` | seuil d'entrée +10 sur ce marché (ou suspension) |
| TP_TOO_TIGHT (RR < 1.5) | `WIDEN_TAKE_PROFIT` | TP +0.5R, stop ATR +0.5 sur ce marché |
| COST_LEAK | `TIGHTEN_COST_FILTER` | `max_cost_ratio` 0.4 pour ce marché |
| PROFITABLE | `KEEP_AND_SCALE` | seuil d'entrée −3 (capitaliser sur l'edge) |
| < 10 trades | `OBSERVE` | aucun (pas de tuning sur du bruit) |

**Application** : le JSON produit par l'audit se colle dans le réglage
`market_tuning` (`POST /api/settings`) — appliqué **à chaud**, fusionné sur les
défauts de classe. Le `SignalEngine` utilise alors pour CHAQUE marché son
propre seuil d'entrée / TP / stop.

```bash
# 1. auditer, 2. récupérer le JSON market_tuning affiché, 3. l'appliquer :
curl -X POST /api/settings -H "X-API-Key: …" \
     -d '{"market_tuning": "{\"doge_usdt\": {\"min_score\": 95}, \"eur_usd\": {\"risk_reward\": 3.0}}"}'
```

### 7.5 Résumé des changements (Lot R)

| Fichier | Changement |
|---|---|
| `api/engines/market_tuning.py` | **nouveau** : tuning par marché/classe, régimes, faisabilité capital, recommandations audit |
| `api/engines/signal_engine.py` | seuil/SL/TP effectifs par marché + régime, exposés dans le signal |
| `api/engines/settings_schema.py` | réglages `regime_adaptation_enabled`, `market_tuning` |
| `api/index.py` | câblage à chaud + `GET /api/optimization` |
| `scripts/profit_audit.py` | par marché / par classe / par période + `--json` + recommandations par marché |
| `scripts/optimize_params.py` | faisabilité des marchés au capital (levier du profil) |
| `tests/test_market_tuning.py` | **nouveau** : 21 tests |

**Tests** : **375 passés / 6 skips réseau / 0 échec**.
