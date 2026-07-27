# Multi-Environment Trading Platform Architecture

## Version: 1.0 (Design Specification)
## Date: 2026-07-23
## Status: ARCHITECTURAL DESIGN — NOT YET IMPLEMENTED

---

## 1. Core Principle

The trading platform operates as a **single market intelligence engine** that produces universal opportunity assessments. Multiple independent trading environments consume these assessments and apply their own policies, risk rules, and execution constraints.

```
┌─────────────────────────────────────────────────────┐
│              MARKET INTELLIGENCE ENGINE              │
│                    (Universal)                       │
│                                                     │
│   Market Data → Features → Patterns → Strategy →    │
│   Scoring → Market State → OpportunityAssessment    │
└───────────────────────┬─────────────────────────────┘
                        │
              OpportunityAssessment
              (frozen, immutable)
                        │
         ┌──────────────┼──────────────┐
         │              │              │
         ▼              ▼              ▼
┌─────────────┐ ┌─────────────┐ ┌─────────────┐
│  Retail_v1  │ │  FTMO_v1    │ │ The5ers_v1  │
│             │ │             │ │             │
│  Policy     │ │  Policy     │ │  Policy     │
│  Risk       │ │  Risk       │ │  Risk       │
│  Execution  │ │  Execution  │ │  Execution  │
│  Persistence│ │  Persistence│ │  Persistence│
└─────────────┘ └─────────────┘ └─────────────┘
```

---

## 2. Architectural Boundary

**OpportunityAssessment** is the canonical handoff between intelligence and environments.

### Above the Boundary (Universal — computed once per symbol per cycle)

| Component | Responsibility |
|-----------|---------------|
| MT5 Data Feed | Raw candle/tick ingestion |
| Timeframe Cache | H4/H1/M15 candle management |
| MarketContextBuilder | Multi-TF synthesis |
| Pattern Detection | Candlestick pattern identification |
| Strategy Classification | Regime → eligibility → strategy selection |
| 10-Factor Scoring | Weighted composite quality score |
| Market State Engine | STRUCTURED / TRANSITIONAL / CHOP classification |

**Output:** `OpportunityAssessment` (frozen dataclass — never modified after construction)

### Below the Boundary (Per-Environment — computed independently)

| Component | Responsibility |
|-----------|---------------|
| Probability Estimator | p_success calculation (may use env-specific calibration) |
| Expected Value | EV = p × reward - (1-p) × risk |
| Execution Policy | trade_allowed gate (thresholds from profile) |
| Risk Manager | SL/TP geometry + volume sizing (from profile limits) |
| Runtime Guard Chain | Daily limits, cooldown, exposure, prop rules |
| Execution | Broker interaction (env owns broker session) |
| Trade Management | Break-even, trailing, partial TP |
| Persistence | Trade journal, trade truth (tagged with env_id) |

---

## 3. Runtime Model

### Recommended: Fan-Out Architecture

```
Scanner Loop (one per symbol)
  ↓ produces candles, detects patterns, builds context
  ↓
run_new_engine() → OpportunityAssessment
  ↓
for each active_environment:
    env.evaluate(opportunity)  → ACCEPT / REJECT
    if ACCEPT:
        env.risk_evaluate(opportunity) → OrderIntent or REJECT
        if OrderIntent:
            env.guard_chain(intent) → PASS / BLOCK
            if PASS:
                env.execute(intent) → broker
```

**Why fan-out, not multiple scanners:**
- Market data is expensive to fetch (MT5 connection, candle copies)
- Pattern detection is deterministic — running it N times wastes CPU
- One intelligence output, N policy evaluations
- Scales to 50+ environments without proportional MT5 load

**Thread safety:** Environments are independent. No shared mutable state between environments. Each owns its positions, cooldowns, and exposure tracking.

---

## 4. Environment Lifecycle

```
CREATED → INITIALISING → ACTIVE → PAUSED → STOPPED → ARCHIVED
                ↑                    │
                └────────────────────┘ (resume)
```

| State | Behaviour |
|-------|-----------|
| CREATED | Profile loaded, no broker connection |
| INITIALISING | Broker connection, position recovery, state restoration |
| ACTIVE | Evaluates opportunities, places trades |
| PAUSED | Receives opportunities but does not execute (monitoring only) |
| STOPPED | No evaluation, no execution, state preserved |
| ARCHIVED | Historical data retained, environment removed from runtime |

---

## 5. What Each Environment Owns

| Owned Resource | Shared? | Reason |
|----------------|---------|--------|
| Policy Profile | No | Different rules per env |
| Broker Session | No | Different accounts/credentials |
| Position State | No | Independent portfolios |
| Trade History | No | Per-env P&L tracking |
| Cooldown State | No | Independent trade frequency |
| Exposure State | No | Independent risk limits |
| Drawdown Tracking | No | Independent equity curves |
| Magic Number | No | Position identification |
| Persistence (tagged) | Shared bucket, partitioned | Cost efficiency |

| Shared Resource | Why Shared |
|-----------------|-----------|
| Candle Data | Same market reality |
| Pattern Detection | Deterministic per bar |
| OpportunityAssessment | One analysis, many consumers |
| Score Calibration Curve | Empirical market truth (not account-specific) |
| Configuration Schema | Structural consistency |

---

## 6. Data Flow

```
MT5 Feed
  │
  ├── copy_rates_closed(EURUSD, M5, 300)
  │
  ▼
Candle Buffer ──────────────────────────────────────┐
  │                                                  │
  ├── HTF Cache (H4/H1/M15)                         │
  │     └── MarketContext                            │
  │                                                  │
  ├── Pattern Detection                              │
  │     └── [Signal, Signal, ...]                    │
  │                                                  │
  ├── Strategy Classification                        │
  │     └── ActivationResult                         │
  │                                                  │
  ├── Scoring                                        │
  │     └── components, score_neutral, score_strategy│
  │                                                  │
  ├── Market State                                   │
  │     └── MarketStateResult                        │
  │                                                  │
  └──▶ OpportunityAssessment ◀──────────────────────┘
           │
           │ (broadcast to all active environments)
           │
           ├──▶ Environment: Retail_Growth_v1
           │     ├── PolicyEvaluator.evaluate(opp) → ACCEPT
           │     ├── RiskManager.evaluate(opp, candles, bid, ask) → Intent
           │     ├── GuardChain.evaluate(intent) → PASS
           │     ├── Execution.execute(intent) → Fill
           │     └── Persistence.record(opp_id, env_id, decision, outcome)
           │
           ├──▶ Environment: FTMO_v1
           │     ├── PolicyEvaluator.evaluate(opp) → REJECT (daily limit)
           │     └── Persistence.record(opp_id, env_id, decision="REJECTED")
           │
           └──▶ Environment: The5ers_v1
                 ├── PolicyEvaluator.evaluate(opp) → ACCEPT
                 ├── RiskManager.evaluate(opp, ...) → REJECT (min SL)
                 └── Persistence.record(opp_id, env_id, decision="RISK_REJECT")
```

---

## 7. Key Design Decisions

### 7.1 EV Calculation: Universal or Per-Environment?

**Per-environment.** The EV formula is universal mathematics (`p × reward - (1-p) × risk`), but the inputs differ:
- **Risk** depends on position sizing (which depends on account balance)
- **Reward** depends on TP rules (which may differ per profile)
- **p_success** is market-derived but the acceptance threshold differs

Therefore: compute EV per environment using the same probability estimate but environment-specific SL/TP geometry.

### 7.2 Position Sizing: Who Owns It?

**The environment.** Position sizing depends on:
- Account balance (environment-specific)
- Risk percentage (profile-specific)
- Max lot size (broker/prop-specific)
- Correlation limits (portfolio-specific)

### 7.3 Trade Management: Shared or Independent?

**Independent.** Break-even triggers, trailing parameters, and partial TP rules may differ between a growth retail account and a prop firm account. Each environment owns its TradeStateManager instance.

### 7.4 Broker Connection: Shared or Independent?

**Depends on broker architecture:**
- Same broker, same account → shared MT5 connection, different magic numbers
- Same broker, different accounts → separate login sessions
- Different brokers → completely separate connections

**Recommendation:** Each environment owns a logical broker interface. If two environments share the same MT5 login, they share the connection but use different magic numbers for position identification.

---

## 8. Comparison with Current Architecture

| Aspect | Current | Target |
|--------|---------|--------|
| Environments | 1 (implicit) | N (explicit) |
| Config | Single flat file | Profile-based |
| Execution | Single magic number | Per-environment magic |
| Persistence | Global (no env tag) | Partitioned by env_id |
| Guard chain | Global config | Profile-loaded |
| Position tracking | Single TradeStateManager | Per-environment TSM |
| Analytics | Global aggregation | Per-environment + global |
| OpportunityAssessment | Exists (correct) | Unchanged (validated) |

---

## 9. Migration Safety

The migration from single-environment to multi-environment can happen **incrementally** without breaking the live pipeline:

1. **Phase 0 (today):** Add `environment_id` field to persistence records
2. **Phase 1:** Extract policy config into a `PolicyProfile` dataclass
3. **Phase 2:** Wrap current runtime in an `Environment` class
4. **Phase 3:** Support loading multiple profiles at startup
5. **Phase 4:** Fan-out OpportunityAssessment to multiple environments
6. **Phase 5:** Per-environment broker connections

Each phase is independently deployable and testable. No phase requires stopping the live system.
