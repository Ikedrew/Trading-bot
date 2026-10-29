"""HD11 L1 chronological pattern-degradation research.

The independent unit is one canonical opportunity with exactly one completed
CURRENT ``PRIMARY_HORIZON_SIMULATION`` outcome. L1 is deliberately separate
from E2's pooled pattern-expectancy calculation and artifact.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import math
import statistics
from typing import Any, Mapping

from research_engine.control_plane.evidence_provenance import (
    attest_current_subset, build_evidence_provenance, evidence_digest,
    select_current_evidence,
)
from research_engine.control_plane.shadow_timestamp_normalization import (
    CANDLE_NORMALIZATION_VERSION,
    NORMALIZATION_CONTRACT_VERSION,
    TIMESTAMP_SEMANTICS,
    normalize_post_candle_utc_lifecycle,
)
from research_engine.experiments.experiment_base import (
    build_fingerprint_from_provenance, build_report,
)
from research_engine.registry.learning_adaptation_adjudication import (
    ALPHA, HD11_VERSION, L1_ESTIMAND, L1_MIN,
)

REPORT_FILENAME = "l1_pattern_degradation.json"
PRIMARY_OUTCOME = "PRIMARY_HORIZON_SIMULATION"
Z_95 = 1.96
INFERENCE_METHOD = "independent-window normal approximation with sample-standard-error"
MULTIPLICITY_METHOD = "Holm step-down family-wise error-rate control"
CURRENT_TIMESTAMP_MODE = "CURRENT_PRODUCER_CANONICAL_UTC"
HISTORICAL_TIMESTAMP_MODE = "GOVERNED_HISTORICAL_NORMALIZATION"


def _identity(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("identity")
    return value if isinstance(value, dict) else {}


def _snapshot(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("decision_snapshot")
    return value if isinstance(value, dict) else {}


def _outcome(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("simulated_outcome")
    return value if isinstance(value, dict) else {}


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _base_core(record: dict[str, Any]) -> tuple[str, str, float] | None:
    identity = _identity(record)
    opportunity_id = str(identity.get("canonical_opportunity_id", "") or "").strip()
    pattern = str(_snapshot(record).get("pattern", "") or "").strip()
    r_value = _finite_number(_outcome(record).get("pnl_r_multiple"))
    if not opportunity_id or not pattern or r_value is None:
        return None
    return opportunity_id, pattern, r_value


def _core(
    record: dict[str, Any], entry_times: Mapping[str, float],
) -> tuple[str, str, float, float] | None:
    base = _base_core(record)
    if base is None:
        return None
    opportunity_id, pattern, r_value = base
    entry_time = entry_times.get(opportunity_id)
    if entry_time is None:
        return None
    return opportunity_id, pattern, entry_time, r_value


def _canonical_json(record: dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)


def _lifecycle_key(record: Mapping[str, Any]) -> tuple[str, str, str]:
    identity = record.get("identity") if isinstance(record.get("identity"), Mapping) else {}
    return (
        str(record.get("shadow_trade_id", "") or identity.get("shadow_trade_id", "") or ""),
        str(record.get("canonical_opportunity_id", "") or identity.get("canonical_opportunity_id", "") or ""),
        str(record.get("horizon", "") or identity.get("trade_horizon", "") or identity.get("evaluated_horizon", "") or ""),
    )


def _integer(value: Any) -> int | None:
    number = _finite_number(value)
    if number is None or int(number) != number:
        return None
    return int(number)


def _current_open_entry_time(opened: Mapping[str, Any]) -> int | None:
    """Accept only the existing authority's explicitly current producer semantics."""
    if (
        opened.get("schema_version") != "shadow_runtime_v1"
        or opened.get("event_type") != "OPEN"
        or opened.get("market_timestamp_semantics") != TIMESTAMP_SEMANTICS
        or opened.get("market_timestamp_normalization_version") != CANDLE_NORMALIZATION_VERSION
    ):
        return None
    entry = _integer(opened.get("entry_market_time"))
    claimed = _integer(opened.get("entry_market_time_utc_epoch_s"))
    event = _integer(opened.get("event_market_time"))
    event_claimed = _integer(opened.get("event_market_time_utc_epoch_s"))
    opportunity = _integer(opened.get("opportunity_market_time"))
    opportunity_claimed = _integer(opened.get("opportunity_market_time_utc_epoch_s"))
    if (
        entry is None or entry <= 0
        or {entry, claimed, event, event_claimed, opportunity, opportunity_claimed} != {entry}
    ):
        return None
    parts = str(opened.get("canonical_opportunity_id", "") or "").split("*", 2)
    try:
        opportunity_epoch = int(parts[1]) if len(parts) == 3 else None
    except ValueError:
        return None
    return entry if opportunity_epoch == entry else None


def _candle_subset(
    opened: Mapping[str, Any], closed: Mapping[str, Any], candle_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    symbol = str(opened.get("symbol", "") or "")
    entry = _integer(opened.get("entry_market_time"))
    exit_time = _integer(closed.get("exit_market_time"))
    if not symbol or entry is None or exit_time is None:
        return []
    result = []
    for event in candle_events:
        if str(event.get("symbol", "") or "") != symbol:
            continue
        payload = event.get("payload")
        stamp = _integer(payload.get("ts")) if isinstance(payload, Mapping) else None
        if stamp is not None and entry * 1000 < stamp <= exit_time * 1000:
            result.append(event)
    return result


def _resolve_entry_time(
    record: dict[str, Any],
    opens: Mapping[tuple[str, str, str], list[dict[str, Any]]],
    closes: Mapping[tuple[str, str, str], list[dict[str, Any]]],
    candle_events: list[dict[str, Any]],
) -> tuple[int | None, dict[str, Any]]:
    key = _lifecycle_key(record)
    source_opens, source_closes = opens.get(key, []), closes.get(key, [])
    if len(source_opens) != 1 or len(source_closes) != 1:
        return None, {"identity": list(key), "reason": "AMBIGUOUS_LIFECYCLE_AUTHORITY"}
    opened, closed = source_opens[0], source_closes[0]
    current = _current_open_entry_time(opened)
    if current is not None:
        material = {
            "mode": CURRENT_TIMESTAMP_MODE,
            "identity": list(key),
            "entry_utc_epoch_s": current,
            "timestamp_semantics": TIMESTAMP_SEMANTICS,
            "timestamp_normalization_version": CANDLE_NORMALIZATION_VERSION,
            "source_digest": evidence_digest([opened, closed]),
        }
        material["digest"] = evidence_digest([material])
        return current, material

    normalized = normalize_post_candle_utc_lifecycle(
        opened, closed, _candle_subset(opened, closed, candle_events),
    )
    if not normalized.eligible or normalized.normalized_open is None:
        return None, {
            "identity": list(key),
            "reason": normalized.reason,
            "normalization_provenance_digest": normalized.provenance.get("digest"),
        }
    entry = _integer(normalized.normalized_open.get("entry_market_time_utc_epoch_s"))
    if entry is None:
        return None, {
            "identity": list(key), "reason": "MISSING_GOVERNED_ENTRY_TIME",
            "normalization_provenance_digest": normalized.provenance.get("digest"),
        }
    return entry, {
        "mode": HISTORICAL_TIMESTAMP_MODE,
        "identity": list(key),
        "entry_utc_epoch_s": entry,
        "normalization_contract_version": NORMALIZATION_CONTRACT_VERSION,
        "normalization_reason": normalized.reason,
        "normalization_provenance_digest": normalized.provenance.get("digest"),
    }


def _timestamp_sources(
    events: list[dict[str, Any]],
) -> tuple[dict[tuple[str, str, str], list[dict[str, Any]]], dict[tuple[str, str, str], list[dict[str, Any]]]]:
    opens: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    closes: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if not isinstance(event, dict) or event.get("schema_version") != "shadow_runtime_v1":
            continue
        if event.get("event_type") == "OPEN":
            opens[_lifecycle_key(event)].append(event)
        elif event.get("event_type") == "CLOSE":
            closes[_lifecycle_key(event)].append(event)
    return opens, closes


def _select_observations(
    current: list[dict[str, Any]],
    opens: Mapping[tuple[str, str, str], list[dict[str, Any]]],
    closes: Mapping[tuple[str, str, str], list[dict[str, Any]]],
    candle_events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, float], dict[str, int], list[str], list[dict[str, Any]]]:
    exclusions: Counter[str] = Counter()
    primary: list[dict[str, Any]] = []
    for record in current:
        if str(_identity(record).get("shadow_type", "") or "") != PRIMARY_OUTCOME:
            exclusions["non_primary_horizon_simulation"] += 1
            continue
        if _base_core(record) is None:
            exclusions["missing_or_invalid_required_field"] += 1
            continue
        primary.append(record)

    by_opportunity: dict[str, list[tuple[dict[str, Any], int, dict[str, Any]]]] = defaultdict(list)
    unresolved: list[str] = []
    timestamp_provenance: list[dict[str, Any]] = []
    for record in primary:
        base = _base_core(record)
        assert base is not None
        entry_time, provenance = _resolve_entry_time(record, opens, closes, candle_events)
        timestamp_provenance.append(provenance)
        if entry_time is None:
            exclusions["unresolved_governed_entry_time"] += 1
            unresolved.append(base[0])
            continue
        by_opportunity[base[0]].append((record, entry_time, provenance))

    observations: list[dict[str, Any]] = []
    entry_times: dict[str, float] = {}
    conflicts: list[str] = []
    for opportunity_id in sorted(by_opportunity):
        resolved = by_opportunity[opportunity_id]
        cores = {
            (base[0], base[1], entry_time, base[2])
            for record, entry_time, _ in resolved
            if (base := _base_core(record)) is not None
        }
        if len(cores) != 1:
            exclusions["conflicting_primary_outcome"] += len(resolved)
            conflicts.append(opportunity_id)
            continue
        # Identical account fanout remains one scientific observation.
        representative = min((item[0] for item in resolved), key=_canonical_json)
        observations.append(representative)
        entry_times[opportunity_id] = resolved[0][1]
        if len(resolved) > 1:
            exclusions["collapsed_duplicate_or_account_fanout"] += len(resolved) - 1
    return (
        observations, entry_times, dict(sorted(exclusions.items())), conflicts,
        sorted(timestamp_provenance, key=lambda item: (item.get("identity", []), item.get("reason", ""))),
    )


def _holm_adjust(tests: list[tuple[str, float]]) -> dict[str, float]:
    ordered = sorted(tests, key=lambda item: (item[1], item[0]))
    adjusted: dict[str, float] = {}
    running = 0.0
    for index, (identity, p_value) in enumerate(ordered):
        running = max(running, min(1.0, (len(ordered) - index) * p_value))
        adjusted[identity] = running
    return adjusted


def _pattern_test(early: list[float], late: list[float]) -> dict[str, Any]:
    early_mean = statistics.fmean(early)
    late_mean = statistics.fmean(late)
    estimate = late_mean - early_mean
    variance_early = statistics.variance(early) if len(early) > 1 else 0.0
    variance_late = statistics.variance(late) if len(late) > 1 else 0.0
    standard_error = math.sqrt(variance_early / len(early) + variance_late / len(late))
    p_value = (
        1.0 if standard_error == 0.0 and estimate == 0.0
        else 0.0 if standard_error == 0.0
        else math.erfc(abs(estimate / standard_error) / math.sqrt(2.0))
    )
    return {
        "early_n": len(early), "late_n": len(late),
        "early_mean_r": round(early_mean, 6),
        "late_mean_r": round(late_mean, 6),
        "late_minus_early_mean_r": round(estimate, 6),
        "standard_error": round(standard_error, 12),
        "interval_95": {
            "lower_r": round(estimate - Z_95 * standard_error, 6),
            "upper_r": round(estimate + Z_95 * standard_error, 6), "z": Z_95,
        },
        "raw_two_sided_p_value": round(p_value, 12),
    }


def _inference_config() -> dict[str, Any]:
    return {
        "estimand": L1_ESTIMAND, "alpha": ALPHA, "interval_z": Z_95,
        "inference_method": INFERENCE_METHOD,
        "multiplicity_method": MULTIPLICITY_METHOD,
        "multiplicity_family": "all and only sufficient patterns in lexical pattern order",
    }


def validate_l1_report(report: dict[str, Any]) -> tuple[bool, str]:
    """Validate immutable L1 identity and HD11 analysis declarations."""
    if report.get("question_id") != "L1":
        return False, "L1 report has the wrong canonical question identity"
    provenance = report.get("provenance")
    if not isinstance(provenance, dict):
        return False, "L1 report is missing governed provenance"
    if provenance.get("scientific_owner") != "L1" or provenance.get("contract_version") != HD11_VERSION:
        return False, "L1 report ownership or HD11 contract version is invalid"
    if provenance.get("inference_config") != _inference_config():
        return False, "L1 inference or multiplicity configuration is invalid"
    timestamp_authority = provenance.get("timestamp_authority")
    if not isinstance(timestamp_authority, dict):
        return False, "L1 governed timestamp authority is missing"
    timestamp_records = timestamp_authority.get("records")
    if (
        timestamp_authority.get("current_semantics") != TIMESTAMP_SEMANTICS
        or timestamp_authority.get("current_normalization_version") != CANDLE_NORMALIZATION_VERSION
        or timestamp_authority.get("historical_normalization_contract") != NORMALIZATION_CONTRACT_VERSION
        or timestamp_authority.get("digest_algorithm") != "sha256"
        or not isinstance(timestamp_records, list)
    ):
        return False, "L1 governed timestamp authority configuration is invalid"
    try:
        timestamp_digest = evidence_digest(timestamp_records)
    except (TypeError, ValueError, OverflowError):
        return False, "L1 governed timestamp provenance is invalid"
    if timestamp_authority.get("digest") != timestamp_digest:
        return False, "L1 governed timestamp provenance digest is invalid"
    overall = report.get("overall")
    if not isinstance(overall, dict) or overall.get("unit_of_analysis") != "one canonical opportunity":
        return False, "L1 canonical-opportunity grain is missing"
    patterns = overall.get("patterns")
    eligible = overall.get("eligible_patterns")
    chronology = overall.get("chronology")
    if (
        not isinstance(patterns, dict) or not isinstance(eligible, list)
        or eligible != sorted(set(eligible))
        or set(patterns) != set(eligible)
        or not isinstance(chronology, dict)
        or provenance.get("multiplicity_family") != eligible
    ):
        return False, "L1 pattern family or chronology structure is invalid"
    status = str(report.get("status", ""))
    recommendation = str(report.get("recommendation", ""))
    valid_pairs = {
        "COMPLETE": {"RELIABLE_PATTERN_DEGRADATION", "NO_RELIABLE_DEGRADATION"},
        "WAITING_DATA": {"WAIT_FOR_EVIDENCE"},
        "BLOCKED": {"EVIDENCE_CONTRACT_FAILURE"},
    }
    if recommendation not in valid_pairs.get(status, set()):
        return False, "L1 status and governed conclusion are inconsistent"
    if status == "COMPLETE":
        n = overall.get("distinct_canonical_opportunities")
        if isinstance(n, bool) or not isinstance(n, int) or n < L1_MIN["overall"]:
            return False, "L1 COMPLETE report is below the overall evidence minimum"
        if len(eligible) < L1_MIN["patterns"]:
            return False, "L1 COMPLETE report is below the eligible-pattern minimum"
        if any(
            not isinstance(result, dict)
            or isinstance(result.get("early_n"), bool)
            or not isinstance(result.get("early_n"), int)
            or result["early_n"] < L1_MIN["per_pattern_window"]
            or isinstance(result.get("late_n"), bool)
            or not isinstance(result.get("late_n"), int)
            or result["late_n"] < L1_MIN["per_pattern_window"]
            or result.get("classification") not in {
                "RELIABLE_DEGRADATION", "NO_RELIABLE_DEGRADATION",
            }
            for result in patterns.values()
        ):
            return False, "L1 COMPLETE report is below a per-window evidence minimum"
        reliable = sorted(
            pattern for pattern, result in patterns.items()
            if result.get("classification") == "RELIABLE_DEGRADATION"
        )
        if reliable != overall.get("reliably_degraded_patterns"):
            return False, "L1 COMPLETE degradation classifications are inconsistent"
        expected_recommendation = (
            "RELIABLE_PATTERN_DEGRADATION" if reliable else "NO_RELIABLE_DEGRADATION"
        )
        if recommendation != expected_recommendation:
            return False, "L1 COMPLETE conclusion contradicts pattern results"
    structural_failures = overall.get("structural_failures")
    if bool(structural_failures) != (status == "BLOCKED"):
        return False, "L1 structural failure state is inconsistent"
    return True, "L1 HD11 report contract is valid"


def run_l1(
    shadow_trades: list[dict[str, Any]] | None = None,
    *,
    shadow_runtime_events: list[dict[str, Any]] | None = None,
    candle_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Answer L1 from CURRENT primary-horizon canonical opportunities."""
    if shadow_trades is None:
        from research_engine.data_access.s3_source import get_default_source
        from research_engine.data_access.shadow_runtime_ingestion import (
            load_shadow_runtime_events, reconstruct_completed_shadow_trades,
        )
        source_events = (
            list(shadow_runtime_events)
            if shadow_runtime_events is not None else load_shadow_runtime_events()
        )
        raw = reconstruct_completed_shadow_trades(source_events)
        if candle_events is not None:
            source_candles = list(candle_events)
        else:
            historical_authority_needed = any(
                event.get("event_type") == "OPEN"
                and _current_open_entry_time(event) is None
                for event in source_events if isinstance(event, dict)
            )
            source_candles = (
                get_default_source().read_dataset("events")
                if historical_authority_needed else []
            )
    else:
        raw = list(shadow_trades)
        source_events = list(shadow_runtime_events or [])
        source_candles = list(candle_events or [])

    epoch_selection = select_current_evidence("shadow_trades", raw)
    current = epoch_selection.records_for_analysis()
    opens, closes = _timestamp_sources(source_events)
    observations, entry_times, exclusions, conflicts, timestamp_provenance = (
        _select_observations(current, opens, closes, source_candles)
    )
    epoch_counts = epoch_selection.component["epoch_counts"]
    non_current_count = sum(epoch_counts[key] for key in ("TRANSITIONAL", "LEGACY", "INCOMPATIBLE"))
    mixed_epoch = bool(epoch_counts["CURRENT"] and non_current_count)
    if non_current_count:
        exclusions["non_current_or_incompatible"] = non_current_count

    observations.sort(
        key=lambda record: (_core(record, entry_times)[2], _core(record, entry_times)[0])  # type: ignore[index]
    )
    half = len(observations) // 2
    early_records = observations[:half]
    late_records = observations[-half:] if half else []
    midpoint_excluded = len(observations) - len(early_records) - len(late_records)

    early_by_pattern: dict[str, list[float]] = defaultdict(list)
    late_by_pattern: dict[str, list[float]] = defaultdict(list)
    for record in early_records:
        core = _core(record, entry_times)
        assert core is not None
        early_by_pattern[core[1]].append(core[3])
    for record in late_records:
        core = _core(record, entry_times)
        assert core is not None
        late_by_pattern[core[1]].append(core[3])
    all_patterns = sorted(set(early_by_pattern) | set(late_by_pattern))
    eligible_patterns = [
        pattern for pattern in all_patterns
        if len(early_by_pattern[pattern]) >= L1_MIN["per_pattern_window"]
        and len(late_by_pattern[pattern]) >= L1_MIN["per_pattern_window"]
    ]
    results = {pattern: _pattern_test(early_by_pattern[pattern], late_by_pattern[pattern]) for pattern in eligible_patterns}
    adjusted = _holm_adjust([(pattern, result["raw_two_sided_p_value"]) for pattern, result in results.items()])
    degraded: list[str] = []
    for pattern in eligible_patterns:
        result = results[pattern]
        result["holm_adjusted_p_value"] = round(adjusted[pattern], 12)
        reliable = (
            result["late_minus_early_mean_r"] < 0
            and result["interval_95"]["upper_r"] < 0
            and adjusted[pattern] <= ALPHA
        )
        result["classification"] = "RELIABLE_DEGRADATION" if reliable else "NO_RELIABLE_DEGRADATION"
        if reliable:
            degraded.append(pattern)

    structural_failures = []
    if conflicts:
        structural_failures.append("conflicting primary evidence for canonical opportunity")
    if exclusions.get("unresolved_governed_entry_time", 0):
        structural_failures.append("unresolved governed shadow OPEN entry-time authority")
    if mixed_epoch:
        structural_failures.append("mixed evidence epochs")
    sufficient = len(observations) >= L1_MIN["overall"] and len(eligible_patterns) >= L1_MIN["patterns"]
    if structural_failures:
        status, recommendation, finding = "BLOCKED", "EVIDENCE_CONTRACT_FAILURE", "L1 evidence contract failed closed"
    elif not sufficient:
        status, recommendation, finding = "WAITING_DATA", "WAIT_FOR_EVIDENCE", "Insufficient valid CURRENT evidence for L1 pattern degradation"
    else:
        status = "COMPLETE"
        recommendation = "RELIABLE_PATTERN_DEGRADATION" if degraded else "NO_RELIABLE_DEGRADATION"
        finding = f"Reliable degradation detected for {', '.join(degraded)}" if degraded else "No reliable per-pattern degradation detected"

    selection = attest_current_subset("shadow_trades", raw, observations)
    evidence_provenance = build_evidence_provenance(selection)
    fingerprint = build_fingerprint_from_provenance(evidence_provenance, validation_score="BLOCKED" if structural_failures else "CURRENT")
    chronology = {
        "authority": "governed shadow OPEN construction entry_time (canonical UTC epoch seconds)",
        "resolution": (
            "current producer canonical UTC fields or "
            "shadow_post_candle_utc_normalization_v1 for affected historical evidence"
        ),
        "order": "entry_time ascending; canonical_opportunity_id lexical tie-break",
        "windows": "non-overlapping equal early and late halves",
        "early_n": len(early_records), "late_n": len(late_records),
        "midpoint_excluded": midpoint_excluded,
        "early_last": None, "late_first": None,
    }
    if early_records:
        core = _core(early_records[-1], entry_times); assert core is not None
        chronology["early_last"] = {"entry_time": core[2], "canonical_opportunity_id": core[0]}
    if late_records:
        core = _core(late_records[0], entry_times); assert core is not None
        chronology["late_first"] = {"entry_time": core[2], "canonical_opportunity_id": core[0]}

    overall = {
        "finding": finding,
        "unit_of_analysis": "one canonical opportunity",
        "population": "CURRENT completed PRIMARY_HORIZON_SIMULATION shadow outcomes",
        "distinct_canonical_opportunities": len(observations),
        "chronology": chronology,
        "estimand": L1_ESTIMAND, "inference": _inference_config(),
        "requirements": {
            "minimum_overall_opportunities": L1_MIN["overall"],
            "minimum_per_pattern_per_window": L1_MIN["per_pattern_window"],
            "minimum_eligible_patterns": L1_MIN["patterns"],
        },
        "eligible_patterns": eligible_patterns, "patterns": results,
        "reliably_degraded_patterns": degraded,
        "exclusions": dict(sorted(exclusions.items())),
        "structural_failures": structural_failures,
        "conflicting_canonical_opportunity_ids": sorted(conflicts),
        "unresolved_timestamp_opportunity_ids": sorted(set(
            item["identity"][1] for item in timestamp_provenance
            if item.get("reason") and len(item.get("identity", [])) == 3
        )),
    }
    return build_report(
        question_id="L1", status=status, overall=overall,
        confidence="HIGH" if status == "COMPLETE" else "INSUFFICIENT_DATA",
        dataset={"source": "shadow_trades_v1 normalized from shadow_runtime_v1", "sample_size": len(observations), "records_loaded": len(raw)},
        fingerprint=fingerprint, recommendation=recommendation,
        assumptions=[
            "Chronological windows and lexical tie-breaking are fixed before outcomes are inspected.",
            "Account fanout and duplicate identical primary rows collapse to one canonical opportunity.",
            "This observational drift result has no production-mutation authority.",
        ],
        warnings=["Shadow outcomes are simulated research evidence, not realised live P&L."],
        provenance={
            "experiment_module": __name__, "registry_id": "L1", "scientific_owner": "L1",
            "contract_version": HD11_VERSION, "report_identity": REPORT_FILENAME,
            "evidence_identity": {
                "source": "shadow_trades", "schema": "shadow_trades_v1",
                "primary_shadow_type": PRIMARY_OUTCOME,
                "opportunity_key": "identity.canonical_opportunity_id",
                "chronology_authority": "shadow OPEN entry_market_time canonical UTC epoch seconds",
                "historical_normalization_contract": NORMALIZATION_CONTRACT_VERSION,
            },
            "timestamp_authority": {
                "current_semantics": TIMESTAMP_SEMANTICS,
                "current_normalization_version": CANDLE_NORMALIZATION_VERSION,
                "historical_normalization_contract": NORMALIZATION_CONTRACT_VERSION,
                "records": timestamp_provenance,
                "digest_algorithm": "sha256",
                "digest": evidence_digest(timestamp_provenance),
            },
            "inference_config": _inference_config(), "multiplicity_family": eligible_patterns,
            "production_mutation_authority": False,
        },
    )
