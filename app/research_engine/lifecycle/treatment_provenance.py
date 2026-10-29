"""Wave 5.3A value validation. No registry, candidate lookup, or ID rehashing.

The immutable value is canonical JSON text; None means historical evidence is
unavailable. Scope members use exact Wave 4D membership (no case/space folding).
"""
import json
from pathlib import Path


def canonical_spec(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def validate_treatment_spec(spec, treatment_id, *, required=False):
    if spec is None and not required:
        return None
    if not isinstance(spec, str) or not spec:
        raise ValueError("Missing frozen treatment_spec")
    def check(condition):
        if not condition:
            raise ValueError("Invalid frozen treatment_spec")

    try:
        value = json.loads(spec)
        check(isinstance(value, dict) and set(value) == {"change_type", "declared", "scope", "treatment_id"})
        check(isinstance(treatment_id, str) and bool(treatment_id.strip()) and value["treatment_id"] == treatment_id)
        check(isinstance(value["declared"], dict))
        check(isinstance(value["scope"], dict) and set(value["scope"]) == {"symbols", "patterns"})
        for items in value["scope"].values():
            check(items is None or (
                isinstance(items, list) and bool(items)
                and all(isinstance(s, str) and s.strip() for s in items)
                and items == sorted(set(items))))
        if value["change_type"] == "direction_inversion":
            check(value["declared"] == {})
        elif value["change_type"] == "geometry_modification":
            params = value["declared"]
            check(set(params) == {"stop_multiplier"})
            check(type(params["stop_multiplier"]) is float and params["stop_multiplier"] > 0)
        else:
            raise ValueError("Unsupported declaration")
        check(canonical_spec(value) == spec)
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("Invalid frozen treatment_spec or treatment_id mismatch") from exc
    return spec


def validate_evaluation_spec(record, evaluations_dir=None):
    """Compare new provenance to the existing durable evaluation authority.

    Legacy research rows need not have an evaluation file; they remain missing,
    never reconstructed. New frozen provenance must have exactly one authority.
    """
    spec = validate_treatment_spec(record.treatment_spec, record.treatment_id)
    if spec is None:
        return
    cid = record.candidate_id
    if not cid or Path(cid).name != cid or "/" in cid or "\\" in cid:
        raise ValueError("Unsafe candidate ID")
    if evaluations_dir is None:
        from research_engine.lifecycle.candidate_evaluation_bridge import _EVALUATIONS_DIR
        evaluations_dir = _EVALUATIONS_DIR
    path = Path(evaluations_dir) / f"{cid}.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []
    matches = [r for r in rows if r.get("evaluation_id") == record.evaluation_id]
    if (len(matches) != 1 or matches[0].get("treatment_spec") != spec
            or matches[0].get("treatment_id") != record.treatment_id):
        raise ValueError("Evaluation treatment_spec provenance mismatch or unavailable")
