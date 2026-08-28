"""CLI runner: python -m research_projection [--no-backfill] [options]

Reads logs/ (read-only) and materialises/refreshes research_data/.
Never modifies the trading runtime or anything under logs/.
"""

from __future__ import annotations

import argparse
import json
import sys

from .projector import DEFAULT_LOGS_ROOT, DEFAULT_RESEARCH_ROOT, Projector


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m research_projection",
        description="Project logs/ capture into the research_data/ layer "
                    "(read-only on logs/; additive under research_data/).")
    parser.add_argument("--logs-root", default=str(DEFAULT_LOGS_ROOT),
                        help="Source capture root (default: ./logs). Read-only.")
    parser.add_argument("--research-root", default=str(DEFAULT_RESEARCH_ROOT),
                        help="Research layer root (default: ./research_data).")
    parser.add_argument("--no-backfill", action="store_true",
                        help="Do not project existing source bytes; only new "
                             "bytes appended after this run are projected.")
    args = parser.parse_args(argv)

    projector = Projector(logs_root=args.logs_root,
                          research_root=args.research_root,
                          backfill=not args.no_backfill)
    summary = projector.run()
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if summary.get("anomalies"):
        print(f"\n{len(summary['anomalies'])} anomaly/anomalies recorded; "
              f"see the run entry in research_data/manifest/"
              f"projection_state.json.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
