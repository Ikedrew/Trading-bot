"""
Research Command Centre — CLI entrypoint.

Run with:
    python -m research_engine.command_center

Options:
    --json    Output as JSON instead of human-readable format

This is a REPORTING tool only. It does NOT modify trading behaviour.
"""

import json
import sys
from pathlib import Path

# Ensure project root is importable
_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from research_engine.command_center.research_command_center import (
    generate_command_report,
    print_report,
)


def main() -> None:
    """Generate and display the Research Command Centre report."""
    report = generate_command_report()

    if "--json" in sys.argv:
        print(json.dumps(report.to_dict(), indent=2, default=str))
    else:
        print_report(report)


if __name__ == "__main__":
    main()
