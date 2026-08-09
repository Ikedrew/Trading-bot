"""
Research Intelligence — Question Registry.

Central registry of all research questions. Each question defines:
- required fields and segments
- minimum sample size
- experiment module to execute
- status (active/deprecated/draft)
"""

from __future__ import annotations

from typing import Any

from research_engine.v10.research_intelligence.models import QuestionDefinition


# ═══════════════════════════════════════════════════════════════
# REGISTERED QUESTIONS
# ═══════════════════════════════════════════════════════════════

_QUESTIONS: list[QuestionDefinition] = [
    # ─── TRADE / EXECUTION ────────────────────────────────────
    QuestionDefinition(
        id="E1",
        name="True System Expectancy",
        category="outcome",
        domain="trade",
        description="Does the overall system have positive expectancy?",
        required_fields=["execution.r_multiple", "execution.net_realised_pnl"],
        minimum_sample_size=20,
        experiment_module="research_engine.v10.e1_expectancy",
    ),
    QuestionDefinition(
        id="E2",
        name="Pattern Performance",
        category="outcome",
        domain="trade",
        description="Which candlestick patterns produce positive expectancy?",
        required_fields=["strategy.pattern", "execution.r_multiple"],
        minimum_sample_size=10,
        experiment_module="research_engine.v10.e2_pattern",
    ),

    # ─── RISK ─────────────────────────────────────────────────
    QuestionDefinition(
        id="R1",
        name="Risk Model Effectiveness",
        category="risk",
        domain="trade",
        description="Is the risk model correctly sizing positions and limiting drawdown?",
        required_fields=["execution.r_multiple", "execution.stop_loss"],
        minimum_sample_size=20,
        experiment_module="research_engine.v10.r1_risk_model",
    ),
    QuestionDefinition(
        id="R2",
        name="Stop Placement Effectiveness",
        category="risk",
        domain="trade",
        description="Is stop loss placement reducing expectancy by being too tight or wide?",
        required_fields=["execution.stop_loss", "execution.entry_price", "execution.r_multiple"],
        required_segments=["instrument", "regime"],
        minimum_sample_size=15,
        experiment_module="research_engine.v10.r2_stop_effectiveness",
    ),

    # ─── DECISION ─────────────────────────────────────────────
    QuestionDefinition(
        id="D1",
        name="Score Predictive Power",
        category="prediction",
        domain="decision",
        description="Does the decision score predict trade outcome?",
        required_fields=["decision.score", "execution.r_multiple"],
        minimum_sample_size=20,
        experiment_module="research_engine.v10.d1_scoring",
    ),
    QuestionDefinition(
        id="D2",
        name="EV Calibration",
        category="prediction",
        domain="decision",
        description="Are the bot's expected value estimates calibrated?",
        required_fields=["decision.ev", "execution.r_multiple"],
        minimum_sample_size=15,
        experiment_module="research_engine.v10.d2_ev_calibration",
    ),
    QuestionDefinition(
        id="D3",
        name="Decision Threshold Effectiveness",
        category="prediction",
        domain="decision",
        description="Are the score thresholds set optimally?",
        required_fields=["decision.score", "execution.r_multiple"],
        minimum_sample_size=20,
        experiment_module="research_engine.v10.d3_threshold_effectiveness",
    ),

    # ─── MARKET ───────────────────────────────────────────────
    QuestionDefinition(
        id="M1",
        name="Regime Expectancy",
        category="regime",
        domain="market",
        description="Does expectancy differ significantly across market regimes?",
        required_fields=["market.regime", "execution.r_multiple"],
        required_segments=["regime"],
        minimum_sample_size=10,
        experiment_module="research_engine.v10.m1_regime",
    ),
    QuestionDefinition(
        id="C1",
        name="Session Effectiveness",
        category="session",
        domain="market",
        description="Does the system perform differently across trading sessions?",
        required_fields=["market.session", "execution.r_multiple"],
        required_segments=["session"],
        minimum_sample_size=10,
        experiment_module="",
        status="draft",
    ),

    # ─── STRATEGY ─────────────────────────────────────────────
    QuestionDefinition(
        id="S1",
        name="Strategy Family Performance",
        category="performance",
        domain="strategy",
        description="Which strategy families produce the best risk-adjusted returns?",
        required_fields=["strategy.family", "execution.r_multiple"],
        minimum_sample_size=10,
        experiment_module="",
        status="draft",
    ),

    # ─── OPPORTUNITY ──────────────────────────────────────────
    QuestionDefinition(
        id="OQ1",
        name="Opportunity Quality",
        category="opportunity",
        domain="strategy",
        description="Does opportunity quality score predict trade success?",
        required_fields=["strategy.opportunity_quality", "execution.r_multiple"],
        minimum_sample_size=15,
        experiment_module="research_engine.v10.oq1_opportunity_quality",
    ),
    QuestionDefinition(
        id="OQ2",
        name="Opportunity Failure Analysis",
        category="opportunity",
        domain="strategy",
        description="What characterises failed opportunities?",
        required_fields=["strategy.opportunity_quality", "execution.r_multiple"],
        minimum_sample_size=15,
        experiment_module="research_engine.v10.oq2_opportunity_failure",
    ),
]


# ═══════════════════════════════════════════════════════════════
# REGISTRY CLASS
# ═══════════════════════════════════════════════════════════════

class QuestionRegistry:
    """Central registry for research questions."""

    def __init__(self):
        self._questions = {q.id: q for q in _QUESTIONS}

    def get(self, question_id: str) -> QuestionDefinition | None:
        """Get a question by ID."""
        return self._questions.get(question_id.upper())

    def list_active(self) -> list[QuestionDefinition]:
        """List all active questions."""
        return [q for q in self._questions.values() if q.status == "active"]

    def list_by_category(self, category: str) -> list[QuestionDefinition]:
        """List questions in a category."""
        return [q for q in self._questions.values()
                if q.category == category and q.status == "active"]

    def list_by_domain(self, domain: str) -> list[QuestionDefinition]:
        """List questions belonging to a domain."""
        return [q for q in self._questions.values() if q.domain == domain]

    def list_all(self) -> list[QuestionDefinition]:
        """List all questions regardless of status."""
        return list(self._questions.values())

    @property
    def active_ids(self) -> list[str]:
        """Get list of active question IDs."""
        return [q.id for q in self.list_active()]

    def register(self, question: QuestionDefinition) -> None:
        """Register a new question (or update existing)."""
        self._questions[question.id] = question

    def to_dict(self) -> list[dict[str, Any]]:
        """Export registry as list of dicts."""
        return [
            {
                "id": q.id,
                "name": q.name,
                "category": q.category,
                "domain": q.domain,
                "description": q.description,
                "minimum_sample_size": q.minimum_sample_size,
                "experiment_module": q.experiment_module,
                "status": q.status,
            }
            for q in self._questions.values()
        ]
