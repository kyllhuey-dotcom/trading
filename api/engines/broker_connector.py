from typing import Dict, Any, Optional
from .broker_adapters.ccxt_adapter import CCXTAdapter
from .broker_adapters.primexbt_adapter import PrimeXBTAdapter
from .market_universe import MarketUniverse
import httpx

class BrokerConnector:
    """
    Universal Broker & Web3 Wallet Connector (Lot 26).
    """
    def __init__(self):
        self.universe = MarketUniverse()
        self.active_adapters = {}
        self.web3_wallets = {}
        self.emergency_stop_active = False

    async def initialize_from_db(self, db_manager: Any):
        """Load and connect all active brokers from the database (Lot 4 Encryption)."""
        configs = db_manager.get_active_broker_configs()
        for row in configs:
            await self.add_broker(
                broker_id=row["broker_id"],
                exchange_id=row["exchange_id"],
                api_key=row["api_key"],
                api_secret=row["api_secret"],
                passphrase=row["api_passphrase"]
            )
        # Load Web3 Wallets
        with db_manager._get_connection() as conn:
            w_rows = conn.execute("SELECT * FROM web3_wallets WHERE is_active = 1").fetchall()
            for row in w_rows:
                self.web3_wallets[row["wallet_id"]] = {
                    "provider": row["provider"],
                    "address": row["address"],
                    "network": row["network"]
                }

    async def get_all_balances(self) -> Dict[str, Any]:
        """Aggregate balances from brokers and Web3 wallets."""
        results = {}
        # 1. Brokers
        for bid, adapter in self.active_adapters.items():
            try:
                main_balance = await adapter.get_balance()
                results[bid] = {"type": "BROKER", "exchange": adapter.exchange_id, "total_usdt": main_balance}
            except:
                results[bid] = {"type": "BROKER", "error": "Connection failed"}
        
        # 2. Web3 Wallets (Real Data via Public APIs)
        async with httpx.AsyncClient() as client:
            for wid, wdata in self.web3_wallets.items():
                try:
                    balance = 0.0
                    if wdata["provider"] == "METAMASK":
                        # Fetch real ETH balance (using blockcypher or similar free tier)
                        res = await client.get(f"https://api.blockcypher.com/v1/eth/main/addrs/{wdata['address']}/balance", timeout=5.0)
                        if res.status_code == 200:
                            balance = res.json().get("balance", 0) / 10**18 # Wei to ETH
                    
                    results[wid] = {
                        "type": "WEB3",
                        "provider": wdata["provider"],
                        "address": f"{wdata['address'][:6]}...{wdata['address'][-4:]}",
                        "total_usdt": balance # Simplified to main asset balance
                    }
                except:
                    results[wid] = {"type": "WEB3", "error": "Chain sync error"}
        
        return results

    async def add_broker(self, broker_id: str, exchange_id: str, api_key: str, api_secret: str, passphrase: Optional[str] = None) -> bool:
        """Connect to a new broker via adapter (Lot 4)."""
        if exchange_id.upper() == "PRIMEXBT":
             adapter = PrimeXBTAdapter(api_key, api_secret)
        else:
             adapter = CCXTAdapter(exchange_id, api_key, api_secret, passphrase)
        
        success = await adapter.connect()
        if success:
            self.active_adapters[broker_id] = adapter
        return success

    async def close_all_positions(self):
        """Rule 3: Emergency close all positions across all brokers (Lot 3)."""
        results = {}
        for bid, adapter in self.active_adapters.items():
            try:
                res = await adapter.close_all_positions()
                results[bid] = res
            except Exception as e:
                results[bid] = {"success": False, "error": str(e)}
        return results

    def trigger_emergency_stop(self):
        self.emergency_stop_active = True
        return True

    def reset_emergency_stop(self):
        self.emergency_stop_active = False
        return True

    async def set_mode(self, mode: str):
        if mode == "REAL":
            if self.emergency_stop_active:
                return False, "Emergency Stop active."
            if not self.active_adapters:
                return False, "No active broker connected. Please add a broker in settings."
            return True, "LIVE MODE active (EXPERIMENTAL - Use with caution)."
        return True, "DEMO MODE active."

    async def execute(self, signal: Dict[str, Any], risk: Dict[str, Any]):
        if not self.active_adapters:
            return {"success": False, "reason": "NO_BROKER_CONNECTED"}
        
        # Strategy: Execute on the first available broker for now
        # Institutional improvement: Could select broker based on liquidity or specific asset mapping
        broker_id = list(self.active_adapters.keys())[0]
        adapter = self.active_adapters[broker_id]
        
        market_id = signal.get("market_id")
        broker_symbol = self.universe.map_to_broker(market_id, adapter.exchange_id)
        
        if not broker_symbol:
            return {"success": False, "reason": f"UNSUPPORTED_SYMBOL: {market_id}"}
            
        return await adapter.execute_order(
            symbol=broker_symbol,
            side=signal["direction"].lower(),
            quantity=risk["quantity"],
            sl=signal["sl"],
            tp=signal["tp"]
        )

    def get_status(self) -> Dict[str, Any]:
        return {
            "connected_brokers": list(self.active_adapters.keys()),
            "broker_count": len(self.active_adapters),
            "emergency_stop": self.emergency_stop_active
        }
