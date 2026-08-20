from typing import Dict, Any, List

class MarketCatalog:
    """
    Market Catalog (Rule 7, 10).
    Centralizes all instrument definitions and their metadata.
    """
    
    # Categorized assets with their specific metadata
    CATALOG = {
        "CRYPTO": {
            "BTC/USDT": {"provider": "gate", "tick_size": 0.1, "lot_size": 0.0001, "min_order": 10.0, "leverage": 100, "currency": "USD"},
            "ETH/USDT": {"provider": "gate", "tick_size": 0.01, "lot_size": 0.001, "min_order": 10.0, "leverage": 100, "currency": "USD"},
            "SOL/USDT": {"provider": "gate", "tick_size": 0.001, "lot_size": 0.01, "min_order": 10.0, "leverage": 50, "currency": "USD"},
            "XRP/USDT": {"provider": "gate", "tick_size": 0.0001, "lot_size": 1.0, "min_order": 10.0, "leverage": 20, "currency": "USD"},
            "BNB/USDT": {"provider": "gate", "tick_size": 0.01, "lot_size": 0.01, "min_order": 10.0, "leverage": 50, "currency": "USD"},
            "DOGE/USDT": {"provider": "gate", "tick_size": 0.00001, "lot_size": 10.0, "min_order": 10.0, "leverage": 20, "currency": "USD"},
            "ADA/USDT": {"provider": "gate", "tick_size": 0.0001, "lot_size": 1.0, "min_order": 10.0, "leverage": 20, "currency": "USD"},
            "LTC/USDT": {"provider": "gate", "tick_size": 0.01, "lot_size": 0.1, "min_order": 10.0, "leverage": 30, "currency": "USD"},
        },
        "FOREX": {
            "EURUSD=X": {"name": "EUR/USD", "provider": "yahoo", "tick_size": 0.00001, "lot_size": 1000, "min_order": 1000.0, "leverage": 30, "currency": "USD"},
            "GBPUSD=X": {"name": "GBP/USD", "provider": "yahoo", "tick_size": 0.00001, "lot_size": 1000, "min_order": 1000.0, "leverage": 30, "currency": "USD"},
            "USDJPY=X": {"name": "USD/JPY", "provider": "yahoo", "tick_size": 0.001, "lot_size": 1000, "min_order": 1000.0, "leverage": 30, "currency": "JPY"},
            "USDCHF=X": {"name": "USD/CHF", "provider": "yahoo", "tick_size": 0.00001, "lot_size": 1000, "min_order": 1000.0, "leverage": 30, "currency": "CHF"},
            "AUDUSD=X": {"name": "AUD/USD", "provider": "yahoo", "tick_size": 0.00001, "lot_size": 1000, "min_order": 1000.0, "leverage": 30, "currency": "USD"},
            "USDCAD=X": {"name": "USD/CAD", "provider": "yahoo", "tick_size": 0.00001, "lot_size": 1000, "min_order": 1000.0, "leverage": 30, "currency": "CAD"},
            "NZDUSD=X": {"name": "NZD/USD", "provider": "yahoo", "tick_size": 0.00001, "lot_size": 1000, "min_order": 1000.0, "leverage": 30, "currency": "USD"},
            "EURGBP=X": {"name": "EUR/GBP", "provider": "yahoo", "tick_size": 0.00001, "lot_size": 1000, "min_order": 1000.0, "leverage": 30, "currency": "GBP"},
            "EURJPY=X": {"name": "EUR/JPY", "provider": "yahoo", "tick_size": 0.001, "lot_size": 1000, "min_order": 1000.0, "leverage": 30, "currency": "JPY"},
        },
        "INDICES": {
            "^GSPC": {"name": "S&P 500", "provider": "yahoo", "tick_size": 0.25, "lot_size": 1, "min_order": 1.0, "leverage": 20, "currency": "USD"},
            "^IXIC": {"name": "Nasdaq 100", "provider": "yahoo", "tick_size": 0.25, "lot_size": 1, "min_order": 1.0, "leverage": 20, "currency": "USD"},
            "^DJI": {"name": "Dow Jones", "provider": "yahoo", "tick_size": 1.0, "lot_size": 1, "min_order": 1.0, "leverage": 20, "currency": "USD"},
            "^GDAXI": {"name": "DAX 40", "provider": "yahoo", "tick_size": 0.5, "lot_size": 1, "min_order": 1.0, "leverage": 20, "currency": "EUR"},
            "^FTSE": {"name": "FTSE 100", "provider": "yahoo", "tick_size": 0.5, "lot_size": 1, "min_order": 1.0, "leverage": 20, "currency": "GBP"},
            "^FCHI": {"name": "CAC 40", "provider": "yahoo", "tick_size": 0.5, "lot_size": 1, "min_order": 1.0, "leverage": 20, "currency": "EUR"},
            "^N225": {"name": "Nikkei 225", "provider": "yahoo", "tick_size": 1.0, "lot_size": 1, "min_order": 1.0, "leverage": 20, "currency": "JPY"},
        },
        "COMMODITIES": {
            "GC=F": {"name": "Gold", "provider": "yahoo", "tick_size": 0.1, "lot_size": 1, "min_order": 0.1, "leverage": 20, "currency": "USD"},
            "SI=F": {"name": "Silver", "provider": "yahoo", "tick_size": 0.005, "lot_size": 1, "min_order": 1.0, "leverage": 20, "currency": "USD"},
            "CL=F": {"name": "Crude Oil", "provider": "yahoo", "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage": 10, "currency": "USD"},
            "BZ=F": {"name": "Brent", "provider": "yahoo", "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage": 10, "currency": "USD"},
            "NG=F": {"name": "Natural Gas", "provider": "yahoo", "tick_size": 0.001, "lot_size": 1, "min_order": 1.0, "leverage": 10, "currency": "USD"},
            "HG=F": {"name": "Copper", "provider": "yahoo", "tick_size": 0.0005, "lot_size": 1, "min_order": 1.0, "leverage": 10, "currency": "USD"},
        }
    }

    @classmethod
    def get_categories(cls) -> List[str]:
        return list(cls.CATALOG.keys())

    @classmethod
    def get_symbols_by_category(cls, category: str) -> List[str]:
        return list(cls.CATALOG.get(category, {}).keys())

    @classmethod
    def get_all_symbols(cls) -> List[str]:
        all_symbols = []
        for cat in cls.CATALOG.values():
            all_symbols.extend(cat.keys())
        return all_symbols

    @classmethod
    def get_info(cls, symbol: str) -> Dict[str, Any]:
        for cat_name, assets in cls.CATALOG.items():
            if symbol in assets:
                info = assets[symbol].copy()
                info["asset_class"] = cat_name
                info["symbol"] = symbol
                if "name" not in info:
                    info["name"] = symbol
                return info
        return {}
