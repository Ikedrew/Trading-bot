"""
Canonical Candidate Recommendation - Durable, evidence-attributed recommendation record.

Wave 4E.1: CandidateEvaluation -> canonical recommendation with exact evidence attribution.

A recommendation is NOT an optimisation and NOT permission to modify production.
It is a durable interpretation of a completed candidate evaluation.

The required chain:
    candidate -> exact treatment -> exact baseline -> exact paired evidence
    -> evaluation -> recommendation -> later human decision

A recommendation MUST NOT:
    * mark candidate ACCEPTED
    * invoke record_human_decision automatically
    * create fake approval
    * modify production
    * write an application record
    * change active baseline
    * mutate candidate configuration

Identity: REC-{evaluation_id} (deterministic). Store is append-only.
Fail-closed: missing candidate_id -> no record.
Everything else -> NON-ACTIONABLE record preserving evaluation truth.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_engine.lifecycle.candidate_evaluator import CandidateEvaluation

logger = logging.getLogger(__name__)

_RECOMMENDATIONS_DIR = 'data/research/lifecycle/recommendations'

@dataclass
class CandidateRecommendation:
    """One durable, evidence-attributed recommendation record."""

    recommendation_id: str = ""
    evaluation_id: str = ""
    candidate_id: str = ""
    treatment_id: str = ""
    baseline_id: str = ""
    baseline_config_hash: str = ""
    eligible_pairs: int = 0
    total_observations_raw: int = 0
    excluded_unpaired: int = 0
    excluded_pre_boundary: int = 0

    decision: str = ''
    decision_reason: str = ''
    mean_delta_r: float = 0.0
    mean_baseline_r: float = 0.0
    mean_candidate_r: float = 0.0
    candidate_win_rate: float = 0.0
    ci_lower: float | None = None
    ci_upper: float | None = None
    permutation_p: float | None = None
    oos_n: int = 0
    oos_delta_r: float = 0.0
    confidence: str = ''
    risk_level: str = ''
    symbols_positive: int = 0
    symbols_total: int = 0
    periods_positive: int = 0
    periods_total: int = 0
    survives_outlier_removal: bool = False
    worst_delta_r: float = 0.0
    promotion_blocked: bool = False
    promotion_block_reason: str = ''
    actionable: bool = False
    limitations: list[str] = field(default_factory=list)
    created_at: str = ''

    def to_dict(self) -> dict[str, Any]:
        return {
            'recommendation_id': self.recommendation_id,
            'evaluation_id': self.evaluation_id,
            'candidate_id': self.candidate_id,
            'treatment_id': self.treatment_id,
            'baseline_id': self.baseline_id,
            'baseline_config_hash': self.baseline_config_hash,
            'eligible_pairs': self.eligible_pairs,
            'total_observations_raw': self.total_observations_raw,
            'excluded_unpaired': self.excluded_unpaired,
            'excluded_pre_boundary': self.excluded_pre_boundary,
            'decision': self.decision,
            'decision_reason': self.decision_reason,
            'mean_delta_r': self.mean_delta_r,
            'mean_baseline_r': self.mean_baseline_r,
            'mean_candidate_r': self.mean_candidate_r,
            'candidate_win_rate': self.candidate_win_rate,
            'ci_lower': self.ci_lower,
            'ci_upper': self.ci_upper,
            'permutation_p': self.permutation_p,
            'oos_n': self.oos_n,
            'oos_delta_r': self.oos_delta_r,
            'confidence': self.confidence,
            'risk_level': self.risk_level,
            'symbols_positive': self.symbols_positive,
            'symbols_total': self.symbols_total,
            'periods_positive': self.periods_positive,
            'periods_total': self.periods_total,
            'survives_outlier_removal': self.survives_outlier_removal,
            'worst_delta_r': self.worst_delta_r,
            'promotion_blocked': self.promotion_blocked,
            'promotion_block_reason': self.promotion_block_reason,
            'actionable': self.actionable,
            'limitations': self.limitations,
            'created_at': self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'CandidateRecommendation':
        return cls(
            recommendation_id=data.get('recommendation_id', ''),
            evaluation_id=data.get('evaluation_id', ''),
            candidate_id=data.get('candidate_id', ''),
            treatment_id=data.get('treatment_id', ''),
            baseline_id=data.get('baseline_id', ''),
            baseline_config_hash=data.get('baseline_config_hash', ''),
            eligible_pairs=data.get('eligible_pairs', 0),
            total_observations_raw=data.get('total_observations_raw', 0),
            excluded_unpaired=data.get('excluded_unpaired', 0),
            excluded_pre_boundary=data.get('excluded_pre_boundary', 0),
            decision=data.get('decision', ''),
            decision_reason=data.get('decision_reason', ''),
            mean_delta_r=data.get('mean_delta_r', 0.0),
            mean_baseline_r=data.get('mean_baseline_r', 0.0),
            mean_candidate_r=data.get('mean_candidate_r', 0.0),
            candidate_win_rate=data.get('candidate_win_rate', 0.0),
            ci_lower=data.get('ci_lower'),
            ci_upper=data.get('ci_upper'),
            permutation_p=data.get('permutation_p'),
            oos_n=data.get('oos_n', 0),
            oos_delta_r=data.get('oos_delta_r', 0.0),
            confidence=data.get('confidence', ''),
            risk_level=data.get('risk_level', ''),
            symbols_positive=data.get('symbols_positive', 0),
            symbols_total=data.get('symbols_total', 0),
            periods_positive=data.get('periods_positive', 0),
            periods_total=data.get('periods_total', 0),
            survives_outlier_removal=data.get('survives_outlier_removal', False),
            worst_delta_r=data.get('worst_delta_r', 0.0),
            promotion_blocked=data.get('promotion_blocked', False),
            promotion_block_reason=data.get('promotion_block_reason', ''),
            actionable=data.get('actionable', False),
            limitations=data.get('limitations', []),
            created_at=data.get('created_at', ''),
        )


class RecommendationStore:
    def __init__(self, recommendations_dir: str | None = None) -> None:
        self._dir = Path(recommendations_dir or _RECOMMENDATIONS_DIR)
        self._recommendations: list[CandidateRecommendation] = []
        self._load()

    def get_by_evaluation_id(self, evaluation_id: str) -> CandidateRecommendation | None:
        for r in self._recommendations:
            if r.evaluation_id == evaluation_id:
                return r
        return None

    def get_by_candidate_id(self, candidate_id: str) -> list[CandidateRecommendation]:
        return [r for r in self._recommendations if r.candidate_id == candidate_id]

    def list_all(self) -> list[CandidateRecommendation]:
        return list(self._recommendations)

    def list_actionable(self) -> list[CandidateRecommendation]:
        return [r for r in self._recommendations if r.actionable]

    def append(self, recommendation: CandidateRecommendation) -> bool:
        if self.get_by_evaluation_id(recommendation.evaluation_id) is not None:
            logger.debug('[REC_STORE] duplicate - skipping')
            return False
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / 'recommendations.jsonl'
        line = json.dumps(recommendation.to_dict(), default=str) + chr(10)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, line.encode('utf-8'))
        finally:
            os.close(fd)
        self._recommendations.append(recommendation)
        logger.info('[REC_STORE] written %s actionable=%s', recommendation.recommendation_id, recommendation.actionable)
        return True

    def _load(self) -> None:
        path = self._dir / 'recommendations.jsonl'
        if not path.exists():
            return
        try:
            for line in path.read_text(encoding='utf-8').splitlines():
                if line.strip():
                    try:
                        self._recommendations.append(CandidateRecommendation.from_dict(json.loads(line)))
                    except (json.JSONDecodeError, TypeError, AttributeError):
                        continue
        except OSError:
            pass


def _derive_recommendation_id(evaluation_id: str) -> str:
    return f'REC-{evaluation_id}'


def _actionable(evaluation: CandidateEvaluation) -> bool:
    """A recommendation is actionable ONLY when VALIDATED, not blocked,
    has complete provenance (treatment_id, baseline_id, config_hash),
    sufficient sample size, and sufficient confidence."""
    if evaluation.decision != "VALIDATED":
        return False
    if evaluation.promotion_blocked:
        return False
    # Missing provenance makes the recommendation non-actionable (fail-closed)
    if not evaluation.treatment_id:
        return False
    if not evaluation.baseline_id:
        return False
    if not evaluation.config_hash:
        return False
    # Small sample size makes it non-actionable
    if evaluation.eligible_pairs < 30:
        return False
    # Insufficient confidence makes it non-actionable
    if evaluation.confidence == "INSUFFICIENT":
        return False
    return True


def _derive_limitations(evaluation: CandidateEvaluation) -> list[str]:
    limitations: list[str] = []
    if not evaluation.treatment_id:
        limitations.append('missing_treatment_identity')
    if not evaluation.baseline_id:
        limitations.append('missing_baseline_id')
    if not evaluation.config_hash:
        limitations.append('missing_baseline_config_hash')
    if evaluation.eligible_pairs < 30:
        limitations.append(f'small_sample: N={evaluation.eligible_pairs}<30')
    if evaluation.confidence == 'INSUFFICIENT':
        limitations.append('insufficient_evidence_confidence')
    if evaluation.risk_level == 'HIGH':
        limitations.append('high_risk_level')
    if not evaluation.survives_outlier_removal:
        limitations.append('fails_outlier_removal_robustness')
    if evaluation.ci_lower is not None and evaluation.ci_lower <= 0:
        limitations.append('ci_includes_zero')
    if evaluation.permutation_p is not None and evaluation.permutation_p >= 0.05:
        limitations.append(f'permutation_p_not_significant: p={evaluation.permutation_p}')
    return limitations


def create_recommendation(
    evaluation: CandidateEvaluation,
    *,
    store: RecommendationStore | None = None,
) -> CandidateRecommendation | None:
    if not isinstance(evaluation.candidate_id, str) or not evaluation.candidate_id.strip():
        logger.warning('[REC] fail-closed: missing candidate_id')
        return None
    rec = CandidateRecommendation(
        recommendation_id=_derive_recommendation_id(evaluation.evaluation_id),
        evaluation_id=evaluation.evaluation_id,
        candidate_id=evaluation.candidate_id,
        treatment_id=evaluation.treatment_id or '',
        baseline_id=evaluation.baseline_id or '',
        baseline_config_hash=evaluation.config_hash or '',
        eligible_pairs=evaluation.eligible_pairs,
        total_observations_raw=evaluation.total_observations_raw,
        excluded_unpaired=evaluation.excluded_unpaired,
        excluded_pre_boundary=evaluation.excluded_pre_boundary,
        decision=evaluation.decision,
        decision_reason=evaluation.decision_reason,
        mean_delta_r=evaluation.mean_delta_r,
        mean_baseline_r=evaluation.mean_baseline_r,
        mean_candidate_r=evaluation.mean_candidate_r,
        candidate_win_rate=evaluation.candidate_win_rate,
        ci_lower=evaluation.ci_lower,
        ci_upper=evaluation.ci_upper,
        permutation_p=evaluation.permutation_p,
        oos_n=evaluation.oos_n,
        oos_delta_r=evaluation.oos_delta_r,
        confidence=evaluation.confidence,
        risk_level=evaluation.risk_level,
        symbols_positive=evaluation.symbols_positive,
        symbols_total=evaluation.symbols_total,
        periods_positive=evaluation.periods_positive,
        periods_total=evaluation.periods_total,
        survives_outlier_removal=evaluation.survives_outlier_removal,
        worst_delta_r=evaluation.worst_delta_r,
        promotion_blocked=evaluation.promotion_blocked,
        promotion_block_reason=evaluation.promotion_block_reason,
        actionable=_actionable(evaluation),
        limitations=_derive_limitations(evaluation),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    if store is not None:
        store.append(rec)
    return rec


def create_recommendation_from_evaluation(
    evaluation: CandidateEvaluation,
    *,
    store: RecommendationStore | None = None,
) -> CandidateRecommendation | None:
    return create_recommendation(evaluation, store=store)
