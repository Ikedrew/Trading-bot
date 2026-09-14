"""Wave A3.1 safe definition assessment for M3, M7, and P1.

The existing registry, runners, evidence resolver, readiness rules, report
contracts, and focused tests do not establish complete, mutually consistent
scientific definitions for these targets.  They therefore remain unchanged and
fail-closed.  This module records the exact authority conflicts without changing
runner, evidence, readiness, report, or collection behaviour.
"""
from __future__ import annotations

from dataclasses import replace

from research_engine.registry.research_question_models import (
    ResearchQuestionDefinition,
)


WAVE_A3_1_TARGETS = frozenset({"M3", "M7", "P1"})
WAVE_A3_TARGETS = WAVE_A3_1_TARGETS

# No A3.1 target can be closed without choosing new scientific semantics or
# changing an existing runner.  An empty override set is intentional.
WAVE_A3_RESOLVED = frozenset()
WAVE_A3_UNRESOLVED = WAVE_A3_TARGETS
WAVE_A3_OVERRIDES: dict[str, dict] = {}

WAVE_A3_UNRESOLVED_REASONS = {
    "M3": (
        "Registry intent asks whether market_phase improves prediction beyond "
        "H4 regime alone. The dedicated runner (market_research.run_m3) does "
        "not perform predictive validation, model comparison, time ordering, "
        "or an out-of-sample test. It pools CURRENT completed shadow records "
        "across symbols, strategies, and simulated horizons, treats each "
        "record as an observation, and compares the unweighted standard "
        "deviation of regime-cell mean R with regime+phase-cell mean R. It "
        "sets phase_adds_predictive_value when combined dispersion is >115% "
        "of regime-only dispersion and declares COMPLETE with any positive "
        "number of combined records; there is no scientifically sufficient "
        "minimum sample or per-cell threshold. Account executions are not in "
        "the population, but the runner defines no canonical-opportunity "
        "deduplication rule. Calling this dispersion proxy incremental "
        "predictive power would invent semantics not established by the "
        "implementation. Left fail-closed."
    ),
    "M7": (
        "Registry intent asks whether combining regime and phase improves "
        "predictive power beyond either alone. The dedicated runner "
        "(market_research.run_m7) performs an observational, pooled comparison "
        "of the unweighted standard deviation of cell mean R for regime-only, "
        "phase-only, and regime+phase segmentations. It performs no predictive "
        "validation, time/phase ordering, within-symbol or within-strategy "
        "control, or out-of-sample comparison. It sets combined_adds_value on "
        "a strict dispersion comparison and declares COMPLETE with any "
        "positive number of combined records; there is no scientifically "
        "sufficient minimum sample or per-cell threshold. Each CURRENT "
        "completed shadow record is counted, with no account execution input "
        "and no canonical-opportunity deduplication rule. The registry's "
        "predictive claim cannot be reduced to this descriptive association "
        "at the definition layer. Left fail-closed."
    ),
    "P1": (
        "Registry intent asks for expected promotion effects on EV, win rate, "
        "drawdown, trade frequency, and risk using shadow_trades plus "
        "decision_trace. The dedicated runner (promotion_impact."
        "run_promotion_impact) reads only CURRENT shadow records and performs "
        "no decision_trace join. It estimates two fixed in-sample scenarios: "
        "remove pattern groups with >=10 records and mean R below -0.2R, and "
        "raise the score threshold from 0.35 to 0.45. It reports EV changes "
        "and trade-count changes plus current win rate, but does not estimate "
        "promotion effects on drawdown or risk and does not estimate changed "
        "win rate. Readiness requires 100 shadow records, 95% outcome, 80% "
        "entity lineage, and 50% canonical strategy coverage; the runner then "
        "requires 100 R outcomes and >=10 remaining trades for threshold "
        "feasibility. Its unit is a completed shadow record grouped by pattern "
        "or score, without canonical-opportunity deduplication; account "
        "executions are absent, but multiple shadow simulations for one "
        "opportunity can count separately. Declaring the missing join, broader "
        "outcomes, independent opportunity unit, or causal/predictive impact "
        "would require new semantics or runner changes. Left fail-closed."
    ),
}


def apply_wave_a3_definitions(
    definitions: dict[str, ResearchQuestionDefinition],
) -> dict[str, ResearchQuestionDefinition]:
    """Return definitions unchanged because every A3.1 target is unresolved."""
    result = dict(definitions)
    for qid, overrides in WAVE_A3_OVERRIDES.items():
        # Kept structurally consistent with earlier Wave A modules so a future
        # evidence-backed tranche can add safe overrides without a new framework.
        result[qid] = replace(result[qid], **overrides)
    return result
