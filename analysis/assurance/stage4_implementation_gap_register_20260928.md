# Stage 4 Implementation Gap Register

Generated from frozen Stage 4 certification evidence; no schema migration or research execution was performed.

## R1

```json
{
  "assurance_status_of_blocked_classification": "VERIFIED",
  "contract": "HD10 R1 over the Gate 1 required-field population",
  "dependency": "HD10 governed population adapter",
  "governed": 635,
  "mismatch": "runner population 10,803 != governed population 635",
  "question_id": "R1",
  "repair": "Add a frozen-population HD10 adapter accepting exactly the 635 governed records.",
  "runnable_immediately_after_repair": true,
  "runner": "r1_risk_layer_effectiveness.run_r1",
  "runner_n": 10803
}
```

## R2

```json
{
  "assurance_status_of_blocked_classification": "VERIFIED",
  "contract": "HD10 R2 over the Gate 1 required-field population",
  "dependency": "HD10 governed population adapter",
  "governed": 635,
  "mismatch": "runner population 10,803 != governed population 635",
  "question_id": "R2",
  "repair": "Add a frozen-population HD10 adapter accepting exactly the 635 governed records.",
  "runnable_immediately_after_repair": true,
  "runner": "r2_guard_attribution.run_r2",
  "runner_n": 10803
}
```

## L3

```json
{
  "assurance_status_of_blocked_classification": "VERIFIED",
  "contract": "L3 must own a distinct governed learning report",
  "dependency": "report ownership registry",
  "governed": 95,
  "mismatch": "declared report is canonically owned by D1",
  "question_id": "L3",
  "repair": "Assign L3 an independent report identity and ownership contract.",
  "runnable_immediately_after_repair": true,
  "runner": "declared runner with q1_component_reward.json",
  "runner_n": null
}
```

## L6

```json
{
  "assurance_status_of_blocked_classification": "VERIFIED",
  "contract": "L6 requires a governed historical learning runner",
  "dependency": "new L6 runner",
  "governed": 1852,
  "mismatch": "no governed runner/module exists for 1,852 usable records",
  "question_id": "L6",
  "repair": "Implement the canonical L6 method and persist an independently owned report.",
  "runnable_immediately_after_repair": true,
  "runner": "missing",
  "runner_n": null
}
```

## L7

```json
{
  "assurance_status_of_blocked_classification": "VERIFIED",
  "contract": "L7 comparison requires governed control_label and candidate_label",
  "dependency": "L7 label contract",
  "governed": 14046,
  "mismatch": "runner labels are absent from the canonical evidence contract",
  "question_id": "L7",
  "repair": "Govern control/candidate label fields and bind them to the L7 runner.",
  "runnable_immediately_after_repair": true,
  "runner": "declared runner",
  "runner_n": null
}
```

## G2

```json
{
  "assurance_status_of_blocked_classification": "VERIFIED",
  "contract": "G2 lineage coverage over 261 Gate 1 eligible records",
  "dependency": "HD14 denominator adapter",
  "governed": 261,
  "mismatch": "runner denominator 22,521 != governed population 261",
  "question_id": "G2",
  "repair": "Reconcile identity grain and pass exactly the governed 261-record population.",
  "runnable_immediately_after_repair": true,
  "runner": "lineage_coverage.run_g2",
  "runner_n": 22521
}
```

## G3

```json
{
  "assurance_status_of_blocked_classification": "VERIFIED",
  "contract": "G3 governance conclusion requires a governed CURRENT L6 dependency",
  "dependency": "L6",
  "governed": 1852,
  "mismatch": "required L6 scientific dependency is not implemented",
  "question_id": "G3",
  "repair": "Implement and certify L6, then bind its CURRENT result into G3.",
  "runnable_immediately_after_repair": false,
  "runner": "declared runner",
  "runner_n": null
}
```

## EX2

```json
{
  "assurance_status_of_blocked_classification": "VERIFIED",
  "contract": "HD09 EX2 over the frozen Gate 1 exit-policy population",
  "dependency": "HD09 frozen population adapter",
  "governed": 8760,
  "mismatch": "runner reconstructs 9,045 observations != governed population 8,760",
  "question_id": "EX2",
  "repair": "Add a frozen governed-population HD09 adapter and forbid repository event-file reconstruction.",
  "runnable_immediately_after_repair": true,
  "runner": "exit_policy_governed.run_ex2",
  "runner_n": 9045
}
```
