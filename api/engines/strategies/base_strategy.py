from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
import pandas as pd

class BaseStrategy(ABC):
    @abstractmethod
    def generate_signal(self, 
                        market_id: str,
                        df: pd.DataFrame, 
                        orderbook: Optional[Dict[str, Any]] = None, 
                        trades: Optional[List[Dict[str, Any]]] = None,
                        cross_quotes: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        Génère un signal de trading basé sur la stratégie spécifique.
        Retourne un dictionnaire avec :
        - status: "SIGNAL_DETECTED" | "NO_TRADE"
        - direction: "BUY" | "SELL"
        - score: 0-100
        - reason: str
        - entry: float
        - sl: float
        - tp: float
        """
        pass
