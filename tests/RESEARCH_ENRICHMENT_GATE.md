# Research Enrichment Gate

Dataset enrichment may begin only when this selection is green. It covers
research-data contracts and deliberately excludes broker, S3/network,
Discord, mutable daily state, and other operational tests.

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_semantic_ownership_contract.py `
  tests/test_canonical_identity.py `
  tests/test_canonical_lineage_contract.py `
  tests/test_runtime_canonical_lineage_regression.py `
  tests/test_recovery_identity.py `
  tests/test_future_data_contract.py `
  tests/test_universe_contracts.py `
  tests/test_research_universe_baseline.py `
  tests/test_outcome_enrichment.py `
  tests/test_research_projection.py `
  tests/test_shadow_counterfactual_population.py `
  tests/test_shadow_runtime_contract.py `
  tests/test_v2_outcome_linker.py `
  tests/test_v3_outcome_linker.py `
  tests/test_v3_shadow_outcome_linker.py `
  tests/test_v10_outcome_lineage.py `
  tests/test_v10_schema_freeze.py
```

Coverage: semantic ownership; canonical identity and linkage; restart and
same-root reconstruction; temporal leakage; active/retired research-universe
classification; realised outcome authority; research projection authority;
live/shadow separation; V2/V3/V10 linkage; schema validity.
