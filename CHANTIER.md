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
