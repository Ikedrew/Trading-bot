# Assured findings (70 current VERIFIED)

cert: `b42b4bfcfa1eaf7df09cac9f88011c89362cf2ca907217e8466fff9fea3adc8b`
store: `d80ed828d5a35f315c7d1f0e1836f30e02c215eb5abf7b0f8d45d5d27cccc4c9`
counts: `{"COMPLETE": 12, "HISTORICALLY_UNANSWERABLE": 29, "IMPLEMENTATION_BLOCKED": 8, "INSUFFICIENT_DATA": 16, "NEGATIVE_RESULT": 2, "WAITING_DATA": 3}`

## Current findings

### D1 [INSUFFICIENT_DATA]

Unresolved D1 (Which of the 10 scoring components best predict actual R-multiple outcomes?): CURRENT artifact with status INSUFFICIENT_DATA. usable=95 anal=0 cand=36641. missing ['components.* | score_strategy | score_neutral', 'simulated_outcome.pnl_r_multiple | r_multiple']. Shortfall/revisit semantics; NOT market truth.

### D2 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable D2 (Is the system's predicted probability (p_success) calibrated to actual win rate?): CURRENT artifact with status INSUFFICIENT_DATA. usable=0 anal=0 cand=36641. schema gap STAGE4-DATA-D2; V2/V3 additive only.

### D3 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable D3 (Does the pre-decision predicted-EV (R units) gate relate to subsequent realised R?): CURRENT artifact with status INSUFFICIENT_DATA. usable=0 anal=0 cand=36641. schema gap STAGE4-DATA-D3; V2/V3 additive only.

### D4 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable D4 (Does the canonical pre-decision score (score_strategy) separate subsequent realised R, and is a discovery-selected threshold supported on later unseen data?): CURRENT artifact with status BLOCKED. usable=635 anal=0 cand=36641. schema gap STAGE4-DATA-D4; V2/V3 additive only.

### D5 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable D5 (Do rejected/no-trade canonical opportunities subsequently show counterfactual shadow outcomes indicating the rejection logic filters harmful or discards useful opportunities?): CURRENT artifact with status BLOCKED. usable=635 anal=0 cand=36641. schema gap STAGE4-DATA-D5; V2/V3 additive only.

### D6 [INSUFFICIENT_DATA]

Unresolved D6 (Does the portfolio ranker's ordering of candidates (by rank_position) match realised/shadow outcomes — i.e., do higher-ranked candidates genuinely outperform lower-ranked ones?): CURRENT artifact with status INSUFFICIENT_DATA. usable=516 anal=40 cand=14581. missing ['candidates.rank_position', 'simulated_outcome.pnl_r_multiple']. Shortfall/revisit semantics; NOT market truth.

### E1 [NEGATIVE_RESULT]

Resolved negative/null E1 (What is the true expectancy of the production decision pipeline measured by R-multiple per trade?): CURRENT artifact with status COMPLETE. usable=1852 anal=1852 cand=14077. research_engine.experiments.expected_value.run report COMPLETE. Settled finding, not absence of evidence.

### E2 [COMPLETE]

Resolved positive E2 (Which candlestick patterns contain positive expectancy across all conditions?): CURRENT artifact with status COMPLETE. usable=1852 anal=1852 cand=14077. research_engine.experiments.legacy_canonical.run_q05 report COMPLETE. Consumable as scientific truth.

### E3 [INSUFFICIENT_DATA]

Unresolved E3 (Which active non-NONE V10 StrategyFamily values contain positive expectancy on completed primary shadow outcomes?): CURRENT artifact with status INSUFFICIENT_DATA. usable=14046 anal=1083 cand=14077. missing ['strategy', 'r_multiple']. Shortfall/revisit semantics; NOT market truth.

### E4 [INSUFFICIENT_DATA]

Unresolved E4 (Which strategy × pattern combinations produce edge? (e.g. REVERSAL + TWEEZER_TOP)): CURRENT artifact with status INSUFFICIENT_DATA. usable=1852 anal=4 cand=14077. missing ['decision_snapshot.strategy | decision_snapshot.pattern; simulated_outcome.pnl_r_multiple']. Shortfall/revisit semantics; NOT market truth.

### E5 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable E5 (Does the measured edge survive on unseen market data using walk-forward testing with rolling windows?): CURRENT artifact with status INSUFFICIENT_DATA. usable=0 anal=0 cand=14077. schema gap STAGE4-DATA-E5; V2/V3 additive only.

### EX1 [COMPLETE]

Resolved positive EX1 (Does modifying exit policy (trailing stop, reduced TP, time-based) improve system expected value compared to the current max_bars timeout?): CURRENT artifact with status COMPLETE. usable=14046 anal=9601 cand=14077. research_engine.experiments.exit_policy_governed.run_ex1 report COMPLETE. Consumable as scientific truth.

### EX10 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable EX10 (Does the exit policy improvement hold on out-of-sample data using time-ordered walk-forward validation?): Historical governed population has no analytically usable rows; absent/unresolved: entry_time. usable=0 anal=0 cand=14077. schema gap STAGE4-DATA-EX10; V2/V3 additive only.

### EX2 [IMPLEMENTATION_BLOCKED]

Unresolved impl block EX2 (Does a trailing stop mechanism capture more of the available MFE than the current exit, using bar-by-bar sequential simulation?): HD09 EX2 reconstructs 9,045 exit-policy observations from repository event files, but the frozen Gate 1 usable population is 8,760; the runner has no frozen governed-population adapter. usable=8760 anal=9045 cand=14077. research_engine.experiments.exit_policy_governed.run_ex2 report COMPLETE; impl-gap EX2; repair only.

### EX3 [COMPLETE]

Resolved positive EX3 (What take-profit distance maximises expectancy? Tests TP at 0.25R through 3.0R using MFE data to determine reachability.): CURRENT artifact with status COMPLETE. usable=14046 anal=14046 cand=14077. research_engine.experiments.exit_management.run_ex3 report COMPLETE. Consumable as scientific truth.

### EX4 [COMPLETE]

Resolved positive EX4 (Does the current SL distance preserve signal quality, or does widening/tightening SL improve outcomes?): CURRENT artifact with status COMPLETE. usable=14046 anal=14046 cand=14077. research_engine.experiments.exit_management.run_ex4 report COMPLETE. Consumable as scientific truth.

### EX5 [WAITING_DATA]

Unresolved EX5 (Does trade horizon (SCALP/INTRADAY/EXTENDED) require a different exit policy? Tests trailing parameters per horizon.): CURRENT artifact with status WAITING_DATA. usable=14046 anal=9601 cand=14077. trigger GOVERNED_EVIDENCE_CONTRACT_REEVALUATION ['canonical_opportunity_id']; rerun only after a new frozen evidence epoch satisfies the listed observable/threshold contract. NOT market truth.

### EX6 [WAITING_DATA]

Unresolved EX6 (Does each strategy family (REVERSAL/MOMENTUM/CONTINUATION) require a different exit policy?): CURRENT artifact with status WAITING_DATA. usable=1852 anal=1046 cand=14077. trigger GOVERNED_EVIDENCE_CONTRACT_REEVALUATION ['canonical_opportunity_id']; rerun only after a new frozen evidence epoch satisfies the listed observable/threshold contract. NOT market truth.

### EX7 [COMPLETE]

Resolved positive EX7 (Does market regime (TRENDING/RANGING/TRANSITIONAL) require a different exit policy?): CURRENT artifact with status COMPLETE. usable=14046 anal=9591 cand=14077. research_engine.experiments.exit_policy_heterogeneity.run_ex7 report COMPLETE. Consumable as scientific truth.

### EX8 [WAITING_DATA]

Unresolved EX8 (Do different candlestick patterns require different exit policies based on their MFE/MAE profiles?): CURRENT artifact with status WAITING_DATA. usable=1852 anal=1046 cand=14077. trigger GOVERNED_EVIDENCE_CONTRACT_REEVALUATION ['canonical_opportunity_id']; rerun only after a new frozen evidence epoch satisfies the listed observable/threshold contract. NOT market truth.

### EX9 [COMPLETE]

Resolved positive EX9 (Does the proposed exit policy reduce the timeout exit rate and convert timeout losses into captured profits?): CURRENT artifact with status COMPLETE. usable=14046 anal=936 cand=14077. research_engine.experiments.exit_policy_governed.run_ex9 report COMPLETE. Consumable as scientific truth.

### EXEC1 [INSUFFICIENT_DATA]

Unresolved EXEC1 (Do execution failures or adverse conditions degrade otherwise valid opportunities?): CURRENT artifact with status INSUFFICIENT_DATA. usable=365 anal=365 cand=55782. missing ['result_ok + retcode + account execution identity', 'market_access.spread_atr_ratio']. Shortfall/revisit semantics; NOT market truth.

### G1 [NEGATIVE_RESULT]

Resolved negative/null G1 (Is the current dataset suitable for the intended research questions? (field coverage, source type, sample size)): CURRENT artifact with status BLOCKED. usable=14046 anal=70 cand=14077. research_engine.experiments.dataset_suitability.run_g1 report BLOCKED. Settled finding, not absence of evidence.

### G2 [IMPLEMENTATION_BLOCKED]

Unresolved impl block G2 (What percentage of canonical opportunities have valid decision-to-outcome lineage on (entity_id, canonical_opportunity_id)?): HD14 runner denominator is 22,521 identities but the canonical Gate 1 usable G2 population is 261; reconciling the distinct denominator contracts requires an engineering contract repair. usable=261 anal=22521 cand=36641. research_engine.experiments.lineage_coverage.run_g2 report COMPLETE; impl-gap G2; repair only.

### G3 [IMPLEMENTATION_BLOCKED]

Unresolved impl block G3 (Can research conclusions be trusted given current validation status, coverage percentages, and sample sizes?): G3 requires a governed CURRENT L6 dependency, but L6 has no declared runner or owned report. usable=1852 anal=None cand=14077. research_engine.experiments.research_validity.run_g3 report MISSING; impl-gap G3; repair only.

### HORIZON-1 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable HORIZON-1 (Within-opportunity comparison: for each canonical opportunity with simulated outcomes for the selected horizon AND >=1 alternative horizon, was the selection supported by subsequent evidence? Simulated counterfactuals on shadow-observed opportunities.): CURRENT artifact with status INSUFFICIENT_DATA. usable=0 anal=0 cand=81994. schema gap STAGE4-DATA-HORIZON-1; V2/V3 additive only.

### L1 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable L1 (Are patterns degrading in performance over time?): Historical governed population has no analytically usable rows; absent/unresolved: entry_time. usable=0 anal=0 cand=14077. schema gap STAGE4-DATA-L1; V2/V3 additive only.

### L2 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable L2 (Does the system improve after architecture changes? (compare pre/post periods)): CURRENT artifact with status INSUFFICIENT_DATA. usable=0 anal=0 cand=14077. schema gap STAGE4-DATA-L2; V2/V3 additive only.

### L3 [IMPLEMENTATION_BLOCKED]

Unresolved impl block L3 (Are the scoring weight assumptions, regime classifications, and strategy mappings still correct?): Declared q1_component_reward.json is adjudicated to D1; L3 has no independently owned CURRENT report path. usable=95 anal=None cand=36641. research_engine.experiments.component_reward.run report MISSING; impl-gap L3; repair only.

### L4 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable L4 (Does market behaviour change over time in ways that invalidate strategy assumptions?): Historical governed population has no analytically usable rows; absent/unresolved: entry_time. usable=0 anal=0 cand=15679. schema gap STAGE4-DATA-L4; V2/V3 additive only.

### L5 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable L5 (Do patterns, strategies, or market assumptions lose predictive power over time?): CURRENT artifact with status INSUFFICIENT_DATA. usable=0 anal=0 cand=14077. schema gap STAGE4-DATA-L5; V2/V3 additive only.

### L6 [IMPLEMENTATION_BLOCKED]

Unresolved impl block L6 (How trustworthy is each research conclusion given dataset validity, coverage, and sample size?): No governed runner/module is declared. usable=1852 anal=None cand=14077. MISSING report MISSING; impl-gap L6; repair only.

### L7 [IMPLEMENTATION_BLOCKED]

Unresolved impl block L7 (Does a proposed strategy change outperform the currently promoted version when evaluated in shadow mode with statistical significance?): Runner requires governed control_label and candidate_label, but the canonical L7 contract declares neither. usable=14046 anal=None cand=14077. research_engine.experiments.shadow_ab_validation.run_shadow_ab_validation report MISSING; impl-gap L7; repair only.

### M1 [COMPLETE]

Resolved positive M1 (Does H4 regime classification (TRENDING/RANGING/TRANSITIONAL) predict trade R-multiple?): CURRENT artifact with status COMPLETE. usable=14046 anal=14036 cand=14077. research_engine.experiments.market_prediction_rw2.run_m1 report COMPLETE. Consumable as scientific truth.

### M10 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable M10 (Does each market phase require a different strategy family (reversal, continuation, momentum, breakout) rather than different pattern weighting within one family?): CURRENT artifact with status INSUFFICIENT_DATA. usable=0 anal=0 cand=14077. schema gap STAGE4-DATA-M10; V2/V3 additive only.

### M11 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable M11 (Does market context (regime + phase + bias) provide more predictive value for trade outcomes than the pattern identity itself?): CURRENT artifact with status INSUFFICIENT_DATA. usable=0 anal=0 cand=36641. schema gap STAGE4-DATA-M11; V2/V3 additive only.

### M2 [INSUFFICIENT_DATA]

Unresolved M2 (Which H4 regimes produce edge for each strategy type?): CURRENT artifact with status INSUFFICIENT_DATA. usable=14046 anal=4 cand=14077. missing ['decision_snapshot.h4_regime | decision_snapshot.strategy; simulated_outcome.pnl_r_multiple']. Shortfall/revisit semantics; NOT market truth.

### M3 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable M3 (Does market_phase (IMPULSE/PULLBACK/CONSOLIDATION/EXHAUSTION/REVERSAL) improve prediction beyond H4 regime alone?): CURRENT artifact with status INSUFFICIENT_DATA. usable=0 anal=0 cand=14077. schema gap STAGE4-DATA-M3; V2/V3 additive only.

### M4 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable M4 (Which regime × phase × strategy combinations produce edge?): CURRENT artifact with status INSUFFICIENT_DATA. usable=0 anal=0 cand=14077. schema gap STAGE4-DATA-M4; V2/V3 additive only.

### M5 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable M5 (Do rapid phase transitions predict subsequent realised strategy drawdown in cumulative R? This is not account-equity or mark-to-market drawdown.): CURRENT artifact with status INSUFFICIENT_DATA. usable=0 anal=0 cand=14077. schema gap STAGE4-DATA-M5; V2/V3 additive only.

### M6 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable M6 (Which market phases (IMPULSE/PULLBACK/CONSOLIDATION/EXHAUSTION/REVERSAL) contain real edge?): CURRENT artifact with status INSUFFICIENT_DATA. usable=0 anal=0 cand=14077. schema gap STAGE4-DATA-M6; V2/V3 additive only.

### M7 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable M7 (Does combining regime and phase improve predictive power beyond either alone?): CURRENT artifact with status INSUFFICIENT_DATA. usable=0 anal=0 cand=14077. schema gap STAGE4-DATA-M7; V2/V3 additive only.

### M8 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable M8 (Do market phase transitions (e.g. IMPULSE→EXHAUSTION) predict future trade outcomes?): CURRENT artifact with status INSUFFICIENT_DATA. usable=0 anal=0 cand=15679. schema gap STAGE4-DATA-M8; V2/V3 additive only.

### M9 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable M9 (For each market phase (IMPULSE, PULLBACK, CONSOLIDATION, EXHAUSTION, REVERSAL), which patterns or behaviours actually belong there and produce positive expectancy?): CURRENT artifact with status INSUFFICIENT_DATA. usable=0 anal=0 cand=14077. schema gap STAGE4-DATA-M9; V2/V3 additive only.

### MGMT-1 [COMPLETE]

Resolved positive MGMT-1 (Observational association between management actions and realised outcomes. NOT causal.): CURRENT artifact with status COMPLETE. usable=84 anal=84 cand=233. research_engine.experiments.management_research.run_mgmt1 report COMPLETE. Consumable as scientific truth.

### MGMT-2 [COMPLETE]

Resolved positive MGMT-2 (Per-action-type outcome analysis (SLTP_MODIFY, PARTIAL_CLOSE, CLOSE). OBSERVATIONAL.): CURRENT artifact with status COMPLETE. usable=95 anal=95 cand=233. research_engine.experiments.management_research.run_mgmt2 report COMPLETE. Consumable as scientific truth.

### OPP-1 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable OPP-1 (Were the opportunities promoted into the decision pipeline better than the opportunities rejected or filtered out?): CURRENT artifact with status BLOCKED. usable=3073 anal=3073 cand=22619. schema gap STAGE4-DATA-OPP-1; V2/V3 additive only.

### P1 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable P1 (If a specific recommendation is promoted into production, what measurable improvement in EV, win rate, drawdown, trade frequency, and risk is expected?): CURRENT artifact with status INSUFFICIENT_DATA. usable=0 anal=0 cand=36641. schema gap STAGE4-DATA-P1; V2/V3 additive only.

### PORT-1 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable PORT-1 (Did the portfolio/ranking layer select the best available opportunity from the ranked candidate set? Evaluates whether the selected candidate's outcome is competitive with the best-ranked candidate in the same cycle.): CURRENT artifact with status INSUFFICIENT_DATA. usable=0 anal=0 cand=14581. schema gap STAGE4-DATA-PORT-1; V2/V3 additive only.

### PROT1 [COMPLETE]

Resolved positive PROT1 (Are positions retaining the protection (SL/TP) the system intended?): CURRENT artifact with status COMPLETE. usable=125 anal=125 cand=125. research_engine.experiments.execution_protection_research.run_prot1 report COMPLETE. Consumable as scientific truth.

### R1 [IMPLEMENTATION_BLOCKED]

Unresolved impl block R1 (Does the risk layer (guards, daily loss limit, exposure) improve overall expectancy and survival?): HD10 runner analytical population is 10,803 opportunities but the canonical Gate 1 usable population for R1 is 635; the runner lacks an adapter for the governed required-field population. usable=635 anal=10803 cand=36641. research_engine.experiments.r1_risk_layer_effectiveness.run_r1 report BLOCKED; impl-gap R1; repair only.

### R2 [IMPLEMENTATION_BLOCKED]

Unresolved impl block R2 (Did each individual guard (spread, correlation, regime, daily loss) improve final expectancy?): HD10 runner analytical population is 10,803 opportunities but the canonical Gate 1 usable population for R2 is 635; the runner lacks an adapter for the governed required-field population. usable=635 anal=10803 cand=36641. research_engine.experiments.r2_guard_attribution.run_r2 report BLOCKED; impl-gap R2; repair only.

### R3 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable R3 (Given the measured edge, win rate, variance and position sizing, what is the probability that this system eventually reaches catastrophic drawdown?): Historical governed population has no analytically usable rows; absent/unresolved: win_rate, position_size. usable=0 anal=0 cand=14077. schema gap STAGE4-DATA-R3; V2/V3 additive only.

### R4 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable R4 (At what realised drawdown should the system automatically suspend trading because historical recovery probability becomes unacceptable?): Historical governed population has no analytically usable rows; absent/unresolved: entry_time. usable=0 anal=0 cand=14077. schema gap STAGE4-DATA-R4; V2/V3 additive only.

### R5 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable R5 (What position sizing model (fixed risk, Kelly, half-Kelly, fractional Kelly, fixed lot, dynamic) maximises long-term growth while respecting acceptable drawdown?): Historical governed population has no analytically usable rows; absent/unresolved: win_rate. usable=0 anal=0 cand=14077. schema gap STAGE4-DATA-R5; V2/V3 additive only.

### RISK-1 [COMPLETE]

Resolved positive RISK-1 (When the bot realises a loss, does the realised loss magnitude respect the 1-R planned-risk definition? How often do realised losses exceed the plan (ELEVATED/CRITICAL)?): CURRENT artifact with status COMPLETE. usable=84 anal=84 cand=84. research_engine.experiments.risk_research.run_risk1 report COMPLETE. Consumable as scientific truth.

### S1 [INSUFFICIENT_DATA]

Unresolved S1 (Superseded alias of E3: expectancy for each active non-NONE V10 StrategyFamily on completed primary shadow outcomes.): Governed scientific alias of E3: CURRENT artifact with status INSUFFICIENT_DATA. usable=14046 anal=1083 cand=14077. missing ['strategy', 'r_multiple']. Shortfall/revisit semantics; NOT market truth.

### S2 [INSUFFICIENT_DATA]

Unresolved S2 (Does trade horizon (SCALP/INTRADAY/EXTENDED) independently affect expectancy?): CURRENT artifact with status INSUFFICIENT_DATA. usable=14046 anal=14046 cand=14077. missing ['identity.evaluated_horizon | identity.shadow_type | simulated_outcome.pnl_r_multiple']. Shortfall/revisit semantics; NOT market truth.

### S3 [INSUFFICIENT_DATA]

Unresolved S3 (Which strategy × horizon combinations work? (e.g. REVERSAL+SCALP vs CONTINUATION+EXTENDED)): CURRENT artifact with status INSUFFICIENT_DATA. usable=14046 anal=14046 cand=14077. missing ['identity.canonical_opportunity_id | identity.evaluated_horizon | decision_snapshot.strategy | simulated_outcome.pnl_r_multiple']. Shortfall/revisit semantics; NOT market truth.

### S4 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable S4 (Are strategies specialised for certain market phases? (e.g. REVERSAL only in EXHAUSTION)): CURRENT artifact with status INSUFFICIENT_DATA. usable=0 anal=0 cand=14077. schema gap STAGE4-DATA-S4; V2/V3 additive only.

### S5 [INSUFFICIENT_DATA]

Unresolved S5 (Which active non-NONE V10 StrategyFamily values retain expectancy after accounting for the evaluated trade horizon (SCALP/INTRADAY/EXTENDED) under which outcomes were simulated?): CURRENT artifact with status INSUFFICIENT_DATA. usable=14046 anal=1852 cand=14077. missing ['identity.canonical_opportunity_id', 'identity.strategy_id', 'identity.evaluated_horizon', 'simulated_outcome.pnl_r_multiple', 'lifecycle.status']. Shortfall/revisit semantics; NOT market truth.

### S6 [INSUFFICIENT_DATA]

Unresolved S6 (Which canonical trade horizons (SCALP/INTRADAY/EXTENDED) retain expectancy after accounting for the six active V10 StrategyFamily values?): CURRENT artifact with status INSUFFICIENT_DATA. usable=14046 anal=1852 cand=14077. missing ['identity.canonical_opportunity_id', 'identity.strategy_id', 'identity.evaluated_horizon', 'simulated_outcome.pnl_r_multiple', 'lifecycle.status']. Shortfall/revisit semantics; NOT market truth.

### S7 [INSUFFICIENT_DATA]

Unresolved S7 (Are strategies profitable only at specific horizons? (e.g. REVERSAL works at SCALP but not EXTENDED)): CURRENT artifact with status INSUFFICIENT_DATA. usable=14046 anal=0 cand=14077. missing ['identity.canonical_opportunity_id', 'identity.strategy_id', 'identity.evaluated_horizon', 'simulated_outcome.pnl_r_multiple', 'lifecycle.status']. Shortfall/revisit semantics; NOT market truth.

### STRAT-1 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable STRAT-1 (Ranking/monotonicity analysis of pre-decision strategy-selection confidence against subsequent primary-horizon shadow outcomes. NOT calibration (confidence is a relative score). Rejected strategies have no simulated outcomes — selection optimality cannot yet be proven.): CURRENT artifact with status INSUFFICIENT_DATA. usable=0 anal=0 cand=17103. schema gap STAGE4-DATA-STRAT-1; V2/V3 additive only.

### X1 [INSUFFICIENT_DATA]

Unresolved X1 (What is the real slippage model per symbol per session?): CURRENT artifact with status INSUFFICIENT_DATA. usable=23 anal=23 cand=33218. missing ['slippage (slippage_semantic=measured_execution_slippage)', 'market_access.session_state']. Shortfall/revisit semantics; NOT market truth.

### X2 [COMPLETE]

Resolved positive X2 (Are broker rejections/failures predictable by time, symbol, or market condition?): CURRENT artifact with status COMPLETE. usable=434 anal=434 cand=548. research_engine.experiments.execution_protection_research.run_x2 report COMPLETE. Consumable as scientific truth.

### X3 [INSUFFICIENT_DATA]

Unresolved X3 (Which trading sessions produce the best execution quality (lowest slippage, fewest rejects)?): CURRENT artifact with status INSUFFICIENT_DATA. usable=23 anal=0 cand=33218. missing ['slippage + slippage_semantic + result_ok', 'market_access.session_state']. Shortfall/revisit semantics; NOT market truth.

### X4 [INSUFFICIENT_DATA]

Unresolved X4 (How much theoretical edge (from shadow R) is lost during real execution?): CURRENT artifact with status INSUFFICIENT_DATA. usable=30 anal=25 cand=14161. missing ['simulated_outcome.pnl_r_multiple | r_multiple', 'outcome.r_multiple_realised | live_r_multiple']. Shortfall/revisit semantics; NOT market truth.

### X5 [HISTORICALLY_UNANSWERABLE]

Unresolved unrecoverable X5 (Does pre-decision predicted EV (predicted_ev_r_v1, R units) correspond to subsequent realised R at canonical-opportunity level, and does it persist on later unseen evidence?): CURRENT artifact with status INSUFFICIENT_DATA. usable=0 anal=0 cand=36641. schema gap STAGE4-DATA-X5; V2/V3 additive only.

### X6 [INSUFFICIENT_DATA]

Unresolved X6 (Under what conditions (symbol, session, spread, volatility) does execution quality degrade?): CURRENT artifact with status INSUFFICIENT_DATA. usable=78 anal=0 cand=55782. missing ['account_id', 'broker_symbol', 'slippage_semantic', 'result_ok', 'retcode', 'timestamp_utc', 'market_access.session_state', 'market_access.spread', 'market_access.spread_atr_ratio', 'canonical_opportunity_id', 'v10_market_state.regime.volatility_state']. Shortfall/revisit semantics; NOT market truth.
