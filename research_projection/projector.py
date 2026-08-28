"""Read-only projector: logs/ (existing capture) -> research_data/ (research layer).

Contract (Phase 7B):
  * logs/ is opened READ-ONLY. Nothing under logs/ is ever modified, moved,
    renamed or disabled. The trading runtime does not depend on this module.
  * research_data/ is a NEW derived research representation layer, populated by
    field-level reconciliation (per ownership.py) -- never folder mirroring.
  * Canonical lineage is preserved VERBATIM. An empty canonical root stays
    empty. Records that cannot be reliably linked are marked
    link_status="unresolved" -- lineage is never fabricated.
  * LIVE and SHADOW remain separately identifiable (separate areas/schemas).
  * Idempotency: a research record's identity is a hash of
    (dataset, source relative path, source-record fingerprint). Re-running the
    projector over unchanged source bytes produces no new records. If a source
    record's content changes, the new fingerprint makes the change detectable
    (a new record carrying the new fingerprint is emitted; the old one is
    retained -- never silently overwritten).
  * Cursor-based incremental projection: each source file's consumed byte
    offset is persisted in research_data/manifest/projection_state.json.
    Appended runtime bytes are projected as they appear. Historical backfill
    is opt-in via backfill=True (default on first run).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .layout import (
    ENVELOPE_KEYS,
    RESEARCH_AREAS,
    SHADOW_EVENT_TO_AREA,
    SOURCE_DATASETS,
)
from .ownership import (
    ASSESSMENT_DROPS,
    ASSESSMENT_EV_FIELDS,
    CANONICAL_FIELD,
    DECISION_AUDIT_DROPS,
    DECISION_TRACE_DROPS,
    DECISION_TRACE_RENAMES,
    EXECUTION_CONTEXT_DROPS,
    EXECUTION_RESULTS_DROPS,
    OPPORTUNITY_DROPS,
    OUTCOME_FORBIDDEN_IN_EXECUTION,
    PROJECTOR_VERSION,
    RISK_DEVIATION_KEEP,
    SHADOW_DROPS,
    SHADOW_EVENT_TYPES,
    TRADE_JOURNAL_KEEP,
    drop_nested,
    validate_canonical_root,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOGS_ROOT = REPO_ROOT / "logs"
DEFAULT_RESEARCH_ROOT = REPO_ROOT / "research_data"

MANIFEST_DIRNAME = "manifest"
STATE_FILENAME = "projection_state.json"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(record: dict) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(record: dict) -> str:
    """Content fingerprint of a raw source record (change-detection anchor)."""
    digest = hashlib.sha256(_canonical_json(record).encode("utf-8")).hexdigest()
    return "sha256:" + digest


def _research_id(prefix: str, dataset: str, src_rel: str, fingerprint: str) -> str:
    """Deterministic research identity: same source record -> same id."""
    digest = hashlib.sha256(
        f"{dataset}|{src_rel}|{fingerprint}".encode("utf-8")
    ).hexdigest()
    return f"{prefix}_{digest[:16]}"


def _load_jsonl(path: Path):
    """Yield (line_no, record-or-None) from a JSONL file. None = parse error."""
    with open(path, "r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield line_no, json.loads(line)
            except json.JSONDecodeError:
                yield line_no, None


def _iter_source_files(logs_root: Path, dataset: str):
    """Yield (symbol_or_None, date, path) for a dataset's source files."""
    spec = SOURCE_DATASETS[dataset]
    root = logs_root / spec["path"]
    if not root.is_dir():
        return
    layout = spec["layout"]
    if layout == "symbol_date":
        for symbol_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            for f in sorted(symbol_dir.glob("*.jsonl")):
                yield symbol_dir.name, f.stem, f
    elif layout == "flat_date":
        for f in sorted(root.glob("*.jsonl")):
            yield None, f.stem, f
    elif layout == "symbol_date_flatname":
        for f in sorted(root.glob("*.jsonl")):
            symbol = f.stem.rsplit("_", 1)[0]
            yield symbol, f.stem[len(symbol) + 1:], f


def _extract_symbol_date(path: Path) -> tuple[str, str]:
    """(symbol, date) from a symbol_date-layout source path."""
    return path.parent.name, path.stem



# ---------------------------------------------------------------------------
# Projector
# ---------------------------------------------------------------------------

class Projector:
    """Projects logs/ capture into research_data/ (read-only on logs/)."""

    def __init__(self, logs_root: Path | str | None = None,
                 research_root: Path | str | None = None,
                 backfill: bool = True):
        self.logs_root = Path(logs_root) if logs_root else DEFAULT_LOGS_ROOT
        self.research_root = Path(research_root) if research_root else DEFAULT_RESEARCH_ROOT
        self.backfill = backfill
        self._cursors: dict[str, dict] = {}
        self._known_ids: dict[str, set] = {}      # output rel path -> set(research_id)
        self._summary: dict = {}
        self._dec_idx_cache: dict[str, dict] = {}
        self._exec_idx_cache: dict[str, dict] = {}
        self._obs_bar_idx_cache: dict[str, dict] = {}
        self._journal_idx_cache: dict | None = None

    # ------------------------------------------------------------------ run
    def run(self) -> dict:
        self._summary = {
            "started_at_utc": _now_utc(),
            "finished_at_utc": None,
            "projector_version": PROJECTOR_VERSION,
            "backfill": self.backfill,
            "areas": {},
            "sources": {"files_scanned": 0, "files_with_new_data": 0,
                        "files_reset": 0},
            "anomalies": [],
        }
        self._cursors = self._load_cursors()
        self._scan_known_ids()

        for area in ("live/observation", "live/opportunity", "live/decision",
                     "live/execution", "live/outcome", "market_context"):
            getattr(self, "_project_" + area.replace("/", "_"))()
        self._project_shadow()

        self._summary["finished_at_utc"] = _now_utc()
        self._save_cursors()
        return self._summary

    # ------------------------------------------------------- output helpers
    def _out_path(self, area: str, symbol: str, date: str) -> Path:
        return self.research_root / area / symbol / f"{date}.jsonl"

    def _scan_known_ids(self) -> None:
        """Pre-scan existing research output so re-runs never duplicate."""
        self._known_ids = {}
        for area in RESEARCH_AREAS:
            area_dir = self.research_root / area
            if not area_dir.is_dir():
                continue
            for f in area_dir.rglob("*.jsonl"):
                ids: set = set()
                rel = str(f.relative_to(self.research_root)).replace("\\", "/")
                for _, rec in _load_jsonl(f):
                    if isinstance(rec, dict) and "research_id" in rec:
                        ids.add(rec["research_id"])
                self._known_ids[rel] = ids

    def _emit(self, area: str, symbol: str, date: str, record: dict) -> bool:
        """Append a research record. Returns False if already projected."""
        out = self._out_path(area, symbol, date)
        rel = str(out.relative_to(self.research_root)).replace("\\", "/")
        known = self._known_ids.setdefault(rel, set())
        rid = record["research_id"]
        if rid in known:
            self._bump(area, "records_skipped_existing")
            return False
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        known.add(rid)
        self._bump(area, "records_projected")
        return True

    def _bump(self, area: str, counter: str, n: int = 1) -> None:
        bucket = self._summary["areas"].setdefault(
            area, {"records_projected": 0, "records_skipped_existing": 0,
                   "records_skipped_unidentifiable": 0})
        bucket[counter] += n

    def _anomaly(self, kind: str, **detail) -> None:
        self._summary["anomalies"].append({"kind": kind, **detail})

    def _count_file(self) -> None:
        self._summary["sources"]["files_scanned"] += 1

    # ------------------------------------------------------- cursor helpers
    def _state_path(self) -> Path:
        return self.research_root / MANIFEST_DIRNAME / STATE_FILENAME

    def _load_cursors(self) -> dict:
        state = self._state_path()
        if state.is_file():
            try:
                data = json.loads(state.read_text(encoding="utf-8"))
                return dict(data.get("cursors", {}))
            except (json.JSONDecodeError, OSError):
                self._anomaly("state_unreadable", path=str(state))
        return {}

    def _save_cursors(self) -> None:
        # Deferred import avoids a circular import (manifest imports layout
        # and ownership; it never needs the Projector class itself).
        from .manifest import write_manifest
        write_manifest(self.research_root, run_summary=self._summary,
                       cursors=self._cursors, logs_root=self.logs_root)

    def _consume_source(self, dataset: str, path: Path):
        """Yield (source_rel_key, record) for NEW complete lines of a file.

        Manages the byte-offset cursor. Files that shrank are treated as
        rewritten: the cursor resets and the whole file is re-read (content
        hashes make re-projection idempotent, so this is safe).
        """
        key = str(path.relative_to(self.logs_root)).replace("\\", "/")
        cursor = self._cursors.get(key, {})
        offset = int(cursor.get("offset", 0))
        size = path.stat().st_size

        if offset > size:
            self._anomaly("source_file_reset", path=key,
                          expected_offset=offset, actual_size=size)
            self._summary["sources"]["files_reset"] += 1
            offset = 0
        elif offset == size and offset > 0:
            return  # nothing new
        elif offset == 0 and size > 0 and not self.backfill and not cursor:
            # honour no-backfill: skip existing bytes, remember the position
            self._cursors[key] = {"offset": size,
                                  "records_projected": cursor.get("records_projected", 0)}
            return

        self._count_file()
        with open(path, "rb") as fh:
            fh.seek(offset)
            data = fh.read()
        cut = data.rfind(b"\n")
        if cut == -1:
            return  # no complete line yet; cursor stays put
        new_offset = offset + cut + 1
        self._cursors[key] = {"offset": new_offset,
                              "records_projected": cursor.get("records_projected", 0)}
        self._summary["sources"]["files_with_new_data"] += 1
        for raw in data[: cut + 1].splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._anomaly("source_line_unparseable", path=key,
                              byte_offset=offset)
                continue
            if not isinstance(rec, dict):
                self._anomaly("source_line_not_object", path=key)
                continue
            yield key, rec

    # ------------------------------------------------------ envelope builder
    def _build_record(self, *, area: str, prefix: str, dataset: str,
                      src_rel: str, source_record: dict,
                      projected_fields: dict, lineage: dict,
                      link_status: str, source_schema: str | None,
                      reconciliation: list | None = None,
                      owner_note: str | None = None) -> dict:
        fp = _fingerprint(source_record)
        rid = _research_id(prefix, dataset, src_rel, fp)
        lin = {k: v for k, v in lineage.items() if v not in (None, "")}
        lin = dict(lin)  # do not mutate caller dict
        if CANONICAL_FIELD in lin:
            lin["canonical_root_valid"] = validate_canonical_root(
                lin[CANONICAL_FIELD], lin.get("symbol"))
        lin["link_status"] = link_status
        rec = {
            "research_id": rid,
            "research_schema": RESEARCH_AREAS[area]["schema"],
            "research_area": area,
            "projector_version": PROJECTOR_VERSION,
            "projected_at_utc": _now_utc(),
            "source_schema": source_schema,
            "research_source": {
                "dataset": dataset,
                "path": f"logs/{src_rel}",
                "fingerprint": fp,
                "owner": owner_note or RESEARCH_AREAS[area]["sources"][0],
            },
            "research_lineage": lin,
        }
        if reconciliation:
            rec["research_reconciliation"] = reconciliation
        rec.update(projected_fields)
        return rec

    # ------------------------------------------------- live/observation
    def _project_live_observation(self) -> None:
        for symbol, date, path in _iter_source_files(self.logs_root,
                                                     "strategy_observations"):
            for src_rel, rec_src in self._consume_source("strategy_observations",
                                                         path):
                fields = dict(rec_src)
                source_schema = fields.pop("schema_version", None)
                lineage = {
                    "observation_id": fields.get("observation_id"),
                    "entity_id": fields.get("entity_id"),
                    "symbol": fields.get("symbol"),
                    "cycle_id": fields.get("cycle_id"),
                }
                record = self._build_record(
                    area="live/observation", prefix="robs",
                    dataset="strategy_observations", src_rel=src_rel,
                    source_record=rec_src, projected_fields=fields,
                    lineage=lineage, link_status="self_only",
                    source_schema=source_schema)
                self._emit("live/observation", symbol, date, record)

    # ------------------------------------------------- live/opportunity
    def _project_live_opportunity(self) -> None:
        for symbol, date, path in _iter_source_files(self.logs_root,
                                                     "opportunities"):
            for src_rel, rec_src in self._consume_source("opportunities", path):
                fields = dict(rec_src)
                source_schema = fields.pop("schema_version", None)
                # Per ownership registry, opportunity_id is the retired
                # identity slot; it is retained in lineage (verbatim) but
                # dropped from the projected fields.
                opportunity_id = fields.get("opportunity_id")
                canonical = fields.get(CANONICAL_FIELD)
                lineage = {
                    CANONICAL_FIELD: canonical,
                    "opportunity_id": opportunity_id,
                    "opportunity_group_id": canonical or opportunity_id,
                    "entity_id": fields.get("entity_id"),
                    "correlation_id": fields.get("correlation_id"),
                    "decision_id": fields.get("decision_id"),
                    "symbol": fields.get("symbol"),
                    "cycle_id": fields.get("cycle_id"),
                }
                fields = {k: v for k, v in fields.items()
                          if k not in OPPORTUNITY_DROPS}
                record = self._build_record(
                    area="live/opportunity", prefix="ropp",
                    dataset="opportunities", src_rel=src_rel,
                    source_record=rec_src, projected_fields=fields,
                    lineage=lineage, link_status="self_only",
                    source_schema=source_schema)
                self._emit("live/opportunity", symbol, date, record)

    # ------------------------------------------------- live/decision
    def _decision_indexes(self, symbol: str) -> dict:
        """Full-read (lookup-side) indexes for one symbol's decision partners.

        Contributions join ONLY on exact identifiers (entity_id /
        decision_id / correlation_id). Never on timestamp proximity.
        """
        if symbol in self._dec_idx_cache:
            return self._dec_idx_cache[symbol]
        idx = {
            "trace_by_entity": {},
            "audit_by_entity": {},
            "audit_by_decision": {},
            "audit_by_corr": {},
            "asmt_by_entity": {},
            "asmt_by_decision": {},
            "asmt_by_corr": {},
        }
        for _, date, path in _iter_source_files(self.logs_root, "decision_trace"):
            for _, rec in _load_jsonl(path):
                if not isinstance(rec, dict):
                    continue
                eid = rec.get("entity_id") or ""
                if eid and eid not in idx["trace_by_entity"]:
                    idx["trace_by_entity"][eid] = rec
        for _, date, path in _iter_source_files(self.logs_root, "decision_audit"):
            for _, rec in _load_jsonl(path):
                if not isinstance(rec, dict):
                    continue
                eid = rec.get("entity_id") or ""
                did = rec.get("decision_id") or ""
                cid = rec.get("correlation_id") or ""
                if eid and eid not in idx["audit_by_entity"]:
                    idx["audit_by_entity"][eid] = rec
                if did and did not in idx["audit_by_decision"]:
                    idx["audit_by_decision"][did] = rec
                if cid and cid not in idx["audit_by_corr"]:
                    idx["audit_by_corr"][cid] = rec
        for _, date, path in _iter_source_files(self.logs_root, "assessments"):
            for _, rec in _load_jsonl(path):
                if not isinstance(rec, dict):
                    continue
                eid = rec.get("entity_id") or ""
                did = rec.get("decision_id") or ""
                cid = rec.get("correlation_id") or ""
                if eid and eid not in idx["asmt_by_entity"]:
                    idx["asmt_by_entity"][eid] = rec
                if did and did not in idx["asmt_by_decision"]:
                    idx["asmt_by_decision"][did] = rec
                if cid and cid not in idx["asmt_by_corr"]:
                    idx["asmt_by_corr"][cid] = rec
        self._dec_idx_cache[symbol] = idx
        return idx

    @staticmethod
    def _merge_contributor(record: dict, contributor: dict, drops: set,
                           renames: dict, owner: str,
                           reconciliation: list) -> bool:
        """Field-level reconciliation: non-owners contribute only fields the
        owner does not already carry. Disagreement is recorded, never silently
        overwritten. Returns True if any field was contributed."""
        contributed = False
        for key, value in contributor.items():
            # renames apply BEFORE the drop check: a key renamed by the
            # registry (e.g. correlation_id -> trace_id) is evaluated under
            # its research name.
            new_key = renames.get(key, key)
            if new_key in drops:
                continue
            if new_key in ENVELOPE_KEYS:
                continue
            if new_key in record:
                if record[new_key] != value:
                    reconciliation.append({
                        "kind": "value_conflict",
                        "field": new_key,
                        "owner": record.get("research_source", {}).get("owner")
                                 if isinstance(record.get("research_source"), dict) else owner,
                        "kept_value": record[new_key],
                        "rejected_value": value,
                        "rejected_source": owner,
                        "rule": "owner field retained; disagreement recorded, not overwritten",
                    })
                continue
            record[new_key] = value
            contributed = True
        return contributed

    def _project_live_decision(self) -> None:
        for symbol, date, path in _iter_source_files(self.logs_root,
                                                     "decision_ledger"):
            idx = None
            for src_rel, rec_src in self._consume_source("decision_ledger",
                                                         path):
                if idx is None:
                    idx = self._decision_indexes(symbol)
                fields = dict(rec_src)
                source_schema = fields.pop("schema_version", None)
                entity_id = fields.get("entity_id") or ""
                corr = (fields.get("correlation_id")
                        or fields.get("context_snapshot_id") or "")
                decision_id = fields.get("decision_id") or ""
                # spine = the ledger record itself
                spine = dict(fields)
                reconciliation: list = []

                trace = (idx["trace_by_entity"].get(entity_id)
                         if entity_id else None)
                if trace is not None:
                    # derived counts replace the verbose stage lists
                    trace_detail = dict(trace)
                    trace_detail["stages_reached_count"] = len(
                        trace.get("stages_reached") or [])
                    trace_detail["stages_passed_count"] = len(
                        trace.get("stages_passed") or [])
                    self._merge_contributor(
                        spine, trace_detail, DECISION_TRACE_DROPS,
                        DECISION_TRACE_RENAMES, "decision_trace",
                        reconciliation)

                audit = None
                if entity_id:
                    audit = idx["audit_by_entity"].get(entity_id)
                if audit is None and decision_id:
                    audit = idx["audit_by_decision"].get(decision_id)
                if audit is None and corr:
                    audit = idx["audit_by_corr"].get(corr)
                if audit is not None:
                    self._merge_contributor(
                        spine, audit, DECISION_AUDIT_DROPS, {},
                        "decision_audit", reconciliation)

                asmt = None
                if entity_id:
                    asmt = idx["asmt_by_entity"].get(entity_id)
                if asmt is None and decision_id:
                    asmt = idx["asmt_by_decision"].get(decision_id)
                if asmt is None and corr:
                    asmt = idx["asmt_by_corr"].get(corr)
                if asmt is not None:
                    ev_block = {k: asmt[k] for k in ASSESSMENT_EV_FIELDS
                                if k in asmt}
                    self._merge_contributor(
                        spine, ev_block, set(), {}, "assessments",
                        reconciliation)

                if CANONICAL_FIELD not in spine and asmt is not None:
                    # canonical root may live on the assessment row; preserve
                    # it verbatim (may be empty -> stays empty)
                    val = asmt.get(CANONICAL_FIELD) or ""
                    if val:
                        spine[CANONICAL_FIELD] = val

                matched = bool(trace or audit or asmt)
                lineage = {
                    CANONICAL_FIELD: spine.get(CANONICAL_FIELD),
                    "entity_id": entity_id or None,
                    "correlation_id": fields.get("correlation_id") or None,
                    "context_snapshot_id": fields.get("context_snapshot_id"),
                    "decision_id": spine.get("decision_id"),
                    "trace_id": spine.get("trace_id"),
                    "symbol": fields.get("symbol"),
                    "cycle_id": fields.get("cycle_id"),
                }
                record = self._build_record(
                    area="live/decision", prefix="rdec",
                    dataset="decision_ledger", src_rel=src_rel,
                    source_record=rec_src, projected_fields=spine,
                    lineage=lineage,
                    link_status="resolved" if matched else "unresolved",
                    source_schema=source_schema,
                    reconciliation=reconciliation,
                    owner_note="decision_ledger (spine)")
                self._emit("live/decision", symbol, date, record)

    # ------------------------------------------------- live/execution
    def _execution_indexes(self, symbol: str) -> dict:
        """Lookup indexes for one symbol: results/context by correlation_id."""
        results_key = f"{symbol}::results"
        context_key = f"{symbol}::context"
        if results_key not in self._exec_idx_cache:
            results: dict[str, list] = {}
            for _, date, path in _iter_source_files(self.logs_root,
                                                    "execution_results"):
                for _, rec in _load_jsonl(path):
                    if not isinstance(rec, dict):
                        continue
                    cid = rec.get("correlation_id") or ""
                    if cid:
                        results.setdefault(cid, []).append(rec)
            self._exec_idx_cache[results_key] = {"by_corr": results}
        if context_key not in self._exec_idx_cache:
            contexts: dict[str, list] = {}
            for _, date, path in _iter_source_files(self.logs_root,
                                                    "execution_context"):
                for _, rec in _load_jsonl(path):
                    if not isinstance(rec, dict):
                        continue
                    cid = rec.get("correlation_id") or ""
                    if cid:
                        contexts.setdefault(cid, []).append(rec)
            self._exec_idx_cache[context_key] = {"by_corr": contexts}
        return (self._exec_idx_cache[results_key]["by_corr"],
                self._exec_idx_cache[context_key]["by_corr"])

    def _project_live_execution(self) -> None:
        for symbol, date, path in _iter_source_files(self.logs_root,
                                                     "execution_context"):
            idx = None
            for src_rel, rec_src in self._consume_source("execution_context",
                                                         path):
                if idx is None:
                    results_idx, _ = self._execution_indexes(symbol)
                    idx = results_idx
                fields = dict(rec_src)
                source_schema = fields.pop("schema_version", None)
                corr = fields.get("correlation_id") or ""
                spine = dict(fields)
                reconciliation: list = []
                matches = idx.get(corr, []) if corr else []
                for result in matches:
                    result_fields = {k: v for k, v in result.items()
                                     if k not in EXECUTION_RESULTS_DROPS}
                    self._merge_contributor(
                        spine, result_fields, set(), {}, "execution_results",
                        reconciliation)
                # outcome boundary: never let outcome fields into execution
                for forbidden in OUTCOME_FORBIDDEN_IN_EXECUTION:
                    if forbidden in spine:
                        reconciliation.append({
                            "kind": "outcome_boundary_enforced",
                            "field": forbidden,
                            "rule": "outcome fields forbidden in live/execution "
                                    "(owner: live/outcome)",
                        })
                        spine.pop(forbidden, None)

                lineage = {
                    "correlation_id": corr or None,
                    "entity_id": fields.get("entity_id"),
                    "decision_id": spine.get("decision_id"),
                    "observation_id": spine.get("observation_id"),
                    CANONICAL_FIELD: spine.get(CANONICAL_FIELD),
                    "symbol": fields.get("symbol"),
                    "cycle_id": fields.get("cycle_id"),
                }
                record = self._build_record(
                    area="live/execution", prefix="rexe",
                    dataset="execution_context", src_rel=src_rel,
                    source_record=rec_src, projected_fields=spine,
                    lineage=lineage,
                    link_status="resolved" if matches else "unresolved",
                    source_schema=source_schema,
                    reconciliation=reconciliation,
                    owner_note="execution_context (pre-trade)")
                self._emit("live/execution", symbol, date, record)

        # execution_results with no pre-trade context are still projected
        # (honest representation) rather than silently dropped.
        self._project_orphan_execution_results()

    def _project_orphan_execution_results(self) -> None:
        for symbol, date, path in _iter_source_files(self.logs_root,
                                                     "execution_results"):
            ctx_idx = None
            for src_rel, rec_src in self._consume_source("execution_results",
                                                         path):
                if ctx_idx is None:
                    _, ctx_idx = self._execution_indexes(symbol)
                corr = rec_src.get("correlation_id") or ""
                if corr and ctx_idx.get(corr):
                    continue  # represented via execution_context owner row
                fields = {k: v for k, v in rec_src.items()
                          if k not in EXECUTION_RESULTS_DROPS}
                lineage = {
                    "correlation_id": corr or None,
                    "entity_id": rec_src.get("entity_id"),
                    "decision_id": rec_src.get("decision_id"),
                    CANONICAL_FIELD: rec_src.get(CANONICAL_FIELD),
                    "symbol": rec_src.get("symbol"),
                    "cycle_id": rec_src.get("cycle_id"),
                }
                record = self._build_record(
                    area="live/execution", prefix="rexe",
                    dataset="execution_results", src_rel=src_rel,
                    source_record=rec_src, projected_fields=fields,
                    lineage=lineage, link_status="unresolved",
                    source_schema=rec_src.get("schema_version"),
                    reconciliation=[{
                        "kind": "unlinked_fill",
                        "rule": "no execution_context row carries this "
                                "correlation_id; fill emitted standalone",
                    }],
                    owner_note="execution_results_only")
                self._emit("live/execution", symbol, date, record)

    # ------------------------------------------------- live/outcome
    def _journal_index(self) -> dict:
        if self._journal_idx_cache is None:
            idx: dict[str, list] = {}
            for _, date, path in _iter_source_files(self.logs_root,
                                                    "trade_journal"):
                for _, rec in _load_jsonl(path):
                    if not isinstance(rec, dict):
                        continue
                    tid = rec.get("trade_id") or ""
                    if tid:
                        idx.setdefault(tid, []).append(rec)
            self._journal_idx_cache = idx
        return self._journal_idx_cache

    def _risk_index(self, symbol: str) -> dict:
        cache_key = f"{symbol}::risk"
        cached = getattr(self, "_risk_idx_cache", None)
        if cached is None:
            cached = self._risk_idx_cache = {}
        if cache_key in cached:
            return cached[cache_key]
        idx: dict[str, list] = {}
        for _, date, path in _iter_source_files(self.logs_root,
                                                "risk_deviation"):
            for _, rec in _load_jsonl(path):
                if not isinstance(rec, dict):
                    continue
                tid = rec.get("trade_id") or ""
                if tid:
                    idx.setdefault(tid, []).append(rec)
        cached[cache_key] = idx
        return idx

    def _project_live_outcome(self) -> None:
        for symbol, date, path in _iter_source_files(self.logs_root,
                                                     "trade_truth"):
            journal_idx = risk_idx = None
            for src_rel, rec_src in self._consume_source("trade_truth", path):
                if journal_idx is None:
                    journal_idx = self._journal_index()
                    risk_idx = self._risk_index(symbol)
                fields = dict(rec_src)
                source_schema = fields.pop("schema_version", None)
                identity = fields.get("identity") or {}
                trade_id = identity.get("trade_id") or ""
                corr = identity.get("correlation_id") or ""
                spine = dict(fields)
                reconciliation: list = []

                matched = False
                for journal in journal_idx.get(trade_id, []) if trade_id else []:
                    keep = {k: journal[k] for k in TRADE_JOURNAL_KEEP
                            if k in journal}
                    if self._merge_contributor(spine, keep, set(), {},
                                               "trade_journal",
                                               reconciliation):
                        matched = True
                for risk in risk_idx.get(trade_id, []) if trade_id else []:
                    keep = {k: risk[k] for k in RISK_DEVIATION_KEEP
                            if k in risk}
                    if self._merge_contributor(spine, keep, set(), {},
                                               "risk_deviation",
                                               reconciliation):
                        matched = True

                # canonical root may be carried elsewhere on the truth row
                canonical = (spine.get(CANONICAL_FIELD)
                             or identity.get(CANONICAL_FIELD) or "")
                lineage = {
                    CANONICAL_FIELD: canonical or None,
                    "trade_id": trade_id or None,
                    "correlation_id": corr or None,
                    "symbol": identity.get("symbol") or fields.get("symbol"),
                }
                record = self._build_record(
                    area="live/outcome", prefix="rout",
                    dataset="trade_truth", src_rel=src_rel,
                    source_record=rec_src, projected_fields=spine,
                    lineage=lineage,
                    link_status="resolved" if matched else "unresolved",
                    source_schema=source_schema,
                    reconciliation=reconciliation,
                    owner_note="trade_truth (canonical outcome)")
                self._emit("live/outcome", symbol, date, record)

    # ------------------------------------------------- market_context
    def _obs_bar_index(self, symbol: str) -> dict:
        """(symbol, bar_time) -> observation identity, for exact same-bar
        reconciliation of market-context records (never proximity matching)."""
        if symbol in self._obs_bar_idx_cache:
            return self._obs_bar_idx_cache[symbol]
        idx: dict[tuple, dict] = {}
        for _, date, path in _iter_source_files(self.logs_root,
                                                "strategy_observations"):
            for _, rec in _load_jsonl(path):
                if not isinstance(rec, dict):
                    continue
                ts = rec.get("timestamp_utc")
                if ts is None:
                    continue
                try:
                    key = (rec.get("symbol") or symbol, int(float(ts)))
                except (TypeError, ValueError):
                    continue
                if key not in idx:
                    idx[key] = rec
        self._obs_bar_idx_cache[symbol] = idx
        return idx

    def _reconcile_market_context_ids(self, fields: dict, symbol: str,
                                      reconciliation: list) -> None:
        ts = fields.get("timestamp_utc")
        if ts is None:
            return
        try:
            bar_key = (symbol, int(float(ts)))
        except (TypeError, ValueError):
            return
        obs = self._obs_bar_index(symbol).get(bar_key)
        if obs is None:
            return
        if not fields.get("cycle_id") and obs.get("cycle_id") is not None:
            fields["cycle_id"] = obs["cycle_id"]
            reconciliation.append({
                "kind": "same_bar_reconciliation",
                "field": "cycle_id",
                "source": "strategy_observations (exact same-bar row)",
                "source_observation_id": obs.get("observation_id"),
            })
        if not fields.get("entity_id") and obs.get("entity_id"):
            fields["entity_id"] = obs["entity_id"]
            reconciliation.append({
                "kind": "same_bar_reconciliation",
                "field": "entity_id",
                "source": "strategy_observations (exact same-bar row)",
                "source_observation_id": obs.get("observation_id"),
            })

    def _project_market_context(self) -> None:
        # primary: logs/v3_shadow/market_context
        covered: set = set()
        for symbol, date, path in _iter_source_files(
                self.logs_root, "v3_market_context"):
            covered.add((symbol, date))
            for src_rel, rec_src in self._consume_source("v3_market_context",
                                                         path):
                fields = dict(rec_src)
                source_schema = fields.pop("schema_version", None)
                reconciliation: list = []
                self._reconcile_market_context_ids(fields, symbol,
                                                   reconciliation)
                lineage = {
                    "entity_id": fields.get("entity_id"),
                    "symbol": fields.get("symbol"),
                    "cycle_id": fields.get("cycle_id"),
                    "timestamp_utc": fields.get("timestamp_utc"),
                }
                record = self._build_record(
                    area="market_context", prefix="rmcx",
                    dataset="v3_market_context", src_rel=src_rel,
                    source_record=rec_src, projected_fields=fields,
                    lineage=lineage, link_status="self_only",
                    source_schema=source_schema,
                    reconciliation=reconciliation or None)
                self._emit("market_context", symbol, date, record)

        # fallback: logs/market_context for symbol/dates v3 does not cover
        for symbol, date, path in _iter_source_files(self.logs_root,
                                                     "market_context"):
            if (symbol, date) in covered:
                continue
            for src_rel, rec_src in self._consume_source("market_context",
                                                         path):
                fields = dict(rec_src)
                source_schema = fields.pop("schema_version", None)
                reconciliation: list = []
                self._reconcile_market_context_ids(fields, symbol,
                                                   reconciliation)
                lineage = {
                    "entity_id": fields.get("entity_id"),
                    "symbol": fields.get("symbol"),
                    "cycle_id": fields.get("cycle_id"),
                    "timestamp_utc": fields.get("timestamp_utc"),
                }
                record = self._build_record(
                    area="market_context", prefix="rmcx",
                    dataset="market_context", src_rel=src_rel,
                    source_record=rec_src, projected_fields=fields,
                    lineage=lineage, link_status="self_only",
                    source_schema=source_schema,
                    reconciliation=reconciliation or None)
                self._emit("market_context", symbol, date, record)

    # ------------------------------------------------- shadow/*
    def _project_shadow(self) -> None:
        for symbol, date, path in _iter_source_files(self.logs_root,
                                                     "shadow_runtime_v1"):
            for src_rel, rec_src in self._consume_source("shadow_runtime_v1",
                                                         path):
                event_type = rec_src.get("event_type")
                area = SHADOW_EVENT_TO_AREA.get(event_type)
                if area is None:
                    self._anomaly("unknown_shadow_event_type",
                                  path=src_rel, event_type=event_type)
                    continue
                fields = dict(rec_src)
                source_schema = fields.pop("schema_version", None)
                drop_nested(fields, SHADOW_DROPS)
                lineage = {
                    CANONICAL_FIELD: fields.get(CANONICAL_FIELD),
                    "plan_id": fields.get("plan_id"),
                    "shadow_trade_id": fields.get("shadow_trade_id"),
                    "entity_id": fields.get("entity_id"),
                    "symbol": fields.get("symbol"),
                    "cycle_id": fields.get("cycle_id"),
                }
                record = self._build_record(
                    area=area, prefix=RESEARCH_AREAS[area]["prefix"],
                    dataset="shadow_runtime_v1", src_rel=src_rel,
                    source_record=rec_src, projected_fields=fields,
                    lineage=lineage, link_status="self_only",
                    source_schema=source_schema)
                self._emit(area, symbol, date, record)

