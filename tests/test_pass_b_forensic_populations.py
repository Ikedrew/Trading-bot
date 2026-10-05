"""Pass B targeted verification (no full cycle; no re-baseline)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_engine.control_plane.pass_b_forensic_ledger import (
    CLASSIFICATION,
    FORENSIC_PROJECTION_ID,
    FORENSIC_SNAPSHOT_ID,
    FROZEN_GOVERNED_USABLE,
    ledger,
)
from research_engine.control_plane.stage4_ex2_l7_blocker_adjudication import (
    EX2_POPULATION,
)
from research_engine.control_plane.stage4_implementation_repairs import (
    CURRENT_EXACT_POPULATION_RETIRED,
    GOVERNED_USABLE,
    HISTORICAL_GOVERNED_USABLE,
    REPAIR_MODULE_VERSION,
    Stage4RepairError,
)
from research_engine.control_plane.stage4_impl_population2 import (
    enforce_exact_population,
)
from research_engine.experiments.architecture_assumption_validity import (
    validate_l3_report,
)
from research_engine.experiments.component_reward import (
    build_attribution_records,
)
from research_engine.experiments.lineage_coverage import classify_lineage
from research_engine.registry.research_question_registry import REGISTRY_BY_ID

ROOT = Path(__file__).resolve().parents[2]
PROJECTION = (
    ROOT / "data" / "research" / "continuous" / "projection" / "history"
    / f"{FORENSIC_PROJECTION_ID}.json"
)


def _projection_results() -> dict:
    payload = json.loads(PROJECTION.read_text(encoding="utf-8"))
    return {item["question_id"]: item for item in payload["canonical_questions"]}


def test_historical_authority_is_preserved_but_current_l3_g2_gates_are_retired():
    assert GOVERNED_USABLE == FROZEN_GOVERNED_USABLE
    assert HISTORICAL_GOVERNED_USABLE == FROZEN_GOVERNED_USABLE
    assert GOVERNED_USABLE["L3"] == 95
    assert GOVERNED_USABLE["G2"] == 261
    assert GOVERNED_USABLE["EX2"] == 8760
    assert REPAIR_MODULE_VERSION == "stage4_impl_repairs_v2"
    assert EX2_POPULATION == 8760
    assert CURRENT_EXACT_POPULATION_RETIRED == {"L3", "G2"}


def test_exact_population_gate_is_historical_only_for_l3_and_g2():
    with pytest.raises(Stage4RepairError, match="CURRENT_EXACT_POPULATION_RETIRED:G2"):
        enforce_exact_population("G2", [{}] * 384)
    with pytest.raises(Stage4RepairError):
        enforce_exact_population("EX2", [{}] * 13795)
    with pytest.raises(Stage4RepairError, match="CURRENT_EXACT_POPULATION_RETIRED:L3"):
        enforce_exact_population("L3", [{"a": 1}] * 95)
    with pytest.raises(Stage4RepairError, match="CURRENT_EXACT_POPULATION_RETIRED:G2"):
        enforce_exact_population("G2", [{"a": 1}] * 261)
