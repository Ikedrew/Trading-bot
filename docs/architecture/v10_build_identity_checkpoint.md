# V10 Build Identity Checkpoint

## Purpose

Every live bot startup is tied to an exact Git revision. This eliminates ambiguity about which code version is running — a problem that previously caused confusion when fixes were deployed but the process hadn't restarted.

Combined with the existing V10 CODE VERSION block (file modification timestamps), the build identity provides two independent verification methods:
1. **File timestamps** — proves which files on disk were loaded
2. **Git commit** — proves which revision of the codebase is running

---

## Data Source

| Field | Source | Command |
|---|---|---|
| `git_commit` | Short SHA from HEAD | `git rev-parse --short HEAD` |
| `branch` | Current branch name | `git rev-parse --abbrev-ref HEAD` |
| `started_at` | Python `datetime.now(UTC)` | N/A (runtime clock) |

---

## Failure Handling

| Failure Scenario | Behaviour |
|---|---|
| `.git` directory missing | Returns `git_commit: UNKNOWN`, `branch: UNKNOWN` |
| Git not installed on system | Same — `FileNotFoundError` caught |
| Git command times out (>5s) | Same — `TimeoutExpired` caught |
| Running from packaged/deployed environment | Same — graceful degradation |
| Subprocess raises any exception | Same — all exceptions caught in `_run_git()` |
| `started_at` unaffected by Git failures | Always populated from system clock |

**The bot NEVER fails startup due to Git unavailability.**

---

## Example Startup Output

```
==================================================
V10 CODE VERSION
==================================================
  strategy_engine: 2026-07-31 08:15:42 UTC
    path: C:\...\core\v10\strategy_engine.py
  entry_engine:    2026-07-30 22:06:29 UTC
    path: C:\...\core\v10\entry_engine.py
  opportunity_engine: 2026-07-30 17:09:28 UTC
    path: C:\...\core\v10\opportunity_engine.py
  H4 trend propagation: ACTIVE (all regimes)
  ENGINE_MODE: V10
==================================================
==================================================
V10 BUILD ID
==================================================
  git_commit: a3f7c21
  branch:     main
  started_at: 2026-07-31 09:00:15 UTC
==================================================
──────────────────────────────────────────────────
MACRO CONTEXT LAYER
──────────────────────────────────────────────────
  D1 (Daily):   ENABLED
  W1 (Weekly):  ENABLED
  MN (Monthly): ENABLED
  Module loaded: core.timeframes.macro_alignment
──────────────────────────────────────────────────
```

---

## Files

| File | Role |
|---|---|
| `core/runtime/build_identity.py` | Helper: `get_build_identity()` → `BuildIdentity` dataclass |
| `core/runtime/live_scanner.py` | Consumer: prints + logs at startup |
| `tests/test_build_identity.py` | 14 unit tests |

---

## Persistent Log Entry

```
[V10 BUILD] commit=a3f7c21 branch=main started=2026-07-31 09:00:15 UTC
```

This line appears in the persistent log file, enabling post-hoc queries like:
- "Which commit was running when trade X was taken?"
- "Did the bot restart between these two decisions?"
- "Is the running process on the expected branch?"
