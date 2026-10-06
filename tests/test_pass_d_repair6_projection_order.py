from __future__ import annotations

from copy import deepcopy
import json
import random
from types import SimpleNamespace

import pytest

from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
from research_engine.v10.continuous.research_projection import (
    ResearchProjectionError,
    build_evaluation_refresh_projection,
    build_unified_research_projection,
)


def _questions(order):
    return {qid: {
        "question_id": qid, "status": "INSUFFICIENT_DATA",
        "reason_code": "INSUFFICIENT_EVIDENCE", "reason": f"Exact reason: {qid}",
        "reason_details": {"gate": False, "values": [None, 0, "é"]},
        "snapshot_id": "ISNAP-EXISTING", "result_id": f"RESULT-{qid}",
        "evaluation_identity": {"question_id": qid, "evaluator_semantic_version": "v5"},
        "result": {"question_id": qid, "status": "INSUFFICIENT_DATA",
                   "reason_code": "INSUFFICIENT_EVIDENCE", "reason": f"Exact reason: {qid}",
                   "reason_details": {"sufficiency": {"gate": False}},
                   "snapshot_id": "ISNAP-EXISTING", "result_id": f"RESULT-{qid}",
                   "evaluation_identity_digest": f"DIGEST-{qid}"},
    } for qid in order}


def _build(mode, questions, generated=()):
    prior = {"canonical_questions": [], "generated_questions": list(generated),
             "findings": [{"finding_id": "F-EXISTING"}]}
    common = dict(continuous_cycle_id="ORDER-ONLY", frontier={"snapshot_id": "ISNAP-EXISTING"},
                  question_projection={"questions": questions})
    if mode == "refresh":
        return build_evaluation_refresh_projection(
            **common, predecessor_projection=prior, refreshed_question_ids=())
    return build_unified_research_projection(
        **common, bridge=None, scientific_store=SimpleNamespace(document={}),
        optimisation_registry=SimpleNamespace(list_hypotheses=lambda: [], list_candidates=lambda: []),
        validation_store=SimpleNamespace(ordered=lambda: []),
        q71={"generated_questions": list(generated)})


@pytest.mark.parametrize("mode", ["unified", "refresh"])
@pytest.mark.parametrize("input_order", ["shuffled", "alphabetical", "reversed"])
def test_canonical_order_and_exact_authority_preservation(mode, input_order):
    order = list(BASELINE_QUESTION_IDS)
    if input_order == "shuffled":
        random.Random(6).shuffle(order)
    elif input_order == "alphabetical":
        order.sort()
    else:
        order.reverse()
    questions = _questions(order)
    before = deepcopy(questions)
    # The exact old builders' sorting path fails this contract, independently
    # of insertion order. This makes the regression non-vacuous.
    old_ids = [qid for qid, _ in sorted(questions.items())]
    assert old_ids != list(BASELINE_QUESTION_IDS)
    assert order != list(BASELINE_QUESTION_IDS)

    generated = [{"generated_question_id": "Q71-OWNED", "status": "ACTIVE"},
                 {"generated_question_id": "Q72-OWNED", "status": "WAITING_FOR_DATA"}]
    projection = _build(mode, questions, generated)
    rows = projection["canonical_questions"]
    ids = [row["question_id"] for row in rows]
    assert ids == list(BASELINE_QUESTION_IDS)
    assert len(ids) == len(set(ids)) == 70
    assert set(BASELINE_QUESTION_IDS) - set(ids) == set()
    for row in rows:
        original = before[row["question_id"]]
        for field, value in original.items():
            assert row[field] == value
            assert json.dumps(row[field], ensure_ascii=False) == json.dumps(value, ensure_ascii=False)
    assert questions == before
    assert projection["generated_questions"] == generated
    assert _build(mode, _questions(BASELINE_QUESTION_IDS), generated) == projection
    assert _build(mode, questions)["canonical_questions"] == rows


@pytest.mark.parametrize("mode", ["unified", "refresh"])
@pytest.mark.parametrize("defect", ["missing", "empty", "duplicate_row", "duplicate_result", "alias", "sequence"])
def test_invalid_baseline_fails_closed(mode, defect):
    questions = _questions(BASELINE_QUESTION_IDS)
    first, second = BASELINE_QUESTION_IDS[:2]
    if defect == "missing":
        del questions[first]
    elif defect == "empty":
        questions = {}
    elif defect == "duplicate_row":
        questions[second]["question_id"] = first
    elif defect == "duplicate_result":
        questions[second]["result"]["question_id"] = first
    elif defect == "alias":
        questions["ALIAS"] = questions.pop(first)
    else:
        questions = list(questions.values()) + [questions[first]]
    with pytest.raises(ResearchProjectionError, match="CANONICAL_70_PROJECTION_INVARIANT_FAILED"):
        _build(mode, questions)


def test_absent_question_authority_preserves_existing_unified_policy():
    projection = build_unified_research_projection(
        continuous_cycle_id="NO-AUTHORITY", frontier={}, question_projection=None, bridge=None,
        scientific_store=SimpleNamespace(document={}),
        optimisation_registry=SimpleNamespace(list_hypotheses=lambda: [], list_candidates=lambda: []),
        validation_store=SimpleNamespace(ordered=lambda: []))
    assert projection["canonical_questions"] == []
