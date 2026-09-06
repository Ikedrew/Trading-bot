"""
Stale/Parallel Architecture Guard — regression tests (Gap 9 final cleanup).

Prevents reintroduction of retired/parallel research architectures:

  1. retired modules deleted from the active tree (research_projection,
     v10 s3_publisher) stay retired;
  2. no active Research Engine module imports the retired V10 campaign/
     router/storage/old-bucket chain or the retired Lambda copies;
  3. no active old-bucket names in canonical research modules;
  4. no research-side decision_audit readers (production decision_id minting
     in core/ is explicitly out of scope and allowed);
  5. shadow_ev entry points refuse to run without --offline-replay;
  6. the retired `v10-engine` cockpit S3 publish stays retired;
  7. lifecycle surfaces never import a question bank;
  8. core research_anomaly / local preprocessing chain is not imported by
     the canonical research engine.

Fake-S3 only; no real AWS in tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RESEARCH_ENGINE = ROOT / "research_engine"

# Retired modules/dirs that must stay out of the active tree
RETIRED_PATHS = [
    RESEARCH_ENGINE / "v10" / "persistence" / "s3_publisher.py",
    ROOT / "research_projection",
]

# Retired V10 campaign/router/storage/old-bucket execution chain — must never
# be imported by the canonical lifecycle/experiment/dataset surfaces.
RETIRED_V10_EXECUTION_MODULES = (
    "research_engine.v10.campaigns",
    "research_engine.v10.operations",
    "research_engine.v10.research_intelligence.experiment_runner",
    "research_engine.v10.research_intelligence.question_registry",
    "research_engine.v10.persistence.s3_publisher",
    "research_engine.v10.research_universe",
    "core.research_ready_dataset",
    "core.validated_trade_dataset",
    "core.trades_clean",
    "core.research_anomaly",
)

# Canonical surfaces that must remain clean of retired imports
CANONICAL_SURFACE_GLOBS = [
    "research_engine/main.py",
    "research_engine/experiments/*.py",
    "research_engine/data_access/*.py",
    "research_engine/lifecycle/*.py",
    "research_engine/correlation/*.py",
    "research_engine/edge_attribution/*.py",
    "research_engine/edge_candidates/*.py",
    "research_engine/registry/inventory_guard.py",
    "research_engine/v10/universes/builder.py",
    "research_engine/v10/universes/shadow_outcome_universe.py",
    "research_engine/v10/universes/shadow_reality_universe.py",
]


def _canonical_sources() -> dict[str, str]:
    out: dict[str, str] = {}
    for pattern in CANONICAL_SURFACE_GLOBS:
        for path in ROOT.glob(pattern):
            if path.is_file():
                out[str(path.relative_to(ROOT)).replace("\\", "/")] = path.read_text(
                    encoding="utf-8", errors="replace")
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# RETIRED PATHS STAY RETIRED
# ═══════════════════════════════════════════════════════════════════════════════


class TestRetiredPathsStayRetired:
    def test_research_projection_is_gone(self):
        assert not (ROOT / "research_projection").exists()

    def test_v10_s3_publisher_is_gone(self):
        assert not (RESEARCH_ENGINE / "v10" / "persistence" / "s3_publisher.py").exists()

    def test_no_active_module_imports_research_projection(self):
        offenders = []
        for f in RESEARCH_ENGINE.rglob("*.py"):
            if "__pycache__" in f.parts:
                continue
            src = f.read_text(encoding="utf-8", errors="replace")
            if "research_projection" in src and "import" in src:
                offenders.append(str(f.relative_to(ROOT)))
        assert offenders == []

    def test_no_active_module_imports_the_retired_publisher(self):
        offenders = []
        for f in RESEARCH_ENGINE.rglob("*.py"):
            if "__pycache__" in f.parts:
                continue
            src = f.read_text(encoding="utf-8", errors="replace")
            if "s3_publisher" in src and "import" in src:
                offenders.append(str(f.relative_to(ROOT)))
        assert offenders == []


# ═══════════════════════════════════════════════════════════════════════════════
# RETIRED IMPORTS / OLD BUCKETS / DECISION_AUDIT
# ═══════════════════════════════════════════════════════════════════════════════


class TestRetiredImportsAndBuckets:
    def test_canonical_surfaces_do_not_import_retired_v10_chain(self):
        offenders = []
        for rel, src in _canonical_sources().items():
            for module in RETIRED_V10_EXECUTION_MODULES:
                if f"import {module}" in src or f"from {module}" in src:
                    offenders.append(f"{rel}: {module}")
        assert offenders == []

    def test_no_old_bucket_names_in_canonical_research_modules(self):
        """Code-level occurrences only; retirement documentation and the
        classified F (historical) retired-V10 chain are excluded — the chain
        itself is guard-verified unreachable from canonical surfaces."""
        retired_chain = ("v10/operations/", "v10/campaigns/",
                         "v10/research_intelligence/experiment_runner.py")
        offenders = []
        for f in RESEARCH_ENGINE.rglob("*.py"):
            if "__pycache__" in f.parts:
                continue
            rel = f.relative_to(ROOT).as_posix()
            if any(part in rel for part in retired_chain):
                continue
            src = f.read_text(encoding="utf-8", errors="replace")
            if "v10-engine" not in src:
                continue
            for line in src.splitlines():
                if "v10-engine" in line and not line.lstrip().startswith("#"):
                    offenders.append(f"{rel}: {line.strip()[:60]}")
        assert offenders == []

    def test_no_research_side_decision_audit_dependency(self):
        """No research-side import of decision_audit (removal notes in
        comments are explicitly allowed and documented)."""
        offenders = []
        for f in RESEARCH_ENGINE.rglob("*.py"):
            if "__pycache__" in f.parts:
                continue
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                if "decision_audit" in line and "import" in line:
                    if line.lstrip().startswith("#"):
                        continue
                    offenders.append(f"{f.relative_to(ROOT)}: {line.strip()[:60]}")
        assert offenders == []

    def test_lambda_copies_are_marked_retired_and_isolated(self):
        marker = ROOT / "lambda" / "RETIRED.md"
        assert marker.exists()
        assert "NOT the canonical Research Engine" in marker.read_text(encoding="utf-8")
        # canonical research_engine must not import from lambda/
        for f in RESEARCH_ENGINE.rglob("*.py"):
            if "__pycache__" in f.parts:
                continue
            src = f.read_text(encoding="utf-8", errors="replace")
            assert "from lambda" not in src and "import lambda" not in src


# ═══════════════════════════════════════════════════════════════════════════════
# SHADOW_EV OFFLINE GATE
# ═══════════════════════════════════════════════════════════════════════════════


class TestShadowEvOfflineGate:
    def test_entry_points_require_explicit_offline_replay(self):
        for name in ("run_shadow_ev.py", "run_walk_forward.py"):
            src = (RESEARCH_ENGINE / "shadow_ev" / name).read_text(encoding="utf-8")
            assert 'OFFLINE_REPLAY_FLAG = "--offline-replay"' in src
            assert "raise SystemExit(2)" in src

    def test_shadow_ev_refuses_without_offline_flag(self, monkeypatch, capsys):
        monkeypatch.chdir(Path.cwd())
        monkeypatch.setattr(sys, "argv", ["run_shadow_ev.py"])
        from research_engine.shadow_ev import run_shadow_ev

        with pytest.raises(SystemExit) as excinfo:
            run_shadow_ev.main()
        assert excinfo.value.code == 2

    def test_shadow_ev_runs_with_offline_flag(self, monkeypatch):
        from research_engine.shadow_ev import run_shadow_ev

        monkeypatch.setattr(sys, "argv", ["run_shadow_ev.py", "--offline-replay"])
        monkeypatch.setattr(run_shadow_ev, "load_decision_trace", lambda *a, **k: [])

        def _empty_result(traces):
            # minimal assessment-shaped stub; result.confidence drives the
            # INSUFFICIENT_DATA early-return path in main()
            from types import SimpleNamespace
            return SimpleNamespace(
                confidence="INSUFFICIENT_DATA", conclusion="no data (test)",
                models=[], decisions_with_outcome=0, to_dict=lambda: {},
            )

        monkeypatch.setattr(run_shadow_ev, "run_shadow_ev_replay", _empty_result)
        run_shadow_ev.main()  # must not raise SystemExit


# ═══════════════════════════════════════════════════════════════════════════════
# QUESTION INVENTORY STILL SINGLE-SOURCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestQuestionInventoryStillSingleSource:
    def test_lifecycle_never_imports_a_question_bank(self):
        for name in ("research_cycle_runner.py", "cycle_snapshot.py",
                     "finding_trigger.py", "orchestrator.py"):
            src = (RESEARCH_ENGINE / "lifecycle" / name).read_text(encoding="utf-8")
            assert "question_bank" not in src
            assert "legacy_question_bank" not in src

    def test_legacy_bank_still_pointed_at_canonical(self):
        from research_engine.v10.universes.legacy_question_bank import (
            CANONICAL_QUESTION_INVENTORY,
        )
        assert CANONICAL_QUESTION_INVENTORY == \
            "research_engine.registry.research_question_registry"
