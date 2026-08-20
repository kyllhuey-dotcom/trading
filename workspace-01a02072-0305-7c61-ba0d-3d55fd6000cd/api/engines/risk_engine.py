from typing import Dict, Any, Optional

class RiskEngine:
    def __init__(self, max_risk_pct: float = 1.0, max_daily_loss_pct: float = 5.0, default_leverage: int = 1):
        self.max_risk_pct = max_risk_pct # Risque 1% par trade par défaut
        self.max_daily_loss_pct = max_daily_loss_pct
        self.default_leverage = default_leverage
        
    def calculate_position_size(self, balance: float, entry: float, stop_loss: float, fee_pct: float = 0.05) -> Dict[str, Any]:
        """
        Optimisation du Risk Engine : Frais réduits et validation stricte.
        """
        if balance <= 0:
            return {"allowed": False, "reason": "Insufficient balance"}
        
        # Sécurité Drawdown
        if balance < 10.0: # Hard limit pour compte 20€
            return {"allowed": False, "reason": "Capital protection: balance too low"}

        # 1. Calcul du montant risqué en cash
        risk_amount = balance * (self.max_risk_pct / 100)
        
        # 2. Distance au stop loss
        price_risk_per_unit = abs(entry - stop_loss)
        if price_risk_per_unit == 0:
            return {"allowed": False, "reason": "Invalid Stop Loss (distance zero)"}
            
        # 3. Quantité à acheter/vendre pour respecter le risque cash
        # Formule : Quantité = Risque / Distance Stop
        quantity = risk_amount / price_risk_per_unit
        
        # 4. Valeur nominale de la position (Notional Value)
        notional_value = quantity * entry
        
        # 5. Calcul du levier nécessaire
        # Levier = Valeur Nominale / Capital
        required_leverage = notional_value / balance
        
        # 6. Vérification des frais (estimation simple entrée + sortie)
        estimated_fees = notional_value * (fee_pct / 100) * 2
        
        # Rule 12 : Petit capital (ex: 20€)
        # On vérifie si la position est trop petite ou trop grande
        min_notional = 10.0 # Souvent 10 USDT sur Binance
        if notional_value < min_notional:
            # Si trop petit, on essaie d'ajuster à la taille minimale si le risque le permet
            if (min_notional / entry) * price_risk_per_unit <= risk_amount * 1.5: # Tolérance 50% extra risque
                notional_value = min_notional
                quantity = notional_value / entry
                required_leverage = notional_value / balance
            else:
                return {"allowed": False, "reason": f"Position size too small for broker ({notional_value:.2f} < {min_notional})"}

        # Sécurité levier (Rule 11)
        max_allowed_leverage = 20 # Limite de sécurité configurable
        if required_leverage > max_allowed_leverage:
             return {"allowed": False, "reason": f"Required leverage too high ({required_leverage:.1f}x > {max_allowed_leverage}x)"}

        return {
            "allowed": True,
            "balance": balance,
            "risk_amount": float(risk_amount),
            "quantity": float(quantity),
            "notional_value": float(notional_value),
            "leverage": float(required_leverage),
            "estimated_fees": float(estimated_fees),
            "risk_reward_ratio": 1.5, # Fixé par SignalEngine pour l'instant
            "max_risk_pct": self.max_risk_pct
        }

    def check_daily_limit(self, current_pnl: float, balance: float) -> bool:
        limit = - (balance * (self.max_daily_loss_pct / 100))
        return current_pnl > limit
