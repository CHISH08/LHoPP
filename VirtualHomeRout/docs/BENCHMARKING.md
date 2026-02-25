# VirtualHome Benchmarking Guide

## 1. Environment Setup (`venv_vh`)

Recommended Python version: `3.11.x`.

```powershell
cd VirtualHomeRout
python -m venv ..\.venv_vh
..\.venv_vh\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## 2. Minimal Run

```powershell
python vh_runner.py --mode offline --model reactive --tasks 3 --parallel 1 --speed 12 --repeats 1 --unity-executable dataset/windows_exec.v2.3.0/VirtualHome.exe --no-keep-unity-alive
```

What this run does:
- automatically splits `tasks` evenly across `easy/medium/hard`
- uses `parallel` for both worker count and Unity slot count
- auto-saves example videos for `offline|all` modes
- uses `speed` as Unity `time_scale`

## 3. Main Flags (Minimal Interface)

- `--mode {online,offline,all}`
- `--model {reactive,planner,hierarchical,hybrid}`
- `--tasks <N>`
- `--parallel <N>`
- `--speed <float>`
- `--repeats <N>`
- `--unity-executable <path>` (required for `offline`/`all`)

Additional flags (rarely needed):
- `--dataset-root`
- `--seed`
- `--keep-unity-alive/--no-keep-unity-alive`

Compatibility note:
- legacy flags (`--metrics-mode`, `--arch`, `--max-*`, `--parallel-workers`, `--env-slots`, etc.) are still supported but hidden from `--help`.

## 4. Timeout Behavior

- Episodes can end softly due to time/progress limits.
- This is not a benchmark crash: `termination_reason` is logged and metrics are computed as failure or partial progress.

## 5. Output Artifacts

Under `benchmarks/virtualhome/<run_name>/`:
- `logs/run.log` - run progress log
- `reports/report.json` - full report
- `reports/episodes.csv` - per-episode table
- `telemetry/telemetry_steps.jsonl` - step telemetry (if enabled)
- `unity_logs/*` - Unity logs
- `video_examples/raw/*`, `video_examples/mp4/*`, `video_examples/video_examples_manifest.json` - video artifacts
