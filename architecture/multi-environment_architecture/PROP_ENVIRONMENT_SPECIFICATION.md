# Prop Environment Specification

## Document Version: 1.0
## Status: ARCHITECTURAL SPECIFICATION
## Scope: Account/Environment Rules Layer

---

## 1. Overview

### Why an Environment Abstraction Exists

A trading system that operates across multiple account types faces a fundamental tension: the market analysis that identifies opportunities is universal, but the rules governing whether those opportunities can be acted upon vary dramatically between account contexts.

A retail growth account may tolerate aggressive position sizing and accept higher drawdown. A prop firm evaluation account operates under contractual constraints where a single violation terminates the account. A funded prop account prioritises capital preservation above opportunity capture.

The environment abstraction exists to resolve this tension by establishing a clean boundary between market intelligence and account policy.

### Why Trading Logic Must Be Separated From Account Constraints

If account constraints are embedded within the trading engine:

- Adding a new account type requires modifying strategy code
- A rule change at one prop firm risks breaking logic for another
- Testing becomes combinatorial (every strategy × every account type)
- The system cannot answer "what would the engine do without these constraints?"

Separation ensures the trading engine remains a pure function of market state, while account-specific behaviour is expressed through configuration.

### How One Core Engine Supports Multiple Environments

```
┌─────────────────────────────────────────────────┐
│            CORE TRADING ENGINE                   │
│                                                  │
│   Market Data → Analysis → Scoring →            │
│   Probability → Opportunity Assessment          │
│                                                  │
│   Output: Trade Proposal                        │
│   (symbol, direction, confidence, evidence)     │
└────────────────────────┬────────────────────────┘
                         │
                         │  Trade Proposal
                         │  (environment-agnostic)
                         │
         ┌───────────────┼───────────────┐
         │               │               │
         ▼               ▼               ▼
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│ Environment │  │ Environment │  │ Environment │
│  Profile A  │  │  Profile B  │  │  Profile C  │
│             │  │             │  │             │
│  Validate   │  │  Validate   │  │  Validate   │
│  Size       │  │  Size       │  │  Size       │
│  Approve    │  │  Reject     │  │  Approve    │
└─────────────┘  └─────────────┘  └─────────────┘
```

The engine produces one universal assessment. Each environment independently decides what to do with it.

---

## 2. Design Principles

### Principle 1: Strategy Independence

The trading engine decides what the market offers. The environment decides what the account permits.

The engine has no knowledge of:
- Account balance
- Drawdown state
- Daily loss tracking
- Position limits
- Contractual obligations
- Account objectives

Its responsibility ends when it produces a trade proposal with associated confidence, evidence, and risk context.

### Principle 2: Configuration Over Code

Different environments are represented through versioned configuration profiles, not through conditional logic branches within the engine.

Adding a new prop firm, a new retail strategy, or a new account type requires only:
1. A new environment profile (configuration)
2. No modification to the core engine
3. No modification to existing environment profiles

### Principle 3: Risk Authority Remains External

The engine generates trade opportunities. It does not enforce risk limits.

Risk enforcement is owned exclusively by the environment layer:
- The engine may estimate probability and expected value
- The environment decides whether the risk is acceptable given current account state
- The environment owns position sizing authority
- The environment owns the final execution permission

### Principle 4: Immutable Trade Proposals

The trade proposal produced by the core engine is frozen and immutable. No downstream component may modify the market analysis. Environments consume the proposal exactly as produced and make independent decisions about it.

### Principle 5: Complete Observability

Every environment decision — approval, rejection, sizing adjustment — must be recorded with full reasoning. An operator must be able to reconstruct why any environment accepted or rejected any opportunity at any point in time.

---

## 3. Environment Profile Definition

An `EnvironmentProfile` is a frozen, versioned configuration that completely defines how an account evaluates trade proposals and manages risk.

### 3.1 Account Information

| Field | Type | Description |
|-------|------|-------------|
| `account_type` | enum | RETAIL, PROP_EVALUATION, PROP_FUNDED, DEMO |
| `starting_balance` | float | Initial capital at environment creation |
| `account_currency` | string | Base currency for P&L calculation |
| `leverage` | float | Maximum leverage available |
| `account_status` | enum | ACTIVE, PAUSED, LOCKED, TERMINATED |

### 3.2 Risk Constraints

| Field | Type | Description |
|-------|------|-------------|
| `max_risk_per_trade_pct` | float | Maximum capital at risk per individual trade |
| `max_daily_loss_pct` | float | Maximum permitted loss in a single trading day |
| `max_total_drawdown_pct` | float | Maximum peak-to-trough equity decline |
| `max_open_positions` | int | Maximum concurrent open positions |
| `max_currency_exposure` | float | Maximum net exposure per currency (lots) |
| `max_lot_size` | float | Absolute maximum order size |
| `max_portfolio_risk_pct` | float | Maximum aggregate risk across all positions |
| `cooldown_after_loss_seconds` | float | Mandatory pause after a losing trade |

### 3.3 Trading Rules

| Field | Type | Description |
|-------|------|-------------|
| `allowed_instruments` | list | Symbols permitted for trading (empty = all) |
| `blocked_instruments` | list | Symbols explicitly prohibited |
| `allowed_sessions` | list | Trading hours (UTC start/end pairs) |
| `weekend_hold_allowed` | bool | Whether positions may be held over weekends |
| `news_trading_allowed` | bool | Whether trading during high-impact news is permitted |
| `minimum_trading_days` | int | Minimum days with at least one trade (challenge compliance) |
| `max_daily_profit_pct` | float | Maximum profit per day before locking (consistency rules) |
| `max_single_day_contribution_pct` | float | Maximum % of total profit from any single day |

### 3.4 Operational Controls

| Field | Type | Description |
|-------|------|-------------|
| `emergency_stop_drawdown_pct` | float | Drawdown level that triggers immediate halt |
| `lock_after_target_reached` | bool | Whether to stop trading after profit target |
| `profit_target_pct` | float | Profit target for challenge completion |
| `conservative_threshold_pct` | float | Profit level at which to reduce risk |
| `conservative_size_reduction` | float | Position size multiplier in conservative mode |
| `flatten_before_weekend` | bool | Close all positions before Friday close |
| `friday_flatten_hour_utc` | int | Hour to begin Friday position flattening |

---

## 4. Runtime Decision Flow

### Complete Flow

```
Market Data (universal)
    │
    ▼
Market Context Analysis (universal)
    │
    ▼
Pattern Detection + Scoring (universal)
    │
    ▼
Opportunity Assessment (universal, frozen)
    │
    ▼
┌──────────────────────────────────────┐
│     ENVIRONMENT VALIDATION LAYER     │
│                                      │
│  1. Account status check             │
│  2. Daily loss limit check           │
│  3. Drawdown limit check             │
│  4. Position count check             │
│  5. Session/hours check              │
│  6. Instrument allowed check         │
│  7. Consistency rule check           │
│  8. Cooldown check                   │
│  9. Weekend protection check         │
│  10. Expected value threshold check  │
│                                      │
│  Result: APPROVED or REJECTED        │
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│        RISK SIZING LAYER             │
│                                      │
│  - Position size from risk budget    │
│  - SL/TP validation                  │
│  - Minimum stop distance check       │
│  - Exposure limit check              │
│                                      │
│  Result: Sized OrderIntent or REJECT │
└──────────────────┬───────────────────┘
                   │
                   ▼
Execution Request (broker-specific — separate architecture)
```

### When a Trade Is Approved

1. Environment validation passes all gates
2. Risk sizing produces a valid OrderIntent
3. The sized intent is forwarded to the execution layer
4. Decision audit records: environment_id, approval reason, sizing details
5. If execution succeeds: position registered, lifecycle tracking begins

### When a Trade Is Rejected

1. The first failing gate produces a structured rejection
2. Rejection includes: guard name, reason, current values vs limits
3. Decision audit records: environment_id, rejection reason, gate that blocked
4. The core engine's opportunity assessment is preserved unchanged
5. Shadow trade may be opened to track what would have happened

### When a Risk Limit Is Reached

1. The environment transitions to a protective state (PAUSED or LOCKED)
2. All new trade proposals are rejected with reason "limit_reached"
3. Existing positions may be managed (break-even, trailing) or flattened
4. Operator is alerted via observability channels
5. Recovery conditions are evaluated each cycle (e.g., daily reset)

---

## 5. Multiple Account Support

### Same Signal, Different Outcomes

The core engine detects an opportunity:

```
Opportunity Assessment:
  Symbol:     EURUSD
  Direction:  BUY
  Score:      0.72
  Confidence: MEDIUM-HIGH
  EV:         +0.000034
  Pattern:    BULLISH_ENGULFING
  Regime:     STRUCTURED
```

This single assessment is evaluated by three environments simultaneously:

#### Retail Growth Account

```
Environment: retail_growth_v1
  Account status: ACTIVE
  Daily loss used: 1.2% (limit: 8%)
  Drawdown: 3.1% (limit: none)
  Open positions: 1 (limit: 5)
  Session: London (allowed)
  
  Decision: APPROVED
  Position size: 0.05 lots (1% risk)
  Reason: All gates passed, within risk budget
```

#### Prop Evaluation Account

```
Environment: prop_evaluation_v1
  Account status: ACTIVE
  Daily loss used: 3.8% (limit: 5%)
  Drawdown: 7.2% (limit: 10%)
  Open positions: 2 (limit: 3)
  Session: London (allowed)
  
  Decision: REJECTED
  Reason: Daily loss 3.8% + proposed risk 0.5% = 4.3% 
          Remaining buffer (0.7%) below minimum safety margin
  Guard: daily_loss_proximity_guard
```

#### Funded Prop Account

```
Environment: prop_funded_v1
  Account status: CONSERVATIVE_MODE (target 80% reached)
  Daily loss used: 0.4% (limit: 4%)
  Drawdown: 1.8% (limit: 6%)
  Open positions: 0 (limit: 2)
  Session: London (allowed)
  
  Decision: APPROVED
  Position size: 0.02 lots (0.25% risk — reduced by conservative mode)
  Reason: All gates passed, conservative sizing applied
```

### Key Insight

The market opportunity is identical. The engine's analysis is identical. Only the environment's policy differs. This separation ensures that improving the engine improves all environments simultaneously, while account-specific constraints never contaminate market intelligence.

---

## 6. Environment Configuration Example

### Retail Growth Profile

```yaml
profile:
  id: "retail_growth_v1"
  version: "1.0.0"
  account_type: "RETAIL"
  description: "Growth-focused retail account with moderate risk tolerance"

account:
  starting_balance: 5000.00
  currency: "USD"
  leverage: 500

risk:
  max_risk_per_trade_pct: 1.0
  max_daily_loss_pct: 8.0
  max_total_drawdown_pct: 20.0
  max_open_positions: 5
  max_currency_exposure_lots: 15.0
  max_lot_size: 1.0
  cooldown_after_loss_seconds: 300

rules:
  allowed_instruments: []  # empty = all available
  allowed_sessions:
    - { start_utc: 7, end_utc: 21 }
  weekend_hold_allowed: false
  news_trading_allowed: true
  minimum_trading_days: 0
  max_daily_profit_pct: 0  # 0 = no cap

controls:
  emergency_stop_drawdown_pct: 15.0
  lock_after_target_reached: false
  flatten_before_weekend: true
  friday_flatten_hour_utc: 20
```

### Prop Evaluation Profile

```yaml
profile:
  id: "prop_evaluation_v1"
  version: "1.0.0"
  account_type: "PROP_EVALUATION"
  description: "Prop firm challenge account — strict compliance required"

account:
  starting_balance: 100000.00
  currency: "USD"
  leverage: 100

risk:
  max_risk_per_trade_pct: 0.5
  max_daily_loss_pct: 5.0
  max_total_drawdown_pct: 10.0
  max_open_positions: 3
  max_currency_exposure_lots: 5.0
  max_lot_size: 5.0
  cooldown_after_loss_seconds: 600

rules:
  allowed_instruments: []
  allowed_sessions:
    - { start_utc: 8, end_utc: 20 }
  weekend_hold_allowed: false
  news_trading_allowed: false
  minimum_trading_days: 5
  max_daily_profit_pct: 2.0
  max_single_day_contribution_pct: 40.0

controls:
  emergency_stop_drawdown_pct: 8.0
  lock_after_target_reached: true
  profit_target_pct: 8.0
  conservative_threshold_pct: 80.0
  conservative_size_reduction: 0.5
  flatten_before_weekend: true
  friday_flatten_hour_utc: 20
```

### Funded Prop Account Profile

```yaml
profile:
  id: "prop_funded_v1"
  version: "1.0.0"
  account_type: "PROP_FUNDED"
  description: "Funded prop account — capital preservation priority"

account:
  starting_balance: 100000.00
  currency: "USD"
  leverage: 100

risk:
  max_risk_per_trade_pct: 0.25
  max_daily_loss_pct: 4.0
  max_total_drawdown_pct: 6.0
  max_open_positions: 2
  max_currency_exposure_lots: 3.0
  max_lot_size: 2.0
  cooldown_after_loss_seconds: 900

rules:
  allowed_instruments: []
  allowed_sessions:
    - { start_utc: 8, end_utc: 18 }
  weekend_hold_allowed: false
  news_trading_allowed: false
  minimum_trading_days: 10
  max_daily_profit_pct: 1.5
  max_single_day_contribution_pct: 30.0

controls:
  emergency_stop_drawdown_pct: 5.0
  lock_after_target_reached: false
  flatten_before_weekend: true
  friday_flatten_hour_utc: 18
```

---

## 7. Relationship With Existing Architecture

### Current System Components

| Component | Relationship to Environment Layer |
|-----------|----------------------------------|
| **DecisionEngine** (new_engine.py) | Produces OpportunityAssessment — environment-agnostic. Unmodified. |
| **OpportunityAssessment** | The handoff object between engine and environment. Frozen, immutable. |
| **ExecutionPolicy** (execution_policy.py) | Currently evaluates EV/RR thresholds — becomes part of environment validation. |
| **RiskManager** (risk/manager.py) | Currently computes SL/TP/sizing — becomes the risk sizing layer within each environment. |
| **RuntimeGuardChain** (runtime_guard_chain.py) | Currently evaluates 10 guards — becomes the environment validation gate chain. |
| **ExecutionOrchestrator** | Receives sized intent from environment — forwards to broker. Separate from environment. |
| **DecisionTrace** | Records engine output — extended with environment_id field. |
| **DecisionAudit** | Records policy decision — extended with environment_id, profile_version. |
| **TradeTruth** | Records execution reality — extended with environment_id. |

### Where the Environment Layer Sits

```
┌─────────────────────────────────────────────┐
│ CORE ENGINE (unchanged)                     │
│   Pattern → Strategy → Score → Assessment   │
└─────────────────────┬───────────────────────┘
                      │
                      │ OpportunityAssessment
                      │
┌─────────────────────┴───────────────────────┐
│ ENVIRONMENT LAYER (this document)           │
│                                             │
│   EnvironmentProfile                        │
│     → ExecutionPolicy (EV/threshold gates)  │
│     → RuntimeGuardChain (10 guards)         │
│     → RiskManager (SL/TP/sizing)            │
│     → Environment State (drawdown, P&L)     │
│                                             │
│   Output: Sized OrderIntent or Rejection    │
└─────────────────────┬───────────────────────┘
                      │
                      │ OrderIntent
                      │
┌─────────────────────┴───────────────────────┐
│ EXECUTION LAYER (separate architecture)     │
│   Broker connection, order submission,      │
│   fill handling, position management        │
└─────────────────────────────────────────────┘
```

---

## 8. Future Extensions

### Multi-Account Manager

A higher-level orchestrator that manages multiple active environments simultaneously, allocating opportunities across accounts based on priority, capacity, and risk budget.

### Portfolio Allocation

Cross-environment awareness for operators managing total capital across multiple accounts. Ensures aggregate exposure across all environments remains within personal risk tolerance.

### Account Health Monitoring

Continuous assessment of each environment's distance from critical thresholds. Proactive alerts when approaching limits rather than reactive blocks after violation.

### Automatic Risk Scaling

Dynamic adjustment of position sizing based on account performance trajectory. Environments that are performing well may gradually increase risk; underperforming environments reduce risk before limits are hit.

### Environment-Specific Optimisation

Per-environment calibration of EV thresholds, pattern preferences, and session timing based on historical performance within that specific account context.

### Challenge Progress Tracking

For evaluation accounts: real-time progress toward profit targets with intelligent trade frequency management to satisfy minimum trading day requirements without forcing suboptimal entries.

---

## 9. Non-Goals

This document explicitly does **NOT** define:

| Non-Goal | Owner |
|----------|-------|
| Trading strategy logic | Core Engine Architecture |
| Market analysis methodology | Core Engine Architecture |
| Pattern detection algorithms | Core Engine Architecture |
| Probability estimation models | Core Engine Architecture |
| Score calibration curves | Research Engine |
| Broker connectivity | Execution Architecture |
| Order submission protocols | Execution Architecture |
| Fill handling and reconciliation | Execution Architecture |
| Broker selection logic | Execution Architecture |
| Network resilience and reconnection | Infrastructure Architecture |
| Market data feed management | Data Architecture |

The environment layer consumes the output of the core engine and produces input for the execution layer. It owns neither the upstream analysis nor the downstream broker interaction.

---

## Document Authority

This specification defines the canonical boundaries and responsibilities of the environment abstraction layer. All implementation must conform to these definitions. Deviations require explicit architectural review and documentation update.

Changes to environment profiles are configuration changes, not code changes. The system must support adding, modifying, or removing environment profiles without any modification to the core trading engine or the execution layer.
