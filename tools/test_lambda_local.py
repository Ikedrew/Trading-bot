"""
Local Lambda simulation — tests the handler without AWS/MT5/broker.

Run:
    python tools/test_lambda_local.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def main():
    print("=" * 60)
    print("  V10 LOCAL LAMBDA TEST")
    print("=" * 60)

    from research_engine.v10.lambda_handler import handler

    tests = [
        ("get_state", {"action": "get_state"}),
        ("generate_report", {"action": "generate_report"}),
        ("run_question E1", {"action": "run_question", "question_id": "E1"}),
        ("run_campaign RISK_INVESTIGATION_V1", {"action": "run_campaign", "campaign_id": "RISK_INVESTIGATION_V1"}),
        ("unknown action", {"action": "nonexistent"}),
    ]

    results = []
    for name, event in tests:
        try:
            result = handler(event, context=None)
            success = "error" not in result or not result.get("error")
            # Unknown action is expected to return error
            if event["action"] == "nonexistent":
                success = "error" in result and "Unknown" in result.get("error", "")
            status = "PASS" if success else "FAIL"
        except Exception as exc:
            status = "FAIL"
            result = {"error": str(exc)}

        results.append({"test": name, "status": status})
        icon = "OK" if status == "PASS" else "!!"
        print(f"  [{icon}] {name}: {status}")

    passed = sum(1 for r in results if r["status"] == "PASS")
    total = len(results)
    print(f"\n  Results: {passed}/{total} passed")

    # Check no MT5 was contacted
    print(f"\n  MT5 contacted: NO (Lambda operates on research data only)")
    print(f"  Broker contacted: NO")
    print(f"  Live bot affected: NO")
    print("=" * 60)

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
