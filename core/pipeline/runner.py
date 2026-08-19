from scoring_engine import run_scoring_engine
from eligibility import check_eligibility
from execution import check_execution
from control_layer import control_gate
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)


def run_cycle(inputs, state):

    # 1. SCORING
    score_result = run_scoring_engine(**inputs)

    if score_result is not None:
        logging.info(f"[SCORING BLOCK] {score_result}")
        return score_result

    # 2. L4 GATES
    ok, reason = check_eligibility(state)
    if not ok:
        result = {"status": "BLOCKED_L4", "reason": reason}
        logging.info(result)
        return result

    # 3. L5 GATES
    ok, reason = check_execution(state)
    if not ok:
        result = {"status": "BLOCKED_L5", "reason": reason}
        logging.info(result)
        return result

    # 4. FINAL DECISION (strategy passed)
    decision = {
        "status": "EXECUTE",
        "score": getattr(score_result, "final_score", None)
    }

    # 5. CONTROL LAYER (THIS IS YOUR OVERRIDE SYSTEM)
    ok, reason = control_gate(decision)

    if not ok:
        result = {
            "status": "BLOCKED_CONTROL",
            "reason": reason,
            "decision": decision
        }
        logging.info(result)
        return result

    # 6. EXECUTION
    logging.info(f"[EXECUTE] {decision}")
    return decision