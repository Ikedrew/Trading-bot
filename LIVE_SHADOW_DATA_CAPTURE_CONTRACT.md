# LIVE + SHADOW DATA CAPTURE CONTRACT

**Phase:** Forensic Definition (post gated Trial 3)
**Date:** 2026-08-26
**Status:** DESIGN ONLY — no source edits, no config changes, no trades, no Shadow gate activation.
**Method:** Read-only inspection of the codebase, current persistence schemas, and prior architectural audits.
**Scope:** Define, field-by-field and lineage-by-lineage, exactly what the LIVE and SHADOW data paths must capture so both paths can collect production data and be compared without conflating real observations with simulated outcomes.

> ⚠️ **Corrective note re: the task brief.** The brief references `core/persistence/persistence.py`. **That exact file does not exist.** The persistence contract is a **one-writer-per-dataset** design implemented across multiple modules: `core/persistence/decision_trace_writer.py`, `core/persistence/execution_result_writer.py`, `core/persistence/opportunity_assessment_writer.py`, plus per-domain modules `core/assessment/persistence.py`, `core/market_context/persistence.py`, `core/opportunity/persistence.py`, `core/portfolio_ranking/persistence.py`, and **two separate shadow persistence modules** (`core/shadow_trades.py` legacy and `core/shadow/persistence.py` new). The authoritative-vs-duplicate analysis in §7 treats this actual surface, not the assumed path.

---

## 0. Ground Truth From Source (facts the rest of this contract is built on)

| # | Fact | Evidence (file) | Status |
|---|------|-----------------|--------|
| F1 | LIVE decision root today is **`entity_id`** = `f"{symbol}_{int(closed_bar_time)}"` | `core/pipeline/new_engine.py:105`; `core/v10/scanner_adapter.py:57,134` | Implemented |
| F2 | V10 reasoning root is **`observation_id`** = `sha256(f"{symbol}_{timestamp}")[:16]` (16-hex) | `core/v10/opportunity_engine.py:361-364` | Implemented |
| F3 | Strategy observation record id is **`observation_id`** = `f"{symbol}_{cycle_id}_{timestamp_utc}"` (e.g. `EURUSD_42857_1753574400`) | `architecture/strategies/DATA_LINEAGE_AUDIT.md:256` | Implemented (strategy_observations dataset) |
| F4 | Approved canonical lineage **root** is **`canonical_opportunity_id`** = `{SYMBOL}*{bar_time}*{pattern}` | `core/identity/canonical.py:6,62-89` (mandated owner) | Design-approved; partially propagated (§7) |
| F5 | Legacy per-pattern **`opportunity_id`** = `{symbol}_{bar_time}_{pattern}` | `core/opportunity/factory.py:63`; `core/opportunity/opportunity.py:68` | Implemented |
| F6 | **`assessment_id`** is minted under TWO different formats | `core/assessment/builder.py:63` (`..._assessment`) vs `core/persistence/opportunity_assessment_writer.py:101` (`{sym}_{bar}_{cycle}`) | **Duplicate/conflicting** |
| F7 | **`decision_id`** = `uuid4().hex` minted at decision-audit persist | `core/decision_audit.py` (per `architecture/03_decision/EVENT_IDENTITY_OWNERSHIP_AUDIT.md`) | Implemented (non-deterministic) |
| F8 | **`correlation_id`** has at least 4 different formats in use | `{cycle}_{symbol}_{ts}`; `COR-{YMD}-{cycle}-{symbol}-{hash}`; `HORIZON-{cycle}-{symbol}`; `v10_{symbol}_{ts}_{cycle}` | Conflicting |
| F9 | Per-dataset probes = `shadow_id` legacy primary `shadow_{cycle}_{symbol}`; legacy horizon `hshadow_{cycle}_{symbol}_{HORIZON}`; NEW `nplan_{cycle}_{symbol}_{bar}` / child `nshadow_{id}_{symbol}_{HORIZON}` | `core/shadow/runtime.py:147`, `tests/test_shadow_runtime_contract.py:217` | Mismatched across eras |
| F10 | Shadow gates exist and are intended OFF for this phase; NEW runtime gated by `SHADOW_RUNTIME_V2_ENABLED`, legacy by `ENABLE_LEGACY_SHADOW_PIPELINE`. Repo `core/config.py:75` currently reads **`True`** for the NEW gate (must be verified against `.env`, which overrides) | `core/config.py:71-79`; `_verify_fix.py:13-14` | ⚠ verify before enabling anything |
| F11 | LIVE won the sole authoritative write must come via DecisionLedgerWriter; the research V10 payload travels as a `v10{...}` sub-dict inside the ledger row | `core/v10/persistence_adapter.py:239-270,273-304` | Implemented |

---

## 1. Architecture

### 2.1 The non-negotiable separation (the contract this blueprint protects)

```
LIVE  = facts about what the real trading system OBSERVED, DECIDED, EXECUTED,
        HELD, CLOSED and REALISED.  (provenance: OBSERVED / DERIVED / SYSTEM)
SHADOW= what a shadow strategy WOULD have decided/executed under the SAME market
        observation, represented ONLY as simulated data.
        (provenance: SIMULATED / DERIVED)
```

Rules (must hold in the final implementation):
1. LIVE and SHADOW **SHARE** canonical upstream market-observation lineage (same `canonical_opportunity_id`, same observation root).
2. LIVE and SHADOW **MUST NOT share** mutable outcome/execution records — SHADOW writes its own append-only event stream (`core/shadow/persistence.py` → `logs/shadow_runtime_v1/`), LIVE writes execution + trade-truth + journal records.
3. SHADOW values are ALWAYS marked `SIMULATED` / `DERIVED`; they must never be persisted into any broker-fact record, and broker-fact records must reject cross-layer contamination (this already exists via `_FORBIDDEN_FIELDS` in `core/trade_truth.py` and `core/execution_context.py`).
4. Provenance enum for every field: **OBSERVED** (raw broker/robot observation), **DERIVED** (computed from observed), **SIMULATED** (counterfactual/synthetic), **SYSTEM** (runtime/infra facts).

### 2.2 Current end-to-end (as-built, both new and legacy shadow loops)

```
MT5 feed → data/mt5_data (candles, bid/ask, spread, offset)
   │
   ▼  MARKET OBSERVATION  (bar_time, entity_id minted here-or-new_engine)
core/runtime/live_scanner (per symbol per closed bar)
   ├─ strategy observation (strategy_observations)  ── OBSUP
   ├─ core/pipeline/new_engine → entity_id, pattern, decision stages
   │       OpportunityAssessment → Assessment → DecisionTrace → DecisionLedger → DecisionAudit
   │
   ├─ [EXECUTE branch]
   │      execution_context (frozen env snapshot)
   │      OrderIntent (risk_id=observation_id) → execution.execute() → ExecutionResult(done,tp,deal,retcode,slip)
   │      Position / TradeIdentity (carries observation_id + canonical) → trade_truth at close → trade_journal
   │
   └─ [SHADOW branch — gated]
          LEGACY (ENABLE_LEGACY_SHADOW_PIPELINE=False) → logs/shadow_trades (shadow_trades_v2)
          NEW     (SHADOW_RUNTIME_V2_ENABLED)          → core/shadow/runtime.py → logs/shadow_runtime_v1
              live_facts inherited (no shadow decision) → PLAN → OPEN → PROGRESS* → CLOSE
```

### 2.3 The five persistence interfaces today (all read independently)

1. `core/decision_ledger.py` — every decision (authoritative).
2. `core/decision_audit.py` + `core/decision_trace.py` — explainable decision trail.
3. `core/execution_context.py` + `core/persistence/execution_result_writer.py` — execution env + result.
4. `core/trade_truth.py` (execution reality) + `core/trade_journal.py` (completed trade outcome).
5. SHADOW: `core/shadow_trades.py` (legacy, disabled) + `core/shadow/persistence.py` (NEW, gated, provisionally `logs/shadow_runtime_v1`).

> READ: LIVE facts → renamed R-observation from `strategy_observation`; V10 hashed `observation_id`; `entity_id`. Three different "observation identities" on the same market bar (see §3.7) is today's central lineage risk.

---
---

## 2. Canonical lineage

### 2.1 Complete intended LIVE lineage

```
MARKET OBSERVATION   (MT5 feed → closed bar; broker symbol; bar_time; OHLC; bid/ask/spread; feed_state)
      │  canonical_opportunity_id minted here (symbol × normalized bar_time × primary pattern)
      ▼
MARKET CONTEXT       (regime, volatility, h4/h1/m15/m5 bias & strength, location, displacement)
      ▼
OPPORTUNITY          (pattern, pattern_direction, pattern_quality, entry_reference, structural levels, sibling_patterns)
      ▼
ASSESSMENT           (quality scores, score_strategy, confidence, score_attribution/reasoning, scoring-input snapshot)
      ▼
DECISION             (decision enum: EXECUTE | NO_TRADE | RISK_BLOCK | SESSION_BLOCK | PATTERN_REJECT | KILL_SWITCH | DAILY_LOSS_BLOCK | STALE_DATA | FEED_BLOCKED + reason + score + strategy + decision_id)
      ▼
RISK                 (risk_decision APPROVED/BLOCKED, reason, position sizing inputs, stop/target distances, risk amount, limits evaluated, resulting state)
      ▼
EXECUTION INTENT     (OrderIntent: side, volume, entry_reference, sl, tp + execution_context snapshot)
      ▼
EXECUTION RESULT     (broker request→response: retcode, deal, order_ticket, fill_price, slippage, latency, bid/ask at decision & execution, protection status)
      ▼
POSITION LIFECYCLE   (opened → modified → closed; timestamps, prices, volume, broker ids, lifecycle_state)
      ▼
OUTCOME              (realised P/L, realised R, exit reason, MAE, MFE, holding duration, costs, final trade state)
```

### 2.2 Complete intended SHADOW lineage

```
MARKET OBSERVATION   (shared canonical root — SAME observation as LIVE)
      ▼
MARKET CONTEXT       (inherited as live_facts — same frozen context)
      ▼
SHADOW OPPORTUNITY   (per-horizon eligibility: eligible_horizons + horizon_assessments; live pattern inherited)
      ▼
SHADOW ASSESSMENT    (per-horizon local approximation: entry placement vs structure; no fabricated shadow score)
      ▼
SHADOW DECISION      (NONE by design — live v10_action inherited; no shadow verdict)
      ▼
SHADOW RISK          (SIMULATED geometry: structure-based SL, target RR TP, R_NORMALISED sizing)
      ▼
SHADOW PLAN          (PLAN event: all 3 horizons, NOT_ELIGIBLE/CONSTRUCTED/SIMULATED states)
      ▼
SHADOW OPEN          (OPEN event: construction + assumptions + lifecycle_initial; shadow_trade_id minted per horizon)
      ▼
SHADOW PROGRESS      (PROGRESS* events: checkpointed lifecycle state, watermark, honest data_gaps)
      ▼
SHADOW CLOSE         (CLOSE event: exit price/reason, bars_held, outcome block)
      ▼
SHADOW OUTCOME       (simulated P/L in R, MFE/MAE, costs, exit — explicitly SIMULATED, never broker facts)
```

### 2.3 Exact divergence point LIVE ↔ SHADOW

| Stage | LIVE | SHADOW | Diverges? |
|---|---|---|---|
| MARKET OBSERVATION | broker feed | shared canonical root | **NO — shared** |
| MARKET CONTEXT | frozen decision context | inherited `live_facts` | **NO — shared** |
| OPPORTUNITY | pattern + opportunity record | eligibility per horizon | Partial — shared inputs, different evaluation |
| ASSESSMENT | score/quality | local approximation only | **YES — diverge here** |
| DECISION | full verdict enum | none (inherited v10_action) | **NO — shadow has no own decision** |
| RISK | APP/BLOCK + sizing inputs | SIMULATED geometry | **YES — diverge** |
| EXECUTION | broker request/response, bid/ask, fill | SIMULATED plan/construction | **YES — diverge (never share records)** |
| POSITION / LIFECYCLE | broker position events | PLAN/OPEN/PROGRESS/CLOSE simulated stream | **YES — fully separate stores** |
| OUTCOME | realised P/L + R + costs | simulated R + zero-cost policy | **YES — MUST NOT be conflated** |

**Summary:** both lanes SHARE observation, context, and the live decision facts (namespaced `live_facts`). They diverge at assessment-derived geometry and never share execution or outcome records. This is already correctly implemented in the NEW Shadow runtime (`core/shadow/models.py`) — the remaining work is propagating the canonical root through the LIVE lane so the shared lineage is actually joinable.

---

## 3. Canonical Identity Audit

### 3.1 Inventory — every identifier

| Identifier | Format (evidence) | Generating file/function | Deterministic? | Persisted to | Parent/root | LIVE==SHADOW today? | Notes |
|------------|-------------------|--------------------------|----------------|--------------|-------------|---------------------|-------|
| `entity_id` | `{SYMBOL}_{int(bar_time)}` (EURUSD_1787725800) | new_engine.py:105; v10/scanner_adapter.py:57,134 | ✅ yes | decision_ledger/audit/trace, execution_result, assessment, trade_truth-world | Observation bar | ✓ same (both branches derive from same bar) | **Primary de-facto join key today** |
| `observation_id` (V10) | `sha256(sym+ts)[:16]` (a1a2…) | v10/opportunity_engine.py:361 | ✅ yes | v10_decision records, ledger `v10` sub-dict | V10 bar | ✓ for V10 branches | Not equal to strategy `observation_id` |
| `observation_id` (strategy) | `{SYM}_{cycle}_{ts}` (EURUSD_42857_1753574400) | core/strategies observer/persistence | ✅ yes | `logs/strategy_observations` | ROS cycle | ⚠ different scheme | **Mismatch with V10 observed hash (see §3.7)** |
| `canonical_opportunity_id` | `{SYMBOL}*{bar_time}*{pattern}` (EURUSD*1784800000*TWEEZER_TOP) | core/identity/canonical.py make_canonical_opportunity_id | YES (mandatory normalized) | assessment, decision_audit, decision_ledger, exec_result, trade_journal, NEW shadow events | **THE planned root** | ✓ both branches mint identical | **THE root the target architecture must center** |
| `opportunity_id` (legacy per-pattern) | `{sym}_{bar}_{pattern}` | core/opportunity/factory.py:63; assessment/builder.py:64 | yes | opportunity_assessment_log/assessments | per-pattern | ✗ never in SHADOW | sibling-heavy, not a lineage root |
| `assessment_id` | two formats | builder.py:63 vs opportunity_assessment_writer.py:101 | yes | assessments + opportunity_assessment | per (sym,bar) | ✗ not in SHADOW | **conflicting minting (F6)** |
| `decision_id` | uuid4 hex | decision_audit persist_new_engine_decision_audit | NO (random) | decision_audit, execution_result | runtime | ✗ shadow has no decision_id | child ID |
| `correlation_id` | multiple formats (§1.8) | live_scanner generate_correlation_id / persistence_adapter / horizon | NO | execution_context, exec_result, shadow_trades, events | cycle | ⚠ inconsistent | **never a join key by rule (canonical.py:27)** |
| `risk_id` | = V10 observation_id (or "") | OrderIntent (scanner_adapter _build_order_intent) | — | OrderIntent/exec intent | oss | — | alias of observation_id |
| `shadow_trade_id` (NEW) | `nplan_{cycle}_{sym}_{bar}` / `nshadow_{id}_{sym}_{HORIZON}` | core/shadow/runtime.py | yes (root) / yes(child) | NEW shadow stream | canonical_opportunity_id | SHADOW | SHADOW-only mint |
| `shadow_trade_id` (legacy) | `hshadow_{cycle}_{sym}_{HORIZON}` / `shadow_{cycle}_{sym}` / `candidate` | core/shadow_trades.py / research engine | — | logs/shadow_trades | correlation_id → entity_id | ✗ joins only via corr | legacy |
| `trade_id` | `pos_{deal}` / uuid | trade_identity.py / broker | ✗ broker | trade_journal, trade_truth, trade_truth_graph | — | SHADOW | broker fact only |
| `position_id` / `order_id` / `deal` | MT5 int | broker | no | execution_result, journal | broker | SHADOW | broker fact only |
| `runtime_session_id` | uuid/counter | runtime | — | decision_trace/audit | — | — | diagnostic |
| `horizon_id` | SCALP/INTRADAY/EXTENDED | horizon engine | — | horizon shadow | — | — | semantic tag, not a key |

### 3.2 Minimum canonical root required for full trade/SHADOW reconstruction

**`canonical_opportunity_id` = `{SYMBOL}*{normalized_bar_time}*{PRIMARY_PATTERN}`** (asset, closed-bar time int-normalized, primary pattern). It is:
- replay-stable (derived only from market data),
- mintable **before the verdict** (so NO_TRADE and EXECUTE AND SHADOW all share it),
- identical across LIVE and SHADOW (same inputs),
- immutable,
- normalised for int/float bar-time parity.

**Required routing rule (target):** every LIVE decision/execution/outcome record **and** every SHADOW event MUST carry `canonical_opportunity_id`. `entity_id` stays as the observation-level alias/compat key; `observation_id` (both variants) becomes a read-only compatibility key, never a cross-domain join. NO new ID scheme is minted (per mission §3).

### 3.3 The three-identity mismatch (mission §3 highlight)

| Dataset | observation_id meaning | Root family |
|---------|------------------------|-------------|
| `strategy_observations` | `{symbol}_{cycle}_{ts}` | cycle-based |
| V10 decision/de code | `sha256(symbol+ts)[:16]` | ts-hash | 
| de-facto LIVE/SHADOW join | `entity_id {symbol}_{ts}` | ts-based |
| approved | `canonical …{symbol}*{ts}*{pattern}` | ts+pattern |

**Consequence:** a single market bar can appear under 3-4 unrelated IDs across datasets, blocking direct joins. Only `entity_id` and `canonical_opportunity_id` normalise; the others are lossy for join purposes. **BLOCKING** for cross-dataset reconstruction until propagated.

### 3.4 Immutable vs timestamped/clock

See §4 per-field table — key principle: every persisted value is timestamped, and each LANE records **market-time (broker bar epoch + broker UTC offset)** and **system-time (`recorded_at_utc`)** (see NEW shadow `market_block`/`_wall_stamp`; this MUST be mirrored on the LIVE execution/trade path, which already carries `timestamp_unix`+`timestamp_utc`). Market-vs-system provenance is explicit.

---

## 4. LIVE capture matrix

Column legend:
- **Req** = R/O (Required/Optional)
- **Prov** = provenance: OBSERVED / DERIVED / SIMULATED / SYSTEM
- **Parent** = canonical parent ID on the record
- **Immt** = immutable after write (Y/N)
- **clock** = M market-time, S system-time, B both
- **Rec** = required for reconstruction (Y/N)
- **Res** = required for research (Y/N)
- **cmp** = comparability with SHADOW: Direct / Norm(after normalisation) / No
- **Status** = current capture: ✅ / ⚠ partial / ✖ absent

Intended canonical destination of every row is given in §7. Nothing below is SIMULATED — that is the SHADOW lane (§5).

### 4.A Observation (per closed bar)

| Field | Type | Req | Prov | Persist (today) | Parent | Immt | clock | Rec | Res | cmp | Status | Gap |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| symbol (broker-resolved e.g. EURUSD_SB) | str | R | OBS | ledger/audit/ctx/exec | canonical | Y | B | Y | Y | Direct | ✅ | symbol variants (SB vs canonical) must normalise |
| timeframe | int | O | OBS | strategy_observations, market_state | canonical | Y | M | Y | Y | Direct | ⚠️ | not on every decision row |
| bar_time (broker epoch s) | int | R | OBS | audit/ledger/trace/ctx | canonical | Y | M | Y | Y | Direct | ✅ | — |
| broker UTC offset | int | R | OBS/SYS | NEW shadow only | canonical | Y | S | Y | Y | — | ⚠️ | LIVE rows MISSING offset -> UTC undeterministic |
| OHLC | float x4 | R | OBS | audit(mkt_state), opp | canonical | Y | M | Y | Y | Direct | ⚠️ | no canonical per-bar OHLC standalone record |
| spread | float | R | OBS | execution_context | corr | Y | M | Y | Y | Direct | ✅ | only EXECUTE lane |
| bid / ask at closure | float | R | OBS | execution_context | corr | Y | M | Y | Y | Direct | ✅ | NO_TRADE has no price record |
| feed_state | str | O | OBS/SYS | execution_context | corr | Y | S | N | Y | Direct | ✅ | only EXECUTE |
| tick_age_ms | int | O | SYS | execution_context | corr | Y | S | N | Y | Direct | ✅ | sparse |
| bar_age / staleness | float | O | SYS | stale_monitor | bar | Y | S | N | Y | Direct | ⚠️ | not persisted per decision |
| data_quality | enum | R | SYS | NEW shadow data_gaps | canonical | Y | M | Y | Y | Direct | ⚠️ | LIVE has no per-record data_quality field |
| entity/observation id | str | R | DER | audit/ledger/exec | canonical | Y | M | Y | Y | — | ✅ | 3 schemes (§3.7) |
| cycle id | int | R | SYS | ledger/trace/audit/ctx | corr | Y | S | Y | Y | Direct | ✅ | cycle_id=0 bug fixed |

### 4.B Market context (captured at decision time, not reconstructed)

| Field | Type | Req | Prov | Persist | Parent | Immt | clock | Rec | Res | cmp | Status | Gap |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| regime | str | R | DER | audit.market_state, market_context_v1 | canonical | Y | M | Y | Y | Norm | ✅ | — |
| volatility_state / expansion_state | str | R | DER | v10 market_state | canonical | Y | M | Y | Y | Norm | ✅ | — |
| h4/h1/m15/m5 bias & strength | str | R | DER | v10 record / decision snapshot | canonical | Y | M | Y | Y | Norm | ✅ | — |
| location_type / range_position / zone_quality | str/fl | R | DER | v10 market_state | canonical | Y | M | Y | Y | Norm | ✅ | — |
| displacement (m15) present/dir/mag | bool/f | O | DER | v10 market_state / opp output | canonical | Y | M | Y | Y | Norm | ✅ | — |
| htf_snapshot (research) | dict | O | DER | research_shadow_trades only | canonical | Y | M | Y | Y | Norm | ⚠️ | LIVE decision rows lack htf_snapshot; shadow has it for research |

**Principle:** context must be frozen at decision time. Satisfied via V10 `OpportunityAssessment`/`V10DecisionRecord` and `core/market_context/persistence.py`. The SHADOW lane inherits these as `live_facts`.

### 4.C Opportunity

| Field | Req | Prov | Persist (today) | Parent | Immt | clock | Rec | Res | cmp | Status | Gap |
|---|---|---|---|---|---|---|---|---|---|---|---|
| pattern | R | DER | assessment, ledger, trace, v4 | canonical | Y | M | Y | Y | Direct | ✅ | normalise case |
| pattern_direction | R | DER | opp / assessment | canonical | Y | M | Y | Y | Direct | ✅ | — |
| pattern_timestamp | R | OBS | opportunity | canonical | Y | M | Y | Y | Direct | ✅ | — |
| opportunity_id (legacy) | R | DER | opp_assessment_log | sibling | Y | M | Y | N | — | ✅ | sibling-heavy |
| canonical_opportunity_id | R | DER | assessment, audit, ledger, journal | canonical | Y | M | Y | Y | Direct | ⚠️ | not yet universal (§7) |
| entry_reference | R | DER | opp.proposed_entry; OrderIntent | canonical | Y | M | Y | Y | Norm | ⚠️ | live ref vs simulated bid/ask (§8) |
| structural levels (sup/res, swing) | O | DER | opp + assessment | canonical | Y | M | Y | Y | Direct | ⚠️ | not on every record |
| displacement / geometry | O | DER | opp_assessment | canonical | Y | M | Y | Y | Direct | ⚠️ | analysis-time only |
| sibling_patterns | O | DER | opp.sibling_patterns | canonical | Y | M | Y | Y | Norm | ⚠️ | primary selection non-deterministic across datasets |

**Opportunity gap (structural):** opportunity appears in 3 records (Opportunity, OpportunityAssessment, legacy per-pattern). Target a single Opportunity FACT keyed by `canonical_opportunity_id`.

### 4.D Assessment

| Field | Req | Prov | Persist | Immt | clock | Rec | Res | Status | Gap |
|---|---|---|---|---|---|---|---|---|---|
| assessment_id | R | DER | assessments, opp_assessment_log | Y | M | Y | Y | ⚠️ | two conflicting formats |
| quality (5 sub-scores) | R | DER | audit/ledger/assessment | Y | M | Y | Y | ✅ | — |
| score_strategy / overall_score | R | DER | trace/audit/assessment | Y | S | Y | Y | ✅ | — |
| confidence | O | DER | assessment | Y | M | Y | Y | ✅ | — |
| score_attribution / reasoning | O | DER | audit/trace | Y | S | Y | Y | ✅ | — |
| scoring-input snapshot | R | DER | NOT fully persisted | — | — | Y | ⚠️ | cannot re-derive a score from persisted inputs alone |

### 4.E Decision (EXECUTE / NO_TRADE / RISK_BLOCK / SESSION_BLOCK / PATTERN_REJECT / KILL_SWITCH / DAILY_LOSS_BLOCK / STALE_DATA / FEED_BLOCKED — NEVER collapse)

| Field | Req | Prov | Persist | Immt | clock | Rec | Res | cmp | Status | Gap |
|---|---|---|---|---|---|---|---|---|---|---|
| decision (DecisionOutcome enum) | R | DER | decision_ledger | Y | S | Y | Y | Direct | ✅ | V10 funnel collapses to EXECUTE/NO_TRADE |
| decision_action / stage | R | DER | opp_assessment | Y | S | Y | Y | Norm | ⚠️ | per-stage flags missing post-verdict |
| score | R | DER | trace/audit/assessment | Y | S | Y | Y | Direct | ✅ | — |
| confidence | R | DER | assessment | Y | M | Y | Y | Direct | ✅ | — |
| strategy | R | DER | trace/audit/assessment | Y | M | Y | Y | Direct | ✅ | — |
| strategy_confidence | R | DER | trace | Y | M | Y | Y | Direct | ✅ | — |
| reason / rejection_reason / stage | R | DER | trace/audit | Y | S | Y | Y | Direct | ✅ | item V10 rejection_stage retained |
| decision_timestamp | R | SYS | audit | Y | S | Y | Y | Direct | ✅ | — |
| parent_opportunity (canonical) | R | DER | audit | Y | M | Y | Y | — | ⚠️ | see §7 |
| parent observation (entity_id / obs) | R | DER | audit | Y | M | Y | Y | — | ✅ | identity family mismatch |
| decision_id (uuid) | R | SYS | audit/exec_result | Y | S | Y | Y | — | ✅ | child ID |
| causal_signature | O | DER | ledger | Y | S | N | Y | Norm | ✅ | — |

### 4.F Risk

| Field | Req | Prov | Persist | Immt | clock | Rec | Res | cmp | Status | Gap |
|---|---|---|---|---|---|---|---|---|---|---|
| risk_decision (APPROVED/BLOCKED) | R | DER | audit / ledger | Y | S | Y | Y | Direct | ⚠️ | funnel RISK_BLOCK→shadow 0/216 |
| risk_reason | R | DER | audit/ledger | Y | S | Y | Y | Direct | ⚠️ | partial |
| position_size / volume | R | DER | intent.volume, exec_result, journal | Y | M | Y | Y | Direct | ✅ | — |
| stop_distance (pips, price) | R | DER | audit / risk_deviation | Y | M | Y | Y | Direct | ✅ | live vs simulated reference |
| target_distance | R | DER | audit / risk_deviation | Y | M | Y | Y | Direct | ✅ | — |
| risk_amount / risk_pct | R | DER | risk_deviation, audit | Y | — | Y | Y | Direct | ⚠️ | RiskDev not canonically linked |
| stops_level + requested/confirmed | R | DER/OBS | execution_result | Y | S | Y | Y | Direct | ✅ | pre/post protection |
| rr_effective | O | DER | audit | Y | — | Y | Y | Direct | ✅ | — |
| risk_environment | O | SYST | execution_context | Y | S | Y | Y | Direct | ⚠️ | — |

**Risk gap:** risk is NOT one canonical dataset — it spreads across decision_audit risk fields, `risk_deviation`, and OrderIntent. A single RISK FACT record keyed by canonical is the target.
### 4.G Execution (intent + context + broker request/response + result)

| Field | Req | Prov | Persist | Parent | Immt | clock | Rec | Res | cmp | Status | Gap |
|---|---|---|---|---|---|---|---|---|---|---|---|
| execution_context record | R | OBS/SYST | execution_context | correlation | Y | S | Y | Y | Direct | ✅ | frozen env, forbidden-fields enforced |
| OrderIntent (side, vol, sl, tp, entry_reference) | R | DER | exec path / intent | canonical | Y | M | Y | Y | Direct | ✅ | — |
| bid/ask at DECISION | R | OBS | execution_context | correlation | Y | S | Y | Y | Direct | ✅ | — |
| bid/ask at EXECUTION | O | OBS | NOT standalone | correlation | — | — | Y | Y | Direct | ✖ | **to add** (missing fields in execution_context) |
| requested_price / entry_reference | R | DER | OrderIntent → exec_result | canonical | Y | M | Y | Y | Norm | ✅ | — |
| actual_fill_price | R | OBS | exec_result.fill_price | decision | Y | M | Y | Y | DIRECT | ✅ | — |
| slippage_entry | R | DER | exec_result.slippage | decision | Y | M | Y | Y | DIRECT | ✅ | — |
| retcode | R | OBS | exec_result.retcode | decision | Y | S | Y | Y | — | ✅ | — |
| deal / order_ticket | R | OBS | exec_result.order/deal | decision | Y | S | Y | Y | — | ✅ | — |
| latency_ms | O | SYS | exec_context.latency_ms | correlation | Y | S | Y | Y | Norm | ✅ | — |
| requested_sl/tp + broker_confirmed | R | DER/OBS | execution_result | decision | Y | S | Y | Y | Direct | ✅ | protection status |
| rejection_reason / protection_failure | O | OBS | execution_result | decision | Y | S | Y | Y | — | ✅ | — |
| prep/filled timing decision→fill | O | SYS | execution_result | decision | Y | S | Y | Y | Norm | ⚠️ | execution latency not fully derived |

### 4.H Position lifecycle (opened / modified / closed)

| Event | Required fields | Persist (today) | Parent | Gap |
|---|---|---|---|---|
| opened | position id, open time/price, volume | trade_truth, exec_result, journal | correlation/canonical | TRADE_TRUTH not created at ENTRY; **cascades**: journal absent until close |
| modified | lifecycle events (SL→BE, trailing, partials) | trade_truth_graph, events | position | fragmented; not a single timeline |
| closed | exit time/price, volume, close_reason | trade_truth, trade_journal | position/correlation | — |
| broker ids | deal/ticket/position | broker + exec_result | broker | — |
| lifecycle_state | enum + transition | events, trade_truth_graph | — | — |

**Position gap (verified):** position lifecycle is fragmented across `trade_truth`, `trade_journal`, `trade_truth_graph`, and `events/`. A single lifecycle timeline keyed `position_id → decision_id → canonical_opportunity_id` is required.

### 4.I Outcome (LIVE — realised)

| Field | Source (today) | Gap |
|---|---|---|
| realised P/L (net_pnl) | trade_truth.outcome, trade_journal | — |
| realised R (r_multiple_realised) | trade_truth | — |
| exit reason | trade_truth / trade_journal enum | constrained enum, aligned with shadow exit_reason |
| MAE / MFE (R) | trade_truth mae_r/mfe_r | — |
| holding duration | trade_truth / journal | — |
| commission / swap / costs | trade_journal, trade_truth | — |
| final trade state | trade_truth state | — |
| exit bid/ask/spread/latency | — | ✗ NOT captured for exit → to add |

**Outcome blocker (verified §10):** trade_truth was historically written at CLOSE only, making immediate reconstruction impossible until close; fix direction = create trade_truth chain (entry lifecycle) and update on close. `trade_truth._FORBIDDEN_FIELDS` ensures no simulated/strategy fields cross — preserve this.
## 5. SHADOW capture matrix

**Canonical rule (already in `core/shadow/models.py`):** SHADOW is a **pre-verdict** branch. It has **NO shadow decision stage** — live V10 facts are inherited as `live_facts`. `canonical_opportunity_id` is inherited verbatim onto every event; `shadow_trade_id` is the only ID minted. Every event carries three version dimensions (`schema_version`, `construction_model_version`, `simulation_model_version`). Lifecycle = PLAN → OPEN → PROGRESS → CLOSE (append-only, single writer `core/shadow/runtime.py`).

**Provenance:** all constructed/assumed values are SIMULATED or DERIVED. Nothing in this lane is OBSERVED as a broker fact. Every OPEN event carries the full `simulation_assumptions` block (fill_model=EXACT_PRICE, slippage=ZERO, commission=ZERO, spread=ZERO_COST, position_size=R_NORMALISED) from `core/shadow/assumptions.py`.

### 5.A Shared observation (must reference the exact canonical LIVE observation)

| Field | Req | Prov | Notes |
|---|---|---|---|
| canonical_opportunity_id | R | DER | inherited verbatim from LIVE, on every event |
| symbol | R | OBS | inherited from LIVE market |
| event_market_time (raw broker epoch) + utc + offset | R | OBS/SYS | `market_block()` preserves raw, derives UTC via persisted broker_offset |
| live_facts (entity_id, cycle_id, regime, h4/h1, market_phase, score, v10_action) | R | DER/OBS | namespaced `live_facts` — live V10 facts, never presented as shadow decisions |
| structure (m5 hi/lo, m15 sup/res, h1 swings) | R/O | DER/LIVE | fed into trade construction |

### 5.B Shadow opportunity

| Field | Req | Prov | Notes |
|---|---|---|---|
| pattern, direction, pattern_quality | R | DER | inherited live observation |
| eligible_horizons & horizon_assessments (confidence, reasoning) | R | DER | per-horizon eligibility computed by shadow |
| v10_action / v10_selected_horizon | R | OBS | live V10 verdict (inherited) |

### 5.C Shadow assessment
Shadow reuses the inherited LIVE OpportunityAssessment via `live_facts`; per-horizon local approximation (entry placement vs structure) is the shadow's own DERIVED value. No separate shadow score is fabricated — the plan is built from live facts + horizon geometry.

### 5.D Shadow decision
**None by design.** V10/engine live decision is inherited. Anything that looks like a shadow decision is `live_facts.v10_action` (OBS) — this prevents the illusion of a shadow choosing independently.

### 5.E Shadow risk
Per-horizon SL distance from structure, TP from target RR, risk/reward — all SIMULATED/DERIVED geometry (see OPEN `construction`). Position sizing is `R_NORMALISED` (position_size_model) and is SIMULATED.
### 5.F Shadow execution plan (OPEN event — must be explicitly SIMULATED)

| Field | Req | Prov | Notes |
|---|---|---|---|
| shadow_trade_id | R | DER | child of canonical root; one per horizon |
| construction: direction, entry_price (ask for BUY / bid for SELL), stop_loss, take_profit, risk_distance, intended_rr | R | SIMULATED/DERIVED | from `build_horizon_trade` |
| entry_price_basis | R | SIMULATED | ASK/BID; declared in assumptions |
| simulation_assumptions (fill, slippage, commission, spread, position_size, timeout_bars, checkpoint_interval, pip_convention) | R | SIMULATED | `build_assumptions()` — any change must bump SIMULATION_MODEL_VERSION |
| lifecycle_initial (bars_elapsed=0, mfe/mae) | R | SIMULATED | — |
| construction_model_version | R | SYST | geometry/provenance rules version |

### 5.G Shadow lifecycle — definition of each event

| Event | Emitted when | Contents |
|---|---|---|
| **PLAN** | one per canonical root per cycle | plan_id `nplan_{cycle}_{sym}_{bar}`, all 3 horizons with NOT_ELIGIBLE / CONSTRUCTED / SIMULATED state + confidence + reasoning; root, symbol, market_time |
| **OPEN** | per constructed horizon | full construction + assumptions + lifecycle_initial + shadow_trade_id + live_facts ref |
| **PROGRESS** | every checkpoint interval (default 12 bars) | lifecycle dict (bars_elapsed, mfe/mae, state_log_tail, data_gaps), watermark = last_evaluated_bar_time |
| **CLOSE** | horizon exit (SL/TP/timeout) | exit_price, exit_reason, exit_bar_index, bars_held, closed_at_utc, outcome (pnl_r_multiple, mfe_r, mae_r, risk_distance, intended_rr), trade_state_progression, data_gaps, final_lifecycle |

Every event carries schema/construction/simulation versions + broker_offset + wall clock. Recovery replays ONLY this domain, never legacy datasets.

### 5.H Shadow outcome (implicit in CLOSE)

| Field | Req | Prov | Notes |
|---|---|---|---|
| simulated exit price / exit_reason | R | SIMULATED | SL / TP / timeout |
| simulated P/L (pnl_r_multiple) | R | SIMULATED | computed from closed bars |
| simulated R, MFE_r, MAE_r | R | SIMULATED | from max favourable/adverse |
| holding duration (bars_held) | R | SIMULATED | closed bars |
| simulated costs | R | SIMULATED | ZERO by policy, declared |
| data_gaps | R | DERIVED | honest gaps, never fabricated |

**Impossibility requirement:** shadow outcome is separated (own stream, own `shadow_trade_id`, SIMULATED provenance, no broker ids). A future researcher can never mistake it for broker-realised outcome.
---

## 6. LIVE ↔ SHADOW comparability matrix

**Classification:** DIRECTLY COMPARABLE / COMPARABLE AFTER NORMALISATION / LIVE ONLY / SHADOW ONLY / MUST NOT BE COMPARED.

These directions preserve: OBSERVED LIVE EXECUTION ≠ SIMULATED SHADOW EXECUTION.

| Compare key | LIVE field | SHADOW field | Classification | Notes |
|---|---|---|---|---|
| observation root | entity_id / canonical | canonical_opportunity_id | DIRECTLY COMPARABLE (identity) | canonical is shared verbatim |
| bar timestamp | bar_time | event_market_time (UTC-normalised) | COMPARABLE AFTER NORMALISATION | SHADOW derives UTC from broker offset; LIVE must mirror the same derivation |
| pattern / direction | pattern + direction | pattern + direction (live_facts) | DIRECTLY COMPARABLE | identical source |
| entry reference | entry_reference (OrderIntent) | construction.entry_price | COMPARABLE AFTER NORMALISATION | LIVE entry_reference may differ from raw bid/ask at decision; shadow uses live bid/ask → compare against LIVE bid/ask, not entry_reference |
| bid/ask | bid/ask at decision (exec_context) | entry_price_basis ASK/BID (live) | DIRECTLY COMPARABLE when LIVE captures bid/ask at decision | shadow entry == live bid/ask by construction (EXACT_FILL_COUNTERFACTUAL) |
| spread | spread (exec_context) | spread_policy = ZERO_COST | MUST NOT BE COMPARED | shadow assumes zero spread; LIVE spread is real cost |
| slippage | slippage_entry (exec_result) | slippage_policy = ZERO | MUST NOT BE COMPARED as equals | can be reported as "simulated slippage assumption" only |
| displacement | m15 displacement magnitude | not modelled in shadow geometry directly | COMPARABLE AFTER NORMALISATION (via live_facts) | both derive from same market context |
| SL / TP | sl/tp (OrderIntent) + broker-confirmed | construction.stop_loss/take_profit | COMPARABLE AFTER NORMALISATION | shadow SL is structural (m5/m15/h1); LIVE SL may be risk-managed differently — compare deviation, not raw equality |
| stop/reference price | entry_reference vs actual bid/ask | construction.entry_price | MUST NOT BE COMPARED as fill | see §8 execution staleness |
| pnl / r | realised net_pnl, r_multiple_realised | pnl_r_multiple (simulated) | MUST NOT BE COMPARED as realised facts | report simulated vs realised side-by-side, never merge |
| MFE / MAE | mae_r / mfe_r | mae_r / mfe_r | COMPARABLE AFTER NORMALISATION | same definitions (`compute_mae_r`, `compute_mfe_r`) |
| exit reason | CloseReason enum (incl. manual/management) | exit_reason (SL/TP/timeout) | COMPARABLE AFTER NORMALISATION | map management exits to closest simulated policy; manual close has no shadow equivalent |
| holding duration | duration_seconds | bars_held × M5 interval | COMPARABLE AFTER NORMALISATION | shadow uses closed bars; LIVE uses seconds |
| execution latency | latency_ms | N/A | LIVE ONLY | shadow has zero latency model |
| outcome timing | exit_time (broker) | exit_market_time | COMPARABLE AFTER NORMALISATION | both market-time but shadow is bar-sampled |
| costs (commission/swap) | commission + swap (journal) | commission_policy ZERO | MUST NOT BE COMPARED | cost differential is a finding, not a comparison |
| regime/context | market_context snapshot | live_facts context | DIRECTLY COMPARABLE | same frozen context inherited |
| score / confidence / strategy | audit + assessment | live_facts score/strategy | DIRECTLY COMPARABLE | identical inherited values |
| decision / rejection | DecisionOutcome + reason | v10_action + v10_rejection_stage | DIRECTLY COMPARABLE | both carry live verdict |
| position size | volume (exec_result) | position_size_model R_NORMALISED | COMPARABLE AFTER NORMALISATION | LIVE size is risk-capped; shadow is R-normalised — compare R, not lots |
| risk block | RISK_BLOCK + reason | N/A (shadow pre-verdict) | LIVE ONLY | shadow cannot represent a risk block that stopped execution |
| execution context infra (feed, tick age) | execution_context | N/A | LIVE ONLY | infra facts belong to live lane |
| broker ids | deal/order/ticket | N/A | LIVE ONLY | shadow never has broker ids |

**Preserving the execution distinction at the point of comparison:**
- Compare **intended** (entry_reference, sl, tp) across lanes.
- Compare **filled** (fill_price, slippage) only within LIVE, or against shadow model assumptions as a reported delta, never as equal.
- Any research dataset MUST carry a `lane` column with values `LIVE_EXECUTED` / `SHADOW_SIMULATED` and a provenance tag per numeric field.
---

## 7. Persistence map

### 7.1 The persistence surface actually in the codebase

| Dataset | Producer (write function) | Local | S3 prefix / key | Schema | Domain |
|---|---|---|---|---|---|
| events | `core/event_stream.py` | `events/{D}.jsonl` | `events/symbol={S}/date={D}/` | Envelope | SYSTEM |
| decision_ledger | `core/decision_ledger.py:get_ledger().record` | `logs/decision_ledger/{S}/{D}.jsonl` | `decision_ledger/symbol={S}/date={D}/` | decision_ledger_v1 | DECISIONS |
| decision_audit | `core/decision_audit.py:persist_new_engine_decision_audit` | `logs/decision_audit/{S}_{D}.jsonl` | `decision_audit/symbol={S}/date={D}/` | decision_audit_v1 | DECISIONS |
| decision_trace | `core/decision_trace.py` | `logs/decision_trace/{S}/{D}.jsonl` | `decision_trace/symbol={S}/date={D}/` | decision_trace_v1/v2 | DECISIONS |
| execution_context | `core/execution_context.py:persist_execution_context` | `logs/execution_context/{S}/{D}.jsonl` | `execution_context/symbol={S}/date={D}/` | execution_context_v1 | EXECUTION |
| execution_results | `core/persistence/execution_result_writer.py` | `logs/execution_results/{S}/{D}.jsonl` | `execution_results/symbol={S}/date={D}/` | execution_results_v1 | EXECUTION |
| opportunity_assessment | `core/persistence/opportunity_assessment_writer.py` | `logs/opportunity_assessment_log/{S}/{D}.jsonl` | `opportunity_assessment/symbol={S}/date={D}/` | opportunity_assessment_v1 | OPPORTUNITY |
| assessments | `core/assessment/persistence.py` | `logs/assessments/{S}/{D}.jsonl` | `assessments/symbol={S}/date={D}/` | assessment_v1 | OPPORTUNITY/ASSESSMENT |
| market_context | `core/market_context/persistence.py` | `logs/market_context/{S}/{D}.jsonl` | `market_context/schema_version=market_context_v1/...` | market_context_v1 | MARKET CONTEXT |
| strategy_observations | `core/strategies/observation_persistence.py` | `logs/strategy_observations/{S}/{D}.jsonl` | `strategy_observations/symbol={S}/date={D}/` | strategy_observation_v1 | OBSERVATION |
| shadow_trades (LEGACY) | `core/shadow_trades.py:_persist_shadow_trade` | `logs/shadow_trades/{S}/{D}.jsonl` | `shadow_trades/schema_version=shadow_trades_v2/...` | shadow_trades_v2 | SHADOW |
| research_shadow_trades | `core/research_assessment/research_shadow_engine.py` | `logs/research_shadow_trades/{S}/{D}.jsonl` | `research_shadow_trades/schema_version=research_shadow_trades_v1/...` | research_shadow_trades_v1 | RESEARCH/SHADOW |
| SHADOW runtime v1 (NEW) | `core/shadow/persistence.py:ShadowEventWriter.append` | `logs/shadow_runtime_v1/{S}/{D}.jsonl` (PROVISIONAL) | `shadow_runtime/schema_version=shadow_runtime_v1/symbol={S}/date={D}/part-000.jsonl` | shadow_runtime_v1 | SHADOW |
| trade_truth | `core/trade_truth.py:persist_trade_truth` | `logs/trade_truth/{S}/{D}.jsonl` | `trades/schema_version=trade_truth_v3/symbol={S}/date={D}/` | trade_truth_v3 | OUTCOMES |
| trade_journal | `core/trade_journal.py` | `logs/trade_journal/{D}.jsonl` | `trade_journal/schema_version=trade_journal_v1/date={D}/` | trade_journal_v1 | OUTCOMES |
| trade_truth_graph | `core/trade_truth_graph.py` | `logs/trade_truth_graph/{S}/{D}.jsonl` | `trade_truth_graph/symbol={S}/date={D}/` | trade_truth_graph_v2 | POSITION LIFECYCLE |
| risk_deviation | `core/risk_deviation.py` | `logs/risk_deviation/...` | `risk_deviation/...` | risk_deviation_v1 | RISK |
### 7.2 Duplicated persistence interfaces

| Interface pair | What each does | Authoritative? (based on current architecture + usage) |
|---|---|---|
| `core/v10/persistence_adapter.py:build_v10_decision_record`/`persist_v10_full` vs `core/decision_ledger.py` (DecisionLedgerWriter) | V10 adapter used to write a SECOND ledger row; remediated contract embeds V10 payload as `v10{...}` sub-dict inside the single authoritative `DecisionLedgerWriter` row | **decision_ledger.py is authoritative.** The V10 adapter now only builds the `v10` sub-dict (`build_v10_ledger_entry` → `_write_to_ledger`). `build_v10_decision_record` (dataset `v10_decision_v1`) is research/compat |
| `core/persistence/opportunity_assessment_writer.py` vs `core/assessment/persistence.py` | Two opportunity/assessment datasets with different `assessment_id` formats | **assessment_v1 (`core/assessment/persistence.py`) is the canonical assessment domain; opportunity_assessment_v1 is the observational log.** assessment_id format conflict (§3) must be resolved |
| `core/shadow_trades.py` (legacy) vs `core/shadow/persistence.py` (NEW) | Two SHADOW persistence writers; NEW is isolated v1 runtime stream, legacy is disabled | **NEW (`core/shadow/persistence.py`) is authoritative for the future SHADOW dataset.** Legacy stream preserved read-only, research-only |
| `core/execution_context.py` + `core/persistence/execution_result_writer.py` | intent context (pre-execution) + result (post-broker) | **Both authoritative for their phase** (context then result); both must carry same decision_id/correlation_id/canonical |

### 7.3 Canonical destination per domain (target after implementation phase)

| Required field-group | CURRENT LOCATION | CANONICAL DESTINATION | WRITE FUNCTION | PARENT ID |
|---|---|---|---|---|
| Observation (bar, OHLC, bid/ask/spread) | fragmented (audit, opp, context) | OBSERVATIONS dataset (per-bar canonical) | market data / new observer | canonical_opportunity_id |
| Market context | market_context dataset + audit | MARKET CONTEXT (market_context_v1) | `core/market_context/persistence.py` | canonical_opportunity_id |
| Opportunity | opp + opp_assessment log | OPPORTUNITY (canonicalised) | opportunity factory + assessment writer | canonical_opportunity_id |
| Assessment | assessments + audit | ASSESSMENT (assessment_v1) | `core/assessment/persistence.py` | canonical_opportunity_id |
| Decision | decision_ledger + audit + trace | DECISIONS (ledger canonical; audit/trace explainable) | `core/decision_ledger.py` | canonical + decision_id |
| Risk | audit risk + risk_deviation | RISK (risk_deviation canonicalised) | `core/risk_deviation.py` | canonical_opportunity_id |
| Execution intent+context | execution_context | EXECUTION (execution_context_v1) | `core/execution_context.py` | correlation + decision + canonical |
| Execution result | execution_results | EXECUTION (execution_results_v1) | `core/persistence/execution_result_writer.py` | decision_id + canonical |
| Position lifecycle | trade_truth + trade_truth_graph + journal | OUTCOMES (trade_truth canonical) + lifecycle timeline | `core/trade_truth.py` | position_id + canonical |
| Outcome | trade_truth + journal | OUTCOMES (trade_truth canonical, journal aggregator) | `core/trade_truth.py`, `core/trade_journal.py` | trade_id + canonical |
| Shadow | shadow_runtime_v1 (NEW), legacy shadow_trades, research_shadow_trades | SHADOW (shadow_runtime_v1, PROVISIONAL) | `core/shadow/persistence.py` | canonical root + shadow_trade_id |
| Research dataset | derived | RESEARCH | research_engine builders | — |

**Persistence principles to enforce (already binding):** local is truth, single gate (`EVENT_STREAM_S3_MIRROR`), append-only immutable, one-writer-per-dataset, versioned schemas, Hive partitioning.

> ⚠️ **S3 bucket inconsistency (known defect, NOT to fix now):** the PERSISTENCE_ARCHITECTURE_AUDIT_FINAL lists bucket `trading-bot-data-mk1`, but module-level constants in `decision_ledger`, `decision_audit`, `execution_context`, `execution_results`, `trade_truth`, and NEW shadow point to `v10-engine`. Reconcile in the implementation phase.
---

## 8. Reconstruction test

The reconstruction MUST be possible using persisted records ALONE (no runtime state, no memory).

### 8.1 LIVE — one real executed trade

Required records in order:

| # | Record | Persisted dataset | Join/lookup key | Present today? |
|---|---|---|---|---|
| 1 | market observation (bar) | strategy_observations / decision_audit market_state | entity_id / canonical | ⚠️ per-bar standalone observation record missing |
| 2 | market context | market_context_v1 / audit | canonical / entity_id | ✅ |
| 3 | opportunity | opportunity_assessment_log / assessments | opportunity_id / canonical | ✅ (3-record fragmentation) |
| 4 | assessment | assessments / audit | assessment_id (conflicting) / canonical | ✅ |
| 5 | decision | decision_ledger (EXECUTE) | entity_id + cycle_id / canonical | ✅ |
| 6 | risk | audit risk / risk_deviation | canonical | ⚠️ risk blocked vs approved, fragmented |
| 7 | execution intent | OrderIntent (via exec result fields) | decision_id / canonical | ✅ |
| 8 | broker execution | execution_results | decision_id / correlation_id | ✅ |
| 9 | position lifecycle | trade_truth_graph / events / trade_truth | position_id / correlation | ⚠️ fragmented, no single timeline |
| 10 | outcome | trade_truth (closed) / trade_journal | trade_id / correlation | ⚠️ trade_truth originally at close only |

**Minimum records for full LIVE reconstruction:**
- decision_audit (has correlation_id, decision_id, entity_id, canonical, bid/ask at decision)
- execution_results (fill, retcode, deal, slippage, decision_id)
- trade_truth GRAPH or lifecycle event chain (position → open → modify → close)
- trade_truth outcome row (final r, pnl, exit reason, mae/mfe)
- assessment + opportunity + market_context via canonical

**Missing links (blocking):**
1. `position_id ↔ canonical_opportunity_id` direct link (currently only via correlation_id → audit → entity_id, 2 hops; legacy TRADE_TRUTH lacked entity_id/canonical)
2. trade_truth not created at ENTRY → position lifecycle gaps until close
3. execution_context lacks `bid/ask at execution` and `bid/ask at exit` (only at decision)
4. NO_TRADE decisions have `correlation_id=""` → cannot join those cycles to context (P2 unresolved)
5. observation per-bar canonical record doesn't exist as a standalone dataset — bar OHLC floats inside audit only for EXECUTE-ish cycles

### 8.2 SHADOW — one shadow trade

Required records (all from NEW shadow_runtime_v1 stream):

| Event | Covers | Present today (NEW runtime)? |
|---|---|---|
| PLAN | shared observation + opportunity + horizons assessed | ✅ |
| OPEN | shadow plan/risk/execution-intent (construction + assumptions) | ✅ |
| PROGRESS* | lifecycle state | ✅ (checkpointed) |
| CLOSE | shadow outcome (pnl_r, mfe/mae, exit reason, bars) | ✅ |

**Join to LIVE parent:** OPEN/CLOSE carry `canonical_opportunity_id` (inherited). If the LIVE lane also carries the same canonical root, reconstruction of "same bar live trade vs shadow horizon" is direct (0 hops).

**Missing links (blocking):**
1. LIVE rows do NOT yet all carry `canonical_opportunity_id` → shadow-to-live join requires the identity migration first
2. Legacy shadow_trades records carry `correlation_id` only (no canonical/entity in 36% of records) → these CANNOT be joined to LIVE (classify LEGACY, research-only)
3. NEW shadow stream directory/schema is PROVISIONAL (`logs/shadow_runtime_v1`); final production path is a pending decision

---

## 9. Research dataset requirements

### 9.1 Required final research-ready LIVE and SHADOW datasets

| Requirement | LIVE dataset must contain | SHADOW dataset must contain | join |
|---|---|---|---|
| opportunity frequency | pattern, bar_time, opportunity_id, canonical | canonical (all horizons with eligibility state) | canonical |
| decision frequency | decision (full enum), decision_id, timestamp | v10_action (inherited) | canonical |
| strategy selection | strategy, strategy_confidence | strategy (inherited) | canonical |
| risk blocks | risk_decision, risk_reason, position sizing inputs | n/a pre-verdict (SHADOW ONLY) | — |
| execution eligibility | execution_context (spread, feed, tick_age), exec_attempt | entry eligibility per horizon | canonical |
| simulated vs realised entry | entry_reference, bid/ask at decision/exec, fill_price, slippage | construction.entry_price, entry_price_basis | canonical + horizon |
| simulated vs realised outcome | r_realised, pnl_net, exit_reason, mfe/mae, duration | pnl_r, exit_reason, mfe/mae, bars_held | canonical |
| expectancy | n, sum R, per-strategy/per-horizon | same (simulated) | canonical |
| win rate | win/loss per outcome | same (simulated) | canonical |
| MFE/MAE | mfe_r/mae_r | mfe_r/mae_r | canonical |
| displacement | m15 displacement magnitude | live_facts displacement | canonical |
| regime/context | market_context snapshot | live_facts context | canonical |
| costs | commission, swap | ZERO (declared policy) | canonical |
| slippage | slippage_entry | ZERO (declared policy) | canonical |
| latency | latency_ms | — | LIVE ONLY |
| temporal/session effects | session_state, event_market_time (UTC) | same (market-time based) | canonical |

### 9.2 Dataset lineage rules (do not build yet)

- Every research row MUST carry: `canonical_opportunity_id`, `lane` (`LIVE_EXECUTED` / `SHADOW_SIMULATED`), a `provenance` tag per numeric field, `schema/construction/simulation version`, and `market_time_utc` (derived with persisted broker offset).
- No LIVE row may ever be derived from a shadow record, and no shadow record may be presented as broker fact.
- Keep two physical datasets (LIVE research, SHADOW research), joinable on canonical; a derived VIEW may pair them but must never merge.
---

## 10. Shadow population mapping (existing populations — DO NOT merge or delete)

Existing populations from the SHADOW paper-trail (legacy `shadow_trades` + `research_shadow_trades` + NEW `shadow_runtime_v1`):

| Population | What it represents | Upstream observation it references | Decision it references | Joins to LIVE today? | Lifecycle complete? | In canonical SHADOW dataset? | Research-only? |
|---|---|---|---|---|---|---|---|
| **V10_PRIMARY** (`shadow_type=V10_PRIMARY`) | Legacy primary shadow for EXECUTE decisions | correlation_id → entity_id (not canonical) | EXECUTE decision | ⚠️ only via correlation → audit → entity (or entity_id when present); 36% lack entity | Yes (open→evaluate→close persisted) | ⚠️ **RETIRED** as authoritative baseline (Phase 1I-C) — redesignated research/historical | ✅ research-only |
| **HORIZON_SCALP** (`evaluated_horizon=SCALP`) | Per-horizon counterfactual (M5 structure SL, 2R TP) | entity_id (95% present) / horizon correlation | ALL decisions with pattern (EXECUTE + NO_TRADE) | ✅ via entity_id | Yes (legacy lifecycle) | ⚠️ legacy-format; the NEW runtime supersedes as SHADOW (PLAN/OPEN/PROGRESS/CLOSE) | ✅ research-only |
| **HORIZON_INTRADAY** | Per-horizon counterfactual (M15 SL, 3R) | entity_id | ALL decisions with pattern | ✅ via entity_id | Yes | ⚠️ legacy-format | ✅ research-only |
| **HORIZON_EXTENDED** | Per-horizon counterfactual (H1 SL, 4R) | entity_id | ALL decisions with pattern | ✅ via entity_id | Yes | ⚠️ legacy-format | ✅ research-only |
| **RESEARCH_SHADOW** (`research_shadow_trades`, `shadow_type=CANDIDATE_*` / RESEARCH_WOULD_EXECUTE) | Promotion/EV disagreement engine | research candidate (candidate_id), no entity | RESEARCH_WOULD_EXECUTE | ❌ NOT LINKED to LIVE (no callers found) | Varies | ⚠️ separate dataset, not canonical SHADOW | ✅ research-only |

Key conclusions:
- The **NEW `shadow_runtime_v1`** is the canonical SHADOW dataset going forward (one PLAN → OPEN per horizon, PROGRESS, CLOSE, keyed by canonical root + shadow_trade_id). Legacy `shadow_trades` and `research_shadow_trades` are **legacy/historical**, preserved read-only.
- **V10_PRIMARY is formally retired** as the authoritative shadow baseline (per `research_engine/lifecycle/candidate_auto_evaluator.py` Phase 1I-C); it remains as historical research data only.
- Legacy records with empty entity_id (36%) or missing canonical **cannot** be joined to LIVE → classify LEGACY and exclude from canonical research populations.

---

## 11. Existing known lineage problems (verified — classify severity)

Mission §11 list, each classified BLOCKING / NON-BLOCKING / ORPHANED-LEGACY:

| # | Problem | Evidence | Severity |
|---|---|---|---|
| 1 | Shadow records lacking entity_ids | 36% of legacy shadow records (shadow_lineage_foundation_audit); NEW runtime has no entity_id field either (uses canonical only) | **BLOCKING** for shadow→live join via entity; mitigated if canonical is used instead |
| 2 | Decision ledger records lacking entity_id | Pre-engine exits write entity_id="" (identity audit loss point #3); decision_ledger has no decision_id/runtime_session_id fields | **BLOCKING** for full decision reconstruction on blocked cycles |
| 3 | observation_id mismatch: strategy `EURUSD_{cycle}_{ts}` vs V10 hashed 16-hex vs entity_id | §3.7; three concurrent identity families | **BLOCKING** for cross-dataset reconstruction |
| 4 | execution_context missing fields | No bid/ask at execution, no exit bid/ask/spread/latency; entity_id/cycle_id absent by design | **BLOCKING** for slippage/latency-vs-shadow comparison |
| 5 | duplicated persistence interfaces | V10 persistence_adapter vs decision_ledger (remediated); two assessment writers; two shadow writers | **NON-BLOCKING** redundancy (authority decided §7.2) — but assessment_id format conflict is BLOCKING |
| 6 | shadow records without corresponding decision_ledger entries | 623 V10 shadows in non-execution/no-ledger periods (funnel analysis) | **NON-BLOCKING** (historical gap; classify pre-ledger LEGACY) |
| 7 | inability to join shadow EXECUTE records to LIVE decision records | legacy join only via correlation→audit→entity (hops); 36% entity missing | **BLOCKING** until canonical joins implemented |
| 8 | simulated entry reference vs actual bid/ask | shadow entry == live bid/ask (EXACT_FILL) but LIVE record stores entry_reference not raw bid/ask in exec lane | **BLOCKING** for "entry assumption vs actual" analysis until LIVE captures bid/ask at decision+exec |
| 9 | execution staleness | tick_age_ms/spread captured in exec_context but not on every decision, and only EXECUTE lane; bar age not persisted per record | **NON-BLOCKING** but required for staleness-analysis of shadow vs live |
| 10 | RISK_BLOCK representation | Legacy funnel showed RISK_BLOCK matched to shadow 0/216; risk split across audit + risk_deviation; no canonical RISK FACT | **BLOCKING** for risk-elbow analysis until single RISK dataset |

### 11.1 Additional verified defects (beyond mission list)
- **S3 bucket mismatch** `trading-bot-data-mk1` (docs) vs `v10-engine` (module constants) — NON-BLOCKING, reconcile at implementation.
- **assessment_id double-format** (`core/assessment/builder.py:63` vs `core/persistence/opportunity_assessment_writer.py:101`) — BLOCKING for assessment joins.
- **trade_truth not created at ENTRY, only CLOSE** — BLOCKING for immediate lifecycle reconstruction (LINEAGE_REPORT_SUMMARY finding).
- **NO_TRADE correlation_id empty** — NON-BLOCKING for decisions, BLOCKING for full join graph on rejected opportunities.
---

## 12. Blocking gaps (must be resolved before the data contract is operatible)

| Blocking gap | What breaks | Where it must be implemented |
|---|---|---|
| G1. Canonical root not propagated to every LIVE record | No single shared root → LIVE↔SHADOW reconstruction impossible | Propagate `canonical_opportunity_id` to decision_audit, decision_ledger, execution_context, execution_results, trade_truth, trade_journal, events |
| G2. Three observation identity families (strategy obs / V10 hash / entity) | Cross-dataset join impossible without normalisation | One canonical mapping layer; keep compatibility keys but never join on them |
| G3. trade_truth not created at ENTRY | Cannot reconstruct immediate lifecycle; journal cascades missing | Create trade_truth chain at entry; update on modify/close |
| G4. execution_context missing fields (bid/ask at exec, exit, latency) | Cannot compute real vs simulated entry/slippage/timing on exact bars | Add fields to execution_context builder + persisted schema |
| G5. risk fragments across datasets | RISK_BLOCK analysis impossible per decision | Single canonical RISK FACT keyed by canonical |
| G6. Legacy shadow entity_id missing (36%) + no canonical | Shadow population cannot join to LIVE | NEW runtime is keyed on canonical (already correct); build a canonical legacy-mapping table for `shadow_trades_v2` |
| G7. NEW shadow dataset PROVISIONAL (logs/shadow_runtime_v1, bucket v10-engine) | Production location/ownership undefined | Decide canonical SHADOW dataset name/location; contract-gate it |
| G8. NO_TRADE correlation_id empty | Rejected-opportunity join graph incomplete | Generate correlation_id on ALL paths (P2) + carry canonical |
| G9. assessment_id format conflict | Assessment joins unreliable | Unify minting to one owner (assessment_v1) |
| G10. S3 bucket inconsistency | Data lake fragmentation | Reconcile to one canonical bucket |

---

## 13. Proposed implementation order (after this contract is approved — NOT NOW)

Phase order respects immutability and no-live-impact:
1. **Identity propagation (G1, G2).** Extend producers so decision/audit/ledger/exec/context/trade rows carry `canonical_opportunity_id`. Add a canonical legacy-mapping reader (read-only).
2. **Observation canonicalisation (G2).** Define per-bar OBSERVATIONS canonical record; mint canonical at observation time; alias tables for strategy_obs and V10 hash.
3. **trade_truth chain at entry (G3).** Change writer to create a lifecycle chain at entry, append modifications, close finalises (preserving `_FORBIDDEN_FIELDS`).
4. **execution_context completeness (G4).** Add bid/ask at execution, exit snapshot, latency chain. Additive field changes only.
5. **Canonical RISK FACT (G5).** Single risk record per canonical; risk_deviation/audit risk fields feed it.
6. **Shadow canonicalisation (G6, G7).** Fix NEW runtime provenance/version naming; decide production dataset location; leave legacy as read-only LEGACY.
7. **NO_TRADE correlation (G8)** and **assessment_id unification (G9)**.
8. **Persistence consolidation per §7.1/§7.2** (one writer per dataset enforced; bucket reconciliation G10).
9. **Enable the SHADOW gate** ONLY after §5-§6 contract is satisfied, and verify on a shadow-only trial. No LIVE order until proofs pass.

### 13.1 Capture-status ledger (final classification)

**ALREADY CAPTURED CORRECTLY**
- decision_ledger (all DecisionOutcome, causal_signature, v10 sub-dict) — EXECUTE/NO_TRADE/RISK_BLOCK preserved distinct ✅
- decision_audit/decision_trace (rich explainable decision trail, rejection_stage retained) ✅
- execution_context frozen-at-decision env snapshot (spread, bid/ask, feed, latency, forbidden-fields enforced) ✅
- execution_results (fill, retcode, deal, slippage, protection status, decision_id linkage) ✅
- NEW shadow_runtime_v1 event stream (PLAN→OPEN→PROGRESS→CLOSE, canonical root, three version dims, SIMULATED provenance, honest gaps) ✅
- trade_truth _FORBIDDEN_FIELDS boundary (no simulated/strategy contamination) ✅
- market_context_v1 (frozen decision-time context) ✅

**PARTIALLY CAPTURED**
- observation per-bar OHLC (embedded in audit/opportunity, no standalone canonical) ⚠️
- entry/exit price at decision vs execution vs fill ⚠️
- position lifecycle (fragmented across 3 datasets) ⚠️
- legacy shadow population entity_id (64% present, 36% missing) ⚠️

**CAPTURED BUT NOT CANONICALLY LINKED**
- trade_truth ↔ execution_results ↔ decision_audit (only via 2-hop correlation join; canonical root missing on legacy rows)
- risk_deviation (exists but not keyed to canonical)
- strategy_observations (mint its own `{sym}_{cycle}_{ts}`; not linked to canonical)
- legacy shadow_trades_v2 (keyed on correlation, not canonical)

**NOT CURRENTLY CAPTURED**
- bid/ask at execution & at exit (execution_context)
- standalone per-bar OBSERVATION canonical record
- per-decision full scoring-input snapshot (cannot recompute scores)
- exit-side execution context (exit spread/latency)
- canonical root on every LIVE row

**REQUIRES IMPLEMENTATION**
- G1–G10 (§12) — identity propagation, canonical observations, trade_truth entry chain, execution_context completeness, canonical RISK FACT, shadow production dataset decision, correlation on all paths, assessment_id unification, one-writer consolidation, bucket reconciliation.

---

*End of contract. No source files modified, no config changed, no Shadow gate enabled, no trades placed. This is a blueprint only; the FORENSIC DEFINITION phase is complete.*