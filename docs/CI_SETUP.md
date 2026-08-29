# MISE EN PLACE CI GITHUB — v3.3.2 (action requise, 2 minutes)

## Pourquoi ce document

Le commit de la campagne v3.3.2 contient 3 fichiers de workflow GitHub.
L'agent de codage pousse avec le compte applicatif `arena-ai-coding-agent[bot]`,
qui **n'a pas la permission `workflows`** : GitHub refuse tout commit touchant
`.github/workflows/` par ce compte. C'est une règle GitHub stricte — seuls
l'owner du repo (ou un compte ayant la permission `workflows: write`) peut
commiter ces fichiers.

Les 3 fichiers sont livrés **prêts à coller** dans `docs/ci/workflows/` :

| Fichier livré ici | Emplacement final | Rôle |
|---|---|---|
| `docs/ci/workflows/ci.yml` | `.github/workflows/ci.yml` | CI cœur : Python 3.11 + `scripts/validate.sh` (6 portes : déps+import+pip check, compileall+ruff, secrets, **suite ≥ 792 avec cov branches ≥ 85 %**, cov `api/engines` ≥ 80 %, point d'entrée) |
| `docs/ci/workflows/realtime-data-audit.yml` | `.github/workflows/realtime-data-audit.yml` | **Campagne live** : `scripts/realtime_data_audit.py` (10 endpoints publics, tolérances 0,10 %/1 %/0,7 %, fraîcheur 10 s) — c'est ici que le PASS réel des données se joue, rapport JSON en artefact |
| `docs/ci/workflows/testnet-campaign.yml` | `.github/workflows/testnet-campaign.yml` | **Campagne testnet OPT-IN MANUEL** : sandbox forcé, clés via secrets GitHub, rapport scrubé en artefact |

## Option A — vous déposez les fichiers (recommandé, 2 minutes)

Depuis votre machine (ou l'UI GitHub → *Add file* 3 fois) :

```bash
mkdir -p .github/workflows
cp docs/ci/workflows/ci.yml \
   docs/ci/workflows/realtime-data-audit.yml \
   docs/ci/workflows/testnet-campaign.yml .github/workflows/
git add .github/workflows
git commit -m "CI v3.3.2: workflows ci/audit/testnet (permission workflows)"
git push
```

Puis ré-ajoutez les 2 badges sous le titre du `README.md` (section « CI
v3.3.2 » du bas de README ou en tête) :

```markdown
[![CI](https://github.com/kyllhuey-dotcom/trading/actions/workflows/ci.yml/badge.svg)](https://github.com/kyllhuey-dotcom/trading/actions/workflows/ci.yml)
[![Realtime data audit](https://github.com/kyllhuey-dotcom/trading/actions/workflows/realtime-data-audit.yml/badge.svg)](https://github.com/kyllhuey-dotcom/trading/actions/workflows/realtime-data-audit.yml)
```

→ Le premier run de `ci.yml` part tout seul (push sur `main`/`arena/**`/PR).
**Watcher ce run jusqu'au vert** est la dernière porte de la campagne.
`realtime-data-audit.yml` tourne sur push/`main` + manuel : c'est le premier
**PASS réel** de la campagne données (le sandbox de l'agent n'a pas de sortie
réseau vers les endpoints marchés — HTTP 000 — donc ce PASS ne peut s'obtenir
que là, rien n'est simulé).

## Option B — donner la permission `workflows` à l'app, l'agent finit

Repo → *Settings* → *Applications* (ou *Developer settings → GitHub Apps*) →
l'app Arena : ajouter la permission **workflows: write**. Dites-le ensuite à
la session de codage : elle pousse les 3 fichiers + les badges et ouvre le
deuxième PR.

## Campagne testnet (après dépôt des workflows)

1. Settings → *Secrets and variables* → *Actions* → ajouter (clés **testnet
   officielles que vous avez créées vous-même**, read+trading, jamais de
   withdrawal — `docs/TESTNET_KEYS_GUIDE.md`) :
   `BINANCE_API_KEY`, `BINANCE_API_SECRET`, `BYBIT_API_KEY`, `BYBIT_API_SECRET`,
   `OKX_API_KEY`, `OKX_API_SECRET`, `OKX_API_PASSPHRASE`, `GATE_API_KEY`,
   `GATE_API_SECRET`.
2. Actions → *Testnet campaign* → *Run workflow*.
3. Le rapport `data/testnet_matrix_*.json` (artefact) doit afficher
   `overall_status: PASS` — c'est la précondition documentée avant de sortir
   **REAL** du statut expérimental. **Jusqu'à ce PASS : REAL reste
   expérimental, aucun ARM réel.**

## Règles rappelées (non négociables)

- Jamais de donnée synthétique, jamais de faux PASS, jamais de baisse de seuil.
- Le PASS testnet s'obtient avec **vos** clés — jamais de clés tierces, rien
  dans Git (les secrets vivent dans les settings du repo uniquement).
- REAL expérimental jusqu'à PASS testnet documenté.
