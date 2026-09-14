"""Wave A4.1 semantic-alignment assessment.

The registry, runners, resolver, readiness rules, and report contracts do not
currently establish mutually consistent scientific definitions for this
tranche.  The targets therefore remain unchanged and fail-closed.  This module
records the exact conflicts without changing runner, evidence, readiness,
report, collection, trading, or multi-account behaviour.
"""
from __future__ import annotations

from dataclasses import replace

from research_engine.registry.research_question_models import (
    ResearchQuestionDefinition,
)


WAVE_A4_1_TARGETS = frozenset({"M1", "M11", "X3", "EXEC1"})
WAVE_A4_TARGETS = WAVE_A4_1_TARGETS

# No A4.1 target can be closed without an approved semantic narrowing or a
# runner/evidence repair.  The empty override set is intentional.
WAVE_A4_RESOLVED = frozenset()
WAVE_A4_UNRESOLVED = WAVE_A4_TARGETS
WAVE_A4_OVERRIDES: dict[str, dict] = {}

WAVE_A4_UNRESOLVED_REASONS = {
    "M1": (
        "Registry intent asks whether authoritative H4 regime classification "
        "predicts trade R-multiple. The mapped legacy_canonical.run_q06 runner "
        "does not relate regime to outcomes at all: it counts the raw "
        "decision_trace regime field, separately counts shadow outcomes, and "
        "reports only regime frequency. It performs no deterministic trace-to-"
        "outcome join, regime-cell R statistics, controls, ordering, or "
        "predictive validation. Its effective sample is split between decision "
        "traces for the reported distribution and completed shadow records for "
        "status/confidence; COMPLETE requires merely one shadow outcome, while "
        "the registry declares coverage gates but no sample threshold. The "
        "shadow loader also combines canonical reconstructed and separate "
        "research-shadow populations without an M1 CURRENT-only filter or "
        "canonical-opportunity deduplication. Account execution fanout is not "
        "an input, but repeated horizon simulations can inflate the shadow "
        "count. This is descriptive regime-frequency reporting, not associative "
        "or predictive outcome research. Repair requires one authoritative H4 "
        "regime field on a CURRENT completed-shadow population, an approved "
        "canonical-opportunity/horizon observation rule, deterministic outcome "
        "pairing, explicit grouping/controls, and a scientifically sufficient "
        "predictive or approved descriptive test. Left fail-closed."
    ),
    "M11": (
        "Registry intent asks whether regime + phase + bias provides more "
        "predictive value than pattern identity and declares shadow_trades plus "
        "decision_trace with lineage coverage. market_research.run_m11 loads "
        "only shadow trades and performs no decision_trace join. It accepts "
        "compatibility fields for regime, phase, bias, and pattern, although "
        "bias is not a registry required field. It pools CURRENT completed "
        "shadow records across symbol, strategy, and horizon; one shadow "
        "lifecycle is one observation, so account fanout is absent but multiple "
        "horizon simulations for one canonical opportunity count separately. "
        "The metric is the unweighted standard deviation of in-sample cell mean "
        "R for pattern-only versus regime+phase+bias cells, with 'more "
        "predictive' set when context dispersion exceeds pattern dispersion by "
        "15%. There is no per-cell minimum, confounder control, time ordering, "
        "holdout, or predictive score; COMPLETE requires only one fully labelled "
        "combined record, despite the registry coverage rules and lack of an "
        "explicit minimum sample. This is a pooled descriptive association, not "
        "comparative predictive evidence. Repair requires authoritative context "
        "fields and join ownership, an approved opportunity/horizon unit, "
        "confounder and cell-sufficiency rules, and genuine predictive validation "
        "or an approved observational narrowing. Left fail-closed."
    ),
    "X3": (
        "Registry intent asks which sessions have lowest slippage and fewest "
        "rejects. execution_protection_research.run_x3 analyses only slippage; "
        "it does not compute rejection or failure rates from result_ok/retcode "
        "or execution_attempts. Its session authority is explicitly the nested "
        "execution_context.market_access.session_state field, flattened only by "
        "the runner adapter. Results join to one context by correlation_id, with "
        "canonical opportunity/entity/symbol consistency checks; one context may "
        "legitimately fan out to distinct account-grained results identified by "
        "account/order/deal/position identity. Thus one matched account execution "
        "is the execution observation, not a new strategy signal. Only records "
        "whose producer marks slippage_semantic as "
        "measured_execution_slippage are analysed; resolver-derived requested-"
        "versus-fill compatibility values and missing slippage are excluded. The "
        "registry and runner both require >=30 matched observations, while the "
        "runner reports only session cells with >=10 observations and declares "
        "COMPLETE at the overall threshold even if no session cell survives. "
        "This coherently supports descriptive account-execution slippage by "
        "session, but not the registry's combined slippage-and-rejection claim. "
        "Repair requires approved narrowing to measured-slippage profiling or a "
        "defined account-level rejection population/metric and aligned cell "
        "completion rule. Left fail-closed."
    ),
    "EXEC1": (
        "Registry intent asks whether execution failures or adverse conditions "
        "degrade otherwise valid opportunities. The canonical resolver correctly "
        "uses CURRENT execution_results_v1 as the primary population, and "
        "execution_protection_research.run_exec1 likewise loads execution "
        "results; it does not use protection_audit or treat execution_attempts "
        "as equivalent evidence. Each account-grained execution result is a "
        "legitimate observation, allowing different account outcomes for one "
        "canonical decision, but the runner neither reports account grouping nor "
        "enforces a correlation_id + account identity grain. It descriptively "
        "counts result_ok, retcodes, and per-symbol failure rates; missing "
        "result_ok defaults to failure and missing retcode to '?', so rejection "
        "semantics are not isolated. Optional execution_context is joined by "
        "correlation_id, but the purported spread_at_execution statistic is "
        "populated with result slippage rather than context spread. No valid-"
        "opportunity or outcome evidence is joined, so the runner cannot measure "
        "degradation of otherwise valid opportunities or adverse-condition "
        "effects. The registry and runner require >=30 result rows; per-symbol "
        "cells require >=10. This supports descriptive execution-result "
        "reliability only, not associative degradation or counterfactual trade "
        "impact. Repair requires an explicit account-result identity/failure "
        "contract, correct context metrics, deterministic linkage to a defined "
        "otherwise-valid opportunity population and outcome metric, and an "
        "approved descriptive narrowing or association design. Left fail-closed."
    ),
}


def apply_wave_a4_definitions(
    definitions: dict[str, ResearchQuestionDefinition],
) -> dict[str, ResearchQuestionDefinition]:
    """Apply only evidence-backed A4 overrides; A4.1 intentionally has none."""
    result = dict(definitions)
    for qid, overrides in WAVE_A4_OVERRIDES.items():
        result[qid] = replace(result[qid], **overrides)
    return result
