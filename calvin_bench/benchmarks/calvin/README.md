# CALVIN Benchmark Protocol for LHoPP

This folder defines a reproducible evaluation protocol for CALVIN that is aligned with LHoPP goals and complements the existing VirtualHome benchmark.

Scope:
- test only (no training or finetuning during benchmark runs)
- fair cross-architecture comparison (planner vs policy vs hybrid)
- explicit stress evaluation for robustness, recovery, safety, and behavior under impossible conditions

## Document Map

- [docs/SCENARIOS.md](docs/SCENARIOS.md)  
  Full benchmark matrix: ideal and stress tracks, sensor/action-space sweeps, perturbation profiles, and run schedule.

- [docs/METRICS.md](docs/METRICS.md)  
  Metric definitions and formulas, including long-horizon, robustness, rollback/recovery, safety, and impossible-condition behavior.

- [docs/COMPARISON.md](docs/COMPARISON.md)  
  How to compare models fairly across families, and how to do internal comparisons policy vs policy and planner vs planner.

- [docs/TASK_STRATIFICATION.md](docs/TASK_STRATIFICATION.md)  
  Task-type splits for CALVIN (speed, accuracy, vision reliance, reasoning, contact precision) and how to use them in reporting.

- [task_taxonomy.yaml](task_taxonomy.yaml)  
  Reproducible task grouping config used by scenario and metric reports.

## Why This Complements VirtualHome

VirtualHome already covers action-format correctness, discrete planning errors, and simulator rejection patterns.  
This CALVIN protocol adds what VirtualHome does not cover deeply:
- multimodal sensor dependence (RGB, depth, tactile, proprioception)
- continuous-control robustness under perturbations
- recovery and rollback cost after physical disturbances
- safety and neatness in embodied manipulation
- safe behavior when goals become impossible
