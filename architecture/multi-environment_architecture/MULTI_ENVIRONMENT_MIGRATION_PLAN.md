# Multi-Environment Migration Plan

## Version: 1.0
## Date: 2026-07-23

---

## Principle

Every phase is independently deployable, testable, and reversible. No phase requires stopping the live trading pipeline. The system remains a working single-environment bot at every intermediate state.

---

## Phase 0 — Observability Foundation (LOW RISK)

**Goal:** Add `environment_id` to all new records without changing any logic.

**Changes:**
- Add `environment_id: str = "default"` field to:
  - decision_audit records
  - decision_trace records  
  - decision_ledger records
  - execution_result records
  - trade_truth records
  - trade_journal records
- All existing code produces `"default"` — no behaviour change
- Old records without the field are implicitly `"default"` when read

**Test:** All existing tests pass unchanged. New records contain the field.

**Risk:** None — additive field, backward-compatible reads.

**Benefit:** Enables future per-environment filtering in Athena immediately.

---

## Phase 1 — PolicyProfile Dataclass (LOW RISK)

**Goal:** Extract all environment-specific config into a named structure.

**Changes:**
- Create `PolicyProfile` frozen dataclass (mirrors config values)
- Create `profiles/default.py` containing current config values
- `RiskManager.__init__()` accepts a `PolicyProfile` instead of reading `config` globals
- `runtime_guard_chain` accepts profile parameter
- Current live_scanner creates ONE profile from existing config and passes it

**Test:** Identical behaviour — same values, different source. All tests pass.

**Risk:** Low — structural refactor, no logic change. If profile loading fails, falls back to config globals.

**Benefit:** Policy is now an explicit object rather than implicit globals. Ready for multi-instantiation.

---

## Phase 2 — Environment Class (MEDIUM RISK)

**Goal:** Wrap current per-symbol state into an `Environment` class.

**Changes:**
- Create `Environment` class containing:
  - `env_id: str`
  - `profile: PolicyProfile`
  - `trade_manager: TradeStateManager`
  - `risk_manager: RiskManager`
  - `execution: MT5Execution`
  - `cooldown: TradeCooldownManager`
  - `daily_limit: DailyTradeLimitManager`
- Current `_LiveSymbolState` becomes a data source; `Environment` becomes the decision maker
- live_scanner creates ONE environment with `env_id="default"`

**Test:** Behaviour unchanged — same decisions, same persistence. Environment is a wrapper.

**Risk:** Medium — touches runtime orchestration. Thorough integration testing required.

**Benefit:** The environment abstraction exists as a concrete object. Ready for duplication.

---

## Phase 3 — Multi-Profile Loading (MEDIUM RISK)

**Goal:** Load multiple environment profiles at startup.

**Changes:**
- Configuration supports a list of environment definitions
- Scanner creates N environments from config (initially N=1)
- Startup recovery runs per-environment (filter by magic number)
- Each environment has its own magic number

**Test:** With N=1, identical to current. With N=2, both environments receive opportunities independently.

**Risk:** Medium — introduces multi-environment loop. Must verify no cross-contamination between environment states.

**Benefit:** Multiple environments can be configured and activated.

---

## Phase 4 — Fan-Out Evaluation (MEDIUM RISK)

**Goal:** Each cycle's OpportunityAssessment is evaluated by ALL active environments.

**Changes:**
- After `run_new_engine()` produces an opportunity:
  ```python
  for env in active_environments:
      env.evaluate_opportunity(opportunity, candles, bid, ask)
  ```
- Each environment independently runs: policy → risk → guards → execution
- Persistence records include `environment_id` from Phase 0

**Test:** With 1 environment = current behaviour. With 2 environments, both independently process the same opportunity.

**Risk:** Medium — parallel evaluation must not have shared mutable state. Position tracking must be correctly isolated by magic number.

**Benefit:** The full multi-environment runtime is operational.

---

## Phase 5 — Per-Environment Broker Sessions (HIGH RISK)

**Goal:** Support environments with different broker accounts/logins.

**Changes:**
- Each environment owns a `BrokerSession` (MT5 connection or abstraction)
- Same-account environments share an MT5 connection (different magic numbers)
- Different-account environments have separate connections
- Position recovery scoped to magic number per environment

**Test:** Requires multiple MT5 accounts (demo). Cannot test with a single broker login.

**Risk:** High — multiple broker connections, reconnection logic per-environment.

**Benefit:** Prop firm accounts (FTMO, The5ers) can be connected independently.

---

## Phase 6 — Persistence Restructure (LOW RISK)

**Goal:** Move from flat storage to environment-partitioned paths.

**Changes:**
- S3 keys include `env={environment_id}` partition
- Local JSONL files include environment prefix
- Athena table definitions updated with partition columns
- Reader functions accept `environment_id` filter

**Test:** New data flows to new paths. Old data remains readable (backward-compatible fallback).

**Risk:** Low — additive path restructure. Old paths still readable.

**Benefit:** Clean per-environment data separation for analytics.

---

## Summary Timeline

| Phase | Risk | Dependency | Estimated Effort |
|-------|------|-----------|-----------------|
| 0 — env_id field | LOW | None | 1-2 hours |
| 1 — PolicyProfile | LOW | Phase 0 | 3-4 hours |
| 2 — Environment class | MEDIUM | Phase 1 | 4-6 hours |
| 3 — Multi-profile loading | MEDIUM | Phase 2 | 2-3 hours |
| 4 — Fan-out evaluation | MEDIUM | Phase 3 | 4-6 hours |
| 5 — Per-env broker | HIGH | Phase 4 | 8-12 hours |
| 6 — Persistence restructure | LOW | Phase 4 | 2-3 hours |

**Total estimated effort:** 24-36 hours of implementation across 6 independent phases.

**Recommended order:** 0 → 1 → 2 → 3 → 4 → 6 → 5 (defer broker separation until prop accounts are ready)

---

## Rollback Strategy

Each phase has a clear rollback:
- Phase 0: Remove field (or leave — harmless)
- Phase 1: Revert to config globals
- Phase 2: Remove Environment wrapper
- Phase 3: Config `environments = [default]` (single)
- Phase 4: Skip fan-out (single env evaluation)
- Phase 5: Single broker connection
- Phase 6: Legacy path fallback in readers
