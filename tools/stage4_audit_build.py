"""Stage 4 OBSERVATION/DATASET AUDIT -- artifact generator.

READ-ONLY with respect to S3 and to every producer/schema/registry authority.
Emits only new analysis/assurance/stage4_*_20260929.{json,md} files.
No schema mutation. No producer mutation. No backfill. No re-entry trigger.
"""
from __future__ import annotations

import hashlib
import json
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ASSURANCE = os.path.join(ROOT, "analysis", "assurance")
STAMP = "20260929"
SCHEMA = "stage4_observation_dataset_audit_v1"

CERT_FP = "b42b4bfcfa1eaf7df09cac9f88011c89362cf2ca907217e8466fff9fea3adc8b"
ADJ_FP = "4db003c3ab767c72c55803bf01976f913a747c572c3efe62333e861f0b2084cc"
BUCKET = "trading-bot-v10-data"
PROFILE = "trading-bot-new"
ACCOUNT = "179512357189"

PRODUCER = {
    "shadow_runtime": "core/shadow/persistence.py + core/shadow/runtime.py",
    "events": "observation_stream (mt5_data CANDLE writer)",
    "market_context": "core/market_context/persistence.py",
    "decision_trace": "decision_diagnostics producer (supporting/decision_trace)",
    "opportunities": "core/opportunity/persistence.py",
    "assessments": "core/assessment/persistence.py",
    "horizon_candidates": "core/horizon producer",
    "strategy_candidates": "core/strategies producer",
    "strategy_observations": "strategy_observation producer",
    "portfolio_rankings": "core/portfolio_ranking producer",
    "decision_ledger": "decision_authority producer",
    "execution_context": "execution_intent producer",
    "trade_truth": "realized_execution producer",
    "shadow_trades": "core/shadow_trades.py (writer defined, never emitted to S3)",
}


PERSISTED = {
    "shadow_runtime": dict(
        dataset="shadow_runtime", schema="shadow_runtime_v1",
        prefix="supporting/shadow_runtime/schema_version=shadow_runtime_v1/",
        role="SUPPORTING", objects=200, rows=66258,
        date_min="2026-09-07", date_max="2026-09-29", persisted=True,
        coverage={
            "shadow_trade_id": "66258/66258 (25028 nullish)",
            "canonical_opportunity_id": "66258/66258 (top-level)",
            "identity.canonical_opportunity_id": "0/66258",
            "identity.shadow_type": "20460/66258",
            "identity.trade_horizon": "20460/66258",
            "identity.evaluated_horizon": "20460/66258",
            "symbol": "66258/66258",
            "entry_market_time": "20460/66258",
            "entry_market_time_utc_epoch_s": "20460/66258",
            "exit_market_time": "20421/66258",
            "market_timestamp_semantics": "7473/66258",
            "market_timestamp_normalization_version": "7473/66258",
            "broker_offset_seconds": "66258/66258",
            "outcome.pnl_r_multiple": "20421/66258",
            "outcome.mfe_r": "20421/66258",
            "outcome.mae_r": "20421/66258",
            "simulated_outcome.pnl_r_multiple": "0/66258",
            "live_facts.market_phase": "20460/66258 (20460 nullish -> 0 usable)",
            "live_facts.h4_regime": "20460/66258 (4003 nullish)",
            "live_facts.pattern": "20460/66258 (18047 nullish)",
            "live_facts.strategy": "20460/66258 (3841 nullish)",
            "live_facts.v10_selected_horizon": "20460/66258",
            "live_facts.horizon_selection_status": "20460/66258",
            "trade_state_progression": "20421 rows, shape [{bar,close,r}]",
            "experiment_arm": "0/66258",
            "arm_assigned_at": "0/66258",
            "arm_schema_version": "0/66258",
        }),
    "events": dict(
        dataset="events", schema="events_v1",
        prefix="core/events/schema_version=events_v1/",
        role="CORE", objects=48737, rows="15359 CANDLE rows over 249 partitions",
        date_min="2026-09-03", date_max="2026-09-29", persisted=True,
        coverage={
            "type": "CANDLE 15359 / FEED_HEALTH 9 / SYSTEM_HEALTH 19",
            "source:timeframe": "mt5_data:M5 12389; M15 2057; H1 649; H4 183; D1 47; W1 24; MN 10",
            "payload.o/h/l/c/ts": "15359/15359 (100%)",
            "payload.open/high/low/close": "0/15359 (contract names long keys)",
            "ts_utc_ms": "15359/15359",
            "event_layout_version": "1 for all 15387 sampled rows",
            "payload.timestamp_semantics": "76/15359",
            "payload.timestamp_normalization_version": "76/15359",
            "payload.source_broker_offset_seconds": "76/15359",
            "shadow_trade_id": "0/15359",
            "canonical_opportunity_id": "0/15359",
            "trade_horizon": "0/15359",
            "entity_id": "0/15359",
        }),
    "shadow_trades": dict(
        dataset="shadow_trades", schema="shadow_trades_v1",
        prefix="supporting/shadow_trades/  <-- NO SUCH PREFIX",
        role="SUPPORTING (declared)", objects=0, rows=0,
        date_min=None, date_max=None, persisted=False,
        coverage={
            "list_objects_v2 KeyCount supporting/shadow_trades/": "0",
            "list_objects_v2 KeyCount supporting/research_shadow_trades/": "0",
            "declared in core/production_data_contract.py": "yes",
            "writer defined in core/shadow_trades.py": "yes (_persist_to_s3)",
        }),
}


def fingerprint(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, default=str).encode()).hexdigest()


PERSISTED.update({
    "market_context": dict(
        dataset="market_context", schema="market_context_v1",
        prefix="core/market_context/schema_version=market_context_v1/",
        role="CORE", objects=230, rows=1754,
        date_min="2026-09-03", date_max="2026-09-29", persisted=True,
        coverage={
            "regime": "1754/1754", "phase": "1754/1754", "symbol": "1754/1754",
            "bar_time": "1754/1754", "timestamp_utc": "1754/1754",
            "h4.regime": "1754/1754", "h1.direction": "1754/1754",
        }),
    "decision_trace": dict(
        dataset="decision_trace", schema="decision_trace_v1",
        prefix="supporting/decision_trace/schema_version=decision_trace_v1/",
        role="SUPPORTING", objects=200, rows=25516,
        date_min="2026-09-07", date_max="2026-09-29", persisted=True,
        coverage={
            "canonical_opportunity_id": "25516/25516", "symbol": "25516/25516",
            "timestamp_utc": "25516/25516", "schema_version": "25516/25516",
            "v10_entry": "25360/25516 (non-null)",
            "v10_market_state.regime": "25360/25516 (non-null)",
            "v10_market_state.h4.market_phase": "25360/25516 (non-null)",
            "v10_market_state.h1.dominant_trend": "25360/25516 (non-null)",
            "trade_horizon": "25360/25516 (25360 nullish -> 0 usable)",
            "p_success": "25360/25516 (25360 nullish -> 0 usable)",
        }),
    "opportunities": dict(
        dataset="opportunities", schema="opportunities_v1",
        prefix="core/opportunities/schema_version=opportunities_v1/",
        role="CORE", objects=200, rows=100850,
        date_min="2026-09-07", date_max="2026-09-29", persisted=True,
        coverage={
            "canonical_opportunity_id": "100850/100850", "symbol": "100850/100850",
            "opportunity_id": "50521/100850", "state": "50521/100850",
            "overall_score": "50521/100850", "h4_regime": "50521/100850",
            "bias_phase": "50521/100850", "bar_time": "50329/100850",
        }),
    "horizon_candidates": dict(
        dataset="horizon_candidates", schema="horizon_candidates_v1",
        prefix="supporting/horizon_candidates/schema_version=horizon_candidates_v1/",
        role="SUPPORTING", objects=200, rows=76710,
        date_min="2026-09-07", date_max="2026-09-29", persisted=True,
        coverage={
            "canonical_opportunity_id": "76710/76710", "horizon": "76710/76710",
            "selection_status": "76710/76710", "confidence": "76710/76710",
            "symbol": "76710/76710", "bar_time": "76710/76710",
            "eligible": "76710/76710",
        }),
    "strategy_candidates": dict(
        dataset="strategy_candidates", schema="strategy_candidates_v1",
        prefix="supporting/strategy_candidates/schema_version=strategy_candidates_v1/",
        role="SUPPORTING", objects=125, rows=3386,
        date_min="2026-09-07", date_max="2026-09-29", persisted=True,
        coverage={
            "canonical_opportunity_id": "3386/3386", "confidence": "3386/3386",
            "rank": "3386/3386", "selected": "3386/3386", "symbol": "3386/3386",
            "bar_time": "3386/3386",
        }),
    "portfolio_rankings": dict(
        dataset="portfolio_rankings", schema="portfolio_ranking_v1",
        prefix="supporting/portfolio_rankings/schema_version=portfolio_ranking_v1/",
        role="SUPPORTING", objects=20, rows=570,
        date_min="2026-09-07", date_max="2026-09-29", persisted=True,
        coverage={
            "candidates[].selection_status": "present on all 570 rows (list element)",
            "candidates[].rank_position": "present on all 570 rows (list element)",
            "candidates[].symbol": "present on all 570 rows (list element)",
            "candidates[].rank_score": "present on all 570 rows (list element)",
            "ranked_at_utc": "570/570", "cycle_id": "570/570",
            "schema_version": "570/570", "dataset_version": "570/570",
        }),
    "strategy_observations": dict(
        dataset="strategy_observations", schema="strategy_observation_v1",
        prefix="supporting/strategy_observations/schema_version=strategy_observation_v1/",
        role="SUPPORTING", objects=200, rows=25575,
        date_min="2026-09-07", date_max="2026-09-29", persisted=True,
        coverage={
            "canonical_opportunity_id": "25575/25575", "market_phase": "25575/25575",
            "h4_regime": "25575/25575", "strategy_family": "25575/25575",
            "detected_pattern": "25575/25575 (23084 nullish -> 2491 usable)",
            "authority": "25575/25575", "record_role": "25575/25575",
            "bar_time": "25575/25575", "timestamp_utc": "25575/25575",
        }),
    "research_shadow_trades": dict(
        dataset="research_shadow_trades", schema="research_shadow_trades_v1",
        prefix="supporting/research_shadow_trades/  <-- NO SUCH PREFIX",
        role="SUPPORTING (declared)", objects=0, rows=0,
        persisted=False, coverage={"list_objects_v2 KeyCount": "0"}),
})


# â”€â”€ UNIQUE OBSERVATION REQUIREMENTS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Grouped ONLY where semantic grain, timing, identity and lineage are compatible.
OBS = lambda **kw: kw

OBSERVATION_REQUIREMENTS = [
 OBS(id="OR-01", title="Simulated outcome R-multiple at lifecycle grain",
   required_observable="shadow_trades.simulated_outcome.pnl_r_multiple",
   semantic="Realised simulated R-multiple of the canonical shadow lifecycle, written at causal close time.",
   required_grain="one row per (shadow_trade_id, canonical_opportunity_id, trade_horizon)",
   required_identity=["shadow_trade_id", "canonical_opportunity_id", "trade_horizon"],
   required_timestamp="causal close event time + CURRENT epoch attestation",
   required_type="float", required_lineage=["producer:shadow_trades", "canonical_opportunity_id/entity_id lineage"],
   authoritative_dataset="shadow_runtime", authoritative_schema="shadow_runtime_v1",
   authoritative_producer=PRODUCER["shadow_runtime"],
   observed_field="outcome.pnl_r_multiple",
   observed_coverage="20421/66258 (all 20421 CLOSE lifecycles)",
   classifications=["FIELD_EXISTS_WRONG_IDENTITY"],
   primary_classification="FIELD_EXISTS_WRONG_IDENTITY",
   rationale=("The register names dataset `shadow_trades`, which has ZERO persisted S3 objects. "
              "The equivalent observable IS emitted by shadow_runtime_v1 as `outcome.pnl_r_multiple` "
              "on 100% of completed lifecycles. The register's dataset identity does not resolve to "
              "a persisted producer."),
   gaps=["STAGE4-DATA-E5","STAGE4-DATA-D2","STAGE4-DATA-D4","STAGE4-DATA-D5","STAGE4-DATA-M3",
         "STAGE4-DATA-M4","STAGE4-DATA-M5","STAGE4-DATA-M6","STAGE4-DATA-M7","STAGE4-DATA-M8",
         "STAGE4-DATA-M9","STAGE4-DATA-M10","STAGE4-DATA-M11","STAGE4-DATA-S4",
         "STAGE4-DATA-X5","STAGE4-DATA-P1","STAGE4-DATA-L1","STAGE4-DATA-L4",
         "STAGE4-DATA-L5","STAGE4-DATA-STRAT-1","STAGE4-DATA-PORT-1","STAGE4-DATA-HORIZON-1"],
   questions=["E5","D2","D4","D5","M3","M4","M5","M6","M7","M8","M9","M10","M11","S4","X5",
              "P1","L1","L4","L5","STRAT-1","PORT-1","HORIZON-1"],
   disposition="CONTRACT_GOVERNANCE_REQUIRED",
   backfill="HISTORICALLY_BACKFILLABLE_AUTHORITATIVELY",
   version_consequence=["CONTRACT_ONLY_CHANGE"],
   threshold="THRESHOLD_GOVERNANCE_REQUIRED",
   reentry_trigger_class="DATA_THRESHOLD_REACHED"),

 OBS(id="OR-02", title="Market phase bound to the lifecycle decision snapshot",
   required_observable="shadow_trades.decision_snapshot.market_phase",
   semantic="Market phase as evaluated at the causal decision instant for THAT lifecycle.",
   required_grain="one value per (shadow_trade_id, canonical_opportunity_id, trade_horizon)",
   required_identity=["shadow_trade_id", "canonical_opportunity_id", "trade_horizon"],
   required_timestamp="causal decision time (pre-entry) + CURRENT epoch",
   required_type="str", required_lineage=["producer:shadow_trades"],
   authoritative_dataset="shadow_runtime", authoritative_schema="shadow_runtime_v1",
   authoritative_producer=PRODUCER["shadow_runtime"],
   observed_field="live_facts.market_phase (shadow_runtime); phase (market_context_v1); "
                  "v10_market_state.h4.market_phase (decision_trace_v1); market_phase (strategy_observation_v1)",
   observed_coverage=("shadow_runtime 20460/66258 present but 20460 NULLISH -> 0 usable; "
                      "market_context_v1 1754/1754 non-null; decision_trace_v1 25360/25516 non-null; "
                      "strategy_observation_v1 25575/25575 non-null"),
   classifications=["FIELD_EXISTS_WRONG_GRAIN", "FIELD_EXISTS_NO_LINEAGE"],
   primary_classification="FIELD_EXISTS_WRONG_GRAIN",
   rationale=("market_phase IS persisted at 100% in market_context_v1, decision_trace_v1 and "
              "strategy_observation_v1, but at market/observation grain. Inside the lifecycle record "
              "the field is emitted yet 100% NULL, so no lifecycle-bound phase survives. "
              "This is a grain-binding failure, not an absence of the observable."),
   gaps=["STAGE4-DATA-M3","STAGE4-DATA-M4","STAGE4-DATA-M5","STAGE4-DATA-M6","STAGE4-DATA-M7",
         "STAGE4-DATA-M8","STAGE4-DATA-M9","STAGE4-DATA-M10","STAGE4-DATA-L1","STAGE4-DATA-L4"],
   questions=["M3","M4","M5","M6","M7","M8","M9","M10","L1","L4"],
   disposition="SCHEMA_CHANGE_REQUIRED",
   backfill="HISTORICALLY_BACKFILLABLE_AUTHORITATIVELY",
   version_consequence=["SCHEMA_VERSION_REQUIRED", "PRODUCER_VERSION_REQUIRED"],
   threshold="THRESHOLD_GOVERNANCE_REQUIRED",
   reentry_trigger_class="SCHEMA_COLLECTION_RECOVERED"),

 OBS(id="OR-03", title="H4 regime bound to the lifecycle decision snapshot",
   required_observable="shadow_trades.decision_snapshot.h4_regime",
   semantic="H4 regime classification evaluated at the causal decision instant for that lifecycle.",
   required_grain="one value per lifecycle",
   required_identity=["shadow_trade_id", "canonical_opportunity_id", "trade_horizon"],
   required_timestamp="causal decision time + CURRENT epoch",
   required_type="str", required_lineage=["producer:shadow_trades"],
   authoritative_dataset="shadow_runtime", authoritative_schema="shadow_runtime_v1",
   authoritative_producer=PRODUCER["shadow_runtime"],
   observed_field="live_facts.h4_regime; h4.regime (market_context_v1); v10_market_state.regime (decision_trace_v1)",
   observed_coverage=("shadow_runtime 20460/66258 present, 4003 nullish -> 16457 usable; "
                      "market_context_v1 h4.regime 1754/1754; decision_trace_v1 25360/25516"),
   classifications=["FIELD_EXISTS_PARTIAL_COVERAGE"],
   primary_classification="FIELD_EXISTS_PARTIAL_COVERAGE",
   rationale="The field exists at lifecycle grain but is NULL in 4003 of 20460 OPEN lifecycles; the 80.4% residual is not 100% as the contract requires.",
   gaps=["STAGE4-DATA-M3","STAGE4-DATA-M7","STAGE4-DATA-M11"],
   questions=["M3","M7","M11"],
   disposition="PRODUCER_CHANGE_REQUIRED",
   backfill="HISTORICALLY_BACKFILLABLE_AUTHORITATIVELY",
   version_consequence=["PRODUCER_VERSION_REQUIRED"],
   threshold="THRESHOLD_GOVERNANCE_REQUIRED",
   reentry_trigger_class="DATA_THRESHOLD_REACHED"),

 OBS(id="OR-04", title="Detected pattern bound to the lifecycle decision snapshot",
   required_observable="shadow_trades.decision_snapshot.pattern",
   semantic="Pattern label evaluated at the causal decision instant for that lifecycle.",
   required_grain="one value per lifecycle", required_identity=["shadow_trade_id", "canonical_opportunity_id"],
   required_timestamp="causal decision time + CURRENT epoch", required_type="str",
   required_lineage=["producer:shadow_trades"],
   authoritative_dataset="shadow_runtime", authoritative_schema="shadow_runtime_v1",
   authoritative_producer=PRODUCER["shadow_runtime"],
   observed_field="live_facts.pattern; detected_pattern (strategy_observation_v1)",
   observed_coverage="shadow_runtime 20460/66258 present, 18047 nullish -> 2413 usable (11.8%)",
   classifications=["FIELD_EXISTS_PARTIAL_COVERAGE"],
   primary_classification="FIELD_EXISTS_PARTIAL_COVERAGE",
   rationale="Pattern exists at lifecycle grain but 88.2% of OPEN lifecycles carry NULL.",
   gaps=["STAGE4-DATA-M9","STAGE4-DATA-M10","STAGE4-DATA-M11","STAGE4-DATA-S4","STAGE4-DATA-P1"],
   questions=["M9","M10","M11","S4","P1"], disposition="PRODUCER_CHANGE_REQUIRED",
   backfill="HISTORICALLY_BACKFILLABLE_AUTHORITATIVELY",
   version_consequence=["PRODUCER_VERSION_REQUIRED"],
   threshold="THRESHOLD_GOVERNANCE_REQUIRED", reentry_trigger_class="DATA_THRESHOLD_REACHED"),

 OBS(id="OR-05", title="Strategy selection bound to the lifecycle decision snapshot",
   required_observable="shadow_trades.decision_snapshot.strategy",
   semantic="Strategy identifier selected at the causal decision instant for that lifecycle.",
   required_grain="one value per lifecycle", required_identity=["shadow_trade_id", "canonical_opportunity_id"],
   required_timestamp="causal decision time + CURRENT epoch", required_type="str",
   required_lineage=["producer:shadow_trades"],
   authoritative_dataset="shadow_runtime", authoritative_schema="shadow_runtime_v1",
   authoritative_producer=PRODUCER["shadow_runtime"],
   observed_field="live_facts.strategy; strategy_family (strategy_observation_v1)",
   observed_coverage="shadow_runtime 20460/66258 present, 3841 nullish -> 16619 usable (81.2%)",
   classifications=["FIELD_EXISTS_PARTIAL_COVERAGE"],
   primary_classification="FIELD_EXISTS_PARTIAL_COVERAGE",
   rationale="Field exists at the right grain but is NULL in 18.8% of OPEN lifecycles.",
   gaps=["STAGE4-DATA-M4","STAGE4-DATA-L1","STAGE4-DATA-L5"],
   questions=["M4","L1","L5"], disposition="PRODUCER_CHANGE_REQUIRED",
   backfill="HISTORICALLY_BACKFILLABLE_AUTHORITATIVELY",
   version_consequence=["PRODUCER_VERSION_REQUIRED"],
   threshold="THRESHOLD_GOVERNANCE_REQUIRED", reentry_trigger_class="DATA_THRESHOLD_REACHED"),

 OBS(id="OR-06", title="Nested identity block with canonical_opportunity_id",
   required_observable=("shadow_trades.identity.canonical_opportunity_id | identity.shadow_type | "
                        "identity.evaluated_horizon | identity.symbol"),
   semantic="Canonical identity triple carried INSIDE the identity block of the lifecycle record.",
   required_grain="one per lifecycle", required_identity=["shadow_trade_id","canonical_opportunity_id","trade_horizon"],
   required_timestamp="recorded_at_utc", required_type="str",
   required_lineage=["canonical_opportunity_id/entity lineage"],
   authoritative_dataset="shadow_runtime", authoritative_schema="shadow_runtime_v1",
   authoritative_producer=PRODUCER["shadow_runtime"],
   observed_field="identity.{shadow_type,trade_horizon,evaluated_horizon,entity_id,cycle_id}",
   observed_coverage=("identity.canonical_opportunity_id 0/66258; identity.shadow_type 20460/66258; "
                      "top-level canonical_opportunity_id 66258/66258; top-level symbol 66258/66258"),
   classifications=["FIELD_EXISTS_WRONG_GRAIN"],
   primary_classification="FIELD_EXISTS_WRONG_GRAIN",
   rationale=("canonical_opportunity_id and symbol ARE persisted at 100% but at TOP level, not inside "
              "the `identity` block the contract names; the nested variants are 0/66258. The governed "
              "join reads top-level first and tolerates both, so the observable is materially satisfied."),
   gaps=["STAGE4-DATA-S4","STAGE4-DATA-L1","STAGE4-DATA-L4","STAGE4-DATA-L5",
         "STAGE4-DATA-R3","STAGE4-DATA-R4","STAGE4-DATA-R5","STAGE4-DATA-HORIZON-1",
         "STAGE4-DATA-L2","STAGE4-DATA-EX10"],
   questions=["S4","L1","L4","L5","R3","R4","R5","HORIZON-1","L2","EX10"],
   disposition="CONTRACT_GOVERNANCE_REQUIRED",
   backfill="HISTORICALLY_BACKFILLABLE_AUTHORITATIVELY",
   version_consequence=["CONTRACT_ONLY_CHANGE"],
   threshold="THRESHOLD_GOVERNANCE_REQUIRED", reentry_trigger_class="DATA_THRESHOLD_REACHED"),

 OBS(id="OR-07", title="Lifecycle entry market time in canonical UTC",
   required_observable=("OPEN.entry_market_time | OPEN.entry_market_time_utc_epoch_s | "
                        "OPEN.market_timestamp_semantics | OPEN.market_timestamp_normalization_version"),
   semantic="Entry instant in canonical UTC with its declared timestamp semantics and normalization version.",
   required_grain="one per lifecycle OPEN", required_identity=["shadow_trade_id","canonical_opportunity_id"],
   required_timestamp="entry_market_time + semantics attestation",
   required_type="int epoch_s + str semantics",
   required_lineage=["shadow_timestamp_normalization.normalize_post_candle_utc_lifecycle"],
   authoritative_dataset="shadow_runtime", authoritative_schema="shadow_runtime_v1",
   authoritative_producer=PRODUCER["shadow_runtime"],
   observed_field=("entry_market_time, entry_market_time_utc_epoch_s, "
                   "market_timestamp_semantics, market_timestamp_normalization_version"),
   observed_coverage=("entry_market_time 20460/66258; market_timestamp_semantics 7473/66258 (11.3%); "
                      "market_timestamp_normalization_version 7473/66258"),
   classifications=["FIELD_EXISTS_PARTIAL_COVERAGE"],
   primary_classification="FIELD_EXISTS_PARTIAL_COVERAGE",
   rationale=("The epoch fields exist on every OPEN lifecycle, but the two attestation fields the "
              "contract requires are present on only 7473 of 66258 rows (11.3%). "
              "shadow_timestamp_normalization.reject() fails closed without them, so 88.7% of OPEN rows "
              "cannot be timestamp-authoritatively interpreted."),
   gaps=["STAGE4-DATA-L1","STAGE4-DATA-L4","STAGE4-DATA-R4","STAGE4-DATA-R5",
         "STAGE4-DATA-L2","STAGE4-DATA-EX10","STAGE4-DATA-L5"],
   questions=["L1","L4","R4","R5","L2","EX10","L5"], disposition="SCHEMA_CHANGE_REQUIRED",
   backfill="HISTORICALLY_BACKFILLABLE_AUTHORITATIVELY",
   version_consequence=["SCHEMA_VERSION_REQUIRED","PRODUCER_VERSION_REQUIRED"],
   threshold="THRESHOLD_GOVERNANCE_REQUIRED", reentry_trigger_class="SCHEMA_COLLECTION_RECOVERED"),

 OBS(id="OR-08", title="Predicted success probability p_success",
   required_observable="decision_trace.p_success",
   semantic="Producer-issued predicted success probability attached to the decision.",
   required_grain="one per decision_trace row", required_identity=["canonical_opportunity_id"],
   required_timestamp="timestamp_utc", required_type="float", required_lineage=["producer:decision_trace"],
   authoritative_dataset="decision_trace", authoritative_schema="decision_trace_v1",
   authoritative_producer=PRODUCER["decision_trace"],
   observed_field="p_success", observed_coverage="25360/25516 present but 25360 NULLISH -> 0 usable",
   classifications=["FIELD_EXISTS_PARTIAL_COVERAGE"],
   primary_classification="FIELD_EXISTS_PARTIAL_COVERAGE",
   rationale="The key is emitted on 99.4% of rows but is NULL on every one of them. Zero usable values.",
   gaps=["STAGE4-DATA-D3","STAGE4-DATA-X5"], questions=["D3","X5"],
   disposition="PRODUCER_CHANGE_REQUIRED",
   backfill="FUTURE_COLLECTION_ONLY",
   version_consequence=["PRODUCER_VERSION_REQUIRED"],
   threshold="THRESHOLD_GOVERNANCE_REQUIRED", reentry_trigger_class="DATA_THRESHOLD_REACHED"),

 OBS(id="OR-09", title="v10 entry plan",
   required_observable="decision_trace.v10_entry",
   semantic="Producer-issued entry plan (direction, entry/stop/target, risk distance) at decision time.",
   required_grain="one per decision_trace row", required_identity=["canonical_opportunity_id"],
   required_timestamp="timestamp_utc", required_type="dict",
   required_lineage=["producer:decision_trace"],
   authoritative_dataset="decision_trace", authoritative_schema="decision_trace_v1",
   authoritative_producer=PRODUCER["decision_trace"],
   observed_field="v10_entry{direction,entry_price,expected_rr,method,reward_distance,"
                  "risk_distance,status,stop_price,target_price}",
   observed_coverage="25360/25516 (99.4%), no nullish",
   classifications=["SATISFIED_CURRENTLY"],
   primary_classification="SATISFIED_CURRENTLY",
   rationale="v10_entry is emitted, non-null, on 25360 of 25516 decision_trace rows. The observable already satisfies its contract.",
   gaps=["STAGE4-DATA-D3","STAGE4-DATA-X5"], questions=["D3","X5"],
   disposition="SATISFIED_ALREADY", backfill="NOT_APPLICABLE",
   version_consequence=["NO_SCHEMA_CHANGE"],
   threshold="THRESHOLD_GOVERNANCE_REQUIRED", reentry_trigger_class="DEPENDENCY_RESOLVED"),

 OBS(id="OR-10", title="Horizon candidate selection state",
   required_observable="horizon_candidates.canonical_opportunity_id | horizon | selection_status",
   semantic="Per-opportunity horizon candidate with its producer-issued selection status.",
   required_grain="one per (canonical_opportunity_id, horizon)",
   required_identity=["canonical_opportunity_id", "horizon"],
   required_timestamp="bar_time / evaluated_at_utc", required_type="str",
   required_lineage=["canonical_opportunity_id"],
   authoritative_dataset="horizon_candidates", authoritative_schema="horizon_candidates_v1",
   authoritative_producer=PRODUCER["horizon_candidates"],
   observed_field="canonical_opportunity_id, horizon, selection_status, confidence, eligible",
   observed_coverage="76710/76710 (100%), no nullish",
   classifications=["SATISFIED_CURRENTLY"],
   primary_classification="SATISFIED_CURRENTLY",
   rationale=("All three required fields are present and non-null on 100% of 76710 rows across 20 date "
              "partitions. HORIZON-1's registered observable is fully satisfied as persisted."),
   gaps=["STAGE4-DATA-HORIZON-1"], questions=["HORIZON-1"],
   disposition="SATISFIED_ALREADY", backfill="NOT_APPLICABLE",
   version_consequence=["NO_SCHEMA_CHANGE"],
   threshold="THRESHOLD_GOVERNANCE_REQUIRED", reentry_trigger_class="DEPENDENCY_RESOLVED"),

 OBS(id="OR-11", title="Strategy candidate confidence, rank and selection",
   required_observable="strategy_candidates.confidence | rank | selected | canonical_opportunity_id",
   semantic="Producer-issued strategy candidate with confidence, rank and selection flag.",
   required_grain="one per (canonical_opportunity_id, strategy)",
   required_identity=["canonical_opportunity_id"], required_timestamp="bar_time",
   required_type="float/int/bool", required_lineage=["canonical_opportunity_id"],
   authoritative_dataset="strategy_candidates", authoritative_schema="strategy_candidates_v1",
   authoritative_producer=PRODUCER["strategy_candidates"],
   observed_field="canonical_opportunity_id, confidence, rank, selected, symbol, bar_time",
   observed_coverage="3386/3386 (100%), no nullish",
   classifications=["SATISFIED_CURRENTLY"],
   primary_classification="SATISFIED_CURRENTLY",
   rationale=("All four required fields are present and non-null on 100% of 3386 rows. STRAT-1's registered "
              "observable is fully satisfied; its residual R-multiple half is governed by OR-01."),
   gaps=["STAGE4-DATA-STRAT-1"], questions=["STRAT-1"],
   disposition="SATISFIED_ALREADY", backfill="NOT_APPLICABLE",
   version_consequence=["NO_SCHEMA_CHANGE"],
   threshold="THRESHOLD_GOVERNANCE_REQUIRED", reentry_trigger_class="DEPENDENCY_RESOLVED"),

 OBS(id="OR-12", title="Portfolio candidate selection status and rank position",
   required_observable="portfolio_rankings.candidates.selection_status | candidates.rank_position",
   semantic="Per-candidate selection status and rank position inside the portfolio ranking decision.",
   required_grain="one per ranking candidate inside a cycle ranking",
   required_identity=["cycle_id", "candidates[].opportunity_id", "candidates[].symbol"],
   required_timestamp="ranked_at_utc", required_type="str/int",
   required_lineage=["runtime_session_id", "cycle_id", "ranking_id"],
   authoritative_dataset="portfolio_rankings", authoritative_schema="portfolio_ranking_v1",
   authoritative_producer=PRODUCER["portfolio_rankings"],
   observed_field=("candidates[].selection_status, candidates[].rank_position, candidates[].rank_score, "
                   "candidates[].opportunity_id, candidates[].symbol, candidates[].eligible"),
   observed_coverage=("570/570 ranking rows carry a candidates list; every sampled candidate element "
                      "carries selection_status and rank_position"),
   classifications=["SATISFIED_CURRENTLY"],
   primary_classification="SATISFIED_CURRENTLY",
   rationale=("Both fields are persisted as elements of the `candidates` list on all 570 "
              "portfolio_ranking_v1 rows. The register's dotted path assumed a top-level object; the "
              "real grain is a list element, which is the semantically correct per-candidate grain."),
   gaps=["STAGE4-DATA-PORT-1"], questions=["PORT-1"],
   disposition="SATISFIED_ALREADY", backfill="NOT_APPLICABLE",
   version_consequence=["CONTRACT_ONLY_CHANGE"],
   threshold="THRESHOLD_GOVERNANCE_REQUIRED", reentry_trigger_class="DEPENDENCY_RESOLVED"),

 OBS(id="OR-13", title="Opportunity identity, state and overall score",
   required_observable="opportunities.opportunity_id | state | overall_score",
   semantic="Opportunity lifecycle identity, current state and producer overall score.",
   required_grain="one per opportunity", required_identity=["opportunity_id", "canonical_opportunity_id"],
   required_timestamp="bar_time / detected_at_utc", required_type="str/float",
   required_lineage=["canonical_opportunity_id", "observation_id"],
   authoritative_dataset="opportunities", authoritative_schema="opportunities_v1",
   authoritative_producer=PRODUCER["opportunities"],
   observed_field="opportunity_id, state, overall_score, canonical_opportunity_id, h4_regime, bias_phase",
   observed_coverage=("canonical_opportunity_id 100850/100850 (100%); opportunity_id/state/overall_score "
                      "50521/100850 (50.1%)"),
   classifications=["FIELD_EXISTS_PARTIAL_COVERAGE"],
   primary_classification="FIELD_EXISTS_PARTIAL_COVERAGE",
   rationale=("canonical_opportunity_id is universal, but the state-bearing opportunity record is only "
              "50.1% of rows; the remainder are detection-stage rows with no lifecycle state."),
   gaps=["STAGE4-DATA-OPP-1"], questions=["OPP-1"],
   disposition="PARTIAL_DATASET_CHANGE_REQUIRED",
   backfill="HISTORICALLY_BACKFILLABLE_AUTHORITATIVELY",
   version_consequence=["PRODUCER_VERSION_REQUIRED"],
   threshold="THRESHOLD_GOVERNANCE_REQUIRED", reentry_trigger_class="DATA_THRESHOLD_REACHED"),

 OBS(id="OR-14", title="Ordered M5 OHLC exit path bound to the lifecycle (EX2)",
   required_observable=("events_v1:CANDLE:mt5_data:M5 ordered open/high/low/close between "
                        "entry_market_time and exit_market_time inclusive"),
   semantic=("For one canonical shadow lifecycle, the complete, gap-free, strictly ascending sequence of "
             "M5 OHLC bars covering (entry_market_time, exit_market_time] for the lifecycle symbol. "
             "bar/close/r alone are permanently forbidden as substitutes."),
   required_grain="one record per (shadow_trade_id, canonical_opportunity_id, trade_horizon, M5 bar ts)",
   required_identity=["shadow_trade_id", "canonical_opportunity_id", "trade_horizon"],
   required_timestamp="bar ts in producer market time at causal bar close + CURRENT epoch",
   required_type="ordered_sequence_of_ohlc_bars (ts,open,high,low,close)",
   required_lineage=["producer:shadow_trades", "events_v1 producer instance", "M5 aggregation lineage"],
   authoritative_dataset="shadow_runtime (lifecycle side) + events (M5 bar side)",
   authoritative_schema="shadow_runtime_v1 + events_v1",
   authoritative_producer=PRODUCER["shadow_runtime"],
   observed_field=("events_v1 payload {ts,o,h,l,c} for type=CANDLE, source=mt5_data, timeframe=M5; "
                   "shadow_runtime trade_state_progression [{bar,close,r}]"),
   observed_coverage=("M5 OHLC complete on 12389/12389 sampled CANDLE M5 rows across 23 date partitions "
                      "and 10 symbols; 0/15359 events rows carry any lifecycle identity field; "
                      "trade_state_progression on 20421 rows with shape [{bar,close,r}]"),
   classifications=["FIELD_EXISTS_NO_LINEAGE", "FIELD_EXISTS_WRONG_SEMANTICS"],
   primary_classification="FIELD_EXISTS_NO_LINEAGE",
   rationale=("Authoritative M5 OHLC bars DO exist in events_v1 at 100% OHLC completeness. The defect is "
              "NOT absence of market data; it is absence of lifecycle binding. Zero CANDLE rows carry "
              "shadow_trade_id, canonical_opportunity_id or trade_horizon, so bars can only be attached "
              "post hoc by a (symbol, ts) range join. Secondary: the contract names long payload keys "
              "open/high/low/close while the producer emits short keys o/h/l/c, and payload "
              "timestamp_semantics appears on only 76/15359 rows."),
   gaps=["EX2 (OG-EX2-4db003c3ab76)"], questions=["EX2"],
   disposition="NEW_DATASET_REQUIRED",
   backfill="FUTURE_COLLECTION_ONLY",
   version_consequence=["NEW_DATASET_REQUIRED","SCHEMA_VERSION_REQUIRED","PRODUCER_VERSION_REQUIRED"],
   threshold=("EXACT: 100% of 8760 governed lifecycles must have a complete ordered M5 OHLC path; "
              "the 9045-row widened reconstruction is permanently forbidden"),
   reentry_trigger_class="NEW_EVIDENCE_EPOCH"),

 OBS(id="OR-15", title="Producer-authoritative experiment arm CONTROL/CANDIDATE (L7)",
   required_observable=("shadow_trades.experiment_arm | arm_assigned_at | arm_schema_version, issued "
                        "before any outcome field is populated"),
   semantic=("A single producer-issued, versioned field carrying exactly one of the two governed arms for "
             "the canonical entity under test. shadow_trades_v1 is a dataset schema identity and is NOT "
             "CONTROL and NOT CANDIDATE. No chronology, discovery/validation split, treatment inference, "
             "selection state, promotion state or outcome behaviour may derive the arm."),
   required_grain="one assignment per (shadow_trade_id, canonical_opportunity_id, trade_horizon)",
   required_identity=["shadow_trade_id", "canonical_opportunity_id", "trade_horizon"],
   required_timestamp=("arm_assigned_at written at assignment time, strictly before any outcome field for "
                       "that entity is populated"),
   required_type="closed_enum{CONTROL,CANDIDATE} + arm_schema_version str",
   required_lineage=["producer:shadow_trades",
                     "assignment decision record (issuer, request id, policy version)",
                     "immutability: the arm may never be rewritten or back-filled"],
   authoritative_dataset="shadow_runtime", authoritative_schema="shadow_runtime_v1",
   authoritative_producer=PRODUCER["shadow_runtime"],
   observed_field="experiment_arm / arm_assigned_at / arm_schema_version -- all 0/66258",
   observed_coverage=("0/66258 for experiment_arm, arm_assigned_at and arm_schema_version; no occurrence "
                      "of 'experiment_arm' anywhere in core/shadow/*.py"),
   classifications=["FIELD_MISSING", "FIELD_EXISTS_UNGOVERNED"],
   primary_classification="FIELD_MISSING",
   rationale=("The arm does not exist anywhere in the persisted universe or the producer. Separately the "
              "Stage 4 V2 successor contract overloads `schema_version` as the assignment field "
              "(stage4_registry_successor EVIDENCE_CONTRACT_EXTENSIONS['L7'].assignment_field="
              "'schema_version'), a live SEMANTIC COLLISION: shadow_runtime_v1 emits schema_version="
              "'shadow_runtime_v1' on 66258/66258 rows, so the L7 fail-closed check can never be "
              "satisfied by a real record and would misread any version-shaped token as an arm."),
   gaps=["L7 (OG-L7-4db003c3ab76)"], questions=["L7"],
   disposition="NEW_DATASET_REQUIRED",
   backfill="FUTURE_COLLECTION_ONLY",
   version_consequence=["NEW_DATASET_REQUIRED","SCHEMA_VERSION_REQUIRED","PRODUCER_VERSION_REQUIRED",
                        "CONTRACT_ONLY_CHANGE"],
   threshold=("L7_MIN = {control: 100, candidate: 100, cell: 30} per research_engine/registry/"
              "learning_adaptation_adjudication.L7_MIN; completeness requires 100% of the governed "
              "population to be producer-assigned"),
   reentry_trigger_class="NEW_EVIDENCE_EPOCH"),
]

# â”€â”€ DATASET IMPROVEMENT MATRIX â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
DATASET_MATRIX = [
 dict(dataset="shadow_runtime", current_schema="shadow_runtime_v1", current_generation=1,
      persisted_objects=200, persisted_rows=66258, date_span="2026-09-07..2026-09-29",
      producer=PRODUCER["shadow_runtime"], producer_module="core/shadow/runtime.py:580",
      requirements=["OR-01","OR-02","OR-03","OR-04","OR-05","OR-06","OR-07","OR-14","OR-15"],
      questions=["E5","D2","D4","D5","M3","M4","M5","M6","M7","M8","M9","M10","M11","S4","X5",
                 "P1","L1","L4","L5","L2","EX10","R3","R4","R5","HORIZON-1","STRAT-1","PORT-1","EX2","L7"],
      new_fields=["decision_snapshot.market_phase (non-null, bound at OPEN)",
                  "decision_snapshot.h4_regime (100% non-null)",
                  "decision_snapshot.pattern (100% non-null)",
                  "decision_snapshot.strategy (100% non-null)",
                  "identity.canonical_opportunity_id (nested mirror)",
                  "market_timestamp_semantics + market_timestamp_normalization_version on 100% of OPEN",
                  "exit_bar_path_m5_v2[] (ordered OHLC bound to the lifecycle)",
                  "experiment_arm + arm_assigned_at + arm_schema_version + arm_policy_version"],
      semantic_corrections=["live_facts.market_phase is emitted but 100% NULL -- must carry the value",
                            "trade_state_progression [{bar,close,r}] is NOT an OHLC path"],
      grain_corrections=["OHLC bars bound to (shadow_trade_id, canonical_opportunity_id, trade_horizon)",
                         "experiment arm bound one-per-lifecycle, not per-dataset"],
      identity_corrections=["nested identity.canonical_opportunity_id mirror for contract conformance"],
      timestamp_corrections=["market_timestamp_semantics/normalization_version on 100% of OPEN rows",
                             "arm_assigned_at strictly precedes outcome population"],
      lineage_additions=["arm assignment decision record (issuer, request id, policy version)",
                         "M5 path -> lifecycle binding edge"],
      backfill_possible="PARTIAL (OR-01..OR-07 yes; OR-14/OR-15 no)",
      future_only=["OR-14","OR-15"],
      schema_version_bump="YES -> shadow_runtime_v2", producer_version_bump="YES",
      dataset_version_bump="YES -> generation 2", new_dataset_required="YES (exit_bar_path_m5_v2)",
      reentry_unlocked=["EX2","L7","E5","D2","D4","D5","M3","M4","M5","M6","M7","M8","M9","M10",
                        "M11","S4","X5","P1","L1","L2","L4","L5","R3","R4","R5","EX10",
                        "HORIZON-1","STRAT-1","PORT-1"],
      priority="P0", dependency_order=1,
      priority_rationale="sole owner of 9 of 15 observation requirements; every other change depends on it"),

 dict(dataset="events", current_schema="events_v1", current_generation=1,
      persisted_objects=48737, persisted_rows="15359 CANDLE sampled / 249 partitions",
      date_span="2026-09-03..2026-09-29", producer=PRODUCER["events"],
      producer_module="observation_stream",
      requirements=["OR-14"], questions=["EX2","L1","L4"],
      new_fields=["lifecycle_binding: shadow_trade_id, canonical_opportunity_id, trade_horizon",
                  "payload.timestamp_semantics + timestamp_normalization_version on 100% of M5 rows"],
      semantic_corrections=["payload key naming o/h/l/c vs contract open/high/low/close"],
      grain_corrections=["CANDLE rows must be joinable to a lifecycle without post-hoc range inference"],
      identity_corrections=["carry the canonical lifecycle identity on the M5 bar record"],
      timestamp_corrections=["payload.timestamp_semantics on 100% (currently 76/15359)"],
      lineage_additions=["M5 aggregation lineage from the base timeframe feed"],
      backfill_possible="NO", future_only=["OR-14"],
      schema_version_bump="YES -> events_v2", producer_version_bump="YES",
      dataset_version_bump="YES -> generation 2",
      new_dataset_required="NO (if exit_bar_path_m5_v2 is owned separately)",
      reentry_unlocked=["EX2"], priority="P0", dependency_order=2,
      priority_rationale="bar side of the EX2 path; must emit before the bound path dataset can close"),

 dict(dataset="decision_trace", current_schema="decision_trace_v1", current_generation=1,
      persisted_objects=200, persisted_rows=25516, date_span="2026-09-07..2026-09-29",
      producer=PRODUCER["decision_trace"], producer_module="decision_diagnostics",
      requirements=["OR-08","OR-09"], questions=["D3","X5"],
      new_fields=["p_success populated (currently emitted but 100% NULL)"],
      semantic_corrections=[], grain_corrections=[], identity_corrections=[],
      timestamp_corrections=[], lineage_additions=[],
      backfill_possible="NO (p_success was never populated)", future_only=["OR-08"],
      schema_version_bump="NO", producer_version_bump="YES",
      dataset_version_bump="NO", new_dataset_required="NO",
      reentry_unlocked=["D3","X5"], priority="P1", dependency_order=3,
      priority_rationale="single nullable field; v10_entry (OR-09) is already satisfied"),

 dict(dataset="opportunities", current_schema="opportunities_v1", current_generation=1,
      persisted_objects=200, persisted_rows=100850, date_span="2026-09-07..2026-09-29",
      producer=PRODUCER["opportunities"], producer_module="core/opportunity/persistence.py",
      requirements=["OR-13"], questions=["OPP-1"],
      new_fields=["state-bearing record emitted for every canonical_opportunity_id (currently 50.1%)"],
      semantic_corrections=[], grain_corrections=["one state row per canonical opportunity"],
      identity_corrections=[], timestamp_corrections=[], lineage_additions=[],
      backfill_possible="YES", future_only=[],
      schema_version_bump="NO", producer_version_bump="YES",
      dataset_version_bump="NO", new_dataset_required="NO",
      reentry_unlocked=["OPP-1"], priority="P2", dependency_order=4,
      priority_rationale="50.1% coverage is the binding constraint; identity is already universal"),

 dict(dataset="shadow_trades", current_schema="shadow_trades_v1 (DECLARED, NEVER EMITTED)",
      current_generation=1, persisted_objects=0, persisted_rows=0, date_span=None,
      producer=PRODUCER["shadow_trades"], producer_module="core/shadow_trades.py:_persist_to_s3",
      requirements=["OR-01","OR-02","OR-03","OR-04","OR-05","OR-06","OR-07","OR-15"],
      questions=["E5","D2","D3","D4","D5","M3","M4","M5","M6","M7","M8","M9","M10","M11",
                 "S4","X5","P1","L1","L2","L4","L5","R3","R4","R5","EX10","HORIZON-1",
                 "STRAT-1","PORT-1","L7"],
      new_fields=["(none -- this dataset has never emitted a single S3 object)"],
      semantic_corrections=["the register names a dataset whose producer never persists"],
      grain_corrections=[], identity_corrections=[], timestamp_corrections=[], lineage_additions=[],
      backfill_possible="NO", future_only=["all"],
      schema_version_bump="N/A", producer_version_bump="NO",
      dataset_version_bump="N/A",
      new_dataset_required="NO -- re-point the register to shadow_runtime_v1",
      reentry_unlocked=[], priority="P0", dependency_order=0,
      priority_rationale="governance correction: phantom owner of 27 of 31 governed gaps"),
]

# â”€â”€ GOVERNED GAP INVENTORY (31) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
GOVERNED_GAPS = [g["gap_id"] for g in json.load(
    open(os.path.join(ASSURANCE, "stage4_dataset_schema_gap_register_20260928.json"),
         encoding="utf-8"))["gaps"]]
GOVERNED_GAPS += ["EX2 (OG-EX2-4db003c3ab76)", "L7 (OG-L7-4db003c3ab76)"]

# Every governed gap must trace to >=1 observation requirement.
COVERED = set()
for _o in OBSERVATION_REQUIREMENTS:
    COVERED.update(_o["gaps"])

UNACCOUNTED = [g for g in GOVERNED_GAPS if g not in COVERED]
OWNERLESS = [o["id"] for o in OBSERVATION_REQUIREMENTS
             if not o.get("authoritative_dataset") or not o.get("authoritative_producer")]

DISPOSITION_ORDER = ["SATISFIED_ALREADY", "CONTRACT_GOVERNANCE_REQUIRED", "PARTIAL_DATASET_CHANGE_REQUIRED",
                     "PRODUCER_CHANGE_REQUIRED", "SCHEMA_CHANGE_REQUIRED", "NEW_DATASET_REQUIRED"]
CONSERVATION = {d: 0 for d in DISPOSITION_ORDER}
for _o in OBSERVATION_REQUIREMENTS:
    CONSERVATION[_o["disposition"]] += 1

BACKFILL = {"HISTORICALLY_BACKFILLABLE_AUTHORITATIVELY": 0, "FUTURE_COLLECTION_ONLY": 0, "NOT_APPLICABLE": 0}
for _o in OBSERVATION_REQUIREMENTS:
    BACKFILL[_o["backfill"]] += 1

VC = {}
for _o in OBSERVATION_REQUIREMENTS:
    for _v in _o["version_consequence"]:
        VC[_v] = VC.get(_v, 0) + 1


def primary_disposition_by_gap():
    out = {}
    for o in OBSERVATION_REQUIREMENTS:
        for g in o["gaps"]:
            out.setdefault(g, {"requirement": o["id"], "disposition": o["disposition"]})
    return out


GAP_TO_OR = primary_disposition_by_gap()

# â”€â”€ REVERSE MAPPING: dataset change -> requirements -> questions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
REVERSE = {d["dataset"]: {"requirements": d["requirements"],
                          "questions_unlocked": d["reentry_unlocked"],
                          "schema_bump": d["schema_version_bump"],
                          "producer_bump": d["producer_version_bump"],
                          "new_dataset": d["new_dataset_required"]}
           for d in DATASET_MATRIX}

SHARED_ROOT_CAUSES = [
 dict(root_cause="re-point the dataset-schema gap register from the phantom `shadow_trades` "
                 "to the real persisted producer `shadow_runtime_v1`",
      evidence="list_objects_v2 KeyCount=0 for supporting/shadow_trades/; 66258 rows persisted at "
               "supporting/shadow_runtime/schema_version=shadow_runtime_v1/",
      requirements=["OR-01","OR-02","OR-03","OR-04","OR-05","OR-06","OR-07","OR-15"],
      gaps_affected=27,
      questions=["E5","D2","D3","D4","D5","M3","M4","M5","M6","M7","M8","M9","M10","M11","S4","X5",
                 "P1","L1","L2","L4","L5","R3","R4","R5","EX10","HORIZON-1","STRAT-1","PORT-1","L7"],
      effect="removes 27 of 31 false dataset-level blockers without emitting a single new field"),
 dict(root_cause="populate the lifecycle-bound decision snapshot (market_phase, h4_regime, pattern, strategy)",
      evidence="market_phase 100% NULL in shadow_runtime while 100% non-null in market_context_v1, "
               "decision_trace_v1 and strategy_observation_v1",
      requirements=["OR-02","OR-03","OR-04","OR-05"],
      gaps_affected=14,
      questions=["M3","M4","M5","M6","M7","M8","M9","M10","M11","S4","P1","L1","L5"],
      effect="one producer fix satisfies the market-state half of 14 registered gaps"),
 dict(root_cause="emit market_timestamp_semantics + market_timestamp_normalization_version on 100% of OPEN rows",
      evidence="present on 7473/66258 shadow_runtime rows (11.3%); "
               "shadow_timestamp_normalization.reject() fails closed without them",
      requirements=["OR-07"], gaps_affected=7,
      questions=["L1","L2","L4","L5","R4","R5","EX10"],
      effect="unblocks canonical-UTC interpretation for every timestamp-dependent question"),
 dict(root_cause="bind authoritative M5 OHLC bars to the shadow lifecycle at capture time",
      evidence="12389/12389 M5 rows OHLC-complete in events_v1, but 0/15359 carry any lifecycle identity",
      requirements=["OR-14"], gaps_affected=1, questions=["EX2"],
      effect="the only true future-only data gap; NOT a market-data absence"),
 dict(root_cause="issue a producer-authoritative experiment arm before outcome population",
      evidence="experiment_arm/arm_assigned_at/arm_schema_version = 0/66258 and absent from core/shadow/*.py",
      requirements=["OR-15"], gaps_affected=1, questions=["L7"],
      effect="the only true future-only labelling gap; cannot be inferred, only issued"),
]

EX2_AUDIT = dict(
  observation_requirement="OG-EX2-4db003c3ab76",
  missing_observable="events_v1:CANDLE:mt5_data:M5 ordered open/high/low/close between "
                     "entry_market_time and exit_market_time inclusive",
  candidate_dataset="events (bar side) + shadow_runtime (lifecycle side)",
  authoritative_dataset="shadow_runtime (owns the bound path via a new exit_bar_path_m5_v2 dataset), "
                        "with events as the authoritative bar source",
  producer=PRODUCER["shadow_runtime"] + " (capture at lifecycle time)",
  required_fields=["shadow_trade_id", "canonical_opportunity_id", "trade_horizon", "symbol",
                   "bar_ts (strictly ascending)", "open", "high", "low", "close",
                   "bar_timestamp_semantics", "bar_timestamp_normalization_version", "schema_version"],
  required_identity="one record per (shadow_trade_id, canonical_opportunity_id, trade_horizon, M5 bar ts)",
  required_time_semantics=("bar ts in producer market time at the causal bar close event plus CURRENT epoch "
                           "attestation; interval (entry_market_time, exit_market_time] inclusive of exit bar"),
  required_lineage=["producer:shadow_trades", "events_v1 producer instance",
                    "M5 aggregation lineage from the base timeframe feed",
                    "binding edge lifecycle -> bar proven at capture, not inferred post hoc"],
  existing_partial_source="events_v1 CANDLE M5 bars (12389/12389 OHLC-complete) and shadow_runtime "
                          "trade_state_progression [{bar,close,r}] (20421 rows)",
  why_partial_source_insufficient=(
      "The M5 bars are complete and authoritative as BARS, but 0/15359 carry shadow_trade_id, "
      "canonical_opportunity_id or trade_horizon, so a (symbol, ts) range join is the only way to attach "
      "them, which the scientific contract does not accept as authoritative binding. trade_state_progression "
      "carries only {bar, close, r} -- no open, high or low -- and is permanently forbidden as a substitute."),
  schema_change_required="YES", producer_change_required="YES", new_dataset_required="YES",
  historical_backfill_possible="NO", future_collection_only="YES",
  m5_bars_exist="YES (events_v1, 100% OHLC-complete, 23 date partitions, 10 symbols)",
  defect_is="ABSENCE OF LIFECYCLE BINDING, NOT ABSENCE OF MARKET DATA",
)

L7_AUDIT = dict(
  observation_requirement="OG-L7-4db003c3ab76",
  missing_observable="producer-authoritative experiment arm assignment exactly CONTROL or CANDIDATE, "
                     "issued before outcome knowledge can contaminate it",
  authoritative_producer=PRODUCER["shadow_runtime"] + " (assignment decision record)",
  authoritative_dataset="shadow_runtime (new arm dataset v2)",
  canonical_entity="(shadow_trade_id, canonical_opportunity_id, trade_horizon)",
  required_fields=["experiment_arm", "arm_assigned_at", "arm_schema_version", "arm_policy_version",
                   "arm_assignment_id", "arm_issuer", "arm_request_id", "experiment_id", "treatment_id"],
  allowed_values=["CONTROL", "CANDIDATE"],
  assignment_timestamp="arm_assigned_at, written at assignment time, strictly before any outcome field",
  assignment_policy_version="arm_policy_version + arm_schema_version required and persisted",
  experiment_treatment_identity="experiment_id / treatment_id required in addition to the arm",
  lineage=["assignment decision record (issuer, request id, policy version)",
           "immutability: the arm may never be rewritten or back-filled",
           "back to the research candidate / promotion state that issued it"],
  existing_partial_source="NONE (experiment_arm/arm_assigned_at/arm_schema_version = 0/66258; no occurrence "
                          "of 'experiment_arm' in core/shadow/*.py)",
  why_shadow_trades_v1_insufficient=(
      "shadow_trades_v1 is a dataset schema identity string, not an arm. The Stage 4 V2 successor contract "
      "currently overloads `schema_version` as the assignment field, which collides head-on with the "
      "persisted schema_version value 'shadow_runtime_v1' emitted on 66258/66258 rows. That collision must "
      "be corrected before any arm can bind."),
  schema_change_required="YES", producer_change_required="YES", new_dataset_required="YES",
  historical_backfill_possible="NO", future_collection_only="YES",
  governed_threshold="L7_MIN = {control: 100, candidate: 100, cell: 30}",
)

# â”€â”€ RE-ENTRY ELIGIBILITY â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
REENTRY = [
 dict(requirement="OR-01", question_ids=["E5","D2","D4","D5","M3","M4","M5","M6","M7","M8","M9","M10",
                                         "M11","S4","X5","P1","L1","L4","L5","STRAT-1","PORT-1","HORIZON-1"],
      schema_deployed="re-point register to shadow_runtime_v1 (no schema change)",
      producer_emitting="outcome.pnl_r_multiple already emitting on 100% of CLOSE lifecycles",
      minimum_completeness="100% of governed CLOSE lifecycles",
      minimum_sample="THRESHOLD_GOVERNANCE_REQUIRED", minimum_duration="THRESHOLD_GOVERNANCE_REQUIRED",
      lineage_valid="canonical_opportunity_id present on 66258/66258 (top-level)",
      null_tolerance="0 null outcomes on CLOSE lifecycles",
      evidence_epoch="FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
      dependencies=["OR-06"], trigger_class="DATA_THRESHOLD_REACHED",
      immediately_eligible_after_audit="NO -- threshold governance required"),
 dict(requirement="OR-02", question_ids=["M3","M4","M5","M6","M7","M8","M9","M10","L1","L4"],
      schema_deployed="shadow_runtime_v2 emits decision_snapshot.market_phase non-null",
      producer_emitting="core/shadow/runtime.py populates market_phase at OPEN",
      minimum_completeness="100% of OPEN lifecycles", minimum_sample="THRESHOLD_GOVERNANCE_REQUIRED",
      minimum_duration="THRESHOLD_GOVERNANCE_REQUIRED", lineage_valid="lifecycle-bound, not market-grain",
      null_tolerance="0 null", evidence_epoch="NEW_EVIDENCE_EPOCH",
      dependencies=["OR-06"], trigger_class="SCHEMA_COLLECTION_RECOVERED",
      immediately_eligible_after_audit="NO"),
 dict(requirement="OR-08", question_ids=["D3","X5"],
      schema_deployed="no schema change (key already emitted)",
      producer_emitting="decision_trace producer must populate p_success",
      minimum_completeness="100%", minimum_sample="THRESHOLD_GOVERNANCE_REQUIRED",
      minimum_duration="THRESHOLD_GOVERNANCE_REQUIRED", lineage_valid="canonical_opportunity_id 25516/25516",
      null_tolerance="0 null", evidence_epoch="NEW_EVIDENCE_EPOCH",
      dependencies=["OR-09"], trigger_class="SCHEMA_COLLECTION_RECOVERED",
      immediately_eligible_after_audit="NO"),
 dict(requirement="OR-14", question_ids=["EX2"],
      schema_deployed="exit_bar_path_m5_v2 + events_v2 lifecycle binding",
      producer_emitting="shadow_runtime captures ordered M5 OHLC at lifecycle time",
      minimum_completeness="100% of 8760 governed lifecycles (exact)",
      minimum_sample="8760 (fixed governed roster)", minimum_duration="full lifecycle coverage",
      lineage_valid="binding edge proven at capture",
      null_tolerance="0 missing bars between entry and exit inclusive",
      evidence_epoch="NEW_EVIDENCE_EPOCH", dependencies=["OR-07"],
      trigger_class="NEW_EVIDENCE_EPOCH", immediately_eligible_after_audit="NO"),
 dict(requirement="OR-15", question_ids=["L7"],
      schema_deployed="arm dataset v2 with dedicated experiment_arm field (NOT schema_version)",
      producer_emitting="shadow_runtime issues arm before outcome population",
      minimum_completeness="100% of the governed L7 population",
      minimum_sample="L7_MIN control>=100, candidate>=100, cell>=30",
      minimum_duration="THRESHOLD_GOVERNANCE_REQUIRED", lineage_valid="assignment decision record present",
      null_tolerance="0 unlabeled, 0 unknown, 0 inferred",
      evidence_epoch="NEW_EVIDENCE_EPOCH", dependencies=[],
      trigger_class="NEW_EVIDENCE_EPOCH", immediately_eligible_after_audit="NO"),
]

AUDIT = {
 "schema": SCHEMA, "stage": "STAGE4_OBSERVATION_DATASET_AUDIT", "stamp": STAMP,
 "mode": "AUDIT_DISCOVERY_ONLY", "implementation_performed": False,
 "baseline_certification_fingerprint": CERT_FP,
 "ex2_l7_adjudication_fingerprint": ADJ_FP,
 "s3": {"bucket": BUCKET, "profile": PROFILE, "account": ACCOUNT,
        "access": "READ_ONLY", "writes": 0, "deletes": 0, "copies": 0, "lifecycle_changes": 0,
        "authentication": "VERIFIED via sts get-caller-identity"},
 "counts": {
   "GAP_COUNT_RAW": len(GOVERNED_GAPS),
   "UNIQUE_OBSERVATION_REQUIREMENTS": len(OBSERVATION_REQUIREMENTS),
   "DATASETS_INSPECTED": len(PERSISTED),
   "PRODUCERS_INSPECTED": len(PRODUCER),
   "PERSISTED_S3_DATASETS_INSPECTED": sum(1 for d in PERSISTED.values() if d.get("persisted")),
   "DECLARED_BUT_UNPERSISTED_DATASETS": sum(1 for d in PERSISTED.values() if not d.get("persisted")),
 },
 "conservation": {
   "TOTAL_GOVERNED_GAPS": len(GOVERNED_GAPS),
   "mutually_exclusive_primary_disposition_by_requirement": CONSERVATION,
   "sum_of_primary_dispositions": sum(CONSERVATION.values()),
   "UNACCOUNTED_GAPS": len(UNACCOUNTED),
   "unaccounted_gap_ids": UNACCOUNTED,
   "GAPS_WITHOUT_DATASET_OR_PRODUCER_OWNER": len(OWNERLESS),
   "ownerless_requirement_ids": OWNERLESS,
   "UNEXPLAINED_FIELD_SCHEMA_MISMATCHES": 0,
   "conserved": len(UNACCOUNTED) == 0 and len(OWNERLESS) == 0
                and sum(CONSERVATION.values()) == len(OBSERVATION_REQUIREMENTS),
 },
 "backfill_analysis": BACKFILL,
 "version_consequences": VC,
 "governed_gaps": GOVERNED_GAPS,
 "gap_to_observation_requirement": GAP_TO_OR,
 "observation_requirements": OBSERVATION_REQUIREMENTS,
 "persisted_dataset_universe": PERSISTED,
 "dataset_improvement_matrix": DATASET_MATRIX,
 "reverse_mapping": REVERSE,
 "shared_root_causes": SHARED_ROOT_CAUSES,
 "ex2_audit": EX2_AUDIT,
 "l7_audit": L7_AUDIT,
 "reentry_eligibility": REENTRY,
 "mutation_ledger": {
   "s3_writes": 0, "schema_mutations": 0, "producer_mutations": 0,
   "research_reentry_events": 0, "q71_started": False, "rb1_created": False,
   "new_observation_requirements_deployed": False, "backfills_performed": 0,
 },
}
AUDIT["audit_fingerprint"] = fingerprint(
    {k: v for k, v in AUDIT.items() if k != "audit_fingerprint"})

MATRIX = {
 "schema": "stage4_observation_requirement_matrix_v1", "stamp": STAMP,
 "baseline_certification_fingerprint": CERT_FP,
 "counts": AUDIT["counts"],
 "observation_requirements": OBSERVATION_REQUIREMENTS,
 "governed_gaps": GOVERNED_GAPS,
 "gap_to_observation_requirement": GAP_TO_OR,
 "shared_root_causes": SHARED_ROOT_CAUSES,
 "reverse_mapping": REVERSE,
}
IMPROVEMENT = {
 "schema": "stage4_dataset_improvement_matrix_v1", "stamp": STAMP,
 "baseline_certification_fingerprint": CERT_FP,
 "persisted_dataset_universe": PERSISTED,
 "dataset_improvement_matrix": DATASET_MATRIX,
 "version_consequences": VC,
 "backfill_analysis": BACKFILL,
 "dependency_order": [d["dataset"] for d in sorted(DATASET_MATRIX,
                                                   key=lambda x: x["dependency_order"])],
}


def write(path, payload):
    with open(os.path.join(ASSURANCE, path), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, default=str)
        fh.write("\n")
    return os.path.join(ASSURANCE, path)


def md_table(rows, cols):
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    return "\n".join(out)


def main():
    write(f"stage4_observation_dataset_audit_{STAMP}.json", AUDIT)
    write(f"stage4_observation_requirement_matrix_{STAMP}.json", MATRIX)
    write(f"stage4_dataset_improvement_matrix_{STAMP}.json", IMPROVEMENT)

    c, cons = AUDIT["counts"], AUDIT["conservation"]
    disp = cons["mutually_exclusive_primary_disposition_by_requirement"]
    L = ["# Stage 4 Observation / Dataset Audit", "",
         f"Stamp: {STAMP}  |  Mode: {AUDIT['mode']}  |  Implementation performed: NO", "",
         f"Baseline certification fingerprint: `{CERT_FP}`", "",
         f"EX2/L7 adjudication fingerprint: `{ADJ_FP}`", "",
         "## Headline", "",
         f"The expected universe of **31 governed gaps is NOT 31 independent dataset changes**. The "
         f"{c['GAP_COUNT_RAW']} governed gaps resolve to **{c['UNIQUE_OBSERVATION_REQUIREMENTS']} unique "
         f"observation requirements** touching **{c['DATASETS_INSPECTED']} datasets**, of which only "
         f"**2 are genuinely future-only** (EX2, L7).", "",
         "### The single largest finding", "",
         "`shadow_trades` / `shadow_trades_v1` is named as the current dataset by **27 of 31** governed "
         "gaps -- but `list_objects_v2` returns **KeyCount = 0** for `supporting/shadow_trades/`. The "
         "dataset is declared in `core/production_data_contract.py` and has a writer in "
         "`core/shadow_trades.py`, but it has **never emitted a single persisted object**. The real "
         "shadow lifecycle producer is `core/shadow/persistence.py` writing `shadow_runtime_v1`, which "
         "has persisted **66,258 rows**. Most registered 'missing' observables already exist there under a "
         "different dataset identity and/or path.", "",
         "## Conservation", "",
         md_table([{"metric": k, "value": v} for k, v in [
             ("TOTAL_GOVERNED_GAPS", cons["TOTAL_GOVERNED_GAPS"]),
             ("UNIQUE_OBSERVATION_REQUIREMENTS", c["UNIQUE_OBSERVATION_REQUIREMENTS"]),
             ("SATISFIED_ALREADY", disp["SATISFIED_ALREADY"]),
             ("CONTRACT_GOVERNANCE_REQUIRED", disp["CONTRACT_GOVERNANCE_REQUIRED"]),
             ("PARTIAL_DATASET_CHANGE_REQUIRED", disp["PARTIAL_DATASET_CHANGE_REQUIRED"]),
             ("PRODUCER_CHANGE_REQUIRED", disp["PRODUCER_CHANGE_REQUIRED"]),
             ("SCHEMA_CHANGE_REQUIRED", disp["SCHEMA_CHANGE_REQUIRED"]),
             ("NEW_DATASET_REQUIRED", disp["NEW_DATASET_REQUIRED"]),
             ("UNACCOUNTED_GAPS", cons["UNACCOUNTED_GAPS"]),
             ("GAPS_WITHOUT_DATASET_OR_PRODUCER_OWNER", cons["GAPS_WITHOUT_DATASET_OR_PRODUCER_OWNER"]),
             ("UNEXPLAINED_FIELD_SCHEMA_MISMATCHES", cons["UNEXPLAINED_FIELD_SCHEMA_MISMATCHES"]),
             ("conserved", cons["conserved"]),
         ]], ["metric", "value"]), "",
         "## Persisted S3 universe (read-only)", "",
         md_table([{"dataset": d["dataset"], "schema": d["schema"], "prefix": d["prefix"],
                    "objects": d["objects"], "rows": d["rows"],
                    "span": f"{d.get('date_min')}..{d.get('date_max')}" if d.get("date_min") else "NOT PERSISTED"}
                   for d in PERSISTED.values()],
                  ["dataset", "schema", "prefix", "objects", "rows", "span"]), "",
         "## Observation requirements", "",
         md_table([{"id": o["id"], "observable": o["required_observable"],
                    "dataset": o["authoritative_dataset"], "classification": o["primary_classification"],
                    "disposition": o["disposition"], "gaps": len(o["gaps"]),
                    "questions": len(o["questions"])} for o in OBSERVATION_REQUIREMENTS],
                  ["id", "observable", "dataset", "classification", "disposition", "gaps", "questions"]),
         "", "## Shared root causes", ""]
    for i, rc in enumerate(SHARED_ROOT_CAUSES, 1):
        L += [f"### {i}. {rc['root_cause']}", "",
              f"- Evidence: {rc['evidence']}",
              f"- Requirements satisfied: `{', '.join(rc['requirements'])}`",
              f"- Governed gaps affected: {rc['gaps_affected']}",
              f"- Questions affected: {', '.join(rc['questions'])}",
              f"- Effect: {rc['effect']}", ""]
    L += ["## Dataset improvement matrix", "",
          md_table([{"dataset": d["dataset"], "schema": d["current_schema"],
                     "rows": d["persisted_rows"], "schema bump": d["schema_version_bump"],
                     "producer bump": d["producer_version_bump"], "new dataset": d["new_dataset_required"],
                     "backfill": d["backfill_possible"], "order": d["dependency_order"]}
                    for d in sorted(DATASET_MATRIX, key=lambda x: x["dependency_order"])],
                    ["dataset", "schema", "rows", "schema bump", "producer bump", "new dataset",
                     "backfill", "order"]), "",
          "## EX2", "", "```", "EX2_OBSERVATION_REQUIREMENT"]
    L += [f"{k}: {v}" for k, v in EX2_AUDIT.items()]
    L += ["```", "", "## L7", "", "```", "L7_OBSERVATION_REQUIREMENT"]
    L += [f"{k}: {v}" for k, v in L7_AUDIT.items()]
    L += ["```", "", "## Mutation ledger", "",
          "```", "S3 WRITES: 0", "SCHEMA MUTATIONS: 0", "PRODUCER MUTATIONS: 0",
          "RESEARCH RE-ENTRY EVENTS: 0", "Q71+ STARTED: NO", "BACKFILLS PERFORMED: 0", "```", "",
          "This audit is a BLUEPRINT ONLY. No dataset improvement has been implemented, no collection "
          "activated, no backfill performed and no research question re-entered.", ""]
    with open(os.path.join(ASSURANCE, f"stage4_observation_dataset_audit_{STAMP}.md"),
              "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    for p in (f"stage4_observation_dataset_audit_{STAMP}.json",
              f"stage4_observation_requirement_matrix_{STAMP}.json",
              f"stage4_dataset_improvement_matrix_{STAMP}.json",
              f"stage4_observation_dataset_audit_{STAMP}.md"):
        print("wrote " + os.path.join(ASSURANCE, p))
    print(json.dumps({"counts": c, "conservation": cons, "backfill": BACKFILL,
                      "version_consequences": VC}, indent=2, default=str))


if __name__ == "__main__":
    main()
