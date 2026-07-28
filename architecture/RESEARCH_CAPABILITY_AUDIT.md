# Research Capability Audit — Complete System Assessment

---

## Progress Report

### Research Domain Completion

| Domain | ✓ Complete | 🟡 Partial | ✗ Missing |
|--------|-----------|-----------|----------|
| **Market Behaviour** | Regime classification, phase detection, direction | Phase transition prediction | Microstructure, order flow |
| **Pattern Discovery** | 14 patterns detected, per-pattern EV, degradation tracking | Pattern × context interaction (M9) | New pattern discovery, adaptive detection |
| **Regime Analysis** | H4 regime classification, regime → outcome correlation | Regime × strategy interaction | Regime prediction (forward-looking) |
| **Context Analysis** | MarketContext (H4/H1/M15/M5), tradability score, conflict detection | Phase × family matching (M10), HTF alignment value | Dynamic context weighting |
| **Strategy Selection** | Strategy activation (3 types), weight profiles, StrategyFamilyAuthority, Knowledge Library (17 strategies) | Strategy condition evaluation, phase-eligible filtering | Active strategy selection based on validated evidence |
| **Expected Value** | EV calculation, p_success, dual EV (synthetic vs empirical), EV gate | EV calibration per context | Adaptive EV threshold |
| **Execution Quality** | Shadow trade simulation, slippage journal, broker reliability | Shadow vs live comparison (X4) | Execution timing optimisation |
| **Risk Management** | SL/TP geometry, position sizing, drawdown guards, probability of ruin (R3), drawdown halt (R4) | Guard efficacy measurement | Adaptive risk per regime |
| **Portfolio Construction** | Multi-symbol scanner, portfolio ranking (shadow), symbol universe analysis | Ranking accuracy measurement (D6) | Correlation-aware sizing |
| **Learning** | Pattern degradation tracking, feature versioning, schema migration, A/B validation framework | Walk-forward (E5), system improvement tracking | Automated strategy promotion |
| **Production Monitoring** | Event stream, stale data monitor, health checks, runtime state classifier | Decision funnel analytics | Alerting on edge decay |

### Estimated Completion

| Layer | Completion |
|-------|-----------|
| Observation Layer | **92%** — All market data captured, MarketContext persisted, strategy observations live, shadow trades running |
| Evidence Layer | **78%** — Shadow outcomes linked, decision traces persisted, but automated strategy↔outcome linking not yet wired |
| Learning Layer | **45%** — Walk-forward infrastructure exists (E5), A/B framework exists (L7), but no automated promotion pipeline |
| Research Engine | **85%** — 51 questions registered, 28 have runners, 12-section command centre, decision gates |
| Production Readiness | **30%** — System collects data and reports findings, but cannot autonomously act on discoveries |

---

## What The System Will Automatically Discover After 6 Months

Given uninterrupted data collection with the current implementation:

### Certainties (will be answered by the existing experiment runners):

1. **True system EV** (E1) — Exact expectancy per trade with statistical significance. Whether the system has edge or not.

2. **Per-pattern expectancy** (E2) — Which of the 14 patterns produce positive R. Which should be disabled.

3. **Per-strategy expectancy** (E3) — Whether REVERSAL/CONTINUATION/FALSE_BREAK actually differ in performance.

4. **Regime predictive value** (M1) — Whether H4 regime classification correlates with outcome.

5. **Scoring component value** (D1) — Which of the 10 scoring components actually predict success. Which are noise.

6. **Confidence calibration** (D2) — Whether predicted probability matches actual win rate.

7. **Phase × pattern interaction** (M9) — Which patterns work in which phases.

8. **Phase × family interaction** (M10) — Whether strategy families should be scoped to phases.

9. **Guard efficacy** (R1/R2) — Whether risk guards improve or reduce EV.

10. **Probability of ruin** (R3) — Survival probability given measured statistics.

11. **Drawdown threshold** (R4) — At what drawdown the system should halt.

12. **Position sizing model** (R5) — Which sizing model maximises growth within acceptable risk.

13. **Walk-forward validation** (E5) — Whether measured edge holds on unseen data.

14. **Strategy observation patterns** — Via Observer #7: which strategies are phase-eligible and how often conditions are fully met.

### Probable (will be answerable if field coverage is sufficient):

15. **Phase improves prediction** (M3) — Whether adding phase to regime improves predictions.

16. **Strategy × pattern edge** (E4) — Which specific strategy+pattern pairs work.

17. **Horizon effect** (S2/S6) — Whether SCALP/INTRADAY/EXTENDED horizons differ.

18. **Strategy × phase specialisation** (S4) — Whether strategies work only in certain phases.

19. **Missed opportunity cost** (D5) — How much EV is lost to unnecessary rejections.

20. **Portfolio ranking quality** (D6) — Whether the ranking model picks the best trade.

### Unknown (depends on data coverage and field population):

21. **Shadow vs live execution gap** (X4/X5) — Requires sufficient live trade truth records.

22. **Phase transition prediction** (M5/M8) — Requires long phase history with linked outcomes.

23. **Market behaviour drift** (L4) — Requires 6+ months of stable collection to compare periods.

24. **Strategy intelligence value** — Whether FULLY_MET observations produce better outcomes than NOT_MET (the fundamental question for the taxonomy work).

---

## The Fundamental Question

After 6 months, the system will be able to conclusively answer:

> **"Does this system have a statistical edge, and if so, under what specific conditions does it exist?"**

If the answer is YES with statistical significance:
- The system knows WHICH patterns, WHICH regimes, WHICH phases, and WHICH strategies to focus on.
- It can promote validated strategies through decision gates.
- It can disable underperforming patterns and adjust thresholds.

If the answer is NO:
- The system knows the current approach doesn't work.
- It knows WHERE it fails (which components, which contexts).
- It has the data to guide fundamental redesign decisions.

Either outcome is valuable. The research architecture is designed to discover truth — not to confirm assumptions.
