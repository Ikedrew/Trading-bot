"""Shadow Runtime lifecycle observability — ROOT CHANGES 02/03/04/05.

This module builds the four lifecycle-bound evidence blocks that the persisted
``shadow_runtime_v1`` OPEN event was missing.  It is pure construction logic: no
I/O, no clock access, no randomness, no network.  ``core/shadow/runtime.py``
calls these builders once, at OPEN, and the results are frozen into the
immutable OPEN definition.

Root change mapping (governance identities from
``research_engine/control_plane/stage4_dataset_authority.py``):

  ROOT-02-LIFECYCLE-DECISION-SNAPSHOT   -> :func:`build_decision_snapshot`
  ROOT-03-MARKET-TIMESTAMP-SEMANTICS    -> :func:`build_market_time_attestation`
  ROOT-04-EX2-LIFECYCLE-M5-PATH         -> M5 OHLC path helpers
  ROOT-05-L7-EXPERIMENT-ARM             -> :func:`assign_experiment_arm`

Design rules that apply to every builder here:

  1. NEVER FABRICATE.  A fact the producer did not hold is recorded as an
     explicit absence with a machine-readable reason.  The governance
     requirement is coverage of the *record*, not invention of the *value*:
     100% of lifecycles must carry an honest, decision-bound statement about
     the fact, including "this was not available at decision time".
  2. BOUND TO THE LIFECYCLE.  Every block is keyed on the exact
     ``(shadow_trade_id, canonical_opportunity_id, trade_horizon)`` identity
     and stamped with the decision instant, so a consumer never has to join
     post hoc on a proxy key.
  3. DETERMINISTIC.  Same inputs -> same bytes.  The digests below are
     sha256 over canonical JSON, so tampering with a persisted block is
     detectable and a replay is byte-identical.
  4. ADDITIVE ONLY.  Existing ``live_facts`` / ``construction`` /
     ``simulation_assumptions`` blocks are untouched; these are new keys.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping

from core.shadow.models import (
    M5_BAR_INTERVAL_S,
    MARKET_TIMESTAMP_NORMALIZATION_VERSION,
    MARKET_TIMESTAMP_SEMANTICS,
    SCHEMA_VERSION,
)

# ═══════════════════════════════════════════════════════════════════════════
# DATASET / PRODUCER PROVENANCE (single governed source of truth)
#
# These are the values stamped onto every observability block so a consumer
# never has to guess which dataset version, schema generation and producer
# version a record belongs to.  They are the SAME identities the control-plane
# version authority (``stage4_data_versioning``) governs, restated here as
# literals so the producer carries no import-time dependency on the research
# engine.  ``tests/test_stage4_observation_closeout.py`` asserts the two agree.
# ═══════════════════════════════════════════════════════════════════════════

SHADOW_RUNTIME_DATASET = "shadow_runtime"

#: The dataset VERSION.  Unchanged by the Stage 4 observability work: the
#: dataset still means the same lifecycle/event stream at the same grain with
#: the same canonical identity, so NO dataset version bump is required.
SHADOW_RUNTIME_DATASET_VERSION = SCHEMA_VERSION

#: Schema generation.  Generation 1 is the historical baseline; generation 2
#: adds the ROOT-02/03/04/05 observability fields.  Generation-1 records are
#: never rewritten and must not be claimed to satisfy generation-2 contracts.
SHADOW_RUNTIME_SCHEMA_GENERATION = 2
PREVIOUS_SCHEMA_GENERATION = 1

#: The exact generation-2 field set this producer is governed to emit.  Kept
#: here (not only in the control-plane registry) so the startup guard in
#: ``core.observability_contract`` can compare the two and fail closed when the
#: producer is edited without a governed contract change.
SHADOW_RUNTIME_GEN2_FIELDS: tuple[str, ...] = (
    "decision_snapshot",        # ROOT-02 lifecycle decision snapshot
    "market_time_attestation",  # ROOT-03 market-time semantics
    "experiment_arm",           # ROOT-05 pre-outcome experiment arm
    "lifecycle_m5_path",        # ROOT-04 lifecycle-bound M5 OHLC path
)

#: Producer identity + version for the generation-2 emission.
SHADOW_RUNTIME_PRODUCER = "core/shadow/runtime.py + core/shadow/persistence.py"
SHADOW_RUNTIME_PRODUCER_VERSION = "shadow_runtime_producer_v2"
PREVIOUS_PRODUCER_VERSION = "shadow_runtime_producer_v1"



# ═══════════════════════════════════════════════════════════════════════════
# SHARED HASHING
# ═══════════════════════════════════════════════════════════════════════════

def canonical_digest(payload: Any) -> str:
    """sha256 over canonical JSON — stable across processes and key order."""
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _epoch_to_iso(epoch_s: int) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(int(epoch_s), tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )



# ═══════════════════════════════════════════════════════════════════════════
# ROOT-02 — LIFECYCLE-BOUND DECISION SNAPSHOT
# ═══════════════════════════════════════════════════════════════════════════

DECISION_SNAPSHOT_VERSION = "lifecycle_decision_snapshot_v1"

#: A value was held by the producer at the decision instant and is recorded.
STATE_OBSERVED = "OBSERVED"
#: The producer held nothing at the decision instant.  Recorded honestly.
STATE_ABSENT = "ABSENT_AT_DECISION_TIME"

#: Machine-readable reason an inherited live fact was absent.  This is
#: PROVENANCE, not an excuse: it names the upstream that declined to supply.
ABSENT_UPSTREAM_EMPTY = "UPSTREAM_EMPTY"
ABSENT_NOT_APPLICABLE = "NOT_APPLICABLE_TO_THIS_OPPORTUNITY"

#: The decision-bound facts and the upstream that authoritatively owns each.
#: ``numeric`` fields treat 0.0 as a legitimate OBSERVED value; only ``None``
#: and the empty string count as absent, so a genuine score of 0.0 is kept.
DECISION_FACT_SOURCES: tuple[tuple[str, str, str, bool], ...] = (
    # (fact key, upstream owner, source path, is_numeric)
    ("v10_action", "v10_pipeline_result", "new_result.action", False),
    ("v10_rejection_stage", "v10_pipeline_result",
     "new_result.rejection_stage", False),
    ("v10_selected_horizon", "v10_pipeline_result",
     "horizon.horizon_type", False),
    ("pattern", "signal_detection", "new_result.pattern", False),
    ("strategy", "v10_pipeline_result", "strategy.strategy_family", False),
    ("score", "signal_detection", "new_result.score", True),
    ("regime", "v10_pipeline_result", "market_state.regime.regime", False),
    ("h4_regime", "v10_pipeline_result", "market_state.regime.regime", False),
    ("h1_bias", "htf_context", "bias.direction", False),
    ("market_phase", "assessment", "assessment.market_phase", False),
    ("market_phase_confidence", "assessment",
     "assessment.market_phase_confidence", True),
)


def _fact_state(value: Any, *, is_numeric: bool) -> tuple[str, Any]:
    """Classify one inherited fact.  Returns ``(state, recorded_value)``."""
    if value is None:
        return STATE_ABSENT, None
    if is_numeric:
        try:
            return STATE_OBSERVED, float(value)
        except (TypeError, ValueError):
            return STATE_ABSENT, None
    text = str(value).strip()
    # An empty string is a genuine "upstream declined" signal.  The literal
    # "NONE" taxonomy member IS a value and is preserved verbatim.
    return (STATE_OBSERVED, text) if text else (STATE_ABSENT, None)


def build_decision_snapshot(
    ctx: Mapping[str, Any],
    *,
    shadow_trade_id: str,
    canonical_opportunity_id: str,
    trade_horizon: str,
    decision_market_time_utc: int,
) -> dict[str, Any]:
    """Build the ROOT-02 lifecycle-bound decision snapshot.

    Every governed decision fact is recorded with an explicit state.  A fact
    the producer did not hold yields ``ABSENT_AT_DECISION_TIME`` plus the
    upstream that declined, which is what makes the snapshot honest: the
    consumer can distinguish "we looked and there was nothing" from "this
    field was never captured".  No value is ever invented, defaulted, or
    back-filled from any later observation.
    """
    facts: dict[str, Any] = {}
    observed = 0
    absent = 0
    for key, owner, source_path, is_numeric in DECISION_FACT_SOURCES:
        state, value = _fact_state(ctx.get(key), is_numeric=is_numeric)
        if state == STATE_OBSERVED:
            observed += 1
        else:
            absent += 1
        facts[key] = {
            "state": state,
            "value": value,
            "upstream_owner": owner,
            "upstream_source_path": source_path,
            "absent_reason": (
                None if state == STATE_OBSERVED else ABSENT_UPSTREAM_EMPTY
            ),
        }

    identity = {
        "shadow_trade_id": shadow_trade_id,
        "canonical_opportunity_id": canonical_opportunity_id,
        "trade_horizon": trade_horizon,
    }
    payload: dict[str, Any] = {
        "snapshot_version": DECISION_SNAPSHOT_VERSION,
        "lifecycle_identity": identity,
        # The decision instant, in the same canonical UTC bar-open clock the
        # rest of the record uses (see the ROOT-03 attestation).
        "decision_market_time_utc": int(decision_market_time_utc),
        "facts": facts,
        "completeness": {
            "facts_total": len(facts),
            "facts_observed": observed,
            "facts_absent": absent,
            # Coverage is about the RECORD, not the value: every governed fact
            # carries a state, so completeness of the snapshot itself is 100%
            # while the underlying upstream gaps stay visible and countable.
            "record_completeness_ratio": 1.0,
            "value_availability_ratio": (
                round(observed / len(facts), 6) if facts else 0.0
            ),
        },
        "semantics": (
            "Inherited live observations as they stood at the decision "
            "instant, bound to this lifecycle identity. NOT shadow decisions, "
            "and never revised after OPEN."
        ),
    }
    payload["snapshot_digest"] = canonical_digest(payload)
    return payload


def decision_snapshot_is_tampered(snapshot: Mapping[str, Any]) -> bool:
    """Fail-closed integrity check for a persisted decision snapshot."""
    if not isinstance(snapshot, Mapping):
        return True
    recorded = snapshot.get("snapshot_digest")
    if not recorded:
        return True
    body = {k: v for k, v in snapshot.items() if k != "snapshot_digest"}
    return canonical_digest(body) != recorded



# ═══════════════════════════════════════════════════════════════════════════
# ROOT-03 — MARKET-TIME SEMANTICS (every record, unambiguously)
# ═══════════════════════════════════════════════════════════════════════════

MARKET_TIME_ATTESTATION_VERSION = "market_time_attestation_v1"

#: The single meaning every market timestamp in this record carries.
MEANING_M5_BAR_OPEN_UTC = "M5_BAR_OPEN_EPOCH_UTC"

#: Each market-time field the shadow record writes.  OPEN writes
#: event/opportunity/entry market time; CLOSE adds exit_market_time under the
#: same attestation rule.
ATTESTED_MARKET_TIME_FIELDS: tuple[str, ...] = (
    "event_market_time",
    "opportunity_market_time",
    "entry_market_time",
    "exit_market_time",
)


def build_market_time_attestation(
    market_time_fields: Mapping[str, Any],
    *,
    broker_offset_seconds: int,
) -> dict[str, Any]:
    """Declare, per timestamp, exactly what its value means (ROOT-03).

    The governed requirement is that a consumer can interpret every market
    timestamp without inference.  Each supplied field is therefore emitted
    with an explicit meaning, unit, clock and ISO rendering, and the whole
    block is digested so it cannot be edited after the fact.

    Fields absent from the record are listed in ``absent_fields`` rather than
    silently omitted, so "not applicable to this event type" is itself
    explicit and auditable.
    """
    entries: dict[str, Any] = {}
    absent: list[str] = []
    for name in ATTESTED_MARKET_TIME_FIELDS:
        raw = market_time_fields.get(name)
        if raw is None:
            absent.append(name)
            continue
        try:
            epoch = int(raw)
        except (TypeError, ValueError):
            absent.append(name)
            continue
        entries[name] = {
            "epoch_s": epoch,
            "utc_iso8601": _epoch_to_iso(epoch),
            "meaning": MEANING_M5_BAR_OPEN_UTC,
            "clock": "UTC",
            "unit": "seconds",
            "value_is_bar_open": True,
        }

    payload: dict[str, Any] = {
        "attestation_version": MARKET_TIME_ATTESTATION_VERSION,
        "market_timestamp_semantics": MARKET_TIMESTAMP_SEMANTICS,
        "market_timestamp_normalization_version": (
            MARKET_TIMESTAMP_NORMALIZATION_VERSION
        ),
        "bar_interval_seconds": M5_BAR_INTERVAL_S,
        "bar_alignment_modulus_seconds": M5_BAR_INTERVAL_S,
        "timebase": "UTC",
        "broker_offset_seconds": int(broker_offset_seconds),
        # The offset is provenance only.  It is recorded so a reader can
        # reproduce the derivation, and it is NEVER re-applied to a value
        # that is already canonical UTC.
        "offset_applied_by_producer": False,
        "offsets": entries,
        "absent_fields": sorted(absent),
        "semantics": (
            "Every listed market timestamp is the UTC epoch of the open time "
            "of the authoritative closed M5 bar named by the field, on a "
            f"{M5_BAR_INTERVAL_S}s grid. Values are already canonical UTC: the "
            "broker/server-to-UTC conversion happened once upstream in the "
            "MT5 feed and must not be repeated."
        ),
    }
    payload["attestation_digest"] = canonical_digest(payload)
    return payload


def market_time_attestation_errors(
    attestation: Mapping[str, Any] | None,
    market_time_fields: Mapping[str, Any],
) -> list[str]:
    """Validate a ROOT-03 attestation against the record it describes.

    Returns a list of human-readable problems; empty means valid.  The checks
    are deliberately strict, because the whole point of ROOT-03 is that a
    timestamp is interpretable WITHOUT inference:

      * the attestation must exist and carry a matching digest,
      * every market-time field present on the record must be declared,
      * every declared value must equal the record's own value,
      * every value must sit on the M5 bar grid.
    """
    problems: list[str] = []
    if not isinstance(attestation, Mapping):
        return ["MARKET_TIME_ATTESTATION_MISSING"]

    recorded = attestation.get("attestation_digest")
    if not recorded:
        return ["MARKET_TIME_ATTESTATION_DIGEST_MISSING"]
    body = {k: v for k, v in attestation.items()
            if k != "attestation_digest"}
    if canonical_digest(body) != recorded:
        problems.append("MARKET_TIME_ATTESTATION_DIGEST_MISMATCH")

    offsets = attestation.get("offsets")
    if not isinstance(offsets, Mapping):
        return problems + ["MARKET_TIME_ATTESTATION_OFFSETS_MISSING"]

    for name in ATTESTED_MARKET_TIME_FIELDS:
        raw = market_time_fields.get(name)
        declared = offsets.get(name)
        if raw is None:
            continue  # legitimately absent for this event type
        if not isinstance(declared, Mapping):
            problems.append(f"MARKET_TIME_UNDECLARED:{name}")
            continue
        try:
            record_epoch = int(raw)
            declared_epoch = int(declared.get("epoch_s"))
        except (TypeError, ValueError):
            problems.append(f"MARKET_TIME_NOT_INTEGER:{name}")
            continue
        if record_epoch != declared_epoch:
            problems.append(f"MARKET_TIME_VALUE_DISAGREEMENT:{name}")
        if declared.get("meaning") != MEANING_M5_BAR_OPEN_UTC:
            problems.append(f"MARKET_TIME_MEANING_UNCLEAR:{name}")
        if record_epoch % M5_BAR_INTERVAL_S:
            problems.append(f"MARKET_TIME_OFF_BAR_GRID:{name}")
    return problems



# ═══════════════════════════════════════════════════════════════════════════
# ROOT-04 — LIFECYCLE-BOUND M5 OHLC PATH
# ═══════════════════════════════════════════════════════════════════════════

M5_PATH_VERSION = "lifecycle_m5_path_v2"
"""EX2 contract version.  v2 completes the audited EX2 field set: dataset and
schema generation, producer version provenance, per-bar timestamp semantics and
normalization version, explicit ordering, and lineage from lifecycle to path.
The dataset is UNCHANGED (additive block evolution), so this is a schema
GENERATION bump on ``shadow_runtime_v1``, not a dataset version bump."""

#: Path completeness states.
PATH_COMPLETE = "COMPLETE"
PATH_DEGRADED = "DEGRADED"
PATH_EMPTY = "EMPTY"

#: EX2 per-bar timestamp semantics.  Identical clock contract to ROOT-03, restated
#: on the path itself so a consumer of the path never has to reach into the
#: sibling attestation block to interpret a bar timestamp.
BAR_TIME_SEMANTICS = MEANING_M5_BAR_OPEN_UTC

#: Ordering contract: strictly ascending bar-open epochs on the M5 grid.
ORDERING_CONTRACT = "STRICTLY_ASCENDING_BAR_OPEN_UTC"


def m5_bar_entry(
    *,
    bar_time_utc: int,
    bar_open: Any,
    bar_high: float,
    bar_low: float,
    bar_close: float,
    bar_index: int,
) -> dict[str, Any]:
    """One lifecycle-evaluated M5 bar, recorded with its full OHLC + clock.

    ``bar_open`` is threaded from the authoritative feed.  When a caller
    cannot supply it the entry is still recorded — marked ``open_available:
    False`` — because dropping the bar would silently destroy the sequence,
    and an explicitly incomplete bar is honest in a way a missing one is not.
    """
    try:
        open_value = float(bar_open)
        open_available = True
    except (TypeError, ValueError):
        open_value = None
        open_available = False
    return {
        "bar_time_utc": int(bar_time_utc),
        "bar_time_utc_iso8601": _epoch_to_iso(int(bar_time_utc)),
        # Per-bar timestamp semantics, stated on the bar itself.  A consumer of
        # the path never has to reach into a sibling attestation block to know
        # what a bar timestamp means.
        "bar_time_semantics": BAR_TIME_SEMANTICS,
        "bar_time_unit": "seconds",
        "bar_time_timebase": "UTC",
        "bar_interval_seconds": M5_BAR_INTERVAL_S,
        "open": open_value,
        "high": float(bar_high),
        "low": float(bar_low),
        "close": float(bar_close),
        "open_available": open_available,
        "bar_index": int(bar_index),
    }


def summarise_m5_path(entries: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Assess completeness/ordering of a lifecycle M5 path.

    EX2's contract requires a strictly ascending, gap-free, OHLC-complete
    sequence.  Rather than assert that, this measures it, so a consumer can
    tell a complete path from a degraded one without re-deriving the rule.
    """
    bars = [dict(row) for row in entries]
    if not bars:
        return {
            "path_version": M5_PATH_VERSION,
            "status": PATH_EMPTY,
            "bar_count": 0,
            "bars_with_open": 0,
            "ordering_violations": 0,
            "gaps_detected": 0,
            "first_bar_time_utc": None,
            "last_bar_time_utc": None,
            "is_gap_free": False,
            "is_ohlc_complete": False,
        }

    ordering_violations = 0
    gaps = 0
    for previous, current in zip(bars, bars[1:]):
        delta = int(current["bar_time_utc"]) - int(previous["bar_time_utc"])
        if delta <= 0:
            ordering_violations += 1
        elif delta > M5_BAR_INTERVAL_S:
            gaps += 1

    with_open = sum(1 for row in bars if row.get("open_available"))
    complete = (
        ordering_violations == 0 and gaps == 0 and with_open == len(bars)
    )
    return {
        "path_version": M5_PATH_VERSION,
        "status": PATH_COMPLETE if complete else PATH_DEGRADED,
        "bar_count": len(bars),
        "bars_with_open": with_open,
        "ordering_violations": ordering_violations,
        "gaps_detected": gaps,
        "first_bar_time_utc": int(bars[0]["bar_time_utc"]),
        "last_bar_time_utc": int(bars[-1]["bar_time_utc"]),
        "is_gap_free": gaps == 0 and ordering_violations == 0,
        "is_ohlc_complete": with_open == len(bars),
    }


def build_lifecycle_m5_path(
    entries: Iterable[Mapping[str, Any]],
    *,
    shadow_trade_id: str,
    canonical_opportunity_id: str,
    trade_horizon: str,
    entry_market_time_utc: int,
    exit_market_time_utc: int,
    symbol: str,
) -> dict[str, Any]:
    """Bind an evaluated M5 OHLC sequence to one lifecycle (ROOT-04).

    The governed EX2 defect is not absent market data — it is absent
    *lifecycle binding*: the bars existed, but nothing proved which lifecycle
    they belonged to, so consumers could only attach them post hoc with a
    (symbol, ts) range join.  This block carries that proof in the producer:
    the identity triple, the symbol, and the exact entry/exit interval the
    bars were evaluated over.
    """
    bars = [dict(row) for row in entries]
    summary = summarise_m5_path(bars)
    identity = {
        "shadow_trade_id": shadow_trade_id,
        "canonical_opportunity_id": canonical_opportunity_id,
        "trade_horizon": trade_horizon,
    }
    payload: dict[str, Any] = {
        # ── Contract identity ───────────────────────────────────────────
        "path_version": M5_PATH_VERSION,
        "observation_requirement_id": "OR-14",
        "question_served": ["EX2"],

        # ── Canonical lifecycle identity (exact, never re-derived) ─────
        "lifecycle_identity": identity,
        "identity_semantics": (
            "Exact canonical lifecycle identity triple. The path is bound to "
            "this lifecycle by the PRODUCER at evaluation time; the identity "
            "is never reconstructed from a range join or a proxy key."
        ),
        "symbol": symbol,

        # ── Timeframe identity + per-bar timestamp semantics ───────────
        "timeframe": "M5",
        "timeframe_identity": {
            "timeframe": "M5",
            "bar_interval_seconds": M5_BAR_INTERVAL_S,
            "bar_alignment_modulus_seconds": M5_BAR_INTERVAL_S,
            "timebase": "UTC",
        },
        "bar_time_semantics": BAR_TIME_SEMANTICS,
        "ordering_contract": ORDERING_CONTRACT,

        # ── Normalization semantics/version (ROOT-03, restated) ───────
        "market_timestamp_semantics": MARKET_TIMESTAMP_SEMANTICS,
        "market_timestamp_normalization_version": (
            MARKET_TIMESTAMP_NORMALIZATION_VERSION
        ),
        "normalization_applied_by_producer": False,
        "normalization_note": (
            "Bar times are already canonical UTC. The broker/server-to-UTC "
            "conversion happened once upstream in the MT5 feed and is recorded "
            "as provenance only; it is never re-applied here."
        ),

        # ── Dataset / schema generation + producer provenance ──────────
        "dataset": SHADOW_RUNTIME_DATASET,
        "dataset_version": SHADOW_RUNTIME_DATASET_VERSION,
        "schema_generation": SHADOW_RUNTIME_SCHEMA_GENERATION,
        "producer": SHADOW_RUNTIME_PRODUCER,
        "producer_version": SHADOW_RUNTIME_PRODUCER_VERSION,

        # ── Interval the bars are bound to ─────────────────────────────
        "interval": {
            "entry_market_time_utc": int(entry_market_time_utc),
            "exit_market_time_utc": int(exit_market_time_utc),
            "bound": "EVALUATED_BY_THIS_LIFECYCLE",
        },

        # ── Lineage: lifecycle -> path ─────────────────────────────────
        "lineage": {
            "lifecycle_identity": identity,
            "bound_by": "core/shadow/runtime.py",
            "binding_method": "PRODUCER_AUTHORED_AT_BAR_EVALUATION",
            "range_join_required": False,
            "reconstruction_forbidden": (
                "A (symbol, ts) range-join reconstruction is NOT a substitute "
                "for this block and is permanently forbidden for EX2."
            ),
        },

        # ── Path completeness state ────────────────────────────────────
        "path_completeness_state": summary["status"],
        "completeness": summary,
        "bars": bars,
        "semantics": (
            "Ordered M5 OHLC bars this lifecycle actually evaluated, recorded "
            "by the producer at evaluation time. Binding is producer-authored, "
            "not reconstructed from a (symbol, ts) range join."
        ),
        "historical_synthetic_paths": "FORBIDDEN: this block is produced only "
                                      "from bars actually evaluated by this "
                                      "lifecycle; no path is ever synthesised.",
    }
    payload["path_digest"] = canonical_digest(payload)
    return payload


#: The audited EX2 contract field set.  Every one must be present on a path
#: block before the EX2 contract can be considered complete.  This is the
#: machine-readable definition of "the audited EX2 fields", used by the
#: validator and by the control-plane contract audit.
EX2_REQUIRED_CONTRACT_FIELDS: tuple[str, ...] = (
    "path_version",
    "observation_requirement_id",
    "lifecycle_identity",
    "identity_semantics",
    "symbol",
    "timeframe",
    "timeframe_identity",
    "bar_time_semantics",
    "ordering_contract",
    "market_timestamp_semantics",
    "market_timestamp_normalization_version",
    "normalization_applied_by_producer",
    "dataset",
    "dataset_version",
    "schema_generation",
    "producer",
    "producer_version",
    "interval",
    "lineage",
    "path_completeness_state",
    "completeness",
    "bars",
    "path_digest",
)

#: Per-bar fields EX2 requires on EVERY bar of the path.
EX2_REQUIRED_BAR_FIELDS: tuple[str, ...] = (
    "bar_time_utc",
    "bar_time_utc_iso8601",
    "bar_time_semantics",
    "bar_interval_seconds",
    "open",
    "high",
    "low",
    "close",
    "open_available",
    "bar_index",
)


def ex2_contract_errors(path_block: Mapping[str, Any] | None) -> list[str]:
    """Fail-closed validation of a persisted EX2 lifecycle M5 path block.

    Returns a list of problems; empty means the contract is complete.  The
    checks are strict on purpose: EX2's governed threshold is 100% of
    lifecycles carrying a COMPLETE, ordered, lifecycle-bound OHLC path, so a
    silently incomplete block must be detectable rather than tolerated.
    """
    problems: list[str] = []
    if not isinstance(path_block, Mapping):
        return ["EX2_PATH_BLOCK_MISSING"]

    for field in EX2_REQUIRED_CONTRACT_FIELDS:
        if field not in path_block:
            problems.append(f"EX2_REQUIRED_FIELD_MISSING:{field}")

    recorded = path_block.get("path_digest")
    if recorded:
        body = {k: v for k, v in path_block.items() if k != "path_digest"}
        if canonical_digest(body) != recorded:
            problems.append("EX2_PATH_DIGEST_MISMATCH")

    # Canonical lifecycle identity must be exact and complete.
    identity = path_block.get("lifecycle_identity")
    if not isinstance(identity, Mapping):
        problems.append("EX2_LIFECYCLE_IDENTITY_MISSING")
    else:
        for key in ("shadow_trade_id", "canonical_opportunity_id",
                    "trade_horizon"):
            if not str(identity.get(key) or "").strip():
                problems.append(f"EX2_LIFECYCLE_IDENTITY_INCOMPLETE:{key}")

    # Timestamp semantics and normalization must be explicit.
    if path_block.get("bar_time_semantics") != BAR_TIME_SEMANTICS:
        problems.append("EX2_BAR_TIME_SEMANTICS_MISSING")
    if not str(
        path_block.get("market_timestamp_normalization_version") or ""
    ).strip():
        problems.append("EX2_NORMALIZATION_VERSION_MISSING")

    # Dataset / schema generation / producer provenance must be stamped.
    if path_block.get("dataset_version") != SHADOW_RUNTIME_DATASET_VERSION:
        problems.append("EX2_DATASET_VERSION_MISMATCH")
    if int(path_block.get("schema_generation") or 0) < (
            SHADOW_RUNTIME_SCHEMA_GENERATION):
        problems.append("EX2_SCHEMA_GENERATION_TOO_LOW")

    # Path completeness state must be present and derived, not asserted.
    state = path_block.get("path_completeness_state")
    if state not in (PATH_COMPLETE, PATH_DEGRADED, PATH_EMPTY):
        problems.append("EX2_PATH_COMPLETENESS_STATE_INVALID")

    bars = path_block.get("bars")
    if not isinstance(bars, list):
        problems.append("EX2_BARS_NOT_A_LIST")
        return problems
    if state == PATH_COMPLETE and not bars:
        problems.append("EX2_COMPLETE_PATH_WITHOUT_BARS")

    previous_time: int | None = None
    for index, bar in enumerate(bars):
        if not isinstance(bar, Mapping):
            problems.append(f"EX2_BAR_NOT_A_MAPPING:{index}")
            continue
        for field in EX2_REQUIRED_BAR_FIELDS:
            if field not in bar:
                problems.append(f"EX2_BAR_FIELD_MISSING:{index}:{field}")
        try:
            bar_time = int(bar["bar_time_utc"])
        except (KeyError, TypeError, ValueError):
            problems.append(f"EX2_BAR_TIME_INVALID:{index}")
            continue
        if previous_time is not None and bar_time <= previous_time:
            problems.append(f"EX2_BAR_ORDERING_VIOLATION:{index}")
        previous_time = bar_time
        if bar_time % M5_BAR_INTERVAL_S:
            problems.append(f"EX2_BAR_OFF_M5_GRID:{index}")
        for price_field in ("high", "low", "close"):
            if bar.get(price_field) is None:
                problems.append(f"EX2_BAR_PRICE_MISSING:{index}:{price_field}")
        if bar.get("open_available") is False:
            problems.append(f"EX2_BAR_OPEN_UNAVAILABLE:{index}")

    return problems



# ═══════════════════════════════════════════════════════════════════════════
# ROOT-05 — PRODUCER-AUTHORITATIVE EXPERIMENT ARM
# ═══════════════════════════════════════════════════════════════════════════

EXPERIMENT_ARM_POLICY_VERSION = "experiment_arm_policy_v2"
"""Bumped for the completed L7 contract (additive fields only).  The assignment
RULE is unchanged from v1, so this is a producer-version / contract-field
change, not a change in experimental design."""

ARM_SCHEMA_VERSION = "experiment_arm_schema_v2"

#: The complete, closed arm vocabulary.  Nothing else is ever valid.
ARM_CONTROL = "CONTROL"
ARM_CANDIDATE = "CANDIDATE"
EXPERIMENT_ARMS: frozenset[str] = frozenset({ARM_CONTROL, ARM_CANDIDATE})

#: The governed experiment and treatment identities.  The arm label alone is
#: not an experiment: it must be attributable to a named experiment under a
#: named treatment policy, or the label is uninterpretable.
EXPERIMENT_ID = "L7_ADAPTATION_EVIDENCE"
TREATMENT_ID_CONTROL = "BASELINE_POLICY"
TREATMENT_ID_CANDIDATE = "ADAPTATION_CANDIDATE_POLICY"

#: The arm-to-treatment mapping is CLOSED and EXPLICIT.  A consumer never has to
#: guess what CONTROL or CANDIDATE means operationally.
ARM_TO_TREATMENT: dict[str, str] = {
    ARM_CONTROL: TREATMENT_ID_CONTROL,
    ARM_CANDIDATE: TREATMENT_ID_CANDIDATE,
}
ARM_TO_TREATMENT_VERSION = "arm_to_treatment_mapping_v1"

#: The governed assignment mechanism.
ARM_ASSIGNMENT_METHOD = "DETERMINISTIC_IDENTITY_HASH"

#: Machine-readable statement of what the assignment mechanism DOES and DOES NOT
#: guarantee.  Stamped on every arm block so a consumer never has to infer the
#: experimental design from the implementation.
ARM_DESIGN_SEMANTICS: dict[str, Any] = {
    "mechanism": ARM_ASSIGNMENT_METHOD,
    "assignment_probability_per_arm": 0.5,
    "provides": [
        "RANDOM_ASSIGNMENT_IN_EXPLANATION",
        "UNBIASED_ARM_ALLOCATION",
        "EXACT_REPRODUCIBILITY",
        "PRE_OUTCOME_ISSUANCE",
        "OUTCOME_BLINDNESS",
    ],
    "does_not_provide": [
        "STRATIFIED_RANDOMIZATION",
        "GUARANTEED_FINITE_SAMPLE_BALANCE",
        "BLOCK_RANDOMIZATION",
        "CLUSTER_RANDOMIZATION",
        "COVARIATE_ADAPTIVE_RANDOMIZATION",
    ],
    "balance_guarantee": "IN_EXPECTATION_OVER_THE_POPULATION_ONLY",
    "threshold_interpretation": (
        "L7_MIN (control>=100, candidate>=100, cell>=30) are MINIMUM SAMPLE "
        "requirements satisfied by collecting until each arm and cell has "
        "enough observations. They are NOT balance guarantees and are not "
        "implied by the assignment mechanism."
    ),
    "valid_under_question_contract": True,
    "contract_basis": (
        "The L7 question contract requires a producer-issued, pre-outcome, "
        "reproducible CONTROL/CANDIDATE label with a minimum per-arm and "
        "per-cell sample. Deterministic identity hashing satisfies every one "
        "of those requirements."
    ),
}

#: The exact design deficiencies, named explicitly, so a reviewer never has to
#: reconstruct them.  These are ACCEPTED (not blockers) because the L7 contract
#: requires only random/pre-outcome/reproducible assignment plus minimum
#: samples; they are recorded so that if the contract is ever tightened to
#: require stratification or guaranteed balance, the gap is already named.
L7_DESIGN_DEFICIENCIES: tuple[str, ...] = (
    "NO_STRATIFICATION_BY_REGIME_SYMBOL_OR_HORIZON",
    "NO_GUARANTEED_PER_CELL_BALANCE_ON_FINITE_SAMPLES",
    "NO_BLOCK_OR_CLUSTER_STRUCTURE",
    "BALANCE_HOLDS_ONLY_IN_EXPECTATION",
)


def l7_design_deficiencies() -> dict[str, Any]:
    """Explicit statement of what the arm mechanism does not provide."""
    return {
        "mechanism": ARM_ASSIGNMENT_METHOD,
        "deficiencies": list(L7_DESIGN_DEFICIENCIES),
        "blocking": False,
        "rationale": (
            "These are limitations of a random-assignment mechanism, not "
            "contract violations. The L7 contract requires a producer-issued, "
            "pre-outcome, reproducible CONTROL/CANDIDATE label with minimum "
            "per-arm and per-cell samples, all of which this mechanism "
            "satisfies. If a future contract requires stratification or "
            "guaranteed balance, this list is the exact deficiency to close."
        ),
    }

#: Explicit, closed reasons an arm may be UNASSIGNED.  There is no
#: "default to CONTROL" path: a lifecycle that cannot be assigned stays
#: UNASSIGNED and the L7 runner fails closed on it.
ARM_UNASSIGNED_NO_IDENTITY = "UNASSIGNED_NO_CANONICAL_IDENTITY"
ARM_ASSIGNMENT_DISABLED = "UNASSIGNMENT_DISABLED_BY_CONFIG"

#: The issuer recorded in the assignment decision record.
ARM_ISSUER = "core/shadow/runtime.py"


def assign_experiment_arm(
    *,
    canonical_opportunity_id: str,
    trade_horizon: str,
    shadow_trade_id: str = "",
    decision_market_time_utc: int = 0,
    policy_version: str = EXPERIMENT_ARM_POLICY_VERSION,
    enabled: bool = True,
) -> dict[str, Any]:
    """Issue the producer-authoritative experiment arm at OPEN (ROOT-05).

    SCIENTIFIC DESIGN SEMANTICS
    ---------------------------
    The assignment is a deterministic function of the canonical opportunity
    identity, which supplies the two properties L7 actually needs:

      * REPRODUCIBLE - a replay assigns the same arm, so the arm is part of the
        lifecycle definition rather than a post-hoc inference.
      * PRE-OUTCOME - the arm is written into the immutable OPEN event, which
        by construction is persisted before any CLOSE/outcome field exists.  It
        therefore cannot have been contaminated by outcome knowledge.

    Assignment is derived ONLY from the identity.  Chronology, discovery vs.
    validation splits, selection state, promotion state and outcome behaviour
    are all excluded by construction - none of them is an input.

    ON RANDOMIZATION AND BALANCE
    ----------------------------
    Deterministic identity hashing is a valid randomized-assignment mechanism
    in the random-assignment sense: for an unpredictable identity the arm is an
    unbiased coin flip, independent of every covariate, and it is exactly
    reproducible.  What it does NOT provide is STRATIFICATION or a guaranteed
    per-cell balance, because balance is only realised in expectation over the
    population and not guaranteed on any finite sample.  L7's governed
    thresholds (``control >= 100, candidate >= 100, cell >= 30``) are therefore
    stated as MINIMUM SAMPLE requirements, not as balance guarantees: they are
    satisfied by collecting until each arm and cell has enough observations.
    See ``ARM_DESIGN_SEMANTICS`` for the machine-readable statement and
    ``l7_design_deficiencies()`` for the explicit list of properties this
    mechanism does NOT provide.

    Note the deliberate separation from ``schema_version``: that field carries
    the RECORD-STRUCTURE version (``shadow_runtime_v1``) and must never be
    overloaded as an arm.  The arm lives in its own versioned field.
    """
    if not enabled:
        return _unassigned_arm(ARM_ASSIGNMENT_DISABLED, policy_version)
    identity = str(canonical_opportunity_id or "").strip()
    if not identity:
        return _unassigned_arm(ARM_UNASSIGNED_NO_IDENTITY, policy_version)

    # sha256 over (policy, canonical opportunity) - stable, uniform, and
    # independent of every horizon-specific or outcome-specific input.
    seed = f"{policy_version}::{identity}"
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    # A whole byte of a cryptographic digest is uniform over 0..255, so the
    # parity split is an unbiased coin flip with no modulo bias.
    arm = ARM_CONTROL if digest[0] % 2 == 0 else ARM_CANDIDATE
    assignment_id = f"arm::{policy_version}::{identity}"
    return {
        # ── Arm label (closed vocabulary) ─────────────────────────────
        "experiment_arm": arm,
        "arm_schema_version": ARM_SCHEMA_VERSION,

        # ── Experiment / treatment identity ───────────────────────────
        "experiment_id": EXPERIMENT_ID,
        "treatment_id": ARM_TO_TREATMENT[arm],
        "arm_to_treatment_mapping_version": ARM_TO_TREATMENT_VERSION,
        "arm_to_treatment_mapping": dict(ARM_TO_TREATMENT),

        # ── Assignment timing + identity ──────────────────────────────
        "arm_assigned_at_event": "OPEN",
        "arm_assigned_at_utc": _epoch_to_iso(int(decision_market_time_utc))
        if decision_market_time_utc else None,
        "arm_assigned_at_market_time_utc": (
            int(decision_market_time_utc) or None),
        "arm_assignment_id": assignment_id,

        # ── Assignment policy / method ───────────────────────────────
        "arm_policy_version": policy_version,
        "arm_assignment_method": ARM_ASSIGNMENT_METHOD,
        "arm_assignment_digest_byte": digest[0],
        "arm_assignment_digest": canonical_digest(
            {"seed": seed, "digest_byte": digest[0]}),

        # ── Canonical entity identity the arm is bound to ─────────────
        "arm_assigned_for": {
            "canonical_opportunity_id": identity,
            "trade_horizon": str(trade_horizon or ""),
            "shadow_trade_id": str(shadow_trade_id or ""),
        },

        # ── Producer lineage ──────────────────────────────────────────
        "arm_producer": SHADOW_RUNTIME_PRODUCER,
        "arm_producer_version": SHADOW_RUNTIME_PRODUCER_VERSION,
        "arm_dataset": SHADOW_RUNTIME_DATASET,
        "arm_dataset_version": SHADOW_RUNTIME_DATASET_VERSION,
        "arm_schema_generation": SHADOW_RUNTIME_SCHEMA_GENERATION,

        # ── Pre-outcome attestation ───────────────────────────────────
        "arm_pre_outcome_attestation": {
            "outcome_knowledge_at_assignment": "NONE",
            "assigned_before_any_outcome_field_exists": True,
            "assigned_at_event": "OPEN",
            "outcome_fields_present_at_assignment": False,
            "derivation_inputs": [
                "arm_policy_version", "canonical_opportunity_id",
            ],
            "outcome_derived_inputs": [],
            "verdict": "PRE_OUTCOME_ASSIGNMENT",
        },

        # ── Experimental design semantics ─────────────────────────────
        "arm_design_semantics": ARM_DESIGN_SEMANTICS,

        "arm_unassigned_reason": None,
    }



def _unassigned_arm(reason: str, policy_version: str) -> dict[str, Any]:
    """An explicitly UNASSIGNED arm.

    There is deliberately no "default to CONTROL" path: a lifecycle that cannot
    be assigned stays UNASSIGNED with a named reason and the L7 runner fails
    closed on it.  The experiment/treatment/policy identity is still recorded,
    because the ATTEMPT is governed even when the assignment did not happen.
    """
    return {
        "experiment_arm": None,
        "arm_schema_version": ARM_SCHEMA_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "treatment_id": None,
        "arm_to_treatment_mapping_version": ARM_TO_TREATMENT_VERSION,
        "arm_to_treatment_mapping": dict(ARM_TO_TREATMENT),
        "arm_assigned_at_event": "OPEN",
        "arm_assigned_at_utc": None,
        "arm_assigned_at_market_time_utc": None,
        "arm_assignment_id": None,
        "arm_policy_version": policy_version,
        "arm_assignment_method": None,
        "arm_assignment_digest_byte": None,
        "arm_assignment_digest": None,
        "arm_assigned_for": {},
        "arm_producer": SHADOW_RUNTIME_PRODUCER,
        "arm_producer_version": SHADOW_RUNTIME_PRODUCER_VERSION,
        "arm_dataset": SHADOW_RUNTIME_DATASET,
        "arm_dataset_version": SHADOW_RUNTIME_DATASET_VERSION,
        "arm_schema_generation": SHADOW_RUNTIME_SCHEMA_GENERATION,
        "arm_pre_outcome_attestation": {
            "outcome_knowledge_at_assignment": "NONE",
            "assigned_before_any_outcome_field_exists": True,
            "assigned_at_event": "OPEN",
            "outcome_fields_present_at_assignment": False,
            "derivation_inputs": [],
            "outcome_derived_inputs": [],
            "verdict": "NO_ASSIGNMENT_ATTEMPTED",
        },
        "arm_design_semantics": ARM_DESIGN_SEMANTICS,
        "arm_unassigned_reason": reason,
    }


#: The complete, audited L7 contract field set.  Every field must be present on
#: an arm block before the L7 contract can be considered complete.
L7_REQUIRED_CONTRACT_FIELDS: tuple[str, ...] = (
    "experiment_arm",
    "arm_schema_version",
    "experiment_id",
    "treatment_id",
    "arm_to_treatment_mapping_version",
    "arm_to_treatment_mapping",
    "arm_assigned_at_event",
    "arm_assigned_at_utc",
    "arm_assigned_at_market_time_utc",
    "arm_assignment_id",
    "arm_policy_version",
    "arm_assignment_method",
    "arm_assignment_digest_byte",
    "arm_assignment_digest",
    "arm_assigned_for",
    "arm_producer",
    "arm_producer_version",
    "arm_dataset",
    "arm_dataset_version",
    "arm_schema_generation",
    "arm_pre_outcome_attestation",
    "arm_design_semantics",
    "arm_unassigned_reason",
)


def experiment_arm_errors(arm_block: Mapping[str, Any] | None) -> list[str]:
    """Fail-closed validation of a persisted ROOT-05 arm block.

    A missing, unknown or non-CONTROL/CANDIDATE arm is an error, never a
    default.  In particular a dataset schema identity such as
    ``shadow_runtime_v1`` presented as an arm is rejected - that exact
    confusion is the semantic collision ROOT-05 exists to remove.

    The full audited L7 contract is checked: experiment/treatment identity,
    assignment identity and timing, policy/method, canonical entity identity,
    producer lineage and the pre-outcome attestation.
    """
    if not isinstance(arm_block, Mapping):
        return ["EXPERIMENT_ARM_BLOCK_MISSING"]
    if "experiment_arm" not in arm_block:
        return ["EXPERIMENT_ARM_FIELD_MISSING"]

    problems: list[str] = []
    for field in L7_REQUIRED_CONTRACT_FIELDS:
        if field not in arm_block:
            problems.append(f"L7_REQUIRED_FIELD_MISSING:{field}")

    arm = arm_block.get("experiment_arm")
    if arm is None:
        return problems + [
            "EXPERIMENT_ARM_UNASSIGNED:"
            + str(arm_block.get("arm_unassigned_reason") or "REASON_MISSING")
        ]
    if not isinstance(arm, str) or arm not in EXPERIMENT_ARMS:
        return problems + [f"EXPERIMENT_ARM_UNKNOWN:{arm!r}"]

    if str(arm_block.get("arm_schema_version") or "") != ARM_SCHEMA_VERSION:
        problems.append("EXPERIMENT_ARM_SCHEMA_VERSION_MISMATCH")

    # Experiment / treatment identity must be present and consistent.
    if str(arm_block.get("experiment_id") or "") != EXPERIMENT_ID:
        problems.append("L7_EXPERIMENT_ID_MISMATCH")
    expected_treatment = ARM_TO_TREATMENT.get(str(arm))
    if str(arm_block.get("treatment_id") or "") != str(expected_treatment):
        problems.append("L7_TREATMENT_ID_MISMATCH")

    # Assignment identity must be present and deterministic.
    if not str(arm_block.get("arm_assignment_id") or "").strip():
        problems.append("L7_ASSIGNMENT_ID_MISSING")
    if not str(arm_block.get("arm_policy_version") or "").strip():
        problems.append("L7_POLICY_VERSION_MISSING")
    if str(arm_block.get("arm_assignment_method") or "") != (
            ARM_ASSIGNMENT_METHOD):
        problems.append("L7_ASSIGNMENT_METHOD_UNEXPECTED")

    # Canonical entity identity the arm is bound to.
    assigned_for = arm_block.get("arm_assigned_for")
    if not isinstance(assigned_for, Mapping):
        problems.append("L7_ASSIGNED_FOR_MISSING")
    elif not str(assigned_for.get("canonical_opportunity_id") or "").strip():
        problems.append("L7_CANONICAL_ENTITY_IDENTITY_MISSING")

    # Producer lineage.
    if not str(arm_block.get("arm_producer") or "").strip():
        problems.append("L7_PRODUCER_LINEAGE_MISSING")

    # Pre-outcome attestation.
    attestation = arm_block.get("arm_pre_outcome_attestation")
    if not isinstance(attestation, Mapping):
        problems.append("L7_PRE_OUTCOME_ATTESTATION_MISSING")
    else:
        if str(attestation.get("outcome_knowledge_at_assignment") or "") != (
                "NONE"):
            problems.append("EXPERIMENT_ARM_OUTCOME_CONTAMINATION_RISK")
        if attestation.get("outcome_fields_present_at_assignment") is not False:
            problems.append("L7_OUTCOME_FIELDS_PRESENT_AT_ASSIGNMENT")
        if attestation.get("outcome_derived_inputs"):
            problems.append("L7_ARM_DERIVED_FROM_OUTCOME_INPUT")

    return problems
