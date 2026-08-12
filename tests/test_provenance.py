"""
Tests for Evidence / Provenance / Versioning layer.

Covers:
- Provenance vocabulary (all types exist)
- Observed evidence creation and identification
- Derived evidence with source lineage
- Joined evidence with authoritative universe and join key
- Reconstructed evidence identification
- Counterfactual protection (cannot masquerade as observed)
- Provenance validation (invalid declarations detected)
- Evidence lineage for findings
- Evidence lineage for lifecycle traces
- Evidence lineage for metrics
- Version preservation (population_versions, universe_versions, trace_hash distinct)
- Backward compatibility (UNKNOWN for legacy data)
- Hash/version determinism
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_engine.v10.provenance.model import (
    ProvenanceType,
    EvidenceProvenance,
    SourceReference,
    SourceKind,
)
from research_engine.v10.provenance.validation import (
    ProvenanceValidator,
    ProvenanceValidationResult,
)
from research_engine.v10.provenance.lineage import EvidenceLineage


# ═══════════════════════════════════════════════════════════════════════════════
# PROVENANCE VOCABULARY
# ═══════════════════════════════════════════════════════════════════════════════


class TestProvenanceVocabulary:

    def test_all_types_exist(self):
        assert ProvenanceType.OBSERVED == "OBSERVED"
        assert ProvenanceType.DERIVED == "DERIVED"
        assert ProvenanceType.JOINED == "JOINED"
        assert ProvenanceType.RECONSTRUCTED == "RECONSTRUCTED"
        assert ProvenanceType.COUNTERFACTUAL == "COUNTERFACTUAL"
        assert ProvenanceType.UNKNOWN == "UNKNOWN"

    def test_type_count(self):
        assert len(ProvenanceType) == 6

    def test_source_kinds_exist(self):
        assert SourceKind.FILE == "FILE"
        assert SourceKind.UNIVERSE == "UNIVERSE"
        assert SourceKind.CALCULATION == "CALCULATION"
        assert SourceKind.ENRICHMENT == "ENRICHMENT"
        assert SourceKind.TRACE == "TRACE"


# ═══════════════════════════════════════════════════════════════════════════════
# OBSERVED EVIDENCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestObservedEvidence:

    def test_factory(self):
        p = EvidenceProvenance.observed("MARKET", version="abc123")
        assert p.provenance_type == "OBSERVED"
        assert p.authoritative_universe == "MARKET"
        assert p.source.version == "abc123"

    def test_to_dict(self):
        p = EvidenceProvenance.observed("DECISION")
        d = p.to_dict()
        assert d["provenance_type"] == "OBSERVED"
        assert d["authoritative_universe"] == "DECISION"


# ═══════════════════════════════════════════════════════════════════════════════
# DERIVED EVIDENCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestDerivedEvidence:

    def test_factory(self):
        p = EvidenceProvenance.derived(
            universe="OUTCOME",
            derived_from=["all_outcomes"],
            transformation="mean(r_multiple)",
            population_version="abc123",
        )
        assert p.provenance_type == "DERIVED"
        assert p.authoritative_universe == "OUTCOME"
        assert "all_outcomes" in p.derived_from
        assert p.transformation == "mean(r_multiple)"

    def test_to_dict(self):
        p = EvidenceProvenance.derived("OUTCOME", ["all_trades"], "mean(r_multiple)")
        d = p.to_dict()
        assert d["provenance_type"] == "DERIVED"
        assert "all_trades" in d["derived_from"]
        assert d["transformation"] == "mean(r_multiple)"


# ═══════════════════════════════════════════════════════════════════════════════
# JOINED EVIDENCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestJoinedEvidence:

    def test_factory(self):
        p = EvidenceProvenance.joined("OUTCOME", join_key="entity_id", source_version="xyz789")
        assert p.provenance_type == "JOINED"
        assert p.authoritative_universe == "OUTCOME"
        assert p.source.join_key == "entity_id"
        assert p.source.version == "xyz789"

    def test_preserves_ownership(self):
        p = EvidenceProvenance.joined("OUTCOME")
        assert p.authoritative_universe == "OUTCOME"
        # Even when viewed from Decision context, ownership remains OUTCOME

    def test_to_dict(self):
        p = EvidenceProvenance.joined("RISK", join_key="entity_id")
        d = p.to_dict()
        assert d["provenance_type"] == "JOINED"
        assert d["authoritative_universe"] == "RISK"
        assert d["source"]["join_key"] == "entity_id"


# ═══════════════════════════════════════════════════════════════════════════════
# RECONSTRUCTED EVIDENCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestReconstructedEvidence:

    def test_factory(self):
        p = EvidenceProvenance.reconstructed(["MARKET", "DECISION", "EXECUTION"])
        assert p.provenance_type == "RECONSTRUCTED"
        assert "MARKET" in p.derived_from

    def test_to_dict(self):
        p = EvidenceProvenance.reconstructed(["DECISION"])
        d = p.to_dict()
        assert d["provenance_type"] == "RECONSTRUCTED"


# ═══════════════════════════════════════════════════════════════════════════════
# COUNTERFACTUAL EVIDENCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestCounterfactualEvidence:

    def test_factory(self):
        p = EvidenceProvenance.counterfactual("zero slippage simulation")
        assert p.provenance_type == "COUNTERFACTUAL"
        assert "zero slippage" in p.transformation

    def test_is_distinct_from_observed(self):
        obs = EvidenceProvenance.observed("MARKET")
        cf = EvidenceProvenance.counterfactual()
        assert obs.provenance_type != cf.provenance_type


# ═══════════════════════════════════════════════════════════════════════════════
# UNKNOWN / LEGACY
# ═══════════════════════════════════════════════════════════════════════════════


class TestUnknownProvenance:

    def test_factory(self):
        p = EvidenceProvenance.unknown()
        assert p.provenance_type == "UNKNOWN"

    def test_backward_compatible(self):
        """Legacy data without provenance is representable."""
        p = EvidenceProvenance.unknown()
        d = p.to_dict()
        assert d["provenance_type"] == "UNKNOWN"


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestProvenanceValidation:

    def test_valid_observed(self):
        v = ProvenanceValidator()
        p = EvidenceProvenance.observed("MARKET")
        result = v.validate(p, "regime")
        assert result.valid

    def test_derived_without_source_is_error(self):
        v = ProvenanceValidator()
        p = EvidenceProvenance(
            provenance_type=ProvenanceType.DERIVED.value,
            derived_from=[],  # Missing source!
        )
        result = v.validate(p, "mean_r")
        assert not result.valid
        assert result.errors == 1

    def test_joined_without_universe_is_error(self):
        v = ProvenanceValidator()
        p = EvidenceProvenance(
            provenance_type=ProvenanceType.JOINED.value,
            authoritative_universe="",  # Missing!
            source=SourceReference(join_key="entity_id"),
        )
        result = v.validate(p, "r_multiple")
        assert not result.valid

    def test_joined_without_join_key_is_error(self):
        v = ProvenanceValidator()
        p = EvidenceProvenance(
            provenance_type=ProvenanceType.JOINED.value,
            authoritative_universe="OUTCOME",
            source=SourceReference(join_key=""),  # Missing!
        )
        result = v.validate(p, "r_multiple")
        assert not result.valid

    def test_unknown_is_info(self):
        v = ProvenanceValidator()
        p = EvidenceProvenance.unknown()
        result = v.validate(p, "legacy_field")
        assert result.valid  # INFO, not ERROR
        assert result.warnings == 0
        assert len(result.issues) == 1
        assert result.issues[0].severity == "INFO"

    def test_counterfactual_contamination(self):
        v = ProvenanceValidator()
        items = [
            ("regime", EvidenceProvenance.observed("MARKET")),
            ("simulated_r", EvidenceProvenance.counterfactual("no slippage")),
        ]
        issues = v.check_counterfactual_contamination(items)
        assert len(issues) == 1
        assert "COUNTERFACTUAL" in issues[0].message

    def test_no_contamination_when_separate(self):
        v = ProvenanceValidator()
        items = [
            ("regime", EvidenceProvenance.observed("MARKET")),
            ("score", EvidenceProvenance.observed("DECISION")),
        ]
        issues = v.check_counterfactual_contamination(items)
        assert len(issues) == 0

    def test_batch_validation(self):
        v = ProvenanceValidator()
        items = [
            ("regime", EvidenceProvenance.observed("MARKET")),
            ("bad_derived", EvidenceProvenance(
                provenance_type=ProvenanceType.DERIVED.value,
                derived_from=[],
            )),
        ]
        result = v.validate_batch(items)
        assert result.checked == 2
        assert result.errors == 1
        assert not result.valid


# ═══════════════════════════════════════════════════════════════════════════════
# EVIDENCE LINEAGE
# ═══════════════════════════════════════════════════════════════════════════════


class TestEvidenceLineage:

    def test_finding_lineage(self):
        lineage = EvidenceLineage.for_finding(
            question_id="E-001",
            run_id="run_123",
            universe_versions={"EXECUTION": "abc"},
            population_versions={"all_trades": "def"},
            populations_used=["all_trades"],
            universes_used=["EXECUTION"],
            analysis_method="expectancy",
            analysis_version="1.0.0",
        )
        assert lineage.artifact_type == "FINDING"
        assert lineage.run_id == "run_123"
        assert lineage.universe_versions["EXECUTION"] == "abc"
        assert lineage.population_versions["all_trades"] == "def"

    def test_lifecycle_trace_lineage(self):
        lineage = EvidenceLineage.for_lifecycle_trace(
            entity_id="e1",
            trace_hash="abcdef1234567890",
            universe_versions={"MARKET": "m1", "DECISION": "d1"},
            present_universes=["MARKET", "DECISION"],
        )
        assert lineage.artifact_type == "LIFECYCLE_TRACE"
        assert lineage.trace_hash == "abcdef1234567890"
        assert "trace" in lineage.evidence_provenance
        assert lineage.evidence_provenance["trace"].provenance_type == "RECONSTRUCTED"

    def test_metric_lineage(self):
        lineage = EvidenceLineage.for_metric(
            metric_name="mean_r",
            universe="OUTCOME",
            population="all_outcomes",
            population_version="xyz",
            transformation="mean(r_multiple)",
        )
        assert lineage.artifact_type == "METRIC"
        assert lineage.evidence_provenance["mean_r"].provenance_type == "DERIVED"
        assert lineage.evidence_provenance["mean_r"].transformation == "mean(r_multiple)"

    def test_lineage_to_dict(self):
        lineage = EvidenceLineage.for_finding(
            question_id="E-001",
            run_id="run_1",
            universe_versions={"EXECUTION": "a"},
            population_versions={"all_trades": "b"},
            populations_used=["all_trades"],
            universes_used=["EXECUTION"],
        )
        d = lineage.to_dict()
        assert "universe_versions" in d
        assert "population_versions" in d
        assert "run_id" in d
        assert "evidence_provenance" in d

    def test_versions_remain_distinct(self):
        """universe_versions, population_versions, trace_hash, run_id are all separate."""
        lineage = EvidenceLineage.for_lifecycle_trace(
            entity_id="e1",
            trace_hash="hash123",
            universe_versions={"MARKET": "uv1"},
            present_universes=["MARKET"],
        )
        d = lineage.to_dict()
        assert d["universe_versions"] != d["trace_hash"]
        assert d["trace_hash"] == "hash123"
        assert d["universe_versions"] == {"MARKET": "uv1"}


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "--tb=short", "-p", "no:conftest"]))
