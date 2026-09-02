"""Research-universe boundaries that must remain stable before enrichment."""

from pathlib import Path

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


def test_shadow_runtime_stream_is_not_silently_reclassified(tmp_path: Path):
    runtime_dir = tmp_path / "shadow_runtime_v1"
    runtime_dir.mkdir()
    (runtime_dir / "EURUSD.jsonl").write_text(
        '{"schema_version":"shadow_runtime_v1","event_type":"CLOSE",'
        '"simulated_outcome":{"pnl_r_multiple":1.0}}\n',
        encoding="utf-8",
    )
    builder = ShadowOutcomeUniverseBuilder(source_dir=runtime_dir)
    assert builder.build() == []
