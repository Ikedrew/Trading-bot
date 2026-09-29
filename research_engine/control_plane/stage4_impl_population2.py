"""Population adapter helpers (additive Stage 4 repair)."""
from __future__ import annotations
from typing import Any, Mapping, Sequence
from research_engine.control_plane.stage4_implementation_repairs import GOVERNED_USABLE, FORBIDDEN_RUNNER_N, Stage4RepairError

def record_identity(row):
    """Return the canonical opportunity identity from flat or governed rows."""
    if not isinstance(row, Mapping):
        return ""
    identity = row.get("identity") if isinstance(row.get("identity"), Mapping) else {}
    return str(row.get("canonical_opportunity_id") or identity.get("canonical_opportunity_id") or "")

def filter_sources_to_governed_population(question_id, governed_records, *sources):
    """Filter source authorities before analysis using the Gate 1 identity roster."""
    governed = enforce_exact_population(question_id, governed_records)
    identities = {record_identity(row) for row in governed}
    if "" in identities or len(identities) != len(governed):
        raise Stage4RepairError("GOVERNED_IDENTITY_NOT_UNIQUE:" + str(question_id))
    filtered = []
    for source in sources:
        filtered.append([dict(row) for row in source if record_identity(row) in identities])
    return tuple(filtered)
def enforce_exact_population(question_id, records, identities=None):
    qid = str(question_id or "").strip().upper()
    if qid not in GOVERNED_USABLE:
        raise Stage4RepairError("UNKNOWN_GOVERNED_QUESTION:" + qid)
    expected = int(GOVERNED_USABLE[qid])
    rows = list(records or [])
    forbidden = FORBIDDEN_RUNNER_N.get(qid)
    if forbidden is not None and len(rows) == forbidden:
        raise Stage4RepairError("FORBIDDEN_RUNNER_POPULATION:" + qid)
    if identities is not None:
        wanted = [str(i) for i in identities]
        if len(wanted) != len(set(wanted)):
            raise Stage4RepairError("DUPLICATE_GOVERNED_IDENTITY:" + qid)
        if len(wanted) != expected:
            raise Stage4RepairError("GOVERNED_IDENTITY_COUNT_MISMATCH:" + qid)
        by_id = {}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            for key in ("canonical_opportunity_id", "entity_id", "lifecycle_identity", "shadow_trade_id"):
                val = row.get(key)
                if val is not None and str(val).strip():
                    by_id.setdefault(str(val), dict(row))
        selected = []
        for ident in wanted:
            hit = by_id.get(str(ident))
            if hit is None:
                raise Stage4RepairError("GOVERNED_IDENTITY_ABSENT:" + qid + ":" + str(ident))
            selected.append(hit)
        if len(selected) != expected:
            raise Stage4RepairError("POPULATION_MISMATCH:" + qid)
        return selected
    if len(rows) != expected:
        raise Stage4RepairError("POPULATION_MISMATCH:" + qid + ":got=" + str(len(rows)) + ":expected=" + str(expected))
    out = []
    for r in rows:
        out.append(dict(r) if isinstance(r, Mapping) else {})
    return out
def require_governed_identities_for_science(question_id, identities):
    if not identities:
        raise Stage4RepairError("GOVERNED_IDENTITY_LIST_REQUIRED:" + str(question_id))
    qid = str(question_id or "").strip().upper()
    if len(list(identities)) != int(GOVERNED_USABLE[qid]):
        raise Stage4RepairError("POPULATION_MISMATCH:" + qid)
