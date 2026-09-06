"""Gap 9 Phase 2 — exact programmatic inventory diff of the two question
inventories (plus historical registry files). Read-only."""

from __future__ import annotations

import difflib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> None:
    from research_engine.registry.research_question_registry import REGISTRY
    from research_engine.v10.universes.question_bank import QUESTION_BANK, RETIRED_QUESTIONS
    from research_engine.v10.universes.models import QuestionStatus

    reg = {q.id: q for q in REGISTRY}
    bank = {q.question_id: q for q in QUESTION_BANK}
    retired = {q.question_id: q for q in RETIRED_QUESTIONS}

    print("=" * 72)
    print("INVENTORY COUNTS")
    print("=" * 72)
    print(f"canonical registry (research_question_registry): {len(reg)}")
    print(f"question bank (v10/universes/question_bank):      {len(bank)}")
    print(f"question bank RETIRED_QUESTIONS:                  {len(retired)}")

    inter = sorted(set(reg) & set(bank))
    reg_only = sorted(set(reg) - set(bank))
    bank_only = sorted(set(bank) - set(reg))

    print(f"\nintersection: {len(inter)}")
    print(f"registry-only: {len(reg_only)}")
    print(f"bank-only: {len(bank_only)}")

    print("\n" + "=" * 72)
    print("INTERSECTION — semantic comparison")
    print("=" * 72)
    conflicts = 0
    for qid in inter:
        r, b = reg[qid], bank[qid]
        title_sim = difflib.SequenceMatcher(
            None, (r.title or "").lower(), (b.title or "").lower()).ratio()
        r_sample = getattr(r, "minimum_sample_size", None)
        diffs = []
        if title_sim < 0.5:
            diffs.append(f"title(reg='{r.title}' vs bank='{b.title}')")
        if r_sample is not None and b.minimum_sample_size != r_sample:
            diffs.append(f"min_sample(reg={r_sample} vs bank={b.minimum_sample_size})")
        r_runner = bool(getattr(r, "runner_module", ""))
        if r_runner:
            diffs.append("registry-RUNNER (bank has none)")
        if diffs:
            conflicts += 1
            print(f"  {qid}: {'; '.join(diffs)}")
    print(f"  -> intersecting entries with semantic differences: {conflicts}/{len(inter)}")

    print("\n" + "=" * 72)
    print("BANK-ONLY entries (source_intent shows intended legacy alias)")
    print("=" * 72)
    for qid in bank_only:
        b = bank[qid]
        aliases = ",".join(b.source_intent)
        # alias present in registry?
        aliased = [a for a in b.source_intent if a in reg]
        print(f"  {qid} [{b.status.value}] aliases={aliases} "
              f"aliased-in-registry={aliased or 'NONE'} "
              f"min_n={b.minimum_sample_size}")

    print("\n" + "=" * 72)
    print("REGISTRY-ONLY entries")
    print("=" * 72)
    print("  " + ", ".join(reg_only))

    # alias coverage: does every bank-only question alias to a registry id?
    unaliased = [qid for qid in bank_only
                 if not any(a in reg for a in bank[qid].source_intent)]
    print(f"\nbank-only WITHOUT any registry alias: {len(unaliased)}")
    for qid in unaliased:
        b = bank[qid]
        print(f"  {qid}: {b.title} [status={b.status.value}]")

    # retired in bank
    print("\n" + "=" * 72)
    print("BANK RETIRED_QUESTIONS")
    print("=" * 72)
    for qid, q in sorted(retired.items()):
        print(f"  {qid}: {q.title}")

    # executable summary
    executable = [q.id for q in REGISTRY if q.runner_module and q.runner_function]
    print("\n" + "=" * 72)
    print("CANONICAL EXECUTABLE QUESTIONS")
    print("=" * 72)
    print(f"  registry entries with runner: {len(executable)}")
    print("  " + ", ".join(sorted(executable)))

    # status distribution in bank
    from collections import Counter
    print("\nbank status distribution:", dict(Counter(q.status.value for q in QUESTION_BANK)))


if __name__ == "__main__":
    main()
