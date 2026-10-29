"""
═══════════════════════════════════════════════════════════════════════════════
V10 RESEARCH REGISTRY — Source of truth for all V10 research questions.
═══════════════════════════════════════════════════════════════════════════════

Created: 2026-08-05
Previous registry: research_registry_v1_old_engine.py (frozen, archived)

Migration policy:
    CARRY_OVER — Question valid for V10 unchanged
    MODIFY     — Question adapted for V10 architecture changes
    ARCHIVE    — Question only applied to old engine (not migrated)
    NEW_V10    — New question required by V10-specific systems

Each question answers: "What decision will this research help us make?"

Data source: logs/research_ready_trade_dataset/research_ready_trades.jsonl
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ═══════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════

class V10Category(str, Enum):
    SYSTEM_EDGE = "SYSTEM_EDGE"
    MARKET_CONTEXT = "MARKET_CONTEXT"
    OPPORTUNITY_QUALITY = "OPPORTUNITY_QUALITY"
    DECISION_QUALITY = "DECISION_QUALITY"
    STRATEGY_CLASSIFICATION = "STRATEGY_CLASSIFICATION"
    RISK_MANAGEMENT = "RISK_MANAGEMENT"
    EXECUTION_QUALITY = "EXECUTION_QUALITY"
    EXIT_MANAGEMENT = "EXIT_MANAGEMENT"
    LEARNING_EVOLUTION = "LEARNING_EVOLUTION"


class V10Status(str, Enum):
    READY = "READY"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"


class V10Priority(str, Enum):
    P0 = "P0"  # Must answer before trusting live results
    P1 = "P1"  # Important for confidence
    P2 = "P2"  # Useful refinement


class MigrationSource(str, Enum):
    CARRY_OVER = "CARRY_OVER"
    MODIFY = "MODIFY"
    ARCHIVE = "ARCHIVE"  # Not migrated (reference only)
    NEW_V10 = "NEW_V10"


# ═══════════════════════════════════════════════════════════════
# V10 RESEARCH QUESTION MODEL
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class V10ResearchQuestion:
    """A single V10 research question with full context."""

    research_id: str
    title: str
    description: str
    category: V10Category
    source: MigrationSource
    old_ids: tuple[str, ...] = ()  # Which old registry IDs this replaces

    # Status (computed from data availability)
    status: V10Status = V10Status.BLOCKED
    status_reason: str = ""

    # Data requirements
    required_data: tuple[str, ...] = ()
    available_data: tuple[str, ...] = ()

    # Decision context
    expected_decision: str = ""  # What decision does this help make?

    priority: V10Priority = V10Priority.P1

    def to_dict(self) -> dict[str, Any]:
        return {
            "research_id": self.research_id,
            "title": self.title,
            "category": self.category.value,
            "source": self.source.value,
            "old_ids": list(self.old_ids),
            "status": self.status.value,
            "status_reason": self.status_reason,
            "required_data": list(self.required_data),
            "available_data": list(self.available_data),
            "expected_decision": self.expected_decision,
            "priority": self.priority.value,
        }


# ═══════════════════════════════════════════════════════════════
# V10 REGISTRY — SYSTEM EDGE
# ═══════════════════════════════════════════════════════════════

V10_E1 = V10ResearchQuestion(
    research_id="V10-E1",
    title="True system expectancy",
    description="What is the realised expectancy (mean R-multiple) of the V10 pipeline?",
    category=V10Category.SYSTEM_EDGE,
    source=MigrationSource.CARRY_OVER,
    old_ids=("E1",),
    status=V10Status.READY,
    status_reason="84 validated trades with exit_reason + risk geometry available",
    required_data=("entry_price", "stop_loss", "exit_price", "direction", "final_pnl"),
    available_data=("entry_price", "stop_loss", "exit_price", "direction", "final_pnl"),
    expected_decision="Should we continue trading this system live? Is expectancy positive?",
    priority=V10Priority.P0,
)

V10_E2 = V10ResearchQuestion(
    research_id="V10-E2",
    title="Pattern expectancy",
    description="Which candlestick patterns produce positive expectancy in V10?",
    category=V10Category.SYSTEM_EDGE,
    source=MigrationSource.CARRY_OVER,
    old_ids=("E2",),
    status=V10Status.READY,
    status_reason="Pattern field at 100% coverage",
    required_data=("pattern", "r_multiple"),
    available_data=("pattern", "r_multiple"),
    expected_decision="Should we disable any patterns? Should we weight them differently?",
    priority=V10Priority.P0,
)

V10_E3 = V10ResearchQuestion(
    research_id="V10-E3",
    title="Strategy family expectancy",
    description="Which V10 strategy families (TREND_CONTINUATION, MEAN_REVERSION, etc.) produce edge?",
    category=V10Category.STRATEGY_CLASSIFICATION,
    source=MigrationSource.MODIFY,
    old_ids=("E3", "S1", "S5"),
    status=V10Status.BLOCKED,
    status_reason="Strategy field at 14% — V10 strategy_family not populated in historical decision traces",
    required_data=("strategy_family", "r_multiple"),
    available_data=("r_multiple",),
    expected_decision="Should we disable specific strategy families? Re-weight priority order?",
    priority=V10Priority.P0,
)

V10_E4 = V10ResearchQuestion(
    research_id="V10-E4",
    title="Out-of-sample edge validation",
    description="Does measured edge survive walk-forward testing?",
    category=V10Category.SYSTEM_EDGE,
    source=MigrationSource.CARRY_OVER,
    old_ids=("E5",),
    status=V10Status.BLOCKED,
    status_reason="Need 200+ validated trades for meaningful train/test split (currently 84)",
    required_data=("r_multiple", "entry_time", "pattern"),
    available_data=("r_multiple", "entry_time", "pattern"),
    expected_decision="Can we trust the measured edge isn't overfitted?",
    priority=V10Priority.P0,
)

# ═══════════════════════════════════════════════════════════════
# V10 REGISTRY — MARKET CONTEXT
# ═══════════════════════════════════════════════════════════════

V10_M1 = V10ResearchQuestion(
    research_id="V10-M1",
    title="Regime predicts outcomes",
    description="Does V10 regime classification (TRENDING/RANGING/TRANSITIONAL) predict trade R-multiple?",
    category=V10Category.MARKET_CONTEXT,
    source=MigrationSource.CARRY_OVER,
    old_ids=("M1",),
    status=V10Status.READY,
    status_reason="Regime at 90% coverage",
    required_data=("regime", "r_multiple"),
    available_data=("regime", "r_multiple"),
    expected_decision="Should we filter trades by regime? Does regime improve/reduce expectancy?",
    priority=V10Priority.P0,
)

V10_M2 = V10ResearchQuestion(
    research_id="V10-M2",
    title="HTF alignment value",
    description="Does HTF alignment score predict trade outcomes better than individual timeframe data?",
    category=V10Category.MARKET_CONTEXT,
    source=MigrationSource.NEW_V10,
    old_ids=(),
    status=V10Status.PARTIAL,
    status_reason="HTF alignment not directly in research dataset but derivable from decision_trace join",
    required_data=("htf_alignment", "r_multiple"),
    available_data=("r_multiple",),
    expected_decision="Should HTF alignment gate trading? What minimum alignment produces edge?",
    priority=V10Priority.P1,
)

V10_M3 = V10ResearchQuestion(
    research_id="V10-M3",
    title="Regime + volatility interaction",
    description="Does combining regime + volatility_state improve prediction beyond regime alone?",
    category=V10Category.MARKET_CONTEXT,
    source=MigrationSource.NEW_V10,
    old_ids=(),
    status=V10Status.BLOCKED,
    status_reason="Volatility state not in current research dataset",
    required_data=("regime", "volatility_state", "r_multiple"),
    available_data=("regime", "r_multiple"),
    expected_decision="Should we add volatility to the opportunity quality assessment?",
    priority=V10Priority.P1,
)

# ═══════════════════════════════════════════════════════════════
# V10 REGISTRY — OPPORTUNITY QUALITY
# ═══════════════════════════════════════════════════════════════

V10_OQ1 = V10ResearchQuestion(
    research_id="V10-OQ1",
    title="Opportunity quality predicts outcomes",
    description="Does the V10 4-dimension quality score (location/structure/behaviour/formation) correlate with realised R?",
    category=V10Category.OPPORTUNITY_QUALITY,
    source=MigrationSource.NEW_V10,
    old_ids=(),
    status=V10Status.BLOCKED,
    status_reason="Opportunity quality score not yet in research dataset (available via V10 pipeline_result)",
    required_data=("opportunity_quality", "location_score", "structure_score", "r_multiple"),
    available_data=("r_multiple",),
    expected_decision="Should quality score scale position size? What quality threshold produces edge?",
    priority=V10Priority.P0,
)

V10_OQ2 = V10ResearchQuestion(
    research_id="V10-OQ2",
    title="Opportunity ranking accuracy",
    description="When multiple opportunities exist, does the ranking engine select the highest-R outcome?",
    category=V10Category.OPPORTUNITY_QUALITY,
    source=MigrationSource.NEW_V10,
    old_ids=("D6",),
    status=V10Status.BLOCKED,
    status_reason="Shadow ranking data accumulating (logs/ranking_shadow/) — need 50+ multi-candidate cycles",
    required_data=("ranking_recommended", "actually_executed", "r_multiple"),
    available_data=(),
    expected_decision="Should ranking be promoted from shadow to active? Does it improve selection?",
    priority=V10Priority.P0,
)

# ═══════════════════════════════════════════════════════════════
# V10 REGISTRY — DECISION QUALITY
# ═══════════════════════════════════════════════════════════════

V10_D1 = V10ResearchQuestion(
    research_id="V10-D1",
    title="Scoring components predict R",
    description="Which scoring components best predict actual R-multiple outcomes?",
    category=V10Category.DECISION_QUALITY,
    source=MigrationSource.CARRY_OVER,
    old_ids=("D1",),
    status=V10Status.READY,
    status_reason="Score at 99%, components available via decision_trace join",
    required_data=("score", "components", "r_multiple"),
    available_data=("score", "r_multiple"),
    expected_decision="Should we re-weight scoring components? Which are predictive vs noise?",
    priority=V10Priority.P0,
)

V10_D2 = V10ResearchQuestion(
    research_id="V10-D2",
    title="EV calibration",
    description="Is the EV estimate calibrated — does predicted win probability match actual outcomes?",
    category=V10Category.DECISION_QUALITY,
    source=MigrationSource.CARRY_OVER,
    old_ids=("D2", "D3"),
    status=V10Status.READY,
    status_reason="EV at 90% coverage",
    required_data=("ev", "r_multiple"),
    available_data=("ev", "r_multiple"),
    expected_decision="Should EV gate be enabled/disabled? Is predicted probability trustworthy?",
    priority=V10Priority.P0,
)

# ═══════════════════════════════════════════════════════════════
# V10 REGISTRY — STRATEGY CLASSIFICATION
# ═══════════════════════════════════════════════════════════════

V10_SC1 = V10ResearchQuestion(
    research_id="V10-SC1",
    title="V10 strategy family edge",
    description="Which of the 6 V10 strategy families produce positive expectancy?",
    category=V10Category.STRATEGY_CLASSIFICATION,
    source=MigrationSource.MODIFY,
    old_ids=("E3", "S1"),
    status=V10Status.BLOCKED,
    status_reason="Strategy field at 14% — new V10 trades will populate this correctly",
    required_data=("strategy_family", "r_multiple"),
    available_data=("r_multiple",),
    expected_decision="Disable unprofitable families? Adjust priority order?",
    priority=V10Priority.P0,
)

V10_SC2 = V10ResearchQuestion(
    research_id="V10-SC2",
    title="Strategy × regime interaction",
    description="Do strategies perform differently in different regimes?",
    category=V10Category.STRATEGY_CLASSIFICATION,
    source=MigrationSource.MODIFY,
    old_ids=("M2", "S4"),
    status=V10Status.BLOCKED,
    status_reason="Strategy field at 14%",
    required_data=("strategy_family", "regime", "r_multiple"),
    available_data=("regime", "r_multiple"),
    expected_decision="Should strategy selection be regime-gated?",
    priority=V10Priority.P1,
)

# ═══════════════════════════════════════════════════════════════
# V10 REGISTRY — RISK MANAGEMENT
# ═══════════════════════════════════════════════════════════════

V10_R1 = V10ResearchQuestion(
    research_id="V10-R1",
    title="Risk model effectiveness",
    description="Does the risk layer (guards, daily loss, exposure) improve survival?",
    category=V10Category.RISK_MANAGEMENT,
    source=MigrationSource.CARRY_OVER,
    old_ids=("R1", "R2"),
    status=V10Status.PARTIAL,
    status_reason="Need rejected+allowed comparison from decision traces",
    required_data=("terminal_stage", "r_multiple"),
    available_data=("r_multiple",),
    expected_decision="Are guards removing edge or protecting capital?",
    priority=V10Priority.P1,
)

V10_R2 = V10ResearchQuestion(
    research_id="V10-R2",
    title="Probability of ruin",
    description="Given measured edge and variance, what is ruin probability?",
    category=V10Category.RISK_MANAGEMENT,
    source=MigrationSource.CARRY_OVER,
    old_ids=("R3",),
    status=V10Status.BLOCKED,
    status_reason="Need 200+ trades for reliable variance estimation",
    required_data=("r_multiple",),
    available_data=("r_multiple",),
    expected_decision="Is the system safe to trade at current sizing? Need to reduce?",
    priority=V10Priority.P0,
)

V10_R3 = V10ResearchQuestion(
    research_id="V10-R3",
    title="Quality-scaled position sizing",
    description="Does scaling position size by opportunity quality improve risk-adjusted returns?",
    category=V10Category.RISK_MANAGEMENT,
    source=MigrationSource.NEW_V10,
    old_ids=("R5",),
    status=V10Status.BLOCKED,
    status_reason="Opportunity quality not in research dataset yet",
    required_data=("opportunity_quality", "r_multiple", "position_size"),
    available_data=("r_multiple",),
    expected_decision="Should we implement quality-scaled sizing?",
    priority=V10Priority.P1,
)

# ═══════════════════════════════════════════════════════════════
# V10 REGISTRY — EXECUTION QUALITY
# ═══════════════════════════════════════════════════════════════

V10_X1 = V10ResearchQuestion(
    research_id="V10-X1",
    title="Execution quality by session",
    description="Which sessions produce best fills (lowest slippage, fastest execution)?",
    category=V10Category.EXECUTION_QUALITY,
    source=MigrationSource.CARRY_OVER,
    old_ids=("X1", "X3"),
    status=V10Status.BLOCKED,
    status_reason="Execution results dataset not joined to research dataset",
    required_data=("slippage", "session", "fill_latency"),
    available_data=(),
    expected_decision="Should we restrict trading to specific sessions?",
    priority=V10Priority.P2,
)

V10_X2 = V10ResearchQuestion(
    research_id="V10-X2",
    title="Protection verification reliability",
    description="How often does SL/TP verification succeed? What causes failures?",
    category=V10Category.EXECUTION_QUALITY,
    source=MigrationSource.NEW_V10,
    old_ids=(),
    status=V10Status.PARTIAL,
    status_reason="Protection audit data exists (logs/protection_audit/) but not joined",
    required_data=("protection_status", "verification_latency"),
    available_data=(),
    expected_decision="Is broker protection reliable? Do we need additional safety?",
    priority=V10Priority.P1,
)

# ═══════════════════════════════════════════════════════════════
# V10 REGISTRY — EXIT MANAGEMENT
# ═══════════════════════════════════════════════════════════════

V10_EX1 = V10ResearchQuestion(
    research_id="V10-EX1",
    title="Exit reason distribution",
    description="What percentage of trades hit SL vs TP vs time exit?",
    category=V10Category.EXIT_MANAGEMENT,
    source=MigrationSource.MODIFY,
    old_ids=("EX1",),
    status=V10Status.READY,
    status_reason="Exit reasons reconstructed at 100% (68 SL, 16 TP)",
    required_data=("exit_reason_validated", "r_multiple"),
    available_data=("exit_reason_validated", "r_multiple"),
    expected_decision="Is SL/TP ratio healthy? Are we exiting too early/late?",
    priority=V10Priority.P0,
)

V10_EX2 = V10ResearchQuestion(
    research_id="V10-EX2",
    title="Trailing stop effectiveness",
    description="Does the trailing stop capture more profit than fixed TP?",
    category=V10Category.EXIT_MANAGEMENT,
    source=MigrationSource.CARRY_OVER,
    old_ids=("EX2",),
    status=V10Status.BLOCKED,
    status_reason="Need MFE/MAE data from shadow trades (bar-by-bar progression)",
    required_data=("mfe_r", "pnl_r_multiple", "trade_state_progression"),
    available_data=(),
    expected_decision="Should trailing stop parameters be adjusted?",
    priority=V10Priority.P1,
)

V10_EX3 = V10ResearchQuestion(
    research_id="V10-EX3",
    title="Horizon determines exit policy",
    description="Does each trade horizon need different exit parameters?",
    category=V10Category.EXIT_MANAGEMENT,
    source=MigrationSource.CARRY_OVER,
    old_ids=("EX5",),
    status=V10Status.PARTIAL,
    status_reason="Horizon at 67% coverage — partial analysis possible",
    required_data=("trade_horizon", "exit_reason_validated", "duration_seconds", "r_multiple"),
    available_data=("trade_horizon", "exit_reason_validated", "duration_seconds", "r_multiple"),
    expected_decision="Should horizon profiles have different break-even/trailing settings?",
    priority=V10Priority.P1,
)

# ═══════════════════════════════════════════════════════════════
# V10 REGISTRY — LEARNING / EVOLUTION
# ═══════════════════════════════════════════════════════════════

V10_L1 = V10ResearchQuestion(
    research_id="V10-L1",
    title="Pattern degradation over time",
    description="Are any patterns losing edge over the observation period?",
    category=V10Category.LEARNING_EVOLUTION,
    source=MigrationSource.CARRY_OVER,
    old_ids=("L1",),
    status=V10Status.PARTIAL,
    status_reason="Only 14 days of data — insufficient for degradation detection",
    required_data=("pattern", "r_multiple", "entry_time"),
    available_data=("pattern", "r_multiple", "entry_time"),
    expected_decision="Should any pattern be disabled or down-weighted?",
    priority=V10Priority.P2,
)

V10_L2 = V10ResearchQuestion(
    research_id="V10-L2",
    title="Architecture improvement tracking",
    description="Did the V10 migration improve expectancy vs old engine?",
    category=V10Category.LEARNING_EVOLUTION,
    source=MigrationSource.CARRY_OVER,
    old_ids=("L2",),
    status=V10Status.BLOCKED,
    status_reason="Need pre-V10 and post-V10 comparable datasets",
    required_data=("r_multiple", "schema_version", "entry_time"),
    available_data=("r_multiple", "entry_time"),
    expected_decision="Was V10 migration a net positive?",
    priority=V10Priority.P2,
)


# ═══════════════════════════════════════════════════════════════
# REGISTRY
# ═══════════════════════════════════════════════════════════════

V10_REGISTRY: tuple[V10ResearchQuestion, ...] = (
    # System Edge
    V10_E1, V10_E2, V10_E3, V10_E4,
    # Market Context
    V10_M1, V10_M2, V10_M3,
    # Opportunity Quality
    V10_OQ1, V10_OQ2,
    # Decision Quality
    V10_D1, V10_D2,
    # Strategy Classification
    V10_SC1, V10_SC2,
    # Risk Management
    V10_R1, V10_R2, V10_R3,
    # Execution Quality
    V10_X1, V10_X2,
    # Exit Management
    V10_EX1, V10_EX2, V10_EX3,
    # Learning / Evolution
    V10_L1, V10_L2,
)

V10_REGISTRY_BY_ID: dict[str, V10ResearchQuestion] = {q.research_id: q for q in V10_REGISTRY}


def get_v10_question(research_id: str) -> V10ResearchQuestion | None:
    return V10_REGISTRY_BY_ID.get(research_id)


def get_v10_questions_by_status(status: V10Status) -> list[V10ResearchQuestion]:
    return [q for q in V10_REGISTRY if q.status == status]


def get_v10_questions_by_category(category: V10Category) -> list[V10ResearchQuestion]:
    return [q for q in V10_REGISTRY if q.category == category]
