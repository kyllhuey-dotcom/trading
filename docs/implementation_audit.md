# Implementation Audit — Quantum Trade Pro v2.0

État réel post-refonte (août 2026). Chaque affirmation est vérifiable dans le code ou par les tests.

## 1. Données de marché
- **Zéro donnée factice** : le mode DEMO utilise les prix réels (Gate → Bybit → Binance pour le crypto ; Yahoo Finance pour le reste).
- **Redondance** : fallback automatique par instrument + cooldown de 5 min par provider en échec.
- **Typage** : modèles Pydantic v2 (`TickerModel`, `OHLCVModel`) pour toute donnée normalisée.
- **Fraîcheur** : `is_fresh()` bloque l'exécution sur donnée obsolète (crypto < 5 s, autres < 60 s).
- **Timeouts** : aucun appel réseau ne peut bloquer le scan ou l'API (30 s / 20 s / 10 s).

## 2. Exécution
- **DEMO** : papier réaliste (latence, slippage, rejets simulés configurables) sur prix bid/ask réels.
- **REAL** : ordres market réels via CCXT + ordres SL/TP de protection ; position enregistrée en DB et réconciliée avec le broker.
- **Anti-doublon** : throttle 5 s + client_order_id unique + refus de ré-ouvrir un symbole déjà en position.
- **Emergency stop** : désarme, ferme les positions démo ET réelles (close_all_positions sur tous les adaptateurs), puis bloque le passage en REAL tant que le reset n'a pas été fait.

## 3. Gestion de risque (appliquée à l'ordre)
- Sizing par % de risque / distance SL, plafond de levier.
- Validation du sens du SL (BUY → SL < entry ; SELL → SL > entry).
- Limite de perte quotidienne **au niveau de l'ordre**.
- Cool-down après perte, max positions simultanées, filtre de corrélation.
- Drawdown global avec pic persistant (survit aux redéploiements).

## 4. Sécurité
- Auth `X-API-Key` sur tous les endpoints mutables.
- Secrets brokers chiffrés Fernet au repos.
- Pas de secrets dans le repo (scan automatisé dans `scripts/validate.sh`).

## 5. Qualité
- 52 tests / 3 skips réseau, suite isolée (DB temporaire).
- `scripts/smoke_test.py` pour valider un déploiement.
- `scripts/validate.sh` : scan de secrets + tests + gate de couverture 60 %.
