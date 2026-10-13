"""Phase 2C provenance reuse: equivalence + forgery/multiplicity safety."""

from __future__ import annotations

from copy import deepcopy

import pytest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from research_engine.control_plane.evidence_provenance import (
    _canonical_json,
    attest_current_subset,
    evidence_digest,
    select_current_evidence,
)
from tests.test_repair_2b1_strategy_expectancy import _sufficient_population


def _epoch_rows(n: int = 8) -> list[dict]:
    return deepcopy(_sufficient_population()[:n])


def test_per_operation_reuse_matches_authoritative_digest_encoding():
    rows = _epoch_rows()
    sel = select_current_evidence("shadow_trades", rows)
    assert sel.component["digest"] == evidence_digest(sel.records)
    analysed = sel.records_for_analysis()
    att = attest_current_subset("shadow_trades", rows, analysed)
    assert att.component["digest"] == evidence_digest(att.records)


def test_duplicate_multiplicity_beyond_available_fails():
    rows = _epoch_rows(4)
    selected = rows + [deepcopy(rows[0])]
    with pytest.raises(ValueError):
        attest_current_subset("shadow_trades", rows, selected)


def test_forged_row_fails_subset_attestation():
    rows = _epoch_rows(4)
    selected = deepcopy(rows[:2])
    selected[0]["identity"]["canonical_opportunity_id"] = "forged-opp"
    with pytest.raises(ValueError):
        attest_current_subset("shadow_trades", rows, selected)


def test_changed_nested_field_fails_subset_attestation():
    rows = _epoch_rows(4)
    selected = deepcopy(rows[:2])
    selected[1]["simulated_outcome"]["pnl_r_multiple"] = 999.25
    with pytest.raises(ValueError):
        attest_current_subset("shadow_trades", rows, selected)


def test_stale_epoch_input_is_not_current():
    rows = _epoch_rows(4)
    rows[0]["schema_version"] = "shadow_trades_v0"
    sel = select_current_evidence("shadow_trades", rows)
    assert sel.component["records_used"] == len(rows) - 1
    with pytest.raises(ValueError):
        attest_current_subset("shadow_trades", rows, rows)


def test_mutating_caller_records_after_selection_does_not_change_attested_digest():
    rows = _epoch_rows(4)
    before = _canonical_json(rows[0])
    sel = select_current_evidence("shadow_trades", rows)
    digest_before = sel.component["digest"]
    rows[0]["simulated_outcome"]["pnl_r_multiple"] = -1234.5
    assert _canonical_json(rows[0]) != before
    assert sel.component["digest"] == digest_before
    # The mutated caller record must no longer verify as part of the
    # previously attested population.
    with pytest.raises(ValueError):
        attest_current_subset("shadow_trades", rows, list(sel.records))


def test_returned_records_are_isolated_from_selection():
    rows = _epoch_rows(4)
    sel = select_current_evidence("shadow_trades", rows)
    out = sel.records_for_analysis()
    out[0]["simulated_outcome"]["pnl_r_multiple"] = -777.0
    again = sel.records_for_analysis()
    assert again[0] != out[0]
    assert sel.component["digest"] == evidence_digest(again)
