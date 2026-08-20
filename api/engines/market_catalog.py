from typing import Dict, Any, List, Optional

class MarketCatalog:
    """
    Global Market Catalog (Rule 7, 10, 12, 13, 14).
    Centralizes all instrument definitions and handles cross-provider/broker mapping.
    """
    
    # Internal ID -> Configuration
    # Mapping logic: Internal ID is the source of truth for the UI.
    CATALOG = {
        "btc_usdt": {
            "display_symbol": "BTC/USDT",
            "asset_class": "CRYPTO",
            "name": "Bitcoin / Tether",
            "providers": {
                "gate": "BTC/USDT",
                "binance": "BTC/USDT"
            },
            "broker_symbols": {
                "primexbt": "BTC/USDT",
                "binance": "BTC/USDT"
            },
            "tick_size": 0.1,
            "lot_size": 0.0001,
            "min_order": 10.0,
            "leverage": 100
        },
        "eth_usdt": {
            "display_symbol": "ETH/USDT",
            "asset_class": "CRYPTO",
            "name": "Ethereum / Tether",
            "providers": {
                "gate": "ETH/USDT",
                "binance": "ETH/USDT"
            },
            "broker_symbols": {
                "primexbt": "ETH/USDT",
                "binance": "ETH/USDT"
            },
            "tick_size": 0.01,
            "lot_size": 0.001,
            "min_order": 10.0,
            "leverage": 100
        },
        "eur_usd": {
            "display_symbol": "EUR/USD",
            "asset_class": "FOREX",
            "name": "Euro / US Dollar",
            "providers": {
                "yahoo_forex": "EURUSD=X"
            },
            "broker_symbols": {
                "primexbt": "EURUSD",
                "activtrades": "EURUSD"
            },
            "tick_size": 0.00001,
            "lot_size": 1000,
            "min_order": 1000.0,
            "leverage": 30
        },
        "gold": {
            "display_symbol": "GOLD",
            "asset_class": "COMMODITIES",
            "name": "Gold Spot",
            "providers": {
                "yahoo_commodities": "GC=F"
            },
            "broker_symbols": {
                "primexbt": "XAUUSD"
            },
            "tick_size": 0.1,
            "lot_size": 1,
            "min_order": 0.1,
            "leverage": 20
        },
        "spx": {
            "display_symbol": "S&P 500",
            "asset_class": "INDICES",
            "name": "S&P 500 Index",
            "providers": {
                "yahoo_indices": "^GSPC"
            },
            "broker_symbols": {
                "primexbt": "SPX"
            },
            "tick_size": 0.25,
            "lot_size": 1,
            "min_order": 1.0,
            "leverage": 20
        }
    }

    @classmethod
    def get_all_ids(cls) -> List[str]:
        return list(cls.CATALOG.keys())

    @classmethod
    def get_categories(cls) -> List[str]:
        return list(set(item["asset_class"] for item in cls.CATALOG.values()))

    @classmethod
    def get_info(cls, market_id: str) -> Optional[Dict[str, Any]]:
        return cls.CATALOG.get(market_id)

    @classmethod
    def map_to_provider(cls, market_id: str, provider_id: str) -> Optional[str]:
        """Rule 14: Internal ID -> Provider Symbol."""
        info = cls.get_info(market_id)
        return info.get("providers", {}).get(provider_id) if info else None

    @classmethod
    def map_to_broker(cls, market_id: str, broker_id: str) -> Optional[str]:
        """Rule 14: Internal ID -> Broker Symbol."""
        info = cls.get_info(market_id)
        return info.get("broker_symbols", {}).get(broker_id) if info else None
