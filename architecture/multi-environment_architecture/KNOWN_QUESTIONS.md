# Known Questions — Currently Answerable

## Date: 2026-07-23

These questions can be answered TODAY with existing data.

---

## Execution & Infrastructure

1. Can the bot place orders with the broker? → **YES** (25 successful fills)
2. Does the bot handle broker rejections? → **YES** (retries, POSITION_NOT_FOUND handling)
3. Does the bot recover after restart? → **YES** (D3 recovery + identity restoration)
4. Is execution slippage acceptable? → **YES** (0.0-0.4 pips average)
5. Does the kill switch work? → **YES** (blocks all execution immediately)
6. Does the idempotency guard prevent duplicate orders? → **YES** (30-second dedup window)
7. Does the circuit breaker trip on MT5 timeouts? → **YES** (3 consecutive timeout threshold)

## Decision Quality

8. What score did each trade receive? → **YES** (decision_trace: 10 components + composite)
9. What was the probability estimate? → **YES** (decision_audit: p_success)
10. What was the expected value? → **YES** (decision_audit: ev, ev_positive)
11. Why was a trade approved? → **YES** (decision_ledger: reasoning, supporting/contradicting evidence)
12. What regime was active? → **YES** (decision_trace: regime, regime_confidence)
13. What pattern triggered the trade? → **YES** (100% of records have pattern_name)
14. What was the confirmation quality? → **YES** (decision_trace: confirmation_score)

## Trade Outcomes

15. What is the current win rate? → **YES** (1/13 = 7.7% on compromised sample)
16. What is the average R-multiple? → **YES** (-1.24 R)
17. What is the profit factor? → **YES** (0.028)
18. How long do trades last? → **YES** (average 45.5 minutes)
19. What was the maximum favourable excursion? → **YES** (per-trade MFE in journal)
20. Which symbol performed best? → **YES** (USDCHF: only winner)
21. Which pattern had a win? → **YES** (TWEEZER_TOP: 1/5)

## Risk & Guards

22. Does the minimum SL guard reject tight stops? → **YES** (adaptive formula: max(floor, ATR, spread))
23. Does the correlation guard block overexposure? → **YES** (blocked 23.9 lot trades correctly)
24. Does the spread guard block bad conditions? → **YES** (blocked 3 off-session trades)
25. Are all 10 runtime guards operational? → **YES** (tested in guard chain)

## Persistence & Forensics

26. Can every trade be reconstructed end-to-end? → **YES** for 80% (20/25)
27. Is S3 isolated from tests? → **YES** (Path.resolve() guard + conftest fixture)
28. Are shadow trades being generated? → **YES** (171 shadow trade records)
29. Is the EV experiment mode tracking? → **YES** (ev_experiment_mode field deployed)
