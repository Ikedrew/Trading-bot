12"""Wave 6.1A — pure candidate baseline-impact classification tests.

Proves the deterministic classification contract between a frozen candidate
treatment_spec (Wave 5.3A canonical JSON) and a frozen deployed
treatment_spec, using exact Wave 4D Cartesian scope semantics. Pure only:
no stores, no baseline lookup, no lifecycle mutation, no persistence.
"""

from __future__ import annotations

import json

import pytest

from research_engine.lifecycle.candidate_impact_classifier import (
    CandidateImpactResult,
    ImpactClassification,
    classify_candidate_impact,
)


def _frozen_spec(treatment_id, *, change_type="direction_inversion",
                 symbols=None, patterns=None, extra=None):
    """Build a Wave 5.3A canonical frozen treatment_spec text."""
    declared = {} if change_type == "direction_inversion" else {"stop_multiplier": 2.0}
    value = {
        "change_type": change_type,
        "declared": declared,
        # Wave 5.3A authority: scope members are sorted, unique, non-empty.
        "scope": {"symbols": sorted(symbols) if symbols else None,
                  "patterns": sorted(patterns) if patterns else None},
        "treatment_id": treatment_id,
    }
    if extra:
        value.update(extra)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _classify(cand_spec, dep_spec, *, cand_id="T-C", dep_id="T-D"):
    return classify_candidate_impact(cand_id, cand_spec, dep_id, dep_spec)


# ─── 1. Proven disjoint symbols → UNAFFECTED ─────────────────────────────────

def test_disjoint_symbols_unaffected():
    result = _classify(
        _frozen_spec("T-C", symbols=["GBPUSD"]),
        _frozen_spec("T-D", symbols=["EURUSD"]),
    )
    assert result.classification is ImpactClassification.UNAFFECTED
    assert "DISJOINT_SYMBOL_SCOPE" in result.reason_codes


# ─── 2. Proven disjoint patterns → UNAFFECTED ────────────────────────────────

def test_disjoint_patterns_unaffected():
    result = _classify(
        _frozen_spec("T-C", symbols=["EURUSD"], patterns=["Hammer"]),
        _frozen_spec("T-D", symbols=["EURUSD"], patterns=["Evening Star"]),
    )
    assert result.classification is ImpactClassification.UNAFFECTED
    assert "DISJOINT_PATTERN_SCOPE" in result.reason_codes
    assert "DISJOINT_SYMBOL_SCOPE" not in result.reason_codes


# ─── 3. Exact overlap → MATERIALLY_AFFECTED ──────────────────────────────────

def test_exact_overlap_material():
    result = _classify(
        _frozen_spec("T-C", symbols=["EURUSD"], patterns=["Hammer"]),
        _frozen_spec("T-D", symbols=["EURUSD"], patterns=["Hammer"]),
    )
    assert result.classification is ImpactClassification.MATERIALLY_AFFECTED
    assert "FULL_SCOPE_EXPOSURE" in result.reason_codes


# ─── 4. Candidate narrow / deployed broad → MATERIALLY_AFFECTED ─────────────

def test_candidate_narrow_deployed_broad_material():
    result = _classify(
        _frozen_spec("T-C", symbols=["EURUSD"]),
        _frozen_spec("T-D"),
    )
    assert result.classification is ImpactClassification.MATERIALLY_AFFECTED
    assert "BROAD_DEPLOYED_SCOPE" in result.reason_codes
    assert "FULL_SCOPE_EXPOSURE" in result.reason_codes


# ─── 5. Candidate broad / deployed narrow → NEVER UNAFFECTED ────────────────

@pytest.mark.parametrize("dep_symbols,dep_patterns", [
    (["EURUSD"], None),
    (["EURUSD"], ["Hammer"]),
    (None, ["Hammer"]),
    (None, None),
])
def test_candidate_broad_deployed_narrow_never_unaffected(dep_symbols, dep_patterns):
    result = _classify(
        _frozen_spec("T-C"),
        _frozen_spec("T-D", symbols=dep_symbols, patterns=dep_patterns),
    )
    assert result.classification is not ImpactClassification.UNAFFECTED
    assert result.classification is ImpactClassification.MATERIALLY_AFFECTED
    assert "BROAD_CANDIDATE_SCOPE" in result.reason_codes


def test_candidate_broad_deployed_broad_material():
    result = _classify(_frozen_spec("T-C"), _frozen_spec("T-D"))
    assert result.classification is ImpactClassification.MATERIALLY_AFFECTED
    assert "BROAD_CANDIDATE_SCOPE" in result.reason_codes
    assert "BROAD_DEPLOYED_SCOPE" in result.reason_codes


# ─── 6. Partial symbol scope overlap ─────────────────────────────────────────

def test_partial_symbol_scope_overlap():
    result = _classify(
        _frozen_spec("T-C", symbols=["EURUSD", "GBPUSD"]),
        _frozen_spec("T-D", symbols=["EURUSD"]),
    )
    assert result.classification is ImpactClassification.PARTIALLY_AFFECTED
    assert "PARTIAL_SCOPE_OVERLAP" in result.reason_codes
    assert "DISJOINT_SYMBOL_SCOPE" not in result.reason_codes


# ─── 7. Partial pattern scope overlap ────────────────────────────────────────

def test_partial_pattern_scope_overlap():
    result = _classify(
        _frozen_spec("T-C", patterns=["Hammer", "Engulfing"]),
        _frozen_spec("T-D", patterns=["Hammer"]),
    )
    assert result.classification is ImpactClassification.PARTIALLY_AFFECTED
    assert "PARTIAL_SCOPE_OVERLAP" in result.reason_codes


# ─── 8. Cartesian symbol AND pattern partial overlap ────────────────────────

@pytest.mark.parametrize("dep_symbols,dep_patterns", [
    (["EURUSD"], ["Hammer"]),
    (["EURUSD"], ["Engulfing"]),
    (["GBPUSD"], ["Hammer"]),
    (["GBPUSD"], ["Engulfing"]),
])
def test_cartesian_partial_overlap(dep_symbols, dep_patterns):
    # Every single deployed (symbol, pattern) cell is one of several cells
    # of the candidate scope → PARTIAL, per Cartesian AND semantics.
    result = _classify(
        _frozen_spec("T-C", symbols=["EURUSD", "GBPUSD"],
                     patterns=["Hammer", "Engulfing"]),
        _frozen_spec("T-D", symbols=dep_symbols, patterns=dep_patterns),
    )
    assert result.classification is ImpactClassification.PARTIALLY_AFFECTED
    assert "PARTIAL_SCOPE_OVERLAP" in result.reason_codes


def test_cartesian_subset_in_each_dimension_is_still_partial():
    # Deployed covers {EURUSD} x {Hammer, Engulfing}: each candidate
    # DIMENSION is fully covered, but the Cartesian population is not
    # (GBPUSD cells untouched) → conservative PARTIAL, never FULL exposure.
    result = _classify(
        _frozen_spec("T-C", symbols=["EURUSD", "GBPUSD"],
                     patterns=["Hammer", "Engulfing"]),
        _frozen_spec("T-D", symbols=["EURUSD"],
                     patterns=["Hammer", "Engulfing"]),
    )
    assert result.classification is ImpactClassification.PARTIALLY_AFFECTED


# ─── 9. One disjoint dimension decides UNAFFECTED (Cartesian AND) ────────────

def test_one_disjoint_dimension_unaffected_despite_symbol_overlap():
    result = _classify(
        _frozen_spec("T-C", symbols=["EURUSD", "GBPUSD"],
                     patterns=["Hammer", "Engulfing"]),
        _frozen_spec("T-D", symbols=["EURUSD"], patterns=["Evening Star"]),
    )
    assert result.classification is ImpactClassification.UNAFFECTED
    assert "DISJOINT_PATTERN_SCOPE" in result.reason_codes
    assert "DISJOINT_SYMBOL_SCOPE" not in result.reason_codes


# ─── 10. Treatment type is recorded, never an independence proof ─────────────

def test_different_treatment_types_overlapping_scope_not_unaffected():
    result = _classify(
        _frozen_spec("T-C", change_type="direction_inversion",
                     symbols=["EURUSD"], patterns=["Hammer"]),
        _frozen_spec("T-D", change_type="geometry_modification",
                     symbols=["EURUSD"], patterns=["Hammer"]),
    )
    assert result.classification is ImpactClassification.MATERIALLY_AFFECTED
    assert "DIFFERENT_TREATMENT_TYPE" in result.reason_codes
    assert result.candidate_treatment_type == "direction_inversion"
    assert result.deployed_treatment_type == "geometry_modification"


def test_same_treatment_type_recorded():
    result = _classify(
        _frozen_spec("T-C", symbols=["GBPUSD"]),
        _frozen_spec("T-D", symbols=["EURUSD"]),
    )
    assert result.classification is ImpactClassification.UNAFFECTED
    assert "SAME_TREATMENT_TYPE" in result.reason_codes
    assert result.candidate_treatment_id == "T-C"
    assert result.deployed_treatment_id == "T-D"


# ─── 11. Malformed CANDIDATE provenance fails closed ─────────────────────────

@pytest.mark.parametrize("cand_spec,cand_id", [
    ("not-json", "T-C"),
    (None, "T-C"),
    ("", "T-C"),
    (_frozen_spec("OTHER"), "T-C"),          # treatment_id mismatch
    (_frozen_spec("T-C", extra={"symbol": "EURUSD"}), "T-C"),  # stray key
    (json.dumps({"change_type": "direction_inversion", "declared": {},
                 "scope": {"symbols": ["EURUSD"], "patterns": None},
                 "treatment_id": "T-C"}), "T-C"),  # non-canonical JSON text
    (json.dumps({"change_type": "direction_inversion", "declared": {},
                 "scope": {"symbols": ["EURUSD", "EURUSD"], "patterns": None},
                 "treatment_id": "T-C"}), "T-C"),  # duplicated scope members
])
def test_malformed_candidate_provenance_fails_closed(cand_spec, cand_id):
    result = _classify(cand_spec, _frozen_spec("T-D"), cand_id=cand_id)
    assert result.classification is ImpactClassification.INDETERMINATE
    assert result.reason_codes == ("MALFORMED_PROVENANCE",)
    assert result.candidate_scope is None
    assert result.deployed_scope is None


# ─── 12. Malformed DEPLOYED provenance fails closed ──────────────────────────

@pytest.mark.parametrize("dep_spec,dep_id", [
    ("not-json", "T-D"),
    (None, "T-D"),
    (None, ""),                              # missing treatment_id
    (_frozen_spec("OTHER"), "T-D"),          # treatment_id mismatch
    (json.dumps({"change_type": "direction_inversion", "declared": {},
                 "scope": {"symbols": None, "patterns": []},
                 "treatment_id": "T-D"},
                sort_keys=True, separators=(",", ":")), "T-D"),  # empty list
])
def test_malformed_deployed_provenance_fails_closed(dep_spec, dep_id):
    result = _classify(_frozen_spec("T-C"), dep_spec, dep_id=dep_id)
    assert result.classification is ImpactClassification.INDETERMINATE
    assert result.reason_codes == ("MALFORMED_PROVENANCE",)
    assert result.candidate_scope is None
    assert result.deployed_scope is None


# ─── 13. Deterministic: identical input → identical output ───────────────────

def test_deterministic_identical_output():
    cand = _frozen_spec("T-C", symbols=["EURUSD", "GBPUSD"],
                        patterns=["Hammer", "Engulfing"])
    dep = _frozen_spec("T-D", symbols=["EURUSD"], patterns=["Hammer"])
    first = _classify(cand, dep)
    second = _classify(cand, dep)
    assert isinstance(first, CandidateImpactResult)
    assert first == second
    assert first.reason_codes == second.reason_codes
    assert first.candidate_scope == second.candidate_scope
    assert first.deployed_scope == second.deployed_scope

