from typing import Dict, Any, List

class MarketCatalog:
    """
    Responsabilités :
    - Récupérer les instruments ;
    - Identifier leur classe ;
    - Connaître le symbole broker ;
    - Connaître le tick size, lot size, min order.
    """
    ASSETS = {
        "BTC/USDT": {"class": "CRYPTO", "provider": "crypto", "tick_size": 0.01, "lot_size": 0.00001, "min_order": 10.0, "leverage": 100},
        "ETH/USDT": {"class": "CRYPTO", "provider": "crypto", "tick_size": 0.01, "lot_size": 0.0001, "min_order": 10.0, "leverage": 100},
        "SOL/USDT": {"class": "CRYPTO", "provider": "crypto", "tick_size": 0.001, "lot_size": 0.01, "min_order": 10.0, "leverage": 50},
        "EURUSD=X": {"class": "FOREX", "provider": "yahoo", "tick_size": 0.00001, "lot_size": 1000, "min_order": 1000.0, "leverage": 30},
        "GBPUSD=X": {"class": "FOREX", "provider": "yahoo", "tick_size": 0.00001, "lot_size": 1000, "min_order": 1000.0, "leverage": 30},
        "GC=F": {"class": "COMMODITY", "provider": "yahoo", "tick_size": 0.1, "lot_size": 1, "min_order": 1.0, "leverage": 20}, # Gold
        "CL=F": {"class": "COMMODITY", "provider": "yahoo", "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage": 10}, # Crude Oil
        "^GSPC": {"class": "INDEX", "provider": "yahoo", "tick_size": 0.25, "lot_size": 1, "min_order": 1.0, "leverage": 20}, # S&P 500
        "^IXIC": {"class": "INDEX", "provider": "yahoo", "tick_size": 0.25, "lot_size": 1, "min_order": 1.0, "leverage": 20}, # Nasdaq
    }

    @classmethod
    def get_all_symbols(cls) -> List[str]:
        return list(cls.ASSETS.keys())

    @classmethod
    def get_info(cls, symbol: str) -> Dict[str, Any]:
        return cls.ASSETS.get(symbol, {})
