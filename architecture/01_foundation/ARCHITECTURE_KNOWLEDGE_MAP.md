# ARCHITECTURE KNOWLEDGE MAP

**Date:** 2026-07-23
**Status:** Foundation document for System Intelligence Layer.
**Principle:** "Self-Understanding Before Self-Improvement"
**Purpose:** Map what the system knows about itself — what exists, where, who owns it, and what is unknown.
**Note:** Documentation was restructured on 2026-07-23 into numbered category folders (01_foundation/ through 13_future_intelligence/). Path references below reflect pre-restructure locations. The canonical structure is now:
- `architecture/01_foundation/` — Principles, knowledge map
- `architecture/02_authority/` — Ownership, authority, dependencies
- `architecture/03_decision/` — Decision pipeline
- `architecture/04_execution/` — Execution, horizon, runtime
- `architecture/06_market_intelligence/` — Market context, timeframes
- `architecture/07_persistence/` — Datasets, schemas, field audit
- `architecture/08_observability/` — Readiness scoring
- `architecture/09_research/` — Research engine
- `architecture/10_validation/` — Production readiness audits
- `architecture/11_historical/` — Completed/superseded work
- `architecture/12_incidents/` — Resolved incidents
- `architecture/13_future_intelligence/` — System Intelligence Layer design

---

## 1. Documentation Inventory

**Total documents discovered: 77 markdown files across 5 locations.**

### 1.1 Mission & Governance (2 documents)

| Document | Location | Type | Authority | Status | Confidence |
|----------|----------|:----:|:---------:|:------:|:----------:|
| SYSTEM_INTELLIGENCE_PRINCIPLES.md | architecture/mission/ | Principle | PRIMARY | Current | 100% |
| SYSTEM_INTELLIGENCE_LAYER_DESIGN.md | architecture/mission/ | Architecture | PRIMARY | Current | 100% |

### 1.2 Architecture (41 documents)

| Document | Location | Type | Authority | Status | Confidence |
|----------|----------|:----:|:---------:|:------:|:----------:|
| TOP_LEVEL_SYSTEM_AUTHORITY_AUDIT.md | architecture/ | Audit | PRIMARY | Current | 95% |
| PRODUCTION_INTELLIGENCE_READINESS_REVIEW.md | architecture/ | Audit | PRIMARY | Current | 95% |
| FIELD_POPULATION_AUDIT.md | architecture/ | Audit | PRIMARY | Current | 90% |
| HORIZON_EXECUTION_ARCHITECTURE.md | architecture/ | Architecture | PRIMARY | Current | 95% |
| HORIZON_EXECUTION_POLICY.md | architecture/ | Architecture | PRIMARY | Current | 95% |
| REFACTOR_BLUEPRINT.md | architecture/ | Architecture | SECONDARY | Needs review | 75% |
| RESPONSIBILITY_OWNERSHIP_AUDIT.md | architecture/ | Audit | SECONDARY | Current | 85% |
| ARCHITECTURAL_OWNERSHIP_AND_ROUTING.md | architecture/ | Architecture | SECONDARY | Needs review | 70% |
| BOT_ARCHITECTURE_OWNERSHIP_MAP.md | architecture/ | Architecture | SECONDARY | Needs review | 70% |
| DATA_FLOW_ARCHITECTURE_AUDIT.md | architecture/ | Audit | SECONDARY | Needs review | 75% |
| DECISION_EXPLAINABILITY_AUDIT.md | architecture/ | Audit | SECONDARY | Needs review | 70% |
| DECISION_OBJECT_OWNERSHIP_AUDIT.md | architecture/ | Audit | HISTORICAL | Needs review | 60% |
| DEPENDENCY_GRAPH_AUDIT.md | architecture/ | Audit | SECONDARY | Needs review | 70% |
| EVENT_IDENTITY_OWNERSHIP_AUDIT.md | architecture/ | Audit | SECONDARY | Needs review | 75% |
| EXECUTION_BRIDGE_GAP_REPORT.md | architecture/ | Audit | HISTORICAL | Outdated | 40% |
| FULL_ARCHITECTURE_AUDIT.md | architecture/ | Audit | HISTORICAL | Needs review | 50% |
| LIVE_SCANNER_RESPONSIBILITY_AUDIT.md | architecture/ | Audit | SECONDARY | Needs review | 70% |
| MODEL_AND_PERSISTENCE_COMPLETENESS_REPORT.md | architecture/ | Audit | HISTORICAL | Outdated | 50% |
| MODULE_CLASSIFICATION_AUDIT.md | architecture/ | Audit | SECONDARY | Needs review | 65% |
| RESEARCH_ENGINE_ARCHITECTURE.md | architecture/ | Architecture | PRIMARY | Current | 85% |
| RESEARCH_ENGINE_CORRELATION_AUDIT.md | architecture/ | Audit | SECONDARY | Current | 80% |
| RESEARCH_ENGINE_PHASE0_REPOSITORY_AUDIT.md | architecture/ | Audit | HISTORICAL | Outdated | 40% |
| RESEARCH_ENGINE_QUESTION_BANK.md | architecture/ | Research | SECONDARY | Current | 80% |
| TRADE_LIFECYCLE_EVENT_VERIFICATION.md | architecture/ | Audit | SECONDARY | Needs review | 70% |
| PRODUCTION_READINESS_01-07 (7 documents) | architecture/ | Audit | SECONDARY | Needs review | 70% |
| MARKET_CONTEXT_* (5 documents) | architecture/ | Architecture | SECONDARY | Needs review | 65% |
| H1/H4_*_AUDIT (2 documents) | architecture/ | Audit | SECONDARY | Needs review | 65% |
| CANDIDATE_PROMOTION_ASSESSMENT.md | architecture/ | Audit | HISTORICAL | Unknown | 50% |
| STRATEGY_SELECTION_NULL_AUDIT.md | architecture/ | Audit | SECONDARY | Needs review | 60% |
| MEMORY_FAILURE_INVESTIGATION.md | architecture/ | Audit | HISTORICAL | Outdated | 30% |

### 1.3 Persistence (4 documents)

| Document | Location | Type | Authority | Status | Confidence |
|----------|----------|:----:|:---------:|:------:|:----------:|
| PERSISTENCE_ARCHITECTURE_AUDIT_FINAL.md | architecture/local_+_s3_persistence/ | Architecture | PRIMARY | Current | 100% |
| PERSISTENCE_OBSERVABILITY_REVIEW.md | architecture/local_+_s3_persistence/ | Audit | PRIMARY | Current | 100% |
| PERSISTENCE_OWNERSHIP_AUDIT.md | architecture/local_+_s3_persistence/ | Audit | HISTORICAL | Superseded | 40% |
| PERSISTENCE_DELIVERY_AUDIT.md | architecture/local_+_s3_persistence/ | Audit | HISTORICAL | Superseded | 40% |

### 1.4 Multi-Environment (11 documents)

| Document | Location | Type | Authority | Status | Confidence |
|----------|----------|:----:|:---------:|:------:|:----------:|
| MULTI_ENVIRONMENT_ARCHITECTURE.md | architecture/multi-environment_architecture/ | Architecture | PRIMARY | Current | 80% |
| ARCHITECTURE_PRINCIPLES.md | architecture/multi-environment_architecture/ | Principle | SECONDARY | Current | 80% |
| ENVIRONMENT_MODEL.md | architecture/multi-environment_architecture/ | Architecture | SECONDARY | Current | 80% |
| POLICY_PROFILE_SPECIFICATION.md | architecture/multi-environment_architecture/ | Architecture | SECONDARY | Current | 75% |
| All others (7 documents) | architecture/multi-environment_architecture/ | Mixed | SECONDARY | Current | 75% |

### 1.5 Phase Documentation (20 documents)

| Document | Location | Type | Authority | Status | Confidence |
|----------|----------|:----:|:---------:|:------:|:----------:|
| PHASE_1_RISK_PROTECTION_AUDIT.md | docs/ | Audit | HISTORICAL | Completed | 60% |
| PHASE_2*_*.md (6 documents) | docs/ | Audit | HISTORICAL | Completed | 60% |
| PHASE_3*_*.md (5 documents) | docs/ | Audit | HISTORICAL | Completed | 60% |
| PHASE_4*_*.md (8 documents) | docs/ | Audit | HISTORICAL | Completed | 65% |
| TRADE_FORENSIC_CHECKPOINT_001.md | docs/ | Audit | HISTORICAL | Completed | 70% |

---

## 2. Current System Knowledge Map

### Mission and Governance

| Area | Documentation Exists? | Authority Document |
|------|:---------------------:|:------------------:|
| System principles | ✅ | SYSTEM_INTELLIGENCE_PRINCIPLES.md |
| Design philosophy | ✅ | SYSTEM_INTELLIGENCE_PRINCIPLES.md §3 |
| Ownership rules | ✅ | TOP_LEVEL_SYSTEM_AUTHORITY_AUDIT.md |
| Change control | ⚠️ Minimal | SYSTEM_INTELLIGENCE_PRINCIPLES.md §13 (Governance) |
| Mission statement | ❌ None | No explicit MISSION.md |
| Version control policy | ❌ None | No VERSION or CHANGELOG |

### Decision System

| Area | Documentation Exists? | Authority Document |
|------|:---------------------:|:------------------:|
| Decision engine architecture | ✅ | PRODUCTION_READINESS_02_PIPELINE_AUDIT.md |
| Scoring system | ✅ | Decision trace fields define components |
| Strategy selection | ✅ | STRATEGY_SELECTION_NULL_AUDIT.md |
| Decision lifecycle | ✅ | SYSTEM_INTELLIGENCE_PRINCIPLES.md §4 |
| Guard chain | ✅ | TOP_LEVEL_SYSTEM_AUTHORITY_AUDIT.md §2.6 |

### Market Intelligence

| Area | Documentation Exists? | Authority Document |
|------|:---------------------:|:------------------:|
| Market context layer | ✅ | CURRENT_MARKET_CONTEXT_ARCHITECTURE.md |
| Timeframe architecture | ✅ | H1/H4 audit documents |
| Regime classification | ✅ | Implicit in decision_trace fields |
| Horizon classification | ✅ | HORIZON_EXECUTION_ARCHITECTURE.md |

### Risk System

| Area | Documentation Exists? | Authority Document |
|------|:---------------------:|:------------------:|
| Guard chain | ✅ | TOP_LEVEL_SYSTEM_AUTHORITY_AUDIT.md |
| Risk veto authority | ✅ | TOP_LEVEL_SYSTEM_AUTHORITY_AUDIT.md §2.6 |
| Protection verification | ✅ | PHASE_1_RISK_PROTECTION_AUDIT.md |
| Portfolio limits | ✅ | HORIZON_EXECUTION_POLICY.md |

### Execution System

| Area | Documentation Exists? | Authority Document |
|------|:---------------------:|:------------------:|
| Execution authority | ✅ | TOP_LEVEL_SYSTEM_AUTHORITY_AUDIT.md §2.7 |
| Broker interaction | ⚠️ Partial | EXECUTION_BRIDGE_GAP_REPORT.md (may be outdated) |
| Order lifecycle | ⚠️ Partial | Implicit in execution_results schema |

### Persistence System

| Area | Documentation Exists? | Authority Document |
|------|:---------------------:|:------------------:|
| Complete dataset inventory | ✅ | PERSISTENCE_ARCHITECTURE_AUDIT_FINAL.md |
| S3 architecture | ✅ | PERSISTENCE_ARCHITECTURE_AUDIT_FINAL.md |
| Schema contracts | ✅ | PERSISTENCE_ARCHITECTURE_AUDIT_FINAL.md |
| Field population | ✅ | FIELD_POPULATION_AUDIT.md |
| Data quality | ✅ | PRODUCTION_INTELLIGENCE_READINESS_REVIEW.md |

### Research System

| Area | Documentation Exists? | Authority Document |
|------|:---------------------:|:------------------:|
| Research engine architecture | ✅ | RESEARCH_ENGINE_ARCHITECTURE.md |
| Question bank | ✅ | RESEARCH_ENGINE_QUESTION_BANK.md |
| Horizon research | ✅ | SYSTEM_INTELLIGENCE_LAYER_DESIGN.md (references) |
| Shadow evaluation | ✅ | Code documentation (core/horizon/shadow_evaluation.py) |

### Operations

| Area | Documentation Exists? | Authority Document |
|------|:---------------------:|:------------------:|
| Deployment | ⚠️ Partial | PRODUCTION_READINESS_07_DEPLOYMENT_REPORT.md |
| Runtime monitoring | ⚠️ Partial | health_monitor.py code docs |
| Incident handling | ❌ None | No runbook |
| Configuration management | ⚠️ Partial | config.py comments only |

---

## 3. Architecture Ownership Map

| Subsystem | Purpose | Authority Files | Inputs | Outputs | Dependencies |
|-----------|---------|:---------------:|--------|---------|:------------:|
| **Bootstrap** | Start/stop/mode selection | `main.py`, `config.py` | Environment, config | Running scanner | MT5, config_validation |
| **Runtime Loop** | Cycle orchestration | `core/runtime/live_scanner.py` | Ticks, bars | Decisions, executions | All subsystems |
| **Decision Engine** | EXECUTE/NO_TRADE production | `core/pipeline/new_engine.py` | Market data, patterns | Action + intent | scoring, strategy, policy |
| **Horizon System** | Multi-horizon classification + authority | `core/horizon/` | Assessments | Classification, shadows | classifier, profiles, authority |
| **Risk Veto** | Block unsafe trades | `risk/runtime_guard_chain.py` | Intent, positions | ALLOWED/BLOCKED | 10 individual guards |
| **Execution** | Submit orders to broker | `execution/execution_orchestrator.py` | Intent, correlation_id | Fill result | mt5_execution |
| **Trade Management** | Position lifecycle (BE/trail/exit) | `core/trade_management/manager.py` | Price ticks, positions | SL modifications, closes | sl_tp_rules, horizon profiles |
| **Persistence** | 24 dataset append-only storage | 23 writer modules | Runtime events | JSONL + S3 | config (S3 gate) |
| **Research** | Read-only analysis | `research_engine/` | Persisted datasets | Reports, observations | data_access/loaders |

---

## 4. Documentation Truth Assessment

### Source of Truth Documents (7)

| Document | Domain | Why Authoritative |
|----------|--------|-------------------|
| SYSTEM_INTELLIGENCE_PRINCIPLES.md | Governance | Constitutional standard. All decisions reference it. |
| PERSISTENCE_ARCHITECTURE_AUDIT_FINAL.md | Persistence | Reflects verified implementation (24/24 datasets). |
| TOP_LEVEL_SYSTEM_AUTHORITY_AUDIT.md | Authority | Traced from source code. Verified hierarchy. |
| HORIZON_EXECUTION_POLICY.md | Horizon | Approved design. Implementation matches. |
| HORIZON_EXECUTION_ARCHITECTURE.md | Horizon | Implementation complete and tested. |
| PRODUCTION_INTELLIGENCE_READINESS_REVIEW.md | Readiness | Comprehensive scoring with evidence. |
| FIELD_POPULATION_AUDIT.md | Data quality | Traced every field to runtime source. |

### Documents With Potential Conflicts

| Document A | Document B | Conflict Area | Resolution |
|:----------:|:----------:|:-------------:|:----------:|
| PERSISTENCE_OWNERSHIP_AUDIT.md | PERSISTENCE_ARCHITECTURE_AUDIT_FINAL.md | Dataset count, S3 status | FINAL supersedes (marked in header) |
| FULL_ARCHITECTURE_AUDIT.md | TOP_LEVEL_SYSTEM_AUTHORITY_AUDIT.md | Component list, authority | TOP_LEVEL is newer and verified |
| BOT_ARCHITECTURE_OWNERSHIP_MAP.md | RESPONSIBILITY_OWNERSHIP_AUDIT.md | Module responsibilities | RESPONSIBILITY is post-refactor (newer) |

### Missing Documentation

| Area | Impact | Priority |
|------|--------|:--------:|
| Explicit MISSION.md | Team alignment, onboarding | P2 |
| Incident runbook | Production debugging during outages | P2 |
| Configuration change process | Safe production changes | P1 |
| Version/release tracking | Cannot link code version to trades | P1 |
| Broker interaction reference | Execution edge cases undocumented | P3 |
| Test strategy document | No formal testing philosophy | P3 |

---

## 5. Proposed Future Documentation Structure

```
architecture/
├── mission/                         # Governance and principles
│   ├── SYSTEM_INTELLIGENCE_PRINCIPLES.md     ✅ EXISTS
│   ├── SYSTEM_INTELLIGENCE_LAYER_DESIGN.md   ✅ EXISTS
│   ├── ARCHITECTURE_KNOWLEDGE_MAP.md         ✅ THIS DOCUMENT
│   └── MISSION.md                            ❌ TO CREATE
│
├── decision_system/                 # Decision engine, scoring, strategy
│   ├── DECISION_ENGINE_ARCHITECTURE.md       (from PRODUCTION_READINESS_02)
│   ├── SCORING_SYSTEM.md                     (new)
│   └── STRATEGY_SELECTION.md                 (from STRATEGY_SELECTION_NULL_AUDIT)
│
├── market_intelligence/             # Market context, regimes, timeframes
│   ├── MARKET_CONTEXT_ARCHITECTURE.md        (from CURRENT_MARKET_CONTEXT)
│   ├── HTF_ARCHITECTURE.md                   (from H1/H4 audits)
│   └── HORIZON_SYSTEM.md                     (from HORIZON_EXECUTION_*)
│
├── risk_system/                     # Guards, veto, limits
│   ├── GUARD_CHAIN_ARCHITECTURE.md           (new, from authority audit)
│   └── PORTFOLIO_LIMITS.md                   (from HORIZON_EXECUTION_POLICY)
│
├── execution_system/                # Broker, orders, fills
│   ├── EXECUTION_ARCHITECTURE.md             (new)
│   └── PROTECTION_VERIFICATION.md            (from PHASE_1 audit)
│
├── trade_management/                # Position lifecycle
│   └── TRADE_MANAGEMENT_ARCHITECTURE.md      (new)
│
├── persistence/                     # Data layer (MOVED from local_+_s3_persistence/)
│   ├── PERSISTENCE_ARCHITECTURE_AUDIT_FINAL.md  ✅ EXISTS
│   ├── PERSISTENCE_OBSERVABILITY_REVIEW.md      ✅ EXISTS
│   └── FIELD_POPULATION_AUDIT.md                ✅ EXISTS (currently at root)
│
├── research/                        # Research engine, experiments
│   ├── RESEARCH_ENGINE_ARCHITECTURE.md       ✅ EXISTS
│   └── RESEARCH_QUESTION_BANK.md             ✅ EXISTS
│
├── operations/                      # Deployment, monitoring, incidents
│   ├── DEPLOYMENT_GUIDE.md                   (from PRODUCTION_READINESS_07)
│   ├── INCIDENT_RUNBOOK.md                   ❌ TO CREATE
│   └── CONFIGURATION_MANAGEMENT.md           ❌ TO CREATE
│
├── audits/                          # Point-in-time audits (historical)
│   ├── PRODUCTION_READINESS_01-07/
│   ├── REFACTOR_BLUEPRINT.md
│   └── ...
│
└── archive/                         # Completed phase docs, outdated audits
    ├── docs/PHASE_1-4 (moved from docs/)
    └── superseded audits
```

---

## 6. Migration Plan (Recommendations Only)

| Current Location | Recommended Location | Reason | Priority |
|-----------------|---------------------|--------|:--------:|
| architecture/FIELD_POPULATION_AUDIT.md | architecture/persistence/ | Belongs with persistence docs | P2 |
| architecture/HORIZON_EXECUTION_* | architecture/market_intelligence/ | Horizon is market intelligence | P2 |
| architecture/PRODUCTION_READINESS_* | architecture/audits/ | Historical audits | P3 |
| architecture/local_+_s3_persistence/ | architecture/persistence/ | Cleaner path | P3 |
| docs/PHASE_*_*.md | architecture/archive/ | Historical phase work | P3 |
| architecture/MEMORY_FAILURE_INVESTIGATION.md | architecture/archive/ | Resolved investigation | P3 |
| architecture/EXECUTION_BRIDGE_GAP_REPORT.md | architecture/archive/ | Likely outdated | P3 |

**Do not migrate yet.** This table is for future reference.

---

## 7. System Intelligence Layer Requirements

Based on discovery, the System Intelligence Layer needs access to:

### Component Knowledge
- 9 major subsystems identified (§3)
- 23+ registered persistence writers
- 10 runtime guard modules
- 7 authority layers (from TOP_LEVEL audit)

### Documentation Knowledge
- 7 source-of-truth documents
- ~30 secondary/historical documents
- 6 areas with missing documentation

### Data Knowledge
- 24 persisted datasets (all schemas, all fields)
- ~600 fields traced (from FIELD_POPULATION_AUDIT)
- 6 join keys (entity_id, opportunity_id, decision_id, correlation_id, trade_id, cycle_id)

### Decision Knowledge
- Complete EXECUTE path (14 stages, all persisted)
- Complete NO_TRADE path (5 stages, all persisted)
- 7 decision types (EXECUTE, NO_TRADE, RISK_BLOCK, SESSION_BLOCK, PATTERN_REJECT, KILL_SWITCH, DAILY_LOSS_BLOCK)

---

## 8. System Intelligence Readiness Assessment

| Dimension | Score | Evidence |
|-----------|:-----:|----------|
| Understanding | **85/100** | 7 authority documents exist. 6 areas undocumented. |
| Documentation coverage | **75/100** | 77 documents but many are historical/unreviewed. |
| Ownership clarity | **90/100** | TOP_LEVEL audit maps all authorities. |
| Data visibility | **94/100** | 24/24 datasets, 600 fields traced. |
| Decision visibility | **96/100** | Full EXECUTE + NO_TRADE chains persisted. |
| Research readiness | **91/100** | Research engine + horizon research + shadow evaluation. |

**Overall: 88.5/100**

### What Is Already Strong
- Persistence architecture (100% coverage, versioned, Hive-partitioned)
- Decision traceability (entity_id + correlation_id spine)
- Authority documentation (clear hierarchy, veto chain mapped)
- Research infrastructure (contracts, observations, reports, shadows)

### What Prevents Full Self-Understanding
- ~30 documents of uncertain accuracy (needs review pass)
- No version tracking (cannot link code state to trade outcomes)
- Operational documentation gaps (no runbook, no config change process)
- No automated architecture drift detection

### Highest-Value Improvements
1. **P1:** Review and update the ~15 "Needs review" documents (eliminate uncertainty)
2. **P1:** Create configuration management process (safe change control)
3. **P2:** Create MISSION.md (explicit purpose statement)
4. **P2:** Archive completed phase documents (reduce noise)
5. **P3:** Implement automated architecture drift detection

---

## 9. Final Recommendation

### Current Maturity Level

**The system has strong self-understanding (85/100) with documented authority, complete persistence, and traceable decisions.** The primary gap is documentation maintenance — many documents exist but their accuracy relative to current implementation is uncertain.

### Biggest Knowledge Gaps

1. **Document freshness** — ~30 documents have not been verified against current code
2. **Operational knowledge** — no runbook, no config change process, no version tracking
3. **Mission clarity** — implicit mission but no explicit document

### Recommended Order of Fixes

1. Mark clearly outdated documents as ARCHIVE (reduces confusion)
2. Create configuration change process (prevents unsafe production changes)
3. Create MISSION.md (anchors all other decisions)
4. Review and update ~15 "Needs review" secondary documents
5. Implement automated drift detection (System Intelligence Layer Phase 1)

### Is the System Ready for System Intelligence Layer Implementation?

**YES — conditionally.**

The foundational knowledge exists:
- Authority hierarchy is mapped
- All 24 datasets are documented and accessible
- Decision flows are traceable
- Research infrastructure exists

The System Intelligence Layer can begin at **Phase 1 (Observer)** — building a structured model from the 7 source-of-truth documents and the 24 persistence schemas. It should NOT begin at Phase 3 (Advisor) until the ~30 uncertain documents have been reviewed.

---

*End of Architecture Knowledge Map.*
