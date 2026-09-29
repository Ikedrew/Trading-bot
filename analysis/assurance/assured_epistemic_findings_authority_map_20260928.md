# Architecture / authority map

Gate1 checkpoints -> historical pass (producer)
-> final certification (certifier)
-> assured_epistemic_findings_20260928.json (THIS authority)

## Paths
- producers: HistoricalResearchPass / FinalAssuranceCert
- authority: control_plane/assured_epistemic_findings.py
- consumers: consume_scientific_truth / gate_q71 / findings[].consumption
- raw analysis/reports/* NOT authority (bypass rejected)

## Versioning
- v1 CURRENT x70, supersedes None, superseded 0
- revisions append; history immutable

## Upstream note
- assurance_consumption_gate lacks IMPLEMENTATION_BLOCKED;
  authority admits it repair-only (no gate file edit).

## Q71+
- q71_started False; gated via gate_q71().

