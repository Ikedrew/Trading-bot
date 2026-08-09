# Lambda Data Source Trace — Audit Report

## Executive Summary

Lambda E1 returns `sample_size=0` because the experiment execution path reads from a **local filesystem path** (`data/research/research_universe.jsonl`) which does not exist in Lambda's ephemeral `/var/task` filesystem. The S3 storage adapter exists and is configured via environment variables, but it is **never called** by the ExperimentRunner → ResearchSegmenter code path.

---

## E1 Execution Trace

```
lambda_handler.handler(event)
    ↓
ResearchRouter() ← universe_file=None
    ↓
_run_question() → ExperimentRunner(universe_file=None)
    ↓
ExperimentRunner.__init__ → self._universe_file = Path("data/research/research_universe.jsonl")
    ↓
run() → self.segmenter.filter(**{})
    ↓
ResearchSegmenter.__init__ → Path("data/research/research_universe.jsonl")
    ↓
events property → _load_jsonl(path)
    ↓
_load_jsonl → path.exists() → FALSE (in Lambda) → returns []
    ↓
filter(**{}) → returns []
    ↓
"No trades match the specified filters" → n=0, INCONCLUSIVE
```

---

## Actual Data Source

| Question | Answer |
|---|---|
| What does E1 actually read? | `data/research/research_universe.jsonl` (relative local path) |
| Uses S3 adapter? | **NO** |
| Uses RESEARCH_UNIVERSE_KEY? | **NO** |
| Loader function | `segmentation_engine._load_jsonl(Path)` |

---

## Lambda Configuration vs Reality

| Config Key | Value | Used by E1? |
|---|---|---|
| RESEARCH_STORAGE | s3 | NO |
| RESEARCH_BUCKET | v10-engine | NO |
| RESEARCH_UNIVERSE_KEY | data/research/research_universe.jsonl | NO |

The `operations/storage.py` `ResearchStorage` class reads these env vars, but the ExperimentRunner/Segmenter path **never instantiates or calls** `ResearchStorage`.

---

## Dataset Locations

| Dataset | Records | Local Path | In S3? | Used By E1? |
|---|---|---|---|---|
| research_universe.jsonl | 94 | data/research/research_universe.jsonl | Uploaded | YES (local only) |
| research_ready_trades.jsonl | 94 | logs/research_ready_trade_dataset/ | Yes | NO (legacy path) |

---

## Local vs Lambda Comparison

| Environment | File Exists? | Records | E1 Result |
|---|---|---|---|
| Local (Windows) | YES | 94 | sample_size=94 |
| Lambda (/var/task) | NO | 0 | sample_size=0, INCONCLUSIVE |

---

## Schema Compatibility

The research_universe.jsonl schema IS compatible with E1. The experiment works perfectly locally. The issue is purely **file availability**, not schema.

---

## Root Cause

**Dataset wiring problem.**

The `ResearchSegmenter._load_jsonl()` uses `Path.exists()` on a relative filesystem path. In Lambda's `/var/task` directory, this file does not exist because:
1. The ZIP package intentionally excludes datasets (they're too large)
2. The S3 adapter was built but never connected to the experiment execution chain

---

## Intended Architecture

**Architecture A:** `research_universe.jsonl → ResearchSegmenter → E1`

This is correct. The file must be available where the segmenter expects it.

---

## Recommended Next Action (do not implement yet)

The smallest correct fix: **Make `ResearchRouter._run_question()` download the universe from S3 to a temp file before passing it to `ExperimentRunner`**, OR modify `ResearchSegmenter` to accept data from the `ResearchStorage` adapter.

This is a **Lambda deployment wiring problem** — not a research architecture problem, dataset problem, schema problem, or experiment problem.

---

## Classification

| Question | Answer |
|---|---|
| Lambda deployment problem? | Partially — Lambda itself works fine |
| S3 configuration problem? | No — env vars are correct |
| Dataset wiring problem? | **YES — this is the root cause** |
| Schema problem? | No |
| Experiment problem? | No |
| Smallest fix? | Connect ExperimentRunner data path to S3 storage adapter |

---
