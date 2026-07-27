# OBSERVER QUERY ARCHITECTURE

**Status:** Design blueprint. No implementation until validated.
**Purpose:** Define how natural language questions flow from input to structured response.
**Constraint:** The Observer never modifies the trading system. Read-only at every stage.

---

## 1. Query Pipeline (End-to-End)

```
USER INPUT (natural language)
    │
    ▼
┌─────────────────────┐
│  1. INTENT LAYER    │  Classify what the user is asking
│                     │  Output: Intent(action, symbol, confidence)
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  2. ROUTING LAYER   │  Map intent to evidence domain(s)
│                     │  Output: list[Domain] with relevance scores
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  3. EVIDENCE LAYER  │  Retrieve raw data from identified sources
│                     │  Output: Evidence(records, timestamps, fields)
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  4. REASONING LAYER │  Interpret evidence, construct causal chain
│                     │  Output: Explanation(chain, conclusion, confidence)
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  5. RESPONSE LAYER  │  Format explanation for human consumption
│                     │  Output: Structured text answer
└─────────────────────┘
```

---

## 2. Layer 1: Intent Classification

### Input
Raw text from user (any format — question, keyword, command).

### Process

```
1. Exact command check (backward compat: "state", "health", "config", etc.)
2. Pattern matching against known intent templates
3. Entity extraction (symbol: EURUSD, trade_id: pos_12345)
4. Confidence scoring (0.0–1.0)
5. Fallback: "route" intent if no confident match
```

### Output

```python
Intent(
    action="explain",       # what to do
    symbol="EURUSD",       # extracted entity
    trade_id="",           # if trade-specific
    question="...",        # original text (for routing fallback)
    confidence=0.85,       # how sure we are
)
```

### Intent Categories

| Intent | Triggers On | Routes To |
|--------|------------|:---------:|
| state | "running", "alive", "status" | Runtime reader |
| health | "datasets", "fresh", "stale", "quality" | Health checker |
| config | "enabled", "config", "limits", "flags" | Config reader |
| explain | "why didn't", "rejected", "decision" + SYMBOL | Decision explainer |
| trade | "why did ... lose/win" + TRADE_ID | Trade explainer |
| trades | "win rate", "pnl", "performance", "pattern" | Trade summary |
| guards | "blocked", "guard", "veto", "risk block" | Guard stats |
| domains | "what do you know", "domains" | Domain lister |
| route | (anything unrecognised) | Domain router |
| help | "help", "?" | Help text |

### Design Decisions

- **No AI/LLM in intent classification.** Pure keyword + regex matching is sufficient for a domain-specific system with ~10 intent types.
- **Confidence threshold: 0.5.** Below this → fallback to domain routing.
- **Exact commands always win.** "state" never gets misrouted.

---

## 3. Layer 2: Domain Routing

### Input
An `Intent` object (from Layer 1). If `intent.action == "route"`, the original question is routed.

### Process

For direct intents (state, health, config, trades, guards):
```
Skip routing → go directly to evidence layer with predetermined source.
```

For routed questions:
```
1. Tokenise question
2. Score each of 15 domains by keyword overlap
3. Score by answer-phrase overlap
4. Return top 3 domains ranked by relevance
```

### Output

```python
[
    ("decision", Domain(...), 3.0),   # Most relevant
    ("risk", Domain(...), 1.5),       # Secondary
    ("execution", Domain(...), 0.5),  # Tertiary
]
```

Each domain carries:
- `evidence_sources`: exact file paths to query
- `authority_files`: where the design truth lives
- `answers`: what questions this domain can answer

### Design Decisions

- **Maximum 3 domains returned.** More creates noise.
- **Relevance score is additive.** Multiple keyword matches compound.
- **Unknown questions get honest "I don't know" + suggestions.** Never fabricate.

---

## 4. Layer 3: Evidence Collection

### Input
A domain (or direct action) with known evidence sources.

### Process

Depending on intent:

| Action | Evidence Source | Query Method |
|--------|:-------------:|:------------:|
| state | `runtime/heartbeat.json` + `core/config.py` | Read JSON + import config |
| health | `logs/` (24 subdirs) | List files, check mtime, count lines |
| config | `core/config.py` | Import and read attributes |
| explain | `logs/decision_ledger/{SYM}/latest.jsonl` + `logs/decision_trace/{SYM}/latest.jsonl` | Read last line, parse JSON, join by entity_id |
| trade | `logs/trade_journal/*.jsonl` | Search by trade_id |
| trades | `logs/trade_journal/*.jsonl` | Read all, filter by date, aggregate |
| guards | `logs/decision_ledger/` (RISK_BLOCK records) | Filter + count by risk_flag |
| route | (none — domain metadata only) | Return domain descriptions |

### Output

```python
Evidence(
    records=[...],          # Raw data records retrieved
    source_files=["..."],   # Which files were read
    timestamp="...",        # When the evidence was generated
    completeness="full",    # or "partial" if data missing
)
```

### Design Decisions

- **Always read latest file first.** Questions are usually about recent state.
- **Never read ALL history** unless explicitly asked (performance).
- **Return raw records.** Reasoning layer interprets.
- **If source missing → Evidence(completeness="missing", records=[]).** Never crash.

---

## 5. Layer 4: Reasoning

### Input
Evidence records from Layer 3.

### Process

Depends on intent:

**For `explain` (decision explanation):**
```
1. Read decision_ledger record → decision + reason
2. If entity_id present → join to decision_trace for component scores
3. Build causal chain: regime → pattern → score → stage → reason
4. Identify terminal stage (where pipeline stopped)
5. Determine "closest flip" (what would change the decision)
```

**For `trade` (trade outcome explanation):**
```
1. Read trade_journal → entry, exit, close_reason, pnl
2. Compute R-multiple from risk distance
3. Classify: market-driven loss vs system error vs management exit
4. Generate human explanation
```

**For `trades` (performance analysis):**
```
1. Aggregate: wins, losses, total pnl
2. Compute: win_rate, avg_R, avg_duration
3. Group by: horizon, pattern, symbol
4. Rank: best/worst performers
```

**For `guards` (risk analysis):**
```
1. Count RISK_BLOCK by guard name
2. Count by symbol
3. Identify most-blocking guard
```

**For `route` (domain routing — no deep reasoning):**
```
1. Return domain descriptions + evidence locations
2. No data retrieval — just navigation guidance
```

### Output

```python
Explanation(
    conclusion="EURUSD was rejected because...",
    chain=["regime: RANGING", "pattern: THREE_WHITE_SOLDIERS", "stage: swing", "reason: h1_swing_bearish"],
    confidence=0.9,
    source="decision_ledger + decision_trace",
)
```

### Design Decisions

- **Reasoning is deterministic.** Same evidence → same explanation. No randomness.
- **Causal chain is explicit.** The explanation shows the path, not just the conclusion.
- **Confidence reflects data completeness.** If decision_trace not found → confidence drops.
- **Never infer beyond evidence.** If we can't prove it, we say "unknown".

---

## 6. Layer 5: Response Generation

### Input
An `Explanation` from Layer 4.

### Process

```
1. Select format based on intent:
   - explain → structured evidence display (Symbol, Decision, Reason, Stage, Evidence)
   - trades → statistical summary (counts, rates, rankings)
   - state → key-value pairs (status, mode, symbols)
   - route → domain guidance (where to look)

2. Apply formatting:
   - Concise (no wall of text)
   - Key info first
   - Evidence cited
   - ASCII-safe (Windows terminal)
```

### Output

Plain text to terminal. Structured for readability:

```
  Symbol:   EURUSD
  Decision: NO_TRADE
  Reason:   swing_blocked: h1_swing_bearish (BUY blocked without H1 BOS)
  Stage:    swing
  Pattern:  THREE_WHITE_SOLDIERS
  Regime:   RANGING
  Chain:    session+pattern+regime→swing_filter
```

### Design Decisions

- **No markdown in terminal output.** Plain text with indentation.
- **Most important information first.** Decision before evidence details.
- **Evidence source always cited.** User can verify independently.
- **Graceful degradation.** Missing data shown as "?" not omitted silently.

---

## 7. Error Handling (Per Layer)

| Layer | Failure Mode | Behaviour |
|:-----:|:------------:|:----------|
| Intent | Cannot classify | action="route", confidence=0.3, fall through to domain routing |
| Routing | No domain matches | Return "I don't know" + suggestion list |
| Evidence | File not found | Evidence(completeness="missing"), reasoning skips that source |
| Evidence | Parse error | Skip record, continue with what's available |
| Reasoning | Insufficient evidence | Conclusion = "insufficient data", confidence drops |
| Response | Unicode error | ASCII fallback encoding |

**Principle:** The Observer never crashes. Every failure mode produces a degraded-but-useful response.

---

## 8. Current Implementation Mapping

| Layer | Implemented In | Status |
|:-----:|:--------------|:------:|
| Intent | `system_intelligence/intent.py` | ✅ v1 complete |
| Routing | `system_intelligence/domains.py` | ✅ v1 complete (15 domains) |
| Evidence | `system_intelligence/explain.py`, `state.py`, `health.py`, `trades.py`, `guards.py` | ✅ v1 complete |
| Reasoning | Embedded in evidence modules (explain, trades) | ⚠️ Mixed with evidence |
| Response | `system_intelligence/console.py` | ✅ v1 complete |

### Gaps for v2

| Gap | Current State | v2 Improvement |
|:----|:--------------|:---------------|
| Reasoning is coupled to evidence | explain.py does both retrieval AND interpretation | Separate into `evidence.py` + `reasoning.py` |
| No cross-domain queries | Each intent maps to ONE action | Allow "why + how much" to chain explain + trades |
| No time-range awareness | `explain` always reads latest | Add "yesterday" / "last week" temporal parsing |
| No comparison capability | Cannot answer "better than last week?" | Add historical comparison in reasoning layer |
| Response is text-only | Terminal output | Add JSON mode for programmatic consumers |

---

## 9. Future Extension Points

### Adding a New Question Type

```
1. Add patterns to intent.py → _INTENT_PATTERNS
2. Add handler method to observer.py
3. Add evidence reader (or reuse existing)
4. Add response formatting to console.py
```

No architectural change needed. The pipeline stays the same.

### Adding a New Domain

```
1. Add Domain() entry to domains.py → DOMAINS dict
2. Define: keywords, evidence_sources, answers, authority_files
```

Routing automatically includes it. No other changes.

### Adding a New Evidence Source

```
1. Create reader function in appropriate module (or new module)
2. Wire it into the relevant intent handler in observer.py
```

### Upgrading to AI-Powered Reasoning

If an LLM is added later:
- Layers 1-3 remain unchanged (deterministic, fast)
- Layer 4 (Reasoning) could be enhanced with LLM for complex multi-domain questions
- Layer 5 (Response) could generate more natural explanations
- **Constraint preserved:** LLM reasoning must still cite evidence sources. No fabrication.

---

## 10. Validation Criteria

The architecture is valid when:

| Test | Pass Condition |
|------|:---------------|
| Direct commands work | "state" → runtime state (no routing overhead) |
| Natural language works | "Is the bot running?" → same result as "state" |
| Symbol extraction works | "Why didn't GBPUSD trade?" → explain("GBPUSD") |
| Unknown questions degrade gracefully | "What is love?" → "I don't know" + suggestions |
| Missing data doesn't crash | Delete heartbeat.json → state() returns "UNKNOWN" |
| Cross-domain question routes correctly | "Are datasets healthy and is the bot running?" → Persistence + Runtime |
| Response is always ASCII-safe | No UnicodeEncodeError on Windows |
| No query modifies system state | Read-only verified by architecture |

---

*End of Observer Query Architecture.*
