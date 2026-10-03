"""Generic, restart-safe pairing of authoritative baseline and candidate CLOSEs."""

from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.shadow.candidate_evaluation_persistence import (
    CandidateEvaluationWriter,
)

logger = logging.getLogger(__name__)
SCHEMA_VERSION = "shadow_candidate_evaluation_v1"


def _event_key(event: dict[str, Any]) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":"), default=str)


def _unique_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for event in events:
        key = _event_key(event)
        if key not in seen:
            seen.add(key)
            result.append(event)
    return result


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _read_event_directory(base_dir: str, label: str
                          ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    root = Path(base_dir)
    events: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    if not root.exists():
        return events, errors
    try:
        paths = sorted(root.rglob("*.jsonl"))
    except OSError as exc:
        return events, [{
            "reason": f"{label}_SOURCE_READ_FAILURE",
            "identity": "",
            "diagnostic": str(exc),
        }]
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            errors.append({
                "reason": f"{label}_SOURCE_READ_FAILURE",
                "identity": "",
                "diagnostic": f"{path}:{exc}",
            })
            continue
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append({
                    "reason": f"{label}_SOURCE_CORRUPT_RECORD",
                    "identity": "",
                    "diagnostic": f"{path}:{line_number}:{exc}",
                })
                continue
            if not isinstance(event, dict):
                errors.append({
                    "reason": f"{label}_SOURCE_INVALID_RECORD",
                    "identity": "",
                    "diagnostic": f"{path}:{line_number}",
                })
                continue
            events.append(event)
    for error in errors:
        logger.error(
            "[SHADOW_CANDIDATE_EVALUATION_SOURCE_INVALID] %s %s",
            error["reason"], error.get("diagnostic", ""),
        )
    return events, errors


def _pair_id(*, shadow_trade_id: str, canonical_opportunity_id: str,
             trade_horizon: str, candidate_id: str, policy_id: str,
             treatment_hash: str) -> str:
    fields = (
        shadow_trade_id, canonical_opportunity_id, trade_horizon.upper(),
        candidate_id, policy_id, treatment_hash,
    )
    digest = hashlib.sha256("|".join(fields).encode("utf-8")).hexdigest()[:32]
    return f"pair_{digest}"


class CandidateEvaluation:
    """Build persisted pairs from the two truth streams; never changes either."""

    def __init__(self, writer: CandidateEvaluationWriter | None = None,
                 baseline_dir: str | None = None,
                 candidate_dir: str | None = None,
                 candidate_runtime=None) -> None:
        self._writer = writer or CandidateEvaluationWriter()
        self._baseline_dir = baseline_dir
        self._candidate_dir = candidate_dir
        self._candidate_runtime = candidate_runtime
        self._last_errors: list[dict[str, str]] = []

    def _sources(self):
        from core.shadow.candidate_runtime import get_candidate_runtime

        if self._baseline_dir:
            baseline_dir = self._baseline_dir
        else:
            from core.shadow.persistence import get_base_dir
            baseline_dir = get_base_dir()
        if self._candidate_dir:
            candidate_dir = self._candidate_dir
        elif self._candidate_runtime is not None:
            candidate_dir = self._candidate_runtime._writer.base_dir
        else:
            candidate_dir = get_candidate_runtime()._writer.base_dir
        runtime = self._candidate_runtime or get_candidate_runtime()
        registrations = runtime.registrations()
        baseline_events, baseline_errors = _read_event_directory(
            baseline_dir, "BASELINE"
        )
        candidate_events, candidate_errors = _read_event_directory(
            candidate_dir, "CANDIDATE"
        )
        return (
            baseline_events,
            candidate_events,
            registrations,
            baseline_errors + candidate_errors,
        )

    def reconcile(self) -> dict[str, Any]:
        baseline_events, candidate_events, registrations, source_errors = self._sources()
        registration_map = {
            (str(reg.candidate_id), str(reg.policy_id)): reg
            for reg in registrations
        }
        persisted, persisted_errors = _read_event_directory(
            self._writer.base_dir, "EVALUATION"
        )
        errors: list[dict[str, str]] = source_errors + persisted_errors
        persisted_pairs: dict[str, dict[str, Any]] = {}
        for event in persisted:
            if (
                event.get("event_type") != "PAIRED_OUTCOME"
                or event.get("schema_version") != SCHEMA_VERSION
                or not event.get("pair_id")
            ):
                continue
            pair_id = str(event["pair_id"])
            if pair_id in persisted_pairs:
                errors.append({
                    "reason": "EVALUATION_DUPLICATE_PAIRED_RECORD",
                    "identity": pair_id,
                })
                continue
            persisted_pairs[pair_id] = event
        baseline_opens: dict[str, list[dict[str, Any]]] = {}
        baseline_closes: dict[str, list[dict[str, Any]]] = {}
        candidate_opens: dict[str, list[dict[str, Any]]] = {}
        candidate_closes: dict[str, list[dict[str, Any]]] = {}

        for event in baseline_events:
            tid = str(event.get("shadow_trade_id") or "")
            if event.get("event_type") == "OPEN" and tid:
                baseline_opens.setdefault(tid, []).append(event)
            elif event.get("event_type") == "CLOSE" and tid:
                baseline_closes.setdefault(tid, []).append(event)
            elif event.get("event_type") == "CLOSE":
                errors.append({
                    "reason": "BASELINE_CLOSE_IDENTITY_MISSING",
                    "identity": "",
                })
        for event in candidate_events:
            rid = str(event.get("candidate_runtime_id") or "")
            if event.get("event_type") == "CANDIDATE_OPEN" and rid:
                candidate_opens.setdefault(rid, []).append(event)
            elif event.get("event_type") == "CANDIDATE_CLOSE" and rid:
                candidate_closes.setdefault(rid, []).append(event)
            elif event.get("event_type") == "CANDIDATE_CLOSE":
                errors.append({
                    "reason": "CANDIDATE_RUNTIME_ID_MISSING",
                    "identity": "",
                })

        for group in (baseline_opens, baseline_closes, candidate_opens, candidate_closes):
            for key in list(group):
                group[key] = _unique_events(group[key])

        created = 0
        for runtime_id, closes in candidate_closes.items():
            if len(closes) != 1:
                errors.append({
                    "reason": "DUPLICATE_CANDIDATE_TERMINAL_OUTCOME",
                    "identity": runtime_id,
                })
                continue
            candidate_close = closes[0]
            candidate_open_list = candidate_opens.get(runtime_id, [])
            if len(candidate_open_list) != 1:
                errors.append({
                    "reason": "MISSING_OR_AMBIGUOUS_CANDIDATE_OPEN",
                    "identity": runtime_id,
                })
                continue
            candidate_open = candidate_open_list[0]
            candidate_state = dict(candidate_close.get("state") or {})
            candidate_id = str(candidate_close.get("candidate_id") or "")
            policy_id = str(candidate_close.get("policy_id") or "")
            treatment_hash = str(candidate_close.get("treatment_hash") or "")
            shadow_trade_id = str(candidate_close.get("shadow_trade_id") or "")
            canonical_id = str(candidate_close.get("canonical_opportunity_id") or "")
            horizon = str(candidate_close.get("trade_horizon") or "")
            identity_values = (
                shadow_trade_id, canonical_id, horizon, candidate_id,
                policy_id, treatment_hash,
            )
            if not all(identity_values):
                errors.append({
                    "reason": "CANDIDATE_IDENTITY_MISSING",
                    "identity": runtime_id,
                })
                continue

            registration = registration_map.get((candidate_id, policy_id))
            if registration is None or str(registration.treatment_hash) != treatment_hash:
                errors.append({
                    "reason": "TREATMENT_HASH_MISMATCH_OR_UNREGISTERED",
                    "identity": runtime_id,
                })
                continue
            candidate_entry_time = int(
                candidate_open.get("bar_time_utc")
                or candidate_state.get("entry_time") or 0)
            if candidate_entry_time < int(
                    getattr(registration, "activation_frontier_epoch_s", 0) or 0):
                # Historical/offline evidence is lineage only, never prospective n.
                continue
            if (
                candidate_open.get("shadow_trade_id") != shadow_trade_id
                or candidate_open.get("canonical_opportunity_id") != canonical_id
                or str(candidate_open.get("trade_horizon") or "").upper() != horizon.upper()
                or candidate_open.get("treatment_hash") != treatment_hash
                or candidate_open.get("candidate_id") != candidate_id
                or candidate_open.get("policy_id") != policy_id
            ):
                errors.append({
                    "reason": "CANDIDATE_OPEN_CLOSE_IDENTITY_MISMATCH",
                    "identity": runtime_id,
                })
                continue

            baseline_open_list = baseline_opens.get(shadow_trade_id, [])
            baseline_close_list = baseline_closes.get(shadow_trade_id, [])
            if len(baseline_open_list) != 1:
                errors.append({
                    "reason": "MISSING_OR_AMBIGUOUS_BASELINE_OPEN",
                    "identity": shadow_trade_id,
                })
                continue
            baseline_open = baseline_open_list[0]
            baseline_open_horizon = str(
                baseline_open.get("horizon")
                or dict(baseline_open.get("identity") or {}).get("trade_horizon")
                or dict(baseline_open.get("identity") or {}).get("evaluated_horizon")
                or ""
            )
            if (
                baseline_open.get("canonical_opportunity_id") != canonical_id
                or baseline_open_horizon.upper() != horizon.upper()
            ):
                errors.append({
                    "reason": "BASELINE_CANDIDATE_CANONICAL_OR_HORIZON_MISMATCH",
                    "identity": runtime_id,
                })
                continue
            if not baseline_close_list:
                continue
            if len(baseline_close_list) != 1:
                errors.append({
                    "reason": "AMBIGUOUS_BASELINE_TERMINAL_OUTCOME",
                    "identity": shadow_trade_id,
                })
                continue
            baseline_close = baseline_close_list[0]
            baseline_horizon = str(
                baseline_close.get("horizon")
                or dict(baseline_close.get("identity") or {}).get("trade_horizon")
                or dict(baseline_close.get("identity") or {}).get("evaluated_horizon")
                or ""
            )
            if (
                baseline_close.get("canonical_opportunity_id") != canonical_id
                or baseline_open.get("canonical_opportunity_id") != canonical_id
                or baseline_horizon.upper() != horizon.upper()
            ):
                errors.append({
                    "reason": "BASELINE_CANDIDATE_CANONICAL_OR_HORIZON_MISMATCH",
                    "identity": runtime_id,
                })
                continue

            arm_assignment = dict(baseline_open.get("experiment_arm") or {})
            experiment_id = str(arm_assignment.get("experiment_id") or "")
            experiment_arm = str(arm_assignment.get("experiment_arm") or "")
            if (
                experiment_arm != "CANDIDATE"
                or str(candidate_close.get("experiment_arm") or candidate_state.get(
                    "experiment_arm", ""
                )) != experiment_arm
                or str(candidate_close.get("experiment_id") or candidate_state.get(
                    "experiment_id", ""
                )) != experiment_id
                or str(candidate_open.get("experiment_arm") or "") != experiment_arm
                or str(candidate_open.get("experiment_id") or "") != experiment_id
            ):
                errors.append({
                    "reason": "EXPERIMENT_ARM_LINEAGE_MISMATCH",
                    "identity": runtime_id,
                })
                continue

            baseline_outcome = dict(baseline_close.get("outcome") or {})
            candidate_outcome = dict(candidate_close.get("outcome") or {})
            baseline_r = _finite_number(baseline_outcome.get("pnl_r_multiple"))
            candidate_r = _finite_number(candidate_outcome.get("candidate_r"))
            if baseline_r is None or candidate_r is None:
                errors.append({
                    "reason": "TERMINAL_R_OUTCOME_MISSING_OR_NONFINITE",
                    "identity": runtime_id,
                })
                continue

            pid = _pair_id(
                shadow_trade_id=shadow_trade_id,
                canonical_opportunity_id=canonical_id,
                trade_horizon=horizon,
                candidate_id=candidate_id,
                policy_id=policy_id,
                treatment_hash=treatment_hash,
            )
            if pid in persisted_pairs:
                continue
            delta = candidate_r - baseline_r
            classification = (
                "IMPROVED" if delta > 0
                else "WORSENED" if delta < 0
                else "UNCHANGED"
            )
            baseline_exit_time = int(
                baseline_close.get("exit_market_time_utc_epoch_s")
                or baseline_close.get("exit_market_time")
                or 0
            )
            candidate_exit_time = int(
                candidate_outcome.get("exit_time")
                or candidate_close.get("bar_time_utc")
                or 0
            )
            record = {
                "schema_version": SCHEMA_VERSION,
                "event_type": "PAIRED_OUTCOME",
                "pair_id": pid,
                "candidate_runtime_id": runtime_id,
                "candidate_id": candidate_id,
                "policy_id": policy_id,
                "treatment_hash": treatment_hash,
                "shadow_trade_id": shadow_trade_id,
                "canonical_opportunity_id": canonical_id,
                "trade_horizon": horizon,
                "experiment_id": experiment_id,
                "experiment_arm": experiment_arm,
                "baseline_r": baseline_r,
                "candidate_r": candidate_r,
                "paired_delta_r": delta,
                "baseline_exit_reason": str(baseline_close.get("exit_reason") or ""),
                "candidate_exit_reason": str(candidate_outcome.get("exit_reason") or ""),
                "baseline_exit_time": baseline_exit_time,
                "candidate_exit_time": candidate_exit_time,
                "candidate_entry_time": candidate_entry_time,
                "outcome_classification": classification,
                "lineage": {
                    "baseline_event_id": str(baseline_close.get("event_id") or ""),
                    "baseline_open_event_id": str(baseline_open.get("event_id") or ""),
                    "candidate_open_event_id": str(
                        candidate_open.get("event_id") or ""
                    ),
                    "candidate_close_event_id": str(
                        candidate_close.get("event_id") or ""
                    ),
                    "baseline_record_lineage": dict(
                        baseline_close.get("record_lineage") or {}
                    ),
                    "baseline_schema_version": str(
                        baseline_close.get("schema_version") or ""
                    ),
                    "candidate_lineage": dict(candidate_close.get("lineage") or {}),
                    "candidate_schema_version": str(
                        candidate_close.get("schema_version") or ""
                    ),
                    "candidate_runtime_version": str(
                        candidate_close.get("runtime_version") or ""
                    ),
                },
                "evaluation_time_utc": datetime.now(timezone.utc).isoformat(),
            }
            symbol = str(baseline_close.get("symbol") or "UNKNOWN")
            if self._writer.append(
                event=record, symbol=symbol,
                market_time_utc=baseline_exit_time,
            ):
                persisted_pairs[pid] = record
                created += 1
            else:
                errors.append({
                    "reason": "EVALUATION_PERSISTENCE_FAILED",
                    "identity": pid,
                })

        self._last_errors = errors
        return {
            "paired": len(persisted_pairs),
            "created": created,
            "integrity_violations": len(errors),
            "errors": list(errors),
        }

    def records(self) -> list[dict[str, Any]]:
        unique: dict[str, dict[str, Any]] = {}
        events, _ = _read_event_directory(self._writer.base_dir, "EVALUATION")
        for event in events:
            if (
                event.get("event_type") == "PAIRED_OUTCOME"
                and event.get("schema_version") == SCHEMA_VERSION
                and event.get("pair_id")
            ):
                unique.setdefault(str(event["pair_id"]), event)
        return list(unique.values())

    def integrity_errors(self) -> list[dict[str, str]]:
        return list(self._last_errors)


_EVALUATION: CandidateEvaluation | None = None


def get_candidate_evaluation() -> CandidateEvaluation:
    global _EVALUATION
    if _EVALUATION is None:
        _EVALUATION = CandidateEvaluation()
    return _EVALUATION


def reset_candidate_evaluation() -> None:
    global _EVALUATION
    _EVALUATION = None


__all__ = [
    "CandidateEvaluation",
    "SCHEMA_VERSION",
    "get_candidate_evaluation",
    "reset_candidate_evaluation",
]
