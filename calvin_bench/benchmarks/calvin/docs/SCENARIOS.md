# CALVIN Benchmark Scenarios

This document defines the scenario matrix for fair and interpretable benchmarking of heterogeneous models on CALVIN.

## Core Rules

1. No training, finetuning, prompt tuning, or adapter retraining during benchmark.
2. All models run on the same fixed episode list, seeds, and stopping budgets.
3. Every stress run has a matched ideal run with the same `(sequence_id, seed, action_space, sensor_profile, task_stratum)`.
4. Models are informed about active sensors through an explicit `active_modalities` field in observation metadata.
5. Common reporting uses architecture-neutral metrics only.

## Evaluation Axes

Every run is defined by:
- `action_space`
- `sensor_profile`
- `perturbation_profile`
- `task_stratum`

## Action Spaces

The protocol uses an action-space sweep in normal conditions to expose degradation and adaptation limits.

- `rel7d`  
  Relative Cartesian displacement plus gripper action.

- `abs_pose`  
  Absolute Cartesian target pose plus gripper action.

- `joint_dof`  
  Joint-space control plus gripper action.  
  Note: this requires a joint-control adapter in the current codebase. If a model does not support this mode, report as `unsupported` and score it as failure for this axis in adaptation metrics.

## Sensor Profiles

- `all_modalities`: static RGB, gripper RGB, tactile, depth, proprioception
- `single_static_rgb`
- `single_gripper_rgb`
- `single_tactile`
- `single_depth_static`
- `single_proprio`
- `no_camera` (state only)
- `dual_static_tactile`
- `dual_static_proprio`

## Tracks

## T0 - Ideal Action-Space Sweep (Required)

Goal:
- establish clean capability baseline for every action space
- quantify degradation from `rel7d` to `abs_pose` to `joint_dof`

Conditions:
- no perturbations
- deterministic seeds
- default camera setup and no runtime sensor dropout

Required matrix:
- all models x all action spaces x `all_modalities`

Outputs:
- long-horizon quality by action space
- adaptation gap between action representations

## T1 - Ideal Sensor Matrix (Required)

Goal:
- isolate sensor dependence under clean control

Conditions:
- no perturbations
- run for each action space from T0
- run across all sensor profiles listed above

Required matrix:
- all models x all action spaces x all sensor profiles

Outputs:
- modality reliance map per model
- fair planner/policy comparison under identical observable inputs

## T2 - Stress Sensor Failures and Noise (Required)

Goal:
- evaluate perception robustness

Perturbation families:
- Gaussian RGB noise
- blur/compression artifacts
- depth dropouts and quantization
- tactile channel noise/dropout
- random sensor blackouts mid-episode

Important:
- run this track for every action space
- include dedicated `abs_pose` stress scenarios, not only `rel7d`

Outputs:
- `SensorRobustAUC`
- degradation curves vs perturbation severity

## T3 - Stress Actuation and Control (Required)

Goal:
- evaluate control robustness and safety

Perturbation families:
- action delay
- hold-last-action events
- action noise and clipping
- control packet drop (skip action update)

Required:
- `abs_pose` with failures is mandatory
- `joint_dof` with failures is mandatory when model and adapter support it

Outputs:
- `ActuationRobustAUC`
- safety and neatness under unstable control

## T4 - Recovery and Rollback Cost (Required)

Goal:
- measure recovery quality and cost after controlled disturbances

Procedure:
- inject a perturbation event at predefined checkpoints in episode
- continue execution under fixed budgets
- compare against matched ideal pair

Outputs:
- `RecoveryRate`
- `RecoveryCostSteps`
- `RecoveryCostTime`
- rollback diagnostics (if planner traces are available)

## T5 - Impossible Conditions and Safe Behavior (Required)

Goal:
- test behavior when goal completion is impossible

Examples:
- required object removed/unreachable
- required mechanism locked
- critical sensor unavailable

Evaluation target:
- not success, but safe and reasonable behavior
- fast and safe termination or safe fallback behavior

Outputs:
- `ImpossibleSafeAbortRate`
- `ImpossibleHazardRate`
- `ImpossibleTimeToTerminate`

## Task-Stratified Reporting

All tracks are reported by task strata:
- speed/reaction
- accuracy/neatness
- vision-dominant
- contact-precision
- compositional-reasoning

See [TASK_STRATIFICATION.md](TASK_STRATIFICATION.md) and [task_taxonomy.yaml](../task_taxonomy.yaml).

## Run Tiers

- `smoke`: 100 sequences, quick infrastructure validation
- `full`: 1000 sequences (default CALVIN long-horizon scale)

For publication-level comparisons, use `full` only.

## Interpretation Rules

1. Always interpret stress metrics relative to matched ideal baseline.
2. Compare action-space robustness only against the same sensor profile.
3. Compare sensor robustness only against the same action space.
4. Do not merge impossible-condition metrics with success-rate metrics.
5. Keep cross-family ranking separate from family-specific diagnostics.
