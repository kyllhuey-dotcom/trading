from typing import Dict, Any, List, Optional
from datetime import datetime
import pytz

class MarketUniverse:
    """
    Market Universe (Rule 5, 12, 13, 14).
    Responsible for discovering instruments, mapping them to providers/brokers,
    and managing their operational status and constraints.
    """
    
    ASSET_CLASSES = ["CRYPTO", "FOREX", "INDICES", "COMMODITIES", "STOCKS", "FUTURES", "BONDS", "ETFS"]
    
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
            "pol_usdt": {"display_symbol": "POL/USDT", "asset_class": "CRYPTO", "name": "Polygon Ecosystem", "providers": {"gate": "POL/USDT", "bybit": "POL/USDT"}, "broker_symbols": {"gate": "POL/USDT"}, "tick_size": 0.0001, "lot_size": 1.0, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "doge_usdt": {"display_symbol": "DOGE/USDT", "asset_class": "CRYPTO", "name": "Dogecoin", "providers": {"gate": "DOGE/USDT", "bybit": "DOGE/USDT"}, "broker_symbols": {"gate": "DOGE/USDT"}, "tick_size": 0.00001, "lot_size": 10.0, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "ltc_usdt": {"display_symbol": "LTC/USDT", "asset_class": "CRYPTO", "name": "Litecoin", "providers": {"gate": "LTC/USDT", "bybit": "LTC/USDT"}, "broker_symbols": {"gate": "LTC/USDT"}, "tick_size": 0.01, "lot_size": 0.1, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "trx_usdt": {"display_symbol": "TRX/USDT", "asset_class": "CRYPTO", "name": "TRON", "providers": {"gate": "TRX/USDT", "bybit": "TRX/USDT"}, "broker_symbols": {"gate": "TRX/USDT"}, "tick_size": 0.00001, "lot_size": 10.0, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "uni_usdt": {"display_symbol": "UNI/USDT", "asset_class": "CRYPTO", "name": "Uniswap", "providers": {"gate": "UNI/USDT", "bybit": "UNI/USDT"}, "broker_symbols": {"gate": "UNI/USDT"}, "tick_size": 0.001, "lot_size": 0.1, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "avax_usdt": {"display_symbol": "AVAX/USDT", "asset_class": "CRYPTO", "name": "Avalanche", "providers": {"gate": "AVAX/USDT", "bybit": "AVAX/USDT"}, "broker_symbols": {"gate": "AVAX/USDT"}, "tick_size": 0.001, "lot_size": 0.1, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "shib_usdt": {"display_symbol": "SHIB/USDT", "asset_class": "CRYPTO", "name": "Shiba Inu", "providers": {"gate": "SHIB/USDT", "bybit": "SHIB/USDT"}, "broker_symbols": {"gate": "SHIB/USDT"}, "tick_size": 0.00000001, "lot_size": 1000.0, "min_order": 10.0, "leverage_max": 10, "timezone": "UTC"},
            "near_usdt": {"display_symbol": "NEAR/USDT", "asset_class": "CRYPTO", "name": "NEAR Protocol", "providers": {"gate": "NEAR/USDT", "bybit": "NEAR/USDT"}, "broker_symbols": {"gate": "NEAR/USDT"}, "tick_size": 0.001, "lot_size": 0.1, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "atom_usdt": {"display_symbol": "ATOM/USDT", "asset_class": "CRYPTO", "name": "Cosmos", "providers": {"gate": "ATOM/USDT", "bybit": "ATOM/USDT"}, "broker_symbols": {"gate": "ATOM/USDT"}, "tick_size": 0.001, "lot_size": 0.1, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "pepe_usdt": {"display_symbol": "PEPE/USDT", "asset_class": "CRYPTO", "name": "Pepe", "providers": {"gate": "PEPE/USDT", "bybit": "PEPE/USDT"}, "broker_symbols": {"gate": "PEPE/USDT"}, "tick_size": 0.00000001, "lot_size": 1000.0, "min_order": 10.0, "leverage_max": 10, "timezone": "UTC"},
            "fil_usdt": {"display_symbol": "FIL/USDT", "asset_class": "CRYPTO", "name": "Filecoin", "providers": {"gate": "FIL/USDT", "bybit": "FIL/USDT"}, "broker_symbols": {"gate": "FIL/USDT"}, "tick_size": 0.001, "lot_size": 0.1, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "apt_usdt": {"display_symbol": "APT/USDT", "asset_class": "CRYPTO", "name": "Aptos", "providers": {"gate": "APT/USDT", "bybit": "APT/USDT"}, "broker_symbols": {"gate": "APT/USDT"}, "tick_size": 0.001, "lot_size": 0.1, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "arb_usdt": {"display_symbol": "ARB/USDT", "asset_class": "CRYPTO", "name": "Arbitrum", "providers": {"gate": "ARB/USDT", "bybit": "ARB/USDT"}, "broker_symbols": {"gate": "ARB/USDT"}, "tick_size": 0.0001, "lot_size": 1.0, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "op_usdt": {"display_symbol": "OP/USDT", "asset_class": "CRYPTO", "name": "Optimism", "providers": {"gate": "OP/USDT", "bybit": "OP/USDT"}, "broker_symbols": {"gate": "OP/USDT"}, "tick_size": 0.0001, "lot_size": 1.0, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "fet_usdt": {"display_symbol": "FET/USDT", "asset_class": "CRYPTO", "name": "Fetch.ai", "providers": {"gate": "FET/USDT", "bybit": "FET/USDT"}, "broker_symbols": {"gate": "FET/USDT"}, "tick_size": 0.0001, "lot_size": 1.0, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "tia_usdt": {"display_symbol": "TIA/USDT", "asset_class": "CRYPTO", "name": "Celestia", "providers": {"gate": "TIA/USDT", "bybit": "TIA/USDT"}, "broker_symbols": {"gate": "TIA/USDT"}, "tick_size": 0.001, "lot_size": 0.1, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "sei_usdt": {"display_symbol": "SEI/USDT", "asset_class": "CRYPTO", "name": "Sei Network", "providers": {"gate": "SEI/USDT", "bybit": "SEI/USDT"}, "broker_symbols": {"gate": "SEI/USDT"}, "tick_size": 0.0001, "lot_size": 1.0, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "jup_usdt": {"display_symbol": "JUP/USDT", "asset_class": "CRYPTO", "name": "Jupiter", "providers": {"gate": "JUP/USDT", "bybit": "JUP/USDT"}, "broker_symbols": {"gate": "JUP/USDT"}, "tick_size": 0.0001, "lot_size": 1.0, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "pyth_usdt": {"display_symbol": "PYTH/USDT", "asset_class": "CRYPTO", "name": "Pyth Network", "providers": {"gate": "PYTH/USDT", "bybit": "PYTH/USDT"}, "broker_symbols": {"gate": "PYTH/USDT"}, "tick_size": 0.0001, "lot_size": 1.0, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "stx_usdt": {"display_symbol": "STX/USDT", "asset_class": "CRYPTO", "name": "Stacks", "providers": {"gate": "STX/USDT", "bybit": "STX/USDT"}, "broker_symbols": {"gate": "STX/USDT"}, "tick_size": 0.001, "lot_size": 0.1, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "steth_usdt": {"display_symbol": "stETH/USDT", "asset_class": "CRYPTO", "name": "Lido Staked ETH", "providers": {"gate": "STETH/USDT"}, "broker_symbols": {"gate": "STETH/USDT"}, "tick_size": 0.01, "lot_size": 0.001, "min_order": 10.0, "leverage_max": 10, "timezone": "UTC"},
            "render_usdt": {"display_symbol": "RENDER/USDT", "asset_class": "CRYPTO", "name": "Render Token", "providers": {"gate": "RENDER/USDT"}, "broker_symbols": {"gate": "RENDER/USDT"}, "tick_size": 0.001, "lot_size": 0.1, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "kas_usdt": {"display_symbol": "KAS/USDT", "asset_class": "CRYPTO", "name": "Kaspa", "providers": {"gate": "KAS/USDT"}, "broker_symbols": {"gate": "KAS/USDT"}, "tick_size": 0.00001, "lot_size": 10.0, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "sui_usdt": {"display_symbol": "SUI/USDT", "asset_class": "CRYPTO", "name": "Sui", "providers": {"gate": "SUI/USDT"}, "broker_symbols": {"gate": "SUI/USDT"}, "tick_size": 0.0001, "lot_size": 1.0, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "tao_usdt": {"display_symbol": "TAO/USDT", "asset_class": "CRYPTO", "name": "Bittensor", "providers": {"gate": "TAO/USDT"}, "broker_symbols": {"gate": "TAO/USDT"}, "tick_size": 0.01, "lot_size": 0.001, "min_order": 10.0, "leverage_max": 10, "timezone": "UTC"},
            "xlm_usdt": {"display_symbol": "XLM/USDT", "asset_class": "CRYPTO", "name": "Stellar", "providers": {"gate": "XLM/USDT"}, "broker_symbols": {"gate": "XLM/USDT"}, "tick_size": 0.0001, "lot_size": 1.0, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "etc_usdt": {"display_symbol": "ETC/USDT", "asset_class": "CRYPTO", "name": "Ethereum Classic", "providers": {"gate": "ETC/USDT"}, "broker_symbols": {"gate": "ETC/USDT"}, "tick_size": 0.01, "lot_size": 0.1, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "bch_usdt": {"display_symbol": "BCH/USDT", "asset_class": "CRYPTO", "name": "Bitcoin Cash", "providers": {"gate": "BCH/USDT"}, "broker_symbols": {"gate": "BCH/USDT"}, "tick_size": 0.1, "lot_size": 0.01, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},

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
            "eur_chf": {"display_symbol": "EUR/CHF", "asset_class": "FOREX", "name": "Euro / Swiss Franc", "providers": {"yahoo_forex": "EURCHF=X"}, "broker_symbols": {"gate": "EURCHF"}, "tick_size": 0.00001, "lot_size": 1000, "min_order": 1000.0, "leverage_max": 30, "timezone": "Europe/Zurich"},
            "eur_aud": {"display_symbol": "EUR/AUD", "asset_class": "FOREX", "name": "Euro / Australian Dollar", "providers": {"yahoo_forex": "EURAUD=X"}, "broker_symbols": {"gate": "EURAUD"}, "tick_size": 0.00001, "lot_size": 1000, "min_order": 1000.0, "leverage_max": 30, "timezone": "Australia/Sydney"},
            "gbp_chf": {"display_symbol": "GBP/CHF", "asset_class": "FOREX", "name": "British Pound / Swiss Franc", "providers": {"yahoo_forex": "GBPCHF=X"}, "broker_symbols": {"gate": "GBPCHF"}, "tick_size": 0.00001, "lot_size": 1000, "min_order": 1000.0, "leverage_max": 30, "timezone": "Europe/Zurich"},
            "cad_jpy": {"display_symbol": "CAD/JPY", "asset_class": "FOREX", "name": "Canadian Dollar / Japanese Yen", "providers": {"yahoo_forex": "CADJPY=X"}, "broker_symbols": {"gate": "CADJPY"}, "tick_size": 0.001, "lot_size": 1000, "min_order": 1000.0, "leverage_max": 30, "timezone": "Asia/Tokyo"},
            "aud_jpy": {"display_symbol": "AUD/JPY", "asset_class": "FOREX", "name": "Australian Dollar / Japanese Yen", "providers": {"yahoo_forex": "AUDJPY=X"}, "broker_symbols": {"gate": "AUDJPY"}, "tick_size": 0.001, "lot_size": 1000, "min_order": 1000.0, "leverage_max": 30, "timezone": "Asia/Tokyo"},

            # --- COMMODITIES (Full Spectrum) ---
            "gold": {"display_symbol": "GOLD", "asset_class": "COMMODITIES", "name": "Gold Spot", "providers": {"yahoo_commodities": "GC=F"}, "broker_symbols": {"gate": "XAUUSD"}, "tick_size": 0.1, "lot_size": 1, "min_order": 0.1, "leverage_max": 20, "timezone": "UTC"},
            "silver": {"display_symbol": "SILVER", "asset_class": "COMMODITIES", "name": "Silver Spot", "providers": {"yahoo_commodities": "SI=F"}, "broker_symbols": {"gate": "XAGUSD"}, "tick_size": 0.001, "lot_size": 1, "min_order": 1, "leverage_max": 20, "timezone": "UTC"},
            "crude_oil": {"display_symbol": "WTI OIL", "asset_class": "COMMODITIES", "name": "Crude Oil WTI", "providers": {"yahoo_commodities": "CL=F"}, "broker_symbols": {"gate": "WTI"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1, "leverage_max": 20, "timezone": "America/New_York"},
            "brent_oil": {"display_symbol": "BRENT", "asset_class": "COMMODITIES", "name": "Brent Crude Oil", "providers": {"yahoo_commodities": "BZ=F"}, "broker_symbols": {"gate": "BRENT"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1, "leverage_max": 20, "timezone": "Europe/London"},
            "natural_gas": {"display_symbol": "NATGAS", "asset_class": "COMMODITIES", "name": "Natural Gas", "providers": {"yahoo_commodities": "NG=F"}, "broker_symbols": {"gate": "NATGAS"}, "tick_size": 0.001, "lot_size": 1, "min_order": 1, "leverage_max": 20, "timezone": "America/New_York"},
            "copper": {"display_symbol": "COPPER", "asset_class": "COMMODITIES", "name": "Copper", "providers": {"yahoo_commodities": "HG=F"}, "broker_symbols": {"gate": "COPPER"}, "tick_size": 0.0001, "lot_size": 1, "min_order": 1, "leverage_max": 20, "timezone": "America/New_York"},
            "platinum": {"display_symbol": "PLATINUM", "asset_class": "COMMODITIES", "name": "Platinum", "providers": {"yahoo_commodities": "PL=F"}, "broker_symbols": {"gate": "PLAT"}, "tick_size": 0.1, "lot_size": 1, "min_order": 0.1, "leverage_max": 20, "timezone": "UTC"},
            "corn": {"display_symbol": "CORN", "asset_class": "COMMODITIES", "name": "Corn", "providers": {"yahoo_commodities": "ZC=F"}, "broker_symbols": {"gate": "CORN"}, "tick_size": 0.25, "lot_size": 1, "min_order": 1, "leverage_max": 10, "timezone": "America/Chicago"},
            "soybeans": {"display_symbol": "SOYBEANS", "asset_class": "COMMODITIES", "name": "Soybeans", "providers": {"yahoo_commodities": "ZS=F"}, "broker_symbols": {"gate": "SOYBEANS"}, "tick_size": 0.25, "lot_size": 1, "min_order": 1, "leverage_max": 10, "timezone": "America/Chicago"},
            "sugar": {"display_symbol": "SUGAR", "asset_class": "COMMODITIES", "name": "Sugar", "providers": {"yahoo_commodities": "SB=F"}, "broker_symbols": {"gate": "SUGAR"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1, "leverage_max": 10, "timezone": "America/New_York"},
            "coffee": {"display_symbol": "COFFEE", "asset_class": "COMMODITIES", "name": "Coffee", "providers": {"yahoo_commodities": "KC=F"}, "broker_symbols": {"gate": "COFFEE"}, "tick_size": 0.05, "lot_size": 1, "min_order": 1, "leverage_max": 10, "timezone": "America/New_York"},
            "cotton": {"display_symbol": "COTTON", "asset_class": "COMMODITIES", "name": "Cotton", "providers": {"yahoo_commodities": "CT=F"}, "broker_symbols": {"gate": "COTTON"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1, "leverage_max": 10, "timezone": "America/New_York"},
            "cocoa": {"display_symbol": "COCOA", "asset_class": "COMMODITIES", "name": "Cocoa", "providers": {"yahoo_commodities": "CC=F"}, "broker_symbols": {"gate": "COCOA"}, "tick_size": 1.0, "lot_size": 1, "min_order": 1, "leverage_max": 10, "timezone": "America/New_York"},
            "palladium": {"display_symbol": "PALLADIUM", "asset_class": "COMMODITIES", "name": "Palladium", "providers": {"yahoo_commodities": "PA=F"}, "broker_symbols": {"gate": "PALL"}, "tick_size": 0.1, "lot_size": 1, "min_order": 0.1, "leverage_max": 10, "timezone": "UTC"},
            "live_cattle": {"display_symbol": "CATTLE", "asset_class": "COMMODITIES", "name": "Live Cattle", "providers": {"yahoo_commodities": "LE=F"}, "broker_symbols": {"gate": "CATTLE"}, "tick_size": 0.025, "lot_size": 1, "min_order": 1, "leverage_max": 10, "timezone": "America/Chicago"},
            "aluminum": {"display_symbol": "ALUMINUM", "asset_class": "COMMODITIES", "name": "Aluminum", "providers": {"yahoo_commodities": "ALI=F"}, "broker_symbols": {"gate": "ALUM"}, "tick_size": 0.5, "lot_size": 1, "min_order": 1, "leverage_max": 10, "timezone": "Europe/London"},
            "zinc": {"display_symbol": "ZINC", "asset_class": "COMMODITIES", "name": "Zinc", "providers": {"yahoo_commodities": "ZNC=F"}, "broker_symbols": {"gate": "ZINC"}, "tick_size": 0.5, "lot_size": 1, "min_order": 1, "leverage_max": 10, "timezone": "Europe/London"},
            # LUMBER (LBS=F) was delisted, replaced by LBR=F
            "lumber": {"display_symbol": "LUMBER", "asset_class": "COMMODITIES", "name": "Lumber", "providers": {"yahoo_commodities": "LBR=F"}, "broker_symbols": {"gate": "LUMBER"}, "tick_size": 0.1, "lot_size": 1, "min_order": 1, "leverage_max": 10, "timezone": "America/Chicago"},
            "orange_juice": {"display_symbol": "ORANGE JUICE", "asset_class": "COMMODITIES", "name": "Orange Juice", "providers": {"yahoo_commodities": "OJ=F"}, "broker_symbols": {"gate": "OJ"}, "tick_size": 0.05, "lot_size": 1, "min_order": 1, "leverage_max": 10, "timezone": "America/New_York"},
            "lean_hogs": {"display_symbol": "HOGS", "asset_class": "COMMODITIES", "name": "Lean Hogs", "providers": {"yahoo_commodities": "HE=F"}, "broker_symbols": {"gate": "HOGS"}, "tick_size": 0.025, "lot_size": 1, "min_order": 1, "leverage_max": 10, "timezone": "America/Chicago"},
            "feeder_cattle": {"display_symbol": "FEEDER CATTLE", "asset_class": "COMMODITIES", "name": "Feeder Cattle", "providers": {"yahoo_commodities": "GF=F"}, "broker_symbols": {"gate": "FCATTLE"}, "tick_size": 0.025, "lot_size": 1, "min_order": 1, "leverage_max": 10, "timezone": "America/Chicago"},

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
            "rut": {"display_symbol": "RUSSELL 2000", "asset_class": "INDICES", "name": "Russell 2000", "providers": {"yahoo_indices": "^RUT"}, "broker_symbols": {"gate": "RUT"}, "tick_size": 0.1, "lot_size": 1, "min_order": 1.0, "leverage_max": 20, "timezone": "America/New_York"},
            "ibex": {"display_symbol": "IBEX 35", "asset_class": "INDICES", "name": "IBEX 35", "providers": {"yahoo_indices": "^IBEX"}, "broker_symbols": {"gate": "IBEX"}, "tick_size": 1.0, "lot_size": 1, "min_order": 1.0, "leverage_max": 20, "timezone": "Europe/Madrid"},
            "tsx": {"display_symbol": "TSX COMPOSITE", "asset_class": "INDICES", "name": "S&P/TSX Composite", "providers": {"yahoo_indices": "^GSPTSE"}, "broker_symbols": {"gate": "TSX"}, "tick_size": 1.0, "lot_size": 1, "min_order": 1.0, "leverage_max": 20, "timezone": "America/Toronto"},
            "smi": {"display_symbol": "SMI", "asset_class": "INDICES", "name": "Swiss Market Index", "providers": {"yahoo_indices": "^SSMI"}, "broker_symbols": {"gate": "SMI"}, "tick_size": 1.0, "lot_size": 1, "min_order": 1.0, "leverage_max": 20, "timezone": "Europe/Zurich"},
            "vix": {"display_symbol": "VIX", "asset_class": "INDICES", "name": "VIX Volatility Index", "providers": {"yahoo_indices": "^VIX"}, "broker_symbols": {"gate": "VIX"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 1, "timezone": "America/New_York"},
            "stoxx50": {"display_symbol": "EURO STOXX 50", "asset_class": "INDICES", "name": "EURO STOXX 50", "providers": {"yahoo_indices": "^STOXX50E"}, "broker_symbols": {"gate": "EU50"}, "tick_size": 1.0, "lot_size": 1, "min_order": 1.0, "leverage_max": 20, "timezone": "Europe/Luxembourg"},

            # --- STOCKS (US Tech & Global Blue Chips) ---
            "aapl": {"display_symbol": "AAPL", "asset_class": "STOCKS", "name": "Apple Inc.", "providers": {"yahoo_stocks": "AAPL"}, "broker_symbols": {"gate": "AAPL"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 5, "timezone": "America/New_York"},
            "tsla": {"display_symbol": "TSLA", "asset_class": "STOCKS", "name": "Tesla, Inc.", "providers": {"yahoo_stocks": "TSLA"}, "broker_symbols": {"gate": "TSLA"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 5, "timezone": "America/New_York"},
            "nvda": {"display_symbol": "NVDA", "asset_class": "STOCKS", "name": "NVIDIA Corporation", "providers": {"yahoo_stocks": "NVDA"}, "broker_symbols": {"gate": "NVDA"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 5, "timezone": "America/New_York"},
            "msft": {"display_symbol": "MSFT", "asset_class": "STOCKS", "name": "Microsoft Corporation", "providers": {"yahoo_stocks": "MSFT"}, "broker_symbols": {"gate": "MSFT"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 5, "timezone": "America/New_York"},
            "amzn": {"display_symbol": "AMZN", "asset_class": "STOCKS", "name": "Amazon.com, Inc.", "providers": {"yahoo_stocks": "AMZN"}, "broker_symbols": {"gate": "AMZN"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 5, "timezone": "America/New_York"},
            "googl": {"display_symbol": "GOOGL", "asset_class": "STOCKS", "name": "Alphabet Inc.", "providers": {"yahoo_stocks": "GOOGL"}, "broker_symbols": {"gate": "GOOGL"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 5, "timezone": "America/New_York"},
            "meta": {"display_symbol": "META", "asset_class": "STOCKS", "name": "Meta Platforms", "providers": {"yahoo_stocks": "META"}, "broker_symbols": {"gate": "META"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 5, "timezone": "America/New_York"},
            "nflx": {"display_symbol": "NFLX", "asset_class": "STOCKS", "name": "Netflix, Inc.", "providers": {"yahoo_stocks": "NFLX"}, "broker_symbols": {"gate": "NFLX"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 5, "timezone": "America/New_York"},
            "amd": {"display_symbol": "AMD", "asset_class": "STOCKS", "name": "AMD", "providers": {"yahoo_stocks": "AMD"}, "broker_symbols": {"gate": "AMD"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 5, "timezone": "America/New_York"},
            "dis": {"display_symbol": "DIS", "asset_class": "STOCKS", "name": "Disney", "providers": {"yahoo_stocks": "DIS"}, "broker_symbols": {"gate": "DIS"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 5, "timezone": "America/New_York"},
            "pypl": {"display_symbol": "PYPL", "asset_class": "STOCKS", "name": "PayPal", "providers": {"yahoo_stocks": "PYPL"}, "broker_symbols": {"gate": "PYPL"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 5, "timezone": "America/New_York"},
            "uber": {"display_symbol": "UBER", "asset_class": "STOCKS", "name": "Uber", "providers": {"yahoo_stocks": "UBER"}, "broker_symbols": {"gate": "UBER"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 5, "timezone": "America/New_York"},
            "coin": {"display_symbol": "COIN", "asset_class": "STOCKS", "name": "Coinbase", "providers": {"yahoo_stocks": "COIN"}, "broker_symbols": {"gate": "COIN"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 5, "timezone": "America/New_York"},
            "asml": {"display_symbol": "ASML", "asset_class": "STOCKS", "name": "ASML Holding N.V.", "providers": {"yahoo_stocks": "ASML"}, "broker_symbols": {"gate": "ASML"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 5, "timezone": "Europe/Amsterdam"},
            "mc_pa": {"display_symbol": "LVMH", "asset_class": "STOCKS", "name": "LVMH", "providers": {"yahoo_stocks": "MC.PA"}, "broker_symbols": {"gate": "MC"}, "tick_size": 0.1, "lot_size": 1, "min_order": 1.0, "leverage_max": 5, "timezone": "Europe/Paris"},
            "sap_de": {"display_symbol": "SAP", "asset_class": "STOCKS", "name": "SAP SE", "providers": {"yahoo_stocks": "SAP.DE"}, "broker_symbols": {"gate": "SAP"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 5, "timezone": "Europe/Berlin"},
            "air_pa": {"display_symbol": "AIRBUS", "asset_class": "STOCKS", "name": "Airbus SE", "providers": {"yahoo_stocks": "AIR.PA"}, "broker_symbols": {"gate": "AIR"}, "tick_size": 0.02, "lot_size": 1, "min_order": 1.0, "leverage_max": 5, "timezone": "Europe/Paris"},

            # --- FUTURES (Continuous Contracts) ---
            "es_f": {"display_symbol": "E-MINI S&P 500", "asset_class": "FUTURES", "name": "S&P 500 Futures", "providers": {"yahoo_futures": "ES=F"}, "broker_symbols": {"gate": "ES"}, "tick_size": 0.25, "lot_size": 1, "min_order": 1.0, "leverage_max": 50, "timezone": "America/New_York"},
            "nq_f": {"display_symbol": "NASDAQ 100 FUT", "asset_class": "FUTURES", "name": "Nasdaq 100 Futures", "providers": {"yahoo_futures": "NQ=F"}, "broker_symbols": {"gate": "NQ"}, "tick_size": 0.25, "lot_size": 1, "min_order": 1.0, "leverage_max": 50, "timezone": "America/New_York"},
            "ym_f": {"display_symbol": "DOW FUTURES", "asset_class": "FUTURES", "name": "Dow Jones Futures", "providers": {"yahoo_futures": "YM=F"}, "broker_symbols": {"gate": "YM"}, "tick_size": 1.0, "lot_size": 1, "min_order": 1.0, "leverage_max": 50, "timezone": "America/New_York"},
            "cl_f": {"display_symbol": "CRUDE OIL FUT", "asset_class": "FUTURES", "name": "WTI Oil Futures", "providers": {"yahoo_futures": "CL=F"}, "broker_symbols": {"gate": "CL"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 20, "timezone": "America/New_York"},
            "gc_f": {"display_symbol": "GOLD FUTURES", "asset_class": "FUTURES", "name": "Gold Futures", "providers": {"yahoo_futures": "GC=F"}, "broker_symbols": {"gate": "GC"}, "tick_size": 0.1, "lot_size": 1, "min_order": 0.1, "leverage_max": 20, "timezone": "UTC"},
            "btc_f": {"display_symbol": "BTC PERP", "asset_class": "FUTURES", "name": "Bitcoin Perpetual", "providers": {"gate": "BTC_USDT"}, "broker_symbols": {"gate": "BTC_USDT"}, "tick_size": 0.1, "lot_size": 0.0001, "min_order": 10.0, "leverage_max": 100, "timezone": "UTC"},
            "eth_f": {"display_symbol": "ETH PERP", "asset_class": "FUTURES", "name": "Ethereum Perpetual", "providers": {"gate": "ETH_USDT"}, "broker_symbols": {"gate": "ETH_USDT"}, "tick_size": 0.01, "lot_size": 0.001, "min_order": 10.0, "leverage_max": 100, "timezone": "UTC"},

            # --- BONDS (Treasuries & Yields) ---
            "tnx": {"display_symbol": "10Y TREASURY", "asset_class": "BONDS", "name": "US 10Y Yield", "providers": {"yahoo_bonds": "^TNX"}, "broker_symbols": {"gate": "TNX"}, "tick_size": 0.001, "lot_size": 1, "min_order": 1.0, "leverage_max": 1, "timezone": "America/New_York"},
            "tyx": {"display_symbol": "30Y TREASURY", "asset_class": "BONDS", "name": "US 30Y Yield", "providers": {"yahoo_bonds": "^TYX"}, "broker_symbols": {"gate": "TYX"}, "tick_size": 0.001, "lot_size": 1, "min_order": 1.0, "leverage_max": 1, "timezone": "America/New_York"},
            "fvx": {"display_symbol": "5Y TREASURY", "asset_class": "BONDS", "name": "US 5Y Yield", "providers": {"yahoo_bonds": "^FVX"}, "broker_symbols": {"gate": "FVX"}, "tick_size": 0.001, "lot_size": 1, "min_order": 1.0, "leverage_max": 1, "timezone": "America/New_York"},
            "tlt": {"display_symbol": "TLT BOND ETF", "asset_class": "BONDS", "name": "iShares 20+ Year Treasury", "providers": {"yahoo_bonds": "TLT"}, "broker_symbols": {"gate": "TLT"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 10, "timezone": "America/New_York"},
            "ief": {"display_symbol": "7-10Y BOND ETF", "asset_class": "BONDS", "name": "iShares 7-10 Year Treasury", "providers": {"yahoo_bonds": "IEF"}, "broker_symbols": {"gate": "IEF"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 10, "timezone": "America/New_York"},
            "lqd": {"display_symbol": "CORP BOND ETF", "asset_class": "BONDS", "name": "iBoxx $ Invest Grade Corp Bond", "providers": {"yahoo_bonds": "LQD"}, "broker_symbols": {"gate": "LQD"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 10, "timezone": "America/New_York"},

            # --- ETFS (Thematic & Sectoral) ---
            "spy": {"display_symbol": "SPY", "asset_class": "ETFS", "name": "SPDR S&P 500 ETF Trust", "providers": {"yahoo_etfs": "SPY"}, "broker_symbols": {"gate": "SPY"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 5, "timezone": "America/New_York"},
            "qqq": {"display_symbol": "QQQ", "asset_class": "ETFS", "name": "Invesco QQQ Trust", "providers": {"yahoo_etfs": "QQQ"}, "broker_symbols": {"gate": "QQQ"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 5, "timezone": "America/New_York"},
            "smh": {"display_symbol": "SMH SEMI", "asset_class": "ETFS", "name": "VanEck Semiconductor ETF", "providers": {"yahoo_etfs": "SMH"}, "broker_symbols": {"gate": "SMH"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 5, "timezone": "America/New_York"},
            "arkk": {"display_symbol": "ARKK", "asset_class": "ETFS", "name": "ARK Innovation ETF", "providers": {"yahoo_etfs": "ARKK"}, "broker_symbols": {"gate": "ARKK"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 5, "timezone": "America/New_York"},
            "gld": {"display_symbol": "GLD GOLD ETF", "asset_class": "ETFS", "name": "SPDR Gold Shares", "providers": {"yahoo_etfs": "GLD"}, "broker_symbols": {"gate": "GLD"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 5, "timezone": "America/New_York"},
            "uso": {"display_symbol": "USO OIL ETF", "asset_class": "ETFS", "name": "United States Oil Fund", "providers": {"yahoo_etfs": "USO"}, "broker_symbols": {"gate": "USO"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 5, "timezone": "America/New_York"},
            "dia": {"display_symbol": "DIA DOW ETF", "asset_class": "ETFS", "name": "SPDR Dow Jones Industrial Average", "providers": {"yahoo_etfs": "DIA"}, "broker_symbols": {"gate": "DIA"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 5, "timezone": "America/New_York"},
            "iwm": {"display_symbol": "IWM RUSSELL", "asset_class": "ETFS", "name": "iShares Russell 2000 ETF", "providers": {"yahoo_etfs": "IWM"}, "broker_symbols": {"gate": "IWM"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 5, "timezone": "America/New_York"},
            "xle": {"display_symbol": "XLE ENERGY", "asset_class": "ETFS", "name": "Energy Select Sector SPDR", "providers": {"yahoo_etfs": "XLE"}, "broker_symbols": {"gate": "XLE"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 5, "timezone": "America/New_York"},
            "xlf": {"display_symbol": "XLF FIN", "asset_class": "ETFS", "name": "Financial Select Sector SPDR", "providers": {"yahoo_etfs": "XLF"}, "broker_symbols": {"gate": "XLF"}, "tick_size": 0.01, "lot_size": 1, "min_order": 1.0, "leverage_max": 5, "timezone": "America/New_York"}
        }

        # v2.6: remove three duplicate contracts that represented an already
        # tracked underlying (gold/gc_f, spx/es_f, nasdaq/nq_f).  Replace them
        # with liquid, independent crypto underlyings so the universe remains
        # 127 instruments without showing or trading the same exposure twice.
        for duplicate_id in ("gc_f", "es_f", "nq_f"):
            self.universe.pop(duplicate_id, None)
        self.universe.update({
            "inj_usdt": {"display_symbol": "INJ/USDT", "asset_class": "CRYPTO", "name": "Injective", "providers": {"gate": "INJ/USDT", "bybit": "INJ/USDT"}, "broker_symbols": {"gate": "INJ/USDT"}, "tick_size": 0.001, "lot_size": 0.1, "min_order": 10.0, "leverage_max": 20, "timezone": "UTC"},
            "wif_usdt": {"display_symbol": "WIF/USDT", "asset_class": "CRYPTO", "name": "dogwifhat", "providers": {"gate": "WIF/USDT", "bybit": "WIF/USDT"}, "broker_symbols": {"gate": "WIF/USDT"}, "tick_size": 0.0001, "lot_size": 1.0, "min_order": 10.0, "leverage_max": 10, "timezone": "UTC"},
            "ondo_usdt": {"display_symbol": "ONDO/USDT", "asset_class": "CRYPTO", "name": "Ondo", "providers": {"gate": "ONDO/USDT", "bybit": "ONDO/USDT"}, "broker_symbols": {"gate": "ONDO/USDT"}, "tick_size": 0.0001, "lot_size": 1.0, "min_order": 10.0, "leverage_max": 10, "timezone": "UTC"},
        })
        for market_id, instrument in self.universe.items():
            instrument.setdefault("underlying", market_id)

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
        Rule 11: Determine if market is open based on its specific hours and timezone.
        """
        info = self.get_info(market_id)
        if not info:
            return "UNAVAILABLE"
        
        asset_class = info.get("asset_class")
        if asset_class == "CRYPTO":
            return "OPEN"
            
        tz = pytz.timezone(info.get("timezone", "UTC"))
        now = datetime.now(tz)
        weekday = now.weekday() # Mon=0, Sun=6
        hour = now.hour
        minute = now.minute
        time_float = hour + minute / 60.0

        if asset_class == "FOREX":
            # Forex is 24/5: Opens Sunday 22:00 GMT, Closes Friday 22:00 GMT
            # Simplifying for local timezone (approximate)
            if weekday == 5: # Saturday: Closed
                return "CLOSED"
            if weekday == 6 and hour < 22: # Sunday before 22h: Closed
                return "CLOSED"
            if weekday == 4 and hour >= 22: # Friday after 22h: Closed
                return "CLOSED"
            return "OPEN"
            
        if asset_class == "STOCKS" or asset_class == "INDICES":
            # US Markets: 9:30 AM - 16:00 PM
            if "America/New_York" in info.get("timezone", ""):
                if weekday >= 5:
                    return "CLOSED"
                if 9.5 <= time_float < 16.0:
                    return "OPEN"
                return "CLOSED"
            # European Markets: 9:00 AM - 17:30 PM
            if "Europe" in info.get("timezone", ""):
                if weekday >= 5:
                    return "CLOSED"
                if 9.0 <= time_float < 17.5:
                    return "OPEN"
                return "CLOSED"
            # Asian Markets: Approximate
            if "Asia" in info.get("timezone", ""):
                if weekday >= 5:
                    return "CLOSED"
                if 9.0 <= hour < 15:
                    return "OPEN"  # Simple proxy for Nikkei/HSI
                return "CLOSED"

        if asset_class == "COMMODITIES":
            # Most commodities: 23 hours a day, closed on weekends
            if weekday >= 5:
                return "CLOSED"
            if hour == 17:
                return "CLOSED"  # Daily break
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
