"""
Static architecture guard — the Research Engine reads source data from S3 ONLY.

After the S3 migration the permanent contract is:

    Research Engine → research_engine/data_access/s3_source.py → S3

This guard fails the build if any ACTIVE module under research_engine/ reintroduces
a direct production-source read from local ``logs/<dataset>`` or treats the derived
``data/research/research_universe.jsonl`` file as a source. Future research code
must go through the shared S3 data-access layer, even if this migration is later
forgotten.

Precision: the guard flags a forbidden path literal ONLY when that literal is
actually consumed by a filesystem read in the same module (open / Path.read_text /
rglob / glob / iterdir / exists / a local _load_jsonl helper). Purely descriptive
strings — report ``dataset_sources=[...]`` labels, log messages, registry
descriptions — are not reads and are not flagged.

Documented exemptions (named, never silent):
    - the shared S3 layer + the loaders that delegate to it;
    - documentation-only contract modules (lineage strings, not reads);
    - retired/dead universe builders the orchestrator never builds;
    - derived-artifact PRODUCERS (they WRITE rebuildable artifacts) + the
      research-ready/governance staging pipeline — migrating their own source
      reads is an explicit, separate follow-up; they are not on the active
      research READ path that experiments/universes use;
    - operational-state and derived-view local files (logs/research_lifecycle/,
      logs/research_views/) which are the Research Engine's own operational
      output, not production source datasets.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.production_data_contract import PRODUCTION_SCHEMA_REGISTRY

ROOT = Path(__file__).resolve().parent.parent
RE_DIR = ROOT / "research_engine"

_SHARED_LAYER = "research_engine/data_access/s3_source.py"

# Production-source read targets that must come from S3, not local disk:
#   - logs/<dataset>/           for every production-contract dataset
#   - data/research/research_universe.jsonl   (derived source-of-record file)
_FORBIDDEN_DERIVED_SOURCE = "data/research/research_universe.jsonl"
_PROD_DATASET_DIRS = tuple(f"logs/{name}" for name in PRODUCTION_SCHEMA_REGISTRY)
# Plus the research-ready staging dataset (derived, but historically read locally).
_RESEARCH_READY_DIR = "logs/research_ready_trade_dataset"

# Tokens that indicate a string literal is used for a filesystem READ.
_READ_TOKENS = ("open(", "read_text(", "rglob(", ".glob(", "iterdir(", "exists(", "_load_jsonl", "Path(")

# Files exempt from the guard, each with a documented reason.
_EXEMPT: dict[str, str] = {
    "research_engine/data_access/s3_source.py": "the shared S3 access layer",
    "research_engine/data_access/loaders.py": "delegates to the shared S3 layer",
    # Documentation-only: descriptive source strings, not reads.
    "research_engine/v10/universes/contracts.py": "documentation-only source_datasets strings",
    "research_engine/v10/universes/future_data_contract.py": "documentation-only path patterns",
    # Retired / dead — orchestrator explicitly pops it and never builds it.
    "research_engine/v10/universes/shadow_reality_universe.py": "RETIRED — not built by orchestrator",
    # Derived-artifact PRODUCERS + research-ready/governance staging pipeline.
    # Their own source reads are a separate, tracked follow-up. Not on the active
    # research READ path used by experiments/universes.
    "research_engine/v10/research_universe.py": "PRODUCER of the research_universe artifact (follow-up)",
    "research_engine/v10/decision_enrichment.py": "PRODUCER of the enriched research-ready artifact (follow-up)",
    "research_engine/v10/pnl_normalization.py": "PnL normalisation helper for the research-ready producer (follow-up)",
    "research_engine/v10/data_governance.py": "governance gate over the research-ready staging pipeline (follow-up)",
    "research_engine/v10/segmentation.py": "legacy segmentation; local view cache, source falls back to S3 load_trades (follow-up)",
    "research_engine/v10/anomaly_layer.py": "writes/reads its own logs/research_views derived-view output",
    "research_engine/v10/shadow/shadow_registry.py": "PRODUCER of data/research/shadow registry state (follow-up)",
    "research_engine/registry/registry_audit.py": "offline registry audit CLI over shadow/trace dirs (follow-up)",
    "research_engine/audit/run_audit.py": "offline audit CLI (follow-up)",
    "research_engine/v10/operations/storage.py": "Lambda storage abstraction (separate follow-up)",
    "research_engine/v10/operations/router.py": "Lambda /tmp staging (separate follow-up)",
    "research_engine/v10/operations/research_report.py": "operations report over research_universe artifact (follow-up)",
    # Migrated consumers that still declare a now-unused legacy constant purely
    # as a default-arg fallback string (the actual read is S3). Listed so the
    # descriptive constant does not trip the guard; verified they read via
    # get_default_source().read_artifact("research_universe").
    "research_engine/v10/segmentation_engine.py": "reads research_universe via S3 read_artifact; constant is a dead default only",
    "research_engine/v10/research_intelligence/experiment_runner.py": "reads via S3 segmenter; constant is a dead default only",
    "research_engine/v10/validation_lab/replay_engine.py": "reads research_universe via S3 read_artifact; constant is a dead default only",
    "research_engine/v10/baselines/snapshot_builder.py": "reads research_universe via S3 read_artifact; constant is a dead default only",
    # Cockpit reads operational lifecycle state (logs/research_lifecycle/), not
    # production source datasets.
    "research_engine/v10/cockpit/aggregator.py": "reads operational lifecycle state, not production source datasets",
}


def _iter_active_py_files():
    for p in sorted(RE_DIR.rglob("*.py")):
        parts = p.parts
        if "__pycache__" in parts or "tests" in parts:
            continue
        yield p


def _rel(p: Path) -> str:
    return str(p.relative_to(ROOT)).replace("\\", "/")


def _forbidden_read_offenders(rel: str, source: str) -> list[str]:
    """Return offending line descriptions where a forbidden production-source
    path literal is used on a line that also performs a filesystem read."""
    offenders: list[str] = []
    lines = source.splitlines()
    forbidden_fragments = (_FORBIDDEN_DERIVED_SOURCE, _RESEARCH_READY_DIR, *_PROD_DATASET_DIRS)

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # Does this line reference a forbidden production-source path?
        hit = next((f for f in forbidden_fragments if f in line), None)
        if not hit:
            continue
        # Is the SAME line (or a small window) performing a read? Look at this
        # line plus the next 2 for a read token applied to the path.
        window = "\n".join(lines[i - 1 : i + 2])
        if any(tok in window for tok in _READ_TOKENS):
            offenders.append(f"{rel}:{i}: {stripped}  [source path: {hit}]")
    return offenders


def test_no_active_research_module_reads_production_source_from_local_disk():
    """No active research_engine module may READ a production-source dataset from
    local logs/ or the derived research_universe.jsonl — S3 is authoritative."""
    offenders: list[str] = []
    for path in _iter_active_py_files():
        rel = _rel(path)
        if rel in _EXEMPT:
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        offenders.extend(_forbidden_read_offenders(rel, source))

    assert not offenders, (
        "Active research_engine modules must read source data from S3 via the "
        "shared data-access layer, not local logs/ or data/research/. Offenders:\n"
        + "\n".join(offenders)
        + "\n\nRoute the read through research_engine.data_access.s3_source, or if "
          "the module is a legitimate producer/dead/doc/operational exception add "
          "it to _EXEMPT with a documented reason."
    )


def test_s3_clients_created_only_in_shared_layer():
    """boto3 S3 clients must be created only inside the shared S3 access layer,
    not scattered across individual research modules/experiments.

    The operations/ Lambda subsystem and the research-report publisher are
    separate documented subsystems (their own follow-up)."""
    offenders: list[str] = []
    allowed = {
        _SHARED_LAYER,
        "research_engine/v10/operations/storage.py",
        "research_engine/v10/operations/router.py",
        "research_engine/v10/persistence/s3_publisher.py",  # report/artifact publisher
        "research_engine/v10/cockpit/refresh.py",           # cockpit S3 report refresh
    }
    for path in _iter_active_py_files():
        rel = _rel(path)
        if rel in allowed:
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        for i, line in enumerate(source.splitlines(), 1):
            s = line.strip()
            if s.startswith("#"):
                continue
            if "boto3.client(" in s or "boto3.resource(" in s:
                offenders.append(f"{rel}:{i}: {s}")

    assert not offenders, (
        "boto3 S3 clients must be created only in the shared S3 access layer. "
        "Route reads through S3ResearchDataSource. Offenders:\n" + "\n".join(offenders)
    )


def test_shared_layer_exists_and_exposes_contract():
    """The shared S3 layer must exist and expose the dataset-oriented API."""
    layer = ROOT / _SHARED_LAYER
    assert layer.exists(), "shared S3 access layer missing"
    src = layer.read_text(encoding="utf-8")
    for token in (
        "class S3ResearchDataSource",
        "def read_dataset",
        "def read_artifact",
        "NEW_RUNTIME_S3_BUCKET",
        "s3_base_prefix",
        "ResearchDataSourceError",
    ):
        assert token in src, f"shared S3 layer missing contract element: {token}"


def test_core_loaders_route_through_shared_layer():
    """The 12 dataset loaders must delegate to the shared S3 source (no logs/ reads)."""
    src = (RE_DIR / "data_access" / "loaders.py").read_text(encoding="utf-8")
    assert "get_default_source" in src
    # No filesystem read of a logs/ path (prose mentions of the word are fine).
    assert not _forbidden_read_offenders("research_engine/data_access/loaders.py", src), \
        "loaders.py must not read local logs/ paths"
