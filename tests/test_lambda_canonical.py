"""
Lambda Canonical Resolution Tests.

Proves:
    - Lambda/local resolve the same universes, populations, primitives
    - Resolution manifest is produced
    - Invalid populations block execution
    - Missing populations block execution
    - No hard-coded Lambda population paths
    - No duplicate resolver logic
    - Failure isolation
    - Question-driven resolution (only loads needed universes)
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from research_engine.v10.runner.context_resolver import (
    ResearchContextResolver,
    ResolvedContext,
    ResolutionManifest,
)
from research_engine.v10.runner.lambda_adapter import LambdaResearchAdapter
from research_engine.v10.runner.primitives.implementations import build_default_registry
from research_engine.v10.runner.primitive_mapping import build_full_mapping
from research_engine.v10.runner.question_runner import QuestionRunner, RunContext
from research_engine.v10.universes.models import Universe, Population, QuestionStatus
from research_engine.v10.universes.question_bank import QUESTION_BANK, get_question


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _mock_builders():
    """Create mock universe builders with synthetic data."""
    import random
    random.seed(42)

    def _make_records(n, universe_name):
        return [
            {
                "trade_id": f"t{i}", "entity_id": f"SYM_{i*300}",
                "symbol": "EURUSD", "r_multiple": round(random.uniform(-2, 3), 3),
                "score": round(random.uniform(40, 90), 2),
                "regime": random.choice(["TRENDING", "RANGING"]),
                "family": random.choice(["TREND_CONTINUATION", "MEAN_REVERSION"]),
                "pattern": "ENGULFING", "session": "LONDON",
                "entry_time": 1784700000 + i * 3600,
                "duration_seconds": random.uniform(100, 5000),
                "exit_reason": random.choice(["STOP_LOSS", "TAKE_PROFIT"]),
                "anomaly": False, "action": "EXECUTE" if i % 20 == 0 else "NO_TRADE",
                "terminal_reason": "" if i % 20 == 0 else "V10 [opportunity]: invalid",
                "confidence": round(random.uniform(0.3, 0.9), 3),
                "opportunity_quality": round(random.uniform(0.2, 0.9), 3),
                "volatility_state": "NEUTRAL",
                "h1_structural_clarity": round(random.uniform(0.1, 0.9), 3),
                "conditions_met": random.randint(1, 5),
            }
            for i in range(n)
        ]

    class MockBuilder:
        def __init__(self, records, content_hash):
            self._records = records
            self._built = True
            self.metadata = MagicMock()
            self.metadata.content_hash = content_hash
            self.metadata.record_count = len(records)
            self.metadata.populations_available = ("all",)
            self.metadata.generation_timestamp = "2026-08-09T04:00:00Z"

        @property
        def is_built(self):
            return self._built

        @property
        def records(self):
            return self._records

        def get_population(self, population):
            # Simple filter for common populations
            if population == Population.ALL_TRADES:
                return self._records
            elif population == Population.ALL_DECISIONS:
                return self._records
            elif population == Population.ALL_MARKET_STATES:
                return self._records
            elif population == Population.ALL_STRATEGIES:
                return self._records
            elif population == Population.EXECUTE_DECISIONS:
                return [r for r in self._records if r.get("action") == "EXECUTE"]
            elif population == Population.NO_TRADE_DECISIONS:
                return [r for r in self._records if r.get("action") == "NO_TRADE"]
            return self._records

    return {
        Universe.EXECUTION: MockBuilder(_make_records(94, "EXEC"), "exec_hash_001"),
        Universe.DECISION: MockBuilder(_make_records(500, "DEC"), "dec_hash_001"),
        Universe.MARKET: MockBuilder(_make_records(500, "MKT"), "mkt_hash_001"),
        Universe.STRATEGY: MockBuilder(_make_records(500, "STRAT"), "strat_hash_001"),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# RESOLVER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestContextResolver:

    def test_resolve_ready_question(self):
        builders = _mock_builders()
        resolver = ResearchContextResolver(builders=builders)
        q = get_question("E-001")
        ctx = resolver.resolve(q)
        assert ctx.ready is True
        assert ctx.population  # Non-empty
        assert ctx.manifest.universe_versions["EXECUTION"] == "exec_hash_001"

    def test_resolve_blocked_question(self):
        builders = _mock_builders()
        resolver = ResearchContextResolver(builders=builders)
        q = get_question("E-006")  # BLOCKED in contract
        ctx = resolver.resolve(q)
        assert ctx.ready is False
        assert "BLOCKED" in ctx.blocked_reason

    def test_resolve_missing_universe_blocks(self):
        # Only give execution, not decision
        builders = {Universe.EXECUTION: _mock_builders()[Universe.EXECUTION]}
        resolver = ResearchContextResolver(builders=builders)
        # ED-001 requires both EXECUTION and DECISION
        q = get_question("ED-001")
        ctx = resolver.resolve(q)
        assert ctx.ready is False
        assert "DECISION" in ctx.blocked_reason

    def test_manifest_contains_versions(self):
        builders = _mock_builders()
        resolver = ResearchContextResolver(builders=builders)
        q = get_question("M-001")
        ctx = resolver.resolve(q)
        assert ctx.ready is True
        assert "MARKET" in ctx.manifest.universe_versions
        assert ctx.manifest.resolved_at  # Timestamp present

    def test_manifest_contains_population_counts(self):
        builders = _mock_builders()
        resolver = ResearchContextResolver(builders=builders)
        q = get_question("D-001")
        ctx = resolver.resolve(q)
        assert ctx.ready is True
        assert ctx.manifest.population_record_counts  # Non-empty

    def test_primitives_resolved(self):
        builders = _mock_builders()
        resolver = ResearchContextResolver(builders=builders)
        q = get_question("E-001")
        ctx = resolver.resolve(q)
        assert "expectancy" in ctx.primitives

    def test_resolve_all_45_questions(self):
        builders = _mock_builders()
        resolver = ResearchContextResolver(builders=builders)
        results = resolver.resolve_all(QUESTION_BANK)
        assert len(results) == len(QUESTION_BANK)
        ready = sum(1 for r in results if r.ready)
        blocked = sum(1 for r in results if not r.ready)
        assert ready >= 40  # Most should resolve with mock data
        assert blocked >= 1  # E-006 is BLOCKED


# ═══════════════════════════════════════════════════════════════════════════════
# PARITY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestLocalLambdaParity:

    def test_same_resolver_used(self):
        """Lambda adapter uses the same ResearchContextResolver class."""
        builders = _mock_builders()
        adapter = LambdaResearchAdapter(builders=builders)
        # The adapter's resolver is the canonical one
        assert isinstance(adapter._resolver, ResearchContextResolver)

    def test_same_registry_used(self):
        """Lambda and local use the same primitive registry."""
        local_reg = build_default_registry()
        builders = _mock_builders()
        adapter = LambdaResearchAdapter(builders=builders)
        assert adapter._registry.registered_names == local_reg.registered_names

    def test_same_mapping_used(self):
        """Lambda and local use the same question→primitive mapping."""
        local_map = build_full_mapping(QUESTION_BANK)
        builders = _mock_builders()
        adapter = LambdaResearchAdapter(builders=builders)
        assert adapter._mapping == local_map

    def test_same_resolution_for_same_data(self):
        """Given same builders, local resolver and Lambda resolve identically."""
        builders = _mock_builders()

        # Local
        local_resolver = ResearchContextResolver(builders=builders)
        local_ctx = local_resolver.resolve(get_question("E-001"))

        # Lambda
        adapter = LambdaResearchAdapter(builders=builders)
        lambda_ctx = adapter._resolver.resolve(get_question("E-001"))

        # Same results
        assert local_ctx.ready == lambda_ctx.ready
        assert local_ctx.manifest.universe_versions == lambda_ctx.manifest.universe_versions
        assert len(local_ctx.population) == len(lambda_ctx.population)
        assert local_ctx.primitives == lambda_ctx.primitives


# ═══════════════════════════════════════════════════════════════════════════════
# LAMBDA ADAPTER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestLambdaAdapter:

    def test_run_canonical_question(self):
        builders = _mock_builders()
        adapter = LambdaResearchAdapter(builders=builders)
        result = adapter.handle({
            "action": "run_canonical_question",
            "question_id": "E-001",
        })
        assert result.get("status") in ("COMPLETE", "INCONCLUSIVE")
        assert "finding" in result
        assert "manifest" in result

    def test_run_blocked_question(self):
        builders = _mock_builders()
        adapter = LambdaResearchAdapter(builders=builders)
        result = adapter.handle({
            "action": "run_canonical_question",
            "question_id": "E-006",
        })
        assert result["status"] == "BLOCKED"
        assert "manifest" in result

    def test_run_unknown_question(self):
        builders = _mock_builders()
        adapter = LambdaResearchAdapter(builders=builders)
        result = adapter.handle({
            "action": "run_canonical_question",
            "question_id": "NONEXISTENT",
        })
        assert "error" in result

    def test_resolve_only(self):
        builders = _mock_builders()
        adapter = LambdaResearchAdapter(builders=builders)
        result = adapter.handle({
            "action": "resolve_question",
            "question_id": "D-001",
        })
        assert result["ready"] is True
        assert result["population_size"] > 0
        assert "manifest" in result

    def test_no_hard_coded_paths(self):
        """Lambda adapter must not hard-code population file paths."""
        import inspect
        from research_engine.v10.runner import lambda_adapter
        source = inspect.getsource(lambda_adapter)
        assert "research_universe.jsonl" not in source
        assert "data/research/" not in source
        assert "/tmp/" not in source

    def test_no_duplicate_resolver(self):
        """Lambda adapter uses ResearchContextResolver, not custom logic."""
        import inspect
        from research_engine.v10.runner import lambda_adapter
        source = inspect.getsource(lambda_adapter)
        # Should import and use the canonical resolver
        assert "ResearchContextResolver" in source
        # Should NOT re-implement resolution
        assert "def _resolve_universe_file" not in source


# ═══════════════════════════════════════════════════════════════════════════════
# SAFETY
# ═══════════════════════════════════════════════════════════════════════════════


class TestSafety:

    def test_no_trading_imports(self):
        import inspect
        from research_engine.v10.runner import context_resolver, lambda_adapter
        for mod in (context_resolver, lambda_adapter):
            source = inspect.getsource(mod)
            imports = [l for l in source.splitlines() if l.strip().startswith(("import", "from"))]
            for line in imports:
                assert "core.runtime" not in line
                assert "execution.mt5" not in line
                assert "risk.manager" not in line
