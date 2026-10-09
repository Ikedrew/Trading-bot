"""Run the three adversarial probes against Git HEAD and the repaired source.

This standalone verification script never changes the working tree.  Baseline
modules are loaded from immutable Git blobs into a temporary directory.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

from research_engine.control_plane.governed_counterfactual_evidence import (
    CounterfactualEvidenceStore,
)
from research_engine.v10.continuous.candidate_observation import (
    candidate_evidence_accounting, reconcile_candidate_observations,
)
from research_engine.v10.continuous.validation_queue import (
    FORWARD_VALIDATION, ValidationQueueStore, enqueue_forward_validation,
    process_validation_queue,
)
from tests.test_ar02_ar04_ar05_evidence_identity import (
    _initial_and_forward, _registered,
)
from tests.test_candidate_observation import _catalog
from tests.test_production_validation_executors import (
    _evidence, _enqueue, _registry,
)


def _baseline_module(repo: Path, relative: str, temporary: Path, name: str):
    source = subprocess.run(
        ["git", "show", "HEAD:" + relative], cwd=repo, check=True,
        capture_output=True, text=True, encoding="utf-8").stdout
    path = temporary / (name + ".py")
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="ar-evidence-baseline-") as directory:
        root = Path(directory)
        original_observation = _baseline_module(
            repo, "research_engine/v10/continuous/candidate_observation.py",
            root, "baseline_candidate_observation")
        original_validation = _baseline_module(
            repo, "research_engine/v10/continuous/production_validation.py",
            root, "baseline_production_validation")

        # AR-02: the original policy-only selector awards S1 rows to the S2
        # candidate; the repaired selector has no eligible S2 population.
        _, registrations = _registered(root / "ar02", [
            ("A", "S1", "S1", "E-S1"), ("B", "S2", "S2", "E-S2")])
        evidence = CounterfactualEvidenceStore(root / "ar02-evidence")
        evidence.register(_evidence("S1"))
        old = original_observation.candidate_evidence_accounting(
            registrations.load(), evidence)
        new = candidate_evidence_accounting(registrations.load(), evidence)
        assert old["B"]["sample_count"] == 4
        assert new["B"]["sample_count"] == 0
        print("AR-02 original=incorrectly_attributed_4 repaired=blocked_0")

        # AR-05: an old valid registration ID does not include either identity.
        registry, _ = _registered(root / "ar05", [("A", "S1", "S1", "E-S1")])
        candidate = registry.get_candidate("A")
        old_store = original_observation.CandidateObservationStore(
            root / "ar05" / "legacy.json")
        old_store.save({"A": original_observation.CandidateObservationRegistration(
            registration_id=original_observation.observation_registration_id(candidate),
            candidate_id="A", policy_id=candidate.policy_id,
            treatment_hash=candidate.treatment_hash, baseline_id=candidate.baseline_id,
            source_snapshot_id="WRONG", validation_plan_digest="WRONG")})
        original_observation.reconcile_candidate_observations(
            registry, old_store, _catalog(), snapshot_id="S1")
        assert old_store.load()["A"].source_snapshot_id == "WRONG"
        try:
            reconcile_candidate_observations(registry, old_store, _catalog(),
                                             snapshot_id="S1")
        except ValueError as exc:
            assert "CANDIDATE_OBSERVATION_IDENTITY_CONFLICT" in str(exc)
        else:
            raise AssertionError("Repaired registration accepted legacy collision")
        print("AR-05 original=retained_conflict repaired=identity_conflict")

        # AR-04: identical opportunities can have different row digests when
        # counterfactual outcomes change.  Original forward filtering misses it.
        trial = root / "ar04-old"
        registry, candidate, plan = _registry(trial / "registry")
        evidence = CounterfactualEvidenceStore(trial / "evidence")
        evidence.register(_evidence("S1"))
        queue = ValidationQueueStore(trial / "state" / "validation_queue.json")
        _enqueue(registry, candidate, plan, queue)
        initial_executor, forward_executor = original_validation.production_executors(
            state_root=trial / "state", evidence_directory=trial / "evidence")
        process_validation_queue(store=queue, registry=registry,
                                 validation_executor=initial_executor,
                                 forward_executor=forward_executor)
        assert registry.get_candidate(candidate.candidate_id).status == "VALIDATED"
        enqueue_forward_validation(candidate, plan.to_dict(), store=queue,
                                   snapshot_id="S1", cycle_id="C2",
                                   timestamp="2026-01-02T00:00:00Z")
        later = _evidence("S2", delta=1.2,
                          frontier_start="2026-01-02T00:00:00Z",
                          frontier_end="2026-01-03T00:00:00Z")
        evidence.register(later)
        process_validation_queue(store=queue, registry=registry,
                                 validation_executor=initial_executor,
                                 forward_executor=forward_executor)
        forward = next(item for item in queue.ordered()
                       if item.kind == FORWARD_VALIDATION)
        assert forward.output_validation_record["status"] == "FORWARD_VALIDATED"
        repaired = _initial_and_forward(root / "ar04-new", later)
        assert repaired.output_validation_record["reason"] == (
            "FORWARD_OBSERVATION_POPULATION_OVERLAP")
        print("AR-04 original=forward_validated_overlap repaired=overlap_blocked")


if __name__ == "__main__":
    main()
