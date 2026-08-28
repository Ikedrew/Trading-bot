"""Manifest writer for the research_data/ layer.

The manifest makes the research layer answerable:
  * schema.json            -- what each research record type looks like and
                              which rules produced it (schema/version metadata)
  * source_map.json        -- where every research area comes from
                              (source mappings + lineage rules)
  * field_ownership.json   -- field-level ownership / reconciliation rules
                              (which fields are authoritative)
  * projection_state.json  -- cursors + run history + source fingerprints
                              (traceability + idempotency/change detection)

Manifest writes are confined to research_data/manifest/. logs/ is never
touched.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .layout import RESEARCH_AREAS, SOURCE_DATASETS
from .ownership import (
    ASSESSMENT_DROPS,
    ASSESSMENT_EV_FIELDS,
    CANONICAL_FIELD,
    DECISION_AUDIT_DROPS,
    DECISION_TRACE_DROPS,
    DECISION_TRACE_RENAMES,
    EXECUTION_CONTEXT_DROPS,
    EXECUTION_RESULTS_DROPS,
    NEW_LAYER_SCHEMA_PREFIX,
    OPPORTUNITY_DROPS,
    OUTCOME_FORBIDDEN_IN_EXECUTION,
    OWNERSHIP_REGISTRY,
    PROJECTOR_VERSION,
    RISK_DEVIATION_KEEP,
    SHADOW_DROPS,
    SHADOW_EVENT_TYPES,
    TRADE_JOURNAL_DROPS,
    TRADE_JOURNAL_KEEP,
)

MANIFEST_VERSION = "1.0.0"

STATE_FILENAME = "projection_state.json"
SCHEMA_FILENAME = "schema.json"
SOURCE_MAP_FILENAME = "source_map.json"
OWNERSHIP_FILENAME = "field_ownership.json"
MAX_RUN_HISTORY = 50

# Reconciliation / lineage rules enforced by the projector (documented here so
# the manifest alone is enough to understand the layer).
LINEAGE_RULES = {
    "canonical_root": "canonical_opportunity_id is preserved verbatim; an "
                      "empty canonical root stays empty (no fabrication). "
                      "Format must be SYMBOL*BAR_TIME*PATTERN when non-empty.",
    "identifier_semantics": "observation_id, entity_id, correlation_id, "
                            "decision_id, execution ids, plan_id and "
                            "shadow_trade_id have DIFFERENT semantics and are "
                            "never treated as interchangeable or merged.",
    "joining": "Records are joined only on exact identifier matches "
               "(entity_id / decision_id / correlation_id / trade_id / "
               "same-bar (symbol, bar_time)). Timestamp proximity, symbol, "
               "price or similar-looking ids never create lineage.",
    "link_status": "resolved = joined to at least one contributor; "
                   "unresolved = join attempted, no partner found; "
                   "self_only = self-contained record (no join attempted).",
    "live_shadow_separation": "LIVE records live under live/; SHADOW records "
                              "under shadow/ (split by event_type). They are "
                              "never merged into one dataset.",
    "outcome_boundary": "Outcome fields (pnl, exit facts) are forbidden in "
                        "live/execution records; violations are stripped and "
                        "recorded in research_reconciliation.",
    "reconciliation": "When a contributor field collides with an owner field "
                      "and the values disagree, the owner value is retained "
                      "and the disagreement is recorded in "
                      "research_reconciliation -- never silently overwritten.",
    "idempotency": "A research record's identity is "
                   "hash(dataset, source path, source fingerprint). Re-running "
                   "over unchanged source bytes emits nothing. A changed "
                   "source record yields a NEW fingerprint (change is "
                   "detectable; the previous record is retained).",
    "source_readonly": "logs/ is opened read-only. The projector never "
                       "creates, modifies, moves or deletes anything under "
                       "logs/.",
}


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def build_schema_doc(logs_root: str = "logs") -> dict:
    """schema.json payload: research schemas + the rules that produced them."""
    schemas = {}
    for area, spec in RESEARCH_AREAS.items():
        schemas[spec["schema"]] = {
            "research_area": area,
            "version": 1,
            "research_id_prefix": spec["prefix"],
            "grain": spec["grain"],
            "sources": [
                {"dataset": ds,
                 "path": f"{logs_root}/{SOURCE_DATASETS[ds]['path']}"}
                for ds in spec["sources"]
            ],
            "lineage_fields": spec["lineage_fields"],
            "join_rule": spec["join"],
            "envelope_keys": [
                "research_id", "research_schema", "research_area",
                "projector_version", "projected_at_utc", "source_schema",
                "research_source", "research_lineage",
                "research_reconciliation (present only when a reconciliation "
                "event occurred)",
            ],
        }
    return {
        "manifest_version": MANIFEST_VERSION,
        "projector_version": PROJECTOR_VERSION,
        "schema_prefix": NEW_LAYER_SCHEMA_PREFIX,
        "generated_at_utc": _now_utc(),
        "canonical_root_field": CANONICAL_FIELD,
        "schemas": schemas,
        "lineage_rules": LINEAGE_RULES,
    }


def build_source_map_doc(logs_root: str = "logs") -> dict:
    """source_map.json payload: research area -> source dataset mapping."""
    return {
        "manifest_version": MANIFEST_VERSION,
        "generated_at_utc": _now_utc(),
        "source_root": logs_root,
        "research_root": "research_data",
        "source_datasets": {
            name: {"path": f"{logs_root}/{spec['path']}",
                   "layout": spec["layout"],
                   "read_only": True}
            for name, spec in SOURCE_DATASETS.items()
        },
        "research_areas": {
            area: {
                "schema": spec["schema"],
                "output_path": f"research_data/{area}/<SYMBOL>/<YYYY-MM-DD>.jsonl",
                "sources": spec["sources"],
                "ownership": OWNERSHIP_REGISTRY.get(
                    area, OWNERSHIP_REGISTRY.get("shadow/plan|open|progress|close")),
            }
            for area, spec in RESEARCH_AREAS.items()
        },
        "shadow_event_routing": {
            "source": f"{logs_root}/shadow_runtime_v1",
            "event_type_to_area": {
                evt: f"research_data/shadow/{sub}"
                for evt, sub in SHADOW_EVENT_TYPES.items()
            },
        },
    }


def build_ownership_doc() -> dict:
    """field_ownership.json payload: authoritative field rules."""
    return {
        "manifest_version": MANIFEST_VERSION,
        "generated_at_utc": _now_utc(),
        "registry": OWNERSHIP_REGISTRY,
        "rules": {
            "opportunity_drops": sorted(OPPORTUNITY_DROPS),
            "decision_trace_drops": sorted(DECISION_TRACE_DROPS),
            "decision_trace_renames": DECISION_TRACE_RENAMES,
            "decision_audit_drops": sorted(DECISION_AUDIT_DROPS),
            "assessment_ev_fields_owner": "assessments",
            "assessment_ev_fields": ASSESSMENT_EV_FIELDS,
            "assessment_drops": sorted(ASSESSMENT_DROPS),
            "execution_context_drops": sorted(EXECUTION_CONTEXT_DROPS),
            "execution_results_drops": sorted(EXECUTION_RESULTS_DROPS),
            "trade_truth_role": "canonical outcome owner",
            "trade_journal_drops": sorted(TRADE_JOURNAL_DROPS),
            "trade_journal_keep": TRADE_JOURNAL_KEEP,
            "risk_deviation_keep": RISK_DEVIATION_KEEP,
            "shadow_drops": sorted(SHADOW_DROPS),
            "outcome_forbidden_in_execution": sorted(
                OUTCOME_FORBIDDEN_IN_EXECUTION),
        },
        "principle": "merge == field-level reconciliation into NEW research "
                     "records with one authoritative source per field; "
                     "disagreements are recorded, never silently overwritten.",
    }


def write_manifest(research_root: Path | str,
                   run_summary: dict | None = None,
                   cursors: dict | None = None,
                   logs_root: Path | str | None = None) -> dict:
    """Write/refresh the manifest. Returns the written file paths."""
    root = Path(research_root)
    manifest_dir = root / "manifest"
    logs_label = "logs" if logs_root is None else str(logs_root)

    written: dict = {}
    _write_json(manifest_dir / SCHEMA_FILENAME,
                build_schema_doc(str(logs_label)))
    written["schema"] = str(manifest_dir / SCHEMA_FILENAME)
    _write_json(manifest_dir / SOURCE_MAP_FILENAME,
                build_source_map_doc(str(logs_label)))
    written["source_map"] = str(manifest_dir / SOURCE_MAP_FILENAME)
    _write_json(manifest_dir / OWNERSHIP_FILENAME, build_ownership_doc())
    written["field_ownership"] = str(manifest_dir / OWNERSHIP_FILENAME)

    # projection_state: merge cursors and append run history
    state_path = manifest_dir / STATE_FILENAME
    state: dict = {}
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = {}
    if cursors is not None:
        state["cursors"] = cursors
    state.setdefault("cursors", {})
    runs = state.setdefault("runs", [])
    if run_summary is not None:
        runs.append({
            "started_at_utc": run_summary.get("started_at_utc"),
            "finished_at_utc": run_summary.get("finished_at_utc"),
            "projector_version": run_summary.get("projector_version"),
            "backfill": run_summary.get("backfill"),
            "areas": run_summary.get("areas", {}),
            "sources": run_summary.get("sources", {}),
            "anomaly_count": len(run_summary.get("anomalies", [])),
        })
        del runs[:-MAX_RUN_HISTORY]
    state["projector_version"] = PROJECTOR_VERSION
    state["manifest_version"] = MANIFEST_VERSION
    state["last_run_utc"] = runs[-1]["finished_at_utc"] if runs else _now_utc()
    state["run_count"] = len(runs)
    _write_json(state_path, state)
    written["projection_state"] = str(state_path)
    return written
