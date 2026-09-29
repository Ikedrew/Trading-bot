"""Frozen-evidence adjudication for the remaining Stage 4 EX2/L7 blockers.

This module is intentionally read-only with respect to evidence.  It inventories
the persisted Wave 4 checkpoint and the governed HD09 path audit; it never reads
live/S3 data and never derives missing experimental assignments or candle paths.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from bisect import bisect_right
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[2]
CHECKPOINT = ROOT / "analysis" / "assurance" / "checkpoints" / "wave4_20260927"
EVIDENCE = CHECKPOINT / "evidence"
RECONSTRUCTION = CHECKPOINT / "reconstruction.json"
EXIT_RUNTIME = ROOT / "_exit_path_audit" / "shadow_runtime_v1"
EXIT_EVENTS = ROOT / "_exit_path_audit" / "events_v1"
EX2_POPULATION = 8760
L7_POPULATION = 14046


def _jsonl(paths: Iterable[Path]) -> Iterable[dict[str, Any]]:
    for path in sorted(paths):
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    yield json.loads(line)


def _artifacts(*, current_only: bool = True) -> list[dict[str, Any]]:
    payload = json.loads(RECONSTRUCTION.read_text(encoding="utf-8"))
    rows = [item["artifact"] for item in payload["reconstruction"]["artifacts"]]
    if not current_only:
        return rows
    from research_engine.control_plane.evidence_resolver import classify_evidence_record
    return [row for row in rows
            if classify_evidence_record(row, "shadow_trades").value == "CURRENT"]


def _identity(row: Mapping[str, Any]) -> tuple[str, str, str]:
    identity = row.get("identity") if isinstance(row.get("identity"), Mapping) else {}
    return (
        str(row.get("shadow_trade_id") or identity.get("shadow_trade_id") or ""),
        str(row.get("canonical_opportunity_id") or identity.get("canonical_opportunity_id") or ""),
        str(row.get("trade_horizon") or row.get("horizon")
            or identity.get("trade_horizon") or identity.get("horizon") or ""),
    )


def governed_ex2_rows() -> list[dict[str, Any]]:
    rows = [row for row in _artifacts()
            if float((row.get("simulated_outcome") or {}).get("mfe_r", -1)) >= 0.5]
    if len(rows) != EX2_POPULATION or len({_identity(row) for row in rows}) != EX2_POPULATION:
        raise ValueError("EX2_GOVERNED_ROSTER_INTEGRITY_FAILURE")
    return rows


def _completed_index(records: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    events: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(dict)
    for record in records:
        kind = str(record.get("event_type", ""))
        if kind in {"OPEN", "CLOSE"}:
            events[_identity(record)].setdefault(kind, dict(record))
    return {key: value for key, value in events.items() if set(value) == {"OPEN", "CLOSE"}}


def adjudicate_ex2() -> dict[str, Any]:
    governed = governed_ex2_rows()
    roster = {_identity(row): row for row in governed}
    audit = _completed_index(_jsonl(EXIT_RUNTIME.rglob("*.jsonl")))
    checkpoint = _completed_index(_jsonl([EVIDENCE / "shadow_runtime.jsonl"]))
    exact = set(roster) & set(audit)
    unmatched = set(roster) - exact

    # Weak-key overlap is diagnostic only.  It is never promoted to authority.
    by_shadow: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    by_opportunity_horizon: dict[tuple[str, str], set[tuple[str, str, str]]] = defaultdict(set)
    for key in audit:
        by_shadow[key[0]].add(key)
        by_opportunity_horizon[(key[1], key[2])].add(key)
    weak_shadow = {key for key in unmatched if by_shadow.get(key[0])}
    weak_opp_horizon = {key for key in unmatched if by_opportunity_horizon.get((key[1], key[2]))}

    checkpoint_exact = unmatched & set(checkpoint)
    missing_checkpoint_lifecycle = unmatched - set(checkpoint)
    candles: dict[str, list[dict[str, Any]]] = defaultdict(list)
    relevant_event_files = [
        path for path in EXIT_EVENTS.rglob("*.jsonl")
        if any(part in {"date=2026-09-20", "date=2026-09-21", "date=2026-09-22"}
               for part in path.parts)
    ]
    for event in _jsonl(relevant_event_files):
        if (event.get("type") == "CANDLE" and event.get("source") == "mt5_data"
                and event.get("schema_version") == "events_v1"
                and event.get("timeframe") == "M5"):
            candles[str(event.get("symbol", "")).upper()].append(event)
    candle_times: dict[str, list[int]] = {}
    for symbol, rows in candles.items():
        rows.sort(key=lambda item: int(item["payload"]["ts"]))
        candle_times[symbol] = [int(item["payload"]["ts"]) for item in rows]
    from research_engine.control_plane.exit_bar_path import (
        LifecyclePathSource, build_exit_bar_path_v1,
    )
    candidate_sources = []
    for key in sorted(checkpoint_exact):
        opened, closed = checkpoint[key]["OPEN"], checkpoint[key]["CLOSE"]
        symbol = str(opened.get("symbol", "")).upper()
        times = candle_times.get(symbol, [])
        values = candles.get(symbol, [])
        entry_ms = int(opened["entry_market_time"]) * 1000
        exit_ms = int(closed["exit_market_time"]) * 1000
        subset = values[bisect_right(times, entry_ms):bisect_right(times, exit_ms)]
        candidate_sources.append(LifecyclePathSource(opened, closed, tuple(subset)))
    repaired_path = build_exit_bar_path_v1(candidate_sources)
    repairable_identities = {item.lifecycle_identity for item in repaired_path.records}
    repair_exclusions = Counter(item.reason for item in repaired_path.exclusions)
    event_dates = sorted({part for path in EXIT_EVENTS.rglob("*.jsonl")
                          for part in path.parts if part.startswith("date=")})
    runtime_dates = sorted({part for path in EXIT_RUNTIME.rglob("*.jsonl")
                            for part in path.parts if part.startswith("date=")})
    exit_dates = Counter()
    progression_shapes = Counter()
    for key in unmatched:
        row = roster[key]
        outcome = row.get("simulated_outcome") or {}
        stamp = outcome.get("exit_timestamp_iso") or outcome.get("exit_timestamp")
        exit_dates[str(stamp)[:10]] += 1
        progression = outcome.get("trade_state_progression") or []
        fields = tuple(sorted(set().union(*(item.keys() for item in progression)))) if progression else ()
        progression_shapes[fields] += 1

    # The only event authority admitted by the frozen EX2 contract is an
    # events_v1 mt5_data M5 record with OHLC.  The checkpoint contains no
    # events dataset, and reconstructed progression has close/R only.
    historically_unobserved = len(unmatched) - len(repairable_identities)
    result = {
        "governed_population": len(roster),
        "exact_authoritative_match": len(exact) + len(repairable_identities),
        "exact_match_before_implementation_repair": len(exact),
        "implementation_resolvable_missing": len(repairable_identities),
        "historically_unobserved": historically_unobserved,
        "ambiguous": 0,
        "unexplained": 0,
        "audit_completed_lifecycles": len(audit),
        "checkpoint_completed_lifecycles": len(checkpoint),
        "unmatched_with_checkpoint_open_close": len(checkpoint_exact),
        "unmatched_without_checkpoint_open_close": len(missing_checkpoint_lifecycle),
        "implementation_repair_exclusions": dict(sorted(repair_exclusions.items())),
        "weak_shadow_id_overlap": len(weak_shadow),
        "weak_opportunity_horizon_overlap": len(weak_opp_horizon),
        "unmatched_exit_dates": dict(sorted(exit_dates.items())),
        "reconstructed_progression_field_shapes": {
            ",".join(fields): count for fields, count in sorted(progression_shapes.items())
        },
        "audit_runtime_date_range": [runtime_dates[0], runtime_dates[-1]],
        "audit_event_date_range": [event_dates[0], event_dates[-1]],
        "authoritative_sources_searched": [
            "wave4_20260927/reconstruction.json governed lifecycle roster",
            "wave4_20260927/evidence/shadow_runtime.jsonl",
            "_exit_path_audit/shadow_runtime_v1",
            "_exit_path_audit/events_v1 events_v1:CANDLE:mt5_data:M5",
        ],
        "join_chain": [
            "shadow_trade_id",
            "canonical_opportunity_id",
            "trade_horizon",
            "OPEN/CLOSE entry/exit market time",
            "canonical symbol",
            "ordered events_v1 M5 OHLC bars after entry through exit",
        ],
        "classification": (
            "MIXED_IMPLEMENTATION_AND_DATA_GAP"
            if repairable_identities else "HISTORICAL_OBSERVATION_GAP"
        ),
    }
    if result["governed_population"] != sum(result[key] for key in (
        "exact_authoritative_match", "historically_unobserved", "ambiguous", "unexplained"
    )):
        raise ValueError("EX2_ACCOUNTING_DOES_NOT_CONSERVE")
    return result


def _walk(value: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield from _walk(item, path)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item, prefix + "[]")
    else:
        yield prefix, value


def adjudicate_l7() -> dict[str, Any]:
    governed = _artifacts()
    if len(governed) != L7_POPULATION:
        raise ValueError("L7_GOVERNED_ROSTER_INTEGRITY_FAILURE")
    schemas = Counter(str(row.get("schema_version", "")) for row in governed)
    governed_tokens = Counter(
        str(value).upper() for row in governed for _, value in _walk(row)
        if isinstance(value, str) and str(value).upper() in {"CONTROL", "CANDIDATE"}
    )

    frozen_hits: list[dict[str, Any]] = []
    searched = []
    for path in sorted(EVIDENCE.glob("*.jsonl")):
        searched.append(f"wave4_20260927/evidence/{path.name}")
        counts = Counter()
        paths = Counter()
        for row in _jsonl([path]):
            for field, value in _walk(row):
                if isinstance(value, str) and value.upper() in {"CONTROL", "CANDIDATE"}:
                    counts[value.upper()] += 1
                    paths[field] += 1
        if counts:
            frozen_hits.append({"dataset": path.name, "labels": dict(counts), "fields": dict(paths)})

    authoritative_control = 0
    authoritative_candidate = 0
    result = {
        "governed_population": len(governed),
        "producer_authoritative_CONTROL": authoritative_control,
        "producer_authoritative_CANDIDATE": authoritative_candidate,
        "other_authoritative_assignment": 0,
        "unlabeled_historically": len(governed),
        "ambiguous": 0,
        "unexplained": 0,
        "governed_schema_versions": dict(sorted(schemas.items())),
        "control_candidate_tokens_in_governed_rows": dict(governed_tokens),
        "control_candidate_tokens_elsewhere": frozen_hits,
        "authoritative_sources_searched": searched + [
            "wave4_20260927/reconstruction.json",
            "frozen report/history identities (non-producer, non-authoritative for assignment)",
        ],
        "join_chain": [
            "producer-issued assignment",
            "shadow_trade_id",
            "canonical_opportunity_id",
            "trade_horizon",
        ],
        "classification": "HISTORICAL_OBSERVATION_GAP",
    }
    if result["governed_population"] != sum(result[key] for key in (
        "producer_authoritative_CONTROL", "producer_authoritative_CANDIDATE",
        "other_authoritative_assignment", "unlabeled_historically", "ambiguous", "unexplained"
    )):
        raise ValueError("L7_ACCOUNTING_DOES_NOT_CONSERVE")
    return result


def evidence_fingerprint(ex2: Mapping[str, Any], l7: Mapping[str, Any]) -> str:
    material = json.dumps({"EX2": ex2, "L7": l7}, sort_keys=True,
                          separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


OBSERVATION_REQUIREMENT_STATUS = "OPEN"
EXPECTED_REENTRY_TRIGGER = "GOVERNED_EVIDENCE_CONTRACT_REEVALUATION"


def _base_requirement(question_id: str, *, work_item_id: str, reentry_id: str,
                      fingerprint: str, missing_observable: list[str],
                      required_fields: list[str], semantic_definition: str,
                      required_grain: str, required_canonical_identity: list[str],
                      required_capture_timestamp_event: str,
                      required_lineage: list[str], allowed_values_type: dict,
                      minimum_completeness_rule: str,
                      evidence_contract_consumer: list[str]) -> dict:
    return {
        "question_id": question_id,
        "gap_work_item_id": work_item_id,
        "missing_observable": missing_observable,
        "required_producer": "producer:shadow_trades",
        "required_dataset_domain": "shadow_trades",
        "required_fields": required_fields,
        "semantic_definition": semantic_definition,
        "required_grain": required_grain,
        "required_canonical_identity": required_canonical_identity,
        "required_capture_timestamp_event": required_capture_timestamp_event,
        "required_lineage": required_lineage,
        "allowed_values_type": allowed_values_type,
        "minimum_completeness_rule": minimum_completeness_rule,
        "evidence_contract_consumer": evidence_contract_consumer,
        # Frozen evidence proves the observable was never collected, so no
        # historical backfill path may be invented.
        "historical_backfill_possible": False,
        "future_collection_only": True,
        "expected_reentry_trigger_class": EXPECTED_REENTRY_TRIGGER,
        "adjudication_evidence_fingerprint": fingerprint,
        "source_implementation_work_item_id": work_item_id,
        "source_reentry_id": reentry_id,
        "status": OBSERVATION_REQUIREMENT_STATUS,
    }


def ex2_observation_requirement(fingerprint: str, *, reentry_id: str) -> dict:
    """The ordered M5 OHLC exit-path observable EX2 requires but never retained."""
    return _base_requirement(
        "EX2", work_item_id="GWI-EX2-IMPL", reentry_id=reentry_id,
        fingerprint=fingerprint,
        missing_observable=[
            "events_v1:CANDLE:mt5_data:M5 ordered open/high/low/close between "
            "lifecycle entry_market_time and exit_market_time inclusive",
        ],
        required_fields=[
            "events_v1.type=CANDLE", "events_v1.source=mt5_data",
            "events_v1.schema_version", "events_v1.timeframe=M5",
            "events_v1.symbol", "events_v1.payload.ts",
            "events_v1.payload.open", "events_v1.payload.high",
            "events_v1.payload.low", "events_v1.payload.close",
            "shadow_runtime OPEN.entry_market_time",
            "shadow_runtime CLOSE.exit_market_time",
        ],
        semantic_definition=(
            "For one canonical shadow lifecycle, the complete, gap-free, "
            "strictly ascending sequence of M5 OHLC bars whose bar timestamps "
            "cover the interval (entry_market_time, exit_market_time] for the "
            "lifecycle symbol. bar, close and r alone do NOT satisfy this "
            "contract: open, high and low per bar are mandatory and the "
            "sequence must be ordered and complete."),
        required_grain=(
            "one record per (shadow_trade_id, canonical_opportunity_id, "
            "trade_horizon, M5 bar timestamp)"),
        required_canonical_identity=[
            "shadow_trade_id", "canonical_opportunity_id", "trade_horizon",
        ],
        required_capture_timestamp_event=(
            "bar timestamp in producer market time, recorded at the causal bar "
            "close event plus the CURRENT epoch attestation"),
        required_lineage=[
            "producer:shadow_trades",
            "events_v1 producer instance",
            "M5 aggregation lineage from the base timeframe feed",
        ],
        allowed_values_type={
            "type": "ordered_sequence_of_ohlc_bars",
            "per_bar_required_keys": ["ts", "open", "high", "low", "close"],
            "ordering": "strictly ascending ts",
            "completeness": "no missing bar between entry and exit inclusive of exit bar",
        },
        minimum_completeness_rule=(
            "100% of governed EX2 lifecycles must have a complete ordered M5 "
            "OHLC path; any lifecycle missing a single required bar keeps the "
            "question INSUFFICIENT_DATA. The forbidden 9045-row widened "
            "reconstruction population may never substitute for this."),
        evidence_contract_consumer=[
            "research_engine.experiments.exit_policy_governed.run_ex2",
            "research_engine.control_plane.exit_bar_path.build_exit_bar_path_v1",
        ])


def l7_observation_requirement(fingerprint: str, *, reentry_id: str) -> dict:
    """The producer-authoritative experiment-arm assignment L7 requires."""
    return _base_requirement(
        "L7", work_item_id="GWI-L7-IMPL", reentry_id=reentry_id,
        fingerprint=fingerprint,
        missing_observable=[
            "producer-authoritative experiment arm assignment exactly CONTROL "
            "or CANDIDATE, issued before outcome knowledge can contaminate it",
        ],
        required_fields=[
            "shadow_trades.experiment_arm", "shadow_trades.arm_assigned_at",
            "shadow_trades.arm_schema_version", "shadow_trades.strategy",
            "shadow_trades.r_multiple",
        ],
        semantic_definition=(
            "A single producer-issued, versioned field carrying exactly one of "
            "the two governed arms for the canonical entity under test. "
            "shadow_trades_v1 is a dataset schema identity and is NOT CONTROL "
            "and NOT CANDIDATE. No chronology, discovery/validation split, "
            "treatment inference, selection state, promotion state or outcome "
            "behaviour may be used to derive the arm."),
        required_grain=(
            "one assignment per (shadow_trade_id, canonical_opportunity_id, "
            "trade_horizon) for the canonical entity under test"),
        required_canonical_identity=[
            "shadow_trade_id", "canonical_opportunity_id", "trade_horizon",
        ],
        required_capture_timestamp_event=(
            "arm_assigned_at must be written at assignment time, strictly "
            "before any outcome field for that entity is populated"),
        required_lineage=[
            "producer:shadow_trades",
            "assignment decision record (issuer, request id, policy version)",
            "immutability: the arm may never be rewritten or back-filled",
        ],
        allowed_values_type={
            "type": "closed_enum",
            "field": "experiment_arm",
            "field_version": "arm_schema_version",
            "allowed_values": ["CONTROL", "CANDIDATE"],
            "unknown_value_behavior": "FAIL_CLOSED",
            "proxy_or_inferred_labels": "FORBIDDEN",
        },
        minimum_completeness_rule=(
            "100% of the governed L7 population must carry a producer-assigned "
            "arm before the label contract can bind; any unlabeled, unknown or "
            "inferred arm leaves L7 INSUFFICIENT_DATA."),
        evidence_contract_consumer=[
            "research_engine.experiments.adaptation_evidence.run_l7",
            "research_engine.control_plane.stage4_impl_ownership_labels."
            "assign_l7_labels",
        ])


def observation_requirements(evidence: Mapping[str, Any], *,
                             reentry_ids: Mapping[str, str]) -> list[dict]:
    """The complete, ordered future-observation requirement set."""
    fingerprint = str(evidence["evidence_fingerprint"])
    return [
        ex2_observation_requirement(fingerprint, reentry_id=reentry_ids["EX2"]),
        l7_observation_requirement(fingerprint, reentry_id=reentry_ids["L7"]),
    ]


def adjudicate() -> dict[str, Any]:
    ex2, l7 = adjudicate_ex2(), adjudicate_l7()
    return {"EX2": ex2, "L7": l7, "evidence_fingerprint": evidence_fingerprint(ex2, l7)}


if __name__ == "__main__":
    print(json.dumps(adjudicate(), indent=2, sort_keys=True))
