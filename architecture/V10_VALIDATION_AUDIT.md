# V10 Validation Audit — End-to-End Pipeline

**Date:** 2026-07-27
**Auditor:** Automated validation
**Result:** V10 IS the sole decision authority with documented exceptions

---

## Pipeline Execution Verification

| # | Layer | Called | Returns Result | Stored in Context | Later Replaced? | Rebuilt? |
|---|---|---|---|---|---|---|
| 1 | MarketState | ✓ | ✓ | ✓ | No | No |
| 2 | Opportunity | ✓ | ✓ | ✓ | No | No |
| 3 | Strategy | ✓ | ✓ | ✓ | No | No |
| 4 | Horizon | ✓ | ✓ | ✓ | No | No |
| 5 | Entry | ✓ | ✓ | ✓ | No | No |
| 6 | Risk | ✓ | ✓ | ✓ | No | No |
| 7 | Execution | ✓ | ✓ | ✓ | No | No |
| 8 | DecisionContext | ✓ | ✓ | — (is the context) | No | No |
| 9 | Persistence | ✓ | ✓ | — | — | — |

**All 7 stages execute in strict sequence. No stage is skipped. No outputs are overwritten.**

---

## Layer-by-Layer Audit

### 1. Market Understanding — SOLE SOURCE ✓
- Built once in `scanner_adapter.py` via `build_market_understanding()`
- No duplicate builders found downstream
- HTF context/market context builders exist but are observational (legacy shadow pipeline) — they do NOT feed back into V10

### 2. Timeframe Authority — ENFORCED ✓
- `H4State` → `H1State` → `M15State` → `M5State` hierarchy in `V10MarketState`
- `M5State` has NO direction-authority fields (verified by test)
- `_evaluate_structure()` in opportunity engine uses H1 for direction, never M5
- Entry direction comes from `OpportunityAssessment.directional_bias` (H1-derived)

### 3. Market State — BUILT ONCE ✓
- `build_v10_market_state()` called exactly once per pipeline invocation
- No secondary copies or rebuilds found
- All downstream engines receive the same `V10MarketState` instance

### 4. Opportunity — SINGLE AUTHORITY ✓
- `assess_opportunity()` is the only opportunity detector for V10
- Legacy `create_opportunity()` (shadow opportunity layer) runs separately but is marked "observation only — never affects trading decisions"
- No legacy candidate filtering affects V10 path

### 5. Strategy — SELECTED ONCE ✓
- `select_strategy()` produces final `StrategyDecision`
- Nothing downstream modifies `strategy_family`, `confidence`, or `reasoning`
- Risk engine uses strategy for min R:R selection but never changes it

### 6. Horizon — PRODUCED ALWAYS ✓
- `assess_horizon()` always executes regardless of strategy result
- **Finding: `PERMITTED_HORIZONS = ["SCALP"]` exists in config but is NOT consumed by V10 pipeline.** The V10 horizon engine produces SCALP/INTRADAY/EXTENDED freely. PERMITTED_HORIZONS is only used by `core/horizon/execution_authority.py` (legacy) which is NOT called in the V10 path.
- V10 horizon is unrestricted ✓

### 7. Entry — USES FULL CONTEXT ✓
- `build_entry_decision()` consumes opportunity + strategy + horizon
- M5 provides confirmation_candle/rejection but never overrides direction
- Stop placement from H1/M15 structure (structural invalidation)

### 8. Risk — GATE ONLY ✓
- `assess_risk()` approves or rejects — never modifies direction/strategy/horizon
- Position sizing uses stop distance + risk percentage
- Strategy-aware min R:R (BREAKOUT requires 2.0, others 1.5)
- Horizon-aware size reduction (EXTENDED = 0.75× multiplier)

### 9. Execution — OPERATOR ONLY ✓
- `build_execution_decision()` checks broker state — never modifies trade idea
- Maps entry_method → order_type (LIMIT/MARKET/STOP)
- Preserves direction, stop, target exactly from EntryDecision
- Spread guard: blocks if spread > 30% of stop distance

### 10. DecisionContext — PROGRESSIVE ACCUMULATION ✓
- Each `.with_*()` returns a new frozen instance
- Previous stages always accessible
- `completed_stages` tuple grows monotonically
- `terminal_stage` set automatically on failure

### 11. Terminal Output — V10 ONLY ✓
- `format_v10_decision()` uses `V10DecisionContext`
- Output shows: [V10 MARKET UNDERSTANDING], [V10 OPPORTUNITY], [V10 STRATEGY], etc.
- No Composite Score, Grade, Threshold, Neutral Score in output

### 12. Persistence — COMPLETE CHAIN ✓
- `persist_v10_full()` writes both V10 decision record AND legacy-compatible ledger entry
- Record contains all 7 layers + rejection_stage + rejection_reason
- Both EXECUTE and NO_TRADE decisions are persisted

### 13. S3 — NOT YET MIGRATED ⚠️
- V10 decision records write to local JSONL only
- S3 mirror uses legacy event_stream infrastructure (still functional for trade events)
- V10 decisions are NOT yet mirrored to S3

### 14. Research — CONSUMABLE ✓
- V10 records contain `strategy_family`, `horizon`, `entry_method`, `rejection_stage`
- Research can query: "What happened after MEAN_REVERSION?" / "Which rejections were correct?"
- Records are in `logs/v10_decisions/{SYMBOL}/{DATE}.jsonl`

---

## Legacy Override Audit

### Search Results for Legacy Concepts in Runtime Path:

| Concept | Found in Runtime? | Can Influence V10? |
|---|---|---|
| `composite_score` | NOT FOUND in core/ | No |
| `candidate_score` | NOT FOUND in core/ | No |
| `neutral_score` | NOT FOUND in core/ | No |
| `strategy_score` | NOT FOUND in core/ | No |
| `grade` | NOT FOUND in core/ | No |
| `pattern_gate` | NOT FOUND in core/ | No |
| `run_new_engine` | Line 462 of live_scanner | **Guarded by `if _engine_mode == "V10": pass`** — does NOT execute |
| `MIN_SCORE_TO_TRADE` | config.py line ~155 | Only used by legacy `run_new_engine` — never read by V10 |
| `PERMITTED_HORIZONS` | config.py line 308 | Only used by legacy `execution_authority.py` — NOT in V10 path |

**No legacy scoring concept can influence V10 decisions.**

---

## Hidden Override Audit

### After V10 produces its result, what can modify it?

| Code Location | What It Does | Modifies V10 Decision? |
|---|---|---|
| HTF context rebuild (line ~407) | Builds `_new_engine_htf` | NO — observational, feeds legacy path only |
| Market context build (line ~415) | Builds `_market_context` | NO — observational only |
| Shadow opportunity layer (line ~430) | Creates legacy Opportunity objects | NO — "must NEVER affect trading" |
| Bias FSM update (line ~470) | Updates engine_state bias | NO — legacy FSM, not read by V10 |
| Assessment builder (line ~500) | Builds assessment record | NO — observational only |
| Horizon classifier (line ~520) | Legacy horizon classification | NO — different system |
| Runtime guard chain (line ~1028) | `evaluate_runtime_guards()` | **CAN BLOCK execution** but NEVER modifies direction/stop/target |

### Runtime Guard Chain (the only post-V10 gate):

The guard chain at line 1028 can REJECT a trade (spread too wide, position limit, cooldown active). This is correct capital protection — it's equivalent to V10's own execution checks but uses the existing infrastructure. It does NOT:
- Change direction
- Modify stop/target
- Select a different strategy
- Override the opportunity assessment

**Verdict: Guard chain is a safety layer, not a decision override.**

---

## Identified Issues

### Issue 1: Observational Code Still Runs Under V10 ⚠️

Even when `ENGINE_MODE == "V10"`, the following still execute:
- HTF context build
- Market context build
- Shadow opportunity layer
- Bias FSM update
- Assessment builder
- Horizon classifier

These are all marked "observational — never affects trading" but they consume CPU/time unnecessarily. They should be gated behind `if _engine_mode != "V10":` for efficiency.

**Impact:** Performance only. No correctness issue.

### Issue 2: S3 Mirror Not Migrated ⚠️

V10 decisions are persisted locally but not mirrored to S3. The legacy event_stream handles trade execution events, but V10-specific decision records (the full reasoning chain) are local-only.

**Impact:** Research data not cloud-backed. No correctness issue.

### Issue 3: Account/Broker Context Uses Hardcoded Defaults ⚠️

The scanner adapter uses `balance=10000, margin=10000` defaults instead of reading from MT5 account info. This means:
- Position sizing is approximate
- Margin checks always pass
- Daily loss tracking not connected to real P&L

**Impact:** Risk model is not production-accurate. Requires MT5 integration.

---

## Dependency Map: Market Data → Research

```
MT5 Data Feed
    │
    ▼
live_scanner.py (bar provision)
    │
    ▼
run_v10_cycle() [scanner_adapter.py]
    │
    ├── build_market_understanding() [v3_shadow/builders.py]
    │       │
    │       ▼
    ├── build_v3_market_context() [v3_shadow/context_builders.py]
    │       │
    │       ▼
    └── V10Pipeline.process() [v10/pipeline.py]
            │
            ├── build_v10_market_state() → V10MarketState
            ├── assess_opportunity() → OpportunityAssessment
            ├── select_strategy() → StrategyDecision
            ├── assess_horizon() → HorizonDecision
            ├── build_entry_decision() → EntryDecision
            ├── assess_risk() → RiskDecision
            └── build_execution_decision() → ExecutionDecision
                    │
                    ▼
            PipelineResult + V10DecisionContext
                    │
                    ├── format_v10_decision() → Terminal output
                    ├── persist_v10_full() → logs/v10_decisions/*.jsonl
                    └── build_v10_ledger_entry() → Decision ledger
                            │
                            ▼
                    Research Engine (consumable)
```

---

## Final Verdict

### V10 IS the sole runtime decision authority.

**Confirmed:**
- ✓ All 7 stages execute in sequence
- ✓ No stage is skipped or rebuilt
- ✓ No legacy scoring influences decisions
- ✓ `run_new_engine` is fully guarded (does not execute under V10)
- ✓ DecisionContext accumulates progressively
- ✓ Terminal output uses V10 format only
- ✓ Persistence captures complete chain
- ✓ Direction comes from H1 authority (never M5)
- ✓ Guard chain is safety-only (blocks, never modifies)
- ✓ `PERMITTED_HORIZONS` does NOT affect V10

**Exceptions (non-blocking):**
- ⚠️ Observational legacy code still runs (HTF/MarketContext/Shadow) — performance issue only
- ⚠️ S3 mirror not migrated — local persistence works
- ⚠️ Account/broker context uses defaults — risk sizing approximate
- ⚠️ Legacy guard chain is the final safety gate (correct architecture, but duplicates V10's own execution checks)

**No code path exists that can override V10's direction, strategy, horizon, stop, or target between pipeline output and broker submission.**
