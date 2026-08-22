# Audit de rentabilité & optimisation — Quantum Trade Pro

> **Périmètre de cet audit** : le dépôt contient **aucune base de trades** (les
> `uploads/*.csv` sont des logs serveur Railway, pas des trades). L'audit porte
> donc sur **le code, les stratégies, la gestion de risque et la configuration**.
> Dès qu'une base `quantum_trade.db` (ou un export) sera fournie, lancer
> `python3 scripts/profit_audit.py <db> <balance>` pour obtenir les statistiques
> réelles + les recommandations d'optimisation.

Ce document applique la méthodologie demandée au bot **Quantum Trade Pro** :

1. Audit des performances (données historiques).
2. Évaluation des stratégies.
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
