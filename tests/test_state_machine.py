from api.engines.state_machine import StateMachine, BotState

def test_state_machine_transitions():
    sm = StateMachine()
    assert sm.current_state == BotState.STOPPED
    
    sm.transition_to(BotState.RUNNING)
    assert sm.current_state == BotState.RUNNING
    
    sm.transition_to(BotState.EMERGENCY_STOP)
    assert sm.current_state == BotState.EMERGENCY_STOP
