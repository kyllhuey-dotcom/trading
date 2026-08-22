# 🏗️ CHANTIER DE REFONTE — Quantum Trade Pro v2.0

Plan d'exécution lot par lot (chaque lot est testé avant de passer au suivant).

| Lot | Périmètre | Statut |
|---|---|---|
| 1 | Cœur de trading : market_id dans les signaux, RiskEngine (SL, daily loss, cooldown, positions max), ExecutionEngine (IDs uniques, metadata) | ✅ |
| 2 | Mode REAL honnête : CCXTAdapter réel (ordre, SL/TP, close_all), PrimeXBT via CCXT, BrokerConnector, Router idempotent | ✅ |
| 3 | Sécurité : auth API, chiffrement Fernet correct, .gitignore + purge des fichiers sensibles | ✅ |
| 4 | API complète + frontend aligné (endpoints manquants, contrat /api/status, JS corrigé) | ✅ |
| 5 | Robustesse moteurs : SQLite, logging, providers (binance), code mort supprimé/branché | ✅ |
| 6 | Tests : config pytest, isolation DB, corrections, nouveaux tests P0 (52 passés, 3 skips réseau) | ✅ |
| 7 | Déploiement & repo : Railway (volume, healthcheck), requirements dev, nettoyage | ✅ |
| 8 | Documentation : README, contrat API réel, audits honnêtes | ✅ |
| 9 | Validation finale : suite complète, serveur live, endpoints, rapport | ✅ |
| 10 (LOT Q) | Petits capitaux (1 $→10 $), profils capital-aware (MICRO/RETAIL/STANDARD), stop ATR paramétrable, optimisation pilotée par l'audit | ✅ |
| 11 (ordre final) | Hygiène & remise en ordre : artefacts purgés (`uploads/*.csv`, fichier vide `op`), suite unifiée dans `tests/` (`test_lot2_data.py` racine → `test_data_engine_live.py`), anciens lots 9–13 renommés par fonctionnalité, isolation `data/` garantie (perf + logs), dépendance fantôme `httpx2` supprimée, version API resynchronisée | ✅ |

> **Note de numérotation** : ce plan (lots 1–10) concerne la refonte v2. Les fichiers de
> tests `test_lot9…lot13` dataient d'un plan antérieur (v1.4 : positions, notifications,
> performance, backtest, paper trading) et entraient en collision avec cette numérotation.
> Ils ont été renommés en noms fonctionnels au lot 11 (`test_position_management.py`,
> `test_notifications.py`, `test_performance.py`, `test_backtest.py`,
> `test_paper_trading.py`, `test_docs_contracts.py`).
