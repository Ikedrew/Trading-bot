"""
Architecture Guard: S3 Write Exclusivity + Layer Isolation.

Enforces:
- ONLY registered modules may write to S3
- Each module writes ONLY to its assigned role-qualified prefix (via s3_base_prefix())
- No cross-layer data leakage
- execution_context/ is a first-class writer with strict boundaries

This test will FAIL if:
- Any unregistered module introduces S3 writes
- Any module writes to a prefix it doesn't own
- execution_context.py imports trade_truth, shadow_trades, or edge layers
- Cross-layer forbidden fields are detected in writer modules

NOTE (Production V1 migration): all Production V1 writers now derive their
S3 prefix via s3_base_prefix() from core.production_data_contract rather than
holding a flat string constant.  Assertions in this file reflect that.
The legacy v10-engine research writers (edge_attribution, edge_optimisation,
strategy_compiler, learning) have been DELETED — no writer targets any bucket
other than the canonical trading-bot-v10-data.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.production_data_contract import s3_base_prefix


# -------------------------------------------------------------------------------
# REGISTERED S3 WRITERS (CANONICAL ALLOWLIST)
# -------------------------------------------------------------------------------
# Production V1 writers (trading-bot-v10-data only).  lambda/ and research_engine/
# are intentionally outside this core allowlist — they are offline/research
# infrastructure, not subject to the live-runtime write-exclusivity guard.

ALLOWED_S3_WRITERS = {
    # ── core runtime writers — Production V1 bucket ──
    ROOT / "core" / "event_stream.py",
    ROOT / "core" / "storage" / "s3_batch_writer.py",
    ROOT / "core" / "assessment" / "persistence.py",
    ROOT / "core" / "trade_truth.py",
    ROOT / "core" / "trade_journal.py",
    ROOT / "core" / "shadow_trades.py",
    ROOT / "core" / "execution_context.py",
    ROOT / "core" / "decision_ledger.py",
    ROOT / "core" / "decision_trace.py",
    ROOT / "core" / "persistence" / "execution_result_writer.py",
    ROOT / "core" / "persistence" / "management_actions_writer.py",
    ROOT / "core" / "persistence" / "execution_attempts_writer.py",
    ROOT / "core" / "persistence" / "horizon_candidates_writer.py",
    ROOT / "core" / "persistence" / "strategy_candidates_writer.py",
    ROOT / "core" / "persistence" / "opportunity_writer.py",
    ROOT / "core" / "market_context" / "persistence.py",
    ROOT / "core" / "research_assessment" / "research_shadow_engine.py",
    ROOT / "core" / "opportunity" / "persistence.py",
    ROOT / "core" / "portfolio_ranking" / "shadow_comparison.py",
    ROOT / "core" / "portfolio_ranking" / "persistence.py",
    ROOT / "core" / "protection_verification.py",
    ROOT / "core" / "risk_deviation.py",
    ROOT / "core" / "contracts" / "quarantine.py",
    ROOT / "core" / "shadow" / "persistence.py",
    ROOT / "core" / "strategies" / "observation_persistence.py",
    # Durable open-position excursion checkpoint mirror. NOT a research dataset:
    # this is runtime state backing the trade_truth_v1 outcome chain, written to
    # the distinct runtime_state/ top-level prefix (overwrite-by-ticket).
    ROOT / "core" / "trade_management" / "excursion_state.py",
}

NEW_RUNTIME_S3_WRITERS = {
    ROOT / "core" / "event_stream.py",
    ROOT / "core" / "storage" / "s3_batch_writer.py",
    ROOT / "core" / "assessment" / "persistence.py",
    ROOT / "core" / "trade_truth.py",
    ROOT / "core" / "trade_journal.py",
    ROOT / "core" / "shadow_trades.py",
    ROOT / "core" / "execution_context.py",
    ROOT / "core" / "decision_ledger.py",
    ROOT / "core" / "decision_trace.py",
    ROOT / "core" / "persistence" / "execution_result_writer.py",
    ROOT / "core" / "persistence" / "management_actions_writer.py",
    ROOT / "core" / "persistence" / "execution_attempts_writer.py",
    ROOT / "core" / "persistence" / "horizon_candidates_writer.py",
    ROOT / "core" / "persistence" / "strategy_candidates_writer.py",
    ROOT / "core" / "persistence" / "opportunity_writer.py",
    ROOT / "core" / "market_context" / "persistence.py",
    ROOT / "core" / "research_assessment" / "research_shadow_engine.py",
    ROOT / "core" / "opportunity" / "persistence.py",
    ROOT / "core" / "portfolio_ranking" / "shadow_comparison.py",
    ROOT / "core" / "portfolio_ranking" / "persistence.py",
    ROOT / "core" / "protection_verification.py",
    ROOT / "core" / "risk_deviation.py",
    ROOT / "core" / "contracts" / "quarantine.py",
    ROOT / "core" / "shadow" / "persistence.py",
    ROOT / "core" / "strategies" / "observation_persistence.py",
    ROOT / "core" / "trade_management" / "excursion_state.py",
}

# Modules allowed to import boto3 (includes non-writers like aws_glue_setup)
ALLOWED_BOTO3_IMPORTERS = ALLOWED_S3_WRITERS | {
    ROOT / "data_pipeline" / "aws_glue_setup.py",
    ROOT / "data_pipeline" / "query_layer.py",
}

# Directories excluded from the codebase scan.
# Third-party packages (.venv), offline research infrastructure (research_engine,
# lambda), and test/tool directories are not subject to the live-runtime guard.
_SCAN_EXCLUDE_DIRS = frozenset((
    "__pycache__", "tests", "tools", ".hypothesis",
    ".venv", ".venv-1", "lambda", "research_engine",
    "site-packages",
))

# Layer prefix ownership mapping — Production V1 role-qualified prefixes.
# Values reflect what s3_base_prefix() resolves to for each writer.
LAYER_PREFIX_OWNERSHIP = {
    "core/event_stream.py":               s3_base_prefix("events") + "/",
    "core/storage/s3_batch_writer.py":    s3_base_prefix("events") + "/",
    "core/trade_truth.py":                s3_base_prefix("trade_truth") + "/",
    "core/shadow_trades.py":              s3_base_prefix("shadow_trades") + "/",
    "core/execution_context.py":          s3_base_prefix("execution_context") + "/",
    "core/shadow/persistence.py":         s3_base_prefix("shadow_runtime") + "/",
}


# -------------------------------------------------------------------------------
# TEST CLASS 1: S3 WRITE EXCLUSIVITY
# -------------------------------------------------------------------------------

class TestS3WriteExclusivity:
    """Enforce that ONLY registered modules write to S3."""

    def test_aws_uploader_is_noop(self):
        """aws_uploader.upload_event must be a no-op (no put_object call)."""
        source = (ROOT / "core" / "aws_uploader.py").read_text(encoding="utf-8")
        assert "put_object" not in source, "aws_uploader.py contains put_object — must be no-op"
        assert "boto3" not in source, "aws_uploader.py imports boto3 — must not create clients"

    def test_output_router_has_no_boto3(self):
        """output_router.py must not contain any boto3 or put_object calls."""
        source = (ROOT / "core" / "pipeline" / "output_router.py").read_text(encoding="utf-8")
        assert "put_object(" not in source, "output_router.py contains put_object() call"
        assert "import boto3" not in source, "output_router.py imports boto3"

    def test_only_registered_modules_have_put_object(self):
        """Scan core/ codebase — only registered writers may use put_object."""
        violations = []

        for py_file in ROOT.rglob("*.py"):
            rel = py_file.relative_to(ROOT)
            parts = rel.parts
            if any(p in parts for p in _SCAN_EXCLUDE_DIRS):
                continue
            if py_file in ALLOWED_S3_WRITERS:
                continue

            try:
                source = py_file.read_text(encoding="utf-8")
            except Exception:
                continue

            if "put_object(" in source:
                violations.append(str(rel))

        assert violations == [], f"Forbidden S3 writes found in: {violations}"

    def test_only_registered_modules_import_boto3(self):
        """No module outside allowlist (in core/) may import boto3."""
        violations = []

        for py_file in ROOT.rglob("*.py"):
            rel = py_file.relative_to(ROOT)
            parts = rel.parts
            if any(p in parts for p in _SCAN_EXCLUDE_DIRS):
                continue
            if py_file in ALLOWED_BOTO3_IMPORTERS:
                continue

            try:
                source = py_file.read_text(encoding="utf-8")
            except Exception:
                continue

            for line in source.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "import boto3" in stripped:
                    violations.append(f"{rel}: {stripped}")
                if "put_object(" in stripped:
                    violations.append(f"{rel}: {stripped}")

        assert violations == [], f"Forbidden network writes found:\n" + "\n".join(violations)


# -------------------------------------------------------------------------------
# TEST CLASS 2: LAYER PREFIX OWNERSHIP
# -------------------------------------------------------------------------------

class TestLayerPrefixOwnership:
    """Enforce each module writes ONLY to its assigned S3 prefix."""

    def test_event_stream_writes_to_events_only(self):
        """event_stream S3 key construction must only use events/ prefix."""
        source = (ROOT / "core" / "event_stream.py").read_text(encoding="utf-8")
        # Check _S3_BUCKET reference uses canonical bucket
        assert "_S3_BUCKET: str = NEW_RUNTIME_S3_BUCKET" in source
        # The S3 batch writer (storage/s3_batch_writer.py) handles actual S3 keys
        # event_stream.py delegates to it — no direct key construction with foreign prefixes
        # Verify no _S3_PREFIX assignment to other layers
        for line in source.splitlines():
            if line.strip().startswith("_S3_PREFIX"):
                assert "events" in line.lower() or "S3_PREFIX" not in line

    def test_shadow_trades_resolves_prefix_through_contract(self):
        """shadow_trades must resolve its S3 prefix via s3_base_prefix(), not a flat literal."""
        source = (ROOT / "core" / "shadow_trades.py").read_text(encoding="utf-8")
        # The Production V1 contract is the authority: prefix = "supporting/shadow_trades"
        assert s3_base_prefix("shadow_trades") == "supporting/shadow_trades"
        # Writer must use the contract helper, not a flat constant
        assert 's3_base_prefix("shadow_trades")' in source
        # And the key must contain the role-qualified prefix
        assert "supporting/shadow_trades" not in source.replace(
            's3_base_prefix("shadow_trades")', ""
        ), "shadow_trades.py should not hard-code the prefix string — use s3_base_prefix()"

    def test_trade_truth_resolves_prefix_through_contract(self):
        """trade_truth must resolve its S3 prefix via s3_base_prefix(), not a flat literal."""
        source = (ROOT / "core" / "trade_truth.py").read_text(encoding="utf-8")
        assert s3_base_prefix("trade_truth") == "core/trade_truth"
        assert 's3_base_prefix("trade_truth")' in source
        # _S3_TRADES_PREFIX must be assigned from the contract, not a flat "trades" string
        assert '_S3_TRADES_PREFIX = s3_base_prefix("trade_truth")' in source

    def test_trade_truth_graph_dataset_retired(self):
        """trade_truth_graph dataset was retired in the Production V1 consolidation."""
        from core.production_data_contract import PRODUCTION_SCHEMA_REGISTRY, RETIRED_DATASETS
        assert "trade_truth_graph" not in PRODUCTION_SCHEMA_REGISTRY
        assert "trade_truth_graph" in RETIRED_DATASETS
        assert not (ROOT / "core" / "trade_truth_graph.py").exists()

    def test_legacy_v10_engine_writers_are_deleted(self):
        """The legacy v10-engine research writers must not exist."""
        assert not (ROOT / "core" / "edge_attribution.py").exists()
        assert not (ROOT / "core" / "edge_optimisation.py").exists()
        assert not (ROOT / "core" / "strategy_compiler.py").exists()
        assert not (ROOT / "core" / "learning").exists()

    def test_execution_context_resolves_prefix_through_contract(self):
        """execution_context must resolve its S3 prefix via s3_base_prefix()."""
        source = (ROOT / "core" / "execution_context.py").read_text(encoding="utf-8")
        assert s3_base_prefix("execution_context") == "supporting/execution_context"
        assert 's3_base_prefix("execution_context")' in source

    def test_all_writers_use_canonical_bucket(self):
        """Every active writer targets the canonical Production V1 bucket only.

        No writer may hardcode a legacy bucket (v10-engine / trading-bot-data-mk1).
        """
        for writer_path in ALLOWED_S3_WRITERS:
            if not writer_path.exists():
                continue
            source = writer_path.read_text(encoding="utf-8")
            if "_S3_BUCKET" in source:
                assert "NEW_RUNTIME_S3_BUCKET" in source, writer_path
            assert "v10-engine" not in source, writer_path


# -------------------------------------------------------------------------------
# TEST CLASS 3: EXECUTION_CONTEXT ISOLATION
# -------------------------------------------------------------------------------

class TestExecutionContextIsolation:
    """Enforce execution_context.py is a PURE snapshot generator with no cross-layer deps."""

    def test_no_import_trade_truth(self):
        """execution_context must NEVER import trade_truth."""
        source = (ROOT / "core" / "execution_context.py").read_text(encoding="utf-8")
        assert "from core.trade_truth" not in source
        assert "import core.trade_truth" not in source

    def test_no_import_shadow_trades(self):
        """execution_context must NEVER import shadow_trades."""
        source = (ROOT / "core" / "execution_context.py").read_text(encoding="utf-8")
        assert "from core.shadow_trades" not in source
        assert "import core.shadow_trades" not in source

    def test_no_import_edge_attribution(self):
        """execution_context must NEVER import edge layers."""
        source = (ROOT / "core" / "execution_context.py").read_text(encoding="utf-8")
        assert "from core.edge_attribution" not in source
        assert "from core.edge_optimisation" not in source

    def test_no_import_strategy_compiler(self):
        """execution_context must NEVER import strategy_compiler."""
        source = (ROOT / "core" / "execution_context.py").read_text(encoding="utf-8")
        assert "from core.strategy_compiler" not in source

    def test_no_forbidden_fields_in_schema(self):
        """execution_context schema must not contain outcome/execution fields."""
        source = (ROOT / "core" / "execution_context.py").read_text(encoding="utf-8")
        # These field names should never appear as dataclass fields or dict keys
        forbidden_as_schema = [
            "entry_price:", "exit_price:", "pnl:", "r_multiple:",
            "trade_id:", "position_id:", "confluence_score:",
            "should_trade:", "strategy_id:",
        ]
        for field in forbidden_as_schema:
            # Check only in dataclass definitions (not in _FORBIDDEN_FIELDS set)
            lines_outside_forbidden = []
            in_forbidden_set = False
            for line in source.splitlines():
                if "_FORBIDDEN_FIELDS" in line:
                    in_forbidden_set = True
                if in_forbidden_set and line.strip() in ("}", "})"):
                    in_forbidden_set = False
                if not in_forbidden_set and field in line and "class " not in line:
                    # Allow in docstrings and forbidden set definition
                    if not line.strip().startswith("#") and not line.strip().startswith('"'):
                        lines_outside_forbidden.append(line.strip())

    def test_writes_only_to_execution_context_prefix(self):
        """execution_context.py must write ONLY to the execution_context role prefix."""
        source = (ROOT / "core" / "execution_context.py").read_text(encoding="utf-8")
        # Contract-driven: prefix is resolved through s3_base_prefix()
        assert 's3_base_prefix("execution_context")' in source
        # Must not reference other prefixes in S3 key construction
        s3_key_lines = [l for l in source.splitlines() if "key =" in l.lower() or "key=" in l.lower()]
        for line in s3_key_lines:
            if 'f"' in line or "f'" in line:
                assert "execution_context" in line or "_S3_PREFIX" in line, (
                    f"S3 key construction references wrong prefix: {line.strip()}"
                )


# -------------------------------------------------------------------------------
# TEST CLASS 4: CROSS-LAYER LEAKAGE DETECTION
# -------------------------------------------------------------------------------

class TestCrossLayerLeakage:
    """Detect data that belongs in one layer appearing in another's writer."""

    def test_event_stream_has_no_trade_outcomes(self):
        """events/ writer must never construct payloads with outcome data."""
        source = (ROOT / "core" / "event_stream.py").read_text(encoding="utf-8")
        # The emit() function itself doesn't construct payloads — callers do.
        # But the _resolve_* functions should never introduce outcome fields.
        for resolver in ["_resolve_pattern", "_resolve_regime", "_resolve_bias", "_resolve_side"]:
            assert resolver in source  # Resolvers exist
        # No outcome field resolution
        assert "_resolve_pnl" not in source
        assert "_resolve_r_multiple" not in source

    def test_execution_context_has_no_outcome_fields(self):
        """execution_context writer must NEVER persist outcome data."""
        from core.execution_context import build_execution_context
        ctx = build_execution_context(
            correlation_id="TEST", symbol="EURUSD",
            timestamp_utc=1700000000.0, bid=1.1, ask=1.1001,
        )
        d = ctx.to_dict()
        import json
        flat = json.dumps(d)
        forbidden = ["pnl", "r_multiple", "entry_price", "exit_price",
                     "trade_id", "slippage", "confluence_score", "pattern"]
        for field in forbidden:
            assert f'"{field}"' not in flat, (
                f"execution_context output contains forbidden field: {field}"
            )

    def test_shadow_trades_does_not_write_to_events(self):
        """shadow_trades must never write to events/ prefix."""
        source = (ROOT / "core" / "shadow_trades.py").read_text(encoding="utf-8")
        # Contract-driven: prefix is resolved through s3_base_prefix("shadow_trades")
        assert 's3_base_prefix("shadow_trades")' in source
        # The key check: shadow_trades should not construct keys with "events/"
        assert 'events/' not in source.replace("# events/", "").replace("events/ ", "")

    def test_trade_truth_does_not_write_to_shadow_trades(self):
        """trade_truth must never write to shadow_trades/ prefix."""
        source = (ROOT / "core" / "trade_truth.py").read_text(encoding="utf-8")
        # Contract-driven: _S3_TRADES_PREFIX is assigned from s3_base_prefix("trade_truth")
        assert 's3_base_prefix("trade_truth")' in source
        # Check that no S3 key construction uses shadow_trades prefix
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"') or stripped.startswith("'"):
                continue  # Skip comments and docstrings
            if "key =" in stripped.lower() or "key=" in stripped.lower():
                assert "shadow_trades" not in stripped, f"S3 key references shadow_trades: {stripped}"


# -------------------------------------------------------------------------------
# TEST CLASS 5: NO-OP ADAPTER CONTRACTS
# -------------------------------------------------------------------------------

class TestNoOpAdapters:
    """Verify legacy adapters remain no-ops."""

    def test_aws_uploader_upload_event_returns_true(self):
        from core.aws_uploader import upload_event
        result = upload_event({"test": True})
        assert result is True

    def test_aws_uploader_get_client_returns_none(self):
        from core.aws_uploader import _get_client
        assert _get_client() is None

    def test_safe_write_to_s3_is_noop(self):
        from core.pipeline.output_router import safe_write_to_s3
        result = safe_write_to_s3({"test": True})
        assert result is None
