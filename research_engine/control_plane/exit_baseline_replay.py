"""Governed SHADOW_BASELINE_V1 replay and reproduction agreement.

This module implements only the HD09 baseline.  It does not define or replay
candidate exit policies and it does not implement any EX question.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
from typing import Any, Mapping

from research_engine.control_plane.evidence_provenance import evidence_digest
from research_engine.control_plane.evidence_readiness import (
    EvidenceReadiness,
    EvidenceRequirement,
    evaluate_evidence_readiness,
)
from research_engine.control_plane.exit_bar_path import (
    SCHEMA_VERSION as PATH_SCHEMA_VERSION,
    ExitBarPathRecord,
    GovernedExitBarPathEvidence,
)
from research_engine.registry.exit_policy_adjudication import HD09_ADJUDICATED_CONTRACT


REPLAY_SCHEMA_VERSION = "shadow_baseline_replay_v1"
REPRODUCTION_SCHEMA_VERSION = "shadow_baseline_reproduction_v1"
HD09_ADJUDICATION_VERSION = str(HD09_ADJUDICATED_CONTRACT["version"])
BASELINE_POLICY_V1: Mapping[str, Any] = HD09_ADJUDICATED_CONTRACT["baseline"]
BASELINE_REPRODUCTION_CONTRACT: Mapping[str, Any] = (
    HD09_ADJUDICATED_CONTRACT["baseline_reproduction"]
)
BASELINE_POLICY_ID = str(BASELINE_POLICY_V1["policy_id"])
BASELINE_POLICY_VERSION = int(BASELINE_POLICY_V1["policy_version"])
AUTHORITY_MISSING = "BASELINE_REPRODUCTION_AUTHORITY_MISSING"
REPRODUCTION_FAILED = "BASELINE_REPRODUCTION_FAILED"
ELIGIBLE = "ELIGIBLE"
EXCLUDED = "EXCLUDED"
_EXIT_REASONS = frozenset({"stop_loss", "take_profit", "timeout"})
_COMPARISON_ORDER = (
    "observed_exit_reason",
    "exit_utc_epoch_s",
    "observed_bars_held",
    "observed_exit_price",
    "observed_pnl_r_multiple",
    "observed_mfe_r",
)


if BASELINE_POLICY_V1["barrier_order"] != (
    "effective_protective_stop", "take_profit", "timeout",
):
    raise RuntimeError("HD09 SHADOW_BASELINE_V1 barrier order changed")
if BASELINE_POLICY_V1["same_bar_sl_tp_collision"] != "SL_FIRST":
    raise RuntimeError("HD09 SHADOW_BASELINE_V1 collision rule changed")
if BASELINE_POLICY_V1["fill_model"] != "EXACT_PRICE":
    raise RuntimeError("HD09 SHADOW_BASELINE_V1 fill model changed")
if BASELINE_REPRODUCTION_CONTRACT["bars_held_rule"] != "exact integer equality":
    raise RuntimeError("HD09 baseline reproduction bars-held rule changed")


@dataclass(frozen=True)
class ReproductionDiagnostic:
    field: str
    observed: Any
    reproduced: Any
    comparison_rule: str

    def record(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "observed": self.observed,
            "reproduced": self.reproduced,
            "comparison_rule": self.comparison_rule,
        }


@dataclass(frozen=True)
class BaselineReplayResult:
    schema_version: str
    lifecycle_identity: tuple[str, str, str]
    canonical_opportunity_id: str
    canonical_symbol: str
    trade_horizon: str
    exit_reason: str
    exit_utc_epoch_s: int
    exit_price: float
    bars_held: int
    pnl_r_multiple: float
    mfe_r: float
    baseline_policy_id: str
    baseline_policy_version: int
    source_path_analytical_digest: str
    replay_eligibility_state: str
    replay_digest: str

    def analytical_record(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "lifecycle_identity": list(self.lifecycle_identity),
            "canonical_opportunity_id": self.canonical_opportunity_id,
            "canonical_symbol": self.canonical_symbol,
            "trade_horizon": self.trade_horizon,
            "exit_reason": self.exit_reason,
            "exit_utc_epoch_s": self.exit_utc_epoch_s,
            "exit_price": self.exit_price,
            "bars_held": self.bars_held,
            "pnl_r_multiple": self.pnl_r_multiple,
            "mfe_r": self.mfe_r,
            "baseline_policy_id": self.baseline_policy_id,
            "baseline_policy_version": self.baseline_policy_version,
            "source_path_analytical_digest": self.source_path_analytical_digest,
            "replay_eligibility_state": self.replay_eligibility_state,
            "replay_digest": self.replay_digest,
        }


@dataclass(frozen=True)
class BaselineReplayAttempt:
    lifecycle_identity: tuple[str, str, str]
    replay: BaselineReplayResult | None
    eligibility_state: str
    reason: str | None
    diagnostics: tuple[ReproductionDiagnostic, ...]
    attempt_digest: str


@dataclass(frozen=True)
class BaselineReproductionRecord:
    lifecycle_identity: tuple[str, str, str]
    canonical_opportunity_id: str
    canonical_symbol: str
    trade_horizon: str
    replay: BaselineReplayResult
    path_analytical_digest: str
    observed_authority_digest: str
    reproduction_digest: str
    eligibility_state: str = ELIGIBLE

    def analytical_record(self) -> dict[str, Any]:
        return {
            "lifecycle_identity": list(self.lifecycle_identity),
            "canonical_opportunity_id": self.canonical_opportunity_id,
            "canonical_symbol": self.canonical_symbol,
            "trade_horizon": self.trade_horizon,
            "replay": self.replay.analytical_record(),
            "path_analytical_digest": self.path_analytical_digest,
            "observed_authority_digest": self.observed_authority_digest,
            "reproduction_digest": self.reproduction_digest,
            "eligibility_state": self.eligibility_state,
        }


@dataclass(frozen=True)
class BaselineReproductionExclusion:
    lifecycle_identity: tuple[str, str, str]
    canonical_symbol: str
    trade_horizon: str
    reason: str
    diagnostics: tuple[ReproductionDiagnostic, ...]
    path_analytical_digest: str
    replay_digest: str | None
    exclusion_digest: str
    eligibility_state: str = EXCLUDED

    def record(self) -> dict[str, Any]:
        return {
            "lifecycle_identity": list(self.lifecycle_identity),
            "canonical_symbol": self.canonical_symbol,
            "trade_horizon": self.trade_horizon,
            "reason": self.reason,
            "diagnostics": [item.record() for item in self.diagnostics],
            "path_analytical_digest": self.path_analytical_digest,
            "replay_digest": self.replay_digest,
            "exclusion_digest": self.exclusion_digest,
            "eligibility_state": self.eligibility_state,
        }


@dataclass(frozen=True)
class BaselineReproductionSummary:
    total_completed_lifecycles: int
    input_eligible_paths: int
    successful_reproductions: int
    reproduction_failures: int
    authority_missing_failures: int
    upstream_path_exclusions: int
    distinct_successful_opportunities: int
    successful_reproduction_coverage: float
    mismatch_counts_by_field: dict[str, int]
    authority_missing_counts_by_field: dict[str, int]
    failure_counts_by_symbol: dict[str, int]
    failure_counts_by_horizon: dict[str, int]


@dataclass(frozen=True)
class GovernedBaselineReproductionPopulation:
    schema_version: str
    records: tuple[BaselineReproductionRecord, ...]
    exclusions: tuple[BaselineReproductionExclusion, ...]
    summary: BaselineReproductionSummary
    provenance: dict[str, Any]

    def readiness(self, requirement: EvidenceRequirement) -> EvidenceReadiness:
        return evaluate_evidence_readiness(
            total_count=self.summary.total_completed_lifecycles,
            valid_count=self.summary.successful_reproductions,
            distinct_valid_count=self.summary.distinct_successful_opportunities,
            requirement=requirement,
        )


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
    field: str, observed: Any, reproduced: Any, rule: str,
) -> ReproductionDiagnostic:
    return ReproductionDiagnostic(field, observed, reproduced, rule)


def _authority_diagnostics(record: ExitBarPathRecord) -> tuple[ReproductionDiagnostic, ...]:
    issues: list[ReproductionDiagnostic] = []
    identity = record.lifecycle_identity
    if (
        not isinstance(identity, tuple) or len(identity) != 3
        or not all(isinstance(item, str) and item for item in identity)
        or identity[1] != record.canonical_opportunity_id
        or identity[2] != record.trade_horizon
    ):
        issues.append(_diagnostic(
            "lifecycle_identity", identity, None, "valid canonical lifecycle identity",
        ))
    if record.schema_version != PATH_SCHEMA_VERSION or record.eligibility_state != ELIGIBLE:
        issues.append(_diagnostic(
            "exit_bar_path_v1", record.schema_version, PATH_SCHEMA_VERSION,
            "governed eligible exit_bar_path_v1 record",
        ))
    path_material = record.analytical_record()
    path_material.pop("analytical_digest", None)
    if (
        not isinstance(record.analytical_digest, str)
        or not record.analytical_digest
        or evidence_digest((path_material,)) != record.analytical_digest
    ):
        issues.append(_diagnostic(
            "analytical_digest", record.analytical_digest, None,
            "valid governed path analytical digest",
        ))
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
    timeout = _integer(record.baseline_timeout_bars)
    expected_timeout = BASELINE_POLICY_V1["authoritative_timeout_bars"].get(
        record.trade_horizon
    )
    if timeout is None or timeout <= 0 or timeout != expected_timeout:
        issues.append(_diagnostic(
            "baseline_timeout_bars", record.baseline_timeout_bars, expected_timeout,
            "exact HD09 horizon timeout authority",
        ))
    if (
        not isinstance(record.baseline_simulation_model_version, str)
        or not record.baseline_simulation_model_version
    ):
        issues.append(_diagnostic(
            "baseline_simulation_model_version",
            record.baseline_simulation_model_version,
            None,
            "non-empty governed OPEN simulation model identity",
        ))
    if record.observed_exit_reason not in _EXIT_REASONS:
        issues.append(_diagnostic(
            "observed_exit_reason", record.observed_exit_reason, None,
            "canonical observed exit-reason token",
        ))
    if (value := _finite(record.observed_exit_price)) is None or value <= 0:
        issues.append(_diagnostic(
            "observed_exit_price", record.observed_exit_price, None,
            "finite positive observed exit price",
        ))
    if (value := _integer(record.observed_bars_held)) is None or value <= 0:
        issues.append(_diagnostic(
            "observed_bars_held", record.observed_bars_held, None,
            "positive observed integer bars held",
        ))
    if _finite(record.observed_pnl_r_multiple) is None:
        issues.append(_diagnostic(
            "observed_pnl_r_multiple", record.observed_pnl_r_multiple, None,
            "finite observed PnL R",
        ))
    if (value := _finite(record.observed_mfe_r)) is None or value < 0:
        issues.append(_diagnostic(
            "observed_mfe_r", record.observed_mfe_r, None,
            "finite non-negative observed MFE R",
        ))
    entry_utc = _integer(record.entry_utc_epoch_s)
    exit_utc = _integer(record.exit_utc_epoch_s)
    if entry_utc is None or exit_utc is None or entry_utc >= exit_utc:
        issues.append(_diagnostic(
            "lifecycle_utc_boundaries",
            (record.entry_utc_epoch_s, record.exit_utc_epoch_s),
            None,
            "unambiguous increasing UTC epoch-second boundaries",
        ))
    bars = record.ordered_m5_bars
    previous = (entry_utc or 0) * 1000
    if not bars:
        issues.append(_diagnostic("ordered_m5_bars", 0, None, "non-empty M5 path"))
    for index, bar in enumerate(bars, 1):
        timestamp = _integer(bar.timestamp_utc_ms)
        ohlc = tuple(_finite(value) for value in (bar.open, bar.high, bar.low, bar.close))
        valid_geometry = (
            all(value is not None and value > 0 for value in ohlc)
            and ohlc[1] >= max(ohlc[0], ohlc[3])
            and ohlc[2] <= min(ohlc[0], ohlc[3])
            and ohlc[1] >= ohlc[2]
        )
        if timestamp is None or timestamp % 1000 or timestamp <= previous:
            issues.append(_diagnostic(
                f"ordered_m5_bars[{index}].timestamp_utc_ms",
                bar.timestamp_utc_ms,
                None,
                "strictly increasing whole-second post-entry UTC timestamp",
            ))
        if not valid_geometry:
            issues.append(_diagnostic(
                f"ordered_m5_bars[{index}].ohlc", ohlc, None,
                "finite positive possible OHLC geometry",
            ))
        if timestamp is not None:
            previous = timestamp
    return tuple(issues)


def _attempt_digest(
    record: ExitBarPathRecord,
    *,
    replay: BaselineReplayResult | None,
    reason: str | None,
    diagnostics: tuple[ReproductionDiagnostic, ...],
) -> str:
    return evidence_digest(({
        "schema_version": REPLAY_SCHEMA_VERSION,
        "hd09_adjudication_version": HD09_ADJUDICATION_VERSION,
        "baseline_policy": BASELINE_POLICY_V1,
        "source_path_analytical_digest": record.analytical_digest,
        "replay_digest": replay.replay_digest if replay else None,
        "reason": reason,
        "diagnostics": [item.record() for item in diagnostics],
    },))


def replay_shadow_baseline_v1(record: ExitBarPathRecord) -> BaselineReplayAttempt:
    """Replay one governed path under the frozen SHADOW_BASELINE_V1 policy."""
    authority_issues = _authority_diagnostics(record)
    if authority_issues:
        return BaselineReplayAttempt(
            lifecycle_identity=record.lifecycle_identity,
            replay=None,
            eligibility_state=EXCLUDED,
            reason=AUTHORITY_MISSING,
            diagnostics=authority_issues,
            attempt_digest=_attempt_digest(
                record, replay=None, reason=AUTHORITY_MISSING,
                diagnostics=authority_issues,
            ),
        )

    risk = abs(record.entry_price - record.baseline_stop_loss)
    favourable_extreme = record.entry_price
    exit_reason: str | None = None
    exit_price: float | None = None
    exit_utc: int | None = None
    bars_held = 0

    for bars_held, bar in enumerate(record.ordered_m5_bars, 1):
        favourable_extreme = (
            max(favourable_extreme, bar.high)
            if record.direction == "BUY"
            else min(favourable_extreme, bar.low)
        )
        if record.direction == "BUY":
            if bar.low <= record.baseline_stop_loss:
                exit_price, exit_reason = record.baseline_stop_loss, "stop_loss"
            elif bar.high >= record.baseline_take_profit:
                exit_price, exit_reason = record.baseline_take_profit, "take_profit"
        else:
            if bar.high >= record.baseline_stop_loss:
                exit_price, exit_reason = record.baseline_stop_loss, "stop_loss"
            elif bar.low <= record.baseline_take_profit:
                exit_price, exit_reason = record.baseline_take_profit, "take_profit"
        if exit_price is None and bars_held >= record.baseline_timeout_bars:
            exit_price, exit_reason = bar.close, "timeout"
        if exit_price is not None:
            exit_utc = bar.timestamp_utc_ms // 1000
            break

    if exit_price is None or exit_reason is None or exit_utc is None:
        diagnostics = (_diagnostic(
            "replay_exit", None, None,
            "SL, TP, or timeout must occur within the governed baseline path",
        ),)
        return BaselineReplayAttempt(
            lifecycle_identity=record.lifecycle_identity,
            replay=None,
            eligibility_state=EXCLUDED,
            reason=REPRODUCTION_FAILED,
            diagnostics=diagnostics,
            attempt_digest=_attempt_digest(
                record, replay=None, reason=REPRODUCTION_FAILED,
                diagnostics=diagnostics,
            ),
        )

    pnl = (
        (exit_price - record.entry_price) / risk
        if record.direction == "BUY"
        else (record.entry_price - exit_price) / risk
    )
    mfe = max(
        0.0,
        (
            (favourable_extreme - record.entry_price) / risk
            if record.direction == "BUY"
            else (record.entry_price - favourable_extreme) / risk
        ),
    )
    replay_material = {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "hd09_adjudication_version": HD09_ADJUDICATION_VERSION,
        "baseline_policy": BASELINE_POLICY_V1,
        "lifecycle_identity": list(record.lifecycle_identity),
        "exit_reason": exit_reason,
        "exit_utc_epoch_s": exit_utc,
        "exit_price": exit_price,
        "bars_held": bars_held,
        "pnl_r_multiple": pnl,
        "mfe_r": mfe,
        "source_path_analytical_digest": record.analytical_digest,
    }
    replay_digest = evidence_digest((replay_material,))
    replay = BaselineReplayResult(
        schema_version=REPLAY_SCHEMA_VERSION,
        lifecycle_identity=record.lifecycle_identity,
        canonical_opportunity_id=record.canonical_opportunity_id,
        canonical_symbol=record.canonical_symbol,
        trade_horizon=record.trade_horizon,
        exit_reason=exit_reason,
        exit_utc_epoch_s=exit_utc,
        exit_price=exit_price,
        bars_held=bars_held,
        pnl_r_multiple=pnl,
        mfe_r=mfe,
        baseline_policy_id=BASELINE_POLICY_ID,
        baseline_policy_version=BASELINE_POLICY_VERSION,
        source_path_analytical_digest=record.analytical_digest,
        replay_eligibility_state=ELIGIBLE,
        replay_digest=replay_digest,
    )
    return BaselineReplayAttempt(
        lifecycle_identity=record.lifecycle_identity,
        replay=replay,
        eligibility_state=ELIGIBLE,
        reason=None,
        diagnostics=(),
        attempt_digest=_attempt_digest(
            record, replay=replay, reason=None, diagnostics=(),
        ),
    )


def _comparison_diagnostics(
    record: ExitBarPathRecord, replay: BaselineReplayResult,
) -> tuple[ReproductionDiagnostic, ...]:
    checks = {
        "observed_exit_reason": (
            record.observed_exit_reason,
            replay.exit_reason,
            record.observed_exit_reason == replay.exit_reason,
            "exact canonical token equality",
        ),
        "exit_utc_epoch_s": (
            record.exit_utc_epoch_s,
            replay.exit_utc_epoch_s,
            record.exit_utc_epoch_s == replay.exit_utc_epoch_s,
            "exact UTC epoch-second equality",
        ),
        "observed_bars_held": (
            record.observed_bars_held,
            replay.bars_held,
            record.observed_bars_held == replay.bars_held,
            "exact integer equality",
        ),
        "observed_exit_price": (
            record.observed_exit_price,
            replay.exit_price,
            math.isclose(
                record.observed_exit_price, replay.exit_price,
                rel_tol=0.0, abs_tol=1e-12,
            ),
            "math.isclose(rel_tol=0, abs_tol=1e-12)",
        ),
        "observed_pnl_r_multiple": (
            record.observed_pnl_r_multiple,
            round(replay.pnl_r_multiple, 4),
            math.isclose(
                record.observed_pnl_r_multiple,
                round(replay.pnl_r_multiple, 4),
                rel_tol=0.0, abs_tol=1e-12,
            ),
            "round(replay_R, 4), then math.isclose(rel_tol=0, abs_tol=1e-12)",
        ),
        "observed_mfe_r": (
            record.observed_mfe_r,
            round(replay.mfe_r, 4),
            math.isclose(
                record.observed_mfe_r,
                round(replay.mfe_r, 4),
                rel_tol=0.0, abs_tol=1e-12,
            ),
            "round(replay_MFE_R, 4), then math.isclose(rel_tol=0, abs_tol=1e-12)",
        ),
    }
    return tuple(
        _diagnostic(field, observed, reproduced, rule)
        for field in _COMPARISON_ORDER
        for observed, reproduced, matches, rule in (checks[field],)
        if not matches
    )


def reproduce_shadow_baseline_v1(
    record: ExitBarPathRecord,
) -> BaselineReproductionRecord | BaselineReproductionExclusion:
    """Replay and compare one path with its governed observed baseline."""
    attempt = replay_shadow_baseline_v1(record)
    if attempt.replay is None:
        material = {
            "reason": attempt.reason,
            "attempt_digest": attempt.attempt_digest,
            "diagnostics": [item.record() for item in attempt.diagnostics],
        }
        return BaselineReproductionExclusion(
            lifecycle_identity=record.lifecycle_identity,
            canonical_symbol=record.canonical_symbol,
            trade_horizon=record.trade_horizon,
            reason=attempt.reason or REPRODUCTION_FAILED,
            diagnostics=attempt.diagnostics,
            path_analytical_digest=record.analytical_digest,
            replay_digest=None,
            exclusion_digest=evidence_digest((material,)),
        )

    replay = attempt.replay
    diagnostics = _comparison_diagnostics(record, replay)
    observed_material = {
        field: getattr(record, field)
        for field in BASELINE_REPRODUCTION_CONTRACT["required_observed_fields"]
    }
    observed_digest = evidence_digest((observed_material,))
    material = {
        "schema_version": REPRODUCTION_SCHEMA_VERSION,
        "hd09_adjudication_version": HD09_ADJUDICATION_VERSION,
        "baseline_policy": BASELINE_POLICY_V1,
        "path_analytical_digest": record.analytical_digest,
        "replay_digest": replay.replay_digest,
        "observed_authority_digest": observed_digest,
        "diagnostics": [item.record() for item in diagnostics],
        "eligibility_state": ELIGIBLE if not diagnostics else EXCLUDED,
    }
    reproduction_digest = evidence_digest((material,))
    if diagnostics:
        return BaselineReproductionExclusion(
            lifecycle_identity=record.lifecycle_identity,
            canonical_symbol=record.canonical_symbol,
            trade_horizon=record.trade_horizon,
            reason=REPRODUCTION_FAILED,
            diagnostics=diagnostics,
            path_analytical_digest=record.analytical_digest,
            replay_digest=replay.replay_digest,
            exclusion_digest=reproduction_digest,
        )
    return BaselineReproductionRecord(
        lifecycle_identity=record.lifecycle_identity,
        canonical_opportunity_id=record.canonical_opportunity_id,
        canonical_symbol=record.canonical_symbol,
        trade_horizon=record.trade_horizon,
        replay=replay,
        path_analytical_digest=record.analytical_digest,
        observed_authority_digest=observed_digest,
        reproduction_digest=reproduction_digest,
    )


def _validate_path_population(evidence: GovernedExitBarPathEvidence) -> None:
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


def build_baseline_reproduction_population(
    evidence: GovernedExitBarPathEvidence,
) -> GovernedBaselineReproductionPopulation:
    """Build the deterministic reproduced-baseline population."""
    _validate_path_population(evidence)
    outcomes = [
        reproduce_shadow_baseline_v1(record)
        for record in sorted(evidence.records, key=lambda item: item.lifecycle_identity)
    ]
    records = sorted(
        (item for item in outcomes if isinstance(item, BaselineReproductionRecord)),
        key=lambda item: item.lifecycle_identity,
    )
    exclusions = sorted(
        (item for item in outcomes if isinstance(item, BaselineReproductionExclusion)),
        key=lambda item: (item.lifecycle_identity, item.reason, item.exclusion_digest),
    )
    mismatch_counts = Counter(
        diagnostic.field
        for exclusion in exclusions
        if exclusion.reason == REPRODUCTION_FAILED
        for diagnostic in exclusion.diagnostics
    )
    authority_missing_counts = Counter(
        diagnostic.field
        for exclusion in exclusions
        if exclusion.reason == AUTHORITY_MISSING
        for diagnostic in exclusion.diagnostics
    )
    failure_symbol_counts = Counter(item.canonical_symbol for item in exclusions)
    failure_horizon_counts = Counter(item.trade_horizon for item in exclusions)
    successful = len(records)
    total = evidence.summary.total_completed_lifecycles
    summary = BaselineReproductionSummary(
        total_completed_lifecycles=total,
        input_eligible_paths=len(evidence.records),
        successful_reproductions=successful,
        reproduction_failures=sum(
            item.reason == REPRODUCTION_FAILED for item in exclusions
        ),
        authority_missing_failures=sum(
            item.reason == AUTHORITY_MISSING for item in exclusions
        ),
        upstream_path_exclusions=len(evidence.exclusions),
        distinct_successful_opportunities=len({
            item.canonical_opportunity_id for item in records
        }),
        successful_reproduction_coverage=(100.0 * successful / total) if total else 0.0,
        mismatch_counts_by_field=dict(sorted(mismatch_counts.items())),
        authority_missing_counts_by_field=dict(sorted(authority_missing_counts.items())),
        failure_counts_by_symbol=dict(sorted(failure_symbol_counts.items())),
        failure_counts_by_horizon=dict(sorted(failure_horizon_counts.items())),
    )
    provenance = {
        "schema_version": REPRODUCTION_SCHEMA_VERSION,
        "hd09_adjudication_version": HD09_ADJUDICATION_VERSION,
        "baseline_policy": BASELINE_POLICY_V1,
        "source_exit_bar_path_provenance_digest": evidence.provenance["digest"],
        "source_exit_bar_path_analytical_digest": evidence.provenance["analytical_digest"],
        "source_exit_bar_path_exclusion_digest": evidence.provenance["exclusion_digest"],
        "successful_reproduction_digest": evidence_digest([
            item.analytical_record() for item in records
        ]),
        "reproduction_exclusion_digest": evidence_digest([
            item.record() for item in exclusions
        ]),
        "input_completed_lifecycles": total,
        "input_eligible_paths": len(evidence.records),
        "records_used": successful,
        "records_excluded_by_reproduction": len(exclusions),
        "upstream_path_exclusions": len(evidence.exclusions),
        "digest_algorithm": "sha256",
    }
    provenance["digest"] = evidence_digest((provenance,))
    return GovernedBaselineReproductionPopulation(
        schema_version=REPRODUCTION_SCHEMA_VERSION,
        records=tuple(records),
        exclusions=tuple(exclusions),
        summary=summary,
        provenance=provenance,
    )
