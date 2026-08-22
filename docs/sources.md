# Sources & Méthodologie — Quantum Trade Pro v2.0

## 1. Structure de Marché (Analysis Engine)
- **Concept** : Price Action institutionnel.
- **Pivots (fractals)** : détection de sommets/creux locaux par fenêtre glissante.
- **HH/HL/LH/LL** : définition standard de la tendance (Dow Theory).
- **BOS (Break of Structure)** : cassure du dernier sommet/creux par la clôture.
- **CHoCH (Change of Character)** : premier signe de retournement.

## 2. Money Management (Risk Engine)
- **Dimensionnement** : `Position Size = Risk Amount / Stop Distance`, risque 1 % par défaut.
- **Levier** : calculé dynamiquement, plafonné (20x par défaut).
- **Protections** : sens du SL validé, perte quotidienne max (3 %), cool-down après perte,
  max positions (3), corrélation, drawdown global (10 % → emergency stop).

## 3. Données de Marché (Data Engine)
- **Crypto** : Gate.io (primaire) → Bybit (backup) → Binance (tertiaire), via CCXT.
- **Forex / Indices / Matières / Actions / Futures / Obligations / ETF** : Yahoo Finance
  (données différées ~15 min — utilisées pour du structurel, pas du scalping).
- **Fraîcheur** : blocage de l'exécution si la donnée est trop ancienne
  (crypto < 5 s, autres < 60 s).
- **Redondance** : fallback automatique + cooldown de 5 min par provider en échec.

## 4. Calendrier Économique (News Engine)
- **Source** : ForexFactory, récupérée par **scraping HTML** (le site ne fournit pas
  de flux JSON officiel — contrairement à ce qu'indiquaient les versions précédentes
  de ce document).
- **Filtrage** : événements « High Impact » sur la devise de l'actif (USD/EUR inclus).
- **Fenêtre de sécurité** : 30 min avant / 60 min après un événement bloquant.
- **Fail-safe** : si le calendrier est injoignable, le bot refuse de trader.

## 5. Actualités (News Aggregator)
- ForexLive (RSS par catégorie), ING Think (RSS), BBC Business (RSS).
- Déduplication par hash de titre + scoring d'impact par mots-clés.

## 6. Discipline & Sécurité
- **Machine à états** : STOPPED / RUNNING / EMERGENCY_STOP pilotée par l'API.
- **Sessions** : crypto 24/7 ; forex 24/5 ; actions/indices selon fuseau local
  (approximations documentées dans `market_universe.py` — pas de gestion des jours fériés).
- **Emergency Stop** : désarme, ferme toutes les positions (démo ET réelles), bloque
  le passage en REAL jusqu'au reset.
- **Auth** : clé API sur tous les endpoints mutables ; secrets brokers chiffrés au repos.
