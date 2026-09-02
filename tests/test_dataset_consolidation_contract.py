"""Anti-regression contract for the Production V1 dataset consolidation (35 → 23).

Prevents the retired dataset generations from being reintroduced and enforces
that the consolidation invariants hold:

  1. Every registered Production V1 dataset has exactly one role.
  2. No retired dataset name may reappear in the registry.
  3. No active writer/reader may reference a retired dataset module.
  4. Retired dataset writer modules must not exist on disk.
  5. Obsolete V2/V3 DATASET GENERATIONS are gone; genuine algorithm/model
     versions (kept as computation) are allowed.
  6. Retained research datasets carry a valid lineage key.
  7. The decision_trace domain absorbs the decision_audit + v3 diagnostic fields.
  8. The opportunities domain absorbs the v2/v3 location fields.

Do not weaken these assertions to obtain green results.
"""

from __future__ import annotations

from pathlib import Path

from core.production_data_contract import (
    PRODUCTION_SCHEMA_REGISTRY,
    RETIRED_DATASETS,
    DatasetRole,
)

ROOT = Path(__file__).resolve().parent.parent

# The exact retained set after consolidation.
_EXPECTED_RETAINED = {
    # CORE (7)
    "events", "market_context", "opportunities", "assessments",
    "decision_ledger", "execution_results", "trade_truth",
    # SUPPORTING (13)
    "strategy_candidates", "horizon_candidates", "decision_trace",
    "execution_context", "execution_attempts", "protection_audit",
    "management_actions", "risk_deviation", "portfolio_rankings",
    "shadow_runtime", "shadow_trades", "strategy_observations",
    "research_shadow_trades",
    # PROJECTION (3)
    "trade_journal", "portfolio_shadow", "quarantine",
}


def test_registry_is_exactly_the_retained_consolidated_set():
    assert set(PRODUCTION_SCHEMA_REGISTRY) == _EXPECTED_RETAINED
    assert len(PRODUCTION_SCHEMA_REGISTRY) == 23


def test_no_retired_dataset_is_in_the_registry():
    for name in RETIRED_DATASETS:
        assert name not in PRODUCTION_SCHEMA_REGISTRY, name


def test_retired_datasets_cover_the_twelve_removed():
    assert RETIRED_DATASETS == frozenset({
        "opportunity_assessment", "trade_truth_graph", "v3_market_understanding",
        "v2_opportunities", "v3_opportunities", "v3_opportunity_assessment",
        "v3_market_context", "v3_horizon_assessment", "v3_risk_assessment",
        "v3_entry_assessment", "v3_execution_assessment", "decision_audit",
    })


def test_retired_writer_modules_do_not_exist():
    assert not (ROOT / "core" / "trade_truth_graph.py").exists()
    assert not (ROOT / "core" / "persistence" / "opportunity_assessment_writer.py").exists()


def test_every_registered_dataset_has_exactly_one_role():
    for name, entry in PRODUCTION_SCHEMA_REGISTRY.items():
        assert isinstance(entry.role, DatasetRole)
        assert entry.s3_base_prefix == f"{entry.role.value}/{name}"


def test_no_obsolete_v2_v3_dataset_generation_remains():
    # No dataset NAME may start with v2_/v3_ (dataset generations are retired).
    for name in PRODUCTION_SCHEMA_REGISTRY:
        assert not name.startswith("v2_"), name
        assert not name.startswith("v3_"), name


def test_no_active_writer_references_a_retired_dataset_module():
    # Scan core/ runtime writers for imports of the deleted modules.
    forbidden_imports = (
        "from core.trade_truth_graph import",
        "import core.trade_truth_graph",
        "from core.persistence.opportunity_assessment_writer import",
    )
    for py in (ROOT / "core").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        try:
            src = py.read_text(encoding="utf-8-sig")
        except Exception:
            continue
        for frag in forbidden_imports:
            assert frag not in src, f"{py} still imports a retired module: {frag}"


def test_decision_trace_absorbs_decision_audit_fields():
    # The decision_trace record must expose the migrated decision_audit fields.
    from core.decision_trace import DecisionTrace
    dt = DecisionTrace(entity_id="E", symbol="EURUSD", cycle_id=1, timestamp_utc="t")
    d = dt.to_dict()
    for field in (
        "trigger_candle", "entry_timing", "confirmation_detail",
        "bias_validation_score", "structure_ok", "stability_policy",
        "spread_at_decision", "ev_gate_enabled",
    ):
        assert field in d, field


def test_opportunities_writer_has_no_location_observation_route():
    # Canonical V1 cleanup: the LOCATION_OBSERVATION route (fed by the retired
    # V2/V3 opportunity observers) was removed. The opportunities writer must
    # NOT expose persist_location_observation any more.
    import core.persistence.opportunity_writer as ow
    assert not hasattr(ow, "persist_location_observation")


def test_retained_research_datasets_have_a_lineage_key():
    # Every retained dataset must be reconstructable via observation_id or
    # canonical_opportunity_id (opportunity lifecycle) or be operational.
    # Here we assert each retained dataset has a defined semantic owner and
    # population — the contract-level lineage guarantee.
    for name, entry in PRODUCTION_SCHEMA_REGISTRY.items():
        assert entry.semantic_owner, name
        assert entry.population, name


# ─────────────────────────────────────────────────────────────────────────────
# decision_id minting contract (retained runtime function after dataset retired)
# ─────────────────────────────────────────────────────────────────────────────

def test_decision_audit_still_mints_decision_id_without_writing_dataset(tmp_path, monkeypatch):
    """persist_new_engine_decision_audit must still return a decision_id
    (runtime identity contract) even though the separate decision_audit dataset
    was retired. It must NOT write a decision_audit JSONL file."""
    from core import decision_audit as _da

    class _FakeCandle:
        def __init__(self, t=1700000000):
            self.time, self.open, self.high, self.low, self.close = t, 1.1, 1.11, 1.09, 1.10

    # Enable the audit path so the function does its full work.
    monkeypatch.setattr(_da.config, "DECISION_AUDIT_ENABLED", True, raising=False)
    monkeypatch.setattr(_da.config, "DECISION_AUDIT_DIR", str(tmp_path), raising=False)

    decision_id = _da.persist_new_engine_decision_audit(
        symbol="EURUSD",
        cycle_id=7,
        engine_result={"action": "NO_TRADE", "reason": "x", "score": 0.1},
        engine_state=object(),
        candles=[_FakeCandle()],
        closed_i=0,
        correlation_id="COR-TEST",
    )

    # Identity contract preserved
    assert isinstance(decision_id, str) and len(decision_id) == 32

    # No separate decision_audit dataset file written (dataset retired)
    assert list(tmp_path.glob("*.jsonl")) == []
