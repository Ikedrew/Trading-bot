# Trading Platform Constitution

## Purpose

This document defines the non-negotiable architectural principles governing the trading platform.

These principles exist independently of implementation details. Every new feature, refactor, optimisation, policy profile, broker integration, persistence change, observability enhancement, or execution improvement must comply with these principles.

If a proposed change violates one or more principles, the change must be reconsidered before implementation.

These principles are intended to preserve architectural integrity as the platform evolves from a single trading account into a multi-environment trading platform.

---

## Core Philosophy

The platform exists to separate **objective market intelligence** from **subjective trading policy**.

The market has only one truth. Different trading environments may legitimately make different decisions about that truth.

The architecture must preserve this distinction.

---

## Principle 1 — Market Truth Exists Only Once

Market analysis shall be performed exactly once for every market opportunity.

The platform shall never duplicate analytical computation simply because multiple trading environments exist. Every environment consumes the same market intelligence.

Market truth is universal.

---

## Principle 2 — OpportunityAssessment Is the Architectural Boundary

OpportunityAssessment is the canonical boundary between market intelligence and environment-specific decision making.

- Everything **above** OpportunityAssessment belongs to the Core Intelligence Engine.
- Everything **below** OpportunityAssessment belongs to environment-specific processing.

This boundary shall remain explicit throughout the system.

---

## Principle 3 — OpportunityAssessment Is Immutable

Once created, an OpportunityAssessment shall never be modified.

No downstream component may mutate market intelligence. Policy, risk, execution, persistence, and observability must consume the assessment exactly as it was produced.

Market truth is frozen.

---

## Principle 4 — One Intelligence Engine

The platform contains exactly one Core Intelligence Engine.

The intelligence engine is responsible only for understanding the market. It has no knowledge of:

- account balances
- broker rules
- prop firm requirements
- drawdown
- position sizing
- execution state
- profit targets
- account objectives

Its responsibility ends when the OpportunityAssessment is produced.

---

## Principle 5 — Environments Own Decisions

Trading environments own all subjective decision making.

Each environment independently determines whether an opportunity should be traded. Environments may legitimately reach different conclusions while evaluating the same OpportunityAssessment.

Decision making belongs to the environment, not the market intelligence engine.

---

## Principle 6 — Complete Environment Isolation

Every environment shall own its own:

- Policy
- Risk State
- Execution State
- Portfolio
- Trade Lifecycle
- Persistence
- Analytics
- Observability

No environment may modify another environment's state. Isolation guarantees reproducibility, stability, and independent optimisation.

---

## Principle 7 — Policy Is Configurable, Not Hardcoded

Trading behaviour shall be defined through versioned policy profiles.

Examples include:

- Retail_Growth
- Retail_Conservative
- Retail_Experimental
- FTMO
- The5ers
- FundingPips

Adding a new environment must require only a new policy profile. The Core Intelligence Engine must never require modification to support a new trading environment.

---

## Principle 8 — Risk Is Environment-Specific

Risk management is not market intelligence. Risk belongs exclusively to the trading environment.

Each environment owns:

- Position sizing
- Exposure
- Drawdown
- Cooldowns
- Open positions
- Account protection
- Broker limitations

Risk calculations shall never influence historical market truth.

---

## Principle 9 — Execution Is Environment-Specific

Execution represents interaction with a broker. Execution belongs exclusively to the environment.

Each environment owns:

- Broker session
- Order lifecycle
- Trade lifecycle
- Recovery
- Broker state
- Position state

Execution results never modify OpportunityAssessment.

---

## Principle 10 — One Opportunity, Many Decisions

A single market opportunity may produce multiple independent decisions.

Example:

```
OpportunityAssessment
  ↓
  Retail    → Execute
  FTMO     → Reject
  The5ers  → Execute
  FundingPips → Reject
```

The platform must support many independent outcomes originating from one market opportunity.

---

## Principle 11 — Canonical Persistence

Market intelligence shall be persisted exactly once.

Environment-specific decisions, execution records, and trade outcomes shall reference the canonical OpportunityAssessment. The platform shall minimise duplication while preserving complete forensic traceability.

---

## Principle 12 — Complete Traceability

Every action must be reconstructable.

The platform shall preserve the complete chain:

```
Market Data
  ↓
OpportunityAssessment
  ↓
Decision
  ↓
Risk
  ↓
Execution
  ↓
Trade Outcome
  ↓
Performance Analytics
```

No information required for forensic reconstruction shall be lost.

---

## Principle 13 — Every Entity Has an Identity

Every major entity shall possess a globally unique identifier.

Examples include:

- OpportunityID
- EnvironmentID
- ProfileID
- PolicyVersion
- DecisionID
- ExecutionID
- TradeID
- CorrelationID

Relationships between entities shall be explicit.

---

## Principle 14 — Observability Is a First-Class System

Observability is not optional.

Every environment must expose sufficient information to answer:

1. What opportunity existed?
2. Why was it accepted or rejected?
3. Which policy evaluated it?
4. Which execution occurred?
5. What was the outcome?
6. How did the environment perform over time?

No architectural change should reduce observability.

---

## Principle 15 — Analytics Are Evidence

Platform evolution shall be driven by evidence rather than intuition.

- Every optimisation must be measurable.
- Every policy change must be attributable.
- Every improvement must be verifiable.
- Historical analysis must remain reproducible.

---

## Principle 16 — Scalability Without Redesign

The architecture shall support growth from:

```
One retail account
  → Multiple retail accounts
    → Multiple prop firms
      → Multiple brokers
        → Future environments
```

without requiring changes to the Core Intelligence Engine.

Scalability shall be achieved through composition rather than duplication.

---

## Principle 17 — Stability Before Optimisation

Architectural stability takes precedence over feature expansion.

The platform shall favour:

- explicit boundaries
- immutable data
- deterministic behaviour
- versioned policy
- reproducible analytics

over premature optimisation.

---

## Principle 18 — Documentation Is Part of the Architecture

Architecture is defined not only by code but also by documentation.

All major architectural decisions shall be documented. Documentation shall evolve alongside the platform and remain the authoritative reference for future development.

---

## Architectural Constitution

Every proposed change should answer the following questions before implementation:

1. Does this preserve the OpportunityAssessment boundary?
2. Does this introduce duplication of market intelligence?
3. Does this weaken environment isolation?
4. Does this reduce forensic traceability?
5. Does this improve or reduce observability?
6. Can this change be measured?
7. Does this preserve scalability?
8. Could this be implemented by creating a new policy profile instead of modifying the Core Intelligence Engine?

If any answer violates these principles, the implementation should be reconsidered.

---

## Final Statement

The objective of this platform is not simply to automate trading.

Its objective is to create a deterministic, observable, evidence-driven trading platform in which one universal intelligence engine can safely operate multiple independent trading environments while preserving market truth, architectural integrity, and long-term scalability.
