"""GOVERNED OBSERVABILITY EMISSION CONTRACT (fail closed).

Stage 4 predeployment cleanup.  This module is the single runtime authority for
"which contract is a newly produced observation record allowed to claim", and it
exists to make one specific deployment failure IMPOSSIBLE:

    new producer code  +  old schema-generation metadata  ->  silent coexistence

WHY A RUNTIME MODULE AND NOT ONLY A REGISTRY
---------------------------------------------
The control-plane ``stage4_data_versioning`` registry already governs dataset
version, schema generation, producer version and evidence epoch, but a registry
only *declares*.  It cannot stop a running process from stamping generation 1 on
a record that actually carries generation-2 fields.  This module is the
enforcement point: it is called on the ACTUAL serialization path, immediately
before a record is handed to the writer, and it raises rather than downgrades.

THE CONTRACT
------------
For every newly emitted record:

  1. The record must carry a ``record_lineage`` envelope naming the dataset,
     dataset version, schema generation, producer version, evidence epoch and
     collection start.  A record with no lineage block predates the contract and
     is not a record this producer is allowed to emit.
  2. The claimed schema generation MUST be the current governed generation.  A
     record claiming an older generation is a drift, never a downgrade.
  3. The claimed producer version MUST be the current governed producer version.
  4. The claimed evidence epoch MUST be the epoch that the governed registry
     resolves for (dataset, dataset version, schema generation, producer
     version).  The epoch is stamped on the row AND independently resolvable,
     and the two can never disagree.
  5. Every generation-2 field required for that event type MUST be present.  A
     record that CLAIMS generation 2 while omitting a governed generation-2
     field fails closed; the contract permits no silent partial emission.
  6. The record's own ``schema_version`` (dataset identity) MUST equal the
     governed dataset version.

FAIL-CLOSED SEMANTICS
--------------------
Every violation raises ``ObservabilityContractError``.  There is no permissive
fallback, no default epoch, no "assume generation 1" and no degraded mode: if
the code and the contract disagree, the process must not emit the record.

This module performs no I/O, no S3 write, no historical mutation, no research
re-entry and never starts Q71+.
"""

from __future__ import annotations

from typing import Any, Mapping

# ═══════════════════════════════════════════════════════════════════════════
# IDENTITY
# ═══════════════════════════════════════════════════════════════════════════

LINEAGE_VERSION = "observation_record_lineage_v1"

#: Governance stamp / collection start shared with the control-plane policy.
COLLECTION_START = "2026-09-29"

DATASET_SHADOW_RUNTIME = "shadow_runtime"
DATASET_DECISION_TRACE = "decision_trace"


class ObservabilityContractError(RuntimeError):
    """A producer contract invariant was violated (fail closed)."""


# ═══════════════════════════════════════════════════════════════════════════
# GOVERNED CONTRACTS
# ═══════════════════════════════════════════════════════════════════════════

#: The four generation-2 observability blocks on ``shadow_runtime``.
SHADOW_RUNTIME_GEN2_FIELDS: tuple[str, ...] = (
    "decision_snapshot",
    "market_time_attestation",
    "experiment_arm",
    "lifecycle_m5_path",
)

#: Which generation-2 blocks each ``shadow_runtime`` event type must carry.
#: PLAN and PROGRESS are lifecycle-transition events and are governed to carry
#: none of the four blocks; OPEN freezes the decision-time evidence and CLOSE
#: freezes the realised path.  A block required here but missing is a FAIL.
SHADOW_RUNTIME_REQUIRED_BY_EVENT: dict[str, tuple[str, ...]] = {
    "PLAN": (),
    "OPEN": ("decision_snapshot", "market_time_attestation", "experiment_arm"),
    "PROGRESS": (),
    "CLOSE": ("lifecycle_m5_path", "market_time_attestation"),
}

#: The generation-2 field added to ``decision_trace``.
DECISION_TRACE_GEN2_FIELDS: tuple[str, ...] = ("predicted_success",)




#: The complete governed contract for every dataset this Stage 4 deployment
#: emits.  ``required_fields`` is the union across event types; the per-event
#: requirement is enforced by ``SHADOW_RUNTIME_REQUIRED_BY_EVENT``.
CONTRACTS: dict[str, dict[str, Any]] = {
    DATASET_SHADOW_RUNTIME: {
        "dataset": DATASET_SHADOW_RUNTIME,
        "dataset_version": "shadow_runtime_v1",
        "schema_generation": 2,
        "producer_version": "shadow_runtime_producer_v2",
        "evidence_epoch": "STAGE4-EPOCH-SHADOW-RUNTIME-G2",
        "collection_start": COLLECTION_START,
        "required_fields": SHADOW_RUNTIME_GEN2_FIELDS,
        "previous_schema_generation": 1,
        "previous_producer_version": "shadow_runtime_producer_v1",
    },
    DATASET_DECISION_TRACE: {
        "dataset": DATASET_DECISION_TRACE,
        "dataset_version": "decision_trace_v1",
        "schema_generation": 2,
        "producer_version": "decision_trace_producer_v2",
        "evidence_epoch": "STAGE4-EPOCH-DECISION-TRACE-G2",
        "collection_start": COLLECTION_START,
        "required_fields": DECISION_TRACE_GEN2_FIELDS,
        "previous_schema_generation": 1,
        "previous_producer_version": "decision_trace_producer_v1",
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# EVIDENCE EPOCH RESOLUTION (deterministic, unambiguous, immutable)
# ═══════════════════════════════════════════════════════════════════════════

#: The immutable epoch table.  Keyed on the FULL identity tuple so epoch
#: assignment can never be ambiguous: no two entries can collide on a subset.
#:
#: This is the registry-lookup half of TASK 5.  Epoch identity does not have to
#: be duplicated on every row, but the linkage must be deterministic and
#: unambiguous -- so the key is the complete (dataset, dataset_version,
#: schema_generation, producer_version) tuple, and a lookup that does not match
#: exactly one entry fails closed rather than guessing.
_EPOCH_TABLE: dict[tuple[str, str, int, str], dict[str, str]] = {
    ("shadow_runtime", "shadow_runtime_v1", 1, "shadow_runtime_producer_v1"): {
        "epoch_id": "STAGE4-EPOCH-SHADOW-RUNTIME-G1",
        "status": "RETIRED",
        "collection_start": COLLECTION_START,
    },
    ("shadow_runtime", "shadow_runtime_v1", 2, "shadow_runtime_producer_v2"): {
        "epoch_id": "STAGE4-EPOCH-SHADOW-RUNTIME-G2",
        "status": "COLLECTING",
        "collection_start": COLLECTION_START,
    },
    ("decision_trace", "decision_trace_v1", 1, "decision_trace_producer_v1"): {
        "epoch_id": "STAGE4-EPOCH-DECISION-TRACE-G1",
        "status": "RETIRED",
        "collection_start": COLLECTION_START,
    },
    ("decision_trace", "decision_trace_v1", 2, "decision_trace_producer_v2"): {
        "epoch_id": "STAGE4-EPOCH-DECISION-TRACE-G2",
        "status": "COLLECTING",
        "collection_start": COLLECTION_START,
    },
}
def epoch_identity_tuple(
        record_lineage: Mapping[str, Any]) -> tuple[str, str, int, str]:
    """The exact tuple an epoch is resolved by, read from a lineage envelope."""
    return (
        str(record_lineage.get("dataset", "")),
        str(record_lineage.get("dataset_version", "")),
        int(record_lineage.get("schema_generation", 0) or 0),
        str(record_lineage.get("producer_version", "")),
    )


def resolve_evidence_epoch(
        dataset: str,
        dataset_version: str,
        schema_generation: int,
        producer_version: str) -> dict[str, str]:
    """Resolve the ONE evidence epoch for a complete identity tuple.

    Fails closed when the tuple is unknown: an unresolvable epoch is never
    approximated by the nearest generation, because that is exactly the
    ambiguity this function exists to remove.
    """
    key = (str(dataset), str(dataset_version),
           int(schema_generation), str(producer_version))
    matches = [row for identity, row in _EPOCH_TABLE.items() if identity == key]
    if not matches:
        raise ObservabilityContractError(
            "UNRESOLVABLE_EVIDENCE_EPOCH:" + ":".join(map(str, key)))
    if len(matches) > 1:  # pragma: no cover - keys unique by construction
        raise ObservabilityContractError(
            "AMBIGUOUS_EVIDENCE_EPOCH:" + ":".join(map(str, key)))
    return dict(matches[0])


def validate_epoch_table() -> list[str]:
    """Prove the epoch table is unambiguous and internally consistent.

    Returns a list of problems; empty means the table is sound.  A governed
    contract's own epoch must resolve to a COLLECTING epoch whose collection
    start matches the contract.
    """
    problems: list[str] = []
    for dataset, contract in CONTRACTS.items():
        key = (dataset, str(contract["dataset_version"]),
               int(contract["schema_generation"]),
               str(contract["producer_version"]))
        matches = [row for identity, row in _EPOCH_TABLE.items() if identity == key]
        if len(matches) != 1:
            problems.append(
                f"CONTRACT_EPOCH_NOT_UNIQUE:{dataset}:{len(matches)}")
            continue
        row = matches[0]
        if row["epoch_id"] != contract["evidence_epoch"]:
            problems.append(
                f"CONTRACT_EPOCH_DISAGREEMENT:{dataset}:"
                f"{contract['evidence_epoch']}!={row['epoch_id']}")
        if row["status"] != "COLLECTING":
            problems.append(f"CONTRACT_EPOCH_NOT_COLLECTING:{dataset}")
        if row["collection_start"] != contract["collection_start"]:
            problems.append(f"CONTRACT_EPOCH_COLLECTION_START_MISMATCH:{dataset}")
    return problems


def epoch_table_as_dict() -> list[dict[str, Any]]:
    """The epoch table in a deterministic, reportable order."""
    return [
        dict({
            "dataset": identity[0],
            "dataset_version": identity[1],
            "schema_generation": identity[2],
            "producer_version": identity[3],
        }, **row)
        for identity, row in sorted(_EPOCH_TABLE.items())
    ]


def contract_for(dataset: str) -> dict[str, Any]:
    """The governed contract for one dataset, fail closed when ungoverned."""
    contract = CONTRACTS.get(str(dataset))
    if contract is None:
        raise ObservabilityContractError("UNGOVERNED_DATASET:" + str(dataset))
    return dict(contract)


# ═══════════════════════════════════════════════════════════════════════════
# STARTUP GUARD
# ═══════════════════════════════════════════════════════════════════════════

def assert_producer_contract(dataset: str) -> dict[str, Any]:
    """Startup guard: the emitting code must agree with the governed contract.

    Compares the contract against the constants the PRODUCER code actually
    imports, so a producer edited without a governed contract change fails at
    startup rather than at read time.  Fails closed on any mismatch.
    """
    contract = contract_for(dataset)

    if dataset == DATASET_SHADOW_RUNTIME:
        from core.shadow import observability as obs

        checks: tuple[tuple[str, Any, Any], ...] = (
            ("dataset_version", obs.SHADOW_RUNTIME_DATASET_VERSION,
             contract["dataset_version"]),
            ("schema_generation", obs.SHADOW_RUNTIME_SCHEMA_GENERATION,
             contract["schema_generation"]),
            ("producer_version", obs.SHADOW_RUNTIME_PRODUCER_VERSION,
             contract["producer_version"]),
            ("required_fields", tuple(obs.SHADOW_RUNTIME_GEN2_FIELDS),
             contract["required_fields"]),
        )
    elif dataset == DATASET_DECISION_TRACE:
        from core.pipeline import predicted_success as ps

        checks = (
            ("dataset_version", ps.DECISION_TRACE_DATASET_VERSION,
             contract["dataset_version"]),
            ("schema_generation", ps.DECISION_TRACE_SCHEMA_GENERATION,
             contract["schema_generation"]),
            ("producer_version", ps.DECISION_TRACE_PRODUCER_VERSION,
             contract["producer_version"]),
            ("required_fields", tuple(ps.DECISION_TRACE_GEN2_FIELDS),
             contract["required_fields"]),
        )
    else:  # pragma: no cover - guarded by contract_for
        raise ObservabilityContractError("UNGOVERNED_DATASET:" + str(dataset))

    for name, emitted, governed in checks:
        if emitted != governed:
            raise ObservabilityContractError(
                f"PRODUCER_CONTRACT_DRIFT:{dataset}:{name}:"
                f"emitted={emitted!r}!=governed={governed!r}")

    problems = validate_epoch_table()
    if problems:
        raise ObservabilityContractError(
            "EVIDENCE_EPOCH_TABLE_INVALID:" + ";".join(problems))
    return dict(contract)


# ═══════════════════════════════════════════════════════════════════════════
# RECORD LINEAGE (what a new record must claim)
# ═══════════════════════════════════════════════════════════════════════════

def build_record_lineage(dataset: str, *, event_type: str = "") -> dict[str, Any]:
    """The governed lineage envelope for one newly produced record.

    Built from the CONTRACT, never from producer-local constants, so the stamp
    and the enforcement can never be derived from two different truths.
    """
    contract = contract_for(dataset)
    resolved = resolve_evidence_epoch(
        contract["dataset"], contract["dataset_version"],
        contract["schema_generation"], contract["producer_version"])
    return {
        "lineage_version": LINEAGE_VERSION,
        "dataset": contract["dataset"],
        "dataset_version": contract["dataset_version"],
        "schema_generation": contract["schema_generation"],
        "producer_version": contract["producer_version"],
        "evidence_epoch": resolved["epoch_id"],
        "collection_start": contract["collection_start"],
        "event_type": str(event_type or ""),
    }


# ═══════════════════════════════════════════════════════════════════════════
# PER-RECORD FAIL-CLOSED GUARD
# ═══════════════════════════════════════════════════════════════════════════

def _required_fields_for(
        dataset: str,
        record: Mapping[str, Any],
        lineage: Mapping[str, Any]) -> tuple[str, ...]:
    """Which generation-2 fields this specific record must carry."""
    if dataset == DATASET_SHADOW_RUNTIME:
        event_type = str(
            lineage.get("event_type") or record.get("event_type") or "")
        required = SHADOW_RUNTIME_REQUIRED_BY_EVENT.get(event_type)
        if required is None:
            raise ObservabilityContractError(
                f"UNGOVERNED_EVENT_TYPE:{dataset}:{event_type!r}")
        return required
    return DECISION_TRACE_GEN2_FIELDS


def assert_emission_contract(
        record: Mapping[str, Any], *, dataset: str) -> None:
    """Validate one outgoing record against the governed contract.

    Raises ``ObservabilityContractError`` on any drift.  This is called on the
    real serialization path, not only in tests, which is what makes the
    new-code/old-metadata combination impossible rather than merely unlikely.
    """
    contract = contract_for(dataset)
    if not isinstance(record, Mapping):
        raise ObservabilityContractError(f"RECORD_NOT_A_MAPPING:{dataset}")

    lineage = record.get("record_lineage")
    if not isinstance(lineage, Mapping):
        # A generation-2 field with no lineage block is precisely "new code
        # coexisting with old generation metadata": fail closed, do not guess.
        present = [f for f in contract["required_fields"] if f in record]
        raise ObservabilityContractError(
            f"RECORD_LINEAGE_MISSING:{dataset}:"
            f"generation2_fields_present={','.join(present) or 'NONE'}")

    # 1. Dataset identity on the record itself must match the contract.
    declared = str(record.get("schema_version", ""))
    if declared != contract["dataset_version"]:
        raise ObservabilityContractError(
            f"DATASET_VERSION_MISMATCH:{dataset}:"
            f"{declared!r}!={contract['dataset_version']!r}")

    # 2. Lineage must name this dataset and its current dataset version.
    if str(lineage.get("dataset", "")) != contract["dataset"]:
        raise ObservabilityContractError(
            f"LINEAGE_DATASET_MISMATCH:{dataset}:{lineage.get('dataset')!r}")
    if str(lineage.get("dataset_version", "")) != contract["dataset_version"]:
        raise ObservabilityContractError(
            f"LINEAGE_DATASET_VERSION_MISMATCH:{dataset}:"
            f"{lineage.get('dataset_version')!r}")

    # 3. Generation must be the current governed generation.  An older claim is
    #    a DRIFT and is never downgraded into an accepted emission.
    try:
        claimed_generation = int(lineage.get("schema_generation", 0) or 0)
    except (TypeError, ValueError):
        raise ObservabilityContractError(
            f"LINEAGE_GENERATION_NOT_INTEGER:{dataset}") from None
    if claimed_generation != int(contract["schema_generation"]):
        raise ObservabilityContractError(
            f"GENERATION_PRODUCER_DRIFT:{dataset}:claimed={claimed_generation}"
            f"!=governed={contract['schema_generation']}")

    # 4. Producer version must be the current governed producer version.
    if str(lineage.get("producer_version", "")) != contract["producer_version"]:
        raise ObservabilityContractError(
            f"PRODUCER_VERSION_DRIFT:{dataset}:"
            f"{lineage.get('producer_version')!r}"
            f"!={contract['producer_version']!r}")

    # 5. Evidence epoch must be exactly the resolved epoch.
    resolved = resolve_evidence_epoch(*epoch_identity_tuple(lineage))
    claimed_epoch = str(lineage.get("evidence_epoch", ""))
    if claimed_epoch != resolved["epoch_id"]:
        raise ObservabilityContractError(
            f"EVIDENCE_EPOCH_MISMATCH:{dataset}:"
            f"{claimed_epoch!r}!={resolved['epoch_id']!r}")
    if claimed_epoch != contract["evidence_epoch"]:
        raise ObservabilityContractError(
            f"EVIDENCE_EPOCH_NOT_CURRENT:{dataset}:{claimed_epoch!r}")

    # 6. Collection start must match the governed epoch.
    if str(lineage.get("collection_start", "")) != resolved["collection_start"]:
        raise ObservabilityContractError(
            f"COLLECTION_START_MISMATCH:{dataset}:"
            f"{lineage.get('collection_start')!r}")

    # 7. Every generation-2 field required for this event type must be present.
    for field in _required_fields_for(dataset, record, lineage):
        if record.get(field) is None:
            raise ObservabilityContractError(f"GEN2_FIELD_MISSING:{dataset}:{field}")


def record_contract_summary(dataset: str) -> dict[str, Any]:
    """A reportable statement of what the runtime guarantees for one dataset."""
    contract = contract_for(dataset)
    resolved = resolve_evidence_epoch(
        contract["dataset"], contract["dataset_version"],
        contract["schema_generation"], contract["producer_version"])
    return {
        "dataset": contract["dataset"],
        "dataset_version": contract["dataset_version"],
        "schema_generation": contract["schema_generation"],
        "producer_version": contract["producer_version"],
        "evidence_epoch": resolved["epoch_id"],
        "evidence_epoch_status": resolved["status"],
        "collection_start": contract["collection_start"],
        "previous_schema_generation": contract["previous_schema_generation"],
        "previous_producer_version": contract["previous_producer_version"],
        "required_fields": list(contract["required_fields"]),
        "required_by_event": (
            {k: list(v) for k, v in SHADOW_RUNTIME_REQUIRED_BY_EVENT.items()}
            if dataset == DATASET_SHADOW_RUNTIME else None
        ),
        "lineage_version": LINEAGE_VERSION,
        "epoch_resolution": (
            "DETERMINISTIC_REGISTRY_LOOKUP_ON_FULL_IDENTITY_TUPLE"
        ),
    }


__all__ = [
    "COLLECTION_START", "CONTRACTS", "DATASET_DECISION_TRACE",
    "DATASET_SHADOW_RUNTIME", "DECISION_TRACE_GEN2_FIELDS", "LINEAGE_VERSION",
    "ObservabilityContractError", "SHADOW_RUNTIME_GEN2_FIELDS",
    "SHADOW_RUNTIME_REQUIRED_BY_EVENT", "assert_emission_contract",
    "assert_producer_contract", "build_record_lineage", "contract_for",
    "epoch_identity_tuple", "epoch_table_as_dict",
    "record_contract_summary", "resolve_evidence_epoch",
    "validate_epoch_table",
]
