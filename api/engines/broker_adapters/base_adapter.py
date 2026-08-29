from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional


class BrokerAdapter(ABC):
    """Universal broker interface (all execution targets must implement it)."""

    # v3.1 P0-2: True only when the broker can actually enumerate positions
    # (derivatives). Spot-only adapters return [] from get_positions() and
    # that empty list must NEVER be treated as "everything was closed".
    positions_authoritative: bool = False

    async def close_position(self, symbol: str, side: str, quantity: float) -> Dict[str, Any]:
        """v3.1 P0-5: close one position (market reduce-only hedge).

        Default implementation refuses — adapters that support it override.
        """
        return {"success": False, "reason": "CLOSE_POSITION_NOT_SUPPORTED"}

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
