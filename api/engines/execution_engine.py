import json
import os
from datetime import datetime
from typing import List, Dict, Any, Optional

class ExecutionEngine:
    """
    Simule l'exécution en mode DEMO en utilisant les prix réels (Rule 27).
    Prépare l'interface pour le mode REAL (Lot 8).
    """
    def __init__(self, portfolio: Any, data_dir: str = "data"):
        self.portfolio = portfolio
        self.data_dir = data_dir
        self.active_positions_file = os.path.join(data_dir, "active_positions.json")
        self.active_positions = self._load_positions()

    def _load_positions(self):
        if os.path.exists(self.active_positions_file):
            with open(self.active_positions_file, 'r') as f:
                try: return json.load(f)
                except: return []
        return []

    def _save_positions(self):
        with open(self.active_positions_file, 'w') as f:
            json.dump(self.active_positions, f, indent=2)

    async def execute_order(self, mode: str, signal: Dict[str, Any], risk: Dict[str, Any], ticker: Dict[str, Any]) -> Dict[str, Any]:
        """
        Exécute un ordre en utilisant le prix réel (Bid/Ask).
        """
        if self.active_positions:
            return {"success": False, "reason": "Already have an open position"}

        # Rule 27 : Fill simulé basé sur bid/ask réel
        # Si BUY -> on achète au ASK. Si SELL -> on vend au BID.
        entry_price = ticker.get('ask') if signal["direction"] == "BUY" else ticker.get('bid')
        
        # Fallback si bid/ask non dispos
        if not entry_price:
            entry_price = ticker.get('last')

        # Ajout du slippage (0.01% par défaut pour simulation réaliste)
        slippage = 0.0001 
        if signal["direction"] == "BUY":
            entry_price *= (1 + slippage)
        else:
            entry_price *= (1 - slippage)

        position = {
            "id": f"SIM-{int(datetime.now().timestamp())}",
            "mode": mode,
            "symbol": signal["symbol"],
            "direction": signal["direction"],
            "entry_price": float(entry_price),
            "quantity": risk["quantity"],
            "sl": signal["sl"],
            "tp": signal["tp"],
            "leverage": risk["leverage"],
            "fees": risk["estimated_fees"] / 2, # Frais d'ouverture
            "open_time": datetime.now().isoformat(),
            "status": "OPEN",
            "pnl": - (risk["estimated_fees"] / 2) # On commence avec les frais payés
        }

        self.active_positions.append(position)
        self._save_positions()
        return {"success": True, "position": position}

    async def update_active_positions(self, mode: str, tickers: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Met à jour le P&L latent et vérifie les sorties (SL/TP) avec prix réels.
        """
        closed_trades = []
        for pos in self.active_positions[:]:
            if pos["mode"] != mode: continue
            
            ticker = tickers.get(pos["symbol"])
            if not ticker: continue
            
            # Prix pour fermer : Si on est LONG -> on vend au BID. Si SHORT -> on achète au ASK.
            current_exit_price = ticker.get('bid') if pos["direction"] == "BUY" else ticker.get('ask')
            if not current_exit_price: current_exit_price = ticker.get('last')
            
            # Calcul PnL
            if pos["direction"] == "BUY":
                pnl = (current_exit_price - pos["entry_price"]) * pos["quantity"]
            else:
                pnl = (pos["entry_price"] - current_exit_price) * pos["quantity"]
            
            pos["pnl"] = float(pnl)

            # Vérification SL / TP
            hit_sl = (pos["direction"] == "BUY" and current_exit_price <= pos["sl"]) or \
                     (pos["direction"] == "SELL" and current_exit_price >= pos["sl"])
            hit_tp = (pos["direction"] == "BUY" and current_exit_price >= pos["tp"]) or \
                     (pos["direction"] == "SELL" and current_exit_price <= pos["tp"])

            if hit_sl or hit_tp:
                pos["status"] = "CLOSED"
                pos["exit_price"] = float(current_exit_price)
                pos["close_time"] = datetime.now().isoformat()
                # On retire les frais de sortie
                pos["pnl"] -= (pos["fees"]) 
                
                self.portfolio.update_balance(mode, pos["pnl"])
                self.portfolio.add_to_history(pos)
                self.active_positions.remove(pos)
                closed_trades.append(pos)
        
        if closed_trades:
            self._save_positions()
        return closed_trades
