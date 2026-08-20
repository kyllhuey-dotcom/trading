from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional

class BrokerAdapter(ABC):
    """
    Interface universelle pour les brokers (Rule 29, 60).
    """
    @abstractmethod
    async def connect(self) -> bool:
        pass

    @abstractmethod
    async def get_balance(self) -> float:
        pass

    @abstractmethod
    async def get_positions(self) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def execute_order(self, symbol: str, side: str, quantity: float, sl: float, tp: float) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        pass

    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        pass
