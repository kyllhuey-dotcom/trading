from .ccxt_adapter import CCXTAdapter
from typing import Dict, Any, Optional


class PrimeXBTAdapter(CCXTAdapter):
    """
    PrimeXBT adapter implemented through CCXT (which supports primexbt).
    Inherits real execution, position and balance management.
    """

    def __init__(self, api_key: Optional[str] = None,
                 api_secret: Optional[str] = None,
                 passphrase: Optional[str] = None):
        super().__init__("primexbt", api_key=api_key, api_secret=api_secret, passphrase=passphrase)

    def get_status(self) -> Dict[str, Any]:
        status = super().get_status()
        status.update({
            "broker": "PRIMEXBT",
            "note": "Futures/CFD execution. Ensure API has trade + withdrawal-off permissions.",
        })
        return status
