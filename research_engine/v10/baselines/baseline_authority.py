"""
Baseline Authority — durable active-baseline identity (Wave 4C.1).

Extends the existing baseline machinery (SnapshotRegistry + data/baselines/)
with ONE durable pointer file answering, restart-safely:

    "Which BaselineSnapshot is currently active?"

Design (identity and provenance ONLY — this module never mutates trading or
production configuration):

    - Pointer persistence: data/baselines/active_baseline.json — inside the
      EXISTING baseline directory. No second registry/authority is created.
    - active_baseline_id ALWAYS references an actually persisted
      BaselineSnapshot (validated on read AND on activation).
    - previous_baseline_id preserves rollback PROVENANCE only (A → B keeps
      previous=A). Rollback itself is deliberately NOT implemented here.
    - Activation requires a non-empty actor + reason (explicit human/system
      action); bootstrap uses a distinguishable system actor/reason.
    - Malformed/corrupt pointer state FAILS CLOSED (raises BaselineStateError)
      instead of silently returning None (which would trigger a silent rebase).
    - Bootstrap is idempotent: repeated capture of unchanged state reuses the
      existing equivalent snapshot (deterministic identity_hash) and retains
      an existing valid active baseline; config drift is REPORTED, never
      silently rebased.
    - Identity hashing reuses the existing primitive
      core.research_events.compute_config_hash(). NOTE (Wave 4C.0, NOT
      expanded here): that primitive's material-parameter coverage is
      incomplete — documented limitation of the config identity contract.

Persistence semantics: pointer + snapshot writes are atomic within a volume
(temp file + os.replace). A hard process kill between temp-write and replace
leaves only a harmless `.tmp` residue; readers never observe partial JSON.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_engine.v10.baselines.snapshot_registry import SnapshotRegistry

logger = logging.getLogger(__name__)

# ─── Persistence defaults (module-attr injection pattern) ────────────────────
# Read at CALL time so tests can redirect persistence via monkeypatch on these
# module attributes — the same convention used across the persistence layer
# (e.g. candidate_evaluation_bridge._EVALUATIONS_DIR). Defaults unchanged.
_BASELINES_DIR = "data/baselines"
_ACTIVE_POINTER_FILE = "data/baselines/active_baseline.json"

_POINTER_SCHEMA_VERSION = "active_baseline_v1"

# Distinguishable system/bootstrap identity (an audit label, NOT permission
# infrastructure — no authentication is invented in this wave).
BOOTSTRAP_ACTOR = "system:baseline_bootstrap"
BOOTSTRAP_REASON = (
    "bootstrap: capture current production/research state as the canonical "
    "active baseline (no active baseline existed)"
)


class BaselineStateError(RuntimeError):
    """Active-baseline state is malformed/corrupt/unresolvable — fail closed."""


class BaselineMissingError(BaselineStateError):
    """Activation attempted against a snapshot_id with no persisted snapshot."""


@dataclass
class ActiveBaselineState:
    """Durable active-baseline pointer record (Wave 4C.1 contract)."""

    active_baseline_id: str
    previous_baseline_id: str = ""
    activated_at: str = ""
    actor: str = ""
    reason: str = ""

    def __post_init__(self):
        if not self.activated_at:
            self.activated_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": _POINTER_SCHEMA_VERSION,
            "active_baseline_id": self.active_baseline_id,
            "previous_baseline_id": self.previous_baseline_id,
            "activated_at": self.activated_at,
            "actor": self.actor,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ActiveBaselineState":
        if not isinstance(data, dict):
            raise ValueError("active-baseline pointer is not a JSON object")
        active = data.get("active_baseline_id")
        if not isinstance(active, str) or not active.strip():
            raise ValueError("missing/empty active_baseline_id")
        previous = data.get("previous_baseline_id", "")
        if not isinstance(previous, str):
            raise ValueError("previous_baseline_id must be a string")
        # Replay invariant: previous must NEVER equal active.
        if previous and previous == active:
            raise ValueError("previous_baseline_id == active_baseline_id (corrupt)")
        state = cls(
            active_baseline_id=active,
            previous_baseline_id=previous,
            activated_at=data.get("activated_at", ""),
            actor=data.get("actor", ""),
            reason=data.get("reason", ""),
        )
        for _field in ("activated_at", "actor", "reason"):
            if not isinstance(getattr(state, _field), str):
                raise ValueError(f"{_field} must be a string")
        return state


@dataclass
class BootstrapResult:
    """Outcome of ensure_active_baseline()."""

    action: str                 # "created" | "reused_equivalent" | "existing_active_retained"
    snapshot: Any               # BaselineSnapshot
    config_drift_detected: bool = False
    drift_detail: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# POINTER HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _pointer_path(pointer_file: str | Path | None = None) -> Path:
    """Resolve pointer path; module attrs read at call time (test injection)."""
    return Path(pointer_file or _ACTIVE_POINTER_FILE)


def _default_registry() -> SnapshotRegistry:
    return SnapshotRegistry(baselines_dir=_BASELINES_DIR)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """
    Atomic pointer write (temp file + os.replace, fsynced).

    Failure semantics: os.replace is atomic within a volume (Windows NTFS and
    POSIX), so a reader never observes partial JSON. A hard kill between
    temp-write and replace leaves only a `.tmp` residue; the previous pointer
    file remains fully valid.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, path)


def load_baseline_snapshot(
    snapshot_id: str,
    *,
    registry: SnapshotRegistry | None = None,
) -> Any:
    """Load a persisted BaselineSnapshot by ID (None if absent/corrupt)."""
    reg = registry or _default_registry()
    return reg.load(snapshot_id)

# ═══════════════════════════════════════════════════════════════════════════════
# CANDIDATE BASELINE VALIDATION (Wave 4C.2 — activation invariant)
# ═══════════════════════════════════════════════════════════════════════════════

def validate_candidate_baseline(
    candidate_baseline_id: str,
    candidate_config_hash: str,
    *,
    registry: SnapshotRegistry | None = None,
    pointer_file: str | Path | None = None,
) -> tuple[bool, str]:
    """
    Prove the Wave 4C.2 baseline-bound activation invariant for ONE candidate.

    Reuses the 4C.1 authority ONLY (this pointer file, this snapshot store,
    this config-hash primitive). No second registry, no second identity
    algorithm, no "current baseline" concept.

    Required proofs, in evaluation order (FAIL CLOSED on the first failure):

        1. candidate has a real baseline_id          → missing_baseline_id
        2. active-baseline state is readable         → baseline_state_error
        3. an active baseline exists                 → no_active_baseline
        4. candidate baseline_id == active id        → stale_baseline
        5. referenced snapshot exists/loadable       → missing_snapshot
        6. candidate baseline_config_hash exists     → missing_baseline_provenance
        7. candidate hash == snapshot config_hash    → config_provenance_mismatch
        8. snapshot config_hash == current config    → stale_config
           (an empty/unavailable current config hash is also fail-closed:
            the match cannot be PROVEN)

    Returns (ok, reason). `reason` is a deterministic, auditable token
    (optionally with detail after ": ") suitable for persistence in skip
    records. Never raises for validation failures; BaselineStateError from
    the authority is converted to the fail-closed `baseline_state_error`
    reason. Callers must NOT activate, rebase, or rewrite the candidate on
    (False, ...).
    """
    # 1. Real baseline_id
    if not isinstance(candidate_baseline_id, str) or not candidate_baseline_id.strip():
        return False, "missing_baseline_id"

    # 2/3/4. Durable active-baseline state (reuse get_active fail-closed)
    try:
        active = get_active(registry=registry, pointer_file=pointer_file)
    except BaselineStateError as e:
        return False, f"baseline_state_error: {str(e)[:120]}"
    if active is None:
        return False, "no_active_baseline"
    if candidate_baseline_id != active.active_baseline_id:
        return False, (
            f"stale_baseline: candidate baseline '{candidate_baseline_id}' "
            f"!= active baseline '{active.active_baseline_id}'"
        )

    # 5. Referenced snapshot must exist and be loadable (corrupt JSON also
    #    fails this check — a corrupt snapshot is not a proven snapshot).
    reg = registry or _default_registry()
    snapshot = reg.load(candidate_baseline_id)
    if snapshot is None:
        return False, (
            f"missing_snapshot: active baseline '{candidate_baseline_id}' "
            f"cannot be loaded from the baseline store"
        )

    # 6. Candidate config provenance must exist
    if not isinstance(candidate_config_hash, str) or not candidate_config_hash.strip():
        return False, "missing_baseline_provenance"

    # 7. Provenance must match the referenced snapshot's identity
    if candidate_config_hash != snapshot.config_hash:
        return False, (
            f"config_provenance_mismatch: candidate provenance "
            f"'{candidate_config_hash}' != snapshot config_hash "
            f"'{snapshot.config_hash}'"
        )

    # 8. Snapshot identity must match the CURRENT production config, using the
    #    SAME primitive the 4C.1 snapshot builder used at capture time.
    try:
        from core.research_events import compute_config_hash
        current_hash = compute_config_hash()
    except Exception as e:  # noqa: BLE001 — fail closed on any identity failure
        return False, f"stale_config: current config identity unavailable ({str(e)[:80]})"
    if current_hash in ("", "UNKNOWN"):
        return False, "stale_config: current config identity unavailable"
    if snapshot.config_hash != current_hash:
        return False, (
            f"stale_config: baseline config_hash '{snapshot.config_hash}' "
            f"!= current config hash '{current_hash}'"
        )

    return True, "baseline_valid"



# ═══════════════════════════════════════════════════════════════════════════════
# ACTIVE BASELINE POINTER — READ / EXPLICIT ACTIVATION
# ═══════════════════════════════════════════════════════════════════════════════

def get_active(
    *,
    registry: SnapshotRegistry | None = None,
    pointer_file: str | Path | None = None,
) -> ActiveBaselineState | None:
    """
    Read the durable active-baseline pointer.

    Returns None when no pointer file exists yet (no active baseline).
    Raises BaselineStateError (FAIL CLOSED) when the pointer exists but is
    malformed, or when it references a missing/corrupt snapshot — the pointer
    must always refer to an actually persisted BaselineSnapshot, and callers
    must never silently rebase onto a different baseline.
    """
    path = _pointer_path(pointer_file)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        state = ActiveBaselineState.from_dict(data)
    except (json.JSONDecodeError, ValueError, OSError) as e:
        raise BaselineStateError(
            f"malformed active-baseline pointer at {path}: {e}"
        ) from e

    reg = registry or _default_registry()
    if reg.load(state.active_baseline_id) is None:
        raise BaselineStateError(
            f"active baseline '{state.active_baseline_id}' references a "
            f"missing/corrupt snapshot in the baseline store (fail closed)"
        )
    return state


def set_active(
    snapshot_id: str,
    *,
    actor: str,
    reason: str,
    registry: SnapshotRegistry | None = None,
    pointer_file: str | Path | None = None,
) -> ActiveBaselineState:
    """
    Explicitly activate a persisted snapshot (audit-annotated).

    Contract:
        - actor/reason must be non-empty (an explicit action is required; no
          permission infrastructure is invented).
        - snapshot_id MUST exist as a persisted BaselineSnapshot, otherwise
          BaselineMissingError is raised and the pointer is left UNCHANGED.
        - A → B durably preserves previous=A.
        - Idempotent replay (activate B while B is already active) preserves
          the ORIGINAL previous_baseline_id — replay must never corrupt the
          pointer into previous == active.
    """
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("actor is required for an explicit active-baseline change")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason is required for an explicit active-baseline change")

    reg = registry or _default_registry()
    if reg.load(snapshot_id) is None:
        raise BaselineMissingError(
            f"cannot activate unknown snapshot '{snapshot_id}' — active "
            f"pointer left unchanged (fail closed)"
        )

    current = get_active(registry=reg, pointer_file=pointer_file)

    # Idempotent replay: re-activating the already-active baseline must NOT
    # rewrite previous_baseline_id (previous stays the ORIGINAL previous).
    if current is not None and current.active_baseline_id == snapshot_id:
        logger.info(
            "[BASELINE_AUTHORITY] Idempotent replay: %s already active "
            "(previous=%s preserved)", snapshot_id, current.previous_baseline_id or "<none>",
        )
        return current

    previous = current.active_baseline_id if current is not None else ""
    state = ActiveBaselineState(
        active_baseline_id=snapshot_id,
        previous_baseline_id=previous,
        actor=actor.strip(),
        reason=reason.strip(),
    )
    if state.previous_baseline_id == state.active_baseline_id:
        raise BaselineStateError("activation would corrupt previous == active")
    _atomic_write_json(_pointer_path(pointer_file), state.to_dict())
    logger.info(
        "[BASELINE_AUTHORITY] Active baseline: %s (previous=%s, actor=%s, reason=%s)",
        state.active_baseline_id, previous or "<none>", state.actor, state.reason[:80],
    )
    return state


# ═══════════════════════════════════════════════════════════════════════════════
# SAFE BOOTSTRAP + FAIL-CLOSED CANDIDATE RESOLUTION
# ═══════════════════════════════════════════════════════════════════════════════

def ensure_active_baseline(
    *,
    registry: SnapshotRegistry | None = None,
    builder: Any | None = None,
    pointer_file: str | Path | None = None,
    actor: str = BOOTSTRAP_ACTOR,
    reason: str = BOOTSTRAP_REASON,
) -> BootstrapResult:
    """
    Safe, idempotent bootstrap: no active baseline → capture current state →
    persist canonical snapshot → mark it active.

    Properties (Wave 4C.1):
        - observation/capture ONLY; never writes production configuration.
        - an existing VALID active baseline is reused, never silently rebased;
          configuration drift vs the active baseline is REPORTED (returned in
          BootstrapResult and logged), never acted on.
        - repeated bootstrap over unchanged state reuses the already-persisted
          equivalent snapshot (deterministic identity_hash) — no duplicate
          equivalent-snapshot explosion.
        - corrupt/missing-target pointer state fails closed (raises via
          get_active()).
    """
    reg = registry or _default_registry()
    current = get_active(registry=reg, pointer_file=pointer_file)

    # ── Existing valid active baseline: retain + report drift ────────────────
    if current is not None:
        snapshot = reg.load(current.active_baseline_id)
        drift = False
        detail = ""
        try:
            from core.research_events import compute_config_hash
            current_hash = compute_config_hash()
            if (
                snapshot.config_hash
                and current_hash not in ("", "UNKNOWN")
                and current_hash != snapshot.config_hash
            ):
                drift = True
                detail = (
                    f"active baseline '{snapshot.snapshot_id}' config_hash="
                    f"'{snapshot.config_hash}' != current config hash "
                    f"'{current_hash}' — reported, NOT rebased"
                )
                logger.warning("[BASELINE_AUTHORITY] Config drift: %s", detail)
        except ImportError:
            pass  # config identity primitive unavailable → cannot compare
        return BootstrapResult(
            action="existing_active_retained",
            snapshot=snapshot,
            config_drift_detected=drift,
            drift_detail=detail,
        )

    # ── No active baseline: capture current state (READ-ONLY capture) ────────
    if builder is None:
        from research_engine.v10.baselines.snapshot_builder import SnapshotBuilder
        builder = SnapshotBuilder()
    try:
        snapshot = builder.build()
    except Exception as e:
        raise BaselineStateError(f"bootstrap snapshot capture failed: {e}") from e

    # Reuse an already-persisted equivalent snapshot (same deterministic
    # identity_hash) so repeated bootstraps never duplicate baselines.
    reused = None
    if snapshot.identity_hash:
        for sid in reg.list_snapshots():
            other = reg.load(sid)
            if other is not None and other.identity_hash == snapshot.identity_hash:
                reused = other
                break
    if reused is not None:
        snapshot = reused
        action = "reused_equivalent"
    else:
        reg.save(snapshot)
        action = "created"

    set_active(
        snapshot.snapshot_id,
        actor=actor,
        reason=reason,
        registry=reg,
        pointer_file=pointer_file,
    )
    return BootstrapResult(action=action, snapshot=snapshot)


def resolve_candidate_baseline() -> str:
    """
    Canonical active-baseline ID for candidate creation (FAIL CLOSED).

    Uses the canonical bootstrap API (ensure_active_baseline) — snapshot
    building is never duplicated inside the orchestrator. Raises
    BaselineStateError when trustworthy baseline identity cannot be
    established; callers must NOT silently fall back to placeholders
    (e.g. the retired "current_v10").
    """
    result = ensure_active_baseline()
    if result.config_drift_detected:
        # Drift is REPORTED, not acted on here: candidate creation still binds
        # to the EXISTING active baseline; evaluation-time staleness gates
        # enforce config freshness before READY_FOR_REVIEW promotion.
        logger.warning(
            "[BASELINE_AUTHORITY] Candidate baseline resolution: %s",
            result.drift_detail,
        )
    return result.snapshot.snapshot_id


def research_epoch(
    *,
    registry: SnapshotRegistry | None = None,
    pointer_file: str | Path | None = None,
) -> str:
    """Canonical research/optimisation epoch identity (Wave 5.4).

    The epoch IS the active production baseline identity from the existing
    4C.1 authority — no second identity system, no wall clock, no counter.
    New optimisation cycles (finding re-detection, candidate creation) bind
    to this value; when the active baseline advances N → N+1 the epoch
    advances with it, and when no active baseline exists yet all pre-
    bootstrap triggers share the empty-string epoch (historical behaviour).
    """
    state = get_active(registry=registry, pointer_file=pointer_file)
    return state.active_baseline_id if state is not None else ""
