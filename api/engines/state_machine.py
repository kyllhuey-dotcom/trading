from enum import Enum
from typing import Set

class BotState(str, Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    ERROR = "ERROR"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    # Legacy compatibility or extra detail
    ANALYZING = "ANALYZING"
    SIGNAL_DETECTED = "SIGNAL_DETECTED"
    POSITION_OPEN = "POSITION_OPEN"

class StateMachine:
    """
    Rule 34: Gestionnaire d'états du bot.
    """
    def __init__(self):
        self.current_state = BotState.STOPPED

    def transition_to(self, new_state: BotState):
        # Simplification: allow all transitions for now, 
        # but could be used to enforce rules in Lot 11
        self.current_state = new_state
        return self.current_state
