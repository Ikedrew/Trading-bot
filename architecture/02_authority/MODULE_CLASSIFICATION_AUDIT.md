# MODULE CLASSIFICATION AUDIT

**Generated:** 2026-07-16  
**Method:** Import tracing from main.py → runtime paths, grep verification, test coverage check  
**Config:** `USE_NEW_PIPELINE=True`, `ENABLE_LEGACY_SHADOW_PIPELINE=False`, `REPLAY_MODE=False`  
**Total modules scanned:** ~250+

---

## RUNTIME AUTHORITY CHAIN

```
main.py
  → core.loop (run_live_scanner)
    → core.runtime.live_scanner (LIVE AUTHORITY)
      → core.pipeline.new_engine (DECISION AUTHORITY)
      → execution.mt5_execution (EXECUTION AUTHORITY)
      → core.engine.process_bar (LEGACY — gated by ENABLE_LEGACY_SHADOW_PIPELINE=False)
```

---

## CLASSIFICATION CRITERIA

- **KEEP**: Required by active runtime, replay, or tests. Correctly located.
- **MOVE**: Still required but in the wrong package. Suggest destination.
- **DELETE**: Not reachable from any active path. Dead code.
- **REVIEW**: Uncertain — edge case or conditional reachability.

---

