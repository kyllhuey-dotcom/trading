# Broker Architecture — v2.0

## Abstraction
`BrokerAdapter` (ABC) impose l'interface unique pour toute cible d'exécution :
`connect`, `get_balance`, `get_positions`, `execute_order` (market + SL/TP),
`close_all_positions` (emergency exit), `cancel_order`, `get_status`, `close`.

## Adaptateurs
- **CCXTAdapter** : exécution réelle pour tout exchange CCXT (binance, gate, bybit,
  kraken, okx…). L'ordre est réel (`create_order`), les SL/TP sont attachés en ordres
  conditionnels (best-effort selon l'exchange), et l'emergency stop ferme positions et
  ordres ouverts.
- **PrimeXBTAdapter** : sous-classe CCXT (`primexbt` est supporté par CCXT —
  l'ancienne affirmation « pas d'API publique » était incorrecte).

## Routage
- `ExecutionRouter` : DEMO → `ExecutionEngine` (papier), REAL → `BrokerConnector`.
  Anti-doublon : throttle 5 s + `client_order_id` unique.
- `BrokerConnector` : choisit le premier broker connecté ayant le symbole mappé
  (`market_universe.broker_symbols`), exécute, puis **persiste la position REAL en DB**
  (id `REAL-*`, métadonnées d'ordres brokers).
- Réconciliation : toutes les secondes, les positions DB « OPEN » dont le broker n'a
  plus trace sont fermées en DB (`BROKER_RECONCILED_CLOSE`) — jamais de fausse fermeture locale.

## Sécurité
- Identifiants chiffrés Fernet au repos (table `broker_configs`).
- `POST /api/mode` refuse le passage en REAL sans broker connecté ou si
  l'emergency stop est actif.
- Emergency stop : ferme tout sur tous les brokers connectés.

## Wallets Web3
- Adresses publiques enregistrées en DB (Metamask/Phantom/OKX).
- Solde ETH via BlockCypher (lecture seule) — les wallets ne signent rien.
