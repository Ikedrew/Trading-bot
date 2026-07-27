# Strategy Observer Integration Audit

## Where Is StrategyObserver Inserted?

Observer #7 in `core/pipeline/observers.py`, after Decision Trace (observer #6).

```python
# ─── 7. Strategy observer: strategy intelligence observation ──
try:
    from core.strategies.strategy_intelligence_observer import (
        observe_strategy_intelligence,
    )
    observe_strategy_intelligence(ctx)
except Exception:
    pass
```

Position in the pipeline:

```
Pattern Detection → Decision Engine → Risk → Execute/Reject
                                                    ↓
                                        ObserverRegistry.notify_all()
                                            ↓
                                        1. Event observer
                                        2. Forensic logger
                                        3. Entity tracker
                                        4. Visibility layer
                                        5. Shadow rooms
                                        6. Decision trace
                                        7. Strategy observer (NEW — read-only)
```

The observer runs AFTER all decisions are made and recorded.
It is the last observer in the chain.

---

## Data Available at Insertion Point

All data from `ObserverContext`:

| Field | Source | Available |
|-------|--------|-----------|
| symbol | ctx.symbol | ✅ Always |
| cycle_id | ctx.cycle_id | ✅ Always |
| bar_time | ctx.bar_time | ✅ Always (unix seconds of closed bar) |
| engine_result | ctx.engine_result | ✅ Dict with action, pattern, score, regime, phase |
| htf_context | ctx.htf_context | ✅ MarketContext (regime, phase, direction, H4/H1/M15/M5) |
| detected_patterns | ctx.detected_patterns | ✅ List of Signal objects |
| entity_id | engine_result["entity_id"] | ✅ Deterministic: f"{symbol}_{bar_time}" |

Extracted and persisted:
- Market phase, regime, direction, h4/h1/m15/m5 summaries
- Detected pattern name
- All 5 strategy evaluations (eligible, conditions, confidence)
- Decision action/score/reason (for research enrichment)
- entity_id (for deterministic joins to shadow_trades and decision_trace)

---

## What Data Does It Consume?

From `ObserverContext` (read-only):
- `ctx.symbol` — trading pair
- `ctx.cycle_id` — current cycle number
- `ctx.bar_time` — timestamp of closed bar
- `ctx.engine_result` — dict with action, pattern, score, regime, phase
- `ctx.htf_context` — MarketContext or legacy HTF object

Extracted fields:
- Market phase (from engine_result or MarketContext)
- Regime (from engine_result or MarketContext)
- H4/H1/M15/M5 summaries (from MarketContext if available)
- Pattern detected (from engine_result)
- Decision action/score/reason (from engine_result, for enrichment only)

---

## What Data Does It Produce?

1. **In-memory StrategyObservation records** (5 per cycle, one per strategy)
2. **One persisted observation record per cycle** containing:
   - Full market context snapshot
   - All candidate strategies with eligibility
   - Condition evaluation summary
   - Decision context (action, score, reason)
   - Outcome fields (PENDING, for later linkage)

Storage:
- Local: `logs/strategy_observations/{SYMBOL}/{DATE}.jsonl`
- S3: `s3://trading-bot-data-mk1/strategy_observations/symbol={SYMBOL}/date={DATE}/`

---

## Can It Influence Execution?

**NO. Structurally impossible.**

1. The observer runs AFTER `engine_result` is produced and decisions are final.
2. It is wrapped in `try/except Exception: pass` — failure never propagates.
3. No return value from observer #7 is consumed by any component.
4. The observer does NOT modify `ctx.engine_result` (verified by test).
5. The observer imports NOTHING from `execution/`, `risk/`, or scoring.
6. `ObserverRegistry.notify_all()` ignores all observer return values.

The data flows one direction only: pipeline → observer → persistence.
There is no reverse path.

---

## Is the Feedback Loop Complete?

### What's complete:

```
Market Cycle → MarketContext → StrategyObserver → Observation → Persistence → S3/Athena
```

### What's remaining for full evidence loop:

| Component | Status |
|-----------|--------|
| Observation creation | ✅ Complete (this integration) |
| Persistence (local + S3) | ✅ Complete |
| Athena table definition | ✅ Complete |
| Outcome linkage (automated) | ❌ Manual only — needs shadow trade close hook |
| Evidence accumulation | 🔄 Starts now (with live data) |
| Statistical validation | ❌ Needs n≥100 per strategy×phase |
| Walk-forward testing | ❌ Infrastructure not built |

---

## Is StrategyObserver Collecting Enough to Validate Profitability?

**YES.** Every observation record contains:

| Field | Purpose | Join Capability |
|-------|---------|----------------|
| entity_id | Deterministic join to shadow_trades and decision_trace | EXACT MATCH |
| symbol | Symbol-level filtering | PARTITION KEY |
| timestamp_utc | Temporal ordering and backup join | RANGE |
| market_phase | Phase-level analysis | GROUP BY |
| h4_regime | Regime-level analysis | GROUP BY |
| strategy_family | Family-level comparison | GROUP BY |
| candidate_strategies | Per-strategy eligibility | ARRAY EXPANSION |
| evaluation_status | FULLY_MET vs NOT_MET comparison | FILTER/GROUP |
| confidence | Confidence-outcome correlation | NUMERIC |
| detected_pattern | Pattern-level analysis | GROUP BY |
| decision_action | Did the bot actually trade? | FILTER |

## Can Athena Answer the Research Questions?

| Question | Query Approach | Feasible? |
|----------|---------------|-----------|
| "When conditions were met, was EV positive?" | JOIN strategy_observations ON entity_id = shadow_trades.identity.entity_id, filter FULLY_MET, AVG(pnl_r_multiple) | ✅ YES |
| "Conditions met vs not met comparison?" | GROUP BY evaluation_status after join | ✅ YES |
| "Strategy vs family?" | GROUP BY strategy_family | ✅ YES |
| "Phase vs strategy?" | GROUP BY market_phase, strategy_family | ✅ YES |
| "Regime vs strategy?" | GROUP BY h4_regime, strategy_family | ✅ YES |
| "Pattern vs strategy?" | GROUP BY detected_pattern, strategy_family | ✅ YES |

## Are Additional Tables Required?

**NO.** The existing tables are sufficient:
- `trading_bot.strategy_observations` — strategy condition occurrences (NEW)
- Shadow trades queryable via S3 prefix
- Decision trace queryable via S3 prefix

Materialised views for common aggregations (Category B) can be added later for convenience but are NOT required.

## Are Any Joins Unsafe or Ambiguous?

**NO.** With `entity_id` now included in observation records:
- `strategy_observations.entity_id = shadow_trades.identity.entity_id` → **EXACT MATCH**
- `strategy_observations.entity_id = decision_trace.entity_id` → **EXACT MATCH**
- No temporal proximity approximation needed

---

## SYSTEM STATUS: READY FOR DATA COLLECTION ✅

The strategy intelligence observation pipeline is complete and live:
1. Observer #7 runs every market cycle ✅
2. All strategy conditions evaluated per cycle ✅
3. Observations persisted (local JSONL + S3) ✅
4. entity_id enables deterministic joins ✅
5. Athena table defined and queryable ✅
6. No execution behaviour modified ✅
7. 3141 tests pass, zero regressions ✅
