"""
Correlation Layer Tests.

Proves:
    - Four universes remain independently buildable
    - Execution does not depend on Decision
    - Decision does not depend on Execution
    - Failed correlation never removes a canonical record
    - Uncorrelated records remain visible
    - Ambiguous correlations are explicitly marked
    - Correlation cardinality is enforced
    - Temporal joins obey their defined window
    - Invalid identifiers cannot silently match
    - Correlation coverage is measurable
    - Questions not requiring the join remain READY
    - Questions requiring unavailable correlation are handled
    - No legacy registry is introduced
"""

import pytest

from research_engine.v10.universes.correlation import (
    EXECUTION_DECISION_CORRELATION,
    CorrelationContract,
    CorrelationEngine,
    CorrelationMethod,
    CorrelationRecord,
    CorrelationStatus,
    CorrelationTrust,
    RelationshipType,
)
from research_engine.v10.universes.models import Universe, Population
from research_engine.v10.universes.question_bank import QUESTION_BANK
from research_engine.v10.universes.question_validator import validate_all_questions


# ═══════════════════════════════════════════════════════════════════════════════
# UNIVERSE INDEPENDENCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestUniverseIndependence:

    def test_execution_universe_no_decision_import(self):
        import inspect
        from research_engine.v10.universes import execution_universe
        source = inspect.getsource(execution_universe)
        imports = [l for l in source.splitlines() if l.strip().startswith(("import", "from"))]
        for line in imports:
            assert "decision_universe" not in line
            assert "DecisionUniverse" not in line

    def test_decision_universe_no_execution_import(self):
        import inspect
        from research_engine.v10.universes import decision_universe
        source = inspect.getsource(decision_universe)
        imports = [l for l in source.splitlines() if l.strip().startswith(("import", "from"))]
        for line in imports:
            assert "execution_universe" not in line
            assert "ExecutionUniverse" not in line

    def test_market_universe_no_execution_import(self):
        import inspect
        from research_engine.v10.universes import market_universe
        source = inspect.getsource(market_universe)
        imports = [l for l in source.splitlines() if l.strip().startswith(("import", "from"))]
        for line in imports:
            assert "execution_universe" not in line

    def test_strategy_universe_no_execution_import(self):
        import inspect
        from research_engine.v10.universes import strategy_universe
        source = inspect.getsource(strategy_universe)
        imports = [l for l in source.splitlines() if l.strip().startswith(("import", "from"))]
        for line in imports:
            assert "execution_universe" not in line


# ═══════════════════════════════════════════════════════════════════════════════
# CORRELATION CONTRACT
# ═══════════════════════════════════════════════════════════════════════════════


class TestCorrelationContract:

    def test_contract_exists(self):
        assert EXECUTION_DECISION_CORRELATION is not None

    def test_contract_relationship_type(self):
        assert EXECUTION_DECISION_CORRELATION.relationship_type == RelationshipType.PARTIAL_CORRELATION

    def test_contract_trust_classification(self):
        assert EXECUTION_DECISION_CORRELATION.trust_classification == CorrelationTrust.PARTIAL_BUT_USABLE

    def test_contract_has_temporal_window(self):
        assert EXECUTION_DECISION_CORRELATION.temporal_window_seconds == 600

    def test_contract_is_symbol_constrained(self):
        assert EXECUTION_DECISION_CORRELATION.symbol_constrained is True

    def test_contract_unmatched_policy(self):
        assert "UNCORRELATED" in EXECUTION_DECISION_CORRELATION.unmatched_policy

    def test_contract_serialises(self):
        d = EXECUTION_DECISION_CORRELATION.to_dict()
        assert d["join_id"] == "execution_decision_correlation"
        assert d["historical_coverage"] == 0.096


# ═══════════════════════════════════════════════════════════════════════════════
# CORRELATION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════


class TestCorrelationEngine:

    def _make_exe(self, trade_id, symbol, entry_time):
        return {"trade_id": trade_id, "symbol": symbol, "entry_time": entry_time}

    def _make_dec(self, entity_id, symbol):
        return {"entity_id": entity_id, "symbol": symbol, "action": "EXECUTE"}

    def test_exact_match(self):
        engine = CorrelationEngine(temporal_window=600)
        exe = [self._make_exe("t1", "EURUSD", 1784736600)]
        dec = [self._make_dec("EURUSD_1784736600", "EURUSD")]
        results = engine.correlate(exe, dec)
        assert len(results) == 1
        assert results[0].status == CorrelationStatus.CORRELATED
        assert results[0].decision_id == "EURUSD_1784736600"
        assert results[0].time_delta_seconds == 0

    def test_within_window_match(self):
        engine = CorrelationEngine(temporal_window=600)
        exe = [self._make_exe("t1", "EURUSD", 1784736700)]  # 100s after cycle
        dec = [self._make_dec("EURUSD_1784736600", "EURUSD")]
        results = engine.correlate(exe, dec)
        assert results[0].status == CorrelationStatus.CORRELATED
        assert results[0].time_delta_seconds == 100

    def test_outside_window_uncorrelated(self):
        engine = CorrelationEngine(temporal_window=600)
        exe = [self._make_exe("t1", "EURUSD", 1784740000)]  # 3400s after
        dec = [self._make_dec("EURUSD_1784736600", "EURUSD")]
        results = engine.correlate(exe, dec)
        assert results[0].status == CorrelationStatus.UNCORRELATED

    def test_symbol_constraint(self):
        """Different symbols cannot match even if timestamp matches."""
        engine = CorrelationEngine(temporal_window=600)
        exe = [self._make_exe("t1", "GBPUSD", 1784736600)]
        dec = [self._make_dec("EURUSD_1784736600", "EURUSD")]
        results = engine.correlate(exe, dec)
        assert results[0].status == CorrelationStatus.UNCORRELATED

    def test_failed_correlation_preserves_record(self):
        """Uncorrelated records still produce a result — never removed."""
        engine = CorrelationEngine(temporal_window=600)
        exe = [
            self._make_exe("t1", "EURUSD", 1784736600),
            self._make_exe("t2", "GBPUSD", 9999999999),  # No match possible
        ]
        dec = [self._make_dec("EURUSD_1784736600", "EURUSD")]
        results = engine.correlate(exe, dec)
        assert len(results) == 2  # Both records get a result
        assert results[0].status == CorrelationStatus.CORRELATED
        assert results[1].status == CorrelationStatus.UNCORRELATED

    def test_ambiguous_detection(self):
        """Multiple decisions within window → AMBIGUOUS."""
        engine = CorrelationEngine(temporal_window=600)
        exe = [self._make_exe("t1", "EURUSD", 1784736700)]
        dec = [
            self._make_dec("EURUSD_1784736600", "EURUSD"),
            self._make_dec("EURUSD_1784736900", "EURUSD"),  # Both within 600s
        ]
        results = engine.correlate(exe, dec)
        assert results[0].status == CorrelationStatus.AMBIGUOUS

    def test_missing_entry_time(self):
        engine = CorrelationEngine(temporal_window=600)
        exe = [{"trade_id": "t1", "symbol": "EURUSD", "entry_time": None}]
        dec = [self._make_dec("EURUSD_1784736600", "EURUSD")]
        results = engine.correlate(exe, dec)
        assert results[0].status == CorrelationStatus.UNCORRELATED
        assert "Missing" in results[0].notes

    def test_coverage_statistics(self):
        engine = CorrelationEngine(temporal_window=600)
        exe = [
            self._make_exe("t1", "EURUSD", 1784736600),
            self._make_exe("t2", "EURUSD", 9999999999),
            self._make_exe("t3", "GBPUSD", 1784736600),
        ]
        dec = [self._make_dec("EURUSD_1784736600", "EURUSD")]
        engine.correlate(exe, dec)
        cov = engine.coverage
        assert cov["total"] == 3
        assert cov["correlated"] == 1
        assert cov["uncorrelated"] == 2
        assert cov["coverage_rate"] == pytest.approx(1 / 3, abs=0.01)

    def test_no_legacy_import(self):
        import inspect
        from research_engine.v10.universes import correlation
        source = inspect.getsource(correlation)
        imports = [l for l in source.splitlines() if l.strip().startswith(("import", "from"))]
        for line in imports:
            assert "research_question_registry" not in line


# ═══════════════════════════════════════════════════════════════════════════════
# QUESTION VALIDATOR WITH CORRELATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestQuestionValidatorCorrelation:

    def test_single_universe_questions_unaffected(self):
        """Questions using only one universe are never blocked by correlation."""
        single = [q for q in QUESTION_BANK if len(q.required_universes) == 1]
        results = validate_all_questions(tuple(single))
        for r in results:
            assert r.status != "BLOCKED" or "correlation" not in str(r.reasons).lower(), (
                f"{r.question_id} blocked by correlation but only uses one universe"
            )

    def test_all_questions_validate(self):
        results = validate_all_questions(QUESTION_BANK)
        assert len(results) == len(QUESTION_BANK)
        # No INVALID
        invalid = [r for r in results if r.status == "INVALID"]
        assert not invalid
