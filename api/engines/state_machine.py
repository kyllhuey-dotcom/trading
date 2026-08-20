from enum import Enum
from typing import Set

class BotState(str, Enum):
    OFFLINE = "OFFLINE"
    READY = "READY"
    ANALYZING = "ANALYZING"
    WAITING = "WAITING"
    SIGNAL_DETECTED = "SIGNAL_DETECTED"
    EXECUTING = "EXECUTING"
    POSITION_OPEN = "POSITION_OPEN"
    COOLDOWN = "COOLDOWN"
    NO_TRADE = "NO_TRADE"
    DATA_ERROR = "DATA_ERROR"
    RISK_LOCK = "RISK_LOCK"
    BROKER_ERROR = "BROKER_ERROR"
    EMERGENCY_STOP = "EMERGENCY_STOP"

class StateMachine:
    """
    Rule 34: Gestionnaire d'états du bot.
    """
    def __init__(self):
        self.current_state = BotState.OFFLINE

    def transition_to(self, new_state: BotState):
        # Simplification: allow all transitions for now, 
        # but could be used to enforce rules in Lot 11
        self.current_state = new_state
        return self.current_state
