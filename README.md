# LHoPP: Unified Benchmark for Long-Horizon Planning and Policies

## Abstract

Long-horizon robot task planning requires building and executing hierarchical action sequences under partial observability, execution failures, and unexpected perturbations. Recent progress spans three largely incomparable families of methods: classical symbolic and task-and-motion planners, LLM-based planners that compose skills/tools with online correction, and large policy/VLA models that map observations and instructions directly to actions.

Existing empirical comparisons are often not reproducible and frequently unfair due to mismatched action spaces, different skill libraries and termination criteria, hidden prompts/tooling, and the lack of stress tests that measure recovery behavior rather than only binary success.

LHoPP proposes a unified, reproducible benchmarking protocol for long-horizon robot task planning across heterogeneous planners and policy baselines. The protocol standardizes a shared action/skill interface, environment seeding, and episode-level trace logging (observations, plans, skill calls, failures, timing, and token budgets), enabling exact re-runs and post-hoc auditing. Beyond success rate, it includes diagnostic metrics for decomposition quality, end-to-end efficiency, robustness under ablations, and replanning/backtracking cost.

The full protocol targets two complementary regimes:
- `VirtualHome` (program-driven household activities)
- `CALVIN` (language-conditioned manipulation)

This repository currently includes the runnable benchmark pipeline for `VirtualHome` and detailed protocol documentation for `CALVIN`.

## Repository Structure

- `VirtualHomeRout/vh_runner.py` - benchmark runner
- `VirtualHomeRout/requirements.txt` - reproducible dependencies
- `VirtualHomeRout/docs/BENCHMARKING.md` - benchmark setup and run guide
- `VirtualHomeRout/docs/METRICS.md` - metric definitions and interpretation
- `calvin_bench/benchmarks/calvin/README.md` - CALVIN benchmark protocol overview
- `calvin_bench/benchmarks/calvin/docs/SCENARIOS.md` - CALVIN ideal and stress scenario matrix
- `calvin_bench/benchmarks/calvin/docs/METRICS.md` - CALVIN metric definitions
- `calvin_bench/benchmarks/calvin/docs/COMPARISON.md` - fair comparison rules
- `calvin_bench/benchmarks/calvin/docs/TASK_STRATIFICATION.md` - CALVIN task-type stratification

## Reproducible Setup (From Scratch, Python 3.11)

### 1. Prerequisites

- Python `3.11.x`
- `git`
- VirtualHome Unity simulator `v2.3.0` for your OS

Unity downloads:
- Linux: http://virtual-home.org//release/simulator/v2.0/v2.3.0/linux_exec.zip
- macOS: http://virtual-home.org/release/simulator/v2.0/v2.3.0/macos_exec.zip
- Windows: http://virtual-home.org//release/simulator/v2.0/v2.3.0/windows_exec.zip

Unpack the simulator into `VirtualHomeRout/dataset/`.

Expected executable paths:
- Windows: `VirtualHomeRout/dataset/windows_exec.v2.3.0/VirtualHome.exe`
- Linux: `VirtualHomeRout/dataset/linux_exec.v2.3.0/linux_exec.x86_64`
- macOS: `VirtualHomeRout/dataset/macos_exec.v2.3.0/macos_exec.app`

### 2. Create Virtual Environment

From repository root:

```powershell
cd VirtualHomeRout
python -m venv ..\.venv_vh
```

Activate:

Windows PowerShell:

```powershell
..\.venv_vh\Scripts\Activate.ps1
```

Linux/macOS:

```bash
source ../.venv_vh/bin/activate
```

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Check Python version:

```powershell
python --version
```

It should report `Python 3.11.x`.

### 3. Install Dependencies

From `VirtualHomeRout/`:

```powershell
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

### 4. Minimal Smoke Run (VirtualHome)

```powershell
python vh_runner.py --mode offline --model reactive --tasks 3 --parallel 1 --speed 12 --repeats 1 --unity-executable dataset/windows_exec.v2.3.0/VirtualHome.exe --no-keep-unity-alive
```

This run:
- selects one task per difficulty (`easy/medium/hard`)
- computes benchmark metrics
- saves example videos
- writes outputs under `VirtualHomeRout/benchmarks/virtualhome/...`

## Minimal CLI (vh_runner.py)

- `--mode {online,offline,all}`
- `--model {reactive,planner,hierarchical,hybrid}`
- `--tasks N`
- `--parallel N`
- `--speed FLOAT`
- `--repeats N`
- `--unity-executable PATH` (required for `offline` and `all`)

## Expected Outputs

After a successful run:
- `reports/report.json`
- `reports/episodes.csv`
- `logs/run.log`
- `video_examples/video_examples_manifest.json`
- `video_examples/mp4/*.mp4` (Unity modes)

## Documentation

- Benchmark setup and execution: [VirtualHomeRout/docs/BENCHMARKING.md](VirtualHomeRout/docs/BENCHMARKING.md)
- Metrics and interpretation: [VirtualHomeRout/docs/METRICS.md](VirtualHomeRout/docs/METRICS.md)
