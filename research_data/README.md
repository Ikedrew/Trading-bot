# research_data/ — Research Representation Layer

**Status:** research-only. Nothing in the trading runtime depends on this layer.
**Source system:** `logs/` (existing operational capture — READ-ONLY).
**Owner code:** `research_projection/` (projector, manifest, ownership registry).

---

## 1. Architectural position

```
                    ┌─────────────────────────┐
                    │      EXISTING BOT       │
                    │ strategy/decision/risk/ │
                    │ execution/shadow        │
                    └───────────┬─────────────┘
                                │ writes (unchanged)
                                ▼
                    ┌─────────────────────────┐
                    │         logs/           │   EXISTING SOURCE SYSTEM
                    │  (never modified by     │
                    │   the research layer)   │
                    └───────────┬─────────────┘
                                │ READ / PROJECT   (python -m research_projection)
                                ▼
                    ┌─────────────────────────┐
                    │     research_data/      │   NEW RESEARCH LAYER
                    │ live/ shadow/           │
                    │ market_context/ manifest│
                    └─────────────────────────┘
```

`research_data/` is a **projection / materialisation layer**, not a
replacement for `logs/`. The projector opens `logs/` read-only, applies
field-level reconciliation (per `research_projection/ownership.py`), and
writes new research records here. It never modifies, moves, renames or
disables anything under `logs/`, and it never changes trading behaviour.

## 2. Layout

```
research_data/
├── market_context/<SYMBOL>/<date>.jsonl   research_market_context_v1
├── live/
│   ├── observation/<SYMBOL>/<date>.jsonl  research_observation_v1
│   ├── opportunity/<SYMBOL>/<date>.jsonl  research_opportunity_v1
│   ├── decision/<SYMBOL>/<date>.jsonl     research_decision_v1
│   ├── execution/<SYMBOL>/<date>.jsonl    research_execution_v1
│   └── outcome/<SYMBOL>/<date>.jsonl      research_outcome_v1
├── shadow/
│   ├── plan/<SYMBOL>/<date>.jsonl         research_shadow_plan_v1
│   ├── open/<SYMBOL>/<date>.jsonl         research_shadow_open_v1
│   ├── progress/<SYMBOL>/<date>.jsonl     research_shadow_progress_v1
│   └── close/<SYMBOL>/<date>.jsonl        research_shadow_close_v1
└── manifest/
    ├── schema.json            research schemas + lineage rules
    ├── source_map.json        research area -> source dataset mapping
    ├── field_ownership.json   authoritative field rules (drop/keep lists)
    └── projection_state.json  cursors + run history (idempotency state)
```

LIVE and SHADOW are **separate areas with separate schemas**. They are never
merged into one dataset, but both preserve their canonical identifiers so
research can compare them deliberately.

## 3. Record shape

Every research record is flat (source fields retained under their source
names, minus the registry drop lists) plus a reserved `research_*` envelope:

| Envelope field | Meaning |
|---|---|
| `research_id` | deterministic identity: `hash(dataset, source path, source fingerprint)` — same source record always resolves to the same research id |
| `research_schema` / `research_area` | schema name and area this record belongs to |
| `projector_version` / `projected_at_utc` | projection metadata |
| `source_schema` | the source record's `schema_version` (captured before drop) |
| `research_source` | `{dataset, path (logs/...), fingerprint, owner}` — where this record came from |
| `research_lineage` | identifiers preserved **verbatim** + `link_status` + `canonical_root_valid` |
| `research_reconciliation` | present only when a reconciliation event occurred (value conflicts, outcome-boundary enforcement, same-bar reconciliation, unlinked fills) |

`link_status` semantics (honest lineage — never fabricated):

* `resolved` — record joined to at least one contributor via exact identifier match
* `unresolved` — join attempted, no partner found (the record is still emitted)
* `self_only` — self-contained record; no join attempted

## 4. Core invariants

1. **Read-only source.** `logs/` is opened read-only. Verified by test.
2. **No fabricated lineage.** Empty canonical roots stay empty; records are
   joined only on exact identifier matches (entity_id / decision_id /
   correlation_id / trade_id / same-bar `(symbol, bar_time)`). Timestamps,
   symbols and similar-looking ids never create relationships.
3. **Identifier semantics preserved.** `observation_id`, `entity_id`,
   `correlation_id`, `decision_id`, `trace_id`, `plan_id`,
   `shadow_trade_id`, `trade_id` are different id spaces and are never
   treated as interchangeable.
4. **Field-level reconciliation.** "Merge" means per-field ownership (see
   `manifest/field_ownership.json`); disagreements are recorded in
   `research_reconciliation`, never silently overwritten.
5. **Outcome boundary.** Outcome fields (pnl, exit facts) never appear in
   `live/execution` records; violations are stripped and recorded.
6. **Idempotency.** Byte-offset cursors (per source file, stored in
   `manifest/projection_state.json`) plus content-hash identities: running
   the projector repeatedly emits nothing for unchanged source bytes. A
   changed source record yields a new fingerprint — the change is
   detectable, the previous record is retained.
7. **Runtime isolation.** No strategy/decision/risk/execution/broker/shadow
   code path reads or depends on this layer.

## 5. Running the projector

```bash
python -m research_projection               # project new bytes (backfills on first run)
python -m research_projection --no-backfill # only bytes appended after this run
python -m research_projection --logs-root <path> --research-root <path>
```

The command prints a JSON summary (per-area counts, file cursors, anomalies)
and refreshes the manifest. It is safe to run while the bot operates: it only
reads `logs/` and appends under `research_data/`.
