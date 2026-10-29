"""
CLI entry point for V10 Research Operations.

Usage:
    python -m research_engine.v10.operations run_campaign FX_OPT_V1
    python -m research_engine.v10.operations run_question R2 --instrument FX --regime TRENDING
    python -m research_engine.v10.operations dashboard
    python -m research_engine.v10.operations report
    python -m research_engine.v10.operations state
    python -m research_engine.v10.operations validate V10.1_STOP_ATR_2.0
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
logging.basicConfig(level=logging.INFO, stream=sys.stdout)

from research_engine.v10.operations import ResearchRouter


def main():
    if len(sys.argv) < 2:
        _usage()
        sys.exit(1)

    action = sys.argv[1]
    router = ResearchRouter()

    # Parse CLI into event
    event = _parse_cli(action, sys.argv[2:])
    result = router.execute(event)

    # Output
    if "error" in result and result["error"]:
        print(f"\nERROR: {result['error']}")
        sys.exit(1)

    print(json.dumps(result, indent=2, default=str))


def _parse_cli(action: str, args: list[str]) -> dict:
    """Convert CLI arguments into a router event."""
    filters = {}
    # Extract --key value pairs
    i = 0
    positional = []
    while i < len(args):
        if args[i].startswith("--"):
            key = args[i][2:]
            if i + 1 < len(args):
                filters[key] = args[i + 1]
                i += 2
            else:
                i += 1
        else:
            positional.append(args[i])
            i += 1

    if action == "run_campaign":
        return {"action": "run_campaign", "campaign_id": positional[0] if positional else "", "filters": filters or None}
    elif action == "run_question":
        return {"action": "run_question", "question_id": positional[0] if positional else "", "filters": filters}
    elif action == "validate":
        return {"action": "run_candidate_validation", "candidate_id": positional[0] if positional else "", "filters": filters or None}
    elif action == "dashboard":
        return {"action": "generate_dashboard"}
    elif action == "report":
        return {"action": "generate_report"}
    elif action == "state":
        return {"action": "get_state"}
    elif action == "shadow":
        return {"action": "run_shadow_processing", "trades": []}
    else:
        return {"action": action}


def _usage():
    print("""
V10 Research Operations CLI

Usage:
    python -m research_engine.v10.operations <action> [args] [--filters]

Actions:
    run_campaign <campaign_id>           Run a registered campaign
    run_question <question_id> [--filters]  Run a single question
    validate <candidate_id>              Validate a candidate
    dashboard                            Generate candidate dashboard
    report                               Generate operational report
    state                                Show research state
    shadow                               Process shadow candidates

Examples:
    python -m research_engine.v10.operations run_campaign FX_OPT_V1
    python -m research_engine.v10.operations run_question R2 --instrument FX
    python -m research_engine.v10.operations dashboard
""")


if __name__ == "__main__":
    main()
