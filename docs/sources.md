# Sources & Méthodologie - Trading Agent

## 1. Structure de Marché (Analysis Engine)
- **Concept** : Price Action institutionnel.
- **Pivots (Fractals)** : Basés sur la détection de sommets et creux locaux via une fenêtre glissante.
- **HH/HL/LH/LL** : Définition standard de la tendance (Dow Theory).
- **BOS (Break of Structure)** : Cassure d'un sommet/creux précédent par le prix de clôture.
- **CHoCH (Change of Character)** : Premier signe de retournement de tendance.

## 2. Money Management (Risk Engine)
- **Dimensionnement** : Basé sur le risque cash (1% par défaut). Formule: `Position Size = Risk Amount / Stop Distance`.
- **Levier** : Calculé dynamiquement pour ne pas dépasser le plafond de sécurité (20x).
- **Minimum Order** : Validation par rapport aux contraintes réelles des brokers (simulé à 10€).

## 3. Données de Marché (Data Engine)
- **Sources** : 
    - Crypto : Gate.io (via CCXT).
    - Forex/Matières/Indices : Yahoo Finance.
- **Fraîcheur** : Blocage systématique si la donnée date de plus de 60 secondes.

## 4. Calendrier Économique
- **Source** : Forexfactory (Flux JSON officiel).
- **Filtrage** : Exclusion des annonces "High Impact" sur USD/EUR.

## 5. Discipline & Sécurité
- **Machine à États** : Transition d'états immuables pour empêcher les ordres simultanés ou non autorisés.
- **Emergency Stop** : Désarment immédiat et blocage complet.
- **Trading Days** : Mardi, Mercredi, Jeudi uniquement (Europe/Paris).
