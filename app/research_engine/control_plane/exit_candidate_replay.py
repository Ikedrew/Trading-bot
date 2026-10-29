"""Deterministic candidate replay over the governed SHADOW_BASELINE_V1 population.

Implements exactly the nine HD09-frozen exit candidate policies
(``CANDIDATE_POLICIES_V1``: three TRAILING, three REDUCED_TP, three TIME_CAP)
as a strict overlay on successfully reproduced ``SHADOW_BASELINE_V1``
lifecycles.  No scientific choice is introduced here: policy vocabulary,
parameters, barrier ordering, MFE semantics, exit-reason tokens, row grain,
and digest rules all come verbatim from ``HD09_ADJUDICATED_CONTRACT``.

Guarantees
----------
* One row per pairing identity ``(shadow_trade_id, canonical_opportunity_id,
  trade_horizon)`` x candidate policy; cluster identity is
  ``canonical_opportunity_id``; duplicates and account fanout fail closed.
* A lifecycle enters candidate evaluation ONLY after the supplied governed
  reproduction record is re-verified by a fresh ``reproduce_shadow_baseline_v1``
  rerun whose digest equals the supplied ``reproduction_digest``.
* Candidate replay never extends beyond the reproduced baseline path (every
  policy keeps the original SL, the original TP except for the reduced-TP
  cap, and the baseline timeout; the time cap is
  ``min(candidate bar_cap, baseline_timeout_bars)``).
* Fail-closed with stable machine-readable reasons.  Check precedence (first
  failing check wins, one reason per outcome)::

      1.  record lifecycle-identity self-consistency  -> CANDIDATE_LIFECYCLE_IDENTITY_CONFLICT
      2.  frozen policy id resolution                -> CANDIDATE_POLICY_IDENTITY_INVALID
      3.  exact frozen policy parameters             -> CANDIDATE_POLICY_PARAMETERS_MISMATCH
      4.  direction / SL / TP geometry               -> CANDIDATE_GEOMETRY_INVALID
      5.  ordered M5 path (non-empty, timestamps)    -> CANDIDATE_PATH_INSUFFICIENT
          ordered-bar OHLC possibility               -> CANDIDATE_GEOMETRY_INVALID
      6.  baseline reproduction chain: missing/ineligible/fresh-rerun
                                                       -> BASELINE_REPRODUCTION_REQUIRED
          path/reproduction binding mismatch          -> CANDIDATE_LIFECYCLE_IDENTITY_CONFLICT
          supplied reproduction digest mismatch       -> CANDIDATE_PROVENANCE_FAILURE
      7.  no exit inside the reproduced path          -> CANDIDATE_PATH_INSUFFICIENT
      8.  non-finite candidate outputs                -> CANDIDATE_NON_FINITE_RESULT
      9.  digest materialisation failure              -> CANDIDATE_PROVENANCE_FAILURE

* Reorder invariance: population rows are stored in sorted
  ``(lifecycle_identity, frozen policy order)`` sequence and every digest uses
  the order-independent ``evidence_digest``.  Digests are sensitive to OHLC
  (via ``source_path_analytical_digest``), to baseline reproduction identity
  (via ``baseline_replay_digest`` / ``baseline_reproduction_digest``), and to
  frozen policy identity/parameters.
"""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from research_engine.control_plane.evidence_provenance import evidence_digest
from research_engine.control_plane.exit_bar_path import (
    SCHEMA_VERSION as PATH_SCHEMA_VERSION,
    ExitBarPathRecord,
    GovernedExitBarPathEvidence,
)
from research_engine.control_plane.exit_baseline_replay import (
    BASELINE_POLICY_V1,
    ELIGIBLE,
    EXCLUDED,
    HD09_ADJUDICATION_VERSION,
    REPRODUCTION_SCHEMA_VERSION,
    BaselineReproductionRecord,
    GovernedBaselineReproductionPopulation,
    ReproductionDiagnostic,
    reproduce_shadow_baseline_v1,
)
from research_engine.registry.exit_policy_adjudication import (
    CANDIDATE_POLICIES_V1,
    CANDIDATE_REPLAY_CONTRACT,
    COMMON_ANALYTICAL_CONTRACT,
    HD09_ADJUDICATED_CONTRACT,
)
from core.shadow.frozen_trailing_policy import (
    advance_frozen_trailing,
    initialise_frozen_trailing,
)

CANDIDATE_REPLAY_SCHEMA_VERSION = "exit_candidate_replay_v1"

# Stable machine-readable fail-closed reasons.
BASELINE_REPRODUCTION_REQUIRED = "BASELINE_REPRODUCTION_REQUIRED"
POLICY_IDENTITY_INVALID = "CANDIDATE_POLICY_IDENTITY_INVALID"
POLICY_PARAMETERS_MISMATCH = "CANDIDATE_POLICY_PARAMETERS_MISMATCH"
GEOMETRY_INVALID = "CANDIDATE_GEOMETRY_INVALID"
PATH_INSUFFICIENT = "CANDIDATE_PATH_INSUFFICIENT"
NON_FINITE_RESULT = "CANDIDATE_NON_FINITE_RESULT"
PROVENANCE_FAILURE = "CANDIDATE_PROVENANCE_FAILURE"
IDENTITY_CONFLICT = "CANDIDATE_LIFECYCLE_IDENTITY_CONFLICT"

CANDIDATE_EXIT_REASONS = ("stop_loss", "take_profit", "timeout", "time_cap")
BASELINE_TIMEOUT_REASON = "timeout"
CANDIDATE_TIME_CAP_REASON = "time_cap"

# --- Import-time guards: fail loudly if the frozen HD09 vocabulary drifts. ---
_EXPECTED_POLICY_LAYOUT: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("TRAIL_ACT_0_25R_DIST_0_10R_V1", "TRAILING", {"activation_r": 0.25, "distance_r": 0.10}),
    ("TRAIL_ACT_0_50R_DIST_0_25R_V1", "TRAILING", {"activation_r": 0.50, "distance_r": 0.25}),
    ("TRAIL_ACT_1_00R_DIST_0_50R_V1", "TRAILING", {"activation_r": 1.00, "distance_r": 0.50}),
    ("REDUCED_TP_0_50R_V1", "REDUCED_TP", {"target_cap_r": 0.50}),
    ("REDUCED_TP_1_00R_V1", "REDUCED_TP", {"target_cap_r": 1.00}),
    ("REDUCED_TP_1_50R_V1", "REDUCED_TP", {"target_cap_r": 1.50}),
    ("TIME_CAP_20_BARS_V1", "TIME_CAP", {"bar_cap": 20}),
    ("TIME_CAP_60_BARS_V1", "TIME_CAP", {"bar_cap": 60}),
    ("TIME_CAP_180_BARS_V1", "TIME_CAP", {"bar_cap": 180}),
)


def _guard(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(f"exit candidate replay guard failed: {message}")


_guard(
    HD09_ADJUDICATED_CONTRACT.get("candidate_policies") == CANDIDATE_POLICIES_V1,
    "HD09 candidate_policies must be CANDIDATE_POLICIES_V1",
)
_guard(len(CANDIDATE_POLICIES_V1) == 9, "exactly nine frozen candidate policies")
for _policy_id, _policy_type, _params in _EXPECTED_POLICY_LAYOUT:
    _entry = next(
        (item for item in CANDIDATE_POLICIES_V1 if item.get("policy_id") == _policy_id),
        None,
    )
    _guard(_entry is not None, f"frozen policy {_policy_id} missing")
    _guard(_entry.get("policy_type") == _policy_type, f"frozen type drift for {_policy_id}")
    _guard(
        set(_entry) == {"policy_id", "policy_type", *_params}
        and all(
            type(_entry[key]) is type(_params[key]) and _entry[key] == _params[key]
            for key in _params
        ),
        f"frozen parameter drift for {_policy_id}",
    )
_guard(
    tuple(item["policy_id"] for item in CANDIDATE_POLICIES_V1)
    == tuple(item[0] for item in _EXPECTED_POLICY_LAYOUT),
    "frozen candidate order changed",
)
_guard(
    HD09_ADJUDICATED_CONTRACT.get("baseline") == BASELINE_POLICY_V1,
    "baseline policy drift",
)
_guard(
    str(HD09_ADJUDICATED_CONTRACT.get("version")) == HD09_ADJUDICATION_VERSION,
    "HD09 adjudication version drift",
)

_candidate_contract = CANDIDATE_REPLAY_CONTRACT
_guard(_candidate_contract.get("vocabulary_closed") is True, "vocabulary must be closed")
_guard(
    _candidate_contract.get("parameter_search_forbidden") is True,
    "parameter search must be forbidden",
)
_guard(
    _candidate_contract.get("outcome_driven_expansion_forbidden") is True,
    "outcome-driven expansion must be forbidden",
)
_common = _candidate_contract.get("common", {})
_guard(_common.get("original_sl_active") is True, "original SL must stay active")
_guard(
    _common.get("original_tp_active_except_reduced_tp_cap") is True,
    "original TP must stay active except under the reduced-TP cap",
)
_guard(_common.get("baseline_timeout_active") is True, "baseline timeout must stay active")
_guard(
    str(_common.get("path_boundary", "")).startswith(
        "candidate replay may not extend beyond the reproduced baseline path"
    ),
    "candidate path boundary drift",
)
_trailing = _candidate_contract.get("trailing", {})
_guard(_trailing.get("effective_next_bar") is True, "trailing stop must be effective next bar")
_guard(
    _trailing.get("activation_bar_rule")
    == "a stop computed from a bar cannot trigger within that same bar",
    "trailing activation-bar rule drift",
)
_guard(
    _trailing.get("same_bar_trailing_tp_collision")
    == "effective protective stop first, then original TP",
    "trailing same-bar ordering drift",
)
_guard(
    _trailing.get("monotonic")
    == "BUY trailing level never decreases; SELL trailing level never increases",
    "trailing monotonicity drift",
)
_guard(
    str(_trailing.get("favourable_extreme", "")).startswith(
        "BUY uses completed-bar high; SELL uses completed-bar low"
    ),
    "trailing favourable-extreme rule drift",
)
_reduced = _candidate_contract.get("reduced_tp", {})
_guard(
    _reduced.get("barrier_order") == "original SL first, capped TP second, baseline timeout third",
    "reduced-TP barrier order drift",
)
_guard(
    str(_reduced.get("target", "")).startswith(
        "The closer of original TP and entry +/- target_cap_r*initial_risk_distance"
    ),
    "reduced-TP target rule drift",
)
_time_cap = _candidate_contract.get("time_cap", {})
_guard(
    _time_cap.get("effective_cap") == "min(candidate bar_cap, baseline_timeout_bars)",
    "time-cap effective-cap rule drift",
)
_guard(
    _time_cap.get("cap_bar_order")
    == "original SL first, original TP second, then cap exit at bar close",
    "time-cap bar ordering drift",
)
_guard(
    _time_cap.get("bar_count") == "first post-entry completed M5 bar is bar 1",
    "time-cap bar counting drift",
)
_guard(
    "the canonical reason remains timeout" in str(_time_cap.get("exit_reason", "")),
    "time-cap exit-reason rule drift",
)
_analytical = COMMON_ANALYTICAL_CONTRACT
_guard(
    _analytical.get("row_grain") == "one completed lifecycle x one candidate policy",
    "row grain drift",
)
_guard(
    _analytical.get("pairing_identity")
    == ("shadow_trade_id", "canonical_opportunity_id", "trade_horizon"),
    "pairing identity drift",
)
_guard(
    _analytical.get("cluster_identity") == "canonical_opportunity_id",
    "cluster identity drift",
)
_guard(
    _analytical.get("account_fanout_rule")
    == "account/broker executions do not create exit-policy observations",
    "account fanout rule drift",
)
_guard(
    _analytical.get("duplicate_rule")
    == "one row per pairing identity and candidate; duplicates fail closed",
    "duplicate rule drift",
)

# Public frozen registry (never mutated; entries are plain scalar dicts).
CANDIDATE_POLICIES: tuple[Mapping[str, Any], ...] = tuple(CANDIDATE_POLICIES_V1)
CANDIDATE_POLICY_IDS: tuple[str, ...] = tuple(item["policy_id"] for item in CANDIDATE_POLICIES)
CANDIDATE_POLICY_COUNT: int = len(CANDIDATE_POLICIES)
CANDIDATE_POLICY_BY_ID: dict[str, Mapping[str, Any]] = {
    item["policy_id"]: item for item in CANDIDATE_POLICIES
}
CANDIDATE_POLICY_ORDER: dict[str, int] = {
    item["policy_id"]: index for index, item in enumerate(CANDIDATE_POLICIES)
}

@dataclass(frozen=True)
class CandidateReplayResult:
    """One grain row: one reproduced lifecycle x one frozen candidate policy."""

    schema_version: str
    lifecycle_identity: tuple[str, str, str]
    canonical_opportunity_id: str
    canonical_symbol: str
    trade_horizon: str
    candidate_policy: dict[str, Any]
    candidate_policy_id: str
    candidate_policy_type: str
    exit_reason: str
    exit_utc_epoch_s: int
    exit_price: float
    bars_held: int
    candidate_r: float
    candidate_mfe_r: float
    baseline_window_mfe_r: float
    baseline_replay_digest: str
    baseline_reproduction_digest: str
    source_path_analytical_digest: str
    hd09_adjudication_version: str
    candidate_replay_digest: str
    eligibility_state: str = ELIGIBLE

    def _digest_material(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "hd09_adjudication_version": self.hd09_adjudication_version,
            "candidate_policy": dict(self.candidate_policy),
            "candidate_policy_id": self.candidate_policy_id,
            "candidate_policy_type": self.candidate_policy_type,
            "lifecycle_identity": list(self.lifecycle_identity),
            "canonical_opportunity_id": self.canonical_opportunity_id,
            "canonical_symbol": self.canonical_symbol,
            "trade_horizon": self.trade_horizon,
            "exit_reason": self.exit_reason,
            "exit_utc_epoch_s": self.exit_utc_epoch_s,
            "exit_price": self.exit_price,
            "bars_held": self.bars_held,
            "candidate_r": self.candidate_r,
            "candidate_mfe_r": self.candidate_mfe_r,
            "baseline_window_mfe_r": self.baseline_window_mfe_r,
            "baseline_replay_digest": self.baseline_replay_digest,
            "baseline_reproduction_digest": self.baseline_reproduction_digest,
            "source_path_analytical_digest": self.source_path_analytical_digest,
            "eligibility_state": self.eligibility_state,
        }

    def analytical_record(self) -> dict[str, Any]:
        material = self._digest_material()
        material["candidate_replay_digest"] = self.candidate_replay_digest
        return material


def candidate_replay_digest(record: CandidateReplayResult) -> str:
    """Recompute a row digest from its fields (integrity verification hook)."""
    return evidence_digest((record._digest_material(),))


@dataclass(frozen=True)
class CandidateReplayExclusion:
    """One grain failure: lifecycle identity x candidate policy, stable reason."""

    lifecycle_identity: tuple[str, str, str]
    canonical_symbol: str
    trade_horizon: str
    candidate_policy_id: str | None
    reason: str
    diagnostics: tuple[ReproductionDiagnostic, ...]
    path_analytical_digest: str | None
    exclusion_digest: str
    eligibility_state: str = EXCLUDED

    def record(self) -> dict[str, Any]:
        return {
            "lifecycle_identity": list(self.lifecycle_identity),
            "canonical_symbol": self.canonical_symbol,
            "trade_horizon": self.trade_horizon,
            "candidate_policy_id": self.candidate_policy_id,
            "reason": self.reason,
            "diagnostics": [item.record() for item in self.diagnostics],
            "path_analytical_digest": self.path_analytical_digest,
            "exclusion_digest": self.exclusion_digest,
            "eligibility_state": self.eligibility_state,
        }


@dataclass(frozen=True)
class CandidateReplaySummary:
    input_path_lifecycles: int
    upstream_path_exclusions: int
    input_reproduced_lifecycles: int
    upstream_reproduction_exclusions: int
    candidate_eligible_lifecycles: int
    candidate_rows: int
    maximum_policy_count: int
    expected_maximum_candidate_rows: int
    rows_by_policy: dict[str, int]
    candidate_exclusions: int
    exclusions_by_reason: dict[str, int]
    distinct_eligible_opportunities: int


@dataclass(frozen=True)
class CandidateReplayPopulation:
    schema_version: str
    records: tuple[CandidateReplayResult, ...]
    exclusions: tuple[CandidateReplayExclusion, ...]
    summary: CandidateReplaySummary
    provenance: dict[str, Any]

    def analytical_record(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "records": [item.analytical_record() for item in self.records],
            "exclusions": [item.record() for item in self.exclusions],
        }


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    number = _finite(value)
    if number is None or int(number) != number:
        return None
    return int(number)


def _diagnostic(
    field: str, observed: Any, expected: Any, rule: str,
) -> ReproductionDiagnostic:
    return ReproductionDiagnostic(field, observed, expected, rule)


def _identity_issues(record: ExitBarPathRecord) -> tuple[ReproductionDiagnostic, ...]:
    identity = record.lifecycle_identity
    if (
        not isinstance(identity, tuple) or len(identity) != 3
        or not all(isinstance(item, str) and item for item in identity)
        or identity[1] != record.canonical_opportunity_id
        or identity[2] != record.trade_horizon
    ):
        return (_diagnostic(
            "lifecycle_identity",
            list(identity) if isinstance(identity, tuple) else identity,
            None,
            "valid canonical pairing identity (shadow_trade_id, canonical_opportunity_id, "
            "trade_horizon) bound to the record fields",
        ),)
    return ()


def _geometry_issues(record: ExitBarPathRecord) -> tuple[ReproductionDiagnostic, ...]:
    issues: list[ReproductionDiagnostic] = []
    if record.direction not in {"BUY", "SELL"}:
        issues.append(_diagnostic("direction", record.direction, None, "BUY or SELL"))
    entry = _finite(record.entry_price)
    stop = _finite(record.baseline_stop_loss)
    target = _finite(record.baseline_take_profit)
    if entry is None or stop is None or target is None or entry <= 0 or stop <= 0 or target <= 0:
        issues.append(_diagnostic(
            "baseline_geometry", (entry, stop, target), None,
            "finite positive entry, SL, and TP",
        ))
    elif not (
        (record.direction == "BUY" and stop < entry < target)
        or (record.direction == "SELL" and target < entry < stop)
    ):
        issues.append(_diagnostic(
            "baseline_geometry", (entry, stop, target), None,
            "direction-consistent non-zero initial risk geometry",
        ))
    return tuple(issues)


def _ordered_path_issues(record: ExitBarPathRecord) -> tuple[ReproductionDiagnostic, ...]:
    bars = record.ordered_m5_bars
    if not bars:
        return (_diagnostic("ordered_m5_bars", 0, None, "non-empty M5 path"),)
    entry_utc = _integer(record.entry_utc_epoch_s)
    if entry_utc is None:
        return (_diagnostic(
            "entry_utc_epoch_s", record.entry_utc_epoch_s, None,
            "integer UTC epoch-second entry boundary",
        ),)
    previous = entry_utc * 1000
    issues: list[ReproductionDiagnostic] = []
    for index, bar in enumerate(bars, 1):
        timestamp = _integer(bar.timestamp_utc_ms)
        if timestamp is None or timestamp % 1000 or timestamp <= previous:
            issues.append(_diagnostic(
                f"ordered_m5_bars[{index}].timestamp_utc_ms",
                bar.timestamp_utc_ms, None,
                "strictly increasing whole-second post-entry UTC timestamp",
            ))
        if timestamp is not None:
            previous = timestamp
    return tuple(issues)


def _bar_geometry_issues(record: ExitBarPathRecord) -> tuple[ReproductionDiagnostic, ...]:
    issues: list[ReproductionDiagnostic] = []
    for index, bar in enumerate(record.ordered_m5_bars, 1):
        ohlc = tuple(_finite(value) for value in (bar.open, bar.high, bar.low, bar.close))
        valid_geometry = (
            all(value is not None and value > 0 for value in ohlc)
            and ohlc[1] >= max(ohlc[0], ohlc[3])
            and ohlc[2] <= min(ohlc[0], ohlc[3])
            and ohlc[1] >= ohlc[2]
        )
        if not valid_geometry:
            issues.append(_diagnostic(
                f"ordered_m5_bars[{index}].ohlc", ohlc, None,
                "finite positive possible OHLC geometry",
            ))
    return tuple(issues)


def _exact_policy_match(supplied: Mapping[str, Any], frozen: Mapping[str, Any]) -> bool:
    if set(supplied) != set(frozen):
        return False
    return all(
        type(supplied[key]) is type(frozen[key]) and supplied[key] == frozen[key]
        for key in frozen
    )


def _resolve_policy(
    policy: Any,
) -> tuple[Mapping[str, Any] | None, str | None, str | None, tuple[ReproductionDiagnostic, ...]]:
    """Resolve a caller argument against the frozen nine-policy vocabulary."""
    if isinstance(policy, str):
        frozen = CANDIDATE_POLICY_BY_ID.get(policy)
        if frozen is None:
            return None, POLICY_IDENTITY_INVALID, policy, (_diagnostic(
                "candidate_policy_id", policy, sorted(CANDIDATE_POLICY_IDS),
                "one of the nine frozen HD09 candidate policy ids",
            ),)
        return frozen, None, policy, ()
    if isinstance(policy, Mapping):
        supplied_id = policy.get("policy_id")
        public_id = supplied_id if isinstance(supplied_id, str) else None
        frozen = CANDIDATE_POLICY_BY_ID.get(public_id) if public_id is not None else None
        if frozen is None:
            return None, POLICY_IDENTITY_INVALID, public_id, (_diagnostic(
                "candidate_policy_id", supplied_id, sorted(CANDIDATE_POLICY_IDS),
                "frozen HD09 candidate policy id",
            ),)
        if not _exact_policy_match(policy, frozen):
            return None, POLICY_PARAMETERS_MISMATCH, public_id, (_diagnostic(
                "candidate_policy", dict(policy), dict(frozen),
                "exact frozen HD09 candidate policy entry (identity, type, and parameters)",
            ),)
        return frozen, None, public_id, ()
    return None, POLICY_IDENTITY_INVALID, None, (_diagnostic(
        "candidate_policy", type(policy).__name__, None,
        "frozen policy id string or exact frozen policy mapping",
    ),)


def _exclusion(
    record: ExitBarPathRecord,
    candidate_policy_id: str | None,
    reason: str,
    diagnostics: tuple[ReproductionDiagnostic, ...],
) -> CandidateReplayExclusion:
    identity_value = (
        list(record.lifecycle_identity)
        if isinstance(record.lifecycle_identity, tuple) else record.lifecycle_identity
    )
    digest_value = (
        record.analytical_digest if isinstance(record.analytical_digest, str) else None
    )
    material = {
        "schema_version": CANDIDATE_REPLAY_SCHEMA_VERSION,
        "hd09_adjudication_version": HD09_ADJUDICATION_VERSION,
        "lifecycle_identity": identity_value,
        "candidate_policy_id": candidate_policy_id,
        "reason": reason,
        "diagnostics": [item.record() for item in diagnostics],
        "path_analytical_digest": digest_value,
    }
    return CandidateReplayExclusion(
        lifecycle_identity=record.lifecycle_identity,
        canonical_symbol=record.canonical_symbol,
        trade_horizon=record.trade_horizon,
        candidate_policy_id=candidate_policy_id,
        reason=reason,
        diagnostics=diagnostics,
        path_analytical_digest=digest_value,
        exclusion_digest=evidence_digest((material,)),
    )


@dataclass(frozen=True)
class _Outcome:
    exit_reason: str
    exit_price: float
    bars_held: int
    exit_utc_epoch_s: int
    candidate_r: float
    candidate_mfe_r: float


def _evaluate_outcome(
    record: ExitBarPathRecord, policy: Mapping[str, Any],
) -> tuple[_Outcome | None, tuple[ReproductionDiagnostic, ...]]:
    """Evaluate one frozen policy over the governed path.

    Precondition: the record already passed identity/geometry/path validation
    (and, through the public entry points, a fresh governed baseline
    reproduction rerun).  Returns ``(outcome, ())`` or ``(None, diagnostics)``.

    Barrier order on every bar: protective stop first, then TP, then the
    time boundary (baseline timeout, or the time-cap exit).  The trailing
    stop for bar N is computed only from completed bars 1..N-1 (effective
    next bar, monotonic running extreme, original SL always retained).
    """
    sell = record.direction == "SELL"
    entry = float(record.entry_price)
    stop = float(record.baseline_stop_loss)
    target = float(record.baseline_take_profit)
    timeout_bars = int(record.baseline_timeout_bars)
    risk = abs(entry - stop)
    policy_type = policy["policy_type"]

    barrier_tp = target
    if policy_type == "TRAILING":
        activation_r = float(policy["activation_r"])
        distance_r = float(policy["distance_r"])
        trailing_state = initialise_frozen_trailing(
            entry_price=entry, stop_loss=stop, take_profit=target,
            risk_distance=risk, timeout_bars=timeout_bars,
        )
    elif policy_type == "REDUCED_TP":
        cap_r = float(policy["target_cap_r"])
        # The cap only ever moves the target closer to entry.
        barrier_tp = (
            min(target, entry + cap_r * risk) if not sell
            else max(target, entry - cap_r * risk)
        )
    elif policy_type == "TIME_CAP":
        bar_cap = int(policy["bar_cap"])
        effective_cap = min(bar_cap, timeout_bars)
        cap_reason = (
            CANDIDATE_TIME_CAP_REASON if bar_cap < timeout_bars
            else BASELINE_TIMEOUT_REASON
        )
    else:  # pragma: no cover - guarded frozen vocabulary
        return None, (_diagnostic(
            "policy_type", policy_type, None, "frozen candidate policy type",
        ),)

    mfe_extreme = entry
    trail_extreme = entry
    activated = False
    exit_reason: str | None = None
    exit_price: float | None = None
    exit_utc = 0
    bars_held = 0

    for bars_held, bar in enumerate(record.ordered_m5_bars, 1):
        # Baseline-window MFE: full current bar high (BUY) / low (SELL),
        # including the full exit bar range.
        if not sell:
            mfe_extreme = max(mfe_extreme, bar.high)
        else:
            mfe_extreme = min(mfe_extreme, bar.low)

        if policy_type == "TRAILING":
            step = advance_frozen_trailing(
                policy=policy, entry_price=entry, stop_loss=stop,
                take_profit=target, risk_distance=risk,
                direction=record.direction, bar_high=bar.high,
                bar_low=bar.low, bar_close=bar.close,
                prior_state=trailing_state,
            )
            trailing_state = step.state
            if step.terminal:
                exit_reason = step.exit_reason
                exit_price = step.exit_price
                exit_utc = bar.timestamp_utc_ms // 1000
                break
            continue

        # Effective protective stop for this bar: trailing uses only bars
        # completed BEFORE this bar; the original SL is always retained.
        if policy_type == "TRAILING":
            if not activated:
                stop_level = stop
            elif not sell:
                stop_level = max(stop, trail_extreme - distance_r * risk)
            else:
                stop_level = min(stop, trail_extreme + distance_r * risk)
        else:
            stop_level = stop
        tp_level = barrier_tp if policy_type == "REDUCED_TP" else target

        if not sell:
            if bar.low <= stop_level:
                exit_reason, exit_price = "stop_loss", stop_level
            elif bar.high >= tp_level:
                exit_reason, exit_price = "take_profit", tp_level
        else:
            if bar.high >= stop_level:
                exit_reason, exit_price = "stop_loss", stop_level
            elif bar.low <= tp_level:
                exit_reason, exit_price = "take_profit", tp_level
        if exit_price is None:
            if policy_type == "TIME_CAP":
                if bars_held >= effective_cap:
                    exit_reason, exit_price = cap_reason, bar.close
            elif bars_held >= timeout_bars:
                exit_reason, exit_price = BASELINE_TIMEOUT_REASON, bar.close
        if exit_price is not None:
            exit_utc = bar.timestamp_utc_ms // 1000
            break

        # No exit this bar: update trailing state so it is effective NEXT bar.
        if not sell:
            trail_extreme = max(trail_extreme, bar.high)
        else:
            trail_extreme = min(trail_extreme, bar.low)
        if policy_type == "TRAILING" and not activated:
            excursion = (trail_extreme - entry) if not sell else (entry - trail_extreme)
            if excursion >= activation_r * risk:
                activated = True
    else:
        return None, (_diagnostic(
            "replay_exit", None, None,
            "candidate SL, TP, or timeout/cap must occur within the reproduced baseline path",
        ),)

    candidate_r = (exit_price - entry) / risk if not sell else (entry - exit_price) / risk
    candidate_mfe = max(0.0, (
        (mfe_extreme - entry) / risk if not sell else (entry - mfe_extreme) / risk
    ))
    return _Outcome(
        exit_reason=exit_reason,
        exit_price=float(exit_price),
        bars_held=bars_held,
        exit_utc_epoch_s=exit_utc,
        candidate_r=candidate_r,
        candidate_mfe_r=candidate_mfe,
    ), ()


def _verify_reproduction(
    record: ExitBarPathRecord,
    reproduction: BaselineReproductionRecord | None,
) -> tuple[str | None, tuple[ReproductionDiagnostic, ...]]:
    """Verify the governed SHADOW_BASELINE_V1 reproduction chain.

    Returns ``(None, ())`` when the supplied reproduction record is bound to
    this exact path and re-verified by a fresh governed rerun, otherwise the
    stable fail-closed reason and its diagnostics.
    """
    if reproduction is None or not isinstance(reproduction, BaselineReproductionRecord):
        return BASELINE_REPRODUCTION_REQUIRED, (_diagnostic(
            "baseline_reproduction", None, None,
            "governed SHADOW_BASELINE_V1 reproduction record re-verified by fresh rerun",
        ),)
    binding: list[ReproductionDiagnostic] = []
    if reproduction.lifecycle_identity != record.lifecycle_identity:
        binding.append(_diagnostic(
            "lifecycle_identity", list(reproduction.lifecycle_identity),
            list(record.lifecycle_identity) if isinstance(record.lifecycle_identity, tuple)
            else record.lifecycle_identity,
            "exact pairing identity equality with the exit_bar_path_v1 record",
        ))
    if reproduction.canonical_opportunity_id != record.canonical_opportunity_id:
        binding.append(_diagnostic(
            "canonical_opportunity_id", reproduction.canonical_opportunity_id,
            record.canonical_opportunity_id,
            "exact equality with the exit_bar_path_v1 record",
        ))
    if reproduction.canonical_symbol != record.canonical_symbol:
        binding.append(_diagnostic(
            "canonical_symbol", reproduction.canonical_symbol,
            record.canonical_symbol, "exact equality with the exit_bar_path_v1 record",
        ))
    if reproduction.trade_horizon != record.trade_horizon:
        binding.append(_diagnostic(
            "trade_horizon", reproduction.trade_horizon, record.trade_horizon,
            "exact equality with the exit_bar_path_v1 record",
        ))
    if reproduction.path_analytical_digest != record.analytical_digest:
        binding.append(_diagnostic(
            "path_analytical_digest", reproduction.path_analytical_digest,
            record.analytical_digest,
            "reproduction must be bound to this exit_bar_path_v1 analytical digest",
        ))
    if reproduction.replay.source_path_analytical_digest != record.analytical_digest:
        binding.append(_diagnostic(
            "replay_source_path_analytical_digest",
            reproduction.replay.source_path_analytical_digest,
            record.analytical_digest,
            "reproduced replay must be bound to this exit_bar_path_v1 analytical digest",
        ))
    if binding:
        return IDENTITY_CONFLICT, tuple(binding)
    if (
        reproduction.eligibility_state != ELIGIBLE
        or reproduction.replay.replay_eligibility_state != ELIGIBLE
    ):
        return BASELINE_REPRODUCTION_REQUIRED, (_diagnostic(
            "replay_eligibility_state",
            (reproduction.eligibility_state, reproduction.replay.replay_eligibility_state),
            (ELIGIBLE, ELIGIBLE), "eligible governed SHADOW_BASELINE_V1 reproduction",
        ),)
    fresh = reproduce_shadow_baseline_v1(record)
    if not isinstance(fresh, BaselineReproductionRecord):
        fresh_diagnostics = fresh.diagnostics or (_diagnostic(
            "baseline_reproduction_rerun", fresh.reason, None,
            "fresh governed SHADOW_BASELINE_V1 rerun must reproduce the record",
        ),)
        return BASELINE_REPRODUCTION_REQUIRED, fresh_diagnostics
    if fresh.reproduction_digest != reproduction.reproduction_digest:
        return PROVENANCE_FAILURE, (_diagnostic(
            "reproduction_digest", reproduction.reproduction_digest,
            fresh.reproduction_digest,
            "supplied reproduction must equal a fresh governed SHADOW_BASELINE_V1 rerun",
        ),)
    if fresh.replay.replay_digest != reproduction.replay.replay_digest:
        return PROVENANCE_FAILURE, (_diagnostic(
            "replay_digest", reproduction.replay.replay_digest,
            fresh.replay.replay_digest,
            "supplied baseline replay must equal a fresh governed rerun",
        ),)
    return None, ()


def _policy_id_label(policy: Any) -> str | None:
    if isinstance(policy, str):
        return policy
    if isinstance(policy, Mapping):
        supplied = policy.get("policy_id")
        return supplied if isinstance(supplied, str) else None
    return None


def _prepare_candidate(
    record: ExitBarPathRecord, policy: Any,
) -> tuple[
    Mapping[str, Any] | None, str | None, str | None,
    tuple[ReproductionDiagnostic, ...],
]:
    """Local checks: identity, policy vocabulary/params, geometry, ordered path.

    Returns ``(frozen_policy, resolved_id, failure_reason, diagnostics)``;
    ``failure_reason`` is ``None`` exactly when the record and policy passed
    every local check (steps 1-5 of the module precedence order).
    """
    identity_issues = _identity_issues(record)
    if identity_issues:
        return None, _policy_id_label(policy), IDENTITY_CONFLICT, identity_issues

    frozen, failure_reason, supplied_id, policy_diagnostics = _resolve_policy(policy)
    resolved_id = frozen["policy_id"] if frozen is not None else supplied_id
    if failure_reason is not None:
        return None, resolved_id, failure_reason, policy_diagnostics

    geometry_issues = _geometry_issues(record)
    if geometry_issues:
        return None, resolved_id, GEOMETRY_INVALID, geometry_issues

    path_issues = _ordered_path_issues(record)
    if path_issues:
        return None, resolved_id, PATH_INSUFFICIENT, path_issues

    bar_issues = _bar_geometry_issues(record)
    if bar_issues:
        return None, resolved_id, GEOMETRY_INVALID, bar_issues

    return frozen, resolved_id, None, ()


def _finalise_candidate(
    record: ExitBarPathRecord,
    reproduction: BaselineReproductionRecord | None,
    frozen: Mapping[str, Any],
    resolved_id: str | None,
    *,
    skip_reproduction_check: bool,
) -> CandidateReplayResult | CandidateReplayExclusion:
    """Steps 6-9: reproduction chain, evaluation, finiteness, digest material.

    ``skip_reproduction_check`` is only used by the population builder after
    it has already run ``_verify_reproduction`` for this exact record (the
    fresh-rerun result is policy-independent, so it is computed once per
    lifecycle instead of nine times).
    """
    if not skip_reproduction_check:
        reproduction_reason, reproduction_diagnostics = _verify_reproduction(
            record, reproduction,
        )
        if reproduction_reason is not None:
            return _exclusion(
                record, resolved_id, reproduction_reason, reproduction_diagnostics,
            )

    outcome, outcome_diagnostics = _evaluate_outcome(record, frozen)
    if outcome is None:
        return _exclusion(record, resolved_id, PATH_INSUFFICIENT, outcome_diagnostics)

    outputs = (outcome.exit_price, outcome.candidate_r, outcome.candidate_mfe_r)
    if not all(math.isfinite(value) for value in outputs):
        # Non-finite values cannot enter the canonical JSON digest; expose
        # them as stable strings so the exclusion still materialises.
        observed_outputs = tuple(
            value if math.isfinite(value) else repr(value) for value in outputs
        )
        return _exclusion(record, resolved_id, NON_FINITE_RESULT, (_diagnostic(
            "candidate_outputs", observed_outputs, None,
            "finite candidate exit price, R, and MFE R",
        ),))

    assert reproduction is not None  # verified above; narrows for type checkers
    material = {
        "schema_version": CANDIDATE_REPLAY_SCHEMA_VERSION,
        "hd09_adjudication_version": HD09_ADJUDICATION_VERSION,
        "candidate_policy": dict(frozen),
        "candidate_policy_id": frozen["policy_id"],
        "candidate_policy_type": frozen["policy_type"],
        "lifecycle_identity": list(record.lifecycle_identity),
        "canonical_opportunity_id": record.canonical_opportunity_id,
        "canonical_symbol": record.canonical_symbol,
        "trade_horizon": record.trade_horizon,
        "exit_reason": outcome.exit_reason,
        "exit_utc_epoch_s": outcome.exit_utc_epoch_s,
        "exit_price": outcome.exit_price,
        "bars_held": outcome.bars_held,
        "candidate_r": outcome.candidate_r,
        "candidate_mfe_r": outcome.candidate_mfe_r,
        "baseline_window_mfe_r": reproduction.replay.mfe_r,
        "baseline_replay_digest": reproduction.replay.replay_digest,
        "baseline_reproduction_digest": reproduction.reproduction_digest,
        "source_path_analytical_digest": record.analytical_digest,
        "eligibility_state": ELIGIBLE,
    }
    try:
        digest = evidence_digest((material,))
    except (TypeError, ValueError, OverflowError) as error:
        return _exclusion(record, resolved_id, PROVENANCE_FAILURE, (_diagnostic(
            "candidate_replay_digest", repr(error), None,
            "order-independent sha256 digest of the frozen candidate material",
        ),))
    return CandidateReplayResult(
        **{**material, "lifecycle_identity": record.lifecycle_identity},
        candidate_replay_digest=digest,
    )


def replay_candidate_policy(
    record: ExitBarPathRecord,
    reproduction: BaselineReproductionRecord | None,
    policy: Any,
) -> CandidateReplayResult | CandidateReplayExclusion:
    """Replay one frozen candidate policy for one governed lifecycle.

    Deterministic fail-closed pipeline (first failing check wins, exactly one
    stable reason per outcome) - see the module docstring for the full
    precedence order.  ``reproduction`` must be the governed
    ``SHADOW_BASELINE_V1`` reproduction record for this exact path; it is
    re-verified by a fresh rerun before any candidate evaluation.
    """
    frozen, resolved_id, failure_reason, diagnostics = _prepare_candidate(record, policy)
    if failure_reason is not None:
        return _exclusion(record, resolved_id, failure_reason, diagnostics)
    return _finalise_candidate(
        record, reproduction, frozen, resolved_id, skip_reproduction_check=False,
    )


def _validate_path_evidence(evidence: GovernedExitBarPathEvidence) -> None:
    if evidence.schema_version != PATH_SCHEMA_VERSION:
        raise ValueError("Missing governed exit_bar_path_v1 authority")
    expected_analytical = evidence_digest([
        item.analytical_record() for item in evidence.records
    ])
    if evidence.provenance.get("analytical_digest") != expected_analytical:
        raise ValueError("exit_bar_path_v1 analytical provenance failed")
    provenance = dict(evidence.provenance)
    supplied_digest = provenance.pop("digest", None)
    if supplied_digest != evidence_digest((provenance,)):
        raise ValueError("exit_bar_path_v1 population provenance failed")


def _validate_reproduction_population(
    reproduction: GovernedBaselineReproductionPopulation,
    evidence: GovernedExitBarPathEvidence,
) -> None:
    if reproduction.schema_version != REPRODUCTION_SCHEMA_VERSION:
        raise ValueError("Missing governed shadow_baseline_reproduction_v1 authority")
    if (
        reproduction.provenance.get("source_exit_bar_path_provenance_digest")
        != evidence.provenance["digest"]
    ):
        raise ValueError(
            "baseline reproduction population is not bound to the supplied exit_bar_path_v1 evidence"
        )
    expected_successful = evidence_digest([
        item.analytical_record() for item in reproduction.records
    ])
    if reproduction.provenance.get("successful_reproduction_digest") != expected_successful:
        raise ValueError("baseline reproduction analytical provenance failed")
    provenance = dict(reproduction.provenance)
    supplied_digest = provenance.pop("digest", None)
    if supplied_digest != evidence_digest((provenance,)):
        raise ValueError("baseline reproduction population provenance failed")


def _sort_identity(value: Any) -> tuple[str, ...]:
    if isinstance(value, tuple):
        return tuple(str(part) for part in value)
    return (str(value),)


def build_candidate_replay_population(
    evidence: GovernedExitBarPathEvidence,
    reproduction: GovernedBaselineReproductionPopulation,
) -> CandidateReplayPopulation:
    """Build the deterministic nine-candidate replay population.

    Grain: one row per pairing identity
    ``(shadow_trade_id, canonical_opportunity_id, trade_horizon)`` x frozen
    candidate policy (cluster identity ``canonical_opportunity_id``).  Every
    path lifecycle that lacks a verified governed
    ``SHADOW_BASELINE_V1`` reproduction fails closed with nine grain
    exclusions; duplicate pairing identities fail closed at both grains.
    Row and exclusion counts are reported as observed - the expected maximum
    (eligible lifecycles x nine) is informational and never forced.
    """
    _validate_path_evidence(evidence)
    _validate_reproduction_population(reproduction, evidence)

    path_counts = Counter(item.lifecycle_identity for item in evidence.records)
    reproduction_counts = Counter(item.lifecycle_identity for item in reproduction.records)
    reproduction_index: dict[tuple[str, str, str], BaselineReproductionRecord] = {}
    for item in reproduction.records:
        reproduction_index.setdefault(item.lifecycle_identity, item)

    duplicate_rule = "one row per pairing identity and candidate; duplicates fail closed"
    rows: list[CandidateReplayResult] = []
    exclusions: list[CandidateReplayExclusion] = []
    for record in sorted(
        evidence.records, key=lambda item: _sort_identity(item.lifecycle_identity),
    ):
        identity = record.lifecycle_identity
        if path_counts[identity] > 1 or reproduction_counts[identity] > 1:
            duplicate_diagnostic = _diagnostic(
                "lifecycle_identity",
                {
                    "path_records": path_counts[identity],
                    "reproduction_records": reproduction_counts[identity],
                },
                1,
                duplicate_rule,
            )
            for policy in CANDIDATE_POLICIES:
                exclusions.append(_exclusion(
                    record, policy["policy_id"], IDENTITY_CONFLICT, (duplicate_diagnostic,),
                ))
            continue
        reproduction_record = reproduction_index.get(identity)
        verification: tuple[
            str | None, tuple[ReproductionDiagnostic, ...]
        ] | None = None
        for policy in CANDIDATE_POLICIES:
            frozen, resolved_id, failure_reason, diagnostics = _prepare_candidate(
                record, policy,
            )
            if failure_reason is not None:
                exclusions.append(_exclusion(
                    record, resolved_id, failure_reason, diagnostics,
                ))
                continue
            if verification is None:
                # The fresh governed rerun is policy-independent: verify once
                # per lifecycle, then fan out to the nine frozen policies.
                verification = _verify_reproduction(record, reproduction_record)
            verification_reason, verification_diagnostics = verification
            if verification_reason is not None:
                exclusions.append(_exclusion(
                    record, resolved_id, verification_reason, verification_diagnostics,
                ))
                continue
            outcome = _finalise_candidate(
                record, reproduction_record, frozen, resolved_id,
                skip_reproduction_check=True,
            )
            if isinstance(outcome, CandidateReplayResult):
                rows.append(outcome)
            else:
                exclusions.append(outcome)

    rows.sort(key=lambda item: (
        _sort_identity(item.lifecycle_identity),
        CANDIDATE_POLICY_ORDER[item.candidate_policy_id],
    ))
    exclusions.sort(key=lambda item: (
        _sort_identity(item.lifecycle_identity),
        CANDIDATE_POLICY_ORDER.get(item.candidate_policy_id, -1),
        item.reason,
        item.exclusion_digest,
    ))

    row_counts = Counter(item.candidate_policy_id for item in rows)
    reason_counts = Counter(item.reason for item in exclusions)
    eligible_identities = {_sort_identity(item.lifecycle_identity) for item in rows}
    summary = CandidateReplaySummary(
        input_path_lifecycles=len(evidence.records),
        upstream_path_exclusions=len(evidence.exclusions),
        input_reproduced_lifecycles=len(reproduction.records),
        upstream_reproduction_exclusions=len(reproduction.exclusions),
        candidate_eligible_lifecycles=len(eligible_identities),
        candidate_rows=len(rows),
        maximum_policy_count=CANDIDATE_POLICY_COUNT,
        expected_maximum_candidate_rows=len(eligible_identities) * CANDIDATE_POLICY_COUNT,
        rows_by_policy={
            policy_id: row_counts.get(policy_id, 0)
            for policy_id in CANDIDATE_POLICY_IDS
        },
        candidate_exclusions=len(exclusions),
        exclusions_by_reason=dict(sorted(reason_counts.items())),
        distinct_eligible_opportunities=len({
            item.canonical_opportunity_id for item in rows
        }),
    )
    provenance: dict[str, Any] = {
        "schema_version": CANDIDATE_REPLAY_SCHEMA_VERSION,
        "hd09_adjudication_version": HD09_ADJUDICATION_VERSION,
        "baseline_policy": BASELINE_POLICY_V1,
        "candidate_policies": [dict(item) for item in CANDIDATE_POLICIES],
        "row_grain": "one completed lifecycle x one candidate policy",
        "pairing_identity": ["shadow_trade_id", "canonical_opportunity_id", "trade_horizon"],
        "source_exit_bar_path_provenance_digest": evidence.provenance["digest"],
        "source_exit_bar_path_analytical_digest": evidence.provenance["analytical_digest"],
        "source_exit_bar_path_exclusion_digest": evidence.provenance["exclusion_digest"],
        "source_baseline_reproduction_provenance_digest": reproduction.provenance["digest"],
        "successful_reproduction_digest": reproduction.provenance[
            "successful_reproduction_digest"
        ],
        "candidate_rows_digest": evidence_digest([
            item.analytical_record() for item in rows
        ]),
        "candidate_exclusion_digest": evidence_digest([
            item.record() for item in exclusions
        ]),
        "input_path_lifecycles": len(evidence.records),
        "input_reproduced_lifecycles": len(reproduction.records),
        "candidate_eligible_lifecycles": summary.candidate_eligible_lifecycles,
        "candidate_rows": len(rows),
        "candidate_exclusions": len(exclusions),
        "maximum_policy_count": CANDIDATE_POLICY_COUNT,
        "digest_algorithm": "sha256",
    }
    provenance["digest"] = evidence_digest((provenance,))
    return CandidateReplayPopulation(
        schema_version=CANDIDATE_REPLAY_SCHEMA_VERSION,
        records=tuple(rows),
        exclusions=tuple(exclusions),
        summary=summary,
        provenance=provenance,
    )
