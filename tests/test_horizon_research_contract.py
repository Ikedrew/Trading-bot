"""
Horizon Research Contract — Comprehensive tests.

Validates:
    1. All three horizons have research contracts
    2. Each contract has a version
    3. Execution state is unchanged (PERMITTED_HORIZONS)
    4. INTRADAY remains disabled
    5. EXTENDED remains disabled
    6. Research contracts serialize correctly (to_dict / from_dict)
    7. Version changes create separate identities
    8. Observations can be compared against expectations
    9. Comparison produces correct ValidationStatus
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from core.horizon.research_contract import (
    HorizonResearchContract,
    HorizonObservation,
    ContractAssessment,
    ValidationStatus,
    compare_contract_to_observation,
    get_active_contract,
    get_contract_by_version,
    RESEARCH_CONTRACTS,
    ACTIVE_CONTRACT_VERSION,
    SCALP_RESEARCH_V1,
    INTRADAY_RESEARCH_V1,
    EXTENDED_RESEARCH_V1,
)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. All Three Horizons Have Research Contracts
# ═══════════════════════════════════════════════════════════════════════════════

class TestContractsExist:
    def test_scalp_contract_exists(self):
        assert SCALP_RESEARCH_V1 is not None
        assert SCALP_RESEARCH_V1.horizon == "SCALP"

    def test_intraday_contract_exists(self):
        assert INTRADAY_RESEARCH_V1 is not None
        assert INTRADAY_RESEARCH_V1.horizon == "INTRADAY"

    def test_extended_contract_exists(self):
        assert EXTENDED_RESEARCH_V1 is not None
        assert EXTENDED_RESEARCH_V1.horizon == "EXTENDED"

    def test_registry_has_all_three(self):
        assert len(RESEARCH_CONTRACTS) == 3
        assert "SCALP_RESEARCH_V1" in RESEARCH_CONTRACTS
        assert "INTRADAY_RESEARCH_V1" in RESEARCH_CONTRACTS
        assert "EXTENDED_RESEARCH_V1" in RESEARCH_CONTRACTS

    def test_active_versions_for_all_horizons(self):
        assert "SCALP" in ACTIVE_CONTRACT_VERSION
        assert "INTRADAY" in ACTIVE_CONTRACT_VERSION
        assert "EXTENDED" in ACTIVE_CONTRACT_VERSION


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Each Contract Has A Version
# ═══════════════════════════════════════════════════════════════════════════════

class TestVersioning:
    def test_scalp_version(self):
        assert SCALP_RESEARCH_V1.profile_version == "SCALP_RESEARCH_V1"

    def test_intraday_version(self):
        assert INTRADAY_RESEARCH_V1.profile_version == "INTRADAY_RESEARCH_V1"

    def test_extended_version(self):
        assert EXTENDED_RESEARCH_V1.profile_version == "EXTENDED_RESEARCH_V1"

    def test_version_changes_create_separate_identity(self):
        """A V2 contract is a distinct object from V1."""
        v2 = HorizonResearchContract(
            horizon="SCALP",
            profile_version="SCALP_RESEARCH_V2",
            expected_rr=1.7,
            expected_hold_min_minutes=2.0,
            expected_hold_max_minutes=60.0,
            notes="V2: reduced RR expectation based on observed data.",
        )
        assert v2.profile_version != SCALP_RESEARCH_V1.profile_version
        assert v2.expected_rr != SCALP_RESEARCH_V1.expected_rr
        assert v2.horizon == SCALP_RESEARCH_V1.horizon

    def test_get_contract_by_version(self):
        result = get_contract_by_version("SCALP_RESEARCH_V1")
        assert result is SCALP_RESEARCH_V1

    def test_get_contract_by_version_unknown(self):
        result = get_contract_by_version("SCALP_RESEARCH_V99")
        assert result is None

    def test_get_active_contract(self):
        result = get_active_contract("SCALP")
        assert result is SCALP_RESEARCH_V1

    def test_get_active_contract_case_insensitive(self):
        result = get_active_contract("scalp")
        # get_active_contract uses .upper()
        assert result is SCALP_RESEARCH_V1


# ═══════════════════════════════════════════════════════════════════════════════
# 3 & 4 & 5. Execution State Unchanged
# ═══════════════════════════════════════════════════════════════════════════════

class TestExecutionUnchanged:
    def test_permitted_horizons_still_scalp_only(self):
        from core import config
        assert config.PERMITTED_HORIZONS == ["SCALP"]

    def test_intraday_not_permitted(self):
        from core.horizon.horizon_manager import get_horizon_manager, reset_horizon_manager
        reset_horizon_manager()
        mgr = get_horizon_manager()
        assert mgr.is_permitted("INTRADAY") is False
        reset_horizon_manager()

    def test_extended_not_permitted(self):
        from core.horizon.horizon_manager import get_horizon_manager, reset_horizon_manager
        reset_horizon_manager()
        mgr = get_horizon_manager()
        assert mgr.is_permitted("EXTENDED") is False
        reset_horizon_manager()

    def test_execution_authority_blocks_intraday(self):
        from core.horizon.execution_authority import HorizonExecutionAuthority
        auth = HorizonExecutionAuthority()
        result = auth.can_open(symbol="EURUSD", horizon="INTRADAY", current_positions=[])
        assert result.allowed is False

    def test_execution_authority_blocks_extended(self):
        from core.horizon.execution_authority import HorizonExecutionAuthority
        auth = HorizonExecutionAuthority()
        result = auth.can_open(symbol="EURUSD", horizon="EXTENDED", current_positions=[])
        assert result.allowed is False


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Serialization
# ═══════════════════════════════════════════════════════════════════════════════

class TestSerialization:
    def test_contract_to_dict(self):
        d = SCALP_RESEARCH_V1.to_dict()
        assert d["horizon"] == "SCALP"
        assert d["profile_version"] == "SCALP_RESEARCH_V1"
        assert d["expected_move"]["min_pips"] == 3.0
        assert d["expected_move"]["max_pips"] == 15.0
        assert d["expected_hold"]["min_minutes"] == 2.0
        assert d["expected_hold"]["max_minutes"] == 90.0
        assert d["expected_rr"] == 2.0
        assert d["expected_win_rate"] == 0.45

    def test_contract_from_dict_roundtrip(self):
        d = INTRADAY_RESEARCH_V1.to_dict()
        restored = HorizonResearchContract.from_dict(d)
        assert restored.horizon == "INTRADAY"
        assert restored.profile_version == "INTRADAY_RESEARCH_V1"
        assert restored.expected_rr == 3.0
        assert restored.expected_hold_max_minutes == 240.0

    def test_observation_to_dict(self):
        obs = HorizonObservation(
            horizon="SCALP",
            profile_version="SCALP_RESEARCH_V1",
            sample_size=50,
            observed_rr=1.8,
            observed_win_rate=0.44,
            observed_hold_average_minutes=42.0,
            exit_reasons={"tp_hit": 22, "sl_hit": 28},
        )
        d = obs.to_dict()
        assert d["horizon"] == "SCALP"
        assert d["sample_size"] == 50
        assert d["observed_rr"] == 1.8
        assert d["exit_reasons"] == {"tp_hit": 22, "sl_hit": 28}
        assert "generated_at" in d

    def test_observation_from_dict_roundtrip(self):
        obs = HorizonObservation(
            horizon="EXTENDED",
            profile_version="EXTENDED_RESEARCH_V1",
            sample_size=30,
            observed_rr=3.5,
            observed_hold_average_minutes=400.0,
            observed_mfe_pips=55.0,
        )
        d = obs.to_dict()
        restored = HorizonObservation.from_dict(d)
        assert restored.horizon == "EXTENDED"
        assert restored.sample_size == 30
        assert restored.observed_rr == 3.5
        assert restored.observed_hold_average_minutes == 400.0

    def test_assessment_to_dict(self):
        a = ContractAssessment(
            horizon="SCALP",
            profile_version="SCALP_RESEARCH_V1",
            field="rr",
            expected_min=1.4,
            expected_max=2.6,
            observed=1.8,
            status=ValidationStatus.VALIDATED,
        )
        d = a.to_dict()
        assert d["status"] == "VALIDATED"
        assert d["field"] == "rr"


# ═══════════════════════════════════════════════════════════════════════════════
# 7 & 8. Comparison: Observation vs Expectation
# ═══════════════════════════════════════════════════════════════════════════════

class TestComparison:
    def test_validated_when_within_range(self):
        """Observation within expected range → VALIDATED."""
        obs = HorizonObservation(
            horizon="SCALP",
            profile_version="SCALP_RESEARCH_V1",
            sample_size=50,
            observed_hold_average_minutes=42.0,
            observed_rr=1.9,
            observed_win_rate=0.44,
            observed_move_average_pips=8.0,
            observed_mae_pips=3.5,
            observed_mfe_pips=7.0,
        )
        results = compare_contract_to_observation(SCALP_RESEARCH_V1, obs)
        statuses = {r.field: r.status for r in results}
        assert statuses["hold_average_minutes"] == ValidationStatus.VALIDATED
        assert statuses["rr"] == ValidationStatus.VALIDATED
        assert statuses["win_rate"] == ValidationStatus.VALIDATED

    def test_review_required_when_outside_range(self):
        """Observation outside expected range → REVIEW_REQUIRED."""
        obs = HorizonObservation(
            horizon="SCALP",
            profile_version="SCALP_RESEARCH_V1",
            sample_size=50,
            observed_hold_average_minutes=150.0,  # Way above 90 max
            observed_rr=0.8,                      # Way below 2.0 expected
            observed_win_rate=0.20,               # Way below 0.45
            observed_move_average_pips=1.0,       # Below 3.0 min
            observed_mae_pips=10.0,               # Above 5.0 expected
            observed_mfe_pips=2.0,                # Below expected range
        )
        results = compare_contract_to_observation(SCALP_RESEARCH_V1, obs)
        statuses = {r.field: r.status for r in results}
        assert statuses["hold_average_minutes"] == ValidationStatus.REVIEW_REQUIRED
        assert statuses["rr"] == ValidationStatus.REVIEW_REQUIRED
        assert statuses["win_rate"] == ValidationStatus.REVIEW_REQUIRED

    def test_insufficient_data_below_min_sample(self):
        """Too few samples → INSUFFICIENT_DATA."""
        obs = HorizonObservation(
            horizon="SCALP",
            profile_version="SCALP_RESEARCH_V1",
            sample_size=5,  # Below default min of 20
        )
        results = compare_contract_to_observation(SCALP_RESEARCH_V1, obs)
        assert len(results) == 1
        assert results[0].status == ValidationStatus.INSUFFICIENT_DATA
        assert results[0].field == "sample_size"

    def test_custom_min_sample_size(self):
        """Custom minimum sample size respected."""
        obs = HorizonObservation(
            horizon="SCALP",
            profile_version="SCALP_RESEARCH_V1",
            sample_size=10,
            observed_hold_average_minutes=42.0,
            observed_rr=1.9,
        )
        # min=20 → insufficient
        results_strict = compare_contract_to_observation(SCALP_RESEARCH_V1, obs, min_sample_size=20)
        assert results_strict[0].status == ValidationStatus.INSUFFICIENT_DATA

        # min=5 → proceeds with comparison
        results_relaxed = compare_contract_to_observation(SCALP_RESEARCH_V1, obs, min_sample_size=5)
        assert any(r.field == "hold_average_minutes" for r in results_relaxed)

    def test_deviation_pct_calculated(self):
        """Deviation percentage shows how far outside the range."""
        obs = HorizonObservation(
            horizon="SCALP",
            profile_version="SCALP_RESEARCH_V1",
            sample_size=50,
            observed_hold_average_minutes=180.0,  # 90 above max of 90
            observed_rr=2.0,
            observed_win_rate=0.45,
        )
        results = compare_contract_to_observation(SCALP_RESEARCH_V1, obs)
        hold_result = next(r for r in results if r.field == "hold_average_minutes")
        assert hold_result.status == ValidationStatus.REVIEW_REQUIRED
        assert hold_result.deviation_pct > 0

    def test_intraday_comparison(self):
        """INTRADAY contract comparison works correctly."""
        obs = HorizonObservation(
            horizon="INTRADAY",
            profile_version="INTRADAY_RESEARCH_V1",
            sample_size=30,
            observed_hold_average_minutes=120.0,
            observed_rr=2.8,
            observed_win_rate=0.38,
            observed_move_average_pips=25.0,
            observed_mae_pips=10.0,
            observed_mfe_pips=20.0,
        )
        results = compare_contract_to_observation(INTRADAY_RESEARCH_V1, obs)
        statuses = {r.field: r.status for r in results}
        assert statuses["hold_average_minutes"] == ValidationStatus.VALIDATED
        assert statuses["rr"] == ValidationStatus.VALIDATED


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Contract Content Validation
# ═══════════════════════════════════════════════════════════════════════════════

class TestContractContent:
    def test_scalp_expectations_sensible(self):
        c = SCALP_RESEARCH_V1
        assert c.expected_hold_min_minutes < c.expected_hold_max_minutes
        assert c.expected_move_min_pips < c.expected_move_max_pips
        assert 0 < c.expected_win_rate < 1
        assert c.expected_rr > 0
        assert len(c.notes) > 0

    def test_intraday_expectations_sensible(self):
        c = INTRADAY_RESEARCH_V1
        assert c.expected_hold_max_minutes == 240.0
        assert c.expected_rr == 3.0
        assert c.expected_move_max_pips > SCALP_RESEARCH_V1.expected_move_max_pips

    def test_extended_expectations_sensible(self):
        c = EXTENDED_RESEARCH_V1
        assert c.expected_hold_max_minutes == 720.0
        assert c.expected_rr == 4.0
        assert c.expected_move_max_pips > INTRADAY_RESEARCH_V1.expected_move_max_pips

    def test_horizons_have_increasing_expectations(self):
        """Higher horizons expect larger moves, longer holds, higher RR."""
        assert SCALP_RESEARCH_V1.expected_rr < INTRADAY_RESEARCH_V1.expected_rr < EXTENDED_RESEARCH_V1.expected_rr
        assert SCALP_RESEARCH_V1.expected_hold_max_minutes < INTRADAY_RESEARCH_V1.expected_hold_max_minutes < EXTENDED_RESEARCH_V1.expected_hold_max_minutes
        assert SCALP_RESEARCH_V1.expected_move_max_pips < INTRADAY_RESEARCH_V1.expected_move_max_pips < EXTENDED_RESEARCH_V1.expected_move_max_pips
