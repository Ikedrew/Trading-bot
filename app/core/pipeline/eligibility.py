def check_eligibility(state):

    if state.cooldown_active:
        return False, "Cooldown active"

    if state.max_positions_reached:
        return False, "Max positions reached"

    if state.direction_locked:
        return False, "Direction locked"

    return True, None