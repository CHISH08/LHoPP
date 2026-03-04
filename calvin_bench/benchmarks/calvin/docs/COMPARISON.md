# Comparison Protocol

This document defines how to compare heterogeneous models fairly on CALVIN.

## 1. Comparison Modes

Use three separate modes:

1. `CrossFamily`  
   Planner vs policy vs hybrid using architecture-neutral metrics only.

2. `PolicyInternal`  
   Policy vs policy using control-specific diagnostics.

3. `PlannerInternal`  
   Planner vs planner using planning-specific diagnostics.

Do not mix internal diagnostics into the main cross-family rank.

## 2. Fairness Contract

All models must share:
- same sequence list and seeds
- same episode budget (`max_steps`, `max_time`)
- same action-space and sensor-profile matrix per track
- same perturbation schedule for matched stress runs
- same termination criteria

All models must expose the same benchmark interface:
- `reset()`
- `step(obs, goal, context)`
- optional trace hooks for plan/replan metadata

## 3. Track-Aware Comparison

Use the same track IDs as in [SCENARIOS.md](SCENARIOS.md):
- `T0` Ideal Action-Space Sweep
- `T1` Ideal Sensor Matrix
- `T2` Stress Sensor Failures and Noise
- `T3` Stress Actuation and Control
- `T4` Recovery and Rollback
- `T5` Impossible Conditions

Comparison rules:
- compare stress only against matched ideal baseline
- compare models within the same `(track, action_space, sensor_profile, task_stratum)`
- aggregate only after per-cell metrics are computed

## 4. Main Cross-Family Ranking

Recommended main ranking fields:
- `ChainAUC`
- `Progress`
- `EffSteps`
- `SensorRobustAUC`
- `ActuationRobustAUC`
- `RecoveryRate`
- `NeatSuccessRate`
- `UnsafeContactRate`

Optional composite score:
- normalize each metric to `[0,1]`
- invert risk metrics (for example `UnsafeContactRate`)
- compute geometric mean as `CoreScore`

Publish both:
- component metrics table
- composite score table

## 5. Internal Comparison Packs

## PolicyInternal Pack

Primary:
- `ChainAUC`
- `Progress`
- `ActuationRobustAUC`
- `NeatSuccessRate`

Diagnostics:
- `ActionJitter`
- `GripperChatterRate`
- `DelaySensitivity`

## PlannerInternal Pack

Primary:
- `ChainAUC`
- `Progress`
- `RecoveryRate`

Diagnostics:
- `ReplanCount`
- `BacktrackSteps`
- `PlanRepairLatency`
- `TokenCostPerSuccess` (if applicable)

## 6. Handling Unsupported Modes

If a model cannot run a required action space or sensor profile:
- mark run status as `unsupported`
- count as failure for adaptation/robustness aggregates that require that axis
- keep explicit `coverage` metric to avoid hidden exclusions

Recommended:
- report `Coverage = supported_cells / required_cells`

## 7. Statistical Reporting

Required:
- mean and median
- 95% paired bootstrap CI
- paired delta vs matched ideal baseline

Recommended:
- paired non-parametric significance tests for key deltas

## 8. Reporting Format

Each result table should include:
- `model_id`
- `family` (`planner`, `policy`, `hybrid`)
- `track_id`
- `action_space`
- `sensor_profile`
- `task_stratum`
- metric values
- CI bounds
- coverage status

## 9. Cross-Benchmark Alignment With VirtualHome

For final LHoPP reports:
- keep a shared top-level metric schema (`success`, `progress`, `efficiency`, `latency`, `robustness`, `safety`)
- use benchmark-specific submetrics underneath
- compare architecture trends across benchmarks, not raw metric magnitudes
