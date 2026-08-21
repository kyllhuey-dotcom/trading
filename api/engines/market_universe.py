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
        # Massive Institutional Universe Expansion (Lot 11)
        self.universe = {
            # --- CRYPTO (Top Liquid Assets) ---
            "btc_usdt": {"display_symbol": "BTC/USDT", "asset_class": "CRYPTO", "name": "Bitcoin", "providers": {"gate": "BTC/USDT", "bybit": "BTC/USDT"}, "broker_symbols": {"gate": "BTC/USDT"}, "tick_size": 0.1, "lot_size": 0.0001, "min_order": 10.0, "leverage_max": 100, "timezone": "UTC"},
            "eth_usdt": {"display_symbol": "ETH/USDT", "asset_class": "CRYPTO", "name": "Ethereum", "providers": {"gate": "ETH/USDT", "bybit": "ETH/USDT"}, "broker_symbols": {"gate": "ETH/USDT"}, "tick_size": 0.01, "lot_size": 0.001, "min_order": 10.0, "leverage_max": 100, "timezone": "UTC"},
            "sol_usdt": {"display_symbol": "SOL/USDT", "asset_class": "CRYPTO", "name": "Solana", "providers": {"gate": "SOL/USDT", "bybit": "SOL/USDT"}, "broker_symbols": {"gate": "SOL/USDT"}, "tick_size": 0.001, "lot_size": 0.1, "min_order": 10.0, "leverage_max": 50, "timezone": "UTC"},
            "bnb_usdt": {"display_symbol": "BNB/USDT", "asset_class": "CRYPTO", "name": "Binance Coin", "providers": {"gate": "BNB/USDT", "bybit": "BNB/USDT"}, "broker_symbols": {"gate": "BNB/USDT"}, "tick_size": 0.01, "lot_size": 0.01, "min_order": 10.0, "leverage_max": 50, "timezone": "UTC"},
            "ada_usdt": {"display_symbol": "ADA/USDT", "asset_class": "CRYPTO", "name": "Cardano", "providers": {"gate": "ADA/USDT", "bybit": "ADA/USDT"}, "broker_symbols": {"gate": "ADA/USDT"}, "tick_size": 0.0001, "lot_size": 1.0, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "xrp_usdt": {"display_symbol": "XRP/USDT", "asset_class": "CRYPTO", "name": "Ripple", "providers": {"gate": "XRP/USDT", "bybit": "XRP/USDT"}, "broker_symbols": {"gate": "XRP/USDT"}, "tick_size": 0.0001, "lot_size": 1.0, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "dot_usdt": {"display_symbol": "DOT/USDT", "asset_class": "CRYPTO", "name": "Polkadot", "providers": {"gate": "DOT/USDT", "bybit": "DOT/USDT"}, "broker_symbols": {"gate": "DOT/USDT"}, "tick_size": 0.001, "lot_size": 0.1, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "link_usdt": {"display_symbol": "LINK/USDT", "asset_class": "CRYPTO", "name": "Chainlink", "providers": {"gate": "LINK/USDT", "bybit": "LINK/USDT"}, "broker_symbols": {"gate": "LINK/USDT"}, "tick_size": 0.001, "lot_size": 0.1, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "matic_usdt": {"display_symbol": "MATIC/USDT", "asset_class": "CRYPTO", "name": "Polygon", "providers": {"gate": "MATIC/USDT", "bybit": "MATIC/USDT"}, "broker_symbols": {"gate": "MATIC/USDT"}, "tick_size": 0.0001, "lot_size": 1.0, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "doge_usdt": {"display_symbol": "DOGE/USDT", "asset_class": "CRYPTO", "name": "Dogecoin", "providers": {"gate": "DOGE/USDT", "bybit": "DOGE/USDT"}, "broker_symbols": {"gate": "DOGE/USDT"}, "tick_size": 0.00001, "lot_size": 10.0, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "ltc_usdt": {"display_symbol": "LTC/USDT", "asset_class": "CRYPTO", "name": "Litecoin", "providers": {"gate": "LTC/USDT", "bybit": "LTC/USDT"}, "broker_symbols": {"gate": "LTC/USDT"}, "tick_size": 0.01, "lot_size": 0.1, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "trx_usdt": {"display_symbol": "TRX/USDT", "asset_class": "CRYPTO", "name": "TRON", "providers": {"gate": "TRX/USDT", "bybit": "TRX/USDT"}, "broker_symbols": {"gate": "TRX/USDT"}, "tick_size": 0.00001, "lot_size": 10.0, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "uni_usdt": {"display_symbol": "UNI/USDT", "asset_class": "CRYPTO", "name": "Uniswap", "providers": {"gate": "UNI/USDT", "bybit": "UNI/USDT"}, "broker_symbols": {"gate": "UNI/USDT"}, "tick_size": 0.001, "lot_size": 0.1, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "avax_usdt": {"display_symbol": "AVAX/USDT", "asset_class": "CRYPTO", "name": "Avalanche", "providers": {"gate": "AVAX/USDT", "bybit": "AVAX/USDT"}, "broker_symbols": {"gate": "AVAX/USDT"}, "tick_size": 0.001, "lot_size": 0.1, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "shib_usdt": {"display_symbol": "SHIB/USDT", "asset_class": "CRYPTO", "name": "Shiba Inu", "providers": {"gate": "SHIB/USDT", "bybit": "SHIB/USDT"}, "broker_symbols": {"gate": "SHIB/USDT"}, "tick_size": 0.00000001, "lot_size": 1000.0, "min_order": 10.0, "leverage_max": 10, "timezone": "UTC"},
            "near_usdt": {"display_symbol": "NEAR/USDT", "asset_class": "CRYPTO", "name": "NEAR Protocol", "providers": {"gate": "NEAR/USDT", "bybit": "NEAR/USDT"}, "broker_symbols": {"gate": "NEAR/USDT"}, "tick_size": 0.001, "lot_size": 0.1, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "atom_usdt": {"display_symbol": "ATOM/USDT", "asset_class": "CRYPTO", "name": "Cosmos", "providers": {"gate": "ATOM/USDT", "bybit": "ATOM/USDT"}, "broker_symbols": {"gate": "ATOM/USDT"}, "tick_size": 0.001, "lot_size": 0.1, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},

            # --- FOREX (All Majors & Minors) ---
            "eur_usd": {"display_symbol": "EUR/USD", "asset_class": "FOREX", "name": "Euro / US Dollar", "providers": {"yahoo_forex": "EURUSD=X"}, "broker_symbols": {"gate": "EURUSD"}, "tick_size": 0.00001, "lot_size": 1000, "min_order": 1000.0, "leverage_max": 30, "timezone": "Europe/London"},
            "gbp_usd": {"display_symbol": "GBP/USD", "asset_class": "FOREX", "name": "British Pound / US Dollar", "providers": {"yahoo_forex": "GBPUSD=X"}, "broker_symbols": {"gate": "GBPUSD"}, "tick_size": 0.00001, "lot_size": 1000, "min_order": 1000.0, "leverage_max": 30, "timezone": "Europe/London"},
            "usd_jpy": {"display_symbol": "USD/JPY", "asset_class": "FOREX", "name": "US Dollar / Japanese Yen", "providers": {"yahoo_forex": "JPY=X"}, "broker_symbols": {"gate": "USDJPY"}, "tick_size": 0.001, "lot_size": 1000, "min_order": 1000.0, "leverage_max": 30, "timezone": "Asia/Tokyo"},
            "aud_usd": {"display_symbol": "AUD/USD", "asset_class": "FOREX", "name": "Australian Dollar / US Dollar", "providers": {"yahoo_forex": "AUDUSD=X"}, "broker_symbols": {"gate": "AUDUSD"}, "tick_size": 0.00001, "lot_size": 1000, "min_order": 1000.0, "leverage_max": 30, "timezone": "Australia/Sydney"},
            "usd_cad": {"display_symbol": "USD/CAD", "asset_class": "FOREX", "name": "US Dollar / Canadian Dollar", "providers": {"yahoo_forex": "USDCAD=X"}, "broker_symbols": {"gate": "USDCAD"}, "tick_size": 0.00001, "lot_size": 1000, "min_order": 1000.0, "leverage_max": 30, "timezone": "America/New_York"},
            "usd_chf": {"display_symbol": "USD/CHF", "asset_class": "FOREX", "name": "US Dollar / Swiss Franc", "providers": {"yahoo_forex": "USDCHF=X"}, "broker_symbols": {"gate": "USDCHF"}, "tick_size": 0.00001, "lot_size": 1000, "min_order": 1000.0, "leverage_max": 30, "timezone": "Europe/Zurich"},
            "nzd_usd": {"display_symbol": "NZD/USD", "asset_class": "FOREX", "name": "New Zealand Dollar / US Dollar", "providers": {"yahoo_forex": "NZDUSD=X"}, "broker_symbols": {"gate": "NZDUSD"}, "tick_size": 0.00001, "lot_size": 1000, "min_order": 1000.0, "leverage_max": 30, "timezone": "Pacific/Auckland"},
            "eur_jpy": {"display_symbol": "EUR/JPY", "asset_class": "FOREX", "name": "Euro / Japanese Yen", "providers": {"yahoo_forex": "EURJPY=X"}, "broker_symbols": {"gate": "EURJPY"}, "tick_size": 0.001, "lot_size": 1000, "min_order": 1000.0, "leverage_max": 30, "timezone": "Asia/Tokyo"},
            "gbp_jpy": {"display_symbol": "GBP/JPY", "asset_class": "FOREX", "name": "British Pound / Japanese Yen", "providers": {"yahoo_forex": "GBPJPY=X"}, "broker_symbols": {"gate": "GBPJPY"}, "tick_size": 0.001, "lot_size": 1000, "min_order": 1000.0, "leverage_max": 30, "timezone": "Asia/Tokyo"},
            "eur_gbp": {"display_symbol": "EUR/GBP", "asset_class": "FOREX", "name": "Euro / British Pound", "providers": {"yahoo_forex": "EURGBP=X"}, "broker_symbols": {"gate": "EURGBP"}, "tick_size": 0.00001, "lot_size": 1000, "min_order": 1000.0, "leverage_max": 30, "timezone": "Europe/London"},

            # --- COMMODITIES (Full Spectrum) ---
            "gold": {"display_symbol": "GOLD", "asset_class": "COMMODITIES", "name": "Gold Spot", "providers": {"yahoo_commodities": "GC=F"}, "broker_symbols": {"gate": "XAUUSD"}, "tick_size": 0.1, "lot_size": 1, "min_order": 0.1, "leverage_max": 20, "timezone": "UTC"},
            "silver": {"display_symbol": "SILVER", "asset_class": "COMMODITIES", "name": "Silver Spot", "providers": {"yahoo_commodities": "SI=F"}, "broker_symbols": {"gate": "XAGUSD"}, "tick_size": 0.001, "lot_size": 1, "min_order": 1, "leverage_max": 20, "timezone": "UTC"},
            "crude_oil": {"display_symbol": "WTI OIL", "asset_class": "COMMODITIES", "name": "Crude Oil WTI", "providers": {"yahoo_commodities": "CL=F"}, "broker_symbols": {"gate": "WTI"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1, "leverage_max": 20, "timezone": "America/New_York"},
            "brent_oil": {"display_symbol": "BRENT", "asset_class": "COMMODITIES", "name": "Brent Crude Oil", "providers": {"yahoo_commodities": "BZ=F"}, "broker_symbols": {"gate": "BRENT"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1, "leverage_max": 20, "timezone": "Europe/London"},
            "natural_gas": {"display_symbol": "NATGAS", "asset_class": "COMMODITIES", "name": "Natural Gas", "providers": {"yahoo_commodities": "NG=F"}, "broker_symbols": {"gate": "NATGAS"}, "tick_size": 0.001, "lot_size": 1, "min_order": 1, "leverage_max": 20, "timezone": "America/New_York"},
            "copper": {"display_symbol": "COPPER", "asset_class": "COMMODITIES", "name": "Copper", "providers": {"yahoo_commodities": "HG=F"}, "broker_symbols": {"gate": "COPPER"}, "tick_size": 0.0001, "lot_size": 1, "min_order": 1, "leverage_max": 20, "timezone": "America/New_York"},
            "platinum": {"display_symbol": "PLATINUM", "asset_class": "COMMODITIES", "name": "Platinum", "providers": {"yahoo_commodities": "PL=F"}, "broker_symbols": {"gate": "PLAT"}, "tick_size": 0.1, "lot_size": 1, "min_order": 0.1, "leverage_max": 20, "timezone": "UTC"},
            "corn": {"display_symbol": "CORN", "asset_class": "COMMODITIES", "name": "Corn", "providers": {"yahoo_commodities": "ZC=F"}, "broker_symbols": {"gate": "CORN"}, "tick_size": 0.25, "lot_size": 1, "min_order": 1, "leverage_max": 10, "timezone": "America/Chicago"},
            "wheat": {"display_symbol": "WHEAT", "asset_class": "COMMODITIES", "name": "Wheat", "providers": {"yahoo_commodities": "W=F"}, "broker_symbols": {"gate": "WHEAT"}, "tick_size": 0.25, "lot_size": 1, "min_order": 1, "leverage_max": 10, "timezone": "America/Chicago"},

            # --- INDICES (Global Major Indices) ---
            "spx": {"display_symbol": "S&P 500", "asset_class": "INDICES", "name": "S&P 500", "providers": {"yahoo_indices": "^GSPC"}, "broker_symbols": {"gate": "SPX"}, "tick_size": 0.25, "lot_size": 1, "min_order": 1.0, "leverage_max": 20, "timezone": "America/New_York"},
            "nasdaq": {"display_symbol": "NASDAQ 100", "asset_class": "INDICES", "name": "NASDAQ 100", "providers": {"yahoo_indices": "^IXIC"}, "broker_symbols": {"gate": "NDX"}, "tick_size": 0.25, "lot_size": 1, "min_order": 1.0, "leverage_max": 20, "timezone": "America/New_York"},
            "dax": {"display_symbol": "DAX 40", "asset_class": "INDICES", "name": "DAX 40", "providers": {"yahoo_indices": "^GDAXI"}, "broker_symbols": {"gate": "GER40"}, "tick_size": 1.0, "lot_size": 1, "min_order": 1.0, "leverage_max": 20, "timezone": "Europe/Berlin"},
            "dow_jones": {"display_symbol": "DOW 30", "asset_class": "INDICES", "name": "Dow Jones 30", "providers": {"yahoo_indices": "^DJI"}, "broker_symbols": {"gate": "DOW"}, "tick_size": 1.0, "lot_size": 1, "min_order": 1.0, "leverage_max": 20, "timezone": "America/New_York"},
            "ftse_100": {"display_symbol": "FTSE 100", "asset_class": "INDICES", "name": "FTSE 100", "providers": {"yahoo_indices": "^FTSE"}, "broker_symbols": {"gate": "UK100"}, "tick_size": 0.5, "lot_size": 1, "min_order": 1.0, "leverage_max": 20, "timezone": "Europe/London"},
            "cac_40": {"display_symbol": "CAC 40", "asset_class": "INDICES", "name": "CAC 40", "providers": {"yahoo_indices": "^FCHI"}, "broker_symbols": {"gate": "FRA40"}, "tick_size": 0.5, "lot_size": 1, "min_order": 1.0, "leverage_max": 20, "timezone": "Europe/Paris"},
            "nikkei_225": {"display_symbol": "NIKKEI 225", "asset_class": "INDICES", "name": "Nikkei 225", "providers": {"yahoo_indices": "^N225"}, "broker_symbols": {"gate": "JPN225"}, "tick_size": 1.0, "lot_size": 1, "min_order": 1.0, "leverage_max": 20, "timezone": "Asia/Tokyo"},
            "hsi": {"display_symbol": "HANG SENG", "asset_class": "INDICES", "name": "Hang Seng Index", "providers": {"yahoo_indices": "^HSI"}, "broker_symbols": {"gate": "HK50"}, "tick_size": 1.0, "lot_size": 1, "min_order": 1.0, "leverage_max": 20, "timezone": "Asia/Hong_Kong"},
            "asx_200": {"display_symbol": "ASX 200", "asset_class": "INDICES", "name": "S&P/ASX 200", "providers": {"yahoo_indices": "^AXJO"}, "broker_symbols": {"gate": "AUS200"}, "tick_size": 1.0, "lot_size": 1, "min_order": 1.0, "leverage_max": 20, "timezone": "Australia/Sydney"},
            "rut": {"display_symbol": "RUSSELL 2000", "asset_class": "INDICES", "name": "Russell 2000", "providers": {"yahoo_indices": "^RUT"}, "broker_symbols": {"gate": "RUT"}, "tick_size": 0.1, "lot_size": 1, "min_order": 1.0, "leverage_max": 20, "timezone": "America/New_York"}
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
            
        # Basic check for non-crypto
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
