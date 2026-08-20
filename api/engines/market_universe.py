from typing import Dict, Any, List, Optional
from datetime import datetime
import pytz

class MarketUniverse:
    """
    Market Universe (Rule 5, 12, 13, 14).
    Responsible for discovering instruments, mapping them to providers/brokers,
    and managing their operational status and constraints.
    """
    
    ASSET_CLASSES = ["CRYPTO", "FOREX", "INDICES", "COMMODITIES"]
    
    def __init__(self):
        # Initial static universe (can be expanded dynamically in Lot 2/3)
        self.universe = {
            # CRYPTO
            "btc_usdt": {
                "display_symbol": "BTC/USDT",
                "asset_class": "CRYPTO",
                "name": "Bitcoin",
                "providers": {"gate": "BTC/USDT"},
                "broker_symbols": {"primexbt": "BTC/USDT"},
                "tick_size": 0.1,
                "lot_size": 0.0001,
                "min_order": 10.0,
                "leverage_max": 100,
                "timezone": "UTC"
            },
            "eth_usdt": {
                "display_symbol": "ETH/USDT",
                "asset_class": "CRYPTO",
                "name": "Ethereum",
                "providers": {"gate": "ETH/USDT"},
                "broker_symbols": {"primexbt": "ETH/USDT"},
                "tick_size": 0.01,
                "lot_size": 0.001,
                "min_order": 10.0,
                "leverage_max": 100,
                "timezone": "UTC"
            },
            "sol_usdt": {
                "display_symbol": "SOL/USDT",
                "asset_class": "CRYPTO",
                "name": "Solana",
                "providers": {"gate": "SOL/USDT"},
                "broker_symbols": {"primexbt": "SOL/USDT"},
                "tick_size": 0.001,
                "lot_size": 0.1,
                "min_order": 10.0,
                "leverage_max": 50,
                "timezone": "UTC"
            },
            "bnb_usdt": {
                "display_symbol": "BNB/USDT",
                "asset_class": "CRYPTO",
                "name": "Binance Coin",
                "providers": {"gate": "BNB/USDT"},
                "broker_symbols": {"primexbt": "BNB/USDT"},
                "tick_size": 0.01,
                "lot_size": 0.01,
                "min_order": 10.0,
                "leverage_max": 50,
                "timezone": "UTC"
            },
            
            # FOREX
            "eur_usd": {
                "display_symbol": "EUR/USD",
                "asset_class": "FOREX",
                "name": "Euro / US Dollar",
                "providers": {"yahoo_forex": "EURUSD=X"},
                "broker_symbols": {"primexbt": "EURUSD"},
                "tick_size": 0.00001,
                "lot_size": 1000,
                "min_order": 1000.0,
                "leverage_max": 30,
                "timezone": "Europe/London"
            },
            "gbp_usd": {
                "display_symbol": "GBP/USD",
                "asset_class": "FOREX",
                "name": "British Pound / US Dollar",
                "providers": {"yahoo_forex": "GBPUSD=X"},
                "broker_symbols": {"primexbt": "GBPUSD"},
                "tick_size": 0.00001,
                "lot_size": 1000,
                "min_order": 1000.0,
                "leverage_max": 30,
                "timezone": "Europe/London"
            },
            "usd_jpy": {
                "display_symbol": "USD/JPY",
                "asset_class": "FOREX",
                "name": "US Dollar / Japanese Yen",
                "providers": {"yahoo_forex": "JPY=X"},
                "broker_symbols": {"primexbt": "USDJPY"},
                "tick_size": 0.001,
                "lot_size": 1000,
                "min_order": 1000.0,
                "leverage_max": 30,
                "timezone": "Asia/Tokyo"
            },
            
            # COMMODITIES
            "gold": {
                "display_symbol": "GOLD",
                "asset_class": "COMMODITIES",
                "name": "Gold Spot",
                "providers": {"yahoo_commodities": "GC=F"},
                "broker_symbols": {"primexbt": "XAUUSD"},
                "tick_size": 0.1,
                "lot_size": 1,
                "min_order": 0.1,
                "leverage_max": 20,
                "timezone": "UTC"
            },
            "silver": {
                "display_symbol": "SILVER",
                "asset_class": "COMMODITIES",
                "name": "Silver Spot",
                "providers": {"yahoo_commodities": "SI=F"},
                "broker_symbols": {"primexbt": "XAGUSD"},
                "tick_size": 0.001,
                "lot_size": 1,
                "min_order": 1,
                "leverage_max": 20,
                "timezone": "UTC"
            },
            "crude_oil": {
                "display_symbol": "WTI OIL",
                "asset_class": "COMMODITIES",
                "name": "Crude Oil WTI",
                "providers": {"yahoo_commodities": "CL=F"},
                "broker_symbols": {"primexbt": "WTI"},
                "tick_size": 0.01,
                "lot_size": 1,
                "min_order": 1,
                "leverage_max": 20,
                "timezone": "America/New_York"
            },
            
            # INDICES
            "spx": {
                "display_symbol": "S&P 500",
                "asset_class": "INDICES",
                "name": "S&P 500 Index",
                "providers": {"yahoo_indices": "^GSPC"},
                "broker_symbols": {"primexbt": "SPX"},
                "tick_size": 0.25,
                "lot_size": 1,
                "min_order": 1.0,
                "leverage_max": 20,
                "timezone": "America/New_York"
            },
            "nasdaq": {
                "display_symbol": "NASDAQ 100",
                "asset_class": "INDICES",
                "name": "NASDAQ 100 Index",
                "providers": {"yahoo_indices": "^IXIC"},
                "broker_symbols": {"primexbt": "NDX"},
                "tick_size": 0.25,
                "lot_size": 1,
                "min_order": 1.0,
                "leverage_max": 20,
                "timezone": "America/New_York"
            },
            "dax": {
                "display_symbol": "DAX 40",
                "asset_class": "INDICES",
                "name": "DAX 40 Performance-Index",
                "providers": {"yahoo_indices": "^GDAXI"},
                "broker_symbols": {"primexbt": "GER40"},
                "tick_size": 1.0,
                "lot_size": 1,
                "min_order": 1.0,
                "leverage_max": 20,
                "timezone": "Europe/Berlin"
            }
        }

    def get_all_ids(self) -> List[str]:
        return list(self.universe.keys())

    def get_categories(self) -> List[str]:
        return self.ASSET_CLASSES

    def get_info(self, market_id: str) -> Optional[Dict[str, Any]]:
        return self.universe.get(market_id)

    def get_by_class(self, asset_class: str) -> List[Dict[str, Any]]:
        return [item for item in self.universe.values() if item["asset_class"] == asset_class]

    def get_market_status(self, market_id: str) -> str:
        """
        Rule 11: Determine if market is open based on its timezone and current time.
        """
        info = self.get_info(market_id)
        if not info: return "UNAVAILABLE"
        
        if info["asset_class"] == "CRYPTO":
            return "OPEN"
            
        # Basic check for non-crypto (Simplified for Lot 2)
        tz = pytz.timezone(info.get("timezone", "UTC"))
        now = datetime.now(tz)
        
        # Weekend check
        if now.weekday() >= 5: # Sat/Sun
            return "CLOSED"
            
        # Hour check (9h - 22h local as a safe proxy)
        if 9 <= now.hour < 22:
            return "OPEN"
            
        return "CLOSED"

    def map_to_provider(self, market_id: str, provider_id: str) -> Optional[str]:
        """Rule 14: Map internal ID to provider symbol."""
        info = self.get_info(market_id)
        return info.get("providers", {}).get(provider_id) if info else None

    def map_to_broker(self, market_id: str, broker_id: str) -> Optional[str]:
        """Rule 14: Map internal ID to broker symbol."""
        info = self.get_info(market_id)
        return info.get("broker_symbols", {}).get(broker_id) if info else None
