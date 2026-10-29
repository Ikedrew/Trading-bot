def check_execution(state):

    if state.spread > state.max_spread:
        return False, "Spread too high"

    if state.margin_ok is False:
        return False, "Insufficient margin"

    if state.risk_exceeded:
        return False, "Risk limit hit"

    return True, None