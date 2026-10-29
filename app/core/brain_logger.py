from core import config


def log_decision(step, status, reason=""):
    """
    step: market_filter / setup / confirmation / risk / execution
    status: PASS / FAIL / SKIP
    reason: human readable explanation
    """
    if not getattr(config, "LEGACY_DECISION_LOGS", False):
        return

    msg = f"[{step}] -> {status}"
    if reason:
        msg += f" | {reason}"

    print(msg)