# RETIRED — STALE PACKAGED COPIES (Gap 9 final architecture cleanup)

The directories in `lambda/` are **stale point-in-time snapshots** of older
research architecture, NOT the canonical Research Engine.

| Directory | What it was | Why retired |
|---|---|---|
| `lambda/research_engine/` | A packaged copy of an older research-engine question/execution architecture (23-question V10 registry era) | Targets the retired `v10-engine` bucket, reads the retired local `research_ready_trade_dataset` preprocessing chain, and shares no code with the canonical `research_engine/` at the repository root |
| `lambda/anomaly_analysis/` | A standalone anomaly-report Lambda extracted from `core/research_anomaly.py` | Reads the same retired local dataset chain and retired bucket |

The canonical Research Engine is the repository-root `research_engine/`
package. It is invoked via:

- `python -m research_engine.main`
- `python scripts/run_research_cycle.py` (weekly scheduled cycle)
- `python -m research_engine.experiments.research_runner` (run_all)

There is no deployment pipeline (Terraform/CloudFormation/CI) in this
repository that packages or deploys these Lambda handlers; they are retained
only as historical reference. Do NOT:

- import anything from `lambda/` in canonical code;
- treat `lambda/research_engine/` as a second Research Engine;
- deploy these handlers without first re-deriving them from the canonical
  package and re-verifying the dataset/bucket contracts.
