"""
═══════════════════════════════════════════════════════════════════════════════
NEW-ENGINE CANONICAL QUESTION BANK
═══════════════════════════════════════════════════════════════════════════════

This is the SINGLE authoritative research question registry for the new engine.

All previous registries are superseded:
    - research_engine/registry/research_question_registry.py (61 questions, legacy)
    - research_engine/registry/v10_research_registry.py (23 questions, V10 formal)
    - research_engine/v10/research_intelligence/question_registry.py (12, Lambda)

Those files remain for historical reference ONLY.
They are NOT runtime dependencies of this registry.

ID Scheme:
    E-nnn   = Execution angle primary
    D-nnn   = Decision angle primary
    M-nnn   = Market angle primary
    S-nnn   = Strategy angle primary
    ED-nnn  = Execution + Decision cross-angle
    EM-nnn  = Execution + Market cross-angle
    DM-nnn  = Decision + Market cross-angle
    DS-nnn  = Decision + Strategy cross-angle
    MS-nnn  = Market + Strategy cross-angle
    EDM-nnn = Execution + Decision + Market
    EDS-nnn = Execution + Decision + Strategy
    DMS-nnn = Decision + Market + Strategy
    EDMS-nnn = All four angles

Created: 2026-08-08
"""

from __future__ import annotations

from research_engine.v10.universes.models import (
    AnalysisType,
    AngleRequirement,
    JoinRequirement,
    JoinType,
    NewEngineQuestion,
    Population,
    QuestionStatus,
    Universe,
    ViewType,
)


# ═══════════════════════════════════════════════════════════════════════════════
# EXECUTION-PRIMARY QUESTIONS (E-nnn)
# Questions whose primary analytical angle is trade execution outcomes.
# ═══════════════════════════════════════════════════════════════════════════════

E_001 = NewEngineQuestion(
    question_id="E-001",
    title="System Expectancy",
    research_intent=(
        "What is the realised expectancy (mean R-multiple) of the production "
        "pipeline? Is the system generating positive expected value per trade?"
    ),
    required_universes=(Universe.EXECUTION,),
    required_populations=(Population.ALL_TRADES,),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.EXECUTION,
            populations=(Population.ALL_TRADES, Population.WINNING_TRADES, Population.LOSING_TRADES),
            required_fields=("r_multiple", "net_realised_pnl", "direction"),
        ),
    ),
    views=(ViewType.NORMAL, ViewType.ANOMALOUS),
    analysis_type=AnalysisType.EXPECTANCY,
    minimum_sample_size=20,
    status=QuestionStatus.READY,
    source_intent=("E1", "V10-E1", "Lambda-E1"),
    decision_enabled="Should we continue trading this system live?",
)

E_002 = NewEngineQuestion(
    question_id="E-002",
    title="Win/Loss Distribution Shape",
    research_intent=(
        "What is the shape of the win/loss distribution? Are wins larger than "
        "losses? Is variance acceptable for the measured edge?"
    ),
    required_universes=(Universe.EXECUTION,),
    required_populations=(Population.ALL_TRADES, Population.WINNING_TRADES, Population.LOSING_TRADES),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.EXECUTION,
            populations=(Population.ALL_TRADES,),
            required_fields=("r_multiple", "duration_seconds", "exit_reason"),
        ),
    ),
    views=(ViewType.NORMAL,),
    analysis_type=AnalysisType.DISTRIBUTION,
    minimum_sample_size=30,
    status=QuestionStatus.READY,
    source_intent=("E1", "V10-E1", "V10-R2"),
    decision_enabled="Is variance acceptable? Do we need position sizing adjustment?",
)

E_003 = NewEngineQuestion(
    question_id="E-003",
    title="Exit Reason Distribution",
    research_intent=(
        "What percentage of trades exit via stop loss vs take profit vs time "
        "exit vs manual close? Is the SL/TP ratio healthy?"
    ),
    required_universes=(Universe.EXECUTION,),
    required_populations=(Population.ALL_TRADES,),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.EXECUTION,
            populations=(Population.ALL_TRADES,),
            required_fields=("exit_reason", "r_multiple", "duration_seconds"),
        ),
    ),
    views=(ViewType.NORMAL,),
    analysis_type=AnalysisType.SEGMENTATION,
    minimum_sample_size=20,
    status=QuestionStatus.READY,
    source_intent=("V10-EX1", "EX1"),
    decision_enabled="Are we exiting too early/late? Is SL/TP ratio healthy?",
)

E_004 = NewEngineQuestion(
    question_id="E-004",
    title="Execution Quality by Session",
    research_intent=(
        "Which trading sessions produce the best execution quality — lowest "
        "slippage, fastest fills, fewest broker rejections?"
    ),
    required_universes=(Universe.EXECUTION,),
    required_populations=(
        Population.ALL_TRADES, Population.SESSION_LONDON,
        Population.SESSION_NY, Population.SESSION_ASIA,
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.EXECUTION,
            populations=(Population.ALL_TRADES,),
            required_fields=("symbol", "entry_time", "duration_seconds", "r_multiple"),
        ),
    ),
    views=(ViewType.NORMAL, ViewType.ANOMALOUS),
    analysis_type=AnalysisType.SEGMENTATION,
    minimum_sample_size=10,
    status=QuestionStatus.READY,
    source_intent=("X1", "X3", "V10-X1"),
    decision_enabled="Should we restrict trading to specific sessions?",
)

E_005 = NewEngineQuestion(
    question_id="E-005",
    title="Probability of Ruin",
    research_intent=(
        "Given measured edge, win rate, and variance, what is the probability "
        "the system reaches catastrophic drawdown under current position sizing?"
    ),
    required_universes=(Universe.EXECUTION,),
    required_populations=(Population.ALL_TRADES,),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.EXECUTION,
            populations=(Population.ALL_TRADES,),
            required_fields=("r_multiple", "volume"),
        ),
    ),
    views=(ViewType.NORMAL,),
    analysis_type=AnalysisType.SIMULATION,
    minimum_sample_size=50,
    dependencies=("E-001",),
    status=QuestionStatus.PARTIAL,
    source_intent=("R3", "V10-R2"),
    decision_enabled="Is the system safe at current sizing? Need to reduce risk?",
)

E_006 = NewEngineQuestion(
    question_id="E-006",
    title="Out-of-Sample Edge Validation",
    research_intent=(
        "Does the measured edge survive walk-forward testing on unseen data? "
        "Is the system overfitted to historical conditions?"
    ),
    required_universes=(Universe.EXECUTION,),
    required_populations=(Population.ALL_TRADES,),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.EXECUTION,
            populations=(Population.ALL_TRADES,),
            required_fields=("r_multiple", "entry_time", "symbol"),
        ),
    ),
    views=(ViewType.NORMAL,),
    analysis_type=AnalysisType.TEMPORAL,
    minimum_sample_size=100,
    dependencies=("E-001",),
    status=QuestionStatus.BLOCKED,
    source_intent=("E5", "V10-E4"),
    decision_enabled="Can we trust the measured edge is not overfitted?",
)

E_007 = NewEngineQuestion(
    question_id="E-007",
    title="Stop Placement Effectiveness",
    research_intent=(
        "Is stop loss placement reducing expectancy by being too tight or too "
        "wide? What SL distance maximises expectancy?"
    ),
    required_universes=(Universe.EXECUTION,),
    required_populations=(Population.ALL_TRADES, Population.LOSING_TRADES),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.EXECUTION,
            populations=(Population.ALL_TRADES,),
            required_fields=("stop_loss", "entry_price", "r_multiple", "exit_reason"),
        ),
    ),
    views=(ViewType.NORMAL,),
    analysis_type=AnalysisType.SIMULATION,
    minimum_sample_size=20,
    status=QuestionStatus.READY,
    source_intent=("Lambda-R2", "EX_B", "EX4"),
    decision_enabled="Should stop distance be adjusted?",
)


# ═══════════════════════════════════════════════════════════════════════════════
# DECISION-PRIMARY QUESTIONS (D-nnn)
# Questions whose primary analytical angle is decision quality and filtering.
# ═══════════════════════════════════════════════════════════════════════════════

D_001 = NewEngineQuestion(
    question_id="D-001",
    title="Score Predictive Power",
    research_intent=(
        "Does the decision score actually predict trade outcome? Which scoring "
        "components (location, structure, behaviour, formation) have real "
        "predictive value vs noise?"
    ),
    required_universes=(Universe.DECISION,),
    required_populations=(Population.ALL_DECISIONS, Population.EXECUTE_DECISIONS),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.DECISION,
            populations=(Population.EXECUTE_DECISIONS,),
            required_fields=("score", "components", "r_multiple"),
        ),
    ),
    views=(ViewType.NORMAL, ViewType.ANOMALOUS),
    analysis_type=AnalysisType.CORRELATION,
    minimum_sample_size=20,
    status=QuestionStatus.READY,
    source_intent=("D1", "V10-D1", "Lambda-D1"),
    decision_enabled="Should we re-weight scoring components?",
)

D_002 = NewEngineQuestion(
    question_id="D-002",
    title="EV Calibration",
    research_intent=(
        "Is the expected value estimate calibrated? Does predicted win "
        "probability match actual outcomes across confidence buckets?"
    ),
    required_universes=(Universe.DECISION,),
    required_populations=(Population.EXECUTE_DECISIONS,),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.DECISION,
            populations=(Population.EXECUTE_DECISIONS,),
            required_fields=("ev", "p_success", "r_multiple"),
        ),
    ),
    views=(ViewType.NORMAL,),
    analysis_type=AnalysisType.CALIBRATION,
    minimum_sample_size=15,
    status=QuestionStatus.READY,
    source_intent=("D2", "D3", "V10-D2", "Lambda-D2"),
    decision_enabled="Should EV gate be enabled/disabled? Is predicted probability trustworthy?",
)

D_003 = NewEngineQuestion(
    question_id="D-003",
    title="Decision Threshold Effectiveness",
    research_intent=(
        "Are score thresholds set optimally? Would raising/lowering the "
        "threshold improve overall expectancy?"
    ),
    required_universes=(Universe.DECISION,),
    required_populations=(
        Population.ALL_DECISIONS, Population.EXECUTE_DECISIONS,
        Population.HIGH_SCORE_DECISIONS, Population.LOW_SCORE_DECISIONS,
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.DECISION,
            populations=(Population.EXECUTE_DECISIONS,),
            required_fields=("score", "r_multiple"),
        ),
    ),
    views=(ViewType.NORMAL,),
    analysis_type=AnalysisType.SEGMENTATION,
    minimum_sample_size=20,
    status=QuestionStatus.READY,
    source_intent=("D4", "V10-D3", "Lambda-D3"),
    decision_enabled="Should score thresholds be adjusted?",
)

D_004 = NewEngineQuestion(
    question_id="D-004",
    title="Rejection Stage Analysis",
    research_intent=(
        "Where in the decision pipeline are trades most commonly rejected? "
        "Which rejection stage removes the most potential edge vs protecting "
        "from losses?"
    ),
    required_universes=(Universe.DECISION,),
    required_populations=(
        Population.NO_TRADE_DECISIONS, Population.REJECTED_AT_OPPORTUNITY,
        Population.REJECTED_AT_STRATEGY, Population.REJECTED_AT_ENTRY,
        Population.REJECTED_AT_RISK, Population.REJECTED_AT_EXECUTION,
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.DECISION,
            populations=(Population.NO_TRADE_DECISIONS,),
            required_fields=("terminal_stage", "terminal_reason"),
        ),
    ),
    views=(ViewType.NORMAL, ViewType.EXCEPTIONAL),
    analysis_type=AnalysisType.SEGMENTATION,
    minimum_sample_size=50,
    status=QuestionStatus.READY,
    source_intent=("D5", "R1", "V10-R1"),
    decision_enabled="Are guards removing edge or protecting capital?",
)

D_005 = NewEngineQuestion(
    question_id="D-005",
    title="Opportunity Quality Predictive Value",
    research_intent=(
        "Does the 4-dimension opportunity quality score (location/structure/"
        "behaviour/formation) predict trade outcomes? Which dimensions matter?"
    ),
    required_universes=(Universe.DECISION,),
    required_populations=(Population.EXECUTE_DECISIONS,),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.DECISION,
            populations=(Population.EXECUTE_DECISIONS,),
            required_fields=(
                "opportunity_quality", "location_score", "structure_score",
                "behaviour_score", "formation_score", "r_multiple",
            ),
        ),
    ),
    views=(ViewType.NORMAL, ViewType.ANOMALOUS),
    analysis_type=AnalysisType.CORRELATION,
    minimum_sample_size=15,
    status=QuestionStatus.READY,
    source_intent=("V10-OQ1", "Lambda-OQ1"),
    decision_enabled="Should quality score scale position size? What threshold produces edge?",
)

D_006 = NewEngineQuestion(
    question_id="D-006",
    title="Opportunity Failure Characterisation",
    research_intent=(
        "What characterises opportunities that look good but fail? Are there "
        "identifiable patterns in false positives?"
    ),
    required_universes=(Universe.DECISION,),
    required_populations=(Population.EXECUTE_DECISIONS,),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.DECISION,
            populations=(Population.EXECUTE_DECISIONS,),
            required_fields=(
                "opportunity_quality", "r_multiple", "score", "components",
            ),
        ),
    ),
    views=(ViewType.NORMAL, ViewType.ANOMALOUS, ViewType.EXCEPTIONAL),
    analysis_type=AnalysisType.SEGMENTATION,
    minimum_sample_size=15,
    status=QuestionStatus.READY,
    source_intent=("V10-OQ2", "Lambda-OQ2"),
    decision_enabled="Can we filter false positives before execution?",
)

D_007 = NewEngineQuestion(
    question_id="D-007",
    title="Risk Gate Value",
    research_intent=(
        "Does the risk management layer (R:R minimum, daily loss limit, "
        "exposure guards) improve overall survival and expectancy, or does "
        "it filter out profitable opportunities?"
    ),
    required_universes=(Universe.DECISION,),
    required_populations=(
        Population.ALL_DECISIONS, Population.REJECTED_AT_RISK,
        Population.EXECUTE_DECISIONS,
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.DECISION,
            populations=(Population.REJECTED_AT_RISK,),
            required_fields=("terminal_reason", "ev", "score"),
        ),
    ),
    views=(ViewType.NORMAL, ViewType.EXCEPTIONAL),
    analysis_type=AnalysisType.COUNTERFACTUAL,
    minimum_sample_size=20,
    status=QuestionStatus.READY,
    source_intent=("R1", "R2", "V10-R1"),
    decision_enabled="Should risk gates be tightened or relaxed?",
)

# ═══════════════════════════════════════════════════════════════════════════════
# MARKET-PRIMARY QUESTIONS (M-nnn)
# Questions whose primary analytical angle is market state and context.
# ═══════════════════════════════════════════════════════════════════════════════

M_001 = NewEngineQuestion(
    question_id="M-001",
    title="Regime Predicts Outcomes",
    research_intent=(
        "Does H4 regime classification (TRENDING/RANGING/TRANSITIONAL) "
        "predict trade R-multiple? Should regime gate trading?"
    ),
    required_universes=(Universe.MARKET,),
    required_populations=(
        Population.ALL_MARKET_STATES, Population.TRENDING_REGIME,
        Population.RANGING_REGIME, Population.TRANSITIONAL_REGIME,
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.MARKET,
            populations=(Population.ALL_MARKET_STATES,),
            required_fields=("regime", "regime_confidence"),
        ),
    ),
    views=(ViewType.NORMAL, ViewType.ANOMALOUS),
    analysis_type=AnalysisType.SEGMENTATION,
    minimum_sample_size=10,
    status=QuestionStatus.READY,
    source_intent=("M1", "V10-M1", "Lambda-M1"),
    decision_enabled="Should we filter trades by regime?",
)

M_002 = NewEngineQuestion(
    question_id="M-002",
    title="HTF Alignment Value",
    research_intent=(
        "Does higher-timeframe alignment (macro bias strength, structure "
        "alignment) predict trade success better than individual timeframe data?"
    ),
    required_universes=(Universe.MARKET,),
    required_populations=(Population.ALL_MARKET_STATES,),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.MARKET,
            populations=(Population.ALL_MARKET_STATES,),
            required_fields=(
                "htf_alignment_macro_bias", "htf_alignment_strength",
                "structure_alignment",
            ),
        ),
    ),
    views=(ViewType.NORMAL,),
    analysis_type=AnalysisType.CORRELATION,
    minimum_sample_size=15,
    status=QuestionStatus.READY,
    source_intent=("V10-M2", "M2"),
    decision_enabled="Should HTF alignment gate trading? What minimum produces edge?",
)

M_003 = NewEngineQuestion(
    question_id="M-003",
    title="Volatility State Impact",
    research_intent=(
        "Does volatility state (HIGH/NEUTRAL/LOW) affect expectancy? Does "
        "combining regime + volatility improve prediction beyond regime alone?"
    ),
    required_universes=(Universe.MARKET,),
    required_populations=(
        Population.ALL_MARKET_STATES, Population.HIGH_VOLATILITY,
        Population.LOW_VOLATILITY,
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.MARKET,
            populations=(Population.ALL_MARKET_STATES,),
            required_fields=("regime", "volatility_state", "h4_atr"),
        ),
    ),
    views=(ViewType.NORMAL, ViewType.ANOMALOUS),
    analysis_type=AnalysisType.SEGMENTATION,
    minimum_sample_size=10,
    status=QuestionStatus.READY,
    source_intent=("V10-M3", "M3", "M4"),
    decision_enabled="Should we add volatility to opportunity quality assessment?",
)

M_004 = NewEngineQuestion(
    question_id="M-004",
    title="Market Structure Clarity",
    research_intent=(
        "Does H1 structural clarity (BOS confirmed, swing structure, ChoCH) "
        "predict better trade outcomes? Is there a clarity threshold below "
        "which trading becomes negative EV?"
    ),
    required_universes=(Universe.MARKET,),
    required_populations=(Population.ALL_MARKET_STATES,),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.MARKET,
            populations=(Population.ALL_MARKET_STATES,),
            required_fields=(
                "h1_structural_clarity", "h1_bos_confirmed",
                "h1_dominant_trend",
            ),
        ),
    ),
    views=(ViewType.NORMAL,),
    analysis_type=AnalysisType.CORRELATION,
    minimum_sample_size=15,
    status=QuestionStatus.READY,
    source_intent=("M3", "M11"),
    decision_enabled="Should structural clarity gate opportunity detection?",
)

M_005 = NewEngineQuestion(
    question_id="M-005",
    title="Location Quality Impact",
    research_intent=(
        "Does price location (institutional zone, premium/discount, range "
        "position) predict trade outcomes? Is location more predictive than "
        "pattern identity?"
    ),
    required_universes=(Universe.MARKET,),
    required_populations=(Population.ALL_MARKET_STATES,),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.MARKET,
            populations=(Population.ALL_MARKET_STATES,),
            required_fields=(
                "location_type", "inside_institutional_zone",
                "zone_quality", "premium_discount",
            ),
        ),
    ),
    views=(ViewType.NORMAL, ViewType.EXCEPTIONAL),
    analysis_type=AnalysisType.SEGMENTATION,
    minimum_sample_size=15,
    status=QuestionStatus.READY,
    source_intent=("M9", "M11"),
    decision_enabled="Should location weight be increased in scoring?",
)

M_006 = NewEngineQuestion(
    question_id="M-006",
    title="Session Edge Variation",
    research_intent=(
        "Does expectancy vary significantly across trading sessions (London, "
        "New York, Asia)? Are there sessions that should be avoided?"
    ),
    required_universes=(Universe.MARKET,),
    required_populations=(
        Population.ALL_MARKET_STATES, Population.SESSION_LONDON,
        Population.SESSION_NY, Population.SESSION_ASIA,
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.MARKET,
            populations=(Population.ALL_MARKET_STATES,),
            required_fields=("session",),
        ),
    ),
    views=(ViewType.NORMAL,),
    analysis_type=AnalysisType.SEGMENTATION,
    minimum_sample_size=10,
    status=QuestionStatus.READY,
    source_intent=("X3", "Lambda-C1"),
    decision_enabled="Should we restrict trading to specific sessions?",
)

# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY-PRIMARY QUESTIONS (S-nnn)
# Questions whose primary analytical angle is strategy selection and behaviour.
# ═══════════════════════════════════════════════════════════════════════════════

S_001 = NewEngineQuestion(
    question_id="S-001",
    title="Strategy Family Expectancy",
    research_intent=(
        "Which strategy families (TREND_CONTINUATION, MEAN_REVERSION, "
        "BREAKOUT, MOMENTUM, etc.) produce positive expectancy independently?"
    ),
    required_universes=(Universe.STRATEGY,),
    required_populations=(
        Population.ALL_STRATEGIES, Population.TREND_CONTINUATION,
        Population.MEAN_REVERSION, Population.BREAKOUT, Population.MOMENTUM,
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.STRATEGY,
            populations=(Population.ALL_STRATEGIES,),
            required_fields=("family", "confidence", "r_multiple"),
        ),
    ),
    views=(ViewType.NORMAL, ViewType.ANOMALOUS),
    analysis_type=AnalysisType.SEGMENTATION,
    minimum_sample_size=10,
    status=QuestionStatus.READY,
    source_intent=("E3", "S1", "V10-E3", "V10-SC1", "Lambda-S1"),
    decision_enabled="Should we disable unprofitable strategy families?",
)

S_002 = NewEngineQuestion(
    question_id="S-002",
    title="Pattern Expectancy",
    research_intent=(
        "Which candlestick patterns contain positive expectancy? Are any "
        "patterns consistently negative and should be disabled?"
    ),
    required_universes=(Universe.STRATEGY,),
    required_populations=(Population.ALL_STRATEGIES,),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.STRATEGY,
            populations=(Population.ALL_STRATEGIES,),
            required_fields=("pattern", "r_multiple"),
        ),
    ),
    views=(ViewType.NORMAL, ViewType.ANOMALOUS),
    analysis_type=AnalysisType.SEGMENTATION,
    minimum_sample_size=10,
    status=QuestionStatus.READY,
    source_intent=("E2", "V10-E2", "Lambda-E2"),
    decision_enabled="Should we disable any patterns? Re-weight them?",
)

S_003 = NewEngineQuestion(
    question_id="S-003",
    title="Strategy Selection Accuracy",
    research_intent=(
        "When the strategy engine selects a strategy, does that selection "
        "predict better outcomes than random? Is confidence calibrated?"
    ),
    required_universes=(Universe.STRATEGY,),
    required_populations=(
        Population.STRATEGY_SELECTED, Population.STRATEGY_ELIGIBLE,
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.STRATEGY,
            populations=(Population.STRATEGY_SELECTED,),
            required_fields=("family", "confidence", "conditions_met"),
        ),
    ),
    views=(ViewType.NORMAL,),
    analysis_type=AnalysisType.CALIBRATION,
    minimum_sample_size=15,
    status=QuestionStatus.READY,
    source_intent=("S1", "S5", "V10-SC1"),
    decision_enabled="Should strategy confidence gate trading?",
)

S_004 = NewEngineQuestion(
    question_id="S-004",
    title="Strategy Rejection Patterns",
    research_intent=(
        "When opportunities are detected but no strategy matches, what "
        "characterises these gaps? Are there profitable patterns the strategy "
        "engine currently misses?"
    ),
    required_universes=(Universe.STRATEGY,),
    required_populations=(
        Population.STRATEGY_REJECTED, Population.ALL_STRATEGIES,
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.STRATEGY,
            populations=(Population.STRATEGY_REJECTED,),
            required_fields=("family", "conditions_met", "reasoning"),
        ),
    ),
    views=(ViewType.NORMAL, ViewType.EXCEPTIONAL),
    analysis_type=AnalysisType.DISTRIBUTION,
    minimum_sample_size=20,
    status=QuestionStatus.READY,
    source_intent=("S4", "V10-SC2"),
    decision_enabled="Should new strategy families be added?",
)

# ═══════════════════════════════════════════════════════════════════════════════
# CROSS-ANGLE: EXECUTION + DECISION (ED-nnn)
# ═══════════════════════════════════════════════════════════════════════════════

ED_001 = NewEngineQuestion(
    question_id="ED-001",
    title="Decision-to-Execution Edge Leakage",
    research_intent=(
        "How much expected edge (from decision EV and score) is lost between "
        "the decision point and realised execution? Where does leakage occur?"
    ),
    required_universes=(Universe.EXECUTION, Universe.DECISION),
    required_populations=(Population.ALL_TRADES, Population.EXECUTE_DECISIONS),
    required_joins=(
        JoinRequirement(
            from_universe=Universe.DECISION,
            to_universe=Universe.EXECUTION,
            join_type=JoinType.ENTITY_ID,
            join_field="entity_id",
        ),
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.EXECUTION,
            populations=(Population.ALL_TRADES,),
            required_fields=("r_multiple", "net_realised_pnl"),
        ),
        AngleRequirement(
            universe=Universe.DECISION,
            populations=(Population.EXECUTE_DECISIONS,),
            required_fields=("ev", "score", "p_success"),
        ),
    ),
    views=(ViewType.NORMAL, ViewType.ANOMALOUS),
    analysis_type=AnalysisType.COMPARISON,
    minimum_sample_size=20,
    status=QuestionStatus.READY,
    source_intent=("X4", "X5"),
    decision_enabled="Where is edge being lost? Can execution be improved?",
)

ED_002 = NewEngineQuestion(
    question_id="ED-002",
    title="Missed Opportunity Cost",
    research_intent=(
        "Which rejected decisions (NO_TRADE) would have succeeded if allowed "
        "through? What is the cost of over-filtering?"
    ),
    required_universes=(Universe.EXECUTION, Universe.DECISION),
    required_populations=(
        Population.NO_TRADE_DECISIONS, Population.ALL_TRADES,
    ),
    required_joins=(
        JoinRequirement(
            from_universe=Universe.DECISION,
            to_universe=Universe.EXECUTION,
            join_type=JoinType.CORRELATION_ID,
            join_field="correlation_id",
        ),
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.DECISION,
            populations=(Population.NO_TRADE_DECISIONS,),
            required_fields=("terminal_stage", "terminal_reason", "score", "ev"),
        ),
        AngleRequirement(
            universe=Universe.EXECUTION,
            populations=(Population.ALL_TRADES,),
            required_fields=("r_multiple",),
        ),
    ),
    views=(ViewType.NORMAL, ViewType.EXCEPTIONAL),
    analysis_type=AnalysisType.COUNTERFACTUAL,
    minimum_sample_size=20,
    status=QuestionStatus.PARTIAL,
    source_intent=("D5", "Q3"),
    decision_enabled="Should rejection thresholds be relaxed?",
)

# ═══════════════════════════════════════════════════════════════════════════════
# CROSS-ANGLE: EXECUTION + MARKET (EM-nnn)
# ═══════════════════════════════════════════════════════════════════════════════

EM_001 = NewEngineQuestion(
    question_id="EM-001",
    title="Regime-Conditioned Expectancy",
    research_intent=(
        "Does trade expectancy differ significantly across market regimes "
        "when measured on realised execution outcomes?"
    ),
    required_universes=(Universe.EXECUTION, Universe.MARKET),
    required_populations=(
        Population.ALL_TRADES, Population.TRENDING_REGIME,
        Population.RANGING_REGIME,
    ),
    required_joins=(
        JoinRequirement(
            from_universe=Universe.EXECUTION,
            to_universe=Universe.MARKET,
            join_type=JoinType.ENTITY_ID,
            join_field="entity_id",
        ),
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.EXECUTION,
            populations=(Population.ALL_TRADES,),
            required_fields=("r_multiple",),
        ),
        AngleRequirement(
            universe=Universe.MARKET,
            populations=(Population.ALL_MARKET_STATES,),
            required_fields=("regime",),
        ),
    ),
    views=(ViewType.NORMAL, ViewType.ANOMALOUS),
    analysis_type=AnalysisType.SEGMENTATION,
    minimum_sample_size=10,
    status=QuestionStatus.READY,
    source_intent=("M1", "V10-M1"),
    decision_enabled="Should regime gate execution?",
)

# ═══════════════════════════════════════════════════════════════════════════════
# CROSS-ANGLE: DECISION + MARKET (DM-nnn)
# ═══════════════════════════════════════════════════════════════════════════════

DM_001 = NewEngineQuestion(
    question_id="DM-001",
    title="Decision Quality Under Regime",
    research_intent=(
        "Does the decision engine perform differently across market regimes? "
        "Is scoring accuracy regime-dependent?"
    ),
    required_universes=(Universe.DECISION, Universe.MARKET),
    required_populations=(
        Population.EXECUTE_DECISIONS, Population.ALL_MARKET_STATES,
        Population.TRENDING_REGIME, Population.RANGING_REGIME,
    ),
    required_joins=(
        JoinRequirement(
            from_universe=Universe.DECISION,
            to_universe=Universe.MARKET,
            join_type=JoinType.ENTITY_ID,
            join_field="entity_id",
        ),
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.DECISION,
            populations=(Population.EXECUTE_DECISIONS,),
            required_fields=("score", "r_multiple"),
        ),
        AngleRequirement(
            universe=Universe.MARKET,
            populations=(Population.ALL_MARKET_STATES,),
            required_fields=("regime",),
        ),
    ),
    views=(ViewType.NORMAL, ViewType.ANOMALOUS),
    analysis_type=AnalysisType.SEGMENTATION,
    minimum_sample_size=10,
    status=QuestionStatus.READY,
    source_intent=("D4", "M2"),
    decision_enabled="Should scoring be regime-adapted?",
)

DM_002 = NewEngineQuestion(
    question_id="DM-002",
    title="Opportunity Detection vs Market State",
    research_intent=(
        "Does opportunity quality (the 4-dimension score) remain predictive "
        "across all market states, or does it degrade in specific conditions?"
    ),
    required_universes=(Universe.DECISION, Universe.MARKET),
    required_populations=(
        Population.ALL_DECISIONS, Population.ALL_MARKET_STATES,
    ),
    required_joins=(
        JoinRequirement(
            from_universe=Universe.DECISION,
            to_universe=Universe.MARKET,
            join_type=JoinType.ENTITY_ID,
            join_field="entity_id",
        ),
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.DECISION,
            populations=(Population.ALL_DECISIONS,),
            required_fields=("opportunity_quality", "location_score"),
        ),
        AngleRequirement(
            universe=Universe.MARKET,
            populations=(Population.ALL_MARKET_STATES,),
            required_fields=("regime", "volatility_state"),
        ),
    ),
    views=(ViewType.NORMAL, ViewType.EXCEPTIONAL),
    analysis_type=AnalysisType.COMPARISON,
    minimum_sample_size=15,
    status=QuestionStatus.READY,
    source_intent=("V10-OQ1", "M11"),
    decision_enabled="Should opportunity thresholds be regime-dependent?",
)

# ═══════════════════════════════════════════════════════════════════════════════
# CROSS-ANGLE: DECISION + STRATEGY (DS-nnn)
# ═══════════════════════════════════════════════════════════════════════════════

DS_001 = NewEngineQuestion(
    question_id="DS-001",
    title="Strategy Confidence Calibration",
    research_intent=(
        "Is strategy confidence calibrated to outcomes? Does high strategy "
        "confidence actually predict better R-multiples?"
    ),
    required_universes=(Universe.DECISION, Universe.STRATEGY),
    required_populations=(
        Population.EXECUTE_DECISIONS, Population.STRATEGY_SELECTED,
    ),
    required_joins=(
        JoinRequirement(
            from_universe=Universe.DECISION,
            to_universe=Universe.STRATEGY,
            join_type=JoinType.ENTITY_ID,
            join_field="entity_id",
        ),
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.DECISION,
            populations=(Population.EXECUTE_DECISIONS,),
            required_fields=("score", "r_multiple"),
        ),
        AngleRequirement(
            universe=Universe.STRATEGY,
            populations=(Population.STRATEGY_SELECTED,),
            required_fields=("confidence", "family"),
        ),
    ),
    views=(ViewType.NORMAL,),
    analysis_type=AnalysisType.CALIBRATION,
    minimum_sample_size=15,
    status=QuestionStatus.READY,
    source_intent=("S1", "S5", "V10-SC1"),
    decision_enabled="Should strategy confidence gate execution?",
)

# ═══════════════════════════════════════════════════════════════════════════════
# CROSS-ANGLE: MARKET + STRATEGY (MS-nnn)
# ═══════════════════════════════════════════════════════════════════════════════

MS_001 = NewEngineQuestion(
    question_id="MS-001",
    title="Strategy × Regime Interaction",
    research_intent=(
        "Do strategy families perform differently across market regimes? "
        "Should strategy selection be regime-gated?"
    ),
    required_universes=(Universe.MARKET, Universe.STRATEGY),
    required_populations=(
        Population.ALL_MARKET_STATES, Population.ALL_STRATEGIES,
        Population.TRENDING_REGIME, Population.RANGING_REGIME,
    ),
    required_joins=(
        JoinRequirement(
            from_universe=Universe.STRATEGY,
            to_universe=Universe.MARKET,
            join_type=JoinType.ENTITY_ID,
            join_field="entity_id",
        ),
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.MARKET,
            populations=(Population.ALL_MARKET_STATES,),
            required_fields=("regime",),
        ),
        AngleRequirement(
            universe=Universe.STRATEGY,
            populations=(Population.ALL_STRATEGIES,),
            required_fields=("family", "r_multiple"),
        ),
    ),
    views=(ViewType.NORMAL, ViewType.ANOMALOUS),
    analysis_type=AnalysisType.SEGMENTATION,
    minimum_sample_size=10,
    status=QuestionStatus.READY,
    source_intent=("M2", "S3", "V10-SC2"),
    decision_enabled="Should strategy selection be regime-gated?",
)

MS_002 = NewEngineQuestion(
    question_id="MS-002",
    title="Pattern × Market Context Interaction",
    research_intent=(
        "Are specific patterns only profitable in certain market conditions? "
        "Is context more predictive than pattern identity alone?"
    ),
    required_universes=(Universe.MARKET, Universe.STRATEGY),
    required_populations=(
        Population.ALL_MARKET_STATES, Population.ALL_STRATEGIES,
    ),
    required_joins=(
        JoinRequirement(
            from_universe=Universe.STRATEGY,
            to_universe=Universe.MARKET,
            join_type=JoinType.ENTITY_ID,
            join_field="entity_id",
        ),
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.MARKET,
            populations=(Population.ALL_MARKET_STATES,),
            required_fields=("regime", "h1_structural_clarity"),
        ),
        AngleRequirement(
            universe=Universe.STRATEGY,
            populations=(Population.ALL_STRATEGIES,),
            required_fields=("pattern", "r_multiple"),
        ),
    ),
    views=(ViewType.NORMAL,),
    analysis_type=AnalysisType.SEGMENTATION,
    minimum_sample_size=10,
    status=QuestionStatus.READY,
    source_intent=("M9", "M11", "E2"),
    decision_enabled="Should patterns be context-gated?",
)

# ═══════════════════════════════════════════════════════════════════════════════
# CROSS-ANGLE: EXECUTION + DECISION + MARKET (EDM-nnn)
# ═══════════════════════════════════════════════════════════════════════════════

EDM_001 = NewEngineQuestion(
    question_id="EDM-001",
    title="Complete Trade Lifecycle Analysis",
    research_intent=(
        "For executed trades, what is the full pathway from market state → "
        "decision → execution outcome? Where does the pipeline add or lose value?"
    ),
    required_universes=(Universe.EXECUTION, Universe.DECISION, Universe.MARKET),
    required_populations=(
        Population.ALL_TRADES, Population.EXECUTE_DECISIONS,
        Population.ALL_MARKET_STATES,
    ),
    required_joins=(
        JoinRequirement(
            from_universe=Universe.DECISION,
            to_universe=Universe.EXECUTION,
            join_type=JoinType.ENTITY_ID,
            join_field="entity_id",
        ),
        JoinRequirement(
            from_universe=Universe.DECISION,
            to_universe=Universe.MARKET,
            join_type=JoinType.ENTITY_ID,
            join_field="entity_id",
        ),
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.EXECUTION,
            populations=(Population.ALL_TRADES,),
            required_fields=("r_multiple",),
        ),
        AngleRequirement(
            universe=Universe.DECISION,
            populations=(Population.EXECUTE_DECISIONS,),
            required_fields=("score", "ev"),
        ),
        AngleRequirement(
            universe=Universe.MARKET,
            populations=(Population.ALL_MARKET_STATES,),
            required_fields=("regime", "volatility_state"),
        ),
    ),
    views=(ViewType.NORMAL, ViewType.ANOMALOUS, ViewType.EXCEPTIONAL),
    analysis_type=AnalysisType.COMPARISON,
    minimum_sample_size=20,
    status=QuestionStatus.READY,
    source_intent=("E1", "D1", "M1"),
    decision_enabled="Where in the pipeline should improvements focus?",
)

# ═══════════════════════════════════════════════════════════════════════════════
# CROSS-ANGLE: DECISION + MARKET + STRATEGY (DMS-nnn)
# ═══════════════════════════════════════════════════════════════════════════════

DMS_001 = NewEngineQuestion(
    question_id="DMS-001",
    title="Decision Quality Across Strategy × Market",
    research_intent=(
        "Does decision quality (score accuracy, EV calibration) vary when "
        "segmented by both strategy family AND market regime simultaneously?"
    ),
    required_universes=(Universe.DECISION, Universe.MARKET, Universe.STRATEGY),
    required_populations=(
        Population.EXECUTE_DECISIONS, Population.ALL_MARKET_STATES,
        Population.ALL_STRATEGIES,
    ),
    required_joins=(
        JoinRequirement(
            from_universe=Universe.DECISION,
            to_universe=Universe.MARKET,
            join_type=JoinType.ENTITY_ID,
            join_field="entity_id",
        ),
        JoinRequirement(
            from_universe=Universe.DECISION,
            to_universe=Universe.STRATEGY,
            join_type=JoinType.ENTITY_ID,
            join_field="entity_id",
        ),
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.DECISION,
            populations=(Population.EXECUTE_DECISIONS,),
            required_fields=("score", "r_multiple"),
        ),
        AngleRequirement(
            universe=Universe.MARKET,
            populations=(Population.ALL_MARKET_STATES,),
            required_fields=("regime",),
        ),
        AngleRequirement(
            universe=Universe.STRATEGY,
            populations=(Population.ALL_STRATEGIES,),
            required_fields=("family",),
        ),
    ),
    views=(ViewType.NORMAL,),
    analysis_type=AnalysisType.SEGMENTATION,
    minimum_sample_size=10,
    dependencies=("DM-001", "DS-001", "MS-001"),
    status=QuestionStatus.READY,
    source_intent=("M4", "S3", "V10-SC2"),
    decision_enabled="Should scoring be adapted per strategy×regime combination?",
)

# ═══════════════════════════════════════════════════════════════════════════════
# CROSS-ANGLE: ALL FOUR (EDMS-nnn)
# ═══════════════════════════════════════════════════════════════════════════════

EDMS_001 = NewEngineQuestion(
    question_id="EDMS-001",
    title="Full System Attribution",
    research_intent=(
        "Across all four angles — what is the relative contribution of "
        "market conditions, strategy selection, decision quality, and "
        "execution quality to final trade outcomes?"
    ),
    required_universes=(
        Universe.EXECUTION, Universe.DECISION,
        Universe.MARKET, Universe.STRATEGY,
    ),
    required_populations=(
        Population.ALL_TRADES, Population.EXECUTE_DECISIONS,
        Population.ALL_MARKET_STATES, Population.ALL_STRATEGIES,
    ),
    required_joins=(
        JoinRequirement(
            from_universe=Universe.DECISION,
            to_universe=Universe.EXECUTION,
            join_type=JoinType.ENTITY_ID,
            join_field="entity_id",
        ),
        JoinRequirement(
            from_universe=Universe.DECISION,
            to_universe=Universe.MARKET,
            join_type=JoinType.ENTITY_ID,
            join_field="entity_id",
        ),
        JoinRequirement(
            from_universe=Universe.DECISION,
            to_universe=Universe.STRATEGY,
            join_type=JoinType.ENTITY_ID,
            join_field="entity_id",
        ),
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.EXECUTION,
            populations=(Population.ALL_TRADES,),
            required_fields=("r_multiple",),
        ),
        AngleRequirement(
            universe=Universe.DECISION,
            populations=(Population.EXECUTE_DECISIONS,),
            required_fields=("score", "ev", "opportunity_quality"),
        ),
        AngleRequirement(
            universe=Universe.MARKET,
            populations=(Population.ALL_MARKET_STATES,),
            required_fields=("regime", "volatility_state", "h1_structural_clarity"),
        ),
        AngleRequirement(
            universe=Universe.STRATEGY,
            populations=(Population.ALL_STRATEGIES,),
            required_fields=("family", "pattern", "confidence"),
        ),
    ),
    views=(ViewType.NORMAL, ViewType.ANOMALOUS, ViewType.EXCEPTIONAL),
    analysis_type=AnalysisType.CORRELATION,
    minimum_sample_size=30,
    dependencies=("E-001", "D-001", "M-001", "S-001"),
    status=QuestionStatus.READY,
    source_intent=("L3", "D1", "M1", "S1"),
    decision_enabled="Where should system improvement efforts focus?",
)

# ═══════════════════════════════════════════════════════════════════════════════
# TEMPORAL / DEGRADATION QUESTIONS
# These span angles but focus on time-series behaviour.
# ═══════════════════════════════════════════════════════════════════════════════

E_008 = NewEngineQuestion(
    question_id="E-008",
    title="Pattern Degradation Over Time",
    research_intent=(
        "Are any patterns losing edge over the observation period? Is there "
        "evidence of market adaptation to the system's patterns?"
    ),
    required_universes=(Universe.EXECUTION,),
    required_populations=(Population.ALL_TRADES,),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.EXECUTION,
            populations=(Population.ALL_TRADES,),
            required_fields=("r_multiple", "entry_time", "symbol"),
        ),
    ),
    views=(ViewType.NORMAL,),
    analysis_type=AnalysisType.DEGRADATION,
    minimum_sample_size=50,
    status=QuestionStatus.PARTIAL,
    source_intent=("L1", "L4", "L5", "V10-L1"),
    decision_enabled="Should any pattern be disabled or down-weighted?",
)

EM_002 = NewEngineQuestion(
    question_id="EM-002",
    title="Market Drift Detection",
    research_intent=(
        "Is market behaviour changing over time in ways that invalidate "
        "system assumptions? Are regime distributions shifting?"
    ),
    required_universes=(Universe.EXECUTION, Universe.MARKET),
    required_populations=(
        Population.ALL_TRADES, Population.ALL_MARKET_STATES,
    ),
    required_joins=(
        JoinRequirement(
            from_universe=Universe.EXECUTION,
            to_universe=Universe.MARKET,
            join_type=JoinType.ENTITY_ID,
            join_field="entity_id",
        ),
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.EXECUTION,
            populations=(Population.ALL_TRADES,),
            required_fields=("r_multiple", "entry_time"),
        ),
        AngleRequirement(
            universe=Universe.MARKET,
            populations=(Population.ALL_MARKET_STATES,),
            required_fields=("regime", "volatility_state"),
        ),
    ),
    views=(ViewType.NORMAL, ViewType.ANOMALOUS),
    analysis_type=AnalysisType.DEGRADATION,
    minimum_sample_size=30,
    status=QuestionStatus.PARTIAL,
    source_intent=("L4", "V10-L1"),
    decision_enabled="Are system assumptions still valid? Need recalibration?",
)

# ═══════════════════════════════════════════════════════════════════════════════
# EXIT / POSITION MANAGEMENT QUESTIONS
# Recovered from archived exit research — now viable with execution data.
# ═══════════════════════════════════════════════════════════════════════════════

E_009 = NewEngineQuestion(
    question_id="E-009",
    title="Trade Duration vs Outcome",
    research_intent=(
        "Does trade duration affect expectancy? Are trades that last longer "
        "systematically better or worse than quick trades?"
    ),
    required_universes=(Universe.EXECUTION,),
    required_populations=(Population.ALL_TRADES,),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.EXECUTION,
            populations=(Population.ALL_TRADES,),
            required_fields=("r_multiple", "duration_seconds", "exit_reason"),
        ),
    ),
    views=(ViewType.NORMAL,),
    analysis_type=AnalysisType.CORRELATION,
    minimum_sample_size=20,
    status=QuestionStatus.READY,
    source_intent=("EX_A", "EX5", "V10-EX3"),
    decision_enabled="Should time-based exits be adjusted?",
)

E_010 = NewEngineQuestion(
    question_id="E-010",
    title="Risk:Reward Ratio Effectiveness",
    research_intent=(
        "What R:R ratios are actually achieved vs intended? Does target R:R "
        "at entry predict outcome quality?"
    ),
    required_universes=(Universe.EXECUTION,),
    required_populations=(Population.ALL_TRADES,),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.EXECUTION,
            populations=(Population.ALL_TRADES,),
            required_fields=(
                "stop_loss", "take_profit", "entry_price",
                "exit_price", "r_multiple",
            ),
        ),
    ),
    views=(ViewType.NORMAL,),
    analysis_type=AnalysisType.COMPARISON,
    minimum_sample_size=20,
    status=QuestionStatus.READY,
    source_intent=("EX3", "EX_C", "R5"),
    decision_enabled="Should minimum R:R requirements change?",
)

# ═══════════════════════════════════════════════════════════════════════════════
# ADDITIONAL CROSS-ANGLE QUESTIONS
# ═══════════════════════════════════════════════════════════════════════════════

ES_001 = NewEngineQuestion(
    question_id="ES-001",
    title="Execution Quality by Strategy",
    research_intent=(
        "Do different strategy families produce systematically different "
        "execution quality? Are some strategies more execution-sensitive?"
    ),
    required_universes=(Universe.EXECUTION, Universe.STRATEGY),
    required_populations=(Population.ALL_TRADES, Population.ALL_STRATEGIES),
    required_joins=(
        JoinRequirement(
            from_universe=Universe.EXECUTION,
            to_universe=Universe.STRATEGY,
            join_type=JoinType.ENTITY_ID,
            join_field="entity_id",
        ),
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.EXECUTION,
            populations=(Population.ALL_TRADES,),
            required_fields=("r_multiple", "duration_seconds", "exit_reason"),
        ),
        AngleRequirement(
            universe=Universe.STRATEGY,
            populations=(Population.ALL_STRATEGIES,),
            required_fields=("family", "pattern"),
        ),
    ),
    views=(ViewType.NORMAL,),
    analysis_type=AnalysisType.SEGMENTATION,
    minimum_sample_size=10,
    status=QuestionStatus.READY,
    source_intent=("E3", "E4", "S1", "EX6"),
    decision_enabled="Should execution parameters differ by strategy?",
)

DM_003 = NewEngineQuestion(
    question_id="DM-003",
    title="Rejection Rate by Market State",
    research_intent=(
        "Does the NO_TRADE rate vary by market state? Are there market "
        "conditions where the system rejects everything (possibly missing edge)?"
    ),
    required_universes=(Universe.DECISION, Universe.MARKET),
    required_populations=(
        Population.ALL_DECISIONS, Population.NO_TRADE_DECISIONS,
        Population.ALL_MARKET_STATES,
    ),
    required_joins=(
        JoinRequirement(
            from_universe=Universe.DECISION,
            to_universe=Universe.MARKET,
            join_type=JoinType.ENTITY_ID,
            join_field="entity_id",
        ),
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.DECISION,
            populations=(Population.ALL_DECISIONS,),
            required_fields=("action", "terminal_stage"),
        ),
        AngleRequirement(
            universe=Universe.MARKET,
            populations=(Population.ALL_MARKET_STATES,),
            required_fields=("regime", "volatility_state"),
        ),
    ),
    views=(ViewType.NORMAL, ViewType.EXCEPTIONAL),
    analysis_type=AnalysisType.SEGMENTATION,
    minimum_sample_size=30,
    status=QuestionStatus.READY,
    source_intent=("D5", "M5", "V10-R1"),
    decision_enabled="Are there regimes where the system is too conservative?",
)

DS_002 = NewEngineQuestion(
    question_id="DS-002",
    title="Strategy Conditions vs Outcome",
    research_intent=(
        "Do the number and type of strategy conditions met at entry predict "
        "trade outcome? Is the conditions framework adding value?"
    ),
    required_universes=(Universe.DECISION, Universe.STRATEGY),
    required_populations=(
        Population.EXECUTE_DECISIONS, Population.STRATEGY_SELECTED,
    ),
    required_joins=(
        JoinRequirement(
            from_universe=Universe.DECISION,
            to_universe=Universe.STRATEGY,
            join_type=JoinType.ENTITY_ID,
            join_field="entity_id",
        ),
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.DECISION,
            populations=(Population.EXECUTE_DECISIONS,),
            required_fields=("r_multiple",),
        ),
        AngleRequirement(
            universe=Universe.STRATEGY,
            populations=(Population.STRATEGY_SELECTED,),
            required_fields=("conditions_met", "family", "confidence"),
        ),
    ),
    views=(ViewType.NORMAL,),
    analysis_type=AnalysisType.CORRELATION,
    minimum_sample_size=15,
    status=QuestionStatus.READY,
    source_intent=("S4", "V10-SC1"),
    decision_enabled="Should minimum conditions-met be raised?",
)

MS_003 = NewEngineQuestion(
    question_id="MS-003",
    title="Strategy Availability by Market State",
    research_intent=(
        "How does market state affect which strategies become eligible? "
        "Are there market conditions where no strategy qualifies (coverage gap)?"
    ),
    required_universes=(Universe.MARKET, Universe.STRATEGY),
    required_populations=(
        Population.ALL_MARKET_STATES, Population.STRATEGY_ELIGIBLE,
        Population.STRATEGY_REJECTED,
    ),
    required_joins=(
        JoinRequirement(
            from_universe=Universe.STRATEGY,
            to_universe=Universe.MARKET,
            join_type=JoinType.ENTITY_ID,
            join_field="entity_id",
        ),
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.MARKET,
            populations=(Population.ALL_MARKET_STATES,),
            required_fields=("regime", "h4_phase"),
        ),
        AngleRequirement(
            universe=Universe.STRATEGY,
            populations=(Population.STRATEGY_ELIGIBLE,),
            required_fields=("family", "conditions_met"),
        ),
    ),
    views=(ViewType.NORMAL, ViewType.EXCEPTIONAL),
    analysis_type=AnalysisType.DISTRIBUTION,
    minimum_sample_size=30,
    status=QuestionStatus.READY,
    source_intent=("M10", "S4", "V10-SC2"),
    decision_enabled="Are there coverage gaps that need new strategies?",
)

# ═══════════════════════════════════════════════════════════════════════════════
# ADDITIONAL QUESTIONS: POSITION SIZING & PROMOTION
# Recovered intents that become actionable with the new universe architecture.
# ═══════════════════════════════════════════════════════════════════════════════

ED_003 = NewEngineQuestion(
    question_id="ED-003",
    title="Position Sizing Effectiveness",
    research_intent=(
        "Does quality-scaled position sizing (scaling by opportunity quality "
        "or strategy confidence) improve risk-adjusted returns vs fixed sizing?"
    ),
    required_universes=(Universe.EXECUTION, Universe.DECISION),
    required_populations=(Population.ALL_TRADES, Population.EXECUTE_DECISIONS),
    required_joins=(
        JoinRequirement(
            from_universe=Universe.DECISION,
            to_universe=Universe.EXECUTION,
            join_type=JoinType.ENTITY_ID,
            join_field="entity_id",
        ),
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.EXECUTION,
            populations=(Population.ALL_TRADES,),
            required_fields=("r_multiple", "volume"),
        ),
        AngleRequirement(
            universe=Universe.DECISION,
            populations=(Population.EXECUTE_DECISIONS,),
            required_fields=("opportunity_quality", "score"),
        ),
    ),
    views=(ViewType.NORMAL,),
    analysis_type=AnalysisType.SIMULATION,
    minimum_sample_size=30,
    dependencies=("E-001", "D-005"),
    status=QuestionStatus.PARTIAL,
    source_intent=("R5", "V10-R3"),
    decision_enabled="Should we implement quality-scaled sizing?",
)

EDMS_002 = NewEngineQuestion(
    question_id="EDMS-002",
    title="Promotion Impact Analysis",
    research_intent=(
        "If a specific research finding is promoted to production (e.g. "
        "disable a pattern, adjust threshold, gate by regime), what is the "
        "expected impact on EV, win rate, drawdown, and trade frequency?"
    ),
    required_universes=(
        Universe.EXECUTION, Universe.DECISION,
        Universe.MARKET, Universe.STRATEGY,
    ),
    required_populations=(
        Population.ALL_TRADES, Population.EXECUTE_DECISIONS,
        Population.ALL_MARKET_STATES, Population.ALL_STRATEGIES,
    ),
    required_joins=(
        JoinRequirement(
            from_universe=Universe.DECISION,
            to_universe=Universe.EXECUTION,
            join_type=JoinType.ENTITY_ID,
            join_field="entity_id",
        ),
        JoinRequirement(
            from_universe=Universe.DECISION,
            to_universe=Universe.MARKET,
            join_type=JoinType.ENTITY_ID,
            join_field="entity_id",
        ),
        JoinRequirement(
            from_universe=Universe.DECISION,
            to_universe=Universe.STRATEGY,
            join_type=JoinType.ENTITY_ID,
            join_field="entity_id",
        ),
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.EXECUTION,
            populations=(Population.ALL_TRADES,),
            required_fields=("r_multiple",),
        ),
        AngleRequirement(
            universe=Universe.DECISION,
            populations=(Population.EXECUTE_DECISIONS,),
            required_fields=("score", "ev"),
        ),
        AngleRequirement(
            universe=Universe.MARKET,
            populations=(Population.ALL_MARKET_STATES,),
            required_fields=("regime",),
        ),
        AngleRequirement(
            universe=Universe.STRATEGY,
            populations=(Population.ALL_STRATEGIES,),
            required_fields=("family", "pattern"),
        ),
    ),
    views=(ViewType.NORMAL,),
    analysis_type=AnalysisType.SIMULATION,
    minimum_sample_size=50,
    dependencies=("E-001", "EDMS-001"),
    status=QuestionStatus.PARTIAL,
    source_intent=("P1", "L7"),
    decision_enabled="Should this finding be promoted to production?",
)


# ═══════════════════════════════════════════════════════════════════════════════
# SHADOW RESEARCH QUESTIONS (SD-nnn)
# Counterfactual questions operating on the Shadow Outcome Universe.
# Evidence from these questions is ALWAYS labelled COUNTERFACTUAL.
# ═══════════════════════════════════════════════════════════════════════════════

SD_001 = NewEngineQuestion(
    question_id="SD-001",
    title="Shadow Counterfactual Expectancy",
    research_intent=(
        "What is the counterfactual expectancy of ALL detected opportunities? "
        "This represents the total opportunity pool value before filtering."
    ),
    required_universes=(Universe.SHADOW_OUTCOME,),
    required_populations=(Population.ALL_SHADOW_OUTCOMES,),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.SHADOW_OUTCOME,
            populations=(Population.ALL_SHADOW_OUTCOMES, Population.SHADOW_WINS, Population.SHADOW_LOSSES),
            required_fields=("r_multiple", "direction", "exit_reason"),
        ),
    ),
    views=(ViewType.NORMAL,),
    analysis_type=AnalysisType.EXPECTANCY,
    minimum_sample_size=30,
    status=QuestionStatus.READY,
    source_intent=("E1-shadow",),
    decision_enabled="What is the opportunity pool value before V10 filtering?",
)

SD_002 = NewEngineQuestion(
    question_id="SD-002",
    title="Missed Opportunity Cost",
    research_intent=(
        "What counterfactual outcome did opportunities produce that V10 "
        "rejected (NO_TRADE decisions)? What is the cost of over-filtering?"
    ),
    required_universes=(Universe.SHADOW_OUTCOME,),
    required_populations=(Population.SHADOW_FROM_NO_TRADE,),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.SHADOW_OUTCOME,
            populations=(Population.SHADOW_FROM_NO_TRADE,),
            required_fields=("r_multiple", "exit_reason", "pattern"),
        ),
    ),
    views=(ViewType.NORMAL,),
    analysis_type=AnalysisType.EXPECTANCY,
    minimum_sample_size=20,
    status=QuestionStatus.READY,
    source_intent=("ED-002", "D5"),
    decision_enabled="Is V10 rejecting profitable opportunities? What is the filtering cost?",
)

SD_004 = NewEngineQuestion(
    question_id="SD-004",
    title="Rejection Stage Counterfactual Expectancy",
    research_intent=(
        "What counterfactual expectancy did rejected opportunities produce, "
        "segmented by the V10 pipeline stage that rejected them? Which stages "
        "remove the most counterfactual edge?"
    ),
    required_universes=(Universe.SHADOW_OUTCOME, Universe.DECISION),
    required_populations=(Population.SHADOW_FROM_NO_TRADE, Population.NO_TRADE_DECISIONS),
    required_joins=(
        JoinRequirement(
            from_universe=Universe.SHADOW_OUTCOME,
            to_universe=Universe.DECISION,
            join_type=JoinType.ENTITY_ID,
            join_field="entity_id",
        ),
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.SHADOW_OUTCOME,
            populations=(Population.SHADOW_FROM_NO_TRADE,),
            required_fields=("r_multiple", "entity_id"),
        ),
        AngleRequirement(
            universe=Universe.DECISION,
            populations=(Population.NO_TRADE_DECISIONS,),
            required_fields=("terminal_stage", "terminal_reason", "entity_id"),
        ),
    ),
    views=(ViewType.NORMAL,),
    analysis_type=AnalysisType.SEGMENTATION,
    minimum_sample_size=20,
    status=QuestionStatus.READY,
    source_intent=("D-004", "R1"),
    decision_enabled="Which rejection stages remove edge vs protect capital?",
)

SD_005 = NewEngineQuestion(
    question_id="SD-005",
    title="Shadow Horizon Comparison",
    research_intent=(
        "Which trade horizon (SCALP / INTRADAY / EXTENDED) captures the most "
        "counterfactual edge from detected opportunities?"
    ),
    required_universes=(Universe.SHADOW_OUTCOME,),
    required_populations=(
        Population.HORIZON_SCALP, Population.HORIZON_INTRADAY,
        Population.HORIZON_EXTENDED,
    ),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.SHADOW_OUTCOME,
            populations=(Population.HORIZON_SCALP, Population.HORIZON_INTRADAY),
            required_fields=("r_multiple", "trade_horizon", "exit_reason"),
        ),
    ),
    views=(ViewType.NORMAL,),
    analysis_type=AnalysisType.COMPARISON,
    minimum_sample_size=10,
    status=QuestionStatus.READY,
    source_intent=("S2", "S6"),
    decision_enabled="Should V10 prefer a different trade horizon?",
)

SD_006 = NewEngineQuestion(
    question_id="SD-006",
    title="Shadow Strategy Expectancy",
    research_intent=(
        "Which strategy families produce positive counterfactual expectancy "
        "across ALL detected opportunities (not just executed ones)?"
    ),
    required_universes=(Universe.SHADOW_OUTCOME,),
    required_populations=(Population.ALL_SHADOW_OUTCOMES,),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.SHADOW_OUTCOME,
            populations=(Population.ALL_SHADOW_OUTCOMES,),
            required_fields=("r_multiple", "strategy_id", "pattern"),
        ),
    ),
    views=(ViewType.NORMAL,),
    analysis_type=AnalysisType.SEGMENTATION,
    minimum_sample_size=10,
    status=QuestionStatus.READY,
    source_intent=("S-001", "E3"),
    decision_enabled="Should any strategy family be disabled or prioritised?",
)

SD_007 = NewEngineQuestion(
    question_id="SD-007",
    title="Shadow Regime Expectancy",
    research_intent=(
        "Does market regime predict counterfactual outcome across ALL "
        "detected opportunities? Should regime gate opportunity detection?"
    ),
    required_universes=(Universe.SHADOW_OUTCOME,),
    required_populations=(Population.ALL_SHADOW_OUTCOMES,),
    angle_requirements=(
        AngleRequirement(
            universe=Universe.SHADOW_OUTCOME,
            populations=(Population.ALL_SHADOW_OUTCOMES,),
            required_fields=("r_multiple", "regime"),
        ),
    ),
    views=(ViewType.NORMAL,),
    analysis_type=AnalysisType.SEGMENTATION,
    minimum_sample_size=10,
    status=QuestionStatus.READY,
    source_intent=("M-001", "EM-001"),
    decision_enabled="Should regime filter ALL opportunity detection, not just execution?",
)


# ═══════════════════════════════════════════════════════════════════════════════
# QUESTION BANK REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

QUESTION_BANK: tuple[NewEngineQuestion, ...] = (
    # Execution-primary (E-nnn)
    E_001, E_002, E_003, E_004, E_005, E_006, E_007, E_008, E_009, E_010,
    # Decision-primary (D-nnn)
    D_001, D_002, D_003, D_004, D_005, D_006, D_007,
    # Market-primary (M-nnn)
    M_001, M_002, M_003, M_004, M_005, M_006,
    # Strategy-primary (S-nnn)
    S_001, S_002, S_003, S_004,
    # Cross-angle: Execution + Decision (ED-nnn)
    ED_001, ED_002, ED_003,
    # Cross-angle: Execution + Market (EM-nnn)
    EM_001, EM_002,
    # Cross-angle: Execution + Strategy (ES-nnn)
    ES_001,
    # Cross-angle: Decision + Market (DM-nnn)
    DM_001, DM_002, DM_003,
    # Cross-angle: Decision + Strategy (DS-nnn)
    DS_001, DS_002,
    # Cross-angle: Market + Strategy (MS-nnn)
    MS_001, MS_002, MS_003,
    # Three-angle: Execution + Decision + Market (EDM-nnn)
    EDM_001,
    # Three-angle: Decision + Market + Strategy (DMS-nnn)
    DMS_001,
    # Four-angle: All (EDMS-nnn)
    EDMS_001, EDMS_002,
    # Shadow research questions (SD-nnn) — counterfactual evidence
    SD_001, SD_002, SD_004, SD_005, SD_006, SD_007,
)

QUESTION_BANK_BY_ID: dict[str, NewEngineQuestion] = {
    q.question_id: q for q in QUESTION_BANK
}


# ═══════════════════════════════════════════════════════════════════════════════
# ACCESSOR FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════


def get_question(question_id: str) -> NewEngineQuestion | None:
    """Look up a question by its new-engine ID."""
    return QUESTION_BANK_BY_ID.get(question_id)


def get_questions_by_universe(universe: Universe) -> list[NewEngineQuestion]:
    """Return all questions that require a given universe."""
    return [q for q in QUESTION_BANK if universe in q.required_universes]


def get_questions_by_status(status: QuestionStatus) -> list[NewEngineQuestion]:
    """Return all questions with a given execution status."""
    return [q for q in QUESTION_BANK if q.status == status]


def get_cross_angle_questions() -> list[NewEngineQuestion]:
    """Return all questions that span multiple universes."""
    return [q for q in QUESTION_BANK if q.is_cross_angle]


def get_single_angle_questions() -> list[NewEngineQuestion]:
    """Return all questions that use exactly one universe."""
    return [q for q in QUESTION_BANK if not q.is_cross_angle]


def get_questions_requiring_join(
    from_u: Universe, to_u: Universe
) -> list[NewEngineQuestion]:
    """Return questions that join two specific universes."""
    return [
        q for q in QUESTION_BANK
        if any(
            j.from_universe == from_u and j.to_universe == to_u
            for j in q.required_joins
        )
    ]


def get_ready_questions() -> list[NewEngineQuestion]:
    """Return all questions that can be executed immediately."""
    return [q for q in QUESTION_BANK if q.status == QuestionStatus.READY]


def get_questions_with_view(view: ViewType) -> list[NewEngineQuestion]:
    """Return all questions that declare a specific view type."""
    return [q for q in QUESTION_BANK if view in q.views]
