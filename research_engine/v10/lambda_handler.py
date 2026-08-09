"""
V10 Research Lambda Handler.

AWS Lambda entry point for research operations.
Routes events to the ResearchRouter without duplicating logic.

SAFETY: This module NEVER imports broker execution code.

Usage (Lambda):
    handler({"action": "run_campaign", "campaign_id": "FX_OPT_V1"}, context)

Usage (local):
    python -m research_engine.v10.lambda_handler '{"action": "run_campaign", "campaign_id": "FX_OPT_V1"}'
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """
    AWS Lambda entry point.

    Args:
        event: Action payload (see ResearchRouter for supported actions)
        context: Lambda context (unused, kept for interface compat)

    Returns:
        JSON-serialisable result dict.
    """
    from research_engine.v10.operations import ResearchRouter

    logger.info(f"[LAMBDA] Received: action={event.get('action', '?')}")
    router = ResearchRouter.create()
    result = router.execute(event)
    return result


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

    if len(sys.argv) < 2:
        print("Usage: python -m research_engine.v10.lambda_handler '{\"action\": \"...\", ...}'")
        sys.exit(1)

    event = json.loads(sys.argv[1])
    result = handler(event)
    print(json.dumps(result, indent=2, default=str))
