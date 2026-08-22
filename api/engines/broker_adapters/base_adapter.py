from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional


class BrokerAdapter(ABC):
    """Universal broker interface (all execution targets must implement it)."""

    @abstractmethod
    async def connect(self) -> bool:
        """Connect and validate credentials."""

    @abstractmethod
    async def get_balance(self, asset: str = 'USDT') -> float:
        """Return the free/total balance for one asset."""

    @abstractmethod
    async def get_positions(self) -> List[Dict[str, Any]]:
        """Return open positions on the broker."""

    @abstractmethod
    async def execute_order(self, symbol: str, side: str, quantity: float,
                            sl: Optional[float] = None, tp: Optional[float] = None) -> Dict[str, Any]:
        """Place a real market order with optional SL/TP protection."""

    @abstractmethod
    async def close_all_positions(self) -> Dict[str, Any]:
        """Emergency exit: close every open position and cancel open orders."""

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> bool:
        """Cancel a pending order."""

    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        """Human-readable connection status."""

    @abstractmethod
    async def close(self) -> None:
        """Release the underlying client."""
