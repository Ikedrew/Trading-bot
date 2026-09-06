"""
Research Engine Dataset Disposition Layer — the explicit research contract overlay.

Every active dataset in the canonical production V1 data contract
(``core.production_data_contract.PRODUCTION_SCHEMA_REGISTRY``) must have an
explicit RESEARCH disposition. This module is the single, machine-readable
registry of ``dataset -> research disposition -> reason`` so that:

    - a future completeness audit can distinguish accidentally unconsumed,
      intentionally excluded, and actively consumed datasets;
    - every dataset name answers: WHAT is its research status, WHY, WHO consumes
      it, WHICH keys join it, and WHEN its fields become observable
      (temporal availability / leakage classification);
    - the Research Engine can loudly fail when a new canonical dataset is added
      to the production contract WITHOUT a deliberate research disposition
      (see ``assert_full_coverage()``).

This layer is NOT a second production data contract. It is a read-only
disposition overlay over the existing production contract.

Disposition classes (mapped from the audit's Phase-2 A–F classes):

    A — RESEARCH INPUT              -> status ``DIRECTLY_CONSUMED``
    B — SUPPORTING/DIAGNOSTIC       -> status ``SUPPORTING_CONSUMED``
    C — OPERATIONAL ONLY            -> status ``INTENTIONALLY_OPERATIONAL``
    D — REDUNDANT / DERIVABLE       -> status ``REDUNDANT_DERIVED``
    E — STATE / NON-EVENT           -> status ``RUNTIME_STATE``
    (derived research artifacts)    -> status ``RESEARCH_OUTPUT``

Temporal availability (Phase 5 — leakage prevention):

    BEFORE_DECISION                 evidence known when the decision is made;
                                    safe as pre-decision explanatory features.
    AFTER_DECISION_BEFORE_OUTCOME   evidence known only after the decision but
                                    before the final outcome; safe for
                                    execution/management analysis, NEVER for
                                    pre-decision decision features.
    AFTER_OUTCOME                   final result/labels; may only be used as
                                    outcome/label evidence, NEVER projected back
                                    into pre-decision explanatory features.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from core.production_data_contract import PRODUCTION_SCHEMA_REGISTRY


# ═══════════════════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════════════════


class ResearchDispositionStatus(str, Enum):
    """Machine-readable research status for a dataset."""

    DIRECTLY_CONSUMED = "DIRECTLY_CONSUMED"                # A — research input
    SUPPORTING_CONSUMED = "SUPPORTING_CONSUMED"            # B — diagnostics
    INTENTIONALLY_OPERATIONAL = "INTENTIONALLY_OPERATIONAL"  # C — operational only
    REDUNDANT_DERIVED = "REDUNDANT_DERIVED"                # D — duplicates authority
    RUNTIME_STATE = "RUNTIME_STATE"                        # E — state, non-event
    RESEARCH_OUTPUT = "RESEARCH_OUTPUT"                    # derived research artifacts


class Phase2Disposition(str, Enum):
    """The audit's Phase-2 semantic classification exactly."""

    A_RESEARCH_INPUT = "A_RESEARCH_INPUT"
    B_SUPPORTING_DIAGNOSTIC = "B_SUPPORTING_DIAGNOSTIC"
    C_OPERATIONAL_ONLY = "C_OPERATIONAL_ONLY"
    D_REDUNDANT_DERIVABLE = "D_REDUNDANT_DERIVABLE"
    E_STATE_NON_EVENT = "E_STATE_NON_EVENT"
    F_UNKNOWN = "F_UNKNOWN"


class TemporalAvailability(str, Enum):
    """When a dataset's fields become observable relative to the trade lifecycle."""

    BEFORE_DECISION = "BEFORE_DECISION"
    AFTER_DECISION_BEFORE_OUTCOME = "AFTER_DECISION_BEFORE_OUTCOME"
    AFTER_OUTCOME = "AFTER_OUTCOME"
    LIFECYCLE = "LIFECYCLE"  # emitted at multiple stages; classify per field usage
# ═══════════════════════════════════════════════════════════════════════════════
# DISPOSITION MODEL
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ResearchDisposition:
    """Explicit research disposition for one canonical V1 dataset.

    Attributes:
        dataset: production-contract dataset name (position_excursion is the one
            documented registry-external runtime-state surface).
        phase2: the Step-4 audit Phase-2 classification (A–F).
        status: machine-readable research status.
        reason: WHY this disposition (audit rationale).
        research_purpose: what research questions the dataset answers, or why it
            must not influence research conclusions.
        consumers: registered Research Engine consumers (names of evidence
            modules / loaders / universes). Empty tuple = deliberately no
            consumer.
        join_keys: canonical lineage / join fields (order = preferred join order).
        temporal_availability: leakage classification (Phase 5).
        authoritative_alternative: dataset that is the canonical authority for
            any overlapping information (empty when this is the authority).
        lineage_guard_notes: documented edge cases for joins (e.g. null
            trade_id on ENTRY attempts).
    """

    dataset: str
    phase2: Phase2Disposition
    status: ResearchDispositionStatus
    reason: str
    research_purpose: str
    consumers: tuple[str, ...] = ()
    join_keys: tuple[str, ...] = ()
    temporal_availability: TemporalAvailability = TemporalAvailability.LIFECYCLE
    authoritative_alternative: str = ""
    lineage_guard_notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "phase2": self.phase2.value,
            "status": self.status.value,
            "reason": self.reason,
            "research_purpose": self.research_purpose,
            "consumers": list(self.consumers),
            "join_keys": list(self.join_keys),
            "temporal_availability": self.temporal_availability.value,
            "authoritative_alternative": self.authoritative_alternative,
            "lineage_guard_notes": self.lineage_guard_notes,
        }
# ═══════════════════════════════════════════════════════════════════════════════
# THE DISPOSITION REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

RESEARCH_DISPOSITIONS: dict[str, ResearchDisposition] = {
    # ────────────────────────────────────────────────────────────────────────────
    # THE SIX STEP-4 AUDIT DATASETS (explicit dispositions decided by the audit)
    # ────────────────────────────────────────────────────────────────────────────
    "events": ResearchDisposition(
        dataset="events",
        phase2=Phase2Disposition.C_OPERATIONAL_ONLY,
        status=ResearchDispositionStatus.INTENTIONALLY_OPERATIONAL,
        reason=(
            "Canonical observation/telemetry transport (strict allowlist: CANDLE, "
            "FEATURE_UPDATE, FEED_HEALTH, DATA_GAP, RECONNECT, SYSTEM_HEALTH, "
            "CLOCK_SYNC). Raw market observations + infrastructure telemetry, with "
            "payload documented as opaque to analytics. The domain facts that the "
            "Research Engine analyses (opportunities, decisions, executions, "
            "outcomes, market state) are all consumed from their authoritative "
            "domain datasets. Treating the raw event stream as research rows would "
            "double-count facts already represented by decision/execution/outcome "
            "datasets and create a generic observation universe with no signed-off "
            "research question."
        ),
        research_purpose=(
            "Operational observability, replay, dashboards, Athena monitoring, and "
            "missing-event/feed-health diagnostics. Deliberately NOT a statistical "
            "research population for V1."
        ),
        consumers=(),
        join_keys=(),
        temporal_availability=TemporalAvailability.LIFECYCLE,
        lineage_guard_notes=(
            "If a future V2 research surface needs raw-candle or feed-telemetry "
            "evidence it must consume selected event TYPES only (e.g. DATA_GAP / "
            "FEED_HEALTH for acquisition-integrity diagnostics) — never the whole "
            "stream as a research population."
        ),
    ),
    "horizon_candidates": ResearchDisposition(
        dataset="horizon_candidates",
        phase2=Phase2Disposition.A_RESEARCH_INPUT,
        status=ResearchDispositionStatus.DIRECTLY_CONSUMED,
        reason=(
            "Preserves the complete horizon candidate search space per opportunity: "
            "every evaluated horizon (SCALP/INTRADAY/EXTENDED) with eligible, "
            "confidence, reasoning, evidence sub-dict, and selection_status "
            "(SELECTED/REJECTED/INELIGIBLE/NOT_APPLICABLE). This counterfactual "
            "information (selected vs rejected vs ineligible horizons) is NOT "
            "available in decision_trace (which only carries the terminal "
            "decision/strategy) and is unique to this dataset."
        ),
        research_purpose=(
            "Selected-vs-rejected horizon comparison: is the chosen horizon the "
            "right one? Eligibility and confidence calibration by horizon; "
            "HTF-alignment gating analysis; horizon selection attribution."
        ),
        consumers=("research_engine.evidence.horizon_candidates:horizon_candidate_evidence",),
        join_keys=("canonical_opportunity_id", "observation_id", "entity_id",
                   "correlation_id", "cycle_id"),
        temporal_availability=TemporalAvailability.BEFORE_DECISION,
        lineage_guard_notes=(
            "Produced at decision time (classify_horizons) BEFORE selection; safe "
            "as pre-decision evidence. decision_id may be empty on records emitted "
            "before the decision record is minted — join via "
            "canonical_opportunity_id / entity_id instead."
        ),
    ),
"strategy_candidates": ResearchDisposition(
        dataset="strategy_candidates",
        phase2=Phase2Disposition.A_RESEARCH_INPUT,
        status=ResearchDispositionStatus.DIRECTLY_CONSUMED,
        reason=(
            "Records every strategy candidate evaluated by select_strategy() — the "
            "complete candidate set with rank, selected flag, confidence, reasoning "
            "list and supporting_conditions. Provides counterfactual "
            "rejected-vs-selected strategy evidence absent from decision_trace "
            "(which only persists the winning v10_strategy). Research may observe "
            "and analyse candidates; it never promotes them."
        ),
        research_purpose=(
            "Strategy selection attribution; rejected-vs-selected strategy "
            "comparison; candidate confidence/condition diagnostics; how close the "
            "winner was to alternatives."
        ),
        consumers=("research_engine.evidence.strategy_candidates:strategy_candidate_evidence",),
        join_keys=("canonical_opportunity_id", "observation_id", "correlation_id",
                   "decision_id", "cycle_id"),
        temporal_availability=TemporalAvailability.BEFORE_DECISION,
        lineage_guard_notes=(
            "Some records carry empty correlation_id / cycle_id=0 and null "
            "entity_id (live-observed) — canonical_opportunity_id is the reliable "
            "join key. Cannot automatically promote candidates."
        ),
    ),
    "execution_attempts": ResearchDisposition(
        dataset="execution_attempts",
        phase2=Phase2Disposition.B_SUPPORTING_DIAGNOSTIC,
        status=ResearchDispositionStatus.SUPPORTING_CONSUMED,
        reason=(
            "One record per individual broker interaction (entry/order_send, "
            "SLTP_MODIFY, CLOSE), preserving retries/requotes/rejections that are "
            "lost in execution_results (which is one aggregate record per "
            "orchestrator call). Unique evidence: retry_reason, attempt_number, "
            "broker_result.retcode/comment, bid/ask/spread at attempt, slippage, "
            "broker_confirmed sl/tp/protection_status."
        ),
        research_purpose=(
            "Execution-quality diagnostics: retry/rejection/friction attribution, "
            "broker reliability, slippage and spread-at-attempt analysis, "
            "execution failure attribution. The research grain is one row per "
            "attempt and one trade may have MANY attempts — attempts must never be "
            "treated as separate trade outcomes."
        ),
        consumers=("research_engine.evidence.execution_attempts:execution_attempt_evidence",),
        join_keys=("correlation_id", "canonical_opportunity_id", "decision_id",
                   "trade_id", "broker_result.deal"),
        temporal_availability=TemporalAvailability.AFTER_DECISION_BEFORE_OUTCOME,
        lineage_guard_notes=(
            "trade_id is null for ENTRY attempts (minted downstream by position "
            "registration) and may be null on management-retry SLTP/CLOSE attempts; "
            "join ENTRY attempts to the realised trade via broker_result.deal == "
            "trade_truth position ticket, or via correlation_id when present."
        ),
    ),
    "management_actions": ResearchDisposition(
        dataset="management_actions",
        phase2=Phase2Disposition.B_SUPPORTING_DIAGNOSTIC,
        status=ResearchDispositionStatus.SUPPORTING_CONSUMED,
        reason=(
            "Persists every trade-management action at the moment the management "
            "layer INITIATES it (before the broker call): SLTP_MODIFY, "
            "PARTIAL_CLOSE, CLOSE, with action_reason (e.g. take_profit, "
            "SLTP_RETRY, PARTIAL_TP, trailing/breakeven reasons) and requested sl/"
            "tp/volume. Records management intervention that is NOT visible in "
            "trade_truth's single exit.exit_reason and NOT duplicated by "
            "execution_attempts (management actions exist even when the broker "
            "rejects the call)."
        ),
        research_purpose=(
            "Management-effectiveness research: did SLTP_MODIFY / PARTIAL_CLOSE "
            "interventions improve or harm outcomes? Distinguishes automatic close "
            "reason (trade_truth exit_reason) from management intervention "
            "(action_type+action_reason); per-trade management intensity."
        ),
        consumers=("research_engine.evidence.management_actions:management_actions_evidence",),
        join_keys=("trade_id", "correlation_id", "canonical_opportunity_id",
                   "decision_id", "observation_id", "cycle_id"),
        temporal_availability=TemporalAvailability.AFTER_DECISION_BEFORE_OUTCOME,
        lineage_guard_notes=(
            "Full lineage live-verified. CLOSE action_reason describes the close "
            "intent (e.g. take_profit) — outcome effects must be analysed at the "
            "trade grain via trade_truth / trade_journal, never as pre-decision "
            "features."
        ),
    ),
"position_excursion": ResearchDisposition(
        dataset="position_excursion",
        phase2=Phase2Disposition.E_STATE_NON_EVENT,
        status=ResearchDispositionStatus.RUNTIME_STATE,
        reason=(
            "Durable MUTABLE runtime-state checkpoint (one JSON file per broker "
            "position ticket, OVERWRITE on each excursion-extreme change, latest-"
            "state). Written outside the research-dataset registry under the "
            "registry-external top-level prefix runtime_state/. Its purpose is "
            "restart recovery so final MFE/MAE describe the full trade lifetime. "
            "It is NOT a historical event stream; historical excursion progression "
            "is not preserved; consuming current state would introduce "
            "survivorship/look-ahead bias (open positions present, closed "
            "positions' latest extremes overwritten)."
        ),
        research_purpose=(
            "Runtime recovery telemetry only. The research-worthy final MFE/MAE "
            "evidence is canonicalised into trade_truth outcome "
            "(max_favourable_price / max_adverse_price / mfe_r / mae_r / "
            "excursion_provenance) and trade_journal — live-verified."
        ),
        consumers=(),
        join_keys=("position_ticket", "trade_id", "correlation_id",
                   "canonical_opportunity_id"),
        temporal_availability=TemporalAvailability.LIFECYCLE,
        authoritative_alternative="trade_truth",
        lineage_guard_notes=(
            "Sanctioned S3 reader cannot read it (objects are ticket=*.json, and "
            "the dataset is deliberately not in the production contract registry). "
            "Final excursion evidence is authoritative in trade_truth outcome."
        ),
    ),
    # ────────────────────────────────────────────────────────────────────────────
    # REMAINING ACTIVE CONTRACT DATASETS — explicit dispositions (all 23)
    # ────────────────────────────────────────────────────────────────────────────
    "market_context": ResearchDisposition(
        dataset="market_context",
        phase2=Phase2Disposition.A_RESEARCH_INPUT,
        status=ResearchDispositionStatus.DIRECTLY_CONSUMED,
        reason="Canonical market-state environment (regime/phase/H4/H1/M15/M5 summaries) feeding the MARKET universe.",
        research_purpose="Regime/phase/market-state expectancy; market-quality gating attribution.",
        consumers=("research_engine.v10.universes.market_universe:MarketUniverseBuilder",
                   "research_engine.data_access.loaders:load_market_context"),
        join_keys=("symbol", "cycle_id", "timestamp_utc", "canonical_opportunity_id", "entity_id"),
        temporal_availability=TemporalAvailability.BEFORE_DECISION,
        lineage_guard_notes="Snapshot at observation/decision time; pre-decision-safe.",
    ),
    "opportunities": ResearchDisposition(
        dataset="opportunities",
        phase2=Phase2Disposition.A_RESEARCH_INPUT,
        status=ResearchDispositionStatus.DIRECTLY_CONSUMED,
        reason="Canonical opportunity lifecycle records (what appeared, state, rejection_stage, pattern, scores) — the DECISION universe base population.",
        research_purpose="Opportunity quality / rejection-stage analysis; missed-opportunity counterfactuals.",
        consumers=("research_engine.data_access.loaders:load_opportunities",
                   "research_engine.v10.universes.decision_universe"),
        join_keys=("opportunity_id", "canonical_opportunity_id", "entity_id", "cycle_id"),
        temporal_availability=TemporalAvailability.BEFORE_DECISION,
        lineage_guard_notes="Opportunity truth is articulated BEFORE the decision; always pre-decision-safe.",
    ),
    "assessments": ResearchDisposition(
        dataset="assessments",
        phase2=Phase2Disposition.A_RESEARCH_INPUT,
        status=ResearchDispositionStatus.DIRECTLY_CONSUMED,
        reason="Opportunity evaluation records (score_neutral/score_strategy, ev, p_success, rr_effective) — assessments of how good the opportunity was.",
        research_purpose="EV calibration; score-to-outcome link; assessment quality.",
        consumers=("research_engine.data_access.loaders:load_assessments",),
        join_keys=("assessment_id", "opportunity_id", "canonical_opportunity_id",
                   "entity_id", "cycle_id"),
        temporal_availability=TemporalAvailability.BEFORE_DECISION,
        lineage_guard_notes="Pre-decision warning: assessments must never embed future outcome fields (guarded by architecture tests).",
    ),
    "decision_ledger": ResearchDisposition(
        dataset="decision_ledger",
        phase2=Phase2Disposition.A_RESEARCH_INPUT,
        status=ResearchDispositionStatus.DIRECTLY_CONSUMED,
        reason="Authoritative terminal decision record (decision, reason, signal_score, regime, execution_intent).",
        research_purpose="Decision-quality research; terminal decision attribution; execution-intent completeness.",
        consumers=("research_engine.data_access.loaders:load_decision_ledger",
                   "core.causal.replay"),
        join_keys=("cycle_id", "correlation_id", "entity_id", "decision_id"),
        temporal_availability=TemporalAvailability.BEFORE_DECISION,
        lineage_guard_notes="Terminal decision is written at decision time (before execution); pre-decision-safe.",
    ),
    "execution_results": ResearchDisposition(
        dataset="execution_results",
        phase2=Phase2Disposition.A_RESEARCH_INPUT,
        status=ResearchDispositionStatus.DIRECTLY_CONSUMED,
        reason="Per-orchestrator-call execution result (broker response, fill, slippage) — used by ExecutionUniverseBuilder for entity_id enrichment and execution research.",
        research_purpose="Execution-outcome linkage to decisions; execution attribution; entity_id correlation spine.",
        consumers=("research_engine.v10.universes.execution_universe:ExecutionUniverseBuilder",
                   "research_engine.data_access.loaders:load_execution_results"),
        join_keys=("correlation_id", "decision_id", "entity_id", "trade_id"),
        temporal_availability=TemporalAvailability.AFTER_DECISION_BEFORE_OUTCOME,
        lineage_guard_notes="Execution outcome known after decision; never a pre-decision feature.",
    ),
    "trade_truth": ResearchDisposition(
        dataset="trade_truth",
        phase2=Phase2Disposition.A_RESEARCH_INPUT,
        status=ResearchDispositionStatus.DIRECTLY_CONSUMED,
        reason="Authoritative realised-outcome dataset (identity/execution/timestamps/outcome incl. mfe_r/mae_r/excursion_provenance/exit) — the EXECUTION/OUTCOME universe population.",
        research_purpose="Realised expectancy; exit-reason analysis; MFE/MAE outcome labels; full trade outcome research.",
        consumers=("research_engine.v10.universes.execution_universe:ExecutionUniverseBuilder",
                   "research_engine.data_access.loaders:load_trade_truth",
                   "research_engine.main:load_trade_truth"),
        join_keys=("identity.trade_id", "identity.correlation_id",
                   "identity.canonical_opportunity_id", "identity.symbol"),
        temporal_availability=TemporalAvailability.AFTER_OUTCOME,
        lineage_guard_notes="AFTER_OUTCOME: may only be used as outcome/label evidence, never as a pre-decision explanatory feature.",
    ),
"decision_trace": ResearchDisposition(
        dataset="decision_trace",
        phase2=Phase2Disposition.A_RESEARCH_INPUT,
        status=ResearchDispositionStatus.DIRECTLY_CONSUMED,
        reason="Detailed decision diagnostics (action, terminal_stage/reason, v10_* sub-objects) — primary DECISION + MARKET + STRATEGY universe source.",
        research_purpose="Decision quality / rejection-stage analysis; opportunity-quality prediction; risk-gate effectiveness; strategy attribution.",
        consumers=("research_engine.v10.universes.decision_universe:DecisionUniverseBuilder",
                   "research_engine.v10.universes.strategy_universe:StrategyUniverseBuilder",
                   "research_engine.data_access.loaders:load_decision_trace"),
        join_keys=("entity_id", "correlation_id", "cycle_id", "decision_id", "observation_id"),
        temporal_availability=TemporalAvailability.BEFORE_DECISION,
        lineage_guard_notes="Snapshot is a projection of the canonical decision at decision time; pre-decision-safe as decision evidence.",
    ),
    "execution_context": ResearchDisposition(
        dataset="execution_context",
        phase2=Phase2Disposition.A_RESEARCH_INPUT,
        status=ResearchDispositionStatus.DIRECTLY_CONSUMED,
        reason="Pre-trade execution environment snapshot (market access, spread, latency, risk environment).",
        research_purpose="Execution-environment expectancy; spread/latency impact; execution-quality context.",
        consumers=("research_engine.data_access.loaders:load_execution_context",),
        join_keys=("correlation_id", "symbol", "timestamp_utc"),
        temporal_availability=TemporalAvailability.AFTER_DECISION_BEFORE_OUTCOME,
        lineage_guard_notes="Built at execution intent time (after decision); execution-side feature only.",
    ),
    "protection_audit": ResearchDisposition(
        dataset="protection_audit",
        phase2=Phase2Disposition.B_SUPPORTING_DIAGNOSTIC,
        status=ResearchDispositionStatus.SUPPORTING_CONSUMED,
        reason="Post-fill SL/TP verification records — execution-protection quality evidence, not a primary statistical population.",
        research_purpose="Protection-verification diagnostics; broker SL/TP confirmation reliability; anomaly attribution.",
        consumers=("research_engine.data_access.loaders:load_protection_audit",),
        join_keys=("position_ticket", "correlation_id", "trade_id"),
        temporal_availability=TemporalAvailability.AFTER_DECISION_BEFORE_OUTCOME,
        lineage_guard_notes="Post-fill verification; diagnostics-only evidence.",
    ),
    "risk_deviation": ResearchDisposition(
        dataset="risk_deviation",
        phase2=Phase2Disposition.B_SUPPORTING_DIAGNOSTIC,
        status=ResearchDispositionStatus.SUPPORTING_CONSUMED,
        reason="Planned-vs-actual risk measurement — risk-execution quality evidence, supporting/diagnostic.",
        research_purpose="Risk-control accuracy; planned-risk vs realised-risk deviation attribution.",
        consumers=("research_engine.data_access.loaders:load_risk_deviation",),
        join_keys=("trade_id", "correlation_id", "symbol"),
        temporal_availability=TemporalAvailability.AFTER_OUTCOME,
        lineage_guard_notes="Deviation is computed after the trade is realised; AFTER_OUTCOME evidence.",
    ),
    "portfolio_rankings": ResearchDisposition(
        dataset="portfolio_rankings",
        phase2=Phase2Disposition.B_SUPPORTING_DIAGNOSTIC,
        status=ResearchDispositionStatus.SUPPORTING_CONSUMED,
        reason="Cross-symbol opportunity ranking records (portfolio-scoped, date-partitioned).",
        research_purpose="Portfolio-ranking quality; selected-vs-outranked opportunity analysis.",
        consumers=("research_engine.data_access.loaders:load_portfolio_rankings",),
        join_keys=("ranking_id", "cycle_id", "canonical_opportunity_id", "symbol"),
        temporal_availability=TemporalAvailability.BEFORE_DECISION,
        lineage_guard_notes="Portfolio-rank ordering is computed pre-decision.",
    ),
    "shadow_runtime": ResearchDisposition(
        dataset="shadow_runtime",
        phase2=Phase2Disposition.A_RESEARCH_INPUT,
        status=ResearchDispositionStatus.DIRECTLY_CONSUMED,
        reason="Canonical shadow event stream — the AUTHORITATIVE production shadow source (nshadow_* lifecycles) ingested by the sanctioned shadow ingestion layer.",
        research_purpose="Shadow prediction-vs-live-outcome validation (Q16); counterfactual shadow research.",
        consumers=("research_engine.data_access.shadow_runtime_ingestion:ingest_completed_shadow_trades",
                   "research_engine.v10.universes.shadow_reality_universe"),
        join_keys=("shadow_trade_id", "canonical_opportunity_id", "observation_id", "plan_id", "entity_id"),
        temporal_availability=TemporalAvailability.AFTER_DECISION_BEFORE_OUTCOME,
        lineage_guard_notes="Shadow lifecycles pair OPEN+CLOSE; OPEN/PROGRESS without CLOSE never become outcomes.",
    ),
    "shadow_trades": ResearchDisposition(
        dataset="shadow_trades",
        phase2=Phase2Disposition.B_SUPPORTING_DIAGNOSTIC,
        status=ResearchDispositionStatus.SUPPORTING_CONSUMED,
        reason="Legacy shadow-trade record shape retained for historical queries; active shadow research now sources from shadow_runtime via shadow_runtime_ingestion.",
        research_purpose="Historical shadow comparison (pre-V1-resolution); legacy loader retained for regression tests/compat.",
        consumers=("research_engine.data_access.loaders:load_shadow_trades",
                   "research_engine.main:load_shadow_trades"),
        join_keys=("shadow_trade_id", "correlation_id", "canonical_opportunity_id", "trade_id"),
        temporal_availability=TemporalAvailability.AFTER_DECISION_BEFORE_OUTCOME,
        lineage_guard_notes="Legacy shape only; active shadow research must prefer shadow_runtime.",
    ),
"strategy_observations": ResearchDisposition(
        dataset="strategy_observations",
        phase2=Phase2Disposition.A_RESEARCH_INPUT,
        status=ResearchDispositionStatus.DIRECTLY_CONSUMED,
        reason="Detailed per-observation strategy evaluation records (candidate strategies, conditions, evaluation_status) — secondary STRATEGY universe source.",
        research_purpose="Strategy×regime×pattern expectancy; strategy-condition effectiveness; strategy evaluation quality.",
        consumers=("research_engine.v10.universes.strategy_universe:StrategyUniverseBuilder",
                   "research_engine.data_access.loaders"),
        join_keys=("observation_id", "entity_id", "cycle_id", "symbol"),
        temporal_availability=TemporalAvailability.BEFORE_DECISION,
        lineage_guard_notes="Observation-time projection; pre-decision-safe.",
    ),
    "research_shadow_trades": ResearchDisposition(
        dataset="research_shadow_trades",
        phase2=Phase2Disposition.D_REDUNDANT_DERIVABLE,
        status=ResearchDispositionStatus.RESEARCH_OUTPUT,
        reason="Research assessment output (shadow-trade research shape) — a derived research artifact of the research assessment engine.",
        research_purpose="Rebuildable research-working copy; the canonical shadow source is shadow_runtime.",
        consumers=("core.research_assessment.research_shadow_engine",),
        join_keys=("shadow_trade_id", "correlation_id", "canonical_opportunity_id"),
        temporal_availability=TemporalAvailability.AFTER_DECISION_BEFORE_OUTCOME,
        authoritative_alternative="shadow_runtime",
        lineage_guard_notes="Derived/rebuildable; treat as artifact, not new evidence.",
    ),
    "trade_journal": ResearchDisposition(
        dataset="trade_journal",
        phase2=Phase2Disposition.A_RESEARCH_INPUT,
        status=ResearchDispositionStatus.DIRECTLY_CONSUMED,
        reason="Realised trade lifecycle projection (entry/exit/PnL, close_reason, initial sl/tp, MFE/MAE, horizon) — the horizon-research primary source.",
        research_purpose="Horizon performance research; realised-trade lifecycle analysis; outcome projection.",
        consumers=("research_engine.horizon_research",
                   "research_engine.data_access.loaders:load_trade_journal"),
        join_keys=("trade_id", "position_ticket", "correlation_id",
                   "canonical_opportunity_id", "trade_horizon"),
        temporal_availability=TemporalAvailability.AFTER_OUTCOME,
        lineage_guard_notes="Projection of realised outcomes; AFTER_OUTCOME labels only.",
    ),
    "portfolio_shadow": ResearchDisposition(
        dataset="portfolio_shadow",
        phase2=Phase2Disposition.A_RESEARCH_INPUT,
        status=ResearchDispositionStatus.DIRECTLY_CONSUMED,
        reason="Portfolio-wide shadow comparison records (ranking vs actual execution scenario).",
        research_purpose="Portfolio-ranking shadow validation; selected-vs-shadow outcome comparison.",
        consumers=("research_engine.data_access.loaders:load_shadow_comparisons",),
        join_keys=("ranking_id", "cycle_id", "symbol", "canonical_opportunity_id"),
        temporal_availability=TemporalAvailability.AFTER_DECISION_BEFORE_OUTCOME,
        lineage_guard_notes="Shadow scenario evidence (counterfactual), never pre-decision.",
    ),
    "quarantine": ResearchDisposition(
        dataset="quarantine",
        phase2=Phase2Disposition.C_OPERATIONAL_ONLY,
        status=ResearchDispositionStatus.INTENTIONALLY_OPERATIONAL,
        reason=(
            "Contract-validation projection holding records REJECTED by canonical "
            "V1 validation. These are, by design, NOT part of any research "
            "population — including them would contaminate statistical evidence. "
            "Their existence is an operational correctness signal."
        ),
        research_purpose=(
            "Operational validation hygiene; auditing ingestion defects. Excluded "
            "from all research populations."
        ),
        consumers=(),
        join_keys=(),
        temporal_availability=TemporalAvailability.LIFECYCLE,
        lineage_guard_notes="Quarantined records must never enter universe populations (guard: reader is contract-only).",
    ),
}
# ═══════════════════════════════════════════════════════════════════════════════
# LOOKUP API
# ═══════════════════════════════════════════════════════════════════════════════


def dataset_disposition(dataset: str) -> ResearchDisposition | None:
    """Return the explicit research disposition for a dataset (None = unclassified)."""
    return RESEARCH_DISPOSITIONS.get(dataset)


def require_disposition(dataset: str) -> ResearchDisposition:
    disp = RESEARCH_DISPOSITIONS.get(dataset)
    if disp is None:
        raise KeyError(
            f"dataset '{dataset}' has NO Research Engine disposition — add one to "
            f"research_engine.dataset_disposition.RESEARCH_DISPOSITIONS before "
            f"this dataset can be considered by research."
        )
    return disp


def uncovered_active_datasets() -> list[str]:
    """Contract datasets with no explicit research disposition."""
    return sorted(
        ds for ds in PRODUCTION_SCHEMA_REGISTRY if ds not in RESEARCH_DISPOSITIONS
    )


def coverage_report() -> dict[str, Any]:
    """Full machine-readable disposition coverage report."""
    registry = PRODUCTION_SCHEMA_REGISTRY
    covered = {ds: RESEARCH_DISPOSITIONS[ds].to_dict() for ds in registry if ds in RESEARCH_DISPOSITIONS}
    uncovered = uncovered_active_datasets()
    external_known = {
        ds: RESEARCH_DISPOSITIONS[ds].to_dict()
        for ds in RESEARCH_DISPOSITIONS
        if ds not in registry
    }
    return {
        "active_contract_datasets": sorted(registry),
        "covered": len(covered),
        "uncovered": uncovered,
        "registry_external_documented": external_known,
        "dispositions": covered,
    }


def assert_full_coverage() -> None:
    """Coverage guard: every active canonical dataset has an explicit disposition.

    Raises RuntimeError naming any contract dataset missing a disposition so a
    future ingestion gap (new dataset without a research disposition) fails
    loudly instead of silently going unconsumed.
    """
    missing = uncovered_active_datasets()
    if missing:
        raise RuntimeError(
            "Research Engine disposition coverage is INCOMPLETE. The following "
            "active canonical production datasets have NO explicit research "
            "disposition (add them to RESEARCH_DISPOSITIONS): "
            + ", ".join(missing)
        )


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5 — LEAKAGE PROTECTION
# ═══════════════════════════════════════════════════════════════════════════════

# Outcome/label fields that must NEVER be consumed as explanatory features of a
# decision made earlier. Keep in sync with the execution/outcome schemas.
OUTCOME_LABEL_FIELDS: frozenset[str] = frozenset({
    "r_multiple", "r_multiple_realised", "final_r", "pnl_realised", "pnl",
    "net_profit", "net_pnl", "exit_reason", "close_reason", "mfe_r", "mae_r",
    "max_favourable_price", "max_adverse_price", "exit_fill_price",
    "exit_timestamp_broker", "r", "win", "rr_realised",
})

# Datasets that are outcome truth (labels) and therefore AFTER_OUTCOME only.
OUTCOME_AUTHORITATIVE_DATASETS: frozenset[str] = frozenset({
    "trade_truth", "trade_journal", "risk_deviation",
})


def assert_not_outcome_as_decision_feature(*, feature: str, source_dataset: str) -> None:
    """Leakage guard: forbid OUTCOME fields sourced AFTER_OUTCOME as decision features.

    Raises ValueError when a consumer tries to use an outcome/label field as a
    pre-decision explanatory feature (e.g. using trade_truth.r_multiple_realised
    to explain a past decision).
    """
    disp = dataset_disposition(source_dataset)
    # Authoritative outcome datasets are checked FIRST so the message names the
    # strongest guarantee: these datasets are outcome truth by contract.
    if source_dataset in OUTCOME_AUTHORITATIVE_DATASETS and feature in OUTCOME_LABEL_FIELDS:
        raise ValueError(
            f"Outcome leakage: field '{feature}' is an outcome label from "
            f"authoritative dataset '{source_dataset}'."
        )
    if disp and disp.temporal_availability is TemporalAvailability.AFTER_OUTCOME:
        if feature in OUTCOME_LABEL_FIELDS:
            raise ValueError(
                f"Outcome leakage: field '{feature}' sourced from '{source_dataset}' "
                f"is classified AFTER_OUTCOME and may never be used as a "
                f"pre-decision explanatory feature."
            )


def temporal_availability(dataset: str) -> TemporalAvailability:
    """Temporal availability class for a dataset (Phase 5 classification)."""
    return require_disposition(dataset).temporal_availability