"""
Research Cycle Runner — Scheduled/continuous research execution.

A single idempotent cycle that:
    1. Loads current research population
    2. Scans for anomalous findings
    3. Generates eligible triggers
    4. Optionally investigates them autonomously
    5. Records everything in the audit trail
    6. Respects budget limits and deduplication

Can be invoked:
    - Periodically from within the live scanner loop (post-cycle hook)
    - As a standalone one-shot script
    - Via AWS Lambda (event-driven)
    - From a scheduled task

Modes:
    DETECT_ONLY (default): Detect anomalies, create triggers, stop.
    DETECT_AND_INVESTIGATE: Detect + run full governed investigation.

Idempotency:
    - Same dataset fingerprint + same finding → no duplicate investigation
    - Uses FindingTriggerEngine's built-in deduplication
    - Persists cycle state to disk for restart recovery

Concurrency:
    - File-based lock prevents simultaneous research cycles
    - Uses same atomic-lock pattern as instance_lock.py

This module NEVER modifies production V10 or trading behaviour.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_engine.lifecycle.finding_trigger import (
    EligibilityConfig,
    ExecutionMode,
    FindingTriggerEngine,
    TriggerStatus,
)


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ResearchCycleConfig:
    """Configuration for the research cycle runner."""
    mode: ExecutionMode = ExecutionMode.DETECT_ONLY
    max_investigations_per_cycle: int = 2
    max_active_investigations: int = 5
    min_cycle_interval_seconds: float = 3600.0  # Minimum 1 hour between cycles
    eligibility: EligibilityConfig = field(default_factory=EligibilityConfig)


# ═══════════════════════════════════════════════════════════════════════════════
# CYCLE STATE (persistence)
# ═══════════════════════════════════════════════════════════════════════════════

_STATE_DIR = Path("logs/research_lifecycle")
_STATE_FILE = _STATE_DIR / "cycle_state.json"
_LOCK_FILE = _STATE_DIR / "research_cycle.lock"


@dataclass
class CycleState:
    """Persisted state of the research cycle runner."""
    last_cycle_id: str = ""
    last_cycle_timestamp: str = ""
    last_dataset_fingerprint: str = ""
    total_cycles: int = 0
    total_investigations: int = 0
    last_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__

    @classmethod
    def load(cls) -> "CycleState":
        if _STATE_FILE.exists():
            try:
                data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
                return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            except Exception:
                pass
        return cls()

    def save(self) -> None:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(_STATE_FILE)


# ═══════════════════════════════════════════════════════════════════════════════
# CONCURRENCY LOCK
# ═══════════════════════════════════════════════════════════════════════════════

def _acquire_research_lock() -> bool:
    """Acquire file lock for research cycle (prevents concurrent execution)."""
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(_LOCK_FILE), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            data = json.dumps({"pid": os.getpid(), "timestamp": datetime.now(timezone.utc).isoformat()})
            os.write(fd, data.encode("utf-8"))
        finally:
            os.close(fd)
        return True
    except FileExistsError:
        # Check if stale (PID dead)
        try:
            lock_data = json.loads(_LOCK_FILE.read_text(encoding="utf-8"))
            pid = lock_data.get("pid", 0)
            if pid and not _is_pid_alive(pid):
                _LOCK_FILE.unlink(missing_ok=True)
                return _acquire_research_lock()  # Retry after removing stale lock
        except Exception:
            pass
        return False


def _release_research_lock() -> None:
    """Release research cycle lock."""
    try:
        if _LOCK_FILE.exists():
            data = json.loads(_LOCK_FILE.read_text(encoding="utf-8"))
            if data.get("pid") == os.getpid():
                _LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        _LOCK_FILE.unlink(missing_ok=True)


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except (OSError, PermissionError):
        return False
    except Exception:
        return True


# ═══════════════════════════════════════════════════════════════════════════════
# CYCLE RESULT
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class CycleResult:
    """Result of one research cycle execution."""
    cycle_id: str = ""
    timestamp: str = ""
    status: str = ""                    # "complete" | "failed" | "skipped" | "locked"
    findings_scanned: int = 0
    triggers_detected: int = 0
    triggers_eligible: int = 0
    triggers_dismissed: int = 0
    investigations_started: int = 0
    investigations_completed: int = 0
    investigations_failed: int = 0
    errors: list[str] = field(default_factory=list)
    dataset_fingerprint: str = ""
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__


# ═══════════════════════════════════════════════════════════════════════════════
# RESEARCH CYCLE RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

class ResearchCycleRunner:
    """
    Executes one complete research cycle: scan → detect → (investigate) → report.
    
    Idempotent: same data produces same triggers, no duplicate investigations.
    Safe: concurrent execution prevented by file lock.
    Observable: audit trail for every action.
    Governed: never promotes research to production.
    """

    def __init__(self, config: ResearchCycleConfig | None = None):
        self._config = config or ResearchCycleConfig()
        self._state = CycleState.load()

    def run_cycle(self) -> CycleResult:
        """
        Execute one research cycle.
        
        Returns CycleResult describing what happened.
        Never raises — all errors are captured in the result.
        """
        cycle_id = f"RC-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4]}"
        result = CycleResult(
            cycle_id=cycle_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        start_time = time.time()

        # ─── CONCURRENCY CHECK ────────────────────────────────────────
        if not _acquire_research_lock():
            result.status = "locked"
            result.errors.append("Another research cycle is running")
            return result

        try:
            # ─── COOLDOWN CHECK ───────────────────────────────────────
            if self._state.last_cycle_timestamp:
                try:
                    from datetime import datetime as _dt
                    last_ts = _dt.fromisoformat(self._state.last_cycle_timestamp)
                    elapsed = (datetime.now(timezone.utc) - last_ts).total_seconds()
                    if elapsed < self._config.min_cycle_interval_seconds:
                        result.status = "skipped"
                        result.errors.append(
                            f"Cooldown: {elapsed:.0f}s elapsed, need {self._config.min_cycle_interval_seconds:.0f}s")
                        return result
                except Exception:
                    pass

            # ─── SCAN RESEARCH POPULATION ─────────────────────────────
            self._audit("RESEARCH_CYCLE_STARTED", cycle_id)
            population_stats = self._scan_population()
            result.findings_scanned = population_stats.get("total_patterns", 0)
            result.dataset_fingerprint = population_stats.get("fingerprint", "")

            # ─── DETECT TRIGGERS ──────────────────────────────────────
            engine = FindingTriggerEngine(
                mode=self._config.mode,
                config=self._config.eligibility,
            )

            triggers = self._detect_findings(engine, population_stats.get("patterns", {}))
            result.triggers_detected = len(triggers)
            result.triggers_eligible = sum(1 for t in triggers if t.status == TriggerStatus.ELIGIBLE)
            result.triggers_dismissed = sum(1 for t in triggers
                                            if t.status in (TriggerStatus.DISMISSED, TriggerStatus.BLOCKED))

            # ─── INVESTIGATE (if DETECT_AND_INVESTIGATE) ──────────────
            if self._config.mode == ExecutionMode.DETECT_AND_INVESTIGATE:
                eligible = [t for t in triggers if t.status == TriggerStatus.ELIGIBLE]
                investigated = self._investigate_eligible(
                    engine, eligible[:self._config.max_investigations_per_cycle])
                result.investigations_started = len(investigated)
                result.investigations_completed = sum(1 for i in investigated if i.get("status") == "complete")
                result.investigations_failed = sum(1 for i in investigated if i.get("status") == "failed")

            # ─── CANDIDATE ACTIVATION GATE ────────────────────────────
            # Activate eligible PROPOSED candidates → SHADOW_TESTING
            # (observation-only — never modifies production)
            try:
                from research_engine.lifecycle.candidate_activation_gate import activate_eligible_candidates
                activation = activate_eligible_candidates()
                if activation.candidates_activated > 0:
                    self._audit("CANDIDATES_ACTIVATED", cycle_id, activation.to_dict())
            except Exception:
                pass  # Activation gate must never block research cycle

            # ─── CANDIDATE AUTO-EVALUATION ────────────────────────────
            # Evaluate SHADOW_TESTING candidates with sufficient paired evidence
            # (observation-only — never modifies production)
            try:
                from research_engine.lifecycle.candidate_auto_evaluator import auto_evaluate_candidates
                auto_eval = auto_evaluate_candidates()
                if auto_eval.candidates_evaluated > 0:
                    self._audit("CANDIDATES_AUTO_EVALUATED", cycle_id, auto_eval.to_dict())
            except Exception:
                pass  # Auto-evaluation must never block research cycle

            # ─── UPDATE STATE ─────────────────────────────────────────
            result.status = "complete"
            result.duration_seconds = time.time() - start_time

            self._state.last_cycle_id = cycle_id
            self._state.last_cycle_timestamp = datetime.now(timezone.utc).isoformat()
            self._state.last_dataset_fingerprint = result.dataset_fingerprint
            self._state.total_cycles += 1
            self._state.total_investigations += result.investigations_completed
            self._state.last_error = ""
            self._state.save()

            self._audit("RESEARCH_CYCLE_COMPLETED", cycle_id,
                        {"triggers": result.triggers_detected, "eligible": result.triggers_eligible,
                         "investigated": result.investigations_started})

        except Exception as e:
            result.status = "failed"
            result.errors.append(str(e)[:200])
            result.duration_seconds = time.time() - start_time
            self._state.last_error = str(e)[:200]
            self._state.save()
            self._audit("RESEARCH_CYCLE_FAILED", cycle_id, {"error": str(e)[:200]})

        finally:
            _release_research_lock()

        return result

    # ─── INTERNAL: SCAN POPULATION ────────────────────────────────────

    def _scan_population(self) -> dict[str, Any]:
        """Load and summarise the current research population by pattern."""
        import statistics
        try:
            from research_engine.lifecycle.experiment_templates import _load_shadow_population
            from research_engine.lifecycle.dataset_fingerprint import compute_content_hash

            population = _load_shadow_population()
            if not population:
                return {"total_patterns": 0, "patterns": {}, "fingerprint": ""}

            # Group by pattern
            from collections import defaultdict
            by_pattern: dict[str, list] = defaultdict(list)
            for p in population:
                pat = p.get("pattern", "")
                if pat:
                    by_pattern[pat].append(p)

            # Compute per-pattern statistics (from existing shadow outcomes)
            # We use the population's own data — NOT re-simulating
            pattern_stats = {}
            for pat, records in by_pattern.items():
                if len(records) < 10:
                    continue
                # These records don't have r_multiple directly — they're input records
                # Pattern stats would come from the shadow outcomes, which we load separately
                pattern_stats[pat] = {"n": len(records)}

            # Fingerprint for idempotency
            fingerprint = compute_content_hash(population[:100])  # Sample for speed

            return {
                "total_patterns": len(pattern_stats),
                "patterns": pattern_stats,
                "fingerprint": fingerprint[:16],
            }
        except Exception:
            return {"total_patterns": 0, "patterns": {}, "fingerprint": ""}

    # ─── INTERNAL: DETECT FINDINGS ────────────────────────────────────

    def _detect_findings(self, engine: FindingTriggerEngine,
                         pattern_stats: dict) -> list:
        """Detect anomalous patterns from population statistics."""
        triggers = []

        # Load shadow outcomes for pattern-level R statistics
        try:
            from research_engine.lifecycle.experiment_templates import _load_shadow_population
            population = _load_shadow_population()

            # We need R-multiples from the shadow outcome universe
            from research_engine.v10.universes.shadow_outcome_universe import ShadowOutcomeUniverseBuilder
            from research_engine.v10.universes.models import Population
            builder = ShadowOutcomeUniverseBuilder()
            builder.build()
            shadows_all = builder.get_population(Population.ALL_SHADOW_OUTCOMES)
            # Phase 1I-C: PRIMARY_V10_SHADOW population retired with V10_PRIMARY.
            # Pattern-level R statistics now use the canonical Horizon Shadow
            # lineage (HORIZON_ALTERNATIVE records carry the same opportunity
            # pattern/score context at decision time).
            shadows = [s for s in shadows_all if s.get("shadow_type") == "HORIZON_ALTERNATIVE"]

            # Filter to real (has correlation_id)
            real_shadows = [s for s in shadows if s.get("correlation_id")]

            # Per-pattern R statistics
            import statistics
            from collections import defaultdict
            by_pattern: dict[str, list[float]] = defaultdict(list)
            for s in real_shadows:
                pat = s.get("pattern", "")
                r = s.get("r_multiple")
                if pat and r is not None:
                    by_pattern[pat].append(r)

            for pat, r_vals in by_pattern.items():
                if len(r_vals) < self._config.eligibility.min_sample_size:
                    continue
                mean_r = statistics.mean(r_vals)
                wr = sum(1 for r in r_vals if r > 0) / len(r_vals)

                trigger = engine.detect_from_pattern_performance(
                    pattern=pat, mean_r=mean_r, win_rate=wr, sample_size=len(r_vals),
                    source="research_cycle_runner",
                )
                if trigger:
                    triggers.append(trigger)

            # ─── NEW DETECTORS (6 categories) ─────────────────────────
            triggers.extend(engine.detect_direction_asymmetry(real_shadows, source="research_cycle_runner"))
            triggers.extend(engine.detect_regime_anomaly(real_shadows, source="research_cycle_runner"))
            triggers.extend(engine.detect_score_monotonicity(real_shadows, source="research_cycle_runner"))
            triggers.extend(engine.detect_temporal_instability(real_shadows, source="research_cycle_runner"))
            triggers.extend(engine.detect_geometry_anomaly(real_shadows, source="research_cycle_runner"))
            triggers.extend(engine.detect_symbol_anomaly(real_shadows, source="research_cycle_runner"))

            # ─── RED-AREA DETECTORS (coverage completion) ─────────
            triggers.extend(engine.detect_exit_inefficiency(
                builder.get_population(Population.ALL_SHADOW_OUTCOMES),
                source="research_cycle_runner"))

            # ─── SURFACE DETECTORS (system-wide coverage) ─────────
            try:
                from research_engine.lifecycle.surface_detectors import run_surface_detectors
                triggers.extend(run_surface_detectors(
                    engine=engine,
                    real_shadows=real_shadows,
                    all_shadows=builder.get_population(Population.ALL_SHADOW_OUTCOMES),
                    source="research_cycle_runner",
                ))
            except Exception:
                pass  # Surface detectors must never block the research cycle

            # ─── EVIDENCE LAYER (system-wide evidence records) ────────
            try:
                from research_engine.lifecycle.evidence_layer import collect_evidence
                collect_evidence(
                    real_shadows=real_shadows,
                    all_shadows=builder.get_population(Population.ALL_SHADOW_OUTCOMES),
                    triggers_generated=len(triggers),
                    cycle_id=f"EV-{self._state.total_cycles + 1}",
                )
            except Exception:
                pass  # Evidence layer must never block the research cycle

        except Exception:
            pass

        return triggers

    # ─── INTERNAL: INVESTIGATE ────────────────────────────────────────

    def _investigate_eligible(self, engine: FindingTriggerEngine,
                              eligible: list) -> list[dict]:
        """Run governed investigations for eligible triggers using category-driven contracts."""
        from research_engine.lifecycle.orchestrator import ResearchOrchestrator
        from research_engine.lifecycle.investigation_contracts import (
            get_contract, build_experiment_from_trigger,
        )

        results = []
        orch = ResearchOrchestrator()

        for trigger in eligible:
            try:
                # Check contract support
                contract = get_contract(trigger.category)
                if not contract.supported:
                    results.append({"trigger_id": trigger.trigger_id, "status": "blocked",
                                    "error": contract.unsupported_reason})
                    continue

                # Create hypothesis from trigger
                h = orch.detect_and_register(
                    title=trigger.title,
                    description=trigger.observation,
                    claim=trigger.suggested_claim,
                    null_hypothesis=trigger.suggested_null,
                    category=trigger.suggested_hypothesis_category,
                    source=f"research_cycle:{trigger.trigger_id}",
                    source_finding_id=trigger.finding_id,
                    multiple_testing_count=max(1, len(eligible)),
                )
                engine.mark_registered(trigger.trigger_id, h.hypothesis_id)

                # Build experiment definition from contract (not hard-coded)
                defn, err = build_experiment_from_trigger(
                    trigger,
                    hypothesis_id=h.hypothesis_id,
                    min_sample_size=self._config.eligibility.min_sample_size,
                )
                if defn is None:
                    results.append({"trigger_id": trigger.trigger_id, "status": "failed",
                                    "error": err})
                    continue

                engine.mark_investigating(trigger.trigger_id)

                # Run full governed investigation
                inv = orch.investigate(h, contract.experiment_type, defn)

                engine.mark_completed(trigger.trigger_id)
                results.append({"trigger_id": trigger.trigger_id, "status": inv.status,
                                "conclusion": inv.conclusion})

            except Exception as e:
                results.append({"trigger_id": trigger.trigger_id, "status": "failed",
                                "error": str(e)[:100]})

        return results

    # ─── AUDIT ────────────────────────────────────────────────────────

    def _audit(self, event: str, cycle_id: str, data: dict | None = None) -> None:
        try:
            _STATE_DIR.mkdir(parents=True, exist_ok=True)
            audit_path = _STATE_DIR / "audit_log.jsonl"
            entry = {"timestamp": datetime.now(timezone.utc).isoformat(),
                     "event": event, "cycle_id": cycle_id, **(data or {})}
            fd = os.open(str(audit_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            try:
                os.write(fd, (json.dumps(entry, separators=(",", ":")) + "\n").encode("utf-8"))
            finally:
                os.close(fd)
        except Exception:
            pass

    # ─── SUMMARY FOR COMMAND CENTER ───────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        """Current runner status for Command Center."""
        return {
            "last_cycle": self._state.last_cycle_timestamp,
            "total_cycles": self._state.total_cycles,
            "total_investigations": self._state.total_investigations,
            "last_fingerprint": self._state.last_dataset_fingerprint,
            "last_error": self._state.last_error,
            "mode": self._config.mode.value,
        }
