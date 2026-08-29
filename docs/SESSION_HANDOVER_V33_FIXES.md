# SESSION HANDOVER — v3.3 FIXES (recréé) — 2026-08-29

> **Honnêteté d'abord.** Ce document recrée la passation officielle
> `SESSION_HANDOVER_V33_FIXES.md`, perdue avec le sandbox de la session
> précédente. Les commits locaux décrits dans l'original (`1f9fa4e`,
> `c0cdba5`, `362116e`, branche locale `arena/01a04e5f-trading`) n'existent
> sur **aucune** ref — ni locale, ni distante (branches `arena/*`,
> `refs/pull/*`), vérifié par `git log --all` et `git ls-remote`. Ils étaient
> locaux à un sandbox détruit et sont **irrécupérables**. Le travail décrit
> ci-dessous a donc été **réimplémenté** à partir de la description de la
> passation, puis validé intégralement.

## État de la base

- v3.3.0 mergée dans `main` (commit `2d4c5cb`, PR #25).
- La **comptabilité de close partiel** (delta `broker_filled -
  last_accounted_filled`, close complet refusé si `filled < quantity`,
  frais pro-rata) était **déjà dans la base mergée** (pnl_engine +
  broker_connector + tests v33) : ré-assertée, non dupliquée.

## Réimplémentation (cette session, PR unique vers main)

| Point aveugle v3.3 | Réimplémentation |
|---|---|
| Retry + jitter des lectures | `read_with_retry` (backoff exponentiel, full jitter, borné) sur les lectures **idempotentes** (fetch_balance, fetch_positions, fetch_order) ; **jamais** sur create/cancel/close (risque ORDER_STATE_UNKNOWN) — testé, y compris garde structurelle. |
| Correlation IDs | Middleware HTTP : `X-Correlation-ID` (fourni et assaini, sinon généré `qtp-<32hex>`), écho sur toute réponse, injection dans les métadonnées d'audit (`audit_details`). Tentative d'injection → ID sûr généré. |
| Collision backup | `scripts/backup_db.py` : deux backups à la seconde près ne s'écrasent plus (suffixes `_1`, `_2`, …) ; sha256 sidecar par fichier — testé. |
| i18n 4 langues | Parité stricte fr/en/es/de ré-assertée (284 clés × 4) ; clé `noMarketData` ajoutée dans les 4 dictionnaires. |
| Bandeau « no market data » | `#no-market-data-banner` : affiché UNIQUEMENT quand un scan TERMINÉ rapporte zéro ligne exploitable (tout `DATA_UNAVAILABLE`, ou 0/126 + marchés indisponibles/erreur scan) ; jamais inventé. |
| Docs clés testnet | `docs/TESTNET_KEYS_GUIDE.md` (portails officiels, permissions trading-sans-withdrawal, env vars, diagnostic des échecs réels). |
| Fix import script standalone | `scripts/testnet_broker_matrix.py` : bootstrap `sys.path` depuis `__file__` — le script se lance depuis n'importe quel cwd. |
| Bug Google Translate | Voir `docs/FINALISATION_V33.md` §Google Translate : `notranslate` sur TOUTES les zones dynamiques (statiques + conteneurs réécrits + réécritures `className` préservées), `document.documentElement.lang` au chargement et à chaque changement de langue, re-render i18n sans chevauchement — tests de régression statiques dédiés. |

## Campagne testnet — EN ATTENTE (blocage externe réel)

Sortie réseau du sandbox : **absente** (HTTP 000 sur testnet.binance.vision,
api-testnet.bybit.com, okx.com, api.gateio.ws). Aucun résultat simulé, aucun
PASS fabriqué. Commande exacte à exécuter depuis une machine avec internet :
`docs/TESTNET_KEYS_GUIDE.md` §2-4 (clés créées par le propriétaire sur les
portails officiels, permissions trading uniquement, jamais withdrawal).

**REAL reste expérimental** tant que le rapport JSON n'affiche pas
`overall_status: PASS` — conformément à la convention du repo.
