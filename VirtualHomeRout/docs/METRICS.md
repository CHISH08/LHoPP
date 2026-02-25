# VirtualHome Benchmark Metrics

This document explains the metrics written to `report.json` and summary files.

## Overview

The benchmark evaluates:
- whether the agent finishes full tasks
- whether predicted actions are executable in Unity
- controller response stability and latency
- typical failure/rejection patterns

## Key Aggregated Metrics

### `online_success_rate`

Fraction of episodes completed fully (`online_success=true`).

Why it matters:
- primary end-to-end completion KPI.

### `online_goal_completion_ratio`

Average fraction of target actions completed per episode.

Why it matters:
- captures partial progress even on failed episodes.

### `sim_step_success_rate`

Fraction of successful Unity executions among attempted action executions.

Why it matters:
- measures action executability and simulator validity.

### `sim_reject_count_avg`

Average number of Unity `sim_reject` events per episode.

Why it matters:
- indicates how often actions are rejected due to state/context incompatibility.

### `sim_exception_count_avg`

Average number of Unity-side exceptions per episode.

Why it matters:
- infrastructure/integration reliability signal.

### `sim_exec_time_per_step_sec`

Average simulator execution time per action step.

Why it matters:
- performance monitoring and regression detection.

### `invalid_format`

Average number of invalid-format actions per episode.

Why it matters:
- validates action format contract compliance (`<char0> [Verb] <obj> (id)`).

### `disabled_action_violations`

Average number of steps where the model picks a disabled action.

Why it matters:
- robustness signal under perturbations and observation constraints.

### `mean_latency_sec`, `p95_latency_sec`

Mean and p95 controller response latency.

Why it matters:
- planning/inference stability and tail-latency behavior.

## Important Per-Episode Fields

- `online_success` - episode success flag
- `online_goal_completion_ratio` - completion progress on target script
- `steps_used`, `optimal_steps` - actual vs reference step count
- `error_actions` - number of error steps
- `reject_reason_hist` - Unity rejection reason distribution
- `decision_time_total_sec`, `decision_time_per_step_sec` - decision latency
- `termination_reason` - terminal state (`completed`, `max_steps_reached`, `episode_time_limit_exceeded`, `no_progress_timeout`)
- `episode_timeout`, `no_progress_termination` - soft timeout flags

## How To Read Results

- High `online_success_rate` with low `sim_step_success_rate` is uncommon and usually indicates sample skew or pipeline mismatch.
- Low `invalid_format` but high `sim_reject_count_avg` often means format is correct but state-aware action logic is weak.
- Increasing `p95_latency_sec` with stable mean latency indicates heavier tail delays.

## Where To Find Metrics

- global aggregate: `reports/report.json` -> `summary_all`
- per-scenario aggregate: `summary_by_scenario`
- per-difficulty aggregate: `summary_by_difficulty`
- per-episode details: `episodes` in `report.json` and `reports/episodes.csv`
