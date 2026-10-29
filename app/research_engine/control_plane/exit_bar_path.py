"""Governed ``exit_bar_path_v1`` evidence for exit counterfactual research.

The authority is a deterministic research-side projection over completed
``shadow_runtime_v1`` OPEN+CLOSE lifecycles and authoritative ``events_v1`` M5
candles.  Historical timestamp interpretation is delegated exclusively to
``shadow_timestamp_normalization``.  This module does not simulate an exit
policy or implement any EX question.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
import math
from typing import Any, Iterable, Mapping

from core.shadow.models import (
    EXIT_STOP_LOSS,
    EXIT_TAKE_PROFIT,
    EXIT_TIMEOUT,
    SIMULATION_MODEL_VERSION,
    TIMEOUT_BARS,
)
from research_engine.control_plane.evidence_provenance import evidence_digest
from research_engine.control_plane.evidence_readiness import (
    EvidenceReadiness,
    EvidenceRequirement,
    evaluate_evidence_readiness,
)
from research_engine.control_plane.shadow_timestamp_normalization import (
    NORMALIZATION_CONTRACT_VERSION,
    TIMESTAMP_SEMANTICS,
    normalize_post_candle_utc_lifecycle,
)
from research_engine.registry.exit_policy_adjudication import (
    BASELINE_POLICY_V1,
    BASELINE_REPRODUCTION_CONTRACT,
)


SCHEMA_VERSION = "exit_bar_path_v1"
SOURCE_EVIDENCE_IDENTITY = "shadow_runtime_v1+events_v1:CANDLE:mt5_data:M5"
TIMESTAMP_VERSION = NORMALIZATION_CONTRACT_VERSION
_DIGEST_NOT_SUPPLIED = object()
BASELINE_AUTHORITY_FIELDS = tuple(
    BASELINE_REPRODUCTION_CONTRACT["exit_bar_path_v1_required_extension"]["fields"]
)
_EXPECTED_BASELINE_AUTHORITY_FIELDS = (
    "baseline_timeout_bars",
    "baseline_simulation_model_version",
    "observed_exit_reason",
    "observed_exit_price",
    "observed_bars_held",
    "observed_pnl_r_multiple",
    "observed_mfe_r",
)
if BASELINE_AUTHORITY_FIELDS != _EXPECTED_BASELINE_AUTHORITY_FIELDS:
    raise RuntimeError("HD09 exit_bar_path_v1 baseline extension contract changed")
if BASELINE_POLICY_V1["authoritative_timeout_bars"] != TIMEOUT_BARS:
    raise RuntimeError("HD09 timeout authority conflicts with the shadow runtime")


@dataclass(frozen=True)
class LifecyclePathSource:
    """One canonical completed lifecycle plus candidate authoritative bars."""

    open_event: Mapping[str, Any]
    close_event: Mapping[str, Any]
    candle_events: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class ExitBar:
    timestamp_utc_ms: int
    open: float
    high: float
    low: float
    close: float

    def analytical_record(self) -> dict[str, Any]:
        return {
            "timestamp_utc_ms": self.timestamp_utc_ms,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
        }


@dataclass(frozen=True)
class ExitBarPathRecord:
    schema_version: str
    lifecycle_identity: tuple[str, str, str]
    canonical_opportunity_id: str
    canonical_symbol: str
    trade_horizon: str
    direction: str
    entry_utc_epoch_s: int
    exit_utc_epoch_s: int
    entry_price: float
    baseline_stop_loss: float
    baseline_take_profit: float
    baseline_timeout_bars: int
    baseline_simulation_model_version: str
    observed_exit_reason: str
    observed_exit_price: float
    observed_bars_held: int
    observed_pnl_r_multiple: float
    observed_mfe_r: float
    timeframe: str
    ordered_m5_bars: tuple[ExitBar, ...]
    timestamp_semantics: str
    timestamp_version: str
    source_evidence_identity: str
    lifecycle_source_digest: str
    m5_source_digest: str
    normalization_provenance_digest: str
    analytical_digest: str
    eligibility_state: str = "ELIGIBLE"

    def analytical_record(self) -> dict[str, Any]:
        """Return the complete digest-bearing canonical analytical record."""
        return {
            "schema_version": self.schema_version,
            "lifecycle_identity": list(self.lifecycle_identity),
            "canonical_opportunity_id": self.canonical_opportunity_id,
            "canonical_symbol": self.canonical_symbol,
            "trade_horizon": self.trade_horizon,
            "direction": self.direction,
            "entry_utc_epoch_s": self.entry_utc_epoch_s,
            "exit_utc_epoch_s": self.exit_utc_epoch_s,
            "entry_price": self.entry_price,
            "baseline_stop_loss": self.baseline_stop_loss,
            "baseline_take_profit": self.baseline_take_profit,
            "baseline_timeout_bars": self.baseline_timeout_bars,
            "baseline_simulation_model_version": self.baseline_simulation_model_version,
            "observed_exit_reason": self.observed_exit_reason,
            "observed_exit_price": self.observed_exit_price,
            "observed_bars_held": self.observed_bars_held,
            "observed_pnl_r_multiple": self.observed_pnl_r_multiple,
            "observed_mfe_r": self.observed_mfe_r,
            "timeframe": self.timeframe,
            "ordered_m5_bars": [bar.analytical_record() for bar in self.ordered_m5_bars],
            "timestamp_semantics": self.timestamp_semantics,
            "timestamp_version": self.timestamp_version,
            "source_evidence_identity": self.source_evidence_identity,
            "lifecycle_source_digest": self.lifecycle_source_digest,
            "m5_source_digest": self.m5_source_digest,
            "normalization_provenance_digest": self.normalization_provenance_digest,
            "eligibility_state": self.eligibility_state,
            "analytical_digest": self.analytical_digest,
        }


@dataclass(frozen=True)
class ExitBarPathExclusion:
    lifecycle_identity: tuple[str, str, str] | None
    reason: str
    source_digest: str | None
    normalization_provenance_digest: str | None
    eligibility_state: str = "EXCLUDED"

    def record(self) -> dict[str, Any]:
        return {
            "lifecycle_identity": (
                list(self.lifecycle_identity) if self.lifecycle_identity else None
            ),
            "reason": self.reason,
            "source_digest": self.source_digest,
            "normalization_provenance_digest": self.normalization_provenance_digest,
            "eligibility_state": self.eligibility_state,
        }


@dataclass(frozen=True)
class ExitBarPathSummary:
    total_completed_lifecycles: int
    eligible_lifecycles: int
    eligible_distinct_canonical_opportunities: int
    coverage_percentage: float
    exclusions_by_reason: dict[str, int]
    counts_by_symbol: dict[str, int]
    counts_by_horizon: dict[str, int]
    earliest_eligible_entry_utc: int | None
    latest_eligible_exit_utc: int | None


@dataclass(frozen=True)
class GovernedExitBarPathEvidence:
    schema_version: str
    records: tuple[ExitBarPathRecord, ...]
    exclusions: tuple[ExitBarPathExclusion, ...]
    summary: ExitBarPathSummary
    provenance: dict[str, Any]

    def readiness(self, requirement: EvidenceRequirement) -> EvidenceReadiness:
        return evaluate_evidence_readiness(
            total_count=self.summary.total_completed_lifecycles,
            valid_count=self.summary.eligible_lifecycles,
            distinct_valid_count=(
                self.summary.eligible_distinct_canonical_opportunities
            ),
            requirement=requirement,
        )


def _identity(event: Mapping[str, Any]) -> tuple[str, str, str] | None:
    identity = (
        str(event.get("shadow_trade_id", "") or ""),
        str(event.get("canonical_opportunity_id", "") or ""),
        str(event.get("horizon", "") or ""),
    )
    return identity if all(identity) else None


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)) or int(value) != value:
        return None
    return int(value)


def _source_records(source: LifecyclePathSource) -> list[dict[str, Any]]:
    return [
        deepcopy(dict(source.open_event)),
        deepcopy(dict(source.close_event)),
        *(deepcopy(dict(item)) for item in source.candle_events),
    ]


def _safe_source_digest(source: LifecyclePathSource) -> str | None:
    try:
        return evidence_digest(_source_records(source))
    except (TypeError, ValueError, OverflowError):
        return None


def _exclusion(
    source: LifecyclePathSource,
    reason: str,
    *,
    normalization_digest: str | None = None,
    source_digest: str | None | object = _DIGEST_NOT_SUPPLIED,
) -> ExitBarPathExclusion:
    resolved_source_digest = (
        _safe_source_digest(source)
        if source_digest is _DIGEST_NOT_SUPPLIED
        else source_digest
    )
    return ExitBarPathExclusion(
        lifecycle_identity=_identity(source.open_event),
        reason=reason,
        source_digest=resolved_source_digest,
        normalization_provenance_digest=normalization_digest,
    )


def _canonical_record(
    source: LifecyclePathSource,
    *,
    source_digest: str | None,
) -> tuple[ExitBarPathRecord | None, ExitBarPathExclusion | None]:
    if source_digest is None:
        return None, _exclusion(
            source, "PROVENANCE_FAILURE", source_digest=None,
        )
    try:
        normalized = normalize_post_candle_utc_lifecycle(
            source.open_event, source.close_event, source.candle_events,
        )
    except (TypeError, ValueError, OverflowError):
        return None, _exclusion(
            source, "PROVENANCE_FAILURE", source_digest=source_digest,
        )

    normalization_digest = normalized.provenance.get("digest")
    if not normalized.eligible:
        return None, _exclusion(
            source, normalized.reason,
            normalization_digest=str(normalization_digest or "") or None,
            source_digest=source_digest,
        )
    if (
        not isinstance(normalization_digest, str)
        or len(normalization_digest) != 64
        or normalized.normalized_open is None
        or normalized.normalized_close is None
        or normalized.lifecycle_identity is None
    ):
        return None, _exclusion(
            source, "PROVENANCE_FAILURE", source_digest=source_digest,
        )

    opened = normalized.normalized_open
    closed = normalized.normalized_close
    identity = normalized.lifecycle_identity
    if _identity(opened) != identity or _identity(closed) != identity:
        return None, _exclusion(
            source, "LIFECYCLE_IDENTITY_MISMATCH",
            normalization_digest=normalization_digest,
            source_digest=source_digest,
        )

    symbol = str(opened.get("symbol", "") or "")
    horizon = str(opened.get("horizon", "") or "")
    if symbol != str(closed.get("symbol", "") or ""):
        return None, _exclusion(
            source, "CANONICAL_SYMBOL_MISMATCH",
            normalization_digest=normalization_digest,
            source_digest=source_digest,
        )
    if horizon != str(closed.get("horizon", "") or "") or horizon != identity[2]:
        return None, _exclusion(
            source, "HORIZON_MISMATCH",
            normalization_digest=normalization_digest,
            source_digest=source_digest,
        )

    construction = opened.get("construction")
    if not isinstance(construction, Mapping):
        return None, _exclusion(
            source, "MISSING_BASELINE_GEOMETRY",
            normalization_digest=normalization_digest,
            source_digest=source_digest,
        )
    direction = str(construction.get("direction", "") or "").upper()
    entry = _finite(construction.get("entry_price"))
    stop = _finite(construction.get("stop_loss"))
    target = _finite(construction.get("take_profit"))
    if direction not in {"BUY", "SELL"} or None in (entry, stop, target):
        return None, _exclusion(
            source, "MISSING_BASELINE_GEOMETRY",
            normalization_digest=normalization_digest,
            source_digest=source_digest,
        )
    assert entry is not None and stop is not None and target is not None
    valid_geometry = (
        stop < entry < target if direction == "BUY" else target < entry < stop
    )
    if not valid_geometry:
        return None, _exclusion(
            source, "INVALID_BASELINE_GEOMETRY",
            normalization_digest=normalization_digest,
            source_digest=source_digest,
        )

    assumptions = opened.get("simulation_assumptions")
    if not isinstance(assumptions, Mapping) or "timeout_bars" not in assumptions:
        return None, _exclusion(
            source, "MISSING_BASELINE_TIMEOUT_AUTHORITY",
            normalization_digest=normalization_digest,
            source_digest=source_digest,
        )
    baseline_timeout_bars = _integer(assumptions.get("timeout_bars"))
    if baseline_timeout_bars is None or baseline_timeout_bars <= 0:
        return None, _exclusion(
            source, "INVALID_BASELINE_TIMEOUT_AUTHORITY",
            normalization_digest=normalization_digest,
            source_digest=source_digest,
        )
    canonical_timeout = TIMEOUT_BARS.get(horizon)
    if canonical_timeout is None or baseline_timeout_bars != canonical_timeout:
        return None, _exclusion(
            source, "BASELINE_TIMEOUT_AUTHORITY_MISMATCH",
            normalization_digest=normalization_digest,
            source_digest=source_digest,
        )

    if "simulation_model_version" not in opened:
        return None, _exclusion(
            source, "MISSING_BASELINE_SIMULATION_MODEL_VERSION",
            normalization_digest=normalization_digest,
            source_digest=source_digest,
        )
    baseline_model_version = opened.get("simulation_model_version")
    if not isinstance(baseline_model_version, str) or not baseline_model_version:
        return None, _exclusion(
            source, "INVALID_BASELINE_SIMULATION_MODEL_VERSION",
            normalization_digest=normalization_digest,
            source_digest=source_digest,
        )
    if baseline_model_version != SIMULATION_MODEL_VERSION:
        return None, _exclusion(
            source, "INCOMPATIBLE_BASELINE_SIMULATION_MODEL_VERSION",
            normalization_digest=normalization_digest,
            source_digest=source_digest,
        )
    close_model_version = closed.get("simulation_model_version")
    if close_model_version != baseline_model_version:
        return None, _exclusion(
            source, "BASELINE_SIMULATION_MODEL_IDENTITY_CONFLICT",
            normalization_digest=normalization_digest,
            source_digest=source_digest,
        )

    if "exit_reason" not in closed:
        return None, _exclusion(
            source, "MISSING_OBSERVED_EXIT_REASON",
            normalization_digest=normalization_digest,
            source_digest=source_digest,
        )
    observed_exit_reason = closed.get("exit_reason")
    if observed_exit_reason not in {EXIT_STOP_LOSS, EXIT_TAKE_PROFIT, EXIT_TIMEOUT}:
        return None, _exclusion(
            source, "INVALID_OBSERVED_EXIT_REASON",
            normalization_digest=normalization_digest,
            source_digest=source_digest,
        )
    if "exit_price" not in closed:
        return None, _exclusion(
            source, "MISSING_OBSERVED_EXIT_PRICE",
            normalization_digest=normalization_digest,
            source_digest=source_digest,
        )
    observed_exit_price = _finite(closed.get("exit_price"))
    if observed_exit_price is None or observed_exit_price <= 0:
        return None, _exclusion(
            source, "INVALID_OBSERVED_EXIT_PRICE",
            normalization_digest=normalization_digest,
            source_digest=source_digest,
        )
    if "bars_held" not in closed:
        return None, _exclusion(
            source, "MISSING_OBSERVED_BARS_HELD",
            normalization_digest=normalization_digest,
            source_digest=source_digest,
        )
    observed_bars_held = _integer(closed.get("bars_held"))
    if observed_bars_held is None or not 1 <= observed_bars_held <= baseline_timeout_bars:
        return None, _exclusion(
            source, "INVALID_OBSERVED_BARS_HELD",
            normalization_digest=normalization_digest,
            source_digest=source_digest,
        )
    if observed_exit_reason == EXIT_TIMEOUT and observed_bars_held != baseline_timeout_bars:
        return None, _exclusion(
            source, "OBSERVED_BASELINE_AUTHORITY_INCONSISTENT",
            normalization_digest=normalization_digest,
            source_digest=source_digest,
        )

    outcome = closed.get("outcome")
    if not isinstance(outcome, Mapping):
        return None, _exclusion(
            source, "MISSING_OBSERVED_OUTCOME_AUTHORITY",
            normalization_digest=normalization_digest,
            source_digest=source_digest,
        )
    if "pnl_r_multiple" not in outcome:
        return None, _exclusion(
            source, "MISSING_OBSERVED_PNL_R_MULTIPLE",
            normalization_digest=normalization_digest,
            source_digest=source_digest,
        )
    observed_pnl_r = _finite(outcome.get("pnl_r_multiple"))
    if observed_pnl_r is None:
        return None, _exclusion(
            source, "INVALID_OBSERVED_PNL_R_MULTIPLE",
            normalization_digest=normalization_digest,
            source_digest=source_digest,
        )
    if "mfe_r" not in outcome:
        return None, _exclusion(
            source, "MISSING_OBSERVED_MFE_R",
            normalization_digest=normalization_digest,
            source_digest=source_digest,
        )
    observed_mfe_r = _finite(outcome.get("mfe_r"))
    if observed_mfe_r is None or observed_mfe_r < 0:
        return None, _exclusion(
            source, "INVALID_OBSERVED_MFE_R",
            normalization_digest=normalization_digest,
            source_digest=source_digest,
        )

    entry_time = opened.get("entry_market_time_utc_epoch_s")
    exit_time = closed.get("exit_market_time_utc_epoch_s")
    if (
        isinstance(entry_time, bool) or not isinstance(entry_time, int)
        or isinstance(exit_time, bool) or not isinstance(exit_time, int)
        or exit_time <= entry_time
    ):
        return None, _exclusion(
            source, "AMBIGUOUS_LIFECYCLE_BOUNDARY",
            normalization_digest=normalization_digest,
            source_digest=source_digest,
        )

    bars: list[ExitBar] = []
    selected_candle_records: list[dict[str, Any]] = []
    for candle in normalized.ordered_m5_path:
        payload = candle.get("payload")
        if not isinstance(payload, Mapping):
            return None, _exclusion(
                source, "MALFORMED_M5_BAR",
                normalization_digest=normalization_digest,
                source_digest=source_digest,
            )
        timestamp = payload.get("ts")
        values = tuple(_finite(payload.get(key)) for key in ("o", "h", "l", "c"))
        if (
            isinstance(timestamp, bool) or not isinstance(timestamp, int)
            or any(value is None for value in values)
        ):
            return None, _exclusion(
                source, "MALFORMED_M5_BAR",
                normalization_digest=normalization_digest,
                source_digest=source_digest,
            )
        o, high, low, close = values
        assert o is not None and high is not None and low is not None and close is not None
        if low > min(o, close) or high < max(o, close) or low > high:
            return None, _exclusion(
                source, "IMPOSSIBLE_M5_GEOMETRY",
                normalization_digest=normalization_digest,
                source_digest=source_digest,
            )
        bars.append(ExitBar(timestamp, o, high, low, close))
        selected_candle_records.append(deepcopy(dict(candle)))

    times = [bar.timestamp_utc_ms for bar in bars]
    if (
        not bars
        or times != sorted(times)
        or len(times) != len(set(times))
        or times[-1] != exit_time * 1000
    ):
        return None, _exclusion(
            source, "INVALID_ORDERED_M5_PATH",
            normalization_digest=normalization_digest,
            source_digest=source_digest,
        )

    lifecycle_digest = evidence_digest((dict(source.open_event), dict(source.close_event)))
    m5_digest = evidence_digest(selected_candle_records)
    material = {
        "schema_version": SCHEMA_VERSION,
        "lifecycle_identity": list(identity),
        "canonical_opportunity_id": identity[1],
        "canonical_symbol": symbol,
        "trade_horizon": horizon,
        "direction": direction,
        "entry_utc_epoch_s": entry_time,
        "exit_utc_epoch_s": exit_time,
        "entry_price": entry,
        "baseline_stop_loss": stop,
        "baseline_take_profit": target,
        "baseline_timeout_bars": baseline_timeout_bars,
        "baseline_simulation_model_version": baseline_model_version,
        "observed_exit_reason": observed_exit_reason,
        "observed_exit_price": observed_exit_price,
        "observed_bars_held": observed_bars_held,
        "observed_pnl_r_multiple": observed_pnl_r,
        "observed_mfe_r": observed_mfe_r,
        "timeframe": "M5",
        "ordered_m5_bars": [bar.analytical_record() for bar in bars],
        "timestamp_semantics": TIMESTAMP_SEMANTICS,
        "timestamp_version": TIMESTAMP_VERSION,
        "source_evidence_identity": SOURCE_EVIDENCE_IDENTITY,
        "lifecycle_source_digest": lifecycle_digest,
        "m5_source_digest": m5_digest,
        "normalization_provenance_digest": normalization_digest,
        "eligibility_state": "ELIGIBLE",
    }
    analytical_digest = evidence_digest((material,))
    return ExitBarPathRecord(
        schema_version=SCHEMA_VERSION,
        lifecycle_identity=identity,
        canonical_opportunity_id=identity[1],
        canonical_symbol=symbol,
        trade_horizon=horizon,
        direction=direction,
        entry_utc_epoch_s=entry_time,
        exit_utc_epoch_s=exit_time,
        entry_price=entry,
        baseline_stop_loss=stop,
        baseline_take_profit=target,
        baseline_timeout_bars=baseline_timeout_bars,
        baseline_simulation_model_version=baseline_model_version,
        observed_exit_reason=observed_exit_reason,
        observed_exit_price=observed_exit_price,
        observed_bars_held=observed_bars_held,
        observed_pnl_r_multiple=observed_pnl_r,
        observed_mfe_r=observed_mfe_r,
        timeframe="M5",
        ordered_m5_bars=tuple(bars),
        timestamp_semantics=TIMESTAMP_SEMANTICS,
        timestamp_version=TIMESTAMP_VERSION,
        source_evidence_identity=SOURCE_EVIDENCE_IDENTITY,
        lifecycle_source_digest=lifecycle_digest,
        m5_source_digest=m5_digest,
        normalization_provenance_digest=normalization_digest,
        analytical_digest=analytical_digest,
    ), None


def build_exit_bar_path_v1(
    lifecycle_sources: Iterable[LifecyclePathSource],
) -> GovernedExitBarPathEvidence:
    """Build canonical path evidence without mutating any source record."""
    sources = tuple(lifecycle_sources)
    records: list[ExitBarPathRecord] = []
    exclusions: list[ExitBarPathExclusion] = []
    identity_counts = Counter(
        identity for source in sources
        if (identity := _identity(source.open_event)) is not None
    )

    prepared = tuple(
        (source, _identity(source.open_event), _safe_source_digest(source))
        for source in sources
    )
    sortable = sorted(
        prepared,
        key=lambda item: (item[1] or ("", "", ""), item[2] or ""),
    )
    for source, identity, source_digest in sortable:
        if identity is not None and identity_counts[identity] > 1:
            exclusions.append(_exclusion(
                source, "DUPLICATE_LIFECYCLE_IDENTITY",
                source_digest=source_digest,
            ))
            continue
        record, exclusion = _canonical_record(
            source, source_digest=source_digest,
        )
        if record is not None:
            records.append(record)
        elif exclusion is not None:
            exclusions.append(exclusion)

    records.sort(key=lambda item: item.lifecycle_identity)
    exclusions.sort(
        key=lambda item: (
            item.lifecycle_identity or ("", "", ""), item.reason,
            item.source_digest or "",
        )
    )
    total = len(sources)
    eligible = len(records)
    reason_counts = Counter(item.reason for item in exclusions)
    symbol_counts = Counter(item.canonical_symbol for item in records)
    horizon_counts = Counter(item.trade_horizon for item in records)
    summary = ExitBarPathSummary(
        total_completed_lifecycles=total,
        eligible_lifecycles=eligible,
        eligible_distinct_canonical_opportunities=len({
            item.canonical_opportunity_id for item in records
        }),
        coverage_percentage=(100.0 * eligible / total) if total else 0.0,
        exclusions_by_reason=dict(sorted(reason_counts.items())),
        counts_by_symbol=dict(sorted(symbol_counts.items())),
        counts_by_horizon=dict(sorted(horizon_counts.items())),
        earliest_eligible_entry_utc=(
            min(item.entry_utc_epoch_s for item in records) if records else None
        ),
        latest_eligible_exit_utc=(
            max(item.exit_utc_epoch_s for item in records) if records else None
        ),
    )
    source_digests = [
        {"source_digest": source_digest or "PROVENANCE_FAILURE"}
        for _, _, source_digest in prepared
    ]
    provenance = {
        "schema_version": SCHEMA_VERSION,
        "source_evidence_identity": SOURCE_EVIDENCE_IDENTITY,
        "timestamp_semantics": TIMESTAMP_SEMANTICS,
        "timestamp_version": TIMESTAMP_VERSION,
        "normalization_contract_version": NORMALIZATION_CONTRACT_VERSION,
        "source_input_digest": evidence_digest(source_digests),
        "eligible_lifecycle_digest": evidence_digest([
            {"analytical_digest": item.analytical_digest} for item in records
        ]),
        "exclusion_digest": evidence_digest([item.record() for item in exclusions]),
        "analytical_digest": evidence_digest([
            item.analytical_record() for item in records
        ]),
        "input_lifecycles": total,
        "records_used": eligible,
        "records_excluded": len(exclusions),
        "exclusions_by_reason": summary.exclusions_by_reason,
        "digest_algorithm": "sha256",
    }
    provenance["digest"] = evidence_digest((provenance,))
    return GovernedExitBarPathEvidence(
        schema_version=SCHEMA_VERSION,
        records=tuple(records),
        exclusions=tuple(exclusions),
        summary=summary,
        provenance=provenance,
    )
