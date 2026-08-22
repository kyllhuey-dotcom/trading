(function (global) {
    const DICT = {
        en: {
            dashboard: "Dashboard", markets: "Market Hub", scanner: "Global Radar", trading: "Trade Terminal",
            brokers: "Brokers & Wallets", positions: "Open Positions", history: "Trade Journal",
            settings: "Settings", emergency: "Emergency Stop", start: "START SYSTEM", stop: "STOP SYSTEM",
            demo: "DEMO", live: "LIVE", command: "Command Center", commandSub: "Real-time performance and system diagnostic.",
            balance: "Account Balance", equity: "Current Equity", dailyPnl: "Daily P&L", drawdown: "Max Drawdown",
            engineHealth: "Engine Health", diagnosis: "Decision Diagnostic", opportunities: "Opportunities",
            calendar: "Calendar", radarTitle: "Global Radar", radarSub: "Unified scanning of all tracked instruments.",
            all: "All", cryptoOnly: "Crypto only", instrument: "Instrument", livePrice: "Prix live",
            change24h: "Variation 24h", bias: "Bias", score: "Score", strategy: "Stratégie",
            dataAge: "Age data", action: "Action", trade: "TRADE", wait: "WAIT",
            hubTitle: "Market Hub", hubSub: "Global assets across 5 classes.", volume: "Volume", variation: "Variation",
            terminal: "Trade Terminal", executeSignal: "Execute Signal", market: "Market", limit: "Limit", stop: "Stop",
            buy: "BUY", sell: "SELL", lotSize: "Lot Size", stopLoss: "Stop Loss", takeProfit: "Take Profit",
            riskBased: "Risk-based size", orderBook: "Order Book", newsImpact: "News Impact",
            arm: "ARM", disarm: "DISARM", exposure: "Exposure", exitAll: "Exit All", journal: "Trade Journal",
            intel: "Intelligence Config", intelSub: "Fine-tune your algorithmic DNA and execution parameters.",
            language: "Language", timezone: "Timezone", deploy: "Deploy Neural Parameters",
            deployed: "Parameters deployed live", waitingSetup: "Waiting for institutional setup",
            executing: "Executing high-conviction trade…", noBook: "No order book", disconnected: "Disconnected"
        },
        fr: {
            dashboard: "Tableau de bord", markets: "Market Hub", scanner: "Radar global", trading: "Terminal",
            brokers: "Brokers & Wallets", positions: "Positions", history: "Journal",
            settings: "Paramètres", emergency: "Arrêt d'urgence", start: "DÉMARRER", stop: "ARRÊTER",
            demo: "DÉMO", live: "LIVE", command: "Centre de commande", commandSub: "Performance temps réel et diagnostic.",
            balance: "Solde", equity: "Equity", dailyPnl: "P&L journalier", drawdown: "Drawdown",
            engineHealth: "Santé moteur", diagnosis: "Diagnostic", opportunities: "Opportunités",
            calendar: "Calendrier", radarTitle: "Radar global", radarSub: "Scan unifié de tous les instruments.",
            all: "Toutes", cryptoOnly: "Crypto only", instrument: "Instrument", livePrice: "Prix live",
            change24h: "Variation 24h", bias: "Bias", score: "Score", strategy: "Stratégie",
            dataAge: "Age data", action: "Action", trade: "TRADE", wait: "ATTENTE",
            hubTitle: "Market Hub", hubSub: "Actifs globaux.", volume: "Volume", variation: "Variation",
            terminal: "Terminal", executeSignal: "Exécuter le signal", market: "Marché", limit: "Limite", stop: "Stop",
            buy: "ACHAT", sell: "VENTE", lotSize: "Lot", stopLoss: "Stop Loss", takeProfit: "Take Profit",
            riskBased: "Taille au risque", orderBook: "Carnet d'ordres", newsImpact: "Impact news",
            arm: "ARMER", disarm: "DÉSARMER", exposure: "Exposition", exitAll: "Tout fermer", journal: "Journal",
            intel: "Configuration intelligence", intelSub: "Paramètres algorithmiques.",
            language: "Langue", timezone: "Fuseau", deploy: "Déployer",
            deployed: "Parameters deployed live", waitingSetup: "En attente d'un setup ≥80",
            executing: "Exécution high-conviction…", noBook: "Pas de carnet", disconnected: "Déconnecté"
        },
        es: {
            dashboard: "Panel", markets: "Market Hub", scanner: "Radar global", trading: "Terminal",
            brokers: "Brokers", positions: "Posiciones", history: "Diario",
            settings: "Ajustes", emergency: "Parada de emergencia", start: "INICIAR", stop: "PARAR",
            demo: "DEMO", live: "LIVE", command: "Centro de mando", commandSub: "Rendimiento en tiempo real.",
            balance: "Balance", equity: "Equity", dailyPnl: "P&L diario", drawdown: "Drawdown",
            engineHealth: "Salud del motor", diagnosis: "Diagnóstico", opportunities: "Oportunidades",
            calendar: "Calendario", radarTitle: "Radar global", radarSub: "Escaneo unificado.",
            all: "Todas", cryptoOnly: "Solo crypto", instrument: "Instrumento", livePrice: "Prix live",
            change24h: "Variation 24h", bias: "Bias", score: "Score", strategy: "Stratégie",
            dataAge: "Age data", action: "Acción", trade: "TRADE", wait: "ESPERA",
            hubTitle: "Market Hub", hubSub: "Activos globales.", volume: "Volumen", variation: "Variation",
            terminal: "Terminal", executeSignal: "Ejecutar señal", market: "Mercado", limit: "Límite", stop: "Stop",
            buy: "COMPRA", sell: "VENTA", lotSize: "Lote", stopLoss: "Stop Loss", takeProfit: "Take Profit",
            riskBased: "Tamaño por riesgo", orderBook: "Libro de órdenes", newsImpact: "Impacto noticias",
            arm: "ARMAR", disarm: "DESARMAR", exposure: "Exposición", exitAll: "Cerrar todo", journal: "Diario",
            intel: "Config inteligencia", intelSub: "Parámetros algorítmicos.",
            language: "Idioma", timezone: "Zona horaria", deploy: "Desplegar",
            deployed: "Parameters deployed live", waitingSetup: "Esperando setup institucional",
            executing: "Ejecutando trade…", noBook: "Sin libro", disconnected: "Desconectado"
        },
        de: {
            dashboard: "Dashboard", markets: "Market Hub", scanner: "Global Radar", trading: "Terminal",
            brokers: "Broker", positions: "Positionen", history: "Journal",
            settings: "Einstellungen", emergency: "Not-Aus", start: "START", stop: "STOP",
            demo: "DEMO", live: "LIVE", command: "Kommandozentrale", commandSub: "Echtzeit-Diagnose.",
            balance: "Saldo", equity: "Equity", dailyPnl: "Tages-P&L", drawdown: "Drawdown",
            engineHealth: "Engine Health", diagnosis: "Diagnose", opportunities: "Chancen",
            calendar: "Kalender", radarTitle: "Global Radar", radarSub: "Einheitlicher Scan.",
            all: "Alle", cryptoOnly: "Nur Crypto", instrument: "Instrument", livePrice: "Prix live",
            change24h: "Variation 24h", bias: "Bias", score: "Score", strategy: "Stratégie",
            dataAge: "Age data", action: "Aktion", trade: "TRADE", wait: "WARTEN",
            hubTitle: "Market Hub", hubSub: "Globale Assets.", volume: "Volumen", variation: "Variation",
            terminal: "Terminal", executeSignal: "Signal ausführen", market: "Market", limit: "Limit", stop: "Stop",
            buy: "KAUF", sell: "VERKAUF", lotSize: "Los", stopLoss: "Stop Loss", takeProfit: "Take Profit",
            riskBased: "Risikobasierte Größe", orderBook: "Orderbuch", newsImpact: "News-Impact",
            arm: "SCHARF", disarm: "ENTSCHÄRFT", exposure: "Exposure", exitAll: "Alle schließen", journal: "Journal",
            intel: "Intelligence Config", intelSub: "Algorithmische Parameter.",
            language: "Sprache", timezone: "Zeitzone", deploy: "Deploy",
            deployed: "Parameters deployed live", waitingSetup: "Warte auf Setup ≥80",
            executing: "Führe High-Conviction Trade aus…", noBook: "Kein Buch", disconnected: "Getrennt"
        }
    };

    let currentLang = "en";

    function t(key) {
        const pack = DICT[currentLang] || DICT.en;
        return pack[key] || (DICT.en[key] || key);
    }

    function applyLanguage(lang) {
        currentLang = DICT[lang] ? lang : "en";
        try { localStorage.setItem("qtp-lang", currentLang); } catch (e) {}
        document.documentElement.setAttribute("lang", currentLang);
        document.querySelectorAll("[data-i18n]").forEach(el => {
            const key = el.getAttribute("data-i18n");
            if (key) el.textContent = t(key);
        });
    }

    global.QTP_I18N = { t, applyLanguage, get currentLang() { return currentLang; }, DICT };
})(window);
