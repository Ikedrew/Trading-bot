"""Effective definition closure for Stage IV question assurance.

This layer does not create new question meaning.  It projects scientific
contracts already frozen in HD06-HD12 adjudication modules into the canonical
``ResearchQuestionDefinition`` representation consumed by assurance.
"""
from __future__ import annotations

from dataclasses import replace

from research_engine.registry.research_question_models import (
    CompletionRule,
    EvidenceAuthority,
    JoinContract,
    QuestionLifecycle,
)
from research_engine.registry.wave_a_no_runner_definitions import (
    WAVE_A_NO_RUNNER_DESIGNS,
)


def _design_definition(current, question_id: str):
    design = WAVE_A_NO_RUNNER_DESIGNS[question_id]
    authorities = tuple(
        EvidenceAuthority(
            dataset=item.dataset,
            field_path=",".join(item.fields),
            semantic_meaning=item.semantic_authority,
            current_eligibility=True,
        )
        for item in design.evidence_authority
    )
    joins = tuple(design.join_contract)
    join = None
    if joins:
        join = JoinContract(
            join_keys=tuple(dict.fromkeys(key for item in joins for key in item.keys)),
            cardinality="governed",
            conflict_policy="reject",
            description="; ".join(
                f"{item.left}->{item.right}: {item.purpose}"
                for item in joins
            ),
        )
    return replace(
        current,
        question_wording=design.canonical_intent,
        research_intent=design.canonical_intent,
        hypothesis=design.hypothesis,
        null_hypothesis=design.null_hypothesis,
        population_definition=design.population_definition,
        metric_definition=design.metric_definition,
        evidence_authorities=authorities,
        join_contract=join,
        epoch_requirement=design.epoch_requirement,
        completion_rule=CompletionRule(
            "governed_contract", None, design.completion_criterion
        ),
        runner_module=current.runner_module,
        runner_function=current.runner_function,
        report_filename=current.report_filename,
    )


def _authority(dataset: str, fields: tuple[str, ...], meaning: str) -> EvidenceAuthority:
    return EvidenceAuthority(
        dataset=dataset,
        field_path=",".join(fields),
        semantic_meaning=meaning,
        current_eligibility=True,
    )


def _single_source(
    current,
    *,
    population: str,
    metric: str,
    fields: tuple[str, ...],
    completion: str,
    minimum: int | None = None,
):
    return replace(
        current,
        hypothesis=f"The governed effect for {current.canonical_question_id} is present.",
        null_hypothesis=f"The governed effect for {current.canonical_question_id} is absent.",
        population_definition=population,
        metric_definition=metric,
        evidence_authorities=(
            _authority("shadow_trades", fields, "CURRENT canonical terminal shadow evidence"),
        ),
        minimum_sample=minimum if minimum is not None else current.minimum_sample,
        completion_rule=CompletionRule("governed_contract", minimum, completion),
    )


def apply_wave4_assurance_definitions(definitions):
    """Return definitions enriched only from existing governed contracts."""
    result = dict(definitions)

    # HD01: S1 is a governed alias, not a second scientific result.
    e3 = result["E3"]
    result["S1"] = replace(
        result["S1"],
        lifecycle_status=QuestionLifecycle.SUPERSEDED,
        question_wording=result["S1"].question_wording,
        research_intent=e3.research_intent,
        hypothesis=e3.hypothesis,
        null_hypothesis=e3.null_hypothesis,
        population_definition=e3.population_definition,
        metric_definition=e3.metric_definition,
        evidence_authorities=e3.evidence_authorities,
        join_contract=e3.join_contract,
        epoch_requirement=e3.epoch_requirement,
        minimum_sample=e3.minimum_sample,
        completion_rule=e3.completion_rule,
    )

    # HD06-HD08 and HD12 are already fully expressed by NoRunnerDesign records.
    for question_id in ("S5", "S6", "S7", "X6", "L6"):
        result[question_id] = _design_definition(result[question_id], question_id)

    # HD10 risk contracts.  Exact model detail remains owned by
    # risk_policy_adjudication; these fields identify its population/estimand.
    risk_common = (
        "CURRENT completed PRIMARY_HORIZON_SIMULATION shadow outcomes collapsed "
        "to one observation per canonical opportunity; account and horizon fanout excluded."
    )
    result["R1"] = replace(
        result["R1"],
        hypothesis="Risk-policy-allowed and blocked-counterfactual arms differ in opportunity-weighted realised R.",
        null_hypothesis="Risk-policy-allowed and blocked-counterfactual arms have equal opportunity-weighted realised R.",
        population_definition="CURRENT risk-stage decisions with deterministic canonical-opportunity shadow outcomes in allowed and blocked-counterfactual arms.",
        metric_definition="Allowed-minus-blocked opportunity-weighted mean R with cluster-robust inference and survival diagnostics.",
        evidence_authorities=(
            _authority("decision_trace", ("canonical_opportunity_id", "terminal_stage", "terminal_reason"), "Frozen pre-outcome risk decision"),
            _authority("shadow_trades", ("canonical_opportunity_id", "r_multiple"), "Terminal counterfactual outcome"),
        ),
        join_contract=JoinContract(("canonical_opportunity_id",), "one_to_one", "reject", "Risk decision to primary terminal shadow outcome"),
        minimum_sample=100,
        completion_rule=CompletionRule("two_arm_risk_effect", 100, ">=50 distinct opportunities in each governed arm; sufficient null may complete"),
    )
    result["R2"] = replace(
        result["R2"],
        hypothesis="At least one frozen risk guard has non-zero exclusive opportunity-weighted outcome effect.",
        null_hypothesis="No frozen risk guard has a reliable exclusive opportunity-weighted outcome effect.",
        population_definition="CURRENT risk-rejected canonical opportunities attributable exclusively to one frozen guard and carrying one terminal primary shadow outcome.",
        metric_definition="Per-guard exclusive opportunity-weighted mean counterfactual R effect with the frozen multiplicity family.",
        evidence_authorities=(
            _authority("decision_trace", ("canonical_opportunity_id", "terminal_reason"), "Frozen guard attribution"),
            _authority("shadow_trades", ("canonical_opportunity_id", "r_multiple"), "Terminal counterfactual outcome"),
        ),
        join_contract=JoinContract(("canonical_opportunity_id",), "one_to_one", "reject", "Exclusive guard decision to primary terminal shadow outcome"),
        minimum_sample=30,
        completion_rule=CompletionRule("guard_exclusive_effect", 30, ">=30 exclusive opportunities per reported guard; sufficient null may complete"),
    )
    result["R3"] = _single_source(
        result["R3"], population=risk_common,
        metric="Probability of catastrophic drawdown under the frozen HD10 ruin model and deterministic seed set.",
        fields=("canonical_opportunity_id", "r_multiple"), minimum=50,
        completion=">=50 distinct opportunities with >=95% outcome coverage; an estimable adverse result is complete",
    )
    result["R4"] = _single_source(
        result["R4"], population=risk_common,
        metric="Historical recovery probability over the frozen drawdown grid on a chronological compounded-R path.",
        fields=("canonical_opportunity_id", "entry_time", "r_multiple"), minimum=50,
        completion=">=50 distinct chronological opportunities and at least one breached grid threshold",
    )
    result["R5"] = _single_source(
        result["R5"], population=risk_common,
        metric="Geometric mean growth per lifecycle for the closed HD10 sizing-model vocabulary subject to drawdown and ruin eligibility.",
        fields=("canonical_opportunity_id", "r_multiple"), minimum=50,
        completion=">=50 distinct opportunities; a sufficient no-eligible-model result may complete",
    )

    # HD09 exit-policy contracts: terminal paths, not entry/exit aggregates.
    exit_population = (
        "CURRENT completed primary shadow lifecycles with reproducible baseline, "
        "complete ordered M5 exit_bar_path_v1, and one canonical opportunity identity."
    )
    exit_fields = ("canonical_opportunity_id", "trade_state_progression", "pnl_r_multiple")
    result["EX1"] = _single_source(
        result["EX1"], population=exit_population,
        metric="Nine-policy paired candidate-minus-reproduced-baseline R effects with clustered inference and one Holm family.",
        fields=exit_fields, minimum=200,
        completion=">=200 paired lifecycles and distinct opportunities; valid null may complete",
    )
    result["EX2"] = _single_source(
        result["EX2"], population=exit_population,
        metric="Trailing-policy paired change in baseline-window MFE retention with clustered inference.",
        fields=exit_fields + ("mfe_r",), minimum=200,
        completion=">=200 eligible paired lifecycles and distinct opportunities; valid null may complete",
    )
    result["EX9"] = _single_source(
        result["EX9"], population=exit_population,
        metric="Paired timeout-indicator change and positive conversion of baseline timeout losses across the frozen 18-test family.",
        fields=exit_fields + ("exit_reason",), minimum=200,
        completion=">=200 paired opportunities and >=30 baseline timeout-loss opportunities for conversion claims",
    )
    result["EX10"] = _single_source(
        result["EX10"], population=exit_population,
        metric="Pooled first-unseen candidate-minus-baseline R across five purged, embargoed expanding validation folds.",
        fields=exit_fields + ("entry_time",), minimum=450,
        completion=">=450 opportunities, five valid folds, >=200 training and >=50 validation opportunities per fold",
    )
    for question_id, dimension, fields, coverage in (
        ("EX5", "trade_horizon", exit_fields + ("trade_horizon",), 0.50),
        ("EX6", "strategy_family", exit_fields + ("strategy",), 0.50),
        ("EX7", "market_regime", exit_fields + ("h4_regime",), 0.80),
        ("EX8", "candlestick_pattern", exit_fields + ("pattern",), 0.50),
    ):
        result[question_id] = _single_source(
            result[question_id], population=exit_population,
            metric=f"Cluster-robust heterogeneity of paired exit-policy R effect by immutable OPEN {dimension}; global interaction test gates follow-ups.",
            fields=fields, minimum=200,
            completion=f">=200 distinct opportunities, {coverage:.0%} {dimension} coverage, >=30 per included cell and >=2 levels; sufficient null may complete",
        )

    # HD11/HD12 meaning is frozen even where implementation remains absent or
    # mismatched.  Assurance must distinguish definition closure from execution.
    result["L2"] = _single_source(
        result["L2"],
        population="CURRENT terminal canonical opportunities on both sides of one immutable, versioned architecture activation boundary with common-support mix controls.",
        metric="Post-minus-pre opportunity-weighted mean R with predeclared version boundary and controlled symbol/regime/strategy mix.",
        fields=("canonical_opportunity_id", "schema_version", "entry_time", "r_multiple"), minimum=200,
        completion=">=100 pre and >=100 post opportunities with >=30 per retained common-support cell",
    )
    result["L3"] = replace(
        result["L3"],
        hypothesis="At least one of the frozen weight, regime, or strategy-mapping assumptions is empirically valid.",
        null_hypothesis="The frozen tests find no reliable support for the weight, regime, or mapping assumptions.",
        population_definition="CURRENT canonical opportunities with persisted decision-time weight vector, governed market regime and strategy identity paired one-to-one to primary terminal shadow outcome.",
        metric_definition="Three frozen subtests: weight-vector rank association, regime outcome separation, and strategy-family outcome separation, with one Holm family.",
        evidence_authorities=(
            _authority("decision_trace", ("canonical_opportunity_id", "component_weights", "regime", "strategy"), "Persisted decision-time assumption state"),
            _authority("shadow_trades", ("canonical_opportunity_id", "r_multiple"), "Primary terminal shadow outcome"),
        ),
        join_contract=JoinContract(("canonical_opportunity_id",), "one_to_one", "reject", "Decision-time assumptions to terminal primary outcome"),
        minimum_sample=100,
        completion_rule=CompletionRule("three_subtest_assumption_validity", 100, "All weight/regime/mapping subtests must be estimable or explicit sufficient nulls"),
    )
    result["L7"] = _single_source(
        result["L7"],
        population="Prospectively intervention-assigned CURRENT canonical opportunities in frozen control and candidate arms with one terminal primary shadow outcome.",
        metric="Candidate-minus-control opportunity-weighted mean R under the HD11 intervention-gated adaptation contract.",
        fields=("canonical_opportunity_id", "intervention_arm", "r_multiple"), minimum=200,
        completion=">=100 control and >=100 candidate opportunities with >=30 per retained comparison cell",
    )
    return result


__all__ = ["apply_wave4_assurance_definitions"]
