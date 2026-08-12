# Research Control Plane — Operating Procedure

## Overview

The research control plane provides a CLI interface for using the V10 research engine to optimise the trading bot.

**Normal workflow: targeted research, NOT the full 45-question bank.**

## Commands

| Command | Purpose |
|---------|---------|
| `python research.py status` | Check current research state |
| `python research.py <QUESTION_ID>` | Run one question (e.g., `python research.py M-002`) |
| `python research.py inspect <QUESTION_ID>` | Inspect latest result without rerunning |
| `python research.py angle <ANGLE>` | Run all questions for one angle |
| `python research.py all` | Run the full 45-question bank |
| `python research.py bottleneck` | Identify research-supported bottleneck |
| `python research.py next` | Recommend next investigation |
| `python research.py optimisation list` | List recorded optimisations |
| `python research.py optimisation create` | Record a new optimisation |
| `python research.py optimisation validate <ID>` | Check validation status |

## Operating Procedure

### Step 1: Check status

```
python research.py status
```

See how many questions are complete, inconclusive, or blocked.

### Step 2: Run bottleneck analysis

```
python research.py bottleneck
```

Identifies the strongest research-supported performance bottleneck from existing evidence.

### Step 3: Investigate specific questions

```
python research.py M-002
python research.py M-004
```

Run targeted questions to resolve uncertainty. Check analytical sample size and evidence.

### Step 4: Inspect results

```
python research.py inspect M-002
```

See the full finding without rerunning.

### Step 5: Get next recommendation

```
python research.py next
```

Prioritised list of what to investigate next.

### Step 6: Decide on optimisation

If evidence is strong enough, record the proposed change:

```
python research.py optimisation create
```

### Step 7: Implement the bot change (separately)

Make the code change to the trading bot. The research system does not automatically modify trading parameters.

### Step 8: Collect new data

Run the bot. New trades/decisions will enter the data platform.

### Step 9: Validate

```
python research.py optimisation validate OPT-XXXXXX
```

Rerun validation questions and compare pre/post.

### Step 10: Repeat

Continue the cycle: research → evidence → decision → change → validation.

## When to use each mode

| Situation | Command |
|-----------|---------|
| Quick check on one component | `python research.py <QID>` |
| Investigate an entire domain | `python research.py angle market` |
| Full research snapshot | `python research.py all` |
| Just want to read results | `python research.py inspect <QID>` |
| What's the biggest problem? | `python research.py bottleneck` |
| What should I look at next? | `python research.py next` |

## Available Angles

- `execution` — Trade outcomes, slippage, duration, exit reasons
- `decision` — Scoring, EV, calibration, thresholds, opportunity quality
- `market` — Regime, HTF alignment, volatility, structure, session
- `strategy` — Strategy family, pattern, selection accuracy, conditions

## Data Flow

```
Bot generates data (trades, decisions, market observations)
     ↓
Universes are built from logs/ at runtime
     ↓
Outcome enrichment joins execution results to other universes
     ↓
Question runner resolves populations and executes primitives
     ↓
Findings are saved to reports/research/questions/{QID}/
     ↓
You inspect results and decide what to change
```

No manual JSONL regeneration required. The research commands use the canonical data path.

## Question-Specific Parameters

Some questions have explicit parameter mappings (not primitive defaults):

| Question | Field | Value | Reason |
|----------|-------|-------|--------|
| M-002 | feature_field | htf_alignment_strength | Market Universe predictor |
| M-004 | feature_field | h1_structural_clarity | Market Universe predictor |
| S-003 | predicted_field | confidence | Strategy Universe probability |

These are automatically resolved by the runner.
