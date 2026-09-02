"""
Static anti-regression guard for the canonical Production V1 lineage.

The governing rule: ALL canonical Production V1 data flows through
    observation_id  (market observation)
    canonical_opportunity_id  (opportunity/decision/execution/outcome/shadow)

The retired V2/V3 opportunity/shadow lineage has been DELETED. This guard fails
the build if any ACTIVE runtime/research code reintroduces it.

Allowed exceptions (NOT scanned as active runtime/research):
    - AWS API names such as ``list_objects_v2``
    - the explicit RETIRED_DATASETS anti-regression allowlist (names that must
      never return)
    - archived historical scripts/tests under analysis/ and captured data
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# ACTIVE runtime/research source trees. analysis/ (archived historical audits)
# and tests/ are intentionally excluded.
ACTIVE_DIRS = ("core", "research_engine", "data_pipeline", "execution", "risk", "strategy")

# Modules/packages that were deleted with the retired lineage.
FORBIDDEN_IMPORT_PREFIXES = (
    "core.v3_shadow",
    "core.v2_opportunity",
    "core.v3_opportunity",
    "core.observers.v2_opportunity_observer",
    "core.observers.v3_opportunity_observer",
    "core.research.v2_outcome_linker",
    "core.research.v3_outcome_linker",
    "research_engine.v2_discovery",
    # Deleted legacy v10-engine research writers.
    "core.strategy_compiler",
    "core.edge_attribution",
    "core.edge_optimisation",
    "core.learning",
)

# Retired identity model / symbol names that must not appear in active code.
FORBIDDEN_SYMBOLS = (
    "V2Opportunity",
    "V3Opportunity",
    "V3MarketContext",
    "build_v3_market_context",
    "observe_v2_opportunity",
    "observe_v3_opportunity",
    "observe_market_understanding",
    "persist_location_observation",
    "link_v3_shadow_outcomes",
    "V3ShadowLinkageReport",
    # Retired event-type / record-role tokens from the deleted V3 shadow lineage.
    # No active writer emits these and no active reader may special-case them.
    "V3_DIAGNOSTIC",
    "V3_CONTEXT",
    "v3_decision_diagnostic",
    "v3_market_interpretation",
)

# Old local persistence routes for the retired lineage.
FORBIDDEN_PATH_LITERALS = (
    "logs/v3_shadow",
    "logs/v2_opportunities",
    "logs/v3_opportunities",
)


def _iter_active_py_files():
    for d in ACTIVE_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            yield p


def _imported_modules(tree: ast.AST) -> set[str]:
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                mods.add(node.module)
    return mods


def test_no_deleted_modules_are_deleted_on_disk():
    """The retired lineage modules/packages must not exist on disk."""
    for rel in (
        "core/v3_shadow",
        "core/v2_opportunity.py",
        "core/v3_opportunity.py",
        "core/v2_opportunity_builder.py",
        "core/v3_opportunity_builder.py",
        "core/observers/v2_opportunity_observer.py",
        "core/observers/v3_opportunity_observer.py",
        "core/research/v2_outcome_linker.py",
        "core/research/v3_outcome_linker.py",
        "research_engine/v2_discovery",
    ):
        assert not (ROOT / rel).exists(), f"retired path still present: {rel}"


def test_active_code_has_no_retired_lineage_imports():
    """No active runtime/research module imports a deleted retired-lineage module."""
    offenders: list[str] = []
    for path in _iter_active_py_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for mod in _imported_modules(tree):
            for prefix in FORBIDDEN_IMPORT_PREFIXES:
                if mod == prefix or mod.startswith(prefix + "."):
                    offenders.append(f"{path.relative_to(ROOT)} imports {mod}")
    assert not offenders, "retired lineage imports found:\n" + "\n".join(offenders)


def test_active_code_has_no_retired_identity_symbols():
    """No active runtime/research source references retired V2/V3 identity symbols."""
    offenders: list[str] = []
    for path in _iter_active_py_files():
        text = path.read_text(encoding="utf-8-sig")
        for sym in FORBIDDEN_SYMBOLS:
            if sym in text:
                offenders.append(f"{path.relative_to(ROOT)} references {sym}")
    assert not offenders, "retired identity symbols found:\n" + "\n".join(offenders)


def test_active_code_has_no_retired_local_persistence_routes():
    """No active runtime/research source writes/reads the retired local routes."""
    offenders: list[str] = []
    for path in _iter_active_py_files():
        text = path.read_text(encoding="utf-8-sig")
        for lit in FORBIDDEN_PATH_LITERALS:
            if lit in text:
                offenders.append(f"{path.relative_to(ROOT)} references {lit}")
    assert not offenders, "retired local persistence routes found:\n" + "\n".join(offenders)


def test_canonical_identity_helpers_exist():
    """The single canonical identity model must be present and importable."""
    from core.identity.canonical import make_canonical_opportunity_id, mint_observation_id

    assert callable(make_canonical_opportunity_id)
    assert callable(mint_observation_id)


# ─── Archived analysis guard ──────────────────────────────────────────────────
# The analysis/ scripts are archived (not active runtime/research), but they must
# not reintroduce dependencies on the deleted V2/V3 opportunity/shadow lineage:
# deleted modules, retired outcome linkers, or the retired V2 discovery package.

ANALYSIS_DIR = ROOT / "analysis"


def _iter_analysis_py_files():
    if not ANALYSIS_DIR.exists():
        return
    for p in ANALYSIS_DIR.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        yield p


def test_analysis_has_no_retired_lineage_imports():
    """No analysis script may import a deleted V2/V3 opportunity/shadow module."""
    offenders: list[str] = []
    for path in _iter_analysis_py_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for mod in _imported_modules(tree):
            for prefix in FORBIDDEN_IMPORT_PREFIXES:
                if mod == prefix or mod.startswith(prefix + "."):
                    offenders.append(f"{path.relative_to(ROOT)} imports {mod}")
    assert not offenders, "retired lineage imports in analysis/:\n" + "\n".join(offenders)


def test_analysis_has_no_retired_lineage_data_routes():
    """No analysis script may read the retired V2/V3 datasets/schemas/local routes."""
    markers = (
        "logs/v3_shadow",
        "logs/v2_opportunities",
        "logs/v3_opportunities",
        "shadow_trades_v2",
        "trade_truth_v3",
        "V2Opportunity",
        "V3Opportunity",
    )
    offenders: list[str] = []
    for path in _iter_analysis_py_files():
        text = path.read_text(encoding="utf-8-sig")
        for marker in markers:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)} references {marker}")
    assert not offenders, "retired lineage data routes in analysis/:\n" + "\n".join(offenders)


# ─── Legacy persistence-universe guard ────────────────────────────────────────
# The legacy v10-engine research writers (strategy_compiler, edge_attribution,
# edge_optimisation, learning/store) have been DELETED. No active runtime/research
# source may write to a legacy bucket or create a second persistence universe.
# The sole active trading/research data bucket is trading-bot-v10-data.

LEGACY_BUCKETS = ("v10-engine", "trading-bot-data-mk1")

# Read-only / validation / infrastructure modules that legitimately still NAME a
# legacy bucket in a docstring, a historical S3 READER path, or a contract-layer
# string registry. None of these WRITE to a legacy bucket. Verified by
# test_no_active_writer_targets_a_legacy_bucket (which scans for write sinks).
_LEGACY_BUCKET_STRING_ALLOWED = {
    "data_pipeline/query_layer.py",      # docstring: remote READER over mk1
    "data_pipeline/aws_glue_setup.py",   # Athena/Glue infra over the mk1 data lake
    "core/trade_truth.py",               # docstring only; writes via NEW_RUNTIME_S3_BUCKET
    "core/strategies/observation_persistence.py",  # docstring only; writes canonical
    "core/shadow_trades.py",             # docstring only; writes via NEW_RUNTIME_S3_BUCKET
    "core/portfolio_ranking/persistence.py",       # docstring only
    "core/persistence/strategy_candidates_writer.py",  # docstring only
    "core/persistence/opportunity_writer.py",          # docstring only
    "core/persistence/execution_result_writer.py",     # docstring only
    "core/offline_query.py",             # read-only analytics; no bucket writes
    "core/feature_role_contract.py",     # contract-layer string registry
}


def _writes_to_bucket_literal(text: str, bucket: str) -> bool:
    """Heuristic: a bucket literal assigned to an _S3_BUCKET constant or passed to
    a put_object/upload Bucket= argument (i.e. an executable WRITE sink)."""
    import re

    patterns = (
        rf'_S3_BUCKET\s*=\s*["\']{re.escape(bucket)}["\']',
        rf'Bucket\s*=\s*["\']{re.escape(bucket)}["\']',
    )
    return any(re.search(p, text) for p in patterns)


# Research REPORT/DASHBOARD publishing infrastructure. This is NOT the trading/
# research DATASET universe — it emits human-facing report artifacts (JSON/MD/HTML
# dashboards) under reports/v10-research/, env-configurable via RESEARCH_BUCKET.
# It is explicitly independent report infrastructure, not a trading-data universe.
# (Flagged for the data-layer audit; excluded from the dataset-writer guard.)
_RESEARCH_REPORT_INFRA_ALLOWED = {
    "research_engine/v10/persistence/s3_publisher.py",
    "research_engine/v10/operations/storage.py",
    "research_engine/v10/cockpit/refresh.py",
}


def test_no_active_dataset_writer_targets_a_legacy_bucket():
    """No active runtime/research DATASET writer assigns a legacy bucket as a sink.

    The research report/dashboard publisher (reports/v10-research/) is explicit,
    env-configurable report infrastructure and is excluded — it does not persist
    trading/research datasets.
    """
    offenders: list[str] = []
    for path in _iter_active_py_files():
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        if rel in _RESEARCH_REPORT_INFRA_ALLOWED:
            continue
        text = path.read_text(encoding="utf-8-sig")
        for bucket in LEGACY_BUCKETS:
            if _writes_to_bucket_literal(text, bucket):
                offenders.append(f"{rel} writes to legacy bucket {bucket}")
    assert not offenders, "legacy-bucket dataset-write sinks found:\n" + "\n".join(offenders)


def test_legacy_bucket_names_only_in_allowed_readonly_modules():
    """Any mention of a legacy bucket in active source must be in an allowed
    read-only/infra/docstring module — never a new write universe."""
    allowed = _LEGACY_BUCKET_STRING_ALLOWED | _RESEARCH_REPORT_INFRA_ALLOWED
    offenders: list[str] = []
    for path in _iter_active_py_files():
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        text = path.read_text(encoding="utf-8-sig")
        for bucket in LEGACY_BUCKETS:
            if bucket in text and rel not in allowed:
                offenders.append(f"{rel} references legacy bucket {bucket}")
    assert not offenders, (
        "unexpected legacy-bucket reference (add to allowlist only if truly "
        "read-only/infra):\n" + "\n".join(offenders)
    )


def test_legacy_v10_engine_writers_deleted_on_disk():
    """The four named legacy v10-engine writers must not exist."""
    for rel in (
        "core/strategy_compiler.py",
        "core/edge_attribution.py",
        "core/edge_optimisation.py",
        "core/learning",
    ):
        assert not (ROOT / rel).exists(), f"legacy writer still present: {rel}"


def test_sole_active_data_bucket_is_v10_data():
    """The canonical Production V1 runtime bucket constant is trading-bot-v10-data."""
    from core.config import NEW_RUNTIME_S3_BUCKET

    assert NEW_RUNTIME_S3_BUCKET == "trading-bot-v10-data"
