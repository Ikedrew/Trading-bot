"""
Research Question Registry v2 — All research questions with structured requirements.

Organised by category. Each question declares its data needs, validation rules,
and dependencies so the audit system can compute READY/BLOCKED/WAITING status.

Legacy Q1-Q25 IDs are mapped via legacy_ids field for traceability.
"""

from __future__ import annotations

from research_engine.registry.research_question_models import (
    DataSource,
    QuestionCategory,
    QuestionPriority,
    ResearchQuestion,
    ValidationRule,
)

# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY E — SYSTEM EDGE
# ═══════════════════════════════════════════════════════════════════════════════

E1 = ResearchQuestion(
    id="E1",
    category=QuestionCategory.SYSTEM_EDGE,
    title="True system expectancy",
    description="What is the true expectancy of the production decision pipeline measured by R-multiple per trade?",
    required_fields=("entity_id", "pattern", "score", "r_multiple", "exit_reason"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P0,
    validation_rules=(
        ValidationRule("lineage_coverage", ">=", 0.80, "Need entity_id to link decision context to outcomes"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Nearly all records must have R-multiple"),
    ),
    runner_module="research_engine.experiments.expected_value",
    runner_function="run",
    report_filename="q19_expected_value.json",
    legacy_ids=("Q19",),
)

E2 = ResearchQuestion(
    id="E2",
    category=QuestionCategory.SYSTEM_EDGE,
    title="Pattern expectancy",
    description="Which candlestick patterns contain positive expectancy across all conditions?",
    required_fields=("pattern", "r_multiple"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P0,
    validation_rules=(
        ValidationRule("pattern_coverage", ">=", 0.50, "At least 50% of records must have pattern"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Nearly all records must have outcome"),
    ),
    runner_module="research_engine.experiments.legacy_canonical",
    runner_function="run_q05",
    report_filename="q5_pattern_degradation.json",
    legacy_ids=("Q5", "Q24"),
)

E3 = ResearchQuestion(
    id="E3",
    category=QuestionCategory.SYSTEM_EDGE,
    title="Strategy expectancy",
    description="Which strategy types (REVERSAL/CONTINUATION/FALSE_BREAK) contain positive expectancy?",
    required_fields=("strategy", "r_multiple"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P0,
    validation_rules=(
        ValidationRule("strategy_coverage", ">=", 0.50, "Clean strategy field required (not combined with horizon)"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome data required"),
    ),
    runner_module="research_engine.experiments.legacy_canonical",
    runner_function="run_q24",
    report_filename="q24_strategy_edge.json",
    legacy_ids=("Q24",),
)

E4 = ResearchQuestion(
    id="E4",
    category=QuestionCategory.SYSTEM_EDGE,
    title="Strategy × pattern combinations",
    description="Which strategy × pattern combinations produce edge? (e.g. REVERSAL + TWEEZER_TOP)",
    required_fields=("strategy", "pattern", "r_multiple"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P1,
    validation_rules=(
        ValidationRule("strategy_coverage", ">=", 0.50, "Clean strategy required"),
        ValidationRule("pattern_coverage", ">=", 0.50, "Pattern required"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required"),
    ),
    depends_on=("E2", "E3"),
)

# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY M — MARKET CONTEXT
# ═══════════════════════════════════════════════════════════════════════════════

M1 = ResearchQuestion(
    id="M1",
    category=QuestionCategory.MARKET_CONTEXT,
    title="H4 regime predicts outcomes",
    description="Does H4 regime classification (TRENDING/RANGING/TRANSITIONAL) predict trade R-multiple?",
    required_fields=("h4_regime", "r_multiple"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P0,
    validation_rules=(
        ValidationRule("h4_regime_coverage", ">=", 0.80, "H4 regime must be populated in most records"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required"),
    ),
    runner_module="research_engine.experiments.legacy_canonical",
    runner_function="run_q06",
    report_filename="q6_regime_accuracy.json",
    legacy_ids=("Q6", "Q23"),
)

M2 = ResearchQuestion(
    id="M2",
    category=QuestionCategory.MARKET_CONTEXT,
    title="H4 regime edge by strategy",
    description="Which H4 regimes produce edge for each strategy type?",
    required_fields=("h4_regime", "strategy", "r_multiple"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P1,
    validation_rules=(
        ValidationRule("h4_regime_coverage", ">=", 0.80, "H4 regime required"),
        ValidationRule("strategy_coverage", ">=", 0.50, "Clean strategy required"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required"),
    ),
    depends_on=("M1", "E3"),
)

M3 = ResearchQuestion(
    id="M3",
    category=QuestionCategory.MARKET_CONTEXT,
    title="Phase improves prediction beyond regime",
    description="Does market_phase (IMPULSE/PULLBACK/CONSOLIDATION/EXHAUSTION/REVERSAL) improve prediction beyond H4 regime alone?",
    required_fields=("h4_regime", "market_phase", "r_multiple"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P1,
    validation_rules=(
        ValidationRule("h4_regime_coverage", ">=", 0.80, "H4 regime required"),
        ValidationRule("market_phase_coverage", ">=", 0.80, "Phase must be populated"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required"),
    ),
    depends_on=("M1",),
)

M4 = ResearchQuestion(
    id="M4",
    category=QuestionCategory.MARKET_CONTEXT,
    title="Regime × phase × strategy edge",
    description="Which regime × phase × strategy combinations produce edge?",
    required_fields=("h4_regime", "market_phase", "strategy", "pattern", "r_multiple"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P1,
    validation_rules=(
        ValidationRule("h4_regime_coverage", ">=", 0.80, "H4 regime required"),
        ValidationRule("market_phase_coverage", ">=", 0.80, "Phase required"),
        ValidationRule("strategy_coverage", ">=", 0.50, "Clean strategy required"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required"),
    ),
    depends_on=("M1", "M3", "E3"),
)

M5 = ResearchQuestion(
    id="M5",
    category=QuestionCategory.MARKET_CONTEXT,
    title="Phase transitions predict drawdown",
    description="Do rapid phase transitions predict drawdown periods?",
    required_fields=("market_phase", "timestamp"),
    data_sources=(DataSource.MARKET_CONTEXT, DataSource.EQUITY_CURVE),
    priority=QuestionPriority.P2,
    validation_rules=(
        ValidationRule("market_phase_coverage", ">=", 0.80, "Phase history required"),
    ),
)

# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY D — DECISION QUALITY
# ═══════════════════════════════════════════════════════════════════════════════

D1 = ResearchQuestion(
    id="D1",
    category=QuestionCategory.DECISION_QUALITY,
    title="Scoring components predict R",
    description="Which of the 10 scoring components best predict actual R-multiple outcomes?",
    required_fields=("score", "components", "r_multiple"),
    data_sources=(DataSource.SHADOW_TRADES, DataSource.DECISION_TRACE),
    priority=QuestionPriority.P0,
    validation_rules=(
        ValidationRule("lineage_coverage", ">=", 0.80, "Need entity_id join to link scores to outcomes"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required"),
    ),
    runner_module="research_engine.experiments.component_reward",
    runner_function="run",
    report_filename="q1_component_reward.json",
    legacy_ids=("Q1",),
)

D2 = ResearchQuestion(
    id="D2",
    category=QuestionCategory.DECISION_QUALITY,
    title="Confidence calibration",
    description="Is the system's predicted probability (p_success) calibrated to actual win rate?",
    required_fields=("score", "p_success", "r_multiple"),
    data_sources=(DataSource.DECISION_TRACE, DataSource.SHADOW_TRADES),
    priority=QuestionPriority.P0,
    validation_rules=(
        ValidationRule("lineage_coverage", ">=", 0.80, "Need trace→outcome join"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required"),
    ),
    runner_module="research_engine.experiments.legacy_canonical",
    runner_function="run_q04",
    report_filename="q4_confidence_calibration.json",
    legacy_ids=("Q4", "Q20"),
)

D3 = ResearchQuestion(
    id="D3",
    category=QuestionCategory.DECISION_QUALITY,
    title="EV filtering value",
    description="Does enabling the EV gate (blocking negative-EV trades) improve realised expectancy?",
    required_fields=("ev", "r_multiple", "policy_trade_allowed"),
    data_sources=(DataSource.SHADOW_TRADES, DataSource.DECISION_TRACE),
    priority=QuestionPriority.P1,
    validation_rules=(
        ValidationRule("lineage_coverage", ">=", 0.80, "Need decision context linked to outcomes"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required"),
    ),
    runner_module="research_engine.experiments.legacy_canonical",
    runner_function="run_q21",
    report_filename="q21_calibration_ev_impact.json",
    legacy_ids=("Q21", "Q22"),
)

D4 = ResearchQuestion(
    id="D4",
    category=QuestionCategory.DECISION_QUALITY,
    title="Optimal thresholds by context",
    description="Are score thresholds optimal when segmented by regime and market state?",
    required_fields=("score", "h4_regime", "market_state", "r_multiple"),
    data_sources=(DataSource.SHADOW_TRADES, DataSource.DECISION_TRACE),
    priority=QuestionPriority.P1,
    validation_rules=(
        ValidationRule("lineage_coverage", ">=", 0.80, "Need trace join"),
        ValidationRule("h4_regime_coverage", ">=", 0.80, "Regime required for segmentation"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required"),
    ),
    runner_module="research_engine.experiments.legacy_canonical",
    runner_function="run_q02",
    report_filename="q2_regime_threshold.json",
    legacy_ids=("Q2",),
)

D5 = ResearchQuestion(
    id="D5",
    category=QuestionCategory.DECISION_QUALITY,
    title="Missed opportunity cost",
    description="Which rejected decisions would have succeeded if allowed through?",
    required_fields=("entity_id", "terminal_stage", "r_multiple"),
    data_sources=(DataSource.DECISION_TRACE, DataSource.SHADOW_TRADES),
    priority=QuestionPriority.P1,
    validation_rules=(
        ValidationRule("lineage_coverage", ">=", 0.80, "Need trace→shadow join for rejected-but-would-have-won"),
        ValidationRule("outcome_coverage", ">=", 0.50, "Shadow outcomes for rejected signals"),
    ),
    runner_module="research_engine.experiments.legacy_canonical",
    runner_function="run_q03",
    report_filename="q3_missed_opportunity.json",
    legacy_ids=("Q3",),
)

# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY S — STRATEGY AND HORIZON
# ═══════════════════════════════════════════════════════════════════════════════

S1 = ResearchQuestion(
    id="S1",
    category=QuestionCategory.STRATEGY_HORIZON,
    title="Strategy expectancy by type",
    description="Does each strategy type (REVERSAL/CONTINUATION/FALSE_BREAK) have positive expectancy independently?",
    required_fields=("strategy", "r_multiple"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P0,
    validation_rules=(
        ValidationRule("strategy_coverage", ">=", 0.50, "Clean strategy values required"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required"),
    ),
    runner_module="research_engine.experiments.legacy_canonical",
    runner_function="run_q24",
    report_filename="q24_strategy_edge.json",
    legacy_ids=("Q24",),
)

S2 = ResearchQuestion(
    id="S2",
    category=QuestionCategory.STRATEGY_HORIZON,
    title="Horizon affects expectancy",
    description="Does trade horizon (SCALP/INTRADAY/EXTENDED) independently affect expectancy?",
    required_fields=("trade_horizon", "r_multiple"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P1,
    validation_rules=(
        ValidationRule("horizon_coverage", ">=", 0.50, "Separate horizon field required"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required"),
    ),
)

S3 = ResearchQuestion(
    id="S3",
    category=QuestionCategory.STRATEGY_HORIZON,
    title="Strategy × horizon combinations",
    description="Which strategy × horizon combinations work? (e.g. REVERSAL+SCALP vs CONTINUATION+EXTENDED)",
    required_fields=("strategy", "trade_horizon", "r_multiple"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P1,
    validation_rules=(
        ValidationRule("strategy_coverage", ">=", 0.50, "Clean strategy required"),
        ValidationRule("horizon_coverage", ">=", 0.50, "Separate horizon required"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required"),
    ),
    depends_on=("S1", "S2"),
)

S4 = ResearchQuestion(
    id="S4",
    category=QuestionCategory.STRATEGY_HORIZON,
    title="Strategies specialised for phases",
    description="Are strategies specialised for certain market phases? (e.g. REVERSAL only in EXHAUSTION)",
    required_fields=("strategy", "market_phase", "r_multiple"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P1,
    validation_rules=(
        ValidationRule("strategy_coverage", ">=", 0.50, "Clean strategy required"),
        ValidationRule("market_phase_coverage", ">=", 0.80, "Phase required"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required"),
    ),
    depends_on=("E3", "M3"),
)

# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY X — EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════

X1 = ResearchQuestion(
    id="X1",
    category=QuestionCategory.EXECUTION,
    title="Slippage model",
    description="What is the real slippage model per symbol per session?",
    required_fields=("symbol", "slippage", "session"),
    data_sources=(DataSource.SLIPPAGE_JOURNAL, DataSource.TRADE_TRUTH),
    priority=QuestionPriority.P2,
    validation_rules=(),
    runner_module="research_engine.experiments.legacy_canonical",
    runner_function="run_q11",
    report_filename="q11_slippage_model.json",
    legacy_ids=("Q11",),
)

X2 = ResearchQuestion(
    id="X2",
    category=QuestionCategory.EXECUTION,
    title="Broker failure patterns",
    description="Are broker rejections/failures predictable by time, symbol, or market condition?",
    required_fields=("retcode", "symbol", "timestamp"),
    data_sources=(DataSource.EXECUTION_CONTEXT,),
    priority=QuestionPriority.P2,
    validation_rules=(),
    runner_module="research_engine.experiments.legacy_canonical",
    runner_function="run_q12",
    report_filename="q12_broker_reliability.json",
    legacy_ids=("Q12",),
)

X3 = ResearchQuestion(
    id="X3",
    category=QuestionCategory.EXECUTION,
    title="Session execution quality",
    description="Which trading sessions produce the best execution quality (lowest slippage, fewest rejects)?",
    required_fields=("symbol", "session", "slippage", "fill_latency"),
    data_sources=(DataSource.SLIPPAGE_JOURNAL, DataSource.EXECUTION_CONTEXT),
    priority=QuestionPriority.P2,
    validation_rules=(),
    runner_module="research_engine.experiments.legacy_canonical",
    runner_function="run_q09",
    report_filename="q9_spread_fill_quality.json",
    legacy_ids=("Q9",),
)

X4 = ResearchQuestion(
    id="X4",
    category=QuestionCategory.EXECUTION,
    title="Edge lost in execution",
    description="How much theoretical edge (from shadow R) is lost during real execution?",
    required_fields=("entity_id", "shadow_r_multiple", "live_r_multiple"),
    data_sources=(DataSource.SHADOW_TRADES, DataSource.TRADE_TRUTH),
    priority=QuestionPriority.P1,
    validation_rules=(
        ValidationRule("lineage_coverage", ">=", 0.80, "Need shadow↔live outcome join"),
    ),
    runner_module="research_engine.experiments.shadow_validation",
    runner_function="run",
    report_filename="q16_shadow_validation.json",
    legacy_ids=("Q16",),
)

# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY L — SYSTEM LEARNING
# ═══════════════════════════════════════════════════════════════════════════════

L1 = ResearchQuestion(
    id="L1",
    category=QuestionCategory.SYSTEM_LEARNING,
    title="Pattern degradation",
    description="Are patterns degrading in performance over time?",
    required_fields=("pattern", "r_multiple", "entry_time"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P2,
    validation_rules=(
        ValidationRule("pattern_coverage", ">=", 0.50, "Pattern required"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required"),
    ),
    runner_module="research_engine.experiments.legacy_canonical",
    runner_function="run_q05",
    report_filename="q5_pattern_degradation.json",
    legacy_ids=("Q5",),
)

L2 = ResearchQuestion(
    id="L2",
    category=QuestionCategory.SYSTEM_LEARNING,
    title="System improvement tracking",
    description="Does the system improve after architecture changes? (compare pre/post periods)",
    required_fields=("r_multiple", "entry_time", "schema_version"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P3,
    validation_rules=(),
    runner_module="research_engine.experiments.legacy_canonical",
    runner_function="run_q15",
    report_filename="q15_learning_velocity.json",
    legacy_ids=("Q15",),
)

L3 = ResearchQuestion(
    id="L3",
    category=QuestionCategory.SYSTEM_LEARNING,
    title="Architecture assumptions valid",
    description="Are the scoring weight assumptions, regime classifications, and strategy mappings still correct?",
    required_fields=("components", "strategy", "regime", "r_multiple"),
    data_sources=(DataSource.SHADOW_TRADES, DataSource.DECISION_TRACE),
    priority=QuestionPriority.P2,
    validation_rules=(
        ValidationRule("lineage_coverage", ">=", 0.80, "Need decision context linked to outcomes"),
        ValidationRule("strategy_coverage", ">=", 0.50, "Clean strategy required"),
    ),
    runner_module="research_engine.experiments.component_reward",
    runner_function="run",
    report_filename="q1_component_reward.json",
    legacy_ids=("Q1",),
)

L4 = ResearchQuestion(
    id="L4",
    category=QuestionCategory.SYSTEM_LEARNING,
    title="Market behaviour drift",
    description="Does market behaviour change over time in ways that invalidate strategy assumptions?",
    required_fields=("h4_regime", "pattern", "r_multiple", "entry_time"),
    data_sources=(DataSource.SHADOW_TRADES, DataSource.MARKET_CONTEXT),
    priority=QuestionPriority.P3,
    validation_rules=(
        ValidationRule("h4_regime_coverage", ">=", 0.80, "Regime history required"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required"),
    ),
    legacy_ids=("Q17",),
    runner_module="research_engine.experiments.legacy_canonical",
    runner_function="run_q17",
    report_filename="q17_drawdown_precursors.json",
)

# ═══════════════════════════════════════════════════════════════════════════════
# ADDITIONAL MARKET CONTEXT (M6–M8)
# ═══════════════════════════════════════════════════════════════════════════════

M6 = ResearchQuestion(
    id="M6",
    category=QuestionCategory.MARKET_CONTEXT,
    title="Market phase expectancy",
    description="Which market phases (IMPULSE/PULLBACK/CONSOLIDATION/EXHAUSTION/REVERSAL) contain real edge?",
    required_fields=("market_phase", "r_multiple"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P1,
    validation_rules=(
        ValidationRule("market_phase_coverage", ">=", 0.80, "Phase must be populated"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required"),
    ),
)

M7 = ResearchQuestion(
    id="M7",
    category=QuestionCategory.MARKET_CONTEXT,
    title="Regime + phase interaction",
    description="Does combining regime and phase improve predictive power beyond either alone?",
    required_fields=("h4_regime", "market_phase", "r_multiple"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P1,
    validation_rules=(
        ValidationRule("h4_regime_coverage", ">=", 0.80, "H4 regime required"),
        ValidationRule("market_phase_coverage", ">=", 0.80, "Phase required"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required"),
    ),
    depends_on=("M1", "M6"),
)

M8 = ResearchQuestion(
    id="M8",
    category=QuestionCategory.MARKET_CONTEXT,
    title="Phase transition behaviour",
    description="Do market phase transitions (e.g. IMPULSE→EXHAUSTION) predict future trade outcomes?",
    required_fields=("market_phase", "entry_time", "r_multiple"),
    data_sources=(DataSource.MARKET_CONTEXT, DataSource.SHADOW_TRADES),
    priority=QuestionPriority.P2,
    validation_rules=(
        ValidationRule("market_phase_coverage", ">=", 0.80, "Phase history required"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required"),
    ),
)

# ═══════════════════════════════════════════════════════════════════════════════
# ADDITIONAL STRATEGY & HORIZON (S5–S7)
# ═══════════════════════════════════════════════════════════════════════════════

S5 = ResearchQuestion(
    id="S5",
    category=QuestionCategory.STRATEGY_HORIZON,
    title="Strategy identity expectancy",
    description="Which strategy identities (REVERSAL/CONTINUATION/FALSE_BREAK) contain real expectancy independently of horizon?",
    required_fields=("strategy", "r_multiple"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P0,
    validation_rules=(
        ValidationRule("strategy_coverage", ">=", 0.50, "Clean strategy required"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required"),
    ),
)

S6 = ResearchQuestion(
    id="S6",
    category=QuestionCategory.STRATEGY_HORIZON,
    title="Horizon expectancy",
    description="Which trade horizons (SCALP/INTRADAY/EXTENDED) contain real expectancy independently of strategy?",
    required_fields=("trade_horizon", "r_multiple"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P1,
    validation_rules=(
        ValidationRule("horizon_coverage", ">=", 0.50, "Separate horizon field required"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required"),
    ),
)

S7 = ResearchQuestion(
    id="S7",
    category=QuestionCategory.STRATEGY_HORIZON,
    title="Strategy × horizon interaction",
    description="Are strategies profitable only at specific horizons? (e.g. REVERSAL works at SCALP but not EXTENDED)",
    required_fields=("strategy", "trade_horizon", "r_multiple"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P1,
    validation_rules=(
        ValidationRule("strategy_coverage", ">=", 0.50, "Clean strategy required"),
        ValidationRule("horizon_coverage", ">=", 0.50, "Separate horizon required"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required"),
    ),
    depends_on=("S5", "S6"),
)

# ═══════════════════════════════════════════════════════════════════════════════
# ADDITIONAL EXECUTION (X5–X6)
# ═══════════════════════════════════════════════════════════════════════════════

X5 = ResearchQuestion(
    id="X5",
    category=QuestionCategory.EXECUTION,
    title="Execution leakage",
    description="How much expected edge is lost between decision (shadow R) and execution (live R)?",
    required_fields=("entity_id", "r_multiple", "ev"),
    data_sources=(DataSource.SHADOW_TRADES, DataSource.TRADE_TRUTH),
    priority=QuestionPriority.P1,
    validation_rules=(
        ValidationRule("lineage_coverage", ">=", 0.80, "Need shadow↔live join"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required"),
    ),
)

X6 = ResearchQuestion(
    id="X6",
    category=QuestionCategory.EXECUTION,
    title="Execution stability",
    description="Under what conditions (symbol, session, spread, volatility) does execution quality degrade?",
    required_fields=("symbol", "slippage", "spread"),
    data_sources=(DataSource.SLIPPAGE_JOURNAL, DataSource.EXECUTION_CONTEXT),
    priority=QuestionPriority.P2,
    validation_rules=(),
)

# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY R — RISK MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

R1 = ResearchQuestion(
    id="R1",
    category=QuestionCategory.RISK_MANAGEMENT,
    title="Risk model effectiveness",
    description="Does the risk layer (guards, daily loss limit, exposure) improve overall expectancy and survival?",
    required_fields=("entity_id", "r_multiple", "terminal_stage"),
    data_sources=(DataSource.DECISION_TRACE, DataSource.SHADOW_TRADES),
    priority=QuestionPriority.P1,
    validation_rules=(
        ValidationRule("lineage_coverage", ">=", 0.80, "Need decision→outcome lineage"),
        ValidationRule("outcome_coverage", ">=", 0.50, "Outcomes for both allowed and blocked trades"),
    ),
    runner_module="research_engine.experiments.legacy_canonical",
    runner_function="run_q10",
    report_filename="q10_guard_efficacy.json",
    legacy_ids=("Q10",),
)

R2 = ResearchQuestion(
    id="R2",
    category=QuestionCategory.RISK_MANAGEMENT,
    title="Guard value analysis",
    description="Did each individual guard (spread, correlation, regime, daily loss) improve final expectancy?",
    required_fields=("entity_id", "terminal_stage", "terminal_reason", "r_multiple"),
    data_sources=(DataSource.DECISION_TRACE, DataSource.SHADOW_TRADES),
    priority=QuestionPriority.P1,
    validation_rules=(
        ValidationRule("lineage_coverage", ">=", 0.80, "Need trace→shadow join for counterfactual"),
        ValidationRule("outcome_coverage", ">=", 0.50, "Shadow outcomes for rejected signals needed"),
    ),
    runner_module="research_engine.experiments.legacy_canonical",
    runner_function="run_q10",
    report_filename="q10_guard_efficacy.json",
    legacy_ids=("Q10",),
)

# ═══════════════════════════════════════════════════════════════════════════════
# ADDITIONAL SYSTEM LEARNING (L5–L6)
# ═══════════════════════════════════════════════════════════════════════════════

L5 = ResearchQuestion(
    id="L5",
    category=QuestionCategory.SYSTEM_LEARNING,
    title="Model drift detection",
    description="Do patterns, strategies, or market assumptions lose predictive power over time?",
    required_fields=("pattern", "strategy", "r_multiple", "entry_time"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P2,
    validation_rules=(
        ValidationRule("pattern_coverage", ">=", 0.50, "Pattern required"),
        ValidationRule("strategy_coverage", ">=", 0.50, "Strategy required"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required"),
    ),
)

L6 = ResearchQuestion(
    id="L6",
    category=QuestionCategory.SYSTEM_LEARNING,
    title="Research confidence scoring",
    description="How trustworthy is each research conclusion given dataset validity, coverage, and sample size?",
    required_fields=("r_multiple", "pattern"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P1,
    validation_rules=(
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required for confidence assessment"),
    ),
)

# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY G — DATA GOVERNANCE
# ═══════════════════════════════════════════════════════════════════════════════

G1 = ResearchQuestion(
    id="G1",
    category=QuestionCategory.DATA_GOVERNANCE,
    title="Dataset completeness",
    description="Is the current dataset suitable for the intended research questions? (field coverage, source type, sample size)",
    required_fields=("r_multiple",),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P0,
    validation_rules=(
        ValidationRule("outcome_coverage", ">=", 0.50, "At least half of records must have outcomes"),
    ),
)

G2 = ResearchQuestion(
    id="G2",
    category=QuestionCategory.DATA_GOVERNANCE,
    title="Lineage coverage",
    description="What percentage of trades have valid decision-to-outcome lineage (entity_id join)?",
    required_fields=("entity_id", "r_multiple"),
    data_sources=(DataSource.SHADOW_TRADES, DataSource.DECISION_TRACE),
    priority=QuestionPriority.P0,
    validation_rules=(
        ValidationRule("lineage_coverage", ">=", 0.50, "At least 50% lineage for this meta-question"),
    ),
)

G3 = ResearchQuestion(
    id="G3",
    category=QuestionCategory.DATA_GOVERNANCE,
    title="Research validity assessment",
    description="Can research conclusions be trusted given current validation status, coverage percentages, and sample sizes?",
    required_fields=("r_multiple", "pattern"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P0,
    validation_rules=(
        ValidationRule("outcome_coverage", ">=", 0.80, "Strong outcome coverage needed for validity assessment"),
        ValidationRule("pattern_coverage", ">=", 0.50, "Pattern coverage needed"),
    ),
)

# ═══════════════════════════════════════════════════════════════════════════════
# EXPANDED RISK MANAGEMENT (R3–R5)
# ═══════════════════════════════════════════════════════════════════════════════

R3 = ResearchQuestion(
    id="R3",
    category=QuestionCategory.RISK_MANAGEMENT,
    title="Probability of ruin",
    description="Given the measured edge, win rate, variance and position sizing, what is the probability that this system eventually reaches catastrophic drawdown?",
    required_fields=("r_multiple", "win_rate", "position_size"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P0,
    validation_rules=(
        ValidationRule("outcome_coverage", ">=", 0.95, "Nearly all records must have R-multiple for variance estimation"),
        ValidationRule("lineage_coverage", ">=", 0.80, "Need full lifecycle for accurate win rate"),
    ),
    depends_on=("E1",),
    runner_module="research_engine.experiments.probability_of_ruin",
    runner_function="run_probability_of_ruin",
    report_filename="r3_probability_of_ruin.json",
)

R4 = ResearchQuestion(
    id="R4",
    category=QuestionCategory.RISK_MANAGEMENT,
    title="Drawdown halt threshold",
    description="At what realised drawdown should the system automatically suspend trading because historical recovery probability becomes unacceptable?",
    required_fields=("r_multiple", "entry_time"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P0,
    validation_rules=(
        ValidationRule("outcome_coverage", ">=", 0.95, "Full outcome history required for drawdown modelling"),
    ),
    depends_on=("R3",),
    runner_module="research_engine.experiments.drawdown_threshold",
    runner_function="run_drawdown_threshold",
    report_filename="r4_drawdown_threshold.json",
)

R5 = ResearchQuestion(
    id="R5",
    category=QuestionCategory.RISK_MANAGEMENT,
    title="Position sizing optimisation",
    description="What position sizing model (fixed risk, Kelly, half-Kelly, fractional Kelly, fixed lot, dynamic) maximises long-term growth while respecting acceptable drawdown?",
    required_fields=("r_multiple", "win_rate"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P1,
    validation_rules=(
        ValidationRule("outcome_coverage", ">=", 0.95, "Full outcome distribution required for Kelly calculation"),
    ),
    depends_on=("E1", "R3"),
    runner_module="research_engine.experiments.position_sizing",
    runner_function="run_position_sizing",
    report_filename="r5_position_sizing.json",
)

# ═══════════════════════════════════════════════════════════════════════════════
# EXPANDED SYSTEM EDGE (E5)
# ═══════════════════════════════════════════════════════════════════════════════

E5 = ResearchQuestion(
    id="E5",
    category=QuestionCategory.SYSTEM_EDGE,
    title="Out-of-sample validation",
    description="Does the measured edge survive on unseen market data using walk-forward testing with rolling windows?",
    required_fields=("r_multiple", "entry_time", "pattern"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P0,
    validation_rules=(
        ValidationRule("outcome_coverage", ">=", 0.95, "Full outcomes needed for walk-forward split"),
        ValidationRule("lineage_coverage", ">=", 0.80, "Need decision context to prevent lookahead"),
    ),
    depends_on=("E1",),
    runner_module="research_engine.experiments.out_of_sample_validation",
    runner_function="run_out_of_sample_validation",
    report_filename="e5_out_of_sample.json",
)

# ═══════════════════════════════════════════════════════════════════════════════
# EXPANDED DECISION QUALITY (D6)
# ═══════════════════════════════════════════════════════════════════════════════

D6 = ResearchQuestion(
    id="D6",
    category=QuestionCategory.DECISION_QUALITY,
    title="Portfolio ranking quality",
    description="When multiple trades are available simultaneously, is the ranking model consistently choosing the highest expectancy opportunity?",
    required_fields=("entity_id", "r_multiple", "cycle_id"),
    data_sources=(DataSource.SHADOW_TRADES, DataSource.DECISION_TRACE),
    priority=QuestionPriority.P1,
    validation_rules=(
        ValidationRule("lineage_coverage", ">=", 0.80, "Need full decision lineage for ranking evaluation"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required to measure ranking accuracy"),
    ),
    runner_module="research_engine.experiments.portfolio_ranking",
    runner_function="run_portfolio_ranking",
    report_filename="d6_portfolio_ranking.json",
)

# ═══════════════════════════════════════════════════════════════════════════════
# EXPANDED SYSTEM LEARNING (L7)
# ═══════════════════════════════════════════════════════════════════════════════

L7 = ResearchQuestion(
    id="L7",
    category=QuestionCategory.SYSTEM_LEARNING,
    title="Shadow A/B validation",
    description="Does a proposed strategy change outperform the currently promoted version when evaluated in shadow mode with statistical significance?",
    required_fields=("r_multiple", "strategy", "schema_version"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P0,
    validation_rules=(
        ValidationRule("outcome_coverage", ">=", 0.95, "Full outcomes needed for significance testing"),
        ValidationRule("strategy_coverage", ">=", 0.50, "Clean strategy labels needed to separate control vs candidate"),
    ),
    depends_on=("E1", "E5"),
    runner_module="research_engine.experiments.shadow_ab_validation",
    runner_function="run_shadow_ab_validation",
    report_filename="l7_shadow_ab_validation.json",
)

# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY P — PROMOTION INTELLIGENCE
# ═══════════════════════════════════════════════════════════════════════════════

P1 = ResearchQuestion(
    id="P1",
    category=QuestionCategory.PROMOTION_INTELLIGENCE,
    title="Promotion impact analysis",
    description="If a specific recommendation is promoted into production, what measurable improvement in EV, win rate, drawdown, trade frequency, and risk is expected?",
    required_fields=("r_multiple", "strategy", "pattern", "entry_time"),
    data_sources=(DataSource.SHADOW_TRADES, DataSource.DECISION_TRACE),
    priority=QuestionPriority.P0,
    validation_rules=(
        ValidationRule("outcome_coverage", ">=", 0.95, "Full outcomes needed for impact estimation"),
        ValidationRule("lineage_coverage", ">=", 0.80, "Need decision context for change modelling"),
        ValidationRule("strategy_coverage", ">=", 0.50, "Clean strategy required for segmentation"),
    ),
    depends_on=("E1", "E5", "R3"),
    runner_module="research_engine.experiments.promotion_impact",
    runner_function="run_promotion_impact",
    report_filename="p1_promotion_impact.json",
)

# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY-CONTEXT ALIGNMENT (M9–M11)
# ═══════════════════════════════════════════════════════════════════════════════

M9 = ResearchQuestion(
    id="M9",
    category=QuestionCategory.MARKET_CONTEXT,
    title="Phase-appropriate pattern classification",
    description="For each market phase (IMPULSE, PULLBACK, CONSOLIDATION, EXHAUSTION, REVERSAL), which patterns or behaviours actually belong there and produce positive expectancy?",
    required_fields=("market_phase", "pattern", "r_multiple"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P0,
    validation_rules=(
        ValidationRule("market_phase_coverage", ">=", 0.80, "Phase must be populated to stratify by phase"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required"),
    ),
    depends_on=("M6", "E2"),
    runner_module="research_engine.experiments.m9_phase_pattern",
    runner_function="run_m9_phase_pattern",
    report_filename="m9_phase_pattern.json",
)

M10 = ResearchQuestion(
    id="M10",
    category=QuestionCategory.MARKET_CONTEXT,
    title="Strategy family required per phase",
    description="Does each market phase require a different strategy family (reversal, continuation, momentum, breakout) rather than different pattern weighting within one family?",
    required_fields=("market_phase", "strategy", "pattern", "r_multiple"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P0,
    validation_rules=(
        ValidationRule("market_phase_coverage", ">=", 0.80, "Phase required for stratification"),
        ValidationRule("strategy_coverage", ">=", 0.50, "Clean strategy labels needed"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required"),
    ),
    depends_on=("M9", "S1"),
    runner_module="research_engine.experiments.m10_strategy_family_per_phase",
    runner_function="run_m10_strategy_family_per_phase",
    report_filename="m10_strategy_family_per_phase.json",
)

M11 = ResearchQuestion(
    id="M11",
    category=QuestionCategory.MARKET_CONTEXT,
    title="Context predictive value vs pattern",
    description="Does market context (regime + phase + bias) provide more predictive value for trade outcomes than the pattern identity itself?",
    required_fields=("h4_regime", "market_phase", "pattern", "r_multiple"),
    data_sources=(DataSource.SHADOW_TRADES, DataSource.DECISION_TRACE),
    priority=QuestionPriority.P0,
    validation_rules=(
        ValidationRule("h4_regime_coverage", ">=", 0.80, "Regime required"),
        ValidationRule("market_phase_coverage", ">=", 0.80, "Phase required"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Outcome required"),
        ValidationRule("lineage_coverage", ">=", 0.80, "Need decision context linked to outcomes"),
    ),
    depends_on=("M1", "M9"),
)

# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY EX — EXIT MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

EX1 = ResearchQuestion(
    id="EX1",
    category=QuestionCategory.EXIT_MANAGEMENT,
    title="Exit policy improves EV",
    description="Does modifying exit policy (trailing stop, reduced TP, time-based) improve system expected value compared to the current max_bars timeout?",
    required_fields=("pnl_r_multiple", "mfe_r", "mae_r", "exit_reason", "bars_held", "trade_state_progression"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P0,
    validation_rules=(
        ValidationRule("outcome_coverage", ">=", 0.95, "Need R-multiple for almost all trades"),
        ValidationRule("sample_size", ">=", 200, "Need sufficient trades for paired comparison"),
    ),
)

EX2 = ResearchQuestion(
    id="EX2",
    category=QuestionCategory.EXIT_MANAGEMENT,
    title="Trailing stop improves MFE capture",
    description="Does a trailing stop mechanism capture more of the available MFE than the current exit, using bar-by-bar sequential simulation?",
    required_fields=("mfe_r", "pnl_r_multiple", "trade_state_progression"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P0,
    validation_rules=(
        ValidationRule("outcome_coverage", ">=", 0.95, "Need outcomes for comparison"),
        ValidationRule("sample_size", ">=", 200, "Need sufficient trades for statistical test"),
    ),
)

EX3 = ResearchQuestion(
    id="EX3",
    category=QuestionCategory.EXIT_MANAGEMENT,
    title="Optimal TP distance",
    description="What take-profit distance maximises expectancy? Tests TP at 0.25R through 3.0R using MFE data to determine reachability.",
    required_fields=("mfe_r", "mae_r", "pnl_r_multiple"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P0,
    validation_rules=(
        ValidationRule("outcome_coverage", ">=", 0.95, "Need R + MFE for simulation"),
        ValidationRule("sample_size", ">=", 200, "Need sufficient sample per TP level"),
    ),
)

EX4 = ResearchQuestion(
    id="EX4",
    category=QuestionCategory.EXIT_MANAGEMENT,
    title="Optimal SL distance",
    description="Does the current SL distance preserve signal quality, or does widening/tightening SL improve outcomes?",
    required_fields=("mae_r", "mfe_r", "pnl_r_multiple", "exit_reason"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P1,
    validation_rules=(
        ValidationRule("outcome_coverage", ">=", 0.95, "Need outcome data"),
        ValidationRule("sample_size", ">=", 200, "Need per-SL-variant comparison"),
    ),
)

EX5 = ResearchQuestion(
    id="EX5",
    category=QuestionCategory.EXIT_MANAGEMENT,
    title="Horizon changes optimal exit",
    description="Does trade horizon (SCALP/INTRADAY/EXTENDED) require a different exit policy? Tests trailing parameters per horizon.",
    required_fields=("trade_horizon", "mfe_r", "pnl_r_multiple", "trade_state_progression"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P1,
    validation_rules=(
        ValidationRule("horizon_coverage", ">=", 0.50, "Need horizon field populated"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Need outcomes"),
    ),
    depends_on=("EX2",),
)

EX6 = ResearchQuestion(
    id="EX6",
    category=QuestionCategory.EXIT_MANAGEMENT,
    title="Exit depends on strategy family",
    description="Does each strategy family (REVERSAL/MOMENTUM/CONTINUATION) require a different exit policy?",
    required_fields=("strategy", "pattern", "mfe_r", "pnl_r_multiple", "trade_state_progression"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P1,
    validation_rules=(
        ValidationRule("strategy_coverage", ">=", 0.50, "Need strategy field for segmentation"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Need outcomes"),
    ),
    depends_on=("EX2",),
)

EX7 = ResearchQuestion(
    id="EX7",
    category=QuestionCategory.EXIT_MANAGEMENT,
    title="Exit depends on market regime",
    description="Does market regime (TRENDING/RANGING/TRANSITIONAL) require a different exit policy?",
    required_fields=("h4_regime", "mfe_r", "pnl_r_multiple", "trade_state_progression"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P1,
    validation_rules=(
        ValidationRule("h4_regime_coverage", ">=", 0.80, "Need regime for segmentation"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Need outcomes"),
    ),
    depends_on=("EX2",),
)

EX8 = ResearchQuestion(
    id="EX8",
    category=QuestionCategory.EXIT_MANAGEMENT,
    title="Exit depends on pattern type",
    description="Do different candlestick patterns require different exit policies based on their MFE/MAE profiles?",
    required_fields=("pattern", "mfe_r", "mae_r", "pnl_r_multiple", "trade_state_progression"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P2,
    validation_rules=(
        ValidationRule("pattern_coverage", ">=", 0.50, "Need pattern field"),
        ValidationRule("outcome_coverage", ">=", 0.95, "Need outcomes"),
    ),
    depends_on=("EX2",),
)

EX9 = ResearchQuestion(
    id="EX9",
    category=QuestionCategory.EXIT_MANAGEMENT,
    title="Exit reduces timeout losses",
    description="Does the proposed exit policy reduce the timeout exit rate and convert timeout losses into captured profits?",
    required_fields=("exit_reason", "bars_held", "mfe_r", "pnl_r_multiple", "trade_state_progression"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P0,
    validation_rules=(
        ValidationRule("outcome_coverage", ">=", 0.95, "Need exit_reason + outcome"),
        ValidationRule("sample_size", ">=", 200, "Need sufficient trades"),
    ),
)

EX10 = ResearchQuestion(
    id="EX10",
    category=QuestionCategory.EXIT_MANAGEMENT,
    title="Exit survives walk-forward",
    description="Does the exit policy improvement hold on out-of-sample data using time-ordered walk-forward validation?",
    required_fields=("pnl_r_multiple", "trade_state_progression", "entry_time"),
    data_sources=(DataSource.SHADOW_TRADES,),
    priority=QuestionPriority.P0,
    validation_rules=(
        ValidationRule("outcome_coverage", ">=", 0.95, "Need outcomes for train/test split"),
        ValidationRule("sample_size", ">=", 200, "Need sufficient sample for temporal split"),
    ),
    depends_on=("EX1", "EX2"),
)


# ═══════════════════════════════════════════════════════════════════════════════
# REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

REGISTRY: tuple[ResearchQuestion, ...] = (
    # System Edge
    E1, E2, E3, E4, E5,
    # Market Context
    M1, M2, M3, M4, M5, M6, M7, M8, M9, M10, M11,
    # Decision Quality
    D1, D2, D3, D4, D5, D6,
    # Strategy & Horizon
    S1, S2, S3, S4, S5, S6, S7,
    # Execution
    X1, X2, X3, X4, X5, X6,
    # Risk Management
    R1, R2, R3, R4, R5,
    # System Learning
    L1, L2, L3, L4, L5, L6, L7,
    # Data Governance
    G1, G2, G3,
    # Promotion Intelligence
    P1,
    # Exit Management
    EX1, EX2, EX3, EX4, EX5, EX6, EX7, EX8, EX9, EX10,
)

REGISTRY_BY_ID: dict[str, ResearchQuestion] = {q.id: q for q in REGISTRY}


def get_question(question_id: str) -> ResearchQuestion | None:
    """Look up a question by ID. Returns None if not found."""
    return REGISTRY_BY_ID.get(question_id)


def get_questions_by_category(category: QuestionCategory) -> list[ResearchQuestion]:
    """Return all questions in a given category."""
    return [q for q in REGISTRY if q.category == category]


def get_questions_by_priority(priority: QuestionPriority) -> list[ResearchQuestion]:
    """Return all questions at a given priority level."""
    return [q for q in REGISTRY if q.priority == priority]
