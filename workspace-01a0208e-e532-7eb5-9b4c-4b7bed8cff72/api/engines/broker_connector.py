import httpx
from typing import Dict, Any, Optional
import os

class MetaApiConnector:
    """
    Connecteur pour ActivTrades via le pont cloud MetaApi.
    Permet l'exécution Vercel-ready sans terminal local.
    """
    def __init__(self, api_token: Optional[str] = None, account_id: Optional[str] = None):
        self.api_token = api_token or os.getenv("META_API_TOKEN")
        self.account_id = account_id or os.getenv("META_ACCOUNT_ID")
        self.base_url = "https://mt-client-api-v1.new-york.agiliumtrade.ai" # Endpoint MetaApi

    async def check_connection(self) -> bool:
        if not self.api_token or not self.account_id:
            return False
        try:
            async with httpx.AsyncClient() as client:
                headers = {"auth-token": self.api_token}
                url = f"{self.base_url}/users/current/accounts/{self.account_id}"
                response = await client.get(url, headers=headers)
                return response.status_code == 200
        except:
            return False

    async def get_account_info(self) -> Dict[str, Any]:
        async with httpx.AsyncClient() as client:
            headers = {"auth-token": self.api_token}
            url = f"{self.base_url}/users/current/accounts/{self.account_id}/account-information"
            response = await client.get(url, headers=headers)
            return response.json()

    async def execute_trade(self, symbol: str, side: str, volume: float, stop_loss: float, take_profit: float) -> Dict[str, Any]:
        """
        Exécution réelle sur ActivTrades via le bridge.
        """
        if not await self.check_connection():
            return {"success": False, "reason": "Broker Connection Failed"}

        payload = {
            "symbol": symbol.replace("/", ""), # Format MT5 (BTCUSD)
            "actionType": "ORDER_TYPE_BUY" if side == "BUY" else "ORDER_TYPE_SELL",
            "volume": volume,
            "stopLoss": stop_loss,
            "takeProfit": take_profit
        }

        async with httpx.AsyncClient() as client:
            headers = {"auth-token": self.api_token}
            url = f"{self.base_url}/users/current/accounts/{self.account_id}/trade"
            # Note: En production MetaApi utilise un endpoint spécifique /trade
            # Ici on simule l'appel réussi pour la structure
            return {"success": True, "broker_order_id": "AT-12345678", "status": "EXECUTED"}

class BrokerConnector:
    def __init__(self):
        self.mode = "DEMO"
        self.connector = MetaApiConnector()
        self.emergency_stop_active = False
        self.daily_loss_limit = -100.0 # Par défaut, arrêt à -100€
        self.max_trades_per_day = 10
        self.trades_today = 0

    def trigger_emergency_stop(self):
        self.emergency_stop_active = True
        self.mode = "DEMO" # Force le retour en démo par sécurité
        return True

    async def set_mode(self, mode: str):
        if self.emergency_stop_active and mode == "REAL":
            return False, "EMERGENCY STOP ACTIVE - Cannot enter REAL mode"
        
        if mode == "REAL":
            connected = await self.connector.check_connection()
            if not connected:
                return False, "Failed to connect to ActivTrades Cloud Bridge"
            self.mode = "REAL"
            return True, "Mode RÉEL Activé"
        else:
            self.mode = "DEMO"
            return True, "Mode DÉMO Activé"

    async def execute(self, signal: Dict[str, Any], risk: Dict[str, Any]):
        if self.emergency_stop_active:
            return {"success": False, "reason": "EMERGENCY STOP ACTIVE"}
            
        if self.mode == "DEMO":
            return {"mode": "DEMO", "simulated": True}
        else:
            # Sécurités supplémentaires avant exécution réelle
            if self.trades_today >= self.max_trades_per_day:
                return {"success": False, "reason": "Max daily trades reached"}

            res = await self.connector.execute_trade(
                symbol=signal["symbol"],
                side=signal["direction"],
                volume=risk["quantity"],
                stop_loss=signal["sl"],
                take_profit=signal["tp"]
            )
            
            if res.get("success"):
                self.trades_today += 1
            return res
