from typing import Dict, Any, Optional

class RiskEngine:
    """
    Risk Engine Professionnel (Rule 24, 25, 26).
    Gère le dimensionnement des positions, le levier et la protection du capital.
    """
    def __init__(self, max_risk_pct: float = 1.0, max_leverage: int = 20, min_account_balance: float = 10.0):
        self.max_risk_pct = max_risk_pct
        self.max_leverage = max_leverage
        self.min_account_balance = min_account_balance

    def calculate_position_size(self, balance: float, entry: float, stop_loss: float, fee_pct: float = 0.05) -> Dict[str, Any]:
        """
        Calcule la taille de position et le levier (Rule 24, 25, 26).
        """
        # 1. FAIL-SAFE: Solde insuffisant (Rule 25)
        if balance < self.min_account_balance:
            return {"allowed": False, "reason": f"Account balance too low: {balance:.2f} < {self.min_account_balance}"}

        # 2. CALCUL DU RISQUE CASH
        risk_amount = balance * (self.max_risk_pct / 100)
        
        # 3. DISTANCE AU STOP LOSS
        price_risk_per_unit = abs(entry - stop_loss)
        if price_risk_per_unit == 0:
            return {"allowed": False, "reason": "Invalid Stop Loss distance (Zero)"}

        # 4. QUANTITÉ THÉORIQUE (Notional Risk)
        # Quantity = Risk Amount / Price Risk
        quantity = risk_amount / price_risk_per_unit
        
        # 5. VALEUR NOMINALE (Notional Value)
        notional_value = quantity * entry
        
        # 6. CALCUL DU LEVIER NÉCESSAIRE (Rule 26)
        # Leverage = Notional Value / Balance
        required_leverage = notional_value / balance
        
        # 7. SÉCURITÉ LEVIER (Rule 26)
        if required_leverage > self.max_leverage:
            # Si le levier requis est trop haut, on réduit la taille de position 
            # pour ne pas dépasser le levier max, même si on risque moins de 1%
            safe_leverage = self.max_leverage
            notional_value = balance * safe_leverage
            quantity = notional_value / entry
            required_leverage = safe_leverage
            # Recalcul du risque réel
            actual_risk_amount = quantity * price_risk_per_unit
            actual_risk_pct = (actual_risk_amount / balance) * 100
        else:
            actual_risk_pct = self.max_risk_pct
            actual_risk_amount = risk_amount

        # 8. VÉRIFICATION MINIMUM ORDER SIZE (Rule 25)
        # Simulation d'un minimum de 10 USDT pour les petites positions (Rule 12/25)
        if notional_value < 10.0:
             return {
                "allowed": False, 
                "reason": f"Order size too small ({notional_value:.2f}€). Min: 10€",
                "notional": notional_value
             }

        # 9. ESTIMATION DES FRAIS (Rule 21)
        estimated_fees = notional_value * (fee_pct / 100) * 2 # In + Out

        return {
            "allowed": True,
            "balance": balance,
            "risk_amount": float(actual_risk_amount),
            "risk_pct": float(actual_risk_pct),
            "quantity": float(quantity),
            "notional_value": float(notional_value),
            "leverage": float(required_leverage),
            "estimated_fees": float(estimated_fees),
            "max_allowed_leverage": self.max_leverage
        }

    def check_daily_loss(self, current_pnl: float, balance: float, limit_pct: float = 2.0) -> Dict[str, Any]:
        limit = -(balance * (limit_pct / 100))
        is_locked = current_pnl <= limit
        return {
            "is_locked": is_locked,
            "daily_pnl": current_pnl,
            "loss_limit": limit,
            "status": "LOCKED" if is_locked else "SAFE"
        }
