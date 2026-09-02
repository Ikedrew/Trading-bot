"""Research-universe boundaries that must remain stable before enrichment."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _s3_fake import install_fake_s3, reset_fake_s3

from research_engine.v10.universes.contracts import UNIVERSE_CONTRACTS
from research_engine.v10.universes.models import (
    ACTIVE_UNIVERSES,
    RETIRED_UNIVERSES,
    Universe,
)
from research_engine.v10.universes.question_bank import (
    QUESTION_BANK,
    RETIRED_QUESTIONS,
)
from research_engine.v10.universes.shadow_outcome_universe import (
    ShadowOutcomeUniverseBuilder,
)


def test_shadow_outcome_is_active_and_contracted():
    assert Universe.SHADOW_OUTCOME in ACTIVE_UNIVERSES
    assert Universe.SHADOW_OUTCOME in UNIVERSE_CONTRACTS
    contract = UNIVERSE_CONTRACTS[Universe.SHADOW_OUTCOME]
    # Fresh Production V1 baseline: the shadow-outcome contract accepts the V1
    # shadow schema only — no v2/v3 schema compatibility is retained.
    assert contract.source_schema_versions == ("shadow_trades_v1",)
    assert "counterfactual" in contract.description.lower()


def test_shadow_reality_is_explicitly_retired():
    assert RETIRED_UNIVERSES == (Universe.SHADOW_REALITY,)
    assert Universe.SHADOW_REALITY not in ACTIVE_UNIVERSES
    assert Universe.SHADOW_REALITY not in UNIVERSE_CONTRACTS
    assert {q.question_id for q in RETIRED_QUESTIONS} == {
        "SR-001", "SR-002", "SR-003", "SR-004", "SR-005",
    }
    assert all(
        Universe.SHADOW_REALITY not in question.required_universes
        for question in QUESTION_BANK
    )


def test_shadow_runtime_stream_is_not_silently_reclassified():
    # Seed S3 shadow_trades with a runtime-stream record whose schema is NOT the
    # supported V1 shadow schema — it must be excluded, not silently reclassified.
    fake = install_fake_s3()
    try:
        fake.add("shadow_trades", [{
            "schema_version": "shadow_runtime_v1",
            "event_type": "CLOSE",
            "simulated_outcome": {"pnl_r_multiple": 1.0},
        }], symbol="EURUSD")
        builder = ShadowOutcomeUniverseBuilder()
        assert builder.build() == []
    finally:
        reset_fake_s3()
