# Implementation Audit - Trading Agent

## 1. État Actuel
L'application dispose d'une structure modulaire FastAPI avec plusieurs moteurs (Data, Analysis, News, Signal, Risk, Execution, Broker). Le frontend est une interface premium inspirée d'Apple.

## 2. Problèmes Identifiés

### Données Synthétiques (Violation Règle 3)
- Le scanner de marché (`scanner_data`) est codé en dur dans `api/index.py`.
- Le graphique frontend utilise `Math.random()` pour générer des barres.
- Les statistiques de performance (PF, Winrate) sont initialisées à des valeurs arbitraires.
- La liquidité est simulée par un bonus fixe de +15 dans le `SignalEngine`.

### Architecture et Logique
- Le cycle de décision (Rule 26/56) n'est pas centralisé dans un Orchestrateur, mais dispersé dans `get_status`.
- La détection de range est trop simpliste (CV < 0.1).
- Le moteur de news (`NewsEngine`) autorise le trading 7j/7 alors que la règle 14 impose Mar/Mer/Jeu.
- Le connecteur broker est un mock pour ActivTrades, ne respectant pas la consigne PrimeXBT (Lot 8).

### Frontend
- Incohérence entre les IDs de boutons (`arm-button` vs `main-arm-btn`).
- Navigation basée sur des sélecteurs globaux fragiles.
- Pas d'affichage réel de l'état "STALE" des données.

## 3. Causes
- Développement par lots successifs privilégiant le visuel pour les premiers rendus.
- Absence d'orchestrateur centralisé pour valider les règles de sécurité.

## 4. Corrections à Apporter (Lot 1)
- Supprimer les données hardcodées du backend.
- Centraliser les états dans une vraie `StateMachine`.
- Corriger la règle des jours autorisés.
- Harmoniser le frontend pour supprimer les bugs de boutons.
- Créer la structure pour l'orchestrateur de trading.

## 5. Risques
- Interruption du flux de données réel rendant l'interface "vide".
- Complexité de l'orchestrateur pouvant ralentir les réponses de l'API.

## 6. Tests Nécessaires
- Validation du blocage les jours non autorisés.
- Test de l'Emergency Stop.
- Vérification de l'absence de données aléatoires dans les logs.
