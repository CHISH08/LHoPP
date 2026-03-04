# Task Stratification in CALVIN

This document explains how to split CALVIN tasks by type for deeper analysis.

## Can CALVIN Be Split by Task Type?

Short answer:
- yes, but mostly via derived labels
- partially supported natively

What exists natively:
- explicit task list and task oracle definitions  
  [new_playtable_tasks.yaml](../../../calvin/calvin_models/conf/callbacks/rollout/tasks/new_playtable_tasks.yaml)
- symbolic preconditions/effects and sequence generation rules  
  [multistep_sequences.py](../../../calvin/calvin_models/calvin_agent/evaluation/multistep_sequences.py)

What does not exist natively:
- explicit labels like `speed_task`, `accuracy_task`, `vision_task`

Conclusion:
- CALVIN supports robust stratification, but these strata must be defined in benchmark protocol metadata.

## Proposed Strata

The benchmark uses the reproducible mapping in [task_taxonomy.yaml](../task_taxonomy.yaml).

Main strata:
- `speed_reaction`
- `accuracy_neatness`
- `vision_dominant`
- `contact_precision`
- `state_reasoning`
- `compositional_long_horizon`

## Why These Strata Matter

- `speed_reaction` isolates fast and simple manipulations with low planning depth.
- `accuracy_neatness` captures precision-sensitive outcomes where collateral motion matters.
- `vision_dominant` measures dependence on visual modality quality and failures.
- `contact_precision` highlights tasks where tactile/proprioception can help robustness.
- `state_reasoning` stresses condition-based behavior beyond pure motion execution.
- `compositional_long_horizon` targets multi-step dependency chains and recovery under failures.

## Reporting Requirements

For every metric in [METRICS.md](METRICS.md), report:
- overall value
- value per stratum
- stratum-level delta to matched ideal baseline for stress tracks

This prevents a model from looking strong only because it overfits one easy task family.

## Minimal Practical Split (if runtime is limited)

If full stratification is too expensive, keep at least:
- `speed_reaction`
- `accuracy_neatness`
- `state_reasoning`

This gives a useful capability profile with manageable compute.
