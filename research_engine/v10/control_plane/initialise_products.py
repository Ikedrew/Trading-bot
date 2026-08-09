"""
Initialise all 45 question products from the canonical question bank.

Creates reports/research/questions/{QID}/question.json for every active question.
Safe to re-run — only writes question.json, never overwrites findings.

Usage:
    python -m research_engine.v10.control_plane.initialise_products
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from research_engine.v10.control_plane.question_products import QuestionProductManager
from research_engine.v10.universes.question_bank import QUESTION_BANK


def initialise_all_products(base_dir: Path | str | None = None) -> int:
    """
    Initialise product directories for all 45 questions.

    Returns count of products initialised.
    """
    mgr = QuestionProductManager(base_dir=base_dir)
    count = mgr.initialise_all(QUESTION_BANK)
    return count


if __name__ == "__main__":
    import os
    os.chdir(str(_PROJECT_ROOT))
    count = initialise_all_products()
    print(f"Initialised {count} question products in reports/research/questions/")
    # Verify
    from research_engine.v10.control_plane.question_products import _QUESTIONS_DIR
    ids = sorted(d.name for d in _QUESTIONS_DIR.iterdir() if d.is_dir())
    print(f"Product directories: {len(ids)}")
    print(f"Sample: {ids[:5]}")
