# Audit v3.2 — angles morts REAL

La v3.2 durcit l'exécution REAL : annulation best-effort des protections jumelles, détection honnête des fills spot, types stop par exchange, idempotence `clientOrderId`, comptabilité PnL/frais au close, alerte NAKED, et backstop sans double ordre. Le catalogue ne prétend plus router le tradfi vers Gate.

Les invariants v3.1 restent obligatoires : aucune clôture DB sans preuve broker, une liste de positions spot vide n'est pas une preuve, fail-close SL/TP et sandbox réel. REAL reste expérimental et exige une campagne testnet avant ARM réel.
