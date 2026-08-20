# Agent de Trading Ultra-Scalping - Méthodologie

## Principes de Décision
Le bot suit un cycle de décision strict (Règle 26) :
1. **Validation des Données** (Réelles uniquement)
2. **Calendrier Économique** (Filtrage des annonces)
3. **Disponibilité Marché** (Autorisé 7j/7 si ouvert)
4. **Fraicheur des Données** (Vérification < 5 min)
4. **Liquidité & Spread**
5. **Analyse de Structure** (HH/HL, Trend)
6. **Filtre de Range** (Interdiction absolue)
7. **Signal & Momentum**
8. **Calcul du Risque** (Risk Engine)
9. **Exécution**

## Sources & Méthodologie
- Analyse de structure basée sur le Price Action (BOS, CHoCH).
- Scalping de momentum en tendance établie.
- Gestion du risque fixe par trade avec ajustement dynamique du levier.
