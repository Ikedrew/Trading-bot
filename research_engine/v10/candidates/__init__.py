"""
V10 Candidate Registry.

Central management layer for optimisation candidates throughout their lifecycle.
Tracks creation, validation, evidence history, and final disposition.

Usage:
    from research_engine.v10.candidates import CandidateRegistry

    registry = CandidateRegistry()
    registry.create(candidate)
    registry.update_status("V10.1_STOP_ATR_2.0", "VALIDATING")
    registry.add_validation_result("V10.1_STOP_ATR_2.0", validation_run)
"""

from research_engine.v10.candidates.candidate_registry import CandidateRegistry
from research_engine.v10.candidates.models import CandidateRecord, CandidateStatus
from research_engine.v10.candidates.evaluation_report import CandidateEvaluationReport

__all__ = ["CandidateRegistry", "CandidateRecord", "CandidateStatus", "CandidateEvaluationReport"]
